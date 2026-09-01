"""Phase A append-only legacy backfill for Market_Observation_V2.

This controlled migration never changes Market_Raw or the formal A:L range.
It stores legacy observations as unresolved history; canonicalization is out of
scope and remains disabled.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from typing import Sequence

try:
    from c3_2_pending_raw_persistence import MARKET_RAW_COLUMNS
    from company_market_core import DataContractError
except ModuleNotFoundError:  # imported as scripts.c3_2_observation_migration
    from scripts.c3_2_pending_raw_persistence import MARKET_RAW_COLUMNS
    from scripts.company_market_core import DataContractError


V2_WORKSHEET = "Market_Observation_V2"
MIGRATION_VERSION = "C3.2_PHASE_A_LEGACY_V1"
V2_EXTENSION_COLUMNS = (
    "observation_id", "observation_at", "observation_kind", "canonical_status",
    "canonical_reason", "canonicalized_at", "legacy_source_row", "migration_version",
)
V2_COLUMNS = MARKET_RAW_COLUMNS + V2_EXTENSION_COLUMNS


@dataclass(frozen=True)
class MigrationPlan:
    source_count: int
    expected_rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class MigrationReconciliation:
    source_count: int
    existing_count: int
    matched_count: int
    append_count: int
    status: str
    failure_reason: str | None = None


def enforce_phase_a_safety() -> None:
    """Phase A cannot enable the formal market write path."""
    os.environ.pop("CONTROLLED_WRITE_APPROVAL", None)
    os.environ["ALLOW_GOOGLE_SHEET_WRITE"] = "0"
    os.environ["ALLOW_PENDING_RAW_WRITE"] = "0"


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _padded(row: Sequence[object], length: int) -> tuple[str, ...]:
    if len(row) > length:
        raise DataContractError("Row exceeds approved schema width.")
    return tuple(_text(value) for value in row) + ("",) * (length - len(row))


def legacy_observation_id(source_row: int, raw_row: Sequence[object]) -> str:
    """Deterministic physical ID; source row prevents same-value row collapse."""
    payload = "\x1f".join((MIGRATION_VERSION, str(source_row), *_padded(raw_row, len(MARKET_RAW_COLUMNS))))
    return "legacy-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_legacy_backfill_plan(headers: Sequence[object], raw_values: Sequence[Sequence[object]]) -> MigrationPlan:
    if tuple(_text(value) for value in headers) != MARKET_RAW_COLUMNS:
        raise DataContractError("Market_Raw schema mismatch.")
    rows: list[tuple[str, ...]] = []
    for offset, raw_row in enumerate(raw_values, start=2):
        preserved = _padded(raw_row, len(MARKET_RAW_COLUMNS))
        rows.append(preserved + (
            legacy_observation_id(offset, raw_row), "", "LEGACY_UNVERIFIED", "LEGACY_UNVERIFIED",
            "BACKFILL_NO_OBSERVATION_TIMESTAMP", "", str(offset), MIGRATION_VERSION,
        ))
    return MigrationPlan(len(raw_values), tuple(rows))


def reconcile_legacy_backfill(plan: MigrationPlan, headers: Sequence[object], existing_values: Sequence[Sequence[object]]) -> MigrationReconciliation:
    if tuple(_text(value) for value in headers) != V2_COLUMNS:
        return MigrationReconciliation(plan.source_count, len(existing_values), 0, 0, "FAIL_CLOSED", "V2_SCHEMA_MISMATCH")
    expected = {row[len(MARKET_RAW_COLUMNS)]: row for row in plan.expected_rows}
    existing: dict[str, tuple[str, ...]] = {}
    for raw_row in existing_values:
        row = _padded(raw_row, len(V2_COLUMNS))
        observation_id = row[len(MARKET_RAW_COLUMNS)]
        if not observation_id or observation_id in existing:
            return MigrationReconciliation(plan.source_count, len(existing_values), 0, 0, "FAIL_CLOSED", "OBSERVATION_ID_NOT_UNIQUE")
        existing[observation_id] = row
    unknown = set(existing) - set(expected)
    if unknown:
        return MigrationReconciliation(plan.source_count, len(existing_values), 0, 0, "FAIL_CLOSED", "UNEXPECTED_OBSERVATION_ID")
    mismatched = [key for key in set(existing) & set(expected) if existing[key] != expected[key]]
    if mismatched:
        return MigrationReconciliation(plan.source_count, len(existing_values), 0, 0, "FAIL_CLOSED", "OBSERVATION_VALUE_MISMATCH")
    matched = len(existing)
    return MigrationReconciliation(plan.source_count, len(existing_values), matched, plan.source_count - matched, "READY")


def _read_values(worksheet) -> tuple[Sequence[object], Sequence[Sequence[object]]]:
    values = worksheet.get_all_values()
    return (values[0], values[1:]) if values else ((), ())


def run_phase_a_migration(*, sheet_id: str, credential_file: str) -> MigrationReconciliation:
    """Create the isolated V2 sheet when absent, append only missing legacy IDs, then read back."""
    enforce_phase_a_safety()
    import gspread
    from gspread.exceptions import WorksheetNotFound

    spreadsheet = gspread.service_account(filename=credential_file).open_by_key(sheet_id)
    raw_headers, raw_values = _read_values(spreadsheet.worksheet("Market_Raw"))
    plan = build_legacy_backfill_plan(raw_headers, raw_values)
    try:
        target = spreadsheet.worksheet(V2_WORKSHEET)
    except WorksheetNotFound:
        target = spreadsheet.add_worksheet(title=V2_WORKSHEET, rows=max(plan.source_count + 1, 1000), cols=len(V2_COLUMNS))
        target.update([list(V2_COLUMNS)], "A1", value_input_option="RAW")
    target_headers, target_values = _read_values(target)
    reconciliation = reconcile_legacy_backfill(plan, target_headers, target_values)
    if reconciliation.status != "READY":
        return reconciliation
    if reconciliation.append_count:
        existing_ids = {row[len(MARKET_RAW_COLUMNS)] for row in target_values}
        rows = [list(row) for row in plan.expected_rows if row[len(MARKET_RAW_COLUMNS)] not in existing_ids]
        target.append_rows(rows, value_input_option="RAW")
    after_headers, after_values = _read_values(target)
    after = reconcile_legacy_backfill(plan, after_headers, after_values)
    if after.status != "READY" or after.append_count:
        return MigrationReconciliation(plan.source_count, len(after_values), after.matched_count, after.append_count, "FAIL_CLOSED", "READBACK_RECONCILIATION_FAILED")
    return MigrationReconciliation(plan.source_count, len(after_values), after.matched_count, 0, "MIGRATION_COMPLETE")


def main() -> int:
    result = run_phase_a_migration(
        sheet_id=os.environ.get("GOOGLE_SHEET_ID", ""),
        credential_file=os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"),
    )
    print("PHASE_A_MIGRATION=" + result.status + " source=" + str(result.source_count) + " migrated=" + str(result.matched_count) + " append=" + str(result.append_count))
    return 0 if result.status == "MIGRATION_COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
