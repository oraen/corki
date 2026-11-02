/* Homepage global preferences. Other settings and credentials stay untouched. */
let globalConfigurationSaving = false;
let sessionMaintenanceRequested = false;
function globalConfigurationPanel(config = {}) {
 const options = [
  ['approval_free', '免审批', '允许 Agent 直接执行操作', 'tools'],
  ['self_evolution', '自进化', 'Corki将在工作中不断学习自己改造自己', 'skills'],
  ['memories', '记忆', '在任务之间保留并使用长期记忆', 'memories'],
  ['auto_cleanup', '自动清理会话', '自动清理超过 3 个月未更新的会话', 'clock'],
 ];
 return `<section class="panel global-config-panel" aria-labelledby="global-config-title"><div class="panel-head"><h2 id="global-config-title">全局配置</h2></div>${globalDefaultFields()}<div class="global-config-grid">${options.map(([key, title, description, name]) => `<div class="global-config-option"><span class="global-config-icon">${icon(name)}</span><div class="global-config-label"><span id="global-${key}-label">${title}</span><p id="global-${key}-description">${description}</p></div><button type="button" class="toggle ${config[key] ? 'on' : ''}" role="switch" aria-checked="${!!config[key]}" aria-labelledby="global-${key}-label" aria-describedby="global-${key}-description" data-action="global-toggle" data-key="${key}" ${globalConfigurationSaving ? 'disabled' : ''}></button></div>`).join('')}</div></section>`;
}
async function toggleGlobalConfiguration(target) {
 if (globalConfigurationSaving) return;
 globalConfigurationSaving = true;
 document.querySelectorAll('.global-config-panel .toggle, .global-config-panel .select-trigger').forEach(button => button.disabled = true);
 try {
  const result = await api('mutate', {kind:'global-config', action:'set', key:target.dataset.key,
   enabled:target.getAttribute('aria-checked') !== 'true', revision:state.revision});
  state.revision = result.revision;
  state.global_config = result.global_config;
 } catch (error) {
  toast(error.message);
 } finally {
  globalConfigurationSaving = false;
  const panel = document.querySelector('.global-config-panel');
  if (panel) {panel.outerHTML = globalConfigurationPanel(state.global_config);bindGlobalDefaults();}
 }
}
function requestSessionMaintenance() {
 if (sessionMaintenanceRequested) return;
 sessionMaintenanceRequested = true;
 // Independent of rendering: no spinner, no session data in the browser request.
 void api('mutate', {kind:'global-config', action:'visit'}).catch(() => {
  sessionMaintenanceRequested = false;
 });
}

let globalDefaultsController;
function unbindGlobalDefaults(){globalDefaultsController?.abort();globalDefaultsController=null;}
function globalDefaultFields(){
 const providers=state.providers.filter(p=>p.enabled!==false&&Object.keys(p.models).length),selected=providers.find(p=>p.id===state.default.provider);
 const field=(key,label,options,value)=>`<div class="field protocol-field global-default-field" data-default-field="${key}"><span id="global-default-${key}-label">${label}</span><input type="hidden" value="${esc(value||'')}"><button type="button" class="select-trigger" aria-labelledby="global-default-${key}-label global-default-${key}-value" aria-haspopup="listbox" aria-expanded="false" aria-controls="global-default-${key}-options" ${globalConfigurationSaving||!options.length?'disabled':''}><span class="select-current" id="global-default-${key}-value">${esc(value||'暂无可用配置')}</span>${icon('chevron')}</button><div class="select-menu" id="global-default-${key}-options" role="listbox" aria-labelledby="global-default-${key}-label" hidden>${options.map(id=>`<button type="button" role="option" tabindex="-1" data-value="${esc(id)}" aria-selected="${id===value}"><strong>${esc(id)}</strong>${icon('check')}</button>`).join('')}</div></div>`;
 return `<div class="global-defaults">${field('provider','默认供应商',providers.map(p=>p.id),selected?.id)}${field('model','默认模型',Object.keys(selected?.models||{}),state.default.model)}</div>`;
}
function bindGlobalDefaults(){
 unbindGlobalDefaults();globalDefaultsController=new AbortController();
 document.querySelectorAll('[data-default-field]').forEach(root=>{
  bindCustomSelect(root,globalDefaultsController.signal);
  root.querySelector('input').onchange=async event=>{
   if(globalConfigurationSaving)return;
   const key=root.dataset.defaultField,value=event.target.value;
   if(value===state.default[key])return;
   globalConfigurationSaving=true;
   document.querySelectorAll('.global-config-panel button').forEach(button=>button.disabled=true);
   try{
    await api('mutate',{kind:key==='provider'?'providers':'models',action:'default',id:value,provider:state.default.provider,revision:state.revision});
    await load(true);toast('默认配置已更新');
   }catch(error){toast(error.message);}
   finally{
    globalConfigurationSaving=false;
    const panel=document.querySelector('.global-config-panel');
    if(panel){panel.outerHTML=globalConfigurationPanel(state.global_config);bindGlobalDefaults();}
   }
  };
 });
}
