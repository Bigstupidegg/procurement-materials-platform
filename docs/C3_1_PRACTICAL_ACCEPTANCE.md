# C3.1 Practical Acceptance — Company Daily Market Workbook

Validation basis: current authoritative workbook / Google Sheet `大宗材料 行情統計表`, confirmed on 2026-08-26.

## 1. Authoritative workbook scope

C3.1 now treats the current workbook as the single operational source of layout truth.

Worksheet: `大宗材料 行情統計表`

There is no required second `行情統計表資料來源` worksheet in the authoritative version.

The operational table uses four header rows and daily business-day rows starting at row 5.

```text
A: 日期
B: 銅 COPPER — USD / TONNE — LME OFFER — 現貨
C: 銅 COPPER — USD / TONNE — LME OFFER — 期貨(3月)
D: 電解銅 Copper Cathode — USD / TONNE — SMM — 現貨
E: 鋁 ALUMINIUM — USD / TONNE — LME — 現貨
F: 鉛 LEAD — USD / TONNE — LME — 現貨
G: 鎳 NICKEL — USD / TONNE — LME — 現貨
H: 錫 TIN — USD / TONNE — LME — 現貨
I: 鋅 ZINC — USD / TONNE — LME — 現貨
J: 油 — USD / DRUM — 鉅亨 倫敦布蘭特 — 現貨
K: 銀 — CENT / OUNCE — 鉅亨 紐約白銀 — 現貨
L: 黃金 — USD / OUNCE — 鉅亨 紐約黃金 — 現貨
```

For the current 2026-08 snapshot, day `26` is at **row 16**.

The legacy `update_prices_v5.py` scan can resolve the row incorrectly because it offsets the enumerated column-A index. C3.1 now returns the actual one-based worksheet row and resolves day 26 to row 16.

## 2. Source policy derived from the authoritative main sheet

The A1:L4 contract itself defines the company-facing source identity:

- Copper Cash: LME OFFER / 現貨
- Copper 3-month: LME OFFER / 期貨(3月)
- Electrolytic Copper: SMM / 現貨
- Aluminium / Lead / Nickel / Tin / Zinc: LME / 現貨
- Oil: 鉅亨 倫敦布蘭特 / 現貨
- Silver: 鉅亨 紐約白銀 / 現貨
- Gold: 鉅亨 紐約黃金 / 現貨

The collector keeps its fetch URLs in code, but no longer requires a second workbook tab to validate them.

## 3. Unit policy

The current company workbook is authoritative for operational Sheet labels.

Therefore C3.1 validates the current labels exactly, including:

```text
D2 = USD / TONNE
J2 = USD / DRUM
```

The collector must not silently rewrite these labels or block the workbook because of a model-side preference.

Separately, source-native unit metadata is preserved in the local audit snapshot so later analytics can distinguish source metadata from company workbook labels. No automatic unit conversion is performed during the A:L operational write.

## 4. Write safety

Before any A:L row update, C3.1 requires:

1. Main sheet A1:L4 matches the authoritative material / unit / source / term contract.
2. Today's day-of-month row exists exactly once.
3. Every one of the 11 market values is successful before a full-row write.
4. `ALLOW_GOOGLE_SHEET_WRITE=1` is explicitly enabled.

The default is dry-run. Missing/ambiguous rows, shifted columns, changed authoritative labels, missing OFFER headers, or incomplete full-row data stop the write.

## 5. Acceptance status

### Passed

- Current Google Sheet and uploaded workbook agree on A1:L16.
- Workbook contains one required worksheet: `大宗材料 行情統計表`.
- A1:L4 authoritative layout contract captured.
- Day 26 resolves to row 16.
- B:L column order and material identity validated.
- Copper LME Cash / 3-month source-term identity validated.
- Company-defined D2 / J2 unit labels are preserved as authoritative workbook labels.
- Fail-closed row and layout validation implemented.
- Dry-run safety gate implemented.
- J/K/L collection remains aligned with company-facing 鉅亨 source labels.

### Remaining before replacing the operational v5 script

- Run Selenium in the company Windows environment and confirm LME `OFFER` is found by semantic header.
- Confirm 鉅亨 pages expose `收盤價` to Selenium for Brent / Silver / Gold.
- Confirm SMM `均價` is found on the live page.
- Execute one dry-run and compare all 11 generated values to the same-day workbook row.
- Only after a value-by-value match, set `ALLOW_GOOGLE_SHEET_WRITE=1`.

C3.1 remains decision-data infrastructure only. It does not produce an automatic copper purchase order or automatic buy/no-buy decision.
