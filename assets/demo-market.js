(function(){
'use strict';

/* v2.3 Development Demo market fixture.
   This module is for source/local UI development only and is removed from
   the production GitHub Pages artifact by scripts/prepare_site.py. */

const MATERIALS=[
  {id:'zinc',zh:'鋅',en:'Zinc',ccy:'USD',unit:'公噸(MT)',base:2850,vol:.045,drift:.0006,seed:101,source:'Development Demo fixture'},
  {id:'copper',zh:'銅',en:'Copper',ccy:'USD',unit:'公噸(MT)',base:9200,vol:.040,drift:.0010,seed:202,source:'Development Demo fixture'},
  {id:'aluminium',zh:'鋁',en:'Aluminium',ccy:'USD',unit:'公噸(MT)',base:2450,vol:.035,drift:.0003,seed:303,source:'Development Demo fixture'},
  {id:'nickel',zh:'鎳',en:'Nickel',ccy:'USD',unit:'公噸(MT)',base:16800,vol:.055,drift:-.0012,seed:404,source:'Development Demo fixture'},
  {id:'iron_ore',zh:'鐵礦砂',en:'Iron Ore',ccy:'USD',unit:'公噸(MT)',base:105,vol:.050,drift:-.0004,seed:505,source:'Development Demo fixture'},
  {id:'crude_oil',zh:'原油',en:'Crude Oil',ccy:'USD',unit:'桶(bbl)',base:82,vol:.038,drift:.0002,seed:606,source:'Development Demo fixture'},
  {id:'natural_gas',zh:'天然氣',en:'Natural Gas',ccy:'USD',unit:'MMBtu',base:2.85,vol:.070,drift:.0008,seed:707,source:'Development Demo fixture'}
];
const COLORS=['#0B5769','#B3392C','#C88A1B','#1E7A4C','#5B4B9C','#2E6DB4','#9C4E1F'];
const state={selected:new Set(['copper']),period:13,mode:'actual',effective:'actual'};
let chart=null,csvPayload=null;

function rng(seed){return function(){seed|=0;seed=(seed+0x6D2B79F5)|0;let t=Math.imul(seed^(seed>>>15),1|seed);t=(t+Math.imul(t^(t>>>7),61|t))^t;return((t^(t>>>14))>>>0)/4294967296;};}
function generate(mat){
  const random=rng(mat.seed),series=[],today=new Date(2026,6,28);let price=mat.base*.78;
  for(let i=60;i>=0;i--){
    const d=new Date(today.getFullYear(),today.getMonth()-i,28);
    const shock=(random()-.5)*2*mat.vol;
    price=Math.max(price*(1+mat.drift+shock),mat.base*.25);
    series.push({period:d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'),price:price});
  }
  const factor=mat.base/series[series.length-1].price;
  series.forEach(function(p,i){const w=i/(series.length-1);p.price*=1+(factor-1)*.9*w;});
  return series;
}
MATERIALS.forEach(function(m){m.series=generate(m);});

function esc(v){return String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function num(v,d){return Number(v).toLocaleString('zh-Hant-TW',{minimumFractionDigits:d,maximumFractionDigits:d});}
function pct(a,b){return b?(a-b)/b*100:null;}
function signed(v){if(v===null||!isFinite(v))return'—';return(v>0?'▲ +':v<0?'▼ ':'— ')+Number(v).toFixed(2)+'%';}
function cls(v){return v>0.0005?'up':v<-0.0005?'down':'flat';}
function period(v){return String(v).replace('-','/');}

function renderCards(){
  const grid=document.getElementById('cardGrid');grid.innerHTML='';
  MATERIALS.forEach(function(m){
    const s=m.series,l=s[s.length-1],p1=s[s.length-2],p3=s[s.length-4],p12=s[s.length-13],d=l.price<10?2:0;
    const card=document.createElement('div');card.className='mcard';
    card.innerHTML='<span class="demo-tag">Development Demo</span><div class="name-row"><div class="name-zh">'+esc(m.zh)+'</div><div class="name-en">'+esc(m.en)+'</div></div><div class="price-row"><span class="price">'+num(l.price,d)+'</span><span class="ccy-unit">'+esc(m.ccy)+' / '+esc(m.unit)+'</span></div><div class="chg-grid"><div class="chg-box"><div class="lbl">前一期</div><span class="chg-val '+cls(pct(l.price,p1.price))+'">'+signed(pct(l.price,p1.price))+'</span></div><div class="chg-box"><div class="lbl">三個月</div><span class="chg-val '+cls(pct(l.price,p3.price))+'">'+signed(pct(l.price,p3.price))+'</span></div><div class="chg-box"><div class="lbl">一年</div><span class="chg-val '+cls(pct(l.price,p12.price))+'">'+signed(pct(l.price,p12.price))+'</span></div></div><div class="meta"><div>價格資料月份：<b>'+period(l.period)+'</b></div><div>資料來源：<b>Development Demo fixture</b></div><div>正式部署：<b>此模組會被移除</b></div></div>';
    grid.appendChild(card);
  });
}

function renderChips(){
  const wrap=document.getElementById('materialChips');wrap.innerHTML='';
  MATERIALS.forEach(function(m){const b=document.createElement('button');b.type='button';b.dataset.id=m.id;b.className='chip'+(state.selected.has(m.id)?' selected':'');b.setAttribute('aria-pressed',state.selected.has(m.id)?'true':'false');b.textContent=m.zh+' '+m.en;b.onclick=function(){if(state.selected.has(m.id)){if(state.selected.size>1)state.selected.delete(m.id);}else state.selected.add(m.id);renderChips();draw();};wrap.appendChild(b);});
}

function payload(){
  const selected=MATERIALS.filter(function(m){return state.selected.has(m.id);});
  const mismatch=selected.length>1&&new Set(selected.map(function(m){return m.unit+'|'+m.ccy;})).size>1;
  const effective=mismatch?'index':state.mode;state.effective=effective;
  const labels=(selected[0]||MATERIALS[0]).series.slice(-state.period).map(function(p){return period(p.period);});
  const sets=selected.map(function(m,i){const series=m.series.slice(-state.period),start=series[0].price;return{label:m.zh+' '+m.en,data:series.map(function(p){return effective==='index'?p.price/start*100:p.price;}),borderColor:COLORS[i%COLORS.length],backgroundColor:COLORS[i%COLORS.length]+'22',borderWidth:2,pointRadius:0,pointHitRadius:8,tension:.15,fill:false,_mat:m,_series:series};});
  return{selected:selected,mismatch:mismatch,effective:effective,labels:labels,sets:sets};
}

function draw(){
  const p=payload();csvPayload=p;
  const actual=document.querySelector('#modeSeg button[data-mode="actual"]'),index=document.querySelector('#modeSeg button[data-mode="index"]'),note=document.getElementById('modeNote');
  actual.disabled=p.mismatch;actual.classList.toggle('active',!p.mismatch&&p.effective==='actual');index.classList.toggle('active',p.mismatch||p.effective==='index');note.textContent=p.mismatch?'⚠️ 單位不同，Development Demo 已自動切換為指數化比較。':'';
  const legend=document.getElementById('chartLegend');legend.innerHTML='';p.sets.forEach(function(d){const item=document.createElement('div');item.className='legend-item';item.innerHTML='<span class="legend-dot" style="background:'+d.borderColor+'"></span>'+esc(d.label)+'（'+esc(p.effective==='index'?'指數化（起始=100）':d._mat.ccy+'／'+d._mat.unit)+'）';legend.appendChild(item);});
  const stats=document.getElementById('statRow');stats.innerHTML='';p.sets.forEach(function(d){[['期間最高',Math.max.apply(null,d.data)],['期間最低',Math.min.apply(null,d.data)],['期間平均',d.data.reduce(function(a,b){return a+b;},0)/d.data.length]].forEach(function(r){const box=document.createElement('div');box.className='stat-box';box.innerHTML='<div class="lbl">'+esc(d.label+'｜'+r[0])+'</div><div class="val">'+(p.effective==='index'?num(r[1],2)+' 指數點':num(r[1],Math.abs(r[1])<10?2:0)+' '+esc(d._mat.ccy)+'／'+esc(d._mat.unit))+'</div>';stats.appendChild(box);});});
  document.getElementById('chartSrSummary').textContent='Development Demo 圖表摘要：'+p.sets.map(function(d){return d.label+' '+num(d.data[0],2)+' → '+num(d.data[d.data.length-1],2);}).join('；');
  document.getElementById('chartTitle').textContent=p.selected.length===1?p.selected[0].zh+'（'+p.selected[0].en+'）Development Demo 走勢':'Development Demo 多材料比較';
  if(typeof Chart==='undefined')return;
  if(chart)chart.destroy();
  chart=new Chart(document.getElementById('priceChart').getContext('2d'),{type:'line',data:{labels:p.labels,datasets:p.sets},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{display:false}},scales:{x:{grid:{color:'#EEF1F4'}},y:{grid:{color:'#EEF1F4'}}}}});
}

function csvText(v){let s=String(v);if(/^[=+\-@]/.test(s))s="'"+s;return'"'+s.replace(/"/g,'""')+'"';}
function exportCsv(){if(!csvPayload)return;const head=['材料中文','材料英文','月份','數值','顯示模式','資料來源','是否為示範資料'].map(csvText),rows=[];csvPayload.sets.forEach(function(d){d._series.forEach(function(o,i){rows.push([csvText(d._mat.zh),csvText(d._mat.en),csvText(o.period),Number(d.data[i]).toFixed(4),csvText(csvPayload.effective),csvText('Development Demo fixture'),csvText('是')]);});});const blob=new Blob(['\uFEFF'+[head].concat(rows).map(function(r){return r.join(',');}).join('\n')],{type:'text/csv;charset=utf-8;'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='Development_Demo_原材料價格.csv';document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);}

renderCards();renderChips();draw();
document.getElementById('periodSeg').addEventListener('click',function(e){const b=e.target.closest('button');if(!b)return;document.querySelectorAll('#periodSeg button').forEach(function(x){x.classList.toggle('active',x===b);});state.period=parseInt(b.dataset.period,10);draw();});
document.getElementById('modeSeg').addEventListener('click',function(e){const b=e.target.closest('button');if(!b||b.disabled)return;state.mode=b.dataset.mode;draw();});
document.getElementById('exportCsvBtn').addEventListener('click',exportCsv);
window.addEventListener('procurement:chart-visible',function(){if(chart)try{chart.resize();}catch(e){}});
})();
