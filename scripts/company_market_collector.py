from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from company_market_core import (
    DataContractError,
    MarketQuote,
    extract_table_value,
    find_sheet_row,
    require_success,
)


TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_PATH = ROOT / "runtime" / "company-market" / "latest.json"

LME_URLS = {
    "copper_lme_cash": ("銅_LME_現貨", "https://www.lme.com/metals/non-ferrous/lme-copper#Trading+summary", "Cash"),
    "copper_lme_3m": ("銅_LME_期貨", "https://www.lme.com/metals/non-ferrous/lme-copper#Trading+summary", "3-month"),
    "aluminium_lme_cash": ("鋁_LME", "https://www.lme.com/metals/non-ferrous/lme-aluminium#Trading+summary", "Cash"),
    "lead_lme_cash": ("鉛_LME", "https://www.lme.com/metals/non-ferrous/lme-lead#Summary", "Cash"),
    "nickel_lme_cash": ("鎳_LME", "https://www.lme.com/metals/non-ferrous/lme-nickel#Summary", "Cash"),
    "tin_lme_cash": ("錫_LME", "https://www.lme.com/metals/non-ferrous/lme-tin#Summary", "Cash"),
    "zinc_lme_cash": ("鋅_LME", "https://www.lme.com/metals/non-ferrous/lme-zinc#Summary", "Cash"),
}

SHEET_COLUMNS = (
    ("copper_lme_cash", "銅_LME_現貨"),
    ("copper_lme_3m", "銅_LME_期貨"),
    ("smm_electrolytic_copper", "電解銅_SMM"),
    ("aluminium_lme_cash", "鋁_LME"),
    ("lead_lme_cash", "鉛_LME"),
    ("nickel_lme_cash", "鎳_LME"),
    ("tin_lme_cash", "錫_LME"),
    ("zinc_lme_cash", "鋅_LME"),
    ("brent_yahoo", "布蘭特原油"),
    ("silver_yahoo", "紐約白銀"),
    ("gold_yahoo", "紐約黃金"),
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

    driver.get("https://www.smm.com.cn/price")
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


def fetch_yfinance_quotes() -> dict[str, MarketQuote]:
    import yfinance as yf

    specs = {
        "brent_yahoo": ("布蘭特原油", "BZ=F", "USD", "USD/bbl"),
        "silver_yahoo": ("紐約白銀", "SI=F", "USD", "USD/troy oz"),
        "gold_yahoo": ("紐約黃金", "GC=F", "USD", "USD/troy oz"),
    }
    quotes: dict[str, MarketQuote] = {}
    silver_mode = os.getenv("SILVER_OUTPUT_UNIT", "USD_PER_OZ").upper()

    for key, (name, symbol, currency, unit) in specs.items():
        try:
            history = yf.Ticker(symbol).history(period="5d")
            if history.empty:
                raise DataContractError("yfinance 沒有回傳任何近期資料")
            value = float(history["Close"].iloc[-1])
            observed_at = str(history.index[-1])
            output_unit = unit
            if key == "silver_yahoo" and silver_mode == "US_CENTS_PER_OZ":
                value *= 100.0
                output_unit = "US cents/troy oz"
            elif key == "silver_yahoo" and silver_mode != "USD_PER_OZ":
                raise DataContractError(
                    "SILVER_OUTPUT_UNIT 只允許 USD_PER_OZ 或 US_CENTS_PER_OZ"
                )
            quotes[key] = make_quote(
                key=key,
                name=name,
                source="Yahoo Finance",
                instrument=symbol,
                term="Continuous Futures",
                quote_type="Last Close",
                currency=currency,
                unit=output_unit,
                value=round(value, 2),
                observed_at=observed_at,
            )
        except Exception as exc:
            quotes[key] = make_quote(
                key=key,
                name=name,
                source="Yahoo Finance",
                instrument=symbol,
                term="Continuous Futures",
                quote_type="Last Close",
                currency=currency,
                unit=unit,
                value=None,
                error=str(exc),
            )
    return quotes


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
                    instrument=name.replace("_LME", ""),
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
                    instrument=name.replace("_LME", ""),
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
                name="電解銅_SMM",
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
                name="電解銅_SMM",
                source="Shanghai Metals Market",
                instrument="1# Electrolytic Copper",
                term="Spot",
                quote_type="Average",
                currency="CNY",
                unit="CNY/MT",
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
        "schemaVersion": 1,
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


def update_google_sheet(quotes: dict[str, MarketQuote]) -> None:
    import gspread

    require_success(quotes, ("copper_lme_cash",))

    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    if not sheet_id:
        raise DataContractError("未設定 GOOGLE_SHEET_ID；不允許猜測或硬編碼公司 Sheet")

    credentials = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    gc = gspread.service_account(filename=credentials)
    book = gc.open_by_key(sheet_id)
    worksheet_name = os.getenv("GOOGLE_SHEET_WORKSHEET", "").strip()
    sheet = book.worksheet(worksheet_name) if worksheet_name else book.sheet1

    target_date = now_taipei().date()
    target_row = find_sheet_row(sheet.col_values(1), target_date)
    if target_row is None:
        if os.getenv("ALLOW_SHEET_APPEND", "0") == "1":
            target_row = len(sheet.col_values(1)) + 1
        else:
            raise DataContractError(
                f"Google Sheet 找不到 {target_date.isoformat()} / {target_date.day} 號資料列；"
                "為避免寫錯列，已停止更新。"
            )

    date_mode = os.getenv("COMPANY_SHEET_DATE_FORMAT", "DAY").upper()
    if date_mode == "ISO":
        date_value = target_date.isoformat()
    elif date_mode == "DAY":
        date_value = str(target_date.day)
    else:
        raise DataContractError("COMPANY_SHEET_DATE_FORMAT 只允許 DAY 或 ISO")

    row_data = [date_value]
    for key, _label in SHEET_COLUMNS:
        quote = quotes.get(key)
        row_data.append(quote.value if quote and quote.ok else "")

    range_name = f"A{target_row}:L{target_row}"
    sheet.update(values=[row_data], range_name=range_name)
    print(f"Google Sheet 更新完成：{range_name}")


def main() -> int:
    print("C3.1 Company Market Collector：開始抓取每日市場資料")
    quotes = fetch_yfinance_quotes()
    quotes.update(fetch_browser_quotes())

    audit_path = write_audit_snapshot(quotes)
    print(f"稽核快照：{audit_path}")

    # Copper Cash OFFER is operationally critical. If it fails, do not write any
    # procurement-operation row to Google Sheets.
    require_success(quotes, ("copper_lme_cash",))

    if os.getenv("GOOGLE_SHEET_ID", "").strip():
        update_google_sheet(quotes)
    else:
        print("GOOGLE_SHEET_ID 未設定：本次只產生本機稽核快照，不寫入 Google Sheet")

    print("今日報價摘要：")
    for key, quote in quotes.items():
        print(f"- {key}: {quote.status} {quote.value if quote.ok else quote.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
