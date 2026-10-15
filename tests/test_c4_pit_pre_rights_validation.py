"""Adversarial C4-PIT-2H-R1 disposable persistence validation tests."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from uuid import uuid4

import duckdb

from scripts.c4_pit_evidence_contract import (
    DAY_GRANULAR_POLICY_VERSION,
    STRICT_INTRADAY_POLICY_VERSION,
    EvidenceArtifact,
    EvidenceContractError,
    TemporalBound,
    evaluate_temporal_eligibility,
    select_visible_vintage,
    validate_artifact_consistency,
    validate_revision_lineage,
)
from scripts.c4_pit_pre_rights_validation import (
    APPROVED_MIGRATION_CHECKSUM,
    DATASET_RIGHTS_PROFILE,
    DATASET_SOURCE_CHANNEL,
    DATABASE_CLASSIFICATION,
    EXPORT_ALLOWED,
    MIGRATION_ID,
    PDF_RIGHTS_PROFILE,
    PDF_SOURCE_CHANNEL,
    PUBLICATION_ALLOWED,
    REPOSITORY_BASE_COMMIT_SHA,
    PUBLIC_API_ALLOWED,
    REDISTRIBUTION_ALLOWED,
    SCHEMA_VERSION,
    SYNTHETIC_RIGHTS_PROFILE,
    SYNTHETIC_SOURCE_CHANNEL,
    VALIDATION_CONTRACT_VERSION,
    VALIDATION_DATABASE_ROOT,
    WORLD_BANK_COPPER_RECORD_COUNT,
    WORLD_BANK_SOURCE_PATH,
    WORLD_BANK_SOURCE_SHA256,
    IntegrityValidationError,
    PreRightsValidationDatabase,
    RightsGateBlocked,
    RightsGateDecision,
    ValidationConflictError,
    ValidationDatabasePathError,
    ValidationMigrationError,
    _json,
    _validation_hash,
    acquisition_manifest_identity,
    build_synthetic_2024_02_case,
    expected_schema_fingerprint,
    migration_checksum,
    migration_sql_bytes,
)
from scripts.c4_rd_contract import (
    BACKTEST_ENABLED_SOURCES,
    PIT_ENABLED_SOURCES,
    RESEARCH_ENABLED_SOURCES,
    SAFETY_FLAGS,
    canonical_json_bytes,
    parse_json_strict,
)


OLD_PRE_R1_CHECKSUM = "77ae6b69145e70e2dac450e20a02bd3ec34f301bbb35caf259d7fbac0643180b"


class ValidationDatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        VALIDATION_DATABASE_ROOT.mkdir(parents=True, exist_ok=True)
        self.database_path = VALIDATION_DATABASE_ROOT / f"r1_{uuid4().hex}.duckdb"
        self.database = PreRightsValidationDatabase(self.database_path)
        self.database.initialize_schema(
            applied_at="2026-10-08T00:00:00Z",
            code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
        )

    def tearDown(self) -> None:
        if hasattr(self, "database"):
            del self.database
        for path in (self.database_path, Path(f"{self.database_path}.wal")):
            if path.exists():
                path.unlink()

    def reopen(self) -> PreRightsValidationDatabase:
        del self.database
        self.database = PreRightsValidationDatabase(self.database_path)
        self.database.initialize_schema(
            applied_at="2026-10-09T00:00:00Z",
            code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
        )
        return self.database

    def assessment(
        self,
        *,
        source_channel: str = SYNTHETIC_SOURCE_CHANNEL,
        rights_profile_id: str = SYNTHETIC_RIGHTS_PROFILE,
        activity: str = "SYNTHETIC_MATERIALIZATION",
        assessed_at: str = "2026-10-08T00:00:01Z",
    ) -> str:
        return self.database.record_rights_assessment(
            source_channel=source_channel,
            rights_profile_id=rights_profile_id,
            activity=activity,
            assessed_at=assessed_at,
        )

    def authorized_run(
        self,
        *,
        run_key: str = "AUTHORIZED_TEST_RUN",
        planned_at: str = "2026-10-08T00:00:00Z",
    ) -> str:
        assessment_id = self.assessment()
        return self.database.create_acquisition_run(
            run_key=run_key,
            planned_at=planned_at,
            source_channel=SYNTHETIC_SOURCE_CHANNEL,
            rights_profile_id=SYNTHETIC_RIGHTS_PROFILE,
            rights_assessment_id=assessment_id,
            authorized_activity="SYNTHETIC_MATERIALIZATION",
        )

    def extra_path(self, label: str) -> Path:
        return VALIDATION_DATABASE_ROOT / f"{label}_{uuid4().hex}.duckdb"

    @staticmethod
    def remove_db(path: Path) -> None:
        for candidate in (path, Path(f"{path}.wal")):
            if candidate.exists():
                candidate.unlink()


class MigrationAndDatabaseSafetyTests(ValidationDatabaseTestCase):
    def test_fresh_database_and_valid_reopen_verify_checksum_sentinel_and_catalog(self) -> None:
        self.assertEqual(migration_checksum(), APPROVED_MIGRATION_CHECKSUM)
        self.assertNotEqual(APPROVED_MIGRATION_CHECKSUM, OLD_PRE_R1_CHECKSUM)
        reopened = PreRightsValidationDatabase(self.database_path)
        self.assertEqual(
            reopened.initialize_schema(
                applied_at="2026-10-09T00:00:00Z",
                code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
            ),
            APPROVED_MIGRATION_CHECKSUM,
        )
        self.assertEqual(len(reopened.table_names()), 26)
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            row = connection.execute(
                "SELECT database_purpose, schema_contract, schema_version, migration_id, "
                "migration_checksum, schema_fingerprint FROM schema_migrations"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            row,
            (
                DATABASE_CLASSIFICATION,
                VALIDATION_CONTRACT_VERSION,
                SCHEMA_VERSION,
                MIGRATION_ID,
                APPROVED_MIGRATION_CHECKSUM,
                expected_schema_fingerprint(),
            ),
        )

    def _assert_sentinel_tamper_rejected(
        self, column: str, value: str, *, label: str
    ) -> None:
        path = self.extra_path(label)
        adapter = PreRightsValidationDatabase(path)
        adapter.initialize_schema(
            applied_at="2026-10-08T00:00:00Z",
            code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
        )
        connection = duckdb.connect(str(path))
        connection.execute(f"UPDATE schema_migrations SET {column}=?", [value])
        connection.close()
        del adapter
        try:
            reopened = PreRightsValidationDatabase(path)
            with self.assertRaises(ValidationMigrationError):
                reopened.initialize_schema(
                    applied_at="2026-10-09T00:00:00Z",
                    code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
                )
        finally:
            self.remove_db(path)

    def test_sentinel_created_at_tamper_rejected_on_fresh_reopen(self) -> None:
        for label, value in (
            ("malformed", "NOT_A_TIMESTAMP"),
            ("noncanonical", "2026-10-08T08:00:00+08:00"),
        ):
            with self.subTest(label=label):
                self._assert_sentinel_tamper_rejected(
                    "created_at", value, label=f"created_at_{label}"
                )

    def test_sentinel_applied_at_tamper_rejected_on_fresh_reopen(self) -> None:
        for label, value in (
            ("malformed", "NOT_A_TIMESTAMP"),
            ("precedes_created", "2026-10-07T23:59:59Z"),
        ):
            with self.subTest(label=label):
                self._assert_sentinel_tamper_rejected(
                    "applied_at", value, label=f"applied_at_{label}"
                )

    def test_sentinel_code_commit_sha_tamper_rejected_on_fresh_reopen(self) -> None:
        for label, value in (
            ("malformed", "NOT_A_GIT_SHA"),
            ("unexpected_full_sha", "0" * 40),
        ):
            with self.subTest(label=label):
                self._assert_sentinel_tamper_rejected(
                    "code_commit_sha", value, label=f"code_sha_{label}"
                )
        with self.assertRaisesRegex(
            ValidationMigrationError, "authorized repository base commit"
        ):
            self.database.initialize_schema(
                applied_at="2026-10-09T00:00:00Z", code_commit_sha="f" * 40
            )

    def test_windows_junction_escape_is_rejected(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows junction regression is Windows-specific")
        link = VALIDATION_DATABASE_ROOT / f"junction_{uuid4().hex}"
        target = VALIDATION_DATABASE_ROOT.parent / f"junction_target_{uuid4().hex}"
        target.mkdir(parents=False)
        creation = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            check=False,
        )
        try:
            self.assertEqual(creation.returncode, 0, creation.stderr or creation.stdout)
            with self.assertRaises(ValidationDatabasePathError):
                PreRightsValidationDatabase(link / "escaped.duckdb")
        finally:
            if link.exists():
                link.rmdir()
            if target.exists():
                target.rmdir()

    def test_partial_migration_failure_rolls_back_all_schema_objects(self) -> None:
        path = self.extra_path("partial_migration")
        adapter = PreRightsValidationDatabase(path)
        try:
            with patch.object(
                PreRightsValidationDatabase,
                "_seed_controls",
                side_effect=ValidationMigrationError("INJECTED_PARTIAL_MIGRATION_FAILURE"),
            ), self.assertRaisesRegex(
                ValidationMigrationError, "INJECTED_PARTIAL_MIGRATION_FAILURE"
            ):
                adapter.initialize_schema(
                    applied_at="2026-10-08T00:00:00Z",
                    code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
                )
            connection = duckdb.connect(str(path), read_only=True)
            try:
                tables = connection.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='main'"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(tables, [])
        finally:
            del adapter
            self.remove_db(path)

    def test_arbitrary_neutral_path_outside_allowlisted_root_is_rejected(self) -> None:
        outside = VALIDATION_DATABASE_ROOT.parent / "neutral-outside" / "neutral.duckdb"
        outside.parent.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(ValidationDatabasePathError):
            PreRightsValidationDatabase(outside)
        self.assertFalse(outside.exists())

    def test_path_traversal_and_non_duckdb_suffix_are_rejected(self) -> None:
        with self.assertRaises(ValidationDatabasePathError):
            PreRightsValidationDatabase(VALIDATION_DATABASE_ROOT / ".." / "escape.duckdb")
        with self.assertRaises(ValidationDatabasePathError):
            PreRightsValidationDatabase(VALIDATION_DATABASE_ROOT / "validation.db")

    def test_authoritative_private_style_names_remain_rejected_inside_root(self) -> None:
        for name in (
            "production.duckdb",
            "authoritative.duckdb",
            "private_research.duckdb",
            "canonical.duckdb",
            "db2.duckdb",
        ):
            with self.subTest(name=name), self.assertRaises(ValidationDatabasePathError):
                PreRightsValidationDatabase(VALIDATION_DATABASE_ROOT / name)

    def test_reserved_token_inside_disposable_identifier_is_not_a_false_positive(self) -> None:
        path = VALIDATION_DATABASE_ROOT / "r1_a1db2f09.duckdb"
        adapter = PreRightsValidationDatabase(path)
        self.assertEqual(adapter.database_path, path.resolve())

    def test_existing_unrelated_database_inside_root_is_not_adopted_or_mutated(self) -> None:
        path = self.extra_path("unrelated")
        connection = duckdb.connect(str(path))
        connection.execute("CREATE TABLE unrelated_only(value INTEGER)")
        connection.close()
        try:
            adapter = PreRightsValidationDatabase(path)
            with self.assertRaises(ValidationMigrationError):
                adapter.initialize_schema(
                    applied_at="2026-10-08T00:00:00Z",
                    code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
                )
            connection = duckdb.connect(str(path), read_only=True)
            try:
                tables = connection.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(tables, [("unrelated_only",)])
        finally:
            self.remove_db(path)

    def test_marker_only_forgery_is_rejected(self) -> None:
        path = self.extra_path("marker_only")
        marker_sql = migration_sql_bytes().decode("utf-8").split(
            "CREATE TABLE system_policy", 1
        )[0]
        connection = duckdb.connect(str(path))
        connection.execute(marker_sql)
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                MIGRATION_ID,
                SCHEMA_VERSION,
                DATABASE_CLASSIFICATION,
                VALIDATION_CONTRACT_VERSION,
                APPROVED_MIGRATION_CHECKSUM,
                expected_schema_fingerprint(),
                "2026-10-08T00:00:00Z",
                "2026-10-08T00:00:00Z",
                "FORGED",
            ],
        )
        connection.close()
        try:
            with self.assertRaises(ValidationMigrationError):
                PreRightsValidationDatabase(path).initialize_schema(
                    applied_at="2026-10-08T00:00:00Z",
                    code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
                )
        finally:
            self.remove_db(path)

    def test_valid_sentinel_with_missing_table_is_rejected(self) -> None:
        connection = duckdb.connect(str(self.database_path))
        connection.execute("DROP TABLE dataset_observation")
        connection.close()
        with self.assertRaises(ValidationMigrationError):
            self.database.initialize_schema(
                applied_at="2026-10-08T00:00:00Z",
                code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
            )

    def _forged_schema(self, replacement_from: str, replacement_to: str) -> Path:
        path = self.extra_path("forged_schema")
        sql = migration_sql_bytes().decode("utf-8")
        self.assertIn(replacement_from, sql)
        connection = duckdb.connect(str(path))
        connection.execute(sql.replace(replacement_from, replacement_to, 1))
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                MIGRATION_ID,
                SCHEMA_VERSION,
                DATABASE_CLASSIFICATION,
                VALIDATION_CONTRACT_VERSION,
                APPROVED_MIGRATION_CHECKSUM,
                expected_schema_fingerprint(),
                "2026-10-08T00:00:00Z",
                "2026-10-08T00:00:00Z",
                "FORGED",
            ],
        )
        connection.close()
        return path

    def test_valid_sentinel_with_altered_column_is_rejected(self) -> None:
        path = self._forged_schema(
            "    value DECIMAL(38, 12) NOT NULL,", "    value VARCHAR NOT NULL,"
        )
        try:
            with self.assertRaises(ValidationMigrationError):
                PreRightsValidationDatabase(path).initialize_schema(
                    applied_at="2026-10-08T00:00:00Z",
                    code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
                )
        finally:
            self.remove_db(path)

    def test_valid_sentinel_with_altered_check_constraint_is_rejected(self) -> None:
        path = self._forged_schema(
            "CHECK (data_classification = 'REAL_DATASET_DERIVED')",
            "CHECK (data_classification IN ('REAL_DATASET_DERIVED', 'FORGED'))",
        )
        try:
            with self.assertRaises(ValidationMigrationError):
                PreRightsValidationDatabase(path).initialize_schema(
                    applied_at="2026-10-08T00:00:00Z",
                    code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
                )
        finally:
            self.remove_db(path)

    def test_valid_sentinel_with_altered_foreign_key_is_rejected(self) -> None:
        path = self._forged_schema(
            "rights_assessment_id VARCHAR NOT NULL REFERENCES rights_assessment(rights_assessment_id),",
            "rights_assessment_id VARCHAR NOT NULL REFERENCES rights_profile(rights_profile_id),",
        )
        try:
            with self.assertRaises(ValidationMigrationError):
                PreRightsValidationDatabase(path).initialize_schema(
                    applied_at="2026-10-08T00:00:00Z",
                    code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
                )
        finally:
            self.remove_db(path)

    def test_old_disposable_checksum_is_recreate_required(self) -> None:
        connection = duckdb.connect(str(self.database_path))
        connection.execute(
            "UPDATE schema_migrations SET migration_checksum=?", [OLD_PRE_R1_CHECKSUM]
        )
        connection.close()
        with self.assertRaises(ValidationMigrationError):
            self.database.initialize_schema(
                applied_at="2026-10-08T00:00:00Z",
                code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
            )

    def test_relational_and_public_export_constraints_fail_closed(self) -> None:
        connection = duckdb.connect(str(self.database_path))
        try:
            with self.assertRaises(duckdb.ConstraintException):
                connection.execute("UPDATE system_policy SET publication_allowed=TRUE")
            with self.assertRaises(duckdb.ConstraintException):
                connection.execute(
                    "INSERT INTO temporal_bound VALUES ('bad', 'UNKNOWN', 'UNKNOWN', NULL, NULL, NULL, '{}')"
                )
        finally:
            connection.close()

    def test_controls_and_operational_flags_remain_frozen(self) -> None:
        self.assertEqual(DATABASE_CLASSIFICATION, "C4_PIT_PRE_RIGHTS_VALIDATION_DB")
        self.assertFalse(PUBLICATION_ALLOWED)
        self.assertFalse(PUBLIC_API_ALLOWED)
        self.assertFalse(EXPORT_ALLOWED)
        self.assertFalse(REDISTRIBUTION_ALLOWED)
        self.assertTrue(all(value is False for value in SAFETY_FLAGS.values()))
        self.assertEqual(PIT_ENABLED_SOURCES, ())
        self.assertEqual(RESEARCH_ENABLED_SOURCES, ())
        self.assertEqual(BACKTEST_ENABLED_SOURCES, ())


class RightsIntegrityTests(ValidationDatabaseTestCase):
    def test_missing_review_required_and_channel_profile_mismatch_are_blocked(self) -> None:
        missing = self.database.evaluate_rights(
            source_channel=SYNTHETIC_SOURCE_CHANNEL,
            rights_profile_id=None,
            activity="SYNTHETIC_MATERIALIZATION",
        )
        review = self.database.evaluate_rights(
            source_channel=PDF_SOURCE_CHANNEL,
            rights_profile_id=PDF_RIGHTS_PROFILE,
            activity="REAL_PDF_RETRIEVAL",
        )
        xlsx_pdf = self.database.evaluate_rights(
            source_channel=PDF_SOURCE_CHANNEL,
            rights_profile_id=DATASET_RIGHTS_PROFILE,
            activity="REAL_PDF_RETRIEVAL",
        )
        synthetic_pdf = self.database.evaluate_rights(
            source_channel=PDF_SOURCE_CHANNEL,
            rights_profile_id=SYNTHETIC_RIGHTS_PROFILE,
            activity="REAL_PDF_RETRIEVAL",
        )
        self.assertEqual(missing.decision, "BLOCKED_DISABLED")
        self.assertEqual(review.decision, "BLOCKED_REVIEW_REQUIRED")
        self.assertFalse(xlsx_pdf.passed)
        self.assertFalse(synthetic_pdf.passed)

    def test_xlsx_and_synthetic_profiles_pass_only_their_exact_activities(self) -> None:
        self.assertTrue(
            self.database.evaluate_rights(
                source_channel=DATASET_SOURCE_CHANNEL,
                rights_profile_id=DATASET_RIGHTS_PROFILE,
                activity="DATASET_READ_ONLY_IMPORT",
            ).passed
        )
        self.assertTrue(
            self.database.evaluate_rights(
                source_channel=SYNTHETIC_SOURCE_CHANNEL,
                rights_profile_id=SYNTHETIC_RIGHTS_PROFILE,
                activity="SYNTHETIC_MATERIALIZATION",
            ).passed
        )

    def test_caller_forged_pass_decision_is_rejected(self) -> None:
        forged = RightsGateDecision(
            "SYNTHETIC_MATERIALIZATION",
            "PASS",
            DATASET_RIGHTS_PROFILE,
            SYNTHETIC_SOURCE_CHANNEL,
            "CALLER_ASSERTED",
        )
        with self.assertRaises(RightsGateBlocked):
            self.database.record_rights_assessment(
                assessed_at="2026-10-08T00:00:01Z", decision=forged
            )

    def test_run_requires_exact_persisted_pass_assessment(self) -> None:
        with self.assertRaises(RightsGateBlocked):
            self.database.create_acquisition_run(
                run_key="MISSING_ASSESSMENT",
                planned_at="2026-10-08T00:00:00Z",
                source_channel=SYNTHETIC_SOURCE_CHANNEL,
                rights_profile_id=SYNTHETIC_RIGHTS_PROFILE,
                rights_assessment_id="0" * 64,
                authorized_activity="SYNTHETIC_MATERIALIZATION",
            )

    def test_run_channel_profile_assessment_mismatch_is_rejected(self) -> None:
        assessment_id = self.assessment()
        with self.assertRaises(RightsGateBlocked):
            self.database.create_acquisition_run(
                run_key="PDF_MISMATCH",
                planned_at="2026-10-08T00:00:00Z",
                source_channel=PDF_SOURCE_CHANNEL,
                rights_profile_id=SYNTHETIC_RIGHTS_PROFILE,
                rights_assessment_id=assessment_id,
                authorized_activity="SYNTHETIC_MATERIALIZATION",
            )

    def test_direct_sql_mismatched_run_is_blocked_by_composite_fk(self) -> None:
        assessment_id = self.assessment()
        connection = duckdb.connect(str(self.database_path))
        try:
            with self.assertRaises(duckdb.ConstraintException):
                connection.execute(
                    "INSERT INTO acquisition_run VALUES (?, 'SYNTHETIC_VALIDATION', ?, ?, '1.0.0', ?, "
                    "'SYNTHETIC_MATERIALIZATION', ?, '2026-10-08T00:00:00Z', '{}')",
                    [
                        "1" * 64,
                        PDF_SOURCE_CHANNEL,
                        SYNTHETIC_RIGHTS_PROFILE,
                        assessment_id,
                        "SYNTHETIC_DISPOSABLE_RESTRICTED@1.0.0",
                    ],
                )
        finally:
            connection.close()

    def test_later_blocked_assessment_deactivates_existing_trust(self) -> None:
        result = self.database.persist_synthetic_e2e(build_synthetic_2024_02_case())
        self.assertEqual(self.database.active_trusted_collection_ids(), (result.trusted_collection_id,))
        blocked_projection = {
            "source_channel": SYNTHETIC_SOURCE_CHANNEL,
            "activity_status": "DISABLED",
            "rights_profile_id": SYNTHETIC_RIGHTS_PROFILE,
            "profile_version": "1.0.0",
        }
        connection = duckdb.connect(str(self.database_path))
        connection.execute(
            "UPDATE source_allowlist SET activity_status='DISABLED', allowlist_json=? "
            "WHERE source_channel=?",
            [canonical_json_bytes(blocked_projection).decode("utf-8"), SYNTHETIC_SOURCE_CHANNEL],
        )
        connection.close()
        self.database.record_rights_assessment(
            source_channel=SYNTHETIC_SOURCE_CHANNEL,
            rights_profile_id=SYNTHETIC_RIGHTS_PROFILE,
            activity="SYNTHETIC_MATERIALIZATION",
            assessed_at="2026-10-08T03:00:00Z",
        )
        self.assertFalse(self.database.validate_integrity().rights_valid)
        self.assertEqual(self.database.active_trusted_collection_ids(), ())

    def test_ambiguous_same_time_current_assessment_is_prevented(self) -> None:
        assessment_id = self.assessment()
        connection = duckdb.connect(str(self.database_path))
        try:
            with self.assertRaises(duckdb.ConstraintException):
                connection.execute(
                    "INSERT INTO rights_assessment SELECT ?, rights_profile_id, profile_version, "
                    "source_channel, assessed_activity, decision, assessed_at, assessment_json "
                    "FROM rights_assessment WHERE rights_assessment_id=?",
                    ["2" * 64, assessment_id],
                )
        finally:
            connection.close()

    def test_publication_and_export_always_fail_closed(self) -> None:
        for activity in ("PUBLICATION", "EXPORT"):
            with self.subTest(activity=activity):
                result = self.database.evaluate_rights(
                    source_channel=SYNTHETIC_SOURCE_CHANNEL,
                    rights_profile_id=SYNTHETIC_RIGHTS_PROFILE,
                    activity=activity,
                )
                self.assertEqual(result.decision, "BLOCKED_PUBLIC_POLICY")


class AcquisitionStateMachineTests(ValidationDatabaseTestCase):
    def _persist_direct_running_event(self, run_id: str, occurred_at: str) -> None:
        event = {
            "run_state": "RUNNING",
            "occurred_at": occurred_at,
            "reason": "DIRECT_SQL_TIMESTAMP_ORDERING_PROBE",
        }
        connection = duckdb.connect(str(self.database_path))
        try:
            connection.execute(
                "INSERT INTO acquisition_run_event VALUES (?, 1, 'RUNNING', ?, ?)",
                [run_id, occurred_at, _json(event)],
            )
        finally:
            connection.close()

    def test_fractional_20_1_to_20_is_retrograde_for_append_and_replay(self) -> None:
        run_id = self.authorized_run(
            run_key="FRACTIONAL_RETROGRADE_TENTH", planned_at="2026-10-08T01:00:20.1Z"
        )
        with self.assertRaisesRegex(ValidationConflictError, "timestamp is retrograde"):
            self.database.transition_run(
                run_id,
                "RUNNING",
                occurred_at="2026-10-08T01:00:20Z",
                reason="RETROGRADE",
            )
        self._persist_direct_running_event(run_id, "2026-10-08T01:00:20Z")
        self.reopen()
        with self.assertRaisesRegex(IntegrityValidationError, "timestamps are not canonical/ordered"):
            self.database.validate_integrity()
        self.assertEqual(self.database.active_trusted_collection_ids(), ())

    def test_fractional_20_01_to_20_001_is_retrograde_for_append_and_replay(self) -> None:
        run_id = self.authorized_run(
            run_key="FRACTIONAL_RETROGRADE_HUNDREDTH",
            planned_at="2026-10-08T01:00:20.01Z",
        )
        with self.assertRaisesRegex(ValidationConflictError, "timestamp is retrograde"):
            self.database.transition_run(
                run_id,
                "RUNNING",
                occurred_at="2026-10-08T01:00:20.001Z",
                reason="RETROGRADE",
            )
        self._persist_direct_running_event(run_id, "2026-10-08T01:00:20.001Z")
        self.reopen()
        with self.assertRaisesRegex(IntegrityValidationError, "timestamps are not canonical/ordered"):
            self.database.validate_integrity()
        self.assertEqual(self.database.active_trusted_collection_ids(), ())

    def test_fractional_20_to_20_1_is_valid_for_append_and_replay(self) -> None:
        run_id = self.authorized_run(
            run_key="FRACTIONAL_FORWARD_TENTH", planned_at="2026-10-08T01:00:20Z"
        )
        self.database.transition_run(
            run_id,
            "RUNNING",
            occurred_at="2026-10-08T01:00:20.1Z",
            reason="FORWARD",
        )
        self.reopen()
        self.database.validate_integrity()
        self.assertEqual(self.database.latest_run_state(run_id), "RUNNING")

    def test_fractional_20_001_to_20_01_is_valid_for_append_and_replay(self) -> None:
        run_id = self.authorized_run(
            run_key="FRACTIONAL_FORWARD_HUNDREDTH",
            planned_at="2026-10-08T01:00:20.001Z",
        )
        self.database.transition_run(
            run_id,
            "RUNNING",
            occurred_at="2026-10-08T01:00:20.01Z",
            reason="FORWARD",
        )
        self.reopen()
        self.database.validate_integrity()
        self.assertEqual(self.database.latest_run_state(run_id), "RUNNING")

    def test_fractional_retrograde_complete_tamper_is_untrusted_after_restart(self) -> None:
        result = self.database.persist_synthetic_e2e(build_synthetic_2024_02_case())
        event = {
            "run_state": "RUNNING",
            "occurred_at": "2026-10-08T01:00:20.1Z",
            "reason": "RIGHTS_PASS_AND_RUN_STARTED",
        }
        connection = duckdb.connect(str(self.database_path))
        try:
            connection.execute(
                "UPDATE acquisition_run_event SET occurred_at=?, event_json=? "
                "WHERE acquisition_run_id=? AND event_ordinal=1",
                [event["occurred_at"], _json(event), result.acquisition_run_id],
            )
        finally:
            connection.close()
        self.reopen()
        with self.assertRaisesRegex(IntegrityValidationError, "timestamps are not canonical/ordered"):
            self.database.validate_integrity()
        self.assertEqual(self.database.active_trusted_collection_ids(), ())

    def test_valid_fractional_synthetic_e2e_remains_active_after_restart(self) -> None:
        result = self.database.persist_synthetic_e2e(
            build_synthetic_2024_02_case(),
            run_key="VALID_FRACTIONAL_SYNTHETIC_E2E",
            planned_at="2026-10-08T01:00:00.1Z",
            assessed_at="2026-10-08T01:00:01.01Z",
        )
        self.reopen()
        self.database.validate_integrity()
        self.assertEqual(
            self.database.active_trusted_collection_ids(), (result.trusted_collection_id,)
        )

    def test_legal_history_and_idempotent_invalidation(self) -> None:
        run_id = self.database.persist_synthetic_e2e(
            build_synthetic_2024_02_case()
        ).acquisition_run_id
        first = self.database.transition_run(
            run_id, "INVALIDATED", occurred_at="2026-10-08T02:00:00Z", reason="ROLLBACK"
        )
        second = self.database.transition_run(
            run_id, "INVALIDATED", occurred_at="2026-10-08T02:00:01Z", reason="REPEAT"
        )
        self.assertEqual(first, second)
        self.assertEqual(self.database.latest_run_state(run_id), "INVALIDATED")

    def test_failed_blocked_and_invalidated_are_not_revivable(self) -> None:
        scenarios = (("FAILED", "COMPLETE"), ("BLOCKED_RIGHTS", "COMPLETE"))
        for terminal, attempted in scenarios:
            with self.subTest(terminal=terminal):
                run_id = self.authorized_run(run_key=f"STATE_{terminal}")
                self.database.transition_run(
                    run_id,
                    terminal,
                    occurred_at="2026-10-08T00:00:02Z",
                    reason="TEST",
                )
                with self.assertRaises(ValidationConflictError):
                    self.database.transition_run(
                        run_id,
                        attempted,
                        occurred_at="2026-10-08T00:00:03Z",
                        reason="ILLEGAL",
                    )

    def test_running_and_partial_have_no_trust(self) -> None:
        run_id = self.authorized_run()
        self.database.transition_run(
            run_id, "RUNNING", occurred_at="2026-10-08T00:00:02Z", reason="START"
        )
        self.assertEqual(self.database.active_trusted_collection_ids(), ())
        self.database.transition_run(
            run_id, "PARTIAL", occurred_at="2026-10-08T00:00:03Z", reason="PARTIAL"
        )
        self.assertEqual(self.database.active_trusted_collection_ids(), ())

    def test_public_transition_run_cannot_complete(self) -> None:
        run_id = self.authorized_run(run_key="PUBLIC_COMPLETE_REJECTED")
        self.database.transition_run(
            run_id, "RUNNING", occurred_at="2026-10-08T00:00:02Z", reason="START"
        )
        with self.assertRaisesRegex(
            ValidationConflictError, "validated run-specific completion proof"
        ):
            self.database.transition_run(
                run_id,
                "COMPLETE",
                occurred_at="2026-10-08T00:00:03Z",
                reason="CALLER_ASSERTED_DONE",
            )
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM acquisition_run_event "
                    "WHERE acquisition_run_id=? AND run_state='COMPLETE'",
                    [run_id],
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()
        self.reopen()
        self.assertEqual(self.database.latest_run_state(run_id), "RUNNING")
        self.assertEqual(self.database.active_trusted_collection_ids(), ())

    def test_direct_sql_incomplete_complete_is_rejected_after_restart(self) -> None:
        run_id = self.authorized_run(run_key="DIRECT_SQL_INCOMPLETE_COMPLETE")
        self.database.transition_run(
            run_id, "RUNNING", occurred_at="2026-10-08T00:00:02Z", reason="START"
        )
        event = {
            "run_state": "COMPLETE",
            "occurred_at": "2026-10-08T00:00:03Z",
            "reason": "DIRECT_SQL_NO_COMPLETION_PROOF",
        }
        connection = duckdb.connect(str(self.database_path))
        connection.execute(
            "INSERT INTO acquisition_run_event VALUES (?, 2, 'COMPLETE', ?, ?)",
            [run_id, event["occurred_at"], _json(event)],
        )
        connection.close()
        self.reopen()
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM acquisition_run_event "
                    "WHERE acquisition_run_id=? AND run_state='COMPLETE'",
                    [run_id],
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()
        with self.assertRaisesRegex(
            IntegrityValidationError, "lacks validated trusted-promotion proof"
        ):
            self.database.validate_integrity()
        self.assertEqual(self.database.active_trusted_collection_ids(), ())

    def test_direct_sql_invalidated_to_complete_is_detected_and_untrusted(self) -> None:
        result = self.database.persist_synthetic_e2e(build_synthetic_2024_02_case())
        self.database.transition_run(
            result.acquisition_run_id,
            "INVALIDATED",
            occurred_at="2026-10-08T03:00:00Z",
            reason="ROLLBACK",
        )
        event = {
            "run_state": "COMPLETE",
            "occurred_at": "2026-10-08T03:00:01Z",
            "reason": "DIRECT_SQL_REVIVAL",
        }
        connection = duckdb.connect(str(self.database_path))
        connection.execute(
            "INSERT INTO acquisition_run_event VALUES (?, 4, 'COMPLETE', ?, ?)",
            [result.acquisition_run_id, event["occurred_at"], _json(event)],
        )
        connection.close()
        with self.assertRaises(IntegrityValidationError):
            self.database.validate_integrity()
        self.assertEqual(self.database.active_trusted_collection_ids(), ())


class SyntheticWorkflowAndQueryTests(ValidationDatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.case = build_synthetic_2024_02_case()
        self.result = self.database.persist_synthetic_e2e(self.case)

    def test_complete_workflow_replays_and_derives_active_trust(self) -> None:
        replay = self.database.validate_integrity()
        self.assertTrue(replay.content_hashes_valid)
        self.assertTrue(replay.manifest_hashes_valid)
        self.assertTrue(replay.lineage_valid)
        self.assertTrue(replay.rights_valid)
        self.assertTrue(replay.trusted_membership_valid)
        self.assertEqual(replay.selected_vintage_claim_id, self.case.vintages[1].vintage_claim_id)
        self.assertEqual(self.database.latest_run_state(self.result.acquisition_run_id), "COMPLETE")
        self.assertEqual(self.database.active_trusted_collection_ids(), (self.result.trusted_collection_id,))

    def test_exact_repeat_is_idempotent(self) -> None:
        self.assertEqual(self.database.persist_synthetic_e2e(self.case), self.result)
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM evidence_artifact").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM trusted_collection").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM acquisition_run_event").fetchone()[0], 3)
        finally:
            connection.close()

    def test_manifest_is_order_independent_and_membership_sensitive(self) -> None:
        provenance = self.database.provenance_for_vintage(self.case.vintages[0].vintage_claim_id)
        arguments = {
            "acquisition_run_id": self.result.acquisition_run_id,
            "rights_profile_id": SYNTHETIC_RIGHTS_PROFILE,
            "rights_profile_version": "1.0.0",
            "rights_assessment_id": provenance["rights_assessment_id"],
        }
        first, first_projection = acquisition_manifest_identity(
            **arguments, artifacts=self.case.artifacts
        )
        reordered, reordered_projection = acquisition_manifest_identity(
            **arguments, artifacts=tuple(reversed(self.case.artifacts))
        )
        changed, _ = acquisition_manifest_identity(
            **arguments, artifacts=self.case.artifacts[:1]
        )
        self.assertEqual(first, self.result.manifest_hash)
        self.assertEqual(reordered, first)
        self.assertEqual(reordered_projection, first_projection)
        self.assertNotEqual(changed, first)

    def test_history_and_exact_provenance_queries(self) -> None:
        history = self.database.copper_history()
        self.assertEqual([row["value"] for row in history], [Decimal("8300"), Decimal("8305")])
        provenance = self.database.provenance_for_vintage(self.case.vintages[1].vintage_claim_id)
        self.assertEqual(provenance["manifest_id"], self.result.manifest_id)
        self.assertEqual(provenance["audit_reference_id"], self.result.audit_reference_id)

    def test_provenance_query_rejects_multiple_authoritative_chains(self) -> None:
        connection = duckdb.connect(str(self.database_path))
        row = connection.execute(
            "SELECT acquisition_run_id, manifest_id, rights_assessment_id, audit_reference_id, "
            "replay_fingerprint, collection_json FROM trusted_collection"
        ).fetchone()
        projection = parse_json_strict(row[5])
        projection["promoted_at"] = "2026-10-08T01:00:21Z"
        second_id = _validation_hash("TRUSTED_COLLECTION", projection)
        connection.execute(
            "INSERT INTO trusted_collection VALUES (?, ?, ?, ?, ?, ?, ?, 'SYNTHETIC_VALIDATED', ?, ?)",
            [second_id, second_id, *row[:5], projection["promoted_at"], _json(projection)],
        )
        members = connection.execute(
            "SELECT membership_ordinal, artifact_id, claim_id, binding_id, vintage_claim_id, "
            "audit_reference_id FROM trusted_collection_membership ORDER BY membership_ordinal"
        ).fetchall()
        for ordinal, artifact_id, claim_id, binding_id, vintage_id, audit_id in members:
            member = {
                "artifact_id": artifact_id,
                "claim_id": claim_id,
                "binding_id": binding_id,
                "vintage_claim_id": vintage_id,
                "audit_reference_id": audit_id,
            }
            connection.execute(
                "INSERT INTO trusted_collection_membership VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    second_id,
                    ordinal,
                    artifact_id,
                    claim_id,
                    binding_id,
                    vintage_id,
                    audit_id,
                    _validation_hash("TRUSTED_COLLECTION_MEMBERSHIP", member),
                ],
            )
        connection.close()
        self.database.validate_integrity()
        with self.assertRaises(IntegrityValidationError):
            self.database.provenance_for_vintage(self.case.vintages[0].vintage_claim_id)

    def test_restart_recomputes_every_persisted_layer(self) -> None:
        del self.database
        reopened = PreRightsValidationDatabase(self.database_path)
        reopened.initialize_schema(
            applied_at="2026-10-09T00:00:00Z",
            code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
        )
        replay = reopened.validate_integrity()
        self.assertEqual(replay.selected_vintage_claim_id, self.case.vintages[1].vintage_claim_id)
        self.assertEqual(reopened.active_trusted_collection_ids(), (self.result.trusted_collection_id,))
        self.assertEqual(
            reopened.provenance_for_vintage(replay.selected_vintage_claim_id)["manifest_id"],
            self.result.manifest_id,
        )

    def test_invalidation_preserves_history_and_removes_active_trust(self) -> None:
        before = self.database.provenance_for_vintage(self.case.vintages[1].vintage_claim_id)
        self.database.transition_run(
            self.result.acquisition_run_id,
            "INVALIDATED",
            occurred_at="2026-10-08T02:00:00Z",
            reason="LOGICAL_ROLLBACK",
        )
        self.assertEqual(self.database.active_trusted_collection_ids(), ())
        self.assertEqual(
            self.database.provenance_for_vintage(self.case.vintages[1].vintage_claim_id), before
        )
        self.assertTrue(self.database.validate_integrity().content_hashes_valid)

    def test_performance_sanity_is_bounded(self) -> None:
        metrics = self.database.performance_sanity(iterations=3)
        self.assertEqual(metrics["artifact_count"], 2)
        self.assertEqual(metrics["vintage_count"], 2)
        self.assertGreaterEqual(metrics["history_total_ms"], 0)


class TransactionFailureMatrixTests(ValidationDatabaseTestCase):
    def test_every_injected_failure_leaves_no_complete_or_trust(self) -> None:
        points = (
            "after_run",
            "after_artifact",
            "after_retrieval",
            "after_manifest",
            "after_claim",
            "after_binding",
            "after_vintage",
            "after_audit",
            "before_replay",
            "during_replay",
            "after_replay_before_promotion",
            "during_promotion_transaction",
            "before_promotion_commit",
        )
        for point in points:
            with self.subTest(point=point):
                path = self.extra_path(f"failure_{point}")
                adapter = PreRightsValidationDatabase(path)
                adapter.initialize_schema(
                    applied_at="2026-10-08T00:00:00Z",
                    code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
                )
                try:
                    with self.assertRaises(IntegrityValidationError):
                        adapter.persist_synthetic_e2e(
                            build_synthetic_2024_02_case(),
                            run_key=f"FAILURE_{point}",
                            failure_at=point,
                        )
                    connection = duckdb.connect(str(path), read_only=True)
                    try:
                        complete = connection.execute(
                            "SELECT COUNT(*) FROM acquisition_run_event WHERE run_state='COMPLETE'"
                        ).fetchone()[0]
                        trusted = connection.execute(
                            "SELECT COUNT(*) FROM trusted_collection"
                        ).fetchone()[0]
                    finally:
                        connection.close()
                    self.assertEqual(complete, 0)
                    self.assertEqual(trusted, 0)
                    self.assertEqual(adapter.active_trusted_collection_ids(), ())
                finally:
                    del adapter
                    self.remove_db(path)

    def test_overridden_strict_replay_failure_cannot_promote(self) -> None:
        class ReplayFailureDatabase(PreRightsValidationDatabase):
            def validate_integrity(self):  # type: ignore[override]
                raise IntegrityValidationError("INJECTED_STRICT_REPLAY_FAILURE")

        path = self.extra_path("override_replay_failure")
        adapter = ReplayFailureDatabase(path)
        adapter.initialize_schema(
            applied_at="2026-10-08T00:00:00Z",
            code_commit_sha=REPOSITORY_BASE_COMMIT_SHA,
        )
        try:
            with self.assertRaisesRegex(IntegrityValidationError, "INJECTED_STRICT_REPLAY_FAILURE"):
                adapter.persist_synthetic_e2e(build_synthetic_2024_02_case())
            connection = duckdb.connect(str(path), read_only=True)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM acquisition_run_event WHERE run_state='COMPLETE'"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM trusted_collection").fetchone()[0],
                    0,
                )
            finally:
                connection.close()
        finally:
            del adapter
            self.remove_db(path)


class AuthoritativeReplayAndTamperTests(ValidationDatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.case = build_synthetic_2024_02_case()
        self.result = self.database.persist_synthetic_e2e(self.case)

    def _direct_tamper(self, sql: str, parameters: list[object] | None = None) -> None:
        connection = duckdb.connect(str(self.database_path))
        try:
            connection.execute(sql, parameters or [])
        finally:
            connection.close()
        with self.assertRaises(IntegrityValidationError):
            self.database.validate_integrity()
        self.assertEqual(self.database.active_trusted_collection_ids(), ())

    def _insert_altered_trusted_projection(
        self,
        *,
        promoted_at: str,
        projection_mutation=None,
        membership_mutation=None,
    ) -> str:
        connection = duckdb.connect(str(self.database_path))
        row = connection.execute(
            "SELECT acquisition_run_id, manifest_id, rights_assessment_id, "
            "audit_reference_id, replay_fingerprint, collection_json "
            "FROM trusted_collection"
        ).fetchone()
        projection = parse_json_strict(row[5])
        projection["promoted_at"] = promoted_at
        members = [dict(member) for member in projection["members"]]
        if membership_mutation is not None:
            membership_mutation(members)
        projection["members"] = members
        if projection_mutation is not None:
            projection_mutation(projection)
        collection_id = _validation_hash("TRUSTED_COLLECTION", projection)
        connection.execute(
            "INSERT INTO trusted_collection VALUES "
            "(?, ?, ?, ?, ?, ?, ?, 'SYNTHETIC_VALIDATED', ?, ?)",
            [
                collection_id,
                collection_id,
                *row[:5],
                promoted_at,
                _json(projection),
            ],
        )
        for ordinal, member in enumerate(members):
            connection.execute(
                "INSERT INTO trusted_collection_membership VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    collection_id,
                    ordinal,
                    member["artifact_id"],
                    member["claim_id"],
                    member["binding_id"],
                    member["vintage_claim_id"],
                    member["audit_reference_id"],
                    _validation_hash("TRUSTED_COLLECTION_MEMBERSHIP", member),
                ],
            )
        connection.close()
        return collection_id

    def _assert_promotion_tamper_rejected_after_restart(self, reason: str) -> None:
        self.reopen()
        with self.assertRaisesRegex(IntegrityValidationError, reason):
            self.database.validate_integrity()
        self.assertEqual(self.database.active_trusted_collection_ids(), ())

    def test_false_trusted_manifest_hash_is_rejected_after_restart(self) -> None:
        self._insert_altered_trusted_projection(
            promoted_at="2026-10-08T01:00:31Z",
            projection_mutation=lambda projection: projection.__setitem__(
                "manifest_hash", "f" * 64
            ),
        )
        self._assert_promotion_tamper_rejected_after_restart(
            "trusted collection promotion provenance mismatch"
        )

    def test_promotion_checks_manifest_fail_is_rejected_after_restart(self) -> None:
        self._insert_altered_trusted_projection(
            promoted_at="2026-10-08T01:00:32Z",
            projection_mutation=lambda projection: projection["promotion_checks"].__setitem__(
                "manifest", "FAIL"
            ),
        )
        self._assert_promotion_tamper_rejected_after_restart(
            "trusted collection promotion provenance mismatch"
        )

    def test_promotion_checks_rights_fail_is_rejected_after_restart(self) -> None:
        self._insert_altered_trusted_projection(
            promoted_at="2026-10-08T01:00:33Z",
            projection_mutation=lambda projection: projection["promotion_checks"].__setitem__(
                "rights", "FAIL"
            ),
        )
        self._assert_promotion_tamper_rejected_after_restart(
            "trusted collection promotion provenance mismatch"
        )

    def test_self_asserted_pass_cannot_bypass_altered_membership(self) -> None:
        def cross_wire(members):
            first = {
                key: members[0][key]
                for key in ("claim_id", "binding_id", "vintage_claim_id")
            }
            second = {
                key: members[1][key]
                for key in ("claim_id", "binding_id", "vintage_claim_id")
            }
            members[0].update(second)
            members[1].update(first)

        self._insert_altered_trusted_projection(
            promoted_at="2026-10-08T01:00:34Z",
            membership_mutation=cross_wire,
        )
        self._assert_promotion_tamper_rejected_after_restart(
            "trusted collection exact membership mismatch"
        )

    def test_self_asserted_pass_with_unexpected_check_is_rejected(self) -> None:
        self._insert_altered_trusted_projection(
            promoted_at="2026-10-08T01:00:35Z",
            projection_mutation=lambda projection: projection["promotion_checks"].__setitem__(
                "caller_asserted", "PASS"
            ),
        )
        self._assert_promotion_tamper_rejected_after_restart(
            "trusted collection promotion provenance mismatch"
        )

    def test_exact_valid_promotion_remains_active_after_restart(self) -> None:
        self.reopen()
        self.database.validate_integrity()
        self.assertEqual(
            self.database.active_trusted_collection_ids(),
            (self.result.trusted_collection_id,),
        )

    def test_artifact_payload_tamper_is_detected(self) -> None:
        self._direct_tamper(
            "UPDATE restricted_artifact_blob SET payload_bytes=? "
            "WHERE payload_hash=(SELECT payload_hash FROM restricted_artifact_blob LIMIT 1)",
            [b"CORRUPTED"],
        )

    def test_fabricated_source_release_identity_is_detected(self) -> None:
        release = self.case.releases[0]
        connection = duckdb.connect(str(self.database_path))
        connection.execute(
            "INSERT INTO source_release VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                "f" * 64,
                release.content_hash,
                release.source_id,
                release.data_acquisition_channel,
                release.historical_evidence_channel,
                release.release_family,
                _json(release.content_projection()),
            ],
        )
        connection.close()
        with self.assertRaises(IntegrityValidationError):
            self.database.validate_integrity()

    def test_fabricated_artifact_identity_is_detected(self) -> None:
        original = self.case.artifacts[0]
        release = self.case.releases[0]
        locator = "synthetic://world-bank-release/forged-artifact"
        forged_object = EvidenceArtifact(
            source_release_id=release.source_release_id,
            artifact_role=original.artifact_role,
            media_type=original.media_type,
            official_locator=locator,
            payload_hash=original.payload_hash,
            byte_size=original.byte_size,
            retrieved_at=original.retrieved_at,
            source_document_id="FORGED_DOCUMENT",
        )
        provenance = self.database.provenance_for_vintage(self.case.vintages[0].vintage_claim_id)
        retrieval_projection = {
            "acquisition_run_id": self.result.acquisition_run_id,
            "source_release_id": release.source_release_id,
            "retrieval_mode": "SYNTHETIC_FIXTURE",
            "source_locator_safe": locator,
            "retrieved_at": original.retrieved_at,
            "response_payload_hash": original.payload_hash,
        }
        retrieval_id = _validation_hash("RETRIEVAL_EVIDENCE", retrieval_projection)
        connection = duckdb.connect(str(self.database_path))
        connection.execute(
            "INSERT INTO retrieval_evidence VALUES (?, ?, ?, 'SYNTHETIC_FIXTURE', ?, ?, ?, ?)",
            [
                retrieval_id,
                self.result.acquisition_run_id,
                release.source_release_id,
                locator,
                original.retrieved_at,
                original.payload_hash,
                _json(retrieval_projection),
            ],
        )
        connection.execute(
            "INSERT INTO evidence_artifact VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)",
            [
                "e" * 64,
                forged_object.content_hash,
                release.source_release_id,
                self.result.acquisition_run_id,
                retrieval_id,
                SYNTHETIC_RIGHTS_PROFILE,
                provenance["rights_assessment_id"],
                forged_object.payload_hash,
                locator,
                forged_object.media_type,
                forged_object.byte_size,
                _json(forged_object.content_projection()),
            ],
        )
        connection.close()
        with self.assertRaises(IntegrityValidationError):
            self.database.validate_integrity()

    def test_fabricated_claim_binding_and_vintage_identities_are_detected(self) -> None:
        claim = replace(
            self.case.claims[0],
            safe_locator={**dict(self.case.claims[0].safe_locator), "row": "FORGED_ROW"},
        )
        binding = replace(
            self.case.bindings[0],
            source_record_identifier="WORLD_BANK:PINK_SHEET:COPPER:FORGED",
        )
        vintage = replace(
            self.case.vintages[1], release_record_key="SYNTHETIC_FORGED_RELEASE_RECORD"
        )
        connection = duckdb.connect(str(self.database_path))
        temporal_id = connection.execute(
            "SELECT temporal_bound_id FROM evidence_claim WHERE claim_id=?",
            [self.case.claims[0].claim_id],
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO evidence_claim VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "d" * 64,
                claim.content_hash,
                claim.source_release_id,
                claim.artifact_id,
                temporal_id,
                claim.claim_type,
                claim.subject_scope,
                _json(claim.content_projection()),
            ],
        )
        connection.execute(
            "INSERT INTO evidence_subject_binding VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                "c" * 64,
                binding.content_hash,
                binding.claim_id,
                binding.source_release_id,
                binding.artifact_id,
                binding.subject_scope,
                _json(binding.content_projection()),
            ],
        )
        vintage_temporal_id = connection.execute(
            "SELECT temporal_bound_id FROM historical_vintage_claim WHERE vintage_claim_id=?",
            [self.case.vintages[1].vintage_claim_id],
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO historical_vintage_claim VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)",
            [
                "b" * 64,
                vintage.content_hash,
                vintage.observation_id,
                vintage.source_release_id,
                vintage_temporal_id,
                vintage.parent_vintage_claim_id,
                vintage.semantic_value,
                vintage.unit,
                vintage.currency,
                _json(vintage.content_projection()),
            ],
        )
        connection.close()
        with self.assertRaises(IntegrityValidationError):
            self.database.validate_integrity()

    def test_forged_claim_independently_fails_closed_after_restart(self) -> None:
        claim = replace(
            self.case.claims[0],
            safe_locator={**dict(self.case.claims[0].safe_locator), "row": "FORGED_ROW_ONLY"},
        )
        connection = duckdb.connect(str(self.database_path))
        temporal_id = connection.execute(
            "SELECT temporal_bound_id FROM evidence_claim WHERE claim_id=?",
            [self.case.claims[0].claim_id],
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO evidence_claim VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "a" * 64,
                claim.content_hash,
                claim.source_release_id,
                claim.artifact_id,
                temporal_id,
                claim.claim_type,
                claim.subject_scope,
                _json(claim.content_projection()),
            ],
        )
        connection.close()
        self.reopen()
        with self.assertRaisesRegex(IntegrityValidationError, "claim authoritative replay"):
            self.database.validate_integrity()
        self.assertEqual(self.database.active_trusted_collection_ids(), ())

    def test_forged_binding_independently_fails_closed_after_restart(self) -> None:
        binding = replace(
            self.case.bindings[0],
            source_record_identifier="WORLD_BANK:PINK_SHEET:COPPER:FORGED_BINDING_ONLY",
        )
        connection = duckdb.connect(str(self.database_path))
        connection.execute(
            "INSERT INTO evidence_subject_binding VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                "9" * 64,
                binding.content_hash,
                binding.claim_id,
                binding.source_release_id,
                binding.artifact_id,
                binding.subject_scope,
                _json(binding.content_projection()),
            ],
        )
        connection.close()
        self.reopen()
        with self.assertRaisesRegex(IntegrityValidationError, "binding authoritative replay"):
            self.database.validate_integrity()
        self.assertEqual(self.database.active_trusted_collection_ids(), ())

    def test_forged_vintage_independently_fails_closed_after_restart(self) -> None:
        vintage = replace(
            self.case.vintages[1],
            release_record_key="SYNTHETIC_FORGED_VINTAGE_ONLY",
        )
        connection = duckdb.connect(str(self.database_path))
        temporal_id = connection.execute(
            "SELECT temporal_bound_id FROM historical_vintage_claim WHERE vintage_claim_id=?",
            [self.case.vintages[1].vintage_claim_id],
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO historical_vintage_claim VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)",
            [
                "8" * 64,
                vintage.content_hash,
                vintage.observation_id,
                vintage.source_release_id,
                temporal_id,
                vintage.parent_vintage_claim_id,
                vintage.semantic_value,
                vintage.unit,
                vintage.currency,
                _json(vintage.content_projection()),
            ],
        )
        connection.close()
        self.reopen()
        with self.assertRaisesRegex(IntegrityValidationError, "vintage authoritative replay"):
            self.database.validate_integrity()
        self.assertEqual(self.database.active_trusted_collection_ids(), ())

    def test_direct_trust_insert_with_incomplete_membership_is_inactive(self) -> None:
        connection = duckdb.connect(str(self.database_path))
        row = connection.execute(
            "SELECT acquisition_run_id, manifest_id, rights_assessment_id, audit_reference_id, "
            "replay_fingerprint FROM trusted_collection"
        ).fetchone()
        projection = {
            "status": "SYNTHETIC_VALIDATED",
            "acquisition_run_id": row[0],
            "manifest_id": row[1],
            "manifest_hash": row[1],
            "rights_assessment_id": row[2],
            "audit_reference_id": row[3],
            "replay_fingerprint": row[4],
            "promoted_at": "2026-10-08T01:00:30Z",
            "members": (),
            "promotion_checks": {
                "rights": "PASS",
                "manifest": "PASS",
                "content_hash": "PASS",
                "contract": "PASS",
                "read_back": "PASS",
                "audit_association": "PASS",
            },
        }
        collection_id = _validation_hash("TRUSTED_COLLECTION", projection)
        connection.execute(
            "INSERT INTO trusted_collection VALUES (?, ?, ?, ?, ?, ?, ?, 'SYNTHETIC_VALIDATED', ?, ?)",
            [collection_id, collection_id, *row, projection["promoted_at"], _json(projection)],
        )
        connection.close()
        with self.assertRaises(IntegrityValidationError):
            self.database.validate_integrity()
        self.assertEqual(self.database.active_trusted_collection_ids(), ())


class DatasetSourceContractTests(ValidationDatabaseTestCase):
    def source_document(self) -> dict[str, object]:
        return parse_json_strict(WORLD_BANK_SOURCE_PATH.read_bytes())

    def test_exact_repository_source_contract_has_799_complete_copper_months(self) -> None:
        values = self.database._validate_world_bank_copper_document(self.source_document())
        self.assertEqual(len(values), WORLD_BANK_COPPER_RECORD_COUNT)
        self.assertEqual(values[0][0], "1960-01")
        self.assertEqual(values[-1][0], "2026-07")

    def test_wrong_source_sha_fails_closed(self) -> None:
        with patch(
            "scripts.c4_pit_pre_rights_validation.WORLD_BANK_SOURCE_SHA256", "0" * 64
        ), self.assertRaises(ValidationConflictError):
            self.database.import_dataset_copy()

    def test_generic_rows_and_nonapproved_source_path_cannot_self_label_real(self) -> None:
        with self.assertRaises(ValidationConflictError):
            self.database.import_dataset_copy([{"commodity": "Copper"}])
        with self.assertRaises(ValidationConflictError):
            self.database.import_dataset_copy(self.database_path)

    def test_missing_extra_duplicate_and_malformed_periods_fail(self) -> None:
        mutations = {}
        missing = self.source_document()
        missing["series"]["copper"]["observations"].pop()
        mutations["missing"] = missing
        extra = self.source_document()
        extra["series"]["copper"]["observations"].append(
            {"period": "2026-08", "value": 1}
        )
        mutations["extra"] = extra
        duplicate = self.source_document()
        duplicate["series"]["copper"]["observations"][1]["period"] = "1960-01"
        mutations["duplicate"] = duplicate
        malformed = self.source_document()
        malformed["series"]["copper"]["observations"][0]["period"] = "1960-13"
        mutations["malformed"] = malformed
        for name, document in mutations.items():
            with self.subTest(name=name), self.assertRaises(ValidationConflictError):
                self.database._validate_world_bank_copper_document(document)

    def test_empty_commodity_unit_and_invalid_decimal_fail(self) -> None:
        cases = {}
        empty_commodity = self.source_document()
        empty_commodity["series"]["copper"]["nameEn"] = ""
        cases["commodity"] = empty_commodity
        empty_unit = self.source_document()
        empty_unit["series"]["copper"]["sourceUnit"] = ""
        cases["unit"] = empty_unit
        invalid_value = self.source_document()
        invalid_value["series"]["copper"]["observations"][0]["value"] = Decimal("NaN")
        cases["decimal"] = invalid_value
        for name, document in cases.items():
            with self.subTest(name=name), self.assertRaises(ValidationConflictError):
                self.database._validate_world_bank_copper_document(document)

    def test_correct_source_import_is_read_only_idempotent_and_not_pit_verified(self) -> None:
        before = hashlib.sha256(WORLD_BANK_SOURCE_PATH.read_bytes()).hexdigest()
        self.assertEqual(self.database.import_dataset_copy(), WORLD_BANK_COPPER_RECORD_COUNT)
        self.assertEqual(self.database.import_dataset_copy(), WORLD_BANK_COPPER_RECORD_COUNT)
        after = hashlib.sha256(WORLD_BANK_SOURCE_PATH.read_bytes()).hexdigest()
        self.assertEqual(before, WORLD_BANK_SOURCE_SHA256)
        self.assertEqual(after, before)
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            row = connection.execute(
                "SELECT COUNT(*), MIN(period), MAX(period), "
                "COUNT(DISTINCT period), MIN(historical_availability_status), "
                "MAX(historical_availability_status), MIN(data_classification), "
                "MAX(data_classification) FROM dataset_observation"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            row,
            (
                799,
                "1960-01",
                "2026-07",
                799,
                "ABSENT_NOT_PIT_VERIFIED",
                "ABSENT_NOT_PIT_VERIFIED",
                "REAL_DATASET_DERIVED",
                "REAL_DATASET_DERIVED",
            ),
        )
        self.assertTrue(self.database.validate_integrity().rights_valid)

    def test_fabricated_persisted_lineage_is_detected(self) -> None:
        self.database.import_dataset_copy()
        connection = duckdb.connect(str(self.database_path))
        connection.execute(
            "UPDATE dataset_observation SET source_lineage_json=? WHERE period='1960-01'",
            [_json({"fabricated": True})],
        )
        connection.close()
        with self.assertRaises(IntegrityValidationError):
            self.database.validate_integrity()


class TemporalAndRevisionTests(ValidationDatabaseTestCase):
    def test_all_temporal_bound_types_round_trip_without_precision_loss(self) -> None:
        values = (
            TemporalBound("EXACT", "EXACT_TIMESTAMP", "2024-04-08T12:00:00+08:00"),
            TemporalBound("BY_DATE", "DATE_ONLY", "2024-04-08", source_timezone="Asia/Taipei"),
            TemporalBound("NOT_BEFORE", "DATE_ONLY", "2024-04-08"),
            TemporalBound("NOT_LATER_THAN", "MONTH_ONLY", "2024-04"),
            TemporalBound("INTERVAL", "DATE_ONLY", ("2024-04-01", "2024-04-30")),
            TemporalBound("UNKNOWN", "UNKNOWN", None),
        )
        for value in values:
            with self.subTest(bound_type=value.bound_type):
                loaded = self.database.load_temporal_bound(
                    self.database.persist_temporal_bound(value)
                )
                self.assertEqual(loaded.projection(), value.projection())

    def test_date_only_never_becomes_timestamp_and_strict_intraday_rejects(self) -> None:
        bound = TemporalBound(
            "BY_DATE", "DATE_ONLY", "2024-04-08", source_timezone="Asia/Taipei"
        )
        strict = evaluate_temporal_eligibility(
            bound,
            cutoff_at="2024-04-10T00:00:00Z",
            mode="STRICT_INTRADAY_PIT",
            policy_version=STRICT_INTRADAY_POLICY_VERSION,
        )
        day = evaluate_temporal_eligibility(
            bound,
            cutoff_at="2024-04-10T00:00:00Z",
            mode="DAY_GRANULAR_PIT",
            source_timezone="Asia/Taipei",
            policy_version=DAY_GRANULAR_POLICY_VERSION,
        )
        self.assertFalse(strict.eligible)
        self.assertIsNone(strict.source_factual_timestamp)
        self.assertTrue(day.eligible)
        self.assertIsNone(day.source_factual_timestamp)

    def test_revision_selection_prevents_future_visibility(self) -> None:
        case = build_synthetic_2024_02_case()
        march = select_visible_vintage(
            case.vintages,
            cutoff_at="2024-04-01T00:00:00Z",
            mode="DAY_GRANULAR_PIT",
            source_timezone="Asia/Taipei",
            policy_version=DAY_GRANULAR_POLICY_VERSION,
        )
        april = select_visible_vintage(
            case.vintages,
            cutoff_at="2024-04-09T00:00:00Z",
            mode="DAY_GRANULAR_PIT",
            source_timezone="Asia/Taipei",
            policy_version=DAY_GRANULAR_POLICY_VERSION,
        )
        self.assertEqual(march.vintage_claim_id, case.vintages[0].vintage_claim_id)
        self.assertEqual(april.vintage_claim_id, case.vintages[1].vintage_claim_id)

    def test_self_parent_cycle_retrograde_sibling_disconnected_and_missing_parent_fail(self) -> None:
        root, child = build_synthetic_2024_02_case().vintages
        with self.assertRaises(EvidenceContractError):
            replace(root, parent_vintage_claim_id=root.vintage_claim_id)
        cases = (
            (replace(root, parent_vintage_claim_id=child.vintage_claim_id), child),
            (root, replace(child, temporal_bound=root.temporal_bound)),
            (root, child, replace(child, release_record_key="SIBLING")),
            (root, replace(child, parent_vintage_claim_id=None)),
            (root, replace(child, parent_vintage_claim_id="f" * 64)),
        )
        for vintages in cases:
            with self.assertRaises(EvidenceContractError):
                validate_revision_lineage(vintages)

    def test_same_locator_changed_bytes_is_an_explicit_conflict(self) -> None:
        original = build_synthetic_2024_02_case().artifacts[0]
        changed = EvidenceArtifact.from_payload(
            source_release_id=original.source_release_id,
            artifact_role=original.artifact_role,
            media_type=original.media_type,
            official_locator=original.official_locator,
            payload=b"DIFFERENT SYNTHETIC BYTES",
            retrieved_at=original.retrieved_at,
            source_document_id=original.source_document_id,
        )
        with self.assertRaises(EvidenceContractError):
            validate_artifact_consistency((original, changed))


if __name__ == "__main__":
    unittest.main()
