(function(){
'use strict';

/* v2.3 source/local development bootstrap only.
   Production GitHub Pages never ships or executes this file.
   scripts/prepare_site.py replaces this bootstrap with app-core.js and
   removes demo-market.js from the production artifact. */

function loadScript(src){
  return new Promise(function(resolve,reject){
    const script=document.createElement('script');
    script.src=src;
    script.onload=resolve;
    script.onerror=function(){reject(new Error('無法載入 '+src));};
    document.body.appendChild(script);
  });
}

async function initDevelopmentMode(){
  try{
    await loadScript('./assets/app-core.js');
    await loadScript('./assets/demo-market.js');
    document.documentElement.dataset.runtimeMode='development-demo';
  }catch(error){
    console.error(error);
    const notice=document.querySelector('.notice-banner');
    if(notice){
      notice.innerHTML='<b>Development Demo 載入失敗。</b> 請檢查 app-core.js 與 demo-market.js。';
    }
  }
}

initDevelopmentMode();
})();
