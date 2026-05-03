"""Investment aggregation + writer for the Dinero invertido <year> tab.

Aggregates BUY-side events from TR (savings plan, saveback, regular trades)
into a {(asset, year, month): total} dict, then writes only the current
month and future months to the Sheet (past months are not touched, so the
user's manual edits in earlier months survive).
"""
from collections import defaultdict
from datetime import datetime

import gspread

from tr_sync import (
    ASSET_NAME_MAP,
    EXCLUDED_STATUSES,
    INVESTMENT_EVENT_TYPES,
    INVESTMENTS_SHEET,
    INVESTMENTS_SHEET_YEAR,
    MONTH_NAMES_ES,
    SAVEBACK_LABEL,
    TIMEZONE,
    log,
)


def aggregate_investments(events):
    """Return {(asset_label, year, month): total_amount}."""
    totals: dict[tuple[str, int, int], float] = defaultdict(float)
    seen_unknown = set()
    for raw in events:
        et = raw.get("eventType")
        if et not in INVESTMENT_EVENT_TYPES:
            continue
        if raw.get("status") in EXCLUDED_STATUSES:
            continue
        title = (raw.get("title") or "").strip()
        if et == "SAVEBACK_AGGREGATE":
            asset = SAVEBACK_LABEL
        else:
            asset = ASSET_NAME_MAP.get(title)
            if asset is None:
                if title not in seen_unknown:
                    log.warning(f"   unknown asset in TR: '{title}' → new row with that name")
                    seen_unknown.add(title)
                asset = title
        value = (raw.get("amount") or {}).get("value")
        if value is None:
            continue
        # TRADING_TRADE_EXECUTED covers buys and sells; we only sum buys (negative amount).
        if et == "TRADING_TRADE_EXECUTED" and value >= 0:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(TIMEZONE)
        totals[(asset, ts.year, ts.month)] += abs(value)
    return totals


def sync_investments(spreadsheet, events, dry_run):
    """Recompute totals per (asset, month) and overwrite the cells.

    Only writes for INVESTMENTS_SHEET_YEAR, current month or future ones.
    """
    now = datetime.now(tz=TIMEZONE)
    totals = aggregate_investments(events)
    totals = {
        (a, y, m): v for (a, y, m), v in totals.items()
        if y == INVESTMENTS_SHEET_YEAR
        and (y > now.year or (y == now.year and m >= now.month))
    }
    if not totals:
        log.info("\n[Investments] nothing to update (current or future months)")
        return

    log.info(f"\n[Investments] {len(totals)} cells to update:")
    for (asset, year, month), v in sorted(totals.items()):
        log.info(f"   {asset:<14} / {MONTH_NAMES_ES[month-1]} {year}: {v:>8.2f}")

    if dry_run:
        return

    try:
        worksheet = spreadsheet.worksheet(INVESTMENTS_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        log.warning(f"   tab '{INVESTMENTS_SHEET}' not found — skipping investments")
        return

    col_a = worksheet.col_values(1)
    headers = worksheet.row_values(1)
    asset_to_row = {label.strip(): i for i, label in enumerate(col_a, start=1) if label.strip()}
    month_to_col = {h.strip().lower(): i for i, h in enumerate(headers, start=1) if h.strip()}

    for asset in sorted({a for (a, _, _) in totals.keys()}):
        if asset not in asset_to_row:
            new_row = max(asset_to_row.values(), default=1) + 1
            worksheet.update_cell(new_row, 1, asset)
            asset_to_row[asset] = new_row
            log.info(f"   new row '{asset}' at {new_row}")

    for (year, month) in sorted({(y, m) for (_, y, m) in totals.keys()}):
        header = f"{MONTH_NAMES_ES[month-1]} {year}".lower()
        if header not in month_to_col:
            new_col = max(month_to_col.values(), default=1) + 1
            month_str = f"{MONTH_NAMES_ES[month-1]} {year}"
            worksheet.update_cell(1, new_col, month_str)
            month_to_col[header] = new_col
            log.info(f"   new column '{month_str}' at {new_col}")

    cells = []
    for (asset, year, month), value in totals.items():
        header = f"{MONTH_NAMES_ES[month-1]} {year}".lower()
        cells.append(gspread.cell.Cell(asset_to_row[asset], month_to_col[header], round(value, 2)))
    worksheet.update_cells(cells, value_input_option="USER_ENTERED")
    log.info(f"   {len(cells)} cells written")
