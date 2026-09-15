from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.c4_3_baseline_forecast_lab import LabError, NOTICES
from scripts.c4_full_minimal_pipeline import (
    INPUT_CLASSIFICATIONS,
    STAGES,
    build_full_track,
    publish_full_track,
)


TEST_TEMP_ROOT = Path(__file__).parents[1] / "runtime"


def observation(
    observation_id: str,
    material_id: str,
    source_id: str,
    source_date: date,
    value: float,
    *,
    unit: str,
) -> dict[str, object]:
    return {
        "observation_id": observation_id,
        "record_id": "record-" + observation_id,
        "material_id": material_id,
        "source_id": source_id,
        "source_date": source_date.isoformat(),
        "price": value,
        "currency": "USD",
        "unit": unit,
        "observed_at": source_date.isoformat() + "T16:00:00+00:00",
        "available_at": datetime.combine(
            source_date + timedelta(days=1), datetime.min.time(), timezone.utc
        ).isoformat(),
        "source_status": "SUCCESS",
        "date_parse_status": "PARSED",
        "anomaly_status": "NOT_EVALUATED",
        "observation_kind": "DAILY_SNAPSHOT",
        "canonical_status": "SHADOW_UNRESOLVED",
        "data_classification": "SHADOW",
        "run_id": "synthetic-run",
        "collector_version": "synthetic-fixture-v1",
        "lineage": {"fixture": True, "classification": "SYNTHETIC_NON_OPERATIONAL"},
    }


def full_rows(days: int = 95) -> list[dict[str, object]]:
    start = date(2026, 1, 1)
    rows: list[dict[str, object]] = []
    for offset in range(days):
        current = start + timedelta(days=offset)
        if current.weekday() >= 5:
            continue
        rows.append(observation(
            f"cu-{offset:03d}", "CU_LME_CASH", "LME_CASH_OFFER", current,
            100.0 + offset * 0.4 + (offset % 4) * 0.3, unit="USD/MT",
        ))
        rows.append(observation(
            f"brent-{offset:03d}", "BRENT_FUT", "YFINANCE_BZ=F", current,
            70.0 + offset * 0.2 - (offset % 3) * 0.25, unit="USD/BBL",
        ))
    return rows


def shadow_export_rows(days: int = 95) -> list[dict[str, object]]:
    rows = full_rows(days)
    for row in rows:
        row["lineage"] = {"basis": "C3_V2_FIELDS", "run_id": "shadow-export-fixture"}
    return rows


class FullMinimalBuildTests(unittest.TestCase):
    def test_every_stage_builds_and_synthetic_closeout_is_never_operational(self):
        run_id, reports, forecast_lines, markdown = build_full_track(
            full_rows(),
            input_digest="a" * 64,
            input_classification="SYNTHETIC_NON_OPERATIONAL",
            generated_at="2026-09-15T03:00:00Z",
        )
        self.assertTrue(run_id.startswith("c4-full-"))
        closeout = reports["c4_7_research_closeout_report.json"]
        self.assertEqual(set(closeout["stage_statuses"]), set(STAGES))
        self.assertEqual(closeout["status"], "RESEARCH_TRACK_COMPLETE_SYNTHETIC_NON_OPERATIONAL")
        self.assertEqual(closeout["formal_production_c4_closeout"], "NOT_AUTHORIZED")
        self.assertEqual(closeout["c5_procurement_automation"], "NOT_AUTHORIZED")
        self.assertTrue(any(row.get("record_type") == "FORECAST" for row in forecast_lines))
        self.assertIn("SYNTHETIC_NON_OPERATIONAL", markdown)

    def test_shadow_research_export_is_rejected_pending_real_data_gate(self):
        with self.assertRaisesRegex(LabError, "REAL_DATA_READINESS_GATE_REQUIRED"):
            build_full_track(
                shadow_export_rows(),
                input_digest="b" * 64,
                input_classification="SHADOW_RESEARCH_EXPORT",
                generated_at="2026-09-15T04:00:00Z",
            )

    def test_synthetic_lineage_cannot_be_promoted_by_cli_classification(self):
        with self.assertRaisesRegex(LabError, "REAL_DATA_READINESS_GATE_REQUIRED"):
            build_full_track(
                full_rows(),
                input_digest="9" * 64,
                input_classification="SHADOW_RESEARCH_EXPORT",
                generated_at="2026-09-15T04:30:00Z",
            )

    def test_synthetic_classification_requires_synthetic_lineage(self):
        with self.assertRaisesRegex(LabError, "SYNTHETIC_CLASSIFICATION_LINEAGE_REQUIRED"):
            build_full_track(
                shadow_export_rows(),
                input_digest="8" * 64,
                input_classification="SYNTHETIC_NON_OPERATIONAL",
                generated_at="2026-09-15T04:45:00Z",
            )

    def test_invalid_synthetic_fixture_markers_are_rejected(self):
        for marker in ("false", "0", "yes", "no"):
            with self.subTest(marker=marker), self.assertRaisesRegex(
                LabError, "INVALID_SYNTHETIC_FIXTURE_MARKER"
            ):
                rows = full_rows(10)
                for row in rows:
                    row["lineage"] = {"fixture": marker}
                build_full_track(
                    rows,
                    input_digest="7" * 64,
                    input_classification="SYNTHETIC_NON_OPERATIONAL",
                    generated_at="2026-09-15T04:50:00Z",
                )

    def test_synthetic_outputs_have_one_authoritative_classification(self):
        _, reports, forecast_lines, _ = build_full_track(
            full_rows(),
            input_digest="6" * 64,
            input_classification="SYNTHETIC_NON_OPERATIONAL",
            generated_at="2026-09-15T04:55:00Z",
        )

        def classifications(value: object) -> list[str]:
            found: list[str] = []
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {
                        "classification",
                        "input_classification",
                        "research_classification",
                        "operational_status",
                    }:
                        found.append(str(item))
                    found.extend(classifications(item))
            elif isinstance(value, list):
                for item in value:
                    found.extend(classifications(item))
            return found

        values = classifications({"reports": reports, "forecasts": forecast_lines})
        self.assertTrue(values)
        self.assertEqual(set(values), {"SYNTHETIC_NON_OPERATIONAL"})

    def test_empty_synthetic_input_remains_non_operational_and_data_insufficient(self):
        _, reports, forecast_lines, _ = build_full_track(
            [],
            input_digest="0" * 64,
            input_classification="SYNTHETIC_NON_OPERATIONAL",
            generated_at="2026-09-15T05:00:00Z",
        )
        self.assertEqual(
            reports["c4_7_research_closeout_report.json"]["status"],
            "RESEARCH_TRACK_COMPLETE_SYNTHETIC_NON_OPERATIONAL",
        )
        self.assertEqual(reports["c4_1_data_readiness_report.json"]["status"], "INSUFFICIENT_DATA")
        self.assertEqual(len([row for row in forecast_lines if row.get("record_type") == "FORECAST"]), 0)

    def test_input_classification_is_explicit_and_allowlisted(self):
        self.assertEqual(INPUT_CLASSIFICATIONS, ("SYNTHETIC_NON_OPERATIONAL",))
        with self.assertRaisesRegex(LabError, "INVALID_INPUT_CLASSIFICATION"):
            build_full_track(
                full_rows(10),
                input_digest="c" * 64,
                input_classification="PRODUCTION",
                generated_at="2026-09-15T06:00:00Z",
            )

    def test_contract_and_point_in_time_stages_pass(self):
        _, reports, _, _ = build_full_track(
            full_rows(),
            input_digest="d" * 64,
            input_classification="SYNTHETIC_NON_OPERATIONAL",
            generated_at="2026-09-15T07:00:00Z",
        )
        contract = reports["c4_0_contract_report.json"]
        point_in_time = reports["c4_2_point_in_time_report.json"]
        self.assertEqual(contract["status"], "PASS")
        self.assertEqual(contract["assertions"]["stages_exact"], list(STAGES))
        self.assertEqual(point_in_time["status"], "PASS")
        self.assertGreater(point_in_time["check_count"], 0)
        self.assertEqual(point_in_time["violation_count"], 0)

    def test_comparison_and_multi_material_results_are_populated(self):
        _, reports, _, _ = build_full_track(
            full_rows(),
            input_digest="e" * 64,
            input_classification="SYNTHETIC_NON_OPERATIONAL",
            generated_at="2026-09-15T08:00:00Z",
        )
        comparison = reports["c4_4_baseline_comparison_report.json"]
        shadow = reports["c4_5_shadow_research_report.json"]
        self.assertEqual(comparison["status"], "COMPLETE")
        self.assertEqual(
            set(comparison["method_results"]),
            {"last_observation_direction", "moving_average_trend", "momentum_direction", "volatility_flag", "risk_flag"},
        )
        self.assertEqual(shadow["material_count"], 2)
        self.assertGreater(shadow["forecast_count"], 0)
        self.assertEqual(shadow["production_or_procurement_action"], "NONE")

    def test_repeated_build_is_byte_stable_at_same_declared_instant(self):
        args = {
            "input_digest": "f" * 64,
            "input_classification": "SYNTHETIC_NON_OPERATIONAL",
            "generated_at": "2026-09-15T09:00:00Z",
        }
        first = build_full_track(full_rows(), **args)
        second = build_full_track(full_rows(), **args)
        self.assertEqual(first, second)
        self.assertEqual(first[1]["c4_6_reproducibility_report.json"]["status"], "PASS")
        self.assertTrue(first[1]["c4_6_reproducibility_report.json"]["same_input_same_build"])

    def test_all_generated_content_carries_notices_and_omits_raw_prices(self):
        _, reports, forecast_lines, markdown = build_full_track(
            full_rows(),
            input_digest="1" * 64,
            input_classification="SYNTHETIC_NON_OPERATIONAL",
            generated_at="2026-09-15T10:00:00Z",
        )
        self.assertTrue(all(tuple(report["notices"]) == NOTICES for report in reports.values()))
        self.assertTrue(all(tuple(row["notices"]) == NOTICES for row in forecast_lines))
        serialized = json.dumps({"reports": reports, "forecasts": forecast_lines})
        self.assertNotIn('"price"', serialized)
        for notice in NOTICES:
            self.assertIn(notice, markdown)


class FullMinimalPublicationTests(unittest.TestCase):
    def setUp(self):
        self.result = build_full_track(
            full_rows(),
            input_digest="2" * 64,
            input_classification="SYNTHETIC_NON_OPERATIONAL",
            generated_at="2026-09-15T11:00:00Z",
        )

    def test_publish_writes_complete_hashed_local_package(self):
        run_id, reports, forecast_lines, markdown = self.result
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            permitted = Path(temporary)
            output = publish_full_track(
                run_id,
                reports,
                forecast_lines,
                markdown,
                output_root=permitted / "runs",
                permitted_root=permitted,
            )
            manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
            expected_payloads = {path.name for path in output.iterdir()} - {"run_manifest.json"}
            self.assertEqual(set(manifest["files"]), expected_payloads)
            self.assertEqual(len(expected_payloads), 14)
            self.assertEqual(set(manifest["stage_files"]), set(STAGES))
            self.assertEqual(tuple(manifest["notices"]), NOTICES)
            self.assertEqual(manifest["ml_model"], "NONE")
            for name, expected in manifest["files"].items():
                payload = (output / name).read_bytes()
                self.assertEqual(len(payload), expected["bytes"], name)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), expected["sha256"], name)

            guardrails = {
                "google_sheets_write": "DISABLED",
                "production_a_l_write": "DISABLED",
                "canonical_promotion": "DISABLED",
                "deferred_persistence": "DISABLED",
                "ml_model": "NONE",
                "procurement_signal": "DISABLED",
                "procurement_decision": "NOT_AUTHORIZED",
                "production_allowed": False,
                "canonical_allowed": False,
                "procurement_allowed": False,
            }

            def assert_guardrails(artifact: dict[str, object], name: str) -> None:
                for key, expected in guardrails.items():
                    self.assertEqual(artifact.get(key), expected, f"{name}:{key}")

            for path in output.iterdir():
                if path.suffix == ".json":
                    assert_guardrails(json.loads(path.read_text(encoding="utf-8")), path.name)
                elif path.suffix == ".jsonl":
                    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                        assert_guardrails(json.loads(line), f"{path.name}:{line_number}")
                elif path.suffix == ".md":
                    text = path.read_text(encoding="utf-8")
                    for expected in (
                        "Google Sheets write: `DISABLED`",
                        "Production A:L write: `DISABLED`",
                        "Canonical promotion: `DISABLED`",
                        "Deferred persistence: `DISABLED`",
                        "ML model: `NONE`",
                        "Procurement signal: `DISABLED`",
                        "Procurement decision: `NOT_AUTHORIZED`",
                    ):
                        self.assertIn(expected, text)

    def test_publish_is_non_overwriting(self):
        run_id, reports, forecast_lines, markdown = self.result
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            permitted = Path(temporary)
            publish_full_track(
                run_id, reports, forecast_lines, markdown,
                output_root=permitted, permitted_root=permitted,
            )
            with self.assertRaisesRegex(LabError, "RUN_OUTPUT_ALREADY_EXISTS_NO_OVERWRITE"):
                publish_full_track(
                    run_id, reports, forecast_lines, markdown,
                    output_root=permitted, permitted_root=permitted,
                )

    def test_publish_refuses_escape_from_local_boundary(self):
        run_id, reports, forecast_lines, markdown = self.result
        with (
            tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as permitted,
            tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as outside,
        ):
            with self.assertRaisesRegex(LabError, "OUTPUT_ROOT_OUTSIDE_LOCAL_RESEARCH_BOUNDARY"):
                publish_full_track(
                    run_id,
                    reports,
                    forecast_lines,
                    markdown,
                    output_root=Path(outside),
                    permitted_root=Path(permitted),
                )

    def test_pipeline_module_has_no_network_sheet_or_ml_import(self):
        source = (Path(__file__).parents[1] / "scripts" / "c4_full_minimal_pipeline.py").read_text(encoding="utf-8")
        for forbidden in ("gspread", "requests", "sklearn", "tensorflow", "torch"):
            self.assertNotIn("import " + forbidden, source)


class DocumentationBoundaryTests(unittest.TestCase):
    def test_both_docs_state_the_synthetic_only_gate_boundaries(self):
        repository = Path(__file__).parents[1]
        required = (
            "SYNTHETIC_NON_OPERATIONAL",
            "SYNTHETIC_FIXTURE",
            "Local Synthetic Proof-of-Pipeline",
            "Not real Market_Observation_V2 backtest",
            "Not Production",
            "Not Canonical",
            "Not Procurement Decision",
            "Not C4 closeout",
            "Real Data Readiness Gate",
            "Production Forecast Gate",
        )
        for relative_path in (
            "docs/C4_3_BASELINE_FORECAST_LAB.md",
            "docs/C4_FULL_MINIMAL_TRACK.md",
        ):
            with self.subTest(path=relative_path):
                text = (repository / relative_path).read_text(encoding="utf-8")
                for notice in required:
                    self.assertIn(notice, text, f"{relative_path}:{notice}")


if __name__ == "__main__":
    unittest.main()
