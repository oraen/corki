import { EditorView, basicSetup } from "codemirror";
import { EditorState } from "@codemirror/state";
import { json } from "@codemirror/lang-json";
const contextEvents = [
  "SessionStart",
  "SubagentStart",
  "UserPromptSubmit",
  "PreToolUse",
  "PostToolUse",
];
const typeLabels = {
  command: "本地命令",
  mcp_tool: "MCP 工具",
  prompt: "提示词",
  skill: "Skills",
};
const summary = (h) =>
  h.command ||
  h.prompt ||
  h.path?.split("/").slice(-2, -1)[0] ||
  `${h.server} / ${h.tool}`;
const labels = {
  SessionStart: ["会话开始", "启动或恢复会话时"],
  SessionEnd: ["会话结束", "会话关闭时"],
  UserPromptSubmit: ["提交提问", "用户提交输入时"],
  PreToolUse: ["工具执行前", "检查或调整工具输入"],
  PostToolUse: ["工具执行后", "处理工具执行结果"],
  PreCompact: ["压缩前", "上下文压缩之前"],
  PostCompact: ["压缩后", "上下文压缩之后"],
  SubagentStart: ["子 Agent 开始", "子 Agent 启动时"],
  SubagentStop: ["子 Agent 结束", "子 Agent 完成时"],
  Stop: ["回答结束", "Agent 准备结束本轮时"],
  Interrupt: ["执行中断", "用户停止执行时"],
};
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const symbol = (name) =>
  window.document.querySelector("#nav") ? icon(name) : "";
function picker(name, title, options, value) {
  return `<div class="field protocol-field hook-select"><span>${title}</span><input type="hidden" name="${name}" value="${esc(value)}"><button type="button" class="select-trigger" aria-haspopup="listbox" aria-expanded="false"><span class="select-current">${esc(options.find((o) => o[0] === value)?.[1] || "请选择")}</span>${symbol("chevron")}</button><div class="select-menu" role="listbox" hidden>${options.map(([v, t]) => `<button type="button" role="option" tabindex="-1" data-value="${esc(v)}" aria-selected="${v === value}"><strong>${esc(t)}</strong></button>`).join("")}</div></div>`;
}
class HooksWorkspace {
  constructor() {
    this.generation = 0;
    this.root = null;
    this.editor = null;
  }
  unmount() {
    this.generation++;
    this.root = null;
  }
  mount(root) {
    this.root = root;
    this.load();
  }
  async load() {
    const gen = ++this.generation;
    this.root.innerHTML =
      '<div class="panel hooks-loading"><span class="spinner"></span>正在读取 Hooks…</div>';
    try {
      const data = await api("hooks");
      if (gen !== this.generation) return;
      this.data = data;
      this.draw();
    } catch (e) {
      if (gen === this.generation)
        this.root.innerHTML = `<div class="panel hooks-loading">${esc(e.message)}<button class="button" id="hooks-retry">重试</button></div>`;
      this.root
        ?.querySelector("#hooks-retry")
        ?.addEventListener("click", () => this.load());
    }
  }
  draw() {
    if (!this.root) return;
    const rows = this.data.entries;
    this.root.innerHTML = `<section class="panel hooks-heading"><div class="hooks-global"><span class="skill-group-icon">${symbol("hooks")}</span><div><strong>Hooks</strong><span class="subtle">${rows.length} 个配置</span></div><button class="toggle ${this.data.enabled ? "on" : ""}" role="switch" aria-label="启用 Hooks" aria-checked="${this.data.enabled}" data-hk="global"></button></div><button class="button primary" data-hk="new">${symbol("plus")}添加 Hook</button></section>${!this.data.enabled ? '<div class="notice">Hooks 总开关已关闭，配置会保留。</div>' : ""}${rows.length ? `<div class="skill-grid hooks-grid">${rows.map((r, i) => `<article class="skill-card ${r.enabled ? "" : "is-disabled"}"><div class="skill-card-top"><span class="skill-avatar">${symbol("hooks")}</span><div class="skill-card-name"><button class="skill-name" data-hk="edit" data-index="${i}">${esc(r.handler.statusMessage || labels[r.event]?.[0] || r.event)}</button><span class="subtle">${esc(r.event)}</span></div><button class="toggle ${r.enabled ? "on" : ""}" role="switch" aria-label="启用 ${esc(r.handler.statusMessage || r.event)}" aria-checked="${r.enabled}" data-hk="toggle" data-index="${i}"></button></div><p class="hooks-command" title="${esc(summary(r.handler))}">${esc(summary(r.handler))}</p><div class="hooks-tags"><span class="badge">${typeLabels[r.handler.type] || r.handler.type}</span><span class="subtle">${esc(r.matcher || "全部")} · ${r.handler.async ? "异步" : "同步"}</span></div>${r.error ? `<p class="hooks-error">${esc(r.error)}</p>` : !r.trusted ? '<p class="subtle">待确认启用</p>' : ""}<div class="skill-card-bottom"><span>${r.source === "settings" ? "settings.json" : "hooks.json"}</span><button class="text-button danger" data-hk="delete" data-index="${i}">删除</button></div></article>`).join("")}</div>` : '<section class="panel hooks-empty">' + symbol("hooks") + "<strong>暂无 Hooks</strong><p>在会话、工具执行等事件中运行自定义操作</p></section>"}`;
    this.root.onclick = (e) => {
      const b = e.target.closest("[data-hk]");
      if (!b) return;
      const r = rows[Number(b.dataset.index)];
      if (b.dataset.hk === "new" || b.dataset.hk === "edit")
        this.edit(b.dataset.hk === "edit" ? r : null);
      if (b.dataset.hk === "delete") this.remove(r);
      if (b.dataset.hk === "toggle")
        this.change({ action: "toggle", id: r.id, enabled: !r.enabled }, b);
      if (b.dataset.hk === "global")
        this.change({ action: "global", enabled: !this.data.enabled }, b);
    };
  }
  payload(data) {
    return {
      kind: "hooks",
      revision: this.data.revision,
      file_revision: this.data.file_revision,
      ...data,
    };
  }
  async change(data, b) {
    if (b) b.disabled = true;
    try {
      const result = await api("mutate", this.payload(data));
      toast(result.message);
      if (this.root) await this.load();
    } catch (e) {
      toast(e.message);
      if (b) b.disabled = false;
    }
  }
  remove(row) {
    const request = this.payload({ action: "delete", id: row.id });
    showDialog(
      "删除 Hook",
      `<div class="hooks-delete"><strong>${esc(row.handler.statusMessage || row.event)}</strong><p>删除后无法恢复，确认删除？</p></div>`,
      async () => {
        await api("mutate", request);
        toast("Hook 已删除");
        if (this.root) await this.load();
      },
      "",
    );
    document.querySelector("#save").textContent = "删除";
  }
  edit(row) {
    const h = row?.handler || { type: "command" },
      event = row?.event || "UserPromptSubmit";
    const originalPayload = {
      revision: this.data.revision,
      file_revision: this.data.file_revision,
    };
    const field = (name, label, value = "", placeholder = "") =>
      `<label class="field"><span>${label}</span><input name="${name}" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off"></label>`;
    let uploadedPath =
      h.path && !(this.data.skills || []).some((s) => s.path === h.path)
        ? h.path
        : "";
    const availableSkills = this.data.skills || [];
    const skillOptions = availableSkills.map((s) => [
      s.path,
      `${s.name} · ${{ builtin: "内置", corki: "扩展", public: "公共" }[s.group] || "项目"}`,
    ]);
    showDialog(
      row ? "编辑 Hook" : "添加 Hook",
      `<div class="hooks-form"><div class="field-grid">${picker(
        "event",
        "触发事件",
        this.data.events.map((e) => [e, `${labels[e][0]} · ${e}`]),
        event,
      )}${picker(
        "type",
        "执行方式",
        [
          ["command", "本地命令"],
          ["mcp_tool", "MCP 工具"],
          ["prompt", "提示词"],
          ["skill", "Skills"],
        ],
        h.type,
      )}</div><p class="hooks-event-description" id="hooks-event-description"></p>${field("statusMessage", "名称", h.statusMessage || "", "可选，默认使用事件名称")}<div id="hooks-command-field"><label class="field"><span>执行命令</span><textarea name="command" rows="3" spellcheck="false" placeholder="python3 /path/to/hook.py">${esc(h.command || "")}</textarea></label></div><div id="hooks-prompt-field"><label class="field"><span>提示词</span><textarea name="prompt" rows="5" placeholder="例如：回答前先核实关键事实，引用资料时标明来源。">${esc(h.prompt || "")}</textarea></label></div><div id="hooks-skill-field"><div class="mcp-transports"><button type="button" class="mcp-transport" data-skill-mode="existing">选择已有 Skills</button><button type="button" class="mcp-transport" data-skill-mode="upload">上传 Skills</button></div><div id="hook-existing-skill">${picker("skill_path", "选择 Skills", skillOptions.length ? skillOptions : [["", "暂无已启用的 Skills"]], h.path || skillOptions[0]?.[0] || "")}</div><div id="hook-upload-skill"><div class="hook-skill-upload" id="hook-skill-drop"><strong>将技能包拖入该区域</strong><div class="actions"><label class="button">选择文件夹<input id="hook-skill-folder" type="file" webkitdirectory multiple hidden></label><label class="button">选择 ZIP 或 SKILL.md<input id="hook-skill-file" type="file" accept=".zip,.md" hidden></label></div></div><p id="hook-skill-upload-status" aria-live="polite">${uploadedPath ? "已保存的私有 Skill" : ""}</p></div></div><div id="hooks-mcp-fields"><div class="field-grid">${picker("server", "MCP 服务", state.tools?.length ? state.tools.map((s) => [s.id, s.id]) : [["", "暂无 MCP 服务"]], h.server || state.tools?.[0]?.id || "")}${field("tool", "工具名称", h.tool || "")}</div><div class="field"><span>工具参数 · JSON</span><div id="hooks-json-editor"></div></div></div><details class="mcp-advanced"><summary>更多配置</summary><div class="field-grid">${field("matcher", "匹配条件", row?.matcher || "*", "* 表示全部")}${field("timeout", "超时（秒）", h.timeout ?? "", "默认使用运行时配置")}</div><div class="hooks-checks"><label><input type="checkbox" name="async" ${h.async ? "checked" : ""}>异步执行</label></div></details><div class="hooks-checks"><label><input type="checkbox" name="enabled" ${!row || row.enabled ? "checked" : ""}>启用此 Hook</label><span class="subtle">启用即信任此配置，届时自动执行</span></div></div>`,
      async (form) => {
        const event = form.get("event"),
          type = form.get("type");
        const handler = { ...h, type };
        for (const key of [
          "command",
          "commandWindows",
          "command_windows",
          "async",
          "server",
          "tool",
          "input",
          "prompt",
          "path",
        ])
          delete handler[key];
        if (type === "command") {
          handler.command = String(form.get("command")).trim();
          handler.async =
            event === "SessionEnd" ? false : form.get("async") === "on";
          if (h.commandWindows) handler.commandWindows = h.commandWindows;
          if (h.command_windows) handler.command_windows = h.command_windows;
        } else if (type === "prompt") {
          handler.prompt = String(form.get("prompt")).trim();
        } else if (type === "skill") {
          handler.path =
            skillMode === "upload" ? uploadedPath : form.get("skill_path");
          if (!handler.path) throw Error("请先选择或上传 Skill");
        } else {
          handler.server = form.get("server");
          handler.tool = String(form.get("tool")).trim();
          try {
            handler.input = JSON.parse(this.editor.state.doc.toString());
          } catch {
            throw Error("工具参数不是有效的 JSON");
          }
        }
        const timeout = ["prompt", "skill"].includes(type)
          ? ""
          : String(form.get("timeout")).trim();
        if (timeout) {
          if (!/^\d+$/.test(timeout)) throw Error("超时需为非负整数秒");
          handler.timeout = Number(timeout);
        } else delete handler.timeout;
        const title = String(form.get("statusMessage")).trim();
        if (title) handler.statusMessage = title;
        else delete handler.statusMessage;
        await api("mutate", {
          kind: "hooks",
          ...originalPayload,
          action: "save",
          id: row?.id,
          event,
          matcher: String(form.get("matcher")).trim() || "*",
          handler,
          enabled: form.get("enabled") === "on",
        });
        toast("Hook 已保存");
        if (this.root) await this.load();
      },
      "新会话使用更新后的配置",
    );
    const dialog = document.querySelector("#editor");
    dialog.querySelectorAll(".hook-select").forEach(bindCustomSelect);
    this.editor?.destroy();
    this.editor = new EditorView({
      state: EditorState.create({
        doc: JSON.stringify(h.input || {}, null, 2),
        extensions: [
          basicSetup,
          json(),
          EditorView.theme({
            "&": { fontSize: "12px", maxHeight: "220px" },
            ".cm-scroller": { overflow: "auto" },
            "&.cm-focused": { outline: "none" },
            ".cm-content": { minHeight: "90px" },
          }),
        ],
      }),
      parent: document.querySelector("#hooks-json-editor"),
    });
    let skillMode = uploadedPath ? "upload" : "existing";
    const setSkillMode = (mode) => {
      skillMode = mode;
      dialog.querySelector("#hook-existing-skill").hidden = mode !== "existing";
      dialog.querySelector("#hook-upload-skill").hidden = mode !== "upload";
      dialog
        .querySelectorAll("[data-skill-mode]")
        .forEach((b) =>
          b.classList.toggle("active", b.dataset.skillMode === mode),
        );
    };
    dialog
      .querySelectorAll("[data-skill-mode]")
      .forEach((b) => (b.onclick = () => setSkillMode(b.dataset.skillMode)));
    setSkillMode(skillMode);
    const base64 = (f) =>
      new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(",")[1]);
        reader.onerror = () => reject(Error("读取文件失败"));
        reader.readAsDataURL(f);
      });
    let uploadGeneration = 0;
    const discard = (path) => {
      if (path && path !== h.path)
        void api("mutate", {
          kind: "hooks",
          action: "discard-upload",
          path,
        }).catch(() => {});
    };
    const upload = async (files) => {
      const gen = ++uploadGeneration;
      discard(uploadedPath);
      uploadedPath = "";
      const status = dialog.querySelector("#hook-skill-upload-status"),
        save = document.querySelector("#save");
      save.disabled = true;
      status.textContent = "正在导入…";
      try {
        files = [...files].filter(
          (f) =>
            !(f.webkitRelativePath || f.name)
              .split("/")
              .some((p) => [".DS_Store", "__MACOSX"].includes(p)),
        );
        if (!files.length) throw Error("请选择技能文件");
        const upload =
          files.length === 1 && files[0].name.toLowerCase().endsWith(".zip")
            ? { archive: await base64(files[0]) }
            : {
                files: await Promise.all(
                  files.map(async (f) => ({
                    path: f.webkitRelativePath || f.name,
                    data: await base64(f),
                  })),
                ),
              };
        const result = await api("mutate", {
          kind: "hooks",
          action: "upload-skill",
          ...originalPayload,
          upload,
        });
        if (gen !== uploadGeneration || !dialog.open) {
          discard(result.path);
          return;
        }
        uploadedPath = result.path;
        status.textContent = result.name + " · 已导入";
      } catch (e) {
        if (gen === uploadGeneration) status.textContent = e.message;
      } finally {
        if (gen === uploadGeneration && dialog.open) save.disabled = false;
      }
    };
    dialog.querySelector("#hook-skill-folder").onchange = (e) =>
      upload(e.target.files);
    dialog.querySelector("#hook-skill-file").onchange = (e) =>
      upload(e.target.files);
    const zone = dialog.querySelector("#hook-skill-drop");
    zone.ondragover = (e) => e.preventDefault();
    zone.ondrop = (e) => {
      e.preventDefault();
      if (
        [...e.dataTransfer.items].some(
          (i) => i.webkitGetAsEntry?.()?.isDirectory,
        )
      ) {
        dialog.querySelector("#hook-skill-upload-status").textContent =
          "文件夹请使用选择文件夹按钮";
        return;
      }
      upload(e.dataTransfer.files);
    };
    dialog.addEventListener(
      "close",
      () => {
        uploadGeneration++;
        discard(uploadedPath);
      },
      { once: true },
    );
    const update = () => {
      const type = dialog.querySelector("[name=type]").value;
      const contextType = ["prompt", "skill"].includes(type);
      const eventInput = dialog.querySelector("[name=event]");
      if (contextType && !contextEvents.includes(eventInput.value)) {
        eventInput.value = "UserPromptSubmit";
        eventInput.parentElement.querySelector(".select-current").textContent =
          "提交提问 · UserPromptSubmit";
      }
      const event = eventInput.value;
      eventInput.parentElement
        .querySelectorAll("[role=option]")
        .forEach((o) => {
          o.hidden = contextType && !contextEvents.includes(o.dataset.value);
          o.disabled = o.hidden;
        });
      document.querySelector("#hooks-prompt-field").hidden = type !== "prompt";
      document.querySelector("#hooks-skill-field").hidden = type !== "skill";
      dialog.querySelector("[name=timeout]").closest("label").hidden =
        contextType;
      dialog.querySelector("[name=async]").closest(".hooks-checks").hidden =
        type !== "command";
      document.querySelector("#hooks-command-field").hidden =
        type !== "command";
      document.querySelector("#hooks-mcp-fields").hidden = type !== "mcp_tool";
      const async = dialog.querySelector("[name=async]");
      async.disabled = type !== "command" || event === "SessionEnd";
      document.querySelector("#hooks-event-description").textContent =
        labels[event][1];
    };
    dialog.querySelector("[name=event]").onchange = update;
    dialog.querySelector("[name=type]").onchange = update;
    update();
    dialog.addEventListener(
      "close",
      () => {
        this.editor?.destroy();
        this.editor = null;
      },
      { once: true },
    );
  }
}
window.CorkiHooks = new HooksWorkspace();
