(function(){
'use strict';
const ORDER=['zinc','copper','aluminium','nickel','iron_ore','crude_oil','natural_gas'];
const COLORS=['#0B5769','#B3392C','#C88A1B','#1E7A4C','#5B4B9C','#2E6DB4','#9C4E1F'];
const state={selected:new Set(['copper']),period:13,mode:'actual',effective:'actual'};
let materials=[],wb=null,status=null,chart=null,csvPayload=null;
const $=s=>document.querySelector(s);
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const num=(v,d)=>Number.isFinite(+v)?(+v).toLocaleString('zh-Hant-TW',{minimumFractionDigits:d??(Math.abs(+v)<10?2:0),maximumFractionDigits:d??(Math.abs(+v)<10?2:0)}):'—';
const period=p=>/^\d{4}-\d{2}$/.test(String(p))?p.replace('-','/'):String(p||'—');
const taipei=iso=>{const d=new Date(iso);return Number.isNaN(d.getTime())?String(iso||'—'):new Intl.DateTimeFormat('zh-TW',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d);};
const pct=(a,b)=>Number.isFinite(+a)&&Number.isFinite(+b)&&+b!==0?(+a-+b)/+b*100:null;
const signed=v=>!Number.isFinite(+v)?'—':(+v>0?'▲ +':+v<0?'▼ ':'— ')+(+v).toFixed(2)+'%';
const cls=v=>+v>0.0005?'up':+v<-0.0005?'down':'flat';

function loading(){
 document.title='國際原材料價格與採購分析平台｜資料載入中';
 $('.demo-pill').textContent='正在載入 World Bank 月度資料…';
 $('.notice-banner').innerHTML='正在讀取同站的 <b>World Bank Pink Sheet 月度資料</b>，驗證完成前不顯示市場價格。';
 $('#cardGrid').innerHTML='<div class="panel" style="grid-column:1/-1;">資料載入與驗證中…</div>';
 $('#priceChart').style.visibility='hidden';
 $('#exportCsvBtn').disabled=true;
}
function validate(data,st){
 if(data?.source!=='WORLD_BANK_PINK_SHEET'||data?.isRealData!==true)throw Error('world-bank.json不是正式資料');
 if(st?.dataMode!=='WORLD_BANK_PRIMARY'||st?.worldBank?.status!=='SUCCESS')throw Error('status.json未顯示同步成功');
 if(!data.dataset?.latestPeriod||!data.series)throw Error('資料結構不完整');
 ORDER.forEach(id=>{const s=data.series[id];if(!s||!Array.isArray(s.observations)||s.observations.length<61)throw Error(id+'資料不足');if(s.latestPeriod!==data.dataset.latestPeriod)throw Error(id+'最新月份不一致');if(!s.observations.every(p=>/^\d{4}-\d{2}$/.test(p.period)&&Number.isFinite(+p.value)))throw Error(id+'含無效資料');});
}
function makeMaterials(){
 materials=ORDER.map(id=>{const s=wb.series[id];return{id,zh:s.nameZh,en:s.nameEn,ccy:s.currency,unit:s.displayUnit,sourceUnit:s.sourceUnit,source:s.attributionNote,lme:s.isLmeDerived===true,series:s.observations.map(p=>({period:p.period,price:+p.value}))};});
}
function copy(){
 const latest=period(wb.dataset.latestPeriod),sync=taipei(status.worldBank.lastSuccessAt||wb.generatedAt);
 document.title='國際原材料價格與採購分析平台｜World Bank 月度資料';
 $('.brand .sub').textContent='International Raw Materials Procurement Analytics（World Bank 月度資料版 v1.3.0）';
 $('.demo-pill').textContent='World Bank Pink Sheet・資料至 '+latest;
 $('.notice-banner').innerHTML='目前顯示 <b>World Bank Commodity Price Data（Pink Sheet）月度名目美元資料</b>，資料月份截至 <b>'+esc(latest)+'</b>；系統最後同步時間為 <b>'+esc(sync)+'</b>。資料具發布時差，應搭配供應商報價、匯率及合約條件判讀。';
 $('#overview .page-desc').textContent='七項核心原材料之World Bank Pink Sheet月度價格，供採購人員快速掌握前一期、三個月與一年變化。';
 $('#chart .page-desc').textContent='選擇單一或多項材料比較World Bank月度價格；不同單位材料會自動切換為起始值100的指數化模式。';
 $('#priceChart').setAttribute('aria-label','World Bank Pink Sheet原材料月度價格走勢圖');
 $('.assump-box').innerHTML='<b>計算假設：</b>各成本項目彼此獨立、線性加總；未計入稅費、關稅、運費突發變動或供應商毛利調整；外幣曝險可能與其他成本重疊，使用時請自行檢視。';
 $('.disclaimer').textContent='試算結果依使用者輸入推估，不代表供應商實際成本結構；市場資料與試算均不構成財務或投資建議。';
 $('.appfoot').textContent='國際原材料價格與採購分析平台｜市場資料：World Bank Pink Sheet（月度）｜資料月份 '+latest+'｜© 2026';
}
function cards(){
 const sync=taipei(status.worldBank.lastSuccessAt||wb.generatedAt),grid=$('#cardGrid');grid.innerHTML='';
 materials.forEach(m=>{const s=m.series,l=s.at(-1),p1=s.at(-2),p3=s.at(-4),p12=s.at(-13),d=Math.abs(l.price)<10?2:0,card=document.createElement('div');card.className='mcard';card.innerHTML='<span class="demo-tag">'+(m.lme?'WB／LME月度基準':'World Bank')+'</span><div class="name-row"><div class="name-zh">'+esc(m.zh)+'</div><div class="name-en">'+esc(m.en)+'</div></div><div class="price-row"><span class="price">'+num(l.price,d)+'</span><span class="ccy-unit">'+esc(m.ccy)+' / '+esc(m.unit)+'</span></div><div class="chg-grid"><div class="chg-box"><div class="lbl">前一期</div><span class="chg-val '+cls(pct(l.price,p1.price))+'">'+signed(pct(l.price,p1.price))+'</span></div><div class="chg-box"><div class="lbl">三個月</div><span class="chg-val '+cls(pct(l.price,p3.price))+'">'+signed(pct(l.price,p3.price))+'</span></div><div class="chg-box"><div class="lbl">一年</div><span class="chg-val '+cls(pct(l.price,p12.price))+'">'+signed(pct(l.price,p12.price))+'</span></div></div><div class="meta"><div>價格資料月份：<b>'+period(l.period)+'</b></div><div>資料來源：<b>World Bank Pink Sheet</b></div><div>原始單位：<b>'+esc(m.sourceUnit)+'</b></div><div>系統最後同步：<b>'+esc(sync)+'</b></div></div>';grid.appendChild(card);});
}
function clone(id){const old=document.getElementById(id),neu=old.cloneNode(true);old.replaceWith(neu);return neu;}
function chips(){const wrap=$('#materialChips');wrap.innerHTML='';materials.forEach(m=>{const b=document.createElement('button');b.type='button';b.dataset.id=m.id;b.textContent=m.zh+' '+m.en;b.className='chip'+(state.selected.has(m.id)?' selected':'');b.setAttribute('aria-pressed',state.selected.has(m.id));b.onclick=()=>{if(state.selected.has(m.id)){if(state.selected.size>1)state.selected.delete(m.id);}else state.selected.add(m.id);chips();draw();};wrap.appendChild(b);});}
function controls(){
 chips();
 const ps=clone('periodSeg');ps.onclick=e=>{const b=e.target.closest('button');if(!b)return;ps.querySelectorAll('button').forEach(x=>{x.classList.remove('active');x.setAttribute('aria-pressed','false');});b.classList.add('active');b.setAttribute('aria-pressed','true');state.period=parseInt(b.dataset.period,10);draw();};
 const ms=clone('modeSeg');ms.onclick=e=>{const b=e.target.closest('button');if(!b||b.disabled)return;state.mode=b.dataset.mode;draw();};
 const ex=clone('exportCsvBtn');ex.disabled=false;ex.onclick=exportCsv;
}
function dataset(){
 const selected=materials.filter(m=>state.selected.has(m.id)),mismatch=selected.length>1&&new Set(selected.map(m=>m.unit+'|'+m.ccy)).size>1,effective=mismatch?'index':state.mode,base=selected[0]||materials[0];state.effective=effective;
 const labels=base.series.slice(-state.period).map(p=>period(p.period));
 const sets=selected.map((m,i)=>{const series=m.series.slice(-state.period),start=series[0].price;return{label:m.zh+' '+m.en,data:series.map(p=>effective==='index'?p.price/start*100:p.price),borderColor:COLORS[i%COLORS.length],backgroundColor:COLORS[i%COLORS.length]+'22',borderWidth:2,pointRadius:0,pointHitRadius:8,tension:.15,fill:false,_mat:m,_series:series};});
 return{selected,mismatch,effective,labels,sets};
}
function modeUI(p){const a=$('#modeSeg button[data-mode="actual"]'),i=$('#modeSeg button[data-mode="index"]'),n=$('#modeNote');a.disabled=p.mismatch;a.classList.toggle('active',!p.mismatch&&p.effective==='actual');i.classList.toggle('active',p.mismatch||p.effective==='index');a.setAttribute('aria-pressed',a.classList.contains('active'));i.setAttribute('aria-pressed',i.classList.contains('active'));n.className=p.mismatch?'small-note warn-note':'small-note';n.textContent=p.mismatch?'⚠️ 所選材料單位或幣別不同，已自動使用指數化比較模式（起始值=100）。':'';}
function legend(p){const w=$('#chartLegend');w.innerHTML='';p.sets.forEach(d=>{const item=document.createElement('div');item.className='legend-item';item.innerHTML='<span class="legend-dot" style="background:'+d.borderColor+'"></span>';item.appendChild(document.createTextNode(d.label+'（'+(p.effective==='index'?'指數化（起始=100）':d._mat.ccy+'／'+d._mat.unit)+'）'));w.appendChild(item);});}
function stats(p){const w=$('#statRow');w.innerHTML='';p.sets.forEach(d=>{const v=d.data,rows=[['期間最高',Math.max(...v)],['期間最低',Math.min(...v)],['期間平均',v.reduce((a,b)=>a+b,0)/v.length]];rows.forEach(r=>{const box=document.createElement('div');box.className='stat-box';box.innerHTML='<div class="lbl">'+esc(d.label+'｜'+r[0])+'</div><div class="val">'+(p.effective==='index'?num(r[1],2)+' 指數點':num(r[1],Math.abs(r[1])<10?2:0)+' '+esc(d._mat.ccy)+'／'+esc(d._mat.unit))+'</div>';w.appendChild(box);});});}
function tooltip(ctx){const el=$('#chartTooltip'),t=ctx.tooltip;if(!t||t.opacity===0){el.style.opacity=0;return;}const idx=t.dataPoints[0].dataIndex;el.innerHTML=t.dataPoints.map(dp=>{const d=ctx.chart.data.datasets[dp.datasetIndex],o=d._series[idx],v=dp.raw;return'<div class="t-zh">'+esc(d.label)+'</div>月份：'+period(o.period)+'<br>'+(state.effective==='index'?'指數：'+num(v,2)+'點<br>':'價格：'+num(v,Math.abs(v)<10?2:0)+' '+esc(d._mat.ccy)+'<br>單位：'+esc(d._mat.unit)+'<br>')+'來源：World Bank Pink Sheet';}).join('');let x=t.caretX+12;if(x+240>ctx.chart.canvas.parentElement.getBoundingClientRect().width)x=t.caretX-250;el.style.left=x+'px';el.style.top=(t.caretY-10)+'px';el.style.opacity=1;}
function draw(){
 const p=dataset();csvPayload=p;modeUI(p);legend(p);stats(p);$('#chartSrSummary').textContent='World Bank月度價格圖表摘要：'+p.sets.map(d=>d.label+'從'+num(d.data[0],2)+'變化至'+num(d.data.at(-1),2)+(p.effective==='index'?'指數點':' '+d._mat.ccy)).join('；');$('#chartTitle').textContent=p.selected.length===1?p.selected[0].zh+'（'+p.selected[0].en+'）月度價格走勢':'多材料月度價格比較（'+(p.effective==='index'?'指數化，起始=100':'實際價格')+'）';
 const old=$('#priceChart');if(!old.dataset.live){const c=old.cloneNode(false);c.dataset.live='1';c.style.visibility='visible';old.replaceWith(c);}const canvas=$('#priceChart'),fb=$('#chartFallback');fb.classList.remove('show');canvas.style.display='block';try{if(chart)chart.destroy();chart=new Chart(canvas.getContext('2d'),{type:'line',data:{labels:p.labels,datasets:p.sets},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{display:false},tooltip:{enabled:false,external:tooltip}},scales:{x:{grid:{color:'#EEF1F4'},ticks:{color:'#8695A6',font:{size:10.5},maxRotation:0,autoSkip:true,maxTicksLimit:10}},y:{grid:{color:'#EEF1F4'},ticks:{color:'#8695A6',font:{size:10.5}},title:{display:true,text:p.effective==='index'?'指數（起始值=100）':'價格（單位依所選材料，詳見圖例）',color:'#8695A6',font:{size:10.5}}}}}});}catch(e){fb.textContent='圖表元件無法呈現World Bank資料，請重新整理頁面。';fb.classList.add('show');canvas.style.display='none';console.error(e);}
}
const csvText=v=>{let s=String(v);if(/^[=+\-@]/.test(s))s="'"+s;return'"'+s.replace(/"/g,'""')+'"';};
function exportCsv(){if(!csvPayload)return;const head=['材料中文','材料英文','月份','數值','顯示模式','幣別','顯示單位','原始單位','資料來源','是否為示範資料','資料頻率','資料集最新月份','系統同步時間'].map(csvText),rows=[];csvPayload.sets.forEach(d=>d._series.forEach((o,i)=>rows.push([csvText(d._mat.zh),csvText(d._mat.en),csvText(o.period),Number(d.data[i]).toFixed(4),csvText(csvPayload.effective==='index'?'指數化(起始=100)':'實際價格'),csvText(csvPayload.effective==='index'?'—':d._mat.ccy),csvText(csvPayload.effective==='index'?'指數點':d._mat.unit),csvText(d._mat.sourceUnit),csvText(d._mat.source),csvText('否'),csvText('月'),csvText(wb.dataset.latestPeriod),csvText(taipei(status.worldBank.lastSuccessAt||wb.generatedAt))])));const blob=new Blob(['\uFEFF'+[head,...rows].map(r=>r.join(',')).join('\n')],{type:'text/csv;charset=utf-8;'}),url=URL.createObjectURL(blob),a=document.createElement('a'),d=new Date();a.href=url;a.download='World_Bank_原材料月度價格_'+d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+'.csv';document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);}
function fail(e){console.error(e);document.title='國際原材料價格與採購分析平台｜資料載入失敗';$('.demo-pill').textContent='World Bank 資料載入失敗';$('.notice-banner').innerHTML='<b>正式市場資料未載入成功。</b> 為避免誤用，本頁已停止顯示價格；請重新整理並檢查GitHub Actions。';$('#cardGrid').innerHTML='<div class="panel" style="grid-column:1/-1;">無法載入已驗證的World Bank資料：'+esc(e.message||e)+'</div>';$('#priceChart').style.display='none';$('#chartFallback').textContent='正式市場資料載入失敗，因此不顯示圖表。';$('#chartFallback').classList.add('show');$('#exportCsvBtn').disabled=true;}
async function json(url){const r=await fetch(url,{cache:'no-store',headers:{Accept:'application/json'}});if(!r.ok)throw Error(url+' HTTP '+r.status);return r.json();}
async function init(){loading();try{[wb,status]=await Promise.all([json('./data/world-bank.json'),json('./data/status.json')]);validate(wb,status);makeMaterials();copy();cards();controls();draw();document.querySelectorAll('.sidenav button,.mobile-tabs button').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.target==='chart'&&chart)setTimeout(()=>{try{chart.resize();}catch(e){}},60);}));}catch(e){fail(e);}}
init();
})();
