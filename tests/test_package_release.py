from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "package_release.py"
spec = importlib.util.spec_from_file_location("package_release", MODULE)
release = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = release
spec.loader.exec_module(release)

COMMIT = "1" * 40
SHA = "a" * 64
MONTHS = [f"2024-{m:02d}" for m in range(1, 13)] + ["2025-01"]
UNITS = {
    "zinc": ("公噸(MT)", "($/mt)"),
    "copper": ("公噸(MT)", "($/mt)"),
    "aluminium": ("公噸(MT)", "($/mt)"),
    "nickel": ("公噸(MT)", "($/mt)"),
    "iron_ore": ("乾公噸單位(dmtu)", "($/dmtu)"),
    "crude_oil": ("桶(bbl)", "($/bbl)"),
    "natural_gas": ("MMBtu", "($/mmbtu)"),
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def fixture(root: Path) -> None:
    (root / "assets").mkdir()
    (root / "scripts").mkdir()
    (root / "config").mkdir()
    wb_series, signal_materials, material_config = {}, {}, []
    for mid in release.IDS:
        unit, source_unit = UNITS[mid]
        material_config.append({"id": mid, "currency": "USD", "unit": unit})
        obs = [{"period": period, "value": float(100 + i)} for i, period in enumerate(MONTHS)]
        wb_series[mid] = {"currency": "USD", "displayUnit": unit,
                          "sourceUnit": source_unit, "latestPeriod": "2025-01",
                          "observations": obs}
        changes = {}
        bases = {}
        for key, offset in release.OFFSETS.items():
            bases[key] = obs[-1-offset]["period"]
            changes[key] = round((112 - obs[-1-offset]["value"])
                                 / obs[-1-offset]["value"] * 100, 6)
        signal_materials[mid] = {
            "id": mid, "currency": "USD", "displayUnit": unit,
            "sourceUnit": source_unit, "latestPeriod": "2025-01",
            "latestValue": 112.0, "changes": changes, "changeBasePeriods": bases,
            "negotiationSignal": {"triggerWindow": "3M"},
        }
    write_json(root / "data/world-bank.json", {
        "source": "WORLD_BANK_PINK_SHEET", "isRealData": True,
        "dataset": {"latestPeriod": "2025-01", "workbookUpdatedOn": "2025-02-04",
                    "download": {"sha256": SHA}},
        "series": wb_series,
    })
    write_json(root / "data/signals.json", {
        "isRealData": True, "primarySource": "WORLD_BANK_PINK_SHEET",
        "signalPolicy": {"automaticSupplierPriceDecision": False},
        "latestPeriod": "2025-01", "sourceSha256": SHA,
        "sourceUpdatedOn": "2025-02-04", "materials": signal_materials,
    })
    write_json(root / "data/status.json", {
        "dataMode": "WORLD_BANK_PRIMARY", "isStale": False,
        "worldBank": {"status": "SUCCESS", "latestPeriod": "2025-01",
                      "sourceSha256": SHA, "sourceUpdatedOn": "2025-02-04"},
    })
    write_json(root / "data/comparison.json", {
        "isRealData": True, "primarySource": "WORLD_BANK_PINK_SHEET",
    })
    write_json(root / "config/materials.json", material_config)
    write_json(root / "config/signal-rules.json", {"primarySource": "WORLD_BANK_PINK_SHEET"})
    write_json(root / "config/release.json", {"version": "2.3.0"})
    (root / "scripts/build_signals.py").write_text("synthetic builder\n", encoding="utf-8")
    (root / "scripts/prepare_site.py").write_text("synthetic site builder\n", encoding="utf-8")
    (root / "assets/trend-signals.js").write_text(
        "sourceSha256 sourceUpdatedOn changeBasePeriods manualRateEdited "
        "fetch('./data/signals.json');", encoding="utf-8")
    (root / "assets/supplier-rationality.js").write_text("synthetic supplier\n", encoding="utf-8")
    site = root / "_site"
    shutil.copytree(root / "assets", site / "assets")
    shutil.copytree(root / "data", site / "data")
    (site / "index.html").write_text(
        '<script src="./assets/app-core.js"></script>'
        '<script src="./assets/trend-signals.js"></script>', encoding="utf-8")


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        fixture(self.root)
        self.output = Path(self.temp.name) / "output"
        self.legacy = Path(self.temp.name) / "legacy"
        shutil.copytree(self.root / "_site", self.legacy)
        (self.legacy / "config").mkdir()
        shutil.copy2(self.root / "config/release.json", self.legacy / "config/release.json")
        old = json.loads((self.legacy / "data/signals.json").read_text(encoding="utf-8"))
        old.pop("sourceSha256")
        old.pop("sourceUpdatedOn")
        write_json(self.legacy / "data/signals.json", old)
        (self.legacy / "assets/trend-signals.js").write_text(
            "fetch('./data/signals.json')", encoding="utf-8")

    def change(self, path, action):
        file = self.root / path
        value = json.loads(file.read_text(encoding="utf-8"))
        action(value)
        write_json(file, value)

    def blocked(self):
        with self.assertRaises(release.ReleaseBlocked):
            release.validate_sources(self.root)

    def package(self):
        return release.package_release(self.root, self.root / "_site", self.output, COMMIT, self.legacy)

    def test_matching_release_has_commit_hashes_and_project_path(self):
        manifest = self.package()
        release.verify_release(self.output, manifest["releaseId"])
        self.assertEqual(manifest["sourceCommit"], COMMIT)
        self.assertIn("./releases/" + manifest["releaseId"] + "/index.html",
                      (self.output / "index.html").read_text(encoding="utf-8"))
        self.assertTrue((self.output / "releases" / manifest["releaseId"]
                         / "data/signals.json").is_file())

    def test_new_frontend_rejects_old_schema(self):
        self.change("data/signals.json", lambda d: d.pop("sourceSha256"))
        self.blocked()

    def test_old_frontend_rejects_new_schema(self):
        (self.root / "assets/trend-signals.js").write_text(
            "fetch('./data/signals.json')", encoding="utf-8")
        self.blocked()

    def test_signal_source_hash_mismatch(self):
        self.change("data/signals.json", lambda d: d.update(sourceSha256="b" * 64))
        self.blocked()

    def test_status_source_hash_mismatch(self):
        self.change("data/status.json",
                    lambda d: d["worldBank"].update(sourceSha256="b" * 64))
        self.blocked()

    def test_stale_source_status(self):
        self.change("data/status.json", lambda d: d.update(isStale=True))
        self.blocked()

    def test_missing_comparison_month(self):
        self.change("data/world-bank.json",
                    lambda d: d["series"]["copper"]["observations"].pop(8))
        self.blocked()

    def test_null_and_string_percentage(self):
        for value in (None, "5%"):
            with self.subTest(value=value):
                self.change("data/signals.json",
                            lambda d: d["materials"]["copper"]["changes"].update(
                                sixMonthPercent=value))
                self.blocked()

    def test_wrong_currency_and_unit(self):
        self.change("data/signals.json",
                    lambda d: d["materials"]["copper"].update(currency="EUR"))
        self.blocked()
        self.change("data/signals.json",
                    lambda d: d["materials"]["copper"].update(currency="USD", sourceUnit="wrong"))
        self.blocked()

    def test_unsupported_material_id(self):
        self.change("data/signals.json",
                    lambda d: d["materials"].update(unknown=d["materials"].pop("zinc")))
        self.blocked()

    def test_wrong_anchor_month(self):
        self.change("data/signals.json",
                    lambda d: d["materials"]["copper"]["changeBasePeriods"].update(
                        sixMonthPercent="2024-08"))
        self.blocked()

    def test_wrong_relative_asset_path(self):
        manifest = self.package()
        (self.output / "index.html").write_text('<a href="/releases/wrong">bad</a>',
                                                  encoding="utf-8")
        with self.assertRaises(release.ReleaseBlocked):
            release.verify_release(self.output, manifest["releaseId"])

    def test_mismatched_release_fingerprint(self):
        self.package()
        with self.assertRaises(release.ReleaseBlocked):
            release.verify_release(self.output, "0" * 20)

    def test_two_publish_requests_cannot_reuse_artifact_path(self):
        self.package()
        with self.assertRaises(release.ReleaseBlocked):
            self.package()

    def test_cached_legacy_root_cannot_mix_with_new_json(self):
        self.package()
        self.assertEqual(release.digest(self.output / "assets/trend-signals.js"),
                         release.digest(self.legacy / "assets/trend-signals.js"))
        self.assertEqual(release.digest(self.output / "data/signals.json"),
                         release.digest(self.legacy / "data/signals.json"))
        self.assertNotEqual(release.digest(self.output / "data/signals.json"),
                            release.digest(next((self.output / "releases").glob("*/data/signals.json"))))

    def test_rollback_to_previous_verified_artifact(self):
        first = self.package()
        preserved = Path(self.temp.name) / "previous"
        shutil.copytree(self.output, preserved)
        release.verify_release(preserved, first["releaseId"])
        self.assertEqual(release.tree_hash(preserved, {
            f'releases/{first["releaseId"]}/release-manifest.json'
        }), first["artifactTreeSha256"])

    def test_missing_manifest_or_artifact_hash(self):
        manifest = self.package()
        file = self.output / "releases" / manifest["releaseId"] / "release-manifest.json"
        file.unlink()
        with self.assertRaises(release.ReleaseBlocked):
            release.verify_release(self.output, manifest["releaseId"])

    def test_modified_site_data_is_rejected(self):
        (self.root / "_site/data/signals.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(release.ReleaseBlocked):
            self.package()

    def test_failed_upstream_workflow_is_rejected(self):
        payload = {"repository": {"id": 1315749314,
                   "full_name": "Bigstupidegg/procurement-materials-platform"},
                   "workflow_run": {"name": "Update World Bank Data",
                                    "path": ".github/workflows/update-world-bank.yml",
                                    "conclusion": "failure", "head_branch": "main",
                                    "head_repository": {"id": 1315749314},
                                    "event": "schedule", "head_sha": COMMIT}}
        with self.assertRaises(release.ReleaseBlocked):
            release.validate_event(self.root, "workflow_run", payload, COMMIT)

    def test_untrusted_upstream_or_stale_commit_is_rejected(self):
        payload = {"repository": {"id": 1315749314,
                   "full_name": "Bigstupidegg/procurement-materials-platform"},
                   "workflow_run": {"name": "Quality Check",
                                    "path": ".github/workflows/quality-check.yml", "conclusion": "success",
                                    "head_branch": "main", "head_repository": {"id": 1315749314},
                                    "event": "push", "head_sha": COMMIT}}
        with self.assertRaises(release.ReleaseBlocked):
            release.validate_event(self.root, "workflow_run", payload, COMMIT)
        payload["workflow_run"]["name"] = "Update World Bank Data"
        payload["workflow_run"]["path"] = ".github/workflows/update-world-bank.yml"
        payload["workflow_run"]["head_sha"] = "2" * 40
        with self.assertRaises(release.ReleaseBlocked):
            release.validate_event(self.root, "workflow_run", payload, COMMIT)


if __name__ == "__main__":
    unittest.main()
