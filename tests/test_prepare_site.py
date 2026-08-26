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

    def test_build_production_core_strips_demo_market_engine(self):
        original_site = prepare_site.SITE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                prepare_site.SITE = Path(tmp)
                asset_dir = prepare_site.SITE / "assets"
                asset_dir.mkdir(parents=True)
                source = asset_dir / "app.js"
                source.write_text(
                    "(function(){\n'use strict';\n"
                    "function mulberry32(seed){ return seed; }\n"
                    "const MATERIALS = [1];\n"
                    "function genSeries(){ return []; }\n"
                    + prepare_site.CALCULATOR_MARKER
                    + "\nfunction validateAndCalc(){}\n"
                    + "function switchTab(target){\n"
                    + "  if(target==='chart' && priceChart){ setTimeout(function(){ try{ priceChart.resize(); }catch(e){} },50); }\n"
                    + "}\n})();\n",
                    encoding="utf-8",
                )

                target = prepare_site.build_production_core()
                result = target.read_text(encoding="utf-8")

                self.assertTrue(target.is_file())
                self.assertFalse(source.exists())
                self.assertIn("validateAndCalc", result)
                self.assertIn("procurement:chart-visible", result)
                self.assertNotIn("mulberry32", result)
                self.assertNotIn("const MATERIALS = [", result)
                self.assertNotIn("genSeries(", result)
        finally:
            prepare_site.SITE = original_site

    def test_inject_resources_switches_to_production_core(self):
        html = "<html><head></head><body>" + prepare_site.SOURCE_APP_SCRIPT + "</body></html>"
        result = prepare_site.inject_resources(html)
        self.assertIn(prepare_site.PRODUCTION_APP_SCRIPT, result)
        self.assertNotIn(prepare_site.SOURCE_APP_SCRIPT, result)
        self.assertIn("world-bank-live.js", result)

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


if __name__ == "__main__":
    unittest.main()
