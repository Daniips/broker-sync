"""--init-sheet: create missing tabs in the Google Sheet with the minimum schema."""
from tr_sync import (
    ASSET_NAME_MAP,
    EXPENSES_SHEET,
    FEATURES,
    INCOME_SHEET,
    INIT_HEADERS,
    INVESTMENTS_SHEET,
    LAYOUT_DEFAULT,
    LEDGER_COLUMNS,
    LEDGER_HEADERS,
    PORTFOLIO_CELL_MAP,
    PORTFOLIO_SHEET,
    PORTFOLIO_VALUE_RANGE,
    SHEET_CONFIGS,
    log,
    open_spreadsheet,
)
from core.utils import column_letter_to_index, parse_a1_column_range


def init_sheet(dry_run: bool = False):
    """Create the missing tabs in the Google Sheet with the minimum schema.

    Idempotent: if a tab already exists it's left alone and only logged.
    Tabs created (when the matching feature is enabled):
      - Gastos / Ingresos / Dinero invertido <year>: empty; sync will create
        the month headers the first time it writes.
      - Portfolio: with `portfolio_cell_map` labels in the column to the
        left of `portfolio_value_range`, so it's visible which asset
        corresponds to each cell.
      - Estado sync and _sync_state are created automatically later
        (status on first write_status, sync_state on first load_synced_ids).
    """
    log.info("Abriendo Google Sheet...")
    spreadsheet = open_spreadsheet()
    log.info(f"  → {spreadsheet.title}")
    existing = {ws.title for ws in spreadsheet.worksheets()}
    log.info(f"  Pestañas existentes: {sorted(existing)}")

    targets = []
    if FEATURES.get("expenses", True):
        targets.append((EXPENSES_SHEET, "expenses"))
    if FEATURES.get("income", True):
        targets.append((INCOME_SHEET, "income"))
    if FEATURES.get("investments", True):
        targets.append((INVESTMENTS_SHEET, "investments"))
    if FEATURES.get("portfolio", True):
        targets.append((PORTFOLIO_SHEET, "portfolio"))

    created = 0
    for name, kind in targets:
        if name in existing:
            log.info(f"  ✓ '{name}' ya existe — no se toca.")
            continue
        if dry_run:
            log.info(f"  [dry-run] crearía '{name}' ({kind}).")
            continue
        ws = spreadsheet.add_worksheet(title=name, rows=200, cols=26)
        log.info(f"  ✚ Creada '{name}' ({kind}).")
        created += 1

        if kind == "portfolio":
            col, row_start, row_end = parse_a1_column_range(PORTFOLIO_VALUE_RANGE)
            if col is None:
                log.warning(f"     portfolio_value_range='{PORTFOLIO_VALUE_RANGE}' no parseable; "
                            f"no se prerellena la columna de labels.")
            else:
                col_idx = column_letter_to_index(col)
                # Header row at row_start - 1 (if there's space)
                if row_start >= 2:
                    ws.update_cell(row_start - 1, max(col_idx - 1, 1), INIT_HEADERS["portfolio_asset_column"])
                    ws.update_cell(row_start - 1, col_idx, INIT_HEADERS["portfolio_value_column"])
                # Labels in the column to the left of the range
                if col_idx >= 2:
                    label_col = col_idx - 1
                    for i, (_isin, label) in enumerate(PORTFOLIO_CELL_MAP):
                        r = row_start + i
                        if r > row_end:
                            break
                        ws.update_cell(r, label_col, label)
                    log.info(f"     labels prerellenados en columna {chr(ord('A') + label_col - 1)}.")

        elif kind == "investments":
            ws.update_cell(1, 1, INIT_HEADERS["investments_asset_column"])
            assets = sorted(set(ASSET_NAME_MAP.values()))
            for i, asset in enumerate(assets, start=2):
                ws.update_cell(i, 1, asset)
            log.info(f"     {len(assets)} activos prerellenados en columna A.")

        elif kind in ("expenses", "income"):
            sheet_layout = SHEET_CONFIGS[name].get("layout", LAYOUT_DEFAULT)
            if sheet_layout == "ledger":
                date_col = column_letter_to_index(LEDGER_COLUMNS["date"])
                concept_col = column_letter_to_index(LEDGER_COLUMNS["concept"])
                amount_col = column_letter_to_index(LEDGER_COLUMNS["amount"])
                ws.update_cell(1, date_col, LEDGER_HEADERS[0])
                ws.update_cell(1, concept_col, LEDGER_HEADERS[1])
                ws.update_cell(1, amount_col, LEDGER_HEADERS[2])
                cols_repr = f"{LEDGER_COLUMNS['date']}/{LEDGER_COLUMNS['concept']}/{LEDGER_COLUMNS['amount']}"
                log.info(f"     headers ledger en columnas {cols_repr} de fila 1.")

    log.info(f"\n✅ init-sheet completado. {created} pestaña(s) creada(s).")
    if not dry_run:
        log.info(f"   Pestañas Estado sync y _sync_state se crearán automáticamente al primer sync.")
