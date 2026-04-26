#!/usr/bin/env python3
"""Sincroniza gastos e ingresos de Trade Republic con la Sheet."""

import sys

# Shortcircuit para el subcomando `config`: delega al config_cli ANTES de
# cargar imports pesados (pytr, gspread) o el propio config.yaml. Esto permite
# que `python tr_sync.py config init` funcione antes incluso de tener Sheet.
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "config":
    from config_cli import main as _config_main
    sys.exit(_config_main(sys.argv[2:]))

import argparse
import asyncio
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
from pytr.account import login
from pytr.portfolio import Portfolio
from pytr.timeline import Timeline

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


# ── Trade Republic ────────────────────────────────────────────────────────

def _patch_compact_portfolio_with_sec_acc_no(tr):
    """Workaround para pytr-org/pytr#246: compactPortfolio devuelve [] sin secAccNo."""
    settings = tr.settings()
    sec_acc_no = settings.get("securitiesAccountNumber")
    if not sec_acc_no:
        for k in ("accountNumber", "secAccNo"):
            if settings.get(k):
                sec_acc_no = settings[k]
                break
    if not sec_acc_no:
        raise RuntimeError(f"No encuentro securitiesAccountNumber en settings(): keys={list(settings.keys())}")

    async def compact_portfolio_patched():
        return await tr.subscribe({"type": "compactPortfolio", "secAccNo": sec_acc_no})

    tr.compact_portfolio = compact_portfolio_patched


async def fetch_tr_portfolio(tr):
    """Devuelve la lista de posiciones (instrumentos) actuales del usuario en TR.

    Cada posición es un dict con al menos {instrumentId, netValue, ...}. No incluye cash.
    Para obtener instrumentos + cash en una sola llamada, usa `fetch_tr_portfolio_and_cash`.
    """
    _patch_compact_portfolio_with_sec_acc_no(tr)
    p = Portfolio(tr, include_watchlist=False, lang="es", output=None)
    await p.portfolio_loop()
    return p.portfolio


async def fetch_tr_events(tr, not_before_ts: float = 0.0):
    """Descarga eventos brutos de la timeline de TR a partir de `not_before_ts` (epoch).

    Devuelve la lista de eventos en bruto tal como los emite la API de TR (idioma:
    el que tenga configurado el `_locale` del cliente, por defecto alemán).
    """
    collected = []

    def on_event(event):
        collected.append(event)

    PYTR_OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
    timeline = Timeline(
        tr,
        output_path=PYTR_OUTPUT_PATH,
        not_before=not_before_ts,
        store_event_database=False,
        event_callback=on_event,
    )
    await timeline.tl_loop()
    return collected


def normalize_event(raw):
    """Normaliza un evento bruto de TR a un dict con campos útiles para escribir en la Sheet.

    Devuelve None si el evento no tiene amount o timestamp utilizables.

    Campos del dict resultante:
      - id           : id único del evento (para deduplicar)
      - ts           : datetime con zona TIMEZONE
      - month_key    : (año, mes) — para agrupar
      - concepto     : title del evento (lo que se escribe en la Sheet)
      - importe      : abs(value) redondeado a 2 decimales
      - type         : eventType de TR
      - raw_value    : value original (con signo) para clasificar gasto/ingreso
    """
    amount_block = raw.get("amount") or {}
    value = amount_block.get("value")
    if value is None:
        return None
    ts_str = raw.get("timestamp")
    if not ts_str:
        return None
    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(TIMEZONE)
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
    """True si el evento debe ignorarse según patrones configurados."""
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
    """Devuelve {sheet_name: [normalized_events]} según los filtros."""
    out = {name: [] for name in SHEET_CONFIGS}
    ignored = {name: [] for name in SHEET_CONFIGS}
    for raw in events:
        if raw.get("status") in EXCLUDED_STATUSES:
            continue
        et = raw.get("eventType")
        for name, cfg in SHEET_CONFIGS.items():
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
            log.info(f"  [{name}] {len(items)} evento(s) ignorado(s) por config.yaml → ignore_events:")
            for n in items:
                log.info(f"     - {n['ts'].date()}  {n['importe']:>8.2f} €  '{n['concepto']}'")
    return out


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


def find_month_columns(worksheet, year, month):
    """Localiza el par (col_concepto, col_importe) buscando el header del importe en fila 1.

    Asume que la columna de concepto está a la izquierda de la del importe.
    """
    headers = worksheet.row_values(1)
    expected = MONTH_HEADER_AMOUNT.format(month=MONTH_NAMES_ES[month-1], year=year).lower()
    for i, h in enumerate(headers, start=1):
        if h.strip().lower() == expected:
            if i > 1:
                return (i - 1, i)
    return (None, None)


def create_month_columns(worksheet, year, month):
    """Crea dos columnas nuevas (concepto + importe) al final de la fila 1 con los patrones configurados."""
    headers = worksheet.row_values(1)
    next_col = len(headers) + 1
    concepto_header = MONTH_HEADER_CONCEPT.format(month=MONTH_NAMES_ES[month-1], year=year)
    importe_header = MONTH_HEADER_AMOUNT.format(month=MONTH_NAMES_ES[month-1], year=year)
    worksheet.update_cell(1, next_col, concepto_header)
    worksheet.update_cell(1, next_col + 1, importe_header)
    log.info(f"   [{worksheet.title}] columnas creadas: '{concepto_header}' | '{importe_header}'")
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


def aggregate_investments(events):
    """Devuelve {(asset_label, year, month): total_amount}."""
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
                    log.warning(f"   activo desconocido en TR: '{title}' → fila nueva con ese nombre")
                    seen_unknown.add(title)
                asset = title
        value = (raw.get("amount") or {}).get("value")
        if value is None:
            continue
        # TRADING_TRADE_EXECUTED incluye compras y ventas; solo sumamos compras (importe negativo).
        if et == "TRADING_TRADE_EXECUTED" and value >= 0:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(TIMEZONE)
        totals[(asset, ts.year, ts.month)] += abs(value)
    return totals


def sync_investments(spreadsheet, events, dry_run):
    """Recalcula totales por (activo, mes) y sobrescribe las celdas.
    Solo escribe en el año `INVESTMENTS_SHEET_YEAR`, mes actual o futuro."""
    now = datetime.now(tz=TIMEZONE)
    totals = aggregate_investments(events)
    totals = {
        (a, y, m): v for (a, y, m), v in totals.items()
        if y == INVESTMENTS_SHEET_YEAR
        and (y > now.year or (y == now.year and m >= now.month))
    }
    if not totals:
        log.info("\n[Inversiones] nada que actualizar (mes actual o futuros)")
        return

    log.info(f"\n[Inversiones] {len(totals)} celdas a actualizar:")
    for (asset, year, month), v in sorted(totals.items()):
        log.info(f"   {asset:<14} / {MONTH_NAMES_ES[month-1]} {year}: {v:>8.2f}")

    if dry_run:
        return

    try:
        worksheet = spreadsheet.worksheet(INVESTMENTS_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        log.warning(f"   pestaña '{INVESTMENTS_SHEET}' no encontrada — salto inversiones")
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
            log.info(f"   nueva fila '{asset}' en {new_row}")

    for (year, month) in sorted({(y, m) for (_, y, m) in totals.keys()}):
        header = f"{MONTH_NAMES_ES[month-1]} {year}".lower()
        if header not in month_to_col:
            new_col = max(month_to_col.values(), default=1) + 1
            month_str = f"{MONTH_NAMES_ES[month-1]} {year}"
            worksheet.update_cell(1, new_col, month_str)
            month_to_col[header] = new_col
            log.info(f"   nueva columna '{month_str}' en {new_col}")

    cells = []
    for (asset, year, month), value in totals.items():
        header = f"{MONTH_NAMES_ES[month-1]} {year}".lower()
        cells.append(gspread.cell.Cell(asset_to_row[asset], month_to_col[header], round(value, 2)))
    worksheet.update_cells(cells, value_input_option="USER_ENTERED")
    log.info(f"   {len(cells)} celdas escritas")


def sync_to_sheet(spreadsheet, sheet_name, txs, dry_run):
    """Escribe `txs` en la pestaña `sheet_name` según el layout configurado.

    Layouts:
      - 'monthly_columns' (default): meses como pares de columnas Concepto+Importe.
        Detecta el bloque resumen al final del mes e inserta las nuevas filas justo encima.
      - 'ledger': cada evento es una fila con columnas Fecha/Concepto/Importe.
        Append simple a la primera fila libre. Sin lógica de bloque resumen.
    """
    if not txs:
        return []
    cfg = SHEET_CONFIGS[sheet_name]
    layout = cfg.get("layout", LAYOUT_DEFAULT)
    if layout == "ledger":
        return _sync_ledger_layout(spreadsheet, sheet_name, txs, dry_run)
    return _sync_monthly_columns_layout(spreadsheet, sheet_name, txs, dry_run, cfg)


def _sync_monthly_columns_layout(spreadsheet, sheet_name, txs, dry_run, cfg):
    """Layout original: meses como pares de columnas (Concepto+Importe), bloque resumen al final."""
    worksheet = spreadsheet.worksheet(sheet_name)

    by_month: dict[tuple[int, int], list] = {}
    for e in txs:
        by_month.setdefault(e["month_key"], []).append(e)

    written_ids = []
    for (year, month), month_txs in sorted(by_month.items()):
        month_txs.sort(key=lambda e: e["ts"])
        log.info(f"\n[{sheet_name} / {MONTH_NAMES_ES[month-1].capitalize()} {year}] {len(month_txs)} movimientos:")
        for tx in month_txs:
            log.info(f"   {tx['ts'].date()}  {tx['importe']:>8.2f}  {tx['concepto']}")

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
    """Layout 'ledger': una fila por evento con columnas configurables (date/concept/amount)."""
    worksheet = spreadsheet.worksheet(sheet_name)
    txs_sorted = sorted(txs, key=lambda e: e["ts"])

    log.info(f"\n[{sheet_name} / ledger] {len(txs_sorted)} movimientos:")
    for tx in txs_sorted:
        log.info(f"   {tx['ts'].date()}  {tx['importe']:>8.2f}  {tx['concepto']}")

    if dry_run:
        return []

    date_col = _column_letter_to_index(LEDGER_COLUMNS["date"])
    concept_col = _column_letter_to_index(LEDGER_COLUMNS["concept"])
    amount_col = _column_letter_to_index(LEDGER_COLUMNS["amount"])

    # Asegura los 3 headers en fila 1 (uno por columna configurada)
    existing_row1 = worksheet.row_values(1)
    headers_to_set = [
        (date_col, LEDGER_HEADERS[0]),
        (concept_col, LEDGER_HEADERS[1]),
        (amount_col, LEDGER_HEADERS[2]),
    ]
    for col_idx, header in headers_to_set:
        current = existing_row1[col_idx - 1] if col_idx - 1 < len(existing_row1) else ""
        if current.strip().lower() != header.lower():
            worksheet.update_cell(1, col_idx, header)

    # Primera fila vacía: miramos la columna 'date'
    col_date_values = worksheet.col_values(date_col)
    first_empty = max(len(col_date_values) + 1, 2)

    # Escribe celda a celda (admite columnas no contiguas y en cualquier orden)
    cells = []
    for offset, tx in enumerate(txs_sorted):
        row = first_empty + offset
        cells.append(gspread.cell.Cell(row, date_col, tx["ts"].date().isoformat()))
        cells.append(gspread.cell.Cell(row, concept_col, tx["concepto"]))
        cells.append(gspread.cell.Cell(row, amount_col, tx["importe"]))
    worksheet.update_cells(cells, value_input_option="USER_ENTERED")
    return [t["id"] for t in txs_sorted]


# ── Orquestación ──────────────────────────────────────────────────────────

# Helpers movidos a core/utils.py — re-exportados aquí con prefix `_` por
# compatibilidad con código existente (tests, llamadas externas).
from core.utils import (
    column_letter_to_index as _column_letter_to_index,
    parse_a1_column_range as _parse_a1_column_range,
)


def doctor():
    """Health check: verifica que el setup está listo para sincronizar.

    Comprueba (en orden, sin tocar TR para no disparar prompts de SMS):
      1. config.yaml cargado y validado al import (si llegamos aquí, OK).
      2. portfolio_cell_map ↔ portfolio_value_range coherentes.
      3. Sesión local de pytr presente (~/.pytr/).
      4. Google Sheet accesible (OAuth gspread + sheet_id válido).
      5. Pestañas requeridas presentes (según features activos).

    Devuelve 0 si todo OK, 1 si hay errores críticos.
    """
    log.info("🔍 tr-sync doctor — chequeo de setup\n")
    errors = []
    warnings = []

    # 1. Config (ya validado al cargar el módulo; aquí solo confirmamos)
    log.info(f"✓ config.yaml cargado y validado (sheet_id={SHEET_ID[:10]}...)")
    log.info(f"  features activos: {', '.join(k for k, v in FEATURES.items() if v)}")

    # 2. portfolio_cell_map vs portfolio_value_range
    col, row_start, row_end = _parse_a1_column_range(PORTFOLIO_VALUE_RANGE)
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

    # 3. Sesión pytr
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

    # 5. Pestañas
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

    # ── Resumen ──
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


def init_sheet(dry_run: bool = False):
    """Crea las pestañas que faltan en la Google Sheet con la estructura mínima.

    Idempotente: si la pestaña ya existe se respeta tal cual y solo se loggea.
    Pestañas que crea (si están habilitadas en `features`):
      - Gastos / Ingresos / Dinero invertido <año>: vacías; el sync creará los
        headers de mes la primera vez que escriba.
      - Calculo ganancias: con los labels de `portfolio_cell_map` en la columna
        a la izquierda del `portfolio_value_range`, para que veas qué activo
        corresponde a cada celda.
      - Estado sync y _sync_state se crean automáticamente más adelante
        (status en el primer write_status, sync_state en el primer load_synced_ids).
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
            col, row_start, row_end = _parse_a1_column_range(PORTFOLIO_VALUE_RANGE)
            if col is None:
                log.warning(f"     portfolio_value_range='{PORTFOLIO_VALUE_RANGE}' no parseable; "
                            f"no se prerellena la columna de labels.")
            else:
                col_idx = _column_letter_to_index(col)
                # Cabecera fila row_start - 1 (si hay sitio)
                if row_start >= 2:
                    ws.update_cell(row_start - 1, max(col_idx - 1, 1), INIT_HEADERS["portfolio_asset_column"])
                    ws.update_cell(row_start - 1, col_idx, INIT_HEADERS["portfolio_value_column"])
                # Labels en la columna a la izquierda del rango
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
                date_col = _column_letter_to_index(LEDGER_COLUMNS["date"])
                concept_col = _column_letter_to_index(LEDGER_COLUMNS["concept"])
                amount_col = _column_letter_to_index(LEDGER_COLUMNS["amount"])
                ws.update_cell(1, date_col, LEDGER_HEADERS[0])
                ws.update_cell(1, concept_col, LEDGER_HEADERS[1])
                ws.update_cell(1, amount_col, LEDGER_HEADERS[2])
                cols_repr = f"{LEDGER_COLUMNS['date']}/{LEDGER_COLUMNS['concept']}/{LEDGER_COLUMNS['amount']}"
                log.info(f"     headers ledger en columnas {cols_repr} de fila 1.")

    log.info(f"\n✅ init-sheet completado. {created} pestaña(s) creada(s).")
    if not dry_run:
        log.info(f"   Pestañas Estado sync y _sync_state se crearán automáticamente al primer sync.")


def dump_mwr_flows(
    *,
    bonus_as: str = "income",
    refresh: bool = False,
    locale: str = "us",
):
    """Imprime los flujos de caja que `mwr()` usa para calcular el MWR all-time.

    Output en formato TSV (date<TAB>amount<TAB>kind<TAB>title) listo para pegar en
    Google Sheets o Excel y aplicar `XIRR/TIR.NO.PER` como sanity check.

    Convención de signos (idéntica a la de mwr()):
      - BUY: amount negativo (salida del bolsillo).
      - SELL: amount positivo (entrada al bolsillo).
      - DIVIDEND: amount positivo (cobro recibido).
      - Snapshot final: amount positivo igual a positions_value_eur.

    `locale="us"` (default): números con `.` decimal. Funciona en Excel y en
    Sheets con configuración United States.
    `locale="es"`: números con `,` decimal. Para Sheets en español sin tener
    que reformatear cada celda.

    `bonus_as="income"` (default): saveback excluido como aportación. Usa
    `bonus_as="deposit"` para incluirlo.
    """
    from core.types import TxKind

    snapshot, txs, _ = _ensure_tr_session(refresh=refresh)

    flows: list[tuple[datetime, float, str, str]] = []
    for tx in txs:
        if tx.kind == TxKind.BUY:
            if tx.is_bonus and bonus_as == "income":
                continue
            flows.append((tx.ts, -abs(tx.amount_eur), "BUY", tx.title))
        elif tx.kind == TxKind.SELL:
            flows.append((tx.ts, abs(tx.amount_eur), "SELL", tx.title))
        elif tx.kind == TxKind.DIVIDEND:
            flows.append((tx.ts, abs(tx.amount_eur), "DIVIDEND", tx.title))
    flows.sort(key=lambda x: x[0])
    flows.append((snapshot.ts, snapshot.positions_value_eur, "FINAL", "Posiciones actuales"))

    def fmt_amount(x: float) -> str:
        s = f"{x:.2f}"
        return s.replace(".", ",") if locale == "es" else s

    formula_es = "=TIR.NO.PER(B2:B{n}; A2:A{n})".format(n=len(flows) + 1)
    formula_us = "=XIRR(B2:B{n}, A2:A{n})".format(n=len(flows) + 1)

    print(f"# MWR cash flows (bonus_as={bonus_as}, locale={locale})")
    if locale == "es":
        print(f"# Pega en Sheets ES y aplica:  {formula_es}")
    else:
        print(f"# Pega en Sheets/Excel y aplica:  {formula_us}")
    print(f"# {len(flows)-1} flujos + 1 valor final = {len(flows)} filas.")
    print()
    print("date\tamount\tkind\ttitle")
    for ts, amount, kind, title in flows:
        print(f"{ts.date().isoformat()}\t{fmt_amount(amount)}\t{kind}\t{title}")


def debug_isin(isin: str, *, refresh: bool = False):
    """Imprime todas las transacciones que el adapter saca para un ISIN.

    Útil para reconciliar con tu Excel: ves cada BUY/SELL/DIVIDEND con su
    fecha, importe y shares, y puedes contrastar qué es lo que TR realmente
    emite. Si tu Excel tiene un número que no aparece aquí, ese número no
    está en TR — viene de otra fuente (manual, bonus en cash, etc.).
    """
    from collections import Counter

    _, txs, _ = _ensure_tr_session(refresh=refresh)
    matches = [t for t in txs if t.isin == isin]
    log.info(f"   {len(matches)} transacciones para {isin}.\n")

    if not matches:
        log.warning("No se encontraron transacciones para ese ISIN. Verifica que sea correcto.")
        return

    # Resumen por kind
    by_kind = Counter(t.kind.value for t in matches)
    print(f"Resumen por kind:")
    for k, n in sorted(by_kind.items()):
        total = sum(t.amount_eur for t in matches if t.kind.value == k)
        print(f"  {k:<12} count={n:>4}   suma={total:>12,.2f} €")
    print()

    # Detalle ordenado
    print(f"{'fecha':<12} {'kind':<10} {'shares':>10} {'amount':>12} {'bonus':>6}  título")
    print(f"{'-'*12} {'-'*10} {'-'*10} {'-'*12} {'-'*6}  {'-'*40}")
    for t in sorted(matches, key=lambda x: x.ts):
        ts = t.ts.strftime("%Y-%m-%d")
        sh = f"{t.shares:.6f}" if t.shares is not None else "n/a"
        bonus = "yes" if t.is_bonus else "no"
        print(f"{ts:<12} {t.kind.value:<10} {sh:>10} {t.amount_eur:>+12,.2f} {bonus:>6}  {t.title[:50]}")
    print()

    # Cost basis (FIFO, saveback a 0)
    from core.metrics import cost_basis_of_current_holdings
    cb = cost_basis_of_current_holdings(matches, bonus_at_zero_cost=True)
    cb_full = cost_basis_of_current_holdings(matches, bonus_at_zero_cost=False)
    print("Cost basis tras FIFO:")
    print(f"  saveback a 0€:           {cb.get(isin, 0.0):>12,.2f} €")
    print(f"  saveback a precio merc.: {cb_full.get(isin, 0.0):>12,.2f} €")


def backfill_snapshots(start_iso: str | None = None, frequency: str = "weekly", *, refresh: bool = False):
    if not is_feature_enabled("backfill_snapshots"):
        log.info("Feature 'backfill_snapshots' deshabilitada (config o broker).")
        return
    """Reconstruye snapshots históricos y los persiste en `_snapshots` (+ posiciones).

    Para cada fecha histórica D entre `start` y hoy (a la cadencia indicada):
      - Calcula shares de cada ISIN actual en D (retrocediendo desde el snapshot
        actual aplicando la inversa de cada BUY/SELL posterior a D).
      - Calcula cash en D (retrocediendo flujos `from_cash=True`).
      - Consulta a TR el precio histórico de cada ISIN para D.
      - Compone el PortfolioSnapshot reconstruido y lo escribe en las pestañas.

    Caveats: ver core/backfill.py. Saveback shares sin info quedan ligeramente
    sobreestimadas (error <1%); ISINs con price history no disponible se
    excluyen del positions_value (ej. cripto a veces falla por exchange).

    Args:
      start_iso: fecha ISO (YYYY-MM-DD) desde la que reconstruir. Default: today − 365 días.
      frequency: "weekly" | "monthly" | "biweekly". Default "weekly".
    """
    from brokers.tr.adapter import fetch_price_history_with_fallback, fetch_snapshot, fetch_transactions, price_at
    from core.backfill import reconstruct_snapshot_at

    tz = TIMEZONE
    # Normalizamos a mediodía para que re-ejecutar backfill produzca exactamente
    # los mismos timestamps (y el dedup por ts funcione). Si lo dejáramos como
    # `datetime.now()`, dos backfills en el mismo día generarían filas duplicadas.
    today_noon = datetime.now(tz=tz).replace(hour=12, minute=0, second=0, microsecond=0)
    if start_iso:
        start = datetime.fromisoformat(start_iso).replace(tzinfo=tz, hour=12, minute=0, second=0, microsecond=0)
    else:
        start = today_noon - timedelta(days=365)

    if frequency == "weekly":
        delta = timedelta(days=7)
    elif frequency == "biweekly":
        delta = timedelta(days=14)
    elif frequency == "monthly":
        delta = timedelta(days=30)
    else:
        raise ValueError(f"frequency desconocida: {frequency!r}. Usa weekly|biweekly|monthly.")

    dates = []
    d = start
    while d <= today_noon - delta:
        dates.append(d)
        d += delta
    if not dates:
        log.error(f"No hay fechas a reconstruir entre {start.date()} y {today_noon.date()} con cadencia {frequency}.")
        return

    # Determine the longest range we need given start date.
    days_back = (today_noon - start).days
    if days_back <= 30: range_str = "1m"
    elif days_back <= 90: range_str = "3m"
    elif days_back <= 365: range_str = "1y"
    elif days_back <= 365*5: range_str = "5y"
    else: range_str = "max"

    # Cache hit ahorra solo el fetch de snapshot/txs, no el de precios históricos
    # (esos requieren conexión activa). Aún así, ahorra ~10 segundos.
    cached = None if refresh else _load_cached_tr(CACHE_PATH)

    log.info("Conectando a Trade Republic...")
    tr = login()

    # IMPORTANTE: todas las llamadas async tienen que vivir en un único event loop
    # porque la conexión WebSocket se queda atada al primer loop.
    async def _gather():
        if cached:
            snap, txs_local, _bench = cached  # benchmarks irrelevantes para backfill
            log.info(f"   ⚡ snapshot+txs desde cache ({len(txs_local)} txs, {len(snap.positions)} posiciones)\n")
        else:
            log.info("Descargando snapshot actual y transacciones...")
            snap = await fetch_snapshot(tr, tz=tz)
            txs_local = await fetch_transactions(tr, tz=tz, gift_overrides=GIFT_COST_OVERRIDES)
            log.info(f"   {len(txs_local)} transacciones, {len(snap.positions)} posiciones.\n")

        log.info(f"Descargando histórico de precios (range={range_str}) para {len(snap.positions)} ISINs...")
        # Construye lista de exchanges a probar por ISIN.
        # - El que TR devuelve en compactPortfolio (si lo hay) → primero.
        # - Luego LSX (default para acciones/ETFs).
        # - Para cripto (CRYPTO_ISINS), añade BTLX y BSF como fallback.
        crypto_fallbacks = ["BTLX", "BSF"]
        prices: dict[str, list[dict]] = {}
        for i, p in enumerate(snap.positions):
            exchanges = []
            if p.exchange_id:
                exchanges.append(p.exchange_id)
            if "LSX" not in exchanges:
                exchanges.append("LSX")
            if p.isin in CRYPTO_ISINS:
                for ex in crypto_fallbacks:
                    if ex not in exchanges:
                        exchanges.append(ex)
            history, used = await fetch_price_history_with_fallback(
                tr, p.isin,
                range_str=range_str,
                exchanges=exchanges,
                debug=(i == 0),
            )
            prices[p.isin] = history
            status = f"{len(history)} barras (.{used})" if history else f"n/a (probé {','.join(exchanges)})"
            log.info(f"   {(p.title or p.isin)[:36]:<36}  {status}")
        return snap, txs_local, prices

    snapshot, txs, price_history = asyncio.run(_gather())
    if not cached:
        # No guardamos benchmarks aquí (no los hemos pedido). Si había alguno
        # en cache antiguo, se pierde — el siguiente `make insights` lo redownload.
        _save_cached_tr(CACHE_PATH, snapshot, txs)

    if not any(price_history.values()):
        log.error("\nNinguna posición devolvió histórico de precios. Backfill abortado.")
        log.error("Posibles causas: aggregateHistory no disponible para ningún ISIN, problema de exchange.")
        return

    log.info(f"\nReconstruyendo {len(dates)} snapshots ({frequency})...")
    records: list[tuple] = []
    for d in dates:
        prices = {}
        for isin, history in price_history.items():
            p = price_at(history, d)
            if p is not None:
                prices[isin] = p
        snap = reconstruct_snapshot_at(d, snapshot, txs, prices)
        if not snap.positions and snap.cash_eur == snapshot.cash_eur:
            log.info(f"   {d.date()}  sin actividad — skip")
            continue
        records.append((snap, None))
        log.info(
            f"   {d.date()}  cash={snap.cash_eur:>9.2f}  "
            f"pos={snap.positions_value_eur:>9.2f}  total={snap.total_eur:>9.2f}  "
            f"({len(snap.positions)} pos)"
        )

    log.info(f"\nEscribiendo {len(records)} snapshots a `{SNAPSHOTS_SHEET}` (en batch, dedup activado)...")
    spreadsheet = open_spreadsheet()
    store = _make_snapshot_store(spreadsheet)
    try:
        written = store.append_batch(records, skip_existing=True)
        skipped = len(records) - written
        log.info(f"OK: {written} nuevos snapshots escritos, {skipped} ya existían (dedup por ts).")
        if written > 0:
            log.info(f"   Próximo `make insights` ya tendrá MWR YTD/12m si hay snapshots anteriores al periodo.")
    except Exception as e:
        log.error(f"⚠ Falló la escritura batch: {e}")
        log.error(f"   Si es rate limit, espera 1-2 minutos y vuelve a ejecutar — el dedup omitirá los ya escritos.")


def sync_insights(verbose: bool = False, *, refresh: bool = False):
    if not is_feature_enabled("insights"):
        log.info("Feature 'insights' deshabilitada (config o broker).")
        return
    """Imprime insights de inversión en consola. No toca la Sheet.

    Bloques (siempre):
      1. PATRIMONIO ACTUAL: cash + posiciones.
      2. RENTABILIDAD — POSICIONES ACTUALES: dos lecturas del mismo cost
         basis: con y sin saveback descontado.
      3. RENTABILIDAD — HISTÓRICO COMPLETO: MWR all-time anualizado
         (incluye dividendos cobrados) en dos modos (saveback como income
         vs como aportación).
      4. APORTACIONES MENSUALES: este mes vs media de los últimos 12 meses.

    Bloque adicional con `verbose=True`:
      - POR POSICIÓN: tabla detallada por ISIN para diagnóstico/reconciliar
        con Excel.

    MWR YTD / 12m se omite: necesita snapshot histórico al inicio del periodo
    (siguiente iteración).
    """
    from core.metrics import (
        benchmark_return,
        concentration,
        contribution_vs_average,
        cost_basis_total as _cb_total,
        cost_basis_user_paid_per_isin,
        currency_exposure,
        monthly_contributions,
        mwr,
        per_position_attribution,
        simple_return,
        total_invested,
        unrealized_return,
        unrealized_return_user_paid,
    )

    benchmark_isins = (BENCHMARK_ISIN,) if BENCHMARK_ISIN else ()
    snapshot, txs, benchmarks = _ensure_tr_session(refresh=refresh, benchmark_isins=benchmark_isins)
    log.info(f"   {len(txs)} transacciones, {len(snapshot.positions)} posiciones.")

    # Persiste snapshot + carga histórico para MWR YTD/12m.
    snapshot_history: list[dict] = []
    try:
        spreadsheet = open_spreadsheet()
        store = _make_snapshot_store(spreadsheet)
        store.append(snapshot, _cb_total(snapshot))
        snapshot_history = store.load_history()
        log.info(f"   snapshot guardado. histórico: {len(snapshot_history)} entradas.\n")
    except Exception as e:
        log.warning(f"   ⚠ no se pudo persistir/cargar snapshots ({e}); MWR YTD/12m se omite.\n")

    now = datetime.now(tz=TIMEZONE)

    bar = "═" * 64

    def fmt_pct(x, *, anual=False, sign=True):
        if x is None:
            return "n/a"
        s = f"{x*100:+.2f}" if sign else f"{x*100:.2f}"
        return f"{s} %" + (" anual" if anual else "")

    def fmt_eur(x):
        s = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{s} €"

    crypto_value = sum(p.net_value_eur for p in snapshot.positions if p.isin in CRYPTO_ISINS)
    etf_value = snapshot.positions_value_eur - crypto_value

    print(bar)
    print("  PATRIMONIO ACTUAL")
    print(bar)
    if crypto_value > 0:
        print(f"  Cartera (ETFs/acciones): {fmt_eur(etf_value):>16}")
        print(f"  Cripto:                  {fmt_eur(crypto_value):>16}")
    else:
        print(f"  Posiciones: {fmt_eur(etf_value):>16}")
    print(f"  Cash:                    {fmt_eur(snapshot.cash_eur):>16}")
    print(f"  TOTAL:                   {fmt_eur(snapshot.total_eur):>16}")
    print()

    print(bar)
    print("  RENTABILIDAD — POSICIONES ACTUALES")
    print(bar)
    up = unrealized_return_user_paid(snapshot, txs)
    ur = unrealized_return(snapshot, txs=txs)
    if up and ur:
        print(f"  Cost basis sin saveback:   {fmt_eur(up['cost_basis']):>14}  ← lo que tú pusiste")
        print(f"  Cost basis con saveback:   {fmt_eur(ur['cost_basis']):>14}  ← averageBuyIn API bruto")
        print(f"  Valor actual:              {fmt_eur(up['value']):>14}")
        print(f"  Plusvalía sobre tu dinero: {fmt_eur(up['pnl_eur']):>14}  ({fmt_pct(up['pnl_pct'])})  ← matchea Excel y TR app")
        print(f"  Plusvalía sobre bruto:     {fmt_eur(ur['pnl_eur']):>14}  ({fmt_pct(ur['pnl_pct'])})  ← saveback incluido como coste")
        if ur["positions_with_cost"] < ur["positions_total"]:
            missing = ur["positions_total"] - ur["positions_with_cost"]
            print(f"  ⚠  {missing} posición(es) sin averageBuyIn; excluida(s).")
    elif ur:
        print(f"  Cost basis (con saveback): {fmt_eur(ur['cost_basis']):>14}")
        print(f"  Valor actual:              {fmt_eur(ur['value']):>14}")
        print(f"  Plusvalía latente:         {fmt_eur(ur['pnl_eur']):>14}  ({fmt_pct(ur['pnl_pct'])})")
    else:
        print("  (sin cost basis disponible — broker no devolvió averageBuyIn)")
    print()

    print(bar)
    print("  RENTABILIDAD — HISTÓRICO COMPLETO (incluye ventas y dividendos)")
    print(bar)

    ytd_start = datetime(now.year, 1, 1, tzinfo=TIMEZONE)
    twelvem_start = now - timedelta(days=365)
    ytd_value = snapshot_value_at(snapshot_history, ytd_start) if snapshot_history else None
    twelvem_value = snapshot_value_at(snapshot_history, twelvem_start) if snapshot_history else None

    for label, mode in (
        ("Mi dinero (saveback como income — default)", "income"),
        ("Incluyendo saveback como aportación", "deposit"),
    ):
        invested = total_invested(txs, bonus_as=mode)
        mwr_all = mwr(txs, snapshot, bonus_as=mode)
        mwr_ytd = mwr(txs, snapshot, bonus_as=mode, start=ytd_start, start_value=ytd_value) if ytd_value else None
        mwr_12m = mwr(txs, snapshot, bonus_as=mode, start=twelvem_start, start_value=twelvem_value) if twelvem_value else None
        print(f"  ── {label} ──")
        print(f"    Aportado neto (BUYs − SELLs):  {fmt_eur(invested):>16}")
        print(f"    MWR all-time:                  {fmt_pct(mwr_all, anual=True):>16}")
        print(f"    MWR YTD ({now.year}):                  {fmt_pct(mwr_ytd, anual=True):>16}")
        print(f"    MWR 12 meses:                  {fmt_pct(mwr_12m, anual=True):>16}")
        print()

    if not ytd_value and not twelvem_value:
        print(f"  ℹ MWR YTD / 12m saldrán n/a hasta que haya un snapshot anterior")
        print(f"    al inicio del periodo. Cada `make insights/sync/portfolio` añade uno.")
        print()

    # ── Benchmark vs MWR ──────────────────────────────────────────────
    if BENCHMARK_ISIN and benchmarks.get(BENCHMARK_ISIN):
        bench_history = benchmarks[BENCHMARK_ISIN]
        first_tx_ts = txs[0].ts if txs else None
        windows = []
        # All-time: desde la primera transacción del usuario hasta hoy.
        if first_tx_ts:
            br = benchmark_return(bench_history, first_tx_ts, now)
            mwr_all_inc = mwr(txs, snapshot, bonus_as="income")
            if br and mwr_all_inc is not None:
                windows.append(("all-time", mwr_all_inc, br["annualized_return"]))
        # YTD
        if ytd_value:
            br = benchmark_return(bench_history, ytd_start, now)
            mwr_y = mwr(txs, snapshot, bonus_as="income", start=ytd_start, start_value=ytd_value)
            if br and mwr_y is not None:
                windows.append((f"YTD ({now.year})", mwr_y, br["annualized_return"]))
        # 12m
        if twelvem_value:
            br = benchmark_return(bench_history, twelvem_start, now)
            mwr_12 = mwr(txs, snapshot, bonus_as="income", start=twelvem_start, start_value=twelvem_value)
            if br and mwr_12 is not None:
                windows.append(("12 meses", mwr_12, br["annualized_return"]))

        if windows:
            print(bar)
            print(f"  RENTABILIDAD VS BENCHMARK ({BENCHMARK_LABEL})")
            print(bar)
            print(f"  {'Periodo':<14} {'Tu MWR (income)':>18}  {'Benchmark':>16}  {'Δ vs benchmark':>18}")
            print(f"  {'-'*14} {'-'*18}  {'-'*16}  {'-'*18}")
            for label, mwr_v, bench_v in windows:
                delta_pp = (mwr_v - bench_v) * 100
                marker = " ✓" if delta_pp > 0 else ("  " if delta_pp == 0 else "  ")
                print(
                    f"  {label:<14} "
                    f"{mwr_v*100:>+15.2f} %  "
                    f"{bench_v*100:>+13.2f} %  "
                    f"{delta_pp:>+15.2f} pp{marker}"
                )
            print()
    elif BENCHMARK_ISIN:
        print(f"  ℹ Benchmark {BENCHMARK_ISIN} no disponible (sin histórico de precios).")
        print()

    print(bar)
    print("  APORTACIONES MENSUALES (compras brutas, incluye saveback/regalos)")
    print(bar)
    monthly = monthly_contributions(txs)
    cmp = contribution_vs_average(txs, now.year, now.month)
    if cmp:
        delta_str = "n/a" if cmp["delta_pct"] is None else f"{cmp['delta_pct']*100:+.1f}%"
        print(f"  Este mes ({now.year}-{now.month:02d}):       {fmt_eur(cmp['this_month']):>16}")
        print(f"  Media últimos {cmp['window_months_used']}m:        {fmt_eur(cmp['avg']):>16}")
        print(f"  Δ vs media:              {delta_str:>16}")
    elif monthly:
        last = sorted(monthly.items())[-3:]
        print("  (sin histórico suficiente para comparar; últimos meses con aportación):")
        for (y, m), v in last:
            print(f"    {y}-{m:02d}:  {fmt_eur(v):>16}")
    else:
        print("  (sin aportaciones registradas)")
    print()

    print(bar)
    if CONCENTRATION_LIMITS and CONCENTRATION_THRESHOLD is not None:
        header = f"  CONCENTRACIÓN (% sobre posiciones, límites por activo + threshold global {CONCENTRATION_THRESHOLD*100:.0f}%)"
    elif CONCENTRATION_LIMITS:
        header = f"  CONCENTRACIÓN (% sobre posiciones, alerta solo en activos con límite explícito)"
    elif CONCENTRATION_THRESHOLD is not None:
        header = f"  CONCENTRACIÓN (% sobre posiciones, alerta a >{CONCENTRATION_THRESHOLD*100:.0f}%)"
    else:
        header = f"  CONCENTRACIÓN (% sobre posiciones, sin alertas)"
    print(header)
    print(bar)
    conc = concentration(
        snapshot,
        limits=CONCENTRATION_LIMITS or None,
        default_threshold=CONCENTRATION_THRESHOLD,
    )
    if conc:
        max_bar = 18
        max_pct = max(c["pct"] for c in conc)
        exceeded_count = 0
        # "(global)" tag only useful in mixed mode: when some ISINs have explicit
        # limits and others fall back. In pure-global or pure-explicit, suppress.
        has_per_asset = bool(CONCENTRATION_LIMITS)
        for entry in conc:
            pct = entry["pct"]
            bar_len = int(round(pct * max_bar / max_pct))
            bar_str = "█" * bar_len
            title = (entry["title"] or entry["isin"])[:28]
            limit = entry["limit"]
            margin = entry["margin_pp"]
            if limit is None:
                trail = ""
            else:
                has_explicit = has_per_asset and entry["isin"] in CONCENTRATION_LIMITS
                source = " (global)" if (has_per_asset and not has_explicit) else ""
                if entry["exceeded"]:
                    trail = f"  límite {limit*100:>4.0f}%{source}, EXCEDIDO en {abs(margin):>4.1f} pp"
                    exceeded_count += 1
                else:
                    trail = f"  límite {limit*100:>4.0f}%{source}, margen {margin:>+5.1f} pp"
            print(f"  {title:<28} {pct*100:>6.2f}%  {bar_str:<{max_bar}}{trail}")
        print()
        if exceeded_count == 0:
            if any(e["limit"] is not None for e in conc):
                print(f"  ✓ Todas las posiciones dentro de su límite.")
        else:
            print(f"  ⚠ {exceeded_count} posición(es) por encima de su límite individual.")
            print(f"    Considera rebalancear si quieres ajustarlas.")
    print()

    # ── Atribución por posición ────────────────────────────────────────
    print(bar)
    print("  ATRIBUCIÓN DE RENDIMIENTO POR POSICIÓN (MWR per-ISIN, modo income)")
    print(bar)
    attr = per_position_attribution(snapshot, txs, bonus_as="income")
    if attr:
        label_w = max((len(p["title"] or "") for p in attr), default=10)
        label_w = min(max(label_w, 14), 32)
        print(f"  {'Activo':<{label_w}} {'valor':>12} {'peso':>6} {'MWR pos.':>11} {'aporta':>11}")
        print(f"  {'-'*label_w} {'-'*12} {'-'*6} {'-'*11} {'-'*11}")
        sum_contrib = 0.0
        for entry in attr:
            title = (entry["title"] or entry["isin"])[:label_w]
            print(
                f"  {title:<{label_w}} "
                f"{fmt_eur(entry['value']):>12} "
                f"{entry['value_pct']*100:>5.1f}% "
                f"{entry['position_mwr']*100:>+9.2f} % "
                f"{entry['contribution_pp']:>+8.2f} pp"
            )
            sum_contrib += entry["contribution_pp"]
        print(f"  {'-'*label_w} {'-'*12} {'-'*6} {'-'*11} {'-'*11}")
        print(f"  {'TOTAL contribuciones':<{label_w}} {' ':>12} {' ':>6} {' ':>11} {sum_contrib:>+8.2f} pp")
        print()
        print(f"  ── Suma ≈ rentabilidad anualizada de las posiciones vivas (no incluye")
        print(f"     ventas pasadas). Para el MWR all-time del portfolio completo, mira")
        print(f"     el bloque 'RENTABILIDAD — HISTÓRICO COMPLETO' arriba.")
    else:
        print("  (sin atribución disponible — posiciones sin flujos suficientes)")
    print()

    # ── Exposición por divisa ──────────────────────────────────────────
    if ASSET_CURRENCIES:
        print(bar)
        print("  EXPOSICIÓN POR DIVISA (sobre patrimonio total, incluye cash)")
        print(bar)
        exposure = currency_exposure(snapshot, ASSET_CURRENCIES, cash_currency="EUR")
        if exposure:
            max_bar = 22
            max_pct = max(x["pct"] for x in exposure)
            for entry in exposure:
                pct = entry["pct"]
                bar_len = int(round(pct * max_bar / max_pct)) if max_pct > 0 else 0
                bar_str = "█" * bar_len
                cur = entry["currency"]
                detail = f"{entry['n_positions']} pos." if entry["n_positions"] else "cash"
                print(f"  {cur:<8} {fmt_eur(entry['value_eur']):>14}  ({pct*100:>5.1f}%)  {bar_str:<{max_bar}}  {detail}")
            unknown = [x for x in exposure if x["currency"] == "UNKNOWN"]
            if unknown:
                print()
                print(f"  ⚠ {unknown[0]['n_positions']} posición(es) sin divisa mapeada en `asset_currencies` del config.")
        print()

    if verbose:
        print(bar)
        print("  POR POSICIÓN (--verbose)")
        print(bar)
        cb_user_per_isin = cost_basis_user_paid_per_isin(snapshot, txs)
        label_w = max((len(p.title or "") for p in snapshot.positions), default=10)
        label_w = min(max(label_w, 12), 28)
        print(f"  {'Activo':<{label_w}} {'valor':>12} {'cb propio':>12} {'Δ propio':>9} {'cb bruto':>12} {'Δ bruto':>9}")
        print(f"  {'-'*label_w} {'-'*12} {'-'*12} {'-'*9} {'-'*12} {'-'*9}")
        sum_value = 0.0
        sum_cb_user = 0.0
        sum_cb_tr = 0.0
        for p in sorted(snapshot.positions, key=lambda x: -x.net_value_eur):
            cb_user = cb_user_per_isin.get(p.isin)
            cb_tr = p.cost_basis_eur
            d_user = (p.net_value_eur - cb_user) / cb_user if cb_user and cb_user > 0 else None
            d_tr = (p.net_value_eur - cb_tr) / cb_tr if cb_tr and cb_tr > 0 else None
            title = (p.title or p.isin)[:label_w]
            cb_user_s = fmt_eur(cb_user) if cb_user else "      n/a"
            cb_tr_s = fmt_eur(cb_tr) if cb_tr else "      n/a"
            d_user_s = fmt_pct(d_user) if d_user is not None else "n/a"
            d_tr_s = fmt_pct(d_tr) if d_tr is not None else "n/a"
            print(f"  {title:<{label_w}} {fmt_eur(p.net_value_eur):>12} {cb_user_s:>12} {d_user_s:>9} {cb_tr_s:>12} {d_tr_s:>9}")
            sum_value += p.net_value_eur
            if cb_user: sum_cb_user += cb_user
            if cb_tr: sum_cb_tr += cb_tr
        print(f"  {'-'*label_w} {'-'*12} {'-'*12} {'-'*9} {'-'*12} {'-'*9}")
        d_user_t = (sum_value - sum_cb_user) / sum_cb_user if sum_cb_user > 0 else None
        d_tr_t = (sum_value - sum_cb_tr) / sum_cb_tr if sum_cb_tr > 0 else None
        d_user_total_s = fmt_pct(d_user_t) if d_user_t is not None else 'n/a'
        d_tr_total_s = fmt_pct(d_tr_t) if d_tr_t is not None else 'n/a'
        print(f"  {'TOTAL':<{label_w}} {fmt_eur(sum_value):>12} {fmt_eur(sum_cb_user):>12} {d_user_total_s:>9} {fmt_eur(sum_cb_tr):>12} {d_tr_total_s:>9}")
        print()


def sync_portfolio(dry_run: bool):
    """Snapshot del portfolio: escribe los `netValue` actuales en `PORTFOLIO_SHEET!PORTFOLIO_VALUE_RANGE`.

    Lee `PORTFOLIO_CELL_MAP` del config y escribe una fila por cada ISIN configurado, en orden.
    Si un ISIN no se encuentra en TR, deja la celda vacía y avisa.
    Con `dry_run=True` solo imprime los valores en consola, no toca la Sheet.
    Si `features.portfolio=false` en config.yaml, no hace nada.
    """
    if not is_feature_enabled("portfolio"):
        log.info("Feature 'portfolio' deshabilitada (config o broker).")
        return
    log.info("Conectando a Trade Republic...")
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
            log.warning(f"   {label:<12} ISIN={isin}  (no encontrado en TR)")
            continue
        net_value = float(pos["netValue"])
        values.append([net_value])
        log.info(f"   {label:<12} ISIN={isin}  {net_value:>10.2f} €")

    if dry_run:
        log.info("\n[dry-run] no se escribe nada en la Sheet.")
        return

    spreadsheet = open_spreadsheet()
    worksheet = spreadsheet.worksheet(PORTFOLIO_SHEET)
    worksheet.update(range_name=PORTFOLIO_VALUE_RANGE, values=values, value_input_option="USER_ENTERED")
    log.info(f"\nOK: {len(values) - len(missing)}/{len(values)} celdas escritas en {PORTFOLIO_SHEET}!{PORTFOLIO_VALUE_RANGE}")
    write_status(spreadsheet, "portfolio")

    # Persiste snapshot completo (cash + posiciones) para histórico de MWR.
    try:
        from brokers.tr.adapter import fetch_snapshot
        from core.metrics import cost_basis_total as _cb_total
        snap = asyncio.run(fetch_snapshot(tr, tz=TIMEZONE))
        store = _make_snapshot_store(spreadsheet)
        store.append(snap, _cb_total(snap))
        log.info(f"   snapshot guardado en `{SNAPSHOTS_SHEET}` (oculto).")
    except Exception as e:
        log.warning(f"   ⚠ no se pudo guardar snapshot ({e})")


def sync(dry_run: bool, since: datetime | None, init_mode: bool):
    """Sincroniza eventos de TR con la Sheet (gastos, ingresos, inversiones).

    Modos:
      - normal (defaults): descarga eventos desde inicio del mes actual − DEFAULT_BUFFER_DAYS;
        escribe gastos/ingresos nuevos (deduplicando con _sync_state); recalcula la pestaña
        de inversiones del mes actual (no toca meses pasados).
      - `since`: ventana custom para reprocesar un rango; útil cuando rellenas atrasados.
      - `init_mode`: descarga TODO el histórico, marca todos los eventos como ya
        sincronizados pero NO escribe nada en gastos/ingresos. Sirve para "alinear" la
        Sheet con TR cuando arrancas desde cero. Inversiones no se tocan.

    Con `dry_run=True` se descargan eventos pero no se escribe nada en la Sheet.
    """
    log.info("Conectando a Trade Republic...")
    tr = login()
    if init_mode:
        not_before = 0.0
        log.info("  modo init: descargando todo el historial")
    elif since:
        not_before = since.timestamp()
        log.info(f"  ventana: desde {since.date()}")
    else:
        now = datetime.now(tz=TIMEZONE)
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        cutoff = start_of_month - timedelta(days=DEFAULT_BUFFER_DAYS)
        not_before = cutoff.timestamp()
        log.info(f"  ventana: desde {cutoff.date()} (inicio mes actual - {DEFAULT_BUFFER_DAYS} días)")
    raw_events = asyncio.run(fetch_tr_events(tr, not_before))
    log.info(f"  {len(raw_events)} eventos descargados")

    flows = filter_events_by_flow(raw_events)
    # Aplica feature toggles: vacía la lista de la pestaña deshabilitada para que no se escriba.
    if not FEATURES.get("expenses", True):
        flows[EXPENSES_SHEET] = []
        log.info("  features.expenses=false → gastos no se sincronizan")
    if not FEATURES.get("income", True):
        flows[INCOME_SHEET] = []
        log.info("  features.income=false → ingresos no se sincronizan")
    if since:
        for k in flows:
            flows[k] = [e for e in flows[k] if e["ts"] >= since]
    flow_total = sum(len(v) for v in flows.values())
    log.info(f"  {flow_total} en gastos/ingresos")

    log.info("Abriendo Google Sheet...")
    spreadsheet = open_spreadsheet()

    if init_mode:
        all_ids = [e["id"] for v in flows.values() for e in v]
        append_synced_ids(spreadsheet, all_ids)
        log.info(f"\n[INIT] {len(all_ids)} gastos/ingresos marcados como sincronizados.")
        log.info("Inversiones intactas (los valores que ya tienes se respetan).")
        log.info("Próximas ejecuciones: gastos/ingresos nuevos + inversiones del mes actual recalculadas.")
        return

    synced_ids = load_synced_ids(spreadsheet)
    new_flows = {
        name: [e for e in events if e["id"] not in synced_ids]
        for name, events in flows.items()
    }
    new_total = sum(len(v) for v in new_flows.values())
    log.info(f"  {new_total} nuevos en gastos/ingresos (resto ya sincronizados)")

    written_ids = []
    for sheet_name, sheet_txs in new_flows.items():
        if sheet_txs:
            written_ids.extend(sync_to_sheet(spreadsheet, sheet_name, sheet_txs, dry_run))

    if not dry_run and written_ids:
        append_synced_ids(spreadsheet, written_ids)

    if FEATURES.get("investments", True):
        sync_investments(spreadsheet, raw_events, dry_run)
    else:
        log.info("\n  features.investments=false → inversiones no se sincronizan")

    if not dry_run:
        write_status(spreadsheet, "sync")
        log.info(f"\nOK: {len(written_ids)} mov. en gastos/ingresos + inversiones recalculadas.")


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
            sys.exit(doctor())
        elif args.features:
            list_features()
        elif args.init_sheet:
            init_sheet(dry_run=args.dry_run)
        elif args.debug_isin:
            debug_isin(args.debug_isin, refresh=args.refresh)
        elif args.mwr_flows:
            dump_mwr_flows(bonus_as=args.bonus_as, refresh=args.refresh, locale=args.locale)
        elif args.backfill_snapshots:
            backfill_snapshots(start_iso=args.start, frequency=args.frequency, refresh=args.refresh)
        elif args.insights:
            sync_insights(verbose=args.verbose, refresh=args.refresh)
        elif args.renta:
            from reports.renta_es import sync_renta as _sync_renta
            year = args.year or (datetime.now(tz=TIMEZONE).year - 1)
            _sync_renta(year, dry_run=args.dry_run)
        elif args.portfolio:
            sync_portfolio(dry_run=args.dry_run)
        else:
            sync(dry_run=args.dry_run, since=since, init_mode=args.init)
    except KeyboardInterrupt:
        log.info("\nInterrumpido.")
        sys.exit(130)


if __name__ == "__main__":
    main()
