'use strict';

function quickToolsCards(){
 return `<section class="panel quick-tools-panel"><div class="panel-head"><h2>快速使用</h2></div><div class="quick-tools-grid">${[
  ['guide','使用指引','从首次配置到完成一个任务','knowledge'],
  ['setup','快速配置','快速使用厂商和模型初始化配置','config'],
  ['launch','快速启动','打开 CLI 或桌面应用','developer']
 ].map(([id,title,note,i])=>`<button type="button" class="quick-tool-card" data-action="quick-${id}"><span class="quick-tool-icon">${icon(i)}</span><span><strong>${title}</strong><small>${note}</small></span></button>`).join('')}</div></section>`;
}

function serviceDevelopmentCards(){
 return `<section class="panel quick-tools-panel" aria-labelledby="service-development-title"><div class="panel-head"><h2 id="service-development-title">服务开发</h2></div><div class="quick-tools-grid"><div class="quick-tool-card toolbox-card service-development-card"><span class="quick-tool-icon">${icon('providers')}</span><strong>应用管理</strong></div></div></section>`;
}

function toolboxCards(){
 return `<section class="panel quick-tools-panel" aria-labelledby="toolbox-title"><div class="panel-head"><h2 id="toolbox-title">工具箱</h2></div><div class="quick-tools-grid"><button type="button" class="quick-tool-card toolbox-card" data-action="open-adaptor"><span class="quick-tool-icon">${icon('providers')}</span><strong>套餐转发</strong></button><button type="button" class="quick-tool-card toolbox-card" data-action="open-resource-packages"><span class="quick-tool-icon">${window.CorkiResourcePackages.icon()}</span><strong>插件管理</strong></button><button type="button" class="quick-tool-card toolbox-card" data-action="open-channels"><span class="quick-tool-icon">${icon('tools')}</span><strong>转发网关</strong></button></div></section>`;
}

function guidePage(){
 const steps=[
  ['01','准备密钥','从你想使用的模型供应商获取 API Key。这里以 DeepSeek 为例；密钥只需在下一步填入管理页。',''],
  ['02','配置默认模型','在「快速配置」里选 DeepSeek、deepseek-flash，填入 API Key 并确认。Corki 会把它设为默认模型。','<button type="button" class="guide-action" data-action="quick-setup">打开快速配置 →</button>'],
  ['03','先试一句话','进入「简单对话」，发送“请用一句话介绍你自己”。看到流式回复后，就可以开始正式任务。','<a class="guide-action" href="#chat">打开简单对话 →</a>'],
  ['04','在工作目录启动','打开终端，运行下面的示例命令。Corki 会以启动时所在目录作为工作空间。','']
 ];
 const areas=[
  ['models','模型管理','查看供应商和模型，切换默认模型，测试模型回复。'],
  ['chat','简单对话','直接和已配置的模型聊天，记录保存在本地浏览器。'],
  ['skills','Skills','查看、导入和管理技能；内置技能只读。'],
  ['knowledge','知识库','整理 Markdown 或文本资料；目前不自动提供给 Agent 检索。'],
  ['tools','工具','管理 MCP 与自定义工具，并测试连接或调用。'],
  ['memories','记忆管理','查看和编辑 Corki 的本地记忆。'],
  ['logs','运行日志','回看会话和工具调用记录。'],
  ['config','配置文件','查看配置手册、编辑设置和恢复历史版本。']
 ];
 return `<div class="guide-page"><section class="guide-hero"><span class="guide-hero-mark">✳</span><div><span class="guide-kicker">快速入门</span><h1>用 Corki 完成第一项任务</h1><p>以 DeepSeek 为例，完成配置、验证，然后在自己的工作目录启动。</p></div></section><div class="guide-section-heading"><h2>从零开始</h2><span>4 步完成</span></div><section class="guide-steps">${steps.map(([number,title,description,action])=>`<article class="guide-step"><span class="guide-step-number">${number}</span><div class="guide-step-content"><h3>${title}</h3><p>${description}</p>${number==='04'?`<div class="guide-code"><span>终端</span><code>mkdir -p ~/corki-demo<br>cd ~/corki-demo<br>corki</code></div><p class="guide-example">启动后可以输入：<q>创建一个简洁的待办清单网页，支持添加、完成和删除任务。</q></p><button type="button" class="guide-action" data-action="quick-launch">或使用快速启动 →</button>`:action}</div></article>`).join('')}</section><div class="guide-section-heading guide-features-heading"><h2>管理页面能做什么</h2></div><section class="guide-feature-grid">${areas.map(([id,title,description])=>`<a class="guide-feature" href="#${id}"><span class="guide-feature-icon">${icon(id)}</span><strong>${title}</strong><p>${description}</p><span class="guide-feature-arrow">→</span></a>`).join('')}</section></div>`;
}

function quickDialog(title,body,label,onSubmit){
 const dialog=document.createElement('dialog');dialog.className='quick-dialog';
 dialog.innerHTML=`<form class="quick-form"><header class="quick-header"><span class="quick-tool-icon">${icon(label==='启动 Corki'?'developer':'config')}</span><h2>${title}</h2><button type="button" class="icon-button quick-close" aria-label="关闭">✕</button></header><fieldset class="quick-body">${body}</fieldset><footer class="quick-footer"><span class="quick-error" role="alert"></span><button type="button" class="button quick-cancel">取消</button><button type="submit" class="button primary quick-confirm">${label}</button></footer></form>`;
 document.body.append(dialog);let saving=false;
 const close=()=>{if(!saving)dialog.close();};
 dialog.querySelector('.quick-close').onclick=close;dialog.querySelector('.quick-cancel').onclick=close;
 dialog.addEventListener('cancel',e=>{if(saving)e.preventDefault();});
 dialog.addEventListener('close',()=>dialog.remove(),{once:true});
 dialog.querySelector('form').onsubmit=async e=>{e.preventDefault();if(saving)return;const error=dialog.querySelector('.quick-error'),button=dialog.querySelector('.quick-confirm');error.textContent='';saving=true;button.disabled=true;button.textContent='处理中…';dialog.querySelector('fieldset').disabled=true;try{await onSubmit(dialog);dialog.close();}catch(err){error.textContent=err.message;}finally{saving=false;button.disabled=false;button.textContent=label;dialog.querySelector('fieldset').disabled=false;}};
 dialog.addEventListener('click',e=>{for(const menu of dialog.querySelectorAll('.quick-select[open]'))if(!menu.contains(e.target))menu.open=false;});
 dialog.addEventListener('keydown',e=>{if(e.key==='Escape'){const menu=dialog.querySelector('.quick-select[open]');if(menu){e.preventDefault();e.stopPropagation();menu.open=false;menu.querySelector('summary').focus();}}});
 dialog.showModal();return dialog;
}

function quickSelect(label,summary,content,extra=''){
 return `<div class="quick-field"><span>${label}</span><details class="quick-select" ${extra}><summary><span>${summary}</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5"/></svg></summary><div class="quick-options">${content}</div></details></div>`;
}

function quickVendorLabel(provider){
 return `<span class="quick-vendor-label"><img src="/provider-icons/${esc(provider.id)}.png" alt="" width="22" height="22"><span>${esc(provider.label)}</span></span>`;
}

async function openQuickSetup(mode='configure'){
 const adding=mode==='add';
 const catalog=await api('quick-templates');
 if(adding)catalog.providers=catalog.providers.filter(p=>{
  const existing=state.providers.find(item=>item.id===p.id);
  return !existing||Object.keys(p.models).some(model=>!Object.hasOwn(existing.models,model));
 });
 if(!catalog.providers.length){toast('模板中的模型都已添加');return;}
 const drafts=new Map();let selected=catalog.providers[0]?.id||'';
 const option=(value,label,checked,name,note='',multiple=false)=>`<label class="quick-option"><input type="${multiple?'checkbox':'radio'}" name="${name}" value="${esc(value)}" ${checked?'checked':''}><span>${esc(label)}${note?`<small>${esc(note)}</small>`:''}</span></label>`;
 const vendorOption=p=>`<label class="quick-option"><input type="radio" name="quick-vendor" value="${esc(p.id)}" ${p.id===selected?'checked':''}>${quickVendorLabel(p)}</label>`;
 const dialog=quickDialog(adding?'快速添加':'快速配置',`${quickSelect(adding?'供应商':'厂商',quickVendorLabel(catalog.providers[0]),catalog.providers.map(vendorOption).join(''),'data-vendors')}<div class="quick-setup-fields"></div>`,adding?'确认添加':'确认配置',async()=>{
  capture();const draft=drafts.get(selected);
  if(!selected||(adding?!draft?.models?.length:!draft?.model))throw new Error(adding?'请至少选择一个模型':'请选择厂商和模型');
  const existing=adding&&state.providers.find(p=>p.id===selected);
  if(!existing?.has_key&&!draft.api_key.trim())throw new Error('请填写 API Key');
  const result=await api('mutate',{kind:'quick-tools',action:adding?'add':'configure',revision:catalog.revision,provider:selected,...(adding?{models:draft.models}:{model:draft.model}),api_key:draft.api_key});
  toast(result.message);await load(true);
 });
 dialog.classList.add('quick-setup-dialog');
 function capture(){const key=dialog.querySelector('[data-key]'),models=[...dialog.querySelectorAll('[data-models] input:checked')];if(models.length||adding)drafts.set(selected,{api_key:key?.value??drafts.get(selected)?.api_key??'',model:models[0]?.value||'',models:models.map(item=>item.value)});}
 function renderFields(){
  const p=catalog.providers.find(p=>p.id===selected);if(!p)return;
  const existing=adding&&state.providers.find(item=>item.id===selected);
  const modelIds=Object.keys(p.models).filter(id=>!existing||!Object.hasOwn(existing.models,id));
  if(!drafts.has(selected))drafts.set(selected,{api_key:'',model:adding?'':modelIds[0],models:[]});
  const draft=drafts.get(selected);
  const options=(adding?`<div class="quick-model-actions"><button type="button" class="text-button" data-select-all aria-pressed="${draft.models.length===modelIds.length}">${draft.models.length===modelIds.length?'取消全选':'全选'}</button></div>`:'')+modelIds.map(id=>option(id,p.models[id].label||id,adding?draft.models.includes(id):id===draft.model,'quick-model','',adding)).join('');
  const summary=adding?(draft.models.length?`已选 ${draft.models.length} 个模型`:'选择模型'):(p.models[draft.model]?.label||draft.model);
  dialog.querySelector('.quick-setup-fields').innerHTML=`${quickSelect('模型',esc(summary),options,'data-models')}${existing?.has_key?'<div class="quick-existing-key">沿用已配置的 API Key</div>':`<label class="quick-field"><span>API Key</span><span class="quick-secret"><input data-key type="password" value="${esc(draft.api_key)}" autocomplete="new-password" placeholder="输入 ${esc(p.label)} API Key" aria-label="API Key"><button type="button" class="icon-button" data-reveal aria-label="显示 API Key">${icon('eye')}</button></span></label>`}`;
 }
 dialog.addEventListener('change',e=>{
  if(e.target.closest('[data-vendors]')){capture();selected=e.target.value;const menu=e.target.closest('details');menu.querySelector('summary>span').innerHTML=quickVendorLabel(catalog.providers.find(p=>p.id===selected));menu.open=false;renderFields();}
  else if(e.target.closest('[data-models]')){capture();const menu=e.target.closest('details');const p=catalog.providers.find(p=>p.id===selected);menu.querySelector('summary>span').textContent=adding?(drafts.get(selected).models.length?`已选 ${drafts.get(selected).models.length} 个模型`:'选择模型'):(p.models[e.target.value].label||e.target.value);if(adding)syncSelectAll(menu);else menu.open=false;}
 });
 function syncSelectAll(menu){const inputs=[...menu.querySelectorAll('input[name="quick-model"]')],all=inputs.length>0&&inputs.every(input=>input.checked),button=menu.querySelector('[data-select-all]');button.textContent=all?'取消全选':'全选';button.setAttribute('aria-pressed',String(all));}
 dialog.addEventListener('click',e=>{const button=e.target.closest('[data-select-all]');if(!button||button.disabled)return;const menu=button.closest('[data-models]'),inputs=[...menu.querySelectorAll('input[name="quick-model"]')],checked=!inputs.every(input=>input.checked);for(const input of inputs)input.checked=checked;capture();menu.querySelector('summary>span').textContent=checked?`已选 ${inputs.length} 个模型`:'选择模型';syncSelectAll(menu);});
 dialog.addEventListener('click',e=>{const button=e.target.closest('[data-reveal]');if(button){const input=button.previousElementSibling;input.type=input.type==='password'?'text':'password';button.innerHTML=icon(input.type==='password'?'eye':'eyeOff');button.setAttribute('aria-label',input.type==='password'?'显示 API Key':'隐藏 API Key');}});
 dialog.addEventListener('close',()=>{drafts.clear();},{once:true});renderFields();
}

function openQuickAdd(){return openQuickSetup('add');}

function openQuickLaunch(){
 const dialog=quickDialog('快速启动',`${quickSelect('启动方式','桌面应用',`<label class="quick-option"><input type="radio" name="launch-mode" value="cli"><span>CLI</span></label><label class="quick-option"><input type="radio" name="launch-mode" value="desktop" checked><span>桌面应用</span></label>`,'data-launch-mode')}<label class="quick-field"><span>工作目录</span><div class="quick-directory"><input id="quick-directory" placeholder="~/.corki/main/default" autocomplete="off" spellcheck="false"><button type="button" class="button" data-pick-directory>${icon('knowledge')}选择目录</button></div></label><p class="quick-directory-note">留空使用默认目录</p>`,'启动 Corki',async()=>{
  const result=await api('mutate',{kind:'quick-tools',action:'launch',mode:dialog.querySelector('[name="launch-mode"]:checked').value,directory:dialog.querySelector('#quick-directory').value});toast(result.message);
 });
 dialog.classList.add('quick-launch-dialog');
 dialog.querySelector('[data-launch-mode]').addEventListener('change',e=>{const menu=e.currentTarget;menu.querySelector('summary>span').textContent=e.target.value==='desktop'?'桌面应用':'CLI';menu.open=false;});
 dialog.querySelector('[data-pick-directory]').onclick=async e=>{const button=e.currentTarget,confirm=dialog.querySelector('.quick-confirm');button.disabled=true;confirm.disabled=true;dialog.querySelector('.quick-error').textContent='';try{const result=await api('mutate',{kind:'quick-tools',action:'choose-directory'});if(dialog.isConnected&&result.path)dialog.querySelector('#quick-directory').value=result.path;}catch(error){if(dialog.isConnected)dialog.querySelector('.quick-error').textContent=error.message;}finally{button.disabled=false;confirm.disabled=false;}};
 dialog.querySelector('#quick-directory').focus();
}
