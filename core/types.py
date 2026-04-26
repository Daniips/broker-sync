"""
Broker-agnostic data model for transactions, positions and portfolio snapshots.
Modelo de datos agnóstico de broker para transacciones, posiciones y snapshots.

Each broker adapter (brokers/*/adapter.py) is responsible for translating its
raw events into these types. core/* modules consume only these types — they
must not import from brokers/*.

Cada adapter de broker (brokers/*/adapter.py) es responsable de traducir sus
eventos raw a estos tipos. Los módulos en core/* solo consumen estos tipos —
no deben importar de brokers/*.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TxKind(str, Enum):
    """Kinds of cash-flow events tracked by metrics.

    Convention for `Transaction.amount_eur` sign:
      BUY        → negative (cash leaves the cash account into a position)
      SELL       → positive (cash enters from a position)
      DIVIDEND   → positive (cash in)
      INTEREST   → positive (cash in)
      DEPOSIT    → positive (external cash in to the broker account)
      WITHDRAWAL → negative (external cash out)
      FEE        → negative (cash out)
      TAX        → negative (cash out)

    Adapters MUST respect this sign convention.
    """
    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    INTEREST = "interest"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    FEE = "fee"
    TAX = "tax"


@dataclass(frozen=True)
class Transaction:
    """One movement in a broker account, normalized.

    `is_bonus=True` marks money/shares received as a broker perk (TR saveback,
    free shares, referral bonuses). Metric functions accept a `bonus_as` flag
    to decide whether to count these as user contributions or as portfolio
    income.
    """
    id: str
    ts: datetime  # tz-aware
    kind: TxKind
    amount_eur: float
    title: str
    broker: str
    isin: Optional[str] = None
    shares: Optional[float] = None
    is_bonus: bool = False


@dataclass(frozen=True)
class Position:
    """Open position at a point in time.

    `cost_basis_eur` is the total amount paid for the shares currently held
    (avg buy price × shares). When None, the broker did not provide it; metrics
    that need cost basis will skip that position.
    """
    isin: str
    title: str
    net_value_eur: float
    broker: str
    shares: float = 0.0
    cost_basis_eur: Optional[float] = None


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Full account state at `ts`: cash + open positions."""
    ts: datetime
    cash_eur: float
    positions: tuple[Position, ...] = field(default_factory=tuple)

    @property
    def positions_value_eur(self) -> float:
        return sum(p.net_value_eur for p in self.positions)

    @property
    def total_eur(self) -> float:
        return self.cash_eur + self.positions_value_eur
