"""Unit tests for core/backfill.py — pure synthetic data."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from core.backfill import cash_at, reconstruct_snapshot_at, shares_at
from core.types import PortfolioSnapshot, Position, Transaction, TxKind


TZ = ZoneInfo("Europe/Madrid")


def t(year, month, day=1):
    return datetime(year, month, day, 12, tzinfo=TZ)


def buy(when, amount, *, isin="X1", shares=None, is_bonus=False, from_cash=True):
    return Transaction(
        id=f"buy-{when.isoformat()}-{amount}",
        ts=when,
        kind=TxKind.BUY,
        amount_eur=-abs(amount),
        title="buy",
        broker="t",
        isin=isin,
        shares=shares,
        is_bonus=is_bonus,
        from_cash=from_cash,
    )


def sell(when, amount, *, isin="X1", shares=None):
    return Transaction(
        id=f"sell-{when.isoformat()}-{amount}",
        ts=when,
        kind=TxKind.SELL,
        amount_eur=abs(amount),
        title="sell",
        broker="t",
        isin=isin,
        shares=shares,
    )


def deposit(when, amount):
    return Transaction(
        id=f"dep-{when.isoformat()}-{amount}",
        ts=when,
        kind=TxKind.DEPOSIT,
        amount_eur=abs(amount),
        title="deposit",
        broker="t",
    )


def make_snap(ts, *, cash, positions):
    pos = tuple(
        Position(isin=p["isin"], title=p["isin"], net_value_eur=p["value"],
                 broker="t", shares=p.get("shares", 0))
        for p in positions
    )
    return PortfolioSnapshot(ts=ts, cash_eur=cash, positions=pos)


class SharesAtTests(unittest.TestCase):
    def test_walks_backward_buys(self):
        # Today: 10 shares. BUY of 6 in March, BUY of 4 in Jan. At Feb 1: 4 shares.
        txs = [buy(t(2025, 1, 15), 100, shares=4), buy(t(2025, 3, 15), 200, shares=6)]
        snap = make_snap(t(2025, 4, 1), cash=0, positions=[{"isin": "X1", "value": 1000, "shares": 10}])
        result = shares_at(t(2025, 2, 1), snap, txs)
        self.assertAlmostEqual(result["X1"], 4.0)

    def test_handles_sells(self):
        # Today: 5 shares. BUY 10 in Jan, SELL 5 in Mar. At Feb 1: 10 shares.
        txs = [buy(t(2025, 1, 1), 100, shares=10), sell(t(2025, 3, 1), 50, shares=5)]
        snap = make_snap(t(2025, 4, 1), cash=0, positions=[{"isin": "X1", "value": 500, "shares": 5}])
        result = shares_at(t(2025, 2, 1), snap, txs)
        self.assertAlmostEqual(result["X1"], 10.0)

    def test_excludes_isin_not_yet_bought(self):
        # BUY in March. At Feb (before BUY) shares = 0 → excluded.
        txs = [buy(t(2025, 3, 1), 100, shares=10)]
        snap = make_snap(t(2025, 4, 1), cash=0, positions=[{"isin": "X1", "value": 1000, "shares": 10}])
        result = shares_at(t(2025, 2, 1), snap, txs)
        self.assertNotIn("X1", result)

    def test_skips_buys_without_shares(self):
        # Saveback BUY without shares → contributes 0 to share delta.
        txs = [buy(t(2025, 1, 1), 100, shares=10),
               buy(t(2025, 3, 1), 5, shares=None, is_bonus=True, from_cash=False)]
        snap = make_snap(t(2025, 4, 1), cash=0, positions=[{"isin": "X1", "value": 1100, "shares": 10.05}])
        # At Feb 1, snapshot says we had 10.05 shares (current). The skipped saveback means
        # we don't subtract — slight overestimate (10.05 vs true 10.0).
        result = shares_at(t(2025, 2, 1), snap, txs)
        self.assertAlmostEqual(result["X1"], 10.05)


class CashAtTests(unittest.TestCase):
    def test_reverses_buy(self):
        # Today: 500. BUY of 300 in March. At Feb 1: 800.
        txs = [buy(t(2025, 3, 1), 300, shares=3)]
        result = cash_at(t(2025, 2, 1), 500.0, txs)
        self.assertAlmostEqual(result, 800.0)

    def test_reverses_deposit(self):
        # Today: 1000. DEPOSIT 500 in March. At Feb 1: 500.
        txs = [deposit(t(2025, 3, 1), 500)]
        result = cash_at(t(2025, 2, 1), 1000.0, txs)
        self.assertAlmostEqual(result, 500.0)

    def test_skips_saveback_no_cash_movement(self):
        # Saveback didn't move cash → cash unchanged when reversing.
        txs = [buy(t(2025, 3, 1), 5, shares=None, is_bonus=True, from_cash=False)]
        result = cash_at(t(2025, 2, 1), 1000.0, txs)
        self.assertAlmostEqual(result, 1000.0)


class ReconstructSnapshotAtTests(unittest.TestCase):
    def test_full_reconstruction(self):
        # User: in March bought 10 shares at €100 (€1000 total). Today snapshot
        # has 10 shares (no further activity). At Feb 1, no shares yet, but cash
        # had been higher by €1000.
        txs = [buy(t(2025, 3, 1), 1000, shares=10)]
        snap_today = make_snap(t(2025, 4, 1), cash=500, positions=[{"isin": "X1", "value": 1100, "shares": 10}])
        result = reconstruct_snapshot_at(t(2025, 2, 1), snap_today, txs, prices_at_date={"X1": 95.0})
        # No X1 yet at Feb 1 → no positions
        self.assertEqual(len(result.positions), 0)
        self.assertAlmostEqual(result.cash_eur, 1500.0)

    def test_with_partial_history(self):
        # At March 15, after the BUY of 10 shares but before any further moves.
        txs = [buy(t(2025, 3, 1), 1000, shares=10),
               buy(t(2025, 4, 1), 500, shares=5)]
        snap_today = make_snap(t(2026, 1, 1), cash=200, positions=[{"isin": "X1", "value": 2250, "shares": 15}])
        # At March 15, only the first BUY had happened: 10 shares, cash = 200 + 500 = 700.
        result = reconstruct_snapshot_at(t(2025, 3, 15), snap_today, txs, prices_at_date={"X1": 110.0})
        self.assertEqual(len(result.positions), 1)
        self.assertAlmostEqual(result.positions[0].shares, 10.0)
        self.assertAlmostEqual(result.positions[0].net_value_eur, 1100.0)
        self.assertAlmostEqual(result.cash_eur, 700.0)

    def test_skips_isin_without_price(self):
        # 1 ISIN held, no price → excluded from positions.
        txs = [buy(t(2025, 3, 1), 1000, shares=10)]
        snap_today = make_snap(t(2025, 4, 1), cash=0, positions=[{"isin": "X1", "value": 1100, "shares": 10}])
        result = reconstruct_snapshot_at(t(2025, 3, 15), snap_today, txs, prices_at_date={})
        self.assertEqual(len(result.positions), 0)


if __name__ == "__main__":
    unittest.main()
