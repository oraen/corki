// Transport configuration owns its revision and lifecycle, separate from prompt editing.
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
const svg=path=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
const icons={plus:svg('<path d="M12 5v14M5 12h14"/>'),edit:svg('<path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15z"/>'),trash:svg('<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>'),close:svg('<path d="m6 6 12 12M6 18 18 6"/>'),network:svg('<circle cx="5" cy="12" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="m7 11 10-4M7 13l10 4"/>'),chevron:svg('<path d="m8 10 4 4 4-4"/>'),refresh:svg('<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1M6 18a7 7 0 0 0 12-1"/>')};

// Compact overview; the full page remains the connection configuration authority.
export class ApplicationKafkaGatewayView {
  constructor(api){this.api=api;this.generation=0;}
  mount(root,toast){
    const first=!this.root;this.root=root;this.toast=toast;
    root.onclick=event=>{event.stopPropagation();this.click(event).catch(error=>this.toast(error.message));};
    this.render();if(first)this.reload().catch(error=>{this.error=error.message;this.render();this.toast(error.message);});
  }
  unmount(){this.generation++;if(this.root)this.root.onclick=null;this.root=null;}
  async reload(){
    const generation=this.generation;const data=await this.api('kafka');
    if(generation!==this.generation||!this.root)return;
    this.state=data.global_state;this.error='';this.render();
  }
  render(){
    if(!this.root)return;
    const busy=this.changing||(!this.state&&!this.error);
    const enabled=!!this.state?.enabled;
    const text=this.changing?(enabled?'关闭中':'开启中'):this.error?'读取失败':this.state?(enabled?'已开启':'已关闭'):'加载中';
    this.root.innerHTML=`<div class="application-gateway-summary"><span class="application-gateway-mark">${icons.network}</span><div class="application-gateway-title"><strong>Kafka 接入</strong><span class="application-gateway-state ${this.error?'failed':enabled?'running':''}">${busy?'<i class="spinner"></i>':'<i></i>'}${text}</span></div></div><div class="application-gateway-controls"><button class="icon-button" data-application-kafka="refresh" aria-label="刷新 Kafka 接入状态" ${busy?'disabled':''}>${icons.refresh}</button><button class="button" data-application-kafka="settings" ${busy?'disabled':''}>接入设置</button><button class="toggle${enabled?' on':''}" role="switch" aria-label="启用全局 Kafka" aria-checked="${enabled}" data-application-kafka="toggle" ${busy||!this.state?'disabled':''}></button></div>`;
  }
  async click(event){
    const action=event.target.closest('[data-application-kafka]')?.dataset.applicationKafka;
    if(action==='settings'){location.hash='#applications/kafka';return;}
    if(action==='refresh')return this.reload();
    if(action!=='toggle'||this.changing||!this.state)return;
    this.changing=true;this.render();const generation=this.generation;
    try{
      const state=await this.api('kafka-global',{enabled:!this.state.enabled,expected:this.state.revision},true);
      if(generation===this.generation){this.state=state;this.toast(state.enabled?'Kafka 接入已开启':'Kafka 接入已关闭');}
    }catch(error){if(generation===this.generation)await this.reload().catch(()=>{});throw error;}
    finally{this.changing=false;if(generation!==this.generation&&this.root)await this.reload().catch(()=>{});this.render();}
  }
}

export class ApplicationAccessView {
  constructor(api, services, owner='') {
    this.api=api;this.services=services;this.owner=owner;this.generation=0;this.pending=new Set();
  }
  mount(root){
    this.root=root;this.visible=true;
    root.onclick=event=>{event.stopPropagation();this.click(event).catch(error=>this.services.toast(error.message));};
    root.onchange=event=>{event.stopPropagation();this.change(event).catch(error=>this.services.toast(error.message));};
    root.innerHTML='<div class="application-access-loading"><span class="spinner"></span>正在读取接入配置…</div>';
    this.load().catch(error=>{if(this.root)this.root.innerHTML=`<div class="application-access-loading">${esc(error.message)}<button class="button" data-access-action="reload">重试</button></div>`;});
  }
  setVisible(visible){
    this.visible=visible;clearTimeout(this.timer);
    if(visible&&this.root&&this.data)this.poll();
  }
  destroy(){
    this.generation++;this.visible=false;clearTimeout(this.timer);
    if(this.root){this.root.onclick=this.root.onchange=null;}
    this.root=null;
    this.closeDialog();
  }
  async load(){
    const generation=this.generation;
    const [data,flags]=await Promise.all([this.api('kafka',{app:this.owner}),this.owner?this.api('access',{app:this.owner}):Promise.resolve(null)]);
    if(generation!==this.generation||!this.root)return;
    this.data=data;this.flags=flags;this.render();this.poll();
  }
  poll(){
    clearTimeout(this.timer);
    if(!this.visible||!this.root||!this.data?.connections.some(c=>c.enabled&&(this.owner||this.data.global_state.enabled)))return;
    const generation=this.generation;
    this.timer=setTimeout(async()=>{
      try{
        const data=await this.api('kafka',{app:this.owner});
        if(generation!==this.generation||!this.root)return;
        this.data=data;
        // Only runtime badges/counters change during polling. Keep focus, inputs and scroll stable.
        for(const connection of data.connections){
          const card=[...this.root.querySelectorAll('[data-kafka-card]')].find(c=>c.dataset.kafkaCard===connection.id);
          if(card)this.updateCard(card,connection);
        }
      }catch{/* Keep the last confirmed configuration; manual refresh remains available. */}
      if(generation===this.generation)this.poll();
    },5000);
  }
  status(connection){
    if(this.pending.has(connection.id))return {name:'保存中',kind:'busy'};
    if(!connection.enabled)return {name:'已关闭',kind:'off'};
    if(!this.owner&&!this.data.global_state.enabled)return {name:'已暂停',kind:'off'};
    const state=this.data.runtime[connection.id]?.state;
    return state==='connected'?{name:'已连接',kind:'connected'}:state==='failed'?{name:'连接异常',kind:'failed'}:{name:'连接中',kind:'busy'};
  }
  badge(connection){const s=this.status(connection);if(s.kind==='failed')return `<button class="app-kafka-status failed" data-access-action="error" data-connection="${connection.id}"><i></i>${s.name}</button>`;return `<span class="app-kafka-status ${s.kind}">${s.kind==='busy'?'<i class="spinner"></i>':'<i></i>'}${s.name}</span>`;}
  statistics(connection){
    const metric=this.data.metrics[connection.id]||{};
    return `${metric.queued?`<span>待处理 ${metric.queued}</span>`:''}${metric.failed?`<button class="text-button app-kafka-failed" data-access-action="error" data-connection="${connection.id}">回复失败 ${metric.failed}</button><button class="text-button" data-access-action="retry" data-connection="${connection.id}" ${!connection.enabled||(!this.owner&&!this.data.global_state.enabled)?'disabled':''}>重试发送</button>`:''}`;
  }
  updateCard(card,connection){
    card.querySelector('[data-kafka-status]').innerHTML=this.badge(connection);
    card.querySelector('[data-kafka-stats]').innerHTML=this.statistics(connection);
  }
  card(connection){
    return `<article class="app-kafka-card" data-kafka-card="${connection.id}"><div class="app-kafka-card-top"><span class="app-kafka-mark">${icons.network}</span><div class="app-kafka-identity"><h4>${esc(connection.name)}</h4><div class="app-kafka-brokers">${esc(connection.brokers.join(', '))}</div></div><span data-kafka-status>${this.badge(connection)}</span><button class="toggle${connection.enabled?' on':''}" role="switch" aria-label="启用 ${esc(connection.name)}" aria-checked="${connection.enabled}" data-access-action="toggle" data-connection="${connection.id}" ${this.pending.has(connection.id)?'disabled':''}></button></div><dl class="app-kafka-topics"><div><dt>输入</dt><dd>${connection.input_topics.map(topic=>`<span>${esc(topic)}</span>`).join('')}</dd></div><div><dt>输出</dt><dd><span>${esc(connection.output_topic)}</span></dd></div></dl><div class="app-kafka-card-foot"><div class="app-kafka-stats" data-kafka-stats>${this.statistics(connection)}</div><button class="text-button" data-access-action="edit" data-connection="${connection.id}">编辑</button><button class="text-button app-kafka-delete" data-access-action="delete" data-connection="${connection.id}">删除</button></div></article>`;
  }
  protocol(){
    const input={...(this.owner?{}:{appid:100000}),input:"你好",id:"request-1"};
    const output={appid:100000,id:"request-1",event:"result",status:"completed",output:"你好！"};
    return `<details class="app-kafka-protocol"><summary>消息格式 ${icons.chevron}</summary><div><section><h4>输入</h4><pre>${esc(JSON.stringify(input,null,2))}</pre></section><section><h4>输出</h4><pre>${esc(JSON.stringify(output,null,2))}</pre></section></div><p>${this.owner?'appid 可省略，提供时须与当前应用一致。':'appid 填写目标应用 ID。'}id 可选，返回时原样保留；未提供时不添加。</p></details>`;
  }
  render(){
    if(!this.root)return;
    const global=this.data.global_state;
    this.root.innerHTML=`<div class="application-access-workspace">${this.owner?`<section class="app-access-flags" aria-label="调用方式"><label class="app-access-choice"><input type="checkbox" name="http_enabled" ${this.flags.http_enabled?'checked':''} ${this.savingFlags?'disabled':''}><span><strong>HTTP 调用</strong><small>允许通过应用网关调用此应用</small></span></label><label class="app-access-choice"><input type="checkbox" name="global_kafka" ${this.flags.global_kafka?'checked':''} ${this.savingFlags?'disabled':''}><span><strong>接入全局 Kafka</strong><small>处理全局 Topic 中指定此应用 ID 的消息</small></span></label><a class="text-button app-access-global-link" href="#applications/kafka">管理全局接入</a></section>`:`<div class="app-kafka-global-heading"><div><h2>Kafka 接入 <small>${this.data.connections.length}</small></h2></div><span class="app-kafka-global-state">${global.enabled?'已开启':'已关闭'}</span><button class="toggle${global.enabled?' on':''}" role="switch" aria-label="启用全局 Kafka" aria-checked="${global.enabled}" data-access-action="global-toggle" ${this.savingGlobal?'disabled':''}></button></div>`}<div class="app-kafka-section-heading"><h3>${this.owner?'独立 Kafka 接入':'集群接入'}${this.owner?` <small>${this.data.connections.length}</small>`:''}</h3><button class="icon-button" data-access-action="reload" aria-label="刷新 Kafka 状态">${icons.refresh}</button><button class="button" data-access-action="add">${icons.plus}添加接入</button></div>${this.data.runtime.service?.error?`<p class="app-kafka-service-error" role="alert">${esc(this.data.runtime.service.error)}</p>`:''}<div class="app-kafka-grid">${this.data.connections.map(c=>this.card(c)).join('')}</div>${!this.data.connections.length?`<div class="app-kafka-empty"><span>${icons.network}</span><h3>暂无 Kafka 接入</h3><p>${this.owner?'独立配置此应用使用的集群与 Topic':'添加集群后，消息通过 appid 路由到已接入的应用'}</p></div>`:''}${this.protocol()}</div>`;
  }
  async change(event){
    if(!this.owner||!['http_enabled','global_kafka'].includes(event.target.name)||this.savingFlags)return;
    const old=this.flags;
    const next={...old,[event.target.name]:event.target.checked};
    this.savingFlags=true;this.flags=next;this.render();
    try{
      const saved=await this.api('save-access',{app:this.owner,expected:old.revision,http_enabled:next.http_enabled,global_kafka:next.global_kafka},true);
      this.flags={...next,...saved};this.services.toast('接入配置已保存');
    }catch(error){this.flags=old;throw error;}
    finally{this.savingFlags=false;this.render();}
  }
  async click(event){
    const button=event.target.closest('[data-access-action]');if(!button)return;
    const action=button.dataset.accessAction;
    if(action==='reload')return this.load();
    if(action==='add')return this.editor();
    if(action==='global-toggle'){
      if(this.savingGlobal)return;
      this.savingGlobal=true;this.render();
      try{await this.api('kafka-global',{enabled:!this.data.global_state.enabled,expected:this.data.global_state.revision},true);await this.load();}
      finally{this.savingGlobal=false;this.render();}return;
    }
    const connection=this.data.connections.find(c=>c.id===button.dataset.connection);if(!connection)return;
    if(action==='edit')return this.editor(connection);
    if(action==='error')return this.errorDialog(connection);
    if(action==='delete')return this.confirmDelete(connection);
    if(action==='retry'){
      button.disabled=true;
      try{await this.api('retry-kafka',{app:this.owner,connection:connection.id},true);this.services.toast('已重新加入发送队列');await this.load();}
      finally{if(button.isConnected)button.disabled=false;}return;
    }
    if(action==='toggle'){
      if(this.pending.has(connection.id))return;
      this.pending.add(connection.id);this.render();
      try{await this.api('save-kafka',{app:this.owner,connection:connection.id,expected:connection.revision,values:{enabled:!connection.enabled}},true);await this.load();}
      finally{this.pending.delete(connection.id);this.render();}
    }
  }
  closeDialog(){if(this.dialog){this.dialog.close();this.dialog.remove();this.dialog=null;}}
  showDialog(dialog){
    this.closeDialog();this.dialog=dialog;document.body.append(dialog);
    const previous=document.activeElement;
    const closeMenus=()=>{for(const detail of dialog.querySelectorAll('[data-kafka-select][open]'))detail.open=false;};
    const body=dialog.querySelector('.app-kafka-dialog-body');
    body?.addEventListener('scroll',closeMenus,{passive:true});
    window.addEventListener('resize',closeMenus);
    dialog.addEventListener('close',()=>{window.removeEventListener('resize',closeMenus);body?.removeEventListener('scroll',closeMenus);dialog.remove();if(this.dialog===dialog)this.dialog=null;if(previous?.isConnected)previous.focus();},{once:true});
    for(const detail of dialog.querySelectorAll('[data-kafka-select]'))detail.addEventListener('toggle',()=>{
      if(!detail.open)return;
      for(const other of dialog.querySelectorAll('[data-kafka-select][open]'))if(other!==detail)other.open=false;
      const rect=detail.querySelector('summary').getBoundingClientRect();
      const menu=detail.querySelector('.application-menu');
      menu.style.cssText=`position:fixed;left:${rect.left}px;top:${rect.bottom}px;bottom:auto;width:${rect.width}px;max-height:${Math.max(80,innerHeight-rect.bottom-16)}px`;
    });
    dialog.showModal();
  }
  errorDialog(connection){
    const error=this.data.metrics[connection.id]?.last_error||this.data.runtime[connection.id]?.error||'尚无具体错误信息';
    const dialog=document.createElement('dialog');dialog.className='app-kafka-dialog app-kafka-error-dialog';
    dialog.innerHTML=`<div class="app-kafka-dialog-heading"><h2>${esc(connection.name)}</h2><button class="icon-button" data-close aria-label="关闭">${icons.close}</button></div><div class="app-kafka-dialog-body"><pre>${esc(error)}</pre></div>`;
    dialog.querySelector('[data-close]').onclick=()=>this.closeDialog();this.showDialog(dialog);
  }
  confirmDelete(connection){
    const dialog=document.createElement('dialog');dialog.className='app-kafka-dialog app-kafka-confirm';dialog.setAttribute('aria-labelledby','kafka-delete-title');
    dialog.innerHTML=`<div class="app-kafka-dialog-body"><h2 id="kafka-delete-title">删除 Kafka 接入？</h2><div class="app-kafka-delete-target">${icons.network}<strong>${esc(connection.name)}</strong></div><p>此接入的任务将停止，未处理消息和待发送结果将删除。已保存的应用会话会保留，删除后无法恢复。</p><p data-error role="alert" hidden></p></div><div class="dialog-foot"><button class="button" data-cancel autofocus>取消</button><button class="button danger" data-delete>确认删除</button></div>`;
    dialog.querySelector('[data-cancel]').onclick=()=>this.closeDialog();
    let busy=false;dialog.oncancel=e=>{if(busy)e.preventDefault();};
    dialog.querySelector('[data-delete]').onclick=async()=>{
      if(busy)return;busy=true;for(const b of dialog.querySelectorAll('button'))b.disabled=true;dialog.querySelector('[data-delete]').textContent='删除中…';
      try{await this.api('delete-kafka',{app:this.owner,connection:connection.id,expected:connection.revision,stop:true},true);this.closeDialog();await this.load();this.services.toast('Kafka 接入已删除');}
      catch(error){const el=dialog.querySelector('[data-error]');el.textContent=error.message;el.hidden=false;busy=false;for(const b of dialog.querySelectorAll('button'))b.disabled=false;dialog.querySelector('[data-delete]').textContent='确认删除';}
    };this.showDialog(dialog);
  }
  select(name,label,value,options){
    return `<div class="app-kafka-field"><span>${label}</span><details class="application-picker" data-kafka-select="${name}"><summary role="button" aria-label="${label}"><span data-selected>${esc(options.find(o=>o[0]===value)?.[1]||value)}</span>${icons.chevron}</summary><div class="application-menu" role="listbox" aria-label="${label}">${options.map(([key,text])=>`<button type="button" role="option" aria-selected="${key===value}" data-select-value="${key}">${text}</button>`).join('')}</div></details></div>`;
  }
  editor(connection=null){
    const c=connection||{name:'Kafka 接入 '+(this.data.connections.length+1),brokers:[],input_topics:[],output_topic:'',group_id:'',enabled:true,security_protocol:'PLAINTEXT',sasl_mechanism:'PLAIN',sasl_username:'',ssl_ca_location:'',ssl_certificate_location:'',ssl_key_location:''};
    const selected={security_protocol:c.security_protocol,sasl_mechanism:c.sasl_mechanism};let topics=[...c.input_topics];
    const field=(name,label,value='',placeholder='',type='text')=>`<label class="app-kafka-field"><span>${label}</span><input name="${name}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="${type==='password'?'new-password':'off'}"></label>`;
    const dialog=document.createElement('dialog');dialog.className='app-kafka-dialog';dialog.setAttribute('aria-labelledby','kafka-editor-title');
    dialog.innerHTML=`<div class="app-kafka-dialog-heading"><h2 id="kafka-editor-title">${connection?'编辑 Kafka 接入':'添加 Kafka 接入'}</h2><button class="icon-button" data-close aria-label="关闭">${icons.close}</button></div><div class="app-kafka-dialog-body"><div class="app-kafka-form-grid">${field('name','接入名称',c.name)}${field('group_id','消费组',c.group_id,'自动生成')}</div><label class="app-kafka-field"><span>Kafka 地址</span><textarea name="brokers" rows="2" placeholder="localhost:9092，多个地址用逗号分隔">${esc(c.brokers.join(', '))}</textarea></label><div class="app-kafka-field"><span>输入 Topic</span><div class="app-kafka-topic-input"><div data-topics></div><input name="topic-entry" aria-label="添加输入 Topic" placeholder="输入 Topic 后按回车添加"></div></div>${field('output_topic','输出 Topic',c.output_topic)}<section class="app-kafka-security"><h3>认证与安全设置</h3><div class="app-kafka-security-body">${this.select('security_protocol','安全协议',selected.security_protocol,[['PLAINTEXT','无认证'],['SASL_PLAINTEXT','SASL'],['SSL','TLS'],['SASL_SSL','SASL + TLS']])}<div data-sasl>${this.select('sasl_mechanism','认证方式',selected.sasl_mechanism,[['PLAIN','PLAIN'],['SCRAM-SHA-256','SCRAM-SHA-256'],['SCRAM-SHA-512','SCRAM-SHA-512']])}<div class="app-kafka-form-grid">${field('sasl_username','用户名',c.sasl_username)}${field('sasl_password','密码','',c.has_sasl_password?'已配置':'输入密码','password')}</div></div><div data-tls>${field('ssl_ca_location','CA 证书路径',c.ssl_ca_location,'使用系统信任证书')}<div class="app-kafka-form-grid">${field('ssl_certificate_location','客户端证书路径',c.ssl_certificate_location)}${field('ssl_key_location','客户端私钥路径',c.ssl_key_location)}</div>${field('ssl_key_password','私钥密码','',c.has_ssl_key_password?'已配置':'可选','password')}</div></div></section><p class="app-kafka-form-error" data-error role="alert" hidden></p></div><div class="dialog-foot"><label class="app-kafka-enable"><input type="checkbox" name="enabled" ${c.enabled?'checked':''}>启用此接入</label><button class="button" data-close>取消</button><button class="button primary" data-save>保存接入</button></div>`;
    const paintTopics=()=>{dialog.querySelector('[data-topics]').innerHTML=topics.map((topic,i)=>`<span>${esc(topic)}<button type="button" data-remove-topic="${i}" aria-label="移除 ${esc(topic)}">${icons.close}</button></span>`).join('');};
    const addTopics=()=>{const input=dialog.querySelector('[name=topic-entry]');topics=[...new Set([...topics,...input.value.split(/[,，\n]+/).map(t=>t.trim()).filter(Boolean)])];input.value='';paintTopics();};
    const input=dialog.querySelector('[name=topic-entry]');
    input.onkeydown=e=>{if(e.key==='Enter'&&!e.isComposing&&e.keyCode!==229){e.preventDefault();addTopics();}};
    input.onblur=addTopics;input.onpaste=()=>setTimeout(()=>{if(dialog.isConnected&&/[,，\n]/.test(input.value))addTopics();},0);
    const updateSecurity=()=>{dialog.querySelector('[data-sasl]').hidden=!selected.security_protocol.startsWith('SASL');dialog.querySelector('[data-tls]').hidden=!selected.security_protocol.includes('SSL');};
    let busy=false;dialog.oncancel=e=>{if(busy)e.preventDefault();};
    const generation=this.generation;
    dialog.onclick=async event=>{
      const button=event.target.closest('button');if(!button)return;
      if(button.hasAttribute('data-close')){if(!busy)this.closeDialog();return;}
      if(button.hasAttribute('data-remove-topic')){topics.splice(Number(button.dataset.removeTopic),1);paintTopics();return;}
      if(button.hasAttribute('data-select-value')){
        const detail=button.closest('[data-kafka-select]');selected[detail.dataset.kafkaSelect]=button.dataset.selectValue;
        detail.querySelector('[data-selected]').textContent=button.textContent;detail.open=false;
        for(const option of detail.querySelectorAll('[role=option]'))option.setAttribute('aria-selected',String(option===button));
        updateSecurity();return;
      }
      if(!button.hasAttribute('data-save')||busy)return;
      addTopics();const values={...selected,input_topics:topics,enabled:dialog.querySelector('[name=enabled]').checked};
      for(const element of dialog.querySelectorAll('input[name],textarea[name]')){
        const name=element.name;if(['topic-entry','enabled'].includes(name))continue;
        if((name==='sasl_password'||name==='ssl_key_password')&&!element.value&&connection)continue;
        if(name==='group_id'&&!element.value.trim()&&!connection)continue;
        values[name]=element.value;
      }
      busy=true;button.disabled=true;button.textContent='保存中…';
      try{await this.api('save-kafka',{app:this.owner,connection:connection?.id,expected:connection?.revision,values},true);if(generation!==this.generation)return;this.closeDialog();await this.load();this.services.toast('Kafka 接入已保存');}
      catch(error){const el=dialog.querySelector('[data-error]');el.textContent=error.message;el.hidden=false;}
      finally{busy=false;button.disabled=false;button.textContent='保存接入';}
    };
    dialog.addEventListener('pointerdown',event=>{for(const detail of dialog.querySelectorAll('[data-kafka-select][open]'))if(!detail.contains(event.target))detail.open=false;});
    dialog.addEventListener('keydown',event=>{if(event.key==='Escape'&&dialog.querySelector('[data-kafka-select][open]')){event.preventDefault();event.stopPropagation();for(const detail of dialog.querySelectorAll('[data-kafka-select][open]'))detail.open=false;}});
    paintTopics();updateSecurity();this.showDialog(dialog);
  }
}
