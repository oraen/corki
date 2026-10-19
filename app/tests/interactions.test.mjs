import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { pathToFileURL } from "node:url";
const dir = await mkdtemp(path.join(os.tmpdir(), "corki-form-test-"));
await build({
  entryPoints: ["renderer/interactions.ts"],
  outfile: path.join(dir, "forms.mjs"),
  bundle: true,
  platform: "node",
  format: "esm",
});
const { defaults, choices, formContent } = await import(
  pathToFileURL(path.join(dir, "forms.mjs"))
);
test.after(() => rm(dir, { recursive: true, force: true }));
test("typed elicitation defaults preserve false/zero and omit blank optional numeric inputs", () => {
  const fields = {
    flag: { type: "boolean", default: false },
    n: { type: "integer", default: 0 },
    optional: { type: "number" },
    tags: { type: "array" },
  };
  const answers = { ...defaults(fields), optional: "", tags: ["one"] };
  assert.deepEqual(
    { ...formContent(fields, answers) },
    { flag: false, n: 0, tags: ["one"] },
  );
  assert.throws(() => formContent(fields, { n: "1.5" }), /整数/);
  assert.throws(() => formContent(fields, { optional: "Infinity" }), /数字/);
});
test("named enums and array alternatives retain declared values", () => {
  assert.deepEqual(choices({ enum: ["a"], enumNames: ["Label"] }), [
    { id: "a", label: "Label" },
  ]);
  assert.deepEqual(choices({ oneOf: [{ const: "value", title: "Name" }] }), [
    { id: "value", label: "Name" },
  ]);
  assert.deepEqual(choices({ anyOf: [{ const: "value", title: "Name" }] }), [
    { id: "value", label: "Name" },
  ]);
});
test("form field names cannot mutate the serialization object prototype", () => {
  const fields = JSON.parse('{"__proto__":{"type":"array"}}');
  const answer = JSON.parse('{"__proto__":["safe"]}');
  const result = formContent(fields, answer);
  assert.equal(Object.getPrototypeOf(result), null);
  assert.equal(JSON.stringify(result), '{"__proto__":["safe"]}');
});
