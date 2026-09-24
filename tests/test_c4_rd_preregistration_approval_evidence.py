from __future__ import annotations

import ast
import base64
import copy
from dataclasses import FrozenInstanceError, fields
import inspect
import json
import unittest
from unittest.mock import patch

from scripts import c4_rd_preregistration_approval_evidence as evidence
from scripts import c4_rd_preregistration_governance as governance
from scripts.c4_rd_contract import (
    BACKTEST_ENABLED_SOURCES,
    CANONICAL_JSON_PROFILE,
    HASH_DOMAINS,
    HASH_PROFILE,
    PIT_ENABLED_SOURCES,
    RD3_OPEN_BLOCKERS,
    RESEARCH_ENABLED_SOURCES,
    SAFETY_FLAGS,
    canonical_json_bytes,
    parse_json_strict,
)
from scripts.c4_rd_preregistration_approval_evidence import (
    APPROVAL_ARTIFACT_CONTRACT_VERSION,
    APPROVAL_ARTIFACT_DIRECTORY,
    APPROVAL_ARTIFACT_TYPE,
    APPROVAL_BASE_BRANCH,
    APPROVAL_EVIDENCE_CONTRACT_VERSION,
    APPROVAL_INTENT,
    APPROVAL_REPOSITORY,
    ApprovalArtifactMismatch,
    ApprovalEvidenceTransportError,
    InvalidGitHubApprovalEvidence,
    PreregistrationApprovalEvidenceError,
    VerifiedGitHubApprovalEvidence,
    build_preregistration_approval_artifact_bytes,
    preregistration_approval_artifact_path,
    verify_github_pr_approval,
)
from scripts.c4_rd_preregistration_governance import (
    APPROVED,
    GITHUB_PR_MERGE,
    OPERATIONAL_VERIFIED,
    InvalidApprovalRecord,
    build_operational_preregistration_approval_record,
    require_formal_backtest_authorization,
)
from tests.test_c4_rd_preregistration import QUESTION, build_protocol, synthetic_results


PR_NUMBER = 73
HEAD_SHA = "a" * 40
MERGE_SHA = "b" * 40
TREE_SHA = "c" * 40
BASE_PARENT_SHA = "d" * 40
MERGED_AT = "2026-09-24T05:00:00Z"
RECORDED_AT = "2026-09-24T06:00:00Z"
BACKTEST_AT = "2026-09-24T07:00:00Z"


class TestC4RDPreregistrationApprovalEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.results = synthetic_results()
        cls.protocol = build_protocol(
            cls.results,
            version="1.0.0",
            question=QUESTION,
            protocol_id="C4-RD-7-SYNTHETIC-DIRECTION",
        ).protocol

    def endpoints(self):
        repo = f"/repos/{APPROVAL_REPOSITORY}"
        artifact_path = preregistration_approval_artifact_path(protocol=self.protocol)
        return {
            "pull": f"{repo}/pulls/{PR_NUMBER}",
            "files": f"{repo}/pulls/{PR_NUMBER}/files",
            "commit": f"{repo}/git/commits/{MERGE_SHA}",
            "contents": f"{repo}/contents/{artifact_path}",
        }

    def valid_responses(self):
        paths = self.endpoints()
        artifact_path = preregistration_approval_artifact_path(protocol=self.protocol)
        raw = build_preregistration_approval_artifact_bytes(protocol=self.protocol)
        return {
            paths["pull"]: {
                "state": "closed",
                "merged": True,
                "base": {
                    "ref": APPROVAL_BASE_BRANCH,
                    "repo": {"full_name": APPROVAL_REPOSITORY},
                },
                "head": {
                    "sha": HEAD_SHA,
                    "repo": {"full_name": APPROVAL_REPOSITORY},
                },
                "html_url": (
                    f"https://github.com/{APPROVAL_REPOSITORY}/pull/{PR_NUMBER}"
                ),
                "merge_commit_sha": MERGE_SHA,
                "merged_at": MERGED_AT,
                "merged_by": {"type": "User", "login": "human-reviewer"},
                "changed_files": 1,
            },
            paths["files"]: [{"filename": artifact_path, "status": "added"}],
            paths["commit"]: {
                "sha": MERGE_SHA,
                "tree": {"sha": TREE_SHA},
                "parents": [{"sha": BASE_PARENT_SHA}, {"sha": HEAD_SHA}],
            },
            paths["contents"]: {
                "type": "file",
                "path": artifact_path,
                "encoding": "base64",
                "size": len(raw),
                "content": base64.b64encode(raw).decode("ascii"),
            },
        }

    @staticmethod
    def adapter(responses, calls=None):
        def get(endpoint, query=None):
            if calls is not None:
                calls.append((endpoint, query))
            if endpoint not in responses:
                raise ApprovalEvidenceTransportError("mock endpoint unavailable")
            return copy.deepcopy(responses[endpoint])

        return get

    def verify(self, responses=None, calls=None):
        responses = self.valid_responses() if responses is None else responses
        with patch.object(evidence, "_github_get_json", side_effect=self.adapter(responses, calls)):
            return verify_github_pr_approval(protocol=self.protocol, pr_number=PR_NUMBER)

    def set_raw(self, responses, raw):
        contents = responses[self.endpoints()["contents"]]
        contents["size"] = len(raw)
        contents["content"] = base64.b64encode(raw).decode("ascii")

    def assert_invalid(self, responses, error_type=InvalidGitHubApprovalEvidence):
        with self.assertRaises(error_type):
            self.verify(responses)

    def test_constants_and_error_hierarchy_are_exact(self):
        self.assertEqual(APPROVAL_ARTIFACT_CONTRACT_VERSION, "1.0.0")
        self.assertEqual(APPROVAL_ARTIFACT_TYPE, "C4_PREREGISTRATION_APPROVAL_ARTIFACT")
        self.assertEqual(APPROVAL_INTENT, "HUMAN_RESEARCH_PROTOCOL_APPROVAL")
        self.assertEqual(APPROVAL_EVIDENCE_CONTRACT_VERSION, "1.0.0")
        self.assertEqual(APPROVAL_REPOSITORY, "Bigstupidegg/procurement-materials-platform")
        self.assertEqual(APPROVAL_BASE_BRANCH, "v2.3-c3-2-daily-automation")
        self.assertEqual(APPROVAL_ARTIFACT_DIRECTORY, "research/preregistrations")
        for error_type in (
            ApprovalEvidenceTransportError,
            InvalidGitHubApprovalEvidence,
            ApprovalArtifactMismatch,
        ):
            self.assertTrue(issubclass(error_type, PreregistrationApprovalEvidenceError))

    def test_artifact_path_and_bytes_are_deterministic_canonical_and_identity_only(self):
        path_one = preregistration_approval_artifact_path(protocol=self.protocol)
        path_two = preregistration_approval_artifact_path(protocol=self.protocol)
        raw_one = build_preregistration_approval_artifact_bytes(protocol=self.protocol)
        raw_two = build_preregistration_approval_artifact_bytes(protocol=self.protocol)
        self.assertEqual(path_one, path_two)
        self.assertEqual(
            path_one,
            f"research/preregistrations/{self.protocol.preregistration_identity}.json",
        )
        self.assertNotIn(self.protocol.research_protocol_id, path_one)
        self.assertEqual(raw_one, raw_two)
        self.assertFalse(raw_one.startswith(b"\xef\xbb\xbf"))
        self.assertFalse(raw_one.endswith(b"\n"))
        parsed = parse_json_strict(raw_one)
        self.assertEqual(canonical_json_bytes(parsed), raw_one)
        self.assertEqual(len(parsed), 11)
        self.assertEqual(
            set(parsed),
            {
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
            },
        )

    def test_artifact_functions_require_exact_trusted_protocol(self):
        for substitute in (self.protocol.preregistration_identity, {}, object()):
            with self.subTest(substitute=type(substitute).__name__):
                with self.assertRaises(PreregistrationApprovalEvidenceError):
                    preregistration_approval_artifact_path(protocol=substitute)
                with self.assertRaises(PreregistrationApprovalEvidenceError):
                    build_preregistration_approval_artifact_bytes(protocol=substitute)

    def test_valid_mocked_github_flow_and_operational_builder(self):
        calls = []
        verified = self.verify(calls=calls)
        self.assertIs(type(verified), VerifiedGitHubApprovalEvidence)
        self.assertEqual(verified.evidence_contract_version, "1.0.0")
        self.assertEqual(verified.repository_full_name, APPROVAL_REPOSITORY)
        self.assertEqual(verified.pr_number, PR_NUMBER)
        self.assertEqual(verified.base_branch, APPROVAL_BASE_BRANCH)
        self.assertEqual(verified.head_sha, HEAD_SHA)
        self.assertEqual(verified.merge_commit_sha, MERGE_SHA)
        self.assertEqual(verified.merge_tree_sha, TREE_SHA)
        self.assertEqual(verified.merged_at, MERGED_AT)
        self.assertEqual(verified.merged_by_login, "human-reviewer")
        self.assertEqual(
            verified.artifact_preregistration_identity,
            self.protocol.preregistration_identity,
        )
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[1][1], {"per_page": "100"})
        self.assertEqual(calls[3][1], {"ref": MERGE_SHA})

        record = build_operational_preregistration_approval_record(
            protocol=self.protocol,
            verified_evidence=verified,
            recorded_at=RECORDED_AT,
        )
        self.assertEqual(record.approval_state, APPROVED)
        self.assertEqual(record.operational_status, OPERATIONAL_VERIFIED)
        self.assertEqual(record.approval_evidence_type, GITHUB_PR_MERGE)
        self.assertEqual(record.approved_at, verified.merged_at)
        self.assertEqual(record.preregistration_identity, self.protocol.preregistration_identity)
        self.assertEqual(record.approved_protocol_hash, self.protocol.preregistration_identity)
        require_formal_backtest_authorization(
            approval_record=record, backtest_started_at=BACKTEST_AT
        )
        self.assertTrue(all(value is False for value in SAFETY_FLAGS.values()))

    def test_verified_evidence_is_frozen_slotted_and_rejects_direct_construction(self):
        verified = self.verify()
        values = {item.name: getattr(verified, item.name) for item in fields(verified)}
        with self.assertRaises(InvalidGitHubApprovalEvidence):
            VerifiedGitHubApprovalEvidence(**values)
        with self.assertRaises(FrozenInstanceError):
            verified.merge_commit_sha = "f" * 40
        self.assertFalse(hasattr(verified, "__dict__"))
        self.assertNotIn("_construction_token", {item.name for item in fields(verified)})
        self.assertNotIn("_construction_token", VerifiedGitHubApprovalEvidence.__slots__)

    def test_verifier_inputs_and_transport_failures_are_distinct(self):
        for number in (True, 0, -1, "73"):
            with self.subTest(number=number):
                with self.assertRaises(InvalidGitHubApprovalEvidence):
                    verify_github_pr_approval(protocol=self.protocol, pr_number=number)
        with self.assertRaises(PreregistrationApprovalEvidenceError):
            verify_github_pr_approval(protocol={}, pr_number=PR_NUMBER)
        with patch.object(
            evidence,
            "_github_get_json",
            side_effect=ApprovalEvidenceTransportError("mock 404 or network failure"),
        ):
            with self.assertRaises(ApprovalEvidenceTransportError):
                verify_github_pr_approval(protocol=self.protocol, pr_number=PR_NUMBER)

    def test_pr_metadata_negative_matrix(self):
        cases = (
            ("open PR", ("state",), "open"),
            ("closed unmerged", ("merged",), False),
            ("wrong base", ("base", "ref"), "main"),
            ("wrong base repo", ("base", "repo", "full_name"), "other/repo"),
            ("fork head", ("head", "repo", "full_name"), "fork/repo"),
            ("wrong URL", ("html_url",), "https://example.invalid/pull/73"),
            ("invalid merge SHA", ("merge_commit_sha",), "B" * 40),
            ("invalid head SHA", ("head", "sha"), "short"),
            ("missing merged_at", ("merged_at",), None),
            ("noncanonical merged_at", ("merged_at",), "2026-09-24T13:00:00+08:00"),
            ("Bot type", ("merged_by", "type"), "Bot"),
            ("bot login", ("merged_by", "login"), "reviewer[bot]"),
            ("blank login", ("merged_by", "login"), " "),
            ("missing merged_by", ("merged_by",), None),
            ("changed_files zero", ("changed_files",), 0),
            ("changed_files multiple", ("changed_files",), 2),
        )
        pull_endpoint = self.endpoints()["pull"]
        for name, path, value in cases:
            with self.subTest(name=name):
                responses = self.valid_responses()
                target = responses[pull_endpoint]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.assert_invalid(responses)

    def test_artifact_only_pr_and_code_pr_regression_matrix(self):
        files_endpoint = self.endpoints()["files"]
        artifact_path = preregistration_approval_artifact_path(protocol=self.protocol)
        cases = (
            ("empty files", []),
            ("multiple files", [
                {"filename": artifact_path, "status": "added"},
                {"filename": "scripts/code.py", "status": "modified"},
            ]),
            ("code PR", [{"filename": "scripts/code.py", "status": "added"}]),
            ("test PR", [{"filename": "tests/test_code.py", "status": "added"}]),
            ("workflow PR", [{"filename": ".github/workflows/ci.yml", "status": "added"}]),
            ("modified artifact", [{"filename": artifact_path, "status": "modified"}]),
            ("removed artifact", [{"filename": artifact_path, "status": "removed"}]),
            ("renamed artifact", [{"filename": artifact_path, "status": "renamed"}]),
            ("copied artifact", [{"filename": artifact_path, "status": "copied"}]),
        )
        for name, files in cases:
            with self.subTest(name=name):
                responses = self.valid_responses()
                responses[files_endpoint] = files
                self.assert_invalid(responses)

    def test_merge_commit_negative_matrix(self):
        endpoint = self.endpoints()["commit"]
        cases = (
            ("SHA mismatch", ("sha",), "e" * 40),
            ("invalid tree", ("tree", "sha"), "INVALID"),
            ("second parent mismatch", ("parents", 1, "sha"), "e" * 40),
        )
        for name, path, value in cases:
            with self.subTest(name=name):
                responses = self.valid_responses()
                target = responses[endpoint]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.assert_invalid(responses)
        for parents in ([], [{"sha": BASE_PARENT_SHA}], [
            {"sha": BASE_PARENT_SHA}, {"sha": HEAD_SHA}, {"sha": "e" * 40}
        ]):
            with self.subTest(parent_count=len(parents)):
                responses = self.valid_responses()
                responses[endpoint]["parents"] = parents
                self.assert_invalid(responses)

    def test_merge_commit_artifact_readback_negative_matrix(self):
        endpoint = self.endpoints()["contents"]
        cases = (
            ("not file", "type", "dir"),
            ("wrong path", "path", "research/preregistrations/wrong.json"),
            ("wrong encoding", "encoding", "utf-8"),
            ("too large", "size", 1024 * 1024 + 1),
            ("invalid base64", "content", "%%%"),
        )
        for name, key, value in cases:
            with self.subTest(name=name):
                responses = self.valid_responses()
                responses[endpoint][key] = value
                if key == "content":
                    responses[endpoint]["size"] = 1
                self.assert_invalid(responses, ApprovalArtifactMismatch)
        responses = self.valid_responses()
        del responses[endpoint]
        self.assert_invalid(responses, ApprovalEvidenceTransportError)

    def test_raw_byte_mismatch_matrix(self):
        canonical = build_preregistration_approval_artifact_bytes(protocol=self.protocol)
        parsed = json.loads(canonical)
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False).encode("utf-8")
        variants = (
            ("BOM", b"\xef\xbb\xbf" + canonical),
            ("trailing newline", canonical + b"\n"),
            ("pretty print", pretty),
        )
        for name, raw in variants:
            with self.subTest(name=name):
                responses = self.valid_responses()
                self.set_raw(responses, raw)
                self.assert_invalid(responses, ApprovalArtifactMismatch)

    def test_independent_strict_parse_rejects_duplicate_key(self):
        canonical = build_preregistration_approval_artifact_bytes(protocol=self.protocol)
        duplicate = canonical.replace(
            b'{"approval_intent":',
            b'{"approval_intent":"HUMAN_RESEARCH_PROTOCOL_APPROVAL","approval_intent":',
            1,
        )
        responses = self.valid_responses()
        self.set_raw(responses, duplicate)
        with patch.object(
            evidence, "build_preregistration_approval_artifact_bytes", return_value=duplicate
        ):
            self.assert_invalid(responses, ApprovalArtifactMismatch)

    def test_independent_schema_constant_and_rehash_negative_matrix(self):
        base = parse_json_strict(
            build_preregistration_approval_artifact_bytes(protocol=self.protocol)
        )
        mutations = []

        extra = copy.deepcopy(base)
        extra["extra"] = "forbidden"
        mutations.append(("extra key", extra))
        missing = copy.deepcopy(base)
        del missing["approval_intent"]
        mutations.append(("missing key", missing))
        fields_to_change = {
            "artifact_contract_version": "2.0.0",
            "artifact_type": "WRONG",
            "approval_intent": "WRONG",
            "research_protocol_id": "WRONG",
            "preregistration_version": "9.9.9",
            "preregistration_identity": "0" * 64,
            "protocol_contract_version": "2.0.0",
            "hash_domain": "APPROVAL_CONTENT",
            "canonical_json_profile": "WRONG",
            "hash_profile": "WRONG",
        }
        for key, value in fields_to_change.items():
            changed = copy.deepcopy(base)
            changed[key] = value
            mutations.append((key, changed))
        projection = copy.deepcopy(base)
        projection["protocol_semantic_projection"]["research_task"] = "CHANGED"
        mutations.append(("semantic projection and re-hash", projection))

        for name, payload in mutations:
            with self.subTest(name=name):
                raw = canonical_json_bytes(payload)
                responses = self.valid_responses()
                self.set_raw(responses, raw)
                with patch.object(
                    evidence,
                    "build_preregistration_approval_artifact_bytes",
                    return_value=raw,
                ):
                    self.assert_invalid(responses, ApprovalArtifactMismatch)

    def test_operational_builder_rejects_unverified_inputs_identity_mismatch_and_time_travel(self):
        for substitute in ({}, MERGE_SHA, f"https://github.com/{APPROVAL_REPOSITORY}/pull/73"):
            with self.subTest(substitute=type(substitute).__name__):
                with self.assertRaises(InvalidApprovalRecord):
                    build_operational_preregistration_approval_record(
                        protocol=self.protocol,
                        verified_evidence=substitute,
                        recorded_at=RECORDED_AT,
                    )
        verified = self.verify()
        with self.assertRaises(InvalidApprovalRecord):
            build_operational_preregistration_approval_record(
                protocol=self.protocol,
                verified_evidence=verified,
                recorded_at="2026-09-24T04:59:59Z",
            )
        other = build_protocol(
            self.results,
            version="1.0.1",
            question={**QUESTION, "objective": "Different binding."},
            protocol_id="C4-RD-7-SYNTHETIC-DIRECTION",
        ).protocol
        with self.assertRaises(InvalidApprovalRecord):
            build_operational_preregistration_approval_record(
                protocol=other,
                verified_evidence=verified,
                recorded_at=RECORDED_AT,
            )

    def test_public_api_private_tokens_purity_and_no_live_network_injection(self):
        public_functions = {
            name
            for name, value in vars(evidence).items()
            if inspect.isfunction(value)
            and value.__module__ == evidence.__name__
            and not name.startswith("_")
        }
        self.assertEqual(
            public_functions,
            {
                "preregistration_approval_artifact_path",
                "build_preregistration_approval_artifact_bytes",
                "verify_github_pr_approval",
            },
        )
        self.assertEqual(
            set(inspect.signature(verify_github_pr_approval).parameters),
            {"protocol", "pr_number"},
        )
        evidence_tree = ast.parse(inspect.getsource(evidence))
        governance_tree = ast.parse(inspect.getsource(governance))
        evidence_private_imports = {
            alias.name
            for node in ast.walk(evidence_tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name.startswith("_")
        }
        governance_private_imports = {
            alias.name
            for node in ast.walk(governance_tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name.startswith("_")
        }
        self.assertNotIn("_GOVERNANCE_CONSTRUCTION_TOKEN", evidence_private_imports)
        self.assertFalse(any("EVIDENCE" in name for name in governance_private_imports))
        self.assertNotIn("_VERIFIED_EVIDENCE_CONSTRUCTION_TOKEN", governance_private_imports)
        source = inspect.getsource(evidence)
        for forbidden in ("requests", "subprocess", "sqlite3", "uuid", "random"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("download_url", source)

    def test_hash_and_global_safety_regression(self):
        self.assertEqual(len(HASH_DOMAINS), 13)
        self.assertIn("PREREGISTRATION_CONTENT", HASH_DOMAINS)
        for forbidden in (
            "APPROVAL_CONTENT",
            "APPROVAL_ID",
            "CHANGE_CONTENT",
            "CHANGE_ID",
            "EVIDENCE_CONTENT",
            "ARTIFACT_CONTENT",
        ):
            self.assertNotIn(forbidden, HASH_DOMAINS)
        self.assertEqual(CANONICAL_JSON_PROFILE, "C4_CANONICAL_JSON_V1@1.0.0")
        self.assertEqual(HASH_PROFILE, "C4_HASH_PROFILE_V1@1.0.0")
        self.assertEqual(len(SAFETY_FLAGS), 9)
        self.assertTrue(all(value is False for value in SAFETY_FLAGS.values()))
        self.assertEqual(PIT_ENABLED_SOURCES, ())
        self.assertEqual(RESEARCH_ENABLED_SOURCES, ())
        self.assertEqual(BACKTEST_ENABLED_SOURCES, ())
        self.assertEqual(len(RD3_OPEN_BLOCKERS), 15)


if __name__ == "__main__":
    unittest.main()
