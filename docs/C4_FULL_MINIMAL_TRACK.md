# C4 Full Minimal Track — C4.0 to C4.7

Status: local research implementation scope. This track is baseline-only and
contains no ML, Production forecast, Canonical promotion, Google Sheets/A:L
write, procurement decision, or procurement signal.

Synthetic execution is a **Local Synthetic Proof-of-Pipeline** with the single
authoritative operational classification **SYNTHETIC_NON_OPERATIONAL**. It is
**Not Real Market_Observation_V2 Backtest**, **Not Production**, **Not
Canonical**, **Not Procurement Decision**, and is **Research Only**.

The formal C3 handoff sources remain `docs/C3_CLOSING_SUMMARY.md` and its
accepted implementation/closeout references. The working handoff commit is
`7aeddc3758d142fe4f563be5de8ba83fe04aec23`.

## Meaning of “full minimal”

Every C4 stage has one executable, auditable vertical slice and one explicit
result. “Full” means C4.0 through C4.7 are represented end to end. “Minimal”
means the stages share the existing C4.3 deterministic baseline engine and do
not add model training, external services, schedulers, databases, user-facing
Production views, or decision automation.

The track closes only local research implementation. It cannot close a
Production or Canonical C4 gate.

## Stage contract

### C4.0 — Contract freeze

Validates the tracked handoff documents and records the exact stage, horizon,
target, safety, and non-scope contract.

Output: `c4_0_contract_report.json`.

### C4.1 — Local data readiness

Reads only an explicitly supplied local CSV/JSON/JSONL file through the C4.3
adapter. Reports eligible and excluded observations. `LEGACY_UNVERIFIED`,
unapproved Canonical, weekend, invalid-quality, ambiguous-trust, missing
lineage/time/identity, non-positive, and conflicting-ID inputs fail closed or
are excluded as defined by the C4.3 contract.

Comparison series are isolated by the exact
`(material_id, source_id, currency, unit)` tuple.

Outputs: `c4_1_data_readiness_report.json`, `exclusion_report.json`, and
`lineage_report.json`.

### C4.2 — Point-in-time dataset

Uses each origin’s collector `available_at` as cutoff. Features must satisfy
`available_at <= cutoff` and `source_date <= origin_source_date`. Target rows
must be exactly 7, 14, or 28 calendar days later and become available after
the cutoff. Late revisions and neighbouring-date substitution are forbidden.

Outputs: `c4_2_point_in_time_report.json` and
`leakage_check_report.json`.

### C4.3 — Baseline lab

Reuses `scripts/c4_3_baseline_forecast_lab.py`. The only baselines are last
observation direction, moving-average trend, momentum direction, and
volatility flag. The only target domains are direction, volatility, and
research risk. Missing history remains `INSUFFICIENT_DATA`.

Outputs: `c4_3_stage_report.json`, `backtest_summary.json`, and
`research_forecasts.jsonl`.

### C4.4 — Baseline comparison

Aggregates evaluated/correct counts and descriptive accuracy by method and
horizon. It performs no model selection or promotion and gives no procurement
interpretation.

Output: `c4_4_baseline_comparison_report.json`.

### C4.5 — Multi-material Shadow research

Reports local forecast coverage by material, exact horizon, source, currency,
and unit. For synthetic validation input, its status is always
`SYNTHETIC_NON_OPERATIONAL`. No output is a market or procurement action.

Output: `c4_5_shadow_research_report.json`.

### C4.6 — Reproducibility

Rebuilds the C4.3 reports with the same input digest and generation timestamp
and compares canonical serialized output. It records code hashes and a
reproducibility fingerprint. No network or ML dependency is permitted.

Output: `c4_6_reproducibility_report.json`.

### C4.7 — Research closeout

Summarizes every stage and visible evidence gap. Possible successful states
are:

- `RESEARCH_TRACK_COMPLETE` for an eligible local Shadow export;
- `RESEARCH_TRACK_COMPLETE_SYNTHETIC_NON_OPERATIONAL` for a synthetic
  validation package; or
- `IMPLEMENTATION_COMPLETE_DATA_INSUFFICIENT` when the implementation works
  but no exact-horizon evaluation is possible.

`FAIL` is returned for a contract, leakage, or reproducibility failure.
Production C4 closeout and C5 procurement automation remain not authorized.

Outputs: `c4_7_research_closeout_report.json` and
`c4_full_minimal_result.md`.

## Input classification

The CLI requires an explicit classification:

- `SHADOW_RESEARCH_EXPORT`: a local export whose C3 identity, status,
  timestamp, trust, and lineage fields remain present.
- `SYNTHETIC_NON_OPERATIONAL`: generated fixtures used only to prove pipeline
  behavior. Results cannot be described as actual market performance.

There is no default classification. Synthetic classification requires
synthetic evidence in every input row's lineage. Any row with synthetic
lineage is rejected when the CLI classification is `SHADOW_RESEARCH_EXPORT`;
changing a command-line flag can never promote a fixture into Shadow data.
The optional `fixture` marker is type-strict: only JSON boolean `true` is
positive fixture evidence. String values such as `"false"`, `"0"`, `"yes"`,
or `"no"` are rejected rather than interpreted by truthiness. Synthetic
outputs normalize every classification field to `SYNTHETIC_NON_OPERATIONAL`;
source trust metadata may remain separately identified as trust metadata but
is never an operational classification.

## Running the pipeline

```powershell
python scripts/c4_full_minimal_pipeline.py `
  --input C:\local\v2-export.jsonl `
  --input-classification SHADOW_RESEARCH_EXPORT
```

Each run creates a non-overwriting directory beneath:

`runtime/c4-full-minimal-track/<pipeline_run_id>/`

The directory contains all stage reports, C4.3 reports/forecasts, a Markdown
summary, and `run_manifest.json` with hashes for every generated artifact.
Every artifact carries:

- **Research Forecast Only**
- **Not Production**
- **Not Canonical**
- **Not Procurement Decision**

Every JSON/JSONL artifact and manifest also records:

- Google Sheets write: `DISABLED`
- Production A:L write: `DISABLED`
- Canonical promotion: `DISABLED`
- Deferred persistence: `DISABLED`
- ML model: `NONE`
- Procurement signal: `DISABLED`
- Procurement decision: `NOT_AUTHORIZED`

## Current data limitation

The repository does not contain a local `Market_Observation_V2` historical
export. The tracked `data/` files belong to the existing site and are not C3
V2 truth. The old `runtime/company-market/latest.json` snapshot is not silently
converted into history. Until a local Shadow export is explicitly supplied,
the pipeline can produce only synthetic validation results or an honest
`INSUFFICIENT_DATA` result.
