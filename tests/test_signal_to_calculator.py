from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNALS_JS = ROOT / "assets" / "trend-signals.js"
SIGNALS_CSS = ROOT / "assets" / "trend-signals.css"
APP_JS = ROOT / "assets" / "app.js"


class SignalToCalculatorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.signals_js = SIGNALS_JS.read_text(encoding="utf-8")
        cls.signals_css = SIGNALS_CSS.read_text(encoding="utf-8")
        cls.app_js = APP_JS.read_text(encoding="utf-8")

    def test_existing_calculator_contract_is_present(self) -> None:
        for token in ("f_matRate", "f_matRatio", "resetBtn", "validateAndCalc", "id=\"calc\""):
            self.assertIn(token, self.app_js)

    def test_all_supported_market_windows_are_mapped(self) -> None:
        for key in (
            "oneMonthPercent",
            "threeMonthPercent",
            "sixMonthPercent",
            "twelveMonthPercent",
        ):
            self.assertIn(key, self.signals_js)
        self.assertRegex(
            self.signals_js,
            r"carryPeriodKey\s*=\s*['\"]sixMonthPercent['\"]",
        )

    def test_transfer_updates_only_material_rate_and_recalculates(self) -> None:
        self.assertIn("materialRateInput.value=String(appliedValue)", self.signals_js)
        self.assertIn(
            "materialRateInput.dispatchEvent(new Event('input',{bubbles:true}))",
            self.signals_js,
        )
        self.assertIn("activateTab('calc')", self.signals_js)
        self.assertNotRegex(
            self.signals_js,
            r"getElementById\(['\"]f_matRatio['\"]\)\.value\s*=",
        )
        self.assertNotRegex(
            self.signals_js,
            r"querySelector\(['\"]#f_matRatio['\"]\)\.value\s*=",
        )

    def test_source_context_and_manual_change_state_are_recorded(self) -> None:
        for token in (
            "calcMarketContext",
            "World Bank Pink Sheet",
            "FRED僅作交叉核對",
            "已由訊號卡帶入",
            "已手動修改",
            "本次只更新「原材料價格變化率」",
        ):
            self.assertIn(token, self.signals_js)

    def test_reset_clears_market_context(self) -> None:
        self.assertIn("resetButton.addEventListener('click',clearCalculatorContext)", self.signals_js)
        self.assertIn("lastTransfer=null", self.signals_js)

    def test_required_styles_exist(self) -> None:
        for selector in (
            ".sig-carry",
            ".sig-carry-btn",
            ".calc-market-context",
            ".field.market-linked",
        ):
            self.assertIn(selector, self.signals_css)


if __name__ == "__main__":
    unittest.main()
