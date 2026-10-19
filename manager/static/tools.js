'use strict';
const collapsedToolGroups=new Set(['builtin']);
function toolsPage(){
 const rows=state.tools.filter(match).filter(t=>filter==='all'||(filter==='enabled'?t.enabled:!t.enabled));
 const builtin=[['exec_command','执行终端命令'],['write_stdin','与运行中的进程交互'],['apply_patch','编辑工作区文件'],['view_image','查看本地图片'],['update_plan','管理任务计划']].filter(([name,description])=>match({name,description}));
 const section=(id,title,i,count,body)=>`<details class="skill-section tool-section" data-tool-group="${id}" ${collapsedToolGroups.has(id)?'':'open'}><summary class="skill-section-head"><span class="skill-group-icon">${icon(i)}</span><div><h2>${title}${count===null?'':`<span>${count}</span>`}</h2></div>${['mcp','custom'].includes(id)?`<span class="tool-heading-actions">${id==='custom'?`<button type="button" class="button" data-action="custom-import">${customImportIcon()}导入工具</button>`:''}${button(id==='mcp'?'添加 MCP':'添加工具',id==='mcp'?'new':'custom-new','',true)}</span>`:''}<svg class="skill-collapse-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></summary>${body}</details>`;
 const mcp=`<div class="skill-grid">${rows.map(t=>`<article class="skill-card mcp-card ${t.enabled?'':'is-disabled'}"><div class="skill-card-top"><span class="skill-avatar">${esc(skillInitials(t.id))}</span><div class="skill-card-name"><button class="skill-name" data-action="edit" data-id="${esc(t.id)}" title="${esc(t.id)}">${esc(t.id)}</button></div><button class="toggle ${t.enabled?'on':''}" role="switch" aria-checked="${t.enabled}" aria-label="${esc(t.id)} 启用状态" data-action="toggle" data-id="${esc(t.id)}"></button></div><p class="mcp-endpoint" title="${esc(t.url||t.command)}">${esc(t.url||t.command)}</p><div class="skill-card-bottom">${badge(t.transport==='http'?'HTTP':'本地 · stdio')}<div class="actions">${textButton('测试连接','mcp-test',attr(t.id))}${textButton('删除','delete',attr(t.id))}</div></div></article>`).join('')}</div>${rows.length?'':`<div class="skill-empty">${icon('tools')}<span>${state.tools.length?'没有符合筛选条件的 MCP 服务':'暂无 MCP 服务'}</span></div>`}`;
 return toolbar(rows.length+(state.custom_tools||[]).filter(match).filter(t=>filter==='all'||(filter==='enabled'?t.enabled:!t.enabled)).length,tabs([['all','全部'],['enabled','已启用'],['disabled','已停用']]))+section('mcp','MCP管理','tools',state.tools.length,mcp)+section('custom','自定义工具','developer',(state.custom_tools||[]).length,customToolsCards())+section('builtin','内置工具','models',null,`<div class="skill-grid">${builtin.map(([name,description])=>`<article class="skill-card builtin-tool-card"><div class="skill-card-top"><span class="skill-avatar">${icon('tools')}</span><div class="skill-card-name"><strong class="builtin-tool-name">${name}</strong></div></div><p class="skill-description">${description}</p></article>`).join('')}</div>`);
}
document.querySelector('#main').addEventListener('click',event=>{
 if(event.target.closest('.tool-heading-actions button'))event.preventDefault();
});
document.querySelector('#main').addEventListener('toggle',event=>{
 const group=event.target.dataset.toolGroup;if(!group||!event.target.isConnected)return;
 if(event.target.open)collapsedToolGroups.delete(group);else collapsedToolGroups.add(group);
},true);
async function editMcp(id){
 const isNew=!id;
 const loaded=isNew?{config:{},revision:state.revision}:await api('mcp-config?id='+encodeURIComponent(id));
 const original=loaded.config;let transport=original.transport||(original.url?'http':'stdio');
 const input=(name,label,value='',placeholder='')=>`<label class="field"><span>${label}</span><input name="${name}" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off"></label>`;
 showDialog(isNew?'添加 MCP':'连接设置',`<div id="mcp-editor">${input('id','服务名称',id||'','例如 my-tools')}<div class="field"><span>连接方式</span><div class="mcp-transports">${[['stdio','本地进程','developer'],['http','HTTP 服务','providers']].map(([value,label,i])=>`<button class="mcp-transport ${transport===value?'active':''}" type="button" data-transport="${value}" aria-pressed="${transport===value}" ${!isNew&&transport!==value?'disabled':''}>${icon(i)}${label}</button>`).join('')}</div></div><div id="mcp-stdio">${input('command','启动命令',original.command||'','例如 python、uvx 或 npx')}<div class="field"><span>启动参数</span><div id="mcp-args" class="mcp-rows"></div><button class="text-button mcp-add-row" id="mcp-add-arg" type="button">${icon('plus')}添加参数</button></div></div><div id="mcp-http">${input('url','服务地址',original.url||'','https://example.com/mcp')}</div><details class="mcp-advanced"><summary>更多配置<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="m6 9 6 6 6-6"/></svg></summary><div id="mcp-cwd">${input('cwd','工作目录',original.cwd||'','可选')}</div><div id="mcp-bearer">${input('bearer_token_env_var','Token 环境变量',original.bearer_token_env_var||'','可选')}</div><label class="field"><span>超时时间（秒）</span><input name="timeout_seconds" type="number" min="0.1" step="any" value="${esc(original.timeout_seconds||30)}"></label><div class="field"><span id="mcp-pairs-label">环境变量</span><div id="mcp-pairs" class="mcp-rows"></div><button class="text-button mcp-add-row" id="mcp-add-pair" type="button">${icon('plus')}添加条目</button></div></details></div>`,async form=>{
  const draft=readDraft(form);
  await mutate({...draft,kind:'tools',action:'save'});
 },'');
 const root=$('#mcp-editor');root.querySelector('[name=id]').readOnly=!isNew;
 function readDraft(form){
  const values={transport,timeout_seconds:Number(form.get('timeout_seconds'))};
  const pairs={};for(const row of root.querySelectorAll('#mcp-pairs .mcp-pair')){const key=row.querySelector('[data-key]').value.trim(),value=row.querySelector('[data-value]').value;if(!key&&!value)continue;if(!key)throw Error('请填写名称');if(Object.hasOwn(pairs,key))throw Error('存在重复名称');Object.defineProperty(pairs,key,{value,enumerable:true,writable:true,configurable:true});}
  if(transport==='stdio'){values.command=form.get('command');values.args=[...root.querySelectorAll('#mcp-args input')].map(input=>input.value);values.cwd=form.get('cwd').trim()||null;values.env=pairs;}
  else{values.url=form.get('url');values[original.http_headers!==undefined?'http_headers':'headers']=pairs;values.bearer_token_env_var=form.get('bearer_token_env_var').trim()||null;}
  return {id:id||form.get('id').trim(),values,is_new:isNew,revision:loaded.revision};
 }
 root.insertAdjacentHTML('beforeend',mcpTestMarkup('测试连接'));
 const probe=bindMcpTest(root.querySelector('#mcp-test'),()=>({...readDraft(new FormData(root.closest('form'))),kind:'tools',action:'test-connection'}));
 const dialog=$('#editor');
 const cleanup=()=>{probe.destroy();dialog.removeEventListener('close',cleanup);};dialog.addEventListener('close',cleanup);
 root.addEventListener('input',()=>probe.invalidate());
 root.addEventListener('click',event=>{if(event.target.closest('[data-transport],#mcp-add-arg,#mcp-add-pair,[data-remove],[aria-label=删除参数]'))probe.invalidate();});
 const removeIcon='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="m7 7 10 10M17 7 7 17"/></svg>';
 function addArg(value=''){const row=document.createElement('div');row.className='mcp-arg';row.innerHTML=`<input aria-label="启动参数" value="${esc(value)}" autocomplete="off"><button type="button" class="icon-button" aria-label="删除参数">${removeIcon}</button>`;row.querySelector('button').onclick=()=>{probe.invalidate();row.remove();};$('#mcp-args').append(row);return row;}
 function addPair(key='',value=''){const row=document.createElement('div');row.className='mcp-pair';row.innerHTML=`<input data-key aria-label="名称" placeholder="名称" value="${esc(key)}" autocomplete="off"><div class="mcp-secret"><input data-value aria-label="值" placeholder="值" type="password" value="${esc(value)}" autocomplete="new-password"><button type="button" class="icon-button" data-reveal aria-label="显示值">${icon('eye')}</button></div><button type="button" class="icon-button" data-remove aria-label="删除条目">${removeIcon}</button>`;row.querySelector('[data-remove]').onclick=()=>{probe.invalidate();row.remove();};row.querySelector('[data-reveal]').onclick=e=>{const input=row.querySelector('[data-value]'),button=e.currentTarget;const reveal=input.type==='password';input.type=reveal?'text':'password';button.innerHTML=icon(reveal?'eyeOff':'eye');button.setAttribute('aria-label',reveal?'隐藏值':'显示值');};$('#mcp-pairs').append(row);return row;}
 for(const value of original.args||[])addArg(value);
 let pairDrafts={stdio:Object.entries(original.env||{}),http:Object.entries(original.http_headers||original.headers||{})};
 function display(){root.querySelectorAll('[data-transport]').forEach(b=>{b.classList.toggle('active',b.dataset.transport===transport);b.setAttribute('aria-pressed',String(b.dataset.transport===transport));});$('#mcp-stdio').hidden=$('#mcp-cwd').hidden=transport!=='stdio';$('#mcp-http').hidden=$('#mcp-bearer').hidden=transport!=='http';$('#mcp-pairs-label').textContent=transport==='stdio'?'环境变量':'请求头';$('#mcp-pairs').innerHTML='';for(const [key,value] of pairDrafts[transport])addPair(key,value);}
 root.querySelectorAll('[data-transport]').forEach(b=>b.onclick=()=>{pairDrafts[transport]=[...root.querySelectorAll('.mcp-pair')].map(row=>[row.querySelector('[data-key]').value,row.querySelector('[data-value]').value]);transport=b.dataset.transport;display();});
 $('#mcp-add-arg').onclick=()=>addArg().querySelector('input').focus();$('#mcp-add-pair').onclick=()=>addPair().querySelector('input').focus();display();
}
document.querySelector('#editor').addEventListener('close',()=>{if(!$('#editor').open){const root=$('#mcp-editor');if(root)root.remove();}});
function mcpTestMarkup(label='重新测试',name=''){
 return `<div id="mcp-test" class="mcp-test-panel"><div class="mcp-test-heading"><strong>${esc(name)}</strong><button class="button" id="mcp-retest" type="button">${icon('tools')}${label}</button></div><div id="mcp-test-status" role="status" hidden></div><div class="mcp-test-json-head" hidden><span>tools/list 返回结果</span><button class="text-button" type="button" id="mcp-copy-result">复制 JSON</button></div><div id="mcp-test-json" hidden></div></div>`;
}
function testMcp(id){
 showDialog('测试连接',mcpTestMarkup('重新测试',id),null,'');
 const dialog=$('#editor');dialog.classList.add('mcp-test-dialog');$('#cancel-dialog').textContent='关闭';
 const probe=bindMcpTest($('#mcp-test'),()=>({kind:'tools',action:'test-connection',id,revision:state.revision}));
 const cleanup=()=>{probe.destroy();dialog.classList.remove('mcp-test-dialog');dialog.removeEventListener('close',cleanup);};dialog.addEventListener('close',cleanup);probe.run();
}
function bindMcpTest(root,payload){
 let viewer=null,active=true,generation=0;
 function invalidate(){generation++;viewer?.destroy();viewer=null;root.querySelector('#mcp-test-json').hidden=true;root.querySelector('.mcp-test-json-head').hidden=true;root.querySelector('#mcp-test-status').hidden=true;}
 async function run(){
  const current=++generation;const button=root.querySelector('#mcp-retest');button.disabled=true;viewer?.destroy();viewer=null;
  root.querySelector('#mcp-test-status').hidden=false;
  root.querySelector('#mcp-test-json').hidden=true;root.querySelector('.mcp-test-json-head').hidden=true;
  root.querySelector('#mcp-test-status').className='mcp-test-status';root.querySelector('#mcp-test-status').innerHTML='<span class="spinner"></span><span>正在连接并获取工具列表…</span>';
  try{
   const result=await api('mutate',payload());
   if(!active||!root.isConnected||current!==generation)return;
   const status=root.querySelector('#mcp-test-status');status.className='mcp-test-status '+(result.ok?'success':'failure');
   status.innerHTML=result.ok?`${icon('check')}<div><strong>连接成功 · 格式校验通过</strong><span>${result.response?.nextCursor?'本页 ':''}${result.tool_count} 个工具 · ${result.duration_ms} ms</span></div>`:`${icon('info')}<div><strong>测试失败</strong><span>${esc(result.error||'无法读取工具列表')}</span></div>`;
   const content=result.response===undefined?{error:result.error}:result.response;
   const target=root.querySelector('#mcp-test-json');target.hidden=false;root.querySelector('.mcp-test-json-head').hidden=false;
   viewer=window.CorkiJsonViewer(target,content);
   if(root.closest('#mcp-editor'))status.scrollIntoView({block:'start',behavior:'smooth'});
   root.querySelector('#mcp-copy-result').onclick=async()=>{try{await navigator.clipboard.writeText(JSON.stringify(content,null,2));toast('JSON 已复制');}catch{toast('复制失败，请在编辑器中选择内容复制');}};
  }catch(error){if(active&&root.isConnected&&current===generation){const status=root.querySelector('#mcp-test-status');status.className='mcp-test-status failure';status.textContent=error.message;}}
  finally{if(active&&root.isConnected)button.disabled=false;}
 }
 root.querySelector('#mcp-retest').onclick=run;
 return {run,invalidate,destroy(){active=false;invalidate();}};
}

function customToolsCards(){
 const rows=(state.custom_tools||[]).filter(match).filter(t=>filter==='all'||(filter==='enabled'?t.enabled:!t.enabled));
 return `<div class="skill-grid">${rows.map(t=>`<article class="skill-card ${t.enabled?'':'is-disabled'}"><div class="skill-card-top"><span class="skill-avatar">${esc(skillInitials(t.name))}</span><div class="skill-card-name"><button class="skill-name" data-action="custom-edit" data-id="${esc(t.id)}">${esc(t.name)}</button></div><button class="toggle ${t.enabled?'on':''}" role="switch" aria-checked="${t.enabled}" aria-label="${esc(t.name)} 启用状态" data-action="custom-toggle" data-id="${esc(t.id)}" ${t.error?'disabled':''}></button></div><p class="skill-description">${esc(t.description)}</p><div class="skill-card-bottom">${badge(t.transport==='http'?'HTTP':t.transport==='process'?'本地进程':'配置异常')}<div class="actions">${textButton('测试','custom-test',attr(t.id))}${textButton('删除','custom-delete',attr(t.id))}</div></div></article>`).join('')}</div>${rows.length?'':`<div class="skill-empty">${icon('developer')}<span>${(state.custom_tools||[]).length?'没有符合筛选条件的工具':'暂无自定义工具'}</span></div>`}`;
}
async function customToolAction(action,id){
 if(action==='custom-import')return importCustomTool();
 if(action==='custom-test'){const loaded=await api('custom-tool?id='+encodeURIComponent(id));return showCustomToolTest(loaded.config,{id,revision:loaded.revision});}
 if(action==='custom-new'||action==='custom-edit')return editCustomTool(id);
 const item=(state.custom_tools||[]).find(t=>t.id===id);if(!item)return;
 if(action==='custom-toggle')return mutate({kind:'custom-tools',action:'toggle',id,enabled:!item.enabled,revision:item.revision});
 if(action==='custom-delete'){showDialog('删除工具',`<p>确定删除 <strong>${esc(id)}</strong>？</p><p>删除后无法恢复。</p>`,()=>mutate({kind:'custom-tools',action:'delete',id,revision:item.revision}),'');$('#save').textContent='删除';}
}
async function editCustomTool(id){
 const loaded=id?await api('custom-tool?id='+encodeURIComponent(id)):{config:{},revision:null};
 const original=loaded.config;let transport=original.transport||'http';let schemaEditor,argsEditor,envEditor,headersEditor;
 const input=(name,label,value='',placeholder='')=>`<label class="field"><span>${label}</span><input name="${name}" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off" ${name==='name'&&id?'readonly':''}></label>`;
 showDialog(id?'编辑工具':'添加工具',`<div id="custom-tool-editor">${input('name','工具名称',id||'','例如 query_order')}<label class="field"><span>工具描述</span><textarea name="description" rows="2" placeholder="说明工具用途，帮助 Agent 选择调用">${esc(original.description||'')}</textarea></label><div class="field"><span>调用方式</span><div class="mcp-transports">${[['http','HTTP 服务','providers'],['process','本地进程','developer']].map(([value,label,i])=>`<button type="button" class="mcp-transport" data-custom-transport="${value}">${icon(i)}${label}</button>`).join('')}</div></div><div data-custom-http>${input('url','接口地址',original.url||'','https://example.com/api/tool')}<div class="field"><span>请求方法</span><input type="hidden" name="method" value="${esc(original.method||'POST')}"><div class="mcp-transports">${['GET','POST'].map(method=>`<button type="button" class="mcp-transport ${(original.method||'POST')===method?'active':''}" data-http-method="${method}" aria-pressed="${(original.method||'POST')===method}">${method}</button>`).join('')}</div></div></div><div data-custom-process>${input('command','启动命令',original.command||'','例如 python3')}<div class="field"><span>启动参数</span><div class="custom-json compact" id="custom-args"></div></div>${input('cwd','工作目录',original.cwd||'','默认使用当前工作空间')}</div><div class="field"><span>参数定义 <span class="subtle">JSON Schema</span></span><div class="custom-json" id="custom-schema"></div></div><details class="mcp-advanced"><summary>更多配置<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="m6 9 6 6 6-6"/></svg></summary><label class="field"><span>超时时间（秒）</span><input name="timeout_seconds" type="number" min="0.1" max="300" step="any" value="${esc(original.timeout_seconds||30)}"></label><div data-custom-http><div class="field"><span>请求头</span><div class="custom-json compact" id="custom-headers"></div></div></div><div data-custom-process><div class="field"><span>环境变量</span><div class="custom-json compact" id="custom-env"></div></div></div></details></div>`,async form=>{
  const draft=readDraft(form);await mutate({...draft,kind:'custom-tools',action:'save'});
 },'');
 function readDraft(form){
  const values={...original,name:id||form.get('name').trim(),description:form.get('description').trim(),transport,parameters:readSchema(),timeout_seconds:Number(form.get('timeout_seconds'))};
  if(transport==='http'){values.method=form.get('method');values.url=form.get('url').trim();values.headers=headersEditor.value();for(const key of ['command','args','env','cwd'])delete values[key];}
  else{values.command=form.get('command').trim();values.args=argsEditor.value();values.cwd=form.get('cwd').trim()||null;values.env=envEditor.value();for(const key of ['url','method','headers','env_headers'])delete values[key];}
  return {id:values.name,values,is_new:!id,revision:loaded.revision};
 }
 function readSchema(){const schema=schemaEditor.value();if(schema?.parameters&&schema?.transport)throw Error('此处只填写 parameters 内的参数定义；完整 JSON 请使用导入工具，或在格式指引中点击填入表单');return schema;}
 const root=$('#custom-tool-editor'),dialog=$('#editor');dialog.classList.add('custom-tool-dialog');
 root.querySelectorAll('[data-http-method]').forEach(button=>button.onclick=()=>{root.querySelector('[name=method]').value=button.dataset.httpMethod;root.querySelectorAll('[data-http-method]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});});
 let closeGuide=null;const guideButton=document.createElement('button');guideButton.type='button';guideButton.className='button';guideButton.id='custom-format-guide';guideButton.innerHTML=icon('info')+'格式指引';$('#cancel-dialog').before(guideButton);guideButton.onclick=()=>{closeGuide=showCustomToolGuide(transport,applyDemo);};
 let closeTest=null;const testButton=document.createElement('button');testButton.type='button';testButton.className='button';testButton.id='custom-test-draft';testButton.innerHTML=icon('tools')+'测试';guideButton.before(testButton);testButton.onclick=()=>{try{const draft=readDraft(new FormData(root.closest('form')));closeTest=showCustomToolTest(draft.values,draft);}catch(error){$('#form-error').textContent=error.message;}};
 schemaEditor=window.CorkiJsonEditor($('#custom-schema'),original.parameters||{type:'object',properties:{},required:[]},'参数定义');
 argsEditor=window.CorkiJsonEditor($('#custom-args'),original.args||[],'启动参数');
 envEditor=window.CorkiJsonEditor($('#custom-env'),original.env||{},'环境变量');
 headersEditor=window.CorkiJsonEditor($('#custom-headers'),original.headers||{},'请求头');
 function display(){root.querySelectorAll('[data-custom-http]').forEach(e=>e.hidden=transport!=='http');root.querySelectorAll('[data-custom-process]').forEach(e=>e.hidden=transport!=='process');root.querySelectorAll('[data-custom-transport]').forEach(b=>{b.classList.toggle('active',b.dataset.customTransport===transport);b.setAttribute('aria-pressed',String(b.dataset.customTransport===transport));});}
 function applyDemo(data){
  transport=data.transport;
  const values={name:id||data.name,description:data.description,url:data.url||'',command:data.command||'',cwd:data.cwd||'',timeout_seconds:data.timeout_seconds||30,method:data.method||'POST'};
  for(const [name,value] of Object.entries(values))root.querySelector(`[name=${name}]`).value=value;
  schemaEditor.setText(JSON.stringify(data.parameters,null,2));argsEditor.setText(JSON.stringify(data.args||[],null,2));envEditor.setText(JSON.stringify(data.env||{},null,2));headersEditor.setText(JSON.stringify(data.headers||{},null,2));
  root.querySelectorAll('[data-http-method]').forEach(b=>{const active=b.dataset.httpMethod===values.method;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});
  display();$('#form-error').textContent='';
 }
 root.querySelectorAll('[data-custom-transport]').forEach(b=>b.onclick=()=>{transport=b.dataset.customTransport;display();});display();
 const cleanup=()=>{closeTest?.();testButton.remove();closeGuide?.();guideButton.remove();for(const editor of [schemaEditor,argsEditor,envEditor,headersEditor])editor.destroy();root.remove();dialog.classList.remove('custom-tool-dialog');dialog.removeEventListener('close',cleanup);};dialog.addEventListener('close',cleanup);
}

function customImportIcon(){return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 16V3m-4 4 4-4 4 4M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/></svg>';}
function importCustomTool(){
 let editor,active=true,readVersion=0,draftVersion=0,pending=false;
 showDialog('导入工具',`<div id="custom-tool-import"><button type="button" id="custom-import-drop" class="custom-import-drop">${customImportIcon()}<span><strong>上传或拖入 JSON 文件</strong><small id="custom-import-filename" hidden></small></span></button><input type="file" id="custom-import-file" accept=".json,application/json" hidden><div class="custom-import-head"><span>工具配置</span><button type="button" class="text-button" id="custom-import-format">格式化</button></div><div class="custom-json" id="custom-import-json"></div><div id="custom-import-status" class="custom-import-status" role="status"></div></div>`,async()=>{
  if(pending)throw Error('文件读取中，请稍候');
  const raw=editor.text();if(new TextEncoder().encode(raw).length>256*1024)throw Error('工具配置不能超过 256 KB');
  const values=editor.value();
  if(!values||Array.isArray(values)||typeof values!=='object')throw Error('工具配置必须是 JSON 对象');
  if(typeof values.name!=='string'||!values.name)throw Error('请填写工具名称 name');
  await mutate({kind:'custom-tools',action:'save',id:values.name,values,is_new:true,revision:null});
 },'');
 const root=$('#custom-tool-import'),dialog=$('#editor'),drop=$('#custom-import-drop'),fileInput=$('#custom-import-file');
 dialog.classList.add('custom-tool-dialog');$('#save').textContent='保存工具';
 function changed(){draftVersion++;$('#form-error').textContent='';const status=root.querySelector('#custom-import-status');if(!editor.text().trim()){status.textContent='';status.className='custom-import-status';return;}try{editor.value();status.textContent='JSON 格式正确';status.className='custom-import-status valid';}catch{status.textContent='JSON 格式有误';status.className='custom-import-status invalid';}}
 editor=window.CorkiJsonEditor($('#custom-import-json'),undefined,'工具配置',changed,'复制json文本到此处');changed();
 async function readFiles(files){
  const request=++readVersion,version=draftVersion;pending=true;$('#save').disabled=true;$('#form-error').textContent='';
  try{
   if(files.length!==1)throw Error('请一次导入一个 JSON 文件');
   const file=files[0];if(!/\.json$/i.test(file.name))throw Error('请选择 JSON 文件');
   if(file.size>256*1024)throw Error('工具配置不能超过 256 KB');
   const text=(await file.text()).replace(/^\uFEFF/,'');
   if(!active||request!==readVersion||version!==draftVersion)return;
   let display=text;try{display=JSON.stringify(JSON.parse(text),null,2);}catch{}
   editor.setText(display);root.querySelector('#custom-import-filename').textContent=file.name;root.querySelector('#custom-import-filename').hidden=false;
  }catch(error){if(active&&request===readVersion)$('#form-error').textContent=error.message;}
  finally{if(active&&request===readVersion){pending=false;$('#save').disabled=false;fileInput.value='';}}
 }
 drop.onclick=()=>fileInput.click();fileInput.onchange=()=>readFiles([...fileInput.files]);
 // Handle file drops across the dialog, including over the JSON editor.
 root.addEventListener('dragover',event=>{if([...event.dataTransfer.types].includes('Files')){event.preventDefault();drop.classList.add('dragover');}});
 root.addEventListener('dragleave',event=>{if(!root.contains(event.relatedTarget))drop.classList.remove('dragover');});
 root.addEventListener('drop',event=>{if([...event.dataTransfer.types].includes('Files')){event.preventDefault();event.stopPropagation();drop.classList.remove('dragover');readFiles([...event.dataTransfer.files]);}},true);
 $('#custom-import-format').onclick=()=>{try{editor.setText(JSON.stringify(editor.value(),null,2));}catch(error){$('#form-error').textContent=error.message;}};
 const cleanup=()=>{active=false;readVersion++;editor.destroy();root.remove();dialog.classList.remove('custom-tool-dialog');dialog.removeEventListener('close',cleanup);};dialog.addEventListener('close',cleanup);
}

function customToolDemo(transport){
 const common={name:transport==='http'?'github_search_http':'github_search_curl',description:'按关键词搜索 GitHub 公开项目，返回前 3 个项目的名称、链接、描述和星标数量',transport,
  parameters:{type:'object',properties:{q:{type:'string',description:'搜索关键词，可加语言条件，例如 python language:python'}},required:['q'],additionalProperties:false},
  timeout_seconds:30,enabled:true};
 return transport==='http'?{...common,url:'https://api.github.com/search/repositories?per_page=3',method:'GET',headers:{Accept:'application/vnd.github+json','User-Agent':'Corki-Tool-Demo'}}:
  {...common,command:'python3',args:['-c','import json, subprocess, sys; params = json.load(sys.stdin); result = subprocess.run(["curl", "--silent", "--show-error", "--fail", "--max-time", "20", "--get", "--data-urlencode", "q=" + params["q"], "--data-urlencode", "per_page=3", "--header", "Accept: application/vnd.github+json", "--header", "User-Agent: Corki-Tool-Demo", "https://api.github.com/search/repositories"]); sys.exit(result.returncode)'],cwd:null,env:{}};
}
function showCustomToolGuide(transport,onApply){
 const data=customToolDemo(transport),dialog=document.createElement('dialog');let full=false;
 const info=[`工具名称：${data.name}`,`工具描述：${data.description}`,`调用方式：${transport==='http'?'HTTP 服务':'本地进程'}`,...(transport==='http'?[`接口地址：${data.url}`,`请求方法：${data.method}`]:[`启动命令：${data.command}`])].join('\n');
 dialog.className='custom-guide-dialog';dialog.setAttribute('aria-labelledby','custom-guide-title');
 dialog.innerHTML=`<div class="dialog-head"><h2 id="custom-guide-title">${transport==='http'?'HTTP 工具':'本地进程工具'} · 格式指引</h2><button type="button" class="icon-button" data-guide-close aria-label="关闭格式指引">✕</button></div><div class="custom-guide-body"><p>${transport==='http'?'通过 GET 请求搜索 GitHub 公开项目，参数自动编码到 URL，无需 API Key。':'Python 3 从 stdin 读取搜索词，再通过 curl 获取 GitHub 搜索结果；需要本机已安装 Python 3 和 curl。'}</p><div class="custom-guide-meta"><div class="custom-import-head"><label for="custom-guide-info">填写参考</label><button type="button" class="text-button" id="custom-guide-copy-info">复制信息</button></div><textarea id="custom-guide-info" readonly spellcheck="false" rows="5">${esc(info)}</textarea></div><div class="custom-guide-tabs" role="tablist" aria-label="示例内容"><button type="button" role="tab" aria-selected="true" data-guide-view="parameters" class="active">参数定义</button><button type="button" role="tab" aria-selected="false" data-guide-view="full">完整配置</button></div><div id="custom-guide-json" class="custom-json"></div><div class="custom-guide-example"><span>输入 <code>{"q":"python language:python"}</code></span><span>返回 GitHub 搜索结果 JSON（最多 3 个项目）</span></div></div><div class="dialog-foot"><span id="custom-guide-status" role="status"></span><button type="button" class="button" data-guide-close>关闭</button>${onApply?'<button type="button" class="button" id="custom-guide-apply">填入表单</button>':''}<button type="button" class="button primary" id="custom-guide-copy">复制参数定义</button></div>`;
 document.body.append(dialog);let viewer=window.CorkiJsonViewer(dialog.querySelector('#custom-guide-json'),data.parameters);
 dialog.querySelectorAll('[data-guide-view]').forEach(button=>button.onclick=()=>{full=button.dataset.guideView==='full';viewer.destroy();viewer=window.CorkiJsonViewer(dialog.querySelector('#custom-guide-json'),full?data:data.parameters);dialog.querySelectorAll('[data-guide-view]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-selected',String(b===button));});dialog.querySelector('#custom-guide-copy').textContent=full?'复制完整配置':'复制参数定义';dialog.querySelector('#custom-guide-status').textContent='';});
 if(onApply)dialog.querySelector('#custom-guide-apply').onclick=()=>{onApply(data);dialog.close();};
 let disposed=false;const dispose=()=>{if(disposed)return;disposed=true;viewer.destroy();dialog.remove();};
 dialog.addEventListener('close',dispose,{once:true});dialog.querySelectorAll('[data-guide-close]').forEach(button=>button.onclick=()=>dialog.close());
 dialog.querySelector('#custom-guide-copy').onclick=async()=>{try{await navigator.clipboard.writeText(JSON.stringify(full?data:data.parameters,null,2));if(!disposed)dialog.querySelector('#custom-guide-status').textContent='已复制';}catch{if(!disposed)dialog.querySelector('#custom-guide-status').textContent='复制失败，请在编辑器中选择内容复制';}};
 dialog.querySelector('#custom-guide-copy-info').onclick=async()=>{try{await navigator.clipboard.writeText(info);if(!disposed)dialog.querySelector('#custom-guide-status').textContent='信息已复制';}catch{if(!disposed)dialog.querySelector('#custom-guide-status').textContent='复制失败，请选择文本复制';}};
 dialog.showModal();return ()=>{if(!disposed){dialog.close();dispose();}};
}

function customArgumentExample(schema,depth=0){
 if(depth>5)return null;
 if(schema&&Object.hasOwn(schema,'default'))return schema.default;
 if(schema?.examples?.length)return schema.examples[0];
 if(schema?.enum?.length)return schema.enum[0];
 if(schema?.type==='object')return Object.fromEntries(Object.entries(schema.properties||{}).filter(([key,value])=>(schema.required||[]).includes(key)||Object.hasOwn(value,'default')).map(([key,value])=>[key,customArgumentExample(value,depth+1)]));
 if(schema?.type==='array')return [];
 if(schema?.type==='boolean')return false;
 if(['integer','number'].includes(schema?.type))return schema.minimum??0;
 if(schema?.type==='null')return null;
 return '';
}
function showCustomToolTest(config,payload){
 const dialog=document.createElement('dialog');dialog.className='custom-test-dialog';dialog.setAttribute('aria-labelledby','custom-test-title');
 dialog.innerHTML=`<div class="dialog-head"><h2 id="custom-test-title">测试工具 · ${esc(config.name||'未命名')}</h2><button type="button" class="icon-button" data-custom-test-close aria-label="关闭测试">✕</button></div><div class="custom-test-body"><div class="custom-import-head"><span>调用参数</span><button type="button" class="text-button" id="custom-test-format">格式化</button></div><div class="custom-json" id="custom-test-arguments"></div><div class="custom-test-expect"><span>返回格式</span><div class="custom-guide-tabs" role="group" aria-label="返回格式"><button type="button" class="active" data-result-format="json" aria-pressed="true">JSON</button><button type="button" data-result-format="text" aria-pressed="false">文本</button></div></div><div id="custom-test-status" role="status" hidden></div><div id="custom-test-result-wrap" hidden><div class="custom-import-head"><span>返回内容</span><button type="button" class="text-button" id="custom-test-copy">复制结果</button></div><div class="custom-json" id="custom-test-result"></div></div></div><div class="dialog-foot"><span>实际调用 · 最长 30 秒</span><button type="button" class="button" data-custom-test-close>关闭</button><button type="button" class="button primary" id="custom-test-run">开始测试</button></div>`;
 document.body.append(dialog);
 let active=true,busy=false,generation=0,expected='json',viewer=null,output='';
 const q=selector=>dialog.querySelector(selector),status=q('#custom-test-status'),run=q('#custom-test-run');
 function clear(){generation++;viewer?.destroy();viewer=null;status.hidden=true;q('#custom-test-result-wrap').hidden=true;}
 const input=window.CorkiJsonEditor(q('#custom-test-arguments'),customArgumentExample(config.parameters)||{},'调用参数',clear);
 q('#custom-test-format').onclick=()=>{try{input.setText(JSON.stringify(input.value(),null,2));}catch(error){status.hidden=false;status.className='mcp-test-status failure';status.textContent=error.message;}};
 dialog.querySelectorAll('[data-result-format]').forEach(button=>button.onclick=()=>{expected=button.dataset.resultFormat;dialog.querySelectorAll('[data-result-format]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});clear();});
 run.onclick=async()=>{
  if(busy)return;clear();const current=generation;busy=true;run.disabled=true;run.textContent='测试中…';status.hidden=false;status.className='mcp-test-status';status.innerHTML='<span class="spinner"></span>正在执行工具…';
  try{
   const args=input.value();if(!args||typeof args!=='object'||Array.isArray(args))throw Error('调用参数必须是 JSON 对象');
   const result=await api('mutate',{...payload,kind:'custom-tools',action:'test',arguments:args,expected_format:expected});
   if(!active||current!==generation)return;
   status.className='mcp-test-status '+(result.ok?'success':'failure');status.innerHTML=result.ok?`${icon('check')}<div><strong>执行成功 · ${result.format==='json'?'JSON 格式有效':'非空文本'} </strong><span>${result.duration_ms} ms</span></div>`:`${icon('info')}<div><strong>测试失败</strong><span>${esc(result.error||'执行失败')}</span></div>`;
   output=result.format==='json'?JSON.stringify(result.result,null,2):result.output||'';
   q('#custom-test-result-wrap').hidden=false;viewer=result.format==='json'?window.CorkiJsonViewer(q('#custom-test-result'),result.result):window.CorkiTextViewer(q('#custom-test-result'),output);
   status.scrollIntoView({block:'nearest'});
  }catch(error){if(active&&current===generation){status.className='mcp-test-status failure';status.textContent=error.message;}}
  finally{if(active){busy=false;run.disabled=false;run.textContent='重新测试';}}
 };
 q('#custom-test-copy').onclick=async()=>{try{await navigator.clipboard.writeText(output);toast('结果已复制');}catch{toast('复制失败，请选择内容复制');}};
 let disposed=false;const cleanup=()=>{if(disposed)return;disposed=true;active=false;input.destroy();viewer?.destroy();dialog.remove();};
 dialog.addEventListener('close',cleanup,{once:true});dialog.querySelectorAll('[data-custom-test-close]').forEach(button=>button.onclick=()=>dialog.close());
 dialog.showModal();return ()=>{if(!disposed){dialog.close();cleanup();}};
}
