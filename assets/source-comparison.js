(function(){
'use strict';

const ORDER=['zinc','copper','aluminium','nickel','iron_ore','crude_oil','natural_gas'];
const state={materialId:'copper',periodCount:13};
let comparison=null;
let fred=null;
let status=null;
let chart=null;
let currentRows=[];

const $=selector=>document.querySelector(selector);
const esc=value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const period=value=>/^\d{4}-\d{2}$/.test(String(value))?String(value).replace('-','/'):String(value||'—');
const finite=value=>Number.isFinite(Number(value));
const number=(value,decimals)=>{
  if(!finite(value))return '—';
  const n=Number(value);
  const digits=decimals??(Math.abs(n)<10?3:2);
  return n.toLocaleString('zh-Hant-TW',{minimumFractionDigits:digits,maximumFractionDigits:digits});
};
const signedNumber=(value,decimals)=>{
  if(!finite(value))return '—';
  const n=Number(value);
  return (n>0?'+':'')+number(n,decimals);
};
const signedPercent=value=>{
  if(!finite(value))return '—';
  const n=Number(value);
  return (n>0?'▲ +':n<0?'▼ ':'— ')+number(n,3)+'%';
};
const directionClass=value=>Number(value)>0.0005?'up':Number(value)<-0.0005?'down':'flat';
const taipei=iso=>{
  const date=new Date(iso);
  if(Number.isNaN(date.getTime()))return String(iso||'—');
  return new Intl.DateTimeFormat('zh-TW',{
    timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit',
    hour:'2-digit',minute:'2-digit',hour12:false
  }).format(date);
};

function addNavigation(){
  const side=$('.sidenav');
  const mobile=$('.mobile-tabs');
  if(side&&!side.querySelector('[data-target="comparison"]')){
    const button=document.createElement('button');
    button.type='button';
    button.dataset.target='comparison';
    button.setAttribute('aria-pressed','false');
    button.innerHTML='<span class="ic">⇄</span>資料源比較';
    const calc=side.querySelector('[data-target="calc"]');
    side.insertBefore(button,calc||null);
  }
  if(mobile&&!mobile.querySelector('[data-target="comparison"]')){
    const button=document.createElement('button');
    button.type='button';
    button.dataset.target='comparison';
    button.setAttribute('aria-pressed','false');
    button.textContent='⇄ 資料源比較';
    const calc=mobile.querySelector('[data-target="calc"]');
    mobile.insertBefore(button,calc||null);
  }
  document.querySelectorAll('[data-target="comparison"]').forEach(button=>{
    button.addEventListener('click',()=>activateTab('comparison'));
  });
}

function addSection(){
  if($('#comparison'))return;
  const section=document.createElement('section');
  section.className='section';
  section.id='comparison';
  section.innerHTML=`
    <h1 class="page-title">World Bank／FRED 資料源比較</h1>
    <p class="page-desc">以World Bank Pink Sheet作為平台主要價格來源，FRED僅用於同月份交叉核對；本模組不會自動替換主要資料。</p>

    <div class="src-policy" role="note">
      <div><span class="src-policy-label primary">PRIMARY</span><b>World Bank Pink Sheet</b><span>平台主要行情與採購分析依據</span></div>
      <div><span class="src-policy-label compare">COMPARE</span><b>FRED API</b><span>獨立研究與交叉比較來源</span></div>
      <div><span class="src-policy-label no-fallback">NO FALLBACK</span><b>不自動備援</b><span>FRED異常時保留上一版，不靜默取代World Bank</span></div>
    </div>

    <div class="panel src-controls">
      <h2>比較設定</h2>
      <div class="src-control-row">
        <div class="field-block src-material-field">
          <label for="srcMaterialSelect">材料</label>
          <select id="srcMaterialSelect" disabled aria-describedby="srcDataStatus"></select>
        </div>
        <div class="field-block">
          <label id="srcPeriodLabel">觀察期間</label>
          <div class="seg" id="srcPeriodSeg" role="group" aria-labelledby="srcPeriodLabel">
            <button type="button" data-count="13" class="active" aria-pressed="true">1Y</button>
            <button type="button" data-count="37" aria-pressed="false">3Y</button>
            <button type="button" data-count="61" aria-pressed="false">5Y</button>
          </div>
        </div>
        <div class="src-actions">
          <button class="btn primary" type="button" id="srcExportBtn" disabled>⭳ 匯出比較CSV</button>
        </div>
      </div>
      <div class="small-note" id="srcDataStatus" aria-live="polite">正在載入World Bank／FRED正式比較資料…</div>
    </div>

    <div class="src-summary-grid" id="srcSummaryGrid" aria-live="polite">
      <div class="src-summary-card loading">比較資料載入中…</div>
    </div>

    <div class="panel">
      <div class="src-panel-heading">
        <div>
          <h2 id="srcChartTitle">雙來源月度價格走勢</h2>
          <div class="small-note" id="srcChartSubtitle"></div>
        </div>
      </div>
      <div class="src-warning" id="srcWarning" role="alert"></div>
      <div class="src-chart-wrap" id="srcChartWrap">
        <canvas id="srcCompareChart" role="img" aria-label="World Bank與FRED同月份價格比較圖"></canvas>
        <div class="chart-fallback" id="srcChartFallback">比較圖表載入中…</div>
        <div id="srcChartTooltip"></div>
      </div>
      <div class="legend-wrap" id="srcLegend"></div>
    </div>

    <div class="panel">
      <div class="src-panel-heading">
        <div>
          <h2>最近12個月比較明細</h2>
          <div class="small-note">價差定義：FRED − World Bank；百分比以World Bank為基準。</div>
        </div>
      </div>
      <div class="src-table-wrap">
        <table class="src-table">
          <thead>
            <tr>
              <th>月份</th>
              <th>World Bank</th>
              <th>FRED</th>
              <th>價差</th>
              <th>價差%</th>
            </tr>
          </thead>
          <tbody id="srcTableBody"><tr><td colspan="5">資料載入中…</td></tr></tbody>
        </table>
      </div>
      <div class="src-footnote" id="srcFootnote"></div>
    </div>`;
  const main=$('.main');
  const calc=$('#calc');
  main.insertBefore(section,calc||null);
}

function activateTab(target){
  document.querySelectorAll('.section').forEach(section=>section.classList.remove('active'));
  const section=document.getElementById(target);
  if(section)section.classList.add('active');
  document.querySelectorAll('.sidenav button,.mobile-tabs button').forEach(button=>{
    const active=button.dataset.target===target;
    button.classList.toggle('active',active);
    button.setAttribute('aria-pressed',active?'true':'false');
  });
  if(target==='comparison'&&chart){
    setTimeout(()=>{try{chart.resize();}catch(error){}},80);
  }
}

function validatePayloads(){
  if(comparison?.isRealData!==true||comparison?.primarySource!=='WORLD_BANK_PINK_SHEET'||comparison?.comparisonSource!=='FRED'){
    throw new Error('comparison.json不是正式World Bank／FRED比較資料');
  }
  if(comparison?.sourcePolicy?.worldBankRole!=='PRIMARY'||comparison?.sourcePolicy?.fredRole!=='INDEPENDENT_COMPARISON_ONLY'||comparison?.sourcePolicy?.automaticFallback!==false){
    throw new Error('比較資料來源政策不符合平台規則');
  }
  if(fred?.source!=='FRED'||fred?.isRealData!==true||fred?.role!=='INDEPENDENT_COMPARISON_ONLY'){
    throw new Error('fred.json不是正式獨立比較資料');
  }
  if(fred?.dataset?.downloadMethod!=='FRED_API_JSON'||fred?.dataset?.apiKeyRequired!==true||fred?.dataset?.seriesCount!==7){
    throw new Error('FRED資料不是API JSON正式同步結果');
  }
  if(status?.dataMode!=='WORLD_BANK_PRIMARY'||status?.worldBank?.status!=='SUCCESS'||status?.fred?.status!=='SUCCESS'){
    throw new Error('status.json未顯示World Bank與FRED同步成功');
  }
  ORDER.forEach(id=>{
    const material=comparison?.materials?.[id];
    if(!material||material.id!==id||typeof material.comparisonAvailable!=='boolean'){
      throw new Error(`${id}比較資料不完整`);
    }
    if(material.comparisonAvailable){
      if(!Array.isArray(material.observations)||material.observations.length<12){
        throw new Error(`${id}可比較月份不足`);
      }
      const valid=material.observations.every(row=>/^\d{4}-\d{2}$/.test(String(row.period))&&finite(row.worldBankValue)&&finite(row.fredValue)&&finite(row.differenceFredMinusWorldBank)&&finite(row.differencePercentVsWorldBank));
      if(!valid)throw new Error(`${id}含無效比較資料`);
    }
  });
}

function populateControls(){
  const select=$('#srcMaterialSelect');
  select.innerHTML='';
  ORDER.forEach(id=>{
    const material=comparison.materials[id];
    const option=document.createElement('option');
    option.value=id;
    option.textContent=`${material.nameZh} ${material.nameEn}${material.comparisonAvailable?'':'（單位待確認）'}`;
    select.appendChild(option);
  });
  select.value=state.materialId;
  select.disabled=false;
  select.addEventListener('change',()=>{
    state.materialId=select.value;
    render();
  });
  $('#srcPeriodSeg').addEventListener('click',event=>{
    const button=event.target.closest('button[data-count]');
    if(!button)return;
    state.periodCount=Number(button.dataset.count);
    $('#srcPeriodSeg').querySelectorAll('button').forEach(item=>{
      const active=item===button;
      item.classList.toggle('active',active);
      item.setAttribute('aria-pressed',active?'true':'false');
    });
    renderChart(comparison.materials[state.materialId]);
  });
  $('#srcExportBtn').addEventListener('click',exportCsv);
}

function summaryCard(label,value,detail,className=''){
  return `<div class="src-summary-card ${className}"><div class="label">${esc(label)}</div><div class="value">${value}</div><div class="detail">${detail}</div></div>`;
}

function renderSummary(material){
  const comparable=material.comparisonAvailable;
  const latestPeriod=period(material.latestOverlapPeriod);
  const wbUnit=`USD／${material.worldBank.displayUnit}`;
  const fredUnit=material.fred.sourceUnits;
  let html='';
  html+=summaryCard('World Bank主要價格',number(material.latestWorldBankValue),`${esc(wbUnit)}｜${latestPeriod}<br>原始單位：${esc(material.worldBank.sourceUnit)}`,'primary');
  html+=summaryCard('FRED比較價格',number(material.latestFredValue),`${esc(fredUnit)}｜${latestPeriod}<br>Series：${esc(material.fred.seriesId)}`,'compare');
  if(comparable){
    const diff=Number(material.latestFredValue)-Number(material.latestWorldBankValue);
    html+=summaryCard('最新價差（FRED−WB）',signedNumber(diff),`同月份差異：<span class="${directionClass(material.latestDifferencePercentVsWorldBank)}">${signedPercent(material.latestDifferencePercentVsWorldBank)}</span>`);
    html+=summaryCard('近12月平均絕對差異',`${number(material.recent12MonthMeanAbsoluteDifferencePercent,3)}%`,`最大絕對差異：${number(material.recent12MonthMaxAbsoluteDifferencePercent,3)}%`);
  }else{
    html+=summaryCard('價差計算','暫停',esc(material.comparisonReason||'單位相容性尚未確認'),'warning');
    html+=summaryCard('資料處理原則','不換算','保留兩來源原始數值，避免錯誤比較','warning');
  }
  $('#srcSummaryGrid').innerHTML=html;
}

function renderWarning(material){
  const box=$('#srcWarning');
  if(material.comparisonAvailable){
    box.classList.remove('show');
    box.textContent='';
    return;
  }
  box.classList.add('show');
  box.innerHTML=`<b>此材料暫不計算價差：</b>${esc(material.comparisonReason||'兩來源單位未通過相容性驗證。')}`;
}

function externalTooltip(context){
  const tooltip=context.tooltip;
  const element=$('#srcChartTooltip');
  if(!tooltip||tooltip.opacity===0){element.style.opacity=0;return;}
  const points=Array.isArray(tooltip.dataPoints)?tooltip.dataPoints:[];
  const index=points[0]?.dataIndex;
  if(index===undefined){element.style.opacity=0;return;}
  const rows=points.map(point=>{
    const dataset=context.chart.data.datasets[point.datasetIndex];
    return `<div class="src-tooltip-row"><span class="src-tooltip-dot" style="background:${dataset.borderColor}"></span><b>${esc(dataset.label)}</b>：${number(point.raw)}</div>`;
  }).join('');
  element.innerHTML=`<div class="src-tooltip-period">${esc(context.chart.data.labels[index])}</div>${rows}`;
  const parent=context.chart.canvas.parentElement;
  let left=tooltip.caretX+12;
  if(left+230>parent.getBoundingClientRect().width)left=tooltip.caretX-240;
  element.style.left=`${left}px`;
  element.style.top=`${Math.max(8,tooltip.caretY-18)}px`;
  element.style.opacity=1;
}

function renderChart(material){
  const canvas=$('#srcCompareChart');
  const fallback=$('#srcChartFallback');
  const wrap=$('#srcChartWrap');
  $('#srcChartTitle').textContent=`${material.nameZh}（${material.nameEn}）雙來源月度價格`;
  $('#srcChartSubtitle').textContent=`World Bank：${material.worldBank.sourceUnit}｜FRED：${material.fred.sourceUnits}`;
  if(chart){try{chart.destroy();}catch(error){}chart=null;}
  if(!material.comparisonAvailable){
    canvas.style.display='none';
    wrap.classList.add('unavailable');
    fallback.textContent='因兩來源單位尚未完成正式換算規則確認，本材料不繪製價差比較圖。';
    fallback.classList.add('show');
    $('#srcLegend').innerHTML='';
    return;
  }
  wrap.classList.remove('unavailable');
  fallback.classList.remove('show');
  canvas.style.display='block';
  const rows=material.observations.slice(-state.periodCount);
  const labels=rows.map(row=>period(row.period));
  const datasets=[
    {label:'World Bank Pink Sheet（PRIMARY）',data:rows.map(row=>Number(row.worldBankValue)),borderColor:'#0B5769',backgroundColor:'#0B576922',borderWidth:2.4,pointRadius:0,pointHitRadius:8,tension:.15,fill:false},
    {label:`FRED ${material.fred.seriesId}（COMPARE）`,data:rows.map(row=>Number(row.fredValue)),borderColor:'#B3392C',backgroundColor:'#B3392C22',borderWidth:2,pointRadius:0,pointHitRadius:8,tension:.15,fill:false}
  ];
  $('#srcLegend').innerHTML=datasets.map(dataset=>`<div class="legend-item"><span class="legend-dot" style="background:${dataset.borderColor}"></span>${esc(dataset.label)}</div>`).join('');
  try{
    chart=new Chart(canvas.getContext('2d'),{
      type:'line',
      data:{labels,datasets},
      options:{
        responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
        plugins:{legend:{display:false},tooltip:{enabled:false,external:externalTooltip}},
        scales:{
          x:{grid:{color:'#EEF1F4'},ticks:{color:'#8695A6',font:{size:10.5},maxRotation:0,autoSkip:true,maxTicksLimit:10}},
          y:{grid:{color:'#EEF1F4'},ticks:{color:'#8695A6',font:{size:10.5}},title:{display:true,text:`USD／${material.worldBank.displayUnit}`,color:'#8695A6',font:{size:10.5}}}
        }
      }
    });
  }catch(error){
    console.error(error);
    canvas.style.display='none';
    fallback.textContent='雙來源比較圖表無法呈現，請重新整理頁面。';
    fallback.classList.add('show');
  }
}

function renderTable(material){
  const body=$('#srcTableBody');
  const footnote=$('#srcFootnote');
  currentRows=[];
  if(!material.comparisonAvailable){
    body.innerHTML=`<tr><td colspan="5" class="src-empty">${esc(material.comparisonReason||'此材料目前無法直接比較。')}</td></tr>`;
    footnote.innerHTML=`World Bank最新值：<b>${number(material.latestWorldBankValue)}</b>（${esc(material.worldBank.sourceUnit)}）；FRED最新值：<b>${number(material.latestFredValue)}</b>（${esc(material.fred.sourceUnits)}）。`;
    $('#srcExportBtn').disabled=true;
    return;
  }
  currentRows=material.observations.slice(-12);
  body.innerHTML=currentRows.map(row=>{
    const cls=directionClass(row.differencePercentVsWorldBank);
    return `<tr><td>${period(row.period)}</td><td>${number(row.worldBankValue)}</td><td>${number(row.fredValue)}</td><td class="${cls}">${signedNumber(row.differenceFredMinusWorldBank)}</td><td class="${cls}">${signedPercent(row.differencePercentVsWorldBank)}</td></tr>`;
  }).join('');
  footnote.innerHTML=`重疊資料：<b>${number(material.overlapCount,0)}</b>個月（${period(material.firstOverlapPeriod)}～${period(material.latestOverlapPeriod)}）；FRED Series：<a href="https://fred.stlouisfed.org/series/${encodeURIComponent(material.fred.seriesId)}" target="_blank" rel="noopener noreferrer">${esc(material.fred.seriesId)}</a>。`;
  $('#srcExportBtn').disabled=false;
}

function render(){
  const material=comparison.materials[state.materialId];
  renderSummary(material);
  renderWarning(material);
  renderChart(material);
  renderTable(material);
}

const csvCell=value=>{
  let text=String(value??'');
  if(/^[=+\-@]/.test(text))text=`'${text}`;
  return `"${text.replace(/"/g,'""')}"`;
};

function exportCsv(){
  const material=comparison.materials[state.materialId];
  if(!material.comparisonAvailable||!currentRows.length)return;
  const header=['材料中文','材料英文','月份','World Bank數值','FRED數值','價差(FRED-WB)','價差百分比(相對WB)','World Bank單位','FRED單位','FRED Series','主要來源','比較來源','自動備援'];
  const rows=currentRows.map(row=>[
    material.nameZh,material.nameEn,row.period,row.worldBankValue,row.fredValue,
    row.differenceFredMinusWorldBank,row.differencePercentVsWorldBank,
    material.worldBank.sourceUnit,material.fred.sourceUnits,material.fred.seriesId,
    'World Bank Pink Sheet','FRED API','否'
  ]);
  const text='\uFEFF'+[header,...rows].map(row=>row.map(csvCell).join(',')).join('\n');
  const blob=new Blob([text],{type:'text/csv;charset=utf-8;'});
  const url=URL.createObjectURL(blob);
  const link=document.createElement('a');
  const today=new Date();
  link.href=url;
  link.download=`WB_FRED_${state.materialId}_${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function json(url){
  const response=await fetch(url,{cache:'no-store',headers:{Accept:'application/json'}});
  if(!response.ok)throw new Error(`${url} HTTP ${response.status}`);
  return response.json();
}

function readyCopy(){
  const latest=period(status.fred.latestCommonPeriod);
  const synced=taipei(status.fred.lastSuccessAt||comparison.generatedAt);
  $('#srcDataStatus').innerHTML=`FRED API與World Bank比較資料已驗證；共同最新月份 <b>${esc(latest)}</b>，FRED最後同步 <b>${esc(synced)}</b>。`;
  const sub=$('.brand .sub');
  if(sub)sub.textContent='International Raw Materials Procurement Analytics（World Bank＋FRED比較版 v1.4.0）';
  const footer=$('.appfoot');
  if(footer&&!footer.textContent.includes('比較來源：FRED API'))footer.textContent=footer.textContent.replace('｜© 2026','｜比較來源：FRED API｜© 2026');
}

function fail(error){
  console.error(error);
  $('#srcDataStatus').innerHTML=`<span class="up"><b>比較資料載入失敗：</b>${esc(error.message||error)}</span>`;
  $('#srcSummaryGrid').innerHTML='<div class="src-summary-card warning"><div class="label">資料源比較暫停</div><div class="value">未顯示</div><div class="detail">World Bank主行情不受影響；請檢查FRED同步Workflow。</div></div>';
  $('#srcMaterialSelect').disabled=true;
  $('#srcExportBtn').disabled=true;
  $('#srcCompareChart').style.display='none';
  $('#srcChartFallback').textContent='正式比較資料未通過驗證，因此不顯示圖表。';
  $('#srcChartFallback').classList.add('show');
  $('#srcTableBody').innerHTML='<tr><td colspan="5" class="src-empty">正式比較資料未載入。</td></tr>';
}

async function init(){
  addNavigation();
  addSection();
  try{
    [comparison,fred,status]=await Promise.all([
      json('./data/comparison.json'),
      json('./data/fred.json'),
      json('./data/status.json')
    ]);
    validatePayloads();
    populateControls();
    readyCopy();
    render();
  }catch(error){
    fail(error);
  }
}

init();
})();
