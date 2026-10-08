"""Deterministic synthetic tests for the C4-PIT-2B claim-first PoC."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import unittest
from unittest.mock import patch

from scripts.c4_pit_evidence_contract import (
    DAY_GRANULAR_POLICY_VERSION,
    STRICT_INTRADAY_POLICY_VERSION,
    EvidenceArtifact,
    EvidenceClaim,
    EvidenceContractError,
    EvidenceSubjectBinding,
    HistoricalVintageClaim,
    MaterializationContract,
    ObservationVersionCandidate,
    SourceRelease,
    TemporalBound,
    TemporalEligibilityDecision,
    evaluate_temporal_eligibility,
    materialize_observation_version_candidate,
    select_visible_vintage,
    validate_artifact_consistency,
    validate_revision_lineage,
    validate_subject_binding,
)
from scripts.c4_rd_contract import (
    BACKTEST_ENABLED_SOURCES,
    PIT_ENABLED_SOURCES,
    RESEARCH_ENABLED_SOURCES,
    SAFETY_FLAGS,
    CalendarAssignment,
    Observation,
)


SYNTHETIC_TIMEZONE = "Asia/Taipei"
TRANSFORM = "synthetic-world-bank-record@1.0.0"
RECORD_LOCATOR = {
    "page": "SYNTHETIC_PAGE_1",
    "table": "SYNTHETIC_TABLE_MONTHLY",
    "row": "SYNTHETIC_ROW_2024_02",
    "label": "SYNTHETIC_COPPER",
}
PERIOD = {
    "source_period_type": "MONTH",
    "source_market_date": None,
    "source_period_start_date": "2024-02-01",
    "source_period_end_date": "2024-02-29",
}
RECORD_STATES = (
    "SOURCE_RELEASE_BOUND",
    "OFFICIAL_ATTACHMENT_BOUND",
    "OFFICIAL_RECORD_VALUE_BOUND",
    "EXACT_ARTIFACT_HASH_BOUND",
)


def observation() -> Observation:
    assignments = tuple(
        CalendarAssignment("copper", role, "NOT_APPLICABLE")
        for role in ("MARKET", "PUBLICATION", "LOCAL_OPERATIONAL", "SCHEDULER")
    )
    return Observation(
        source_id="WORLD_BANK",
        source_record_identifier="WORLD_BANK:PINK_SHEET:COPPER:2024-02",
        metric_id="MONTHLY_PRICE",
        instrument_id="copper_world_bank_monthly",
        source_period_type="MONTH",
        source_market_date=None,
        source_period_start_date="2024-02-01",
        source_period_end_date="2024-02-29",
        calendar_assignments=assignments,
        semantic_data={"synthetic_contract_fixture": True},
    )


def release(label: str) -> SourceRelease:
    return SourceRelease(
        source_id="WORLD_BANK",
        data_acquisition_channel="WORLD_BANK_PINK_SHEET_XLSX",
        historical_evidence_channel="WORLD_BANK_PINK_SHEET_RELEASE_PDF",
        release_family="SYNTHETIC_PINK_SHEET_MONTHLY_RELEASE",
        official_release_id=f"SYNTHETIC_RELEASE_{label}",
        release_label=f"Synthetic {label} release",
    )


def artifact(item: SourceRelease, label: str, payload: bytes | None = None) -> EvidenceArtifact:
    return EvidenceArtifact.from_payload(
        source_release_id=item.source_release_id,
        artifact_role="SYNTHETIC_RELEASE_EVIDENCE",
        media_type="application/pdf",
        official_locator=f"synthetic://world-bank-release/{label}",
        payload=payload or f"CLEARLY ARTIFICIAL PDF PAYLOAD {label}".encode("ascii"),
        retrieved_at="2026-10-07T00:00:00Z",
        source_document_id=f"SYNTHETIC_DOCUMENT_{label}",
    )


def temporal(value: str) -> TemporalBound:
    return TemporalBound(
        "BY_DATE",
        "DATE_ONLY",
        value,
        source_timezone=SYNTHETIC_TIMEZONE,
    )


def evidence_claim(
    item_release: SourceRelease,
    item_artifact: EvidenceArtifact,
    value: str,
    available_date: str,
) -> EvidenceClaim:
    return EvidenceClaim(
        source_release_id=item_release.source_release_id,
        artifact_id=item_artifact.artifact_id,
        claim_type="RECORD_VALUE",
        subject_scope="RECORD_BOUND",
        claimed_period=PERIOD,
        claimed_value=Decimal(value),
        unit="USD/MT",
        currency="USD",
        temporal_bound=temporal(available_date),
        evidence_authority="OFFICIAL_RECORD",
        safe_locator=RECORD_LOCATOR,
        transformation_version=TRANSFORM,
    )


def record_binding(
    claim: EvidenceClaim,
    item_release: SourceRelease,
    item_artifact: EvidenceArtifact,
    *,
    states: tuple[str, ...] = RECORD_STATES,
) -> EvidenceSubjectBinding:
    return EvidenceSubjectBinding(
        claim_id=claim.claim_id,
        subject_scope="RECORD_BOUND",
        binding_states=states,
        source_release_id=item_release.source_release_id,
        artifact_id=item_artifact.artifact_id,
        expected_artifact_hash=item_artifact.payload_hash,
        record_locator=RECORD_LOCATOR,
        source_id="WORLD_BANK",
        instrument_id="copper_world_bank_monthly",
        metric_id="MONTHLY_PRICE",
        source_record_identifier="WORLD_BANK:PINK_SHEET:COPPER:2024-02",
    )


def vintage(
    item_observation: Observation,
    item_release: SourceRelease,
    claim: EvidenceClaim,
    binding: EvidenceSubjectBinding,
    label: str,
    value: str,
    *,
    parent: str | None = None,
) -> HistoricalVintageClaim:
    return HistoricalVintageClaim(
        observation_id=item_observation.observation_id,
        source_id="WORLD_BANK",
        instrument_id="copper_world_bank_monthly",
        metric_id="MONTHLY_PRICE",
        source_record_identifier="WORLD_BANK:PINK_SHEET:COPPER:2024-02",
        source_period=PERIOD,
        source_release_id=item_release.source_release_id,
        release_record_key=f"SYNTHETIC_{label}_COPPER_2024_02",
        semantic_value=Decimal(value),
        unit="USD/MT",
        currency="USD",
        record_locator=RECORD_LOCATOR,
        evidence_claim_ids=(claim.claim_id,),
        evidence_claim_content_hashes=(claim.content_hash,),
        binding_ids=(binding.binding_id,),
        binding_content_hashes=(binding.content_hash,),
        temporal_bound=claim.temporal_bound,
        evidence_authority="OFFICIAL_RECORD",
        transformation_version=TRANSFORM,
        parent_vintage_claim_id=parent,
    )


class SyntheticCase:
    def __init__(self) -> None:
        self.observation = observation()
        self.march_release = release("2024_03")
        self.april_release = release("2024_04")
        self.march_artifact = artifact(self.march_release, "2024_03")
        self.april_artifact = artifact(self.april_release, "2024_04")
        self.march_claim = evidence_claim(
            self.march_release, self.march_artifact, "8300", "2024-03-06"
        )
        self.april_claim = evidence_claim(
            self.april_release, self.april_artifact, "8305", "2024-04-08"
        )
        self.march_binding = record_binding(
            self.march_claim, self.march_release, self.march_artifact
        )
        self.april_binding = record_binding(
            self.april_claim, self.april_release, self.april_artifact
        )
        self.march = vintage(
            self.observation,
            self.march_release,
            self.march_claim,
            self.march_binding,
            "MARCH",
            "8300",
        )
        self.april = vintage(
            self.observation,
            self.april_release,
            self.april_claim,
            self.april_binding,
            "APRIL",
            "8305",
            parent=self.march.vintage_claim_id,
        )
        self.vintages = (self.march, self.april)
        self.artifacts = (self.march_artifact, self.april_artifact)
        self.bindings = (self.march_binding, self.april_binding)
        self.day_contract = MaterializationContract(
            mode="DAY_GRANULAR_PIT",
            policy_version=DAY_GRANULAR_POLICY_VERSION,
            transformation_version=TRANSFORM,
            source_timezone=SYNTHETIC_TIMEZONE,
        )


class EvidenceContractPositiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = SyntheticCase()

    def test_deterministic_source_release_identity_and_separate_channels(self) -> None:
        replay = release("2024_03")
        self.assertEqual(self.case.march_release.source_release_id, replay.source_release_id)
        self.assertEqual(self.case.march_release.content_hash, replay.content_hash)
        self.assertNotEqual(
            replay.data_acquisition_channel,
            replay.historical_evidence_channel,
        )
        weak = SourceRelease(
            source_id="SYNTHETIC_SOURCE",
            data_acquisition_channel="SYNTHETIC_DATA_CHANNEL",
            historical_evidence_channel="SYNTHETIC_EVIDENCE_CHANNEL",
            release_family="SYNTHETIC_FAMILY",
            release_label="SYNTHETIC_WEAK_RELEASE",
        )
        self.assertEqual(weak.identity_strength, "WEAK_RELEASE_LABEL")

    def test_deterministic_artifact_identity_and_payload_hash(self) -> None:
        replay = artifact(self.case.march_release, "2024_03")
        later_retrieval = replace(replay, retrieved_at="2026-10-08T00:00:00Z")
        self.assertEqual(self.case.march_artifact.artifact_id, replay.artifact_id)
        self.assertEqual(self.case.march_artifact.payload_hash, replay.payload_hash)
        self.assertEqual(replay.artifact_id, later_retrieval.artifact_id)
        self.assertNotEqual(replay.content_hash, later_retrieval.content_hash)

    def test_deterministic_claim_identity_and_content_hash(self) -> None:
        replay = evidence_claim(
            self.case.march_release,
            self.case.march_artifact,
            "8300",
            "2024-03-06",
        )
        self.assertEqual(self.case.march_claim.claim_id, replay.claim_id)
        self.assertEqual(self.case.march_claim.content_hash, replay.content_hash)

    def test_deterministic_vintage_identity_and_content_hash(self) -> None:
        replay = vintage(
            self.case.observation,
            self.case.march_release,
            self.case.march_claim,
            self.case.march_binding,
            "MARCH",
            "8300",
        )
        self.assertEqual(self.case.march.vintage_claim_id, replay.vintage_claim_id)
        self.assertEqual(self.case.march.content_hash, replay.content_hash)

    def test_same_synthetic_input_replays_identically(self) -> None:
        replay = SyntheticCase()
        self.assertEqual(
            (
                self.case.observation.observation_id,
                self.case.march_release.source_release_id,
                self.case.march_artifact.artifact_id,
                self.case.march_claim.claim_id,
                self.case.march_binding.binding_id,
                self.case.march.vintage_claim_id,
                self.case.april.vintage_claim_id,
            ),
            (
                replay.observation.observation_id,
                replay.march_release.source_release_id,
                replay.march_artifact.artifact_id,
                replay.march_claim.claim_id,
                replay.march_binding.binding_id,
                replay.march.vintage_claim_id,
                replay.april.vintage_claim_id,
            ),
        )

    def test_two_vintages_share_observation_but_remain_distinct(self) -> None:
        self.assertEqual(self.case.march.observation_id, self.case.april.observation_id)
        self.assertNotEqual(self.case.march.vintage_claim_id, self.case.april.vintage_claim_id)
        self.assertNotEqual(self.case.march.content_hash, self.case.april.content_hash)
        self.assertEqual(self.case.march.semantic_value, Decimal("8300"))
        self.assertEqual(self.case.april.semantic_value, Decimal("8305"))

    def test_revision_lineage_validates(self) -> None:
        validate_revision_lineage(self.case.vintages)
        self.assertEqual(self.case.april.parent_vintage_claim_id, self.case.march.vintage_claim_id)

    def test_day_granular_cutoff_selects_initial_then_revision(self) -> None:
        before_initial = select_visible_vintage(
            self.case.vintages,
            cutoff_at="2024-03-06T15:59:59Z",
            mode="DAY_GRANULAR_PIT",
            source_timezone=SYNTHETIC_TIMEZONE,
        )
        between = select_visible_vintage(
            self.case.vintages,
            cutoff_at="2024-04-08T15:59:59Z",
            mode="DAY_GRANULAR_PIT",
            source_timezone=SYNTHETIC_TIMEZONE,
        )
        after_revision = select_visible_vintage(
            self.case.vintages,
            cutoff_at="2024-04-08T16:00:00Z",
            mode="DAY_GRANULAR_PIT",
            source_timezone=SYNTHETIC_TIMEZONE,
        )
        self.assertIsNone(before_initial)
        self.assertEqual(between, self.case.march)
        self.assertEqual(after_revision, self.case.april)

    def test_strict_intraday_rejects_both_date_only_vintages(self) -> None:
        self.assertIsNone(
            select_visible_vintage(
                self.case.vintages,
                cutoff_at="2024-05-01T00:00:00Z",
                mode="STRICT_INTRADAY_PIT",
            )
        )
        with self.assertRaises(EvidenceContractError):
            materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=self.case.april_binding,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=self.case.bindings,
                cutoff_at="2024-05-01T00:00:00Z",
                contract=MaterializationContract(
                    mode="STRICT_INTRADAY_PIT",
                    policy_version=STRICT_INTRADAY_POLICY_VERSION,
                    transformation_version=TRANSFORM,
                ),
            )

    def test_claim_first_objects_exist_without_materialization(self) -> None:
        self.assertIsInstance(self.case.april, HistoricalVintageClaim)
        self.assertFalse(isinstance(self.case.april, ObservationVersionCandidate))
        self.assertEqual(PIT_ENABLED_SOURCES, ())
        self.assertEqual(RESEARCH_ENABLED_SOURCES, ())
        self.assertEqual(BACKTEST_ENABLED_SOURCES, ())
        self.assertTrue(all(value is False for value in SAFETY_FLAGS.values()))

    def test_explicit_pure_materialization_returns_candidate_without_factual_midnight(self) -> None:
        candidate = materialize_observation_version_candidate(
            self.case.april,
            claim=self.case.april_claim,
            binding=self.case.april_binding,
            release=self.case.april_release,
            artifact=self.case.april_artifact,
            all_vintages=self.case.vintages,
            all_artifacts=self.case.artifacts,
            all_bindings=self.case.bindings,
            cutoff_at="2024-04-08T16:00:00Z",
            contract=self.case.day_contract,
        )
        self.assertIsInstance(candidate, ObservationVersionCandidate)
        self.assertIsNone(candidate.source_factual_revision_available_at)
        self.assertTrue(candidate.policy_decision.derived)
        self.assertEqual(candidate.policy_decision.eligible_source_local_date, "2024-04-09")
        self.assertEqual(candidate.policy_decision.derived_comparison_boundary_at, "2024-04-08T16:00:00Z")

    def test_exact_canonical_content_replay_materializes(self) -> None:
        candidate = materialize_observation_version_candidate(
            replace(self.case.april),
            claim=replace(self.case.april_claim),
            binding=replace(self.case.april_binding),
            release=replace(self.case.april_release),
            artifact=replace(self.case.april_artifact),
            all_vintages=self.case.vintages,
            all_artifacts=self.case.artifacts,
            all_bindings=self.case.bindings,
            cutoff_at="2024-04-08T16:00:00Z",
            contract=self.case.day_contract,
        )
        self.assertEqual(candidate.semantic_data["value"], Decimal("8305"))

    def test_valid_record_and_record_version_bindings_materialize(self) -> None:
        validate_subject_binding(
            self.case.april_binding,
            claim=self.case.april_claim,
            release=self.case.april_release,
            artifact=self.case.april_artifact,
        )
        version_binding = replace(
            self.case.april_binding,
            subject_scope="RECORD_VERSION_BOUND",
            observation_id=self.case.april.observation_id,
            historical_vintage_claim_id=self.case.april.vintage_claim_id,
            historical_vintage_content_hash=self.case.april.content_hash,
            target_release_record_key=self.case.april.release_record_key,
        )
        candidate = materialize_observation_version_candidate(
            self.case.april,
            claim=self.case.april_claim,
            binding=version_binding,
            release=self.case.april_release,
            artifact=self.case.april_artifact,
            all_vintages=self.case.vintages,
            all_artifacts=self.case.artifacts,
            all_bindings=(*self.case.bindings, version_binding),
            cutoff_at="2024-04-08T16:00:00Z",
            contract=self.case.day_contract,
        )
        self.assertEqual(candidate.binding_id, version_binding.binding_id)

    def test_strict_exact_timestamp_uses_strict_policy_labels(self) -> None:
        bound = TemporalBound("EXACT", "EXACT_TIMESTAMP", "2024-04-08T12:00:00+08:00")
        decision = evaluate_temporal_eligibility(
            bound,
            cutoff_at="2024-04-08T04:00:00Z",
            mode="STRICT_INTRADAY_PIT",
            policy_version=STRICT_INTRADAY_POLICY_VERSION,
        )
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.mode, "STRICT_INTRADAY_PIT")
        self.assertEqual(decision.policy_version, STRICT_INTRADAY_POLICY_VERSION)
        self.assertFalse(decision.derived)
        self.assertEqual(decision.source_factual_timestamp, "2024-04-08T04:00:00Z")


class EvidenceContractNegativeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = SyntheticCase()

    def _april_version_binding(self) -> EvidenceSubjectBinding:
        return replace(
            self.case.april_binding,
            subject_scope="RECORD_VERSION_BOUND",
            observation_id=self.case.april.observation_id,
            historical_vintage_claim_id=self.case.april.vintage_claim_id,
            historical_vintage_content_hash=self.case.april.content_hash,
            target_release_record_key=self.case.april.release_record_key,
        )

    def test_source_release_binding_does_not_imply_attachment(self) -> None:
        release_claim = replace(
            self.case.march_claim,
            artifact_id=None,
            claim_type="SOURCE_RELEASE_PUBLICATION",
            subject_scope="SOURCE_RELEASE_ONLY",
            claimed_value=None,
            unit=None,
            currency=None,
            safe_locator={},
        )
        release_binding = EvidenceSubjectBinding(
            claim_id=release_claim.claim_id,
            subject_scope="SOURCE_RELEASE_ONLY",
            binding_states=("SOURCE_RELEASE_BOUND",),
            source_release_id=self.case.march_release.source_release_id,
        )
        validate_subject_binding(
            release_binding,
            claim=release_claim,
            release=self.case.march_release,
            artifact=None,
        )
        self.assertIsNone(release_binding.artifact_id)
        with self.assertRaises(EvidenceContractError):
            replace(release_binding, subject_scope="ATTACHMENT_BOUND")

    def test_attachment_binding_does_not_imply_record_binding(self) -> None:
        attachment_binding = EvidenceSubjectBinding(
            claim_id=self.case.march_claim.claim_id,
            subject_scope="ATTACHMENT_BOUND",
            binding_states=(
                "SOURCE_RELEASE_BOUND",
                "OFFICIAL_ATTACHMENT_BOUND",
                "EXACT_ARTIFACT_HASH_BOUND",
            ),
            source_release_id=self.case.march_release.source_release_id,
            artifact_id=self.case.march_artifact.artifact_id,
            expected_artifact_hash=self.case.march_artifact.payload_hash,
        )
        self.assertIsNone(attachment_binding.record_locator)
        self.assertNotIn("OFFICIAL_RECORD_VALUE_BOUND", attachment_binding.binding_states)
        with self.assertRaises(EvidenceContractError):
            replace(attachment_binding, subject_scope="RECORD_BOUND")

    def test_record_binding_does_not_imply_record_version_binding(self) -> None:
        self.assertIsNone(self.case.march_binding.observation_id)
        self.assertIsNone(self.case.march_binding.historical_vintage_claim_id)
        with self.assertRaises(EvidenceContractError):
            replace(self.case.march_binding, subject_scope="RECORD_VERSION_BOUND")

    def test_semantic_value_match_does_not_imply_version_equivalence(self) -> None:
        alternate_release = release("2024_05")
        alternate_artifact = artifact(alternate_release, "2024_05")
        alternate_claim = evidence_claim(
            alternate_release, alternate_artifact, "8305", "2024-05-06"
        )
        alternate_binding = record_binding(alternate_claim, alternate_release, alternate_artifact)
        alternate_vintage = vintage(
            self.case.observation,
            alternate_release,
            alternate_claim,
            alternate_binding,
            "MAY",
            "8305",
            parent=self.case.april.vintage_claim_id,
        )
        self.assertEqual(alternate_vintage.semantic_value, self.case.april.semantic_value)
        self.assertNotEqual(alternate_vintage.vintage_claim_id, self.case.april.vintage_claim_id)
        semantic_only = EvidenceSubjectBinding(
            claim_id=alternate_claim.claim_id,
            subject_scope="UNBOUND",
            binding_states=("SEMANTIC_VALUE_MATCH_ONLY",),
        )
        self.assertNotIn("OFFICIAL_RECORD_VALUE_BOUND", semantic_only.binding_states)
        self.assertIsNone(semantic_only.historical_vintage_claim_id)

        current_2026 = Observation(
            source_id="WORLD_BANK",
            source_record_identifier="WORLD_BANK:PINK_SHEET:COPPER:2026-02",
            metric_id="MONTHLY_PRICE",
            instrument_id="copper_world_bank_monthly",
            source_period_type="MONTH",
            source_market_date=None,
            source_period_start_date="2026-02-01",
            source_period_end_date="2026-02-28",
            calendar_assignments=self.case.observation.calendar_assignments,
            semantic_data={"value": Decimal("8305")},
        )
        self.assertNotEqual(current_2026.observation_id, self.case.april.observation_id)

    def test_same_url_with_contradictory_hashes_fails_closed(self) -> None:
        first = self.case.march_artifact
        second = EvidenceArtifact.from_payload(
            source_release_id=first.source_release_id,
            artifact_role=first.artifact_role,
            media_type=first.media_type,
            official_locator=first.official_locator,
            payload=b"DIFFERENT ARTIFICIAL BYTES",
            source_document_id=first.source_document_id,
        )
        with self.assertRaisesRegex(EvidenceContractError, "contradictory"):
            validate_artifact_consistency((first, second))

    def test_date_only_rejected_in_strict_intraday(self) -> None:
        decision = evaluate_temporal_eligibility(
            temporal("2024-04-08"),
            cutoff_at="2024-04-09T00:00:00Z",
            mode="STRICT_INTRADAY_PIT",
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "EXACT_TIMESTAMP_REQUIRED")

    def test_date_only_conservative_next_day_is_day_mode_only(self) -> None:
        before = evaluate_temporal_eligibility(
            temporal("2024-04-08"),
            cutoff_at="2024-04-08T15:59:59Z",
            mode="DAY_GRANULAR_PIT",
            source_timezone=SYNTHETIC_TIMEZONE,
        )
        after = evaluate_temporal_eligibility(
            temporal("2024-04-08"),
            cutoff_at="2024-04-08T16:00:00Z",
            mode="DAY_GRANULAR_PIT",
            source_timezone=SYNTHETIC_TIMEZONE,
        )
        self.assertFalse(before.eligible)
        self.assertTrue(after.eligible)
        self.assertTrue(after.derived)
        self.assertIsNone(after.source_factual_timestamp)

    def test_unknown_temporal_bound_is_not_eligible(self) -> None:
        bound = TemporalBound("UNKNOWN", "UNKNOWN", None)
        decision = evaluate_temporal_eligibility(
            bound,
            cutoff_at="2024-05-01T00:00:00Z",
            mode="DAY_GRANULAR_PIT",
            source_timezone=SYNTHETIC_TIMEZONE,
        )
        self.assertFalse(decision.eligible)
        self.assertIsNone(bound.normalized_start)

    def test_month_only_is_not_silently_converted_to_date(self) -> None:
        bound = TemporalBound("BY_DATE", "MONTH_ONLY", "2024-04")
        self.assertEqual(bound.normalized_start, "2024-04")
        self.assertNotEqual(bound.normalized_start, "2024-04-01")
        self.assertFalse(
            evaluate_temporal_eligibility(
                bound,
                cutoff_at="2024-05-01T00:00:00Z",
                mode="DAY_GRANULAR_PIT",
                source_timezone=SYNTHETIC_TIMEZONE,
            ).eligible
        )

    def test_missing_offset_rejects_exact_timestamp(self) -> None:
        with self.assertRaises(EvidenceContractError):
            TemporalBound("EXACT", "EXACT_TIMESTAMP", "2024-04-08T12:00:00")

    def test_revision_is_invisible_before_and_visible_after_bound(self) -> None:
        before = select_visible_vintage(
            self.case.vintages,
            cutoff_at="2024-04-08T15:59:59Z",
            mode="DAY_GRANULAR_PIT",
            source_timezone=SYNTHETIC_TIMEZONE,
        )
        after = select_visible_vintage(
            self.case.vintages,
            cutoff_at="2024-04-08T16:00:00Z",
            mode="DAY_GRANULAR_PIT",
            source_timezone=SYNTHETIC_TIMEZONE,
        )
        self.assertEqual(before, self.case.march)
        self.assertEqual(after, self.case.april)

    def test_parent_must_belong_to_same_observation(self) -> None:
        other_parent = replace(self.case.march, observation_id="f" * 64)
        with self.assertRaisesRegex(EvidenceContractError, "another Observation"):
            validate_revision_lineage((other_parent, self.case.april))

    def test_self_parent_is_rejected(self) -> None:
        with self.assertRaisesRegex(EvidenceContractError, "own parent"):
            replace(
                self.case.march,
                parent_vintage_claim_id=self.case.march.vintage_claim_id,
            )

    def test_revision_cycle_is_rejected(self) -> None:
        cyclic_march = replace(
            self.case.march,
            parent_vintage_claim_id=self.case.april.vintage_claim_id,
        )
        with self.assertRaisesRegex(EvidenceContractError, "cycle"):
            validate_revision_lineage((cyclic_march, self.case.april))

    def test_ambiguous_authoritative_candidates_fail_closed(self) -> None:
        alternate = replace(
            self.case.april,
            release_record_key="SYNTHETIC_APRIL_ALTERNATE_COPPER_2024_02",
            semantic_value=Decimal("8306"),
        )
        with self.assertRaisesRegex(EvidenceContractError, "ambiguous"):
            select_visible_vintage(
                (self.case.march, self.case.april, alternate),
                cutoff_at="2024-04-08T16:00:00Z",
                mode="DAY_GRANULAR_PIT",
                source_timezone=SYNTHETIC_TIMEZONE,
            )

    def test_unresolved_record_binding_blocks_materialization(self) -> None:
        attachment_only = EvidenceSubjectBinding(
            claim_id=self.case.april_claim.claim_id,
            subject_scope="ATTACHMENT_BOUND",
            binding_states=("SOURCE_RELEASE_BOUND", "OFFICIAL_ATTACHMENT_BOUND"),
            source_release_id=self.case.april_release.source_release_id,
            artifact_id=self.case.april_artifact.artifact_id,
        )
        with self.assertRaises(EvidenceContractError):
            materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=attachment_only,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=self.case.bindings,
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )

    def test_synthetic_artifact_hash_mismatch_blocks_materialization(self) -> None:
        mismatched = replace(self.case.april_binding, expected_artifact_hash="f" * 64)
        with self.assertRaisesRegex(EvidenceContractError, "hash"):
            materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=mismatched,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=(self.case.march_binding, mismatched),
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )

    def test_policy_derived_boundary_is_not_source_factual_timestamp(self) -> None:
        candidate = materialize_observation_version_candidate(
            self.case.april,
            claim=self.case.april_claim,
            binding=self.case.april_binding,
            release=self.case.april_release,
            artifact=self.case.april_artifact,
            all_vintages=self.case.vintages,
            all_artifacts=self.case.artifacts,
            all_bindings=self.case.bindings,
            cutoff_at="2024-04-08T16:00:00Z",
            contract=self.case.day_contract,
        )
        self.assertIsNone(candidate.source_factual_revision_available_at)
        self.assertIsNone(candidate.policy_decision.source_factual_timestamp)
        self.assertIsNotNone(candidate.policy_decision.derived_comparison_boundary_at)

    def test_materialization_is_replayable_and_has_no_file_side_effect(self) -> None:
        before = (
            self.case.april.content_hash,
            self.case.april_claim.content_hash,
            self.case.april_binding.content_hash,
            self.case.april_artifact.content_hash,
        )
        with patch("builtins.open", side_effect=AssertionError("file I/O is forbidden")):
            first = materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=self.case.april_binding,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=self.case.bindings,
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )
            second = materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=self.case.april_binding,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=self.case.bindings,
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )
        after = (
            self.case.april.content_hash,
            self.case.april_claim.content_hash,
            self.case.april_binding.content_hash,
            self.case.april_artifact.content_hash,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.candidate_hash, second.candidate_hash)
        self.assertEqual(before, after)

    def test_exact_type_validation_rejects_bool_and_float_substitutions(self) -> None:
        with self.assertRaises(EvidenceContractError):
            replace(self.case.march_artifact, byte_size=True)
        with self.assertRaises(EvidenceContractError):
            replace(self.case.march_claim, claimed_value=8300.0)

    def test_same_vintage_id_with_modified_value_fails_content_membership(self) -> None:
        substituted_vintage = replace(self.case.april, semantic_value=Decimal("9999"))
        substituted_claim = replace(self.case.april_claim, claimed_value=Decimal("9999"))
        self.assertEqual(substituted_vintage.vintage_claim_id, self.case.april.vintage_claim_id)
        with self.assertRaisesRegex(EvidenceContractError, "vintage content"):
            materialize_observation_version_candidate(
                substituted_vintage,
                claim=substituted_claim,
                binding=self.case.april_binding,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=self.case.bindings,
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )

    def test_same_vintage_id_with_modified_temporal_bound_fails_content_membership(self) -> None:
        substituted = replace(self.case.april, temporal_bound=temporal("2024-04-09"))
        self.assertEqual(substituted.vintage_claim_id, self.case.april.vintage_claim_id)
        with self.assertRaisesRegex(EvidenceContractError, "vintage content"):
            materialize_observation_version_candidate(
                substituted,
                claim=self.case.april_claim,
                binding=self.case.april_binding,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=self.case.bindings,
                cutoff_at="2024-04-09T16:00:00Z",
                contract=self.case.day_contract,
            )

    def test_same_claim_id_with_modified_content_fails_exact_reference(self) -> None:
        substituted = replace(self.case.april_claim, claimed_value=Decimal("9999"))
        self.assertEqual(substituted.claim_id, self.case.april_claim.claim_id)
        with self.assertRaisesRegex(EvidenceContractError, "claim content"):
            materialize_observation_version_candidate(
                self.case.april,
                claim=substituted,
                binding=self.case.april_binding,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=self.case.bindings,
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )

    def test_same_binding_id_with_modified_content_fails_exact_reference(self) -> None:
        substituted = replace(
            self.case.april_binding,
            binding_states=(*self.case.april_binding.binding_states, "SEMANTIC_VALUE_MATCH_ONLY"),
        )
        self.assertEqual(substituted.binding_id, self.case.april_binding.binding_id)
        with self.assertRaisesRegex(EvidenceContractError, "binding content"):
            materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=substituted,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=self.case.bindings,
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )

    def test_scope_rejects_stronger_binding_facets(self) -> None:
        with self.assertRaisesRegex(EvidenceContractError, "incompatible"):
            EvidenceSubjectBinding(
                claim_id=self.case.march_claim.claim_id,
                subject_scope="SOURCE_RELEASE_ONLY",
                binding_states=("SOURCE_RELEASE_BOUND", "OFFICIAL_RECORD_VALUE_BOUND"),
                source_release_id=self.case.march_release.source_release_id,
            )
        with self.assertRaisesRegex(EvidenceContractError, "attachment binding"):
            EvidenceSubjectBinding(
                claim_id=self.case.march_claim.claim_id,
                subject_scope="ATTACHMENT_BOUND",
                binding_states=("SOURCE_RELEASE_BOUND", "OFFICIAL_ATTACHMENT_BOUND"),
                source_release_id=self.case.march_release.source_release_id,
                artifact_id=self.case.march_artifact.artifact_id,
                observation_id=self.case.observation.observation_id,
                historical_vintage_claim_id=self.case.march.vintage_claim_id,
                historical_vintage_content_hash=self.case.march.content_hash,
                target_release_record_key=self.case.march.release_record_key,
            )

    def test_exact_artifact_hash_facet_does_not_promote_attachment_scope(self) -> None:
        attachment = EvidenceSubjectBinding(
            claim_id=self.case.march_claim.claim_id,
            subject_scope="ATTACHMENT_BOUND",
            binding_states=(
                "SOURCE_RELEASE_BOUND",
                "OFFICIAL_ATTACHMENT_BOUND",
                "EXACT_ARTIFACT_HASH_BOUND",
            ),
            source_release_id=self.case.march_release.source_release_id,
            artifact_id=self.case.march_artifact.artifact_id,
            expected_artifact_hash=self.case.march_artifact.payload_hash,
        )
        self.assertEqual(attachment.subject_scope, "ATTACHMENT_BOUND")
        self.assertIsNone(attachment.record_locator)

    def test_record_version_binding_target_must_match_exact_vintage(self) -> None:
        mismatches = (
            {"historical_vintage_claim_id": self.case.march.vintage_claim_id},
            {"historical_vintage_content_hash": "f" * 64},
            {"observation_id": "f" * 64},
            {"target_release_record_key": "SYNTHETIC_OTHER_RELEASE_RECORD"},
        )
        valid = replace(
            self.case.april_binding,
            subject_scope="RECORD_VERSION_BOUND",
            observation_id=self.case.april.observation_id,
            historical_vintage_claim_id=self.case.april.vintage_claim_id,
            historical_vintage_content_hash=self.case.april.content_hash,
            target_release_record_key=self.case.april.release_record_key,
        )
        for changes in mismatches:
            invalid = replace(valid, **changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(
                EvidenceContractError, "record-version binding target"
            ):
                materialize_observation_version_candidate(
                    self.case.april,
                    claim=self.case.april_claim,
                    binding=invalid,
                    release=self.case.april_release,
                    artifact=self.case.april_artifact,
                    all_vintages=self.case.vintages,
                    all_artifacts=self.case.artifacts,
                    all_bindings=(*self.case.bindings, invalid),
                    cutoff_at="2024-04-08T16:00:00Z",
                    contract=self.case.day_contract,
                )

    def test_record_version_binding_rejects_changed_semantic_facet(self) -> None:
        valid = self._april_version_binding()
        substituted = replace(
            valid,
            binding_states=(*valid.binding_states, "SEMANTIC_VALUE_MATCH_ONLY"),
        )
        self.assertEqual(valid.binding_id, substituted.binding_id)
        self.assertNotEqual(valid.content_hash, substituted.content_hash)
        with self.assertRaisesRegex(EvidenceContractError, "binding content"):
            materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=substituted,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=(*self.case.bindings, valid),
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )

    def test_record_version_binding_rejects_other_changed_content(self) -> None:
        valid = self._april_version_binding()
        substituted = replace(valid, historical_vintage_content_hash="f" * 64)
        self.assertEqual(valid.binding_id, substituted.binding_id)
        self.assertNotEqual(valid.content_hash, substituted.content_hash)
        with self.assertRaisesRegex(EvidenceContractError, "binding content"):
            materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=substituted,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=(*self.case.bindings, valid),
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )

    def test_record_version_binding_exact_reconstruction_and_provenance(self) -> None:
        valid = self._april_version_binding()
        reconstructed = replace(valid)
        candidate = materialize_observation_version_candidate(
            self.case.april,
            claim=self.case.april_claim,
            binding=reconstructed,
            release=self.case.april_release,
            artifact=self.case.april_artifact,
            all_vintages=self.case.vintages,
            all_artifacts=self.case.artifacts,
            all_bindings=(*self.case.bindings, valid),
            cutoff_at="2024-04-08T16:00:00Z",
            contract=self.case.day_contract,
        )
        self.assertEqual(candidate.binding_id, valid.binding_id)
        self.assertEqual(candidate.binding_content_hash, valid.content_hash)
        self.assertEqual(
            candidate.content_projection()["binding_content_hash"],
            valid.content_hash,
        )

    def test_candidate_hash_distinguishes_validated_binding_content(self) -> None:
        original = self._april_version_binding()
        substituted = replace(
            original,
            binding_states=(*original.binding_states, "SEMANTIC_VALUE_MATCH_ONLY"),
        )

        def materialize(binding: EvidenceSubjectBinding) -> ObservationVersionCandidate:
            return materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=binding,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=(*self.case.bindings, binding),
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )

        original_candidate = materialize(original)
        substituted_candidate = materialize(substituted)
        self.assertEqual(original.binding_id, substituted.binding_id)
        self.assertNotEqual(
            original_candidate.binding_content_hash,
            substituted_candidate.binding_content_hash,
        )
        self.assertNotEqual(
            original_candidate.candidate_hash,
            substituted_candidate.candidate_hash,
        )

    def test_record_version_binding_absent_from_collection_fails(self) -> None:
        valid = self._april_version_binding()
        with self.assertRaisesRegex(EvidenceContractError, "binding is absent"):
            materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=valid,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=self.case.bindings,
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )

    def test_duplicate_binding_id_with_conflicting_content_fails(self) -> None:
        valid = self._april_version_binding()
        substituted = replace(
            valid,
            binding_states=(*valid.binding_states, "SEMANTIC_VALUE_MATCH_ONLY"),
        )
        with self.assertRaisesRegex(EvidenceContractError, "contradictory canonical content"):
            materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=valid,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=(*self.case.bindings, valid, substituted),
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )

    def test_record_version_scope_requires_all_target_fields(self) -> None:
        with self.assertRaisesRegex(EvidenceContractError, "explicit observation and vintage IDs"):
            replace(self.case.april_binding, subject_scope="RECORD_VERSION_BOUND")

    def test_retrograde_revision_temporal_bound_fails(self) -> None:
        retrograde = replace(self.case.april, temporal_bound=temporal("2024-03-01"))
        with self.assertRaisesRegex(EvidenceContractError, "strictly later"):
            validate_revision_lineage((self.case.march, retrograde))

    def test_competing_siblings_with_different_boundaries_fail(self) -> None:
        sibling = replace(
            self.case.april,
            release_record_key="SYNTHETIC_APRIL_SIBLING",
            semantic_value=Decimal("8306"),
            temporal_bound=temporal("2024-04-09"),
        )
        with self.assertRaisesRegex(EvidenceContractError, "competing sibling"):
            select_visible_vintage(
                (self.case.march, self.case.april, sibling),
                cutoff_at="2024-04-09T16:00:00Z",
                mode="DAY_GRANULAR_PIT",
                source_timezone=SYNTHETIC_TIMEZONE,
            )

    def test_disconnected_competing_roots_fail(self) -> None:
        disconnected = replace(
            self.case.april,
            release_record_key="SYNTHETIC_DISCONNECTED_APRIL",
            parent_vintage_claim_id=None,
        )
        with self.assertRaisesRegex(EvidenceContractError, "disconnected"):
            validate_revision_lineage((self.case.march, disconnected))

    def test_mode_policy_mismatches_and_unknown_policy_fail(self) -> None:
        cases = (
            ("DAY_GRANULAR_PIT", STRICT_INTRADAY_POLICY_VERSION),
            ("STRICT_INTRADAY_PIT", DAY_GRANULAR_POLICY_VERSION),
            ("DAY_GRANULAR_PIT", "SYNTHETIC_UNKNOWN_POLICY"),
        )
        for mode, policy in cases:
            with self.subTest(mode=mode, policy=policy), self.assertRaisesRegex(
                EvidenceContractError, "incompatible"
            ):
                MaterializationContract(
                    mode=mode,
                    policy_version=policy,
                    transformation_version=TRANSFORM,
                    source_timezone=(SYNTHETIC_TIMEZONE if mode == "DAY_GRANULAR_PIT" else None),
                )

    def test_conflicting_timezone_authorities_fail(self) -> None:
        with self.assertRaisesRegex(EvidenceContractError, "conflicting source timezone"):
            evaluate_temporal_eligibility(
                temporal("2024-04-08"),
                cutoff_at="2024-04-09T04:00:00Z",
                mode="DAY_GRANULAR_PIT",
                source_timezone="America/New_York",
                policy_version=DAY_GRANULAR_POLICY_VERSION,
            )
        self.assertTrue(
            evaluate_temporal_eligibility(
                temporal("2024-04-08"),
                cutoff_at="2024-04-08T16:00:00Z",
                mode="DAY_GRANULAR_PIT",
                source_timezone=SYNTHETIC_TIMEZONE,
                policy_version=DAY_GRANULAR_POLICY_VERSION,
            ).eligible
        )

    def test_artifact_must_be_exact_collection_member(self) -> None:
        with self.assertRaisesRegex(EvidenceContractError, "absent"):
            materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=self.case.april_binding,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=(),
                all_bindings=self.case.bindings,
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )
        different_content = replace(
            self.case.april_artifact,
            retrieved_at="2026-10-08T00:00:00Z",
        )
        self.assertEqual(different_content.artifact_id, self.case.april_artifact.artifact_id)
        with self.assertRaisesRegex(EvidenceContractError, "content"):
            materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=self.case.april_binding,
                release=self.case.april_release,
                artifact=self.case.april_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=(self.case.march_artifact, different_content),
                all_bindings=self.case.bindings,
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )

    def test_claim_artifact_and_supplied_artifact_must_match(self) -> None:
        with self.assertRaisesRegex(EvidenceContractError, "artifact source release mismatch"):
            materialize_observation_version_candidate(
                self.case.april,
                claim=self.case.april_claim,
                binding=self.case.april_binding,
                release=self.case.april_release,
                artifact=self.case.march_artifact,
                all_vintages=self.case.vintages,
                all_artifacts=self.case.artifacts,
                all_bindings=self.case.bindings,
                cutoff_at="2024-04-08T16:00:00Z",
                contract=self.case.day_contract,
            )

    def test_direct_candidate_construction_cannot_bypass_materializer(self) -> None:
        candidate = materialize_observation_version_candidate(
            self.case.april,
            claim=self.case.april_claim,
            binding=self.case.april_binding,
            release=self.case.april_release,
            artifact=self.case.april_artifact,
            all_vintages=self.case.vintages,
            all_artifacts=self.case.artifacts,
            all_bindings=self.case.bindings,
            cutoff_at="2024-04-08T16:00:00Z",
            contract=self.case.day_contract,
        )
        invalid_decision = TemporalEligibilityDecision(
            mode="DAY_GRANULAR_PIT",
            policy_version=DAY_GRANULAR_POLICY_VERSION,
            eligible=True,
            reason="VISIBLE",
            source_factual_timestamp=None,
            eligible_source_local_date=None,
            derived_comparison_boundary_at=None,
            derived=False,
        )
        with self.assertRaisesRegex(EvidenceContractError, "explicit validated materialization"):
            replace(candidate, policy_decision=invalid_decision)

    def test_exact_bound_requires_exact_timestamp_precision(self) -> None:
        with self.assertRaisesRegex(EvidenceContractError, "EXACT_TIMESTAMP"):
            TemporalBound("EXACT", "DATE_ONLY", "2024-04-08")
        with self.assertRaisesRegex(EvidenceContractError, "BY_DATE"):
            TemporalBound("BY_DATE", "EXACT_TIMESTAMP", "2024-04-08T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
