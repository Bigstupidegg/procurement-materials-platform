from __future__ import annotations

import ast
import copy
from dataclasses import FrozenInstanceError, fields, replace
import inspect
import unittest
from unittest.mock import patch

from scripts import c4_rd_preregistration as preregistration
from scripts import c4_rd_preregistration_governance as governance
from scripts.c4_rd_contract import (
    BACKTEST_ENABLED_SOURCES,
    HASH_DOMAINS,
    PIT_ENABLED_SOURCES,
    RD3_OPEN_BLOCKERS,
    RESEARCH_ENABLED_SOURCES,
    SAFETY_FLAGS,
)
from scripts.c4_rd_pit_dataset import build_pit_dataset
from scripts.c4_rd_preregistration import (
    BaselineDefinition,
    ExclusionPolicy,
    ExclusionRule,
    MetricDefinition,
    MetricPlan,
    PreregistrationInvalidProtocol,
    ReportingPolicy,
    build_preregistration_protocol,
)
from scripts.c4_rd_preregistration_governance import (
    ADMINISTRATIVE,
    APPROVED,
    BASELINE,
    DATASET,
    EVALUATION,
    EXCLUSION,
    FEATURE,
    FormalBacktestNotAuthorized,
    InvalidApprovalHistory,
    InvalidApprovalRecord,
    InvalidChangeRecord,
    InvalidChangeSet,
    LABEL,
    METRIC,
    PENDING,
    REJECTED,
    REPORTING,
    RESEARCH_QUESTION,
    SUPERSEDED,
    SYNTHETIC_FIXTURE,
    SYNTHETIC_NON_OPERATIONAL,
    PreregistrationApprovalRecord,
    PreregistrationChangeRecord,
    build_operational_preregistration_approval_record,
    build_preregistration_change_record,
    build_synthetic_preregistration_approval_record,
    require_formal_backtest_authorization,
    validate_preregistration_approval_history,
    validate_preregistration_change_set,
)
from tests.test_c4_rd_pit_dataset import candidate, feature_definition, request
from tests.test_c4_rd_preregistration import QUESTION, build_protocol, horizon_request, synthetic_results


T0 = "2026-09-24T04:00:00Z"
T1 = "2026-09-24T05:00:00Z"
T2 = "2026-09-24T06:00:00Z"
T3 = "2026-09-24T07:00:00Z"


class TestC4RDPreregistrationGovernance(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.results = synthetic_results()

    def protocol(self, *, version="1.0.0", question=None, protocol_id=None):
        return build_protocol(
            self.results,
            version=version,
            question=QUESTION if question is None else question,
            protocol_id=(
                "C4-RD-7-SYNTHETIC-DIRECTION" if protocol_id is None else protocol_id
            ),
        ).protocol

    def approval(self, state, *, protocol=None, recorded_at=T2, **changes):
        values = {
            "protocol": self.protocol() if protocol is None else protocol,
            "approval_state": state,
            "recorded_at": recorded_at,
        }
        if state == APPROVED:
            values.update(approved_at=T1, fixture_evidence_reference="fixture://approval")
        elif state == REJECTED:
            values.update(
                fixture_evidence_reference="fixture://rejection",
                rejection_reason="Human reviewer rejected the protocol.",
            )
        values.update(changes)
        return build_synthetic_preregistration_approval_record(**values)

    def superseded(self, *, old=None, new=None, recorded_at=T3, **changes):
        old = self.protocol() if old is None else old
        new = (
            self.protocol(
                version="1.0.1",
                question={**QUESTION, "objective": "Revised synthetic objective."},
            )
            if new is None
            else new
        )
        values = {
            "protocol": old,
            "approval_state": SUPERSEDED,
            "approved_at": T1,
            "fixture_evidence_reference": "fixture://approval",
            "superseded_by_protocol": new,
            "superseded_at": T2,
            "supersession_fixture_evidence_reference": "fixture://supersession",
            "recorded_at": recorded_at,
        }
        values.update(changes)
        return build_synthetic_preregistration_approval_record(**values)

    def changed_question(self, *, old_version="1.0.0", new_version="1.0.1"):
        old = self.protocol(version=old_version)
        new = self.protocol(
            version=new_version,
            question={**QUESTION, "objective": "Changed synthetic objective."},
        )
        return old, new

    def change_record(self, old, new, classification, *, reason=None, recorded_at=T2):
        return build_preregistration_change_record(
            previous_protocol=old,
            new_protocol=new,
            change_classification=classification,
            change_reason=(f"Synthetic {classification} fixture." if reason is None else reason),
            recorded_at=recorded_at,
        )

    def test_frozen_enumerations_and_governance_version_are_exact(self):
        self.assertEqual(governance.GOVERNANCE_CONTRACT_VERSION, "1.0.0")
        self.assertEqual(governance.APPROVAL_STATES, (PENDING, APPROVED, REJECTED, SUPERSEDED))
        self.assertEqual(
            governance.OPERATIONAL_STATUSES,
            (SYNTHETIC_NON_OPERATIONAL, "OPERATIONAL_VERIFIED"),
        )
        self.assertEqual(
            governance.APPROVAL_EVIDENCE_TYPES,
            ("GITHUB_PR_MERGE", "GITHUB_PR_CLOSED_UNMERGED", SYNTHETIC_FIXTURE),
        )
        self.assertEqual(
            governance.CHANGE_CLASSIFICATIONS,
            (
                RESEARCH_QUESTION,
                DATASET,
                FEATURE,
                LABEL,
                BASELINE,
                METRIC,
                EVALUATION,
                EXCLUSION,
                REPORTING,
                ADMINISTRATIVE,
            ),
        )

    def test_authority_records_reject_ordinary_direct_construction(self):
        pending = self.approval(PENDING)
        approval_values = {item.name: getattr(pending, item.name) for item in fields(pending)}
        with self.assertRaises(InvalidApprovalRecord):
            PreregistrationApprovalRecord(**approval_values)

        old, new = self.changed_question()
        change = self.change_record(old, new, RESEARCH_QUESTION)
        change_values = {item.name: getattr(change, item.name) for item in fields(change)}
        with self.assertRaises(InvalidChangeRecord):
            PreregistrationChangeRecord(**change_values)

    def test_construction_token_is_not_stored_or_semantic(self):
        for record_type in (PreregistrationApprovalRecord, PreregistrationChangeRecord):
            self.assertNotIn("_construction_token", {item.name for item in fields(record_type)})
            self.assertNotIn("_construction_token", record_type.__slots__)
        self.assertFalse(hasattr(self.approval(PENDING), "__dict__"))

    def test_records_are_frozen_and_slotted(self):
        approval = self.approval(PENDING)
        old, new = self.changed_question()
        change = self.change_record(old, new, RESEARCH_QUESTION)
        with self.assertRaises(FrozenInstanceError):
            approval.approval_state = APPROVED
        with self.assertRaises(FrozenInstanceError):
            change.change_reason = "mutated"
        self.assertFalse(hasattr(change, "__dict__"))

    def test_protocol_binding_is_derived_from_exact_trusted_protocol(self):
        protocol = self.protocol()
        record = self.approval(PENDING, protocol=protocol)
        self.assertEqual(record.preregistration_identity, protocol.preregistration_identity)
        self.assertEqual(record.preregistration_version, protocol.preregistration_version)
        self.assertEqual(record.approved_protocol_hash, protocol.preregistration_identity)
        for substitute in (
            protocol.preregistration_identity,
            {"preregistration_identity": protocol.preregistration_identity},
            object(),
        ):
            with self.subTest(substitute=type(substitute).__name__):
                with self.assertRaises(InvalidApprovalRecord):
                    self.approval(PENDING, protocol=substitute)

    def test_pending_field_matrix_and_backtest_gate(self):
        record = self.approval(PENDING)
        self.assertEqual(record.approval_state, PENDING)
        self.assertEqual(record.operational_status, SYNTHETIC_NON_OPERATIONAL)
        for name in (
            "approved_at",
            "approval_evidence_type",
            "approval_evidence_reference",
            "merge_commit_sha",
            "rejection_reason",
            "superseded_by_preregistration_identity",
            "superseded_at",
            "supersession_evidence_type",
            "supersession_evidence_reference",
            "supersession_merge_commit_sha",
        ):
            self.assertIsNone(getattr(record, name))
        with self.assertRaises(FormalBacktestNotAuthorized):
            require_formal_backtest_authorization(
                approval_record=record, backtest_started_at=T3
            )

    def test_pending_rejects_forbidden_state_fields(self):
        for changes in (
            {"approved_at": T1},
            {"fixture_evidence_reference": "fixture://unexpected"},
            {"rejection_reason": "unexpected"},
            {"superseded_at": T1},
            {"supersession_fixture_evidence_reference": "fixture://unexpected"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(InvalidApprovalRecord):
                    self.approval(PENDING, **changes)

    def test_synthetic_approved_field_matrix_and_ordering(self):
        record = self.approval(APPROVED)
        self.assertEqual(record.approved_at, T1)
        self.assertEqual(record.approval_evidence_type, SYNTHETIC_FIXTURE)
        self.assertEqual(record.approval_evidence_reference, "fixture://approval")
        self.assertIsNone(record.merge_commit_sha)
        self.assertIsNone(record.rejection_reason)
        self.assertEqual(record.operational_status, SYNTHETIC_NON_OPERATIONAL)
        with self.assertRaises(InvalidApprovalRecord):
            self.approval(APPROVED, recorded_at=T0)
        with self.assertRaises(FormalBacktestNotAuthorized):
            require_formal_backtest_authorization(
                approval_record=record, backtest_started_at=T3
            )

    def test_synthetic_approved_requires_time_and_evidence(self):
        for changes in (
            {"approved_at": None},
            {"fixture_evidence_reference": None},
            {"fixture_evidence_reference": " "},
            {"rejection_reason": "not allowed"},
            {"superseded_at": T2},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(InvalidApprovalRecord):
                    self.approval(APPROVED, **changes)

    def test_rejected_field_matrix_reason_and_terminal_history(self):
        record = self.approval(REJECTED)
        self.assertIsNone(record.approved_at)
        self.assertEqual(record.approval_evidence_type, SYNTHETIC_FIXTURE)
        self.assertTrue(record.rejection_reason)
        validate_preregistration_approval_history([record])
        with self.assertRaises(InvalidApprovalRecord):
            self.approval(REJECTED, rejection_reason=" ")
        with self.assertRaises(InvalidApprovalHistory):
            validate_preregistration_approval_history(
                [record, self.approval(APPROVED, recorded_at=T3)]
            )

    def test_superseded_field_matrix_family_version_and_ordering(self):
        old = self.protocol()
        new = self.protocol(
            version="1.0.1",
            question={**QUESTION, "objective": "Revised synthetic objective."},
        )
        record = self.superseded(old=old, new=new)
        self.assertEqual(record.preregistration_identity, old.preregistration_identity)
        self.assertEqual(
            record.superseded_by_preregistration_identity,
            new.preregistration_identity,
        )
        self.assertEqual(record.supersession_evidence_type, SYNTHETIC_FIXTURE)
        self.assertIsNone(record.supersession_merge_commit_sha)
        for changes in (
            {"superseded_at": T1},
            {"recorded_at": T1},
            {"supersession_fixture_evidence_reference": None},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(InvalidApprovalRecord):
                    self.superseded(old=old, new=new, **changes)
        with self.assertRaises(FormalBacktestNotAuthorized):
            require_formal_backtest_authorization(
                approval_record=record, backtest_started_at="2026-09-24T05:30:00Z"
            )

    def test_supersession_rejects_same_family_identity_or_non_newer_version(self):
        old = self.protocol()
        cases = (
            old,
            self.protocol(
                version="1.0.1",
                protocol_id="OTHER-PROTOCOL-FAMILY",
                question={**QUESTION, "objective": "Other family."},
            ),
            self.protocol(
                version="1.0.0+build.2",
                question={**QUESTION, "objective": "Equal precedence."},
            ),
        )
        for new in cases:
            with self.subTest(version=new.preregistration_version):
                with self.assertRaises(InvalidApprovalRecord):
                    self.superseded(old=old, new=new)

    def test_every_legal_approval_sequence_passes(self):
        old = self.protocol()
        pending = self.approval(PENDING, protocol=old, recorded_at=T0)
        approved = self.approval(APPROVED, protocol=old, recorded_at=T2)
        rejected = self.approval(REJECTED, protocol=old, recorded_at=T2)
        superseded = self.superseded(old=old, recorded_at=T3)
        for records in (
            [pending],
            [pending, approved],
            [pending, rejected],
            [pending, approved, superseded],
            [approved],
            [approved, superseded],
            [rejected],
        ):
            with self.subTest(states=[item.approval_state for item in records]):
                validate_preregistration_approval_history(records)

    def test_illegal_approval_sequences_and_duplicates_fail(self):
        pending = self.approval(PENDING, recorded_at=T0)
        approved = self.approval(APPROVED, recorded_at=T1)
        rejected = self.approval(REJECTED, recorded_at=T1)
        superseded = self.superseded(recorded_at=T2)
        illegal = (
            [pending, pending],
            [approved, approved],
            [rejected, rejected],
            [superseded, superseded],
            [rejected, approved],
            [approved, rejected],
            [superseded, approved],
            [pending, superseded],
        )
        for records in illegal:
            with self.subTest(states=[item.approval_state for item in records]):
                with self.assertRaises(InvalidApprovalHistory):
                    validate_preregistration_approval_history(records)

    def test_approval_history_requires_nonempty_exact_ordered_container(self):
        for records in ([], (), {self.approval(PENDING)}, "not-records"):
            with self.subTest(records_type=type(records).__name__):
                with self.assertRaises(InvalidApprovalHistory):
                    validate_preregistration_approval_history(records)
        with self.assertRaises(InvalidApprovalHistory):
            validate_preregistration_approval_history([self.approval(PENDING), object()])

    def test_approval_history_requires_strict_recorded_order(self):
        pending = self.approval(PENDING, recorded_at=T1)
        approved = self.approval(APPROVED, recorded_at=T1)
        with self.assertRaises(InvalidApprovalHistory):
            validate_preregistration_approval_history([pending, approved])

    def test_approval_history_rejects_binding_mismatch_and_evidence_rewrite(self):
        old = self.protocol()
        approved = self.approval(APPROVED, protocol=old, recorded_at=T2)
        superseded = self.superseded(old=old, recorded_at=T3)
        rewritten = copy.copy(superseded)
        object.__setattr__(rewritten, "approval_evidence_reference", "fixture://rewritten")
        with self.assertRaises(InvalidApprovalHistory):
            validate_preregistration_approval_history([approved, rewritten])

        new = self.protocol(
            version="1.0.1",
            question={**QUESTION, "objective": "Different binding."},
        )
        with self.assertRaises(InvalidApprovalHistory):
            validate_preregistration_approval_history(
                [self.approval(PENDING, protocol=old, recorded_at=T0), self.approval(APPROVED, protocol=new)]
            )

    def test_timestamp_contract_accepts_only_canonical_utc_z(self):
        self.approval(PENDING, recorded_at="2026-09-24T05:00:00.123456Z")
        for value in (
            "2026-09-24T13:00:00+08:00",
            "2026-09-24T05:00:00",
            "2026-09-24T05:00:00.100000Z",
            None,
        ):
            with self.subTest(value=value):
                with self.assertRaises(InvalidApprovalRecord):
                    self.approval(PENDING, recorded_at=value)

    def test_timestamp_ordering_uses_parsed_instants(self):
        pending = self.approval(PENDING, recorded_at="2026-09-24T05:00:00.9Z")
        approved = self.approval(
            APPROVED,
            approved_at=T0,
            recorded_at="2026-09-24T05:00:00Z",
        )
        with self.assertRaises(InvalidApprovalHistory):
            validate_preregistration_approval_history([pending, approved])

    def test_change_record_derives_exact_protocol_pair(self):
        old, new = self.changed_question()
        record = self.change_record(old, new, RESEARCH_QUESTION)
        self.assertEqual(record.research_protocol_id, old.research_protocol_id)
        self.assertEqual(record.previous_preregistration_identity, old.preregistration_identity)
        self.assertEqual(record.new_preregistration_identity, new.preregistration_identity)
        self.assertEqual(record.previous_preregistration_version, old.preregistration_version)
        self.assertEqual(record.new_preregistration_version, new.preregistration_version)
        for substitute in (old.preregistration_identity, {}, object()):
            with self.subTest(substitute=type(substitute).__name__):
                with self.assertRaises(InvalidChangeRecord):
                    self.change_record(substitute, new, RESEARCH_QUESTION)

    def test_change_record_requires_reason_and_truthful_classification(self):
        old, new = self.changed_question()
        for reason in (None, "", " "):
            if reason is None:
                continue
            with self.subTest(reason=reason):
                with self.assertRaises(InvalidChangeRecord):
                    self.change_record(old, new, RESEARCH_QUESTION, reason=reason)
        with self.assertRaises(InvalidChangeRecord):
            self.change_record(old, new, METRIC)

    def test_change_rejects_family_and_contract_version_switch(self):
        old = self.protocol()
        family_new = self.protocol(
            version="1.0.1",
            protocol_id="OTHER-FAMILY",
            question={**QUESTION, "objective": "Changed."},
        )
        with self.assertRaises(InvalidChangeRecord):
            self.change_record(old, family_new, RESEARCH_QUESTION)

        with patch.object(preregistration, "CONTRACT_VERSION", "2.0.0"):
            contract_new = self.protocol(
                version="1.0.1",
                question={**QUESTION, "objective": "Changed contract."},
            )
        with self.assertRaises(InvalidChangeRecord):
            self.change_record(old, contract_new, RESEARCH_QUESTION)

    def test_semantic_change_requires_newer_version_and_rejects_version_only(self):
        old = self.protocol()
        same_version_changed = self.protocol(
            question={**QUESTION, "objective": "Changed without version bump."}
        )
        with self.assertRaises(InvalidChangeRecord):
            self.change_record(old, same_version_changed, RESEARCH_QUESTION)

        version_only = self.protocol(version="1.0.1")
        for classification in (RESEARCH_QUESTION, ADMINISTRATIVE):
            with self.subTest(classification=classification):
                with self.assertRaises(InvalidChangeRecord):
                    self.change_record(old, version_only, classification)

    def test_semver_precedence_prerelease_normal_build_metadata_and_invalid(self):
        old, new = self.changed_question(old_version="1.0.0-alpha", new_version="1.0.0")
        self.change_record(old, new, RESEARCH_QUESTION)
        old, new = self.changed_question(old_version="1.0.0", new_version="1.1.0")
        self.change_record(old, new, RESEARCH_QUESTION)
        old, new = self.changed_question(old_version="1.0.0+abc", new_version="1.0.0+xyz")
        with self.assertRaises(InvalidChangeRecord):
            self.change_record(old, new, RESEARCH_QUESTION)
        with self.assertRaises(InvalidChangeRecord):
            governance._compare_semver("1.0.0-01", "1.0.0", InvalidChangeRecord)
        with self.assertRaises(PreregistrationInvalidProtocol):
            self.protocol(version="invalid")

    def test_administrative_record_requires_same_unchanged_protocol(self):
        protocol = self.protocol()
        record = self.change_record(protocol, protocol, ADMINISTRATIVE)
        validate_preregistration_change_set(
            previous_protocol=protocol,
            new_protocol=protocol,
            records=[record],
        )
        old, new = self.changed_question()
        with self.assertRaises(InvalidChangeRecord):
            self.change_record(old, new, ADMINISTRATIVE)

    def test_research_question_classification_mapping(self):
        old, new = self.changed_question()
        record = self.change_record(old, new, RESEARCH_QUESTION)
        validate_preregistration_change_set(
            previous_protocol=old, new_protocol=new, records=[record]
        )

    def test_dataset_classification_mapping(self):
        old = self.protocol()
        source = candidate(record="V2")
        results = {
            horizon: build_pit_dataset(
                (
                    horizon_request(
                        horizon,
                        target_version_id=source.observation_version.observation_version_id,
                    ),
                ),
                (source,),
            )
            for horizon in preregistration.HORIZONS
        }
        new = build_protocol(results, version="1.0.1").protocol
        record = self.change_record(old, new, DATASET)
        validate_preregistration_change_set(
            previous_protocol=old, new_protocol=new, records=[record]
        )

    def test_feature_and_label_classification_mappings(self):
        old = self.protocol()
        source = candidate()
        definitions = (
            feature_definition("synthetic_price", value_key="feature_value"),
            feature_definition("synthetic_optional", requirement="OPTIONAL", value_key="unused"),
        )
        feature_results = {
            horizon: build_pit_dataset(
                (horizon_request(horizon, definitions=definitions),), (source,)
            )
            for horizon in preregistration.HORIZONS
        }
        feature_new = build_protocol(feature_results, version="1.0.1").protocol
        feature_records = [
            self.change_record(old, feature_new, classification)
            for classification in (DATASET, FEATURE)
        ]
        validate_preregistration_change_set(
            previous_protocol=old, new_protocol=feature_new, records=feature_records
        )

        label_results = {
            horizon: build_pit_dataset(
                (
                    request(
                        label_specification={
                            "label_definition_id": "SYNTHETIC_DIRECTION_V2",
                            "label_horizon": horizon,
                            "label_reference_id": f"SYNTHETIC_{horizon}_REFERENCE_V2",
                        },
                    ),
                ),
                (source,),
            )
            for horizon in preregistration.HORIZONS
        }
        label_new = build_protocol(label_results, version="1.0.1").protocol
        label_records = [
            self.change_record(old, label_new, classification)
            for classification in (DATASET, LABEL)
        ]
        validate_preregistration_change_set(
            previous_protocol=old, new_protocol=label_new, records=label_records
        )

    def test_fixed_plan_classification_mappings_through_real_builder(self):
        old = self.protocol()
        variants = []

        baselines = old.baseline_plan + (
            BaselineDefinition("synthetic_extra", "1.0.0", "NONE", "NONE", "NONE", "NONE"),
        )
        variants.append(("_fixed_baselines", baselines, BASELINE))

        metric = MetricPlan(
            old.metric_plan.primary,
            old.metric_plan.secondary,
            old.metric_plan.diagnostic + (MetricDefinition("synthetic_diagnostic", "1.0.0"),),
        )
        variants.append(("_fixed_metric_plan", metric, METRIC))

        evaluation = replace(
            old.evaluation_protocol,
            primary_total_min=old.evaluation_protocol.primary_total_min + 1,
        )
        variants.append(("_fixed_evaluation_protocol", evaluation, EVALUATION))

        exclusion = ExclusionPolicy(
            old.exclusion_policy.rules + (ExclusionRule("SYNTHETIC_RULE", "1.0.0", "KEEP"),),
            old.exclusion_policy.forbidden_substitutions,
        )
        variants.append(("_fixed_exclusion_policy", exclusion, EXCLUSION))

        reporting = ReportingPolicy(
            old.reporting_policy.mandatory_reporting_fields + ("synthetic_field",),
            old.reporting_policy.null_result_policy,
            old.reporting_policy.failure_result_policy,
        )
        variants.append(("_fixed_reporting_policy", reporting, REPORTING))

        for factory_name, value, classification in variants:
            with self.subTest(classification=classification):
                with patch.object(preregistration, factory_name, return_value=value):
                    new = self.protocol(version="1.0.1")
                record = self.change_record(old, new, classification)
                validate_preregistration_change_set(
                    previous_protocol=old, new_protocol=new, records=[record]
                )

    def test_multi_classification_exact_complete_change_set(self):
        old = self.protocol()
        metric = MetricPlan(
            old.metric_plan.primary,
            old.metric_plan.secondary,
            old.metric_plan.diagnostic + (MetricDefinition("synthetic_diagnostic", "1.0.0"),),
        )
        with patch.object(preregistration, "_fixed_metric_plan", return_value=metric):
            new = self.protocol(
                version="1.0.1",
                question={**QUESTION, "objective": "Changed with metrics."},
            )
        records = [
            self.change_record(old, new, RESEARCH_QUESTION),
            self.change_record(old, new, METRIC),
        ]
        validate_preregistration_change_set(
            previous_protocol=old, new_protocol=new, records=records
        )

        with self.assertRaises(InvalidChangeSet):
            validate_preregistration_change_set(
                previous_protocol=old, new_protocol=new, records=records[:1]
            )
        with self.assertRaises(InvalidChangeSet):
            validate_preregistration_change_set(
                previous_protocol=old, new_protocol=new, records=records + [records[0]]
            )

        extra = copy.copy(records[0])
        object.__setattr__(extra, "change_classification", DATASET)
        with self.assertRaises(InvalidChangeSet):
            validate_preregistration_change_set(
                previous_protocol=old, new_protocol=new, records=records + [extra]
            )

        administrative = copy.copy(records[0])
        object.__setattr__(administrative, "change_classification", ADMINISTRATIVE)
        with self.assertRaises(InvalidChangeSet):
            validate_preregistration_change_set(
                previous_protocol=old, new_protocol=new, records=records + [administrative]
            )

    def test_change_set_rejects_wrong_binding_container_and_types(self):
        old, new = self.changed_question()
        record = self.change_record(old, new, RESEARCH_QUESTION)
        other_old = self.protocol(version="1.0.0-alpha")
        for records in ({record}, "records", [object()]):
            with self.subTest(records_type=type(records).__name__):
                with self.assertRaises(InvalidChangeSet):
                    validate_preregistration_change_set(
                        previous_protocol=old, new_protocol=new, records=records
                    )
        with self.assertRaises(InvalidChangeSet):
            validate_preregistration_change_set(
                previous_protocol=other_old, new_protocol=new, records=[record]
            )

    def test_formal_backtest_gate_rejects_every_publicly_constructible_state(self):
        records = (
            self.approval(PENDING),
            self.approval(APPROVED),
            self.approval(REJECTED),
            self.superseded(),
        )
        for record in records:
            with self.subTest(state=record.approval_state):
                with self.assertRaises(FormalBacktestNotAuthorized):
                    require_formal_backtest_authorization(
                        approval_record=record, backtest_started_at=T3
                    )
        with self.assertRaises(FormalBacktestNotAuthorized):
            require_formal_backtest_authorization(
                approval_record=object(), backtest_started_at=T3
            )

    def test_public_builders_do_not_accept_caller_supplied_authority_fields(self):
        signature = inspect.signature(build_synthetic_preregistration_approval_record)
        forbidden_parameters = {
            "operational_status",
            "approval_evidence_type",
            "merge_commit_sha",
            "supersession_evidence_type",
            "supersession_merge_commit_sha",
            "preregistration_identity",
            "approved_protocol_hash",
        }
        self.assertTrue(forbidden_parameters.isdisjoint(signature.parameters))
        functions = {
            name
            for name, value in vars(governance).items()
            if inspect.isfunction(value)
            and value.__module__ == governance.__name__
            and not name.startswith("_")
        }
        self.assertEqual(
            functions,
            {
                "build_synthetic_preregistration_approval_record",
                "build_operational_preregistration_approval_record",
                "validate_preregistration_approval_history",
                "build_preregistration_change_record",
                "validate_preregistration_change_set",
                "require_formal_backtest_authorization",
            },
        )
        operational_signature = inspect.signature(
            build_operational_preregistration_approval_record
        )
        self.assertEqual(
            set(operational_signature.parameters),
            {"protocol", "verified_evidence", "recorded_at"},
        )

    def test_no_new_hash_domains_and_safety_boundary_unchanged(self):
        self.assertEqual(len(HASH_DOMAINS), 13)
        self.assertIn("PREREGISTRATION_CONTENT", HASH_DOMAINS)
        self.assertTrue(all(
            "APPROVAL" not in domain and "CHANGE" not in domain and "GOVERNANCE" not in domain
            for domain in HASH_DOMAINS
        ))
        self.assertEqual(len(SAFETY_FLAGS), 9)
        self.assertTrue(all(value is False for value in SAFETY_FLAGS.values()))
        self.assertEqual(PIT_ENABLED_SOURCES, ())
        self.assertEqual(RESEARCH_ENABLED_SOURCES, ())
        self.assertEqual(BACKTEST_ENABLED_SOURCES, ())
        self.assertEqual(len(RD3_OPEN_BLOCKERS), 15)

    def test_production_module_is_pure_and_has_no_forbidden_dependencies(self):
        tree = ast.parse(inspect.getsource(governance))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        forbidden_imports = {
            "os",
            "pathlib",
            "requests",
            "urllib",
            "http",
            "subprocess",
            "socket",
            "google",
            "sqlite3",
            "time",
            "datetime",
            "random",
            "secrets",
            "uuid",
            "tempfile",
            "pickle",
            "joblib",
            "sklearn",
            "tensorflow",
            "torch",
        }
        self.assertTrue(imports.isdisjoint(forbidden_imports))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue({"open", "now", "utcnow"}.isdisjoint(calls))
        private_imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name.startswith("_")
        }
        self.assertEqual(private_imports, set())


if __name__ == "__main__":
    unittest.main()
