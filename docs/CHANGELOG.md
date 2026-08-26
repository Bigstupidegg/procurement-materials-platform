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
- `tests/test_prepare_site.py` release-identity regression tests.
- `.github/workflows/quality-check.yml` for pull-request tests, JS syntax checks, site build, and release-identity verification.

### Changed

- README rewritten to reflect the repository's real implementation rather than the obsolete Phase 2 seed-data state.
- Project positioning changed from `v1.2.1 Prototype / Demo Data` to `v2.3 Real Data + Procurement Decision Support`.
- `scripts/prepare_site.py` now reads release metadata and normalizes the built site to the v2.3 identity.
- Built `world-bank-live.js` release wording is normalized from legacy v1.3.0 text to the active release version.
- World Bank documented as the primary market source.
- FRED documented as independent corroboration only.
- Supplier price analysis documented as decision support, not automatic acceptance/rejection.

### Known technical debt

- `assets/app.js` still contains the original v1.2.1 seeded random-walk demo engine.
- Source `assets/world-bank-live.js` still contains a legacy v1.3.0 literal; the v2.3 build currently normalizes the copied deployment asset through `prepare_site.py`.
- The deployed site relies on additional real-data modules injected by `scripts/prepare_site.py`.
- Demo Mode and Real Data Mode must be fully separated during v2.3.
- Production UI must avoid mixed messaging where real market data and legacy demo labels appear together.

### Planned before v2.3 completion

- more prominent latest-period / sync-status UI
- removal of the legacy demo engine from the production data path
- source-consistency regression tests
- clearer fail-state if real-data files cannot be loaded
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
