"use strict";
window.CorkiResourcePackages = (() => {
  const labels = {
    skills: "Skills",
    mcp: "MCP",
    hooks: "Hooks",
    resources: "本地资源",
  };
  const symbol = () =>
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5 9-5Z M3 8v9l9 5 9-5V8 M12 13v9 M7.5 5.5l9 5"/></svg>';
  const transferIcon = (name) => {
    const path =
      name === "upload"
        ? "M12 16V3m-5 5 5-5 5 5 M4 16v5h16v-5"
        : "M12 3v13m-5-5 5 5 5-5 M4 17v4h16v-4";
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`;
  };
  let root = null,
    epoch = 0,
    rows = [],
    selected = new Set(),
    detailId = null;
  const formatBytes = (n) =>
    n < 1024
      ? `${n} B`
      : n < 1024 * 1024
        ? `${(n / 1024).toFixed(1)} KB`
        : `${(n / 1024 / 1024).toFixed(1)} MB`;
  const request = (action, data = {}) =>
    api("mutate", { kind: "resource-packages", action, ...data });
  function download(ids) {
    const link = document.createElement("a");
    link.href =
      "/api/resource-package-export?" +
      ids.map((id) => "id=" + encodeURIComponent(id)).join("&");
    link.download = "";
    document.body.append(link);
    link.click();
    link.remove();
  }
  async function load() {
    const ticket = ++epoch;
    try {
      const data = await api("resource-packages");
      if (ticket !== epoch || !root?.isConnected) return;
      rows = data.packages;
      selected = new Set(
        [...selected].filter((id) => rows.some((row) => row.id === id)),
      );
      if (detailId) await drawDetail();
      else draw();
    } catch (error) {
      if (ticket === epoch && root)
        root.innerHTML = `<section class="panel package-empty">${symbol()}<p>${esc(error.message)}</p><button class="button" id="package-retry">重试</button></section>`;
      root?.querySelector("#package-retry")?.addEventListener("click", load);
    }
  }
  function updateSelection() {
    const button = root?.querySelector("#package-export-selected");
    if (button) {
      button.disabled = !selected.size;
      button.innerHTML =
        transferIcon("download") +
        `批量导出${selected.size ? "（" + selected.size + "）" : ""}`;
    }
  }
  function draw() {
    root.innerHTML = `<section class="panel package-panel"><div class="package-heading"><div class="package-title"><span class="package-mark">${symbol()}</span><h2>插件管理<span class="package-count">${rows.length}</span></h2></div><div class="package-actions"><button type="button" class="button" id="package-export-selected" disabled>${transferIcon("download")}批量导出</button><button type="button" class="button" id="package-create">${icon("plus")}创建插件</button><button type="button" class="button primary" id="package-import">${transferIcon("upload")}导入插件</button></div></div>${
      rows.length
        ? `<div class="package-grid">${rows
            .map(
              (row) =>
                `<article class="package-card" data-package-open="${row.id}" tabindex="0" role="link" aria-label="打开插件 ${esc(row.name)}"><div class="package-card-top"><span class="package-mark">${symbol()}</span><div class="package-card-name"><h3 title="${esc(row.name)}">${esc(row.name)}</h3><time>${esc(when(row.created))}</time></div><label class="package-check package-card-check"><input type="checkbox" data-package-select="${row.id}" aria-label="选择 ${esc(row.name)}" ${selected.has(row.id) ? "checked" : ""}></label></div>${row.description ? `<p class="package-description">${esc(row.description)}</p>` : ""}<div class="package-tags">${Object.entries(
                  labels,
                )
                  .filter(([kind]) =>
                    kind === "resources" ? row.resources : row[kind]?.length,
                  )
                  .map(
                    ([kind, label]) =>
                      `<span>${label}<b>${kind === "resources" ? row.resources : row[kind].length}</b></span>`,
                  )
                  .join(
                    "",
                  )}</div><div class="package-card-bottom"><small>${formatBytes(row.bytes)}</small><div><button type="button" class="text-button" data-package-action="apply" data-id="${row.id}">导入资源</button><button type="button" class="text-button" data-package-action="export" data-id="${row.id}">导出</button><button type="button" class="text-button danger" data-package-action="delete" data-id="${row.id}">删除</button></div></div></article>`,
            )
            .join("")}</div>`
        : `<div class="package-empty"><span>${symbol()}</span><h3>暂无插件</h3><p>导入资源包，或将现有资源保存为插件</p></div>`
    }</section>`;
    root.querySelector("#package-create").onclick = () =>
      create().catch((error) => toast(error.message));
    root.querySelector("#package-import").onclick = () => importDialog();
    root.querySelector("#package-export-selected").onclick = () =>
      download([...selected]);
    root.querySelectorAll("[data-package-select]").forEach(
      (input) =>
        (input.onchange = () => {
          input.checked
            ? selected.add(input.dataset.packageSelect)
            : selected.delete(input.dataset.packageSelect);
          updateSelection();
        }),
    );
    root.querySelectorAll("[data-package-action]").forEach(
      (button) =>
        (button.onclick = () => {
          const row = rows.find((item) => item.id === button.dataset.id);
          if (button.dataset.packageAction === "export") download([row.id]);
          else if (button.dataset.packageAction === "delete") remove(row);
          else importDialog({ id: row.id });
        }),
    );
    root.querySelectorAll("[data-package-open]").forEach((card) => {
      const open = (event) => {
        if (event.target.closest("button,input,label,a")) return;
        location.hash = "#plugins/" + card.dataset.packageOpen;
      };
      card.onclick = open;
      card.onkeydown = (event) => {
        if (event.target === card && event.key === "Enter") {
          event.preventDefault();
          open(event);
        }
      };
    });
    updateSelection();
  }
  async function drawDetail() {
    const ticket = epoch,
      node = root;
    const data = await request("detail", { id: detailId });
    if (ticket !== epoch || node !== root || !node?.isConnected) return;
    const crumb = document.querySelector("#crumb");
    if (crumb) crumb.textContent = data.name;
    root.innerHTML = `<section class="panel package-panel package-detail"><div class="package-heading"><div class="package-title"><span class="package-mark">${symbol()}</span><div><h2>${esc(data.name)}</h2>${data.description ? `<p class="package-detail-description">${esc(data.description)}</p>` : ""}</div></div><div class="package-actions"><button class="button" id="package-detail-export">${transferIcon("download")}导出</button><button class="button primary" id="package-add">${icon("plus")}添加组件</button></div></div><div class="package-detail-sections">${[
      ...Object.keys(labels),
    ]
      .map((kind) => {
        const items = kind === "resources" ? data.files : data[kind];
        return `<section class="package-detail-group"><div class="package-detail-group-heading"><h3>${labels[kind]}<span>${items.length}</span></h3></div>${items.length ? `<div class="package-component-list">${items.map((item) => `<div class="package-component"><span class="package-component-icon">${kind === "resources" ? transferIcon("download") : symbol()}</span><div><strong title="${esc(item.name)}">${esc(item.name)}</strong>${kind === "resources" ? `<small>${formatBytes(item.bytes)}</small>` : ""}</div><button class="text-button danger" data-remove-component data-kind="${kind}" data-name="${esc(item.name)}" aria-label="移除 ${esc(item.name)}">移除</button></div>`).join("")}</div>` : '<div class="package-group-empty">暂无' + labels[kind] + "</div>"}</section>`;
      })
      .join("")}</div></section>`;
    root.querySelector("#package-detail-export").onclick = () =>
      download([data.id]);
    root.querySelector("#package-add").onclick = () =>
      create(data).catch((error) => toast(error.message));
    root.querySelectorAll("[data-remove-component]").forEach(
      (button) =>
        (button.onclick = () => {
          showDialog(
            "移除组件",
            `<div class="package-delete"><span class="package-delete-mark">${icon("trash")}</span><h3>移除「${esc(button.dataset.name)}」？</h3><p>将从此插件中移除，已安装的资源不受影响。</p></div>`,
            async () => {
              await request("update", {
                id: data.id,
                revision: data.digest,
                remove: { [button.dataset.kind]: [button.dataset.name] },
              });
              toast("组件已移除");
              await load();
            },
            "",
          );
          $("#save").textContent = "移除";
        }),
    );
  }
  async function encode(file, path) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let text = "";
    for (let i = 0; i < bytes.length; i += 32768)
      text += String.fromCharCode(...bytes.subarray(i, i + 32768));
    return {
      path: path || file.webkitRelativePath || file.name,
      data: btoa(text),
    };
  }
  async function dropped(event) {
    const entries = [...event.dataTransfer.items]
      .filter((item) => item.kind === "file")
      .map((item) => item.webkitGetAsEntry?.())
      .filter(Boolean);
    if (!entries.some((entry) => entry.isDirectory))
      return Promise.all(
        [...event.dataTransfer.files].map((file) => encode(file)),
      );
    const result = [];
    async function walk(entry, prefix = "") {
      if (entry.isFile) {
        const file = await new Promise((resolve, reject) =>
          entry.file(resolve, reject),
        );
        result.push(await encode(file, prefix + file.name));
        return;
      }
      const reader = entry.createReader();
      while (true) {
        const children = await new Promise((resolve, reject) =>
          reader.readEntries(resolve, reject),
        );
        if (!children.length) break;
        for (const child of children)
          await walk(child, prefix + entry.name + "/");
      }
    }
    for (const entry of entries) await walk(entry);
    return result;
  }
  function uploadHTML() {
    return `<div class="package-drop" tabindex="0" role="button" aria-label="上传文件或目录"><span>${transferIcon("upload")}</span><div><strong>将文件或目录拖入该区域</strong><span>支持 ZIP、Skills 目录、MCP / Hooks 配置及本地资源</span><div class="package-upload-actions"><button class="text-button" type="button" data-package-file>选择文件</button><button class="text-button" type="button" data-package-folder>选择目录</button></div></div><input type="file" multiple hidden data-package-files><input type="file" multiple webkitdirectory hidden data-package-folders></div><div class="package-uploaded" aria-live="polite"></div>`;
  }
  function bindUpload(callback) {
    const zone = $(".package-drop"),
      list = $(".package-uploaded");
    let uploadVersion = 0;
    const receive = async (promise) => {
      const version = ++uploadVersion;
      let ready = false;
      $("#save").disabled = true;
      list.innerHTML = '<span class="spinner"></span>正在读取文件…';
      try {
        const files = await promise;
        if (version !== uploadVersion || !zone.isConnected) return;
        ready = (await callback(files)) !== false;
        if (!zone.isConnected) return;
        list.innerHTML = files.length
          ? `<div><span>${files.length} 个文件</span><button type="button" class="text-button" data-package-clear>移除</button></div><ul>${files
              .slice(0, 5)
              .map(
                (file) =>
                  `<li title="${esc(file.path)}">${esc(file.path)}</li>`,
              )
              .join(
                "",
              )}${files.length > 5 ? `<li class="subtle">另有 ${files.length - 5} 个文件</li>` : ""}</ul>`
          : "";
        list
          .querySelector("[data-package-clear]")
          ?.addEventListener("click", () => receive(Promise.resolve([])));
      } catch (error) {
        $("#form-error").textContent = error.message;
        list.innerHTML = "";
      } finally {
        if (zone.isConnected && version === uploadVersion)
          $("#save").disabled = !ready;
      }
    };
    zone.querySelector("[data-package-file]").onclick = () =>
      zone.querySelector("[data-package-files]").click();
    zone.querySelector("[data-package-folder]").onclick = () =>
      zone.querySelector("[data-package-folders]").click();
    zone
      .querySelectorAll("input[type=file]")
      .forEach(
        (input) =>
          (input.onchange = () =>
            receive(Promise.all([...input.files].map((file) => encode(file))))),
      );
    zone.ondragover = (event) => {
      event.preventDefault();
      zone.classList.add("dragover");
    };
    zone.ondragleave = () => zone.classList.remove("dragover");
    zone.ondrop = (event) => {
      event.preventDefault();
      zone.classList.remove("dragover");
      receive(dropped(event));
    };
    zone.onkeydown = (event) => {
      if (event.target === zone && ["Enter", " "].includes(event.key)) {
        event.preventDefault();
        zone.querySelector("[data-package-files]").click();
      }
    };
  }
  function resourceChoices(data) {
    const groups = [
      {
        kind: "skills",
        title: "扩展 Skills",
        items: data.skills.filter((item) => item.group === "corki"),
      },
      {
        kind: "skills",
        title: "公共 Skills",
        items: data.skills.filter((item) => item.group === "public"),
      },
      { kind: "mcp", title: "MCP", items: data.mcp },
      { kind: "hooks", title: "Hooks", items: data.hooks },
    ];
    return groups
      .map(
        (group) =>
          `<section class="package-resource-section"><h3>${group.title}<small>${group.items.length}</small></h3><div class="package-resource-cards">${group.items.length ? group.items.map((item) => `<label class="package-resource-option package-resource-tile package-check ${item.valid === false ? "is-unavailable" : ""}" data-package-name="${esc(item.name.toLowerCase())}"><span class="package-resource-monogram" aria-hidden="true">${esc(item.name.slice(0, 2).toUpperCase())}</span><span class="package-resource-copy"><strong title="${esc(item.name)}">${esc(item.name)}</strong>${item.description ? `<small>${esc(item.description)}</small>` : ""}</span><input type="checkbox" name="resource:${group.kind}" value="${esc(item.id)}" aria-label="选择 ${esc(item.name)}" ${item.valid === false ? "disabled" : ""}></label>`).join("") : '<p class="package-resource-empty">暂无' + group.title + "</p>"}</div></section>`,
      )
      .join("");
  }
  async function create(target = null) {
    const data = await api("resource-package-inventory");
    if (target)
      for (const kind of ["skills", "mcp", "hooks"])
        data[kind] = data[kind].filter(
          (item) =>
            !target[kind].some(
              (saved) => saved.name.toLowerCase() === item.name.toLowerCase(),
            ),
        );
    if (!root?.isConnected) return;
    let files = [];
    showDialog(
      target ? "添加组件" : "创建插件",
      `<div class="package-create-form">${target ? "" : `<div class="field-grid">${field("package_name", "插件名称", "", "text", "", false, "输入插件名称")}${field("package_description", "描述", "", "text", "", false, "可选")}</div>`}<div class="package-resource-heading"><strong>选择资源</strong><label class="search">${icon("search")}<input type="search" placeholder="搜索资源" aria-label="搜索资源" id="package-resource-search"></label></div><div class="package-resource-groups">${resourceChoices(data)}</div><div class="package-local-heading"><strong>本地文件</strong><span class="subtle">可选</span></div>${uploadHTML()}</div>`,
      async (form) => {
        const selection = Object.fromEntries(
          ["skills", "mcp", "hooks"].map((kind) => [
            kind,
            form.getAll("resource:" + kind),
          ]),
        );
        const result = await request(target ? "update" : "create", {
          ...(target ? { id: target.id, revision: target.digest } : {}),
          name: form.get("package_name"),
          description: form.get("package_description"),
          ...(Object.values(selection).some((items) => items.length)
            ? { selection }
            : {}),
          files,
        });
        toast(result.message);
        await load();
      },
      "",
    );
    $("#save").textContent = target ? "添加" : "创建插件";
    if (!target) {
      $("[name=package_name]").required = true;
      $("[name=package_name]").maxLength = 300;
      $("[name=package_description]").maxLength = 1000;
    }
    $("#package-resource-search").oninput = (event) => {
      const query = event.target.value.toLowerCase();
      $("#fields")
        .querySelectorAll("[data-package-name]")
        .forEach(
          (item) => (item.hidden = !item.dataset.packageName.includes(query)),
        );
    };
    bindUpload((items) => {
      files = items;
    });
  }
  function planHTML(plan) {
    return `<div class="package-import-heading"><span class="package-mark">${symbol()}</span><div><strong>${esc(plan.name)}</strong>${plan.description ? `<p>${esc(plan.description)}</p>` : ""}</div></div><div class="package-import-columns"><section><h3>新增资源 <span>${plan.available.length}</span></h3>${plan.available.length ? `<ul>${plan.available.map((item) => `<li><span>${labels[item.kind]}</span><strong>${esc(item.name)}</strong></li>`).join("")}</ul>` : '<div class="package-plan-empty">没有可新增的资源</div>'}</section><section><h3>同名跳过 <span>${plan.skipped.length}</span></h3>${plan.skipped.length ? `<ul>${plan.skipped.map((item) => `<li><span>${labels[item.kind]}</span><strong>${esc(item.name)}</strong></li>`).join("")}</ul>` : '<div class="package-plan-empty">没有同名资源</div>'}</section></div>${!plan.can_import ? '<div class="package-duplicate-notice">所有资源均已存在，无法重复导入插件</div>' : ""}`;
  }
  async function importDialog(saved) {
    let payload = saved || null,
      previewVersion = 0;
    const refresh = async (input) => {
      const ticket = ++previewVersion;
      payload = null;
      $("#save").disabled = true;
      $("#package-import-preview").innerHTML =
        '<div class="package-loading"><span class="spinner"></span>正在检查资源…</div>';
      try {
        const plan = await request("preview-import", input);
        if (ticket !== previewVersion || !$("#editor").open) return;
        payload = input;
        $("#package-import-preview").innerHTML = planHTML(plan);
        $("#save").disabled = !plan.can_import;
        $("#form-error").textContent = "";
        return plan.can_import;
      } catch (error) {
        if (ticket === previewVersion && $("#editor").open) {
          $("#package-import-preview").innerHTML = "";
          $("#form-error").textContent = error.message;
        }
        return false;
      }
    };
    showDialog(
      "导入插件",
      `<div class="package-import-form">${saved ? "" : uploadHTML()}<div id="package-import-preview"></div><label class="package-check package-enable-after"><input type="checkbox" name="enable_after_import"><span>导入后开启</span></label></div>`,
      async (form) => {
        if (!payload) throw Error("请选择插件 ZIP 或目录");
        const result = await request("import", {
          ...payload,
          enable_after_import: form.get("enable_after_import") === "on",
        });
        toast(result.message);
        await load();
      },
      "",
    );
    $("#save").textContent = "导入";
    $("#save").disabled = true;
    if (saved) await refresh(saved);
    else
      bindUpload(async (files) => {
        if (!files.length) {
          previewVersion++;
          payload = null;
          $("#package-import-preview").innerHTML = "";
          return false;
        }
        if (files.length === 1 && files[0].path.toLowerCase().endsWith(".zip"))
          return refresh({ archive: files[0].data });
        else return refresh({ files });
      });
  }
  function remove(row) {
    showDialog(
      "删除插件",
      `<div class="package-delete"><span class="package-delete-mark">${icon("trash")}</span><h3>删除「${esc(row.name)}」？</h3><p>删除后无法恢复。已导入的资源不受影响。</p></div>`,
      async () => {
        await request("delete", { id: row.id });
        selected.delete(row.id);
        toast("插件已永久删除");
        await load();
      },
      "",
    );
    $("#save").textContent = "删除";
    $("#save").classList.add("package-danger-button");
    $("#editor").addEventListener(
      "close",
      () => $("#save").classList.remove("package-danger-button"),
      { once: true },
    );
  }
  return {
    mount(node, options = {}) {
      root = node;
      detailId = options.id || null;
      load();
    },
    unmount() {
      epoch++;
      root = null;
    },
    icon: symbol,
  };
})();
