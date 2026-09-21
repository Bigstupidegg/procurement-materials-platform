from __future__ import annotations

from dataclasses import fields, replace
from decimal import Decimal
import unittest

from scripts.c4_rd_contract import (
    CALENDAR_ROLES,
    CANONICAL_JSON_PROFILE,
    EXCLUSION_REASON_CODES,
    HASH_PROFILE,
    RD3_OPEN_BLOCKERS,
    RESEARCH_ENABLED_SOURCES,
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
)
from scripts.c4_rd_readiness_evaluator import (
    EVALUATOR_VERSION,
    SOURCE_PROFILE_VERSION,
    EvidenceReference,
    evaluate_readiness,
)
from scripts.c4_rd_research_export import (
    EXPORT_CONTRACT_VERSION,
    ResearchExportInputError,
    build_research_export_decision,
    build_research_export_manifest,
)


H2 = "2" * 64
H3 = "3" * 64


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


def calendar(reference_id: str = "SYNTHETIC_CAL") -> CalendarReference:
    return CalendarReference(reference_id, "1.0.0", "SYNTHETIC_CALENDAR", ({"date": "2026-01-02"},))


def assignments(
    required_role: str | None,
    reference: CalendarReference | None,
    mode: str = "RESOLVED",
) -> tuple[CalendarAssignment, ...]:
    values = []
    for role in CALENDAR_ROLES:
        if role != required_role:
            values.append(CalendarAssignment("synthetic-subject", role, "NOT_APPLICABLE"))
        elif mode == "RESOLVED" and reference is not None:
            values.append(CalendarAssignment(
                "synthetic-subject",
                role,
                "RESOLVED",
                reference.calendar_reference_id,
                reference.calendar_version,
                reference.calendar_hash,
            ))
        elif mode == "CONFLICTING":
            values.append(CalendarAssignment("synthetic-subject", role, "CONFLICTING"))
        else:
            values.append(CalendarAssignment("synthetic-subject", role, "UNVERIFIED"))
    return tuple(values)


def source_spec(profile: str):
    return {
        "LME": ("LME", "LME-COPPER", "MARKET", "DAILY_MARKET"),
        "SMM": ("SMM", "SMM-COPPER", "PUBLICATION", "SNAPSHOT"),
        "YAHOO": ("YAHOO", "HG=F", "MARKET", "DAILY_MARKET"),
        "BZ": ("YAHOO", "BZ=F", None, "DAILY_MARKET"),
        "WORLD_BANK": ("WORLD_BANK", "WB-SERIES", "PUBLICATION", "MONTH"),
        "UNKNOWN": ("UNKNOWN", "UNKNOWN-INSTRUMENT", None, "DAILY_MARKET"),
    }[profile]


def observation(
    *,
    profile: str = "LME",
    record: str = "SYNTHETIC_RECORD_001",
    calendar_mode: str = "RESOLVED",
    include_source_date: bool = True,
) -> tuple[Observation, tuple[CalendarReference, ...]]:
    source_id, instrument_id, required_role, period_type = source_spec(profile)
    ref = calendar(f"SYNTHETIC_CAL_{record}")
    references = (ref,) if required_role is not None and calendar_mode == "RESOLVED" else ()
    if period_type == "MONTH":
        market_date = None
        period_start = "2026-01-01" if include_source_date else None
        period_end = "2026-01-31" if include_source_date else None
    else:
        market_date = "2026-01-02" if include_source_date else None
        period_start = None
        period_end = None
    return Observation(
        source_id=source_id,
        source_record_identifier=record,
        metric_id="SYNTHETIC_PRICE",
        instrument_id=instrument_id,
        source_period_type=period_type,
        source_market_date=market_date,
        source_period_start_date=period_start,
        source_period_end_date=period_end,
        calendar_assignments=assignments(required_role, ref, calendar_mode),
        source_publication_at="2026-01-02T12:00:00Z",
        collected_at="2026-01-03T00:00:00Z",
        created_at="2026-01-03T00:00:01Z",
        semantic_data={"fixture": "SYNTHETIC_ONLY", "value": Decimal("12.50")},
    ), references


def version(item: Observation, record: str = "001") -> ObservationVersion:
    return ObservationVersion(
        observation_id=item.observation_id,
        source_version_or_release_key=f"SYNTHETIC_RELEASE_{record}",
        stable_version_key=f"SYNTHETIC_VERSION_{record}",
        raw_payload_hash=H2,
        transformation_version="synthetic-transform@1.0.0",
        revision_available_at="2026-01-03T00:00:00Z",
        created_at="2026-01-03T00:00:01Z",
        semantic_data={"fixture": "SYNTHETIC_ONLY", "value": 12},
    )


def availability(
    item: ObservationVersion,
    at: str,
    basis: str = "VERIFIED_ARCHIVE_TIMESTAMP",
) -> AvailabilityAssessment:
    return AvailabilityAssessment(
        subject_id=item.observation_version_id,
        assessment_role="SOURCE",
        evaluation_at="2026-01-05T00:00:00Z",
        rule_bundle_version="rd4.2-rules@1.0.0",
        result={"feature_available_at_max": at},
        evidence={"fixture": "SYNTHETIC_ONLY"},
        availability_basis=basis,
    )


def lineage(
    item: ObservationVersion,
    *,
    complete: bool = True,
    vintage: bool = True,
    conflict: bool = False,
) -> LineageAssessment:
    return LineageAssessment(
        subject_id=item.observation_version_id,
        assessment_role="HISTORICAL",
        evaluation_at="2026-01-05T00:00:00Z",
        rule_bundle_version="rd4.2-rules@1.0.0",
        result={
            "lineage_complete": complete,
            "historical_vintage_proven": vintage,
            "lineage_conflict": conflict,
        },
        evidence={"fixture": "SYNTHETIC_ONLY"},
    )


def trust(
    item: ObservationVersion,
    *,
    state: str = "REAL_ORIGIN_VERIFIED",
    quality: str = "PASS",
) -> TrustQualityAssessment:
    return TrustQualityAssessment(
        subject_id=item.observation_version_id,
        assessment_role="SOURCE",
        evaluation_at="2026-01-05T00:00:00Z",
        rule_bundle_version="rd4.2-rules@1.0.0",
        result={"synthetic_rule_check": True},
        evidence={"fixture": "SYNTHETIC_ONLY"},
        trust_state=state,
        quality_status=quality,
        reason_codes=(),
    )


def case(
    *,
    profile: str = "LME",
    record: str = "001",
    available_at: str = "2026-01-03T00:00:00Z",
    cutoff: str = "2026-01-03T00:00:00Z",
    evaluation_as_of: str = "2026-01-05T00:00:00Z",
    availability_basis: str = "VERIFIED_ARCHIVE_TIMESTAMP",
    include_availability: bool = True,
    include_lineage: bool = True,
    lineage_complete: bool = True,
    vintage_proven: bool = True,
    lineage_conflict: bool = False,
    include_trust: bool = True,
    trust_state: str = "REAL_ORIGIN_VERIFIED",
    quality: str = "PASS",
    calendar_mode: str = "RESOLVED",
    include_source_date: bool = True,
    payload=None,
):
    item, refs = observation(
        profile=profile,
        record=f"SYNTHETIC_RECORD_{record}",
        calendar_mode=calendar_mode,
        include_source_date=include_source_date,
    )
    item_version = version(item, record)
    available = availability(item_version, available_at, availability_basis) if include_availability else None
    lineage_value = lineage(
        item_version,
        complete=lineage_complete,
        vintage=vintage_proven,
        conflict=lineage_conflict,
    ) if include_lineage else None
    trust_value = trust(item_version, state=trust_state, quality=quality) if include_trust else None
    bundle = rule_bundle()
    result = evaluate_readiness(
        observation=item,
        observation_version=item_version,
        rule_bundle=bundle,
        evaluation_role="RESEARCH",
        cutoff_at=cutoff,
        evaluation_as_of_at=evaluation_as_of,
        source_profile_id=profile,
        source_profile_version=SOURCE_PROFILE_VERSION,
        availability_assessment=available,
        calendar_references=refs,
        lineage_assessment=lineage_value,
        trust_quality_assessment=trust_value,
        label_available_at=None,
    )
    return {
        "observation": item,
        "observation_version": item_version,
        "evaluation_result": result,
        "research_cutoff_at": cutoff,
        "research_payload": {"synthetic_feature": Decimal("7.25")} if payload is None else payload,
        "availability_assessment": available,
        "lineage_assessment": lineage_value,
        "trust_quality_assessment": trust_value,
        "calendar_references": refs,
    }


def decision_with_authority(**changes):
    values = case()
    values.update(changes)
    return build_research_export_decision(**values), values["evaluation_result"]


def decision_with_authority_from_case(**case_changes):
    values = case(**case_changes)
    return build_research_export_decision(**values), values["evaluation_result"]


def decision(**changes):
    return decision_with_authority(**changes)[0]


def manifest_from(decisions, authorities):
    return build_research_export_manifest(
        decisions,
        authorities,
        research_cutoff_at="2026-01-03T00:00:00Z",
        evaluation_context={"purpose": "SYNTHETIC_RD5_RESEARCH_EXPORT_TEST"},
    )


def manifest(*decision_authority_pairs):
    return manifest_from(
        tuple(item[0] for item in decision_authority_pairs),
        tuple(item[1] for item in decision_authority_pairs),
    )


class InputAndContractTests(unittest.TestCase):
    def test_valid_synthetic_input_enters_exporter(self):
        result = decision()
        self.assertEqual(result.export_contract_version, EXPORT_CONTRACT_VERSION)
        self.assertEqual(result.disposition, "EXCLUDE")

    def test_malformed_observation_hard_fails(self):
        with self.assertRaises(ResearchExportInputError):
            decision(observation=object())

    def test_malformed_evaluation_hard_fails(self):
        with self.assertRaises(ResearchExportInputError):
            decision(evaluation_result=object())

    def test_mismatched_observation_version_hard_fails(self):
        values = case()
        other, _refs = observation(record="SYNTHETIC_RECORD_OTHER")
        values["observation_version"] = version(other, "OTHER")
        with self.assertRaises(ResearchExportInputError):
            build_research_export_decision(**values)

    def test_mismatched_evaluation_content_hash_hard_fails(self):
        values = case()
        values["evaluation_result"] = replace(values["evaluation_result"], observation_content_hash=H3)
        with self.assertRaises(ResearchExportInputError):
            build_research_export_decision(**values)

    def test_non_research_evaluation_hard_fails(self):
        values = case()
        values["evaluation_result"] = replace(values["evaluation_result"], evaluation_role="PIT")
        with self.assertRaises(ResearchExportInputError):
            build_research_export_decision(**values)

    def test_unsupported_export_version_hard_fails(self):
        with self.assertRaises(ResearchExportInputError):
            build_research_export_decision(**case(), export_contract_version="2.0.0")

    def test_invalid_research_payload_hard_fails(self):
        with self.assertRaises(ResearchExportInputError):
            decision(research_payload=["not", "a", "mapping"])

    def test_missing_manifest_context_hard_fails(self):
        item, authority = decision_with_authority()
        with self.assertRaises(ResearchExportInputError):
            build_research_export_manifest(
                (item,),
                (authority,),
                research_cutoff_at="2026-01-03T00:00:00Z",
                evaluation_context={},
            )

    def test_empty_manifest_hard_fails(self):
        with self.assertRaises(ResearchExportInputError):
            build_research_export_manifest(
                (),
                (),
                research_cutoff_at="2026-01-03T00:00:00Z",
                evaluation_context={"purpose": "SYNTHETIC"},
            )


class PositiveSemanticAndOperationalTests(unittest.TestCase):
    def test_source_neutral_synthetic_semantic_path_passes(self):
        result = decision()
        self.assertTrue(result.semantic_export_passed)
        self.assertFalse(result.operationally_authorized)
        self.assertEqual(result.disposition, "EXCLUDE")

    def test_semantic_path_is_not_an_always_exclude_implementation(self):
        passed = decision().semantic_export_passed
        failed = build_research_export_decision(**case(include_availability=False)).semantic_export_passed
        self.assertTrue(passed)
        self.assertFalse(failed)

    def test_verified_availability_can_pass_semantic_layer(self):
        self.assertTrue(decision().semantic_export_passed)

    def test_verified_source_date_can_pass_semantic_layer(self):
        self.assertTrue(decision().semantic_export_passed)
        values = case()
        reasons = set(values["evaluation_result"].reason_codes)
        reasons.add("MISSING_SOURCE_MARKET_DATE")
        values["evaluation_result"] = replace(
            values["evaluation_result"],
            reason_codes=tuple(item for item in EXCLUSION_REASON_CODES if item in reasons),
        )
        failed = build_research_export_decision(**values)
        self.assertIn("RD4_SEMANTIC_REASON_PRESENT", failed.export_reason_codes)

    def test_resolved_calendar_can_pass_semantic_layer(self):
        self.assertTrue(decision().semantic_export_passed)

    def test_valid_trust_can_pass_semantic_layer(self):
        self.assertTrue(decision().semantic_export_passed)

    def test_semantic_success_does_not_enable_operational_include(self):
        result = decision()
        self.assertTrue(result.semantic_export_passed)
        self.assertEqual(result.disposition, "EXCLUDE")
        self.assertIn("RESEARCH_OPERATION_NOT_AUTHORIZED", result.export_reason_codes)

    def test_empty_research_allowlist_blocks_include(self):
        self.assertEqual(RESEARCH_ENABLED_SOURCES, ())
        result = decision()
        self.assertIn("RESEARCH_SOURCE_NOT_ENABLED", result.export_reason_codes)
        self.assertNotEqual(result.disposition, "INCLUDE_CANDIDATE")

    def test_open_blockers_block_include(self):
        result = decision()
        self.assertTrue(result.blocker_ids)
        self.assertIn("OPEN_RD3_BLOCKER", result.export_reason_codes)
        self.assertNotEqual(result.disposition, "INCLUDE_CANDIDATE")

    def test_current_real_source_profiles_are_all_non_include(self):
        for index, profile in enumerate(("LME", "SMM", "YAHOO", "BZ", "WORLD_BANK"), 1):
            result = build_research_export_decision(**case(profile=profile, record=str(index)))
            self.assertNotEqual(result.disposition, "INCLUDE_CANDIDATE")


class PitBoundaryTests(unittest.TestCase):
    def test_before_cutoff_semantically_passes(self):
        result = build_research_export_decision(**case(available_at="2026-01-02T23:59:59.999999Z"))
        self.assertTrue(result.semantic_export_passed)

    def test_exact_cutoff_semantically_passes(self):
        result = decision()
        self.assertTrue(result.semantic_export_passed)
        self.assertEqual(result.temporal_context["feature_available_at_max"], result.temporal_context["research_cutoff_at"])

    def test_one_microsecond_after_cutoff_excludes(self):
        result = build_research_export_decision(**case(available_at="2026-01-03T00:00:00.000001Z"))
        self.assertFalse(result.semantic_export_passed)
        self.assertEqual(result.disposition, "EXCLUDE")
        self.assertIn("FEATURE_AVAILABLE_AFTER_CUTOFF", result.export_reason_codes)

    def test_naive_cutoff_rejected(self):
        values = case()
        values["research_cutoff_at"] = "2026-01-03T00:00:00"
        with self.assertRaises(ResearchExportInputError):
            build_research_export_decision(**values)

    def test_equivalent_timezones_are_deterministic(self):
        utc = decision()
        offset = build_research_export_decision(**case(
            cutoff="2026-01-02T19:00:00-05:00",
            evaluation_as_of="2026-01-04T19:00:00-05:00",
        ))
        self.assertEqual(utc.temporal_context, offset.temporal_context)
        self.assertEqual(utc.content_hash, offset.content_hash)

    def test_no_system_clock_dependency(self):
        values = case()
        self.assertEqual(
            build_research_export_decision(**values),
            build_research_export_decision(**values),
        )


class EvidenceTrustAndCalendarTests(unittest.TestCase):
    def test_legacy_unverified_cannot_include(self):
        result = build_research_export_decision(**case(trust_state="LEGACY_UNVERIFIED"))
        self.assertFalse(result.semantic_export_passed)
        self.assertNotEqual(result.disposition, "INCLUDE_CANDIDATE")
        self.assertIn("LEGACY_UNVERIFIED_FORBIDDEN", result.rd4_reason_codes)

    def test_shadow_unresolved_cannot_include(self):
        result = build_research_export_decision(**case(trust_state="SHADOW_UNRESOLVED"))
        self.assertFalse(result.semantic_export_passed)
        self.assertNotEqual(result.disposition, "INCLUDE_CANDIDATE")

    def test_synthetic_fixture_cannot_be_operationally_authorized(self):
        result = decision()
        self.assertFalse(result.operationally_authorized)

    def test_missing_availability_excludes(self):
        result = build_research_export_decision(**case(include_availability=False))
        self.assertEqual(result.disposition, "EXCLUDE")
        self.assertIn("AVAILABILITY_EVIDENCE_MISSING", result.export_reason_codes)

    def test_unverified_availability_excludes(self):
        result = build_research_export_decision(**case(availability_basis="UNVERIFIED"))
        self.assertIn("AVAILABILITY_NOT_VERIFIED", result.export_reason_codes)
        self.assertEqual(result.disposition, "EXCLUDE")

    def test_conservative_estimate_remains_pit_disabled(self):
        result = build_research_export_decision(**case(availability_basis="APPROVED_CONSERVATIVE_ESTIMATE"))
        self.assertIn("CONSERVATIVE_AVAILABILITY_PIT_DISABLED", result.export_reason_codes)
        self.assertFalse(result.semantic_export_passed)

    def test_missing_required_calendar_fails_closed(self):
        result = build_research_export_decision(**case(calendar_mode="MISSING"))
        self.assertIn("REQUIRED_CALENDAR_EVIDENCE_MISSING", result.export_reason_codes)
        self.assertEqual(result.disposition, "EXCLUDE")

    def test_calendar_conflict_quarantines(self):
        result = build_research_export_decision(**case(calendar_mode="CONFLICTING"))
        self.assertEqual(result.disposition, "QUARANTINE")
        self.assertIn("CALENDAR_EVIDENCE_CONFLICT", result.export_reason_codes)

    def test_calendar_is_not_guessed_from_weekday(self):
        result = build_research_export_decision(**case(calendar_mode="MISSING"))
        self.assertIn("REQUIRED_CALENDAR_EVIDENCE_MISSING", result.export_reason_codes)

    def test_unproven_historical_vintage_excludes(self):
        result = build_research_export_decision(**case(vintage_proven=False))
        self.assertIn("HISTORICAL_VINTAGE_NOT_PROVEN", result.export_reason_codes)
        self.assertEqual(result.disposition, "EXCLUDE")

    def test_licensing_blocker_excludes_operational_candidate(self):
        result = decision()
        self.assertIn("SOURCE_LICENSING_BLOCKED", result.rd4_reason_codes)
        self.assertIn("OPEN_RD3_BLOCKER", result.export_reason_codes)
        self.assertEqual(result.disposition, "EXCLUDE")


class SourceProfileSafetyTests(unittest.TestCase):
    def test_lme_restrictions_preserved(self):
        result = decision()
        self.assertTrue({"RD3-LME-001", "RD3-LME-002", "RD3-LME-003"}.issubset(result.blocker_ids))

    def test_smm_calendar_and_licensing_restrictions_preserved(self):
        result = build_research_export_decision(**case(profile="SMM", record="SMM"))
        self.assertTrue({"RD3-SMM-001", "RD3-SMM-002", "RD3-SMM-003", "RD3-SMM-004"}.issubset(result.blocker_ids))

    def test_yahoo_contract_mapping_restriction_preserved(self):
        result = build_research_export_decision(**case(profile="YAHOO", record="YAHOO"))
        self.assertIn("RD3-YAHOO-002", result.blocker_ids)
        self.assertIn("CONTINUOUS_CONTRACT_MAPPING_UNRESOLVED", result.rd4_reason_codes)

    def test_bz_venue_conflict_preserved(self):
        result = build_research_export_decision(**case(profile="BZ", record="BZ"))
        self.assertIn("RD3-BZ-001", result.blocker_ids)
        self.assertIn("SOURCE_VENUE_MAPPING_CONFLICT", result.rd4_reason_codes)

    def test_world_bank_month_does_not_imply_availability(self):
        result = build_research_export_decision(**case(
            profile="WORLD_BANK", record="WB", include_availability=False,
        ))
        self.assertEqual(result.source_period["source_period_type"], "MONTH")
        self.assertIn("AVAILABILITY_EVIDENCE_MISSING", result.export_reason_codes)
        self.assertEqual(result.disposition, "EXCLUDE")

    def test_unknown_source_profile_fails_closed(self):
        result = build_research_export_decision(**case(profile="UNKNOWN", record="UNKNOWN"))
        self.assertIn("SOURCE_PROFILE_NOT_APPROVED", result.export_reason_codes)
        self.assertEqual(result.disposition, "EXCLUDE")

    def test_all_fifteen_blockers_remain_authoritative(self):
        observed = set()
        for index, profile in enumerate(("LME", "SMM", "YAHOO", "BZ", "WORLD_BANK"), 1):
            result = build_research_export_decision(**case(profile=profile, record=f"AUTH{index}"))
            observed.update(result.blocker_ids)
        self.assertEqual(observed, set(RD3_OPEN_BLOCKERS))


class QuarantineTests(unittest.TestCase):
    def test_evidence_content_hash_mismatch_quarantines(self):
        values = case()
        refs = list(values["evaluation_result"].evidence_references)
        availability_index = next(i for i, ref in enumerate(refs) if ref.evidence_kind == "AVAILABILITY")
        original = refs[availability_index]
        refs[availability_index] = EvidenceReference(original.evidence_kind, original.evidence_id, H3)
        values["evaluation_result"] = replace(values["evaluation_result"], evidence_references=tuple(refs))
        result = build_research_export_decision(**values)
        self.assertEqual(result.disposition, "QUARANTINE")
        self.assertIn("EVIDENCE_REFERENCE_CONFLICT", result.export_reason_codes)

    def test_trust_quality_conflict_quarantines(self):
        result = build_research_export_decision(**case(quality="CONFLICT"))
        self.assertEqual(result.disposition, "QUARANTINE")
        self.assertIn("READINESS_QUARANTINED", result.export_reason_codes)

    def test_lineage_conflict_quarantines(self):
        result = build_research_export_decision(**case(lineage_conflict=True))
        self.assertEqual(result.disposition, "QUARANTINE")
        self.assertIn("REVISION_LINEAGE_CONFLICT", result.export_reason_codes)

    def test_quarantine_is_never_include(self):
        result = build_research_export_decision(**case(quality="CONFLICT"))
        self.assertNotEqual(result.disposition, "INCLUDE_CANDIDATE")

    def test_quarantine_has_no_automatic_promotion(self):
        values = case(quality="CONFLICT")
        first = build_research_export_decision(**values)
        second = build_research_export_decision(**values)
        self.assertEqual(first.disposition, "QUARANTINE")
        self.assertEqual(second.disposition, "QUARANTINE")


class IdentityLineageAndPayloadTests(unittest.TestCase):
    def test_authoritative_identity_is_preserved(self):
        values = case()
        result = build_research_export_decision(**values)
        item = values["observation"]
        item_version = values["observation_version"]
        self.assertEqual(result.observation_id, item.observation_id)
        self.assertEqual(result.observation_version_id, item_version.observation_version_id)
        self.assertEqual(result.source_id, item.source_id)
        self.assertEqual(result.instrument_id, item.instrument_id)
        self.assertEqual(result.raw_payload_hash, item_version.raw_payload_hash)
        self.assertEqual(result.transformation_version, item_version.transformation_version)

    def test_rd4_diagnostics_and_evidence_are_preserved(self):
        values = case()
        result = build_research_export_decision(**values)
        evaluated = values["evaluation_result"]
        self.assertEqual(result.rd4_reason_codes, evaluated.reason_codes)
        self.assertEqual(result.blocker_ids, evaluated.blocker_ids)
        self.assertEqual(len(result.evidence_references), len(evaluated.evidence_references))

    def test_missing_optional_source_fields_are_not_fabricated(self):
        result = decision()
        self.assertIsNone(result.source_period["represented_period_start"])
        self.assertIsNone(result.source_period["represented_period_end"])

    def test_payload_cannot_bypass_evidence_or_authorization(self):
        result = build_research_export_decision(**case(payload={"value_looks_valid": True, "score": 999}))
        self.assertFalse(result.operationally_authorized)
        self.assertEqual(result.disposition, "EXCLUDE")


class DeterminismMutabilityAndManifestTests(unittest.TestCase):
    def assert_authority_reconstruction_rejected(self, **changes):
        original, authority = decision_with_authority()
        try:
            reconstructed = replace(original, **changes)
        except (ResearchExportInputError, TypeError):
            return
        with self.assertRaises(ResearchExportInputError):
            manifest((reconstructed, authority))

    def test_same_inputs_produce_identical_decisions(self):
        values = case()
        first = build_research_export_decision(**values)
        second = build_research_export_decision(**values)
        self.assertEqual(first, second)
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        self.assertEqual(first.content_hash, second.content_hash)

    def test_unordered_input_permutation_produces_same_manifest(self):
        first = decision_with_authority_from_case(record="A")
        second = decision_with_authority_from_case(record="B")
        forward = manifest(first, second)
        reverse = manifest(second, first)
        self.assertEqual(forward.decisions, reverse.decisions)
        self.assertEqual(forward.canonical_bytes, reverse.canonical_bytes)
        self.assertEqual(forward.content_hash, reverse.content_hash)

    def test_reason_and_blocker_order_is_deterministic(self):
        result = build_research_export_decision(**case(include_availability=False, include_lineage=False, include_trust=False))
        self.assertEqual(result.blocker_ids, tuple(item for item in RD3_OPEN_BLOCKERS if item in result.blocker_ids))
        self.assertEqual(result.export_reason_codes, decision_order(result.export_reason_codes))

    def test_caller_list_mutation_cannot_change_decision(self):
        payload_list = [1, 2]
        values = case(payload={"values": payload_list})
        result = build_research_export_decision(**values)
        before = result.content_hash
        payload_list.append(3)
        self.assertEqual(result.research_payload["values"], (1, 2))
        self.assertEqual(result.content_hash, before)

    def test_caller_mapping_mutation_cannot_change_manifest(self):
        context = {"purpose": "SYNTHETIC"}
        item, authority = decision_with_authority()
        result = build_research_export_manifest(
            (item,),
            (authority,),
            research_cutoff_at="2026-01-03T00:00:00Z",
            evaluation_context=context,
        )
        before = result.content_hash
        context["purpose"] = "MUTATED"
        self.assertEqual(result.evaluation_context["purpose"], "SYNTHETIC")
        self.assertEqual(result.content_hash, before)

    def test_frozen_result_mapping_rejects_mutation(self):
        result = decision()
        with self.assertRaises(TypeError):
            result.research_payload["new"] = "value"

    def test_direct_decision_construction_cannot_forge_include(self):
        with self.assertRaises(ResearchExportInputError):
            replace(decision(), disposition="INCLUDE_CANDIDATE")

    def test_direct_decision_construction_cannot_forge_authorization(self):
        with self.assertRaises(ResearchExportInputError):
            replace(decision(), operationally_authorized=True)

    def test_authority_binding_rejects_reconstructed_readiness(self):
        self.assert_authority_reconstruction_rejected(readiness_state="REJECTED")

    def test_authority_binding_rejects_reconstructed_eligibility(self):
        self.assert_authority_reconstruction_rejected(eligibility_state="ELIGIBLE")

    def test_authority_binding_rejects_erased_rd4_reasons(self):
        self.assert_authority_reconstruction_rejected(rd4_reason_codes=())

    def test_authority_binding_rejects_erased_blockers(self):
        self.assert_authority_reconstruction_rejected(blocker_ids=())

    def test_authority_binding_rejects_reconstructed_evaluator_identity(self):
        self.assert_authority_reconstruction_rejected(evaluator_version="FORGED_EVALUATOR")

    def test_authority_binding_rejects_reconstructed_rule_bundle_version(self):
        self.assert_authority_reconstruction_rejected(rule_bundle_version="FORGED_RULES")

    def test_authority_binding_rejects_reconstructed_rule_bundle_hash(self):
        self.assert_authority_reconstruction_rejected(rule_bundle_hash=H3)

    def test_authority_binding_rejects_reconstructed_source_profile_id(self):
        self.assert_authority_reconstruction_rejected(source_profile_id="SMM")

    def test_authority_binding_rejects_reconstructed_source_profile_version(self):
        self.assert_authority_reconstruction_rejected(source_profile_version="9.9.9")

    def test_authority_binding_rejects_reconstructed_observation_content_hash(self):
        self.assert_authority_reconstruction_rejected(observation_content_hash=H3)

    def test_authority_binding_rejects_reconstructed_version_content_hash(self):
        self.assert_authority_reconstruction_rejected(observation_version_content_hash=H3)

    def test_authority_binding_rejects_reconstructed_evidence_references(self):
        original = decision()
        changed = tuple(original.evidence_references[1:])
        self.assert_authority_reconstruction_rejected(evidence_references=changed)

    def test_authority_binding_rejects_snapshot_hash_tampering(self):
        self.assert_authority_reconstruction_rejected(authority_snapshot_hash=H3)

    def test_manifest_counts_reconcile_exactly(self):
        excluded = decision_with_authority()
        quarantined = decision_with_authority_from_case(record="Q", quality="CONFLICT")
        result = manifest(excluded, quarantined)
        self.assertEqual(len(result.decisions), 2)
        self.assertEqual(result.included_candidate_count, 0)
        self.assertEqual(result.excluded_count, 1)
        self.assertEqual(result.quarantined_count, 1)
        self.assertEqual(
            len(result.decisions),
            result.included_candidate_count + result.excluded_count + result.quarantined_count,
        )

    def test_duplicate_record_decision_rejected(self):
        item = decision_with_authority()
        with self.assertRaises(ResearchExportInputError):
            manifest(item, item)

    def test_manifest_distribution_tampering_rejected(self):
        result = manifest(decision_with_authority())
        with self.assertRaises(ResearchExportInputError):
            replace(result, reason_code_distribution={})

    def test_one_semantic_field_change_changes_hash(self):
        first = decision_with_authority_from_case(payload={"synthetic_feature": 1})
        second = decision_with_authority_from_case(payload={"synthetic_feature": 2})
        self.assertNotEqual(first[0].content_hash, second[0].content_hash)
        self.assertNotEqual(manifest(first).content_hash, manifest(second).content_hash)

    def test_manifest_reuses_frozen_canonical_and_hash_profiles(self):
        result = manifest(decision_with_authority())
        self.assertEqual(result.canonical_json_profile, CANONICAL_JSON_PROFILE)
        self.assertEqual(result.hash_profile, HASH_PROFILE)
        self.assertEqual(len(result.content_hash), 64)
        self.assertEqual(result.as_dict()["manifest_content_hash"], result.content_hash)

    def test_manifest_preserves_authorization_and_blocker_authority(self):
        result = manifest(decision_with_authority())
        self.assertEqual(dict(result.authorizations), dict(SAFETY_FLAGS))
        self.assertTrue(all(value is False for value in result.authorizations.values()))
        self.assertEqual(result.research_enabled_sources, ())
        self.assertEqual(result.rd3_open_blockers, RD3_OPEN_BLOCKERS)

    def test_non_identity_audit_timestamp_is_not_implemented(self):
        projection = manifest(decision_with_authority()).content_projection()
        self.assertNotIn("generated_at", projection)
        self.assertNotIn("executed_at", projection)


class IndependentAuthorityBindingTests(unittest.TestCase):
    def assert_forged_snapshot_rejected(self, snapshot_changes, decision_changes=None):
        original, authority = decision_with_authority()
        forged_snapshot = replace(original._authority_snapshot, **snapshot_changes)
        forged_decision = replace(
            original,
            _authority_snapshot=forged_snapshot,
            **(decision_changes or {}),
        )
        with self.assertRaises(ResearchExportInputError):
            manifest((forged_decision, authority))
        return forged_decision

    def test_legitimate_decision_with_exact_authority_is_accepted(self):
        result = manifest(decision_with_authority())
        self.assertEqual(len(result.decisions), 1)
        self.assertEqual(len(result.authoritative_evaluations), 1)

    def test_whole_snapshot_forgery_is_rejected_by_independent_authority(self):
        forged = self.assert_forged_snapshot_rejected({
            "readiness_state": "REJECTED",
            "eligibility_state": "ELIGIBLE",
            "reason_codes": (),
            "blocker_ids": (),
            "rule_bundle_version": "FORGED_RULES",
            "rule_bundle_hash": H3,
            "source_profile_id": "SMM",
            "source_profile_version": "9.9.9",
            "observation_content_hash": H3,
            "observation_version_content_hash": H3,
            "evaluation_snapshot_hash": H3,
        })
        self.assertEqual(forged.readiness_state, "REJECTED")
        self.assertEqual(forged.eligibility_state, "ELIGIBLE")

    def test_direct_decision_constructor_with_forged_snapshot_is_rejected(self):
        original, authority = decision_with_authority()
        forged_snapshot = replace(
            original._authority_snapshot,
            readiness_state="REJECTED",
            eligibility_state="ELIGIBLE",
            evaluation_snapshot_hash=H3,
        )
        constructor_values = {
            item.name: getattr(original, item.name)
            for item in fields(original)
            if item.init
        }
        constructor_values["_authority_snapshot"] = forged_snapshot
        forged_decision = type(original)(**constructor_values)
        with self.assertRaises(ResearchExportInputError):
            manifest((forged_decision, authority))

    def test_readiness_forgery_is_rejected(self):
        self.assert_forged_snapshot_rejected({
            "readiness_state": "REJECTED",
            "evaluation_snapshot_hash": H3,
        })

    def test_eligibility_forgery_is_rejected(self):
        self.assert_forged_snapshot_rejected({
            "eligibility_state": "ELIGIBLE",
            "evaluation_snapshot_hash": H3,
        })

    def test_erased_reasons_are_rejected(self):
        self.assert_forged_snapshot_rejected({
            "reason_codes": (),
            "evaluation_snapshot_hash": H3,
        })

    def test_erased_blockers_are_rejected(self):
        self.assert_forged_snapshot_rejected({
            "blocker_ids": (),
            "evaluation_snapshot_hash": H3,
        })

    def test_evaluator_identity_forgery_hard_fails(self):
        original = decision()
        with self.assertRaises(ResearchExportInputError):
            replace(
                original._authority_snapshot,
                evaluator_version="FORGED_EVALUATOR",
            )

    def test_rule_bundle_identity_forgery_is_rejected(self):
        self.assert_forged_snapshot_rejected({
            "rule_bundle_version": "FORGED_RULES",
            "rule_bundle_hash": H3,
            "evaluation_snapshot_hash": H3,
        })

    def test_source_profile_identity_forgery_is_rejected(self):
        self.assert_forged_snapshot_rejected({
            "source_profile_id": "SMM",
            "source_profile_version": "9.9.9",
            "evaluation_snapshot_hash": H3,
        })

    def test_observation_content_hash_forgery_is_rejected(self):
        self.assert_forged_snapshot_rejected({
            "observation_content_hash": H3,
            "observation_version_content_hash": H3,
            "evaluation_snapshot_hash": H3,
        })

    def test_evidence_reference_forgery_is_rejected(self):
        original = decision()
        altered = original._authority_snapshot.evidence_references[1:]
        evidence_payload = tuple(
            {
                "evidence_kind": kind,
                "evidence_id": evidence_id,
                "content_hash": content_hash,
            }
            for kind, evidence_id, content_hash in altered
        )
        self.assert_forged_snapshot_rejected(
            {
                "evidence_references": altered,
                "evaluation_snapshot_hash": H3,
            },
            {"evidence_references": evidence_payload},
        )

    def test_cutoff_forgery_is_rejected(self):
        original = decision()
        temporal = dict(original.temporal_context)
        temporal["research_cutoff_at"] = "2026-01-02T23:59:59Z"
        self.assert_forged_snapshot_rejected(
            {
                "cutoff_at": temporal["research_cutoff_at"],
                "evaluation_snapshot_hash": H3,
            },
            {"temporal_context": temporal},
        )

    def test_evaluation_as_of_forgery_is_rejected(self):
        original = decision()
        temporal = dict(original.temporal_context)
        temporal["evaluation_as_of_at"] = "2026-01-06T00:00:00Z"
        self.assert_forged_snapshot_rejected(
            {
                "evaluation_as_of_at": temporal["evaluation_as_of_at"],
                "evaluation_snapshot_hash": H3,
            },
            {"temporal_context": temporal},
        )

    def test_label_available_at_forgery_is_rejected(self):
        self.assert_forged_snapshot_rejected({
            "label_available_at": "2026-01-04T00:00:00Z",
            "evaluation_snapshot_hash": H3,
        })

    def test_missing_authoritative_evaluation_hard_fails(self):
        item, _authority = decision_with_authority()
        with self.assertRaises(ResearchExportInputError):
            manifest_from((item,), ())

    def test_duplicate_authoritative_evaluation_hard_fails(self):
        item, authority = decision_with_authority()
        with self.assertRaises(ResearchExportInputError):
            manifest_from((item,), (authority, authority))

    def test_wrong_observation_evaluation_hard_fails(self):
        item, _authority = decision_with_authority()
        _other, wrong = decision_with_authority_from_case(record="WRONG_OBSERVATION")
        with self.assertRaises(ResearchExportInputError):
            manifest_from((item,), (wrong,))

    def test_wrong_observation_version_evaluation_hard_fails(self):
        item, authority = decision_with_authority()
        wrong = replace(authority, observation_version_id=H3)
        with self.assertRaises(ResearchExportInputError):
            manifest_from((item,), (wrong,))

    def test_wrong_role_evaluation_hard_fails(self):
        item, authority = decision_with_authority()
        wrong = replace(authority, evaluation_role="PIT")
        with self.assertRaises(ResearchExportInputError):
            manifest_from((item,), (wrong,))

    def test_wrong_cutoff_evaluation_hard_fails(self):
        item, authority = decision_with_authority()
        wrong = replace(authority, cutoff_at="2026-01-02T23:59:59Z")
        with self.assertRaises(ResearchExportInputError):
            manifest_from((item,), (wrong,))

    def test_extra_authoritative_evaluation_hard_fails(self):
        item, authority = decision_with_authority()
        _other, extra = decision_with_authority_from_case(record="EXTRA")
        with self.assertRaises(ResearchExportInputError):
            manifest_from((item,), (authority, extra))

    def test_authority_input_permutation_is_deterministic(self):
        first = decision_with_authority_from_case(record="AUTH_A")
        second = decision_with_authority_from_case(record="AUTH_B")
        forward = manifest_from(
            (first[0], second[0]),
            (first[1], second[1]),
        )
        reversed_authority = manifest_from(
            (first[0], second[0]),
            (second[1], first[1]),
        )
        self.assertEqual(forward.authoritative_evaluations, reversed_authority.authoritative_evaluations)
        self.assertEqual(forward.canonical_bytes, reversed_authority.canonical_bytes)
        self.assertEqual(forward.content_hash, reversed_authority.content_hash)

    def test_manifest_records_authoritative_evaluation_identity_and_hash(self):
        item, authority = decision_with_authority()
        result = manifest((item, authority))
        binding = result.content_projection()["authoritative_evaluations"][0]
        self.assertEqual(binding["observation_id"], authority.observation_id)
        self.assertEqual(binding["observation_version_id"], authority.observation_version_id)
        self.assertEqual(binding["evaluation_role"], authority.evaluation_role)
        self.assertEqual(binding["evaluation_snapshot_hash"], item._authority_snapshot.evaluation_snapshot_hash)


def decision_order(values):
    authority = EXCLUSION_REASON_CODES + (
        "SOURCE_PROFILE_NOT_APPROVED",
        "SOURCE_DATE_NOT_VERIFIED",
        "AVAILABILITY_EVIDENCE_MISSING",
        "AVAILABILITY_NOT_VERIFIED",
        "CONSERVATIVE_AVAILABILITY_PIT_DISABLED",
        "LINEAGE_EVIDENCE_MISSING",
        "LINEAGE_NOT_COMPLETE",
        "TRUST_EVIDENCE_MISSING",
        "TRUST_NOT_APPROVED",
        "REQUIRED_CALENDAR_EVIDENCE_MISSING",
        "RD4_SEMANTIC_REASON_PRESENT",
        "RESEARCH_OPERATION_NOT_AUTHORIZED",
        "RESEARCH_SOURCE_NOT_ENABLED",
        "RD4_RESEARCH_ELIGIBILITY_NOT_ELIGIBLE",
        "OPEN_RD3_BLOCKER",
        "READINESS_QUARANTINED",
        "READINESS_REJECTED",
        "EVIDENCE_REFERENCE_CONFLICT",
        "CALENDAR_EVIDENCE_CONFLICT",
        "REVISION_LINEAGE_CONFLICT",
    )
    return tuple(item for item in authority if item in set(values))


if __name__ == "__main__":
    unittest.main()
