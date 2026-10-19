'use strict';
window.CorkiCron = (() => {
 let root, timer, generation=0, snapshot={jobs:[]}, search='', pending=false, view='tasks', recordOffset=0, records={records:[],total:0,limit:20}, recordNames=new Set(), filterOpen=false, filterQuery='';
 const active=j=>['claimed','running'].includes(j.last_run?.status);
 const stamp=v=>v?when(new Date(v*1000).toISOString()):'—';
 const labels={claimed:'等待执行',running:'执行中',completed:'已完成',failed:'失败',unknown:'待确认',cancelled:'已停止'};
 function elapsed(run){
  if(!Number.isFinite(run.started)||!Number.isFinite(run.finished)||run.finished<run.started)return '';
  const seconds=Math.round(run.finished-run.started),hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60);
  return [hours?`${hours}h`:'',minutes?`${minutes}m`:'',`${seconds%60}s`].filter(Boolean).join(' ');
 }
 function recordStatus(run){
  const duration=run.status==='completed'?elapsed(run):'';
  return (labels[run.status]||esc(run.status))+(duration?`（${duration}）`:'');
 }
 const glyph=()=>icon('clock');
 function cadence(job){
  const s=job.normalized;
  if(s.kind==='once')return `仅一次 · ${stamp(s.at)}`;
  if(s.kind==='interval'){const n=s.seconds;return `每 ${n%86400===0?n/86400+' 天':n%3600===0?n/3600+' 小时':n/60+' 分钟'}`;}
  const hourly=/^(\d+) \* \* \* \*$/.exec(job.schedule);
  if(hourly)return `每小时 · xx:${hourly[1].padStart(2,'0')}`;
  const m=/^(\d+) (\d+) \* \* \*$/.exec(job.schedule);
  return m?`每天 ${m[2].padStart(2,'0')}:${m[1].padStart(2,'0')}`:`Cron · ${job.schedule}`;
 }
 function tabsHTML(){return `<div class="cron-page-tabs" role="tablist" aria-label="定时任务页面"><button type="button" role="tab" aria-selected="${view==='tasks'}" data-cron-view="tasks">任务列表</button><button type="button" role="tab" aria-selected="${view==='records'}" data-cron-view="records">运行记录</button></div>`;}
 function bindTabs(){root.querySelectorAll('[data-cron-view]').forEach(b=>b.onclick=()=>{view=b.dataset.cronView;filterOpen=false;search='';recordOffset=0;generation++;pending=false;draw();refresh();});}
 function draw(){
  if(!root?.isConnected)return;
  if(view==='records')return drawRecords();
  const jobs=snapshot.jobs.filter(j=>(j.name+' '+(j.description||'')+' '+j.prompt).toLowerCase().includes(search.toLowerCase()));
  root.innerHTML=`<section class="panel cron-panel"><div class="cron-heading"><div class="cron-heading-title"><span class="cron-heading-icon">${glyph()}</span><div><h2>定时任务</h2>${snapshot.online?'':'<p>调度服务未运行，计划任务暂不触发</p>'}</div></div><button class="button primary" data-cron="create">${icon('plus')}新建任务</button></div>${tabsHTML()}<div class="cron-toolbar"><label class="search">${icon('search')}<input data-cron-search placeholder="搜索任务…" aria-label="搜索任务" value="${esc(search)}"></label></div><div class="cron-task-table"><table><thead><tr><th>任务名称</th><th>执行计划</th><th>工作目录</th><th>下次运行</th><th>供应商</th><th>模型</th><th class="cron-task-toggle">启用</th><th class="cron-task-operation">操作</th></tr></thead><tbody>${jobs.map(j=>`<tr class="cron-task-row ${j.enabled?'':'is-paused'}"><td><div class="cron-task-identity"><span class="cron-task-mark">${glyph()}</span><div><h3 title="${esc(j.name)}">${esc(j.name)}</h3></div></div>${j.pause_reason?`<div class="cron-task-warning" title="${esc(j.pause_reason)}">${esc(j.pause_reason)}</div>`:''}</td><td><div class="cron-task-plan">${esc(j.normalized.kind==='once'?'仅一次':cadence(j))}</div></td><td><div class="cron-task-workdir" title="${esc(j.workdir||'~/.corki/main/default')}">${esc(j.workdir||'~/.corki/main/default')}</div></td><td>${active(j)?`<span class="cron-task-live ${j.last_run.status==='running'?'running':''}"><i></i>${j.last_run.status==='running'?'运行中':'准备执行'}</span>`:`<div class="cron-task-time">${j.enabled?stamp(j.next_run):j.normalized.kind==='once'&&!j.next_run?'已结束':'已暂停'}</div>`}</td><td><div class="cron-task-provider" title="${esc(j.provider||state.default.provider)}">${esc(j.provider||state.default.provider||'—')}</div></td><td><div class="cron-task-model-name" title="${esc(j.model||state.default.model)}">${esc(j.model||state.default.model||'—')}</div></td><td class="cron-task-toggle"><button class="toggle ${j.enabled?'on':''}" role="switch" aria-checked="${j.enabled}" aria-label="${j.enabled?'暂停':'开启'} ${esc(j.name)}" data-cron="${j.enabled?'pause':'resume'}" data-id="${j.id}"></button></td><td><div class="cron-task-buttons"><button class="text-button" data-cron="${active(j)?'stop':'run'}" data-id="${j.id}">${active(j)?'停止':'执行一次'}</button><button class="text-button cron-task-secondary" data-cron="history" data-id="${j.id}">执行记录</button><button class="text-button cron-task-secondary" data-cron="edit" data-id="${j.id}" title="编辑任务" aria-label="编辑 ${esc(j.name)}" ${active(j)?'disabled':''}>编辑</button><button class="text-button danger" data-cron="delete" data-id="${j.id}" title="删除任务" aria-label="删除 ${esc(j.name)}" ${active(j)?'disabled':''}>删除</button></div></td></tr>`).join('')}</tbody></table></div>${jobs.length?'':`<div class="cron-empty"><span>${glyph()}</span><h3>${search?'没有匹配的任务':'把日常工作交给 Corki'}</h3><p>${search?'试试其他关键词':'创建任务，按计划生成报告、整理资料或执行工作。'}</p></div>`}</section>`;
  bindTabs();
  root.querySelector('[data-cron-search]').oninput=e=>{search=e.target.value;const p=e.target.selectionStart;draw();const input=root.querySelector('[data-cron-search]');input.focus();input.setSelectionRange(p,p);};
  root.onclick=async e=>{
   const b=e.target.closest('[data-cron]');if(!b||b.disabled)return;
   const job=snapshot.jobs.find(j=>j.id===b.dataset.id),action=b.dataset.cron;
   if(action==='create'||action==='edit')return edit(job);
   if(action==='history'){view='records';recordNames=new Set([job.name]);recordOffset=0;filterQuery='';filterOpen=false;generation++;pending=false;records={...records,records:[],total:0};draw();refresh();return;}
   if(action==='delete'){showDialog('删除任务',`<p class="cron-confirm">删除「${esc(job.name)}」及其执行记录？</p>`,async()=>{await change(job,'delete');},'');$('#save').textContent='删除';return;}
   b.disabled=true;try{await change(job,action);}catch(error){toast(error.message);b.disabled=false;}
  };
 }
 async function refresh(){
  if(pending||!root?.isConnected)return;
  const epoch=generation;pending=true;
  try{const [next,history]=await Promise.all([api('cron'),view==='records'?api('cron?records=1&offset='+recordOffset+[...recordNames].map(name=>'&name='+encodeURIComponent(name)).join('')):Promise.resolve(null)]);if(epoch!==generation)return;const changed=JSON.stringify(next)!==JSON.stringify(snapshot)||(history&&JSON.stringify(history)!==JSON.stringify(records));snapshot=next;if(history)records=history;if(changed||!root.children.length)draw();}
  catch(error){if(epoch===generation)toast(error.message);}
  finally{pending=false;}
 }
 function filterNames(){return [...new Set([...(records.task_names||[]),...snapshot.jobs.map(j=>j.name),...recordNames])].sort((a,b)=>a.localeCompare(b,'zh-CN'));}
 function recordFilterHTML(){
  const selected=[...recordNames];
  return `<div class="cron-record-toolbar"><details class="cron-record-filter" ${filterOpen?'open':''}><summary><span class="cron-filter-label">任务名称</span><span class="cron-filter-selection">${esc(selected.length?selected.join('、'):'全部任务')}</span><span class="cron-filter-chevron">⌄</span></summary><div class="cron-filter-menu"><label class="search">${icon('search')}<input id="cron-filter-search" placeholder="搜索任务名称…" aria-label="搜索任务名称" value="${esc(filterQuery)}"></label><div id="cron-filter-options" role="group" aria-label="选择任务名称"></div></div></details>${selected.length?'<button type="button" class="text-button" id="cron-filter-clear">清除筛选</button>':''}</div>`;
 }
 function bindRecordFilter(){
  const dropdown=root.querySelector('.cron-record-filter');
  dropdown.ontoggle=()=>{if(dropdown.isConnected)filterOpen=dropdown.open;};
  const input=root.querySelector('#cron-filter-search'),options=root.querySelector('#cron-filter-options');
  const choices=()=>{
   const names=filterNames().filter(name=>name.toLowerCase().includes(filterQuery.toLowerCase()));
   options.innerHTML=names.length?names.map(name=>`<button type="button" role="checkbox" aria-checked="${recordNames.has(name)}" data-record-name="${esc(name)}"><span class="cron-filter-check">${recordNames.has(name)?icon('check'):''}</span><span>${esc(name)}</span></button>`).join(''):'<div class="cron-filter-empty">没有匹配的任务</div>';
  };
  choices();input.oninput=()=>{filterQuery=input.value;choices();};
  const apply=()=>{recordOffset=0;generation++;pending=false;records={...records,records:[],total:0};draw();refresh();};
  options.onclick=e=>{const option=e.target.closest('[data-record-name]');if(!option)return;const name=option.dataset.recordName;recordNames.has(name)?recordNames.delete(name):recordNames.add(name);filterOpen=true;apply();};
  root.querySelector('#cron-filter-clear')?.addEventListener('click',()=>{recordNames.clear();filterQuery='';filterOpen=false;apply();});
  dropdown.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();filterOpen=false;dropdown.open=false;dropdown.querySelector('summary').focus();}};
 }
 function drawRecords(){
  root.innerHTML=`<section class="panel cron-panel"><div class="cron-heading"><div class="cron-heading-title"><span class="cron-heading-icon">${glyph()}</span><div><h2>定时任务</h2></div></div></div>${tabsHTML()}${recordFilterHTML()}<div class="cron-record-table"><table><thead><tr><th>任务名称</th><th>运行时间</th><th>结束时间</th><th>状态</th><th class="cron-record-action">操作</th></tr></thead><tbody>${records.records.map(r=>`<tr><td><div class="cron-record-name">${esc(r.name)}</div></td><td><time>${stamp(r.started||r.created)}</time></td><td><time>${['claimed','running'].includes(r.status)||!r.finished?'--':stamp(r.finished)}</time></td><td><span class="cron-record-status"><span class="cron-dot ${esc(r.status)}"></span>${recordStatus(r)}</span></td><td class="cron-record-action"><button type="button" class="text-button" data-cron-detail="${r.id}">查看详情</button></td></tr>`).join('')}</tbody></table>${records.records.length?'':'<div class="cron-empty"><p>暂无运行记录</p></div>'}</div><div class="cron-record-pagination"><span>共 ${records.total} 条记录</span><button type="button" class="button" data-record-page="prev" ${recordOffset===0?'disabled':''}>上一页</button><span>${Math.floor(recordOffset/records.limit)+1} / ${Math.max(1,Math.ceil(records.total/records.limit))}</span><button type="button" class="button" data-record-page="next" ${recordOffset+records.limit>=records.total?'disabled':''}>下一页</button></div></section>`;
  bindTabs();bindRecordFilter();root.onclick=e=>{
   if(!e.target.closest('.cron-record-filter')){filterOpen=false;root.querySelector('.cron-record-filter').open=false;}
   const detail=e.target.closest('[data-cron-detail]');if(detail)return details(detail.dataset.cronDetail);
   const page=e.target.closest('[data-record-page]');if(!page||page.disabled)return;
   recordOffset=Math.max(0,recordOffset+(page.dataset.recordPage==='next'?records.limit:-records.limit));
   generation++;pending=false;refresh();
  };
 }
 async function details(identity){
  const epoch=generation;
  try{
   const r=await api('cron?run='+encodeURIComponent(identity));if(!root?.isConnected||epoch!==generation)return;
   const failed=r.status==='failed';
   const content=failed?(r.error_log||r.error||'未记录详细异常日志'):(r.output||(['claimed','running'].includes(r.status)?'任务正在执行，可稍后重新打开查看结果。':'暂无文本输出'));
   const duration=r.started&&r.finished?`${Math.max(0,Math.round(r.finished-r.started))} 秒`:'—';
   showDialog('运行详情',`<div class="cron-detail-title">${esc(r.definition.name)}</div><div class="cron-detail-meta"><div><span>状态</span><strong>${labels[r.status]||esc(r.status)}</strong></div><div><span>触发方式</span><strong>${r.source==='manual'?'手动执行':'计划执行'}</strong></div><div><span>开始时间</span><strong>${stamp(r.started||r.created)}</strong></div><div><span>结束时间</span><strong>${stamp(r.finished)}</strong></div><div><span>运行耗时</span><strong>${duration}</strong></div><div><span>运行模型</span><strong>${esc(r.definition.model||'默认模型')}${r.definition.provider?` · ${esc(r.definition.provider)}`:''}</strong></div></div><details class="cron-detail-prompt"><summary>任务内容</summary><pre>${esc(r.definition.prompt)}</pre></details><div class="cron-result-head"><strong>${failed?'异常日志':'执行结果'}</strong><button type="button" class="text-button" id="cron-detail-copy">${icon('copy')}复制</button></div>${r.error&&!failed?`<div class="cron-alert">${esc(r.error)}</div>`:''}<pre class="cron-output">${esc(content)}</pre>`,null,'');
   $('#cancel-dialog').textContent='关闭';
   $('#cron-detail-copy').onclick=async()=>{try{await navigator.clipboard.writeText(content);toast('已复制');}catch{toast('复制失败，请手动选择复制');}};
  }catch(error){toast(error.message);}
 }
 async function change(job,action){
  const result=await api('mutate',{kind:'cron',action,id:job?.id,version:job?.version});toast(result.message);await refresh();
 }
 function picker(name,label,options,value){return `<div class="field protocol-field cron-select"><span>${label}</span><input type="hidden" name="${name}" value="${esc(value)}"><button type="button" class="select-trigger" aria-haspopup="listbox" aria-expanded="false"><span class="select-current">${esc(options.find(o=>o[0]===value)?.[1]||value)}</span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m6 8 4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5"/></svg></button><div class="select-menu" role="listbox" hidden>${options.map(([id,label])=>`<button type="button" role="option" aria-selected="${id===value}" data-value="${id}"><strong>${label}</strong></button>`).join('')}</div></div>`;}
 async function edit(job){
  const epoch=generation;
  let catalog;
  try{catalog=await api('snapshot');}catch(error){toast(error.message);return;}
  if(!root?.isConnected||epoch!==generation)return;
  const providers=[...catalog.providers];
  if(job?.provider&&!providers.some(p=>p.id===job.provider))providers.push({id:job.provider,models:{[job.model]:{}}});
  if(!providers.length){toast('请先添加模型供应商和模型');return;}
  const provider=job?.provider||catalog.default.provider||providers[0].id;
  const available=Object.keys(providers.find(p=>p.id===provider)?.models||{});
  const model=job?.model||(provider===catalog.default.provider&&available.includes(catalog.default.model)?catalog.default.model:available[0]||'');
  let mode=job?'cron':'daily';
  if(job?.normalized.kind==='once')mode='once';
  if(job&&/^\d+ \* \* \* \*$/.test(job.schedule))mode='hourly';
  if(job&&/^\d+ \d+ \* \* \*$/.test(job.schedule))mode='daily';
  const zone=job?.timezone||Intl.DateTimeFormat().resolvedOptions().timeZone||'Asia/Shanghai';
  showDialog(job?'编辑任务':'新建任务',`<div class="cron-form">${field('name','任务名称',job?.name||'','text','',false,'例如：每日工作摘要')}${field('description','任务描述',job?.description||'','text','',false,'可选')}<label class="field"><span>任务内容</span><textarea name="prompt" rows="4" maxlength="32000" required placeholder="描述需要 Corki 完成的工作…">${esc(job?.prompt||'')}</textarea></label><div class="field-grid">${picker('provider','模型供应商',providers.map(p=>[p.id,p.id]),provider)}<div id="cron-model-field"></div></div><div class="field-grid">${picker('mode','执行方式',[['daily','每天'],['hourly','每小时'],['once','仅一次'],['cron','Cron 表达式']],mode)}<div id="cron-timing"></div></div>${field('workdir','工作目录',job?.workdir||'','text','',false,'默认 ~/.corki/main/default')}</div>`,async form=>{
   if(!form.get('provider')||!form.get('model'))throw Error('请选择模型供应商和运行模型');
   const kind=form.get('mode');let schedule=form.get('timing');
   if(kind==='daily'){const [h,m]=schedule.split(':').map(Number);schedule=`${m} ${h} * * *`;}
   if(kind==='hourly'){if(!/^[0-5]?[0-9]$/.test(schedule))throw Error('分钟须为 00–59');schedule=`${Number(schedule)} * * * *`;}
   const scheduleKind=['daily','hourly'].includes(kind)?'cron':kind==='cron'&&job?.normalized.kind==='interval'&&schedule===job.schedule?'interval':kind;
   await api('mutate',{kind:'cron',action:'save',id:job?.id,version:job?.version,values:{name:form.get('name'),provider:form.get('provider'),model:form.get('model'),description:form.get('description'),prompt:form.get('prompt'),schedule,schedule_kind:scheduleKind,timezone:zone,workdir:form.get('workdir'),enabled:job?.enabled??true}});
   toast('任务已保存');await refresh();
  },'');
  $('#save').textContent=job?'保存':'创建任务';
  const timing=()=>{
   const m=$('[name=mode]').value;let value='';
   if(m==='daily'){const bits=job?.schedule.split(' ');value=mode==='daily'&&bits?.length===5?bits[1].padStart(2,'0')+':'+bits[0].padStart(2,'0'):'09:00';}
   if(m==='hourly'){
    value=mode==='hourly'?job.schedule.split(' ')[0].padStart(2,'0'):'00';
    $('#cron-timing').innerHTML=`<label class="field"><span>执行时间</span><span class="cron-hourly-time"><span aria-hidden="true">xx:</span><input name="timing" type="text" inputmode="numeric" pattern="[0-5]?[0-9]" maxlength="2" placeholder="00 ~ 59" value="${esc(value)}" aria-label="每小时执行分钟" required autocomplete="off"></span></label>`;
    const minute=$('[name=timing]');
    minute.onblur=()=>{if(/^[0-5]?[0-9]$/.test(minute.value))minute.value=minute.value.padStart(2,'0');};
    minute.onkeydown=e=>{if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();minute.value=String((Number(minute.value||0)+(e.key==='ArrowUp'?1:59))%60).padStart(2,'0');}};
    return;
   }
   if(m==='once')value=mode==='once'?job.schedule:'';
   if(m==='cron')value=mode==='cron'?job?.schedule||'0 9 * * 1-5':'0 9 * * 1-5';
   $('#cron-timing').innerHTML=field('timing',m==='daily'||m==='once'?'执行时间':'表达式',value,m==='daily'?'time':'text','',false,m==='once'?'日期时间 / 秒或毫秒时间戳':'0 9 * * 1-5');
   $('[name=timing]').required=true;
  };
  const modelPicker=(selected)=>{
   const names=Object.keys(providers.find(p=>p.id===$('[name=provider]').value)?.models||{});
   const options=names.map(name=>[name,name]);
   if(selected&&!names.includes(selected))options.unshift([selected,selected+'（已移除）']);
   if(!options.length)options.push(['','暂无模型']);
   $('#cron-model-field').innerHTML=picker('model','运行模型',options,selected??names[0]??'');
   bindCustomSelect($('#cron-model-field .cron-select'));
  };
  modelPicker(model);
  bindCustomSelect($('[name=provider]').closest('.cron-select'));
  $('[name=provider]').onchange=()=>modelPicker();
  bindCustomSelect($('[name=mode]').closest('.cron-select'));$('[name=mode]').onchange=timing;timing();$('[name=name]').required=true;$('[name=description]').maxLength=500;
 }
 return {mount(node){root=node;generation++;snapshot={jobs:[]};pending=false;refresh();timer=setInterval(()=>{if(!document.hidden&&!$('#editor').open&&!filterOpen)refresh();},3000);},unmount(){generation++;filterOpen=false;clearInterval(timer);root=null;}};
})();
