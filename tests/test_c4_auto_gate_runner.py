from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.c4_auto_gate_runner import (
    APPROVAL_BOUNDARY_INPUTS, CHANGE_CLASSES, EXPECTED_HARD_GATES, GateRunner, GateViolation, LocalSchemas,
    MAX_REMEDIATION_CYCLES, REPO_ROOT, ROOT_CAUSE_STATUSES, SCHEMA_DIRECTORY,
    SchemaValidationError, StateMachine, canonical_json_bytes,
    collect_repository_snapshot, collect_repository_state, compare_test_contract, evaluate_command_record,
    evidence_digest, load_contract, load_json_strict, load_manifest,
    parse_test_observations, path_is_allowed, redact_text, render_markdown,
    approval_boundary_digest, run_check, scope_drift, sha256_json, snapshot_paths,
    validate_contract_semantics, verify_base, verify_evidence_integrity,
)


RUNNER_PATH = REPO_ROOT / "scripts" / "c4_auto_gate_runner.py"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], check=check, capture_output=True, text=True, encoding="utf-8", errors="replace")


def workspace_tempdir() -> tempfile.TemporaryDirectory:
    root = REPO_ROOT / "runtime" / "c4-auto-tests"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root, ignore_cleanup_errors=True)


def init_repo(repo: Path) -> str:
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "c4-auto@example.invalid")
    git(repo, "config", "user.name", "C4 AUTO Test")
    (repo / ".gitignore").write_text("runtime/\n", encoding="utf-8")
    (repo / "inside.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".gitignore", "inside.txt")
    git(repo, "commit", "-m", "base")
    # Explicit fixture authority: an independent bare origin, never cached refs.
    authority = repo / "runtime" / "authority.git"
    authority.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "clone", "--bare", "--no-hardlinks", str(repo), str(authority))
    git(authority, "config", "user.email", "c4-auto@example.invalid")
    git(authority, "config", "user.name", "C4 AUTO Test")
    git(repo, "remote", "add", "origin", str(authority))
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def copy_framework(repo: Path) -> tuple[dict, str]:
    contract_source = REPO_ROOT / "config" / "c4_auto" / "engineering_contract.v1.json"
    contract_target = repo / "config" / "c4_auto" / "engineering_contract.v1.json"
    schema_target = repo / "schemas" / "c4_auto"
    contract_target.parent.mkdir(parents=True)
    schema_target.mkdir(parents=True)
    shutil.copy2(contract_source, contract_target)
    for path in SCHEMA_DIRECTORY.glob("*.json"):
        shutil.copy2(path, schema_target / path.name)
    return load_contract(repo, LocalSchemas(schema_target))


def check_spec(check_id: str, text: str = "OK", *, kind: str = "VALIDATION", exit_code: int = 0, sentinel: str | None = "OK", count: int | None = None, code: str | None = None) -> dict:
    command = code if code is not None else f"import sys; print({text!r}); raise SystemExit({exit_code})"
    spec = {
        "id": check_id, "kind": kind, "argv": [sys.executable, "-c", command],
        "timeout_seconds": 10, "expected_exit_codes": [0], "required_sentinel": sentinel,
    }
    if kind == "TEST":
        spec["expected_test_count"] = count if count is not None else 1
        spec["allowed_skips"] = []
    return spec


def manifest_payload(contract_sha: str, base_sha: str, approval_id: str = "SYNTHETIC-HUMAN-GATE") -> dict:
    checks = {tier: check_spec(f"{tier.upper()}_OK", kind="REPLAY" if tier == "replay_e2e" else "VALIDATION") for tier in ("targeted", "focused", "coupled", "full", "replay_e2e")}
    return {
        "schema_version": "c4_auto.stage_manifest.v1", "document_type": "c4_auto_stage_manifest",
        "stage_id": "C4-AUTO-TEST", "manifest_version": "1.3.0",
        "description": "Synthetic local deterministic test stage.",
        "contract": {"contract_id": "C4-AUTO-ENGINEERING-CONTRACT", "contract_version": "1.3.0", "contract_sha256": contract_sha},
        "design_gate": {"approval_id": approval_id, "approved_base_sha": base_sha, "approved_at": "2026-10-15T00:00:00Z"},
        "base": {"branch": "main", "sha": base_sha},
        "scope": {
            "allowed_paths": ["config/c4_auto/*", "schemas/c4_auto/*", "stage.json", "tests/test_fixture.py", "inside.txt"],
            "protected_files": [],
            "test_contract_paths": ["tests/test_fixture.py"], "allowed_preexisting_changes": [],
        },
        "change_classification": "BOUNDED_FIX",
        "counterexamples": [{"id": "CE-17", "invariant": "INV-08", "check_id": "FULL_OK", "expected_result": {"passed": True, "failure_reasons": []}}],
        "acceptance_criteria": [{"id": "AC-01", "source_check": "FULL_OK", "field": "passed", "operator": "TRUE", "expected": True, "required": True, "evidence_basis": "OBSERVED"}],
        "test_tiers": {tier: {"required": True, "checks": [spec]} for tier, spec in checks.items()},
        "remediation": {"max_cycles": 2, "root_cause_required": True},
        "evidence_requirements": {"machine_json": True, "markdown_derived": True, "required_fields": ["verdict", "evidence_sha256", "approval_boundary_id", "ledger_tip_sha256", "candidate_seal_sha256"]},
        "rollback_boundary": {"reversible": True, "instructions": ["Delete synthetic changes and return to base."]},
    }


class TemporaryFrameworkRepo:
    def __init__(self) -> None:
        self.temporary = workspace_tempdir()
        self.root = Path(self.temporary.name)
        self.base_sha = init_repo(self.root)
        self.contract, self.contract_sha = copy_framework(self.root)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.manifest = manifest_payload(self.contract_sha, self.base_sha)
        self.manifest_path = self.root / "stage.json"
        self.write_manifest()
        self.approval_envelope = self.current_envelope()

    def write_manifest(self) -> None:
        self.manifest_path.write_bytes(canonical_json_bytes(self.manifest))

    @property
    def manifest_sha(self) -> str:
        return sha256_json(self.manifest)

    def current_envelope(self) -> dict[str, str]:
        return {
            "stage_id": self.manifest["stage_id"], "approved_contract_sha256": self.contract_sha,
            "approved_manifest_sha256": self.manifest_sha, "approved_base_sha": self.base_sha,
            "approval_id": self.manifest["design_gate"]["approval_id"],
        }

    def approve_current_manifest(self) -> None:
        self.approval_envelope = self.current_envelope()

    def runner(self, run_id: str = "synthetic-run") -> GateRunner:
        runner = GateRunner(self.manifest_path, self.approval_envelope, run_id, self.root)
        runner.bootstrap(allow_terminal=True)
        return runner

    def cli(self, run_id: str, mode: str, *extra: str, expected: int | None = None) -> tuple[subprocess.CompletedProcess[str], dict]:
        command = [
            sys.executable, str(RUNNER_PATH), "--manifest", str(self.manifest_path),
            "--stage-id", self.approval_envelope["stage_id"],
            "--approved-contract-sha256", self.approval_envelope["approved_contract_sha256"],
            "--approved-manifest-sha256", self.approval_envelope["approved_manifest_sha256"],
            "--approved-base-sha", self.approval_envelope["approved_base_sha"],
            "--approval-id", self.approval_envelope["approval_id"], "--run-id", run_id,
            "--repo-root", str(self.root), mode, *extra,
        ]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if expected is not None:
            self.assert_return(result, expected)
        payload = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else {}
        return result, payload

    @staticmethod
    def assert_return(result: subprocess.CompletedProcess[str], expected: int) -> None:
        if result.returncode != expected:
            raise AssertionError(f"rc={result.returncode} expected={expected}\nstdout={result.stdout}\nstderr={result.stderr}")

    def pass_to_focused(self, run_id: str) -> None:
        self.cli(run_id, "preflight", expected=0)
        self.cli(run_id, "targeted", expected=0)
        self.cli(run_id, "focused", expected=0)

    def make_targeted_fail(self) -> None:
        self.manifest["test_tiers"]["targeted"]["checks"][0] = check_spec("TARGETED_OK", text="NO", exit_code=1, sentinel="OK")
        self.write_manifest()
        self.approve_current_manifest()

    def close(self) -> None:
        self.temporary.cleanup()


class R14BaseAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = workspace_tempdir()
        self.root = Path(self.temporary.name)
        self.base = init_repo(self.root)
        self.authority = self.root / "runtime" / "authority.git"

    def tearDown(self):
        self.temporary.cleanup()

    def assert_base_drift(self, branch="main"):
        with self.assertRaises(GateViolation) as caught:
            verify_base(self.root, branch, self.base)
        self.assertEqual(caught.exception.code, "BASE_DRIFT")
        self.assertEqual(caught.exception.verdict, "HUMAN_REVIEW_REQUIRED")

    def other_commit(self):
        return git(self.root, "commit-tree", f"{self.base}^{{tree}}", "-p", self.base, "-m", "fixture divergence").stdout.strip()

    def test_r14_matching_live_remote_passes_without_cached_ref(self):
        self.assertNotEqual(git(self.root, "rev-parse", "--verify", "origin/main", check=False).returncode, 0)
        verify_base(self.root, "main", self.base)

    def test_r14_missing_local_and_remote_branch_fails_closed(self):
        self.assert_base_drift("missing-integration")

    def test_r14_mismatched_live_remote_fails_with_matching_local(self):
        git(self.authority, "update-ref", "refs/heads/main", self.other_commit_in_authority())
        self.assert_base_drift()

    def other_commit_in_authority(self):
        return git(self.authority, "commit-tree", f"{self.base}^{{tree}}", "-p", self.base, "-m", "authority divergence").stdout.strip()

    def test_r14_matching_local_nonexistent_remote_branch_fails(self):
        git(self.authority, "update-ref", "-d", "refs/heads/main")
        self.assert_base_drift()

    def test_r14_stale_local_does_not_override_matching_live_remote(self):
        git(self.root, "update-ref", "refs/heads/approved-integration", self.other_commit())
        git(self.authority, "update-ref", "refs/heads/approved-integration", self.base)
        verify_base(self.root, "approved-integration", self.base)

    def test_r14_matching_cached_ref_cannot_override_divergent_live_remote(self):
        git(self.root, "update-ref", "refs/remotes/origin/main", self.base)
        git(self.authority, "update-ref", "refs/heads/main", self.other_commit_in_authority())
        self.assert_base_drift()

    def test_r14_matching_cache_and_local_cannot_replace_missing_origin(self):
        git(self.root, "remote", "remove", "origin")
        git(self.root, "update-ref", "refs/remotes/origin/main", self.base)
        self.assertEqual(git(self.root, "rev-parse", "origin/main").stdout.strip(), self.base)
        self.assert_base_drift()

    def test_r14_unreachable_remote_fails_closed(self):
        git(self.root, "remote", "set-url", "origin", str(self.root / "nonexistent-authority.git"))
        self.assert_base_drift()

    def remote_response(self, response=None, error=None):
        from scripts import c4_auto_gate_runner as module
        original = module._run_git

        def controlled(repo_root, args, check=True):
            if args[0] == "ls-remote":
                self.assertEqual(args, ["ls-remote", "--exit-code", "--refs", "origin", "refs/heads/main"])
                if error is not None:
                    raise error
                return response
            return original(repo_root, args, check=check)

        return patch.object(module, "_run_git", side_effect=controlled)

    def test_r14_remote_timeout_fails_as_gate_violation(self):
        with self.remote_response(error=subprocess.TimeoutExpired(["git", "ls-remote"], 30)):
            self.assert_base_drift()

    def test_r14_remote_os_error_fails_as_gate_violation(self):
        with self.remote_response(error=OSError("synthetic unavailable transport")):
            self.assert_base_drift()

    def test_r14_malformed_or_ambiguous_response_fails_closed(self):
        good = f"{self.base}\trefs/heads/main\n"
        responses = ["", "not-a-sha\trefs/heads/main\n", good + good,
                     good + f"{self.base}\trefs/heads/other\n",
                     f"{self.base}\trefs/heads/other\n", good + "unexpected\n",
                     f"{self.base} refs/heads/main\n", " " + good,
                     f"{self.base.upper()}\trefs/heads/main\n"]
        for stdout in responses:
            with self.subTest(response=stdout), self.remote_response(subprocess.CompletedProcess([], 0, stdout, "")):
                self.assert_base_drift()

    def test_r14_nonzero_exit_cannot_pass_even_with_matching_output(self):
        response = subprocess.CompletedProcess([], 2, f"{self.base}\trefs/heads/main\n", "")
        with self.remote_response(response):
            self.assert_base_drift()

    def test_r14_stderr_diagnostic_fails_closed_without_leaking(self):
        diagnostic = "synthetic-sensitive-diagnostic"
        response = subprocess.CompletedProcess([], 0, f"{self.base}\trefs/heads/main\n", diagnostic)
        with self.remote_response(response), self.assertRaises(GateViolation) as caught:
            verify_base(self.root, "main", self.base)
        self.assertEqual(caught.exception.code, "BASE_DRIFT")
        self.assertNotIn(diagnostic, str(caught.exception))

    def test_r14_branch_pattern_is_rejected_before_remote_lookup(self):
        with self.remote_response() as mocked:
            self.assert_base_drift("*")
        self.assertFalse(any(call.args[1][0] == "ls-remote" for call in mocked.call_args_list))

    def test_r14_bootstrap_missing_remote_is_persistent_across_processes(self):
        fixture = TemporaryFrameworkRepo()
        try:
            authority = fixture.root / "runtime" / "authority.git"
            git(authority, "update-ref", "-d", "refs/heads/main")
            _, blocked = fixture.cli("r14-missing", "preflight", expected=3)
            self.assertEqual(blocked["code"], "BASE_DRIFT")
            git(authority, "update-ref", "refs/heads/main", fixture.base_sha)
            _, retry = fixture.cli("r14-restored", "preflight", expected=3)
            self.assertEqual(retry["code"], "APPROVAL_TERMINAL")
        finally:
            fixture.close()


class ContractAndOriginalBehaviorTests(unittest.TestCase):
    def test_cycle3_exact_five_field_approval_boundary_identity(self):
        identity = {
            "schema_version": "c4_auto.approval_identity.v1", "stage_id": "C4-AUTO-TEST",
            "contract_sha256": "1" * 64, "manifest_sha256": "2" * 64,
            "approved_base_sha": "3" * 40, "approval_id": "HUMAN-APPROVAL",
        }
        exact = {key: identity[key] for key in APPROVAL_BOUNDARY_INPUTS}
        expected = sha256_json(exact)
        self.assertEqual(approval_boundary_digest(identity), expected)
        changed_schema = {**identity, "schema_version": "c4_auto.approval_identity.v999"}
        self.assertEqual(approval_boundary_digest(changed_schema), expected)
        replacements = {
            "stage_id": "C4-AUTO-OTHER", "contract_sha256": "4" * 64,
            "manifest_sha256": "5" * 64, "approved_base_sha": "6" * 40,
            "approval_id": "OTHER-HUMAN-APPROVAL",
        }
        for key, replacement in replacements.items():
            with self.subTest(key=key):
                self.assertNotEqual(approval_boundary_digest({**identity, key: replacement}), expected)

    def test_cycle3_fresh_process_reconstruction_has_identical_boundary_id(self):
        fixture = TemporaryFrameworkRepo()
        try:
            _, preflight = fixture.cli("identity-run", "preflight", expected=0)
            _, targeted = fixture.cli("identity-run", "targeted", expected=0)
            reconstructed = fixture.runner("identity-run").reconstruct()
            expected = approval_boundary_digest(fixture.runner("identity-run").approval_identity)
            self.assertEqual(preflight["approval_boundary_id"], expected)
            self.assertEqual(targeted["approval_boundary_id"], expected)
            self.assertIn("identity-run", reconstructed.runs)
        finally:
            fixture.close()

    def test_contract_and_schemas_are_strict(self):
        schemas = LocalSchemas()
        contract, digest = load_contract(schemas=schemas)
        self.assertEqual(contract["contract_version"], "1.3.0")
        self.assertEqual(len(contract["global_invariants"]), 15)
        self.assertEqual(len(contract["global_hard_gates"]), 17)
        self.assertEqual(len(digest), 64)
        invalid = deepcopy(contract); invalid["runner_capabilities"]["commit"] = True
        with self.assertRaises(SchemaValidationError): schemas.validate("contract", invalid)
        missing = deepcopy(contract); missing["global_invariants"].pop()
        with self.assertRaisesRegex(GateViolation, "CONTRACT_INVARIANTS_INVALID"): validate_contract_semantics(missing)

    def test_manifest_removed_authority_fields_and_permissions(self):
        fixture = TemporaryFrameworkRepo()
        try:
            invalid = deepcopy(fixture.manifest); invalid["hard_stops"] = []
            fixture.manifest_path.write_bytes(canonical_json_bytes(invalid))
            with self.assertRaises(SchemaValidationError): load_manifest(fixture.manifest_path, LocalSchemas(fixture.root / "schemas/c4_auto"), fixture.contract, fixture.contract_sha)
            invalid = deepcopy(fixture.manifest); invalid["permissions"] = {"commit": False}
            fixture.manifest_path.write_bytes(canonical_json_bytes(invalid))
            with self.assertRaises(SchemaValidationError): load_manifest(fixture.manifest_path, LocalSchemas(fixture.root / "schemas/c4_auto"), fixture.contract, fixture.contract_sha)
        finally: fixture.close()

    def test_state_machine_ce07_ce08_ce09(self):
        machine = StateMachine()
        for state in ("PRECHECK", "DESIGN_APPROVED", "IMPLEMENTING", "TESTING_TARGETED"):
            machine.transition(state)
        with self.assertRaisesRegex(GateViolation, "ILLEGAL_TRANSITION"): machine.transition("PASS_CANDIDATE")
        with self.assertRaisesRegex(GateViolation, "UNKNOWN_ROOT_CAUSE"): machine.request_remediation("UNKNOWN", "BOUNDED_FIX", [])
        with self.assertRaisesRegex(GateViolation, "HUMAN_DESIGN_GATE"): machine.request_remediation("CONFIRMED_BOUNDED", "ARCHITECTURE_CHANGE", [])

    def test_scope_base_and_test_contract_ce01_ce04_ce06(self):
        with workspace_tempdir() as temporary:
            root = Path(temporary); base = init_repo(root)
            (root / "inside.txt").write_text("changed", encoding="utf-8")
            (root / "outside.txt").write_text("outside", encoding="utf-8")
            snapshot = collect_repository_snapshot(root, base)
            self.assertEqual(scope_drift(snapshot, ["inside.txt"]), ("outside.txt",))
            self.assertTrue(path_is_allowed("tests/test_a.py", ["tests/test_*.py"]))
            with self.assertRaisesRegex(GateViolation, "BASE_DRIFT"): verify_base(root, "main", "0" * 40)
            test = root / "tests/test_a.py"; test.parent.mkdir(); test.write_text("A=1\n", encoding="utf-8")
            baseline = snapshot_paths(root, ["tests/test_*.py"]); test.write_text("A=2\n", encoding="utf-8")
            self.assertEqual(compare_test_contract(baseline, snapshot_paths(root, ["tests/test_*.py"])), ("tests/test_a.py",))

    def test_r1_3_index_and_worktree_records_are_independent(self):
        with workspace_tempdir() as temporary:
            root = Path(temporary); base = init_repo(root)
            original = (root / "inside.txt").read_bytes()
            (root / "inside.txt").write_text("staged\n", encoding="utf-8")
            git(root, "add", "--", "inside.txt")
            (root / "inside.txt").write_bytes(original)
            state = collect_repository_state(root, base, ["inside.txt"])
            index = state["index_state_records"][0]
            worktree = state["working_tree_state_records"][0]
            self.assertEqual(index["index_state"], "INDEX_PRESENT")
            self.assertEqual(index["git_mode"], "100644")
            self.assertEqual(index["object_type"], "blob")
            self.assertNotEqual(index["blob_oid"], git(root, "rev-parse", "HEAD:inside.txt").stdout.strip())
            self.assertEqual(worktree["content_sha256"], hashlib.sha256(original).hexdigest())

    def test_r1_3_unsafe_intent_to_add_is_hrr(self):
        fixture = TemporaryFrameworkRepo()
        try:
            fixture.manifest["scope"]["allowed_paths"].append("intent.txt")
            fixture.write_manifest(); fixture.approve_current_manifest()
            (fixture.root / "intent.txt").write_text("intent\n", encoding="utf-8")
            git(fixture.root, "add", "-N", "--", "intent.txt")
            _, result = fixture.cli("intent-to-add", "preflight", expected=3)
            self.assertEqual(result["code"], "UNSAFE_INDEX_STATE")
        finally:
            fixture.close()

    def test_command_integrity_ce02_ce03_ce12_ce13_ce16(self):
        with workspace_tempdir() as temporary:
            root = Path(temporary)
            spec = check_spec("COUNT", text="Ran 2 tests in 0.1s", kind="TEST", sentinel="DONE", count=3)
            record = run_check(spec, "full", root, root / "logs")
            self.assertFalse(record["passed"])
            self.assertIn("TEST_COUNT_MISMATCH", record["failure_reasons"])
            self.assertIn("REQUIRED_SENTINEL_MISSING", record["failure_reasons"])
            changed = dict(record); changed["argv_sha256"] = "0" * 64
            self.assertIn("COMMAND_BINDING_MISMATCH", evaluate_command_record(changed, spec, "full")[1])
            failed = run_check(check_spec("FAIL", exit_code=7), "full", root, root / "logs", 2)
            self.assertIn("UNEXPECTED_EXIT_CODE", failed["failure_reasons"])
            skipped = run_check(check_spec("SKIP", text="Ran 1 test\ntest_x ... skipped 'why'", kind="TEST", sentinel=None, count=1), "focused", root, root / "logs", 3)
            self.assertIn("UNEXPECTED_TEST_SKIP", skipped["failure_reasons"])

    def test_count_parser_and_redaction(self):
        self.assertEqual(parse_test_observations("Ran 4 tests in 0.1s", "")[0], 4)
        self.assertEqual(parse_test_observations("Ran 4 tests\nRan 4 tests", "")[2], "TEST_COUNT_AMBIGUOUS")
        self.assertEqual(parse_test_observations("Ran 4 tests\nRan 5 tests", "")[2], "TEST_COUNT_CONFLICT")
        safe = redact_text("token=abc password:xyz Authorization: Bearer hidden")
        self.assertNotIn("abc", safe); self.assertNotIn("xyz", safe); self.assertNotIn("hidden", safe)


class R1FreshProcessCounterexamples(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = TemporaryFrameworkRepo()

    def tearDown(self) -> None:
        self.fixture.close()

    def _failure_and_remediation(self, run_id: str, change_class: str = "BOUNDED_FIX") -> tuple[str, dict]:
        self.fixture.cli(run_id, "preflight", expected=0)
        _, failed = self.fixture.cli(run_id, "targeted", expected=2)
        failure_id = failed["failure_id"]
        expected = 0 if change_class == "BOUNDED_FIX" else 3
        _, response = self.fixture.cli(
            run_id, "remediate", "--failure-id", failure_id,
            "--root-cause-status", "CONFIRMED_BOUNDED", "--change-class", change_class,
            "--root-cause-detail", "synthetic bounded diagnosis", expected=expected,
        )
        return failure_id, response

    def test_r1_ce_01_terminal_hrr_survives_new_run(self):
        self.fixture.make_targeted_fail()
        _, terminal = self._failure_and_remediation("run-001", "ARCHITECTURE_CHANGE")
        self.assertEqual(terminal["verdict"], "HUMAN_REVIEW_REQUIRED")
        _, restarted = self.fixture.cli("run-002", "preflight", expected=3)
        self.assertEqual(restarted["verdict"], "HUMAN_REVIEW_REQUIRED")
        authority = self.fixture.runner("audit").reconstruct(allow_terminal=True)
        self.assertEqual(authority.terminal_state, "HUMAN_REVIEW_REQUIRED")
        self.assertNotIn("run-002", authority.runs)

    def test_r1_ce_02_budget_is_cumulative_across_run_ids(self):
        self.fixture.make_targeted_fail()
        self._failure_and_remediation("run-001")
        self._failure_and_remediation("run-002")
        self.fixture.cli("run-003", "preflight", expected=0)
        _, failed = self.fixture.cli("run-003", "targeted", expected=2)
        _, third = self.fixture.cli(
            "run-003", "remediate", "--failure-id", failed["failure_id"],
            "--root-cause-status", "CONFIRMED_BOUNDED", "--change-class", "BOUNDED_FIX",
            "--root-cause-detail", "third request", expected=3,
        )
        self.assertEqual(third["code"], "RETRY_LIMIT")
        authority = self.fixture.runner("audit").reconstruct(allow_terminal=True)
        self.assertEqual(len(authority.remediation_cycles), 2)
        self.assertEqual(authority.terminal_state, "HUMAN_REVIEW_REQUIRED")

    def test_r1_ce_03_baseline_drift_survives_new_process_and_run(self):
        self.fixture.cli("run-001", "preflight", expected=0)
        self.fixture.cli("run-001", "targeted", expected=0)
        (self.fixture.root / "tests/test_fixture.py").write_text("VALUE = 2\n", encoding="utf-8")
        _, drift = self.fixture.cli("run-002", "preflight", expected=3)
        self.assertEqual(drift["code"], "TEST_CONTRACT_DRIFT")
        authority = self.fixture.runner("audit").reconstruct(allow_terminal=True)
        self.assertEqual(authority.terminal_state, "HUMAN_REVIEW_REQUIRED")

    def test_r1_ce_04_deleted_event_tail_is_detected(self):
        self.fixture.cli("run-001", "preflight", expected=0)
        runner = self.fixture.runner("run-001")
        events = sorted((runner.run_directory / "events").glob("*.json"))
        events[-1].unlink()
        _, result = self.fixture.cli("run-001", "targeted", expected=3)
        self.assertEqual(result["code"], "APPROVAL_LEDGER_INTEGRITY_FAILURE")
        with self.assertRaisesRegex(GateViolation, "INCOMPLETE_LEDGER_COMMIT"):
            runner.reconstruct(allow_terminal=True)

    def test_r1_ce_05_deleted_result_or_log_is_detected(self):
        self.fixture.cli("run-001", "preflight", expected=0)
        self.fixture.cli("run-001", "targeted", expected=0)
        runner = self.fixture.runner("run-001")
        next((runner.run_directory / "logs").glob("*.log")).unlink()
        _, result = self.fixture.cli("run-001", "focused", expected=3)
        self.assertEqual(result["code"], "APPROVAL_LEDGER_INTEGRITY_FAILURE")
        with self.assertRaisesRegex(GateViolation, "INCOMPLETE_LEDGER_COMMIT"):
            runner.reconstruct(allow_terminal=True)

    def test_r1_ce_06_caller_selected_pass_is_not_a_cli_authority(self):
        command = [
            sys.executable, str(RUNNER_PATH), "--manifest", str(self.fixture.manifest_path),
            "--stage-id", self.fixture.approval_envelope["stage_id"],
            "--approved-contract-sha256", self.fixture.approval_envelope["approved_contract_sha256"],
            "--approved-manifest-sha256", self.fixture.approval_envelope["approved_manifest_sha256"],
            "--approved-base-sha", self.fixture.approval_envelope["approved_base_sha"],
            "--approval-id", self.fixture.approval_envelope["approval_id"], "--run-id", "run-001",
            "--repo-root", str(self.fixture.root), "--verdict", "PASS_CANDIDATE", "final",
        ]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.fixture.root / "runtime/c4-auto").exists())
        self.assertNotIn("PASS_CANDIDATE", result.stdout)

    def test_r1_ce_07_evidence_only_never_repairs_or_upgrades(self):
        self.fixture.cli("run-001", "preflight", expected=0)
        _, incomplete = self.fixture.cli("run-001", "evidence-only", expected=2)
        self.assertEqual(incomplete["code"], "MACHINE_EVIDENCE_MISSING")
        self.assertFalse((self.fixture.runner("run-001").run_directory / "evidence.json").exists())
        self.fixture.make_targeted_fail()

    def test_r1_ce_08_post_full_candidate_mutation_is_terminal(self):
        self.fixture.manifest["test_tiers"]["replay_e2e"]["checks"][0] = check_spec(
            "REPLAY_E2E_OK", kind="REPLAY", sentinel="OK",
            code="from pathlib import Path; Path('inside.txt').write_text('mutated\\n'); print('OK')",
        )
        self.fixture.manifest["counterexamples"][0]["check_id"] = "FULL_OK"
        self.fixture.write_manifest()
        self.fixture.approve_current_manifest()
        self.fixture.pass_to_focused("run-001")
        _, result = self.fixture.cli("run-001", "final", expected=3)
        self.assertEqual(result["code"], "CANDIDATE_DRIFT")
        authority = self.fixture.runner("audit").reconstruct(allow_terminal=True)
        self.assertEqual(authority.terminal_state, "HUMAN_REVIEW_REQUIRED")

    def test_r1_ce_09_duplicate_and_conflicting_counts_fail(self):
        for marker, expected_code in (("Ran 2 tests\nRan 2 tests", "TEST_COUNT_AMBIGUOUS"), ("Ran 2 tests\nRan 3 tests", "TEST_COUNT_CONFLICT")):
            fixture = TemporaryFrameworkRepo()
            try:
                fixture.manifest["test_tiers"]["targeted"]["checks"][0] = check_spec("TARGETED_OK", text=marker, kind="TEST", sentinel=None, count=2)
                fixture.write_manifest()
                fixture.approve_current_manifest()
                fixture.cli("run-count", "preflight", expected=0)
                _, result = fixture.cli("run-count", "targeted", expected=2)
                self.assertEqual(result["verdict"], "FAIL")
                record = next((fixture.runner("run-count").run_directory / "results").glob("*.json"))
                self.assertIn(expected_code, load_json_strict(record)["failure_reasons"])
            finally: fixture.close()

    def test_r1_ce_10_secret_argv_is_rejected_without_persistence(self):
        secret = "S3CRET-VALUE-DO-NOT-PERSIST"
        spec = self.fixture.manifest["test_tiers"]["targeted"]["checks"][0]
        spec["argv"] = [sys.executable, "-c", "print('OK')", "--api-key", secret]
        self.fixture.write_manifest()
        self.fixture.approve_current_manifest()
        self.fixture.cli("run-001", "preflight", expected=0)
        _, result = self.fixture.cli("run-001", "targeted", expected=3)
        self.assertEqual(result["code"], "SECRET_BEARING_ARGV")
        runtime = self.fixture.root / "runtime/c4-auto"
        persisted = "".join(path.read_text(encoding="utf-8", errors="replace") for path in runtime.rglob("*") if path.is_file())
        self.assertNotIn(secret, persisted)

    def test_r1_ce_11_architecture_root_cause_cli_is_terminal(self):
        self.fixture.make_targeted_fail()
        _, response = self._failure_and_remediation("run-architecture", "ARCHITECTURE_CHANGE")
        self.assertEqual(response["code"], "HUMAN_DESIGN_GATE")
        authority = self.fixture.runner("audit").reconstruct(allow_terminal=True)
        self.assertEqual(authority.terminal_state, "HUMAN_REVIEW_REQUIRED")

    def test_r1_ce_12_new_approval_gets_new_boundary_old_terminal_preserved(self):
        self.fixture.make_targeted_fail()
        self._failure_and_remediation("old-run", "ARCHITECTURE_CHANGE")
        old = self.fixture.runner("audit-old")
        old_id = old.approval_boundary_id
        self.fixture.manifest["design_gate"]["approval_id"] = "SYNTHETIC-HUMAN-GATE-NEW"
        self.fixture.write_manifest()
        self.fixture.approve_current_manifest()
        _, created = self.fixture.cli("new-run", "preflight", expected=0)
        new = self.fixture.runner("new-run")
        self.assertNotEqual(old_id, new.approval_boundary_id)
        self.assertIsNone(new.reconstruct().terminal_state)
        old_ledger = old.approval_directory / "ledger"
        self.assertTrue(old_ledger.is_dir())
        old_authority = old.ledger.reconstruct(old.approval_identity)
        self.assertEqual(old_authority.terminal_state, "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(created["approval_boundary_id"], new.approval_boundary_id)


class R12FreshProcessCounterexamples(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = TemporaryFrameworkRepo()

    def tearDown(self) -> None:
        self.fixture.close()

    def _persist_hrr(self, run_id: str = "hrr-run") -> None:
        self.fixture.make_targeted_fail()
        self.fixture.cli(run_id, "preflight", expected=0)
        _, failed = self.fixture.cli(run_id, "targeted", expected=2)
        self.fixture.cli(
            run_id, "remediate", "--failure-id", failed["failure_id"],
            "--root-cause-status", "CONFIRMED_BOUNDED", "--change-class", "ARCHITECTURE_CHANGE",
            "--root-cause-detail", "architecture boundary", expected=3,
        )

    def _persist_fail(self, run_id: str = "fail-run") -> dict:
        self.fixture.manifest["counterexamples"][0]["expected_result"] = {"passed": False, "failure_reasons": ["EXPECTED_NEGATIVE"]}
        self.fixture.write_manifest()
        self.fixture.approve_current_manifest()
        self.fixture.pass_to_focused(run_id)
        return self.fixture.cli(run_id, "final", expected=2)[1]

    def test_r1_2_ce_01_manifest_drift_persists_on_original_boundary(self):
        self.fixture.cli("run-001", "preflight", expected=0)
        original = self.fixture.manifest_path.read_bytes()
        self.fixture.manifest["description"] += " drift"
        self.fixture.write_manifest()
        _, result = self.fixture.cli("run-001", "targeted", expected=3)
        self.assertEqual(result["code"], "MANIFEST_DRIFT")
        self.fixture.manifest_path.write_bytes(original)
        authority = self.fixture.runner("audit").reconstruct(allow_terminal=True)
        self.assertEqual(authority.terminal_state, "HUMAN_REVIEW_REQUIRED")

    def test_r1_2_ce_02_restored_manifest_cannot_clear_hrr(self):
        self.fixture.cli("run-001", "preflight", expected=0)
        original = self.fixture.manifest_path.read_bytes()
        self.fixture.manifest["description"] += " drift"
        self.fixture.write_manifest()
        self.fixture.cli("run-001", "targeted", expected=3)
        self.fixture.manifest_path.write_bytes(original)
        _, result = self.fixture.cli("run-002", "preflight", expected=3)
        self.assertEqual(result["verdict"], "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(result["code"], "APPROVAL_TERMINAL")

    def test_r1_2_ce_03_contract_drift_persists_on_original_boundary(self):
        self.fixture.cli("run-001", "preflight", expected=0)
        path = self.fixture.root / "config/c4_auto/engineering_contract.v1.json"
        original = path.read_bytes()
        contract = load_json_strict(path); contract["rollback_policy"]["default_action"] += "_DRIFT"
        path.write_bytes(canonical_json_bytes(contract))
        _, result = self.fixture.cli("run-001", "targeted", expected=3)
        self.assertEqual(result["code"], "CONTRACT_DRIFT")
        path.write_bytes(original)
        self.assertEqual(self.fixture.runner("audit").reconstruct(allow_terminal=True).terminal_state, "HUMAN_REVIEW_REQUIRED")

    def test_r1_2_ce_04_new_allowed_mutable_path_after_full_is_drift(self):
        self.fixture.manifest["scope"]["allowed_paths"].append("unsealed.txt")
        self.fixture.manifest["test_tiers"]["replay_e2e"]["checks"][0] = check_spec(
            "REPLAY_E2E_OK", kind="REPLAY", code="from pathlib import Path; Path('unsealed.txt').write_text('new\\n'); print('OK')",
        )
        self.fixture.write_manifest(); self.fixture.approve_current_manifest()
        self.fixture.pass_to_focused("seal-run")
        _, result = self.fixture.cli("seal-run", "final", expected=3)
        self.assertEqual(result["code"], "CANDIDATE_DRIFT")

    def test_r1_2_ce_05_replay_mutation_is_candidate_drift(self):
        self.fixture.manifest["test_tiers"]["replay_e2e"]["checks"][0] = check_spec(
            "REPLAY_E2E_OK", kind="REPLAY", code="from pathlib import Path; Path('inside.txt').write_text('changed\\n'); print('OK')",
        )
        self.fixture.write_manifest(); self.fixture.approve_current_manifest()
        self.fixture.pass_to_focused("seal-run")
        _, result = self.fixture.cli("seal-run", "final", expected=3)
        self.assertEqual(result["code"], "CANDIDATE_DRIFT")

    def test_r1_2_ce_06_expected_zero_is_rejected(self):
        self.fixture.manifest["test_tiers"]["targeted"]["checks"][0] = check_spec(
            "TARGETED_OK", kind="TEST", count=0, code="print('Ran 0 tests')", sentinel=None,
        )
        self.fixture.write_manifest(); self.fixture.approve_current_manifest()
        _, result = self.fixture.cli("zero-schema", "preflight", expected=3)
        self.assertEqual(result["code"], "APPROVAL_AUTHORITY_INVALID")

    def test_r1_2_ce_07_observed_zero_cannot_pass(self):
        self.fixture.manifest["test_tiers"]["targeted"]["checks"][0] = check_spec(
            "TARGETED_OK", kind="TEST", count=1, code="print('Ran 0 tests')", sentinel=None,
        )
        self.fixture.write_manifest(); self.fixture.approve_current_manifest()
        self.fixture.cli("zero-run", "preflight", expected=0)
        _, result = self.fixture.cli("zero-run", "targeted", expected=2)
        self.assertEqual(result["verdict"], "FAIL")
        record = load_json_strict(next((self.fixture.runner("zero-run").run_directory / "results").glob("*.json")))
        self.assertIn("ZERO_TESTS_EXECUTED", record["failure_reasons"])

    def test_r1_2_ce_08_expected_result_mismatch_fails_candidate(self):
        self.fixture.manifest["scope"]["allowed_paths"].append("control.txt")
        self.fixture.manifest["test_tiers"]["full"]["checks"][0] = check_spec(
            "FULL_OK", code="from pathlib import Path; import sys; print('OK'); raise SystemExit(0 if Path('control.txt').exists() else 1)",
        )
        self.fixture.manifest["counterexamples"][0]["expected_result"] = {"passed": False, "failure_reasons": ["UNEXPECTED_EXIT_CODE"]}
        self.fixture.write_manifest(); self.fixture.approve_current_manifest()
        self.fixture.pass_to_focused("prior-run")
        _, prior = self.fixture.cli("prior-run", "final", expected=2)
        self.assertEqual(prior["verdict"], "FAIL")
        (self.fixture.root / "control.txt").write_text("current passes\n", encoding="utf-8")
        self.fixture.pass_to_focused("current-run")
        _, evidence = self.fixture.cli("current-run", "final", expected=2)
        self.assertEqual(evidence["verdict"], "FAIL")
        self.assertIn("counterexample:CE-17", evidence["failed"])

    def test_r1_2_ce_09_deleted_result_is_integrity_failure(self):
        self.fixture.cli("run-001", "preflight", expected=0)
        self.fixture.cli("run-001", "targeted", expected=0)
        next((self.fixture.runner("run-001").run_directory / "results").glob("*.json")).unlink()
        _, result = self.fixture.cli("run-001", "focused", expected=3)
        self.assertEqual(result["code"], "APPROVAL_LEDGER_INTEGRITY_FAILURE")

    def test_r1_2_ce_10_evidence_only_preserves_fail_across_run_id(self):
        self._persist_fail()
        _, result = self.fixture.cli("audit-new-run", "evidence-only", expected=2)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertNotIn("audit-new-run", self.fixture.runner("audit").reconstruct(allow_terminal=True).runs)

    def test_r1_2_ce_11_evidence_only_preserves_hrr_across_run_id(self):
        self._persist_hrr()
        _, result = self.fixture.cli("audit-new-run", "evidence-only", expected=3)
        self.assertEqual(result["verdict"], "HUMAN_REVIEW_REQUIRED")
        self.assertNotIn("audit-new-run", self.fixture.runner("audit").reconstruct(allow_terminal=True).runs)

    def test_r1_2_ce_12_unknown_authority_field_or_kind_is_rejected(self):
        cases = (("candidate_paths", []), ("permissions", {}), ("additional_invariants", []))
        for number, (field, value) in enumerate(cases, 1):
            fixture = TemporaryFrameworkRepo()
            try:
                fixture.manifest[field] = value
                fixture.write_manifest(); fixture.approve_current_manifest()
                _, result = fixture.cli(f"unknown-field-{number}", "preflight", expected=3)
                self.assertEqual(result["code"], "APPROVAL_AUTHORITY_INVALID")
            finally:
                fixture.close()
        self.fixture.manifest["test_tiers"]["targeted"]["checks"][0]["kind"] = "COMMAND"
        self.fixture.write_manifest(); self.fixture.approve_current_manifest()
        _, result = self.fixture.cli("unknown-kind", "preflight", expected=3)
        self.assertEqual(result["code"], "APPROVAL_AUTHORITY_INVALID")


class R13FreshProcessCounterexamples(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = TemporaryFrameworkRepo()

    def tearDown(self) -> None:
        self.fixture.close()

    def _approve_check(self, tier: str, code: str) -> None:
        kind = "REPLAY" if tier == "replay_e2e" else "VALIDATION"
        self.fixture.manifest["test_tiers"][tier]["checks"][0] = check_spec(
            f"{tier.upper()}_OK", kind=kind, code=code,
        )
        self.fixture.write_manifest()
        self.fixture.approve_current_manifest()

    def _final_drift(self, tier: str, code: str, run_id: str) -> dict:
        self._approve_check(tier, code)
        self.fixture.pass_to_focused(run_id)
        _, result = self.fixture.cli(run_id, "final", expected=3)
        self.assertEqual(result["code"], "CANDIDATE_DRIFT")
        return result

    def test_r1_3_ce_01_staged_blob_change_after_seal(self):
        code = (
            "from pathlib import Path; import subprocess; p=Path('inside.txt'); original=p.read_bytes(); "
            "p.write_bytes(b'staged-only\\n'); subprocess.run(['git','add','--','inside.txt'],check=True); "
            "p.write_bytes(original); print('OK')"
        )
        self._final_drift("full", code, "r13-ce01")

    def test_r1_3_ce_02_staged_executable_mode_change(self):
        code = "import subprocess; subprocess.run(['git','update-index','--chmod=+x','--','inside.txt'],check=True); print('OK')"
        self._final_drift("full", code, "r13-ce02")

    def test_r1_3_ce_03_staged_deletion_worktree_present(self):
        code = "import subprocess; subprocess.run(['git','rm','--cached','--','inside.txt'],check=True); print('OK')"
        self._final_drift("full", code, "r13-ce03")

    def test_r1_3_ce_04_new_staged_path_during_replay(self):
        self.fixture.manifest["scope"]["allowed_paths"].append("stage-new.txt")
        code = (
            "from pathlib import Path; import subprocess; Path('stage-new.txt').write_text('new\\n'); "
            "subprocess.run(['git','add','--','stage-new.txt'],check=True); print('OK')"
        )
        self._final_drift("replay_e2e", code, "r13-ce04")

    def test_r1_3_ce_05_real_unmerged_index_is_hrr(self):
        self.fixture.cli("r13-ce05", "preflight", expected=0)
        base = self.fixture.base_sha
        git(self.fixture.root, "checkout", "-b", "r13-ours")
        (self.fixture.root / "inside.txt").write_text("ours\n", encoding="utf-8")
        git(self.fixture.root, "add", "--", "inside.txt")
        git(self.fixture.root, "commit", "-m", "r13 ours")
        ours = git(self.fixture.root, "rev-parse", "HEAD").stdout.strip()
        git(self.fixture.root, "checkout", "-b", "r13-theirs", base)
        (self.fixture.root / "inside.txt").write_text("theirs\n", encoding="utf-8")
        git(self.fixture.root, "add", "--", "inside.txt")
        git(self.fixture.root, "commit", "-m", "r13 theirs")
        theirs = git(self.fixture.root, "rev-parse", "HEAD").stdout.strip()
        git(self.fixture.root, "checkout", "main")
        alternate_index = self.fixture.root / ".git" / "r13-unmerged-index"
        environment = dict(os.environ)
        environment["GIT_INDEX_FILE"] = str(alternate_index)
        subprocess.run(
            ["git", "-C", str(self.fixture.root), "read-tree", "-m", base, ours, theirs],
            env=environment, check=True, capture_output=True, text=True, encoding="utf-8",
        )
        os.replace(alternate_index, self.fixture.root / ".git" / "index")
        stages = git(self.fixture.root, "ls-files", "--stage", "--", "inside.txt").stdout
        self.assertIn(" 1\tinside.txt", stages)
        self.assertIn(" 2\tinside.txt", stages)
        self.assertIn(" 3\tinside.txt", stages)
        _, result = self.fixture.cli("r13-ce05", "targeted", expected=3)
        self.assertEqual(result["code"], "UNMERGED_INDEX_STATE")

    def test_r1_3_ce_06_staged_only_test_blob_mutation(self):
        self.fixture.cli("r13-ce06", "preflight", expected=0)
        self.fixture.cli("r13-ce06", "targeted", expected=0)
        path = self.fixture.root / "tests/test_fixture.py"
        original = path.read_bytes()
        path.write_text("VALUE = 2\n", encoding="utf-8")
        git(self.fixture.root, "add", "--", "tests/test_fixture.py")
        path.write_bytes(original)
        _, result = self.fixture.cli("r13-ce06", "focused", expected=3)
        self.assertEqual(result["code"], "TEST_CONTRACT_DRIFT")

    def test_r1_3_ce_07_staged_only_test_mode_mutation(self):
        git(self.fixture.root, "add", "--", "tests/test_fixture.py")
        self.fixture.cli("r13-ce07", "preflight", expected=0)
        self.fixture.cli("r13-ce07", "targeted", expected=0)
        git(self.fixture.root, "update-index", "--chmod=+x", "--", "tests/test_fixture.py")
        _, result = self.fixture.cli("r13-ce07", "focused", expected=3)
        self.assertEqual(result["code"], "TEST_CONTRACT_DRIFT")

    def test_r1_3_ce_08_replay_changes_index_only(self):
        code = (
            "from pathlib import Path; import subprocess; p=Path('inside.txt'); original=p.read_bytes(); "
            "p.write_bytes(b'replay-index\\n'); subprocess.run(['git','add','--','inside.txt'],check=True); "
            "p.write_bytes(original); print('OK')"
        )
        self._final_drift("replay_e2e", code, "r13-ce08")

    def test_r1_3_ce_09_stable_preseal_index_worktree_difference_passes(self):
        (self.fixture.root / "inside.txt").write_text("stable worktree difference\n", encoding="utf-8")
        self.fixture.pass_to_focused("r13-ce09")
        _, evidence = self.fixture.cli("r13-ce09", "final", expected=0)
        self.assertEqual(evidence["verdict"], "PASS_CANDIDATE")
        runner = self.fixture.runner("r13-ce09")
        reference = runner.reconstruct(allow_terminal=True).candidate_references[-1]
        seal = load_json_strict(runner.approval_directory / reference["path"])
        index = {item["path"]: item for item in seal["index_state_records"]}
        worktree = {item["path"]: item for item in seal["working_tree_state_records"]}
        self.assertEqual(index["inside.txt"]["index_state"], "INDEX_PRESENT")
        self.assertNotEqual(index["inside.txt"]["blob_oid"], git(self.fixture.root, "hash-object", "inside.txt").stdout.strip())
        self.assertTrue(worktree["inside.txt"]["filesystem_present"])

    def test_r1_3_ce_10_layered_absence_states_remain_distinct(self):
        self.fixture.manifest["scope"]["allowed_paths"].extend(["index-only.txt", "worktree-only.txt"])
        self.fixture.write_manifest(); self.fixture.approve_current_manifest()
        index_only = self.fixture.root / "index-only.txt"
        index_only.write_text("index only\n", encoding="utf-8")
        git(self.fixture.root, "add", "--", "index-only.txt")
        index_only.unlink()
        (self.fixture.root / "worktree-only.txt").write_text("worktree only\n", encoding="utf-8")
        git(self.fixture.root, "rm", "--", "inside.txt")
        self.fixture.pass_to_focused("r13-ce10")
        _, evidence = self.fixture.cli("r13-ce10", "final", expected=0)
        self.assertEqual(evidence["verdict"], "PASS_CANDIDATE")
        runner = self.fixture.runner("r13-ce10")
        reference = runner.reconstruct(allow_terminal=True).candidate_references[-1]
        seal = load_json_strict(runner.approval_directory / reference["path"])
        index = {item["path"]: item for item in seal["index_state_records"]}
        worktree = {item["path"]: item for item in seal["working_tree_state_records"]}
        self.assertTrue(index["index-only.txt"]["index_present"])
        self.assertFalse(worktree["index-only.txt"]["filesystem_present"])
        self.assertFalse(index["worktree-only.txt"]["index_present"])
        self.assertTrue(worktree["worktree-only.txt"]["filesystem_present"])
        self.assertFalse(index["inside.txt"]["index_present"])
        self.assertFalse(worktree["inside.txt"]["filesystem_present"])


class EndToEndEvidenceTests(unittest.TestCase):
    def test_full_path_ce14_ce15_ce17_and_evidence_replay(self):
        fixture = TemporaryFrameworkRepo()
        try:
            fixture.pass_to_focused("pass-run")
            _, evidence = fixture.cli("pass-run", "final", expected=0)
            self.assertEqual(evidence["verdict"], "PASS_CANDIDATE")
            self.assertEqual(evidence["failed"], [])
            verify_evidence_integrity(evidence, fixture.runner("pass-run").schemas)
            self.assertEqual(evidence["evidence_sha256"], evidence_digest(evidence))
            _, replay = fixture.cli("pass-run", "evidence-only", expected=0)
            self.assertEqual(replay["verdict"], "PASS_CANDIDATE")
            markdown = fixture.runner("pass-run").run_directory / "evidence.md"
            self.assertEqual(markdown.read_text(encoding="utf-8"), render_markdown(evidence))
        finally: fixture.close()

    def test_manifest_contract_and_evidence_tamper_fail_closed_ce05_ce15(self):
        fixture = TemporaryFrameworkRepo()
        try:
            fixture.cli("run-001", "preflight", expected=0)
            fixture.manifest["description"] += " changed"
            fixture.write_manifest()
            result = fixture.cli("run-001", "targeted")[0]
            self.assertEqual(result.returncode, 3)
        finally: fixture.close()


class Cycle3FinalizationCounterexamples(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = TemporaryFrameworkRepo()

    def tearDown(self) -> None:
        self.fixture.close()

    def _ready(self, run_id: str) -> Path:
        self.fixture.pass_to_focused(run_id)
        return self.fixture.runner(run_id).run_directory

    def _assert_no_terminal_pass(self) -> None:
        try:
            authority = self.fixture.runner("cycle3-audit").reconstruct(allow_terminal=True)
        except GateViolation:
            return
        self.assertNotEqual(authority.terminal_state, "PASS_CANDIDATE")

    def test_ce_final_01_missing_machine_evidence_cannot_terminalize_pass(self):
        run_directory = self._ready("ce-final-01")
        result, payload = self.fixture.cli("ce-final-01-audit", "evidence-only", expected=2)
        self.assertEqual(result.returncode, 2)
        self.assertNotEqual(payload.get("verdict"), "PASS_CANDIDATE")
        self.assertEqual(payload["code"], "MACHINE_EVIDENCE_MISSING")
        self.assertFalse((run_directory / "evidence.json").exists())
        self._assert_no_terminal_pass()

    def test_ce_final_02_evidence_validation_failure_cannot_terminalize_pass(self):
        run_directory = self._ready("ce-final-02")
        (run_directory / "evidence.json").mkdir()
        result, payload = self.fixture.cli("ce-final-02", "final", expected=3)
        self.assertNotEqual(payload.get("verdict"), "PASS_CANDIDATE")
        self.assertFalse((run_directory / "evidence.json").is_file())
        self._assert_no_terminal_pass()

    def test_ce_final_03_deleted_evidence_fresh_process_never_returns_pass(self):
        run_directory = self._ready("ce-final-03")
        _, evidence = self.fixture.cli("ce-final-03", "final", expected=0)
        self.assertEqual(evidence["verdict"], "PASS_CANDIDATE")
        (run_directory / "evidence.json").unlink()
        result, payload = self.fixture.cli("ce-final-03-audit", "evidence-only", expected=3)
        self.assertNotEqual(payload.get("verdict"), "PASS_CANDIDATE")
        self.assertEqual(result.returncode, 3)

    def test_ce_final_04_digest_or_ledger_binding_invalid_never_returns_pass(self):
        run_directory = self._ready("ce-final-04")
        self.fixture.cli("ce-final-04", "final", expected=0)
        evidence_path = run_directory / "evidence.json"
        evidence_path.write_bytes(evidence_path.read_bytes() + b"\n")
        result, payload = self.fixture.cli("ce-final-04-audit", "evidence-only", expected=3)
        self.assertNotEqual(payload.get("verdict"), "PASS_CANDIDATE")
        self.assertEqual(result.returncode, 3)

    def test_ce_final_05_required_evidence_field_missing_never_returns_pass(self):
        run_directory = self._ready("ce-final-05")
        self.fixture.cli("ce-final-05", "final", expected=0)
        evidence_path = run_directory / "evidence.json"
        evidence = load_json_strict(evidence_path)
        del evidence["candidate_seal_sha256"]
        evidence_path.write_bytes(canonical_json_bytes(evidence))
        _, payload = self.fixture.cli("ce-final-05-audit", "evidence-only", expected=3)
        self.assertNotEqual(payload.get("verdict"), "PASS_CANDIDATE")

    def test_ce_final_06_complete_bound_evidence_permits_pass(self):
        self._ready("ce-final-06")
        _, evidence = self.fixture.cli("ce-final-06", "final", expected=0)
        self.assertEqual(evidence["verdict"], "PASS_CANDIDATE")
        authority = self.fixture.runner("ce-final-06").reconstruct(allow_terminal=True)
        self.assertEqual(authority.terminal_state, "PASS_CANDIDATE")
        self.assertEqual(len(authority.evidence_references), 1)

    def test_ce_final_07_fresh_reconstruction_retains_valid_finalized_pass(self):
        self._ready("ce-final-07")
        self.fixture.cli("ce-final-07", "final", expected=0)
        _, replay = self.fixture.cli("ce-final-07-audit", "evidence-only", expected=0)
        self.assertEqual(replay["verdict"], "PASS_CANDIDATE")

    def test_ce_final_08_fresh_reconstruction_rejects_incomplete_apparent_pass(self):
        self._ready("ce-final-08")
        runner = self.fixture.runner("ce-final-08")
        runner.ledger.append("TERMINAL", "ce-final-08", {"state": "PASS_CANDIDATE", "code": "APPARENT_PASS"})
        result, payload = self.fixture.cli("ce-final-08-audit", "evidence-only", expected=3)
        self.assertNotEqual(payload.get("verdict"), "PASS_CANDIDATE")
        self.assertEqual(result.returncode, 3)


if __name__ == "__main__":
    unittest.main()

