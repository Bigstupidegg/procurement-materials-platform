"""Read-only GitHub verification for C4 preregistration approval artifacts.

This adapter verifies evidence that already exists.  It cannot create an
artifact, mutate GitHub, or grant any data, backtest, or production authority.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import InitVar, dataclass
import json
import os
import re
from typing import Any
from urllib import error, parse, request

from scripts.c4_rd_contract import (
    CANONICAL_JSON_PROFILE,
    HASH_PROFILE,
    ContractError,
    canonical_hash,
    canonical_json_bytes,
    canonical_timestamp,
    parse_json_strict,
    parse_rfc3339,
)
from scripts.c4_rd_preregistration import PreregistrationProtocol


APPROVAL_ARTIFACT_CONTRACT_VERSION = "1.0.0"
APPROVAL_ARTIFACT_TYPE = "C4_PREREGISTRATION_APPROVAL_ARTIFACT"
APPROVAL_INTENT = "HUMAN_RESEARCH_PROTOCOL_APPROVAL"
APPROVAL_EVIDENCE_CONTRACT_VERSION = "1.0.0"
APPROVAL_REPOSITORY = "Bigstupidegg/procurement-materials-platform"
APPROVAL_BASE_BRANCH = "v2.3-c3-2-daily-automation"
APPROVAL_ARTIFACT_DIRECTORY = "research/preregistrations"

_GITHUB_API_ORIGIN = "https://api.github.com"
_GITHUB_WEB_ORIGIN = "https://github.com"
_USER_AGENT = "procurement-materials-platform-rd75c1"
_HTTP_TIMEOUT_SECONDS = 20
_MAX_APPROVAL_ARTIFACT_BYTES = 1024 * 1024
_VERIFIED_EVIDENCE_CONSTRUCTION_TOKEN = object()
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_ARTIFACT_KEYS = frozenset({
    "artifact_contract_version",
    "artifact_type",
    "approval_intent",
    "research_protocol_id",
    "preregistration_version",
    "preregistration_identity",
    "protocol_contract_version",
    "hash_domain",
    "canonical_json_profile",
    "hash_profile",
    "protocol_semantic_projection",
})


class PreregistrationApprovalEvidenceError(ContractError):
    """Base error for approval-evidence construction and verification."""


class ApprovalEvidenceTransportError(PreregistrationApprovalEvidenceError):
    """GitHub evidence could not be read due to a transport failure."""


class InvalidGitHubApprovalEvidence(PreregistrationApprovalEvidenceError):
    """GitHub metadata is not an eligible Human approval evidence chain."""


class ApprovalArtifactMismatch(PreregistrationApprovalEvidenceError):
    """The merge-commit artifact is not the exact trusted protocol artifact."""


def _require_protocol(protocol: Any) -> PreregistrationProtocol:
    if type(protocol) is not PreregistrationProtocol:
        raise PreregistrationApprovalEvidenceError(
            "protocol must be an exact trusted PreregistrationProtocol"
        )
    return protocol


def _artifact_payload(protocol: PreregistrationProtocol) -> dict[str, Any]:
    return {
        "artifact_contract_version": APPROVAL_ARTIFACT_CONTRACT_VERSION,
        "artifact_type": APPROVAL_ARTIFACT_TYPE,
        "approval_intent": APPROVAL_INTENT,
        "research_protocol_id": protocol.research_protocol_id,
        "preregistration_version": protocol.preregistration_version,
        "preregistration_identity": protocol.preregistration_identity,
        "protocol_contract_version": protocol.contract_version,
        "hash_domain": "PREREGISTRATION_CONTENT",
        "canonical_json_profile": CANONICAL_JSON_PROFILE,
        "hash_profile": HASH_PROFILE,
        "protocol_semantic_projection": protocol.semantic_projection(),
    }


def preregistration_approval_artifact_path(*, protocol: PreregistrationProtocol) -> str:
    """Return the sole approval-artifact path for an exact trusted protocol."""

    trusted = _require_protocol(protocol)
    return f"{APPROVAL_ARTIFACT_DIRECTORY}/{trusted.preregistration_identity}.json"


def build_preregistration_approval_artifact_bytes(
    *, protocol: PreregistrationProtocol
) -> bytes:
    """Build deterministic canonical bytes without materializing a file."""

    trusted = _require_protocol(protocol)
    computed = canonical_hash(
        "PREREGISTRATION_CONTENT",
        trusted.semantic_projection(),
        contract_version=trusted.contract_version,
        canonical_profile=CANONICAL_JSON_PROFILE,
        hash_profile=HASH_PROFILE,
    )
    if computed != trusted.preregistration_identity:
        raise ApprovalArtifactMismatch("trusted protocol identity does not match its projection")
    return canonical_json_bytes(_artifact_payload(trusted))


@dataclass(frozen=True, slots=True)
class VerifiedGitHubApprovalEvidence:
    evidence_contract_version: str
    repository_full_name: str
    pr_number: int
    pr_url: str
    base_branch: str
    head_sha: str
    merge_commit_sha: str
    merge_tree_sha: str
    merged_at: str
    merged_by_login: str
    artifact_path: str
    artifact_preregistration_identity: str
    _construction_token: InitVar[object] = None

    def __post_init__(self, _construction_token: object) -> None:
        if _construction_token is not _VERIFIED_EVIDENCE_CONSTRUCTION_TOKEN:
            raise InvalidGitHubApprovalEvidence(
                "VerifiedGitHubApprovalEvidence may only be created by GitHub verification"
            )


def _github_get_json(endpoint: str, query: Mapping[str, str] | None = None) -> Any:
    """Read one fixed-origin GitHub REST resource and decode its JSON response."""

    if type(endpoint) is not str or not endpoint.startswith(
        f"/repos/{APPROVAL_REPOSITORY}/"
    ):
        raise InvalidGitHubApprovalEvidence("GitHub endpoint escaped the fixed repository")
    url = f"{_GITHUB_API_ORIGIN}{endpoint}"
    if query is not None:
        url = f"{url}?{parse.urlencode(dict(query))}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    github_request = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(github_request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
        raise ApprovalEvidenceTransportError("GitHub evidence transport failed") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidGitHubApprovalEvidence("GitHub returned invalid JSON") from exc


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidGitHubApprovalEvidence(f"{name} must be a JSON object")
    return value


def _nested_mapping(value: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return _mapping(value.get(name), name)


def _git_sha(value: Any, name: str) -> str:
    if type(value) is not str or _GIT_SHA.fullmatch(value) is None:
        raise InvalidGitHubApprovalEvidence(f"{name} must be lowercase 40-character hex")
    return value


def _canonical_utc_timestamp(value: Any, name: str) -> str:
    if type(value) is not str or not value.endswith("Z"):
        raise InvalidGitHubApprovalEvidence(f"{name} must be canonical RFC3339 UTC Z")
    try:
        parse_rfc3339(value)
        if canonical_timestamp(value) != value:
            raise InvalidGitHubApprovalEvidence(f"{name} must be canonical RFC3339 UTC Z")
    except ContractError as exc:
        if isinstance(exc, InvalidGitHubApprovalEvidence):
            raise
        raise InvalidGitHubApprovalEvidence(
            f"{name} must be canonical RFC3339 UTC Z"
        ) from exc
    return value


def _decode_artifact(contents: Mapping[str, Any], expected_path: str) -> bytes:
    if contents.get("type") != "file":
        raise ApprovalArtifactMismatch("approval artifact is not a regular file")
    if contents.get("path") != expected_path:
        raise ApprovalArtifactMismatch("approval artifact returned path mismatch")
    if contents.get("encoding") != "base64":
        raise ApprovalArtifactMismatch("approval artifact encoding must be base64")
    declared_size = contents.get("size")
    if type(declared_size) is not int or not 0 <= declared_size <= _MAX_APPROVAL_ARTIFACT_BYTES:
        raise ApprovalArtifactMismatch("approval artifact exceeds the size boundary")
    content = contents.get("content")
    if type(content) is not str:
        raise ApprovalArtifactMismatch("approval artifact base64 content is missing")
    encoded = "".join(content.split())
    maximum_encoded = ((_MAX_APPROVAL_ARTIFACT_BYTES + 2) // 3) * 4
    if len(encoded) > maximum_encoded:
        raise ApprovalArtifactMismatch("approval artifact exceeds the size boundary")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise ApprovalArtifactMismatch("approval artifact has invalid base64") from exc
    if len(raw) > _MAX_APPROVAL_ARTIFACT_BYTES or len(raw) != declared_size:
        raise ApprovalArtifactMismatch("approval artifact size mismatch")
    return raw


def _validate_artifact_bytes(raw: bytes, protocol: PreregistrationProtocol) -> None:
    expected = build_preregistration_approval_artifact_bytes(protocol=protocol)
    if raw != expected:
        raise ApprovalArtifactMismatch("approval artifact bytes are not exact canonical bytes")
    try:
        parsed = parse_json_strict(raw)
    except ContractError as exc:
        raise ApprovalArtifactMismatch("approval artifact is not strict JSON") from exc
    if not isinstance(parsed, Mapping) or set(parsed) != _ARTIFACT_KEYS or len(parsed) != 11:
        raise ApprovalArtifactMismatch("approval artifact must contain exactly 11 keys")
    exact = {
        "artifact_contract_version": APPROVAL_ARTIFACT_CONTRACT_VERSION,
        "artifact_type": APPROVAL_ARTIFACT_TYPE,
        "approval_intent": APPROVAL_INTENT,
        "research_protocol_id": protocol.research_protocol_id,
        "preregistration_version": protocol.preregistration_version,
        "preregistration_identity": protocol.preregistration_identity,
        "protocol_contract_version": protocol.contract_version,
        "hash_domain": "PREREGISTRATION_CONTENT",
        "canonical_json_profile": CANONICAL_JSON_PROFILE,
        "hash_profile": HASH_PROFILE,
    }
    if any(parsed.get(name) != value for name, value in exact.items()):
        raise ApprovalArtifactMismatch("approval artifact metadata mismatch")
    if canonical_json_bytes(parsed) != raw:
        raise ApprovalArtifactMismatch("approval artifact is not canonical JSON")
    try:
        recomputed = canonical_hash(
            "PREREGISTRATION_CONTENT",
            parsed["protocol_semantic_projection"],
            contract_version=parsed["protocol_contract_version"],
            canonical_profile=parsed["canonical_json_profile"],
            hash_profile=parsed["hash_profile"],
        )
    except ContractError as exc:
        raise ApprovalArtifactMismatch("approval artifact hash inputs are invalid") from exc
    if recomputed != parsed["preregistration_identity"] or recomputed != protocol.preregistration_identity:
        raise ApprovalArtifactMismatch("approval artifact identity re-hash mismatch")


def verify_github_pr_approval(
    *, protocol: PreregistrationProtocol, pr_number: int
) -> VerifiedGitHubApprovalEvidence:
    """Verify one dedicated, Human-merged artifact PR using read-only GitHub GETs."""

    trusted = _require_protocol(protocol)
    if type(pr_number) is not int or pr_number <= 0:
        raise InvalidGitHubApprovalEvidence("pr_number must be a positive strict integer")
    repo_path = f"/repos/{APPROVAL_REPOSITORY}"
    expected_url = f"{_GITHUB_WEB_ORIGIN}/{APPROVAL_REPOSITORY}/pull/{pr_number}"
    expected_path = preregistration_approval_artifact_path(protocol=trusted)

    pull = _mapping(_github_get_json(f"{repo_path}/pulls/{pr_number}"), "pull request")
    base = _nested_mapping(pull, "base")
    head = _nested_mapping(pull, "head")
    base_repo = _nested_mapping(base, "repo")
    head_repo = _nested_mapping(head, "repo")
    if pull.get("state") != "closed" or pull.get("merged") is not True:
        raise InvalidGitHubApprovalEvidence("approval PR must be closed and merged")
    if base.get("ref") != APPROVAL_BASE_BRANCH:
        raise InvalidGitHubApprovalEvidence("approval PR base branch mismatch")
    if base_repo.get("full_name") != APPROVAL_REPOSITORY:
        raise InvalidGitHubApprovalEvidence("approval PR base repository mismatch")
    if head_repo.get("full_name") != APPROVAL_REPOSITORY:
        raise InvalidGitHubApprovalEvidence("approval PR head must be in the fixed repository")
    if pull.get("html_url") != expected_url:
        raise InvalidGitHubApprovalEvidence("approval PR canonical URL mismatch")
    merge_sha = _git_sha(pull.get("merge_commit_sha"), "merge_commit_sha")
    head_sha = _git_sha(head.get("sha"), "head.sha")
    merged_at = _canonical_utc_timestamp(pull.get("merged_at"), "merged_at")
    merged_by = _nested_mapping(pull, "merged_by")
    login = merged_by.get("login")
    if merged_by.get("type") != "User" or type(login) is not str or not login or login.isspace():
        raise InvalidGitHubApprovalEvidence("approval PR must be merged by a Human User")
    if login.casefold().endswith("[bot]"):
        raise InvalidGitHubApprovalEvidence("bot merger cannot grant Human approval")
    if type(pull.get("changed_files")) is not int or pull.get("changed_files") != 1:
        raise InvalidGitHubApprovalEvidence("approval PR must change exactly one file")

    files = _github_get_json(f"{repo_path}/pulls/{pr_number}/files", {"per_page": "100"})
    if type(files) is not list or len(files) != 1:
        raise InvalidGitHubApprovalEvidence("approval PR file listing must contain exactly one file")
    changed = _mapping(files[0], "changed file")
    if changed.get("filename") != expected_path or changed.get("status") != "added":
        raise InvalidGitHubApprovalEvidence("approval PR must add only the exact artifact path")

    commit = _mapping(
        _github_get_json(f"{repo_path}/git/commits/{merge_sha}"), "merge commit"
    )
    if commit.get("sha") != merge_sha:
        raise InvalidGitHubApprovalEvidence("merge commit SHA read-back mismatch")
    tree = _nested_mapping(commit, "tree")
    tree_sha = _git_sha(tree.get("sha"), "merge tree SHA")
    parents = commit.get("parents")
    if type(parents) is not list or len(parents) != 2:
        raise InvalidGitHubApprovalEvidence("approval PR must use a true two-parent merge commit")
    second_parent = _mapping(parents[1], "second merge parent")
    if second_parent.get("sha") != head_sha:
        raise InvalidGitHubApprovalEvidence("second merge parent must equal the approval PR head")

    encoded_path = parse.quote(expected_path, safe="/")
    contents = _mapping(
        _github_get_json(f"{repo_path}/contents/{encoded_path}", {"ref": merge_sha}),
        "approval artifact contents",
    )
    raw = _decode_artifact(contents, expected_path)
    _validate_artifact_bytes(raw, trusted)

    return VerifiedGitHubApprovalEvidence(
        evidence_contract_version=APPROVAL_EVIDENCE_CONTRACT_VERSION,
        repository_full_name=APPROVAL_REPOSITORY,
        pr_number=pr_number,
        pr_url=expected_url,
        base_branch=APPROVAL_BASE_BRANCH,
        head_sha=head_sha,
        merge_commit_sha=merge_sha,
        merge_tree_sha=tree_sha,
        merged_at=merged_at,
        merged_by_login=login,
        artifact_path=expected_path,
        artifact_preregistration_identity=trusted.preregistration_identity,
        _construction_token=_VERIFIED_EVIDENCE_CONSTRUCTION_TOKEN,
    )


__all__ = (
    "APPROVAL_ARTIFACT_CONTRACT_VERSION",
    "APPROVAL_ARTIFACT_TYPE",
    "APPROVAL_INTENT",
    "APPROVAL_EVIDENCE_CONTRACT_VERSION",
    "APPROVAL_REPOSITORY",
    "APPROVAL_BASE_BRANCH",
    "APPROVAL_ARTIFACT_DIRECTORY",
    "PreregistrationApprovalEvidenceError",
    "ApprovalEvidenceTransportError",
    "InvalidGitHubApprovalEvidence",
    "ApprovalArtifactMismatch",
    "VerifiedGitHubApprovalEvidence",
    "preregistration_approval_artifact_path",
    "build_preregistration_approval_artifact_bytes",
    "verify_github_pr_approval",
)
