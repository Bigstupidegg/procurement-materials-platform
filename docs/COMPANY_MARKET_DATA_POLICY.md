# Company Market Data Policy

## Purpose

This policy defines the boundary between the public procurement analytics website and the company's daily operational market-price workflow.

The public GitHub Pages site remains a market-analysis and benchmark layer. Google Sheets remains the private operational record for daily purchasing work. Python collectors may feed both layers, but private procurement information must never be published to the public repository.

## Authoritative company workbook contract

For C3.1, the current company workbook / Google Sheet is the authoritative operational layout.

- Spreadsheet title: `大宗材料 行情統計表`
- Worksheet: `大宗材料 行情統計表`
- Authoritative layout range: `A1:L4`
- There is no required second source-registry worksheet.
- The collector must validate the current A1:L4 material / unit / source / term labels before any write.
- Source labels must match the actual collector implementation.
- Source-native quote metadata is kept in the local audit snapshot for traceability.

Current A:L operational columns are:

```text
A 日期
B 銅 COPPER — USD / TONNE — LME OFFER — 現貨
C 銅 COPPER — USD / TONNE — LME OFFER — 期貨(3月)
D 電解銅 Copper Cathode — CNY / TONNE — SMM — 現貨
E 鋁 ALUMINIUM — USD / TONNE — LME — 現貨
F 鉛 LEAD — USD / TONNE — LME — 現貨
G 鎳 NICKEL — USD / TONNE — LME — 現貨
H 錫 TIN — USD / TONNE — LME — 現貨
I 鋅 ZINC — USD / TONNE — LME — 現貨
J 油 — USD / BBL — yfinance BZ=F — 期貨
K 銀 — CENT / OUNCE — yfinance SI=F — 期貨
L 黃金 — USD / OUNCE — yfinance GC=F — 期貨
```

## Verified collection sources

### LME via Selenium

Used for Copper Cash / Copper 3-month / Aluminium / Lead / Nickel / Tin / Zinc. The collector selects the semantic `OFFER` header instead of relying on a fixed HTML column index.

### SMM via Selenium

Used for `1#电解铜` / electrolytic copper explicit average (`均價`) value. The raw operational value is retained as CNY per tonne; no implicit FX conversion is performed.

### Yahoo Finance via yfinance

Used for the three fields present in the original `update_prices_v5.py` implementation:

- Brent: `BZ=F`, latest available `Close`, USD/bbl
- Silver: `SI=F`, latest available `Close × 100`, US cents/oz
- Gold: `GC=F`, latest available `Close`, USD/oz

These are futures references, not spot prices. yfinance is a data-access library; the collector must retain the ticker symbol, observation timestamp, and quote type (`Close`) in the audit record. These values are market references and should not be represented as exchange settlement or official LME/SMM prices.

## System roles

### 1. Public market-analysis layer — GitHub Pages

Use for World Bank Pink Sheet monthly benchmarks, FRED independent corroboration, trend signals, Should-Cost analysis, and public-safe market statistics.

Do not store internal inventory, purchase quantities, supplier-specific buy decisions, purchase orders, internal target prices, Google service-account credentials, or private Google Sheet exports.

### 2. Private daily-operation layer — Google Sheets

Use for LME Copper Cash OFFER, LME Copper 3-Month, SMM electrolytic copper, yfinance Brent / Silver / Gold references, and future internal purchasing context.

Google Sheets is not a source for the public website unless a later phase creates an explicitly public-safe export.

### 3. Data-engineering layer — Python collector

The collector retrieves prices, validates source structure and the authoritative A1:L4 workbook layout, records provenance, fails closed when required data cannot be proven, writes the private Sheet only after checks pass, and emits a local audit snapshot excluded from Git.

## Copper daily decision hierarchy

1. LME Copper Cash OFFER — primary operational quote
2. LME Copper 3-Month — near-term curve / spread context
3. recent daily LME statistics — short-term position
4. World Bank monthly Copper benchmark — medium-term market context
5. internal inventory, demand, open orders, and purchasing constraints — private decision context

World Bank monthly data must not replace the daily LME Cash OFFER when deciding whether today's spot copper price is attractive.

## Fail-closed requirements

Stop the operational Google Sheet write when the LME OFFER column cannot be identified, the Copper Cash row/quote is missing, any of the 11 full-row values fails, the authoritative A1:L4 layout no longer matches, or the target date row is ambiguous/missing.

A missing quote is safer than a silently incorrect quote.

## Data contract

Each market quote includes:

```text
key
name
source
instrument
term
quote_type
currency
unit
value
fetched_at
observed_at
status
error
```

The local audit snapshot is classified `INTERNAL_OPERATIONAL` and must not be committed to this public repository.

## Credentials and configuration

Secrets must remain outside Git. Supported environment variables:

```text
GOOGLE_SHEET_ID
GOOGLE_SERVICE_ACCOUNT_FILE
GOOGLE_SHEET_WORKSHEET
ALLOW_GOOGLE_SHEET_WRITE=0|1
COMPANY_MARKET_AUDIT_PATH
```

`service_account.json`, `.env`, runtime snapshots, and company operational data are excluded by `.gitignore`.

## C3 phased implementation

### C3.1 — Collector hardening

- semantic LME OFFER-column detection
- explicit SMM average-price detection
- yfinance BZ=F / SI=F / GC=F source handling
- authoritative single-sheet A1:L4 validation
- safe Google Sheet date-row matching
- local audit snapshot
- credential / company-data isolation
- Copper Cash OFFER fail-closed gate
- Live Dry Run by default

### C3.2 — Copper daily analytics

Planned: day-over-day change, 5-day average, 20-day average, 60-day percentile, LME Cash vs 3-Month spread, and daily LME Cash OFFER vs latest World Bank monthly benchmark. Output remains decision support, not an automatic purchase order.

### C3.3 — Public-safe market snapshot (optional)

If useful, export only public market information to GitHub Pages. Do not export company inventory, demand, supplier, PO, target, or buy/no-buy records.

### C3.4 — Private procurement context

Add private inputs such as inventory days, safety stock, incoming quantities, and demand only inside an internal system or private data store. Do not put these fields in the current public GitHub Pages repository.
