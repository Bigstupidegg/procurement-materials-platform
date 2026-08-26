from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "assets" / "negotiation-report.js"
CSS = ROOT / "assets" / "negotiation-report.css"
PREPARE = ROOT / "scripts" / "prepare_site.py"


class NegotiationReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = JS.read_text(encoding="utf-8")
        cls.css = CSS.read_text(encoding="utf-8")
        cls.prepare = PREPARE.read_text(encoding="utf-8")

    def test_case_and_procurement_fields_exist(self) -> None:
        for field_id in (
            "nr_supplier", "nr_product", "nr_quote_ref", "nr_quote_date",
            "nr_buyer", "nr_price_unit", "nr_supplier_reason", "nr_evidence_received",
            "nr_conclusion", "nr_target_rate", "nr_note",
        ):
            self.assertIn(field_id, self.js)

    def test_report_uses_same_linear_should_cost_formula(self) -> None:
        for token in (
            "values.f_matRatio*values.f_matRate/100",
            "values.f_fxRatio*values.f_fxRate/100",
            "values.f_procRatio*values.f_procRate/100",
            "values.f_energyRatio*values.f_energyRate/100",
            "values.f_otherRatio*values.f_otherRate/100",
            "values.f_supplierAsk-estimate",
        ):
            self.assertIn(token, self.js)

    def test_module_does_not_write_calculator_inputs(self) -> None:
        self.assertNotRegex(self.js, r"getElementById\(['\"]f_[^'\"]+['\"]\)\.value\s*=")
        self.assertNotRegex(self.js, r"querySelector\(['\"]#f_[^'\"]+['\"]\)\.value\s*=")

    def test_csv_export_has_bom_and_formula_injection_guard(self) -> None:
        self.assertIn("'\\uFEFF'+rows.map", self.js)
        self.assertRegex(self.js, r"/\^\[=\+\\-@\]/")
        self.assertIn("text/csv;charset=utf-8", self.js)
        self.assertIn("議價分析_", self.js)

    def test_print_and_pdf_mode_exist(self) -> None:
        for token in ("window.print()", "afterprint", "negotiation-report-printing", "列印／另存PDF"):
            self.assertIn(token, self.js)
        self.assertIn("@media print", self.css)
        self.assertIn("body.negotiation-report-printing", self.css)

    def test_report_contains_decision_support_boundary(self) -> None:
        for token in (
            "不會自動接受或拒絕供應商調價",
            "原材料行情變化不等於成品價格變化",
            "World Bank為主要市場輸入",
            "FRED僅作交叉核對",
        ):
            self.assertIn(token, self.js)

    def test_prepare_site_injects_resources_after_rationality(self) -> None:
        self.assertIn("negotiation-report.css", self.prepare)
        self.assertIn("negotiation-report.js", self.prepare)
        supplier_index = self.prepare.index("supplier-rationality.js")
        report_index = self.prepare.index("negotiation-report.js")
        self.assertLess(supplier_index, report_index)

        spec = importlib.util.spec_from_file_location("prepare_site", PREPARE)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        base = '<html><head></head><body><script src="./assets/app.js"></script></body></html>'
        once = module.inject_resources(base)
        twice = module.inject_resources(once)
        self.assertEqual(once, twice)
        self.assertIn('app-core.js', once)
        self.assertNotIn('src="./assets/app.js"', once)
        self.assertEqual(once.count("negotiation-report.css"), 1)
        self.assertEqual(once.count("negotiation-report.js"), 1)


if __name__ == "__main__":
    unittest.main()
