import {
  app,
  BrowserWindow,
  ipcMain,
  dialog,
  clipboard,
  shell,
  protocol,
  session,
  Menu,
  screen,
} from "electron";
import path from "node:path";
import os from "node:os";
import { readFile, writeFile, mkdir, stat, rename } from "node:fs/promises";
import { existsSync, watchFile, unwatchFile } from "node:fs";
import { randomUUID } from "node:crypto";
import { Bridge } from "./bridge";
import { ManagerLauncher } from "./manager";
import { saveAsset, readAsset, readBoundedFile } from "./assets";
import { SoundNotifications } from "./sound-notifications";
import { NotificationAudio } from "./notification-audio";
import { METHODS, type Attachment } from "../shared/protocol";

const origin = "corki://desktop";
app.setName("Corki");
protocol.registerSchemesAsPrivileged([
  {
    scheme: "corki",
    privileges: { standard: true, secure: true, supportFetchAPI: true },
  },
]);
const home = path.resolve(
  process.env.CORKI_HOME || path.join(os.homedir(), ".corki"),
);
const ownData = path.join(home, "app");
app.setPath("userData", path.join(ownData, "electron"));
let window: BrowserWindow | null = null,
  bridge: Bridge | null = null,
  quitting = false,
  quitPending = false;
let preferences: any = {};
let soundEnabled = true;
let soundSettingsRevision = 0;
const soundConfigPath = path.join(home, "settings.json");
async function refreshSoundSettings() {
  const revision = ++soundSettingsRevision;
  try {
    const value = await bridge?.request("notifications.read");
    if (!value || revision !== soundSettingsRevision) return;
    soundEnabled = value.enabled;
    sounds.setEnabled(soundEnabled);
    if (!soundEnabled) notificationAudio.stop();
  } catch { /* Keep the last valid preference while the backend is unavailable. */ }
}
const notificationAudio = new NotificationAudio();
const sounds = new SoundNotifications(kind => {
  // Match Codex's default Unfocused policy, checked at delivery time.
  const foreground = window && !window.isDestroyed() && window.isVisible() &&
    !window.isMinimized() && window.isFocused();
  if (!foreground && !quitting && !quitPending && soundEnabled)
    notificationAudio.play(kind);
});
const prefPath = path.join(ownData, "window.json");
async function persist() {
  await mkdir(ownData, { recursive: true });
  const temp = prefPath + "." + randomUUID() + ".tmp";
  await writeFile(temp, JSON.stringify(preferences), { mode: 0o600 });
  await rename(temp, prefPath);
}
function launchDirectory(argv: string[]): string | null {
  const index = argv.indexOf("--corki-directory");
  const value = index >= 0 ? argv[index + 1] : undefined;
  return value &&
    value.length <= 4096 &&
    !/[\0\r\n]/.test(value) &&
    path.isAbsolute(value)
    ? value
    : null;
}
let pendingDirectory = launchDirectory(process.argv);
function selectedPython() {
  const root = app.getAppPath(),
    development = path.resolve(root, "..");
  const configured = preferences.python || process.env.CORKI_PYTHON;
  const local = path.join(
    development,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
  const python =
    configured ||
    (!app.isPackaged && existsSync(local)
      ? local
      : process.platform === "win32"
        ? "python"
        : "python3");
  return python;
}
function makeBridge() {
  const root = app.getAppPath();
  const python = selectedPython();
  const script = app.isPackaged
    ? path.join(process.resourcesPath, "bridge", "main.py")
    : path.join(root, "bridge", "main.py");
  const host = new Bridge(python, script, home, home);
  host.on("event", (event) => {
    sounds.accept(event);
    if (window && !window.isDestroyed())
      window.webContents.send("corki:event", event);
  });
  host.on("offline", (message) => {
    sounds.disconnected();
    if (window && !window.isDestroyed())
      window.webContents.send("corki:event", {
        type: "host.offline",
        data: { message },
        sequence: 0,
        instance: "",
      });
  });
  return host;
}
const manager = new ManagerLauncher({
  python: selectedPython,
  script: app.isPackaged
    ? path.join(process.resourcesPath, "manager", "server.py")
    : path.resolve(app.getAppPath(), "..", "manager", "server.py"),
  home,
  executable: app.isPackaged ? process.execPath : "",
  open: (url) => shell.openExternal(url),
  starting: () => {
    if (window && !window.isDestroyed())
      window.webContents.send("corki:manager-starting");
  },
});
function trusted(event: Electron.IpcMainInvokeEvent) {
  if (
    !window ||
    event.sender !== window.webContents ||
    event.senderFrame !== window.webContents.mainFrame ||
    !event.senderFrame.url.startsWith(origin + "/")
  )
    throw new Error("Untrusted sender");
}
async function importFiles(paths: unknown): Promise<Attachment[]> {
  if (!Array.isArray(paths) || paths.length > 8)
    throw new Error("最多添加 8 个附件");
  let total = 0;
  const result: Attachment[] = [];
  for (const p of paths) {
    if (typeof p !== "string" || !path.isAbsolute(p))
      throw new Error("文件路径无效");
    const info = await stat(p);
    total += info.size;
    if (!info.isFile() || info.size > 1000000 || total > 1200000)
      throw new Error("附件总大小需小于 1.2 MB");
    const ext = path.extname(p).toLowerCase(),
      name = path.basename(p),
      buffer = await readBoundedFile(p, 1_000_000);
    const mime: Record<string, string> = {
      ".png": "image/png",
      ".jpg": "image/jpeg",
      ".jpeg": "image/jpeg",
      ".webp": "image/webp",
      ".gif": "image/gif",
    };
    if (buffer.length !== info.size)
      throw new Error("文件在读取时发生变化，请重新添加");
    if (mime[ext]) {
      const assetId = await saveAsset(home, buffer, mime[ext]);
      result.push({
        id: randomUUID(),
        name,
        type: "image",
        assetId,
        data: `${origin}/attachments/${assetId}`,
      });
    } else if (
      [
        ".txt",
        ".md",
        ".json",
        ".csv",
        ".log",
        ".py",
        ".js",
        ".ts",
        ".html",
        ".css",
        ".yaml",
        ".yml",
        ".xml",
      ].includes(ext)
    ) {
      const data = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
      if (data.includes("\0") || data.length > 200000)
        throw new Error("文本附件过大或不是有效文本");
      const assetId = await saveAsset(home, buffer, "text/plain");
      result.push({ id: randomUUID(), name, type: "text", data: "", assetId });
    } else throw new Error("暂不支持此文件类型，请使用文本或图片");
  }
  return result;
}
function registerIPC() {
  ipcMain.handle("corki:window-state", (e) => {
    trusted(e);
    return {maximized: window!.isMaximized() || window!.isFullScreen()};
  });
  ipcMain.handle("corki:window-control", (e, action: unknown) => {
    trusted(e);
    if (process.platform !== "win32" || typeof action !== "string" || !["minimize", "maximize", "close"].includes(action))
      throw new Error("Unsupported window action");
    const target = window!;
    if (action === "minimize") target.minimize();
    else if (action === "maximize") {
      if (target.isFullScreen()) target.setFullScreen(false);
      else if (target.isMaximized()) target.unmaximize();
      else target.maximize();
    } else target.close();
    return {maximized: !target.isDestroyed() && (target.isMaximized() || target.isFullScreen())};
  });
  ipcMain.handle("corki:launch-directory", async (e) => {
    trusted(e);
    const directory = pendingDirectory;
    if (!directory) return null;
    if (!(await stat(directory)).isDirectory())
      throw new Error("启动工作目录不存在");
    if (pendingDirectory === directory) pendingDirectory = null;
    return directory;
  });
  ipcMain.handle("corki:request", async (e, method, params) => {
    trusted(e);
    if (
      !METHODS.includes(method) ||
      !params ||
      typeof params !== "object" ||
      Array.isArray(params)
    )
      throw new Error("Invalid request");
    try {
      return await bridge!.request(method, params);
    } catch (error) {
      if (method === "turn.submit") sounds.submissionFailed(String(params.requestId || ""));
      throw error;
    }
  });
  ipcMain.handle("corki:directory", async (e) => {
    trusted(e);
    const r = await dialog.showOpenDialog(window!, {
      properties: ["openDirectory", "createDirectory"],
    });
    return r.canceled ? null : r.filePaths[0];
  });
  ipcMain.handle("corki:files", async (e) => {
    trusted(e);
    const r = await dialog.showOpenDialog(window!, {
      properties: ["openFile", "multiSelections"],
    });
    return r.canceled ? [] : importFiles(r.filePaths);
  });
  ipcMain.handle("corki:import-files", async (e, paths) => {
    trusted(e);
    return importFiles(paths);
  });
  ipcMain.handle("corki:copy", (e, value) => {
    trusted(e);
    if (typeof value !== "string" || value.length > 16000000)
      throw new Error("Invalid text");
    clipboard.writeText(value);
  });
  ipcMain.handle("corki:manager-open", async (e) => {
    trusted(e);
    await manager.open();
  });
  ipcMain.handle("corki:link", async (e, value) => {
    trusted(e);
    if (typeof value !== "string" || value.length > 8000)
      throw new Error("Invalid URL");
    const url = new URL(value);
    if (
      !["https:", "http:"].includes(url.protocol) ||
      url.username ||
      url.password
    )
      throw new Error("不支持此链接");
    await shell.openExternal(url.href);
  });
  ipcMain.handle("corki:restart-app", async (e) => {
    trusted(e);
    if (quitPending || quitting) return false;
    quitPending = true;
    try {
      const status = await bridge?.request("status").catch(() => null);
      if (!status || status.running.length) {
        const answer = await dialog.showMessageBox(window!, {
          type: "question",
          message: status ? "停止运行中的任务并重启 Corki？" : "无法确认任务状态，仍要重启 Corki？",
          buttons: ["取消", "重启"], defaultId: 0, cancelId: 0,
        });
        if (answer.response !== 1) return false;
      }
      await persist();
      await bridge?.close();
      quitting = true;
      app.relaunch();
      setTimeout(() => app.quit(), 0);
      return true;
    } finally {
      if (!quitting) quitPending = false;
    }
  });
  ipcMain.handle("corki:restart", async (e) => {
    trusted(e);
    const status = await bridge!.request("status").catch(() => null);
    if (status?.running.length) throw new Error("仍有任务运行，请先停止任务");
    await bridge!.close();
    bridge = makeBridge();
    await bridge.start();
  });
  ipcMain.handle("corki:python", async (e) => {
    trusted(e);
    const status = await bridge!.request("status").catch(() => null);
    if (status?.running.length) throw new Error("请先停止运行中的任务");
    const result = await dialog.showOpenDialog(window!, {
      title: "选择已安装 Corki 的 Python 解释器",
      properties: ["openFile", "showHiddenFiles"],
    });
    if (result.canceled) return false;
    preferences.python = result.filePaths[0];
    await persist();
    await bridge!.close();
    bridge = makeBridge();
    await bridge.start();
    return true;
  });
}
function createWindow() {
  const saved: Record<string, number> = {};
  for (const key of ["x", "y", "width", "height"]) {
    const value = preferences.bounds?.[key];
    if (
      typeof value === "number" &&
      Number.isFinite(value) &&
      Math.abs(value) <= 1_000_000
    )
      saved[key] = Math.round(value);
  }
  const display = screen.getDisplayMatching({
    x: saved.x || 0,
    y: saved.y || 0,
    width: saved.width || 1280,
    height: saved.height || 840,
  }).workArea;
  const width = Math.min(Math.max(saved.width || 1280, 960), display.width),
    height = Math.min(Math.max(saved.height || 840, 640), display.height);
  window = new BrowserWindow({
    width,
    height,
    x: Math.max(
      display.x,
      Math.min(saved.x ?? display.x + 40, display.x + display.width - width),
    ),
    y: Math.max(
      display.y,
      Math.min(saved.y ?? display.y + 40, display.y + display.height - height),
    ),
    minWidth: 900,
    minHeight: 620,
    show: false,
    title: "Corki",
    backgroundColor: "#fcfbf9",
    icon: path.join(__dirname, "resources/corki.png"),
    ...(process.platform === "win32" ? {frame: false} : {}),
    ...(process.platform === "darwin"
      ? {
          titleBarStyle: "hiddenInset" as const,
          trafficLightPosition: { x: 20, y: 18 },
        }
      : {}),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => {
    if (url !== origin + "/index.html") event.preventDefault();
  });
  window.webContents.on("will-attach-webview", (event) =>
    event.preventDefault(),
  );
  window.once("ready-to-show", () => window?.show());
  const publishWindowState = () => {
    if (window && !window.isDestroyed())
      window.webContents.send("corki:window-state", {
        maximized: window.isMaximized() || window.isFullScreen(),
      });
  };
  if (process.platform === "win32") {
    window.on("maximize", publishWindowState);
    window.on("unmaximize", publishWindowState);
    window.on("enter-full-screen", publishWindowState);
    window.on("leave-full-screen", publishWindowState);
  }
  window.on("close", () => {
    if (window && !window.isMaximized() && !window.isFullScreen()) {
      preferences.bounds = window.getBounds();
      void persist().catch(() => {});
    }
  });
  window.on("closed", () => {
    window = null;
    if (process.platform !== "darwin") notificationAudio.suspend();
  });
  window.webContents.on("render-process-gone", () => {
    if (window && !quitting) void window.loadURL(origin + "/index.html");
  });
  void window.loadURL(origin + "/index.html");
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", (_event, argv) => {
    const directory = launchDirectory(argv);
    if (directory) pendingDirectory = directory;
    if (!window) createWindow();
    if (window?.isMinimized()) window.restore();
    window?.show();
    window?.focus();
    if (directory && window && !window.webContents.isLoading())
      window.webContents.send("corki:event", {
        type: "desktop.launch",
        data: {},
      });
  });
  app.whenReady().then(async () => {
    // BrowserWindow.icon does not set the macOS Dock icon, especially when
    // launched through the development Electron executable.
    if (process.platform === "darwin")
      app.dock?.setIcon(path.join(__dirname, "resources/corki.png"));
    await mkdir(ownData, { recursive: true });
    try {
      const loaded = JSON.parse(await readFile(prefPath, "utf8"));
      if (loaded && typeof loaded === "object" && !Array.isArray(loaded))
        preferences = loaded;
      if (typeof preferences.python !== "string") delete preferences.python;
    } catch {}
    protocol.handle("corki", async (request) => {
      const url = new URL(request.url);
      if (url.host === "desktop" && url.pathname.startsWith("/attachments/")) {
        try {
          const { buffer, mime } = await readAsset(
            home,
            url.pathname.slice("/attachments/".length),
          );
          return new Response(new Uint8Array(buffer), {
            headers: {
              "Content-Type": mime,
              "X-Content-Type-Options": "nosniff",
              "Cache-Control": "private, max-age=3600",
            },
          });
        } catch {
          return new Response("Attachment unavailable", { status: 404 });
        }
      }
      const target = path.resolve(
        __dirname,
        "." + decodeURIComponent(url.pathname),
      );
      if (url.host !== "desktop" || !target.startsWith(__dirname + path.sep))
        return new Response("Forbidden", { status: 403 });
      try {
        const types: Record<string, string> = {
          ".html": "text/html; charset=utf-8",
          ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8",
          ".png": "image/png",
          ".jpg": "image/jpeg",
          ".svg": "image/svg+xml",
          ".woff2": "font/woff2",
        };
        const data = await readFile(target);
        return new Response(data, {
          headers: {
            "Content-Type":
              types[path.extname(target)] || "application/octet-stream",
            "Cache-Control": "no-store",
          },
        });
      } catch {
        return new Response("Not found", { status: 404 });
      }
    });
    session.defaultSession.setPermissionRequestHandler(
      (_web, _permission, callback) => callback(false),
    );
    session.defaultSession.webRequest.onBeforeRequest((details, callback) =>
      callback({
        cancel:
          !details.url.startsWith(origin + "/") &&
          !details.url.startsWith("data:") &&
          !details.url.startsWith("devtools:"),
      }),
    );
    Menu.setApplicationMenu(
      process.platform === "win32" ? null : Menu.buildFromTemplate([
        {
          label: "Corki",
          submenu: [
            { role: "about" },
            { type: "separator" },
            { role: "hide" },
            { role: "hideOthers" },
            { role: "unhide" },
            { type: "separator" },
            { role: "quit" },
          ],
        },
        {
          label: "编辑",
          submenu: [
            { role: "undo" },
            { role: "redo" },
            { type: "separator" },
            { role: "cut" },
            { role: "copy" },
            { role: "paste" },
            { role: "selectAll" },
          ],
        },
        {
          label: "视图",
          submenu: [
            { role: "resetZoom" },
            { role: "zoomIn" },
            { role: "zoomOut" },
            { role: "togglefullscreen" },
            ...(!app.isPackaged ? [{ role: "toggleDevTools" as const }] : []),
          ],
        },
      ]),
    );
    bridge = makeBridge();
    watchFile(soundConfigPath, { persistent: false, interval: 500 }, () => {
      if (!quitting && !quitPending) void refreshSoundSettings();
    });
    void refreshSoundSettings();
    registerIPC();
    createWindow();
  });
  app.on("activate", () => {
    if (!window) createWindow();
    if (window?.isMinimized()) window.restore();
    window?.show();
    window?.focus();
  });
  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
  app.on("will-quit", () => {
    unwatchFile(soundConfigPath);
    sounds.dispose();
    notificationAudio.dispose();
  });
  app.on("before-quit", (event) => {
    if (quitting) return;
    event.preventDefault();
    if (quitPending) return;
    quitPending = true;
    void (async () => {
      const status = await bridge?.request("status").catch(() => null);
      if (status?.running.length) {
        const result = await dialog.showMessageBox({
          type: "question",
          message: "退出并停止运行中的任务？",
          buttons: ["继续运行", "停止并退出"],
          defaultId: 0,
          cancelId: 0,
        });
        if (result.response === 0) {
          quitPending = false;
          // Windows/Linux close their last window before before-quit runs.
          // Cancelling exit must restore a visible controller for live tasks.
          if (!window && process.platform !== "darwin") createWindow();
          return;
        }
      }
      quitting = true;
      await bridge?.close();
      app.quit();
    })();
  });
}
