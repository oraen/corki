// Presentation for the independent aiohttp gateway. No model execution lives here.
const icon = (path) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
const network = icon('<circle cx="5" cy="12" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="m7 11 10-4M7 13l10 4"/>');
const copy = icon('<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>');
const refresh = icon('<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/>');

export class ApplicationGatewayView {
  constructor(api) {
    this.api = api;
    this.state = null;
    this.generation = 0;
  }
  mount(root, toast) {
    const first = !this.root;
    this.root = root;
    this.toast = toast;
    this.render();
    if (first) this.reload().catch(error => this.toast(error.message));
  }
  unmount() {
    this.generation++;
    clearTimeout(this.timer);
    this.root = null;
    this.dialog?.close();
    this.dialog?.remove();
    this.dialog = null;
  }
  async reload() {
    const generation = this.generation;
    const state = await this.api("gateway-status");
    if (generation !== this.generation) return;
    this.state = state;
    this.render();
    clearTimeout(this.timer);
    if (["starting", "stopping"].includes(state.state) && this.root) {
      this.timer = setTimeout(() => this.reload().catch(error => this.toast(error.message)), 500);
    }
  }
  render() {
    if (!this.root) return;
    const state = this.state;
    const busy = !state || this.changing || ["starting", "stopping"].includes(state.state);
    const names = {starting:"开启中", stopping:"关闭中", running:"运行中", stopped:"已关闭", failed:"操作失败"};
    const port = !busy && state?.running ? state.port : null;
    const tooltip = port ? `http://127.0.0.1:${port}/app?appid=#{appid}` : "";
    this.root.innerHTML = `<div class="application-gateway-summary"><span class="application-gateway-mark">${network}</span><div class="application-gateway-title"><strong>应用网关</strong>${port ? `<span class="application-gateway-port-info"><span class="application-gateway-port-number">${port}</span><button type="button" class="provider-group-help application-gateway-help" aria-label="访问路径：${tooltip}" data-tooltip="${tooltip}">?</button></span>` : ""}<span class="application-gateway-state ${state?.state === "failed" ? "failed" : state?.running ? "running" : ""}">${busy ? '<i class="spinner"></i>' : '<i></i>'}${names[state?.state] || "加载中"}</span></div></div><div class="application-gateway-controls"><button class="icon-button" data-application-gateway="refresh" aria-label="刷新应用网关状态" ${busy ? "disabled" : ""}>${refresh}</button><button class="button" data-application-gateway="settings" ${busy ? "disabled" : ""}>接入设置</button><button class="toggle${state?.running ? " on" : ""}" role="switch" aria-label="启用应用网关" aria-checked="${!!state?.running}" data-application-gateway="toggle" ${busy ? "disabled" : ""}></button></div>`;
    if (state?.state === "failed" && state.error) {
      const button = document.createElement("button");
      button.className = "text-button application-gateway-error";
      button.dataset.applicationGateway = "error";
      button.textContent = "查看原因";
      this.root.querySelector(".application-gateway-title").append(button);
    }
  }
  async change(values) {
    if (this.changing) return;
    this.changing = true;
    this.render();
    try {
      this.state = await this.api("gateway-configure", values, true);
      if (this.root) {
        this.dialog?.close();
        this.dialog?.remove();
        this.dialog = null;
        await this.reload();
      }
    } finally {
      this.changing = false;
      this.render();
    }
  }
  async click(event) {
    const button = event.target.closest("[data-application-gateway]");
    if (!button) return false;
    const action = button.dataset.applicationGateway;
    if (action === "toggle") await this.change({enabled:!this.state.running, mode:this.state.mode, port:this.state.configured_port});
    if (action === "refresh") await this.reload();
    if (action === "settings" && !this.settingsLoading && !this.dialog?.open) {
      this.settingsLoading = true;
      try { await this.settings(); }
      finally { this.settingsLoading = false; }
    }
    if (action === "error") this.toast(this.state.error);
    return true;
  }
  example(key) {
    const authorization = `Authorization: Bearer ${key}`.replaceAll("'", "'\\''");
    return `curl 'http://127.0.0.1:${this.state.port || (this.state.mode === "manual" ? this.state.configured_port : this.state.preferred_port)}/app?appid=#{appid}' \\\n  -H '${authorization}' \\\n  -H 'Content-Type: application/json' \\\n  -d '{"input":"你好","stream":false}'`;
  }
  async settings() {
    const generation = this.generation;
    const state = await this.api("gateway-status", {include_key:"1"});
    if (generation !== this.generation || !this.root) return;
    this.state = state;
    const key = state.api_key;
    delete state.api_key;
    const dialog = document.createElement("dialog");
    dialog.className = "application-gateway-dialog";
    dialog.setAttribute("aria-labelledby", "application-gateway-dialog-title");
    dialog.innerHTML = `<div class="application-gateway-dialog-heading"><h2 id="application-gateway-dialog-title">应用网关</h2><button class="icon-button" data-gateway-close aria-label="关闭">${icon('<path d="m6 6 12 12M6 18 18 6"/>')}</button></div><div class="application-gateway-dialog-body"><section><h3>端口</h3><div class="application-gateway-port-options"><label><input type="radio" name="gateway-port-mode" value="auto" ${state.mode === "auto" ? "checked" : ""}>自动分配</label><label><input type="radio" name="gateway-port-mode" value="manual" ${state.mode === "manual" ? "checked" : ""}>指定端口</label><input class="application-gateway-port" type="number" name="gateway-port" aria-label="网关端口" min="1" max="65535" value="${state.configured_port}" ${state.mode === "auto" ? "disabled" : ""}></div>${state.running ? "<p>修改端口会重启网关，已接收的任务继续执行。</p>" : ""}</section><section><h3>访问密钥</h3><div class="application-gateway-secret"><input type="password" readonly aria-label="网关访问密钥"><button class="text-button" data-gateway-reveal>显示</button><button class="icon-button" data-gateway-copy-key aria-label="复制访问密钥">${copy}</button></div></section><section><h3>调用示例</h3><pre class="application-gateway-example"></pre></section><p class="application-gateway-dialog-error" role="alert" hidden></p></div><div class="dialog-foot"><button class="button" data-gateway-close>取消</button><button class="button primary" data-gateway-save>${state.running ? "保存并重启" : "保存设置"}</button></div>`;
    dialog.querySelector("[aria-label=网关访问密钥]").value = key;
    const example = dialog.querySelector("pre");
    example.textContent = this.example(key);
    const close = () => {dialog.close(); dialog.remove(); if(this.dialog===dialog)this.dialog=null;};
    dialog.addEventListener("close", () => dialog.remove(), {once:true});
    dialog.onchange = () => {dialog.querySelector("[name=gateway-port]").disabled=dialog.querySelector('[name=gateway-port-mode]:checked').value==="auto";};
    dialog.onclick = async event => {
      const button=event.target.closest("button");
      if(!button)return;
      try {
        if(button.hasAttribute("data-gateway-close"))return close();
        if(button.hasAttribute("data-gateway-reveal")) {
          const input=dialog.querySelector("[aria-label=网关访问密钥]");
          input.type=input.type==="password"?"text":"password";
          button.textContent=input.type==="password"?"显示":"隐藏";
        }
        if(button.hasAttribute("data-gateway-copy-key")){await navigator.clipboard.writeText(key);this.toast("访问密钥已复制");}
        if(button.hasAttribute("data-gateway-save")) {
          const input=dialog.querySelector("[name=gateway-port]");
          const mode=dialog.querySelector('[name=gateway-port-mode]:checked').value;
          if(mode==="manual"&&!input.reportValidity())return;
          const port=mode==="manual"?Number(input.value):state.preferred_port;
          button.disabled=true;
          await this.change({enabled:state.running,mode,port});
        }
      }catch(error){const alert=dialog.querySelector("[role=alert]");alert.textContent=error.message;alert.hidden=false;button.disabled=false;}
    };
    document.body.append(dialog);
    this.dialog=dialog;
    dialog.showModal();
  }
}
