from __future__ import annotations

from datetime import date
import unittest

from scripts.company_market_core import (
    DataContractError,
    MarketQuote,
    extract_table_value,
    find_sheet_row,
    require_success,
)


class CompanyMarketCoreTests(unittest.TestCase):
    def test_lme_offer_uses_header_name_not_fixed_position(self) -> None:
        headers = ["Contract", "Open", "Bid", "Offer", "Change"]
        rows = [
            ["Cash", "13,400", "13,520", "13,545.50", "+10"],
            ["3-month", "13,300", "13,410", "13,430.00", "+8"],
        ]
        value = extract_table_value(
            headers,
            rows,
            row_terms=("Cash",),
            value_headers=("Offer",),
            minimum=100,
        )
        self.assertEqual(value, 13545.50)

    def test_lme_offer_fails_closed_when_offer_header_missing(self) -> None:
        with self.assertRaises(DataContractError):
            extract_table_value(
                ["Contract", "Bid", "Settlement"],
                [["Cash", "13,520", "13,545"]],
                row_terms=("Cash",),
                value_headers=("Offer",),
            )

    def test_smm_average_selects_average_not_first_price(self) -> None:
        headers = ["品名", "最低价", "最高价", "均价", "涨跌"]
        rows = [["1#电解铜", "78,000", "78,300", "78,150", "+200"]]
        value = extract_table_value(
            headers,
            rows,
            row_terms=("1#电解铜",),
            value_headers=("均价", "平均价"),
            minimum=50000,
        )
        self.assertEqual(value, 78150.0)

    def test_sheet_row_matches_exact_day_in_existing_monthly_sheet(self) -> None:
        values = ["日期", "24", "25", "26", "27"]
        self.assertEqual(find_sheet_row(values, date(2026, 8, 26)), 4)

    def test_sheet_row_prefers_full_date(self) -> None:
        values = ["日期", "26", "2026-08-26", "27"]
        self.assertEqual(find_sheet_row(values, date(2026, 8, 26)), 3)

    def test_sheet_row_fails_when_day_only_is_ambiguous(self) -> None:
        with self.assertRaises(DataContractError):
            find_sheet_row(["日期", "26", "26"], date(2026, 8, 26))

    def test_required_copper_cash_must_succeed(self) -> None:
        quote = MarketQuote(
            key="copper_lme_cash",
            name="銅_LME_現貨",
            source="London Metal Exchange",
            instrument="Copper",
            term="Cash",
            quote_type="OFFER",
            currency="USD",
            unit="USD/MT",
            value=None,
            fetched_at="2026-08-26T14:00:00+08:00",
            status="ERROR",
            error="offer header missing",
        )
        with self.assertRaises(DataContractError):
            require_success({"copper_lme_cash": quote}, ("copper_lme_cash",))


if __name__ == "__main__":
    unittest.main()
