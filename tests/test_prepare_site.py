import tempfile
import unittest
from pathlib import Path

from scripts import prepare_site


class PrepareSiteV23Tests(unittest.TestCase):
    def test_normalize_index_identity_removes_obsolete_demo_claims(self):
        html = (
            '<title>國際原材料價格與採購分析平台（原型・示範資料）v1.2.1</title>'
            'International Raw Materials Procurement Analytics（介面原型 v1.2.1）'
            '<span class="demo-pill">示範資料原型・非真實市場行情</span>'
            '本平台目前為<b>介面原型（Prototype）v1.2.1</b>，所有價格、走勢與計算結果均為<b>示範資料</b>，未連接任何外部市場資料來源，<b>不得作為採購、財務或投資決策依據</b>。'
        )
        result = prepare_site.normalize_index_identity(html, "2.3.0")
        self.assertIn("v2.3.0", result)
        self.assertIn("正式市場資料・採購決策支援", result)
        self.assertIn("World Bank Pink Sheet", result)
        self.assertNotIn("未連接任何外部市場資料來源", result)
        self.assertNotIn("介面原型 v1.2.1", result)

    def test_source_boundary_is_explicit(self):
        prepare_site.validate_source_boundary()
        core = (prepare_site.ASSETS / "app-core.js").read_text(encoding="utf-8")
        demo = (prepare_site.ASSETS / "demo-market.js").read_text(encoding="utf-8")
        bootstrap = (prepare_site.ASSETS / "app.js").read_text(encoding="utf-8")
        self.assertIn("validateAndCalc", core)
        self.assertIn("switchTab", core)
        self.assertNotIn("const MATERIALS", core)
        self.assertNotIn("Development Demo fixture", core)
        self.assertIn("Development Demo fixture", demo)
        self.assertNotIn("validateAndCalc", demo)
        self.assertIn("app-core.js", bootstrap)
        self.assertIn("demo-market.js", bootstrap)

    def test_inject_resources_switches_bootstrap_to_production_core(self):
        html = "<html><head></head><body>" + prepare_site.SOURCE_BOOTSTRAP_SCRIPT + "</body></html>"
        result = prepare_site.inject_resources(html)
        twice = prepare_site.inject_resources(result)
        self.assertEqual(result, twice)
        self.assertIn(prepare_site.PRODUCTION_CORE_SCRIPT, result)
        self.assertNotIn(prepare_site.SOURCE_BOOTSTRAP_SCRIPT, result)
        self.assertIn("world-bank-live.js", result)
        self.assertIn("data-freshness.js", result)
        self.assertLess(result.index("world-bank-live.js"), result.index("data-freshness.js"))

    def test_remove_development_assets_and_verify_boundary(self):
        original_site = prepare_site.SITE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                prepare_site.SITE = Path(tmp)
                assets = prepare_site.SITE / "assets"
                assets.mkdir(parents=True)
                (assets / "app.js").write_text("bootstrap", encoding="utf-8")
                (assets / "demo-market.js").write_text("const MATERIALS=[]", encoding="utf-8")
                (assets / "app-core.js").write_text("function validateAndCalc(){}", encoding="utf-8")
                (prepare_site.SITE / "index.html").write_text(
                    '<html><body><script src="./assets/app-core.js"></script></body></html>',
                    encoding="utf-8",
                )
                prepare_site.remove_development_assets()
                prepare_site.verify_production_boundary()
                self.assertFalse((assets / "app.js").exists())
                self.assertFalse((assets / "demo-market.js").exists())
                self.assertTrue((assets / "app-core.js").exists())
        finally:
            prepare_site.SITE = original_site

    def test_normalize_live_asset_identity_uses_release_version(self):
        original_site = prepare_site.SITE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                prepare_site.SITE = Path(tmp)
                asset_dir = prepare_site.SITE / "assets"
                asset_dir.mkdir(parents=True)
                live = asset_dir / "world-bank-live.js"
                live.write_text(
                    "International Raw Materials Procurement Analytics（World Bank 月度資料版 v1.3.0）\n"
                    "document.title='國際原材料價格與採購分析平台｜World Bank 月度資料';\n",
                    encoding="utf-8",
                )
                prepare_site.normalize_live_asset_identity("2.3.0")
                result = live.read_text(encoding="utf-8")
                self.assertIn("World Bank 月度資料版 v2.3.0", result)
                self.assertIn("World Bank 月度資料 v2.3.0", result)
                self.assertNotIn("v1.3.0", result)
        finally:
            prepare_site.SITE = original_site

    def test_freshness_panel_uses_same_origin_status(self):
        js = (prepare_site.ASSETS / "data-freshness.js").read_text(encoding="utf-8")
        for token in (
            "./data/status.json",
            "最新市場月份",
            "World Bank 最後同步",
            "來源資料更新日",
            "FRED 交叉核對",
            "isStale",
        ):
            self.assertIn(token, js)
        self.assertNotIn("fetch('http", js)


if __name__ == "__main__":
    unittest.main()
