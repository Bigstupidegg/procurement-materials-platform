# C4-PIT Pre-Rights Technical Database Validation

## Scope and hard boundary

This is a disposable, repository-local, single-writer validation PoC. It tests
synthetic evidence persistence and a read-only copy of the already present
`data/world-bank.json` dataset. It does not authorize or perform historical
World Bank PDF acquisition.

`WORLD_BANK_PINK_SHEET_RELEASE_PDF` remains `REVIEW_REQUIRED`. No real
historical PDF is downloaded, hashed, parsed, or stored, and real historical PIT
readiness remains unproven.

The adapter performs no network I/O and does not connect to the authoritative
private research database, the DB2 pilot database, Google Sheets, Production,
Canonical, or Deferred stores. It does not enable PIT, Research Dataset,
Backtest, Prediction, public display, public API, redistribution, or export.

The public/export constants remain false, the repository safety flags remain
false, and the PIT/research/backtest enabled-source tuples remain empty.

## Disposable database identity and location

Every database path must resolve beneath this repository-local root:

`runtime/c4-pit-pre-rights-validation/`

Paths outside that root, traversal escapes, symlink targets, non-`.duckdb`
files, and reserved authoritative/private database names are rejected. An
existing nonempty database is never adopted or upgraded merely because it is a
valid DuckDB file.

The `schema_migrations` row also serves as the durable database sentinel and
records the database purpose, schema contract/version, migration ID/checksum,
deterministic catalog fingerprint, timestamps, and code commit identifier.

`created_at` and `applied_at` are required canonical, timezone-aware UTC
RFC3339 timestamps. On creation they are identical; on every reopen both are
parsed and canonical serialization is enforced, and `applied_at` may not
precede `created_at`.

`code_commit_sha` means the authorized repository HEAD/base from which this
uncommitted validation work began:
`a6e5f61f24f9b3f16398fc6c31f1033de424199d`. It must be the exact lowercase
40-character Git SHA on creation and reopen. It does **not** claim that the
commit contains this uncommitted validator, tests, SQL, or documentation. The
migration checksum and catalog fingerprint separately authenticate the SQL and
database structure; they are not a hash of the Python validator source.

The sentinel is not sufficient by itself. On every initialization/reopen, the
actual DuckDB catalog is compared with a descriptor generated from the approved,
checksummed migration. The comparison covers exact tables, columns, types,
nullability, primary keys, foreign keys, unique constraints, and check
constraints. Marker-only, missing-table, altered-column, altered-constraint,
altered-foreign-key, old-checksum, or malformed/mismatched-sentinel databases
are rejected and must be recreated. Transaction failure during initial
migration leaves no partially applied schema objects.

## Migration

- File: `scripts/sql/c4_pit_pre_rights_validation_v1.sql`
- Schema: `C4_PIT_PRE_RIGHTS_VALIDATION_DB_V1@1.1.0`
- Migration ID: `C4_PIT_PRE_RIGHTS_VALIDATION_DB_V1_INITIAL`
- Canonical LF SHA-256:
  `b7a93970b9363ffd7994d10048eb3449dabf607fb889bd144f71ced785baaa57`

The schema still contains 26 tables. R2 does not change this migration or its
checksum. The unpromoted disposable V1 contract supersedes pre-R1 files, which
are intentionally obsolete and are not upgraded in place. DDL application is
transactional and contains no reverse or destructive authoritative migration.

## Architecture reuse and authoritative replay

The existing C4-PIT-2B classes remain authoritative for SourceRelease,
EvidenceArtifact, EvidenceClaim, EvidenceSubjectBinding,
HistoricalVintageClaim, TemporalBound, identity, content, and revision lineage.
Replay reconstructs those exact objects through their existing constructors and
compares stable IDs, content hashes, canonical projections, relational columns,
and exact memberships.

The adapter does not maintain a duplicate PIT identity envelope.
Validation-only objects—runs, assessments, manifests, audits, schema
fingerprints, and promotion records—use a namespaced validation projection on
the existing canonical hash primitive.

## Rights, runs, and current trust

Callers cannot persist a supplied `RightsGateDecision(PASS)`. A rights
assessment is derived internally from the persisted profile, profile version,
source channel, allowlist, requested activity, and fail-closed system policy.

Every acquisition run immutably references the exact assessment that authorized
its channel, profile/version, and activity. Composite database constraints reject
cross-channel substitutions. XLSX rights do not authorize PDF activity,
synthetic rights do not authorize PDF activity, and PDF rights remain blocked.

Assessments are append-only history. Current rights are resolved
deterministically from the latest applicable assessment and current
profile/allowlist state. A later blocked or disabled decision makes existing
trust inactive without deleting historical evidence.

Acquisition state is derived by replaying the complete ordered event history.
Ordinals must be contiguous, timestamps canonical and non-retrograde, and every
transition must belong to the declared transition graph. Canonical RFC3339 is
the storage representation, while chronology is compared using parsed,
timezone-aware instants rather than serialized timestamp strings. This is an
acquisition-event ordering guarantee only; it does not expand the TemporalBound
domain contract. `INVALIDATED` is terminal and repeated invalidation is
idempotent.

The general `transition_run()` API cannot authorize `COMPLETE`, even when the
ordinary transition graph would otherwise permit `RUNNING → COMPLETE` or
`PARTIAL → COMPLETE`. COMPLETE is a privileged state accepted only when replay
can prove the completion contract for the persisted run kind:

- `SYNTHETIC_VALIDATION` requires the exact validated manifest, audit,
  evidence identities, trusted-promotion projection, and memberships.
- `DATASET_READ_ONLY_VALIDATION` requires the authenticated repository source
  contract and the complete validated 799-row import projection.

A stored COMPLETE event by itself is never completion proof. Direct SQL can
physically append a syntactically legal but incomplete COMPLETE event to a
DuckDB file; fresh-adapter replay rejects that run, active-trust APIs return no
trust, and normal platform queries do not accept it. The physical row is left
in place as tamper evidence.

## Replay-before-promotion

Synthetic persistence uses two safety phases:

1. Persist evidence, manifest, and audit while the run remains `RUNNING`, then
   commit.
2. Reopen/replay the persisted state and construct the candidate. Only after
   strict replay passes does one transaction append `COMPLETE`, insert the
   promotion and exact memberships, revalidate the resulting state, and commit.

Any failure before or during the promotion transaction leaves no durable
`COMPLETE` event and no trusted collection. There is no caller-supplied
`validated` flag, token, or PASS value that grants completion authority.
Promotion records the run, manifest, rights assessment, audit reference, replay
fingerprint, timestamp, exact canonical membership, and promotion checks.

The trusted promotion is an authoritative persisted projection, not a
self-asserted PASS envelope. Replay reconstructs it from the validated manifest,
canonical manifest-ordered membership, current rights, audit association,
content/contract read-back, and replay result. The stored `manifest_hash` must
equal the recomputed manifest hash, and `promotion_checks` must exactly equal
the canonical required check set with every value `PASS`. Missing, failed, or
unexpected checks; a false manifest hash; changed membership; or an otherwise
self-consistent caller hash all fail closed.

Trust is revalidated rather than accepted as a stored boolean. Active trust
requires valid schema identity, legal run history, current PASS rights, exact
manifest/artifact/claim/binding/vintage relationships, coherent audit
provenance, exact promotion projection, successful replay, and a
non-invalidated run. A direct SQL insert of a collection without complete
membership or with self-asserted promotion values does not create active trust.

## Synthetic evidence

The March 8300 and April 8305 records are explicitly synthetic. Their payloads
use `synthetic://` locators, synthetic release/document identifiers,
`synthetic_only=TRUE`, and attribution stating they are not World Bank evidence.
They exercise hashing, manifests, memberships, temporal bounds, revision
lineage, cutoff selection, and restart replay only.

## World Bank dataset source contract

Only `import_dataset_copy()` may assign `REAL_DATASET_DERIVED`, and it accepts
only the exact repository file:

- path: `data/world-bank.json`
- SHA-256: `4ae3196f8a7083827675d09479b8377f0a96e2302f5d049ebd1fdf71761ad08b`
- Copper periods: 799
- first period: `1960-01`
- last period: `2026-07`
- unit: `($/mt)`

The importer validates source metadata, exact hash, record count, period syntax,
monthly ordering and completeness, duplicate/missing/extra periods, commodity,
unit, and exact finite Decimal values. Generic caller rows and caller-supplied
lineage cannot self-label as real data.

Each row records the source contract, relative path, SHA-256, channel, import
run, rights profile, and rights assessment. Availability remains
`ABSENT_NOT_PIT_VERIFIED`; period, file time, repository time, import time,
retrieval time, and row order are not treated as historical availability.

The import reads the source without modifying or backfilling it. These
validation rows do not enable the platform Research Dataset.

Dataset completion is separate from artifact/trusted-collection completion. It
does not require historical-PDF evidence, but its COMPLETE state is still
replay-verifiable from the authenticated source hash, exact source contract,
rights binding, complete month range, and every persisted dataset row. The
import rows, COMPLETE event, and final in-transaction replay succeed or roll
back together.

## Temporal and revision behavior

TemporalBound retains EXACT, BY_DATE, NOT_BEFORE, NOT_LATER_THAN, INTERVAL, and
UNKNOWN semantics and precision. DATE_ONLY is not promoted to a timestamp,
`retrieved_at` is not availability, STRICT_INTRADAY rejects insufficient
evidence, and DAY_GRANULAR remains explicit.

Persisted vintage replay reuses the authoritative lineage validator for
self-parenting, missing parents, cycles, sibling ambiguity, disconnected roots,
retrograde revisions, and future visibility.

## Queries and audit boundary

Copper history explicitly filters World Bank Copper monthly synthetic vintage
semantics. Provenance traversal is constrained through one collection, audit,
run, manifest, artifact, claim, binding, and vintage chain; absent or multiple
chains fail as ambiguous. Trust and rights queries invoke persisted replay.

Application APIs are append/invalidation-oriented and do not expose ordinary
UPDATE/DELETE operations for historical evidence or audit rows. A person or
process with arbitrary write access to the DuckDB file is outside that logical
append-only trust boundary. Tampering is detected where covered by constraints
and replay, but this file is not a hardened server database.

This PoC assumes one writer. Distributed locking and multi-writer coordination
are future production architecture work.

## Validation coverage and limitations

Focused tests cover the Human Review counterexamples, public and direct-SQL
completion bypasses, exact trusted-promotion reconstruction, sentinel tampering,
junction/path and catalog forgery, partial migration rollback, rights bindings
and invalidation, legal event history, idempotent invalidation,
replay-before-promotion failure injection, independent claim/binding/vintage
identity reconstruction, direct-SQL trust tampering, query ambiguity, restart
replay, temporal/revision cases, and the exact 799-row dataset source contract.

A passing result means technical pre-rights validation of this disposable local
PoC only. It does not mean production readiness, rights approval, real PDF
validation, or real historical PIT readiness.
