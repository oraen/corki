import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { protocol, status } from "./protocol.mjs";

const channel = process.argv[2];
const modules = {
  telegram: "./telegram.mjs",
  dingtalk: "./dingtalk.mjs",
  feishu: "./feishu.mjs",
  whatsapp: "./whatsapp.mjs",
  wechat: "./wechat.mjs",
};
let adapter,
  initialized = false;
const incoming = protocol({
  async initialize(params) {
    if (initialized || params.protocol !== 1 || !modules[channel])
      throw new Error("protocol mismatch");
    initialized = true;
    const require = createRequire(params.dependencies);
    const load = async (name) =>
      import(pathToFileURL(require.resolve(name)).href);
    const module = await import(modules[channel]);
    adapter = await module.create({
      ...params, incoming, require, load,
      fatal: () => setTimeout(() => process.exit(1), 100),
    });
    // Readiness means transport connected, not merely a child process started.
    setImmediate(() =>
      adapter.start().catch(() => {
        status("error", { code: "network" });
        process.exitCode = 1;
        setTimeout(() => process.exit(1), 100);
      }),
    );
    return { protocol: 1 };
  },
  async send(params) {
    if (!adapter || typeof params.text !== "string" || !params.message)
      throw new Error("not initialized");
    const result = await adapter.send(params);
    return { sent: true, ...(result?.receipt ? {receipt:result.receipt} : {}) };
  },
  async recall(params) {
    if (!adapter || typeof adapter.recall !== "function") throw new Error("recall not supported");
    return adapter.recall(params.receipt);
  },
  async progress(params) {
    if (!adapter || !params.message || typeof adapter.progress !== "function")
      return {supported:false};
    return await adapter.progress(params);
  },
  async download_images(params) {
    if(!adapter || typeof adapter.downloadImages!=='function')throw new Error('images not supported');
    return adapter.downloadImages(params.images);
  },
  async verify(params) {
    await adapter?.verify?.(params.code);
  },
    async login(params) {
    if (channel !== "wechat" || !adapter || typeof adapter.login !== "function")
      throw new Error("interactive login not supported");
      return adapter.login({ refresh: params?.refresh === true });
  },
  async shutdown() {
    await adapter?.close?.();
    setTimeout(() => process.exit(0), 20);
  },
});
