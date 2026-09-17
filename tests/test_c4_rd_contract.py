from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
import hashlib
import inspect
import unittest
from collections import deque
from types import MappingProxyType

import scripts.c4_rd_contract as contract_module

from scripts.c4_rd_contract import (
    AVAILABILITY_BASES, BACKTEST_ENABLED_SOURCES, CALENDAR_ROLES, CALENDAR_ROLE_STATUSES,
    CANONICAL_JSON_PROFILE, ELIGIBILITY_STATES, EXCLUSION_REASON_CODES, HASH_DOMAINS,
    HASH_PROFILE, PIT_ENABLED_SOURCES, QUALITY_STATUSES, RAW_BYTES_HASH_PROFILE,
    RD3_OPEN_BLOCKERS, READINESS_STATES, RESEARCH_ENABLED_SOURCES, SAFETY_FLAGS,
    SOURCE_PERIOD_TYPES, STALE_REASON_ALIASES, TRUST_STATES, AvailabilityAssessment,
    Assessment, BacktestDatasetEligibilityAssessment, BacktestRecordEligibilityAssessment,
    CalendarAssignment, CalendarReference, ContractError, ExclusionReason, ImmutableMapping,
    LineageAssessment, Observation, ObservationVersion, PITEligibilityAssessment,
    ReadinessManifest, ResearchEligibilityAssessment, RuleBundle, TrustQualityAssessment,
    canonical_hash, canonical_json_bytes, canonical_timestamp, normalize_calendar_assignments,
    normalize_exclusion_reasons, model_field_names,
    parse_json_strict, parse_rfc3339, raw_payload_hash, validate_pit_structure,
    validate_source_period,
)


H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
EXPECTED_AUTHORIZATION_FIELDS = (
    "real_data_ingestion_allowed", "shadow_export_allowed", "research_dataset_allowed",
    "backtest_allowed", "production_allowed", "canonical_allowed", "deferred_allowed",
    "ml_allowed", "procurement_allowed",
)
EXPECTED_RD3_BLOCKERS = (
    "RD3-LME-001", "RD3-LME-002", "RD3-LME-003",
    "RD3-SMM-001", "RD3-SMM-002", "RD3-SMM-003", "RD3-SMM-004",
    "RD3-YAHOO-001", "RD3-YAHOO-002", "RD3-YAHOO-003", "RD3-YAHOO-004",
    "RD3-BZ-001", "RD3-WB-001", "RD3-WB-002", "RD3-WB-003",
)


def assignments(subject: str = "synthetic-subject") -> tuple[CalendarAssignment, ...]:
    return tuple(
        CalendarAssignment(subject, role, "NOT_APPLICABLE", None, None, None)
        for role in CALENDAR_ROLES
    )


def observation(**changes):
    values = {
        "source_id": "SYNTHETIC_TEST_SOURCE", "source_record_identifier": "SYNTHETIC_RECORD_001",
        "metric_id": "SYNTHETIC_PRICE", "instrument_id": "SYNTHETIC_COPPER",
        "source_period_type": "DAILY_MARKET", "source_market_date": "2026-01-02",
        "source_period_start_date": None, "source_period_end_date": None,
        "calendar_assignments": assignments(), "collected_at": "2026-01-03T00:00:00Z",
        "created_at": "2026-01-03T00:00:01Z", "semantic_data": {"value": Decimal("12.50")},
    }
    values.update(changes)
    return Observation(**values)


def version(**changes):
    values = {
        "observation_id": H1, "source_version_or_release_key": "SYNTHETIC_RELEASE_001",
        "stable_version_key": "SYNTHETIC_VERSION_001", "raw_payload_hash": H2,
        "transformation_version": "synthetic-transform@1.0.0", "parent_version_id": None,
        "revision_available_at": "2026-01-03T00:00:00Z", "created_at": "2026-01-03T00:00:01Z",
        "semantic_data": {"value": 12},
    }
    values.update(changes)
    return ObservationVersion(**values)


def manifest(**changes):
    values = {
        "manifest_kind": "SYNTHETIC_READINESS", "semantic_subject_scope_hash": H1,
        "evaluation_as_of_at": "2026-01-04T00:00:00Z", "contract_version": "1.0.0",
        "rule_bundle_version": "rules@1.0.0", "entries": (),
        "authorizations": {name: False for name in EXPECTED_AUTHORIZATION_FIELDS},
        "blocker_ids": EXPECTED_RD3_BLOCKERS,
    }
    values.update(changes)
    return ReadinessManifest(**values)


def assessment_values(**changes):
    values = {
        "subject_id": H1, "assessment_role": "SOURCE",
        "evaluation_at": "2026-01-04T00:00:00Z", "rule_bundle_version": "rules@1.0.0",
        "result": {"eligible": False}, "evidence": {"synthetic": True},
    }
    values.update(changes)
    return values


def exclusion_reasons() -> tuple[ExclusionReason, ...]:
    return tuple(
        ExclusionReason(code, "BLOCK", "RECORD", f"Synthetic {index}", "Synthetic remediation", index)
        for index, code in enumerate(EXCLUSION_REASON_CODES)
    )


def semantic_fingerprint(item):
    values = [item.content_projection()]
    for name in (
        "observation_id", "observation_version_id", "assessment_id", "calendar_hash",
        "manifest_id", "content_hash",
    ):
        if hasattr(item, name):
            values.append(getattr(item, name))
    if hasattr(item, "identity_projection"):
        values.append(item.identity_projection())
    return tuple(values)


class FrozenContractTests(unittest.TestCase):
    def test_frozen_enum_exactness(self):
        self.assertEqual(TRUST_STATES, ("REAL_ORIGIN_UNVERIFIED", "REAL_ORIGIN_VERIFIED", "SHADOW_UNRESOLVED", "LEGACY_UNVERIFIED", "TRUST_REJECTED"))
        self.assertEqual(QUALITY_STATUSES, ("NOT_ASSESSED", "PASS", "WARNING", "FAIL", "CONFLICT"))
        self.assertEqual(READINESS_STATES[-2:], ("QUARANTINED", "REJECTED"))
        self.assertEqual(ELIGIBILITY_STATES, ("NOT_EVALUATED", "ELIGIBLE", "INELIGIBLE"))
        self.assertEqual(SOURCE_PERIOD_TYPES, ("DAILY_MARKET", "SNAPSHOT", "MONTH", "QUARTER", "OTHER_BOUNDED_PERIOD"))
        self.assertEqual(CALENDAR_ROLE_STATUSES, ("RESOLVED", "NOT_APPLICABLE", "UNVERIFIED", "CONFLICTING", "OUT_OF_RANGE"))
        self.assertEqual(len(AVAILABILITY_BASES), 6)

    def test_stale_aliases_are_not_catalog_codes(self):
        self.assertTrue(STALE_REASON_ALIASES.isdisjoint(EXCLUSION_REASON_CODES))
        with self.assertRaises(ContractError):
            ExclusionReason("INVALID_VALUE", "BLOCK", "RECORD", "old", "none", 1)

    def test_exact_reason_catalog_and_rd3_blockers(self):
        self.assertEqual(len(EXCLUSION_REASON_CODES), 30)
        self.assertEqual(EXCLUSION_REASON_CODES[-3:], ("SOURCE_VENUE_MAPPING_CONFLICT", "CONTINUOUS_CONTRACT_MAPPING_UNRESOLVED", "SOURCE_LICENSING_BLOCKED"))
        self.assertEqual(len(RD3_OPEN_BLOCKERS), 15)

    def test_daily_and_snapshot_period_rules(self):
        validate_source_period("DAILY_MARKET", "2026-01-02", None, None)
        validate_source_period("SNAPSHOT", "2026-01-02", None, None)
        with self.assertRaises(ContractError):
            validate_source_period("DAILY_MARKET", None, "2026-01-01", "2026-01-01")

    def test_exact_month_boundaries(self):
        validate_source_period("MONTH", None, "2024-02-01", "2024-02-29")
        with self.assertRaises(ContractError):
            validate_source_period("MONTH", None, "2024-02-02", "2024-02-29")

    def test_exact_quarter_boundaries(self):
        validate_source_period("QUARTER", None, "2026-10-01", "2026-12-31")
        with self.assertRaises(ContractError):
            validate_source_period("QUARTER", None, "2026-10-01", "2026-12-30")

    def test_other_bounded_period_order(self):
        validate_source_period("OTHER_BOUNDED_PERIOD", None, "2026-01-02", "2026-02-03")
        with self.assertRaises(ContractError):
            validate_source_period("OTHER_BOUNDED_PERIOD", None, "2026-02-03", "2026-01-02")

    def test_calendar_role_uniqueness_and_completeness(self):
        self.assertEqual(tuple(item.role for item in normalize_calendar_assignments(assignments())), CALENDAR_ROLES)
        duplicate = assignments()[:-1] + (assignments()[0],)
        with self.assertRaises(ContractError):
            normalize_calendar_assignments(duplicate)
        with self.assertRaises(ContractError):
            normalize_calendar_assignments(assignments()[:-1])

    def test_observation_rejects_mutable_pseudo_calendar_assignment(self):
        class PseudoCalendarAssignment:
            def __init__(self):
                self.subject_id = "synthetic-subject"
                self.role = "MARKET"
                self.status = "NOT_APPLICABLE"
                self.calendar_reference_id = None
                self.calendar_version = None
                self.calendar_hash = None

        pseudo = PseudoCalendarAssignment()
        candidate = list(assignments())
        candidate[0] = pseudo
        accepted = None
        with self.assertRaises(ContractError):
            accepted = observation(calendar_assignments=candidate)
        pseudo.role = "PUBLICATION"
        self.assertIsNone(accepted)

    def test_observation_accepts_real_calendar_assignments(self):
        real_assignments = list(assignments())
        accepted = observation(calendar_assignments=real_assignments)
        real_assignments.clear()
        self.assertEqual(accepted.calendar_assignments, assignments())
        self.assertTrue(all(type(item) is CalendarAssignment for item in accepted.calendar_assignments))

    def test_calendar_assignment_nullability(self):
        with self.assertRaises(ContractError):
            CalendarAssignment("s", "MARKET", "NOT_APPLICABLE", "cal", "v1", H1)
        with self.assertRaises(ContractError):
            CalendarAssignment("s", "MARKET", "RESOLVED", None, None, None)

    def test_timestamp_timezone_and_fraction_rules(self):
        self.assertEqual(parse_rfc3339("2026-01-01T00:00:00.123456+08:00").microsecond, 123456)
        for invalid in ("2026-01-01T00:00:00", "2026-01-01 00:00:00Z", "2026-01-01T00:00:00.1234567Z"):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError):
                parse_rfc3339(invalid)

    def test_timestamp_utc_canonicalization(self):
        self.assertEqual(canonical_timestamp("2026-01-01T08:00:00+08:00"), "2026-01-01T00:00:00Z")
        self.assertEqual(canonical_timestamp("2026-01-01T00:00:00.120000Z"), "2026-01-01T00:00:00.12Z")
        with self.assertRaises(ContractError):
            canonical_timestamp(datetime(2026, 1, 1))

    def test_strict_json_duplicate_key_rejected(self):
        with self.assertRaises(ContractError):
            parse_json_strict('{"a":1,"a":2}')

    def test_nfc_key_and_string_normalization(self):
        self.assertEqual(canonical_json_bytes({"e\u0301": "e\u0301"}), '{"é":"é"}'.encode())

    def test_nfc_key_collision_rejected(self):
        with self.assertRaises(ContractError):
            canonical_json_bytes({"é": 1, "e\u0301": 2})

    def test_dict_insertion_order_determinism(self):
        self.assertEqual(canonical_json_bytes({"b": 2, "a": 1}), canonical_json_bytes({"a": 1, "b": 2}))
        self.assertEqual(canonical_json_bytes({"b": 2, "a": 1}), b'{"a":1,"b":2}')

    def test_decimal_output_and_negative_zero(self):
        self.assertEqual(canonical_json_bytes([Decimal("12.3400"), Decimal("1E+3"), Decimal("-0.000")]), b"[12.34,1000,0]")

    def test_float_nan_and_infinity_rejected(self):
        for value in (1.0, float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ContractError):
                canonical_json_bytes(value)

    def test_invalid_unicode_rejected(self):
        with self.assertRaises(ContractError):
            canonical_json_bytes("\ud800")

    def test_semantic_array_order_preserved(self):
        self.assertNotEqual(canonical_json_bytes(["b", "a"]), canonical_json_bytes(["a", "b"]))

    def test_contract_unordered_calendar_normalization(self):
        reversed_items = tuple(reversed(assignments()))
        self.assertEqual(tuple(x.role for x in normalize_calendar_assignments(reversed_items)), CALENDAR_ROLES)

    def test_exclusion_catalog_normalization_uses_frozen_sort_order(self):
        first = ExclusionReason(EXCLUSION_REASON_CODES[0], "BLOCK", "RECORD", "a", "a", 1)
        second = ExclusionReason(EXCLUSION_REASON_CODES[1], "BLOCK", "RECORD", "b", "b", 2)
        self.assertEqual(normalize_exclusion_reasons((second, first)), (first, second))
        with self.assertRaises(ContractError):
            normalize_exclusion_reasons((first, first))

    def test_rule_bundle_rejects_mutable_pseudo_exclusion_reason(self):
        class PseudoExclusionReason:
            def __init__(self, source):
                self.code = source.code
                self.severity = source.severity
                self.blocking_scope = source.blocking_scope
                self.description = source.description
                self.remediation = source.remediation
                self.sort_order = source.sort_order

        candidate = list(exclusion_reasons())
        pseudo = PseudoExclusionReason(candidate[0])
        candidate[0] = pseudo
        accepted = None
        with self.assertRaises(ContractError):
            accepted = RuleBundle(
                rule_bundle_version="rules@1", contract_version="1.0.0",
                exclusion_catalog_version="1.1.0", exclusion_reasons=candidate, rules={},
            )
        pseudo.code = EXCLUSION_REASON_CODES[1]
        self.assertIsNone(accepted)

    def test_rule_bundle_accepts_real_exclusion_reasons(self):
        real_reasons = list(exclusion_reasons())
        accepted = RuleBundle(
            rule_bundle_version="rules@1", contract_version="1.0.0",
            exclusion_catalog_version="1.1.0", exclusion_reasons=real_reasons, rules={},
        )
        real_reasons.clear()
        self.assertEqual(accepted.exclusion_reasons, exclusion_reasons())
        self.assertTrue(all(type(item) is ExclusionReason for item in accepted.exclusion_reasons))

    def test_distinct_date_and_timestamp_roles_are_validated(self):
        item = observation(
            scheduler_execution_at="2026-01-03T08:00:00+08:00",
            local_business_date="2026-01-03",
            source_business_date="2026-01-02",
            scheduler_business_date="2026-01-03",
            source_publication_at="2026-01-02T15:00:00Z",
            channel_available_at="2026-01-02T15:01:00Z",
        )
        self.assertEqual(item.local_business_date, "2026-01-03")
        with self.assertRaises(ContractError):
            observation(local_business_date="2026-01-03T00:00:00Z")

    def test_domain_separation_and_unknown_domain(self):
        self.assertNotEqual(canonical_hash("OBSERVATION_ID", {"x": 1}), canonical_hash("OBSERVATION_CONTENT", {"x": 1}))
        self.assertEqual(len(HASH_DOMAINS), 9)
        with self.assertRaises(ContractError):
            canonical_hash("UNKNOWN", {})

    def test_unsupported_profiles_rejected(self):
        with self.assertRaises(ContractError):
            canonical_json_bytes({}, profile="C4_CANONICAL_JSON_V2@2.0.0")
        with self.assertRaises(ContractError):
            canonical_hash("OBSERVATION_ID", {}, hash_profile="OTHER")
        with self.assertRaises(ContractError):
            raw_payload_hash(b"x", profile="OTHER")

    def test_raw_byte_hash_exactness(self):
        payload = b"{\"x\":1}\r\n"
        self.assertEqual(raw_payload_hash(payload), hashlib.sha256(payload).hexdigest())
        self.assertNotEqual(raw_payload_hash(payload), raw_payload_hash(payload.rstrip()))

    def test_synthetic_classification_and_authorizations(self):
        item = observation()
        self.assertEqual((item.data_origin, item.operational_status), ("SYNTHETIC_FIXTURE", "SYNTHETIC_NON_OPERATIONAL"))
        self.assertFalse(any(SAFETY_FLAGS.values()))
        self.assertEqual((PIT_ENABLED_SOURCES, RESEARCH_ENABLED_SOURCES, BACKTEST_ENABLED_SOURCES), ((), (), ()))

    def test_models_are_frozen(self):
        item = observation()
        with self.assertRaises(FrozenInstanceError):
            item.source_id = "changed"
        with self.assertRaises(TypeError):
            item.semantic_data["value"] = Decimal("99")

    def test_systematic_supported_api_freeze_matrix(self):
        base = assessment_values()
        pit = assessment_values(
            eligibility_state="INELIGIBLE", feature_available_at_max="2026-01-01T00:00:00Z",
            research_cutoff_at="2026-01-02T00:00:00Z", label_available_at="2026-01-03T00:00:00Z",
            evaluation_as_of_at="2026-01-04T00:00:00Z", reason_codes=(),
        )
        cases = (
            (observation(), (("source_id", "CHANGED"), ("semantic_data", {"changed": True}))),
            (version(), (("raw_payload_hash", H3), ("transformation_version", "changed@2"))),
            (CalendarReference("CAL", "1", "SYNTHETIC", ({"date": "2026-01-01"},)), (("calendar_reference_id", "CHANGED"),)),
            (CalendarAssignment("subject", "MARKET", "NOT_APPLICABLE"), (("subject_id", "CHANGED"),)),
            (Assessment(**base), (("assessment_role", "CHANGED"), ("result", {"changed": True}))),
            (AvailabilityAssessment(**base, availability_basis="UNVERIFIED"), (("availability_basis", "CHANGED"),)),
            (LineageAssessment(**base), (("evaluation_at", "2027-01-01T00:00:00Z"),)),
            (TrustQualityAssessment(**base, trust_state="TRUST_REJECTED", quality_status="FAIL", reason_codes=()), (("trust_state", "REAL_ORIGIN_VERIFIED"),)),
            (PITEligibilityAssessment(**pit), (("research_cutoff_at", "2026-01-01T00:00:00Z"),)),
            (ResearchEligibilityAssessment(**base, eligibility_state="INELIGIBLE", reason_codes=()), (("eligibility_state", "ELIGIBLE"),)),
            (BacktestRecordEligibilityAssessment(**base, eligibility_state="INELIGIBLE", reason_codes=()), (("scope", "DATASET"),)),
            (BacktestDatasetEligibilityAssessment(**base, eligibility_state="INELIGIBLE", reason_codes=()), (("scope", "RECORD"),)),
            (RuleBundle(rule_bundle_version="rules@1", contract_version="1.0.0", exclusion_catalog_version="1.1.0", exclusion_reasons=exclusion_reasons(), rules={"x": 1}), (("rules", {"changed": True}), ("rule_bundle_version", "changed"))),
            (ExclusionReason(EXCLUSION_REASON_CODES[0], "BLOCK", "RECORD", "d", "r", 0), (("code", EXCLUSION_REASON_CODES[1]),)),
            (manifest(), (("authorizations", {name: name == "production_allowed" for name in EXPECTED_AUTHORIZATION_FIELDS}), ("blocker_ids", ("RD3-FAKE-001",)), ("contract_version", "2.0.0"), ("evaluation_as_of_at", "2027-01-01T00:00:00Z"))),
        )
        for item, attacks in cases:
            before = semantic_fingerprint(item)
            for field_name, replacement in attacks:
                with self.subTest(model=type(item).__name__, field=field_name):
                    with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
                        setattr(item, field_name, replacement)
                    self.assertEqual(semantic_fingerprint(item), before)

    def test_public_models_embed_semantic_state_in_frozen_slots(self):
        item = observation()
        self.assertTrue(is_dataclass(item))
        self.assertIs(type(item), Observation)
        self.assertFalse(hasattr(item, "__dict__"))
        self.assertEqual(tuple(item.name for item in fields(item)), model_field_names(Observation))
        self.assertEqual(item.source_id, "SYNTHETIC_TEST_SOURCE")
        self.assertIsInstance(item.semantic_data, MappingProxyType)

    def test_systematic_caller_owned_container_isolation(self):
        observation_input = {"nested": {"items": [1, 2]}}
        version_input = {"nested": {"items": [1, 2]}}
        calendar_input = {"nested": {"items": [1, 2]}}
        assessment_input = {"nested": {"items": [1, 2]}}
        rules_input = {"nested": {"items": [1, 2]}}
        entry_input = {"path": "synthetic", "nested": {"items": [1, 2]}}
        blocker_input = list(EXPECTED_RD3_BLOCKERS)
        reason_input = list(exclusion_reasons())
        models_and_inputs = (
            (observation(semantic_data=observation_input), observation_input),
            (version(semantic_data=version_input), version_input),
            (CalendarReference("CAL", "1", "SYNTHETIC", (calendar_input,)), calendar_input),
            (AvailabilityAssessment(**assessment_values(result=assessment_input), availability_basis="UNVERIFIED"), assessment_input),
            (RuleBundle(rule_bundle_version="rules@1", contract_version="1.0.0", exclusion_catalog_version="1.1.0", exclusion_reasons=reason_input, rules=rules_input), rules_input),
            (manifest(entries=(entry_input,), blocker_ids=blocker_input), entry_input),
        )
        before = [semantic_fingerprint(item) for item, _source in models_and_inputs]
        observation_input["nested"]["items"].append(3)
        version_input["nested"]["items"].append(3)
        calendar_input["nested"]["items"].append(3)
        assessment_input["nested"]["items"].append(3)
        rules_input["nested"]["items"].append(3)
        entry_input["nested"]["items"].append(3)
        blocker_input[0] = "RD3-FAKE-001"
        reason_input.clear()
        self.assertEqual([semantic_fingerprint(item) for item, _source in models_and_inputs], before)

    def test_ordinary_constructors_enforce_exported_contract_catalogs(self):
        self.assertEqual(tuple(SAFETY_FLAGS), EXPECTED_AUTHORIZATION_FIELDS)
        self.assertEqual(RD3_OPEN_BLOCKERS, EXPECTED_RD3_BLOCKERS)
        invalid_constructors = (
            lambda: validate_source_period("FAKE", None, None, None),
            lambda: CalendarAssignment("s", "FAKE", "NOT_APPLICABLE"),
            lambda: CalendarAssignment("s", "MARKET", "FAKE"),
            lambda: AvailabilityAssessment(**assessment_values(), availability_basis="FAKE"),
            lambda: TrustQualityAssessment(**assessment_values(), trust_state="FAKE", quality_status="FAIL", reason_codes=()),
            lambda: TrustQualityAssessment(**assessment_values(), trust_state="TRUST_REJECTED", quality_status="FAKE", reason_codes=()),
            lambda: ResearchEligibilityAssessment(**assessment_values(), eligibility_state="FAKE", reason_codes=()),
            lambda: ExclusionReason("FAKE", "BLOCK", "RECORD", "d", "r", 0),
            lambda: canonical_hash("FAKE", {}),
            lambda: manifest(contract_version="9.9.9"),
        )
        for construct in invalid_constructors:
            with self.subTest(construct=construct), self.assertRaises(ContractError):
                construct()

    def test_forged_immutable_mapping_is_resnapshotted_at_model_boundary(self):
        mutable = {"items": [1, 2]}
        forged = tuple.__new__(ImmutableMapping, (("nested", mutable),))
        item = observation(semantic_data=forged)
        before = semantic_fingerprint(item)
        self.assertIsNot(item.semantic_data, forged)
        mutable["items"].append(3)
        self.assertEqual(semantic_fingerprint(item), before)

    def test_semantic_content_is_defensively_and_recursively_frozen(self):
        source = {"nested": {"value": 1}, "items": [1, {"value": 2}]}
        item = observation(semantic_data=source)
        before_id, before_hash = item.observation_id, item.content_hash
        source["nested"]["value"] = 99
        source["items"].append(3)
        source["items"][1]["value"] = 99
        self.assertEqual(item.semantic_data["nested"]["value"], 1)
        self.assertEqual(item.semantic_data["items"][1]["value"], 2)
        self.assertEqual((item.observation_id, item.content_hash), (before_id, before_hash))
        self.assertIsInstance(item.semantic_data, MappingProxyType)
        self.assertNotIsInstance(item.semantic_data, dict)
        with self.assertRaises(TypeError):
            dict.__setitem__(item.semantic_data, "bypass", True)

    def test_immutable_mapping_backing_cannot_be_rebound(self):
        wrapped = ImmutableMapping({"nested": {"x": [1, 2]}})
        for attack in (
            lambda: setattr(wrapped, "_ImmutableMapping__data", {}),
            lambda: object.__setattr__(wrapped, "_ImmutableMapping__data", {}),
            lambda: setattr(wrapped, "backing", {}),
        ):
            with self.subTest(attack=attack), self.assertRaises((AttributeError, TypeError)):
                attack()
        self.assertEqual(wrapped["nested"]["x"], (1, 2))

    def test_existing_immutable_mapping_is_defensively_resnapshotted(self):
        wrapped = ImmutableMapping({"nested": {"x": [1, 2]}})
        item = observation(semantic_data=wrapped)
        before = (item.content_projection(), item.observation_id, item.content_hash)
        self.assertIsNot(item.semantic_data, wrapped)
        with self.assertRaises((AttributeError, TypeError)):
            object.__setattr__(wrapped, "_ImmutableMapping__data", {"changed": True})
        self.assertEqual((item.content_projection(), item.observation_id, item.content_hash), before)

    def test_mutable_mapping_subclass_is_snapshotted_and_immutable_subclass_fails_closed(self):
        class ExternalMapping(Mapping):
            def __init__(self, source):
                self.source = source

            def __getitem__(self, key):
                return self.source[key]

            def __iter__(self):
                return iter(self.source)

            def __len__(self):
                return len(self.source)

        source = {"nested": {"x": [1, 2]}}
        item = observation(semantic_data=ExternalMapping(source))
        before = (item.content_projection(), item.content_hash)
        source["nested"]["x"].append(3)
        source["nested"] = {"x": [9]}
        self.assertEqual((item.content_projection(), item.content_hash), before)
        with self.assertRaises(TypeError):
            class MutableImmutableMapping(ImmutableMapping):
                pass

    def test_mutable_enum_value_is_snapshotted(self):
        class MutableValue(Enum):
            ITEM = {"nested": [1, 2]}

        item = observation(semantic_data={"enum_value": MutableValue.ITEM})
        before = (item.content_projection(), item.content_hash)
        MutableValue.ITEM.value["nested"].append(3)
        self.assertEqual((item.content_projection(), item.content_hash), before)

    def test_set_and_unsupported_mutable_container_fail_closed(self):
        with self.assertRaises(ContractError):
            observation(semantic_data={"unordered": {"a", "b"}})
        with self.assertRaises(ContractError):
            observation(semantic_data={"queue": deque([1, 2])})
        with self.assertRaises(ContractError):
            observation(semantic_data={"bytes": bytearray(b"x")})

    def test_authorization_policy_display_view_is_immutable(self):
        self.assertEqual(tuple(SAFETY_FLAGS), EXPECTED_AUTHORIZATION_FIELDS)
        with self.assertRaises(TypeError):
            SAFETY_FLAGS["production_allowed"] = True

    def test_every_authorization_requires_exact_boolean_false(self):
        for name in EXPECTED_AUTHORIZATION_FIELDS:
            changed = {field: False for field in EXPECTED_AUTHORIZATION_FIELDS}
            changed[name] = True
            with self.subTest(flag=name), self.assertRaises(ContractError):
                manifest(authorizations=changed)

        class FalseLike:
            def __bool__(self):
                return False

            def __eq__(self, other):
                return other is False

        for value in (0, 1, None, "false", "False", [], {}, Decimal(0), FalseLike()):
            changed = {field: False for field in EXPECTED_AUTHORIZATION_FIELDS}
            changed["production_allowed"] = value
            with self.subTest(value=repr(value)), self.assertRaises(ContractError):
                manifest(authorizations=changed)
        missing = {field: False for field in EXPECTED_AUTHORIZATION_FIELDS[:-1]}
        with self.assertRaises(ContractError):
            manifest(authorizations=missing)
        renamed = {field: False for field in EXPECTED_AUTHORIZATION_FIELDS[:-1]}
        renamed["procurement_enabled"] = False
        with self.assertRaises(ContractError):
            manifest(authorizations=renamed)

    def test_manifest_requires_exact_independent_rd3_blocker_set(self):
        self.assertEqual(set(RD3_OPEN_BLOCKERS), set(EXPECTED_RD3_BLOCKERS))
        item = manifest(blocker_ids=tuple(reversed(EXPECTED_RD3_BLOCKERS)))
        self.assertEqual(item.blocker_ids, tuple(sorted(EXPECTED_RD3_BLOCKERS)))
        invalid = (
            EXPECTED_RD3_BLOCKERS[:-1],
            EXPECTED_RD3_BLOCKERS[:-1] + ("RD3-FAKE-001",),
            EXPECTED_RD3_BLOCKERS + ("RD3-FAKE-001",),
            EXPECTED_RD3_BLOCKERS[:-1] + (EXPECTED_RD3_BLOCKERS[0],),
            EXPECTED_RD3_BLOCKERS[:-1] + ("rd3-wb-003",),
            EXPECTED_RD3_BLOCKERS[:-1] + ("RD3-WB-003 ",),
        )
        for blocker_ids in invalid:
            with self.subTest(blocker_ids=blocker_ids), self.assertRaises(ContractError):
                manifest(blocker_ids=blocker_ids)

    def test_required_identifiers_and_manifest_contract_version_fail_closed(self):
        for field_name in ("source_id", "source_record_identifier", "metric_id", "instrument_id"):
            with self.subTest(field=field_name), self.assertRaises(ContractError):
                observation(**{field_name: ""})
        with self.assertRaises(ContractError):
            CalendarAssignment("", "MARKET", "NOT_APPLICABLE")
        with self.assertRaises(ContractError):
            CalendarReference("", "1.0.0", "SYNTHETIC")
        with self.assertRaises(ContractError):
            manifest(contract_version="2.0.0")

    def test_backtest_models_fix_assessment_type_and_scope(self):
        base = {
            "subject_id": H1, "assessment_role": "SOURCE",
            "evaluation_at": "2026-01-04T00:00:00Z", "rule_bundle_version": "rules@1",
            "result": {}, "eligibility_state": "INELIGIBLE", "reason_codes": (),
        }
        record = BacktestRecordEligibilityAssessment(**base)
        dataset = BacktestDatasetEligibilityAssessment(**base)
        self.assertEqual((record.assessment_type, record.scope), ("BACKTEST_RECORD_ELIGIBILITY", "RECORD"))
        self.assertEqual((dataset.assessment_type, dataset.scope), ("BACKTEST_DATASET_ELIGIBILITY", "DATASET"))
        with self.assertRaises(TypeError):
            BacktestRecordEligibilityAssessment(**base, scope="DATASET")
        with self.assertRaises(TypeError):
            BacktestDatasetEligibilityAssessment(**base, scope="RECORD")

    def test_observation_identity_excludes_operational_timestamps(self):
        first = observation(created_at="2026-01-03T00:00:01Z", collected_at="2026-01-03T00:00:00Z")
        second = observation(created_at="2026-01-04T00:00:01Z", collected_at="2026-01-04T00:00:00Z")
        self.assertEqual(first.observation_id, second.observation_id)
        self.assertNotEqual(first.content_hash, second.content_hash)

    def test_observation_identity_changes_with_represented_period(self):
        self.assertNotEqual(observation().observation_id, observation(source_market_date="2026-01-03").observation_id)

    def test_observation_version_same_five_fields_same_id(self):
        first = version(created_at="2026-01-03T00:00:01Z", revision_available_at="2026-01-03T00:00:00Z")
        second = version(created_at="2026-02-03T00:00:01Z", revision_available_at="2026-02-03T00:00:00Z")
        self.assertEqual(first.observation_version_id, second.observation_version_id)
        self.assertNotEqual(first.content_hash, second.content_hash)

    def test_observation_version_parent_is_content_not_identity(self):
        self.assertEqual(version(parent_version_id=None).observation_version_id, version(parent_version_id=H3).observation_version_id)
        self.assertNotEqual(version(parent_version_id=None).content_hash, version(parent_version_id=H3).content_hash)

    def test_observation_version_each_frozen_field_changes_id(self):
        original = version().observation_version_id
        variants = (
            version(observation_id=H3), version(source_version_or_release_key="SYNTHETIC_RELEASE_002"),
            version(stable_version_key="SYNTHETIC_VERSION_002"), version(raw_payload_hash=H3),
            version(transformation_version="synthetic-transform@2.0.0"),
        )
        self.assertTrue(all(item.observation_version_id != original for item in variants))

    def test_observation_version_rejects_implicit_latest(self):
        with self.assertRaises(ContractError):
            version(transformation_version="latest")

    def test_downstream_assessment_does_not_change_version_id(self):
        item = version()
        first = AvailabilityAssessment(subject_id=item.observation_version_id, assessment_role="SOURCE", evaluation_at="2026-01-04T00:00:00Z", rule_bundle_version="rules@1", result={"eligible": False}, availability_basis="UNVERIFIED")
        second = AvailabilityAssessment(subject_id=item.observation_version_id, assessment_role="SOURCE", evaluation_at="2026-01-04T00:00:00Z", rule_bundle_version="rules@1", result={"eligible": True}, availability_basis="UNVERIFIED")
        self.assertEqual(item.observation_version_id, version().observation_version_id)
        self.assertEqual(first.assessment_id, second.assessment_id)
        self.assertNotEqual(first.content_hash, second.content_hash)

    def test_equivalent_assessment_instants_have_identical_id_and_content_hash(self):
        common = {
            "subject_id": H1, "assessment_role": "SOURCE", "rule_bundle_version": "rules@1",
            "result": {"eligible": False}, "availability_basis": "UNVERIFIED",
        }
        utc = AvailabilityAssessment(evaluation_at="2026-09-16T10:00:00Z", **common)
        offset = AvailabilityAssessment(evaluation_at="2026-09-16T05:00:00-05:00", **common)
        self.assertEqual(utc.evaluation_at, "2026-09-16T10:00:00Z")
        self.assertEqual(utc, offset)
        self.assertEqual(utc.assessment_id, offset.assessment_id)
        self.assertEqual(utc.content_hash, offset.content_hash)

    def test_equivalent_observation_timestamps_have_identical_content_hash(self):
        utc = observation(collected_at="2026-09-16T10:00:00Z", observed_at="2026-09-16T10:00:00.100000Z")
        offset = observation(collected_at="2026-09-16T05:00:00-05:00", observed_at="2026-09-16T05:00:00.1-05:00")
        self.assertEqual(utc.observed_at, "2026-09-16T10:00:00.1Z")
        self.assertEqual(utc.content_hash, offset.content_hash)

    def test_manifest_equivalent_instants_have_identical_stable_identity(self):
        common = {
            "manifest_kind": "SYNTHETIC_READINESS", "semantic_subject_scope_hash": H1,
            "contract_version": "1.0.0", "rule_bundle_version": "rules@1", "entries": (),
            "authorizations": dict(SAFETY_FLAGS),
        }
        utc = ReadinessManifest(evaluation_as_of_at="2026-09-16T10:00:00Z", **common)
        offset = ReadinessManifest(evaluation_as_of_at="2026-09-16T05:00:00-05:00", **common)
        self.assertEqual(utc.manifest_id, offset.manifest_id)
        self.assertEqual(utc.content_hash, offset.content_hash)

    def test_model_timestamp_fraction_and_invalid_shapes(self):
        item = version(revision_available_at="2026-01-03T00:00:00.100000Z")
        self.assertEqual(item.revision_available_at, "2026-01-03T00:00:00.1Z")
        self.assertEqual(version(created_at="2026-01-03T00:00:00.000000Z").created_at, "2026-01-03T00:00:00Z")
        with self.assertRaises(ContractError):
            version(created_at="2026-01-03T00:00:00")
        with self.assertRaises(ContractError):
            version(created_at="2026-01-03T00:00:00.1234567Z")

    def test_content_projection_has_no_own_hash(self):
        self.assertNotIn("content_hash", observation().content_projection())
        self.assertNotIn("content_hash", version().content_projection())

    def test_materialized_ids_and_hashes_match_public_canonical_contract(self):
        observed = observation()
        self.assertEqual(observed.observation_id, canonical_hash("OBSERVATION_ID", observed.identity_projection()))
        self.assertEqual(observed.content_hash, canonical_hash("OBSERVATION_CONTENT", observed.content_projection()))
        observed_version = version()
        self.assertEqual(
            observed_version.observation_version_id,
            canonical_hash("OBSERVATION_VERSION_ID", observed_version.identity_projection()),
        )
        self.assertEqual(
            observed_version.content_hash,
            canonical_hash("OBSERVATION_VERSION_CONTENT", observed_version.content_projection()),
        )
        assessed = AvailabilityAssessment(**assessment_values(), availability_basis="UNVERIFIED")
        self.assertEqual(assessed.assessment_id, canonical_hash("ASSESSMENT_ID", assessed.identity_projection()))
        self.assertEqual(assessed.content_hash, canonical_hash("ASSESSMENT_CONTENT", assessed.content_projection()))
        calendar = CalendarReference("CAL", "1", "SYNTHETIC", ({"date": "2026-01-01"},))
        self.assertEqual(calendar.calendar_hash, canonical_hash("CALENDAR_CONTENT", calendar.content_projection()))
        ready = manifest()
        self.assertEqual(ready.manifest_id, canonical_hash("MANIFEST_CONTENT", {"identity": ready.identity_projection()}))
        self.assertEqual(ready.content_hash, canonical_hash("MANIFEST_CONTENT", ready.content_projection()))

    def test_pit_structural_consistency_only(self):
        validate_pit_structure("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z", "2026-01-04T00:00:00Z")
        with self.assertRaises(ContractError):
            validate_pit_structure("2026-01-03T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-04T00:00:00Z", "2026-01-05T00:00:00Z")

    def test_public_model_names_are_frozen_value_classes(self):
        model_types = (
            CalendarAssignment, Observation, ObservationVersion, CalendarReference, Assessment,
            AvailabilityAssessment, LineageAssessment, TrustQualityAssessment,
            PITEligibilityAssessment, ResearchEligibilityAssessment,
            BacktestRecordEligibilityAssessment, BacktestDatasetEligibilityAssessment,
            RuleBundle, ExclusionReason, ReadinessManifest,
        )
        for model_type in model_types:
            with self.subTest(model=model_type.__name__):
                self.assertIsInstance(model_type, type)
                self.assertTrue(is_dataclass(model_type))
                self.assertTrue(hasattr(model_type, "__slots__"))
                self.assertFalse(hasattr(model_type, "func"))
                self.assertEqual(
                    model_field_names(model_type),
                    tuple(item.name for item in fields(model_type)),
                )
        self.assertEqual(model_field_names(Observation)[:4], (
            "source_id", "source_record_identifier", "metric_id", "instrument_id",
        ))

    def test_semantic_state_is_embedded_and_deterministic(self):
        first_source = {"nested": {"items": [1, 2]}}
        second_source = {"nested": {"items": [1, 2]}}
        first = observation(semantic_data=first_source)
        second = observation(semantic_data=second_source)
        self.assertEqual(first, second)
        self.assertIsNot(first.semantic_data, second.semantic_data)
        self.assertEqual(first.content_projection(), second.content_projection())
        self.assertEqual(first.identity_projection(), second.identity_projection())
        self.assertEqual(first.observation_id, second.observation_id)
        self.assertEqual(first.content_hash, second.content_hash)
        first_source["nested"]["items"].append(3)
        second_source.clear()
        self.assertEqual(first.content_projection(), second.content_projection())

    def test_supported_constructor_path_fails_closed(self):
        with self.assertRaises(ContractError):
            observation(data_origin="REAL")
        with self.assertRaises(ContractError):
            version(raw_payload_hash="not-a-hash")
        malicious_flags = {name: False for name in EXPECTED_AUTHORIZATION_FIELDS}
        malicious_flags["production_allowed"] = True
        with self.assertRaises(ContractError):
            manifest(authorizations=malicious_flags)
        with self.assertRaises(ContractError):
            manifest(blocker_ids=tuple(f"RD3-FAKE-{index:03d}" for index in range(15)))

    def test_threat_boundary_has_no_closure_registry_claim(self):
        source = inspect.getsource(contract_module)
        self.assertNotIn("state_by_id", source)
        self.assertNotIn("functools.partial", source)
        self.assertIn("not a hostile-code sandbox", contract_module.__doc__)
        self.assertIn("no claim", contract_module.__doc__)

    def test_same_major_hash_compatibility_is_explicit(self):
        value = {"synthetic": True}
        first = canonical_hash("OBSERVATION_ID", value, contract_version="1.0.0")
        later = canonical_hash("OBSERVATION_ID", value, contract_version="1.9.7")
        self.assertNotEqual(first, later)
        self.assertEqual(len(later), 64)
        with self.assertRaises(ContractError):
            canonical_hash("OBSERVATION_ID", value, contract_version="2.0.0")


if __name__ == "__main__":
    unittest.main()
