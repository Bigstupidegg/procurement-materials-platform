from __future__ import annotations

from datetime import date
import unittest

from scripts.company_market_core import (
    DataContractError,
    MarketQuote,
    extract_first_table_value,
    extract_table_value,
    find_sheet_row,
    require_success,
    validate_sheet_layout,
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
        rows = [["1#电解铜", "108,400", "108,800", "108,600", "+200"]]
        value = extract_table_value(
            headers,
            rows,
            row_terms=("1#电解铜",),
            value_headers=("均价", "平均价"),
            minimum=50000,
        )
        self.assertEqual(value, 108600.0)

    def test_cnyes_close_selects_close_header(self) -> None:
        headers = ["日期", "收盤價", "漲跌", "漲%", "開盤價"]
        rows = [
            ["20260826", "69.235", "+1.2", "+1.76", "68.100"],
            ["20260825", "68.682", "-0.1", "-0.14", "68.900"],
        ]
        value = extract_first_table_value(
            headers,
            rows,
            value_headers=("收盤價", "close"),
            minimum=1,
        )
        self.assertEqual(value, 69.235)

    def test_sheet_row_matches_exact_day_in_existing_monthly_sheet(self) -> None:
        values = ["日期", "24", "25", "26", "27"]
        self.assertEqual(find_sheet_row(values, date(2026, 8, 26)), 4)

    def test_sheet_row_matches_actual_uploaded_layout(self) -> None:
        # A1:A16 from the supplied company workbook: four header rows followed by
        # business-day rows.  The 26th is row 16, not row 17.
        values = [
            "日期", "", "資料來源", "",
            11, 12, 13, 14, 17, 18, 19, 20, 21, 24, 25, "26",
        ]
        self.assertEqual(find_sheet_row(values, date(2026, 8, 26)), 16)

    def test_sheet_row_prefers_full_date(self) -> None:
        values = ["日期", "26", "2026-08-26", "27"]
        self.assertEqual(find_sheet_row(values, date(2026, 8, 26)), 3)

    def test_sheet_row_fails_when_day_only_is_ambiguous(self) -> None:
        with self.assertRaises(DataContractError):
            find_sheet_row(["日期", "26", "26"], date(2026, 8, 26))

    def test_actual_company_sheet_layout_contract_passes(self) -> None:
        actual = [
            ["日期", "銅 COPPER", "銅 COPPER", "電解銅 Copper Cathode", "鋁 ALUMINIUM", "鉛 LEAD", "鎳 NICKEL", "錫 TIN", "鋅 ZINC", "油", "銀", "黃金"],
            ["", "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / DRUM", "CENT / OUNCE", "USD / OUNCE"],
            ["資料來源", "LME OFFER", "LME OFFER", "SMM", "LME", "LME", "LME", "LME", "LME", "鉅亨 倫敦布蘭特", "鉅亨 紐約白銀", "鉅亨 紐約黃金"],
            ["", "現貨", "期貨(3月)", "現貨", "現貨", "現貨", "現貨", "現貨", "現貨", "現貨", "現貨", "現貨"],
        ]
        expected = [
            actual[0],
            [None, "USD / TONNE", "USD / TONNE", None, "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / TONNE", None, "CENT / OUNCE", "USD / OUNCE"],
            actual[2],
            [None] + actual[3][1:],
        ]
        validate_sheet_layout(actual, expected)

    def test_sheet_layout_fails_if_copper_source_column_shifts(self) -> None:
        actual = [
            ["日期", "銅 COPPER", "銅 COPPER"],
            ["", "USD / TONNE", "USD / TONNE"],
            ["資料來源", "LME", "LME OFFER"],
            ["", "現貨", "期貨(3月)"],
        ]
        expected = [
            ["日期", "銅 COPPER", "銅 COPPER"],
            [None, "USD / TONNE", "USD / TONNE"],
            ["資料來源", "LME OFFER", "LME OFFER"],
            [None, "現貨", "期貨(3月)"],
        ]
        with self.assertRaises(DataContractError):
            validate_sheet_layout(actual, expected)

    def test_required_copper_cash_must_succeed(self) -> None:
        quote = MarketQuote(
            key="copper_lme_cash",
            name="銅 COPPER",
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
