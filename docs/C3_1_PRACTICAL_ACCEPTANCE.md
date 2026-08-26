# C3.1 Practical Acceptance — Company Daily Market Workbook

Validation basis: current authoritative workbook / Google Sheet `大宗材料 行情統計表`, revalidated on 2026-08-26 against the original `update_prices_v5.py` source behavior.

## Authoritative workbook

Worksheet: `大宗材料 行情統計表`

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

## Date contract

Column A is now a strict full-date field.

- A1 = `日期`
- A5 downward = true Google Sheets date values displayed as `yyyy/mm/dd`
- Example: `2026/08/26` resolves to row 16 in the current sheet
- Day-only values such as `26` are not accepted
- Alternate separators such as `2026-08-26` or `2026.08.26` are not accepted by the C3.1 row-matching contract
- If the full date is missing or duplicated, the collector stops without writing

This removes cross-month ambiguity and makes the operational row auditable.

## Verified source contract

The actual source-of-truth is the original Python behavior:

- LME: Copper Cash OFFER, Copper 3-month OFFER, Aluminium / Lead / Nickel / Tin / Zinc Cash OFFER.
- SMM: electrolytic copper explicit average (`均價`), CNY/tonne.
- yfinance: Brent `BZ=F` Close, Silver `SI=F` Close ×100, Gold `GC=F` Close.

C3.1 improves LME and SMM extraction by validating semantic headers instead of relying on fixed HTML positions.

The Google Sheet metadata has been corrected to match the actual sources: D2=`CNY / TONNE`, J2=`USD / BBL`, J3:L3=`yfinance BZ=F / SI=F / GC=F`, J4:L4=`期貨`.

## Live Dry Run

A Live Dry Run means:

1. real Selenium access to LME/SMM;
2. real yfinance requests;
3. real Google Sheet authentication and A1:L4 validation;
4. real full-date target-row resolution using `yyyy/mm/dd`;
5. print the exact A:L row that would be written;
6. no Sheet write because `ALLOW_GOOGLE_SHEET_WRITE=0`.

It is live because every external dependency is real, and dry because operational cells remain unchanged.

### Windows one-command runner

From the repository root on the operational Windows PC:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_c3_1_live_dry_run.ps1 -SheetId "<GOOGLE_SHEET_ID>"
```

The helper script:

- forces `ALLOW_GOOGLE_SHEET_WRITE=0`;
- checks Python and `service_account.json`;
- installs/verifies collector dependencies;
- runs C3.1 unit tests;
- performs real LME / SMM / yfinance retrieval;
- authenticates to the real Google Sheet;
- validates A1:L4 and today's `yyyy/mm/dd` row;
- prints the exact dry-run row and quote summary;
- never writes Sheet cells.

If the service-account file is stored elsewhere, pass `-CredentialFile "C:\path\service_account.json"`.

## Write safety

Before any A:L update:

1. A1:L4 must match.
2. Today's complete `yyyy/mm/dd` date must exist exactly once in column A.
3. All 11 market values must succeed.
4. Copper Cash OFFER must succeed.
5. `ALLOW_GOOGLE_SHEET_WRITE=1` must be explicit.

## Remaining before operational cutover

- Run one company-PC Live Dry Run.
- Confirm LME OFFER detection live.
- Confirm SMM average detection live.
- Confirm yfinance BZ=F / SI=F / GC=F values.
- Confirm the collector resolves today's full date to the intended row.
- Compare all 11 values to expected same-day observations.
- Only then enable real Sheet writes.

C3.1 remains decision-data infrastructure only; it does not create an automatic PO or buy/no-buy action.
