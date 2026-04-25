"""
Parsers for raw Trade Republic events.
Parsers de eventos brutos de Trade Republic.

These extractors operate on the JSON returned by the TR API (`amount`,
`details.sections`, `icon` fields) and are independent from the global config.
Used to build buy/sell lots for the FIFO engine.

Estos extractores trabajan sobre el JSON que devuelve la API de TR (`amount`,
`details.sections`, `icon`) y son independientes del config global. Se usan
para construir lotes de compra/venta para el motor FIFO.
"""
from __future__ import annotations

import re

from core.utils import parse_de_number


# ISIN regex: 2 letters + 9 alphanumeric + 1 digit (ISO 6166).
# ISIN regex: 2 letras + 9 alfanuméricos + 1 dígito (ISO 6166).
_ISIN_RE = re.compile(r"logos/([A-Z]{2}[A-Z0-9]{9}\d)/v")


def extract_isin_from_icon(raw):
    """
    Look up the ISIN in icon / avatar.asset / details.sections[0].data.icon.

    For gifts (GIFTING_RECIPIENT_ACTIVITY) the root `icon` is the generic
    'timeline_gift'; the actual ISIN lives inside `details.sections[0].data.icon`.

    Busca el ISIN en icon / avatar.asset / details.sections[0].data.icon.
    Para regalos el root `icon` es genérico ('timeline_gift') y el ISIN real
    vive dentro de `details.sections[0].data.icon`.
    """
    candidates = [raw.get("icon"), (raw.get("avatar") or {}).get("asset")]
    sections = ((raw.get("details") or {}).get("sections") or [])
    if sections:
        header = sections[0]
        if header.get("type") == "header":
            hd = header.get("data") or {}
            icon = hd.get("icon")
            if isinstance(icon, str):
                candidates.append(icon)
            elif isinstance(icon, dict):
                candidates.append(icon.get("asset"))
    for field in candidates:
        if not field:
            continue
        m = _ISIN_RE.search(field)
        if m:
            return m.group(1)
    return None


def extract_trade_details(raw):
    """
    Extract {isin, shares, unit_price} from the details.sections of a TR event.

    Supports two formats:
      A) TRADING_TRADE_EXECUTED / SAVINGSPLAN / SAVEBACK: row "Transaktion" inside
         the "Übersicht" table, with shares in displayValue.prefix ('1,035444 × ').
      B) TRADE_INVOICE (older buys/sells): own section title="Transaktion" with
         rows 'Anteile' / 'Aktien' and 'Aktienkurs'.

    Extrae {isin, shares, unit_price} del bloque details.sections.
    Soporta dos formatos:
      A) TRADING_TRADE_EXECUTED / SAVINGSPLAN / SAVEBACK: fila "Transaktion"
         dentro de la tabla "Übersicht", con shares en displayValue.prefix.
      B) TRADE_INVOICE (compras/ventas antiguas): sección propia title="Transaktion"
         con filas 'Anteile' / 'Aktien' y 'Aktienkurs'.
    """
    details = raw.get("details") or {}
    sections = details.get("sections") or []

    isin = None
    for s in sections:
        if s.get("type") == "header":
            action = s.get("action") or {}
            if action.get("type") == "instrumentDetail":
                isin = action.get("payload")
            break

    shares = unit_price = None

    # Format A
    # Formato A
    for s in sections:
        if s.get("type") != "table":
            continue
        rows = s.get("data") or []
        if not isinstance(rows, list):
            continue
        for r in rows:
            if not isinstance(r, dict) or r.get("title") != "Transaktion":
                continue
            detail = r.get("detail")
            if not isinstance(detail, dict):
                continue
            dv = detail.get("displayValue") or {}
            if isinstance(dv, dict):
                prefix = (dv.get("prefix") or "").strip()
                m = re.match(r"^([\d.,]+)\s*×", prefix)
                if m:
                    shares = parse_de_number(m.group(1))
                unit_price = parse_de_number(dv.get("text")) or unit_price
            if unit_price is None:
                unit_price = parse_de_number(detail.get("text"))
            break
        if shares is not None:
            break

    # Format B (fallback)
    # Formato B (fallback)
    if shares is None:
        for s in sections:
            if s.get("type") != "table" or s.get("title") != "Transaktion":
                continue
            rows = s.get("data") or []
            if not isinstance(rows, list):
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                title = r.get("title")
                detail = r.get("detail")
                text = detail.get("text") if isinstance(detail, dict) else None
                if title in ("Anteile", "Aktien", "Stücke", "Nominale"):
                    shares = parse_de_number(text)
                elif title == "Aktienkurs":
                    unit_price = parse_de_number(text)
            break

    return {"isin": isin, "shares": shares, "unit_price": unit_price}


def extract_gift_details(raw):
    """
    Extract {isin, shares, cost_eur} from GIFTING_RECIPIENT_ACTIVITY / LOTTERY_PRIZE.

    The fiscal cost (market value at reception) lives in Transaktion→Summe.
    Falls back to shares × Aktienkurs from the Übersicht block if missing.

    Extrae {isin, shares, cost_eur} de GIFTING_RECIPIENT_ACTIVITY / LOTTERY_PRIZE.
    El coste fiscal (valor al recibir) está en Transaktion→Summe. Si no está,
    cae a shares × Aktienkurs del bloque Übersicht.
    """
    details = raw.get("details") or {}
    sections = details.get("sections") or []

    isin = extract_isin_from_icon(raw)
    if not isin:
        for s in sections:
            if s.get("type") == "header":
                action = s.get("action") or {}
                if action.get("type") == "instrumentDetail":
                    isin = action.get("payload")
                break

    shares = unit_price = total_cost = None

    for s in sections:
        if s.get("type") != "table":
            continue
        title = s.get("title")
        rows = s.get("data") or []
        if not isinstance(rows, list):
            continue
        if title == "Übersicht":
            for r in rows:
                if not isinstance(r, dict):
                    continue
                rt = r.get("title")
                detail = r.get("detail")
                text = detail.get("text") if isinstance(detail, dict) else None
                if rt == "Aktien":
                    shares = parse_de_number(text)
                elif rt == "Aktienkurs":
                    unit_price = parse_de_number(text)
        elif title == "Transaktion":
            for r in rows:
                if not isinstance(r, dict):
                    continue
                if r.get("title") in ("Summe", "Gesamt"):
                    detail = r.get("detail")
                    text = detail.get("text") if isinstance(detail, dict) else None
                    parsed = parse_de_number(text)
                    if parsed is not None:
                        total_cost = parsed

    if total_cost is None and shares is not None and unit_price is not None:
        total_cost = shares * unit_price

    return {"isin": isin, "shares": shares, "cost_eur": total_cost}


def extract_dividend_details(raw):
    """
    Extract {isin, gross, tax, net, subtitle} from a SSP_CORPORATE_ACTION_CASH event.

    The 'Geschäft' table has rows 'Bruttoertrag' (gross), 'Steuer' (withholding)
    and 'Gesamt' (net).

    Extrae {isin, gross, tax, net, subtitle} de SSP_CORPORATE_ACTION_CASH.
    La tabla 'Geschäft' tiene filas 'Bruttoertrag' (bruto), 'Steuer' (retención)
    y 'Gesamt' (neto).
    """
    isin = extract_isin_from_icon(raw)
    if not isin:
        sections = ((raw.get("details") or {}).get("sections") or [])
        for s in sections:
            if s.get("type") == "header":
                action = s.get("action") or {}
                if action.get("type") == "instrumentDetail":
                    isin = action.get("payload")
                break

    gross = tax = net = None
    sections = ((raw.get("details") or {}).get("sections") or [])
    for s in sections:
        if s.get("type") != "table" or s.get("title") != "Geschäft":
            continue
        for r in s.get("data") or []:
            if not isinstance(r, dict):
                continue
            title = r.get("title")
            detail = r.get("detail")
            text = detail.get("text") if isinstance(detail, dict) else None
            if title == "Bruttoertrag":
                gross = parse_de_number(text)
            elif title == "Steuer":
                tax = parse_de_number(text)
            elif title == "Gesamt":
                net = parse_de_number(text)
        break

    # Fallback: if Gesamt isn't found in Geschäft, use amount.value.
    # Fallback: si no encontramos Gesamt en Geschäft, usar amount.value.
    if net is None:
        net = (raw.get("amount") or {}).get("value")
    if gross is None and net is not None and tax is not None:
        gross = net + tax
    if gross is None and net is not None:
        gross = net

    return {
        "isin": isin,
        "gross": gross,
        "tax": tax or 0.0,
        "net": net,
        "subtitle": (raw.get("subtitle") or "").strip(),
    }
