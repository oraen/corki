import { listPackage } from "@electron/asar";
import { _electron as electron } from "playwright";
import { mkdtemp, mkdir, writeFile, readdir, stat, rm } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import assert from "node:assert/strict";
import { python, systemPath } from "./platform.mjs";
assert(["darwin", "win32"].includes(process.platform), "Unsupported package test host");
const mac = process.platform === "darwin";
const bundle = process.env.CORKI_TEST_PACKAGE_BUNDLE
  ? path.resolve(process.env.CORKI_TEST_PACKAGE_BUNDLE)
  : path.resolve(`out/Corki-${process.platform}-${process.arch}`, ...(mac ? ["Corki.app"] : []));
const resources = path.join(bundle, mac ? "Contents/Resources" : "resources");
// asar exposes host-native separators even though the payload is identical.
const entries = listPackage(path.join(resources, "app.asar"))
  .map(entry => entry.replaceAll('\\', '/'));
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
const expected = ["content.py", "followups.py", "live.py", "main.py", "pipe_reader.py", "protocol.py", "service.py", "state.py"];
assert.deepEqual(
  (await readdir(path.join(resources, "bridge"))).sort(),
  expected,
);
assert.deepEqual((await readdir(path.join(resources, "manager"))).sort(), ["server.py", "static", "templates"]);
assert((await stat(path.join(resources, "manager/static/index.html"))).isFile());
assert((await stat(path.join(resources, "manager/templates/quick-setup.json"))).isFile());
if (mac) assert((await stat(path.join(resources, "electron.icns"))).size > 1000);
const home = await mkdtemp(path.join(os.tmpdir(), "corki-package-check-"));
let app;
try {
  await mkdir(path.join(home, "app"));
  await writeFile(
    path.join(home, "app/window.json"),
    JSON.stringify({
      python,
    }),
  );
  const env = { ...process.env, PATH: systemPath, CORKI_HOME: home };
  for (const key of [
    "CORKI_PYTHON",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "ELECTRON_RUN_AS_NODE",
  ])
    delete env[key];
  app = await electron.launch({
    executablePath: path.join(bundle, mac ? "Contents/MacOS/Corki" : "Corki.exe"),
    args: [],
    cwd: home,
    env,
  });
  assert.equal(await app.evaluate(({ app }) => app.isPackaged), true);
  assert.equal(await app.evaluate(({ app }) => app.getName()), "Corki");
  if (!mac) {
    const branding = await app.evaluate(async ({ app, nativeImage }, source) => {
      const actual = await app.getFileIcon(process.execPath, { size: "normal" });
      const expected = nativeImage.createFromPath(source).resize({
        ...actual.getSize(), quality: "best",
      });
      if (actual.isEmpty() || expected.isEmpty()) return { empty: true };
      const pixels = actual.toBitmap({ scaleFactor: 1 });
      const reference = expected.toBitmap({ scaleFactor: 1 });
      if (pixels.length !== reference.length) return { sizeMismatch: true };
      let difference = 0;
      for (let i = 0; i < pixels.length; i++)
        difference += Math.abs(pixels[i] - reference[i]);
      return { difference: difference / pixels.length };
    }, path.join(resources, "app.asar/dist/resources/corki.png"));
    // Independent Windows shell extraction catches invalid embedded resources.
    // Allow codec interpolation at edges, while rejecting the generic/stock icon.
    assert(
      Number.isFinite(branding.difference) && branding.difference < 5,
      `Windows executable icon differs from Corki artwork: ${JSON.stringify(branding)}`,
    );
  }
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
} catch (error) {
  // Preserve the original launch/protocol failure if Windows cleanup also fails.
  console.error('Package launch check failed:', error);
  throw error;
} finally {
  if (app) await app.close();
  await rm(home, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 });
}
