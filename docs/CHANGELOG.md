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

### Changed

- README rewritten to reflect the repository's real implementation rather than the obsolete Phase 2 seed-data state.
- Project positioning changed from `v1.2.1 Prototype / Demo Data` to `v2.3 Real Data + Procurement Decision Support`.
- `assets/app.js` is now a Development Demo bootstrap only; it no longer contains market simulation, Should-Cost, or navigation business logic.
- `scripts/prepare_site.py` now uses source-level `app-core.js` directly instead of extracting a production core from the legacy monolithic app at build time.
- Production build replaces the development bootstrap reference with `app-core.js` and removes both `_site/assets/app.js` and `_site/assets/demo-market.js`.
- Market cards, chart, tooltip, statistics, and CSV in production are owned by the validated World Bank real-data module.
- Built `world-bank-live.js` release wording is normalized from legacy v1.3.0 text to the active release version.
- Pages deployment rejects a build if Development Demo code appears in the production core or production HTML.
- Existing calculator integration tests now treat `assets/app-core.js` as the formal Should-Cost contract.
- World Bank remains the primary market source; FRED remains independent corroboration only.
- Supplier price analysis remains decision support, not automatic acceptance/rejection.

### Remaining v2.3 technical debt

- Source `assets/world-bank-live.js` still contains a legacy v1.3.0 literal; the v2.3 build normalizes the copied deployment asset through `prepare_site.py`.
- Browser-level source-consistency tests for cards/chart/tooltip/CSV are still pending.
- Visual regression coverage for loading, success, stale, and fail-closed states is still pending.
- Rule/config threshold consolidation is still pending.

### Planned before v2.3 completion

- add browser-level cards/chart/tooltip/CSV source-consistency coverage
- add visual/status regression coverage
- consolidate duplicated rule/config thresholds where applicable
- add schema-version enforcement for derived JSON
- Supplier Case foundation for TTP Radiator and Bushing

## [2.2.x] - historical implementation state

The repository already contained, before the v2.3 documentation baseline:

- World Bank synchronization
- FRED synchronization
- source comparison
- procurement trend signals
- supplier rationality rules
- negotiation report UI
- automated GitHub Pages deployment

These capabilities were not consistently reflected in the previous README/version wording, which is why v2.3 begins with project consolidation rather than feature expansion.

## [1.2.1] - prototype baseline

Legacy front-end prototype included:

- raw-material overview cards
- multi-material chart
- index-mode comparison
- CSV export
- procurement cost impact calculator
- seeded random-walk demonstration market data

The v1.2.1 prototype remains historically useful for UI behavior, but it is no longer an accurate description of the current production data architecture.
