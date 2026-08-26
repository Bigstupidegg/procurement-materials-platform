# C3.1 Practical Acceptance — Company Daily Market Workbook

Validation basis: current authoritative workbook / Google Sheet `大宗材料 行情統計表`, revalidated on 2026-08-26 against the original `update_prices_v5.py` source behavior.

## 1. Authoritative workbook scope

Worksheet: `大宗材料 行情統計表`

There is one required operational worksheet. The table uses four header rows and daily business-day rows starting at row 5.

```text
A: 日期
B: 銅 COPPER — USD / TONNE — LME OFFER — 現貨
C: 銅 COPPER — USD / TONNE — LME OFFER — 期貨(3月)
D: 電解銅 Copper Cathode — CNY / TONNE — SMM — 現貨
E: 鋁 ALUMINIUM — USD / TONNE — LME — 現貨
F: 鉛 LEAD — USD / TONNE — LME — 現貨
G: 鎳 NICKEL — USD / TONNE — LME — 現貨
H: 錫 TIN — USD / TONNE — LME — 現貨
I: 鋅 ZINC — USD / TONNE — LME — 現貨
J: 油 — USD / BBL — yfinance BZ=F — 期貨
K: 銀 — CENT / OUNCE — yfinance SI=F — 期貨
L: 黃金 — USD / OUNCE — yfinance GC=F — 期貨
```

For the current 2026-08 snapshot, day `26` is at row 16.

## 2. Verified source contract

The source-of-truth for how prices are fetched is the original Python collector behavior, not stale text labels in the Sheet.

### LME

- Copper Cash: LME Copper Cash OFFER.
- Copper 3-month: LME Copper 3-month OFFER.
- Aluminium / Lead / Nickel / Tin / Zinc: LME Cash OFFER.
- C3.1 improves the original fixed `cells[2]` selection by locating the semantic `OFFER` header and failing closed if it cannot be proven.

### SMM

- Electrolytic Copper: SMM `1#电解铜` / electrolytic copper.
- C3.1 selects the explicit average-price (`均價`) column instead of returning the first large numeric value.
- Raw operational value is retained as CNY per tonne; no implicit FX conversion is performed.

### yfinance

The original `update_prices_v5.py` uses yfinance for J/K/L:

- Brent: `BZ=F`, latest available `Close`, USD/bbl.
- Silver: `SI=F`, latest available `Close` multiplied by 100, stored as US cents/oz.
- Gold: `GC=F`, latest available `Close`, USD/oz.

These are futures references, not spot prices.

The current Google Sheet A1:L4 metadata has been corrected to match those actual source semantics:

- D2: `CNY / TONNE`
- J2: `USD / BBL`
- J3:L3: `yfinance BZ=F / SI=F / GC=F`
- J4:L4: `期貨`

## 3. Live Dry Run definition

A Live Dry Run uses the real company execution path without modifying the Sheet:

1. Launch the real Selenium browser on the company PC.
2. Fetch current LME and SMM values.
3. Call yfinance for BZ=F / SI=F / GC=F.
4. Authenticate to the real Google Sheet.
5. Validate A1:L4 and resolve today's target row.
6. Print the exact A:L row that would be written.
7. Stop because `ALLOW_GOOGLE_SHEET_WRITE=0`.

It is "live" because all external systems are real, and "dry" because no operational cell is changed.

## 4. Write safety

Before any A:L update, C3.1 requires:

1. A1:L4 matches the authoritative material / unit / source / term contract.
2. Today's day-of-month row exists exactly once.
3. All 11 required market values succeed before a full-row write.
4. LME Copper Cash OFFER is specifically required as the primary daily copper reference.
5. `ALLOW_GOOGLE_SHEET_WRITE=1` is explicitly enabled.

Default remains Live Dry Run.

## 5. Acceptance status

### Passed

- Single-sheet workbook contract confirmed.
- Day 26 resolves to row 16.
- LME / SMM / yfinance source responsibilities reconciled with the original Python script.
- Google Sheet metadata corrected to match actual source semantics.
- Fail-closed row, source, and layout validation implemented.
- Full-row partial-write protection implemented.
- Live Dry Run safety gate implemented.

### Remaining before replacing the operational v5 script

- Run one Live Dry Run on the company Windows PC.
- Confirm LME `OFFER` header is found live for Cash and 3-month.
- Confirm SMM `均價` is found live.
- Confirm yfinance returns BZ=F / SI=F / GC=F values.
- Compare all 11 generated values with the same-day expected market observations.
- Only after value-by-value acceptance, set `ALLOW_GOOGLE_SHEET_WRITE=1`.

C3.1 is decision-data infrastructure only. It does not issue an automatic purchase order or automatic buy/no-buy decision.
