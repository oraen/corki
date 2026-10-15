import { basicSetup } from 'codemirror';
import { Compartment, EditorState } from '@codemirror/state';
import { EditorView, keymap, placeholder } from '@codemirror/view';
import { foldService } from '@codemirror/language';
import {jsonLanguage as language, jsonHighlighting as highlighting} from './json-highlighting.js';
import { openSearchPanel } from '@codemirror/search';
import { setDiagnostics } from '@codemirror/lint';
import { MergeView, unifiedMergeView, getChunks, getOriginalDoc } from '@codemirror/merge';
import { applyEdits, format, parse, parseTree } from 'jsonc-parser';
import { manualDetail, manualFields, manualMarkup } from './config-manual.js';

const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = window.CorkiDateTime;
const foldCache = new WeakMap();
const jsonFolding = foldService.of((state, start) => {
  let ranges = foldCache.get(state.doc);
  if (!ranges) {
    ranges = new Map();
    const root = parseTree(state.doc.toString(), [], {allowTrailingComma:true});
    const stack = root ? [root] : [];
    while (stack.length) {
      const node = stack.pop();
      if (node.type === 'object' || node.type === 'array') {
        const line = state.doc.lineAt(node.offset);
        const end = node.offset + node.length - 1;
        if (end > line.to && !ranges.has(line.from)) ranges.set(line.from, {from:node.offset+1, to:end});
      }
      if (node.children) stack.push(...node.children);
    }
    foldCache.set(state.doc, ranges);
  }
  return ranges.get(start) || null;
});
const phrases = EditorState.phrases.of({
  'Find':'查找', 'Replace':'替换', 'next':'下一个', 'previous':'上一个', 'all':'全部',
  'match case':'区分大小写', 'regexp':'正则表达式', 'by word':'整词匹配',
  'replace':'替换', 'replace all':'全部替换', 'close':'关闭',
  'Fold line':'折叠行', 'Unfold line':'展开行', 'to':'至',
});
const theme = EditorView.theme({
  '&': {fontSize: '13px', backgroundColor:'#fff', color:'#28344b'},
  '.cm-scroller': {fontFamily:'ui-monospace, SFMono-Regular, Menlo, monospace', lineHeight:'1.8'},
  '.cm-content': {padding:'16px 0'},
  '.cm-gutters': {backgroundColor:'#fbfcfe', color:'#a0a9ba', borderRight:'1px solid #eef0f5'},
  '.cm-lineNumbers .cm-gutterElement': {minWidth:'40px', padding:'0 12px 0 6px'},
  '.cm-activeLine': {backgroundColor:'#fff8f2'},
  '.cm-activeLineGutter': {backgroundColor:'#fff0e4'},
  '&.cm-focused': {outline:'none'},
  '.cm-searchMatch': {backgroundColor:'#ffdf8090'},
  '.cm-panels': {backgroundColor:'#f5f7fc', color:'#344054'},
});
const reasonLabel = reason => ({
  '当前配置 / 外部更新':'配置快照', '修改前配置':'初始版本', '编辑配置文件':'配置编辑',
  '回滚历史版本':'版本回滚', '测试恢复原配置':'测试恢复',
}[reason] || reason.replace(/^(providers|models|skills|tools): (save|delete|default|toggle)$/, (_, kind, action) => `${({providers:'供应商',models:'模型',skills:'技能',tools:'工具'})[kind]}${({save:'更新',delete:'删除',default:'切换',toggle:'启停'})[action]}`));
const btn = (label, action, extra='') => `<button type="button" class="button" data-config-action="${action}" ${extra}>${label}</button>`;

class ConfigWorkspace {
  constructor() {
    this.tab='current'; this.doc=null; this.draft=''; this.editing=false; this.busy=false;
    this.manualSelected=manualFields[0].path;
    this.viewingId=null; this.viewContent=null; this.left=''; this.collapse=true;
    this.layout=window.innerWidth<=600?'unified':'split';
    this.request=0; this.validation=0; this.status=''; this.statusKind=''; this.chunk=0;
    this.editable=new Compartment();
    window.addEventListener('beforeunload', event => {
      if(this.dirty){event.preventDefault();event.returnValue='';}
    });
  }
  get dirty(){return !!this.doc && this.draft!==this.doc.content;}
  mount(host, services){
    this.disposeViews(); this.host=host; this.services=services;
    this.render();
    this.reloadLatest().catch(error=>this.error(error));
  }
  unmount(){this.disposeViews();this.host=null;this.request++;this.refreshRequest=(this.refreshRequest||0)+1;this.loading=false;}
  async reloadLatest(){
    if(this.busy)return;
    if(this.dirty){this.confirm('重新载入配置？','未保存的修改将被丢弃。',()=>this.refresh());return;}
    await this.refresh();
  }
  disposeViews(){
    this.versionRequest=(this.versionRequest||0)+1;
    if(this.editor){this.savedState=this.editor.state;this.editor.destroy();this.editor=null;}
    if(this.merge){this.merge.destroy();this.merge=null;}
    if(this.unified){this.unified.destroy();this.unified=null;}
  }
  error(error){this.services.toast(error.message);this.status=error.message;this.statusKind='error';this.updateStatus();if(!this.doc&&this.host?.isConnected){const empty=this.host.querySelector('.config-loading');if(empty)empty.textContent=error.message;}}
  async refresh(){
    const request=this.refreshRequest=(this.refreshRequest||0)+1;
    this.loading=true;this.setEditable();this.updateStatus();
    try {
      const doc=await this.services.api('config');
      if(request!==this.refreshRequest)return;
      this.doc=doc;this.draft=doc.content;this.savedState=null;this.editing=false;
      this.status='已保存';this.statusKind='';this.viewingId=null;this.viewContent=null;
      if(!this.left||!doc.versions.some(v=>v.id===this.left))this.left=doc.versions[1]?.id||doc.versions[0]?.id||'current';
    } catch(error){if(request===this.refreshRequest)throw error;} finally {if(request===this.refreshRequest){this.loading=false;this.setEditable();this.updateStatus();}}
    if(request!==this.refreshRequest)return;
    // Destroy old views before resetting their cached state.
    this.disposeViews();this.savedState=null;this.render();
  }
  render(){
    if(!this.host?.isConnected)return;
    this.disposeViews();
    if(this.resetEditorState){this.savedState=null;this.resetEditorState=false;}
    const count=this.doc?.versions.length||0;
    this.host.innerHTML=`<div class="config-tabbar" role="tablist" aria-label="配置视图"><button role="tab" aria-selected="${this.tab==='current'}" data-config-action="current">当前配置</button><button role="tab" aria-selected="${this.tab==='compare'}" data-config-action="compare" ${count<2?'disabled':''}>版本对比 <span>${count}</span></button><div class="config-top-actions">${btn('配置手册','manual',`aria-pressed="${this.tab==='manual'}"`)}${btn('刷新','refresh')}${btn('编辑配置','edit','id="config-edit"')}${btn('取消编辑','cancel','id="config-cancel"')}${btn('保存更改','save','id="config-save"')}</div></div>
      ${this.tab==='current'?'':`<div class="config-subview-bar"><button type="button" class="config-view-back" data-config-action="back-to-current"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14.5 18-6-6 6-6"/></svg><span>返回配置</span></button></div>`}
      ${this.tab==='manual'?manualMarkup(this.manualSelected):!this.doc?'<div class="panel config-loading">正在读取配置…</div>':this.tab==='current'?this.currentMarkup():this.compareMarkup()}
      <dialog id="config-confirm"><div class="dialog-head"><h2 id="config-confirm-title"></h2><button class="icon-button" data-config-action="dismiss" aria-label="关闭">✕</button></div><div id="config-confirm-body"></div><div class="dialog-foot"><button class="button" data-config-action="dismiss">取消</button><button class="button primary" data-config-action="confirm">确认</button></div></dialog>
      `;
    this.host.onclick=event=>{const field=event.target.closest('[data-manual-field]');if(field){this.manualSelected=field.dataset.manualField;this.host.querySelectorAll('[data-manual-field]').forEach(button=>{button.classList.toggle('selected',button===field);button.setAttribute('aria-current',String(button===field));});this.host.querySelector('.manual-detail').innerHTML=manualDetail(this.manualSelected);return;}const target=event.target.closest('[data-config-action]');if(target)this.action(target).catch(error=>this.error(error));};
    this.host.onchange=event=>this.change(event).catch(error=>this.error(error));
    if(this.doc){if(this.tab==='current')this.createEditor();else if(this.tab==='compare')this.createDiff();}
    this.updateStatus();
  }
  currentMarkup(){
    const historical=this.viewingId!==null;
    const version=this.doc.versions.find(v=>v.id===this.viewingId);
    return `<div class="config-workbench"><section class="panel config-editor-panel"><div class="config-editor-toolbar"><span class="config-file-icon">{ }</span><strong>${historical?'历史版本':'settings.json'}</strong><span class="config-mode" id="config-mode"></span><div class="config-editor-tools">${btn('查找','search','title="⌘ / Ctrl F"')}${btn('格式化','format','id="config-format" title="格式化 JSONC，保留注释"')}${btn('校验','validate')}</div></div><div id="json-editor" aria-label="JSON 配置编辑器"></div><div class="config-statusbar"><span id="config-status" role="status"></span><span id="config-cursor"></span><span>JSONC · UTF-8</span></div></section><section class="panel config-history"><div class="history-heading"><h2>版本历史 <span>${this.doc.versions.length}</span></h2></div><div class="version-timeline">${this.doc.versions.map((v,i)=>`<article class="version-item ${(historical?this.viewingId===v.id:i===0)?'selected':''}"><button type="button" class="version-open" data-config-action="select-version" data-id="${escape(v.id)}" aria-current="${(historical?this.viewingId===v.id:i===0)?'true':'false'}"><strong>${escape(reasonLabel(v.reason))}${i===0?'（当前）':''}</strong><small>${date(v.created)}</small></button><div class="version-item-bottom"><code>${v.revision.slice(0,8)}</code>${i===0?'':`<button class="text-button" data-config-action="compare-current" data-id="${escape(v.id)}">对比当前</button><button class="text-button" data-config-action="restore" data-id="${escape(v.id)}">回滚</button>`}</div></article>`).join('')}</div></section></div>`;
  }
  compareMarkup(){
    const version=this.doc.versions.find(v=>v.id===this.left);
    return `<section class="panel config-diff-panel"><div class="diff-fixed-pair"><span>历史版本 · ${version?`${date(version.created)} · ${escape(reasonLabel(version.reason))}`:'未找到'}</span><span>当前配置</span></div><div class="diff-toolbar"><div id="diff-summary" role="status">正在计算差异…</div><div class="diff-tools"><div class="diff-layout-switch">${btn('并排','split',`aria-pressed="${this.layout==='split'}"`)}${btn('行内','unified',`aria-pressed="${this.layout==='unified'}"`)}</div>${btn('↑','previous','aria-label="上一个差异"')}${btn('↓','next','aria-label="下一个差异"')}<label><input id="diff-collapse" type="checkbox" ${this.collapse?'checked':''}> 折叠相同内容</label></div></div><div class="diff-side-labels ${this.layout==='unified'?'is-unified':''}"><span>历史版本 <i class="diff-removed">删除</i></span><span>当前配置 <i class="diff-added">新增</i></span></div><div id="json-diff" class="${this.layout==='unified'?'is-unified':''}"></div><div class="config-statusbar"><span>只读对比</span><span>JSONC · UTF-8</span></div></section>`;
  }
  extensions(readOnly, label){return [basicSetup,language,jsonFolding,highlighting,theme,phrases,EditorState.readOnly.of(readOnly),EditorView.editable.of(!readOnly),EditorView.contentAttributes.of({'aria-label':label,tabindex:'0'})];}
  createEditor(){
    const parent=this.host.querySelector('#json-editor');
    const config=[basicSetup,language,jsonFolding,highlighting,theme,phrases,
      this.editable.of([EditorState.readOnly.of(!this.editing),EditorView.editable.of(this.editing)]),
      EditorView.contentAttributes.of({'aria-label':this.viewingId?'历史配置内容':'settings.json 配置内容',spellcheck:'false',tabindex:'0'}),
      keymap.of([{key:'Mod-s',run:()=>{this.save().catch(e=>this.error(e));return true;}}]),
      EditorView.updateListener.of(update=>{
        if(update.docChanged){this.draft=update.state.doc.toString();this.status=this.dirty?'未保存':'已保存';this.statusKind='';this.validation++;clearTimeout(this.timer);this.timer=setTimeout(()=>this.validate(),650);}
        if(update.docChanged||update.selectionSet)this.updateStatus();
      })];
    this.editor=new EditorView({parent,state:this.viewingId?EditorState.create({doc:this.viewContent,extensions:config}):this.savedState||EditorState.create({doc:this.draft,extensions:config})});
    this.setEditable();
  }
  setEditable(){if(this.editor)this.editor.dispatch({effects:this.editable.reconfigure([EditorState.readOnly.of(!this.editing||!!this.viewingId||this.busy||this.loading),EditorView.editable.of(this.editing&&!this.viewingId&&!this.busy&&!this.loading)])});}
  updateStatus(){
    if(!this.host?.isConnected)return;
    const q=s=>this.host.querySelector(s);
    const edit=q('#config-edit'), cancel=q('#config-cancel'), save=q('#config-save');
    if(edit){edit.hidden=this.editing||this.tab==='manual';edit.disabled=this.busy||this.loading||!this.doc||!!this.viewingId;}
    if(cancel){cancel.hidden=!this.editing||this.tab==='manual';cancel.disabled=this.busy||this.loading;}
    if(save){save.hidden=!this.editing||this.tab==='manual';save.disabled=!this.dirty||!!this.viewingId||this.busy||this.loading;save.textContent=this.busy?'正在保存…':'保存更改';save.classList.add('primary');}
    if(q('#config-format'))q('#config-format').disabled=!this.editing||!!this.viewingId||this.busy||this.loading;
    if(q('[data-config-action="validate"]'))q('[data-config-action="validate"]').disabled=!!this.viewingId||this.busy||this.loading;
    if(q('#config-mode'))q('#config-mode').textContent=this.viewingId?'历史 · 只读':this.editing?'编辑中':'只读';
    if(q('#config-status')){q('#config-status').textContent=this.viewingId?'历史版本 · '+date(this.doc.versions.find(v=>v.id===this.viewingId)?.created):this.status||'已保存';q('#config-status').dataset.kind=this.statusKind;}
    if(q('#config-cursor')&&this.editor){const pos=this.editor.state.selection.main.head,line=this.editor.state.doc.lineAt(pos);q('#config-cursor').textContent=`行 ${line.number}, 列 ${pos-line.from+1} · ${this.editor.state.doc.lines} 行`;}
    this.host.querySelectorAll('[data-config-action="refresh"], [data-config-action="restore"]').forEach(el=>el.disabled=this.busy||this.loading);
  }
  async validate(){
    if(!this.doc)return false;
    const draft=this.draft, sequence=++this.validation;
    this.status='正在校验…';this.statusKind='';this.updateStatus();
    let diagnostics=[];
    try{
      await this.services.api('mutate',{kind:'config',action:'validate',content:draft});
      if(sequence!==this.validation||draft!==this.draft)return false;
      this.status=this.dirty?'校验通过 · 未保存':'校验通过';this.statusKind='success';
      return true;
    }catch(error){
      if(sequence!==this.validation||draft!==this.draft)return false;
      const match=error.message.match(/line (\d+), column (\d+)/);
      this.status=match?`JSON 格式错误 · 第 ${match[1]} 行，第 ${match[2]} 列`:error.message;this.statusKind='error';
      if(match&&this.editor){const line=this.editor.state.doc.line(Math.min(Number(match[1]),this.editor.state.doc.lines));const from=Math.min(line.to,line.from+Number(match[2])-1);diagnostics=[{from,to:Math.min(from+1,this.editor.state.doc.length),severity:'error',message:this.status}];}
      return false;
    }finally{
      if(sequence===this.validation&&draft===this.draft){if(this.editor)this.editor.dispatch(setDiagnostics(this.editor.state,diagnostics));this.updateStatus();}
    }
  }
  async save(){
    if(!this.editing||!this.dirty||this.busy)return;
    this.busy=true;this.setEditable();this.updateStatus();clearTimeout(this.timer);
    try{
      if(!await this.validate())return;
      await this.services.api('mutate',{kind:'config',action:'save',content:this.draft,revision:this.doc.revision});
      await this.refresh();await this.services.reloadSnapshot();this.services.toast('配置已保存');
    }finally{this.busy=false;this.setEditable();this.updateStatus();}
  }
  async viewVersion(id){
    if(this.busy||this.loading||!this.doc?.versions.some(v=>v.id===id))return;
    if(this.dirty){this.confirm('切换版本？','未保存的修改将被丢弃。',async()=>{this.draft=this.doc.content;this.editing=false;await this.viewVersion(id);});return;}
    const sequence=this.versionRequest=(this.versionRequest||0)+1;
    try{
      const current=id===this.doc.versions[0]?.id;
      const data=current?null:await this.services.api('config?id='+encodeURIComponent(id));
      if(sequence!==this.versionRequest||!this.host?.isConnected)return;
      this.viewingId=current?null:id;
      this.viewContent=data?.content||null;
      this.editing=false;this.resetEditorState=true;this.tab='current';this.render();
    }catch(error){if(sequence===this.versionRequest)throw error;}
  }
  confirm(title, body, callback){
    const dialog=this.host.querySelector('#config-confirm');
    dialog.querySelector('#config-confirm-title').textContent=title;
    dialog.querySelector('#config-confirm-body').textContent=body;
    this.confirmAction=callback;dialog.showModal();
  }
  async action(target){
    const action=target.dataset.configAction;
    if(action==='manual'){if(this.busy)return;if(this.tab==='manual'){if(this.viewingId)await this.viewVersion(this.doc.versions[0]?.id);else{this.tab='current';this.render();}}else{this.tab='manual';this.render();}return;}
    if(action==='select-version'){await this.viewVersion(target.dataset.id);return;}
    if(action==='dismiss'){this.host.querySelector('#config-confirm').close();return;}
    if(action==='confirm'){target.disabled=true;try{await this.confirmAction();this.host.querySelector('#config-confirm')?.close();}finally{target.disabled=false;}return;}
    if(this.busy||this.loading)return;
    if(action==='edit'&&!this.viewingId){this.editing=true;this.tab='current';this.render();this.editor?.focus();}
    if(action==='cancel'){
      const cancel=()=>{this.disposeViews();this.draft=this.doc.content;this.savedState=null;this.editing=false;this.status='已保存';this.statusKind='';this.validation++;clearTimeout(this.timer);this.render();};
      if(this.dirty)this.confirm('放弃更改？','未保存的修改将被丢弃。',cancel);else cancel();
    }
    if(action==='refresh')await this.reloadLatest();
    if(action==='save')await this.save();
    if(action==='search'&&this.editor){openSearchPanel(this.editor);this.editor.focus();}
    if(action==='validate')await this.validate();
    if(action==='format'&&this.editing&&this.editor){
      const errors=[];parse(this.draft,errors,{allowTrailingComma:true});
      if(errors.length){await this.validate();return;}
      const text=applyEdits(this.draft,format(this.draft,undefined,{tabSize:2,insertSpaces:true,eol:'\n'}));
      if(text!==this.draft)this.editor.dispatch({changes:{from:0,to:this.editor.state.doc.length,insert:text}});
      this.editor.focus();
    }
    if(action==='current'||action==='back-to-current'){
      if(this.viewingId)await this.viewVersion(this.doc.versions[0]?.id);
      else{this.tab='current';this.render();}
    }
    if(action==='compare'&&this.doc.versions.length>1){this.left=this.viewingId||this.left;this.tab='compare';this.render();}
    if(action==='compare-current'){this.left=target.dataset.id;this.tab='compare';this.render();}
    if(action==='split'||action==='unified'){this.layout=action;this.render();}
    if(action==='next'||action==='previous')this.jump(action==='next'?1:-1);
    if(action==='restore'){
      const id=target.dataset.id, version=this.doc.versions.find(v=>v.id===id), revision=this.doc.revision;
      this.confirm('回滚此版本？',`${date(version.created)} · ${reasonLabel(version.reason)}。${this.dirty?'未保存的修改将被替换。':''}回滚后保留当前版本。`,async()=>{
        this.busy=true;this.setEditable();this.updateStatus();
        try{await this.services.api('mutate',{kind:'config',action:'restore',id,revision});await this.refresh();await this.services.reloadSnapshot();this.services.toast('配置已回滚');}
        finally{this.busy=false;this.setEditable();this.updateStatus();}
      });
    }
  }
  async change(event){
    if(event.target.id==='diff-collapse'){this.collapse=event.target.checked;await this.createDiff();}
  }
  async createDiff(){
    const sequence=++this.request, parent=this.host?.querySelector('#json-diff');
    if(!parent)return;
    if(this.merge){this.merge.destroy();this.merge=null;}
    if(this.unified){this.unified.destroy();this.unified=null;}
    parent.replaceChildren();this.host.querySelector('#diff-summary').textContent='正在计算差异…';
    const content=async id=>{const data=await this.services.api('config'+(id==='current'?'':'?id='+encodeURIComponent(id)));return {text:data.content,revision:id==='current'?data.revision:data.versions.find(v=>v.id===id)?.revision};};
    try{
      const first=content(this.left);
      const [a,b]=await Promise.all([first,content('current')]);
      if(sequence!==this.request||!parent.isConnected)return;
      const collapseUnchanged=this.collapse?{margin:3,minSize:6}:undefined;
      if(this.layout==='unified'){
        this.unified=new EditorView({parent,doc:b.text,extensions:[...this.extensions(true,'配置差异'),unifiedMergeView({original:a.text,mergeControls:false,highlightChanges:true,gutter:true,collapseUnchanged})]});
      }else{
        this.merge=new MergeView({parent,a:{doc:a.text,extensions:this.extensions(true,'基准配置')},b:{doc:b.text,extensions:this.extensions(true,'目标配置')},highlightChanges:true,gutter:true,collapseUnchanged});
      }
      this.chunk=-1;this.diffSummary();
    }catch(error){if(sequence===this.request&&parent.isConnected){this.host.querySelector('#diff-summary').textContent='对比加载失败';parent.textContent=error.message;}}
  }
  chunks(){return this.merge?.chunks || (this.unified?getChunks(this.unified.state)?.chunks:[]) || [];}
  diffSummary(){
    if(!this.merge&&!this.unified)return;
    const chunks=this.chunks();
    const a=this.merge?this.merge.a.state.doc:getOriginalDoc(this.unified.state);
    const b=this.merge?this.merge.b.state.doc:this.unified.state.doc;
    let added=0,removed=0;
    for(const chunk of chunks){const count=text=>text?text.split('\n').length-(text.endsWith('\n')?1:0):0;removed+=count(a.sliceString(chunk.fromA,chunk.toA));added+=count(b.sliceString(chunk.fromB,chunk.toB));}
    this.host.querySelector('#diff-summary').innerHTML=chunks.length?`<strong>${chunks.length} 处差异</strong><span class="diff-added">+${added} 行</span><span class="diff-removed">−${removed} 行</span><span class="diff-position">${this.chunk>=0?`${this.chunk+1} / ${chunks.length}`:''}</span>`:'<span class="diff-identical">✓ 两个版本内容一致</span>';
    for(const action of ['previous','next'])this.host.querySelector(`[data-config-action="${action}"]`).disabled=!chunks.length;
  }
  jump(direction){
    const chunks=this.chunks();
    if(!chunks.length)return;
    this.chunk=this.chunk<0?(direction>0?0:chunks.length-1):(this.chunk+direction+chunks.length)%chunks.length;
    const chunk=chunks[this.chunk];
    const targets=this.merge?[[this.merge.a,chunk.fromA],[this.merge.b,chunk.fromB]]:[[this.unified,chunk.fromB]];
    for(const [view,pos] of targets)view.dispatch({selection:{anchor:Math.min(pos,view.state.doc.length)},effects:EditorView.scrollIntoView(Math.min(pos,view.state.doc.length),{y:'center'})});
    this.diffSummary();
  }
}
window.CorkiConfig=new ConfigWorkspace();

// Shared read-only JSON surface for local API diagnostics.
window.CorkiJsonViewer = (parent, value) => {
  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc: JSON.stringify(value, null, 2),
      extensions: [basicSetup, language, jsonFolding, highlighting, theme, phrases,
        EditorState.readOnly.of(true), EditorView.editable.of(false),
        EditorView.contentAttributes.of({'aria-label':'MCP 返回 JSON',tabindex:'0'})],
    }),
  });
  return {destroy:()=>view.destroy()};
};

// Editable JSON fields share the same highlighting and folding as configuration files.
window.CorkiJsonEditor = (parent, value, label='JSON', onChange=()=>{}, hint='') => {
  const view=new EditorView({parent,state:EditorState.create({doc:value===undefined?'':JSON.stringify(value,null,2),
    extensions:[basicSetup,language,jsonFolding,highlighting,theme,phrases,placeholder(hint),
      EditorView.updateListener.of(update=>{if(update.docChanged)onChange();}),
      EditorView.contentAttributes.of({'aria-label':label})]})});
  return {text:()=>view.state.doc.toString(),setText:text=>view.dispatch({changes:{from:0,to:view.state.doc.length,insert:text}}),
    value:()=>{try{return JSON.parse(view.state.doc.toString());}catch{throw Error(label+'：JSON 格式错误');}},
    destroy:()=>view.destroy()};
};

window.CorkiTextViewer = (parent, text) => {
  const view=new EditorView({parent,state:EditorState.create({doc:text,
    extensions:[basicSetup,theme,phrases,EditorState.readOnly.of(true),EditorView.editable.of(false),
      EditorView.contentAttributes.of({'aria-label':'工具返回内容',tabindex:'0'})]})});
  return {destroy:()=>view.destroy()};
};
