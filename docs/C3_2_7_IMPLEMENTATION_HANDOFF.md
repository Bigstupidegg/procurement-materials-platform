# C3.2-7 Codex Implementation Handoff

This document is the complete bounded implementation contract for Codex. Do
not infer missing policy from chat history. If a required decision is not
specified here, stop and return the question to Work.

## Objective

Implement a deterministic, read-only validation and evidence layer for the
daily C3.2 scheduled Shadow run. Correlate one actual execution across Windows
Task Scheduler, the PowerShell wrapper, Python runner, runner log, date/source
readiness, canonical evaluation, V2 append/read-back, append-only integrity,
and production/canonical safety. Produce immutable local JSON and derived
Markdown evidence without changing business data or authorization state.

## Repository boundary

- Repository: `Bigstupidegg/procurement-materials-platform`.
- Required branch: `v2.3-c3-2-daily-automation`.
- Accepted operational baseline: `e198db5`.
- C3.2-6 closeout at handoff preparation: `63949e9` plus the C3.2-7 tracked
  contract commit.
- Human Gate at C3.2-6: PASS.
- Production/A:L, canonical persistence/promotion, and Deferred Assembly
  persistence remain disabled.
- `Market_Raw` is immutable; V2 is append-only; `LEGACY_UNVERIFIED` rows may
  never be promoted.
- `.ai/C3_2_CURRENT_STATE.md` is ignored local recovery state, not the sole
  authoritative source.

## Approved architecture

1. The existing scheduled wrapper creates a `wrapper_execution_id`, captures
   safe repository/wrapper context, enforces existing write gates, and passes
   the identity to the existing Python Shadow runner.
2. The runner retains all existing collection, canonical-evaluation, append,
   and read-back behavior. It may emit one versioned structured summary that
   contains no prices, credentials, Sheet ID, or unnecessary raw data.
3. Synchronous core validation runs after Python completes. The wrapper records
   child exit provenance and never masks a non-zero Python result.
4. Because Scheduler completion events exist only after the action exits, the
   wrapper starts a hidden detached read-only finalizer. The finalizer waits a
   bounded period for matching completion evidence and writes the final
   immutable report. On Windows, background launch must use a hidden window.
5. A later validator invocation may reconcile missing finalization by re-reading
   existing evidence only. It must not rerun or backfill the market job.

## Scope

- Versioned evidence capture and schema validation.
- Scheduler event parsing and unique instance/run correlation.
- Wrapper, runner, log, DATE_CONTEXT, source-readiness, canonical-safety, V2
  append/read-back, append-only, and safety checks.
- Immutable local JSON plus Markdown rendering.
- `PASS`, `WARNING`, `FAIL`, `BLOCKED`, and persistent `NOT_VERIFIED` handling.
- Idempotent repeat validation and conflict preservation.
- Local-only notification through exit status, logs, and reports.

## Non-scope

- Production/A:L writes or authorization.
- Canonical promotion/persistence or Deferred Assembly persistence.
- Modification, deletion, repair, rewrite, or backfill of Sheet data.
- Scheduler configuration changes.
- Source selection, pricing, canonicalization, or assembly policy changes.
- External notification integrations.
- Automatic AI repair, deployment, merge, release, or Human Gate approval.
- Holiday-calendar or source-publication policy invention.

## Exact allowed files

Codex may add or modify only:

- `scripts/c3_2_run_validator.py` (new).
- `scripts/run_c3_2_validator.ps1` (new, if needed for detached finalization).
- `scripts/run_c3_2_scheduled_shadow.ps1` (minimal orchestration/evidence hook).
- `scripts/c3_2_scheduled_shadow_runner.py` (minimal run identity and structured
  evidence emission; no business-behavior change).
- `scripts/company-market-requirements.txt` (only to add the pinned,
  standards-compliant runtime JSON Schema validator required below).
- `config/c3_2_validator.json` (new, versioned non-secret policy values).
- `config/c3_2_evidence_report.schema.json` (new).
- `tests/test_c3_2_run_validator.py` (new).
- `tests/test_c3_2_scheduled_shadow_runner.py` (minimal additions).
- `tests/test_powershell_wrapper.py` (minimal additions).
- `docs/C3_2_7_SCOPE.md` and `docs/C3_2_7_IMPLEMENTATION_HANDOFF.md` only when
  implementation reality requires a clarification that does not change policy.

If another tracked file appears necessary, stop and return to Work for an
explicit scope amendment.

## Forbidden files and areas

- `scripts/company_market_collector.py` and formal A:L writer behavior.
- `scripts/c3_2_observation_migration.py` and migration behavior.
- `scripts/c3_2_pending_raw_persistence.py` and `Market_Raw` persistence.
- `scripts/c3_2_canonical_store.py` persistence or canonical promotion.
- `scripts/c3_2_deferred_assembly.py` persistence behavior.
- Existing source-readiness/canonicalization policy modules unless Work first
  amends scope.
- `.gitignore`, credentials, security configuration, Scheduler configuration,
  `.github` workflows, production data, and generated daily evidence.
- Any merge to main, release/tag, force-push, history rewrite, or destructive
  command.

Generated evidence must remain ignored/local under `%LOCALAPPDATA%`, not the
repository.

## Severity contract

- `PASS`: uniquely correlated mandatory evidence; every integrity and safety
  check passes; `pass_scope=SHADOW_OPERATIONAL_ONLY`.
- `WARNING`: safe completed run with a non-fatal condition, including a valid
  StartWhenAvailable recovery, completion after 10 but within 15 minutes,
  expected delayed/unconfirmed source, recognized stderr warning, or harmless
  duplicate-same candidate.
- `FAIL`: observed application, integrity, or safety failure, including Python
  non-zero, wrapper masking, invalid/inferred/substituted dates, duplicate slot
  execution, missed slot, conflicting confirmed data, unauthorized production
  or canonical state, unexpected row delta, or mutation/reorder/deletion of an
  existing V2 row.
- `BLOCKED`: mandatory evidence, correlation, schema, permissions, or storage
  is inaccessible, ambiguous, or unsupported. Never emit PASS.
- `NOT_VERIFIED`: an explicit per-claim state that always remains visible.

Aggregate precedence is observed `FAIL`, otherwise `BLOCKED`, otherwise
`WARNING`, otherwise `PASS`. Preserve the original runner result and evidence
even when validator processing fails.

## Run identity and traceability

The wrapper generates a UUID `wrapper_execution_id` before starting Python.
The final `run_id` is a SHA-256-based identifier over a versioned namespace,
Scheduler task path, Scheduler instance ID, time-trigger event record ID, and
trigger UTC timestamp.

A final PASS requires one unique mapping among:

- scheduled slot;
- Scheduler instance/event sequence;
- wrapper execution interval and ID;
- runner process/result and structured summary;
- one runner log with matching ID;
- V2 pre/post/read-back reconciliation.

Timestamp-only ambiguous correlation is `BLOCKED`. Manual runs have
user-trigger evidence and cannot satisfy a natural slot. Retries require a new
identity plus `retry_of_run_id`; they never replace the original report.

If validation repeats with the same run ID and identical bytes/hash, return
`DUPLICATE_SAME` without rewrite. Different evidence for an existing run ID
must preserve the original, write a separate conflict record, return
`BLOCKED`, and require human review. Report revisions, if unavoidable, are
append-only and explicitly linked.

## Evidence schema

The authoritative JSON includes at least:

- `schema_version`, `report_id`, `report_revision`, `report_created_at`,
  `report_sha256`;
- `run_id`, `wrapper_execution_id`, `scheduled_slot`, `execution_kind`;
- `scheduler_task_name`, `scheduler_instance_id`,
  `scheduler_trigger_kind`, `scheduler_execution_datetime`;
- `validator_execution_datetime`;
- `scheduler_result`, `wrapper_result`, `runner_result`, `validator_result`;
- `runner_log_reference`, `runner_log_sha256`;
- `date_context`, `source_readiness`, `canonical_evaluation`;
- `append_result`, `readback_result`, `append_only_status`;
- `production_status`, `a_l_change`, `canonical_persistence_status`;
- `repository_version` and relevant tracked-file digests;
- `warnings`, `failures`, `not_verified`, `blocked`;
- `evidence_references`, `final_result`, `pass_scope`.

Every assertion carries `value`, `basis` (`OBSERVED`, `DERIVED`, or
`NOT_VERIFIED`), evidence reference, observed timestamp, and evidence digest
where applicable. Store identifiers/counts/statuses/hashes only. Do not store
prices, credentials, Sheet ID, private keys, tokens, or unnecessary raw rows.

## Evidence storage and permissions

- Directory:
  `%LOCALAPPDATA%\ProcurementMaterialsPlatform\evidence\c3_2_7\YYYY-MM-DD\`.
- JSON is authoritative; Markdown is derived from JSON.
- Use exclusive deterministic final names and atomic same-directory temporary
  write, flush/close, then non-replacing rename.
- Never overwrite accepted evidence. Same hash is idempotent; different content
  creates a conflict artifact and `BLOCKED`.
- Retention target is at least 400 days. Initial implementation performs no
  deletion. No cleanup code is in scope.
- Inherit the current user's `%LOCALAPPDATA%` ACL. Do not modify or broaden
  permissions. Broad untrusted write access, ownership mismatch, or inability
  to establish directory integrity is `BLOCKED`.
- Storage failure must remain local, redacted, and visible. Do not fall back to
  Git, database, Sheet, or business data.

## Run-window policy

- Required current task:
  `ProcurementMaterialsPlatform-C3_2_5-ShadowPilot`.
- Scheduled slot: daily `16:30:00 Asia/Taipei` from the current task trigger.
- On-time natural: unique time-trigger instance in
  `[slot, slot + 2 minutes)`.
- Expected completion: within 10 minutes of instance start.
- Successful completion after 10 but within 15 minutes: `WARNING`.
- No terminal completion evidence after 15 minutes: `BLOCKED` unless explicit
  Scheduler/runner failure proves `FAIL`.
- StartWhenAvailable recovery: no time-trigger/user-trigger event for that
  instance, start after the two-minute window and before the next slot, unique
  mapping to the most recent unfulfilled slot, and current setting remains
  true. Result cannot exceed `WARNING`.
- Missed: at the next daily slot, the prior slot has no natural or uniquely
  attributable recovery. Proven missed is `FAIL`; inaccessible/ambiguous
  evidence is `BLOCKED`.
- Duplicate: more than one natural/recovery execution mapped to one slot is
  `FAIL`.
- Manual execution cannot satisfy natural-run acceptance. Retry is distinct and
  must name the original run ID.
- Drift in task/action/trigger/timezone/StartWhenAvailable/IgnoreNew blocks PASS
  and must not be repaired by Codex.

The two-minute and 15-minute boundaries are evidence-based: historical natural
triggers occurred at the slot or +1 second, the earliest recovery was +15m59s,
and the longest observed execution completed in 7m21s.

## Source, canonical, and data checks

- Keep Scheduler/local dates separate from source market date, collector
  availability, and canonical target.
- Require `canonical_target_basis=EXPLICIT_SOURCE_MARKET_DATE_ONLY`.
- SMM requires explicit Daily Snapshot date; LME requires explicit delayed
  official source date; Yahoo confirmation requires later-day exact-date
  historical Close evidence.
- Never infer a missing holiday, source date, publication timestamp, value, or
  neighbouring-date substitute.
- Weekend target, mixed date, incomplete 11 materials, non-canonical assembly
  input, or confirmed conflict must fail closed.
- Capture V2 pre-count and ordered pre-row/ID digest, planned/actual append
  counts and appended IDs, post-count, ordered post-prefix digest, and read-back
  IDs. Existing rows must remain unchanged/in order and new rows must be tail
  appends only.
- Require `post_count - pre_count = actual_append_count`, unique nonblank IDs,
  and read-back of every appended ID.
- Confirm zero unauthorized canonical status/persistence/promotion.
- The validator does not modify data to resolve a discrepancy.

## Production/A:L policy

Initial implementation uses daily **control-path verification only**.

Observe the wrapper forcing `ALLOW_GOOGLE_SHEET_WRITE=0` and
`ALLOW_PENDING_RAW_WRITE=0`, clearing `CONTROLLED_WRITE_APPROVAL`, and invoking
the expected Shadow runner at the reported repository version. Report
`production_status=DISABLED_BY_CONTROL` and `a_l_change=NOT_VERIFIED`.

Do not add A:L reads or a before/after digest in this implementation. Never
claim actual A:L no-change from control-path evidence.

## Exit-code contract

- `0`: PASS or WARNING.
- Any Python non-zero: return the exact Python code and set
  `exit_origin=RUNNER`.
- The following are used only when Python returned `0`:
  - `70`: deterministic validator FAIL.
  - `71`: validator BLOCKED.
  - `72`: validator internal/unhandled error.
  - `73`: capture/report storage failure.

The synchronous wrapper must preserve Python precedence. Detached-finalizer
failure after wrapper exit cannot rewrite Scheduler action result and instead
creates or causes a later BLOCKED reconciliation record.

## Initial notification and Scheduler integration

Notification is local only: JSON/Markdown report, wrapper log/stderr summary,
and Scheduler action result. Do not add external connections.

Keep the current Scheduler task name and configuration unchanged. Use hidden
detached finalization for the initial implementation. Any future event-triggered
task or rename requires a separate Scheduler Human Gate.

## Safety constraints

- Keep `ALLOW_GOOGLE_SHEET_WRITE=0` and Production/A:L disabled.
- Keep canonical and Deferred Assembly persistence disabled.
- Never promote `LEGACY_UNVERIFIED`.
- Never modify or overwrite existing V2 or `Market_Raw` rows.
- Never rerun or backfill to manufacture evidence.
- Never log credentials, Sheet IDs, prices, or sensitive raw values.
- Validator/AI output is not Human Approval.
- Preserve authoritative holiday calendar and source-native publication
  timestamp as persistent `NOT_VERIFIED` findings.

## Required targeted tests

Use deterministic fixtures/mocks only; automated tests must not write live
Scheduler or Sheet state.

1. JSON schema and JSON-to-Markdown consistency.
2. Natural trigger, same-day recovery, overnight recovery, manual run, explicit
   retry, missed slot, duplicate slot, and ambiguous Scheduler correlation.
3. Trigger boundary at slot, +1 second, two-minute exclusion, 10/15-minute
   completion boundaries, and next-slot missed determination.
4. Wrapper preserves Python `1` and any arbitrary Python non-zero even when the
   validator also fails.
5. Reserved exits `70-73` apply only after Python `0`.
6. Native stderr warning with Python `0` remains warning/success; real non-zero
   remains failure.
7. Missing, truncated, reused, overwritten, and conflicting log/capture
   evidence.
8. DATE_CONTEXT timezone and no execution/local-date inference.
9. SMM, LME, Yahoo, delayed, pending, unconfirmed, missing, invalid, and
   source-native-publication-unverified states.
10. Weekend, holiday-unverified, incomplete 11/11, mixed-date, carry-forward,
    substitution, and confirmed-conflict safety.
11. V2 append arithmetic, tail-only append, unchanged prefix, ID uniqueness,
    exact read-back, unexpected count, deletion, reorder, and mutation.
12. Canonical worksheet absent/empty and unauthorized promotion detection.
13. Control-path production verification reports
    `a_l_change=NOT_VERIFIED` and never claims no cell change.
14. Atomic write, same-hash idempotency, different-hash conflict preservation,
    unsafe permissions, and storage failure.
15. Detached-finalizer timeout/recovery detection without rerunning the market
    job.
16. Redaction tests for credentials, Sheet ID, market prices, private keys, and
    tokens.

## Full regression requirement

Run the complete repository regression suite after targeted tests. Any existing
test failure is a blocker. Run syntax/compile checks for all changed Python and
PowerShell files. Tests must not create tracked artifacts or live external
state. Refresh `.ai/C3_2_CURRENT_STATE.md` after material test/commit/push
checkpoints as required by `AGENTS.md`, but never track or push `.ai/`.

## Acceptance criteria

- One final PASS report maps to exactly one natural Scheduler instance.
- Timestamp-only or otherwise ambiguous evidence cannot PASS.
- Python failure evidence and exit code are never hidden or replaced.
- Warning and NOT_VERIFIED findings remain visible.
- V2 append/read-back and existing-row immutability are deterministically
  reconciled without data writes by the validator.
- Production reports only `DISABLED_BY_CONTROL`; A:L actual change remains
  `NOT_VERIFIED` under the selected policy.
- No unauthorized canonical status or persistence exists.
- Repeat validation is idempotent and conflicts are preserved without
  overwrite.
- Storage or validator failure cannot cause business-data repair or job rerun.
- Targeted tests, syntax checks, and full regression pass.
- `git diff` contains only explicitly allowed files.
- No Scheduler, Sheet, credentials, production path, canonical persistence, or
  generated evidence is changed.
- One separately approved natural Shadow validation agrees with manual
  reconciliation before operational acceptance.

## Rollback

Revert the C3.2-7 wrapper hook and new validator/schema/config/test files to the
accepted pre-validator behavior. Preserve generated evidence as immutable
history. Do not delete/rewrite Sheet rows or reports. No Sheet rollback is
required because the validator introduces no business-data writes. Scheduler
rollback is not required because Scheduler configuration is not changed.

## Required Codex implementation result

Return to Work with:

1. branch and exact implementation commit SHA;
2. changed-file list and confirmation every file is allowed;
3. concise architecture/behavior summary;
4. exact targeted-test, syntax-check, and full-regression commands/results;
5. fixture-generated sample JSON and Markdown report paths plus schema result;
6. exit-code and runner-precedence evidence;
7. idempotency/conflict and redaction evidence;
8. confirmation that no Scheduler or Sheet operation occurred;
9. confirmation Production/A:L and canonical persistence remain disabled;
10. repository/origin/working-tree status and push status;
11. residual risks, open questions, and rollback command/commit boundary;
12. refreshed ignored `.ai/C3_2_CURRENT_STATE.md` recovery state, never
    committed or pushed.

## Ownership

Implementation ownership is **Work -> Codex -> Work**. Codex performs the
bounded code/test work; Work independently reviews the diff, tests, evidence
fixtures, safety state, and any separately approved natural Shadow validation.

## Third corrective implementation addendum (authoritative)

This addendum supersedes less-specific earlier C3.2-7 identity, runtime
validation, capture, and missed-run statements. It exists because independent
Work revalidation of `1aaa750` reproduced contract failures that earlier
helper-oriented tests did not cover. Codex must implement exactly this addendum
and must not infer alternative policy from chat history.

### 1. Authoritative run identity contract

The only authoritative Scheduler execution identity is this five-part tuple:

| Field | Meaning and source |
| --- | --- |
| `scheduler_task_name` | Configured Task Scheduler task name/path, observed from task XML and Scheduler event XML. |
| `scheduler_instance_id` | Required Task Scheduler Operational `Correlation.ActivityID` GUID, normalized without braces. |
| `scheduler_trigger_event_record_id` | Required EventRecordID for the unique trigger event. |
| `scheduler_trigger_timestamp` | Required UTC `SystemTime` of that trigger event. |
| `trigger_kind` | `TIME` only for Event 107; `RECOVERY` only for Event 114; `MANUAL` only from explicit user-trigger evidence. |

`scheduler_instance_id` is the primary Scheduler identity. The other fields
prevent an Activity GUID from being detached from its trigger provenance. The
versioned `run_id` is SHA-256 over the configured namespace, task path, and
all Scheduler identity fields; it is an evidence/report identity, not an input
chosen by the wrapper.

`wrapper_execution_id` is a fresh UUID generated before Python starts. It is a
child-process identity only. The wrapper passes it verbatim to the runner, and
the runner’s sole initial identity is that same value; a process ID is
diagnostic only and cannot be used as a run identity.

The wrapper capture must contain `wrapper_execution_id`, wrapper start/end,
runner result, runner-summary digest, runner-log path/digest, and repository
version. The normalized Scheduler evidence and validator input must contain
the complete Scheduler tuple and action/start/result/end timestamps. The
structured runner terminal summary must contain `wrapper_execution_id`, runner
result, schema version, and all required operational groups. After selecting a
unique Scheduler instance, the detached finalizer must emit exactly one
structured **evidence-terminal binding record** in the wrapper log (or an
immutable explicitly referenced companion log) containing the complete
Scheduler tuple, `wrapper_execution_id`, runner-summary digest, final-log
digest, and derived `run_id`. The final report repeats those fields.

PASS requires all of these assertions:

1. one eligible Scheduler tuple maps to one action interval;
2. that interval contains the captured wrapper interval;
3. the capture UUID equals the runner terminal UUID;
4. the capture runner-summary/log digests equal re-read bytes;
5. the evidence-terminal Scheduler tuple equals normalized Scheduler evidence;
6. the terminal UUID/digests equal capture/runner evidence; and
7. report `run_id` is recomputed from the terminal/normalized tuple and equals
   the report value.

Any absent, ambiguous, or unequal mandatory binding is `BLOCKED`; a
schema-valid but contradictory or reused identity/digest is `FAIL`. Fixture
mode alone may use declared synthetic identity values, but must set
`fixture_evidence.non_operational=true`, use `fixture://` references, and end
`BLOCKED`. No identity member may be omitted in an end-to-end fixture; a
fixture intentionally omitting one is invalid evidence and must be BLOCKED.
Fixtures must never reach an operational PASS. A log/capture from Scheduler
instance A used with instance B therefore has a terminal tuple or digest
mismatch and is rejected deterministically.

### 2. Runtime validation layers

Implement three non-overlapping layers, in this order:

1. **JSON Schema layer.** Load the checked-in
   `config/c3_2_evidence_report.schema.json` at runtime and validate with a
   standards-compliant Draft 2020-12 implementation plus format checking. Add
   the required pinned dependency only in
   `scripts/company-market-requirements.txt`. This layer alone enforces the
   schema: required/unknown properties, types and nullability, enums, UTC or
   offset timestamps, GUID/record-ID/hash patterns, assertion structure, and
   evidence-reference syntax. Do not retain a hand-written partial schema
   validator that purports to duplicate it.
2. **Cross-field contract layer.** After schema success, recompute and compare
   identities, hashes, report hash/run ID, timestamp ordering, Scheduler
   trigger/action/result/end relationships, wrapper interval containment,
   runner/wrapper results, and `exit_origin`. It also rejects a nonzero Event
   201 result even when Event 102 exists.
3. **Operational semantic layer.** After cross-field success, inspect the
   content of mandatory groups as defined below. It may not accept an empty
   object, an empty map/list where evidence is required, an arbitrary status,
   or a field that merely has the right JSON type.

The structural/schema layer is unavailable or internally invalid only if the
checked-in schema/validator cannot load (`72`). Malformed, missing, or
unusable runtime evidence yields `BLOCKED`/`71`; observed contradictory,
tampered, duplicate, nonzero, or unauthorized evidence yields `FAIL`/`70`.
Neither outcome can be PASS. A permitted limitation is an explicit
`NOT_VERIFIED` assertion, but cannot substitute for mandatory run evidence.

### 3. Real Scheduler missed-run derivation

For each evaluated slot `S`, calculate `S` and `next_slot(S)` from the
verified daily 16:30 Asia/Taipei task trigger. Query the Task Scheduler
Operational log for the configured task and IDs 100, 102, 107, 114, 200, and
201 over `[S - 2 minutes, next_slot(S) + 15 minutes]`; retain XML plus record
metadata sufficient to verify the query. Group by Activity GUID, never by
localized rendered message or timestamp alone.

Negative evidence is sufficient only when the log is enabled/readable, the
query succeeds, retention demonstrably covers from before `S` through at
least `next_slot(S)`, and coverage boundaries are recorded. If this proof is
missing or event XML is ambiguous, the result is `BLOCKED`, not missed.

| Situation | Deterministic classification |
| --- | --- |
| Exactly one Event 107 trigger in `[S, S+2m)` with success | `NATURAL`; completion <=10m is eligible for PASS, >10m and <=15m is WARNING. |
| Event 107 candidate has no terminal evidence by `S+15m` | BLOCKED unless an explicit Scheduler/runner failure proves FAIL. |
| Exactly one Event 114, no qualifying 107/user trigger, after `S+2m` and before `next_slot(S)`, with `StartWhenAvailable=true` | `RECOVERY`, WARNING only; never natural PASS. |
| Verified coverage reaches `next_slot(S)` and no natural or uniquely attributable recovery exists | `MISSED`, FAIL. |
| Two or more natural/recovery instances map to `S` | `DUPLICATE`, FAIL. |
| Event 201 carries nonzero result, with or without Event 102 | FAIL. |
| Log/query/retention unavailable or candidates cannot map uniquely | BLOCKED. |

A finalizer before `next_slot(S)` may report current completion evidence but
cannot manufacture a missed outcome. A later read-only validator invocation
must derive the prior-slot result when coverage reaches that boundary. No
injected `missed_slot`/`evidence_complete` fixture field may be accepted by
operational code.

### 4. Mandatory capture semantic contract

All content below is required after the JSON Schema layer; the exact runner
data may be redacted to identifiers, dates, statuses, counts, and digests.

| Group | Minimum operational content | Missing/empty/contradictory outcome |
| --- | --- | --- |
| `date_context` | timezone-aware scheduler execution timestamp; local calendar date/status; local business date or explicit unresolved status; `canonical_target_basis=EXPLICIT_SOURCE_MARKET_DATE_ONLY`. | Missing/empty/unparseable: BLOCKED. Inferred/contradictory basis: FAIL. |
| `source_readiness` | `EVALUATED` with non-empty source-family readiness/status entries, target/candidate counts, and explicit source dates where evaluated; or an explicit valid normal-skip state. Preserve publication timestamp as NOT_VERIFIED where unavailable. | Missing/empty/unknown status: BLOCKED. Contradictory/invalid source evidence: FAIL. |
| `canonical_evaluation` | decision status; evaluated target count or explicit not-applicable reason; `persistence`, `promotion`, and `deferred_assembly_persistence` explicitly `DISABLED`/`NONE`. | Missing/empty: BLOCKED. Any enabled/persisted/promoted state: FAIL. |
| `append` / read-back | explicit status; pre/post counts; planned/actual counts; pre and post-prefix SHA-256 digest; appended IDs and exact read-back IDs, or explicit zero/normal-skip proof. | Missing/empty/unparseable: BLOCKED. Count/ID/digest mismatch, mutation, reorder, or deletion: FAIL. |
| Production controls | observed write gates `0`, cleared approval, approved runner path/repository version; `production_status=DISABLED_BY_CONTROL`; `a_l_change=NOT_VERIFIED`. | Missing evidence: BLOCKED. Gate/runner mismatch or claim of actual A:L no-change: FAIL. |
| Canonical/deferred state | explicit disabled/no-persist values from the runner/capture. | Missing: BLOCKED. Any persistence/promotion enablement: FAIL. |
| Runner and evidence terminal | exactly one structured runner terminal plus exactly one structured evidence-terminal binding record, with identity/digest fields required above. | Missing, multiple, malformed, or empty: BLOCKED. Mismatch/reuse/tampering: FAIL. |

### 5. Corrected tests

The previous tests could pass because they exercised helper functions and
fixture-only injected indicators, while the production path neither bound a
Scheduler instance into its terminal record nor executed the complete JSON
Schema and semantic contracts. The corrected tests must use deterministic
fixtures through the real parser/validator interfaces.

Add an end-to-end fixture path:

`historical Scheduler XML -> normalization/aggregation -> wrapper capture +
runner terminal + evidence-terminal binding -> runtime JSON Schema ->
cross-field validation -> semantic validation -> final result`.

It must cover, with deterministic expected results:

- a valid capture/log reused under a different Scheduler instance (`FAIL`);
- invalid `execution_kind`, `exit_origin`, and nested assertion/evidence
  reference values (rejected before PASS);
- empty and malformed date/source/canonical/append groups (`BLOCKED`);
- XML-derived proven missed slot (`FAIL`) and unavailable coverage (`BLOCKED`);
- duplicate historical Scheduler execution (`FAIL`);
- Event 102 plus nonzero Event 201 (`FAIL`); and
- Event 114 recovery that cannot be classified `NATURAL` or PASS.

Retain existing tests for runner-exit precedence, atomic publication,
idempotency/conflict preservation, and fixture isolation. Tests may write only
temporary fixture roots and must assert no generated evidence is persisted to
the operational local evidence root.

### 6. Minimum third corrective code scope

Expected changes are limited to:

- `scripts/c3_2_run_validator.py`: replace the shallow schema routine with the
  real schema layer; add identity/cross-field/semantic layers and evidence-based
  missed-slot derivation.
- `config/c3_2_evidence_report.schema.json`: version/extend identity, terminal
  binding, ID, timestamp, assertion, and reference definitions.
- `scripts/run_c3_2_scheduled_shadow.ps1`: capture and emit/append the final
  evidence-terminal binding record without changing the runner exit precedence
  or any write gate.
- `scripts/run_c3_2_validator.ps1`: query the exact historical Scheduler range
  and coverage metadata required for slot derivation; remain read-only.
- `scripts/c3_2_scheduled_shadow_runner.py`: emit only the additional
  non-sensitive runner terminal fields required for the binding.
- `scripts/company-market-requirements.txt`: only the pinned JSON Schema
  validator dependency.
- the three listed C3.2-7 test modules: add end-to-end and adversarial cases.

No other tracked runtime/persistence/source/Scheduler file is authorized. Do
not redesign runner exits, atomic publishing, idempotency, or fixture isolation
except for additions strictly necessary to carry the binding fields.

### 7. Third-fix acceptance checklist

1. One final operational report is uniquely bound to one Scheduler execution.
2. Reused log/capture across Scheduler instances fails.
3. Runtime uses the authoritative checked-in JSON Schema.
4. Invalid enums and nested contract/reference values are rejected.
5. Semantic-empty mandatory groups block/fail and cannot PASS.
6. Missed execution is derived from real Scheduler XML/coverage, not fixture
   flags.
7. Recovery cannot become natural PASS.
8. Nonzero Event 201 cannot be overridden by Event 102.
9. Existing runner-exit precedence remains correct.
10. Atomic/idempotent publication remains correct.
11. Fixtures remain isolated from operational evidence.
12. Targeted adversarial tests pass.
13. Full regression and changed-file syntax checks pass.
14. Scheduler, Sheet/business data, Production/A:L, canonical/deferred
    persistence, and credentials remain unchanged/disabled.
15. Natural-run acceptance remains a separately approved later Human Gate.
