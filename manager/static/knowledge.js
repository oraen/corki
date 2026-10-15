'use strict';
const collapsedKnowledgeGroups=new Set(['builtin']);
const knowledgeEditIcon='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9M4 16.5 16.5 4a2.1 2.1 0 0 1 3 3L7 19.5 3 21l1-4.5Z"/></svg>';
function knowledgePage(){
 const all=state.files.filter(f=>f.kind==='knowledge');
 const rows=all.filter(f=>match(f)&&(filter==='all'||(filter==='enabled'?f.enabled:!f.enabled)));
 const groups=[['user','用户知识库','folder'],['builtin','内置知识库','shield']];
 return toolbar(rows.length,tabs([['all','全部'],['enabled','已启用'],['disabled','已停用']]),`<button class="button primary" data-action="new">${icon('plus')}添加文档</button>`)
  +notice('知识库文档目前仅供管理，尚未接入 Agent 自动检索。')
  +groups.map(([group,title,symbol])=>{
   const items=rows.filter(f=>f.group===group),total=all.filter(f=>f.group===group).length;
   return `<details class="skill-section knowledge-section" data-knowledge-group="${group}" ${collapsedKnowledgeGroups.has(group)?'':'open'}><summary class="skill-section-head"><span class="skill-group-icon ${group==='builtin'?'builtin':'corki'}">${skillIcon(symbol)}</span><div><h2>${title}<span>${total}</span></h2></div><svg class="skill-collapse-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></summary><div class="skill-grid">${items.map(f=>`<article class="skill-card knowledge-card ${f.enabled?'':'is-disabled'}"><div class="skill-card-top"><span class="skill-avatar knowledge-avatar">${icon('knowledge')}</span><div class="skill-card-name"><button class="skill-name" data-action="file" data-id="${esc(f.id)}" title="${esc(f.name)}">${esc(f.name)}</button></div><button class="toggle ${f.enabled?'on':''}" role="switch" aria-checked="${f.enabled}" aria-label="${esc(f.name)} 启用状态" data-action="toggle" data-id="${esc(f.id)}"></button></div><p class="skill-description" title="${esc(f.description)}">${esc(f.description||'暂无摘要')}</p><div class="skill-card-bottom"><span>${size(f.bytes)} · ${when(f.updated)}</span><div class="knowledge-card-actions"><button class="skill-detail-button" data-action="file" data-id="${esc(f.id)}">${f.writable?knowledgeEditIcon:skillIcon('eye')}${f.writable?'编辑':'查看'}</button>${f.writable?`<button class="skill-detail-button danger" data-action="delete" data-id="${esc(f.id)}">删除</button>`:''}</div></div></article>`).join('')}</div>${items.length?'':`<div class="skill-empty">${skillIcon(symbol)}<span>${total?'没有符合筛选条件的文档':group==='builtin'?'暂无内置知识库':'暂无用户知识库'}</span></div>`}</details>`;
  }).join('');
}
document.querySelector('#main').addEventListener('toggle',event=>{
 const group=event.target.dataset.knowledgeGroup;
 if(!group||!event.target.isConnected)return;
 if(event.target.open)collapsedKnowledgeGroups.delete(group);else collapsedKnowledgeGroups.add(group);
},true);
function createKnowledge(){
 const glyphs={upload:'M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5',text:'M4 5h16M12 5v15M8 20h8',web:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18'};
 const glyph=name=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${glyphs[name]}"/></svg>`;
 showDialog('新建知识库',`<div id="knowledge-import"><label class="field"><span>文件名</span><input name="name" placeholder="不含扩展名" autocomplete="off" maxlength="80" required></label><div class="field"><span>导入方式</span><div class="knowledge-methods" role="group" aria-label="导入方式">${[['upload','上传文档'],['text','输入文本'],['web','解析网页']].map(([id,label])=>`<button type="button" class="knowledge-method ${id==='upload'?'active':''}" data-method="${id}" aria-pressed="${id==='upload'}">${glyph(id)}<span>${label}</span></button>`).join('')}</div></div><div id="knowledge-upload"><label class="knowledge-dropzone" tabindex="0">${glyph('upload')}<strong>点击上传或拖入文档</strong><span>Markdown · TXT · PDF · DOCX</span><input id="knowledge-file" type="file" accept=".md,.txt,.pdf,.docx" hidden></label><div class="knowledge-source" id="knowledge-source" aria-live="polite"></div></div><div id="knowledge-web" hidden><div class="knowledge-url"><input id="knowledge-url" type="url" placeholder="粘贴网页链接" aria-label="网页地址"><button class="button" type="button" id="knowledge-parse">解析</button></div></div><label class="field knowledge-content" id="knowledge-content-field" hidden><span>文档内容</span><textarea name="content" placeholder="输入文档内容" spellcheck="false"></textarea></label><div id="knowledge-progress" role="status"></div></div>`,async form=>{
  if(pending)throw Error('请等待解析完成');
  const content=form.get('content');if(!content.trim())throw Error('请上传文档、输入文本或解析网页');
  await mutate({kind:'knowledge',action:'create',name:form.get('name'),content});
 },'');
 const root=$('#knowledge-import'),name=root.querySelector('[name=name]'),editor=root.querySelector('textarea'),save=$('#save');
 let method='upload',pending=false,version=0;
 save.textContent='保存';
 const sync=()=>{editor.readOnly=pending;save.disabled=pending||!name.value.trim()||!editor.value.trim();$('#knowledge-content-field').hidden=method!=='text'&&!editor.value;};
 name.oninput=editor.oninput=sync;
 root.querySelectorAll('[data-method]').forEach(button=>button.onclick=()=>{
  method=button.dataset.method;version++;pending=false;$('#knowledge-progress').textContent='';$('#form-error').textContent='';
  root.querySelectorAll('[data-method]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});
  $('#knowledge-upload').hidden=method!=='upload';$('#knowledge-web').hidden=method!=='web';sync();if(method==='text')editor.focus();
 });
 async function parse(task){
  const request=++version;pending=true;$('#form-error').textContent='';$('#knowledge-progress').innerHTML='<span class="spinner"></span>正在解析…';sync();
  try{const result=await task();if(request!==version||!root.isConnected||!$('#editor').open)return;editor.value=result.content;if(result.source)root.querySelector('#knowledge-source').textContent=result.source;if(!name.value.trim())name.value=result.name;}
  catch(error){if(request===version&&root.isConnected&&$('#editor').open)$('#form-error').textContent=error.message;}
  finally{if(request===version&&root.isConnected&&$('#editor').open){pending=false;$('#knowledge-progress').textContent='';sync();}}
 }
 const upload=file=>{
  if(!file)return;
  parse(async()=>{
   if(file.size>8*1024*1024)throw Error('请选择不超过 8 MB 的文档');
   if(!/\.(md|txt|pdf|docx)$/i.test(file.name))throw Error('请选择 Markdown、TXT、PDF 或 DOCX 文档');
   const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('文件读取失败'));reader.readAsDataURL(file);});
   const result=await api('mutate',{kind:'knowledge',action:'parse-document',filename:file.name,data});
   return {...result,source:file.name};
  });
 };
 $('#knowledge-file').onchange=e=>upload(e.target.files[0]);
 const zone=root.querySelector('.knowledge-dropzone');
 zone.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('#knowledge-file').click();}};
 zone.ondragover=e=>{e.preventDefault();zone.classList.add('dragover');};zone.ondragleave=()=>zone.classList.remove('dragover');
 zone.ondrop=e=>{e.preventDefault();zone.classList.remove('dragover');if(e.dataTransfer.files.length!==1){$('#form-error').textContent='请一次上传一个文档';return;}upload(e.dataTransfer.files[0]);};
 $('#knowledge-parse').onclick=()=>parse(()=>api('mutate',{kind:'knowledge',action:'parse-web',url:$('#knowledge-url').value.trim()}));
 $('#knowledge-url').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$('#knowledge-parse').click();}};
 sync();
}
