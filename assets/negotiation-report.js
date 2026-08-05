(function(){
'use strict';

const CALC_FIELDS=[
  'f_price','f_matRatio','f_matRate','f_fxRatio','f_fxRate',
  'f_procRatio','f_procRate','f_energyRatio','f_energyRate',
  'f_otherRatio','f_otherRate','f_supplierAsk'
];
const REPORT_FIELDS=[
  'nr_supplier','nr_product','nr_quote_ref','nr_quote_date','nr_buyer',
  'nr_price_unit','nr_supplier_reason','nr_evidence_received',
  'nr_conclusion','nr_target_rate','nr_note'
];
const COST_ROWS=[
  ['原材料','f_matRatio','f_matRate'],
  ['匯率曝險','f_fxRatio','f_fxRate'],
  ['加工成本','f_procRatio','f_procRate'],
  ['能源成本','f_energyRatio','f_energyRate'],
  ['其他成本','f_otherRatio','f_otherRate']
];

let latestSnapshot=null;
let renderTimer=null;

const $=selector=>document.querySelector(selector);
const text=(selector,fallback='—')=>{
  const value=$(selector)?.textContent?.trim();
  return value||fallback;
};
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

function addPanel(){
  if($('#negotiationReportPanel'))return;
  const resultPanel=$('#calc .result-panel');
  const rationality=$('#supplierRationalityPanel');
  const formulaBox=$('#formulaBox');
  if(!resultPanel||!formulaBox)throw Error('找不到Should-Cost結果區');

  const panel=document.createElement('section');
  panel.id='negotiationReportPanel';
  panel.className='nr-panel';
  panel.innerHTML=`
    <div class="nr-panel-head">
      <div>
        <div class="nr-eyebrow">3D-2C NEGOTIATION REPORT</div>
        <h3>議價分析摘要與匯出</h3>
        <p>整理供應商資料、Should-Cost結果、合理性判讀與採購結論，可匯出CSV或列印／另存PDF。</p>
      </div>
      <span class="nr-state" id="nrState">等待有效分析</span>
    </div>

    <div class="nr-form" id="nrForm">
      <div class="nr-fields">
        <label><span>供應商</span><input id="nr_supplier" type="text" maxlength="120" placeholder="例如 ABC Transformer Parts"></label>
        <label><span>產品／料號</span><input id="nr_product" type="text" maxlength="160" placeholder="例如 Radiator Type A／PN-001"></label>
        <label><span>報價單號</span><input id="nr_quote_ref" type="text" maxlength="80" placeholder="Quotation No."></label>
        <label><span>報價日期</span><input id="nr_quote_date" type="date"></label>
        <label><span>分析人員</span><input id="nr_buyer" type="text" maxlength="80" placeholder="採購承辦人"></label>
        <label><span>幣別／價格單位</span><input id="nr_price_unit" type="text" maxlength="40" placeholder="例如 USD／件；僅供報告標示"></label>
      </div>
      <div class="nr-textareas">
        <label><span>供應商漲價理由</span><textarea id="nr_supplier_reason" rows="3" maxlength="1000" placeholder="例如銅價、匯率、能源或運費上漲"></textarea></label>
        <label><span>已取得的成本證據</span><textarea id="nr_evidence_received" rows="3" maxlength="1000" placeholder="例如成本結構、發票、採購月份或價格公式"></textarea></label>
      </div>
      <div class="nr-conclusion-row">
        <label><span>採購議價結論</span><select id="nr_conclusion"><option>待供應商補充資料</option><option>高優先挑戰漲價</option><option>要求降價</option><option>要求擴大降價幅度</option><option>條件式接受部分調整</option><option>不接受本次調價</option><option>持續觀察／暫不調整</option><option>其他</option></select></label>
        <label><span>議價目標幅度（選填）</span><div class="nr-input-unit"><input id="nr_target_rate" type="number" step="0.01" min="-100" placeholder="例如 3.50"><span>%</span></div></label>
      </div>
      <label class="nr-note-field"><span>採購補充說明／談判紀錄</span><textarea id="nr_note" rows="4" maxlength="2000" placeholder="記錄供應商回覆、內部決議、適用日期與後續追蹤事項"></textarea></label>
      <div class="nr-privacy">上述文字只保留在目前瀏覽器頁面，不會寫回GitHub、FRED或其他外部服務。</div>
    </div>

    <div class="nr-actions">
      <button type="button" class="btn" id="nrRefresh">↻ 更新摘要</button>
      <button type="button" class="btn primary" id="nrExportCsv" disabled>⭳ 匯出議價CSV</button>
      <button type="button" class="btn primary" id="nrPrint" disabled>▣ 列印／另存PDF</button>
      <span id="nrStatus" role="status">完成有效Should-Cost分析後即可匯出。</span>
    </div>

    <article class="nr-report" id="nrReport" aria-live="polite">
      <header class="nr-report-header">
        <div>
          <div class="nr-report-kicker">INTERNATIONAL RAW MATERIALS PROCUREMENT ANALYTICS</div>
          <h2>供應商調價議價分析摘要</h2>
        </div>
        <div class="nr-report-time"><span>產生時間</span><b id="nrOutTime">—</b></div>
      </header>

      <section class="nr-report-section">
        <h3>一、案件基本資料</h3>
        <div class="nr-report-grid">
          <div><span>供應商</span><b id="nrOutSupplier">未填</b></div>
          <div><span>產品／料號</span><b id="nrOutProduct">未填</b></div>
          <div><span>報價單號</span><b id="nrOutQuoteRef">未填</b></div>
          <div><span>報價日期</span><b id="nrOutQuoteDate">未填</b></div>
          <div><span>分析人員</span><b id="nrOutBuyer">未填</b></div>
          <div><span>幣別／價格單位</span><b id="nrOutPriceUnit">沿用試算器單位</b></div>
        </div>
      </section>

      <section class="nr-report-section">
        <h3>二、市場資料與Should-Cost摘要</h3>
        <div class="nr-source" id="nrOutMarketContext">尚未綁定World Bank材料訊號卡資料。</div>
        <div class="nr-summary-grid">
          <div><span>產品目前單價</span><b id="nrOutCurrentPrice">—</b></div>
          <div><span>供應商要求</span><b id="nrOutSupplierAsk">—</b></div>
          <div><span>Should-Cost推估</span><b id="nrOutEstimate">—</b></div>
          <div><span>未解釋差距</span><b id="nrOutGap">—</b></div>
          <div><span>推估合理新價格</span><b id="nrOutEstimatedPrice">—</b></div>
          <div><span>供應商要求價格</span><b id="nrOutRequestedPrice">—</b></div>
        </div>
        <table class="nr-cost-table">
          <thead><tr><th>成本項目</th><th>成本／曝險占比</th><th>變化率</th><th>成品影響幅度</th></tr></thead>
          <tbody id="nrOutCostRows"></tbody>
        </table>
      </section>

      <section class="nr-report-section">
        <h3>三、合理性判讀與採購建議</h3>
        <div class="nr-verdict"><span id="nrOutVerdictLabel">等待有效分析</span><b id="nrOutVerdictTitle">—</b><p id="nrOutVerdictText">—</p></div>
        <ul class="nr-evidence" id="nrOutEvidence"></ul>
      </section>

      <section class="nr-report-section nr-two-column">
        <div><h3>四、供應商說明與證據</h3><dl><dt>供應商漲價理由</dt><dd id="nrOutSupplierReason">未填</dd><dt>已取得的成本證據</dt><dd id="nrOutEvidenceReceived">未填</dd></dl></div>
        <div><h3>五、採購議價結論</h3><dl><dt>議價結論</dt><dd id="nrOutConclusion">待供應商補充資料</dd><dt>議價目標幅度</dt><dd id="nrOutTargetRate">未填</dd><dt>補充說明／談判紀錄</dt><dd id="nrOutNote">未填</dd></dl></div>
      </section>

      <footer class="nr-report-footer">
        本摘要為決策支援文件，不會自動接受或拒絕供應商調價。原材料行情變化不等於成品價格變化；應再核對材料占比、實際採購月份、庫存、匯率、運費、加工、能源、合約公式及毛利變動。World Bank為主要市場輸入；FRED僅作交叉核對。
      </footer>
    </article>`;

  if(rationality)rationality.insertAdjacentElement('afterend',panel);
  else formulaBox.insertAdjacentElement('beforebegin',panel);
}

function readInput(id){return String(document.getElementById(id)?.value??'').trim();}
function readNumber(id){
  const raw=readInput(id);
  if(raw==='')return 0;
  const value=Number(raw);
  return Number.isFinite(value)?value:null;
}
function emptyLabel(value,fallback='未填'){return value||fallback;}
function localDateTime(){
  return new Intl.DateTimeFormat('zh-TW',{
    year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false
  }).format(new Date());
}
function dateStamp(){
  const date=new Date();
  return `${date.getFullYear()}${String(date.getMonth()+1).padStart(2,'0')}${String(date.getDate()).padStart(2,'0')}`;
}

function collect(){
  const values={};
  for(const id of CALC_FIELDS){
    const value=readNumber(id);
    if(value===null)return {valid:false,reason:`${id}含無效數字`};
    values[id]=value;
  }
  if(values.f_price<0)return {valid:false,reason:'產品目前單價不得小於0'};
  const ratioIds=['f_matRatio','f_fxRatio','f_procRatio','f_energyRatio','f_otherRatio'];
  if(ratioIds.some(id=>values[id]<0||values[id]>100))return {valid:false,reason:'成本占比須介於0%至100%'};
  const rateIds=['f_matRate','f_fxRate','f_procRate','f_energyRate','f_otherRate','f_supplierAsk'];
  if(rateIds.some(id=>values[id]<-100))return {valid:false,reason:'變化率不得低於-100%'};
  if(values.f_matRatio+values.f_procRatio+values.f_energyRatio+values.f_otherRatio>100){
    return {valid:false,reason:'基本成本占比合計超過100%'};
  }

  const impacts={
    material:values.f_matRatio*values.f_matRate/100,
    fx:values.f_fxRatio*values.f_fxRate/100,
    process:values.f_procRatio*values.f_procRate/100,
    energy:values.f_energyRatio*values.f_energyRate/100,
    other:values.f_otherRatio*values.f_otherRate/100
  };
  const estimate=Object.values(impacts).reduce((sum,value)=>sum+value,0);
  const gap=values.f_supplierAsk-estimate;
  const estimatedPrice=values.f_price*(1+estimate/100);
  const requestedPrice=values.f_price*(1+values.f_supplierAsk/100);
  const unexplainedAmount=values.f_price*gap/100;
  if(![...Object.values(impacts),estimate,gap,estimatedPrice,requestedPrice,unexplainedAmount].every(Number.isFinite)){
    return {valid:false,reason:'計算結果超出可處理範圍'};
  }

  const verdictLabel=text('#sraBadge','等待有效分析');
  const verdictTitle=text('#sraVerdictTitle');
  const verdictText=text('#sraVerdictText');
  const rationalityReady=text('#sraSupplierAsk')!=='—'&&!$('#sraBadge')?.classList.contains('waiting');
  const evidence=Array.from(document.querySelectorAll('#sraEvidence li')).map(item=>item.textContent.trim()).filter(Boolean);
  const targetRaw=readInput('nr_target_rate');
  const targetRate=targetRaw===''?null:Number(targetRaw);
  if(targetRaw!==''&&(!Number.isFinite(targetRate)||targetRate<-100)){
    return {valid:false,reason:'議價目標幅度必須是大於等於-100%的有效數字'};
  }

  return {
    valid:rationalityReady,
    reason:rationalityReady?'':'合理性判讀尚未完成',
    generatedAt:localDateTime(),
    meta:{
      supplier:readInput('nr_supplier'),product:readInput('nr_product'),
      quoteRef:readInput('nr_quote_ref'),quoteDate:readInput('nr_quote_date'),
      buyer:readInput('nr_buyer'),priceUnit:readInput('nr_price_unit'),
      supplierReason:readInput('nr_supplier_reason'),
      evidenceReceived:readInput('nr_evidence_received'),
      conclusion:readInput('nr_conclusion'),targetRate,note:readInput('nr_note')
    },
    values,impacts,estimate,gap,estimatedPrice,requestedPrice,unexplainedAmount,
    verdict:{label:verdictLabel,title:verdictTitle,text:verdictText,evidence},
    marketContext:text('#sraMarketContext','尚未綁定World Bank材料訊號卡資料。'),
    marketLinked:$('#calcMarketContext')?.classList.contains('show')===true,
    marketManuallyChanged:$('#calcMarketContext')?.classList.contains('manually-changed')===true
  };
}

function setText(id,value){const element=document.getElementById(id);if(element)element.textContent=value;}
function buildCostRows(snapshot){
  const body=$('#nrOutCostRows');
  body.innerHTML='';
  const impactMap={
    'f_matRatio':snapshot.impacts.material,'f_fxRatio':snapshot.impacts.fx,
    'f_procRatio':snapshot.impacts.process,'f_energyRatio':snapshot.impacts.energy,
    'f_otherRatio':snapshot.impacts.other
  };
  COST_ROWS.forEach(([label,ratioId,rateId])=>{
    const row=document.createElement('tr');
    [label,`${number(snapshot.values[ratioId])}%`,`${signedPercent(snapshot.values[rateId])}`,signedPoints(impactMap[ratioId])].forEach(value=>{
      const cell=document.createElement('td');cell.textContent=value;row.appendChild(cell);
    });
    body.appendChild(row);
  });
}
function renderEvidence(items){
  const list=$('#nrOutEvidence');list.innerHTML='';
  (items.length?items:['尚無合理性判讀證據。']).forEach(value=>{const item=document.createElement('li');item.textContent=value;list.appendChild(item);});
}
function render(snapshot){
  latestSnapshot=snapshot;
  const valid=snapshot.valid===true;
  $('#nrExportCsv').disabled=!valid;
  $('#nrPrint').disabled=!valid;
  $('#nrState').textContent=valid?'摘要可匯出':'等待有效分析';
  $('#nrState').className=`nr-state ${valid?'ready':'waiting'}`;
  $('#nrStatus').textContent=valid?'摘要已同步目前Should-Cost欄位與合理性判讀。':'暫停匯出：'+snapshot.reason;
  if(!snapshot.values)return;

  setText('nrOutTime',snapshot.generatedAt);
  setText('nrOutSupplier',emptyLabel(snapshot.meta.supplier));
  setText('nrOutProduct',emptyLabel(snapshot.meta.product));
  setText('nrOutQuoteRef',emptyLabel(snapshot.meta.quoteRef));
  setText('nrOutQuoteDate',emptyLabel(snapshot.meta.quoteDate));
  setText('nrOutBuyer',emptyLabel(snapshot.meta.buyer));
  setText('nrOutPriceUnit',emptyLabel(snapshot.meta.priceUnit,'沿用試算器單位'));
  setText('nrOutMarketContext',snapshot.marketContext);
  setText('nrOutCurrentPrice',`${number(snapshot.values.f_price)} ${emptyLabel(snapshot.meta.priceUnit,'試算器單位')}`);
  setText('nrOutSupplierAsk',signedPercent(snapshot.values.f_supplierAsk));
  setText('nrOutEstimate',signedPercent(snapshot.estimate));
  setText('nrOutGap',signedPoints(snapshot.gap));
  setText('nrOutEstimatedPrice',`${number(snapshot.estimatedPrice)} ${emptyLabel(snapshot.meta.priceUnit,'試算器單位')}`);
  setText('nrOutRequestedPrice',`${number(snapshot.requestedPrice)} ${emptyLabel(snapshot.meta.priceUnit,'試算器單位')}`);
  buildCostRows(snapshot);
  setText('nrOutVerdictLabel',snapshot.verdict.label);
  setText('nrOutVerdictTitle',snapshot.verdict.title);
  setText('nrOutVerdictText',snapshot.verdict.text);
  renderEvidence(snapshot.verdict.evidence);
  setText('nrOutSupplierReason',emptyLabel(snapshot.meta.supplierReason));
  setText('nrOutEvidenceReceived',emptyLabel(snapshot.meta.evidenceReceived));
  setText('nrOutConclusion',emptyLabel(snapshot.meta.conclusion,'待供應商補充資料'));
  setText('nrOutTargetRate',snapshot.meta.targetRate===null?'未填':signedPercent(snapshot.meta.targetRate));
  setText('nrOutNote',emptyLabel(snapshot.meta.note));
}

function update(){
  window.clearTimeout(renderTimer);
  renderTimer=window.setTimeout(()=>render(collect()),25);
}
function csvText(value,userProvided=false){
  let textValue=String(value??'');
  if(userProvided&&/^[=+\-@]/.test(textValue))textValue="'"+textValue;
  return `"${textValue.replace(/"/g,'""')}"`;
}
function csvNumber(value){return Number.isFinite(Number(value))?String(Number(value)):'';}
function buildCsv(snapshot){
  const unit=emptyLabel(snapshot.meta.priceUnit,'試算器單位');
  const rows=[
    ['區段','欄位','值','單位／備註'],
    ['文件','報告名稱','供應商調價議價分析摘要',''],
    ['文件','產生時間',snapshot.generatedAt,''],
    ['案件','供應商',snapshot.meta.supplier,''],
    ['案件','產品／料號',snapshot.meta.product,''],
    ['案件','報價單號',snapshot.meta.quoteRef,''],
    ['案件','報價日期',snapshot.meta.quoteDate,''],
    ['案件','分析人員',snapshot.meta.buyer,''],
    ['案件','幣別／價格單位',snapshot.meta.priceUnit,''],
    ['市場','資料來源紀錄',snapshot.marketContext,'World Bank主要；FRED交叉核對'],
    ['試算','產品目前單價',snapshot.values.f_price,unit],
    ['試算','供應商要求漲幅',snapshot.values.f_supplierAsk,'%'],
    ['試算','Should-Cost推估合理幅度',snapshot.estimate,'%'],
    ['試算','未解釋差距',snapshot.gap,'個百分點'],
    ['試算','每單位未解釋價差',snapshot.unexplainedAmount,unit],
    ['試算','推估合理新價格',snapshot.estimatedPrice,unit],
    ['試算','供應商要求價格',snapshot.requestedPrice,unit]
  ];
  COST_ROWS.forEach(([label,ratioId,rateId])=>{
    const impactKey=ratioId==='f_matRatio'?'material':ratioId==='f_fxRatio'?'fx':ratioId==='f_procRatio'?'process':ratioId==='f_energyRatio'?'energy':'other';
    rows.push(['成本結構',`${label}占比`,snapshot.values[ratioId],'%']);
    rows.push(['成本結構',`${label}變化率`,snapshot.values[rateId],'%']);
    rows.push(['成本結構',`${label}成品影響幅度`,snapshot.impacts[impactKey],'個百分點']);
  });
  rows.push(
    ['判讀','系統建議分類',snapshot.verdict.label,'決策支援'],
    ['判讀','系統建議標題',snapshot.verdict.title,''],
    ['判讀','系統建議說明',snapshot.verdict.text,''],
    ['判讀','判讀證據',snapshot.verdict.evidence.join('；'),''],
    ['供應商','漲價理由',snapshot.meta.supplierReason,''],
    ['供應商','已取得成本證據',snapshot.meta.evidenceReceived,''],
    ['採購','議價結論',snapshot.meta.conclusion,''],
    ['採購','議價目標幅度',snapshot.meta.targetRate===null?'':snapshot.meta.targetRate,'%'],
    ['採購','補充說明／談判紀錄',snapshot.meta.note,''],
    ['政策','自動接受／拒絕','否','原材料漲幅不等於成品漲幅']
  );
  const userRows=new Set(['供應商','產品／料號','報價單號','分析人員','幣別／價格單位','漲價理由','已取得成本證據','議價結論','補充說明／談判紀錄']);
  return '\uFEFF'+rows.map((row,index)=>row.map((value,column)=>{
    if(index===0)return csvText(value);
    const numeric=typeof value==='number';
    return numeric?csvNumber(value):csvText(value,userRows.has(row[1])&&column===2);
  }).join(',')).join('\r\n');
}
function filenamePart(value,fallback){
  const cleaned=String(value||'').trim().replace(/[\\/:*?"<>|]+/g,'_').replace(/\s+/g,'_').slice(0,40);
  return cleaned||fallback;
}
function exportCsv(){
  const snapshot=collect();render(snapshot);if(!snapshot.valid)return;
  const blob=new Blob([buildCsv(snapshot)],{type:'text/csv;charset=utf-8;'});
  const url=URL.createObjectURL(blob);
  const anchor=document.createElement('a');
  anchor.href=url;
  anchor.download=`議價分析_${filenamePart(snapshot.meta.supplier,'供應商未填')}_${filenamePart(snapshot.meta.product,'產品未填')}_${dateStamp()}.csv`;
  document.body.appendChild(anchor);anchor.click();anchor.remove();URL.revokeObjectURL(url);
  $('#nrStatus').textContent='議價分析CSV已產生。';
}
function endPrintMode(){document.body.classList.remove('negotiation-report-printing');}
function printReport(){
  const snapshot=collect();render(snapshot);if(!snapshot.valid)return;
  document.body.classList.add('negotiation-report-printing');
  $('#nrStatus').textContent='已開啟列印視窗；可在目的地選擇「另存為PDF」。';
  window.requestAnimationFrame(()=>window.print());
  window.setTimeout(endPrintMode,30000);
}
function setup(){
  [...CALC_FIELDS,...REPORT_FIELDS].forEach(id=>document.getElementById(id)?.addEventListener('input',update));
  $('#nr_conclusion')?.addEventListener('change',update);
  $('#nrRefresh')?.addEventListener('click',()=>render(collect()));
  $('#nrExportCsv')?.addEventListener('click',exportCsv);
  $('#nrPrint')?.addEventListener('click',printReport);
  window.addEventListener('afterprint',endPrintMode);
  const observer=new MutationObserver(update);
  const rationality=$('#supplierRationalityPanel');
  if(rationality)observer.observe(rationality,{subtree:true,childList:true,characterData:true,attributes:true});
}
function init(){
  try{addPanel();setup();render(collect());}
  catch(error){console.error(error);}
}

init();
})();
