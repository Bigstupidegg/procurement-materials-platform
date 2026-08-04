(function(){
'use strict';
const ORDER=['zinc','copper','aluminium','nickel','iron_ore','crude_oil','natural_gas'];
let signals=null,status=null,activeFilter='ALL';
const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const finite=v=>Number.isFinite(Number(v));
const number=(v,d)=>{if(!finite(v))return '—';const n=Number(v),digits=d??(Math.abs(n)<10?2:0);return n.toLocaleString('zh-Hant-TW',{minimumFractionDigits:digits,maximumFractionDigits:digits});};
const period=v=>/^\d{4}-\d{2}$/.test(String(v))?String(v).replace('-','/'):String(v||'—');
const signedPercent=v=>{if(!finite(v))return '—';const n=Number(v);return(n>0?'▲ +':n<0?'▼ ':'— ')+number(n,2)+'%';};
const direction=v=>Number(v)>0.0005?'up':Number(v)<-0.0005?'down':'flat';
const taipei=iso=>{const d=new Date(iso);return Number.isNaN(d.getTime())?String(iso||'—'):new Intl.DateTimeFormat('zh-TW',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d);};

function addNavigation(){
 const side=$('.sidenav'),mobile=$('.mobile-tabs');
 if(side&&!side.querySelector('[data-target="signals"]')){const b=document.createElement('button');b.type='button';b.dataset.target='signals';b.setAttribute('aria-pressed','false');b.innerHTML='<span class="ic">◆</span>採購議價訊號';side.insertBefore(b,side.querySelector('[data-target="comparison"]')||side.querySelector('[data-target="calc"]')||null);}
 if(mobile&&!mobile.querySelector('[data-target="signals"]')){const b=document.createElement('button');b.type='button';b.dataset.target='signals';b.setAttribute('aria-pressed','false');b.textContent='◆ 議價訊號';mobile.insertBefore(b,mobile.querySelector('[data-target="comparison"]')||mobile.querySelector('[data-target="calc"]')||null);}
 document.querySelectorAll('[data-target="signals"]').forEach(b=>b.addEventListener('click',()=>activateTab('signals')));
}
function addSection(){
 if($('#signals'))return;
 const section=document.createElement('section');section.className='section';section.id='signals';section.innerHTML=`
 <h1 class="page-title">材料趨勢與議價訊號</h1>
 <p class="page-desc">將World Bank月度價格轉成採購可執行的市場訊號；FRED僅用於來源一致性檢查，不會取代主要行情或自動決定供應商價格。</p>
 <div class="sig-policy" role="note">
  <div><span class="tag">TREND</span><b>World Bank決定市場方向</b><span>使用1、3、6、12個月變化與近12月價格位置。</span></div>
  <div><span class="tag compare">CORROBORATE</span><b>FRED只做交叉核對</b><span>來源差異不會改寫World Bank趨勢訊號。</span></div>
  <div><span class="tag limit">LIMIT</span><b>不等於供應商實際成本</b><span>仍須核對材料占比、庫存、採購落後期、匯率、運費與加工成本。</span></div>
 </div>
 <div class="panel sig-toolbar"><div class="sig-toolbar-row"><div class="field-block"><label id="sigFilterLabel">篩選訊號</label><div class="sig-filter-group" id="sigFilters" role="group" aria-labelledby="sigFilterLabel"><button type="button" data-filter="ALL" class="active" aria-pressed="true">全部</button><button type="button" data-filter="DOWN" aria-pressed="false">降價／挑戰漲價</button><button type="button" data-filter="UP" aria-pressed="false">上漲核實</button><button type="button" data-filter="MONITOR" aria-pressed="false">盤整觀察</button></div></div><div class="small-note sig-status" id="sigStatus" aria-live="polite">正在載入正式議價訊號…</div></div></div>
 <div class="sig-summary" id="sigSummary"><div class="sig-summary-box"><div class="label">資料載入中</div><div class="value">—</div></div></div>
 <div class="sig-grid" id="sigGrid"><div class="sig-empty">正式訊號資料載入中…</div></div>`;
 const main=$('.main');main.insertBefore(section,$('#comparison')||$('#calc')||null);
}
function activateTab(target){document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));const s=document.getElementById(target);if(s)s.classList.add('active');document.querySelectorAll('.sidenav button,.mobile-tabs button').forEach(b=>{const a=b.dataset.target===target;b.classList.toggle('active',a);b.setAttribute('aria-pressed',a?'true':'false');});}
function validate(){
 if(signals?.isRealData!==true||signals?.primarySource!=='WORLD_BANK_PINK_SHEET'||signals?.comparisonSource!=='FRED')throw Error('signals.json不是正式World Bank／FRED訊號資料');
 if(signals?.signalPolicy?.worldBankDeterminesTrend!==true||signals?.signalPolicy?.fredUsedForCorroborationOnly!==true||signals?.signalPolicy?.automaticSupplierPriceDecision!==false)throw Error('議價訊號政策不符合平台規則');
 if(status?.dataMode!=='WORLD_BANK_PRIMARY'||status?.worldBank?.status!=='SUCCESS')throw Error('status.json未顯示World Bank主資料成功');
 if(signals.latestPeriod!==status.worldBank.latestPeriod)throw Error('議價訊號月份與World Bank最新月份不一致');
 ORDER.forEach(id=>{const x=signals?.materials?.[id];if(!x||x.id!==id||!x.negotiationSignal||!x.trend||!x.changes)throw Error(id+'議價訊號結構不完整');['oneMonthPercent','threeMonthPercent','sixMonthPercent','twelveMonthPercent'].forEach(k=>{if(!finite(x.changes[k]))throw Error(id+'缺少'+k);});});
}
function groupFor(code){if(['NEGOTIATE_REDUCTION','CHALLENGE_INCREASE'].includes(code))return'DOWN';if(['VERIFY_STRONG_INCREASE','VERIFY_INCREASE'].includes(code))return'UP';return'MONITOR';}
function priorityLabel(p){return p==='HIGH'?'高優先':p==='MEDIUM'?'中優先':'一般觀察';}
function sourceLabel(s){const latest=finite(s.latestDifferencePercentVsWorldBank)?'；最新差異 '+signedPercent(s.latestDifferencePercentVsWorldBank):'';return s.label+latest;}
function changeBox(label,value){return`<div class="sig-change"><div class="label">${esc(label)}</div><div class="value ${direction(value)}">${signedPercent(value)}</div></div>`;}
function card(item){
 const signal=item.negotiationSignal,stats=item.twelveMonthStatistics,source=item.sourceCorroboration,priority=String(signal.priority||'LOW').toLowerCase(),position=Math.max(0,Math.min(100,Number(stats.rangePositionPercent)||0)),group=groupFor(signal.code);
 return`<article class="sig-card priority-${priority}" data-group="${group}"><div class="sig-card-head"><div class="sig-material"><div class="zh">${esc(item.nameZh)}</div><div class="en">${esc(item.nameEn)}</div></div><div class="sig-badges"><span class="sig-badge ${priority}">${esc(priorityLabel(signal.priority))}</span><span class="sig-badge">${esc(signal.label)}</span></div></div><div class="sig-latest"><span class="value">${number(item.latestValue)}</span><span class="unit">${esc(item.currency)}／${esc(item.displayUnit)}</span><span class="period">資料 ${period(item.latestPeriod)}</span></div><div class="sig-change-grid">${changeBox('1個月',item.changes.oneMonthPercent)}${changeBox('3個月',item.changes.threeMonthPercent)}${changeBox('6個月',item.changes.sixMonthPercent)}${changeBox('12個月',item.changes.twelveMonthPercent)}</div><div class="sig-range"><div class="sig-range-head"><span>${esc(stats.positionLabel)}</span><span>相對12月均價 ${signedPercent(stats.latestVsAveragePercent)}</span></div><div class="sig-range-track"><div class="sig-range-fill" style="width:${position.toFixed(1)}%"></div></div><div class="sig-range-meta"><span>低 ${number(stats.low)}</span><span>位置 ${number(position,1)}%</span><span>高 ${number(stats.high)}</span></div></div><div class="sig-block"><div class="title">市場趨勢</div><div class="headline">${esc(item.trend.label)}</div><p>${esc(item.trend.summary)}</p></div><div class="sig-block"><div class="title">訊號判讀</div><div class="headline">${esc(signal.marketInterpretation)}</div></div><div class="sig-action"><div class="title">建議採購動作</div><p>${esc(signal.recommendedAction)}</p></div><div class="sig-check"><b>核實重點：</b>${esc(signal.supplierClaimCheck)}</div><div class="sig-source ${String(source.confidence||'MEDIUM').toLowerCase()}"><span class="dot"></span><span><b>${esc(sourceLabel(source))}</b><br>${esc(source.note)}</span></div></article>`;
}
function renderSummary(){const counts={DOWN:0,UP:0,MONITOR:0,HIGH:0};ORDER.map(id=>signals.materials[id]).forEach(x=>{counts[groupFor(x.negotiationSignal.code)]++;if(x.negotiationSignal.priority==='HIGH')counts.HIGH++;});$('#sigSummary').innerHTML=`<div class="sig-summary-box"><div class="label">高優先訊號</div><div class="value">${counts.HIGH}</div><div class="detail">需優先進行供應商核實或議價</div></div><div class="sig-summary-box"><div class="label">降價／挑戰漲價</div><div class="value">${counts.DOWN}</div><div class="detail">市場方向偏弱</div></div><div class="sig-summary-box"><div class="label">上漲核實</div><div class="value">${counts.UP}</div><div class="detail">先驗證成本傳導</div></div><div class="sig-summary-box"><div class="label">盤整觀察</div><div class="value">${counts.MONITOR}</div><div class="detail">以條件與觸發門檻議價</div></div>`;}
function renderCards(){$('#sigGrid').innerHTML=ORDER.map(id=>card(signals.materials[id])).join('');applyFilter();}
function applyFilter(){let visible=0;document.querySelectorAll('.sig-card').forEach(c=>{const show=activeFilter==='ALL'||c.dataset.group===activeFilter;c.classList.toggle('hidden',!show);if(show)visible++;});let empty=$('#sigFilterEmpty');if(!visible){if(!empty){empty=document.createElement('div');empty.id='sigFilterEmpty';empty.className='sig-empty';empty.textContent='目前沒有符合此分類的材料訊號。';$('#sigGrid').appendChild(empty);}}else if(empty)empty.remove();}
function setupFilters(){$('#sigFilters').addEventListener('click',e=>{const b=e.target.closest('button[data-filter]');if(!b)return;activeFilter=b.dataset.filter;$('#sigFilters').querySelectorAll('button').forEach(x=>{const a=x===b;x.classList.toggle('active',a);x.setAttribute('aria-pressed',a?'true':'false');});applyFilter();});}
async function json(url){const r=await fetch(url,{cache:'no-store',headers:{Accept:'application/json'}});if(!r.ok)throw Error(url+' HTTP '+r.status);return r.json();}
function ready(){$('#sigStatus').innerHTML=`正式訊號已驗證；資料至 <b>${esc(period(signals.latestPeriod))}</b>，最後產生時間 <b>${esc(taipei(signals.generatedAt))}</b>。`;const sub=$('.brand .sub');if(sub)sub.textContent='International Raw Materials Procurement Analytics（採購訊號版 v1.5.0）';renderSummary();renderCards();}
function fail(e){console.error(e);$('#sigStatus').innerHTML=`<span class="up"><b>議價訊號載入失敗：</b>${esc(e.message||e)}</span>`;$('#sigSummary').innerHTML='<div class="sig-summary-box"><div class="label">訊號模組暫停</div><div class="value">—</div><div class="detail">World Bank主行情與其他模組不受影響</div></div>';$('#sigGrid').innerHTML='<div class="sig-empty">正式signals.json未通過驗證，因此不顯示議價訊號。</div>';}
async function init(){addNavigation();addSection();setupFilters();try{[signals,status]=await Promise.all([json('./data/signals.json'),json('./data/status.json')]);validate();ready();}catch(e){fail(e);}}
init();
})();
