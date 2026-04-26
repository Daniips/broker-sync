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

_SHEETS = CONFIG["sheets"]
EXPENSES_SHEET = _SHEETS["expenses"]
INCOME_SHEET = _SHEETS["income"]
PORTFOLIO_SHEET = _SHEETS["portfolio"]
STATUS_SHEET = _SHEETS["status"]
SYNC_STATE_SHEET = _SHEETS["sync_state"]
SNAPSHOTS_SHEET = _SHEETS.get("snapshots", "_snapshots")

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
_FEATURES_DEFAULT = {"expenses": True, "income": True, "investments": True, "portfolio": True}
FEATURES = {**_FEATURES_DEFAULT, **(CONFIG.get("features") or {})}

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

def open_spreadsheet():
    gc = gspread.oauth()
    return gc.open_by_key(SHEET_ID)


def write_status(spreadsheet, key: str):
    """Actualiza la pestaña STATUS_SHEET con el timestamp actual para `key`."""
    label = STATUS_LABELS[key]
    try:
        ws = spreadsheet.worksheet(STATUS_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=STATUS_SHEET, rows=10, cols=2)
        ws.update(values=[["Proceso", "Último OK"]], range_name="A1:B1")
        for i, lbl in enumerate(STATUS_LABELS.values(), start=2):
            ws.update_cell(i, 1, lbl)

    col_a = ws.col_values(1)
    row = next((i for i, v in enumerate(col_a, start=1) if v.strip() == label), None)
    if row is None:
        row = len(col_a) + 1
        ws.update_cell(row, 1, label)
    now = datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    ws.update_cell(row, 2, now)


def get_or_create_sync_state(spreadsheet):
    try:
        return spreadsheet.worksheet(SYNC_STATE_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=SYNC_STATE_SHEET, rows=1000, cols=1)
        ws.update(values=[["tr_event_id"]], range_name="A1")
        spreadsheet.batch_update({"requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": ws.id, "hidden": True},
                "fields": "hidden",
            }
        }]})
        return ws


def load_synced_ids(spreadsheet):
    ws = get_or_create_sync_state(spreadsheet)
    return set(ws.col_values(1)[1:])


def append_synced_ids(spreadsheet, new_ids):
    if not new_ids:
        return
    ws = get_or_create_sync_state(spreadsheet)
    ws.append_rows([[x] for x in new_ids], value_input_option="RAW")


# ── Snapshots históricos ──────────────────────────────────────────────────
# Pestaña oculta `_snapshots` con una fila por ejecución de sync/portfolio/
# insights. Sirve para desbloquear MWR YTD/12m: el valor de las posiciones al
# inicio del periodo se modela como "deposit sintético" en `core.metrics.mwr`.

_SNAPSHOTS_HEADER = ["ts", "cash_eur", "positions_value_eur", "cost_basis_eur", "total_eur"]


def get_or_create_snapshots_sheet(spreadsheet):
    try:
        return spreadsheet.worksheet(SNAPSHOTS_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=SNAPSHOTS_SHEET, rows=1000, cols=len(_SNAPSHOTS_HEADER))
        ws.update(values=[_SNAPSHOTS_HEADER], range_name=f"A1:{chr(ord('A')+len(_SNAPSHOTS_HEADER)-1)}1")
        spreadsheet.batch_update({"requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": ws.id, "hidden": True},
                "fields": "hidden",
            }
        }]})
        return ws


def append_snapshot_row(spreadsheet, snapshot, cost_basis_total_eur):
    """Añade una fila a la pestaña `_snapshots` con el estado actual."""
    from core.metrics import cost_basis_total as _cb_total
    ws = get_or_create_snapshots_sheet(spreadsheet)
    cb = cost_basis_total_eur if cost_basis_total_eur is not None else (_cb_total(snapshot) or 0.0)
    ws.append_rows([[
        snapshot.ts.isoformat(),
        round(snapshot.cash_eur, 2),
        round(snapshot.positions_value_eur, 2),
        round(cb, 2),
        round(snapshot.total_eur, 2),
    ]], value_input_option="RAW")


def load_snapshot_history(spreadsheet) -> list[dict]:
    """Lee todas las filas de `_snapshots` y devuelve dicts ordenados por ts ascendente."""
    try:
        ws = spreadsheet.worksheet(SNAPSHOTS_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        return []
    rows = ws.get_all_values()
    if len(rows) < 2:
        return []
    out = []
    for row in rows[1:]:
        if len(row) < 5 or not row[0]:
            continue
        try:
            ts = datetime.fromisoformat(row[0])
            out.append({
                "ts": ts,
                "cash_eur": float(row[1] or 0),
                "positions_value_eur": float(row[2] or 0),
                "cost_basis_eur": float(row[3] or 0),
                "total_eur": float(row[4] or 0),
            })
        except (ValueError, IndexError):
            continue
    out.sort(key=lambda x: x["ts"])
    return out


def snapshot_value_at(snapshots: list[dict], target_ts: datetime) -> float | None:
    """Devuelve positions_value_eur del último snapshot ≤ target_ts. None si no hay."""
    candidates = [s for s in snapshots if s["ts"] <= target_ts]
    if not candidates:
        return None
    return candidates[-1]["positions_value_eur"]


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


def debug_isin(isin: str):
    """Imprime todas las transacciones que el adapter saca para un ISIN.

    Útil para reconciliar con tu Excel: ves cada BUY/SELL/DIVIDEND con su
    fecha, importe y shares, y puedes contrastar qué es lo que TR realmente
    emite. Si tu Excel tiene un número que no aparece aquí, ese número no
    está en TR — viene de otra fuente (manual, bonus en cash, etc.).
    """
    from brokers.tr.adapter import fetch_transactions
    from collections import Counter

    log.info("Conectando a Trade Republic...")
    tr = login()
    log.info(f"Descargando transacciones (filtrando por ISIN={isin})...")
    txs = asyncio.run(fetch_transactions(tr, tz=TIMEZONE, gift_overrides=GIFT_COST_OVERRIDES))
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


def sync_insights(verbose: bool = False):
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
    from brokers.tr.adapter import fetch_snapshot, fetch_transactions
    from core.metrics import (
        contribution_vs_average,
        cost_basis_total as _cb_total,
        cost_basis_user_paid_per_isin,
        monthly_contributions,
        mwr,
        simple_return,
        total_invested,
        unrealized_return,
        unrealized_return_user_paid,
    )

    log.info("Conectando a Trade Republic...")
    tr = login()
    log.info("Descargando snapshot y transacciones...")
    snapshot = asyncio.run(fetch_snapshot(tr, tz=TIMEZONE))
    txs = asyncio.run(fetch_transactions(tr, tz=TIMEZONE, gift_overrides=GIFT_COST_OVERRIDES))
    log.info(f"   {len(txs)} transacciones, {len(snapshot.positions)} posiciones.")

    # Persiste snapshot + carga histórico para MWR YTD/12m.
    snapshot_history: list[dict] = []
    try:
        spreadsheet = open_spreadsheet()
        append_snapshot_row(spreadsheet, snapshot, _cb_total(snapshot))
        snapshot_history = load_snapshot_history(spreadsheet)
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
    if not FEATURES.get("portfolio", True):
        log.info("features.portfolio=false → snapshot de portfolio deshabilitado.")
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
        append_snapshot_row(spreadsheet, snap, _cb_total(snap))
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


# ── Informe IRPF (FIFO) ───────────────────────────────────────────────────

TAX_LOT_EVENT_TYPES = {
    "TRADING_TRADE_EXECUTED",
    "TRADING_SAVINGSPLAN_EXECUTED",
    "SAVEBACK_AGGREGATE",
    "TRADE_INVOICE",
    "GIFTING_RECIPIENT_ACTIVITY",
    "GIFTING_LOTTERY_PRIZE_ACTIVITY",
}

# Subconjunto: regalos recibidos (no hay amount.value en el root; el coste fiscal
# está en details.sections → Transaktion → Summe).
GIFT_EVENT_TYPES = {
    "GIFTING_RECIPIENT_ACTIVITY",
    "GIFTING_LOTTERY_PRIZE_ACTIVITY",
}

# Orden de preferencia cuando el mismo trade aparece en dos eventTypes.
_TAX_LOT_PREFERENCE = {
    "TRADING_TRADE_EXECUTED": 0,
    "TRADING_SAVINGSPLAN_EXECUTED": 1,
    "SAVEBACK_AGGREGATE": 2,
    "TRADE_INVOICE": 3,
    "GIFTING_LOTTERY_PRIZE_ACTIVITY": 4,
    "GIFTING_RECIPIENT_ACTIVITY": 5,
}

# Override manual para regalos cuyos detalles TR no traiga parseables.
# Se configura en config.yaml → gift_cost_overrides.
GIFT_COST_OVERRIDES: dict = CONFIG.get("gift_cost_overrides") or {}


# Movido a core/utils.py
from core.utils import parse_de_number as _parse_de_number


# Parsers de eventos TR movidos a brokers/tr/parser.py — re-exportados aquí
# con prefix `_` para mantener compatibilidad con tests y código existente.
from brokers.tr.parser import (
    extract_isin_from_icon as _extract_isin_from_icon,
    extract_trade_details as _extract_trade_details,
    extract_gift_details as _extract_gift_details,
)


def _build_lots_and_sales(events, target_year):
    """Del histórico completo: lotes de compra (ordenados) + ventas del año target.

    `amount.value` ya es neto (incluye comisión descontada en venta / sumada en compra),
    por tanto coste de adquisición = abs(amount.value) para compras y valor de
    transmisión = amount.value para ventas.
    """
    buy_lots = []
    sales = []
    skipped = []
    seen = set()  # dedup: (isin, ts_to_minute, abs(value))

    # Prioriza TRADING_TRADE_EXECUTED sobre TRADE_INVOICE si coexisten.
    ordered = sorted(events, key=lambda e: _TAX_LOT_PREFERENCE.get(e.get("eventType"), 99))

    for raw in ordered:
        et = raw.get("eventType")
        if et not in TAX_LOT_EVENT_TYPES:
            continue
        if raw.get("status") in EXCLUDED_STATUSES:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(TIMEZONE)
        title = (raw.get("title") or "").strip()

        # Regalos (lotería / ETF-Geschenk): el coste fiscal está en details, no en amount.value.
        if et in GIFT_EVENT_TYPES:
            g = _extract_gift_details(raw)
            isin = g["isin"]
            shares = g["shares"]
            cost = g["cost_eur"]
            # Override manual si TR no trae datos
            if isin in GIFT_COST_OVERRIDES:
                ov = GIFT_COST_OVERRIDES[isin]
                shares = ov.get("shares", shares)
                cost = ov.get("cost_eur", cost)
            if not isin or not shares or shares <= 0 or not cost or cost <= 0:
                skipped.append({"ts": ts, "title": title, "value": None, "type": et})
                continue
            fingerprint = (isin, int(ts.timestamp() // 60), round(cost, 2))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            buy_lots.append({
                "timestamp": ts,
                "isin": isin,
                "title": title or "GIFT",
                "shares": shares,
                "cost_eur": cost,
            })
            continue

        value = (raw.get("amount") or {}).get("value")
        if value is None:
            continue
        d = _extract_trade_details(raw)
        isin, shares = d["isin"], d["shares"]

        if not isin or shares is None or shares <= 0:
            skipped.append({"ts": ts, "title": title, "value": value, "type": et})
            continue

        fingerprint = (isin, int(ts.timestamp() // 60), round(abs(value), 2))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)

        if value > 0:  # venta
            if ts.year != target_year:
                continue
            sales.append({
                "timestamp": ts,
                "isin": isin,
                "title": title,
                "shares": shares,
                "proceeds_eur": abs(value),
            })
        else:  # compra
            buy_lots.append({
                "timestamp": ts,
                "isin": isin,
                "title": title,
                "shares": shares,
                "cost_eur": abs(value),
            })

    buy_lots.sort(key=lambda x: x["timestamp"])
    sales.sort(key=lambda x: x["timestamp"])
    return buy_lots, sales, skipped


# FIFO movido a core/fifo.py — re-exportado para compatibilidad
from core.fifo import apply_fifo as _apply_fifo


# ── Rendimientos del capital mobiliario (dividendos, intereses, bonos) ────

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


# Movido a brokers/tr/parser.py
from brokers.tr.parser import extract_dividend_details as _extract_dividend_details


def _collect_dividends(events, year):
    """Lista de dividendos del año con bruto, retención y neto por operación."""
    out = []
    for raw in events:
        if raw.get("eventType") != "SSP_CORPORATE_ACTION_CASH":
            continue
        if raw.get("status") in EXCLUDED_STATUSES:
            continue
        subtitle = (raw.get("subtitle") or "").strip()
        if subtitle not in DIVIDEND_SUBTITLES:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(TIMEZONE)
        if ts.year != year:
            continue
        d = _extract_dividend_details(raw)
        out.append({
            "timestamp": ts,
            "isin": d["isin"] or "?",
            "title": (raw.get("title") or "").strip(),
            "subtitle": subtitle,
            "gross": d["gross"] or 0.0,
            "tax": d["tax"] or 0.0,
            "net": d["net"] or 0.0,
        })
    out.sort(key=lambda x: x["timestamp"])
    return out


def _collect_interest(events, year):
    """Lista de intereses en efectivo del año (INTEREST_PAYOUT[_CREATED])."""
    out = []
    for raw in events:
        if raw.get("eventType") not in {"INTEREST_PAYOUT", "INTEREST_PAYOUT_CREATED"}:
            continue
        if raw.get("status") in EXCLUDED_STATUSES:
            continue
        value = (raw.get("amount") or {}).get("value")
        if value is None:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(TIMEZONE)
        if ts.year != year:
            continue
        out.append({
            "timestamp": ts,
            "title": (raw.get("title") or "").strip(),
            "subtitle": (raw.get("subtitle") or "").strip(),
            "amount": float(value),
        })
    out.sort(key=lambda x: x["timestamp"])
    return out


def _collect_bond_income(events, year):
    """Rendimiento neto de bonos, agrupado por ISIN.

    Para cada bono con cupón/amortización en `year`:
      rendimiento_neto = cupones_año + importe_amortización − coste_de_compra

    Devuelve [{isin, title, cupones, amortizacion, coste, rendimiento_neto, flows}]
    con `flows` = lista de dicts para trazabilidad.
    """
    by_isin = {}

    # 1) Cupones y amortizaciones del año target — estos marcan qué ISINs son bonos
    for raw in events:
        if raw.get("status") in EXCLUDED_STATUSES:
            continue
        if raw.get("eventType") != "SSP_CORPORATE_ACTION_CASH":
            continue
        subtitle = (raw.get("subtitle") or "").strip()
        if subtitle not in BOND_SUBTITLES:
            continue
        value = (raw.get("amount") or {}).get("value")
        if value is None:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(TIMEZONE)
        if ts.year != year:
            continue
        isin = _extract_isin_from_icon(raw)
        if not isin:
            continue
        e = by_isin.setdefault(isin, {
            "isin": isin,
            "title": (raw.get("title") or "").strip(),
            "cupones": 0.0, "amortizacion": 0.0, "coste": 0.0,
            "rendimiento_neto": 0.0, "flows": [],
        })
        if subtitle in BOND_MATURITY_SUBTITLES:
            e["amortizacion"] += float(value)
        else:
            e["cupones"] += float(value)
        e["flows"].append({"ts": ts, "subtitle": subtitle, "amount": float(value)})

    # 2) Compras asociadas (cualquier fecha, todo el histórico) por ISIN de bono detectado
    for raw in events:
        if raw.get("status") in EXCLUDED_STATUSES:
            continue
        et = raw.get("eventType")
        if et not in {"TRADE_INVOICE", "TRADING_TRADE_EXECUTED"}:
            continue
        value = (raw.get("amount") or {}).get("value")
        if value is None or value >= 0:
            continue
        d = _extract_trade_details(raw)
        isin = d.get("isin") or _extract_isin_from_icon(raw)
        if not isin or isin not in by_isin:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(TIMEZONE)
        by_isin[isin]["coste"] += abs(float(value))
        by_isin[isin]["flows"].append({"ts": ts, "subtitle": "Kauforder", "amount": float(value)})

    # 3) Cerrar: calcular rendimiento neto y ordenar flows cronológicamente
    out = []
    for isin, e in by_isin.items():
        e["rendimiento_neto"] = e["cupones"] + e["amortizacion"] - e["coste"]
        e["flows"].sort(key=lambda x: x["ts"])
        out.append(e)
    out.sort(key=lambda x: x["flows"][0]["ts"] if x["flows"] else datetime.min.replace(tzinfo=TIMEZONE))
    return out


def _collect_saveback(events, year):
    """Saveback recibido en el año (controvertido fiscalmente: rendimiento en especie)."""
    out = []
    for raw in events:
        if raw.get("eventType") != "SAVEBACK_AGGREGATE":
            continue
        if raw.get("status") in EXCLUDED_STATUSES:
            continue
        value = (raw.get("amount") or {}).get("value")
        if value is None:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(TIMEZONE)
        if ts.year != year:
            continue
        out.append({
            "ts": ts,
            "title": (raw.get("title") or "").strip(),
            "amount": abs(float(value)),
        })
    out.sort(key=lambda x: x["ts"])
    return out


def _retentions_by_country(dividends):
    """Agrupa retención extranjera por país (primeros 2 chars del ISIN)."""
    by_country = defaultdict(lambda: {"gross": 0.0, "tax": 0.0, "net": 0.0, "count": 0})
    for d in dividends:
        isin = d.get("isin") or "??"
        country = isin[:2] if len(isin) >= 2 else "??"
        bc = by_country[country]
        bc["gross"] += d["gross"]
        bc["tax"] += d["tax"]
        bc["net"] += d["net"]
        bc["count"] += 1
    return dict(by_country)


async def fetch_tr_portfolio_and_cash(tr):
    """Devuelve (portfolio_positions, cash_list). Reutiliza el loop de pytr Portfolio."""
    _patch_compact_portfolio_with_sec_acc_no(tr)
    p = Portfolio(tr, include_watchlist=False, lang="es", output=None)
    await p.portfolio_loop()
    return p.portfolio, (p.cash or [])


def _get_total_position_and_cash(tr):
    """Snapshot completo: instrumentos + cash. Devuelve (items, total_inst, cash_items, cash_total)."""
    positions, cash = asyncio.run(fetch_tr_portfolio_and_cash(tr))
    items = []
    total_inst = 0.0
    for p in positions:
        v = float(p.get("netValue") or 0)
        if v <= 0:
            continue
        items.append({"isin": p.get("instrumentId"), "value_eur": v})
        total_inst += v
    items.sort(key=lambda x: -x["value_eur"])

    cash_items = []
    cash_total_eur = 0.0
    for c in cash:
        amt = float(c.get("amount") or 0)
        currency = c.get("currencyId", "EUR")
        cash_items.append({"currency": currency, "amount": amt})
        if currency == "EUR":
            cash_total_eur += amt
    return items, total_inst, cash_items, cash_total_eur


def write_renta_to_sheet(spreadsheet, year, results, skipped, dividends, interests, bonds,
                         crypto, retentions=None, savebacks=None,
                         portfolio_items=None, portfolio_total=0.0,
                         cash_items=None, cash_total=0.0):
    """Escribe el informe IRPF completo en la pestaña 'Renta YYYY'."""
    sheet_name = f"Renta {year}"
    try:
        ws = spreadsheet.worksheet(sheet_name)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=sheet_name, rows=200, cols=10)

    rows = []

    # ── 1. Ganancias y pérdidas patrimoniales (FIFO) ──
    rows.append([f"1. GANANCIAS Y PÉRDIDAS PATRIMONIALES (FIFO) — Renta {year}"])
    rows.append([
        "Fecha", "Asset", "ISIN", "Shares vendidas",
        "Valor transmisión (€)", "Coste adquisición FIFO (€)",
        "Ganancia/Pérdida (€)", "Nº lotes casados",
        "Shares sin casar", "Aviso",
    ])
    total_proceeds = total_cost = total_gain = 0.0
    for r in results:
        s = r["sale"]
        aviso = "HISTÓRICO INCOMPLETO" if r["shares_unmatched"] > 1e-6 else ""
        rows.append([
            s["timestamp"].date().isoformat(),
            s["title"], s["isin"],
            round(s["shares"], 6),
            round(s["proceeds_eur"], 2),
            round(r["cost_basis"], 2),
            round(r["gain_loss"], 2),
            len(r["matched_lots"]),
            round(r["shares_unmatched"], 6) if r["shares_unmatched"] > 1e-6 else 0,
            aviso,
        ])
        total_proceeds += s["proceeds_eur"]
        total_cost += r["cost_basis"]
        total_gain += r["gain_loss"]
    rows.append(["TOTAL", "", "", "",
                 round(total_proceeds, 2), round(total_cost, 2), round(total_gain, 2),
                 "", "", ""])
    if skipped:
        rows.append([f"Operaciones omitidas (sin ISIN/shares parseables): {len(skipped)}"])

    # ── 2. Dividendos (casilla 0029) ──
    rows.append([])
    rows.append([f"2. DIVIDENDOS — casilla 0029"])
    rows.append(["Fecha", "Asset", "ISIN", "Tipo", "Bruto (€)", "Retención (€)", "Neto (€)"])
    tot_gross = tot_tax = tot_net = 0.0
    for d in dividends:
        rows.append([
            d["timestamp"].date().isoformat(),
            d["title"], d["isin"], _es(d["subtitle"]),
            round(d["gross"], 2), round(d["tax"], 2), round(d["net"], 2),
        ])
        tot_gross += d["gross"]
        tot_tax += d["tax"]
        tot_net += d["net"]
    rows.append(["TOTAL", "", "", "",
                 round(tot_gross, 2), round(tot_tax, 2), round(tot_net, 2)])

    # ── 3. Intereses (casilla 0027) ──
    rows.append([])
    rows.append([f"3. INTERESES — casilla 0027"])
    rows.append(["Fecha", "Descripción", "Detalle", "Importe (€)"])
    tot_int = 0.0
    for i in interests:
        rows.append([
            i["timestamp"].date().isoformat(),
            _es(i["title"]), _es(i["subtitle"]),
            round(i["amount"], 2),
        ])
        tot_int += i["amount"]
    rows.append(["TOTAL", "", "", round(tot_int, 2)])

    # ── 4. Rendimientos de bonos / otros activos financieros (casilla 0031) ──
    rows.append([])
    rows.append([f"4. RENDIMIENTOS DE BONOS / OTROS ACT. FINANCIEROS — casilla 0031"])
    rows.append(["ISIN", "Título", "Cupones (€)", "Amortización (€)", "Coste compra (€)", "Rendimiento neto (€)"])
    tot_bond = 0.0
    for b in bonds:
        rows.append([
            b["isin"], b["title"],
            round(b["cupones"], 2),
            round(b["amortizacion"], 2),
            round(b["coste"], 2),
            round(b["rendimiento_neto"], 2),
        ])
        tot_bond += b["rendimiento_neto"]
    rows.append(["TOTAL", "", "", "", "", round(tot_bond, 2)])
    # Detalle de flujos por bono
    for b in bonds:
        rows.append([])
        rows.append([f"  Flujos {b['isin']} ({b['title']}):"])
        for f in b["flows"]:
            rows.append([f["ts"].date().isoformat(), _es(f["subtitle"]), "", "", "", round(f["amount"], 2)])

    # ── 5. Resumen por casilla ──
    rows.append([])
    rows.append(["5. RESUMEN POR CASILLA (contrastar con borrador Hacienda)"])
    rows.append(["Casilla", "Concepto", "Importe script (€)"])
    rows.append(["0027", "Intereses", round(tot_int, 2)])
    rows.append(["0029", "Dividendos (neto)", round(tot_net, 2)])
    rows.append(["0029", "Dividendos (bruto, si quieres declarar bruto)", round(tot_gross, 2)])
    rows.append(["0029", "Retención extranjera (deducción doble imposición)", round(tot_tax, 2)])
    rows.append(["0031", "Rendimientos bonos / otros activos financieros", round(tot_bond, 2)])
    rows.append([
        "Ganancias patrimoniales",
        f"Total G/P neta (ETFs + acciones)",
        round(total_gain, 2),
    ])

    # ── 6. Retenciones por país ──
    rows.append([])
    rows.append([f"6. RETENCIONES EXTRANJERAS POR PAÍS (deducción doble imposición)"])
    rows.append(["País (ISIN[:2])", "Nº dividendos", "Bruto (€)", "Retención (€)", "Neto (€)"])
    for country, r in sorted((retentions or {}).items()):
        rows.append([country, r["count"], round(r["gross"], 2),
                     round(r["tax"], 2), round(r["net"], 2)])

    # ── 7. Saveback ──
    rows.append([])
    rows.append([f"7. SAVEBACK RECIBIDO (rendimiento en especie controvertido)"])
    rows.append(["Fecha", "Asset", "Importe (€)"])
    tot_sb = 0.0
    for s in (savebacks or []):
        rows.append([s["ts"].date().isoformat(), s["title"], round(s["amount"], 2)])
        tot_sb += s["amount"]
    rows.append(["TOTAL", "", round(tot_sb, 2)])

    # ── 8. Cripto ──
    rows.append([])
    rows.append([f"8. POSICIÓN CRIPTO (snapshot actual; Modelo 721 si >50k €)"])
    rows.append(["ISIN", "Activo", "Valor actual (€)"])
    tot_crypto = 0.0
    for c in crypto:
        rows.append([c["isin"], c["label"], round(c["value_eur"], 2)])
        tot_crypto += c["value_eur"]
    rows.append(["TOTAL", "", round(tot_crypto, 2)])

    # ── 9. Modelo 720 ──
    rows.append([])
    rows.append([f"9. SALDO TR — Modelo 720 (umbral 50.000 €)"])
    rows.append([f"⚠ Snapshot HOY, no a 31/12/{year}. Para el dato oficial usa el Jährlicher Steuerbericht {year} de TR."])
    rows.append(["IBAN español NO exime: TR custodia en Alemania → cuenta como bien extranjero."])
    rows.append(["ISIN / Concepto", "Valor (€)"])
    for it in (portfolio_items or []):
        rows.append([it["isin"], round(it["value_eur"], 2)])
    rows.append(["Subtotal instrumentos", round(portfolio_total, 2)])
    for c in (cash_items or []):
        rows.append([f"Cash {c['currency']}", round(c["amount"], 2)])
    rows.append(["Subtotal cash", round(cash_total, 2)])
    grand_total = portfolio_total + cash_total
    rows.append(["TOTAL HOY (instrumentos + cash)", round(grand_total, 2)])
    if grand_total > 50000:
        rows.append(["⚠ >50k€ HOY — atención al Modelo 720 del año en curso."])
    else:
        rows.append(["Lejos del umbral de 50k€; sin obligación previsible."])

    rows.append([])
    rows.append([f"Generado: {datetime.now(tz=TIMEZONE).strftime('%Y-%m-%d %H:%M')}"])

    ws.update(values=rows, range_name="A1", value_input_option="USER_ENTERED")
    log.info(f"  pestaña '{sheet_name}' actualizada")


def sync_renta(year, dry_run=False):
    """Genera el informe IRPF del año fiscal `year` (consola + pestaña 'Renta YYYY').

    Secciones del informe (ver RENTA.md para detalle de cada una):
      1. Ganancias / pérdidas patrimoniales (FIFO por ISIN)
      2. Dividendos (casilla 0029) con bruto, retención y neto
      3. Intereses (casilla 0027)
      4. Bonos / otros activos financieros (casilla 0031) con rendimiento neto
      5. Resumen por casilla — para contrastar con el borrador de Hacienda
      6. Retenciones extranjeras por país (deducción doble imposición)
      7. Saveback recibido (controvertido fiscalmente, informativo)
      8. Posición cripto (informativo Modelo 721)
      9. Saldo total TR (orientativo Modelo 720; snapshot HOY, no a 31/12)

    Con `dry_run=True` solo imprime en consola, no escribe en la Sheet.

    Importante: las cifras son orientativas; verifica contra el PDF
    "Jährlicher Steuerbericht YYYY" oficial de TR antes de presentar la declaración.
    """
    log.info(f"Conectando a Trade Republic...")
    tr = login()
    events = asyncio.run(fetch_tr_events(tr))
    log.info(f"  {len(events)} eventos descargados (histórico completo)")

    buy_lots, sales, skipped = _build_lots_and_sales(events, year)
    dividends = _collect_dividends(events, year)
    interests = _collect_interest(events, year)
    bonds = _collect_bond_income(events, year)

    # Cálculos (se hacen siempre — son baratos; los toggles solo afectan a la salida).
    results = _apply_fifo(buy_lots, sales) if sales else []
    total_proceeds = sum(r["sale"]["proceeds_eur"] for r in results)
    total_cost = sum(r["cost_basis"] for r in results)
    total_gain = sum(r["gain_loss"] for r in results)
    tot_gross = sum(d["gross"] for d in dividends)
    tot_tax = sum(d["tax"] for d in dividends)
    tot_net = sum(d["net"] for d in dividends)
    tot_int = sum(i["amount"] for i in interests)
    tot_bond = sum(b["rendimiento_neto"] for b in bonds)
    retentions = _retentions_by_country(dividends)
    savebacks = _collect_saveback(events, year)
    tot_sb = sum(s["amount"] for s in savebacks)

    # ── 1. Ganancias/pérdidas ──
    if RENTA_SECTIONS.get("fifo", True):
        log.info(f"\n[Renta {year}] 1. GANANCIAS/PÉRDIDAS PATRIMONIALES (FIFO)")
        if skipped:
            log.warning(f"  {len(skipped)} operaciones sin ISIN/shares parseables — se omiten.")
        for r in results:
            s = r["sale"]
            sign = "+" if r["gain_loss"] >= 0 else ""
            log.info(f"  {s['timestamp'].date()}  {s['title']:<32} ISIN={s['isin']}  "
                     f"vta={s['proceeds_eur']:>7.2f}€  coste={r['cost_basis']:>7.2f}€  "
                     f"G/P={sign}{r['gain_loss']:>7.2f}€")
            if r["shares_unmatched"] > 1e-6:
                log.warning(f"    AVISO: {r['shares_unmatched']:.6f} shares sin casar")
        log.info(f"  → TOTAL G/P neta: {'+' if total_gain >= 0 else ''}{total_gain:.2f} €")

    # ── 2. Dividendos ──
    if RENTA_SECTIONS.get("dividends", True):
        log.info(f"\n[Renta {year}] 2. DIVIDENDOS (casilla 0029)")
        for d in dividends:
            log.info(f"  {d['timestamp'].date()}  {d['title']:<28} {_es(d['subtitle']):<28} "
                     f"bruto={d['gross']:>6.2f}€  ret={d['tax']:>5.2f}€  neto={d['net']:>6.2f}€")
        log.info(f"  → TOTAL: bruto={tot_gross:.2f}€  retención extranjera={tot_tax:.2f}€  neto={tot_net:.2f}€")

    # ── 3. Intereses ──
    if RENTA_SECTIONS.get("interest", True):
        log.info(f"\n[Renta {year}] 3. INTERESES (casilla 0027)")
        for i in interests:
            log.info(f"  {i['timestamp'].date()}  {_es(i['title']):<14} {_es(i['subtitle']):<18} {i['amount']:>7.2f} €")
        log.info(f"  → TOTAL intereses: {tot_int:.2f} €")

    # ── 4. Bonos (extranjeros) / otros activos financieros ──
    if RENTA_SECTIONS.get("bonds", True):
        log.info(f"\n[Renta {year}] 4. RENDIMIENTOS DE BONOS / OTROS ACT. FINANCIEROS (casilla 0031)")
        for b in bonds:
            log.info(f"  ISIN {b['isin']}  '{b['title']}'")
            for f in b["flows"]:
                log.info(f"    {f['ts'].date()}  {_es(f['subtitle']):<22} {f['amount']:>+10.2f} €")
            log.info(f"    cupones   = {b['cupones']:>+10.2f} €")
            log.info(f"    amortiz.  = {b['amortizacion']:>+10.2f} €")
            log.info(f"    coste     = {-b['coste']:>+10.2f} €")
            log.info(f"    → rendim. neto: {b['rendimiento_neto']:>+10.2f} €  "
                     f"({b['rendimiento_neto']/b['coste']*100:+.2f}% sobre inversión)"
                     if b['coste'] > 0 else f"    → rendim. neto: {b['rendimiento_neto']:>+10.2f} €")
        log.info(f"  → TOTAL rendimiento neto bonos: {tot_bond:+.2f} €")

    # ── 5. Resumen por casilla ──
    if RENTA_SECTIONS.get("summary_by_box", True):
        log.info(f"\n[Renta {year}] 5. RESUMEN POR CASILLA (contrastar con borrador)")
        log.info(f"  Casilla 0027 (Intereses)           : {tot_int:>8.2f} €")
        log.info(f"  Casilla 0029 (Dividendos neto)     : {tot_net:>8.2f} €")
        log.info(f"    · retención extranjera (ded.DII) : {tot_tax:>8.2f} €")
        log.info(f"  Casilla 0031 (bonos/otros act.fin.): {tot_bond:>8.2f} €")
        log.info(f"  Ganancias/pérdidas patrimoniales   : {total_gain:>+8.2f} €  ({len(results)} ventas)")

    # ── 6. Retenciones extranjeras por país ──
    if RENTA_SECTIONS.get("retentions", True):
        log.info(f"\n[Renta {year}] 6. RETENCIONES EXTRANJERAS POR PAÍS (deducción doble imposición)")
        for country, r in sorted(retentions.items()):
            if r["tax"] > 0 or r["gross"] > 0:
                log.info(f"  {country}: {r['count']} dividendos, bruto={r['gross']:.2f}€, "
                         f"retención={r['tax']:.2f}€, neto={r['net']:.2f}€")
        if not retentions:
            log.info("  (sin dividendos)")

    # ── 7. Saveback ──
    if RENTA_SECTIONS.get("saveback", True):
        log.info(f"\n[Renta {year}] 7. SAVEBACK RECIBIDO (controvertido: rendimiento en especie)")
        log.info(f"  {len(savebacks)} eventos saveback en {year}, total = {tot_sb:.2f} €")
        log.info(f"  TR no lo reporta a Hacienda. Algunos asesores lo declaran como rdto. capital mobiliario en 0029.")

    # ── 8. Snapshot portfolio + cash (necesario para crypto y modelo720) ──
    portfolio_items, portfolio_total = [], 0.0
    cash_items, cash_total = [], 0.0
    crypto = []
    if RENTA_SECTIONS.get("crypto", True) or RENTA_SECTIONS.get("modelo720", True):
        try:
            portfolio_items, portfolio_total, cash_items, cash_total = _get_total_position_and_cash(tr)
        except Exception as e:
            log.warning(f"  no se pudo obtener snapshot de portfolio/cash: {e}")
        for it in portfolio_items:
            if it["isin"] in CRYPTO_ISINS:
                label = next((lbl for isin, lbl in PORTFOLIO_CELL_MAP if isin == it["isin"]), it["isin"])
                crypto.append({"isin": it["isin"], "label": label, "value_eur": it["value_eur"]})
    tot_crypto = sum(c["value_eur"] for c in crypto)

    if RENTA_SECTIONS.get("crypto", True):
        log.info(f"\n[Renta {year}] 8. POSICIÓN CRIPTO (snapshot actual)")
        for c in crypto:
            log.info(f"  {c['label']:<10} ISIN={c['isin']}  {c['value_eur']:>8.2f} €")
        log.info(f"  → TOTAL cripto: {tot_crypto:.2f} €  "
                 f"{'(>50k€ → Modelo 721)' if tot_crypto > 50000 else '(<50k€, sin Modelo 721)'}")

    if RENTA_SECTIONS.get("modelo720", True):
        now_str = datetime.now(tz=TIMEZONE).strftime("%Y-%m-%d")
        grand_total = portfolio_total + cash_total
        log.info(f"\n[Renta {year}] 9. SALDO TR — orientativo Modelo 720 (umbral 50.000 €)")
        log.info(f"  Posiciones (instrumentos): {portfolio_total:>10.2f} €")
        for c in cash_items:
            log.info(f"  Cash {c['currency']:<3}             : {c['amount']:>10.2f} €")
        log.info(f"  TOTAL HOY ({now_str})    : {grand_total:>10.2f} €")
        if grand_total > 50000:
            log.warning(f"  ⚠  >50k€ HOY → presta atención al Modelo 720 del año actual.")
        else:
            log.info(f"  Lejos del umbral de 50k€ HOY; sin obligación previsible para el año en curso.")
        log.info(f"  ⚠  El Modelo 720 se basa en el saldo a 31/12/{year} o saldo medio Q4, NO el de hoy.")
        log.info(f"     Para el dato OFICIAL: abre el PDF 'Jährlicher Steuerbericht {year}' de TR.")
        log.info(f"  NOTA: el IBAN español de TR no exime del 720; lo que cuenta es el custodio (DE).")

    if dry_run:
        log.info("\n[dry-run] no se escribe en la Sheet.")
        return

    log.info("\nAbriendo Google Sheet...")
    spreadsheet = open_spreadsheet()
    write_renta_to_sheet(spreadsheet, year, results, skipped, dividends, interests, bonds,
                         crypto, retentions, savebacks, portfolio_items, portfolio_total,
                         cash_items, cash_total)


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
    args = p.parse_args()
    since = None
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=TIMEZONE)
    try:
        if args.doctor:
            sys.exit(doctor())
        elif args.init_sheet:
            init_sheet(dry_run=args.dry_run)
        elif args.debug_isin:
            debug_isin(args.debug_isin)
        elif args.insights:
            sync_insights(verbose=args.verbose)
        elif args.renta:
            year = args.year or (datetime.now(tz=TIMEZONE).year - 1)
            sync_renta(year, dry_run=args.dry_run)
        elif args.portfolio:
            sync_portfolio(dry_run=args.dry_run)
        else:
            sync(dry_run=args.dry_run, since=since, init_mode=args.init)
    except KeyboardInterrupt:
        log.info("\nInterrumpido.")
        sys.exit(130)


if __name__ == "__main__":
    main()
