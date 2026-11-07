import { BrowserWindow, shell } from "electron";
import type { SoundKind } from "./sound-notifications";

// Dedicated audio renderer stays alive when the chat is reloaded or minimized.
// No preload, Node access, external URLs, subprocesses, or console windows.
export class NotificationAudio {
  private window?: BrowserWindow;
  private ready?: Promise<void>;
  private generation = 0;
  private stopped = false;
  private pending?: SoundKind;
  private playing = false;
  private lastFallback = 0;
  private prepare() {
    if (this.ready) return this.ready;
    const window = new BrowserWindow({
      show: false, focusable: false, skipTaskbar: true, width: 1, height: 1,
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false,
        backgroundThrottling: false, autoplayPolicy: "no-user-gesture-required" },
    });
    this.window = window;
    window.setMenu(null);
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-navigate", event => event.preventDefault());
    window.on("closed", () => {
      if (this.window === window) { this.window = undefined; this.ready = undefined; }
    });
    this.ready = window.loadURL("corki://desktop/notification-audio.html").catch(error => {
      if (!window.isDestroyed()) window.destroy();
      throw error;
    });
    return this.ready;
  }
  play(kind: SoundKind) {
    if (this.stopped) return;
    const priority = { complete: 0, attention: 1, error: 2 };
    if (!this.pending || priority[kind] > priority[this.pending]) this.pending = kind;
    if (!this.playing) void this.drain();
  }
  private async drain() {
    this.playing = true;
    const generation = this.generation;
    try {
      while (this.pending && !this.stopped && generation === this.generation) {
        await this.prepare();
        if (this.stopped || generation !== this.generation) break;
        const kind = this.pending;
        this.pending = undefined;
        let timeout: ReturnType<typeof setTimeout> | undefined;
        try {
          await Promise.race([
            this.window!.webContents.executeJavaScript(`window.playNotification(${JSON.stringify(kind)})`),
            new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error("Audio timeout")), 2500); }),
          ]);
        } finally { clearTimeout(timeout); }
      }
    } catch {
      this.pending = undefined;
      // A broken audio device/renderer must not affect the Agent or flood alerts.
      if (!this.stopped && generation === this.generation && Date.now() - this.lastFallback > 2000) {
        this.lastFallback = Date.now();
        try { shell.beep(); } catch {}
      }
      if (this.window && !this.window.isDestroyed()) this.window.destroy();
    } finally {
      this.playing = false;
      if (this.pending && !this.stopped) void this.drain();
    }
  }
  stop() {
    this.generation++;
    this.pending = undefined;
    if (this.window && !this.window.isDestroyed())
      void this.window.webContents.executeJavaScript("window.stopNotification?.()").catch(() => {});
  }
  dispose() {
    this.stopped = true;
    this.suspend();
  }
  suspend() {
    this.stop();
    if (this.window && !this.window.isDestroyed()) this.window.destroy();
  }
}
