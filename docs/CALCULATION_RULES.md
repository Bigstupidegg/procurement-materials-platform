# Calculation Rules — v2.3 Baseline

This document defines the calculation logic that must remain traceable across UI, scripts, tests, and reports.

## 1. Percentage change

For current value `C` and previous/base value `B`:

```text
Change % = (C - B) / B × 100
```

If `B = 0`, the result is undefined and must not be silently converted to zero.

## 2. Index comparison

For mixed materials or units, normalize each series to a common starting value:

```text
Index_t = Price_t / Price_base × 100
```

Use index mode when comparing materials with different currencies or units. Do not plot unlike raw units on one axis as if directly comparable.

## 3. Observation windows

Current analytical windows:

- 1 month
- 3 months
- 6 months
- 12 months

Additional UI windows may include 3 years and 5 years for charting, but procurement signal rules should use explicitly documented windows.

## 4. Twelve-month statistics

For the most recent 12 monthly observations:

```text
average = mean(prices)
high = max(prices)
low = min(prices)
latest_vs_average_% = (latest - average) / average × 100
```

Range position:

```text
range_position_% = (latest - low) / (high - low) × 100
```

If `high = low`, range position is undefined and must be handled explicitly.

Monthly return volatility should be calculated from monthly percentage returns and use one documented standard-deviation convention consistently.

## 5. Source comparison

World Bank is the primary market source. FRED is corroboration only.

For aligned observations:

```text
difference = FRED - WorldBank
difference_% = (FRED - WorldBank) / WorldBank × 100
```

A mean absolute source difference may be calculated across the latest 12 aligned months:

```text
MAD % = mean(abs(difference_%))
```

Source agreement affects confidence only; it must not cause FRED to overwrite the World Bank trend.

## 6. Should-Cost — single component

For a component with cost share `S` and cost change `R`:

```text
Component Impact = S × R
```

When percentages are entered as human-readable percentages:

```text
Impact percentage points = (S% × R%) / 100
```

Example:

```text
Material share = 35%
Material change = +6%
Material impact = 35 × 6 / 100 = +2.10 percentage points
```

## 7. Should-Cost — total expected change

Current linear additive model:

```text
Expected Price Change
= Raw Material Impact
+ Processing Impact
+ Energy Impact
+ Other Cost Impact
+ FX Exposure Impact
```

The base cost shares for raw material + processing + energy + other should not exceed 100%.

FX exposure is currently treated separately because it may overlap with other cost buckets. The user must explicitly avoid double counting.

## 8. Expected new price

For current finished-product price `P` and expected change `E`:

```text
Expected New Price = P × (1 + E)
```

Where `E` is expressed as a decimal.

Example:

```text
P = 1000
E = 0.10
Expected New Price = 1100
```

## 9. Supplier requested increase

```text
Supplier Requested Increase %
= (Requested Price - Current Price) / Current Price × 100
```

When the supplier directly supplies a percentage, retain both the stated percentage and the percentage recomputed from prices when both prices are available. Differences should be surfaced, not hidden.

## 10. Negotiation gap

```text
Negotiation Gap
= Supplier Requested Increase
- Expected Price Change
```

Interpretation:

- near zero: supplier request roughly matches modeled inputs
- positive gap: supplier asks more than modeled impact
- negative gap: supplier asks less than modeled impact

This is evidence for negotiation, not an automatic accept/reject decision.

## 11. Supplier rationality thresholds

Current rule file defines thresholds in percentage points:

```text
modelMatchTolerancePercentagePoints = 0.5
requestEvidenceGapPercentagePoints = 1.5
highChallengeGapPercentagePoints = 3.0
```

These thresholds should remain configuration-driven and must not be duplicated as unrelated hard-coded values in multiple UI modules.

## 12. Market signal principles

Known signal classes include:

- `VERIFY_STRONG_INCREASE`
- `VERIFY_INCREASE`
- `NEGOTIATE_REDUCTION`
- `CHALLENGE_INCREASE`

The market signal should be derived from World Bank trend metrics. FRED contributes corroboration confidence only.

The signal communicates market direction and an appropriate procurement action, not the supplier's true cost.

## 13. TTP Radiator model

Target expanded model:

```text
Expected Change
= Steel Share × Steel Change
+ Zinc Share × Zinc Change
+ Packaging Share × Packaging Change
+ Energy Share × Energy Change
+ Labor Share × Labor Change
+ FX Impact
+ Freight Impact
```

Historic analytical references discussed:

```text
CR Coil 1.00 mm: ~INR 74,050/t → ~INR 81,720/t
Steel market change: ~+10%
Prior inferred radiator impact from steel: ~+6.2 percentage points
```

The inferred cost share must never be treated as verified supplier BOM without evidence.

## 14. Bushing material-basket model

Target model should support multiple materials such as:

- Copper
- Silver
- Resin

For material `i`:

```text
Material Basket Impact
= Σ(Material Share_i × Material Change_i)
```

If only an aggregate material-basket change is available:

```text
Material Impact
= Aggregate Material Cost Share × Aggregate Material Basket Change
```

Historic example discussed:

```text
Material share = 57%
Aggregate material change = +43%
Simple material impact = 57 × 43 / 100 = +24.51 percentage points
```

This still requires verification of base period, procurement lag, inventory, FX treatment, and whether the basket actually matches the supplier product.

## 15. Double-counting controls

Before adding impacts, check whether one input already contains another.

Common risk examples:

- material price already includes FX movement
- freight embedded in delivered material quotation
- energy surcharge included in processing rate
- supplier's 'incoming cost' already aggregates several cost categories

When overlap cannot be quantified, report an uncertainty range rather than forcing a single precise result.

## 16. Rounding

Recommended policy:

- internal calculations: retain full precision
- displayed percentages: normally 2 decimals
- prices: follow currency-specific precision
- comparisons: calculate from unrounded values

Do not round intermediate values before final aggregation unless a contract explicitly requires it.

## 17. Auditability requirement

Any number shown in a Negotiation Report should be traceable to:

```text
source
source period
raw value
transformation / formula
cost share or business assumption
final derived value
```

If a value is manually entered or inferred, label it as an assumption rather than source data.
