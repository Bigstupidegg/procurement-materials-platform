import csv
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
import yfinance as yf
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager


# ============================================================
# C3.1 v6.3 修正版
#
# 目標：
# 1. LME / SMM / yfinance 仍沿用原始資料來源
# 2. 11 項報價逐項重試與狀態分類
# 3. Fail-Closed：任一 FAIL / ANOMALY 均不寫 Google Sheet
# 4. 自動使用 Asia/Taipei 當天日期，格式 yyyy/mm/dd
# 5. 今天不存在 -> 新增下一列；已存在 -> 更新當天列
# 6. Google Sheet A 欄寫入真正日期語意（USER_ENTERED）
# 7. 合理價格範圍 + 與上一筆資料的異常波動檢查
# 8. 本機 JSON / CSV audit history，不上傳 GitHub
# 9. 預設 DRY_RUN=True
# ============================================================

VERSION = "6.3"
DRY_RUN = True

TAIPEI = ZoneInfo("Asia/Taipei")

DEFAULT_SHEET_ID = "1-YWjUm1d-8ZwuOIr-9YhbRIly2OYEfJOJ52hbz407rQ"
SHEET_ID = os.getenv("GOOGLE_SHEET_ID", DEFAULT_SHEET_ID)

SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "service_account.json",
)

WORKSHEET_NAME = os.getenv(
    "GOOGLE_SHEET_WORKSHEET",
    "大宗材料 行情統計表",
)

AUDIT_DIR = Path(
    os.getenv("COMPANY_MARKET_AUDIT_DIR", "runtime/company-market")
)

MAX_ATTEMPTS = 3
RETRY_WAIT_SECONDS = (5, 10)

MAX_DAILY_CHANGE_PCT = {
    "銅_LME_現貨": 20.0,
    "銅_LME_期貨": 20.0,
    "電解銅_SMM": 20.0,
    "鋁_LME": 20.0,
    "鉛_LME": 20.0,
    "鎳_LME": 25.0,
    "錫_LME": 25.0,
    "鋅_LME": 20.0,
    "布蘭特原油": 25.0,
    "紐約白銀": 30.0,
    "紐約黃金": 20.0,
}

VALID_RANGES = {
    "銅_LME_現貨": (1000.0, 30000.0),
    "銅_LME_期貨": (1000.0, 30000.0),
    "電解銅_SMM": (50000.0, 200000.0),
    "鋁_LME": (500.0, 10000.0),
    "鉛_LME": (500.0, 10000.0),
    "鎳_LME": (5000.0, 100000.0),
    "錫_LME": (10000.0, 100000.0),
    "鋅_LME": (500.0, 10000.0),
    "布蘭特原油": (20.0, 250.0),
    "紐約白銀": (500.0, 20000.0),
    "紐約黃金": (500.0, 10000.0),
}

EXPECTED_LAYOUT = [
    [
        "日期", "銅 COPPER", "銅 COPPER", "電解銅 Copper Cathode",
        "鋁 ALUMINIUM", "鉛 LEAD", "鎳 NICKEL", "錫 TIN",
        "鋅 ZINC", "油", "銀", "黃金"
    ],
    [
        "", "USD / TONNE", "USD / TONNE", "CNY / TONNE",
        "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / TONNE",
        "USD / TONNE", "USD / BBL", "CENT / OUNCE", "USD / OUNCE"
    ],
    [
        "資料來源", "LME OFFER", "LME OFFER", "SMM",
        "LME", "LME", "LME", "LME", "LME",
        "yfinance BZ=F", "yfinance SI=F", "yfinance GC=F"
    ],
    [
        "", "現貨", "期貨(3月)", "現貨", "現貨", "現貨",
        "現貨", "現貨", "現貨", "期貨", "期貨", "期貨"
    ],
]

REQUIRED_KEYS = [
    "銅_LME_現貨", "銅_LME_期貨", "電解銅_SMM", "鋁_LME", "鉛_LME",
    "鎳_LME", "錫_LME", "鋅_LME", "布蘭特原油", "紐約白銀", "紐約黃金",
]

SHEET_COLUMNS = {
    "銅_LME_現貨": 2,
    "銅_LME_期貨": 3,
    "電解銅_SMM": 4,
    "鋁_LME": 5,
    "鉛_LME": 6,
    "鎳_LME": 7,
    "錫_LME": 8,
    "鋅_LME": 9,
    "布蘭特原油": 10,
    "紐約白銀": 11,
    "紐約黃金": 12,
}


@dataclass
class QuoteResult:
    key: str
    value: float | None
    status: str
    source: str
    instrument: str
    observed_at: str | None
    attempts: int
    error: str | None = None
    previous_value: float | None = None
    change_pct: float | None = None

    @property
    def usable(self):
        return self.status in ("SUCCESS", "RETRY_SUCCESS")


def now_taipei():
    return datetime.now(TAIPEI)


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def parse_number(text):
    cleaned = str(text or "").strip()
    cleaned = cleaned.replace(",", "").replace("$", "").replace("US$", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
    if not match:
        raise ValueError(f"無法解析數值：{text!r}")
    return float(match.group(0))


def validate_value_range(key, value):
    minimum, maximum = VALID_RANGES[key]
    if not (minimum <= value <= maximum):
        raise ValueError(
            f"{key} 數值 {value} 超出資料品質合理範圍 {minimum} ~ {maximum}"
        )


def get_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def get_table_matrix(table):
    rows = table.find_elements(By.TAG_NAME, "tr")
    matrix = []
    for row in rows:
        cells = row.find_elements(By.XPATH, ".//th | .//td")
        values = [cell.text.strip() for cell in cells]
        if any(values):
            matrix.append(values)
    return matrix


def find_header_row_and_index(matrix, accepted_headers):
    accepted = [normalize_text(x) for x in accepted_headers]
    for row_index, row in enumerate(matrix):
        for col_index, cell in enumerate(row):
            normalized = normalize_text(cell)
            if any(normalized == header or header in normalized for header in accepted):
                return row_index, col_index
    raise RuntimeError("找不到必要欄位：" + ", ".join(accepted_headers))


def retry_call(label, callable_fn):
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            value, observed_at = callable_fn()
            return value, observed_at, attempt
        except Exception as exc:
            last_error = exc
            print(f"  [RETRY] {label} attempt {attempt}/{MAX_ATTEMPTS}: {exc}")
            if attempt < MAX_ATTEMPTS:
                wait_seconds = RETRY_WAIT_SECONDS[min(attempt - 1, len(RETRY_WAIT_SECONDS) - 1)]
                time.sleep(wait_seconds)
    raise RuntimeError(f"{label} 重試 {MAX_ATTEMPTS} 次仍失敗：{last_error}")


def _fetch_lme_once(driver, url, term_type):
    driver.get(url)
    time.sleep(5)
    tables = driver.find_elements(By.TAG_NAME, "table")
    if not tables:
        raise RuntimeError("頁面找不到任何 table")
    failures = []
    for table in tables:
        try:
            matrix = get_table_matrix(table)
            if not matrix:
                continue
            header_row_index, offer_index = find_header_row_and_index(matrix, ["Offer"])
            for row in matrix[header_row_index + 1:]:
                row_text = normalize_text(" ".join(row))
                if normalize_text(term_type) not in row_text:
                    continue
                if offer_index >= len(row):
                    raise RuntimeError(f"{term_type} 資料列欄位不足，無法安全取得 OFFER")
                value = parse_number(row[offer_index])
                return value, now_taipei().isoformat(timespec="seconds")
            failures.append(f"table 有 OFFER，但找不到 {term_type} 資料列")
        except Exception as exc:
            failures.append(str(exc))
    raise RuntimeError(f"無法安全辨識 {term_type} OFFER；" + " | ".join(failures[-3:]))


def fetch_lme_result(driver, key, url, term_type):
    try:
        value, observed_at, attempts = retry_call(
            f"LME {key}", lambda: _fetch_lme_once(driver, url, term_type)
        )
        validate_value_range(key, value)
        status = "SUCCESS" if attempts == 1 else "RETRY_SUCCESS"
        return QuoteResult(key, value, status, "LME", term_type, observed_at, attempts)
    except Exception as exc:
        return QuoteResult(key, None, "FAIL", "LME", term_type, None, MAX_ATTEMPTS, str(exc))


def _fetch_smm_once(driver):
    driver.get("https://www.smm.com.cn/price")
    time.sleep(5)
    tables = driver.find_elements(By.TAG_NAME, "table")
    if not tables:
        raise RuntimeError("SMM 頁面找不到任何 table")
    failures = []
    for table in tables:
        try:
            matrix = get_table_matrix(table)
            if not matrix:
                continue
            header_row_index, avg_index = find_header_row_and_index(
                matrix, ["均价", "平均价", "Average", "Avg"]
            )
            for row in matrix[header_row_index + 1:]:
                row_text = normalize_text(" ".join(row))
                if not any(normalize_text(keyword) in row_text for keyword in ["1#电解铜", "1#電解銅", "电解铜", "電解銅"]):
                    continue
                if avg_index >= len(row):
                    raise RuntimeError("SMM 電解銅資料列欄位不足，無法取得均價")
                value = parse_number(row[avg_index])
                return value, now_taipei().isoformat(timespec="seconds")
            failures.append("table 有均價欄，但找不到電解銅資料列")
        except Exception as exc:
            failures.append(str(exc))
    raise RuntimeError("無法安全辨識 SMM 電解銅均價；" + " | ".join(failures[-3:]))


def fetch_smm_result(driver):
    key = "電解銅_SMM"
    try:
        value, observed_at, attempts = retry_call("SMM 電解銅均價", lambda: _fetch_smm_once(driver))
        validate_value_range(key, value)
        status = "SUCCESS" if attempts == 1 else "RETRY_SUCCESS"
        return QuoteResult(key, value, status, "SMM", "1#電解銅均價", observed_at, attempts)
    except Exception as exc:
        return QuoteResult(key, None, "FAIL", "SMM", "1#電解銅均價", None, MAX_ATTEMPTS, str(exc))


YFINANCE_SPECS = {
    "布蘭特原油": ("BZ=F", 1.0),
    "紐約白銀": ("SI=F", 100.0),
    "紐約黃金": ("GC=F", 1.0),
}


def _fetch_yfinance_once(symbol, multiplier):
    history = yf.Ticker(symbol).history(period="5d")
    if history.empty:
        raise RuntimeError(f"{symbol} 沒有回傳近期資料")
    raw_value = float(history["Close"].iloc[-1])
    value = round(raw_value * multiplier, 2)
    return value, str(history.index[-1])


def fetch_yfinance_result(key, symbol, multiplier):
    try:
        value, observed_at, attempts = retry_call(
            f"yfinance {symbol}", lambda: _fetch_yfinance_once(symbol, multiplier)
        )
        validate_value_range(key, value)
        status = "SUCCESS" if attempts == 1 else "RETRY_SUCCESS"
        return QuoteResult(key, value, status, "yfinance", symbol, observed_at, attempts)
    except Exception as exc:
        return QuoteResult(key, None, "FAIL", "yfinance", symbol, None, MAX_ATTEMPTS, str(exc))


def validate_sheet_layout(sheet):
    actual = sheet.get("A1:L4")
    if len(actual) < 4:
        raise RuntimeError("Google Sheet A1:L4 表頭不完整")
    for r in range(4):
        actual_row = actual[r]
        expected_row = EXPECTED_LAYOUT[r]
        if len(actual_row) < 12:
            actual_row = actual_row + [""] * (12 - len(actual_row))
        for c in range(12):
            expected = expected_row[c]
            actual_value = actual_row[c]
            if normalize_text(actual_value) != normalize_text(expected):
                col = chr(ord("A") + c)
                raise RuntimeError(
                    f"Google Sheet 版型不符：{col}{r+1} 預期 {expected!r}，實際 {actual_value!r}"
                )


def parse_sheet_date(raw):
    text = str(raw or "").strip()
    match = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", text)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def get_formatted_col_a(sheet):
    try:
        values = sheet.get("A1:A1000", value_render_option="FORMATTED_VALUE")
        return [row[0] if row else "" for row in values]
    except TypeError:
        return sheet.col_values(1)


def resolve_target_row(sheet, target_date):
    target_text = target_date.strftime("%Y/%m/%d")
    col_a = get_formatted_col_a(sheet)
    matches = []
    for row_index, raw in enumerate(col_a, start=1):
        parsed = parse_sheet_date(raw)
        if parsed == target_date:
            matches.append(row_index)
    if len(matches) > 1:
        raise RuntimeError(
            f"Google Sheet 找到多筆今天日期 {target_text}：{matches}；為避免覆寫錯誤資料，停止更新"
        )
    if len(matches) == 1:
        print(f"  [OK] 今天日期已存在：{target_text} -> Row {matches[0]}")
        return matches[0], False
    valid_date_rows = []
    for row_index, raw in enumerate(col_a, start=1):
        if row_index < 5:
            continue
        if parse_sheet_date(raw) is not None:
            valid_date_rows.append(row_index)
    last_data_row = max(valid_date_rows) if valid_date_rows else 4
    target_row = max(5, last_data_row + 1)
    print(f"  [OK] 今天日期尚未存在，自動新增：{target_text} -> Row {target_row}")
    return target_row, True


def get_previous_market_row(sheet, target_row):
    if target_row <= 5:
        return None, None
    values = sheet.get(f"A5:L{target_row - 1}")
    for offset in range(len(values) - 1, -1, -1):
        row = values[offset]
        row_number = 5 + offset
        if not row:
            continue
        date_value = row[0] if len(row) > 0 else ""
        if parse_sheet_date(date_value) is None:
            continue
        padded = row + [""] * (12 - len(row))
        return row_number, padded[1:12]
    return None, None


def apply_anomaly_checks(results, sheet, target_row):
    previous_row, previous_values = get_previous_market_row(sheet, target_row)
    if previous_row is None:
        print("  [INFO] 無上一筆行情資料，略過日變動異常檢查。")
        return
    print(f"  [INFO] 異常波動比較基準：Row {previous_row}")
    for key in REQUIRED_KEYS:
        result = results[key]
        if not result.usable:
            continue
        col_index = SHEET_COLUMNS[key] - 2
        previous_raw = previous_values[col_index]
        try:
            previous_value = float(str(previous_raw).replace(",", "").strip())
        except Exception:
            continue
        if previous_value <= 0:
            continue
        change_pct = ((result.value - previous_value) / previous_value) * 100.0
        result.previous_value = previous_value
        result.change_pct = round(change_pct, 4)
        threshold = MAX_DAILY_CHANGE_PCT[key]
        if abs(change_pct) > threshold:
            result.status = "ANOMALY"
            result.error = (
                f"與上一筆 {previous_value} 相比變動 {change_pct:+.2f}% ，超過 ±{threshold:.1f}% 防呆門檻"
            )


def write_sheet_row(sheet, target_row, target_date, results):
    target_text = target_date.strftime("%Y/%m/%d")
    row_data = [
        target_text,
        results["銅_LME_現貨"].value,
        results["銅_LME_期貨"].value,
        results["電解銅_SMM"].value,
        results["鋁_LME"].value,
        results["鉛_LME"].value,
        results["鎳_LME"].value,
        results["錫_LME"].value,
        results["鋅_LME"].value,
        results["布蘭特原油"].value,
        results["紐約白銀"].value,
        results["紐約黃金"].value,
    ]
    range_name = f"A{target_row}:L{target_row}"
    print("")
    print("=" * 78)
    print(f"目標日期：{target_text}（Asia/Taipei 自動取得）")
    print(f"目標範圍：{range_name}")
    print("準備寫入資料：")
    print(row_data)
    print("=" * 78)
    if DRY_RUN:
        print("LIVE DRY RUN：所有必要檢查通過；本次不修改 Google Sheet。")
        return
    sheet.update(values=[row_data], range_name=range_name, value_input_option="USER_ENTERED")
    try:
        sheet.format(f"A{target_row}", {"numberFormat": {"type": "DATE", "pattern": "yyyy/mm/dd"}})
    except Exception as exc:
        print(f"  [WARN] A欄日期格式設定失敗，但資料已寫入：{exc}")
    print(f"Google Sheet 更新完成：{range_name}")


def save_audit(results, target_date, target_row, is_new_row):
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = now_taipei()
    stamp = generated_at.strftime("%Y%m%d_%H%M%S")
    payload = {
        "version": VERSION,
        "generatedAt": generated_at.isoformat(timespec="seconds"),
        "targetDate": target_date.strftime("%Y/%m/%d"),
        "targetRow": target_row,
        "isNewRow": is_new_row,
        "dryRun": DRY_RUN,
        "results": {key: asdict(results[key]) for key in REQUIRED_KEYS},
    }
    json_path = AUDIT_DIR / f"market_audit_{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = AUDIT_DIR / "market_audit_history.csv"
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        if write_header:
            writer.writerow([
                "generated_at", "target_date", "key", "value", "status", "source",
                "instrument", "observed_at", "attempts", "previous_value", "change_pct", "error",
            ])
        for key in REQUIRED_KEYS:
            result = results[key]
            writer.writerow([
                generated_at.isoformat(timespec="seconds"), target_date.strftime("%Y/%m/%d"), key,
                result.value, result.status, result.source, result.instrument, result.observed_at,
                result.attempts, result.previous_value, result.change_pct, result.error,
            ])
    print(f"稽核 JSON：{json_path}")
    print(f"稽核歷史 CSV：{csv_path}")


def print_summary(results):
    print("")
    print("今日報價抓取摘要：")
    for key in REQUIRED_KEYS:
        result = results[key]
        value_text = f"{result.value}" if result.value is not None else "-"
        retry_text = f" attempts={result.attempts}" if result.attempts > 1 else ""
        change_text = f" change={result.change_pct:+.2f}%" if result.change_pct is not None else ""
        error_text = f" | {result.error}" if result.error else ""
        print(f"  [{result.status}] {key}: {value_text}{retry_text}{change_text}{error_text}")


def require_all_usable(results):
    blockers = [
        key for key in REQUIRED_KEYS
        if results[key].status not in ("SUCCESS", "RETRY_SUCCESS")
    ]
    if blockers:
        details = "; ".join(f"{key}={results[key].status}" for key in blockers)
        raise RuntimeError("必要行情未全部通過，Google Sheet 不更新：" + details)


if __name__ == "__main__":
    print("=" * 78)
    print(f"C3.1 大宗材料行情抓取修正版 v{VERSION}")
    print(f"DRY_RUN = {DRY_RUN}")
    print(f"Taipei time = {now_taipei().isoformat(timespec='seconds')}")
    print("=" * 78)

    results = {}

    print("正在透過 yfinance API 取得油 / 銀 / 金...")
    for key, (symbol, multiplier) in YFINANCE_SPECS.items():
        results[key] = fetch_yfinance_result(key, symbol, multiplier)

    print("正在啟動 Selenium 抓取 LME / SMM...")
    driver = get_driver()
    try:
        lme_specs = [
            ("銅_LME_現貨", "https://www.lme.com/metals/non-ferrous/lme-copper#Trading+summary", "Cash"),
            ("銅_LME_期貨", "https://www.lme.com/metals/non-ferrous/lme-copper#Trading+summary", "3-month"),
            ("鋁_LME", "https://www.lme.com/metals/non-ferrous/lme-aluminium#Trading+summary", "Cash"),
            ("鉛_LME", "https://www.lme.com/metals/non-ferrous/lme-lead#Summary", "Cash"),
            ("鎳_LME", "https://www.lme.com/metals/non-ferrous/lme-nickel#Summary", "Cash"),
            ("錫_LME", "https://www.lme.com/metals/non-ferrous/lme-tin#Summary", "Cash"),
            ("鋅_LME", "https://www.lme.com/metals/non-ferrous/lme-zinc#Summary", "Cash"),
        ]
        for key, url, term_type in lme_specs:
            results[key] = fetch_lme_result(driver, key, url, term_type)
        results["電解銅_SMM"] = fetch_smm_result(driver)
    finally:
        driver.quit()

    print("正在連線至 Google Sheets...")
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)
    book = gc.open_by_key(SHEET_ID)
    try:
        sheet = book.worksheet(WORKSHEET_NAME)
    except Exception as exc:
        raise RuntimeError(
            f"找不到指定工作表 {WORKSHEET_NAME!r}；為避免寫錯工作表，不自動退回 sheet1。"
        ) from exc

    validate_sheet_layout(sheet)
    target_date = now_taipei().date()
    target_row, is_new_row = resolve_target_row(sheet, target_date)
    apply_anomaly_checks(results, sheet, target_row)
    print_summary(results)
    save_audit(results, target_date, target_row, is_new_row)
    require_all_usable(results)
    write_sheet_row(sheet, target_row, target_date, results)
