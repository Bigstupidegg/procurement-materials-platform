from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

try:
    from company_market_core import DataContractError, MarketQuote, find_previous_valid_values
except ModuleNotFoundError:  # imported as scripts.company_market_preflight in unit tests
    from scripts.company_market_core import (
        DataContractError,
        MarketQuote,
        find_previous_valid_values,
    )


SUCCESS_STATUSES = frozenset({"SUCCESS", "RETRY_SUCCESS"})


@dataclass(frozen=True)
class AnomalyCheck:
    key: str
    column: int
    previous_row: int | None
    previous_value: float | None
    change_pct: float | None
    threshold_pct: float
    passed: bool


@dataclass(frozen=True)
class PreflightReport:
    target_date: str
    target_row: int
    target_range: str
    quote_count: int
    anomaly_checks_completed: bool
    layout_contract: str
    audit_path: str
    approval_token: str


def expected_approval_token(target_date: date, target_row: int) -> str:
    return (
        f"APPROVE-C3.1-WRITE-{target_date.strftime('%Y/%m/%d')}-"
        f"A{target_row}:L{target_row}"
    )


def run_anomaly_checks(
    quotes: Mapping[str, MarketQuote],
    required_keys: Sequence[str],
    sheet_rows: Sequence[Sequence[object]],
    target_row: int,
    thresholds_pct: Mapping[str, float],
) -> tuple[AnomalyCheck, ...]:
    """Check every quote against its own most recent valid column value."""

    previous = find_previous_valid_values(sheet_rows, target_row)
    checks: list[AnomalyCheck] = []
    for offset, key in enumerate(required_keys, start=2):
        if key not in thresholds_pct:
            raise DataContractError(f"缺少異常門檻：{key}")
        quote = quotes.get(key)
        if (
            quote is None
            or quote.status not in SUCCESS_STATUSES
            or quote.value is None
        ):
            raise DataContractError(f"行情不可用，無法完成異常檢查：{key}")
        prior = previous[offset]
        threshold = thresholds_pct[key]
        if prior is None:
            checks.append(AnomalyCheck(key, offset, None, None, None, threshold, True))
            continue
        change_pct = ((quote.value - prior.value) / prior.value) * 100.0
        checks.append(
            AnomalyCheck(
                key, offset, prior.row, prior.value, round(change_pct, 4),
                threshold, abs(change_pct) <= threshold,
            )
        )
    return tuple(checks)


def validate_controlled_write_preflight(
    *,
    target_date: date,
    expected_date: date,
    target_row: int,
    is_new_row: bool,
    target_row_values: Sequence[object],
    quotes: Mapping[str, MarketQuote],
    required_keys: Sequence[str],
    anomaly_checks: Sequence[AnomalyCheck],
    layout_validated: bool,
    audit_path: Path,
) -> PreflightReport:
    blockers: list[str] = []
    if target_date != expected_date:
        blockers.append("目標日期不是本次 Asia/Taipei 作業日期")
    if target_row < 5:
        blockers.append("目標列不在資料區 A5:L")
    if not is_new_row:
        blockers.append("Controlled Write 僅允許全新列，不允許更新既有日期列")
    padded = list(target_row_values) + [""] * (12 - len(target_row_values))
    if any(str(value or "").strip() for value in padded[:12]):
        blockers.append(f"A{target_row}:L{target_row} 已有資料，禁止覆蓋")

    unavailable = [
        key for key in required_keys
        if key not in quotes
        or quotes[key].status not in SUCCESS_STATUSES
        or quotes[key].value is None
    ]
    if unavailable:
        blockers.append("11 項行情未全部可用：" + ", ".join(unavailable))

    checked_keys = {check.key for check in anomaly_checks}
    if checked_keys != set(required_keys):
        blockers.append("anomaly 檢查未涵蓋全部 11 項行情")
    failed_anomalies = [check.key for check in anomaly_checks if not check.passed]
    if failed_anomalies:
        blockers.append("anomaly 檢查失敗：" + ", ".join(failed_anomalies))
    if not layout_validated:
        blockers.append("A1:L4 contract 尚未驗證")
    if not audit_path.is_file() or audit_path.stat().st_size == 0:
        blockers.append("audit 尚未成功建立")

    if blockers:
        raise DataContractError("Controlled Write preflight 失敗：" + "; ".join(blockers))

    target_range = f"A{target_row}:L{target_row}"
    return PreflightReport(
        target_date=target_date.strftime("%Y/%m/%d"),
        target_row=target_row,
        target_range=target_range,
        quote_count=len(required_keys),
        anomaly_checks_completed=True,
        layout_contract="A1:L4",
        audit_path=str(audit_path),
        approval_token=expected_approval_token(target_date, target_row),
    )


def require_explicit_write_approval(
    report: PreflightReport,
    *,
    write_enabled: str,
    approval: str,
) -> None:
    """Fail closed unless a human supplies approval bound to this date and range."""

    if write_enabled != "1" or approval.strip() != report.approval_token:
        raise DataContractError(
            "真正 write action 尚未獲人工明確批准；需同時設定 "
            "ALLOW_GOOGLE_SHEET_WRITE=1 與完全相符的 CONTROLLED_WRITE_APPROVAL"
        )
