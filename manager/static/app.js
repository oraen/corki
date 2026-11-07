'use strict';
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const providerIconDomains = [
 ['deepseek',['deepseek.com']],
 ['qwen',['dashscope.aliyuncs.com','dashscope-intl.aliyuncs.com','dashscope-us.aliyuncs.com','qwen.ai']],
 ['kimi',['moonshot.cn','moonshot.ai','kimi.com']],
 ['openai',['openai.com']],
 ['claude',['anthropic.com','claude.ai','claude.com']],
 ['gemini',['generativelanguage.googleapis.com','gemini.google.com']],
 ['glm',['bigmodel.cn','z.ai']],
 ['minimax',['minimax.cn','minimax.io']],
 ['xai',['x.ai']],
 ['mistral',['mistral.ai']]
];
function providerIconId(baseUrl){
 let host;
 try{host=new URL(baseUrl).hostname.toLowerCase().replace(/\.$/,'');}catch{return '';}
 return providerIconDomains.find(([,domains])=>domains.some(domain=>host===domain||host.endsWith('.'+domain)))?.[0]||'';
}
function providerCardIcon(provider,model=false,modelName=""){
 const name=(model&&window.CorkiModelAvatar.id(modelName))||(provider.id==='adaptor-openai'?'openai':provider.id==='adaptor-qoder'?'qoder':providerIconId(provider.base_url));
 if(name)return `<span class="${model?'model-card-icon':'provider-logo'} has-brand-icon"><img src="/provider-icons/${name}.png" alt="" width="28" height="28"></span>`;
 return model?`<span class="model-card-icon">${icon('models')}</span>`:`<span class="provider-logo ${provider.id.includes('codex')?'dark':''}">${esc(provider.id.slice(0,1).toUpperCase())}</span>`;
}
const paths = {
 sound:'m11 5-6 4H2v6h3l6 4V5z M15 8a6 6 0 0 1 0 8 M18 5a10 10 0 0 1 0 14',
 overview:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
 providers:'M4 4h16v6H4z M4 14h16v6H4z M8 7h.01 M8 17h.01',
 models:'M12 3 3 8l9 5 9-5-9-5z M3 12l9 5 9-5 M3 16l9 5 9-5',
 skills:'m13 2-9 12h7l-1 8 10-12h-7l1-8z',
 knowledge:'M4 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H4z M13 7a3 3 0 0 1 3-3h5v15h-4a4 4 0 0 0-4 2',
 tools:'M14 6a5 5 0 0 0-6 6L3 17a3 3 0 0 0 4 4l5-5a5 5 0 0 0 6-6l-4 3-3-3 3-4z',
 chat:'M4 5h16v12H8l-4 3V5z M8 9h8 M8 13h5',
 memories:'M9 4a3 3 0 0 0-5 3 4 4 0 0 0-1 7 4 4 0 0 0 6 6V4z M15 4a3 3 0 0 1 5 3 4 4 0 0 1 1 7 4 4 0 0 1-6 6V4z M5 10h4 M15 15h4',
 logs:'M4 3h16v18H4z M8 8h8 M8 12h8 M8 16h4',
 search:'m21 21-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
 save:'M5 3h13l3 3v15H3V3h2zm2 0v7h10V3M7 21v-7h10v7',plus:'M12 5v14 M5 12h14',arrow:'M5 12h14 m-5-5 5 5-5 5', info:'M12 11v6 M12 7h.01 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
 clock:'M12 7v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',check:'m5 12 4 4L19 6'
};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.models}"/></svg>`;
paths.config=paths.logs;
paths.cron=paths.clock;
paths.play='m9 5 11 7-11 7V5z';
paths.close='M6 6l12 12M6 18 18 6';
paths.edit='m16 3 5 5-12 12H4v-5L16 3z M13 6l5 5';
paths.trash='M3 6h18 M9 6V3h6v3 M5 6l1 15h12l1-15 M10 10v7 M14 10v7';
paths.chevron='m9 5 7 7-7 7';
paths.copy='M9 9h12v12H9z M15 9V3H3v12h6';
paths.developer='m8 6-6 6 6 6 M16 6l6 6-6 6 M14 3l-4 18';
paths.user='M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M4 21v-2a8 8 0 0 1 16 0v2';
paths.eye='M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0';
paths.eyeOff='m3 3 18 18 M10.5 5.1 12 5c6.5 0 10 7 10 7a19 19 0 0 1-3.2 4 M6.2 6.2A21 21 0 0 0 2 12s3.5 7 10 7a12 12 0 0 0 5-1 M10 10a3 3 0 0 0 4 4';
const routes = {channels:['转发网关','CHANNELS'],plugins:['插件管理','PLUGINS'],adaptor:['套餐转发','ADAPTOR'],overview:['主页','HOME'],guide:['使用指引','GUIDE'],models:['模型管理','MODELS'],skills:['Skills','CAPABILITIES'],knowledge:['知识库','KNOWLEDGE'],tools:['工具','TOOLS'],cron:['定时任务','CRON'],chat:['简单对话','CHAT'],memories:['记忆管理','MEMORY'],logs:['运行日志','LOGS'],config:['配置文件','CONFIGURATION'],database:['数据库管理','DATABASE'],hooks:['Hooks管理','HOOKS']};
const developerRoutes=['memories','logs','config','database','hooks'];
paths.database=paths.providers;paths.hooks='M4 4v5a4 4 0 0 0 4 4h8a4 4 0 0 1 4 4v3M12 3v18M8 17l4 4 4-4';
let developerExpanded=false;
let selectedProvider='', selectedModel='';
let state, route = 'overview', query = '', filter = 'all', busy = false, submitAction;
const channelNames = new Map();
let activeModelTest = null;
const fmt = n => new Intl.NumberFormat('zh-CN',{notation:Number(n)>99999?'compact':'standard',maximumFractionDigits:1}).format(n||0);
const when = window.CorkiDateTime;
const size = n => n<1024 ? `${n} B` : `${(n/1024).toFixed(1)} KB`;
const badge = (v, color='') => `<span class="badge ${color}">${esc(v)}</span>`;
const status = v => ({completed:['已完成','green'],failed:['失败','red'],running:['运行中','purple'],cancelled:['已取消','orange'],interrupted:['已中断','orange'],aborted:['已中止','orange'],succeeded:['已完成','green']})[v] || [v||'暂无运行',''];
const statusBadge = v => badge(...status(v));
const button = (text, action, attrs='', primary=false) => `<button class="button ${primary?'primary':''}" data-action="${action}" ${attrs}>${primary?icon(action==='model-save'?'save':'plus'):''}${text}</button>`;
const textButton = (text, action, attrs='') => `<button class="text-button" data-action="${action}" ${attrs}>${text}</button>`;
const attr = (id,provider) => `data-id="${esc(id)}" ${provider?`data-provider="${esc(provider)}"`:''}`;
const empty = (title='暂无内容',desc='暂无已配置项目。') => `<div class="empty">${icon(route)}<h3>${title}</h3><p>${desc}</p></div>`;
const notice = text => `<div class="notice">${icon('info')}<span>${text}</span></div>`;
const match = item => JSON.stringify(item).toLowerCase().includes(query.toLowerCase());
function toast(text){$('#toast').textContent=text;$('#toast').classList.add('visible');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').classList.remove('visible'),4500);}
async function api(path,body){const response=await fetch('/api/'+path,body?{method:'POST',headers:{'Content-Type':'application/json','X-Corki-Manager':'1',...(((body.kind==='channel-gateway'&&body.action==='install')||(body.kind==='skills'&&['import','validate-import'].includes(body.action))||(body.kind==='hooks'&&body.action==='upload-skill')||(body.kind==='resource-packages'&&['create','update','preview-import','import'].includes(body.action)))?{'X-Corki-Skill-Import':'1'}:{})},body:JSON.stringify(body)}:{cache:'no-store'});const data=await response.json();if(!response.ok)throw new Error(data.error||'请求失败');return data;}
async function load(quiet=false){if(busy)return;busy=true;$('#refresh').disabled=true;try{state=await api('snapshot');render();requestSessionMaintenance();if(!quiet)toast('工作空间已更新');}catch(e){if(!state)$('#main').innerHTML=empty('暂时无法读取工作空间',esc(e.message))+button('重试','refresh');else toast(e.message);}finally{busy=false;$('#refresh').disabled=false;}}
function models(){return state.providers.flatMap(p=>Object.entries(p.models).map(([id,m])=>({id,provider:p.id,...m}))).sort((a,b)=>a.id.localeCompare(b.id)||a.provider.localeCompare(b.provider));}
function toolbar(count,tabs='',action=''){return `<div class="toolbar"><label class="search">${icon('search')}<input id="search" placeholder="搜索名称、描述…" value="${esc(query)}" aria-label="搜索当前列表"></label>${tabs}<span class="count">共 ${count} 项</span>${action}</div>`;}
function tabs(values){return `<div class="filters">${values.map(([id,name])=>`<button data-action="filter" data-id="${id}" class="${filter===id?'active':''}">${name}</button>`).join('')}</div>`;}
function table(headers,rows){return `<div class="panel table-wrap"><table><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>${rows.length?'':empty('没有匹配的记录','试试其他关键词，或添加新的内容。')}</div>`;}
function overview(){const allModels=models(),total=state.totals;const stats=[['模型供应商',state.providers.length,'providers',`${allModels.length} 个模型已配置`],['已启用 Skills',state.files.filter(f=>f.kind==='skills'&&f.enabled).length,'skills','按本地启停配置统计'],['累计会话',total.sessions||0,'clock','来自 Corki 会话数据库'],['累计 Tokens',usageTokens((total.input||0)+(total.output||0)),'models',`${usageTokens(total.cached||0)} 输入 tokens 命中缓存`]];return `<div class="stats">${stats.map(([title,value,i,note])=>`<div class="stat"><div class="stat-top">${title}<span class="stat-icon">${icon(i)}</span></div><div class="stat-number"${i==='models'?` title="${usageTokens((total.input||0)+(total.output||0))} tokens"`: ''}>${esc(value)}</div><div class="stat-note"${i==='models'?` title="${usageTokens(total.cached||0)} tokens"`: ''}>${note}</div></div>`).join('')}</div>${usageDashboard()}${globalConfigurationPanel(state.global_config)}${quickToolsCards()}${toolboxCards()}<section class="panel"><div class="panel-head" style="padding-bottom:20px"><div><h2>最近会话</h2></div><a class="text-button" href="#logs">查看全部 →</a></div><div class="table-wrap"><table><thead><tr><th>会话 / 工作目录</th><th>模型</th><th>状态</th><th>最近更新</th></tr></thead><tbody>${sessionRows(state.sessions.slice(0,5))}</tbody></table>${state.sessions.length?'':empty('暂无会话记录','在 Corki 中开始一个任务即可。')}</div></section>`;}
function sessionRows(rows){return rows.map(s=>`<tr><td><span class="row-title">${esc(s.preview||String(s.cwd||'').replace(/[\\/]+$/, '').split(/[\\/]/).pop()||'未命名会话')}</span><span class="row-sub">${esc(s.cwd)}</span></td><td>${esc(s.model||'—')}</td><td>${statusBadge(s.status)}</td><td class="subtle">${when(s.updated_at)}</td></tr>`).join('');}
const collapsedProviderGroups=new Set(['system']);
function providerCard(p){return `<article class="provider-card ${p.enabled===false?'is-disabled':''} ${p.id===state.default.provider?'is-default':''}"><div class="provider-card-heading">${providerCardIcon(p)}<a class="provider-card-name" href="#models/${encodeURIComponent(p.id)}" title="${esc(p.id)}">${esc(p.id)}</a>${p.id===state.default.provider?'<span class="card-default-tag">默认</span>':''}${p.id.startsWith('adaptor-')?'':`<button type="button" class="toggle ${p.enabled!==false?'on':''}" role="switch" aria-label="${esc(p.id)} 启用状态" aria-checked="${p.enabled!==false}" data-action="provider-toggle" data-id="${esc(p.id)}"></button>`}</div><div class="provider-card-body"><div class="provider-card-url mono" title="${esc(p.base_url)}">${esc(p.base_url)}</div><div class="provider-card-meta"><span>${Object.keys(p.models).length} 个模型</span>${badge(p.api_mode==='responses'?'Responses':'Chat Completions')}</div></div><div class="provider-card-footer"><div class="actions">${p.id!==state.default.provider?`<button class="text-button" data-action="provider-default" data-id="${esc(p.id)}" ${Object.keys(p.models).length?'':'disabled title="请先添加模型"'}>设为默认</button>`:''}${textButton('连接设置','edit',attr(p.id))}${p.id.startsWith('adaptor-')?'':textButton('删除','delete',attr(p.id))}</div></div></article>`;}
function providersPage(){
 const rows=state.providers.filter(match).filter(p=>!p.id.startsWith('adaptor-')||p.enabled!==false);
 const sections=[['custom','自定义供应商','providers'],['system','系统适配供应商','tools']];
 return toolbar(rows.length,'',`<div class="provider-toolbar-actions">${button('添加供应商','new','',true)}${button('快速添加','quick-add')}</div>`)+sections.map(([group,title,symbol])=>{
  const belongs=p=>p.id.startsWith('adaptor-')===(group==='system');
  const visible=p=>belongs(p)&&(group!=='system'||p.enabled!==false);
  const items=rows.filter(visible),total=state.providers.filter(visible).length;
  return `<details class="skill-section provider-section" data-provider-group="${group}" ${collapsedProviderGroups.has(group)?'':'open'}><summary class="skill-section-head"><span class="skill-group-icon ${group==='system'?'builtin':'corki'}">${icon(symbol)}</span><div class="provider-group-heading"><h2>${title}<span>${total}</span></h2>${group==='system'?'<span class="provider-group-help" tabindex="0" aria-label="可以在主页-工具箱-套餐转发中管理适配供应商" data-tooltip="可以在&quot;主页-工具箱-套餐转发&quot;中管理适配供应商。">?</span>':''}</div><svg class="skill-collapse-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></summary>${items.length?`<div class="provider-grid">${items.map(providerCard).join('')}</div>`:`<div class="skill-empty">${query?'暂无匹配的供应商':group==='system'?'暂无已开启的系统适配供应商':'暂无'+title}</div>`}</details>`;
 }).join('');
}

function modelKind(){return route==='models'?(selectedProvider?'models':'providers'):route;}
function modelLink(p,m=''){return '#models/'+encodeURIComponent(p)+(m?'/'+encodeURIComponent(m):'');}
function modelReasoningOptions(model){
 const levels=Array.isArray(model?.supported_reasoning_levels)?model.supported_reasoning_levels.map(p=>p.effort):['none','minimal','low','medium','high','xhigh','max','ultra'];
 return [['','模型默认'],...levels.map(v=>[v,v])];
}
function modelsPage(){
 if(!selectedProvider)return providersPage();
 const p=state.providers.find(p=>p.id===selectedProvider);
 if(!p)return empty('供应商不存在','请返回模型管理。')+'<a class="button" href="#models">返回</a>';
 const back='';
 if(selectedModel){
  const m=p.models[selectedModel];
  if(!m)return back+empty('模型不存在');
  return back+`<section class="panel config-panel"><div class="model-config-heading"><h2>${esc(selectedModel)}</h2>${button('保存配置','model-save','',true)}</div><p>${esc(p.id)} · 模型配置</p><div class="field-grid">${field('context_window','上下文窗口（tokens）',m.context_window||262144,'number')}${select('reasoning_effort','默认推理等级',modelReasoningOptions(m),m.reasoning_effort||'')}</div>${descriptionField(m.description)}</section>`;
 }
 const rows=models().filter(m=>m.provider===p.id&&match(m));
 return back+toolbar(rows.length,'',button('添加模型','new','',true))+`<div class="model-grid">${rows.map(m=>{const current=m.id===p.default_model;return `<article class="model-card ${current?'is-default':''}"><div class="model-card-heading">${providerCardIcon(p,true,m.id)}<a class="model-card-name" href="${modelLink(p.id,m.id)}" title="${esc(m.id)}">${esc(m.id)}</a>${current?'<span class="card-default-tag">默认</span>':''}</div><p class="model-card-description" title="${esc(m.description||'')}">${esc(m.description||'暂无描述')}</p><div class="model-card-footer">${textButton('模型测试','model-test',attr(m.id,p.id))}${current?'':textButton('设为默认','default',attr(m.id,p.id))}${textButton('删除','delete',attr(m.id,p.id))}</div></article>`;}).join('')}</div>`+(rows.length?'':empty('尚未配置模型','添加此供应商下的第一个模型。'));

}
function configPage(){return '<div id="config-workspace"></div>'; }
function filesPage(){let rows=state.files.filter(f=>f.kind===route&&match(f));if(filter==='enabled')rows=rows.filter(f=>f.enabled);if(filter==='editable')rows=rows.filter(f=>f.writable);const action=route==='memories'?'':button(route==='skills'?'创建 Skill':'添加文档','new','',true);let note=route==='knowledge'?'未启用自动检索与索引。':route==='memories'?'':'共享技能只读。';return toolbar(rows.length,tabs([['all','全部'],[route==='skills'?'enabled':'editable',route==='skills'?'已启用':'可编辑']]),action)+(note?notice(note):'')+`<div class="cards">${rows.map(f=>`<article class="panel entity-card"><div class="entity-head"><span class="resource-icon">${icon(route)}</span>${route==='skills'?badge(f.source||'用户'): ''}${route==='skills'?`<button class="toggle ${f.enabled?'on':''}" role="switch" aria-checked="${f.enabled}" aria-label="${esc(f.name)} 启用状态" data-action="toggle" data-id="${f.id}"></button>`:badge(f.writable?'可编辑':'只读')}</div><div class="entity-body"><div class="entity-title">${esc(f.name)}</div><div class="entity-desc">${esc(f.description||'暂无描述')}</div></div><div class="entity-footer"><span class="subtle">${size(f.bytes)} · ${when(f.updated)}</span><div class="actions">${textButton('查看'+(f.writable?' / 编辑':''),'file',attr(f.id))}${route==='knowledge'?textButton('删除','delete',attr(f.id)):''}</div></div></article>`).join('')}</div>${rows.length?'':empty(route==='knowledge'?'暂无知识文档':'暂无匹配的资源',route==='knowledge'?'':'资源会从 Corki 的本地目录中读取。')}`;}

function logsPage(){const rows=(filter==='tools'?state.executions:state.sessions).filter(match);return toolbar(rows.length,tabs([['all','会话记录'],['tools','工具调用']]),`<span class="badge green">每 15 秒自动刷新</span>`)+(filter==='tools'?table(['工具','状态','开始时间','调用 ID','操作'],rows.map(r=>`<tr><td><span class="table-title"><span class="mini-icon">${icon('tools')}</span>${esc(r.tool_name)}</span></td><td>${statusBadge(r.status)}</td><td>${when(r.started_at)}</td><td class="mono">${esc(r.call_id.slice(0,20))}…</td><td>${textButton('查看输出','execution',attr(r.call_id))}</td></tr>`)):table(['会话 / 工作目录','模型','状态','最近更新'],rows.map(s=>sessionRows([s]))))+`<p style="margin-top:15px;font-size:10px">最近 40 个会话 / 80 次调用 · 状态来自历史记录</p>`;}
function renderNav(){
 const link=id=>`<a href="#${id}" class="${id===route||(id==='overview'&&['guide','adaptor','plugins','channels'].includes(route))?'active':''}" ${id===route?'aria-current="page"':''}>${icon(id)}<span>${routes[id][0]}</span></a>`;
 $('#nav').innerHTML=Object.keys(routes).filter(id=>!['guide','adaptor','plugins','channels'].includes(id)&&!developerRoutes.includes(id)).map(link).join('')+`<div class="nav-group"><button type="button" class="nav-group-toggle ${developerRoutes.includes(route)?'has-active':''}" aria-expanded="${developerExpanded}" aria-controls="developer-nav">${icon('developer')}<span>开发者</span><svg class="nav-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg></button><div id="developer-nav" class="nav-submenu" ${developerExpanded?'':'hidden'}>${developerRoutes.map(link).join('')}</div></div>`;
}
function renderBreadcrumbs(){
 const channelDetail=route==='channels'&&selectedProvider;
 const knowledgeDetail=route==='knowledge'&&selectedProvider;
 const pluginDetail=route==='plugins'&&selectedProvider;
 const sqlPage=route==='database'&&selectedProvider==='sql';
 const nested=route==='models'&&selectedProvider;
 const items=['guide','adaptor','plugins','channels'].includes(route)?[['工作空间',''],['主页','#overview'],[routes[route][0],'#'+route]]:[['工作空间',''],[routes[route][0],'#'+route]];
 if(pluginDetail)items.push(['插件详情',location.hash]);
 if(knowledgeDetail)items.push([state.files.find(f=>f.id===selectedProvider)?.name||'文档详情',location.hash]);
 if(channelDetail)items.push([channelNames.get(selectedProvider)?'接入管理-'+channelNames.get(selectedProvider):'接入管理','#channels/'+encodeURIComponent(selectedProvider)]);
 if(channelDetail&&selectedModel)items.push(['会话页'+selectedModel,location.hash]);
 if(sqlPage)items.push(['SQL 查询',location.hash]);
 if(nested)items.push([selectedProvider,modelLink(selectedProvider)]);
 if(nested&&selectedModel)items.push([selectedModel,modelLink(selectedProvider,selectedModel)]);
 const back=knowledgeDetail||nested||sqlPage||['guide','adaptor','plugins','channels'].includes(route)?`<a class="header-back" href="${pluginDetail?'#plugins':channelDetail?(selectedModel?'#channels/'+encodeURIComponent(selectedProvider):'#channels'):knowledgeDetail?'#knowledge':sqlPage?'#database':['guide','adaptor','plugins','channels'].includes(route)?'#overview':selectedModel?modelLink(selectedProvider):'#models'}" aria-label="返回上一级" title="返回上一级"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg><span>返回</span></a>`:`<button class="header-back" type="button" disabled aria-label="返回上一级"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg><span>返回</span></button>`;
 $('#header-navigation').innerHTML=back+`<div class="breadcrumbs" role="navigation" aria-label="当前位置">${items.map(([label,href],i)=>`${i?'<span class="crumb-separator" aria-hidden="true">/</span>':''}${i===0?`<span class="crumb-root">${esc(label)}</span>`:i===items.length-1?`<strong id="crumb" aria-current="page" title="${esc(label)}">${esc(label)}</strong>`:`<a href="${href}" title="${esc(label)}">${esc(label)}</a>`}`).join('')}</div>`;
}
function render(){if(!state)return;unbindGlobalDefaults();if(route==='knowledge'&&selectedProvider===window.CorkiKnowledge.id&&window.CorkiKnowledge.root?.isConnected&&window.CorkiKnowledge.editing)return;window.CorkiChannels.unmount();window.CorkiResourcePackages.unmount();window.CorkiAdaptor.unmount();window.CorkiKnowledge.unmount();window.CorkiHooks.unmount();window.CorkiSQL.unmount();window.CorkiDatabase.unmount();window.CorkiCron.unmount();window.CorkiConfig.unmount();window.CorkiSimpleChat.unmount();document.body.classList.toggle('chat-route',route==='chat');renderBreadcrumbs();renderNav();const views={channels:()=>'<div id="channel-gateway-workspace"></div>',plugins:()=>'<div id="resource-packages-workspace"></div>',adaptor:()=>'<div id="adaptor-workspace"></div>',hooks:()=>'<div id="hooks-workspace"></div>',database:()=>'<div id="database-workspace"></div>',overview,guide:guidePage,skills:skillsPage,knowledge:()=>selectedProvider?'<div id="knowledge-workspace"></div>':knowledgePage(),models:modelsPage,tools:toolsPage,cron:()=>'<div id="cron-workspace"></div>',chat:()=>'<div id="simple-chat-workspace"></div>',logs:logsPage,config:configPage};$('#main').innerHTML=(state.errors.length?notice(state.errors.map(esc).join(' ')):'')+(views[route]||filesPage)();if(route==='overview')bindGlobalDefaults();if(route==='channels')window.CorkiChannels.mount($('#channel-gateway-workspace'),{api,toast,id:selectedProvider,conversation:selectedModel,providers:state.providers,onConnectionName:(id,name)=>{if(channelNames.get(id)===name)return;if(name)channelNames.set(id,name);else channelNames.delete(id);if(route==='channels'&&selectedProvider===id)renderBreadcrumbs();}});if(route==='plugins')window.CorkiResourcePackages.mount($('#resource-packages-workspace'),{id:selectedProvider});if(route==='adaptor')window.CorkiAdaptor.mount($('#adaptor-workspace'),{api,toast,refreshSnapshot:async()=>{state=await api('snapshot');}});if(route==='knowledge'&&selectedProvider)window.CorkiKnowledge.mount($('#knowledge-workspace'),{id:selectedProvider,api,toast,refreshSnapshot:async()=>{state=await api('snapshot');renderBreadcrumbs();}});if(route==='database'){if(selectedProvider==='sql')window.CorkiSQL.mount($('#database-workspace'),selectedModel);else window.CorkiDatabase.mount($('#database-workspace'));}if(route==='hooks')window.CorkiHooks.mount($('#hooks-workspace'));if(route==='cron')window.CorkiCron.mount($('#cron-workspace'));if(route==='config')window.CorkiConfig.mount($('#config-workspace'),{api,toast,reloadSnapshot:()=>load(true)});if(route==='chat')window.CorkiSimpleChat.mount($('#simple-chat-workspace'),{providers:state.providers.filter(p=>p.enabled!==false),defaultModel:state.default,toast});}
function navigate(){const parts=location.hash.slice(1).split('/');route=parts[0] in routes?parts[0]:'overview';if(developerRoutes.includes(route))developerExpanded=true;try{selectedProvider=decodeURIComponent(parts[1]||'');selectedModel=decodeURIComponent(parts[2]||'');}catch{selectedProvider='';selectedModel='';}query='';filter='all';document.body.classList.remove('open-nav');render();}
function descriptionField(value=''){return `<label class="field model-description-field"><span>模型描述</span><textarea name="description" rows="3" maxlength="500" placeholder="简要介绍模型的用途或特点">${esc(value)}</textarea></label>`;}
function field(name,label,value='',type='text',hint='',disabled=false,placeholder=''){return `<label class="field"><span>${label}</span><input name="${name}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${disabled?'disabled':''} ${name==='api_key'?'autocomplete="new-password"':'autocomplete="off"'} ${type==='number'?'min="1024" max="10000000"':''}><small>${hint}</small></label>`;}
function select(name,label,options,value,disabled=false){return `<label class="field"><span>${label}</span><select name="${name}" ${disabled?'disabled':''}>${options.map(o=>`<option value="${esc(o[0])}" ${o[0]===value?'selected':''}>${esc(o[1])}</option>`).join('')}</select></label>`;}
function providerSelect(value,disabled=false){const options=state.providers.map(p=>p.id);return `<div class="field protocol-field provider-select-field"><span id="provider-select-label">所属供应商</span><input type="hidden" name="provider" value="${esc(value)}"><button type="button" class="select-trigger" aria-labelledby="provider-select-label provider-select-value" aria-haspopup="listbox" aria-expanded="false" aria-controls="provider-select-options" ${disabled?'disabled':''}><span id="provider-select-value" class="select-current">${esc(value)}</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m7 10 5 5 5-5"/></svg></button><div id="provider-select-options" class="select-menu" role="listbox" aria-labelledby="provider-select-label" hidden>${options.map(id=>`<button type="button" role="option" tabindex="-1" data-value="${esc(id)}" aria-selected="${id===value}"><span><strong>${esc(id)}</strong></span>${icon('check')}</button>`).join('')}</div></div>`;}
function protocolSelect(value){
 const options=[['responses','Responses','OpenAI Responses API'],['chat_completions','Chat Completions','OpenAI 兼容聊天接口']];
 return `<div class="field protocol-field"><span id="protocol-label">接口协议</span><input type="hidden" name="api_mode" value="${esc(value)}"><button type="button" class="select-trigger" aria-labelledby="protocol-label protocol-value" aria-haspopup="listbox" aria-expanded="false" aria-controls="protocol-options"><span id="protocol-value" class="select-current">${options.find(o=>o[0]===value)?.[1]||'选择协议'}</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m7 10 5 5 5-5"/></svg></button><div id="protocol-options" class="select-menu" role="listbox" aria-labelledby="protocol-label" hidden>${options.map(([id,title,detail])=>`<button type="button" role="option" tabindex="-1" data-value="${id}" aria-selected="${id===value}"><span><strong>${title}</strong><small>${detail}</small></span>${icon('check')}</button>`).join('')}</div></div>`;
}
function bindCustomSelect(root,signal){
 const trigger=root.querySelector('.select-trigger'),menu=root.querySelector('.select-menu'),input=root.querySelector('input'),options=[...menu.querySelectorAll('[role=option]')];
 const close=(focus=false)=>{menu.hidden=true;trigger.setAttribute('aria-expanded','false');if(focus)trigger.focus();};
 const available=()=>options.filter(o=>!o.hidden&&!o.disabled);
 const open=()=>{menu.hidden=false;trigger.setAttribute('aria-expanded','true');const choices=available();(choices.find(o=>o.dataset.value===input.value)||choices[0])?.focus();};
 trigger.onclick=()=>menu.hidden?open():close();
 options.forEach(option=>option.onclick=()=>{input.value=option.dataset.value;root.querySelector('.select-current').textContent=option.querySelector('strong').textContent;options.forEach(o=>o.setAttribute('aria-selected',String(o===option)));input.dispatchEvent(new Event('change',{bubbles:true}));close(true);});
 root.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&!menu.hidden){e.preventDefault();e.stopPropagation();close(true);}
  else if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();if(menu.hidden){open();return;}const choices=available();if(!choices.length)return;let i=choices.indexOf(document.activeElement);i=e.key==='Home'?0:e.key==='End'?choices.length-1:(i+(e.key==='ArrowDown'?1:-1)+choices.length)%choices.length;choices[i].focus();}
  else if(e.key==='Enter'||e.key===' '){e.preventDefault();if(menu.hidden)open();else if(options.includes(document.activeElement))document.activeElement.click();else close();}
  else if(e.key==='Tab')close();
 });
 root.addEventListener('focusout',e=>{if(!root.contains(e.relatedTarget))close();});
 const outside=e=>{if(!root.contains(e.target))close();};
 document.addEventListener('pointerdown',outside,{signal});
 if(!signal)$('#editor').addEventListener('close',()=>document.removeEventListener('pointerdown',outside),{once:true});
}
function keyField(saved){return `<div class="field"><label for="provider-key">API Key</label><div class="secret-control"><input id="provider-key" name="api_key" type="password" value="${saved?'********':''}" data-saved="${!!saved}" data-changed="false" autocomplete="new-password" spellcheck="false" placeholder="填写供应商的访问密钥"><button type="button" class="secret-toggle" aria-label="显示 API Key" aria-pressed="false" title="显示 API Key">${icon('eye')}</button></div></div>`;}
function bindKeyField(id){
 const input=$('#provider-key'),toggle=$('.secret-toggle');let loaded=false,request=0;
 input.addEventListener('focus',()=>{if(input.dataset.saved==='true'&&!loaded&&input.dataset.changed!=='true')input.select();});
 input.addEventListener('beforeinput',()=>{if(input.dataset.saved==='true'&&!loaded&&input.dataset.changed!=='true')input.value='';});
 input.addEventListener('input',()=>{input.dataset.changed='true';request++;});
 toggle.addEventListener('click',async()=>{
  const ticket=++request;toggle.disabled=true;
  try{
   if(input.type==='password'&&input.dataset.saved==='true'&&!loaded&&input.dataset.changed!=='true'){
    const result=await api('provider-key?id='+encodeURIComponent(id));
    if(!input.isConnected||ticket!==request||!$('#editor').open)return;
    input.value=result.api_key;loaded=true;
   }
   input.type=input.type==='password'?'text':'password';
   const visible=input.type==='text';toggle.innerHTML=icon(visible?'eyeOff':'eye');toggle.title=visible?'隐藏 API Key':'显示 API Key';toggle.setAttribute('aria-label',toggle.title);toggle.setAttribute('aria-pressed',String(visible));
  }catch{if(input.isConnected)$('#form-error').textContent='密钥读取失败，请重试';}
  finally{toggle.disabled=false;}
 });
}
function showDialog(title,content,callback,note='更改会保存到本地'){resetSkillDialog();const saveHint=$('#editor .adaptor-save-hint');if(saveHint)saveHint.replaceWith($('#save'));$('#editor').classList.remove('adaptor-readonly');$('#save').removeAttribute('title');$('#save').removeAttribute('aria-disabled');$('#editor').classList.remove('knowledge-delete-dialog');$('#save').textContent='保存更改';$('#cancel-dialog').textContent='取消';submitAction=callback;$('#dialog-title').textContent=title;$('#fields').innerHTML=content;$('#form-note').textContent=note;$('#form-error').textContent='';$('#save').hidden=!callback;$('#save').disabled=false;$('#editor').showModal();}
function renderModelTestMessages(){const list=$('#model-test-messages');if(!list)return;const items=activeModelTest?.messages||[];list.innerHTML=items.length?items.map(item=>`<article class="test-message ${item.role==='assistant'?'assistant':'user'} ${item.error?'failed':''}"><span class="test-message-avatar">${item.role==='assistant'?window.CorkiModelAvatar.image(activeModelTest.model,icon('models')):'<img class="test-user-avatar" src="/chat-crab.jpg" alt="" draggable="false">'}</span><div class="test-message-body">${item.streaming?'<div class="test-message-label"><span class="stream-indicator"><i></i>正在生成</span></div>':''}<div class="test-message-text">${item.content?esc(item.content):item.streaming?'<span class="test-cursor"></span>':item.error?'':'<span class="test-empty-reply">模型没有返回文本</span>'}</div></div></article>`).join(''):`<div class="model-test-welcome"><span class="model-test-orbit">${icon('models')}</span><h3>准备好测试模型了吗？</h3><p>发送一条消息，直接查看模型的实时回复。</p><div class="test-suggestions"><button type="button" data-test-prompt="请用一句话介绍你自己。">介绍一下自己 <span>↗</span></button><button type="button" data-test-prompt="请用三点说明你最擅长的事情。">你擅长什么 <span>↗</span></button></div></div>`;list.scrollTop=list.scrollHeight;}
function openModelTest(model,provider){const profile=state.providers.find(item=>item.id===provider);if(!profile||!Object.hasOwn(profile.models,model))return toast('模型配置已变化，请刷新后重试');const dialog=$('#model-test-dialog');activeModelTest={model,provider,messages:[],controller:null,busy:false};$('#model-test-title').textContent=model;$('#model-test-provider').textContent=provider;$('#model-test-state').textContent='就绪';$('#model-test-state').className='model-test-state';$('#model-test-input').value='';$('#model-test-input').disabled=false;$('#model-test-send').disabled=false;renderModelTestMessages();dialog.showModal();requestAnimationFrame(()=>$('#model-test-input').focus());}
function closeModelTest(){if(activeModelTest?.controller)activeModelTest.controller.abort();activeModelTest=null;$('#model-test-dialog').close();}
function setModelTestBusy(value){if(!activeModelTest)return;activeModelTest.busy=value;$('#model-test-send').disabled=value;$('#model-test-input').disabled=value;if(value){$('#model-test-state').textContent='正在连接模型…';$('#model-test-state').className='model-test-state working';}else if($('#model-test-state').classList.contains('working')){$('#model-test-state').textContent='就绪';$('#model-test-state').className='model-test-state';}}
async function sendModelTest(){const input=$('#model-test-input');const content=input.value.trim();if(!content||!activeModelTest||activeModelTest.busy)return;const target=activeModelTest;target.messages.push({role:'user',content},{role:'assistant',content:'',streaming:true});input.value='';input.style.height='auto';renderModelTestMessages();setModelTestBusy(true);target.controller=new AbortController();try{const history=target.messages.slice(0,-1).filter(item=>!item.error&&item.content.trim()).slice(-20).map(({role,content})=>({role,content}));const response=await fetch('/api/model-test',{method:'POST',headers:{'Content-Type':'application/json','X-Corki-Manager':'1'},body:JSON.stringify({provider:target.provider,model:target.model,messages:history}),signal:target.controller.signal});if(!response.ok){let message='模型请求失败';try{message=(await response.json()).error||message;}catch{}throw new Error(message);}if(!response.body)throw new Error('当前浏览器不支持流式读取');const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',eventName='';const handleEvent=raw=>{let data='';for(const line of raw.split(/\r?\n/)){if(line.startsWith('event:'))eventName=line.slice(6).trim();else if(line.startsWith('data:'))data+=line.slice(5).trim();}if(!data)return;const payload=JSON.parse(data);const reply=target.messages.at(-1);if(eventName==='delta'){reply.content+=payload.text||'';renderModelTestMessages();$('#model-test-state').textContent='正在接收回复';}else if(eventName==='error')throw new Error(payload.message||'模型请求失败');else if(eventName==='done'){reply.streaming=false;$('#model-test-state').textContent='回复完成';$('#model-test-state').className='model-test-state';}};while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});let boundary;while((boundary=buffer.indexOf('\n\n'))>=0){const raw=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);handleEvent(raw);}if(done)break;}if(buffer.trim())handleEvent(buffer);const finalReply=target.messages.at(-1);if(finalReply)finalReply.streaming=false;renderModelTestMessages();}catch(error){const reply=target.messages.at(-1);if(reply?.streaming){reply.streaming=false;reply.error=true;reply.content=error.name==='AbortError'?'本次测试已取消':error.message;renderModelTestMessages();}if(error.name!=='AbortError'){$('#model-test-state').textContent='请求失败';$('#model-test-state').className='model-test-state failed';}}finally{if(activeModelTest===target){target.controller=null;setModelTestBusy(false);$('#model-test-input').focus();}}}
async function mutate(data){const result=await api('mutate',{revision:state.revision,...data});toast(result.message);await load(true);}
function editEntity(id,provider){
 if(route==='tools')return editMcp(id);
 const kind=modelKind();provider=provider||selectedProvider;
 const isNew=!id,snapRevision=state.revision;let fields='',original;
 if(kind==='providers'){
  original=state.providers.find(p=>p.id===id);
  fields=field('id','供应商名称',id,'text','',false,'')+field('base_url','API 地址',original?.base_url||'','url')+protocolSelect(original?.api_mode||'responses')+keyField(original?.has_key);
 }else if(kind==='models'){
  if(!state.providers.length)return toast('请先添加供应商');
  original=models().find(m=>m.id===id&&m.provider===provider);
  fields=providerSelect(provider||state.providers[0].id,!isNew)+field('id','模型名称',id,'text','填写上游 API 使用的模型 ID。',!isNew)+`<div class="field-grid">${field('context_window','上下文窗口（tokens）',original?.context_window||262144,'number')}${select('reasoning_effort','默认推理等级',modelReasoningOptions(original),original?.reasoning_effort||'')}</div>`+descriptionField(original?.description)+notice('这里只保存模型配置，不验证账号是否有该模型权限。请以供应商公布的模型能力为准。');
 }else{
  original=state.tools.find(t=>t.id===id);
  fields=field('id','MCP 名称',id,'text','名称创建后不可更改。',!isNew)+select('transport','传输方式',[['http','HTTP'],['stdio','标准输入输出（stdio）']],original?.transport||'http',!isNew)+field('url','HTTP 地址',original?.url||'','url')+field('command','启动命令',original?.command||'','text','stdio 连接填写可执行命令；已有参数和认证设置将保留。');
 }
 showDialog(isNew?'添加'+(kind==='providers'?'供应商':kind==='models'?'模型':routes[kind][0]):'编辑'+(kind==='providers'?'供应商':kind==='models'?'模型':routes[kind][0]),fields,async form=>{
  const values=Object.fromEntries(form),key=$('#provider-key');
  if(key&&key.dataset.changed!=='true')delete values.api_key;
  const destination=kind==='models'?(isNew?values.provider:provider):provider||values.provider;
  await mutate({kind,action:'save',id:id||values.id,provider:destination,values,revision:snapRevision});
  if(kind==='models'&&isNew&&destination!==selectedProvider)location.hash=modelLink(destination);
 },kind==='providers'&&!isNew?'':'更改会保存到本地');
 if(kind==='providers'){
  bindKeyField(id);bindCustomSelect($('.protocol-field'));
  if(id?.startsWith('adaptor-')){
   $('#dialog-title').textContent='连接设置';$('#editor').classList.add('adaptor-readonly');
   for(const input of $('#edit-form').querySelectorAll('input'))input.readOnly=true;
   for(const button of $('#edit-form').querySelectorAll('.select-trigger'))button.disabled=true;
   const save=$('#save'),hint=document.createElement('span');hint.className='adaptor-save-hint';hint.tabIndex=0;
   hint.dataset.tooltip='适配模型不允许编辑';hint.setAttribute('aria-label','适配模型不允许编辑');
   save.before(hint);hint.appendChild(save);save.disabled=true;save.setAttribute('aria-disabled','true');save.title='适配模型不允许编辑';submitAction=null;
  }
 }else if(kind==='models')bindCustomSelect($('.provider-select-field'));
}
async function editFile(id){if(id&&route==='knowledge'){location.hash='knowledge/'+encodeURIComponent(id);return;}if(!id&&route==='knowledge')return createKnowledge();const kind=route;let file;if(id)file=await api('file?id='+encodeURIComponent(id));const template=kind==='skills'?'---\nname: my-skill\ndescription: 说明此技能适用的任务。\n---\n\n# 工作步骤\n\n':'# 新文档\n\n';let content=(id?'':field('name','文件名'))+`<label class="field"><span>${file&&!file.writable?'只读内容':'文档内容'}</span><textarea name="content" spellcheck="false" ${file&&!file.writable?'readonly':''}>${esc(file?.content??template)}</textarea></label>`+(file?`<p class="mono" style="overflow-wrap:anywhere">${esc(file.path)}</p>`:'');showDialog(file?.name||('新建'+routes[kind][0]),content,file&&!file.writable?null:async form=>{await mutate({kind,action:id?'save':'create',id,name:form.get('name'),content:form.get('content'),revision:file?.revision});},kind==='knowledge'?'仅保存文档，不自动接入 Agent':file?.group==='public'?'修改公共技能会影响共用它的 Agent；原文件会备份':'保存前检测版本，原文件会备份');}
async function action(target){const name=target.dataset.action,id=target.dataset.id,provider=target.dataset.provider;try{
if(name==='open-channels'){location.hash='#channels';return;}if(name==='open-adaptor'){location.hash='#adaptor';return;}
if(name==='provider-toggle'){target.disabled=true;return await mutate({kind:'providers',action:'toggle',id,values:{enabled:target.getAttribute('aria-checked')!=='true'}});}
if(name==='global-toggle')return toggleGlobalConfiguration(target);
if(name==='open-resource-packages'){location.hash='#plugins';return;}if(name==='quick-guide'){location.hash='#guide';return;}if(name==='quick-setup')return openQuickSetup();if(name==='quick-add')return openQuickAdd();if(name==='quick-launch')return openQuickLaunch();
if(name==='provider-default'){const p=state.providers.find(p=>p.id===id);const model=p&&(p.default_model||Object.keys(p.models)[0]);if(!model)return toast('请先为该供应商添加模型');target.disabled=true;return await mutate({kind:'providers',action:'default',id});}
if(name==='model-test')return openModelTest(id,provider);if(name==='model-save'){const values={description:$('#main [name=description]').value,context_window:Number($('#main [name=context_window]').value),reasoning_effort:$('#main [name=reasoning_effort]').value};return await mutate({kind:'models',action:'save',id:selectedModel,provider:selectedProvider,values});}
if(name==='knowledge-retry')return await mutate({kind:'knowledge',action:'retry',id});if(name.startsWith('custom-'))return await customToolAction(name,id);if(name==='mcp-test')return testMcp(id);if(name==='skill-delete')return confirmSkillDelete(id);if(name==='skill-import')return importSkills();if(name==='skill-view')return viewSkill(id);if(name==='refresh')return load();if(name==='filter'){filter=id;return render();}if(name==='new'||name==='edit')return await (['models','tools'].includes(route)?editEntity(id,provider):editFile());if(name==='file')return await editFile(id);if(name==='execution'){const data=await api('execution?id='+encodeURIComponent(id));return showDialog(data.tool_name+' · 执行输出',`<pre class="output">${esc(data.content)}</pre>`,null,'输出已做凭据脱敏');}if(name==='toggle'&&route==='knowledge')return await toggleKnowledge(id);if(name==='toggle'){const item=(['skills','knowledge'].includes(route)?state.files:state.tools).find(f=>f.id===id);if(!item)throw Error('项目不存在，请刷新页面');target.disabled=true;return await mutate({kind:route,action:'toggle',id,values:{enabled:!item.enabled},...(route==='knowledge'?{knowledge_revision:state.knowledge_revision}:{})});}if(name==='default'){return showDialog('切换默认模型',`<p>将默认模型设为 <strong>${esc(id)}</strong>（${esc(provider)}）。切换到此供应商时使用该模型，已有会话保持当前模型。</p>`,async()=>mutate({kind:'models',action:'default',id,provider}));}if(name==='delete'&&route==='knowledge')return confirmKnowledgeDelete(id);if(name==='delete'){const kind=modelKind();let revision=state.revision;if(kind==='knowledge')revision=state.files.find(f=>f.id===id).revision;showDialog('确认删除',`<p>确定删除 <strong>${esc(kind==='knowledge'?state.files.find(f=>f.id===id).name:id)}</strong>？${kind==='providers'?'该供应商下的模型配置也会删除。':''}${kind==='knowledge'?'删除后无法恢复。':'更改前会保留配置备份。'}</p>`,async()=>{await mutate({kind,action:'delete',id,provider,revision});if(kind==='models'&&selectedModel)location.hash=modelLink(selectedProvider);});if(kind==='providers')$('#save').textContent='确认删除';}}catch(e){toast(e.message);target.disabled=false;}}
$('#main').addEventListener('toggle',event=>{
 const group=event.target.dataset.providerGroup;
 if(!group||!event.target.isConnected)return;
 if(event.target.open)collapsedProviderGroups.delete(group);else collapsedProviderGroups.add(group);
},true);
$('#main').addEventListener('click',e=>{const target=e.target.closest('[data-action]');if(target)action(target);});
$('#main').addEventListener('input',e=>{if(e.target.id==='search'){const pos=e.target.selectionStart;query=e.target.value;render();$('#search').focus();$('#search').setSelectionRange(pos,pos);}});
$('#edit-form').addEventListener('submit',async e=>{e.preventDefault();if(!submitAction)return;$('#save').disabled=true;$('#form-error').textContent='';try{await submitAction(new FormData(e.target));$('#editor').close();}catch(error){$('#form-error').textContent=error.message;}finally{$('#save').disabled=false;}});
$('#close-dialog').onclick=$('#cancel-dialog').onclick=()=>$('#editor').close();
$('#editor').addEventListener('close',()=>{if(!$('#editor').open){resetSkillDialog();const key=$('#provider-key');if(key){key.value='';key.type='password';}}});
$('#model-test-close').onclick=closeModelTest;$('#model-test-dialog').addEventListener('close',()=>{if(activeModelTest?.controller)activeModelTest.controller.abort();activeModelTest=null;});
$('#model-test-send').onclick=sendModelTest;
$('#model-test-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();sendModelTest();}});
$('#model-test-input').addEventListener('input',e=>{e.target.style.height='auto';e.target.style.height=Math.min(e.target.scrollHeight,150)+'px';});
$('#model-test-messages').addEventListener('click',e=>{const prompt=e.target.closest('[data-test-prompt]');if(prompt){$('#model-test-input').value=prompt.dataset.testPrompt;$('#model-test-input').dispatchEvent(new Event('input'));$('#model-test-input').focus();}});
$('#refresh').onclick=()=>load();$('#menu').onclick=()=>document.body.classList.toggle('open-nav');$('#scrim').onclick=()=>document.body.classList.remove('open-nav');
$('#nav').addEventListener('click',e=>{if(e.target.closest('.nav-group-toggle')){developerExpanded=!developerExpanded;renderNav();$('#nav .nav-group-toggle').focus();return;}const link=e.target.closest('a');if(link?.getAttribute('href')==='#config'&&route==='config'){e.preventDefault();window.CorkiConfig.reloadLatest().catch(error=>window.CorkiConfig.error(error));}});
window.addEventListener('hashchange',navigate);navigate();load(true);setInterval(()=>{if(route==='logs'&&!$('#editor').open&&!document.hidden)load(true);},15000);
