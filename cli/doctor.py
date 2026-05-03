"""--doctor: setup health check (no TR network calls so SMS prompts don't fire)."""
from pathlib import Path

from tr_sync import (
    EXPENSES_SHEET,
    FEATURES,
    INCOME_SHEET,
    INVESTMENTS_SHEET,
    PORTFOLIO_CELL_MAP,
    PORTFOLIO_SHEET,
    PORTFOLIO_VALUE_RANGE,
    SHEET_ID,
    STATUS_SHEET,
    SYNC_STATE_SHEET,
    log,
    open_spreadsheet,
)
from core.utils import parse_a1_column_range


def doctor():
    """Health check: verify the setup is ready to sync.

    Steps (in order; nothing TR-network-bound to avoid SMS prompts):
      1. config.yaml loaded and validated at import (if we got here, OK).
      2. portfolio_cell_map and portfolio_value_range sized consistently.
      3. Local pytr session present (~/.pytr/).
      4. Google Sheet reachable (gspread OAuth + valid sheet_id).
      5. Required tabs present (depends on which features are active).

    Returns 0 if everything is OK, 1 if there are blocking errors.
    """
    log.info("🔍 tr-sync doctor — chequeo de setup\n")
    errors = []
    warnings = []

    # 1. Config (already validated at module load; here we just confirm)
    log.info(f"✓ config.yaml cargado y validado (sheet_id={SHEET_ID[:10]}...)")
    log.info(f"  features activos: {', '.join(k for k, v in FEATURES.items() if v)}")

    # 2. portfolio_cell_map vs portfolio_value_range
    col, row_start, row_end = parse_a1_column_range(PORTFOLIO_VALUE_RANGE)
    if col is None:
        errors.append(f"portfolio_value_range='{PORTFOLIO_VALUE_RANGE}' no parseable como rango A1 de una columna.")
    else:
        cells = row_end - row_start + 1
        n = len(PORTFOLIO_CELL_MAP)
        if cells != n:
            errors.append(
                f"portfolio_value_range '{PORTFOLIO_VALUE_RANGE}' tiene {cells} celdas, "
                f"pero portfolio_cell_map tiene {n} entradas. Ajusta uno de los dos."
            )
        else:
            log.info(f"✓ portfolio_value_range {PORTFOLIO_VALUE_RANGE} ({cells} celdas) coincide con portfolio_cell_map ({n} entradas)")

    # 3. pytr session
    pytr_dir = Path.home() / ".pytr"
    if pytr_dir.exists() and any(pytr_dir.iterdir()):
        log.info(f"✓ Sesión pytr presente en {pytr_dir}")
    else:
        warnings.append(f"{pytr_dir} no existe o está vacío. Lanza `make login` antes de `make sync`.")

    # 4. Google Sheet
    try:
        spreadsheet = open_spreadsheet()
        log.info(f"✓ Google Sheet accesible: '{spreadsheet.title}'")
    except Exception as e:
        errors.append(f"No se pudo abrir Google Sheet (sheet_id='{SHEET_ID}'): {e}")
        spreadsheet = None

    # 5. Tabs
    if spreadsheet is not None:
        existing_tabs = {ws.title for ws in spreadsheet.worksheets()}
        log.info(f"  pestañas en la Sheet: {sorted(existing_tabs)}")

        required = []
        if FEATURES.get("expenses", True):    required.append(EXPENSES_SHEET)
        if FEATURES.get("income", True):      required.append(INCOME_SHEET)
        if FEATURES.get("investments", True): required.append(INVESTMENTS_SHEET)
        if FEATURES.get("portfolio", True):   required.append(PORTFOLIO_SHEET)

        for tab in required:
            if tab in existing_tabs:
                log.info(f"✓ Pestaña '{tab}' presente")
            else:
                errors.append(f"Pestaña '{tab}' no encontrada. Lanza `make init-sheet` o créala a mano (ver SHEET_TEMPLATE.md).")

        for tab in (STATUS_SHEET, SYNC_STATE_SHEET):
            if tab in existing_tabs:
                log.info(f"✓ Pestaña '{tab}' presente")
            else:
                warnings.append(f"Pestaña '{tab}' no existe (se creará automáticamente al primer sync).")

    # Summary
    log.info("")
    if warnings:
        log.info("⚠️  Avisos no críticos:")
        for w in warnings:
            log.info(f"   - {w}")
        log.info("")
    if errors:
        log.error("❌ Errores que tienes que resolver:")
        for e in errors:
            log.error(f"   - {e}")
        return 1
    if warnings:
        log.info("✅ Setup OK (con avisos). Puedes lanzar `make sync`.")
    else:
        log.info("✅ Todo OK. Puedes lanzar `make sync`, `make portfolio` o `make renta`.")
    return 0
