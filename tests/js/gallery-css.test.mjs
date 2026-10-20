// A guard for a bug found in 0.3.0: the selection gallery's tiles overlapped once the gallery scrolled, because a grid row got its
// height from `aspect-ratio` (which a grid sizes by the tile's minimum, 0). The square must come from the padding.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const css = fs.readFileSync(new URL("../../src/monkap/static/monkap.css", import.meta.url), "utf8");

function rule(selector) {
  const at = css.indexOf(`${selector} {`);
  assert.ok(at >= 0, `no rule for ${selector}`);
  return css.slice(at, css.indexOf("}", at));
}

test("a gallery tile is a square made by its padding, not by aspect-ratio", () => {
  const tile = rule(".mk-tile");
  assert.match(tile, /height:\s*0/);
  assert.match(tile, /padding-bottom:\s*100%/);
  assert.doesNotMatch(tile, /aspect-ratio/);
});

test("the tile's picture is laid over it, so it adds nothing to the row's height", () => {
  assert.match(rule(".mk-tile img"), /position:\s*absolute/);
});
