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
 return `<section class="panel global-config-panel" aria-labelledby="global-config-title"><div class="panel-head"><h2 id="global-config-title">全局配置</h2></div><div class="global-config-grid">${options.map(([key, title, description, name]) => `<div class="global-config-option"><span class="global-config-icon">${icon(name)}</span><div class="global-config-label"><span id="global-${key}-label">${title}</span><p id="global-${key}-description">${description}</p></div><button type="button" class="toggle ${config[key] ? 'on' : ''}" role="switch" aria-checked="${!!config[key]}" aria-labelledby="global-${key}-label" aria-describedby="global-${key}-description" data-action="global-toggle" data-key="${key}" ${globalConfigurationSaving ? 'disabled' : ''}></button></div>`).join('')}</div></section>`;
}
async function toggleGlobalConfiguration(target) {
 if (globalConfigurationSaving) return;
 globalConfigurationSaving = true;
 document.querySelectorAll('.global-config-panel .toggle').forEach(button => button.disabled = true);
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
  if (panel) panel.outerHTML = globalConfigurationPanel(state.global_config);
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
