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
- `tests/test_prepare_site.py` release-identity and production-path regression tests.
- `.github/workflows/quality-check.yml` for pull-request tests, JS syntax checks, site build, release identity, and Demo-leakage guards.
- Generated production-only `_site/assets/app-core.js`, containing calculator/navigation logic without the seeded market simulator.

### Changed

- README rewritten to reflect the repository's real implementation rather than the obsolete Phase 2 seed-data state.
- Project positioning changed from `v1.2.1 Prototype / Demo Data` to `v2.3 Real Data + Procurement Decision Support`.
- `scripts/prepare_site.py` now reads release metadata and normalizes the built site to the v2.3 identity.
- Production build now replaces the legacy `assets/app.js` reference with `assets/app-core.js` and removes `_site/assets/app.js`.
- Market cards, chart, tooltip, statistics, and CSV in production are owned by the validated World Bank real-data module rather than the seeded Demo engine.
- Built `world-bank-live.js` release wording is normalized from legacy v1.3.0 text to the active release version.
- Pages deployment now rejects a build if the seeded Demo engine appears in the production core or production HTML.
- World Bank documented as the primary market source.
- FRED documented as independent corroboration only.
- Supplier price analysis documented as decision support, not automatic acceptance/rejection.

### Known technical debt

- Source `assets/app.js` still contains the original v1.2.1 seeded random-walk demo engine and retains an ambiguous production-sounding filename; it is no longer part of the built production runtime.
- Source `assets/world-bank-live.js` still contains a legacy v1.3.0 literal; the v2.3 build currently normalizes the copied deployment asset through `prepare_site.py`.
- Browser-level source-consistency tests for cards/chart/tooltip/CSV are still pending.
- Production UI should expose latest-period / synchronization freshness more prominently.

### Planned before v2.3 completion

- rename/restructure the legacy Demo fixture at source level
- add browser-level cards/chart/tooltip/CSV source-consistency coverage
- more prominent latest-period / sync-status UI
- consolidate duplicated rule/config thresholds where applicable
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
