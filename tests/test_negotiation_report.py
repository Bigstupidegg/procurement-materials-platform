from __future__ import annotations

import importlib.util
import re
import subprocess
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


NODE_REPORT_STATE = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const nodes=new Map();
function element(){
  const classes=new Set();
  return {value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,
    classList:{contains:x=>classes.has(x),add:x=>classes.add(x),remove:x=>classes.delete(x)},
    appendChild:()=>{}};
}
const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
const document={getElementById:get,querySelector:s=>get(s.slice(1)),
  querySelectorAll:()=>[],createElement:element};
let printCalls=0,blobCalls=0;
const window={requestAnimationFrame:fn=>fn(),print:()=>printCalls++};
let source=fs.readFileSync(process.argv[1],'utf8');
const anchor='\ninit();\n})();';
assert(source.includes(anchor),'test hook must match module entry point');
source=source.replace(anchor,'\nwindow.__test={collect,render,exportCsv,printReport};\n})();');
vm.runInNewContext(source,{window,document,Intl,Date,Number,console,
  Blob:class{constructor(){blobCalls++;}}});
const api=window.__test;
const defaults={f_price:1000,f_matRatio:40,f_matRate:12.02,f_fxRatio:0,f_fxRate:0,
  f_procRatio:30,f_procRate:0,f_energyRatio:10,f_energyRate:0,
  f_otherRatio:20,f_otherRate:0,f_supplierAsk:8};
for(const [id,value] of Object.entries(defaults))get(id).value=String(value);
get('sraSupplierAsk').textContent='+8.00%';
get('sraBadge').textContent='高優先挑戰漲價';
get('sraVerdictTitle').textContent='供應商要求明顯高於Should-Cost推估';
get('nr_supplier').value='UAT supplier';
const valid=api.collect();assert.equal(valid.valid,true);
api.render(valid);assert.equal(get('nrReport').hidden,false);
assert.equal(get('nrOutEstimate').textContent,'+4.81%');

const changes={ratio:['f_matRatio','80'],rate:['f_matRate','-101'],
  price:['f_price','-1'],target:['nr_target_rate','-101'],pending:null};
const mode=process.argv[2];assert(Object.hasOwn(changes,mode));
if(changes[mode])get(changes[mode][0]).value=changes[mode][1];
else get('sraBadge').classList.add('waiting');
const invalid=api.collect();assert.equal(invalid.valid,false);
api.render(invalid);
assert.equal(get('nrReport').hidden,true,'old report must not remain visible');
assert.equal(get('nrExportCsv').disabled,true);
assert.equal(get('nrPrint').disabled,true);
assert(get('nrStatus').textContent.startsWith('暫停匯出：'));
api.exportCsv();api.printReport();
assert.equal(blobCalls,0,'invalid input must never create an export');
assert.equal(printCalls,0,'invalid input must never open print');
assert.equal(get('nr_supplier').value,'UAT supplier','case input must be retained');

if(changes[mode])get(changes[mode][0]).value=mode==='target'?'':String(defaults[changes[mode][0]]);
else get('sraBadge').classList.remove('waiting');
get('f_matRate').value='10';
const restored=api.collect();assert.equal(restored.valid,true);api.render(restored);
assert.equal(get('nrReport').hidden,false);
assert.equal(get('nrOutEstimate').textContent,'+4.00%','report must use current restored values');
assert.equal(get('nrExportCsv').disabled,false);
assert.equal(get('nrPrint').disabled,false);
console.log('PASS '+mode);
"""


class NegotiationReportBehaviorTests(unittest.TestCase):
    def check_transition(self, mode):
        result = subprocess.run(["node", "-e", NODE_REPORT_STATE, str(JS), mode],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ratio_invalid_hides_old_report_and_recovers(self):
        self.check_transition("ratio")

    def test_rate_invalid_hides_old_report_and_recovers(self):
        self.check_transition("rate")

    def test_price_invalid_hides_old_report_and_recovers(self):
        self.check_transition("price")

    def test_target_invalid_hides_old_report_and_recovers(self):
        self.check_transition("target")

    def test_pending_rationality_hides_old_report_and_recovers(self):
        self.check_transition("pending")


if __name__ == "__main__":
    unittest.main()
