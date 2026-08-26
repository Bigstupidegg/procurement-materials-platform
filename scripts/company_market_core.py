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
    """Return the first valid numeric value under a semantic table header.

    This is used for pages such as Cnyes where the first data row is the latest
    observation and the required value is identified by a header such as 收盤價.
    """

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
    """Validate the company sheet's structural contract before any write.

    ``None`` in expected_rows means that the cell is intentionally not enforced.
    All other cells are compared after whitespace/case normalization.  This catches
    shifted columns or renamed source/term labels before a market value can be
    written into the wrong material column.
    """

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
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def find_sheet_row(col_values: Sequence[object], target_date: date) -> int | None:
    """Return the actual 1-based Google Sheet row for target_date.

    Full ISO-style dates take precedence. For backward compatibility with the
    company's current monthly sheet, a single day-of-month value (1..31) is also
    accepted. Ambiguous day-only matches fail closed.
    """

    for row_index, raw in enumerate(col_values, start=1):
        parsed = _parse_full_date(str(raw or ""))
        if parsed == target_date:
            return row_index

    day_matches: list[int] = []
    for row_index, raw in enumerate(col_values, start=1):
        text = str(raw or "").strip()
        if re.fullmatch(r"\d{1,2}", text) and int(text) == target_date.day:
            day_matches.append(row_index)

    if len(day_matches) > 1:
        raise DataContractError(
            f"日期欄位中找到多個 {target_date.day} 號，無法判定應寫入哪一列"
        )
    return day_matches[0] if day_matches else None


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

    @property
    def ok(self) -> bool:
        return self.status == "SUCCESS" and self.value is not None

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
