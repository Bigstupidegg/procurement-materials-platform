# C3.2-6 Data Readiness / Market Calendar Hardening

Status: implementation scope approved after C3.2-5 Shadow Acceptance. This
checkpoint remains Shadow-only and stops before canonical persistence,
Deferred Assembly persistence, A:L, or Production Write.

## Exact scope

C3.2-6 hardens the date and readiness decisions used by the scheduled Shadow
runner and the pure canonicalization/assembly layers. It does not add a writer,
change the V2 schema, alter existing observations, or create historical data.

The following concepts are independent:

1. **Scheduler execution date/time** is the actual timezone-aware instant when
   Windows starts the runner. StartWhenAvailable may make this later than the
   configured trigger.
2. **Local business date** is a Taipei operational-calendar concept. A weekday
   is not asserted to be a business day unless an authoritative holiday
   calendar is supplied. This value is diagnostic and never selects a market
   date.
3. **Source market date** is parsed only from source-provided metadata. A
   weekend-labelled source date is ineligible for canonical targeting. No
   execution date, prior value, or neighbouring date may replace it.
4. **Source publication/availability date** is the date the observation first
   became available to this collector (`observation_at`). It is not called an
   official publication timestamp unless the source provides one. Yahoo final
   maturity requires later-day availability of a historical row for the exact
   target date.
5. **Canonical target date** is an explicit eligible source market date that is
   evaluated independently of execution/local dates. Completion still requires
   all 11 governed materials for that same date and source-specific maturity.

## Required behavior

- A missed-schedule recovery records its actual execution instant, collects
  normally when allowed, and re-evaluates stored open Shadow dates. It never
  changes the canonical target to the recovery date or manufactures missed-day
  observations.
- The 2026-09-07 08:05 recovery evidence targeting source date 2026-09-06 is
  preserved, but Sunday 2026-09-06 is never an eligible canonical target.
- Weekend execution is a normal collection skip. Weekday holiday status is
  not guessed: sources may be ready on different regional calendars, and an
  absent/non-mature source leaves the target unresolved.
- SMM readiness requires an explicit Daily Snapshot source date. LME readiness
  requires its explicit delayed official source date. Yahoo same-day data stays
  `YAHOO_UNCONFIRMED`; only later availability of a parseable historical Close
  for the exact target date is confirmed.
- Every run re-evaluates governed, explicit, weekday open Shadow dates so later
  LME/Yahoo availability can advance readiness without backfill or date
  substitution.
- Canonicalization fails closed for weekend targets, missing required
  materials, invalid timestamps, and confirmed-vs-confirmed conflicts.
- Deferred Assembly fails closed for a non-business target and continues to
  require canonical-only, 11/11 same-date inputs.

## Acceptance criteria

1. The five date concepts above are represented in code/logging and tests; no
   local/execution date can become a canonical target implicitly.
2. The 2026-09-07/2026-09-06 recovery scenario is covered by a regression test.
3. Weekend targets and incomplete 11-material canonical sets fail closed.
4. SMM, LME, Yahoo, delayed-source, and no-inference readiness behavior is
   covered by deterministic unit tests.
5. Existing append-only ID reconciliation/read-back and conflict safeguards
   remain intact.
6. `Market_Raw`, existing V2 rows, Scheduler configuration, and A:L are
   unchanged; `ALLOW_GOOGLE_SHEET_WRITE=0` remains enforced.
7. Targeted tests and the full repository regression suite pass.

## Rollback boundary

Rollback is a normal revert of the C3.2-6 code, tests, and this document. No
Sheet rollback, row deletion, or observation rewrite is permitted or needed.
Observations appended by later autonomous Shadow runs remain immutable history.
The scheduled launcher may be paused only at a separate operational Human Gate.

## Affected components

- `scripts/c3_2_market_calendar.py`: pure date/readiness policy.
- `scripts/c3_2_scheduled_shadow_runner.py`: execution context, source-date
  eligibility, and readiness-aware re-evaluation.
- `scripts/c3_2_observation_canonicalization.py`: target/maturity/completeness
  fail-closed rules.
- `scripts/c3_2_deferred_assembly.py`: non-business target rejection.
- corresponding deterministic tests only.

## Next gate

Passing C3.2-6 proves Shadow-only readiness behavior. Persisting canonical
decisions, enabling Deferred Assembly persistence, or writing A:L/Production
remains the next Human Gate and requires separate explicit approval.
