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
    log.info("🔍 tr-sync doctor — setup check\n")
    errors = []
    warnings = []

    # 1. Config (already validated at module load; here we just confirm)
    log.info(f"✓ config.yaml loaded and validated (sheet_id={SHEET_ID[:10]}...)")
    log.info(f"  active features: {', '.join(k for k, v in FEATURES.items() if v)}")

    # 2. portfolio_cell_map vs portfolio_value_range
    col, row_start, row_end = parse_a1_column_range(PORTFOLIO_VALUE_RANGE)
    if col is None:
        errors.append(f"portfolio_value_range='{PORTFOLIO_VALUE_RANGE}' not parseable as a single-column A1 range.")
    else:
        cells = row_end - row_start + 1
        n = len(PORTFOLIO_CELL_MAP)
        if cells != n:
            errors.append(
                f"portfolio_value_range '{PORTFOLIO_VALUE_RANGE}' has {cells} cells, "
                f"but portfolio_cell_map has {n} entries. Adjust one of the two."
            )
        else:
            log.info(f"✓ portfolio_value_range {PORTFOLIO_VALUE_RANGE} ({cells} cells) matches portfolio_cell_map ({n} entries)")

    # 3. pytr session
    pytr_dir = Path.home() / ".pytr"
    if pytr_dir.exists() and any(pytr_dir.iterdir()):
        log.info(f"✓ pytr session present at {pytr_dir}")
    else:
        warnings.append(f"{pytr_dir} does not exist or is empty. Run `make login` before `make sync`.")

    # 4. Google Sheet
    try:
        spreadsheet = open_spreadsheet()
        log.info(f"✓ Google Sheet reachable: '{spreadsheet.title}'")
    except Exception as e:
        errors.append(f"Could not open Google Sheet (sheet_id='{SHEET_ID}'): {e}")
        spreadsheet = None

    # 5. Tabs
    if spreadsheet is not None:
        existing_tabs = {ws.title for ws in spreadsheet.worksheets()}
        log.info(f"  tabs in the Sheet: {sorted(existing_tabs)}")

        required = []
        if FEATURES.get("expenses", True):    required.append(EXPENSES_SHEET)
        if FEATURES.get("income", True):      required.append(INCOME_SHEET)
        if FEATURES.get("investments", True): required.append(INVESTMENTS_SHEET)
        if FEATURES.get("portfolio", True):   required.append(PORTFOLIO_SHEET)

        for tab in required:
            if tab in existing_tabs:
                log.info(f"✓ Tab '{tab}' present")
            else:
                errors.append(f"Tab '{tab}' not found. Run `make init-sheet` or create it manually (see SHEET_TEMPLATE.md).")

        for tab in (STATUS_SHEET, SYNC_STATE_SHEET):
            if tab in existing_tabs:
                log.info(f"✓ Tab '{tab}' present")
            else:
                warnings.append(f"Tab '{tab}' does not exist (it will be created automatically on the first sync).")

    # Summary
    log.info("")
    if warnings:
        log.info("⚠️  Non-critical warnings:")
        for w in warnings:
            log.info(f"   - {w}")
        log.info("")
    if errors:
        log.error("❌ Errors you need to fix:")
        for e in errors:
            log.error(f"   - {e}")
        return 1
    if warnings:
        log.info("✅ Setup OK (with warnings). You can run `make sync`.")
    else:
        log.info("✅ All OK. You can run `make sync`, `make portfolio` or `make renta`.")
    return 0
