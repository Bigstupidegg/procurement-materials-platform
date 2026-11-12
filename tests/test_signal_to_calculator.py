from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNALS_JS = ROOT / "assets" / "trend-signals.js"
SIGNALS_CSS = ROOT / "assets" / "trend-signals.css"
APP_CORE_JS = ROOT / "assets" / "app-core.js"
INDEX_HTML = ROOT / "index.html"


class SignalToCalculatorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.signals_js = SIGNALS_JS.read_text(encoding="utf-8")
        cls.signals_css = SIGNALS_CSS.read_text(encoding="utf-8")
        cls.app_core_js = APP_CORE_JS.read_text(encoding="utf-8")
        cls.index_html = INDEX_HTML.read_text(encoding="utf-8")

    def test_existing_calculator_contract_is_present(self) -> None:
        for token in ("f_matRate", "f_matRatio", "resetBtn", "validateAndCalc"):
            self.assertIn(token, self.app_core_js)
        self.assertIn('id="calc"', self.index_html)
        self.assertIn("sourceSha256", self.signals_js)
        self.assertIn("changeBasePeriods", self.signals_js)

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
        self.assertIn("resetButton.addEventListener('click',()=>{", self.signals_js)
        self.assertIn("manualRateEdited=false", self.signals_js)
        self.assertIn("clearCalculatorContext();", self.signals_js)
        self.assertIn("lastTransfer=null", self.signals_js)

    def test_required_styles_exist(self) -> None:
        for selector in (
            ".sig-carry",
            ".sig-carry-btn",
            ".calc-market-context",
            ".field.market-linked",
        ):
            self.assertIn(selector, self.signals_css)


NODE_HANDOFF_SCRIPT = r"""
'use strict';
const fs=require('fs'),vm=require('vm');
const sourcePath=process.argv[1],mode=process.argv[2];
function check(ok,message){if(!ok)throw Error(message);}
const pendingTimers=[],focusEvents=[],scrollEvents=[];
const deferTimers=mode.startsWith('focus_');
function element(id='anonymous'){
  const handlers={},classes=new Set();
  const node={id,value:'',textContent:'',innerHTML:'',dataset:{},
    classList:{add:(...x)=>x.forEach(v=>classes.add(v)),
      remove:(...x)=>x.forEach(v=>classes.delete(v)),
      toggle:(v,on)=>on?classes.add(v):classes.delete(v),
      contains:v=>classes.has(v)},
    addEventListener:(name,fn)=>(handlers[name]||(handlers[name]=[])).push(fn),
    dispatchEvent:event=>(handlers[event.type]||[]).forEach(fn=>fn(event)),
    closest:()=>element(),
    focus:()=>{document.activeElement=node;focusEvents.push(id);},
    scrollIntoView:()=>scrollEvents.push(id),setAttribute:()=>{}};
  return node;
}
const nodes={};
const get=id=>(nodes[id]||(nodes[id]=element(id)));
const document={querySelector:s=>s.startsWith('#')?get(s.slice(1)):null,
  getElementById:get,querySelectorAll:()=>[],activeElement:null};
const window={setTimeout:(fn,delay)=>deferTimers?pendingTimers.push({fn,delay}):fn()};
const Event=class{constructor(type){this.type=type;}};
let source=fs.readFileSync(sourcePath,'utf8');
const anchor='\ninit();\n})();';
check(source.includes(anchor),'test hook missing');
source=source.replace(anchor,"\nwindow.__test={setData:(s,st)=>{signals=s;status=st;},validate,setupInteractions,transferToCalculator};\n})();");
vm.runInNewContext(source,{document,window,Event,console,Intl,Date,Number,Math,String,Error,Set,Array,isFinite});
const ids=['zinc','copper','aluminium','nickel','iron_ore','crude_oil','natural_gas'];
const changes={oneMonthPercent:1,threeMonthPercent:5,sixMonthPercent:10,twelveMonthPercent:2};
if(mode==='aligned_down'){changes.threeMonthPercent=-5;changes.sixMonthPercent=-10;}
if(mode==='up_down'){changes.threeMonthPercent=10;changes.sixMonthPercent=-5;}
if(mode==='down_up'){changes.threeMonthPercent=-10;changes.sixMonthPercent=5;}
const materials={};
for(const id of ids)materials[id]={id,currency:'USD',sourceUnit:'($/mt)',displayUnit:'公噸(MT)',
  latestPeriod:'2025-01',changes:Object.assign({},changes),
  changeBasePeriods:{oneMonthPercent:'2024-12',threeMonthPercent:'2024-10',
    sixMonthPercent:'2024-07',twelveMonthPercent:'2024-01'},
  negotiationSignal:{triggerWindow:'3M'},trend:{}};
const signals={isRealData:true,primarySource:'WORLD_BANK_PINK_SHEET',comparisonSource:'FRED',
  signalPolicy:{worldBankDeterminesTrend:true,fredUsedForCorroborationOnly:true,
    automaticSupplierPriceDecision:false},
  latestPeriod:'2025-01',sourceSha256:'a'.repeat(64),sourceUpdatedOn:'2025-02-04',materials};
const status={dataMode:'WORLD_BANK_PRIMARY',isStale:false,
  worldBank:{status:'SUCCESS',latestPeriod:'2025-01',
    sourceSha256:'a'.repeat(64),sourceUpdatedOn:'2025-02-04'}};
if(mode==='old_schema'){delete signals.sourceSha256;delete signals.sourceUpdatedOn;
  for(const id of ids){delete materials[id].changeBasePeriods;delete materials[id].negotiationSignal.triggerWindow;}}
if(mode==='null')materials.copper.changes.sixMonthPercent=null;
if(mode==='string_percent')materials.copper.changes.sixMonthPercent='5%';
if(mode==='stale')status.isStale=true;
if(mode==='version_mismatch')status.worldBank.sourceSha256='b'.repeat(64);
if(mode==='wrong_anchor')materials.copper.changeBasePeriods.sixMonthPercent='2024-08';
if(mode==='currency')materials.copper.currency='EUR';
window.__test.setData(signals,status);
const rejection=['old_schema','null','string_percent','stale','version_mismatch','wrong_anchor','currency'];
if(rejection.includes(mode)){
  let rejected=false;
  try{window.__test.validate();}catch(error){rejected=true;}
  check(rejected,'unsafe payload accepted: '+mode);
}else{
  window.__test.validate();
  window.__test.setupInteractions();
  const rate=get('f_matRate');
  rate.value='6';
  get('f_matRatio').value='35';
  if(mode==='manual'){
    rate.value='4.2';
    rate.dispatchEvent(new Event('input'));
    let rejected=false;
    try{window.__test.transferToCalculator('copper');}catch(error){rejected=true;}
    check(rejected&&rate.value==='4.2','manual value overwritten');
  }else{
    window.__test.transferToCalculator('copper');
    check(rate.value===String(Math.round(changes.sixMonthPercent*100)/100),'wrong six-month value');
    check(get('f_matRatio').value==='35','manual weight changed');
    check(get('calcMarketContextWindow').textContent.includes('2024/07'),'base period not shown');
    check(get('calcMarketContextSourceVersion').textContent==='2025-02-04','source version not shown');
    if(mode==='up_down'||mode==='down_up')
      check(get('calcMarketContextNote').textContent.includes('方向相反'),'opposite horizons not disclosed');
    if(deferTimers){
      check(pendingTimers.some(timer=>timer.delay===60),'real deferred handoff not exercised');
      if(mode==='focus_normal'){
        check(document.activeElement===rate,'handoff must focus material input synchronously');
        const count=focusEvents.length;
        pendingTimers.splice(0).forEach(timer=>timer.fn());
        check(document.activeElement===rate,'ordinary handoff lost focus');
        check(focusEvents.length===count,'deferred callback must never focus an input');
        check(scrollEvents.length===1,'ordinary handoff lost context scroll');
      }else if(mode==='focus_rapid'){
        materials.aluminium.changes.sixMonthPercent=20;
        window.__test.transferToCalculator('aluminium');
        check(rate.value==='20','second handoff did not replace the market value');
        const other=get('nr_note');other.focus();other.value='latest note';
        pendingTimers.splice(0).forEach(timer=>timer.fn());
        check(document.activeElement===other,'older queued handoff stole focus');
        check(rate.value==='20'&&other.value==='latest note','queued handoff changed inputs');
        check(scrollEvents.length===0,'queued handoff scrolled away from current work');
      }else{
        const target=get(mode==='focus_numeric'?'f_price':mode==='focus_reset'?'resetBtn':
          mode==='focus_navigation'?'overviewTab':'nr_note');
        target.focus();target.value=mode==='focus_numeric'?'1250':'UAT note';
        if(mode==='focus_reset'){
          target.dispatchEvent(new Event('click'));
          rate.value='6';
        }
        const expectedRate=rate.value,expectedTarget=target.value;
        pendingTimers.splice(0).forEach(timer=>timer.fn());
        check(document.activeElement===target,'deferred handoff stole focus from '+target.id);
        check(rate.value===expectedRate&&target.value===expectedTarget,'deferred handoff changed inputs');
        check(scrollEvents.length===0,'deferred handoff scrolled away from current work');
      }
      console.log(JSON.stringify({mode,active:document.activeElement.id,focusEvents,scrollEvents}));
    }
  }
}
console.log('PASS '+mode);
"""

class SyntheticDOMHandoffTests(unittest.TestCase):
    def check_synthetic(self, mode: str) -> None:
        result = subprocess.run(
            ["node", "-e", NODE_HANDOFF_SCRIPT, str(SIGNALS_JS), mode],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS " + mode, result.stdout)

    def test_aligned_up(self): self.check_synthetic("aligned_up")
    def test_aligned_down(self): self.check_synthetic("aligned_down")
    def test_up_down(self): self.check_synthetic("up_down")
    def test_down_up(self): self.check_synthetic("down_up")
    def test_manual_input_priority(self): self.check_synthetic("manual")
    def test_old_schema_fails_closed(self): self.check_synthetic("old_schema")
    def test_null_percentage_fails_closed(self): self.check_synthetic("null")
    def test_string_percentage_fails_closed(self): self.check_synthetic("string_percent")
    def test_stale_status_fails_closed(self): self.check_synthetic("stale")
    def test_source_version_fails_closed(self): self.check_synthetic("version_mismatch")
    def test_wrong_base_month_fails_closed(self): self.check_synthetic("wrong_anchor")
    def test_wrong_currency_fails_closed(self): self.check_synthetic("currency")
    def test_focus_is_set_before_deferred_scroll(self): self.check_synthetic("focus_normal")
    def test_metadata_edit_keeps_focus(self): self.check_synthetic("focus_metadata")
    def test_numeric_edit_keeps_focus(self): self.check_synthetic("focus_numeric")
    def test_reset_cancels_deferred_scroll_and_focus(self): self.check_synthetic("focus_reset")
    def test_navigation_cancels_deferred_scroll_and_focus(self): self.check_synthetic("focus_navigation")
    def test_rapid_handoffs_cannot_steal_later_focus(self): self.check_synthetic("focus_rapid")


if __name__ == "__main__":
    unittest.main()
