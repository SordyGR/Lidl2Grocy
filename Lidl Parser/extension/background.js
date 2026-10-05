let cancelFlag = false;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const HIST = (n) => `https://www.lidl-hellas.gr/mre/purchase-history?client_id=GreeceRetailClient&country_code=gr&language=el-GR&page=${n}`;
const DETAIL = (t) => `https://www.lidl-hellas.gr/mre/purchase-detail?client_id=GreeceRetailClient&country_code=gr&language=el-GR&t=${encodeURIComponent(t)}`;

function progress(text, done = false) {
  browser.runtime.sendMessage({type: "progress", text, done}).catch(() => {});
}

// Open url in a background tab, resolve with the content script's message.
// The sweep awaits one page at a time, so the first matching message wins.
function loadInTab(url, want, timeoutMs = 30000) {
  return new Promise(async (resolve) => {
    const tab = await browser.tabs.create({url, active: false});
    const done = async (m) => {
      clearTimeout(timer);
      browser.tabs.onRemoved.removeListener(onGone);
      browser.runtime.onMessage.removeListener(onMsg);
      try { await browser.tabs.remove(tab.id); } catch (_) {}
      resolve(m);
    };
    const timer = setTimeout(() => done({timeout: true}), timeoutMs);
    function onGone(id) {
      if (id === tab.id) done({closed: true});
    }
    function onMsg(m) {
      if (m.type !== want) return;
      done(m);
    }
    browser.tabs.onRemoved.addListener(onGone);
    browser.runtime.onMessage.addListener(onMsg);
  });
}

async function postJSON(base, token, path, body) {
  const r = await fetch(base + path, {
    method: "POST",
    headers: {"Content-Type": "application/json", "Authorization": "Bearer " + token},
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json();
}

// Receipt tid from an open purchase-detail URL (?t=...), or null.
function tidFromUrl(url) {
  const m = /[?&]t=([\w-]+)/.exec(url || "");
  return m ? m[1] : null;
}

// Capture the receipt open in the current tab and send it to the server.
// Reuses the sweep's diff-then-fetch chain, so reruns are duplicates, not doubles.
async function sendActive() {
  const s = await browser.storage.local.get({serverUrl: "", token: ""});
  const base = (s.serverUrl || "").replace(/\/$/, "");
  if (!base || !s.token) {
    progress("Set the server URL and token first (token: dashboard Settings → Extension).", true);
    return;
  }
  const tabs = await browser.tabs.query({active: true, currentWindow: true});
  const tab = tabs[0];
  const tid = tidFromUrl(tab ? tab.url : "");
  if (!tab || !tid) {
    progress("Open a Lidl receipt (purchase-detail page) in the current tab first, then Send again.", true);
    return;
  }
  progress("Reading the open receipt…");
  const d = await new Promise((resolve) => {
    const timer = setTimeout(() => resolve({timeout: true}), 20000);
    function onMsg(m) {
      if (!m || m.type !== "activeDetail") return;
      clearTimeout(timer);
      browser.runtime.onMessage.removeListener(onMsg);
      resolve(m);
    }
    browser.runtime.onMessage.addListener(onMsg);
    browser.tabs.sendMessage(tab.id, {type: "grab"}).catch(() => resolve({timeout: true}));
  });
  if (!d.text) {
    progress("Could not read this receipt (page not ready or logged out) — reload it and retry.", true);
    return;
  }
  try {
    const need = await postJSON(base, s.token, "/api/ext/need", {tids: [tid]});
    if (!need.new || !need.new.length) {
      progress("Already on the server (duplicate) — nothing to do. Review on the dashboard.", true);
      return;
    }
    const r = await postJSON(base, s.token, "/api/ext/receipt", {tid, text: d.text});
    if (r.status === "duplicate") progress("Already on the server (duplicate) — nothing to do. Review on the dashboard.", true);
    else if (r.status === "imported") progress(`Sent: ${r.items} item(s), ${r.total} €. Review on the dashboard.`, true);
    else progress(`Server refused it (${r.error || r.status}).`, true);
  } catch (e) {
    progress(`Server unreachable (${e.message}). Check the URL; container running?`, true);
  }
}

async function sweep(fullScan) {
  cancelFlag = false;
  const s = await browser.storage.local.get({serverUrl: "", token: "", pageCap: 5});
  const base = (s.serverUrl || "").replace(/\/$/, "");
  if (!base || !s.token) {
    progress("Set the server URL and token first (token: dashboard Settings → Extension).", true);
    return;
  }
  try {
    const ping = await fetch(base + "/api/ext/status",
      {headers: {"Authorization": "Bearer " + s.token}});
    if (ping.status === 401) { progress("Token rejected — copy it from Settings → Extension.", true); return; }
    if (!ping.ok) throw new Error("HTTP " + ping.status);
  } catch (e) {
    progress(`Server unreachable (${e.message}). Check the URL; container running? HTTPS with a self-signed cert needs a one-time browser exception.`, true);
    return;
  }
  const cap = fullScan ? 100 : Math.max(1, Math.min(100, s.pageCap || 5));
  const seen = new Set();
  const tids = [];
  let page = 0;
  while (page < cap && !cancelFlag) {
    page += 1;
    progress(`History page ${page}… (${tids.length} tids so far)`);
    const m = await loadInTab(HIST(page), "history");
    if (m.loggedOut || m.closed) { progress("Log into lidl-hellas.gr in this browser first, then Sync again.", true); return; }
    if (m.timeout) { progress(`Stopped at page ${page} (Lidl did not answer) — rerun later; resume is automatic.`, true); return; }
    let foundNew = false;
    for (const t of m.tids || []) {
      if (!seen.has(t)) { seen.add(t); tids.push(t); foundNew = true; }
    }
    if (!foundNew) break;
    if (!fullScan && page >= cap) break;
    if (fullScan && !m.hasNext) break;
    if (fullScan) await sleep(1500 + Math.random() * 1500);
  }
  if (cancelFlag) { progress("Cancelled.", true); return; }
  if (!tids.length) { progress("No purchase links found. If you are logged in, Lidl may have changed its pages — paste fallback still works on the dashboard.", true); return; }
  // diff in chunks of 200
  let fresh = [];
  for (let i = 0; i < tids.length; i += 200) {
    const r = await postJSON(base, s.token, "/api/ext/need", {tids: tids.slice(i, i + 200)});
    fresh = fresh.concat(r.new);
  }
  const skipped = tids.length - fresh.length;
  let done = 0, dups = 0;
  const failed = [];
  for (const t of fresh) {
    if (cancelFlag) break;
    progress(`Receipt ${done + 1}/${fresh.length} (skipped ${skipped})…`);
    const d = await loadInTab(DETAIL(t), "detail");
    if (d.loggedOut || d.closed || d.timeout || !d.text) {
      failed.push(t);
    } else {
      try {
        const r = await postJSON(base, s.token, "/api/ext/receipt", {tid: t, text: d.text});
        if (r.status === "duplicate") dups += 1;
        else if (r.status !== "imported") failed.push(t);
      } catch (e) { failed.push(t); }
    }
    done += 1;
    if (fullScan) await sleep(1500 + Math.random() * 1500);
  }
  const ok = done - failed.length - dups;
  const tail = failed.length ? ` (${failed.slice(0, 3).join(", ")}${failed.length > 3 ? "…" : ""}) — rerun to retry` : "";
  progress(`Done: ${ok} fetched • ${skipped + dups} skipped • ${failed.length} failed${tail}. Review on the dashboard.`, true);
}

browser.runtime.onMessage.addListener((m) => {
  if (m.type === "sync") sweep(!!m.fullScan);
  if (m.type === "sendActive") sendActive();
  if (m.type === "cancel") cancelFlag = true;
});
