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

    def test_source_usage_rights_state_allowlist_keeps_unknown_fail_closed(self) -> None:
        connection = duckdb.connect(str(self.database_path))
        try:
            connection.execute(
                "INSERT INTO source_usage_rights VALUES (?, ?, ?, ?, ?)",
                ["SYNTHETIC_RIGHTS_UNKNOWN", "SYNTHETIC_SOURCE", "UNKNOWN", "2026-09-24T00:00:00Z", "{}"],
            )
            stored = connection.execute(
                "SELECT rights_state FROM source_usage_rights WHERE source_usage_rights_id = ?",
                ["SYNTHETIC_RIGHTS_UNKNOWN"],
            ).fetchone()[0]
            self.assertEqual(stored, "UNKNOWN")
            self.assertNotEqual(stored, "ALLOWED")
            with self.assertRaises(duckdb.ConstraintException):
                connection.execute(
                    "INSERT INTO source_usage_rights VALUES (?, ?, ?, ?, ?)",
                    ["SYNTHETIC_RIGHTS_INVALID", "SYNTHETIC_SOURCE", "UNREVIEWED", "2026-09-24T00:00:00Z", "{}"],
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
