# Data Source Policy — v2.3

This policy governs external market data used by the platform.

## 1. Source roles

### Primary source

World Bank Pink Sheet is the current primary market source for the seven core commodities.

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
source URL
series code / workbook column
currency
unit
frequency
generated timestamp
latest period
source update date
source hash
sync status
```

## 3. Staleness and failure

If a production source fails validation or synchronization:

- keep the last known valid dataset
- mark status / warning clearly
- do not replace production data with demo data without an explicit Demo Mode indicator
- do not present an unverified newest value as valid

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

## 5. Source additions

Potential future sources include JPC, LME-related references, FX, freight, wage, CPI, and other macroeconomic datasets.

Before adding a new source:

1. Define its analytical role.
2. Document licensing / access constraints.
3. Define the expected schema and units.
4. Add validation.
5. Add source status reporting.
6. Update `DATA_DICTIONARY.md`.
7. Update `CALCULATION_RULES.md` if the source affects formulas.
8. Add tests.

## 6. Procurement interpretation

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

## 7. Security

API keys must remain in GitHub Actions secrets or equivalent protected CI configuration. They must not be committed to repository files, generated JSON, browser JavaScript, commit messages, or logs.

Browser code should read prepared same-origin data files rather than directly exposing protected source credentials.
