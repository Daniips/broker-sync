"""
Trade Republic adapter: translates TR raw events / portfolio into the
broker-agnostic types defined in core.types.

Adapter de Trade Republic: traduce los eventos brutos y el portfolio de TR
a los tipos agnósticos definidos en core.types.

Public surface / Superficie pública:
  - raw_event_to_tx(raw, *, tz)          → Transaction | None  (pure)
  - fetch_transactions(tr, *, tz, since) → list[Transaction]   (async, I/O)
  - fetch_snapshot(tr, *, tz)            → PortfolioSnapshot   (async, I/O)
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from core.types import PortfolioSnapshot, Position, Transaction, TxKind


# Subtitles (alemán) para clasificar SSP_CORPORATE_ACTION_CASH. Si TR introduce
# subtitles nuevos, ampliar aquí; el código del usuario en config.yaml ya los
# permite extender para el flujo de Renta — aquí los mantenemos en mínimo
# operativo para métricas (dividend / bond_cash unificados como cash inflow).
_DIVIDEND_SUBTITLES = {"Bardividende", "Aktienprämiendividende", "Kapitalertrag"}
_BOND_CASH_SUBTITLES = {"Zinszahlung", "Kupon", "Endgültige Fälligkeit"}

_EXCLUDED_STATUSES = {"CANCELED", "CANCELLED", "FAILED", "REJECTED", "PENDING"}


def _parse_ts(raw, tz: ZoneInfo) -> Optional[datetime]:
    ts_str = raw.get("timestamp")
    if not ts_str:
        return None
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(tz)


def raw_event_to_tx(
    raw: dict,
    *,
    tz: ZoneInfo,
    gift_overrides: Optional[dict] = None,
) -> Optional[Transaction]:
    """Convert a TR raw event into a Transaction. Returns None if the event
    is not relevant for portfolio metrics (cancelled, malformed, or a kind
    we deliberately ignore).

    `gift_overrides` is the same dict accepted by the legacy renta logic:
    `{ISIN: {shares, cost_eur}}`. Used to patch GIFTING events whose details
    section TR does not return parseable.

    Convierte un evento bruto de TR en una Transaction. Devuelve None si el
    evento no es relevante (cancelado, mal formado, o un tipo que ignoramos).
    """
    if raw.get("status") in _EXCLUDED_STATUSES:
        return None

    et = raw.get("eventType")
    tx_id = raw.get("id")
    ts = _parse_ts(raw, tz)
    if not tx_id or not ts:
        return None

    title = (raw.get("title") or "").strip()
    subtitle = (raw.get("subtitle") or "").strip()
    value = (raw.get("amount") or {}).get("value")

    # Lazy import to keep this module importable without the TR parser stack.
    from brokers.tr.parser import (
        extract_dividend_details,
        extract_gift_details,
        extract_isin_from_icon,
        extract_trade_details,
    )

    kind: Optional[TxKind] = None
    isin: Optional[str] = None
    shares: Optional[float] = None
    amount_eur: Optional[float] = None
    is_bonus = False
    from_cash = True

    if et in {"TRADING_TRADE_EXECUTED", "TRADING_SAVINGSPLAN_EXECUTED", "TRADE_INVOICE"}:
        if value is None:
            return None
        d = extract_trade_details(raw)
        isin = d.get("isin")
        shares = d.get("shares")
        if value < 0:
            kind = TxKind.BUY
            amount_eur = float(value)  # negative
        elif value > 0:
            kind = TxKind.SELL
            amount_eur = float(value)  # positive
        else:
            return None

    elif et == "SAVEBACK_AGGREGATE":
        if value is None:
            return None
        d = extract_trade_details(raw)
        isin = d.get("isin")
        shares = d.get("shares")
        kind = TxKind.BUY
        amount_eur = -abs(float(value))
        is_bonus = True
        from_cash = False  # saveback shares are TR-funded, not user cash

    elif et in {"GIFTING_RECIPIENT_ACTIVITY", "GIFTING_LOTTERY_PRIZE_ACTIVITY"}:
        g = extract_gift_details(raw)
        isin = g.get("isin")
        shares = g.get("shares")
        cost = g.get("cost_eur")
        # Apply manual overrides for gifts whose details TR did not return parseable.
        if gift_overrides and isin in gift_overrides:
            ov = gift_overrides[isin]
            shares = ov.get("shares", shares)
            cost = ov.get("cost_eur", cost)
        if not isin or not shares or shares <= 0 or not cost or cost <= 0:
            return None
        kind = TxKind.BUY
        amount_eur = -abs(float(cost))
        # Gifts have a real cost basis (delivery price), unlike saveback. The
        # user's "Invertido" Excel and TR's averageBuyIn both count gift shares
        # at delivery price, so we treat them as normal BUYs (is_bonus=False).
        # Saveback (delivered against your spending) is the only case where the
        # share is genuinely "free money" and gets is_bonus=True.
        is_bonus = False
        from_cash = False  # gifts are TR-funded (no cash deducted from user)

    elif et == "SSP_CORPORATE_ACTION_CASH":
        if value is None:
            return None
        if subtitle in _DIVIDEND_SUBTITLES:
            kind = TxKind.DIVIDEND
            d = extract_dividend_details(raw)
            isin = d.get("isin")
            amount_eur = float(value)
        elif subtitle in _BOND_CASH_SUBTITLES:
            # Cupones y amortización los tratamos como cash-in tipo dividendo
            # para flujo de caja. Para Renta el flujo IRPF se computa aparte.
            kind = TxKind.DIVIDEND
            isin = extract_isin_from_icon(raw)
            amount_eur = float(value)
        else:
            return None

    elif et in {"INTEREST_PAYOUT", "INTEREST_PAYOUT_CREATED"}:
        if value is None:
            return None
        kind = TxKind.INTEREST
        amount_eur = float(value)

    elif et in {"BANK_TRANSACTION_INCOMING", "PAYMENT_BIZUM_C2C_INCOMING"}:
        if value is None:
            return None
        kind = TxKind.DEPOSIT
        amount_eur = float(value)

    elif et in {"BANK_TRANSACTION_OUTGOING", "PAYMENT_BIZUM_C2C_OUTGOING", "CARD_TRANSACTION"}:
        if value is None:
            return None
        kind = TxKind.WITHDRAWAL
        amount_eur = float(value)

    else:
        return None

    if kind is None or amount_eur is None:
        return None

    return Transaction(
        id=str(tx_id),
        ts=ts,
        kind=kind,
        amount_eur=amount_eur,
        title=title or subtitle or et,
        broker="tr",
        isin=isin,
        shares=shares,
        is_bonus=is_bonus,
        from_cash=from_cash,
    )


async def fetch_transactions(
    tr,
    *,
    tz: ZoneInfo,
    since: Optional[datetime] = None,
    gift_overrides: Optional[dict] = None,
) -> list[Transaction]:
    """Download TR timeline and convert relevant events to Transactions.
    Sorted ascending by `ts`. `gift_overrides` is forwarded to raw_event_to_tx.
    """
    from brokers.tr.sync_io import fetch_tr_events

    not_before = since.timestamp() if since else 0.0
    raw_events = await fetch_tr_events(tr, not_before_ts=not_before)
    txs: list[Transaction] = []
    seen_ids: set[str] = set()
    for raw in raw_events:
        tx = raw_event_to_tx(raw, tz=tz, gift_overrides=gift_overrides)
        if tx is None:
            continue
        if tx.id in seen_ids:
            continue
        seen_ids.add(tx.id)
        txs.append(tx)
    txs.sort(key=lambda t: t.ts)
    return txs


async def fetch_price_history(
    tr,
    isin: str,
    *,
    range_str: str = "1y",
    exchange: str = "LSX",
    topic: str = "aggregateHistoryLight",
    timeout: float = 10.0,
    debug: bool = False,
) -> list[dict]:
    """Fetch historical OHLC bars for an ISIN via TR's WebSocket subscription.

    `topic`: TR subscription type. "aggregateHistoryLight" (default) is the
    current name; "aggregateHistory" was deprecated. Try "aggregateHistory" or
    "chartHistory" as fallbacks if needed.
    `range_str`: "1d" / "5d" / "1m" / "3m" / "1y" / "5y" / "max".
    `exchange`: TR market identifier; default "LSX" (Lang & Schwarz).

    Returns list of {ts: datetime (UTC), close: float} sorted by ts ascending.
    Returns [] on error and logs the actual cause.
    """
    import asyncio as _asyncio
    import logging
    from datetime import timezone

    log = logging.getLogger("tr_sync")

    sub_id = None
    try:
        # Bypass pytr's `performance_history` helper because it hardcodes
        # "aggregateHistory" which TR has deprecated. Subscribe with our topic.
        sub_id = await tr.subscribe({
            "type": topic,
            "id": f"{isin}.{exchange}",
            "range": range_str,
        })
        result = await _asyncio.wait_for(tr._recv_subscription(sub_id), timeout=timeout)
    except _asyncio.TimeoutError:
        log.warning(f"   ⏱  timeout esperando {topic} para {isin}.{exchange}")
        return []
    except Exception as e:
        log.warning(f"   ⚠  error en {topic} para {isin}.{exchange}: {type(e).__name__}: {e}")
        return []
    finally:
        if sub_id is not None:
            try:
                await tr.unsubscribe(sub_id)
            except Exception:
                pass

    if debug:
        keys = list(result.keys()) if isinstance(result, dict) else type(result).__name__
        log.info(f"   [debug] respuesta {isin}: keys={keys}")

    if not isinstance(result, dict):
        log.warning(f"   ⚠  respuesta no-dict para {isin}: {type(result).__name__}")
        return []
    aggs = result.get("aggregates")
    if aggs is None:
        aggs = result.get("data") or []
    if not isinstance(aggs, list):
        log.warning(f"   ⚠  no encontré lista de bars en respuesta para {isin}: keys={list(result.keys())}")
        return []

    out = []
    for bar in aggs:
        if not isinstance(bar, dict):
            continue
        time_val = bar.get("time") or bar.get("timestamp") or bar.get("t")
        close = bar.get("close") if "close" in bar else bar.get("c")
        if time_val is None or close is None:
            continue
        try:
            ts_sec = float(time_val) / 1000.0 if float(time_val) > 1e11 else float(time_val)
            ts = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
            out.append({"ts": ts, "close": float(close)})
        except (ValueError, TypeError, OSError):
            continue
    out.sort(key=lambda x: x["ts"])
    if debug:
        log.info(f"   [debug] {isin}: {len(out)} barras parseadas")
    return out


async def fetch_instrument_exchanges(
    tr,
    isin: str,
    *,
    timeout: float = 5.0,
    debug: bool = False,
) -> list[str]:
    """Query `instrument_details(isin)` and parse out the available exchange IDs.

    Returns a list of exchange identifiers (e.g. ["LSX", "BTLX", "BSF"]) that
    TR considers valid for this ISIN. Empty list on any error or unknown shape.

    Used as last-resort fallback in `fetch_price_history_with_fallback` for
    instruments where the hardcoded list ("LSX", "BTLX", "BSF") doesn't match
    — typical for crypto ISINs where the actual exchange is broker-specific
    and changes between regions.
    """
    import asyncio as _asyncio
    import logging

    log = logging.getLogger("tr_sync")
    sub_id = None
    try:
        sub_id = await tr.instrument_details(isin)
        result = await _asyncio.wait_for(tr._recv_subscription(sub_id), timeout=timeout)
    except Exception as e:
        log.debug(f"instrument_details({isin}) failed: {type(e).__name__}: {e}")
        return []
    finally:
        if sub_id is not None:
            try:
                await tr.unsubscribe(sub_id)
            except Exception:
                pass

    if debug and isinstance(result, dict):
        log.info(f"   [debug] instrument_details({isin}) keys: {list(result.keys())}")

    if not isinstance(result, dict):
        log.warning(f"   ⚠  instrument_details({isin}) devolvió {type(result).__name__}, no dict")
        return []

    # TR returns a structure like {"exchanges": [{"slug": "LSX", ...}, ...]} or
    # similar. Try the common variants defensively.
    candidates = (
        result.get("exchanges")
        or result.get("exchangeIds")
        or result.get("availableExchanges")
        or []
    )
    out: list[str] = []
    for entry in candidates:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict):
            ex_id = (
                entry.get("slug")
                or entry.get("id")
                or entry.get("exchangeId")
                or entry.get("name")
            )
            if ex_id and isinstance(ex_id, str):
                out.append(ex_id)
    # Always log what we found — only fires when fallback is needed, so noise is bounded.
    log.info(f"   discovered exchanges for {isin}: {out or '(none)'}")
    return out


async def fetch_price_history_with_fallback(
    tr,
    isin: str,
    *,
    range_str: str = "1y",
    exchanges: Optional[list[str]] = None,
    timeout: float = 5.0,
    debug: bool = False,
) -> tuple[list[dict], Optional[str]]:
    """Try several exchanges in order until one returns data.

    Order:
      1. The exchanges hint passed by the caller (typically from snapshot's
         `exchange_id` and a hardcoded list of common venues).
      2. If all fail, query `instrument_details(isin)` to discover actual
         exchanges TR knows about, and retry with those.

    Returns (history, exchange_used). If everything fails, returns ([], None).
    """
    import logging
    log = logging.getLogger("tr_sync")
    if not exchanges:
        exchanges = ["LSX"]
    tried = set()
    for exch in exchanges:
        tried.add(exch)
        history = await fetch_price_history(
            tr, isin,
            range_str=range_str,
            exchange=exch,
            timeout=timeout,
            debug=debug,
        )
        if history:
            if exch != exchanges[0]:
                log.info(f"   ✓  {isin} encontrado en exchange '{exch}' (fallback)")
            return history, exch

    # Last resort: ask TR what exchanges it actually has for this ISIN.
    discovered = await fetch_instrument_exchanges(tr, isin, timeout=timeout, debug=debug)
    new_exchanges = [e for e in discovered if e not in tried]
    if new_exchanges:
        log.info(f"   → {isin}: probando exchanges descubiertos: {new_exchanges}")
        for exch in new_exchanges:
            history = await fetch_price_history(
                tr, isin,
                range_str=range_str,
                exchange=exch,
                timeout=timeout,
                debug=False,
            )
            if history:
                log.info(f"   ✓  {isin} encontrado en exchange '{exch}' (descubierto via instrument_details)")
                return history, exch
    return [], None


def price_at(history: list[dict], target: datetime) -> Optional[float]:
    """Return close price of the latest bar with ts <= target. None if no bar fits."""
    if not history:
        return None
    candidates = [b for b in history if b["ts"] <= target]
    if not candidates:
        return None
    return candidates[-1]["close"]


async def fetch_snapshot(tr, *, tz: ZoneInfo) -> PortfolioSnapshot:
    """Build a PortfolioSnapshot from TR's compact portfolio + cash.

    Construye un PortfolioSnapshot a partir del portfolio compacto + cash de TR.
    """
    from brokers.tr.sync_io import fetch_tr_portfolio_and_cash

    positions, cash = await fetch_tr_portfolio_and_cash(tr)
    pos_objs: list[Position] = []
    for p in positions:
        net_value = float(p.get("netValue") or 0.0)
        if net_value <= 0:
            continue
        shares = float(p.get("netSize") or 0.0)
        avg_buy = float(p.get("averageBuyIn") or 0.0)
        cost_basis = avg_buy * shares if avg_buy > 0 and shares > 0 else None
        # TR may expose the trading venue under several names. Take the first
        # non-empty one we recognize. If a list, pick the first entry.
        exch = p.get("exchangeId") or p.get("exchange") or p.get("tradingVenue")
        if not exch:
            exch_list = p.get("exchangeIds") or p.get("availableExchanges")
            if isinstance(exch_list, list) and exch_list:
                exch = exch_list[0] if isinstance(exch_list[0], str) else exch_list[0].get("id") if isinstance(exch_list[0], dict) else None
        pos_objs.append(
            Position(
                isin=p.get("instrumentId") or "?",
                title=(p.get("name") or p.get("instrumentId") or "?"),
                net_value_eur=net_value,
                broker="tr",
                shares=shares,
                cost_basis_eur=cost_basis,
                exchange_id=exch if isinstance(exch, str) else None,
            )
        )
    cash_eur = 0.0
    for c in cash or []:
        if c.get("currencyId") == "EUR":
            cash_eur += float(c.get("amount") or 0.0)
    return PortfolioSnapshot(
        ts=datetime.now(tz=tz),
        cash_eur=cash_eur,
        positions=tuple(pos_objs),
    )
