from __future__ import annotations

import ast
import copy
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import inspect
import re
import unittest
from unittest.mock import patch

from scripts import c4_rd_preregistration as preregistration
from scripts.c4_rd_contract import (
    BACKTEST_ENABLED_SOURCES,
    PIT_ENABLED_SOURCES,
    RD3_OPEN_BLOCKERS,
    RESEARCH_ENABLED_SOURCES,
    SAFETY_FLAGS,
    canonical_hash,
    canonical_json_bytes,
)
from scripts.c4_rd_pit_dataset import PITDatasetResult, build_pit_dataset
from scripts.c4_rd_preregistration import (
    PreregistrationHardFail,
    PreregistrationInvalidProtocol,
    ResearchQuestion,
    build_preregistration_protocol,
)
from tests.test_c4_rd_pit_dataset import candidate, feature_definition, request


QUESTION = {
    "objective": "Test incremental Direction evidence.",
    "population_scope": "Synthetic subject fixtures only.",
    "intervention_or_method": "Preregistered candidate evaluated later.",
    "comparator": "Three frozen Direction baselines.",
    "outcome": "Balanced accuracy at the primary horizon.",
}
EXPECTED_TOP_LEVEL_KEYS = {
    "contract_version",
    "preregistration_version",
    "research_protocol_id",
    "research_question",
    "research_task",
    "research_subjects",
    "horizon_plan",
    "dataset_bindings",
    "feature_binding",
    "label_protocol",
    "baseline_plan",
    "metric_plan",
    "evaluation_protocol",
    "exclusion_policy",
    "reporting_policy",
    "authorization_snapshot",
}


def plain(value):
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if hasattr(value, "items"):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def horizon_request(horizon: str, **kwargs):
    return request(
        label_specification={
            "label_definition_id": "SYNTHETIC_DIRECTION",
            "label_horizon": horizon,
            "label_reference_id": f"SYNTHETIC_{horizon}_REFERENCE",
        },
        **kwargs,
    )


def synthetic_results() -> dict[str, PITDatasetResult]:
    source = candidate()
    return {
        horizon: build_pit_dataset((horizon_request(horizon),), (source,))
        for horizon in preregistration.HORIZONS
    }


def build_protocol(
    results: dict[str, PITDatasetResult],
    *,
    version: str = "1.0.0",
    protocol_id: str = "C4-RD-7-SYNTHETIC-DIRECTION",
    question=None,
    subjects=None,
    runtime_metadata=None,
):
    return build_preregistration_protocol(
        preregistration_version=version,
        research_protocol_id=protocol_id,
        research_question=QUESTION if question is None else question,
        research_subjects=["SYNTHETIC_SUBJECT_A"] if subjects is None else subjects,
        dataset_results_by_horizon=results,
        runtime_metadata={} if runtime_metadata is None else runtime_metadata,
    )


def replace_request_scope(result: PITDatasetResult, mutate) -> PITDatasetResult:
    entries = [plain(item) for item in result.manifest.request_scope]
    mutate(entries)
    entries.sort(
        key=lambda item: (
            item["research_cutoff_at"],
            item["research_subject_id"],
            item["observation_version_id"],
            canonical_json_bytes(item),
        )
    )
    manifest = replace(result.manifest, request_scope=tuple(entries))
    return replace(result, manifest=manifest)


def replace_manifest(result: PITDatasetResult, **changes) -> PITDatasetResult:
    return replace(result, manifest=replace(result.manifest, **changes))


def forge_manifest(result: PITDatasetResult, **changes) -> PITDatasetResult:
    """Create hostile exact-type input to exercise RD-7 defense-in-depth."""

    manifest = copy.copy(result.manifest)
    for name, value in changes.items():
        object.__setattr__(manifest, name, value)
    forged = object.__new__(PITDatasetResult)
    object.__setattr__(forged, "rows", result.rows)
    object.__setattr__(forged, "manifest", manifest)
    object.__setattr__(forged, "diagnostics", result.diagnostics)
    return forged


def with_horizon(
    results: dict[str, PITDatasetResult], horizon: str, replacement: PITDatasetResult
) -> dict[str, PITDatasetResult]:
    changed = dict(results)
    changed[horizon] = replacement
    return changed


def assert_no_float(test: unittest.TestCase, value) -> None:
    if isinstance(value, dict):
        for item in value.values():
            assert_no_float(test, item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_no_float(test, item)
    else:
        test.assertNotIsInstance(value, float)


class TestC4RDPreregistration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.results = synthetic_results()

    def test_valid_true_integration_fixture_is_deterministic(self):
        first = build_protocol(self.results)
        second = build_protocol(self.results)
        self.assertEqual(first.validation_status, "VALID")
        self.assertEqual(first.preregistration_identity, second.preregistration_identity)
        self.assertRegex(first.preregistration_identity, r"^[0-9a-f]{64}$")
        self.assertEqual(
            first.preregistration_identity,
            canonical_hash("PREREGISTRATION_CONTENT", first.protocol.semantic_projection()),
        )
        row_id_sets = [result.manifest.ordered_row_ids for result in self.results.values()]
        self.assertEqual(row_id_sets[0], row_id_sets[1])
        self.assertEqual(row_id_sets[1], row_id_sets[2])
        self.assertEqual(len({result.dataset_identity for result in self.results.values()}), 3)
        self.assertTrue(all(
            row.operational_status == "SYNTHETIC_NON_OPERATIONAL"
            for result in self.results.values()
            for row in result.rows
        ))

    def test_semantic_projection_has_exact_top_level_shape(self):
        projection = build_protocol(self.results).protocol.semantic_projection()
        self.assertEqual(set(projection), EXPECTED_TOP_LEVEL_KEYS)
        self.assertNotIn("runtime_metadata", projection)
        self.assertNotIn("preregistration_identity", projection)

    def test_strict_semver_accepts_valid_values(self):
        valid = (
            "1.0.0", "1.1.0", "2.0.0", "1.0.0-alpha", "1.0.0-alpha.1",
            "1.0.0+build.1", "1.0.0-alpha+build.1",
        )
        for value in valid:
            with self.subTest(value=value):
                self.assertEqual(build_protocol(self.results, version=value).protocol.preregistration_version, value)

    def test_strict_semver_rejects_invalid_values(self):
        invalid = (
            "latest", "current", "v1", "1", "1.0", "01.0.0", "1.01.0",
            "1.0.01", "1.0.0-01", "1.0.0-alpha..1", "", " ", 1,
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(PreregistrationInvalidProtocol):
                    build_protocol(self.results, version=value)

    def test_research_protocol_id_and_question_validation(self):
        for protocol_id in ("", " ", None, 1):
            with self.subTest(protocol_id=protocol_id):
                with self.assertRaises(PreregistrationInvalidProtocol):
                    build_protocol(self.results, protocol_id=protocol_id)
        for question in (
            {key: value for key, value in QUESTION.items() if key != "outcome"},
            {**QUESTION, "extra": "forbidden"},
            {**QUESTION, "outcome": " "},
            "not-a-mapping",
        ):
            with self.subTest(question=question):
                with self.assertRaises(PreregistrationInvalidProtocol):
                    build_protocol(self.results, question=question)

    def test_subject_validation_normalization_and_permutation(self):
        with self.assertRaises(PreregistrationInvalidProtocol):
            build_protocol(self.results, subjects=[])
        for values in (("A", "A"), ("\u00e9", "e\u0301"), {"A"}, ("",)):
            with self.subTest(values=values):
                with self.assertRaises(PreregistrationInvalidProtocol):
                    build_protocol(self.results, subjects=values)

        two_subject_results = synthetic_results()
        source_b = candidate(subject="SYNTHETIC_SUBJECT_B", record="V2")
        source_a = candidate()
        two_subject_results = {
            horizon: build_pit_dataset(
                (
                    horizon_request(horizon),
                    horizon_request(
                        horizon,
                        subject="SYNTHETIC_SUBJECT_B",
                        target_version_id=source_b.observation_version.observation_version_id,
                    ),
                ),
                (source_a, source_b),
            )
            for horizon in preregistration.HORIZONS
        }
        forward = build_protocol(
            two_subject_results,
            subjects=["SYNTHETIC_SUBJECT_A", "SYNTHETIC_SUBJECT_B"],
        )
        reverse = build_protocol(
            two_subject_results,
            subjects=["SYNTHETIC_SUBJECT_B", "SYNTHETIC_SUBJECT_A"],
        )
        self.assertEqual(forward.preregistration_identity, reverse.preregistration_identity)
        self.assertEqual(
            forward.protocol.research_subjects,
            ("SYNTHETIC_SUBJECT_A", "SYNTHETIC_SUBJECT_B"),
        )

    def test_horizon_mapping_permutation_is_nonsemantic(self):
        reverse = {key: self.results[key] for key in reversed(preregistration.HORIZONS)}
        self.assertEqual(
            build_protocol(self.results).preregistration_identity,
            build_protocol(reverse).preregistration_identity,
        )

    def test_exact_horizon_authority_is_required(self):
        for horizon in preregistration.HORIZONS:
            with self.subTest(missing=horizon):
                values = dict(self.results)
                del values[horizon]
                with self.assertRaises(PreregistrationHardFail):
                    build_protocol(values)
        extra = dict(self.results, P1D=self.results["P7D"])
        with self.assertRaises(PreregistrationHardFail):
            build_protocol(extra)

    def test_only_exact_pit_dataset_result_is_authority(self):
        invalid = (
            object(),
            self.results["P7D"].manifest,
            self.results["P7D"].dataset_identity,
            {"dataset_identity": self.results["P7D"].dataset_identity},
        )
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                values = dict(self.results)
                values["P7D"] = value
                with self.assertRaises(PreregistrationHardFail):
                    build_protocol(values)

    def test_label_horizon_and_uniqueness_are_enforced(self):
        wrong = replace_request_scope(
            self.results["P7D"],
            lambda entries: entries[0]["label_specification"].update(label_horizon="P14D"),
        )
        with self.assertRaises(PreregistrationHardFail):
            build_protocol(with_horizon(self.results, "P7D", wrong))

        def add_mixed(entries, field):
            duplicate = copy.deepcopy(entries[0])
            duplicate["research_subject_id"] = "SYNTHETIC_SUBJECT_B"
            duplicate["observation_version_id"] = "b" * 64
            duplicate["label_specification"][field] = f"MIXED_{field}"
            entries.append(duplicate)

        for field in ("label_definition_id", "label_reference_id"):
            with self.subTest(field=field):
                mixed = replace_request_scope(
                    self.results["P7D"], lambda entries, field=field: add_mixed(entries, field)
                )
                with self.assertRaises(PreregistrationHardFail):
                    build_protocol(with_horizon(self.results, "P7D", mixed))

    def test_label_definition_must_match_across_horizons(self):
        changed = replace_request_scope(
            self.results["P28D"],
            lambda entries: entries[0]["label_specification"].update(
                label_definition_id="OTHER_DIRECTION"
            ),
        )
        with self.assertRaises(PreregistrationHardFail):
            build_protocol(with_horizon(self.results, "P28D", changed))

    def test_reduced_request_and_row_universes_must_align(self):
        changed = replace_request_scope(
            self.results["P14D"],
            lambda entries: entries[0].update(research_cutoff_at="2026-01-03T10:29:59Z"),
        )
        with self.assertRaises(PreregistrationHardFail):
            build_protocol(with_horizon(self.results, "P14D", changed))

        forged = forge_manifest(self.results["P14D"], ordered_row_ids=("b" * 64,))
        with self.assertRaises(PreregistrationHardFail):
            build_protocol(with_horizon(self.results, "P14D", forged))

    def test_manifest_versions_profiles_and_policies_must_align(self):
        def set_feature_set(entries):
            entries[0]["feature_set_version"] = "other-feature-set@1.0.0"

        scope_changed = replace_request_scope(self.results["P28D"], set_feature_set)
        feature_set_changed = replace_manifest(
            scope_changed, feature_set_version="other-feature-set@1.0.0"
        )
        variants = (
            feature_set_changed,
            forge_manifest(
                self.results["P28D"],
                feature_computation_profile_version="OTHER_PROFILE@1.0.0",
            ),
            forge_manifest(self.results["P28D"], cutoff_policy_version="OTHER_POLICY@1.0.0"),
        )
        for value in variants:
            with self.subTest(field=value.manifest.feature_set_version):
                with self.assertRaises(PreregistrationHardFail):
                    build_protocol(with_horizon(self.results, "P28D", value))

    def test_rule_source_authorization_and_accounting_must_align(self):
        original = self.results["P28D"]
        variants = (
            replace_manifest(original, rule_bundle_bindings=()),
            replace_manifest(original, source_profile_bindings=()),
            forge_manifest(original, authorization_snapshot={"safety_flags": {}}),
            forge_manifest(original, include_count=2),
            replace_manifest(original, exclude_count=1),
            replace_manifest(original, quarantine_count=1),
            replace_manifest(original, exclusion_reason_summary={"TEST": 1}),
            replace_manifest(original, quarantine_reason_summary={"TEST": 1}),
        )
        for value in variants:
            with self.subTest(manifest=value.manifest.content_projection()):
                with self.assertRaises(PreregistrationHardFail):
                    build_protocol(with_horizon(self.results, "P28D", value))

    def test_subject_universe_must_match_each_manifest(self):
        changed = replace_request_scope(
            self.results["P7D"],
            lambda entries: entries[0].update(research_subject_id="SYNTHETIC_SUBJECT_B"),
        )
        with self.assertRaises(PreregistrationHardFail):
            build_protocol(with_horizon(self.results, "P7D", changed))

    def test_feature_definition_semantics_and_order_are_preserved(self):
        source = candidate()
        definitions = (
            feature_definition("synthetic_price", value_key="feature_value"),
            feature_definition("synthetic_optional", requirement="OPTIONAL", value_key="unused"),
        )
        results = {
            horizon: build_pit_dataset(
                (horizon_request(horizon, definitions=definitions),), (source,)
            )
            for horizon in preregistration.HORIZONS
        }
        valid = build_protocol(results)
        projected = valid.protocol.feature_binding.semantic_projection()["feature_definitions"]
        self.assertEqual(
            [item["feature_definition_id"] for item in projected],
            ["synthetic_price", "synthetic_optional"],
        )
        self.assertEqual(projected[0]["value_key"], "feature_value")
        self.assertEqual(projected[1]["requirement"], "OPTIONAL")

        for field, value in (("value_key", "changed"), ("requirement", "MANDATORY")):
            def mutate(entries, field=field, value=value):
                entries[0]["feature_definitions"][1][field] = value

            changed = replace_request_scope(results["P28D"], mutate)
            with self.subTest(field=field):
                with self.assertRaises(PreregistrationHardFail):
                    build_protocol(with_horizon(results, "P28D", changed))

        reversed_definitions = replace_request_scope(
            results["P28D"],
            lambda entries: entries[0].update(
                feature_definitions=list(reversed(entries[0]["feature_definitions"]))
            ),
        )
        with self.assertRaises(PreregistrationHardFail):
            build_protocol(with_horizon(results, "P28D", reversed_definitions))

    def test_frozen_direction_research_semantics_are_exact(self):
        protocol = build_protocol(self.results).protocol
        self.assertEqual(protocol.research_task, "DIRECTION")
        self.assertEqual(
            [(item.forecast_horizon, item.role) for item in protocol.horizon_plan],
            [("P7D", "SECONDARY"), ("P14D", "PRIMARY"), ("P28D", "SECONDARY")],
        )
        self.assertEqual(
            [(item.baseline_id, item.baseline_version) for item in protocol.baseline_plan],
            [
                ("last_observation_direction", "1.0.0"),
                ("momentum_direction", "1.0.0"),
                ("moving_average_trend", "1.0.0"),
            ],
        )
        self.assertFalse(any(
            "volatility" in item.baseline_id or "risk" in item.baseline_id
            for item in protocol.baseline_plan
        ))
        self.assertEqual(
            protocol.label_protocol.semantic_projection(),
            {
                "label_definition_id": "SYNTHETIC_DIRECTION",
                "target_computation_rule": "SIGN_OF_EXACT_HORIZON_RETURN",
                "target_availability_rule": "LABEL_AVAILABLE_STRICTLY_AFTER_CUTOFF",
                "missing_label_policy": "EXCLUDE_FROM_EVALUATION",
                "direction_semantics": {
                    "UP": "RETURN_GT_ZERO", "DOWN": "RETURN_LT_ZERO", "FLAT": "RETURN_EQ_ZERO"
                },
                "flat_evaluation_policy": "ZERO_RETURN_NON_DIRECTIONAL_EXCLUDE_PRIMARY_KEEP_ACCOUNTING",
            },
        )

    def test_metric_and_evaluation_plans_are_exact_declarations(self):
        protocol = build_protocol(self.results).protocol
        metrics = protocol.metric_plan.semantic_projection()
        self.assertEqual(metrics["primary"], [{
            "metric_id": "balanced_accuracy",
            "metric_version": "1.0.0",
            "aggregation_scope": "SUBJECT_MACRO",
        }])
        self.assertEqual(
            [item["metric_id"] for item in metrics["secondary"]], ["accuracy", "macro_f1"]
        )
        self.assertEqual(
            [item["metric_id"] for item in metrics["diagnostic"]],
            sorted((
                "actual_distribution", "confusion_matrix", "evaluation_coverage",
                "exclusion_reason_counts", "f1_down", "f1_up", "precision_down",
                "precision_up", "prediction_distribution", "recall_down", "recall_up",
            )),
        )
        evaluation = protocol.evaluation_protocol
        self.assertEqual(evaluation.primary_horizon, "P14D")
        self.assertEqual((evaluation.primary_total_min, evaluation.primary_up_min, evaluation.primary_down_min), (120, 40, 40))
        self.assertEqual((evaluation.per_subject_total_min, evaluation.per_subject_up_min, evaluation.per_subject_down_min), (30, 10, 10))
        self.assertEqual(evaluation.candidate_required_coverage, Decimal("1"))
        self.assertEqual(evaluation.practical_superiority_floor, Decimal("0.02"))
        self.assertEqual(evaluation.confidence_level, Decimal("0.95"))
        self.assertEqual(evaluation.bootstrap_replicates, 2000)
        self.assertEqual(evaluation.bootstrap_block_length_origins, 28)
        self.assertEqual(evaluation.model_training_mode, "NONE")
        assert_no_float(self, protocol.semantic_projection())

    def test_exclusion_reporting_and_evaluation_enumerations_are_exact(self):
        protocol = build_protocol(self.results).protocol
        self.assertEqual(
            protocol.evaluation_protocol.forbidden_practices,
            (
                "RANDOM_SHUFFLE", "RANDOM_K_FOLD", "FUTURE_AWARE_SCALING",
                "FULL_HISTORY_FIT", "FUTURE_INFORMED_FEATURE_SELECTION",
            ),
        )
        self.assertEqual(
            protocol.evaluation_protocol.result_states,
            (
                "EVIDENCE_SUPPORTS_INCREMENTAL_VALUE", "NO_CLEAR_INCREMENTAL_VALUE",
                "INSUFFICIENT_EVIDENCE", "INVALID_EVALUATION",
            ),
        )
        self.assertEqual(len(protocol.exclusion_policy.rules), 6)
        self.assertEqual(
            protocol.reporting_policy.null_result_policy, "MANDATORY_PRESERVE_AND_REPORT"
        )
        self.assertEqual(
            protocol.reporting_policy.failure_result_policy, "MANDATORY_PRESERVE_AND_REPORT"
        )

    def test_runtime_metadata_and_caller_mutation_are_nonsemantic(self):
        question = dict(QUESTION)
        subjects = ["SYNTHETIC_SUBJECT_A"]
        runtime = {"run": ["one"]}
        first = build_protocol(
            self.results, question=question, subjects=subjects, runtime_metadata=runtime
        )
        identity = first.preregistration_identity
        projection = first.protocol.semantic_projection()
        question["objective"] = "mutated"
        subjects.append("MUTATED")
        runtime["run"].append("mutated")
        self.assertEqual(first.preregistration_identity, identity)
        self.assertEqual(first.protocol.semantic_projection(), projection)
        second = build_protocol(self.results, runtime_metadata={"run": "two"})
        self.assertEqual(identity, second.preregistration_identity)
        self.assertNotEqual(first.runtime_metadata, second.runtime_metadata)

    def test_semantic_version_and_question_change_identity(self):
        original = build_protocol(self.results)
        changed_version = build_protocol(self.results, version="1.0.1")
        changed_question = build_protocol(
            self.results, question={**QUESTION, "objective": "Different objective."}
        )
        self.assertNotEqual(original.preregistration_identity, changed_version.preregistration_identity)
        self.assertNotEqual(original.preregistration_identity, changed_question.preregistration_identity)

    def test_core_objects_are_frozen_and_slotted(self):
        protocol = build_protocol(self.results).protocol
        with self.assertRaises(FrozenInstanceError):
            protocol.research_task = "OTHER"
        self.assertFalse(hasattr(protocol, "__dict__"))
        with self.assertRaises(TypeError):
            protocol.authorization_snapshot["new"] = True

    def test_safety_controls_and_authorization_changes_fail_closed(self):
        self.assertEqual(len(SAFETY_FLAGS), 9)
        self.assertTrue(all(value is False for value in SAFETY_FLAGS.values()))
        self.assertEqual(PIT_ENABLED_SOURCES, ())
        self.assertEqual(RESEARCH_ENABLED_SOURCES, ())
        self.assertEqual(BACKTEST_ENABLED_SOURCES, ())
        self.assertEqual(len(RD3_OPEN_BLOCKERS), 15)
        with patch.object(preregistration, "SAFETY_FLAGS", {**SAFETY_FLAGS, "PRODUCTION_WRITE": True}):
            with self.assertRaises(PreregistrationHardFail):
                build_protocol(self.results)

    def test_real_operational_scope_is_rejected(self):
        changed = replace_request_scope(
            self.results["P7D"],
            lambda entries: entries[0].update(execution_mode="REAL_OPERATIONAL"),
        )
        with self.assertRaises(PreregistrationHardFail):
            build_protocol(with_horizon(self.results, "P7D", changed))

    def test_public_builder_does_not_accept_derived_semantics(self):
        signature = inspect.signature(build_preregistration_protocol)
        self.assertEqual(
            tuple(signature.parameters),
            (
                "preregistration_version", "research_protocol_id", "research_question",
                "research_subjects", "dataset_results_by_horizon", "runtime_metadata",
            ),
        )
        self.assertTrue(all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in signature.parameters.values()
        ))

    def test_no_approval_change_backtest_or_realized_payload_api(self):
        names = set(vars(preregistration))
        forbidden_names = {
            "PreregistrationApprovalRecord", "PreregistrationChangeRecord",
            "build_backtest", "run_backtest", "execute_bootstrap",
            "realized_label", "realized_target", "label_value", "truth_value",
        }
        self.assertTrue(forbidden_names.isdisjoint(names))
        builders = {
            name for name, value in vars(preregistration).items()
            if (
                inspect.isfunction(value)
                and value.__module__ == preregistration.__name__
                and not name.startswith("_")
            )
        }
        self.assertEqual(builders, {"build_preregistration_protocol"})

    def test_production_module_has_no_forbidden_dependencies_or_impure_calls(self):
        tree = ast.parse(inspect.getsource(preregistration))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        forbidden_imports = {
            "os", "pathlib", "requests", "urllib", "http", "subprocess", "socket",
            "google", "sqlite3", "time", "datetime", "random", "secrets", "uuid",
            "tempfile", "pickle", "joblib", "sklearn", "tensorflow", "torch",
        }
        self.assertTrue(imports.isdisjoint(forbidden_imports))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("open", calls)


if __name__ == "__main__":
    unittest.main()
