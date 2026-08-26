# Data Dictionary — v2.3 Baseline

This document defines the principal data files and fields used by the procurement materials platform. It is intentionally concise and should be updated whenever a JSON schema changes.

## 1. `data/status.json`

Purpose: latest synchronization and freshness status.

| Field | Type | Description |
|---|---|---|
| `schemaVersion` | integer | Schema version |
| `generatedAt` | ISO datetime | Status file generation time |
| `dataMode` | string | Primary data operating mode |
| `worldBank.status` | string | World Bank synchronization status |
| `worldBank.lastAttemptAt` | ISO datetime | Last World Bank attempt |
| `worldBank.lastSuccessAt` | ISO datetime | Last successful World Bank sync |
| `worldBank.latestPeriod` | `YYYY-MM` | Latest source period |
| `worldBank.sourceUpdatedOn` | date | Source workbook update date |
| `worldBank.sourceSha256` | string | Source file SHA-256 |
| `worldBank.sourceUrl` | URL | Approved source URL |
| `fred.status` | string | FRED synchronization status |
| `fred.role` | string | FRED analytical role |
| `fred.latestCommonPeriod` | `YYYY-MM` | Latest common period across series |
| `fred.latestAvailablePeriod` | `YYYY-MM` | Latest available period |
| `fred.seriesCount` | integer | Number of FRED series |
| `isStale` | boolean | Whether data is considered stale |
| `warnings` | array | Human-readable warnings |

## 2. `data/world-bank.json`

Purpose: primary market-price series from World Bank Pink Sheet.

Expected top-level fields:

| Field | Type | Description |
|---|---|---|
| `generatedAt` | ISO datetime | Generation time |
| `source` | string | Source identifier |
| `sourceUrl` | URL | Source workbook URL |
| `fileHash` / source hash | string | Source integrity hash |
| `series` | object | Material series keyed by material ID |

Each material series should preserve:

| Field | Type | Description |
|---|---|---|
| `nameZh` | string | Chinese name |
| `nameEn` | string | English name |
| `currency` | string | Currency |
| `unit` | string | Display unit |
| `points` | array | Monthly price observations |

Observation:

```json
{
  "date": "2026-07-01",
  "price": 1234.56
}
```

## 3. `data/fred.json`

Purpose: independent market comparison source.

Important principle: FRED is corroboration only and does not replace the World Bank primary trend.

Recommended fields per series:

| Field | Type | Description |
|---|---|---|
| `seriesCode` | string | FRED series code |
| `frequency` | string | Data frequency |
| `unit` | string | Source unit |
| `lastObservationDate` | date | Latest observation |
| `points` | array | Monthly observations |

## 4. `data/comparison.json`

Purpose: aligned World Bank / FRED comparison.

Recommended fields:

```text
materialId
period
worldBankValue
fredValue
difference
differencePercent
```

This file is derived data and should never be treated as an independent source of truth.

## 5. `data/signals.json`

Purpose: derived market-trend and procurement-negotiation signals.

Top-level concepts:

| Field | Description |
|---|---|
| `generatedAt` | Signal generation time |
| `isRealData` | Whether signal is based on real market data |
| `latestPeriod` | Latest evaluated period |
| `primarySource` | Primary signal source |
| `comparisonSource` | Corroboration source |
| `signalPolicy` | Signal governance rules |
| `thresholds` | Rule thresholds |
| `summary` | Aggregate signal counts |
| `materials` | Per-material signal details |

Per-material fields include:

```text
id
nameZh
nameEn
currency
displayUnit
sourceUnit
latestPeriod
latestValue
changes
twelveMonthStatistics
trend
negotiationSignal
sourceCorroboration
evidence
limitations
```

### `changes`

```text
oneMonthPercent
threeMonthPercent
sixMonthPercent
twelveMonthPercent
```

### `twelveMonthStatistics`

```text
average
high
low
latestVsAveragePercent
rangePositionPercent
positionLabel
monthlyReturnVolatilityPercent
```

### `negotiationSignal`

```text
code
label
priority
marketInterpretation
recommendedAction
supplierClaimCheck
```

Current known signal codes include:

- `VERIFY_STRONG_INCREASE`
- `VERIFY_INCREASE`
- `NEGOTIATE_REDUCTION`
- `CHALLENGE_INCREASE`

## 6. `data/should-cost-rules.json`

Purpose: rationality thresholds and policy for supplier price-request comparison.

Key fields:

| Field | Description |
|---|---|
| `isDecisionSupportOnly` | Must remain true for current design |
| `thresholds.modelMatchTolerancePercentagePoints` | Model-match tolerance |
| `thresholds.requestEvidenceGapPercentagePoints` | Evidence-request threshold |
| `thresholds.highChallengeGapPercentagePoints` | High-challenge threshold |
| `policy.automaticAcceptance` | Must remain false |
| `policy.automaticRejection` | Must remain false |
| `policy.rawMaterialChangeEqualsFinishedPriceChange` | Must remain false |
| `policy.worldBankRole` | World Bank analytical role |
| `policy.fredRole` | FRED analytical role |

## 7. Future Supplier Case schema

Target location: `cases/<case-id>/case.json`

```text
case_id              string
supplier             string
product_family       string
part_number          string|null
country              string
currency             string
old_price            number
requested_price      number
requested_change     number
request_date         date
effective_date       date|null
cost_breakdown       object
market_base_period   YYYY-MM
market_current_period YYYY-MM
should_cost_change   number
negotiation_gap      number
negotiation_target   number|null
final_price          number|null
status               string
evidence             array
notes                string
```

## 8. Schema governance

Whenever a production JSON schema changes:

1. Increment a schema version where applicable.
2. Update this document.
3. Update validation/tests before deployment.
4. Record the change in `docs/CHANGELOG.md`.
5. Avoid silently renaming material IDs or units.
