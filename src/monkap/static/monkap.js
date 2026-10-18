// MonKap's page: go through the pictures of a folder, caption them, and frame a part of a picture to save as a new
// picture (Kapture). The files are handled on the server (core/, api.py); the frame and view maths is monkap_geo.js;
// the small helpers ($, el, api, debounce) and the open folder (S.imageFolder) are in lib.js.
"use strict";
(function () {
  if (!window.MonkapGeo) return;
  const G = window.MonkapGeo, {Rect, View} = G;
  const ITEM_H = 96, ITEM_V = 104;       // filmstrip pitch, px (a row in a narrow window, else a column)
  const mq = window.matchMedia("(max-width: 1000px)");
  const horizontal = () => mq.matches;
  const pitch = () => (horizontal() ? ITEM_H : ITEM_V);
  const EDGE = 2400;                     // long edge of the whole-picture preview
  const GRAB = 9;                        // handle grab radius, px

  const M = {
    folder: "", srcFolder: null, items: [], index: -1, trigger: "", settings: null,
    cur: null,                           // the open picture: {name, iw, ih, bmp, loaded, inherited}
    frame: null, ratio: "1:1", view: new View(), fitMode: true, tile: null,
    dirty: false, busy: false, loadToken: 0, tileSeq: 0, thumbs: new Map(), bitmaps: new Map(),
    modal: null, lastDelete: null,
  };
  let regen = false;                     // Regenerate (AI) is running
  const $q = (id) => document.getElementById(id);
  const root = $q("mk"), stage = $q("mkStage"), cv = $q("mkCanvas"), ctx = cv.getContext("2d");
  const text = $q("mkText"), strip = $q("mkStrip"), stripIn = $q("mkStripIn");
  let cw = 0, ch = 0, dpr = 1, drag = null, drawPending = false;

  // ---- small helpers -------------------------------------------------------------------------------------------
  const qs = (o) => Object.entries(o).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  const url = (route, o = {}) => `/api/${route}?${qs({folder: M.folder, ...o})}`;
  const capExtNow = () => (M.settings && M.settings.caption_ext) || ".txt";
  const active = () => true;
  const ratioValue = () => G.ratioValue(M.ratio);
  const clamp = (x, lo, hi) => Math.min(Math.max(x, lo), hi);
  const pos = (e) => { const r = stage.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };

  async function bitmap(u) {
    const r = await fetch(u);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return createImageBitmap(await r.blob());
  }

  function status(msg, kind = "", action = null) {
    const s = $q("mkStatus");
    s.className = "field-hint mk-status" + (kind ? " " + kind : "");
    s.textContent = msg || "";
    if (action) s.append(el("button", {class: "btn btn-sm", text: action.label, onclick: action.run}));
  }
  function message(msg) { $q("mkMsg").textContent = msg || ""; }

  // ---- the folder and its filmstrip ------------------------------------------------------------------------
  async function loadSettings() {
    const r = await api("/api/state");
    M.settings = r.settings;
    M.tasks = r.tasks;
    setRatio(M.settings.ratio, false);
  }

  async function fetchList() {
    const r = await api(url("list"));
    M.folder = r.folder;
    M.trigger = r.trigger || "";
    return r.items;
  }

  async function loadFolder(select) {
    M.folder = (S.imageFolder || "").trim();
    M.srcFolder = M.folder;
    $q("mkChange").title = M.folder ? `Folder: ${M.folder}. Click to open another.` : "Open a folder of pictures.";
    closePicture();
    if (!M.folder) { message("Open a folder of pictures (the link above)."); M.items = []; drawStrip(true); return; }
    try {
      if (!M.settings) await loadSettings();
      M.items = await fetchList();
    } catch (e) { M.items = []; message(e.message); drawStrip(true); return; }
    drawStrip(true);
    $q("mkChange").textContent = folderLabel();
    if (!M.items.length) { message("No pictures in this folder (png, jpg, webp, bmp, avif)."); return; }
    const i = select ? M.items.findIndex((it) => it[0] === select) : 0;
    await open(Math.max(0, i));
  }

  // the list again after files appeared or went (a Kapture, an undo), without touching the open picture
  async function refreshList() {
    M.items = await fetchList();
    const i = M.cur ? M.items.findIndex((it) => it[0] === M.cur.name) : -1;
    M.index = i;
    drawStrip(true);
    scrollStripTo(i);
    syncButtons();
  }

  function drawStrip(reset = false) {
    const h = horizontal(), p = pitch();
    strip.classList.toggle("h", h); strip.classList.toggle("v", !h);
    if (reset) { M.thumbs.clear(); stripIn.replaceChildren(); }
    const total = M.items.length * p + 8;
    stripIn.style.width = h ? total + "px" : "auto";
    stripIn.style.height = h ? "100%" : total + "px";
    const at = h ? strip.scrollLeft : strip.scrollTop, size = (h ? strip.clientWidth : strip.clientHeight) || 600;
    const first = Math.max(0, Math.floor(at / p) - 4), last = Math.min(M.items.length - 1, Math.ceil((at + size) / p) + 4);
    for (const [i, node] of [...M.thumbs]) if (i < first || i > last) { node.remove(); M.thumbs.delete(i); }
    for (let i = first; i <= last; i++) {
      if (M.thumbs.has(i)) continue;
      const [name, has] = M.items[i];
      const info = el("button", {class: "mk-i", type: "button", text: "i", title: "About this picture", onclick: (e) => {
        e.stopPropagation();
        popover(e.currentTarget, [name, M.items[i][1] ? "Has a caption" : "Empty caption"]);
      }});
      const node = el("div", {class: "mk-th" + (i === M.index ? " cur" : ""), onclick: () => go(i - M.index)},
        el("img", {src: url("thumb", {name}), alt: "", draggable: "false"}),
        el("span", {class: "pill" + (has ? " has" : ""), text: "Empty caption"}), info);
      node.style[h ? "left" : "top"] = (i * p + 4) + "px";
      stripIn.append(node);
      M.thumbs.set(i, node);
    }
  }
  const drawStripSoon = () => requestAnimationFrame(() => drawStrip());
  function markCurrent() { for (const [i, n] of M.thumbs) n.classList.toggle("cur", i === M.index); }
  function scrollStripTo(i) {
    if (i < 0) return;
    const h = horizontal(), p = pitch(), size = h ? strip.clientWidth : strip.clientHeight;
    const to = clamp(i * p - (size - p) / 2, 0, Math.max(0, M.items.length * p - size));
    if (h) strip.scrollLeft = to; else strip.scrollTop = to;
    drawStrip();
    markCurrent();
  }
  function setDot(name, has) {                                   // the "Empty caption" pill of a picture
    const i = M.items.findIndex((it) => it[0] === name);
    if (i < 0) return;
    M.items[i][1] = has ? 1 : 0;
    const n = M.thumbs.get(i);
    if (n) n.querySelector(".pill").classList.toggle("has", !!has);
  }

  // ---- small pop-ups --------------------------------------------------------------------------------------------
  let pop = null;
  function closePop() { if (pop) { pop.remove(); pop = null; } }
  function popover(anchor, lines) {
    closePop();
    pop = el("div", {class: "mk-pop"}, lines.map((l) => el("div", {text: l})));
    document.body.append(pop);
    const r = anchor.getBoundingClientRect();
    pop.style.top = Math.max(8, Math.min(r.bottom + 6, innerHeight - pop.offsetHeight - 8)) + "px";
    pop.style.left = clamp(r.right - pop.offsetWidth, 8, innerWidth - pop.offsetWidth - 8) + "px";
  }
  document.addEventListener("pointerdown", (e) => { if (pop && !pop.contains(e.target) && !e.target.closest(".mk-i")) closePop(); }, true);

  // ---- opening a picture ----------------------------------------------------------------------------------
  function closePicture() {
    M.loadToken++;
    M.cur = null; M.frame = null; M.tile = null; M.dirty = false; M.index = -1;
    text.value = ""; $q("mkBadge").style.display = "none";
    redraw(); updateMode(); syncButtons();
  }

  async function getBitmap(name) {
    const key = `${M.folder}\n${name}`;
    let hit = M.bitmaps.get(key);
    if (hit) { M.bitmaps.delete(key); M.bitmaps.set(key, hit); return hit; }
    hit = await bitmap(url("view", {name, edge: EDGE}));
    M.bitmaps.set(key, hit);
    for (const [k, old] of [...M.bitmaps]) {                      // a handful kept: the open picture and its neighbours
      if (M.bitmaps.size <= 5) break;
      if (M.cur && old === M.cur.bmp) continue;
      M.bitmaps.delete(k);
      old.close();
    }
    return hit;
  }

  async function open(i) {
    if (i < 0 || i >= M.items.length || M.busy) return;
    try { await flush(); } catch (e) { status(e.message, "err"); return; }      // the caption stays and the page stays
    const name = M.items[i][0], my = ++M.loadToken;
    M.index = i; markCurrent(); scrollStripTo(i);
    message("Loading…");
    let info, bmp;
    try {
      [info, bmp] = await Promise.all([api(url("info", {name, ext: capExtNow()})), getBitmap(name)]);
    } catch (e) {
      if (my === M.loadToken) { M.cur = null; redraw(); message(`${name}: ${e.message}`); }
      return;
    }
    if (my !== M.loadToken) return;
    message("");
    M.cur = {name, iw: info.size[0], ih: info.size[1], bmp, loaded: info.caption, inherited: info.inherited};
    M.frame = null; M.tile = null; M.dirty = false;
    text.value = info.caption || "";
    const badge = $q("mkBadge");
    badge.textContent = "↳ copied from the original - check that it fits this crop";
    badge.style.display = info.inherited ? "" : "none";
    status("");
    resize();                                     // also fits the view and draws
    updateMode(); syncButtons();
    focusHome();
    for (const j of [i + 1, i - 1]) if (M.items[j]) getBitmap(M.items[j][0]).catch(() => {});   // the neighbours, ready
  }

  const go = (d) => open(M.index + d);

  // ---- the canvas -----------------------------------------------------------------------------------------
  function resize() {
    const r = stage.getBoundingClientRect();
    if (r.width < 20 || r.height < 20) return;
    dpr = window.devicePixelRatio || 1;
    cw = Math.floor(r.width); ch = Math.floor(r.height);
    cv.width = Math.round(cw * dpr); cv.height = Math.round(ch * dpr);
    const c = M.cur;
    if (c) {
      if (M.fitMode) M.view.fit(c.iw, c.ih, cw, ch);
      else M.view.clampPan(c.iw, c.ih, cw, ch);
      scheduleTile();
    }
    draw();                                       // now, not next frame: a resized canvas is blank until drawn
  }
  new ResizeObserver(resize).observe(stage);


  function fitView() {
    const c = M.cur;
    if (!c) return;
    M.fitMode = true; M.view.fit(c.iw, c.ih, cw, ch); scheduleTile(); redraw();
  }
  function actualSize() {
    const c = M.cur;
    if (!c) return;
    M.fitMode = false; M.view.setScaleAt(cw / 2, ch / 2, 1, c.iw, c.ih, cw, ch); scheduleTile(); redraw();
  }
  function zoomBy(f) {
    const c = M.cur;
    if (!c) return;
    M.view.zoomAt(cw / 2, ch / 2, f, c.iw, c.ih, cw, ch);
    M.fitMode = Math.abs(M.view.scale - View.fitScale(c.iw, c.ih, cw, ch)) < 1e-6;
    scheduleTile(); redraw();
  }
  // the label is the real zoom (1 picture pixel per CSS pixel = 100%)
  function updateZoomUI() {
    const c = M.cur;
    for (const id of ["mkZoomIn", "mkZoomOut", "mkActual", "mkFit"]) $q(id).disabled = !c;
    $q("mkActual").textContent = c ? Math.round(M.view.scale * 100) + "%" : "100%";
  }

  // which buttons make sense now: Kapture needs a frame on the picture
  function syncButtons() {
    const c = M.cur;
    $q("mkKapture").disabled = !c || !M.frame || M.busy;
    $q("mkPrev").disabled = !c || M.index <= 0;
    $q("mkNext").disabled = !c || M.index >= M.items.length - 1;
    $q("mkDelete").disabled = !c;
    $q("mkRegen").disabled = !c || regen;
    $q("mkInfo").style.display = c ? "" : "none";
    root.classList.toggle("has-frame", !!(c && M.frame));
    triggerState();
  }

  // the trigger word button: shown when the folder has one, greyed out when the caption already has it
  function triggerState() {
    const b = $q("mkTrigger");
    b.style.display = M.trigger && M.cur ? "" : "none";
    const has = G.hasTrigger(text.value, M.trigger);
    b.disabled = has;
    b.title = has ? "The trigger word is already in the caption" : `Put the trigger word first: ${M.trigger}`;
  }
  function addTrigger() {
    if (!M.cur || !M.trigger) return;
    const next = G.withTrigger(text.value, M.trigger);
    if (next === text.value.trim()) return;
    text.value = next;
    M.dirty = true;
    $q("mkBadge").style.display = "none";
    autosave(); triggerState();
    text.focus({preventScroll: true});
    text.setSelectionRange(next.length, next.length);
  }

  function redraw() {
    if (drawPending) return;
    drawPending = true;
    requestAnimationFrame(() => { drawPending = false; draw(); });
  }

  function draw() { paint(); updateZoomUI(); }

  function paint() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#10151b"; ctx.fillRect(0, 0, cw, ch);
    const c = M.cur;
    if (!c) return;
    const v = M.view, bmp = c.bmp;
    const [x0, y0] = v.toCanvas(0, 0), [x1, y1] = v.toCanvas(c.iw, c.ih);
    ctx.imageSmoothingQuality = "high";
    ctx.imageSmoothingEnabled = v.scale * (c.iw / bmp.width) * dpr < 1 || bmp.width < c.iw;   // crisp pixels when zoomed in
    ctx.drawImage(bmp, x0, y0, x1 - x0, y1 - y0);
    const t = M.tile;
    if (t && t.name === c.name) {
      const [tx0, ty0] = v.toCanvas(t.box[0], t.box[1]), [tx1, ty1] = v.toCanvas(t.box[2], t.box[3]);
      ctx.imageSmoothingEnabled = (tx1 - tx0) * dpr / t.bmp.width < 1;
      ctx.drawImage(t.bmp, tx0, ty0, tx1 - tx0, ty1 - ty0);
    }
    if (M.frame) drawFrame(v.rectToCanvas(M.frame));
  }

  function currentBox() {
    const c = M.cur;
    if (!c || !M.frame) return null;
    try { return G.boxFromRect(M.frame, c.iw, c.ih, ratioValue()); } catch (e) { return null; }
  }

  function drawFrame([l, t, r, b]) {
    ctx.save();
    ctx.fillStyle = "rgba(8,12,18,.62)";
    ctx.beginPath(); ctx.rect(0, 0, cw, ch); ctx.rect(l, t, r - l, b - t); ctx.fill("evenodd");
    if (!M.settings || M.settings.show_thirds) {
      ctx.strokeStyle = "rgba(255,255,255,.28)"; ctx.lineWidth = 1; ctx.beginPath();
      for (const k of [1 / 3, 2 / 3]) {
        ctx.moveTo(l + (r - l) * k, t); ctx.lineTo(l + (r - l) * k, b);
        ctx.moveTo(l, t + (b - t) * k); ctx.lineTo(r, t + (b - t) * k);
      }
      ctx.stroke();
    }
    ctx.lineWidth = 2; ctx.strokeStyle = "rgba(0,0,0,.6)"; ctx.strokeRect(l, t, r - l, b - t);
    ctx.lineWidth = 1; ctx.strokeStyle = "#fff"; ctx.strokeRect(l + .5, t + .5, r - l - 1, b - t - 1);
    const hs = 5, mx = (l + r) / 2, my = (t + b) / 2;
    ctx.fillStyle = "#3B82F6"; ctx.strokeStyle = "#fff";
    for (const [x, y] of [[l, t], [mx, t], [r, t], [r, my], [r, b], [mx, b], [l, b], [l, my]]) {
      ctx.fillRect(x - hs, y - hs, hs * 2, hs * 2); ctx.strokeRect(x - hs + .5, y - hs + .5, hs * 2 - 1, hs * 2 - 1);
    }
    const box = currentBox();
    if (box) {
      const label = `${box[2] - box[0]} × ${box[3] - box[1]} px`;
      ctx.font = "600 12px Segoe UI, Arial, sans-serif";
      const w = ctx.measureText(label).width + 14, ly = t - 26 >= 2 ? t - 26 : t + 6;
      ctx.fillStyle = "rgba(15,20,28,.88)"; ctx.fillRect(l, ly, w, 20);
      ctx.fillStyle = "#fff"; ctx.textBaseline = "middle"; ctx.fillText(label, l + 7, ly + 10);
    }
    ctx.restore();
  }

  // the part of the picture on screen, sharper than the whole-picture preview, fetched when zoomed in
  const scheduleTile = debounce(fetchTile, 140);
  async function fetchTile() {
    const c = M.cur;
    if (!c || c.bmp.width >= c.iw) return;                     // the preview is already at native size
    const v = M.view, need = v.scale * dpr, have = c.bmp.width / c.iw;
    if (need <= have * 1.1) { M.tile = null; redraw(); return; }
    const [l, t, r, b] = v.visibleBox(c.iw, c.ih, cw, ch);
    const t0 = M.tile;
    if (t0 && t0.name === c.name && t0.box[0] <= l && t0.box[1] <= t && t0.box[2] >= r && t0.box[3] >= b && t0.need >= need * 0.9) return;
    const px = (r - l) * 0.2, py = (b - t) * 0.2;
    const box = [Math.max(0, Math.floor(l - px)), Math.max(0, Math.floor(t - py)),
                 Math.min(c.iw, Math.ceil(r + px)), Math.min(c.ih, Math.ceil(b + py))];
    const w = Math.min(box[2] - box[0], Math.ceil((box[2] - box[0]) * need), 4096);
    const seq = ++M.tileSeq, name = c.name;
    let bmp;
    try { bmp = await bitmap(url("tile", {name, x0: box[0], y0: box[1], x1: box[2], y1: box[3], w})); } catch (e) { return; }
    if (seq !== M.tileSeq || !M.cur || M.cur.name !== name) { bmp.close(); return; }
    if (M.tile) M.tile.bmp.close();
    M.tile = {name, box, bmp, need};
    redraw();
  }

  // ---- the frame ------------------------------------------------------------------------------------------
  function frameChanged() { updateMode(); syncButtons(); }
  function clearFrame() { if (M.frame) { M.frame = null; frameChanged(); redraw(); } }

  function drawFrom([ax, ay], px, py) {                         // a new frame from the anchor to the pointer
    const c = M.cur, ratio = ratioValue();
    const sx = px >= ax ? 1 : -1, sy = py >= ay ? 1 : -1;
    let dx = Math.abs(px - ax), dy = Math.abs(py - ay);
    const roomX = sx > 0 ? c.iw - ax : ax, roomY = sy > 0 ? c.ih - ay : ay;
    if (ratio) {
      const w = Math.min(Math.max(dx, dy * ratio), roomX, roomY * ratio);
      dx = w; dy = w / ratio;
    } else { dx = Math.min(dx, roomX); dy = Math.min(dy, roomY); }
    const x2 = ax + sx * dx, y2 = ay + sy * dy;
    return new Rect(Math.min(ax, x2), Math.min(ay, y2), Math.max(ax, x2), Math.max(ay, y2));
  }

  const CURSORS = {nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize", n: "ns-resize",
                   s: "ns-resize", e: "ew-resize", w: "ew-resize", move: "move"};

  const capture = (e) => { try { stage.setPointerCapture(e.pointerId); } catch (_) { /* not an active pointer */ } };
  const touches = new Map();                                       // finger id -> [x, y]
  let pinch = null;                                                // two fingers: the last distance and middle
  const fingers = () => [...touches.values()];
  const spread = ([a, b]) => ({d: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1, mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]});

  stage.addEventListener("pointerdown", (e) => {
    const c = M.cur;
    if (!c || e.target !== cv || (e.button !== 0 && e.button !== 1)) return;
    e.preventDefault();
    stage.focus({preventScroll: true});
    const [cx, cy] = pos(e), [ix, iy] = M.view.toImage(cx, cy);
    if (e.pointerType === "touch") {
      touches.set(e.pointerId, [cx, cy]);
      capture(e);
      if (touches.size >= 2) {                                     // the second finger: what the first began is dropped
        if (drag && drag.kind === "draw") M.frame = drag.prev;
        drag = null;
        pinch = spread(fingers());
        frameChanged(); redraw();
        return;
      }
    }
    if (e.button === 1) {
      drag = {kind: "pan", cx, cy}; stage.style.cursor = "grabbing";
    } else {
      const hit = M.frame ? G.hitTest(M.view.rectToCanvas(M.frame), cx, cy, GRAB) : null;
      if (hit && hit !== "move") drag = {kind: "resize", handle: hit};
      else if (hit === "move") drag = {kind: "move", start: [ix, iy], rect: M.frame};
      else drag = {kind: "draw", a: [clamp(ix, 0, c.iw), clamp(iy, 0, c.ih)], from: [cx, cy], moved: false, prev: M.frame};
    }
    capture(e);
    updateMode();
  });

  stage.addEventListener("pointermove", (e) => {
    const c = M.cur;
    if (!c) return;
    const [cx, cy] = pos(e);
    if (touches.has(e.pointerId)) touches.set(e.pointerId, [cx, cy]);
    if (pinch) {
      if (touches.size >= 2) {
        const now = spread(fingers());
        M.view.zoomAt(now.mid[0], now.mid[1], now.d / pinch.d, c.iw, c.ih, cw, ch);
        M.view.panBy(now.mid[0] - pinch.mid[0], now.mid[1] - pinch.mid[1], c.iw, c.ih, cw, ch);
        pinch = now; M.fitMode = false; scheduleTile(); redraw();
      }
      return;
    }
    if (!drag) {
      const hit = M.frame ? G.hitTest(M.view.rectToCanvas(M.frame), cx, cy, GRAB) : null;
      stage.style.cursor = CURSORS[hit] || "crosshair";
      return;
    }
    const [ix, iy] = M.view.toImage(cx, cy);
    if (drag.kind === "pan") {
      M.view.panBy(cx - drag.cx, cy - drag.cy, c.iw, c.ih, cw, ch);
      drag.cx = cx; drag.cy = cy; M.fitMode = false; scheduleTile();
    } else if (drag.kind === "resize") {
      M.frame = G.resizeRect(M.frame, drag.handle, ix, iy, c.iw, c.ih, ratioValue());
    } else if (drag.kind === "move") {
      M.frame = G.moveRect(drag.rect, ix - drag.start[0], iy - drag.start[1], c.iw, c.ih);
    } else {
      if (!drag.moved && Math.hypot(cx - drag.from[0], cy - drag.from[1]) < 4) return;
      drag.moved = true;
      M.frame = drawFrom(drag.a, ix, iy);
    }
    frameChanged(); redraw();
  });

  const endDrag = (e) => {
    touches.delete(e.pointerId);
    if (pinch) { if (touches.size < 2) pinch = null; return; }     // fingers still down are not a new drag
    if (!drag) return;
    const d = drag; drag = null;
    try { stage.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
    if (d.kind === "draw") {
      if (!d.moved) M.frame = null;                              // a click without a drag drops the selection
      else if (M.frame && (M.frame.w < G.MIN_FRAME || M.frame.h < G.MIN_FRAME)) M.frame = null;   // a slip, not a frame
    }
    stage.style.cursor = "crosshair";
    frameChanged(); redraw();
  };
  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);
  stage.addEventListener("mousedown", (e) => { if (e.button === 1) e.preventDefault(); });   // no autoscroll
  stage.addEventListener("contextmenu", (e) => e.preventDefault());       // the right button is free
  stage.addEventListener("auxclick", (e) => e.preventDefault());

  stage.addEventListener("wheel", (e) => {
    const c = M.cur;
    if (!c) return;
    e.preventDefault();
    const [cx, cy] = pos(e);
    M.view.zoomAt(cx, cy, Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0015)), c.iw, c.ih, cw, ch);
    M.fitMode = Math.abs(M.view.scale - View.fitScale(c.iw, c.ih, cw, ch)) < 1e-6;
    scheduleTile(); redraw();
  }, {passive: false});

  function nudge(key, shift, ctrl) {                             // arrows move the frame by a pixel, Ctrl resizes it
    const c = M.cur, f = M.frame;
    if (!c || !f) return false;
    const d = shift ? 10 : 1;
    const dx = key === "ArrowRight" ? d : key === "ArrowLeft" ? -d : 0;
    const dy = key === "ArrowDown" ? d : key === "ArrowUp" ? -d : 0;
    M.frame = ctrl ? G.resizeRect(f, "se", f.r + dx, f.b + dy, c.iw, c.ih, ratioValue()) : G.moveRect(f, dx, dy, c.iw, c.ih);
    redraw();
    return true;
  }

  function setRatio(name, save = true) {
    M.ratio = name;
    const box = $q("mkRatios");
    if (!box.children.length) {
      for (const n of Object.keys(G.RATIOS)) box.append(el("button", {type: "button", "data-r": n, text: n,
        onclick: () => setRatio(n)}));
    }
    for (const b of box.children) b.classList.toggle("on", b.dataset.r === name);
    const c = M.cur;
    if (M.frame && c) M.frame = G.applyRatio(M.frame, ratioValue(), c.iw, c.ih);
    redraw();
    if (save && M.settings) {
      M.settings.ratio = name;
      api("/api/settings", {settings: {ratio: name}}).catch(() => {});
    }
  }

  // ---- typing mode and selection mode ------------------------------------------------------------------------
  // The caption box is home: typing is what you do most. Working on a frame (the stage has the focus and a frame
  // exists) is a mode of its own, shown by a ring and a banner, in which C saves the frame.
  function updateMode() { root.classList.toggle("selecting", !!M.frame && document.activeElement === stage); }
  function focusHome() {
    if (!active() || M.modal) return;
    if (M.frame && document.activeElement === stage) return;
    text.focus({preventScroll: true});
  }
  stage.addEventListener("focus", updateMode);
  stage.addEventListener("blur", updateMode);

  // ---- the caption ----------------------------------------------------------------------------------------
  let saving = Promise.resolve();
  const flush = () => (saving = saving.then(doFlush, doFlush));

  async function doFlush() {
    const c = M.cur;
    if (!c || !M.dirty) return;
    const sent = text.value;
    const r = await api("/api/caption", {folder: M.folder, name: c.name, text: sent, loaded: c.loaded, ext: capExtNow()});
    c.loaded = sent.trim() || null;
    if (text.value === sent) M.dirty = false;                     // typed on meanwhile: the next save takes it
    setDot(c.name, r.has_caption);
    if (r.conflict) status(`Saved. The caption file had changed on disk: the other text is kept in ${r.backup}`, "warn");
    else if (r.kind === "cleared") status(`Caption removed${r.backup ? ` (the old text is kept in ${r.backup})` : ""}: the picture is left out of training until it has one.`, "warn");
    else if (r.kind === "written") status("Caption saved.", "ok");
  }
  const autosave = debounce(() => flush().catch((e) => status(`Could not save the caption: ${e.message}`, "err")), 900);
  text.addEventListener("input", () => {
    M.dirty = true;
    triggerState();
    $q("mkBadge").style.display = "none";
    autosave();
  });
  text.addEventListener("focus", updateMode);

  // ---- Kapture ------------------------------------------------------------------------------------------------
  async function kapture() {
    const c = M.cur;
    if (!c || M.busy) return;
    const box = currentBox();
    if (!box) { status("Nothing to kapture yet - drag on the picture to select an area first.", "warn"); return; }
    M.busy = true;
    syncButtons();
    try {
      const r = await api("/api/kapture", {folder: M.folder, name: c.name, box, text: text.value,
        trigger: M.trigger, ext: capExtNow()});
      await refreshList();
      const how = {empty: "no caption yet", copy: "caption copied", trigger: "caption copied with the trigger word"}[r.mode] || "";
      status(`Saved ✓  ${r.name}  (${r.size[0]}×${r.size[1]}${how ? ", " + how : ""})${r.notes.length ? " - " + r.notes.join("; ") : ""}`,
             r.notes.length ? "warn" : "ok");
    } catch (e) { status(`Kapture failed: ${e.message}`, "err"); }
    finally {
      M.busy = false; syncButtons();
      if (M.frame && active()) stage.focus({preventScroll: true});     // a click on the Kapture button must not end selection mode
    }
  }

  // ---- Regenerate (AI): the model and task chosen in the settings, for this one picture ------------------------------
  async function regenerate() {
    const c = M.cur;
    if (!c || regen) return;
    if (!M.settings.lm_model) { openSettings("ai"); return; }
    regen = true;
    syncButtons();
    const prev = text.value;
    try {
      await flush();                                             // what is typed is on disk first, so nothing is lost
      status("Writing the caption…");
      const r = await api("/api/recaption", {folder: M.folder, name: c.name});
      if (M.cur !== c) { status(`${c.name}: new caption written.`, "ok"); return; }     // the user moved on meanwhile
      text.value = r.caption; c.loaded = r.caption; M.dirty = false;
      setDot(c.name, true);
      triggerState();
      status("New caption written.", "ok", prev.trim() && prev.trim() !== r.caption ? {label: "Restore previous caption", run: () => {
        text.value = prev; M.dirty = true; $q("mkBadge").style.display = "none"; triggerState();
        flush().then(() => status("The previous caption is back.", "ok")).catch((e) => status(e.message, "err"));
      }} : null);
    } catch (e) { if (M.cur === c) status(`Could not regenerate: ${e.message}`, "err"); }
    finally { regen = false; syncButtons(); }
  }

  // ---- delete ---------------------------------------------------------------------------------------------------
  async function requestDelete() {
    const c = M.cur;
    if (!c || M.busy) return;
    const s = M.settings || {};
    const soft = s.delete_mode !== "hard";
    if (s.confirm_delete !== false) {
      const body = el("div", {},
        el("p", {class: "lead", text: soft
          ? "The picture and its caption move into this folder's removed/ folder; Undo brings them back."
          : "The picture and its caption are erased for good. This cannot be undone."}),
        el("div", {class: "quote", text: `${c.name}  ·  ${c.iw}×${c.ih} px\n${text.value.trim() || "(no caption)"}`}));
      if (!(await confirmDialog(soft ? "Remove this picture?" : "Delete this picture for good?", body, soft ? "Remove" : "Delete", soft))) return;
    }
    M.busy = true;
    M.dirty = false;                                             // a caption of a picture that is going is not saved
    const name = c.name, at = M.index;
    try {
      const r = await api("/api/delete", {folder: M.folder, name, ext: capExtNow()});
      M.items.splice(at, 1);
      M.thumbs.clear(); stripIn.replaceChildren();
      closePicture();
      M.busy = false;
      M.index = -1;
      if (!M.items.length) { drawStrip(true); message("No pictures left in this folder."); }
      else { drawStrip(true); await open(Math.min(at, M.items.length - 1)); }
      if (r.mode === "soft") {
        M.lastDelete = {moved: r.moved, name};
        status(`${name} moved to removed/.`, "ok", {label: "Undo", run: undoDelete});
      } else {
        status(`${name} deleted${r.purged ? ` (${r.purged} cached latent file${r.purged > 1 ? "s" : ""} too)` : ""}.`, "ok");
      }
    } catch (e) { M.busy = false; status(`Could not delete: ${e.message}`, "err"); }
  }

  async function undoDelete() {
    const d = M.lastDelete;
    if (!d) return;
    M.lastDelete = null;
    try {
      await api("/api/undelete", {folder: M.folder, moved: d.moved});
      M.items = await fetchList();
      drawStrip(true);
      await open(Math.max(0, M.items.findIndex((it) => it[0] === d.name)));
      status(`${d.name} is back.`, "ok");
    } catch (e) { status(`Undo failed: ${e.message}`, "err"); }
  }

  // ---- dialogs --------------------------------------------------------------------------------------------------
  function dialog(content) {
    const veil = el("div", {class: "mk-veil"}, el("div", {class: "mk-dialog", role: "dialog", "aria-modal": "true"}, content));
    document.body.append(veil);
    M.modal = veil;
    const done = () => { veil.remove(); M.modal = null; focusHome(); };
    return {veil, done};
  }

  // Enter = yes (only after a moment, so the key that opened it cannot answer it), Esc = no
  function confirmDialog(title, body, yesLabel, safe) {
    return new Promise((resolve) => {
      const yes = el("button", {class: "btn " + (safe ? "btn-primary" : "btn-danger"), text: yesLabel});
      const no = el("button", {class: "btn", text: "Cancel"});
      const {veil, done} = dialog([el("h2", {text: title}), body, el("div", {class: "buttons"}, no, yes)]);
      const shown = performance.now();
      const finish = (v) => { document.removeEventListener("keydown", onKey, true); done(); resolve(v); };
      const onKey = (e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
        else if (e.key === "Enter") {
          e.preventDefault(); e.stopPropagation();
          if (!e.repeat && performance.now() - shown > 250) finish(true);
        }
      };
      document.addEventListener("keydown", onKey, true);
      yes.addEventListener("click", () => finish(true));
      no.addEventListener("click", () => finish(false));
      veil.addEventListener("mousedown", (e) => { if (e.target === veil) finish(false); });
      yes.focus();
    });
  }

  // ---- the folder ---------------------------------------------------------------------------------------------------
  function folderLabel() {
    const parts = (M.folder || "").split(/[\\/]+/).filter(Boolean);
    return parts.length ? `${parts[parts.length - 1]} · change…` : "Open a folder…";
  }

  // in MonKap's own window the system's folder dialog is there (window.pywebview, set up a moment after the page loads)
  const nativePicker = () => !!(window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder);

  async function openFolderDialog() {
    if (M.modal) return;
    const path = el("input", {type: "text", spellcheck: "false", placeholder: "Folder path", value: M.folder || "", style: "flex:1 1 280px"});
    const trig = el("input", {type: "text", spellcheck: "false", placeholder: "optional, e.g. ohwx", value: M.trigger || ""});
    const list = el("div", {class: "mk-browse"});
    const here = el("div", {class: "field-hint"});
    const err = el("div", {class: "field-hint warn"});
    const go = el("button", {class: "btn btn-primary", text: "Open this folder"});
    const cancel = el("button", {class: "btn", text: "Cancel"});
    const row = (label, p) => el("button", {type: "button", class: "mk-dir", text: label, onclick: () => browse(p)});
    async function browse(p) {
      err.textContent = "";
      try {
        const r = await api("/api/browse?path=" + encodeURIComponent(p || ""));
        path.value = r.path;
        list.replaceChildren();
        if (r.path && r.parent !== null) list.append(row("↑  up", r.parent));
        for (const d of r.dirs) list.append(row(d.name, d.path));
        here.textContent = r.path ? `${r.pictures} picture${r.pictures === 1 ? "" : "s"} here` : "Drives";
      } catch (e) { err.textContent = e.message; }
    }
    const recent = (M.settings && M.settings.recent) || [];
    const content = [
      el("h2", {text: "Open a folder"}),
      el("div", {class: "row"}, path, el("button", {class: "btn", text: "Go", onclick: () => browse(path.value.trim())}),
        nativePicker() ? el("button", {class: "btn", text: "Browse…", onclick: async () => {
          const got = await window.pywebview.api.pick_folder(path.value.trim());
          if (got) { path.value = got; browse(got); }
        }}) : null),
      here, list,
      recent.length ? el("div", {class: "mk-recent"}, el("div", {class: "field-hint", text: "Opened lately:"}),
        recent.map((p) => el("button", {type: "button", class: "mk-dir", text: p, onclick: () => { path.value = p; browse(p); }}))) : null,
      el("div", {class: "row"}, el("label", {class: "k", text: "Trigger word:"}), trig),
      el("div", {class: "field-hint", text: "Optional. Put first in the captions MonKap writes for this folder (a Kapture can copy it, Regenerate adds it)."}),
      err, el("div", {class: "buttons"}, cancel, go)];
    const {veil, done} = dialog(content);
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } };
    const close = () => { document.removeEventListener("keydown", onKey, true); done(); };
    document.addEventListener("keydown", onKey, true);
    cancel.addEventListener("click", close);
    veil.addEventListener("mousedown", (e) => { if (e.target === veil) close(); });
    path.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); browse(path.value.trim()); } });
    go.addEventListener("click", async () => {
      try {
        await flush().catch(() => {});
        const r = await api("/api/folder", {folder: path.value.trim(), trigger: trig.value});
        S.imageFolder = r.folder;
        M.settings.recent = [r.folder, ...(M.settings.recent || []).filter((x) => x !== r.folder)].slice(0, 12);
        close();
        await loadFolder(null);
      } catch (e) { err.textContent = e.message; }
    });
    browse(M.folder || (recent[0] || ""));
    path.focus();
  }

  // ---- settings -----------------------------------------------------------------------------------------------------
  async function openSettings(section) {
    if (M.modal) return;
    let s, tasks;
    try { const r = await api("/api/state"); s = r.settings; tasks = r.tasks; } catch (e) { status(e.message, "err"); return; }
    const field = (key, kind = "text") => el("input", {type: kind, "data-k": key, "data-t": kind, value: s[key] === undefined ? "" : String(s[key]), spellcheck: "false"});
    const tpl = field("template");
    const eg = el("div", {class: "eg"});
    const err = el("div", {class: "field-hint warn"});
    const preview = debounce(async () => {
      const r = await api("/api/template?t=" + encodeURIComponent(tpl.value)).catch(() => null);
      if (!r) return;
      eg.className = "eg" + (r.ok ? "" : " bad");
      eg.textContent = r.ok ? "Example: " + r.examples.join(", ") + " …" + (r.note ? "\n" + r.note : "") : r.error;
    }, 200);
    tpl.addEventListener("input", preview); preview();
    const radio = (name, value, label, hint) => el("label", {class: "opt"},
      el("input", {type: "radio", name, value, checked: s[name] === value}),
      el("span", {}, label, hint ? el("div", {class: "field-hint", text: hint}) : null));
    const tick = (key, label, hint) => el("label", {class: "opt"},
      el("input", {type: "checkbox", "data-k": key, checked: !!s[key]}),
      el("span", {}, label, hint ? el("div", {class: "field-hint", text: hint}) : null));
    const h3 = (t) => el("h3", {text: t, style: "margin:14px 0 4px"});

    // the caption model: its server, the model, the task and what the task tells the model
    const url = field("lm_url");
    url.placeholder = "http://localhost:1234";
    const model = el("select", {"data-k": "lm_model", "data-t": "select"});
    const found = el("div", {class: "field-hint"});
    async function loadModels() {
      found.textContent = "Asking the server…";
      let r;
      try { r = await api("/api/ai/models?url=" + encodeURIComponent(url.value.trim())); } catch (e) { r = {models: [], error: e.message}; }
      const keep = model.value || s.lm_model;
      model.replaceChildren(el("option", {value: "", text: "(none chosen)"}));
      for (const m of r.models) model.append(el("option", {value: m.id, text: m.id + (m.loaded ? "  (loaded)" : "")}));
      if (keep && !r.models.some((m) => m.id === keep)) model.append(el("option", {value: keep, text: keep + "  (not found on this server)"}));
      model.value = keep || "";
      found.textContent = r.error ? r.error :
        (r.models.length ? `${r.models.length} model${r.models.length > 1 ? "s" : ""} that can see pictures. ` : "The server has no model that can see pictures. ") +
        "The pictures are sent to this address, so use one you trust.";
    }
    const task = el("select", {"data-k": "ai_task", "data-t": "select"},
      tasks.map((t) => el("option", {value: t.key, text: t.label})));
    task.value = tasks.some((t) => t.key === s.ai_task) ? s.ai_task : "training";
    const instr = el("textarea", {rows: "7", style: "width:100%"});
    const instrBox = el("div", {style: "display:none"}, instr,
      el("div", {class: "row"},
        el("button", {class: "btn btn-sm", text: "Save instruction", onclick: async () => {
          try {
            const r = await api("/api/ai/instruction", {key: task.value, text: instr.value});
            tasks = r.tasks; M.tasks = r.tasks; showInstr(); instrNote.textContent = "Saved.";
          } catch (e) { instrNote.textContent = e.message; }
        }}),
        el("button", {class: "btn btn-sm", text: "Back to the shipped text", onclick: async () => {
          const t = tasks.find((x) => x.key === task.value);
          instr.value = t.builtin;
          try { const r = await api("/api/ai/instruction", {key: task.value, text: ""}); tasks = r.tasks; M.tasks = r.tasks; instrNote.textContent = "Back to the shipped text."; }
          catch (e) { instrNote.textContent = e.message; }
        }})));
    const instrNote = el("span", {class: "field-hint"});
    instrBox.lastChild.append(instrNote);
    const showInstr = () => { const t = tasks.find((x) => x.key === task.value); instr.value = t.instruction; instrNote.textContent = t.edited ? "Edited." : "As shipped."; };
    const edit = el("button", {class: "btn btn-sm", text: "Edit instruction…", onclick: () => {
      const open = instrBox.style.display === "none";
      instrBox.style.display = open ? "" : "none";
      if (open) showInstr();
    }});
    task.addEventListener("change", () => { if (instrBox.style.display !== "none") showInstr(); const t = tasks.find((x) => x.key === task.value); tokens.value = t.tokens; });
    const tokens = field("ai_max_tokens", "number");
    tokens.className = "w-num"; tokens.min = "16"; tokens.max = "4000";

    const forgetNote = el("div", {class: "field-hint"});
    const save = el("button", {class: "btn btn-primary", text: "Save"});
    const cancel = el("button", {class: "btn", text: "Cancel"});
    const content = [
      el("h2", {text: "MonKap settings"}),
      el("div", {class: "row"}, el("label", {class: "k", text: "Names of new pictures:"}), tpl),
      eg, el("div", {class: "field-hint", text: "{parent} is the picture's name, {n} the next free number ({n:03d} pads it to three digits). A number is never used twice, even after a crop is deleted."}),
      el("div", {class: "row"}, el("label", {class: "k", text: "Caption files end in:"}), field("caption_ext")),
      el("div", {class: "field-hint", text: ".txt unless your trainer reads another extension."}),
      h3("A new picture's caption"),
      radio("caption_mode", "copy", "Copy the original's caption", "Edit it afterwards: the crop is flagged until you do."),
      radio("caption_mode", "trigger", "Copy it, with the trigger word first", "The trigger word of the folder (set when you open it)."),
      radio("caption_mode", "empty", "No caption", "The new picture has no caption file until it gets one."),
      h3("Deleting a picture"),
      radio("delete_mode", "soft", "Move it to the folder's removed/ folder", "With Undo. Recommended."),
      radio("delete_mode", "hard", "Erase it for good", "The picture and its caption. No Undo."),
      tick("confirm_delete", "Ask before deleting", "Enter confirms, Esc cancels."),
      el("div", {class: "row"}, el("label", {class: "k", text: "Latent cache folder:"}), field("latent_cache_dir")),
      el("div", {class: "field-hint", text: "Optional. A trainer's cache of the pictures' latents: erasing a picture for good also clears its files there, so a new picture can't meet old ones."}),
      h3("Caption model"),
      el("div", {class: "row"}, el("label", {class: "k", text: "Server:"}), url, el("button", {class: "btn btn-sm", text: "Refresh", onclick: loadModels})),
      el("div", {class: "row"}, el("label", {class: "k", text: "Model:"}), model), found,
      el("div", {class: "row"}, el("label", {class: "k", text: "Task:"}), task, edit), instrBox,
      el("div", {class: "row"}, el("label", {class: "k", text: "Max tokens:"}), tokens,
        el("span", {class: "field-hint", text: "How long a caption may be."})),
      tick("ai_think", "Let the model think first", "Slower, and the answer needs more room. Most models caption better and faster without it."),
      el("div", {class: "field-hint", text: "Any server with LM Studio's or OpenAI's API works (LM Studio, Ollama, llama.cpp, vLLM…)."}),
      h3("Other"),
      tick("store_provenance", "Remember where each crop came from", "A note in the folder's .monkap/provenance.jsonl; it is never a caption."),
      tick("show_thirds", "Rule-of-thirds lines in the frame"),
      el("div", {class: "row"}, el("button", {class: "btn btn-sm", text: "What does MonKap keep on this computer?", onclick: showData})),
      forgetNote,
      err, el("div", {class: "buttons"}, cancel, save)];
    async function showData() {
      try {
        const r = await api("/api/data");
        const total = r.files.reduce((a, f) => a + f[1], 0);
        forgetNote.replaceChildren(
          el("div", {text: `MonKap's own files: ${r.root}  (${r.files.length} file${r.files.length === 1 ? "" : "s"}, ${Math.max(1, Math.round(total / 1024))} KB: your settings and the folders you opened).`}),
          el("div", {text: "Inside a dataset folder MonKap keeps a hidden .monkap folder (the crop ledger and caption backups), and nothing else besides the pictures and captions you asked for. Pictures, previews and tiles are made in memory and never written out. Deleting files from a disk doesn't guarantee they can't be recovered."}),
          (() => {
            const b = el("button", {class: "btn btn-sm", text: "Remove MonKap's own files…", style: "margin-top:6px"});
            let armed = false;
            b.addEventListener("click", async () => {
              if (!armed) { armed = true; b.textContent = "Click again: settings and the list of opened folders are deleted (datasets are not touched)"; return; }
              const x = await api("/api/data/forget", {});
              forgetNote.textContent = `Removed ${x.removed} file(s). The page keeps working with the defaults.`;
            });
            return b;
          })());
      } catch (e) { forgetNote.textContent = e.message; }
    }
    const {veil, done} = dialog(content);
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } };
    const close = () => { document.removeEventListener("keydown", onKey, true); done(); };
    document.addEventListener("keydown", onKey, true);
    cancel.addEventListener("click", close);
    veil.addEventListener("mousedown", (e) => { if (e.target === veil) close(); });
    save.addEventListener("click", async () => {
      const next = {template: tpl.value};
      for (const r of veil.querySelectorAll("input[type=radio]:checked")) next[r.name] = r.value;
      for (const k of veil.querySelectorAll("input[type=checkbox]")) next[k.dataset.k] = k.checked;
      for (const i of veil.querySelectorAll("[data-t]")) next[i.dataset.k] = i.dataset.t === "number" ? Number(i.value) : i.value;
      try {
        const r = await api("/api/settings", {settings: next});
        M.settings = r.settings; M.tasks = r.tasks;
        redraw();
        syncButtons();
        close();
        status(r.note || "Settings saved.", r.note ? "warn" : "ok");
      } catch (e) { err.textContent = e.message; }
    });
    loadModels().then(() => { if (section === "ai") { model.scrollIntoView({block: "center"}); model.focus(); } });
    if (section !== "ai") tpl.focus();
  }

  // ---- keys -----------------------------------------------------------------------------------------------------
  document.addEventListener("keydown", (e) => {
    if (!active() || M.modal) return;
    const typing = e.target instanceof Element && e.target.closest("textarea, input:not([type=range]), select, [contenteditable]");
    const mod = e.ctrlKey || e.altKey || e.metaKey;
    if (e.code === "KeyC" && !mod && !e.shiftKey && !typing) {   // by the physical key: any layout, any NumLock
      e.preventDefault();
      if (!e.repeat) kapture();                                  // a held key is ONE crop
    } else if (e.key === "Escape") {
      if (pop) { closePop(); return; }
      if (M.frame || typing) { e.preventDefault(); clearFrame(); text.focus({preventScroll: true}); }
    } else if (e.key === "PageUp" || e.key === "PageDown") {
      e.preventDefault();
      if (!e.repeat) go(e.key === "PageUp" ? -1 : 1);
    } else if (e.key === "Delete" && e.shiftKey && !mod) {
      const sel = e.target === text && text.selectionStart !== text.selectionEnd;
      if (sel) return;                                           // Shift+Delete is Cut when text is selected
      e.preventDefault();
      if (!e.repeat) requestDelete();                            // a held key is ONE picture
    } else if (e.key.startsWith("Arrow") && !typing && M.frame && e.target === stage) {
      e.preventDefault();
      nudge(e.key, e.shiftKey, e.ctrlKey);
    }
  }, true);

  // ---- wiring ---------------------------------------------------------------------------------------------------
  $q("mkPrev").addEventListener("click", () => go(-1));
  $q("mkNext").addEventListener("click", () => go(1));
  $q("mkKapture").addEventListener("click", kapture);
  $q("mkDelete").addEventListener("click", requestDelete);
  $q("mkFit").addEventListener("click", fitView);
  $q("mkActual").addEventListener("click", actualSize);
  $q("mkZoomIn").addEventListener("click", () => zoomBy(1.25));
  $q("mkZoomOut").addEventListener("click", () => zoomBy(0.8));
  $q("mkTrigger").addEventListener("click", addTrigger);
  $q("mkSettings").addEventListener("click", openSettings);
  $q("mkHelp").addEventListener("click", openHelp);
  $q("mkChange").addEventListener("click", (e) => { e.preventDefault(); openFolderDialog(); });
  $q("mkInfo").addEventListener("click", (e) => {
    const c = M.cur;
    if (!c) return;
    popover(e.currentTarget, [c.name, `${c.iw} × ${c.ih} px`, `Picture ${M.index + 1} of ${M.items.length}`,
      M.items[M.index] && M.items[M.index][1] ? "Has a caption" : "Empty caption", M.folder]);
  });
  $q("mkDrawer").addEventListener("click", () => {
    const open = root.classList.toggle("strip-open");
    $q("mkDrawer").setAttribute("aria-expanded", String(open));
    $q("mkDrawer").textContent = open ? "Pictures ▴" : "Pictures ▾";
    if (open) { drawStrip(true); scrollStripTo(M.index); }
  });
  $q("mkRegen").addEventListener("click", regenerate);
  strip.addEventListener("scroll", drawStripSoon);
  strip.addEventListener("wheel", (e) => {                        // in a row the wheel scrolls sideways
    if (horizontal()) { e.preventDefault(); strip.scrollLeft += e.deltaY + e.deltaX; }
  }, {passive: false});
  mq.addEventListener("change", () => { drawStrip(true); scrollStripTo(M.index); resize(); });

  function openHelp() {
    if (M.modal) return;
    const key = (...k) => el("span", {}, k.map((t) => el("kbd", {text: t})));
    const txt = (t) => el("span", {text: t});
    const rows = [
      [txt("Left button drag"), "select an area (a frame)"],
      [txt("Drag a handle or inside"), "resize or move the frame; the arrow keys move it by a pixel (Shift: ten), with Ctrl they resize it"],
      [txt("Middle button drag"), "move the picture"],
      [txt("Wheel"), "zoom at the cursor"],
      [key("C"), "Kapture: save the frame as a new picture"],
      [key("Esc"), "drop the frame and go back to typing"],
      [key("PgUp"), "previous picture"],
      [key("PgDn"), "next picture"],
      [key("⇧", "Del"), "delete this picture (the caption box keeps Cut when text is selected)"]];
    const list = el("dl", {class: "mk-keys"});
    for (const [k, what] of rows) list.append(el("dt", {}, k), el("dd", {text: what}));
    const close = el("button", {class: "btn btn-primary", text: "Close"});
    const {veil, done} = dialog([
      el("h2", {text: "MonKap"}),
      el("p", {class: "lead", text: "Go through the pictures of a folder one by one: caption them, and frame a part of a picture to save it as a new picture (Kapture), without leaving the page."}),
      el("p", {class: "lead", text: "The crop is cut from the original file at its own pixels and saved next to it as a new PNG; nothing is resized and the original is never touched. The new picture's caption starts as chosen in the settings (the gear). A caption is saved by itself when you move on."}),
      list, el("div", {class: "buttons"}, close)]);
    const onKey = (e) => { if (e.key === "Escape" || e.key === "Enter") { e.preventDefault(); e.stopPropagation(); shut(); } };
    const shut = () => { document.removeEventListener("keydown", onKey, true); done(); };
    document.addEventListener("keydown", onKey, true);
    close.addEventListener("click", shut);
    veil.addEventListener("mousedown", (e) => { if (e.target === veil) shut(); });
    close.focus();
  }

  // coming back to the page: files and captions may have changed meanwhile (another program), so the list and the open
  // picture's caption are read again - a caption being typed is left alone
  async function refreshOnShow() {
    if (!M.folder) return;
    await refreshList();
    triggerState();
    const c = M.cur;
    if (c && M.index < 0) { await loadFolder(null); return; }          // the open picture went: start again
    if (!c || M.dirty) return;
    const info = await api(url("info", {name: c.name, ext: capExtNow()}));
    if (info.caption !== c.loaded) {
      c.loaded = info.caption; text.value = info.caption || ""; M.dirty = false;
      $q("mkBadge").style.display = info.inherited ? "" : "none";
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) flush().catch(() => {});
    else { resize(); refreshOnShow().catch(() => {}); }
  });

  // closing the page with a caption not yet saved: send it on the way out
  window.addEventListener("pagehide", () => {
    const c = M.cur;
    if (!c || !M.dirty) return;
    fetch("/api/caption", {method: "POST", keepalive: true,
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({folder: M.folder, name: c.name, text: text.value, loaded: c.loaded, ext: capExtNow()})}).catch(() => {});
  });

  setRatio("1:1", false);
  window.MonKap = {state: M, open, go, kapture, flush};

  // start: the folder open last time, or ask for one
  (async () => {
    try { await loadSettings(); } catch (e) { message(e.message); return; }
    S.imageFolder = M.settings.last_folder || "";
    if (S.imageFolder) await loadFolder(null);
    else {
      message("Open a folder of pictures (the link above).");
      // in MonKap's own window the native folder picker is ready a moment after the page: give it that moment
      await Promise.race([new Promise((r) => window.addEventListener("pywebviewready", r, {once: true})), new Promise((r) => setTimeout(r, 700))]);
      openFolderDialog();
    }
  })();
})();
