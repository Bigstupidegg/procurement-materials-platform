(function(){
'use strict';

/* v2.3 shared application core.
   This file owns only Should-Cost calculation and navigation.
   Market cards, chart, tooltip, statistics and CSV are owned by a market module. */

function fmtNum(n, decimals){
  if(n===null||n===undefined||!isFinite(n)) return '—';
  const dec = decimals!==undefined ? decimals : (Math.abs(n)<10 ? 2 : 0);
  return Number(n).toLocaleString('zh-Hant-TW',{
    minimumFractionDigits:dec,
    maximumFractionDigits:dec
  });
}

function formatSigned(pct, decimals){
  const dec = decimals===undefined ? 2 : decimals;
  if(pct===null||pct===undefined||!isFinite(pct)) return '—';
  const rounded = Number(Number(pct).toFixed(dec));
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

const ERR_MAP = {
  f_price:'err_price', f_matRatio:'err_matRatio', f_matRate:'err_matRate',
  f_fxRatio:'err_fxRatio', f_fxRate:'err_fxRate',
  f_procRatio:'err_procRatio', f_procRate:'err_procRate',
  f_energyRatio:'err_energyRatio', f_energyRate:'err_energyRate',
  f_otherRatio:'err_otherRatio', f_otherRate:'err_otherRate',
  f_supplierAsk:'err_supplierAsk'
};

const INPUT_IDS = [
  'f_price','f_matRatio','f_matRate','f_fxRatio','f_fxRate',
  'f_procRatio','f_procRate','f_energyRatio','f_energyRate',
  'f_otherRatio','f_otherRate','f_supplierAsk'
];

function parseField(id){
  const el = document.getElementById(id);
  const raw = el.value.trim();
  if(raw==='') return {value:0,valid:true,el:el};
  const v = parseFloat(raw);
  if(isNaN(v)||!isFinite(v)) return {value:0,valid:false,el:el};
  return {value:v,valid:true,el:el};
}

function clearFieldStates(){
  Object.keys(ERR_MAP).forEach(function(id){
    const errEl=document.getElementById(ERR_MAP[id]);
    if(errEl) errEl.textContent='';
    const inputEl=document.getElementById(id);
    if(inputEl) inputEl.classList.remove('invalid');
  });
}

function setFieldError(id,msg){
  const errEl=document.getElementById(ERR_MAP[id]);
  const inputEl=document.getElementById(id);
  if(errEl) errEl.textContent=msg;
  if(inputEl) inputEl.classList.add('invalid');
}

function showErrorState(message){
  const hero=document.getElementById('resultHero');
  hero.classList.add('error-state');
  document.getElementById('resultRate').innerHTML='<span class="error-text">'+message+'</span>';
  document.getElementById('resultPrice').textContent='';
  ['impactMat','impactFx','impactProc','impactEnergy','impactOther'].forEach(function(id){
    const el=document.getElementById(id);
    el.textContent='—';
    el.style.color='';
  });
  document.getElementById('cmpEstimate').textContent='—';
  document.getElementById('cmpEstimate').style.color='';
  document.getElementById('cmpSupplier').textContent='—';
  document.getElementById('cmpSupplier').style.color='';
  document.getElementById('cmpGap').textContent='—';
  document.getElementById('formulaBox').textContent='無法計算，請先修正標示的輸入內容後再檢視計算過程。';
}

function setImpactDisplay(id,pct){
  const el=document.getElementById(id);
  el.textContent=formatSigned(pct);
  const cls=signColorClass(pct);
  el.style.color=cls==='up' ? 'var(--up)' : (cls==='down' ? 'var(--down)' : 'var(--text-1)');
}

function gapText(gap){
  if(Math.abs(gap)<0.005) return '0.00個百分點（供應商等於推估值）';
  const dir=gap>0 ? '高於' : '低於';
  const sign=gap>0 ? '+' : '';
  return sign+gap.toFixed(2)+'個百分點（供應商'+dir+'推估值）';
}

function validateAndCalc(){
  clearFieldStates();
  document.getElementById('resultHero').classList.remove('error-state');
  const warnEl=document.getElementById('ratioWarn');
  warnEl.classList.remove('show');

  const parsed={};
  let invalid=false;
  INPUT_IDS.forEach(function(id){
    const p=parseField(id);
    parsed[id]=p;
    if(!p.valid){
      setFieldError(id,'請輸入有效數字（不可為空白以外的無效值、NaN 或 Infinity）。');
      invalid=true;
    }
  });
  if(invalid){
    showErrorState('無法計算，請先修正標示的輸入內容。');
    return;
  }

  const price=parsed.f_price.value;
  if(price<0){
    setFieldError('f_price','產品單價不得小於 0。');
    invalid=true;
  }

  [
    ['f_matRatio','原材料成本占比'],['f_procRatio','加工成本占比'],
    ['f_energyRatio','能源成本占比'],['f_otherRatio','其他成本占比'],
    ['f_fxRatio','外幣曝險占總成本比例']
  ].forEach(function(pair){
    const v=parsed[pair[0]].value;
    if(v<0||v>100){
      setFieldError(pair[0],pair[1]+'須介於 0% 至 100% 之間，目前輸入 '+v+'%。');
      invalid=true;
    }
  });

  let rateMinInvalid=false;
  [
    ['f_matRate','原材料價格變化率'],['f_fxRate','匯率變化率'],
    ['f_procRate','加工成本變化率'],['f_energyRate','能源成本變化率'],
    ['f_otherRate','其他成本變化率'],['f_supplierAsk','供應商要求漲幅']
  ].forEach(function(pair){
    const v=parsed[pair[0]].value;
    if(v < -100){
      setFieldError(pair[0],pair[1]+'不得低於 -100%，目前輸入 '+v+'%。');
      invalid=true;
      rateMinInvalid=true;
    }
  });

  const matRatio=parsed.f_matRatio.value;
  const procRatio=parsed.f_procRatio.value;
  const energyRatio=parsed.f_energyRatio.value;
  const otherRatio=parsed.f_otherRatio.value;
  const fxRatio=parsed.f_fxRatio.value;
  const baseRatioSum=matRatio+procRatio+energyRatio+otherRatio;
  if(baseRatioSum>100){
    warnEl.classList.add('show');
    invalid=true;
  }

  if(invalid){
    showErrorState(rateMinInvalid ? '無法計算，變化率不得低於-100%。' : '無法計算，請先修正標示的輸入內容。');
    return;
  }

  const matRate=parsed.f_matRate.value;
  const fxRate=parsed.f_fxRate.value;
  const procRate=parsed.f_procRate.value;
  const energyRate=parsed.f_energyRate.value;
  const otherRate=parsed.f_otherRate.value;
  const supplierAsk=parsed.f_supplierAsk.value;

  const impMat=(matRatio/100)*(matRate/100);
  const impFx=(fxRatio/100)*(fxRate/100);
  const impProc=(procRatio/100)*(procRate/100);
  const impEnergy=(energyRatio/100)*(energyRate/100);
  const impOther=(otherRatio/100)*(otherRate/100);
  const totalRate=impMat+impFx+impProc+impEnergy+impOther;
  const newPrice=price*(1+totalRate);
  const gap=supplierAsk-(totalRate*100);

  const computed=[impMat,impFx,impProc,impEnergy,impOther,totalRate,newPrice,gap,totalRate*100];
  if(!computed.every(Number.isFinite)){
    showErrorState('無法計算，輸入數值過大或計算結果超出可處理範圍。');
    document.getElementById('formulaBox').textContent='請降低輸入數值後重新計算。';
    return;
  }
  if(newPrice<0){
    showErrorState('無法計算，目前輸入情境將導致新價格為負數，情境不合理。');
    return;
  }

  setImpactDisplay('impactMat',impMat*100);
  setImpactDisplay('impactFx',impFx*100);
  setImpactDisplay('impactProc',impProc*100);
  setImpactDisplay('impactEnergy',impEnergy*100);
  setImpactDisplay('impactOther',impOther*100);

  const totalPct=totalRate*100;
  document.getElementById('resultRate').innerHTML=
    '<span style="color:'+(signColorClass(totalPct)==='up'?'var(--up)':(signColorClass(totalPct)==='down'?'var(--down)':'inherit'))+'">'+formatSigned(totalPct)+'</span>';
  document.getElementById('resultPrice').textContent=
    '推估合理新價格：'+fmtNum(newPrice,2)+' 元（原單價 '+fmtNum(price,2)+' 元）';
  document.getElementById('cmpEstimate').textContent=formatSigned(totalPct);
  document.getElementById('cmpEstimate').style.color=signColorClass(totalPct)==='up'?'var(--up)':(signColorClass(totalPct)==='down'?'var(--down)':'var(--text-1)');
  document.getElementById('cmpSupplier').textContent=formatSigned(supplierAsk);
  document.getElementById('cmpSupplier').style.color=signColorClass(supplierAsk)==='up'?'var(--up)':(signColorClass(supplierAsk)==='down'?'var(--down)':'var(--text-1)');
  document.getElementById('cmpGap').textContent=gapText(gap);
  document.getElementById('cmpGap').style.color=Math.abs(gap)<0.005?'var(--text-1)':(gap>0?'var(--up)':'var(--down)');

  document.getElementById('formulaBox').textContent=
'推估合理漲幅 = 原材料影響 + 匯率影響 + 加工影響 + 能源影響 + 其他影響\n'+
'           = ('+matRatio+'% × '+matRate+'%) + ('+fxRatio+'% × '+fxRate+'%) + ('+procRatio+'% × '+procRate+'%)\n'+
'           + ('+energyRatio+'% × '+energyRate+'%) + ('+otherRatio+'% × '+otherRate+'%)\n'+
'           = '+totalPct.toFixed(2)+'%\n\n'+
'推估合理新價格 = 產品目前單價 × (1 + 推估合理漲幅)\n'+
'             = '+fmtNum(price,2)+' × (1 + '+totalRate.toFixed(4)+')\n'+
'             = '+fmtNum(newPrice,2)+' 元\n\n'+
'基本成本占比合計（不含外幣曝險） = '+matRatio+'% + '+procRatio+'% + '+energyRatio+'% + '+otherRatio+'% = '+baseRatioSum.toFixed(1)+'%';
}

INPUT_IDS.forEach(function(id){
  document.getElementById(id).addEventListener('input',validateAndCalc);
});

document.getElementById('resetBtn').addEventListener('click',function(){
  const defaults={
    f_price:1000,f_matRatio:35,f_matRate:6,f_fxRatio:15,f_fxRate:2,
    f_procRatio:25,f_procRate:3,f_energyRatio:10,f_energyRate:4,
    f_otherRatio:15,f_otherRate:0,f_supplierAsk:8
  };
  Object.keys(defaults).forEach(function(id){document.getElementById(id).value=defaults[id];});
  validateAndCalc();
});
validateAndCalc();

function switchTab(target){
  document.querySelectorAll('.section').forEach(function(s){s.classList.remove('active');});
  document.getElementById(target).classList.add('active');
  document.querySelectorAll('.sidenav button, .mobile-tabs button').forEach(function(b){
    const active=b.dataset.target===target;
    b.classList.toggle('active',active);
    b.setAttribute('aria-pressed',active?'true':'false');
  });
  if(target==='chart'){
    setTimeout(function(){window.dispatchEvent(new CustomEvent('procurement:chart-visible'));},50);
  }
}

document.querySelectorAll('.sidenav button, .mobile-tabs button').forEach(function(btn){
  btn.addEventListener('click',function(){switchTab(btn.dataset.target);});
});

window.procurementAppCore={validateAndCalc:validateAndCalc,switchTab:switchTab};
})();
