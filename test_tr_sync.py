import unittest
from datetime import datetime

import tr_sync


def raw_event(**overrides):
    base = {
        "id": "evt-1",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-04-10T12:00:00Z",
        "amount": {"value": -12.50},
        "title": "Mercadona",
        "status": "EXECUTED",
    }
    base.update(overrides)
    return base


class NormalizeEventTests(unittest.TestCase):
    def test_returns_none_if_amount_missing(self):
        self.assertIsNone(tr_sync.normalize_event(raw_event(amount={})))

    def test_returns_none_if_timestamp_missing(self):
        self.assertIsNone(tr_sync.normalize_event(raw_event(timestamp=None)))

    def test_importe_is_abs_and_rounded(self):
        n = tr_sync.normalize_event(raw_event(amount={"value": -12.345}))
        self.assertEqual(n["importe"], 12.35)
        self.assertEqual(n["raw_value"], -12.345)

    def test_month_key_uses_madrid_tz(self):
        # 2026-01-01T00:30:00Z -> 01:30 Madrid (CET), sigue siendo enero
        n = tr_sync.normalize_event(raw_event(timestamp="2026-01-01T00:30:00Z"))
        self.assertEqual(n["month_key"], (2026, 1))


class FilterEventsByFlowTests(unittest.TestCase):
    def test_routes_expense_by_type_and_sign(self):
        events = [
            raw_event(id="a", eventType="CARD_TRANSACTION", amount={"value": -10.0}),
            raw_event(id="b", eventType="CARD_TRANSACTION", amount={"value": 10.0}),  # refund, se descarta
        ]
        flows = tr_sync.filter_events_by_flow(events)
        self.assertEqual([e["id"] for e in flows["Gastos"]], ["a"])
        self.assertEqual(flows["Ingresos"], [])

    def test_routes_income_by_type_and_sign(self):
        events = [
            raw_event(id="c", eventType="BANK_TRANSACTION_INCOMING", amount={"value": 50.0}),
            raw_event(id="d", eventType="BANK_TRANSACTION_INCOMING", amount={"value": -50.0}),
        ]
        flows = tr_sync.filter_events_by_flow(events)
        self.assertEqual([e["id"] for e in flows["Ingresos"]], ["c"])
        self.assertEqual(flows["Gastos"], [])

    def test_excludes_canceled(self):
        events = [raw_event(id="e", status="CANCELED")]
        flows = tr_sync.filter_events_by_flow(events)
        self.assertEqual(flows["Gastos"], [])


class AggregateInvestmentsTests(unittest.TestCase):
    def _sp(self, **kw):
        defaults = {
            "eventType": "TRADING_SAVINGSPLAN_EXECUTED",
            "title": "Core S&P 500 USD (Acc)",
            "amount": {"value": -100.0},
        }
        defaults.update(kw)
        return raw_event(**defaults)

    def test_savings_plan_is_summed_under_mapped_name(self):
        events = [self._sp(id="1"), self._sp(id="2", amount={"value": -50.0})]
        totals = tr_sync.aggregate_investments(events)
        self.assertEqual(totals[("SP 500", 2026, 4)], 150.0)

    def test_saveback_uses_saveback_label(self):
        events = [raw_event(
            id="sb",
            eventType="SAVEBACK_AGGREGATE",
            title="Saveback",
            amount={"value": -15.0},
        )]
        totals = tr_sync.aggregate_investments(events)
        self.assertEqual(totals[(tr_sync.SAVEBACK_LABEL, 2026, 4)], 15.0)

    def test_canceled_savings_plan_ignored(self):
        events = [self._sp(id="x", status="CANCELED")]
        self.assertEqual(tr_sync.aggregate_investments(events), {})

    def test_manual_buy_is_counted(self):
        events = [raw_event(
            id="buy",
            eventType="TRADING_TRADE_EXECUTED",
            title="Core MSCI EM IMI USD (Acc)",
            amount={"value": -200.0},
        )]
        totals = tr_sync.aggregate_investments(events)
        self.assertEqual(totals[("MSCI EM IMI", 2026, 4)], 200.0)

    def test_manual_sell_is_ignored(self):
        events = [raw_event(
            id="sell",
            eventType="TRADING_TRADE_EXECUTED",
            title="Physical Gold USD (Acc)",
            amount={"value": 300.0},  # venta: valor positivo
        )]
        self.assertEqual(tr_sync.aggregate_investments(events), {})

    def test_unknown_asset_uses_raw_title(self):
        events = [self._sp(id="u", title="Activo desconocido XYZ")]
        totals = tr_sync.aggregate_investments(events)
        self.assertEqual(totals[("Activo desconocido XYZ", 2026, 4)], 100.0)


class ParseDeNumberTests(unittest.TestCase):
    def test_decimal_comma(self):
        self.assertAlmostEqual(tr_sync._parse_de_number("31,18"), 31.18)

    def test_with_currency(self):
        self.assertAlmostEqual(tr_sync._parse_de_number("31,18 €"), 31.18)

    def test_with_thousands_and_decimal(self):
        self.assertAlmostEqual(tr_sync._parse_de_number("1.234,56"), 1234.56)

    def test_fractional_shares(self):
        self.assertAlmostEqual(tr_sync._parse_de_number("1,035444"), 1.035444)

    def test_nbsp_and_spaces(self):
        self.assertAlmostEqual(tr_sync._parse_de_number("1\xa0000,50"), 1000.50)

    def test_none_or_invalid(self):
        self.assertIsNone(tr_sync._parse_de_number(None))
        self.assertIsNone(tr_sync._parse_de_number(""))
        self.assertIsNone(tr_sync._parse_de_number("abc"))


def _trade_event(isin="DE0005557508", shares_prefix="1,035444", price="31,18 €",
                 summe="31,29 €", amount_value=31.29, ts="2025-07-28T17:46:33.499+0000",
                 eventType="TRADING_TRADE_EXECUTED", status="EXECUTED", title="Deutsche Telekom"):
    return {
        "id": "evt",
        "timestamp": ts,
        "title": title,
        "amount": {"value": amount_value, "currency": "EUR"},
        "status": status,
        "eventType": eventType,
        "details": {
            "sections": [
                {"type": "header", "action": {"type": "instrumentDetail", "payload": isin}},
                {"type": "table", "title": "Übersicht", "data": [
                    {"title": "Asset", "detail": {"text": title}},
                    {"title": "Transaktion", "detail": {
                        "text": f"{shares_prefix} × {price}",
                        "displayValue": {"text": price, "prefix": f"{shares_prefix} × "},
                    }},
                    {"title": "Gebühr", "detail": {"text": "1,00 €"}},
                    {"title": "Summe", "detail": {"text": summe}},
                ]},
            ],
        },
    }


class ExtractTradeDetailsTests(unittest.TestCase):
    def test_extracts_from_real_shape(self):
        d = tr_sync._extract_trade_details(_trade_event())
        self.assertEqual(d["isin"], "DE0005557508")
        self.assertAlmostEqual(d["shares"], 1.035444)
        self.assertAlmostEqual(d["unit_price"], 31.18)

    def test_missing_details_returns_nones(self):
        d = tr_sync._extract_trade_details({"details": {"sections": []}})
        self.assertEqual(d, {"isin": None, "shares": None, "unit_price": None})

    def test_extracts_from_trade_invoice_format(self):
        # formato TRADE_INVOICE: sección propia 'Transaktion' con filas Anteile/Aktienkurs
        raw = {
            "eventType": "TRADE_INVOICE",
            "details": {"sections": [
                {"type": "header", "action": {"type": "instrumentDetail", "payload": "DE0005557508"}},
                {"type": "table", "title": "Übersicht", "data": [
                    {"title": "Status", "detail": {"text": "Ausgeführt"}},
                    {"title": "Orderart", "detail": {"text": "Kauf"}},
                ]},
                {"type": "table", "title": "Transaktion", "data": [
                    {"title": "Anteile", "detail": {"text": "1,035444"}},
                    {"title": "Aktienkurs", "detail": {"text": "25,10 €"}},
                    {"title": "Gebühr", "detail": {"text": "1,00 €"}},
                    {"title": "Gesamt", "detail": {"text": "26,99 €"}},
                ]},
            ]}
        }
        d = tr_sync._extract_trade_details(raw)
        self.assertEqual(d["isin"], "DE0005557508")
        self.assertAlmostEqual(d["shares"], 1.035444)
        self.assertAlmostEqual(d["unit_price"], 25.10)


class BuildLotsAndSalesTests(unittest.TestCase):
    def test_classifies_buy_and_sell_and_filters_year(self):
        events = [
            _trade_event(amount_value=-50.0, ts="2024-01-15T10:00:00+0000"),  # buy 2024
            _trade_event(amount_value=60.0, ts="2025-06-01T10:00:00+0000"),   # sell 2025
            _trade_event(amount_value=70.0, ts="2024-03-01T10:00:00+0000"),   # sell 2024 (ignorada)
        ]
        lots, sales, skipped = tr_sync._build_lots_and_sales(events, target_year=2025)
        self.assertEqual(len(lots), 1)
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0]["timestamp"].year, 2025)
        self.assertEqual(skipped, [])

    def test_skips_events_without_isin(self):
        ev = _trade_event()
        ev["details"]["sections"] = []
        _, _, skipped = tr_sync._build_lots_and_sales([ev], target_year=2025)
        self.assertEqual(len(skipped), 1)

    def test_excludes_canceled(self):
        ev = _trade_event(status="CANCELED", amount_value=60.0, ts="2025-06-01T10:00:00+0000")
        _, sales, _ = tr_sync._build_lots_and_sales([ev], target_year=2025)
        self.assertEqual(sales, [])

    def test_dedups_same_trade_across_event_types(self):
        # el mismo trade aparece en TRADING_TRADE_EXECUTED y TRADE_INVOICE — solo uno debe contar
        ev1 = _trade_event(amount_value=-50.0, ts="2024-01-15T10:00:00+0000",
                           eventType="TRADING_TRADE_EXECUTED")
        ev2 = _trade_event(amount_value=-50.0, ts="2024-01-15T10:00:00+0000",
                           eventType="TRADE_INVOICE")
        lots, _, _ = tr_sync._build_lots_and_sales([ev1, ev2], target_year=2025)
        self.assertEqual(len(lots), 1)

    def test_trade_invoice_buy_is_included(self):
        # Compra antigua en formato TRADE_INVOICE — tiene que entrar como buy lot
        raw = {
            "eventType": "TRADE_INVOICE",
            "id": "x",
            "timestamp": "2024-08-22T19:52:21+0000",
            "title": "Deutsche Telekom",
            "amount": {"value": -26.99},
            "status": "EXECUTED",
            "details": {"sections": [
                {"type": "header", "action": {"type": "instrumentDetail", "payload": "DE0005557508"}},
                {"type": "table", "title": "Transaktion", "data": [
                    {"title": "Anteile", "detail": {"text": "1,035444"}},
                    {"title": "Aktienkurs", "detail": {"text": "25,10 €"}},
                ]},
            ]}
        }
        lots, _, skipped = tr_sync._build_lots_and_sales([raw], target_year=2025)
        self.assertEqual(len(lots), 1)
        self.assertAlmostEqual(lots[0]["cost_eur"], 26.99)
        self.assertAlmostEqual(lots[0]["shares"], 1.035444)
        self.assertEqual(skipped, [])


def _gift_event(isin="US88160R1014", shares="0,006", unit_price="335,05 €",
                summe="2,00 €", ts="2024-12-03T11:15:18+0000",
                eventType="GIFTING_LOTTERY_PRIZE_ACTIVITY", title="Verlosung"):
    return {
        "id": "gift",
        "timestamp": ts,
        "title": title,
        "icon": f"logos/{isin}/v2",
        "avatar": {"asset": f"logos/{isin}/v2"},
        "eventType": eventType,
        "amount": None,  # los regalos no traen amount en root
        "status": "EXECUTED",
        "details": {"sections": [
            {"type": "header", "title": "Du hast 2,00 € gewonnen."},
            {"type": "table", "title": "Übersicht", "data": [
                {"title": "Status", "detail": {"text": "Ausgeführt"}},
                {"title": "Orderart", "detail": {"text": "Kauf"}},
                {"title": "Asset", "detail": {"text": "Tesla"}},
                {"title": "Aktien", "detail": {"text": shares}},
                {"title": "Aktienkurs", "detail": {"text": unit_price}},
            ]},
            {"type": "table", "title": "Transaktion", "data": [
                {"title": "Aktien", "detail": {"text": "2,01 €"}},  # valor, NO shares — trampa
                {"title": "Summe", "detail": {"text": summe}},
            ]},
        ]}
    }


class ExtractGiftDetailsTests(unittest.TestCase):
    def test_extracts_isin_shares_and_total_cost(self):
        d = tr_sync._extract_gift_details(_gift_event())
        self.assertEqual(d["isin"], "US88160R1014")
        self.assertAlmostEqual(d["shares"], 0.006)
        self.assertAlmostEqual(d["cost_eur"], 2.00)

    def test_falls_back_to_shares_times_price_if_summe_missing(self):
        raw = _gift_event()
        # quita Transaktion entera
        raw["details"]["sections"] = [s for s in raw["details"]["sections"]
                                       if s.get("title") != "Transaktion"]
        d = tr_sync._extract_gift_details(raw)
        self.assertAlmostEqual(d["cost_eur"], 0.006 * 335.05, places=4)

    def test_isin_falls_back_to_avatar_if_icon_missing(self):
        raw = _gift_event()
        raw["icon"] = None
        d = tr_sync._extract_gift_details(raw)
        self.assertEqual(d["isin"], "US88160R1014")

    def test_isin_extracted_from_header_data_icon_when_root_is_generic(self):
        # formato GIFTING_RECIPIENT_ACTIVITY: root icon='logos/timeline_gift/v2' genérico
        raw = {
            "eventType": "GIFTING_RECIPIENT_ACTIVITY",
            "icon": "logos/timeline_gift/v2",
            "avatar": None,
            "timestamp": "2024-12-22T07:49:21+0000",
            "title": "ETF-Geschenk",
            "amount": None,
            "status": "EXECUTED",
            "details": {"sections": [
                {"type": "header", "data": {"icon": "logos/LU1681048804/v2"}},
                {"type": "table", "title": "Übersicht", "data": [
                    {"title": "Asset", "detail": {"text": "S&P 500 EUR (Acc)"}},
                    {"title": "Aktien", "detail": {"text": "0.222311"}},
                    {"title": "Aktienkurs", "detail": {"text": "112,40 €"}},
                ]},
                {"type": "table", "title": "Transaktion", "data": [
                    {"title": "Aktien", "detail": {"text": "24,99 €"}},
                    {"title": "Summe", "detail": {"text": "25,00 €"}},
                ]},
            ]}
        }
        d = tr_sync._extract_gift_details(raw)
        self.assertEqual(d["isin"], "LU1681048804")
        self.assertAlmostEqual(d["shares"], 0.222311)
        self.assertAlmostEqual(d["cost_eur"], 25.00)


class GiftAsBuyLotIntegrationTests(unittest.TestCase):
    def test_gift_creates_buy_lot_with_cost_from_summe(self):
        events = [_gift_event()]
        lots, sales, skipped = tr_sync._build_lots_and_sales(events, target_year=2025)
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0]["isin"], "US88160R1014")
        self.assertAlmostEqual(lots[0]["shares"], 0.006)
        self.assertAlmostEqual(lots[0]["cost_eur"], 2.00)
        self.assertEqual(skipped, [])

    def test_gift_fifo_end_to_end(self):
        # Tesla regalo 2.00€ + venta 0.49€ → pérdida 1.51€
        gift = _gift_event()
        sell = _trade_event(
            eventType="TRADING_TRADE_EXECUTED",
            isin="US88160R1014",
            shares_prefix="0,006",
            price="248,95 €",
            summe="0,49 €",
            amount_value=0.49,
            ts="2025-07-07T08:33:07+0000",
            title="Tesla",
        )
        lots, sales, _ = tr_sync._build_lots_and_sales([gift, sell], target_year=2025)
        self.assertEqual(len(lots), 1)
        self.assertEqual(len(sales), 1)
        fifo = tr_sync._apply_fifo(lots, sales)
        self.assertAlmostEqual(fifo[0]["cost_basis"], 2.00, places=2)
        self.assertAlmostEqual(fifo[0]["gain_loss"], -1.51, places=2)


class FifoTests(unittest.TestCase):
    def _lot(self, isin, shares, cost_eur, ts):
        return {"timestamp": datetime.fromisoformat(ts), "isin": isin, "title": isin,
                "shares": shares, "cost_eur": cost_eur}

    def _sale(self, isin, shares, proceeds_eur, ts):
        return {"timestamp": datetime.fromisoformat(ts), "isin": isin, "title": isin,
                "shares": shares, "proceeds_eur": proceeds_eur}

    def test_single_lot_full_sale(self):
        lots = [self._lot("X", 10, 100, "2024-01-01")]
        sales = [self._sale("X", 10, 150, "2025-01-01")]
        r = tr_sync._apply_fifo(lots, sales)[0]
        self.assertAlmostEqual(r["cost_basis"], 100.0)
        self.assertAlmostEqual(r["gain_loss"], 50.0)
        self.assertEqual(r["shares_unmatched"], 0)

    def test_fifo_consumes_oldest_first(self):
        lots = [
            self._lot("X", 5, 50, "2023-01-01"),   # 10 €/share
            self._lot("X", 5, 100, "2024-01-01"),  # 20 €/share
        ]
        sales = [self._sale("X", 6, 200, "2025-01-01")]
        r = tr_sync._apply_fifo(lots, sales)[0]
        # 5 @ 10€ + 1 @ 20€ = 70€ coste; ganancia = 130€
        self.assertAlmostEqual(r["cost_basis"], 70.0)
        self.assertAlmostEqual(r["gain_loss"], 130.0)

    def test_unmatched_when_not_enough_buys(self):
        lots = [self._lot("X", 5, 50, "2023-01-01")]
        sales = [self._sale("X", 10, 200, "2025-01-01")]
        r = tr_sync._apply_fifo(lots, sales)[0]
        self.assertAlmostEqual(r["shares_unmatched"], 5.0)
        self.assertAlmostEqual(r["cost_basis"], 50.0)

    def test_different_isins_dont_mix(self):
        lots = [
            self._lot("A", 10, 100, "2023-01-01"),
            self._lot("B", 10, 200, "2023-01-02"),
        ]
        sales = [self._sale("A", 10, 150, "2025-01-01")]
        r = tr_sync._apply_fifo(lots, sales)[0]
        self.assertAlmostEqual(r["cost_basis"], 100.0)

    def test_multiple_sales_share_lots(self):
        lots = [self._lot("X", 10, 100, "2023-01-01")]  # 10 €/share
        sales = [
            self._sale("X", 4, 60, "2025-01-01"),  # cost 40, gain 20
            self._sale("X", 6, 90, "2025-02-01"),  # cost 60, gain 30
        ]
        rs = tr_sync._apply_fifo(lots, sales)
        self.assertAlmostEqual(rs[0]["cost_basis"], 40.0)
        self.assertAlmostEqual(rs[1]["cost_basis"], 60.0)
        self.assertAlmostEqual(sum(r["gain_loss"] for r in rs), 50.0)

    def test_fractional_shares(self):
        lots = [self._lot("X", 1.035444, 32.29, "2024-01-01")]
        sales = [self._sale("X", 1.035444, 31.29, "2025-01-01")]
        r = tr_sync._apply_fifo(lots, sales)[0]
        self.assertAlmostEqual(r["gain_loss"], -1.0, places=2)


class DividendCollectionTests(unittest.TestCase):
    def _dividend_event(self, isin="US47215P1066", ts="2025-04-29T15:12:57+0000",
                        subtitle="Bardividende", gross="1,73 €", tax="0,00 €",
                        gesamt="1,73 €", amount=1.73):
        return {
            "eventType": "SSP_CORPORATE_ACTION_CASH",
            "timestamp": ts,
            "subtitle": subtitle,
            "title": "JD.com (ADR)",
            "icon": f"logos/{isin}/v2",
            "amount": {"value": amount, "currency": "EUR"},
            "status": "EXECUTED",
            "details": {"sections": [
                {"type": "header"},
                {"type": "table", "title": "Übersicht", "data": [
                    {"title": "Event", "detail": {"text": subtitle}},
                ]},
                {"type": "table", "title": "Geschäft", "data": [
                    {"title": "Bruttoertrag", "detail": {"text": gross}},
                    {"title": "Steuer", "detail": {"text": tax}},
                    {"title": "Gesamt", "detail": {"text": gesamt}},
                ]},
            ]}
        }

    def test_extracts_gross_tax_net(self):
        d = tr_sync._extract_dividend_details(
            self._dividend_event(gross="2,00 €", tax="0,30 €", gesamt="1,70 €", amount=1.70)
        )
        self.assertAlmostEqual(d["gross"], 2.00)
        self.assertAlmostEqual(d["tax"], 0.30)
        self.assertAlmostEqual(d["net"], 1.70)

    def test_collect_filters_by_year_and_subtitle(self):
        events = [
            self._dividend_event(ts="2025-04-29T15:12:57+0000", subtitle="Bardividende"),
            self._dividend_event(ts="2024-10-03T08:01:11+0000", subtitle="Bardividende"),
            self._dividend_event(ts="2025-07-16T11:06:45+0000", subtitle="AlgoNoDividendo"),
        ]
        out = tr_sync._collect_dividends(events, year=2025)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["timestamp"].year, 2025)

    def test_aktienprämiendividende_is_counted(self):
        ev = self._dividend_event(subtitle="Aktienprämiendividende")
        out = tr_sync._collect_dividends([ev], year=2025)
        self.assertEqual(len(out), 1)


class InterestCollectionTests(unittest.TestCase):
    def _interest_event(self, ts="2025-04-01T06:27:31+0000", amount=12.34,
                        eventType="INTEREST_PAYOUT", title="Zinsen", subtitle="2,5 % p.a."):
        return {
            "eventType": eventType,
            "timestamp": ts,
            "title": title,
            "subtitle": subtitle,
            "amount": {"value": amount, "currency": "EUR"},
            "status": "EXECUTED",
        }

    def test_sums_interest_of_year(self):
        events = [
            self._interest_event(ts="2025-01-01T00:00:00+0000", amount=10.0),
            self._interest_event(ts="2025-02-01T00:00:00+0000", amount=5.50),
            self._interest_event(ts="2024-12-01T00:00:00+0000", amount=3.0),  # año distinto
        ]
        out = tr_sync._collect_interest(events, year=2025)
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(sum(i["amount"] for i in out), 15.50)

    def test_includes_interest_payout_created_too(self):
        ev = self._interest_event(eventType="INTEREST_PAYOUT_CREATED", amount=1.0)
        out = tr_sync._collect_interest([ev], year=2025)
        self.assertEqual(len(out), 1)


class BondIncomeCollectionTests(unittest.TestCase):
    def _bond_income_event(self, subtitle, amount, isin="XS0213101073",
                           ts="2025-02-24T15:13:26+0000"):
        return {
            "eventType": "SSP_CORPORATE_ACTION_CASH",
            "timestamp": ts,
            "subtitle": subtitle,
            "title": "Feb. 2025",
            "icon": f"logos/bondissuer-LEI/v2",
            "amount": {"value": amount},
            "status": "EXECUTED",
            "details": {"sections": [
                {"type": "header", "data": {"icon": f"logos/{isin}/v2"}},
            ]},
        }

    def _bond_buy_event(self, amount, isin="XS0213101073", ts="2024-10-17T16:17:15+0000"):
        return {
            "eventType": "TRADE_INVOICE",
            "timestamp": ts,
            "subtitle": "Kauforder",
            "title": "Feb. 2025",
            "icon": f"logos/bondissuer-LEI/v2",
            "amount": {"value": amount},
            "status": "EXECUTED",
            "details": {"sections": [
                {"type": "header", "action": {"type": "instrumentDetail", "payload": isin}},
            ]},
        }

    def test_real_bond_flow_computes_net_yield(self):
        # Reproducir el caso real del usuario: compra 2000,99 + cupón 106,07 + amortiz. 1928,51
        events = [
            self._bond_buy_event(-2000.99),
            self._bond_income_event("Zinszahlung", 106.07),
            self._bond_income_event("Endgültige Fälligkeit", 1928.51),
        ]
        out = tr_sync._collect_bond_income(events, year=2025)
        self.assertEqual(len(out), 1)
        b = out[0]
        self.assertEqual(b["isin"], "XS0213101073")
        self.assertAlmostEqual(b["cupones"], 106.07)
        self.assertAlmostEqual(b["amortizacion"], 1928.51)
        self.assertAlmostEqual(b["coste"], 2000.99)
        self.assertAlmostEqual(b["rendimiento_neto"], 33.59, places=2)

    def test_ignores_non_bond_subtitles(self):
        events = [self._bond_income_event("Bardividende", 1.0)]
        self.assertEqual(tr_sync._collect_bond_income(events, year=2025), [])

    def test_ignores_income_outside_target_year(self):
        events = [self._bond_income_event("Zinszahlung", 10.0, ts="2024-06-01T00:00:00+0000")]
        self.assertEqual(tr_sync._collect_bond_income(events, year=2025), [])


class SavebackCollectionTests(unittest.TestCase):
    def test_sums_savebacks_of_year(self):
        events = [
            {"eventType": "SAVEBACK_AGGREGATE", "timestamp": "2025-04-02T13:53:35+0000",
             "amount": {"value": -15.0}, "status": "EXECUTED", "title": "Core S&P 500 USD (Acc)"},
            {"eventType": "SAVEBACK_AGGREGATE", "timestamp": "2025-05-02T13:47:32+0000",
             "amount": {"value": -10.0}, "status": "EXECUTED", "title": "Core S&P 500 USD (Acc)"},
            {"eventType": "SAVEBACK_AGGREGATE", "timestamp": "2024-12-02T14:45:15+0000",
             "amount": {"value": -8.0}, "status": "EXECUTED", "title": "Core S&P 500 USD (Acc)"},
        ]
        out = tr_sync._collect_saveback(events, year=2025)
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(sum(s["amount"] for s in out), 25.0)


class RetentionsByCountryTests(unittest.TestCase):
    def test_groups_by_isin_country_prefix(self):
        dividends = [
            {"isin": "US47215P1066", "gross": 1.73, "tax": 0.26, "net": 1.47},
            {"isin": "US0378331005", "gross": 2.0, "tax": 0.30, "net": 1.70},
            {"isin": "DE0005557508", "gross": 0.93, "tax": 0.0, "net": 0.93},
        ]
        out = tr_sync._retentions_by_country(dividends)
        self.assertEqual(out["US"]["count"], 2)
        self.assertAlmostEqual(out["US"]["tax"], 0.56, places=2)
        self.assertAlmostEqual(out["DE"]["gross"], 0.93)


class IgnoreEventsTests(unittest.TestCase):
    def test_matches_title_case_insensitive(self):
        raw = {"title": "Jane Doe", "subtitle": ""}
        cfg = {"title_contains": ["jane doe"], "subtitle_contains": []}
        self.assertTrue(tr_sync._matches_ignore(raw, cfg))

    def test_matches_subtitle(self):
        raw = {"title": "X", "subtitle": "transferencia imagin"}
        cfg = {"title_contains": [], "subtitle_contains": ["imagin"]}
        self.assertTrue(tr_sync._matches_ignore(raw, cfg))

    def test_no_match_returns_false(self):
        raw = {"title": "Mercadona", "subtitle": ""}
        cfg = {"title_contains": ["imagin"], "subtitle_contains": []}
        self.assertFalse(tr_sync._matches_ignore(raw, cfg))

    def test_empty_config_does_not_match(self):
        raw = {"title": "X", "subtitle": "Y"}
        self.assertFalse(tr_sync._matches_ignore(raw, {}))


class A1RangeParserTests(unittest.TestCase):
    def test_parses_simple_column_range(self):
        self.assertEqual(tr_sync._parse_a1_column_range("C2:C8"), ("C", 2, 8))

    def test_parses_multi_letter_column(self):
        self.assertEqual(tr_sync._parse_a1_column_range("AA1:AA10"), ("AA", 1, 10))

    def test_returns_none_for_multi_column_range(self):
        self.assertEqual(tr_sync._parse_a1_column_range("A1:B5"), (None, None, None))

    def test_returns_none_for_invalid_string(self):
        self.assertEqual(tr_sync._parse_a1_column_range("not a range"), (None, None, None))
        self.assertEqual(tr_sync._parse_a1_column_range(""), (None, None, None))
        self.assertEqual(tr_sync._parse_a1_column_range(None), (None, None, None))


class ColumnLetterToIndexTests(unittest.TestCase):
    def test_basic_letters(self):
        self.assertEqual(tr_sync._column_letter_to_index("A"), 1)
        self.assertEqual(tr_sync._column_letter_to_index("Z"), 26)

    def test_two_letters(self):
        self.assertEqual(tr_sync._column_letter_to_index("AA"), 27)
        self.assertEqual(tr_sync._column_letter_to_index("AZ"), 52)
        self.assertEqual(tr_sync._column_letter_to_index("BA"), 53)


class LedgerLayoutTests(unittest.TestCase):
    """Verifica que sync_to_sheet con layout='ledger' escribe correctamente."""

    def _make_worksheet(self, existing_a_col=None, existing_row1=None,
                        col_values_by_idx=None):
        """Mock simple de gspread.Worksheet que captura update_cells/update_cell."""
        class FakeWS:
            def __init__(self):
                # `col_a` se mantiene por retrocompat: equivale a col_values_by_idx[1].
                base_cols = dict(col_values_by_idx or {})
                if existing_a_col and 1 not in base_cols:
                    base_cols[1] = list(existing_a_col)
                self.col_data = base_cols
                self.row1 = list(existing_row1 or [])
                self.update_cell_calls = []   # [(row, col, value)]
                self.update_cells_calls = []  # [list of Cell-like]
            def col_values(self, col):
                return list(self.col_data.get(col, []))
            def row_values(self, row):
                if row == 1:
                    return self.row1
                return []
            def update_cell(self, row, col, value):
                self.update_cell_calls.append((row, col, value))
            def update_cells(self, cells, value_input_option=None):
                self.update_cells_calls.append(list(cells))
        return FakeWS()

    def _make_spreadsheet(self, ws):
        class FakeSS:
            def worksheet(self, name):
                return ws
        return FakeSS()

    def _make_tx(self, idx, ts="2026-04-15T10:00:00+0000", importe=10.50, concepto="Mercadona"):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return {
            "id": f"tx-{idx}",
            "ts": datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Madrid")),
            "month_key": (2026, 4),
            "concepto": concepto,
            "importe": importe,
            "type": "CARD_TRANSACTION",
            "raw_value": -importe,
        }

    def test_ledger_writes_headers_when_missing(self):
        ws = self._make_worksheet(existing_a_col=[], existing_row1=[])
        ss = self._make_spreadsheet(ws)
        ids = tr_sync._sync_ledger_layout(ss, tr_sync.EXPENSES_SHEET, [self._make_tx(1)], dry_run=False)
        # 3 update_cell para los 3 headers (default columnas 1,2,3)
        self.assertEqual(len(ws.update_cell_calls), 3)
        self.assertEqual({(r, c) for r, c, _ in ws.update_cell_calls}, {(1, 1), (1, 2), (1, 3)})
        self.assertEqual(len(ws.update_cells_calls), 1)  # los datos
        self.assertEqual(ids, ["tx-1"])

    def test_ledger_skips_headers_if_already_present(self):
        ws = self._make_worksheet(
            col_values_by_idx={1: [tr_sync.LEDGER_HEADERS[0]]},
            existing_row1=tr_sync.LEDGER_HEADERS,
        )
        ss = self._make_spreadsheet(ws)
        tr_sync._sync_ledger_layout(ss, tr_sync.EXPENSES_SHEET, [self._make_tx(1)], dry_run=False)
        # Headers ya están: no se llama update_cell
        self.assertEqual(ws.update_cell_calls, [])
        # Sí se escriben los datos vía update_cells
        self.assertEqual(len(ws.update_cells_calls), 1)

    def test_ledger_appends_after_existing_rows(self):
        ws = self._make_worksheet(
            col_values_by_idx={1: [tr_sync.LEDGER_HEADERS[0], "2026-04-01", "2026-04-02"]},
            existing_row1=tr_sync.LEDGER_HEADERS,
        )
        ss = self._make_spreadsheet(ws)
        tr_sync._sync_ledger_layout(ss, tr_sync.EXPENSES_SHEET, [self._make_tx(1)], dry_run=False)
        # Próxima fila libre = 4. Verificamos que las celdas escritas están en fila 4.
        cells = ws.update_cells_calls[0]
        rows_written = {c.row for c in cells}
        self.assertEqual(rows_written, {4})

    def test_ledger_dry_run_writes_nothing(self):
        ws = self._make_worksheet()
        ss = self._make_spreadsheet(ws)
        ids = tr_sync._sync_ledger_layout(ss, tr_sync.EXPENSES_SHEET, [self._make_tx(1)], dry_run=True)
        self.assertEqual(ws.update_cell_calls, [])
        self.assertEqual(ws.update_cells_calls, [])
        self.assertEqual(ids, [])

    def test_ledger_writes_rows_in_chronological_order(self):
        ws = self._make_worksheet()
        ss = self._make_spreadsheet(ws)
        txs = [
            self._make_tx(1, ts="2026-04-20T10:00:00+0000", importe=20),
            self._make_tx(2, ts="2026-04-10T10:00:00+0000", importe=10),
            self._make_tx(3, ts="2026-04-15T10:00:00+0000", importe=15),
        ]
        ids = tr_sync._sync_ledger_layout(ss, tr_sync.EXPENSES_SHEET, txs, dry_run=False)
        self.assertEqual(ids, ["tx-2", "tx-3", "tx-1"])
        # Inspeccionamos las celdas: ordenadas por fila ascendente, las dates aumentan
        cells = ws.update_cells_calls[-1]
        date_col_idx = tr_sync._column_letter_to_index(tr_sync.LEDGER_COLUMNS["date"])
        date_cells = sorted([c for c in cells if c.col == date_col_idx], key=lambda c: c.row)
        self.assertEqual([c.value for c in date_cells], ["2026-04-10", "2026-04-15", "2026-04-20"])


class LedgerCustomColumnsTests(unittest.TestCase):
    """Verifica que LEDGER_COLUMNS funciona con columnas no-default (no A/B/C)."""

    def setUp(self):
        # Salvar y monkeypatch LEDGER_COLUMNS para B/D/F (columnas no contiguas)
        self._saved = dict(tr_sync.LEDGER_COLUMNS)
        tr_sync.LEDGER_COLUMNS = {"date": "B", "concept": "D", "amount": "F"}

    def tearDown(self):
        tr_sync.LEDGER_COLUMNS = self._saved

    def _make_ws_and_ss(self, col_values=None, row1=None):
        class FakeWS:
            def __init__(self):
                self.col_data = dict(col_values or {})
                self.row1 = list(row1 or [])
                self.update_cell_calls = []
                self.update_cells_calls = []
            def col_values(self, col):
                return list(self.col_data.get(col, []))
            def row_values(self, row):
                return self.row1 if row == 1 else []
            def update_cell(self, row, col, value):
                self.update_cell_calls.append((row, col, value))
            def update_cells(self, cells, value_input_option=None):
                self.update_cells_calls.append(list(cells))
        ws = FakeWS()
        class FakeSS:
            def worksheet(self, name):
                return ws
        return ws, FakeSS()

    def _make_tx(self, idx=1):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return {
            "id": f"tx-{idx}",
            "ts": datetime.fromisoformat("2026-04-15T10:00:00+00:00").astimezone(ZoneInfo("Europe/Madrid")),
            "month_key": (2026, 4),
            "concepto": "Mercadona",
            "importe": 12.34,
            "type": "CARD_TRANSACTION",
            "raw_value": -12.34,
        }

    def test_writes_to_configured_columns_not_abc(self):
        ws, ss = self._make_ws_and_ss()
        tr_sync._sync_ledger_layout(ss, tr_sync.EXPENSES_SHEET, [self._make_tx()], dry_run=False)
        # Headers escritos en columnas 2, 4, 6
        cols_written = {c for _, c, _ in ws.update_cell_calls}
        self.assertEqual(cols_written, {2, 4, 6})
        # Datos también escritos en esas mismas columnas
        cells = ws.update_cells_calls[0]
        self.assertEqual({c.col for c in cells}, {2, 4, 6})
        # Ninguna celda escrita en col A
        self.assertNotIn(1, {c.col for c in cells})

    def test_first_empty_row_uses_date_column_not_col_a(self):
        # col B (date) tiene 5 valores → próxima fila libre = 6
        ws, ss = self._make_ws_and_ss(
            col_values={2: ["Fecha", "2026-04-10", "2026-04-11", "2026-04-12", "2026-04-13"]},
            row1=["", "Fecha", "", "Concepto", "", "Importe"],
        )
        tr_sync._sync_ledger_layout(ss, tr_sync.EXPENSES_SHEET, [self._make_tx()], dry_run=False)
        cells = ws.update_cells_calls[0]
        rows_written = {c.row for c in cells}
        self.assertEqual(rows_written, {6})


class MonthHeaderFormatTests(unittest.TestCase):
    """Verifica que los patrones MONTH_HEADER_AMOUNT/CONCEPT son configurables."""

    def test_default_format_is_spanish(self):
        self.assertEqual(tr_sync.MONTH_HEADER_AMOUNT, "{month} {year}")
        self.assertEqual(tr_sync.MONTH_HEADER_CONCEPT, "Concepto {month}")

    def test_format_can_use_year_and_month_placeholders(self):
        # Con default: "abril 2026" → header importe; "Concepto abril" → header concepto
        amount = tr_sync.MONTH_HEADER_AMOUNT.format(month="abril", year=2026)
        concept = tr_sync.MONTH_HEADER_CONCEPT.format(month="abril", year=2026)
        self.assertEqual(amount, "abril 2026")
        self.assertEqual(concept, "Concepto abril")


if __name__ == "__main__":
    unittest.main()
