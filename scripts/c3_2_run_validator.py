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
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "c3_2_validator.json"
SCHEMA_PATH = ROOT / "config" / "c3_2_evidence_report.schema.json"
UTC = timezone.utc
TAIPEI = ZoneInfo("Asia/Taipei")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def make_run_id(config: dict[str, Any], instance_id: str, trigger_record_id: str, trigger_utc: str) -> str:
    material = "|".join((config["run_id_namespace"], config["scheduler_task_path"], instance_id, trigger_record_id, trigger_utc))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _slot_for(start: datetime, config: dict[str, Any]) -> datetime:
    local = start.astimezone(TAIPEI)
    hour, minute, second = (int(part) for part in config["slot_time"].split(":"))
    return local.replace(hour=hour, minute=minute, second=second, microsecond=0)


def correlate_scheduler(events: list[dict[str, Any]], config: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Classify one captured instance without timestamp-only matching."""
    valid = [event for event in events if event.get("task_name") == config["scheduler_task_name"] and parse_datetime(event.get("start"))]
    if not valid:
        return {"kind": "AMBIGUOUS", "blocked": "SCHEDULER_EVIDENCE_UNAVAILABLE"}
    candidates: list[dict[str, Any]] = []
    for event in valid:
        start = parse_datetime(event["start"])
        assert start is not None
        slot = _slot_for(start, config)
        if start.astimezone(TAIPEI) < slot:
            slot -= timedelta(days=1)
        trigger = event.get("trigger_kind")
        if trigger == "USER":
            kind = "MANUAL"
        elif trigger == "TIME" and slot <= start.astimezone(TAIPEI) < slot + timedelta(seconds=config["natural_window_seconds"]):
            kind = "NATURAL"
        elif trigger not in {"TIME", "USER"} and config["start_when_available"] and start.astimezone(TAIPEI) >= slot + timedelta(seconds=config["natural_window_seconds"]):
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


def validate_report_schema(report: dict[str, Any], schema: dict[str, Any] | None = None) -> list[str]:
    schema = schema or _load_json(SCHEMA_PATH)
    errors = ["missing:" + key for key in schema["required"] if key not in report]
    if report.get("schema_version") != "c3_2_7.evidence.v1": errors.append("schema_version")
    if report.get("final_result") not in {"PASS", "WARNING", "FAIL", "BLOCKED"}: errors.append("final_result")
    for key in ("warnings", "failures", "not_verified", "blocked"):
        if key in report and not isinstance(report[key], list): errors.append(key)
    return errors


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
    runner_result = capture.get("runner_result")
    if not isinstance(runner_result, int): blocked.append("RUNNER_RESULT_MISSING")
    elif runner_result != 0: failures.append("RUNNER_NONZERO")
    gates = capture.get("control_path", {})
    expected_gates = gates.get("allow_google_sheet_write") == "0" and gates.get("allow_pending_raw_write") == "0" and gates.get("controlled_write_approval") in {None, ""} and gates.get("runner") == "scripts/c3_2_scheduled_shadow_runner.py"
    if not expected_gates: failures.append("PRODUCTION_CONTROL_PATH_INVALID")
    date_context = capture.get("date_context")
    if not isinstance(date_context, dict) or date_context.get("canonical_target_basis") != "EXPLICIT_SOURCE_MARKET_DATE_ONLY": blocked.append("DATE_CONTEXT_MISSING_OR_INVALID")
    source_readiness = capture.get("source_readiness")
    if not isinstance(source_readiness, dict): blocked.append("SOURCE_READINESS_MISSING")
    canonical = capture.get("canonical_evaluation")
    if not isinstance(canonical, dict): blocked.append("CANONICAL_EVALUATION_MISSING")
    elif canonical.get("persistence") not in {"DISABLED", "NONE"} or canonical.get("promotion") not in {"DISABLED", "NONE"}: failures.append("UNAUTHORIZED_CANONICAL_PERSISTENCE")
    append_result, readback_result, append_only = _append_checks(capture, failures, blocked)
    runner_log = capture.get("runner_log", {})
    log_path = runner_log.get("path")
    log_digest = runner_log.get("sha256")
    if not log_path or not log_digest or runner_log.get("wrapper_execution_id") != capture.get("wrapper_execution_id"):
        blocked.append("RUNNER_LOG_CORRELATION_MISSING")
    if correlation.get("kind") == "MANUAL": warnings.append("MANUAL_EXECUTION_NOT_NATURAL_ACCEPTANCE")
    if correlation.get("kind") == "RETRY" and not event.get("retry_of_run_id"): blocked.append("RETRY_PARENT_MISSING")
    final = "FAIL" if failures else "BLOCKED" if blocked else "WARNING" if warnings else "PASS"
    instance_id = str(event.get("instance_id", "UNVERIFIED"))
    trigger_id = str(event.get("record_id", "UNVERIFIED"))
    trigger_utc = str(event.get("trigger_utc", event.get("start", "UNVERIFIED")))
    run_id = make_run_id(config, instance_id, trigger_id, trigger_utc)
    timestamp = capture.get("capture_created_at") or now.isoformat()
    report = {
        "schema_version": "c3_2_7.evidence.v1", "report_id": run_id, "report_revision": 1,
        "report_created_at": timestamp, "report_sha256": "", "run_id": run_id,
        "wrapper_execution_id": capture.get("wrapper_execution_id", "UNVERIFIED"),
        "scheduled_slot": event.get("scheduled_slot"), "execution_kind": correlation.get("kind"),
        "scheduler_task_name": config["scheduler_task_name"], "scheduler_instance_id": instance_id,
        "scheduler_trigger_kind": event.get("trigger_kind"), "scheduler_execution_datetime": event.get("start"),
        "validator_execution_datetime": now.isoformat(), "scheduler_result": assertion(event.get("result"), "OBSERVED", "scheduler.event", event.get("end")),
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


def _safe_directory(directory: Path) -> bool:
    try:
        if os.name == "nt":
            # ACL inspection is delegated to the Windows wrapper/capture.  Do
            # not rewrite ACLs; POSIX mode bits are not meaningful on NTFS.
            return True
        mode = directory.stat().st_mode
        return not bool(mode & 0o002)
    except OSError:
        return False


def write_immutable_report(report: dict[str, Any], root: Path | None = None) -> tuple[str, Path | None, Path | None]:
    root = root or Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "ProcurementMaterialsPlatform" / "evidence" / "c3_2_7"
    day = (parse_datetime(report["report_created_at"]) or datetime.now(UTC)).astimezone(TAIPEI).date().isoformat()
    directory = root / day
    directory.mkdir(parents=True, exist_ok=True)
    if not _safe_directory(directory): return "STORAGE_UNSAFE", None, None
    stem = "c3_2_7-" + report["run_id"]
    target = directory / (stem + ".json")
    encoded = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if target.exists():
        if sha256_bytes(target.read_bytes()) == sha256_bytes(encoded): return "DUPLICATE_SAME", target, directory / (stem + ".md")
        conflict = directory / (stem + ".conflict-" + sha256_bytes(encoded)[:12] + ".json")
        if not conflict.exists(): conflict.write_bytes(encoded)
        return "CONFLICT", target, conflict
    with tempfile.NamedTemporaryFile(mode="wb", dir=directory, prefix="." + stem + ".", delete=False) as temporary:
        temporary.write(encoded); temporary.flush(); os.fsync(temporary.fileno()); temporary_path = Path(temporary.name)
    try:
        os.link(temporary_path, target)
    except FileExistsError:
        temporary_path.unlink(missing_ok=True)
        return "DUPLICATE_SAME", target, directory / (stem + ".md")
    except OSError:
        # A platform without hard-link support cannot prove non-replacement.
        # Leave the temporary file for diagnostics and fail closed.
        temporary_path.unlink(missing_ok=True)
        return "STORAGE_UNSAFE", None, None
    else:
        temporary_path.unlink(missing_ok=True)
    markdown = directory / (stem + ".md")
    markdown.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return "CREATED", target, markdown


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--stage", choices=("core", "final"), default="final")
    args = parser.parse_args()
    try:
        capture = _load_json(args.capture)
        events = _load_json(args.events).get("events", []) if args.events else capture.get("scheduler_events", [])
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
        if status == "CONFLICT": print("C3_2_VALIDATOR=BLOCKED storage=CONFLICT"); return validator_exit(int(capture.get("runner_result", 0)), "BLOCKED")
        if status == "STORAGE_UNSAFE": print("C3_2_VALIDATOR=STORAGE_FAILURE"); return validator_exit(int(capture.get("runner_result", 0)), report["final_result"], storage_failure=True)
        print("C3_2_VALIDATOR=" + report["final_result"] + " storage=" + status + " json=" + str(json_path) + " markdown=" + str(markdown_path))
        return validator_exit(int(capture.get("runner_result", 0)), report["final_result"])
    except Exception as exc:
        print("C3_2_VALIDATOR=INTERNAL error=" + type(exc).__name__)
        return 72


if __name__ == "__main__":
    raise SystemExit(main())
