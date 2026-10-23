// The Enhance lab: a gallery of every picture of the folder, and next to it the tasks (pictures + one extra instruction) that
// ComfyUI works through one picture at a time. A finished picture waits for a decision (Resolve, below). The server does the work and
// keeps the tasks and the waiting results on disk; this file only shows them and sends the user's choices.
"use strict";
const MonKapLab = (() => {
  const SOFT = 180, VERY_SOFT = 60;                     // blur score: below is "soft", below is "very soft" (a rough hint, not a measurement)
  const mp = (w, h) => (w * h) / 1e6;
  const fmtMp = (v) => (v >= 10 ? v.toFixed(0) : v.toFixed(1)) + " MP";
  const mins = (s) => (s < 90 ? `${Math.max(1, Math.round(s))} s` : `${Math.round(s / 60)} min`);
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

  // host: {root (the element to put the lab in), M, url(route, opts), pills(row), verdictOf(name), onState(st), blocked(), resolve(name)}
  function create(host) {
    const M = host.M;
    const view = el("section", {class: "mk-lab", id: "mkLab", hidden: true, "aria-label": "Enhance lab"});
    host.root.append(view);
    let open = false, st = null, facts = {}, factsLeft = 0, factsTimer = 0, timer = 0, sig = "", polling = false;
    let picked = new Set(), anchor = -1, shown = [], armed = false;
    const tiles = new Map(), cards = new Map(), eta = new Map();
    let lastFolder = null;

    // ---- the left side: filters and the gallery ------------------------------------------------------------------------
    const show = el("select", {title: "Which pictures to list"});
    const sort = el("select", {title: "The order of the list"},
      [["name", "By name"], ["soft", "Blurriest first (rough estimate)"], ["small", "Smallest first"], ["date", "Caption changed (newest first)"]]
        .map(([v, t]) => el("option", {value: v, text: t})));
    const count = el("span", {class: "lab-count"});
    const pickBtn = (label, title, run) => el("button", {type: "button", class: "btn btn-sm", text: label, title, onclick: run});
    const bar = el("div", {class: "lab-bar"}, show, sort,
      pickBtn("All", "Pick all the pictures listed (Ctrl+A)", () => pick(shown.map((r) => r[0]), true)),
      pickBtn("None", "Pick none", () => { picked = new Set(); picksChanged(); }),
      pickBtn("Invert", "Invert the picks among the pictures listed", () => {
        const now = new Set(shown.map((r) => r[0]).filter((n) => !picked.has(n)));
        for (const r of shown) picked.delete(r[0]);
        for (const n of now) picked.add(n);
        picksChanged();
      }), count);
    const grid = el("div", {class: "lab-grid", tabindex: "0", "aria-label": "The pictures of the folder"});
    const left = el("div", {class: "lab-left"}, bar, grid);

    const inTask = (name) => !!(st && st.assigned[name]);
    const pending = (name) => !!(st && st.pending.some((p) => p.name === name));
    const failed = (name) => !!(st && st.tasks.some((t) => t.failed && t.failed[name]));
    const PROBLEMS = ["excluded", "stuck", "suspect", "watch"];
    const problem = (name) => { const v = host.verdictOf(name); return !!(v && PROBLEMS.includes(v.verdict)); };
    const small = (name) => { const f = facts[name]; return !!(f && f[0] && mp(f[0], f[1]) < 1); };
    const soft = (name) => { const f = facts[name]; return !!(f && f[2] !== null && f[2] !== undefined && f[2] < SOFT); };
    const FILTERS = {
      cand: (r) => !r[4] && !inTask(r[0]) && !pending(r[0]), wait: (r) => pending(r[0]), task: (r) => inTask(r[0]),
      failed: (r) => failed(r[0]), enh: (r) => !!r[4], small: (r) => small(r[0]), soft: (r) => soft(r[0]), problems: (r) => problem(r[0]),
    };

    function renderShow() {
      const rows = M.raw || [], keep = show.value;
      const n = (k) => rows.filter(FILTERS[k]).length;
      const opts = [["all", `All pictures (${rows.length})`], ["cand", `Candidates: not enhanced, in no task (${n("cand")})`],
        ["wait", `Waiting for a decision (${n("wait")})`], ["task", `In a task (${n("task")})`], ["failed", `Failed in a task (${n("failed")})`],
        ["enh", `Enhanced by AI (${n("enh")})`], ["small", `Under 1 MP (${n("small")})`], ["soft", `Look soft (${n("soft")})`]];
      if (M.review && M.review.available) opts.push(["problems", `Training problems (${n("problems")})`]);
      show.replaceChildren(...opts.map(([v, t]) => el("option", {value: v, text: t})));
      show.value = opts.some((o) => o[0] === keep) ? keep : "all";
    }

    function arranged() {
      const p = FILTERS[show.value] || null, by = sort.value;
      const out = (M.raw || []).filter((r) => !p || p(r));
      const area = (r) => { const f = facts[r[0]]; return f && f[0] ? f[0] * f[1] : 1e15; };
      const sharp = (r) => { const f = facts[r[0]]; return f && f[2] !== null && f[2] !== undefined ? f[2] : 1e12; };
      if (by === "soft") out.sort((a, b) => sharp(a) - sharp(b));
      else if (by === "small") out.sort((a, b) => area(a) - area(b));
      else if (by === "date") out.sort((a, b) => (b[3] || 0) - (a[3] || 0));
      return out;
    }

    function makeTile(row) {
      const name = row[0];
      const node = el("div", {class: "lab-tile", "data-name": name, title: name},
        el("img", {src: host.url("thumb", {name}), alt: "", loading: "lazy", draggable: "false"}),
        el("span", {class: "sz"}), el("span", {class: "bl"}), el("span", {class: "tk"}), host.pills(row),
        el("span", {class: "ck", text: "✓"}),
        el("button", {type: "button", class: "wt", title: "Look at the enhanced picture and decide", "aria-label": "Decide about the enhanced picture", text: "?",
          onclick: (e) => { e.stopPropagation(); openResolve(name); }}),
        el("span", {class: "fl", title: "Failed in its task", text: "!"}));
      node.addEventListener("click", (e) => clickTile(name, e));
      return node;
    }

    function paintTile(name) {
      const n = tiles.get(name);
      if (!n) return;
      const f = facts[name], t = st && st.assigned[name];
      n.classList.toggle("picked", picked.has(name));
      n.classList.toggle("waiting", pending(name));
      n.classList.toggle("failed", failed(name));
      n.querySelector(".sz").textContent = f && f[0] ? `${fmtMp(mp(f[0], f[1]))} · ${f[0]}×${f[1]}` : "";
      const bl = n.querySelector(".bl"), b = f ? f[2] : null;
      bl.textContent = b === null || b === undefined ? "" : b < VERY_SOFT ? "very soft" : b < SOFT ? "soft" : "";
      bl.title = "A rough sharpness estimate from the picture's fine detail: only a hint";
      n.querySelector(".tk").textContent = t ? `Task ${t}` : "";
      n.title = failed(name) ? `${name}: ${st.tasks.map((x) => x.failed && x.failed[name]).find(Boolean)}` : name;
    }
    function paintAll() { for (const name of tiles.keys()) paintTile(name); }

    function rebuild() {
      shown = arranged();
      tiles.clear();
      const frag = document.createDocumentFragment();
      for (const r of shown) { const n = makeTile(r); tiles.set(r[0], n); frag.append(n); }
      grid.replaceChildren(...(shown.length ? [frag] : [el("div", {class: "empty", text: (M.raw || []).length ? "No pictures match this filter." : "No pictures in this folder."})]));
      paintAll();
      picksChanged(true);
    }

    function clickTile(name, e) {
      if (e.shiftKey && anchor >= 0 && shown[anchor]) {
        const a = anchor, b = shown.findIndex((r) => r[0] === name);
        if (b >= 0) pick(shown.slice(Math.min(a, b), Math.max(a, b) + 1).map((r) => r[0]), true);
      } else {
        if (picked.has(name)) picked.delete(name); else picked.add(name);
        picksChanged();
      }
      anchor = shown.findIndex((r) => r[0] === name);
    }
    function pick(names, on) { for (const n of names) { if (on) picked.add(n); else picked.delete(n); } picksChanged(); }
    function picksChanged(quiet) {
      armed = false;
      if (!quiet) for (const [name, n] of tiles) n.classList.toggle("picked", picked.has(name));
      count.textContent = `${shown.length} listed · ${picked.size} picked`;
      renderMake();
    }

    // ---- the pictures' facts (size, rough sharpness): read in the background by the server ---------------------------------
    async function loadFacts() {
      clearTimeout(factsTimer);
      if (!open || !M.folder) return;
      let r;
      try { r = await api(host.url("lab/facts")); } catch (e) { return; }
      facts = r.items; factsLeft = r.left;
      paintAll(); renderShow();
      if (factsLeft) factsTimer = setTimeout(loadFacts, 800);
      else if (["soft", "small"].includes(sort.value) || ["small", "soft"].includes(show.value)) rebuild();
    }

    // ---- the right side: make a task, and the tasks ----------------------------------------------------------------------
    const words = el("textarea", {rows: "3", placeholder: "Additional instruction for the enhancer (empty: the defaults)", spellcheck: "false",
      "aria-label": "Additional instruction for the new task"});
    const make = el("button", {type: "button", class: "btn btn-primary btn-sm", onclick: makeTask});
    const labNote = el("div", {class: "field-hint lab-note"});
    const list = el("div", {class: "lab-list"});
    const side = el("aside", {class: "lab-tasks"}, el("h3", {text: "Tasks"}), el("div", {class: "lab-new"}, words, make, labNote), list);
    view.append(left, side);

    function say(text, kind = "", action = null) {
      labNote.className = "field-hint lab-note" + (kind ? " " + kind : "");
      labNote.textContent = text || "";
      if (action) labNote.append(" ", el("button", {type: "button", class: "btn btn-sm", text: action.label, onclick: action.run}));
    }
    function renderMake() {
      const n = picked.size;
      make.disabled = !n;
      make.textContent = armed ? "Create the task anyway" : n ? `Create a task from ${plural(n, "picked picture")}` : "Create a task from the picked pictures";
      make.title = n ? "A task is a list of pictures and one extra instruction; start it when you are ready" : "Pick some pictures in the gallery first";
    }
    async function makeTask() {
      const names = [...picked];
      const enhanced = (M.raw || []).filter((r) => picked.has(r[0]) && r[4]).length;
      if (enhanced && !armed) {
        armed = true; renderMake();
        say(`${plural(enhanced, "picked picture")} ${enhanced === 1 ? "was" : "were"} already enhanced by AI. Enhancing again works from the current picture, not the first original. Click again to make the task anyway.`, "warn");
        return;
      }
      make.disabled = true;
      try {
        const r = await api("/api/lab/task", {folder: M.folder, names, instruction: words.value});
        picked = new Set(); words.value = "";
        const more = r.skipped.length > 6 ? ` and ${r.skipped.length - 6} more` : "";
        say(r.skipped.length ? `Task made with ${plural(r.added, "picture")}. Left out, because they are in a task or wait for a decision: ${r.skipped.slice(0, 6).join(", ")}${more}.`
          : `Task made with ${plural(r.added, "picture")}.`, r.skipped.length ? "warn" : "ok-note");
        await poll(true);
      } catch (e) { say(e.message, "warn"); }
      armed = false;
      picksChanged();
    }

    function stateText(t) {
      if (t.state === "running") return "running";
      if (t.state === "queued") return `queued, number ${t.position}`;
      if (t.state === "done") return "done";
      if (t.state === "stopped") return "stopped";
      return "not started";
    }
    function etaText(t) {
      const e = eta.get(t.id), left = t.pictures.length - Object.keys(t.failed || {}).length;
      if (!e || t.state !== "running" || t.done <= e.done0 || left <= 0) return "";
      const per = (Date.now() - e.t0) / 1000 / (t.done - e.done0);
      return `, about ${mins(per * left)} left`;
    }
    function card(t) {
      const live = t.state === "queued" || t.state === "running";
      const box = el("div", {class: `lab-card ${t.state}`, "data-id": t.id});
      const act = [];
      if (live) act.push(el("button", {type: "button", class: "btn btn-sm btn-danger", text: "Stop",
        title: t.state === "running" ? "Interrupt the picture ComfyUI is working on and stop the task" : "Take this task out of the queue", onclick: () => call("stop", t.id)}));
      else act.push(el("button", {type: "button", class: "btn btn-sm btn-primary", text: t.state === "stopped" ? "Start again" : "Start", disabled: !t.pictures.length,
        title: t.pictures.length ? "Put the task in the queue: it starts at once when nothing else is running" : "The task has no pictures left",
        onclick: () => call("start", t.id)}));
      if (!live) act.push(el("button", {type: "button", class: "btn btn-sm", text: "Remove", title: "Remove the task (the pictures stay as they are)", onclick: () => call("task/delete", t.id)}));
      const ins = el("textarea", {rows: "2", spellcheck: "false", placeholder: "Additional instruction (empty: the defaults)", readonly: live,
        title: live ? "A running task can't be edited: stop it first" : "Saved when you leave the box"});
      ins.value = t.instruction || "";
      ins.addEventListener("change", () => call("task/update", t.id, {instruction: ins.value}));
      const frac = t.total ? Math.round(100 * t.done / t.total) : 0;
      const failedNames = Object.keys(t.failed || {});
      const cur = st.current && st.current.task === t.id && !st.current.regen ? st.current : null;
      const parts = [el("div", {class: "head"}, el("b", {text: t.name}), el("span", {class: "state", text: stateText(t)})),
        ins,
        el("div", {class: "bar"}, el("i", {style: `width:${frac}%`})),
        el("div", {class: "line", text: `${t.done} of ${t.total} done${etaText(t)}`}),
        cur ? el("div", {class: "line cur", text: `${cur.name}: ${cur.stage}${cur.steps ? ` (step ${cur.step} of ${cur.steps})` : ""}`}) : null,
        t.error ? el("div", {class: "field-hint warn", text: t.error}) : null,
        failedNames.length ? el("details", {class: "fails"}, el("summary", {text: `${plural(failedNames.length, "picture")} failed`}),
          ...failedNames.map((n) => el("div", {class: "f", title: t.failed[n], text: `${n}: ${t.failed[n]}`})),
          !live ? el("button", {type: "button", class: "btn btn-sm", text: "Take the failed ones out", onclick: () => call("task/update", t.id, {remove: failedNames})}) : null) : null,
        el("div", {class: "acts"}, act)];
      box.append(...parts.filter(Boolean));
      return box;
    }
    async function call(route, tid, extra = {}) {
      try { await api("/api/lab/" + route, {folder: M.folder, task: tid, ...extra}); say(""); }
      catch (e) { say(e.message, "warn"); }
      await poll(true);
    }
    function renderTasks() {
      const seen = new Set();
      for (const t of st.tasks) {
        seen.add(t.id);
        const live = t.state === "running" || t.state === "queued";
        if (live && !eta.has(t.id)) eta.set(t.id, {t0: Date.now(), done0: t.done});
        if (!live) eta.delete(t.id);
        const s = JSON.stringify([t, st.current && st.current.task === t.id ? [st.current.name, st.current.stage, st.current.step] : 0, etaText(t)]);
        let c = cards.get(t.id);
        if (!c || c.s !== s) {
          const node = card(t);
          if (c) c.node.replaceWith(node); else list.append(node);
          cards.set(t.id, {node, s});
        }
      }
      for (const [id, c] of cards) if (!seen.has(id)) { c.node.remove(); cards.delete(id); }
      for (const n of [...list.children]) if (n.classList.contains("empty")) n.remove();
      if (!st.tasks.length) list.append(el("div", {class: "empty", text: "No tasks yet. Pick pictures on the left and make one."}));
      if (st.error) say(st.error, "warn");
    }

    // ---- keeping up with the server ----------------------------------------------------------------------------------------
    async function poll(force) {
      if (polling || !M.folder) return;
      polling = true;
      try {
        const r = await api(host.url("lab/state"));
        const s = JSON.stringify(r), first = !st;
        st = r;
        if (s !== sig || force === true) {
          sig = s;
          if (open) {
            renderTasks();
            if (first || ["cand", "wait", "task", "failed"].includes(show.value)) rebuild(); else paintAll();
            renderShow();
            syncResolve();
          }
        }
        host.onState(st);
      } catch (e) { /* the server is busy or gone: the next tick asks again */ }
      finally { polling = false; }
    }

    // ---- Resolve: look at a finished picture next to its original and decide ---------------------------------------------------
    // Regenerate stays on the picture; every other decision moves on to the next one that waits.
    const res = {open: false, name: "", busy: false, confirm: null, seen: ""};
    const ro = el("img", {class: "o", alt: "Original", draggable: "false"}), re = el("img", {class: "e", alt: "Enhanced", draggable: "false"});
    const rcut = el("div", {class: "cut", role: "slider", tabindex: "0", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "50",
      "aria-label": "Divider between the original (left) and the enhanced picture (right)"}, el("span"));
    const rfit = el("div", {class: "fit"}, ro, re, rcut);
    const rdiff = el("div", {class: "mk-diff"}, rfit);
    const rbg = el("img", {class: "bg", alt: ""}), rwhat = el("div", {class: "what"}), rline = el("div", {class: "mk-enh-line"});
    const rbarFill = el("i"), rbar = el("div", {class: "mk-enh-bar wait"}, rbarFill);
    const rprog = el("div", {class: "mk-enhov", hidden: true}, rbg, el("div", {class: "box"}, rwhat, rbar, rline));
    const rstage = el("div", {class: "rstage"}, rdiff, rprog);
    const rtitle = el("span", {class: "rtitle"}), rmsg = el("div", {class: "field-hint rmsg"});
    const rclose = el("button", {type: "button", class: "btn btn-sm", text: "Back to the gallery", title: "Close this view (Esc); the results keep waiting", onclick: () => closeResolve()});
    const racts = el("div", {class: "racts"});
    const resNode = el("div", {class: "lab-res", hidden: true, role: "dialog", "aria-label": "Decide about an enhanced picture"},
      el("div", {class: "rtop"}, rtitle, rclose), rstage, rmsg, racts);
    view.append(resNode);

    const setCut = (pct) => {
      pct = Math.max(0, Math.min(100, pct));
      rfit.style.setProperty("--cut", pct + "%");
      rcut.setAttribute("aria-valuenow", String(Math.round(pct)));
    };
    function placeDiff() {
      const r = rdiff.getBoundingClientRect(), w = ro.naturalWidth, h = ro.naturalHeight;
      if (!w || !h || r.width < 40) return;
      const k = Math.min((r.width - 24) / w, (r.height - 24) / h);
      rfit.style.width = Math.max(40, Math.floor(w * k)) + "px";
      rfit.style.height = Math.max(40, Math.floor(h * k)) + "px";
    }
    new ResizeObserver(placeDiff).observe(rdiff);
    ro.addEventListener("load", placeDiff);
    const cutAt = (e) => { const r = rfit.getBoundingClientRect(); setCut(100 * (e.clientX - r.left) / r.width); };
    rfit.addEventListener("pointerdown", (e) => { if (e.button !== 0) return; rfit.setPointerCapture(e.pointerId); cutAt(e); e.preventDefault(); });
    rfit.addEventListener("pointermove", (e) => { if (rfit.hasPointerCapture(e.pointerId)) cutAt(e); });
    rcut.addEventListener("keydown", (e) => {
      const now = Number(rcut.getAttribute("aria-valuenow") || 50), d = e.shiftKey ? 10 : 2;
      if (e.key === "ArrowLeft") { setCut(now - d); e.preventDefault(); } else if (e.key === "ArrowRight") { setCut(now + d); e.preventDefault(); }
    });

    const waitingNames = () => (st ? st.pending.map((p) => p.name) : []);
    const entryOf = (name) => (st ? st.pending.find((p) => p.name === name) : null);
    const regenNow = () => !!(st && st.current && st.current.regen && st.current.name === res.name);

    function openResolve(name) {
      if (!entryOf(name)) return;
      res.open = true; res.confirm = null; res.busy = false;
      resNode.hidden = false;
      showResult(name);
    }
    function closeResolve() { res.open = false; resNode.hidden = true; res.name = ""; res.seen = ""; }
    function showResult(name) {
      res.name = name; res.confirm = null;
      const p = entryOf(name);
      ro.src = host.url("view", {name, edge: 2000});
      re.src = host.url("lab/result", {name, v: p ? p.made_at : 0});
      res.seen = String(p ? p.made_at : "");
      setCut(50);
      rmsg.textContent = ""; rmsg.className = "field-hint rmsg";
      drawActions();
    }
    function note(text, kind = "", action = null) {
      rmsg.className = "field-hint rmsg" + (kind ? " " + kind : "");
      rmsg.textContent = text || "";
      if (action) rmsg.append(" ", el("button", {type: "button", class: "btn btn-sm", text: action.label, onclick: action.run}));
    }
    function nextAfter(was) {                                       // the waiting picture that took the decided one's place
      const now = waitingNames(), i = was.index;
      return now[Math.min(i, now.length - 1)] || null;
    }
    async function decide(action, extra = {}) {
      const was = {index: Math.max(0, waitingNames().indexOf(res.name))}, name = res.name;
      res.busy = true; drawActions();
      let r;
      try { r = await api("/api/lab/resolve", {folder: M.folder, name, action, ...extra}); }
      catch (e) { res.busy = false; res.confirm = null; drawActions(); note(e.message, "warn"); return; }
      res.busy = false;
      if (r.stale) { res.confirm = "stale"; drawActions(); return; }
      await poll(true);
      await host.refresh();
      host.forget(name); if (r.name) host.forget(r.name);
      const next = nextAfter(was);
      if (next) showResult(next); else closeResolve();
      reportDone(action, r, name);
    }
    function reportDone(action, r, name) {
      if (action === "discard") say(`Discarded the result for ${name}.`, "ok-note");
      else if (action === "save") say(`Saved ✓  ${r.name}  (${r.size[0]}×${r.size[1]})${r.notes && r.notes.length ? " - " + r.notes.join("; ") : ""}`, r.notes && r.notes.length ? "warn" : "ok-note");
      else if (action === "replace") {
        say(`Replaced ✓  ${r.name}  (${r.size[0]}×${r.size[1]}); the original is kept.`, "ok-note", {label: "Undo", run: async () => {
          try {
            await api("/api/enhance/undo", {folder: M.folder, name: r.name, backup: r.backup, old: r.old, was_enhanced: r.was_enhanced});
            host.forget(r.name); await host.refresh();
            say("The original is back (the result was already discarded).", "ok-note");
          } catch (e) { say(`Undo failed: ${e.message}`, "warn"); }
        }});
      } else if (action === "delete") {
        const undo = r.mode === "soft" && r.moved && r.moved.length ? {label: "Undo", run: async () => {
          try { await api("/api/undelete", {folder: M.folder, moved: r.moved}); await host.refresh(); say("The picture is back.", "ok-note"); }
          catch (e) { say(`Undo failed: ${e.message}`, "warn"); }
        }} : null;
        say(r.mode === "soft" ? `${name} moved to the removed folder.` : `${name} erased for good.`, "ok-note", undo);
      }
    }
    async function regenerate() {
      res.busy = true; drawActions();
      try { await api("/api/lab/regenerate", {folder: M.folder, name: res.name}); note(""); }
      catch (e) { note(e.message, "warn"); }
      res.busy = false;
      await poll(true);
    }
    async function stopRegen() {
      if (st && st.current) try { await api("/api/lab/stop", {folder: M.folder, task: st.current.task}); } catch (e) { note(e.message, "warn"); }
      await poll(true);
    }
    const hardMode = () => !!(M.settings && M.settings.delete_mode === "hard");

    function drawActions() {
      const p = entryOf(res.name), list = waitingNames(), i = list.indexOf(res.name);
      rtitle.textContent = p ? `${res.name} · ${i + 1} of ${list.length} waiting${p.stale ? " · the original changed since" : ""}` : res.name;
      const btn = (label, cls, title, run, off) => el("button", {type: "button", class: "btn " + cls, text: label, title: off || title, disabled: !!off, onclick: run});
      if (regenNow()) {
        racts.replaceChildren(btn("Stop", "btn-danger", "Stop making the picture again", stopRegen));
        return;
      }
      if (res.confirm === "stale") {
        note("The original picture was changed after this result was made. Replacing it now replaces the changed picture.", "warn");
        racts.replaceChildren(btn("Replace anyway", "btn-primary", "Replace the changed picture; the original is kept", () => decide("replace", {force: true})),
          btn("Cancel", "", "Leave it as it is", () => { res.confirm = null; note(""); drawActions(); }));
        return;
      }
      if (res.confirm === "hard") {
        note("Erase the original picture and its caption for good? This has no Undo.", "warn");
        racts.replaceChildren(btn("Erase for good", "btn-danger", "Delete the original picture and its caption", () => decide("delete")),
          btn("Cancel", "", "Keep the picture", () => { res.confirm = null; note(""); drawActions(); }));
        return;
      }
      const off = res.busy ? "Wait a moment" : "";
      const gone = p && p.gone ? "The original picture isn't there any more" : "";
      const why = off || (st && st.running ? "Regenerate will be enabled after all tasks are done or stopped" : "");
      racts.replaceChildren(
        btn("Regenerate", "", "Make this picture again with a new seed (the same words)", regenerate, why),
        btn("Discard", "", "Throw the result away", () => decide("discard"), off),
        btn("Save as new", "", "Save the result as a new picture next to the original, named and captioned like a crop", () => decide("save"), off || gone),
        btn("Replace", "btn-primary", "The result takes the original's place; the original is kept and Undo brings it back", () => decide("replace"), off || gone),
        btn("Delete as unfixable", "btn-danger", hardMode() ? "Erase the original: it can't be made better (asks first)" : "Move the original to the removed folder: it can't be made better",
          () => { if (hardMode()) { res.confirm = "hard"; drawActions(); } else decide("delete"); }, off || gone));
    }

    // follows the server: a decided picture moves on; a regenerating one shows progress and then the new result
    function syncResolve() {
      if (!res.open) return;
      const p = entryOf(res.name), regen = regenNow();
      if (!p && !regen) {
        const was = waitingNames()[0];
        if (was) showResult(was); else closeResolve();
        return;
      }
      if (regen) {
        const c = st.current;
        rprog.hidden = false; rdiff.hidden = true;
        rbg.src = host.url("view", {name: res.name, edge: 800});
        rwhat.textContent = c.stage + (c.steps ? `  (step ${c.step} of ${c.steps})` : "");
        rbar.classList.toggle("wait", c.percent === null);
        rbarFill.style.width = c.percent === null ? "" : c.percent + "%";
        rline.textContent = `${Math.round(c.elapsed)} s`;
        if (c.preview && String(c.preview) !== res.preview) { res.preview = String(c.preview); rbg.src = "/api/lab/preview?v=" + res.preview; }
        drawActions();
        return;
      }
      rprog.hidden = true; rdiff.hidden = false;
      if (String(p.made_at) !== res.seen) { res.seen = String(p.made_at); re.src = host.url("lab/result", {name: res.name, v: p.made_at}); setCut(50); }
      if (!res.confirm) drawActions();
    }

    document.addEventListener("keydown", (e) => {
      if (!open || host.blocked()) return;
      if (res.open) {
        if (e.target instanceof Element && e.target.closest("textarea, input, select")) return;
        const list = waitingNames(), i = list.indexOf(res.name);
        if (e.key === "Escape") { e.preventDefault(); closeResolve(); }
        else if ((e.key === "PageUp" || e.key === "PageDown") && !regenNow()) {
          e.preventDefault();
          const to = list[i + (e.key === "PageUp" ? -1 : 1)];
          if (to && !e.repeat) showResult(to);
        }
        return;
      }
      if (e.target instanceof Element && e.target.closest("textarea, input, select, [contenteditable]")) return;
      if ((e.ctrlKey || e.metaKey) && e.code === "KeyA") { e.preventDefault(); pick(shown.map((r) => r[0]), true); }
      else if (e.key === "Escape") { picked = new Set(); picksChanged(); }
    });
    show.addEventListener("change", rebuild);
    sort.addEventListener("change", rebuild);

    return {
      get open() { return open; },
      get running() { return !!(st && st.running); },
      get state() { return st; },
      refreshRows() { renderShow(); if (open) rebuild(); },
      async enter() {
        open = true; view.hidden = false;
        if (M.folder !== lastFolder) { lastFolder = M.folder; picked = new Set(); st = null; sig = ""; facts = {}; cards.clear(); list.replaceChildren(); say(""); }
        renderShow(); rebuild(); renderMake();
        await poll(true);
        loadFacts();
        clearInterval(timer);
        timer = setInterval(poll, 1000);
      },
      leave() { closeResolve(); open = false; view.hidden = true; clearInterval(timer); clearTimeout(factsTimer); },
      poll,
    };
  }
  return {create};
})();
