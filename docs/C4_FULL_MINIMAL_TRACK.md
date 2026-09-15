# C4 Full Minimal Track — C4.0 to C4.7

Scope: **Local Synthetic Proof-of-Pipeline only**. The only authorized input
has `data_origin = SYNTHETIC_FIXTURE`; dataset and operational status are both
`SYNTHETIC_NON_OPERATIONAL`.

The boundary is explicit: **Not real Market_Observation_V2 backtest**, **Not
Shadow export validation**, **Not Production**, **Not Canonical**, **Not
Procurement Decision**, **Not Procurement Signal**, and **Not C4 closeout**.
This track is local-only, baseline-only, research-only, synthetic-only, and
non-operational. It contains no ML, Google Sheets/A:L write, Production
forecast, Canonical promotion, Deferred persistence, procurement decision, or
procurement signal.

The **Real Data Readiness Gate** is still required before any real
Market_Observation_V2 or Shadow export use. The **Production Forecast Gate**
is future-only and requires separate Human Gate approval. Merge is not
authorized unless a later Web Human Gate explicitly approves it.

The formal C3 handoff sources remain `docs/C3_CLOSING_SUMMARY.md` and its
accepted implementation/closeout references. The working handoff commit is
`7aeddc3758d142fe4f563be5de8ba83fe04aec23`.

## Meaning of “full minimal”

Every C4 stage has one executable, auditable vertical slice and one explicit
result. “Full” means C4.0 through C4.7 are represented end to end. “Minimal”
means the stages share the existing C4.3 deterministic baseline engine and do
not add model training, external services, schedulers, databases, user-facing
Production views, or decision automation.

The track supplies only local synthetic implementation evidence. It does not
close a research, Production, Canonical, procurement, or other C4 gate.

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

### C4.5 — Multi-material synthetic research coverage

Reports synthetic forecast coverage by material, exact horizon, source,
currency, and unit. Its status is always `SYNTHETIC_NON_OPERATIONAL`. The
historical stage filename is retained for package compatibility; it does not
mean a Shadow export was accepted or validated. No output is a market or
procurement action.

Output: `c4_5_shadow_research_report.json`.

### C4.6 — Reproducibility

Rebuilds the C4.3 reports with the same input digest and generation timestamp
and compares canonical serialized output. It records code hashes and a
reproducibility fingerprint. No network or ML dependency is permitted.

Output: `c4_6_reproducibility_report.json`.

### C4.7 — Synthetic evidence summary

Summarizes every stage and visible evidence gap. The only non-failure result
in this PR is `RESEARCH_TRACK_COMPLETE_SYNTHETIC_NON_OPERATIONAL`. The word
`COMPLETE` in this status means only that the local synthetic pipeline ran; it
does not mean real-data validation, production readiness, or C4 closeout.

`FAIL` is returned for a contract, leakage, or reproducibility failure.
Production C4 closeout and C5 procurement automation remain not authorized.

The historical output filename is retained for package compatibility; it is
not authorization or a closeout claim.

Outputs: `c4_7_research_closeout_report.json` and
`c4_full_minimal_result.md`.

## Input classification

The CLI requires the single explicit classification
`SYNTHETIC_NON_OPERATIONAL`, for generated fixtures used only to prove local
pipeline behavior. Results cannot be described as actual market performance.

There is no default classification. Synthetic classification requires
synthetic evidence in every input row's lineage. `SHADOW_RESEARCH_EXPORT` and
other real/Shadow export classifications fail closed with
`REAL_DATA_READINESS_GATE_REQUIRED`; changing a command-line flag can never
promote a fixture or enable real data.
The optional `fixture` marker is type-strict: only JSON boolean `true` is
positive fixture evidence. String values such as `"false"`, `"0"`, `"yes"`,
or `"no"` are rejected rather than interpreted by truthiness. Synthetic
outputs normalize every classification field to `SYNTHETIC_NON_OPERATIONAL`;
source trust metadata may remain separately identified as trust metadata but
is never an operational classification.

## Running the pipeline

```powershell
python scripts/c4_full_minimal_pipeline.py `
  --input C:\local\c4-synthetic-fixture.jsonl `
  --input-classification SYNTHETIC_NON_OPERATIONAL
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

The repository does not contain an authorized `Market_Observation_V2`
historical export for this PR. The tracked `data/` files belong to the existing
site and are not C3 V2 truth. The old `runtime/company-market/latest.json`
snapshot is not silently converted into history. Real Market_Observation_V2 /
Shadow research export support is deferred to the Real Data Readiness Gate and
is not implemented or authorized in this PR.
