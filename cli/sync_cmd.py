"""Default subcommand: sync TR events with the Sheet (expenses/income/investments)."""
import asyncio
from datetime import datetime, timedelta

from pytr.account import login

from tr_sync import (
    DEFAULT_BUFFER_DAYS,
    EXPENSES_SHEET,
    FEATURES,
    INCOME_SHEET,
    TIMEZONE,
    append_synced_ids,
    fetch_tr_events,
    filter_events_by_flow,
    load_synced_ids,
    log,
    open_spreadsheet,
    sync_investments,
    sync_to_sheet,
    write_status,
)


def sync(dry_run: bool, since: datetime | None, init_mode: bool):
    """Sync TR events with the Sheet (expenses, income, investments).

    Modes:
      - normal (defaults): fetch events from start-of-month − DEFAULT_BUFFER_DAYS;
        write new expenses/income (deduping via _sync_state); recompute the
        investments tab for the current month (past months are not touched).
      - `since`: custom window to reprocess a range; useful when filling backlog.
      - `init_mode`: fetch ALL history, mark every event as already synced
        but DO NOT write to expenses/income. Used to align the Sheet with TR
        when starting from scratch. Investments are not touched.

    With `dry_run=True` events are fetched but nothing is written.
    """
    log.info("Connecting to Trade Republic...")
    tr = login()
    if init_mode:
        not_before = 0.0
        log.info("  init mode: downloading full history")
    elif since:
        not_before = since.timestamp()
        log.info(f"  window: since {since.date()}")
    else:
        now = datetime.now(tz=TIMEZONE)
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        cutoff = start_of_month - timedelta(days=DEFAULT_BUFFER_DAYS)
        not_before = cutoff.timestamp()
        log.info(f"  window: since {cutoff.date()} (start of current month - {DEFAULT_BUFFER_DAYS} days)")
    raw_events = asyncio.run(fetch_tr_events(tr, not_before))
    log.info(f"  {len(raw_events)} events downloaded")

    flows = filter_events_by_flow(raw_events)
    # Apply feature toggles: empty the disabled tab's list so nothing gets written.
    if not FEATURES.get("expenses", True):
        flows[EXPENSES_SHEET] = []
        log.info("  features.expenses=false → expenses are not synced")
    if not FEATURES.get("income", True):
        flows[INCOME_SHEET] = []
        log.info("  features.income=false → income is not synced")
    if since:
        for k in flows:
            flows[k] = [e for e in flows[k] if e["ts"] >= since]
    flow_total = sum(len(v) for v in flows.values())
    log.info(f"  {flow_total} in expenses/income")

    log.info("Opening Google Sheet...")
    spreadsheet = open_spreadsheet()

    if init_mode:
        all_ids = [e["id"] for v in flows.values() for e in v]
        append_synced_ids(spreadsheet, all_ids)
        log.info(f"\n[INIT] {len(all_ids)} expense/income items marked as synced.")
        log.info("Investments untouched (existing values are preserved).")
        log.info("Future runs: new expenses/income + recomputed investments for the current month.")
        return

    synced_ids = load_synced_ids(spreadsheet)
    new_flows = {
        name: [e for e in events if e["id"] not in synced_ids]
        for name, events in flows.items()
    }
    new_total = sum(len(v) for v in new_flows.values())
    log.info(f"  {new_total} new in expenses/income (rest already synced)")

    written_ids = []
    for sheet_name, sheet_txs in new_flows.items():
        if sheet_txs:
            written_ids.extend(sync_to_sheet(spreadsheet, sheet_name, sheet_txs, dry_run))

    if not dry_run and written_ids:
        append_synced_ids(spreadsheet, written_ids)

    if FEATURES.get("investments", True):
        sync_investments(spreadsheet, raw_events, dry_run)
    else:
        log.info("\n  features.investments=false → investments are not synced")

    if not dry_run:
        write_status(spreadsheet, "sync")
        log.info(f"\nOK: {len(written_ids)} expense/income items + investments recomputed.")
