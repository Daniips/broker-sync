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
    from tr_sync import fetch_tr_events

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


async def fetch_snapshot(tr, *, tz: ZoneInfo) -> PortfolioSnapshot:
    """Build a PortfolioSnapshot from TR's compact portfolio + cash.

    Construye un PortfolioSnapshot a partir del portfolio compacto + cash de TR.
    """
    from tr_sync import fetch_tr_portfolio_and_cash

    positions, cash = await fetch_tr_portfolio_and_cash(tr)
    pos_objs: list[Position] = []
    for p in positions:
        net_value = float(p.get("netValue") or 0.0)
        if net_value <= 0:
            continue
        shares = float(p.get("netSize") or 0.0)
        avg_buy = float(p.get("averageBuyIn") or 0.0)
        cost_basis = avg_buy * shares if avg_buy > 0 and shares > 0 else None
        pos_objs.append(
            Position(
                isin=p.get("instrumentId") or "?",
                title=(p.get("name") or p.get("instrumentId") or "?"),
                net_value_eur=net_value,
                broker="tr",
                shares=shares,
                cost_basis_eur=cost_basis,
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
