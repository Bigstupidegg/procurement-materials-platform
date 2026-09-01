"""Append-only V2 planning for C3.2 Shadow observations.

This module never writes A:L or Market_Raw.  V2 persistence is deliberately
separate and callers must perform an explicit append after reconciliation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import os
from typing import Iterable, Mapping, Sequence

try:
    from c3_2_observation_canonicalization import YAHOO_UNCONFIRMED, classify_yahoo_close
    from c3_2_observation_migration import V2_COLUMNS
    from c3_2_pending_raw_persistence import MARKET_RAW_COLUMNS
    from company_market_collector import normalize_market_date
    from company_market_core import DataContractError, MarketQuote
except ModuleNotFoundError:
    from scripts.c3_2_observation_canonicalization import YAHOO_UNCONFIRMED, classify_yahoo_close
    from scripts.c3_2_observation_migration import V2_COLUMNS
    from scripts.c3_2_pending_raw_persistence import MARKET_RAW_COLUMNS
    from scripts.company_market_collector import normalize_market_date
    from scripts.company_market_core import DataContractError, MarketQuote


OBSERVATION_SPECS = {
    "copper_lme_cash": ("CU_LME_CASH", "LME_CASH_OFFER", "SPOT", "DAILY_SNAPSHOT"),
    "copper_lme_3m": ("CU_LME_3M", "LME_3M_OFFER", "FUTURE", "DAILY_SNAPSHOT"),
    "smm_electrolytic_copper": ("CU_SMM_CATHODE", "SMM_1_COPPER_CATHODE", "SPOT", "DAILY_SNAPSHOT"),
    "aluminium_lme_cash": ("AL_LME_CASH", "LME_CASH_OFFER", "SPOT", "DAILY_SNAPSHOT"),
    "lead_lme_cash": ("PB_LME_CASH", "LME_CASH_OFFER", "SPOT", "DAILY_SNAPSHOT"),
    "nickel_lme_cash": ("NI_LME_CASH", "LME_CASH_OFFER", "SPOT", "DAILY_SNAPSHOT"),
    "tin_lme_cash": ("SN_LME_CASH", "LME_CASH_OFFER", "SPOT", "DAILY_SNAPSHOT"),
    "zinc_lme_cash": ("ZN_LME_CASH", "LME_CASH_OFFER", "SPOT", "DAILY_SNAPSHOT"),
    "brent_yfinance": ("BRENT_FUT", "YFINANCE_BZ=F", "FUTURE", YAHOO_UNCONFIRMED),
    "silver_yfinance": ("SILVER_FUT", "YFINANCE_SI=F", "FUTURE", YAHOO_UNCONFIRMED),
    "gold_yfinance": ("GOLD_FUT", "YFINANCE_GC=F", "FUTURE", YAHOO_UNCONFIRMED),
}


@dataclass(frozen=True)
class ShadowAppendPlan:
    rows: tuple[tuple[str, ...], ...]
    duplicate_same_count: int
    status: str
    failure_reason: str | None = None


def enforce_shadow_write_safety() -> None:
    os.environ.pop("CONTROLLED_WRITE_APPROVAL", None)
    os.environ["ALLOW_GOOGLE_SHEET_WRITE"] = "0"
    os.environ["ALLOW_PENDING_RAW_WRITE"] = "0"


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _padded(row: Sequence[object]) -> tuple[str, ...]:
    if len(row) > len(V2_COLUMNS):
        raise DataContractError("V2 observation row exceeds approved schema width.")
    return tuple(_text(value) for value in row) + ("",) * (len(V2_COLUMNS) - len(row))


def _numeric(value: object) -> str:
    try:
        return format(Decimal(str(value)), "f").rstrip("0").rstrip(".") or "0"
    except (InvalidOperation, ValueError) as exc:
        raise DataContractError("Observation price is not numeric.") from exc


def shadow_observation_id(*, source_date: str, material_id: str, source_id: str, price: object, currency: str, unit: str, market_type: str, observation_kind: str) -> str:
    payload = "\x1f".join(("C3.2_SHADOW_V1", source_date, material_id, source_id, _numeric(price), currency, unit, market_type, observation_kind))
    return "shadow-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_shadow_observation_row(key: str, quote: MarketQuote, *, evaluated_on: date, collected_at: str) -> tuple[str, ...]:
    if key not in OBSERVATION_SPECS or not quote.ok:
        raise DataContractError("Only mapped successful observations may enter V2 Shadow.")
    source_date = normalize_market_date(quote.observed_at)
    if not source_date:
        raise DataContractError("Observation source date is required.")
    material_id, source_id, market_type, default_kind = OBSERVATION_SPECS[key]
    kind = default_kind
    if default_kind == YAHOO_UNCONFIRMED:
        kind = classify_yahoo_close(source_date, evaluated_on=evaluated_on, historical_date_present=False, close_parseable=False)
    observation_id = shadow_observation_id(
        source_date=source_date, material_id=material_id, source_id=source_id, price=quote.value,
        currency=quote.currency, unit=quote.unit, market_type=market_type, observation_kind=kind,
    )
    raw = (
        observation_id, "", material_id, source_id, source_date, _numeric(quote.value), quote.currency,
        quote.unit, market_type, collected_at, "+08:00", quote.status, "PARSED", str(quote.attempts),
        "NOT_EVALUATED", "", "", "", "", "SHADOW", "C3.2-shadow-v1", collected_at,
    )
    return raw + (observation_id, collected_at, kind, "SHADOW_UNRESOLVED", kind, "", "", "C3.2_SHADOW_V1")


def plan_shadow_observation_append(candidate_rows: Iterable[Sequence[object]], existing_rows: Iterable[Sequence[object]]) -> ShadowAppendPlan:
    index = len(MARKET_RAW_COLUMNS)
    existing: dict[str, tuple[str, ...]] = {}
    for value in existing_rows:
        row = _padded(value)
        observation_id = row[index]
        if not observation_id or observation_id in existing:
            return ShadowAppendPlan((), 0, "FAIL_CLOSED", "OBSERVATION_ID_NOT_UNIQUE")
        existing[observation_id] = row
    planned: list[tuple[str, ...]] = []
    duplicate_same = 0
    for value in candidate_rows:
        row = _padded(value)
        observation_id = row[index]
        if not observation_id:
            return ShadowAppendPlan((), duplicate_same, "FAIL_CLOSED", "MISSING_OBSERVATION_ID")
        prior = existing.get(observation_id)
        if prior is not None:
            # The deterministic ID covers the source-value identity. Collection
            # timestamps and run metadata may differ on a harmless re-read.
            identity_fields = (2, 3, 4, 5, 6, 7, 8, index + 2)
            if any(prior[field] != row[field] for field in identity_fields):
                return ShadowAppendPlan((), duplicate_same, "FAIL_CLOSED", "OBSERVATION_ID_VALUE_MISMATCH")
            duplicate_same += 1
            continue
        existing[observation_id] = row
        planned.append(row)
    return ShadowAppendPlan(tuple(planned), duplicate_same, "READY")


def append_shadow_observation_plan(*, sheet_id: str, credential_file: str, plan: ShadowAppendPlan) -> ShadowAppendPlan:
    """Append only reconciled V2 rows, then verify every new immutable ID."""
    enforce_shadow_write_safety()
    if plan.status != "READY":
        return plan
    if not plan.rows:
        return plan
    import gspread

    sheet = gspread.service_account(filename=credential_file).open_by_key(sheet_id).worksheet("Market_Observation_V2")
    headers = tuple(sheet.get_all_values()[0])
    if headers != V2_COLUMNS:
        return ShadowAppendPlan((), plan.duplicate_same_count, "FAIL_CLOSED", "V2_SCHEMA_MISMATCH")
    sheet.append_rows([list(row) for row in plan.rows], value_input_option="RAW")
    rows = [_padded(row) for row in sheet.get_all_values()[1:]]
    ids = {row[len(MARKET_RAW_COLUMNS)] for row in rows}
    if not all(row[len(MARKET_RAW_COLUMNS)] in ids for row in plan.rows):
        return ShadowAppendPlan((), plan.duplicate_same_count, "FAIL_CLOSED", "READBACK_ID_MISMATCH")
    return ShadowAppendPlan(plan.rows, plan.duplicate_same_count, "APPEND_COMPLETE")
