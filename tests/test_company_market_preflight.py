from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import unittest

from scripts.company_market_core import DataContractError, MarketQuote
from scripts.company_market_preflight import (
    expected_approval_token,
    require_explicit_write_approval,
    run_anomaly_checks,
    validate_controlled_write_preflight,
)


KEYS = tuple(f"quote_{index}" for index in range(11))
THRESHOLDS = {key: 20.0 for key in KEYS}


class AuditPathStub:
    def __init__(self, exists=True, size=2):
        self.exists = exists
        self.size = size

    def is_file(self):
        return self.exists

    def stat(self):
        return SimpleNamespace(st_size=self.size)

    def __str__(self):
        return "runtime/company-market/latest.json"


def make_quotes(value: float = 110.0):
    return {
        key: MarketQuote(
            key=key, name=key, source="test", instrument=key, term="Cash",
            quote_type="Close", currency="USD", unit="unit", value=value,
            fetched_at="2026-08-27T09:00:00+08:00", status="SUCCESS",
        )
        for key in KEYS
    }


class ControlledWritePreflightTests(unittest.TestCase):
    def setUp(self):
        self.target_date = date(2026, 8, 27)
        self.rows = [[], [], [], [], ["2026/08/26"] + [100.0] * 11, []]
        self.quotes = make_quotes()
        self.checks = run_anomaly_checks(
            self.quotes, KEYS, self.rows, 6, THRESHOLDS
        )

    def validate(self, audit_path, **overrides):
        arguments = dict(
            target_date=self.target_date,
            expected_date=self.target_date,
            target_row=6,
            is_new_row=True,
            target_row_values=[],
            quotes=self.quotes,
            required_keys=KEYS,
            anomaly_checks=self.checks,
            layout_validated=True,
            audit_path=audit_path,
        )
        arguments.update(overrides)
        return validate_controlled_write_preflight(**arguments)

    def test_complete_preflight_returns_date_range_bound_approval_token(self):
        report = self.validate(AuditPathStub())
        self.assertEqual(report.target_range, "A6:L6")
        self.assertEqual(report.quote_count, 11)
        self.assertEqual(
            report.approval_token,
            "APPROVE-C3.1-WRITE-2026/08/27-A6:L6",
        )

    def test_occupied_target_row_fails_closed(self):
        with self.assertRaises(DataContractError):
            self.validate(AuditPathStub(), target_row_values=["", 999])

    def test_existing_date_row_cannot_be_overwritten(self):
        with self.assertRaises(DataContractError):
            self.validate(AuditPathStub(), is_new_row=False)

    def test_missing_quote_or_anomaly_check_fails_closed(self):
        incomplete = dict(self.quotes)
        incomplete.pop(KEYS[-1])
        with self.assertRaises(DataContractError):
            self.validate(
                AuditPathStub(), quotes=incomplete, anomaly_checks=self.checks[:-1]
            )

    def test_anomaly_uses_previous_value_per_column_and_blocks_spike(self):
        quotes = make_quotes()
        quotes[KEYS[4]] = make_quotes(150.0)[KEYS[4]]
        checks = run_anomaly_checks(quotes, KEYS, self.rows, 6, THRESHOLDS)
        self.assertFalse(checks[4].passed)
        self.assertEqual(checks[4].previous_row, 5)
        self.assertEqual(checks[4].change_pct, 50.0)

    def test_missing_or_empty_audit_fails_closed(self):
        with self.assertRaises(DataContractError):
            self.validate(AuditPathStub(exists=False, size=0))

    def test_write_needs_both_switch_and_exact_human_approval(self):
        report = self.validate(AuditPathStub())
        with self.assertRaises(DataContractError):
            require_explicit_write_approval(
                report, write_enabled="1", approval="APPROVE"
            )
        require_explicit_write_approval(
            report,
            write_enabled="1",
            approval=expected_approval_token(self.target_date, 6),
        )


if __name__ == "__main__":
    unittest.main()
