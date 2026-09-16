"""C4.0-C4.7 full minimal local research pipeline.

The pipeline orchestrates the validated C4.3 baseline lab and emits one
local-only result package. It has no network, Google Sheets, Production,
Canonical, ML, or procurement-action capability.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from c4_3_baseline_forecast_lab import (
        ALGORITHM_VERSION as BASELINE_ALGORITHM_VERSION,
        HANDOFF_WORKING_REFERENCE,
        HORIZONS,
        NOTICES,
        TARGETS,
        LabError,
        build_reports as build_baseline_reports,
        enforce_local_safety,
        load_local_rows,
    )
except ModuleNotFoundError:
    from scripts.c4_3_baseline_forecast_lab import (
        ALGORITHM_VERSION as BASELINE_ALGORITHM_VERSION,
        HANDOFF_WORKING_REFERENCE,
        HORIZONS,
        NOTICES,
        TARGETS,
        LabError,
        build_reports as build_baseline_reports,
        enforce_local_safety,
        load_local_rows,
    )


PIPELINE_VERSION = "C4_FULL_MINIMAL_TRACK_V1"
STAGES = ("C4.0", "C4.1", "C4.2", "C4.3", "C4.4", "C4.5", "C4.6", "C4.7")
INPUT_CLASSIFICATIONS = ("SYNTHETIC_NON_OPERATIONAL",)
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = REPO_ROOT / "runtime" / "c4-full-minimal-track"
HANDOFF_SOURCES = (
    REPO_ROOT / "docs" / "C3_CLOSING_SUMMARY.md",
    REPO_ROOT / "docs" / "C3_2_7_CLOSEOUT.md",
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _row_has_synthetic_lineage(row: Mapping[str, Any]) -> bool:
    lineage = row.get("lineage")
    if isinstance(lineage, str) and lineage.strip():
        try:
            lineage = json.loads(lineage)
        except json.JSONDecodeError as exc:
            raise LabError("INVALID_LINEAGE_JSON") from exc
    if not isinstance(lineage, Mapping):
        return False
    fixture = lineage.get("fixture")
    if "fixture" in lineage and not isinstance(fixture, bool):
        raise LabError("INVALID_SYNTHETIC_FIXTURE_MARKER")
    classification = str(lineage.get("classification", "")).strip().upper()
    if fixture is False and classification == "SYNTHETIC_NON_OPERATIONAL":
        raise LabError("CONFLICTING_SYNTHETIC_LINEAGE")
    return fixture is True or classification == "SYNTHETIC_NON_OPERATIONAL"


def _validate_input_classification(rows: Sequence[Mapping[str, Any]], input_classification: str) -> None:
    synthetic_flags = [_row_has_synthetic_lineage(row) for row in rows]
    if rows and not all(synthetic_flags):
        raise LabError("SYNTHETIC_CLASSIFICATION_LINEAGE_REQUIRED")


def _validate_authorized_input_classification(input_classification: str) -> None:
    normalized = input_classification.strip().upper()
    real_or_shadow_markers = ("SHADOW", "REAL_DATA", "MARKET_OBSERVATION_V2")
    if any(marker in normalized for marker in real_or_shadow_markers):
        raise LabError("REAL_DATA_READINESS_GATE_REQUIRED")
    if input_classification not in INPUT_CLASSIFICATIONS:
        raise LabError("INVALID_INPUT_CLASSIFICATION")


def _common(
    pipeline_run_id: str,
    baseline_run_id: str,
    generated_at: str,
    input_digest: str,
    input_classification: str,
) -> dict[str, Any]:
    return {
        "pipeline_run_id": pipeline_run_id,
        "baseline_run_id": baseline_run_id,
        "generated_at": generated_at,
        "pipeline_version": PIPELINE_VERSION,
        "baseline_algorithm_version": BASELINE_ALGORITHM_VERSION,
        "handoff_working_reference": HANDOFF_WORKING_REFERENCE,
        "input_sha256": input_digest,
        "input_classification": input_classification,
        "research_classification": "SYNTHETIC_NON_OPERATIONAL",
        "operational_status": "SYNTHETIC_NON_OPERATIONAL",
        "data_origin": "SYNTHETIC_FIXTURE",
        "notices": list(NOTICES),
        "horizons_days": list(HORIZONS),
        "target_scope": list(TARGETS),
        "ml_model": "NONE",
        "google_sheets_write": "DISABLED",
        "production_forecast": "DISABLED",
        "production_a_l_write": "DISABLED",
        "canonical_promotion": "DISABLED",
        "deferred_persistence": "DISABLED",
        "production_allowed": False,
        "canonical_allowed": False,
        "procurement_allowed": False,
        "procurement_decision": "NOT_AUTHORIZED",
        "procurement_signal": "DISABLED",
    }


def _contract_report(common: Mapping[str, Any]) -> dict[str, Any]:
    sources = []
    missing = []
    for path in HANDOFF_SOURCES:
        if path.is_file():
            sources.append({
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256_file(path),
            })
        else:
            missing.append(path.relative_to(REPO_ROOT).as_posix())
    assertions = {
        "stages_exact": list(STAGES),
        "horizons_exact": list(HORIZONS),
        "targets_exact": list(TARGETS),
        "baseline_only": True,
        "no_ml": True,
        "local_only": True,
        "research_only": True,
        "legacy_unverified_training_truth": "FORBIDDEN",
        "sheet_write": "DISABLED",
        "production_and_canonical": "DISABLED",
        "deferred_persistence": "DISABLED",
        "procurement_signal": "DISABLED",
        "procurement_decision": "NOT_AUTHORIZED",
    }
    return {
        **common,
        "stage": "C4.0",
        "report_type": "CONTRACT_REPORT",
        "status": "PASS" if not missing else "BLOCKED",
        "handoff_sources": sources,
        "missing_handoff_sources": missing,
        "assertions": assertions,
    }


def _readiness_report(common: Mapping[str, Any], baseline_reports: Mapping[str, Any], input_count: int) -> dict[str, Any]:
    backtest = baseline_reports["backtest_summary.json"]
    exclusions = baseline_reports["exclusion_report.json"]
    eligible = int(backtest["eligible_observation_count"])
    reasons = dict(exclusions["reason_counts"])
    return {
        **common,
        "stage": "C4.1",
        "report_type": "DATA_READINESS_REPORT",
        "status": "READY_FOR_LOCAL_RESEARCH" if eligible else "INSUFFICIENT_DATA",
        "input_observation_count": input_count,
        "eligible_observation_count": eligible,
        "excluded_observation_count": int(exclusions["input_exclusion_count"]),
        "exclusion_reason_counts": reasons,
        "legacy_unverified_excluded_count": int(reasons.get("LEGACY_UNVERIFIED_EXCLUDED", 0)),
        "series_identity": ["material_id", "source_id", "currency", "unit"],
        "collector_availability_basis": "available_at OR C3 observation_at",
        "source_native_publication_timestamp": "NOT_VERIFIED",
        "canonical_truth": "NOT_AVAILABLE_NOT_USED",
    }


def _point_in_time_report(common: Mapping[str, Any], baseline_reports: Mapping[str, Any]) -> dict[str, Any]:
    leakage = baseline_reports["leakage_check_report.json"]
    status = leakage["status"]
    return {
        **common,
        "stage": "C4.2",
        "report_type": "POINT_IN_TIME_DATASET_REPORT",
        "status": status,
        "cutoff_basis": "ORIGIN_COLLECTOR_AVAILABLE_AT",
        "feature_rule": "available_at <= cutoff AND source_date <= origin_source_date",
        "truth_rule": "exact origin_source_date + horizon_days AND available_at > cutoff",
        "neighbour_date_substitution": "FORBIDDEN",
        "late_revision_feature_use": "FORBIDDEN",
        "check_count": int(leakage["check_count"]),
        "violation_count": int(leakage["violation_count"]),
    }


def _combine_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    evaluated = sum(int(row["evaluated"]) for row in rows)
    correct = sum(int(row["correct"]) for row in rows)
    return {
        "evaluated": evaluated,
        "correct": correct,
        "accuracy": round(correct / evaluated, 6) if evaluated else None,
    }


def _comparison_report(common: Mapping[str, Any], baseline_reports: Mapping[str, Any]) -> dict[str, Any]:
    by_horizon = baseline_reports["backtest_summary.json"]["results_by_horizon"]
    direction_methods = ("last_observation_direction", "moving_average_trend", "momentum_direction")
    method_results: dict[str, Any] = {}
    for method in direction_methods:
        method_results[method] = {
            "target": "direction",
            **_combine_metrics([by_horizon[str(h)]["direction"][method] for h in HORIZONS]),
        }
    method_results["volatility_flag"] = {
        "target": "volatility",
        **_combine_metrics([by_horizon[str(h)]["volatility"] for h in HORIZONS]),
    }
    method_results["risk_flag"] = {
        "target": "risk",
        **_combine_metrics([by_horizon[str(h)]["risk"] for h in HORIZONS]),
    }
    evaluated = sum(item["evaluated"] for item in method_results.values())
    return {
        **common,
        "stage": "C4.4",
        "report_type": "BASELINE_COMPARISON_REPORT",
        "status": "COMPLETE" if evaluated else "INSUFFICIENT_DATA",
        "method_results": method_results,
        "results_by_horizon": by_horizon,
        "selection_or_promotion": "NONE",
        "procurement_interpretation": "FORBIDDEN",
    }


def _shadow_research_report(common: Mapping[str, Any], forecast_lines: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    forecasts = [row for row in forecast_lines if row.get("record_type") == "FORECAST"]
    materials: dict[str, dict[str, Any]] = {}
    for row in forecasts:
        origin = row["origin"]
        material = str(origin["material_id"])
        summary = materials.setdefault(material, {
            "forecast_count": 0,
            "horizons": {str(value): 0 for value in HORIZONS},
            "source_series": set(),
        })
        summary["forecast_count"] += 1
        summary["horizons"][str(row["horizon_days"])] += 1
        summary["source_series"].add("|".join((
            str(origin["source_id"]), str(origin["currency"]), str(origin["unit"]),
        )))
    serializable = {
        key: {**value, "source_series": sorted(value["source_series"])}
        for key, value in sorted(materials.items())
    }
    return {
        **common,
        "stage": "C4.5",
        "report_type": "SHADOW_RESEARCH_RUN_REPORT",
        "status": "SYNTHETIC_NON_OPERATIONAL",
        "forecast_count": len(forecasts),
        "material_count": len(serializable),
        "materials": serializable,
        "forecast_artifact": "research_forecasts.jsonl",
        "production_or_procurement_action": "NONE",
    }


def _reproducibility_report(
    common: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    first_reports: Mapping[str, Any],
    first_lines: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    repeated_run_id, repeated_reports, repeated_lines = build_baseline_reports(
        rows,
        input_digest=str(common["input_sha256"]),
        generated_at=str(common["generated_at"]),
    )
    repeat_equal = (
        repeated_run_id == common["baseline_run_id"]
        and _canonical_bytes(repeated_reports) == _canonical_bytes(first_reports)
        and _canonical_bytes(repeated_lines) == _canonical_bytes(first_lines)
    )
    code_files = (
        Path(__file__).resolve(),
        REPO_ROOT / "scripts" / "c4_3_baseline_forecast_lab.py",
    )
    code_digests = {
        path.relative_to(REPO_ROOT).as_posix(): _sha256_file(path)
        for path in code_files
    }
    fingerprint = _sha256_bytes(_canonical_bytes({
        "input_sha256": common["input_sha256"],
        "pipeline_version": PIPELINE_VERSION,
        "baseline_algorithm_version": BASELINE_ALGORITHM_VERSION,
        "horizons": HORIZONS,
        "targets": TARGETS,
        "code_digests": code_digests,
    }))
    return {
        **common,
        "stage": "C4.6",
        "report_type": "REPRODUCIBILITY_REPORT",
        "status": "PASS" if repeat_equal else "FAIL",
        "same_input_same_build": repeat_equal,
        "reproducibility_fingerprint": fingerprint,
        "code_sha256": code_digests,
        "network_dependency": "NONE",
        "ml_dependency": "NONE",
    }


def _closeout_report(common: Mapping[str, Any], stages: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    contract_ok = stages["C4.0"]["status"] == "PASS"
    leakage_ok = stages["C4.2"]["status"] in {"PASS", "NOT_EVALUATED_INSUFFICIENT_DATA"}
    reproducible = stages["C4.6"]["status"] == "PASS"
    if not (contract_ok and leakage_ok and reproducible):
        status = "FAIL"
    else:
        status = "RESEARCH_TRACK_COMPLETE_SYNTHETIC_NON_OPERATIONAL"
    stage_statuses = {key: value["status"] for key, value in stages.items()}
    stage_statuses["C4.7"] = status
    return {
        **common,
        "stage": "C4.7",
        "report_type": "RESEARCH_CLOSEOUT_REPORT",
        "status": status,
        "stage_statuses": stage_statuses,
        "verified": [
            "C4.0-C4.7 local pipeline executes",
            "C4.3 deterministic baselines only",
            "point-in-time leakage checks run for every evaluated case",
            "Legacy and unapproved Canonical rows are excluded",
            "results retain identity, availability, quality/trust, and lineage",
            "same input and timestamp reproduce identical baseline reports",
        ],
        "not_verified": [
            "actual Market_Observation_V2 historical export performance",
            "source-native publication timestamp",
            "authoritative regional holiday calendar",
            "Canonical or Production truth",
            "Production forecast performance",
        ],
        "formal_production_c4_closeout": "NOT_AUTHORIZED",
        "c5_procurement_automation": "NOT_AUTHORIZED",
    }


def _decorate_baseline_reports(
    common: Mapping[str, Any], baseline_reports: Mapping[str, Any]
) -> dict[str, Any]:
    decorated: dict[str, Any] = {}
    for name, report in baseline_reports.items():
        item: Mapping[str, Any] = {
            **report,
            **common,
            "stage": "C4.3",
        }
        if common["operational_status"] == "SYNTHETIC_NON_OPERATIONAL":
            item = _normalize_synthetic_classifications(item)
        decorated[name] = item
    return decorated


def _decorate_forecasts(
    common: Mapping[str, Any], forecast_lines: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    decorated = [{**row, **common, "stage": "C4.3"} for row in forecast_lines]
    if common["operational_status"] == "SYNTHETIC_NON_OPERATIONAL":
        return [_normalize_synthetic_classifications(row) for row in decorated]
    return decorated


def _normalize_synthetic_classifications(value: Any) -> Any:
    """Make SYNTHETIC_NON_OPERATIONAL authoritative at every payload level."""
    if isinstance(value, Mapping):
        return {
            key: (
                "SYNTHETIC_NON_OPERATIONAL"
                if key in {"classification", "research_classification", "operational_status"}
                else _normalize_synthetic_classifications(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_synthetic_classifications(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_synthetic_classifications(item) for item in value)
    return value


def build_full_track(
    rows: Sequence[Mapping[str, Any]],
    *,
    input_digest: str,
    input_classification: str,
    generated_at: str | None = None,
) -> tuple[str, Mapping[str, Any], list[Mapping[str, Any]], str]:
    _validate_authorized_input_classification(input_classification)
    _validate_input_classification(rows, input_classification)
    generated_at = generated_at or _iso_now()
    baseline_run_id, original_baseline_reports, original_forecast_lines = build_baseline_reports(
        rows, input_digest=input_digest, generated_at=generated_at
    )
    pipeline_seed = {
        "pipeline_version": PIPELINE_VERSION,
        "baseline_run_id": baseline_run_id,
        "input_classification": input_classification,
    }
    pipeline_run_id = "c4-full-" + _sha256_bytes(_canonical_bytes(pipeline_seed))
    common = _common(
        pipeline_run_id, baseline_run_id, generated_at, input_digest, input_classification
    )
    baseline_reports = _decorate_baseline_reports(common, original_baseline_reports)
    forecast_lines = _decorate_forecasts(common, original_forecast_lines)

    stage_reports: dict[str, Mapping[str, Any]] = {}
    stage_reports["C4.0"] = _contract_report(common)
    stage_reports["C4.1"] = _readiness_report(common, baseline_reports, len(rows))
    stage_reports["C4.2"] = _point_in_time_report(common, baseline_reports)
    stage_reports["C4.3"] = {
        **common,
        "stage": "C4.3",
        "report_type": "BASELINE_LAB_STAGE_REPORT",
        "status": baseline_reports["backtest_summary.json"]["status"],
        "forecast_count": baseline_reports["backtest_summary.json"]["forecast_count"],
        "artifacts": sorted([*baseline_reports, "research_forecasts.jsonl"]),
    }
    stage_reports["C4.4"] = _comparison_report(common, baseline_reports)
    stage_reports["C4.5"] = _shadow_research_report(common, forecast_lines)
    stage_reports["C4.6"] = _reproducibility_report(
        common, rows, original_baseline_reports, original_forecast_lines
    )
    stage_reports["C4.7"] = _closeout_report(common, stage_reports)

    reports: dict[str, Any] = {
        "c4_0_contract_report.json": stage_reports["C4.0"],
        "c4_1_data_readiness_report.json": stage_reports["C4.1"],
        "c4_2_point_in_time_report.json": stage_reports["C4.2"],
        **baseline_reports,
        "c4_3_stage_report.json": stage_reports["C4.3"],
        "c4_4_baseline_comparison_report.json": stage_reports["C4.4"],
        "c4_5_shadow_research_report.json": stage_reports["C4.5"],
        "c4_6_reproducibility_report.json": stage_reports["C4.6"],
        "c4_7_research_closeout_report.json": stage_reports["C4.7"],
    }
    markdown = render_result_markdown(common, stage_reports)
    return pipeline_run_id, reports, forecast_lines, markdown


def render_result_markdown(common: Mapping[str, Any], stages: Mapping[str, Mapping[str, Any]]) -> str:
    lines = [
        "# C4 Full Minimal Track — Local Research Result",
        "",
        *[f"**{notice}**  " for notice in NOTICES],
        "",
        f"- Pipeline run: `{common['pipeline_run_id']}`",
        f"- Input classification: `{common['input_classification']}`",
        f"- Research classification: `{common['research_classification']}`",
        f"- Operational status: `{common['operational_status']}`",
        f"- Data origin: `{common['data_origin']}`",
        f"- Horizons: `{', '.join(str(value) for value in HORIZONS)} days`",
        f"- Targets: `{', '.join(TARGETS)}`",
        f"- ML model: `NONE`",
        f"- Google Sheets write: `DISABLED`",
        f"- Production A:L write: `DISABLED`",
        f"- Canonical promotion: `DISABLED`",
        f"- Deferred persistence: `DISABLED`",
        f"- Procurement signal: `DISABLED`",
        f"- Procurement decision: `NOT_AUTHORIZED`",
        "",
        "## Stage results",
        "",
        "| Stage | Status |",
        "| --- | --- |",
    ]
    lines.extend(f"| {stage} | {stages[stage]['status']} |" for stage in STAGES)
    lines.extend([
        "",
        "## Boundary",
        "",
        "Local Synthetic Proof-of-Pipeline",
        "Not real Market_Observation_V2 backtest",
        "Not Shadow export validation",
        "",
        "This is only local synthetic implementation evidence. It is Not C4 closeout and",
        "does not approve Production forecasting, Canonical promotion, Google Sheets/A:L",
        "writes, ML, or procurement decisions/signals.",
        "",
        "## Remaining evidence gap",
        "",
    ])
    lines.extend(f"- {item}" for item in stages["C4.7"]["not_verified"])
    return "\n".join(lines) + "\n"


def publish_full_track(
    pipeline_run_id: str,
    reports: Mapping[str, Any],
    forecast_lines: Sequence[Mapping[str, Any]],
    markdown: str,
    *,
    output_root: Path = OUTPUT_ROOT,
    permitted_root: Path = OUTPUT_ROOT,
) -> Path:
    output_root = output_root.resolve()
    permitted_root = permitted_root.resolve()
    if not _within(output_root, permitted_root):
        raise LabError("OUTPUT_ROOT_OUTSIDE_LOCAL_RESEARCH_BOUNDARY")
    output_root.mkdir(parents=True, exist_ok=True)
    run_directory = output_root / pipeline_run_id
    try:
        run_directory.mkdir()
    except FileExistsError as exc:
        raise LabError("RUN_OUTPUT_ALREADY_EXISTS_NO_OVERWRITE") from exc

    payloads = {name: _json_bytes(report) for name, report in reports.items()}
    payloads["research_forecasts.jsonl"] = _jsonl_bytes(forecast_lines)
    payloads["c4_full_minimal_result.md"] = markdown.encode("utf-8")
    for name, payload in payloads.items():
        (run_directory / name).write_bytes(payload)

    closeout = reports["c4_7_research_closeout_report.json"]
    manifest = {
        **{key: closeout[key] for key in (
            "pipeline_run_id", "baseline_run_id", "generated_at", "pipeline_version",
            "baseline_algorithm_version", "handoff_working_reference", "input_sha256",
            "input_classification", "research_classification", "notices", "horizons_days",
            "target_scope", "ml_model", "google_sheets_write", "production_forecast",
            "production_a_l_write", "canonical_promotion", "deferred_persistence",
            "production_allowed", "canonical_allowed", "procurement_allowed",
            "procurement_decision", "procurement_signal", "operational_status", "data_origin",
        )},
        "report_type": "C4_FULL_MINIMAL_RUN_MANIFEST",
        "status": closeout["status"],
        "stage_files": {
            "C4.0": ["c4_0_contract_report.json"],
            "C4.1": ["c4_1_data_readiness_report.json", "exclusion_report.json", "lineage_report.json"],
            "C4.2": ["c4_2_point_in_time_report.json", "leakage_check_report.json"],
            "C4.3": ["c4_3_stage_report.json", "backtest_summary.json", "research_forecasts.jsonl"],
            "C4.4": ["c4_4_baseline_comparison_report.json"],
            "C4.5": ["c4_5_shadow_research_report.json"],
            "C4.6": ["c4_6_reproducibility_report.json"],
            "C4.7": ["c4_7_research_closeout_report.json", "c4_full_minimal_result.md"],
        },
        "files": {
            name: {"sha256": _sha256_bytes(payload), "bytes": len(payload)}
            for name, payload in sorted(payloads.items())
        },
    }
    (run_directory / "run_manifest.json").write_bytes(_json_bytes(manifest))
    return run_directory


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C4.0-C4.7 full minimal local research pipeline")
    parser.add_argument("--input", required=True, type=Path, help="Local CSV, JSON, or JSONL observations")
    parser.add_argument(
        "--input-classification",
        required=True,
        help="Must be SYNTHETIC_NON_OPERATIONAL; real and Shadow inputs require a later gate",
    )
    args = parser.parse_args(argv)
    enforce_local_safety()
    try:
        rows, input_digest = load_local_rows(args.input.resolve())
        pipeline_run_id, reports, forecast_lines, markdown = build_full_track(
            rows,
            input_digest=input_digest,
            input_classification=args.input_classification,
        )
        output = publish_full_track(pipeline_run_id, reports, forecast_lines, markdown)
    except (LabError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        print("C4_FULL_MINIMAL_TRACK=FAIL reason=" + str(exc))
        return 2
    status = reports["c4_7_research_closeout_report.json"]["status"]
    print("C4_FULL_MINIMAL_TRACK=" + status + " run_id=" + pipeline_run_id + " output=" + str(output))
    return 2 if status == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
