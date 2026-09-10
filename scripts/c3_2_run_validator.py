"""C3.2-7 read-only scheduled Shadow evidence validator.

This module deliberately consumes captures; it never launches a market job,
opens a Sheet, or changes Task Scheduler configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "c3_2_validator.json"
SCHEMA_PATH = ROOT / "config" / "c3_2_evidence_report.schema.json"
UTC = timezone.utc
TAIPEI = ZoneInfo("Asia/Taipei")
FORMAT_CHECKER = FormatChecker()


@FORMAT_CHECKER.checks("date-time")
def _is_rfc3339_datetime(value: object) -> bool:
    return not isinstance(value, str) or parse_datetime(value) is not None


def _load_json(path: Path) -> dict[str, Any]:
    # Windows PowerShell 5.1 writes UTF-8 captures with a BOM; Python must
    # accept both that form and BOM-free atomic evidence files.
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def assertion(value: Any, basis: str, reference: str | None = None, observed_at: str | None = None, digest: str | None = None) -> dict[str, Any]:
    return {"value": value, "basis": basis, "evidence_reference": reference, "observed_at": observed_at, "evidence_sha256": digest}


def make_run_id(config: dict[str, Any], task_name: str, instance_id: str, trigger_record_id: str, trigger_utc: str, trigger_kind: str) -> str:
    material = "|".join((config["run_id_namespace"], config["scheduler_task_path"], task_name, instance_id, trigger_record_id, trigger_utc, trigger_kind))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _slot_for(start: datetime, config: dict[str, Any]) -> datetime:
    local = start.astimezone(TAIPEI)
    hour, minute, second = (int(part) for part in config["slot_time"].split(":"))
    return local.replace(hour=hour, minute=minute, second=second, microsecond=0)


def normalize_scheduler_events(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate locale-independent Task Scheduler XML by Activity/Instance GUID."""
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        if "event_xml" not in record:
            # Deterministic normalized fixtures remain supported.
            groups[str(record.get("instance_id", len(groups)))] = dict(record)
            continue
        try:
            root = ElementTree.fromstring(record["event_xml"])
        except (ElementTree.ParseError, TypeError):
            continue
        ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
        system = root.find("e:System", ns)
        if system is None:
            continue
        event_id_node = system.find("e:EventID", ns)
        record_node = system.find("e:EventRecordID", ns)
        time_node = system.find("e:TimeCreated", ns)
        correlation = system.find("e:Correlation", ns)
        event_id = int(event_id_node.text or "0") if event_id_node is not None else 0
        if event_id not in {100, 102, 107, 114, 200, 201}:
            continue
        data = {node.attrib.get("Name", ""): node.text or "" for node in root.findall("e:EventData/e:Data", ns)}
        activity = (correlation.attrib.get("ActivityID") if correlation is not None else "") or data.get("InstanceId", "")
        activity = activity.strip("{}")
        if not activity:
            activity = "MISSING:" + str(record_node.text if record_node is not None else record.get("record_id", ""))
        item = groups.setdefault(activity, {"instance_id": activity, "task_name": data.get("TaskName", "").lstrip("\\")})
        when = time_node.attrib.get("SystemTime") if time_node is not None else record.get("observed_at")
        record_id = str(record_node.text if record_node is not None else record.get("record_id", ""))
        item["task_name"] = item.get("task_name") or data.get("TaskName", "").lstrip("\\")
        if event_id in {107, 114}:
            if item.get("record_id"):
                item["ambiguous_trigger"] = True
            item.update(record_id=record_id, trigger_utc=when, trigger_kind="TIME" if event_id == 107 else "RECOVERY")
        elif event_id == 100:
            item["start"] = when
        elif event_id == 200:
            item["action_start"] = when
        elif event_id == 201:
            item["action_completion"] = when
            raw_result = data.get("ResultCode", "")
            try:
                item["result"] = int(raw_result, 0)
            except (TypeError, ValueError):
                item["result"] = None
        elif event_id == 102:
            item["end"] = when
    return list(groups.values())


def correlate_scheduler(events: list[dict[str, Any]] | dict[str, Any], config: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Classify one captured instance without timestamp-only matching."""
    bundle = events if isinstance(events, dict) else {"events": events}
    coverage = bundle.get("coverage", {}) if isinstance(bundle.get("coverage", {}), dict) else {}
    events = normalize_scheduler_events(bundle.get("events", []))
    valid = [event for event in events if event.get("task_name") == config["scheduler_task_name"] and parse_datetime(event.get("start"))]
    if not valid:
        slot = parse_datetime(coverage.get("slot"))
        complete = coverage.get("log_enabled") is True and coverage.get("readable") is True and coverage.get("query_succeeded") is True and parse_datetime(coverage.get("coverage_start")) and parse_datetime(coverage.get("coverage_end"))
        if slot and now >= slot + timedelta(days=1) and complete and parse_datetime(coverage["coverage_start"]) <= slot - timedelta(minutes=2) and parse_datetime(coverage["coverage_end"]) >= slot + timedelta(days=1):
            return {"kind": "MISSED", "fail": "MISSED_SCHEDULED_SLOT", "event": {"scheduled_slot": slot.isoformat(), "task_name": config["scheduler_task_name"]}}
        if slot and now >= slot + timedelta(days=1):
            return {"kind": "AMBIGUOUS", "blocked": "SCHEDULER_COVERAGE_INCOMPLETE"}
        return {"kind": "AMBIGUOUS", "blocked": "SCHEDULER_EVIDENCE_UNAVAILABLE"}
    candidates: list[dict[str, Any]] = []
    for event in valid:
        if not event.get("instance_id") or str(event.get("instance_id", "")).startswith("MISSING:"):
            return {"kind": "AMBIGUOUS", "blocked": "SCHEDULER_INSTANCE_ID_MISSING", "event": event}
        if not event.get("record_id"):
            return {"kind": "AMBIGUOUS", "blocked": "SCHEDULER_TRIGGER_RECORD_ID_MISSING", "event": event}
        if event.get("ambiguous_trigger"):
            return {"kind": "AMBIGUOUS", "blocked": "AMBIGUOUS_SCHEDULER_TRIGGER", "event": event}
        start = parse_datetime(event["start"])
        assert start is not None
        trigger_time = parse_datetime(event.get("trigger_utc"))
        classification_time = trigger_time if event.get("trigger_kind") == "TIME" and trigger_time else start
        slot = _slot_for(classification_time, config)
        if classification_time.astimezone(TAIPEI) < slot:
            slot -= timedelta(days=1)
        trigger = event.get("trigger_kind")
        if trigger == "USER":
            kind = "MANUAL"
        elif trigger == "TIME" and slot <= classification_time.astimezone(TAIPEI) < slot + timedelta(seconds=config["natural_window_seconds"]):
            kind = "NATURAL"
        elif trigger == "RECOVERY" and config["start_when_available"] and start.astimezone(TAIPEI) >= slot + timedelta(seconds=config["natural_window_seconds"]):
            kind = "RECOVERY"
        else:
            kind = "AMBIGUOUS"
        event = dict(event, scheduled_slot=slot.isoformat(), execution_kind=kind)
        candidates.append(event)
    non_manual = [event for event in candidates if event["execution_kind"] in {"NATURAL", "RECOVERY"}]
    slots: dict[str, list[dict[str, Any]]] = {}
    for event in non_manual:
        slots.setdefault(event["scheduled_slot"], []).append(event)
    if any(len(items) > 1 for items in slots.values()):
        return {"kind": "DUPLICATE", "fail": "DUPLICATE_SLOT_EXECUTION", "events": candidates}
    if len(candidates) != 1:
        return {"kind": "AMBIGUOUS", "blocked": "AMBIGUOUS_SCHEDULER_CORRELATION", "events": candidates}
    event = candidates[0]
    if event["execution_kind"] == "AMBIGUOUS":
        return {"kind": "AMBIGUOUS", "blocked": "UNSUPPORTED_TRIGGER_OR_WINDOW", "event": event}
    if event["execution_kind"] == "MANUAL" and not event.get("retry_of_run_id"):
        return {"kind": "MANUAL", "event": event}
    if event["execution_kind"] == "MANUAL" and event.get("retry_of_run_id"):
        return {"kind": "RETRY", "event": event}
    end = parse_datetime(event.get("end"))
    start = parse_datetime(event["start"])
    assert start is not None
    if not event.get("action_start") or not event.get("action_completion"):
        return {"kind": event["execution_kind"], "event": event, "blocked": "SCHEDULER_ACTION_EVIDENCE_INCOMPLETE"}
    if event.get("result") is None:
        return {"kind": event["execution_kind"], "event": event, "blocked": "SCHEDULER_ACTION_RESULT_MISSING"}
    if event.get("result") != 0:
        return {"kind": event["execution_kind"], "event": event, "fail": "SCHEDULER_ACTION_NONZERO"}
    if end is None and now >= start + timedelta(seconds=config["completion_blocked_seconds"]):
        return {"kind": event["execution_kind"], "event": event, "blocked": "TERMINAL_COMPLETION_UNAVAILABLE"}
    if end is None:
        return {"kind": event["execution_kind"], "event": event, "blocked": "TERMINAL_COMPLETION_PENDING"}
    duration = (end - start).total_seconds()
    result: dict[str, Any] = {"kind": event["execution_kind"], "event": event, "duration": duration}
    if duration > config["completion_blocked_seconds"]:
        result["blocked"] = "COMPLETION_EXCEEDED_15_MINUTES"
    elif duration > config["completion_warning_seconds"]:
        result["warning"] = "COMPLETION_AFTER_10_MINUTES"
    if event["execution_kind"] == "RECOVERY":
        result["warning"] = "START_WHEN_AVAILABLE_RECOVERY"
    return result


def _redact(value: Any) -> Any:
    secret = re.compile(r"(?i)(AIza[\w-]{10,}|ya29\.[\w-]+|-----BEGIN [A-Z ]*PRIVATE KEY-----|google[_ -]?sheet[_ -]?id\s*[:=]\s*\S+|token\s*[:=]\s*\S+|password\s*[:=]\s*\S+)")
    price = re.compile(r"(?i)(price|value)\s*[:=]\s*\d+(?:\.\d+)?")
    if isinstance(value, str):
        return price.sub("<redacted-market-value>", secret.sub("<redacted>", value))
    if isinstance(value, list): return [_redact(item) for item in value]
    if isinstance(value, dict): return {key: _redact(item) for key, item in value.items()}
    return value


def _verify_runner_log(capture: dict[str, Any], blocked: list[str]) -> tuple[str | None, str | None, dict[str, Any] | None, str | None]:
    """Re-read the immutable log; a capture-provided hash is never trusted."""
    runner_log = capture.get("runner_log", {})
    path_text = runner_log.get("path") if isinstance(runner_log, dict) else None
    if not path_text:
        blocked.append("RUNNER_LOG_REFERENCE_MISSING")
        return None, None, None, None
    path = Path(path_text)
    fixture = capture.get("fixture_evidence")
    if isinstance(fixture, dict) and fixture.get("non_operational") is True:
        # Fixtures are isolated from operational evidence and can exercise the
        # parser without pretending to be an accepted natural execution.
        payload = str(runner_log.get("fixture_content", "")).encode("utf-8")
        path_text = "fixture://" + path.name
    else:
        try:
            payload = path.read_bytes()
        except OSError:
            blocked.append("RUNNER_LOG_UNAVAILABLE")
            return path_text, None, None, None
    digest = sha256_bytes(payload)
    if digest != runner_log.get("sha256"):
        blocked.append("RUNNER_LOG_DIGEST_MISMATCH")
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = payload.decode("utf-16")
    else:
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            blocked.append("RUNNER_LOG_ENCODING_UNSUPPORTED")
            return path_text, digest, None, None
    marker = "C3_2_RUNNER_SUMMARY="
    identity = capture.get("wrapper_execution_id")
    terminal_lines = [line[len(marker):] for line in text.splitlines() if line.startswith(marker)]
    try:
        terminal = json.loads(terminal_lines[0]) if len(terminal_lines) == 1 else None
    except json.JSONDecodeError:
        terminal = None
    summary_digest = sha256_bytes(terminal_lines[0].encode("utf-8")) if isinstance(terminal, dict) and len(terminal_lines) == 1 else None
    if not isinstance(terminal, dict) or terminal.get("schema_version") != "c3_2_7.runner_summary.v1" or terminal.get("wrapper_execution_id") != identity or terminal.get("runner_result") != capture.get("runner_result"):
        blocked.append("RUNNER_LOG_TERMINAL_IDENTITY_MISSING")
    if runner_log.get("wrapper_execution_id") != identity:
        blocked.append("RUNNER_LOG_REUSED_OR_MISMATCHED")
    if isinstance(fixture, dict) and fixture.get("non_operational") is True:
        blocked.append("FIXTURE_EVIDENCE_NON_OPERATIONAL")
    return path_text, digest, terminal, summary_digest


def validate_report_schema(report: dict[str, Any], schema: dict[str, Any] | None = None) -> list[str]:
    validator = Draft202012Validator(schema or _load_json(SCHEMA_PATH), format_checker=FORMAT_CHECKER)
    return sorted({"schema:" + "/".join(str(part) for part in error.absolute_path) + ":" + error.message for error in validator.iter_errors(report)})


def _append_checks(capture: dict[str, Any], failures: list[str], blocked: list[str]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    append = capture.get("append", {})
    required = ("pre_count", "post_count", "planned_count", "actual_count", "pre_digest", "post_prefix_digest", "appended_ids", "readback_ids")
    if any(key not in append for key in required):
        blocked.append("V2_APPEND_READBACK_EVIDENCE_MISSING")
    else:
        if append["post_count"] - append["pre_count"] != append["actual_count"]: failures.append("V2_UNEXPECTED_ROW_DELTA")
        if append["planned_count"] != append["actual_count"]: failures.append("V2_PLANNED_ACTUAL_MISMATCH")
        ids = append["appended_ids"]
        if not all(ids) or len(ids) != len(set(ids)): failures.append("V2_APPENDED_IDS_INVALID")
        if ids != append["readback_ids"]: failures.append("V2_READBACK_MISMATCH")
        if append["pre_digest"] != append["post_prefix_digest"]: failures.append("V2_PREFIX_MUTATED_OR_REORDERED")
    return assertion(append.get("actual_count"), "OBSERVED", "capture.append"), assertion(append.get("readback_ids"), "OBSERVED", "capture.append"), assertion("PASS" if not failures else "FAIL", "DERIVED", "capture.append")


def _semantic_checks(capture: dict[str, Any], terminal: dict[str, Any] | None, failures: list[str], blocked: list[str]) -> None:
    date_context = capture.get("date_context")
    if not isinstance(date_context, dict) or parse_datetime(date_context.get("scheduler_execution_at")) is None or not date_context.get("local_calendar_date") or not date_context.get("local_calendar_status"):
        blocked.append("DATE_CONTEXT_SEMANTIC_INVALID")
    elif date_context.get("canonical_target_basis") != "EXPLICIT_SOURCE_MARKET_DATE_ONLY": failures.append("DATE_CONTEXT_INFERRED_OR_CONTRADICTORY")
    source = capture.get("source_readiness")
    if not isinstance(source, dict) or source.get("status") not in {"EVALUATED", "NORMAL_SKIP_NON_BUSINESS_DAY"}:
        blocked.append("SOURCE_READINESS_SEMANTIC_INVALID")
    elif source["status"] == "EVALUATED" and (not isinstance(source.get("by_source"), dict) or not source["by_source"] or not isinstance(source.get("target_count"), int) or not isinstance(source.get("candidate_count"), int)):
        blocked.append("SOURCE_READINESS_SEMANTIC_INVALID")
    canonical = capture.get("canonical_evaluation")
    if isinstance(canonical, dict) and any(canonical.get(key) not in {"DISABLED", "NONE"} for key in ("persistence", "promotion", "deferred_assembly_persistence")):
        failures.append("UNAUTHORIZED_CANONICAL_PERSISTENCE")
    if not isinstance(canonical, dict) or not canonical.get("status") or (canonical.get("status") != "NOT_APPLICABLE" and not isinstance(canonical.get("evaluated_target_count"), int)):
        blocked.append("CANONICAL_EVALUATION_SEMANTIC_INVALID")
    if not isinstance(terminal, dict): blocked.append("RUNNER_TERMINAL_MISSING")


def _binding_checks(capture: dict[str, Any], event: dict[str, Any], terminal: dict[str, Any] | None, summary_digest: str | None, log_digest: str | None, run_id: str, failures: list[str], blocked: list[str]) -> dict[str, Any]:
    binding = capture.get("evidence_terminal")
    if not isinstance(binding, dict):
        blocked.append("EVIDENCE_TERMINAL_MISSING")
        return {}
    if not isinstance(terminal, dict) or not summary_digest or not log_digest:
        blocked.append("EVIDENCE_TERMINAL_INPUT_UNAVAILABLE")
        return binding
    required = ("scheduler_task_name", "scheduler_instance_id", "scheduler_trigger_event_record_id", "scheduler_trigger_timestamp", "trigger_kind", "wrapper_execution_id", "runner_summary_digest", "final_log_sha256", "run_id", "evidence_reference")
    if any(not binding.get(key) for key in required):
        blocked.append("EVIDENCE_TERMINAL_INCOMPLETE")
        return binding
    if not event:
        blocked.append("EVIDENCE_TERMINAL_SCHEDULER_UNAVAILABLE")
        return binding
    expected = {"scheduler_task_name": event.get("task_name"), "scheduler_instance_id": event.get("instance_id"), "scheduler_trigger_event_record_id": event.get("record_id"), "scheduler_trigger_timestamp": event.get("trigger_utc"), "trigger_kind": event.get("trigger_kind"), "wrapper_execution_id": capture.get("wrapper_execution_id"), "runner_summary_digest": summary_digest, "final_log_sha256": log_digest, "run_id": run_id}
    if any(binding.get(key) != value for key, value in expected.items()): failures.append("EVIDENCE_TERMINAL_BINDING_MISMATCH")
    if terminal and terminal.get("wrapper_execution_id") != binding.get("wrapper_execution_id"): failures.append("RUNNER_TERMINAL_BINDING_MISMATCH")
    if capture.get("runner_summary_digest") and capture.get("runner_summary_digest") != summary_digest: failures.append("RUNNER_SUMMARY_DIGEST_MISMATCH")
    return binding


def build_report(capture: dict[str, Any], events: list[dict[str, Any]], config: dict[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    config = config or _load_json(CONFIG_PATH)
    now = now or datetime.now(UTC)
    warnings: list[str] = []
    failures: list[str] = []
    blocked: list[str] = []
    not_verified = ["AUTHORITATIVE_HOLIDAY_CALENDAR", "SOURCE_NATIVE_PUBLICATION_TIMESTAMP", "A_L_CHANGE"]
    correlation = correlate_scheduler(events, config, now)
    if correlation.get("warning"): warnings.append(correlation["warning"])
    if correlation.get("fail"): failures.append(correlation["fail"])
    if correlation.get("blocked"): blocked.append(correlation["blocked"])
    event = correlation.get("event", {})
    wrapper_started = parse_datetime(capture.get("wrapper_started_at"))
    wrapper_completed = parse_datetime(capture.get("wrapper_completed_at"))
    action_start = parse_datetime(event.get("action_start"))
    action_completion = parse_datetime(event.get("action_completion"))
    if event and (not wrapper_started or not wrapper_completed or not action_start or not action_completion or not (action_start <= wrapper_started <= wrapper_completed <= action_completion + timedelta(seconds=5))):
        blocked.append("WRAPPER_SCHEDULER_INTERVAL_MISMATCH")
    runner_result = capture.get("runner_result")
    if not isinstance(runner_result, int): blocked.append("RUNNER_RESULT_MISSING")
    elif runner_result != 0: failures.append("RUNNER_NONZERO")
    gates = capture.get("control_path", {})
    expected_gates = gates.get("allow_google_sheet_write") == "0" and gates.get("allow_pending_raw_write") == "0" and gates.get("controlled_write_approval") in {None, ""} and gates.get("runner") == "scripts/c3_2_scheduled_shadow_runner.py"
    if not expected_gates: failures.append("PRODUCTION_CONTROL_PATH_INVALID")
    date_context = capture.get("date_context")
    source_readiness = capture.get("source_readiness")
    if not isinstance(source_readiness, dict): blocked.append("SOURCE_READINESS_MISSING")
    canonical = capture.get("canonical_evaluation")
    append_result, readback_result, append_only = _append_checks(capture, failures, blocked)
    log_path, log_digest, terminal, summary_digest = _verify_runner_log(capture, blocked)
    if log_digest is not None and log_digest != (capture.get("runner_log", {}) or {}).get("sha256"):
        failures.append("FINAL_LOG_DIGEST_MISMATCH")
    if correlation.get("kind") == "MANUAL": warnings.append("MANUAL_EXECUTION_NOT_NATURAL_ACCEPTANCE")
    if correlation.get("kind") == "RETRY" and not event.get("retry_of_run_id"): blocked.append("RETRY_PARENT_MISSING")
    final = "FAIL" if failures else "BLOCKED" if blocked else "WARNING" if warnings else "PASS"
    instance_id = str(event.get("instance_id", "UNVERIFIED"))
    trigger_id = str(event.get("record_id", "UNVERIFIED"))
    trigger_utc = str(event.get("trigger_utc", event.get("start", "1970-01-01T00:00:00+00:00")))
    trigger_kind = str(event.get("trigger_kind", "UNVERIFIED"))
    run_id = make_run_id(config, config["scheduler_task_name"], instance_id, trigger_id, trigger_utc, trigger_kind)
    _semantic_checks(capture, terminal, failures, blocked)
    binding = _binding_checks(capture, event, terminal, summary_digest, log_digest, run_id, failures, blocked)
    final = "FAIL" if failures else "BLOCKED" if blocked else "WARNING" if warnings else "PASS"
    timestamp = capture.get("capture_created_at") or now.isoformat()
    report = {
        "schema_version": "c3_2_7.evidence.v1", "report_id": run_id, "report_revision": 1,
        "report_created_at": timestamp, "report_sha256": "", "run_id": run_id,
        "wrapper_execution_id": capture.get("wrapper_execution_id", "00000000-0000-4000-8000-000000000000"),
        "scheduled_slot": event.get("scheduled_slot"), "execution_kind": correlation.get("kind"),
        "scheduler_task_name": config["scheduler_task_name"], "scheduler_instance_id": instance_id,
        "scheduler_trigger_event_record_id": trigger_id, "scheduler_trigger_timestamp": trigger_utc, "scheduler_trigger_kind": trigger_kind, "scheduler_execution_datetime": event.get("start"),
        "validator_execution_datetime": timestamp, "scheduler_result": assertion(event.get("result"), "OBSERVED", "scheduler.event", event.get("end")),
        "exit_origin": "RUNNER" if isinstance(runner_result, int) and runner_result != 0 else "VALIDATOR",
        "runner_summary_digest": summary_digest or "0" * 64, "evidence_terminal": binding or {"scheduler_task_name": config["scheduler_task_name"], "scheduler_instance_id": "00000000-0000-4000-8000-000000000000", "scheduler_trigger_event_record_id": "UNVERIFIED", "scheduler_trigger_timestamp": "1970-01-01T00:00:00+00:00", "trigger_kind": "TIME", "wrapper_execution_id": capture.get("wrapper_execution_id", "00000000-0000-4000-8000-000000000000"), "runner_summary_digest": summary_digest or "0" * 64, "final_log_sha256": log_digest or "0" * 64, "run_id": run_id, "evidence_reference": "fixture://missing-terminal"},
        "wrapper_result": assertion(capture.get("wrapper_result"), "OBSERVED", "capture.wrapper"),
        "runner_result": assertion(runner_result, "OBSERVED", "capture.runner"),
        "validator_result": assertion(final, "DERIVED", "validator"),
        "runner_log_reference": assertion(log_path, "OBSERVED", "capture.runner_log", digest=log_digest), "runner_log_sha256": log_digest,
        "date_context": assertion(date_context, "OBSERVED" if date_context else "NOT_VERIFIED", "capture.date_context"),
        "source_readiness": assertion(source_readiness, "OBSERVED" if source_readiness else "NOT_VERIFIED", "capture.source_readiness"),
        "canonical_evaluation": assertion(canonical, "OBSERVED" if canonical else "NOT_VERIFIED", "capture.canonical_evaluation"),
        "append_result": append_result, "readback_result": readback_result, "append_only_status": append_only,
        "production_status": assertion("DISABLED_BY_CONTROL" if expected_gates else "CONTROL_INVALID", "OBSERVED", "capture.control_path"),
        "a_l_change": assertion("NOT_VERIFIED", "NOT_VERIFIED", "control-path-only"),
        "canonical_persistence_status": assertion((canonical or {}).get("persistence", "NOT_VERIFIED"), "OBSERVED" if canonical else "NOT_VERIFIED", "capture.canonical_evaluation"),
        "repository_version": assertion(capture.get("repository_version"), "OBSERVED", "capture.repository"),
        "warnings": sorted(set(warnings)), "failures": sorted(set(failures)), "not_verified": not_verified, "blocked": sorted(set(blocked)),
        "evidence_references": _redact(capture.get("evidence_references", [])), "final_result": final,
        "pass_scope": "SHADOW_OPERATIONAL_ONLY" if final == "PASS" else None,
    }
    report["report_sha256"] = sha256_bytes(canonical_bytes({key: value for key, value in report.items() if key != "report_sha256"}))
    return _redact(report)


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join((
        "# C3.2-7 Evidence Report", "", f"- Result: `{report['final_result']}`", f"- Run ID: `{report['run_id']}`", f"- Execution: `{report['execution_kind']}`", f"- Report SHA-256: `{report['report_sha256']}`", "",
        "## Findings", "", f"- Warnings: {', '.join(report['warnings']) or 'none'}", f"- Failures: {', '.join(report['failures']) or 'none'}", f"- Blocked: {', '.join(report['blocked']) or 'none'}", f"- Not verified: {', '.join(report['not_verified']) or 'none'}", "",
        "## Safety", "", f"- Production: `{report['production_status']['value']}`", f"- A:L change: `{report['a_l_change']['value']}`", f"- Canonical persistence: `{report['canonical_persistence_status']['value']}`", "",
    ))


def validator_exit(runner_result: int, final_result: str, *, storage_failure: bool = False, internal_error: bool = False) -> int:
    if runner_result != 0: return runner_result
    if storage_failure: return 73
    if internal_error: return 72
    return {"FAIL": 70, "BLOCKED": 71}.get(final_result, 0)


def _safe_directory(directory: Path, *, check_acl: bool = True) -> bool:
    try:
        if os.name == "nt":
            probe = directory / (".c3_2_7-probe-" + os.urandom(6).hex())
            with probe.open("xb") as handle:
                handle.write(b"probe"); handle.flush(); os.fsync(handle.fileno())
            probe.unlink()
            if not check_acl:
                return True
            environment = os.environ.copy(); environment["C3_2_ACL_PATH"] = str(directory)
            command = "Write-Output (Get-Acl -LiteralPath $env:C3_2_ACL_PATH).Sddl; Write-Output ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value)"
            completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True, timeout=10, env=environment, check=False)
            lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
            if completed.returncode != 0 or len(lines) < 2:
                return False
            sddl, current_sid = lines[-2], lines[-1]
            owner = sddl.split("G:", 1)[0].removeprefix("O:") if sddl.startswith("O:") else ""
            if owner not in {current_sid, "BA", "SY"}:
                return False
            for ace in re.findall(r"\([^)]*\)", sddl):
                if ace.startswith("(A;") and ace.endswith((";;;WD)", ";;;AN)", ";;;AU)", ";;;BU)")) and any(right in ace for right in ("FA", "FW", "GA", "GW", "WD", "AD", "DC")):
                    return False
            return True
        mode = directory.stat().st_mode
        return not bool(mode & 0o002)
    except OSError:
        return False


def _publish_exclusive(target: Path, payload: bytes) -> str:
    """Atomically publish by hard-linking a flushed same-directory temp file."""
    with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, prefix="." + target.name + ".", delete=False) as temporary:
        temporary.write(payload); temporary.flush(); os.fsync(temporary.fileno()); temporary_path = Path(temporary.name)
    try:
        os.link(temporary_path, target)
    except FileExistsError:
        return "SAME" if target.read_bytes() == payload else "DIFFERENT"
    except OSError:
        return "ERROR"
    finally:
        temporary_path.unlink(missing_ok=True)
    return "CREATED"


def write_immutable_report(report: dict[str, Any], root: Path | None = None) -> tuple[str, Path | None, Path | None]:
    if _load_json(CONFIG_PATH).get("retention_days", 0) < 400:
        return "RETENTION_UNSAFE", None, None
    local_base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))).resolve()
    operational = root is None
    root = root or local_base / "ProcurementMaterialsPlatform" / "evidence" / "c3_2_7"
    if operational:
        try:
            root.resolve().relative_to(local_base)
        except (OSError, ValueError):
            return "STORAGE_UNSAFE", None, None
    day = (parse_datetime(report["report_created_at"]) or datetime.now(UTC)).astimezone(TAIPEI).date().isoformat()
    directory = root / day
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return "STORAGE_UNSAFE", None, None
    if not _safe_directory(directory, check_acl=operational): return "STORAGE_UNSAFE", None, None
    stem = "c3_2_7-" + report["run_id"]
    target = directory / (stem + ".json")
    encoded = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    markdown = directory / (stem + ".md")
    markdown_bytes = render_markdown(report).encode("utf-8")
    if target.exists():
        if target.read_bytes() == encoded:
            markdown_status = _publish_exclusive(markdown, markdown_bytes)
            return ("DUPLICATE_SAME" if markdown_status in {"SAME", "CREATED"} else "MARKDOWN_CONFLICT"), target, markdown
        conflict = directory / (stem + ".conflict-" + sha256_bytes(encoded)[:12] + ".json")
        conflict_status = _publish_exclusive(conflict, encoded)
        return ("CONFLICT" if conflict_status in {"CREATED", "SAME"} else "STORAGE_UNSAFE"), target, conflict
    primary_status = _publish_exclusive(target, encoded)
    if primary_status == "SAME": return write_immutable_report(report, root)
    if primary_status == "DIFFERENT": return write_immutable_report(report, root)
    if primary_status != "CREATED": return "STORAGE_UNSAFE", None, None
    markdown_status = _publish_exclusive(markdown, markdown_bytes)
    if markdown_status not in {"CREATED", "SAME"}: return "MARKDOWN_CONFLICT", target, markdown
    return "CREATED", target, markdown


def emit_evidence_terminal(capture: dict[str, Any], events: list[dict[str, Any]] | dict[str, Any], config: dict[str, Any]) -> dict[str, Any] | None:
    correlation = correlate_scheduler(events, config, datetime.now(UTC))
    event = correlation.get("event")
    if not isinstance(event, dict): return None
    blocked: list[str] = []
    _, log_digest, terminal, summary_digest = _verify_runner_log(capture, blocked)
    if blocked or not isinstance(terminal, dict): return None
    run_id = make_run_id(config, event.get("task_name", config["scheduler_task_name"]), event.get("instance_id", ""), event.get("record_id", ""), event.get("trigger_utc", ""), event.get("trigger_kind", ""))
    return {"scheduler_task_name": event.get("task_name"), "scheduler_instance_id": event.get("instance_id"), "scheduler_trigger_event_record_id": event.get("record_id"), "scheduler_trigger_timestamp": event.get("trigger_utc"), "trigger_kind": event.get("trigger_kind"), "wrapper_execution_id": capture.get("wrapper_execution_id"), "runner_summary_digest": summary_digest, "final_log_sha256": log_digest, "run_id": run_id, "evidence_reference": "companion://evidence-terminal"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--terminal", type=Path)
    parser.add_argument("--emit-evidence-terminal", action="store_true")
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--stage", choices=("core", "final"), default="final")
    args = parser.parse_args()
    try:
        capture = _load_json(args.capture)
        events = _load_json(args.events) if args.events else capture.get("scheduler_events", [])
        if args.emit_evidence_terminal:
            binding = emit_evidence_terminal(capture, events, _load_json(CONFIG_PATH))
            if binding is None: print("C3_2_VALIDATOR=BLOCKED evidence_terminal=unavailable"); return 71
            if args.terminal is None: print("C3_2_VALIDATOR=INTERNAL terminal_path_missing"); return 72
            payload = json.dumps(binding, ensure_ascii=True, sort_keys=True).encode("utf-8") + b"\n"
            if _publish_exclusive(args.terminal, payload) not in {"CREATED", "SAME"}: print("C3_2_VALIDATOR=BLOCKED evidence_terminal=conflict"); return 71
            print("C3_2_VALIDATOR=EVIDENCE_TERMINAL path=" + str(args.terminal)); return 0
        if args.terminal:
            capture["evidence_terminal"] = _load_json(args.terminal)
        report = build_report(capture, events)
        errors = validate_report_schema(report)
        if errors:
            print("C3_2_VALIDATOR=INTERNAL schema_errors=" + ",".join(errors)); return 72
        if args.stage == "core":
            # Completion/Scheduler correlation is intentionally unavailable
            # while the Scheduler action is still running.  Core validation
            # checks only capture provenance; finalization performs the full
            # fail-closed report after the action exits.
            core_ok = isinstance(capture.get("runner_result"), int) and isinstance(capture.get("runner_log"), dict) and isinstance(capture.get("control_path"), dict)
            print("C3_2_VALIDATOR=CORE result=" + ("READY" if core_ok else "BLOCKED"))
            if int(capture.get("runner_result", 0)) != 0: return int(capture["runner_result"])
            return 0 if core_ok else 71
        status, json_path, markdown_path = write_immutable_report(report, args.evidence_root)
        if status in {"CONFLICT", "MARKDOWN_CONFLICT"}: print("C3_2_VALIDATOR=BLOCKED storage=" + status); return validator_exit(int(capture.get("runner_result", 0)), "BLOCKED")
        if status in {"STORAGE_UNSAFE", "RETENTION_UNSAFE"}: print("C3_2_VALIDATOR=STORAGE_FAILURE"); return validator_exit(int(capture.get("runner_result", 0)), report["final_result"], storage_failure=True)
        print("C3_2_VALIDATOR=" + report["final_result"] + " storage=" + status + " json=" + str(json_path) + " markdown=" + str(markdown_path))
        return validator_exit(int(capture.get("runner_result", 0)), report["final_result"])
    except Exception as exc:
        print("C3_2_VALIDATOR=INTERNAL error=" + type(exc).__name__)
        return 72


if __name__ == "__main__":
    raise SystemExit(main())
