"""Trade Republic event/portfolio fetching + raw-to-Sheet normalization.

This is the I/O layer for the TR broker. The functions here:
  - Talk to TR's pytr client (login, websocket loops).
  - Normalize raw timeline events into the dict shape used by sync().
  - Filter events into the per-Sheet flows (expenses / income).

Pure-data adapter logic (raw → Transaction / PortfolioSnapshot) lives in
`brokers/tr/adapter.py`. Parser primitives live in `brokers/tr/parser.py`.
"""
from datetime import datetime

from pytr.portfolio import Portfolio
from pytr.timeline import Timeline

import tr_sync as _ts


def _patch_compact_portfolio_with_sec_acc_no(tr):
    """Workaround for pytr-org/pytr#246: compactPortfolio returns [] without secAccNo."""
    settings = tr.settings()
    sec_acc_no = settings.get("securitiesAccountNumber")
    if not sec_acc_no:
        for k in ("accountNumber", "secAccNo"):
            if settings.get(k):
                sec_acc_no = settings[k]
                break
    if not sec_acc_no:
        raise RuntimeError(f"Could not find securitiesAccountNumber in settings(): keys={list(settings.keys())}")

    async def compact_portfolio_patched():
        return await tr.subscribe({"type": "compactPortfolio", "secAccNo": sec_acc_no})

    tr.compact_portfolio = compact_portfolio_patched


async def fetch_tr_portfolio(tr):
    """Return the user's current TR positions (instruments only, no cash).

    Each position is a dict with at least {instrumentId, netValue, ...}. Use
    `fetch_tr_portfolio_and_cash` to get instruments + cash in one go.
    """
    _patch_compact_portfolio_with_sec_acc_no(tr)
    p = Portfolio(tr, include_watchlist=False, lang="es", output=None)
    await p.portfolio_loop()
    return p.portfolio


async def fetch_tr_portfolio_and_cash(tr):
    """Return (portfolio_positions, cash_list). Reuses pytr's Portfolio loop."""
    _patch_compact_portfolio_with_sec_acc_no(tr)
    p = Portfolio(tr, include_watchlist=False, lang="es", output=None)
    await p.portfolio_loop()
    return p.portfolio, (p.cash or [])


async def fetch_tr_events(tr, not_before_ts: float = 0.0):
    """Download raw timeline events from TR starting at `not_before_ts` (epoch).

    Returns the events as TR emits them (locale: whatever the client's
    `_locale` is set to, defaults to German).
    """
    collected = []

    def on_event(event):
        collected.append(event)

    _ts.PYTR_OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
    timeline = Timeline(
        tr,
        output_path=_ts.PYTR_OUTPUT_PATH,
        not_before=not_before_ts,
        store_event_database=False,
        event_callback=on_event,
    )
    await timeline.tl_loop()
    return collected


def normalize_event(raw):
    """Normalize a raw TR event into the dict shape used by sync_to_sheet().

    Returns None if the event has no usable amount or timestamp.

    Output dict fields:
      - id:        unique event id (used to dedup).
      - ts:        datetime in TIMEZONE.
      - month_key: (year, month) tuple — used for grouping.
      - concepto:  the event's title (gets written to the Sheet).
      - importe:   abs(value) rounded to 2 decimals.
      - type:      TR's eventType.
      - raw_value: the original signed value (used to classify expense/income).

    Note: keys 'concepto' / 'importe' are kept in Spanish for compatibility
    with downstream Sheet writers and existing tests.
    """
    amount_block = raw.get("amount") or {}
    value = amount_block.get("value")
    if value is None:
        return None
    ts_str = raw.get("timestamp")
    if not ts_str:
        return None
    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(_ts.TIMEZONE)
    title = (raw.get("title") or "").strip()
    return {
        "id": raw["id"],
        "ts": ts,
        "month_key": (ts.year, ts.month),
        "concepto": title,
        "importe": round(abs(value), 2),
        "type": raw.get("eventType"),
        "raw_value": value,
    }


def _matches_ignore(raw, ignore_cfg):
    """True if the event should be ignored according to the configured patterns."""
    title = (raw.get("title") or "").lower()
    subtitle = (raw.get("subtitle") or "").lower()
    for needle in ignore_cfg.get("title_contains", []):
        if needle and needle in title:
            return True
    for needle in ignore_cfg.get("subtitle_contains", []):
        if needle and needle in subtitle:
            return True
    return False


def filter_events_by_flow(events):
    """Return {sheet_name: [normalized_events]} according to the per-tab filters."""
    out = {name: [] for name in _ts.SHEET_CONFIGS}
    ignored = {name: [] for name in _ts.SHEET_CONFIGS}
    for raw in events:
        if raw.get("status") in _ts.EXCLUDED_STATUSES:
            continue
        et = raw.get("eventType")
        for name, cfg in _ts.SHEET_CONFIGS.items():
            if et not in cfg["event_types"]:
                continue
            n = normalize_event(raw)
            if not n or n["importe"] <= 0:
                continue
            sign = cfg["expected_sign"]
            if sign < 0 and n["raw_value"] >= 0:
                continue
            if sign > 0 and n["raw_value"] <= 0:
                continue
            if _matches_ignore(raw, cfg.get("ignore", {})):
                ignored[name].append(n)
                break
            out[name].append(n)
            break
    for name, items in ignored.items():
        if items:
            _ts.log.info(f"  [{name}] {len(items)} event(s) ignored per config.yaml → ignore_events:")
            for n in items:
                _ts.log.info(f"     - {n['ts'].date()}  {n['importe']:>8.2f} €  '{n['concepto']}'")
    return out
