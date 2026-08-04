(function(){
'use strict';

const RULES_URL='./data/should-cost-rules.json';
const FIELD_IDS=[
  'f_price','f_matRatio','f_matRate','f_fxRatio','f_fxRate',
  'f_procRatio','f_procRate','f_energyRatio','f_energyRate',
  'f_otherRatio','f_otherRate','f_supplierAsk'
];
const RATIO_IDS=['f_matRatio','f_fxRatio','f_procRatio','f_energyRatio','f_otherRatio'];
const RATE_IDS=['f_matRate','f_fxRate','f_procRate','f_energyRate','f_otherRate','f_supplierAsk'];

let rules=null;

const $=selector=>document.querySelector(selector);
const finite=value=>Number.isFinite(Number(value));
const number=(value,decimals=2)=>finite(value)
  ?Number(value).toLocaleString('zh-Hant-TW',{minimumFractionDigits:decimals,maximumFractionDigits:decimals})
  :'—';
const signed=(value,decimals=2)=>{
  if(!finite(value))return '—';
  const numeric=Number(value);
  return `${numeric>0?'+':''}${number(numeric,decimals)}`;
};
const signedPercent=value=>finite(value)?`${signed(value)}%`:'—';
const signedPoints=value=>finite(value)?`${signed(value)} 個百分點`:'—';
const direction=value=>Number(value)>0.0005?'up':Number(value)<-0.0005?'down':'flat';

function addPanel(){
  if($('#supplierRationalityPanel'))return;
  const resultPanel=$('#calc .result-panel');
  const formulaBox=$('#formulaBox');
  if(!resultPanel||!formulaBox)throw Error('找不到Should-Cost結果區');

  const panel=document.createElement('section');
  panel.id='supplierRationalityPanel';
  panel.className='sra-panel';
  panel.setAttribute('aria-live','polite');
  panel.innerHTML=`
    <div class="sra-head">
      <div>
        <div class="sra-eyebrow">3D-2B SUPPLIER REQUEST REVIEW</div>
        <h3>供應商漲幅合理性分析</h3>
        <p>比較供應商要求、Should-Cost推估與各成本項目可解釋幅度。</p>
      </div>
      <span class="sra-badge waiting" id="sraBadge">載入規則中</span>
    </div>

    <div class="sra-status" id="sraStatus">正在載入正式判讀規則…</div>

    <div class="sra-metrics">
      <div class="sra-metric"><span>供應商要求</span><b id="sraSupplierAsk">—</b><small>使用者輸入</small></div>
      <div class="sra-metric"><span>Should-Cost推估</span><b id="sraEstimate">—</b><small>全部成本線性加總</small></div>
      <div class="sra-metric"><span>材料行情可解釋</span><b id="sraMaterialImpact">—</b><small>材料占比 × 材料變化率</small></div>
      <div class="sra-metric"><span>其他輸入成本可解釋</span><b id="sraOtherImpact">—</b><small>匯率＋加工＋能源＋其他</small></div>
      <div class="sra-metric emphasis"><span id="sraGapLabel">未由目前輸入解釋</span><b id="sraGap">—</b><small id="sraGapAmount">每單位價差 —</small></div>
    </div>

    <div class="sra-verdict" id="sraVerdict">
      <div class="sra-verdict-title" id="sraVerdictTitle">等待有效試算結果</div>
      <p id="sraVerdictText">完成Should-Cost欄位後，系統會顯示建議採購動作。</p>
      <ul id="sraEvidence"></ul>
    </div>

    <div class="sra-market-context" id="sraMarketContext">目前原材料變化率尚未綁定訊號卡來源。</div>
    <div class="sra-policy-note">本模組不自動接受或拒絕供應商調價；原材料漲幅不等於成品漲幅，仍須核對材料占比、實際採購月份、庫存、匯率、運費、加工、能源、合約公式與毛利變動。</div>`;
  formulaBox.insertAdjacentElement('beforebegin',panel);
}

function parseField(id){
  const element=document.getElementById(id);
  if(!element)return {valid:false,value:0};
  const raw=String(element.value??'').trim();
  if(raw==='')return {valid:true,value:0};
  const value=Number(raw);
  return {valid:Number.isFinite(value),value:Number.isFinite(value)?value:0};
}

function calculate(){
  const values={};
  for(const id of FIELD_IDS){
    const parsed=parseField(id);
    if(!parsed.valid)return {valid:false,reason:'欄位含無效數字'};
    values[id]=parsed.value;
  }
  if(values.f_price<0)return {valid:false,reason:'產品目前單價不得小於0'};
  if(RATIO_IDS.some(id=>values[id]<0||values[id]>100)){
    return {valid:false,reason:'成本占比必須介於0%至100%'};
  }
  if(RATE_IDS.some(id=>values[id]<-100)){
    return {valid:false,reason:'變化率不得低於-100%'};
  }
  const baseRatioSum=values.f_matRatio+values.f_procRatio+values.f_energyRatio+values.f_otherRatio;
  if(baseRatioSum>100)return {valid:false,reason:'基本成本占比合計超過100%'};

  const materialImpactPercentagePoints=(values.f_matRatio*values.f_matRate)/100;
  const fxImpactPercentagePoints=(values.f_fxRatio*values.f_fxRate)/100;
  const processImpactPercentagePoints=(values.f_procRatio*values.f_procRate)/100;
  const energyImpactPercentagePoints=(values.f_energyRatio*values.f_energyRate)/100;
  const otherImpactPercentagePoints=(values.f_otherRatio*values.f_otherRate)/100;
  const estimatedPercentagePoints=
    materialImpactPercentagePoints+
    fxImpactPercentagePoints+
    processImpactPercentagePoints+
    energyImpactPercentagePoints+
    otherImpactPercentagePoints;
  const otherInputImpactPercentagePoints=estimatedPercentagePoints-materialImpactPercentagePoints;
  const supplierAsk=values.f_supplierAsk;
  const gapPercentagePoints=supplierAsk-estimatedPercentagePoints;
  const estimatedNewPrice=values.f_price*(1+estimatedPercentagePoints/100);
  const supplierRequestedPrice=values.f_price*(1+supplierAsk/100);
  const unexplainedAmountPerUnit=values.f_price*gapPercentagePoints/100;

  const computed=[
    materialImpactPercentagePoints,otherInputImpactPercentagePoints,
    estimatedPercentagePoints,gapPercentagePoints,estimatedNewPrice,
    supplierRequestedPrice,unexplainedAmountPerUnit
  ];
  if(!computed.every(Number.isFinite)||estimatedNewPrice<0||supplierRequestedPrice<0){
    return {valid:false,reason:'計算結果超出可處理範圍'};
  }
  return {
    valid:true,
    values,
    materialImpactPercentagePoints,
    otherInputImpactPercentagePoints,
    estimatedPercentagePoints,
    supplierAsk,
    gapPercentagePoints,
    estimatedNewPrice,
    supplierRequestedPrice,
    unexplainedAmountPerUnit
  };
}

function validateRules(payload){
  const thresholds=payload?.thresholds;
  const policy=payload?.policy;
  const match=Number(thresholds?.modelMatchTolerancePercentagePoints);
  const evidence=Number(thresholds?.requestEvidenceGapPercentagePoints);
  const challenge=Number(thresholds?.highChallengeGapPercentagePoints);
  if(payload?.schemaVersion!==1||payload?.isDecisionSupportOnly!==true){
    throw Error('判讀規則版本或用途標示不正確');
  }
  if(![match,evidence,challenge].every(Number.isFinite)||match<0||!(match<evidence&&evidence<challenge)){
    throw Error('判讀門檻設定不正確');
  }
  if(
    policy?.automaticAcceptance!==false||
    policy?.automaticRejection!==false||
    policy?.rawMaterialChangeEqualsFinishedPriceChange!==false||
    policy?.fredRole!=='CORROBORATION_ONLY'
  )throw Error('判讀政策不符合採購安全規則');
}

function recommendation(analysis){
  const thresholds=rules.thresholds;
  const match=Number(thresholds.modelMatchTolerancePercentagePoints);
  const evidence=Number(thresholds.requestEvidenceGapPercentagePoints);
  const challenge=Number(thresholds.highChallengeGapPercentagePoints);
  const gap=analysis.gapPercentagePoints;
  const estimate=analysis.estimatedPercentagePoints;
  const ask=analysis.supplierAsk;
  const material=analysis.materialImpactPercentagePoints;

  if(estimate<-match&&ask>=0){
    return {
      code:'REQUEST_REDUCTION',tone:'high',label:'要求降價／挑戰漲價',
      title:'目前模型不支持供應商漲價，應啟動降價檢討',
      text:'Should-Cost推估為下降或持平，但供應商未提出相對應降幅。先否決以市場上漲為由的調價，並要求供應商說明成本未下降的具體原因。',
      evidence:[
        `Should-Cost推估 ${signedPercent(estimate)}，供應商要求 ${signedPercent(ask)}`,
        `未由目前輸入解釋 ${signedPoints(gap)}`,
        '要求提供實際採購月份、庫存週期與價格回落的對稱調整機制'
      ]
    };
  }
  if(estimate<-match&&ask<0&&gap>match){
    const tone=gap>=challenge?'high':'medium';
    return {
      code:'REQUEST_DEEPER_REDUCTION',tone,label:'要求擴大降價幅度',
      title:'供應商已降價，但降幅仍小於Should-Cost推估',
      text:'目前方向正確，但供應商提供的降幅尚未完整反映模型中的成本下降。要求說明未傳導部分，並進一步協商降價。',
      evidence:[
        `Should-Cost推估 ${signedPercent(estimate)}，供應商條件 ${signedPercent(ask)}`,
        `尚未反映的降價幅度 ${signedPoints(gap)}`,
        '確認舊庫存消化時間與降價生效日，避免無限期延後傳導'
      ]
    };
  }
  if(material<-match&&ask>0&&gap>match){
    return {
      code:'CHALLENGE_INCREASE',tone:'high',label:'材料行情不支持漲價',
      title:'材料行情為下降，漲價必須由其他成本逐項證明',
      text:'原材料市場方向與供應商漲價要求相反。即使其他成本推高整體模型，也不能把漲幅歸因於原材料。',
      evidence:[
        `材料行情可解釋幅度 ${signedPoints(material)}`,
        `其他輸入成本可解釋幅度 ${signedPoints(analysis.otherInputImpactPercentagePoints)}`,
        `供應商要求高於整體模型 ${signedPoints(Math.max(0,gap))}`
      ]
    };
  }
  if(gap>=challenge){
    return {
      code:'HIGH_CHALLENGE',tone:'high',label:'高優先挑戰漲價',
      title:'供應商要求明顯高於Should-Cost推估',
      text:'超出模型的部分不可由目前成本輸入解釋。要求供應商提出成本結構、發票或採購月份等證據，否則不接受超額部分。',
      evidence:[
        `供應商要求高於模型 ${signedPoints(gap)}`,
        `材料行情僅解釋 ${signedPoints(analysis.materialImpactPercentagePoints)}`,
        `每單位未解釋價差 ${signed(analysis.unexplainedAmountPerUnit)} 元`
      ]
    };
  }
  if(gap>=evidence){
    return {
      code:'REQUEST_EVIDENCE',tone:'medium',label:'要求補充成本證據',
      title:'供應商要求高於模型，需補充資料後再議',
      text:'差距已超過一般容許區間。先要求材料占比、採購落後期、匯率、運費、加工與能源變動的明細，再決定可接受幅度。',
      evidence:[
        `供應商要求高於模型 ${signedPoints(gap)}`,
        `模型可解釋總幅度 ${signedPercent(analysis.estimatedPercentagePoints)}`,
        '建議採分段調整、暫時附加費或價格重議條款，避免永久性一次調足'
      ]
    };
  }
  if(gap>match){
    return {
      code:'CLARIFY_SMALL_GAP',tone:'medium',label:'小幅差距需說明',
      title:'供應商要求略高於模型',
      text:'差距不大，但仍不應直接視為合理。要求供應商針對超出部分提供簡要說明，並保留市場回落時的對稱調降條款。',
      evidence:[
        `差距 ${signedPoints(gap)}`,
        `模型推估 ${signedPercent(estimate)}`,
        '確認報價有效期、採購期間與價格重議觸發點'
      ]
    };
  }
  if(Math.abs(gap)<=match){
    return {
      code:'CONDITIONAL_REVIEW',tone:'balanced',label:'接近模型區間',
      title:'供應商要求與Should-Cost推估接近',
      text:'數值接近不代表自動接受。仍須核實成本占比與實際採購時點，再以條件式調整或對稱條款完成議價。',
      evidence:[
        `差距落在 ±${number(match)} 個百分點內`,
        `材料行情可解釋 ${signedPoints(analysis.materialImpactPercentagePoints)}`,
        '建議確認降價回溯、價格公式與下一次重議日期'
      ]
    };
  }
  return {
    code:'SUPPLIER_BELOW_MODEL',tone:'favorable',label:'供應商要求低於模型',
    title:'供應商要求未高於Should-Cost推估',
    text:'目前條件相對模型較有利，不需主動提高價格；仍應確認品質、交期與調價條款，避免日後以其他名目補回差額。',
    evidence:[
      `供應商要求低於模型 ${signedPoints(Math.abs(gap))}`,
      `Should-Cost推估 ${signedPercent(estimate)}`,
      '保留市場回落時的降價機制與書面價格基準'
    ]
  };
}

function marketContextText(){
  const context=$('#calcMarketContext');
  if(!context||!context.classList.contains('show')){
    return '目前原材料變化率為手動輸入，尚未綁定材料訊號卡與World Bank資料月份。';
  }
  const material=$('#calcMarketContextMaterial')?.textContent?.trim()||'—';
  const windowLabel=$('#calcMarketContextWindow')?.textContent?.trim()||'—';
  const value=$('#calcMarketContextValue')?.textContent?.trim()||'—';
  const periodLabel=$('#calcMarketContextPeriod')?.textContent?.trim()||'—';
  if(context.classList.contains('manually-changed')){
    return `市場資料紀錄：${material}｜${windowLabel}｜原帶入值 ${value}｜${periodLabel}；目前原材料變化率已手動修改。`;
  }
  return `市場資料：${material}｜${windowLabel}｜${value}｜World Bank Pink Sheet｜${periodLabel}；FRED僅作交叉核對。`;
}

function setMetric(id,value,classValue){
  const element=$(id);
  if(!element)return;
  element.textContent=value;
  element.className=classValue?direction(classValue):'';
}

function renderInvalid(reason){
  $('#sraBadge').className='sra-badge waiting';
  $('#sraBadge').textContent='等待有效輸入';
  $('#sraStatus').textContent=`合理性分析暫停：${reason}`;
  ['#sraSupplierAsk','#sraEstimate','#sraMaterialImpact','#sraOtherImpact','#sraGap'].forEach(id=>setMetric(id,'—'));
  $('#sraGapAmount').textContent='每單位價差 —';
  $('#sraVerdict').className='sra-verdict waiting';
  $('#sraVerdictTitle').textContent='等待有效Should-Cost結果';
  $('#sraVerdictText').textContent='修正欄位後，系統會自動重新判讀。';
  $('#sraEvidence').innerHTML='';
  $('#sraMarketContext').textContent=marketContextText();
}

function render(){
  if(!rules)return;
  const analysis=calculate();
  if(!analysis.valid){renderInvalid(analysis.reason);return;}
  const decision=recommendation(analysis);
  $('#sraBadge').className=`sra-badge ${decision.tone}`;
  $('#sraBadge').textContent=decision.label;
  $('#sraStatus').textContent=`判讀規則：接近模型 ±${number(rules.thresholds.modelMatchTolerancePercentagePoints)} 個百分點；補證門檻 ${number(rules.thresholds.requestEvidenceGapPercentagePoints)}；高優先挑戰 ${number(rules.thresholds.highChallengeGapPercentagePoints)}。`;
  setMetric('#sraSupplierAsk',signedPercent(analysis.supplierAsk),analysis.supplierAsk);
  setMetric('#sraEstimate',signedPercent(analysis.estimatedPercentagePoints),analysis.estimatedPercentagePoints);
  setMetric('#sraMaterialImpact',signedPoints(analysis.materialImpactPercentagePoints),analysis.materialImpactPercentagePoints);
  setMetric('#sraOtherImpact',signedPoints(analysis.otherInputImpactPercentagePoints),analysis.otherInputImpactPercentagePoints);
  setMetric('#sraGap',signedPoints(analysis.gapPercentagePoints),analysis.gapPercentagePoints);
  $('#sraGapLabel').textContent=analysis.gapPercentagePoints>=0?'未由目前輸入解釋':'供應商低於模型幅度';
  $('#sraGapAmount').textContent=`每單位價差 ${signed(analysis.unexplainedAmountPerUnit)} 元`;
  $('#sraVerdict').className=`sra-verdict ${decision.tone}`;
  $('#sraVerdictTitle').textContent=decision.title;
  $('#sraVerdictText').textContent=decision.text;
  $('#sraEvidence').innerHTML='';
  decision.evidence.forEach(text=>{
    const item=document.createElement('li');
    item.textContent=text;
    $('#sraEvidence').appendChild(item);
  });
  $('#sraMarketContext').textContent=marketContextText();
}

function scheduleRender(){window.setTimeout(render,0);}

function setupListeners(){
  FIELD_IDS.forEach(id=>{
    const input=document.getElementById(id);
    if(input)input.addEventListener('input',scheduleRender);
  });
  const reset=$('#resetBtn');
  if(reset)reset.addEventListener('click',scheduleRender);
}

async function loadRules(){
  const response=await fetch(RULES_URL,{cache:'no-store',headers:{Accept:'application/json'}});
  if(!response.ok)throw Error(`判讀規則 HTTP ${response.status}`);
  const payload=await response.json();
  validateRules(payload);
  return payload;
}

async function init(){
  try{
    addPanel();
    setupListeners();
    rules=await loadRules();
    render();
  }catch(error){
    console.error(error);
    const panel=$('#supplierRationalityPanel');
    if(panel){
      $('#sraBadge').className='sra-badge high';
      $('#sraBadge').textContent='規則載入失敗';
      $('#sraStatus').textContent=String(error.message||error);
      $('#sraVerdictTitle').textContent='合理性分析暫停';
      $('#sraVerdictText').textContent='Should-Cost原有計算不受影響，請檢查部署的判讀規則檔。';
    }
  }
}

init();
})();
