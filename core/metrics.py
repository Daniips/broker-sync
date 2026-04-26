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


def concentration(
    snapshot: PortfolioSnapshot,
    *,
    scope: Literal["positions", "total"] = "positions",
) -> list[dict]:
    """Distribución de valor entre posiciones, ordenada de más a menos peso.

    `scope="positions"`: % sobre el valor de las posiciones (excluye cash).
    `scope="total"`: % sobre el patrimonio total (cash + posiciones).

    Devuelve [{isin, title, value, pct}, ...] sorted desc por pct.
    Lista vacía si no hay posiciones (o denominador es 0).
    """
    if scope == "total":
        denom = snapshot.total_eur
    else:
        denom = snapshot.positions_value_eur
    if denom <= 0:
        return []
    out = [
        {
            "isin": p.isin,
            "title": p.title,
            "value": p.net_value_eur,
            "pct": p.net_value_eur / denom,
        }
        for p in snapshot.positions
    ]
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
