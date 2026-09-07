"""Append-only canonical decisions for the C3.2 dual-read stage."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterable

try:
    from c3_2_observation_canonicalization import CanonicalDailyRecord
except ModuleNotFoundError:
    from scripts.c3_2_observation_canonicalization import CanonicalDailyRecord


CANONICAL_WORKSHEET = "Canonical_Daily_V2"
CANONICAL_COLUMNS = ("decision_id", "source_date", "material_id", "observation_id", "canonical_status", "canonical_reason", "decided_at", "decision_version")


@dataclass(frozen=True)
class CanonicalDecisionPlan:
    rows: tuple[tuple[str, ...], ...]
    duplicate_same_count: int
    status: str
    failure_reason: str | None = None


def decision_id(record: CanonicalDailyRecord) -> str:
    observation = record.observation
    payload = "\x1f".join(("C3.2_CANONICAL_V1", observation.source_date, observation.material_id, observation.record_id, record.canonical_status, record.canonical_reason))
    return "canonical-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_canonical_decisions(records: Iterable[CanonicalDailyRecord], *, decided_at: str, existing_rows: Iterable[tuple[str, ...]] = ()) -> CanonicalDecisionPlan:
    existing = {row[0]: row for row in existing_rows if row and row[0]}
    planned: list[tuple[str, ...]] = []
    duplicates = 0
    for record in records:
        if record.canonical_status != "CANONICAL" or record.observation.observation_kind == "LEGACY_UNVERIFIED":
            return CanonicalDecisionPlan((), duplicates, "FAIL_CLOSED", "NON_GOVERNED_CANONICAL_RECORD")
        row = (decision_id(record), record.observation.source_date, record.observation.material_id, record.observation.record_id, record.canonical_status, record.canonical_reason, decided_at, "C3.2_CANONICAL_V1")
        prior = existing.get(row[0])
        if prior is not None:
            if tuple(prior[:6]) != row[:6]:
                return CanonicalDecisionPlan((), duplicates, "FAIL_CLOSED", "DECISION_ID_MISMATCH")
            duplicates += 1
            continue
        existing[row[0]] = row
        planned.append(row)
    return CanonicalDecisionPlan(tuple(planned), duplicates, "READY")
