"""Private, append-only DuckDB persistence for approved C4 contracts.

DB1B adds synthetic/non-operational persistence for RD4 readiness evaluations
and RD6 PIT dataset authority while preserving the frozen DB1A V1 schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
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
    build_real_historical_observation,
    canonical_hash,
    canonical_json_bytes,
    canonical_timestamp,
    parse_date,
    parse_json_strict,
)
from scripts.c4_rd_pit_dataset import (
    PITDatasetManifest,
    PITDatasetResult,
    PITDatasetRow,
    PITFeatureSnapshot,
)
from scripts.c4_rd_readiness_evaluator import EvidenceReference, ReadinessEvaluationResult


SCHEMA_VERSION = "C4_PRIVATE_RESEARCH_DB_V1@1.0.0"
MIGRATION_ID = "C4_PRIVATE_RESEARCH_DB_V1_INITIAL"
MIGRATION_PATH = Path(__file__).resolve().parent / "sql" / "c4_private_research_db_v1.sql"
APPROVED_MIGRATION_CHECKSUM = (
    "91b858d068df103e2d6629153923f597999cee222470f98321fcc4b89f93dcd3"
)
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


def _canonicalize_migration_sql_bytes(raw_bytes: bytes) -> bytes:
    """Canonicalize only CRLF newlines and reject unsupported bare CR bytes."""
    canonical_bytes = raw_bytes.replace(b"\r\n", b"\n")
    if b"\r" in canonical_bytes:
        raise MigrationError("migration SQL contains an unsupported bare CR byte")
    return canonical_bytes


def migration_sql_bytes() -> bytes:
    """Return the canonical LF bytes of the frozen approved V1 migration."""
    canonical_bytes = _canonicalize_migration_sql_bytes(MIGRATION_PATH.read_bytes())
    computed_checksum = hashlib.sha256(canonical_bytes).hexdigest()
    if computed_checksum != APPROVED_MIGRATION_CHECKSUM:
        raise MigrationError("canonical migration SQL checksum does not match approved V1 authority")
    return canonical_bytes


def migration_checksum() -> str:
    """Return SHA-256 of the canonical approved V1 migration bytes."""
    return hashlib.sha256(migration_sql_bytes()).hexdigest()


def _canonical_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _require_hash(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PrivateResearchDBError(f"{field_name} must be lowercase SHA-256 hex")


def _canonical_persisted_at(value: str) -> str:
    if not isinstance(value, str):
        raise PrivateResearchDBError("persisted_at must be an explicit canonicalizable timestamp")
    try:
        return canonical_timestamp(value)
    except Exception as exc:
        raise PrivateResearchDBError(
            "persisted_at must be an explicit canonicalizable timestamp"
        ) from exc


def _stored_json(value: str, expected_type: type, field_name: str) -> Any:
    try:
        parsed = parse_json_strict(value)
    except Exception as exc:
        raise PersistenceConflictError(f"stored {field_name} is not strict JSON") from exc
    if type(parsed) is not expected_type:
        raise PersistenceConflictError(
            f"stored {field_name} must be a JSON {expected_type.__name__}"
        )
    return parsed


def _rehydrate_world_bank_copper_semantic_data(
    semantic_data: dict[str, Any],
    *,
    source_id: str | None,
    metric_id: str | None,
    instrument_id: str | None,
    source_period_type: str | None,
) -> dict[str, Any]:
    """Restore persisted Decimal type only for the approved World Bank domain."""
    if (
        source_id,
        metric_id,
        instrument_id,
        source_period_type,
    ) != (
        "WORLD_BANK",
        "MONTHLY_PRICE",
        "copper_world_bank_monthly",
        "MONTH",
    ):
        return semantic_data

    value = semantic_data.get("value")
    if type(value) is Decimal:
        return semantic_data
    if type(value) is int:
        return {**semantic_data, "value": Decimal(value)}
    raise PersistenceConflictError(
        "stored World Bank Copper semantic value is not a Decimal or exact integer"
    )


class PrivateResearchDatabase:
    """Small explicit adapter for the frozen append-only DB1 persistence boundary."""

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

    def _persist_immutable_row(
        self,
        table_name: str,
        values: tuple[Any, ...],
    ) -> str:
        if table_name not in {
            "source_registry", "subject_registry", "instrument_registry",
            "source_usage_rights", "raw_payload", "raw_capture",
        }:
            raise PrivateResearchDBError("unsupported immutable registry table")
        columns = APPROVED_TABLE_COLUMNS[table_name]
        if len(values) != len(columns):
            raise PrivateResearchDBError("immutable registry row does not match the frozen schema")
        key_column = APPROVED_PRIMARY_KEYS[table_name][0]
        key_value = values[columns.index(key_column)]
        connection = self._connect()
        try:
            connection.execute("BEGIN TRANSACTION")
            stored = connection.execute(
                f"SELECT {', '.join(columns)} FROM {table_name} WHERE {key_column} = ?",
                [key_value],
            ).fetchone()
            if stored is None:
                connection.execute(
                    f"INSERT INTO {table_name} ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in values)})",
                    values,
                )
            elif tuple(stored) != values:
                raise PersistenceConflictError(
                    f"{table_name} key contradicts immutable stored content"
                )
            connection.execute("COMMIT")
            return str(key_value)
        except duckdb.ConstraintException as exc:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise PersistenceConflictError(
                f"{table_name} persistence constraint conflict"
            ) from exc
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            connection.close()

    def _load_immutable_row(self, table_name: str, key_value: str) -> Mapping[str, Any]:
        if table_name not in {
            "source_registry", "subject_registry", "instrument_registry",
            "source_usage_rights", "raw_payload", "raw_capture",
        }:
            raise PrivateResearchDBError("unsupported immutable registry table")
        columns = APPROVED_TABLE_COLUMNS[table_name]
        key_column = APPROVED_PRIMARY_KEYS[table_name][0]
        connection = self._connect()
        try:
            row = connection.execute(
                f"SELECT {', '.join(columns)} FROM {table_name} WHERE {key_column} = ?",
                [key_value],
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(key_value)
        return dict(zip(columns, row, strict=True))

    def persist_source_registry(
        self,
        *,
        source_id: str,
        source_name: str,
        access_channel: str,
        default_timezone: str,
        data_classification: str,
        registry_version: str,
        active: bool,
        created_at: str,
    ) -> str:
        if type(active) is not bool:
            raise PrivateResearchDBError("source registry active must be a strict boolean")
        values = (
            source_id, source_name, access_channel, default_timezone, data_classification,
            registry_version, active, _canonical_persisted_at(created_at),
        )
        if any(not isinstance(value, str) or not value for value in values[:6]):
            raise PrivateResearchDBError("source registry strings must be explicit")
        return self._persist_immutable_row("source_registry", values)

    def load_source_registry(self, source_id: str) -> Mapping[str, Any]:
        return self._load_immutable_row("source_registry", source_id)

    def persist_subject_registry(
        self,
        *,
        subject_id: str,
        subject_snapshot: Mapping[str, Any],
        registered_at: str,
    ) -> str:
        if not isinstance(subject_id, str) or not subject_id or not isinstance(subject_snapshot, Mapping):
            raise PrivateResearchDBError("subject registry values must be explicit")
        return self._persist_immutable_row(
            "subject_registry",
            (subject_id, _canonical_text(subject_snapshot), _canonical_persisted_at(registered_at)),
        )

    def load_subject_registry(self, subject_id: str) -> Mapping[str, Any]:
        return self._load_immutable_row("subject_registry", subject_id)

    def persist_instrument_registry(
        self,
        *,
        instrument_id: str,
        subject_id: str,
        source_id: str,
        source_symbol: str,
        market_or_venue: str,
        metric_id: str,
        quote_type: str,
        term: str,
        currency: str,
        unit: str,
        source_period_type: str,
        instrument_version: str,
        active_from: str,
        active_to: str | None,
    ) -> str:
        string_values = (
            instrument_id, subject_id, source_id, source_symbol, market_or_venue, metric_id,
            quote_type, term, currency, unit, source_period_type, instrument_version,
        )
        if any(not isinstance(value, str) or not value for value in string_values):
            raise PrivateResearchDBError("instrument registry strings must be explicit")
        parse_date(active_from)
        if active_to is not None:
            parse_date(active_to)
            if active_to < active_from:
                raise PrivateResearchDBError("instrument active_to must not precede active_from")
        return self._persist_immutable_row(
            "instrument_registry", string_values + (active_from, active_to),
        )

    def load_instrument_registry(self, instrument_id: str) -> Mapping[str, Any]:
        return self._load_immutable_row("instrument_registry", instrument_id)

    def persist_source_usage_rights(
        self,
        *,
        rights_profile_id: str,
        source_id: str,
        access_channel: str,
        instrument_scope: str,
        automated_access: str,
        private_storage: str,
        historical_archive: str,
        internal_analysis: str,
        backtest: str,
        prediction: str,
        internal_display: str,
        internet_display: str,
        redistribution: str,
        evidence_reference: str,
        review_status: str,
        effective_from: str,
        effective_to: str | None,
        reviewed_at: str | None,
    ) -> str:
        decisions = (
            automated_access, private_storage, historical_archive, internal_analysis, backtest,
            prediction, internal_display, internet_display, redistribution,
        )
        if any(
            value not in {"ALLOWED", "PROHIBITED", "REVIEW_REQUIRED", "UNKNOWN"}
            for value in decisions
        ):
            raise PrivateResearchDBError("source usage rights decision is not allowlisted")
        required_strings = (
            rights_profile_id, source_id, access_channel, instrument_scope,
            evidence_reference, review_status,
        )
        if any(not isinstance(value, str) or not value for value in required_strings):
            raise PrivateResearchDBError("source usage rights strings must be explicit")
        parse_date(effective_from)
        if effective_to is not None:
            parse_date(effective_to)
            if effective_to < effective_from:
                raise PrivateResearchDBError("rights effective_to must not precede effective_from")
        canonical_reviewed_at = (
            None if reviewed_at is None else _canonical_persisted_at(reviewed_at)
        )
        return self._persist_immutable_row(
            "source_usage_rights",
            (
                rights_profile_id, source_id, access_channel, instrument_scope, *decisions,
                evidence_reference, review_status, effective_from, effective_to,
                canonical_reviewed_at,
            ),
        )

    def load_source_usage_rights(self, rights_profile_id: str) -> Mapping[str, Any]:
        return self._load_immutable_row("source_usage_rights", rights_profile_id)

    def persist_raw_payload(
        self,
        *,
        raw_payload_hash: str,
        relative_path: str,
        content_type: str,
        byte_size: int,
        data_classification: str,
        first_seen_at: str,
    ) -> str:
        _require_hash(raw_payload_hash, "raw_payload_hash")
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise PrivateResearchDBError("raw payload path must be a safe relative path")
        if type(byte_size) is not int or byte_size < 0:
            raise PrivateResearchDBError("raw payload byte_size must be a non-negative integer")
        if any(not isinstance(value, str) or not value for value in (content_type, data_classification)):
            raise PrivateResearchDBError("raw payload metadata strings must be explicit")
        return self._persist_immutable_row(
            "raw_payload",
            (
                raw_payload_hash, path.as_posix(), content_type, byte_size, data_classification,
                _canonical_persisted_at(first_seen_at),
            ),
        )

    def load_raw_payload(self, raw_payload_hash: str) -> Mapping[str, Any]:
        _require_hash(raw_payload_hash, "raw_payload_hash")
        return self._load_immutable_row("raw_payload", raw_payload_hash)

    def persist_raw_capture(
        self,
        *,
        capture_id: str,
        source_id: str,
        instrument_id: str | None,
        access_channel: str,
        source_locator_safe: str,
        collected_at: str,
        collection_status: str,
        raw_payload_hash: str,
        collector_version: str,
        rights_profile_id: str,
        error_class: str | None,
        created_at: str,
    ) -> str:
        _require_hash(raw_payload_hash, "raw_payload_hash")
        required_strings = (
            capture_id, source_id, access_channel, source_locator_safe, collection_status,
            collector_version, rights_profile_id,
        )
        if any(not isinstance(value, str) or not value for value in required_strings):
            raise PrivateResearchDBError("raw capture strings must be explicit")
        if instrument_id is not None and (not isinstance(instrument_id, str) or not instrument_id):
            raise PrivateResearchDBError("raw capture instrument_id must be explicit when present")
        if error_class is not None and (not isinstance(error_class, str) or not error_class):
            raise PrivateResearchDBError("raw capture error_class must be explicit when present")
        return self._persist_immutable_row(
            "raw_capture",
            (
                capture_id, source_id, instrument_id, access_channel, source_locator_safe,
                _canonical_persisted_at(collected_at), collection_status, raw_payload_hash,
                collector_version, rights_profile_id, error_class,
                _canonical_persisted_at(created_at),
            ),
        )

    def load_raw_capture(self, capture_id: str) -> Mapping[str, Any]:
        return self._load_immutable_row("raw_capture", capture_id)

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
        semantic_data = _rehydrate_world_bank_copper_semantic_data(
            semantic_data,
            source_id=source_id,
            metric_id=metric_id,
            instrument_id=instrument_id,
            source_period_type=source_period_type,
        )
        observation_values = {
            "source_id": source_id,
            "source_record_identifier": source_record_identifier,
            "metric_id": metric_id,
            "instrument_id": instrument_id,
            "source_period_type": source_period_type,
            "source_market_date": source_market_date,
            "source_period_start_date": source_period_start_date,
            "source_period_end_date": source_period_end_date,
            "calendar_assignments": tuple(by_role[role] for role in CALENDAR_ROLES),
            "data_origin": data_origin,
            "operational_status": operational_status,
            "scheduler_execution_at": scheduler_execution_at,
            "local_business_date": local_business_date,
            "source_business_date": source_business_date,
            "scheduler_business_date": scheduler_business_date,
            "source_publication_at": source_publication_at,
            "collected_at": collected_at,
            "observed_at": observed_at,
            "source_available_at": source_available_at,
            "channel_available_at": channel_available_at,
            "created_at": created_at,
            "semantic_data": semantic_data,
        }
        if data_origin == "REAL_HISTORICAL" and operational_status == "REAL_NON_OPERATIONAL":
            loaded = build_real_historical_observation(**observation_values)
        else:
            loaded = Observation(**observation_values)
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

    def observation_version_content_hashes(self, observation_version_id: str) -> tuple[str, ...]:
        _require_hash(observation_version_id, "observation_version_id")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT observation_version_content_hash FROM observation_version_snapshot "
                "WHERE observation_version_id = ? ORDER BY observation_version_content_hash",
                [observation_version_id],
            ).fetchall()
            return tuple(row[0] for row in rows)
        finally:
            connection.close()

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
                "s.content_projection_json, o.source_id, o.metric_id, o.instrument_id, "
                "o.source_period_type FROM observation_version_snapshot s "
                "JOIN observation_version_identity i USING (observation_version_id) "
                "LEFT JOIN observation_identity o ON o.observation_id = i.observation_id "
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
            source_id,
            metric_id,
            instrument_id,
            source_period_type,
        ) = row
        semantic_data = parse_json_strict(semantic_data_json)
        if not isinstance(semantic_data, dict):
            raise PersistenceConflictError("stored version semantic_data is not an object")
        semantic_data = _rehydrate_world_bank_copper_semantic_data(
            semantic_data,
            source_id=source_id,
            metric_id=metric_id,
            instrument_id=instrument_id,
            source_period_type=source_period_type,
        )
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

    def persist_readiness_evaluation(
        self,
        result: ReadinessEvaluationResult,
        *,
        persisted_at: str,
    ) -> str:
        """Append one exact RD4 result, retaining first-persistence metadata."""
        if type(result) is not ReadinessEvaluationResult:
            raise PrivateResearchDBError(
                "only an exact validated ReadinessEvaluationResult may be persisted"
            )
        canonical_persisted_at = _canonical_persisted_at(persisted_at)
        evaluation_hash = canonical_hash(
            "MANIFEST_CONTENT",
            {"rd4_2_evaluation_result": result.as_dict()},
        )
        values = (
            evaluation_hash,
            result.observation_id,
            result.observation_version_id,
            result.evaluation_role,
            result.cutoff_at,
            result.evaluation_as_of_at,
            result.label_available_at,
            result.readiness_state,
            result.eligibility_state,
            _canonical_text(result.reason_codes),
            _canonical_text(result.blocker_ids),
            _canonical_text(tuple(item.as_dict() for item in result.evidence_references)),
            result.contract_version,
            result.evaluator_version,
            result.rule_bundle_version,
            result.rule_bundle_hash,
            result.source_profile_id,
            result.source_profile_version,
            result.observation_content_hash,
            result.observation_version_content_hash,
            canonical_persisted_at,
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN TRANSACTION")
            stored = connection.execute(
                "SELECT * FROM readiness_evaluation WHERE evaluation_hash = ?",
                [evaluation_hash],
            ).fetchone()
            if stored is None:
                connection.execute(
                    f"INSERT INTO readiness_evaluation VALUES ({', '.join('?' for _ in values)})",
                    values,
                )
            elif tuple(stored[:-1]) != values[:-1]:
                raise PersistenceConflictError(
                    "readiness evaluation hash contradicts immutable stored authority"
                )
            connection.execute("COMMIT")
            return evaluation_hash
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            connection.close()

    def load_readiness_evaluation(self, evaluation_hash: str) -> ReadinessEvaluationResult:
        """Reconstruct and re-hash one exact persisted RD4 result."""
        _require_hash(evaluation_hash, "evaluation_hash")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM readiness_evaluation WHERE evaluation_hash = ?",
                [evaluation_hash],
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(evaluation_hash)
        try:
            reason_codes = _stored_json(row[9], list, "reason_codes_json")
            blocker_ids = _stored_json(row[10], list, "blocker_ids_json")
            evidence_values = _stored_json(row[11], list, "evidence_references_json")
            if not all(type(item) is str for item in reason_codes + blocker_ids):
                raise PersistenceConflictError("stored readiness codes must be strings")
            evidence: list[EvidenceReference] = []
            for item in evidence_values:
                if type(item) is not dict or set(item) != {
                    "evidence_kind", "evidence_id", "content_hash",
                } or not all(type(value) is str for value in item.values()):
                    raise PersistenceConflictError(
                        "stored readiness evidence reference is malformed"
                    )
                evidence.append(EvidenceReference(**item))
            loaded = ReadinessEvaluationResult(
                observation_id=row[1],
                observation_version_id=row[2],
                evaluation_role=row[3],
                cutoff_at=row[4],
                evaluation_as_of_at=row[5],
                label_available_at=row[6],
                readiness_state=row[7],
                eligibility_state=row[8],
                reason_codes=tuple(reason_codes),
                blocker_ids=tuple(blocker_ids),
                evidence_references=tuple(evidence),
                contract_version=row[12],
                evaluator_version=row[13],
                rule_bundle_version=row[14],
                rule_bundle_hash=row[15],
                source_profile_id=row[16],
                source_profile_version=row[17],
                observation_content_hash=row[18],
                observation_version_content_hash=row[19],
            )
            recomputed_hash = canonical_hash(
                "MANIFEST_CONTENT",
                {"rd4_2_evaluation_result": loaded.as_dict()},
            )
            if row[0] != evaluation_hash or recomputed_hash != evaluation_hash:
                raise PersistenceConflictError("loaded readiness evaluation hash drifted")
            return loaded
        except PersistenceConflictError:
            raise
        except Exception as exc:
            raise PersistenceConflictError(
                "stored readiness evaluation cannot reconstruct authoritative RD4 content"
            ) from exc

    def persist_pit_dataset(
        self,
        result: PITDatasetResult,
        *,
        persisted_at: str,
    ) -> str:
        """Atomically append one exact synthetic RD6 dataset authority."""
        if type(result) is not PITDatasetResult:
            raise PrivateResearchDBError(
                "only an exact validated PITDatasetResult may be persisted"
            )
        canonical_persisted_at = _canonical_persisted_at(persisted_at)
        dataset_identity = result.dataset_identity
        if dataset_identity != result.manifest.manifest_hash:
            raise PersistenceConflictError("PIT result identity does not match its manifest")

        manifest = result.manifest
        manifest_values = (
            dataset_identity,
            manifest.manifest_type,
            manifest.manifest_version,
            manifest.dataset_contract_version,
            manifest.feature_set_version,
            manifest.feature_computation_profile_version,
            manifest.cutoff_policy_version,
            _canonical_text(manifest.rule_bundle_bindings),
            _canonical_text(manifest.source_profile_bindings),
            _canonical_text(manifest.authorization_snapshot),
            _canonical_text(manifest.request_scope),
            manifest.include_count,
            manifest.exclude_count,
            manifest.quarantine_count,
            _canonical_text(manifest.exclusion_reason_summary),
            _canonical_text(manifest.quarantine_reason_summary),
            canonical_persisted_at,
        )
        expected_rows: list[tuple[Any, ...]] = []
        expected_features: dict[str, list[tuple[Any, ...]]] = {}
        for row_ordinal, item in enumerate(result.rows):
            row_values = (
                item.row_content_hash,
                item.row_id,
                dataset_identity,
                row_ordinal,
                item.research_subject_id,
                item.observation_version_id,
                item.research_cutoff_at,
                item.feature_set_version,
                item.feature_computation_profile_version,
                item.cutoff_policy_version,
                item.feature_available_at_max,
                _canonical_text(item.rd4_authority_bindings),
                _canonical_text(item.rd5_authority_bindings),
                _canonical_text(item.rule_bundle_bindings),
                _canonical_text(item.source_profile_bindings),
                _canonical_text(item.label_specification),
                _canonical_text(item.authorization_snapshot),
                item.operational_status,
                canonical_persisted_at,
            )
            expected_rows.append(row_values)
            feature_values: list[tuple[Any, ...]] = []
            for feature_ordinal, feature in enumerate(item.features):
                feature_values.append((
                    item.row_content_hash,
                    feature_ordinal,
                    feature.feature_content_hash,
                    feature.feature_definition_id,
                    feature.feature_definition_version,
                    feature.feature_computation_profile_version,
                    feature.research_cutoff_at,
                    feature.value_state,
                    _canonical_text(feature.value),
                    feature.source_observation_id,
                    feature.source_observation_version_id,
                    feature.source_observed_at,
                    feature.source_available_at,
                    feature.source_profile_id,
                    feature.source_profile_version,
                    _canonical_text(feature.evidence_refs),
                    feature.rd4_evaluation_hash,
                    feature.rd5_decision_hash,
                    feature.authority_binding_ref,
                ))
            expected_features[item.row_content_hash] = feature_values

        connection = self._connect()
        try:
            connection.execute("BEGIN TRANSACTION")
            stored_manifest = connection.execute(
                "SELECT * FROM pit_dataset_manifest WHERE dataset_identity = ?",
                [dataset_identity],
            ).fetchone()
            if stored_manifest is None:
                connection.execute(
                    f"INSERT INTO pit_dataset_manifest VALUES "
                    f"({', '.join('?' for _ in manifest_values)})",
                    manifest_values,
                )
            elif tuple(stored_manifest[:-1]) != manifest_values[:-1]:
                raise PersistenceConflictError(
                    "PIT manifest identity contradicts immutable stored authority"
                )

            for row_values in expected_rows:
                stored_row = connection.execute(
                    "SELECT * FROM pit_dataset_row "
                    "WHERE dataset_identity = ? AND manifest_row_ordinal = ?",
                    [dataset_identity, row_values[3]],
                ).fetchone()
                if stored_row is None:
                    conflicting_membership = connection.execute(
                        "SELECT 1 FROM pit_dataset_row "
                        "WHERE dataset_identity = ? AND row_content_hash = ?",
                        [dataset_identity, row_values[0]],
                    ).fetchone()
                    if conflicting_membership is not None:
                        raise PersistenceConflictError(
                            "PIT row hash already has conflicting manifest membership"
                        )
                    connection.execute(
                        f"INSERT INTO pit_dataset_row VALUES "
                        f"({', '.join('?' for _ in row_values)})",
                        row_values,
                    )
                elif tuple(stored_row[:-1]) != row_values[:-1]:
                    raise PersistenceConflictError(
                        "PIT row membership contradicts immutable stored authority"
                    )

                for feature_values in expected_features[row_values[0]]:
                    stored_feature = connection.execute(
                        "SELECT * FROM pit_feature_snapshot "
                        "WHERE row_content_hash = ? AND feature_ordinal = ?",
                        [row_values[0], feature_values[1]],
                    ).fetchone()
                    if stored_feature is None:
                        connection.execute(
                            f"INSERT INTO pit_feature_snapshot VALUES "
                            f"({', '.join('?' for _ in feature_values)})",
                            feature_values,
                        )
                    elif tuple(stored_feature) != feature_values:
                        raise PersistenceConflictError(
                            "PIT feature key contradicts immutable shared row content"
                        )

            stored_rows = connection.execute(
                "SELECT * FROM pit_dataset_row WHERE dataset_identity = ? "
                "ORDER BY manifest_row_ordinal",
                [dataset_identity],
            ).fetchall()
            if len(stored_rows) != len(expected_rows) or any(
                tuple(stored[:-1]) != expected[:-1]
                for stored, expected in zip(stored_rows, expected_rows, strict=True)
            ):
                raise PersistenceConflictError(
                    "stored PIT manifest membership differs from authoritative rows"
                )
            for row_hash, feature_values in expected_features.items():
                stored_features = connection.execute(
                    "SELECT * FROM pit_feature_snapshot WHERE row_content_hash = ? "
                    "ORDER BY feature_ordinal",
                    [row_hash],
                ).fetchall()
                if [tuple(item) for item in stored_features] != feature_values:
                    raise PersistenceConflictError(
                        "stored PIT feature membership differs from immutable row content"
                    )
            connection.execute("COMMIT")
            return dataset_identity
        except duckdb.ConstraintException as exc:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise PersistenceConflictError("PIT persistence constraint conflict") from exc
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            connection.close()

    def load_pit_dataset(self, dataset_identity: str) -> PITDatasetResult:
        """Reconstruct and verify RD6 authority; runtime diagnostics intentionally return as ``()``."""
        _require_hash(dataset_identity, "dataset_identity")
        connection = self._connect()
        try:
            manifest_row = connection.execute(
                "SELECT * FROM pit_dataset_manifest WHERE dataset_identity = ?",
                [dataset_identity],
            ).fetchone()
            if manifest_row is None:
                raise KeyError(dataset_identity)
            row_records = connection.execute(
                "SELECT * FROM pit_dataset_row WHERE dataset_identity = ? "
                "ORDER BY manifest_row_ordinal",
                [dataset_identity],
            ).fetchall()
            feature_records = {
                row[0]: connection.execute(
                    "SELECT * FROM pit_feature_snapshot WHERE row_content_hash = ? "
                    "ORDER BY feature_ordinal",
                    [row[0]],
                ).fetchall()
                for row in row_records
            }
        finally:
            connection.close()

        try:
            if tuple(row[3] for row in row_records) != tuple(range(len(row_records))):
                raise PersistenceConflictError("stored PIT row ordinals are not contiguous")
            rows: list[PITDatasetRow] = []
            for row in row_records:
                stored_features = feature_records[row[0]]
                if tuple(item[1] for item in stored_features) != tuple(
                    range(len(stored_features))
                ):
                    raise PersistenceConflictError(
                        "stored PIT feature ordinals are not contiguous"
                    )
                features: list[PITFeatureSnapshot] = []
                for feature in stored_features:
                    evidence_refs = _stored_json(feature[15], list, "evidence_refs_json")
                    if any(type(item) is not dict for item in evidence_refs):
                        raise PersistenceConflictError(
                            "stored PIT evidence references must be JSON objects"
                        )
                    reconstructed = PITFeatureSnapshot(
                        feature_definition_id=feature[3],
                        feature_definition_version=feature[4],
                        feature_computation_profile_version=feature[5],
                        research_cutoff_at=feature[6],
                        value_state=feature[7],
                        value=parse_json_strict(feature[8]),
                        source_observation_id=feature[9],
                        source_observation_version_id=feature[10],
                        source_observed_at=feature[11],
                        source_available_at=feature[12],
                        source_profile_id=feature[13],
                        source_profile_version=feature[14],
                        evidence_refs=tuple(evidence_refs),
                        rd4_evaluation_hash=feature[16],
                        rd5_decision_hash=feature[17],
                        authority_binding_ref=feature[18],
                    )
                    if reconstructed.feature_content_hash != feature[2]:
                        raise PersistenceConflictError("loaded PIT feature content hash drifted")
                    features.append(reconstructed)

                list_fields = {
                    11: "rd4_authority_bindings_json",
                    12: "rd5_authority_bindings_json",
                    13: "rule_bundle_bindings_json",
                    14: "source_profile_bindings_json",
                }
                parsed_lists = {
                    index: _stored_json(row[index], list, name)
                    for index, name in list_fields.items()
                }
                if any(
                    any(type(item) is not dict for item in values)
                    for values in parsed_lists.values()
                ):
                    raise PersistenceConflictError(
                        "stored PIT authority bindings must be JSON objects"
                    )
                reconstructed_row = PITDatasetRow(
                    dataset_contract_version=manifest_row[3],
                    research_subject_id=row[4],
                    observation_version_id=row[5],
                    research_cutoff_at=row[6],
                    feature_set_version=row[7],
                    feature_computation_profile_version=row[8],
                    cutoff_policy_version=row[9],
                    features=tuple(features),
                    feature_available_at_max=row[10],
                    rd4_authority_bindings=tuple(parsed_lists[11]),
                    rd5_authority_bindings=tuple(parsed_lists[12]),
                    rule_bundle_bindings=tuple(parsed_lists[13]),
                    source_profile_bindings=tuple(parsed_lists[14]),
                    label_specification=_stored_json(
                        row[15], dict, "label_specification_json"
                    ),
                    authorization_snapshot=_stored_json(
                        row[16], dict, "row authorization_snapshot_json"
                    ),
                    operational_status=row[17],
                )
                if reconstructed_row.row_id != row[1]:
                    raise PersistenceConflictError("loaded PIT row identity hash drifted")
                if reconstructed_row.row_content_hash != row[0]:
                    raise PersistenceConflictError("loaded PIT row content hash drifted")
                rows.append(reconstructed_row)

            manifest = PITDatasetManifest(
                manifest_type=manifest_row[1],
                manifest_version=manifest_row[2],
                dataset_contract_version=manifest_row[3],
                feature_set_version=manifest_row[4],
                feature_computation_profile_version=manifest_row[5],
                cutoff_policy_version=manifest_row[6],
                rule_bundle_bindings=tuple(_stored_json(
                    manifest_row[7], list, "manifest rule_bundle_bindings_json"
                )),
                source_profile_bindings=tuple(_stored_json(
                    manifest_row[8], list, "manifest source_profile_bindings_json"
                )),
                authorization_snapshot=_stored_json(
                    manifest_row[9], dict, "manifest authorization_snapshot_json"
                ),
                request_scope=tuple(_stored_json(
                    manifest_row[10], list, "request_scope_json"
                )),
                ordered_row_ids=tuple(item.row_id for item in rows),
                ordered_row_content_hashes=tuple(item.row_content_hash for item in rows),
                include_count=manifest_row[11],
                exclude_count=manifest_row[12],
                quarantine_count=manifest_row[13],
                exclusion_reason_summary=_stored_json(
                    manifest_row[14], dict, "exclusion_reason_summary_json"
                ),
                quarantine_reason_summary=_stored_json(
                    manifest_row[15], dict, "quarantine_reason_summary_json"
                ),
            )
            if manifest_row[0] != dataset_identity or manifest.manifest_hash != dataset_identity:
                raise PersistenceConflictError("loaded PIT manifest identity hash drifted")
            return PITDatasetResult(tuple(rows), manifest, ())
        except PersistenceConflictError:
            raise
        except Exception as exc:
            raise PersistenceConflictError(
                "stored PIT dataset cannot reconstruct authoritative RD6 content"
            ) from exc
