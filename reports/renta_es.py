"""
Informe IRPF español (Renta) generado a partir de eventos de Trade Republic.

Spanish tax report (Renta) generated from Trade Republic events.

Este módulo es España-específico. Para otros regímenes fiscales (UK ISA,
DE Steuerbericht, etc.) se añadirían módulos hermanos `reports/renta_uk.py`,
`reports/renta_de.py`, etc., todos consumiendo `core.fifo` y los parsers de
`brokers/tr/parser.py`.

This module is Spain-specific. For other tax regimes (UK ISA, DE
Steuerbericht, etc.) sibling modules `reports/renta_uk.py` /
`renta_de.py` would be added, all consuming `core.fifo` and the parsers
in `brokers/tr/parser.py`.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime

import gspread

import tr_sync
from brokers.tr.parser import (
    extract_dividend_details,
    extract_gift_details,
    extract_isin_from_icon,
    extract_trade_details,
)
from core.fifo import apply_fifo


# ── Constants (renta-only, no config dependency) ──────────────────────────

TAX_LOT_EVENT_TYPES = {
    "TRADING_TRADE_EXECUTED",
    "TRADING_SAVINGSPLAN_EXECUTED",
    "SAVEBACK_AGGREGATE",
    "TRADE_INVOICE",
    "GIFTING_RECIPIENT_ACTIVITY",
    "GIFTING_LOTTERY_PRIZE_ACTIVITY",
}

# Gifts: their cost basis lives in details.sections (not in amount.value).
# Regalos: el coste fiscal está en details.sections, no en amount.value.
GIFT_EVENT_TYPES = {
    "GIFTING_RECIPIENT_ACTIVITY",
    "GIFTING_LOTTERY_PRIZE_ACTIVITY",
}

# Order of preference when the same trade appears under two eventTypes.
# Orden de preferencia cuando el mismo trade aparece en dos eventTypes.
_TAX_LOT_PREFERENCE = {
    "TRADING_TRADE_EXECUTED": 0,
    "TRADING_SAVINGSPLAN_EXECUTED": 1,
    "SAVEBACK_AGGREGATE": 2,
    "TRADE_INVOICE": 3,
    "GIFTING_LOTTERY_PRIZE_ACTIVITY": 4,
    "GIFTING_RECIPIENT_ACTIVITY": 5,
}


# ── Lots and sales builder ────────────────────────────────────────────────

def _build_lots_and_sales(events, target_year):
    """Del histórico completo: lotes de compra (ordenados) + ventas del año target.

    `amount.value` ya es neto (incluye comisión descontada en venta / sumada en compra),
    por tanto coste de adquisición = abs(amount.value) para compras y valor de
    transmisión = amount.value para ventas.
    """
    log = tr_sync.log
    tz = tr_sync.TIMEZONE
    excluded_statuses = tr_sync.EXCLUDED_STATUSES
    gift_cost_overrides = tr_sync.GIFT_COST_OVERRIDES

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
        if raw.get("status") in excluded_statuses:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(tz)
        title = (raw.get("title") or "").strip()

        # Regalos (lotería / ETF-Geschenk): el coste fiscal está en details, no en amount.value.
        if et in GIFT_EVENT_TYPES:
            g = extract_gift_details(raw)
            isin = g["isin"]
            shares = g["shares"]
            cost = g["cost_eur"]
            # Override manual si TR no trae datos
            if isin in gift_cost_overrides:
                ov = gift_cost_overrides[isin]
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
        d = extract_trade_details(raw)
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


# ── Income collectors ─────────────────────────────────────────────────────

def _collect_dividends(events, year):
    """Lista de dividendos del año con bruto, retención y neto por operación."""
    tz = tr_sync.TIMEZONE
    excluded_statuses = tr_sync.EXCLUDED_STATUSES
    dividend_subtitles = tr_sync.DIVIDEND_SUBTITLES

    out = []
    for raw in events:
        if raw.get("eventType") != "SSP_CORPORATE_ACTION_CASH":
            continue
        if raw.get("status") in excluded_statuses:
            continue
        subtitle = (raw.get("subtitle") or "").strip()
        if subtitle not in dividend_subtitles:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(tz)
        if ts.year != year:
            continue
        d = extract_dividend_details(raw)
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
    tz = tr_sync.TIMEZONE
    excluded_statuses = tr_sync.EXCLUDED_STATUSES

    out = []
    for raw in events:
        if raw.get("eventType") not in {"INTEREST_PAYOUT", "INTEREST_PAYOUT_CREATED"}:
            continue
        if raw.get("status") in excluded_statuses:
            continue
        value = (raw.get("amount") or {}).get("value")
        if value is None:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(tz)
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
    tz = tr_sync.TIMEZONE
    excluded_statuses = tr_sync.EXCLUDED_STATUSES
    bond_subtitles = tr_sync.BOND_SUBTITLES
    bond_maturity_subtitles = tr_sync.BOND_MATURITY_SUBTITLES

    by_isin = {}

    # 1) Cupones y amortizaciones del año target — estos marcan qué ISINs son bonos
    for raw in events:
        if raw.get("status") in excluded_statuses:
            continue
        if raw.get("eventType") != "SSP_CORPORATE_ACTION_CASH":
            continue
        subtitle = (raw.get("subtitle") or "").strip()
        if subtitle not in bond_subtitles:
            continue
        value = (raw.get("amount") or {}).get("value")
        if value is None:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(tz)
        if ts.year != year:
            continue
        isin = extract_isin_from_icon(raw)
        if not isin:
            continue
        e = by_isin.setdefault(isin, {
            "isin": isin,
            "title": (raw.get("title") or "").strip(),
            "cupones": 0.0, "amortizacion": 0.0, "coste": 0.0,
            "rendimiento_neto": 0.0, "flows": [],
        })
        if subtitle in bond_maturity_subtitles:
            e["amortizacion"] += float(value)
        else:
            e["cupones"] += float(value)
        e["flows"].append({"ts": ts, "subtitle": subtitle, "amount": float(value)})

    # 2) Compras asociadas (cualquier fecha, todo el histórico) por ISIN de bono detectado
    for raw in events:
        if raw.get("status") in excluded_statuses:
            continue
        et = raw.get("eventType")
        if et not in {"TRADE_INVOICE", "TRADING_TRADE_EXECUTED"}:
            continue
        value = (raw.get("amount") or {}).get("value")
        if value is None or value >= 0:
            continue
        d = extract_trade_details(raw)
        isin = d.get("isin") or extract_isin_from_icon(raw)
        if not isin or isin not in by_isin:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(tz)
        by_isin[isin]["coste"] += abs(float(value))
        by_isin[isin]["flows"].append({"ts": ts, "subtitle": "Kauforder", "amount": float(value)})

    # 3) Cerrar: calcular rendimiento neto y ordenar flows cronológicamente
    out = []
    for isin, e in by_isin.items():
        e["rendimiento_neto"] = e["cupones"] + e["amortizacion"] - e["coste"]
        e["flows"].sort(key=lambda x: x["ts"])
        out.append(e)
    out.sort(key=lambda x: x["flows"][0]["ts"] if x["flows"] else datetime.min.replace(tzinfo=tz))
    return out


def _collect_saveback(events, year):
    """Saveback recibido en el año (controvertido fiscalmente: rendimiento en especie)."""
    tz = tr_sync.TIMEZONE
    excluded_statuses = tr_sync.EXCLUDED_STATUSES

    out = []
    for raw in events:
        if raw.get("eventType") != "SAVEBACK_AGGREGATE":
            continue
        if raw.get("status") in excluded_statuses:
            continue
        value = (raw.get("amount") or {}).get("value")
        if value is None:
            continue
        ts_str = raw.get("timestamp")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(tz)
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


def _get_total_position_and_cash(tr):
    """Snapshot completo: instrumentos + cash. Devuelve (items, total_inst, cash_items, cash_total)."""
    positions, cash = asyncio.run(tr_sync.fetch_tr_portfolio_and_cash(tr))
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


# ── Sheet writer ──────────────────────────────────────────────────────────

def write_renta_to_sheet(spreadsheet, year, results, skipped, dividends, interests, bonds,
                         crypto, retentions=None, savebacks=None,
                         portfolio_items=None, portfolio_total=0.0,
                         cash_items=None, cash_total=0.0):
    """Escribe el informe IRPF completo en la pestaña 'Renta YYYY'."""
    log = tr_sync.log
    tz = tr_sync.TIMEZONE
    _es = tr_sync._es

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
    rows.append([f"Generado: {datetime.now(tz=tz).strftime('%Y-%m-%d %H:%M')}"])

    ws.update(values=rows, range_name="A1", value_input_option="USER_ENTERED")
    log.info(f"  pestaña '{sheet_name}' actualizada")


# ── Main entry point ──────────────────────────────────────────────────────

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
    log = tr_sync.log
    tz = tr_sync.TIMEZONE
    _es = tr_sync._es

    if not tr_sync.is_feature_enabled("renta"):
        log.info("Feature 'renta' deshabilitada (config o broker).")
        return
    log.info(f"Conectando a Trade Republic...")
    tr = tr_sync.login()
    events = asyncio.run(tr_sync.fetch_tr_events(tr))
    log.info(f"  {len(events)} eventos descargados (histórico completo)")

    buy_lots, sales, skipped = _build_lots_and_sales(events, year)
    dividends = _collect_dividends(events, year)
    interests = _collect_interest(events, year)
    bonds = _collect_bond_income(events, year)

    # Cálculos (se hacen siempre — son baratos; los toggles solo afectan a la salida).
    results = apply_fifo(buy_lots, sales) if sales else []
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

    sections = tr_sync.RENTA_SECTIONS

    # ── 1. Ganancias/pérdidas ──
    if sections.get("fifo", True):
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
    if sections.get("dividends", True):
        log.info(f"\n[Renta {year}] 2. DIVIDENDOS (casilla 0029)")
        for d in dividends:
            log.info(f"  {d['timestamp'].date()}  {d['title']:<28} {_es(d['subtitle']):<28} "
                     f"bruto={d['gross']:>6.2f}€  ret={d['tax']:>5.2f}€  neto={d['net']:>6.2f}€")
        log.info(f"  → TOTAL: bruto={tot_gross:.2f}€  retención extranjera={tot_tax:.2f}€  neto={tot_net:.2f}€")

    # ── 3. Intereses ──
    if sections.get("interest", True):
        log.info(f"\n[Renta {year}] 3. INTERESES (casilla 0027)")
        for i in interests:
            log.info(f"  {i['timestamp'].date()}  {_es(i['title']):<14} {_es(i['subtitle']):<18} {i['amount']:>7.2f} €")
        log.info(f"  → TOTAL intereses: {tot_int:.2f} €")

    # ── 4. Bonos (extranjeros) / otros activos financieros ──
    if sections.get("bonds", True):
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
    if sections.get("summary_by_box", True):
        log.info(f"\n[Renta {year}] 5. RESUMEN POR CASILLA (contrastar con borrador)")
        log.info(f"  Casilla 0027 (Intereses)           : {tot_int:>8.2f} €")
        log.info(f"  Casilla 0029 (Dividendos neto)     : {tot_net:>8.2f} €")
        log.info(f"    · retención extranjera (ded.DII) : {tot_tax:>8.2f} €")
        log.info(f"  Casilla 0031 (bonos/otros act.fin.): {tot_bond:>8.2f} €")
        log.info(f"  Ganancias/pérdidas patrimoniales   : {total_gain:>+8.2f} €  ({len(results)} ventas)")

    # ── 6. Retenciones extranjeras por país ──
    if sections.get("retentions", True):
        log.info(f"\n[Renta {year}] 6. RETENCIONES EXTRANJERAS POR PAÍS (deducción doble imposición)")
        for country, r in sorted(retentions.items()):
            if r["tax"] > 0 or r["gross"] > 0:
                log.info(f"  {country}: {r['count']} dividendos, bruto={r['gross']:.2f}€, "
                         f"retención={r['tax']:.2f}€, neto={r['net']:.2f}€")
        if not retentions:
            log.info("  (sin dividendos)")

    # ── 7. Saveback ──
    if sections.get("saveback", True):
        log.info(f"\n[Renta {year}] 7. SAVEBACK RECIBIDO (controvertido: rendimiento en especie)")
        log.info(f"  {len(savebacks)} eventos saveback en {year}, total = {tot_sb:.2f} €")
        log.info(f"  TR no lo reporta a Hacienda. Algunos asesores lo declaran como rdto. capital mobiliario en 0029.")

    # ── 8. Snapshot portfolio + cash (necesario para crypto y modelo720) ──
    portfolio_items, portfolio_total = [], 0.0
    cash_items, cash_total = [], 0.0
    crypto = []
    if sections.get("crypto", True) or sections.get("modelo720", True):
        try:
            portfolio_items, portfolio_total, cash_items, cash_total = _get_total_position_and_cash(tr)
        except Exception as e:
            log.warning(f"  no se pudo obtener snapshot de portfolio/cash: {e}")
        for it in portfolio_items:
            if it["isin"] in tr_sync.CRYPTO_ISINS:
                label = next((lbl for isin, lbl in tr_sync.PORTFOLIO_CELL_MAP if isin == it["isin"]), it["isin"])
                crypto.append({"isin": it["isin"], "label": label, "value_eur": it["value_eur"]})
    tot_crypto = sum(c["value_eur"] for c in crypto)

    if sections.get("crypto", True):
        log.info(f"\n[Renta {year}] 8. POSICIÓN CRIPTO (snapshot actual)")
        for c in crypto:
            log.info(f"  {c['label']:<10} ISIN={c['isin']}  {c['value_eur']:>8.2f} €")
        log.info(f"  → TOTAL cripto: {tot_crypto:.2f} €  "
                 f"{'(>50k€ → Modelo 721)' if tot_crypto > 50000 else '(<50k€, sin Modelo 721)'}")

    if sections.get("modelo720", True):
        now_str = datetime.now(tz=tz).strftime("%Y-%m-%d")
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
    spreadsheet = tr_sync.open_spreadsheet()
    write_renta_to_sheet(spreadsheet, year, results, skipped, dividends, interests, bonds,
                         crypto, retentions, savebacks, portfolio_items, portfolio_total,
                         cash_items, cash_total)
