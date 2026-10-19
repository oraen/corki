import { build } from "esbuild";
import { mkdir, cp } from "node:fs/promises";
await mkdir("dist", { recursive: true });
await Promise.all([
  build({
    entryPoints: ["electron/main.ts"],
    outfile: "dist/main.cjs",
    bundle: true,
    platform: "node",
    format: "cjs",
    external: ["electron"],
    sourcemap: false,
  }),
  build({
    entryPoints: ["preload/index.ts"],
    outfile: "dist/preload.cjs",
    bundle: true,
    platform: "node",
    format: "cjs",
    external: ["electron"],
    sourcemap: false,
  }),
  build({
    entryPoints: ["renderer/index.tsx"],
    outfile: "dist/renderer.js",
    bundle: true,
    platform: "browser",
    format: "iife",
    minify: true,
    legalComments: "eof",
  }),
]);
await cp("renderer/index.html", "dist/index.html");
await cp("resources", "dist/resources", { recursive: true });
