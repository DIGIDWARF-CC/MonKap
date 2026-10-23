// The page's own files hang together: every script the page loads exists and parses, the lab's element ids are in the page, and
// the lab is made with the helpers it needs.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const dir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "src", "monkap", "static");
const read = (n) => fs.readFileSync(path.join(dir, n), "utf8");

test("every script of the page exists and parses", () => {
  const html = read("index.html");
  const scripts = [...html.matchAll(/<script src="\/([^"]+)"/g)].map((m) => m[1]);
  assert.ok(scripts.includes("monkap_lab.js"));
  assert.ok(scripts.indexOf("monkap_lab.js") < scripts.indexOf("monkap.js"), "the lab loads before the page that makes it");
  for (const s of scripts) assert.doesNotThrow(() => new vm.Script(read(s), { filename: s }), s);
});

test("the ids the page script looks up are in the page", () => {
  const html = read("index.html");
  const js = read("monkap.js");
  const ids = new Set([...js.matchAll(/\$q\("([A-Za-z]+)"\)/g)].map((m) => m[1]));
  for (const m of js.matchAll(/getElementById\("([A-Za-z]+)"\)/g)) ids.add(m[1]);
  const missing = [...ids].filter((id) => !html.includes(`id="${id}"`));
  assert.deepEqual(missing, []);
});

test("the greyed rail icon names the reason, and leaving the lab names the rule", () => {
  const js = read("monkap.js");
  assert.match(js, /Please stop any running enhance tasks before returning to main view/);
  assert.match(read("monkap_lab.js"), /Regenerate will be enabled after all tasks are done or stopped/);
});
