"""C3.2 Pending Raw Persistence Pilot: append-only, non-production snapshots.

This module is deliberately isolated from the formal A:L market write path.
It can append only explicitly dated SMM/Yahoo pending snapshots to Market_Raw
when its separate, opt-in runtime gate is enabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import math
import os
from typing import Callable, Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

try:
    from c3_2_pilot_dry_run import RUN_AUDIT_COLUMNS, append_run_audit, enforce_write_disabled
    from company_market_collector import (
        fetch_smm_average_with_date, fetch_yfinance_quotes, get_driver, make_quote, normalize_market_date,
    )
    from company_market_core import DataContractError, MarketQuote
except ModuleNotFoundError:  # imported as scripts.c3_2_pending_raw_persistence
    from scripts.c3_2_pilot_dry_run import RUN_AUDIT_COLUMNS, append_run_audit, enforce_write_disabled
    from scripts.company_market_collector import (
        fetch_smm_average_with_date, fetch_yfinance_quotes, get_driver, make_quote, normalize_market_date,
    )
    from scripts.company_market_core import DataContractError, MarketQuote


TAIPEI = ZoneInfo("Asia/Taipei")
ALLOW_PENDING_RAW_WRITE = "ALLOW_PENDING_RAW_WRITE"
MARKET_RAW_COLUMNS = (
    "record_id", "business_date", "material_id", "source_id", "source_date", "price", "currency", "unit",
    "market_type", "collected_at", "collected_timezone", "source_status", "date_parse_status", "attempts",
    "anomaly_status", "previous_value", "previous_business_date", "change_pct", "run_id", "data_classification",
    "collector_version", "created_at",
)
PILOT_MATERIALS = ("CU_SMM_CATHODE", "BRENT_FUT", "SILVER_FUT", "GOLD_FUT")
QUOTE_TO_PENDING = {
    "smm_electrolytic_copper": ("CU_SMM_CATHODE", "SMM_1_COPPER_CATHODE", "SPOT"),
    "brent_yfinance": ("BRENT_FUT", "YFINANCE_BZ=F", "FUTURE"),
    "silver_yfinance": ("SILVER_FUT", "YFINANCE_SI=F", "FUTURE"),
    "gold_yfinance": ("GOLD_FUT", "YFINANCE_GC=F", "FUTURE"),
}


@dataclass(frozen=True)
class PendingPersistenceResult:
    run_id: str
    requested_count: int
    appended_count: int
    duplicate_same_count: int
    conflict_count: int
    missing_materials: tuple[str, ...]
    readback_status: str
    final_status: str
    error_code: str

    def safe_summary(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "requested_count": self.requested_count,
            "appended_count": self.appended_count,
            "duplicate_same_count": self.duplicate_same_count,
            "conflict_count": self.conflict_count,
            "missing_materials": self.missing_materials,
            "readback_status": self.readback_status,
            "final_status": self.final_status,
            "error_code": self.error_code,
            "formal_market_write": "DISABLED",
            "raw_values_logged": "NO",
        }


def enforce_pending_raw_safety() -> bool:
    """Keep the formal write path closed and return only the pending gate state."""
    pending_enabled = os.environ.get(ALLOW_PENDING_RAW_WRITE, "0").strip() == "1"
    enforce_write_disabled()
    return pending_enabled


def fetch_pending_pilot_quotes() -> dict[str, MarketQuote]:
    """Collect only the permitted four Pilot inputs; no LME collection occurs here."""
    quotes: dict[str, MarketQuote] = {}
    driver = get_driver()
    try:
        try:
            value, observed_at = fetch_smm_average_with_date(driver)
            quotes["smm_electrolytic_copper"] = make_quote(
                key="smm_electrolytic_copper", name="電解銅 Copper Cathode", source="Shanghai Metals Market",
                instrument="1# Electrolytic Copper", term="Spot", quote_type="Average", currency="CNY",
                unit="CNY/MT", value=value, observed_at=observed_at,
            )
        except Exception as exc:
            quotes["smm_electrolytic_copper"] = make_quote(
                key="smm_electrolytic_copper", name="電解銅 Copper Cathode", source="Shanghai Metals Market",
                instrument="1# Electrolytic Copper", term="Spot", quote_type="Average", currency="CNY",
                unit="CNY/MT", value=None, error=type(exc).__name__,
            )
    finally:
        driver.quit()
    quotes.update({key: value for key, value in fetch_yfinance_quotes().items() if key in QUOTE_TO_PENDING})
    return quotes


def _quote_is_valid(quote: MarketQuote) -> bool:
    return (
        quote.status in {"SUCCESS", "RETRY_SUCCESS"}
        and quote.value is not None
        and isinstance(quote.value, (int, float))
        and not isinstance(quote.value, bool)
        and math.isfinite(quote.value)
        and bool(normalize_market_date(quote.observed_at))
    )


def build_pending_rows(quotes: Mapping[str, MarketQuote], *, run_id: str, now: datetime | None = None) -> tuple[list[tuple[object, ...]], tuple[str, ...]]:
    """Convert only independently valid, explicitly dated Pilot quotes to pending rows."""
    timestamp = (now or datetime.now(TAIPEI)).astimezone(TAIPEI).isoformat(timespec="seconds")
    rows: list[tuple[object, ...]] = []
    missing: list[str] = []
    for quote_key, (material_id, source_id, market_type) in QUOTE_TO_PENDING.items():
        quote = quotes.get(quote_key)
        if quote is None or not _quote_is_valid(quote):
            missing.append(material_id)
            continue
        source_date = normalize_market_date(quote.observed_at)
        rows.append((
            f"{source_date.replace('-', '')}_{material_id}", "", material_id, source_id, source_date,
            quote.value, quote.currency, quote.unit, market_type, quote.fetched_at, "Asia/Taipei", "SUCCESS", "PASS",
            quote.attempts, "NOT_CHECKED", "", "", "", run_id, "INTERNAL_OPERATIONAL", "C3.2-2", timestamp,
        ))
    return rows, tuple(missing)


def _rows_as_dicts(headers: Sequence[object], values: Sequence[Sequence[object]]) -> list[dict[str, object]]:
    if tuple(str(value) for value in headers) != MARKET_RAW_COLUMNS:
        raise DataContractError("Market_Raw schema mismatch.")
    return [dict(zip(MARKET_RAW_COLUMNS, list(row) + [""] * (len(MARKET_RAW_COLUMNS) - len(row)))) for row in values]


def _numeric_text(value: object) -> str:
    try:
        return format(Decimal(str(value)), "f").rstrip("0").rstrip(".") or "0"
    except (InvalidOperation, ValueError):
        return str(value).strip()


def _same_pending_value(existing: Mapping[str, object], candidate: Sequence[object]) -> bool:
    return (
        str(existing["source_id"]).strip() == str(candidate[3]).strip()
        and str(existing["unit"]).strip() == str(candidate[7]).strip()
        and _numeric_text(existing["price"]) == _numeric_text(candidate[5])
    )


def classify_pending_rows(candidates: Sequence[tuple[object, ...]], existing_values: Sequence[Sequence[object]]) -> tuple[list[tuple[object, ...]], int, int]:
    """Return appendable rows, same-value duplicates, and non-overwritable conflicts."""
    existing = _rows_as_dicts(MARKET_RAW_COLUMNS, existing_values)
    appendable: list[tuple[object, ...]] = []
    duplicate_same = 0
    conflicts = 0
    for candidate in candidates:
        key = (str(candidate[4]), str(candidate[2]))
        matches = [row for row in existing if (str(row["source_date"]), str(row["material_id"])) == key]
        if not matches:
            appendable.append(candidate)
        elif any(_same_pending_value(row, candidate) for row in matches):
            duplicate_same += 1
        else:
            conflicts += 1
    return appendable, duplicate_same, conflicts


def read_market_raw(sheet_id: str, credential_file: str, *, worksheet_name: str = "Market_Raw") -> tuple[Sequence[object], Sequence[Sequence[object]]]:
    import gspread
    sheet = gspread.service_account(filename=credential_file).open_by_key(sheet_id).worksheet(worksheet_name)
    values = sheet.get_all_values()
    return values[0] if values else (), values[1:] if len(values) > 1 else ()


def append_market_raw_rows(sheet_id: str, credential_file: str, rows: Sequence[Sequence[object]], *, worksheet_name: str = "Market_Raw") -> None:
    if not rows:
        return
    if any(len(row) != len(MARKET_RAW_COLUMNS) or str(row[1]).strip() for row in rows):
        raise DataContractError("Pending row contract rejected.")
    import gspread
    sheet = gspread.service_account(filename=credential_file).open_by_key(sheet_id).worksheet(worksheet_name)
    sheet.append_rows([list(row) for row in rows], value_input_option="USER_ENTERED")


def _readback_matches(expected: Sequence[Sequence[object]], values: Sequence[Sequence[object]]) -> bool:
    by_id = {str(row[0]): row for row in values if row}
    for row in expected:
        actual = by_id.get(str(row[0]))
        if actual is None:
            return False
        padded = list(actual) + [""] * (len(MARKET_RAW_COLUMNS) - len(actual))
        for index in range(len(MARKET_RAW_COLUMNS)):
            # Sheets may render a numeric 100.0 as "100".  Compare that one
            # numeric field as a value; every other persistence field remains exact.
            if index == 5:
                if _numeric_text(padded[index]) != _numeric_text(row[index]):
                    return False
            elif str(padded[index]) != str(row[index]):
                return False
    return True


def build_pending_audit_row(result: PendingPersistenceResult, *, started_at: str, finished_at: str) -> tuple[object, ...]:
    if len(RUN_AUDIT_COLUMNS) != 26:
        raise DataContractError("Run_Audit schema mismatch.")
    final = "HUMAN_REVIEW_REQUIRED" if result.conflict_count else result.final_status
    error = "DUPLICATE_CONFLICT" if result.conflict_count else result.error_code
    notes = "PENDING_RAW_ONLY; formal_market_write=FALSE"
    return (
        result.run_id, started_at, finished_at, "WINDOWS", "PILOT", "C3.2-2", len(PILOT_MATERIALS),
        result.requested_count, "PASS" if not result.missing_materials else "PARTIAL", "PENDING", "",
        "NOT_CHECKED", "DUPLICATE_CONFLICT" if result.conflict_count else "DUPLICATE_SAME_VALUE" if result.duplicate_same_count else "NONE",
        "NOT_CHECKED", "NOT_CHECKED", "FALSE", result.appended_count, result.readback_status, final, "",
        error, 0, "TRUE" if result.appended_count else "FALSE", "1", finished_at, notes,
    )


def run_pending_raw_persistence_pilot(
    *, sheet_id: str, credential_file: str, quote_fetcher: Callable[[], Mapping[str, MarketQuote]] = fetch_pending_pilot_quotes,
    raw_reader: Callable[[str, str], tuple[Sequence[object], Sequence[Sequence[object]]]] = read_market_raw,
    raw_writer: Callable[[str, str, Sequence[Sequence[object]]], None] = append_market_raw_rows,
    audit_writer: Callable[[str, str, Sequence[object]], None] = append_run_audit,
    now: datetime | None = None,
) -> PendingPersistenceResult:
    """Run one strictly scoped Pending Raw pilot and append exactly one audit row."""
    pending_enabled = enforce_pending_raw_safety()
    started = (now or datetime.now(TAIPEI)).astimezone(TAIPEI).isoformat(timespec="seconds")
    run_id = str(uuid4())
    collection_failed = False
    try:
        quotes = quote_fetcher()
    except Exception:
        # Preserve a redacted audit trail without logging browser or provider details.
        quotes = {}
        collection_failed = True
    candidates, missing = build_pending_rows(quotes, run_id=run_id, now=now)
    headers, existing = raw_reader(sheet_id, credential_file)
    _rows_as_dicts(headers, existing)
    appendable, duplicate_same, conflicts = classify_pending_rows(candidates, existing)
    if not pending_enabled:
        appendable = []
        status, error, readback = "FAIL_CLOSED", "PENDING_RAW_WRITE_DISABLED", "NOT_APPLICABLE"
    elif collection_failed:
        appendable = []
        status, error, readback = "FAIL_CLOSED", "COLLECTION_FAILURE", "NOT_APPLICABLE"
    elif conflicts:
        status, error, readback = "HUMAN_REVIEW_REQUIRED", "DUPLICATE_CONFLICT", "NOT_APPLICABLE"
        appendable = []
    else:
        raw_writer(sheet_id, credential_file, appendable)
        _, after = raw_reader(sheet_id, credential_file)
        readback = "MATCH" if _readback_matches(appendable, after) else "MISMATCH"
        status, error = ("PENDING_RAW_ONLY", "PENDING_RAW_ONLY") if readback == "MATCH" else ("FAIL_CLOSED", "READBACK_MISMATCH")
    if "CU_SMM_CATHODE" in missing and status == "PENDING_RAW_ONLY":
        error = "SMM_SNAPSHOT_MISSING"
    result = PendingPersistenceResult(run_id, len(candidates), len(appendable) if readback == "MATCH" else 0, duplicate_same, conflicts, missing, readback, status, error)
    finished = datetime.now(TAIPEI).isoformat(timespec="seconds")
    audit_writer(sheet_id, credential_file, build_pending_audit_row(result, started_at=started, finished_at=finished))
    return result


def main() -> int:
    """Runtime entry point: never prints values, identifiers, or credentials."""
    result = run_pending_raw_persistence_pilot(
        sheet_id=os.environ.get("GOOGLE_SHEET_ID", ""),
        credential_file=os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"),
    )
    summary = result.safe_summary()
    print(
        "PENDING_RAW_RESULT=" + str(summary["final_status"])
        + " requested=" + str(summary["requested_count"])
        + " appended=" + str(summary["appended_count"])
        + " readback=" + str(summary["readback_status"])
        + " formal_market_write=DISABLED raw_values_logged=NO"
    )
    return 0 if result.final_status == "PENDING_RAW_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
