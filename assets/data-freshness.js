(function(){
'use strict';

function esc(v){return String(v==null?'—':v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function taipei(iso){
  if(!iso)return'—';
  const d=new Date(iso);
  if(Number.isNaN(d.getTime()))return String(iso);
  return new Intl.DateTimeFormat('zh-TW',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d);
}
function period(v){return /^\d{4}-\d{2}$/.test(String(v||''))?String(v).replace('-','/'):String(v||'—');}

function ownReleaseIdentity(release){
  const version=String(release&&release.version||'').trim();
  if(!version)return;
  const desired='International Raw Materials Procurement Analytics（Real Data + Procurement Decision Support v'+version+'）';
  const sub=document.querySelector('.brand .sub');
  if(!sub)return;
  const apply=function(){
    if(sub.textContent!==desired)sub.textContent=desired;
    document.documentElement.dataset.releaseVersion=version;
  };
  apply();
  const observer=new MutationObserver(apply);
  observer.observe(sub,{childList:true,characterData:true,subtree:true});
}

function render(status){
  const overview=document.getElementById('overview');
  const grid=document.getElementById('cardGrid');
  if(!overview||!grid)return;
  let panel=document.getElementById('dataFreshnessPanel');
  if(!panel){
    panel=document.createElement('div');
    panel.id='dataFreshnessPanel';
    panel.className='panel';
    panel.style.marginBottom='16px';
    grid.parentNode.insertBefore(panel,grid);
  }

  const wb=status.worldBank||{};
  const fred=status.fred||{};
  const isStale=status.isStale===true;
  const freshness=isStale?'⚠️ 資料可能過期':'✓ 同步狀態正常';
  const freshnessClass=isStale?'warn-note':'small-note';
  panel.innerHTML=
    '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;">'+
      '<div><h2 style="margin-bottom:6px;">資料新鮮度</h2><div class="'+freshnessClass+'">'+esc(freshness)+'</div></div>'+
      '<div style="display:grid;grid-template-columns:repeat(4,minmax(135px,1fr));gap:14px;flex:1;min-width:min(100%,620px);">'+
        '<div><div class="small-note">最新市場月份</div><b>'+esc(period(wb.latestPeriod||fred.latestCommonPeriod))+'</b></div>'+
        '<div><div class="small-note">World Bank 最後同步</div><b>'+esc(taipei(wb.lastSuccessAt))+'</b></div>'+
        '<div><div class="small-note">來源資料更新日</div><b>'+esc(wb.sourceUpdatedOn||'—')+'</b></div>'+
        '<div><div class="small-note">FRED 交叉核對</div><b>'+esc(fred.status||'—')+'</b></div>'+
      '</div>'+
    '</div>'+
    '<div class="small-note" style="margin-top:10px;">World Bank 為主要市場輸入；FRED 僅作獨立交叉核對。資料月份具發布時差，供應商議價仍需搭配成本占比、庫存、匯率、運費與合約條件。</div>';
}

async function json(path){
  const response=await fetch(path,{cache:'no-store',headers:{Accept:'application/json'}});
  if(!response.ok)throw new Error(path+' HTTP '+response.status);
  return response.json();
}

async function init(){
  try{
    const pair=await Promise.all([json('./config/release.json'),json('./data/status.json')]);
    ownReleaseIdentity(pair[0]);
    render(pair[1]);
  }catch(error){
    console.error('release identity / data freshness:',error);
  }
}

init();
})();
