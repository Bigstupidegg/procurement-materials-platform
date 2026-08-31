"""C3.2-2 Windows Pilot preparation: safe, read-only preflight.

This entry point intentionally contains no production market-sheet mutation API
calls. It uses the proven C3.1 layout, row-safety, and anomaly components, and
may append one redacted Run_Audit row for the C3.2 pilot gate.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

try:
    from c3_2_readonly_collection_poc import verify_same_date_atomic_row
    from company_market_collector import (
        COMPANY_MAIN_LAYOUT,
        MAX_DAILY_CHANGE_PCT,
        SHEET_COLUMNS,
        fetch_browser_quotes,
        fetch_yfinance_quotes,
    )
    from company_market_core import DataContractError, MarketQuote, require_success, validate_sheet_layout
    from company_market_preflight import run_anomaly_checks
    from company_market_row_safety import RowSafetyError, resolve_safe_target_row
except ModuleNotFoundError:  # imported as scripts.c3_2_pilot_dry_run
    from scripts.c3_2_readonly_collection_poc import verify_same_date_atomic_row
    from scripts.company_market_collector import (
        COMPANY_MAIN_LAYOUT,
        MAX_DAILY_CHANGE_PCT,
        SHEET_COLUMNS,
        fetch_browser_quotes,
        fetch_yfinance_quotes,
    )
    from scripts.company_market_core import DataContractError, MarketQuote, require_success, validate_sheet_layout
    from scripts.company_market_preflight import run_anomaly_checks
    from scripts.company_market_row_safety import RowSafetyError, resolve_safe_target_row


TAIPEI = ZoneInfo("Asia/Taipei")
STATUS_DIRECTORY_ENV = "COMPANY_MARKET_PILOT_STATUS_DIRECTORY"
RUN_AUDIT_COLUMNS = (
    "run_id", "started_at", "finished_at", "runner_type", "mode",
    "collector_version", "expected_count", "success_count", "collection_status",
    "date_status", "business_date", "sheet_contract_status", "duplicate_status",
    "row_safety_status", "anomaly_status", "write_enabled", "write_count",
    "readback_status", "final_status", "failure_class", "error_code", "retry_total",
    "data_persisted", "audit_version", "created_at", "notes",
)


@dataclass(frozen=True)
class PilotResult:
    execution_at: str
    collection_status: str
    date_status: str
    target_business_date: str | None
    sheet_contract_status: str
    duplicate_status: str
    row_safety_status: str
    anomaly_status: str
    final_status: str
    target_row: int | None = None

    def safe_dict(self) -> dict[str, object]:
        """Return a diagnostic payload which deliberately excludes quote values."""
        return {
            "schema_version": 1,
            "execution_timestamp": self.execution_at,
            "runner_type": "WINDOWS",
            "collection_result": self.collection_status,
            "quote_completeness": "11/11" if self.collection_status == "PASS" else "INCOMPLETE",
            "b_to_l_date_status": self.date_status,
            "target_business_date": self.target_business_date or "UNAVAILABLE",
            "sheet_contract_status": self.sheet_contract_status,
            "duplicate_status": self.duplicate_status,
            "row_safety_status": self.row_safety_status,
            "anomaly_status": self.anomaly_status,
            "mode": "DRY_RUN",
            "google_sheet_write": "DISABLED",
            "audit_persistence": "RUN_AUDIT_ONLY_REDACTED",
            "final_classification": self.final_status,
            "target_row": self.target_row,
        }


def enforce_write_disabled() -> None:
    """Make a write impossible even if a caller inherited an unsafe environment."""
    os.environ.pop("CONTROLLED_WRITE_APPROVAL", None)
    os.environ["ALLOW_GOOGLE_SHEET_WRITE"] = "0"


def append_run_audit(
    sheet_id: str,
    credential_file: str,
    row: Sequence[object],
    *,
    worksheet_name: str = "Run_Audit",
) -> None:
    """Append one redacted audit row; this function has no other Sheet mutation."""
    if len(row) != len(RUN_AUDIT_COLUMNS):
        raise DataContractError("Run_Audit schema column count mismatch.")
    if not sheet_id.strip() or not credential_file.strip():
        raise DataContractError("Run_Audit requires Sheet ID and credential file.")
    import gspread

    audit_sheet = gspread.service_account(filename=credential_file).open_by_key(sheet_id).worksheet(worksheet_name)
    audit_sheet.append_row(list(row), value_input_option="USER_ENTERED")


def build_run_audit_row(
    result: PilotResult,
    *,
    started_at: str,
    finished_at: str,
    run_id: str | None = None,
    success_count: int = 0,
    retry_total: int = 0,
    failure_class: str = "",
    error_code: str = "",
    notes: str = "",
) -> tuple[object, ...]:
    """Build the fixed Run_Audit schema without accepting quote values."""
    safe = result.safe_dict()
    final_status = str(safe["final_classification"])
    if result.date_status == "DATE_MISMATCH":
        error_code = "DATE_MISMATCH"
    return (
        run_id or str(uuid4()), started_at, finished_at, "WINDOWS", "PILOT",
        "C3.2-2", 11, success_count, result.collection_status,
        "MISMATCH" if result.date_status == "DATE_MISMATCH" else result.date_status,
        result.target_business_date or "", result.sheet_contract_status,
        result.duplicate_status, result.row_safety_status, result.anomaly_status,
        "FALSE", 0, "NOT_APPLICABLE", final_status, failure_class, error_code,
        retry_total, "FALSE", "1", finished_at, notes,
    )


def read_sheet_read_only(sheet_id: str, credential_file: str, worksheet_name: str):
    """Read only the C3.1 contract and data range; no mutation methods are used."""
    if not sheet_id.strip() or not credential_file.strip():
        raise DataContractError("Read-only Sheet inspection requires Sheet ID and credential file.")
    import gspread

    worksheet = gspread.service_account(filename=credential_file).open_by_key(sheet_id).worksheet(worksheet_name)
    return worksheet.get("A1:L4"), worksheet.get("A5:L1000")


def evaluate_pilot_dry_run(
    quotes: Mapping[str, MarketQuote],
    contract_rows: Sequence[Sequence[object]],
    data_rows: Sequence[Sequence[object]],
    *,
    execution_at: datetime | None = None,
) -> PilotResult:
    """Evaluate every Pilot preflight guard without deriving a business date."""
    timestamp = (execution_at or datetime.now(TAIPEI)).astimezone(TAIPEI).isoformat(timespec="seconds")
    try:
        require_success(dict(quotes), SHEET_COLUMNS)
        collection_status = "PASS"
    except DataContractError:
        return PilotResult(timestamp, "FAIL", "NOT_EVALUATED", None, "NOT_EVALUATED", "NOT_EVALUATED", "NOT_EVALUATED", "NOT_EVALUATED", "FAIL_CLOSED")

    target_date_text = verify_same_date_atomic_row(dict(quotes))
    if target_date_text is None:
        # A target row cannot be resolved until all eleven source dates prove one date.
        return PilotResult(timestamp, "PASS", "DATE_MISMATCH", None, "PENDING_DATE_ALIGNMENT", "PENDING_DATE_ALIGNMENT", "PENDING_DATE_ALIGNMENT", "PENDING_DATE_ALIGNMENT", "DATE_ALIGNMENT_PENDING")

    try:
        validate_sheet_layout(contract_rows, COMPANY_MAIN_LAYOUT)
    except DataContractError:
        return PilotResult(timestamp, "PASS", "DATE_MATCH", target_date_text, "FAIL", "NOT_EVALUATED", "NOT_EVALUATED", "NOT_EVALUATED", "FAIL_CLOSED")

    from datetime import date
    target_date = date.fromisoformat(target_date_text)
    try:
        target_row, is_new_row = resolve_safe_target_row(data_rows, target_date)
    except RowSafetyError:
        return PilotResult(timestamp, "PASS", "DATE_MATCH", target_date_text, "PASS", "NOT_EVALUATED", "FAIL", "NOT_EVALUATED", "FAIL_CLOSED")

    if not is_new_row:
        return PilotResult(timestamp, "PASS", "DATE_MATCH", target_date_text, "PASS", "DUPLICATE", "FAIL", "NOT_EVALUATED", "FAIL_CLOSED", target_row)

    target_values = list(data_rows[target_row - 5]) if target_row - 5 < len(data_rows) else []
    if any(str(value or "").strip() for value in (target_values + [""] * 12)[:12]):
        return PilotResult(timestamp, "PASS", "DATE_MATCH", target_date_text, "PASS", "CLEAR", "FAIL", "NOT_EVALUATED", "FAIL_CLOSED", target_row)

    checks = run_anomaly_checks(dict(quotes), SHEET_COLUMNS, list(contract_rows) + list(data_rows), target_row, MAX_DAILY_CHANGE_PCT)
    if any(not check.passed for check in checks):
        return PilotResult(timestamp, "PASS", "DATE_MATCH", target_date_text, "PASS", "CLEAR", "PASS", "FAIL", "FAIL_CLOSED", target_row)
    return PilotResult(timestamp, "PASS", "DATE_MATCH", target_date_text, "PASS", "CLEAR", "PASS", "PASS", "DRY_RUN_PASS_WRITE_DISABLED", target_row)


def persist_redacted_status(result: PilotResult, directory: Path | None = None) -> Path:
    """Persist only safe metadata locally, never in the repository or Actions artifacts."""
    root = directory or Path(os.environ.get(STATUS_DIRECTORY_ENV, Path(os.environ["LOCALAPPDATA"]) / "ProcurementMaterialsPlatform" / "pilot-status"))
    root.mkdir(parents=True, exist_ok=True)
    output = root / f"c3_2_pilot-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    output.write_text(json.dumps(result.safe_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def run_pilot_dry_run(
    *,
    sheet_id: str,
    credential_file: str,
    worksheet_name: str,
    browser_fetcher: Callable[[], dict[str, MarketQuote]] = fetch_browser_quotes,
    finance_fetcher: Callable[[], dict[str, MarketQuote]] = fetch_yfinance_quotes,
    sheet_reader: Callable[[str, str, str], tuple[Sequence[Sequence[object]], Sequence[Sequence[object]]]] = read_sheet_read_only,
    status_writer: Callable[[PilotResult], Path] = persist_redacted_status,
    audit_writer: Callable[[str, str, Sequence[object]], None] = append_run_audit,
) -> int:
    enforce_write_disabled()
    started_at = datetime.now(TAIPEI).isoformat(timespec="seconds")
    quotes: dict[str, MarketQuote] = {}
    failure_class = ""
    error_code = ""
    notes = ""
    try:
        quotes.update(browser_fetcher())
        quotes.update(finance_fetcher())
        contract_rows, data_rows = sheet_reader(sheet_id, credential_file, worksheet_name)
        result = evaluate_pilot_dry_run(quotes, contract_rows, data_rows)
    except Exception as exc:
        result = PilotResult(
            started_at, "FAIL", "NOT_CHECKED", None, "NOT_CHECKED", "NOT_CHECKED",
            "NOT_CHECKED", "NOT_CHECKED", "FAIL_CLOSED",
        )
        failure_class = "EXTERNAL_SERVICE_FAILURE"
        error_code = "COLLECTION_OR_INSPECTION_FAILURE"
        notes = type(exc).__name__
    finished_at = datetime.now(TAIPEI).isoformat(timespec="seconds")
    try:
        output = status_writer(result)
    except Exception as exc:
        output = Path("LOCAL_STATUS_WRITE_FAILED")
        notes = "; ".join(filter(None, (notes, type(exc).__name__)))
    try:
        audit_row = build_run_audit_row(
            result,
            started_at=started_at,
            finished_at=finished_at,
            success_count=sum(1 for quote in quotes.values() if quote.ok),
            retry_total=sum(max(0, quote.attempts - 1) for quote in quotes.values()),
            failure_class=failure_class,
            error_code=error_code,
            notes=notes,
        )
        audit_writer(sheet_id, credential_file, audit_row)
    except Exception as exc:
        print("run_audit_write=FAIL classification=ENVIRONMENT_FAILURE")
        print(f"run_audit_error={type(exc).__name__}")
        return 1
    print(f"C3_2_PILOT_RESULT={result.final_status} date_status={result.date_status}")
    print(f"run_audit_write=APPEND_ONLY schema_columns={len(RUN_AUDIT_COLUMNS)}")
    print(f"google_sheet_write=DISABLED audit_persistence=RUN_AUDIT_ONLY raw_values_logged=NO status_path={output}")
    return 0 if result.final_status == "DRY_RUN_PASS_WRITE_DISABLED" else 1


def main() -> int:
    return run_pilot_dry_run(
        sheet_id=os.environ.get("GOOGLE_SHEET_ID", ""),
        credential_file=os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"),
        worksheet_name=os.environ.get("GOOGLE_SHEET_WORKSHEET", "Sheet1"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
