"""
Broker-agnostic financial metrics.
Métricas financieras agnósticas de broker.

Pure functions over core.types.Transaction[] and core.types.PortfolioSnapshot.
No I/O, no broker imports, no Sheet writes.

Funciones puras sobre core.types.Transaction[] y core.types.PortfolioSnapshot.
Sin I/O, sin imports de broker, sin escritura a Sheet.

# Conceptual model / Modelo conceptual

The "portfolio" is defined as the set of invested positions (not the cash
account). Therefore:
  - BUY  = external outflow into the portfolio (your money in)
  - SELL = external inflow back out of the portfolio (your money out)
  - DIVIDEND, INTEREST, DEPOSIT, WITHDRAWAL, FEE, TAX → not used by these
    metrics. They affect the broker cash account, not the invested portfolio.

The user's TR cash balance is shown via PortfolioSnapshot.cash_eur but does
NOT enter return calculations.

# Bonus handling

Transactions with `is_bonus=True` (saveback, gifts) are BUYs but the user did
not pay for them. The `bonus_as` parameter controls treatment:
  - "income"  (default): bonus BUYs are ignored from contributions and from
    MWR cash flows. Their share value silently appears in the snapshot. MWR
    rises (free money). Aligns with "what's the return on MY savings effort?"
  - "deposit": bonus BUYs are treated like normal contributions. They count
    in total_invested and as outflows in MWR. Aligns with "what's the return
    on every euro that ended up invested, regardless of source?"
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Literal, Optional

from core.types import PortfolioSnapshot, Transaction, TxKind


BonusAs = Literal["income", "deposit"]


def _is_invested_buy(tx: Transaction, *, bonus_as: BonusAs) -> bool:
    if tx.kind != TxKind.BUY:
        return False
    if tx.is_bonus and bonus_as == "income":
        return False
    return True


def _in_window(ts: datetime, start: Optional[datetime], end: Optional[datetime]) -> bool:
    if start is not None and ts < start:
        return False
    if end is not None and ts > end:
        return False
    return True


def total_wealth(snapshot: PortfolioSnapshot) -> float:
    """Cash + valor de posiciones."""
    return snapshot.total_eur


def positions_value(snapshot: PortfolioSnapshot) -> float:
    """Solo el valor de las posiciones (excluye cash)."""
    return snapshot.positions_value_eur


def cost_basis_of_current_holdings(
    txs: list[Transaction],
    *,
    bonus_at_zero_cost: bool = True,
) -> dict[str, float]:
    """Cost basis por ISIN de las shares ACTUALMENTE en cartera, vía FIFO.

    Empareja SELLs contra los BUYs más antiguos del mismo ISIN; lo que queda
    sin emparejar son las shares vivas y su coste agregado es el cost basis
    "real" de lo que aún tienes.

    Diferencia clave vs `Position.cost_basis_eur` (que viene de
    `averageBuyIn × netSize` del broker):
      - `bonus_at_zero_cost=True` (default): saveback y regalos contribuyen
        0€ al cost basis. Equivale a "dinero que TÚ pusiste de tu bolsillo
        en estas shares" — matchea el "Invertido" de un Excel manual.
      - `bonus_at_zero_cost=False`: cuenta saveback/regalos a su precio de
        mercado en el momento de entrega — equivale a la fórmula del broker.

    Devuelve {isin: cost_basis_eur} solo de ISINs con cost basis > 0.
    Ignora BUYs/SELLs sin shares conocidos (no se pueden emparejar por FIFO).
    """
    buys_by_isin: dict[str, list[dict]] = defaultdict(list)
    sells_by_isin: dict[str, list[dict]] = defaultdict(list)

    for tx in sorted(txs, key=lambda t: t.ts):
        if not tx.isin or tx.shares is None or tx.shares <= 0:
            continue
        if tx.kind == TxKind.BUY:
            cost = 0.0 if (tx.is_bonus and bonus_at_zero_cost) else abs(tx.amount_eur)
            unit_cost = cost / tx.shares
            buys_by_isin[tx.isin].append({
                "shares_remaining": tx.shares,
                "unit_cost": unit_cost,
            })
        elif tx.kind == TxKind.SELL:
            sells_by_isin[tx.isin].append({"shares": tx.shares})

    out: dict[str, float] = {}
    for isin, lots in buys_by_isin.items():
        for sell in sells_by_isin.get(isin, []):
            remaining = sell["shares"]
            for lot in lots:
                if remaining <= 1e-12:
                    break
                if lot["shares_remaining"] <= 1e-12:
                    continue
                take = min(lot["shares_remaining"], remaining)
                lot["shares_remaining"] -= take
                remaining -= take
        cb = sum(lot["shares_remaining"] * lot["unit_cost"] for lot in lots)
        if cb > 1e-9:
            out[isin] = cb
    return out


def saveback_per_held_isin(
    snapshot: PortfolioSnapshot,
    txs: list[Transaction],
) -> dict[str, float]:
    """Saveback acumulado por ISIN, restringido a posiciones aún en cartera.

    Solo cuenta `is_bonus=True` (saveback genuino — perks tipo regalo de TR
    no entran porque tienen cost basis real, ver adapter).

    Aproximación: si has vendido parcialmente un ISIN con saveback, el número
    sale "conservador" (resta la totalidad del saveback aunque parte se haya
    vendido). En la práctica el usuario no suele vender saveback parcial, así
    que el sesgo es despreciable; cuando guardemos snapshots históricos
    podemos refinar con FIFO de saveback shares específicamente.
    """
    held_isins = {p.isin for p in snapshot.positions if p.isin}
    out: dict[str, float] = defaultdict(float)
    for tx in txs:
        if tx.kind != TxKind.BUY or not tx.is_bonus or tx.isin not in held_isins:
            continue
        out[tx.isin] += abs(tx.amount_eur)
    return dict(out)


def cost_basis_user_paid_per_isin(
    snapshot: PortfolioSnapshot,
    txs: list[Transaction],
) -> dict[str, float]:
    """Cost basis "dinero propio" por ISIN: averageBuyIn×shares del broker
    menos el saveback recibido para ese ISIN.

    Matchea el cálculo típico de un Excel manual:
        Invertido = TR_cost_basis − saveback_recibido

    Razón: TR cuenta saveback shares a precio de mercado en su averageBuyIn,
    inflando el cost basis con dinero que TR te dio gratis. Al restar el
    saveback acumulado, te queda lo que realmente saliste de tu cuenta cash.

    Posiciones sin `cost_basis_eur` (broker no lo devolvió) se omiten.
    """
    saveback = saveback_per_held_isin(snapshot, txs)
    out: dict[str, float] = {}
    for p in snapshot.positions:
        if not p.cost_basis_eur or p.cost_basis_eur <= 0:
            continue
        cb = p.cost_basis_eur - saveback.get(p.isin, 0.0)
        if cb > 0:
            out[p.isin] = cb
    return out


def unrealized_return_user_paid(
    snapshot: PortfolioSnapshot,
    txs: list[Transaction],
) -> Optional[dict]:
    """Plusvalía latente sobre el dinero propio (saveback descontado).

    Usa `cost_basis_user_paid_per_isin`. Matchea el % "Ganancia" típico de
    un Excel manual donde el saveback es "dinero gratis del broker" que no
    infla el cost basis.
    """
    cb_per_isin = cost_basis_user_paid_per_isin(snapshot, txs)
    if not cb_per_isin:
        return None
    total_cb = sum(cb_per_isin.values())
    if total_cb <= 0:
        return None
    held_isins = set(cb_per_isin.keys())
    value = sum(p.net_value_eur for p in snapshot.positions if p.isin in held_isins)
    pnl = value - total_cb
    return {
        "value": value,
        "cost_basis": total_cb,
        "pnl_eur": pnl,
        "pnl_pct": pnl / total_cb,
        "isins_matched": len(cb_per_isin),
    }


def per_position_attribution(
    snapshot: PortfolioSnapshot,
    txs: list[Transaction],
    *,
    bonus_as: BonusAs = "income",
) -> list[dict]:
    """MWR per posición + contribución ponderada al rendimiento de la cartera.

    Para cada posición actualmente en cartera:
      - Filtra los flujos del ISIN: BUYs (negativos), SELLs / DIVIDENDs (positivos).
      - Añade el valor actual como flujo final positivo.
      - Calcula XIRR sobre esa serie → `position_mwr`.

    `contribution_pp` = `position_mwr × value_pct × 100` (puntos porcentuales
    sobre la cartera). La suma de contribuciones aproxima el MWR de las
    posiciones vivas (NO el MWR all-time del portfolio, que incluye también
    flujos de posiciones ya vendidas).

    Devuelve `[{isin, title, value, value_pct, position_mwr, contribution_pp}, ...]`
    ordenado por abs(contribution_pp) descendente. Posiciones cuyo XIRR no
    converge (típico: holding muy corto, pocos flujos) se omiten.
    """
    total_value = snapshot.positions_value_eur
    if total_value <= 0:
        return []

    by_isin: dict[str, list[Transaction]] = defaultdict(list)
    for tx in txs:
        if tx.isin:
            by_isin[tx.isin].append(tx)

    out = []
    for p in snapshot.positions:
        if not p.isin or p.net_value_eur <= 0:
            continue
        position_txs = by_isin.get(p.isin, [])
        flows: list[tuple[datetime, float]] = []
        for tx in position_txs:
            if tx.kind == TxKind.BUY:
                if tx.is_bonus and bonus_as == "income":
                    continue
                flows.append((tx.ts, -abs(tx.amount_eur)))
            elif tx.kind == TxKind.SELL:
                flows.append((tx.ts, abs(tx.amount_eur)))
            elif tx.kind == TxKind.DIVIDEND:
                flows.append((tx.ts, abs(tx.amount_eur)))
        if not flows:
            continue
        flows.append((snapshot.ts, p.net_value_eur))
        position_mwr = xirr(flows)
        if position_mwr is None:
            continue
        value_pct = p.net_value_eur / total_value
        contribution_pp = position_mwr * value_pct * 100.0
        out.append({
            "isin": p.isin,
            "title": p.title,
            "value": p.net_value_eur,
            "value_pct": value_pct,
            "position_mwr": position_mwr,
            "contribution_pp": contribution_pp,
        })
    out.sort(key=lambda x: -abs(x["contribution_pp"]))
    return out


def benchmark_return(
    price_history: list[dict],
    start_ts: datetime,
    end_ts: Optional[datetime] = None,
) -> Optional[dict]:
    """Rentabilidad anualizada de un benchmark entre dos fechas.

    `price_history`: lista de bars `{ts: datetime, close: float}` ordenada
    ascendentemente por ts (lo que devuelve `fetch_price_history`).
    `start_ts`: fecha inicial (usa el bar más cercano y anterior).
    `end_ts`: fecha final (default = último bar disponible).

    Devuelve `{start_price, end_price, start_ts, end_ts, total_return,
    annualized_return, days}` o None si no hay datos suficientes.

    `total_return = end_price/start_price − 1` (acumulado).
    `annualized_return = (1+total)^(365.25/days) − 1` (anualizado, comparable
    contra MWR anualizado).

    Asume que el benchmark es un ETF Acc (acumula dividendos en el precio). Si
    fuera Dist, los dividendos cobrados quedarían fuera y la cifra sería
    conservadora.
    """
    if not price_history:
        return None

    # Bar de inicio: el más reciente con ts ≤ start_ts. Si no hay ninguno
    # anterior, usamos el primero (caso "start_ts es anterior al inicio del
    # histórico" — devolvemos el rendimiento desde que existe data).
    start_candidates = [b for b in price_history if b["ts"] <= start_ts]
    start_bar = start_candidates[-1] if start_candidates else price_history[0]

    # Bar de fin
    if end_ts is None:
        end_bar = price_history[-1]
    else:
        end_candidates = [b for b in price_history if b["ts"] <= end_ts]
        if not end_candidates:
            return None
        end_bar = end_candidates[-1]

    if start_bar["close"] <= 0 or end_bar["close"] <= 0:
        return None
    days = (end_bar["ts"] - start_bar["ts"]).days
    if days <= 0:
        return None

    total = end_bar["close"] / start_bar["close"] - 1.0
    try:
        annualized = (1.0 + total) ** (365.25 / days) - 1.0
    except (ValueError, OverflowError):
        return None

    return {
        "start_price": start_bar["close"],
        "end_price": end_bar["close"],
        "start_ts": start_bar["ts"],
        "end_ts": end_bar["ts"],
        "total_return": total,
        "annualized_return": annualized,
        "days": days,
    }


def currency_exposure(
    snapshot: PortfolioSnapshot,
    currency_map: dict[str, str],
    *,
    cash_currency: str = "EUR",
    include_cash: bool = True,
    unknown_label: str = "UNKNOWN",
) -> list[dict]:
    """Distribución del patrimonio por divisa de denominación.

    `currency_map`: ISIN → divisa (`"USD"`, `"EUR"`, etc.). ISINs sin entrada
    se agrupan bajo `unknown_label`.
    `cash_currency`: divisa del cash account. Default EUR.
    `include_cash`: si True (default), añade cash al bucket de `cash_currency`.

    Devuelve `[{currency, value_eur, pct, n_positions}, ...]` ordenado desc por
    value_eur. `pct` es sobre `total_eur` cuando se incluye cash, sobre
    `positions_value_eur` cuando no.
    """
    by_currency: dict[str, float] = defaultdict(float)
    n_positions: dict[str, int] = defaultdict(int)
    for p in snapshot.positions:
        if p.net_value_eur <= 0:
            continue
        cur = currency_map.get(p.isin or "", unknown_label)
        by_currency[cur] += p.net_value_eur
        n_positions[cur] += 1

    if include_cash and snapshot.cash_eur > 0:
        by_currency[cash_currency] += snapshot.cash_eur
        # No bumpeamos n_positions: cash no es una posición.

    denom = snapshot.total_eur if include_cash else snapshot.positions_value_eur
    if denom <= 0:
        return []

    out = [
        {
            "currency": cur,
            "value_eur": value,
            "pct": value / denom,
            "n_positions": n_positions.get(cur, 0),
        }
        for cur, value in by_currency.items()
    ]
    out.sort(key=lambda x: -x["value_eur"])
    return out


def concentration(
    snapshot: PortfolioSnapshot,
    *,
    scope: Literal["positions", "total"] = "positions",
    limits: Optional[dict[str, float]] = None,
    default_threshold: Optional[float] = None,
) -> list[dict]:
    """Distribución de valor entre posiciones, ordenada de más a menos peso.

    `scope="positions"`: % sobre el valor de las posiciones (excluye cash).
    `scope="total"`: % sobre el patrimonio total (cash + posiciones).

    `limits`: dict ISIN → límite (0-1) por posición. Una entrada permite definir
        un máximo razonable distinto para cada activo (p.ej. SP500 50%, cripto
        8%). ISINs sin entrada caen al `default_threshold`.
    `default_threshold`: límite a aplicar a ISINs no presentes en `limits`.
        Si es None, las posiciones sin límite explícito devuelven `limit=None`
        y `exceeded=False` (no se aplica ninguna alerta).

    Devuelve `[{isin, title, value, pct, limit, margin_pp, exceeded}, ...]`
    sorted desc por pct, donde:
      - `limit`: límite efectivo (0-1) o None si no hay ninguno aplicable.
      - `margin_pp`: (limit - pct) × 100 en puntos porcentuales. Positivo = bajo
        el límite. Negativo = excedido. None si no hay límite.
      - `exceeded`: True si pct > limit (>= con tolerancia 1e-9 para evitar
        falsos positivos por aritmética flotante).

    Lista vacía si no hay posiciones (o denominador es 0).
    """
    if scope == "total":
        denom = snapshot.total_eur
    else:
        denom = snapshot.positions_value_eur
    if denom <= 0:
        return []
    limits = limits or {}
    out = []
    for p in snapshot.positions:
        pct = p.net_value_eur / denom
        limit = limits.get(p.isin) if p.isin else None
        if limit is None:
            limit = default_threshold
        if limit is not None:
            margin_pp = (limit - pct) * 100.0
            exceeded = pct > limit + 1e-9
        else:
            margin_pp = None
            exceeded = False
        out.append({
            "isin": p.isin,
            "title": p.title,
            "value": p.net_value_eur,
            "pct": pct,
            "limit": limit,
            "margin_pp": margin_pp,
            "exceeded": exceeded,
        })
    out.sort(key=lambda x: -x["pct"])
    return out


def cost_basis_total(snapshot: PortfolioSnapshot) -> Optional[float]:
    """Coste total de adquisición de las posiciones actuales (precio medio × shares).

    Devuelve None si ninguna posición tiene cost_basis_eur disponible.
    Solo suma posiciones con cost_basis_eur conocido — si el broker no lo da
    para alguna posición, esa se excluye del total (se avisa por el caller).
    """
    total = 0.0
    found = False
    for p in snapshot.positions:
        if p.cost_basis_eur is None or p.cost_basis_eur <= 0:
            continue
        total += p.cost_basis_eur
        found = True
    return total if found else None


def unrealized_return(
    snapshot: PortfolioSnapshot,
    txs: Optional[list[Transaction]] = None,
) -> Optional[dict]:
    """Plusvalía latente sobre las posiciones ACTUALES.

    Devuelve dos lecturas:
      - pnl_pct (sin dividendos): (positions_value - cost_basis) / cost_basis.
        Plusvalía pura por revalorización.
      - pnl_pct_total (con dividendos): suma los dividendos cobrados de los
        ISINs que tienes vivos. Matchea normalmente el % "Rendimiento" de la
        app de TR. Solo se calcula si pasas `txs`.

    Útil para "¿cómo van mis posiciones vivas?". No incluye intereses, cash,
    ventas pasadas ni saveback/regalos como concepto separado (saveback que
    sigue como acciones cuenta dentro del cost basis y value como cualquier
    otra compra).

    Devuelve None si no hay cost_basis disponible.
    """
    cost_basis = cost_basis_total(snapshot)
    if cost_basis is None or cost_basis <= 0:
        return None
    held_isins = {
        p.isin for p in snapshot.positions
        if p.cost_basis_eur is not None and p.cost_basis_eur > 0 and p.isin
    }
    value = sum(
        p.net_value_eur for p in snapshot.positions
        if p.cost_basis_eur is not None and p.cost_basis_eur > 0
    )
    pnl = value - cost_basis

    dividends_held = None
    pnl_total = None
    pnl_pct_total = None
    if txs is not None:
        dividends_held = sum(
            abs(tx.amount_eur) for tx in txs
            if tx.kind == TxKind.DIVIDEND and tx.isin in held_isins
        )
        pnl_total = pnl + dividends_held
        pnl_pct_total = pnl_total / cost_basis

    n_with = sum(1 for p in snapshot.positions if p.cost_basis_eur is not None and p.cost_basis_eur > 0)
    return {
        "value": value,
        "cost_basis": cost_basis,
        "pnl_eur": pnl,
        "pnl_pct": pnl / cost_basis,
        "dividends_held": dividends_held,
        "pnl_eur_total": pnl_total,
        "pnl_pct_total": pnl_pct_total,
        "positions_with_cost": n_with,
        "positions_total": len(snapshot.positions),
    }


def total_invested(
    txs: list[Transaction],
    *,
    bonus_as: BonusAs = "income",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> float:
    """Aportación neta a posiciones en el periodo: BUYs − SELLs.

    Con `bonus_as='income'` (default) los BUYs con `is_bonus=True` no cuentan.
    """
    total = 0.0
    for tx in txs:
        if not _in_window(tx.ts, start, end):
            continue
        if _is_invested_buy(tx, bonus_as=bonus_as):
            total += abs(tx.amount_eur)
        elif tx.kind == TxKind.SELL:
            total -= abs(tx.amount_eur)
    return total


def simple_return(
    txs: list[Transaction],
    snapshot: PortfolioSnapshot,
    *,
    bonus_as: BonusAs = "income",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Optional[float]:
    """% simple = (positions_value - total_invested) / total_invested.

    No anualizado. Devuelve None si no hay aportaciones netas positivas.
    """
    invested = total_invested(txs, bonus_as=bonus_as, start=start, end=end)
    if invested <= 0:
        return None
    return (snapshot.positions_value_eur - invested) / invested


def xirr(cashflows: list[tuple[datetime, float]], *, guess: float = 0.1) -> Optional[float]:
    """XIRR (annualized IRR over irregular dates).

    `cashflows`: list of (datetime, amount_eur). By convention here:
      negative = cash leaving your pocket / entering the portfolio
      positive = cash returning to your pocket / leaving the portfolio
    The final portfolio value should be added as a positive flow at the
    valuation date.

    Returns the annualized rate as a decimal (e.g. 0.12 = +12% annual) or
    None if Newton-Raphson does not converge or input is degenerate.
    """
    if not cashflows or len(cashflows) < 2:
        return None
    flows = sorted(cashflows, key=lambda x: x[0])
    t0 = flows[0][0]
    has_pos = any(a > 0 for _, a in flows)
    has_neg = any(a < 0 for _, a in flows)
    if not (has_pos and has_neg):
        return None

    years_offsets = [(ts - t0).total_seconds() / (365.25 * 86400) for ts, _ in flows]
    amounts = [a for _, a in flows]

    def npv(rate: float) -> float:
        s = 0.0
        for amt, yrs in zip(amounts, years_offsets):
            s += amt / (1.0 + rate) ** yrs
        return s

    def dnpv(rate: float) -> float:
        s = 0.0
        for amt, yrs in zip(amounts, years_offsets):
            s += -yrs * amt / (1.0 + rate) ** (yrs + 1.0)
        return s

    rate = guess
    for _ in range(200):
        try:
            f = npv(rate)
        except (OverflowError, ZeroDivisionError):
            return None
        if abs(f) < 1e-9:
            return rate
        try:
            df = dnpv(rate)
        except (OverflowError, ZeroDivisionError):
            return None
        if df == 0:
            return None
        new_rate = rate - f / df
        # Keep rate above -1 (1+rate must be positive for real-valued powers).
        if new_rate <= -0.9999999:
            new_rate = (rate - 0.9999999) / 2.0
        if abs(new_rate - rate) < 1e-10:
            return new_rate
        rate = new_rate
    return None


def mwr(
    txs: list[Transaction],
    snapshot: PortfolioSnapshot,
    *,
    bonus_as: BonusAs = "income",
    start: Optional[datetime] = None,
    start_value: Optional[float] = None,
    end: Optional[datetime] = None,
) -> Optional[float]:
    """Money-weighted return (XIRR) anualizado.

    Flujos considerados (todos durante (start, end] si se acota):
      - BUY               → outflow (negativo) — sale dinero del bolsillo al portfolio
      - SELL              → inflow  (positivo) — entra dinero del portfolio al bolsillo
      - DIVIDEND          → inflow  (positivo) — cobro recibido del portfolio
      - Snapshot final    → inflow positivo (positions_value_eur) en `end`/`snapshot.ts`

    INTEREST queda fuera (es rendimiento del cash, no del portfolio invertido).

    Para sub-periodos (start ≠ None):
      - Necesitas pasar `start_value` (valor de las posiciones al inicio del
        periodo), que se modela como "deposit sintético" en `start`.
      - Sin `start_value`, devuelve None (el cálculo daría números absurdos
        al ignorar el valor inicial — no mentimos).

    Para all-time (start=None) el cálculo es exacto sin parámetros extra.
    """
    if start is not None and start_value is None:
        # Honest-fail: sub-period MWR requires the portfolio value at `start`.
        # We don't persist historical snapshots yet, so the caller can't supply
        # this. Returning None instead of computing a wrong number.
        return None

    flows: list[tuple[datetime, float]] = []
    if start is not None and start_value is not None and start_value > 0:
        flows.append((start, -float(start_value)))

    for tx in txs:
        if not _in_window(tx.ts, start, end):
            continue
        if _is_invested_buy(tx, bonus_as=bonus_as):
            flows.append((tx.ts, -abs(tx.amount_eur)))
        elif tx.kind == TxKind.SELL:
            flows.append((tx.ts, abs(tx.amount_eur)))
        elif tx.kind == TxKind.DIVIDEND:
            flows.append((tx.ts, abs(tx.amount_eur)))
    if not flows:
        return None
    final_ts = end or snapshot.ts
    flows.append((final_ts, snapshot.positions_value_eur))
    return xirr(flows)


def monthly_contributions(
    txs: list[Transaction],
    *,
    include_bonus: bool = True,
    include_sells: bool = False,
) -> dict[tuple[int, int], float]:
    """{(year, month): suma de compras del mes}.

    Defaults pensados para matchear el cálculo intuitivo de "cuánto invertí
    este mes" (igual que la pestaña "Dinero invertido" del Sheet):

      - include_bonus=True: saveback y regalos cuentan como aportación.
        El bróker te lo dio, pero acabó como acciones en tu cartera.
      - include_sells=False: solo BUYs, no resta SELLs. Una venta es una
        operación aparte; no "des-invierte" el concepto de aportación
        mensual (si vendiste 50€ de oro, sigues habiendo invertido lo que
        invertiste).

    Cambia los flags si quieres "aportación neta del periodo" (con sells)
    o "solo dinero propio" (sin saveback/regalos).
    """
    out: dict[tuple[int, int], float] = defaultdict(float)
    for tx in txs:
        key = (tx.ts.year, tx.ts.month)
        if tx.kind == TxKind.BUY:
            if tx.is_bonus and not include_bonus:
                continue
            out[key] += abs(tx.amount_eur)
        elif tx.kind == TxKind.SELL and include_sells:
            out[key] -= abs(tx.amount_eur)
    return dict(out)


def contribution_vs_average(
    txs: list[Transaction],
    ref_year: int,
    ref_month: int,
    *,
    include_bonus: bool = True,
    include_sells: bool = False,
    window_months: int = 12,
) -> Optional[dict]:
    """Compara la aportación de (ref_year, ref_month) vs media de los
    `window_months` meses anteriores (no incluye el mes de referencia).

    Solo entran en la media los meses con aportación > 0 (para no diluir
    con meses en los que no aportaste nada).

    Devuelve {this_month, avg, delta_pct, window_months_used} o None si no
    hay meses anteriores con aportaciones.
    """
    monthly = monthly_contributions(txs, include_bonus=include_bonus, include_sells=include_sells)
    this_month = monthly.get((ref_year, ref_month), 0.0)

    prior: list[float] = []
    y, m = ref_year, ref_month
    for _ in range(window_months):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        prior.append(monthly.get((y, m), 0.0))

    nonzero = [v for v in prior if v > 0]
    if not nonzero:
        return None
    avg = sum(nonzero) / len(nonzero)
    delta_pct = (this_month - avg) / avg if avg > 0 else None
    return {
        "this_month": this_month,
        "avg": avg,
        "delta_pct": delta_pct,
        "window_months_used": len(nonzero),
    }


def monthly_deposits(
    txs: list[Transaction],
    *,
    net_of_withdrawals: bool = True,
) -> dict[tuple[int, int], float]:
    """{(year, month): net external cash entering the broker account}.

    Sums DEPOSITs (incoming transfers, including self-transfers from the
    user's own bank account) and subtracts WITHDRAWALs when
    `net_of_withdrawals=True`. Doesn't touch BUYs/SELLs/dividends —
    those are *internal* moves of the broker cash, not external savings.

    Useful as a proxy for the user's savings rate flowing into the broker
    (≈ how much of their salary ends up at TR each month, regardless of
    whether it gets invested or stays as cash).
    """
    out: dict[tuple[int, int], float] = defaultdict(float)
    for tx in txs:
        key = (tx.ts.year, tx.ts.month)
        if tx.kind == TxKind.DEPOSIT:
            out[key] += abs(tx.amount_eur)
        elif tx.kind == TxKind.WITHDRAWAL and net_of_withdrawals:
            out[key] -= abs(tx.amount_eur)
    return dict(out)


def savings_projection(
    current_cash: float,
    current_positions: float,
    monthly_contribution: float,
    monthly_deposit: float,
    annual_return: float,
    targets: list[float],
    *,
    now: datetime,
    max_months: int = 1200,
) -> dict:
    """Compound projection: ETA to reach each target.

    Pure numeric function. Caller decides policy (which months to average,
    whether to use empirical MWR or a fallback rate, etc.).

    Model — each simulated month:
      positions ← positions × (1 + r/12) + monthly_contribution
      cash      ← cash + (monthly_deposit − monthly_contribution)

    Rationale: `monthly_deposit` is total external cash flowing into the
    broker (e.g. salary transferred from the user's own bank account).
    `monthly_contribution` is the part of that cash that gets invested
    each month (BUYs). The leftover stays in cash. Both add toward the
    target; only positions compound.

    Cash can go negative if `monthly_contribution > monthly_deposit` —
    interpret as the user investing from existing reserves. Not capped.

    Stops simulation at `max_months` (default 1200 ≈ 100 years).

    Returns: current, current_cash, current_positions, monthly_contribution,
    monthly_deposit, monthly_cash_flow, annual_return, targets[].
    """
    r_monthly = (1 + annual_return) ** (1 / 12) - 1 if annual_return > -1 else 0.0
    cash_flow = monthly_deposit - monthly_contribution
    current_total = current_cash + current_positions

    targets_out = []
    for t in sorted(targets):
        remaining = t - current_total
        if remaining <= 0:
            targets_out.append({
                "target": t, "remaining": 0.0, "months": 0.0,
                "eta": now, "status": "reached",
            })
            continue

        pos = current_positions
        cash = current_cash
        months = 0
        while pos + cash < t and months < max_months:
            pos = pos * (1 + r_monthly) + monthly_contribution
            cash = cash + cash_flow
            months += 1

        if months >= max_months:
            targets_out.append({
                "target": t, "remaining": remaining, "months": None,
                "eta": None, "status": "non_reachable",
            })
            continue

        # Refine with linear interpolation across the last simulated month
        # so the ETA isn't over-rounded.
        prev_total = (pos - monthly_contribution) / (1 + r_monthly) + (cash - cash_flow) if months > 0 else current_total
        if months > 0 and pos + cash > t:
            frac = (t - prev_total) / ((pos + cash) - prev_total) if (pos + cash) > prev_total else 1.0
            n = (months - 1) + max(0.0, min(1.0, frac))
        else:
            n = float(months)
        eta = now + timedelta(days=n * 30.4375)
        targets_out.append({
            "target": t, "remaining": remaining, "months": n,
            "eta": eta, "status": "projected",
        })

    return {
        "current": current_total,
        "current_cash": current_cash,
        "current_positions": current_positions,
        "monthly_contribution": monthly_contribution,
        "monthly_deposit": monthly_deposit,
        "monthly_cash_flow": cash_flow,
        "annual_return": annual_return,
        "targets": targets_out,
    }


# ── Risk / efficiency metrics ────────────────────────────────────────────
#
# All built on top of monthly Modified Dietz returns derived from the
# snapshot history. With dense snapshots they converge to true TWR-derived
# numbers; with sparse history they're noisy approximations gated by the
# caller (display layer decides whether to show).


def _resample_monthly(snapshots: list[dict]) -> list[dict]:
    """Latest snapshot per calendar month, sorted ascending."""
    by_month: dict[tuple[int, int], dict] = {}
    for s in sorted(snapshots, key=lambda x: x["ts"]):
        by_month[(s["ts"].year, s["ts"].month)] = s
    return [by_month[k] for k in sorted(by_month.keys())]


def _modified_dietz_return(
    s_begin: dict,
    s_end: dict,
    txs: list[Transaction],
    *,
    bonus_as: BonusAs = "income",
) -> Optional[float]:
    """Modified Dietz return for the period (s_begin.ts, s_end.ts] on the
    invested portfolio. Flows: BUY (inflow), SELL (outflow). Saveback per
    `bonus_as`. Dividends ignored (assumes accumulating ETFs)."""
    V0 = s_begin["positions_value_eur"]
    V1 = s_end["positions_value_eur"]
    period_s = (s_end["ts"] - s_begin["ts"]).total_seconds()
    if period_s <= 0 or V0 <= 0:
        return None
    net_flow = 0.0
    weighted = 0.0
    for tx in txs:
        if not (s_begin["ts"] < tx.ts <= s_end["ts"]):
            continue
        if _is_invested_buy(tx, bonus_as=bonus_as):
            f = abs(tx.amount_eur)
        elif tx.kind == TxKind.SELL:
            f = -abs(tx.amount_eur)
        else:
            continue
        net_flow += f
        # Fraction of period remaining after the flow.
        weighted += ((s_end["ts"] - tx.ts).total_seconds() / period_s) * f
    denom = V0 + weighted
    if denom <= 0:
        return None
    return (V1 - V0 - net_flow) / denom


def monthly_returns(
    txs: list[Transaction],
    snapshot_history: list[dict],
    snapshot: PortfolioSnapshot,
    *,
    bonus_as: BonusAs = "income",
) -> list[tuple[datetime, float]]:
    """Series of (month_end_ts, sub-period return). One entry per pair of
    consecutive monthly snapshots. Useful for vol / Sharpe / alpha."""
    history = list(snapshot_history) + [{
        "ts": snapshot.ts,
        "cash_eur": snapshot.cash_eur,
        "positions_value_eur": snapshot.positions_value_eur,
        "total_eur": snapshot.total_eur,
    }]
    monthly_snaps = _resample_monthly(history)
    out: list[tuple[datetime, float]] = []
    for i in range(1, len(monthly_snaps)):
        r = _modified_dietz_return(
            monthly_snaps[i - 1], monthly_snaps[i], txs, bonus_as=bonus_as,
        )
        if r is not None:
            out.append((monthly_snaps[i]["ts"], r))
    return out


def twr(
    txs: list[Transaction],
    snapshot_history: list[dict],
    snapshot: PortfolioSnapshot,
    *,
    bonus_as: BonusAs = "income",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Optional[float]:
    """Time-Weighted Return (annualized). Geometric link of monthly Modified
    Dietz sub-period returns. Strips the timing-of-flows effect that MWR
    embeds — the right metric to compare against a benchmark."""
    series = monthly_returns(txs, snapshot_history, snapshot, bonus_as=bonus_as)
    if start is not None:
        series = [(ts, r) for ts, r in series if ts > start]
    if end is not None:
        series = [(ts, r) for ts, r in series if ts <= end]
    if len(series) < 1:
        return None
    growth = 1.0
    for _, r in series:
        growth *= (1 + r)
    # Annualize using elapsed time of the included sub-periods.
    first_ts = series[0][0]
    last_ts = series[-1][0]
    # Subtract one month from first to get the period start.
    days_total = (last_ts - first_ts).days + 30  # +30 ≈ first sub-period's span
    if days_total <= 0 or growth <= 0:
        return None
    return growth ** (365.25 / days_total) - 1


def benchmark_monthly_returns(
    price_history: list[dict],
) -> list[tuple[datetime, float]]:
    """Monthly returns of a benchmark from its price bars (uses the latest
    close per calendar month)."""
    if not price_history:
        return []
    by_month: dict[tuple[int, int], tuple[datetime, float]] = {}
    for bar in price_history:
        key = (bar["ts"].year, bar["ts"].month)
        if key not in by_month or bar["ts"] > by_month[key][0]:
            by_month[key] = (bar["ts"], bar["close"])
    sorted_pairs = [by_month[k] for k in sorted(by_month.keys())]
    out: list[tuple[datetime, float]] = []
    for i in range(1, len(sorted_pairs)):
        prev_ts, prev_p = sorted_pairs[i - 1]
        cur_ts, cur_p = sorted_pairs[i]
        if prev_p > 0:
            out.append((cur_ts, (cur_p - prev_p) / prev_p))
    return out


def max_drawdown(
    monthly_return_series: list[tuple[datetime, float]],
) -> Optional[dict]:
    """Worst peak-to-trough drawdown of the portfolio's wealth index
    (cumulative product of monthly returns). Strips contribution effect:
    a portfolio that's down 5 % but masked by deposits still shows −5 %.

    Returns {max_dd_pct, peak_ts, trough_ts, recovery_ts, days_to_trough,
    days_to_recovery} or None if no drawdown.
    """
    if len(monthly_return_series) < 2:
        return None
    # Build wealth index starting at 1.0 at the first return's date.
    wealth: list[tuple[datetime, float]] = []
    w = 1.0
    for ts, r in monthly_return_series:
        w *= (1 + r)
        wealth.append((ts, w))
    running_peak = wealth[0][1]
    running_peak_ts = wealth[0][0]
    max_dd = 0.0
    out_peak_ts = running_peak_ts
    out_trough_ts = running_peak_ts
    for ts, v in wealth:
        if v > running_peak:
            running_peak = v
            running_peak_ts = ts
        if running_peak > 0:
            dd = (running_peak - v) / running_peak
            if dd > max_dd:
                max_dd = dd
                out_peak_ts = running_peak_ts
                out_trough_ts = ts
    if max_dd <= 0:
        return None
    # Find the wealth-index value at peak and check recovery after trough.
    peak_w = next(v for ts, v in wealth if ts == out_peak_ts)
    recovery_ts = None
    for ts, v in wealth:
        if ts > out_trough_ts and v >= peak_w:
            recovery_ts = ts
            break
    return {
        "max_dd_pct": max_dd,
        "peak_ts": out_peak_ts,
        "trough_ts": out_trough_ts,
        "recovery_ts": recovery_ts,
        "days_to_trough": (out_trough_ts - out_peak_ts).days,
        "days_to_recovery": (recovery_ts - out_trough_ts).days if recovery_ts else None,
    }


def savings_ratio(
    monthly_contribs: dict[tuple[int, int], float],
    monthly_deps: dict[tuple[int, int], float],
    *,
    now: datetime,
    months_window: int = 6,
) -> Optional[dict]:
    """% of net deposits that gets invested vs accumulated as cash.

    Looks at the last `months_window` completed months. None if there were
    no positive deposits in the window.
    """
    contribs: list[float] = []
    deps: list[float] = []
    y, m = now.year, now.month
    for _ in range(months_window):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        contribs.append(monthly_contribs.get((y, m), 0.0))
        deps.append(monthly_deps.get((y, m), 0.0))
    total_c = sum(contribs)
    total_d = sum(deps)
    if total_d <= 0:
        return None
    return {
        "invested": total_c,
        "deposited": total_d,
        "cash_pile": total_d - total_c,
        "ratio": total_c / total_d,
        "months_used": len(contribs),
    }


def _stdev(xs: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return var ** 0.5


def volatility_annualized(
    monthly_return_series: list[tuple[datetime, float]],
) -> Optional[float]:
    """Annualized volatility from monthly returns (σ × √12)."""
    rets = [r for _, r in monthly_return_series]
    s = _stdev(rets)
    if s is None:
        return None
    return s * (12 ** 0.5)


def sharpe_ratio(
    annualized_return: Optional[float],
    annualized_vol: Optional[float],
    *,
    risk_free: float = 0.02,
) -> Optional[float]:
    """(annualized_return − risk_free) / annualized_vol. None if vol≤0."""
    if annualized_return is None or annualized_vol is None or annualized_vol <= 0:
        return None
    return (annualized_return - risk_free) / annualized_vol


def tracking_error_annualized(
    portfolio_monthly_returns: list[tuple[datetime, float]],
    benchmark_monthly_returns_: list[tuple[datetime, float]],
) -> Optional[dict]:
    """σ of (portfolio_return − benchmark_return) per month, annualized.

    Returns {tracking_error, avg_active_return_annual, n_months} or None
    if fewer than 2 overlapping months.
    """
    bench_by_month = {(ts.year, ts.month): r for ts, r in benchmark_monthly_returns_}
    diffs: list[float] = []
    for ts, port_r in portfolio_monthly_returns:
        b = bench_by_month.get((ts.year, ts.month))
        if b is not None:
            diffs.append(port_r - b)
    if len(diffs) < 2:
        return None
    s = _stdev(diffs)
    if s is None:
        return None
    return {
        "tracking_error": s * (12 ** 0.5),
        "avg_active_return_annual": (sum(diffs) / len(diffs)) * 12,
        "n_months": len(diffs),
    }


def alpha_beta(
    portfolio_monthly_returns: list[tuple[datetime, float]],
    benchmark_monthly_returns_: list[tuple[datetime, float]],
    *,
    min_months: int = 6,
) -> Optional[dict]:
    """OLS regression of portfolio monthly returns vs benchmark.
    β = cov(p, b) / var(b);  α_monthly = mean(p) − β × mean(b).
    Annualized α reported. Needs ≥`min_months` overlapping months.
    """
    bench_by_month = {(ts.year, ts.month): r for ts, r in benchmark_monthly_returns_}
    pairs: list[tuple[float, float]] = []
    for ts, p in portfolio_monthly_returns:
        b = bench_by_month.get((ts.year, ts.month))
        if b is not None:
            pairs.append((p, b))
    if len(pairs) < min_months:
        return None
    n = len(pairs)
    mp = sum(p for p, _ in pairs) / n
    mb = sum(b for _, b in pairs) / n
    cov = sum((p - mp) * (b - mb) for p, b in pairs) / (n - 1)
    var_b = sum((b - mb) ** 2 for _, b in pairs) / (n - 1)
    # Guard against numerically-degenerate benchmarks (e.g. flat series).
    if var_b <= 1e-10:
        return None
    beta = cov / var_b
    alpha_monthly = mp - beta * mb
    return {
        "alpha_annual": alpha_monthly * 12,
        "beta": beta,
        "n_months": n,
    }
