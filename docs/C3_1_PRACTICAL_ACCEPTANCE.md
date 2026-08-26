# C3.1 Practical Acceptance — Company Daily Market Workbook

Validation basis: company workbook snapshot `大宗材料 行情統計表`, supplied on 2026-08-26.

## 1. Main sheet layout

Worksheet: `大宗材料 行情統計表`

The operational table uses four header rows and daily business-day rows starting at row 5.

```text
A: 日期
B: 銅 COPPER — LME OFFER — 現貨
C: 銅 COPPER — LME OFFER — 期貨(3月)
D: 電解銅 Copper Cathode — SMM — 現貨
E: 鋁 ALUMINIUM — LME — 現貨
F: 鉛 LEAD — LME — 現貨
G: 鎳 NICKEL — LME — 現貨
H: 錫 TIN — LME — 現貨
I: 鋅 ZINC — LME — 現貨
J: 油 — 鉅亨 倫敦布蘭特 — 現貨
K: 銀 — 鉅亨 紐約白銀 — 現貨
L: 黃金 — 鉅亨 紐約黃金 — 現貨
```

For the supplied 2026-08 snapshot, day `26` is at **row 16**.

The legacy `update_prices_v5.py` logic used `idx + 2` while scanning column A. On this actual layout, the day 26 row can be resolved one row too low. C3.1 now uses the actual one-based worksheet row and returns row 16.

## 2. Source registry contract

Worksheet: `行情統計表資料來源`

C3.1 now treats this source policy as a write-time contract:

- Copper Cash: LME OFFER / Cash
- Copper 3-month: LME OFFER / 3-month
- Electrolytic Copper: SMM
- Aluminium / Lead / Nickel / Tin / Zinc: LME
- Oil: 鉅亨 倫敦布蘭特 close
- Silver: 鉅亨 紐約白銀 close
- Gold: 鉅亨 紐約黃金 close

The collector must not silently substitute Yahoo Finance for the J/K/L company-source columns.

## 3. Write safety

Before any A:L row update, C3.1 now requires:

1. Main sheet A1:L4 layout matches the expected material/source/term contract.
2. Source sheet A1:E12 matches the configured source registry.
3. Today's day-of-month row exists exactly once.
4. Every one of the 11 market values is successful before a full-row write.
5. `ALLOW_GOOGLE_SHEET_WRITE=1` is explicitly enabled.

The default is dry-run. Missing/ambiguous rows, shifted columns, missing OFFER headers, or incomplete full-row data stop the write.

## 4. Known workbook metadata issues

These are workbook metadata issues, not collector parsing assumptions:

### SMM Copper unit

The main sheet currently labels D2 as `USD / TONNE`, while the daily values are around 107,000–109,000 and the SMM collector preserves the raw SMM value as `CNY/MT`.

Recommended correction before production cutover:

```text
D2: CNY / TONNE
```

If the company requires an USD/tonne SMM value instead, the conversion must be explicit and auditable: raw CNY price, FX source, FX timestamp, conversion formula, and converted USD value should be stored separately. Do not overwrite the raw SMM observation without provenance.

### Brent unit

The main sheet currently labels J2 as `USD / DRUM`. For market-data governance, `USD / BBL` is the recommended label for Brent futures pricing.

Recommended correction:

```text
J2: USD / BBL
```

## 5. Acceptance status

### Passed

- Actual row-number mapping against the supplied workbook.
- B:L column order and material identity.
- Copper LME Cash / 3-month source-term identity.
- Company source registry mapping.
- Fail-closed row and layout validation.
- Dry-run safety gate.
- J/K/L collector source changed from Yahoo Finance to company-required 鉅亨 pages.

### Remaining before replacing the operational v5 script

- Run Selenium in the company Windows environment and confirm LME `OFFER` is found by semantic header.
- Confirm Cnyes pages expose `收盤價` to Selenium for Brent / Silver / Gold.
- Confirm SMM `均價` is found on the live page.
- Correct/approve D2 and J2 unit labels.
- Execute one dry-run and compare all 11 generated values to the same-day workbook row.
- Only after a value-by-value match, set `ALLOW_GOOGLE_SHEET_WRITE=1`.

C3.1 remains decision-data infrastructure only. It does not produce an automatic copper purchase order or automatic buy/no-buy decision.
