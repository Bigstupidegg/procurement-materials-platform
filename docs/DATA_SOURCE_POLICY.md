# Data Source Policy — v2.3

This policy governs external market data used by the platform.

## 1. Source roles

### Primary source

World Bank Pink Sheet is the current primary market source for the seven core commodities on the public GitHub Pages site.

A primary source may determine market trend and derived procurement signals only after:

- source-domain validation
- schema validation
- unit / currency validation
- period continuity checks
- minimum-history checks
- successful status update

### Comparison source

FRED is the current independent comparison source.

A comparison source may:

- corroborate direction
- measure source difference
- affect confidence labels

A comparison source must not silently overwrite the primary series or primary trend signal.

## 2. Provenance requirements

Each production dataset should preserve, where available:

```text
source identifier
source URL / instrument code
series code / workbook column
currency
unit
frequency
generated timestamp
observation timestamp
latest period
source update date
source hash
sync status
```

## 3. Staleness and failure

If a production source fails validation or synchronization:

- keep the last known valid dataset where appropriate
- mark status / warning clearly
- do not replace production data with demo data without an explicit Demo Mode indicator
- do not present an unverified newest value as valid

For the private company daily collector, a required quote failure must stop the operational full-row write.

## 4. Unit and currency rules

Never assume two series are directly comparable solely because they represent similar materials.

Before raw-price comparison, confirm:

- commodity definition
- grade / specification
- currency
- unit
- pricing basis
- delivery basis where applicable
- frequency
- period alignment

When units differ, use normalized index comparison where appropriate.

## 5. Private company daily source contract

The private Google Sheet workflow is separate from the public World Bank/FRED layer. Its verified source contract comes from the original `update_prices_v5.py` implementation and the current C3.1 hardening work.

| Google Sheet field | Source | Instrument / rule |
|---|---|---|
| Copper Cash | LME | Copper Cash `OFFER` |
| Copper 3M | LME | Copper `3-month` `OFFER` |
| Electrolytic Copper | SMM | `1#电解铜`, explicit average / `均價` |
| Aluminium | LME | Cash `OFFER` |
| Lead | LME | Cash `OFFER` |
| Nickel | LME | Cash `OFFER` |
| Tin | LME | Cash `OFFER` |
| Zinc | LME | Cash `OFFER` |
| Brent | yfinance | `BZ=F`, latest available `Close` |
| Silver | yfinance | `SI=F`, latest available `Close × 100` |
| Gold | yfinance | `GC=F`, latest available `Close` |

Operational notes:

- LME and SMM are retrieved through Selenium/browser automation.
- C3.1 identifies the LME `OFFER` column semantically rather than by fixed HTML position.
- C3.1 selects SMM electrolytic-copper average explicitly.
- yfinance is used for J/K/L exactly because the original Python script uses it for those three market references.
- `BZ=F`, `SI=F`, and `GC=F` are futures references, not spot prices.
- LME Copper Cash OFFER is the primary daily copper market reference for purchasing decision support.
- Private Google Sheet data is not a public GitHub Pages dataset.

## 6. Source additions

Potential future sources include JPC, additional LME-related references, FX, freight, wage, CPI, and other macroeconomic datasets.

Before adding a new source:

1. Define its analytical role.
2. Document licensing / access constraints.
3. Define the expected schema and units.
4. Add validation.
5. Add source status reporting.
6. Update `DATA_DICTIONARY.md`.
7. Update `CALCULATION_RULES.md` if the source affects formulas.
8. Add tests.

## 7. Procurement interpretation

Market data is evidence, not supplier cost truth.

Supplier price analysis must distinguish:

```text
Market observation
Supplier claim
Supplier cost structure
Business assumption
Derived calculation
Negotiation decision
```

These categories should not be merged into one unexplained number.

## 8. Security

API keys and company credentials must remain in protected local/CI configuration. They must not be committed to repository files, generated public JSON, browser JavaScript, commit messages, or logs.

Browser code should read prepared same-origin public data files rather than exposing protected source credentials. The company Google service-account credential and Sheet ID must remain outside the public repository.
