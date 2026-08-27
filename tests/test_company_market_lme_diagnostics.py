from __future__ import annotations

import unittest

from scripts.company_market_core import DataContractError
from scripts.company_market_collector import (
    TableSnapshot,
    extract_lme_offer_from_snapshots,
    fetch_lme_offer_with_retry,
    format_table_diagnostics,
)


class LmeDiagnosticsTests(unittest.TestCase):
    def test_extracts_cash_offer_from_semantic_table(self):
        snapshots = (
            TableSnapshot(
                0,
                ("CONTRACT", "BID", "OFFER"),
                ("Cash", "3-month"),
                (("Cash", "1864.00", "1866.00"), ("3-month", "1905", "1907")),
            ),
        )
        self.assertEqual(extract_lme_offer_from_snapshots(snapshots, "Cash"), 1866.0)

    def test_contract_failure_reports_tables_headers_and_row_labels(self):
        snapshots = (
            TableSnapshot(0, ("CONTRACT", "BID"), ("Cash",), (("Cash", "1864"),)),
            TableSnapshot(1, ("STOCKS", "AMOUNT"), ("Opening Stock",), (("Opening Stock", "1"),)),
        )
        with self.assertRaises(DataContractError) as context:
            extract_lme_offer_from_snapshots(snapshots, "Cash")
        diagnostics = format_table_diagnostics(snapshots)
        self.assertIn("header='OFFER'", str(context.exception))
        self.assertIn("headers=['CONTRACT', 'BID']", diagnostics)
        self.assertIn("row_labels=['Cash']", diagnostics)

    def test_transient_failure_retries_and_reports_attempt_count(self):
        calls = []

        def fetcher(driver, url, term):
            calls.append((url, term))
            if len(calls) == 1:
                raise DataContractError("temporary table timeout")
            return 1866.0

        value, attempts = fetch_lme_offer_with_retry(
            object(), "https://www.lme.com/lead", "Cash",
            retry_delays=(), fetcher=fetcher,
        )
        self.assertEqual(value, 1866.0)
        self.assertEqual(attempts, 2)

    def test_retry_exhaustion_includes_each_failure(self):
        def fetcher(driver, url, term):
            raise DataContractError("Cash row not ready")

        with self.assertRaises(DataContractError) as context:
            fetch_lme_offer_with_retry(
                object(), "https://www.lme.com/lead", "Cash",
                max_attempts=3, retry_delays=(), fetcher=fetcher,
            )
        message = str(context.exception)
        self.assertIn("attempt 1/3", message)
        self.assertIn("attempt 3/3", message)


if __name__ == "__main__":
    unittest.main()
