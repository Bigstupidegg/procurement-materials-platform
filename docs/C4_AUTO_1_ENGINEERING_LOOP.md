# C4-AUTO-1 Autonomous Engineering Loop V1.3

## Purpose

C4-AUTO-1 is repository-native development validation tooling. It provides bounded feedback loops while preserving fail-closed authority, deterministic evidence, Human review gates, and reversible development changes.

The framework consists of:

- the versioned Engineering Contract at `config/c4_auto/engineering_contract.v1.json`;
- strict local JSON Schemas under `schemas/c4_auto/`;
- an externally supplied Human-approved approval envelope;
- a per-stage Stage Manifest whose digest is frozen by that envelope;
- the Gate Runner at `scripts/c4_auto_gate_runner.py`;
- ignored approval-wide authority under `runtime/c4-auto/approvals/<approval_boundary_id>/`.

`PASS_CANDIDATE` is a derived machine verdict, not Human approval. The runner exposes no commit, push, pull-request, merge, release, deployment, network-acquisition, authoritative-write, private-write, or historical-PDF-acquisition operation.

## Human-approved bootstrap envelope

Every invocation supplies exactly five authority values:

```text
stage_id
approved_contract_sha256
approved_manifest_sha256
approved_base_sha
approval_id
```

The approval boundary remains the SHA-256 of canonical JSON containing exactly:

```text
stage_id
contract_sha256        = approved_contract_sha256
manifest_sha256        = approved_manifest_sha256
approved_base_sha
approval_id
```

`run_id`, command mode, paths, and schema metadata are not authority inputs. Observed file digests never define or relocate the boundary.

Initialization proceeds in this order:

1. validate the external envelope without trusting current authority files;
2. derive the original approval boundary;
3. create or reconstruct `approval.json` and its ledger;
4. honor existing terminal authority;
5. strictly parse and hash the observed contract and manifest;
6. compare observed digests and semantics with the approved envelope;
7. persist authority-significant initialization failures;
8. enable normal execution only after all checks pass.

Observed manifest or contract drift is terminalized against the original boundary. Restoring file bytes cannot clear the resulting `HUMAN_REVIEW_REQUIRED` state.

If a corrupt ledger cannot safely accept a terminal entry, the runner exclusively creates `bootstrap_terminal.json` at the approval root. This latch is checked before normal execution and has no automatic clearing path. A new Human-approved envelope is required to establish a different boundary.

## Interface

```powershell
py -3 scripts/c4_auto_gate_runner.py `
  --manifest <stage-manifest> `
  --stage-id <approved-stage-id> `
  --approved-contract-sha256 <digest> `
  --approved-manifest-sha256 <digest> `
  --approved-base-sha <git-sha> `
  --approval-id <human-approval-id> `
  --run-id <execution-id> `
  [--repo-root <repository>] `
  preflight|targeted|focused|final|evidence-only
```

Remediation uses `remediate` with a failure ID, structured root-cause status, change class, optional hard-gate IDs, and a concrete explanation. A run ID identifies an execution instance only.

## Stage Manifest

### Live integration-base authority (R1.4)

The approved `base.branch` is verified against the configured `origin` on every
bootstrap and repository guard using `git ls-remote --exit-code --refs origin
refs/heads/<approved-branch>`. Repository identity and the operator-controlled
origin URL must be checked before invocation. Exactly one response is accepted:
the full, exact branch ref and its 40-character lowercase commit SHA, equal to
the unchanged Human-approved base SHA. The base must also be a local commit and
an ancestor of HEAD. Local branches and cached remote-tracking refs do not
establish authority, and stale local branches cannot override a matching live
origin. The three-argument `verify_base` interface and `BASE_DRIFT` /
`HUMAN_REVIEW_REQUIRED` gate contract remain unchanged.

Missing branches, nonzero exits, transport errors, 30-second lookup timeouts,
stderr diagnostics, invalid branch names, malformed output, duplicate rows,
unexpected refs, and SHA mismatches fail closed. There is no offline bypass,
environment switch, or fallback to local/cached refs. Transport diagnostics are
not included in gate details. A bootstrap failure remains terminal for the
original approval boundary even if connectivity or the remote ref is restored.

Offline tests explicitly configure each disposable fixture's origin to a
separate bare repository created from its synthetic base. Production code still
executes the same live-origin query. That fixture repository is controlled test
authority, not an inferred or cached substitute for an unavailable production
origin. Response-injection unit tests are test-only and do not introduce a
runtime verification override. Fixture Git operations affect disposable
repositories only; candidate-repository commit/push gates remain closed.

The strict V1.3 manifest declares:

- stage and Human Design Gate identity;
- approved integration branch and base SHA;
- allowed mutable paths, protected-file hashes, approved pre-existing hashes, and frozen test-contract paths;
- structured counterexamples and Boolean acceptance criteria;
- targeted, focused, coupled, full, and replay/E2E checks;
- the two-cycle remediation limit;
- evidence requirements and rollback instructions.

`candidate_paths`, `base.require_clean_start`, free-text `additional_invariants`, decorative permissions, and acquisition declarations are not accepted. Unknown fields fail schema validation.

## Supported check kinds

Every accepted kind has runtime semantics and is included in command binding and evidence:

- `TEST`: requires `expected_test_count >= 1`, exactly one unambiguous count marker, observed count of at least one, exact count equality, skip-policy enforcement, allowed exit code, and an optional sentinel.
- `VALIDATION`: has no test-count or skip fields; it requires an allowed exit code and non-empty sentinel.
- `REPLAY`: is permitted only in the replay/E2E tier, executes in a fresh child process, has no test-count fields, and requires an allowed exit code and non-empty sentinel.

`Ran 0 tests` never passes a `TEST`. Duplicate or conflicting count markers also fail.

## Child-command trust boundary

Commands are frozen by the Human-approved manifest and run as fixed argument arrays with `shell=False`, a repository working directory, a minimal environment, no inherited `PYTHONPATH`, and a bounded timeout. Raw argv and environment values are not serialized; secret-like argv is rejected and output is redacted before persistence.

AUTO-1 does not claim that argument inspection can prove arbitrary child code has no side effects. Human approval of the frozen local validation command remains part of the trust root. Commands requiring Git writes, network acquisition, business or private writes, or rights-gated artifacts are ineligible for AUTO-1 approval and require a separate Human Gate.

## Repository dual-state model and candidate seal

Every guard observes committed divergence from the approved base, staged changes, unstaged changes, and non-ignored untracked files. A changed path outside `allowed_paths` produces terminal `SCOPE_DRIFT`.

Repository identity has four independent authorities: the approved base/current `HEAD`, the Git index, the working-tree filesystem, and untracked/explicit-absence state. Index identity is collected from the byte- and NUL-delimited output of `git ls-files --stage -z`; it is never inferred from filesystem metadata. Stage-0 records bind path, blob OID, Git mode, object type, and stage number. Every path missing from the index has an explicit `INDEX_ABSENT` record.

Relevant stage 1/2/3 entries produce terminal `UNMERGED_INDEX_STATE`. Duplicate stage-0 entries, zero or invalid object identities, unknown modes or object types, intent-to-add style states, symlink entries, and gitlinks are ineligible for stable sealing and produce terminal `UNSAFE_INDEX_STATE`. The runner never selects an unmerged side automatically.

Working-tree records independently bind filesystem presence, regular-file type, filesystem mode, size, and SHA-256. Missing files have an explicit `ABSENT` record. Symlinks, Windows reparse points, directories, devices, and other non-regular types fail closed.

`allowed_paths` is the Candidate Seal V2 authority. Its canonical path universe is the sorted unique union of matching paths from:

- the approved base tree;
- the current `HEAD` tree;
- every Git index stage;
- staged and unstaged change sets;
- non-ignored untracked files;
- retained paths required to preserve deleted or absent states.

For every universe path, the seal contains exactly one index record and one working-tree record. It also binds the approval boundary, approved contract and manifest digests, approved base SHA, current `HEAD` SHA, Test Baseline V2 digest, frozen command bindings, allowed-path patterns, and the universe itself. This keeps `index absent / worktree present`, `index present / worktree absent`, `both absent`, and `both present` mechanically distinguishable.

The complete dual-state scope is checked immediately before FULL, after FULL, after replay/E2E, and immediately before finalization. A universe, index, or filesystem difference produces terminal `CANDIDATE_DRIFT`; unchanged working-tree bytes cannot conceal a staged blob, mode, type, deletion, or new-path mutation.

## Approval-wide Test Baseline V2

The approval-wide test baseline uses the same canonical test-contract universe and freezes both index and working-tree state. A staged-only test or oracle blob, mode, type, presence, or deletion change therefore produces terminal `TEST_CONTRACT_DRIFT`, even when filesystem bytes are restored. Fresh processes and new run IDs reconstruct the same persisted dual-state baseline.

## Approval-wide authority

The generated layout is:

```text
runtime/c4-auto/approvals/<approval_boundary_id>/
  approval.json
  bootstrap_terminal.json       # only when the ledger cannot safely terminalize
  ledger/NNNNNNNN.json
  test_contract_baseline.json    # dual index/worktree Test Baseline V2
  runs/<run_id>/
    preflight.json
    events/NNNNNNNN.json
    logs/
    results/
    candidate_seals/
    evidence.json
    evidence.md
```

The ledger reconstructs contiguous entries, registered runs, event-chain counts and tips, referenced results and redacted logs, the frozen baseline, remediation count, candidate seals, terminal state, and finalized evidence. Missing, altered, duplicated, or unanchored authority fails closed.

Final `FAIL` and `HUMAN_REVIEW_REQUIRED` apply to the whole approval boundary and survive new processes, run IDs, and modes. The cumulative remediation maximum remains two.

## Counterexamples and acceptance

Each counterexample freezes a structured expected command result:

```json
{"passed": true, "failure_reasons": []}
```

Finalization compares that object exactly with the latest ledger-ordered result for the named check in the current validation run. Historical success from another run cannot satisfy a current counterexample.

Required tiers must contain checks and every required check must have current-run evidence. Acceptance criteria use mechanically observed or derived values only. The caller cannot select a verdict.

`PASS_CANDIDATE` is terminalized only after the candidate seal and required tiers pass, canonical machine evidence is constructed and strictly validated, `evidence.json` is persisted, its digest/reference is bound by an `EVIDENCE_FINALIZED` ledger entry, and fresh authority reconstruction reproduces the same derived verdict. The terminal PASS event and ledger entry are written only after those checks. Missing, invalid, incomplete, digest-mismatched, or unbound machine evidence therefore cannot create or preserve authoritative PASS.

## Evidence-only

Evidence-only first bootstraps and reconstructs the approved boundary. It does not register the supplied run ID, run checks, begin remediation, repair authority, or alter terminal state. It can only validate finalized, ledger-bound machine evidence and regenerate the exactly derived Markdown report. A persisted apparent PASS without that evidence fails closed and is never returned as `PASS_CANDIDATE`.

Persisted `FAIL` remains `FAIL`; persisted `HUMAN_REVIEW_REQUIRED` remains `HUMAN_REVIEW_REQUIRED`. Missing evidence cannot improve either verdict.

## Evidence integrity and limitation

Machine evidence binds approval identity, changed-file classes, remediation history, current-run command results, command bindings, acceptance outcomes, the candidate seal, event chain, and ledger tip. Markdown is derived and non-authoritative.

Local canonical hash chains detect accidental alteration and rollback. They do not provide authenticity against a malicious same-user actor capable of replacing all local files and recomputing every digest. A later phase may anchor final identities to remote CI or review records.

## Rollback and later phases

AUTO-1 changes development tooling only. Rollback is to revert the tooling after a separately authorized commit, stop invoking the runner, return to manual validation, and retain local evidence for Human review. AUTO-1 performs no deployment or data migration.

Commit, push, pull-request creation, remote CI, merge, release, and deployment remain outside AUTO-1 and require separately approved later gates.
