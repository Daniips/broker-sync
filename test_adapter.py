"""Unit tests for brokers/tr/adapter.py.

These tests build minimal raw events (matching the shape TR emits) and
verify that `raw_event_to_tx` maps them to the correct `Transaction`.

For event types where the adapter delegates to `brokers/tr/parser.py`
(TRADING_*, SAVEBACK, GIFTING, dividends), we monkeypatch the parser to
isolate the adapter's logic from the parser's.
"""
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from brokers.tr.adapter import raw_event_to_tx
from core.types import TxKind


TZ = ZoneInfo("Europe/Madrid")


def raw_event(**overrides):
    """Build a minimal TR raw event with sane defaults; override fields as needed."""
    base = {
        "id": "evt-test-001",
        "eventType": "TRADING_TRADE_EXECUTED",
        "timestamp": "2026-04-15T10:30:00.000+0000",
        "amount": {"value": -100.0, "currency": "EUR"},
        "title": "Test Asset",
        "subtitle": "Kauforder",
        "status": "EXECUTED",
        "details": {"sections": []},
    }
    base.update(overrides)
    return base


class StatusFilterTests(unittest.TestCase):
    def test_canceled_returns_none(self):
        for status in ("CANCELED", "CANCELLED", "FAILED", "REJECTED", "PENDING"):
            self.assertIsNone(raw_event_to_tx(raw_event(status=status), tz=TZ))

    def test_executed_passes(self):
        with patch("brokers.tr.parser.extract_trade_details") as p:
            p.return_value = {"isin": "X1", "shares": 1.0, "unit_price": 100.0}
            tx = raw_event_to_tx(raw_event(status="EXECUTED"), tz=TZ)
        self.assertIsNotNone(tx)


class MissingDataTests(unittest.TestCase):
    def test_missing_id_returns_none(self):
        self.assertIsNone(raw_event_to_tx(raw_event(id=None), tz=TZ))

    def test_missing_timestamp_returns_none(self):
        self.assertIsNone(raw_event_to_tx(raw_event(timestamp=None), tz=TZ))

    def test_unknown_event_type_returns_none(self):
        self.assertIsNone(raw_event_to_tx(raw_event(eventType="UNKNOWN_TYPE"), tz=TZ))

    def test_buy_with_missing_amount_returns_none(self):
        with patch("brokers.tr.parser.extract_trade_details") as p:
            p.return_value = {"isin": "X1", "shares": 1.0, "unit_price": 100.0}
            self.assertIsNone(raw_event_to_tx(raw_event(amount={}), tz=TZ))


class TradingTradeExecutedTests(unittest.TestCase):
    def test_buy_negative_value(self):
        with patch("brokers.tr.parser.extract_trade_details") as p:
            p.return_value = {"isin": "IE00B5BMR087", "shares": 0.5, "unit_price": 200.0}
            tx = raw_event_to_tx(raw_event(amount={"value": -100.0}), tz=TZ)
        self.assertEqual(tx.kind, TxKind.BUY)
        self.assertEqual(tx.amount_eur, -100.0)
        self.assertEqual(tx.isin, "IE00B5BMR087")
        self.assertEqual(tx.shares, 0.5)
        self.assertFalse(tx.is_bonus)
        self.assertTrue(tx.from_cash)
        self.assertEqual(tx.broker, "tr")

    def test_sell_positive_value(self):
        with patch("brokers.tr.parser.extract_trade_details") as p:
            p.return_value = {"isin": "X1", "shares": 1.0, "unit_price": 150.0}
            tx = raw_event_to_tx(raw_event(amount={"value": 150.0}), tz=TZ)
        self.assertEqual(tx.kind, TxKind.SELL)
        self.assertEqual(tx.amount_eur, 150.0)

    def test_zero_value_returns_none(self):
        with patch("brokers.tr.parser.extract_trade_details") as p:
            p.return_value = {"isin": "X1", "shares": 1.0, "unit_price": 100.0}
            self.assertIsNone(raw_event_to_tx(raw_event(amount={"value": 0.0}), tz=TZ))


class SavingsPlanTests(unittest.TestCase):
    def test_savings_plan_executed_is_buy(self):
        with patch("brokers.tr.parser.extract_trade_details") as p:
            p.return_value = {"isin": "X1", "shares": 0.1, "unit_price": 75.0}
            tx = raw_event_to_tx(raw_event(
                eventType="TRADING_SAVINGSPLAN_EXECUTED",
                amount={"value": -75.0},
            ), tz=TZ)
        self.assertEqual(tx.kind, TxKind.BUY)
        self.assertFalse(tx.is_bonus)
        self.assertTrue(tx.from_cash)


class SavebackTests(unittest.TestCase):
    def test_saveback_is_buy_bonus_not_from_cash(self):
        with patch("brokers.tr.parser.extract_trade_details") as p:
            p.return_value = {"isin": "X1", "shares": None, "unit_price": None}
            tx = raw_event_to_tx(raw_event(
                eventType="SAVEBACK_AGGREGATE",
                amount={"value": -15.0},
                title="Saveback",
            ), tz=TZ)
        self.assertEqual(tx.kind, TxKind.BUY)
        self.assertEqual(tx.amount_eur, -15.0)
        self.assertTrue(tx.is_bonus)
        self.assertFalse(tx.from_cash)

    def test_saveback_negates_positive_value(self):
        # Some saveback events come with positive value; we always treat them
        # as outflow into shares (BUY direction).
        with patch("brokers.tr.parser.extract_trade_details") as p:
            p.return_value = {"isin": "X1", "shares": None, "unit_price": None}
            tx = raw_event_to_tx(raw_event(
                eventType="SAVEBACK_AGGREGATE",
                amount={"value": 10.0},
            ), tz=TZ)
        self.assertEqual(tx.amount_eur, -10.0)


class GiftingTests(unittest.TestCase):
    def test_gift_is_buy_with_real_cost_basis(self):
        # Gifts have a cost basis equal to the share value at delivery.
        # User's Excel and TR averageBuyIn count them at full price.
        with patch("brokers.tr.parser.extract_gift_details") as p:
            p.return_value = {"isin": "X1", "shares": 0.05, "cost_eur": 9.00}
            tx = raw_event_to_tx(raw_event(
                eventType="GIFTING_RECIPIENT_ACTIVITY",
                title="ETF-Geschenk",
            ), tz=TZ)
        self.assertEqual(tx.kind, TxKind.BUY)
        self.assertEqual(tx.amount_eur, -9.00)
        self.assertEqual(tx.shares, 0.05)
        self.assertFalse(tx.is_bonus)   # gifts are NOT bonus (have real cost basis)
        self.assertFalse(tx.from_cash)  # but they don't move cash either

    def test_gift_skipped_if_unparseable(self):
        with patch("brokers.tr.parser.extract_gift_details") as p:
            p.return_value = {"isin": None, "shares": None, "cost_eur": None}
            tx = raw_event_to_tx(raw_event(
                eventType="GIFTING_RECIPIENT_ACTIVITY",
            ), tz=TZ)
        self.assertIsNone(tx)

    def test_gift_overrides_used_when_parser_fails(self):
        with patch("brokers.tr.parser.extract_gift_details") as p:
            p.return_value = {"isin": "X1", "shares": None, "cost_eur": None}
            tx = raw_event_to_tx(
                raw_event(eventType="GIFTING_RECIPIENT_ACTIVITY"),
                tz=TZ,
                gift_overrides={"X1": {"shares": 0.1, "cost_eur": 50.0}},
            )
        self.assertIsNotNone(tx)
        self.assertEqual(tx.amount_eur, -50.0)
        self.assertEqual(tx.shares, 0.1)


class DividendTests(unittest.TestCase):
    def test_dividend_is_inflow(self):
        with patch("brokers.tr.parser.extract_dividend_details") as p:
            p.return_value = {"isin": "X1", "gross": 30.0, "tax": 4.5, "net": 25.5}
            tx = raw_event_to_tx(raw_event(
                eventType="SSP_CORPORATE_ACTION_CASH",
                subtitle="Bardividende",
                amount={"value": 25.5},
            ), tz=TZ)
        self.assertEqual(tx.kind, TxKind.DIVIDEND)
        self.assertEqual(tx.amount_eur, 25.5)
        self.assertTrue(tx.from_cash)

    def test_bond_coupon_is_dividend_kind(self):
        # Bond coupons (subtitle Zinszahlung) are unified as DIVIDEND for cash flow.
        with patch("brokers.tr.parser.extract_isin_from_icon") as p:
            p.return_value = "BOND1"
            tx = raw_event_to_tx(raw_event(
                eventType="SSP_CORPORATE_ACTION_CASH",
                subtitle="Zinszahlung",
                amount={"value": 12.5},
            ), tz=TZ)
        self.assertEqual(tx.kind, TxKind.DIVIDEND)
        self.assertEqual(tx.amount_eur, 12.5)

    def test_unknown_subtitle_returns_none(self):
        tx = raw_event_to_tx(raw_event(
            eventType="SSP_CORPORATE_ACTION_CASH",
            subtitle="UnknownSubtitle",
            amount={"value": 10.0},
        ), tz=TZ)
        self.assertIsNone(tx)


class InterestTests(unittest.TestCase):
    def test_interest_payout_is_inflow(self):
        tx = raw_event_to_tx(raw_event(
            eventType="INTEREST_PAYOUT",
            amount={"value": 4.32},
            title="Zinsen",
        ), tz=TZ)
        self.assertEqual(tx.kind, TxKind.INTEREST)
        self.assertEqual(tx.amount_eur, 4.32)


class CashFlowTests(unittest.TestCase):
    def test_bank_incoming_is_deposit(self):
        tx = raw_event_to_tx(raw_event(
            eventType="BANK_TRANSACTION_INCOMING",
            amount={"value": 1000.0},
            title="Transferencia",
        ), tz=TZ)
        self.assertEqual(tx.kind, TxKind.DEPOSIT)
        self.assertEqual(tx.amount_eur, 1000.0)
        self.assertTrue(tx.from_cash)

    def test_bizum_incoming_is_deposit(self):
        tx = raw_event_to_tx(raw_event(
            eventType="PAYMENT_BIZUM_C2C_INCOMING",
            amount={"value": 25.0},
        ), tz=TZ)
        self.assertEqual(tx.kind, TxKind.DEPOSIT)

    def test_bank_outgoing_is_withdrawal(self):
        tx = raw_event_to_tx(raw_event(
            eventType="BANK_TRANSACTION_OUTGOING",
            amount={"value": -500.0},
        ), tz=TZ)
        self.assertEqual(tx.kind, TxKind.WITHDRAWAL)
        self.assertEqual(tx.amount_eur, -500.0)

    def test_card_transaction_is_withdrawal(self):
        tx = raw_event_to_tx(raw_event(
            eventType="CARD_TRANSACTION",
            amount={"value": -32.50},
            title="Mercadona",
        ), tz=TZ)
        self.assertEqual(tx.kind, TxKind.WITHDRAWAL)


class TimezoneTests(unittest.TestCase):
    def test_ts_is_tz_aware_in_target_tz(self):
        tx = raw_event_to_tx(raw_event(
            eventType="INTEREST_PAYOUT",
            timestamp="2026-04-15T10:30:00.000Z",
            amount={"value": 1.0},
        ), tz=TZ)
        self.assertIsNotNone(tx.ts.tzinfo)
        # 10:30 UTC = 12:30 Madrid in summer time, 11:30 in winter — both valid.
        self.assertIn(tx.ts.hour, (11, 12))


if __name__ == "__main__":
    unittest.main()
