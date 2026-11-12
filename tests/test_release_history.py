from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_package_release as fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_history as history

release = fixtures.release


class RetainedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReleasePackageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.site = self.fixture.root, self.fixture.root / "_site"
        self.base = Path(self.fixture.temp.name)
        self.first = self.base / "first"
        self.one = release.package_release(self.root, self.site, self.first,
                                          "1" * 40, self.fixture.legacy)

    def next_release(self, previous, name, commit):
        output = self.base / name
        metadata = release.package_release(self.root, self.site, output, commit * 40,
                                           self.fixture.legacy, previous)
        return output, metadata

    def test_three_releases_preserve_old_urls_and_bytes(self):
        second, two = self.next_release(self.first, "second", "2")
        third, three = self.next_release(second, "third", "3")
        self.assertEqual(set(three["retainedReleaseIds"]),
                         {self.one["releaseId"], two["releaseId"]})
        for prior in (self.first, second):
            for file in (prior / "releases").rglob("*"):
                if file.is_file():
                    self.assertEqual(file.read_bytes(),
                                     (third / file.relative_to(prior)).read_bytes())
        release.verify_release(third, three["releaseId"])

    def test_rollback_preserves_newer_urls_and_later_forward_history(self):
        second, two = self.next_release(self.first, "second", "2")
        restored = self.base / "restored"
        metadata = history.recover(second, self.first, restored, 101)
        self.assertEqual(metadata["releaseId"], self.one["releaseId"])
        self.assertEqual(metadata["mode"], "rollback")
        self.assertEqual(metadata["recoveredFromRun"], 101)
        self.assertEqual(metadata["retainedReleaseIds"], [two["releaseId"]])
        self.assertEqual((restored / "index.html").read_bytes(),
                         (self.first / "index.html").read_bytes())
        for file in (second / "releases").rglob("*"):
            if file.is_file():
                self.assertEqual(file.read_bytes(),
                                 (restored / file.relative_to(second)).read_bytes())
        forward, result = self.next_release(restored, "fourth", "4")
        self.assertEqual(set(result["retainedReleaseIds"]),
                         {self.one["releaseId"], two["releaseId"]})
        release.verify_release(forward, result["releaseId"])

    def test_tampered_retained_release_cannot_be_reused(self):
        second, two = self.next_release(self.first, "second", "2")
        (second / "releases" / self.one["releaseId"] / "index.html").write_text("tampered")
        with self.assertRaises(release.ReleaseBlocked):
            self.next_release(second, "third", "3")
        # Resealing the outer metadata does not bypass the immutable release hash.
        release.seal_publication(second, two["releaseId"], "forward")
        with self.assertRaisesRegex(release.ReleaseBlocked, "retained release hash"):
            release.verify_release(second, two["releaseId"])

    def test_missing_publication_or_false_history_set_is_blocked(self):
        metadata = release.read_json(self.first / release.PUBLICATION)
        metadata["retainedReleaseIds"] = ["f" * 20]
        fixtures.write_json(self.first / release.PUBLICATION, metadata)
        with self.assertRaises(release.ReleaseBlocked):
            release.verify_release(self.first, self.one["releaseId"])
        (self.first / release.PUBLICATION).unlink()
        with self.assertRaises(release.ReleaseBlocked):
            self.next_release(self.first, "second", "2")

    def test_history_size_limit_stops_without_discarding_versions(self):
        original = release.tree_hash(self.first)
        with patch.object(release, "MAX_SITE_BYTES", 1):
            with self.assertRaisesRegex(release.ReleaseBlocked, "refusing to discard"):
                self.next_release(self.first, "second", "2")
        self.assertEqual(release.tree_hash(self.first), original)

    def test_valid_tar_roundtrip_preserves_manifest(self):
        download = self.base / "download"
        download.mkdir()
        with tarfile.open(download / "artifact.tar", "w") as archive:
            archive.add(self.first, arcname=".")
        output = self.base / "unpacked"
        with patch.object(history, "ancestor") as ancestry:
            result = history.unpack(download, output, self.root, "2" * 40)
        ancestry.assert_called_once_with(self.root, "1" * 40, "2" * 40)
        self.assertEqual(result, self.one)
        self.assertEqual(release.tree_hash(output), release.tree_hash(self.first))

    def test_unsafe_tar_paths_links_devices_and_duplicates_are_rejected(self):
        for kind in ("parent", "absolute", "symlink", "hardlink", "device", "duplicate"):
            with self.subTest(kind=kind):
                download = self.base / kind
                download.mkdir()
                with tarfile.open(download / "artifact.tar", "w") as archive:
                    member = tarfile.TarInfo("../escape" if kind == "parent"
                                             else "/escape" if kind == "absolute" else "safe")
                    if kind in ("symlink", "hardlink"):
                        member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                        member.linkname = "../escape"
                    elif kind == "device":
                        member.type = tarfile.CHRTYPE
                    archive.addfile(member)
                    if kind == "duplicate":
                        archive.addfile(tarfile.TarInfo("./safe"))
                output = self.base / f"out-{kind}"
                with self.assertRaises(release.ReleaseBlocked):
                    history.unpack(download, output, self.root, "2" * 40)
                self.assertFalse(output.exists())

    def test_downloaded_file_tamper_is_rejected(self):
        (self.first / "index.html").write_text("changed after publication")
        download = self.base / "download"
        download.mkdir()
        with tarfile.open(download / "artifact.tar", "w") as archive:
            archive.add(self.first, arcname=".")
        with self.assertRaises(release.ReleaseBlocked):
            history.unpack(download, self.base / "unpacked", self.root, "2" * 40)


class HistorySelectionTests(unittest.TestCase):
    def setUp(self):
        self.run = {"workflow_id": 7, "path": history.WORKFLOW, "head_repository": {
            "id": history.REPOSITORY_ID}, "head_branch": "main", "event": "push",
            "status": "completed", "conclusion": "success", "head_sha": "1" * 40}
        self.calls = []
        self.expired = False
        self.missing = False
        self.foreign_target = False
        self.empty = False

    def api(self, endpoint):
        self.calls.append(endpoint)
        if endpoint == "actions/runs/99":
            return {"repository": {"id": history.REPOSITORY_ID},
                    "path": history.WORKFLOW, "head_branch": "main", "workflow_id": 7}
        if endpoint.startswith("actions/workflows/7/runs?"):
            # Run creation order is not deployment order.
            return {"workflow_runs": [] if self.empty else [dict(self.run, id=10), dict(self.run, id=11)]}
        if endpoint in ("actions/runs/10", "actions/runs/11"):
            run = dict(self.run, id=int(endpoint.rsplit("/", 1)[1]))
            if self.foreign_target:
                run["head_repository"] = {"id": 42}
            return run
        if "/jobs?" in endpoint:
            run_id = int(endpoint.split("/")[2])
            return {"jobs": [{"steps": [
                {"name": "Upload validated Pages artifact", "conclusion": "success"},
                {"name": "Deploy GitHub Pages", "conclusion": "success",
                 "completed_at": f"2026-11-12T00:{run_id}:00Z"}]}]}
        if "/artifacts?" in endpoint:
            run_id = int(endpoint.split("/")[2])
            return {"total_count": 0 if self.missing else 1, "artifacts": [] if self.missing else [{
                "name": "github-pages", "id": run_id * 100, "expired": self.expired,
                "workflow_run": {"head_sha": "1" * 40}}]}
        raise AssertionError(endpoint)

    def select(self, rollback=0):
        with patch.object(history, "api", side_effect=self.api), \
             patch.object(history, "ancestor"), \
             patch.object(history.subprocess, "run", return_value=subprocess.CompletedProcess(
                 [], 0, "Upload validated Pages artifact", "")):
            return history.select_history(Path("."), "2" * 40, 99, rollback)

    def test_selects_last_actual_deployment_not_creation_order(self):
        self.assertEqual(self.select()["latest"]["runId"], 11)

    def test_expired_latest_or_missing_artifact_does_not_fall_back(self):
        for field in ("expired", "missing"):
            setattr(self, field, True)
            with self.assertRaisesRegex(release.ReleaseBlocked, "missing or expired"):
                self.select()
            setattr(self, field, False)

    def test_first_version_bootstrap_has_no_previous_artifact(self):
        self.empty = True
        self.assertEqual(self.select(), {"latest": None, "target": None})

    def test_pre_migration_workflow_is_not_mistaken_for_versioned_history(self):
        with patch.object(history, "api", side_effect=self.api), \
             patch.object(history, "ancestor"), \
             patch.object(history.subprocess, "run", return_value=subprocess.CompletedProcess(
                 [], 0, "Upload GitHub Pages artifact", "")):
            selected = history.select_history(Path("."), "2" * 40, 99)
        self.assertEqual(selected, {"latest": None, "target": None})
        self.assertFalse(any("/artifacts?" in call for call in self.calls))

    def test_explicit_manual_rollback_selects_trusted_older_run(self):
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch"}):
            selected = self.select(10)
        self.assertEqual(selected["latest"]["runId"], 11)
        self.assertEqual(selected["target"]["runId"], 10)

    def test_automatic_or_foreign_rollback_is_rejected(self):
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push"}):
            with self.assertRaises(release.ReleaseBlocked):
                self.select(10)
        self.foreign_target = True
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch"}):
            with self.assertRaises(release.ReleaseBlocked):
                self.select(10)

    def test_foreign_failed_pr_or_wrong_workflow_run_is_untrusted(self):
        for changes in ({"head_repository": {"id": 42}}, {"event": "pull_request"},
                        {"conclusion": "failure"}, {"head_branch": "branch"},
                        {"path": ".github/workflows/other.yml"}, {"workflow_id": 8}):
            with self.subTest(changes=changes):
                self.assertFalse(history.trusted_run(dict(self.run, **changes), 7))

    def test_unrelated_artifact_commit_is_rejected(self):
        with patch.object(history.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaises(release.ReleaseBlocked):
                history.ancestor(Path("."), "1" * 40, "2" * 40)


if __name__ == "__main__":
    unittest.main()
