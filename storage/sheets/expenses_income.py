"""Google-Sheets writers for the Gastos / Ingresos tabs.

Two layouts are supported (selectable in `config.yaml > sheets.<tab>_layout`):
  - "monthly_columns" (default): months as Concepto+Importe column pairs,
    with a summary block at the bottom that the writer pushes down to make
    room for new rows.
  - "ledger": one row per event with Date / Concept / Amount columns at
    user-chosen letters. Append-only.

Module-level config (LEDGER_COLUMNS, MONTH_NAMES_ES, SHEET_CONFIGS, ...)
is read lazily from `tr_sync` at call time so tests can monkeypatch it.
"""
import gspread

import tr_sync as _ts
from core.utils import column_letter_to_index


def find_month_columns(worksheet, year, month):
    """Locate the (col_concepto, col_importe) pair by header in row 1.

    Assumes the concept column sits to the left of the amount column.
    """
    headers = worksheet.row_values(1)
    expected = _ts.MONTH_HEADER_AMOUNT.format(month=_ts.MONTH_NAMES_ES[month-1], year=year).lower()
    for i, h in enumerate(headers, start=1):
        if h.strip().lower() == expected:
            if i > 1:
                return (i - 1, i)
    return (None, None)


def create_month_columns(worksheet, year, month):
    """Append two new columns (concept + amount) at the right of row 1."""
    headers = worksheet.row_values(1)
    next_col = len(headers) + 1
    concepto_header = _ts.MONTH_HEADER_CONCEPT.format(month=_ts.MONTH_NAMES_ES[month-1], year=year)
    importe_header = _ts.MONTH_HEADER_AMOUNT.format(month=_ts.MONTH_NAMES_ES[month-1], year=year)
    worksheet.update_cell(1, next_col, concepto_header)
    worksheet.update_cell(1, next_col + 1, importe_header)
    _ts.log.info(f"   [{worksheet.title}] columns created: '{concepto_header}' | '{importe_header}'")
    return (next_col, next_col + 1)


def find_summary_block_start(worksheet, col_concepto, markers):
    col_values = worksheet.col_values(col_concepto)
    for i, v in enumerate(col_values, start=1):
        if v.strip().lower() in markers:
            return i
    return None


def find_first_empty_row(worksheet, col_importe):
    col_values = worksheet.col_values(col_importe)
    for i, v in enumerate(col_values, start=1):
        if v == "" and i > 1:
            return i
    return len(col_values) + 1 if col_values else 2


def shift_summary_block(worksheet, col_concepto, summary_start, shift_rows):
    body = {"requests": [{
        "insertRange": {
            "range": {
                "sheetId": worksheet.id,
                "startRowIndex": summary_start - 1,
                "endRowIndex": summary_start - 1 + shift_rows,
                "startColumnIndex": col_concepto - 1,
                "endColumnIndex": col_concepto + 1,
            },
            "shiftDimension": "ROWS",
        }
    }]}
    worksheet.spreadsheet.batch_update(body)


def write_transactions(worksheet, col_concepto, col_importe, start_row, transactions):
    cells = []
    for offset, tx in enumerate(transactions):
        r = start_row + offset
        cells.append(gspread.cell.Cell(r, col_concepto, tx["concepto"]))
        cells.append(gspread.cell.Cell(r, col_importe, tx["importe"]))
    worksheet.update_cells(cells, value_input_option="USER_ENTERED")


def sync_to_sheet(spreadsheet, sheet_name, txs, dry_run):
    """Write `txs` to `sheet_name` according to its configured layout.

    Layouts:
      - 'monthly_columns' (default): months as Concepto+Importe column pairs.
        Detects the summary block at the bottom of the month and inserts
        the new rows just above it.
      - 'ledger': each event is a row with Date/Concept/Amount columns.
        Simple append to the first empty row. No summary-block logic.
    """
    if not txs:
        return []
    cfg = _ts.SHEET_CONFIGS[sheet_name]
    layout = cfg.get("layout", _ts.LAYOUT_DEFAULT)
    if layout == "ledger":
        return _sync_ledger_layout(spreadsheet, sheet_name, txs, dry_run)
    return _sync_monthly_columns_layout(spreadsheet, sheet_name, txs, dry_run, cfg)


def _sync_monthly_columns_layout(spreadsheet, sheet_name, txs, dry_run, cfg):
    """Original layout: months as Concepto+Importe column pairs, summary block at the bottom."""
    worksheet = spreadsheet.worksheet(sheet_name)

    by_month: dict[tuple[int, int], list] = {}
    for e in txs:
        by_month.setdefault(e["month_key"], []).append(e)

    written_ids = []
    for (year, month), month_txs in sorted(by_month.items()):
        month_txs.sort(key=lambda e: e["ts"])
        _ts.log.info(f"\n[{sheet_name} / {_ts.MONTH_NAMES_ES[month-1].capitalize()} {year}] {len(month_txs)} movements:")
        for tx in month_txs:
            _ts.log.info(f"   {tx['ts'].date()}  {tx['importe']:>8.2f}  {tx['concepto']}")

        if dry_run:
            continue

        col_concepto, col_importe = find_month_columns(worksheet, year, month)
        if col_concepto is None:
            col_concepto, col_importe = create_month_columns(worksheet, year, month)

        first_empty = find_first_empty_row(worksheet, col_importe)
        summary_start = find_summary_block_start(worksheet, col_concepto, cfg["summary_markers"])
        n = len(month_txs)

        if summary_start is not None and first_empty >= summary_start:
            first_empty = summary_start
            shift_summary_block(worksheet, col_concepto, summary_start, n)
        elif summary_start is not None:
            available = summary_start - first_empty
            if n > available:
                shift_summary_block(worksheet, col_concepto, summary_start, n - available)

        write_transactions(worksheet, col_concepto, col_importe, first_empty, month_txs)
        written_ids.extend([t["id"] for t in month_txs])

    return written_ids


def _sync_ledger_layout(spreadsheet, sheet_name, txs, dry_run):
    """Ledger layout: one row per event with configurable date/concept/amount columns."""
    worksheet = spreadsheet.worksheet(sheet_name)
    txs_sorted = sorted(txs, key=lambda e: e["ts"])

    _ts.log.info(f"\n[{sheet_name} / ledger] {len(txs_sorted)} movements:")
    for tx in txs_sorted:
        _ts.log.info(f"   {tx['ts'].date()}  {tx['importe']:>8.2f}  {tx['concepto']}")

    if dry_run:
        return []

    # Read LEDGER_COLUMNS / LEDGER_HEADERS from tr_sync at call time so
    # tests can monkeypatch them via `tr_sync.LEDGER_COLUMNS = ...`.
    ledger_columns = _ts.LEDGER_COLUMNS
    ledger_headers = _ts.LEDGER_HEADERS

    date_col = column_letter_to_index(ledger_columns["date"])
    concept_col = column_letter_to_index(ledger_columns["concept"])
    amount_col = column_letter_to_index(ledger_columns["amount"])

    # Ensure the 3 headers exist in row 1 (one per configured column).
    existing_row1 = worksheet.row_values(1)
    headers_to_set = [
        (date_col, ledger_headers[0]),
        (concept_col, ledger_headers[1]),
        (amount_col, ledger_headers[2]),
    ]
    for col_idx, header in headers_to_set:
        current = existing_row1[col_idx - 1] if col_idx - 1 < len(existing_row1) else ""
        if current.strip().lower() != header.lower():
            worksheet.update_cell(1, col_idx, header)

    # First empty row: look at the 'date' column.
    col_date_values = worksheet.col_values(date_col)
    first_empty = max(len(col_date_values) + 1, 2)

    # Write cell-by-cell so non-contiguous columns (any order) work.
    cells = []
    for offset, tx in enumerate(txs_sorted):
        row = first_empty + offset
        cells.append(gspread.cell.Cell(row, date_col, tx["ts"].date().isoformat()))
        cells.append(gspread.cell.Cell(row, concept_col, tx["concepto"]))
        cells.append(gspread.cell.Cell(row, amount_col, tx["importe"]))
    worksheet.update_cells(cells, value_input_option="USER_ENTERED")
    return [t["id"] for t in txs_sorted]
