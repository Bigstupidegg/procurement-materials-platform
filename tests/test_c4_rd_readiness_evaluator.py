from __future__ import annotations

from decimal import Decimal
import unittest

from scripts.c4_rd_contract import (
    CALENDAR_ROLES,
    EXCLUSION_REASON_CODES,
    RD3_OPEN_BLOCKERS,
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
    EvaluationInputError,
    _evaluate_semantic_rule_path,
    evaluate_readiness,
)


H1 = "1" * 64
H2 = "2" * 64


def exclusion_reasons() -> tuple[ExclusionReason, ...]:
    return tuple(
        ExclusionReason(code, "BLOCK", "RECORD", f"Synthetic {index}", "Synthetic remediation", index)
        for index, code in enumerate(EXCLUSION_REASON_CODES)
    )


def rules(**rd4_changes) -> RuleBundle:
    rd4 = {
        "evaluator_version": EVALUATOR_VERSION,
        "rd3_open_blockers": RD3_OPEN_BLOCKERS,
        "pit_enabled_sources": (),
        "research_enabled_sources": (),
        "backtest_enabled_sources": (),
    }
    rd4.update(rd4_changes)
    return RuleBundle(
        rule_bundle_version="rd4.2-rules@1.0.0",
        contract_version="1.0.0",
        exclusion_catalog_version="1.1.0",
        exclusion_reasons=exclusion_reasons(),
        rules={"rd4_2": rd4},
    )


def calendar(kind: str = "SYNTHETIC_MARKET") -> CalendarReference:
    return CalendarReference("SYNTHETIC_CAL", "1.0.0", kind, ({"date": "2026-01-02"},))


def assignments(required_role: str | None = None, reference: CalendarReference | None = None):
    values = []
    for role in CALENDAR_ROLES:
        if role == required_role and reference is not None:
            values.append(CalendarAssignment(
                "synthetic-subject", role, "RESOLVED", reference.calendar_reference_id,
                reference.calendar_version, reference.calendar_hash,
            ))
        else:
            values.append(CalendarAssignment("synthetic-subject", role, "NOT_APPLICABLE"))
    return tuple(values)


def observation(
    *,
    source_id: str = "LME",
    instrument_id: str = "LME-COPPER",
    required_role: str | None = "MARKET",
    reference: CalendarReference | None = None,
    source_period_type: str = "DAILY_MARKET",
    source_market_date: str | None = "2026-01-02",
    source_period_start_date: str | None = None,
    source_period_end_date: str | None = None,
    semantic_data=None,
) -> Observation:
    return Observation(
        source_id=source_id,
        source_record_identifier="SYNTHETIC_RECORD_001",
        metric_id="SYNTHETIC_PRICE",
        instrument_id=instrument_id,
        source_period_type=source_period_type,
        source_market_date=source_market_date,
        source_period_start_date=source_period_start_date,
        source_period_end_date=source_period_end_date,
        calendar_assignments=assignments(required_role, reference),
        collected_at="2026-01-03T00:00:00Z",
        created_at="2026-01-03T00:00:01Z",
        semantic_data={"value": Decimal("12.50")} if semantic_data is None else semantic_data,
    )


def version(item: Observation, *, semantic_data=None) -> ObservationVersion:
    return ObservationVersion(
        observation_id=item.observation_id,
        source_version_or_release_key="SYNTHETIC_RELEASE_001",
        stable_version_key="SYNTHETIC_VERSION_001",
        raw_payload_hash=H2,
        transformation_version="synthetic-transform@1.0.0",
        revision_available_at="2026-01-03T00:00:00Z",
        created_at="2026-01-03T00:00:01Z",
        semantic_data={"value": 12} if semantic_data is None else semantic_data,
    )


def availability(item: ObservationVersion, at: str, basis: str = "VERIFIED_ARCHIVE_TIMESTAMP"):
    return AvailabilityAssessment(
        subject_id=item.observation_version_id,
        assessment_role="SOURCE",
        evaluation_at="2026-01-05T00:00:00Z",
        rule_bundle_version="rd4.2-rules@1.0.0",
        result={"feature_available_at_max": at},
        evidence={"fixture": "SYNTHETIC_FIXTURE"},
        availability_basis=basis,
    )


def lineage(item: ObservationVersion, *, complete=True, vintage=True):
    return LineageAssessment(
        subject_id=item.observation_version_id,
        assessment_role="HISTORICAL",
        evaluation_at="2026-01-05T00:00:00Z",
        rule_bundle_version="rd4.2-rules@1.0.0",
        result={"lineage_complete": complete, "historical_vintage_proven": vintage},
        evidence={"fixture": "SYNTHETIC_FIXTURE"},
    )


def trust(item: ObservationVersion, state="REAL_ORIGIN_VERIFIED", quality="PASS"):
    return TrustQualityAssessment(
        subject_id=item.observation_version_id,
        assessment_role="SOURCE",
        evaluation_at="2026-01-05T00:00:00Z",
        rule_bundle_version="rd4.2-rules@1.0.0",
        result={"synthetic_rule_check": True},
        evidence={"fixture": "SYNTHETIC_FIXTURE"},
        trust_state=state,
        quality_status=quality,
        reason_codes=(),
    )


def request(
    *,
    item: Observation | None = None,
    item_version: ObservationVersion | None = None,
    profile="LME",
    profile_version=SOURCE_PROFILE_VERSION,
    role="PIT",
    available_at="2026-01-03T00:00:00Z",
    availability_basis="VERIFIED_ARCHIVE_TIMESTAMP",
    include_availability=True,
    include_lineage=True,
    include_trust=True,
    calendar_references=None,
    rule_bundle=None,
    cutoff="2026-01-03T00:00:00Z",
    evaluation_as_of="2026-01-05T00:00:00Z",
    label_at="2026-01-04T00:00:00Z",
):
    if item is None:
        ref = calendar()
        item = observation(reference=ref)
        default_references = (ref,)
    else:
        default_references = ()
    item_version = item_version or version(item)
    return {
        "observation": item,
        "observation_version": item_version,
        "rule_bundle": rule_bundle or rules(),
        "evaluation_role": role,
        "cutoff_at": cutoff,
        "evaluation_as_of_at": evaluation_as_of,
        "source_profile_id": profile,
        "source_profile_version": profile_version,
        "availability_assessment": availability(item_version, available_at, availability_basis)
        if include_availability else None,
        "calendar_references": default_references if calendar_references is None else calendar_references,
        "lineage_assessment": lineage(item_version) if include_lineage else None,
        "trust_quality_assessment": trust(item_version) if include_trust else None,
        "label_available_at": label_at,
    }


def semantic_decision(
    *,
    role="PIT",
    feature_at="2026-01-03T00:00:00Z",
    cutoff="2026-01-03T00:00:00Z",
    evaluation_as_of="2026-01-05T00:00:00Z",
    label_at=None,
    source_date_verified=True,
    availability_verified=True,
):
    return _evaluate_semantic_rule_path(
        evaluation_role=role,
        source_date_verified=source_date_verified,
        availability_verified=availability_verified,
        feature_available_at_max=feature_at,
        cutoff_at=cutoff,
        evaluation_as_of_at=evaluation_as_of,
        label_available_at=label_at,
    )


class InputAndIdentityTests(unittest.TestCase):
    def test_valid_input_enters_evaluator(self):
        result = evaluate_readiness(**request())
        self.assertEqual(result.eligibility_state, "INELIGIBLE")
        self.assertEqual(result.observation_id, result.as_dict()["subject"]["observation_id"])

    def test_invalid_observation_hard_fails(self):
        values = request()
        values["observation"] = object()
        with self.assertRaises(EvaluationInputError):
            evaluate_readiness(**values)

    def test_invalid_version_hard_fails(self):
        values = request()
        values["observation_version"] = object()
        with self.assertRaises(EvaluationInputError):
            evaluate_readiness(**values)

    def test_naive_timestamp_hard_fails(self):
        with self.assertRaises(EvaluationInputError):
            evaluate_readiness(**request(cutoff="2026-01-03T00:00:00"))

    def test_unsupported_contract_version_hard_fails(self):
        with self.assertRaises(EvaluationInputError):
            evaluate_readiness(**request(), contract_version="2.0.0")

    def test_observation_version_mismatch_hard_fails(self):
        first = observation(reference=calendar())
        second = observation(instrument_id="LME-ALUMINIUM", reference=calendar())
        with self.assertRaises(EvaluationInputError):
            evaluate_readiness(**request(item=first, item_version=version(second)))

    def test_contradictory_stable_identity_hard_fails(self):
        item = observation(reference=calendar(), semantic_data={"stable_identity": {"source_id": "SMM"}})
        with self.assertRaises(EvaluationInputError):
            evaluate_readiness(**request(item=item))

    def test_rule_bundle_cannot_self_clear_open_blocker(self):
        invalid = rules(rd3_open_blockers=RD3_OPEN_BLOCKERS[:-1])
        with self.assertRaises(EvaluationInputError):
            evaluate_readiness(**request(rule_bundle=invalid))


class PitBoundaryTests(unittest.TestCase):
    def test_feature_pit_before_cutoff_without_label_semantically_passes(self):
        decision = semantic_decision(feature_at="2026-01-02T23:59:59.999999Z")
        self.assertTrue(decision.passed)
        self.assertEqual(decision.reason_codes, ())

    def test_feature_pit_at_cutoff_without_label_semantically_passes(self):
        decision = semantic_decision()
        self.assertTrue(decision.passed)
        result = evaluate_readiness(**request(label_at=None))
        self.assertNotIn("LABEL_HORIZON_NOT_ELAPSED", result.reason_codes)
        self.assertNotIn("LABEL_AVAILABLE_AT_OR_BEFORE_CUTOFF", result.reason_codes)

    def test_feature_pit_one_microsecond_after_cutoff_semantically_fails(self):
        decision = semantic_decision(feature_at="2026-01-03T00:00:00.000001Z")
        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason_codes, ("FEATURE_AVAILABLE_AFTER_CUTOFF",))

    def test_backtest_missing_label_semantically_fails_closed(self):
        decision = semantic_decision(role="BACKTEST")
        self.assertFalse(decision.passed)
        self.assertIn("LABEL_HORIZON_NOT_ELAPSED", decision.reason_codes)

    def test_backtest_label_at_or_before_cutoff_semantically_fails(self):
        decision = semantic_decision(role="BACKTEST", label_at="2026-01-03T00:00:00Z")
        self.assertFalse(decision.passed)
        self.assertIn("LABEL_AVAILABLE_AT_OR_BEFORE_CUTOFF", decision.reason_codes)

    def test_backtest_evaluation_before_label_semantically_fails(self):
        decision = semantic_decision(
            role="BACKTEST",
            evaluation_as_of="2026-01-04T00:00:00Z",
            label_at="2026-01-05T00:00:00Z",
        )
        self.assertFalse(decision.passed)
        self.assertIn("LABEL_HORIZON_NOT_ELAPSED", decision.reason_codes)

    def test_source_date_and_availability_are_required_for_semantic_success(self):
        self.assertTrue(semantic_decision().passed)
        self.assertIn(
            "MISSING_SOURCE_MARKET_DATE",
            semantic_decision(source_date_verified=False).reason_codes,
        )
        self.assertIn(
            "AVAILABILITY_UNVERIFIED",
            semantic_decision(availability_verified=False).reason_codes,
        )

    def test_equivalent_timezones_produce_equal_semantic_decisions(self):
        utc = semantic_decision()
        offset = semantic_decision(
            feature_at="2026-01-02T19:00:00-05:00",
            cutoff="2026-01-02T19:00:00-05:00",
            evaluation_as_of="2026-01-04T19:00:00-05:00",
        )
        self.assertEqual(utc, offset)

    def test_availability_before_cutoff_passes(self):
        result = evaluate_readiness(**request(available_at="2026-01-02T23:59:59.999999Z"))
        self.assertEqual(result.readiness_state, "REAL_AVAILABILITY_VERIFIED")
        self.assertNotIn("FEATURE_AVAILABLE_AFTER_CUTOFF", result.reason_codes)

    def test_availability_exactly_at_cutoff_passes(self):
        result = evaluate_readiness(**request())
        self.assertEqual(result.readiness_state, "REAL_AVAILABILITY_VERIFIED")

    def test_one_microsecond_after_cutoff_fails(self):
        result = evaluate_readiness(**request(available_at="2026-01-03T00:00:00.000001Z"))
        self.assertEqual(result.readiness_state, "REAL_SOURCE_DATE_VERIFIED")
        self.assertIn("FEATURE_AVAILABLE_AFTER_CUTOFF", result.reason_codes)

    def test_missing_availability_cannot_pass(self):
        result = evaluate_readiness(**request(include_availability=False))
        self.assertEqual(result.readiness_state, "REAL_SOURCE_DATE_VERIFIED")
        self.assertIn("MISSING_SOURCE_AVAILABLE_AT", result.reason_codes)

    def test_equivalent_timezones_normalize_identically(self):
        utc = evaluate_readiness(**request())
        offset = evaluate_readiness(**request(
            cutoff="2026-01-02T19:00:00-05:00",
            evaluation_as_of="2026-01-04T19:00:00-05:00",
            label_at="2026-01-03T19:00:00-05:00",
            available_at="2026-01-02T19:00:00-05:00",
        ))
        self.assertEqual(utc.cutoff_at, offset.cutoff_at)
        self.assertEqual(utc.evaluation_as_of_at, offset.evaluation_as_of_at)
        self.assertEqual(utc.label_available_at, offset.label_available_at)
        self.assertEqual(utc.readiness_state, offset.readiness_state)
        self.assertEqual(utc.eligibility_state, offset.eligibility_state)
        self.assertEqual(utc.reason_codes, offset.reason_codes)
        self.assertEqual(utc.blocker_ids, offset.blocker_ids)

    def test_naive_availability_timestamp_rejected(self):
        with self.assertRaises(EvaluationInputError):
            evaluate_readiness(**request(available_at="2026-01-03T00:00:00"))

    def test_label_must_follow_cutoff(self):
        result = evaluate_readiness(**request(
            role="BACKTEST", label_at="2026-01-03T00:00:00Z"
        ))
        self.assertIn("LABEL_AVAILABLE_AT_OR_BEFORE_CUTOFF", result.reason_codes)

    def test_evaluation_must_reach_label(self):
        result = evaluate_readiness(**request(
            role="BACKTEST", evaluation_as_of="2026-01-05T00:00:00Z",
            label_at="2026-01-06T00:00:00Z",
        ))
        self.assertIn("LABEL_HORIZON_NOT_ELAPSED", result.reason_codes)


class EvidenceAndProgressionTests(unittest.TestCase):
    def test_unverified_basis_does_not_advance_readiness(self):
        result = evaluate_readiness(**request(availability_basis="UNVERIFIED"))
        self.assertEqual(result.readiness_state, "REAL_SOURCE_DATE_VERIFIED")
        self.assertIn("AVAILABILITY_UNVERIFIED", result.reason_codes)

    def test_missing_calendar_is_ineligible(self):
        item = observation(reference=None)
        result = evaluate_readiness(**request(item=item))
        self.assertIn("REQUIRED_CALENDAR_ROLE_MISSING", result.reason_codes)
        self.assertIn("CALENDAR_UNRESOLVED", result.reason_codes)

    def test_unproven_vintage_prevents_historical_progression(self):
        values = request(role="BACKTEST")
        item_version = values["observation_version"]
        values["lineage_assessment"] = lineage(item_version, vintage=False)
        result = evaluate_readiness(**values)
        self.assertIn("HISTORICAL_VINTAGE_NOT_PROVEN", result.reason_codes)
        self.assertNotEqual(result.readiness_state, "REAL_BACKTEST_READY")

    def test_licensing_does_not_erase_verified_availability(self):
        result = evaluate_readiness(**request())
        self.assertEqual(result.readiness_state, "REAL_AVAILABILITY_VERIFIED")
        self.assertIn("SOURCE_LICENSING_BLOCKED", result.reason_codes)
        self.assertEqual(result.eligibility_state, "INELIGIBLE")

    def test_readiness_cannot_jump_over_availability(self):
        result = evaluate_readiness(**request(include_availability=False))
        self.assertNotIn(result.readiness_state, ("REAL_RESEARCH_READY", "REAL_BACKTEST_READY"))

    def test_legacy_never_auto_eligible(self):
        values = request()
        item_version = values["observation_version"]
        values["trust_quality_assessment"] = trust(item_version, state="LEGACY_UNVERIFIED")
        result = evaluate_readiness(**values)
        self.assertIn("LEGACY_UNVERIFIED_FORBIDDEN", result.reason_codes)
        self.assertEqual(result.eligibility_state, "INELIGIBLE")

    def test_shadow_never_auto_eligible(self):
        values = request()
        item_version = values["observation_version"]
        values["trust_quality_assessment"] = trust(item_version, state="SHADOW_UNRESOLVED")
        result = evaluate_readiness(**values)
        self.assertIn("SHADOW_EXPORT_NOT_AUTHORIZED", result.reason_codes)
        self.assertEqual(result.eligibility_state, "INELIGIBLE")

    def test_unknown_profile_fails_closed_for_eligibility(self):
        result = evaluate_readiness(**request(profile="UNKNOWN", profile_version="9.9.9"))
        self.assertEqual(result.eligibility_state, "INELIGIBLE")
        self.assertIn("TRUST_STATE_NOT_APPROVED", result.reason_codes)


class SourceProfileTests(unittest.TestCase):
    def test_lme_blocker_mapping(self):
        result = evaluate_readiness(**request(role="BACKTEST"))
        self.assertTrue({"RD3-LME-001", "RD3-LME-002", "RD3-LME-003"}.issubset(result.blocker_ids))

    def test_smm_blocker_mapping_and_calendar_uncertainty(self):
        item = observation(source_id="SMM", instrument_id="SMM-COPPER", required_role="PUBLICATION")
        result = evaluate_readiness(**request(item=item, profile="SMM", role="BACKTEST"))
        self.assertTrue({"RD3-SMM-001", "RD3-SMM-002", "RD3-SMM-003", "RD3-SMM-004"}.issubset(result.blocker_ids))
        self.assertIn("CALENDAR_UNRESOLVED", result.reason_codes)

    def test_smm_normal_time_rule_cannot_bypass_calendar_uncertainty(self):
        item = observation(source_id="SMM", instrument_id="SMM-COPPER", required_role="PUBLICATION")
        bundle = rules(normal_publication_time="11:00")
        result = evaluate_readiness(**request(item=item, profile="SMM", rule_bundle=bundle))
        self.assertIn("RD3-SMM-002", result.blocker_ids)
        self.assertIn("CALENDAR_UNRESOLVED", result.reason_codes)

    def test_yahoo_blocker_mapping(self):
        ref = calendar()
        item = observation(source_id="YAHOO", instrument_id="HG=F", reference=ref)
        result = evaluate_readiness(**request(
            item=item, profile="YAHOO", role="BACKTEST", calendar_references=(ref,)
        ))
        self.assertTrue({
            "RD3-YAHOO-001", "RD3-YAHOO-002", "RD3-YAHOO-003", "RD3-YAHOO-004",
        }.issubset(result.blocker_ids))

    def test_bz_has_venue_conflict_without_guessing(self):
        item = observation(source_id="YAHOO", instrument_id="BZ=F", required_role=None)
        result = evaluate_readiness(**request(item=item, profile="BZ"))
        self.assertIn("RD3-BZ-001", result.blocker_ids)
        self.assertIn("SOURCE_VENUE_MAPPING_CONFLICT", result.reason_codes)
        self.assertNotIn("venue", result.as_dict()["audit"])

    def test_bz_cannot_hide_inside_yahoo_profile(self):
        ref = calendar()
        item = observation(source_id="YAHOO", instrument_id="BZ=F", reference=ref)
        result = evaluate_readiness(**request(
            item=item, profile="YAHOO", calendar_references=(ref,)
        ))
        self.assertIn("RD3-BZ-001", result.blocker_ids)

    def test_world_bank_month_does_not_imply_availability(self):
        item = observation(
            source_id="WORLD_BANK", instrument_id="WB-SERIES", required_role="PUBLICATION",
            source_period_type="MONTH", source_market_date=None,
            source_period_start_date="2026-08-01", source_period_end_date="2026-08-31",
        )
        result = evaluate_readiness(**request(
            item=item, profile="WORLD_BANK", role="RESEARCH", include_availability=False,
            label_at=None,
        ))
        self.assertEqual(result.readiness_state, "REAL_SOURCE_DATE_VERIFIED")
        self.assertIn("MISSING_SOURCE_AVAILABLE_AT", result.reason_codes)
        self.assertTrue({"RD3-WB-001", "RD3-WB-002", "RD3-WB-003"}.issubset(result.blocker_ids))


class DeterminismAndAuthorityTests(unittest.TestCase):
    def test_semantic_success_does_not_enable_operational_eligibility(self):
        self.assertTrue(semantic_decision().passed)
        result = evaluate_readiness(**request(label_at=None))
        self.assertEqual(result.eligibility_state, "INELIGIBLE")
        self.assertIn("SHADOW_EXPORT_NOT_AUTHORIZED", result.reason_codes)

    def test_same_input_produces_identical_result(self):
        values = request()
        self.assertEqual(evaluate_readiness(**values), evaluate_readiness(**values))
        self.assertEqual(evaluate_readiness(**values).as_dict(), evaluate_readiness(**values).as_dict())

    def test_no_hidden_current_time_dependency(self):
        first = evaluate_readiness(**request())
        second = evaluate_readiness(**request())
        self.assertEqual(first, second)

    def test_caller_mutation_cannot_change_accepted_result(self):
        ref = calendar()
        item = observation(reference=ref)
        mutable_references = [ref]
        result = evaluate_readiness(**request(
            item=item, calendar_references=mutable_references
        ))
        before = result.as_dict()
        mutable_references.clear()
        self.assertEqual(result.as_dict(), before)

    def test_profile_cannot_override_core_after_cutoff_failure(self):
        bundle = rules(profile_override={"allow_after_cutoff": True})
        result = evaluate_readiness(**request(
            rule_bundle=bundle, available_at="2026-01-03T00:00:00.000001Z"
        ))
        self.assertIn("FEATURE_AVAILABLE_AFTER_CUTOFF", result.reason_codes)

    def test_multiple_reasons_and_blockers_use_authoritative_order(self):
        result = evaluate_readiness(**request(
            role="BACKTEST", include_availability=False, include_lineage=False,
            include_trust=False, label_at=None,
        ))
        expected_reasons = tuple(code for code in EXCLUSION_REASON_CODES if code in result.reason_codes)
        expected_blockers = tuple(code for code in RD3_OPEN_BLOCKERS if code in result.blocker_ids)
        self.assertEqual(result.reason_codes, expected_reasons)
        self.assertEqual(result.blocker_ids, expected_blockers)

    def test_exact_15_open_blockers_remain_authoritative_across_profiles(self):
        observed = set()
        cases = (
            ("LME", observation(reference=calendar()), "BACKTEST"),
            ("SMM", observation(source_id="SMM", instrument_id="SMM", required_role="PUBLICATION"), "BACKTEST"),
            ("YAHOO", observation(source_id="YAHOO", instrument_id="HG=F"), "BACKTEST"),
            ("BZ", observation(source_id="YAHOO", instrument_id="BZ=F", required_role=None), "PIT"),
            ("WORLD_BANK", observation(
                source_id="WORLD_BANK", instrument_id="WB", required_role="PUBLICATION",
                source_period_type="MONTH", source_market_date=None,
                source_period_start_date="2026-08-01", source_period_end_date="2026-08-31",
            ), "BACKTEST"),
        )
        for profile, item, role in cases:
            observed.update(evaluate_readiness(**request(
                item=item, profile=profile, role=role, calendar_references=(),
            )).blocker_ids)
        self.assertEqual(observed, set(RD3_OPEN_BLOCKERS))


if __name__ == "__main__":
    unittest.main()
