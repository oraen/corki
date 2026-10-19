import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
const tmp = await mkdtemp(path.join(os.tmpdir(), "corki-transcript-"));
await build({
  entryPoints: ["renderer/transcript.ts"],
  outfile: path.join(tmp, "transcript.mjs"),
  bundle: true,
  platform: "node",
  format: "esm",
});
const { transcriptEntries, toolTitle, formatToolText, formatElapsed, formatMessageTime } = await import(
  pathToFileURL(path.join(tmp, "transcript.mjs"))
);
await rm(tmp, { recursive: true, force: true });
const row = (id, role, turnId = "t", extra = {}) => ({
  id,
  role,
  turnId,
  content: id,
  ...extra,
});
test("parallel outputs attach to their own call; missing/off-page calls remain visible", () => {
  const a = row("a", "tool", "t", { callId: "a" }),
    b = row("b", "tool", "t", { callId: "b" });
  const ob = row("ob", "tool-result", "t", { callId: "b" }),
    oa = row("oa", "tool-result", "t", { callId: "a" });
  const orphan = row("orphan", "tool-result", "other", { callId: "a" });
  const entries = transcriptEntries([a, b, ob, oa, orphan]);
  assert.equal(entries.length, 3);
  assert.equal(entries[0].message, a);
  assert.equal(entries[0].output, oa);
  assert.equal(entries[1].output, ob);
  assert.equal(entries[2].message, orphan);
});
test("intermediate commentary is distinct from final answer without discarding identical text", () => {
  const messages = [
    row("u", "user"),
    row("progress", "assistant"),
    row("call", "tool", "t", { callId: "c" }),
    row("out", "tool-result", "t", { callId: "c" }),
    row("final", "assistant"),
  ];
  const entries = transcriptEntries(messages);
  assert(entries.find((e) => e.message.id === "progress").commentary);
  assert(!entries.find((e) => e.message.id === "final").commentary);
  assert.equal(entries.length, 4);
  assert(
    !transcriptEntries([row("old", "assistant", "old"), row("tool", "tool")])[0]
      .commentary,
  );
});
test("command titles and formatted JSON tolerate partial/malicious/plain output", () => {
  assert.equal(
    toolTitle(
      row("a", "tool", "t", {
        name: "functions.exec_command",
        content: '{"cmd":"printf hello"}',
      }),
    ),
    "printf hello",
  );
  assert.equal(
    toolTitle(
      row("a", "tool", "t", { name: "exec_command", content: '{"cmd":' }),
    ),
    "执行命令",
  );
  assert.deepEqual(formatToolText('{"ok":true,"n":2}'), {
    text: '{\n  "ok": true,\n  "n": 2\n}',
    language: "json",
  });
  assert.equal(formatToolText("<script>bad()</script>").language, "plaintext");
});

test('bounded command preview can still supply a safe display-only title',()=>{
 assert.equal(toolTitle(row('a','tool','t',{name:'exec_command',content:'{"cmd":"printf hello'})), 'printf hello…');
 assert.equal(toolTitle(row('a','tool','t',{name:'exec_command',content:'{"cmd":{"bad":true}}'})), '执行命令');
});

test('avatar stays on first answer through tools, final output and stopping',()=>{
 const first=row('first','assistant'), tool=row('tool','tool','t',{callId:'call'});
 const final=row('last','assistant'), stop=row('stopped','stopped');
 for(const messages of [[first],[first,tool],[first,tool,final],[first,tool,stop]]) {
  const entries=transcriptEntries(messages);
  assert.deepEqual(entries.filter(e=>e.showAvatar).map(e=>e.message.id),['first']);
 }
 assert(transcriptEntries([tool,stop]).find(e=>e.message===stop).showAvatar);
 assert.deepEqual(transcriptEntries([first,stop,row('next','assistant','next')]).filter(e=>e.showAvatar).map(e=>e.message.id),['first','next']);
});

test('elapsed duration formats seconds, minutes and hours',()=>{
 assert.equal(formatElapsed(7401), '2h 3m 21s');
 assert.equal(formatElapsed(61), '1m 1s');
 assert.equal(formatElapsed(0), '0s');
 assert.equal(formatElapsed(3600), '1h 0s');
});

test("message times use local calendar fields and omit missing or invalid values", () => {
  const date = new Date(2026, 9, 17, 13, 7);
  assert.equal(formatMessageTime(date.toISOString()), "2026-10-17 13:07");
  assert.equal(formatMessageTime("2026-10-17 05:07:00"), formatMessageTime("2026-10-17T05:07:00Z"));
  assert.equal(formatMessageTime(undefined), "");
  assert.equal(formatMessageTime("invalid"), "");
});
