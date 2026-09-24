"""Private, append-only DuckDB persistence for C4 Observation contracts.

DB1A deliberately exposes persistence only for ``Observation`` and
``ObservationVersion``.  The remaining V1 tables reserve the approved DB1
shape for later stages and have no production persistence API here.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from pathlib import Path
import re
from typing import Any

import duckdb

from scripts.c4_rd_contract import (
    CALENDAR_ROLES,
    CalendarAssignment,
    Observation,
    ObservationVersion,
    canonical_json_bytes,
    canonical_timestamp,
    parse_json_strict,
)


SCHEMA_VERSION = "C4_PRIVATE_RESEARCH_DB_V1@1.0.0"
MIGRATION_ID = "C4_PRIVATE_RESEARCH_DB_V1_INITIAL"
MIGRATION_PATH = Path(__file__).resolve().parent / "sql" / "c4_private_research_db_v1.sql"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
APPROVED_TABLES = (
    "schema_migrations",
    "source_registry",
    "subject_registry",
    "instrument_registry",
    "source_usage_rights",
    "raw_payload",
    "raw_capture",
    "observation_identity",
    "observation_snapshot",
    "observation_calendar_assignment",
    "observation_version_identity",
    "observation_version_snapshot",
    "readiness_evaluation",
    "pit_dataset_manifest",
    "pit_dataset_row",
    "pit_feature_snapshot",
)
APPROVED_TABLE_COLUMNS = {
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
APPROVED_PRIMARY_KEYS = {
    "schema_migrations": ("migration_id",),
    "source_registry": ("source_id",),
    "subject_registry": ("subject_id",),
    "instrument_registry": ("instrument_id",),
    "source_usage_rights": ("rights_profile_id",),
    "raw_payload": ("raw_payload_hash",),
    "raw_capture": ("capture_id",),
    "observation_identity": ("observation_id",),
    "observation_snapshot": ("observation_content_hash",),
    "observation_calendar_assignment": ("observation_content_hash", "calendar_role"),
    "observation_version_identity": ("observation_version_id",),
    "observation_version_snapshot": ("observation_version_content_hash",),
    "readiness_evaluation": ("evaluation_hash",),
    "pit_dataset_manifest": ("dataset_identity",),
    "pit_dataset_row": ("dataset_identity", "manifest_row_ordinal"),
    "pit_feature_snapshot": ("row_content_hash", "feature_ordinal"),
}
APPROVED_UNIQUE_KEYS = {
    "pit_dataset_row": (("dataset_identity", "row_content_hash"),),
}
APPROVED_EXPLICIT_MISSING_NULLABLE_LINEAGE = (
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

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PrivateResearchDBError(RuntimeError):
    """Base error for fail-closed private research database operations."""


class DatabasePathError(PrivateResearchDBError):
    """Raised when a persistent database path violates the private-data boundary."""


class MigrationError(PrivateResearchDBError):
    """Raised when the explicit V1 migration cannot be safely applied."""


class PersistenceConflictError(PrivateResearchDBError):
    """Raised when stored authoritative content contradicts a contract object."""


def migration_sql_bytes() -> bytes:
    """Return the exact checked-in V1 migration bytes."""
    return MIGRATION_PATH.read_bytes()


def migration_checksum() -> str:
    """Return SHA-256 of the exact checked-in migration bytes."""
    return hashlib.sha256(migration_sql_bytes()).hexdigest()


def _canonical_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _require_hash(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PrivateResearchDBError(f"{field_name} must be lowercase SHA-256 hex")


class PrivateResearchDatabase:
    """Small explicit adapter for the append-only DB1A persistence boundary."""

    def __init__(self, database_path: str | Path) -> None:
        if str(database_path) in {"", ":memory:"}:
            raise DatabasePathError("a persistent database path is required")
        requested = Path(database_path).expanduser().resolve(strict=False)
        repository_root = REPOSITORY_ROOT.resolve(strict=True)
        if requested == repository_root or repository_root in requested.parents:
            raise DatabasePathError("private research database path must be outside the repository")
        if requested.exists() and requested.is_dir():
            raise DatabasePathError("database path must identify a file")
        if not requested.parent.exists() or not requested.parent.is_dir():
            raise DatabasePathError("database parent directory must already exist")
        self.database_path = requested

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.database_path))

    @staticmethod
    def _table_names(connection: duckdb.DuckDBPyConnection) -> tuple[str, ...]:
        rows = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        return tuple(row[0] for row in rows)

    @staticmethod
    def _migration_table_exists(connection: duckdb.DuckDBPyConnection) -> bool:
        row = connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name = 'schema_migrations'"
        ).fetchone()
        return bool(row and row[0] == 1)

    @staticmethod
    def _table_columns(connection: duckdb.DuckDBPyConnection) -> dict[str, tuple[str, ...]]:
        rows = connection.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position"
        ).fetchall()
        columns: dict[str, list[str]] = {}
        for table_name, column_name in rows:
            columns.setdefault(table_name, []).append(column_name)
        return {table_name: tuple(names) for table_name, names in columns.items()}

    @staticmethod
    def _primary_keys(connection: duckdb.DuckDBPyConnection) -> dict[str, tuple[str, ...]]:
        rows = connection.execute(
            "SELECT tc.table_name, kcu.column_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_catalog = kcu.constraint_catalog "
            "AND tc.constraint_schema = kcu.constraint_schema "
            "AND tc.constraint_name = kcu.constraint_name "
            "WHERE tc.table_schema = 'main' AND tc.constraint_type = 'PRIMARY KEY' "
            "ORDER BY tc.table_name, kcu.ordinal_position"
        ).fetchall()
        keys: dict[str, list[str]] = {}
        for table_name, column_name in rows:
            keys.setdefault(table_name, []).append(column_name)
        return {table_name: tuple(names) for table_name, names in keys.items()}

    @staticmethod
    def _unique_keys(
        connection: duckdb.DuckDBPyConnection,
    ) -> dict[str, tuple[tuple[str, ...], ...]]:
        rows = connection.execute(
            "SELECT tc.table_name, tc.constraint_name, kcu.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_catalog = kcu.constraint_catalog "
            "AND tc.constraint_schema = kcu.constraint_schema "
            "AND tc.constraint_name = kcu.constraint_name "
            "WHERE tc.table_schema = 'main' AND tc.constraint_type = 'UNIQUE' "
            "ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position"
        ).fetchall()
        grouped: dict[tuple[str, str], list[str]] = {}
        for table_name, constraint_name, column_name in rows:
            grouped.setdefault((table_name, constraint_name), []).append(column_name)
        keys: dict[str, list[tuple[str, ...]]] = {}
        for (table_name, _constraint_name), names in grouped.items():
            keys.setdefault(table_name, []).append(tuple(names))
        return {table_name: tuple(sorted(values)) for table_name, values in keys.items()}

    @staticmethod
    def _column_nullability(
        connection: duckdb.DuckDBPyConnection,
    ) -> dict[tuple[str, str], bool]:
        rows = connection.execute(
            "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'main'"
        ).fetchall()
        return {
            (table_name, column_name): is_nullable == "YES"
            for table_name, column_name, is_nullable in rows
        }

    def initialize_schema(self, *, applied_at: str, code_commit_sha: str) -> str:
        """Apply the exact V1 migration, or verify an identical prior application."""
        if not isinstance(code_commit_sha, str) or not code_commit_sha:
            raise MigrationError("code_commit_sha must be explicit")
        try:
            canonical_applied_at = canonical_timestamp(applied_at)
        except Exception as exc:
            raise MigrationError("applied_at must be an explicit canonicalizable timestamp") from exc
        sql_bytes = migration_sql_bytes()
        try:
            sql = sql_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MigrationError("migration SQL must be UTF-8") from exc
        checksum = hashlib.sha256(sql_bytes).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN TRANSACTION")
            tables_before = self._table_names(connection)
            if self._migration_table_exists(connection):
                rows = connection.execute(
                    "SELECT schema_version, applied_at, code_commit_sha, migration_checksum "
                    "FROM schema_migrations WHERE migration_id = ?",
                    [MIGRATION_ID],
                ).fetchall()
                if len(rows) != 1:
                    raise MigrationError("existing schema has no unique approved V1 migration record")
                schema_version, _stored_applied_at, _stored_sha, stored_checksum = rows[0]
                if schema_version != SCHEMA_VERSION:
                    raise MigrationError("existing migration schema version conflicts with V1")
                if stored_checksum != checksum:
                    raise MigrationError("existing migration checksum conflicts with exact V1 SQL")
            else:
                if tables_before:
                    raise MigrationError("non-empty database without migration authority is forbidden")
                connection.execute(sql)
                connection.execute(
                    "INSERT INTO schema_migrations "
                    "(schema_version, migration_id, applied_at, code_commit_sha, migration_checksum) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [SCHEMA_VERSION, MIGRATION_ID, canonical_applied_at, code_commit_sha, checksum],
                )
            actual_tables = self._table_names(connection)
            if set(actual_tables) != set(APPROVED_TABLES):
                raise MigrationError("database table set does not match the approved V1 schema")
            if self._table_columns(connection) != APPROVED_TABLE_COLUMNS:
                raise MigrationError("database columns do not match the approved V1 schema")
            if self._primary_keys(connection) != APPROVED_PRIMARY_KEYS:
                raise MigrationError("database primary keys do not match the approved V1 schema")
            if self._unique_keys(connection) != APPROVED_UNIQUE_KEYS:
                raise MigrationError("database unique keys do not match the approved V1 schema")
            nullability = self._column_nullability(connection)
            if any(
                not nullability.get(("pit_feature_snapshot", column_name), False)
                for column_name in APPROVED_EXPLICIT_MISSING_NULLABLE_LINEAGE
            ):
                raise MigrationError(
                    "PIT feature lineage nullability does not match the approved V1 schema"
                )
            connection.execute("COMMIT")
            return checksum
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            connection.close()

    def table_names(self) -> tuple[str, ...]:
        connection = self._connect()
        try:
            return self._table_names(connection)
        finally:
            connection.close()

    def migration_record(self) -> Mapping[str, str]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT schema_version, migration_id, applied_at, code_commit_sha, migration_checksum "
                "FROM schema_migrations WHERE migration_id = ?",
                [MIGRATION_ID],
            ).fetchone()
            if row is None:
                raise MigrationError("approved V1 migration is not applied")
            return dict(zip(
                ("schema_version", "migration_id", "applied_at", "code_commit_sha", "migration_checksum"),
                row,
                strict=True,
            ))
        finally:
            connection.close()

    def persist_observation(self, observation: Observation) -> str:
        if type(observation) is not Observation:
            raise PrivateResearchDBError("only an exact validated Observation may be persisted")
        observation_id = observation.observation_id
        content_hash = observation.content_hash
        identity_json = _canonical_text(observation.identity_projection())
        content_json = _canonical_text(observation.content_projection())
        semantic_json = _canonical_text(observation.semantic_data)
        identity_values = (
            observation_id,
            observation.source_id,
            observation.source_record_identifier,
            observation.metric_id,
            observation.instrument_id,
            observation.source_period_type,
            observation.source_market_date,
            observation.source_period_start_date,
            observation.source_period_end_date,
            identity_json,
        )
        snapshot_values = (
            content_hash,
            observation_id,
            observation.data_origin,
            observation.operational_status,
            observation.scheduler_execution_at,
            observation.local_business_date,
            observation.source_business_date,
            observation.scheduler_business_date,
            observation.source_publication_at,
            observation.collected_at,
            observation.observed_at,
            observation.source_available_at,
            observation.channel_available_at,
            observation.created_at,
            semantic_json,
            content_json,
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN TRANSACTION")
            stored_identity = connection.execute(
                "SELECT * FROM observation_identity WHERE observation_id = ?", [observation_id]
            ).fetchone()
            if stored_identity is None:
                connection.execute(
                    "INSERT INTO observation_identity VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    identity_values,
                )
            elif tuple(stored_identity) != identity_values:
                raise PersistenceConflictError("observation identity contradicts immutable stored identity")

            stored_snapshot = connection.execute(
                "SELECT * FROM observation_snapshot WHERE observation_content_hash = ?", [content_hash]
            ).fetchone()
            if stored_snapshot is None:
                connection.execute(
                    "INSERT INTO observation_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    snapshot_values,
                )
                for assignment in observation.calendar_assignments:
                    connection.execute(
                        "INSERT INTO observation_calendar_assignment VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            content_hash,
                            assignment.role,
                            assignment.subject_id,
                            assignment.status,
                            assignment.calendar_reference_id,
                            assignment.calendar_version,
                            assignment.calendar_hash,
                            _canonical_text(assignment.content_projection()),
                        ),
                    )
            elif tuple(stored_snapshot) != snapshot_values:
                raise PersistenceConflictError("observation content hash contradicts immutable stored snapshot")

            stored_assignments = connection.execute(
                "SELECT calendar_role, subject_id, assignment_status, calendar_reference_id, "
                "calendar_version, calendar_hash, assignment_projection_json "
                "FROM observation_calendar_assignment WHERE observation_content_hash = ? "
                "ORDER BY CASE calendar_role WHEN 'MARKET' THEN 1 WHEN 'PUBLICATION' THEN 2 "
                "WHEN 'LOCAL_OPERATIONAL' THEN 3 WHEN 'SCHEDULER' THEN 4 END",
                [content_hash],
            ).fetchall()
            expected_assignments = [
                (
                    item.role,
                    item.subject_id,
                    item.status,
                    item.calendar_reference_id,
                    item.calendar_version,
                    item.calendar_hash,
                    _canonical_text(item.content_projection()),
                )
                for item in observation.calendar_assignments
            ]
            if [tuple(row) for row in stored_assignments] != expected_assignments:
                raise PersistenceConflictError("calendar assignments contradict immutable stored snapshot")
            connection.execute("COMMIT")
            return content_hash
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            connection.close()

    def load_observation(self, observation_content_hash: str) -> Observation:
        _require_hash(observation_content_hash, "observation_content_hash")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT i.observation_id, i.source_id, i.source_record_identifier, i.metric_id, "
                "i.instrument_id, i.source_period_type, i.source_market_date, "
                "i.source_period_start_date, i.source_period_end_date, i.identity_projection_json, "
                "s.data_origin, s.operational_status, s.scheduler_execution_at, "
                "s.local_business_date, s.source_business_date, s.scheduler_business_date, "
                "s.source_publication_at, s.collected_at, s.observed_at, s.source_available_at, "
                "s.channel_available_at, s.created_at, s.semantic_data_json, s.content_projection_json "
                "FROM observation_snapshot s JOIN observation_identity i USING (observation_id) "
                "WHERE s.observation_content_hash = ?",
                [observation_content_hash],
            ).fetchone()
            if row is None:
                raise KeyError(observation_content_hash)
            assignment_rows = connection.execute(
                "SELECT subject_id, calendar_role, assignment_status, calendar_reference_id, "
                "calendar_version, calendar_hash, assignment_projection_json "
                "FROM observation_calendar_assignment WHERE observation_content_hash = ?",
                [observation_content_hash],
            ).fetchall()
        finally:
            connection.close()
        if len(assignment_rows) != 4:
            raise PersistenceConflictError("stored observation does not have four calendar assignments")
        by_role: dict[str, CalendarAssignment] = {}
        for subject_id, role, status, reference_id, version, calendar_hash, stored_json in assignment_rows:
            assignment = CalendarAssignment(subject_id, role, status, reference_id, version, calendar_hash)
            if _canonical_text(assignment.content_projection()) != stored_json or role in by_role:
                raise PersistenceConflictError("stored calendar assignment failed canonical validation")
            by_role[role] = assignment
        if set(by_role) != set(CALENDAR_ROLES):
            raise PersistenceConflictError("stored calendar role set is incomplete")
        (
            stored_observation_id,
            source_id,
            source_record_identifier,
            metric_id,
            instrument_id,
            source_period_type,
            source_market_date,
            source_period_start_date,
            source_period_end_date,
            stored_identity_json,
            data_origin,
            operational_status,
            scheduler_execution_at,
            local_business_date,
            source_business_date,
            scheduler_business_date,
            source_publication_at,
            collected_at,
            observed_at,
            source_available_at,
            channel_available_at,
            created_at,
            semantic_data_json,
            stored_content_json,
        ) = row
        semantic_data = parse_json_strict(semantic_data_json)
        if not isinstance(semantic_data, dict):
            raise PersistenceConflictError("stored observation semantic_data is not an object")
        loaded = Observation(
            source_id=source_id,
            source_record_identifier=source_record_identifier,
            metric_id=metric_id,
            instrument_id=instrument_id,
            source_period_type=source_period_type,
            source_market_date=source_market_date,
            source_period_start_date=source_period_start_date,
            source_period_end_date=source_period_end_date,
            calendar_assignments=tuple(by_role[role] for role in CALENDAR_ROLES),
            data_origin=data_origin,
            operational_status=operational_status,
            scheduler_execution_at=scheduler_execution_at,
            local_business_date=local_business_date,
            source_business_date=source_business_date,
            scheduler_business_date=scheduler_business_date,
            source_publication_at=source_publication_at,
            collected_at=collected_at,
            observed_at=observed_at,
            source_available_at=source_available_at,
            channel_available_at=channel_available_at,
            created_at=created_at,
            semantic_data=semantic_data,
        )
        if loaded.observation_id != stored_observation_id:
            raise PersistenceConflictError("loaded observation identity hash drifted")
        if loaded.content_hash != observation_content_hash:
            raise PersistenceConflictError("loaded observation content hash drifted")
        if _canonical_text(loaded.identity_projection()) != stored_identity_json:
            raise PersistenceConflictError("loaded observation identity projection drifted")
        if _canonical_text(loaded.content_projection()) != stored_content_json:
            raise PersistenceConflictError("loaded observation content projection drifted")
        return loaded

    def observation_content_hashes(self, observation_id: str) -> tuple[str, ...]:
        _require_hash(observation_id, "observation_id")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT observation_content_hash FROM observation_snapshot "
                "WHERE observation_id = ? ORDER BY observation_content_hash",
                [observation_id],
            ).fetchall()
            return tuple(row[0] for row in rows)
        finally:
            connection.close()

    def has_multiple_observation_snapshots(self, observation_id: str) -> bool:
        return len(self.observation_content_hashes(observation_id)) > 1

    def persist_observation_version(self, version: ObservationVersion) -> str:
        if type(version) is not ObservationVersion:
            raise PrivateResearchDBError("only an exact validated ObservationVersion may be persisted")
        version_id = version.observation_version_id
        content_hash = version.content_hash
        identity_values = (
            version_id,
            version.observation_id,
            version.source_version_or_release_key,
            version.stable_version_key,
            version.raw_payload_hash,
            version.transformation_version,
            _canonical_text(version.identity_projection()),
        )
        snapshot_values = (
            content_hash,
            version_id,
            version.parent_version_id,
            version.revision_available_at,
            version.collected_at,
            version.observed_at,
            version.created_at,
            _canonical_text(version.semantic_data),
            _canonical_text(version.content_projection()),
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN TRANSACTION")
            stored_identity = connection.execute(
                "SELECT * FROM observation_version_identity WHERE observation_version_id = ?", [version_id]
            ).fetchone()
            if stored_identity is None:
                connection.execute(
                    "INSERT INTO observation_version_identity VALUES (?, ?, ?, ?, ?, ?, ?)",
                    identity_values,
                )
            elif tuple(stored_identity) != identity_values:
                raise PersistenceConflictError("version identity contradicts immutable stored identity")
            stored_snapshot = connection.execute(
                "SELECT * FROM observation_version_snapshot WHERE observation_version_content_hash = ?",
                [content_hash],
            ).fetchone()
            if stored_snapshot is None:
                connection.execute(
                    "INSERT INTO observation_version_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    snapshot_values,
                )
            elif tuple(stored_snapshot) != snapshot_values:
                raise PersistenceConflictError("version content hash contradicts immutable stored snapshot")
            connection.execute("COMMIT")
            return content_hash
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            connection.close()

    def load_observation_version(self, observation_version_content_hash: str) -> ObservationVersion:
        _require_hash(observation_version_content_hash, "observation_version_content_hash")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT i.observation_version_id, i.observation_id, i.source_version_or_release_key, "
                "i.stable_version_key, i.raw_payload_hash, i.transformation_version, "
                "i.identity_projection_json, s.parent_version_id, s.revision_available_at, "
                "s.collected_at, s.observed_at, s.created_at, s.semantic_data_json, "
                "s.content_projection_json FROM observation_version_snapshot s "
                "JOIN observation_version_identity i USING (observation_version_id) "
                "WHERE s.observation_version_content_hash = ?",
                [observation_version_content_hash],
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(observation_version_content_hash)
        (
            stored_version_id,
            observation_id,
            source_version_or_release_key,
            stable_version_key,
            raw_payload_hash,
            transformation_version,
            stored_identity_json,
            parent_version_id,
            revision_available_at,
            collected_at,
            observed_at,
            created_at,
            semantic_data_json,
            stored_content_json,
        ) = row
        semantic_data = parse_json_strict(semantic_data_json)
        if not isinstance(semantic_data, dict):
            raise PersistenceConflictError("stored version semantic_data is not an object")
        loaded = ObservationVersion(
            observation_id=observation_id,
            source_version_or_release_key=source_version_or_release_key,
            stable_version_key=stable_version_key,
            raw_payload_hash=raw_payload_hash,
            transformation_version=transformation_version,
            parent_version_id=parent_version_id,
            revision_available_at=revision_available_at,
            collected_at=collected_at,
            observed_at=observed_at,
            created_at=created_at,
            semantic_data=semantic_data,
        )
        if loaded.observation_version_id != stored_version_id:
            raise PersistenceConflictError("loaded version identity hash drifted")
        if loaded.content_hash != observation_version_content_hash:
            raise PersistenceConflictError("loaded version content hash drifted")
        if _canonical_text(loaded.identity_projection()) != stored_identity_json:
            raise PersistenceConflictError("loaded version identity projection drifted")
        if _canonical_text(loaded.content_projection()) != stored_content_json:
            raise PersistenceConflictError("loaded version content projection drifted")
        return loaded
