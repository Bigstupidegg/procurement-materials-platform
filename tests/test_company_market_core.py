from __future__ import annotations

from datetime import date
import unittest

from scripts.company_market_core import (
    DataContractError,
    MarketQuote,
    extract_table_value,
    find_previous_valid_values,
    find_sheet_row,
    require_success,
    validate_sheet_layout,
)


AUTHORITATIVE_MAIN_LAYOUT = [
    ["日期", "銅 COPPER", "銅 COPPER", "電解銅 Copper Cathode", "鋁 ALUMINIUM", "鉛 LEAD", "鎳 NICKEL", "錫 TIN", "鋅 ZINC", "油", "銀", "黃金"],
    ["", "USD / TONNE", "USD / TONNE", "CNY / TONNE", "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / BBL", "CENT / OUNCE", "USD / OUNCE"],
    ["資料來源", "LME OFFER", "LME OFFER", "SMM", "LME", "LME", "LME", "LME", "LME", "yfinance BZ=F", "yfinance SI=F", "yfinance GC=F"],
    ["", "現貨", "期貨(3月)", "現貨", "現貨", "現貨", "現貨", "現貨", "現貨", "期貨", "期貨", "期貨"],
]

EXPECTED_MAIN_LAYOUT = [
    AUTHORITATIVE_MAIN_LAYOUT[0],
    [None] + AUTHORITATIVE_MAIN_LAYOUT[1][1:],
    AUTHORITATIVE_MAIN_LAYOUT[2],
    [None] + AUTHORITATIVE_MAIN_LAYOUT[3][1:],
]


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

    def test_sheet_row_matches_authoritative_full_date(self) -> None:
        values = [
            "日期", "", "資料來源", "",
            "2026/08/11", "2026/08/12", "2026/08/13", "2026/08/14",
            "2026/08/17", "2026/08/18", "2026/08/19", "2026/08/20",
            "2026/08/21", "2026/08/24", "2026/08/25", "2026/08/26",
        ]
        self.assertEqual(find_sheet_row(values, date(2026, 8, 26)), 16)

    def test_sheet_row_rejects_day_only_values(self) -> None:
        values = ["日期", "24", "25", "26", "27"]
        self.assertIsNone(find_sheet_row(values, date(2026, 8, 26)))

    def test_sheet_row_rejects_non_authoritative_date_separators(self) -> None:
        values = ["日期", "2026-08-26", "2026.08.26"]
        self.assertIsNone(find_sheet_row(values, date(2026, 8, 26)))

    def test_sheet_row_fails_when_full_date_is_duplicated(self) -> None:
        with self.assertRaises(DataContractError):
            find_sheet_row(
                ["日期", "2026/08/26", "2026/08/26"],
                date(2026, 8, 26),
            )

    def test_previous_valid_price_is_found_independently_per_column(self) -> None:
        rows = [
            ["日期", "銅", "銅3M", "電解銅", "鋁", "鉛", "鎳", "錫", "鋅", "油", "銀", "金"],
            [],
            ["資料來源"],
            [],
            ["2026/08/24", 14100, 14020, 108000, 3150, 1850, 16500, 55000, 3900, 87.0, 6800, 4660],
            ["2026/08/25", 14300, 14220, 108400, 3170, "", "", "", "", 88.58, 6868, 4694.2],
            ["2026/08/26", 14425, 14298, 109575, 3188, 1867, 16780, 55700, 3987, 84.93, 6854, 4679.8],
        ]

        previous = find_previous_valid_values(rows, target_row=7)

        self.assertEqual(previous[2].row, 6)
        self.assertEqual(previous[2].value, 14300.0)
        self.assertEqual(previous[6].row, 5)
        self.assertEqual(previous[6].value, 1850.0)
        self.assertEqual(previous[7].row, 5)
        self.assertEqual(previous[7].value, 16500.0)
        self.assertEqual(previous[8].row, 5)
        self.assertEqual(previous[8].value, 55000.0)
        self.assertEqual(previous[9].row, 5)
        self.assertEqual(previous[9].value, 3900.0)
        self.assertEqual(previous[10].row, 6)
        self.assertEqual(previous[10].value, 88.58)

    def test_previous_valid_price_skips_invalid_date_and_non_numeric_cells(self) -> None:
        rows = [
            ["日期", "銅"],
            [],
            ["資料來源"],
            [],
            ["2026/08/22", "13,900"],
            ["2026-08-24", "14,100"],
            ["2026/08/25", "N/A"],
            ["2026/08/26", "14,425"],
        ]

        previous = find_previous_valid_values(
            rows,
            target_row=8,
            value_columns=(2,),
        )

        self.assertEqual(previous[2].row, 5)
        self.assertEqual(previous[2].value, 13900.0)

    def test_authoritative_company_sheet_layout_contract_passes(self) -> None:
        validate_sheet_layout(AUTHORITATIVE_MAIN_LAYOUT, EXPECTED_MAIN_LAYOUT)

    def test_sheet_layout_fails_if_copper_source_column_shifts(self) -> None:
        actual = [row[:] for row in AUTHORITATIVE_MAIN_LAYOUT]
        actual[2][1] = "LME"
        with self.assertRaises(DataContractError):
            validate_sheet_layout(actual, EXPECTED_MAIN_LAYOUT)

    def test_sheet_layout_fails_if_smm_unit_changes(self) -> None:
        actual = [row[:] for row in AUTHORITATIVE_MAIN_LAYOUT]
        actual[1][3] = "USD / TONNE"
        with self.assertRaises(DataContractError):
            validate_sheet_layout(actual, EXPECTED_MAIN_LAYOUT)

    def test_sheet_layout_fails_if_brent_source_changes(self) -> None:
        actual = [row[:] for row in AUTHORITATIVE_MAIN_LAYOUT]
        actual[2][9] = "鉅亨 倫敦布蘭特"
        with self.assertRaises(DataContractError):
            validate_sheet_layout(actual, EXPECTED_MAIN_LAYOUT)

    def test_sheet_layout_fails_if_yfinance_is_mislabeled_as_spot(self) -> None:
        actual = [row[:] for row in AUTHORITATIVE_MAIN_LAYOUT]
        actual[3][9] = "現貨"
        with self.assertRaises(DataContractError):
            validate_sheet_layout(actual, EXPECTED_MAIN_LAYOUT)

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
