"""Fail-closed C4-AUTO R1 gate runner.

All authority is local, ignored, and scoped to a Human-approved boundary.  A
run identifier is only an execution identifier; it cannot reset terminal
state, remediation budget, or the frozen test contract.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIRECTORY = REPO_ROOT / "schemas" / "c4_auto"
RUNTIME_DIRECTORY = REPO_ROOT / "runtime" / "c4-auto"
SCHEMA_FILES = {
    "contract": "engineering_contract.schema.json",
    "manifest": "stage_manifest.schema.json",
    "evidence": "evidence_report.schema.json",
}
VERDICTS = ("PASS_CANDIDATE", "FAIL", "HUMAN_REVIEW_REQUIRED")
TIERS = ("targeted", "focused", "coupled", "full", "replay_e2e")
MAX_REMEDIATION_CYCLES = 2
CHECK_KINDS = ("TEST", "VALIDATION", "REPLAY")
EXPECTED_INVARIANTS = tuple(f"INV-{number:02d}" for number in range(1, 16))
EXPECTED_HARD_GATES = (
    "HG-ARCHITECTURE", "HG-CANONICAL-CONTRACT", "HG-PRODUCTION-WRITE",
    "HG-AUTHORITATIVE-WRITE", "HG-DESTRUCTIVE-MIGRATION", "HG-CANONICAL-PROMOTION",
    "HG-HISTORICAL-PDF", "HG-RIGHTS", "HG-PIT", "HG-RESEARCH-DATASET",
    "HG-BACKTEST", "HG-PREDICTION", "HG-DEPLOY", "HG-MERGE-RELEASE",
    "HG-ACCEPTANCE", "HG-RETRY", "HG-ROOT-CAUSE",
)
ROOT_CAUSE_STATUSES = ("CONFIRMED_BOUNDED", "UNKNOWN", "AMBIGUOUS", "NOT_VERIFIED")
CHANGE_CLASSES = (
    "BOUNDED_FIX", "ARCHITECTURE_CHANGE", "AUTHORITATIVE_SEMANTIC_CHANGE",
    "RIGHTS_BOUNDARY_CHANGE", "DESTRUCTIVE_CHANGE", "OTHER_HARD_GATE",
)
APPROVAL_BOUNDARY_INPUTS = (
    "stage_id", "contract_sha256", "manifest_sha256", "approved_base_sha", "approval_id",
)
APPROVAL_ENVELOPE_INPUTS = (
    "stage_id", "approved_contract_sha256", "approved_manifest_sha256", "approved_base_sha", "approval_id",
)
TERMINAL_STATES = frozenset({"PASS_CANDIDATE", "FAIL", "HUMAN_REVIEW_REQUIRED"})
TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"PRECHECK"}),
    "PRECHECK": frozenset({"DESIGN_APPROVED", "FAIL", "HUMAN_REVIEW_REQUIRED"}),
    "DESIGN_APPROVED": frozenset({"IMPLEMENTING", "HUMAN_REVIEW_REQUIRED"}),
    "IMPLEMENTING": frozenset({"TESTING_TARGETED", "HUMAN_REVIEW_REQUIRED"}),
    "TESTING_TARGETED": frozenset({"TESTING_FOCUSED", "REMEDIATING", "FAIL", "HUMAN_REVIEW_REQUIRED"}),
    "TESTING_FOCUSED": frozenset({"ADVERSARIAL_REVIEW", "REMEDIATING", "FAIL", "HUMAN_REVIEW_REQUIRED"}),
    "ADVERSARIAL_REVIEW": frozenset({"TESTING_COUPLED", "REMEDIATING", "FAIL", "HUMAN_REVIEW_REQUIRED"}),
    "REMEDIATING": frozenset({"TESTING_TARGETED", "FAIL", "HUMAN_REVIEW_REQUIRED"}),
    "TESTING_COUPLED": frozenset({"TESTING_FULL", "REMEDIATING", "FAIL", "HUMAN_REVIEW_REQUIRED"}),
    "TESTING_FULL": frozenset({"REPLAY_E2E", "EVIDENCE_FINALIZATION", "REMEDIATING", "FAIL", "HUMAN_REVIEW_REQUIRED"}),
    "REPLAY_E2E": frozenset({"EVIDENCE_FINALIZATION", "REMEDIATING", "FAIL", "HUMAN_REVIEW_REQUIRED"}),
    "EVIDENCE_FINALIZATION": TERMINAL_STATES,
    "PASS_CANDIDATE": frozenset(), "FAIL": frozenset(), "HUMAN_REVIEW_REQUIRED": frozenset(),
}


class StrictJsonError(ValueError):
    pass


class SchemaValidationError(ValueError):
    pass


class GateViolation(RuntimeError):
    def __init__(self, code: str, verdict: str, detail: str = "") -> None:
        if verdict not in VERDICTS:
            raise ValueError("invalid gate verdict")
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.verdict, self.detail = code, verdict, detail


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json_strict_bytes(payload: bytes) -> Any:
    if payload.startswith(b"\xef\xbb\xbf"):
        raise StrictJsonError("UTF-8 BOM is not permitted")
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StrictJsonError(str(exc)) from exc


def load_json_strict(path: Path) -> Any:
    try:
        return load_json_strict_bytes(path.read_bytes())
    except OSError as exc:
        raise StrictJsonError(f"cannot read {path}: {exc}") from exc


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def validate_approval_envelope(envelope: Mapping[str, Any]) -> dict[str, str]:
    """Validate the external Human-approved trust root without observed files."""
    if set(envelope) != set(APPROVAL_ENVELOPE_INPUTS):
        raise GateViolation("APPROVAL_ENVELOPE_INVALID", "HUMAN_REVIEW_REQUIRED", "fields")
    result = {key: str(envelope[key]) for key in APPROVAL_ENVELOPE_INPUTS}
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{2,79}", result["stage_id"]):
        raise GateViolation("APPROVAL_ENVELOPE_INVALID", "HUMAN_REVIEW_REQUIRED", "stage_id")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{2,127}", result["approval_id"]):
        raise GateViolation("APPROVAL_ENVELOPE_INVALID", "HUMAN_REVIEW_REQUIRED", "approval_id")
    for key in ("approved_contract_sha256", "approved_manifest_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", result[key]):
            raise GateViolation("APPROVAL_ENVELOPE_INVALID", "HUMAN_REVIEW_REQUIRED", key)
    if not re.fullmatch(r"[0-9a-f]{40}", result["approved_base_sha"]):
        raise GateViolation("APPROVAL_ENVELOPE_INVALID", "HUMAN_REVIEW_REQUIRED", "approved_base_sha")
    return result


def approval_identity_from_envelope(envelope: Mapping[str, str]) -> dict[str, str]:
    return {
        "schema_version": "c4_auto.approval_identity.v1",
        "stage_id": envelope["stage_id"],
        "contract_sha256": envelope["approved_contract_sha256"],
        "manifest_sha256": envelope["approved_manifest_sha256"],
        "approved_base_sha": envelope["approved_base_sha"],
        "approval_id": envelope["approval_id"],
    }


def _json_path(parts: Iterable[Any]) -> str:
    return "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in parts)


class LocalSchemas:
    """Explicit local schemas only; remote resolution is never configured."""

    def __init__(self, directory: Path = SCHEMA_DIRECTORY) -> None:
        self.directory = directory.resolve()
        self.schemas: dict[str, Mapping[str, Any]] = {}
        for name, filename in SCHEMA_FILES.items():
            path = (self.directory / filename).resolve()
            if path.parent != self.directory:
                raise SchemaValidationError("schema path escaped allowlist")
            document = load_json_strict(path)
            if not isinstance(document, dict):
                raise SchemaValidationError(f"{filename} is not an object")
            Draft202012Validator.check_schema(document)
            self.schemas[name] = document

    def validate(self, name: str, instance: Any) -> None:
        if name not in self.schemas:
            raise SchemaValidationError("unknown schema selection")
        validator = Draft202012Validator(self.schemas[name], format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(instance), key=lambda error: (list(error.absolute_path), error.message))
        if errors:
            detail = "; ".join(f"{_json_path(error.absolute_path)}: {error.message}" for error in errors[:8])
            raise SchemaValidationError(detail)

    def validate_definition(self, name: str, definition: str, instance: Any) -> None:
        schema = self.schemas[name]
        subschema = schema.get("$defs", {}).get(definition)
        if not isinstance(subschema, Mapping):
            raise SchemaValidationError(f"unknown definition: {definition}")
        validator = Draft202012Validator(
            {"$schema": schema["$schema"], "$defs": schema["$defs"], **subschema},
            format_checker=FormatChecker(),
        )
        errors = list(validator.iter_errors(instance))
        if errors:
            raise SchemaValidationError(f"{definition}: {errors[0].message}")


def validate_contract_semantics(contract: Mapping[str, Any]) -> None:
    if tuple(item["id"] for item in contract["global_invariants"]) != EXPECTED_INVARIANTS:
        raise GateViolation("CONTRACT_INVARIANTS_INVALID", "HUMAN_REVIEW_REQUIRED")
    if tuple(item["id"] for item in contract["global_hard_gates"]) != EXPECTED_HARD_GATES:
        raise GateViolation("CONTRACT_HARD_GATES_INVALID", "HUMAN_REVIEW_REQUIRED")
    if contract["remediation_policy"]["max_cycles"] != MAX_REMEDIATION_CYCLES:
        raise GateViolation("CONTRACT_RETRY_POLICY_INVALID", "HUMAN_REVIEW_REQUIRED")
    if tuple(contract["verdicts"]) != VERDICTS:
        raise GateViolation("CONTRACT_VERDICTS_INVALID", "HUMAN_REVIEW_REQUIRED")
    if contract["child_command_trust_model"]["model"] != "HUMAN_APPROVED_FROZEN_LOCAL_VALIDATION":
        raise GateViolation("CHILD_COMMAND_TRUST_INVALID", "HUMAN_REVIEW_REQUIRED")
    scope_policy = contract["scope_policy"]
    if scope_policy["repository_state_dimensions"] != ["APPROVED_BASE_HEAD", "GIT_INDEX", "WORKING_TREE", "UNTRACKED_ABSENT"]:
        raise GateViolation("REPOSITORY_STATE_POLICY_INVALID", "HUMAN_REVIEW_REQUIRED")
    if scope_policy["index_source"] != "GIT_LS_FILES_STAGE_Z" or scope_policy["candidate_seal_version"] != 2:
        raise GateViolation("REPOSITORY_STATE_POLICY_INVALID", "HUMAN_REVIEW_REQUIRED")
    if not scope_policy["test_baseline_dual_state"]:
        raise GateViolation("REPOSITORY_STATE_POLICY_INVALID", "HUMAN_REVIEW_REQUIRED")


def load_contract(repo_root: Path = REPO_ROOT, schemas: LocalSchemas | None = None) -> tuple[dict[str, Any], str]:
    document = load_json_strict(repo_root / "config" / "c4_auto" / "engineering_contract.v1.json")
    if not isinstance(document, dict):
        raise SchemaValidationError("contract must be an object")
    (schemas or LocalSchemas(repo_root / "schemas" / "c4_auto")).validate("contract", document)
    validate_contract_semantics(document)
    return document, sha256_json(document)


def validate_manifest_semantics(manifest: Mapping[str, Any], contract: Mapping[str, Any], contract_sha: str) -> None:
    pin = manifest["contract"]
    if pin["contract_id"] != contract["contract_id"] or pin["contract_version"] != contract["contract_version"]:
        raise GateViolation("CONTRACT_IDENTITY_MISMATCH", "HUMAN_REVIEW_REQUIRED")
    if pin["contract_sha256"] != contract_sha:
        raise GateViolation("CONTRACT_DRIFT", "HUMAN_REVIEW_REQUIRED")
    if manifest["design_gate"]["approved_base_sha"] != manifest["base"]["sha"]:
        raise GateViolation("BASE_APPROVAL_MISMATCH", "HUMAN_REVIEW_REQUIRED")
    checks: list[str] = []
    for tier in TIERS:
        spec = manifest["test_tiers"][tier]
        if spec["required"] and not spec["checks"]:
            raise GateViolation("REQUIRED_TIER_EMPTY", "FAIL", tier)
        for item in spec["checks"]:
            if item["kind"] == "REPLAY" and tier != "replay_e2e":
                raise GateViolation("CHECK_KIND_TIER_INVALID", "FAIL", item["id"])
            if tier == "replay_e2e" and item["kind"] != "REPLAY":
                raise GateViolation("CHECK_KIND_TIER_INVALID", "FAIL", item["id"])
            checks.append(item["id"])
    if len(checks) != len(set(checks)):
        raise GateViolation("DUPLICATE_CHECK_ID", "FAIL")
    if any(item["source_check"] not in checks for item in manifest["acceptance_criteria"]):
        raise GateViolation("UNKNOWN_ACCEPTANCE_SOURCE", "FAIL")


def load_manifest(path: Path, schemas: LocalSchemas, contract: Mapping[str, Any], contract_sha: str) -> tuple[dict[str, Any], str]:
    document = load_json_strict(path)
    if not isinstance(document, dict):
        raise SchemaValidationError("manifest must be an object")
    schemas.validate("manifest", document)
    validate_manifest_semantics(document, contract, contract_sha)
    return document, sha256_json(document)


def _run_git(repo_root: Path, args: Sequence[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", "-C", str(repo_root), *args], shell=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if check and result.returncode != 0:
        raise GateViolation("GIT_COMMAND_FAILED", "HUMAN_REVIEW_REQUIRED", redact_text(result.stderr.strip()))
    return result


def _run_git_bytes(repo_root: Path, args: Sequence[str], check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args], shell=False, capture_output=True,
        text=False, timeout=30,
    )
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GateViolation("GIT_COMMAND_FAILED", "HUMAN_REVIEW_REQUIRED", redact_text(detail))
    return result


def _normalize_git_path(payload: bytes) -> str:
    try:
        path = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateViolation("UNSAFE_INDEX_STATE", "HUMAN_REVIEW_REQUIRED", "undecodable path") from exc
    if "\\" in path or path.startswith("/") or any(part in ("", ".", "..") for part in path.split("/")):
        raise GateViolation("UNSAFE_INDEX_STATE", "HUMAN_REVIEW_REQUIRED", "non-canonical path")
    return path


def _git_paths(repo_root: Path, args: Sequence[str]) -> tuple[str, ...]:
    result = _run_git_bytes(repo_root, [*args, "-z"])
    return tuple(sorted(_normalize_git_path(item) for item in result.stdout.split(b"\0") if item))


def collect_git_index(repo_root: Path) -> dict[str, tuple[dict[str, Any], ...]]:
    """Collect the index from byte/NUL-delimited Git plumbing without filesystem inference."""
    result = _run_git_bytes(repo_root, ["ls-files", "--stage", "-z"])
    entries: dict[str, list[dict[str, Any]]] = {}
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            header, raw_path = raw.split(b"\t", 1)
            mode, oid, stage_payload = header.split(b" ")
            stage_number = int(stage_payload)
        except (ValueError, TypeError) as exc:
            raise GateViolation("UNSAFE_INDEX_STATE", "HUMAN_REVIEW_REQUIRED", "malformed index record") from exc
        if not re.fullmatch(rb"[0-7]{6}", mode) or not re.fullmatch(rb"[0-9a-fA-F]+", oid) or stage_number not in (0, 1, 2, 3):
            raise GateViolation("UNSAFE_INDEX_STATE", "HUMAN_REVIEW_REQUIRED", "invalid index record")
        path = _normalize_git_path(raw_path)
        record = {
            "path": path, "git_mode": mode.decode("ascii"),
            "object_oid": oid.decode("ascii").lower(), "stage_number": stage_number,
        }
        if stage_number == 0 and any(item["stage_number"] == 0 for item in entries.get(path, ())):
            raise GateViolation("UNSAFE_INDEX_STATE", "HUMAN_REVIEW_REQUIRED", f"duplicate stage-0: {path}")
        entries.setdefault(path, []).append(record)
    return {path: tuple(sorted(items, key=lambda item: item["stage_number"])) for path, items in sorted(entries.items())}


def _index_state_record(
    repo_root: Path, path: str, entries: Sequence[Mapping[str, Any]], *, intent_to_add: bool = False,
) -> dict[str, Any]:
    if any(item["stage_number"] in (1, 2, 3) for item in entries):
        raise GateViolation("UNMERGED_INDEX_STATE", "HUMAN_REVIEW_REQUIRED", path)
    stage_zero = [item for item in entries if item["stage_number"] == 0]
    if not stage_zero:
        return {"path": path, "index_present": False, "index_state": "INDEX_ABSENT"}
    if len(stage_zero) != 1:
        raise GateViolation("UNSAFE_INDEX_STATE", "HUMAN_REVIEW_REQUIRED", f"stage-0 cardinality: {path}")
    if intent_to_add:
        raise GateViolation("UNSAFE_INDEX_STATE", "HUMAN_REVIEW_REQUIRED", f"intent-to-add: {path}")
    item = stage_zero[0]
    oid, git_mode = item["object_oid"], item["git_mode"]
    if len(oid) not in (40, 64) or not re.fullmatch(r"[0-9a-f]+", oid) or set(oid) == {"0"}:
        raise GateViolation("UNSAFE_INDEX_STATE", "HUMAN_REVIEW_REQUIRED", f"invalid object identity: {path}")
    expected_type = {"100644": "blob", "100755": "blob", "120000": "blob", "160000": "commit"}.get(git_mode)
    if expected_type is None:
        raise GateViolation("UNSAFE_INDEX_STATE", "HUMAN_REVIEW_REQUIRED", f"unknown Git mode: {path}")
    object_result = _run_git(repo_root, ["cat-file", "-t", oid], check=False)
    object_type = object_result.stdout.strip() if object_result.returncode == 0 else ""
    if object_type != expected_type:
        raise GateViolation("UNSAFE_INDEX_STATE", "HUMAN_REVIEW_REQUIRED", f"unstable object identity: {path}")
    if git_mode not in ("100644", "100755"):
        raise GateViolation("UNSAFE_INDEX_STATE", "HUMAN_REVIEW_REQUIRED", f"unsafe Git type: {path}")
    return {
        "path": path, "index_present": True, "index_state": "INDEX_PRESENT",
        "stage_number": 0, "blob_oid": oid, "git_mode": git_mode, "object_type": object_type,
    }


def _working_tree_state_record(repo_root: Path, path: str) -> dict[str, Any]:
    target = repo_root / path
    try:
        info = target.lstat()
    except FileNotFoundError:
        return {"path": path, "filesystem_present": False, "file_type": "ABSENT"}
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if attributes & reparse_flag:
        raise GateViolation("CANDIDATE_UNSAFE_FILE", "HUMAN_REVIEW_REQUIRED", path)
    if stat.S_ISLNK(info.st_mode):
        file_type = "SYMLINK"
    elif stat.S_ISREG(info.st_mode):
        file_type = "REGULAR"
    elif stat.S_ISDIR(info.st_mode):
        file_type = "DIRECTORY"
    elif stat.S_ISCHR(info.st_mode):
        file_type = "CHAR_DEVICE"
    elif stat.S_ISBLK(info.st_mode):
        file_type = "BLOCK_DEVICE"
    elif stat.S_ISFIFO(info.st_mode):
        file_type = "FIFO"
    elif stat.S_ISSOCK(info.st_mode):
        file_type = "SOCKET"
    else:
        file_type = "OTHER"
    if file_type != "REGULAR":
        raise GateViolation("CANDIDATE_UNSAFE_FILE", "HUMAN_REVIEW_REQUIRED", path)
    return {
        "path": path, "filesystem_present": True, "file_type": "REGULAR",
        "filesystem_mode": stat.S_IMODE(info.st_mode), "size": info.st_size,
        "content_sha256": sha256_file(target),
    }


def collect_repository_state(
    repo_root: Path, base_sha: str, patterns: Sequence[str], retained_paths: Sequence[str] = (),
) -> dict[str, Any]:
    """Return canonical, separate index and filesystem state over one path universe."""
    snapshot = collect_repository_snapshot(repo_root, base_sha)
    base_paths = _git_paths(repo_root, ["ls-tree", "-r", "--name-only", base_sha])
    head_paths = _git_paths(repo_root, ["ls-tree", "-r", "--name-only", "HEAD"])
    index = collect_git_index(repo_root)
    candidates = set(base_paths) | set(head_paths) | set(index) | set(snapshot.all_changed) | set(retained_paths)
    universe = tuple(sorted(path for path in candidates if path_is_allowed(path, patterns)))
    if any(not path_is_allowed(path, patterns) for path in retained_paths):
        raise GateViolation("CANDIDATE_SEAL_COVERAGE", "HUMAN_REVIEW_REQUIRED", "retained scope")
    head_path_set = set(head_paths)
    staged_path_set = set(snapshot.staged)
    index_records = [
        _index_state_record(
            repo_root, path, index.get(path, ()),
            intent_to_add=path in index and path not in head_path_set and path not in staged_path_set,
        )
        for path in universe
    ]
    worktree_records = [_working_tree_state_record(repo_root, path) for path in universe]
    return {
        "scope_universe": list(universe),
        "index_state_records": index_records,
        "working_tree_state_records": worktree_records,
    }


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    branch: str
    head_sha: str
    committed: tuple[str, ...]
    staged: tuple[str, ...]
    unstaged: tuple[str, ...]
    untracked: tuple[str, ...]

    @property
    def all_changed(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.committed + self.staged + self.unstaged + self.untracked)))

    def changed_files(self) -> dict[str, list[str]]:
        return {"committed": list(self.committed), "staged": list(self.staged), "unstaged": list(self.unstaged), "untracked": list(self.untracked)}


def collect_repository_snapshot(repo_root: Path, base_sha: str) -> RepositorySnapshot:
    return RepositorySnapshot(
        branch=_run_git(repo_root, ["branch", "--show-current"]).stdout.strip(),
        head_sha=_run_git(repo_root, ["rev-parse", "HEAD"]).stdout.strip(),
        committed=_git_paths(repo_root, ["diff", "--name-only", f"{base_sha}..HEAD"]),
        staged=_git_paths(repo_root, ["diff", "--cached", "--name-only"]),
        unstaged=_git_paths(repo_root, ["diff", "--name-only"]),
        untracked=_git_paths(repo_root, ["ls-files", "--others", "--exclude-standard"]),
    )


def path_is_allowed(path: str, patterns: Sequence[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in patterns)


def scope_drift(snapshot: RepositorySnapshot, allowed: Sequence[str]) -> tuple[str, ...]:
    return tuple(path for path in snapshot.all_changed if not path_is_allowed(path, allowed))


def verify_base(repo_root: Path, branch: str, base_sha: str) -> None:
    """Require live origin authority; local and cached refs never substitute."""
    remote_ref = f"refs/heads/{branch}"
    try:
        if _run_git(repo_root, ["check-ref-format", remote_ref], check=False).returncode != 0:
            raise GateViolation("BASE_DRIFT", "HUMAN_REVIEW_REQUIRED", "invalid integration branch")
        commit = _run_git(repo_root, ["cat-file", "-t", base_sha], check=False)
        if commit.returncode != 0 or commit.stdout.strip() != "commit":
            raise GateViolation("BASE_DRIFT", "HUMAN_REVIEW_REQUIRED")
        if _run_git(repo_root, ["merge-base", "--is-ancestor", base_sha, "HEAD"], check=False).returncode != 0:
            raise GateViolation("BASE_DRIFT", "HUMAN_REVIEW_REQUIRED")
        remote = _run_git(repo_root, ["ls-remote", "--exit-code", "--refs", "origin", remote_ref], check=False)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        # Do not persist Git diagnostics, remote URLs, or exception payloads.
        raise GateViolation("BASE_DRIFT", "HUMAN_REVIEW_REQUIRED", "live origin lookup unavailable") from exc
    if remote.returncode != 0 or remote.stderr.strip():
        raise GateViolation("BASE_DRIFT", "HUMAN_REVIEW_REQUIRED", "live origin lookup failed")
    match = re.fullmatch(rf"([0-9a-f]{{40}})\t{re.escape(remote_ref)}\n?", remote.stdout)
    if match is None or match.group(1) != base_sha:
        raise GateViolation("BASE_DRIFT", "HUMAN_REVIEW_REQUIRED", "live origin ref missing, ambiguous, or mismatched")


def _matching_files(repo_root: Path, patterns: Sequence[str]) -> list[Path]:
    result: dict[str, Path] = {}
    for pattern in patterns:
        for path in repo_root.glob(pattern):
            if path.is_file() or path.is_symlink():
                result[path.relative_to(repo_root).as_posix()] = path
    return [result[key] for key in sorted(result)]


def snapshot_paths(repo_root: Path, patterns: Sequence[str]) -> dict[str, str]:
    return {path.relative_to(repo_root).as_posix(): sha256_file(path) for path in _matching_files(repo_root, patterns) if path.is_file() and not path.is_symlink()}


def compare_test_contract(baseline: Mapping[str, str], current: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(key for key in set(baseline) | set(current) if baseline.get(key) != current.get(key)))


def verify_declared_files(repo_root: Path, entries: Sequence[Mapping[str, str]], code: str) -> None:
    for entry in entries:
        path = repo_root / entry["path"]
        if not path.is_file() or path.is_symlink() or sha256_file(path) != entry["sha256"]:
            raise GateViolation(code, "HUMAN_REVIEW_REQUIRED", entry["path"])


class StateMachine:
    def __init__(self, state: str | None = None, remediation_cycles: int = 0) -> None:
        self.state, self.remediation_cycles = state, remediation_cycles

    def transition(self, target: str) -> None:
        if target not in TRANSITIONS.get(self.state, frozenset()):
            raise GateViolation("ILLEGAL_TRANSITION", "HUMAN_REVIEW_REQUIRED", f"{self.state}->{target}")
        self.state = target

    def request_remediation(self, root_cause_status: str, change_class: str, hard_gate_ids: Sequence[str]) -> None:
        if root_cause_status != "CONFIRMED_BOUNDED":
            raise GateViolation("UNKNOWN_ROOT_CAUSE", "HUMAN_REVIEW_REQUIRED")
        if change_class != "BOUNDED_FIX" or hard_gate_ids:
            raise GateViolation("HUMAN_DESIGN_GATE", "HUMAN_REVIEW_REQUIRED")
        if self.remediation_cycles >= MAX_REMEDIATION_CYCLES:
            raise GateViolation("RETRY_LIMIT", "HUMAN_REVIEW_REQUIRED")
        self.transition("REMEDIATING")
        self.remediation_cycles += 1


def redact_text(value: str) -> str:
    patterns = (
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+",
        r"(?i)((?:token|password|passwd|secret|api[_-]?key|credential|private[_-]?key)\s*[:=]\s*)[^\s]+",
    )
    for pattern in patterns:
        value = re.sub(pattern, r"\1[REDACTED]", value)
    return value


def sanitized_environment() -> dict[str, str]:
    allowed = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "LANG", "LC_ALL")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment["PYTHONNOUSERSITE"] = "1"
    environment.pop("PYTHONPATH", None)
    return environment


SECRET_OPTION = re.compile(r"(?i)(token|password|passwd|secret|api[_-]?key|credential|private[_-]?key)")


def validate_safe_argv(argv: Sequence[str]) -> None:
    for index, item in enumerate(argv):
        option = item.split("=", 1)[0].lstrip("-")
        if SECRET_OPTION.search(option) and ("=" in item or index + 1 < len(argv)):
            raise GateViolation("SECRET_BEARING_ARGV", "HUMAN_REVIEW_REQUIRED", "secret-like command option rejected")


def resolve_executable(argv0: str, cwd: Path) -> Path:
    path = Path(argv0)
    resolved = (cwd / path).resolve() if not path.is_absolute() and (cwd / path).exists() else Path(shutil.which(argv0) or argv0).resolve()
    if not resolved.is_file():
        raise GateViolation("EXECUTABLE_NOT_FOUND", "FAIL")
    return resolved


def parse_test_observations(stdout: str, stderr: str) -> tuple[int | None, list[str], str | None]:
    combined = f"{stdout}\n{stderr}"
    markers = [int(value) for value in re.findall(r"(?m)^Ran\s+(\d+)\s+tests?\b", combined)]
    if not markers:
        count, error = None, "TEST_COUNT_MISSING"
    elif len(markers) > 1 and len(set(markers)) == 1:
        count, error = markers[0], "TEST_COUNT_AMBIGUOUS"
    elif len(set(markers)) > 1:
        count, error = None, "TEST_COUNT_CONFLICT"
    else:
        count, error = markers[0], None
    skips = sorted(set(re.findall(r"(?m)^([^\r\n]+?)\s+\.\.\.\s+skipped\b", combined, flags=re.IGNORECASE)))
    return count, skips, error


def command_binding(spec: Mapping[str, Any], tier: str) -> str:
    binding = {
        "check_id": spec["id"], "kind": spec["kind"], "tier": tier,
        "argv_sha256": sha256_json(spec["argv"]), "timeout_seconds": spec["timeout_seconds"],
        "expected_exit_codes": spec["expected_exit_codes"], "required_sentinel": spec.get("required_sentinel"),
    }
    if spec["kind"] == "TEST":
        binding["expected_test_count"] = spec["expected_test_count"]
        binding["allowed_skips"] = spec["allowed_skips"]
    return sha256_json(binding)


def evaluate_command_record(record: Mapping[str, Any], spec: Mapping[str, Any], tier: str | None = None) -> tuple[bool, list[str]]:
    failures: list[str] = []
    expected_tier = tier or str(record.get("tier"))
    expected_exec = record.get("executable_path")
    binding_ok = (
        record.get("check_id") == spec["id"]
        and record.get("kind") == spec["kind"]
        and record.get("tier") == expected_tier
        and record.get("argv_sha256") == sha256_json(spec["argv"])
        and record.get("timeout_seconds") == spec["timeout_seconds"]
        and record.get("expected_exit_codes") == spec["expected_exit_codes"]
        and record.get("required_sentinel") == spec.get("required_sentinel")
        and record.get("argv_safe") is True
        and isinstance(expected_exec, str)
    )
    if spec["kind"] == "TEST":
        binding_ok = (
            binding_ok
            and record.get("expected_test_count") == spec["expected_test_count"]
            and record.get("allowed_skips") == spec["allowed_skips"]
        )
    else:
        binding_ok = (
            binding_ok
            and "expected_test_count" not in record
            and "observed_test_count" not in record
            and "count_error" not in record
            and "allowed_skips" not in record
            and "observed_skips" not in record
            and "unexpected_skips" not in record
        )
    if not binding_ok:
        failures.append("COMMAND_BINDING_MISMATCH")
    if record.get("timed_out"):
        failures.append("TIMEOUT")
    if record.get("exit_code") not in spec["expected_exit_codes"]:
        failures.append("UNEXPECTED_EXIT_CODE")
    if spec["kind"] == "TEST":
        expected_count = spec["expected_test_count"]
        if record.get("count_error"):
            failures.append(str(record["count_error"]))
        elif record.get("observed_test_count", 0) < 1:
            failures.append("ZERO_TESTS_EXECUTED")
        elif record.get("observed_test_count") != expected_count:
            failures.append("TEST_COUNT_MISMATCH")
        unexpected = sorted(set(record.get("observed_skips", ())) - set(spec["allowed_skips"]))
        if unexpected:
            failures.append("UNEXPECTED_TEST_SKIP")
    sentinel = spec.get("required_sentinel")
    if sentinel is not None and record.get("sentinel_found") is not True:
        failures.append("REQUIRED_SENTINEL_MISSING")
    return not failures, list(dict.fromkeys(failures))


def run_check(spec: Mapping[str, Any], tier: str, repo_root: Path, log_directory: Path, attempt: int = 1) -> dict[str, Any]:
    argv = [str(item) for item in spec["argv"]]
    validate_safe_argv(argv)
    executable = resolve_executable(argv[0], repo_root)
    started = utc_now()
    timed_out = False
    try:
        completed = subprocess.run(
            argv, cwd=repo_root, env=sanitized_environment(), shell=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=spec["timeout_seconds"],
        )
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out, exit_code = True, None
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    safe_out, safe_err = redact_text(stdout), redact_text(stderr)
    log_directory.mkdir(parents=True, exist_ok=True)
    stdout_path = log_directory / f"{tier}-{spec['id']}-{attempt}.stdout.log"
    stderr_path = log_directory / f"{tier}-{spec['id']}-{attempt}.stderr.log"
    stdout_path.write_text(safe_out, encoding="utf-8", newline="\n")
    stderr_path.write_text(safe_err, encoding="utf-8", newline="\n")
    record: dict[str, Any] = {
        "check_id": spec["id"], "kind": spec["kind"], "tier": tier, "attempt": attempt,
        "argv_safe": True, "argv_sha256": sha256_json(argv),
        "executable_path": str(executable), "executable_sha256": sha256_file(executable),
        "started_at": started, "ended_at": utc_now(), "timeout_seconds": spec["timeout_seconds"],
        "timed_out": timed_out, "exit_code": exit_code, "expected_exit_codes": list(spec["expected_exit_codes"]),
        "required_sentinel": spec.get("required_sentinel"),
        "sentinel_found": spec.get("required_sentinel") is None or spec["required_sentinel"] in safe_out or spec["required_sentinel"] in safe_err,
        "stdout_sha256": sha256_bytes(safe_out.encode("utf-8")), "stderr_sha256": sha256_bytes(safe_err.encode("utf-8")),
        "log_references": [stdout_path.name, stderr_path.name], "basis": "OBSERVED",
        "passed": False, "failure_reasons": [],
    }
    if spec["kind"] == "TEST":
        observed_count, skips, count_error = parse_test_observations(safe_out, safe_err)
        record.update({
            "expected_test_count": spec["expected_test_count"], "observed_test_count": observed_count,
            "count_error": count_error, "allowed_skips": list(spec["allowed_skips"]),
            "observed_skips": skips, "unexpected_skips": sorted(set(skips) - set(spec["allowed_skips"])),
        })
    passed, reasons = evaluate_command_record(record, spec, tier)
    record["passed"], record["failure_reasons"] = passed, reasons
    return record


def write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise GateViolation("IMMUTABLE_ARTIFACT_EXISTS", "HUMAN_REVIEW_REQUIRED", path.name) from exc


def event_digest(event: Mapping[str, Any]) -> str:
    return sha256_json({key: value for key, value in event.items() if key != "event_sha256"})


def ledger_digest(entry: Mapping[str, Any]) -> str:
    return sha256_json({key: value for key, value in entry.items() if key != "entry_sha256"})


def evidence_digest(evidence: Mapping[str, Any]) -> str:
    return sha256_json({key: value for key, value in evidence.items() if key != "evidence_sha256"})


def seal_digest(seal: Mapping[str, Any]) -> str:
    return sha256_json({key: value for key, value in seal.items() if key != "seal_sha256"})


def approval_boundary_digest(identity: Mapping[str, Any]) -> str:
    """Hash exactly the five Human-approved authority inputs, in canonical form."""
    try:
        authority = {key: identity[key] for key in APPROVAL_BOUNDARY_INPUTS}
    except KeyError as exc:
        raise GateViolation("APPROVAL_IDENTITY_INVALID", "HUMAN_REVIEW_REQUIRED", str(exc)) from exc
    return sha256_json(authority)


def bootstrap_terminal_digest(latch: Mapping[str, Any]) -> str:
    return sha256_json({key: value for key, value in latch.items() if key != "latch_sha256"})


def artifact_reference(path: Path, approval_directory: Path, role: str) -> dict[str, Any]:
    relative = path.resolve().relative_to(approval_directory.resolve()).as_posix()
    return {"path": relative, "role": role, "sha256": sha256_file(path), "size": path.stat().st_size}


@dataclass(frozen=True, slots=True)
class Authority:
    entries: tuple[dict[str, Any], ...]
    runs: frozenset[str]
    event_expectations: Mapping[str, tuple[int, str]]
    remediation_cycles: tuple[dict[str, Any], ...]
    terminal_state: str | None
    terminal_run_id: str | None
    baseline_reference: Mapping[str, Any] | None
    candidate_references: tuple[Mapping[str, Any], ...]
    evidence_references: tuple[Mapping[str, Any], ...]

    @property
    def ledger_tip_sha256(self) -> str | None:
        return self.entries[-1]["entry_sha256"] if self.entries else None


class ApprovalLedger:
    def __init__(self, directory: Path, boundary_id: str, schemas: LocalSchemas) -> None:
        self.directory, self.boundary_id, self.schemas = directory, boundary_id, schemas
        self.ledger_directory = directory / "ledger"

    def entries(self) -> list[dict[str, Any]]:
        if not self.ledger_directory.exists():
            return []
        paths = sorted(self.ledger_directory.glob("*.json"))
        result: list[dict[str, Any]] = []
        previous: str | None = None
        for expected, path in enumerate(paths, 1):
            if path.name != f"{expected:08d}.json":
                raise GateViolation("INCOMPLETE_LEDGER_COMMIT", "HUMAN_REVIEW_REQUIRED", "non-contiguous ledger")
            entry = load_json_strict(path)
            self.schemas.validate_definition("evidence", "ledger_entry", entry)
            if entry["sequence"] != expected or entry["approval_boundary_id"] != self.boundary_id:
                raise GateViolation("EVIDENCE_TAMPER", "HUMAN_REVIEW_REQUIRED", "ledger identity")
            if entry["previous_entry_sha256"] != previous or entry["entry_sha256"] != ledger_digest(entry):
                raise GateViolation("EVIDENCE_TAMPER", "HUMAN_REVIEW_REQUIRED", "ledger linkage")
            previous = entry["entry_sha256"]
            result.append(entry)
        return result

    def append(self, entry_type: str, run_id: str | None, payload: Mapping[str, Any], references: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
        entries = self.entries()
        entry: dict[str, Any] = {
            "schema_version": "c4_auto.approval_ledger_entry.v1", "sequence": len(entries) + 1,
            "timestamp": utc_now(), "approval_boundary_id": self.boundary_id, "entry_type": entry_type,
            "run_id": run_id, "previous_entry_sha256": entries[-1]["entry_sha256"] if entries else None,
            "payload": dict(payload), "artifact_references": [dict(item) for item in references],
        }
        entry["entry_sha256"] = ledger_digest(entry)
        self.schemas.validate_definition("evidence", "ledger_entry", entry)
        write_new(self.ledger_directory / f"{entry['sequence']:08d}.json", canonical_json_bytes(entry))
        return entry

    def reconstruct(self, expected_identity: Mapping[str, Any]) -> Authority:
        approval_path = self.directory / "approval.json"
        if not approval_path.is_file():
            raise GateViolation("INCOMPLETE_LEDGER_COMMIT", "HUMAN_REVIEW_REQUIRED", "missing approval identity")
        identity = load_json_strict(approval_path)
        self.schemas.validate_definition("evidence", "approval_identity", identity)
        if identity != expected_identity or approval_boundary_digest(identity) != self.boundary_id:
            raise GateViolation("EVIDENCE_TAMPER", "HUMAN_REVIEW_REQUIRED", "approval identity mismatch")
        entries = self.entries()
        if not entries or entries[0]["entry_type"] != "APPROVAL_CREATED":
            raise GateViolation("INCOMPLETE_LEDGER_COMMIT", "HUMAN_REVIEW_REQUIRED", "missing approval genesis")
        runs: set[str] = set()
        event_expectations: dict[str, tuple[int, str]] = {}
        remediation: list[dict[str, Any]] = []
        terminal_state: str | None = None
        terminal_run: str | None = None
        baseline_reference: Mapping[str, Any] | None = None
        candidate_references: list[Mapping[str, Any]] = []
        evidence_references: list[Mapping[str, Any]] = []
        finalized_evidence: dict[str, dict[str, Any]] = {}
        referenced: set[str] = set()
        for entry in entries:
            for reference in entry["artifact_references"]:
                relative = reference["path"]
                if relative in referenced:
                    raise GateViolation("EVIDENCE_TAMPER", "HUMAN_REVIEW_REQUIRED", "artifact referenced twice")
                referenced.add(relative)
                path = self.directory / relative
                if not path.is_file() or path.is_symlink():
                    code = "MACHINE_EVIDENCE_MISSING" if reference.get("role") == "machine_evidence" else "INCOMPLETE_LEDGER_COMMIT"
                    raise GateViolation(code, "HUMAN_REVIEW_REQUIRED", relative)
                if path.stat().st_size != reference["size"] or sha256_file(path) != reference["sha256"]:
                    code = "EVIDENCE_DIGEST_MISMATCH" if reference.get("role") == "machine_evidence" else "INCOMPLETE_LEDGER_COMMIT"
                    raise GateViolation(code, "HUMAN_REVIEW_REQUIRED", relative)
            kind, run_id = entry["entry_type"], entry["run_id"]
            if kind == "RUN_REGISTERED":
                if not run_id or run_id in runs:
                    raise GateViolation("EVIDENCE_TAMPER", "HUMAN_REVIEW_REQUIRED", "run registration")
                runs.add(run_id)
            elif kind == "RUN_EVENT":
                if run_id not in runs:
                    raise GateViolation("INCOMPLETE_LEDGER_COMMIT", "HUMAN_REVIEW_REQUIRED", "event before run")
                expected_count = event_expectations.get(run_id, (0, ""))[0] + 1
                if entry["payload"].get("event_count") != expected_count:
                    raise GateViolation("INCOMPLETE_LEDGER_COMMIT", "HUMAN_REVIEW_REQUIRED", "event count")
                event_expectations[run_id] = (expected_count, entry["payload"].get("event_tip_sha256", ""))
            elif kind == "TEST_BASELINE_FROZEN":
                if baseline_reference is not None or len(entry["artifact_references"]) != 1:
                    raise GateViolation("EVIDENCE_TAMPER", "HUMAN_REVIEW_REQUIRED", "baseline lineage")
                baseline_reference = entry["artifact_references"][0]
            elif kind == "REMEDIATION_STARTED":
                remediation.append(dict(entry["payload"]))
                if len(remediation) > MAX_REMEDIATION_CYCLES:
                    raise GateViolation("EVIDENCE_TAMPER", "HUMAN_REVIEW_REQUIRED", "remediation overflow")
            elif kind == "CANDIDATE_SEALED":
                candidate_references.extend(entry["artifact_references"])
            elif kind == "EVIDENCE_FINALIZED":
                references = entry["artifact_references"]
                if (
                    run_id not in runs or run_id in finalized_evidence or len(references) != 1
                    or references[0].get("role") != "machine_evidence"
                ):
                    raise GateViolation("EVIDENCE_ARTIFACT_UNBOUND", "HUMAN_REVIEW_REQUIRED", "evidence lineage")
                evidence = load_json_strict(self.directory / references[0]["path"])
                verify_evidence_integrity(evidence, self.schemas)
                if (
                    evidence.get("approval_boundary_id") != self.boundary_id
                    or evidence.get("approval_identity") != expected_identity
                    or evidence.get("run_id") != run_id
                    or evidence.get("verdict") != entry["payload"].get("verdict")
                    or evidence.get("evidence_sha256") != entry["payload"].get("evidence_sha256")
                    or evidence.get("ledger_tip_sha256") != entry["previous_entry_sha256"]
                ):
                    raise GateViolation("EVIDENCE_ARTIFACT_UNBOUND", "HUMAN_REVIEW_REQUIRED", "evidence binding")
                finalized_evidence[run_id] = evidence
                evidence_references.extend(references)
            elif kind == "TERMINAL":
                if terminal_state is not None or entry["payload"].get("state") not in TERMINAL_STATES:
                    raise GateViolation("EVIDENCE_TAMPER", "HUMAN_REVIEW_REQUIRED", "terminal lineage")
                if entry["payload"]["state"] == "PASS_CANDIDATE":
                    evidence = finalized_evidence.get(run_id or "")
                    if evidence is None or evidence.get("verdict") != "PASS_CANDIDATE":
                        raise GateViolation("MACHINE_EVIDENCE_MISSING", "HUMAN_REVIEW_REQUIRED", "PASS without bound evidence")
                terminal_state, terminal_run = entry["payload"]["state"], run_id
        run_root = self.directory / "runs"
        actual_runs = {path.name for path in run_root.iterdir() if path.is_dir()} if run_root.exists() else set()
        if actual_runs != runs:
            raise GateViolation("INCOMPLETE_LEDGER_COMMIT", "HUMAN_REVIEW_REQUIRED", "registered run mismatch")
        for run_id in runs:
            events_dir = run_root / run_id / "events"
            paths = sorted(events_dir.glob("*.json")) if events_dir.exists() else []
            expected_count, expected_tip = event_expectations.get(run_id, (0, ""))
            if len(paths) != expected_count:
                raise GateViolation("INCOMPLETE_LEDGER_COMMIT", "HUMAN_REVIEW_REQUIRED", f"{run_id} event tail")
            previous: str | None = None
            for sequence, path in enumerate(paths, 1):
                event = load_json_strict(path)
                self.schemas.validate_definition("evidence", "run_event", event)
                if event["run_id"] != run_id or event["sequence"] != sequence or event["previous_event_sha256"] != previous or event["event_sha256"] != event_digest(event):
                    raise GateViolation("EVIDENCE_TAMPER", "HUMAN_REVIEW_REQUIRED", f"{run_id} event chain")
                previous = event["event_sha256"]
            if (previous or "") != expected_tip:
                raise GateViolation("INCOMPLETE_LEDGER_COMMIT", "HUMAN_REVIEW_REQUIRED", f"{run_id} event tip")
        authority_dirs = ("events", "results", "logs", "candidate_seals")
        actual_artifacts = {
            path.relative_to(self.directory).as_posix()
            for run_id in runs for name in authority_dirs
            for path in (run_root / run_id / name).glob("*") if path.is_file()
        }
        actual_artifacts.update(
            path.relative_to(self.directory).as_posix()
            for run_id in runs for name in ("preflight.json", "evidence.json")
            if (path := run_root / run_id / name).is_file()
        )
        referenced_runtime = {item for item in referenced if item.startswith("runs/")}
        if actual_artifacts != referenced_runtime:
            raise GateViolation("INCOMPLETE_LEDGER_COMMIT", "HUMAN_REVIEW_REQUIRED", "unanchored artifact")
        return Authority(tuple(entries), frozenset(runs), event_expectations, tuple(remediation), terminal_state, terminal_run, baseline_reference, tuple(candidate_references), tuple(evidence_references))


def _resolve_field(value: Any, dotted_path: str) -> Any:
    for part in dotted_path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _compare(operator: str, observed: Any, expected: Any) -> bool:
    if operator == "EQ": return observed == expected
    if operator == "NE": return observed != expected
    if operator == "GE": return observed is not None and observed >= expected
    if operator == "LE": return observed is not None and observed <= expected
    if operator == "TRUE": return observed is True
    if operator == "FALSE": return observed is False
    if operator == "EMPTY": return observed in (None, "", [], {})
    if operator == "NOT_EMPTY": return observed not in (None, "", [], {})
    return False


def evaluate_acceptance(criteria: Sequence[Mapping[str, Any]], records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, Mapping[str, Any]] = {}
    for record in records:
        by_id[record["check_id"]] = record
    results: list[dict[str, Any]] = []
    for criterion in criteria:
        source = by_id.get(criterion["source_check"])
        observed = _resolve_field(source, criterion["field"]) if source else None
        results.append({"id": criterion["id"], "basis": criterion["evidence_basis"], "passed": bool(source) and _compare(criterion["operator"], observed, criterion["expected"]), "observed": observed})
    return results


def verify_evidence_integrity(evidence: Mapping[str, Any], schemas: LocalSchemas) -> None:
    schemas.validate("evidence", evidence)
    if evidence["evidence_sha256"] != evidence_digest(evidence):
        raise GateViolation("EVIDENCE_TAMPER", "HUMAN_REVIEW_REQUIRED", "evidence digest")


def render_markdown(evidence: Mapping[str, Any]) -> str:
    lines = [
        f"# C4-AUTO Evidence — {evidence['stage_id']}", "",
        f"- Run: `{evidence['run_id']}`", f"- Approval boundary: `{evidence['approval_boundary_id']}`",
        f"- Verdict: **{evidence['verdict']}**", f"- Ledger tip: `{evidence['ledger_tip_sha256']}`",
        f"- Evidence SHA-256: `{evidence['evidence_sha256']}`", "", "## Test tiers", "",
    ]
    for tier in TIERS:
        field = "full_regression" if tier == "full" else tier + ("_tests" if tier in ("targeted", "focused", "coupled") else "")
        records = evidence[field]
        lines.append(f"- {tier}: {sum(1 for item in records if item['passed'])}/{len(records)} passed")
    lines.extend(["", "## Failures", ""] + ([f"- {item}" for item in evidence["failed"]] or ["- None"]))
    return "\n".join(lines) + "\n"


class GateRunner:
    def __init__(self, manifest_path: Path, approval_envelope: Mapping[str, Any], run_id: str, repo_root: Path = REPO_ROOT) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", run_id):
            raise GateViolation("INVALID_RUN_ID", "FAIL")
        self.repo_root = repo_root.resolve()
        self.manifest_path = manifest_path.resolve()
        self.run_id = run_id
        self.approval_envelope = validate_approval_envelope(approval_envelope)
        self.approval_identity = approval_identity_from_envelope(self.approval_envelope)
        self.approval_boundary_id = approval_boundary_digest(self.approval_identity)
        self.approval_directory = self.repo_root / "runtime" / "c4-auto" / "approvals" / self.approval_boundary_id
        self.run_directory = self.approval_directory / "runs" / self.run_id
        self.bootstrap_terminal_path = self.approval_directory / "bootstrap_terminal.json"
        self.schemas: LocalSchemas | None = None
        self.ledger: ApprovalLedger | None = None
        self.contract: dict[str, Any] = {}
        self.manifest: dict[str, Any] = {}
        self.contract_sha256 = self.approval_envelope["approved_contract_sha256"]
        self.manifest_sha256 = self.approval_envelope["approved_manifest_sha256"]

    def _write_bootstrap_terminal(self, code: str, detail: str = "") -> None:
        self.approval_directory.mkdir(parents=True, exist_ok=True)
        if self.bootstrap_terminal_path.exists():
            return
        tip: str | None = None
        if self.ledger is not None:
            try:
                entries = self.ledger.entries()
                tip = entries[-1]["entry_sha256"] if entries else None
            except Exception:
                tip = None
        latch: dict[str, Any] = {
            "schema_version": "c4_auto.bootstrap_terminal.v1",
            "approval_boundary_id": self.approval_boundary_id,
            "approval_identity": self.approval_identity,
            "state": "HUMAN_REVIEW_REQUIRED", "code": code,
            "detail": redact_text(detail), "timestamp": utc_now(), "ledger_tip_sha256": tip,
        }
        latch["latch_sha256"] = bootstrap_terminal_digest(latch)
        write_new(self.bootstrap_terminal_path, canonical_json_bytes(latch))

    def _read_bootstrap_terminal(self) -> dict[str, Any] | None:
        if not self.bootstrap_terminal_path.is_file():
            return None
        latch = load_json_strict(self.bootstrap_terminal_path)
        required = {
            "schema_version", "approval_boundary_id", "approval_identity", "state", "code",
            "detail", "timestamp", "ledger_tip_sha256", "latch_sha256",
        }
        if (
            not isinstance(latch, dict) or set(latch) != required
            or latch.get("schema_version") != "c4_auto.bootstrap_terminal.v1"
            or latch.get("approval_boundary_id") != self.approval_boundary_id
            or latch.get("approval_identity") != self.approval_identity
            or latch.get("state") != "HUMAN_REVIEW_REQUIRED"
            or latch.get("latch_sha256") != bootstrap_terminal_digest(latch)
        ):
            raise GateViolation("APPROVAL_LEDGER_INTEGRITY_FAILURE", "HUMAN_REVIEW_REQUIRED", "bootstrap terminal")
        return latch

    def _persist_bootstrap_failure(self, code: str, detail: str = "") -> None:
        if self.ledger is None:
            self._write_bootstrap_terminal(code, detail)
            return
        try:
            authority = self.ledger.reconstruct(self.approval_identity)
            if authority.terminal_state is None:
                self.ledger.append("BOOTSTRAP_FAILURE", None, {
                    "code": code, "detail": redact_text(detail),
                    "approved_contract_sha256": self.approval_envelope["approved_contract_sha256"],
                    "approved_manifest_sha256": self.approval_envelope["approved_manifest_sha256"],
                })
                self.ledger.append("TERMINAL", None, {"state": "HUMAN_REVIEW_REQUIRED", "code": code})
            elif authority.terminal_state != "HUMAN_REVIEW_REQUIRED":
                self._write_bootstrap_terminal(code, detail)
        except Exception:
            self._write_bootstrap_terminal("APPROVAL_LEDGER_INTEGRITY_FAILURE", detail or code)

    def _load_observed_authority(self) -> tuple[dict[str, Any], str, dict[str, Any], str]:
        contract_path = self.repo_root / "config" / "c4_auto" / "engineering_contract.v1.json"
        try:
            contract = load_json_strict(contract_path)
            if not isinstance(contract, dict):
                raise StrictJsonError("contract must be an object")
            contract_sha = sha256_json(contract)
        except (OSError, StrictJsonError, ValueError) as exc:
            self._persist_bootstrap_failure("CONTRACT_AUTHORITY_INVALID", str(exc))
            raise GateViolation("CONTRACT_AUTHORITY_INVALID", "HUMAN_REVIEW_REQUIRED") from exc
        if contract_sha != self.approval_envelope["approved_contract_sha256"]:
            self._persist_bootstrap_failure("CONTRACT_DRIFT", f"observed={contract_sha}")
            raise GateViolation("CONTRACT_DRIFT", "HUMAN_REVIEW_REQUIRED")
        try:
            manifest = load_json_strict(self.manifest_path)
            if not isinstance(manifest, dict):
                raise StrictJsonError("manifest must be an object")
            manifest_sha = sha256_json(manifest)
        except (OSError, StrictJsonError, ValueError) as exc:
            self._persist_bootstrap_failure("MANIFEST_AUTHORITY_INVALID", str(exc))
            raise GateViolation("MANIFEST_AUTHORITY_INVALID", "HUMAN_REVIEW_REQUIRED") from exc
        if manifest_sha != self.approval_envelope["approved_manifest_sha256"]:
            self._persist_bootstrap_failure("MANIFEST_DRIFT", f"observed={manifest_sha}")
            raise GateViolation("MANIFEST_DRIFT", "HUMAN_REVIEW_REQUIRED")
        return contract, contract_sha, manifest, manifest_sha

    def bootstrap(self, allow_terminal: bool = False) -> Authority:
        self.approval_directory.mkdir(parents=True, exist_ok=True)
        latch = self._read_bootstrap_terminal()
        if latch is not None:
            raise GateViolation(latch["code"], "HUMAN_REVIEW_REQUIRED", latch["detail"])
        try:
            self.schemas = LocalSchemas(self.repo_root / "schemas" / "c4_auto")
            self.ledger = ApprovalLedger(self.approval_directory, self.approval_boundary_id, self.schemas)
            self._create_boundary_if_needed()
            authority = self.ledger.reconstruct(self.approval_identity)
        except GateViolation as exc:
            self._write_bootstrap_terminal("APPROVAL_LEDGER_INTEGRITY_FAILURE", str(exc))
            raise GateViolation("APPROVAL_LEDGER_INTEGRITY_FAILURE", "HUMAN_REVIEW_REQUIRED") from exc
        except (OSError, SchemaValidationError, StrictJsonError, ValueError) as exc:
            self._write_bootstrap_terminal("APPROVAL_LEDGER_INTEGRITY_FAILURE", str(exc))
            raise GateViolation("APPROVAL_LEDGER_INTEGRITY_FAILURE", "HUMAN_REVIEW_REQUIRED") from exc
        if authority.terminal_state is not None and not allow_terminal:
            raise GateViolation("APPROVAL_TERMINAL", authority.terminal_state, authority.terminal_run_id or "")
        contract, contract_sha, manifest, manifest_sha = self._load_observed_authority()
        try:
            self.schemas.validate("contract", contract)
            validate_contract_semantics(contract)
            self.schemas.validate("manifest", manifest)
            validate_manifest_semantics(manifest, contract, contract_sha)
        except (SchemaValidationError, GateViolation, ValueError) as exc:
            self._persist_bootstrap_failure("APPROVAL_AUTHORITY_INVALID", str(exc))
            raise GateViolation("APPROVAL_AUTHORITY_INVALID", "HUMAN_REVIEW_REQUIRED") from exc
        semantic_identity = {
            "stage_id": manifest["stage_id"], "approved_contract_sha256": contract_sha,
            "approved_manifest_sha256": manifest_sha,
            "approved_base_sha": manifest["design_gate"]["approved_base_sha"],
            "approval_id": manifest["design_gate"]["approval_id"],
        }
        if semantic_identity != self.approval_envelope:
            self._persist_bootstrap_failure("APPROVAL_ENVELOPE_MISMATCH")
            raise GateViolation("APPROVAL_ENVELOPE_MISMATCH", "HUMAN_REVIEW_REQUIRED")
        try:
            verify_base(self.repo_root, manifest["base"]["branch"], self.approval_envelope["approved_base_sha"])
        except GateViolation as exc:
            self._persist_bootstrap_failure("BASE_DRIFT", str(exc))
            raise
        self.contract, self.contract_sha256 = contract, contract_sha
        self.manifest, self.manifest_sha256 = manifest, manifest_sha
        return authority

    def _create_boundary_if_needed(self) -> None:
        if (self.approval_directory / "approval.json").exists():
            return
        if self.ledger is None:
            raise RuntimeError("bootstrap ledger unavailable")
        self.approval_directory.mkdir(parents=True, exist_ok=True)
        approval_path = self.approval_directory / "approval.json"
        write_new(approval_path, canonical_json_bytes(self.approval_identity))
        self.ledger.append("APPROVAL_CREATED", None, {}, [artifact_reference(approval_path, self.approval_directory, "approval_identity")])

    def _reload_inputs(self) -> None:
        if self.schemas is None:
            raise GateViolation("BOOTSTRAP_REQUIRED", "HUMAN_REVIEW_REQUIRED")
        contract, contract_sha, manifest, manifest_sha = self._load_observed_authority()
        self.schemas.validate("contract", contract)
        validate_contract_semantics(contract)
        self.schemas.validate("manifest", manifest)
        validate_manifest_semantics(manifest, contract, contract_sha)
        self.contract, self.manifest = contract, manifest

    def reconstruct(self, allow_terminal: bool = False) -> Authority:
        if self.ledger is None:
            raise GateViolation("BOOTSTRAP_REQUIRED", "HUMAN_REVIEW_REQUIRED")
        self._reload_inputs()
        authority = self.ledger.reconstruct(self.approval_identity)
        if authority.terminal_state is not None and not allow_terminal:
            raise GateViolation("APPROVAL_TERMINAL", authority.terminal_state, authority.terminal_run_id or "")
        return authority

    def _guard(self, allow_terminal: bool = False) -> tuple[Authority, RepositorySnapshot]:
        authority = self.reconstruct(allow_terminal=allow_terminal)
        if authority.baseline_reference is not None:
            baseline = load_json_strict(self.approval_directory / authority.baseline_reference["path"])
            if baseline.get("schema_version") != "c4_auto.test_contract_baseline.v2":
                raise GateViolation("TEST_CONTRACT_DRIFT", "HUMAN_REVIEW_REQUIRED", "baseline version")
            current = collect_repository_state(
                self.repo_root, self.manifest["base"]["sha"],
                self.manifest["scope"]["test_contract_paths"], baseline["scope_universe"],
            )
            if any(baseline.get(key) != current[key] for key in current):
                if authority.terminal_state is None:
                    self.ledger.append("TERMINAL", None, {"state": "HUMAN_REVIEW_REQUIRED", "code": "TEST_CONTRACT_DRIFT"})
                raise GateViolation("TEST_CONTRACT_DRIFT", "HUMAN_REVIEW_REQUIRED")
        verify_base(self.repo_root, self.manifest["base"]["branch"], self.manifest["base"]["sha"])
        verify_declared_files(self.repo_root, self.manifest["scope"]["protected_files"], "PROTECTED_FILE_DRIFT")
        verify_declared_files(self.repo_root, self.manifest["scope"]["allowed_preexisting_changes"], "PREEXISTING_CHANGE_DRIFT")
        snapshot = collect_repository_snapshot(self.repo_root, self.manifest["base"]["sha"])
        drift = scope_drift(snapshot, self.manifest["scope"]["allowed_paths"])
        if drift:
            raise GateViolation("SCOPE_DRIFT", "HUMAN_REVIEW_REQUIRED", ",".join(drift))
        collect_repository_state(
            self.repo_root, self.manifest["base"]["sha"], self.manifest["scope"]["allowed_paths"],
        )
        return authority, snapshot

    def _events(self, run_id: str | None = None) -> list[dict[str, Any]]:
        directory = self.approval_directory / "runs" / (run_id or self.run_id) / "events"
        return [load_json_strict(path) for path in sorted(directory.glob("*.json"))] if directory.exists() else []

    def _state(self) -> str | None:
        events = self._events()
        return events[-1]["state"] if events else None

    def _append_event(self, target: str, event_type: str, payload: Mapping[str, Any], basis: str = "DERIVED") -> dict[str, Any]:
        events = self._events()
        current = events[-1]["state"] if events else None
        StateMachine(current).transition(target)
        event: dict[str, Any] = {
            "schema_version": "c4_auto.run_event.v1", "sequence": len(events) + 1, "timestamp": utc_now(),
            "run_id": self.run_id, "from_state": current, "state": target, "event_type": event_type,
            "basis": basis, "payload": dict(payload),
            "previous_event_sha256": events[-1]["event_sha256"] if events else None,
        }
        event["event_sha256"] = event_digest(event)
        self.schemas.validate_definition("evidence", "run_event", event)
        path = self.run_directory / "events" / f"{event['sequence']:08d}.json"
        write_new(path, canonical_json_bytes(event))
        reference = artifact_reference(path, self.approval_directory, "run_event")
        self.ledger.append("RUN_EVENT", self.run_id, {"event_count": event["sequence"], "event_tip_sha256": event["event_sha256"]}, [reference])
        return event

    def _terminalize(self, verdict: str, code: str) -> dict[str, Any]:
        if verdict not in ("FAIL", "HUMAN_REVIEW_REQUIRED"):
            raise ValueError("terminalize only accepts blocking verdicts")
        authority = self.reconstruct(allow_terminal=True)
        if authority.terminal_state is not None:
            return {"verdict": authority.terminal_state, "code": "APPROVAL_TERMINAL", "approval_boundary_id": self.approval_boundary_id}
        if self.run_id not in authority.runs:
            return {"verdict": verdict, "code": code, "approval_boundary_id": self.approval_boundary_id}
        self._append_event(verdict, code, {"code": code})
        entry = self.ledger.append("TERMINAL", self.run_id, {"state": verdict, "code": code})
        return {"verdict": verdict, "code": code, "approval_boundary_id": self.approval_boundary_id, "ledger_tip_sha256": entry["entry_sha256"]}

    def preflight(self) -> dict[str, Any]:
        self._create_boundary_if_needed()
        authority, snapshot = self._guard()
        if self.run_id in authority.runs or self.run_directory.exists():
            raise GateViolation("RUN_ID_REUSE", "HUMAN_REVIEW_REQUIRED")
        self.run_directory.mkdir(parents=True)
        preflight = {
            "run_id": self.run_id, "approval_boundary_id": self.approval_boundary_id,
            "started_at": utc_now(), "branch": snapshot.branch, "head_sha": snapshot.head_sha,
            "contract_sha256": self.contract_sha256, "manifest_sha256": self.manifest_sha256,
        }
        path = self.run_directory / "preflight.json"
        write_new(path, canonical_json_bytes(preflight))
        self.ledger.append("RUN_REGISTERED", self.run_id, {}, [artifact_reference(path, self.approval_directory, "preflight")])
        self._append_event("PRECHECK", "PREFLIGHT_PASSED", {})
        self._append_event("DESIGN_APPROVED", "DESIGN_GATE_BOUND", {"approval_id": self.approval_identity["approval_id"]})
        event = self._append_event("IMPLEMENTING", "IMPLEMENTATION_READY", {})
        return {"state": event["state"], "verdict": None, "approval_boundary_id": self.approval_boundary_id}

    def _freeze_or_verify_baseline(self, authority: Authority) -> None:
        retained: Sequence[str] = ()
        baseline: Mapping[str, Any] | None = None
        if authority.baseline_reference is not None:
            baseline = load_json_strict(self.approval_directory / authority.baseline_reference["path"])
            if baseline.get("schema_version") != "c4_auto.test_contract_baseline.v2":
                raise GateViolation("TEST_CONTRACT_DRIFT", "HUMAN_REVIEW_REQUIRED", "baseline version")
            retained = baseline["scope_universe"]
        current = collect_repository_state(
            self.repo_root, self.manifest["base"]["sha"],
            self.manifest["scope"]["test_contract_paths"], retained,
        )
        if authority.baseline_reference is None:
            baseline = {
                "schema_version": "c4_auto.test_contract_baseline.v2",
                "approval_boundary_id": self.approval_boundary_id, **current,
            }
            path = self.approval_directory / "test_contract_baseline.json"
            write_new(path, canonical_json_bytes(baseline))
            self.ledger.append("TEST_BASELINE_FROZEN", self.run_id, {"baseline_sha256": sha256_json(baseline)}, [artifact_reference(path, self.approval_directory, "test_contract_baseline")])
        else:
            if baseline is None or any(baseline.get(key) != current[key] for key in current):
                raise GateViolation("TEST_CONTRACT_DRIFT", "HUMAN_REVIEW_REQUIRED")

    def _tier_records(self, tier: str, run_id: str | None = None) -> list[tuple[dict[str, Any], Mapping[str, Any]]]:
        specs = {item["id"]: item for item in self.manifest["test_tiers"][tier]["checks"]}
        records: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
        for run_dir in sorted((self.approval_directory / "runs").glob("*")):
            if run_id is not None and run_dir.name != run_id:
                continue
            for path in sorted((run_dir / "results").glob(f"{tier}-*.json")):
                record = load_json_strict(path)
                self.schemas.validate_definition("evidence", "command_result", record)
                spec = specs.get(record.get("check_id"))
                if spec is None:
                    raise GateViolation("RESULT_BINDING_MISMATCH", "HUMAN_REVIEW_REQUIRED", path.name)
                valid, reasons = evaluate_command_record(record, spec, tier)
                if valid != record["passed"] or reasons != record["failure_reasons"]:
                    raise GateViolation("RESULT_TAMPER", "HUMAN_REVIEW_REQUIRED", path.name)
                records.append((record, spec))
        return records

    def _run_tier(self, tier: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for spec in self.manifest["test_tiers"][tier]["checks"]:
            attempt = 1 + sum(1 for record, _ in self._tier_records(tier) if record["check_id"] == spec["id"])
            logs = self.run_directory / "logs"
            record = run_check(spec, tier, self.repo_root, logs, attempt)
            result_path = self.run_directory / "results" / f"{tier}-{spec['id']}-{attempt}.json"
            write_new(result_path, canonical_json_bytes(record))
            references = [artifact_reference(result_path, self.approval_directory, "command_result")]
            for log_name in record["log_references"]:
                references.append(artifact_reference(logs / log_name, self.approval_directory, "redacted_log"))
            self.ledger.append("CHECK_RECORDED", self.run_id, {"tier": tier, "check_id": spec["id"], "passed": record["passed"]}, references)
            results.append(record)
        return results

    def _failure(self, tier: str, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        failure_id = sha256_json({"run_id": self.run_id, "tier": tier, "records": [item["check_id"] for item in records], "time": utc_now()})[:24]
        current = self._state()
        self.ledger.append("BOUNDED_FAILURE", self.run_id, {"failure_id": failure_id, "tier": tier, "state": current})
        return {"state": current, "verdict": "FAIL", "failure_id": failure_id, "approval_boundary_id": self.approval_boundary_id}

    def targeted(self) -> dict[str, Any]:
        authority, _ = self._guard()
        if self.run_id not in authority.runs:
            raise GateViolation("RUN_NOT_REGISTERED", "FAIL")
        if self._state() == "REMEDIATING":
            pass
        elif self._state() != "IMPLEMENTING":
            raise GateViolation("ILLEGAL_MODE", "HUMAN_REVIEW_REQUIRED")
        self._freeze_or_verify_baseline(authority)
        self._append_event("TESTING_TARGETED", "TARGETED_STARTED", {})
        records = self._run_tier("targeted")
        if not records or not all(item["passed"] for item in records):
            return self._failure("targeted", records)
        return {"state": "TESTING_TARGETED", "verdict": None, "approval_boundary_id": self.approval_boundary_id}

    def focused(self) -> dict[str, Any]:
        self._guard()
        if self._state() != "TESTING_TARGETED":
            raise GateViolation("ILLEGAL_MODE", "HUMAN_REVIEW_REQUIRED")
        self._append_event("TESTING_FOCUSED", "FOCUSED_STARTED", {})
        records = self._run_tier("focused")
        if not records or not all(item["passed"] for item in records):
            return self._failure("focused", records)
        self._append_event("ADVERSARIAL_REVIEW", "ADVERSARIAL_REVIEW_READY", {})
        return {"state": "ADVERSARIAL_REVIEW", "verdict": None, "approval_boundary_id": self.approval_boundary_id}

    def record_remediation(
        self, failure_id: str, root_cause_status: str, change_class: str,
        hard_gate_ids: Sequence[str], root_cause_detail: str = "bounded root cause",
    ) -> dict[str, Any]:
        authority, _ = self._guard()
        known_failures = {
            entry["payload"]["failure_id"]
            for entry in authority.entries if entry["entry_type"] == "BOUNDED_FAILURE"
        }
        used_failures = {
            entry["payload"]["failure_id"]
            for entry in authority.entries if entry["entry_type"] == "REMEDIATION_STARTED"
        }
        invalid_gates = sorted(set(hard_gate_ids) - set(EXPECTED_HARD_GATES))
        if root_cause_status not in ROOT_CAUSE_STATUSES or change_class not in CHANGE_CLASSES or invalid_gates:
            return self._terminalize("HUMAN_REVIEW_REQUIRED", "ROOT_CAUSE_CLASSIFICATION_INVALID")
        if failure_id not in known_failures or failure_id in used_failures:
            return self._terminalize("HUMAN_REVIEW_REQUIRED", "FAILURE_NOT_REMEDIATION_ELIGIBLE")
        if not root_cause_detail.strip():
            return self._terminalize("HUMAN_REVIEW_REQUIRED", "UNKNOWN_ROOT_CAUSE")
        if root_cause_status != "CONFIRMED_BOUNDED":
            return self._terminalize("HUMAN_REVIEW_REQUIRED", "UNKNOWN_ROOT_CAUSE")
        if change_class != "BOUNDED_FIX" or hard_gate_ids:
            return self._terminalize("HUMAN_REVIEW_REQUIRED", "HUMAN_DESIGN_GATE")
        if len(authority.remediation_cycles) >= MAX_REMEDIATION_CYCLES:
            return self._terminalize("HUMAN_REVIEW_REQUIRED", "RETRY_LIMIT")
        current = self._state()
        StateMachine(current, len(authority.remediation_cycles)).request_remediation(root_cause_status, change_class, hard_gate_ids)
        cycle = {
            "cycle_number": len(authority.remediation_cycles) + 1, "run_id": self.run_id,
            "failure_id": failure_id, "root_cause_status": root_cause_status,
            "root_cause_detail": root_cause_detail, "change_class": change_class,
            "hard_gate_ids": list(hard_gate_ids), "result": "AUTHORIZED",
        }
        self._append_event("REMEDIATING", "REMEDIATION_STARTED", cycle)
        self.ledger.append("REMEDIATION_STARTED", self.run_id, cycle)
        return {**cycle, "state": "REMEDIATING", "verdict": None, "approval_boundary_id": self.approval_boundary_id}

    def _sealed_mutable_state(self, retained_paths: Sequence[str] = ()) -> dict[str, Any]:
        patterns = self.manifest["scope"]["allowed_paths"]
        snapshot = collect_repository_snapshot(self.repo_root, self.manifest["base"]["sha"])
        if any(not path_is_allowed(path, patterns) for path in snapshot.all_changed):
            raise GateViolation("CANDIDATE_SEAL_COVERAGE", "HUMAN_REVIEW_REQUIRED")
        return collect_repository_state(
            self.repo_root, self.manifest["base"]["sha"], patterns, retained_paths,
        )

    def _command_bindings(self) -> dict[str, str]:
        return {
            item["id"]: command_binding(item, tier)
            for tier in TIERS for item in self.manifest["test_tiers"][tier]["checks"]
        }

    def _create_candidate_seal(self) -> dict[str, Any]:
        authority = self.reconstruct()
        if authority.baseline_reference is None:
            raise GateViolation("TEST_BASELINE_MISSING", "HUMAN_REVIEW_REQUIRED")
        baseline_path = self.approval_directory / authority.baseline_reference["path"]
        state = self._sealed_mutable_state()
        seal: dict[str, Any] = {
            "schema_version": "c4_auto.candidate_seal.v2", "approval_boundary_id": self.approval_boundary_id,
            "contract_sha256": self.contract_sha256, "manifest_sha256": self.manifest_sha256,
            "base_sha": self.manifest["base"]["sha"], "head_sha": _run_git(self.repo_root, ["rev-parse", "HEAD"]).stdout.strip(),
            "test_baseline_sha256": sha256_file(baseline_path), "command_bindings": self._command_bindings(),
            "sealed_path_patterns": list(self.manifest["scope"]["allowed_paths"]),
            **state,
        }
        seal["seal_sha256"] = seal_digest(seal)
        self.schemas.validate_definition("evidence", "candidate_seal", seal)
        path = self.run_directory / "candidate_seals" / f"{len(authority.candidate_references) + 1:04d}.json"
        write_new(path, canonical_json_bytes(seal))
        self.ledger.append("CANDIDATE_SEALED", self.run_id, {"seal_sha256": seal["seal_sha256"]}, [artifact_reference(path, self.approval_directory, "candidate_seal")])
        return seal

    def _latest_seal(self) -> dict[str, Any]:
        authority = self.reconstruct(allow_terminal=True)
        if not authority.candidate_references:
            raise GateViolation("CANDIDATE_SEAL_MISSING", "HUMAN_REVIEW_REQUIRED")
        seal = load_json_strict(self.approval_directory / authority.candidate_references[-1]["path"])
        self.schemas.validate_definition("evidence", "candidate_seal", seal)
        if seal["seal_sha256"] != seal_digest(seal):
            raise GateViolation("CANDIDATE_DRIFT", "HUMAN_REVIEW_REQUIRED")
        return seal

    def _verify_candidate_seal(self) -> dict[str, Any]:
        seal = self._latest_seal()
        authority = self.reconstruct(allow_terminal=True)
        baseline_path = self.approval_directory / authority.baseline_reference["path"] if authority.baseline_reference else None
        current_state = self._sealed_mutable_state(seal["scope_universe"])
        expected = {
            "approval_boundary_id": self.approval_boundary_id, "contract_sha256": self.contract_sha256,
            "manifest_sha256": self.manifest_sha256, "base_sha": self.manifest["base"]["sha"],
            "head_sha": _run_git(self.repo_root, ["rev-parse", "HEAD"]).stdout.strip(),
            "test_baseline_sha256": sha256_file(baseline_path) if baseline_path else "",
            "command_bindings": self._command_bindings(),
            "sealed_path_patterns": list(self.manifest["scope"]["allowed_paths"]),
            **current_state,
        }
        if any(seal.get(key) != value for key, value in expected.items()):
            raise GateViolation("CANDIDATE_DRIFT", "HUMAN_REVIEW_REQUIRED")
        return seal

    def _records_by_tier(self, run_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
        selected_run = run_id or self.run_id
        return {tier: [record for record, _ in self._tier_records(tier, selected_run)] for tier in TIERS}

    def _required_tier_failures(self, records: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[str]:
        failures: list[str] = []
        for tier in TIERS:
            if not self.manifest["test_tiers"][tier]["required"]:
                continue
            latest: dict[str, Mapping[str, Any]] = {}
            for record in records[tier]:
                latest[record["check_id"]] = record
            for spec in self.manifest["test_tiers"][tier]["checks"]:
                record = latest.get(spec["id"])
                if record is None:
                    failures.append(f"{tier}:{spec['id']}:REQUIRED_EVIDENCE_MISSING")
                elif not record["passed"]:
                    failures.append(f"{tier}:{spec['id']}:FAILED")
        return failures

    def derive_verdict(self, run_id: str | None = None) -> tuple[str, list[str], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        authority = self.reconstruct(allow_terminal=True)
        if authority.terminal_state in ("FAIL", "HUMAN_REVIEW_REQUIRED"):
            records = self._records_by_tier(run_id or authority.terminal_run_id or self.run_id)
            if run_id is None:
                return authority.terminal_state, ["APPROVAL_TERMINAL"], [], records
        records = self._records_by_tier(run_id)
        failures = self._required_tier_failures(records)
        flat = [item for tier in TIERS for item in records[tier]]
        acceptance = evaluate_acceptance(self.manifest["acceptance_criteria"], flat)
        failures.extend(f"acceptance:{item['id']}" for item in acceptance if not item["passed"])
        latest: dict[str, Mapping[str, Any]] = {}
        for item in flat:
            latest[item["check_id"]] = item
        for counterexample in self.manifest["counterexamples"]:
            record = latest.get(counterexample["check_id"])
            expected = counterexample["expected_result"]
            observed_reasons = sorted(set(record.get("failure_reasons", ()))) if record else []
            if (
                record is None
                or record.get("passed") is not expected["passed"]
                or observed_reasons != sorted(expected["failure_reasons"])
            ):
                failures.append(f"counterexample:{counterexample['id']}")
        try:
            self._verify_candidate_seal()
        except GateViolation as exc:
            failures.append(exc.code)
        return ("PASS_CANDIDATE" if not failures else "FAIL"), failures, acceptance, records

    def _event_references(self) -> list[dict[str, Any]]:
        return [{
            "sequence": event["sequence"], "state": event["state"], "event_type": event["event_type"],
            "event_sha256": event["event_sha256"], "previous_event_sha256": event["previous_event_sha256"],
            "path": f"events/{event['sequence']:08d}.json",
        } for event in self._events()]

    def _build_evidence(
        self, verdict: str, failures: Sequence[str], acceptance: Sequence[Mapping[str, Any]],
        records: Mapping[str, Sequence[Mapping[str, Any]]], ledger_tip: str,
    ) -> dict[str, Any]:
        authority, snapshot = self._guard(allow_terminal=True)
        seal_hash: str | None = None
        if authority.candidate_references:
            seal_hash = load_json_strict(self.approval_directory / authority.candidate_references[-1]["path"])["seal_sha256"]
        evidence: dict[str, Any] = {
            "schema_version": "c4_auto.evidence_report.v1", "stage_id": self.manifest["stage_id"],
            "run_id": self.run_id, "approval_boundary_id": self.approval_boundary_id,
            "approval_identity": self.approval_identity, "contract_id": self.contract["contract_id"],
            "contract_version": self.contract["contract_version"], "contract_sha256": self.contract_sha256,
            "manifest_sha256": self.manifest_sha256, "base_branch": self.manifest["base"]["branch"],
            "base_sha": self.manifest["base"]["sha"], "start_time": load_json_strict(self.run_directory / "preflight.json")["started_at"],
            "end_time": utc_now(), "current_branch": snapshot.branch, "head_sha": snapshot.head_sha,
            "changed_files": snapshot.changed_files(), "scope_drift": list(scope_drift(snapshot, self.manifest["scope"]["allowed_paths"])),
            "remediation_cycles": list(authority.remediation_cycles), "counterexamples": self.manifest["counterexamples"],
            "targeted_tests": list(records["targeted"]), "focused_tests": list(records["focused"]),
            "coupled_tests": list(records["coupled"]), "full_regression": list(records["full"]),
            "replay_e2e": list(records["replay_e2e"]), "failed": list(failures), "skipped": [],
            "hard_gate_triggered": [entry["payload"].get("code", "") for entry in authority.entries if entry["entry_type"] == "TERMINAL" and entry["payload"].get("state") == "HUMAN_REVIEW_REQUIRED"],
            "remaining_risks": ["Local hash chains detect accidental rollback but do not resist a malicious same-user rewrite."],
            "rollback_boundary": self.manifest["rollback_boundary"], "event_chain": self._event_references(),
            "acceptance_results": list(acceptance), "candidate_seal_sha256": seal_hash,
            "ledger_tip_sha256": ledger_tip, "verdict": verdict,
        }
        evidence["evidence_sha256"] = evidence_digest(evidence)
        self.schemas.validate("evidence", evidence)
        for field in self.manifest["evidence_requirements"]["required_fields"]:
            if field not in evidence or evidence[field] is None:
                raise GateViolation("REQUIRED_EVIDENCE_FIELD_MISSING", "FAIL", field)
        return evidence

    def _finalize_derived(self) -> dict[str, Any]:
        verdict, failures, acceptance, records = self.derive_verdict()
        self._append_event("EVIDENCE_FINALIZATION", "EVIDENCE_FINALIZATION_STARTED", {})
        authority = self.reconstruct(allow_terminal=True)
        if authority.ledger_tip_sha256 is None:
            raise GateViolation("EVIDENCE_ARTIFACT_UNBOUND", "HUMAN_REVIEW_REQUIRED", "missing ledger tip")
        evidence = self._build_evidence(verdict, failures, acceptance, records, authority.ledger_tip_sha256)
        evidence_path = self.run_directory / "evidence.json"
        write_new(evidence_path, canonical_json_bytes(evidence))
        self.ledger.append("EVIDENCE_FINALIZED", self.run_id, {"verdict": verdict, "evidence_sha256": evidence["evidence_sha256"]}, [artifact_reference(evidence_path, self.approval_directory, "machine_evidence")])
        self.reconstruct(allow_terminal=True)
        reconstructed_verdict, reconstructed_failures, reconstructed_acceptance, _ = self.derive_verdict(self.run_id)
        if (
            reconstructed_verdict != evidence["verdict"]
            or reconstructed_failures != evidence["failed"]
            or reconstructed_acceptance != evidence["acceptance_results"]
        ):
            raise GateViolation("EVIDENCE_ARTIFACT_UNBOUND", "HUMAN_REVIEW_REQUIRED", "reconstructed verdict mismatch")
        self._append_event(verdict, "VERDICT_DERIVED", {"verdict": verdict, "failures": failures})
        self.ledger.append("TERMINAL", self.run_id, {"state": verdict, "code": "VERDICT_DERIVED"})
        finalized = self.reconstruct(allow_terminal=True)
        if finalized.terminal_state != verdict or finalized.terminal_run_id != self.run_id:
            raise GateViolation("EVIDENCE_ARTIFACT_UNBOUND", "HUMAN_REVIEW_REQUIRED", "terminal reconstruction")
        markdown = render_markdown(evidence)
        (self.run_directory / "evidence.md").write_text(markdown, encoding="utf-8", newline="\n")
        return evidence

    def final(self) -> dict[str, Any]:
        self._guard()
        if self._state() != "ADVERSARIAL_REVIEW":
            raise GateViolation("ILLEGAL_MODE", "HUMAN_REVIEW_REQUIRED")
        self._append_event("TESTING_COUPLED", "COUPLED_STARTED", {})
        coupled = self._run_tier("coupled")
        if not coupled or not all(item["passed"] for item in coupled):
            return self._failure("coupled", coupled)
        self._create_candidate_seal()
        try:
            self._verify_candidate_seal()
        except GateViolation as exc:
            return self._terminalize("HUMAN_REVIEW_REQUIRED", exc.code)
        self._append_event("TESTING_FULL", "FULL_STARTED", {})
        full = self._run_tier("full")
        if not full or not all(item["passed"] for item in full):
            return self._failure("full", full)
        try:
            self._verify_candidate_seal()
        except GateViolation as exc:
            return self._terminalize("HUMAN_REVIEW_REQUIRED", exc.code)
        if self.manifest["test_tiers"]["replay_e2e"]["required"]:
            self._append_event("REPLAY_E2E", "REPLAY_STARTED", {})
            replay = self._run_tier("replay_e2e")
            if not replay or not all(item["passed"] for item in replay):
                return self._failure("replay_e2e", replay)
            try:
                self._verify_candidate_seal()
            except GateViolation as exc:
                return self._terminalize("HUMAN_REVIEW_REQUIRED", exc.code)
        try:
            self._verify_candidate_seal()
        except GateViolation as exc:
            return self._terminalize("HUMAN_REVIEW_REQUIRED", exc.code)
        return self._finalize_derived()

    def evidence_only(self) -> dict[str, Any]:
        authority = self.reconstruct(allow_terminal=True)
        if authority.terminal_state is None:
            raise GateViolation("MACHINE_EVIDENCE_MISSING", "FAIL")
        if not authority.evidence_references:
            if authority.terminal_state == "PASS_CANDIDATE":
                raise GateViolation("MACHINE_EVIDENCE_MISSING", "HUMAN_REVIEW_REQUIRED")
            return {"verdict": authority.terminal_state, "code": "MACHINE_EVIDENCE_MISSING", "approval_boundary_id": self.approval_boundary_id}
        reference = authority.evidence_references[-1]
        evidence = load_json_strict(self.approval_directory / reference["path"])
        verify_evidence_integrity(evidence, self.schemas)
        if evidence["approval_boundary_id"] != self.approval_boundary_id or evidence["run_id"] != authority.terminal_run_id:
            raise GateViolation("EVIDENCE_TAMPER", "HUMAN_REVIEW_REQUIRED")
        if evidence["verdict"] != authority.terminal_state:
            raise GateViolation("EVIDENCE_TAMPER", "HUMAN_REVIEW_REQUIRED")
        verdict, failures, acceptance, records = self.derive_verdict(authority.terminal_run_id)
        if verdict != evidence["verdict"] or failures != evidence["failed"] or acceptance != evidence["acceptance_results"]:
            raise GateViolation("EVIDENCE_TAMPER", "HUMAN_REVIEW_REQUIRED", "recomputed verdict mismatch")
        markdown = render_markdown(evidence)
        markdown_path = self.approval_directory / "runs" / authority.terminal_run_id / "evidence.md"
        markdown_path.write_text(markdown, encoding="utf-8", newline="\n")
        return evidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="C4-AUTO R1 fail-closed local gate runner")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--stage-id", required=True)
    parser.add_argument("--approved-contract-sha256", required=True)
    parser.add_argument("--approved-manifest-sha256", required=True)
    parser.add_argument("--approved-base-sha", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for mode in ("preflight", "targeted", "focused", "final", "evidence-only"):
        subparsers.add_parser(mode)
    remediation = subparsers.add_parser("remediate")
    remediation.add_argument("--failure-id", required=True)
    remediation.add_argument("--root-cause-status", required=True, choices=ROOT_CAUSE_STATUSES)
    remediation.add_argument("--change-class", required=True, choices=CHANGE_CLASSES)
    remediation.add_argument("--hard-gate-id", action="append", default=[], choices=EXPECTED_HARD_GATES)
    remediation.add_argument("--root-cause-detail", default="bounded root cause")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runner: GateRunner | None = None
    try:
        envelope = {
            "stage_id": args.stage_id,
            "approved_contract_sha256": args.approved_contract_sha256,
            "approved_manifest_sha256": args.approved_manifest_sha256,
            "approved_base_sha": args.approved_base_sha,
            "approval_id": args.approval_id,
        }
        runner = GateRunner(args.manifest, envelope, args.run_id, args.repo_root)
        runner.bootstrap(allow_terminal=args.mode == "evidence-only")
        if args.mode == "remediate":
            result = runner.record_remediation(args.failure_id, args.root_cause_status, args.change_class, args.hard_gate_id, args.root_cause_detail)
        else:
            result = getattr(runner, args.mode.replace("-", "_"))()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        verdict = result.get("verdict")
        return 0 if verdict in (None, "PASS_CANDIDATE") else (3 if verdict == "HUMAN_REVIEW_REQUIRED" else 2)
    except (GateViolation, SchemaValidationError, StrictJsonError, OSError, ValueError) as exc:
        verdict = exc.verdict if isinstance(exc, GateViolation) else "HUMAN_REVIEW_REQUIRED"
        code = exc.code if isinstance(exc, GateViolation) else "VALIDATION_ERROR"
        if runner is not None and verdict == "HUMAN_REVIEW_REQUIRED" and code not in {
            "INCOMPLETE_LEDGER_COMMIT", "EVIDENCE_TAMPER", "APPROVAL_TERMINAL", "TEST_CONTRACT_DRIFT",
            "MANIFEST_DRIFT", "CONTRACT_DRIFT", "CONTRACT_AUTHORITY_INVALID", "MANIFEST_AUTHORITY_INVALID",
            "APPROVAL_LEDGER_INTEGRITY_FAILURE", "APPROVAL_AUTHORITY_INVALID", "APPROVAL_ENVELOPE_MISMATCH",
        }:
            try:
                persisted = runner._terminalize(verdict, code)
                verdict = persisted["verdict"]
            except Exception:
                pass
        print(json.dumps({"verdict": verdict, "code": code, "detail": redact_text(str(exc))}, ensure_ascii=False, sort_keys=True))
        return 3 if verdict == "HUMAN_REVIEW_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())



