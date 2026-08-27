from datetime import date
import unittest

from scripts.company_market_row_safety import RowSafetyError, resolve_safe_target_row


class CompanyMarketRowSafetyTests(unittest.TestCase):
    def test_occupied_row_with_day_only_date_fails_closed(self):
        rows = [
            ["2026/08/25", 14425, 14298, 108440],
            ["26", 14425, 14298, 109575],
        ]
        with self.assertRaises(RowSafetyError):
            resolve_safe_target_row(rows, date(2026, 8, 27))

    def test_today_missing_appends_after_last_used_row(self):
        rows = [
            ["2026/08/25", 14425],
            ["2026/08/26", 14525],
        ]
        self.assertEqual(
            resolve_safe_target_row(rows, date(2026, 8, 27)),
            (7, True),
        )

    def test_existing_today_is_reused(self):
        rows = [
            ["2026/08/25", 14425],
            ["2026/08/26", 14525],
            ["2026/08/27", 14600],
        ]
        self.assertEqual(
            resolve_safe_target_row(rows, date(2026, 8, 27)),
            (7, False),
        )

    def test_duplicate_today_fails_closed(self):
        rows = [
            ["2026/08/27", 14525],
            ["2026/08/27", 14600],
        ]
        with self.assertRaises(RowSafetyError):
            resolve_safe_target_row(rows, date(2026, 8, 27))

    def test_invalid_a_only_row_is_not_overwritten(self):
        rows = [
            ["2026/08/25", 14425],
            ["26"],
        ]
        self.assertEqual(
            resolve_safe_target_row(rows, date(2026, 8, 27)),
            (7, True),
        )


if __name__ == "__main__":
    unittest.main()
