import { ApplicationCreatePage } from "./application-create-page.js";
import { ApplicationGatewayView } from "./application-gateway.js";
import { ApplicationAccessView, ApplicationKafkaGatewayView } from "./application-access.js";
import { basicSetup } from "codemirror";
import { EditorState, Compartment } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { markdown } from "@codemirror/lang-markdown";
import { json } from "@codemirror/lang-json";
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import { MergeView } from "@codemirror/merge";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags, highlightCode, classHighlighter } from "@lezer/highlight";

const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const svg = (paths) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
const icons = {
  llm: '<img class="application-type-avatar application-llm-avatar" src="/application-llm-rounded.png" width="43" height="43" alt="LLM 应用">',
  agent: '<img class="application-type-avatar application-agent-avatar" src="/application-agent-v2.png" width="41" height="41" alt="Agent 应用">',
  trash: svg('<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>'),
  copy: svg('<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  chat: svg('<path d="M21 11a9 9 0 0 1-9 9H4l-2 2v-9a9 9 0 1 1 19-2z"/>'),
  arrow: svg('<path d="m9 5 7 7-7 7"/>'),
  folder: svg('<path d="M3 7V4h6l3 3h9v13H3z"/>'),
  stop: svg(
    '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>',
  ),
};
const theme = EditorView.theme({
  "&": { fontSize: "13px", backgroundColor: "#fff", color: "#28344b" },
  ".cm-scroller": {
    fontFamily: "ui-monospace,SFMono-Regular,Menlo,monospace",
    lineHeight: "1.8",
  },
  ".cm-content": { padding: "18px 0" },
  ".cm-gutters": {
    backgroundColor: "#fcfbfa",
    color: "#a0a9ba",
    borderRight: "1px solid #f0ece9",
  },
  ".cm-activeLine": { backgroundColor: "#fff8f2" },
  ".cm-activeLineGutter": { backgroundColor: "#fff0e4" },
  "&.cm-focused": { outline: "none" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": {
    backgroundColor: "#bfdcff !important",
  },
  "& ::selection": { backgroundColor: "#bfdcff" },
});
const colors = syntaxHighlighting(
  HighlightStyle.define([
    { tag: tags.heading, color: "#ba4b00", fontWeight: "650" },
    { tag: tags.strong, fontWeight: "700", color: "#26384d" },
    { tag: tags.emphasis, fontStyle: "italic", color: "#8e5b89" },
    { tag: tags.link, color: "#237e9b" },
    { tag: tags.string, color: "#3b866a" },
    { tag: tags.number, color: "#a15d17" },
    { tag: tags.bool, color: "#9054b3" },
    { tag: tags.keyword, color: "#a75088" },
    { tag: tags.comment, color: "#9299a3" },
    { tag: tags.propertyName, color: "#326a99" },
  ]),
);
const fields = [
  "name",
  "description",
  "type",
  "provider",
  "model",
  "prompt",
  "context",
  "directory",
  "permissions",
  "skills",
];
const defaults = () => ({
  name: "",
  description: "",
  type: "llm",
  provider: "",
  model: "",
  prompt: "",
  context: true,
  directory: "",
  permissions: "",
  skills: [],
});
const bodyOf = (app) => Object.fromEntries(fields.map((k) => [k, app[k]]));
const stamp = (t) => window.CorkiDateTime(t);
const route = (id = "", sub = "") =>
  "#applications" +
  (id ? "/" + encodeURIComponent(id) : "") +
  (sub ? "/" + sub : "");

async function api(action, values = {}, write = false) {
  const response = await fetch(
    "/api/applications" +
      (write ? "" : "?" + new URLSearchParams({ action, ...values })),
    write
      ? {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Corki-Manager": "1",
          },
          body: JSON.stringify({ action, ...values }),
        }
      : { cache: "no-store" },
  );
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}
async function submitTurn(data) {
  try {
    return await api("send", data, true);
  } catch (original) {
    // Reconcile receipt only. Never automatically execute the request twice.
    let accepted, digest;
    try {
      accepted = await api("submission", {
        app: data.app,
        request: data.request,
      });
      const bytes = await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(data.text),
      );
      digest = [...new Uint8Array(bytes)]
        .map((v) => v.toString(16).padStart(2, "0"))
        .join("");
    } catch {
      throw original;
    }
    if (accepted.input_sha !== digest) throw original;
    return accepted;
  }
}
function colorCode(host) {
  for (const code of host.querySelectorAll("pre code")) {
    const language = code.className;
    let parser;
    if (/json/.test(language)) parser = json().language.parser;
    else if (/javascript|typescript|\bjs\b|\bts\b/.test(language))
      parser = javascript().language.parser;
    else if (/python|\bpy\b/.test(language)) parser = python().language.parser;
    if (!parser) continue;
    const text = code.textContent;
    let result = "";
    highlightCode(
      text,
      parser.parse(text),
      classHighlighter,
      (part, classes) => {
        result += classes
          ? `<span class="${classes}">${esc(part)}</span>`
          : esc(part);
      },
      () => {
        result += "\n";
      },
    );
    code.innerHTML = result;
  }
}
class ApplicationsWorkspace {
  constructor() {
    this.saves = new Set();
    this.drafts = new Map();
    this.draftBases = new Map();
    this.generation = 0;
    this.chatStates = new Map();
    this.codeViews = [];
    this.search = "";
    this.kind = "";
    this.myAppsOpen = true;
    this.detailTabs = new Map();
    this.gatewayUI = new ApplicationGatewayView(api);
    this.kafkaUI = new ApplicationKafkaGatewayView(api);
  }
  mount(root, services) {
    this.unmount();
    this.root = root;
    this.services = services;
    this.id = services.id;
    this.detailTab = this.detailTabs.get(this.id) || "info";
    this.sub = services.sub;
    this.editing = false;
    this.form = null;
    this.app = null;
    root.innerHTML =
      '<div class="panel application-empty"><span class="spinner"></span><p>正在读取应用…</p></div>';
    this.outside = (e) => {
      for (const detail of this.root?.querySelectorAll("details[open]") || []) {
        if (!detail.contains(e.target) && !detail.dataset.tool && !detail.matches(".application-skill-group, .application-my-apps"))
          detail.open = false;
      }
    };
    this.escape = (e) => {
      if (e.key === "Escape")
        for (const detail of this.root?.querySelectorAll("details[open]") || [])
          if (!detail.dataset.tool && !detail.matches(".application-skill-group, .application-my-apps")) detail.open = false;
    };
    document.addEventListener("pointerdown", this.outside);
    document.addEventListener("keydown", this.escape);
    this.selectionChanged = () => {
      if (
        this.deferredRender &&
        !window.getSelection()?.toString() &&
        this.root &&
        this.sub === "chat"
      ) {
        this.deferredRender = false;
        this.renderRuns();
      }
    };
    document.addEventListener("selectionchange", this.selectionChanged);
    this.generation++;
    root.classList.add("applications-workspace");
    root.onclick = (e) => this.click(e).catch((x) => services.toast(x.message));
    root.oninput = (e) => this.input(e);
    root.onchange = (e) => this.change(e);
    root.onkeydown = (e) => {
      if (!e.target.matches("[data-app-tab]")) return;
      const tabs = [...root.querySelectorAll("[data-app-tab]")];
      let index = tabs.indexOf(e.target);
      if (e.key === "ArrowRight") index = (index + 1) % tabs.length;
      else if (e.key === "ArrowLeft") index = (index + tabs.length - 1) % tabs.length;
      else if (e.key === "Home") index = 0;
      else if (e.key === "End") index = tabs.length - 1;
      else return;
      e.preventDefault();
      this.setDetailTab(tabs[index].dataset.appTab);
      tabs[index].focus();
    };
    this.menuPosition = (event) => {
      const detail = event.target;
      if (detail.matches?.("[data-my-apps]") && detail.isConnected) {
        this.myAppsOpen = detail.open;
        return;
      }
      if (!detail.matches?.(".application-session details") || !detail.open)
        return;
      const box = detail.querySelector("summary").getBoundingClientRect();
      const menu = detail.querySelector("div");
      menu.style.position = "fixed";
      menu.style.left = Math.max(10, box.right - 120) + "px";
      menu.style.top =
        Math.min(window.innerHeight - 100, box.bottom + 4) + "px";
      menu.style.right = "auto";
      menu.style.zIndex = "60";
    };
    root.addEventListener("toggle", this.menuPosition, true);
    const mountedGeneration = this.generation;
    this.load().catch((e) => {
      if (this.root === root && this.generation === mountedGeneration) {
        this.root.innerHTML = `<div class="panel application-empty"><h3>暂时无法读取应用</h3><p>${esc(e.message)}</p><button class="button" data-app-action="reload">重试</button></div>`;
      }
    });
  }
  unmount() {
    this.gatewayUI.unmount();
    this.kafkaUI.unmount();
    this.dialog?.close();
    this.dialog = null;
    document.removeEventListener("pointerdown", this.outside);
    document.removeEventListener("keydown", this.escape);
    document.removeEventListener("selectionchange", this.selectionChanged);
    this.deferredRender = false;
    this.capture();
    this.generation++;
    clearTimeout(this.pollTimer);
    clearTimeout(this.searchTimer);
    this.dispose();
    if (this.root) {
      this.root.removeEventListener("toggle", this.menuPosition, true);
      this.root.onclick = null;
      this.root.oninput = null;
      this.root.onchange = null;
      this.root.onkeydown = null;
    }
    this.root = null;
  }
  dispose() {
    this.accessUI?.destroy();
    this.accessUI = null;
    this.createPage?.destroy();
    this.createPage = null;
    this.editor?.destroy();
    this.editor = null;
    this.merge?.destroy();
    this.merge = null;
    for (const v of this.codeViews) v.destroy();
    this.codeViews = [];
  }
  capture() {
    if (this.id && this.editing && this.form) {
      const value = {
        ...this.form,
        prompt: this.editor?.state.doc.toString() ?? this.form.prompt,
      };
      this.drafts.set(this.id, value);
      if (!this.draftBases.has(this.id))
        this.draftBases.set(this.id, this.app?.version ?? 0);
      try {
        localStorage.setItem(
          "corki.application.draft." + this.id,
          JSON.stringify({
            values: value,
            expected: this.draftBases.get(this.id),
          }),
        );
      } catch {}
    }
  }
  async load() {
    const generation = this.generation;
    if (this.id === "kafka") {
      this.services.onName?.("Kafka 接入");
      this.accessUI = new ApplicationAccessView(api, this.services);
      this.accessUI.mount(this.root);
      return;
    }
    if (!this.id) {
      await this.list();
      return;
    }
    const [catalog, app] = await Promise.all([
      api("catalog"),
      this.id === "new"
        ? Promise.resolve({
            ...defaults(),
            id: "new",
            version: 0,
            versions: [],
          })
        : api("detail", { app: this.id }),
    ]);
    if (generation !== this.generation) return;
    this.catalog = catalog;
    if (catalog.error) this.services.toast(catalog.error);
    this.app = app;
    this.services.onName?.(app.name || "创建应用");
    if (this.sub === "chat") {
      this.editing = false;
      await this.chat();
      return;
    }
    this.editing = this.id === "new";
    this.historical = null;
    try {
      if (!this.drafts.has(this.id)) {
        const draft = JSON.parse(
          localStorage.getItem("corki.application.draft." + this.id) || "null",
        );
        if (draft) {
          this.drafts.set(this.id, draft.values || draft);
          this.draftBases.set(
            this.id,
            Number.isInteger(draft.expected) ? draft.expected : app.version,
          );
        }
      }
    } catch {}
    this.form = this.drafts.get(this.id) || bodyOf(app);
    if (this.drafts.has(this.id)) this.editing = true;
    if (this.sub === "diff") {
      this.editing = false;
      await this.diff(Number(location.hash.split("/")[3]));
      return;
    }
    this.detail();
  }
  async list(append = false) {
    const generation = this.generation;
    const request = (this.listRequest = (this.listRequest || 0) + 1);
    const data = await api("list", {
      q: this.search,
      type: this.kind,
      offset: append ? this.rows.length : 0,
    });
    if (generation !== this.generation || request !== this.listRequest) return;
    this.rows = append ? [...this.rows, ...data.items] : data.items;
    this.more = data.has_more;
    this.root.innerHTML = `<div class="application-ingress-bar"><section class="application-gateway" aria-label="应用网关" data-application-gateway-host></section><section class="application-gateway" aria-label="Kafka 接入" data-application-kafka-host></section></div><details class="skill-section application-my-apps" data-my-apps ${this.myAppsOpen ? "open" : ""}><summary class="skill-section-head"><span class="skill-group-icon corki">${icons.folder}</span><div><h2>我的应用<span>${this.rows.length}${this.more ? "+" : ""}</span></h2></div><svg class="skill-collapse-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></summary><div class="application-list-toolbar"><label class="search">${svg('<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>')}<input aria-label="搜索应用 ID 或名称" placeholder="搜索应用 ID 或名称" data-app-search value="${esc(this.search)}"></label><div class="filters">${[
      ["", "全部"],
      ["llm", "LLM"],
      ["agent", "Agent"],
    ]
      .map(
        ([id, text]) =>
          `<button data-app-action="filter" data-kind="${id}" class="${this.kind === id ? "active" : ""}">${text}</button>`,
      )
      .join(
        "",
      )}</div><a class="button primary application-create" href="${route("new")}">${icons.plus}创建应用</a></div><div class="application-cards">${this.rows.map((app) => this.card(app)).join("")}</div>${this.rows.length ? "" : `<section class="panel application-empty"><span class="application-empty-icon">${icons.agent}</span><h3>${this.search || this.kind ? "没有匹配的应用" : "创建第一个应用"}</h3><p>${this.search || this.kind ? "试试其他名称或类型" : "用提示词构建 LLM 应用，或接入完整 Agent 能力。"}</p></section>`}${this.more ? '<button class="button application-load-more" data-app-action="more-apps">加载更多</button>' : ""}</details>`;
    this.gatewayUI.mount(this.root.querySelector("[data-application-gateway-host]"), this.services.toast);
    this.kafkaUI.mount(this.root.querySelector("[data-application-kafka-host]"), this.services.toast);
  }
  card(app) {
    return `<article class="application-card${app.enabled ? "" : " is-disabled"}" data-card-id="${app.id}">
      <div class="application-card-top">
        <a class="application-card-heading" href="${route(app.id)}">
          <span class="application-mark">${icons[app.type]}</span>
          <div class="application-card-identity"><h2>${esc(app.name)}</h2></div>
        </a>
        <button class="toggle application-card-switch${app.enabled ? " on" : ""}" role="switch" aria-checked="${app.enabled}" aria-label="启用 ${esc(app.name)}" data-app-action="toggle-app" data-id="${app.id}" ${this.pendingToggles?.has(app.id) ? "disabled" : ""}></button>
      </div>
      <a class="application-card-main" href="${route(app.id)}"><p>${esc(app.description || "暂无描述")}</p></a>
      <div class="application-card-footer">
        <div class="application-card-meta"><div class="application-card-id"><span>应用 ID</span><span>${app.public_id}</span><button class="icon-button application-copy-id" data-app-action="copy-app-id" data-id="${app.id}" aria-label="复制应用 ID ${app.public_id}">${icons.copy}</button></div>
          <div class="application-card-modified">最后修改 <time>${stamp(app.updated)}</time></div></div>
        <div class="application-card-actions"><button class="icon-button application-delete" data-app-action="delete" data-id="${app.id}" aria-label="删除 ${esc(app.name)}">${icons.trash}</button></div>
      </div>
    </article>`;
  }
  async toggleApp(button) {
    const id = button.dataset.id;
    this.pendingToggles ??= new Set();
    if (this.pendingToggles.has(id)) return;
    this.pendingToggles.add(id);
    const generation = this.generation;
    const enabled = button.getAttribute("aria-checked") !== "true";
    button.disabled = true;
    try {
      const result = await api("set-enabled", { app: id, enabled }, true);
      if (generation !== this.generation) return;
      const row = this.rows.find((row) => row.id === id);
      if (row) Object.assign(row, result);
      const card = [...this.root.querySelectorAll("[data-card-id]")].find((card) => card.dataset.cardId === id);
      if (card) {
        card.classList.toggle("is-disabled", !result.enabled);
        const toggle = card.querySelector(".application-card-switch");
        toggle.classList.toggle("on", result.enabled);
        toggle.setAttribute("aria-checked", String(result.enabled));
        card.querySelector("time").textContent = stamp(result.updated);
      }
      this.services.toast(result.enabled ? "应用已开启" : "应用已关闭");
    } finally {
      this.pendingToggles.delete(id);
      if (generation === this.generation) {
        for (const toggle of this.root.querySelectorAll(".application-card-switch"))
          if (toggle.dataset.id === id) toggle.disabled = false;
      }
    }
  }
  detail() {
    this.dispose();
    this.createPage = new ApplicationCreatePage(this, colors, icons);
    this.createPage.render();
  }
  setDetailTab(tab) {
    if (!["info", "prompt", "access"].includes(tab) || (tab === "access" && this.id === "new")) return;
    this.detailTab = tab;
    this.detailTabs.set(this.id, tab);
    this.applyDetailTab();
  }
  applyDetailTab() {
    const active = this.detailTab || "info";
    for (const button of this.root.querySelectorAll("[data-app-tab]")) {
      const selected = button.dataset.appTab === active;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    }
    for (const panel of this.root.querySelectorAll("[data-app-panel]")) panel.hidden = panel.dataset.appPanel !== active;
    this.createPage?.actionLabels();
    if (active === "access" && this.id !== "new") {
      if (!this.accessUI) {
        this.accessUI = new ApplicationAccessView(api, this.services, this.id);
        this.accessUI.mount(this.root.querySelector("[data-app-access]"));
      }
      this.accessUI.setVisible(true);
    } else this.accessUI?.setVisible(false);
    if (active === "prompt") this.editor?.requestMeasure();
  }
  input(event) {
    if (this.createPage?.input(event)) return;
    if (event.target.matches("[data-app-skill-search]")) {
      const query = event.target.value.trim().toLocaleLowerCase();
      let found = 0;
      for (const group of this.root.querySelectorAll(".application-skill-group")) {
        let matches = 0;
        for (const chip of group.querySelectorAll("[data-skill-name]")) {
          chip.hidden = !`${chip.dataset.skillName} ${chip.dataset.skillGroup}`.toLocaleLowerCase().includes(query);
          if (!chip.hidden) matches++;
        }
        group.hidden = !matches;
        if (query && matches) group.open = true;
        found += matches;
      }
      this.root.querySelector(".application-skill-empty").hidden = found > 0;
      return;
    }
    if (event.target.matches("[data-app-search]")) {
      this.search = event.target.value;
      clearTimeout(this.searchTimer);
      const pos = event.target.selectionStart;
      this.searchTimer = setTimeout(
        () =>
          this.list()
            .then(() => {
              const input = this.root?.querySelector("[data-app-search]");
              input?.focus();
              input?.setSelectionRange(pos, pos);
            })
            .catch((e) => this.services.toast(e.message)),
        220,
      );
      return;
    }
    if (
      this.editing &&
      ["name", "description", "directory"].includes(event.target.name)
    ) {
      this.form[event.target.name] = event.target.value;
      this.capture();
    }
    if (event.target.matches("[data-app-input]")) {
      this.chatState.draft = event.target.value;
      this.chatState.drafts[this.chatState.session || "new"] =
        event.target.value;
      this.saveChatState();
      event.target.style.height = "auto";
      event.target.style.height =
        Math.min(event.target.scrollHeight, 180) + "px";
    }
  }
  change(event) {
    if (this.createPage?.change(event)) return;
    if (event.target.name === "context") {
      this.form.context = event.target.checked;
      this.capture();
    }
    if (event.target.name === "skill") {
      this.form.skills = [
        ...this.root.querySelectorAll("[name=skill]:checked"),
      ].map((e) => e.value);
      this.capture();
    }
  }
  async save() {
    if (this.saves.has(this.id) || this.createPage?.accepted) return;
    if (this.createPage && !this.createPage.validate()) return;
    const creation = this.createPage;
    this.saves.add(this.id);
    const id = this.id,
      generation = this.generation;
    const button = this.root.querySelector("[data-app-action=save]");
    const definition = this.root.querySelector(".application-definition");
    const cancel = this.root.querySelector("[data-app-action=cancel-edit]");
    if (definition) definition.inert = true;
    if (cancel) cancel.disabled = true;
    if (button) button.disabled = true;
    creation?.busy(true);
    try {
      this.form.prompt = this.editor.state.doc.toString();
      const submitted = JSON.parse(JSON.stringify(this.form));
      const result = await api(
        "save",
        {
          app: id === "new" ? undefined : id,
          expected: this.draftBases.get(id) ?? this.app.version,
          values: submitted,
          section: this.detailTab || "info",
        },
        true,
      );
      if (JSON.stringify(this.drafts.get(id)) === JSON.stringify(submitted)) {
        this.drafts.delete(id);
        this.draftBases.delete(id);
        localStorage.removeItem("corki.application.draft." + id);
      }
      if (generation !== this.generation) return;
      this.editing = false;
      this.app = result;
      this.form = bodyOf(result);
      this.historical = null;
      this.services.toast("应用已保存");
      if (this.id === "new") {
        creation.accepted = true;
        this.detailTabs.set(result.id, this.detailTab || "info");
        location.hash = route(result.id);
      } else {
        this.services.onName?.(result.name);
        this.detail();
      }
    } catch (error) {
      if (generation === this.generation && this.createPage === creation) creation?.saveError(error);
      throw error;
    } finally {
      this.saves.delete(id);
      if (!creation?.accepted) {
        if (definition?.isConnected) definition.inert = false;
        if (cancel?.isConnected) cancel.disabled = false;
        if (button?.isConnected) button.disabled = false;
        if (generation === this.generation && this.createPage === creation) creation?.busy(false);
      }
    }
  }
  async version(version) {
    if (this.editing) {
      this.services.toast("请先保存或取消当前修改");
      return;
    }
    const generation = this.generation,
      request = (this.versionRequest = (this.versionRequest || 0) + 1);
    const past = await api("detail", { app: this.id, version });
    if (
      generation !== this.generation ||
      request !== this.versionRequest ||
      this.editing
    )
      return;
    this.historical = version === (this.app.prompt_version || this.app.version) ? null : version;
    this.form = { ...bodyOf(this.app), prompt: past.prompt };
    this.detail();
  }
  async diff(version) {
    if (this.sub !== "diff") {
      this.capture();
      location.hash = route(this.id, "diff") + "/" + version;
      return;
    }
    const generation = this.generation;
    const past = await api("detail", { app: this.id, version });
    if (generation !== this.generation) return;
    this.capture();
    this.editing = false;
    this.dispose();
    this.root.innerHTML = `<div class="application-diff-heading"><button class="button" data-app-action="back-detail">返回应用</button><span>版本 ${version} 与当前版本</span></div><div class="panel application-diff"><div class="application-diff-labels"><span>历史版本 ${version}</span><span>当前版本 ${this.app.prompt_version || this.app.version}</span></div><div data-app-diff></div></div>`;
    const a = past.prompt,
      b = this.app.prompt;
    this.merge = new MergeView({
      a: {
        doc: a,
        extensions: [
          basicSetup,
          markdown(),
          theme,
          colors,
          EditorState.readOnly.of(true),
          EditorView.editable.of(false),
        ],
      },
      b: {
        doc: b,
        extensions: [
          basicSetup,
          markdown(),
          theme,
          colors,
          EditorState.readOnly.of(true),
          EditorView.editable.of(false),
        ],
      },
      parent: this.root.querySelector("[data-app-diff]"),
      highlightChanges: true,
      gutter: true,
      collapseUnchanged: { margin: 3, minSize: 5 },
    });
  }
  confirm(title, message, action, danger = false, application = null) {
    const dialog = document.createElement("dialog");
    dialog.className = "application-confirm" + (application ? " application-delete-confirm" : "");
    dialog.setAttribute("aria-labelledby", "application-confirm-title");
    dialog.setAttribute("aria-describedby", "application-confirm-description");
    dialog.innerHTML = application
      ? `<div class="application-delete-body"><h2 id="application-confirm-title">删除应用？</h2><p class="application-delete-intro">删除后无法恢复，请确认要删除的应用。</p>
          <div class="application-delete-target"><span class="application-mark">${icons[application.type]}</span><div><strong>${esc(application.name)}</strong><span>应用 ID <b>${application.public_id}</b></span></div></div>
          <p id="application-confirm-description" class="application-delete-scope">应用配置、所有版本和会话将永久删除，进行中的任务也会停止。</p><p class="application-delete-retained">工作目录中的项目文件会保留。</p>
          <p class="application-delete-error" role="alert" hidden></p></div><div class="dialog-foot"><button class="button" data-dismiss autofocus>取消</button><button class="button danger" data-confirm>确认删除</button></div>`
      : `<div class="application-confirm-body"><span class="application-confirm-mark">${danger ? icons.trash : icons.agent}</span><h2 id="application-confirm-title">${esc(title)}</h2><p id="application-confirm-description">${esc(message)}</p></div><div class="dialog-foot"><button class="button" data-dismiss autofocus>取消</button><button class="button ${danger ? "danger" : "primary"}" data-confirm>${danger ? "确认删除" : "确认"}</button></div>`;
    document.body.append(dialog);
    this.dialog = dialog;
    const dismiss = dialog.querySelector("[data-dismiss]");
    const confirm = dialog.querySelector("[data-confirm]");
    const cleanup = () => {
      dialog.remove();
      if (this.dialog === dialog) this.dialog = null;
    };
    const close = () => { dialog.close(); cleanup(); };
    dismiss.onclick = close;
    dialog.addEventListener("cancel", (event) => {
      if (confirm.disabled) event.preventDefault();
    });
    dialog.addEventListener("close", cleanup, { once: true });
    dialog.querySelector("[data-confirm]").onclick = async (e) => {
      confirm.disabled = true;
      if (application) {
        dismiss.disabled = true;
        confirm.textContent = "删除中…";
        dialog.querySelector("[role=alert]").hidden = true;
      }
      try {
        await action();
        close();
      } catch (error) {
        if (application) {
          const message = dialog.querySelector("[role=alert]");
          message.textContent = error.message;
          message.hidden = false;
          confirm.textContent = "确认删除";
          dismiss.disabled = false;
        } else this.services.toast(error.message);
        confirm.disabled = false;
      }
    };
    dialog.showModal();
  }
  async click(event) {
    if (await this.gatewayUI.click(event)) return;
    const tab = event.target.closest("[data-app-tab]");
    if (tab) { this.setDetailTab(tab.dataset.appTab); return; }
    if (this.createPage && await this.createPage.click(event)) return;
    const picked = event.target.closest("[data-pick]");
    if (picked) {
      this.capture();
      this.form[picked.dataset.pick] = picked.dataset.value;
      if (picked.dataset.pick === "provider") this.form.model = "";
      this.capture();
      this.detail();
      return;
    }
    const button = event.target.closest("[data-app-action]");
    if (!button) return;
    const action = button.dataset.appAction;
    if (action === "toggle-app") return this.toggleApp(button);
    if (action === "copy-app-id") {
      const app = this.rows.find((row) => row.id === button.dataset.id);
      if (app) {
        await navigator.clipboard.writeText(String(app.public_id));
        this.services.toast("应用 ID 已复制");
      }
      return;
    }
    if (action === "reload") return this.load();
    if (action === "filter") {
      this.kind = button.dataset.kind;
      return this.list();
    }
    if (action === "more-apps") return this.list(true);
    if (action === "edit") {
      this.draftBases.set(this.id, this.app.version);
      this.editing = true;
      this.historical = null;
      this.form = bodyOf(this.app);
      return this.detail();
    }
    if (action === "cancel-edit") {
      this.drafts.delete(this.id);
      this.draftBases.delete(this.id);
      localStorage.removeItem("corki.application.draft." + this.id);
      this.editing = false;
      if (this.id === "new") {
        location.hash = route();
        return;
      }
      this.form = bodyOf(this.app);
      this.historical = null;
      return this.detail();
    }
    if (action === "save") return this.save();
    if (action === "version")
      return this.version(Number(button.dataset.version));
    if (action === "diff") return this.diff(Number(button.dataset.version));
    if (action === "back-detail") {
      location.hash = route(this.id);
      return;
    }
    if (action === "restore") {
      const version = Number(button.dataset.version);
      const id = this.id,
        expected = this.app.version,
        generation = this.generation;
      this.confirm(
        "回滚提示词？",
        "恢复此版本的提示词，其他设置保持不变。已开始的任务继续使用原配置。",
        async () => {
          const result = await api(
            "restore-prompt",
            { app: id, version, expected },
            true,
          );
          if (generation !== this.generation) return;
          this.app = result;
          this.form = bodyOf(this.app);
          this.historical = null;
          this.detail();
          this.services.toast(result.version === expected ? "提示词与当前一致，无需回滚" : "提示词已回滚为新版本");
        },
      );
      return;
    }
    if (action === "directory") {
      const generation = this.generation;
      const result = await api("choose-directory", {}, true);
      if (generation !== this.generation) return;
      if (result.directory) {
        if (this.createPage) this.createPage.setDirectory(result.directory);
        else {
          this.form.directory = result.directory;
          this.capture();
          this.detail();
        }
      }
      return;
    }
    if (action === "delete") {
      const app = button.dataset.id;
      const application = this.rows.find((row) => row.id === app);
      if (!application) return;
      const generation = this.generation;
      this.confirm(
        "删除应用？",
        "删除后无法恢复。应用的版本和会话将永久删除，运行中的任务会停止，工作目录文件不受影响。",
        async () => {
          await api("delete", { app, stop: true }, true);
          if (generation !== this.generation) return;
          await this.list();
          this.services.toast("应用已删除");
        },
        true,
        application,
      );
      return;
    }
    if (action === "send") return this.send();
    if (action === "stop") {
      button.disabled = true;
      try {
        await api(
          "stop",
          {
            app: this.id,
            session: this.chatState.session,
            run: this.activeRun.id,
          },
          true,
        );
        await this.refreshChat();
      } finally {
        if (button.isConnected) button.disabled = false;
      }
      return;
    }
    if (action === "new-session") {
      if (this.sending) return;
      this.chatState.drafts[this.chatState.session || "new"] =
        this.chatState.draft;
      this.chatState.session = "";
      this.chatState.draft = this.chatState.drafts.new || "";
      this.chatState.request = null;
      this.chatState.runs = [];
      this.chatState.more = false;
      this.activeRun = null;
      this.saveChatState();
      return this.chat();
    }
    if (action === "session") {
      if (this.sending) return;
      this.chatState.drafts[this.chatState.session || "new"] =
        this.chatState.draft;
      this.chatState.session = button.dataset.id;
      this.chatState.draft =
        this.chatState.drafts[this.chatState.session] || "";
      this.chatState.request = null;
      this.chatState.runs = [];
      this.chatState.more = undefined;
      this.saveChatState();
      return this.chat();
    }
    if (action === "more-messages") {
      const generation = this.generation,
        session = this.chatState.session;
      const list = this.root.querySelector(".application-messages"),
        height = list.scrollHeight;
      button.disabled = true;
      const data = await api("history", {
        app: this.id,
        session: this.chatState.session,
        before: this.chatState.runs[0].created,
      });
      if (generation !== this.generation || session !== this.chatState.session)
        return;
      this.chatState.runs = [...data.items, ...this.chatState.runs];
      this.chatState.more = data.has_more;
      this.renderRuns();
      list.scrollTop += list.scrollHeight - height;
      return;
    }
    if (action === "more-sessions") {
      const generation = this.generation;
      const data = await api("sessions", {
        app: this.id,
        offset: this.chatState.sessions.length,
      });
      if (generation !== this.generation) return;
      this.chatState.sessions.push(...data.items);
      this.chatState.sessionsMore = data.has_more;
      this.renderSessions();
      return;
    }
    if (action === "rename-session") {
      const current = this.chatState.sessions.find(
        (s) => s.id === button.dataset.id,
      );
      this.renameSession(current);
      return;
    }
    if (action === "delete-session") {
      const session = button.dataset.id;
      const app = this.id,
        state = this.chatState,
        generation = this.generation;
      this.confirm(
        "删除会话？",
        "会话记录和模型上下文将永久删除，无法恢复。运行中的任务将停止。",
        async () => {
          await api("delete-session", { app, session, stop: true }, true);
          delete state.drafts[session];
          if (generation !== this.generation) {
            this.saveChatState(state, app);
            return;
          }
          if (this.chatState.session === session) {
            this.chatState.session = "";
            this.chatState.runs = [];
            this.chatState.more = false;
            this.chatState.draft = this.chatState.drafts.new || "";
            this.chatState.request = null;
            this.activeRun = null;
          }
          this.saveChatState();
          await this.chat();
        },
        true,
      );
      return;
    }
    if (action === "copy") {
      const text = this.chatState.runs.find((r) => r.id === button.dataset.run);
      await navigator.clipboard.writeText(
        text.messages?.length
          ? text.messages.map((m) => m.content).join("\n\n")
          : text.answer,
      );
      this.services.toast("已复制回答原文");
      return;
    }
    if (action === "respond") return this.respond(button);
    if (action === "latest") {
      const list = this.root.querySelector(".application-messages");
      list.scrollTo({ top: list.scrollHeight, behavior: "smooth" });
    }
  }
  renameSession(session) {
    const dialog = document.createElement("dialog");
    dialog.className = "application-confirm";
    dialog.innerHTML = `<form><div class="dialog-head"><h2>重命名会话</h2></div><label class="application-field"><input aria-label="会话名称" value="${esc(session.title)}" maxlength="100" required></label><div class="dialog-foot"><button class="button" type="button">取消</button><button class="button primary">保存</button></div></form>`;
    document.body.append(dialog);
    this.dialog = dialog;
    dialog.querySelector("[type=button]").onclick = () => dialog.close();
    dialog.onclose = () => dialog.remove();
    dialog.querySelector("form").onsubmit = async (e) => {
      e.preventDefault();
      try {
        await api(
          "rename-session",
          {
            app: this.id,
            session: session.id,
            title: dialog.querySelector("input").value,
          },
          true,
        );
        dialog.close();
        await this.loadSessions();
      } catch (error) {
        this.services.toast(error.message);
      }
    };
    dialog.showModal();
  }
  readChatState() {
    if (this.chatStates.has(this.id)) return this.chatStates.get(this.id);
    let saved = {};
    try {
      saved = JSON.parse(
        localStorage.getItem("corki.application.chat." + this.id) || "{}",
      );
    } catch {}
    const state = {
      session: typeof saved.session === "string" ? saved.session : "",
      draft: typeof saved.draft === "string" ? saved.draft : "",
      drafts: saved.drafts || {},
      request: saved.request || null,
      runs: [],
      sessions: [],
    };
    this.chatStates.set(this.id, state);
    return state;
  }
  saveChatState(state = this.chatState, id = this.id) {
    if (!state) return;
    try {
      localStorage.setItem(
        "corki.application.chat." + id,
        JSON.stringify({
          session: state.session,
          draft: state.draft,
          drafts: state.drafts,
          request: state.request,
        }),
      );
    } catch {
      this.services.toast("草稿保存失败，请留意浏览器存储空间");
    }
  }
  async chat() {
    clearTimeout(this.pollTimer);
    this.activeRun = null;
    this.chatLoading = true;
    this.dispose();
    this.chatState = this.readChatState();
    this.root.innerHTML = `<section class="application-chat"><aside class="application-sessions"><button class="button primary" data-app-action="new-session">${icons.plus}新会话</button><div data-app-sessions></div></aside><div class="application-chat-main"><div class="application-chat-head"><div><strong>${esc(this.app.name)}</strong><span class="application-type">${this.app.type === "llm" ? "LLM" : "Agent"}</span></div><a class="text-button" href="${route(this.id)}">应用配置</a></div><div class="application-chat-notice" hidden></div><div class="application-messages" role="log" aria-label="应用聊天记录"></div><button class="button application-latest" data-app-action="latest" hidden>回到最新</button><div class="application-interaction" hidden></div><div class="application-composer-wrap"><div class="application-composer"><textarea rows="1" data-app-input aria-label="输入消息" placeholder="发送消息…">${esc(this.chatState.draft)}</textarea><button class="application-send" data-app-action="send" aria-label="发送消息">${svg('<path d="M12 19V5m-6 6 6-6 6 6"/>')}</button><button class="application-stop" data-app-action="stop" aria-label="停止执行" hidden>${icons.stop}</button></div></div></div></section>`;
    const input = this.root.querySelector("[data-app-input]");
    input.onkeydown = (e) => {
      if (
        e.key === "Enter" &&
        !e.shiftKey &&
        !e.isComposing &&
        e.keyCode !== 229
      ) {
        e.preventDefault();
        this.send().catch((error) => this.services.toast(error.message));
      }
    };
    const messages = this.root.querySelector(".application-messages");
    messages.onscroll = () => {
      this.root
        ?.querySelector(".application-latest")
        ?.toggleAttribute(
          "hidden",
          messages.scrollHeight - messages.scrollTop - messages.clientHeight <
            160,
        );
    };
    await this.loadSessions();
    if (this.chatState.session) await this.refreshChat();
    else {
      this.chatLoading = false;
      this.renderRuns();
    }
  }
  async loadSessions() {
    const generation = this.generation;
    const data = await api("sessions", { app: this.id });
    if (generation !== this.generation) return;
    this.chatState.sessions = data.items;
    this.chatState.sessionsMore = data.has_more;
    this.renderSessions();
  }
  renderSessions() {
    const host = this.root?.querySelector("[data-app-sessions]");
    if (!host) return;
    host.innerHTML =
      this.chatState.sessions
        .map(
          (s) =>
            `<div class="application-session ${s.id === this.chatState.session ? "selected" : ""}"><button data-app-action="session" data-id="${s.id}" title="${esc(s.title)}">${esc(s.title)}</button><details><summary aria-label="会话操作">···</summary><div><button data-app-action="rename-session" data-id="${s.id}">重命名</button><button class="application-delete" data-app-action="delete-session" data-id="${s.id}">删除</button></div></details></div>`,
        )
        .join("") +
      (this.chatState.sessionsMore
        ? '<button class="text-button" data-app-action="more-sessions">加载更多</button>'
        : "");
  }
  async refreshChat() {
    clearTimeout(this.pollTimer);
    if (!this.root || this.sub !== "chat" || !this.chatState.session) return;
    const generation = this.generation,
      session = this.chatState.session;
    try {
      const data = await api("history", { app: this.id, session });
      if (generation !== this.generation || session !== this.chatState.session)
        return;
      const old = this.chatState.runs,
        first = data.items[0]?.created;
      this.chatState.runs = [
        ...old.filter((r) => first && r.created < first),
        ...data.items,
      ];
      this.chatState.more = this.chatState.more ?? data.has_more;
      this.chatLoading = false;
      this.activeRun = data.items.find((r) =>
        ["submitted", "running", "waiting", "stopping"].includes(r.status),
      );
      this.renderRuns();
      if (this.activeRun)
        this.pollTimer = setTimeout(() => this.refreshRun(), 700);
    } catch (error) {
      if (generation !== this.generation) return;
      const notice = this.root.querySelector(".application-chat-notice");
      notice.textContent = error.message;
      notice.hidden = false;
      if (this.activeRun)
        this.pollTimer = setTimeout(() => this.refreshChat(), 2000);
    }
  }
  async refreshRun() {
    clearTimeout(this.pollTimer);
    if (!this.root || !this.activeRun) return;
    const generation = this.generation,
      session = this.chatState.session,
      run = this.activeRun;
    try {
      const data = await api("run", {
        app: this.id,
        session,
        run: run.id,
        sequence: run.sequence ?? "",
      });
      if (generation !== this.generation || session !== this.chatState.session)
        return;
      if (!data.unchanged) {
        const index = this.chatState.runs.findIndex((r) => r.id === data.id);
        if (index >= 0) this.chatState.runs[index] = data;
        this.activeRun = [
          "submitted",
          "running",
          "waiting",
          "stopping",
        ].includes(data.status)
          ? data
          : null;
        this.renderRuns();
      }
      if (this.activeRun)
        this.pollTimer = setTimeout(() => this.refreshRun(), 700);
    } catch (error) {
      if (generation !== this.generation) return;
      const notice = this.root.querySelector(".application-chat-notice");
      notice.textContent = error.message;
      notice.hidden = false;
      this.pollTimer = setTimeout(() => this.refreshRun(), 2000);
    }
  }
  renderRuns() {
    const list = this.root?.querySelector(".application-messages");
    if (!list) return;
    const bottom = list.scrollHeight - list.scrollTop - list.clientHeight < 130;
    const runs = this.chatState.runs;
    list.querySelector(".application-chat-empty")?.remove();
    let earlier = list.querySelector("[data-earlier]");
    if (this.chatState.more && !earlier) {
      earlier = document.createElement("div");
      earlier.dataset.earlier = "";
      earlier.innerHTML =
        '<button class="text-button" data-app-action="more-messages">加载更早的消息</button>';
      list.prepend(earlier);
    }
    if (!this.chatState.more) earlier?.remove();
    for (const run of runs) {
      let el = list.querySelector(`[data-run="${run.id}"]`);
      if (!el) {
        el = document.createElement("section");
        el.className = "application-turn";
        el.dataset.run = run.id;
        el.innerHTML = `<article class="application-user-message"><div><div data-user-text></div><small>${stamp(run.created)}</small></div><img src="/chat-crab.jpg" alt="用户"></article><article class="application-assistant-message"><img src="/corki-icon-ears.png" alt="Corki"><div class="application-answer"><div data-process></div><div class="simple-chat-markdown" data-answer></div><div data-run-status></div><div class="application-answer-actions"><button class="text-button" data-app-action="copy" data-run="${run.id}">复制</button><time></time></div></div></article>`;
        list.append(el);
      }
      const user = el.querySelector("[data-user-text]");
      if (user.textContent !== run.input) user.textContent = run.input;
      const raw = run.messages?.length
        ? run.messages.map((m) => m.content).join("\n\n")
        : run.answer || "";
      const answer = el.querySelector("[data-answer]");
      if (answer.dataset.raw !== raw && !window.getSelection()?.toString()) {
        answer.innerHTML = window.CorkiChannelMarkdown(raw);
        answer.dataset.raw = raw;
        colorCode(answer);
      } else if (answer.dataset.raw !== raw) this.deferredRender = true;
      const process = el.querySelector("[data-process]");
      for (const [id, tool] of Object.entries(run.tools || {})) {
        let item = [...process.children].find((t) => t.dataset.tool === id);
        if (!item) {
          item = document.createElement("details");
          item.dataset.tool = id;
          item.innerHTML =
            '<summary><span></span><small></small></summary><div class="application-tool-output"></div>';
          process.append(item);
        }
        const summary = item.querySelector("summary");
        summary.querySelector("span").textContent = tool.name;
        summary.querySelector("small").textContent =
          {
            running: "执行中",
            completed: "已完成",
            failed: "失败",
            unknown: "状态未知",
          }[tool.status] || tool.status;
        item._data = tool;
        item.ontoggle = () => {
          if (item.open && !item._view) {
            const t = item._data;
            const doc =
              "参数\n" +
              t.arguments +
              "\n\n执行输出\n" +
              (t.output || "等待中");
            item._view = new EditorView({
              doc,
              parent: item.querySelector(".application-tool-output"),
              extensions: [
                basicSetup,
                javascript(),
                theme,
                colors,
                EditorState.readOnly.of(true),
                EditorView.editable.of(false),
              ],
            });
            this.codeViews.push(item._view);
          } else if (item._view) {
            const doc =
              "参数\n" +
              item._data.arguments +
              "\n\n执行输出\n" +
              (item._data.output || "等待中");
            if (item._view.state.doc.toString() !== doc)
              item._view.dispatch({
                changes: {
                  from: 0,
                  to: item._view.state.doc.length,
                  insert: doc,
                },
              });
          }
        };
        if (item.open) item.ontoggle();
      }
      const status = el.querySelector("[data-run-status]");
      const active = ["submitted", "running", "waiting", "stopping"].includes(
        run.status,
      );
      status.className = active
        ? "application-working"
        : "application-end-state";
      status.textContent = active
        ? run.status === "waiting"
          ? "等待确认"
          : run.status === "stopping"
            ? "正在停止"
            : "思考中…"
        : run.status === "cancelled"
          ? "回答已停止"
          : run.status === "failed"
            ? run.error || "运行失败"
            : raw
              ? ""
              : "模型没有返回文本";
      el.querySelector(".application-answer-actions").hidden = active || !raw;
      el.querySelector("time").textContent = stamp(run.updated);
    }
    // Preserve chronological order when older pages are prepended.
    if (!window.getSelection()?.toString()) {
      let previous = list.querySelector("[data-earlier]");
      for (const run of runs) {
        const el = list.querySelector(`[data-run="${run.id}"]`);
        if (
          previous
            ? previous.nextElementSibling !== el
            : list.firstElementChild !== el
        )
          list.insertBefore(
            el,
            previous ? previous.nextElementSibling : list.firstElementChild,
          );
        previous = el;
      }
    }
    if (!runs.length)
      list.innerHTML =
        '<div class="application-chat-empty"><img src="/corki-icon-ears.png" alt=""><h2>今天有什么新灵感吗？</h2></div>';
    const active = !!this.activeRun;
    const notice = this.root.querySelector(".application-chat-notice");
    notice.textContent = this.activeRun?.notice || "";
    notice.hidden = !notice.textContent;
    this.root.querySelector("[data-app-action=send]").hidden = active;
    this.root.querySelector("[data-app-action=send]").disabled =
      !!this.chatLoading;
    this.root.querySelector("[data-app-action=stop]").hidden = !active;
    this.root.querySelector("[data-app-input]").disabled = false;
    this.renderInteraction();
    if (bottom) list.scrollTop = list.scrollHeight;
  }
  renderInteraction() {
    const host = this.root.querySelector(".application-interaction"),
      interaction = this.activeRun?.interaction;
    const encoded = JSON.stringify(interaction);
    if (host.dataset.current === encoded) return;
    host.dataset.current = encoded;
    if (!interaction) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    host.hidden = false;
    if (interaction.kind === "question") {
      host.innerHTML =
        interaction.questions
          .map(
            (q) =>
              `<label class="application-field"><span>${esc(q.question)}</span><input data-answer-id="${esc(q.id)}" type="${q.is_secret ? "password" : "text"}" placeholder="输入回答"><small>${q.options.map((o) => esc(o.label)).join(" · ")}</small></label>`,
          )
          .join("") +
        '<button class="button primary" data-app-action="respond" data-decision="answer">提交回答</button>';
    } else {
      host.innerHTML = `<strong>需要确认</strong><p>${esc(interaction.params?.message || "Agent 请求执行操作")}</p><pre>${esc(JSON.stringify(interaction.params?.command || interaction.params, null, 2))}</pre>${interaction.params?.requestedSchema && Object.keys(interaction.params.requestedSchema.properties || {}).length ? '<label class="application-field"><span>补充数据（JSON）</span><textarea data-mcp-response rows="3"></textarea></label>' : ""}<div class="actions"><button class="button" data-app-action="respond" data-decision="decline">拒绝</button><button class="button primary" data-app-action="respond" data-decision="accept">允许本次</button></div>`;
    }
  }
  async respond(button) {
    const interaction = this.activeRun?.interaction;
    if (!interaction) return;
    button.disabled = true;
    try {
      let response;
      if (interaction.kind === "question")
        response = {
          answers: Object.fromEntries(
            [...this.root.querySelectorAll("[data-answer-id]")].map((el) => [
              el.dataset.answerId,
              { answers: [el.value] },
            ]),
          ),
        };
      else {
        const json = this.root.querySelector("[data-mcp-response]")?.value;
        if (json) response = JSON.parse(json);
      }
      await api(
        "respond",
        {
          app: this.id,
          session: this.chatState.session,
          run: this.activeRun.id,
          request: interaction.id,
          decision: button.dataset.decision,
          response,
        },
        true,
      );
      await this.refreshChat();
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  }
  async send() {
    if (this.sending || this.activeRun || this.chatLoading) return;
    const state = this.chatState,
      text = state.draft;
    if (!text.trim()) return;
    this.sending = true;
    const id = this.id;
    const draftKey = state.session || "new";
    state.request = state.request || crypto.randomUUID();
    this.saveChatState(state, id);
    const button = this.root.querySelector("[data-app-action=send]");
    button.disabled = true;
    try {
      const result = await submitTurn({
        app: id,
        session: state.session || undefined,
        text,
        request: state.request,
      });
      state.session = result.session;
      if (state.drafts[draftKey] === text) delete state.drafts[draftKey];
      if (state.draft === text) state.draft = "";
      state.request = null;
      this.saveChatState(state, id);
      if (this.root && this.id === id && this.chatState === state) {
        this.root.querySelector("[data-app-input]").value = state.draft;
        await this.loadSessions();
        await this.refreshChat();
      }
    } catch (error) {
      this.services.toast(error.message);
    } finally {
      this.sending = false;
      if (button.isConnected) button.disabled = false;
    }
  }
}
window.CorkiApplications = new ApplicationsWorkspace();
window.addEventListener("beforeunload", (event) => {
  const workspace = window.CorkiApplications;
  workspace.capture();
  if (
    workspace.editing &&
    workspace.form &&
    JSON.stringify(workspace.form) !== JSON.stringify(bodyOf(workspace.app))
  ) {
    event.preventDefault();
    event.returnValue = "";
  }
});
