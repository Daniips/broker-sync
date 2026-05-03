"""
Historical reconstruction of PortfolioSnapshots from current state,
transaction history, and per-ISIN historical prices.

Pure logic, no I/O. Historical price fetching lives in the broker's adapter
(brokers/<x>/adapter.py).

# Caveats

- BUYs without `shares` (typically saveback, where the parser does not
  extract the share count) are treated as Δ shares = 0. This means shares
  reconstructed at past dates may be slightly overestimated (we assume those
  shares already existed). For saveback the error is typically a fraction
  of a percent.
- If a position was fully sold and no longer exists in `current_snapshot`,
  its history is not reconstructed (we don't know about it). This introduces
  a small MWR error if its SELLs were significant.
- `cost_basis` is not reconstructed historically (None on each Position) —
  reconstructing it would require tracking averageBuyIn over time.
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
    """For each ISIN currently held, compute shares as of `date`.

    Walks back from `current_snapshot` applying the inverse of each tx with
    ts > date:
      - BUY  → subtract shares (we had fewer before the BUY)
      - SELL → add shares (we had more before the SELL)

    Returns {isin: shares} only for ISINs with shares > 0 at `date` (ISINs
    not yet purchased at that date are excluded).
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
    """Compute the cash balance at `date` walking back from `current_cash`.

    Counts only txs with `from_cash=True` (saveback/gifts didn't move cash).
    By convention, `amount_eur` is already signed (BUY < 0, DEPOSIT > 0...),
    so the inverse is `previous_cash = current_cash − amount_eur`.
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
    """Reconstruct a PortfolioSnapshot at `date`.

    `prices_at_date`: {isin: close_price_eur} at `date`. ISINs without a
    price are excluded (they don't add to positions_value); the caller
    should log those cases.

    Resulting Positions have `cost_basis_eur=None` and `title`/`broker`
    inherited from the current position with the same ISIN.
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
