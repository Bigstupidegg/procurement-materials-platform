# C3.2-7 Automated Run Validation & Evidence Reporting

Status: implementation-preparation contract approved after C3.2-7 Design
Review received CONDITIONAL PASS. This checkpoint authorizes documentation
only. Validator implementation requires a separate Work handoff.

## Starting boundary

- Repository: `Bigstupidegg/procurement-materials-platform`.
- Branch: `v2.3-c3-2-daily-automation`.
- Accepted C3.2-6 operational baseline: `e198db5`.
- Accepted C3.2-6 Human Gate closeout: `63949e9`.
- Production/A:L remains disabled and `ALLOW_GOOGLE_SHEET_WRITE=0` remains
  enforced by the scheduled wrapper and runtime guard.
- `Market_Raw` is immutable. `Market_Observation_V2` remains append-only.
- Canonical persistence/promotion and Deferred Assembly persistence remain
  disabled.
- Accepted data state at entry: V2 227 rows, including 158
  `LEGACY_UNVERIFIED` and 69 `SHADOW_UNRESOLVED`; `Market_Raw` 158 rows;
  `Run_Audit` 10 rows; no canonical rows and no `Canonical_Daily_V2` sheet.

`.ai/C3_2_CURRENT_STATE.md` remains ignored local recovery state. It is not a
substitute for this tracked contract or repository evidence.

## Objective

Add a deterministic, read-only validation and evidence layer for each C3.2
scheduled Shadow execution. The validator correlates Scheduler, wrapper,
runner, log, date/readiness, V2 append/read-back, canonical-safety, and
production-safety evidence. It produces an immutable structured report for
Work or a human reviewer without changing business data or authorization
state.

## Scope

- Identify one Scheduler slot and one actual execution instance.
- Distinguish natural, StartWhenAvailable recovery, manual, retry, missed, and
  duplicate execution.
- Validate wrapper and Python exit-code provenance without hiding failures.
- Validate `DATE_CONTEXT`, source readiness, canonical evaluation, V2 append
  arithmetic/read-back, and append-only integrity.
- Verify the scheduled control path keeps Production/A:L and canonical
  persistence disabled.
- Write local machine-readable JSON and a derived Markdown report.
- Classify `PASS`, `WARNING`, `FAIL`, or `BLOCKED` while preserving all
  `NOT_VERIFIED` findings.
- Make repeated validation idempotent and preserve conflicts without overwrite.
- Support local-only, deduplicated notification through exit status, logs, and
  report files in the initial implementation.

## Non-scope

- Production/A:L write or authorization.
- Canonical persistence or promotion.
- Deferred Assembly persistence.
- Modification, deletion, repair, or backfill of any Sheet data.
- Market job rerun intended to manufacture evidence.
- Scheduler creation, rename, update, or event-trigger registration.
- Source-policy, security-policy, or acceptance-criteria changes.
- AI-driven automatic repair, deployment, or Human Gate approval.
- Establishing an authoritative holiday calendar or source-native publication
  timestamp.

## Approved architecture

The existing wrapper remains the Scheduler action. It creates an immutable
capture identity, enforces the existing safety environment, invokes the Python
runner, records the exact child exit and log, and invokes synchronous core
validation. It must always give a non-zero Python result precedence over any
validator-only result.

Scheduler action completion cannot be observed while that same action is still
running. After core capture, the wrapper therefore starts a hidden, detached,
read-only finalizer and exits. The finalizer waits a bounded period for the
matching Scheduler completion events, performs final correlation, and writes
the immutable report. This adds no Scheduler configuration. A later validator
invocation may detect a missing final report, but may only re-read evidence; it
must never rerun the market job.

The initial implementation remains downstream of business behavior. It may add
versioned structured evidence output to the runner, but must not change source
selection, canonicalization rules, append planning, or persistence behavior.

## Authoritative evidence and observation labels

Evidence precedence is repository/current branch and working tree, deterministic
tests, immutable local logs/captures, read-only Sheet evidence, Scheduler event
evidence, then chat summaries. Every reported assertion must state one of:

- `OBSERVED`: directly read from an identified source.
- `DERIVED`: deterministically calculated from identified observed inputs.
- `NOT_VERIFIED`: authoritative evidence was unavailable or does not exist.

Inferences must never be labelled observed. A report must store evidence
references and SHA-256 digests, not credentials, market prices, sensitive raw
rows, or unnecessary account/user values.

## Resolved natural-run window policy

The policy is based on the Scheduler configuration read on 2026-09-10 and its
operational events from 2026-09-01 through 2026-09-09:

- Task: `ProcurementMaterialsPlatform-C3_2_5-ShadowPilot`.
- Calendar trigger: every one day at `16:30:00` in the current Windows local
  timezone, which must be `Asia/Taipei` for validation.
- `StartWhenAvailable=true`.
- `MultipleInstancesPolicy=IgnoreNew`.
- Natural time-trigger events were recorded at `16:30:00` or `16:30:01`.
- The earliest observed non-natural late start was `16:45:59`, 15m59s after
  the slot.
- The longest observed execution completed in 7m21s; accepted 2026-09-09
  execution completed in 1m07s.

Deterministic rules:

1. A **scheduled slot** is each daily `16:30:00 Asia/Taipei` occurrence derived
   from the current task trigger. The slot is Scheduler context only and never
   becomes a source or canonical date.
2. An **on-time natural execution** has a unique Scheduler time-trigger event
   for the task and instance in `[slot, slot + 2 minutes)`. Two minutes is over
   one hundred times the observed 0-1 second trigger latency while remaining
   well below the first observed 15m59s recovery. Successful completion is
   expected within 10 minutes of instance start. Completion after 10 minutes
   but within 15 minutes is `WARNING`; absence of terminal completion evidence
   after 15 minutes is `BLOCKED` unless an explicit failure/termination event
   proves `FAIL`.
3. A **StartWhenAvailable late recovery** has no time-trigger or user-trigger
   event for that instance, starts after the two-minute natural window and
   before the next daily slot, and maps uniquely to the most recent unfulfilled
   slot while `StartWhenAvailable=true`. It is always reported as `WARNING`,
   even when the run succeeds. The observed `2026-09-04 16:45:59` and
   `2026-09-07 08:04:58` starts demonstrate same-day and overnight recovery.
4. A **manual execution** has user-trigger evidence and cannot satisfy a natural
   slot. A retry must have a distinct run identity and an explicit
   `retry_of_run_id`; it never replaces the original report.
5. A slot is **missed** only when the next daily slot begins and no natural or
   uniquely attributable StartWhenAvailable execution exists for the prior
   slot. The one-cadence boundary follows the actual daily trigger and permits
   the observed overnight recovery without inventing a same-evening cutoff.
   A proven missed slot is `FAIL`; inaccessible or ambiguous Scheduler evidence
   is `BLOCKED`.
6. More than one natural/recovery execution mapped to one slot is a
   **duplicate execution** and `FAIL`, even if V2 deduplication prevents a new
   append. A separately identified manual diagnostic run is not allowed to
   masquerade as the slot execution.

Configuration drift in task name, action, trigger cadence/time, timezone,
`StartWhenAvailable`, or instance policy blocks PASS until Work reviews the
new configuration. The validator must not repair Scheduler configuration.

## Severity rules

- `PASS`: all mandatory evidence is uniquely correlated and all safety and
  integrity checks pass. `pass_scope` must be `SHADOW_OPERATIONAL_ONLY`.
- `WARNING`: the run remains safe and complete but has a non-fatal operational
  condition such as late recovery, completion between 10 and 15 minutes,
  expected delayed/unconfirmed source state, recognized stderr warning, or a
  harmless duplicate-same candidate.
- `FAIL`: observed runner/application failure, wrapper result masking,
  unauthorized write/promotion, invalid or inferred date, duplicate slot
  execution, confirmed-value conflict, false completion, unexpected row delta,
  mutation/reorder/deletion of existing V2 rows, or proven missed slot.
- `BLOCKED`: required evidence cannot be accessed, parsed, uniquely correlated,
  stored, or validated. It is distinct from application failure and cannot
  produce PASS.
- `NOT_VERIFIED`: an individual claim lacks authoritative evidence. It remains
  visible independently of the aggregate result.

Observed application/safety failure takes aggregate precedence as `FAIL`.
Without an observed failure, missing mandatory evidence produces `BLOCKED`,
then non-fatal conditions produce `WARNING`, otherwise `PASS`. Warning and
`NOT_VERIFIED` details may never be silently omitted from a later report.

## Resolved evidence storage and retention policy

The authoritative local directory is:

`%LOCALAPPDATA%\ProcurementMaterialsPlatform\evidence\c3_2_7\YYYY-MM-DD\`

Authoritative content is JSON. Markdown is derived and replaceable only by
regeneration from the same JSON. Initial implementation must not write daily
evidence to Git, a database, or Google Sheet.

Files use deterministic final names and exclusive creation. Writes use a
unique temporary file in the destination directory, flush/close successfully,
then rename without replacing an existing final file. If the final file
already exists, byte/hash equality is `DUPLICATE_SAME`; different content is
preserved as a separate conflict record and the run becomes `BLOCKED`. Accepted
evidence is never overwritten.

All evidence has a minimum retention target of 400 days, covering one full
annual operating cycle plus 35 days for delayed review. The initial
implementation performs no automatic deletion because deletion is destructive
and outside this checkpoint. A future human-approved maintenance policy may
remove eligible PASS reports older than 400 days, but must preserve unresolved
WARNING/FAIL/BLOCKED reports and Human Gate references.

The directory inherits the current user's `%LOCALAPPDATA%` ACL. Expected write
access is limited to the current user and normal Windows administrative/system
principals. The implementation must not broaden or rewrite ACLs. A detected
broad untrusted write grant, ownership mismatch, or inability to establish the
evidence directory's integrity is `BLOCKED`.

Storage or retention-health failure must not redirect evidence into the
repository or Sheet and must not trigger cleanup. Preserve available evidence
in the existing wrapper log, emit a redacted diagnostic, and apply the
validator-only storage exit contract when the runner succeeded. A non-zero
runner result always retains precedence.

## Resolved Production/A:L verification policy

C3.2-7 initial implementation uses **Option 1: daily control-path verification
only**.

It must observe that the scheduled wrapper sets `ALLOW_GOOGLE_SHEET_WRITE=0`
and `ALLOW_PENDING_RAW_WRITE=0`, clears `CONTROLLED_WRITE_APPROVAL`, invokes the
approved Shadow runner, and records the repository/wrapper/runner versions. It
reports `production_status=DISABLED_BY_CONTROL`.

This proves the approved scheduled path is disabled; it does not prove actual
cell-level non-change. The report must therefore record
`a_l_change=NOT_VERIFIED` and must not say A:L is unchanged.

A daily A:L before/after digest is not required initially because it adds two
reads of sensitive production data, adds a pre-run dependency to a currently
downstream validator, increases failure coupling, and still proves equality
only within its measurement window. It does not prove that no other actor
wrote A:L. A later read-only digest pilot may be proposed at a separate review
if stronger cell-state evidence becomes necessary. It may never introduce a
write.

## Resolved validator-only exit-code contract

Existing runner behavior uses `0` for success and `1` for failure. C3.2-7
reserves `70-73` only when the Python runner returned `0`:

- `70`: deterministic validator `FAIL` after runner success.
- `71`: validator `BLOCKED` by unavailable, ambiguous, or unsupported evidence.
- `72`: validator internal/unhandled error.
- `73`: capture or report storage failure.

`PASS` and `WARNING` return `0`. If Python returns any non-zero code, the
wrapper returns that exact code regardless of validator outcome and records
`exit_origin=RUNNER`. Reserved codes therefore never replace or hide a Python
failure. A detached-finalizer failure occurring after wrapper exit cannot
change the completed wrapper code; it writes or causes a later `BLOCKED`
reconciliation record.

## Non-blocking operational policy

- Initial notification destination is local only: the evidence JSON/Markdown,
  wrapper log, stderr summary, and Scheduler action result. No email, chat,
  webhook, or external account is added in C3.2-7.
- The current Scheduler task name remains unchanged for evidence continuity.
- Hidden detached finalization is the initial approach because it avoids a
  Scheduler change. A future event-triggered validator task requires a separate
  Scheduler Human Gate.
- Automatic AI repair remains a future extension. It must be bounded,
  evidence-only before authorization, and unable to rerun market jobs or cross
  Production/Canonical Human Gates.

## Human Gate boundary

The validator may classify, report, and notify. Validator PASS, AI GO, or a
clean Shadow report does not authorize Production Write, Canonical Promotion,
Deferred Assembly persistence, destructive migration, Scheduler change,
source/security-policy change, or lower acceptance criteria.

The authoritative holiday-calendar source and source-native publication
timestamp distinct from collector availability remain persistent
`NOT_VERIFIED` findings. They do not force every safe Shadow run to WARNING
while fail-closed controls do not rely on them. Claiming or using an inferred
value is `FAIL`.

## Next gate

Implementation may begin only after Work accepts this tracked contract and
issues the contained Codex handoff. Operational acceptance still requires
deterministic tests, full regression, and one separately approved natural
Shadow validation compared with manual reconciliation.
