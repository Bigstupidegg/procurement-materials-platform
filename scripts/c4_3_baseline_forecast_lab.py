"""C4.3 local-only baseline forecast research lab.

This module consumes only an explicitly supplied local CSV, JSON, or JSONL
export.  It has no network or Google Sheets client, performs no persistence
outside the ignored local runtime directory, and emits no procurement signal.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Iterable, Mapping, Sequence


ALGORITHM_VERSION = "C4.3_BASELINE_RESEARCH_V1"
HANDOFF_WORKING_REFERENCE = "7aeddc3758d142fe4f563be5de8ba83fe04aec23"
HORIZONS = (7, 14, 28)
TARGETS = ("direction", "volatility", "risk")
NOTICES = (
    "Research Forecast Only",
    "Not Production",
    "Not Canonical",
    "Not Procurement Decision",
)
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = REPO_ROOT / "runtime" / "c4_3-baseline-forecast-lab"


class LabError(RuntimeError):
    """A deterministic input, safety, or publication failure."""


@dataclass(frozen=True)
class ResearchObservation:
    observation_id: str
    material_id: str
    source_id: str
    currency: str
    unit: str
    source_date: date
    value: float
    observed_at: str | None
    available_at: datetime
    available_at_basis: str
    quality_status: Mapping[str, Any]
    trust_state: str
    trust_state_basis: str
    lineage: Mapping[str, Any]
    research_classification: str

    def metadata(self) -> dict[str, Any]:
        """Return audit metadata without exposing the observed numeric value."""
        return {
            "observation_id": self.observation_id,
            "material_id": self.material_id,
            "source_id": self.source_id,
            "currency": self.currency,
            "unit": self.unit,
            "source_date": self.source_date.isoformat(),
            "observed_at": self.observed_at,
            "available_at": _iso_datetime(self.available_at),
            "available_at_basis": self.available_at_basis,
            "quality_status": dict(self.quality_status),
            "trust_state": self.trust_state,
            "trust_state_basis": self.trust_state_basis,
            "lineage": dict(self.lineage),
            "research_classification": self.research_classification,
        }


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _upper(value: object) -> str:
    return _text(value).upper()


def _iso_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_date(value: object) -> date:
    try:
        return date.fromisoformat(_text(value))
    except ValueError as exc:
        raise LabError("INVALID_SOURCE_DATE") from exc


def _parse_datetime(value: object) -> datetime:
    raw = _text(value)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise LabError("INVALID_AVAILABLE_AT") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LabError("AVAILABLE_AT_MUST_BE_TIMEZONE_AWARE")
    return parsed


def _parse_value(value: object) -> float:
    try:
        parsed = float(Decimal(_text(value)))
    except (InvalidOperation, ValueError) as exc:
        raise LabError("INVALID_NUMERIC_VALUE") from exc
    if not math.isfinite(parsed):
        raise LabError("INVALID_NUMERIC_VALUE")
    if parsed <= 0:
        raise LabError("NON_POSITIVE_NUMERIC_VALUE")
    return parsed


def _parse_lineage(value: object, raw: Mapping[str, Any], row_number: int) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        parsed = dict(value)
        if parsed:
            return parsed
        raise LabError("MISSING_PROVABLE_LINEAGE")
    if _text(value):
        try:
            parsed = json.loads(_text(value))
        except json.JSONDecodeError as exc:
            raise LabError("INVALID_LINEAGE_JSON") from exc
        if not isinstance(parsed, Mapping):
            raise LabError("LINEAGE_MUST_BE_OBJECT")
        if parsed:
            return dict(parsed)
        raise LabError("MISSING_PROVABLE_LINEAGE")
    fallback = {
        "basis": "C3_V2_FIELDS",
        "input_row": row_number,
        "record_id": _text(raw.get("record_id")) or None,
        "run_id": _text(raw.get("run_id")) or None,
        "collector_version": _text(raw.get("collector_version")) or None,
        "migration_version": _text(raw.get("migration_version")) or None,
    }
    if not any(fallback[key] for key in ("record_id", "run_id", "collector_version", "migration_version")):
        raise LabError("MISSING_PROVABLE_LINEAGE")
    return fallback


def _quality_status(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    supplied = raw.get("quality_status")
    if isinstance(supplied, Mapping):
        return {"basis": "INPUT", **dict(supplied)}
    if _text(supplied):
        return {"basis": "INPUT", "value": _text(supplied)}
    return {
        "basis": "C3_V2_STATUS_FIELDS",
        "source_status": _text(raw.get("source_status")) or "NOT_VERIFIED",
        "date_parse_status": _text(raw.get("date_parse_status")) or "NOT_VERIFIED",
        "anomaly_status": _text(raw.get("anomaly_status")) or "NOT_VERIFIED",
        "observation_kind": _text(raw.get("observation_kind")) or "NOT_VERIFIED",
        "canonical_status": _text(raw.get("canonical_status")) or "NOT_VERIFIED",
    }


def _trust_state(raw: Mapping[str, Any]) -> tuple[str, str]:
    supplied = _upper(raw.get("trust_state"))
    if supplied:
        return supplied, "INPUT"
    states = {
        _upper(raw.get("observation_kind")),
        _upper(raw.get("canonical_status")),
        _upper(raw.get("data_classification")),
    }
    states.discard("")
    if "LEGACY_UNVERIFIED" in states:
        return "LEGACY_UNVERIFIED", "C3_V2_STATUS_FIELDS"
    if "CANONICAL" in states:
        return "CANONICAL", "C3_V2_STATUS_FIELDS"
    if "SHADOW_UNRESOLVED" in states or "SHADOW" in states:
        return "SHADOW_RESEARCH_ONLY", "C3_V2_STATUS_FIELDS"
    return "NOT_VERIFIED", "C3_V2_STATUS_FIELDS"


def normalize_observation(raw: Mapping[str, Any], row_number: int) -> ResearchObservation:
    observation_id = _text(raw.get("observation_id"))
    material_id = _text(raw.get("material_id"))
    source_id = _text(raw.get("source_id"))
    currency = _text(raw.get("currency"))
    unit = _text(raw.get("unit"))
    if not observation_id:
        raise LabError("MISSING_OBSERVATION_ID")
    if not material_id:
        raise LabError("MISSING_MATERIAL_ID")
    if not source_id:
        raise LabError("MISSING_SOURCE_ID")
    if not currency:
        raise LabError("MISSING_CURRENCY")
    if not unit:
        raise LabError("MISSING_UNIT")

    available_raw = raw.get("available_at")
    available_at_basis = "INPUT_AVAILABLE_AT"
    if not _text(available_raw):
        available_raw = raw.get("observation_at")
        available_at_basis = "C3_OBSERVATION_AT_COLLECTOR_AVAILABILITY"
    if not _text(available_raw):
        raise LabError("MISSING_AVAILABLE_AT")

    trust_state, trust_basis = _trust_state(raw)
    classification = "SHADOW_RESEARCH_ONLY" if trust_state == "SHADOW_RESEARCH_ONLY" else "RESEARCH_ONLY"
    value = raw.get("price") if _text(raw.get("price")) else raw.get("value")
    observed_at = _text(raw.get("observed_at")) or None
    return ResearchObservation(
        observation_id=observation_id,
        material_id=material_id,
        source_id=source_id,
        currency=currency,
        unit=unit,
        source_date=_parse_date(raw.get("source_date")),
        value=_parse_value(value),
        observed_at=observed_at,
        available_at=_parse_datetime(available_raw),
        available_at_basis=available_at_basis,
        quality_status=_quality_status(raw),
        trust_state=trust_state,
        trust_state_basis=trust_basis,
        lineage=_parse_lineage(raw.get("lineage"), raw, row_number),
        research_classification=classification,
    )


def _exclusion_metadata(raw: Mapping[str, Any], row_number: int, reason: str) -> dict[str, Any]:
    lineage: Mapping[str, Any]
    try:
        lineage = _parse_lineage(raw.get("lineage"), raw, row_number)
    except LabError:
        lineage = {"basis": "INVALID_OR_UNAVAILABLE", "input_row": row_number}
    return {
        "input_row": row_number,
        "observation_id": _text(raw.get("observation_id")) or None,
        "material_id": _text(raw.get("material_id")) or None,
        "source_id": _text(raw.get("source_id")) or None,
        "currency": _text(raw.get("currency")) or None,
        "unit": _text(raw.get("unit")) or None,
        "source_date": _text(raw.get("source_date")) or None,
        "observed_at": _text(raw.get("observed_at")) or None,
        "available_at": _text(raw.get("available_at") or raw.get("observation_at")) or None,
        "quality_status": _quality_status(raw),
        "trust_state": _trust_state(raw)[0],
        "lineage": dict(lineage),
        "reason": reason,
    }


def prepare_observations(rows: Sequence[Mapping[str, Any]]) -> tuple[list[ResearchObservation], list[dict[str, Any]]]:
    eligible: list[ResearchObservation] = []
    exclusions: list[dict[str, Any]] = []
    seen: dict[str, ResearchObservation] = {}
    for row_number, raw in enumerate(rows, start=1):
        states = {
            _upper(raw.get("trust_state")),
            _upper(raw.get("observation_kind")),
            _upper(raw.get("canonical_status")),
            _upper(raw.get("data_classification")),
        }
        if "LEGACY_UNVERIFIED" in states:
            exclusions.append(_exclusion_metadata(raw, row_number, "LEGACY_UNVERIFIED_EXCLUDED"))
            continue
        if any(state == "CANONICAL" or state.startswith("CANONICAL_") for state in states):
            exclusions.append(_exclusion_metadata(raw, row_number, "CANONICAL_NOT_APPROVED"))
            continue
        if not _upper(raw.get("source_status")):
            exclusions.append(_exclusion_metadata(raw, row_number, "MISSING_SOURCE_STATUS"))
            continue
        if _upper(raw.get("source_status")) not in {"SUCCESS", "RETRY_SUCCESS"}:
            exclusions.append(_exclusion_metadata(raw, row_number, "SOURCE_STATUS_NOT_ELIGIBLE"))
            continue
        if not _upper(raw.get("date_parse_status")):
            exclusions.append(_exclusion_metadata(raw, row_number, "MISSING_DATE_PARSE_STATUS"))
            continue
        if _upper(raw.get("date_parse_status")) != "PARSED":
            exclusions.append(_exclusion_metadata(raw, row_number, "SOURCE_DATE_NOT_PARSED"))
            continue
        try:
            observation = normalize_observation(raw, row_number)
        except LabError as exc:
            exclusions.append(_exclusion_metadata(raw, row_number, str(exc)))
            continue
        if observation.trust_state != "SHADOW_RESEARCH_ONLY":
            exclusions.append(_exclusion_metadata(raw, row_number, "TRUST_STATE_NOT_RESEARCH_ELIGIBLE"))
            continue
        if observation.source_date.weekday() >= 5:
            exclusions.append(_exclusion_metadata(raw, row_number, "WEEKEND_SOURCE_DATE_NOT_ELIGIBLE"))
            continue
        prior = seen.get(observation.observation_id)
        if prior is not None:
            if prior != observation:
                raise LabError("OBSERVATION_ID_CONFLICT")
            exclusions.append(_exclusion_metadata(raw, row_number, "DUPLICATE_SAME_OBSERVATION_ID"))
            continue
        seen[observation.observation_id] = observation
        eligible.append(observation)
    eligible.sort(key=lambda item: (item.material_id, item.available_at, item.source_date, item.observation_id))
    return eligible, exclusions


def load_local_rows(path: Path) -> tuple[list[Mapping[str, Any]], str]:
    if not path.is_file():
        raise LabError("INPUT_FILE_NOT_FOUND")
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    suffix = path.suffix.lower()
    text = payload.decode("utf-8-sig")
    if suffix == ".csv":
        rows = list(csv.DictReader(text.splitlines()))
    elif suffix == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    elif suffix == ".json":
        parsed = json.loads(text)
        rows = parsed if isinstance(parsed, list) else parsed.get("observations", []) if isinstance(parsed, Mapping) else []
    else:
        raise LabError("INPUT_FORMAT_MUST_BE_CSV_JSON_OR_JSONL")
    if not all(isinstance(row, Mapping) for row in rows):
        raise LabError("INPUT_ROWS_MUST_BE_OBJECTS")
    return list(rows), digest


def _latest_by_source_date(
    observations: Iterable[ResearchObservation], *, cutoff: datetime, through: date
) -> list[ResearchObservation]:
    latest: dict[date, ResearchObservation] = {}
    for item in observations:
        if item.available_at > cutoff or item.source_date > through:
            continue
        prior = latest.get(item.source_date)
        if prior is None or (item.available_at, item.observation_id) > (prior.available_at, prior.observation_id):
            latest[item.source_date] = item
    return [latest[key] for key in sorted(latest)]


def _returns(observations: Sequence[ResearchObservation]) -> list[float]:
    values: list[float] = []
    for previous, current in zip(observations, observations[1:]):
        if previous.value != 0:
            values.append(current.value / previous.value - 1.0)
    return values


def _direction(change: float) -> str:
    if change > 0:
        return "UP"
    if change < 0:
        return "DOWN"
    return "FLAT"


def _window(observations: Sequence[ResearchObservation], start: date, end: date) -> list[ResearchObservation]:
    return [item for item in observations if start <= item.source_date <= end]


def baseline_predictions(history: Sequence[ResearchObservation], origin: ResearchObservation, horizon: int) -> dict[str, str]:
    last_direction = "INSUFFICIENT_DATA"
    if len(history) >= 2:
        last_direction = _direction(history[-1].value - history[-2].value)

    trailing = _window(history, origin.source_date - timedelta(days=horizon), origin.source_date)
    moving_average = "INSUFFICIENT_DATA"
    if len(trailing) >= 2:
        moving_average = _direction(origin.value - fmean(item.value for item in trailing))

    momentum = "INSUFFICIENT_DATA"
    comparison_date = origin.source_date - timedelta(days=horizon)
    comparison = [item for item in history if item.source_date == comparison_date]
    if comparison:
        momentum = _direction(origin.value - comparison[-1].value)

    prior_window = _window(
        history,
        origin.source_date - timedelta(days=horizon * 2),
        origin.source_date - timedelta(days=horizon + 1),
    )
    current_returns = _returns(trailing)
    prior_returns = _returns(prior_window)
    volatility = "INSUFFICIENT_DATA"
    if len(current_returns) >= 2 and len(prior_returns) >= 2:
        volatility = "ELEVATED" if pstdev(current_returns) > pstdev(prior_returns) else "NORMAL"

    known_directions = [value for value in (last_direction, moving_average, momentum) if value != "INSUFFICIENT_DATA"]
    risk = "INSUFFICIENT_DATA"
    if volatility != "INSUFFICIENT_DATA" and known_directions:
        disagreement = len(set(known_directions)) > 1
        risk = "ELEVATED" if volatility == "ELEVATED" or disagreement else "NORMAL"
    return {
        "last_observation_direction": last_direction,
        "moving_average_trend": moving_average,
        "momentum_direction": momentum,
        "volatility_flag": volatility,
        "risk_flag": risk,
    }


def _first_truth(
    observations: Sequence[ResearchObservation], origin: ResearchObservation, target_date: date
) -> ResearchObservation | None:
    candidates = [
        item for item in observations
        if item.material_id == origin.material_id
        and item.source_date == target_date
        and item.available_at > origin.available_at
    ]
    return min(candidates, key=lambda item: (item.available_at, item.observation_id), default=None)


def realized_targets(
    material_observations: Sequence[ResearchObservation],
    history: Sequence[ResearchObservation],
    origin: ResearchObservation,
    truth: ResearchObservation,
    horizon: int,
) -> dict[str, str]:
    direction = _direction(truth.value - origin.value)
    trailing = _window(history, origin.source_date - timedelta(days=horizon), origin.source_date)
    historical_returns = _returns(trailing)

    first_future: dict[date, ResearchObservation] = {}
    for item in material_observations:
        if not origin.source_date < item.source_date <= truth.source_date:
            continue
        if not origin.available_at < item.available_at <= truth.available_at:
            continue
        prior = first_future.get(item.source_date)
        if prior is None or (item.available_at, item.observation_id) < (prior.available_at, prior.observation_id):
            first_future[item.source_date] = item
    forward = [origin] + [first_future[key] for key in sorted(first_future)]
    forward_returns = _returns(forward)

    volatility = "INSUFFICIENT_DATA"
    if len(historical_returns) >= 2 and len(forward_returns) >= 2:
        volatility = "ELEVATED" if pstdev(forward_returns) > pstdev(historical_returns) else "NORMAL"

    risk = "INSUFFICIENT_DATA"
    if historical_returns:
        horizon_return = abs(truth.value / origin.value - 1.0) if origin.value else math.inf
        historical_limit = max(abs(value) for value in historical_returns)
        risk = "ELEVATED" if volatility == "ELEVATED" or horizon_return > historical_limit else "NORMAL"
    return {"direction": direction, "volatility": volatility, "risk": risk}


def _forecast_id(origin: ResearchObservation, horizon: int) -> str:
    payload = "\x1f".join((ALGORITHM_VERSION, origin.observation_id, str(horizon)))
    return "forecast-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_backtest(observations: Sequence[ResearchObservation]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_series: dict[tuple[str, str, str, str], list[ResearchObservation]] = {}
    for item in observations:
        key = (item.material_id, item.source_id, item.currency, item.unit)
        by_series.setdefault(key, []).append(item)

    forecasts: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    case_exclusions: list[dict[str, Any]] = []
    for series_key in sorted(by_series):
        material_id, source_id, currency, unit = series_key
        material = by_series[series_key]
        for origin in material:
            history = _latest_by_source_date(material, cutoff=origin.available_at, through=origin.source_date)
            if not history or history[-1].observation_id != origin.observation_id:
                case_exclusions.append({
                    "observation_id": origin.observation_id,
                    "material_id": material_id,
                    "source_id": source_id,
                    "currency": currency,
                    "unit": unit,
                    "source_date": origin.source_date.isoformat(),
                    "reason": "ORIGIN_NOT_LATEST_POINT_IN_TIME_VERSION",
                })
                continue
            for horizon in HORIZONS:
                target_date = origin.source_date + timedelta(days=horizon)
                truth = _first_truth(material, origin, target_date)
                if truth is None:
                    case_exclusions.append({
                        "observation_id": origin.observation_id,
                        "material_id": material_id,
                        "source_id": source_id,
                        "currency": currency,
                        "unit": unit,
                        "source_date": origin.source_date.isoformat(),
                        "horizon_days": horizon,
                        "target_source_date": target_date.isoformat(),
                        "reason": "MISSING_EXACT_HORIZON_TRUTH",
                    })
                    continue

                feature_ids = [item.observation_id for item in history]
                violations: list[str] = []
                if any(item.available_at > origin.available_at for item in history):
                    violations.append("FEATURE_AVAILABLE_AFTER_CUTOFF")
                if any(item.source_date > origin.source_date for item in history):
                    violations.append("FEATURE_SOURCE_DATE_AFTER_ORIGIN")
                if truth.observation_id in set(feature_ids):
                    violations.append("TRUTH_INCLUDED_IN_FEATURES")
                if truth.source_date != target_date:
                    violations.append("NON_EXACT_HORIZON_TRUTH")
                if truth.available_at <= origin.available_at:
                    violations.append("TRUTH_NOT_AVAILABLE_AFTER_CUTOFF")

                forecast_id = _forecast_id(origin, horizon)
                check = {
                    "forecast_id": forecast_id,
                    "cutoff": _iso_datetime(origin.available_at),
                    "max_feature_available_at": _iso_datetime(max(item.available_at for item in history)),
                    "feature_count": len(history),
                    "truth_available_at": _iso_datetime(truth.available_at),
                    "target_source_date": target_date.isoformat(),
                    "status": "PASS" if not violations else "FAIL",
                    "violations": violations,
                }
                checks.append(check)
                forecasts.append({
                    "forecast_id": forecast_id,
                    "horizon_days": horizon,
                    "target_source_date": target_date.isoformat(),
                    "target_scope": list(TARGETS),
                    "origin": origin.metadata(),
                    "truth": truth.metadata(),
                    "feature_observation_ids": feature_ids,
                    "baseline_predictions": baseline_predictions(history, origin, horizon),
                    "realized_targets": realized_targets(material, history, origin, truth, horizon),
                    "no_look_ahead_status": check["status"],
                    "research_classification": "SHADOW_RESEARCH_ONLY",
                })
    return forecasts, checks, case_exclusions


def _metric(predictions: Iterable[tuple[str, str]]) -> dict[str, Any]:
    usable = [(predicted, actual) for predicted, actual in predictions if "INSUFFICIENT_DATA" not in {predicted, actual}]
    correct = sum(predicted == actual for predicted, actual in usable)
    return {
        "evaluated": len(usable),
        "correct": correct,
        "accuracy": round(correct / len(usable), 6) if usable else None,
    }


def summarize_backtest(forecasts: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    summary: dict[str, Any] = {}
    for horizon in HORIZONS:
        rows = [row for row in forecasts if row["horizon_days"] == horizon]
        summary[str(horizon)] = {
            "forecast_count": len(rows),
            "direction": {
                method: _metric((row["baseline_predictions"][method], row["realized_targets"]["direction"]) for row in rows)
                for method in ("last_observation_direction", "moving_average_trend", "momentum_direction")
            },
            "volatility": _metric((row["baseline_predictions"]["volatility_flag"], row["realized_targets"]["volatility"]) for row in rows),
            "risk": _metric((row["baseline_predictions"]["risk_flag"], row["realized_targets"]["risk"]) for row in rows),
        }
    return summary


def _reason_counts(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = _text(row.get("reason")) or "UNKNOWN"
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _common(run_id: str, generated_at: str, input_digest: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "generated_at": generated_at,
        "algorithm_version": ALGORITHM_VERSION,
        "handoff_working_reference": HANDOFF_WORKING_REFERENCE,
        "classification": "LOCAL_RESEARCH_ONLY",
        "operational_status": "RESEARCH_ONLY",
        "data_origin": "LOCAL_INPUT",
        "notices": list(NOTICES),
        "horizons_days": list(HORIZONS),
        "target_scope": list(TARGETS),
        "input_sha256": input_digest,
        "ml_model": "NONE",
        "google_sheets_write": "DISABLED",
        "production_forecast": "DISABLED",
        "production_a_l_write": "DISABLED",
        "production_write": "DISABLED",
        "canonical_promotion": "DISABLED",
        "deferred_persistence": "DISABLED",
        "production_allowed": False,
        "canonical_allowed": False,
        "procurement_allowed": False,
        "procurement_signal": "DISABLED",
        "procurement_decision": "NOT_AUTHORIZED",
    }


def build_reports(
    rows: Sequence[Mapping[str, Any]], *, input_digest: str, generated_at: str | None = None
) -> tuple[str, Mapping[str, Any], list[Mapping[str, Any]]]:
    generated_at = generated_at or _iso_datetime(datetime.now(timezone.utc))
    seed = json.dumps(
        {"algorithm_version": ALGORITHM_VERSION, "generated_at": generated_at, "input_sha256": input_digest},
        sort_keys=True,
        separators=(",", ":"),
    )
    run_id = "c4-3-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()
    observations, input_exclusions = prepare_observations(rows)
    forecasts, checks, case_exclusions = build_backtest(observations)
    common = _common(run_id, generated_at, input_digest)
    leakage_status = (
        "FAIL"
        if any(check["status"] != "PASS" for check in checks)
        else "PASS"
        if checks
        else "NOT_EVALUATED_INSUFFICIENT_DATA"
    )
    overall_status = (
        "FAIL"
        if leakage_status == "FAIL"
        else "COMPLETE"
        if forecasts
        else "INSUFFICIENT_DATA"
    )

    reports: dict[str, Any] = {
        "backtest_summary.json": {
            **common,
            "report_type": "BACKTEST_SUMMARY",
            "status": overall_status,
            "eligible_observation_count": len(observations),
            "forecast_count": len(forecasts),
            "results_by_horizon": summarize_backtest(forecasts),
        },
        "leakage_check_report.json": {
            **common,
            "report_type": "NO_LOOK_AHEAD_CHECK",
            "status": leakage_status,
            "check_count": len(checks),
            "violation_count": sum(len(check["violations"]) for check in checks),
            "checks": checks,
        },
        "exclusion_report.json": {
            **common,
            "report_type": "EXCLUSION_REPORT",
            "status": "COMPLETE",
            "input_exclusion_count": len(input_exclusions),
            "backtest_case_exclusion_count": len(case_exclusions),
            "reason_counts": _reason_counts([*input_exclusions, *case_exclusions]),
            "input_exclusions": input_exclusions,
            "backtest_case_exclusions": case_exclusions,
        },
        "lineage_report.json": {
            **common,
            "report_type": "LINEAGE_REPORT",
            "status": "COMPLETE",
            "observations": [item.metadata() for item in observations],
            "forecast_lineage": [
                {
                    "forecast_id": row["forecast_id"],
                    "origin_observation_id": row["origin"]["observation_id"],
                    "truth_observation_id": row["truth"]["observation_id"],
                    "feature_observation_ids": row["feature_observation_ids"],
                    "horizon_days": row["horizon_days"],
                }
                for row in forecasts
            ],
        },
    }
    forecast_lines: list[Mapping[str, Any]] = [
        {**common, "record_type": "NOTICE", "report_type": "RESEARCH_FORECASTS"},
        *({**common, "record_type": "FORECAST", **row} for row in forecasts),
    ]
    return run_id, reports, forecast_lines


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n").encode("utf-8")


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def publish_reports(
    run_id: str,
    reports: Mapping[str, Any],
    forecast_lines: Sequence[Mapping[str, Any]],
    *,
    output_root: Path = OUTPUT_ROOT,
    permitted_root: Path = OUTPUT_ROOT,
) -> Path:
    output_root = output_root.resolve()
    permitted_root = permitted_root.resolve()
    if not _within(output_root, permitted_root):
        raise LabError("OUTPUT_ROOT_OUTSIDE_LOCAL_RESEARCH_BOUNDARY")
    output_root.mkdir(parents=True, exist_ok=True)
    run_directory = output_root / run_id
    try:
        run_directory.mkdir()
    except FileExistsError as exc:
        raise LabError("RUN_OUTPUT_ALREADY_EXISTS_NO_OVERWRITE") from exc

    payloads = {name: _json_bytes(value) for name, value in reports.items()}
    payloads["research_forecasts.jsonl"] = _jsonl_bytes(forecast_lines)
    for name, payload in payloads.items():
        (run_directory / name).write_bytes(payload)

    first = next(iter(reports.values()))
    manifest = {
        **{key: first[key] for key in (
            "run_id", "generated_at", "algorithm_version", "handoff_working_reference", "classification",
            "operational_status", "data_origin", "notices", "horizons_days", "target_scope",
            "input_sha256", "ml_model", "google_sheets_write", "production_forecast",
            "production_a_l_write", "production_write", "canonical_promotion", "deferred_persistence",
            "production_allowed", "canonical_allowed", "procurement_allowed", "procurement_signal",
            "procurement_decision",
        )},
        "report_type": "RUN_MANIFEST",
        "status": reports["backtest_summary.json"]["status"],
        "files": {
            name: {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
            for name, payload in sorted(payloads.items())
        },
    }
    (run_directory / "run_manifest.json").write_bytes(_json_bytes(manifest))
    return run_directory


def enforce_local_safety() -> None:
    os.environ.pop("CONTROLLED_WRITE_APPROVAL", None)
    os.environ["ALLOW_GOOGLE_SHEET_WRITE"] = "0"
    os.environ["ALLOW_PENDING_RAW_WRITE"] = "0"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C4.3 local research-only baseline forecast lab")
    parser.add_argument("--input", required=True, type=Path, help="Local CSV, JSON, or JSONL observation export")
    args = parser.parse_args(argv)
    enforce_local_safety()
    try:
        rows, input_digest = load_local_rows(args.input.resolve())
        run_id, reports, forecast_lines = build_reports(rows, input_digest=input_digest)
        output = publish_reports(run_id, reports, forecast_lines)
    except (LabError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print("C4_3_BASELINE_FORECAST_LAB=FAIL reason=" + str(exc))
        return 2
    final_status = reports["backtest_summary.json"]["status"]
    print(
        "C4_3_BASELINE_FORECAST_LAB=" + final_status
        + " run_id=" + run_id + " output=" + str(output)
    )
    return 0 if final_status == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
