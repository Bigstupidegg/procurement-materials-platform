# Roadmap

## v2.3 — Project Foundation & Maintainability

Goal: turn the existing real-data and procurement-analysis implementation into a maintainable formal baseline before adding more supplier-specific features.

### Phase A — Documentation baseline

Status: complete for initial v2.3 baseline

- [x] Refresh README to reflect actual repository state.
- [x] Add Project Knowledge Base.
- [x] Add Data Dictionary.
- [x] Add Calculation Rules.
- [x] Add explicit Data Source Policy.
- [x] Add Roadmap.
- [x] Add Changelog.

### Phase B — Version and deployment identity

Status: complete for v2.3 baseline

- [x] Establish a machine-readable release metadata file.
- [x] Make the built GitHub Pages payload identify itself as v2.3.0.
- [x] Remove inaccurate deployed-page claims that the site has no external market data.
- [x] Keep clear decision-support disclaimers.
- [x] Add PR Quality Check for tests, JS syntax, site build, and release identity.
- [x] Show latest market period / synchronization status from `data/status.json` prominently through the freshness panel.

### Phase C — Development Demo / Production Real Data separation

Status: source and production runtime boundaries implemented; browser-level regression coverage remains.

- [x] Remove seeded random-walk generation from the built production core path.
- [x] Create source-level `assets/app-core.js` for Should-Cost and navigation only.
- [x] Create source-level `assets/demo-market.js` as an explicit Development Demo fixture.
- [x] Reduce `assets/app.js` to a Development Demo bootstrap with no business logic.
- [x] Ensure production cards, chart, tooltip, statistics, and CSV are owned by the validated World Bank real-data module.
- [x] Remove `app.js` and `demo-market.js` from the built `_site` artifact.
- [x] Add regression tests and CI guards that reject Demo leakage into production.
- [x] Keep a fail-closed state when required World Bank/status data cannot be loaded or validated.
- [x] Add a freshness/status panel backed by same-origin `data/status.json`.
- [ ] Add browser-level regression coverage for cards/chart/tooltip/CSV source consistency.
- [ ] Add visual regression coverage for loading, success, stale, and fail-closed states.

### Phase D — Rule/config consolidation

Next target after Phase C browser regression coverage.

- [ ] Keep supplier-rationality thresholds in configuration.
- [ ] Audit UI modules for duplicated hard-coded thresholds.
- [ ] Document signal thresholds and confidence rules.
- [ ] Add schema-version checks for derived JSON.
- [ ] Centralize source-role constants where duplicated across UI modules.
- [ ] Add tests proving UI decisions use configured thresholds rather than shadow constants.

### Phase E — Supplier Case foundation

- [ ] Add `cases/` schema.
- [ ] Add supplier master-data schema.
- [ ] Preserve source evidence and manual assumptions separately.
- [ ] Add case status, target, final price, and savings/avoidance fields.

## v2.4 — TTP Radiator Case

Target capabilities:

- JPC CR Coil 1.00 mm time series
- LME Zinc time series
- INR/USD
- Brent / energy context
- 2023 base index = 100
- MoM / QoQ / YoY
- steel-zinc correlation views
- supplier requested increase vs modeled increase
- evidence checklist
- negotiation gap and report

Important: inferred steel cost share must remain clearly labeled as an assumption until verified by BOM / cost breakdown.

## v2.5 — Bushing Material Basket

Target capabilities:

- Copper
- Silver
- Resin
- material basket weighting
- FX exposure control
- historical supplier request comparison
- double-counting warnings
- target / final price record

## v2.6 — Supplier Intelligence

Candidate supplier profiles:

- PCORE / Hubbell
- YASH
- REL
- ZDVolt
- TTP

Potential fields:

```text
supplier_id
company_name
country
manufacturing_locations
product_families
capacity_notes
lead_time_notes
public_financials
sourcing_risk
source_evidence
last_reviewed_at
```

## v3.x — Procurement Transaction Integration

Goal: connect market movement, supplier requests, and actual PO outcomes.

Target flow:

```text
Market Price History
→ Supplier Quotation / Increase Request
→ Negotiation Case
→ PO Actual Transaction Price
```

Candidate integrations:

- WPS / Excel exported procurement history
- latest PO and unit price
- part-number history
- supplier history
- same-day multi-price cases
- ERP export normalization

Questions the platform should eventually answer:

1. What did the market do?
2. What did the supplier ask for?
3. What did the model support?
4. What was negotiated?
5. What price was actually ordered?
6. How did actual purchasing performance compare with market movement?
