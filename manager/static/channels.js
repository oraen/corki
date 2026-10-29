/* Messaging gateway workspace; all mutations cross the independent JSON API. */
window.CorkiChannels = (() => {
  let root,
    host,
    data,
    timer,
    epoch = 0,
    busy = false,
    modal = null,
    activeSelect = null,
    requestTipClose = () => {},
    loading = false,
    selected = null,
    managementTab = "history",
    applicationRows = new Map(),
    applicationBefore = null,
    applicationLoading = false,
    applicationRevision = 0,
    applicationName = "",
    applicationStatus = "",
    applicationFilterTimer = null,
    applicationPending = new Map(),
    historyState = null,
    historyLoading = false,
    selectedThread = null,
    sessionMode = null,
    sessionItems = new Map(),
    sessionsBefore = undefined,
    threadGeneration = 0,
    revision = 0,
    powerPending = null,
    packagePending = new Map(),
    seenJobs = new Map();
  const rememberedTabs = new Map();
  const tabStorageKey = id => 'corki.channel.management-tab.' + id;
  function restoreTab(id) {
    if(!id)return 'history';
    let tab=rememberedTabs.get(id);
    if(!tab) { try { tab=localStorage.getItem(tabStorageKey(id)); } catch {} }
    return ['history','settings','applications'].includes(tab) ? tab : 'history';
  }
  function rememberTab(id, tab) {
    if(!id || rememberedTabs.get(id)===tab)return;
    rememberedTabs.set(id,tab);
    try { localStorage.setItem(tabStorageKey(id),tab); } catch {}
  }
  const esc = (v) =>
    String(v ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const glyph =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="6" cy="12" r="3"/><circle cx="18" cy="5" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 10.5 6.8-4M8.6 13.5l6.8 4"/></svg>';
  const labels = {
    connecting: "连接中",
    online: "已连接",
    qr: "等待扫码",
    verification: "等待验证",
    error: "连接异常",
  };
  const states = {
    queued: "等待执行",
    running: "执行中",
    sending: "发送中",
    completed: "已完成",
    cancelled: "已停止",
    failed: "失败",
    interrupted: "已中断",
  };
  const channelName = (id) =>
    data?.connections?.find((c) => c.id === id)?.name || data?.channels.find((c) => c.id === id)?.name || id;
  const date = (value) =>
    new Date(value * 1000)
      .toLocaleString("sv-SE", { hour12: false })
      .slice(0, 16);
  const mark = (id) =>
    ({ whatsapp: "W", telegram: "T", feishu: "飞", wechat: "微" })[id] ||
    id.slice(0, 2).toUpperCase();
  const avatar = (id, builtin) => {
    const type = data?.connections?.find(c => c.id === id)?.channel || id;
    const known = data?.channels.find(c => c.id === type)?.builtin ?? builtin;
    if (data?.channels.find(c => c.id === type)?.standard) return '<svg class="channel-http-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m10 13 4-4m-6 6-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 3 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 1)"/></svg>';
    return known ? `<img src="/channel-icons/${esc(type)}.${type === "feishu" ? "png" : "svg"}" alt="" width="32" height="32">` : `<span class="channel-initials">${esc(mark(type))}</span>`;
  };
  const activeJob = (id) => packagePending.has(id) ||
    ["queued", "running"].includes(data?.package_jobs?.[id]?.status);
  function observeJobs(value) {
    for (const [id, job] of Object.entries(value.package_jobs || {})) {
      const previous = seenJobs.get(id);
      if (previous?.token === job.token && ["queued", "running"].includes(previous.status) &&
          ["completed", "failed"].includes(job.status)) {
        host.toast(`${channelName(id)}：${job.stage}`);
      }
      seenJobs.set(id, job);
    }
  }
  function channelCard(c) {
    const installed = !c.builtin || data.installed?.[c.id] === true;
    const working = activeJob(c.id);
    const operation = packagePending.get(c.id) || data.package_jobs?.[c.id]?.action;
    const actionLabel = working ? (operation === "uninstall-dependencies" ? "卸载中" : "安装中") : (installed ? (c.builtin ? "卸载" : "删除") : "安装");
    return `<article class="channel-card ${installed ? "" : "not-installed"} ${working ? "is-processing" : ""}" aria-busy="${!!working}">
      <div class="channel-card-head"><span class="channel-avatar">${avatar(c.id,c.builtin)}</span>
      <div class="channel-name"><h3 title="${esc(c.name)}">${esc(c.name)}</h3></div>
      <button class="channel-setup" ${installed ? (c.builtin ? 'data-uninstall' : 'data-delete') : 'data-install'}="${esc(c.id)}" ${working || busy ? "disabled" : ""}>${actionLabel}</button></div>
    </article>`;
  }
  function connectionsPanel() {
    return `<section class="panel channel-panel"><div class="panel-head"><h2>对话接入</h2><button class="button primary" data-act="add-connection">＋ 新增接入</button></div>
    ${data.connections?.length ? `<div class="table-wrap"><table class="channel-connections"><thead><tr><th>接入名称</th><th>渠道</th><th>工作目录</th><th>状态</th><th>启用</th><th>操作</th></tr></thead><tbody>${data.connections.map(c => {
      const state = data.running && (c.enabled || data.statuses[c.id]?.state === "error") ? data.statuses[c.id]?.state : null;
      const installed = data.installed?.[c.channel] !== false;
      const locked = activeJob(c.channel) || busy || (!c.enabled && (!data.running || !installed));
      const hint = activeJob(c.channel) ? "渠道组件处理中" : !installed ? "请先安装此渠道" : !data.running ? "需要先开启转发网关" : "";
      const group = data.conversations?.find(g => g.id === c.conversation_id);
      const path = c.values.workdir || "~/.corki/main/default";
      return `<tr><td><strong>${esc(c.name)}</strong></td><td><span class="channel-table-brand">${avatar(c.channel,true)}${esc(channelName(c.channel))}</span></td><td><span class="channel-path" title="${esc(path)}">${esc(path)}</span></td><td>${state === "error" ? `<button type="button" class="channel-status channel-status-error" data-connection-error="${esc(c.id)}"><i></i>连接异常</button>` : `<span class="channel-status ${state === "online" ? "online" : ""}"><i></i>${labels[state] || (c.enabled ? "待启动" : "已关闭")}</span>`}${["qr","verification"].includes(state) ? `<button class="text-button" data-login="${esc(c.id)}">完成登录</button>` : ""}</td><td><span ${hint ? `data-tooltip="${hint}" tabindex="0"` : ""}><button class="toggle ${c.enabled ? "on" : ""}" role="switch" aria-label="${esc(c.name)} 启用状态" aria-checked="${!!c.enabled}" data-toggle="${esc(c.id)}" ${locked ? "disabled" : ""}></button></span></td><td><div class="actions"><button class="text-button" data-config="${esc(c.id)}">管理</button><button class="text-button channel-remove" data-remove-account="${esc(c.id)}">删除</button></div></td></tr>`;
    }).join("")}</tbody></table></div>` : '<div class="channel-empty">暂无对话接入</div>'}</section>`;
  }
  function draw() {
    if (!root || !data) return;
    if (selected) {
      if (!data.connections.some(c => c.id === selected)) {
        root.innerHTML = '<section class="panel channel-empty">接入不存在<a href="#channels">返回转发网关</a></section>';
      } else if (!root.querySelector('.channel-management')) configure(selected);
      root.querySelectorAll('[data-page-save], [data-page-edit], [data-page-cancel], [data-reset-conversation]').forEach(b => b.disabled = busy);
      const settings=root.querySelector('.channel-account-settings');
      if(settings) {
        const locked=busy || settings.dataset.editing !== 'true';
        lockConnectionFields(settings,locked);
      }
      const validation=data.connections.find(c=>c.id===selected)?.values.session_validation==='true';
      const tab=root.querySelector('[data-channel-tab="applications"]');
      if(tab)tab.hidden=!validation;
      if(!validation && managementTab==='applications')root._showChannelTab?.('history');
      updateQueue();
      return;
    }
    const powerLabel = powerPending ? (powerPending.enabled ? "开启中" : "关闭中") : (data.running ? "运行中" : "已关闭");
    root.innerHTML = `<section class="panel channel-power"><span class="channel-symbol">${glyph}</span><div class="channel-power-title"><h2>转发网关</h2></div><span class="channel-status ${powerPending ? "pending" : data.running ? "online" : ""}" role="status" aria-live="polite">${powerPending ? '<span class="spinner" aria-hidden="true"></span>' : '<i></i>'}${powerLabel}</span><button class="button ${data.running ? "" : "primary"}" data-act="power" aria-busy="${!!powerPending}" ${powerPending ? "disabled" : ""}>${data.running ? "关闭网关" : "开启网关"}</button></section>
  <section class="panel channel-panel"><div class="panel-head"><h2>渠道接入</h2><div class="actions"><button class="button" data-act="import">${'<span aria-hidden="true">＋</span>'} 自定义连接器</button><button class="button primary" data-act="standard">＋ 标准连接器</button></div></div><div class="channel-grid">${data.channels
    .map(channelCard)
    .join("")}</div></section>
  ${connectionsPanel()}
  ${data.interactions.length ? `<section class="panel channel-panel"><div class="panel-head"><h2>等待确认 <span class="subtle">${data.interactions.length}</span></h2></div>${data.interactions.map((i) => `<div class="channel-peer"><span class="channel-avatar compact">${avatar(i.channel, ["whatsapp","telegram","feishu","wechat"].includes(i.channel))}</span><div><strong>${esc(channelName(i.channel))}</strong><p>${i.kind === "question" ? "Agent 需要补充信息" : "Agent 需要审批"}</p></div><button class="button" data-interaction="${esc(i.request_id)}">查看请求</button></div>`).join("")}</section>` : ""}
  `;
    root.querySelectorAll('[data-connection-error]').forEach(button => button.onclick = () => {
      const status=data.statuses[button.dataset.connectionError] || {};
      const d=status.detail;
      const el=dialog('连接异常', `<div class="channel-diagnostic-head">${esc(channelName(button.dataset.connectionError))}</div>${d ? `<dl class="channel-request-detail"><dt>访问地址</dt><dd>${esc(d.url || '—')}</dd><dt>请求方式</dt><dd>${esc(d.method || '—')}</dd><dt>状态码</dt><dd class="channel-http-failure">${esc(d.status || '未收到 HTTP 响应')}</dd><dt>失败时间</dt><dd>${esc(date(d.time))}</dd>${d.stage ? `<dt>发生阶段</dt><dd>${esc(d.stage)}</dd>` : ''}</dl><div class="channel-response-title">响应内容</div><div class="channel-response-body"></div>` : `<p>${esc(status.message || '暂无请求详情，等待下一次连接重试。')}</p>`}`, null);
      el.classList.add('channel-diagnostics');
      el.querySelector('.channel-dialog-foot [data-close]').textContent='关闭';
      if(d) {
        const box=el.querySelector('.channel-response-body'), text=d.response || d.error || '响应正文为空';
        try { const value=JSON.parse(text); const viewer=window.CorkiJsonViewer(box,value);modal.dispose=()=>viewer.destroy(); }
        catch { box.innerHTML=`<pre>${esc(text)}</pre>`; }
      }
    });
    root.querySelector('[data-act="add-connection"]').onclick = () => chooseChannel();
    root.querySelectorAll("[data-remove-account]").forEach(b => b.onclick = () =>
      dialog("删除接入", "<p>登录信息和接入配置将永久删除，无法恢复。已保存的 Corki 会话保留。</p>",
        () => change({action:"delete-connection",id:b.dataset.removeAccount},"正在删除…",true),"确认删除"));
    root.querySelector('[data-act="power"]').onclick = () => power();
    root.querySelector('[data-act="import"]').onclick = () => importDialog();
    root.querySelector('[data-act="standard"]').onclick = () => standardDialog();
    root.querySelectorAll("[data-install]").forEach(b => {
      b.onclick = () => packageChange(b.dataset.install,"dependencies").catch(()=>{});
    });
    root.querySelectorAll("[data-uninstall]").forEach(b => {
      b.onclick = () => dialog("卸载 "+channelName(b.dataset.uninstall),
        "<p>将停止此渠道下的全部接入及任务，并移除依赖。账号配置、登录信息和历史保留。</p>",
        () => packageChange(b.dataset.uninstall,"uninstall-dependencies"),"确认卸载");
    });
    root
      .querySelectorAll("[data-config]")
      .forEach((b) => (b.onclick = () => location.hash = "channels/" + encodeURIComponent(b.dataset.config)));
    root
      .querySelectorAll("[data-delete]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            dialog(
              "删除连接器",
              "<p>连接器及登录信息将永久删除，已保存的 Corki 会话保留。</p>",
              () =>
                change(
                  { action: "delete", id: b.dataset.delete },
                  "正在删除…",
                  true,
                ),
              "确认删除",
            )),
      );
    root
      .querySelectorAll("[data-login]")
      .forEach((b) => (b.onclick = () => login(b.dataset.login)));
    root.querySelectorAll("[data-toggle]").forEach(
      (b) =>
        (b.onclick = () =>
          change(
            {
              action: "channel",
              id: b.dataset.toggle,
              enabled: b.getAttribute("aria-checked") !== "true",
            },
            "正在更新渠道…",
          )),
    );
    root
      .querySelectorAll("[data-interaction]")
      .forEach((b) => (b.onclick = () => interaction(b.dataset.interaction)));
    root.querySelectorAll("button").forEach((b) => {
      if (busy) b.disabled = true;
    });
  }
  async function refresh() {
    if (!root || loading || busy) return;
    loading = true;
    const generation = epoch, version = revision;
    try {
      const value = await host.api("channel-gateway");
      if (generation !== epoch || version !== revision) return;
      observeJobs(value);
      const changed = JSON.stringify(data) !== JSON.stringify(value);
      data = value;
      if (changed) draw();
      updateLogin();
      if (selected) {void refreshHistory();void refreshApplications();}
    } catch (e) {
      if (generation === epoch && root) {
        if (!data) {
          root.innerHTML = '<section class="panel channel-empty"></section>';
          root.firstElementChild.textContent = e.message;
        } else host.toast(e.message);
      }
    } finally {
      if (generation === epoch) loading = false;
    }
  }
  function dialog(title, body, submit, label = "保存") {
    closeDialog();
    const el = document.createElement("dialog");
    el.className = "channel-dialog";
    el.innerHTML = `<form><div class="channel-dialog-head"><h2>${esc(title)}</h2><button type="button" class="icon-button" data-close aria-label="关闭">×</button></div><div class="channel-dialog-body">${body}</div><p class="channel-form-error" role="alert"></p><div class="channel-dialog-foot"><button type="button" class="button" data-close>取消</button>${submit ? `<button class="button primary" type="submit">${esc(label)}</button>` : ""}</div></form>`;
    document.body.append(el);
    modal = { el };
    el.querySelectorAll("[data-close]").forEach(
      (b) => (b.onclick = closeDialog),
    );
    el.addEventListener("cancel", (e) => {
      e.preventDefault();
      closeDialog();
    });
    el.addEventListener("click", (e) => {
      if (e.target === el) closeDialog();
    });
    el.querySelector("form").onsubmit = async (e) => {
      e.preventDefault();
      if (!submit) return;
      const save = el.querySelector("[type=submit]");
      save.disabled = true;
      try {
        await submit(el);
        if (modal?.el === el) closeDialog();
      } catch (error) {
        el.querySelector("[role=alert]").textContent = error.message;
      } finally {
        save.disabled = false;
      }
    };
    el.showModal();
    return el;
  }
  function closeDialog() {
    closeSelect();
    if (modal) {
      modal.dispose?.();
      modal.el.close();
      modal.el.remove();
      modal = null;
    }
  }
  async function packageChange(id, action) {
    if (activeJob(id)) return;
    const generation = epoch, api = host.api, toast = host.toast;
    packagePending.set(id, action);
    revision++;
    draw();
    try {
      const value = await api("mutate", {kind: "channel-gateway", id, action});
      if (generation !== epoch) return;
      revision++;
      observeJobs(value);
      data = value;
    } catch (error) {
      toast(error.message);
      throw error;
    } finally {
      if (generation === epoch) {
        packagePending.delete(id);
        draw();
      }
    }
  }
  async function change(values, message = "正在保存…", propagate = false, success = "操作完成") {
    if (busy) throw new Error("正在处理，请稍后再试");
    busy = true;
    revision++;
    const generation = epoch,
      api = host.api,
      toast = host.toast;
    draw();
    const progress = document.createElement("div");
    progress.className = "channel-progress";
    progress.setAttribute("role", "status");
    progress.innerHTML = '<span class="spinner"></span><span></span>';
    progress.lastElementChild.textContent = message;
    document.body.append(progress);
    try {
      const result = await api("mutate", {
        kind: "channel-gateway",
        ...values,
      });
      if (generation === epoch) {
        data = result;
        draw();
        updateLogin();
      }
      toast(success);
      return result;
    } catch (error) {
      toast(error.message);
      if (propagate) throw error;
    } finally {
      progress.remove();
      if (generation === epoch) {
        busy = false;
        draw();
      }
    }
  }
  function power() {
    if (powerPending || busy) return;
    if (data.running && data.active) {
      dialog(
        "关闭转发网关",
        `<p>将停止 ${data.active} 个正在处理的任务，并断开所有渠道。</p>`,
        () => {
          closeDialog();
          void changePower(false);
        },
        "关闭网关",
      );
      return;
    }
    void changePower(!data.running);
  }
  async function changePower(enabled) {
    if (powerPending) return;
    // Lifecycle belongs to the operation, not the mounted page. Navigating away
    // must neither cancel it nor enable a duplicate start when returning.
    const operation = { enabled }, api = host.api, toast = host.toast;
    powerPending = operation;
    revision++;
    draw();
    try {
      const result = await api("mutate", {kind: "channel-gateway", action: "power", enabled});
      if (data) data = {...data, running: result.running, port: result.port};
      toast(enabled ? "转发网关已开启" : "转发网关已关闭");
    } catch (error) {
      toast(error.message);
    } finally {
      if (powerPending === operation) powerPending = null;
      revision++;
      draw();
      if (root) void refresh();
    }
  }
  const instructions = {
    dingtalk: "",
    telegram:
      "在 Telegram 的 @BotFather 创建机器人，填入 Bot Token。此机器人不能同时被其他服务轮询或绑定 Webhook。",
    feishu:
      "在飞书或 Lark 开放平台创建自建应用，启用机器人，开通私聊消息读取与发送权限，使用长连接订阅 im.message.receive_v1，并发布应用。",
    whatsapp:
      "开启渠道后，在 WhatsApp「已关联设备」中扫描二维码。使用独立设备连接，不同步旧消息。",
    wechat:
      "开启渠道后使用手机微信扫码确认。如需验证码，请输入手机上显示的数字。",
  };
  function selectField(name, items, value) {
    const selected = items.find(i => i[0] === value) || items[0];
    return `<details class="channel-select"><summary aria-haspopup="listbox" aria-expanded="false">${esc(selected?.[1] || "请选择")}<span>⌄</span></summary><div popover="manual" role="listbox">${items.map(([v,label]) => `<button type="button" role="option" aria-selected="${v===selected?.[0]}" data-option="${esc(v)}">${esc(label)}</button>`).join("")}</div><input type="hidden" name="${name}" value="${esc(selected?.[0] || "")}"></details>`;
  }
  function closeSelect() {
    if(!activeSelect)return;
    const {details,menu,controller}=activeSelect;
    activeSelect=null;controller.abort();
    if(menu.matches(':popover-open'))menu.hidePopover();
    details.open=false;details.querySelector('summary').setAttribute('aria-expanded','false');
  }
  function wireSelect(el, callback = () => {}) {
    el.querySelectorAll('.channel-select').forEach(details=>{
      details.addEventListener('toggle',()=>{
        if(!details.open) {if(activeSelect?.details===details)closeSelect();return;}
        if(!details.isConnected)return;
        if(activeSelect?.details===details)return;
        closeSelect();
        const menu=details.querySelector('[popover]'),summary=details.querySelector('summary');
        const controller=new AbortController(),options={signal:controller.signal};
        activeSelect={details,menu,controller};
        const position=()=>{
          if(!details.isConnected || details.closest('[hidden],[inert]')) {closeSelect();return;}
          const box=summary.getBoundingClientRect(),bottom=innerHeight-box.bottom-12,top=box.top-12;
          const up=bottom<Math.min(menu.scrollHeight,240) && top>bottom;
          const height=Math.min(280,Math.max(60,up?top:bottom));
          menu.style.width=Math.min(box.width,innerWidth-24)+'px';
          menu.style.maxHeight=height+'px';
          menu.style.left=Math.max(12,Math.min(box.left,innerWidth-box.width-12))+'px';
          menu.style.top=up?'auto':(box.bottom+6)+'px';
          menu.style.bottom=up?(innerHeight-box.top+6)+'px':'auto';
        };
        menu.showPopover();position();summary.setAttribute('aria-expanded','true');
        menu.querySelectorAll('[data-option]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.option===details.querySelector('input').value)));
        document.addEventListener('pointerdown',event=>{if(!details.contains(event.target))closeSelect();},options);
        document.addEventListener('scroll',event=>{if(!menu.contains(event.target))position();},{...options,capture:true});
        window.addEventListener('resize',position,options);
        details.addEventListener('keydown',event=>{
          const buttons=[...menu.querySelectorAll('[data-option]')];
          if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeSelect();summary.focus();}
          if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
            event.preventDefault();
            const index=buttons.indexOf(document.activeElement);
            const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:index<0?(event.key==='ArrowDown'?0:buttons.length-1):(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;
            buttons[next]?.focus({preventScroll:true});buttons[next]?.scrollIntoView({block:'nearest'});
          }
        },options);
      });
    });
    el.querySelectorAll('[data-option]').forEach(button=>button.onclick=event=>{
      event.preventDefault();event.stopPropagation();
      const details=button.closest('details'),input=details.querySelector('input');
      input.value=button.dataset.option;
      details.querySelector('summary').firstChild.textContent=button.textContent;
      closeSelect();details.open=false;details.querySelector('summary').focus({preventScroll:true});
      callback(input);
    });
  }
  function updateQueue() {
    const box=root?.querySelector('.channel-queue-alert'), account=data?.connections.find(c=>c.id===selected);
    if(!box) return;
    const pending=account?.queue;
    box.hidden=pending?.phase !== "blocked";
    if(box.hidden) { box.replaceChildren(); return; }
    const html=`<div><strong>消息处理已暂停</strong><p>${esc(pending.error)}</p></div><div class="actions"><button class="text-button" data-skip-message>跳过此消息</button></div>`;
    if(box._html === html && box.children.length) return;
    box.innerHTML=html; box._html=html;
    box.querySelector('[data-skip-message]').onclick=()=>dialog("跳过此消息",
      "<p>此消息将不再执行或重试回复，队列会继续处理下一条。已执行的操作不会撤销。</p>",
      ()=>change({action:"skip-http-message",id:selected},"正在跳过…",true),"确认跳过");
  }
  function wireRetryCount(el) {
    el.querySelectorAll('[name="retry_count"], [name="http_retry_count"]').forEach(input => {
      input.addEventListener('beforeinput', event => {
        if (event.data && !/^\d+$/.test(event.data)) event.preventDefault();
      });
      input.addEventListener('paste', event => {
        if (!/^\d+$/.test(event.clipboardData.getData('text').trim())) event.preventDefault();
      });
      input.addEventListener('input', () => {
        if (!input.value) return;
        const value = Number(input.value);
        if (!Number.isFinite(value)) input.value = '';
        else input.value = String(Math.max(0, Math.min(20, Math.trunc(value))));
      });
    });
  }
  function standardDialog() {
    const el=dialog("标准连接器",`<div class="standard-config">
      <label class="channel-field"><span>连接器名称</span><input name="name" required maxlength="60" placeholder="为连接器取个名字"></label>
      <div class="standard-endpoints">
      <label class="channel-field"><span>拉取问题接口<small>POST</small></span><input name="pull_url" required placeholder="https://example.com/questions?account=#{account}" spellcheck="false"></label>
      <label class="channel-field"><span>发送回复接口<small>POST</small></span><input name="reply_url" required placeholder="https://example.com/replies?account=#{account}" spellcheck="false"></label>
      </div><div class="standard-options"><label class="channel-field"><span>回复失败重试次数</span><input name="retry_count" type="number" min="0" max="20" step="1" value="3" required></label>
      <label class="channel-check"><span>禁用参数解析</span><input name="disable_parameters" type="checkbox"></label></div>
      <div class="standard-parameters" role="status" hidden></div></div><div class="standard-protocol" hidden></div>`,async el=>{
        const form=Object.fromEntries(new FormData(el.querySelector('form')));
        const disable=!!el.querySelector('[name="disable_parameters"]').checked;
        const noParameters=disable || !/#[{][A-Za-z_][A-Za-z0-9_]{0,63}[}]/.test(form.pull_url+' '+form.reply_url);
        await change({action:"create-standard",name:form.name,pull_url:form.pull_url,reply_url:form.reply_url,
          disable_parameters:disable,retry_count:Number(form.retry_count)},"正在创建连接器…",true,
          noParameters ? "已创建连接器和对话接入" : "已创建连接器，可在新增接入中填写参数");
      },"创建");
    el.classList.add('standard-dialog');
    wireRetryCount(el);
    const update=()=>{
      const disabled=el.querySelector('[name="disable_parameters"]').checked;
      const raw=['pull_url','reply_url'].map(k=>el.querySelector('[name="'+k+'"]').value).join(' ');
      const names=disabled?[]:[...new Set([...raw.matchAll(/#[{]([A-Za-z_][A-Za-z0-9_]{0,63})[}]/g)].map(m=>m[1]))];
      const parameters=el.querySelector('.standard-parameters');
      parameters.hidden=!names.length;
      parameters.innerHTML=names.length ? '<span>接入参数</span>'+names.map(n=>`<code>${esc(n)}</code>`).join('') : '';
    };
    el.querySelectorAll('input').forEach(input=>input.addEventListener('input',update));update();
    const guide=document.createElement('button');guide.type='button';guide.className='text-button standard-guide-trigger';guide.textContent='对接协议';
    el.querySelector('.channel-dialog-foot').prepend(guide);
    const viewers=[];
    modal.dispose=()=>viewers.forEach(v=>v.destroy());
    guide.onclick=()=>{
      const config=el.querySelector('.standard-config'),article=el.querySelector('.standard-protocol');
      const open=article.hidden;article.hidden=!open;config.hidden=open;
      el.classList.toggle('show-standard-protocol',open);
      el.querySelector('[type=submit]').hidden=open;
      guide.textContent=open?'返回配置':'对接协议';
      if(!open || article.children.length)return;
      const examples=[
        ['拉取请求',{protocol:1,connection_id:'account-example',cursor:'',limit:20}],
        ['拉取响应',{messages:[{id:'question-1001',sender:'user-01',text:'帮我整理今天的工作计划'}],next_cursor:'cursor-1001'}],
        ['回复请求',{protocol:1,type:'reply',connection_id:'account-example',message_id:'question-1001',reply_id:'reply-unique-id',text:'今天的工作计划如下…'}],
        ['确认回执',{received:true,message_id:'question-1001',reply_id:'reply-unique-id'}]
      ];
      article.innerHTML=`<h3>两个 POST JSON 接口</h3><p>Corki 主动拉取问题并回传结果，无需开放本机端口。接口返回 2xx 和下方 JSON。</p>
      ${examples.map(([label,value],i)=>`<div class="connector-code"><div class="connector-code-head"><span>${label}</span><button type="button" class="text-button" data-example-copy="${i}">复制</button></div><div data-example="${i}"></div></div>`).join('')}
      <h3>消息与回执</h3><p>id 是接入内唯一且稳定的消息标识；text 必填；sender 可选。每批最多 20 条，响应 messages 必须是数组；无消息返回空数组。next_cursor 可选，Corki 在整批入库后保存它。</p>
      <p>消息入库后按顺序执行。received 必须为 true，两个 ID 必须与请求一致，才算回复成功。接收端须按 reply_id 去重：即使重复收到，也只投递一次，并返回相同回执。回执丢失会重试相同回复，不重新执行 Agent。</p>
      <h3>等待、重试与恢复</h3><p>队列清空才继续拉取。空结果间隔 1 秒，连续 10 次后改为 5 秒；收到新消息恢复 1 秒。默认首次发送失败后再重试 3 次，间隔 1、2、4 秒，最高 5 秒；每次接口请求超时 15 秒。次数用尽后标记发送失败并保留回答，继续处理下一条。接入管理的对应回答会显示「回复失败」标签。</p>
      <p>type 为 notice 的请求是执行审批等中途通知，也需返回匹配回执，但不能当成最终回答。进程重启后，已保存的回复可继续发送；结果不明的 Agent 任务不会自动重跑，需要在管理页检查处理。</p>
      <h3>参数</h3><p>路径或查询参数可用 <code>#{account}</code> 占位，新增接入时填写值，按 URL 编码替换。不支持替换域名、端口或协议。勾选「禁用参数解析」后按普通字符发送；没有参数时自动创建一条关闭的对话接入。</p>`;
      article.querySelectorAll('[data-example]').forEach(node=>viewers.push(window.CorkiJsonViewer(node,examples[Number(node.dataset.example)][1])));
      article.querySelectorAll('[data-example-copy]').forEach(button=>button.onclick=async()=>{
        try {await navigator.clipboard.writeText(JSON.stringify(examples[Number(button.dataset.exampleCopy)][1],null,2));host.toast('已复制');}catch{host.toast('复制失败，请手动选择内容');}
      });
    };
  }
  function chooseChannel() {
    const options = data.channels.filter(c => !c.builtin || data.installed?.[c.id]);
    if (!options.length) { host.toast("请先安装一个渠道"); return; }
    const el = dialog("新增接入", `<div class="channel-field"><span>渠道</span>${selectField("channel",options.map(c => [c.id,c.name]),options[0].id)}</div>`, null);
    const next = document.createElement("button");
    next.className = "button primary"; next.type = "button"; next.textContent = "下一步";
    next.onclick = () => configure(null,el.querySelector('[name="channel"]').value);
    el.querySelector(".channel-dialog-foot").append(next); wireSelect(el);
  }
  function lockConnectionFields(el, locked) {
    el.querySelector('fieldset').disabled=false;
    el.querySelectorAll('input,textarea').forEach(input=>{
      if(input.type==='checkbox') input.disabled=locked;
      else input.readOnly=locked;
    });
    el.querySelectorAll('fieldset button:not(.channel-reveal)').forEach(b=>b.disabled=locked);
    el.querySelectorAll('details').forEach(d=>{d.inert=locked;if(locked)d.open=false;});
  }
  function configure(id, type) {
    const account = data.connections.find(c => c.id === id);
    const c = data.channels.find(c => c.id === (account?.channel || type));
    const values = account?.values || {session_validation:"true"}, secrets = account?.secrets || {};
    const providers=(host.providers || []).filter(p=>p.enabled!==false && Object.keys(p.models || {}).length);
    const providerItems=[["","默认"],...providers.map(p=>[p.id,p.id])];
    if(values.agent_provider && !providers.some(p=>p.id===values.agent_provider)) providerItems.push([values.agent_provider,values.agent_provider+'（不可用）']);
    const modelItems=(provider,model)=>{
      if(!provider)return [["","默认"]];
      const profile=providers.find(p=>p.id===provider);
      const items=Object.keys(profile?.models || {}).map(m=>[m,m]);
      if(model && !items.some(i=>i[0]===model))items.push([model,model+'（不可用）']);
      return items.length?items:[["","暂无可用模型"]];
    };
    const help = instructions[c.id] ?? (c.standard ? "通过标准 HTTP 协议接收问题并发送回复。" : "请仅使用可信来源的连接器。");
    const replies=[['validation_first_reply','拒绝文案','您暂无机器人使用权限，请联系管理员'],['validation_approved_reply','授权通知','管理员已为您开通权限，现在可以开始对话了'],['validation_disabled_reply','拒绝通知','管理员已经关闭了您的权限']];
    const body = `<div class="channel-connection-form">${help ? `<p class="channel-help">${esc(help)}</p>` : ""}
      <div class="channel-form-grid channel-identity-fields">
        <label class="channel-field channel-name-field"><span>接入名称</span><input name="_name" maxlength="60" required value="${esc(account?.name || "")}" placeholder="为此账号取个名字"></label>
        <div class="channel-field channel-session-mode"><span>会话模式</span>${selectField("session_mode",[["independent","用户独立会话"],["shared","用户共用会话"]],values.session_mode || "independent")}</div>
      </div>
      <div class="channel-form-grid channel-credential-fields" ${!c.fields.length && !c.standard ? 'hidden' : ''}>
        ${c.standard && c.fields.length ? `<label class="channel-check channel-full-field"><input type="checkbox" data-disable-parameters ${values.http_disable_parameters === "true" ? "checked" : ""}><span>禁用参数解析</span></label>` : ""}
        ${c.fields.map(f => `<label class="channel-field" ${c.standard ? 'data-parameter-field' : ''}><span>${esc(f.label)}</span>${f.options ? selectField(f.key,f.options.map(v => [v,v]),values[f.key]) : `<span class="channel-input-wrap"><input name="${esc(f.key)}" ${f.secret ? 'type="password" autocomplete="new-password"' : 'type="text"'} value="${esc(values[f.key] || "")}" placeholder="${f.secret && secrets[f.key] ? "••••••••" : esc(f.label)}" ${f.required && !secrets[f.key] ? "required" : ""}>${f.secret ? '<button type="button" class="channel-reveal">显示</button>' : ""}</span>`}</label>`).join("")}
        ${c.standard ? `<label class="channel-field"><span>回复失败重试次数</span><input name="http_retry_count" type="number" min="0" max="20" step="1" value="${esc(values.http_retry_count ?? c.retry_count ?? 3)}" required></label>` : ""}
      </div>
      <div class="channel-form-grid channel-runtime-fields">
        <div class="channel-field channel-agent-provider"><span>供应商</span>${selectField("agent_provider",providerItems,values.agent_provider || "")}</div>
        <div class="channel-field channel-agent-model"><span>模型</span>${selectField("agent_model",modelItems(values.agent_provider,values.agent_model),values.agent_model || "")}</div>
        <div class="channel-field channel-workdir-field channel-full-field"><span>工作目录</span><div class="channel-directory"><input name="workdir" aria-label="工作目录" value="${esc(values.workdir || "")}" placeholder="默认使用 ~/.corki/main/default"><button type="button" class="button" data-pick-workdir><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z"/></svg>选择目录</button></div></div>
      </div>
      <section class="channel-validation-section" aria-label="会话校验">
        <label class="channel-check channel-validation"><input type="checkbox" name="session_validation" aria-controls="channel-validation-replies" aria-expanded="${values.session_validation==='true'}" ${values.session_validation==='true'?'checked':''}><span><strong>会话校验</strong><small>仅允许已接入的用户对话</small></span></label>
        <div id="channel-validation-replies" class="channel-validation-replies" ${values.session_validation==='true'?'':'hidden'}>${replies.map(([key,label,fallback])=>`<label class="channel-field"><span>${label}</span><textarea name="${key}" rows="2" maxlength="2000" placeholder="${esc(fallback)}">${esc(values[key] || fallback)}</textarea></label>`).join('')}</div>
      </section>
      ${id ? '<button type="button" class="text-button channel-reset">清除登录信息</button>' : ""}</div>`;
    const submit = async (el, confirmReset = false) => {
      if(id && el.dataset.editing !== "true") return;
      const { _name, ...values } = Object.fromEntries(new FormData(el.querySelector("form")));
      el.querySelectorAll('[data-loaded-secret="true"]').forEach(input=>{
        if(input.value===input.dataset.originalSecret) delete values[input.name];
      });
      values.session_validation=el.querySelector('[name="session_validation"]').checked?'true':'false';
      if(c.standard) values.http_disable_parameters = el.querySelector('[data-disable-parameters]')?.checked ? "true" : "false";
      const oldDir = account?.values.workdir || "";
      if (id && values.workdir.trim() !== oldDir && !confirmReset) {
        dialog("修改工作目录", "<p>修改工作目录会重置当前会话，停止当前任务并清空待处理消息。旧会话记录会保留。</p>",
          async () => { await submit(el, true); }, "确认修改");
        return;
      }
      await change({action:"save-connection",...(id ? {id} : {}),channel:c.id,name:_name,values,
        confirm_reset:confirmReset},"正在保存接入…",true);
      if (id) { root.innerHTML = ""; historyState = null; draw(); void refreshHistory(); }
    };
    let el;
    if (!id) {
      el = dialog(`接入 ${c.name}`,body,submit,'创建接入');
      el.classList.add('channel-connect-dialog');
      el.querySelector('.channel-dialog-head h2').insertAdjacentHTML('afterbegin',avatar(c.id,c.builtin));
    }
    else {
      applicationRows=new Map();applicationBefore=null;applicationRevision++;
      selectedThread=null;sessionMode=null;sessionItems=new Map();sessionsBefore=undefined;threadGeneration++;historyLoading=false;
      root.innerHTML = `<div class="channel-management">
        <div class="channel-detail-toolbar"><div class="channel-detail-tabs" role="tablist" aria-label="接入管理">
          <button type="button" role="tab" id="channel-tab-history" aria-controls="channel-pane-history" data-channel-tab="history">会话记录</button><button type="button" role="tab" id="channel-tab-settings" aria-controls="channel-pane-settings" data-channel-tab="settings">会话设置</button><button type="button" role="tab" id="channel-tab-applications" aria-controls="channel-pane-applications" data-channel-tab="applications" ${values.session_validation==='true'?'':'hidden'}>会话申请</button></div>
          <div class="channel-history-actions"><span class="subtle" data-chat-state>只读</span><button class="button" data-reset-conversation>重置全部会话</button></div></div>
        <div class="channel-queue-alert" hidden></div><div class="channel-manage-grid"><section class="panel channel-chat-panel" role="tabpanel" id="channel-pane-history" aria-labelledby="channel-tab-history">
          <div class="channel-chat-layout"><nav class="channel-session-pane" aria-label="接入会话"><div class="channel-session-list"></div><button type="button" class="text-button" data-more-sessions hidden>加载更多会话</button></nav><div class="channel-transcript"><div class="channel-chat-scroll"><button class="text-button channel-more" hidden>加载更早的消息</button><div class="channel-chat-messages"></div><div class="channel-chat-empty">正在读取会话…</div></div>
          <p class="channel-history-error" role="status"></p></div></div></section>
        <section class="panel channel-account-settings" role="tabpanel" id="channel-pane-settings" aria-labelledby="channel-tab-settings"><form><div class="panel-head"><h2>基本信息</h2><button type="button" class="button" data-page-edit>编辑</button></div><div class="channel-settings-scroll"><fieldset class="channel-basic-fields" disabled>${body}</fieldset></div><p class="channel-form-error" role="alert"></p><div class="channel-settings-foot" hidden><button class="button" type="button" data-page-cancel>取消</button><button class="button primary" type="submit" data-page-save>保存</button></div></form></section><section class="panel channel-applications" role="tabpanel" id="channel-pane-applications" aria-labelledby="channel-tab-applications" hidden><div class="channel-application-filters"><label class="channel-application-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg><input type="search" aria-label="搜索用户名称" placeholder="搜索用户名称" maxlength="80" value="${esc(applicationName)}"></label><div class="channel-application-status">${selectField('application_status',[['','全部状态'],['pending','审批中'],['approved','已接入'],['disabled','已禁用']],applicationStatus)}</div></div><div class="channel-applications-content" aria-live="polite"></div><button type="button" class="text-button" data-more-applications hidden>加载更多</button><p class="channel-applications-error" role="alert"></p></section></div></div>`;
      el = root.querySelector('.channel-account-settings');
      const showTab=tab=>{
        requestTipClose();
        closeSelect();
        managementTab=tab;
        rememberTab(id,tab);
        root.querySelectorAll('[data-channel-tab]').forEach(button=>{
          const active=button.dataset.channelTab===tab;
          button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
        });
        root.querySelector('.channel-chat-panel').hidden=tab!=='history';
        el.hidden=tab!=='settings';
        root.querySelector('.channel-applications').hidden=tab!=='applications';
        root.querySelector('.channel-history-actions').hidden=tab!=='history';
        if(tab==='history')void refreshHistory();
        if(tab==='applications')void refreshApplications();
      };
      root.querySelectorAll('[data-channel-tab]').forEach(button=>{
        button.onclick=()=>showTab(button.dataset.channelTab);
        button.onkeydown=event=>{
          if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
          event.preventDefault();
          const tabs=[...root.querySelectorAll('[data-channel-tab]')].filter(b=>!b.hidden).map(b=>b.dataset.channelTab);
          const index=tabs.indexOf(managementTab);
          const tab=event.key==='Home'?tabs[0]:event.key==='End'?tabs.at(-1):tabs[(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length];
          showTab(tab);root.querySelector(`[data-channel-tab="${tab}"]`).focus();
        };
      });
      root._showChannelTab=showTab;
      const filters=root.querySelector('.channel-application-filters');
      filters.querySelector('input[type="search"]').oninput=()=>filterApplications(true);
      filters.querySelector('input[type="search"]').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();filterApplications(false);}};
      wireSelect(filters,()=>filterApplications(false));
      root.querySelector('[data-more-applications]').onclick=()=>refreshApplications(true);
      if(managementTab==='applications' && values.session_validation!=='true')managementTab='settings';
      showTab(managementTab);

      const setEditing = enabled => {
        el.dataset.editing=String(enabled);
        el._editRevision=(el._editRevision || 0)+1;
        lockConnectionFields(el,!enabled);
        el.querySelector('[data-page-edit]').hidden=enabled;
        el.querySelector('.channel-settings-foot').hidden=!enabled;
        el.querySelectorAll('details').forEach(d=>{d.inert=!enabled;d.open=false;});
        el.querySelector('[role=alert]').textContent='';
        if(enabled)el.querySelector('.channel-settings-scroll').scrollTop=0;
      };
      setEditing(false);
      el.querySelector('[data-page-edit]').onclick=()=>setEditing(true);
      el.querySelector('[data-page-cancel]').onclick=()=>{
        el.querySelector('form').reset();
        el.querySelector('[name="session_validation"]').onchange();
        replaceModels(values.agent_provider || '',values.agent_model || '');
        el.querySelectorAll('.channel-select').forEach(d=>{
          const input=d.querySelector('input');
          input.value=initialSelections.get(input.name) ?? '';
          const value=input.value;
          const option=[...d.querySelectorAll('[data-option]')].find(b=>b.dataset.option===value);
          if(option)d.querySelector('summary').firstChild.textContent=option.textContent;
        });
        el.querySelectorAll('.channel-reveal').forEach(b=>{b.previousElementSibling.type='password';b.textContent='显示';});
        el.querySelector('[data-disable-parameters]')?.onchange?.();
        setEditing(false);
      };
      el.querySelector('form').onsubmit = async event => {
        event.preventDefault();
        try { await submit(el); } catch(error) { el.querySelector('[role=alert]').textContent = error.message; }
      };
      root.querySelector('[data-reset-conversation]').onclick = () => dialog("重置全部会话",
        "<p>将停止此接入的所有任务、清空待处理消息，并重置全部用户会话。旧会话记录会保留。</p>",async () => {
          await change({action:"reset-conversation",id},"正在重置会话…",true);
          historyState=null;selectedThread=null;sessionItems.clear();sessionsBefore=undefined;
          threadGeneration++;historyLoading=false;await refreshHistory();
        },"重置会话");
      root.querySelector('.channel-more').onclick = () => refreshHistory(true);
      root.querySelector('[data-more-sessions]').onclick = () => refreshHistory(false,true);
      void refreshHistory();
    }
    el.querySelectorAll('.channel-session-mode [data-option]').forEach(button=>{
      const row=document.createElement('div');row.className='channel-mode-option';
      button.replaceWith(row);row.append(button);
      const help=document.createElement('span');help.className='channel-mode-help';help.tabIndex=0;
      const description=button.dataset.option==='independent' ? '每位用户保留独立的聊天上下文，工作目录仍共用。' : '所有用户共用默认会话，按顺序执行，回答可能引用其他用户的历史。';
      help.setAttribute('aria-label',description);help.innerHTML='?<span role="tooltip">'+esc(description)+'</span>';row.append(help);
    });
    const validation=el.querySelector('[name="session_validation"]');
    validation.onchange=()=>{el.querySelector('.channel-validation-replies').hidden=!validation.checked;validation.setAttribute('aria-expanded',String(validation.checked));};
    const initialSelections=new Map([...el.querySelectorAll('.channel-select input')].map(input=>[input.name,input.value]));
    const replaceModels=(provider,model)=>{
      const field=el.querySelector('.channel-agent-model');
      field.innerHTML='<span>模型</span>'+selectField('agent_model',modelItems(provider,model),model);
      wireSelect(field);
      if(id && el.dataset.editing!=='true')lockConnectionFields(el,true);
    };
    wireSelect(el,input=>{
      if(input.name!=='agent_provider')return;
      const profile=providers.find(p=>p.id===input.value);
      const names=Object.keys(profile?.models || {});
      replaceModels(input.value,names.includes(profile?.default_model)?profile.default_model:(names[0] || ''));
    });
    wireRetryCount(el);
    el.querySelector('[data-pick-workdir]').onclick = async event => {
      const button=event.currentTarget, api=host.api, generation=epoch, editRevision=el._editRevision;
      button.disabled=true;
      try {
        const result=await api('mutate',{kind:'quick-tools',action:'choose-directory'});
        if(generation===epoch && el.isConnected && editRevision===el._editRevision && result.path) {
          const input=el.querySelector('[name="workdir"]');
          input.value=result.path;
          input.dispatchEvent(new Event('input',{bubbles:true}));
        }
      } catch(error) {
        if(generation===epoch && el.isConnected && editRevision===el._editRevision) el.querySelector('[role="alert"]').textContent=error.message;
      } finally { button.disabled=false; }
    };
    const disableParams = el.querySelector('[data-disable-parameters]');
    if(disableParams) {
      const sync = () => el.querySelectorAll('[data-parameter-field]').forEach(label => {
        label.hidden = disableParams.checked;
        const input=label.querySelector('input');
        input.disabled = disableParams.checked;
        input.required = !disableParams.checked && !secrets[input.name];
      });
      disableParams.onchange=sync; sync();
    }
    el.querySelectorAll(".channel-reveal").forEach(b => b.onclick = async () => {
      const input=b.previousElementSibling;
      if(input.type==='text') {
        input.type='password';b.textContent='显示';
        if(input.dataset.loadedSecret==='true' && input.value===input.dataset.originalSecret) input.value='';
        delete input.dataset.loadedSecret;delete input.dataset.originalSecret;
        return;
      }
      const generation=epoch;
      b.disabled=true;
      try {
        if(id && secrets[input.name] && !input.value) {
          const result=await host.api('mutate',{kind:'channel-gateway',action:'reveal-secret',id,field:input.name});
          if(generation!==epoch || !el.isConnected)return;
          // Never overwrite a replacement entered while the request was pending.
          if(!input.value) {
            input.value=result.value;input.dataset.loadedSecret='true';
            input.dataset.originalSecret=result.value;
          }
        }
        input.type='text';b.textContent='隐藏';
      } catch(error) {
        if(generation===epoch && el.isConnected) el.querySelector('[role=alert]').textContent=error.message;
      } finally {b.disabled=false;}
    });
    if(id) el.querySelector(".channel-reset").onclick = () => dialog("清除登录信息",
      "<p>此接入将关闭，清除密钥和登录状态，其他账号不受影响。</p>",
      async () => {
        await change({action:"reset",id},"正在清除…",true);
        root.innerHTML = ""; historyState = null; draw();
      },"确认清除");
  }
  function requestMessage(element) {
    if(!element._messageRequest) {
      element._messageRequest=host.api('mutate',{kind:'channel-gateway',action:'application-message',id:selected,sender:element.dataset.requestMessage})
        .catch(error=>{element._messageRequest=null;throw error;});
    }
    return element._messageRequest;
  }
  function showRequestTooltip(element) {
    requestTipClose();
    const tip=document.createElement('div'),events=new AbortController();
    let hideTimer;
    tip.className='channel-request-tooltip';tip.id='channel-request-tooltip';
    tip.setAttribute('popover','manual');tip.setAttribute('role','tooltip');
    tip.textContent=element.dataset.requestId ?? '正在读取请求消息…';
    document.body.append(tip);tip.showPopover();
    element.setAttribute('aria-describedby',tip.id);
    const position=()=>{
      const r=element.getBoundingClientRect(),t=tip.getBoundingClientRect();
      tip.style.left=Math.max(12,Math.min(r.left,innerWidth-t.width-12))+'px';
      tip.style.top=Math.max(12,r.bottom+8+t.height<=innerHeight-12?r.bottom+8:r.top-t.height-8)+'px';
    };
    const close=()=>{
      clearTimeout(hideTimer);events.abort();tip.remove();element.removeAttribute('aria-describedby');
      requestTipClose=()=>{};
    };
    requestTipClose=close;
    const deferClose=()=>{clearTimeout(hideTimer);hideTimer=setTimeout(close,150);};
    const keep=()=>clearTimeout(hideTimer);
    element.addEventListener('pointerleave',deferClose,{signal:events.signal});
    element.addEventListener('blur',deferClose,{signal:events.signal});
    tip.addEventListener('pointerenter',keep,{signal:events.signal});
    tip.addEventListener('pointerleave',deferClose,{signal:events.signal});
    document.addEventListener('keydown',event=>{if(event.key==='Escape')close();},{signal:events.signal});
    document.addEventListener('pointerdown',event=>{if(!tip.contains(event.target) && !element.contains(event.target))close();},{signal:events.signal});
    document.addEventListener('scroll',event=>{if(!tip.contains(event.target))close();},{capture:true,signal:events.signal});
    window.addEventListener('resize',close,{signal:events.signal});
    position();
    if(element.hasAttribute('data-request-message'))requestMessage(element).then(message=>{
      if(!tip.isConnected || !element.isConnected)return;
      tip.textContent=message.text;position();
    }).catch(()=>{if(tip.isConnected){tip.textContent='读取失败，点击请求消息可重试';position();}});
  }
  function applicationActions(row) {
    const pending=applicationPending.get(row.sender_id);
    if(pending)return `<span class="channel-request-progress ${pending==='接入中'?'admit':'remove'}" role="status" aria-live="polite" aria-busy="true"><span class="spinner" aria-hidden="true"></span>${pending}</span>`;
    return `<button type="button" class="channel-request-action ${row.status==='approved'?'remove':'admit'}" data-request-action="${esc(row.sender_id)}">${row.status==='approved'?'踢出':'接入'}</button>${row.status==='pending'?`<button type="button" class="channel-request-action remove" data-request-deny="${esc(row.sender_id)}">拒绝</button>`:''}`;
  }
  function renderApplications() {
    const pane=root?.querySelector('.channel-applications-content');if(!pane)return;
    const rows=[...applicationRows.values()].sort((a,b)=>b.cursor-a.cursor);
    const labels={pending:'审批中',approved:'已接入',disabled:'已禁用'};
    const html=rows.length?`<div class="table-wrap"><table class="channel-application-table"><thead><tr><th>用户 ID</th><th>用户名称</th><th>请求消息</th><th>请求时间</th><th>状态</th><th>备注</th><th>操作</th></tr></thead><tbody>${rows.map(r=>`<tr><td><code class="channel-request-id" tabindex="0" data-request-id="${esc(r.sender_id)}">${esc(r.sender_id)}</code></td><td><strong>${esc(r.sender_name || r.sender_id)}</strong></td><td><button class="channel-request-preview" data-request-message="${esc(r.sender_id)}">${esc(r.first_message)}</button></td><td class="channel-request-time">${esc(date(r.created_at))}</td><td><span class="channel-request-status ${esc(r.status)}">${labels[r.status] || '审批中'}</span></td><td>${r.notification_status==='failed'?`<button type="button" class="channel-notice-failed" data-notice-error="${esc(r.sender_id)}">通知未送达</button>`:'<span class="subtle">—</span>'}</td><td data-request-actions="${esc(r.sender_id)}">${applicationActions(r)}</td></tr>`).join('')}</tbody></table></div>`:`<div class="channel-empty">${applicationName || applicationStatus?'没有符合条件的申请':'暂无会话申请'}</div>`;
    if(pane._html!==html){
      const scroll=pane.querySelector('.table-wrap'),top=scroll?.scrollTop || 0,left=scroll?.scrollLeft || 0;
      requestTipClose();pane.innerHTML=html;pane._html=html;
      const next=pane.querySelector('.table-wrap');if(next){next.scrollTop=top;next.scrollLeft=left;}
    }
    pane.querySelectorAll('[data-notice-error]').forEach(button=>button.onclick=()=>{
      requestTipClose();
      const row=applicationRows.get(button.dataset.noticeError);if(!row)return;
      const kinds={pending:'拒绝文案',approved:'授权通知',disabled:'拒绝通知'};
      const el=dialog('通知未送达',`<dl class="channel-request-detail"><dt>通知类型</dt><dd>${esc(kinds[row.notification_kind] || '权限通知')}</dd><dt>失败时间</dt><dd>${esc(row.notification_time?date(row.notification_time):'—')}</dd></dl><div class="channel-response-title">失败日志</div><div class="channel-response-body"><pre>${esc(row.notification_error || '未记录详细失败原因')}</pre></div>`,null);
      el.classList.add('channel-diagnostics');
      el.querySelector('.channel-dialog-foot [data-close]').textContent='关闭';
    });
    pane.querySelectorAll('[data-request-id],[data-request-message]').forEach(element=>{
      const show=()=>showRequestTooltip(element);
      element.onpointerenter=show;element.onfocus=show;
    });
    pane.querySelectorAll('[data-request-message]').forEach(button=>button.onclick=async()=>{
      requestTipClose();
      const row=applicationRows.get(button.dataset.requestMessage);if(!row)return;
      const generation=epoch;button.disabled=true;
      try {
        const message=await requestMessage(button);
        if(generation!==epoch)return;
        dialog('请求消息',`<div class="channel-request-message">${esc(message.text)}</div>`,null);
        modal.el.querySelector('[data-close]').setAttribute('aria-label','关闭');
        modal.el.querySelector('.channel-dialog-foot [data-close]').textContent='关闭';
      }catch(error){if(generation===epoch)host.toast(error.message);}
      finally{button.disabled=false;}
    });
    pane.querySelectorAll('[data-request-action],[data-request-deny]').forEach(button=>button.onclick=async()=>{
      const sender=button.dataset.requestAction ?? button.dataset.requestDeny,row=applicationRows.get(sender);
      if(!row || applicationPending.has(sender))return;
      const status=button.hasAttribute('data-request-deny') || row.status==='approved'?'disabled':'approved',generation=epoch,identity=selected;
      applicationPending.set(sender,status==='approved'?'接入中':button.hasAttribute('data-request-deny')?'拒绝中':'踢出中');
      applicationRevision++;requestTipClose();
      // Update only this action cell; keep selection, scrolling and other controls usable.
      button.closest('[data-request-actions]').innerHTML=applicationActions(row);
      pane._html=null;
      try {
        const result=await host.api('mutate',{kind:'channel-gateway',action:'application-status',id:identity,sender,status});
        if(generation!==epoch || identity!==selected)return;
        data=result;const current=applicationRows.get(sender);if(current){current.status=status;current.notification_status='queued';current.notification_error='';if(applicationStatus && status!==applicationStatus)applicationRows.delete(sender);}applicationRevision++;
        host.toast(status==='approved'?'已接入':button.hasAttribute('data-request-deny')?'已拒绝':'已禁用');
      } catch(error){if(generation===epoch)host.toast(error.message);}
      finally {if(generation===epoch){applicationPending.delete(sender);renderApplications();}}
    });
    root.querySelector('[data-more-applications]').hidden=applicationBefore==null;
  }
  function filterApplications(debounce) {
    const filters=root.querySelector('.channel-application-filters');
    applicationName=filters.querySelector('input[type="search"]').value.trim();
    applicationStatus=filters.querySelector('[name="application_status"]').value;
    clearTimeout(applicationFilterTimer);requestTipClose();applicationRevision++;
    applicationRows=new Map();applicationBefore=null;applicationLoading=false;
    root.querySelector('[data-more-applications]').hidden=true;
    const pane=root.querySelector('.channel-applications-content');pane._html=null;
    pane.innerHTML='<div class="channel-empty">正在读取申请…</div>';
    if(debounce)applicationFilterTimer=setTimeout(()=>{applicationFilterTimer=null;void refreshApplications();},250);
    else {applicationFilterTimer=null;void refreshApplications();}
  }
  async function refreshApplications(more=false) {
    if(!root || !selected || managementTab!=='applications' || applicationFilterTimer || applicationLoading || busy)return;
    const generation=epoch,identity=selected,version=applicationRevision;
    const flight={};applicationLoading=flight;
    if(!applicationRows.size){const pane=root.querySelector('.channel-applications-content');pane._html=null;pane.innerHTML='<div class="channel-empty">正在读取申请…</div>';}
    try {
      const result=await host.api('mutate',{kind:'channel-gateway',action:'applications',id:identity,name:applicationName,status:applicationStatus,...(more&&applicationBefore!=null?{before:applicationBefore}:{})});
      if(generation!==epoch || identity!==selected || version!==applicationRevision)return;
      if(!more && applicationRows.size<=100){applicationRows=new Map();applicationBefore=null;}
      for(const row of result.requests)applicationRows.set(row.sender_id,row);
      if(more || applicationRows.size<=100)applicationBefore=result.before;
      renderApplications();root.querySelector('.channel-applications-error').textContent='';
    }catch(error){if(generation===epoch && version===applicationRevision)root.querySelector('.channel-applications-error').textContent=error.message;}
    finally{if(generation===epoch && applicationLoading===flight)applicationLoading=false;}
  }
  function renderSessions() {
    const list=root?.querySelector('.channel-session-list');if(!list)return;
    const rows=[...sessionItems.values()];
    const html=rows.map(row=>`<button type="button" class="channel-session-item ${selectedThread===row.thread?'selected':''}" data-session="${esc(row.thread)}" title="${esc(row.name)}${row.sender?' · '+esc(row.sender):''}"><span class="channel-session-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5A8.5 8.5 0 0 1 10.5 3h2A8.5 8.5 0 0 1 21 11.5Z"/></svg></span><span>${esc(row.name)}</span></button>`).join('') || '<div class="channel-sessions-empty">暂无会话</div>';
    if(list._html!==html) {
      list.innerHTML=html;list._html=html;
      list.querySelectorAll('[data-session]').forEach(button=>button.onclick=()=>{
        if(selectedThread===button.dataset.session)return;
        selectedThread=button.dataset.session;threadGeneration++;historyLoading=false;historyState=null;
        root.querySelector('.channel-chat-messages').replaceChildren();
        const empty=root.querySelector('.channel-chat-empty');empty.hidden=false;empty.textContent='正在读取会话…';
        renderSessions();void refreshHistory();
      });
    }
    root.querySelector('[data-more-sessions]').hidden=sessionsBefore==null;
  }
  async function refreshHistory(older = false, moreSessions = false) {
    if (!selected || !root || historyLoading || busy || managementTab!=="history") return;
    const mode=data.connections.find(c=>c.id===selected)?.values.session_mode || 'independent';
    if(sessionMode!==null && sessionMode!==mode) {
      selectedThread=null;sessionItems.clear();sessionsBefore=undefined;historyState=null;threadGeneration++;
    }
    sessionMode=mode;
    const current = epoch, id = selected, version = revision, view=threadGeneration;
    const before = older ? historyState?.before : null;
    if (older && before == null) return;
    historyLoading = true;
    try {
      const value = await host.api("mutate", {kind:"channel-gateway",action:"conversation",id,
        ...(selectedThread ? {session:selectedThread} : {}),
        ...(moreSessions && sessionsBefore!=null ? {sessions_before:sessionsBefore} : {}),
        ...(older ? {before,thread:historyState.thread} : {})});
      if (current !== epoch || version !== revision || id !== selected || view!==threadGeneration || managementTab!=="history") return;
      if(sessionMode!==(value.mode || mode)) sessionItems.clear();
      sessionMode=value.mode || mode;
      selectedThread=value.thread;
      const newSessions=(value.sessions || []).some(session=>!sessionItems.has(session.thread));
      for(const session of value.sessions || []) sessionItems.set(session.thread,session);
      if(moreSessions || sessionsBefore===undefined || newSessions) sessionsBefore=value.sessions_before;
      renderSessions();
      const fresh = !historyState || historyState.thread !== value.thread;
      if (fresh) historyState = {thread:value.thread, mode:value.mode || mode, messages:new Map(), before:value.before};
      if (older || fresh || historyState.before == null) historyState.before = value.before;
      for (const message of value.messages) historyState.messages.set(message.id,message);
      const scroll = root.querySelector('.channel-chat-scroll'), list = root.querySelector('.channel-chat-messages');
      if (!scroll || !list) return;
      const follow = fresh || scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
      const height = scroll.scrollHeight, top = scroll.scrollTop;
      if (fresh) list.replaceChildren();
      const rows = [...historyState.messages.values()].sort((a,b)=>a.created_at.localeCompare(b.created_at));
      const nodes = new Map([...list.children].map(n=>[n.dataset.id,n]));
      for (const [index,row] of rows.entries()) {
        let node = nodes.get(row.id);
        if (!node) { node = document.createElement('article'); node.dataset.id=row.id; node.className='channel-chat-message '+row.role; }
        if (node._content !== row.content) {
          node.innerHTML = `<img class="channel-chat-avatar" src="${row.role === 'user' ? '/chat-crab.jpg' : '/corki-icon-ears.png'}" alt=""><div class="channel-chat-content"><div class="channel-chat-text">${window.CorkiChannelMarkdown ? window.CorkiChannelMarkdown(row.content) : esc(row.content)}</div><div class="channel-chat-meta"><time>${esc(new Date(row.created_at).toLocaleString('sv-SE').slice(0,16))}</time><span class="channel-reply-failed" hidden>回复失败</span></div></div>`;
          node._content = row.content;
        }
        const failure = node.querySelector('.channel-reply-failed');
        failure.hidden = row.role !== 'assistant' || !row.delivery_failed;
        // Delivery status can change without a text delta; preserve the message DOM.
        // Keep stable nodes while polling; do not recreate the full transcript.
        if (list.children[index] !== node) list.insertBefore(node,list.children[index] || null);
      }
      root.querySelector('.channel-chat-empty').hidden = !!rows.length;
      root.querySelector('.channel-chat-empty').textContent = '暂无会话消息';
      root.querySelector('[data-chat-state]').textContent = value.active ? '正在回复 · 只读' : '只读';
      root.querySelector('.channel-more').hidden = historyState.before == null;
      root.querySelector('.channel-history-error').textContent = '';
      if (older) scroll.scrollTop = top + scroll.scrollHeight - height;
      else if (follow) scroll.scrollTop = scroll.scrollHeight;
    } catch(error) {
      if (current === epoch && view===threadGeneration) { const box = root?.querySelector('.channel-history-error'); if(box)box.textContent=error.message; }
    } finally { if(current===epoch && view===threadGeneration)historyLoading=false; }
  }
  function login(id) {
    const el = dialog(
      `${channelName(id)} · 登录`,
      '<div class="channel-login"></div>',
      null,
    );
    modal.login = id;
    updateLogin();
  }
  function updateLogin() {
    if (!modal?.login) return;
    const box = modal.el.querySelector(".channel-login"),
      value = data.statuses[modal.login];
    if (!box) return;
    if (value?.state === "online") {
      box.innerHTML =
        '<div class="channel-login-success">✓<strong>连接成功</strong></div>';
      return;
    }
    if (value?.state === "verification") {
      if (box.querySelector("input")) return;
      box.innerHTML =
        '<p>输入手机上显示的验证码</p><input inputmode="numeric" aria-label="手机验证码" maxlength="12"><button type="button" class="button primary">确认</button>';
      box.querySelector("button").onclick = () =>
        change({
          action: "verification",
          id: modal.login,
          code: box.querySelector("input").value,
        });
      return;
    }
    if (value?.qr) {
      const old = box.querySelector("img");
      if (old?.src === value.qr) return;
      box.innerHTML = `<img width="260" height="260" alt="登录二维码" src="${esc(value.qr)}"><p>使用手机扫码并确认</p>`;
    } else box.textContent = value?.message || "正在等待连接…";
  }
  function importDialog() {
    let encoded = "", readVersion = 0;
    const el = dialog(
      "安装连接器",
      `<div data-import-pane><label class="channel-upload"><span>${glyph}</span><strong>上传或拖入连接器 ZIP</strong><input type="file" accept=".zip" hidden></label><p class="channel-help">ZIP 需包含 connector.json 和可运行的入口程序。</p><div class="channel-import-guide"><div><strong>第一次制作连接器？</strong><span>查看目录结构、协议说明和可运行示例。</span></div><button type="button" class="button" data-open-guide>格式指引</button></div></div><div data-guide-pane hidden></div>`,
      async () => {
        if (!encoded) throw new Error("请选择 ZIP 文件");
        await change(
          { action: "install", zip: encoded },
          "正在安装连接器…",
          true,
        );
      },
      "安装",
    );
    let guide;
    const body = el.querySelector(".channel-dialog-body");
    const restore = () => {
      guide?.destroy(); guide = null;
      el.classList.remove("channel-guide-open");
      el.querySelector("[data-guide-pane]").hidden = true;
      el.querySelector("[data-guide-pane]").replaceChildren();
      el.querySelector("[data-import-pane]").hidden = false;
      el.querySelector(".channel-dialog-head h2").textContent = "安装连接器";
      body.scrollTop = 0;
      el.querySelector("[data-open-guide]").focus();
    };
    el.querySelector("[data-open-guide]").onclick = () => {
      el.classList.add("channel-guide-open");
      el.querySelector("[data-import-pane]").hidden = true;
      const pane = el.querySelector("[data-guide-pane]"); pane.hidden = false;
      el.querySelector(".channel-dialog-head h2").textContent = "连接器格式指引";
      guide = window.CorkiConnectorGuide.mount(pane,{back:restore,toast:host.toast});
      pane.querySelector("[data-guide-back]").focus();
    };
    modal.dispose = () => guide?.destroy();
    const upload = el.querySelector(".channel-upload"),
      input = upload.querySelector("input");
    async function read(file) {
      if (!file) return;
      const version = ++readVersion;
      encoded = "";
      upload.querySelector("strong").textContent = "上传或拖入连接器 ZIP";
      el.querySelector("[role=alert]").textContent = "";
      if (
        !file.name.toLowerCase().endsWith(".zip") ||
        file.size > 16 * 1024 * 1024
      ) {
        el.querySelector("[role=alert]").textContent =
          "请选择不超过 16 MB 的 ZIP 文件";
        return;
      }
      try {
        const value = await new Promise((resolve, reject) => {
          const r = new FileReader();
          r.onload = () => resolve(r.result.split(",")[1]);
          r.onerror = () => reject(new Error("文件读取失败，请重新选择"));
          r.readAsDataURL(file);
        });
        if (version !== readVersion || !el.isConnected) return;
        encoded = value;
        upload.querySelector("strong").textContent = file.name;
      } catch (error) {
        if (version === readVersion) el.querySelector("[role=alert]").textContent = error.message;
      }
    }
    input.onchange = () => read(input.files[0]);
    upload.ondragover = (e) => {
      e.preventDefault();
      upload.classList.add("dragging");
    };
    upload.ondragleave = () => upload.classList.remove("dragging");
    upload.ondrop = (e) => {
      e.preventDefault();
      upload.classList.remove("dragging");
      void read(e.dataTransfer.files[0]);
    };
  }
  function interaction(id) {
    const i = data.interactions.find((i) => i.request_id === id);
    if (!i) return;
    if (i.kind === "question") {
      dialog(
        "补充信息",
        i.questions
          .map(
            (q) =>
              `<label class="channel-field"><span>${esc(q.question)}</span><input name="${esc(q.id)}" required placeholder="${esc((q.options || []).map((o) => o.label).join(" / "))}"></label>`,
          )
          .join(""),
        async (el) => {
          const answers = {};
          for (const [key, value] of new FormData(el.querySelector("form")))
            answers[key] = { answers: [value] };
          await change(
            { action: "interaction", request_id: id, response: { answers } },
            "正在提交…",
            true,
          );
        },
        "发送",
      );
    } else {
      const el = dialog(
        "执行审批",
        `<pre class="channel-approval">${esc(JSON.stringify(i.params, null, 2))}</pre>`,
        () =>
          change(
            { action: "interaction", request_id: id, decision: "accept" },
            "正在批准…",
            true,
          ),
        "批准一次",
      );
      // Form/URL elicitation cannot be accepted with fabricated empty data.
      if (
        !["shell_approval", "patch_approval", "tool_approval"].includes(i.kind)
      )
        el.querySelector("[type=submit]").hidden = true;
      const decline = document.createElement("button");
      decline.className = "button";
      decline.type = "button";
      decline.textContent = "拒绝";
      decline.onclick = async () => {
        await change({
          action: "interaction",
          request_id: id,
          decision: "decline",
        });
        closeDialog();
      };
      el.querySelector(".channel-dialog-foot").prepend(decline);
    }
  }
  function unmount() {
    clearTimeout(applicationFilterTimer);applicationFilterTimer=null;
    requestTipClose();
    epoch++;
    selected = null; historyState = null; historyLoading = false;
    selectedThread=null;sessionMode=null;sessionItems=new Map();sessionsBefore=undefined;threadGeneration++;
    clearInterval(timer);
    closeDialog();
    root = null;
    host = null;
    data = null;
    busy = false;
    loading = false;
    packagePending = new Map();
    seenJobs = new Map();
    revision++;
  }
  function mount(element, context) {
    unmount();
    root = element;
    host = context;
    applicationRows=new Map();applicationBefore=null;applicationLoading=false;
    applicationRevision++;applicationPending=new Map();applicationName="";applicationStatus="";
    selected = context.id || null;
    managementTab = restoreTab(selected);
    window.scrollTo(0,0);
    root.innerHTML =
      '<section class="panel channel-empty"><span class="spinner"></span>正在读取网关状态…</section>';
    void refresh();
    timer = setInterval(() => {
      if (!document.hidden) void refresh();
    }, 5000);
  }
  return { mount, unmount };
})();
