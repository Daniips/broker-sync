#!/usr/bin/env python3
"""Sync Trade Republic expenses and income with the Sheet."""

import sys

# Shortcircuit for the `config` subcommand: delegate to config_cli BEFORE
# loading heavy imports (pytr, gspread) or config.yaml itself. This allows
# `python tr_sync.py config init` to work even before you have a Sheet.
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "config":
    from config_cli import main as _config_main
    sys.exit(_config_main(sys.argv[2:]))

# When run as a script (`python tr_sync.py`) this module's __name__ is
# "__main__". Submodules such as `brokers.tr.sync_io` do `import tr_sync` to
# read shared constants. Without aliasing, that import would re-execute this
# file as a separate `tr_sync` module — and since we're still in the middle
# of executing it, the circular partial import would fail. Aliasing makes
# both names resolve to the same in-progress module object.
if __name__ == "__main__":
    sys.modules["tr_sync"] = sys.modules["__main__"]

import argparse
import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pytr.account import login

# Default German→Spanish subtitle translation dictionary (the TR API always
# responds in German and we only translate them for user display).
# Can be extended/overridden via config.yaml > subtitle_translations.
_DEFAULT_SUBTITLE_TRANSLATIONS = {
    "Bardividende": "Dividendo en efectivo",
    "Aktienprämiendividende": "Dividendo en acciones (premium)",
    "Kapitalertrag": "Rendimiento de capital",
    "Zinszahlung": "Pago de cupón",
    "Zinsen": "Intereses",
    "Endgültige Fälligkeit": "Vencimiento final",
    "Kupon": "Cupón",
    "Verkaufsorder": "Orden de venta",
    "Kauforder": "Orden de compra",
    "Sparplan ausgeführt": "Plan de ahorro ejecutado",
    "Saveback": "Saveback",
    "Angenommen": "Aceptado",
    "Eingelöst": "Canjeado",
    "Wechsel": "Canje / cambio",
    "Ausgeführt": "Ejecutado",
    "ETF-Geschenk": "Regalo de ETF",
    "Verlosung": "Sorteo",
}


def _es(label):
    """Translate a German subtitle/title to the user's language. If no translation exists, return the original."""
    if not label:
        return label
    return SUBTITLE_TRANSLATIONS.get(label.strip(), label)

# ── Config loading and validation ─────────────────────────────────────────

class ConfigError(Exception):
    """Configuration error with an actionable message for the user."""


def _validate_config(cfg, source_path, *, allow_placeholders=False):
    """Check that the config has the critical fields. Raises ConfigError if not.

    If `allow_placeholders=True` (config.example.yaml fallback case in CI /
    tests), `sheet_id` is accepted with its placeholder value without error
    — the real user will see the "config.yaml not found" warning on stderr,
    but tests don't break.
    """
    errors = []

    sheet_id = cfg.get("sheet_id")
    is_placeholder = sheet_id and (sheet_id.startswith("REEMPLAZA") or sheet_id.startswith("REPLACE_"))
    if not sheet_id:
        errors.append("• `sheet_id` is missing. Set the ID of your Google Sheet.")
    elif is_placeholder and not allow_placeholders:
        errors.append("• `sheet_id` still has the placeholder value. Set the ID of your Google Sheet.")

    sheets = cfg.get("sheets") or {}
    for required in ("expenses", "income", "investments_year_format", "portfolio", "status", "sync_state"):
        if not sheets.get(required):
            errors.append(f"• `sheets.{required}` missing or empty.")

    if "{year}" not in (sheets.get("investments_year_format") or ""):
        errors.append("• `sheets.investments_year_format` must contain '{year}' (e.g. 'Dinero invertido {year}').")

    pcm = cfg.get("portfolio_cell_map")
    if not isinstance(pcm, list) or not pcm:
        errors.append("• `portfolio_cell_map` is missing or empty. Add at least one { isin, label }.")
    else:
        for i, entry in enumerate(pcm):
            if not isinstance(entry, dict) or "isin" not in entry or "label" not in entry:
                errors.append(f"• `portfolio_cell_map[{i}]` must be {{ isin: ..., label: ... }}.")

    pvr = cfg.get("portfolio_value_range")
    if not pvr or ":" not in str(pvr):
        errors.append("• `portfolio_value_range` is missing or doesn't look like an A1 range (e.g. 'C2:C8').")

    if errors:
        raise ConfigError(
            f"Invalid config at {source_path}:\n  " + "\n  ".join(errors) +
            "\n\n  See CONFIG.md for the full reference of each field."
        )


def _load_config():
    """Load config.yaml; if missing, try config.example.yaml with a warning.

    Validate the critical fields on load and abort with a clear message if
    something is missing.
    """
    import yaml
    here = Path(__file__).resolve().parent
    real = here / "config.yaml"
    example = here / "config.example.yaml"
    using_example = False
    if real.exists():
        path = real
    elif example.exists():
        sys.stderr.write(
            f"⚠  Could not find {real}. Using {example} (template values).\n"
            f"   Copy config.example.yaml to config.yaml and fill in your own values.\n"
        )
        path = example
        using_example = True
    else:
        raise FileNotFoundError(
            f"config.yaml missing. Copy config.example.yaml → config.yaml and fill it in."
        )
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    _validate_config(cfg, path, allow_placeholders=using_example)
    return cfg


CONFIG = _load_config()

SHEET_ID = CONFIG["sheet_id"]
TIMEZONE = ZoneInfo(CONFIG.get("timezone", "Europe/Madrid"))
PYTR_OUTPUT_PATH = Path(__file__).resolve().parent / ".pytr_data"
CACHE_PATH = Path(__file__).resolve().parent / ".broker_cache.pickle"

_SHEETS = CONFIG["sheets"]
EXPENSES_SHEET = _SHEETS["expenses"]
INCOME_SHEET = _SHEETS["income"]
PORTFOLIO_SHEET = _SHEETS["portfolio"]
STATUS_SHEET = _SHEETS["status"]
SYNC_STATE_SHEET = _SHEETS["sync_state"]
SNAPSHOTS_SHEET = _SHEETS.get("snapshots", "_snapshots")
SNAPSHOTS_POSITIONS_SHEET = _SHEETS.get("snapshots_positions", "_snapshots_positions")# Fiscal year of the investments tab (env var > config > current year)
INVESTMENTS_SHEET_YEAR = int(
    os.environ.get("TR_SYNC_INVESTMENTS_YEAR")
    or _SHEETS.get("investments_year")
    or datetime.now(tz=TIMEZONE).year
)
INVESTMENTS_SHEET = _SHEETS["investments_year_format"].format(year=INVESTMENTS_SHEET_YEAR)

PORTFOLIO_VALUE_RANGE = CONFIG["portfolio_value_range"]
PORTFOLIO_CELL_MAP = [(c["isin"], c["label"]) for c in CONFIG["portfolio_cell_map"]]

# Supported layouts for Expenses/Income:
#   - "monthly_columns": months as column pairs (Concept+Amount). Default.
#   - "ledger": one row per event with Date/Concept/Amount columns.
LAYOUT_DEFAULT = "monthly_columns"
SUPPORTED_LAYOUTS = {"monthly_columns", "ledger"}
LEDGER_HEADERS = list(_SHEETS.get("ledger_headers") or ["Fecha", "Concepto", "Importe"])

# A1 columns where each field is written in the `ledger` layout. Defaults: A/B/C.
_DEFAULT_LEDGER_COLUMNS = {"date": "A", "concept": "B", "amount": "C"}
LEDGER_COLUMNS = {**_DEFAULT_LEDGER_COLUMNS, **(_SHEETS.get("ledger_columns") or {})}
for _k, _v in LEDGER_COLUMNS.items():
    if not re.fullmatch(r"[A-Z]+", str(_v)):
        raise ValueError(
            f"config.yaml > sheets.ledger_columns.{_k}='{_v}' must be an A1 column letter (A, B, ..., AA, ...)."
        )

# Month-header patterns in `monthly_columns`. {month} is replaced with the
# month name (from MONTH_NAMES_ES) and {year} with the year.
MONTH_HEADER_AMOUNT = _SHEETS.get("month_header_amount", "{month} {year}")
MONTH_HEADER_CONCEPT = _SHEETS.get("month_header_concept", "Concepto {month}")

EXPENSES_LAYOUT = _SHEETS.get("expenses_layout", LAYOUT_DEFAULT)
INCOME_LAYOUT = _SHEETS.get("income_layout", LAYOUT_DEFAULT)
for _name, _layout in [("expenses_layout", EXPENSES_LAYOUT), ("income_layout", INCOME_LAYOUT)]:
    if _layout not in SUPPORTED_LAYOUTS:
        raise ValueError(
            f"config.yaml > sheets.{_name}='{_layout}' not supported. "
            f"Valid values: {sorted(SUPPORTED_LAYOUTS)}."
        )

ASSET_NAME_MAP = CONFIG.get("asset_name_map", {})
STATUS_LABELS = CONFIG.get("status_labels", {"portfolio": "Portfolio", "sync": "Sync completo"})

DEFAULT_BUFFER_DAYS = int(CONFIG.get("default_buffer_days", 7))

# Threshold (% of positions value) above which a position is flagged as
# "high concentration" in the insights block. None = no global threshold;
# only ISINs with an entry in concentration_limits trigger alerts.
_threshold_raw = CONFIG.get("concentration_threshold", 0.35)
CONCENTRATION_THRESHOLD = float(_threshold_raw) if _threshold_raw is not None else None

# Per-ISIN individual limits (override of the global threshold). Each entry
# is {ISIN: float} with a value between 0 and 1. ISINs without an entry fall
# back to the threshold. Lets you set reasonable per-asset tolerances
# (high for SP500, low for crypto).
CONCENTRATION_LIMITS = {
    str(isin): float(limit)
    for isin, limit in (CONFIG.get("concentration_limits") or {}).items()
}

# ISIN of the benchmark to compare your MWR against (e.g. an SP500 ETF). None
# = benchmark block disabled in `make insights`.
BENCHMARK_ISIN = (CONFIG.get("benchmark_isin") or "").strip() or None
BENCHMARK_LABEL = CONFIG.get("benchmark_label") or BENCHMARK_ISIN or "Benchmark"

# ISIN → denomination currency mapping. Used by the currency exposure block
# in `make insights`. ISINs without an entry → "UNKNOWN".
ASSET_CURRENCIES = {
    str(isin): str(currency).upper()
    for isin, currency in (CONFIG.get("asset_currencies") or {}).items()
}

# Declared monthly expense (€/m). Manual override for the "Cash runway"
# block in `make insights`. Use when most spending does not flow through
# the broker (so the WITHDRAWALs average understates real expenses).
# None / 0 → fall back to a 6-month average of broker WITHDRAWALs.
_monthly_expenses_raw = CONFIG.get("monthly_expenses_eur")
MONTHLY_EXPENSES_EUR = float(_monthly_expenses_raw) if _monthly_expenses_raw else None

# Declared monthly income (€/m). Manual override for the "Savings
# efficiency" block. None / 0 → fall back to a 6-month gross average
# of broker DEPOSITs (only meaningful if the user routes income through
# the broker; otherwise set this field explicitly).
_monthly_income_raw = CONFIG.get("monthly_income_eur")
MONTHLY_INCOME_EUR = float(_monthly_income_raw) if _monthly_income_raw else None

# Target cash range held at the broker (€), used by the "Cash target"
# block to flag structural surplus (cash > max_eur) or shortfall
# (cash < min_eur). Each bound accepts:
#   - a scalar (constant value over time)
#   - a dict {ISO_date: €} for a stepped schedule, resolved to the entry
#     whose date is <= today (useful to raise the ceiling every N months
#     without editing config by hand).
# Either bound may be null/omitted; if both are missing the block is
# skipped. Temporal resolution happens in cli/insights.py via
# core.utils.resolve_dated_schedule.
_cash_targets = CONFIG.get("cash_targets") or {}
CASH_TARGET_MIN_SPEC = _cash_targets.get("min_eur")
CASH_TARGET_MAX_SPEC = _cash_targets.get("max_eur")

# TR event types (not in config — they come from the API and don't vary per user)
EXPENSE_EVENT_TYPES = {
    "CARD_TRANSACTION",
    "PAYMENT_BIZUM_C2C_OUTGOING",
    "BANK_TRANSACTION_OUTGOING",
}

INCOME_EVENT_TYPES = {
    "PAYMENT_BIZUM_C2C_INCOMING",
    "BANK_TRANSACTION_INCOMING",
    "INTEREST_PAYOUT",
}

INVESTMENT_EVENT_TYPES = {
    "TRADING_SAVINGSPLAN_EXECUTED",
    "SAVEBACK_AGGREGATE",
    "TRADING_TRADE_EXECUTED",
}

EXCLUDED_STATUSES = {"CANCELED", "CANCELLED", "FAILED", "REJECTED", "PENDING"}

# Label used in the Investments tab for the Saveback row (SAVEBACK_AGGREGATE
# events). Configurable.
SAVEBACK_LABEL = CONFIG.get("saveback_label", "SAVEBACK")

# German → user-language translation dictionary. The default is merged with
# whatever the user provides in config.yaml > subtitle_translations.
SUBTITLE_TRANSLATIONS = {
    **_DEFAULT_SUBTITLE_TRANSLATIONS,
    **(CONFIG.get("subtitle_translations") or {}),
}

# Headers `init-sheet` uses when creating empty tabs. Configurable so users
# in other languages can have "Asset", "Value (€)", etc.
_DEFAULT_INIT_HEADERS = {
    "investments_asset_column": "Activo",
    "portfolio_asset_column": "Activo",
    "portfolio_value_column": "Valor (€)",
}
INIT_HEADERS = {**_DEFAULT_INIT_HEADERS, **(CONFIG.get("init_sheet_headers") or {})}

# Patterns of events to ignore (e.g. the salary you already add by hand).
# Loaded from config.yaml → ignore_events. Case-insensitive substring match
# against both `title` and `subtitle`.
def _build_ignore_patterns(section):
    cfg = (CONFIG.get("ignore_events") or {}).get(section) or {}
    return {
        "title_contains": [s.lower() for s in (cfg.get("title_contains") or [])],
        "subtitle_contains": [s.lower() for s in (cfg.get("subtitle_contains") or [])],
    }


# Per-tab configuration: event filter + summary block markers
SHEET_CONFIGS = {
    EXPENSES_SHEET: {
        "event_types": EXPENSE_EVENT_TYPES,
        "expected_sign": -1,
        "summary_markers": {m.lower() for m in CONFIG.get("summary_markers", {}).get("expenses", [])},
        "ignore": _build_ignore_patterns("expenses"),
        "layout": EXPENSES_LAYOUT,
    },
    INCOME_SHEET: {
        "event_types": INCOME_EVENT_TYPES,
        "expected_sign": 1,
        "summary_markers": {m.lower() for m in CONFIG.get("summary_markers", {}).get("income", [])},
        "ignore": _build_ignore_patterns("income"),
        "layout": INCOME_LAYOUT,
    },
}

_DEFAULT_MONTH_NAMES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
MONTH_NAMES_ES = list(CONFIG.get("month_names") or _DEFAULT_MONTH_NAMES)
if len(MONTH_NAMES_ES) != 12:
    raise ValueError(
        f"config.yaml > month_names must have exactly 12 elements, "
        f"got {len(MONTH_NAMES_ES)}."
    )

# ── Feature toggles (which parts of the sync run) ─────────────────────────
# `FEATURES` is the dict the internal functions already read for the classic
# toggles (expenses/income/investments/portfolio). Kept for compat and
# extended with the rest of the registry's features.
from brokers.tr import CAPABILITIES as BROKER_CAPABILITIES
from core.features import (
    FEATURE_REGISTRY,
    feature_status as _feature_status,
    is_feature_enabled as _is_feature_enabled_core,
)

_FEATURES_DEFAULT = {name: f.default_enabled for name, f in FEATURE_REGISTRY.items()}
FEATURES = {**_FEATURES_DEFAULT, **(CONFIG.get("features") or {})}


def is_feature_enabled(feature_name: str) -> bool:
    """Is the feature active? Combines the user's config and the broker's capabilities.

    Wrapper around `core.features.is_feature_enabled` that already has the
    active broker (TR) data preconfigured.
    """
    return _is_feature_enabled_core(feature_name, BROKER_CAPABILITIES, FEATURES)


def list_features():
    """Print a table of features with their status (config + broker support)."""
    rows = _feature_status(BROKER_CAPABILITIES, FEATURES)
    print(f"\n{'Feature':<22} {'Config':<8} {'Support':<9} {'Effective':<10} Description")
    print(f"{'-'*22} {'-'*8} {'-'*9} {'-'*10} {'-'*55}")
    for r in rows:
        cfg = "✓" if r["enabled_in_config"] else "✗"
        sup = "✓" if r["supported"] else "✗"
        eff = "✓ ON" if r["effective"] else "✗ off"
        print(f"{r['name']:<22} {cfg:<8} {sup:<9} {eff:<10} {r['description']}")
    print()
    print(f"Active broker: TR ({len(BROKER_CAPABILITIES)} capabilities)")
    print(f"Capabilities: {', '.join(sorted(BROKER_CAPABILITIES))}")
    print()
    print("To toggle features without editing the YAML, run:")
    print("  make config-features                 # interactive checkbox")
    print("  python tr_sync.py config set features.<name> false")
    print()

# ── Renta: report sections to generate ────────────────────────────────────
_RENTA_SECTIONS_DEFAULT = {
    "fifo": True,
    "dividends": True,
    "interest": True,
    "bonds": True,
    "summary_by_box": True,
    "retentions": True,
    "saveback": True,
    "crypto": True,
    "modelo720": True,
}
RENTA_SECTIONS = {**_RENTA_SECTIONS_DEFAULT, **(CONFIG.get("renta") or {})}

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tr_sync")
logging.getLogger("pytr").setLevel(logging.WARNING)


# TR fetching + raw-event normalization live in `brokers/tr/sync_io`.
# Re-exported so callers (cli/*, brokers/tr/adapter.py, tests) can keep
# referencing them as `tr_sync.X`.
from brokers.tr.sync_io import (  # noqa: E402
    _matches_ignore,
    _patch_compact_portfolio_with_sec_acc_no,
    fetch_tr_events,
    fetch_tr_portfolio,
    fetch_tr_portfolio_and_cash,
    filter_events_by_flow,
    normalize_event,
)


# ── Google Sheets ─────────────────────────────────────────────────────────
# The Sheet primitives (open, status, sync_state) live in
# `storage/sheets/*`. Here we only keep thin wrappers that apply the
# constants/config of this module.

from storage.sheets.client import open_spreadsheet as _open_spreadsheet
from storage.sheets.status_store import StatusStore
from storage.sheets.sync_state_store import SyncStateStore


def open_spreadsheet():
    return _open_spreadsheet(SHEET_ID)


def _status_store(spreadsheet) -> StatusStore:
    return StatusStore(spreadsheet, sheet_name=STATUS_SHEET, labels=STATUS_LABELS, tz=TIMEZONE)


def _sync_state_store(spreadsheet) -> SyncStateStore:
    return SyncStateStore(spreadsheet, sheet_name=SYNC_STATE_SHEET)


def write_status(spreadsheet, key: str):
    """Update the STATUS_SHEET tab with the current timestamp for `key`."""
    _status_store(spreadsheet).write(key)


def get_or_create_sync_state(spreadsheet):
    return _sync_state_store(spreadsheet)._get_or_create_ws()


def load_synced_ids(spreadsheet):
    return _sync_state_store(spreadsheet).load()


def append_synced_ids(spreadsheet, new_ids):
    _sync_state_store(spreadsheet).append(new_ids)


# ── Historical snapshots ──────────────────────────────────────────────────
# Persistence delegated to `storage.sheets.snapshot_store.SheetsSnapshotStore`.
# Pure logic (schema, row conversion, snapshot_value_at) lives in
# `core.snapshot_store`. tr_sync.py only orchestrates.

from core.cache import (
    invalidate_cache as _invalidate_tr_cache,
    load_cached_session as _load_cached_tr,
    save_cached_session as _save_cached_tr,
)
from core.snapshot_store import snapshot_value_at  # re-export for callers
from storage.sheets.snapshot_store import SheetsSnapshotStore


def _ensure_tr_session(
    *,
    refresh: bool = False,
    benchmark_isins: tuple[str, ...] = (),
):
    """Return (snapshot, txs, benchmarks), using the TR cache if fresh.

    `refresh=True` invalidates the cache and forces a clean fetch.
    `benchmark_isins`: ISINs to download price history for. If the cache
    doesn't contain all of them, a refetch is triggered.

    If the cache is fresh and contains the requested benchmarks, this
    avoids `login()` and the download entirely.

    `benchmarks` is `{ISIN: price_history_list}` for the requested ISINs only.
    """
    from brokers.tr.adapter import fetch_price_history, fetch_snapshot, fetch_transactions

    if not refresh:
        cached = _load_cached_tr(CACHE_PATH)
        if cached:
            snapshot, txs, cached_benchmarks = cached
            # If all requested benchmarks are in the cache, use the cache.
            # If any is missing, refetch to include it.
            if all(isin in cached_benchmarks for isin in benchmark_isins):
                # Return only the requested ones (don't surface old, unrequested benchmarks).
                benchmarks = {isin: cached_benchmarks[isin] for isin in benchmark_isins}
                return snapshot, txs, benchmarks

    log.info("Connecting to Trade Republic...")
    tr = login()
    log.info("Downloading snapshot and transactions...")

    async def _gather():
        snap = await fetch_snapshot(tr, tz=TIMEZONE)
        txs_local = await fetch_transactions(tr, tz=TIMEZONE, gift_overrides=GIFT_COST_OVERRIDES)
        bench = {}
        for isin in benchmark_isins:
            log.info(f"   downloading benchmark history {isin}...")
            history = await fetch_price_history(tr, isin, range_str="max")
            if history:
                bench[isin] = history
                log.info(f"      {len(history)} bars")
            else:
                log.warning(f"      ⚠ could not download history for {isin}")
        return snap, txs_local, bench

    snapshot, txs, benchmarks = asyncio.run(_gather())
    _save_cached_tr(CACHE_PATH, snapshot, txs, benchmarks=benchmarks)
    return snapshot, txs, benchmarks


def _make_snapshot_store(spreadsheet) -> SheetsSnapshotStore:
    return SheetsSnapshotStore(
        spreadsheet,
        agg_sheet=SNAPSHOTS_SHEET,
        positions_sheet=SNAPSHOTS_POSITIONS_SHEET,
    )






# ── Orchestration ─────────────────────────────────────────────────────────

# Helpers moved to core/utils.py — re-exported here with `_` prefix for
# compatibility with existing code (tests, external calls).
from core.utils import (
    column_letter_to_index as _column_letter_to_index,
    parse_a1_column_range as _parse_a1_column_range,
)

# Sheet writers for Gastos / Ingresos live in `storage.sheets.expenses_income`.
# Re-exported here because cli/sync_cmd.py and tests reference them as `tr_sync.X`.
from storage.sheets.expenses_income import (  # noqa: E402
    sync_to_sheet,
    _sync_ledger_layout,
)

# Investment aggregation + writer live in `core.investments`.
# Re-exported here because cli/sync_cmd.py and tests reference them as `tr_sync.X`.
from core.investments import (  # noqa: E402
    aggregate_investments,
    sync_investments,
)


# CLI command implementations have been moved to cli/* (see cli/__init__.py).
# main() imports them lazily so we avoid an import cycle (cli/* import from tr_sync).


# ── IRPF report (FIFO) — moved to reports/renta_es.py ─────────────────────
# The Spanish IRPF report logic lives in `reports/renta_es.py` (~600 lines
# extracted to keep `tr_sync.py` focused on sync/orchestration). Here we
# only keep the config-derived constants (DIVIDEND_SUBTITLES, etc.) that
# `reports/renta_es.py` references via `tr_sync.X`, plus compat shims for
# the existing tests.

# Manual override for gifts whose details TR does not return parseable.
# Configured via config.yaml → gift_cost_overrides.
GIFT_COST_OVERRIDES: dict = CONFIG.get("gift_cost_overrides") or {}

# German subtitles TR uses to classify SSP_CORPORATE_ACTION_CASH events.
# If TR introduces a new subtitle, the user can extend it via config without
# touching code.
_DEFAULT_DIVIDEND_SUBTITLES = {"Bardividende", "Aktienprämiendividende", "Kapitalertrag"}
_DEFAULT_BOND_CASH_SUBTITLES = {"Zinszahlung", "Kupon"}
_DEFAULT_BOND_MATURITY_SUBTITLES = {"Endgültige Fälligkeit"}

_RENTA_CFG = CONFIG.get("renta_classification") or {}
DIVIDEND_SUBTITLES = set(_RENTA_CFG.get("dividend_subtitles") or _DEFAULT_DIVIDEND_SUBTITLES)
BOND_CASH_SUBTITLES = set(_RENTA_CFG.get("bond_cash_subtitles") or _DEFAULT_BOND_CASH_SUBTITLES)
BOND_MATURITY_SUBTITLES = set(_RENTA_CFG.get("bond_maturity_subtitles") or _DEFAULT_BOND_MATURITY_SUBTITLES)
BOND_SUBTITLES = BOND_CASH_SUBTITLES | BOND_MATURITY_SUBTITLES
CRYPTO_ISINS = set(CONFIG.get("crypto_isins", []))


# ── Backward-compat shims for tests ───────────────────────────────────────
# Existing tests import these symbols from `tr_sync`. We re-export them
# from their new locations. The import is lazy (inside functions) to avoid
# the `tr_sync ↔ reports.renta_es` cycle.

from core.utils import parse_de_number as _parse_de_number
from core.fifo import apply_fifo as _apply_fifo
from brokers.tr.parser import (
    extract_isin_from_icon as _extract_isin_from_icon,
    extract_trade_details as _extract_trade_details,
    extract_gift_details as _extract_gift_details,
    extract_dividend_details as _extract_dividend_details,
)


def _build_lots_and_sales(events, target_year):
    """Compat shim — the logic lives in `reports.renta_es._build_lots_and_sales`."""
    from reports.renta_es import _build_lots_and_sales as _real
    return _real(events, target_year)


def _collect_dividends(events, year):
    from reports.renta_es import _collect_dividends as _real
    return _real(events, year)


def _collect_interest(events, year):
    from reports.renta_es import _collect_interest as _real
    return _real(events, year)


def _collect_bond_income(events, year):
    from reports.renta_es import _collect_bond_income as _real
    return _real(events, year)


def _collect_saveback(events, year):
    from reports.renta_es import _collect_saveback as _real
    return _real(events, year)


def _retentions_by_country(dividends):
    from reports.renta_es import _retentions_by_country as _real
    return _real(dividends)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Don't write to the Sheet")
    p.add_argument("--since", type=str, help="ISO date (e.g. 2026-04-19) to filter from")
    p.add_argument("--init", action="store_true",
                   help="Mark every current event as already synced without writing")
    p.add_argument("--portfolio", action="store_true",
                   help="Just the portfolio snapshot (current value per asset). With --dry-run, prints ISINs without writing")
    p.add_argument("--renta", action="store_true",
                   help="IRPF report of capital gains/losses (FIFO). Use --year to set the year.")
    p.add_argument("--year", type=int, default=None,
                   help="Fiscal year for --renta (default: current year - 1)")
    p.add_argument("--init-sheet", action="store_true",
                   help="Create the missing tabs in your Google Sheet with the minimum schema.")
    p.add_argument("--doctor", action="store_true",
                   help="Verify the setup is ready: config, OAuth, Sheet, tabs, pytr session.")
    p.add_argument("--insights", action="store_true",
                   help="Print net worth, contributions and return (simple + MWR) to the console. Does not touch the Sheet.")
    p.add_argument("--verbose", action="store_true",
                   help="With --insights, add the PER POSITION block for diagnosis.")
    p.add_argument("--debug-isin", type=str, metavar="ISIN",
                   help="List every transaction the adapter extracts for a specific ISIN. Useful to reconcile with Excel.")
    p.add_argument("--mwr-flows", action="store_true",
                   help="Dumps the flows used by `mwr()` to stdout as TSV. Paste into Sheets and apply =XIRR as a sanity check.")
    p.add_argument("--bonus-as", type=str, default="income", choices=["income", "deposit"],
                   help="Saveback handling mode in --mwr-flows (default income).")
    p.add_argument("--locale", type=str, default="us", choices=["us", "es"],
                   help="Decimal format in --mwr-flows: 'us' (1234.56, default) or 'es' (1234,56).")
    p.add_argument("--backfill-snapshots", action="store_true",
                   help="Reconstruct historical snapshots via TR aggregateHistory and write them to `_snapshots`.")
    p.add_argument("--start", type=str, default=None, metavar="YYYY-MM-DD",
                   help="With --backfill-snapshots: start date (default: today − 365d).")
    p.add_argument("--frequency", type=str, default="weekly", choices=["weekly", "biweekly", "monthly"],
                   help="With --backfill-snapshots: cadence (default: weekly).")
    p.add_argument("--features", action="store_true",
                   help="Print the features table with their status (config + broker support).")
    p.add_argument("--refresh", action="store_true",
                   help="Invalidate the TR cache and force a clean fetch (useful when TR has emitted new events).")
    args = p.parse_args()
    since = None
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=TIMEZONE)
    try:
        if args.doctor:
            from cli.doctor import doctor
            sys.exit(doctor())
        elif args.features:
            list_features()
        elif args.init_sheet:
            from cli.init_sheet import init_sheet
            init_sheet(dry_run=args.dry_run)
        elif args.debug_isin:
            from cli.debug import debug_isin
            debug_isin(args.debug_isin, refresh=args.refresh)
        elif args.mwr_flows:
            from cli.mwr_flows import dump_mwr_flows
            dump_mwr_flows(bonus_as=args.bonus_as, refresh=args.refresh, locale=args.locale)
        elif args.backfill_snapshots:
            from cli.backfill import backfill_snapshots
            backfill_snapshots(start_iso=args.start, frequency=args.frequency, refresh=args.refresh)
        elif args.insights:
            from cli.insights import sync_insights
            sync_insights(verbose=args.verbose, refresh=args.refresh)
        elif args.renta:
            from reports.renta_es import sync_renta as _sync_renta
            year = args.year or (datetime.now(tz=TIMEZONE).year - 1)
            _sync_renta(year, dry_run=args.dry_run)
        elif args.portfolio:
            from cli.portfolio import sync_portfolio
            sync_portfolio(dry_run=args.dry_run)
        else:
            from cli.sync_cmd import sync
            sync(dry_run=args.dry_run, since=since, init_mode=args.init)
    except KeyboardInterrupt:
        log.info("\nInterrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
