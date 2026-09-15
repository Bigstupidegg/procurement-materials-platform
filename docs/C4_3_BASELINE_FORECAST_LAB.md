# C4.3 Baseline Forecast Lab

Status: minimal local research implementation. It is not a Production,
Canonical, machine-learning, procurement-decision, or procurement-signal
capability.

This is **Research Only**. When the Full Minimal Track invokes it with the
explicit fixture classification, the resulting package is a **Local Synthetic
Proof-of-Pipeline** classified **SYNTHETIC_NON_OPERATIONAL**. It is **Not Real
Market_Observation_V2 Backtest**, **Not Production**, **Not Canonical**, and
**Not Procurement Decision** evidence.

Every generated artifact carries all four notices:

- **Research Forecast Only**
- **Not Production**
- **Not Canonical**
- **Not Procurement Decision**

## Handoff and safety boundary

The formal tracked C3 handoff is `docs/C3_CLOSING_SUMMARY.md`, supported by
`docs/C3_2_7_CLOSEOUT.md`. The C4.3 working reference is
`7aeddc3758d142fe4f563be5de8ba83fe04aec23`; the accepted C3 implementation
and closeout commits remain the values recorded in the closing summary.

The lab reads one explicitly supplied local file and writes only beneath the
ignored `runtime/c4_3-baseline-forecast-lab/` directory. It has no Google
Sheets client, network client, Production writer, Canonical store, Deferred
Assembly writer, or procurement action. It forces the inherited C3 Sheet
write controls off in its own process.

## Local input contract

The command accepts UTF-8 CSV, JSONL, or JSON. JSON is either an array of
observation objects or an object whose `observations` member is that array.
Required calculation fields are:

- `observation_id`
- `material_id`
- `source_id`
- `currency`
- `unit`
- `source_date` (`YYYY-MM-DD`)
- `price` (or `value`)
- a timezone-aware `available_at`, or C3 V2 `observation_at`
- `source_status` (`SUCCESS` or `RETRY_SUCCESS`)
- `date_parse_status` (`PARSED`)
- Shadow trust evidence through `trust_state`, `canonical_status`, or
  `data_classification`

The following audit fields are retained in output:

- `observation_id`, `source_date`, `observed_at`, and `available_at`
- `quality_status`
- `trust_state`
- `lineage`

Lineage must be provable. An explicit non-empty lineage object is accepted.
Otherwise at least one approved C3 fallback identifier (`record_id`, `run_id`,
`collector_version`, or `migration_version`) must be present. A placeholder
lineage whose identifiers are all `null` is forbidden and is excluded as
`MISSING_PROVABLE_LINEAGE`.

For a direct C3 V2-shaped export, the explicit mapping is:

| C4 research field | C3 basis |
| --- | --- |
| `available_at` | `observation_at`, defined by C3.2-6 as first availability to the collector |
| `observed_at` | Preserved only when supplied; otherwise `null`, never invented |
| `quality_status` | Structured preservation of `source_status`, `date_parse_status`, `anomaly_status`, `observation_kind`, and `canonical_status` |
| `trust_state` | `SHADOW` / `SHADOW_UNRESOLVED` becomes `SHADOW_RESEARCH_ONLY` |
| `lineage` | Supplied lineage object, or explicit C3 fields including `record_id`, `run_id`, collector version, and migration version |

`observation_at` is collector availability, not an official source-native
publication timestamp. Source-native availability remains **NOT VERIFIED**.
No missing timestamp or status is inferred from a neighbouring row or date.

## Eligibility and exclusions

- `LEGACY_UNVERIFIED` is always excluded from both features and realized
  research targets.
- Canonical input is excluded because Canonical promotion is not approved.
- Only observations mapped to `SHADOW_RESEARCH_ONLY` are eligible.
- Weekend source dates are excluded and never shifted to a neighbouring date.
- A comparison series is bound to the exact
  `(material_id, source_id, currency, unit)` tuple; sources or units are never
  mixed.
- Non-success source status, unparsed source date, missing/naive availability
  timestamp, non-positive/invalid numeric value, missing identity/status, and
  ambiguous trust are excluded and reported.
- A duplicate ID with identical content is excluded as duplicate-same. A
  duplicate ID with different content fails closed.

Shadow results remain research observations, not training truth, Canonical
truth, or Production truth. The word `truth` in output means only the later
research evaluation row used by the local backtest.

## Horizons, targets, and baselines

The only horizons are 7, 14, and 28 calendar days. Each realized evaluation
requires an observation with exactly:

`target_source_date = origin_source_date + horizon_days`

Missing exact dates are excluded; the lab never substitutes a prior, next, or
local business date.

The only target domains are direction, volatility, and research risk:

- Direction is `UP`, `DOWN`, or `FLAT` from the origin value to the exact
  future value.
- Volatility is `ELEVATED` when the forward-window return dispersion exceeds
  the point-in-time trailing-window dispersion; otherwise `NORMAL`.
- Research risk is `ELEVATED` when forward volatility is elevated or the
  absolute horizon return exceeds the largest point-in-time trailing absolute
  return; otherwise `NORMAL`.

The non-ML baselines are:

- Last observation direction: sign of the last point-to-point change known at
  the origin.
- Moving-average trend: origin value versus the simple average inside the
  matching trailing calendar window.
- Momentum direction: origin value versus the exact source date one horizon
  earlier.
- Volatility flag: trailing-window dispersion versus the preceding,
  non-overlapping window of equal calendar length.

The research risk flag is elevated when the volatility baseline is elevated
or the available direction baselines disagree. Missing minimum history is
reported as `INSUFFICIENT_DATA`; it is not guessed.

## No-look-ahead contract

For each immutable forecast origin, its collector `available_at` is the
point-in-time cutoff. Feature construction permits only observations whose:

- `available_at <= cutoff`; and
- `source_date <= origin_source_date`.

Versions arriving after the cutoff cannot enter the feature set even when
their source date is older. The realized research row must have the exact
future source date, an availability time after the cutoff, and an observation
ID absent from the feature set. Every evaluated forecast records these checks
in `leakage_check_report.json`. Any observed violation makes the run fail.

## Usage and local artifacts

Example:

```powershell
python scripts/c4_3_baseline_forecast_lab.py --input C:\local\v2-export.jsonl
```

The lab creates a non-overwriting run directory under
`runtime/c4_3-baseline-forecast-lab/<run_id>/` containing:

- `run_manifest.json`
- `research_forecasts.jsonl`
- `backtest_summary.json`
- `leakage_check_report.json`
- `exclusion_report.json`
- `lineage_report.json`

The manifest records file hashes, input digest, algorithm version, handoff
reference, safety state, horizons, and target scope. Reports omit observed
numeric prices while retaining identity, time, quality/trust, and lineage
metadata needed to reproduce the selection path.

Every JSON/JSONL artifact and manifest asserts that Google Sheets write,
Production A:L write, Canonical promotion, Deferred persistence, and
procurement signal are `DISABLED`; the ML model is `NONE`, and procurement
decision is `NOT_AUTHORIZED`.

If no exact-horizon case can be evaluated, the run and manifest report
`INSUFFICIENT_DATA`; the leakage check reports
`NOT_EVALUATED_INSUFFICIENT_DATA`. An empty evaluation is never called a
completed backtest.
