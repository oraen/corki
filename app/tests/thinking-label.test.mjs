import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const tmp = await mkdtemp(path.join(os.tmpdir(), "corki-thinking-"));
await build({ entryPoints: ["renderer/thinking-label.tsx"], outfile: path.join(tmp, "label.mjs"), bundle: true, platform: "node", format: "esm" });
const { thinkingLabel, formatThinkingDuration } = await import(pathToFileURL(path.join(tmp, "label.mjs")));
test.after(() => rm(tmp, { recursive: true, force: true }));
test("duration omits seconds from an hour onward", () => {
  for (const [input, output] of [[0, "0s"], [733, "12m 13s"], [3599, "59m 59s"], [3600, "1h 0m"], [9193, "2h 33m"], [90061, "25h 1m"]])
    assert.equal(formatThinkingDuration(input), output);
});
test("goal uses cumulative goal time; normal mode uses current run start", () => {
  const normal = { status: "正在执行", runStartedAt: 1000 };
  assert.equal(thinkingLabel(normal, 734000, 0), "思考中（12m 13s）");
  const goal = { ...normal, goal: { status: "active", time_used_seconds: 3600, updated_at: 730 } };
  assert.equal(thinkingLabel(goal, 734000, 0), "执行Goal中（1h 0m）");
  assert.equal(thinkingLabel({ ...goal, goal: { ...goal.goal, status: "complete" } }, 734000, 0), "思考中（12m 13s）");
});
