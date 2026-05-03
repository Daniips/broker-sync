"""--portfolio: snapshot of current portfolio values written to the Sheet."""
import asyncio

from pytr.account import login

from tr_sync import (
    PORTFOLIO_CELL_MAP,
    PORTFOLIO_SHEET,
    PORTFOLIO_VALUE_RANGE,
    SNAPSHOTS_SHEET,
    TIMEZONE,
    _make_snapshot_store,
    fetch_tr_portfolio,
    is_feature_enabled,
    log,
    open_spreadsheet,
    write_status,
)


def sync_portfolio(dry_run: bool):
    """Portfolio snapshot: write current `netValue` per ISIN to PORTFOLIO_SHEET!PORTFOLIO_VALUE_RANGE.

    Reads `PORTFOLIO_CELL_MAP` from config and writes one row per configured
    ISIN, in order. ISINs not found in TR get an empty cell and a warning.
    With `dry_run=True` only prints to console, doesn't touch the Sheet.
    No-op if `features.portfolio=false`.
    """
    if not is_feature_enabled("portfolio"):
        log.info("Feature 'portfolio' disabled (config or broker).")
        return
    log.info("Connecting to Trade Republic...")
    tr = login()
    positions = asyncio.run(fetch_tr_portfolio(tr))
    by_isin = {p["instrumentId"]: p for p in positions}

    log.info(f"\n[Portfolio] {PORTFOLIO_SHEET}!{PORTFOLIO_VALUE_RANGE}")
    values = []
    missing = []
    for isin, label in PORTFOLIO_CELL_MAP:
        pos = by_isin.get(isin)
        if not pos or "netValue" not in pos:
            missing.append(label)
            values.append([""])
            log.warning(f"   {label:<12} ISIN={isin}  (not found in TR)")
            continue
        net_value = float(pos["netValue"])
        values.append([net_value])
        log.info(f"   {label:<12} ISIN={isin}  {net_value:>10.2f} €")

    if dry_run:
        log.info("\n[dry-run] nothing written to the Sheet.")
        return

    spreadsheet = open_spreadsheet()
    worksheet = spreadsheet.worksheet(PORTFOLIO_SHEET)
    worksheet.update(range_name=PORTFOLIO_VALUE_RANGE, values=values, value_input_option="USER_ENTERED")
    log.info(f"\nOK: {len(values) - len(missing)}/{len(values)} cells written to {PORTFOLIO_SHEET}!{PORTFOLIO_VALUE_RANGE}")
    write_status(spreadsheet, "portfolio")

    # Persist the full snapshot (cash + positions) for MWR history.
    try:
        from brokers.tr.adapter import fetch_snapshot
        from core.metrics import cost_basis_total as _cb_total
        snap = asyncio.run(fetch_snapshot(tr, tz=TIMEZONE))
        store = _make_snapshot_store(spreadsheet)
        store.append(snap, _cb_total(snap))
        log.info(f"   snapshot saved to `{SNAPSHOTS_SHEET}` (hidden).")
    except Exception as e:
        log.warning(f"   ⚠ could not save snapshot ({e})")
