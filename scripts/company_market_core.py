from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import math
import re
from typing import Iterable, Sequence


class DataContractError(RuntimeError):
    """Raised when a market-data source no longer matches the expected contract."""


def normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def parse_numeric(value: object) -> float:
    text = str(value or "").strip()
    text = text.replace(",", "").replace("$", "").replace("US$", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        raise DataContractError(f"無法解析數值：{value!r}")
    number = float(match.group(0))
    if not math.isfinite(number):
        raise DataContractError(f"數值不是有限數：{value!r}")
    return number


def find_header_index(headers: Sequence[object], accepted_labels: Iterable[str]) -> int:
    normalized_headers = [normalize_text(header) for header in headers]
    labels = [normalize_text(label) for label in accepted_labels]
    for index, header in enumerate(normalized_headers):
        for label in labels:
            if header == label or label in header:
                return index
    raise DataContractError(
        "找不到必要欄位：" + ", ".join(str(label) for label in accepted_labels)
    )


def extract_table_value(
    headers: Sequence[object],
    rows: Sequence[Sequence[object]],
    *,
    row_terms: Iterable[str],
    value_headers: Iterable[str],
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Extract a value by semantic header name instead of a fixed column number.

    The function deliberately fails when the required header or target row cannot be
    proven. This is preferred over silently returning the wrong market quote.
    """

    value_index = find_header_index(headers, value_headers)
    terms = [normalize_text(term) for term in row_terms]

    for row in rows:
        haystack = normalize_text(" ".join(str(cell or "") for cell in row))
        if not any(term and term in haystack for term in terms):
            continue
        if value_index >= len(row):
            raise DataContractError("目標列的欄位數不足，無法安全取得報價")
        value = parse_numeric(row[value_index])
        if minimum is not None and value < minimum:
            raise DataContractError(f"報價 {value} 低於允許下限 {minimum}")
        if maximum is not None and value > maximum:
            raise DataContractError(f"報價 {value} 高於允許上限 {maximum}")
        return value

    raise DataContractError(
        "找不到目標資料列：" + ", ".join(str(term) for term in row_terms)
    )


def extract_first_table_value(
    headers: Sequence[object],
    rows: Sequence[Sequence[object]],
    *,
    value_headers: Iterable[str],
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Return the first valid numeric value under a semantic table header."""

    value_index = find_header_index(headers, value_headers)
    for row in rows:
        if value_index >= len(row):
            continue
        try:
            value = parse_numeric(row[value_index])
        except DataContractError:
            continue
        if minimum is not None and value < minimum:
            continue
        if maximum is not None and value > maximum:
            continue
        return value
    raise DataContractError(
        "找不到可用數值欄：" + ", ".join(str(label) for label in value_headers)
    )


def validate_sheet_layout(
    actual_rows: Sequence[Sequence[object]],
    expected_rows: Sequence[Sequence[object | None]],
) -> None:
    """Validate the company sheet's structural contract before any write."""

    if len(actual_rows) < len(expected_rows):
        raise DataContractError(
            f"公司 Sheet 表頭只有 {len(actual_rows)} 列，預期至少 {len(expected_rows)} 列"
        )

    for row_index, expected_row in enumerate(expected_rows):
        actual_row = actual_rows[row_index]
        if len(actual_row) < len(expected_row):
            raise DataContractError(
                f"公司 Sheet 第 {row_index + 1} 列只有 {len(actual_row)} 欄，"
                f"預期至少 {len(expected_row)} 欄"
            )
        for col_index, expected in enumerate(expected_row):
            if expected is None:
                continue
            actual = actual_row[col_index]
            if normalize_text(actual) != normalize_text(expected):
                col_letter = chr(ord("A") + col_index)
                raise DataContractError(
                    f"公司 Sheet 版型不符：{col_letter}{row_index + 1} "
                    f"預期 {expected!r}，實際 {actual!r}"
                )


def _parse_full_date(text: str) -> date | None:
    """Parse only the authoritative company date format: yyyy/mm/dd."""

    text = text.strip()
    if not re.fullmatch(r"\d{4}/\d{2}/\d{2}", text):
        return None
    try:
        return datetime.strptime(text, "%Y/%m/%d").date()
    except ValueError:
        return None


def find_sheet_row(col_values: Sequence[object], target_date: date) -> int | None:
    """Return the 1-based row whose A-cell exactly represents target_date.

    C3.1 intentionally accepts only complete ``yyyy/mm/dd`` dates. Legacy day-only
    values such as ``26`` are rejected by omission rather than guessed, preventing
    cross-month writes to the wrong row.
    """

    matches: list[int] = []
    for row_index, raw in enumerate(col_values, start=1):
        parsed = _parse_full_date(str(raw or ""))
        if parsed == target_date:
            matches.append(row_index)

    if len(matches) > 1:
        raise DataContractError(
            f"日期欄位中找到多個 {target_date.strftime('%Y/%m/%d')}，無法判定應寫入哪一列"
        )
    return matches[0] if matches else None


@dataclass(frozen=True)
class PreviousValue:
    row: int
    value: float


def find_previous_valid_values(
    sheet_rows: Sequence[Sequence[object]],
    target_row: int,
    *,
    first_data_row: int = 5,
    value_columns: Iterable[int] = range(2, 13),
) -> dict[int, PreviousValue | None]:
    """Find the most recent valid historical value independently for each column.

    ``target_row`` and all later rows are excluded. A candidate row is accepted only
    when column A contains the authoritative ``yyyy/mm/dd`` date format and the target
    cell contains a parseable numeric value. Blank or malformed cells are skipped,
    allowing (for example) lead to fall back to row 14 while copper uses row 15.

    Returned dictionary keys are 1-based Google Sheet column numbers (B=2 ... L=12).
    """

    columns = tuple(value_columns)
    if target_row < 1:
        raise DataContractError("target_row 必須是 1-based 正整數")
    if first_data_row < 1:
        raise DataContractError("first_data_row 必須是 1-based 正整數")
    if any(column < 2 for column in columns):
        raise DataContractError("歷史價格欄位必須從 B 欄或之後開始")

    last_candidate_row = min(target_row - 1, len(sheet_rows))
    results: dict[int, PreviousValue | None] = {}

    for column in columns:
        previous: PreviousValue | None = None
        cell_index = column - 1

        for row_number in range(last_candidate_row, first_data_row - 1, -1):
            row = sheet_rows[row_number - 1]
            if not row:
                continue

            date_text = str(row[0] if len(row) >= 1 else "")
            if _parse_full_date(date_text) is None:
                continue
            if cell_index >= len(row):
                continue

            raw_value = row[cell_index]
            if str(raw_value or "").strip() == "":
                continue

            try:
                numeric_value = parse_numeric(raw_value)
            except DataContractError:
                continue

            previous = PreviousValue(row=row_number, value=numeric_value)
            break

        results[column] = previous

    return results


@dataclass(frozen=True)
class MarketQuote:
    key: str
    name: str
    source: str
    instrument: str
    term: str
    quote_type: str
    currency: str
    unit: str
    value: float | None
    fetched_at: str
    status: str
    observed_at: str | None = None
    error: str | None = None
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.status in ("SUCCESS", "RETRY_SUCCESS") and self.value is not None

    def to_dict(self) -> dict:
        return asdict(self)


def require_success(quotes: dict[str, MarketQuote], required_keys: Iterable[str]) -> None:
    failed = [
        key
        for key in required_keys
        if key not in quotes or not quotes[key].ok
    ]
    if failed:
        raise DataContractError(
            "必要市場報價失敗，停止採購作業資料寫入：" + ", ".join(failed)
        )
