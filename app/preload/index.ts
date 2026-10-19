import { contextBridge, ipcRenderer, webUtils } from "electron";
import { METHODS, type DesktopAPI, type HostEvent } from "../shared/protocol";
async function invoke(channel: string, ...args: unknown[]): Promise<any> {
  try {
    return await ipcRenderer.invoke(channel, ...args);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(
      message.replace(
        /^Error invoking remote method 'corki:[^']+': (?:Error: )?/,
        "",
      ),
    );
  }
}
const api: DesktopAPI = {
  request(method, params = {}) {
    if (!METHODS.includes(method))
      return Promise.reject(new Error("Unsupported method"));
    return invoke("corki:request", method, params);
  },
  subscribe(callback) {
    const listener = (_event: unknown, value: HostEvent) => callback(value);
    ipcRenderer.on("corki:event", listener);
    return () => ipcRenderer.removeListener("corki:event", listener);
  },
  takeLaunchDirectory: () => invoke("corki:launch-directory"),
  chooseDirectory: () => invoke("corki:directory"),
  chooseFiles: () => invoke("corki:files"),
  importFiles: (files) =>
    invoke(
      "corki:import-files",
      files.map((file) => webUtils.getPathForFile(file)),
    ),
  copy: (text) => invoke("corki:copy", text),
  openLink: (url) => invoke("corki:link", url),
  restartBackend: () => invoke("corki:restart"),
  restartApp: () => invoke("corki:restart-app"),
  configurePython: () => invoke("corki:python"),
};
contextBridge.exposeInMainWorld("corki", Object.freeze(api));
