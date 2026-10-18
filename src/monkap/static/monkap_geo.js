// MonKap geometry: the zoom / pan transform and the Kapture frame (and one small text helper, the trigger word).
// Pure functions - no DOM, no server - so the maths is tested with Node (src/fizgig/monkap/js_tests/geo.test.mjs).
// Everything the frame stores is in the ORIGINAL picture's pixels; the View is the only thing that knows about the
// screen, so zoom, pan, a resized window or a hi-dpi screen can never change the region that gets cropped.
"use strict";
(function (root) {
  const MIN_FRAME = 16;       // the smallest frame edge, in picture pixels: thinner is a mis-click, not a crop
  const RATIOS = {"1:1": [1, 1], "3:4": [3, 4], "4:3": [4, 3], "9:16": [9, 16], "16:9": [16, 9], "Free": null};

  // Python-style rounding (halves to even): the box a crop reads must not depend on which side of the app made it
  const round = (x) => {
    const f = Math.floor(x), d = x - f;
    return d < 0.5 ? f : d > 0.5 ? f + 1 : (f % 2 === 0 ? f : f + 1);
  };
  const clamp = (x, lo, hi) => Math.min(Math.max(x, lo), hi);

  function ratioValue(name) {
    const pair = RATIOS[name];
    return pair ? pair[0] / pair[1] : null;
  }

  class Rect {
    // axis-aligned, left/top inclusive, right/bottom exclusive, float pixels
    constructor(l, t, r, b) { this.l = l; this.t = t; this.r = r; this.b = b; }
    get w() { return this.r - this.l; }
    get h() { return this.b - this.t; }
    get cx() { return (this.l + this.r) / 2; }
    get cy() { return (this.t + this.b) / 2; }
  }

  const minSize = (ms, iw, ih) => Math.max(1, Math.min(ms, iw, ih));   // a tiny picture stays croppable

  function moveRect(rect, dx, dy, iw, ih) {
    const w = rect.w, h = rect.h;
    const l = clamp(rect.l + dx, 0, Math.max(0, iw - w));
    const t = clamp(rect.t + dy, 0, Math.max(0, ih - h));
    return new Rect(l, t, l + w, t + h);
  }

  // re-shape a frame to a new ratio: same centre, about the same area, inside the picture
  function applyRatio(rect, ratio, iw, ih, ms = MIN_FRAME) {
    if (ratio == null) return rect;
    const area = Math.max(rect.w * rect.h, 1);
    let w = Math.sqrt(area * ratio), h = w / ratio;
    const shrink = Math.min(1, iw / w, ih / h);
    w *= shrink; h *= shrink;
    const min = minSize(ms, iw, ih);
    if (Math.min(w, h) < min) {                          // grow back up to the minimum if the picture allows it
      const grow = min / Math.min(w, h);
      w = Math.min(w * grow, iw); h = Math.min(h * grow, ih);
    }
    const l = clamp(rect.cx - w / 2, 0, iw - w);
    const t = clamp(rect.cy - h / 2, 0, ih - h);
    return new Rect(l, t, l + w, t + h);
  }

  // Drag `handle` to the picture point (px, py). With a ratio the frame keeps it exactly: a corner pins the opposite
  // corner, an edge pins the opposite edge and grows around the frame's centre line. Bounded by the picture on every
  // side, never smaller than `ms`.
  function resizeRect(rect, handle, px, py, iw, ih, ratio, ms = MIN_FRAME) {
    const min = minSize(ms, iw, ih);
    if (ratio == null) {
      let {l, t, r, b} = rect;
      if (handle.includes("w")) l = clamp(px, 0, r - min);
      if (handle.includes("e")) r = clamp(px, l + min, iw);
      if (handle.includes("n")) t = clamp(py, 0, b - min);
      if (handle.includes("s")) b = clamp(py, t + min, ih);
      return new Rect(l, t, r, b);
    }
    const minW = Math.max(min, min * ratio);
    if (handle.length === 2) {                           // corner
      const ax = handle.includes("w") ? rect.r : rect.l;
      const ay = handle.includes("n") ? rect.b : rect.t;
      const sx = handle.includes("w") ? -1 : 1, sy = handle.includes("n") ? -1 : 1;
      const dx = Math.max(0, (px - ax) * sx), dy = Math.max(0, (py - ay) * sy);
      let w = Math.max(dx, dy * ratio);
      const maxW = Math.min(sx < 0 ? ax : iw - ax, (sy < 0 ? ay : ih - ay) * ratio);
      w = maxW < minW ? maxW : Math.min(Math.max(w, minW), maxW);
      const h = w / ratio;
      const [l, r] = sx < 0 ? [ax - w, ax] : [ax, ax + w];
      const [t, b] = sy < 0 ? [ay - h, ay] : [ay, ay + h];
      return new Rect(l, t, r, b);
    }
    if (handle === "e" || handle === "w") {              // edge: width drives, height follows
      const ax = handle === "e" ? rect.l : rect.r, sx = handle === "e" ? 1 : -1;
      let w = Math.max(0, (px - ax) * sx);
      const maxW = Math.min(sx > 0 ? iw - ax : ax, 2 * Math.min(rect.cy, ih - rect.cy) * ratio);
      w = maxW < minW ? maxW : Math.min(Math.max(w, minW), maxW);
      const h = w / ratio;
      const [l, r] = sx > 0 ? [ax, ax + w] : [ax - w, ax];
      return new Rect(l, rect.cy - h / 2, r, rect.cy + h / 2);
    }
    const ay = handle === "s" ? rect.t : rect.b, sy = handle === "s" ? 1 : -1;   // edge n/s: height drives
    let h = Math.max(0, (py - ay) * sy);
    const maxH = Math.min(sy > 0 ? ih - ay : ay, 2 * Math.min(rect.cx, iw - rect.cx) / ratio);
    const minH = minW / ratio;
    h = maxH < minH ? maxH : Math.min(Math.max(h, minH), maxH);
    const w = h * ratio;
    const [t, b] = sy > 0 ? [ay, ay + h] : [ay - h, ay];
    return new Rect(rect.cx - w / 2, t, rect.cx + w / 2, b);
  }

  // The integer pixel box [left, top, right, bottom] a crop of `rect` reads from the picture. Edges are rounded to the
  // nearest pixel and clamped. With a fixed `ratio` the size is rounded as a pair (width first, height from it) so a 1:1
  // frame is exactly square instead of 593x594, as long as that stays within a pixel of the frame that was drawn.
  // A box that rounds to nothing throws rather than produce an empty file.
  function boxFromRect(rect, iw, ih, ratio = null) {
    if (ratio) {
      const w = Math.max(1, round(rect.w)), h = Math.max(1, round(w / ratio));
      if (w <= iw && h <= ih && Math.abs(h - rect.h) <= 1) {
        const l = clamp(round(rect.cx - w / 2), 0, iw - w), t = clamp(round(rect.cy - h / 2), 0, ih - h);
        return [l, t, l + w, t + h];
      }
    }
    const l = clamp(round(rect.l), 0, iw), t = clamp(round(rect.t), 0, ih);
    const r = clamp(round(rect.r), 0, iw), b = clamp(round(rect.b), 0, ih);
    if (r - l < 1 || b - t < 1) throw new Error("The selection is empty.");
    return [l, t, r, b];
  }

  // What is under the canvas point: a handle name, "move" (inside) or null (outside). `rc` is the frame in CANVAS
  // pixels, `grab` the handle's grab radius. Corners win over edges, edges over the interior.
  function hitTest(rc, cx, cy, grab) {
    const [l, t, r, b] = rc;
    if ((r - l) > 0 && (b - t) > 0) grab = Math.min(grab, (r - l) / 3, (b - t) / 3);
    const nl = Math.abs(cx - l) <= grab, nr = Math.abs(cx - r) <= grab;
    const nt = Math.abs(cy - t) <= grab, nb = Math.abs(cy - b) <= grab;
    if (!(l - grab <= cx && cx <= r + grab && t - grab <= cy && cy <= b + grab)) return null;
    if (nt && nl) return "nw";
    if (nt && nr) return "ne";
    if (nb && nl) return "sw";
    if (nb && nr) return "se";
    if (nt) return "n";
    if (nb) return "s";
    if (nl) return "w";
    if (nr) return "e";
    return (l < cx && cx < r && t < cy && cy < b) ? "move" : null;
  }

  // picture -> canvas: canvas = (picture - origin) * scale; (ox, oy) is the picture point at the canvas's top-left
  class View {
    constructor(scale = 1, ox = 0, oy = 0) { this.scale = scale; this.ox = ox; this.oy = oy; }
    toCanvas(ix, iy) { return [(ix - this.ox) * this.scale, (iy - this.oy) * this.scale]; }
    toImage(cx, cy) { return [cx / this.scale + this.ox, cy / this.scale + this.oy]; }
    rectToCanvas(rect) {
      const [l, t] = this.toCanvas(rect.l, rect.t), [r, b] = this.toCanvas(rect.r, rect.b);
      return [l, t, r, b];
    }
    static fitScale(iw, ih, cw, ch, pad = 0) {
      return Math.max(1e-6, Math.min((cw - 2 * pad) / Math.max(iw, 1), (ch - 2 * pad) / Math.max(ih, 1)));
    }
    static zoomLimits(iw, ih, cw, ch) {      // never below Fit (or 100% when Fit is bigger), up to 16x (more when Fit is large)
      const fit = View.fitScale(iw, ih, cw, ch);
      return [Math.min(fit, 1), Math.max(16, fit * 4)];
    }
    fit(iw, ih, cw, ch, pad = 0) {
      this.scale = View.fitScale(iw, ih, cw, ch, pad);
      this.ox = iw / 2 - cw / 2 / this.scale;
      this.oy = ih / 2 - ch / 2 / this.scale;
    }
    clampPan(iw, ih, cw, ch) {               // keep the picture covering the canvas; a smaller one sits centred
      const vw = cw / this.scale, vh = ch / this.scale;
      this.ox = vw >= iw ? iw / 2 - vw / 2 : clamp(this.ox, 0, iw - vw);
      this.oy = vh >= ih ? ih / 2 - vh / 2 : clamp(this.oy, 0, ih - vh);
    }
    zoomAt(cx, cy, factor, iw, ih, cw, ch) {
      this.setScaleAt(cx, cy, this.scale * factor, iw, ih, cw, ch);
    }
    setScaleAt(cx, cy, scale, iw, ih, cw, ch) {   // the picture point under (cx, cy) stays under it
      const [lo, hi] = View.zoomLimits(iw, ih, cw, ch);
      scale = clamp(scale, lo, hi);
      const [ix, iy] = this.toImage(cx, cy);
      this.scale = scale;
      this.ox = ix - cx / scale;
      this.oy = iy - cy / scale;
      this.clampPan(iw, ih, cw, ch);
    }
    panBy(dcx, dcy, iw, ih, cw, ch) {
      this.ox -= dcx / this.scale;
      this.oy -= dcy / this.scale;
      this.clampPan(iw, ih, cw, ch);
    }
    visibleBox(iw, ih, cw, ch) {             // the part of the picture on screen, in picture pixels
      const [l, t] = this.toImage(0, 0), [r, b] = this.toImage(cw, ch);
      return [Math.max(0, l), Math.max(0, t), Math.min(iw, r), Math.min(ih, b)];
    }
  }

  // The trigger word: is it already in the caption as a phrase of its own (at the start or end, or between commas or
  // spaces, not inside another word), whatever the case? And the caption with it put first, as the captioner writes
  // it: "trigger, caption".
  function hasTrigger(text, trigger) {
    const t = String(trigger || "").trim();
    if (!t) return false;
    const esc = t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp("(^|[,;.\\s])" + esc + "($|[,;.\\s])", "i").test(String(text || ""));
  }
  function withTrigger(text, trigger) {
    const t = String(trigger || "").trim(), body = String(text || "").trim();
    if (!t || hasTrigger(body, t)) return body;
    return body ? `${t}, ${body}` : t;
  }

  const geo = {hasTrigger, withTrigger, MIN_FRAME, RATIOS, Rect, View, ratioValue, moveRect, applyRatio, resizeRect, boxFromRect, hitTest, round};
  if (typeof module !== "undefined" && module.exports) module.exports = geo;
  else root.MonkapGeo = geo;
})(typeof window !== "undefined" ? window : globalThis);
