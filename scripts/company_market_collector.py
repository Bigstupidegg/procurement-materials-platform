from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from company_market_core import (
    DataContractError,
    MarketQuote,
    extract_first_table_value,
    extract_table_value,
    find_sheet_row,
    require_success,
    validate_sheet_layout,
)


TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_PATH = ROOT / "runtime" / "company-market" / "latest.json"

MAIN_SHEET_DEFAULT = "大宗材料 行情統計表"
SOURCE_SHEET_DEFAULT = "行情統計表資料來源"

LME_URLS = {
    "copper_lme_cash": ("銅 COPPER", "https://www.lme.com/metals/non-ferrous/lme-copper#Trading+summary", "Cash"),
    "copper_lme_3m": ("銅 COPPER", "https://www.lme.com/metals/non-ferrous/lme-copper#Trading+summary", "3-month"),
    "aluminium_lme_cash": ("鋁 ALUMINIUM", "https://www.lme.com/metals/non-ferrous/lme-aluminium#Trading+summary", "Cash"),
    "lead_lme_cash": ("鉛 LEAD", "https://www.lme.com/metals/non-ferrous/lme-lead#Summary", "Cash"),
    "nickel_lme_cash": ("鎳 NICKEL", "https://www.lme.com/metals/non-ferrous/lme-nickel#Summary", "Cash"),
    "tin_lme_cash": ("錫 TIN", "https://www.lme.com/metals/non-ferrous/lme-tin#Summary", "Cash"),
    "zinc_lme_cash": ("鋅 ZINC", "https://www.lme.com/metals/non-ferrous/lme-zinc#Summary", "Cash"),
}

SMM_URL = "https://www.smm.com.cn/price"

CNYES_URLS = {
    "brent_cnyes": (
        "油",
        "https://www.cnyes.com/futures/html5chart/IBCON.html",
        "連續月倫敦布蘭特",
        1.0,
        "USD",
        "USD/bbl",
        10.0,
    ),
    "silver_cnyes": (
        "銀",
        "https://www.cnyes.com/futures/html5chart/sicon.html",
        "連續月紐約白銀",
        100.0,
        "US cents",
        "US cents/troy oz",
        1.0,
    ),
    "gold_cnyes": (
        "黃金",
        "https://www.cnyes.com/futures/html5chart/gccon.html",
        "連續月紐約黃金",
        1.0,
        "USD",
        "USD/troy oz",
        100.0,
    ),
}

SHEET_COLUMNS = (
    "copper_lme_cash",
    "copper_lme_3m",
    "smm_electrolytic_copper",
    "aluminium_lme_cash",
    "lead_lme_cash",
    "nickel_lme_cash",
    "tin_lme_cash",
    "zinc_lme_cash",
    "brent_cnyes",
    "silver_cnyes",
    "gold_cnyes",
)

# Actual A1:L4 contract observed in the company's 2026-08 sheet.
# D2 and J2 are intentionally not enforced here because the workbook currently
# contains known unit-label inconsistencies documented in C3.1 acceptance notes.
COMPANY_MAIN_LAYOUT = (
    (
        "日期", "銅 COPPER", "銅 COPPER", "電解銅 Copper Cathode",
        "鋁 ALUMINIUM", "鉛 LEAD", "鎳 NICKEL", "錫 TIN", "鋅 ZINC",
        "油", "銀", "黃金",
    ),
    (
        None, "USD / TONNE", "USD / TONNE", None,
        "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / TONNE",
        "USD / TONNE", None, "CENT / OUNCE", "USD / OUNCE",
    ),
    (
        "資料來源", "LME OFFER", "LME OFFER", "SMM", "LME", "LME", "LME",
        "LME", "LME", "鉅亨 倫敦布蘭特", "鉅亨 紐約白銀", "鉅亨 紐約黃金",
    ),
    (
        None, "現貨", "期貨(3月)", "現貨", "現貨", "現貨", "現貨", "現貨",
        "現貨", "現貨", "現貨", "現貨",
    ),
)

COMPANY_SOURCE_REGISTRY = (
    ("材料", "單位", "來源", "網頁關鍵字", "網址"),
    ("銅 COPPER", "USD / TONNE", "LME OFFER", "Cash", LME_URLS["copper_lme_cash"][1]),
    ("銅 COPPER", "USD / TONNE", "LME OFFER", "3-month", LME_URLS["copper_lme_3m"][1]),
    ("電解銅 Copper Cathode", "USD / TONNE", "SMM", "Cash", SMM_URL),
    ("鋁 ALUMINIUM", "USD / TONNE", "LME", "Cash", LME_URLS["aluminium_lme_cash"][1]),
    ("鉛 LEAD", "USD / TONNE", "LME", "Cash", LME_URLS["lead_lme_cash"][1]),
    ("鎳 NICKEL", "USD / TONNE", "LME", "Cash", LME_URLS["nickel_lme_cash"][1]),
    ("錫 TIN", "USD / TONNE", "LME", "Cash", LME_URLS["tin_lme_cash"][1]),
    ("鋅 ZINC", "USD / TONNE", "LME", "Cash", LME_URLS["zinc_lme_cash"][1]),
    ("油", "USD / DRUM", "鉅亨 倫敦布蘭特-收盤價", "收盤價", CNYES_URLS["brent_cnyes"][1]),
    ("銀", "CENT / OUNCE", "鉅亨 紐約白銀-收盤價", "收盤價", CNYES_URLS["silver_cnyes"][1]),
    ("黃金", "USD / OUNCE", "鉅亨 紐約黃金-收盤價", "收盤價", CNYES_URLS["gold_cnyes"][1]),
)


def now_taipei() -> datetime:
    return datetime.now(TAIPEI)


def iso_now() -> str:
    return now_taipei().isoformat(timespec="seconds")


def get_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/152 Safari/537.36"
    )
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )


def table_to_matrix(table) -> tuple[list[str], list[list[str]]]:
    from selenium.webdriver.common.by import By

    tr_elements = table.find_elements(By.TAG_NAME, "tr")
    if not tr_elements:
        raise DataContractError("HTML table 沒有任何資料列")

    headers: list[str] = []
    header_row_index = 0
    for index, row in enumerate(tr_elements):
        ths = row.find_elements(By.TAG_NAME, "th")
        if ths:
            headers = [th.text.strip() for th in ths]
            header_row_index = index
            break

    if not headers:
        first_cells = tr_elements[0].find_elements(By.XPATH, ".//th | .//td")
        headers = [cell.text.strip() for cell in first_cells]
        header_row_index = 0

    rows: list[list[str]] = []
    for row in tr_elements[header_row_index + 1 :]:
        cells = row.find_elements(By.XPATH, ".//td | .//th")
        values = [cell.text.strip() for cell in cells]
        if any(values):
            rows.append(values)
    return headers, rows


def fetch_lme_offer(driver, url: str, term_type: str) -> float:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    driver.get(url)
    WebDriverWait(driver, 20).until(lambda d: d.find_elements(By.TAG_NAME, "table"))

    failures: list[str] = []
    for table in driver.find_elements(By.TAG_NAME, "table"):
        try:
            headers, rows = table_to_matrix(table)
            return extract_table_value(
                headers,
                rows,
                row_terms=(term_type,),
                value_headers=("offer",),
                minimum=100.0,
            )
        except DataContractError as exc:
            failures.append(str(exc))

    raise DataContractError(
        f"LME 頁面無法證明 {term_type} OFFER 欄位；停止使用此報價。"
        + (" | " + failures[-1] if failures else "")
    )


def fetch_smm_average(driver) -> float:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    driver.get(SMM_URL)
    WebDriverWait(driver, 20).until(lambda d: d.find_elements(By.TAG_NAME, "table"))

    failures: list[str] = []
    for table in driver.find_elements(By.TAG_NAME, "table"):
        try:
            headers, rows = table_to_matrix(table)
            return extract_table_value(
                headers,
                rows,
                row_terms=("1#电解铜", "1#電解銅", "电解铜", "電解銅"),
                value_headers=("均价", "平均价", "average", "avg"),
                minimum=50000.0,
            )
        except DataContractError as exc:
            failures.append(str(exc))

    raise DataContractError(
        "SMM 頁面無法證明電解銅『均價』欄位；停止使用此報價。"
        + (" | " + failures[-1] if failures else "")
    )


def fetch_cnyes_close(driver, url: str, *, minimum: float) -> float:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    driver.get(url)
    WebDriverWait(driver, 20).until(lambda d: d.find_elements(By.TAG_NAME, "table"))

    failures: list[str] = []
    for table in driver.find_elements(By.TAG_NAME, "table"):
        try:
            headers, rows = table_to_matrix(table)
            return extract_first_table_value(
                headers,
                rows,
                value_headers=("收盤價", "close"),
                minimum=minimum,
            )
        except DataContractError as exc:
            failures.append(str(exc))

    raise DataContractError(
        "鉅亨頁面無法證明『收盤價』欄位；停止使用此報價。"
        + (" | " + failures[-1] if failures else "")
    )


def make_quote(
    *,
    key: str,
    name: str,
    source: str,
    instrument: str,
    term: str,
    quote_type: str,
    currency: str,
    unit: str,
    value: float | None,
    observed_at: str | None = None,
    error: str | None = None,
) -> MarketQuote:
    return MarketQuote(
        key=key,
        name=name,
        source=source,
        instrument=instrument,
        term=term,
        quote_type=quote_type,
        currency=currency,
        unit=unit,
        value=value,
        fetched_at=iso_now(),
        observed_at=observed_at,
        status="SUCCESS" if value is not None and error is None else "ERROR",
        error=error,
    )


def fetch_browser_quotes() -> dict[str, MarketQuote]:
    quotes: dict[str, MarketQuote] = {}
    driver = get_driver()
    try:
        for key, (name, url, term) in LME_URLS.items():
            try:
                value = fetch_lme_offer(driver, url, term)
                quotes[key] = make_quote(
                    key=key,
                    name=name,
                    source="London Metal Exchange",
                    instrument=name,
                    term=term,
                    quote_type="OFFER",
                    currency="USD",
                    unit="USD/MT",
                    value=value,
                )
            except Exception as exc:
                quotes[key] = make_quote(
                    key=key,
                    name=name,
                    source="London Metal Exchange",
                    instrument=name,
                    term=term,
                    quote_type="OFFER",
                    currency="USD",
                    unit="USD/MT",
                    value=None,
                    error=str(exc),
                )

        try:
            value = fetch_smm_average(driver)
            quotes["smm_electrolytic_copper"] = make_quote(
                key="smm_electrolytic_copper",
                name="電解銅 Copper Cathode",
                source="Shanghai Metals Market",
                instrument="1# Electrolytic Copper",
                term="Spot",
                quote_type="Average",
                currency="CNY",
                unit="CNY/MT",
                value=value,
            )
        except Exception as exc:
            quotes["smm_electrolytic_copper"] = make_quote(
                key="smm_electrolytic_copper",
                name="電解銅 Copper Cathode",
                source="Shanghai Metals Market",
                instrument="1# Electrolytic Copper",
                term="Spot",
                quote_type="Average",
                currency="CNY",
                unit="CNY/MT",
                value=None,
                error=str(exc),
            )

        for key, (name, url, instrument, multiplier, currency, unit, minimum) in CNYES_URLS.items():
            try:
                raw_value = fetch_cnyes_close(driver, url, minimum=minimum)
                value = round(raw_value * multiplier, 2)
                quotes[key] = make_quote(
                    key=key,
                    name=name,
                    source="Anue Cnyes",
                    instrument=instrument,
                    term="Continuous Month",
                    quote_type="Close",
                    currency=currency,
                    unit=unit,
                    value=value,
                )
            except Exception as exc:
                quotes[key] = make_quote(
                    key=key,
                    name=name,
                    source="Anue Cnyes",
                    instrument=instrument,
                    term="Continuous Month",
                    quote_type="Close",
                    currency=currency,
                    unit=unit,
                    value=None,
                    error=str(exc),
                )
    finally:
        driver.quit()
    return quotes


def write_audit_snapshot(quotes: dict[str, MarketQuote]) -> Path:
    output = Path(os.getenv("COMPANY_MARKET_AUDIT_PATH", str(DEFAULT_AUDIT_PATH)))
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": 2,
        "classification": "INTERNAL_OPERATIONAL",
        "generatedAt": iso_now(),
        "decisionPolicy": {
            "copperDailyPrimary": "LME_CASH_OFFER",
            "automaticBuyDecision": False,
            "publicGitHubStorage": False,
        },
        "quotes": {key: quote.to_dict() for key, quote in quotes.items()},
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def validate_company_workbook(book):
    main_name = os.getenv("GOOGLE_SHEET_WORKSHEET", MAIN_SHEET_DEFAULT).strip()
    source_name = os.getenv("GOOGLE_SOURCE_WORKSHEET", SOURCE_SHEET_DEFAULT).strip()
    main_sheet = book.worksheet(main_name)
    source_sheet = book.worksheet(source_name)

    validate_sheet_layout(main_sheet.get("A1:L4"), COMPANY_MAIN_LAYOUT)
    validate_sheet_layout(source_sheet.get("A1:E12"), COMPANY_SOURCE_REGISTRY)

    # Known metadata issues in the supplied workbook. These are not used to select
    # columns, but they must remain visible until the company sheet is corrected.
    units = main_sheet.get("A2:L2")[0]
    warnings: list[str] = []
    if len(units) >= 4 and str(units[3]).strip().upper() == "USD / TONNE":
        warnings.append(
            "D2 電解銅標示為 USD / TONNE，但 SMM collector 保留原始 CNY/MT；"
            "建議公司表修正為 CNY / TONNE，或另定義正式匯率換算規則。"
        )
    if len(units) >= 10 and str(units[9]).strip().upper() == "USD / DRUM":
        warnings.append(
            "J2 原油標示為 USD / DRUM；鉅亨布蘭特期貨實務上建議標示 USD / BBL。"
        )
    return main_sheet, warnings


def update_google_sheet(quotes: dict[str, MarketQuote]) -> None:
    import gspread

    # A full-row write must never blank out existing cells because one source failed.
    require_success(quotes, SHEET_COLUMNS)

    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    if not sheet_id:
        raise DataContractError("未設定 GOOGLE_SHEET_ID；不允許猜測或硬編碼公司 Sheet")

    credentials = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    gc = gspread.service_account(filename=credentials)
    book = gc.open_by_key(sheet_id)
    sheet, warnings = validate_company_workbook(book)
    for warning in warnings:
        print(f"WARNING: {warning}")

    target_date = now_taipei().date()
    target_row = find_sheet_row(sheet.col_values(1), target_date)
    if target_row is None:
        raise DataContractError(
            f"Google Sheet 找不到 {target_date.isoformat()} / {target_date.day} 號資料列；"
            "公司月表只允許寫入已存在的交易日列，已停止更新。"
        )

    date_value = str(target_date.day)
    row_data = [date_value] + [quotes[key].value for key in SHEET_COLUMNS]
    range_name = f"A{target_row}:L{target_row}"

    if os.getenv("ALLOW_GOOGLE_SHEET_WRITE", "0") != "1":
        print(f"DRY RUN：已驗證目標 {range_name}，但 ALLOW_GOOGLE_SHEET_WRITE != 1，不寫入。")
        print("DRY RUN DATA:", row_data)
        return

    sheet.update(values=[row_data], range_name=range_name)
    print(f"Google Sheet 更新完成：{range_name}")


def main() -> int:
    print("C3.1 Company Market Collector：開始抓取公司指定來源的每日市場資料")
    quotes = fetch_browser_quotes()

    audit_path = write_audit_snapshot(quotes)
    print(f"稽核快照：{audit_path}")

    # Copper Cash OFFER is the operationally critical quote used by purchasing.
    require_success(quotes, ("copper_lme_cash",))

    if os.getenv("GOOGLE_SHEET_ID", "").strip():
        update_google_sheet(quotes)
    else:
        print("GOOGLE_SHEET_ID 未設定：本次只產生本機稽核快照，不連線 Google Sheet")

    print("今日報價摘要：")
    for key, quote in quotes.items():
        print(f"- {key}: {quote.status} {quote.value if quote.ok else quote.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
