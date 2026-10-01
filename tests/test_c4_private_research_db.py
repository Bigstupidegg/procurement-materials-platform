from __future__ import annotations

from decimal import Decimal
import hashlib
from pathlib import Path
import tempfile
import unittest

import duckdb

from scripts.c4_private_research_db import (
    APPROVED_TABLES,
    MIGRATION_ID,
    MIGRATION_PATH,
    REPOSITORY_ROOT,
    SCHEMA_VERSION,
    DatabasePathError,
    MigrationError,
    PersistenceConflictError,
    PrivateResearchDatabase,
    migration_checksum,
    migration_sql_bytes,
)
from scripts.c4_rd_contract import (
    BACKTEST_ENABLED_SOURCES,
    CALENDAR_ROLES,
    PIT_ENABLED_SOURCES,
    RD3_OPEN_BLOCKERS,
    RESEARCH_ENABLED_SOURCES,
    SAFETY_FLAGS,
    CalendarAssignment,
    Observation,
    ObservationVersion,
    canonical_json_bytes,
    raw_payload_hash,
)


H1 = "1" * 64
EXPECTED_BLOCKERS = (
    "RD3-LME-001", "RD3-LME-002", "RD3-LME-003",
    "RD3-SMM-001", "RD3-SMM-002", "RD3-SMM-003", "RD3-SMM-004",
    "RD3-YAHOO-001", "RD3-YAHOO-002", "RD3-YAHOO-003", "RD3-YAHOO-004",
    "RD3-BZ-001", "RD3-WB-001", "RD3-WB-002", "RD3-WB-003",
)
EXPECTED_COLUMNS = {
    "schema_migrations": (
        "schema_version", "migration_id", "applied_at", "code_commit_sha", "migration_checksum",
    ),
    "source_registry": (
        "source_id", "source_name", "access_channel", "default_timezone", "data_classification",
        "registry_version", "active", "created_at",
    ),
    "subject_registry": ("subject_id", "subject_snapshot_json", "registered_at"),
    "instrument_registry": (
        "instrument_id", "subject_id", "source_id", "source_symbol", "market_or_venue", "metric_id",
        "quote_type", "term", "currency", "unit", "source_period_type", "instrument_version",
        "active_from", "active_to",
    ),
    "source_usage_rights": (
        "rights_profile_id", "source_id", "access_channel", "instrument_scope", "automated_access",
        "private_storage", "historical_archive", "internal_analysis", "backtest", "prediction",
        "internal_display", "internet_display", "redistribution", "evidence_reference", "review_status",
        "effective_from", "effective_to", "reviewed_at",
    ),
    "raw_payload": (
        "raw_payload_hash", "relative_path", "content_type", "byte_size", "data_classification",
        "first_seen_at",
    ),
    "raw_capture": (
        "capture_id", "source_id", "instrument_id", "access_channel", "source_locator_safe",
        "collected_at", "collection_status", "raw_payload_hash", "collector_version", "rights_profile_id",
        "error_class", "created_at",
    ),
    "observation_identity": (
        "observation_id", "source_id", "source_record_identifier", "metric_id", "instrument_id",
        "source_period_type", "source_market_date", "source_period_start_date", "source_period_end_date",
        "identity_projection_json",
    ),
    "observation_snapshot": (
        "observation_content_hash", "observation_id", "data_origin", "operational_status",
        "scheduler_execution_at", "local_business_date", "source_business_date", "scheduler_business_date",
        "source_publication_at", "collected_at", "observed_at", "source_available_at",
        "channel_available_at", "created_at", "semantic_data_json", "content_projection_json",
    ),
    "observation_calendar_assignment": (
        "observation_content_hash", "calendar_role", "subject_id", "assignment_status",
        "calendar_reference_id", "calendar_version", "calendar_hash", "assignment_projection_json",
    ),
    "observation_version_identity": (
        "observation_version_id", "observation_id", "source_version_or_release_key", "stable_version_key",
        "raw_payload_hash", "transformation_version", "identity_projection_json",
    ),
    "observation_version_snapshot": (
        "observation_version_content_hash", "observation_version_id", "parent_version_id",
        "revision_available_at", "collected_at", "observed_at", "created_at", "semantic_data_json",
        "content_projection_json",
    ),
    "readiness_evaluation": (
        "evaluation_hash", "observation_id", "observation_version_id", "evaluation_role", "cutoff_at",
        "evaluation_as_of_at", "label_available_at", "readiness_state", "eligibility_state",
        "reason_codes_json", "blocker_ids_json", "evidence_references_json", "contract_version",
        "evaluator_version", "rule_bundle_version", "rule_bundle_hash", "source_profile_id",
        "source_profile_version", "observation_content_hash", "observation_version_content_hash",
        "persisted_at",
    ),
    "pit_dataset_manifest": (
        "dataset_identity", "manifest_type", "manifest_version", "dataset_contract_version",
        "feature_set_version", "feature_computation_profile_version", "cutoff_policy_version",
        "rule_bundle_bindings_json", "source_profile_bindings_json", "authorization_snapshot_json",
        "request_scope_json", "include_count", "exclude_count", "quarantine_count",
        "exclusion_reason_summary_json", "quarantine_reason_summary_json", "persisted_at",
    ),
    "pit_dataset_row": (
        "row_content_hash", "row_id", "dataset_identity", "manifest_row_ordinal", "research_subject_id",
        "observation_version_id", "research_cutoff_at", "feature_set_version",
        "feature_computation_profile_version", "cutoff_policy_version", "feature_available_at_max",
        "rd4_authority_bindings_json", "rd5_authority_bindings_json", "rule_bundle_bindings_json",
        "source_profile_bindings_json", "label_specification_json", "authorization_snapshot_json",
        "operational_status", "persisted_at",
    ),
    "pit_feature_snapshot": (
        "row_content_hash", "feature_ordinal", "feature_content_hash", "feature_definition_id",
        "feature_definition_version", "feature_computation_profile_version", "research_cutoff_at",
        "value_state", "value_json", "source_observation_id", "source_observation_version_id",
        "source_observed_at", "source_available_at", "source_profile_id", "source_profile_version",
        "evidence_refs_json", "rd4_evaluation_hash", "rd5_decision_hash", "authority_binding_ref",
    ),
}
RIGHTS_DIMENSIONS = (
    "automated_access", "private_storage", "historical_archive", "internal_analysis", "backtest",
    "prediction", "internal_display", "internet_display", "redistribution",
)


def assignments() -> tuple[CalendarAssignment, ...]:
    return (
        CalendarAssignment("SYNTHETIC_SUBJECT", "MARKET", "RESOLVED", "SYNTHETIC_CAL", "1.0.0", H1),
        CalendarAssignment("SYNTHETIC_SUBJECT", "PUBLICATION", "NOT_APPLICABLE"),
        CalendarAssignment("SYNTHETIC_SUBJECT", "LOCAL_OPERATIONAL", "UNVERIFIED"),
        CalendarAssignment("SYNTHETIC_SUBJECT", "SCHEDULER", "NOT_APPLICABLE"),
    )


def observation(**changes) -> Observation:
    values = {
        "source_id": "SYNTHETIC_TEST_SOURCE",
        "source_record_identifier": "SYNTHETIC_RECORD_001",
        "metric_id": "SYNTHETIC_PRICE",
        "instrument_id": "SYNTHETIC_COPPER",
        "source_period_type": "DAILY_MARKET",
        "source_market_date": "2026-01-02",
        "source_period_start_date": None,
        "source_period_end_date": None,
        "calendar_assignments": assignments(),
        "scheduler_execution_at": "2026-01-03T08:00:00+08:00",
        "local_business_date": "2026-01-03",
        "source_business_date": "2026-01-02",
        "scheduler_business_date": "2026-01-03",
        "source_publication_at": "2026-01-02T16:30:00Z",
        "collected_at": "2026-01-03T00:00:00.120000Z",
        "observed_at": "2026-01-03T00:00:01Z",
        "source_available_at": "2026-01-02T16:30:00Z",
        "channel_available_at": "2026-01-02T16:31:00Z",
        "created_at": "2026-01-03T00:00:02Z",
        "semantic_data": {"value": Decimal("12.50"), "unit": "SYNTHETIC_UNIT", "nested": [1, True, None]},
    }
    values.update(changes)
    return Observation(**values)


def version(item: Observation, **changes) -> ObservationVersion:
    values = {
        "observation_id": item.observation_id,
        "source_version_or_release_key": "SYNTHETIC_RELEASE_001",
        "stable_version_key": "SYNTHETIC_VERSION_001",
        "raw_payload_hash": raw_payload_hash(b"synthetic payload only"),
        "transformation_version": "synthetic-transform@1.0.0",
        "parent_version_id": H1,
        "revision_available_at": "2026-01-03T00:02:00+00:00",
        "collected_at": "2026-01-03T00:02:01Z",
        "observed_at": "2026-01-03T00:02:02Z",
        "created_at": "2026-01-03T00:02:03Z",
        "semantic_data": {"value": Decimal("12.50"), "revision": 1},
    }
    values.update(changes)
    return ObservationVersion(**values)


def pit_dataset_row_values(
    dataset_identity: str,
    manifest_row_ordinal: int,
    row_content_hash: str,
) -> tuple[object, ...]:
    return (
        row_content_hash,
        "4" * 64,
        dataset_identity,
        manifest_row_ordinal,
        "SYNTHETIC_SUBJECT",
        "5" * 64,
        "2026-01-03T00:00:00Z",
        "SYNTHETIC_FEATURE_SET@1.0.0",
        "SYNTHETIC_COMPUTATION_PROFILE@1.0.0",
        "SYNTHETIC_CUTOFF_POLICY@1.0.0",
        "2026-01-02T00:00:00Z",
        "[]",
        "[]",
        "[]",
        "[]",
        "{}",
        "{}",
        "SYNTHETIC_NON_OPERATIONAL",
        "2026-01-03T00:00:01Z",
    )


class PrivateResearchDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="c4_db1a_")
        self.database_path = Path(self.temporary_directory.name) / "synthetic-test.duckdb"
        self.database = PrivateResearchDatabase(self.database_path)
        self.database.initialize_schema(
            applied_at="2026-09-24T00:00:00Z",
            code_commit_sha="SYNTHETIC_TEST_COMMIT",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _columns(self, table_name: str) -> tuple[str, ...]:
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            rows = connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
                [table_name],
            ).fetchall()
            return tuple(row[0] for row in rows)
        finally:
            connection.close()

    def _primary_key(self, table_name: str) -> tuple[str, ...]:
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            rows = connection.execute(
                "SELECT kcu.column_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "ON tc.constraint_catalog = kcu.constraint_catalog "
                "AND tc.constraint_schema = kcu.constraint_schema "
                "AND tc.constraint_name = kcu.constraint_name "
                "WHERE tc.table_schema = 'main' AND tc.table_name = ? "
                "AND tc.constraint_type = 'PRIMARY KEY' ORDER BY kcu.ordinal_position",
                [table_name],
            ).fetchall()
            return tuple(row[0] for row in rows)
        finally:
            connection.close()

    def _unique_keys(self, table_name: str) -> tuple[tuple[str, ...], ...]:
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            rows = connection.execute(
                "SELECT tc.constraint_name, kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "ON tc.constraint_catalog = kcu.constraint_catalog "
                "AND tc.constraint_schema = kcu.constraint_schema "
                "AND tc.constraint_name = kcu.constraint_name "
                "WHERE tc.table_schema = 'main' AND tc.table_name = ? "
                "AND tc.constraint_type = 'UNIQUE' "
                "ORDER BY tc.constraint_name, kcu.ordinal_position",
                [table_name],
            ).fetchall()
        finally:
            connection.close()
        grouped: dict[str, list[str]] = {}
        for constraint_name, column_name in rows:
            grouped.setdefault(constraint_name, []).append(column_name)
        return tuple(sorted(tuple(names) for names in grouped.values()))

    def _nullability(self, table_name: str) -> dict[str, bool]:
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            rows = connection.execute(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'main' AND table_name = ?",
                [table_name],
            ).fetchall()
            return {column_name: is_nullable == "YES" for column_name, is_nullable in rows}
        finally:
            connection.close()

    def test_repository_path_is_rejected_without_creating_database(self) -> None:
        requested = REPOSITORY_ROOT / "forbidden-private-data.duckdb"
        with self.assertRaises(DatabasePathError):
            PrivateResearchDatabase(requested)
        self.assertFalse(requested.exists())

    def test_external_temporary_database_initializes_all_approved_tables(self) -> None:
        self.assertTrue(self.database_path.exists())
        self.assertFalse(self.database_path.is_relative_to(REPOSITORY_ROOT))
        self.assertEqual(set(self.database.table_names()), set(APPROVED_TABLES))
        self.assertEqual(len(self.database.table_names()), 16)
        record = self.database.migration_record()
        self.assertEqual(record["schema_version"], SCHEMA_VERSION)
        self.assertEqual(record["migration_id"], MIGRATION_ID)

    def test_schema_columns_exactly_match_approved_contract(self) -> None:
        self.assertEqual(set(EXPECTED_COLUMNS), set(APPROVED_TABLES))
        for table_name, expected_columns in EXPECTED_COLUMNS.items():
            with self.subTest(table_name=table_name):
                self.assertEqual(self._columns(table_name), expected_columns)

    def test_subject_registry_remains_minimal_and_explicit(self) -> None:
        self.assertEqual(
            self._columns("subject_registry"),
            ("subject_id", "subject_snapshot_json", "registered_at"),
        )

    def test_raw_payload_is_metadata_only_without_blob_storage(self) -> None:
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            rows = connection.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'main' AND table_name = 'raw_payload' ORDER BY ordinal_position"
            ).fetchall()
        finally:
            connection.close()
        self.assertNotIn("payload_bytes", tuple(row[0] for row in rows))
        self.assertTrue(all(row[1].upper() != "BLOB" for row in rows))

    def test_dataset_order_and_feature_membership_keys_are_explicit(self) -> None:
        self.assertIn("manifest_row_ordinal", self._columns("pit_dataset_row"))
        self.assertEqual(
            self._primary_key("pit_dataset_row"),
            ("dataset_identity", "manifest_row_ordinal"),
        )
        self.assertEqual(
            self._unique_keys("pit_dataset_row"),
            (("dataset_identity", "row_content_hash"),),
        )
        self.assertEqual(
            self._primary_key("pit_feature_snapshot"),
            ("row_content_hash", "feature_ordinal"),
        )

    def test_pit_row_membership_is_manifest_scoped_and_deduplicated(self) -> None:
        dataset_a = "a" * 64
        dataset_b = "b" * 64
        row_hash_x = "2" * 64
        row_hash_y = "3" * 64
        placeholders = ", ".join("?" for _ in range(19))
        connection = duckdb.connect(str(self.database_path))
        try:
            connection.execute(
                f"INSERT INTO pit_dataset_row VALUES ({placeholders})",
                pit_dataset_row_values(dataset_a, 0, row_hash_x),
            )
            connection.execute(
                f"INSERT INTO pit_dataset_row VALUES ({placeholders})",
                pit_dataset_row_values(dataset_b, 0, row_hash_x),
            )
            with self.assertRaises(duckdb.ConstraintException):
                connection.execute(
                    f"INSERT INTO pit_dataset_row VALUES ({placeholders})",
                    pit_dataset_row_values(dataset_a, 1, row_hash_x),
                )
            with self.assertRaises(duckdb.ConstraintException):
                connection.execute(
                    f"INSERT INTO pit_dataset_row VALUES ({placeholders})",
                    pit_dataset_row_values(dataset_a, 0, row_hash_y),
                )
            memberships = connection.execute(
                "SELECT dataset_identity, manifest_row_ordinal FROM pit_dataset_row "
                "WHERE row_content_hash = ? ORDER BY dataset_identity",
                [row_hash_x],
            ).fetchall()
            self.assertEqual(memberships, [(dataset_a, 0), (dataset_b, 0)])
        finally:
            connection.close()

    def test_explicit_missing_feature_accepts_null_authoritative_lineage(self) -> None:
        lineage_columns = (
            "source_observation_id",
            "source_observation_version_id",
            "source_observed_at",
            "source_available_at",
            "source_profile_id",
            "source_profile_version",
            "rd4_evaluation_hash",
            "rd5_decision_hash",
            "authority_binding_ref",
        )
        nullability = self._nullability("pit_feature_snapshot")
        self.assertTrue(all(nullability[column_name] for column_name in lineage_columns))
        self.assertTrue(nullability["source_profile_id"])
        self.assertTrue(nullability["source_profile_version"])
        self.assertTrue(nullability["authority_binding_ref"])

        values = (
            "2" * 64,
            0,
            "3" * 64,
            "SYNTHETIC_OPTIONAL_FEATURE",
            "1.0.0",
            "SYNTHETIC_COMPUTATION_PROFILE@1.0.0",
            "2026-01-03T00:00:00Z",
            "EXPLICIT_MISSING",
            canonical_json_bytes(None).decode("utf-8"),
            None,
            None,
            None,
            None,
            None,
            None,
            canonical_json_bytes([]).decode("utf-8"),
            None,
            None,
            None,
        )
        connection = duckdb.connect(str(self.database_path))
        try:
            connection.execute(
                f"INSERT INTO pit_feature_snapshot VALUES ({', '.join('?' for _ in values)})",
                values,
            )
            stored = connection.execute(
                f"SELECT value_state, value_json, evidence_refs_json, {', '.join(lineage_columns)} "
                "FROM pit_feature_snapshot WHERE row_content_hash = ? AND feature_ordinal = 0",
                ["2" * 64],
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(stored[:3], ("EXPLICIT_MISSING", "null", "[]"))
        self.assertEqual(stored[3:], (None,) * len(lineage_columns))

    def test_migration_checksum_uses_exact_sql_bytes_and_is_deterministic(self) -> None:
        expected = hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest()
        self.assertEqual(migration_sql_bytes(), MIGRATION_PATH.read_bytes())
        self.assertEqual(migration_checksum(), expected)
        self.assertEqual(migration_checksum(), migration_checksum())
        self.assertEqual(self.database.migration_record()["migration_checksum"], expected)

    def test_second_identical_migration_application_is_idempotent(self) -> None:
        first = self.database.migration_record()
        checksum = self.database.initialize_schema(
            applied_at="2026-09-25T00:00:00Z",
            code_commit_sha="DIFFERENT_CALLER_CONTEXT",
        )
        self.assertEqual(checksum, migration_checksum())
        self.assertEqual(self.database.migration_record(), first)
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 1)
        finally:
            connection.close()

    def test_applied_migration_with_different_checksum_is_rejected(self) -> None:
        connection = duckdb.connect(str(self.database_path))
        try:
            connection.execute(
                "UPDATE schema_migrations SET migration_checksum = ? WHERE migration_id = ?",
                ["f" * 64, MIGRATION_ID],
            )
        finally:
            connection.close()
        with self.assertRaisesRegex(MigrationError, "checksum conflicts"):
            self.database.initialize_schema(
                applied_at="2026-09-24T00:00:00Z",
                code_commit_sha="SYNTHETIC_TEST_COMMIT",
            )

    def test_existing_database_with_extra_column_is_rejected(self) -> None:
        connection = duckdb.connect(str(self.database_path))
        try:
            connection.execute("ALTER TABLE subject_registry ADD COLUMN unexpected_column VARCHAR")
        finally:
            connection.close()
        with self.assertRaisesRegex(MigrationError, "columns do not match"):
            self.database.initialize_schema(
                applied_at="2026-09-24T00:00:00Z",
                code_commit_sha="SYNTHETIC_TEST_COMMIT",
            )

    def test_observation_exact_round_trip_preserves_authority(self) -> None:
        original = observation()
        persisted_hash = self.database.persist_observation(original)
        loaded = self.database.load_observation(persisted_hash)
        self.assertEqual(loaded.observation_id, original.observation_id)
        self.assertEqual(loaded.content_hash, original.content_hash)
        self.assertEqual(loaded.content_projection(), original.content_projection())
        self.assertEqual(tuple(item.role for item in loaded.calendar_assignments), CALENDAR_ROLES)
        self.assertEqual(loaded.calendar_assignments, original.calendar_assignments)
        self.assertEqual(loaded.scheduler_execution_at, "2026-01-03T00:00:00Z")
        self.assertEqual(loaded.collected_at, "2026-01-03T00:00:00.12Z")
        self.assertEqual(
            canonical_json_bytes(loaded.semantic_data),
            canonical_json_bytes(original.semantic_data),
        )

    def test_observation_exact_duplicate_is_idempotent(self) -> None:
        original = observation()
        first = self.database.persist_observation(original)
        second = self.database.persist_observation(original)
        self.assertEqual(first, second)
        self.assertEqual(self.database.observation_content_hashes(original.observation_id), (original.content_hash,))

    def test_same_observation_identity_retains_distinct_content_snapshots(self) -> None:
        first = observation()
        second = observation(
            collected_at="2026-01-03T00:10:00Z",
            semantic_data={"value": Decimal("12.75"), "unit": "SYNTHETIC_UNIT"},
        )
        self.assertEqual(first.observation_id, second.observation_id)
        self.assertNotEqual(first.content_hash, second.content_hash)
        self.database.persist_observation(first)
        self.database.persist_observation(second)
        self.assertEqual(len(self.database.observation_content_hashes(first.observation_id)), 2)
        self.assertTrue(self.database.has_multiple_observation_snapshots(first.observation_id))
        self.assertEqual(self.database.load_observation(first.content_hash).content_projection(), first.content_projection())
        self.assertEqual(self.database.load_observation(second.content_hash).content_projection(), second.content_projection())

    def test_impossible_snapshot_primary_key_contradiction_fails_closed(self) -> None:
        original = observation()
        self.database.persist_observation(original)
        connection = duckdb.connect(str(self.database_path))
        try:
            connection.execute(
                "UPDATE observation_snapshot SET content_projection_json = '{}' "
                "WHERE observation_content_hash = ?",
                [original.content_hash],
            )
        finally:
            connection.close()
        with self.assertRaises(PersistenceConflictError):
            self.database.persist_observation(original)

    def test_observation_version_exact_round_trip_and_duplicate_are_idempotent(self) -> None:
        item = observation()
        original = version(item)
        first = self.database.persist_observation_version(original)
        second = self.database.persist_observation_version(original)
        loaded = self.database.load_observation_version(first)
        self.assertEqual(first, second)
        self.assertEqual(loaded.observation_version_id, original.observation_version_id)
        self.assertEqual(loaded.content_hash, original.content_hash)
        self.assertEqual(loaded.content_projection(), original.content_projection())
        self.assertEqual(loaded.parent_version_id, H1)
        self.assertEqual(loaded.revision_available_at, "2026-01-03T00:02:00Z")
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM observation_version_snapshot").fetchone()[0], 1)
        finally:
            connection.close()

    def test_source_usage_rights_dimensions_are_independent_and_allowlisted(self) -> None:
        connection = duckdb.connect(str(self.database_path))
        try:
            columns = (
                "rights_profile_id", "source_id", "access_channel", "instrument_scope", *RIGHTS_DIMENSIONS,
                "evidence_reference", "review_status", "effective_from", "effective_to", "reviewed_at",
            )
            insert_sql = (
                f"INSERT INTO source_usage_rights ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})"
            )
            rights_values = (
                "UNKNOWN", "ALLOWED", "PROHIBITED", "REVIEW_REQUIRED", "UNKNOWN", "ALLOWED",
                "PROHIBITED", "REVIEW_REQUIRED", "UNKNOWN",
            )
            connection.execute(
                insert_sql,
                [
                    "SYNTHETIC_RIGHTS_MATRIX", "SYNTHETIC_SOURCE", "SYNTHETIC_CHANNEL", "SYNTHETIC_SCOPE",
                    *rights_values, "SYNTHETIC_EVIDENCE", "SYNTHETIC_REVIEW", "2026-09-24", None, None,
                ],
            )
            stored = connection.execute(
                f"SELECT {', '.join(RIGHTS_DIMENSIONS)} FROM source_usage_rights WHERE rights_profile_id = ?",
                ["SYNTHETIC_RIGHTS_MATRIX"],
            ).fetchone()
            self.assertEqual(stored, rights_values)
            for invalid_dimension in RIGHTS_DIMENSIONS:
                invalid_values = ["UNKNOWN"] * len(RIGHTS_DIMENSIONS)
                invalid_values[RIGHTS_DIMENSIONS.index(invalid_dimension)] = "UNREVIEWED"
                with self.subTest(invalid_dimension=invalid_dimension):
                    with self.assertRaises(duckdb.ConstraintException):
                        connection.execute(
                            insert_sql,
                            [
                                f"SYNTHETIC_INVALID_{invalid_dimension}", "SYNTHETIC_SOURCE",
                                "SYNTHETIC_CHANNEL", "SYNTHETIC_SCOPE", *invalid_values,
                                "SYNTHETIC_EVIDENCE", "SYNTHETIC_REVIEW", "2026-09-24", None, None,
                            ],
                        )
        finally:
            connection.close()

    def test_schema_has_no_credentials_or_procurement_domain_tables(self) -> None:
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            columns = connection.execute(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'main'"
            ).fetchall()
        finally:
            connection.close()
        schema_text = " ".join(" ".join(row) for row in columns).lower()
        self.assertNotIn("credential", schema_text)
        forbidden = ("supplier", "purchase_order", "inventory", "erp", "bom", "target_price", "private_forecast")
        self.assertTrue(all(value not in schema_text for value in forbidden))

    def test_current_c4_safety_contract_remains_closed(self) -> None:
        self.assertTrue(all(value is False for value in SAFETY_FLAGS.values()))
        self.assertEqual(PIT_ENABLED_SOURCES, ())
        self.assertEqual(RESEARCH_ENABLED_SOURCES, ())
        self.assertEqual(BACKTEST_ENABLED_SOURCES, ())
        self.assertEqual(RD3_OPEN_BLOCKERS, EXPECTED_BLOCKERS)


if __name__ == "__main__":
    unittest.main()
