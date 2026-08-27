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
    require_success,
    validate_sheet_layout,
)
from company_market_preflight import (
    require_explicit_write_approval,
    run_anomaly_checks,
    validate_controlled_write_preflight,
)
from company_market_row_safety import resolve_safe_target_row


TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_PATH = ROOT / "runtime" / "company-market" / "latest.json"
MAIN_SHEET_DEFAULT = "大宗材料 行情統計表"

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

YFINANCE_SPECS = {
    "brent_yfinance": ("油", "BZ=F", 1.0, "USD", "USD/bbl"),
    "silver_yfinance": ("銀", "SI=F", 100.0, "US cents", "US cents/troy oz"),
    "gold_yfinance": ("黃金", "GC=F", 1.0, "USD", "USD/troy oz"),
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
    "brent_yfinance",
    "silver_yfinance",
    "gold_yfinance",
)

MAX_DAILY_CHANGE_PCT = {
    "copper_lme_cash": 20.0,
    "copper_lme_3m": 20.0,
    "smm_electrolytic_copper": 20.0,
    "aluminium_lme_cash": 20.0,
    "lead_lme_cash": 20.0,
    "nickel_lme_cash": 25.0,
    "tin_lme_cash": 25.0,
    "zinc_lme_cash": 20.0,
    "brent_yfinance": 25.0,
    "silver_yfinance": 30.0,
    "gold_yfinance": 20.0,
}

# Authoritative A1:L4 contract from the current company Google Sheet.
COMPANY_MAIN_LAYOUT = (
    (
        "日期", "銅 COPPER", "銅 COPPER", "電解銅 Copper Cathode",
        "鋁 ALUMINIUM", "鉛 LEAD", "鎳 NICKEL", "錫 TIN", "鋅 ZINC",
        "油", "銀", "黃金",
    ),
    (
        None, "USD / TONNE", "USD / TONNE", "CNY / TONNE",
        "USD / TONNE", "USD / TONNE", "USD / TONNE", "USD / TONNE",
        "USD / TONNE", "USD / BBL", "CENT / OUNCE", "USD / OUNCE",
    ),
    (
        "資料來源", "LME OFFER", "LME OFFER", "SMM", "LME", "LME", "LME",
        "LME", "LME", "yfinance BZ=F", "yfinance SI=F", "yfinance GC=F",
    ),
    (
        None, "現貨", "期貨(3月)", "現貨", "現貨", "現貨", "現貨", "現貨",
        "現貨", "期貨", "期貨", "期貨",
    ),
)

SHEET_UNIT_LABELS = {
    "copper_lme_cash": "USD / TONNE",
    "copper_lme_3m": "USD / TONNE",
    "smm_electrolytic_copper": "CNY / TONNE",
    "aluminium_lme_cash": "USD / TONNE",
    "lead_lme_cash": "USD / TONNE",
    "nickel_lme_cash": "USD / TONNE",
    "tin_lme_cash": "USD / TONNE",
    "zinc_lme_cash": "USD / TONNE",
    "brent_yfinance": "USD / BBL",
    "silver_yfinance": "CENT / OUNCE",
    "gold_yfinance": "USD / OUNCE",
}


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
    finally:
        driver.quit()
    return quotes


def fetch_yfinance_quotes() -> dict[str, MarketQuote]:
    import yfinance as yf

    quotes: dict[str, MarketQuote] = {}
    for key, (name, symbol, multiplier, currency, unit) in YFINANCE_SPECS.items():
        try:
            history = yf.Ticker(symbol).history(period="5d")
            if history.empty:
                raise DataContractError(f"yfinance {symbol} 沒有回傳近期資料")
            raw_value = float(history["Close"].iloc[-1])
            observed_at = str(history.index[-1])
            value = round(raw_value * multiplier, 2)
            quotes[key] = make_quote(
                key=key,
                name=name,
                source="Yahoo Finance / yfinance",
                instrument=symbol,
                term="Continuous Futures",
                quote_type="Close",
                currency=currency,
                unit=unit,
                value=value,
                observed_at=observed_at,
            )
        except Exception as exc:
            quotes[key] = make_quote(
                key=key,
                name=name,
                source="Yahoo Finance / yfinance",
                instrument=symbol,
                term="Continuous Futures",
                quote_type="Close",
                currency=currency,
                unit=unit,
                value=None,
                error=str(exc),
            )
    return quotes


def write_audit_snapshot(quotes: dict[str, MarketQuote]) -> Path:
    output = Path(os.getenv("COMPANY_MARKET_AUDIT_PATH", str(DEFAULT_AUDIT_PATH)))
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": 4,
        "classification": "INTERNAL_OPERATIONAL",
        "generatedAt": iso_now(),
        "decisionPolicy": {
            "copperDailyPrimary": "LME_CASH_OFFER",
            "automaticBuyDecision": False,
            "publicGitHubStorage": False,
        },
        "sourceContract": {
            "lme": "LME website via Selenium; semantic OFFER header",
            "smm": "SMM website via Selenium; electrolytic copper average",
            "brent": "yfinance BZ=F Close",
            "silver": "yfinance SI=F Close x 100",
            "gold": "yfinance GC=F Close",
        },
        "workbookContract": {
            "authority": "AUTHORITATIVE_COMPANY_WORKBOOK",
            "worksheet": os.getenv("GOOGLE_SHEET_WORKSHEET", MAIN_SHEET_DEFAULT).strip(),
            "layoutRange": "A1:L4",
            "dateFormat": "yyyy/mm/dd",
            "sheetUnitLabels": SHEET_UNIT_LABELS,
        },
        "quotes": {key: quote.to_dict() for key, quote in quotes.items()},
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def validate_company_workbook(book):
    main_name = os.getenv("GOOGLE_SHEET_WORKSHEET", MAIN_SHEET_DEFAULT).strip()
    main_sheet = book.worksheet(main_name)
    validate_sheet_layout(main_sheet.get("A1:L4"), COMPANY_MAIN_LAYOUT)
    return main_sheet


def update_google_sheet(quotes: dict[str, MarketQuote], audit_path: Path) -> None:
    import gspread

    require_success(quotes, SHEET_COLUMNS)

    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    if not sheet_id:
        raise DataContractError("未設定 GOOGLE_SHEET_ID；不允許猜測或硬編碼公司 Sheet")

    credentials = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    gc = gspread.service_account(filename=credentials)
    book = gc.open_by_key(sheet_id)
    sheet = validate_company_workbook(book)

    target_date = now_taipei().date()
    target_date_text = target_date.strftime("%Y/%m/%d")
    sheet_rows = sheet.get("A1:L1000", value_render_option="FORMATTED_VALUE")
    target_row, is_new_row = resolve_safe_target_row(sheet_rows[4:], target_date)
    target_row_values = sheet_rows[target_row - 1] if target_row <= len(sheet_rows) else []

    anomaly_checks = run_anomaly_checks(
        quotes,
        SHEET_COLUMNS,
        sheet_rows,
        target_row,
        MAX_DAILY_CHANGE_PCT,
    )
    report = validate_controlled_write_preflight(
        target_date=target_date,
        expected_date=now_taipei().date(),
        target_row=target_row,
        is_new_row=is_new_row,
        target_row_values=target_row_values,
        quotes=quotes,
        required_keys=SHEET_COLUMNS,
        anomaly_checks=anomaly_checks,
        layout_validated=True,
        audit_path=audit_path,
    )

    row_data = [target_date_text] + [quotes[key].value for key in SHEET_COLUMNS]
    range_name = f"A{target_row}:L{target_row}"

    if os.getenv("ALLOW_GOOGLE_SHEET_WRITE", "0") != "1":
        print(f"CONTROLLED WRITE PREFLIGHT PASS：{target_date_text} -> {range_name}")
        print(f"人工批准字串（尚未批准）：{report.approval_token}")
        print("LIVE DRY RUN：preflight 已完成；不寫入 Google Sheet。")
        print("DRY RUN DATA:", row_data)
        return

    require_explicit_write_approval(
        report,
        write_enabled=os.getenv("ALLOW_GOOGLE_SHEET_WRITE", "0"),
        approval=os.getenv("CONTROLLED_WRITE_APPROVAL", ""),
    )
    sheet.update(values=[row_data], range_name=range_name)
    print(f"Google Sheet 更新完成：{range_name}")


def main() -> int:
    print("C3.1 Company Market Collector：開始抓取 LME / SMM / yfinance 每日市場資料")

    quotes = fetch_browser_quotes()
    quotes.update(fetch_yfinance_quotes())

    audit_path = write_audit_snapshot(quotes)
    print(f"稽核快照：{audit_path}")

    # Copper Cash OFFER is operationally critical. Full-row writes also require
    # all 11 values to succeed in update_google_sheet().
    require_success(quotes, ("copper_lme_cash",))

    if os.getenv("GOOGLE_SHEET_ID", "").strip():
        update_google_sheet(quotes, audit_path)
    else:
        print("GOOGLE_SHEET_ID 未設定：本次只產生本機稽核快照，不連線 Google Sheet")

    print("今日報價摘要：")
    for key in SHEET_COLUMNS:
        quote = quotes[key]
        print(f"- {key}: {quote.status} {quote.value if quote.ok else quote.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
