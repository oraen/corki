import http from "node:http";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdir, open } from "node:fs/promises";
import path from "node:path";

// Probe only a bounded local document, without redirects or configuration data.
export function probeManager(
  port = 17002,
): Promise<"ready" | "absent" | "occupied"> {
  return new Promise((resolve) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout>;
    const finish = (value: "ready" | "absent" | "occupied") => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        resolve(value);
      }
    };
    const request = http.get(
      { hostname: "127.0.0.1", port, path: "/", agent: false },
      (response) => {
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk: string) => {
          body += chunk;
          if (body.length > 131072) {
            finish("occupied");
            response.destroy();
          }
        });
        response.on("end", () =>
          finish(
            response.statusCode === 200 &&
              /<title>Corki · 管理工作台<\/title>/.test(body)
              ? "ready"
              : "occupied",
          ),
        );
        response.on("error", () => finish("occupied"));
      },
    );
    timer = setTimeout(() => {
      finish("occupied");
      request.destroy();
    }, 1200);
    request.on("error", (error: NodeJS.ErrnoException) =>
      finish(error.code === "ECONNREFUSED" ? "absent" : "occupied"),
    );
  });
}

export interface ManagerOptions {
  python: () => string;
  script: string;
  home: string;
  executable: string;
  open: (url: string) => Promise<void>;
  starting: () => void;
  port?: number;
  timeout?: number;
}

// A ready manager is a standalone local service, independent of window lifetime.
// Only a process created by this launcher may be stopped on startup failure.
export class ManagerLauncher {
  private pending: Promise<void> | null = null;
  constructor(private options: ManagerOptions) {}
  open(): Promise<void> {
    if (this.pending) return this.pending;
    this.pending = this.run().finally(() => {
      this.pending = null;
    });
    return this.pending;
  }
  private async run() {
    const o = this.options,
      port = o.port ?? 17002;
    const initial = await probeManager(port);
    if (initial === "occupied")
      throw new Error(
        `端口 ${port} 正在被其他服务占用或无法响应，请检查后重试`,
      );
    if (initial === "ready") return o.open(`http://127.0.0.1:${port}/`);
    o.starting();
    const folder = path.join(o.home, "manager");
    const workspace = path.join(o.home, "main", "default");
    await mkdir(folder, { recursive: true, mode: 0o700 });
    await mkdir(workspace, { recursive: true });
    const logPath = path.join(folder, "service.log");
    const log = await open(logPath, "a", 0o600);
    let child: ChildProcess;
    let failure = "";
    try {
      child = spawn(
        o.python(),
        [
          "-u",
          o.script,
          "--home",
          o.home,
          "--workspace",
          workspace,
          "--port",
          String(port),
        ],
        {
          cwd: o.home,
          detached: true,
          windowsHide: true,
          stdio: ["ignore", log.fd, log.fd],
          env: {
            ...process.env,
            CORKI_HOME: o.home,
            PYTHONIOENCODING: "utf-8",
            CORKI_DESKTOP_EXECUTABLE: o.executable,
          },
        },
      );
      child.on("error", () => {
        failure = "无法启动 Python，请检查 Corki 的 Python 运行环境";
      });
      child.on("exit", (code) => {
        failure ||= `管理服务启动后退出（${code ?? "已终止"}）`;
      });
    } finally {
      await log.close();
    }
    child.unref();
    let ready = false;
    try {
      const deadline = Date.now() + (o.timeout ?? 30000);
      while (Date.now() < deadline) {
        const state = await probeManager(port);
        if (state === "ready") {
          ready = true;
          break;
        }
        if (failure) throw new Error(failure);
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      if (!ready) throw new Error("管理服务启动超时");
    } catch (error) {
      if (child.exitCode === null && child.signalCode === null) child.kill();
      throw new Error(
        `${error instanceof Error ? error.message : "管理服务启动失败"}。可查看 ${logPath}`,
      );
    }
    // Browser failure must not terminate a service which is already ready.
    await o.open(`http://127.0.0.1:${port}/`);
  }
}
