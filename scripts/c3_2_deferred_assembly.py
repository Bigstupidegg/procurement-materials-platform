"""Pure, fail-closed deferred same-date assembly for the C3.2 Free Source PoC.

There is intentionally no persistence client in this module.  Pending records
remain in memory in this PoC; a later human-approved persistence pilot must
provide its own writer after passing the feature gate.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from typing import Iterable

try:
    from company_market_collector import normalize_market_date
except ModuleNotFoundError:  # imported as scripts.c3_2_deferred_assembly
    from scripts.company_market_collector import normalize_market_date


ALLOW_PENDING_RAW_WRITE = "ALLOW_PENDING_RAW_WRITE"
REQUIRED_MATERIALS = (
    "CU_LME_CASH", "CU_LME_3M", "CU_SMM_CATHODE", "AL_LME_CASH",
    "PB_LME_CASH", "NI_LME_CASH", "SN_LME_CASH", "ZN_LME_CASH",
    "BRENT_FUT", "SILVER_FUT", "GOLD_FUT",
)
LME_MATERIALS = frozenset(REQUIRED_MATERIALS[:2] + REQUIRED_MATERIALS[3:8])
NON_LME_MATERIALS = frozenset(set(REQUIRED_MATERIALS) - LME_MATERIALS)
DATE_PARSE_OK = frozenset({"PASS", "MATCH", "PARSED"})


@dataclass(frozen=True)
class PendingSnapshot:
    material_id: str
    source_id: str
    source_date: str
    price: float
    currency: str
    unit: str
    market_type: str
    collected_at: str
    source_status: str
    date_parse_status: str
    run_id: str
    collector_version: str
    data_classification: str = "INTERNAL_OPERATIONAL"
    business_date: str | None = None


@dataclass(frozen=True)
class DeferredAssemblyResult:
    status: str
    business_date: str | None
    canonical_records: tuple[PendingSnapshot, ...]
    duplicate_status: str = "NONE"
    failure_reason: str | None = None


def assemble_deferred_canonical_business_date(target_date: str, canonical_records: Iterable[object]) -> DeferredAssemblyResult:
    """Assemble only records explicitly selected by the canonicalization layer."""
    snapshots: list[PendingSnapshot] = []
    for canonical in canonical_records:
        if getattr(canonical, "canonical_status", None) != "CANONICAL":
            return DeferredAssemblyResult("ASSEMBLY_INCOMPLETE", None, (), failure_reason="NON_CANONICAL_RECORD")
        observation = getattr(canonical, "observation", None)
        if observation is None:
            return DeferredAssemblyResult("ASSEMBLY_INCOMPLETE", None, (), failure_reason="MISSING_OBSERVATION")
        snapshots.append(PendingSnapshot(
            material_id=observation.material_id, source_id=observation.source_id,
            source_date=observation.source_date, price=observation.price,
            currency=observation.currency, unit=observation.unit,
            market_type=observation.market_type, collected_at=observation.observation_at,
            source_status=observation.source_status, date_parse_status=observation.date_parse_status,
            run_id="canonical:" + observation.record_id, collector_version="C3.2-canonical",
        ))
    return assemble_deferred_business_date(target_date, snapshots)


def enforce_pending_raw_write_disabled() -> None:
    """PoC safety: no approval token or environment can enable persistence."""
    os.environ.pop("CONTROLLED_WRITE_APPROVAL", None)
    os.environ[ALLOW_PENDING_RAW_WRITE] = "0"
    os.environ["ALLOW_GOOGLE_SHEET_WRITE"] = "0"


def pending_raw_write_enabled() -> bool:
    """The Free Source PoC deliberately implements no persistence path."""
    return False


def _is_valid_record(record: PendingSnapshot) -> bool:
    return (
        record.source_status == "SUCCESS"
        and record.date_parse_status in DATE_PARSE_OK
        and isinstance(record.price, (int, float))
        and not isinstance(record.price, bool)
        and math.isfinite(record.price)
        and record.data_classification == "INTERNAL_OPERATIONAL"
    )


def assemble_deferred_business_date(
    target_date: str, pending_records: Iterable[PendingSnapshot]
) -> DeferredAssemblyResult:
    """Assemble only explicitly dated, valid records for one business date."""
    target = normalize_market_date(target_date)
    if not target:
        return DeferredAssemblyResult("ASSEMBLY_INCOMPLETE", None, (), failure_reason="INVALID_TARGET_DATE")

    records = tuple(pending_records)
    dated_required = [record for record in records if record.material_id in REQUIRED_MATERIALS]
    if any(normalize_market_date(record.source_date) != target for record in dated_required):
        return DeferredAssemblyResult("DATE_MISMATCH", None, (), failure_reason="SOURCE_DATE_MISMATCH")

    by_material: dict[str, PendingSnapshot] = {}
    duplicate_status = "NONE"
    for record in dated_required:
        existing = by_material.get(record.material_id)
        if existing is None:
            by_material[record.material_id] = record
            continue
        same = (
            existing.source_id == record.source_id
            and existing.price == record.price
            and existing.unit == record.unit
            and existing.currency == record.currency
        )
        if same:
            duplicate_status = "DUPLICATE_SAME_VALUE"
            continue
        return DeferredAssemblyResult(
            "HUMAN_REVIEW_REQUIRED", None, (), "DUPLICATE_CONFLICT", record.material_id
        )

    present = frozenset(by_material)
    if "CU_SMM_CATHODE" not in present:
        return DeferredAssemblyResult("SMM_SNAPSHOT_MISSING", None, (), duplicate_status)
    if NON_LME_MATERIALS.issubset(present) and not (present & LME_MATERIALS):
        return DeferredAssemblyResult("WAITING_FOR_LME_DELAYED_DATA", None, (), duplicate_status)
    if present != frozenset(REQUIRED_MATERIALS):
        return DeferredAssemblyResult("ASSEMBLY_INCOMPLETE", None, (), duplicate_status)

    canonical = tuple(by_material[material] for material in REQUIRED_MATERIALS)
    if not all(_is_valid_record(record) for record in canonical):
        return DeferredAssemblyResult("ASSEMBLY_INCOMPLETE", None, (), duplicate_status, "INVALID_RECORD")
    return DeferredAssemblyResult("ASSEMBLY_COMPLETE", target, canonical, duplicate_status)
