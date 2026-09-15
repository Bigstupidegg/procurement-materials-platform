from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.c4_3_baseline_forecast_lab import (
    HORIZONS,
    NOTICES,
    TARGETS,
    LabError,
    baseline_predictions,
    build_backtest,
    build_reports,
    enforce_local_safety,
    load_local_rows,
    normalize_observation,
    prepare_observations,
    publish_reports,
)


TEST_TEMP_ROOT = Path(__file__).parents[1] / "runtime"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def shadow_row(
    observation_id: str,
    source_date: date,
    value: float,
    *,
    available_at: datetime | None = None,
    observation_at: str | None = None,
    trust_state: str | None = None,
    observation_kind: str = "DAILY_SNAPSHOT",
    canonical_status: str = "SHADOW_UNRESOLVED",
) -> dict[str, object]:
    available = available_at or datetime.combine(source_date + timedelta(days=1), datetime.min.time(), timezone.utc)
    row: dict[str, object] = {
        "observation_id": observation_id,
        "record_id": "record-" + observation_id,
        "material_id": "CU_LME_CASH",
        "source_id": "LME_CASH_OFFER",
        "currency": "USD",
        "unit": "USD/MT",
        "source_date": source_date.isoformat(),
        "price": value,
        "observed_at": source_date.isoformat() + "T16:00:00+00:00",
        "source_status": "SUCCESS",
        "date_parse_status": "PARSED",
        "anomaly_status": "NOT_EVALUATED",
        "observation_kind": observation_kind,
        "canonical_status": canonical_status,
        "data_classification": "SHADOW",
        "run_id": "run-fixture",
        "collector_version": "fixture-v1",
        "lineage": {"fixture": True, "source": "synthetic-test-only"},
    }
    if observation_at is not None:
        row["observation_at"] = observation_at
    else:
        row["available_at"] = available.isoformat()
    if trust_state is not None:
        row["trust_state"] = trust_state
    return row


def history_rows(days: int = 75) -> list[dict[str, object]]:
    start = date(2026, 1, 1)
    return [
        shadow_row(
            f"obs-{offset:03d}",
            start + timedelta(days=offset),
            100.0 + offset * 0.4 + (offset % 5) * 0.7,
        )
        for offset in range(days)
        if (start + timedelta(days=offset)).weekday() < 5
    ]


class C43NormalizationTests(unittest.TestCase):
    def test_c3_observation_at_maps_to_collector_availability_and_preserves_audit_fields(self):
        raw = shadow_row(
            "obs-map",
            date(2026, 1, 1),
            100.0,
            observation_at="2026-01-02T08:00:00+08:00",
        )
        item = normalize_observation(raw, 1)
        metadata = item.metadata()
        self.assertEqual(metadata["available_at"], "2026-01-02T08:00:00+08:00")
        self.assertEqual(metadata["available_at_basis"], "C3_OBSERVATION_AT_COLLECTOR_AVAILABILITY")
        self.assertEqual(metadata["observed_at"], "2026-01-01T16:00:00+00:00")
        self.assertEqual(metadata["trust_state"], "SHADOW_RESEARCH_ONLY")
        self.assertEqual(metadata["research_classification"], "SHADOW_RESEARCH_ONLY")
        self.assertEqual(metadata["lineage"], raw["lineage"])
        self.assertEqual(metadata["quality_status"]["basis"], "C3_V2_STATUS_FIELDS")

    def test_naive_availability_is_excluded_not_inferred(self):
        raw = shadow_row("obs-naive", date(2026, 1, 1), 100.0)
        raw["available_at"] = "2026-01-02T08:00:00"
        eligible, exclusions = prepare_observations([raw])
        self.assertEqual(eligible, [])
        self.assertEqual(exclusions[0]["reason"], "AVAILABLE_AT_MUST_BE_TIMEZONE_AWARE")

    def test_legacy_is_never_eligible(self):
        raw = shadow_row(
            "legacy-1",
            date(2026, 1, 1),
            100.0,
            observation_kind="LEGACY_UNVERIFIED",
            canonical_status="LEGACY_UNVERIFIED",
        )
        eligible, exclusions = prepare_observations([raw])
        self.assertEqual(eligible, [])
        self.assertEqual(exclusions[0]["reason"], "LEGACY_UNVERIFIED_EXCLUDED")

    def test_legacy_data_classification_alone_is_still_excluded(self):
        raw = shadow_row("legacy-classification", date(2026, 1, 1), 100.0)
        raw["data_classification"] = "LEGACY_UNVERIFIED"
        eligible, exclusions = prepare_observations([raw])
        self.assertEqual(eligible, [])
        self.assertEqual(exclusions[0]["reason"], "LEGACY_UNVERIFIED_EXCLUDED")

    def test_duplicate_id_with_different_content_fails_closed(self):
        first = shadow_row("obs-conflict", date(2026, 1, 1), 100.0)
        second = shadow_row("obs-conflict", date(2026, 1, 1), 101.0)
        with self.assertRaisesRegex(LabError, "OBSERVATION_ID_CONFLICT"):
            prepare_observations([first, second])

    def test_unapproved_canonical_input_is_excluded(self):
        raw = shadow_row("obs-canonical", date(2026, 1, 1), 100.0, canonical_status="CANONICAL")
        eligible, exclusions = prepare_observations([raw])
        self.assertEqual(eligible, [])
        self.assertEqual(exclusions[0]["reason"], "CANONICAL_NOT_APPROVED")

    def test_missing_quality_status_fields_are_not_assumed_eligible(self):
        raw = shadow_row("obs-missing-quality", date(2026, 1, 1), 100.0)
        raw["source_status"] = ""
        eligible, exclusions = prepare_observations([raw])
        self.assertEqual(eligible, [])
        self.assertEqual(exclusions[0]["reason"], "MISSING_SOURCE_STATUS")

    def test_missing_provable_lineage_is_excluded(self):
        raw = shadow_row("obs-missing-lineage", date(2026, 1, 1), 100.0)
        for key in ("lineage", "record_id", "run_id", "collector_version", "migration_version"):
            raw.pop(key, None)
        eligible, exclusions = prepare_observations([raw])
        self.assertEqual(eligible, [])
        self.assertEqual(exclusions[0]["reason"], "MISSING_PROVABLE_LINEAGE")

    def test_non_positive_value_is_excluded_before_return_calculation(self):
        raw = shadow_row("obs-zero", date(2026, 1, 1), 0.0)
        eligible, exclusions = prepare_observations([raw])
        self.assertEqual(eligible, [])
        self.assertEqual(exclusions[0]["reason"], "NON_POSITIVE_NUMERIC_VALUE")

    def test_weekend_source_date_is_excluded_not_shifted(self):
        raw = shadow_row("obs-weekend", date(2026, 1, 3), 100.0)
        eligible, exclusions = prepare_observations([raw])
        self.assertEqual(eligible, [])
        self.assertEqual(exclusions[0]["reason"], "WEEKEND_SOURCE_DATE_NOT_ELIGIBLE")


class C43BacktestTests(unittest.TestCase):
    def setUp(self):
        self.observations, exclusions = prepare_observations(history_rows())
        self.assertEqual(exclusions, [])

    def test_only_approved_horizons_targets_and_baselines_are_emitted(self):
        forecasts, checks, _ = build_backtest(self.observations)
        self.assertTrue(forecasts)
        self.assertEqual({row["horizon_days"] for row in forecasts}, set(HORIZONS))
        self.assertTrue(all(tuple(row["target_scope"]) == TARGETS for row in forecasts))
        self.assertEqual(
            set(forecasts[-1]["baseline_predictions"]),
            {
                "last_observation_direction",
                "moving_average_trend",
                "momentum_direction",
                "volatility_flag",
                "risk_flag",
            },
        )
        self.assertEqual(len(checks), len(forecasts))

    def test_every_evaluated_forecast_passes_point_in_time_checks(self):
        forecasts, checks, _ = build_backtest(self.observations)
        self.assertTrue(forecasts)
        self.assertTrue(all(check["status"] == "PASS" for check in checks))
        self.assertTrue(all(check["violations"] == [] for check in checks))
        for row in forecasts:
            self.assertNotIn(row["truth"]["observation_id"], row["feature_observation_ids"])
            self.assertEqual(
                date.fromisoformat(row["target_source_date"]),
                date.fromisoformat(row["origin"]["source_date"]) + timedelta(days=row["horizon_days"]),
            )

    def test_late_revision_cannot_leak_into_earlier_origin(self):
        rows = history_rows()
        rows.append(
            shadow_row(
                "obs-late-revision",
                date(2026, 1, 10),
                999.0,
                available_at=datetime(2026, 2, 20, tzinfo=timezone.utc),
            )
        )
        observations, _ = prepare_observations(rows)
        forecasts, checks, _ = build_backtest(observations)
        origin_id = "obs-029"
        origin_forecasts = [row for row in forecasts if row["origin"]["observation_id"] == origin_id]
        self.assertTrue(origin_forecasts)
        self.assertTrue(all("obs-late-revision" not in row["feature_observation_ids"] for row in origin_forecasts))
        self.assertTrue(all(check["status"] == "PASS" for check in checks))

    def test_different_source_or_unit_never_enters_the_same_feature_series(self):
        rows = history_rows()
        other = shadow_row(
            "obs-other-unit",
            date(2026, 1, 29),
            99999.0,
            available_at=datetime(2026, 1, 30, tzinfo=timezone.utc),
        )
        other["source_id"] = "OTHER_SOURCE"
        other["unit"] = "USD/LB"
        rows.append(other)
        observations, _ = prepare_observations(rows)
        forecasts, _, _ = build_backtest(observations)
        base_forecasts = [row for row in forecasts if row["origin"]["observation_id"] == "obs-029"]
        self.assertTrue(base_forecasts)
        self.assertTrue(all("obs-other-unit" not in row["feature_observation_ids"] for row in base_forecasts))

    def test_missing_exact_target_is_excluded_without_neighbour_substitution(self):
        rows = [row for row in history_rows() if row["source_date"] != "2026-01-08"]
        observations, _ = prepare_observations(rows)
        _, _, exclusions = build_backtest(observations)
        match = [
            row for row in exclusions
            if row.get("observation_id") == "obs-000" and row.get("horizon_days") == 7
        ]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["target_source_date"], "2026-01-08")
        self.assertEqual(match[0]["reason"], "MISSING_EXACT_HORIZON_TRUTH")

    def test_baselines_return_explicit_insufficient_data_instead_of_guessing(self):
        single = self.observations[0]
        predictions = baseline_predictions([single], single, 7)
        self.assertTrue(all(value == "INSUFFICIENT_DATA" for value in predictions.values()))


class C43ReportTests(unittest.TestCase):
    def test_reports_retain_required_metadata_and_notices_without_raw_prices(self):
        run_id, reports, forecast_lines = build_reports(
            history_rows(), input_digest="a" * 64, generated_at="2026-09-15T00:00:00Z"
        )
        self.assertTrue(run_id.startswith("c4-3-"))
        for report in reports.values():
            self.assertEqual(tuple(report["notices"]), NOTICES)
            self.assertEqual(tuple(report["horizons_days"]), HORIZONS)
            self.assertEqual(tuple(report["target_scope"]), TARGETS)
            self.assertEqual(report["google_sheets_write"], "DISABLED")
            self.assertEqual(report["production_a_l_write"], "DISABLED")
            self.assertEqual(report["canonical_promotion"], "DISABLED")
            self.assertEqual(report["deferred_persistence"], "DISABLED")
            self.assertEqual(report["ml_model"], "NONE")
            self.assertEqual(report["procurement_signal"], "DISABLED")
            self.assertEqual(report["procurement_decision"], "NOT_AUTHORIZED")
            self.assertIs(report["production_allowed"], False)
            self.assertIs(report["canonical_allowed"], False)
            self.assertIs(report["procurement_allowed"], False)
            self.assertEqual(report["operational_status"], "SYNTHETIC_NON_OPERATIONAL")
            self.assertEqual(report["data_origin"], "SYNTHETIC_FIXTURE")
        self.assertEqual(forecast_lines[0]["record_type"], "NOTICE")
        forecast = next(row for row in forecast_lines if row["record_type"] == "FORECAST")
        for side in ("origin", "truth"):
            self.assertTrue(
                {"observation_id", "source_date", "observed_at", "available_at", "quality_status", "trust_state", "lineage"}
                <= set(forecast[side])
            )
        serialized = json.dumps({"reports": reports, "forecasts": forecast_lines})
        self.assertNotIn('"price"', serialized)
        self.assertNotIn('"value"', serialized)

    def test_empty_evaluation_is_explicitly_insufficient_not_complete(self):
        _, reports, _ = build_reports([], input_digest="0" * 64, generated_at="2026-09-15T00:00:00Z")
        self.assertEqual(reports["backtest_summary.json"]["status"], "INSUFFICIENT_DATA")
        self.assertEqual(
            reports["leakage_check_report.json"]["status"],
            "NOT_EVALUATED_INSUFFICIENT_DATA",
        )

    def test_non_synthetic_rows_are_rejected_before_report_build(self):
        rows = history_rows()
        for row in rows:
            row["lineage"] = {"basis": "C3_V2_FIELDS", "run_id": "shadow-export"}
        with self.assertRaisesRegex(LabError, "SYNTHETIC_FIXTURE_LINEAGE_REQUIRED"):
            build_reports(
                rows,
                input_digest="9" * 64,
                generated_at="2026-09-15T00:30:00Z",
            )

    def test_publish_creates_only_expected_local_files_and_manifest_hashes(self):
        run_id, reports, forecast_lines = build_reports(
            history_rows(), input_digest="b" * 64, generated_at="2026-09-15T01:00:00Z"
        )
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            permitted = Path(temporary)
            output = publish_reports(
                run_id,
                reports,
                forecast_lines,
                output_root=permitted / "runs",
                permitted_root=permitted,
            )
            self.assertEqual(
                {path.name for path in output.iterdir()},
                {
                    "run_manifest.json",
                    "research_forecasts.jsonl",
                    "backtest_summary.json",
                    "leakage_check_report.json",
                    "exclusion_report.json",
                    "lineage_report.json",
                },
            )
            manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(set(manifest["files"]), {path.name for path in output.iterdir()} - {"run_manifest.json"})
            self.assertEqual(tuple(manifest["notices"]), NOTICES)

    def test_publish_refuses_output_outside_permitted_root(self):
        run_id, reports, forecast_lines = build_reports(
            history_rows(), input_digest="c" * 64, generated_at="2026-09-15T02:00:00Z"
        )
        with (
            tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary,
            tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as outside,
        ):
            with self.assertRaisesRegex(LabError, "OUTPUT_ROOT_OUTSIDE_LOCAL_RESEARCH_BOUNDARY"):
                publish_reports(
                    run_id,
                    reports,
                    forecast_lines,
                    output_root=Path(outside),
                    permitted_root=Path(temporary),
                )

    def test_jsonl_local_loader_and_digest(self):
        rows = history_rows(2)
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "observations.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            loaded, digest = load_local_rows(path)
        self.assertEqual(loaded, rows)
        self.assertEqual(len(digest), 64)

    def test_safety_forces_all_inherited_write_gates_off(self):
        with patch.dict(
            os.environ,
            {
                "ALLOW_GOOGLE_SHEET_WRITE": "1",
                "ALLOW_PENDING_RAW_WRITE": "1",
                "CONTROLLED_WRITE_APPROVAL": "approved",
            },
            clear=False,
        ):
            enforce_local_safety()
            self.assertEqual(os.environ["ALLOW_GOOGLE_SHEET_WRITE"], "0")
            self.assertEqual(os.environ["ALLOW_PENDING_RAW_WRITE"], "0")
            self.assertNotIn("CONTROLLED_WRITE_APPROVAL", os.environ)

    def test_module_has_no_sheet_network_or_ml_dependency(self):
        module_path = Path(__file__).parents[1] / "scripts" / "c4_3_baseline_forecast_lab.py"
        source = module_path.read_text(encoding="utf-8")
        for forbidden in ("gspread", "requests", "sklearn", "tensorflow", "torch"):
            self.assertNotIn("import " + forbidden, source)


if __name__ == "__main__":
    unittest.main()
