from __future__ import annotations

import unittest

from scripts.company_market_core import DataContractError
from scripts.company_market_collector import (
    TableSnapshot,
    expected_lme_identity,
    extract_lme_data_valid_date,
    extract_lme_offer_from_snapshots,
    extract_smm_selected_row_date,
    extract_smm_quote_date,
    fetch_lme_offer_with_retry,
    format_row_timeline,
    format_table_diagnostics,
    lme_console_diagnostics,
    lme_network_diagnostics,
    lme_network_failure_category,
    lme_page_flags,
    lme_semantic_diagnostics,
    normalize_market_date,
    parse_lme_performance_entries,
    rows_transitioned_to_nonzero,
    sanitize_lme_text,
    sanitize_lme_resource_url,
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

    def test_sanitized_semantic_diagnostic_has_shape_but_no_quote_value(self):
        snapshots = (
            TableSnapshot(0, ("CONTRACT", "OFFER"), ("Cash",), (("Cash", "1866.00"),)),
            TableSnapshot(1, (), (), ()),
        )
        diagnostic = lme_semantic_diagnostics(snapshots)
        self.assertIn("table_count=2", diagnostic)
        self.assertIn("row_counts=[1, 0]", diagnostic)
        self.assertIn("cash=True", diagnostic)
        self.assertIn("offer=True", diagnostic)
        self.assertNotIn("1866", diagnostic)

    def test_text_timeline_identity_and_page_flags_are_sanitized(self):
        self.assertEqual(sanitize_lme_text("Lead 1866.25\nOffer"), "Lead <number> Offer")
        self.assertEqual(expected_lme_identity("https://www.lme.com/x/lme-nickel#Summary"), "nickel")
        self.assertEqual(
            format_row_timeline(((0.0, (0, 0)), (1.25, (2, 0)))),
            "[0.0s:[0, 0], 1.2s:[2, 0]]",
        )
        self.assertTrue(rows_transitioned_to_nonzero(((0.0, (0, 0)), (1.25, (2, 0)))))
        self.assertFalse(rows_transitioned_to_nonzero(((0.0, (2, 0)),)))
        flags = lme_page_flags("Cookie consent; verify you are human; access denied")
        self.assertIn("cookie=True", flags)
        self.assertIn("challenge=True", flags)
        self.assertIn("access_denied=True", flags)

    def test_console_diagnostic_reports_category_not_raw_message(self):
        class Driver:
            def get_log(self, kind):
                self.kind = kind
                return [{"level": "SEVERE", "source": "javascript", "message": "price 1866 failed"}]

        diagnostic = lme_console_diagnostics(Driver())
        self.assertIn("console_errors=1", diagnostic)
        self.assertIn("javascript", diagnostic)
        self.assertNotIn("1866", diagnostic)
        self.assertNotIn("failed", diagnostic)

    def test_contract_failure_text_redacts_quote_like_values(self):
        diagnostic = sanitize_lme_text(
            "contract check failed: OFFER value 1866.25 is below minimum 2000",
            limit=500,
        )
        self.assertIn("OFFER value <number>", diagnostic)
        self.assertNotIn("1866", diagnostic)
        self.assertNotIn("2000", diagnostic)

    def test_network_url_sanitization_keeps_only_host_and_path_category(self):
        diagnostic = sanitize_lme_resource_url(
            "https://user:secret@www.lme.com/api/price/1866?token=abc123&session=xyz#quote"
        )
        self.assertEqual(diagnostic, "host=www.lme.com; path_category=api")
        for forbidden in ("user", "secret", "1866", "token", "abc123", "session", "xyz", "#"):
            self.assertNotIn(forbidden, diagnostic)

    def test_network_summary_reports_safe_statuses_and_failure_categories(self):
        def event(method, params):
            return {"message": __import__("json").dumps({"message": {"method": method, "params": params}})}

        entries = (
            event("Network.responseReceived", {"type": "Document", "response": {"status": 200, "url": "https://www.lme.com/metals?Authorization=hidden"}}),
            event("Network.responseReceived", {"type": "XHR", "response": {"status": 429, "url": "https://www.lme.com/api/prices?token=hidden&value=1866"}}),
            event("Network.responseReceived", {"type": "Fetch", "response": {"status": 503, "url": "https://www.lme.com/api/feed#private"}}),
            event("Network.loadingFailed", {"type": "XHR", "errorText": "net::ERR_TIMED_OUT token=hidden"}),
        )
        diagnostic = parse_lme_performance_entries(entries)
        self.assertIn("'200': 1", diagnostic)
        self.assertIn("'429': 1", diagnostic)
        self.assertIn("'503': 1", diagnostic)
        self.assertIn("'2xx': 1", diagnostic)
        self.assertIn("'4xx': 1", diagnostic)
        self.assertIn("'5xx': 1", diagnostic)
        self.assertIn("dynamic_responses=2", diagnostic)
        self.assertIn("dynamic_failed=1", diagnostic)
        self.assertIn("'timeout': 1", diagnostic)
        for forbidden in ("Authorization", "hidden", "token", "1866", "private", "ERR_TIMED_OUT"):
            self.assertNotIn(forbidden, diagnostic)

    def test_network_failure_categories_are_allowlisted(self):
        self.assertEqual(lme_network_failure_category("ERR_NAME_NOT_RESOLVED"), "dns")
        self.assertEqual(lme_network_failure_category("ERR_CONNECTION_REFUSED"), "connection_refused")
        self.assertEqual(lme_network_failure_category("ERR_CONNECTION_RESET"), "connection_reset")
        self.assertEqual(lme_network_failure_category("ERR_BLOCKED_BY_CLIENT"), "blocked")
        self.assertEqual(lme_network_failure_category("ERR_ABORTED"), "aborted")
        self.assertEqual(lme_network_failure_category("unrecognised 1866 token"), "other")

    def test_network_diagnostics_does_not_log_raw_entry_content(self):
        class Driver:
            def get_log(self, kind):
                self.kind = kind
                return [{"message": "not-json Cookie=secret Authorization=private price=1866"}]

        diagnostic = lme_network_diagnostics(Driver())
        self.assertIn("network_available=True", diagnostic)
        for forbidden in ("Cookie", "secret", "Authorization", "private", "1866"):
            self.assertNotIn(forbidden, diagnostic)

    def test_source_date_parsers_require_explicit_source_markers(self):
        self.assertEqual(
            extract_lme_data_valid_date("<span>Data valid for: 28 August 2026</span>"),
            "2026-08-28",
        )
        self.assertEqual(
            extract_smm_quote_date("<span>发布日期：2026年8月28日</span>"),
            "2026-08-28",
        )
        self.assertIsNone(extract_lme_data_valid_date("Page updated 2026-08-28"))
        self.assertIsNone(extract_smm_quote_date("price 1866.00"))
        self.assertEqual(normalize_market_date("2026-08-28 00:00:00-04:00"), "2026-08-28")

    def test_smm_selected_row_date_uses_existing_average_and_date_columns(self):
        headers = ("Name", "Average", "Date")
        rows = (("Copper Cathode", "108600", "2026-08-28"),)
        self.assertEqual(extract_smm_selected_row_date(headers, rows, 108600.0), "2026-08-28")
        self.assertIsNone(extract_smm_selected_row_date(("Name", "Average"), rows, 108600.0))


if __name__ == "__main__":
    unittest.main()
