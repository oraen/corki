import { basicSetup } from "codemirror";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, placeholder } from "@codemirror/view";
import { markdown } from "@codemirror/lang-markdown";

const groups = [
  ["corki", "扩展 Skills", "扩展"],
  ["public", "公共 Skills", "公共"],
  ["work", "工作 Skills", "工作"],
  ["evolution", "自进化 Skills", "自进化"],
  ["builtin", "内置 Skills", "内置"],
];
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);
const svg = (content) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${content}</svg>`;
const chevron = svg('<path d="m8 10 4 4 4-4"/>');
const close = svg('<path d="m6 6 12 12M6 18 18 6"/>');
const file = svg('<path d="M14 3H5v18h14V8zM14 3v5h5M8 12h8M8 16h6"/>');
const scope = (skill) => groups.find(([key]) => key === skill?.group)?.[2] || "其他";
const editorTheme = EditorView.theme({
  "&": { fontSize: "14px", backgroundColor: "#fff", color: "#20263a" },
  ".cm-scroller": { fontFamily: "inherit", lineHeight: "24px" },
  ".cm-content": { padding: "14px 0" },
  ".cm-line": { padding: "0 16px" },
  ".cm-gutters": { display: "none" },
  ".cm-activeLine": { backgroundColor: "transparent" },
  ".cm-placeholder": { color: "#697586" },
  "&.cm-focused": { outline: "none" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": { backgroundColor: "#bfdcff !important" },
  "& ::selection": { backgroundColor: "#bfdcff" },
});

// Creation owns presentation only; the workspace remains the draft/save authority.
export class ApplicationCreatePage {
  constructor(workspace, syntaxColors, icons) {
    this.workspace = workspace;
    this.root = workspace.root;
    this.syntaxColors = syntaxColors;
    this.icons = icons;
    this.skillsDialog = null;
    this.onKey = (event) => this.keydown(event);
  }
  get form() { return this.workspace.form; }
  get catalog() { return this.workspace.catalog; }
  picker(name, label, value, options, disabled = false, hint = "") {
    const selected = options.find(([id]) => id === value);
    return `<div class="app-create-field" data-create-field="${name}"><span id="app-create-${name}-label">${label}</span><details class="application-picker" data-create-picker="${name}" ${disabled ? "inert" : ""}><summary role="button" aria-haspopup="listbox" aria-label="${label}" ${disabled ? 'aria-disabled="true"' : ""}><span><span class="app-create-picker-value">${escape(selected?.[1] || value || "默认")}</span>${!value && hint ? `<small class="app-create-value-tag">${escape(hint)}</small>` : ""}</span>${chevron}</summary><div class="application-menu" role="listbox" aria-label="${label}">${options.map(([id, text]) => `<button type="button" role="option" aria-selected="${id === value}" data-create-pick="${name}" data-value="${escape(id)}">${escape(text)}${id === value ? '<span>✓</span>' : ""}</button>`).join("")}</div></details><small class="app-create-error" data-create-error="${name}" hidden></small></div>`;
  }
  modelFields() {
    const provider = this.catalog.providers.find((item) => item.id === this.form.provider);
    return this.picker("provider", "供应商", this.form.provider,
      [["", "默认"], ...this.catalog.providers.map((item) => [item.id, item.id])],
      !this.editable || !this.catalog.providers.length, this.catalog.default?.provider)
      + this.picker("model", "模型", this.form.model,
        [["", "默认"], ...(provider?.models || []).map((model) => [model, model])],
        !this.editable || !this.form.provider, provider?.default_model || provider?.models?.[0] || this.catalog.default?.model);
  }
  get editable() { return this.workspace.editing && !this.workspace.historical; }
  tabs() {
    const active = this.workspace.detailTab || "info";
    return `<div class="app-create-tabs" role="tablist" aria-label="应用配置">${[["info", "基本信息"], ["prompt", "提示词"], ...(this.workspace.id !== "new" ? [["access", "接入配置"]] : [])].map(([id, text]) => `<button type="button" role="tab" id="app-tab-${id}" aria-controls="app-panel-${id}" aria-selected="${active === id}" tabindex="${active === id ? 0 : -1}" data-app-tab="${id}">${text}</button>`).join("")}</div>`;
  }
  saveLabel() {
    return this.workspace.id === "new" ? "创建应用" : this.workspace.detailTab === "prompt" ? "保存为新版本" : "保存更改";
  }
  actionLabels() {
    const access = this.workspace.detailTab === "access";
    for (const button of this.root.querySelectorAll('.application-detail-actions [data-app-action]')) button.hidden = access;
    const save = this.root.querySelector('[data-app-action="save"]');
    if (save && !this.workspace.saves.has(this.workspace.id)) save.textContent = this.saveLabel();
    const edit = this.root.querySelector('[data-app-action="edit"]');
    if (edit) {
      edit.textContent = this.workspace.detailTab === "prompt" ? "编辑提示词" : "编辑信息";
      edit.disabled = !!this.workspace.historical && this.workspace.detailTab === "prompt";
    }
  }
  history() {
    const app = this.workspace.app;
    const versions = app.versions.filter((version) => version.prompt_changed !== false);
    const current = app.prompt_version || app.version;
    if (this.workspace.id === "new") return "";
    return `<section class="app-create-history" aria-labelledby="app-prompt-history-title"><h3 id="app-prompt-history-title">版本历史 <small>${versions.length}</small></h3><div class="app-prompt-history-list">${versions.map((version) => `<article class="${(this.workspace.historical || current) === version.version ? "selected" : ""}"><button type="button" data-app-action="version" data-version="${version.version}" ${this.workspace.editing ? "disabled" : ""} aria-current="${(this.workspace.historical || current) === version.version ? "true" : "false"}"><strong>版本 ${version.version}${version.version === current ? '<span>当前</span>' : ""}</strong><small>${escape(window.CorkiDateTime(version.created))}</small></button>${version.version === current ? "" : `<div><button type="button" class="text-button" data-app-action="diff" data-version="${version.version}" ${this.workspace.editing ? "disabled" : ""}>对比当前</button><button type="button" class="text-button" data-app-action="restore" data-version="${version.version}" ${this.workspace.editing ? "disabled" : ""}>回滚</button></div>`}</article>`).join("")}</div></section>`;
  }
  render() {
    const f = this.form;
    const creating = this.workspace.id === "new";
    const readOnly = !this.editable;
    const permissionLabels = { ":read-only": "只读", ":workspace": "工作目录读写", ":danger-full-access": "完全访问" };
    this.root.innerHTML = `<div class="application-create-page app-create ${creating ? "" : "application-detail-page"}">
      <div class="application-identity app-create-sr-only"><h2>${creating ? "创建应用" : escape(this.workspace.app.name)}</h2></div>
      <div class="app-create-save-error" role="alert" hidden></div>
      <div class="application-definition app-create-canvas">
        <div class="app-create-topbar">${this.tabs()}<div class="application-detail-actions">${creating ? "" : `<a class="button" href="#applications/${encodeURIComponent(this.workspace.id)}/chat">${this.icons.chat}对话</a>`}${this.workspace.editing ? `<button class="button" data-app-action="cancel-edit">取消</button><button class="button primary" data-app-action="save">${this.saveLabel()}</button>` : '<button class="button primary" data-app-action="edit">编辑信息</button>'}</div></div>
        <div id="app-panel-info" role="tabpanel" aria-labelledby="app-tab-info" data-app-panel="info">
          <fieldset class="app-create-type"><legend class="app-create-sr-only">应用类型</legend><span aria-hidden="true">应用类型</span>${creating ? `<div class="app-create-type-options">${["llm", "agent"].map((type) => `<label><input type="radio" name="application-type" value="${type}" ${f.type === type ? "checked" : ""}><span>${type === "llm" ? "LLM 应用" : "Agent 应用"}</span></label>`).join("")}</div>` : `<span class="app-create-fixed-type">${f.type === "llm" ? "LLM 应用" : "Agent 应用"}</span>`}<p data-create-type-help></p></fieldset>
          <div class="app-create-basic-grid">
            <section class="app-create-basics" aria-label="基本信息">
              <label class="app-create-field" data-create-field="name"><span>应用名称</span><input name="name" maxlength="80" autocomplete="off" placeholder="输入应用名称" value="${escape(f.name)}" ${readOnly ? "readonly" : ""} aria-describedby="app-create-name-error"><small class="app-create-error" id="app-create-name-error" data-create-error="name" hidden></small></label>
              <label class="app-create-field"><span>描述 <small class="app-create-optional">可选</small></span><textarea name="description" rows="3" maxlength="1000" placeholder="简要说明应用的用途" ${readOnly ? "readonly" : ""}>${escape(f.description)}</textarea></label>
            </section>
            <section class="app-create-model-section" aria-label="模型配置"><div class="app-create-models" data-create-models>${this.modelFields()}</div><p class="app-create-model-note" ${this.catalog.providers.length && !this.catalog.error ? "hidden" : ""}>${escape(this.catalog.error || "暂无可用模型，请在模型管理中添加供应商和模型。")}</p><label class="app-create-context" data-create-context><input type="checkbox" name="context" ${f.context ? "checked" : ""} ${readOnly ? "disabled" : ""}><span>携带本会话上下文</span></label></section>
          </div>
          <div class="app-create-agent" data-create-agent>
            <section class="app-create-runtime" aria-labelledby="app-create-runtime-title"><h3 id="app-create-runtime-title">运行环境</h3><label class="app-create-field"><span>工作目录</span><div class="application-directory"><input name="directory" maxlength="4096" placeholder="默认工作目录" value="${escape(f.directory)}" ${readOnly ? "readonly" : ""}><button class="icon-button" data-app-action="directory" aria-label="选择工作目录" ${readOnly ? "disabled" : ""}>${this.icons.folder}</button></div></label>${this.picker("permissions", "操作权限", f.permissions, [["", "默认"], ...this.catalog.permissions.map((permission) => [permission, permissionLabels[permission] || permission])], readOnly)}</section>
            <section class="app-create-skills" aria-labelledby="app-create-skills-title"><div class="app-create-skills-heading"><h3 id="app-create-skills-title">Skills <small data-create-skill-count></small></h3><button class="button" type="button" data-create-action="skills" ${readOnly ? "hidden" : ""}>${this.icons.plus}添加 Skills</button></div><div class="app-create-selected-skills" data-create-selected-skills></div><small class="app-create-error" data-create-error="skills" hidden></small></section>
          </div>
        </div>
        <div id="app-panel-prompt" role="tabpanel" aria-labelledby="app-tab-prompt" data-app-panel="prompt" class="app-create-prompt-layout ${creating ? "is-new" : ""}"><section class="app-create-prompt-section" aria-label="提示词"><div class="app-create-prompt-heading">${creating ? '<span class="app-create-prompt-label">提示词</span>' : `<span class="app-create-prompt-status">${this.workspace.historical ? `版本 ${this.workspace.historical}` : "当前版本"}</span>`}<span class="application-editor-count"></span></div><div class="application-prompt" data-create-field="prompt"></div><small class="app-create-error" data-create-error="prompt" id="app-create-prompt-error" hidden></small></section>${this.history()}</div>
        ${creating ? "" : '<div id="app-panel-access" role="tabpanel" aria-labelledby="app-tab-access" data-app-panel="access"><div data-app-access></div></div>'}
      </div>
    </div>`;
    this.root.addEventListener("keydown", this.onKey);
    this.workspace.editor = new EditorView({
      parent: this.root.querySelector(".application-prompt"),
      state: EditorState.create({
        doc: f.prompt,
        extensions: [basicSetup, markdown(), editorTheme, this.syntaxColors, EditorView.lineWrapping,
          EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly),
          placeholder("描述应用的角色、任务和回答要求"),
          EditorView.contentAttributes.of({ "aria-label": "应用提示词", "aria-describedby": "app-create-prompt-error", spellcheck: "false" }),
          keymap.of([{ key: "Mod-s", run: () => { if (this.editable) this.workspace.save().catch((error) => this.workspace.services.toast(error.message)); return true; } }]),
          EditorView.updateListener.of((update) => {
            if (!update.docChanged) return;
            this.form.prompt = update.state.doc.toString();
            this.changed("prompt");
            this.updateCount();
          }),
        ],
      }),
    });
    this.updateType();
    this.renderSelected();
    this.updateCount();
    this.workspace.applyDetailTab();
  }
  updateCount() {
    this.root.querySelector(".application-editor-count").textContent = `${this.form.prompt.length.toLocaleString()} 字符`;
  }
  updateType() {
    const agent = this.form.type === "agent";
    this.root.querySelector("[data-create-agent]").hidden = !agent;
    this.root.querySelector("[data-create-context]").hidden = agent;
    this.root.querySelector("[data-create-type-help]").textContent = agent ? "调用工具与 Skills 完成任务" : "通过提示词与模型生成回答";
    for (const input of this.root.querySelectorAll('[name="application-type"]')) input.checked = input.value === this.form.type;
  }
  changed(field) {
    this.workspace.capture();
    if (field) this.error(field, "");
    this.root.querySelector(".app-create-save-error").hidden = true;
  }
  input(event) {
    if (!this.editable) return false;
    if (!["name", "description", "directory"].includes(event.target.name)) return false;
    this.form[event.target.name] = event.target.value;
    this.changed(event.target.name);
    return true;
  }
  change(event) {
    if (!this.editable) return false;
    if (event.target.name === "application-type" && this.workspace.id === "new") {
      this.form.type = event.target.value;
      this.updateType();
      this.changed();
      return true;
    }
    if (event.target.name === "context") {
      this.form.context = event.target.checked;
      this.changed();
      return true;
    }
    return false;
  }
  async click(event) {
    if (!this.editable) return false;
    const option = event.target.closest("[data-create-pick]");
    if (option) {
      const name = option.dataset.createPick;
      this.form[name] = option.dataset.value;
      if (name === "provider") this.form.model = "";
      this.changed(name);
      if (name === "provider" || name === "model") {
        this.error("model", "");
        this.root.querySelector("[data-create-models]").innerHTML = this.modelFields();
      } else {
        const details = option.closest("details");
        const choices = [...details.querySelectorAll("[role=option]")].map((item) => [item.dataset.value, item.firstChild.textContent]);
        details.closest("[data-create-field]").outerHTML = this.picker(name, "操作权限", this.form[name], choices);
      }
      this.root.querySelector(`[data-create-picker="${name}"] summary`)?.focus();
      return true;
    }
    const button = event.target.closest("[data-create-action]");
    if (!button) return false;
    if (button.dataset.createAction === "skills") this.openSkills(button);
    if (button.dataset.createAction === "remove-skill") {
      this.form.skills = this.form.skills.filter((id) => id !== button.dataset.id);
      this.changed("skills");
      this.renderSelected();
      this.root.querySelector('[data-create-action="skills"]').focus();
    }
    return true;
  }
  setDirectory(directory) {
    this.form.directory = directory;
    this.root.querySelector('[name="directory"]').value = directory;
    this.changed("directory");
  }
  error(field, message) {
    const note = this.root.querySelector(`[data-create-error="${field}"]`);
    if (!note) return;
    note.textContent = message;
    note.hidden = !message;
    const control = field === "prompt" ? this.root.querySelector(".cm-content") : this.root.querySelector(`[name="${field}"], [data-create-picker="${field}"] summary`);
    if (control) control.setAttribute("aria-invalid", String(!!message));
    this.root.querySelector(`[data-create-field="${field}"]`)?.classList.toggle("has-error", !!message);
  }
  validate() {
    const errors = [];
    if (!this.form.name.trim()) errors.push(["name", "请输入应用名称"]);
    if (!this.form.prompt.trim()) errors.push(["prompt", "请填写提示词"]);
    const provider = this.catalog.providers.find((item) => item.id === this.form.provider);
    if (this.form.provider && !provider) errors.push(["provider", "供应商已不可用，请重新选择"]);
    if (this.form.model && !provider?.models.includes(this.form.model)) errors.push(["model", "模型已不可用，请重新选择"]);
    if (this.form.type === "agent" && this.form.skills.some((id) => !this.catalog.skills.some((skill) => skill.id === id))) errors.push(["skills", "部分 Skills 已不可用，请移除后重新选择"]);
    for (const field of ["name", "prompt", "provider", "model", "skills"]) this.error(field, "");
    for (const [field, message] of errors) this.error(field, message);
    if (errors.length) {
      const field = errors[0][0];
      this.workspace.setDetailTab(field === "prompt" ? "prompt" : "info");
      if (field === "prompt") this.workspace.editor.focus();
      else this.root.querySelector(`[name="${field}"], [data-create-picker="${field}"] summary, [data-create-action="skills"]`)?.focus();
    }
    return !errors.length;
  }
  saveError(error) {
    const note = this.root.querySelector(".app-create-save-error");
    note.textContent = error.message || "创建失败，请重试";
    note.hidden = false;
    if (error.message === "应用名称已存在") this.error("name", error.message);
  }
  busy(pending) {
    const button = this.root.querySelector('[data-app-action="save"]');
    if (button) button.innerHTML = pending ? '<span class="spinner"></span>保存中…' : this.saveLabel();
    if (pending) this.skillsDialog?.close();
  }
  renderSelected() {
    this.root.querySelector("[data-create-skill-count]").textContent = this.form.skills.length || "";
    this.root.querySelector("[data-create-selected-skills]").innerHTML = this.form.skills.length
      ? this.form.skills.map((id) => {
        const skill = this.catalog.skills.find((item) => item.id === id);
        return `<div class="app-create-selected-skill ${skill ? "" : "is-missing"}">${file}<span>${escape(skill?.name || id)}</span><small>${skill ? scope(skill) : "已不可用"}</small>${this.editable ? `<button type="button" data-create-action="remove-skill" data-id="${escape(id)}" aria-label="移除 ${escape(skill?.name || id)}">${close}</button>` : ""}</div>`;
      }).join("")
      : '<p class="app-create-skills-empty">添加应用需要使用的 Skills</p>';
  }
  openSkills(trigger) {
    if (this.skillsDialog || !this.editable) return;
    const dialog = document.createElement("dialog");
    this.skillsDialog = dialog;
    this.scopeFilter = "all";
    this.skillQuery = "";
    this.skillLimit = 100;
    const groupOptions = [["all", "全部"], ...groups.map(([id, name]) => [id, name])];
    if (this.catalog.skills.some((skill) => !groups.some(([id]) => id === skill.group))) groupOptions.push(["other", "其他 Skills"]);
    dialog.className = "app-create-skill-dialog";
    dialog.setAttribute("aria-labelledby", "app-create-skill-dialog-title");
    dialog.innerHTML = `<div class="app-create-skill-dialog-head"><h2 id="app-create-skill-dialog-title">选择 Skills</h2><button type="button" data-skill-close aria-label="关闭">${close}</button></div><div class="app-create-skill-dialog-body"><div class="app-create-skill-categories" role="tablist" aria-label="Skills 类型" aria-orientation="vertical">${groupOptions.map(([id, name]) => `<button type="button" role="tab" data-skill-scope="${id}" aria-selected="${id === "all"}" tabindex="${id === "all" ? 0 : -1}">${name}<small>${this.catalog.skills.filter((skill) => id === "all" || skill.group === id || (id === "other" && !groups.some(([key]) => key === skill.group))).length}</small></button>`).join("")}</div><div class="app-create-skill-results"><label class="app-create-skill-search">${svg('<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>')}<input type="search" aria-label="搜索 Skills" placeholder="搜索 Skills" data-create-skill-search></label><div class="app-create-skill-list" role="region" aria-label="Skills 列表" tabindex="0"></div></div></div><div class="app-create-skill-dialog-foot"><span data-skill-selected-count></span><span class="app-create-error" data-skill-limit-error hidden>最多选择 100 个 Skills</span><button class="button primary" data-skill-close type="button">完成</button></div>`;
    document.body.append(dialog);
    dialog.oninput = (event) => {
      if (!event.target.matches("[data-create-skill-search]")) return;
      this.skillQuery = event.target.value.trim().toLocaleLowerCase();
      this.skillLimit = 100;
      this.updateSkillList();
    };
    dialog.onchange = (event) => {
      if (event.target.name !== "create-skill") return;
      const selected = new Set(this.form.skills);
      const checked = event.target.checked;
      if (checked && selected.size >= 100 && !selected.has(event.target.value)) {
        event.target.checked = false;
        dialog.querySelector("[data-skill-limit-error]").hidden = false;
        return;
      }
      if (checked) selected.add(event.target.value); else selected.delete(event.target.value);
      this.form.skills = [...selected];
      dialog.querySelector("[data-skill-limit-error]").hidden = true;
      this.changed("skills");
      this.renderSelected();
      this.updateSkillCount();
    };
    dialog.onclick = (event) => {
      const tab = event.target.closest("[data-skill-scope]");
      if (tab) {
        this.scopeFilter = tab.dataset.skillScope;
        this.skillLimit = 100;
        for (const item of dialog.querySelectorAll("[data-skill-scope]")) {
          item.setAttribute("aria-selected", String(item === tab));
          item.tabIndex = item === tab ? 0 : -1;
        }
        this.updateSkillList();
      }
      if (event.target.closest("[data-skill-close]")) dialog.close();
      if (event.target.closest("[data-skill-more]")) { this.skillLimit += 100; this.updateSkillList(); }
      if (event.target === dialog) {
        const box = dialog.getBoundingClientRect();
        if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
      }
    };
    dialog.onkeydown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        dialog.close();
        return;
      }
      if (!event.target.matches("[data-skill-scope]")) return;
      const tabs = [...dialog.querySelectorAll("[data-skill-scope]")];
      const index = tabs.indexOf(event.target);
      let next;
      if (event.key === "ArrowDown") next = (index + 1) % tabs.length;
      if (event.key === "ArrowUp") next = (index + tabs.length - 1) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); tabs[next].click(); tabs[next].focus(); }
    };
    dialog.addEventListener("close", () => {
      this.skillsDialog = null;
      dialog.remove();
      if (trigger.isConnected) trigger.focus();
    }, { once: true });
    this.updateSkillList();
    dialog.showModal();
    dialog.querySelector("input").focus();
  }
  updateSkillCount() {
    this.skillsDialog.querySelector("[data-skill-selected-count]").textContent = `已选择 ${this.form.skills.length} 项`;
  }
  updateSkillList() {
    const skills = this.catalog.skills.filter((skill) => {
      const category = groups.find(([id]) => id === skill.group)?.[1] || "其他 Skills";
      return (this.scopeFilter === "all" || skill.group === this.scopeFilter || (this.scopeFilter === "other" && category === "其他 Skills"))
        && `${skill.name} ${category}`.toLocaleLowerCase().includes(this.skillQuery);
    });
    const selected = new Set(this.form.skills);
    const list = this.skillsDialog.querySelector(".app-create-skill-list");
    list.innerHTML = skills.length ? skills.slice(0, this.skillLimit).map((skill) => `<label class="app-create-skill-option">${file}<span><strong>${escape(skill.name)}</strong><small>${escape(groups.find(([id]) => id === skill.group)?.[1] || "其他 Skills")}</small></span><input type="checkbox" name="create-skill" value="${escape(skill.id)}" ${selected.has(skill.id) ? "checked" : ""}></label>`).join("") + (skills.length > this.skillLimit ? '<button class="button" type="button" data-skill-more>显示更多</button>' : "")
      : `<p class="app-create-skill-no-results">${this.catalog.skills.length ? "没有匹配的 Skills" : "暂无可用 Skills"}</p>`;
    this.updateSkillCount();
  }
  keydown(event) {
    const details = event.target.closest("[data-create-picker]");
    if (!details) return;
    const summary = details.querySelector("summary");
    const options = [...details.querySelectorAll("[role=option]")];
    if (event.key === "Escape" && details.open) {
      event.preventDefault(); details.open = false; summary.focus();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const index = options.indexOf(document.activeElement);
      details.open = true;
      const next = index < 0 ? (event.key === "ArrowDown" ? 0 : options.length - 1) : (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
      options[next]?.focus();
    } else if ((event.key === "Home" || event.key === "End") && details.open) {
      event.preventDefault(); options[event.key === "Home" ? 0 : options.length - 1]?.focus();
    }
  }
  destroy() {
    this.root.removeEventListener("keydown", this.onKey);
    this.skillsDialog?.close();
  }
}
