# Changelog

All notable platform changes should be recorded here. Dates use Asia/Taipei calendar dates unless a Git commit timestamp is explicitly referenced.

## [2.3.0] - 2026-08-26

### Added

- v2.3 Project Foundation development line.
- `config/release.json` as machine-readable release metadata.
- `docs/PROJECT_KNOWLEDGE_BASE.md` for business context, supplier cases, and design principles.
- `docs/DATA_DICTIONARY.md` for production and planned JSON schemas.
- `docs/CALCULATION_RULES.md` for market, Should-Cost, supplier-gap, and auditability formulas.
- `docs/DATA_SOURCE_POLICY.md` for source roles, provenance, staleness, units, and security policy.
- `docs/ROADMAP.md` for v2.3 through v3.x planning.
- Source-level `assets/app-core.js` containing Should-Cost and navigation only.
- Source-level `assets/demo-market.js` as an explicit Development Demo market fixture.
- `assets/data-freshness.js` showing latest market period, World Bank synchronization, source update date, FRED corroboration status, and stale state from `data/status.json`.
- `tests/test_prepare_site.py` coverage for release identity, source boundary, production stripping, and freshness status.
- `.github/workflows/quality-check.yml` for pull-request tests, JS syntax checks, site build, release identity, and Demo-leakage guards.
- C3.1 private company-market collector core and tests.
- `docs/C3_1_PRACTICAL_ACCEPTANCE.md` documenting validation against the supplied company workbook and original Python collection behavior.

### Changed

- C3.1 now validates the real `大宗材料 行情統計表` A1:L4 layout before any Google Sheet write.
- C3.1 source contract was revalidated against the original `update_prices_v5.py`: LME for B/C/E-I, SMM for D, and yfinance for J/K/L (`BZ=F`, `SI=F`, `GC=F`).
- The earlier C3.1 substitution of 鉅亨 for J/K/L was removed because it did not match the original Python implementation.
- Google Sheet metadata was corrected to SMM `CNY / TONNE`, Brent `USD / BBL`, explicit yfinance tickers, and futures labeling for J/K/L.
- Company Google Sheet writes are Live Dry Run by default and require `ALLOW_GOOGLE_SHEET_WRITE=1` for an actual update.
- Full-row company writes require all 11 quotes to succeed, preventing failed sources from blanking existing operational cells.
- LME extraction uses the semantic OFFER header rather than the original fixed third-cell assumption.
- SMM extraction uses the explicit average-price column.

### Remaining v2.3 technical debt

- Browser-level source-consistency tests for cards/chart/tooltip/CSV are still pending.
- Visual regression coverage for loading, success, stale, and fail-closed states is still pending.
- Rule/config threshold consolidation is still pending.
- C3.1 Live Dry Run is pending in the company Windows environment.

### Planned before v2.3 completion

- complete C3.1 company-market Live Dry Run acceptance and proceed to Copper daily analytics
- add browser-level cards/chart/tooltip/CSV source-consistency coverage
- add visual/status regression coverage
- consolidate duplicated rule/config thresholds where applicable
- Supplier Case foundation for TTP Radiator and Bushing
