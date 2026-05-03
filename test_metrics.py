"""Unit tests for core/metrics.py — pure synthetic data, no broker dependency."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.metrics import (
    benchmark_return,
    concentration,
    contribution_vs_average,
    cost_basis_of_current_holdings,
    cost_basis_total,
    currency_exposure,
    monthly_contributions,
    mwr,
    per_position_attribution,
    positions_value,
    simple_return,
    total_invested,
    total_wealth,
    unrealized_return,
    unrealized_return_user_paid,
    xirr,
)
from core.types import PortfolioSnapshot, Position, Transaction, TxKind


TZ = ZoneInfo("Europe/Madrid")


def t(year, month, day=1, hour=12):
    return datetime(year, month, day, hour, tzinfo=TZ)


def buy(when, amount, isin="X1", shares=None, is_bonus=False, _id=None):
    return Transaction(
        id=_id or f"buy-{when.isoformat()}-{amount}",
        ts=when,
        kind=TxKind.BUY,
        amount_eur=-abs(amount),
        title="buy",
        broker="test",
        isin=isin,
        shares=shares,
        is_bonus=is_bonus,
    )


def sell(when, amount, isin="X1", shares=None, _id=None):
    return Transaction(
        id=_id or f"sell-{when.isoformat()}-{amount}",
        ts=when,
        kind=TxKind.SELL,
        amount_eur=abs(amount),
        title="sell",
        broker="test",
        isin=isin,
        shares=shares,
    )


def dividend(when, amount, isin="X1", _id=None):
    return Transaction(
        id=_id or f"div-{when.isoformat()}-{amount}",
        ts=when,
        kind=TxKind.DIVIDEND,
        amount_eur=abs(amount),
        title="dividend",
        broker="test",
        isin=isin,
    )


def snap(when, positions_value=0.0, cash=0.0, cost_basis=None):
    if positions_value:
        pos = (Position(isin="X1", title="X", net_value_eur=positions_value,
                        broker="test", cost_basis_eur=cost_basis),)
    else:
        pos = ()
    return PortfolioSnapshot(ts=when, cash_eur=cash, positions=pos)


class XirrTests(unittest.TestCase):
    def test_lump_sum_one_year_10pct(self):
        # -1000 today, +1100 in exactly one year → 10%
        flows = [(t(2025, 1, 1), -1000.0), (t(2026, 1, 1), 1100.0)]
        r = xirr(flows)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r, 0.10, places=3)

    def test_negative_return(self):
        flows = [(t(2025, 1, 1), -1000.0), (t(2026, 1, 1), 900.0)]
        r = xirr(flows)
        self.assertAlmostEqual(r, -0.10, places=3)

    def test_returns_none_if_only_negatives(self):
        flows = [(t(2025, 1, 1), -100.0), (t(2026, 1, 1), -50.0)]
        self.assertIsNone(xirr(flows))

    def test_returns_none_if_empty(self):
        self.assertIsNone(xirr([]))


class TotalInvestedTests(unittest.TestCase):
    def test_buys_minus_sells(self):
        txs = [buy(t(2025, 1, 1), 1000), buy(t(2025, 6, 1), 500), sell(t(2025, 9, 1), 200)]
        self.assertAlmostEqual(total_invested(txs), 1300.0)

    def test_bonus_excluded_when_income(self):
        txs = [buy(t(2025, 1, 1), 1000), buy(t(2025, 2, 1), 15, is_bonus=True)]
        self.assertAlmostEqual(total_invested(txs, bonus_as="income"), 1000.0)

    def test_bonus_included_when_deposit(self):
        txs = [buy(t(2025, 1, 1), 1000), buy(t(2025, 2, 1), 15, is_bonus=True)]
        self.assertAlmostEqual(total_invested(txs, bonus_as="deposit"), 1015.0)

    def test_window(self):
        txs = [buy(t(2024, 6, 1), 500), buy(t(2025, 6, 1), 1000)]
        result = total_invested(txs, start=t(2025, 1, 1))
        self.assertAlmostEqual(result, 1000.0)


class SimpleReturnTests(unittest.TestCase):
    def test_pos_return(self):
        txs = [buy(t(2025, 1, 1), 1000)]
        s = snap(t(2026, 1, 1), positions_value=1100)
        self.assertAlmostEqual(simple_return(txs, s), 0.10, places=4)

    def test_negative_when_value_below_invested(self):
        # 12 monthly buys of 1000 = 12000 invested, value 11000 → −8.33%
        txs = [buy(t(2025, m, 1), 1000) for m in range(1, 13)]
        s = snap(t(2025, 12, 31), positions_value=11000)
        self.assertAlmostEqual(simple_return(txs, s), -1000.0 / 12000.0, places=4)

    def test_none_when_no_invested(self):
        self.assertIsNone(simple_return([], snap(t(2025, 12, 31), positions_value=100)))


class MwrTests(unittest.TestCase):
    def test_lump_sum_matches_simple(self):
        # All money in jan 2025, value 1.10x in jan 2026 → MWR = 10%
        txs = [buy(t(2025, 1, 1), 10000)]
        s = snap(t(2026, 1, 1), positions_value=11000)
        r = mwr(txs, s)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r, 0.10, places=3)

    def test_dca_loss_is_negative(self):
        # 1000/month for 12 months, end value 11000 (less than 12000 invested)
        # MWR is negative AND more negative than the simple −8.3% because
        # money was on average invested ~6 months → annualized rate ≈ −15%.
        txs = [buy(t(2025, m, 1), 1000) for m in range(1, 13)]
        s = snap(t(2026, 1, 1), positions_value=11000)
        r = mwr(txs, s)
        self.assertIsNotNone(r)
        self.assertLess(r, -0.10)
        self.assertGreater(r, -0.25)

    def test_bonus_as_income_increases_mwr(self):
        # User contributes 1000/month + saveback 15/month bonus.
        # End value reflects all (gross): 1000 base + 1000*0.10 growth + saveback*0.10 growth
        # bonus_as=income hides the bonus contributions → MWR looks better
        # bonus_as=deposit includes them → MWR looks worse
        base = [buy(t(2025, m, 1), 1000) for m in range(1, 13)]
        bonus = [buy(t(2025, m, 1), 15, is_bonus=True) for m in range(1, 13)]
        # Final value: assume +5% on each contribution (very rough)
        s = snap(t(2026, 1, 1), positions_value=12000 * 1.05 + 180 * 1.05)
        r_income = mwr(base + bonus, s, bonus_as="income")
        r_deposit = mwr(base + bonus, s, bonus_as="deposit")
        self.assertIsNotNone(r_income)
        self.assertIsNotNone(r_deposit)
        self.assertGreater(r_income, r_deposit)

    def test_none_when_no_buys(self):
        s = snap(t(2026, 1, 1), positions_value=100)
        self.assertIsNone(mwr([], s))


class MonthlyContributionsTests(unittest.TestCase):
    def test_default_is_gross_only_buys_with_bonus(self):
        # Default: include_bonus=True, include_sells=False
        # Matches Excel "Dinero invertido" sheet behavior.
        txs = [
            buy(t(2025, 1, 5), 100),
            buy(t(2025, 1, 20), 200),
            buy(t(2025, 1, 25), 15, is_bonus=True),  # saveback
            buy(t(2025, 2, 10), 300),
            sell(t(2025, 2, 25), 50),  # sale: ignored by default
        ]
        m = monthly_contributions(txs)
        self.assertAlmostEqual(m[(2025, 1)], 315.0)  # 100 + 200 + 15 saveback
        self.assertAlmostEqual(m[(2025, 2)], 300.0)  # sell not subtracted

    def test_exclude_bonus(self):
        txs = [buy(t(2025, 1, 5), 100), buy(t(2025, 1, 25), 15, is_bonus=True)]
        m = monthly_contributions(txs, include_bonus=False)
        self.assertAlmostEqual(m[(2025, 1)], 100.0)

    def test_include_sells(self):
        txs = [buy(t(2025, 2, 10), 300), sell(t(2025, 2, 25), 50)]
        m = monthly_contributions(txs, include_sells=True)
        self.assertAlmostEqual(m[(2025, 2)], 250.0)


class ContributionVsAverageTests(unittest.TestCase):
    def test_compares_against_prior_window(self):
        # 3 prior months at 1000, current at 1500 → +50% vs avg
        txs = [buy(t(2026, m, 1), 1000) for m in (1, 2, 3)]
        txs.append(buy(t(2026, 4, 1), 1500))
        result = contribution_vs_average(txs, 2026, 4, window_months=6)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["this_month"], 1500.0)
        self.assertAlmostEqual(result["avg"], 1000.0)
        self.assertAlmostEqual(result["delta_pct"], 0.5, places=4)
        self.assertEqual(result["window_months_used"], 3)

    def test_default_includes_bonus_and_ignores_sells(self):
        # April: 1000 buy + 15 saveback + 50 sell. With defaults, this_month = 1015.
        txs = [
            buy(t(2026, 1, 1), 1000),
            buy(t(2026, 2, 1), 1000),
            buy(t(2026, 3, 1), 1000),
            buy(t(2026, 4, 1), 1000),
            buy(t(2026, 4, 5), 15, is_bonus=True),
            sell(t(2026, 4, 20), 50),
        ]
        result = contribution_vs_average(txs, 2026, 4, window_months=6)
        self.assertAlmostEqual(result["this_month"], 1015.0)

    def test_returns_none_with_no_history(self):
        txs = [buy(t(2026, 4, 1), 1500)]
        self.assertIsNone(contribution_vs_average(txs, 2026, 4))


class ConcentrationTests(unittest.TestCase):
    def test_orders_desc_by_pct(self):
        positions = (
            Position(isin="A", title="A", net_value_eur=2000, broker="t"),
            Position(isin="B", title="B", net_value_eur=8000, broker="t"),
        )
        s = PortfolioSnapshot(ts=t(2026, 4, 26), cash_eur=0, positions=positions)
        c = concentration(s)
        self.assertEqual(c[0]["isin"], "B")
        self.assertAlmostEqual(c[0]["pct"], 0.8)
        self.assertEqual(c[1]["isin"], "A")
        self.assertAlmostEqual(c[1]["pct"], 0.2)

    def test_total_scope_includes_cash(self):
        positions = (Position(isin="A", title="A", net_value_eur=3000, broker="t"),)
        s = PortfolioSnapshot(ts=t(2026, 4, 26), cash_eur=7000, positions=positions)
        c_pos = concentration(s, scope="positions")
        c_tot = concentration(s, scope="total")
        self.assertAlmostEqual(c_pos[0]["pct"], 1.0)
        self.assertAlmostEqual(c_tot[0]["pct"], 0.3)

    def test_empty_when_no_positions(self):
        s = PortfolioSnapshot(ts=t(2026, 4, 26), cash_eur=100, positions=())
        self.assertEqual(concentration(s), [])

    def test_per_asset_limits_within(self):
        positions = (
            Position(isin="A", title="SP500", net_value_eur=4500, broker="t"),
            Position(isin="B", title="Solana", net_value_eur=300, broker="t"),
        )
        s = PortfolioSnapshot(ts=t(2026, 4, 26), cash_eur=0, positions=positions)
        c = concentration(s, limits={"A": 0.50, "B": 0.08})
        # A: 4500/4800 = 93.75% — over its 50% limit
        # B: 300/4800 = 6.25% — under its 8% limit
        a = next(x for x in c if x["isin"] == "A")
        b = next(x for x in c if x["isin"] == "B")
        self.assertAlmostEqual(a["limit"], 0.50)
        self.assertTrue(a["exceeded"])
        self.assertAlmostEqual(a["margin_pp"], (0.50 - 0.9375) * 100, places=2)
        self.assertAlmostEqual(b["limit"], 0.08)
        self.assertFalse(b["exceeded"])
        self.assertAlmostEqual(b["margin_pp"], (0.08 - 0.0625) * 100, places=2)

    def test_falls_back_to_default_threshold(self):
        positions = (
            Position(isin="A", title="A", net_value_eur=4000, broker="t"),
            Position(isin="B", title="B", net_value_eur=1000, broker="t"),
        )
        s = PortfolioSnapshot(ts=t(2026, 4, 26), cash_eur=0, positions=positions)
        # Only A has an explicit limit; B uses default_threshold
        c = concentration(s, limits={"A": 0.50}, default_threshold=0.30)
        a = next(x for x in c if x["isin"] == "A")
        b = next(x for x in c if x["isin"] == "B")
        self.assertAlmostEqual(a["limit"], 0.50)
        self.assertAlmostEqual(b["limit"], 0.30)
        self.assertTrue(a["exceeded"])  # 80% > 50%
        self.assertFalse(b["exceeded"])  # 20% < 30%

    def test_no_limit_when_neither_provided(self):
        positions = (Position(isin="A", title="A", net_value_eur=1000, broker="t"),)
        s = PortfolioSnapshot(ts=t(2026, 4, 26), cash_eur=0, positions=positions)
        c = concentration(s)  # no limits, no default
        self.assertIsNone(c[0]["limit"])
        self.assertIsNone(c[0]["margin_pp"])
        self.assertFalse(c[0]["exceeded"])


class PerPositionAttributionTests(unittest.TestCase):
    def test_single_position_full_attribution(self):
        # Bought 1000€ of X1 1 year ago, now worth 1100€.
        # Position MWR ≈ 10%; weight 100%; contribution ~10pp.
        txs = [buy(t(2025, 1, 1), 1000, isin="X1")]
        s = PortfolioSnapshot(
            ts=t(2026, 1, 1), cash_eur=0,
            positions=(Position(isin="X1", title="X1", net_value_eur=1100, broker="t"),),
        )
        attr = per_position_attribution(s, txs)
        self.assertEqual(len(attr), 1)
        self.assertAlmostEqual(attr[0]["position_mwr"], 0.10, places=2)
        self.assertAlmostEqual(attr[0]["value_pct"], 1.0)
        self.assertAlmostEqual(attr[0]["contribution_pp"], 10.0, places=1)

    def test_two_positions_sorted_by_abs_contribution(self):
        # X1: bought 1000, now 1100 → +10% MWR, weight 1100/1300 ≈ 84.6% → ~+8.5pp
        # X2: bought 500,  now 200  → very negative MWR, weight ~15.4% → big negative pp
        txs = [
            buy(t(2025, 1, 1), 1000, isin="X1"),
            buy(t(2025, 1, 1), 500, isin="X2"),
        ]
        positions = (
            Position(isin="X1", title="X1", net_value_eur=1100, broker="t"),
            Position(isin="X2", title="X2", net_value_eur=200, broker="t"),
        )
        s = PortfolioSnapshot(ts=t(2026, 1, 1), cash_eur=0, positions=positions)
        attr = per_position_attribution(s, txs)
        self.assertEqual(len(attr), 2)
        # Both contributions are large in absolute value; X2 (-60% MWR ≈ −9pp) is bigger absolute
        # X1 contributes ~+8.5pp; X2 ~-9.2pp. abs(X2) > abs(X1) → X2 first.
        self.assertEqual(attr[0]["isin"], "X2")
        self.assertEqual(attr[1]["isin"], "X1")
        self.assertLess(attr[0]["contribution_pp"], 0)
        self.assertGreater(attr[1]["contribution_pp"], 0)

    def test_dividends_increase_position_mwr(self):
        # Same buy 1000€, current value 1000€ (flat), but with 50€ dividend → ~5% MWR
        txs_no_div = [buy(t(2025, 1, 1), 1000, isin="X1")]
        txs_div = txs_no_div + [dividend(t(2025, 7, 1), 50, isin="X1")]
        s = PortfolioSnapshot(
            ts=t(2026, 1, 1), cash_eur=0,
            positions=(Position(isin="X1", title="X1", net_value_eur=1000, broker="t"),),
        )
        a_no = per_position_attribution(s, txs_no_div)
        a_with = per_position_attribution(s, txs_div)
        self.assertAlmostEqual(a_no[0]["position_mwr"], 0.0, places=3)
        self.assertGreater(a_with[0]["position_mwr"], 0.04)

    def test_skips_position_without_flows(self):
        # Position exists in snapshot but no txs → can't compute, skipped.
        s = PortfolioSnapshot(
            ts=t(2026, 1, 1), cash_eur=0,
            positions=(Position(isin="X1", title="X1", net_value_eur=1000, broker="t"),),
        )
        attr = per_position_attribution(s, [])
        self.assertEqual(attr, [])


class BenchmarkReturnTests(unittest.TestCase):
    def test_one_year_10pct_gain(self):
        history = [
            {"ts": t(2025, 1, 1), "close": 100.0},
            {"ts": t(2025, 7, 1), "close": 105.0},
            {"ts": t(2026, 1, 1), "close": 110.0},
        ]
        r = benchmark_return(history, t(2025, 1, 1), t(2026, 1, 1))
        self.assertAlmostEqual(r["total_return"], 0.10)
        # Exactly 365 days → annualized ≈ 10%.
        self.assertAlmostEqual(r["annualized_return"], 0.10, places=2)

    def test_short_period_extrapolates_correctly(self):
        # 6 months at +5% → annualized ~10.25% (1.05^2 - 1)
        history = [
            {"ts": t(2025, 1, 1), "close": 100.0},
            {"ts": t(2025, 7, 1), "close": 105.0},
        ]
        r = benchmark_return(history, t(2025, 1, 1), t(2025, 7, 1))
        self.assertAlmostEqual(r["total_return"], 0.05, places=4)
        # ~181 days; annualized ≈ (1.05)^(365.25/181) - 1 ≈ 0.1037
        self.assertGreater(r["annualized_return"], 0.10)
        self.assertLess(r["annualized_return"], 0.105)

    def test_negative_return(self):
        history = [
            {"ts": t(2025, 1, 1), "close": 100.0},
            {"ts": t(2026, 1, 1), "close": 90.0},
        ]
        r = benchmark_return(history, t(2025, 1, 1), t(2026, 1, 1))
        self.assertAlmostEqual(r["total_return"], -0.10)
        self.assertAlmostEqual(r["annualized_return"], -0.10, places=2)

    def test_returns_none_if_history_empty(self):
        self.assertIsNone(benchmark_return([], t(2025, 1, 1)))

    def test_returns_none_if_dates_collide(self):
        history = [{"ts": t(2025, 1, 1), "close": 100.0}]
        # start_ts after end_ts → no candidate for end → days <= 0
        r = benchmark_return(history, t(2025, 1, 1), t(2025, 1, 1))
        self.assertIsNone(r)

    def test_uses_last_bar_when_end_ts_none(self):
        history = [
            {"ts": t(2025, 1, 1), "close": 100.0},
            {"ts": t(2025, 6, 1), "close": 110.0},
            {"ts": t(2025, 12, 1), "close": 115.0},
        ]
        r = benchmark_return(history, t(2025, 1, 1))  # no end_ts
        # Should use last bar at 2025-12-01
        self.assertAlmostEqual(r["end_price"], 115.0)


class CurrencyExposureTests(unittest.TestCase):
    def test_groups_by_currency_and_sorts_desc(self):
        positions = (
            Position(isin="A", title="USD ETF", net_value_eur=4000, broker="t"),
            Position(isin="B", title="EUR ETF", net_value_eur=800, broker="t"),
            Position(isin="C", title="USD ETF 2", net_value_eur=1200, broker="t"),
        )
        s = PortfolioSnapshot(ts=t(2026, 4, 26), cash_eur=0, positions=positions)
        e = currency_exposure(s, {"A": "USD", "B": "EUR", "C": "USD"}, include_cash=False)
        self.assertEqual(e[0]["currency"], "USD")
        self.assertAlmostEqual(e[0]["value_eur"], 5200)
        self.assertEqual(e[0]["n_positions"], 2)
        self.assertEqual(e[1]["currency"], "EUR")
        self.assertAlmostEqual(e[1]["value_eur"], 800)

    def test_includes_cash_in_default_eur(self):
        positions = (Position(isin="A", title="USD", net_value_eur=4000, broker="t"),)
        s = PortfolioSnapshot(ts=t(2026, 4, 26), cash_eur=6000, positions=positions)
        e = currency_exposure(s, {"A": "USD"})
        # USD: 4000 (positions). EUR: 6000 (cash).
        eur = next(x for x in e if x["currency"] == "EUR")
        usd = next(x for x in e if x["currency"] == "USD")
        self.assertAlmostEqual(eur["value_eur"], 6000)
        self.assertAlmostEqual(usd["value_eur"], 4000)
        self.assertAlmostEqual(eur["pct"], 0.6)
        self.assertAlmostEqual(usd["pct"], 0.4)
        # Cash isn't a position so n_positions stays 0 for EUR
        self.assertEqual(eur["n_positions"], 0)
        self.assertEqual(usd["n_positions"], 1)

    def test_unknown_isin_goes_to_default_label(self):
        positions = (
            Position(isin="A", title="A", net_value_eur=1000, broker="t"),
            Position(isin="B", title="B", net_value_eur=500, broker="t"),
        )
        s = PortfolioSnapshot(ts=t(2026, 4, 26), cash_eur=0, positions=positions)
        e = currency_exposure(s, {"A": "USD"}, include_cash=False)
        # Only A has currency; B falls to UNKNOWN
        labels = {x["currency"] for x in e}
        self.assertIn("USD", labels)
        self.assertIn("UNKNOWN", labels)


class WealthTests(unittest.TestCase):
    def test_total_combines_cash_and_positions(self):
        s = snap(t(2026, 1, 1), positions_value=8000, cash=2000)
        self.assertAlmostEqual(total_wealth(s), 10000.0)
        self.assertAlmostEqual(positions_value(s), 8000.0)


class UnrealizedReturnTests(unittest.TestCase):
    def test_matches_tr_app_style(self):
        # Cost basis 8000, value 9280 → +16% unrealized P&L
        s = snap(t(2026, 4, 26), positions_value=9280, cost_basis=8000)
        r = unrealized_return(s)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r["pnl_pct"], 0.16, places=4)
        self.assertAlmostEqual(r["pnl_eur"], 1280.0)
        self.assertAlmostEqual(r["cost_basis"], 8000.0)
        self.assertAlmostEqual(r["value"], 9280.0)
        # Without txs, dividend fields are None
        self.assertIsNone(r["dividends_held"])
        self.assertIsNone(r["pnl_pct_total"])

    def test_with_dividends_matches_tr_rendimiento(self):
        # Cost basis 8424.20, value 9423.96 → +11.87% pure unrealized.
        # Plus 186€ dividends of held ISINs → +14.07% total.
        s = snap(t(2026, 4, 26), positions_value=9423.96, cost_basis=8424.20)
        # 186€ dividend on the held position (ISIN X1)
        txs = [dividend(t(2026, 1, 15), 186, isin="X1")]
        r = unrealized_return(s, txs=txs)
        self.assertAlmostEqual(r["pnl_pct"], 0.1187, places=3)
        self.assertAlmostEqual(r["dividends_held"], 186.0)
        self.assertAlmostEqual(r["pnl_pct_total"], 0.1407, places=3)

    def test_dividends_of_sold_positions_not_counted(self):
        # Position X1 still held. Dividend of Y1 (no longer held) should not count.
        s = snap(t(2026, 4, 26), positions_value=1100, cost_basis=1000)
        txs = [dividend(t(2026, 1, 15), 50, isin="Y1")]  # no held
        r = unrealized_return(s, txs=txs)
        self.assertAlmostEqual(r["dividends_held"], 0.0)
        self.assertAlmostEqual(r["pnl_pct_total"], 0.10, places=4)

    def test_none_when_no_cost_basis(self):
        s = snap(t(2026, 4, 26), positions_value=9280, cost_basis=None)
        self.assertIsNone(unrealized_return(s))
        self.assertIsNone(cost_basis_total(s))

    def test_excludes_positions_without_cost_basis(self):
        # Two positions: one with cost basis, one without. Only the first counts.
        positions = (
            Position(isin="A", title="A", net_value_eur=1100, broker="test", cost_basis_eur=1000),
            Position(isin="B", title="B", net_value_eur=500, broker="test", cost_basis_eur=None),
        )
        s = PortfolioSnapshot(ts=t(2026, 4, 26), cash_eur=0, positions=positions)
        r = unrealized_return(s)
        self.assertAlmostEqual(r["value"], 1100.0)
        self.assertAlmostEqual(r["cost_basis"], 1000.0)
        self.assertEqual(r["positions_with_cost"], 1)
        self.assertEqual(r["positions_total"], 2)


class CostBasisFifoTests(unittest.TestCase):
    def test_simple_holding_no_sells(self):
        # 10 shares at 100 = 1000€. No sells. Cost basis 1000.
        txs = [buy(t(2025, 1, 1), 1000, shares=10)]
        cb = cost_basis_of_current_holdings(txs)
        self.assertAlmostEqual(cb["X1"], 1000.0)

    def test_partial_sell_fifo(self):
        # Buy 10 @ 100 (1000), buy 10 @ 150 (1500). Sell 12 → FIFO eats first lot
        # entirely (10 shares at 100 = 1000) and 2 from second lot (300).
        # Remaining: 8 shares of second lot at 150 = 1200.
        txs = [
            buy(t(2025, 1, 1), 1000, shares=10),
            buy(t(2025, 6, 1), 1500, shares=10),
            sell(t(2025, 12, 1), 0, shares=12),
        ]
        cb = cost_basis_of_current_holdings(txs)
        self.assertAlmostEqual(cb["X1"], 1200.0, places=4)

    def test_saveback_at_zero_cost(self):
        # Buy 10 @ 100 (1000), saveback 1 @ 105 (105). Cost basis own = 1000,
        # cost basis with bonus = 1105.
        txs = [
            buy(t(2025, 1, 1), 1000, shares=10),
            buy(t(2025, 2, 1), 105, shares=1, is_bonus=True),
        ]
        cb_user = cost_basis_of_current_holdings(txs, bonus_at_zero_cost=True)
        cb_full = cost_basis_of_current_holdings(txs, bonus_at_zero_cost=False)
        self.assertAlmostEqual(cb_user["X1"], 1000.0)
        self.assertAlmostEqual(cb_full["X1"], 1105.0)

    def test_unrealized_return_user_paid_matches_excel(self):
        # User scenario: own buys 1000€ for 10 shares, saveback 105€ for 1 share.
        # Today value = 11 × 110 = 1210. TR's averageBuyIn×shares = 1105 (own
        # 1000 + saveback 105). User Excel-style: invested = 1105 − 105 saveback
        # = 1000, value 1210 → +21%. TR-style: 1105, +9.5%.
        # NOTE: user_paid does NOT use FIFO — it derives cost basis from the
        # snapshot's cost_basis_eur and subtracts saveback amounts directly.
        # That's why we don't pass `shares=` for the saveback (irrelevant).
        txs = [
            buy(t(2025, 1, 1), 1000, shares=10),
            buy(t(2025, 6, 1), 105, is_bonus=True),
        ]
        s = PortfolioSnapshot(
            ts=t(2026, 1, 1),
            cash_eur=0,
            positions=(Position(isin="X1", title="X", net_value_eur=1210,
                                broker="test", cost_basis_eur=1105),),
        )
        up = unrealized_return_user_paid(s, txs)
        self.assertAlmostEqual(up["cost_basis"], 1000.0)
        self.assertAlmostEqual(up["pnl_pct"], 0.21, places=4)
        # The TR-style metric still uses snapshot cost basis (with bonus included).
        ur = unrealized_return(s)
        self.assertAlmostEqual(ur["pnl_pct"], 105/1105, places=4)

    def test_user_paid_skips_saveback_of_sold_positions(self):
        # User had X1 (sold entirely) and X2 (still held).
        # Saveback came in both. Only X2's saveback should affect user_paid.
        txs = [
            buy(t(2025, 1, 1), 100, isin="X1", shares=1),
            buy(t(2025, 2, 1), 5, isin="X1", is_bonus=True),
            buy(t(2025, 3, 1), 200, isin="X2", shares=2),
            buy(t(2025, 4, 1), 10, isin="X2", is_bonus=True),
        ]
        # Only X2 still held.
        s = PortfolioSnapshot(
            ts=t(2026, 1, 1),
            cash_eur=0,
            positions=(Position(isin="X2", title="X2", net_value_eur=220,
                                broker="test", cost_basis_eur=210),),
        )
        up = unrealized_return_user_paid(s, txs)
        # cost basis = 210 (TR) - 10 (X2 saveback) = 200. X1 saveback ignored.
        self.assertAlmostEqual(up["cost_basis"], 200.0)
        self.assertAlmostEqual(up["pnl_pct"], 0.10, places=4)


class MwrSubPeriodTests(unittest.TestCase):
    def test_returns_none_without_start_value(self):
        txs = [buy(t(2026, 3, 1), 1000), buy(t(2026, 4, 1), 1000)]
        s = snap(t(2026, 4, 26), positions_value=2100)
        r = mwr(txs, s, start=t(2026, 1, 1))
        self.assertIsNone(r)

    def test_works_with_start_value(self):
        # At t=2026-01-01 user already had positions worth 7000.
        # Then bought 1000 in March, 1000 in April. Now positions worth 9280.
        # Cash flows: -7000 (synthetic deposit at start), -1000 mar, -1000 apr,
        # +9280 final. Period ~4 months.
        txs = [buy(t(2026, 3, 1), 1000), buy(t(2026, 4, 1), 1000)]
        s = snap(t(2026, 4, 26), positions_value=9280)
        r = mwr(txs, s, start=t(2026, 1, 1), start_value=7000)
        self.assertIsNotNone(r)
        # Net gain over the period: 9280 - (7000 + 2000) = 280 → ~3% over ~4 months → annualized roughly 8-12%.
        self.assertGreater(r, 0.05)
        self.assertLess(r, 0.20)


class MwrIncludesDividendsTests(unittest.TestCase):
    def test_dividend_counted_as_positive_flow(self):
        # Buy 1000 at t=0. Dividend 50 at t=6 months. Position value 1000 at t=1y.
        # Without dividend, MWR ≈ 0%. With dividend (50), MWR > 0%.
        txs_no_div = [buy(t(2025, 1, 1), 1000)]
        txs_with_div = txs_no_div + [dividend(t(2025, 7, 1), 50)]
        s = snap(t(2026, 1, 1), positions_value=1000)
        r_no = mwr(txs_no_div, s)
        r_with = mwr(txs_with_div, s)
        self.assertIsNotNone(r_no)
        self.assertIsNotNone(r_with)
        self.assertAlmostEqual(r_no, 0.0, places=3)
        self.assertGreater(r_with, 0.04)  # ~5% from the dividend


if __name__ == "__main__":
    unittest.main()
