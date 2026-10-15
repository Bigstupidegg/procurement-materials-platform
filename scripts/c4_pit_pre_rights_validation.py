"""Disposable C4-PIT pre-rights evidence persistence validation.

This module persists only clearly synthetic evidence and optional caller-supplied
read-only dataset copies.  It never performs network I/O, never opens an
authoritative database, and never enables publication, export, research, PIT,
backtest, prediction, Canonical, Deferred, or Production paths.

The C4-PIT-2B claim-first contracts remain authoritative for release, artifact,
claim, binding, temporal, revision, and materialization semantics.  This module
adds a disposable DuckDB representation and replay validator around those exact
objects; it does not introduce a competing identity or canonicalization system.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
from pathlib import Path
import re
import time
from typing import Any, Mapping, Sequence

import duckdb

from scripts.c4_pit_evidence_contract import (
    DAY_GRANULAR_POLICY_VERSION,
    EvidenceArtifact,
    EvidenceClaim,
    EvidenceSubjectBinding,
    HistoricalVintageClaim,
    MaterializationContract,
    SourceRelease,
    TemporalBound,
    materialize_observation_version_candidate,
    select_visible_vintage,
    validate_artifact_consistency,
    validate_binding_consistency,
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
    canonical_hash,
    canonical_json_bytes,
    canonical_timestamp,
    parse_json_strict,
    parse_rfc3339,
    raw_payload_hash,
)


SCHEMA_VERSION = "C4_PIT_PRE_RIGHTS_VALIDATION_DB_V1@1.1.0"
MIGRATION_ID = "C4_PIT_PRE_RIGHTS_VALIDATION_DB_V1_INITIAL"
MIGRATION_PATH = Path(__file__).resolve().parent / "sql" / "c4_pit_pre_rights_validation_v1.sql"
APPROVED_MIGRATION_CHECKSUM = "b7a93970b9363ffd7994d10048eb3449dabf607fb889bd144f71ced785baaa57"
VALIDATION_CONTRACT_VERSION = "C4_PIT_PRE_RIGHTS_VALIDATION_V1@1.1.0"
DATABASE_CLASSIFICATION = "C4_PIT_PRE_RIGHTS_VALIDATION_DB"
REPOSITORY_BASE_COMMIT_SHA = "a6e5f61f24f9b3f16398fc6c31f1033de424199d"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_DATABASE_ROOT = REPOSITORY_ROOT / "runtime" / "c4-pit-pre-rights-validation"
WORLD_BANK_SOURCE_PATH = REPOSITORY_ROOT / "data" / "world-bank.json"
WORLD_BANK_SOURCE_RELATIVE_PATH = "data/world-bank.json"
WORLD_BANK_SOURCE_SHA256 = "4ae3196f8a7083827675d09479b8377f0a96e2302f5d049ebd1fdf71761ad08b"
WORLD_BANK_SOURCE_CONTRACT = "WORLD_BANK_PINK_SHEET_COPPER_JSON@2026-08-04"
WORLD_BANK_COPPER_FIRST_PERIOD = "1960-01"
WORLD_BANK_COPPER_LAST_PERIOD = "2026-07"
WORLD_BANK_COPPER_RECORD_COUNT = 799
WORLD_BANK_COPPER_UNIT = "($/mt)"

SYNTHETIC_SOURCE_CHANNEL = "SYNTHETIC_WORLD_BANK_RELEASE_FIXTURE"
DATASET_SOURCE_CHANNEL = "WORLD_BANK_PINK_SHEET_XLSX_EXISTING_READ_ONLY"
PDF_SOURCE_CHANNEL = "WORLD_BANK_PINK_SHEET_RELEASE_PDF"
SYNTHETIC_RIGHTS_PROFILE = "SYNTHETIC_ALLOW_ALL_INTERNAL_TEST@1.0.0"
DATASET_RIGHTS_PROFILE = "XLSX_INTERNAL_ALLOWED_WITH_CONDITIONS_TEST@1.0.0"
PDF_RIGHTS_PROFILE = "PDF_REVIEW_REQUIRED_TEST@1.0.0"
SYSTEM_POLICY_ID = "C4_PRE_RIGHTS_FAIL_CLOSED_POLICY@1.0.0"
SYNTHETIC_RETENTION_POLICY = "SYNTHETIC_DISPOSABLE_RESTRICTED@1.0.0"

PUBLICATION_ALLOWED = False
PUBLIC_API_ALLOWED = False
EXPORT_ALLOWED = False
REDISTRIBUTION_ALLOWED = False

SYNTHETIC_TIMEZONE = "Asia/Taipei"
SYNTHETIC_TRANSFORM = "synthetic-world-bank-record@1.0.0"
SYNTHETIC_EXTRACTOR = "deterministic-synthetic-record-parser@1.0.0"
SYNTHETIC_RECORD_LOCATOR = {
    "page": "SYNTHETIC_PAGE_1",
    "table": "SYNTHETIC_TABLE_MONTHLY",
    "row": "SYNTHETIC_ROW_2024_02",
    "label": "SYNTHETIC_COPPER",
}
SYNTHETIC_PERIOD = {
    "source_period_type": "MONTH",
    "source_market_date": None,
    "source_period_start_date": "2024-02-01",
    "source_period_end_date": "2024-02-29",
}
SYNTHETIC_BINDING_STATES = (
    "SOURCE_RELEASE_BOUND",
    "OFFICIAL_ATTACHMENT_BOUND",
    "OFFICIAL_RECORD_VALUE_BOUND",
    "EXACT_ARTIFACT_HASH_BOUND",
)

_RUN_TRANSITIONS = {
    None: frozenset({"PLANNED"}),
    "PLANNED": frozenset({"BLOCKED_RIGHTS", "RUNNING", "FAILED", "INVALIDATED"}),
    "BLOCKED_RIGHTS": frozenset({"INVALIDATED"}),
    "RUNNING": frozenset({"PARTIAL", "COMPLETE", "FAILED", "INVALIDATED"}),
    "PARTIAL": frozenset({"COMPLETE", "FAILED", "INVALIDATED"}),
    "COMPLETE": frozenset({"INVALIDATED"}),
    "FAILED": frozenset({"INVALIDATED"}),
    "INVALIDATED": frozenset(),
}


def _trusted_promotion_checks() -> Mapping[str, str]:
    return {
        "rights": "PASS",
        "manifest": "PASS",
        "content_hash": "PASS",
        "contract": "PASS",
        "read_back": "PASS",
        "audit_association": "PASS",
    }


def _acquisition_event_timestamp(value: str) -> tuple[str, datetime]:
    """Return canonical storage text and the aware instant used for ordering."""
    canonical = canonical_timestamp(value)
    return canonical, parse_rfc3339(canonical)


class PreRightsValidationError(RuntimeError):
    """Base fail-closed validation error."""


class ValidationDatabasePathError(PreRightsValidationError):
    """The requested path is not clearly disposable validation storage."""


class ValidationMigrationError(PreRightsValidationError):
    """The validation migration is missing, altered, or conflicts."""


class ValidationConflictError(PreRightsValidationError):
    """An immutable semantic identity conflicts with stored content."""


class RightsGateBlocked(PreRightsValidationError):
    """The requested activity did not pass the explicit rights gate."""


class IntegrityValidationError(PreRightsValidationError):
    """Stored evidence failed deterministic replay or integrity checks."""


def _canonicalize_sql(raw: bytes) -> bytes:
    value = raw.replace(b"\r\n", b"\n")
    if b"\r" in value:
        raise ValidationMigrationError("migration SQL contains unsupported bare CR bytes")
    return value


def migration_sql_bytes() -> bytes:
    value = _canonicalize_sql(MIGRATION_PATH.read_bytes())
    if hashlib.sha256(value).hexdigest() != APPROVED_MIGRATION_CHECKSUM:
        raise ValidationMigrationError("validation migration checksum mismatch")
    return value


def migration_checksum() -> str:
    return hashlib.sha256(migration_sql_bytes()).hexdigest()


def _json(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _parsed(value: str) -> Any:
    return parse_json_strict(value)


def _validation_hash(kind: str, projection: Mapping[str, Any]) -> str:
    return canonical_hash(
        "MANIFEST_CONTENT",
        {
            "validation_contract_version": VALIDATION_CONTRACT_VERSION,
            "validation_kind": kind,
            "projection": projection,
        },
    )


def _schema_descriptor(connection: duckdb.DuckDBPyConnection) -> Mapping[str, Any]:
    columns = connection.execute(
        "SELECT table_name, ordinal_position, column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema='main' "
        "ORDER BY table_name, ordinal_position"
    ).fetchall()
    constraints = connection.execute(
        "SELECT table_name, constraint_index, constraint_type, constraint_text, expression, "
        "constraint_column_names, referenced_table, referenced_column_names "
        "FROM duckdb_constraints() WHERE schema_name='main' "
        "ORDER BY table_name, constraint_index"
    ).fetchall()
    return {
        "tables": tuple(
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='main' ORDER BY table_name"
            ).fetchall()
        ),
        "columns": tuple(
            {
                "table": row[0],
                "ordinal": int(row[1]),
                "name": row[2],
                "type": row[3],
                "nullable": row[4],
                "default": row[5],
            }
            for row in columns
        ),
        "constraints": tuple(
            {
                "table": row[0],
                "ordinal": int(row[1]),
                "type": row[2],
                "text": row[3],
                "expression": row[4],
                "columns": tuple(row[5] or ()),
                "referenced_table": row[6],
                "referenced_columns": tuple(row[7] or ()),
            }
            for row in constraints
        ),
    }


def expected_schema_descriptor() -> Mapping[str, Any]:
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(migration_sql_bytes().decode("utf-8"))
        return _schema_descriptor(connection)
    finally:
        connection.close()


def expected_schema_fingerprint() -> str:
    return _validation_hash("SCHEMA_CATALOG", expected_schema_descriptor())


def _month_sequence(first: str, last: str) -> tuple[str, ...]:
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", first) or not re.fullmatch(
        r"[0-9]{4}-(0[1-9]|1[0-2])", last
    ):
        raise ValidationConflictError("month range is malformed")
    year, month = (int(item) for item in first.split("-"))
    end_year, end_month = (int(item) for item in last.split("-"))
    values: list[str] = []
    while (year, month) <= (end_year, end_month):
        values.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return tuple(values)


def _temporal_id(bound: TemporalBound) -> str:
    return _validation_hash("TEMPORAL_BOUND", bound.projection())


def _temporal_from_projection(value: Mapping[str, Any]) -> TemporalBound:
    original = value["original_value"]
    if isinstance(original, list):
        original = tuple(original)
    return TemporalBound(
        value["bound_type"],
        value["precision"],
        original,
        source_timezone=value["source_timezone"],
    )


@dataclass(frozen=True, slots=True)
class RightsGateDecision:
    activity: str
    decision: str
    rights_profile_id: str | None
    source_channel: str
    reason: str

    @property
    def passed(self) -> bool:
        return self.decision == "PASS"


@dataclass(frozen=True, slots=True)
class SyntheticValidationCase:
    observation: Observation
    releases: tuple[SourceRelease, SourceRelease]
    artifacts: tuple[EvidenceArtifact, EvidenceArtifact]
    payloads: tuple[bytes, bytes]
    claims: tuple[EvidenceClaim, EvidenceClaim]
    bindings: tuple[EvidenceSubjectBinding, EvidenceSubjectBinding]
    vintages: tuple[HistoricalVintageClaim, HistoricalVintageClaim]


@dataclass(frozen=True, slots=True)
class SyntheticE2EResult:
    acquisition_run_id: str
    manifest_id: str
    manifest_hash: str
    audit_reference_id: str
    trusted_collection_id: str
    selected_vintage_claim_id: str
    candidate_hash: str
    artifact_ids: tuple[str, str]
    claim_ids: tuple[str, str]
    binding_ids: tuple[str, str]
    vintage_ids: tuple[str, str]


@dataclass(frozen=True, slots=True)
class ReplayResult:
    content_hashes_valid: bool
    manifest_hashes_valid: bool
    lineage_valid: bool
    rights_valid: bool
    trusted_membership_valid: bool
    selected_vintage_claim_id: str | None


def acquisition_manifest_identity(
    *,
    acquisition_run_id: str,
    rights_profile_id: str,
    rights_profile_version: str,
    rights_assessment_id: str,
    artifacts: Sequence[EvidenceArtifact],
) -> tuple[str, Mapping[str, Any]]:
    """Return deterministic exact-membership manifest identity and projection."""
    values = tuple(artifacts)
    validate_artifact_consistency(values)
    members = tuple(
        {
            "artifact_id": artifact.artifact_id,
            "artifact_content_hash": artifact.content_hash,
            "payload_hash": artifact.payload_hash,
        }
        for artifact in sorted(values, key=lambda item: item.artifact_id)
    )
    projection = {
        "acquisition_run_id": acquisition_run_id,
        "rights_profile_id": rights_profile_id,
        "rights_profile_version": rights_profile_version,
        "rights_assessment_id": rights_assessment_id,
        "artifact_members": members,
    }
    return _validation_hash("ACQUISITION_MANIFEST", projection), projection


def build_synthetic_2024_02_case() -> SyntheticValidationCase:
    assignments = tuple(
        CalendarAssignment("copper", role, "NOT_APPLICABLE")
        for role in ("MARKET", "PUBLICATION", "LOCAL_OPERATIONAL", "SCHEDULER")
    )
    observation = Observation(
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

    releases: list[SourceRelease] = []
    artifacts: list[EvidenceArtifact] = []
    payloads: list[bytes] = []
    claims: list[EvidenceClaim] = []
    bindings: list[EvidenceSubjectBinding] = []
    vintages: list[HistoricalVintageClaim] = []
    parent: str | None = None
    for label, release_month, value, available_date in (
        ("MARCH", "2024_03", "8300", "2024-03-06"),
        ("APRIL", "2024_04", "8305", "2024-04-08"),
    ):
        release = SourceRelease(
            source_id="WORLD_BANK",
            data_acquisition_channel="WORLD_BANK_PINK_SHEET_XLSX",
            historical_evidence_channel="WORLD_BANK_PINK_SHEET_RELEASE_PDF",
            release_family="SYNTHETIC_PINK_SHEET_MONTHLY_RELEASE",
            official_release_id=f"SYNTHETIC_RELEASE_{release_month}",
            release_label=f"Synthetic {release_month} release",
        )
        payload = f"CLEARLY ARTIFICIAL PDF-LIKE PAYLOAD {release_month}".encode("ascii")
        artifact = EvidenceArtifact.from_payload(
            source_release_id=release.source_release_id,
            artifact_role="SYNTHETIC_RELEASE_EVIDENCE",
            media_type="application/pdf",
            official_locator=f"synthetic://world-bank-release/{release_month}",
            payload=payload,
            retrieved_at="2026-10-08T00:00:00Z",
            source_document_id=f"SYNTHETIC_DOCUMENT_{release_month}",
        )
        bound = TemporalBound(
            "BY_DATE", "DATE_ONLY", available_date, source_timezone=SYNTHETIC_TIMEZONE
        )
        claim = EvidenceClaim(
            source_release_id=release.source_release_id,
            artifact_id=artifact.artifact_id,
            claim_type="RECORD_VALUE",
            subject_scope="RECORD_BOUND",
            claimed_period=SYNTHETIC_PERIOD,
            claimed_value=Decimal(value),
            unit="USD/MT",
            currency="USD",
            temporal_bound=bound,
            evidence_authority="OFFICIAL_RECORD",
            safe_locator=SYNTHETIC_RECORD_LOCATOR,
            transformation_version=SYNTHETIC_TRANSFORM,
        )
        binding = EvidenceSubjectBinding(
            claim_id=claim.claim_id,
            subject_scope="RECORD_BOUND",
            binding_states=SYNTHETIC_BINDING_STATES,
            source_release_id=release.source_release_id,
            artifact_id=artifact.artifact_id,
            expected_artifact_hash=artifact.payload_hash,
            record_locator=SYNTHETIC_RECORD_LOCATOR,
            source_id="WORLD_BANK",
            instrument_id="copper_world_bank_monthly",
            metric_id="MONTHLY_PRICE",
            source_record_identifier="WORLD_BANK:PINK_SHEET:COPPER:2024-02",
        )
        vintage = HistoricalVintageClaim(
            observation_id=observation.observation_id,
            source_id="WORLD_BANK",
            instrument_id="copper_world_bank_monthly",
            metric_id="MONTHLY_PRICE",
            source_record_identifier="WORLD_BANK:PINK_SHEET:COPPER:2024-02",
            source_period=SYNTHETIC_PERIOD,
            source_release_id=release.source_release_id,
            release_record_key=f"SYNTHETIC_{label}_COPPER_2024_02",
            semantic_value=Decimal(value),
            unit="USD/MT",
            currency="USD",
            record_locator=SYNTHETIC_RECORD_LOCATOR,
            evidence_claim_ids=(claim.claim_id,),
            evidence_claim_content_hashes=(claim.content_hash,),
            binding_ids=(binding.binding_id,),
            binding_content_hashes=(binding.content_hash,),
            temporal_bound=bound,
            evidence_authority="OFFICIAL_RECORD",
            transformation_version=SYNTHETIC_TRANSFORM,
            parent_vintage_claim_id=parent,
        )
        parent = vintage.vintage_claim_id
        releases.append(release)
        artifacts.append(artifact)
        payloads.append(payload)
        claims.append(claim)
        bindings.append(binding)
        vintages.append(vintage)

    validate_artifact_consistency(artifacts)
    validate_binding_consistency(bindings)
    validate_revision_lineage(vintages)
    for release, artifact, claim, binding in zip(
        releases, artifacts, claims, bindings, strict=True
    ):
        validate_subject_binding(binding, claim=claim, release=release, artifact=artifact)
    return SyntheticValidationCase(
        observation,
        tuple(releases),
        tuple(artifacts),
        tuple(payloads),
        tuple(claims),
        tuple(bindings),
        tuple(vintages),
    )


class PreRightsValidationDatabase:
    """Adapter for one explicitly disposable validation database file."""

    def __init__(self, database_path: str | Path) -> None:
        if str(database_path) in {"", ":memory:"}:
            raise ValidationDatabasePathError("a persistent disposable database path is required")
        supplied = Path(database_path).expanduser()
        if supplied.is_symlink():
            raise ValidationDatabasePathError("validation database path cannot be a symlink")
        requested = supplied.resolve(strict=False)
        validation_root = VALIDATION_DATABASE_ROOT.resolve(strict=False)
        if requested == validation_root or not requested.is_relative_to(validation_root):
            raise ValidationDatabasePathError(
                f"validation database must be beneath {VALIDATION_DATABASE_ROOT}"
            )
        if requested.suffix.lower() != ".duckdb":
            raise ValidationDatabasePathError("validation database must use the .duckdb suffix")
        reserved = {"production", "authoritative", "private_research", "canonical", "db2"}
        if requested.stem.lower() in reserved:
            raise ValidationDatabasePathError(
                "reserved authoritative/private database names are forbidden even inside validation root"
            )
        if requested.exists() and requested.is_dir():
            raise ValidationDatabasePathError("validation database path must be a file")
        if not requested.parent.exists():
            raise ValidationDatabasePathError("validation database parent must already exist")
        self.database_path = requested

    def _connect(self, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.database_path), read_only=read_only)

    def initialize_schema(self, *, applied_at: str, code_commit_sha: str) -> str:
        if (
            not isinstance(code_commit_sha, str)
            or not re.fullmatch(r"[0-9a-f]{40}", code_commit_sha)
            or code_commit_sha != REPOSITORY_BASE_COMMIT_SHA
        ):
            raise ValidationMigrationError(
                "code_commit_sha must equal the authorized repository base commit"
            )
        try:
            applied = canonical_timestamp(applied_at)
        except (TypeError, ValueError) as exc:
            raise ValidationMigrationError("applied_at must be a valid RFC3339 timestamp") from exc
        sql = migration_sql_bytes().decode("utf-8")
        checksum = migration_checksum()
        fingerprint = expected_schema_fingerprint()
        existed_non_empty = self.database_path.exists() and self.database_path.stat().st_size > 0
        if self.database_path.exists() and not existed_non_empty:
            self.database_path.unlink()
        if existed_non_empty:
            try:
                connection = self._connect(read_only=True)
            except Exception as exc:
                raise ValidationMigrationError(
                    "existing file is not a valid Pre-Rights validation database"
                ) from exc
            try:
                self._verify_schema(connection, checksum=checksum, fingerprint=fingerprint)
            finally:
                connection.close()
            return checksum

        connection = self._connect()
        try:
            connection.execute("BEGIN TRANSACTION")
            connection.execute(sql)
            actual = _schema_descriptor(connection)
            if actual != expected_schema_descriptor():
                raise ValidationMigrationError("fresh validation schema does not match approved catalog")
            connection.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    MIGRATION_ID,
                    SCHEMA_VERSION,
                    DATABASE_CLASSIFICATION,
                    VALIDATION_CONTRACT_VERSION,
                    checksum,
                    fingerprint,
                    applied,
                    applied,
                    code_commit_sha,
                ],
            )
            self._seed_controls(connection)
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            connection.close()
        connection = self._connect(read_only=True)
        try:
            self._verify_schema(connection, checksum=checksum, fingerprint=fingerprint)
        finally:
            connection.close()
        return checksum

    @staticmethod
    def _verify_schema(
        connection: duckdb.DuckDBPyConnection,
        *,
        checksum: str,
        fingerprint: str,
    ) -> None:
        try:
            actual_descriptor = _schema_descriptor(connection)
        except Exception as exc:
            raise ValidationMigrationError("validation schema catalog cannot be inspected") from exc
        if actual_descriptor != expected_schema_descriptor():
            raise ValidationMigrationError(
                "existing validation schema differs from the approved catalog; recreate required"
            )
        actual_fingerprint = _validation_hash("SCHEMA_CATALOG", actual_descriptor)
        if actual_fingerprint != fingerprint:
            raise ValidationMigrationError("validation schema fingerprint mismatch")
        try:
            rows = connection.execute(
                "SELECT migration_id, schema_version, database_purpose, schema_contract, "
                "migration_checksum, schema_fingerprint, created_at, applied_at, "
                "code_commit_sha FROM schema_migrations"
            ).fetchall()
        except Exception as exc:
            raise ValidationMigrationError("validation database sentinel is absent") from exc
        expected = (
            MIGRATION_ID,
            SCHEMA_VERSION,
            DATABASE_CLASSIFICATION,
            VALIDATION_CONTRACT_VERSION,
            checksum,
            fingerprint,
        )
        if len(rows) != 1 or tuple(rows[0][:6]) != expected:
            raise ValidationMigrationError(
                "validation database sentinel or migration identity is obsolete or invalid"
            )
        created_at, applied_at, code_commit_sha = rows[0][6:9]
        try:
            canonical_created = canonical_timestamp(created_at)
            canonical_applied = canonical_timestamp(applied_at)
        except (TypeError, ValueError) as exc:
            raise ValidationMigrationError(
                "validation database sentinel timestamps are malformed"
            ) from exc
        if created_at != canonical_created or applied_at != canonical_applied:
            raise ValidationMigrationError(
                "validation database sentinel timestamps are not canonical UTC"
            )
        if parse_rfc3339(canonical_applied) < parse_rfc3339(canonical_created):
            raise ValidationMigrationError(
                "validation database sentinel applied_at precedes created_at"
            )
        if (
            not isinstance(code_commit_sha, str)
            or not re.fullmatch(r"[0-9a-f]{40}", code_commit_sha)
            or code_commit_sha != REPOSITORY_BASE_COMMIT_SHA
        ):
            raise ValidationMigrationError(
                "validation database sentinel repository base provenance mismatch"
            )

    @staticmethod
    def _seed_controls(connection: duckdb.DuckDBPyConnection) -> None:
        system_projection = {
            "policy_id": SYSTEM_POLICY_ID,
            "publication_allowed": False,
            "public_api_allowed": False,
            "export_allowed": False,
            "redistribution_allowed": False,
            "classification": DATABASE_CLASSIFICATION,
        }
        connection.execute(
            "INSERT INTO system_policy VALUES (?, FALSE, FALSE, FALSE, FALSE, ?)",
            [SYSTEM_POLICY_ID, _json(system_projection)],
        )
        profiles = (
            (
                SYNTHETIC_RIGHTS_PROFILE,
                "1.0.0",
                SYNTHETIC_SOURCE_CHANNEL,
                "SYNTHETIC_TEST_ONLY",
                True,
                True,
                True,
                False,
                False,
            ),
            (
                DATASET_RIGHTS_PROFILE,
                "1.0.0",
                DATASET_SOURCE_CHANNEL,
                "ALLOWED_WITH_CONDITIONS",
                False,
                True,
                True,
                False,
                False,
            ),
            (
                PDF_RIGHTS_PROFILE,
                "1.0.0",
                PDF_SOURCE_CHANNEL,
                "REVIEW_REQUIRED",
                False,
                False,
                False,
                False,
                False,
            ),
        )
        for profile in profiles:
            projection = {
                "rights_profile_id": profile[0],
                "profile_version": profile[1],
                "source_channel": profile[2],
                "rights_status": profile[3],
                "automated_retrieval_allowed": profile[4],
                "materialization_allowed": profile[5],
                "internal_analysis_allowed": profile[6],
                "public_use_allowed": profile[7],
                "export_allowed": profile[8],
            }
            connection.execute(
                "INSERT INTO rights_profile VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [*profile, _json(projection)],
            )
        allowlists = (
            (SYNTHETIC_SOURCE_CHANNEL, "ENABLED_SYNTHETIC_ONLY", SYNTHETIC_RIGHTS_PROFILE),
            (DATASET_SOURCE_CHANNEL, "ENABLED_DATASET_READ_ONLY", DATASET_RIGHTS_PROFILE),
            (PDF_SOURCE_CHANNEL, "DISABLED", PDF_RIGHTS_PROFILE),
        )
        for source_channel, status, profile_id in allowlists:
            projection = {
                "source_channel": source_channel,
                "activity_status": status,
                "rights_profile_id": profile_id,
                "profile_version": "1.0.0",
            }
            connection.execute(
                "INSERT INTO source_allowlist VALUES (?, ?, ?, ?, ?)",
                [source_channel, status, profile_id, "1.0.0", _json(projection)],
            )
        retention = {
            "retention_policy_id": SYNTHETIC_RETENTION_POLICY,
            "policy_version": "1.0.0",
            "storage_class": "RESTRICTED_EVIDENCE_STORE",
            "disposition": "DISPOSABLE_TEST_ONLY",
            "synthetic_only": True,
        }
        connection.execute(
            "INSERT INTO retention_policy VALUES (?, ?, ?, ?, ?)",
            [
                SYNTHETIC_RETENTION_POLICY,
                "1.0.0",
                "RESTRICTED_EVIDENCE_STORE",
                "DISPOSABLE_TEST_ONLY",
                _json(retention),
            ],
        )

    def table_names(self) -> tuple[str, ...]:
        connection = self._connect(read_only=True)
        try:
            rows = connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY table_name"
            ).fetchall()
            return tuple(row[0] for row in rows)
        finally:
            connection.close()

    @staticmethod
    def _persist_temporal_bound_row(
        connection: duckdb.DuckDBPyConnection,
        bound: TemporalBound,
    ) -> str:
        if type(bound) is not TemporalBound:
            raise ValidationConflictError("temporal bound must use the exact PIT-2B type")
        temporal_id = _temporal_id(bound)
        PreRightsValidationDatabase._insert_or_verify(
            connection,
            "temporal_bound",
            "temporal_bound_id",
            (
                temporal_id,
                bound.bound_type,
                bound.precision,
                bound.normalized_start,
                bound.normalized_end,
                bound.source_timezone,
                _json(bound.projection()),
            ),
        )
        return temporal_id

    def persist_temporal_bound(self, bound: TemporalBound) -> str:
        connection = self._connect()
        try:
            return self._persist_temporal_bound_row(connection, bound)
        finally:
            connection.close()

    def load_temporal_bound(self, temporal_bound_id: str) -> TemporalBound:
        connection = self._connect(read_only=True)
        try:
            row = connection.execute(
                "SELECT bound_json FROM temporal_bound WHERE temporal_bound_id=?",
                [temporal_bound_id],
            ).fetchone()
            if row is None:
                raise IntegrityValidationError("temporal bound is absent")
            bound = _temporal_from_projection(_parsed(row[0]))
            if _temporal_id(bound) != temporal_bound_id:
                raise IntegrityValidationError("temporal bound identity mismatch")
            return bound
        finally:
            connection.close()

    def evaluate_rights(
        self,
        *,
        source_channel: str,
        rights_profile_id: str | None,
        activity: str,
    ) -> RightsGateDecision:
        connection = self._connect(read_only=True)
        try:
            return self._evaluate_rights_connection(
                connection,
                source_channel=source_channel,
                rights_profile_id=rights_profile_id,
                activity=activity,
            )
        finally:
            connection.close()

    @staticmethod
    def _evaluate_rights_connection(
        connection: duckdb.DuckDBPyConnection,
        *,
        source_channel: str,
        rights_profile_id: str | None,
        activity: str,
    ) -> RightsGateDecision:
        policy = connection.execute(
            "SELECT publication_allowed, public_api_allowed, export_allowed, redistribution_allowed "
            "FROM system_policy WHERE policy_id=?",
            [SYSTEM_POLICY_ID],
        ).fetchone()
        if policy != (False, False, False, False):
            return RightsGateDecision(
                activity,
                "BLOCKED_PUBLIC_POLICY",
                rights_profile_id,
                source_channel,
                "SYSTEM_POLICY_NOT_FAIL_CLOSED",
            )
        if rights_profile_id is None:
            return RightsGateDecision(
                activity, "BLOCKED_DISABLED", None, source_channel, "MISSING_RIGHTS_PROFILE"
            )
        rows = connection.execute(
            "SELECT rp.profile_version, rp.rights_status, rp.automated_retrieval_allowed, "
            "rp.materialization_allowed, rp.internal_analysis_allowed, sa.activity_status "
            "FROM rights_profile rp JOIN source_allowlist sa "
            "ON sa.source_channel=rp.source_channel "
            "AND sa.rights_profile_id=rp.rights_profile_id "
            "AND sa.profile_version=rp.profile_version "
            "WHERE rp.rights_profile_id=? AND rp.source_channel=?",
            [rights_profile_id, source_channel],
        ).fetchall()
        if len(rows) != 1:
            return RightsGateDecision(
                activity,
                "BLOCKED_DISABLED",
                rights_profile_id,
                source_channel,
                "PROFILE_OR_ALLOWLIST_MISMATCH",
            )
        _, status, retrieve, materialize, analyze, allowlist_status = rows[0]
        if activity in {"PUBLICATION", "EXPORT"}:
            return RightsGateDecision(
                activity,
                "BLOCKED_PUBLIC_POLICY",
                rights_profile_id,
                source_channel,
                "SYSTEM_PUBLIC_EXPORT_DISABLED",
            )
        if status == "REVIEW_REQUIRED":
            return RightsGateDecision(
                activity,
                "BLOCKED_REVIEW_REQUIRED",
                rights_profile_id,
                source_channel,
                "RIGHTS_REVIEW_REQUIRED",
            )
        if status == "DISABLED" or allowlist_status == "DISABLED":
            return RightsGateDecision(
                activity,
                "BLOCKED_DISABLED",
                rights_profile_id,
                source_channel,
                "SOURCE_ACTIVITY_DISABLED",
            )
        allowed = {
            "SYNTHETIC_MATERIALIZATION": bool(materialize)
            and allowlist_status == "ENABLED_SYNTHETIC_ONLY",
            "DATASET_READ_ONLY_IMPORT": bool(materialize and analyze)
            and allowlist_status == "ENABLED_DATASET_READ_ONLY",
            "REAL_PDF_RETRIEVAL": bool(retrieve),
        }.get(activity, False)
        if not allowed:
            return RightsGateDecision(
                activity,
                "BLOCKED_DISABLED",
                rights_profile_id,
                source_channel,
                "ACTIVITY_NOT_ALLOWED",
            )
        return RightsGateDecision(
            activity,
            "PASS",
            rights_profile_id,
            source_channel,
            "EXPLICIT_PROFILE_AND_ALLOWLIST_PASS",
        )

    def record_rights_assessment(
        self,
        *,
        source_channel: str | None = None,
        rights_profile_id: str | None = None,
        activity: str | None = None,
        assessed_at: str,
        decision: RightsGateDecision | None = None,
    ) -> str:
        if decision is not None:
            raise RightsGateBlocked("caller-supplied rights decisions are not accepted")
        if source_channel is None or rights_profile_id is None or activity is None:
            raise RightsGateBlocked("missing rights profile assessments fail before persistence")
        timestamp = canonical_timestamp(assessed_at)
        connection = self._connect()
        try:
            derived = self._evaluate_rights_connection(
                connection,
                source_channel=source_channel,
                rights_profile_id=rights_profile_id,
                activity=activity,
            )
            profile_row = connection.execute(
                "SELECT profile_version FROM rights_profile "
                "WHERE rights_profile_id=? AND source_channel=?",
                [rights_profile_id, source_channel],
            ).fetchall()
            if len(profile_row) != 1:
                raise RightsGateBlocked("rights profile/channel is not an exact persisted profile")
            profile_version = profile_row[0][0]
            projection = {
                "rights_profile_id": rights_profile_id,
                "profile_version": profile_version,
                "source_channel": source_channel,
                "assessed_activity": activity,
                "decision": derived.decision,
                "assessed_at": timestamp,
                "reason": derived.reason,
            }
            assessment_id = _validation_hash("RIGHTS_ASSESSMENT", projection)
            self._insert_or_verify(
                connection,
                "rights_assessment",
                "rights_assessment_id",
                (
                    assessment_id,
                    rights_profile_id,
                    profile_version,
                    source_channel,
                    activity,
                    derived.decision,
                    timestamp,
                    _json(projection),
                ),
            )
        finally:
            connection.close()
        return assessment_id

    @staticmethod
    def _rights_assessment_row(
        connection: duckdb.DuckDBPyConnection, rights_assessment_id: str
    ) -> tuple[Any, ...]:
        row = connection.execute(
            "SELECT rights_assessment_id, rights_profile_id, profile_version, source_channel, "
            "assessed_activity, decision, assessed_at, assessment_json "
            "FROM rights_assessment WHERE rights_assessment_id=?",
            [rights_assessment_id],
        ).fetchone()
        if row is None:
            raise RightsGateBlocked("persisted rights assessment is absent")
        projection = _parsed(row[7])
        if _validation_hash("RIGHTS_ASSESSMENT", projection) != row[0] or projection != {
            "rights_profile_id": row[1],
            "profile_version": row[2],
            "source_channel": row[3],
            "assessed_activity": row[4],
            "decision": row[5],
            "assessed_at": row[6],
            "reason": projection.get("reason"),
        }:
            raise IntegrityValidationError("rights assessment identity or row projection mismatch")
        return row

    @classmethod
    def _current_rights_valid(
        cls,
        connection: duckdb.DuckDBPyConnection,
        *,
        rights_profile_id: str,
        source_channel: str,
        activity: str,
    ) -> bool:
        current = connection.execute(
            "SELECT rights_assessment_id, decision FROM rights_assessment "
            "WHERE rights_profile_id=? AND source_channel=? AND assessed_activity=? "
            "ORDER BY assessed_at DESC, rights_assessment_id DESC LIMIT 2",
            [rights_profile_id, source_channel, activity],
        ).fetchall()
        if not current:
            return False
        if len(current) > 1:
            timestamps = connection.execute(
                "SELECT assessed_at FROM rights_assessment WHERE rights_assessment_id IN (?, ?)",
                [current[0][0], current[1][0]],
            ).fetchall()
            if len(timestamps) == 2 and timestamps[0][0] == timestamps[1][0]:
                return False
        derived = cls._evaluate_rights_connection(
            connection,
            source_channel=source_channel,
            rights_profile_id=rights_profile_id,
            activity=activity,
        )
        return current[0][1] == "PASS" and derived.passed

    @staticmethod
    def _insert_or_verify(
        connection: duckdb.DuckDBPyConnection,
        table: str,
        key_column: str,
        values: tuple[Any, ...],
    ) -> None:
        columns = tuple(
            row[1]
            for row in connection.execute(f"PRAGMA table_info('{table}')").fetchall()
        )
        if len(columns) != len(values):
            raise ValidationConflictError(f"{table} row does not match schema")
        key_value = values[columns.index(key_column)]
        stored = connection.execute(
            f"SELECT {', '.join(columns)} FROM {table} WHERE {key_column}=?", [key_value]
        ).fetchone()
        if stored is None:
            connection.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                values,
            )
        elif tuple(stored) != values:
            raise ValidationConflictError(f"{table} semantic identity conflicts with stored content")

    def create_acquisition_run(
        self,
        *,
        run_key: str,
        planned_at: str,
        source_channel: str,
        rights_profile_id: str,
        rights_assessment_id: str,
        authorized_activity: str,
        run_kind: str = "SYNTHETIC_VALIDATION",
    ) -> str:
        timestamp = canonical_timestamp(planned_at)
        connection = self._connect()
        try:
            assessment = self._rights_assessment_row(connection, rights_assessment_id)
            if (
                assessment[1] != rights_profile_id
                or assessment[3] != source_channel
                or assessment[4] != authorized_activity
                or assessment[5] != "PASS"
                or not self._current_rights_valid(
                    connection,
                    rights_profile_id=rights_profile_id,
                    source_channel=source_channel,
                    activity=authorized_activity,
                )
            ):
                raise RightsGateBlocked("acquisition run rights authorization is not current and exact")
            profile_version = assessment[2]
            projection = {
                "run_key": run_key,
                "run_kind": run_kind,
                "source_channel": source_channel,
                "rights_profile_id": rights_profile_id,
                "rights_profile_version": profile_version,
                "rights_assessment_id": rights_assessment_id,
                "authorized_activity": authorized_activity,
                "retention_policy_id": SYNTHETIC_RETENTION_POLICY,
                "planned_at": timestamp,
            }
            run_id = _validation_hash("ACQUISITION_RUN", projection)
            connection.execute("BEGIN TRANSACTION")
            self._insert_or_verify(
                connection,
                "acquisition_run",
                "acquisition_run_id",
                (
                    run_id,
                    run_kind,
                    source_channel,
                    rights_profile_id,
                    profile_version,
                    rights_assessment_id,
                    authorized_activity,
                    SYNTHETIC_RETENTION_POLICY,
                    timestamp,
                    _json(projection),
                ),
            )
            count = connection.execute(
                "SELECT COUNT(*) FROM acquisition_run_event WHERE acquisition_run_id=?", [run_id]
            ).fetchone()[0]
            if count == 0:
                event = {"run_state": "PLANNED", "occurred_at": timestamp, "reason": "RUN_CREATED"}
                connection.execute(
                    "INSERT INTO acquisition_run_event VALUES (?, 0, 'PLANNED', ?, ?)",
                    [run_id, timestamp, _json(event)],
                )
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            connection.close()
        return run_id

    @staticmethod
    def _run_state_from_history(
        connection: duckdb.DuckDBPyConnection, acquisition_run_id: str
    ) -> str:
        rows = connection.execute(
            "SELECT event_ordinal, run_state, occurred_at, event_json "
            "FROM acquisition_run_event WHERE acquisition_run_id=? ORDER BY event_ordinal",
            [acquisition_run_id],
        ).fetchall()
        if not rows:
            raise IntegrityValidationError("acquisition run has no state history")
        current: str | None = None
        previous_instant: datetime | None = None
        for expected_ordinal, row in enumerate(rows):
            ordinal, state, occurred_at, raw = row
            if int(ordinal) != expected_ordinal:
                raise IntegrityValidationError("acquisition run event ordinals are not contiguous")
            if state not in _RUN_TRANSITIONS.get(current, frozenset()):
                raise IntegrityValidationError(
                    f"illegal persisted acquisition transition {current!r} -> {state!r}"
                )
            canonical_at, current_instant = _acquisition_event_timestamp(occurred_at)
            if canonical_at != occurred_at or (
                previous_instant is not None and current_instant < previous_instant
            ):
                raise IntegrityValidationError("acquisition event timestamps are not canonical/ordered")
            projection = _parsed(raw)
            if projection != {
                "run_state": state,
                "occurred_at": occurred_at,
                "reason": projection.get("reason"),
            }:
                raise IntegrityValidationError("acquisition event row/projection mismatch")
            current = state
            previous_instant = current_instant
        assert current is not None
        return current

    @classmethod
    def _append_run_event(
        cls,
        connection: duckdb.DuckDBPyConnection,
        acquisition_run_id: str,
        new_state: str,
        *,
        occurred_at: str,
        reason: str,
    ) -> int:
        current = cls._run_state_from_history(connection, acquisition_run_id)
        rows = connection.execute(
            "SELECT event_ordinal, occurred_at FROM acquisition_run_event "
            "WHERE acquisition_run_id=? ORDER BY event_ordinal DESC LIMIT 1",
            [acquisition_run_id],
        ).fetchone()
        assert rows is not None
        if current == "INVALIDATED" and new_state == "INVALIDATED":
            return int(rows[0])
        if new_state not in _RUN_TRANSITIONS.get(current, frozenset()):
            raise ValidationConflictError(
                f"illegal acquisition transition {current!r} -> {new_state!r}"
            )
        timestamp, timestamp_instant = _acquisition_event_timestamp(occurred_at)
        _, previous_instant = _acquisition_event_timestamp(rows[1])
        if timestamp_instant < previous_instant:
            raise ValidationConflictError("acquisition transition timestamp is retrograde")
        ordinal = int(rows[0]) + 1
        event = {"run_state": new_state, "occurred_at": timestamp, "reason": reason}
        connection.execute(
            "INSERT INTO acquisition_run_event VALUES (?, ?, ?, ?, ?)",
            [acquisition_run_id, ordinal, new_state, timestamp, _json(event)],
        )
        return ordinal

    def transition_run(
        self,
        acquisition_run_id: str,
        new_state: str,
        *,
        occurred_at: str,
        reason: str,
    ) -> int:
        if new_state == "COMPLETE":
            raise ValidationConflictError(
                "COMPLETE requires validated run-specific completion proof"
            )
        connection = self._connect()
        try:
            connection.execute("BEGIN TRANSACTION")
            ordinal = self._append_run_event(
                connection,
                acquisition_run_id,
                new_state,
                occurred_at=occurred_at,
                reason=reason,
            )
            connection.execute("COMMIT")
            return ordinal
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            connection.close()

    def latest_run_state(self, acquisition_run_id: str) -> str:
        connection = self._connect(read_only=True)
        try:
            return self._run_state_from_history(connection, acquisition_run_id)
        finally:
            connection.close()

    def persist_synthetic_e2e(
        self,
        case: SyntheticValidationCase,
        *,
        run_key: str = "C4-PIT-PRE-RIGHTS-SYNTHETIC-2024-02",
        planned_at: str = "2026-10-08T01:00:00Z",
        assessed_at: str = "2026-10-08T01:00:01Z",
        failure_at: str | None = None,
    ) -> SyntheticE2EResult:
        allowed_failures = {
            None,
            "after_run",
            "after_retrieval",
            "after_artifact",
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
        }
        if failure_at not in allowed_failures:
            raise ValueError("unknown synthetic failure injection point")

        def inject(point: str) -> None:
            if failure_at == point:
                raise IntegrityValidationError(f"injected synthetic failure at {point}")

        assessment_id = self.record_rights_assessment(
            source_channel=SYNTHETIC_SOURCE_CHANNEL,
            rights_profile_id=SYNTHETIC_RIGHTS_PROFILE,
            activity="SYNTHETIC_MATERIALIZATION",
            assessed_at=assessed_at,
        )
        run_id = self.create_acquisition_run(
            run_key=run_key,
            planned_at=planned_at,
            source_channel=SYNTHETIC_SOURCE_CHANNEL,
            rights_profile_id=SYNTHETIC_RIGHTS_PROFILE,
            rights_assessment_id=assessment_id,
            authorized_activity="SYNTHETIC_MATERIALIZATION",
        )
        state = self.latest_run_state(run_id)
        if state == "PLANNED":
            self.transition_run(
                run_id,
                "RUNNING",
                occurred_at="2026-10-08T01:00:02Z",
                reason="RIGHTS_PASS",
            )
        elif state not in {"RUNNING", "COMPLETE"}:
            raise ValidationConflictError("synthetic E2E requires a planned or running acquisition run")
        inject("after_run")

        validate_artifact_consistency(case.artifacts)
        validate_binding_consistency(case.bindings)
        validate_revision_lineage(case.vintages)
        connection = self._connect()
        try:
            connection.execute("BEGIN TRANSACTION")
            for index, (release, artifact, payload, claim, binding, vintage) in enumerate(
                zip(
                    case.releases,
                    case.artifacts,
                    case.payloads,
                    case.claims,
                    case.bindings,
                    case.vintages,
                    strict=True,
                )
            ):
                if artifact.payload_hash != raw_payload_hash(payload) or artifact.byte_size != len(payload):
                    raise IntegrityValidationError("synthetic artifact bytes contradict PIT-2B artifact")
                self._insert_or_verify(
                    connection,
                    "source_release",
                    "source_release_id",
                    (
                        release.source_release_id,
                        release.content_hash,
                        release.source_id,
                        release.data_acquisition_channel,
                        release.historical_evidence_channel,
                        release.release_family,
                        _json(release.content_projection()),
                    ),
                )
                retrieval_projection = {
                    "acquisition_run_id": run_id,
                    "source_release_id": release.source_release_id,
                    "retrieval_mode": "SYNTHETIC_FIXTURE",
                    "source_locator_safe": artifact.official_locator,
                    "retrieved_at": artifact.retrieved_at,
                    "response_payload_hash": artifact.payload_hash,
                }
                retrieval_id = _validation_hash("RETRIEVAL_EVIDENCE", retrieval_projection)
                self._insert_or_verify(
                    connection,
                    "retrieval_evidence",
                    "retrieval_evidence_id",
                    (
                        retrieval_id,
                        run_id,
                        release.source_release_id,
                        "SYNTHETIC_FIXTURE",
                        artifact.official_locator,
                        artifact.retrieved_at,
                        artifact.payload_hash,
                        _json(retrieval_projection),
                    ),
                )
                if index == 0:
                    inject("after_retrieval")
                self._insert_or_verify(
                    connection,
                    "restricted_artifact_blob",
                    "payload_hash",
                    (
                        artifact.payload_hash,
                        payload,
                        len(payload),
                        True,
                        SYNTHETIC_RETENTION_POLICY,
                        "RESTRICTED_EVIDENCE_STORE",
                    ),
                )
                self._insert_or_verify(
                    connection,
                    "evidence_artifact",
                    "artifact_id",
                    (
                        artifact.artifact_id,
                        artifact.content_hash,
                        release.source_release_id,
                        run_id,
                        retrieval_id,
                        SYNTHETIC_RIGHTS_PROFILE,
                        assessment_id,
                        artifact.payload_hash,
                        artifact.official_locator,
                        artifact.media_type,
                        artifact.byte_size,
                        _json(artifact.content_projection()),
                        True,
                    ),
                )
                if index == 0:
                    inject("after_artifact")
                temporal_id = self._persist_temporal_bound_row(connection, claim.temporal_bound)
                self._insert_or_verify(
                    connection,
                    "evidence_claim",
                    "claim_id",
                    (
                        claim.claim_id,
                        claim.content_hash,
                        release.source_release_id,
                        artifact.artifact_id,
                        temporal_id,
                        claim.claim_type,
                        claim.subject_scope,
                        _json(claim.content_projection()),
                    ),
                )
                if index == 0:
                    inject("after_claim")
                self._insert_or_verify(
                    connection,
                    "evidence_subject_binding",
                    "binding_id",
                    (
                        binding.binding_id,
                        binding.content_hash,
                        claim.claim_id,
                        release.source_release_id,
                        artifact.artifact_id,
                        binding.subject_scope,
                        _json(binding.content_projection()),
                    ),
                )
                if index == 0:
                    inject("after_binding")
                self._insert_or_verify(
                    connection,
                    "historical_vintage_claim",
                    "vintage_claim_id",
                    (
                        vintage.vintage_claim_id,
                        vintage.content_hash,
                        vintage.observation_id,
                        release.source_release_id,
                        temporal_id,
                        vintage.parent_vintage_claim_id,
                        vintage.semantic_value,
                        vintage.unit,
                        vintage.currency,
                        _json(vintage.content_projection()),
                        True,
                    ),
                )
                if index == 0:
                    inject("after_vintage")
                existing_evidence = connection.execute(
                    "SELECT claim_id, claim_content_hash FROM historical_vintage_claim_evidence WHERE vintage_claim_id=? AND evidence_ordinal=0",
                    [vintage.vintage_claim_id],
                ).fetchone()
                expected_evidence = (claim.claim_id, claim.content_hash)
                if existing_evidence is None:
                    connection.execute(
                        "INSERT INTO historical_vintage_claim_evidence VALUES (?, 0, ?, ?)",
                        [vintage.vintage_claim_id, *expected_evidence],
                    )
                elif tuple(existing_evidence) != expected_evidence:
                    raise ValidationConflictError("vintage evidence membership conflict")
                existing_binding = connection.execute(
                    "SELECT binding_id, binding_content_hash FROM historical_vintage_claim_binding WHERE vintage_claim_id=? AND binding_ordinal=0",
                    [vintage.vintage_claim_id],
                ).fetchone()
                expected_binding = (binding.binding_id, binding.content_hash)
                if existing_binding is None:
                    connection.execute(
                        "INSERT INTO historical_vintage_claim_binding VALUES (?, 0, ?, ?)",
                        [vintage.vintage_claim_id, *expected_binding],
                    )
                elif tuple(existing_binding) != expected_binding:
                    raise ValidationConflictError("vintage binding membership conflict")
                extraction_projection = {
                    "artifact_id": artifact.artifact_id,
                    "artifact_content_hash": artifact.content_hash,
                    "record_locator": SYNTHETIC_RECORD_LOCATOR,
                    "commodity": "Copper",
                    "period": "2024-02",
                    "raw_value": str(vintage.semantic_value),
                    "normalized_value": vintage.semantic_value,
                    "unit": vintage.unit,
                    "extractor_version": SYNTHETIC_EXTRACTOR,
                    "extracted_at": f"2026-10-08T01:00:1{index}Z",
                    "validation_status": "PASS",
                }
                extraction_id = _validation_hash("EXTRACTION_EVIDENCE", extraction_projection)
                self._insert_or_verify(
                    connection,
                    "extraction_evidence",
                    "extraction_id",
                    (
                        extraction_id,
                        artifact.artifact_id,
                        artifact.content_hash,
                        _json(SYNTHETIC_RECORD_LOCATOR),
                        "Copper",
                        "2024-02",
                        str(vintage.semantic_value),
                        vintage.semantic_value,
                        vintage.unit,
                        SYNTHETIC_EXTRACTOR,
                        extraction_projection["extracted_at"],
                        "PASS",
                        _json(extraction_projection),
                    ),
                )
                attribution_projection = {
                    "artifact_id": artifact.artifact_id,
                    "attribution_text": "SYNTHETIC TEST FIXTURE — NOT WORLD BANK EVIDENCE",
                }
                attribution_id = _validation_hash("ATTRIBUTION_METADATA", attribution_projection)
                self._insert_or_verify(
                    connection,
                    "attribution_metadata",
                    "attribution_id",
                    (
                        attribution_id,
                        artifact.artifact_id,
                        attribution_projection["attribution_text"],
                        _json(attribution_projection),
                    ),
                )

            manifest_hash, manifest_projection = acquisition_manifest_identity(
                acquisition_run_id=run_id,
                rights_profile_id=SYNTHETIC_RIGHTS_PROFILE,
                rights_profile_version="1.0.0",
                rights_assessment_id=assessment_id,
                artifacts=case.artifacts,
            )
            members = manifest_projection["artifact_members"]
            manifest_id = manifest_hash
            self._insert_or_verify(
                connection,
                "acquisition_manifest",
                "manifest_id",
                (
                    manifest_id,
                    manifest_hash,
                    run_id,
                    SYNTHETIC_RIGHTS_PROFILE,
                    "1.0.0",
                    assessment_id,
                    _json(manifest_projection),
                ),
            )
            for ordinal, member in enumerate(members):
                existing_member = connection.execute(
                    "SELECT artifact_id, artifact_content_hash, payload_hash FROM acquisition_manifest_member WHERE manifest_id=? AND artifact_ordinal=?",
                    [manifest_id, ordinal],
                ).fetchone()
                expected_member = (
                    member["artifact_id"],
                    member["artifact_content_hash"],
                    member["payload_hash"],
                )
                if existing_member is None:
                    connection.execute(
                        "INSERT INTO acquisition_manifest_member VALUES (?, ?, ?, ?, ?)",
                        [manifest_id, ordinal, *expected_member],
                    )
                elif tuple(existing_member) != expected_member:
                    raise ValidationConflictError("manifest membership conflict")
            inject("after_manifest")

            audit_projection = {
                "acquisition_run_id": run_id,
                "manifest_id": manifest_id,
                "rights_assessment_id": assessment_id,
                "audit_type": "SYNTHETIC_E2E",
                "synthetic_only": True,
            }
            audit_id = _validation_hash("AUDIT_REFERENCE", audit_projection)
            self._insert_or_verify(
                connection,
                "audit_reference",
                "audit_reference_id",
                (audit_id, run_id, manifest_id, assessment_id, "SYNTHETIC_E2E", _json(audit_projection)),
            )
            inject("after_audit")
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            connection.close()

        inject("before_replay")
        inject("during_replay")
        replay = self.validate_integrity()
        if not (
            replay.content_hashes_valid
            and replay.manifest_hashes_valid
            and replay.lineage_valid
            and replay.rights_valid
            and replay.trusted_membership_valid
        ):
            raise IntegrityValidationError("strict pre-promotion replay did not pass")

        selected = select_visible_vintage(
            case.vintages,
            cutoff_at="2024-04-09T00:00:00Z",
            mode="DAY_GRANULAR_PIT",
            source_timezone=SYNTHETIC_TIMEZONE,
            policy_version=DAY_GRANULAR_POLICY_VERSION,
        )
        if selected is None or replay.selected_vintage_claim_id != selected.vintage_claim_id:
            raise IntegrityValidationError("synthetic candidate selection unexpectedly differs on replay")
        index = case.vintages.index(selected)
        candidate = materialize_observation_version_candidate(
            selected,
            claim=case.claims[index],
            binding=case.bindings[index],
            release=case.releases[index],
            artifact=case.artifacts[index],
            all_vintages=case.vintages,
            all_artifacts=case.artifacts,
            all_bindings=case.bindings,
            cutoff_at="2024-04-09T00:00:00Z",
            contract=MaterializationContract(
                "DAY_GRANULAR_PIT",
                DAY_GRANULAR_POLICY_VERSION,
                SYNTHETIC_TRANSFORM,
                source_timezone=SYNTHETIC_TIMEZONE,
            ),
        )

        replay_projection = {
            "content_hashes_valid": replay.content_hashes_valid,
            "manifest_hashes_valid": replay.manifest_hashes_valid,
            "lineage_valid": replay.lineage_valid,
            "rights_valid": replay.rights_valid,
            "trusted_membership_valid": replay.trusted_membership_valid,
            "selected_vintage_claim_id": replay.selected_vintage_claim_id,
        }
        replay_fingerprint = _validation_hash("STRICT_REPLAY_RESULT", replay_projection)
        collection_members = tuple(
            sorted(
                (
                    {
                        "artifact_id": artifact.artifact_id,
                        "claim_id": claim.claim_id,
                        "binding_id": binding.binding_id,
                        "vintage_claim_id": vintage.vintage_claim_id,
                        "audit_reference_id": audit_id,
                    }
                    for artifact, claim, binding, vintage in zip(
                        case.artifacts, case.claims, case.bindings, case.vintages, strict=True
                    )
                ),
                key=lambda member: member["artifact_id"],
            )
        )
        promoted_at = "2026-10-08T01:00:20Z"
        collection_projection = {
            "status": "SYNTHETIC_VALIDATED",
            "acquisition_run_id": run_id,
            "manifest_id": manifest_id,
            "manifest_hash": manifest_hash,
            "rights_assessment_id": assessment_id,
            "audit_reference_id": audit_id,
            "replay_fingerprint": replay_fingerprint,
            "promoted_at": promoted_at,
            "members": collection_members,
            "promotion_checks": _trusted_promotion_checks(),
        }
        collection_hash = _validation_hash("TRUSTED_COLLECTION", collection_projection)
        collection_id = collection_hash
        inject("after_replay_before_promotion")

        connection = self._connect()
        try:
            connection.execute("BEGIN TRANSACTION")
            state = self._run_state_from_history(connection, run_id)
            if state == "RUNNING":
                self._append_run_event(
                    connection,
                    run_id,
                    "COMPLETE",
                    occurred_at=promoted_at,
                    reason="STRICT_REPLAY_AND_PROMOTION_PASS",
                )
            elif state != "COMPLETE":
                raise IntegrityValidationError("promotion requires a RUNNING or COMPLETE run")
            inject("during_promotion_transaction")
            self._insert_or_verify(
                connection,
                "trusted_collection",
                "collection_id",
                (
                    collection_id,
                    collection_hash,
                    run_id,
                    manifest_id,
                    assessment_id,
                    audit_id,
                    replay_fingerprint,
                    "SYNTHETIC_VALIDATED",
                    promoted_at,
                    _json(collection_projection),
                ),
            )
            for ordinal, member in enumerate(collection_members):
                membership_hash = _validation_hash("TRUSTED_COLLECTION_MEMBERSHIP", member)
                existing_member = connection.execute(
                    "SELECT artifact_id, claim_id, binding_id, vintage_claim_id, audit_reference_id, membership_hash FROM trusted_collection_membership WHERE collection_id=? AND membership_ordinal=?",
                    [collection_id, ordinal],
                ).fetchone()
                expected_member = (
                    member["artifact_id"],
                    member["claim_id"],
                    member["binding_id"],
                    member["vintage_claim_id"],
                    member["audit_reference_id"],
                    membership_hash,
                )
                if existing_member is None:
                    connection.execute(
                        "INSERT INTO trusted_collection_membership VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [collection_id, ordinal, *expected_member],
                    )
                elif tuple(existing_member) != expected_member:
                    raise ValidationConflictError("trusted collection membership conflict")
            final_replay = self._validate_integrity_connection(connection)
            if not final_replay.rights_valid:
                raise IntegrityValidationError("rights became invalid during promotion")
            inject("before_promotion_commit")
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            connection.close()
        return SyntheticE2EResult(
            run_id,
            manifest_id,
            manifest_hash,
            audit_id,
            collection_id,
            selected.vintage_claim_id,
            candidate.candidate_hash,
            tuple(item.artifact_id for item in case.artifacts),
            tuple(item.claim_id for item in case.claims),
            tuple(item.binding_id for item in case.bindings),
            tuple(item.vintage_claim_id for item in case.vintages),
        )

    @staticmethod
    def _release_from_projection(projection: Mapping[str, Any]) -> SourceRelease:
        declared = projection["declared_publication_bound"]
        return SourceRelease(
            source_id=projection["source_id"],
            data_acquisition_channel=projection["data_acquisition_channel"],
            historical_evidence_channel=projection["historical_evidence_channel"],
            release_family=projection["release_family"],
            official_release_id=projection["official_release_id"],
            release_label=projection["release_label"],
            declared_publication_bound=(
                None if declared is None else _temporal_from_projection(declared)
            ),
        )

    @staticmethod
    def _artifact_from_projection(projection: Mapping[str, Any]) -> EvidenceArtifact:
        return EvidenceArtifact(
            source_release_id=projection["source_release_id"],
            artifact_role=projection["artifact_role"],
            media_type=projection["media_type"],
            official_locator=projection["official_locator"],
            payload_hash=projection["payload_hash"],
            byte_size=projection["byte_size"],
            retrieved_at=projection["retrieved_at"],
            source_document_id=projection["source_document_id"],
        )

    @staticmethod
    def _claim_from_projection(projection: Mapping[str, Any]) -> EvidenceClaim:
        raw_value = projection["claimed_value"]
        return EvidenceClaim(
            source_release_id=projection["source_release_id"],
            artifact_id=projection["artifact_id"],
            claim_type=projection["claim_type"],
            subject_scope=projection["subject_scope"],
            claimed_period=projection["claimed_period"],
            claimed_value=None if raw_value is None else Decimal(str(raw_value)),
            unit=projection["unit"],
            currency=projection["currency"],
            temporal_bound=_temporal_from_projection(projection["temporal_bound"]),
            evidence_authority=projection["evidence_authority"],
            safe_locator=projection["safe_locator"],
            transformation_version=projection["transformation_version"],
        )

    @staticmethod
    def _binding_from_projection(projection: Mapping[str, Any]) -> EvidenceSubjectBinding:
        return EvidenceSubjectBinding(
            claim_id=projection["claim_id"],
            subject_scope=projection["subject_scope"],
            binding_states=tuple(projection["binding_states"]),
            source_release_id=projection["source_release_id"],
            artifact_id=projection["artifact_id"],
            expected_artifact_hash=projection["expected_artifact_hash"],
            record_locator=projection["record_locator"],
            source_id=projection["source_id"],
            instrument_id=projection["instrument_id"],
            metric_id=projection["metric_id"],
            source_record_identifier=projection["source_record_identifier"],
            observation_id=projection["observation_id"],
            historical_vintage_claim_id=projection["historical_vintage_claim_id"],
            historical_vintage_content_hash=projection["historical_vintage_content_hash"],
            target_release_record_key=projection["target_release_record_key"],
        )

    @staticmethod
    def _vintage_from_projection(projection: Mapping[str, Any]) -> HistoricalVintageClaim:
        return HistoricalVintageClaim(
            observation_id=projection["observation_id"],
            source_id=projection["source_id"],
            instrument_id=projection["instrument_id"],
            metric_id=projection["metric_id"],
            source_record_identifier=projection["source_record_identifier"],
            source_period=projection["source_period"],
            source_release_id=projection["source_release_id"],
            release_record_key=projection["release_record_key"],
            semantic_value=Decimal(str(projection["semantic_value"])),
            unit=projection["unit"],
            currency=projection["currency"],
            record_locator=projection["record_locator"],
            evidence_claim_ids=tuple(projection["evidence_claim_ids"]),
            evidence_claim_content_hashes=tuple(projection["evidence_claim_content_hashes"]),
            binding_ids=tuple(projection["binding_ids"]),
            binding_content_hashes=tuple(projection["binding_content_hashes"]),
            temporal_bound=_temporal_from_projection(projection["temporal_bound"]),
            evidence_authority=projection["evidence_authority"],
            transformation_version=projection["transformation_version"],
            parent_vintage_claim_id=projection["parent_vintage_claim_id"],
        )

    def _validate_integrity_connection(
        self, connection: duckdb.DuckDBPyConnection
    ) -> ReplayResult:
        self._verify_schema(
            connection,
            checksum=migration_checksum(),
            fingerprint=expected_schema_fingerprint(),
        )
        policy = connection.execute(
            "SELECT publication_allowed, public_api_allowed, export_allowed, "
            "redistribution_allowed, policy_json FROM system_policy WHERE policy_id=?",
            [SYSTEM_POLICY_ID],
        ).fetchone()
        if policy is None or policy[:4] != (False, False, False, False):
            raise IntegrityValidationError("public/export system policy is not fail closed")
        policy_projection = _parsed(policy[4])
        if policy_projection != {
            "policy_id": SYSTEM_POLICY_ID,
            "publication_allowed": False,
            "public_api_allowed": False,
            "export_allowed": False,
            "redistribution_allowed": False,
            "classification": DATABASE_CLASSIFICATION,
        }:
            raise IntegrityValidationError("system policy row/projection mismatch")
        if (
            any(SAFETY_FLAGS.values())
            or PIT_ENABLED_SOURCES
            or RESEARCH_ENABLED_SOURCES
            or BACKTEST_ENABLED_SOURCES
        ):
            raise IntegrityValidationError("repository operational safety controls are not frozen")

        for row in connection.execute(
            "SELECT rights_profile_id, profile_version, source_channel, rights_status, "
            "automated_retrieval_allowed, materialization_allowed, internal_analysis_allowed, "
            "public_use_allowed, export_allowed, profile_json FROM rights_profile"
        ).fetchall():
            projection = _parsed(row[9])
            if projection != {
                "rights_profile_id": row[0],
                "profile_version": row[1],
                "source_channel": row[2],
                "rights_status": row[3],
                "automated_retrieval_allowed": row[4],
                "materialization_allowed": row[5],
                "internal_analysis_allowed": row[6],
                "public_use_allowed": row[7],
                "export_allowed": row[8],
            }:
                raise IntegrityValidationError("rights profile row/projection mismatch")
        for row in connection.execute(
            "SELECT source_channel, activity_status, rights_profile_id, profile_version, "
            "allowlist_json FROM source_allowlist"
        ).fetchall():
            if _parsed(row[4]) != {
                "source_channel": row[0],
                "activity_status": row[1],
                "rights_profile_id": row[2],
                "profile_version": row[3],
            }:
                raise IntegrityValidationError("source allowlist row/projection mismatch")

        for (assessment_id,) in connection.execute(
            "SELECT rights_assessment_id FROM rights_assessment"
        ).fetchall():
            self._rights_assessment_row(connection, assessment_id)

        run_states: dict[str, str] = {}
        run_rows: dict[str, tuple[Any, ...]] = {}
        rights_valid = True
        for row in connection.execute(
            "SELECT acquisition_run_id, run_kind, source_channel, rights_profile_id, "
            "rights_profile_version, rights_assessment_id, authorized_activity, "
            "retention_policy_id, planned_at, run_json FROM acquisition_run"
        ).fetchall():
            projection = _parsed(row[9])
            if (
                _validation_hash("ACQUISITION_RUN", projection) != row[0]
                or projection.get("run_kind") != row[1]
                or projection.get("source_channel") != row[2]
                or projection.get("rights_profile_id") != row[3]
                or projection.get("rights_profile_version") != row[4]
                or projection.get("rights_assessment_id") != row[5]
                or projection.get("authorized_activity") != row[6]
                or projection.get("retention_policy_id") != row[7]
                or projection.get("planned_at") != row[8]
            ):
                raise IntegrityValidationError("acquisition run identity or projection mismatch")
            assessment = self._rights_assessment_row(connection, row[5])
            if (
                assessment[1:6] != (row[3], row[4], row[2], row[6], "PASS")
                or not self._current_rights_valid(
                    connection,
                    rights_profile_id=row[3],
                    source_channel=row[2],
                    activity=row[6],
                )
            ):
                rights_valid = False
            run_states[row[0]] = self._run_state_from_history(connection, row[0])
            run_rows[row[0]] = row

        blobs: dict[str, tuple[bytes, int]] = {}
        for payload_hash, payload, byte_size in connection.execute(
            "SELECT payload_hash, payload_bytes, byte_size FROM restricted_artifact_blob"
        ).fetchall():
            payload_bytes = bytes(payload)
            if raw_payload_hash(payload_bytes) != payload_hash or len(payload_bytes) != byte_size:
                raise IntegrityValidationError("artifact payload hash or byte length mismatch")
            blobs[payload_hash] = (payload_bytes, int(byte_size))

        temporal: dict[str, TemporalBound] = {}
        for row in connection.execute(
            "SELECT temporal_bound_id, bound_type, precision, normalized_start, normalized_end, "
            "source_timezone, bound_json FROM temporal_bound"
        ).fetchall():
            projection = _parsed(row[6])
            bound = _temporal_from_projection(projection)
            if (
                _temporal_id(bound) != row[0]
                or _json(bound.projection()) != row[6]
                or (bound.bound_type, bound.precision, bound.normalized_start, bound.normalized_end, bound.source_timezone)
                != row[1:6]
            ):
                raise IntegrityValidationError("temporal bound identity or projection mismatch")
            temporal[row[0]] = bound

        releases: dict[str, SourceRelease] = {}
        for row in connection.execute(
            "SELECT source_release_id, source_release_content_hash, source_id, "
            "data_acquisition_channel, historical_evidence_channel, release_family, release_json "
            "FROM source_release"
        ).fetchall():
            projection = _parsed(row[6])
            release = self._release_from_projection(projection)
            if (
                release.source_release_id != row[0]
                or release.content_hash != row[1]
                or _json(release.content_projection()) != row[6]
                or (
                    release.source_id,
                    release.data_acquisition_channel,
                    release.historical_evidence_channel,
                    release.release_family,
                )
                != row[2:6]
            ):
                raise IntegrityValidationError("source release authoritative replay mismatch")
            releases[row[0]] = release

        retrievals: dict[str, tuple[Any, ...]] = {}
        for row in connection.execute(
            "SELECT retrieval_evidence_id, acquisition_run_id, source_release_id, retrieval_mode, "
            "source_locator_safe, retrieved_at, response_payload_hash, retrieval_json "
            "FROM retrieval_evidence"
        ).fetchall():
            projection = _parsed(row[7])
            if (
                _validation_hash("RETRIEVAL_EVIDENCE", projection) != row[0]
                or projection != {
                    "acquisition_run_id": row[1],
                    "source_release_id": row[2],
                    "retrieval_mode": row[3],
                    "source_locator_safe": row[4],
                    "retrieved_at": row[5],
                    "response_payload_hash": row[6],
                }
                or row[1] not in run_rows
                or row[2] not in releases
            ):
                raise IntegrityValidationError("retrieval evidence identity or provenance mismatch")
            retrievals[row[0]] = row

        artifacts: dict[str, EvidenceArtifact] = {}
        artifact_rows: dict[str, tuple[Any, ...]] = {}
        for row in connection.execute(
            "SELECT artifact_id, artifact_content_hash, source_release_id, acquisition_run_id, "
            "retrieval_evidence_id, rights_profile_id, rights_assessment_id, payload_hash, "
            "official_locator, media_type, byte_size, artifact_json, synthetic_only "
            "FROM evidence_artifact"
        ).fetchall():
            projection = _parsed(row[11])
            artifact = self._artifact_from_projection(projection)
            retrieval = retrievals.get(row[4])
            run = run_rows.get(row[3])
            if (
                artifact.artifact_id != row[0]
                or artifact.content_hash != row[1]
                or _json(artifact.content_projection()) != row[11]
                or artifact.source_release_id != row[2]
                or artifact.payload_hash != row[7]
                or artifact.official_locator != row[8]
                or artifact.media_type != row[9]
                or artifact.byte_size != row[10]
                or row[12] is not True
                or retrieval is None
                or retrieval[1:3] != (row[3], row[2])
                or retrieval[6] != row[7]
                or retrieval[4] != row[8]
                or run is None
                or run[3] != row[5]
                or run[5] != row[6]
                or row[7] not in blobs
                or blobs[row[7]][1] != row[10]
            ):
                raise IntegrityValidationError("artifact authoritative replay/provenance mismatch")
            artifacts[row[0]] = artifact
            artifact_rows[row[0]] = row
        validate_artifact_consistency(tuple(artifacts.values()))

        claims: dict[str, EvidenceClaim] = {}
        for row in connection.execute(
            "SELECT claim_id, claim_content_hash, source_release_id, artifact_id, temporal_bound_id, "
            "claim_type, subject_scope, claim_json FROM evidence_claim"
        ).fetchall():
            projection = _parsed(row[7])
            claim = self._claim_from_projection(projection)
            if (
                claim.claim_id != row[0]
                or claim.content_hash != row[1]
                or _json(claim.content_projection()) != row[7]
                or claim.source_release_id != row[2]
                or claim.artifact_id != row[3]
                or _temporal_id(claim.temporal_bound) != row[4]
                or claim.claim_type != row[5]
                or claim.subject_scope != row[6]
                or row[2] not in releases
                or (row[3] is not None and row[3] not in artifacts)
            ):
                raise IntegrityValidationError("claim authoritative replay/provenance mismatch")
            claims[row[0]] = claim

        bindings: dict[str, EvidenceSubjectBinding] = {}
        for row in connection.execute(
            "SELECT binding_id, binding_content_hash, claim_id, source_release_id, artifact_id, "
            "subject_scope, binding_json FROM evidence_subject_binding"
        ).fetchall():
            projection = _parsed(row[6])
            binding = self._binding_from_projection(projection)
            claim = claims.get(row[2])
            release = releases.get(row[3]) if row[3] is not None else None
            artifact = artifacts.get(row[4]) if row[4] is not None else None
            if (
                binding.binding_id != row[0]
                or binding.content_hash != row[1]
                or _json(binding.content_projection()) != row[6]
                or binding.claim_id != row[2]
                or binding.source_release_id != row[3]
                or binding.artifact_id != row[4]
                or binding.subject_scope != row[5]
                or claim is None
                or release is None
            ):
                raise IntegrityValidationError("binding authoritative replay/provenance mismatch")
            validate_subject_binding(binding, claim=claim, release=release, artifact=artifact)
            bindings[row[0]] = binding
        validate_binding_consistency(tuple(bindings.values()))

        vintages: dict[str, HistoricalVintageClaim] = {}
        for row in connection.execute(
            "SELECT vintage_claim_id, vintage_content_hash, observation_id, source_release_id, "
            "temporal_bound_id, parent_vintage_claim_id, semantic_value, unit, currency, "
            "vintage_json, synthetic_only FROM historical_vintage_claim"
        ).fetchall():
            projection = _parsed(row[9])
            vintage = self._vintage_from_projection(projection)
            if (
                vintage.vintage_claim_id != row[0]
                or vintage.content_hash != row[1]
                or _json(vintage.content_projection()) != row[9]
                or vintage.observation_id != row[2]
                or vintage.source_release_id != row[3]
                or _temporal_id(vintage.temporal_bound) != row[4]
                or vintage.parent_vintage_claim_id != row[5]
                or vintage.semantic_value != row[6]
                or vintage.unit != row[7]
                or vintage.currency != row[8]
                or row[10] is not True
            ):
                raise IntegrityValidationError("vintage authoritative replay/row mismatch")
            evidence_rows = connection.execute(
                "SELECT evidence_ordinal, claim_id, claim_content_hash "
                "FROM historical_vintage_claim_evidence WHERE vintage_claim_id=? ORDER BY evidence_ordinal",
                [row[0]],
            ).fetchall()
            binding_rows = connection.execute(
                "SELECT binding_ordinal, binding_id, binding_content_hash "
                "FROM historical_vintage_claim_binding WHERE vintage_claim_id=? ORDER BY binding_ordinal",
                [row[0]],
            ).fetchall()
            expected_evidence = tuple(
                (ordinal, claim_id, content_hash)
                for ordinal, (claim_id, content_hash) in enumerate(
                    zip(
                        vintage.evidence_claim_ids,
                        vintage.evidence_claim_content_hashes,
                        strict=True,
                    )
                )
            )
            expected_bindings = tuple(
                (ordinal, binding_id, content_hash)
                for ordinal, (binding_id, content_hash) in enumerate(
                    zip(vintage.binding_ids, vintage.binding_content_hashes, strict=True)
                )
            )
            if tuple(evidence_rows) != expected_evidence or tuple(binding_rows) != expected_bindings:
                raise IntegrityValidationError("vintage exact evidence/binding membership mismatch")
            if any(
                claims.get(claim_id) is None
                or claims[claim_id].content_hash != content_hash
                for claim_id, content_hash in zip(
                    vintage.evidence_claim_ids,
                    vintage.evidence_claim_content_hashes,
                    strict=True,
                )
            ) or any(
                bindings.get(binding_id) is None
                or bindings[binding_id].content_hash != content_hash
                for binding_id, content_hash in zip(
                    vintage.binding_ids,
                    vintage.binding_content_hashes,
                    strict=True,
                )
            ):
                raise IntegrityValidationError("vintage references non-authoritative evidence")
            vintages[row[0]] = vintage

        vintage_values = tuple(vintages.values())
        if vintage_values:
            validate_revision_lineage(vintage_values)
            selected = select_visible_vintage(
                vintage_values,
                cutoff_at="2024-04-09T00:00:00Z",
                mode="DAY_GRANULAR_PIT",
                source_timezone=SYNTHETIC_TIMEZONE,
                policy_version=DAY_GRANULAR_POLICY_VERSION,
            )
            if selected is None:
                raise IntegrityValidationError("replay selection produced no visible vintage")
            selected_id: str | None = selected.vintage_claim_id
        else:
            selected_id = None

        manifests: dict[str, tuple[Any, ...]] = {}
        manifest_members: dict[str, tuple[tuple[Any, ...], ...]] = {}
        for row in connection.execute(
            "SELECT manifest_id, manifest_hash, acquisition_run_id, rights_profile_id, "
            "rights_profile_version, rights_assessment_id, manifest_json FROM acquisition_manifest"
        ).fetchall():
            members = tuple(
                connection.execute(
                    "SELECT artifact_id, artifact_content_hash, payload_hash "
                    "FROM acquisition_manifest_member WHERE manifest_id=? ORDER BY artifact_ordinal",
                    [row[0]],
                ).fetchall()
            )
            try:
                member_artifacts = tuple(artifacts[item[0]] for item in members)
            except KeyError as exc:
                raise IntegrityValidationError("manifest references an unknown artifact") from exc
            computed_id, computed_projection = acquisition_manifest_identity(
                acquisition_run_id=row[2],
                rights_profile_id=row[3],
                rights_profile_version=row[4],
                rights_assessment_id=row[5],
                artifacts=member_artifacts,
            )
            expected_members = tuple(
                (
                    item["artifact_id"],
                    item["artifact_content_hash"],
                    item["payload_hash"],
                )
                for item in computed_projection["artifact_members"]
            )
            if (
                computed_id != row[0]
                or computed_id != row[1]
                or _json(computed_projection) != row[6]
                or members != expected_members
                or row[2] not in run_rows
                or run_rows[row[2]][3:6] != (row[3], row[4], row[5])
            ):
                raise IntegrityValidationError("manifest identity, membership, or run binding mismatch")
            manifests[row[0]] = row
            manifest_members[row[0]] = members

        audits: dict[str, tuple[Any, ...]] = {}
        for row in connection.execute(
            "SELECT audit_reference_id, acquisition_run_id, manifest_id, rights_assessment_id, "
            "audit_type, audit_json FROM audit_reference"
        ).fetchall():
            projection = _parsed(row[5])
            manifest = manifests.get(row[2])
            if (
                _validation_hash("AUDIT_REFERENCE", projection) != row[0]
                or projection.get("acquisition_run_id") != row[1]
                or projection.get("manifest_id") != row[2]
                or projection.get("rights_assessment_id") != row[3]
                or projection.get("audit_type") != row[4]
                or manifest is None
                or manifest[2] != row[1]
                or manifest[5] != row[3]
            ):
                raise IntegrityValidationError("audit identity or provenance coherence mismatch")
            audits[row[0]] = row

        for row in connection.execute(
            "SELECT extraction_id, artifact_id, artifact_content_hash, record_locator_json, "
            "commodity, period, raw_value, normalized_value, unit, extractor_version, "
            "extracted_at, validation_status, extraction_json FROM extraction_evidence"
        ).fetchall():
            projection = _parsed(row[12])
            artifact = artifacts.get(row[1])
            if (
                _validation_hash("EXTRACTION_EVIDENCE", projection) != row[0]
                or artifact is None
                or artifact.content_hash != row[2]
                or _json(projection.get("record_locator")) != row[3]
                or projection.get("commodity") != row[4]
                or projection.get("period") != row[5]
                or projection.get("raw_value") != row[6]
                or Decimal(str(projection.get("normalized_value"))) != row[7]
                or projection.get("unit") != row[8]
                or projection.get("extractor_version") != row[9]
                or projection.get("extracted_at") != row[10]
                or projection.get("validation_status") != row[11]
            ):
                raise IntegrityValidationError("extraction evidence replay mismatch")

        for row in connection.execute(
            "SELECT attribution_id, artifact_id, attribution_text, metadata_json "
            "FROM attribution_metadata"
        ).fetchall():
            projection = _parsed(row[3])
            if (
                _validation_hash("ATTRIBUTION_METADATA", projection) != row[0]
                or projection != {"artifact_id": row[1], "attribution_text": row[2]}
                or row[1] not in artifacts
            ):
                raise IntegrityValidationError("attribution metadata replay mismatch")

        trusted_membership_valid = True
        trusted_completed_run_ids: set[str] = set()
        for row in connection.execute(
            "SELECT collection_id, collection_hash, acquisition_run_id, manifest_id, "
            "rights_assessment_id, audit_reference_id, replay_fingerprint, collection_status, "
            "promoted_at, collection_json FROM trusted_collection"
        ).fetchall():
            projection = _parsed(row[9])
            audit = audits.get(row[5])
            manifest = manifests.get(row[3])
            expected_replay = _validation_hash(
                "STRICT_REPLAY_RESULT",
                {
                    "content_hashes_valid": True,
                    "manifest_hashes_valid": True,
                    "lineage_valid": True,
                    "rights_valid": True,
                    "trusted_membership_valid": True,
                    "selected_vintage_claim_id": selected_id,
                },
            )
            if (
                _validation_hash("TRUSTED_COLLECTION", projection) != row[0]
                or row[0] != row[1]
                or projection.get("status") != row[7]
                or projection.get("acquisition_run_id") != row[2]
                or projection.get("manifest_id") != row[3]
                or projection.get("rights_assessment_id") != row[4]
                or projection.get("audit_reference_id") != row[5]
                or projection.get("replay_fingerprint") != row[6]
                or projection.get("promoted_at") != row[8]
                or projection.get("manifest_hash") != (None if manifest is None else manifest[1])
                or projection.get("promotion_checks") != _trusted_promotion_checks()
                or row[6] != expected_replay
                or audit is None
                or audit[1:4] != (row[2], row[3], row[4])
                or manifest is None
                or run_states.get(row[2]) not in {"COMPLETE", "INVALIDATED"}
            ):
                raise IntegrityValidationError("trusted collection promotion provenance mismatch")
            members = tuple(
                connection.execute(
                    "SELECT artifact_id, claim_id, binding_id, vintage_claim_id, "
                    "audit_reference_id, membership_hash FROM trusted_collection_membership "
                    "WHERE collection_id=? ORDER BY membership_ordinal",
                    [row[0]],
                ).fetchall()
            )
            expected_members = tuple(projection.get("members", ()))
            manifest_artifact_ids = {item[0] for item in manifest_members[row[3]]}
            projected_artifact_ids = {
                item.get("artifact_id") for item in expected_members if isinstance(item, Mapping)
            }
            if (
                not expected_members
                or len(members) != len(manifest_members[row[3]])
                or len(members) != len(expected_members)
                or projected_artifact_ids != manifest_artifact_ids
            ):
                raise IntegrityValidationError("trusted collection membership count mismatch")
            for stored, member in zip(members, expected_members, strict=True):
                expected = (
                    member["artifact_id"],
                    member["claim_id"],
                    member["binding_id"],
                    member["vintage_claim_id"],
                    member["audit_reference_id"],
                    _validation_hash("TRUSTED_COLLECTION_MEMBERSHIP", member),
                )
                claim = claims.get(stored[1])
                binding = bindings.get(stored[2])
                vintage = vintages.get(stored[3])
                if (
                    tuple(stored) != expected
                    or stored[0] not in manifest_artifact_ids
                    or claim is None
                    or claim.artifact_id != stored[0]
                    or binding is None
                    or binding.claim_id != stored[1]
                    or binding.artifact_id != stored[0]
                    or vintage is None
                    or stored[1] not in vintage.evidence_claim_ids
                    or stored[2] not in vintage.binding_ids
                    or stored[4] != row[5]
                ):
                    raise IntegrityValidationError("trusted collection exact membership mismatch")
            members_by_artifact = {member[0]: member for member in members}
            authoritative_members = []
            for artifact_id, _, _ in manifest_members[row[3]]:
                member = members_by_artifact.get(artifact_id)
                if member is None:
                    raise IntegrityValidationError(
                        "trusted collection lacks canonical manifest membership"
                    )
                authoritative_members.append(
                    {
                        "artifact_id": member[0],
                        "claim_id": member[1],
                        "binding_id": member[2],
                        "vintage_claim_id": member[3],
                        "audit_reference_id": member[4],
                    }
                )
            authoritative_projection = {
                "status": row[7],
                "acquisition_run_id": row[2],
                "manifest_id": row[3],
                "manifest_hash": manifest[1],
                "rights_assessment_id": row[4],
                "audit_reference_id": row[5],
                "replay_fingerprint": row[6],
                "promoted_at": row[8],
                "members": authoritative_members,
                "promotion_checks": _trusted_promotion_checks(),
            }
            if (
                projection != authoritative_projection
                or _validation_hash("TRUSTED_COLLECTION", authoritative_projection) != row[0]
            ):
                raise IntegrityValidationError(
                    "trusted collection authoritative promotion projection mismatch"
                )
            trusted_completed_run_ids.add(row[2])

        dataset_rows = connection.execute(
            "SELECT dataset_record_id, rights_profile_id, rights_assessment_id, acquisition_run_id, "
            "source_contract_id, source_relative_path, source_sha256, commodity, period, value, unit, "
            "source_lineage_json, historical_availability_status, data_classification "
            "FROM dataset_observation ORDER BY period"
        ).fetchall()
        dataset_completed_run_ids: set[str] = set()
        if dataset_rows:
            source_bytes = WORLD_BANK_SOURCE_PATH.read_bytes()
            if hashlib.sha256(source_bytes).hexdigest() != WORLD_BANK_SOURCE_SHA256:
                raise IntegrityValidationError("approved World Bank source hash changed")
            expected_periods = _month_sequence(
                WORLD_BANK_COPPER_FIRST_PERIOD, WORLD_BANK_COPPER_LAST_PERIOD
            )
            if len(dataset_rows) != WORLD_BANK_COPPER_RECORD_COUNT or tuple(
                row[8] for row in dataset_rows
            ) != expected_periods:
                raise IntegrityValidationError("persisted World Bank Copper months are incomplete")
            for row in dataset_rows:
                lineage = _parsed(row[11])
                expected_lineage = {
                    "source_contract_id": row[4],
                    "source_relative_path": row[5],
                    "source_sha256": row[6],
                    "source_channel": DATASET_SOURCE_CHANNEL,
                    "data_classification": "REAL_DATASET_DERIVED",
                    "import_run_id": row[3],
                    "rights_profile_id": row[1],
                    "rights_assessment_id": row[2],
                }
                projection = {
                    "rights_profile_id": row[1],
                    "rights_assessment_id": row[2],
                    "acquisition_run_id": row[3],
                    "source_contract_id": row[4],
                    "source_relative_path": row[5],
                    "source_sha256": row[6],
                    "commodity": row[7],
                    "period": row[8],
                    "value": row[9],
                    "unit": row[10],
                    "source_lineage": lineage,
                    "historical_availability_status": row[12],
                    "data_classification": row[13],
                }
                run = run_rows.get(row[3])
                if (
                    _validation_hash("DATASET_OBSERVATION", projection) != row[0]
                    or lineage != expected_lineage
                    or row[4] != WORLD_BANK_SOURCE_CONTRACT
                    or row[5] != WORLD_BANK_SOURCE_RELATIVE_PATH
                    or row[6] != WORLD_BANK_SOURCE_SHA256
                    or row[7] != "Copper"
                    or row[10] != WORLD_BANK_COPPER_UNIT
                    or row[12] != "ABSENT_NOT_PIT_VERIFIED"
                    or row[13] != "REAL_DATASET_DERIVED"
                    or run is None
                    or run[2:7]
                    != (
                        DATASET_SOURCE_CHANNEL,
                        DATASET_RIGHTS_PROFILE,
                        "1.0.0",
                        row[2],
                        "DATASET_READ_ONLY_IMPORT",
                    )
                    or run_states.get(row[3]) != "COMPLETE"
                ):
                    raise IntegrityValidationError("dataset source contract or lineage mismatch")
                dataset_completed_run_ids.add(row[3])

        for run_id, state in run_states.items():
            if state != "COMPLETE":
                continue
            run_kind = run_rows[run_id][1]
            if run_kind == "SYNTHETIC_VALIDATION" and run_id not in trusted_completed_run_ids:
                raise IntegrityValidationError(
                    "COMPLETE synthetic run lacks validated trusted-promotion proof"
                )
            if (
                run_kind == "DATASET_READ_ONLY_VALIDATION"
                and run_id not in dataset_completed_run_ids
            ):
                raise IntegrityValidationError(
                    "COMPLETE dataset run lacks authenticated dataset-import proof"
                )

        return ReplayResult(
            True,
            True,
            True,
            rights_valid,
            trusted_membership_valid,
            selected_id,
        )

    def validate_integrity(self) -> ReplayResult:
        connection = self._connect(read_only=True)
        try:
            return self._validate_integrity_connection(connection)
        finally:
            connection.close()

    def provenance_for_vintage(self, vintage_claim_id: str) -> Mapping[str, Any]:
        self.validate_integrity()
        connection = self._connect(read_only=True)
        try:
            rows = connection.execute(
                "SELECT v.vintage_claim_id, v.vintage_content_hash, b.binding_id, c.claim_id, "
                "a.artifact_id, a.payload_hash, tc.acquisition_run_id, tc.manifest_id, "
                "tc.rights_assessment_id, tc.audit_reference_id "
                "FROM trusted_collection_membership tcm "
                "JOIN trusted_collection tc ON tc.collection_id=tcm.collection_id "
                "AND tc.audit_reference_id=tcm.audit_reference_id "
                "JOIN audit_reference au ON au.audit_reference_id=tc.audit_reference_id "
                "AND au.acquisition_run_id=tc.acquisition_run_id "
                "AND au.manifest_id=tc.manifest_id "
                "AND au.rights_assessment_id=tc.rights_assessment_id "
                "JOIN acquisition_manifest_member amm ON amm.manifest_id=tc.manifest_id "
                "AND amm.artifact_id=tcm.artifact_id "
                "JOIN historical_vintage_claim v ON v.vintage_claim_id=tcm.vintage_claim_id "
                "JOIN historical_vintage_claim_binding vb ON vb.vintage_claim_id=v.vintage_claim_id "
                "AND vb.binding_id=tcm.binding_id "
                "JOIN evidence_subject_binding b ON b.binding_id=tcm.binding_id "
                "JOIN historical_vintage_claim_evidence ve ON ve.vintage_claim_id=v.vintage_claim_id "
                "AND ve.claim_id=tcm.claim_id "
                "JOIN evidence_claim c ON c.claim_id=tcm.claim_id AND c.claim_id=b.claim_id "
                "JOIN evidence_artifact a ON a.artifact_id=tcm.artifact_id "
                "AND a.artifact_id=b.artifact_id AND a.artifact_id=c.artifact_id "
                "WHERE tcm.vintage_claim_id=?",
                [vintage_claim_id],
            ).fetchall()
            if len(rows) != 1:
                raise IntegrityValidationError(
                    "vintage provenance is absent or has an ambiguous authoritative chain"
                )
            columns = (
                "vintage_claim_id", "vintage_content_hash", "binding_id", "claim_id",
                "artifact_id", "payload_hash", "acquisition_run_id", "manifest_id",
                "rights_assessment_id", "audit_reference_id",
            )
            return dict(zip(columns, rows[0], strict=True))
        finally:
            connection.close()

    def copper_history(self) -> tuple[Mapping[str, Any], ...]:
        self.validate_integrity()
        connection = self._connect(read_only=True)
        try:
            rows = connection.execute(
                "SELECT vintage_claim_id, semantic_value, unit, currency, parent_vintage_claim_id, tb.normalized_start "
                "FROM historical_vintage_claim v JOIN temporal_bound tb ON tb.temporal_bound_id=v.temporal_bound_id "
                "WHERE json_extract_string(v.vintage_json, '$.source_id')='WORLD_BANK' "
                "AND json_extract_string(v.vintage_json, '$.instrument_id')='copper_world_bank_monthly' "
                "AND json_extract_string(v.vintage_json, '$.metric_id')='MONTHLY_PRICE' "
                "AND v.synthetic_only=TRUE "
                "ORDER BY tb.normalized_start"
            ).fetchall()
            columns = ("vintage_claim_id", "value", "unit", "currency", "parent_vintage_claim_id", "available_date")
            return tuple(dict(zip(columns, row, strict=True)) for row in rows)
        finally:
            connection.close()

    def blocked_records(self) -> tuple[Mapping[str, Any], ...]:
        connection = self._connect(read_only=True)
        try:
            rows = connection.execute(
                "SELECT sa.source_channel, sa.activity_status, rp.rights_status FROM source_allowlist sa JOIN rights_profile rp ON rp.rights_profile_id=sa.rights_profile_id WHERE sa.activity_status='DISABLED' OR rp.rights_status='REVIEW_REQUIRED' ORDER BY sa.source_channel"
            ).fetchall()
            return tuple(
                {"source_channel": row[0], "activity_status": row[1], "rights_status": row[2]}
                for row in rows
            )
        finally:
            connection.close()

    def active_trusted_collection_ids(self) -> tuple[str, ...]:
        """Return only collections that still pass full replay and current rights."""
        try:
            replay = self.validate_integrity()
        except (IntegrityValidationError, ValidationMigrationError, RightsGateBlocked):
            return ()
        if not replay.rights_valid:
            return ()
        connection = self._connect(read_only=True)
        try:
            rows = connection.execute(
                "SELECT collection_id, acquisition_run_id FROM trusted_collection "
                "WHERE collection_status='SYNTHETIC_VALIDATED' ORDER BY collection_id"
            ).fetchall()
            return tuple(
                collection_id
                for collection_id, run_id in rows
                if self._run_state_from_history(connection, run_id) == "COMPLETE"
            )
        finally:
            connection.close()

    @staticmethod
    def _validate_world_bank_copper_document(
        document: Mapping[str, Any],
    ) -> tuple[tuple[str, Decimal], ...]:
        if not isinstance(document, Mapping):
            raise ValidationConflictError("World Bank source must be a JSON object")
        try:
            copper = document["series"]["copper"]
            observations = copper["observations"]
        except (KeyError, TypeError) as exc:
            raise ValidationConflictError("World Bank Copper source structure is absent") from exc
        if (
            document.get("schemaVersion") != 1
            or document.get("source") != "WORLD_BANK_PINK_SHEET"
            or document.get("isRealData") is not True
            or document.get("frequency") != "monthly"
            or copper.get("id") != "copper"
            or copper.get("nameEn") != "Copper"
            or copper.get("currency") != "USD"
            or copper.get("sourceColumn") != "Copper"
            or copper.get("sourceUnit") != WORLD_BANK_COPPER_UNIT
            or copper.get("observationCount") != WORLD_BANK_COPPER_RECORD_COUNT
            or copper.get("firstPeriod") != WORLD_BANK_COPPER_FIRST_PERIOD
            or copper.get("latestPeriod") != WORLD_BANK_COPPER_LAST_PERIOD
            or not isinstance(observations, list)
        ):
            raise ValidationConflictError("World Bank Copper source contract does not match")
        expected_periods = _month_sequence(
            WORLD_BANK_COPPER_FIRST_PERIOD, WORLD_BANK_COPPER_LAST_PERIOD
        )
        if len(observations) != WORLD_BANK_COPPER_RECORD_COUNT:
            raise ValidationConflictError("World Bank Copper record count does not match")
        values: list[tuple[str, Decimal]] = []
        seen: set[str] = set()
        for expected_period, item in zip(expected_periods, observations, strict=True):
            if not isinstance(item, Mapping) or set(item) != {"period", "value"}:
                raise ValidationConflictError("World Bank Copper observation shape is invalid")
            period = item["period"]
            raw_value = item["value"]
            if (
                not isinstance(period, str)
                or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", period)
                or period != expected_period
                or period in seen
            ):
                raise ValidationConflictError("World Bank Copper months are malformed or incomplete")
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, Decimal)):
                raise ValidationConflictError("World Bank Copper values must be exact JSON decimals")
            value = Decimal(raw_value) if isinstance(raw_value, int) else raw_value
            if not value.is_finite() or value <= 0:
                raise ValidationConflictError("World Bank Copper values must be positive finite Decimal")
            seen.add(period)
            values.append((period, value))
        if tuple(period for period, _ in values) != expected_periods:
            raise ValidationConflictError("World Bank Copper monthly completeness failed")
        return tuple(values)

    def import_dataset_copy(
        self,
        source_path: str | Path = WORLD_BANK_SOURCE_PATH,
        *,
        planned_at: str = "2026-10-08T02:00:00Z",
        assessed_at: str = "2026-10-08T02:00:01Z",
    ) -> int:
        if not isinstance(source_path, (str, Path)):
            raise ValidationConflictError("generic caller rows cannot be labeled REAL_DATASET_DERIVED")
        supplied_source = Path(source_path).expanduser()
        if supplied_source.is_symlink():
            raise ValidationConflictError("World Bank source path cannot be a symlink")
        resolved_source = supplied_source.resolve(strict=True)
        if resolved_source != WORLD_BANK_SOURCE_PATH.resolve(strict=True):
            raise ValidationConflictError("REAL_DATASET_DERIVED requires the approved repository source")
        source_bytes = resolved_source.read_bytes()
        actual_sha = hashlib.sha256(source_bytes).hexdigest()
        if actual_sha != WORLD_BANK_SOURCE_SHA256:
            raise ValidationConflictError("World Bank source SHA-256 requires review")
        document = parse_json_strict(source_bytes)
        values = self._validate_world_bank_copper_document(document)
        assessment_id = self.record_rights_assessment(
            source_channel=DATASET_SOURCE_CHANNEL,
            rights_profile_id=DATASET_RIGHTS_PROFILE,
            activity="DATASET_READ_ONLY_IMPORT",
            assessed_at=assessed_at,
        )
        run_id = self.create_acquisition_run(
            run_key=f"C4-PIT-PRE-RIGHTS-DATASET-{WORLD_BANK_SOURCE_SHA256}",
            planned_at=planned_at,
            source_channel=DATASET_SOURCE_CHANNEL,
            rights_profile_id=DATASET_RIGHTS_PROFILE,
            rights_assessment_id=assessment_id,
            authorized_activity="DATASET_READ_ONLY_IMPORT",
            run_kind="DATASET_READ_ONLY_VALIDATION",
        )
        state = self.latest_run_state(run_id)
        if state == "PLANNED":
            self.transition_run(
                run_id,
                "RUNNING",
                occurred_at="2026-10-08T02:00:02Z",
                reason="DATASET_SOURCE_AND_RIGHTS_PASS",
            )
        elif state not in {"RUNNING", "COMPLETE"}:
            raise ValidationConflictError("dataset import run is not reusable")
        source_lineage = {
            "source_contract_id": WORLD_BANK_SOURCE_CONTRACT,
            "source_relative_path": WORLD_BANK_SOURCE_RELATIVE_PATH,
            "source_sha256": WORLD_BANK_SOURCE_SHA256,
            "source_channel": DATASET_SOURCE_CHANNEL,
            "data_classification": "REAL_DATASET_DERIVED",
            "import_run_id": run_id,
            "rights_profile_id": DATASET_RIGHTS_PROFILE,
            "rights_assessment_id": assessment_id,
        }
        connection = self._connect()
        try:
            connection.execute("BEGIN TRANSACTION")
            for period, value in values:
                projection = {
                    "rights_profile_id": DATASET_RIGHTS_PROFILE,
                    "rights_assessment_id": assessment_id,
                    "acquisition_run_id": run_id,
                    "source_contract_id": WORLD_BANK_SOURCE_CONTRACT,
                    "source_relative_path": WORLD_BANK_SOURCE_RELATIVE_PATH,
                    "source_sha256": WORLD_BANK_SOURCE_SHA256,
                    "commodity": "Copper",
                    "period": period,
                    "value": value,
                    "unit": WORLD_BANK_COPPER_UNIT,
                    "source_lineage": source_lineage,
                    "historical_availability_status": "ABSENT_NOT_PIT_VERIFIED",
                    "data_classification": "REAL_DATASET_DERIVED",
                }
                record_id = _validation_hash("DATASET_OBSERVATION", projection)
                self._insert_or_verify(
                    connection,
                    "dataset_observation",
                    "dataset_record_id",
                    (
                        record_id,
                        DATASET_RIGHTS_PROFILE,
                        assessment_id,
                        run_id,
                        WORLD_BANK_SOURCE_CONTRACT,
                        WORLD_BANK_SOURCE_RELATIVE_PATH,
                        WORLD_BANK_SOURCE_SHA256,
                        "Copper",
                        period,
                        value,
                        WORLD_BANK_COPPER_UNIT,
                        _json(source_lineage),
                        "ABSENT_NOT_PIT_VERIFIED",
                        "REAL_DATASET_DERIVED",
                    ),
                )
            if self._run_state_from_history(connection, run_id) == "RUNNING":
                self._append_run_event(
                    connection,
                    run_id,
                    "COMPLETE",
                    occurred_at="2026-10-08T02:00:03Z",
                    reason="DATASET_SOURCE_CONTRACT_REPLAY_PASS",
                )
            replay = self._validate_integrity_connection(connection)
            if not replay.rights_valid:
                raise IntegrityValidationError("dataset rights became invalid during import")
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            connection.close()
        return len(values)

    def performance_sanity(self, *, iterations: int = 100) -> Mapping[str, float | int]:
        if iterations < 1:
            raise ValueError("iterations must be positive")
        connection = self._connect(read_only=True)
        try:
            started = time.perf_counter()
            for _ in range(iterations):
                connection.execute(
                    "SELECT semantic_value FROM historical_vintage_claim ORDER BY semantic_value"
                ).fetchall()
            history_ms = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            for _ in range(iterations):
                connection.execute(
                    "SELECT v.vintage_claim_id, a.artifact_id FROM historical_vintage_claim v JOIN historical_vintage_claim_binding vb ON vb.vintage_claim_id=v.vintage_claim_id JOIN evidence_subject_binding b ON b.binding_id=vb.binding_id JOIN evidence_artifact a ON a.artifact_id=b.artifact_id"
                ).fetchall()
            provenance_ms = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            for _ in range(iterations):
                connection.execute(
                    "SELECT sa.source_channel FROM source_allowlist sa JOIN rights_profile rp ON rp.rights_profile_id=sa.rights_profile_id WHERE sa.activity_status='DISABLED' OR rp.rights_status='REVIEW_REQUIRED'"
                ).fetchall()
            rights_ms = (time.perf_counter() - started) * 1000
            artifact_count = connection.execute("SELECT COUNT(*) FROM evidence_artifact").fetchone()[0]
            vintage_count = connection.execute("SELECT COUNT(*) FROM historical_vintage_claim").fetchone()[0]
            return {
                "iterations": iterations,
                "artifact_count": artifact_count,
                "vintage_count": vintage_count,
                "history_total_ms": round(history_ms, 3),
                "provenance_total_ms": round(provenance_ms, 3),
                "rights_total_ms": round(rights_ms, 3),
            }
        finally:
            connection.close()
