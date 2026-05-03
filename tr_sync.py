#!/usr/bin/env python3
"""Sincroniza gastos e ingresos de Trade Republic con la Sheet."""

import sys

# Shortcircuit para el subcomando `config`: delega al config_cli ANTES de
# cargar imports pesados (pytr, gspread) o el propio config.yaml. Esto permite
# que `python tr_sync.py config init` funcione antes incluso de tener Sheet.
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

# Diccionario por defecto de traducciones alemán→castellano (la API de TR
# responde siempre en alemán y solo traducimos para mostrar al usuario).
# Se puede ampliar/sobrescribir desde config.yaml > subtitle_translations.
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
    """Traduce un subtitle/título alemán al idioma del usuario. Si no hay traducción, devuelve el original."""
    if not label:
        return label
    return SUBTITLE_TRANSLATIONS.get(label.strip(), label)

# ── Carga y validación de configuración ───────────────────────────────────

class ConfigError(Exception):
    """Error de configuración con mensaje accionable para el usuario."""


def _validate_config(cfg, source_path, *, allow_placeholders=False):
    """Comprueba que el config tiene los campos críticos. Lanza ConfigError si no.

    Si `allow_placeholders=True` (caso fallback de config.example.yaml en CI / tests),
    se acepta `sheet_id` con su valor placeholder sin error — el usuario real verá el
    warning de "no se encuentra config.yaml" en stderr, pero los tests no rompen.
    """
    errors = []

    sheet_id = cfg.get("sheet_id")
    is_placeholder = sheet_id and (sheet_id.startswith("REEMPLAZA") or sheet_id.startswith("REPLACE_"))
    if not sheet_id:
        errors.append("• `sheet_id` falta. Pon el ID de tu Google Sheet.")
    elif is_placeholder and not allow_placeholders:
        errors.append("• `sheet_id` sigue con el placeholder. Pon el ID de tu Google Sheet.")

    sheets = cfg.get("sheets") or {}
    for required in ("expenses", "income", "investments_year_format", "portfolio", "status", "sync_state"):
        if not sheets.get(required):
            errors.append(f"• `sheets.{required}` falta o está vacío.")

    if "{year}" not in (sheets.get("investments_year_format") or ""):
        errors.append("• `sheets.investments_year_format` debe contener '{year}' (ej. 'Dinero invertido {year}').")

    pcm = cfg.get("portfolio_cell_map")
    if not isinstance(pcm, list) or not pcm:
        errors.append("• `portfolio_cell_map` falta o está vacía. Añade al menos un { isin, label }.")
    else:
        for i, entry in enumerate(pcm):
            if not isinstance(entry, dict) or "isin" not in entry or "label" not in entry:
                errors.append(f"• `portfolio_cell_map[{i}]` debe ser {{ isin: ..., label: ... }}.")

    pvr = cfg.get("portfolio_value_range")
    if not pvr or ":" not in str(pvr):
        errors.append("• `portfolio_value_range` falta o no parece un rango A1 (ej. 'C2:C8').")

    if errors:
        raise ConfigError(
            f"Config inválida en {source_path}:\n  " + "\n  ".join(errors) +
            "\n\n  Mira CONFIG.md para la referencia completa de cada campo."
        )


def _load_config():
    """Carga config.yaml; si no existe, intenta config.example.yaml con warning.

    Valida los campos críticos al cargar y aborta con un mensaje claro si algo falta.
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
            f"⚠  No se encuentra {real}. Usando {example} (valores de plantilla).\n"
            f"   Copia config.example.yaml a config.yaml y rellénalo con los tuyos.\n"
        )
        path = example
        using_example = True
    else:
        raise FileNotFoundError(
            f"Falta config.yaml. Copia config.example.yaml → config.yaml y rellénalo."
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
SNAPSHOTS_POSITIONS_SHEET = _SHEETS.get("snapshots_positions", "_snapshots_positions")

# Año fiscal de la pestaña inversiones (env var > config > año actual)
INVESTMENTS_SHEET_YEAR = int(
    os.environ.get("TR_SYNC_INVESTMENTS_YEAR")
    or _SHEETS.get("investments_year")
    or datetime.now(tz=TIMEZONE).year
)
INVESTMENTS_SHEET = _SHEETS["investments_year_format"].format(year=INVESTMENTS_SHEET_YEAR)

PORTFOLIO_VALUE_RANGE = CONFIG["portfolio_value_range"]
PORTFOLIO_CELL_MAP = [(c["isin"], c["label"]) for c in CONFIG["portfolio_cell_map"]]

# Layouts soportados para Gastos/Ingresos:
#   - "monthly_columns": meses como pares de columnas (Concepto+Importe). Default.
#   - "ledger": una fila por evento con columnas Fecha/Concepto/Importe.
LAYOUT_DEFAULT = "monthly_columns"
SUPPORTED_LAYOUTS = {"monthly_columns", "ledger"}
LEDGER_HEADERS = list(_SHEETS.get("ledger_headers") or ["Fecha", "Concepto", "Importe"])

# Columnas A1 donde escribir cada campo en el layout `ledger`. Defaults: A/B/C.
_DEFAULT_LEDGER_COLUMNS = {"date": "A", "concept": "B", "amount": "C"}
LEDGER_COLUMNS = {**_DEFAULT_LEDGER_COLUMNS, **(_SHEETS.get("ledger_columns") or {})}
for _k, _v in LEDGER_COLUMNS.items():
    if not re.fullmatch(r"[A-Z]+", str(_v)):
        raise ValueError(
            f"config.yaml > sheets.ledger_columns.{_k}='{_v}' debe ser una letra de columna A1 (A, B, ..., AA, ...)."
        )

# Patrones de los headers de mes en `monthly_columns`. {month} se sustituye por
# el nombre del mes (de MONTH_NAMES_ES) y {year} por el año.
MONTH_HEADER_AMOUNT = _SHEETS.get("month_header_amount", "{month} {year}")
MONTH_HEADER_CONCEPT = _SHEETS.get("month_header_concept", "Concepto {month}")

EXPENSES_LAYOUT = _SHEETS.get("expenses_layout", LAYOUT_DEFAULT)
INCOME_LAYOUT = _SHEETS.get("income_layout", LAYOUT_DEFAULT)
for _name, _layout in [("expenses_layout", EXPENSES_LAYOUT), ("income_layout", INCOME_LAYOUT)]:
    if _layout not in SUPPORTED_LAYOUTS:
        raise ValueError(
            f"config.yaml > sheets.{_name}='{_layout}' no soportado. "
            f"Valores válidos: {sorted(SUPPORTED_LAYOUTS)}."
        )

ASSET_NAME_MAP = CONFIG.get("asset_name_map", {})
STATUS_LABELS = CONFIG.get("status_labels", {"portfolio": "Portfolio", "sync": "Sync completo"})

DEFAULT_BUFFER_DAYS = int(CONFIG.get("default_buffer_days", 7))

# Threshold (% del valor de posiciones) por encima del cual una posición se
# marca como "alta concentración" en el bloque de insights. None = sin
# threshold global; solo alertan los ISINs con entrada en concentration_limits.
_threshold_raw = CONFIG.get("concentration_threshold", 0.35)
CONCENTRATION_THRESHOLD = float(_threshold_raw) if _threshold_raw is not None else None

# Límites individuales por ISIN (override del threshold global). Cada entrada
# es {ISIN: float} con valor entre 0 y 1. ISINs sin entrada caen al threshold.
# Permite definir tolerancias razonables por activo (SP500 alto, cripto bajo).
CONCENTRATION_LIMITS = {
    str(isin): float(limit)
    for isin, limit in (CONFIG.get("concentration_limits") or {}).items()
}

# ISIN del benchmark contra el que comparar tu MWR (p.ej. SP500 ETF). None =
# bloque de benchmark deshabilitado en `make insights`.
BENCHMARK_ISIN = (CONFIG.get("benchmark_isin") or "").strip() or None
BENCHMARK_LABEL = CONFIG.get("benchmark_label") or BENCHMARK_ISIN or "Benchmark"

# Mapeo ISIN → divisa de denominación. Usado por el bloque de exposición por
# divisa en `make insights`. ISINs sin entrada → "UNKNOWN".
ASSET_CURRENCIES = {
    str(isin): str(currency).upper()
    for isin, currency in (CONFIG.get("asset_currencies") or {}).items()
}

# Tipos de evento de TR (no van a config porque son de la API y no varían por usuario)
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

# Label que se usa en la pestaña Inversiones para la fila de Saveback (eventos
# SAVEBACK_AGGREGATE). Configurable.
SAVEBACK_LABEL = CONFIG.get("saveback_label", "SAVEBACK")

# Diccionario de traducciones alemán → idioma del usuario. Se mergea el default
# con lo que el usuario haya puesto en config.yaml > subtitle_translations.
SUBTITLE_TRANSLATIONS = {
    **_DEFAULT_SUBTITLE_TRANSLATIONS,
    **(CONFIG.get("subtitle_translations") or {}),
}

# Headers que `init-sheet` usa al crear pestañas vacías. Configurables para
# que los usuarios en otros idiomas puedan tener "Asset", "Value (€)", etc.
_DEFAULT_INIT_HEADERS = {
    "investments_asset_column": "Activo",
    "portfolio_asset_column": "Activo",
    "portfolio_value_column": "Valor (€)",
}
INIT_HEADERS = {**_DEFAULT_INIT_HEADERS, **(CONFIG.get("init_sheet_headers") or {})}

# Patrones de eventos a ignorar (ej. la nómina que ya añades a mano).
# Se cargan de config.yaml → ignore_events. Match case-insensitive y por
# substring tanto sobre `title` como sobre `subtitle`.
def _build_ignore_patterns(section):
    cfg = (CONFIG.get("ignore_events") or {}).get(section) or {}
    return {
        "title_contains": [s.lower() for s in (cfg.get("title_contains") or [])],
        "subtitle_contains": [s.lower() for s in (cfg.get("subtitle_contains") or [])],
    }


# Configuración por pestaña: filtro de eventos + marcadores del bloque resumen
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
        f"config.yaml > month_names debe tener exactamente 12 elementos, "
        f"recibidos {len(MONTH_NAMES_ES)}."
    )

# ── Feature toggles (qué partes del sync se ejecutan) ─────────────────────
# `FEATURES` es el dict que ya leían las funciones internas para los toggles
# clásicos (expenses/income/investments/portfolio). Lo mantenemos por compat
# y lo extendemos con el resto de features del registro.
from brokers.tr import CAPABILITIES as BROKER_CAPABILITIES
from core.features import (
    FEATURE_REGISTRY,
    feature_status as _feature_status,
    is_feature_enabled as _is_feature_enabled_core,
)

_FEATURES_DEFAULT = {name: f.default_enabled for name, f in FEATURE_REGISTRY.items()}
FEATURES = {**_FEATURES_DEFAULT, **(CONFIG.get("features") or {})}


def is_feature_enabled(feature_name: str) -> bool:
    """¿Está activa la feature? Combina config del usuario y capabilities del broker.

    Wrapper sobre `core.features.is_feature_enabled` que ya tiene los datos del
    broker activo (TR) preconfigurados.
    """
    return _is_feature_enabled_core(feature_name, BROKER_CAPABILITIES, FEATURES)


def list_features():
    """Imprime tabla de features con su estado (config + soporte broker)."""
    rows = _feature_status(BROKER_CAPABILITIES, FEATURES)
    print(f"\n{'Feature':<22} {'Config':<8} {'Soporte':<9} {'Efectiva':<10} Descripción")
    print(f"{'-'*22} {'-'*8} {'-'*9} {'-'*10} {'-'*55}")
    for r in rows:
        cfg = "✓" if r["enabled_in_config"] else "✗"
        sup = "✓" if r["supported"] else "✗"
        eff = "✓ ON" if r["effective"] else "✗ off"
        print(f"{r['name']:<22} {cfg:<8} {sup:<9} {eff:<10} {r['description']}")
    print()
    print(f"Broker activo: TR ({len(BROKER_CAPABILITIES)} capabilities)")
    print(f"Capabilities: {', '.join(sorted(BROKER_CAPABILITIES))}")
    print()
    print("Para activar/desactivar features, edita config.yaml > features:")
    print("  features:")
    print("    insights: false   # ej. apaga el comando insights")
    print()

# ── Renta: secciones del informe a generar ────────────────────────────────
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
# Las primitivas de Sheet (open, status, sync_state) viven en
# `storage/sheets/*`. Aquí solo dejamos thin wrappers que aplican los
# constantes/config de este módulo.

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
    """Actualiza la pestaña STATUS_SHEET con el timestamp actual para `key`."""
    _status_store(spreadsheet).write(key)


def get_or_create_sync_state(spreadsheet):
    return _sync_state_store(spreadsheet)._get_or_create_ws()


def load_synced_ids(spreadsheet):
    return _sync_state_store(spreadsheet).load()


def append_synced_ids(spreadsheet, new_ids):
    _sync_state_store(spreadsheet).append(new_ids)


# ── Snapshots históricos ──────────────────────────────────────────────────
# Persistencia delegada a `storage.sheets.snapshot_store.SheetsSnapshotStore`.
# La lógica pura (esquema, conversión a filas, snapshot_value_at) vive en
# `core.snapshot_store`. tr_sync.py solo orquesta.

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
    """Devuelve (snapshot, txs, benchmarks), usando el cache de TR si está fresco.

    `refresh=True` invalida el cache y fuerza un fetch limpio.
    `benchmark_isins`: ISINs para los que descargar histórico de precios. Si
    el cache no los contiene todos, se hace refetch.

    Si el cache está fresco y contiene los benchmarks pedidos, evita por
    completo el `login()` y la descarga.

    `benchmarks` es `{ISIN: price_history_list}` solo para los ISINs solicitados.
    """
    from brokers.tr.adapter import fetch_price_history, fetch_snapshot, fetch_transactions

    if not refresh:
        cached = _load_cached_tr(CACHE_PATH)
        if cached:
            snapshot, txs, cached_benchmarks = cached
            # Si todos los benchmarks pedidos están en cache, usamos cache.
            # Si falta alguno, hacemos refetch para incluirlo.
            if all(isin in cached_benchmarks for isin in benchmark_isins):
                # Filtramos solo los pedidos (no devolvemos benchmarks viejos no solicitados).
                benchmarks = {isin: cached_benchmarks[isin] for isin in benchmark_isins}
                return snapshot, txs, benchmarks

    log.info("Conectando a Trade Republic...")
    tr = login()
    log.info("Descargando snapshot y transacciones...")

    async def _gather():
        snap = await fetch_snapshot(tr, tz=TIMEZONE)
        txs_local = await fetch_transactions(tr, tz=TIMEZONE, gift_overrides=GIFT_COST_OVERRIDES)
        bench = {}
        for isin in benchmark_isins:
            log.info(f"   descargando histórico benchmark {isin}...")
            history = await fetch_price_history(tr, isin, range_str="max")
            if history:
                bench[isin] = history
                log.info(f"      {len(history)} barras")
            else:
                log.warning(f"      ⚠ no se pudo descargar histórico para {isin}")
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






# ── Orquestación ──────────────────────────────────────────────────────────

# Helpers movidos a core/utils.py — re-exportados aquí con prefix `_` por
# compatibilidad con código existente (tests, llamadas externas).
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


# ── Informe IRPF (FIFO) — movido a reports/renta_es.py ────────────────────
# La lógica del informe IRPF español vive en `reports/renta_es.py` (~600 líneas
# extraídas para mantener `tr_sync.py` enfocado en sync/orquestación). Aquí
# solo dejamos las constantes config-derivadas (DIVIDEND_SUBTITLES, etc.) que
# `reports/renta_es.py` referencia vía `tr_sync.X`, más shims de compat para
# los tests existentes.

# Override manual para regalos cuyos detalles TR no traiga parseables.
# Se configura en config.yaml → gift_cost_overrides.
GIFT_COST_OVERRIDES: dict = CONFIG.get("gift_cost_overrides") or {}

# Subtitles alemanes con los que TR clasifica eventos SSP_CORPORATE_ACTION_CASH.
# Si TR introduce un subtitle nuevo, el usuario puede ampliarlo via config sin tocar código.
_DEFAULT_DIVIDEND_SUBTITLES = {"Bardividende", "Aktienprämiendividende", "Kapitalertrag"}
_DEFAULT_BOND_CASH_SUBTITLES = {"Zinszahlung", "Kupon"}
_DEFAULT_BOND_MATURITY_SUBTITLES = {"Endgültige Fälligkeit"}

_RENTA_CFG = CONFIG.get("renta_classification") or {}
DIVIDEND_SUBTITLES = set(_RENTA_CFG.get("dividend_subtitles") or _DEFAULT_DIVIDEND_SUBTITLES)
BOND_CASH_SUBTITLES = set(_RENTA_CFG.get("bond_cash_subtitles") or _DEFAULT_BOND_CASH_SUBTITLES)
BOND_MATURITY_SUBTITLES = set(_RENTA_CFG.get("bond_maturity_subtitles") or _DEFAULT_BOND_MATURITY_SUBTITLES)
BOND_SUBTITLES = BOND_CASH_SUBTITLES | BOND_MATURITY_SUBTITLES
CRYPTO_ISINS = set(CONFIG.get("crypto_isins", []))


# ── Backward-compat shims para tests ──────────────────────────────────────
# Los tests existentes importan estos símbolos desde `tr_sync`. Re-exportamos
# desde sus nuevas ubicaciones. La importación es lazy (dentro de funciones)
# para evitar el ciclo `tr_sync ↔ reports.renta_es`.

from core.utils import parse_de_number as _parse_de_number
from core.fifo import apply_fifo as _apply_fifo
from brokers.tr.parser import (
    extract_isin_from_icon as _extract_isin_from_icon,
    extract_trade_details as _extract_trade_details,
    extract_gift_details as _extract_gift_details,
    extract_dividend_details as _extract_dividend_details,
)


def _build_lots_and_sales(events, target_year):
    """Compat shim — la lógica vive en `reports.renta_es._build_lots_and_sales`."""
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
    p.add_argument("--dry-run", action="store_true", help="No escribe en la Sheet")
    p.add_argument("--since", type=str, help="Fecha ISO (e.g. 2026-04-19) para filtrar")
    p.add_argument("--init", action="store_true",
                   help="Marca todos los eventos actuales como ya sincronizados sin escribir")
    p.add_argument("--portfolio", action="store_true",
                   help="Solo snapshot del portfolio (valor actual por activo). Con --dry-run, imprime ISINs sin escribir")
    p.add_argument("--renta", action="store_true",
                   help="Informe IRPF de ganancias/pérdidas patrimoniales (FIFO). Usa --year para fijar año.")
    p.add_argument("--year", type=int, default=None,
                   help="Año fiscal para --renta (default: año actual - 1)")
    p.add_argument("--init-sheet", action="store_true",
                   help="Crea las pestañas que faltan en tu Google Sheet con la estructura mínima.")
    p.add_argument("--doctor", action="store_true",
                   help="Verifica que el setup está listo: config, OAuth, Sheet, pestañas, sesión pytr.")
    p.add_argument("--insights", action="store_true",
                   help="Imprime patrimonio, aportaciones y rentabilidad (simple + MWR) en consola. No toca la Sheet.")
    p.add_argument("--verbose", action="store_true",
                   help="Con --insights, añade el bloque POR POSICIÓN para diagnóstico.")
    p.add_argument("--debug-isin", type=str, metavar="ISIN",
                   help="Lista todas las transacciones que el adapter saca para un ISIN concreto. Útil para reconciliar con Excel.")
    p.add_argument("--mwr-flows", action="store_true",
                   help="Vuelca los flujos que `mwr()` usa al stdout en TSV. Pega en Sheets y aplica =XIRR para sanity check.")
    p.add_argument("--bonus-as", type=str, default="income", choices=["income", "deposit"],
                   help="Modo de tratamiento del saveback en --mwr-flows (default income).")
    p.add_argument("--locale", type=str, default="us", choices=["us", "es"],
                   help="Formato decimal en --mwr-flows: 'us' (1234.56, default) o 'es' (1234,56).")
    p.add_argument("--backfill-snapshots", action="store_true",
                   help="Reconstruye snapshots históricos vía TR aggregateHistory y los escribe en `_snapshots`.")
    p.add_argument("--start", type=str, default=None, metavar="YYYY-MM-DD",
                   help="Con --backfill-snapshots: fecha de inicio (default: today − 365d).")
    p.add_argument("--frequency", type=str, default="weekly", choices=["weekly", "biweekly", "monthly"],
                   help="Con --backfill-snapshots: cadencia (default: weekly).")
    p.add_argument("--features", action="store_true",
                   help="Imprime tabla de features con su estado (config + soporte broker).")
    p.add_argument("--refresh", action="store_true",
                   help="Invalida el cache de TR y fuerza un fetch limpio (útil si TR ha emitido nuevos eventos).")
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
        log.info("\nInterrumpido.")
        sys.exit(130)


if __name__ == "__main__":
    main()
