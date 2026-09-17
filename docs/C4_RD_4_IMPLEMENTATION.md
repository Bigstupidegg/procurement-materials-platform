# C4-RD-4.1 Contract Schemas and Core Domain Models

## Scope and status

C4-RD-4.1 is a local-only, source-neutral contract implementation. It defines structural schemas, immutable Python domain models, canonical serialization, deterministic hashing, and local validation. Contract version is `1.0.0`.

This implementation does not ingest real data, evaluate source readiness, authorize a source, export data, persist records, build datasets, run backtests, enable ML, or produce procurement decisions.

All examples and tests are `SYNTHETIC_FIXTURE` / `SYNTHETIC_NON_OPERATIONAL`.

## Schemas

All schemas use JSON Schema Draft 2020-12, stable `urn:c4-rd:schema:<name>:1.0.0` identifiers, local URN references, and closed top-level object shapes.

- `c4_common.schema.json`
- `c4_observation.schema.json`
- `c4_observation_version.schema.json`
- `c4_calendar_reference.schema.json`
- `c4_availability_assessment.schema.json`
- `c4_lineage_assessment.schema.json`
- `c4_trust_quality_assessment.schema.json`
- `c4_pit_eligibility.schema.json`
- `c4_research_eligibility.schema.json`
- `c4_backtest_eligibility.schema.json`
- `c4_rule_bundle.schema.json`
- `c4_readiness_manifest.schema.json`
- `c4_export_contract.schema.json`

Unknown schema IDs and unsupported major versions fail closed. Schema references are resolved exclusively from the approved local schema directory.

C4-RD-4.1.5 changes no schema. The architecture remediation changes Python state storage and corrects the supported threat model; schema payloads and contract semantics remain unchanged.

## Frozen enums

The implementation freezes the approved values for:

- trust state
- quality status
- readiness state
- eligibility state
- source period type
- calendar role
- calendar role status
- availability basis

Aliases are not accepted. In particular, `SYNTHETIC_FIXTURE` is an origin classification and is not a trust state. A synthetic TrustQualityAssessment must use `TRUST_REJECTED` with `TRUST_STATE_NOT_APPROVED`.

## Date, time, and source-period semantics

Timestamp fields require timezone-aware RFC3339 with zero to six fractional digits. Every model normalizes its contract `_at` fields during immutable construction, before identity or content projection. Canonical timestamps are therefore stored as UTC `Z`; a zero fractional part is omitted and non-zero fractional trailing zeros are removed. Naive timestamps are rejected. Nested semantic mappings apply the same rule to keys ending in `_at`.

Date fields accept `YYYY-MM-DD` only. Dates and timestamps are not interchangeable.

Represented-period rules are:

- `DAILY_MARKET` and `SNAPSHOT`: `source_market_date` is required and period bounds are null.
- `MONTH`: market date is null and the bounds must be the exact calendar month.
- `QUARTER`: market date is null and the bounds must be the exact calendar quarter.
- `OTHER_BOUNDED_PERIOD`: market date is null, both bounds are required, and start cannot follow end.

Represented period does not imply publication or availability.

## Calendar assignments

Every Observation contains exactly one assignment for each role, in frozen canonical order:

1. `MARKET`
2. `PUBLICATION`
3. `LOCAL_OPERATIONAL`
4. `SCHEDULER`

`RESOLVED` requires all reference fields. `NOT_APPLICABLE` requires all reference fields to be null. Reusing one CalendarReference across roles still requires separate assignments.

Every nested assignment must be an exact, already validated `CalendarAssignment` value. Duck-typed or pseudo-record objects are rejected even when they expose matching role and reference attributes. Accepted assignments are immutable validated value objects; caller-state isolation applies after this constructor type validation succeeds.

## Immutable domain models and supported trust boundary

The public semantic models are frozen, slotted dataclass value types:

- Observation and ObservationVersion
- CalendarReference and CalendarAssignment
- AvailabilityAssessment, LineageAssessment, and TrustQualityAssessment
- PITEligibilityAssessment and ResearchEligibilityAssessment
- BacktestRecordEligibilityAssessment and BacktestDatasetEligibilityAssessment
- RuleBundle, ExclusionReason, and ReadinessManifest

Ordinary construction validates and normalizes every input before returning an accepted value. Semantic fields are stored directly in the returned object; there is no external `id(obj)` registry, closure-private semantic store, or `functools.partial` constructor authority. Schema payloads, identity projections, stable IDs, and content hashes derive from those embedded values.

The supported trust boundary covers documented constructors, normal attribute access, normal assignment, mapping/list APIs, canonicalization helpers, and local schema validation. Within that boundary:

- invalid ordinary inputs fail closed;
- normal field assignment is rejected by frozen value classes;
- stored mappings expose no mutating API;
- caller-owned mappings and sequences are recursively copied and frozen after required nested semantic element types validate;
- payloads, identities, IDs, and content hashes remain stable after construction; and
- strict authorization, blocker, identity, timestamp, and schema contracts remain enforced.

This module is a data-contract implementation, not a hostile-code sandbox. Arbitrary same-process Python code can inspect or replace module globals, mutate closure cells, invoke low-level primitives such as `object.__setattr__`, monkeypatch classes or methods, reload source, use a debugger, or use native extensions. Those operations can subvert pure-Python implementation behavior and are explicitly outside the trust boundary. RD-4.1 makes no interpreter-level tamper-resistance claim and does not treat naming, closure privacy, or Python-level “private” state as security authority.

This limit removes the previous claim that closure-held validation and semantic state remain authoritative against ordinary same-process introspection or monkeypatching. The prior closure architecture was unsound under that claim because a public partial exposed its Python constructor and writable closure cells. The remediation changes the architecture and narrows the guarantee instead of introducing another pseudo-sealed wrapper.

Nested mappings are copied into fresh built-in `mappingproxy` snapshots; lists and tuples become recursively frozen tuples. Nested semantic sequences validate their element types before acceptance: Observation accepts only exact validated `CalendarAssignment` values, and RuleBundle accepts only exact validated `ExclusionReason` values. Arbitrary duck-typed pseudo-records are rejected rather than coerced or retained. Every incoming Mapping is defensively snapshotted, including an existing `ImmutableMapping`, a caller-defined mutable Mapping implementation, or a wrapper forged with `tuple.__new__`. Generic sets and unsupported mutable containers fail closed. After constructor validation succeeds, caller mutation of original input containers cannot change accepted model state, projections, IDs, or hashes.

`ImmutableMapping` remains an exported compatibility transport snapshot rather than a trust boundary. Python's base `tuple.__new__` can manufacture an unvalidated standalone wrapper. Model construction never retains that wrapper and instead creates a fresh recursive `mappingproxy` snapshot, so the standalone forge is contained and non-authoritative within the supported model path.

## Authorization and readiness invariants

ReadinessManifest accepts exactly nine authorization fields. Every value must have exact Python type `bool` and must be `False`; integer zero, integer one, null, strings, containers, Decimal zero, and custom false-like values are rejected. The exported `SAFETY_FLAGS` mapping is an immutable convenience view, not a same-process security boundary. All nine authorizations remain false: real-data ingestion, Shadow export, research dataset, backtest, Production, Canonical, Deferred, ML, and Procurement.

The manifest must contain the exact set of 15 open RD-3 blocker IDs. Input order is normalized lexicographically for schema-facing serialization. Missing, duplicate, fabricated, replaced, case-altered, whitespace-padded, or extra blocker IDs fail closed. Every blocker remains OPEN; RD-4.1 defines no blocker-extension or blocker-resolution mechanism.

Backtest assessment type and scope are coupled in both models and JSON Schema: `BACKTEST_RECORD_ELIGIBILITY` requires `RECORD`, and `BACKTEST_DATASET_ELIGIBILITY` requires `DATASET`.

## Stable identity

Observation identity contains source identity, source-record identifier, metric/instrument identity, and represented-period identity. Collection, availability, creation, and assessment output fields are excluded.

ObservationVersion identity is exactly:

- `observation_id`
- `source_version_or_release_key`
- `stable_version_key`
- `raw_payload_hash`
- `transformation_version`

`parent_version_id` is lineage/content metadata only. Revision availability, collection, observation, creation, assessment, eligibility, and readiness results are excluded. `latest` is not a valid implicit transformation version.

Assessment identity contains assessment type, subject/version ID, role, applicable cutoff/evaluation instant, and rule-bundle version. Results and evidence belong to content.

Other stable identities are:

- CalendarReference: reference ID plus version
- CalendarAssignment: subject ID plus calendar role
- RuleBundle: rule-bundle version
- ExclusionReason: frozen reason code
- ReadinessManifest: kind, semantic subject-scope hash, evaluation instant, contract version, and rule-bundle version

## Identity versus content hash

Stable identity answers which semantic object/version a record represents. Content hash answers which exact canonical semantic content the object contains. Each content projection excludes its own hash field, so stable IDs never depend on their own content hash and no circular ID/hash dependency exists. Contract-defined upstream IDs and hashes may remain evidence.

Derived IDs follow one consistent schema rule: an object's own derived ID remains available as a model property but is external to that object's schema-facing `content_projection()`. For example, Observation excludes its `observation_id`, ObservationVersion excludes its `observation_version_id`, assessments exclude `assessment_id`, and ReadinessManifest excludes `manifest_id`. Upstream identity fields required by another object, such as ObservationVersion's `observation_id`, remain ordinary schema content. This avoids accidental extra fields under `additionalProperties: false` while retaining every frozen identity projection.

All model fields intended for serialization have matching schema properties. Explicit nulls are retained in schema-facing payloads, and the corresponding schemas explicitly permit null for optional timestamp fields. `model_field_names()` derives its result from dataclass fields for automated field-parity inspection. Model-to-schema round-trip tests cover every structural model, including embedded CalendarAssignment and ExclusionReason definitions. JSON Schema enforces structural payload constraints; model constructors additionally enforce semantic invariants such as source-period consistency, strict authorizations, exact RD-3 blockers, and canonical unordered-collection normalization. Negative-parity cases for empty required identifiers and roles, non-Mapping semantic data, and ReadinessManifest contract version are rejected at construction as well as by schema validation.

## Canonical JSON

`C4_CANONICAL_JSON_V1@1.0.0` produces UTF-8 without BOM or trailing newline. It:

- NFC-normalizes keys and string values
- rejects invalid Unicode, lone surrogates, duplicate raw JSON keys, and post-NFC key collisions
- recursively orders object keys by Unicode code point
- preserves semantic array order
- normalizes only explicitly unordered contract collections
- rejects binary floats and non-finite numbers
- supports integers and Decimal fixed-point output without exponent, rounding, or truncation
- removes trailing fractional zeros and normalizes negative numeric zero to `0`

Calendar assignments, required calendar roles, exclusion catalog records, blocker lists, source allowlists, and manifest entries use only their specified contract ordering.

## Hash profiles

`C4_RAW_BYTES_HASH_V1@1.0.0` hashes exact bytes using SHA-256. It performs no decoding, normalization, trimming, newline conversion, or canonicalization.

`C4_HASH_PROFILE_V1@1.0.0` hashes the canonical bytes after this actual-NUL-byte prefix:

`C4RD<NUL><DOMAIN><NUL>CONTRACT=<version><NUL>CANON=C4_CANONICAL_JSON_V1@1.0.0<NUL>HASH=C4_HASH_PROFILE_V1@1.0.0<NUL>`

The frozen domains are Observation ID/content, ObservationVersion ID/content, assessment ID/content, calendar content, rule-bundle content, and manifest content. Unknown domains and unsupported profiles fail closed.

## Exclusion catalog

Catalog version `1.1.0` contains the 27 frozen base reasons plus:

- `SOURCE_VENUE_MAPPING_CONFLICT`
- `CONTINUOUS_CONTRACT_MAPPING_UNRESOLVED`
- `SOURCE_LICENSING_BLOCKED`

The exact catalog and order are validated. Stale aliases are rejected and never silently mapped.

Every RuleBundle catalog element must be an exact, already validated `ExclusionReason` value. Mutable pseudo-records with matching `code`, `sort_order`, or other attributes fail closed. Accepted exclusion records are immutable validated value objects, so no caller-owned mutable pseudo-record survives construction.

## PIT structure

The contract can represent and check internal timestamp consistency:

- feature availability is no later than research cutoff
- label availability is later than research cutoff
- evaluation is no earlier than label availability

This is structural consistency validation only. It is not a source-level PIT or eligibility engine.

## Local schema validator

`scripts/c4_rd_schema_validator.py` loads an explicit allowlist of local Draft 2020-12 schemas. It has no network retrieval callback and returns deterministic errors containing instance and schema paths. Semantic helpers enforce source periods, calendar-role completeness/uniqueness, PIT timestamp consistency, and the exact reason catalog.

## Inert export contract

The export schema is an inert request shape. It requires `read_only = true` and fixes Shadow, Production, Canonical, Deferred, and Procurement authorization fields to `false`. Schema presence does not authorize export. No exporter, reader, adapter, writer, CLI, or persistence path exists in RD-4.1.

## Tests and validation limits

The ordinary-path suites cover valid and invalid Observation construction, exact nested `CalendarAssignment` and `ExclusionReason` type enforcement, rejection of mutable pseudo-records before schema-facing payload creation, positive acceptance of validated nested values, ObservationVersion's exact five-field identity, assessment and manifest identities, all nine exact-False authorizations, rejection of True and false-like values, the exact 15 OPEN blockers, malformed blocker sets, nested caller-state isolation, hostile Mapping isolation, normal and forged `ImmutableMapping` handling, payload/identity/ID/hash stability, timestamp normalization, canonical JSON, raw-byte and domain hashes, model/schema parity, positive model/schema round trips, Backtest type/scope coupling, and same-major hash compatibility.

The supported mutation matrix applies normal assignment to every contract-bearing model and rechecks payload, identity, stable ID, and content hash. It does not claim that `object.__setattr__`, class/module monkeypatching, closure-cell mutation, or other arbitrary same-process interpreter manipulation is resisted. Architecture regression coverage instead verifies that public models are frozen, slotted value classes, semantic fields are stored directly, no public partial constructor is present, no `state_by_id` semantic registry remains, caller inputs are independently snapshotted, and deterministic values produce deterministic semantics.

The earlier “20/20 adversarial” wording described probes against closure, helper, class, and metaclass rebinding as though those results established a supported tamper-resistance guarantee. That wording is withdrawn. A validation-cell closure replacement completed before the platform Cyber / Trusted Access restriction and demonstrated that the prior guarantee was false. That evidence remains authoritative. Cyber-gated deep authority probes were not retried and no attempt was made to evade or bypass the platform gate. The platform constraint limits additional hostile same-process probing; it does not weaken the supported ordinary-path contract and is not treated as evidence of tamper resistance.

The hostile remote-`$ref` test uses an in-memory schema variant and confirms fail-closed local resolution without a network attempt. This avoids root-level temporary directories while preserving the same security assertion.

Current C4-RD-4.1.6 verification is `66/66 PASS` for the RD-4 contract suite and `28/28 PASS` for the RD-4 schema-validator suite. The unchanged historical suites pass at C4.3 `25/25`, Full Minimal Track `17/17`, and C3 validator `23/23`. Full discovery passes `353/353` with zero failures, zero errors, and zero skips. The three historical test files were not modified.

## Safety boundary and non-goals

The following remain false:

- real-data ingestion
- Shadow export
- research dataset
- backtest
- Production
- Canonical
- Deferred
- ML
- Procurement

PIT, research, and backtest enabled-source lists remain empty. There is no source registry, policy registry, provider logic, collector, adapter, calendar downloader, real-data reader, Google Sheets path, eligibility engine, FSoC evaluator, readiness transition engine, ML model, or procurement signal.

All 15 RD-3 blockers remain open: `RD3-LME-001` through `003`, `RD3-SMM-001` through `004`, `RD3-YAHOO-001` through `004`, `RD3-BZ-001`, and `RD3-WB-001` through `003`.

RD-4.1 does not authorize RD-4.2. No commit, push, or PR is performed by this implementation stage.

The same-major canonical-hash compatibility policy is unchanged. This hardening does not alter identities, canonical JSON, raw-byte hashing, domain separation, timestamp normalization, schemas, or contract semantics.
