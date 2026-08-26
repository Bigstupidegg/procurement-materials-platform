# Roadmap

## v2.3 — Project Foundation & Maintainability

### Phase C3 — Company daily market-data layer

#### C3.1 — Collector hardening and practical workbook acceptance

Status: source contract corrected and authoritative single-sheet workbook contract implemented; one company-PC Live Dry Run remains before operational cutover.

- [x] Replace fixed LME column indexing with semantic `OFFER` header lookup.
- [x] Treat LME Copper Cash OFFER as the primary operational copper quote.
- [x] Select SMM electrolytic-copper `均價` semantically.
- [x] Match the existing monthly Sheet day row exactly and fail on ambiguous/missing dates.
- [x] Treat the current `大宗材料 行情統計表` worksheet as the authoritative operational workbook layout.
- [x] Validate authoritative A1:L4 material / unit / source / term labels before every write.
- [x] Remove the obsolete requirement for a second source-registry worksheet.
- [x] Revalidate collection sources against the original `update_prices_v5.py` implementation.
- [x] Keep B/C/E-I on LME, D on SMM, and J/K/L on yfinance (`BZ=F`, `SI=F`, `GC=F`).
- [x] Remove the incorrect interim C3.1 substitution of 鉅亨 for J/K/L.
- [x] Correct Sheet metadata to SMM CNY/tonne, Brent USD/bbl, explicit yfinance tickers, and futures identity for J/K/L.
- [x] Make Google Sheet writes opt-in via `ALLOW_GOOGLE_SHEET_WRITE=1`; default is Live Dry Run.
- [x] Prevent partial full-row writes: all 11 quotes must succeed before A:L update.
- [x] Keep service-account credentials, Sheet IDs, operational audit data, inventory, supplier/PO data and buy/no-buy decisions out of the public repository.
- [x] Validate the authoritative workbook row mapping: 2026-08-26 resolves to row 16.
- [ ] Run one company-PC Live Dry Run and compare all 11 values with same-day expected observations.
- [ ] Enable `ALLOW_GOOGLE_SHEET_WRITE=1` only after value-by-value acceptance.

#### C3.2 — Copper daily analytics

Planned after C3.1 cutover:

- Day-over-day Copper Cash OFFER change.
- 5-day and 20-day moving averages.
- 60-day price percentile.
- LME Cash vs 3-month spread / backwardation-contango context.
- Latest Cash OFFER vs World Bank monthly Copper benchmark.
- Explainable `FAVORABLE / NEUTRAL / UNFAVORABLE` market condition.
- No automatic PO and no automatic buy/no-buy action.
