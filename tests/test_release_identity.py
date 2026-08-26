from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRESHNESS = ROOT / "assets" / "data-freshness.js"
RELEASE = ROOT / "config" / "release.json"


class ReleaseIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.freshness = FRESHNESS.read_text(encoding="utf-8")
        cls.release = json.loads(RELEASE.read_text(encoding="utf-8"))

    def test_release_metadata_is_v23(self) -> None:
        self.assertEqual(self.release["version"], "2.3.0")
        self.assertEqual(self.release["releaseLine"], "v2.3")

    def test_runtime_identity_prefers_release_metadata(self) -> None:
        for token in (
            "./config/release.json",
            "loadReleaseIdentity",
            "Real Data + Procurement Decision Support v",
            "document.documentElement.dataset.releaseVersion",
        ):
            self.assertIn(token, self.freshness)

    def test_runtime_identity_falls_back_to_built_dom_version(self) -> None:
        for token in (
            "releaseFromDom",
            "DOM_BUILD_IDENTITY",
            "match(/v(\\d+\\.\\d+\\.\\d+)/)",
            "using built DOM identity",
        ):
            self.assertIn(token, self.freshness)

    def test_identity_owner_recovers_from_late_module_overrides(self) -> None:
        self.assertIn("MutationObserver", self.freshness)
        self.assertIn("observer.observe(sub", self.freshness)
        self.assertIn("if(sub.textContent!==desired)sub.textContent=desired", self.freshness)


if __name__ == "__main__":
    unittest.main()
