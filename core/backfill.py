"""
Reconstrucción de PortfolioSnapshots históricos a partir del estado actual,
las transacciones, y precios históricos por ISIN.

Historical reconstruction of PortfolioSnapshots from current state,
transaction history, and per-ISIN historical prices.

Lógica pura, sin I/O. La obtención de precios históricos vive en el adapter
del broker (brokers/<x>/adapter.py).

Pure logic, no I/O. Historical price fetching lives in the broker's adapter.

# Caveats

- BUYs sin `shares` (típicamente saveback, donde el parser no extrae el
  número de acciones) se tratan como Δ shares = 0. Implica que las shares
  reconstruidas a fechas pasadas pueden estar ligeramente sobreestimadas
  (asumimos que esas shares ya existían). Para saveback el error es típicamente
  fracciones de %.
- Si una posición fue vendida totalmente y ya no existe en `current_snapshot`,
  no se reconstruye su histórico (no la conocemos). Para MWR esto introduce un
  pequeño error si los SELLs de esa posición fueron significativos.
- `cost_basis` no se reconstruye en histórico (None en cada Position) — para
  reconstruirlo necesitaríamos tracking de averageBuyIn a lo largo del tiempo.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from core.types import PortfolioSnapshot, Position, Transaction, TxKind


def shares_at(
    date: datetime,
    current_snapshot: PortfolioSnapshot,
    txs: list[Transaction],
) -> dict[str, float]:
    """Para cada ISIN actualmente en cartera, calcula shares en `date`.

    Retrocede desde `current_snapshot` aplicando la inversa de cada tx con ts > date:
      - BUY  → restamos shares (teníamos menos antes del BUY)
      - SELL → sumamos shares (teníamos más antes del SELL)

    Devuelve {isin: shares} solo para ISINs con shares > 0 en `date` (los
    ISINs que aún no se habían comprado en esa fecha se excluyen).
    """
    out: dict[str, float] = {}
    for p in current_snapshot.positions:
        if p.isin:
            out[p.isin] = p.shares or 0.0

    for tx in txs:
        if tx.ts <= date:
            continue
        if not tx.isin or tx.shares is None or tx.shares <= 0:
            continue
        if tx.kind == TxKind.BUY:
            out[tx.isin] = out.get(tx.isin, 0.0) - tx.shares
        elif tx.kind == TxKind.SELL:
            out[tx.isin] = out.get(tx.isin, 0.0) + tx.shares

    return {isin: n for isin, n in out.items() if n > 1e-9}


def cash_at(
    date: datetime,
    current_cash: float,
    txs: list[Transaction],
) -> float:
    """Calcula el balance de cash en `date` retrocediendo desde `current_cash`.

    Solo cuenta tx con `from_cash=True` (saveback/regalos no movieron cash).
    Por convención, `amount_eur` ya viene firmado (BUY < 0, DEPOSIT > 0...),
    así que la inversa es `cash_anterior = cash_actual − amount_eur`.
    """
    cash = current_cash
    for tx in txs:
        if tx.ts <= date:
            continue
        if not tx.from_cash:
            continue
        cash -= tx.amount_eur
    return cash


def reconstruct_snapshot_at(
    date: datetime,
    current_snapshot: PortfolioSnapshot,
    txs: list[Transaction],
    prices_at_date: dict[str, float],
) -> PortfolioSnapshot:
    """Reconstruye un PortfolioSnapshot en `date`.

    `prices_at_date`: {isin: precio_close_eur} en `date`. ISINs sin precio se
    excluyen (no aportan a positions_value); el caller debe loggear esos casos.

    Las Positions resultantes tienen `cost_basis_eur=None` y `title`/`broker`
    heredados de la posición actual del mismo ISIN.
    """
    shares_per_isin = shares_at(date, current_snapshot, txs)
    cash = cash_at(date, current_snapshot.cash_eur, txs)

    title_by_isin = {p.isin: p.title for p in current_snapshot.positions if p.isin}
    broker_by_isin = {p.isin: p.broker for p in current_snapshot.positions if p.isin}

    positions = []
    for isin, n_shares in shares_per_isin.items():
        price = prices_at_date.get(isin)
        if price is None or price <= 0:
            continue
        positions.append(Position(
            isin=isin,
            title=title_by_isin.get(isin, isin),
            net_value_eur=n_shares * price,
            broker=broker_by_isin.get(isin, "unknown"),
            shares=n_shares,
            cost_basis_eur=None,
        ))

    return PortfolioSnapshot(
        ts=date,
        cash_eur=cash,
        positions=tuple(positions),
    )
