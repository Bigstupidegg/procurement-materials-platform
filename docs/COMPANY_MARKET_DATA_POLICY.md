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
- Company-defined unit labels are preserved exactly; the collector must not silently rewrite them.
- Source-native quote metadata is still kept in the local audit snapshot for traceability.

Current A:L operational columns are:

```text
A 日期
B 銅 COPPER — LME OFFER — 現貨
C 銅 COPPER — LME OFFER — 期貨(3月)
D 電解銅 Copper Cathode — SMM — 現貨
E 鋁 ALUMINIUM — LME — 現貨
F 鉛 LEAD — LME — 現貨
G 鎳 NICKEL — LME — 現貨
H 錫 TIN — LME — 現貨
I 鋅 ZINC — LME — 現貨
J 油 — 鉅亨 倫敦布蘭特 — 現貨
K 銀 — 鉅亨 紐約白銀 — 現貨
L 黃金 — 鉅亨 紐約黃金 — 現貨
```

## System roles

### 1. Public market-analysis layer — GitHub Pages

Use for:

- World Bank Pink Sheet monthly benchmarks
- FRED independent corroboration
- market trend and negotiation signals
- Should-Cost analysis
- public-safe market statistics

Do not store:

- internal inventory
- purchase quantities
- supplier-specific buy decisions
- purchase orders
- internal target prices
- Google service-account credentials
- private Google Sheet exports

### 2. Private daily-operation layer — Google Sheets

Use for:

- LME Copper Cash OFFER used in the daily copper-purchase workflow
- LME Copper 3-Month reference
- SMM electrolytic-copper spot reference
- other company-required daily market quotations
- manual notes and future internal purchasing context

Google Sheets is not a source for the public website unless a later phase creates an explicitly public-safe export.

### 3. Data-engineering layer — Python collector

The collector is responsible for:

- retrieving source prices
- validating source structure
- validating the authoritative A1:L4 workbook layout
- recording source / instrument / term / quote type / source-native unit / timestamp
- failing closed when a critical quote cannot be proven
- writing the private Google Sheet only after required checks pass
- writing a local audit snapshot that is excluded from Git

## Copper daily decision hierarchy

For the company's daily copper-purchase workflow, the priority is:

1. LME Copper Cash OFFER — primary operational quote
2. LME Copper 3-Month — near-term curve / spread context
3. recent daily LME statistics — short-term position
4. World Bank monthly Copper benchmark — medium-term market context
5. internal inventory, demand, open orders, and purchasing constraints — private decision context

World Bank monthly data must not replace the daily LME Cash OFFER when deciding whether today's spot copper price is attractive.

## Fail-closed requirements

The collector must stop the operational Google Sheet write when:

- the LME OFFER column cannot be identified by header name
- the Copper Cash row cannot be identified
- the Copper Cash OFFER is missing or invalid
- any of the 11 A:L market quotes fails when a full-row update is requested
- the authoritative A1:L4 layout no longer matches
- the target Google Sheet date row is ambiguous or missing

A missing quote is safer than a silently incorrect quote.

## Data contract

Each market quote is represented with at least:

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

The audit snapshot separately records the authoritative workbook unit labels so source-native metadata and company sheet labels are both traceable.

The local audit snapshot is classified `INTERNAL_OPERATIONAL` and must not be committed to this public repository.

## Credentials and configuration

Secrets must remain outside Git.

Supported environment variables:

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
- company-required 鉅亨 source handling
- authoritative single-sheet A1:L4 validation
- safe Google Sheet date-row matching
- local audit snapshot
- credential / company-data isolation
- Copper Cash OFFER fail-closed gate
- dry-run by default

### C3.2 — Copper daily analytics

Planned calculations:

- day-over-day change
- 5-day average
- 20-day average
- 60-day price percentile
- LME Cash vs 3-Month spread
- daily LME Cash OFFER vs latest World Bank monthly benchmark

Output should be a decision-support label such as `FAVORABLE`, `NEUTRAL`, or `UNFAVORABLE`, not an automatic purchase order.

### C3.3 — Public-safe market snapshot (optional)

If useful, export only public market information to GitHub Pages. Do not export company inventory, demand, supplier, PO, target, or buy/no-buy records.

### C3.4 — Private procurement context

Add private inputs such as inventory days, safety stock, incoming quantities, and demand only inside an internal system or private data store. Do not put these fields in the current public GitHub Pages repository.
