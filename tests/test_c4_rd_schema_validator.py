from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch

import scripts.c4_rd_contract as contract_module
import scripts.c4_rd_schema_validator as schema_module

from scripts.c4_rd_contract import (
    CALENDAR_ROLES, EXCLUSION_REASON_CODES, SAFETY_FLAGS, AvailabilityAssessment,
    BacktestDatasetEligibilityAssessment, BacktestRecordEligibilityAssessment,
    CalendarAssignment, CalendarReference, ContractError, ExclusionReason, LineageAssessment,
    Observation, ObservationVersion, PITEligibilityAssessment, ReadinessManifest,
    ResearchEligibilityAssessment, RuleBundle, TrustQualityAssessment,
    model_field_names,
)
from scripts.c4_rd_schema_validator import LocalSchemaValidator, SCHEMA_DIRECTORY, SCHEMA_FILES, SchemaValidationError


OBSERVATION_SCHEMA = "urn:c4-rd:schema:c4-observation:1.0.0"
TRUST_SCHEMA = "urn:c4-rd:schema:c4-trust-quality-assessment:1.0.0"
RULE_SCHEMA = "urn:c4-rd:schema:c4-rule-bundle:1.0.0"
EXPORT_SCHEMA = "urn:c4-rd:schema:c4-export-contract:1.0.0"
BACKTEST_SCHEMA = "urn:c4-rd:schema:c4-backtest-eligibility:1.0.0"
MANIFEST_SCHEMA = "urn:c4-rd:schema:c4-readiness-manifest:1.0.0"
H = "a" * 64
H2 = "b" * 64
EXPECTED_AUTHORIZATION_FIELDS = (
    "real_data_ingestion_allowed", "shadow_export_allowed", "research_dataset_allowed",
    "backtest_allowed", "production_allowed", "canonical_allowed", "deferred_allowed",
    "ml_allowed", "procurement_allowed",
)
EXPECTED_RD3_BLOCKERS_SORTED = (
    "RD3-BZ-001", "RD3-LME-001", "RD3-LME-002", "RD3-LME-003",
    "RD3-SMM-001", "RD3-SMM-002", "RD3-SMM-003", "RD3-SMM-004",
    "RD3-WB-001", "RD3-WB-002", "RD3-WB-003", "RD3-YAHOO-001",
    "RD3-YAHOO-002", "RD3-YAHOO-003", "RD3-YAHOO-004",
)


def calendar_assignment(role: str, status: str = "NOT_APPLICABLE") -> dict:
    return {
        "subject_id": "SYNTHETIC_RECORD_001", "role": role, "status": status,
        "calendar_reference_id": None, "calendar_version": None, "calendar_hash": None,
    }


def observation() -> dict:
    return {
        "source_id": "SYNTHETIC_TEST_SOURCE", "source_record_identifier": "SYNTHETIC_RECORD_001",
        "metric_id": "SYNTHETIC_PRICE", "instrument_id": "SYNTHETIC_COPPER",
        "source_period_type": "DAILY_MARKET", "source_market_date": "2026-01-02",
        "source_period_start_date": None, "source_period_end_date": None,
        "calendar_assignments": [calendar_assignment(role) for role in CALENDAR_ROLES],
        "data_origin": "SYNTHETIC_FIXTURE", "operational_status": "SYNTHETIC_NON_OPERATIONAL",
        "collected_at": "2026-01-03T00:00:00Z", "semantic_data": {"value": "12.50"},
    }


def readiness_manifest_payload() -> dict:
    return {
        "manifest_kind": "SYNTHETIC_READINESS", "semantic_subject_scope_hash": H,
        "evaluation_as_of_at": "2026-01-04T00:00:00Z", "contract_version": "1.0.0",
        "rule_bundle_version": "rules@1.0.0", "entries": [],
        "blocker_ids": list(EXPECTED_RD3_BLOCKERS_SORTED),
        "authorizations": {name: False for name in EXPECTED_AUTHORIZATION_FIELDS},
        "report_generated_at": None,
    }


def backtest_payload(assessment_type: str, scope: str) -> dict:
    return {
        "assessment_type": assessment_type, "subject_id": H, "assessment_role": "SOURCE",
        "evaluation_at": "2026-01-04T00:00:00Z", "rule_bundle_version": "rules@1.0.0",
        "eligibility_state": "INELIGIBLE", "scope": scope,
        "reason_codes": ["TRUST_STATE_NOT_APPROVED"], "result": {}, "evidence": {},
        "assessed_at": None, "created_at": None,
    }


def exclusion_reasons() -> tuple[ExclusionReason, ...]:
    return tuple(
        ExclusionReason(code, "BLOCK", "RECORD", f"Synthetic description {index}", "Synthetic remediation", index)
        for index, code in enumerate(EXCLUSION_REASON_CODES)
    )


def model_samples() -> tuple[tuple[str, object], ...]:
    calendar_items = tuple(CalendarAssignment("SYNTHETIC_RECORD_001", role, "NOT_APPLICABLE") for role in CALENDAR_ROLES)
    base = {
        "subject_id": H, "assessment_role": "SOURCE", "evaluation_at": "2026-01-04T00:00:00Z",
        "rule_bundle_version": "rules@1.0.0", "result": {"stored": True}, "evidence": {"synthetic": True},
    }
    observation_model = Observation(
        "SYNTHETIC_TEST_SOURCE", "SYNTHETIC_RECORD_001", "SYNTHETIC_PRICE", "SYNTHETIC_COPPER",
        "DAILY_MARKET", "2026-01-02", None, None, calendar_items,
    )
    return (
        (OBSERVATION_SCHEMA, observation_model),
        ("urn:c4-rd:schema:c4-observation-version:1.0.0", ObservationVersion(H, "SYNTHETIC_RELEASE_001", "SYNTHETIC_VERSION_001", H2, "transform@1.0.0")),
        ("urn:c4-rd:schema:c4-calendar-reference:1.0.0", CalendarReference("SYNTHETIC_CALENDAR", "1.0.0", "SYNTHETIC", ())),
        ("urn:c4-rd:schema:c4-availability-assessment:1.0.0", AvailabilityAssessment(**base, availability_basis="UNVERIFIED")),
        ("urn:c4-rd:schema:c4-lineage-assessment:1.0.0", LineageAssessment(**base)),
        (TRUST_SCHEMA, TrustQualityAssessment(**base, trust_state="TRUST_REJECTED", quality_status="FAIL", reason_codes=("TRUST_STATE_NOT_APPROVED",))),
        ("urn:c4-rd:schema:c4-pit-eligibility:1.0.0", PITEligibilityAssessment(**base, eligibility_state="INELIGIBLE", feature_available_at_max="2026-01-01T00:00:00Z", research_cutoff_at="2026-01-02T00:00:00Z", label_available_at="2026-01-03T00:00:00Z", evaluation_as_of_at="2026-01-04T00:00:00Z", reason_codes=("TRUST_STATE_NOT_APPROVED",))),
        ("urn:c4-rd:schema:c4-research-eligibility:1.0.0", ResearchEligibilityAssessment(**base, eligibility_state="INELIGIBLE", reason_codes=("TRUST_STATE_NOT_APPROVED",))),
        ("urn:c4-rd:schema:c4-backtest-eligibility:1.0.0", BacktestRecordEligibilityAssessment(**base, eligibility_state="INELIGIBLE", reason_codes=("TRUST_STATE_NOT_APPROVED",))),
        ("urn:c4-rd:schema:c4-backtest-eligibility:1.0.0", BacktestDatasetEligibilityAssessment(**base, eligibility_state="INELIGIBLE", reason_codes=("TRUST_STATE_NOT_APPROVED",))),
        (RULE_SCHEMA, RuleBundle(rule_bundle_version="rules@1.0.0", contract_version="1.0.0", exclusion_catalog_version="1.1.0", exclusion_reasons=exclusion_reasons(), rules={})),
        ("urn:c4-rd:schema:c4-readiness-manifest:1.0.0", ReadinessManifest(manifest_kind="SYNTHETIC_READINESS", semantic_subject_scope_hash=H, evaluation_as_of_at="2026-01-04T00:00:00Z", contract_version="1.0.0", rule_bundle_version="rules@1.0.0", entries=(), authorizations=dict(SAFETY_FLAGS))),
    )


class SchemaValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = LocalSchemaValidator()

    def assertInvalid(self, schema_id, value):
        result = self.validator.validate(schema_id, value)
        self.assertFalse(result.valid, result.as_dict())
        self.assertTrue(result.errors)
        return result

    def test_all_approved_schemas_load(self):
        self.assertEqual(len(self.validator.schema_ids), len(SCHEMA_FILES))
        self.assertEqual(len(set(self.validator.schema_ids)), 13)

    def test_valid_synthetic_observation(self):
        self.assertTrue(self.validator.validate(OBSERVATION_SCHEMA, observation()).valid)

    def test_unknown_field_rejected(self):
        value = observation(); value["unauthorized"] = True
        self.assertInvalid(OBSERVATION_SCHEMA, value)

    def test_invalid_enum_rejected(self):
        value = observation(); value["source_period_type"] = "DAILY"
        self.assertInvalid(OBSERVATION_SCHEMA, value)

    def test_stale_alias_rejected(self):
        value = {
            "assessment_type": "TRUST_QUALITY", "subject_id": H, "assessment_role": "SOURCE",
            "evaluation_at": "2026-01-01T00:00:00Z", "rule_bundle_version": "rules@1",
            "trust_state": "TRUST_REJECTED", "quality_status": "FAIL",
            "reason_codes": ["INVALID_VALUE"], "result": {}, "evidence": {},
        }
        self.assertInvalid(TRUST_SCHEMA, value)

    def test_invalid_rfc3339_shape_rejected(self):
        value = observation(); value["collected_at"] = "2026-01-03T00:00:00"
        self.assertInvalid(OBSERVATION_SCHEMA, value)

    def test_invalid_sha256_shape_rejected(self):
        value = {
            "contract_version": "1.0.0", "request_id": "SYNTHETIC_EXPORT_REQUEST",
            "read_only": True, "manifest_hash": "ABC", "shadow_export_allowed": False,
            "production_allowed": False, "canonical_allowed": False,
            "deferred_allowed": False, "procurement_allowed": False,
        }
        self.assertInvalid(EXPORT_SCHEMA, value)

    def test_missing_required_field_rejected(self):
        value = observation(); del value["source_id"]
        self.assertInvalid(OBSERVATION_SCHEMA, value)

    def test_conditional_calendar_nullability(self):
        value = observation(); value["calendar_assignments"][0]["status"] = "RESOLVED"
        self.assertInvalid(OBSERVATION_SCHEMA, value)

    def test_source_period_combinations(self):
        value = observation(); value["source_period_type"] = "MONTH"; value["source_market_date"] = None
        value["source_period_start_date"] = "2026-02-01"; value["source_period_end_date"] = "2026-02-28"
        self.assertTrue(self.validator.validate(OBSERVATION_SCHEMA, value).valid)
        value["source_period_end_date"] = "2026-02-27"
        self.assertInvalid(OBSERVATION_SCHEMA, value)

    def test_exact_four_calendar_roles(self):
        value = observation(); value["calendar_assignments"].pop()
        self.assertInvalid(OBSERVATION_SCHEMA, value)

    def test_duplicate_calendar_role_rejected(self):
        value = observation(); value["calendar_assignments"][-1]["role"] = "MARKET"
        self.assertInvalid(OBSERVATION_SCHEMA, value)

    def test_local_ref_resolution_succeeds(self):
        result = self.validator.validate(OBSERVATION_SCHEMA, observation())
        self.assertTrue(result.valid)
        self.assertEqual(result.errors, ())

    def test_unknown_schema_id_and_version_fail_closed(self):
        unsupported = (
            "urn:c4-rd:schema:unknown:1.0.0",
            "urn:c4-rd:schema:c4-observation:2.0.0",
            "file:///tmp/c4-observation.schema.json",
            "../schemas/c4_observation.schema.json",
            "schemas/c4_rd/c4_observation.schema.json",
            "https://example.invalid/c4-observation.schema.json",
        )
        for schema_id in unsupported:
            with self.subTest(schema_id=schema_id), self.assertRaises(SchemaValidationError):
                self.validator.validate(schema_id, {})

    def test_no_remote_refs_are_present(self):
        for schema in self.validator._schemas.values():
            encoded = str(schema)
            self.assertNotIn("'$ref': 'http", encoded)
            self.assertNotIn("'$ref': 'https", encoded)

    def test_rule_catalog_requires_exact_frozen_order(self):
        value = {
            "rule_bundle_version": "rules@1.0.0", "contract_version": "1.0.0",
            "exclusion_catalog_version": "1.1.0", "exclusion_reasons": [item.content_projection() for item in exclusion_reasons()], "rules": {},
        }
        self.assertTrue(self.validator.validate(RULE_SCHEMA, value).valid)
        value["exclusion_reasons"] = list(reversed(value["exclusion_reasons"]))
        self.assertInvalid(RULE_SCHEMA, value)

    def test_backtest_type_scope_pairs_are_coupled(self):
        valid_pairs = (
            ("BACKTEST_RECORD_ELIGIBILITY", "RECORD"),
            ("BACKTEST_DATASET_ELIGIBILITY", "DATASET"),
        )
        invalid_pairs = (
            ("BACKTEST_RECORD_ELIGIBILITY", "DATASET"),
            ("BACKTEST_DATASET_ELIGIBILITY", "RECORD"),
        )
        for assessment_type, scope in valid_pairs:
            with self.subTest(valid=(assessment_type, scope)):
                self.assertTrue(self.validator.validate(BACKTEST_SCHEMA, backtest_payload(assessment_type, scope)).valid)
        for assessment_type, scope in invalid_pairs:
            with self.subTest(invalid=(assessment_type, scope)):
                self.assertInvalid(BACKTEST_SCHEMA, backtest_payload(assessment_type, scope))

    def test_manifest_schema_requires_exact_independent_rd3_blockers(self):
        value = readiness_manifest_payload()
        self.assertTrue(self.validator.validate(MANIFEST_SCHEMA, value).valid)
        invalid = (
            list(EXPECTED_RD3_BLOCKERS_SORTED[:-1]),
            list(EXPECTED_RD3_BLOCKERS_SORTED[:-1]) + ["RD3-FAKE-001"],
            list(EXPECTED_RD3_BLOCKERS_SORTED) + ["RD3-FAKE-001"],
            list(reversed(EXPECTED_RD3_BLOCKERS_SORTED)),
        )
        for blocker_ids in invalid:
            changed = readiness_manifest_payload(); changed["blocker_ids"] = blocker_ids
            with self.subTest(blocker_ids=blocker_ids):
                self.assertInvalid(MANIFEST_SCHEMA, changed)

    def test_schema_authority_ignores_rebound_convenience_globals(self):
        fake_blockers = tuple(f"RD3-FAKE-{index:03d}" for index in range(15))
        with patch.object(contract_module, "RD3_OPEN_BLOCKERS", fake_blockers), patch.object(
            contract_module, "EXCLUSION_REASON_CODES", ("FAKE",)
        ), patch.object(schema_module, "SCHEMA_FILES", ("hostile.schema.json",)):
            validator = LocalSchemaValidator()
            self.assertEqual(len(validator.schema_ids), 13)
            changed = readiness_manifest_payload()
            changed["blocker_ids"] = list(fake_blockers)
            self.assertFalse(validator.validate(MANIFEST_SCHEMA, changed).valid)

    def test_manifest_schema_requires_exact_boolean_false_authorizations(self):
        value = readiness_manifest_payload()
        self.assertEqual(tuple(value["authorizations"]), EXPECTED_AUTHORIZATION_FIELDS)
        for name in EXPECTED_AUTHORIZATION_FIELDS:
            changed = readiness_manifest_payload(); changed["authorizations"][name] = True
            with self.subTest(true_flag=name):
                self.assertInvalid(MANIFEST_SCHEMA, changed)
            changed = readiness_manifest_payload(); del changed["authorizations"][name]
            with self.subTest(missing_flag=name):
                self.assertInvalid(MANIFEST_SCHEMA, changed)
        for invalid_value in (0, 1, None, "false", [], {}):
            changed = readiness_manifest_payload(); changed["authorizations"]["production_allowed"] = invalid_value
            with self.subTest(value=repr(invalid_value)):
                self.assertInvalid(MANIFEST_SCHEMA, changed)

    def test_identified_constructor_and_schema_negative_parity(self):
        changed = observation(); changed["source_id"] = ""
        self.assertInvalid(OBSERVATION_SCHEMA, changed)
        calendar_items = tuple(CalendarAssignment("SYNTHETIC_RECORD_001", role, "NOT_APPLICABLE") for role in CALENDAR_ROLES)
        with self.assertRaises(ContractError):
            Observation("", "SYNTHETIC_RECORD_001", "PRICE", "COPPER", "DAILY_MARKET", "2026-01-02", None, None, calendar_items)
        with self.assertRaises(ContractError):
            CalendarReference("", "1.0.0", "SYNTHETIC", ())
        changed = observation(); changed["semantic_data"] = []
        self.assertInvalid(OBSERVATION_SCHEMA, changed)
        with self.assertRaises(ContractError):
            Observation(
                "SYNTHETIC_TEST_SOURCE", "SYNTHETIC_RECORD_001", "PRICE", "COPPER",
                "DAILY_MARKET", "2026-01-02", None, None, calendar_items, semantic_data=[],
            )
        with self.assertRaises(ContractError):
            AvailabilityAssessment(
                subject_id=H, assessment_role="", evaluation_at="2026-01-04T00:00:00Z",
                rule_bundle_version="rules@1.0.0", result={}, availability_basis="UNVERIFIED",
            )
        changed = readiness_manifest_payload(); changed["contract_version"] = "2.0.0"
        self.assertInvalid(MANIFEST_SCHEMA, changed)
        with self.assertRaises(ContractError):
            ReadinessManifest(
                manifest_kind="SYNTHETIC_READINESS", semantic_subject_scope_hash=H,
                evaluation_as_of_at="2026-01-04T00:00:00Z", contract_version="2.0.0",
                rule_bundle_version="rules@1.0.0", entries=(), authorizations=dict(SAFETY_FLAGS),
            )

    def test_pseudo_calendar_assignment_is_rejected_before_schema_payload(self):
        class PseudoCalendarAssignment:
            def __init__(self):
                self.subject_id = "SYNTHETIC_RECORD_001"
                self.role = "MARKET"
                self.status = "NOT_APPLICABLE"
                self.calendar_reference_id = None
                self.calendar_version = None
                self.calendar_hash = None

        calendar_items = [CalendarAssignment("SYNTHETIC_RECORD_001", role, "NOT_APPLICABLE") for role in CALENDAR_ROLES]
        calendar_items[0] = PseudoCalendarAssignment()
        model = None
        schema_payload = None
        with self.assertRaises(ContractError):
            model = Observation(
                "SYNTHETIC_TEST_SOURCE", "SYNTHETIC_RECORD_001", "PRICE", "COPPER",
                "DAILY_MARKET", "2026-01-02", None, None, calendar_items,
            )
            schema_payload = model.content_projection()
        self.assertIsNone(model)
        self.assertIsNone(schema_payload)

    def test_pseudo_exclusion_reason_is_rejected_before_schema_payload(self):
        class PseudoExclusionReason:
            def __init__(self, source):
                self.code = source.code
                self.severity = source.severity
                self.blocking_scope = source.blocking_scope
                self.description = source.description
                self.remediation = source.remediation
                self.sort_order = source.sort_order

        reasons = list(exclusion_reasons())
        reasons[0] = PseudoExclusionReason(reasons[0])
        model = None
        schema_payload = None
        with self.assertRaises(ContractError):
            model = RuleBundle(
                rule_bundle_version="rules@1.0.0", contract_version="1.0.0",
                exclusion_catalog_version="1.1.0", exclusion_reasons=reasons, rules={},
            )
            schema_payload = model.content_projection()
        self.assertIsNone(model)
        self.assertIsNone(schema_payload)

    def test_every_model_schema_payload_round_trips(self):
        for schema_id, model in model_samples():
            with self.subTest(model=type(model).__name__):
                result = self.validator.validate(schema_id, model.content_projection())
                self.assertTrue(result.valid, result.as_dict())

    def test_model_and_schema_field_parity(self):
        common = self.validator._schemas["urn:c4-rd:schema:c4-common:1.0.0"]
        pairs = [
            (Observation, self.validator._schemas[OBSERVATION_SCHEMA]),
            (ObservationVersion, self.validator._schemas["urn:c4-rd:schema:c4-observation-version:1.0.0"]),
            (CalendarReference, self.validator._schemas["urn:c4-rd:schema:c4-calendar-reference:1.0.0"]),
            (AvailabilityAssessment, self.validator._schemas["urn:c4-rd:schema:c4-availability-assessment:1.0.0"]),
            (LineageAssessment, self.validator._schemas["urn:c4-rd:schema:c4-lineage-assessment:1.0.0"]),
            (TrustQualityAssessment, self.validator._schemas[TRUST_SCHEMA]),
            (PITEligibilityAssessment, self.validator._schemas["urn:c4-rd:schema:c4-pit-eligibility:1.0.0"]),
            (ResearchEligibilityAssessment, self.validator._schemas["urn:c4-rd:schema:c4-research-eligibility:1.0.0"]),
            (BacktestRecordEligibilityAssessment, self.validator._schemas["urn:c4-rd:schema:c4-backtest-eligibility:1.0.0"]),
            (BacktestDatasetEligibilityAssessment, self.validator._schemas["urn:c4-rd:schema:c4-backtest-eligibility:1.0.0"]),
            (RuleBundle, self.validator._schemas[RULE_SCHEMA]),
            (ReadinessManifest, self.validator._schemas["urn:c4-rd:schema:c4-readiness-manifest:1.0.0"]),
            (CalendarAssignment, common["$defs"]["calendarAssignment"]),
            (ExclusionReason, common["$defs"]["exclusionReason"]),
        ]
        for model_type, schema in pairs:
            with self.subTest(model=model_type.__name__):
                self.assertEqual(set(model_field_names(model_type)), set(schema["properties"]))
        for schema_id, model in model_samples():
            payload = model.content_projection()
            schema = self.validator._schemas[schema_id]
            self.assertTrue(set(schema["required"]).issubset(payload))
            self.assertEqual(set(payload), set(schema["properties"]))

    def test_derived_ids_are_consistently_external_to_schema_payloads(self):
        for _schema_id, model in model_samples():
            payload = model.content_projection()
            self.assertNotIn("content_hash", payload)
            if type(model).__name__ == "Observation":
                self.assertNotIn("observation_id", payload)
            if type(model).__name__ == "ObservationVersion":
                self.assertNotIn("observation_version_id", payload)
            if hasattr(model, "assessment_id"):
                self.assertNotIn("assessment_id", payload)
            if type(model).__name__ == "ReadinessManifest":
                self.assertNotIn("manifest_id", payload)

    def test_remote_reference_fails_closed_without_network_fetch(self):
        schema = deepcopy(self.validator._schemas[OBSERVATION_SCHEMA])
        schema["properties"]["source_period_type"]["$ref"] = "https://example.invalid/c4-rd-hostile.schema.json"
        with patch.dict(self.validator._schemas, {OBSERVATION_SCHEMA: schema}), \
             patch("socket.create_connection") as network:
            with self.assertRaisesRegex(SchemaValidationError, "local registry"):
                self.validator.validate(OBSERVATION_SCHEMA, observation())
            network.assert_not_called()

    def test_inert_export_flags_are_fixed_false(self):
        value = {
            "contract_version": "1.0.0", "request_id": "SYNTHETIC_EXPORT_REQUEST",
            "read_only": True, "manifest_hash": H, "shadow_export_allowed": False,
            "production_allowed": False, "canonical_allowed": False,
            "deferred_allowed": False, "procurement_allowed": False,
        }
        self.assertTrue(self.validator.validate(EXPORT_SCHEMA, value).valid)
        for key in ("shadow_export_allowed", "production_allowed", "canonical_allowed", "deferred_allowed", "procurement_allowed"):
            changed = deepcopy(value); changed[key] = True
            self.assertInvalid(EXPORT_SCHEMA, changed)
        changed = deepcopy(value); changed["read_only"] = False
        self.assertInvalid(EXPORT_SCHEMA, changed)


if __name__ == "__main__":
    unittest.main()
