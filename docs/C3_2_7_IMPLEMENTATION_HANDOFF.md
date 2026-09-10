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
