// The few helpers the page uses: element builder, JSON calls to the server, debounce, and the open folder.
"use strict";
const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c);
  return e;
};
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

// the session key travels in a cookie the server set on the first visit, so a call is just a fetch
async function api(path, body) {
  const opt = {};
  if (body !== undefined) {
    opt.method = "POST";
    opt.headers = {"Content-Type": "application/json"};
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || (r.status === 401 ? "The session key is missing: open MonKap with the address it printed." : r.statusText));
  return data;
}

const S = {imageFolder: ""};                 // the folder open now
