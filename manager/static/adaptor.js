/* Subscription forwarding UI. Process control stays in the independent gateway. */
window.CorkiAdaptor = (() => {
 let root=null, host=null, data=null, timer=null, loading=false, alive=0, request=0, progress=null;
 const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const glyph='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4"/></svg>';
 function draw(){
  if(!root||!data)return;
  root.innerHTML=`<section class="panel adaptor-gateway"><div class="adaptor-heading"><span class="adaptor-glyph">${glyph}</span><div class="adaptor-title"><h2>适配网关</h2><p>将本机已登录的模型套餐接入 Corki</p></div><div class="adaptor-runtime"><span class="adaptor-status ${data.running?'online':''}"><i></i><span>${data.running?'开启':'关闭'}</span></span><span class="adaptor-runtime-divider" aria-hidden="true"></span><div class="adaptor-port-readout"><span>端口</span><strong data-gateway-port aria-label="服务端口">${data.running?data.port:'—'}</strong></div><button class="button ${data.running?'':'primary'}" data-power>${data.running?'关闭网关':'开启网关'}</button></div></div>${data.error?`<div class="adaptor-warning">${escape(data.error)}</div>`:''}</section><section class="panel adaptor-providers"><div class="panel-head"><h2>供应商转发</h2></div><div class="adaptor-provider-grid">${data.providers.map(p=>`<article class="adaptor-provider"><div class="adaptor-provider-heading"><span class="adaptor-provider-logo"><img src="/provider-icons/${escape(p.id)}.png" alt="" width="32" height="32"></span><div><h3>${escape(p.name)}</h3><span>本机 Codex 登录</span></div><span class="adaptor-provider-switch"><button type="button" class="toggle ${p.enabled?'on':''}" role="switch" aria-label="${escape(p.name)} 套餐转发" aria-checked="${p.enabled}" data-provider="${escape(p.id)}"></button></span></div><div class="adaptor-address"><span>转发地址</span><code data-provider-address="${escape(p.id)}">${p.configured||data.running?escape(p.base_url):'开启后自动生成'}</code></div></article>`).join('')}</div></section>`;
  syncProviderSwitches();
  root.querySelector('[data-power]').onclick=()=>void change({action:'power',enabled:!data.running},data.running?'正在关闭网关…':'正在开启网关…');
  for(const button of root.querySelectorAll('[data-provider]'))button.onclick=()=>void change({action:'provider',provider:button.dataset.provider,enabled:button.getAttribute('aria-checked')!=='true'},button.getAttribute('aria-checked')==='true'?'正在关闭转发…':'正在检查登录并配置转发…');
 }
 function syncProviderSwitches(){
  for(const p of data.providers){
   const button=root.querySelector(`[data-provider="${p.id}"]`),wrapper=button?.parentElement;
   if(!button||!wrapper)continue;
   const blocked=!data.running&&!p.enabled;
   button.disabled=blocked;
   wrapper.classList.toggle('blocked',blocked);
   if(blocked){wrapper.dataset.tooltip='需要先开启适配网关';wrapper.tabIndex=0;wrapper.setAttribute('aria-label','需要先开启适配网关');}
   else{delete wrapper.dataset.tooltip;wrapper.removeAttribute('tabindex');wrapper.removeAttribute('aria-label');}
  }
 }
 async function refresh(first=false){
  if(loading||!root)return;
  const epoch=alive,ticket=++request;
  try{
   const latest=await host.api('adaptor');
   if(epoch!==alive||ticket!==request||!root)return;
   data=latest;
   if(first||!root.querySelector('[data-power]'))draw();
   else{
    const status=root.querySelector('.adaptor-status');status.classList.toggle('online',data.running);status.querySelector('span').textContent=data.running?'开启':'关闭';
    root.querySelector('[data-gateway-port]').textContent=data.running?String(data.port):'—';
    const power=root.querySelector('[data-power]');power.textContent=data.running?'关闭网关':'开启网关';power.classList.toggle('primary',!data.running);
    for(const p of data.providers){const button=root.querySelector(`[data-provider="${p.id}"]`);button?.setAttribute('aria-checked',String(p.enabled));button?.classList.toggle('on',p.enabled);const address=root.querySelector(`[data-provider-address="${p.id}"]`);if(address)address.textContent=p.configured||data.running?p.base_url:'开启后自动生成'}
    syncProviderSwitches();
   }
  }catch(error){if(epoch===alive&&first)root.innerHTML=`<section class="panel adaptor-load-error"><p>${escape(error.message)}</p><button class="button" data-retry>重试</button></section>`,root.querySelector('[data-retry]').onclick=()=>void refresh(true)}
 }
 function openProgress(message){
  const dialog=document.createElement('dialog');
  dialog.className='adaptor-progress';
  dialog.setAttribute('aria-label',message);
  dialog.setAttribute('aria-busy','true');
  dialog.innerHTML='<span class="spinner" aria-hidden="true"></span><span role="status" aria-live="polite">'+escape(message)+'</span>';
  dialog.addEventListener('cancel',event=>event.preventDefault());
  document.body.append(dialog);dialog.showModal();
  progress=dialog;
  return ()=>{dialog.close();dialog.remove();if(progress===dialog)progress=null;};
 }
 async function change(values,message){
  if(loading||!data)return;
  if(values.action==='provider'&&values.enabled&&!data.running){host.toast('需要先开启适配网关');return;}
  request++;loading=true;const epoch=alive,api=host.api,toast=host.toast,refreshSnapshot=host.refreshSnapshot;
  root.querySelectorAll('button').forEach(item=>item.disabled=true);
  const closeProgress=openProgress(message);
  try{
   const result=await api('mutate',{kind:'adaptor',revision:data.revision,...values});
   await refreshSnapshot();
   if(epoch!==alive||!root)return;
   data=result.gateway;draw();
   toast(result.message);
  }catch(error){
   toast(error.message);
   if(epoch===alive&&root){await api('adaptor').then(value=>{if(epoch===alive)data=value}).catch(()=>{});if(epoch===alive&&root)draw();}
  }finally{closeProgress();if(epoch===alive){loading=false;root?.querySelector(values.action==='power'?'[data-power]':`[data-provider="${values.provider}"]`)?.focus({preventScroll:true});}}
 }
 function unmount(){alive++;if(progress){progress.close();progress.remove();progress=null;}clearInterval(timer);timer=null;root=null;host=null;data=null;loading=false;}
 function mount(element,context){unmount();root=element;host=context;root.innerHTML='<section class="panel adaptor-load-error"><span class="spinner"></span>正在读取网关状态…</section>';void refresh(true);timer=setInterval(()=>{if(!document.hidden)void refresh()},5000);}
 return {mount,unmount};
})();
