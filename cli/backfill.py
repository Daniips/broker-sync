"""--backfill-snapshots: reconstruct historical snapshots via TR aggregateHistory."""
import asyncio
from datetime import datetime, timedelta

from pytr.account import login

from tr_sync import (
    CACHE_PATH,
    CRYPTO_ISINS,
    GIFT_COST_OVERRIDES,
    SNAPSHOTS_SHEET,
    TIMEZONE,
    _make_snapshot_store,
    is_feature_enabled,
    log,
    open_spreadsheet,
)
from core.cache import load_cached_session, save_cached_session


def backfill_snapshots(start_iso: str | None = None, frequency: str = "weekly", *, refresh: bool = False):
    """Reconstruct historical snapshots and persist them to `_snapshots` (+ positions).

    For each historical date D between `start` and today (at the given cadence):
      - Compute each current ISIN's shares at D, walking back from the
        current snapshot and inverting every BUY/SELL after D.
      - Compute cash at D by reverting the `from_cash=True` flows.
      - Query TR for the historical price of each ISIN at D.
      - Build the reconstructed PortfolioSnapshot and write it to the tabs.

    Caveats: see core/backfill.py. Saveback shares without info end up
    slightly overestimated (<1% error); ISINs whose price history is
    unavailable are excluded from positions_value (e.g. crypto sometimes
    fails by exchange).

    Args:
      start_iso: ISO date (YYYY-MM-DD) to start from. Default: today − 365d.
      frequency: "weekly" | "monthly" | "biweekly". Default "weekly".
    """
    if not is_feature_enabled("backfill_snapshots"):
        log.info("Feature 'backfill_snapshots' disabled (config or broker).")
        return

    from brokers.tr.adapter import fetch_price_history_with_fallback, fetch_snapshot, fetch_transactions, price_at
    from core.backfill import reconstruct_snapshot_at

    tz = TIMEZONE
    # Normalize to noon so re-running backfill produces identical timestamps
    # (and the dedup-by-ts works). With `datetime.now()`, two backfills on
    # the same day would create duplicates.
    today_noon = datetime.now(tz=tz).replace(hour=12, minute=0, second=0, microsecond=0)
    if start_iso:
        start = datetime.fromisoformat(start_iso).replace(tzinfo=tz, hour=12, minute=0, second=0, microsecond=0)
    else:
        start = today_noon - timedelta(days=365)

    if frequency == "weekly":
        delta = timedelta(days=7)
    elif frequency == "biweekly":
        delta = timedelta(days=14)
    elif frequency == "monthly":
        delta = timedelta(days=30)
    else:
        raise ValueError(f"unknown frequency: {frequency!r}. Use weekly|biweekly|monthly.")

    dates = []
    d = start
    while d <= today_noon - delta:
        dates.append(d)
        d += delta
    if not dates:
        log.error(f"No dates to reconstruct between {start.date()} and {today_noon.date()} with cadence {frequency}.")
        return

    # Pick the longest range we need given the start date.
    days_back = (today_noon - start).days
    if days_back <= 30: range_str = "1m"
    elif days_back <= 90: range_str = "3m"
    elif days_back <= 365: range_str = "1y"
    elif days_back <= 365*5: range_str = "5y"
    else: range_str = "max"

    # Cache hit only saves the snapshot/txs fetch, not the historical price
    # fetches (those need an active connection). Still saves ~10s.
    cached = None if refresh else load_cached_session(CACHE_PATH)

    log.info("Connecting to Trade Republic...")
    tr = login()

    # IMPORTANT: every async call must live in a single event loop because
    # the WebSocket connection is bound to the first loop it sees.
    async def _gather():
        if cached:
            snap, txs_local, _bench = cached  # benchmarks not needed for backfill
            log.info(f"   ⚡ snapshot+txs from cache ({len(txs_local)} txs, {len(snap.positions)} positions)\n")
        else:
            log.info("Downloading current snapshot and transactions...")
            snap = await fetch_snapshot(tr, tz=tz)
            txs_local = await fetch_transactions(tr, tz=tz, gift_overrides=GIFT_COST_OVERRIDES)
            log.info(f"   {len(txs_local)} transactions, {len(snap.positions)} positions.\n")

        log.info(f"Downloading price history (range={range_str}) for {len(snap.positions)} ISINs...")
        # Build the list of exchanges to try per ISIN.
        # - The one TR returns in compactPortfolio (if any) → first.
        # - Then LSX (default for stocks/ETFs).
        # - For crypto (CRYPTO_ISINS), add BTLX and BSF as fallbacks.
        crypto_fallbacks = ["BTLX", "BSF"]
        prices: dict[str, list[dict]] = {}
        for i, p in enumerate(snap.positions):
            exchanges = []
            if p.exchange_id:
                exchanges.append(p.exchange_id)
            if "LSX" not in exchanges:
                exchanges.append("LSX")
            if p.isin in CRYPTO_ISINS:
                for ex in crypto_fallbacks:
                    if ex not in exchanges:
                        exchanges.append(ex)
            history, used = await fetch_price_history_with_fallback(
                tr, p.isin,
                range_str=range_str,
                exchanges=exchanges,
                debug=(i == 0),
            )
            prices[p.isin] = history
            status = f"{len(history)} bars (.{used})" if history else f"n/a (tried {','.join(exchanges)})"
            log.info(f"   {(p.title or p.isin)[:36]:<36}  {status}")
        return snap, txs_local, prices

    snapshot, txs, price_history = asyncio.run(_gather())
    if not cached:
        # We don't save benchmarks here (none requested). If there were any
        # in an old cache, they're lost — the next `make insights` redownloads.
        save_cached_session(CACHE_PATH, snapshot, txs)

    if not any(price_history.values()):
        log.error("\nNo position returned a price history. Backfill aborted.")
        log.error("Possible causes: aggregateHistory unavailable for any ISIN, exchange issue.")
        return

    log.info(f"\nReconstructing {len(dates)} snapshots ({frequency})...")
    records: list[tuple] = []
    for d in dates:
        prices = {}
        for isin, history in price_history.items():
            p = price_at(history, d)
            if p is not None:
                prices[isin] = p
        snap = reconstruct_snapshot_at(d, snapshot, txs, prices)
        if not snap.positions and snap.cash_eur == snapshot.cash_eur:
            log.info(f"   {d.date()}  no activity — skip")
            continue
        records.append((snap, None))
        log.info(
            f"   {d.date()}  cash={snap.cash_eur:>9.2f}  "
            f"pos={snap.positions_value_eur:>9.2f}  total={snap.total_eur:>9.2f}  "
            f"({len(snap.positions)} pos)"
        )

    log.info(f"\nWriting {len(records)} snapshots to `{SNAPSHOTS_SHEET}` (batched, dedup enabled)...")
    spreadsheet = open_spreadsheet()
    store = _make_snapshot_store(spreadsheet)
    try:
        written = store.append_batch(records, skip_existing=True)
        skipped = len(records) - written
        log.info(f"OK: {written} new snapshots written, {skipped} already existed (dedup by ts).")
        if written > 0:
            log.info(f"   Next `make insights` will have MWR YTD/12m if there are snapshots before the period.")
    except Exception as e:
        log.error(f"⚠ Batch write failed: {e}")
        log.error(f"   If it's a rate limit, wait 1-2 minutes and rerun — dedup will skip the already-written ones.")
