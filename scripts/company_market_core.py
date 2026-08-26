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
