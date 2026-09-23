# C4-RD-6.4B Core PIT Dataset Builder

## Status and boundary

The RD-6.4B builder is deterministic, in-memory, synthetic-only research
infrastructure. It does not acquire provider data, access Google Sheets,
persist Canonical or Deferred records, run backtests, calculate realized
labels, forecast, train models, or emit procurement recommendations.

An included row is always marked `SYNTHETIC_NON_OPERATIONAL`. Inclusion in a
synthetic dataset is not operational authorization. The repository's nine
authorization flags remain `False`; PIT, research, and backtest source
allowlists remain empty; all 15 RD-3 blockers remain open.

## Public API

The additive implementation is in `scripts/c4_rd_pit_dataset.py` and exposes:

- `PITFeatureDefinition`
- `PITDatasetRequest`
- `PITFeatureCandidate`
- `PITFeatureSnapshot`
- `PITDatasetRow`
- `PITDatasetManifest`
- `PITDatasetResult`
- `build_pit_dataset(requests, candidates)`

All logical result objects are frozen, slotted dataclasses. Caller mappings and
sequences are snapshotted into immutable representations before they enter a
semantic projection. Runtime metadata is accepted only for audit/test
isolation and is never included in feature, row, or manifest hashes.

The builder does not add a persistent RD schema. These are local logical
objects only.

## Authoritative interfaces reused

The builder does not reimplement RD-4 or RD-5 authority.

It consumes exact instances of:

- RD-4.1 `Observation` and `ObservationVersion`;
- RD-4.2 `ReadinessEvaluationResult`;
- RD-5 `ResearchExportDecision`.

For every candidate it verifies identity and content-hash parity across those
objects. It then calls the public RD-5 `build_research_export_manifest` path
with the independently supplied RD-4.2 result. That path validates the private
RD-5 authority snapshot against the independent result. A payload claim, a
well-formed hash, or a reconstructed decision cannot replace that authority
chain.

The existing RD-4.1 canonicalization implementation is reused through
`canonical_hash`, `canonical_json_bytes`, `canonical_timestamp`, and
`parse_rfc3339`.

## Pipeline

The implemented pipeline is:

1. snapshot and validate dataset requests;
2. validate every RD-4/RD-5 candidate authority binding;
3. group candidates by subject, cutoff, and feature definition;
4. make post-cutoff versions invisible;
5. retain semantically valid, non-quarantined candidates;
6. select the latest authoritative version available at or before the cutoff;
7. hard-fail unresolved equal-authority ambiguity;
8. construct present or explicit-missing feature snapshots;
9. enforce the row-level no-look-ahead invariant;
10. hash and deduplicate rows;
11. order rows deterministically;
12. build a typed `MANIFEST_CONTENT` manifest;
13. return an immutable in-memory result.

No input order, mapping order, filesystem order, arbitrary hash order, system
clock, or random value is used as an authority tie-breaker.

## PIT selection

Candidate PIT availability comes only from the RD-4/RD-5-bound
`feature_available_at_max`. `Observation.source_available_at` is separate
source-publication authority: the builder verifies that it matches the RD-5
decision's `temporal_context.source_available_at`, but does not require it to
equal feature availability. No timestamp is inferred from either field.

For compatibility, `PITFeatureSnapshot.source_available_at` stores the
authoritative **feature** availability timestamp selected from
`feature_available_at_max`; the field name is not a claim that it stores the
Observation source-publication timestamp.

For a cutoff of 10:30, candidates available at 09:00 and 10:00 are visible;
a candidate available at 12:00 is invisible. The 10:00 version is selected.

If multiple distinct authoritative candidates remain at the same latest
availability timestamp, the build raises
`AMBIGUOUS_AUTHORITATIVE_CANDIDATE`. It never chooses the first candidate or
uses a representation hash as a semantic tie-breaker.

## Missing features and failure classes

Feature definitions explicitly declare `MANDATORY` or `OPTIONAL`.

- Missing mandatory feature: `ROW_EXCLUDE` with
  `MANDATORY_FEATURE_UNAVAILABLE`.
- Missing optional feature: an `EXPLICIT_MISSING` snapshot whose value and
  source lineage are null.
- Any relevant candidate with unresolved authoritative
  `feature_available_at_max`: `QUARANTINE` with
  `FEATURE_AVAILABILITY_UNRESOLVED`, even when another relevant candidate has
  known availability and even when the feature is optional.
- Existing but semantically unsafe candidate: `QUARANTINE`.
- Invalid authority binding, ambiguous latest candidate, future included
  feature, or conflicting duplicate row content: `PITDatasetHardFail`.

There is no zero, forward, backward, mean, median, nearest-future, or guessed
timestamp imputation.

## Hash and lineage model

### Feature

`PITFeatureSnapshot.content_projection()` contains only semantic fields:

- feature definition identity/version;
- computation profile version;
- research cutoff;
- value state and value;
- source observation/version identity;
- observed and available timestamps;
- source profile identity/version;
- evidence references;
- RD-4 evaluation hash;
- RD-5 decision hash and authority binding reference.

Its hash is:

```text
canonical_hash("PIT_FEATURE_CONTENT", semantic_feature_projection)
```

### Row identity

The row identity projection is exactly:

```text
dataset_contract_version
research_subject_id
observation_version_id
research_cutoff_at
feature_set_version
feature_computation_profile_version
cutoff_policy_version
```

Its ID is:

```text
canonical_hash("PIT_DATASET_ROW_ID", identity_projection)
```

Changing feature content without changing this identity keeps the row ID and
changes the row content hash.

### Row content

Row semantic content includes the row ID and identity, ordered features,
maximum feature availability, RD-4/RD-5 bindings, RuleBundle and SourceProfile
bindings, label specification, frozen authorization snapshot, and explicit
non-operational status.

Its hash is:

```text
canonical_hash("PIT_DATASET_ROW_CONTENT", row_content_projection)
```

### Dataset manifest

The typed manifest uses:

```text
manifest_type = C4_RD6_PIT_DATASET
manifest_version = 1.0.0
canonical_hash("MANIFEST_CONTENT", manifest_content_projection)
```

No `PIT_DATASET_ID` domain exists. Dataset identity is exactly the manifest
hash.

The manifest also binds a deterministic `request_scope`. Each entry is exactly
the corresponding `PITDatasetRequest.semantic_projection()`: dataset contract,
subject, observation version, cutoff, feature-set and computation versions,
cutoff policy, ordered feature definitions, label specification, and execution
mode. Identical semantic requests are deduplicated. Entries are ordered by
cutoff, subject, observation version, then canonical projection bytes. Runtime
metadata is excluded. Consequently, different zero-row request populations
still produce different dataset identities, while duplicate or permuted
semantic requests do not change identity.

## Ordering and duplicates

Feature order follows the request's frozen feature-definition order. It is not
blanket-sorted.

Rows use representation ordering only:

```text
research_cutoff_at
research_subject_id
row_id
```

Same row ID and same row content hash is deterministically deduplicated and
recorded as a diagnostic. Same row ID with different content raises
`ROW_ID_CONTENT_CONFLICT` and stops the whole build.

## No-look-ahead invariant

Every included row stores `feature_available_at_max` and
`research_cutoff_at`. `PITDatasetRow` independently validates:

```text
feature_available_at_max <= research_cutoff_at
```

A violation raises `FEATURE_AVAILABLE_AFTER_CUTOFF`; it cannot be downgraded
to a warning, exclusion, or quarantine.

## Feature/label firewall

The request stores a default-deny label definition/reference specification.
It accepts exactly three keys—`label_definition_id`, `label_horizon`, and
`label_reference_id`—and every value must be a non-empty string. Missing keys,
extra keys, nested payloads, nulls, numeric values, empty strings, realized
labels, future prices/returns, realized volatility, and post-cutoff evaluation
results are rejected at the request boundary. This remains a local logical
contract and does not add a persistent schema.

Feature construction reads only the selected
`ObservationVersion.semantic_data[value_key]`. It does not read the label
specification or import C4.3 realized-target/backtest code.

## Synthetic and operational behavior

Synthetic candidates can pass the complete semantic selection, lineage,
hashing, row, and manifest path. They remain
`SYNTHETIC_NON_OPERATIONAL` even when semantically valid.

An operational execution request produces no included row while current
controls are disabled. Shadow or Legacy trust cannot auto-include; a validly
bound but unsafe candidate is quarantined.

## Tests

`tests/test_c4_rd_pit_dataset.py` uses visibly synthetic fixtures and covers:

- positive deterministic dataset construction;
- mapping/input/runtime-metadata permutation;
- latest-known-as-of-cutoff and future-version invisibility;
- mandatory/optional missing behavior and no imputation;
- ambiguity and no-look-ahead hard failures;
- independent RD-4/RD-5 authority mismatch failures;
- Shadow, Legacy, and operational fail-closed behavior;
- deterministic feature and row ordering;
- duplicate deduplication and conflict rejection;
- feature/label firewall;
- unresolved-availability quarantine semantics;
- independent source and feature availability authority;
- exact default-deny label specification shape;
- deterministic request-scope identity, deduplication, and ordering;
- exact approved hash domains and typed manifest identity.

No focused test requires network, provider, database, or Sheet access.

## Rollback

The implementation is additive and has no migration or persistent output.
Rollback is Git-only: revert the RD-6.4B implementation commit. No Sheet,
database, Production, or historical-data repair is involved.
