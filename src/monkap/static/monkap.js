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
    modal: null, lastDelete: null, review: null, raw: [], mech: null,
  };
  let regen = false;                     // Regenerate (AI) is running
  let enh = null;                        // an Enhance run of the main view is open: {c}
  let labBusy = false;                   // tasks of the Enhance lab are going (the server says so in its live answer)
  const LAB_WAIT = "Tasks of the Enhance lab are running: stop them first";
  const LEAVE_LAB = "Please stop any running enhance tasks before returning to main view";
  const railMain = document.getElementById("mkRailMain"), railLab = document.getElementById("mkRailLab");
  let lab = null;                        // the Enhance lab (monkap_lab.js), made once the page's helpers exist
  // selection mode: sel = the picked names, canvas = one picture opened from the gallery, run = captions being written
  const MS = {on: false, canvas: false, sel: new Set(), anchor: -1, tiles: new Map(), status: new Map(), errors: new Map(),
    run: null, undo: null, note: "", lastMany: null, focusing: null,
    folder: "", memory: new Map()};                              // memory: folder -> the names picked there, kept while the page is open
  const $q = (id) => document.getElementById(id);
  const root = $q("mk"), stage = $q("mkStage"), cv = $q("mkCanvas"), ctx = cv.getContext("2d");
  const text = $q("mkText"), strip = $q("mkStrip"), stripIn = $q("mkStripIn");
  let cw = 0, ch = 0, dpr = 1, drag = null, drawPending = false;

  // ---- small helpers -------------------------------------------------------------------------------------------
  const qs = (o) => Object.entries(o).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  const url = (route, o = {}) => `/api/${route}?${qs({folder: M.folder, ...o})}`;
  const capExtNow = () => M.ext || ".txt";
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

  // ---- what a training run found out (Fizgig's per-image loss watch) ---------------------------------------------------
  const verdictOf = (name) => (M.review && M.review.available && M.review.verdicts[name]) || null;

  async function loadReview() {
    M.review = null;
    if (M.folder && M.settings && M.settings.fizgig_dir) {
      try { M.review = await api(url("review")); } catch (e) { M.review = null; }
    }
    renderNote();
    renderFilter();
    $q("mkReview").style.display = M.folder ? "" : "none";
  }

  // Fizgig is set up but has no run on this folder: a yellow (!) that says why when it is clicked; and the odd cases of a run
  // that was found after all (Fizgig moved, the pictures moved)
  function renderNote() {
    const rv = M.review, on = !!(rv && rv.available), fz = !!(M.folder && M.settings && M.settings.fizgig_dir);
    $q("mkNoData").style.display = fz && rv && !on ? "" : "none";
    const note = $q("mkReviewNote");
    const text = !on ? "" : rv.run.relocated ? "Verdicts read from this Fizgig's output folder: the run's recorded folder is gone (Fizgig was moved?)."
      : rv.run.loose ? "Verdicts from a run on a folder with this name, but at another path (the pictures were moved?)." : "";
    note.textContent = text;
    note.style.display = text ? "" : "none";
  }

  // when the newest training run on this folder started (the trainer read the captions then), or null
  const runStart = () => (M.review && M.review.available && typeof M.review.run.started_at === "number" ? M.review.run.started_at : null);

  // the "Show" list: one choice at a time, in groups; a choice that needs a training run is greyed out without one
  function renderFilter() {
    const sel = $q("mkShow"), keep = sel.value, rows = M.raw || [];
    const rv = M.review && M.review.available ? M.review : null, t0 = runStart();
    const n = (f) => rows.filter(f).length;
    const opt = (v, t, off, tip) => el("option", {value: v, text: t, disabled: !!off, title: tip || ""});
    const group = (label, opts) => el("optgroup", {label}, opts.map((o) => opt(...o)));
    const kids = [opt("all", `All pictures (${rows.length})`)];
    if (rv) {
      const list = [["problems", `Problems (${PROBLEMS.reduce((a, k) => a + (rv.counts[k] || 0), 0)})`]];
      for (const l of rv.legend) if (rv.counts[l.key]) list.push([l.key, `${l.label} (${rv.counts[l.key]})`]);
      kids.push(group("Training verdicts", list));
    }
    kids.push(group("Caption", [["cap:none", `No caption (${n((r) => r[2] === "none")})`],
      ["cap:manual", `Written by hand (${n((r) => r[2] === "manual")})`], ["cap:ai", `Written by AI (${n((r) => r[2] === "ai")})`]]));
    const need = "Needs a training run on this folder (set the Fizgig folder in the settings)";
    kids.push(group("Since the last training run", [
      ["run:changed", `Caption changed (${t0 === null ? 0 : n((r) => r[1] && r[3] > t0)})`, t0 === null, need],
      ["run:same", `Caption not changed (${t0 === null ? 0 : n((r) => r[1] && r[3] <= t0)})`, t0 === null, need]]));
    kids.push(group("Picture", [["img:enh", `Enhanced by AI (${n((r) => r[4])})`]]));
    sel.replaceChildren(...kids);
    const ok = [...sel.options].some((o) => o.value === keep && !o.disabled);
    sel.value = ok ? keep : "all";
    for (const o of $q("mkSort").options) if (o.value === "hard" || o.value === "verdict") { o.disabled = !rv; o.title = rv ? "" : "Needs a training run on this folder"; }
    if ($q("mkSort").selectedOptions[0] && $q("mkSort").selectedOptions[0].disabled) $q("mkSort").value = "name";
  }

  const ORDER = ["excluded", "stuck", "suspect", "watch", "warmup", "exhausted", "learning", "mid", "easy"];
  const PROBLEMS = ["excluded", "stuck", "suspect", "watch"];

  // the predicate of the "Show" choice (null: everything)
  function showFilter() {
    const v = $q("mkShow").value, rv = M.review && M.review.available ? M.review : null, t0 = runStart();
    if (v === "all") return null;
    if (v === "problems") return (r) => !!(rv && rv.verdicts[r[0]] && PROBLEMS.includes(rv.verdicts[r[0]].verdict));
    if (v.startsWith("cap:")) return (r) => r[2] === v.slice(4);
    if (v === "run:changed") return (r) => t0 !== null && !!r[1] && r[3] > t0;
    if (v === "run:same") return (r) => t0 !== null && !!r[1] && r[3] <= t0;
    if (v === "img:enh") return (r) => !!r[4];
    return (r) => !!(rv && rv.verdicts[r[0]] && rv.verdicts[r[0]].verdict === v);
  }

  // the list as "Show" and "Sort" ask for it (a picture with no verdict, or no caption, sorts last)
  function arrange(items) {
    const rv = M.review && M.review.available ? M.review.verdicts : null;
    const pred = showFilter(), sort = $q("mkSort").value;
    let out = pred ? items.filter(pred) : items.slice();
    if (sort === "date") out.sort((a, b) => (b[3] || 0) - (a[3] || 0));
    else if (rv && sort === "hard") out.sort((a, b) => (rv[b[0]] ? rv[b[0]].recent : -9) - (rv[a[0]] ? rv[a[0]].recent : -9));
    else if (rv && sort === "verdict") {
      const rank = (it) => (rv[it[0]] ? ORDER.indexOf(rv[it[0]].verdict) : 99);
      const hard = (it) => (rv[it[0]] ? rv[it[0]].recent : -9);
      out.sort((a, b) => rank(a) - rank(b) || hard(b) - hard(a));
    }
    return out;
  }

  // ---- the icons on a picture: who wrote its caption, and whether the picture was enhanced by AI ---------------------------
  const IC_DOC = '<svg viewBox="0 0 24 24"><path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4"/><path d="M9.5 12h6M9.5 15h6M9.5 18h4"/></svg>';
  const IC_SPARK = '<svg viewBox="0 0 24 24"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/></svg>';
  const CAP_TIP = {none: "No caption: this picture is left out of training", manual: "Caption written or edited by hand",
    ai: "Caption written by the model (AI), not touched since"};
  const IMG_TIP = "Picture enhanced by AI";
  function icon(kind, klass) {
    const span = el("span", {class: `ic ${kind} ${klass}`, title: kind === "cap" ? CAP_TIP[klass] : IMG_TIP});
    span.innerHTML = kind === "cap" ? IC_DOC : IC_SPARK;
    return span;
  }
  // row: [name, has a caption, who wrote it, the caption's change time, enhanced]
  function pills(row) {
    const box = el("span", {class: "pills"}, icon("cap", row[2] || (row[1] ? "manual" : "none")));
    if (row[4]) box.append(icon("img", "enh"));
    return box;
  }
  const when = (sec) => (sec ? new Date(sec * 1000).toLocaleString() : "");
  function describe(row) {                                       // the lines of the little "i" of a picture
    const lines = [row[0], CAP_TIP[row[2] || (row[1] ? "manual" : "none")]];
    if (row[1] && row[3]) lines.push("Caption changed: " + when(row[3]));
    if (row[4]) lines.push(IMG_TIP);
    return lines;
  }

  function verdictChip(name) {
    const v = verdictOf(name);
    if (!v) return null;
    return el("span", {class: "vd", text: v.label, style: `background:${v.color}`, title: `${v.label}: ${v.means}`});
  }

  function showVerdict(name) {
    const box = $q("mkVerdict"), v = name ? verdictOf(name) : null;
    box.style.display = v ? "" : "none";
    if (!v) return;
    box.replaceChildren(
      el("span", {class: "vd", text: v.label, style: `background:${v.color}`}),
      el("span", {class: "mean", text: ` ${v.means} `}),
      el("span", {class: "todo", text: v.todo}),
      el("button", {type: "button", class: "mk-more", text: "All verdicts…", onclick: openReviewHelp}));
  }

  function openReviewHelp() {
    if (M.modal) return;
    const rv = M.review, on = !!(rv && rv.available), run = on ? rv.run : null;
    const noData = !!(rv && !on && M.settings && M.settings.fizgig_dir);
    const iconRow = (klass, kind, what) => el("div", {class: "vd-row"}, icon(kind, klass), el("div", {text: what}));
    const icons = [iconRow("none", "cap", "No caption: the picture is left out of training."),
      iconRow("manual", "cap", "The caption was written or edited by hand (a caption MonKap has no note of counts as written by hand)."),
      iconRow("ai", "cap", "The caption was written by the model and has not been touched since."),
      iconRow("enh", "img", "The picture was enhanced by AI (the AI enhance button).")];
    const rows = (rv ? rv.legend : []).map((l) => el("div", {class: "vd-row"},
      el("span", {class: "vd", text: l.label, style: `background:${l.color}`}),
      el("div", {}, el("div", {text: l.means}), el("div", {class: "field-hint", text: "What to do: " + l.todo}))));
    const stamp = run && run.started_at ? new Date(run.started_at * 1000).toLocaleString() : "";
    const close = el("button", {class: "btn btn-primary", text: "Close"});
    const {veil, done} = dialog([
      el("h2", {text: noData ? "No training data" : run ? "What the training run found out" : "What the icons mean"}),
      noData ? el("p", {class: "lead", text: rv.reason}) : null,
      noData ? el("p", {class: "lead", text: "Without a training run on this folder there are no verdicts, and the filters on whether a caption changed since the last run are greyed out."}) : null,
      el("h3", {text: "The icons on the pictures", style: "margin:10px 0 4px"}), ...icons,
      run ? el("h3", {text: "Training verdicts", style: "margin:14px 0 4px"}) : null,
      run ? el("p", {class: "lead", text: `The run "${run.lora}" (${run.family}, ${run.epochs || "?"} epochs${stamp ? ", " + stamp : ""}). ` +
        "Each picture's verdict is Fizgig's loss watch's, from how hard the picture was for the model compared with the others at the same noise level. " +
        "A picture whose caption was changed after the run started has been dealt with: it shows no verdict any more" +
        (rv.handled ? ` (${rv.handled} so far).` : ".")}) : null,
      ...rows,
      rv && rv.advice && rv.advice.length ? el("h3", {text: "What to try", style: "margin:14px 0 4px"}) : null,
      ...(rv && rv.advice ? rv.advice.map((t) => el("p", {class: "lead", text: "• " + t})) : []),
      rv ? el("div", {class: "field-hint", text: "The verdicts read Fizgig's own files and depend on its version; the setting names are as in Fizgig 8.5."}) : null,
      el("div", {class: "buttons"}, close)]);
    const onKey = (e) => { if (e.key === "Escape" || e.key === "Enter") { e.preventDefault(); e.stopPropagation(); shut(); } };
    const shut = () => { document.removeEventListener("keydown", onKey, true); done(); };
    document.addEventListener("keydown", onKey, true);
    close.addEventListener("click", shut);
    veil.addEventListener("mousedown", (e) => { if (e.target === veil) shut(); });
    close.focus();
  }

  // Show / Sort changed: the list again in the new order, the open picture kept when it is still there
  async function rearrange() {
    const cur = M.cur ? M.cur.name : null;
    M.items = arrange(M.raw);
    M.index = cur ? M.items.findIndex((it) => it[0] === cur) : -1;
    drawStrip(true);
    scrollStripTo(M.index);
    syncButtons();
    if (M.index < 0 && M.items.length) await open(0);
    else if (!M.items.length) { closePicture(); message("No pictures with that verdict. Change \"Show\" above the list."); }
  }

  // ---- the folder and its filmstrip ------------------------------------------------------------------------
  async function loadSettings() {
    const r = await api("/api/state");
    M.settings = r.settings;
    M.tasks = r.tasks;
    M.ext = r.caption_ext;
    setRatio(M.settings.ratio, false);
  }

  async function fetchList() {
    const r = await api(url("list"));
    M.folder = r.folder;
    M.trigger = r.trigger || "";
    M.ext = r.ext || M.ext;
    M.raw = r.items;
    renderFilter();                                              // the counts of the "Show" list come from these rows
    if (lab) lab.refreshRows();
    return arrange(r.items);
  }

  async function loadFolder(select) {
    M.folder = (S.imageFolder || "").trim();
    M.srcFolder = M.folder;
    $q("mkChange").title = M.folder ? `Folder: ${M.folder}. Click to open another.` : "Open a folder of pictures.";
    if (MS.on) exitSelection(true);
    closePicture();
    if (!M.folder) { message("Open a folder of pictures (the link above)."); M.items = []; drawStrip(true); return; }
    try {
      if (!M.settings) await loadSettings();
      await loadReview();
      M.items = await fetchList();
    } catch (e) { M.items = []; message(e.message); drawStrip(true); return; }
    drawStrip(true);
    $q("mkChange").textContent = folderLabel();
    if (!M.items.length) { message(M.raw.length ? "No pictures with that verdict. Change \"Show\" above the list." : "No pictures in this folder (png, jpg, webp, bmp, avif)."); return; }
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
      const row = M.items[i], name = row[0];
      const info = el("button", {class: "mk-i", type: "button", text: "i", title: "About this picture", onclick: (e) => {
        e.stopPropagation();
        popover(e.currentTarget, describe(M.items[i]));
      }});
      const node = el("div", {class: "mk-th" + (i === M.index ? " cur" : "") + (MS.on && MS.sel.has(name) ? " sel" : ""),
        onclick: (e) => (MS.on ? pickAt(i, e) : go(i - M.index))},
        el("img", {src: url("thumb", {name}), alt: "", draggable: "false"}),
        pills(row), verdictChip(name), info);
      node.style[h ? "left" : "top"] = (i * p + 4) + "px";
      stripIn.append(node);
      M.thumbs.set(i, node);
    }
    if (reset && MS.on) multiChanged();                          // the list changed: the picked set and the gallery follow
  }
  const drawStripSoon = () => requestAnimationFrame(() => drawStrip());
  function markCurrent() {
    for (const [i, n] of M.thumbs) n.classList.toggle("cur", i === M.index);
    if (MS.on) markGalleryCur();
  }
  function scrollStripTo(i) {
    if (i < 0) return;
    const h = horizontal(), p = pitch(), size = h ? strip.clientWidth : strip.clientHeight;
    const to = clamp(i * p - (size - p) / 2, 0, Math.max(0, M.items.length * p - size));
    if (h) strip.scrollLeft = to; else strip.scrollTop = to;
    drawStrip();
    markCurrent();
  }
  // a caption was written, cleared or restored (by the user, the model or a button): the row, its icons, the verdict
  // st = {has, klass, mtime}; `changed` false when nothing was written
  function setCap(name, st, changed = true) {
    const row = (M.raw || []).find((r) => r[0] === name);
    if (!row) return;
    row[1] = st.has ? 1 : 0;
    row[2] = st.klass || (st.has ? "manual" : "none");
    if (typeof st.mtime === "number") row[3] = st.mtime;
    const nodes = [M.thumbs.get(M.items.indexOf(row)), MS.tiles.get(name)];
    for (const n of nodes) if (n) { const old = n.querySelector(".pills"); if (old) old.replaceWith(pills(row)); }
    if (changed) dropVerdict(name);
    renderFilter();
  }

  // the caption changed after the training run read it: the picture is dealt with, and loses its verdict
  function dropVerdict(name) {
    const rv = M.review;
    if (!rv || !rv.available || !rv.verdicts[name]) return;
    const kind = rv.verdicts[name].verdict;
    delete rv.verdicts[name];
    rv.counts[kind] = Math.max(0, (rv.counts[kind] || 0) - 1);
    rv.handled = (rv.handled || 0) + 1;
    const i = M.items.findIndex((r) => r[0] === name);
    for (const n of [M.thumbs.get(i), MS.tiles.get(name)]) if (n) for (const chip of n.querySelectorAll(".vd")) chip.remove();
    if (M.cur && M.cur.name === name) showVerdict(name);
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
    if (MS.on) markGalleryCur();
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
    if (enh) { status(ENH_WAIT, "warn"); return; }
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
    if (live) live.name = "";                                     // the next answer is the new picture's baseline
    showVerdict(name);
    resize();                                     // also fits the view and draws
    updateMode(); syncButtons();
    focusHome();
    for (const j of [i + 1, i - 1]) if (M.items[j]) getBitmap(M.items[j][0]).catch(() => {});   // the neighbours, ready
  }

  const go = (d) => (MS.on ? goSelected(d) : open(M.index + d));

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
    const zoomTitles = {mkZoomIn: "Zoom in", mkZoomOut: "Zoom out", mkActual: "Zoom. Click for one picture pixel per screen pixel", mkFit: "Fit the whole picture"};
    for (const id of Object.keys(zoomTitles)) { $q(id).disabled = !c; $q(id).title = c ? zoomTitles[id] : "Open a picture first"; }
    $q("mkActual").textContent = c ? Math.round(M.view.scale * 100) + "%" : "100%";
  }

  // which buttons make sense now: Kapture needs a frame on the picture; in the gallery of a selection they act on all the picked
  const REGEN_TITLE = $q("mkRegen").title, DELETE_TITLE = $q("mkDelete").title;
  const ENHANCE_TITLE = "Make this picture sharper and cleaner with ComfyUI (set up in the settings, Enhance tab)";
  const RUN_WAIT = "Wait until the captions are written, or stop them";
  const NO_PIC = "Open a picture first";
  // a greyed button always says why: `why` is the reason (empty: the button works), `title` what it says when it works
  function setBtn(id, why, title) {
    const b = $q(id);
    b.disabled = !!why;
    b.title = why || title;
  }
  function syncButtons() {
    const c = M.cur, gal = inGallery(), run = !!MS.run;
    const picked = MS.on ? selectedNames() : [], n = picked.length;
    const at = MS.on && c ? picked.indexOf(c.name) : -1;
    const busy = M.busy ? "Wait a moment: MonKap is working on the folder" : "";
    setBtn("mkKapture", !c ? NO_PIC : gal ? "Open one picture (double click) to take a frame" : run ? RUN_WAIT : busy ||
      (!M.frame ? "Drag a frame on the picture first" : ""), "Save the frame as a new picture (C)");
    setBtn("mkPrev", !c ? NO_PIC : (MS.on ? at <= 0 : M.index <= 0) ? "This is the first picture" : "", "Previous picture (PgUp)");
    setBtn("mkNext", !c ? NO_PIC : (MS.on ? at < 0 || at >= n - 1 : M.index >= M.items.length - 1) ? "This is the last picture" : "", "Next picture (PgDn)");
    setBtn("mkDelete", run ? RUN_WAIT : gal ? (!n ? "Pick some pictures first" : busy) : !c ? NO_PIC : "",
      gal ? `Delete the ${plural(n, "picked picture")} (asks every time)` : DELETE_TITLE);
    const regenWhy = run ? RUN_WAIT : regen ? "A caption is being written" : gal ? (!n ? "Pick some pictures first" : busy) : !c ? NO_PIC : "";
    setBtn("mkRegen", regenWhy, gal ? `Write the captions of the ${plural(n, "picked picture")} again, one after another: the old texts are replaced (kept, and Undo brings it back)` : REGEN_TITLE);
    setBtn("mkHintBtn", regenWhy, "ReCaption with a hint for the model");
    const enhWhy = !c ? NO_PIC : gal ? "One picture at a time: open it (double click), then enhance it" : regen ? "A caption is being written" :
      enh ? "An enhance run is going" : run ? RUN_WAIT : labBusy ? LAB_WAIT : "";
    setBtn("mkEnhance", enhWhy, ENHANCE_TITLE);
    setBtn("mkEnhHintBtn", enhWhy, "Enhance with your own words for this picture");
    $q("mkRegen").querySelector(".lbl").textContent = gal ? `AI ReCaption (${n})` : "AI ReCaption";
    $q("mkDelete").querySelector(".lbl").textContent = gal ? `Delete selected (${n})` : "Delete…";
    if (!enh) { $q("mkShow").disabled = $q("mkSort").disabled = run;       // the picked set holds still while captions are written
      $q("mkShow").title = $q("mkSort").title = run ? RUN_WAIT : ""; }
    $q("mkInfo").style.display = c ? "" : "none";
    root.classList.toggle("has-frame", !!(c && M.frame));
    lockText();
    triggerState();
    syncRail();
  }

  // the trigger word button: shown when the folder has one, greyed out when the caption already has it
  function triggerState() {
    const b = $q("mkTrigger");
    if (inGallery()) {                                           // in the gallery it works on every picked caption
      b.style.display = M.trigger && selectedNames().length ? "" : "none";
      b.disabled = !!MS.run || M.busy;
      b.title = `Put the trigger word first in every picked caption: ${M.trigger}`;
      return;
    }
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
    M.mech = next;                                               // a change by a button: the model's caption stays the model's
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
    const mech = M.mech !== null && sent.trim() === M.mech;      // the text the trigger word button made, not typed on after it
    const r = await api("/api/caption", {folder: M.folder, name: c.name, text: sent, loaded: c.loaded, ext: capExtNow(), mechanical: mech});
    c.loaded = sent.trim() || null;
    if (text.value === sent) { M.dirty = false; M.mech = null; }  // typed on meanwhile: the next save takes it
    setCap(c.name, {has: r.has_caption, klass: r.class, mtime: r.mtime}, r.kind === "written" || r.kind === "cleared");
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
  async function regenerate(hint = "") {
    const c = M.cur;
    if (!c || regen || enh || MS.run || inGallery()) return;
    if (!M.settings.lm_model) { openSettings("ai"); return; }
    hint = typeof hint === "string" ? hint.trim() : "";
    regen = true;
    syncButtons();
    const prev = text.value;
    try {
      await flush();                                             // what is typed is on disk first, so nothing is lost
      status("Writing the caption…");
      const r = await api("/api/recaption", {folder: M.folder, name: c.name, hint});
      setCap(c.name, {has: true, klass: r.class, mtime: r.mtime});
      if (M.cur !== c) { status(`${c.name}: new caption written.`, "ok"); return; }     // the user moved on meanwhile
      text.value = r.caption; c.loaded = r.caption; M.dirty = false;
      triggerState();
      status("New caption written.", "ok", prev.trim() && prev.trim() !== r.caption ? {label: "Restore previous caption", run: async () => {
        try {
          const back = await api("/api/caption", {folder: M.folder, name: c.name, text: prev, loaded: r.caption, ext: capExtNow(),
            restore: r.note_before || null});
          if (M.cur === c) { text.value = prev; c.loaded = prev.trim() || null; M.dirty = false; $q("mkBadge").style.display = "none"; triggerState(); }
          setCap(c.name, {has: back.has_caption, klass: back.class, mtime: back.mtime});
          status("The previous caption is back.", "ok");
        } catch (e) { status(e.message, "err"); }
      }} : null);
    } catch (e) { if (M.cur === c) status(`Could not ReCaption: ${e.message}`, "err"); }
    finally { regen = false; syncButtons(); }
  }

  // ---- a hint for the model (Regenerate) or your own words for the enhance --------------------------------------------
  const hints = new Map();                                       // "r:picture" / "e:picture" -> the last hint typed (this page only)
  let hintPanel = null, hintAnchor = null;

  function closeHint() {
    if (!hintPanel) return;
    hintPanel.remove(); hintPanel = null; hintAnchor = null;
    document.removeEventListener("keydown", hintKey, true);
    document.removeEventListener("mousedown", hintOutside, true);
  }
  const hintKey = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeHint(); focusHome(); } };
  const hintOutside = (e) => { if (hintPanel && !hintPanel.contains(e.target) && !(hintAnchor && hintAnchor.contains(e.target))) closeHint(); };

  function openHint(kind = "regen") {
    const c = M.cur, enhancing = kind === "enhance", gal = inGallery() && !enhancing;
    if (enhancing ? !c || inGallery() : gal ? !selectedNames().length : !c) return;
    if (regen || enh || M.modal || MS.run) return;
    const anchor = $q(enhancing ? "mkEnhHintBtn" : "mkHintBtn");
    if (hintPanel) { const same = hintAnchor === anchor; closeHint(); if (same) return; }
    if (!enhancing && !M.settings.lm_model) { openSettings("ai"); return; }
    const key = (enhancing ? "e:" : "r:") + (gal ? "*" : c.name);
    const box = el("textarea", {rows: "3", spellcheck: "false", placeholder: enhancing
      ? "e.g. only sharpen the face, keep the background soft"
      : "e.g. a close-up from the rear, the intake manifold is off"});
    box.value = hints.get(key) || "";
    const go = el("button", {type: "button", class: "btn btn-primary btn-sm", text: enhancing ? "Enhance with these words" : gal ? "ReCaption them with hint" : "ReCaption with hint"});
    // unload: an unloaded model is not an error, so the button is always there (when the feature is set up)
    const said = el("span", {class: "field-hint"});
    const unload = el("button", {type: "button", class: "btn btn-sm", title: enhancing
      ? "Ask ComfyUI to unload its models and free the video memory (for a trainer)" : "Unload the caption model from LM Studio (the other models there are left alone)",
      text: enhancing ? "Unload the models from ComfyUI" : "Unload the model from LM Studio"});
    unload.addEventListener("click", async () => {
      unload.disabled = true; said.textContent = "Asking…";
      try {
        if (enhancing) { await api("/api/enhance/free", {}); said.textContent = "ComfyUI was asked to free its models."; }
        else { const r = await api("/api/ai/unload", {}); said.textContent = r.unloaded ? "Unloaded." : "It was not loaded."; }
      } catch (e) { said.textContent = e.message; }
      finally { unload.disabled = false; }
    });
    hintPanel = el("div", {class: "mk-hint", role: "dialog", "aria-label": enhancing ? "Your words for the enhance" : "A hint for the model"},
      el("button", {type: "button", class: "x", text: "×", title: "Close", onclick: () => { closeHint(); focusHome(); }}),
      el("h4", {text: enhancing ? "Your words for the enhance" : gal ? `A hint for the ${plural(selectedNames().length, "picked picture")}` : "A hint for the model"}), box,
      el("div", {class: "bar"}, el("span", {class: "field-hint", text: (enhancing ? "Added to the template's prompt." : "Sent with the task.") + " Ctrl+Enter to go."}), go),
      el("div", {class: "bar unload"}, unload, said));
    hintAnchor = anchor;
    document.body.append(hintPanel);
    const r = anchor.getBoundingClientRect(), w = hintPanel.offsetWidth, h = hintPanel.offsetHeight;
    hintPanel.style.left = clamp(r.right - w, 8, innerWidth - w - 8) + "px";          // right edge on the chevron, above it
    hintPanel.style.top = Math.max(8, r.top - h - 8) + "px";
    const send = () => {
      const t = box.value.trim();
      if (t) hints.set(key, t); else hints.delete(key);
      closeHint();
      if (enhancing) enhance(t); else if (gal) regenerateMany(t); else regenerate(t);
    };
    go.addEventListener("click", send);
    box.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); } });
    document.addEventListener("keydown", hintKey, true);
    document.addEventListener("mousedown", hintOutside, true);
    box.focus();
  }

  // ---- Enhance (AI): ComfyUI makes the picture sharper; the result waits for a decision ---------------------------------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  function dropBitmap(name) {                                    // a picture that changed on disk must be fetched again
    const k = `${M.folder}\n${name}`, b = M.bitmaps.get(k);
    if (b && !(M.cur && b === M.cur.bmp)) b.close();
    M.bitmaps.delete(k);
  }
  async function showAgain(name) {                               // the list, and the picture of that name open again
    dropBitmap(name);
    if (MS.on) MS.sel.add(name);                                 // a replaced picture (maybe under another extension) stays picked
    await refreshList();
    const i = M.items.findIndex((it) => it[0] === name);
    if (i >= 0) { M.cur = null; await open(i); }
  }

  async function enhance(hint = "") {
    const c = M.cur;
    if (!c || enh || regen || M.modal || M.busy || MS.run || inGallery()) return;
    try { await flush(); } catch (e) { status(e.message, "err"); return; }       // what is typed is on disk first
    try { await api("/api/enhance/start", {folder: M.folder, name: c.name, hint: typeof hint === "string" ? hint.trim() : ""}); }
    catch (e) { status(`Enhance: ${e.message}`, "err", {label: "Settings", run: () => openSettings("enhance")}); return; }
    watchEnhance(c);
  }

  // The run, in the main view and without windows: progress over the picture (its own blurred, darkened self, or ComfyUI's
  // latest preview once it has one), then the original and the result under one divider. Only the action row changes.
  const ENH_WAIT = "Finish or stop the enhance of this picture first";
  const enhEl = {ov: $q("mkEnhOv"), bg: $q("mkEnhBg"), what: $q("mkEnhWhat"), bar: $q("mkEnhBar"), line: $q("mkEnhLine"),
    diff: $q("mkDiff"), fit: $q("mkDiffFit"), o: $q("mkDiffO"), e: $q("mkDiffE"), cut: $q("mkDiffCut"), row: $q("mkEnhActions")};

  function enhMode(kind) {                                     // "run" | "result" | "failed" | null (leave the main view alone)
    root.classList.toggle("enhancing", !!kind);
    enhEl.ov.hidden = !(kind === "run" || kind === "failed");
    enhEl.ov.classList.toggle("failed", kind === "failed");
    enhEl.diff.hidden = kind !== "result";
    enhEl.row.hidden = !kind;
    if (!kind) enhEl.row.replaceChildren();
    for (const b of [$q("mkShow"), $q("mkSort")]) { b.disabled = !!kind || !!MS.run; b.title = kind ? ENH_WAIT : (MS.run ? RUN_WAIT : ""); }
    strip.title = kind ? ENH_WAIT : "";
    syncButtons();
    if (kind === "result") placeDiff();
  }

  // both pictures lie in the same rectangle, fitted into the stage: the original's proportions
  function placeDiff() {
    const c = M.cur;
    if (!c || enhEl.diff.hidden) return;
    const r = stage.getBoundingClientRect(), pad = 12;
    const k = Math.min((r.width - 2 * pad) / c.iw, (r.height - 2 * pad) / c.ih);
    enhEl.fit.style.width = Math.max(40, Math.floor(c.iw * k)) + "px";
    enhEl.fit.style.height = Math.max(40, Math.floor(c.ih * k)) + "px";
  }
  new ResizeObserver(placeDiff).observe(stage);

  function setCut(pct) {
    pct = Math.max(0, Math.min(100, pct));
    enhEl.fit.style.setProperty("--cut", pct + "%");
    enhEl.cut.setAttribute("aria-valuenow", String(Math.round(pct)));
  }
  (() => {
    const at = (e) => { const r = enhEl.fit.getBoundingClientRect(); setCut(100 * (e.clientX - r.left) / r.width); };
    enhEl.fit.addEventListener("pointerdown", (e) => { if (e.button !== 0) return; enhEl.fit.setPointerCapture(e.pointerId); at(e); e.preventDefault(); });
    enhEl.fit.addEventListener("pointermove", (e) => { if (enhEl.fit.hasPointerCapture(e.pointerId)) at(e); });
    enhEl.cut.addEventListener("keydown", (e) => {
      const now = Number(enhEl.cut.getAttribute("aria-valuenow") || 50), d = e.shiftKey ? 10 : 2;
      if (e.key === "ArrowLeft") { setCut(now - d); e.preventDefault(); } else if (e.key === "ArrowRight") { setCut(now + d); e.preventDefault(); }
    });
  })();

  function enhButtons(...defs) {                                // the one swapped action row: [label, class, title, run]
    const btns = [];
    for (const [label, cls, title, run] of defs) {
      const b = el("button", {type: "button", class: "btn " + cls, title, text: label});
      b.addEventListener("click", async () => {
        for (const x of btns) x.disabled = true;
        try { await run(b); }
        catch (e) { status(e.message, "err"); for (const x of btns) x.disabled = false; }
      });
      btns.push(b);
    }
    enhEl.row.replaceChildren(...btns);
    return btns;
  }

  function watchEnhance(c) {
    enh = {c};
    enhEl.bg.src = url("view", {name: c.name, edge: 800});
    enhEl.what.textContent = "Starting…";
    enhEl.bar.classList.add("wait"); enhEl.bar.firstChild.style.width = "";
    enhEl.line.replaceChildren();
    enhMode("run");
    const stop = enhButtons(["Stop", "btn-danger", "Stop the enhance of this picture", async (b) => {
      b.textContent = "Stopping…";
      try { await api("/api/enhance/cancel", {}); } catch (e) { /* the loop below ends it when the server says so */ }
    }])[0];
    stop.focus();
    const leave = (msg, kind) => { enh = null; enhMode(null); if (msg) status(msg, kind || ""); focusHome(); };
    (async () => {
      let seen = 0, lost = 0;
      for (;;) {
        let st;
        try { st = await api("/api/enhance/status"); lost = 0; }
        catch (e) { if (++lost > 20) { leave("MonKap lost touch with its own server.", "err"); return; } await sleep(500); continue; }
        if (st.status === "none") { leave(); return; }
        if (st.status === "cancelled") { leave("Enhance stopped.", "warn"); return; }
        if (st.status === "error") {
          enhEl.what.textContent = st.error || "Something went wrong.";
          enhEl.bar.hidden = true; enhEl.line.replaceChildren();
          enhMode("failed");
          enhButtons(["Settings", "", "Check the ComfyUI address and the template", () => { leave(); openSettings("enhance"); }],
            ["Close", "btn-primary", "Back to the picture", () => { enhEl.bar.hidden = false; leave(); }]);
          return;
        }
        if (st.status === "done") { enhEl.bar.hidden = false; showEnhanced(c, st, leave); return; }
        enhEl.bar.hidden = false;
        enhEl.what.textContent = st.stage + (st.steps ? `  (step ${st.step} of ${st.steps})` : "");
        enhEl.bar.classList.toggle("wait", st.percent === null);
        enhEl.bar.firstChild.style.width = st.percent === null ? "" : st.percent + "%";
        const parts = [el("span", {text: mmss(st.elapsed)})];
        if (st.percent !== null) parts.push(el("span", {text: st.percent + "%"}));
        if (!st.live) parts.push(el("span", {text: "no progress information from ComfyUI"}));
        if (st.idle > 20) parts.push(el("span", {class: "warn", text: `no signal for ${Math.round(st.idle)} s (a model may be loading)`}));
        enhEl.line.replaceChildren(...parts);
        if (st.preview && st.preview !== seen) { seen = st.preview; enhEl.bg.src = "/api/enhance/preview?v=" + seen; }
        await sleep(500);
      }
    })();
  }

  // the result: the interactive diff, and Retry (new seed), Discard, Replace or Store as Kapture
  function showEnhanced(c, st, leave) {
    enhEl.o.src = url("view", {name: c.name, edge: 2000});
    enhEl.e.src = "/api/enhance/result?v=" + st.seed;
    setCut(50);
    enhMode("result");
    const decide = (action, extra = {}) => api("/api/enhance/decide", {action, folder: M.folder, name: c.name, ...extra});
    const btns = enhButtons(
      ["Retry (new seed)", "", "Enhance the same picture again with another seed", async () => { await decide("retry"); watchEnhance(c); }],
      ["Discard", "", "Throw the result away", async () => { await decide("discard"); leave("Enhance result discarded."); }],
      ["Replace", "", "The enhanced picture takes the original's place; the original is kept and Undo brings it back", async () => {
        const r = await decide("replace");
        leave();
        await showAgain(r.name);
        status(`Replaced ✓  ${r.name}  (${r.size[0]}×${r.size[1]}); the original is kept.`, "ok", {label: "Undo", run: async () => {
          try {
            await api("/api/enhance/undo", {folder: M.folder, name: r.name, backup: r.backup, old: r.old, was_enhanced: r.was_enhanced});
            dropBitmap(r.name);
            await showAgain(r.old);
            status("The original is back.", "ok");
          } catch (e) { status(`Undo failed: ${e.message}`, "err"); }
        }});
      }],
      ["Store as Kapture", "btn-primary", "Save it as a new picture next to the original, named and captioned like a crop", async () => {
        const r = await decide("kapture", {text: text.value, trigger: M.trigger});
        leave();
        await refreshList();
        status(`Saved ✓  ${r.name}  (${r.size[0]}×${r.size[1]})${r.notes.length ? " - " + r.notes.join("; ") : ""}`, r.notes.length ? "warn" : "ok");
      }]);
    btns[btns.length - 1].focus();
  }

  // ---- selection mode: pick several pictures, then caption or delete them together -----------------------------------------
  // `MS.sel` holds names, but what acts is always the picked pictures that the list shows now: a filter can't hide a victim.
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const selectedNames = () => M.items.filter((it) => MS.sel.has(it[0])).map((it) => it[0]);
  const inGallery = () => MS.on && !MS.canvas;
  const gallery = $q("mkGallery");

  function makeTile(row) {
    const name = row[0];
    const open = el("button", {type: "button", title: "Open this picture (double click)", "aria-label": "Open this picture", text: "⤢",
      onclick: (e) => { e.stopPropagation(); expand(name); }});
    const drop = el("button", {type: "button", title: "Take it out of the selection", "aria-label": "Take it out of the selection", text: "×",
      onclick: (e) => { e.stopPropagation(); setPicked(name, false); }});
    const node = el("div", {class: "mk-tile", "data-name": name, title: name},
      el("img", {src: url("thumb", {name}), alt: "", loading: "lazy", draggable: "false"}),
      verdictChip(name), pills(row), el("span", {class: "state", text: "!"}), el("div", {class: "tools"}, open, drop));
    node.addEventListener("click", () => focusTile(name));
    node.addEventListener("dblclick", () => expand(name));
    return node;
  }

  function paintTile(name) {
    const n = MS.tiles.get(name);
    if (!n) return;
    const st = MS.status.get(name) || "";
    for (const k of ["queued", "working", "done", "failed"]) n.classList.toggle(k, k === st);
    n.title = st === "failed" ? `${name}: ${MS.errors.get(name) || "failed"}` : name;
    for (const b of n.querySelectorAll(".tools button")) b.disabled = !!MS.run && b.textContent === "×";
  }
  function markGalleryCur() {
    const at = M.items[M.index];
    for (const [name, n] of MS.tiles) n.classList.toggle("cur", !!at && at[0] === name);
  }
  function markSelected() {
    for (const [i, node] of M.thumbs) node.classList.toggle("sel", MS.on && !!M.items[i] && MS.sel.has(M.items[i][0]));
  }

  function syncGallery() {
    const names = selectedNames(), want = new Set(names), byName = new Map(M.items.map((it) => [it[0], it]));
    for (const [n, node] of MS.tiles) if (!want.has(n)) { node.remove(); MS.tiles.delete(n); }
    const nodes = names.map((n) => {
      let node = MS.tiles.get(n);
      if (!node) { node = makeTile(byName.get(n)); MS.tiles.set(n, node); paintTile(n); }
      else { const old = node.querySelector(".pills"); if (old) old.replaceWith(pills(byName.get(n))); }    // the list may have been read again
      return node;
    });
    if (nodes.length) gallery.replaceChildren(...nodes);
    else gallery.replaceChildren(el("div", {class: "empty", text: "Nothing is picked. Click pictures in the strip to pick them."}));
    markGalleryCur();
  }

  function lockText() {                                          // a caption that is about to be rewritten is not edited
    const st = MS.run && M.cur ? MS.status.get(M.cur.name) : "";
    text.readOnly = st === "queued" || st === "working";
  }

  function topText() {
    if (!MS.on) return;
    const n = selectedNames().length, run = MS.run, exit = $q("mkExitSel");
    const hidden = MS.sel.size - n;
    $q("mkSelCount").textContent = `${n} selected` + (hidden > 0 ? ` (+${hidden} hidden)` : "");
    $q("mkSelCount").title = hidden > 0 ? `${hidden} more picked, hidden by the filter: they stay picked, but nothing acts on them while hidden` : "";
    $q("mkSelAll").disabled = $q("mkSelNone").disabled = $q("mkSelInv").disabled = !!run;
    if (run) {
      const did = run.done + run.failed, left = run.names.length - did;
      const eta = did && left ? ` · about ${mmss(((performance.now() - run.t0) / did / 1000) * left)} left` : "";
      $q("mkMultiText").textContent = `Writing captions: ${did} of ${run.names.length}${run.failed ? ` · ${run.failed} failed` : ""}${eta}`;
      exit.textContent = run.stop ? "Stopping…" : "Stop processing";
      exit.disabled = run.stop;
    } else {
      $q("mkMultiText").textContent = MS.note || "";
      exit.textContent = "Exit selection mode";
      exit.disabled = false;
    }
    $q("mkUndoCaps").style.display = !run && MS.undo && MS.undo.after.size ? "" : "none";
  }

  // the picked set changed, or the list did: keep every view of it in step
  function multiChanged() {
    if (!MS.on) return;
    const exist = new Set(M.raw.map((it) => it[0]));
    MS.sel = new Set([...MS.sel].filter((n) => exist.has(n)));   // gone from the folder: not picked any more; hidden by a filter: still picked
    markSelected(); syncGallery(); ensureFocus(); topText(); syncButtons();
  }
  function ensureFocus() {
    const picked = selectedNames();
    if (!picked.length) {
      if (MS.canvas) backToGallery();
      if (M.cur) closePicture();
      return;
    }
    if (MS.canvas) return;                                       // the picture in the canvas stays whatever is picked
    if (!M.cur || !MS.sel.has(M.cur.name)) MS.focusing = open(M.items.findIndex((it) => it[0] === picked[0]));
  }

  function setPicked(name, on) {
    if (MS.run) return;
    if (on) MS.sel.add(name); else MS.sel.delete(name);
    multiChanged();
  }
  function pickAt(i, e) {                                        // a click on the strip: pick or unpick; Shift picks a range
    if (MS.run || !M.items[i]) return;
    if (e && e.shiftKey && MS.anchor >= 0) {
      for (let k = Math.min(MS.anchor, i); k <= Math.max(MS.anchor, i); k++) MS.sel.add(M.items[k][0]);
    } else {
      const name = M.items[i][0];
      if (MS.sel.has(name)) MS.sel.delete(name); else MS.sel.add(name);
      MS.anchor = i;
    }
    multiChanged();
  }
  function pickAll() { if (MS.run) return; for (const it of M.items) MS.sel.add(it[0]); multiChanged(); }
  function pickNone() { if (MS.run) return; MS.sel = new Set(); multiChanged(); }
  function pickInvert() {
    if (MS.run) return;
    for (const it of M.items) { if (MS.sel.has(it[0])) MS.sel.delete(it[0]); else MS.sel.add(it[0]); }   // hidden ones stay as they are
    multiChanged();
  }

  async function enterSelection() {
    if (MS.on || !M.folder || !M.items.length || M.modal) return;
    if (enh) { status(ENH_WAIT, "warn"); return; }
    try { await flush(); } catch (e) { status(e.message, "err"); return; }
    clearFrame();
    const exist = new Set(M.raw.map((it) => it[0]));
    const kept = new Set([...(MS.memory.get(M.folder) || [])].filter((n) => exist.has(n)));   // what is still in the folder
    Object.assign(MS, {on: true, canvas: false, anchor: M.index, status: new Map(), errors: new Map(), undo: null, note: "",
      folder: M.folder, sel: kept.size ? kept : new Set(M.cur ? [M.cur.name] : [])});
    root.classList.add("multi", "gal");
    status("");
    markSelected(); syncGallery(); topText(); syncButtons();
    MS.focusing = null;
    ensureFocus();                                               // the picture shown below is one of the picked
    await MS.focusing;                                           // (opening one clears the status line: say it after)
    if (kept.size) status(`The selection from before is back: ${plural(kept.size, "picture")} picked (Select none clears it).`);
  }
  function exitSelection(force = false) {
    if (!MS.on || (MS.run && !force)) return;
    if (MS.sel.size) MS.memory.set(MS.folder, new Set(MS.sel)); else MS.memory.delete(MS.folder);
    Object.assign(MS, {on: false, canvas: false, sel: new Set(), status: new Map(), errors: new Map(), undo: null, note: "", run: null});
    MS.tiles.clear(); gallery.replaceChildren();
    text.readOnly = false;
    root.classList.remove("multi", "gal");
    markSelected(); syncButtons(); resize(); focusHome();
  }

  async function focusTile(name) {                               // a click on a tile: its caption is shown below, to edit
    if (M.cur && M.cur.name === name) return;
    const i = M.items.findIndex((it) => it[0] === name);
    if (i >= 0) await open(i);
  }
  async function expand(name) {                                  // a double click: the picture in the canvas, to look and to frame
    const i = M.items.findIndex((it) => it[0] === name);
    if (i < 0) return;
    MS.canvas = true;
    root.classList.remove("gal");
    await open(i);
    resize(); topText(); syncButtons();
  }
  async function backToGallery() {
    if (!MS.on || !MS.canvas) return;
    try { await flush(); } catch (e) { status(e.message, "err"); }
    MS.canvas = false;
    root.classList.add("gal");
    syncGallery(); topText(); syncButtons();
    const n = M.cur && MS.tiles.get(M.cur.name);
    if (n) n.scrollIntoView({block: "nearest"});
  }

  function goSelected(d) {                                       // Prev / Next and PgUp / PgDn walk the picked pictures
    const names = selectedNames();
    if (!names.length) return;
    const at = M.cur ? names.indexOf(M.cur.name) : -1;
    const to = at < 0 ? (d > 0 ? 0 : names.length - 1) : at + d;
    if (to < 0 || to >= names.length) return;
    return open(M.items.findIndex((it) => it[0] === names[to]));
  }
  async function openNearestPicked(at) {                         // after a picture went: the picked one nearest to its place
    const picked = selectedNames();
    if (!picked.length) { closePicture(); return; }
    const idx = picked.map((n) => M.items.findIndex((it) => it[0] === n));
    await open(idx.find((i) => i >= at) ?? idx[idx.length - 1]);
  }

  // Trigger word, for every picked picture that has a caption (a picture without one stays without)
  async function addTriggerMany() {
    const names = selectedNames();
    if (!names.length || !M.trigger || MS.run || M.busy) return;
    try { await flush(); } catch (e) { status(e.message, "err"); return; }
    let changed = 0, skipped = 0, failed = 0;
    M.busy = true; syncButtons();
    try {
      for (const name of names) {
        try {
          const info = await api(url("info", {name, ext: capExtNow()}));
          if (!info.caption) { skipped++; continue; }
          const next = G.withTrigger(info.caption, M.trigger);
          if (next === info.caption.trim()) continue;
          const saved = await api("/api/caption", {folder: M.folder, name, text: next, loaded: info.caption, ext: capExtNow(), mechanical: true});
          changed++;
          setCap(name, {has: true, klass: saved.class, mtime: saved.mtime});
          if (M.cur && M.cur.name === name) { text.value = next; M.cur.loaded = next; M.dirty = false; }
        } catch (e) { failed++; }
      }
    } finally { M.busy = false; syncButtons(); }
    status(`The trigger word is first in ${plural(changed, "caption")}${skipped ? `; ${plural(skipped, "picture")} without a caption left alone` : ""}${failed ? `; ${failed} failed` : ""}.`,
      failed ? "warn" : "ok");
  }

  // Regenerate the captions of all the picked pictures, one after another. Each caption is written as soon as it is ready (the old
  // one is kept in .monkap/backups), so Stop leaves the finished ones as they are; Undo captions puts the old texts back.
  async function regenerateMany(hint = "") {
    const names = selectedNames();
    if (!names.length || MS.run || regen || enh || M.busy) return;
    if (!M.settings.lm_model) { openSettings("ai"); return; }
    hint = typeof hint === "string" ? hint.trim() : "";
    try { await flush(); } catch (e) { status(e.message, "err"); return; }
    MS.status = new Map(names.map((n) => [n, "queued"]));
    MS.errors = new Map();
    MS.undo = {before: new Map(), after: new Map(), notes: new Map()};
    MS.note = "";
    const run = MS.run = {names, stop: false, done: 0, failed: 0, streak: 0, t0: performance.now()};
    for (const n of names) paintTile(n);
    status("");
    syncButtons(); topText(); lockText();
    let why = "";
    for (const name of names) {
      if (run.stop) break;
      MS.status.set(name, "working"); paintTile(name); lockText();
      try {
        const r = await api("/api/recaption", {folder: M.folder, name, hint});
        MS.status.set(name, "done"); run.done++; run.streak = 0;
        MS.undo.before.set(name, r.before); MS.undo.after.set(name, r.caption); MS.undo.notes.set(name, r.note_before || null);
        setCap(name, {has: true, klass: r.class, mtime: r.mtime});
        const c = M.cur;
        if (c && c.name === name) { text.value = r.caption; c.loaded = r.caption; M.dirty = false; triggerState(); }
      } catch (e) {
        MS.status.set(name, "failed"); MS.errors.set(name, e.message); run.failed++; run.streak++;
        if (run.streak >= 3) { why = ` It stopped after three failures in a row: ${e.message}`; run.stop = true; }
      }
      paintTile(name); topText();
    }
    for (const n of names) if (MS.status.get(n) === "queued") { MS.status.delete(n); paintTile(n); }   // never reached: unmarked
    MS.run = null;
    MS.note = `${plural(run.done, "caption")} updated${run.failed ? `, ${run.failed} failed` : ""}${run.stop && !why ? " (stopped)" : ""}`;
    lockText(); syncButtons(); topText();
    status(`Captions updated for ${plural(run.done, "picture")}${run.failed ? `, ${run.failed} failed (hover the red label)` : ""}${run.stop && !why ? ", then stopped" : ""}.${why}`,
      run.failed || why ? "warn" : "ok");
  }

  async function undoCaptions() {
    const u = MS.undo;
    if (!u || MS.run || M.busy) return;
    MS.undo = null; MS.note = ""; topText();
    let back = 0, failed = 0;
    M.busy = true; syncButtons();
    try {
      for (const [name, after] of u.after) {
        const before = u.before.get(name) || "";
        try {
          const rr = await api("/api/caption", {folder: M.folder, name, text: before, loaded: after, ext: capExtNow(), restore: u.notes.get(name) || null});
          back++;
          MS.status.delete(name); paintTile(name); setCap(name, {has: rr.has_caption, klass: rr.class, mtime: rr.mtime});
          if (M.cur && M.cur.name === name) { text.value = before; M.cur.loaded = before || null; M.dirty = false; triggerState(); }
        } catch (e) { failed++; }
      }
    } finally { M.busy = false; syncButtons(); topText(); }
    status(`The previous captions are back for ${plural(back, "picture")}${failed ? `; ${failed} could not be restored` : ""}.`, failed ? "warn" : "ok");
  }

  // Delete selected: always asked, whatever the settings say about asking
  async function requestDeleteMany() {
    const names = selectedNames();
    if (!names.length || M.busy || MS.run) return;
    const soft = (M.settings || {}).delete_mode !== "hard";
    const shown = names.slice(0, 8);
    const body = el("div", {},
      el("p", {class: "lead", text: soft
        ? "The pictures and their captions move into this folder's removed/ folder; Undo brings them all back."
        : "The pictures and their captions are erased for good. This cannot be undone."}),
      el("div", {class: "quote", text: shown.join("\n") + (names.length > shown.length ? `\n… and ${names.length - shown.length} more` : "")}));
    const title = soft ? `Remove ${plural(names.length, "picture")}?` : `Delete ${plural(names.length, "picture")} for good?`;
    if (!(await confirmDialog(title, body, soft ? `Remove ${names.length}` : `Delete ${names.length}`, soft))) return;
    M.busy = true; syncButtons();
    const focusGoes = !!M.cur && names.includes(M.cur.name);
    if (focusGoes) M.dirty = false;                              // a caption of a picture that is going is not saved
    else { try { await flush(); } catch (e) { /* the page stays as it is */ } }
    try {
      const r = await api("/api/delete_many", {folder: M.folder, names, ext: capExtNow()});
      const gone = new Set(r.deleted);
      if (M.cur && gone.has(M.cur.name)) closePicture();
      for (const n of gone) MS.sel.delete(n);
      M.busy = false;
      M.items = await fetchList();
      M.index = M.cur ? M.items.findIndex((it) => it[0] === M.cur.name) : -1;
      MS.focusing = null;
      drawStrip(true);
      await MS.focusing;                                           // the picture that takes the focus clears the status line: wait for it
      if (!M.items.length) message("No pictures left in this folder.");
      const bad = r.failed.length ? `; ${plural(r.failed.length, "picture")} could not be removed (${r.failed[0].error})` : "";
      if (r.mode === "soft") {
        MS.lastMany = r.moved.length ? {moved: r.moved, names: r.deleted} : null;
        status(`${plural(r.deleted.length, "picture")} moved to removed/${bad}.`, r.failed.length ? "warn" : "ok",
          MS.lastMany ? {label: "Undo", run: undoDeleteMany} : null);
      } else {
        status(`${plural(r.deleted.length, "picture")} deleted${r.purged ? ` (${plural(r.purged, "cached latent file")} too)` : ""}${bad}.`, r.failed.length ? "warn" : "ok");
      }
    } catch (e) { M.busy = false; status(`Could not delete: ${e.message}`, "err"); }
    finally { syncButtons(); }
  }
  async function undoDeleteMany() {
    const d = MS.lastMany;
    if (!d) return;
    MS.lastMany = null;
    try {
      await api("/api/undelete", {folder: M.folder, moved: d.moved});
      M.items = await fetchList();
      if (MS.on) for (const n of d.names) MS.sel.add(n);        // they come back picked
      MS.focusing = null;
      drawStrip(true);
      await MS.focusing;
      status(`${plural(d.names.length, "picture")} back.`, "ok");
    } catch (e) { status(`Undo failed: ${e.message}`, "err"); }
  }

  // ---- delete ---------------------------------------------------------------------------------------------------
  async function requestDelete() {
    const c = M.cur;
    if (!c || M.busy || MS.run) return;
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
      MS.sel.delete(name);
      M.thumbs.clear(); stripIn.replaceChildren();
      closePicture();
      M.busy = false;
      M.index = -1;
      if (!M.items.length) { drawStrip(true); message("No pictures left in this folder."); }
      else if (MS.on) { drawStrip(true); await openNearestPicked(at); }
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
    const under = M.modal;                                       // a window opened over another (a file picker over the settings)
    document.body.append(veil);
    M.modal = veil;
    const done = () => { veil.remove(); M.modal = under; if (!under) focusHome(); };
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
  window.pickFolderFor = (start) => window.pywebview.api.pick_folder(start);
  const nativePicker = () => !!(window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder);

  async function openFolderDialog() {
    if (M.modal || MS.run) return;
    if (enh) { status(ENH_WAIT, "warn"); return; }
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
      el("div", {class: "field-hint", text: "Optional. Put first in the captions MonKap writes for this folder (a Kapture can copy it, AI ReCaption adds it)."}),
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

  // ---- a file picker of MonKap's own (the same in the window and in the browser): folders, and the files asked for ----------
  // resolves with the chosen file's path, or "" when it was cancelled
  function pickFile({title, exts, start}) {
    return new Promise((resolve) => {
      const path = el("input", {type: "text", spellcheck: "false", placeholder: "Folder", style: "flex:1 1 280px"});
      const list = el("div", {class: "mk-browse"});
      const here = el("div", {class: "field-hint"});
      const err = el("div", {class: "field-hint warn"});
      const cancel = el("button", {class: "btn", text: "Cancel"});
      const row = (label, p) => el("button", {type: "button", class: "mk-dir", text: label, onclick: () => browse(p)});
      let close = () => {};
      const finish = (p) => { close(); resolve(p); };
      async function browse(p) {
        err.textContent = "";
        try {
          const r = await api("/api/browse?path=" + encodeURIComponent(p || "") + "&files=" + encodeURIComponent(exts.join(",")));
          path.value = r.path;
          list.replaceChildren();
          if (r.path && r.parent !== null) list.append(row("↑  up", r.parent));
          for (const d of r.dirs) list.append(row(d.name, d.path));
          for (const f of r.files || []) list.append(el("button", {type: "button", class: "mk-dir mk-file", text: f.name, title: f.path, onclick: () => finish(f.path)}));
          here.textContent = !r.path ? "Drives" : (r.files || []).length ? "Click a file to choose it." : `No ${exts.join(" / ")} files here.`;
        } catch (e) { err.textContent = e.message; }
      }
      const {veil, done} = dialog([el("h2", {text: title}),
        el("div", {class: "row"}, path, el("button", {class: "btn", text: "Go", onclick: () => browse(path.value.trim())})),
        here, list, err, el("div", {class: "buttons"}, cancel)]);
      const onKey = (e) => { if (e.key === "Escape" && M.modal === veil) { e.preventDefault(); e.stopPropagation(); finish(""); } };
      close = () => { document.removeEventListener("keydown", onKey, true); done(); };
      document.addEventListener("keydown", onKey, true);
      cancel.addEventListener("click", () => finish(""));
      veil.addEventListener("mousedown", (e) => { if (e.target === veil) finish(""); });
      path.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); browse(path.value.trim()); } });
      browse(start || M.folder || "");
    });
  }

  // ---- settings: one tab for each kind of thing ---------------------------------------------------------------------
  async function openSettings(section) {
    if (M.modal) return;
    let s, tasks;
    try { const r = await api("/api/state"); s = r.settings; tasks = r.tasks; } catch (e) { status(e.message, "err"); return; }
    const field = (key, kind = "text") => el("input", {type: kind, "data-k": key, "data-t": kind, value: s[key] === undefined ? "" : String(s[key]), spellcheck: "false"});
    const row = (label, ...kids) => el("div", {class: "row"}, el("label", {class: "k", text: label}), ...kids);
    const hint = (t) => el("div", {class: "field-hint", text: t});
    const err = el("div", {class: "field-hint warn"});
    const radio = (name, value, label, h) => el("label", {class: "opt"},
      el("input", {type: "radio", name, value, checked: s[name] === value}),
      el("span", {}, label, h ? hint(h) : null));
    const tick = (key, label, h) => el("label", {class: "opt"},
      el("input", {type: "checkbox", "data-k": key, checked: !!s[key]}),
      el("span", {}, label, h ? hint(h) : null));
    const h3 = (t) => el("h3", {text: t, style: "margin:14px 0 4px"});

    // -- general: names and caption files
    const tpl = field("template");
    const eg = el("div", {class: "eg"});
    const preview = debounce(async () => {
      const r = await api("/api/template?t=" + encodeURIComponent(tpl.value)).catch(() => null);
      if (!r) return;
      eg.className = "eg" + (r.ok ? "" : " bad");
      eg.textContent = r.ok ? "Example: " + r.examples.join(", ") + " …" + (r.note ? "\n" + r.note : "") : r.error;
    }, 200);
    tpl.addEventListener("input", preview); preview();
    const general = [
      row("Names of new pictures:", tpl), eg,
      hint("{parent} is the picture's name, {n} the next free number ({n:03d} pads it to three digits). A number is never used twice, even after a crop is deleted."),
      row("Caption files end in:", field("caption_ext")),
      hint(".txt unless your trainer reads another extension. Ignored while MonKap follows Fizgig's (see the Fizgig tab)."),
      h3("A new picture's caption"),
      radio("caption_mode", "copy", "Copy the original's caption", "Edit it afterwards: the crop is flagged until you do."),
      radio("caption_mode", "trigger", "Copy it, with the trigger word first", "The trigger word of the folder (set when you open it)."),
      radio("caption_mode", "empty", "No caption", "The new picture has no caption file until it gets one."),
      h3("Other"),
      tick("store_provenance", "Remember where each crop came from", "A note in the folder's .monkap/provenance.jsonl; it is never a caption."),
      tick("show_thirds", "Rule-of-thirds lines in the frame")];

    // -- deleting
    const cacheBrowse = nativePicker() ? el("button", {type: "button", class: "btn btn-sm", id: "mkCacheBrowse", text: "Browse…", onclick: async () => {
      const f = veil.querySelector("input[data-k=latent_cache_dir]");
      const got = await window.pickFolderFor(f.value.trim());
      if (got) f.value = got;
    }}) : null;
    const deleting = [
      radio("delete_mode", "soft", "Move it to the folder's removed/ folder", "With Undo. Recommended."),
      radio("delete_mode", "hard", "Erase it for good", "The picture and its caption. No Undo."),
      tick("confirm_delete", "Ask before deleting", "Enter confirms, Esc cancels."),
      row("Latent cache folder:", field("latent_cache_dir"), cacheBrowse),
      hint("Optional. A trainer's cache of the pictures' latents: erasing a picture for good also clears its files there, so a new picture can't meet old ones. Ignored while MonKap follows Fizgig's cache.")];

    // -- the caption model: its server, the model, the task and what the task tells the model
    const url = field("lm_url");
    url.placeholder = "http://localhost:1234";
    const model = el("select", {"data-k": "lm_model", "data-t": "select"});
    const found = hint("");
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
    const task = el("select", {"data-k": "ai_task", "data-t": "select"}, tasks.map((t) => el("option", {value: t.key, text: t.label})));
    task.value = tasks.some((t) => t.key === s.ai_task) ? s.ai_task : "training";
    const instr = el("textarea", {rows: "7", style: "width:100%"});
    const instrNote = el("span", {class: "field-hint"});
    const showInstr = () => { const t = tasks.find((x) => x.key === task.value); instr.value = t.instruction; instrNote.textContent = t.edited ? "Edited." : "As shipped."; };
    const sendInstr = async (text, done) => {
      try { const r = await api("/api/ai/instruction", {key: task.value, text}); tasks = r.tasks; M.tasks = r.tasks; showInstr(); instrNote.textContent = done; }
      catch (e) { instrNote.textContent = e.message; }
    };
    const instrBox = el("div", {style: "display:none"}, instr,
      el("div", {class: "row"},
        el("button", {class: "btn btn-sm", text: "Save instruction", onclick: () => sendInstr(instr.value, "Saved.")}),
        el("button", {class: "btn btn-sm", text: "Back to the shipped text", onclick: () => sendInstr("", "Back to the shipped text.")}),
        instrNote));
    const edit = el("button", {class: "btn btn-sm", text: "Edit instruction…", onclick: () => {
      const open = instrBox.style.display === "none";
      instrBox.style.display = open ? "" : "none";
      if (open) showInstr();
    }});
    const tokens = field("ai_max_tokens", "number");
    tokens.className = "w-num"; tokens.min = "16"; tokens.max = "4000";
    task.addEventListener("change", () => {
      if (instrBox.style.display !== "none") showInstr();
      const t = tasks.find((x) => x.key === task.value);
      tokens.value = t.tokens;
    });
    const captioning = [
      row("Server:", url, el("button", {class: "btn btn-sm", text: "Refresh", onclick: loadModels})),
      row("Model:", model), found,
      row("Task:", task, edit), instrBox,
      row("Max tokens:", tokens, hint("How long a caption may be.")),
      tick("ai_think", "Let the model think first", "Slower, and the answer needs more room. Most models caption better and faster without it."),
      hint("Any server with LM Studio's or OpenAI's API works (LM Studio, Ollama, llama.cpp, vLLM…).")];

    // -- Fizgig: a read-only look at an install, for the few things it knows
    const fdir = field("fizgig_dir");
    fdir.placeholder = "The folder with fizgig.py in it";
    const report = el("div", {class: "mk-report"});
    let fizgigOk = false;
    const NOTE = "Fizgig integration enabled";
    // what Fizgig answers for: the field is locked and says so, while the integration is on and its tick is ticked; the
    // field's own value is kept and comes back when the integration (or the tick) goes
    function applyLocks() {
      const q = (k) => veil && veil.querySelector(`input[data-k=${k}]`);
      if (!veil) return;
      for (const k of ["fizgig_trigger", "fizgig_ext", "fizgig_cache"]) q(k).disabled = !fizgigOk;
      for (const [field_, tick_, browse] of [["caption_ext", "fizgig_ext", null], ["latent_cache_dir", "fizgig_cache", veil.querySelector("#mkCacheBrowse")]]) {
        const f = q(field_), lock = fizgigOk && q(tick_).checked;
        if (lock && !f.disabled) { f.dataset.mine = f.value; f.value = NOTE; f.disabled = true; f.classList.add("mk-field-note"); }
        if (!lock && f.disabled) { f.value = f.dataset.mine || ""; f.disabled = false; f.classList.remove("mk-field-note"); }
        if (browse) browse.style.display = lock ? "none" : "";
      }
    }
    async function checkFizgig() {
      report.replaceChildren();
      const d = fdir.value.trim();
      fizgigOk = false;
      if (!d) { applyLocks(); return; }
      let r;
      try { r = await api("/api/fizgig?dir=" + encodeURIComponent(d)); } catch (e) { r = {ok: false, error: e.message}; }
      if (!r.ok) { report.append(el("div", {class: "field-hint warn", text: r.error})); applyLocks(); return; }
      fizgigOk = true;
      applyLocks();
      report.append(el("div", {class: "field-hint ok-note", text: NOTE + ". What Fizgig knows replaces the matching fields (they are greyed out), unless you untick it below."}));
      const line = (k, v) => el("div", {class: "kv"}, el("span", {class: "k", text: k}), el("span", {text: v || "—"}));
      report.append(
        line("Fizgig", r.version ? `version ${r.version}` : "version unknown"),
        line("Training folder", r.training_folder),
        line("Trigger words", `${r.triggers} folder${r.triggers === 1 ? "" : "s"}` + (r.trigger_here ? `, here: ${r.trigger_here}` : "")),
        line("Caption extension", r.caption_ext),
        line("Latent cache", r.cache_root),
        line("LoRA output", r.output_dir));
      if (r.note) report.append(el("div", {class: "field-hint warn", text: r.note}));
    }
    fdir.addEventListener("change", checkFizgig);
    const browseF = nativePicker() ? el("button", {class: "btn btn-sm", text: "Browse…", onclick: async () => {
      const got = await window.pywebview.api.pick_folder(fdir.value.trim());
      if (got) { fdir.value = got; checkFizgig(); }
    }}) : null;
    const fizgig = [
      hint("Optional. Point MonKap at a Fizgig install and it reads a few things from it, instead of asking you twice."),
      row("Fizgig folder:", fdir, browseF, el("button", {class: "btn btn-sm", text: "Check", onclick: checkFizgig})),
      report,
      tick("fizgig_trigger", "Use Fizgig's trigger word", "For a folder that has none set in MonKap."),
      tick("fizgig_ext", "Use Fizgig's Caption Extension", "So the captions are the files its training reads."),
      tick("fizgig_cache", "Clear Fizgig's cached latents when a picture is erased for good", "Only the latents of that picture, in the folder Fizgig keeps for this dataset."),
      el("div", {class: "mk-warn", text: "This reads Fizgig's own files (user_data, its release notes), and never writes to them. It depends on the Fizgig version: if Fizgig changes how it stores folders or settings, it can stop working, and MonKap just goes back to its own settings. Watch for MonKap updates after Fizgig updates."})];

    // -- Enhance: a ComfyUI that is already running, and the template that says what it does
    const comfyUrl = field("comfy_url");
    comfyUrl.placeholder = "http://127.0.0.1:8188";
    const tplPath = field("enhance_template");
    tplPath.placeholder = "Empty: the reference template (Qwen Image 2.1)";
    const enhWords = field("enhance_prompt");
    enhWords.placeholder = "Empty: the template's own words";
    const enhReport = el("div", {class: "mk-report"});
    const say = (text, cls = "") => el("div", {class: "field-hint " + cls, text});
    async function checkEnhance() {
      enhReport.replaceChildren(say("Asking ComfyUI…"));
      let r;
      try { r = await api("/api/enhance/check?url=" + encodeURIComponent(comfyUrl.value.trim()) + "&template=" + encodeURIComponent(tplPath.value.trim())); }
      catch (e) { r = {ok: false, error: e.message, problems: []}; }
      enhReport.replaceChildren();
      if (r.error) enhReport.append(say(r.error, "warn"));
      for (const p of r.problems || []) enhReport.append(say("• " + p, "warn"));
      if (r.ok) enhReport.append(say(`The template is fine and this ComfyUI can run it (${r.custom ? "your template" : "the reference template"}, version ${r.version}).`, "ok-note"));
      if (r.ok && !r.has_size) enhReport.append(say("This template has no \"MonKap Size\" node, so the setting \"Enhanced picture size\" does nothing with it. Download the current reference template to see how it is wired.", "warn"));
    }
    const enhSize = el("select", {"data-k": "enhance_size", "data-t": "select"},
      [["1mp", "1 MP"], ["2mp", "2 MP (default)"], ["4mp", "4 MP"], ["original", "As the original (at most 4 MP)"]].map(([v, t]) => el("option", {value: v, text: t})));
    enhSize.value = ["1mp", "2mp", "4mp", "original"].includes(s.enhance_size) ? s.enhance_size : "2mp";
    const freeComfy = async () => {
      enhReport.replaceChildren(say("Asking ComfyUI…"));
      try { await api("/api/enhance/free", {url: comfyUrl.value.trim()}); enhReport.replaceChildren(say("ComfyUI was asked to unload its models and free the memory.", "ok-note")); }
      catch (e) { enhReport.replaceChildren(say(e.message, "warn")); }
    };
    const enhancing = [
      hint("Optional. MonKap hands the picture to a ComfyUI that is already running (it never starts one) and takes the result back. The pictures are sent to this address, so use one you trust."),
      row("ComfyUI:", comfyUrl, el("button", {type: "button", class: "btn btn-sm", text: "Check", onclick: checkEnhance})),
      row("Template:", tplPath, el("button", {type: "button", class: "btn btn-sm", text: "Browse…", onclick: async () => {
        const got = await pickFile({title: "Choose a template", exts: [".json"], start: tplPath.value.trim()});
        if (got) { tplPath.value = got; checkEnhance(); }
      }})),
      hint("A workflow saved in ComfyUI with Save (API Format). Empty: the reference template, made of ComfyUI's own nodes for Qwen Image 2.1."),
      row("Words:", enhWords),
      hint("What the template is told to do. Empty: the template's own words. Your words for one picture (the arrow next to Enhance) are added after these."),
      row("Enhanced picture size:", enhSize),
      hint("How big the enhanced picture is made. Larger is slower and needs more video memory. It works through the template's \"MonKap Size\" node; a template made before 0.5.0 ignores it (Check says so)."),
      enhReport,
      el("div", {class: "row"},
        el("a", {class: "btn btn-sm", href: "/api/enhance/template", download: "monkap-enhance-reference.json", text: "Download the reference template"}),
        el("button", {type: "button", class: "btn btn-sm", text: "Free ComfyUI's memory", title: "Unload its models, so a trainer has the video memory", onclick: freeComfy})),
      el("div", {class: "mk-warn", text: "Your own template: download the reference, open it in ComfyUI (drag the file into the window) and change what you like between the four nodes titled \"MonKap Image\", \"MonKap Prompt\", \"MonKap Seed\", \"MonKap Size\" (optional) and \"MonKap Result\". Those are how MonKap talks to the workflow: keep them, and keep them linked. Then Save (API Format) and put the file's path above. Finish the workflow with a preview node, so nothing is written to ComfyUI's output folder."})];

    // -- the open folder's housekeeping: every action counts first and always asks (the asking cannot be switched off)
    const mOut = el("div", {class: "field-hint", style: "margin-top:8px"});
    const mUndo = el("button", {type: "button", class: "btn btn-sm", style: "display:none", text: "Undo clearing"});
    let cleared = null;
    const bulkReload = async () => {                                // the list, the icons and the open picture's caption, again
      await loadReview().catch(() => {});
      await refreshList().catch(() => {});
      if (M.cur) { const i = M.items.findIndex((it) => it[0] === M.cur.name); M.cur = null; M.dirty = false; if (i >= 0) await open(i); }
    };
    const mAct = async (action, ask, said) => {
      mOut.textContent = "";
      mUndo.style.display = "none";
      if (!M.folder) { mOut.textContent = "Open a folder first."; return; }
      if (MS.run) { mOut.textContent = "Wait for the running ReCaption, or stop it."; return; }
      let dry;
      try { dry = await api("/api/maintenance", {folder: M.folder, action, dry: true}); } catch (e) { mOut.textContent = e.message; return; }
      if (!dry.count) { mOut.textContent = said.none; return; }
      const a = ask(dry);
      if (!(await confirmDialog(a.title, el("div", {}, el("p", {class: "lead", text: a.body}),
        el("div", {class: "quote", text: M.folder})), a.yes, false))) return;
      try {
        const r = await api("/api/maintenance", {folder: M.folder, action});
        mOut.textContent = said.done(r);
        if (action === "clear_captions") { cleared = r.items; mUndo.style.display = ""; }
        await bulkReload();
      } catch (e) { mOut.textContent = e.message; }
    };
    mUndo.addEventListener("click", async () => {
      if (!cleared) return;
      try {
        const r = await api("/api/maintenance", {folder: M.folder, action: "restore_captions", items: cleared});
        cleared = null; mUndo.style.display = "none";
        mOut.textContent = `${plural(r.count, "caption")} back.`;
        await bulkReload();
      } catch (e) { mOut.textContent = e.message; }
    });
    const mMB = (b) => (b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB");
    const mRow = (label, what, run) => el("div", {class: "row", style: "align-items:flex-start"},
      el("button", {type: "button", class: "btn btn-sm", style: "flex:0 0 230px", text: label, onclick: run}), el("div", {class: "field-hint", style: "flex:1 1 200px", text: what}));
    const maintaining = [
      hint("Housekeeping of the folder that is open now. Each button counts what it would do and asks before it does it."),
      mRow("Clear all captions…", "Removes the caption file of every picture. Each text is kept in .monkap/backups, and Undo (right after) puts them back.",
        () => mAct("clear_captions", (d) => ({title: `Clear ${plural(d.count, "caption")}?`, yes: `Clear ${d.count}`,
          body: `The caption file of ${plural(d.count, "picture")} is removed; the pictures stay and are left out of training until they have captions again. Each text is kept in .monkap/backups, and Undo restores them.`}),
          {none: "No caption files in this folder.", done: (r) => `${plural(r.count, "caption")} cleared (their texts are in .monkap/backups).${r.failed && r.failed.length ? ` ${r.failed.length} could not be cleared.` : ""}`})),
      mRow("Delete empty caption files…", "Caption files with nothing in them: a trainer takes such a picture as captioned with an empty caption.",
        () => mAct("empty_captions", (d) => ({title: `Delete ${plural(d.count, "empty caption file")}?`, yes: `Delete ${d.count}`,
          body: `${plural(d.count, "caption file")} with no text in this folder are deleted.`}),
          {none: "No empty caption files.", done: (r) => `${plural(r.count, "empty caption file")} deleted.`})),
      mRow("Empty the removed/ folder…", "Erases for good the pictures that the soft delete moved to removed/ (no Undo after this).",
        () => mAct("empty_removed", (d) => ({title: "Erase removed/ for good?", yes: "Erase for good",
          body: `${plural(d.count, "file")} (${mMB(d.bytes || 0)}) in removed/ are erased. This cannot be undone.`}),
          {none: "removed/ is empty or not there.", done: (r) => `${plural(r.count, "file")} erased (${mMB(r.bytes || 0)}).`})),
      mRow("Forget MonKap's notes…", "Crop names MonKap has given, where each crop came from, and who wrote each caption. The caption backups and the replaced originals stay.",
        () => mAct("clear_notes", (d) => ({title: "Forget MonKap's notes about this folder?", yes: "Forget the notes",
          body: `${plural(d.count, "notes file")} in .monkap are deleted: the next crop may take a name that was used before, and every caption counts as written by hand again.`}),
          {none: "MonKap has no notes about this folder.", done: (r) => `${plural(r.count, "notes file")} deleted.`})),
      el("div", {class: "row"}, mUndo), mOut];

    // -- what MonKap keeps
    const dataNote = el("div", {class: "field-hint"});
    async function showData() {
      try {
        const r = await api("/api/data");
        const total = r.files.reduce((a, f) => a + f[1], 0);
        const labFiles = r.files.filter((f) => /^lab[\\/]/.test(f[0]));
        const labResults = labFiles.filter((f) => !/state\.json$/.test(f[0])).length, labLists = labFiles.length - labResults;
        const labWarn = labFiles.length ? `It also holds the Enhance lab's work: ${labLists} task list${labLists === 1 ? "" : "s"} and ${labResults} enhanced picture${labResults === 1 ? "" : "s"} still waiting for your decision (${Math.max(1, Math.round(labFiles.reduce((a, f) => a + f[1], 0) / 1048576))} MB). Removing the files deletes them.` : "";
        dataNote.replaceChildren(
          el("div", {text: `MonKap's own files: ${r.root}  (${r.files.length} file${r.files.length === 1 ? "" : "s"}, ${Math.max(1, Math.round(total / 1024))} KB: your settings and the folders you opened).`}),
          el("div", {text: "Inside a dataset folder MonKap keeps a hidden .monkap folder (the crop ledger and caption backups), and nothing else besides the pictures and captions you asked for. Pictures, previews and tiles are made in memory and never written out. Deleting files from a disk doesn't guarantee they can't be recovered."}),
          labWarn ? el("div", {class: "field-hint warn", text: labWarn}) : null,
          (() => {
            const b = el("button", {class: "btn btn-sm", text: "Remove MonKap's own files…", style: "margin-top:6px"});
            let armed = false;
            b.addEventListener("click", async () => {
              if (!armed) {
                armed = true;
                b.textContent = "Click again: settings and the list of opened folders" + (labFiles.length ? ", and the Enhance lab's tasks and waiting results," : "") + " are deleted (datasets are not touched)";
                return;
              }
              try {
                const x = await api("/api/data/forget", {});
                dataNote.textContent = `Removed ${x.removed} file(s). The page keeps working with the defaults.`;
              } catch (e) { dataNote.textContent = e.message; }
            });
            return b;
          })());
      } catch (e) { dataNote.textContent = e.message; }
    }
    const data = [el("div", {class: "row"}, el("button", {class: "btn btn-sm", text: "What does MonKap keep on this computer?", onclick: showData})), dataNote];

    // -- the tabs
    const tabs = [["general", "General", general], ["captions", "Captions", captioning], ["deleting", "Deleting", deleting],
      ["enhance", "Enhance", enhancing], ["fizgig", "Fizgig", fizgig], ["maintenance", "Current dataset maintenance", maintaining], ["data", "Data", data]];
    const panes = tabs.map(([key, , nodes]) => el("div", {class: "mk-pane", "data-pane": key, style: "display:none"}, nodes));
    const bar = el("div", {class: "mk-tabs", role: "tablist"});
    const show = (key) => {
      for (const [k] of tabs) {
        bar.querySelector(`[data-tab="${k}"]`).classList.toggle("on", k === key);
        panes.find((p) => p.dataset.pane === k).style.display = k === key ? "" : "none";
      }
    };
    for (const [key, label] of tabs) bar.append(el("button", {type: "button", class: "mk-tab", "data-tab": key, text: label, onclick: () => show(key)}));
    const save = el("button", {class: "btn btn-primary", text: "Save"});
    const cancel = el("button", {class: "btn", text: "Cancel"});
    let veil = null;
    const shown = dialog([el("h2", {text: "MonKap settings"}), bar, el("div", {class: "mk-panes"}, panes), err,
      el("div", {class: "buttons"}, cancel, save)]);
    veil = shown.veil;
    const done = shown.done;
    const onKey = (e) => { if (e.key === "Escape" && M.modal === veil) { e.preventDefault(); e.stopPropagation(); close(); } };
    const close = () => { document.removeEventListener("keydown", onKey, true); done(); };
    document.addEventListener("keydown", onKey, true);
    cancel.addEventListener("click", close);
    veil.addEventListener("mousedown", (e) => { if (e.target === veil) close(); });
    for (const k of ["fizgig_ext", "fizgig_cache"]) veil.querySelector(`input[data-k=${k}]`).addEventListener("change", applyLocks);
    applyLocks();
    save.addEventListener("click", async () => {
      const next = {template: tpl.value};
      for (const r of veil.querySelectorAll("input[type=radio]:checked")) next[r.name] = r.value;
      for (const k of veil.querySelectorAll("input[type=checkbox]")) next[k.dataset.k] = k.checked;
      for (const i of veil.querySelectorAll("[data-t]")) if (!i.disabled) next[i.dataset.k] = i.dataset.t === "number" ? Number(i.value) : i.value;
      try {
        const r = await api("/api/settings", {settings: next});
        M.settings = r.settings; M.tasks = r.tasks; M.ext = r.caption_ext;
        close();
        status(r.note || "Settings saved.", r.note ? "warn" : "ok");
        await loadReview().catch(() => {});
        await refreshList().catch(() => {});                  // the caption extension or the trigger word may have changed
        redraw(); syncButtons();
      } catch (e) { err.textContent = e.message; }
    });
    show(section === "ai" ? "captions" : section === "enhance" ? "enhance" : "general");
    loadModels().then(() => { if (section === "ai") model.focus(); });
    if (s.fizgig_dir) checkFizgig();
  }

  // ---- keys -----------------------------------------------------------------------------------------------------
  document.addEventListener("keydown", (e) => {
    if (!active() || M.modal || root.classList.contains("lab")) return;           // the lab has its own keys
    const typing = e.target instanceof Element && e.target.closest("textarea, input:not([type=range]), select, [contenteditable]");
    const mod = e.ctrlKey || e.altKey || e.metaKey;
    if (e.code === "KeyC" && !mod && !e.shiftKey && !typing) {   // by the physical key: any layout, any NumLock
      e.preventDefault();
      if (!e.repeat && !inGallery()) kapture();                  // a held key is ONE crop
    } else if (e.key === "Escape") {
      if (pop) { closePop(); return; }
      if (M.frame || typing) { e.preventDefault(); clearFrame(); text.focus({preventScroll: true}); }
    } else if (e.key === "PageUp" || e.key === "PageDown") {
      e.preventDefault();
      if (!e.repeat) go(e.key === "PageUp" ? -1 : 1);
    } else if (e.key === "Delete" && e.shiftKey && !mod) {
      const sel = e.target === text && text.selectionStart !== text.selectionEnd;
      if (sel || inGallery()) return;                            // Shift+Delete is Cut when text is selected; in the gallery deleting is the button
      e.preventDefault();
      if (!e.repeat) requestDelete();                            // a held key is ONE picture
    } else if (e.key.startsWith("Arrow") && !typing && M.frame && e.target === stage) {
      e.preventDefault();
      nudge(e.key, e.shiftKey, e.ctrlKey);
    }
  }, true);

  // ---- the rail: Workbench (this page) and the Enhance lab. Neither way is open while something is running; a greyed icon says why ----
  function syncRail() {
    const inLab = root.classList.contains("lab");
    railMain.classList.toggle("on", !inLab);
    railLab.classList.toggle("on", inLab);
    railMain.disabled = inLab && !!(lab && lab.running);
    railMain.title = railMain.disabled ? LEAVE_LAB : "Workbench";
    const why = inLab ? "" : !M.folder ? "Open a folder first" : MS.run ? RUN_WAIT : enh ? ENH_WAIT : regen ? "A caption is being written" :
      MS.on ? "Leave selection mode first" : M.busy ? "Wait a moment: MonKap is working on the folder" : M.modal ? "Close the open window first" : "";
    railLab.disabled = !!why;
    railLab.title = why || "Enhance lab: enhance many pictures in tasks, then decide about each result";
  }
  async function enterLab() {
    if (railLab.disabled || root.classList.contains("lab")) return;
    try { await flush(); } catch (e) { status(e.message, "err"); return; }
    root.classList.add("lab");
    await lab.enter();
    syncRail();
  }
  function leaveLab() {
    if (!root.classList.contains("lab") || (lab && lab.running)) return;
    lab.leave();
    root.classList.remove("lab");
    syncRail();
    resize();
  }
  lab = MonKapLab.create({
    root, M, url, pills, verdictOf,
    blocked: () => !!M.modal,
    refresh: () => refreshList(),
    forget: (name) => dropBitmap(name),
    onState: (st) => { if (st.running !== labBusy) { labBusy = st.running; syncButtons(); } syncRail(); },
  });
  railLab.addEventListener("click", enterLab);
  railMain.addEventListener("click", leaveLab);

  // ---- wiring ---------------------------------------------------------------------------------------------------
  $q("mkPrev").addEventListener("click", () => go(-1));
  $q("mkNext").addEventListener("click", () => go(1));
  $q("mkKapture").addEventListener("click", kapture);
  $q("mkDelete").addEventListener("click", () => (inGallery() ? requestDeleteMany() : requestDelete()));
  $q("mkFit").addEventListener("click", fitView);
  $q("mkActual").addEventListener("click", actualSize);
  $q("mkZoomIn").addEventListener("click", () => zoomBy(1.25));
  $q("mkZoomOut").addEventListener("click", () => zoomBy(0.8));
  $q("mkTrigger").addEventListener("click", () => (inGallery() ? addTriggerMany() : addTrigger()));
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
  $q("mkRegen").addEventListener("click", () => (inGallery() ? regenerateMany() : regenerate()));
  $q("mkSelEnter").addEventListener("click", enterSelection);
  $q("mkSelAll").addEventListener("click", pickAll);
  $q("mkSelNone").addEventListener("click", pickNone);
  $q("mkSelInv").addEventListener("click", pickInvert);
  $q("mkBackGal").addEventListener("click", backToGallery);
  $q("mkUndoCaps").addEventListener("click", undoCaptions);
  $q("mkExitSel").addEventListener("click", () => { if (MS.run) { MS.run.stop = true; topText(); } else exitSelection(); });
  $q("mkHintBtn").addEventListener("click", () => openHint("regen"));
  $q("mkEnhance").addEventListener("click", () => enhance());
  $q("mkEnhHintBtn").addEventListener("click", () => openHint("enhance"));
  $q("mkShow").addEventListener("change", rearrange);
  $q("mkSort").addEventListener("change", rearrange);
  $q("mkLegend").addEventListener("click", (e) => { e.preventDefault(); openReviewHelp(); });
  $q("mkNoData").addEventListener("click", openReviewHelp);
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
      [key("⇧", "Del"), "delete this picture (the caption box keeps Cut when text is selected)"],
      [txt("Enter selection mode"), "pick pictures in the strip (Shift+click a range) to ReCaption them with the model, add the trigger word or delete them together; deleting always asks. The gallery is for the mouse; PgUp and PgDn walk the picked pictures. The pick is remembered for each folder until the page is closed"]];
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
    await loadReview();
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

  // ---- live: while the page is open, anything changed elsewhere (a training run writing its verdicts, Fizgig or another
  // program adding or rewriting files, a new trigger word or extension in Fizgig) shows within a few seconds -----------
  let live = null, liveBusy = false;
  async function liveTick() {
    if (liveBusy || document.hidden || !M.folder || M.busy || regen || MS.run) return;
    liveBusy = true;
    try {
      const c = M.cur;
      const now = await api(url("live", {name: c ? c.name : ""}));
      const was = live;
      live = now;
      const lb = !!(now.busy && now.busy.lab);
      if (lb !== labBusy) { labBusy = lb; syncButtons(); }
      if (lb && lab && !lab.open && !root.classList.contains("lab")) {   // tasks are running (the page was reloaded): into the lab, rail blocked
        const ok = !railLab.disabled;
        if (ok) { enterLab(); return; }
      }
      if (!enh && c && M.cur === c && now.busy && now.busy.single) {   // the page was reloaded during a run: pick it up again
        const st = await api("/api/enhance/status");
        if (st.name === c.name && st.folder === M.folder && !enh) watchEnhance(c);
      }
      if (!was) return;                                           // the first answer is only the starting point
      if (now.ext !== was.ext) { M.ext = now.ext; }
      if (now.trigger !== was.trigger) { M.trigger = now.trigger; triggerState(); }
      if (now.review !== was.review) {
        await loadReview();
        if (M.cur) showVerdict(M.cur.name);
        await rearrange();
      }
      if (now.folder !== was.folder || now.ext !== was.ext) await refreshList();
      if (c && M.cur === c && !M.dirty && now.name === c.name && now.caption !== c.loaded) {   // rewritten behind the page's back
        c.loaded = now.caption; text.value = now.caption || ""; $q("mkBadge").style.display = "none";
        setCap(c.name, {has: !!now.caption, klass: now.class, mtime: now.mtime}); triggerState();
        status("This caption was changed on disk (by Fizgig or another program): the new text is shown.", "warn");
      }
    } catch (e) { /* the server is busy or gone: the next question tries again */ }
    finally { liveBusy = false; }
  }
  setInterval(liveTick, 3000);
  window.addEventListener("focus", () => { live = null; liveTick(); });

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
