from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.c3_2_run_validator import (
    _load_json, build_report, correlate_scheduler, normalize_scheduler_events, render_markdown,
    sha256_bytes, validate_report_schema, validator_exit, write_immutable_report,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config" / "c3_2_validator.json").read_text(encoding="utf-8"))
UTC = timezone.utc


def event(*, start="2026-09-09T08:30:01+00:00", end="2026-09-09T08:31:08+00:00", trigger="TIME", record="101", instance="instance-1", **extra):
    value = {"task_name": CONFIG["scheduler_task_name"], "start": start, "action_start": start, "action_completion": end, "end": end, "trigger_kind": trigger, "record_id": record, "instance_id": instance, "trigger_utc": start, "result": 0}
    value.update(extra)
    return value


def capture(**overrides):
    value = {
        "capture_created_at": "2026-09-09T08:31:10+00:00", "wrapper_execution_id": "wrap-1", "wrapper_result": 0, "runner_result": 0,
        "wrapper_started_at": "2026-09-09T08:30:02+00:00", "wrapper_completed_at": "2026-09-09T08:31:07+00:00",
        "repository_version": "abc123", "control_path": {"allow_google_sheet_write": "0", "allow_pending_raw_write": "0", "controlled_write_approval": None, "runner": "scripts/c3_2_scheduled_shadow_runner.py"},
        "fixture_evidence": {"non_operational": True},
        "runner_log": {"path": "fixture-run.log", "fixture_content": 'C3_2_RUNNER_SUMMARY={"wrapper_execution_id": "wrap-1", "runner_result": 0}', "sha256": "", "wrapper_execution_id": "wrap-1"},
        "date_context": {"scheduler_execution_at": "2026-09-09T16:30:01+08:00", "local_calendar_date": "2026-09-09", "canonical_target_basis": "EXPLICIT_SOURCE_MARKET_DATE_ONLY"},
        "source_readiness": {"SMM": "READY", "LME": "PENDING", "Yahoo": "YAHOO_UNCONFIRMED", "source_native_publication": "NOT_VERIFIED"},
        "canonical_evaluation": {"persistence": "DISABLED", "promotion": "DISABLED", "status": "UNRESOLVED"},
        "append": {"pre_count": 10, "post_count": 12, "planned_count": 2, "actual_count": 2, "pre_digest": "b" * 64, "post_prefix_digest": "b" * 64, "appended_ids": ["id-1", "id-2"], "readback_ids": ["id-1", "id-2"]},
        "evidence_references": ["safe-log-digest"],
    }
    value.update(overrides)
    log = value["runner_log"]
    from scripts.c3_2_run_validator import sha256_bytes
    log["sha256"] = sha256_bytes(log.get("fixture_content", "").encode("utf-8"))
    return value


class RunValidatorTests(unittest.TestCase):
    def report(self, item=None, events=None, now=None):
        return build_report(item or capture(), events if events is not None else [event()], CONFIG, now or datetime(2026, 9, 9, 8, 35, tzinfo=UTC))

    def test_schema_and_markdown_are_consistent(self):
        report = self.report()
        self.assertEqual(validate_report_schema(report), [])
        markdown = render_markdown(report)
        self.assertIn(report["run_id"], markdown)
        self.assertIn("`BLOCKED`", markdown)
        malformed = dict(report); malformed["report_sha256"] = None; malformed["unsupported"] = True
        self.assertIn("report_sha256", validate_report_schema(malformed))
        self.assertIn("unknown:unsupported", validate_report_schema(malformed))
        malformed = dict(report); malformed["runner_result"] = {"basis": "OBSERVED"}
        self.assertIn("runner_result", validate_report_schema(malformed))
        malformed = dict(report); malformed["report_created_at"] = "not-a-timestamp"
        self.assertIn("report_created_at", validate_report_schema(malformed))
        malformed = dict(report); malformed["report_id"] = None
        self.assertIn("report_id", validate_report_schema(malformed))

    def test_powershell_51_bom_capture_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "capture.json"
            path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"schema_version": "capture"}).encode("utf-8"))
            self.assertEqual(_load_json(path)["schema_version"], "capture")

    def test_run_kinds_natural_recoveries_manual_retry(self):
        natural = correlate_scheduler([event()], CONFIG, datetime(2026, 9, 9, 9, tzinfo=UTC))
        self.assertEqual(natural["kind"], "NATURAL")
        same_day = correlate_scheduler([event(start="2026-09-09T08:45:59+00:00", trigger="RECOVERY")], CONFIG, datetime(2026, 9, 9, 9, tzinfo=UTC))
        self.assertEqual(same_day["kind"], "RECOVERY")
        overnight = correlate_scheduler([event(start="2026-09-07T00:04:58+00:00", trigger="RECOVERY")], CONFIG, datetime(2026, 9, 7, 1, tzinfo=UTC))
        self.assertEqual(overnight["kind"], "RECOVERY")
        self.assertEqual(correlate_scheduler([event(trigger="USER")], CONFIG, datetime.now(UTC))["kind"], "MANUAL")
        self.assertEqual(correlate_scheduler([event(trigger="USER", retry_of_run_id="prior")], CONFIG, datetime.now(UTC))["kind"], "RETRY")

    def test_scheduler_xml_aggregation_is_activity_based_and_locale_independent(self):
        activity = "{11111111-2222-3333-4444-555555555555}"
        def xml(event_id, record_id, when, fields):
            data = "".join(f'<Data Name="{key}">{value}</Data>' for key, value in fields.items())
            return f'<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><EventID>{event_id}</EventID><TimeCreated SystemTime="{when}"/><EventRecordID>{record_id}</EventRecordID><Correlation ActivityID="{activity}"/></System><EventData>{data}</EventData></Event>'
        task = {"TaskName": "\\" + CONFIG["scheduler_task_name"]}
        records = [
            {"event_xml": xml(107, 10, "2026-09-09T08:30:00+00:00", task), "message": "localized"},
            {"event_xml": xml(100, 11, "2026-09-09T08:30:01+00:00", task)},
            {"event_xml": xml(200, 12, "2026-09-09T08:30:02+00:00", task)},
            {"event_xml": xml(201, 13, "2026-09-09T08:31:07+00:00", {**task, "ResultCode": "0"})},
            {"event_xml": xml(102, 14, "2026-09-09T08:31:08+00:00", task)},
        ]
        aggregated = normalize_scheduler_events(records)
        self.assertEqual(len(aggregated), 1)
        self.assertEqual(aggregated[0]["record_id"], "10")
        self.assertEqual(correlate_scheduler(records, CONFIG, datetime(2026, 9, 9, 9, tzinfo=UTC))["kind"], "NATURAL")
        failed = list(records); failed[3] = {"event_xml": xml(201, 13, "2026-09-09T08:31:07+00:00", {**task, "ResultCode": "7"})}
        self.assertEqual(correlate_scheduler(failed, CONFIG, datetime(2026, 9, 9, 9, tzinfo=UTC))["fail"], "SCHEDULER_ACTION_NONZERO")

    def test_scheduler_missing_identity_action_and_proven_miss(self):
        self.assertEqual(correlate_scheduler([event(instance="")], CONFIG, datetime.now(UTC))["blocked"], "SCHEDULER_INSTANCE_ID_MISSING")
        self.assertEqual(correlate_scheduler([event(record="")], CONFIG, datetime.now(UTC))["blocked"], "SCHEDULER_TRIGGER_RECORD_ID_MISSING")
        self.assertEqual(correlate_scheduler([event(action_completion=None)], CONFIG, datetime.now(UTC))["blocked"], "SCHEDULER_ACTION_EVIDENCE_INCOMPLETE")
        missed = correlate_scheduler([{"missed_slot": "2026-09-08T16:30:00+08:00", "evidence_complete": True}], CONFIG, datetime(2026, 9, 10, tzinfo=UTC))
        self.assertEqual(missed["fail"], "MISSED_SCHEDULED_SLOT")

    def test_missed_duplicate_and_ambiguous_are_fail_closed(self):
        duplicate = self.report(events=[event(), event(record="102", instance="instance-2")])
        self.assertEqual(duplicate["final_result"], "FAIL")
        self.assertIn("DUPLICATE_SLOT_EXECUTION", duplicate["failures"])
        ambiguous = self.report(events=[])
        self.assertEqual(ambiguous["final_result"], "BLOCKED")
        # Once the next slot has started, an absent prior event is a proven miss.
        self.assertEqual(correlate_scheduler([], CONFIG, datetime(2026, 9, 10, 9, tzinfo=UTC))["blocked"], "SCHEDULER_EVIDENCE_UNAVAILABLE")

    def test_time_boundaries_and_terminal_completion(self):
        self.assertEqual(correlate_scheduler([event(start="2026-09-09T08:30:00+00:00")], CONFIG, datetime.now(UTC))["kind"], "NATURAL")
        self.assertEqual(correlate_scheduler([event(start="2026-09-09T08:30:01+00:00")], CONFIG, datetime.now(UTC))["kind"], "NATURAL")
        self.assertEqual(correlate_scheduler([event(start="2026-09-09T08:32:00+00:00", trigger="TIME")], CONFIG, datetime.now(UTC))["kind"], "AMBIGUOUS")
        late = correlate_scheduler([event(end="2026-09-09T08:41:01+00:00")], CONFIG, datetime.now(UTC)); self.assertEqual(late["warning"], "COMPLETION_AFTER_10_MINUTES")
        blocked = correlate_scheduler([event(end="2026-09-09T08:45:02+00:00")], CONFIG, datetime.now(UTC)); self.assertEqual(blocked["blocked"], "COMPLETION_EXCEEDED_15_MINUTES")

    def test_runner_exit_precedence_and_reserved_codes(self):
        self.assertEqual(validator_exit(1, "FAIL"), 1)
        self.assertEqual(validator_exit(47, "BLOCKED"), 47)
        self.assertEqual(validator_exit(0, "FAIL"), 70)
        self.assertEqual(validator_exit(0, "BLOCKED"), 71)
        self.assertEqual(validator_exit(0, "PASS", internal_error=True), 72)
        self.assertEqual(validator_exit(0, "PASS", storage_failure=True), 73)

    def test_native_stderr_warning_and_application_failure(self):
        warning = self.report(capture(stderr_warning="safe diagnostic")); self.assertEqual(warning["final_result"], "BLOCKED")
        failure = self.report(capture(runner_result=9)); self.assertEqual(failure["final_result"], "FAIL")
        self.assertEqual(validator_exit(9, failure["final_result"]), 9)

    def test_date_source_and_canonical_fail_closed(self):
        invalid_date = self.report(capture(date_context={"canonical_target_basis": "INFERRED_EXECUTION_DATE"})); self.assertEqual(invalid_date["final_result"], "BLOCKED")
        missing_sources = self.report(capture(source_readiness=None)); self.assertEqual(missing_sources["final_result"], "BLOCKED")
        promotion = self.report(capture(canonical_evaluation={"persistence": "ENABLED", "promotion": "ENABLED"})); self.assertEqual(promotion["final_result"], "FAIL")
        self.assertIn("SOURCE_NATIVE_PUBLICATION_TIMESTAMP", self.report()["not_verified"])

    def test_append_only_integrity_cases(self):
        for changed in (
            {"post_count": 13}, {"post_prefix_digest": "changed"}, {"readback_ids": ["id-2", "id-1"]}, {"appended_ids": ["id-1", "id-1"]},
        ):
            item = capture(); item["append"].update(changed)
            self.assertEqual(self.report(item)["final_result"], "FAIL")
        item = capture(); item["append"] = {"pre_count": 1}
        self.assertEqual(self.report(item)["final_result"], "BLOCKED")

    def test_control_path_never_claims_a_l_no_change(self):
        report = self.report()
        self.assertEqual(report["production_status"]["value"], "DISABLED_BY_CONTROL")
        self.assertEqual(report["a_l_change"]["value"], "NOT_VERIFIED")
        unsafe = self.report(capture(control_path={}))
        self.assertEqual(unsafe["final_result"], "FAIL")

    def test_atomic_idempotency_conflict_and_redaction(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = self.report()
            status, json_path, markdown_path = write_immutable_report(report, Path(temporary))
            self.assertEqual(status, "CREATED"); self.assertTrue(json_path and json_path.exists()); self.assertTrue(markdown_path and markdown_path.exists())
            self.assertEqual(write_immutable_report(report, Path(temporary))[0], "DUPLICATE_SAME")
            assert markdown_path is not None
            markdown_path.write_text("existing different markdown", encoding="utf-8")
            self.assertEqual(write_immutable_report(report, Path(temporary))[0], "MARKDOWN_CONFLICT")
            changed = dict(report); changed["warnings"] = ["different"]
            self.assertEqual(write_immutable_report(changed, Path(temporary))[0], "CONFLICT")
        redacted = self.report(capture(evidence_references=["token=secret GOOGLE_SHEET_ID=sheet-123 price=700.2 -----BEGIN PRIVATE KEY-----"]))
        serialized = json.dumps(redacted)
        self.assertNotIn("sheet-123", serialized); self.assertNotIn("700.2", serialized); self.assertNotIn("secret", serialized)

    def test_atomic_publication_is_race_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = self.report()
            with ThreadPoolExecutor(max_workers=8) as pool:
                statuses = list(pool.map(lambda _: write_immutable_report(report, root)[0], range(8)))
            self.assertEqual(statuses.count("CREATED"), 1)
            self.assertTrue(all(status in {"CREATED", "DUPLICATE_SAME"} for status in statuses))
            changed = dict(report); changed["warnings"] = ["racing-conflict"]
            with ThreadPoolExecutor(max_workers=4) as pool:
                conflicts = list(pool.map(lambda _: write_immutable_report(changed, root)[0], range(4)))
            self.assertTrue(all(status == "CONFLICT" for status in conflicts))

    def test_operational_log_is_recomputed_and_identity_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runner.log"
            payload = b'C3_2_RUNNER_SUMMARY={"wrapper_execution_id": "wrap-1", "runner_result": 0}\n'
            path.write_bytes(payload)
            item = capture(fixture_evidence={"non_operational": False})
            item["runner_log"] = {"path": str(path), "sha256": sha256_bytes(payload), "wrapper_execution_id": "wrap-1"}
            self.assertEqual(self.report(item)["final_result"], "PASS")
            item["runner_log"]["sha256"] = "0" * 64
            self.assertEqual(self.report(item)["final_result"], "BLOCKED")
            item["runner_log"]["sha256"] = sha256_bytes(payload); item["wrapper_execution_id"] = "wrap-2"
            self.assertEqual(self.report(item)["final_result"], "BLOCKED")

    def test_operational_log_utf16_malformed_and_missing_terminal_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runner.log"
            text = 'C3_2_RUNNER_SUMMARY={"wrapper_execution_id": "wrap-1", "runner_result": 0}\r\n'
            payload = text.encode("utf-16")
            path.write_bytes(payload)
            item = capture(fixture_evidence={"non_operational": False})
            item["runner_log"] = {"path": str(path), "sha256": sha256_bytes(payload), "wrapper_execution_id": "wrap-1"}
            self.assertEqual(self.report(item)["final_result"], "PASS")
            malformed = b"C3_2_RUNNER_SUMMARY={not-json}\n"
            path.write_bytes(malformed); item["runner_log"]["sha256"] = sha256_bytes(malformed)
            self.assertEqual(self.report(item)["final_result"], "BLOCKED")
            missing = b"ordinary diagnostic only\n"
            path.write_bytes(missing); item["runner_log"]["sha256"] = sha256_bytes(missing)
            self.assertEqual(self.report(item)["final_result"], "BLOCKED")

    def test_storage_and_retention_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            blocked_root = Path(temporary) / "occupied"
            blocked_root.write_text("not a directory", encoding="ascii")
            self.assertEqual(write_immutable_report(self.report(), blocked_root)[0], "STORAGE_UNSAFE")
            with patch("scripts.c3_2_run_validator._load_json", return_value={"retention_days": 399}):
                self.assertEqual(write_immutable_report(self.report(), Path(temporary) / "evidence")[0], "RETENTION_UNSAFE")


if __name__ == "__main__":
    unittest.main()
