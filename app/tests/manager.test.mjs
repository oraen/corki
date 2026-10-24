import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdtemp, writeFile, readFile, rm } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import http from "node:http";
import { pathToFileURL } from "node:url";
import { python } from "./platform.mjs";
const root = await mkdtemp(path.join(os.tmpdir(), "corki-manager-launch-"));
await build({
  entryPoints: ["electron/manager.ts"],
  outfile: path.join(root, "manager.mjs"),
  bundle: true,
  platform: "node",
  format: "esm",
});
const { ManagerLauncher, probeManager } = await import(
  pathToFileURL(path.join(root, "manager.mjs"))
);
const fixture = path.join(root, "service.py");
await writeFile(
  fixture,
  `import argparse,os,time
from pathlib import Path
from http.server import HTTPServer,BaseHTTPRequestHandler
p=argparse.ArgumentParser();p.add_argument('--home');p.add_argument('--workspace');p.add_argument('--port',type=int);a=p.parse_args()
Path(a.home,'pid').write_text(str(os.getpid()))
Path(a.home,'workspace').write_text(a.workspace)
time.sleep(.3)
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write('<title>Corki · 管理工作台</title>'.encode())
HTTPServer(('127.0.0.1',a.port),Handler).serve_forever()
`,
);
async function serve(body = "<title>Corki · 管理工作台</title>") {
  const server = http.createServer((req, res) => res.end(body));
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return { server, port: server.address().port };
}
async function freePort() {
  const { server, port } = await serve();
  await new Promise((r) => server.close(r));
  return port;
}
function launcher(port, home, extras = {}) {
  return new ManagerLauncher({
    python: () => python,
    script: fixture,
    home,
    executable: process.execPath,
    port,
    timeout: 3000,
    open: async () => {},
    starting: () => {},
    ...extras,
  });
}
test.after(() => rm(root, { recursive: true, force: true }));
test("existing service opens without startup", async () => {
  const { server, port } = await serve();
  let opens = 0;
  try {
    await launcher(port, root, {
      python: () => {
        throw Error("must not launch");
      },
      starting: () => assert.fail(),
      open: async (url) => {
        assert.equal(url, `http://127.0.0.1:${port}/`);
        opens++;
      },
    }).open();
    assert.equal(opens, 1);
  } finally {
    server.close();
  }
});
test("unrelated listener rejected", async () => {
  const { server, port } = await serve("not a manager");
  try {
    assert.equal(await probeManager(port), "occupied");
    await assert.rejects(
      launcher(port, root, {
        open: async () => assert.fail(),
        starting: () => assert.fail(),
      }).open(),
      /占用/,
    );
  } finally {
    server.close();
  }
});
test("cold start waits for readiness; concurrent clicks share launch; Unicode paths", async () => {
  const home = path.join(root, "中文 空格");
  let starts = 0,
    opens = 0,
    pid;
  const port = await freePort();
  const host = launcher(port, home, {
    starting: () => starts++,
    open: async () => {
      assert.equal(await probeManager(port), "ready");
      opens++;
    },
  });
  try {
    const first = host.open();
    assert.equal(host.open(), first);
    await first;
    pid = Number(await readFile(path.join(home, "pid"), "utf8"));
    assert.equal(starts, 1);
    assert.equal(opens, 1);
    assert.equal(
      await readFile(path.join(home, "workspace"), "utf8"),
      path.join(home, "main", "default"),
    );
    await host.open();
    assert.equal(starts, 1);
    assert.equal(opens, 2);
  } finally {
    if (!pid) {
      try {
        pid = Number(await readFile(path.join(home, "pid"), "utf8"));
      } catch {}
    }
    if (pid) process.kill(pid);
  }
});
test("missing interpreter reports error and permits retry", async () => {
  const host = launcher(await freePort(), path.join(root, "missing"), {
    python: () => path.join(root, "no-python"),
    open: async () => assert.fail(),
  });
  await assert.rejects(host.open(), /Python/);
  await assert.rejects(host.open(), /Python/);
});
test("early exit is failure", async () => {
  const broken = path.join(root, "broken.py");
  await writeFile(broken, "raise SystemExit(3)");
  await assert.rejects(
    launcher(await freePort(), path.join(root, "exit"), {
      script: broken,
      open: async () => assert.fail(),
    }).open(),
    /退出/,
  );
});
test("timeout stops owned startup process", async () => {
  const slow = path.join(root, "slow.py");
  await writeFile(
    slow,
    "import os,sys,time\nfrom pathlib import Path\nPath(sys.argv[sys.argv.index('--home')+1],'pid').write_text(str(os.getpid()))\ntime.sleep(120)",
  );
  const home = path.join(root, "timeout");
  await assert.rejects(
    launcher(await freePort(), home, {
      script: slow,
      timeout: 700,
      open: async () => assert.fail(),
    }).open(),
    /超时/,
  );
  const pid = Number(await readFile(path.join(home, "pid"), "utf8"));
  for (let n = 0; n < 30; n++) {
    try {
      process.kill(pid, 0);
    } catch {
      return;
    }
    await new Promise((r) => setTimeout(r, 30));
  }
  assert.fail("startup child survived timeout");
});

test("real manager starts with isolated home and serves templates and static assets", async () => {
  const home = path.join(root, "real-manager");
  const wrapper = path.join(root, "real-manager.py");
  await writeFile(
    wrapper,
    `import os,sys,runpy
from pathlib import Path
Path(sys.argv[sys.argv.index('--home')+1],'pid').write_text(str(os.getpid()))
runpy.run_path(${JSON.stringify(path.resolve("../manager/server.py"))},run_name='__main__')`,
  );
  const port = await freePort();
  let pid;
  try {
    await launcher(port, home, { script: wrapper, timeout: 15000 }).open();
    pid = Number(await readFile(path.join(home, "pid"), "utf8"));
    const templates = await fetch(
      `http://127.0.0.1:${port}/api/quick-templates`,
    );
    assert.equal(templates.status, 200);
    assert.ok((await templates.json()).providers.length);
    assert.equal(
      (await fetch(`http://127.0.0.1:${port}/style.css`)).status,
      200,
    );
  } finally {
    if (!pid) {
      try {
        pid = Number(await readFile(path.join(home, "pid"), "utf8"));
      } catch {}
    }
    if (pid) {
      try {
        process.kill(pid);
      } catch {}
    }
  }
});
