import { packager } from "@electron/packager";
import { mkdtemp, cp, readFile, writeFile, rm, mkdir } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createHash } from "node:crypto";
const exec = promisify(execFile);
// Stage only the allowlisted application payload. Never package a working tree,
// Python environment, user settings, test profiles, or the development node_modules.
const stage = await mkdtemp(path.join(os.tmpdir(), "corki-desktop-package-"));
try {
  const pkg = JSON.parse(await readFile("package.json", "utf8"));
  await writeFile(
    path.join(stage, "package.json"),
    JSON.stringify({
      name: pkg.name,
      productName: pkg.productName,
      version: pkg.version,
      main: pkg.main,
      description: pkg.description,
    }),
  );
  await cp("dist", path.join(stage, "dist"), { recursive: true });
  await cp("bridge", path.join(stage, "bridge"), {
    recursive: true,
    filter: (source) =>
      !source.includes("__pycache__") && !source.endsWith(".pyc"),
  });
  let icon;
  if (process.platform === "darwin") {
    const iconset = path.join(stage, "Corki.iconset");
    await mkdir(iconset);
    for (const size of [16, 32, 128, 256, 512])
      for (const scale of [1, 2]) {
        await exec("sips", [
          "-z",
          String(size * scale),
          String(size * scale),
          "resources/corki.png",
          "--out",
          path.join(
            iconset,
            `icon_${size}x${size}${scale === 2 ? "@2x" : ""}.png`,
          ),
        ]);
      }
    icon = path.join(stage, "Corki.icns");
    await exec("iconutil", ["-c", "icns", iconset, "-o", icon]);
    await rm(iconset, { recursive: true });
  }
  const electronZipDir = process.env.ELECTRON_ZIP_DIR;
  if (electronZipDir) {
    const filename = `electron-v${pkg.devDependencies.electron}-${process.platform}-${process.arch}.zip`;
    const checksums = JSON.parse(
      await readFile("node_modules/electron/checksums.json", "utf8"),
    );
    const hash = createHash("sha256")
      .update(await readFile(path.join(electronZipDir, filename)))
      .digest("hex");
    if (hash !== checksums[filename])
      throw new Error("Cached Electron archive checksum mismatch");
  }
  const output = await packager({
    dir: stage,
    name: "Corki",
    out: "out",
    overwrite: true,
    asar: true,
    icon,
    electronZipDir,
    prune: false,
    electronVersion: pkg.devDependencies.electron,
    appBundleId: "local.corki.desktop",
    // Packager 19 copies extraResource entries without recursive directory support.
    // Use its resource hook so the bridge is installed before signing/moving.
    extraResource: [],
    afterCopyExtraResources: [
      async ({ buildPath, platform }) => {
        const resources =
          platform === "darwin" || platform === "mas"
            ? path.join(buildPath, "Corki.app", "Contents", "Resources")
            : path.join(buildPath, "resources");
        await cp(path.join(stage, "bridge"), path.join(resources, "bridge"), {
          recursive: true,
        });
      },
    ],
    ignore: [/\.map$/, /^\/bridge($|\/)/, /Corki\.icns$/],
    osxSign: false,
  });
  console.log(output.join("\n"));
} finally {
  await rm(stage, { recursive: true, force: true });
}
