from __future__ import annotations

from datetime import date, datetime
import re
from typing import Sequence


class RowSafetyError(RuntimeError):
    """Raised when the company market worksheet cannot be updated safely."""


def parse_full_date(value: object) -> date | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", text)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def resolve_safe_target_row(
    rows_a_to_l: Sequence[Sequence[object]],
    target_date: date,
    *,
    first_data_row: int = 5,
) -> tuple[int, bool]:
    """Return (row, is_new) without overwriting occupied malformed-date rows.

    rows_a_to_l must begin at first_data_row and represent columns A:L.
    Any row with data in B:L but without a full yyyy/mm/dd date in A fails closed.
    If target_date is absent, the new row is after the last actually used A:L row.
    """

    target_text = target_date.strftime("%Y/%m/%d")
    matches: list[int] = []
    last_used_row = first_data_row - 1

    for offset, raw_row in enumerate(rows_a_to_l):
        row_number = first_data_row + offset
        row = list(raw_row or [])
        padded = row + [""] * (12 - len(row))

        a_raw = str(padded[0] or "").strip()
        b_to_l_occupied = any(str(cell or "").strip() for cell in padded[1:12])
        row_used = bool(a_raw) or b_to_l_occupied
        if row_used:
            last_used_row = row_number

        parsed = parse_full_date(a_raw)
        if b_to_l_occupied and parsed is None:
            raise RowSafetyError(
                f"Row {row_number} B:L 已有行情資料，但 A{row_number}={a_raw!r} "
                "不是完整 yyyy/mm/dd 日期"
            )
        if parsed == target_date:
            matches.append(row_number)

    if len(matches) > 1:
        raise RowSafetyError(f"找到多筆日期 {target_text}: {matches}")
    if len(matches) == 1:
        return matches[0], False

    return max(first_data_row, last_used_row + 1), True
