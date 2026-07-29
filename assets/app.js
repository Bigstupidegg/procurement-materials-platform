(function(){
'use strict';

/* ========================================================================
   資料模擬引擎（Seeded Random Walk） — 全部標示為示範資料
   ======================================================================== */
function mulberry32(seed){
  return function(){
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const MATERIALS = [
  { id:'zinc',    zh:'鋅',     en:'Zinc',        ccy:'USD', unit:'公噸(MT)', base:2850,  vol:0.045, drift:0.0006, seed:101, source:'示範資料（模擬倫敦金屬交易所LME格式）' },
  { id:'copper',  zh:'銅',     en:'Copper',      ccy:'USD', unit:'公噸(MT)', base:9200,  vol:0.040, drift:0.0010, seed:202, source:'示範資料（模擬倫敦金屬交易所LME格式）' },
  { id:'alu',     zh:'鋁',     en:'Aluminium',   ccy:'USD', unit:'公噸(MT)', base:2450,  vol:0.035, drift:0.0003, seed:303, source:'示範資料（模擬倫敦金屬交易所LME格式）' },
  { id:'nickel',  zh:'鎳',     en:'Nickel',      ccy:'USD', unit:'公噸(MT)', base:16800, vol:0.055, drift:-0.0012,seed:404, source:'示範資料（模擬倫敦金屬交易所LME格式）' },
  { id:'iron',    zh:'鐵礦砂', en:'Iron Ore',    ccy:'USD', unit:'公噸(MT)', base:105,   vol:0.050, drift:-0.0004,seed:505, source:'示範資料（模擬62%鐵含量到岸價格式）' },
  { id:'oil',     zh:'原油',   en:'Crude Oil',   ccy:'USD', unit:'桶(bbl)',  base:82,    vol:0.038, drift:0.0002, seed:606, source:'示範資料（模擬布蘭特原油Brent格式）' },
  { id:'gas',     zh:'天然氣', en:'Natural Gas', ccy:'USD', unit:'MMBtu',    base:2.85,  vol:0.070, drift:0.0008, seed:707, source:'示範資料（模擬Henry Hub天然氣格式）' },
];

const POINTS = 61; // 最近61筆月資料（至少61筆，涵蓋5Y期間含起點）
const TODAY = new Date(2026,6,28); // 2026-07-28

function genSeries(mat){
  const rng = mulberry32(mat.seed);
  const points = [];
  for(let i=POINTS-1;i>=0;i--){
    const d = new Date(TODAY.getFullYear(), TODAY.getMonth()-i, 28);
    points.push({ date:d, price:null });
  }
  let price = mat.base * 0.78;
  for(let idx=0; idx<points.length; idx++){
    const shock = (rng()-0.5) * 2 * mat.vol;
    price = Math.max(price * (1 + mat.drift + shock), mat.base*0.25);
    points[idx].price = price;
  }
  const lastFactor = mat.base / points[points.length-1].price;
  const blend = 0.9;
  for(let idx=0; idx<points.length; idx++){
    const w = idx/(points.length-1);
    const f = 1 + (lastFactor-1)*blend*w;
    points[idx].price = points[idx].price * f;
  }
  return points;
}

MATERIALS.forEach(m=>{ m.series = genSeries(m); });

function fmtDate(d){
  return d.getFullYear()+'/'+String(d.getMonth()+1).padStart(2,'0');
}
function fmtNum(n, decimals){
  if(n===null||n===undefined||!isFinite(n)) return '—';
  const dec = decimals!==undefined? decimals : (Math.abs(n)<10?2:0);
  return n.toLocaleString('zh-Hant-TW',{minimumFractionDigits:dec, maximumFractionDigits:dec});
}
function pctChange(cur, prev){
  if(prev===0||prev===undefined||prev===null) return null;
  return (cur-prev)/prev*100;
}

/* 統一的正負號格式化：F-05
   正: ▲ +3.00%   負: ▼ -3.00%   零: — 0.00% */
function formatSigned(pct, decimals){
  const dec = decimals===undefined?2:decimals;
  if(pct===null||pct===undefined||!isFinite(pct)) return '—';
  const rounded = Number(pct.toFixed(dec));
  if(rounded>0) return '▲ +'+rounded.toFixed(dec)+'%';
  if(rounded<0) return '▼ '+rounded.toFixed(dec)+'%';
  return '— 0.00%';
}
function signColorClass(pct){
  if(pct===null||pct===undefined||!isFinite(pct)) return 'flat';
  if(pct>0.0005) return 'up';
  if(pct<-0.0005) return 'down';
  return 'flat';
}
function changeHTML(pct){
  const cls = signColorClass(pct);
  return '<span class="chg-val '+cls+'">'+formatSigned(pct)+'</span>';
}

/* R-04：安全轉義，供必須使用innerHTML之處對動態文字內容轉義 */
function escapeHTML(str){
  return String(str)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

/* ========================================================================
   總覽卡片
   ======================================================================== */
function renderCards(){
  const grid = document.getElementById('cardGrid');
  grid.innerHTML = '';
  MATERIALS.forEach(m=>{
    const s = m.series;
    const last = s[s.length-1];
    const prev1 = s[s.length-2];
    const prev3 = s[Math.max(0,s.length-4)];
    const prev12 = s[Math.max(0,s.length-13)];
    const chg1 = pctChange(last.price, prev1.price);
    const chg3 = pctChange(last.price, prev3.price);
    const chg12 = pctChange(last.price, prev12.price);

    const card = document.createElement('div');
    card.className = 'mcard';
    card.innerHTML =
      '<span class="demo-tag">示範資料</span>'+
      '<div class="name-row"><div class="name-zh">'+escapeHTML(m.zh)+'</div><div class="name-en">'+escapeHTML(m.en)+'</div></div>'+
      '<div class="price-row"><span class="price">'+fmtNum(last.price, last.price<10?2:0)+'</span>'+
      '<span class="ccy-unit">'+escapeHTML(m.ccy)+' / '+escapeHTML(m.unit)+'</span></div>'+
      '<div class="chg-grid">'+
        '<div class="chg-box"><div class="lbl">前一期</div>'+changeHTML(chg1)+'</div>'+
        '<div class="chg-box"><div class="lbl">三個月</div>'+changeHTML(chg3)+'</div>'+
        '<div class="chg-box"><div class="lbl">一年</div>'+changeHTML(chg12)+'</div>'+
      '</div>'+
      '<div class="meta">'+
        '<div>價格資料日期：<b>'+escapeHTML(fmtDate(last.date))+'</b></div>'+
        '<div>資料來源：<b>'+escapeHTML(m.source)+'</b></div>'+
        '<div>最後更新時間：<b>2026/07/28 09:00（示範）</b></div>'+
      '</div>';
    grid.appendChild(card);
  });
}
renderCards();

/* ========================================================================
   走勢圖（F-02／F-03／F-06）
   ======================================================================== */
const CHART_COLORS = ['#0B5769','#B3392C','#C88A1B','#1E7A4C','#5B4B9C','#2E6DB4','#9C4E1F'];

const chartState = {
  selected: new Set(['copper']),
  period: 13,     // 資料筆數（F-03）
  userMode: 'actual' // 使用者手動選擇之模式
};

function renderChips(){
  const wrap = document.getElementById('materialChips');
  wrap.innerHTML = '';
  MATERIALS.forEach(m=>{
    const chip = document.createElement('button');
    chip.type = 'button';
    const isSel = chartState.selected.has(m.id);
    chip.className = 'chip' + (isSel?' selected':'');
    chip.setAttribute('aria-pressed', isSel? 'true':'false');
    chip.textContent = m.zh+' '+m.en;
    chip.dataset.id = m.id;
    chip.addEventListener('click', ()=>{
      if(chartState.selected.has(m.id)){
        if(chartState.selected.size>1){ chartState.selected.delete(m.id); }
      } else {
        chartState.selected.add(m.id);
      }
      renderChips();
      updateChart();
    });
    wrap.appendChild(chip);
  });
}
renderChips();

document.getElementById('periodSeg').addEventListener('click', function(e){
  const btn = e.target.closest('button'); if(!btn) return;
  document.querySelectorAll('#periodSeg button').forEach(b=>{ b.classList.remove('active'); b.setAttribute('aria-pressed','false'); });
  btn.classList.add('active'); btn.setAttribute('aria-pressed','true');
  chartState.period = parseInt(btn.dataset.period,10);
  updateChart();
});
document.getElementById('modeSeg').addEventListener('click', function(e){
  const btn = e.target.closest('button'); if(!btn || btn.disabled) return;
  chartState.userMode = btn.dataset.mode;
  updateChart();
});

let priceChart = null;
let chartLibOk = true;
const canvasEl = document.getElementById('priceChart');
const fallbackEl = document.getElementById('chartFallback');
const tooltipEl = document.getElementById('chartTooltip');

function hasUnitMismatch(arr){
  const units = new Set(arr.map(function(m){ return m.unit+'|'+m.ccy; }));
  return units.size>1;
}

function sliceSeries(mat, count){
  const s = mat.series;
  return s.slice(Math.max(0, s.length-count));
}

/* 純資料運算，不依賴 Chart.js，供圖表渲染與CSV共用 (F-02 / F-06) */
function computeDatasets(){
  const arr = MATERIALS.filter(function(m){ return chartState.selected.has(m.id); });
  const count = chartState.period;
  const mismatch = arr.length>1 && hasUnitMismatch(arr);
  const effectiveMode = mismatch ? 'index' : chartState.userMode;

  const baseSeries = sliceSeries(arr[0]||MATERIALS[0], count);
  const labels = baseSeries.map(function(p){ return fmtDate(p.date); });

  const datasets = arr.map(function(m,i){
    const series = sliceSeries(m, count);
    let values;
    if(effectiveMode==='index'){
      const base = series[0].price;
      values = series.map(function(p){ return (p.price/base)*100; });
    } else {
      values = series.map(function(p){ return p.price; });
    }
    return {
      label: m.zh+' '+m.en,
      data: values,
      borderColor: CHART_COLORS[i%CHART_COLORS.length],
      backgroundColor: CHART_COLORS[i%CHART_COLORS.length]+'22',
      borderWidth:2, pointRadius:0, pointHitRadius:8, tension:0.15, fill:false,
      _mat:m, _series:series
    };
  });

  return { arr:arr, count:count, mismatch:mismatch, effectiveMode:effectiveMode, labels:labels, datasets:datasets };
}

function updateModeButtons(payload){
  const actualBtn = document.querySelector('#modeSeg button[data-mode="actual"]');
  const indexBtn = document.querySelector('#modeSeg button[data-mode="index"]');
  const noteEl = document.getElementById('modeNote');

  if(payload.mismatch){
    actualBtn.disabled = true;
    actualBtn.classList.remove('active'); actualBtn.setAttribute('aria-pressed','false');
    indexBtn.classList.add('active'); indexBtn.setAttribute('aria-pressed','true');
    noteEl.className = 'small-note warn-note';
    noteEl.textContent = '⚠️ 所選材料單位或幣別不同，「實際價格」已停用，圖表、Tooltip、統計卡片與CSV皆自動使用「指數化比較模式（起始值=100）」，避免不同單位混合誤讀。';
  } else {
    actualBtn.disabled = false;
    if(payload.effectiveMode==='actual'){
      actualBtn.classList.add('active'); actualBtn.setAttribute('aria-pressed','true');
      indexBtn.classList.remove('active'); indexBtn.setAttribute('aria-pressed','false');
    } else {
      actualBtn.classList.remove('active'); actualBtn.setAttribute('aria-pressed','false');
      indexBtn.classList.add('active'); indexBtn.setAttribute('aria-pressed','true');
    }
    noteEl.className = 'small-note';
    noteEl.textContent = '';
  }
}

function externalTooltipHandler(context){
  const chart = context.chart, tooltip = context.tooltip;
  if(!tooltip || tooltip.opacity===0){ tooltipEl.style.opacity=0; return; }
  const idx = tooltip.dataPoints[0].dataIndex;
  let html = '';
  tooltip.dataPoints.forEach(function(dp){
    const ds = chart.data.datasets[dp.datasetIndex];
    const pt = ds._series[idx];
    const val = dp.raw;
    const isIndex = chartState._currentEffectiveMode === 'index';
    const priceLine = isIndex
      ? ('指數：'+fmtNum(val,2)+'點<br>')
      : ('價格：'+fmtNum(val, val<10?2:0)+' '+escapeHTML(ds._mat.ccy)+'<br>單位：'+escapeHTML(ds._mat.unit)+'<br>');
    html += '<div class="t-zh">'+escapeHTML(ds.label)+'</div>'+
      '日期：'+escapeHTML(fmtDate(pt.date))+'<br>'+
      priceLine+
      '來源：'+escapeHTML(ds._mat.source)+
      '<div style="margin-top:3px;color:#F2B33D;">示範資料</div>';
  });
  tooltipEl.innerHTML = html;
  const rectInfo = chart.canvas;
  let x = tooltip.caretX + 12;
  let y = tooltip.caretY - 10;
  const wrapRect = chart.canvas.parentElement.getBoundingClientRect();
  if(x + 220 > wrapRect.width){ x = tooltip.caretX - 230; }
  tooltipEl.style.left = x+'px';
  tooltipEl.style.top = y+'px';
  tooltipEl.style.opacity = 1;
}

function renderLegend(datasets, effectiveMode){
  const wrap = document.getElementById('chartLegend');
  wrap.innerHTML = '';
  datasets.forEach(function(ds){
    const item = document.createElement('div');
    item.className='legend-item';
    const dot = document.createElement('span');
    dot.className = 'legend-dot';
    dot.style.background = ds.borderColor;
    const unitLabel = effectiveMode==='index' ? '指數化（起始=100）' : (ds._mat.ccy+'／'+ds._mat.unit);
    const textNode = document.createTextNode(ds.label+'（'+unitLabel+'）');
    item.appendChild(dot);
    item.appendChild(textNode);
    wrap.appendChild(item);
  });
}

function renderStats(datasets, effectiveMode){
  const wrap = document.getElementById('statRow');
  wrap.innerHTML='';
  datasets.forEach(function(ds){
    const vals = ds.data;
    const max = Math.max.apply(null, vals), min = Math.min.apply(null, vals);
    const avg = vals.reduce(function(a,b){return a+b;},0)/vals.length;
    const rows = [['期間最高', max], ['期間最低', min], ['期間平均', avg]];
    rows.forEach(function(pair){
      const lbl = pair[0], val = pair[1];
      const box=document.createElement('div');
      box.className='stat-box';
      let valText;
      if(effectiveMode==='index'){
        valText = fmtNum(val, 2)+' 指數點';
      } else {
        valText = fmtNum(val, val<10?2:0)+' '+ds._mat.ccy+'／'+ds._mat.unit;
      }
      const lblDiv = document.createElement('div');
      lblDiv.className = 'lbl';
      lblDiv.textContent = ds.label+'｜'+lbl;
      const valDiv = document.createElement('div');
      valDiv.className = 'val';
      valDiv.textContent = valText;
      box.appendChild(lblDiv);
      box.appendChild(valDiv);
      wrap.appendChild(box);
    });
  });
}

function renderSrSummary(payload){
  const el = document.getElementById('chartSrSummary');
  if(!el) return;
  const parts = payload.datasets.map(function(ds){
    const last = ds.data[ds.data.length-1];
    const first = ds.data[0];
    return ds.label+'從'+fmtNum(first,2)+'變化至'+fmtNum(last,2)+(payload.effectiveMode==='index'?'指數點':(' '+ds._mat.ccy));
  });
  el.textContent = '圖表資料摘要（示範資料）：'+parts.join('；');
}

function updateChart(){
  let payload;
  try{
    payload = computeDatasets();
  }catch(err){
    fallbackEl.classList.add('show');
    canvasEl.style.display='none';
    return;
  }
  chartState._currentEffectiveMode = payload.effectiveMode;
  updateModeButtons(payload);
  renderLegend(payload.datasets, payload.effectiveMode);
  renderStats(payload.datasets, payload.effectiveMode);
  renderSrSummary(payload);
  window._csvExport = payload;

  document.getElementById('chartTitle').textContent =
    payload.arr.length===1
      ? payload.arr[0].zh+'（'+payload.arr[0].en+'）價格走勢'
      : '多材料價格走勢比較（'+(payload.effectiveMode==='index'?'指數化，起始=100':'實際價格')+'）';

  if(typeof Chart === 'undefined'){
    chartLibOk = false;
    fallbackEl.classList.add('show');
    canvasEl.style.display='none';
    return;
  }
  fallbackEl.classList.remove('show');
  canvasEl.style.display='block';

  try{
    if(priceChart){ priceChart.destroy(); }
    const ctx = canvasEl.getContext('2d');
    priceChart = new Chart(ctx, {
      type:'line',
      data:{ labels:payload.labels, datasets:payload.datasets },
      options:{
        responsive:true,
        maintainAspectRatio:false,
        interaction:{ mode:'index', intersect:false },
        plugins:{
          legend:{ display:false },
          tooltip:{ enabled:false, external: externalTooltipHandler }
        },
        scales:{
          x:{ grid:{ color:'#EEF1F4' }, ticks:{ color:'#8695A6', font:{size:10.5}, maxRotation:0, autoSkip:true, maxTicksLimit:10 } },
          y:{ grid:{ color:'#EEF1F4' }, ticks:{ color:'#8695A6', font:{size:10.5} },
              title:{ display:true, text: payload.effectiveMode==='index'?'指數（起始值=100）':'價格（單位依所選材料，詳見圖例）', color:'#8695A6', font:{size:10.5} } }
        }
      }
    });
    chartLibOk = true;
  }catch(err){
    chartLibOk = false;
    fallbackEl.classList.add('show');
    canvasEl.style.display='none';
  }
}

updateChart();

/* CSV 匯出（F-02 / F-07 / R-02） */
/* 文字欄位：材料名稱、日期、來源、模式等。開頭為 =、+、-、@ 時加入單引號防止CSV公式注入 */
function csvEscapeText(cell){
  let s = String(cell);
  if(/^[=+\-@]/.test(s)){ s = "'"+s; }
  return '"'+s.replace(/"/g,'""')+'"';
}
/* 數值欄位：僅接受有限數字，直接輸出數字（不加引號、不加單引號），確保Excel辨識為數字；合法負數不受影響 */
function csvNumber(n){
  const v = Number(n);
  if(!isFinite(v)) return '';
  return v.toFixed(4);
}
function localDateStamp(){
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth()+1).padStart(2,'0');
  const day = String(d.getDate()).padStart(2,'0');
  return y+'-'+m+'-'+day;
}
function localTimestamp(){
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth()+1).padStart(2,'0');
  const day = String(d.getDate()).padStart(2,'0');
  const hh = String(d.getHours()).padStart(2,'0');
  const mm = String(d.getMinutes()).padStart(2,'0');
  return y+'/'+m+'/'+day+' '+hh+':'+mm;
}

document.getElementById('exportCsvBtn').addEventListener('click', function(){
  const payload = window._csvExport;
  if(!payload || !payload.datasets){ return; }
  const modeLabel = payload.effectiveMode==='index' ? '指數化(起始=100)' : '實際價格';
  const exportTime = localTimestamp();
  const header = ['材料中文','材料英文','日期','數值','有效顯示模式','幣別','單位','資料來源','是否為示範資料','資料頻率','匯出時間'].map(csvEscapeText);
  const dataRows = [];
  payload.datasets.forEach(function(ds){
    ds._series.forEach(function(pt,i){
      const val = ds.data[i];
      dataRows.push([
        csvEscapeText(ds._mat.zh),
        csvEscapeText(ds._mat.en),
        csvEscapeText(fmtDate(pt.date)),
        csvNumber(val),
        csvEscapeText(modeLabel),
        csvEscapeText(payload.effectiveMode==='index' ? '—' : ds._mat.ccy),
        csvEscapeText(payload.effectiveMode==='index' ? '指數點' : ds._mat.unit),
        csvEscapeText(ds._mat.source),
        csvEscapeText('是（示範資料，非真實市場行情）'),
        csvEscapeText('月'),
        csvEscapeText(exportTime)
      ]);
    });
  });
  const csv = [header].concat(dataRows).map(function(r){ return r.join(','); }).join('\n');
  const blob = new Blob(['\uFEFF'+csv], {type:'text/csv;charset=utf-8;'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = '原材料價格走勢_示範資料_'+localDateStamp()+'.csv';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

/* ========================================================================
   採購成本影響試算器（F-01／F-04／F-05）
   ======================================================================== */
const RATIO_FIELDS = ['f_matRatio','f_procRatio','f_energyRatio','f_otherRatio']; // 基本成本占比（不含外幣曝險）
const FX_FIELD = 'f_fxRatio';
const RATE_FIELDS = ['f_matRate','f_fxRate','f_procRate','f_energyRate','f_otherRate','f_supplierAsk'];
const ERR_MAP = {
  f_price:'err_price', f_matRatio:'err_matRatio', f_matRate:'err_matRate',
  f_fxRatio:'err_fxRatio', f_fxRate:'err_fxRate',
  f_procRatio:'err_procRatio', f_procRate:'err_procRate',
  f_energyRatio:'err_energyRatio', f_energyRate:'err_energyRate',
  f_otherRatio:'err_otherRatio', f_otherRate:'err_otherRate',
  f_supplierAsk:'err_supplierAsk'
};

function parseField(id){
  const el = document.getElementById(id);
  const raw = el.value.trim();
  if(raw===''){ return { value:0, valid:true, el:el }; }
  const v = parseFloat(raw);
  if(isNaN(v) || !isFinite(v)){ return { value:0, valid:false, el:el }; }
  return { value:v, valid:true, el:el };
}

function clearFieldStates(){
  Object.keys(ERR_MAP).forEach(function(id){
    const errEl = document.getElementById(ERR_MAP[id]);
    if(errEl) errEl.textContent = '';
    const inputEl = document.getElementById(id);
    if(inputEl) inputEl.classList.remove('invalid');
  });
}
function setFieldError(id, msg){
  const errEl = document.getElementById(ERR_MAP[id]);
  const inputEl = document.getElementById(id);
  if(errEl) errEl.textContent = msg;
  if(inputEl) inputEl.classList.add('invalid');
}

function showErrorState(message){
  const hero = document.getElementById('resultHero');
  hero.classList.add('error-state');
  document.getElementById('resultRate').innerHTML = '<span class="error-text">'+message+'</span>';
  document.getElementById('resultPrice').textContent = '';
  ['impactMat','impactFx','impactProc','impactEnergy','impactOther'].forEach(function(id){
    document.getElementById(id).textContent = '—';
    document.getElementById(id).style.color = '';
  });
  document.getElementById('cmpEstimate').textContent = '—';
  document.getElementById('cmpEstimate').style.color = '';
  document.getElementById('cmpSupplier').textContent = '—';
  document.getElementById('cmpSupplier').style.color = '';
  document.getElementById('cmpGap').textContent = '—';
  document.getElementById('formulaBox').textContent = '無法計算，請先修正標示的輸入內容後再檢視計算過程。';
}

function setImpactDisplay(id, pct){
  const el = document.getElementById(id);
  el.textContent = formatSigned(pct);
  const cls = signColorClass(pct);
  el.style.color = cls==='up' ? 'var(--up)' : (cls==='down' ? 'var(--down)' : 'var(--text-1)');
}

function gapText(gap){
  if(Math.abs(gap) < 0.005){ return '0.00個百分點（供應商等於推估值）'; }
  const dir = gap>0 ? '高於' : '低於';
  const sign = gap>0 ? '+' : '';
  return sign+gap.toFixed(2)+'個百分點（供應商'+dir+'推估值）';
}

function validateAndCalc(){
  clearFieldStates();
  document.getElementById('resultHero').classList.remove('error-state');
  const warnEl = document.getElementById('ratioWarn');
  warnEl.classList.remove('show');

  // 逐一解析所有欄位
  const parsed = {};
  let invalid = false;

  ['f_price','f_matRatio','f_matRate','f_fxRatio','f_fxRate','f_procRatio','f_procRate','f_energyRatio','f_energyRate','f_otherRatio','f_otherRate','f_supplierAsk'].forEach(function(id){
    const p = parseField(id);
    parsed[id] = p;
    if(!p.valid){
      setFieldError(id, '請輸入有效數字（不可為空白以外的無效值、NaN 或 Infinity）。');
      invalid = true;
    }
  });

  if(invalid){
    showErrorState('無法計算，請先修正標示的輸入內容。');
    return;
  }

  const price = parsed['f_price'].value;
  if(price < 0){
    setFieldError('f_price', '產品單價不得小於 0。');
    invalid = true;
  }

  const ratioChecks = [
    ['f_matRatio','原材料成本占比'], ['f_procRatio','加工成本占比'],
    ['f_energyRatio','能源成本占比'], ['f_otherRatio','其他成本占比'],
    ['f_fxRatio','外幣曝險占總成本比例']
  ];
  ratioChecks.forEach(function(pair){
    const id = pair[0], label = pair[1];
    const v = parsed[id].value;
    if(v<0 || v>100){
      setFieldError(id, label+'須介於 0% 至 100% 之間，目前輸入 '+v+'%。');
      invalid = true;
    }
  });

  /* R-01：變化率（含供應商要求漲幅）不得小於 -100% */
  const RATE_MIN = -100;
  const rateMinChecks = [
    ['f_matRate','原材料價格變化率'], ['f_fxRate','匯率變化率'],
    ['f_procRate','加工成本變化率'], ['f_energyRate','能源成本變化率'],
    ['f_otherRate','其他成本變化率'], ['f_supplierAsk','供應商要求漲幅']
  ];
  let rateMinInvalid = false;
  rateMinChecks.forEach(function(pair){
    const id = pair[0], label = pair[1];
    const v = parsed[id].value;
    if(v < RATE_MIN){
      setFieldError(id, label+'不得低於 -100%，目前輸入 '+v+'%。');
      invalid = true;
      rateMinInvalid = true;
    }
  });

  const matRatio = parsed['f_matRatio'].value;
  const procRatio = parsed['f_procRatio'].value;
  const energyRatio = parsed['f_energyRatio'].value;
  const otherRatio = parsed['f_otherRatio'].value;
  const fxRatio = parsed['f_fxRatio'].value;

  const baseRatioSum = matRatio+procRatio+energyRatio+otherRatio; // 不含外幣曝險
  if(baseRatioSum > 100){
    warnEl.classList.add('show');
    invalid = true;
  }

  if(invalid){
    showErrorState(rateMinInvalid ? '無法計算，變化率不得低於-100%。' : '無法計算，請先修正標示的輸入內容。');
    return;
  }

  const matRate = parsed['f_matRate'].value;
  const fxRate = parsed['f_fxRate'].value;
  const procRate = parsed['f_procRate'].value;
  const energyRate = parsed['f_energyRate'].value;
  const otherRate = parsed['f_otherRate'].value;
  const supplierAsk = parsed['f_supplierAsk'].value;

  const impMat = (matRatio/100)*(matRate/100);
  const impFx = (fxRatio/100)*(fxRate/100);
  const impProc = (procRatio/100)*(procRate/100);
  const impEnergy = (energyRatio/100)*(energyRate/100);
  const impOther = (otherRatio/100)*(otherRate/100);

  const totalRate = impMat+impFx+impProc+impEnergy+impOther; // fraction
  const newPrice = price * (1+totalRate);
  const gap = supplierAsk - (totalRate*100);

  /* v1.2.1：極端數值溢位檢查，任一計算結果非有限數字時停止計算 */
  const computedValues = [
    impMat, impFx, impProc, impEnergy, impOther,
    totalRate, newPrice, gap, totalRate * 100
  ];
  if(!computedValues.every(Number.isFinite)){
    showErrorState('無法計算，輸入數值過大或計算結果超出可處理範圍。');
    document.getElementById('formulaBox').textContent = '請降低輸入數值後重新計算。';
    return;
  }

  /* R-01：最終檢查，推估新價格不得為負數 */
  if(newPrice < 0){
    showErrorState('無法計算，目前輸入情境將導致新價格為負數，情境不合理。');
    return;
  }

  setImpactDisplay('impactMat', impMat*100);
  setImpactDisplay('impactFx', impFx*100);
  setImpactDisplay('impactProc', impProc*100);
  setImpactDisplay('impactEnergy', impEnergy*100);
  setImpactDisplay('impactOther', impOther*100);

  const totalPct = totalRate*100;
  document.getElementById('resultRate').innerHTML =
    '<span style="color:'+(signColorClass(totalPct)==='up'?'var(--up)':(signColorClass(totalPct)==='down'?'var(--down)':'inherit'))+'">'+formatSigned(totalPct)+'</span>';
  document.getElementById('resultPrice').textContent =
    '推估合理新價格：'+fmtNum(newPrice,2)+' 元（原單價 '+fmtNum(price,2)+' 元）';

  document.getElementById('cmpEstimate').textContent = formatSigned(totalPct);
  document.getElementById('cmpEstimate').style.color = signColorClass(totalPct)==='up'?'var(--up)':(signColorClass(totalPct)==='down'?'var(--down)':'var(--text-1)');
  document.getElementById('cmpSupplier').textContent = formatSigned(supplierAsk);
  document.getElementById('cmpSupplier').style.color = signColorClass(supplierAsk)==='up'?'var(--up)':(signColorClass(supplierAsk)==='down'?'var(--down)':'var(--text-1)');

  document.getElementById('cmpGap').textContent = gapText(gap);
  document.getElementById('cmpGap').style.color = Math.abs(gap)<0.005 ? 'var(--text-1)' : (gap>0?'var(--up)':'var(--down)');

  document.getElementById('formulaBox').textContent =
'推估合理漲幅 = 原材料影響 + 匯率影響 + 加工影響 + 能源影響 + 其他影響\n'+
'           = ('+matRatio+'% × '+matRate+'%) + ('+fxRatio+'% × '+fxRate+'%) + ('+procRatio+'% × '+procRate+'%)\n'+
'           + ('+energyRatio+'% × '+energyRate+'%) + ('+otherRatio+'% × '+otherRate+'%)\n'+
'           = '+totalPct.toFixed(2)+'%\n\n'+
'推估合理新價格 = 產品目前單價 × (1 + 推估合理漲幅)\n'+
'             = '+fmtNum(price,2)+' × (1 + '+totalRate.toFixed(4)+')\n'+
'             = '+fmtNum(newPrice,2)+' 元\n\n'+
'基本成本占比合計（不含外幣曝險） = '+matRatio+'% + '+procRatio+'% + '+energyRatio+'% + '+otherRatio+'% = '+baseRatioSum.toFixed(1)+'%';
}

['f_price','f_matRatio','f_matRate','f_fxRatio','f_fxRate','f_procRatio','f_procRate','f_energyRatio','f_energyRate','f_otherRatio','f_otherRate','f_supplierAsk'].forEach(function(id){
  document.getElementById(id).addEventListener('input', validateAndCalc);
});
document.getElementById('resetBtn').addEventListener('click', function(){
  const defaults = {
    f_price:1000, f_matRatio:35, f_matRate:6, f_fxRatio:15, f_fxRate:2,
    f_procRatio:25, f_procRate:3, f_energyRatio:10, f_energyRate:4,
    f_otherRatio:15, f_otherRate:0, f_supplierAsk:8
  };
  Object.keys(defaults).forEach(function(id){ document.getElementById(id).value = defaults[id]; });
  validateAndCalc();
});
validateAndCalc();

/* ========================================================================
   導覽切換
   ======================================================================== */
function switchTab(target){
  document.querySelectorAll('.section').forEach(function(s){ s.classList.remove('active'); });
  document.getElementById(target).classList.add('active');
  document.querySelectorAll('.sidenav button, .mobile-tabs button').forEach(function(b){
    const isActive = b.dataset.target===target;
    b.classList.toggle('active', isActive);
    b.setAttribute('aria-pressed', isActive?'true':'false');
  });
  if(target==='chart' && priceChart){ setTimeout(function(){ try{ priceChart.resize(); }catch(e){} },50); }
}
document.querySelectorAll('.sidenav button, .mobile-tabs button').forEach(function(btn){
  btn.addEventListener('click', function(){ switchTab(btn.dataset.target); });
});

})();
