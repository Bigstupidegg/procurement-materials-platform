(function(){
'use strict';

const ORDER=['zinc','copper','aluminium','nickel','iron_ore','crude_oil','natural_gas'];
const CARRY_PERIODS={
  oneMonthPercent:{label:'1個月',shortLabel:'1M'},
  threeMonthPercent:{label:'3個月',shortLabel:'3M'},
  sixMonthPercent:{label:'6個月',shortLabel:'6M'},
  twelveMonthPercent:{label:'12個月',shortLabel:'12M'}
};

let signals=null;
let status=null;
let activeFilter='ALL';
let carryPeriodKey='sixMonthPercent';
let lastTransfer=null;

const $=selector=>document.querySelector(selector);
const esc=value=>String(value??'')
  .replace(/&/g,'&amp;')
  .replace(/</g,'&lt;')
  .replace(/>/g,'&gt;')
  .replace(/"/g,'&quot;')
  .replace(/'/g,'&#39;');
const finite=value=>Number.isFinite(Number(value));
const number=(value,decimals)=>{
  if(!finite(value))return '—';
  const numeric=Number(value);
  const digits=decimals??(Math.abs(numeric)<10?2:0);
  return numeric.toLocaleString('zh-Hant-TW',{
    minimumFractionDigits:digits,
    maximumFractionDigits:digits
  });
};
const period=value=>/^\d{4}-\d{2}$/.test(String(value))
  ?String(value).replace('-','/')
  :String(value||'—');
const signedPercent=value=>{
  if(!finite(value))return '—';
  const numeric=Number(value);
  return (numeric>0?'▲ +':numeric<0?'▼ ':'— ')+number(numeric,2)+'%';
};
const direction=value=>Number(value)>0.0005?'up':Number(value)<-0.0005?'down':'flat';
const taipei=iso=>{
  const date=new Date(iso);
  return Number.isNaN(date.getTime())
    ?String(iso||'—')
    :new Intl.DateTimeFormat('zh-TW',{
      timeZone:'Asia/Taipei',
      year:'numeric',
      month:'2-digit',
      day:'2-digit',
      hour:'2-digit',
      minute:'2-digit',
      hour12:false
    }).format(date);
};

function addNavigation(){
  const side=$('.sidenav');
  const mobile=$('.mobile-tabs');
  if(side&&!side.querySelector('[data-target="signals"]')){
    const button=document.createElement('button');
    button.type='button';
    button.dataset.target='signals';
    button.setAttribute('aria-pressed','false');
    button.innerHTML='<span class="ic">◆</span>採購議價訊號';
    side.insertBefore(
      button,
      side.querySelector('[data-target="comparison"]')||
      side.querySelector('[data-target="calc"]')||
      null
    );
  }
  if(mobile&&!mobile.querySelector('[data-target="signals"]')){
    const button=document.createElement('button');
    button.type='button';
    button.dataset.target='signals';
    button.setAttribute('aria-pressed','false');
    button.textContent='◆ 議價訊號';
    mobile.insertBefore(
      button,
      mobile.querySelector('[data-target="comparison"]')||
      mobile.querySelector('[data-target="calc"]')||
      null
    );
  }
  document.querySelectorAll('[data-target="signals"]').forEach(button=>{
    button.addEventListener('click',()=>activateTab('signals'));
  });
}

function addSection(){
  if($('#signals'))return;
  const section=document.createElement('section');
  section.className='section';
  section.id='signals';
  section.innerHTML=`
    <h1 class="page-title">材料趨勢與議價訊號</h1>
    <p class="page-desc">將World Bank月度價格轉成採購可執行的市場訊號；可選擇1、3、6或12個月變化率，一鍵帶入Should-Cost試算器。FRED僅用於來源一致性檢查。</p>

    <div class="sig-policy" role="note">
      <div><span class="tag">TREND</span><b>World Bank決定市場方向</b><span>使用1、3、6、12個月變化與近12月價格位置。</span></div>
      <div><span class="tag compare">CORROBORATE</span><b>FRED只做交叉核對</b><span>來源差異不會改寫World Bank趨勢訊號。</span></div>
      <div><span class="tag limit">LIMIT</span><b>不等於供應商實際成本</b><span>仍須核對材料占比、庫存、採購落後期、匯率、運費與加工成本。</span></div>
    </div>

    <div class="panel sig-toolbar">
      <div class="sig-toolbar-row">
        <div class="field-block">
          <label id="sigFilterLabel">篩選訊號</label>
          <div class="sig-filter-group" id="sigFilters" role="group" aria-labelledby="sigFilterLabel">
            <button type="button" data-filter="ALL" class="active" aria-pressed="true">全部</button>
            <button type="button" data-filter="DOWN" aria-pressed="false">降價／挑戰漲價</button>
            <button type="button" data-filter="UP" aria-pressed="false">上漲核實</button>
            <button type="button" data-filter="MONITOR" aria-pressed="false">盤整觀察</button>
          </div>
        </div>
        <div class="field-block">
          <label id="sigCarryPeriodLabel">帶入Should-Cost期間</label>
          <div class="seg sig-carry-period" id="sigCarryPeriod" role="group" aria-labelledby="sigCarryPeriodLabel">
            <button type="button" data-carry-key="oneMonthPercent" aria-pressed="false">1M</button>
            <button type="button" data-carry-key="threeMonthPercent" aria-pressed="false">3M</button>
            <button type="button" data-carry-key="sixMonthPercent" class="active" aria-pressed="true">6M</button>
            <button type="button" data-carry-key="twelveMonthPercent" aria-pressed="false">12M</button>
          </div>
        </div>
        <div class="small-note sig-status" id="sigStatus" aria-live="polite">正在載入正式議價訊號…</div>
      </div>
    </div>

    <div class="sig-summary" id="sigSummary">
      <div class="sig-summary-box"><div class="label">資料載入中</div><div class="value">—</div></div>
    </div>
    <div class="sig-grid" id="sigGrid"><div class="sig-empty">正式訊號資料載入中…</div></div>`;
  const main=$('.main');
  main.insertBefore(section,$('#comparison')||$('#calc')||null);
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
}

function validate(){
  if(
    signals?.isRealData!==true||
    signals?.primarySource!=='WORLD_BANK_PINK_SHEET'||
    signals?.comparisonSource!=='FRED'
  )throw Error('signals.json不是正式World Bank／FRED訊號資料');

  if(
    signals?.signalPolicy?.worldBankDeterminesTrend!==true||
    signals?.signalPolicy?.fredUsedForCorroborationOnly!==true||
    signals?.signalPolicy?.automaticSupplierPriceDecision!==false
  )throw Error('議價訊號政策不符合平台規則');

  if(status?.dataMode!=='WORLD_BANK_PRIMARY'||status?.worldBank?.status!=='SUCCESS'){
    throw Error('status.json未顯示World Bank主資料成功');
  }
  if(signals.latestPeriod!==status.worldBank.latestPeriod){
    throw Error('議價訊號月份與World Bank最新月份不一致');
  }

  ORDER.forEach(id=>{
    const item=signals?.materials?.[id];
    if(!item||item.id!==id||!item.negotiationSignal||!item.trend||!item.changes){
      throw Error(id+'議價訊號結構不完整');
    }
    Object.keys(CARRY_PERIODS).forEach(key=>{
      if(!finite(item.changes[key]))throw Error(id+'缺少'+key);
      if(Number(item.changes[key])<-100)throw Error(id+'的'+key+'低於試算器可接受範圍');
    });
  });
}

function groupFor(code){
  if(['NEGOTIATE_REDUCTION','CHALLENGE_INCREASE'].includes(code))return'DOWN';
  if(['VERIFY_STRONG_INCREASE','VERIFY_INCREASE'].includes(code))return'UP';
  return'MONITOR';
}

function priorityLabel(priority){
  return priority==='HIGH'?'高優先':priority==='MEDIUM'?'中優先':'一般觀察';
}

function sourceLabel(source){
  const latest=finite(source.latestDifferencePercentVsWorldBank)
    ?'；最新差異 '+signedPercent(source.latestDifferencePercentVsWorldBank)
    :'';
  return source.label+latest;
}

function changeBox(label,value){
  return`<div class="sig-change"><div class="label">${esc(label)}</div><div class="value ${direction(value)}">${signedPercent(value)}</div></div>`;
}

function card(item){
  const signal=item.negotiationSignal;
  const stats=item.twelveMonthStatistics;
  const source=item.sourceCorroboration;
  const priority=String(signal.priority||'LOW').toLowerCase();
  const position=Math.max(0,Math.min(100,Number(stats.rangePositionPercent)||0));
  const group=groupFor(signal.code);
  const carryMeta=CARRY_PERIODS[carryPeriodKey];
  return`
    <article class="sig-card priority-${priority}" data-group="${group}" data-material-id="${esc(item.id)}">
      <div class="sig-card-head">
        <div class="sig-material"><div class="zh">${esc(item.nameZh)}</div><div class="en">${esc(item.nameEn)}</div></div>
        <div class="sig-badges">
          <span class="sig-badge ${priority}">${esc(priorityLabel(signal.priority))}</span>
          <span class="sig-badge">${esc(signal.label)}</span>
        </div>
      </div>
      <div class="sig-latest">
        <span class="value">${number(item.latestValue)}</span>
        <span class="unit">${esc(item.currency)}／${esc(item.displayUnit)}</span>
        <span class="period">資料 ${period(item.latestPeriod)}</span>
      </div>
      <div class="sig-change-grid">
        ${changeBox('1個月',item.changes.oneMonthPercent)}
        ${changeBox('3個月',item.changes.threeMonthPercent)}
        ${changeBox('6個月',item.changes.sixMonthPercent)}
        ${changeBox('12個月',item.changes.twelveMonthPercent)}
      </div>
      <div class="sig-carry">
        <div class="sig-carry-copy">
          <b>帶入Should-Cost試算器</b>
          <span>只更新「原材料價格變化率」，其餘成本欄位維持目前輸入值。</span>
        </div>
        <button class="btn primary sig-carry-btn" type="button" data-carry-material="${esc(item.id)}">帶入${esc(carryMeta.label)}變化率</button>
      </div>
      <div class="sig-range">
        <div class="sig-range-head"><span>${esc(stats.positionLabel)}</span><span>相對12月均價 ${signedPercent(stats.latestVsAveragePercent)}</span></div>
        <div class="sig-range-track"><div class="sig-range-fill" style="width:${position.toFixed(1)}%"></div></div>
        <div class="sig-range-meta"><span>低 ${number(stats.low)}</span><span>位置 ${number(position,1)}%</span><span>高 ${number(stats.high)}</span></div>
      </div>
      <div class="sig-block">
        <div class="title">市場趨勢</div>
        <div class="headline">${esc(item.trend.label)}</div>
        <p>${esc(item.trend.summary)}</p>
      </div>
      <div class="sig-block">
        <div class="title">訊號判讀</div>
        <div class="headline">${esc(signal.marketInterpretation)}</div>
      </div>
      <div class="sig-action">
        <div class="title">建議採購動作</div>
        <p>${esc(signal.recommendedAction)}</p>
      </div>
      <div class="sig-check"><b>核實重點：</b>${esc(signal.supplierClaimCheck)}</div>
      <div class="sig-source ${String(source.confidence||'MEDIUM').toLowerCase()}">
        <span class="dot"></span>
        <span><b>${esc(sourceLabel(source))}</b><br>${esc(source.note)}</span>
      </div>
    </article>`;
}

function renderSummary(){
  const counts={DOWN:0,UP:0,MONITOR:0,HIGH:0};
  ORDER.map(id=>signals.materials[id]).forEach(item=>{
    counts[groupFor(item.negotiationSignal.code)]++;
    if(item.negotiationSignal.priority==='HIGH')counts.HIGH++;
  });
  $('#sigSummary').innerHTML=`
    <div class="sig-summary-box"><div class="label">高優先訊號</div><div class="value">${counts.HIGH}</div><div class="detail">需優先進行供應商核實或議價</div></div>
    <div class="sig-summary-box"><div class="label">降價／挑戰漲價</div><div class="value">${counts.DOWN}</div><div class="detail">市場方向偏弱</div></div>
    <div class="sig-summary-box"><div class="label">上漲核實</div><div class="value">${counts.UP}</div><div class="detail">先驗證成本傳導</div></div>
    <div class="sig-summary-box"><div class="label">盤整觀察</div><div class="value">${counts.MONITOR}</div><div class="detail">以條件與觸發門檻議價</div></div>`;
}

function renderCards(){
  $('#sigGrid').innerHTML=ORDER.map(id=>card(signals.materials[id])).join('');
  applyFilter();
  updateCarryButtonLabels();
}

function applyFilter(){
  let visible=0;
  document.querySelectorAll('.sig-card').forEach(cardElement=>{
    const show=activeFilter==='ALL'||cardElement.dataset.group===activeFilter;
    cardElement.classList.toggle('hidden',!show);
    if(show)visible++;
  });
  let empty=$('#sigFilterEmpty');
  if(!visible){
    if(!empty){
      empty=document.createElement('div');
      empty.id='sigFilterEmpty';
      empty.className='sig-empty';
      empty.textContent='目前沒有符合此分類的材料訊號。';
      $('#sigGrid').appendChild(empty);
    }
  }else if(empty){
    empty.remove();
  }
}

function updateCarryButtonLabels(){
  const meta=CARRY_PERIODS[carryPeriodKey];
  document.querySelectorAll('.sig-carry-btn').forEach(button=>{
    button.textContent=`帶入${meta.label}變化率`;
    button.title=`將${meta.label}World Bank材料變化率帶入Should-Cost試算器`;
  });
  const group=$('#sigCarryPeriod');
  if(group){
    group.querySelectorAll('button[data-carry-key]').forEach(button=>{
      const active=button.dataset.carryKey===carryPeriodKey;
      button.classList.toggle('active',active);
      button.setAttribute('aria-pressed',active?'true':'false');
    });
  }
}

function ensureCalculatorContext(){
  let context=$('#calcMarketContext');
  if(context)return context;
  const calc=$('#calc');
  const description=calc?.querySelector('.page-desc');
  if(!calc||!description)return null;
  context=document.createElement('div');
  context.id='calcMarketContext';
  context.className='calc-market-context';
  context.setAttribute('aria-live','polite');
  context.innerHTML=`
    <div class="calc-market-context-head">
      <div>
        <span class="calc-market-context-state" id="calcMarketContextState">已由訊號卡帶入</span>
        <b id="calcMarketContextTitle">市場資料來源</b>
      </div>
      <span class="calc-market-context-period" id="calcMarketContextPeriod">—</span>
    </div>
    <div class="calc-market-context-grid">
      <div><span>材料</span><b id="calcMarketContextMaterial">—</b></div>
      <div><span>採用期間</span><b id="calcMarketContextWindow">—</b></div>
      <div><span>帶入變化率</span><b id="calcMarketContextValue">—</b></div>
      <div><span>價格來源</span><b>World Bank Pink Sheet</b></div>
    </div>
    <p id="calcMarketContextNote">本次只更新「原材料價格變化率」；材料成本占比、供應商要求漲幅、匯率、加工、能源與其他成本維持原值。FRED僅作交叉核對。</p>`;
  description.insertAdjacentElement('afterend',context);
  return context;
}

function renderCalculatorContext(){
  const context=ensureCalculatorContext();
  if(!context||!lastTransfer)return;
  const input=$('#f_matRate');
  const current=Number(input?.value);
  const changed=!finite(current)||Math.abs(current-lastTransfer.appliedValue)>0.000001;
  context.classList.add('show');
  context.classList.toggle('manually-changed',changed);
  $('#calcMarketContextState').textContent=changed?'已手動修改':'已由訊號卡帶入';
  $('#calcMarketContextTitle').textContent=changed
    ?'原始市場資料紀錄（目前欄位已修改）'
    :'Should-Cost市場資料已套用';
  $('#calcMarketContextPeriod').textContent='資料 '+period(lastTransfer.latestPeriod);
  $('#calcMarketContextMaterial').textContent=`${lastTransfer.nameZh} ${lastTransfer.nameEn}`;
  $('#calcMarketContextWindow').textContent=CARRY_PERIODS[lastTransfer.periodKey].label;
  $('#calcMarketContextValue').textContent=signedPercent(lastTransfer.appliedValue);
  const field=input?.closest('.field');
  if(field)field.classList.toggle('market-linked',!changed);
}

function clearCalculatorContext(){
  lastTransfer=null;
  const context=$('#calcMarketContext');
  if(context){
    context.classList.remove('show','manually-changed');
  }
  const field=$('#f_matRate')?.closest('.field');
  if(field)field.classList.remove('market-linked');
}

function transferToCalculator(materialId){
  const item=signals?.materials?.[materialId];
  const value=item?.changes?.[carryPeriodKey];
  if(!item||!finite(value))throw Error('選定材料缺少可帶入的變化率');
  if(Number(value)<-100)throw Error('選定變化率低於Should-Cost試算器可接受範圍');

  const appliedValue=Math.round(Number(value)*100)/100;
  const materialRateInput=$('#f_matRate');
  if(!materialRateInput)throw Error('找不到Should-Cost原材料價格變化率欄位');

  lastTransfer={
    materialId:item.id,
    nameZh:item.nameZh,
    nameEn:item.nameEn,
    periodKey:carryPeriodKey,
    latestPeriod:item.latestPeriod,
    rawValue:Number(value),
    appliedValue
  };

  materialRateInput.value=String(appliedValue);
  materialRateInput.dispatchEvent(new Event('input',{bubbles:true}));
  renderCalculatorContext();
  activateTab('calc');

  window.setTimeout(()=>{
    const context=$('#calcMarketContext');
    if(context)context.scrollIntoView({behavior:'smooth',block:'start'});
    try{materialRateInput.focus({preventScroll:true});}catch(error){materialRateInput.focus();}
  },60);
}

function setupInteractions(){
  $('#sigFilters').addEventListener('click',event=>{
    const button=event.target.closest('button[data-filter]');
    if(!button)return;
    activeFilter=button.dataset.filter;
    $('#sigFilters').querySelectorAll('button').forEach(item=>{
      const active=item===button;
      item.classList.toggle('active',active);
      item.setAttribute('aria-pressed',active?'true':'false');
    });
    applyFilter();
  });

  $('#sigCarryPeriod').addEventListener('click',event=>{
    const button=event.target.closest('button[data-carry-key]');
    if(!button||!CARRY_PERIODS[button.dataset.carryKey])return;
    carryPeriodKey=button.dataset.carryKey;
    updateCarryButtonLabels();
  });

  $('#sigGrid').addEventListener('click',event=>{
    const button=event.target.closest('button[data-carry-material]');
    if(!button)return;
    try{
      transferToCalculator(button.dataset.carryMaterial);
    }catch(error){
      console.error(error);
      $('#sigStatus').innerHTML=`<span class="up"><b>帶入試算器失敗：</b>${esc(error.message||error)}</span>`;
    }
  });

  const materialRateInput=$('#f_matRate');
  if(materialRateInput){
    materialRateInput.addEventListener('input',()=>{
      if(lastTransfer)renderCalculatorContext();
    });
  }
  const resetButton=$('#resetBtn');
  if(resetButton)resetButton.addEventListener('click',clearCalculatorContext);
}

async function json(url){
  const response=await fetch(url,{cache:'no-store',headers:{Accept:'application/json'}});
  if(!response.ok)throw Error(url+' HTTP '+response.status);
  return response.json();
}

function ready(){
  $('#sigStatus').innerHTML=`正式訊號已驗證；資料至 <b>${esc(period(signals.latestPeriod))}</b>，最後產生時間 <b>${esc(taipei(signals.generatedAt))}</b>。預設帶入6個月變化率。`;
  const sub=$('.brand .sub');
  if(sub)sub.textContent='International Raw Materials Procurement Analytics（Should-Cost串接版 v1.6.0）';
  renderSummary();
  renderCards();
  ensureCalculatorContext();
}

function fail(error){
  console.error(error);
  $('#sigStatus').innerHTML=`<span class="up"><b>議價訊號載入失敗：</b>${esc(error.message||error)}</span>`;
  $('#sigSummary').innerHTML='<div class="sig-summary-box"><div class="label">訊號模組暫停</div><div class="value">—</div><div class="detail">World Bank主行情與其他模組不受影響</div></div>';
  $('#sigGrid').innerHTML='<div class="sig-empty">正式signals.json未通過驗證，因此不顯示議價訊號。</div>';
}

async function init(){
  addNavigation();
  addSection();
  setupInteractions();
  try{
    [signals,status]=await Promise.all([
      json('./data/signals.json'),
      json('./data/status.json')
    ]);
    validate();
    ready();
  }catch(error){
    fail(error);
  }
}

init();
})();
