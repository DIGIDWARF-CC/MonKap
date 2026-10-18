// MonKap's frame and view maths (src/monkap/static/monkap_geo.js). No browser needed:
//     node --test "tests/js/*.test.mjs"
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {test} from "node:test";

const require = createRequire(import.meta.url);
const geo = require("../../src/monkap/static/monkap_geo.js");
const {MIN_FRAME, Rect, View, hasTrigger, withTrigger, applyRatio, boxFromRect, hitTest, moveRect, ratioValue, resizeRect, round} = geo;

const IW = 1000, IH = 600;
const NAMES = ["1:1", "3:4", "4:3", "9:16", "16:9"];
const near = (a, b, eps = 1e-6, msg) => assert.ok(Math.abs(a - b) <= eps, msg || `${a} != ${b}`);
const inside = (r, iw = IW, ih = IH) => r.l >= -1e-6 && r.t >= -1e-6 && r.r <= iw + 1e-6 && r.b <= ih + 1e-6;

// a centred frame covering ~70% of the picture in the requested ratio (a fixture)
function initialRect(iw, ih, ratio) {
  const bw = iw * 0.7, bh = ih * 0.7;
  let w, h;
  if (ratio == null) { w = bw; h = bh; }
  else if (bw / bh > ratio) { h = bh; w = bh * ratio; }
  else { w = bw; h = bw / ratio; }
  return new Rect((iw - w) / 2, (ih - h) / 2, (iw + w) / 2, (ih + h) / 2);
}

test("rounding goes to the even neighbour on a half, like Python's", () => {
  assert.deepEqual([0.5, 1.5, 2.5, 110.5, -0.5, 2.4, 2.6].map(round), [0, 2, 2, 110, 0, 2, 3]);
});

test("view: picture <-> canvas round trip at any scale", () => {
  for (const scale of [0.25, 0.5, 1, 1.37, 4, 16]) {
    const v = new View(scale, 123.5, 77.25);
    for (const [ix, iy] of [[0, 0], [10.5, 20.25], [999, 599]]) {
      const [cx, cy] = v.toCanvas(ix, iy), [bx, by] = v.toImage(cx, cy);
      near(bx, ix); near(by, iy);
    }
  }
});

test("view: zoom keeps the point under the cursor", () => {
  const v = new View(); v.fit(IW, IH, 800, 500);
  const before = v.toImage(310, 240);
  v.zoomAt(310, 240, 2, IW, IH, 800, 500);
  const after = v.toImage(310, 240);
  near(before[0], after[0], 1e-4); near(before[1], after[1], 1e-4);
});

test("view: never zooms out below Fit, and Fit centres the picture", () => {
  const v = new View(); v.fit(IW, IH, 800, 500);
  const fit = v.scale;
  v.zoomAt(400, 250, 0.01, IW, IH, 800, 500);
  near(v.scale, fit, 1e-9);
  const w = new View(); w.fit(IW, IH, 800, 800);
  const [l, t] = w.toCanvas(0, 0), [r, b] = w.toCanvas(IW, IH);
  near(l, 0, 1e-4); near(r, 800, 1e-4); near((t + b) / 2, 400, 1e-4);
});

test("view: a small picture in a big window can still be shown at 100%", () => {
  const v = new View(); v.fit(400, 300, 1200, 900);                    // Fit blows it up 3x
  assert.ok(v.scale > 2.9);
  v.setScaleAt(600, 450, 1, 400, 300, 1200, 900);
  near(v.scale, 1, 1e-9);
  v.setScaleAt(600, 450, 0.2, 400, 300, 1200, 900);                    // but never smaller than 100% there
  near(v.scale, 1, 1e-9);
});

test("view: pan stays inside the picture", () => {
  const v = new View(); v.fit(IW, IH, 800, 500);
  v.zoomAt(400, 250, 4, IW, IH, 800, 500);
  v.panBy(10000, 10000, IW, IH, 800, 500);
  assert.ok(v.ox >= 0 && v.oy >= 0);
  v.panBy(-100000, -100000, IW, IH, 800, 500);
  assert.ok(v.ox + 800 / v.scale <= IW + 1e-6 && v.oy + 500 / v.scale <= IH + 1e-6);
});

test("a frame maps back to the same box whatever the zoom or pan", () => {
  const frame = new Rect(100.4, 50.6, 612.2, 400.9);
  const want = boxFromRect(frame, IW, IH);
  for (const [scale, ox, oy] of [[0.5, 0, 0], [1, 20, 10], [3.3, 80, 40], [12, 100, 50]]) {
    const v = new View(scale, ox, oy);
    const [cl, ct, cr, cb] = v.rectToCanvas(frame);
    const back = new Rect(...v.toImage(cl, ct), ...v.toImage(cr, cb));
    assert.deepEqual(boxFromRect(back, IW, IH), want);
  }
});

test("box: rounding, clamping and an empty selection", () => {
  assert.deepEqual(boxFromRect(new Rect(10.4, 20.6, 110.5, 220.4), IW, IH), [10, 21, 110, 220]);
  assert.deepEqual(boxFromRect(new Rect(-5, -5, IW + 9, IH + 9), IW, IH), [0, 0, IW, IH]);
  assert.throws(() => boxFromRect(new Rect(10.2, 10.2, 10.4, 50), IW, IH), /empty/);
});

test("box: a square frame is exactly square (edges rounded one by one would give 593x594)", () => {
  const [l, t, r, b] = boxFromRect(new Rect(82.76, 158.61, 676.36, 752.21), IW + 400, IH + 400, 1);
  assert.equal(r - l, b - t);
});

test("box: every ratio keeps its ratio and stays inside the picture", () => {
  for (const name of NAMES) {
    const ratio = ratioValue(name);
    for (const start of [initialRect(IW, IH, ratio), new Rect(0, 0, 300 * ratio, 300),
                         new Rect(IW - 300 * ratio, IH - 300, IW, IH)]) {
      const [l, t, r, b] = boxFromRect(start, IW, IH, ratio);
      assert.ok(l >= 0 && t >= 0 && r <= IW && b <= IH, name);
      assert.ok(Math.abs((r - l) / (b - t) - ratio) <= 0.02, `${name} ${[l, t, r, b]}`);
    }
  }
  assert.deepEqual(boxFromRect(new Rect(10.4, 20.6, 110.5, 220.4), IW, IH, null), [10, 21, 110, 220]);
});

test("move: stays inside, keeps its size and ratio", () => {
  const r = new Rect(100, 100, 300, 250);
  for (const [dx, dy] of [[5000, 5000], [-5000, -5000], [3, -4]]) {
    const m = moveRect(r, dx, dy, IW, IH);
    assert.ok(inside(m)); near(m.w, r.w); near(m.h, r.h);
  }
  const f = initialRect(IW, IH, 3 / 4), m = moveRect(f, 123, -45, IW, IH);
  near(m.w / m.h, 3 / 4, 1e-9);
});

test("resize: free edges, clamped to the picture and the minimum", () => {
  const r = new Rect(100, 100, 400, 300);
  assert.equal(resizeRect(r, "e", 500, 0, IW, IH, null).r, 500);
  assert.equal(resizeRect(r, "w", 50, 0, IW, IH, null).l, 50);
  assert.equal(resizeRect(r, "s", 0, 450, IW, IH, null).b, 450);
  assert.equal(resizeRect(r, "n", 0, 20, IW, IH, null).t, 20);
  assert.equal(resizeRect(r, "e", 5000, 0, IW, IH, null).r, IW);
  assert.equal(resizeRect(r, "w", -50, 0, IW, IH, null).l, 0);
  near(resizeRect(r, "e", 0, 0, IW, IH, null).w, MIN_FRAME);
});

test("resize: the ratio holds on every handle, inside the picture, above the minimum", () => {
  for (const name of NAMES) {
    const ratio = ratioValue(name), start = initialRect(IW, IH, ratio);
    for (const handle of ["nw", "ne", "se", "sw", "n", "s", "e", "w"]) {
      for (const [px, py] of [[50, 40], [500, 300], [990, 590], [200, 580], [-100, -100], [5000, 5000]]) {
        const out = resizeRect(start, handle, px, py, IW, IH, ratio);
        const at = `${name} ${handle} ${px},${py}`;
        assert.ok(inside(out), at);
        near(out.w / out.h, ratio, 1e-6, at);
        assert.ok(Math.min(out.w, out.h) >= MIN_FRAME - 1e-6, at);
      }
    }
  }
});

test("resize: a corner pins the opposite corner; a tiny picture stays croppable", () => {
  const out = resizeRect(new Rect(200, 100, 500, 400), "se", 700, 600, IW, IH, 1);
  near(out.l, 200); near(out.t, 100);
  const tiny = resizeRect(new Rect(0, 0, 5, 5), "se", 100, 100, 8, 8, null);
  assert.ok(tiny.r <= 8 && tiny.w > 0);
});

test("a new ratio keeps the centre, stays inside and takes the ratio; Free changes nothing", () => {
  const r = new Rect(300, 150, 700, 450);
  for (const name of NAMES) {
    const out = applyRatio(r, ratioValue(name), IW, IH);
    assert.ok(inside(out));
    near(out.w / out.h, ratioValue(name), 1e-6);
  }
  assert.equal(applyRatio(r, null, IW, IH), r);
});

test("hit test: corners beat edges, edges beat the inside", () => {
  const rc = [100, 100, 300, 250];
  assert.equal(hitTest(rc, 100, 100, 8), "nw");
  assert.equal(hitTest(rc, 300, 250, 8), "se");
  assert.equal(hitTest(rc, 200, 100, 8), "n");
  assert.equal(hitTest(rc, 300, 170, 8), "e");
  assert.equal(hitTest(rc, 200, 170, 8), "move");
  assert.equal(hitTest(rc, 20, 20, 8), null);
  assert.equal(hitTest(rc, 400, 170, 8), null);
});

test("trigger word: found as a phrase of its own, in any case, never inside another word", () => {
  assert.ok(hasTrigger("engine_timing, a chain", "engine_timing"));
  assert.ok(hasTrigger("a chain, ENGINE_TIMING, a belt", "engine_timing"));
  assert.ok(hasTrigger("a chain over sprockets engine_timing", "engine_timing"));
  assert.ok(hasTrigger("ohwx man.", "ohwx"));
  assert.ok(hasTrigger("a photo of ohwx woman", "ohwx woman"));
  assert.ok(!hasTrigger("an ohwxer in a car", "ohwx"));
  assert.ok(!hasTrigger("pre_ohwx", "ohwx"));
  assert.ok(!hasTrigger("anything", ""));
  assert.ok(hasTrigger("a (b) c", "(b)"));                              // characters that mean something in a pattern
});

test("trigger word: put first with a comma, once, and only when it is not there", () => {
  assert.equal(withTrigger("a chain over two sprockets", "engine_timing"), "engine_timing, a chain over two sprockets");
  assert.equal(withTrigger("  a chain  ", "engine_timing"), "engine_timing, a chain");
  assert.equal(withTrigger("", "engine_timing"), "engine_timing");
  assert.equal(withTrigger("engine_timing, a chain", "engine_timing"), "engine_timing, a chain");
  assert.equal(withTrigger("a chain, Engine_Timing", "engine_timing"), "a chain, Engine_Timing");
  assert.equal(withTrigger("a chain", ""), "a chain");
  assert.equal(withTrigger(withTrigger("a chain", "x"), "x"), "x, a chain");     // a second press changes nothing
});
