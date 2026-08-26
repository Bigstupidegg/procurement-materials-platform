from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "assets" / "supplier-rationality.js"
CSS = ROOT / "assets" / "supplier-rationality.css"
RULES = ROOT / "data" / "should-cost-rules.json"
PREPARE = ROOT / "scripts" / "prepare_site.py"
APP_CORE = ROOT / "assets" / "app-core.js"
INDEX = ROOT / "index.html"


class SupplierRationalityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = JS.read_text(encoding="utf-8")
        cls.css = CSS.read_text(encoding="utf-8")
        cls.rules = json.loads(RULES.read_text(encoding="utf-8"))
        cls.prepare = PREPARE.read_text(encoding="utf-8")
        cls.app_core = APP_CORE.read_text(encoding="utf-8")
        cls.index = INDEX.read_text(encoding="utf-8")

    def test_rules_are_ordered_and_never_auto_decide(self) -> None:
        thresholds = self.rules["thresholds"]
        self.assertLess(thresholds["modelMatchTolerancePercentagePoints"], thresholds["requestEvidenceGapPercentagePoints"])
        self.assertLess(thresholds["requestEvidenceGapPercentagePoints"], thresholds["highChallengeGapPercentagePoints"])
        policy = self.rules["policy"]
        self.assertFalse(policy["automaticAcceptance"])
        self.assertFalse(policy["automaticRejection"])
        self.assertFalse(policy["rawMaterialChangeEqualsFinishedPriceChange"])
        self.assertEqual(policy["fredRole"], "CORROBORATION_ONLY")

    def test_analysis_uses_same_cost_components(self) -> None:
        for token in (
            "materialImpactPercentagePoints=(values.f_matRatio*values.f_matRate)/100",
            "fxImpactPercentagePoints=(values.f_fxRatio*values.f_fxRate)/100",
            "processImpactPercentagePoints=(values.f_procRatio*values.f_procRate)/100",
            "energyImpactPercentagePoints=(values.f_energyRatio*values.f_energyRate)/100",
            "otherImpactPercentagePoints=(values.f_otherRatio*values.f_otherRate)/100",
            "gapPercentagePoints=supplierAsk-estimatedPercentagePoints",
        ):
            self.assertIn(token, self.js)

    def test_existing_calculator_contract_is_unchanged(self) -> None:
        for token in ("validateAndCalc", "f_supplierAsk", "f_matRatio", "f_matRate", "cmpGap"):
            self.assertIn(token, self.app_core + self.index)
        self.assertNotIn("supplier-rationality", self.app_core)

    def test_recommendations_cover_required_procurement_actions(self) -> None:
        for token in (
            "REQUEST_REDUCTION", "REQUEST_DEEPER_REDUCTION", "CHALLENGE_INCREASE",
            "HIGH_CHALLENGE", "REQUEST_EVIDENCE", "CONDITIONAL_REVIEW",
            "SUPPLIER_BELOW_MODEL", "不自動接受或拒絕",
        ):
            self.assertIn(token, self.js)

    def test_frontend_only_reads_same_origin_rules_and_inputs(self) -> None:
        self.assertIn("./data/should-cost-rules.json", self.js)
        self.assertNotIn("api.stlouisfed.org", self.js)
        self.assertNotIn("FRED_API_KEY", self.js)
        self.assertNotIn("fetch('http", self.js)

    def test_panel_and_styles_are_present(self) -> None:
        for token in (
            "supplierRationalityPanel", "sraMaterialImpact", "sraOtherImpact",
            "sraGap", "sraVerdictTitle", ".sra-panel", ".sra-metrics", ".sra-verdict",
        ):
            self.assertIn(token, self.js + self.css)

    def test_pages_packaging_includes_rules_and_resources(self) -> None:
        for token in ("should-cost-rules.json", "supplier-rationality.css", "supplier-rationality.js"):
            self.assertIn(token, self.prepare)


if __name__ == "__main__":
    unittest.main()
