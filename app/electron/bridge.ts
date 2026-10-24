import { spawn, ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { StringDecoder } from "node:string_decoder";
import { randomUUID } from "node:crypto";
import { MessageAssembler } from "./framing";
import type { Method } from "../shared/protocol";

const MAX = 2 * 1024 * 1024;
export class Bridge extends EventEmitter {
  private child: ChildProcessWithoutNullStreams | null = null;
  private pending = new Map<
    string,
    {
      resolve: (value: any) => void;
      reject: (reason: Error) => void;
      timer: NodeJS.Timeout;
      method: Method;
    }
  >();
  private starting: Promise<void> | null = null;
  private stopped = false;
  constructor(
    private python: string,
    private script: string,
    private home: string,
    private cwd: string,
  ) {
    super();
  }
  start(): Promise<void> {
    if (this.starting) return this.starting;
    this.stopped = false;
    this.starting = this.launch().catch(async (error) => {
      await this.close();
      this.starting = null;
      throw error;
    });
    return this.starting;
  }
  private async launch() {
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      PYTHONIOENCODING: "utf-8",
      // Installed application bundles are immutable, including bridge imports.
      PYTHONDONTWRITEBYTECODE: "1",
      CORKI_HOME: this.home,
    };
    delete env.ELECTRON_RUN_AS_NODE;
    const child = spawn(this.python, ["-u", this.script, "--home", this.home], {
      cwd: this.cwd,
      env,
      stdio: "pipe",
      windowsHide: true,
    });
    this.child = child;
    const assembler = new MessageAssembler(() => {
      this.fail(new Error("服务消息接收超时"));
      child.kill();
    });
    const decoder = new StringDecoder("utf8");
    let buffer = "";
    child.stdout.on("data", (chunk: Buffer) => {
      buffer += decoder.write(chunk);
      if (Buffer.byteLength(buffer) > MAX && !buffer.includes("\n")) {
        this.fail(new Error("服务消息超过大小限制"));
        child.kill();
        return;
      }
      let at: number;
      while ((at = buffer.indexOf("\n")) >= 0) {
        const raw = buffer.slice(0, at);
        buffer = buffer.slice(at + 1);
        if (Buffer.byteLength(raw) > MAX) {
          this.fail(new Error("服务消息过大"));
          child.kill();
          return;
        }
        try {
          const message: any = assembler.accept(raw);
          if (message === undefined) continue;
          if (message.jsonrpc !== "2.0") throw Error("Invalid RPC");
          if (message.method === "event") {
            const event = message.params;
            if (
              !event ||
              typeof event !== "object" ||
              typeof event.type !== "string" ||
              typeof event.instance !== "string" ||
              !Number.isSafeInteger(event.sequence) ||
              event.sequence < 0 ||
              !event.data ||
              typeof event.data !== "object" ||
              Array.isArray(event.data) ||
              (event.threadId != null && typeof event.threadId !== "string")
            )
              throw new Error("Invalid event envelope");
            this.emit("event", event);
            continue;
          }
          const entry = this.pending.get(message.id);
          if (!entry) continue;
          clearTimeout(entry.timer);
          this.pending.delete(message.id);
          message.error
            ? entry.reject(
                new Error(
                  (entry.method === "turn.submit" ? "提交被拒绝：" : "") +
                    String(message.error.message),
                ),
              )
            : entry.resolve(message.result);
        } catch {
          assembler.clear();
          this.fail(new Error("服务协议异常"));
          child.kill();
          return;
        }
      }
      if (Buffer.byteLength(buffer) > MAX) {
        this.fail(new Error("服务消息超过大小限制"));
        child.kill();
      }
    });
    child.stdin.on("error", () => this.fail(new Error("服务连接已断开")));
    // Never forward raw stderr to the renderer (it may contain secrets or tool output).
    child.stderr.on("data", () => {});
    child.on("error", () =>
      this.fail(
        new Error(
          "无法启动 Corki Python 环境，请选择已安装 Corki 的 Python 解释器。",
        ),
      ),
    );
    child.on("exit", () => {
      assembler.clear();
      if (this.child === child) {
        this.child = null;
        this.starting = null;
        this.fail(new Error("运行服务已断开；未自动重发任务。"));
      }
    });
    // Cold Python environments load the full Agent before accepting requests.
    // Give only process startup extra time; ordinary RPC deadlines stay bounded
    // independently so a slow startup is not mistaken for a disconnected host.
    const result = await this.raw("initialize", { protocolVersion: 1 }, 120000);
    if (result.protocolVersion !== 1) throw new Error("运行服务版本不兼容");
  }
  private fail(error: Error) {
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(error);
    }
    this.pending.clear();
    if (!this.stopped) this.emit("offline", error.message);
  }
  async request(method: Method, params: Record<string, unknown> = {}) {
    await this.start();
    return this.raw(method, params, method === "turn.submit" ? 120000 : 30000);
  }
  private raw(
    method: Method,
    params: Record<string, unknown>,
    timeout: number,
  ): Promise<any> {
    if (!this.child || this.child.stdin.destroyed)
      return Promise.reject(new Error("运行服务未连接"));
    const id = randomUUID(),
      frame = JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n";
    if (Buffer.byteLength(frame) > MAX)
      return Promise.reject(new Error("请求或附件过大，请减少内容"));
    if (this.pending.size >= 64)
      return Promise.reject(new Error("请求过多，请稍后重试"));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new Error(
            method === "turn.submit"
              ? "提交结果未知，请核对会话状态，不要重复发送。"
              : "服务响应超时，请重试",
          ),
        );
      }, timeout);
      this.pending.set(id, { resolve, reject, timer, method });
      this.child!.stdin.write(frame, (error) => {
        if (error) {
          clearTimeout(timer);
          this.pending.delete(id);
          reject(new Error("服务连接已断开"));
        }
      });
    });
  }
  async close() {
    this.stopped = true;
    const child = this.child;
    if (!child) return;
    child.stdin.end();
    await new Promise<void>((resolve) => {
      if (child.exitCode !== null) {
        resolve();
        return;
      }
      const timeout = setTimeout(() => {
        child.kill("SIGTERM");
        const force = setTimeout(() => child.kill("SIGKILL"), 3000);
        force.unref();
      }, 20000);
      child.once("exit", () => {
        clearTimeout(timeout);
        resolve();
      });
    });
    this.fail(new Error("应用已关闭"));
    this.child = null;
    this.starting = null;
  }
}
