'use strict';
let skillImportGeneration=0;
const collapsedSkillGroups=new Set(['builtin']);
const skillPaths={briefcase:'M9 6V4h6v2 M3 7h18v13H3V7z M3 12h18 M10 12v3h4v-3',upload:'M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5',folder:'M3 7V4h6l3 3h9v13H3V7z',shield:'M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3zm-4 9 3 3 5-6',globe:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18',eye:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zm13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0',file:'M5 3h9l5 5v13H5V3zm9 0v6h5M8 13h8M8 17h6'};
function skillIcon(name){return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${skillPaths[name]||skillPaths.file}"/></svg>`;}
function skillInitials(name){const parts=String(name).split('-').filter(Boolean);return (parts.length>1?parts.slice(0,2).map(part=>Array.from(part)[0]).join(''):Array.from(String(name)).slice(0,2).join('')).toUpperCase();}
function skillsPage(){
 const all=state.files.filter(f=>f.kind==='skills'),rows=all.filter(f=>match(f)&&(filter==='all'||(filter==='enabled'?f.enabled:!f.enabled)));
 const groups=[['corki','扩展 Skills','folder'],['public','公共 Skills','globe'],['work','工作 Skills','briefcase'],['builtin','内置 Skills','shield']];
 return toolbar(rows.length,tabs([['all','全部'],['enabled','已启用'],['disabled','已停用']]),`<button class="button primary" data-action="skill-import">${skillIcon('upload')}导入 Skills</button>`)+groups.map(([group,title,i])=>{
 const items=rows.filter(f=>f.group===group),total=all.filter(f=>f.group===group).length;
 return `<details class="skill-section" data-skill-group="${group}" ${collapsedSkillGroups.has(group)?'':'open'}><summary class="skill-section-head"><span class="skill-group-icon ${group}">${skillIcon(i)}</span><div><h2>${title}<span>${total}</span></h2></div><svg class="skill-collapse-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></summary><div class="skill-grid">${items.map(f=>`<article class="skill-card ${f.enabled?'':'is-disabled'}"><div class="skill-card-top"><span class="skill-avatar">${esc(skillInitials(f.name))}</span><div class="skill-card-name"><button class="skill-name" data-action="skill-view" data-id="${f.id}" title="${esc(f.name)}">${esc(f.name)}</button>${f.source==='项目'?'<span class="skill-source">项目</span>':''}</div>${f.group!=='work'?`<button class="toggle ${f.enabled?'on':''}" role="switch" aria-checked="${f.enabled}" aria-label="${esc(f.name)} 启用状态" data-action="toggle" data-id="${f.id}"></button>`:''}</div><p class="skill-description" title="${esc(f.description)}">${esc(f.description||'暂无描述')}</p><div class="skill-card-bottom"><span>${f.valid===false?badge('格式异常','orange'):size(f.bytes)+' · '+when(f.updated)}</span>${f.group!=='builtin'?`<button class="skill-detail-button danger" data-action="skill-delete" data-id="${esc(f.id)}">删除</button>`:''}</div></article>`).join('')}</div>${items.length?'':`<div class="skill-empty">${skillIcon(i)}<span>${total?'没有符合筛选条件的技能':group==='public'?'暂无公共Skills':group==='corki'?'暂无扩展Skills':group==='work'?'暂无工作Skills':'暂无内置技能'}</span></div>`}</details>`;
 }).join('');
}
function resetSkillDialog(){skillImportGeneration++;document.querySelector('#editor')?.classList.remove('skill-dialog');}
async function viewSkill(id){return window.CorkiSkills.open(id,{api,toast,reload:()=>load(true)});}

function importSkills(){
 let payload=null,version=0,lastFiles=[];
 showDialog('导入 Skills',`<div class="skill-dropzone" id="skill-dropzone"><div class="skill-upload-heading"><span class="skill-upload-symbol">${skillIcon('upload')}</span><strong>将文件或目录拖入该区域</strong></div><div class="skill-upload-actions"><label class="button primary">${skillIcon('folder')}选择文件夹<input id="skill-folder-input" type="file" webkitdirectory multiple></label><label class="button">${skillIcon('file')}选择文件<input id="skill-file-input" type="file" accept=".zip,.md"></label></div><small>可拖入 ZIP 或 SKILL.md</small></div><fieldset class="skill-import-target"><legend>导入位置</legend><div class="skill-import-target-options"><label><input type="radio" name="skill-import-group" value="corki"><span class="skill-import-target-icon">${skillIcon('folder')}</span><span class="skill-import-target-copy"><strong>扩展 Skills</strong><small>通常只在Corki上使用的skills</small></span></label><label><input type="radio" name="skill-import-group" value="public" checked><span class="skill-import-target-icon">${skillIcon('globe')}</span><span class="skill-import-target-copy"><strong>公共 Skills</strong><small>机器上所有Agent的公共skills</small></span></label><label><input type="radio" name="skill-import-group" value="work"><span class="skill-import-target-icon">${skillIcon('briefcase')}</span><span class="skill-import-target-copy"><strong>工作 Skills</strong><small>仅在用户明确指定时使用</small></span></label></div></fieldset><div id="skill-import-preview" aria-live="polite"></div>`,async()=>{
   if(!payload)throw Error('请先选择并校验技能');
   await mutate({kind:'skills',action:'import',...payload});
 },'校验通过后可导入');
 $('#editor').classList.add('skill-dialog');$('#cancel-dialog').textContent='取消';$('#save').textContent='导入';$('#save').disabled=true;
 const generation=skillImportGeneration;
 const current=v=>generation===skillImportGeneration&&v===version&&$('#editor').open;
 const base64=file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('文件读取失败'));reader.readAsDataURL(file);});
 async function choose(files){
  lastFiles=[...files];const v=++version;payload=null;$('#save').disabled=true;$('#form-error').textContent='';$('#skill-import-preview').innerHTML='<div class="skill-validating"><span class="spinner"></span>正在读取并校验…</div>';
  try{
   files=[...files].filter(f=>!f.webkitRelativePath.split('/').some(p=>p==='.DS_Store'||p==='__MACOSX'));
   if(!files.length)throw Error('请选择技能文件');
   let candidate;
   if(files.length===1&&files[0].name.toLowerCase().endsWith('.zip'))candidate={archive:await base64(files[0])};
   else candidate={files:await Promise.all(files.map(async f=>({path:f.webkitRelativePath||f.name,data:await base64(f)})))};
   candidate.group=document.querySelector('[name="skill-import-group"]:checked').value;const result=await api('mutate',{kind:'skills',action:'validate-import',...candidate});
   if(!current(v))return;
   payload=candidate;
   $('#skill-import-preview').innerHTML=`<div class="skill-import-summary"><span class="badge green">校验通过</span><strong>${esc(result.name)}</strong><p>${esc(result.description)}</p></div><details class="skill-file-list"><summary>${result.files.length} 个文件 · ${size(result.bytes)}<span>查看文件清单</span></summary>${result.files.map(f=>`<div>${skillIcon('file')}<code>${esc(f.path)}</code><span>${size(f.bytes)}</span></div>`).join('')}</details>`;
   $('#save').disabled=false;
  }catch(e){if(current(v)){$('#skill-import-preview').innerHTML='';$('#form-error').textContent=e.message;}}
 }
 document.querySelectorAll('[name="skill-import-group"]').forEach(input=>input.onchange=()=>{if(lastFiles.length)choose(lastFiles);});
 $('#skill-folder-input').onchange=e=>choose(e.target.files);$('#skill-file-input').onchange=e=>choose(e.target.files);
 const zone=$('#skill-dropzone');zone.ondragover=e=>{e.preventDefault();zone.classList.add('dragover');};zone.ondragleave=()=>zone.classList.remove('dragover');zone.ondrop=e=>{e.preventDefault();zone.classList.remove('dragover');if([...e.dataTransfer.items].some(i=>i.webkitGetAsEntry?.()?.isDirectory)){version++;payload=null;$('#save').disabled=true;$('#skill-import-preview').innerHTML='';$('#form-error').textContent='导入文件夹请使用“选择文件夹”按钮';return;}choose(e.dataTransfer.files);};
}

document.querySelector('#main').addEventListener('toggle',event=>{
 const group=event.target.dataset.skillGroup;
 if(!group||!event.target.isConnected)return;
 if(event.target.open)collapsedSkillGroups.delete(group);else collapsedSkillGroups.add(group);
},true);

function confirmSkillDelete(id){
 const item=state.files.find(f=>f.id===id&&f.kind==='skills');
 if(!item||item.group==='builtin')return toast('此 Skill 不可删除');
 showDialog('删除 Skill',`<div class="knowledge-delete-file"><span class="knowledge-delete-icon">${skillIcon('folder')}</span><div><strong>${esc(item.name)}</strong><span>${{public:'公共 Skills',work:'工作 Skills',corki:'扩展 Skills'}[item.group]}</span></div></div><p class="knowledge-delete-description">将永久删除此 Skill 及其全部文件，删除后无法恢复。${item.group==='public'?'其他 Agent 也将无法再使用此公共 Skill。':''}</p>`,async()=>{
  await mutate({kind:'skills',action:'delete',id,revision:item.revision});
 },'');
 $('#editor').classList.add('knowledge-delete-dialog');
 $('#save').textContent='删除';
 $('#cancel-dialog').focus();
}
