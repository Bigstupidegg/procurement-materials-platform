from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import hashlib
import unittest
from unittest.mock import patch

from scripts.c4_rd_contract import (
    CALENDAR_ROLES,
    EXCLUSION_REASON_CODES,
    RD3_OPEN_BLOCKERS,
    SAFETY_FLAGS,
    AvailabilityAssessment,
    CalendarAssignment,
    CalendarReference,
    ExclusionReason,
    LineageAssessment,
    Observation,
    ObservationVersion,
    RuleBundle,
    TrustQualityAssessment,
    canonical_hash,
    parse_rfc3339,
)
from scripts.c4_rd_pit_dataset import (
    CUTOFF_POLICY_VERSION,
    DATASET_CONTRACT_VERSION,
    DATASET_MANIFEST_TYPE,
    DATASET_MANIFEST_VERSION,
    FEATURE_COMPUTATION_PROFILE_VERSION,
    PITDatasetHardFail,
    PITDatasetRequest,
    PITDatasetRow,
    PITFeatureCandidate,
    PITFeatureDefinition,
    build_pit_dataset,
)
from scripts.c4_rd_readiness_evaluator import (
    EVALUATOR_VERSION,
    SOURCE_PROFILE_VERSION,
    evaluate_readiness,
)
from scripts.c4_rd_research_export import build_research_export_decision


CUTOFF = "2026-01-03T10:30:00Z"
EVALUATION_AT = "2026-01-05T00:00:00Z"
TARGET_VERSION_ID = "a" * 64


def exclusion_reasons() -> tuple[ExclusionReason, ...]:
    return tuple(
        ExclusionReason(code, "BLOCK", "RECORD", f"Synthetic {index}", "Synthetic remediation", index)
        for index, code in enumerate(EXCLUSION_REASON_CODES)
    )


def rule_bundle() -> RuleBundle:
    return RuleBundle(
        rule_bundle_version="rd4.2-rules@1.0.0",
        contract_version="1.0.0",
        exclusion_catalog_version="1.1.0",
        exclusion_reasons=exclusion_reasons(),
        rules={
            "rd4_2": {
                "evaluator_version": EVALUATOR_VERSION,
                "rd3_open_blockers": RD3_OPEN_BLOCKERS,
                "pit_enabled_sources": (),
                "research_enabled_sources": (),
                "backtest_enabled_sources": (),
            }
        },
    )


def calendar(record: str) -> CalendarReference:
    return CalendarReference(
        f"SYNTHETIC_CAL_{record}",
        "1.0.0",
        "SYNTHETIC_CALENDAR",
        ({"date": "2026-01-02"},),
    )


def assignments(reference: CalendarReference) -> tuple[CalendarAssignment, ...]:
    values = []
    for role in CALENDAR_ROLES:
        if role == "MARKET":
            values.append(CalendarAssignment(
                "synthetic-subject",
                role,
                "RESOLVED",
                reference.calendar_reference_id,
                reference.calendar_version,
                reference.calendar_hash,
            ))
        else:
            values.append(CalendarAssignment("synthetic-subject", role, "NOT_APPLICABLE"))
    return tuple(values)


def feature_definition(
    feature_id: str = "synthetic_price",
    *,
    requirement: str = "MANDATORY",
    value_key: str = "feature_value",
) -> PITFeatureDefinition:
    return PITFeatureDefinition(feature_id, f"{feature_id}@1.0.0", value_key, requirement)


def request(
    *,
    subject: str = "SYNTHETIC_SUBJECT_A",
    target_version_id: str = TARGET_VERSION_ID,
    cutoff: str = CUTOFF,
    definitions: tuple[PITFeatureDefinition, ...] | None = None,
    execution_mode: str = "SYNTHETIC_NON_OPERATIONAL",
    label_specification=None,
    runtime_metadata=None,
    dataset_contract_version: str = DATASET_CONTRACT_VERSION,
    feature_computation_profile_version: str = FEATURE_COMPUTATION_PROFILE_VERSION,
    cutoff_policy_version: str = CUTOFF_POLICY_VERSION,
) -> PITDatasetRequest:
    return PITDatasetRequest(
        research_subject_id=subject,
        observation_version_id=target_version_id,
        research_cutoff_at=cutoff,
        feature_set_version="synthetic-feature-set@1.0.0",
        feature_definitions=(feature_definition(),) if definitions is None else definitions,
        label_specification={
            "label_definition_id": "SYNTHETIC_LABEL_SPEC",
            "label_horizon": "P1D",
            "label_reference_id": "SYNTHETIC_REFERENCE_ONLY",
        } if label_specification is None else label_specification,
        execution_mode=execution_mode,
        dataset_contract_version=dataset_contract_version,
        feature_computation_profile_version=feature_computation_profile_version,
        cutoff_policy_version=cutoff_policy_version,
        runtime_metadata={} if runtime_metadata is None else runtime_metadata,
    )


def candidate(
    *,
    subject: str = "SYNTHETIC_SUBJECT_A",
    feature_id: str = "synthetic_price",
    record: str = "V1",
    available_at: str = "2026-01-03T10:00:00Z",
    source_available_at: str | None = None,
    include_availability_assessment: bool = True,
    cutoff: str = CUTOFF,
    value=Decimal("12.50"),
    trust_state: str = "REAL_ORIGIN_VERIFIED",
    quality: str = "PASS",
    runtime_metadata=None,
) -> PITFeatureCandidate:
    reference = calendar(record)
    source_available = available_at if source_available_at is None else source_available_at
    observation = Observation(
        source_id="LME",
        source_record_identifier=f"SYNTHETIC_RECORD_{record}",
        metric_id="SYNTHETIC_PRICE",
        instrument_id="LME-COPPER",
        source_period_type="DAILY_MARKET",
        source_market_date="2026-01-02",
        source_period_start_date=None,
        source_period_end_date=None,
        calendar_assignments=assignments(reference),
        source_publication_at="2026-01-02T12:00:00Z",
        observed_at="2026-01-02T12:00:00Z",
        source_available_at=source_available,
        collected_at=available_at,
        created_at=available_at,
        semantic_data={"fixture": "SYNTHETIC_ONLY", "feature_value": value},
    )
    raw_hash = hashlib.sha256(f"SYNTHETIC_RAW_{record}".encode()).hexdigest()
    version = ObservationVersion(
        observation_id=observation.observation_id,
        source_version_or_release_key=f"SYNTHETIC_RELEASE_{record}",
        stable_version_key=f"SYNTHETIC_VERSION_{record}",
        raw_payload_hash=raw_hash,
        transformation_version="synthetic-transform@1.0.0",
        revision_available_at=available_at,
        collected_at=available_at,
        observed_at="2026-01-02T12:00:00Z",
        created_at=available_at,
        semantic_data={"fixture": "SYNTHETIC_ONLY", "feature_value": value},
    )
    available = (
        AvailabilityAssessment(
            subject_id=version.observation_version_id,
            assessment_role="SOURCE",
            evaluation_at=EVALUATION_AT,
            rule_bundle_version="rd4.2-rules@1.0.0",
            result={"feature_available_at_max": available_at},
            evidence={"fixture": "SYNTHETIC_ONLY"},
            availability_basis="VERIFIED_ARCHIVE_TIMESTAMP",
        )
        if include_availability_assessment
        else None
    )
    lineage = LineageAssessment(
        subject_id=version.observation_version_id,
        assessment_role="HISTORICAL",
        evaluation_at=EVALUATION_AT,
        rule_bundle_version="rd4.2-rules@1.0.0",
        result={
            "lineage_complete": True,
            "historical_vintage_proven": True,
            "lineage_conflict": False,
        },
        evidence={"fixture": "SYNTHETIC_ONLY"},
    )
    trust = TrustQualityAssessment(
        subject_id=version.observation_version_id,
        assessment_role="SOURCE",
        evaluation_at=EVALUATION_AT,
        rule_bundle_version="rd4.2-rules@1.0.0",
        result={"synthetic_rule_check": True},
        evidence={"fixture": "SYNTHETIC_ONLY"},
        trust_state=trust_state,
        quality_status=quality,
        reason_codes=(),
    )
    evaluated = evaluate_readiness(
        observation=observation,
        observation_version=version,
        rule_bundle=rule_bundle(),
        evaluation_role="RESEARCH",
        cutoff_at=cutoff,
        evaluation_as_of_at=EVALUATION_AT,
        source_profile_id="LME",
        source_profile_version=SOURCE_PROFILE_VERSION,
        availability_assessment=available,
        calendar_references=(reference,),
        lineage_assessment=lineage,
        trust_quality_assessment=trust,
        label_available_at=None,
    )
    decision = build_research_export_decision(
        observation=observation,
        observation_version=version,
        evaluation_result=evaluated,
        research_cutoff_at=cutoff,
        research_payload={"fixture": "SYNTHETIC_ONLY", "display_only": True},
        availability_assessment=available,
        lineage_assessment=lineage,
        trust_quality_assessment=trust,
        calendar_references=(reference,),
    )
    return PITFeatureCandidate(
        research_subject_id=subject,
        feature_definition_id=feature_id,
        observation=observation,
        observation_version=version,
        evaluation_result=evaluated,
        research_export_decision=decision,
        runtime_metadata={} if runtime_metadata is None else runtime_metadata,
    )


def build_one(**candidate_changes):
    return build_pit_dataset((request(),), (candidate(**candidate_changes),))


class SyntheticPositiveAndBoundaryTests(unittest.TestCase):
    def test_synthetic_positive_dataset(self):
        item = candidate()
        self.assertTrue(item.research_export_decision.semantic_export_passed)
        self.assertFalse(item.research_export_decision.operationally_authorized)
        result = build_pit_dataset((request(),), (item,))
        self.assertEqual(result.manifest.include_count, 1)
        self.assertEqual(result.rows[0].operational_status, "SYNTHETIC_NON_OPERATIONAL")

    def test_same_semantic_input_same_manifest_hash(self):
        first = build_one()
        second = build_one()
        self.assertEqual(first.dataset_identity, second.dataset_identity)
        self.assertEqual(first.as_dict(), second.as_dict())

    def test_input_permutation_same_dataset(self):
        first_request = request(subject="SYNTHETIC_SUBJECT_A", target_version_id="a" * 64)
        second_request = request(subject="SYNTHETIC_SUBJECT_B", target_version_id="b" * 64)
        first_candidate = candidate(subject="SYNTHETIC_SUBJECT_A", record="A")
        second_candidate = candidate(subject="SYNTHETIC_SUBJECT_B", record="B")
        forward = build_pit_dataset((first_request, second_request), (first_candidate, second_candidate))
        reverse = build_pit_dataset((second_request, first_request), (second_candidate, first_candidate))
        self.assertEqual(forward.as_dict(), reverse.as_dict())

    def test_feature_mapping_permutation_same_feature_hash(self):
        first_value = {"b": Decimal("2"), "a": Decimal("1")}
        second_value = {"a": Decimal("1"), "b": Decimal("2")}
        first = build_one(value=first_value)
        second = build_one(value=second_value)
        self.assertEqual(first.rows[0].features[0].feature_content_hash, second.rows[0].features[0].feature_content_hash)

    def test_synthetic_path_does_not_change_frozen_controls(self):
        result = build_one()
        self.assertTrue(all(value is False for value in SAFETY_FLAGS.values()))
        self.assertTrue(all(value is False for value in result.rows[0].authorization_snapshot["safety_flags"].values()))
        self.assertEqual(result.rows[0].authorization_snapshot["rd3_open_blockers"], RD3_OPEN_BLOCKERS)

    def test_operational_request_cannot_include(self):
        result = build_pit_dataset(
            (request(execution_mode="REAL_OPERATIONAL"),),
            (candidate(),),
        )
        self.assertEqual(result.manifest.include_count, 0)
        self.assertEqual(result.manifest.exclude_count, 1)
        self.assertEqual(result.diagnostics[0]["reason"], "OPERATIONAL_RESEARCH_NOT_AUTHORIZED")


class LatestKnownAndMissingFeatureTests(unittest.TestCase):
    def test_latest_known_as_of_cutoff(self):
        values = (
            candidate(record="V1", available_at="2026-01-03T09:00:00Z", value=Decimal("1")),
            candidate(record="V2", available_at="2026-01-03T10:00:00Z", value=Decimal("2")),
            candidate(record="V3", available_at="2026-01-03T12:00:00Z", value=Decimal("3")),
        )
        result = build_pit_dataset((request(),), values)
        self.assertEqual(result.rows[0].features[0].value, Decimal("2"))
        self.assertEqual(result.rows[0].features[0].source_observation_version_id, values[1].observation_version.observation_version_id)

    def test_future_version_invisible_at_earlier_cutoff(self):
        future = candidate(record="FUTURE", available_at="2026-01-03T12:00:00Z")
        result = build_pit_dataset((request(),), (future,))
        self.assertEqual(result.manifest.include_count, 0)
        self.assertEqual(result.diagnostics[0]["reason"], "MANDATORY_FEATURE_UNAVAILABLE")

    def test_mandatory_missing_excludes_row(self):
        result = build_pit_dataset((request(),), ())
        self.assertEqual((result.manifest.include_count, result.manifest.exclude_count), (0, 1))

    def test_optional_missing_is_explicit(self):
        definitions = (
            feature_definition("synthetic_price", requirement="MANDATORY"),
            feature_definition("synthetic_optional", requirement="OPTIONAL", value_key="optional_value"),
        )
        result = build_pit_dataset((request(definitions=definitions),), (candidate(),))
        missing = result.rows[0].features[1]
        self.assertEqual(missing.value_state, "EXPLICIT_MISSING")
        self.assertIsNone(missing.value)
        self.assertIsNone(missing.source_observation_id)

    def test_no_imputation_for_optional_missing(self):
        definitions = (
            feature_definition("synthetic_price"),
            feature_definition("missing", requirement="OPTIONAL", value_key="missing"),
        )
        result = build_pit_dataset((request(definitions=definitions),), (candidate(),))
        missing = result.rows[0].features[1]
        self.assertNotIn(missing.value, (0, Decimal("0")))
        self.assertEqual(missing.value_state, "EXPLICIT_MISSING")

    def test_ambiguous_authoritative_candidate_hard_fails(self):
        first = candidate(record="AMBIG_A", available_at="2026-01-03T10:00:00Z", value=Decimal("1"))
        second = candidate(record="AMBIG_B", available_at="2026-01-03T10:00:00Z", value=Decimal("2"))
        with self.assertRaisesRegex(PITDatasetHardFail, "AMBIGUOUS_AUTHORITATIVE_CANDIDATE"):
            build_pit_dataset((request(),), (first, second))


class AuthorityAndTrustTests(unittest.TestCase):
    def test_shadow_cannot_auto_include(self):
        result = build_pit_dataset((request(),), (candidate(trust_state="SHADOW_UNRESOLVED"),))
        self.assertEqual(result.manifest.include_count, 0)
        self.assertEqual(result.manifest.quarantine_count, 1)

    def test_legacy_cannot_include(self):
        result = build_pit_dataset((request(),), (candidate(trust_state="LEGACY_UNVERIFIED"),))
        self.assertEqual(result.manifest.include_count, 0)
        self.assertEqual(result.manifest.quarantine_count, 1)

    def test_rd4_authority_mismatch_hard_fails(self):
        item = candidate()
        forged = replace(item.evaluation_result, observation_content_hash="f" * 64)
        item = replace(item, evaluation_result=forged)
        with self.assertRaisesRegex(PITDatasetHardFail, "AUTHORITY_BINDING_MISMATCH"):
            build_pit_dataset((request(),), (item,))

    def test_rd5_authority_mismatch_hard_fails(self):
        item = candidate()
        forged = replace(item.research_export_decision, source_id="SMM")
        item = replace(item, research_export_decision=forged)
        with self.assertRaisesRegex(PITDatasetHardFail, "AUTHORITY_BINDING_MISMATCH"):
            build_pit_dataset((request(),), (item,))

    def test_rd5_version_key_mismatch_hard_fails(self):
        item = candidate()
        forged = replace(item.research_export_decision, stable_version_key="FORGED_VERSION")
        item = replace(item, research_export_decision=forged)
        with self.assertRaisesRegex(PITDatasetHardFail, "AUTHORITY_BINDING_MISMATCH"):
            build_pit_dataset((request(),), (item,))

    def test_rd5_source_period_mismatch_hard_fails(self):
        item = candidate()
        forged = replace(
            item.research_export_decision,
            source_period={**item.research_export_decision.source_period, "source_market_date": "2026-01-01"},
        )
        item = replace(item, research_export_decision=forged)
        with self.assertRaisesRegex(PITDatasetHardFail, "AUTHORITY_BINDING_MISMATCH"):
            build_pit_dataset((request(),), (item,))

    def test_bound_rule_and_source_profiles_preserved(self):
        row = build_one().rows[0]
        self.assertEqual(row.rule_bundle_bindings[0]["rule_bundle_version"], "rd4.2-rules@1.0.0")
        self.assertEqual(row.source_profile_bindings[0], {"source_profile_id": "LME", "source_profile_version": SOURCE_PROFILE_VERSION})


class NoLookAheadAndLabelFirewallTests(unittest.TestCase):
    def test_every_included_row_is_machine_checkable(self):
        result = build_one()
        for row in result.rows:
            self.assertLessEqual(parse_rfc3339(row.feature_available_at_max), parse_rfc3339(row.research_cutoff_at))

    def test_future_feature_in_included_row_hard_fails(self):
        row = build_one().rows[0]
        with self.assertRaisesRegex(PITDatasetHardFail, "FEATURE_AVAILABLE_AFTER_CUTOFF"):
            replace(row, feature_available_at_max="2026-01-03T10:30:01Z")

    def test_realized_label_is_rejected_at_request_boundary(self):
        with self.assertRaisesRegex(PITDatasetHardFail, "must contain exactly"):
            request(label_specification={
                "label_definition_id": "SYNTHETIC",
                "realized_label": Decimal("1"),
            })

    def test_feature_construction_does_not_consume_label_specification(self):
        first = request(label_specification={
            "label_definition_id": "A", "label_horizon": "P1D", "label_reference_id": "REF-A",
        })
        second = request(label_specification={
            "label_definition_id": "B", "label_horizon": "P2D", "label_reference_id": "REF-B",
        })
        item = candidate()
        first_result = build_pit_dataset((first,), (item,))
        second_result = build_pit_dataset((second,), (item,))
        self.assertEqual(
            first_result.rows[0].features[0].feature_content_hash,
            second_result.rows[0].features[0].feature_content_hash,
        )
        self.assertNotEqual(first_result.rows[0].row_content_hash, second_result.rows[0].row_content_hash)


class OrderingDuplicateAndRuntimeTests(unittest.TestCase):
    def test_same_row_same_content_deduplicates(self):
        item_request = request()
        result = build_pit_dataset((item_request, item_request), (candidate(),))
        self.assertEqual(result.manifest.include_count, 1)
        self.assertTrue(any(item["classification"] == "DEDUPLICATED" for item in result.diagnostics))

    def test_same_row_id_different_content_hard_fails(self):
        first = request(label_specification={
            "label_definition_id": "A", "label_horizon": "P1D", "label_reference_id": "REF-A",
        })
        second = request(label_specification={
            "label_definition_id": "B", "label_horizon": "P1D", "label_reference_id": "REF-B",
        })
        with self.assertRaisesRegex(PITDatasetHardFail, "ROW_ID_CONTENT_CONFLICT"):
            build_pit_dataset((first, second), (candidate(),))

    def test_feature_definition_order_is_preserved(self):
        definitions = (
            feature_definition("feature_b", value_key="feature_value"),
            feature_definition("feature_a", value_key="feature_value"),
        )
        values = (
            candidate(feature_id="feature_a", record="A"),
            candidate(feature_id="feature_b", record="B"),
        )
        result = build_pit_dataset((request(definitions=definitions),), values)
        self.assertEqual(tuple(item.feature_definition_id for item in result.rows[0].features), ("feature_b", "feature_a"))

    def test_row_ordering_is_cutoff_subject_row_id(self):
        first_request = request(subject="B", target_version_id="b" * 64, cutoff="2026-01-03T10:30:00Z")
        second_request = request(subject="A", target_version_id="a" * 64, cutoff="2026-01-03T10:30:00Z")
        values = (candidate(subject="B", record="B"), candidate(subject="A", record="A"))
        result = build_pit_dataset((first_request, second_request), values)
        self.assertEqual(tuple(item.research_subject_id for item in result.rows), ("A", "B"))

    def test_runtime_metadata_does_not_change_hashes(self):
        first_request = request(runtime_metadata={"hostname": "synthetic-a", "generated_at": "2026-01-01T00:00:00Z"})
        second_request = request(runtime_metadata={"hostname": "synthetic-b", "generated_at": "2026-02-01T00:00:00Z"})
        first_candidate = candidate(runtime_metadata={"pid": 1})
        second_candidate = candidate(runtime_metadata={"pid": 2})
        first = build_pit_dataset((first_request,), (first_candidate,))
        second = build_pit_dataset((second_request,), (second_candidate,))
        self.assertEqual(first.dataset_identity, second.dataset_identity)
        self.assertEqual(first.rows[0].row_content_hash, second.rows[0].row_content_hash)

    def test_caller_mapping_mutation_cannot_change_result(self):
        metadata = {"nested": [1, 2]}
        item_request = request(runtime_metadata=metadata)
        result = build_pit_dataset((item_request,), (candidate(),))
        before = result.dataset_identity
        metadata["nested"].append(3)
        self.assertEqual(result.dataset_identity, before)


class ContractProfileAndHashDomainTests(unittest.TestCase):
    def test_unknown_dataset_contract_fails_closed(self):
        with self.assertRaisesRegex(PITDatasetHardFail, "unsupported dataset contract"):
            request(dataset_contract_version="UNKNOWN")

    def test_unknown_computation_profile_fails_closed(self):
        with self.assertRaisesRegex(PITDatasetHardFail, "unsupported feature computation"):
            request(feature_computation_profile_version="UNKNOWN")

    def test_unknown_cutoff_policy_fails_closed(self):
        with self.assertRaisesRegex(PITDatasetHardFail, "unsupported cutoff policy"):
            request(cutoff_policy_version="UNKNOWN")

    def _domains(self):
        with patch("scripts.c4_rd_pit_dataset.canonical_hash", wraps=canonical_hash) as mocked:
            result = build_one()
            domains = tuple(call.args[0] for call in mocked.call_args_list)
        return result, domains

    def test_feature_content_domain_used(self):
        _result, domains = self._domains()
        self.assertIn("PIT_FEATURE_CONTENT", domains)

    def test_row_identity_domain_used(self):
        _result, domains = self._domains()
        self.assertIn("PIT_DATASET_ROW_ID", domains)

    def test_row_content_domain_used(self):
        _result, domains = self._domains()
        self.assertIn("PIT_DATASET_ROW_CONTENT", domains)

    def test_typed_manifest_uses_manifest_content(self):
        result, domains = self._domains()
        self.assertIn("MANIFEST_CONTENT", domains)
        self.assertEqual(result.manifest.manifest_type, DATASET_MANIFEST_TYPE)
        self.assertEqual(result.manifest.manifest_version, DATASET_MANIFEST_VERSION)

    def test_dataset_identity_equals_manifest_hash(self):
        result = build_one()
        self.assertEqual(result.dataset_identity, result.manifest.manifest_hash)

    def test_row_identity_stable_while_content_can_change(self):
        first = build_one(value=Decimal("1")).rows[0]
        second = build_one(value=Decimal("2")).rows[0]
        self.assertEqual(first.row_id, second.row_id)
        self.assertNotEqual(first.row_content_hash, second.row_content_hash)

    def test_manifest_contains_no_separate_pit_dataset_id(self):
        result = build_one()
        self.assertNotIn("pit_dataset_id", result.manifest.content_projection())
        self.assertEqual(result.dataset_identity, result.manifest.manifest_hash)


class R1StrictRemediationTests(unittest.TestCase):
    def test_r1_unknown_mandatory_candidate_quarantines(self):
        result = build_pit_dataset(
            (request(),),
            (candidate(include_availability_assessment=False),),
        )
        self.assertEqual((result.manifest.include_count, result.manifest.quarantine_count), (0, 1))
        self.assertEqual(result.diagnostics[0]["reason"], "FEATURE_AVAILABILITY_UNRESOLVED")

    def test_r1_unknown_optional_candidate_quarantines_not_missing(self):
        definitions = (
            feature_definition("synthetic_price"),
            feature_definition("synthetic_optional", requirement="OPTIONAL"),
        )
        values = (
            candidate(),
            candidate(feature_id="synthetic_optional", record="UNKNOWN", include_availability_assessment=False),
        )
        result = build_pit_dataset((request(definitions=definitions),), values)
        self.assertEqual((result.manifest.include_count, result.manifest.quarantine_count), (0, 1))
        self.assertEqual(result.diagnostics[0]["reason"], "FEATURE_AVAILABILITY_UNRESOLVED")

    def test_r1_known_plus_unknown_candidate_quarantines_without_selection(self):
        values = (
            candidate(record="KNOWN", value=Decimal("1")),
            candidate(record="UNKNOWN", value=Decimal("2"), include_availability_assessment=False),
        )
        result = build_pit_dataset((request(),), values)
        self.assertEqual(result.manifest.include_count, 0)
        self.assertEqual(result.diagnostics[0]["reason"], "FEATURE_AVAILABILITY_UNRESOLVED")

    def test_r1_no_candidate_optional_is_explicit_missing(self):
        definitions = (
            feature_definition("synthetic_price"),
            feature_definition("synthetic_optional", requirement="OPTIONAL"),
        )
        result = build_pit_dataset((request(definitions=definitions),), (candidate(),))
        self.assertEqual(result.rows[0].features[1].value_state, "EXPLICIT_MISSING")

    def test_r1_no_candidate_mandatory_excludes(self):
        result = build_pit_dataset((request(),), ())
        self.assertEqual(result.manifest.exclude_count, 1)
        self.assertEqual(result.diagnostics[0]["reason"], "MANDATORY_FEATURE_UNAVAILABLE")

    def test_r1_source_and_feature_availability_are_independent(self):
        result = build_pit_dataset(
            (request(),),
            (candidate(source_available_at="2026-01-03T09:30:00Z"),),
        )
        feature = result.rows[0].features[0]
        self.assertEqual(feature.source_available_at, "2026-01-03T10:00:00Z")

    def test_r1_decision_source_availability_mismatch_hard_fails(self):
        item = candidate()
        temporal = dict(item.research_export_decision.temporal_context)
        temporal["source_available_at"] = "2026-01-03T09:00:00Z"
        forged = replace(item.research_export_decision, temporal_context=temporal)
        with self.assertRaisesRegex(PITDatasetHardFail, "RD-5 source availability"):
            build_pit_dataset((request(),), (replace(item, research_export_decision=forged),))

    def test_r1_exact_label_specification_passes(self):
        item = request(label_specification={
            "label_definition_id": "LABEL", "label_horizon": "P1D", "label_reference_id": "REF",
        })
        self.assertEqual(set(item.label_specification), {
            "label_definition_id", "label_horizon", "label_reference_id",
        })

    def test_r1_label_specification_extra_key_fails(self):
        with self.assertRaisesRegex(PITDatasetHardFail, "must contain exactly"):
            request(label_specification={
                "label_definition_id": "LABEL", "label_horizon": "P1D",
                "label_reference_id": "REF", "extra": "DENIED",
            })

    def test_r1_actual_future_price_label_key_fails(self):
        with self.assertRaisesRegex(PITDatasetHardFail, "must contain exactly"):
            request(label_specification={
                "label_definition_id": "LABEL", "label_horizon": "P1D",
                "label_reference_id": "REF", "actual_future_price": Decimal("99"),
            })

    def test_r1_nested_extra_label_payload_fails(self):
        with self.assertRaisesRegex(PITDatasetHardFail, "non-empty string"):
            request(label_specification={
                "label_definition_id": {"name": "LABEL", "future": "DENIED"},
                "label_horizon": "P1D", "label_reference_id": "REF",
            })

    def test_r1_null_numeric_and_empty_label_values_fail(self):
        for bad_value in (None, 7, ""):
            with self.subTest(bad_value=bad_value):
                with self.assertRaisesRegex(PITDatasetHardFail, "non-empty string"):
                    request(label_specification={
                        "label_definition_id": bad_value,
                        "label_horizon": "P1D",
                        "label_reference_id": "REF",
                    })

    def test_r1_all_excluded_subjects_have_distinct_dataset_identities(self):
        first = build_pit_dataset((request(subject="SUBJECT-A"),), ())
        second = build_pit_dataset((request(subject="SUBJECT-B"),), ())
        self.assertNotEqual(first.dataset_identity, second.dataset_identity)

    def test_r1_duplicate_semantic_request_preserves_scope_and_identity(self):
        item_request = request()
        single = build_pit_dataset((item_request,), (candidate(),))
        duplicate = build_pit_dataset((item_request, item_request), (candidate(),))
        self.assertEqual(single.manifest.request_scope, duplicate.manifest.request_scope)
        self.assertEqual(single.dataset_identity, duplicate.dataset_identity)

    def test_r1_request_permutation_preserves_scope_and_hash(self):
        first_request = request(subject="SUBJECT-A", target_version_id="a" * 64)
        second_request = request(subject="SUBJECT-B", target_version_id="b" * 64)
        values = (candidate(subject="SUBJECT-A", record="A"), candidate(subject="SUBJECT-B", record="B"))
        forward = build_pit_dataset((first_request, second_request), values)
        reverse = build_pit_dataset((second_request, first_request), tuple(reversed(values)))
        self.assertEqual(forward.manifest.request_scope, reverse.manifest.request_scope)
        self.assertEqual(forward.dataset_identity, reverse.dataset_identity)

    def test_r1_runtime_metadata_preserves_scope_and_identity(self):
        first = build_pit_dataset((request(runtime_metadata={"run": "A"}),), (candidate(),))
        second = build_pit_dataset((request(runtime_metadata={"run": "B"}),), (candidate(),))
        self.assertEqual(first.manifest.request_scope, second.manifest.request_scope)
        self.assertEqual(first.dataset_identity, second.dataset_identity)

    def test_r1_row_identity_conflict_still_hard_fails(self):
        first = request(label_specification={
            "label_definition_id": "A", "label_horizon": "P1D", "label_reference_id": "REF-A",
        })
        second = request(label_specification={
            "label_definition_id": "B", "label_horizon": "P1D", "label_reference_id": "REF-B",
        })
        with self.assertRaisesRegex(PITDatasetHardFail, "ROW_ID_CONTENT_CONFLICT"):
            build_pit_dataset((first, second), (candidate(),))

    def test_r1_request_scope_is_in_manifest_projection(self):
        result = build_one()
        projection = result.manifest.content_projection()
        self.assertIn("request_scope", projection)
        self.assertEqual(projection["request_scope"], [request().semantic_projection()])

    def test_r1_uses_no_new_hash_domain(self):
        with patch("scripts.c4_rd_pit_dataset.canonical_hash", wraps=canonical_hash) as mocked:
            build_one()
        domains = {call.args[0] for call in mocked.call_args_list}
        self.assertEqual(domains, {
            "MANIFEST_CONTENT", "PIT_FEATURE_CONTENT", "PIT_DATASET_ROW_ID", "PIT_DATASET_ROW_CONTENT",
        })

    def test_r1_dataset_identity_equals_manifest_hash(self):
        result = build_pit_dataset((request(),), (candidate(),))
        self.assertEqual(result.dataset_identity, result.manifest.manifest_hash)


if __name__ == "__main__":
    unittest.main()
