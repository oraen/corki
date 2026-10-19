import { listPackage } from "@electron/asar";
import { _electron as electron } from "playwright";
import { mkdtemp, mkdir, writeFile, readdir, stat, rm } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import assert from "node:assert/strict";
assert.equal(
  process.platform,
  "darwin",
  "This packaged launch check currently targets macOS",
);
const bundle = path.resolve(`out/Corki-darwin-${process.arch}/Corki.app`);
const resources = path.join(bundle, "Contents/Resources");
const entries = listPackage(path.join(resources, "app.asar"));
assert(
  entries.every(
    (f) => f === "/package.json" || f === "/dist" || f.startsWith("/dist/"),
  ),
);
assert(
  !entries.some((f) =>
    /node_modules|settings\.json|\.sqlite|\.map$|\.env/.test(f),
  ),
);
const expected = ["content.py", "live.py", "main.py", "protocol.py", "service.py", "state.py"];
assert.deepEqual(
  (await readdir(path.join(resources, "bridge"))).sort(),
  expected,
);
assert((await stat(path.join(resources, "electron.icns"))).size > 1000);
const home = await mkdtemp(path.join(os.tmpdir(), "corki-package-check-"));
let app;
try {
  await mkdir(path.join(home, "app"));
  await writeFile(
    path.join(home, "app/window.json"),
    JSON.stringify({
      python:
        process.env.CORKI_TEST_PYTHON || path.resolve("../.venv/bin/python"),
    }),
  );
  const env = { ...process.env, PATH: "/usr/bin:/bin", CORKI_HOME: home };
  for (const key of [
    "CORKI_PYTHON",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "ELECTRON_RUN_AS_NODE",
  ])
    delete env[key];
  app = await electron.launch({
    executablePath: path.join(bundle, "Contents/MacOS/Corki"),
    args: [],
    cwd: home,
    env,
  });
  assert.equal(await app.evaluate(({ app }) => app.isPackaged), true);
  const page = await app.firstWindow();
  await page.waitForFunction(() => !!window.corki);
  const init = await page.evaluate(() =>
    window.corki.request("initialize", { protocolVersion: 1 }),
  );
  assert.equal(init.protocolVersion, 1);
  assert.equal(
    (await page.evaluate(() => window.corki.request("config.read"))).valid,
    false,
  );
  await app.close();
  app = null;
  assert.deepEqual(
    (await readdir(path.join(resources, "bridge"))).sort(),
    expected,
    "Launching must not write Python caches into the application bundle",
  );
  console.log(
    "PASS package allowlist, icon, external cwd/saved Python, invalid config and immutable bridge resources",
  );
} finally {
  if (app) await app.close();
  await rm(home, { recursive: true, force: true });
}
