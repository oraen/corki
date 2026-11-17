'use strict';
window.CorkiHealth = (() => {
 let root, timer, controller, toast, generation = 0;
 const labels = {waiting:'等待中',running:'进行中',normal:'状态正常',abnormal:'状态异常'};
 const escape = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 async function request(start=false) {
  const response = await fetch('/api/health',start?{method:'POST',signal:controller.signal,
   headers:{'Content-Type':'application/json','X-Corki-Manager':'1'},body:JSON.stringify({action:'start'})}
   :{cache:'no-store',signal:controller.signal});
  const result = await response.json();
  if(!response.ok)throw new Error(result.error||'无法读取检查结果');
  return result;
 }
 function render(data) {
  root.innerHTML = `<section class="panel health-panel"><div class="health-heading"><div><h2>健康检查</h2><p>检查本机数据库完整性与配置文件。</p></div><div class="health-actions"><button class="button" data-health="restart" ${data.running?'disabled':''}>重新检查</button><button class="button primary" data-health="repair" ${data.running?'disabled':''}>一键修复</button></div></div><div class="health-items" aria-live="polite">${data.items.map(item=>`<div class="health-item"><span class="health-mark ${item.status}" aria-hidden="true">${item.status==='running'?'<span class="spinner"></span>':item.status==='normal'?'✓':item.status==='abnormal'?'!':'·'}</span><div class="health-item-body"><strong>${escape(item.name)}</strong><p>${escape(item.detail|| (item.status==='running'?'正在检查，请稍候':'尚未开始检查'))}</p></div><span class="health-status ${item.status}">${labels[item.status]||labels.waiting}</span></div>`).join('')}</div><div class="health-summary">${data.running?'正在检查，您可以离开此页面。':data.items.some(i=>i.status==='abnormal')?'检查完成，发现异常项目。':'检查完成，所有项目状态正常。'}</div></section>`;
  root.querySelector('[data-health="repair"]').onclick=()=>toast('未实现功能');
  root.querySelector('[data-health="restart"]').onclick=()=>void check(true);
 }
 async function check(start=false) {
  const ticket=++generation;
  clearTimeout(timer);
  try {
   const data=await request(start);
   if(!root||ticket!==generation)return;
   render(data);
   if(data.running)timer=setTimeout(()=>void check(),500);
  }catch(error){
   if(error.name!=='AbortError'&&root&&ticket===generation){
    toast(error.message);
    root.innerHTML='<section class="panel health-panel"><h2>健康检查</h2><p>暂时无法读取检查结果，请重试。</p><button class="button" data-retry>重试</button></section>';
    root.querySelector('[data-retry]').onclick=()=>void check(true);
   }
  }
 }
 function unmount(){generation++;clearTimeout(timer);controller?.abort();root=null;}
 function mount(target,options){unmount();root=target;toast=options.toast;controller=new AbortController();root.innerHTML='<div class="loading"><span class="spinner"></span>正在准备检查…</div>';void check(true);}
 return {mount,unmount};
})();
