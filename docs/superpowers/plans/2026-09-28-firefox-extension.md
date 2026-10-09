# Firefox Extension Scraper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace server-side Lidl scraping with a Firefox extension + token-gated ingest API on Gamma.

**Architecture:** Flask gains `POST /api/ext/need`, `POST /api/ext/receipt`, `GET /api/ext/status` (Bearer `EXT_INGEST_TOKEN`, auto-generated); all Playwright/login code, routes, templates, and Docker browser layers are deleted; a Manifest-V2 extension scrapes history + details in the user's real session with sweep precautions.

**Tech Stack:** Flask 3 + SQLite (server, pytest), vanilla JS MV2 extension (no build, no JS tests — manual acceptance).

**Spec:** `docs/superpowers/specs/2026-09-28-firefox-extension-design.md` — the plan argues from the spec; executors read both. NOTE: this plan refines two spec lines — `status` returns `{ok,last_sync,receipts}` (no `needs_review`; the extension owns failure display, Task 2 updates the spec), and tab-based extraction is primary (both Lidl pages render via JS — proven by the current `wait_for_selector` calls — so `fetch()` cannot see the content; Task 6 updates the spec).

## Global Constraints

- Tests run from `Lidl Parser/` via `python -m pytest -q`; suite must be green after every task.
- Work on `Gamma` only; local commits per task, never push (user pushes).
- Token travels in `Authorization: Bearer` header only; never log it, never put it in URLs.
- Extension requests to Lidl are sequential, never parallel; full scans throttle 1.5–3 s.
- Nothing auto-pushes to Grocy; review flow is unchanged.
- Both dashboard skins (`dashboard.html`, `dashboard_v2.html`) stay in sync when edited.

## Review Focus

- Oversized/malformed JSON (`tids` > 5000, non-list, 10 MB text) → 400/413, never 500 (Task 2 tests).
- Double Sync / retry POSTs same tid → idempotent `duplicate`, exactly one receipt row (Task 2 test).
- Token in logs or error bodies → assert 401 bodies contain no token (Task 1 test).
- Lidl layout change → unparseable text yields 422, tid NOT recorded, retry later (Task 2 test).
- Remote/VPN self-signed HTTPS → README documents the one-time browser exception; acceptance covers it (Task 7 checklist).

---

### Task 1: Ingest token (generate, persist, gate)

**Files:**
- Modify: `Lidl Parser/settings_store.py` (add key)
- Modify: `Lidl Parser/app.py` (imports + two helpers)
- Create: `Lidl Parser/tests/test_ext_ingest.py` (token tests first; endpoints added in Task 2)

**Interfaces:**
- Consumes: `app_settings.get/set` (existing).
- Produces: `_ext_token() -> str`, `_ext_authorized() -> bool` (used by Task 2 routes and Task 3 settings page).

- [ ] **Step 1: Write the failing tests.** Create `Lidl Parser/tests/test_ext_ingest.py`:

```python
import db as dbmod


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "ext.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def test_token_autogenerates_and_persists(tmp_path, monkeypatch):
    import app as appmod
    _client(tmp_path, monkeypatch)
    t1 = appmod._ext_token()
    assert len(t1) >= 32
    assert appmod._ext_token() == t1  # second call reads DB, no regen


def test_auth_gate(tmp_path, monkeypatch):
    import app as appmod
    c = _client(tmp_path, monkeypatch)
    tok = appmod._ext_token()
    assert c.get("/api/ext/status").status_code == 401
    assert c.get("/api/ext/status",
                 headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/api/ext/status", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert tok not in r.get_data(as_text=True)
```

- [ ] **Step 2: Run to verify failure.**

Run: `python -m pytest tests/test_ext_ingest.py -q` from `Lidl Parser/`
Expected: FAIL (`/api/ext/status` 404 — route does not exist yet; `_ext_token` undefined).

- [ ] **Step 3: Implement.** In `settings_store.py`, add to `KNOWN_KEYS`: `"EXT_INGEST_TOKEN"`. In `app.py`, add `import secrets`, `import hmac` to the stdlib imports, then after the `_FETCH`/`_LOGIN` globals:

```python
def _ext_token():
    tok = app_settings.get("EXT_INGEST_TOKEN")
    if not tok:
        tok = secrets.token_urlsafe(32)
        app_settings.set("EXT_INGEST_TOKEN", tok)
    return tok


def _ext_authorized():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return hmac.compare_digest(auth[len("Bearer "):], _ext_token())
```

Also add a minimal `GET /api/ext/status` stub now (full body in Task 2) so the gate test passes:

```python
@app.route("/api/ext/status")
def api_ext_status():
    if not _ext_authorized():
        return jsonify({"error": "bad or missing token"}), 401
    return jsonify({"ok": True, "last_sync": None, "receipts": 0})
```

- [ ] **Step 4: Run tests.**

Run: `python -m pytest tests/test_ext_ingest.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit.**

```bash
git add "Lidl Parser/settings_store.py" "Lidl Parser/app.py" "Lidl Parser/tests/test_ext_ingest.py"
git commit -m "feat: extension ingest token with lazy generation and auth gate"
```

---

### Task 2: Ingest endpoints (need / receipt / status) + CORS

**Files:**
- Modify: `Lidl Parser/app.py` (CORS hook, three routes, top-level `from fetch_lib import ...`)
- Modify: `Lidl Parser/tests/test_ext_ingest.py` (endpoint tests)
- Modify: `docs/superpowers/specs/2026-09-28-firefox-extension-design.md` (status contract)

**Interfaces:**
- Consumes: `_ext_token/_ext_authorized` (Task 1), `get_db`, `fetch_state` table, `trim_receipt_text/receipt_body_date/pick_filename/import_kept_receipt` from `fetch_lib`, existing receipt-text fixture approach from `tests/test_fetch_import.py`.
- Produces: HTTP contract the extension (Tasks 5–6) codes against — freeze it here:
  - `POST /api/ext/need {tids:[str]} → 200 {new:[str]}`; 400 on malformed; 401 on bad token.
  - `POST /api/ext/receipt {tid:str, text:str} → 200 {status:imported|duplicate, ...}`; 400 on missing fields; 422 on unparseable (tid NOT recorded); 401 on bad token.
  - `GET /api/ext/status → 200 {ok,last_sync,receipts}`; 401 on bad token.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_ext_ingest.py`:

```python
def _authed(c, tok):
    return {"Authorization": f"Bearer {tok}"}


def test_need_diffs_against_fetch_state(tmp_path, monkeypatch):
    import app as appmod
    c = _client(tmp_path, monkeypatch)
    tok = appmod._ext_token()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO fetch_state (t_id, filename) VALUES (?, ?)", ("old1", "f.txt"))
    conn.commit()
    conn.close()
    r = c.post("/api/ext/need", json={"tids": ["old1", "new1", "new1"]}, headers=_authed(c, tok))
    assert r.status_code == 200
    assert r.get_json() == {"new": ["new1"]}


def test_need_rejects_garbage(tmp_path, monkeypatch):
    import app as appmod
    c = _client(tmp_path, monkeypatch)
    tok = appmod._ext_token()
    h = _authed(c, tok)
    assert c.post("/api/ext/need", json={"tids": "nope"}, headers=h).status_code == 400
    assert c.post("/api/ext/need", json={"tids": ["x"] * 5001}, headers=h).status_code == 400
    assert c.post("/api/ext/need", json={}, headers=h).status_code == 400
    assert c.post("/api/ext/need", json={"tids": ["a"]}).status_code == 401


def test_receipt_import_duplicate_and_422(tmp_path, monkeypatch):
    import app as appmod
    c = _client(tmp_path, monkeypatch)
    tok = appmod._ext_token()
    h = _authed(c, tok)
    assert c.post("/api/ext/receipt", json={"tid": "t1"}, headers=h).status_code == 400
    # forced parse failure must not record state (retry later)
    monkeypatch.setattr("fetch_lib.trim_receipt_text", lambda text: (_ for _ in ()).throw(ValueError("no date")))
    r = c.post("/api/ext/receipt", json={"tid": "bad1", "text": "garbage"}, headers=h)
    assert r.status_code == 422
    conn = dbmod.get_db()
    assert conn.execute("SELECT 1 FROM fetch_state WHERE t_id='bad1'").fetchone() is None
    conn.close()


def test_receipt_end_to_end_with_fixture(tmp_path, monkeypatch):
    # Reuse the receipt-text approach from tests/test_fetch_import.py; assert
    # imported → duplicate is idempotent and adds exactly one receipts row.
    import app as appmod
    from test_fetch_import import _sample_text  # helper already in repo; else inline fixture
    c = _client(tmp_path, monkeypatch)
    tok = appmod._ext_token()
    h = _authed(c, tok)
    r1 = c.post("/api/ext/receipt", json={"tid": "e2e1", "text": _sample_text()}, headers=h)
    assert r1.get_json()["status"] == "imported"
    r2 = c.post("/api/ext/receipt", json={"tid": "e2e1", "text": _sample_text()}, headers=h)
    assert r2.get_json()["status"] == "duplicate"
    conn = dbmod.get_db()
    assert conn.execute("SELECT COUNT(*) AS c FROM receipts").fetchone()["c"] == 1
    conn.close()
    s = c.get("/api/ext/status", headers=h).get_json()
    assert s["ok"] is True and s["receipts"] == 1 and s["last_sync"] is not None
```

Note: read `tests/test_fetch_import.py` first; if it has no `_sample_text` helper, inline the smallest receipt text that test uses instead of importing.

- [ ] **Step 2: Run to verify failure.**

Run: `python -m pytest tests/test_ext_ingest.py -q`
Expected: FAIL (`/api/ext/need` 404, status stub shape mismatch).

- [ ] **Step 3: Implement.** In `app.py` add top-level import `from fetch_lib import (trim_receipt_text, receipt_body_date, pick_filename, import_kept_receipt)`, define `RECEIPT_DIR = os.path.join(BASE_DIR, "receipts")`, add the CORS hook, replace the status stub, and add the two POST routes:

```python
@app.after_request
def _ext_cors(resp):
    if request.path.startswith("/api/ext/"):
        resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/ext/need", methods=["POST"])
def api_ext_need():
    if not _ext_authorized():
        return jsonify({"error": "bad or missing token"}), 401
    data = request.get_json(force=True, silent=True) or {}
    tids = data.get("tids")
    if (not isinstance(tids, list) or len(tids) > 5000
            or not all(isinstance(t, str) for t in tids)):
        return jsonify({"error": "tids must be a list of at most 5000 strings"}), 400
    ordered = list(dict.fromkeys(tids))
    conn = get_db()
    try:
        have = {r[0] for r in conn.execute("SELECT t_id FROM fetch_state").fetchall()}
    finally:
        conn.close()
    return jsonify({"new": [t for t in ordered if t not in have]})
```

```python
@app.route("/api/ext/receipt", methods=["POST"])
def api_ext_receipt():
    if not _ext_authorized():
        return jsonify({"error": "bad or missing token"}), 401
    data = request.get_json(force=True, silent=True) or {}
    tid = data.get("tid")
    text = data.get("text", "")
    if not tid or not isinstance(tid, str) or not isinstance(text, str) or not text.strip():
        return jsonify({"error": "tid and non-empty text required"}), 400
    conn = get_db()
    try:
        if conn.execute("SELECT 1 FROM fetch_state WHERE t_id=?", (tid,)).fetchone():
            return jsonify({"status": "duplicate", "tid": tid})
        try:
            kept = trim_receipt_text(text)
            yy, mm, dd = receipt_body_date(kept)
            name = pick_filename(RECEIPT_DIR, conn, yy, mm, dd)
            res = import_kept_receipt(conn, kept, name, tid)
        except Exception as e:
            return jsonify({"status": "error", "tid": tid, "error": str(e)[:200]}), 422
        os.makedirs(RECEIPT_DIR, exist_ok=True)
        with open(os.path.join(RECEIPT_DIR, name), "w", encoding="utf-8") as f:
            f.write(kept)
        return jsonify({"status": "imported", "tid": tid,
                        "items": res["items"], "total": res["total"]})
    finally:
        conn.close()
```

```python
@app.route("/api/ext/status")
def api_ext_status():
    if not _ext_authorized():
        return jsonify({"error": "bad or missing token"}), 401
    conn = get_db()
    try:
        last = conn.execute("SELECT MAX(fetched_at) AS at FROM fetch_state").fetchone()["at"]
        n = conn.execute("SELECT COUNT(*) AS c FROM receipts").fetchone()["c"]
    finally:
        conn.close()
    return jsonify({"ok": True, "last_sync": last, "receipts": n})
```

Then update the spec: in §3 change the status line to `` `GET /api/ext/status` `{ok,last_sync,receipts}` (failure display lives in the extension popup; failed tids are never recorded so the next `need` retries them)`` and in §6 change "`status` exposes `needs_review`" to "`status` exposes `last_sync` + receipt count; per-tid failures are shown by the extension".

- [ ] **Step 4: Run tests.**

Run: `python -m pytest tests/test_ext_ingest.py -q`, then full `python -m pytest -q`
Expected: new file passes; full suite still green (old routes untouched so far).

- [ ] **Step 5: Commit.**

```bash
git add "Lidl Parser/app.py" "Lidl Parser/tests/test_ext_ingest.py" docs/superpowers/specs/2026-09-28-firefox-extension-design.md
git commit -m "feat: extension ingest API (need/receipt/status) with token gate"
```

---

### Task 3: Settings Extension section, drop Lidl credentials

**Files:**
- Modify: `Lidl Parser/app.py` (`settings()` + new `settings_ext_regenerate`, delete `settings_clear_lidl_pass` — locate via `grep -n settings_clear_lidl_pass app.py`)
- Modify: `Lidl Parser/templates/settings.html`
- Modify: `Lidl Parser/tests/test_settings.py`, `Lidl Parser/tests/test_settings_ui.py` (only what asserts removed fields)

**Interfaces:**
- Consumes: `_ext_token()` (Task 1).
- Produces: `vals["EXT_INGEST_TOKEN"]` for the template; `POST /settings/ext-regenerate`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_settings_ui.py` add:

```python
def test_settings_shows_extension_token_and_regenerates(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    body = c.get("/settings").get_data(as_text=True)
    assert "EXT_INGEST_TOKEN" not in body  # token value shown, key name is not UI copy
    assert "Extension" in body
    assert "LIDL_PASS" not in body and "lidl-profile" not in body
    old = appmod._ext_token()
    assert old in body
    r = c.post("/settings/ext-regenerate", follow_redirects=True)
    assert r.status_code == 200
    assert appmod._ext_token() != old
```

Check first how `test_settings_ui.py` builds its client and mirror it (the sketch above assumes the dbmod pattern; adapt names, keep assertions).

- [ ] **Step 2: Run to verify failure.**

Run: `python -m pytest tests/test_settings_ui.py -q`
Expected: FAIL (no "Extension" section).

- [ ] **Step 3: Implement.** In `settings()`: delete the `LIDL_USER` set, the `LIDL_PASS` block, the `LIDL_PASS_SET` lookup, and the `secret_store` import; change vals to `{k: app_settings.get(k) for k in ("GROCY_URL", "GROCY_API_KEY")}` plus `vals["EXT_INGEST_TOKEN"] = _ext_token()`; change the flash to `"Settings saved"`. Add:

```python
@app.route("/settings/ext-regenerate", methods=["POST"])
def settings_ext_regenerate():
    app_settings.set("EXT_INGEST_TOKEN", secrets.token_urlsafe(32))
    flash("Extension token regenerated — update the Firefox extension")
    return redirect(url_for("settings"))
```

Delete the `settings_clear_lidl_pass` route. In `settings.html`: replace the whole `<h2>Lidl</h2>` block (both labels) and the profile/password `<p><small>` note plus the clear-password form with:

```html
<h2>Extension (Firefox)</h2>
<p><small>Receipts arrive via the Firefox extension using this token. Server-side Lidl login is removed.</small></p>
<label>Token <input type="text" readonly value="{{ v['EXT_INGEST_TOKEN'] }}" onclick="this.select()"></label>
<button type="button" onclick="navigator.clipboard.writeText(this.previousElementSibling.value)">Copy</button>
<form method="post" action="{{ url_for('settings_ext_regenerate') }}"><button>Regenerate token</button></form>
```

Fix any other failing assertions about removed Lidl fields in `test_settings.py` / `test_settings_ui.py` (read the failures, keep Grocy/users/cards/units assertions intact).

- [ ] **Step 4: Run tests.**

Run: `python -m pytest tests/test_settings.py tests/test_settings_ui.py tests/test_ext_ingest.py -q`, then full suite.
Expected: green.

- [ ] **Step 5: Commit.**

```bash
git add "Lidl Parser/app.py" "Lidl Parser/templates/settings.html" "Lidl Parser/tests/test_settings.py" "Lidl Parser/tests/test_settings_ui.py"
git commit -m "feat: settings Extension token UI, remove Lidl credentials"
```

---

### Task 4: Delete server-side scraping

**Files:**
- Rewrite: `Lidl Parser/fetch_lidl.py` (paste-only; full content below)
- Modify: `Lidl Parser/app.py` (delete `_run_sync`, `/fetch/run`, `/api/fetch_status`, `/fetch/login`, `_FETCH/_LOGIN/_LOGIN_COOLDOWN_UNTIL/LOGIN_COOLDOWN_S`, `threading` import if orphaned, dashboard `real_login_url`/`container_browser` context)
- Modify: `Lidl Parser/templates/dashboard.html`, `dashboard_v2.html` (remove sync/login buttons + polling JS; keep paste form, pie, everything else)
- Delete: `Lidl Parser/templates/fetch_login.html`, `fetch_login_v2.html`
- Modify: `Lidl Parser/Dockerfile` (drop apt browser libs + `playwright install` line; keep `pip install` + `COPY` + gunicorn CMD)
- Modify: `Lidl Parser/requirements.txt` (drop `playwright` line)
- Delete: `Lidl Parser/secret_store.py` only if `grep -rn secret_store` shows no remaining source/test imports (else keep and note why in commit message)
- Tests: delete files covering only removed behavior; update mixed ones; suite green

**Interfaces:** none produced (pure deletion); `fetch_lidl.cmd_paste(path, t_id) -> dict` and `main()` (`--paste/--tid` only) keep exact signatures for `/fetch/paste` and `test_fetch_import.py`.

- [ ] **Step 1: Record the blast radius.** Run and save the list (do not edit yet):

```bash
grep -rln "fetch_lidl\|fetch/run\|fetch/login\|fetch_status\|_FETCH\|_LOGIN\|secret_store\|LIDL_PASS\|LIDL_USER\|lidl-profile\|container_browser\|real_login_url\|login-btn\|playwright" "Lidl Parser/app.py" "Lidl Parser/templates" "Lidl Parser/tests" "Lidl Parser/Dockerfile" "Lidl Parser/requirements.txt" 2>&1
python -m pytest -q 2>&1 | tail -3
```

- [ ] **Step 2: Rewrite `fetch_lidl.py` paste-only** (overwrite whole file):

```python
"""Paste-only receipt import (server-side Lidl scraping removed).

Receipts arrive via the Firefox extension ingest API or manual paste.
"""
import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from db import init_db, get_db
from fetch_lib import (trim_receipt_text, receipt_body_date,
                       pick_filename, import_kept_receipt)

RECEIPT_DIR = os.path.join(BASE_DIR, "receipts")


def cmd_paste(path: str, t_id) -> dict:
    init_db()
    with open(path, "r", encoding="utf-8") as f:
        full = f.read()
    kept = trim_receipt_text(full)
    yy, mm, dd = receipt_body_date(kept)
    conn = get_db()
    try:
        name = pick_filename(RECEIPT_DIR, conn, yy, mm, dd)
        res = import_kept_receipt(conn, kept, name, t_id)
    finally:
        conn.close()
    os.makedirs(RECEIPT_DIR, exist_ok=True)
    with open(os.path.join(RECEIPT_DIR, name), "w",
              encoding="utf-8") as f:
        f.write(kept)
    print(f"saved {name} ({res['items']} items, {res['total']:.2f} EUR)")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--paste", help="path to full Ctrl+A page text file")
    ap.add_argument("--tid", default=None)
    args = ap.parse_args(argv)
    if args.paste:
        cmd_paste(args.paste, args.tid)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Delete routes + UI + packaging.** In `app.py` delete `_run_sync`, `fetch_run`, `api_fetch_status`, `fetch_login`, the `_FETCH/_LOGIN/_LOGIN_COOLDOWN_UNTIL/LOGIN_COOLDOWN_S` globals, the `fetch_lidl.HISTORY_URL` / `container_browser` context in `dashboard()` (read lines 210–255 first; keep the paste/pie context), and any now-orphaned `threading`/`time` imports (verify with a pyflakes-equivalent: `python -c "import app"` must not warn-unused — unused imports are allowed to stay if shared, but removed names must not be referenced). In both dashboards remove the Sync button, Login button, `fetch/run` + `fetch/login` JS, and status polling; keep the paste form and pie controls byte-identical. Delete the two `fetch_login*.html` templates. Dockerfile: delete the `apt-get` RUN block lines for browser libs and the `python -m playwright install chromium --with-deps` line. `requirements.txt`: delete the `playwright` line.

- [ ] **Step 4: Fix the test fallout.** Run the suite; for each failure: if the test file covers only removed behavior (login, sync run, crawl, status polling), delete the file; if mixed (e.g. `test_user_reported_fixes.py`, dashboard tests), delete only the obsolete test functions. Candidates from the repo listing: `test_fetch_web_login.py`, `test_fetch_ui.py`, `test_fetch_auth.py`, `test_fetch_login_needed.py`, `test_fetch_crawl.py`, `test_fetch_cli.py` (rewrite if it covers `--paste`; delete if sync-only). Verify `test_fetch_import.py`, `test_fetch_lib.py` still pass (paste path untouched). Run: `python -m pytest -q` → all green.

- [ ] **Step 5: Commit.**

```bash
git add -A "Lidl Parser"
git commit -m "chore: remove server-side Lidl scraping (Playwright, login routes, browser packaging)"
```

Verify the commit contains no unintended files: `git show --stat HEAD`.

---

### Task 5: Extension scaffold + popup

**Files:**
- Create: `Lidl Parser/extension/manifest.json`, `popup.html`, `popup.js`

**Interfaces:**
- Consumes: HTTP contract from Task 2 (frozen).
- Produces: `storage.local` keys `{serverUrl, token, pageCap}`; runtime messages `{type:'sync'|'cancel'}` to background (Task 6); progress listener.

- [ ] **Step 1: Create `manifest.json`** (MV2, Firefox):

```json
{
  "manifest_version": 2,
  "name": "Lidl2Grocy Sync",
  "version": "1.0",
  "description": "Sync Lidl Hellas digital receipts to the Lidl2Grocy server.",
  "permissions": ["storage", "tabs", "*://*.lidl-hellas.gr/*", "http://*/*", "https://*/*"],
  "browser_action": {"default_popup": "popup.html", "default_title": "Lidl2Grocy Sync"},
  "background": {"scripts": ["background.js"], "persistent": false},
  "content_scripts": [
    {"matches": ["*://*.lidl-hellas.gr/mre/purchase-history*"], "js": ["content_history.js"], "run_at": "document_idle"},
    {"matches": ["*://*.lidl-hellas.gr/mre/purchase-detail*"], "js": ["content_detail.js"], "run_at": "document_idle"}
  ]
}
```

- [ ] **Step 2: Create `popup.html`.** URL + token + cap fields, full-scan checkbox, Sync/Cancel, status line:

```html
<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body{width:320px;font-family:sans-serif;font-size:13px;padding:8px}
label{display:block;margin:6px 0}input[type=text],input[type=number]{width:100%;box-sizing:border-box}
#status{white-space:pre-wrap;margin-top:8px;min-height:3em}
</style></head><body>
<label>Server URL <input type="text" id="serverUrl" placeholder="http://server:5090"></label>
<label>Token <input type="text" id="token" placeholder="paste from Settings → Extension"></label>
<label>Page cap <input type="number" id="pageCap" min="1" max="100" value="5"></label>
<label><input type="checkbox" id="fullScan"> Full history scan (first run / old account)</label>
<button id="sync">Sync</button>
<button id="cancel" disabled>Cancel</button>
<div id="status"></div>
<script src="popup.js"></script>
</body></html>
```

- [ ] **Step 3: Create `popup.js`.**

```js
const $ = (id) => document.getElementById(id);

async function load() {
  const s = await browser.storage.local.get({serverUrl: "", token: "", pageCap: 5});
  $("serverUrl").value = s.serverUrl;
  $("token").value = s.token;
  $("pageCap").value = s.pageCap;
}

async function save() {
  await browser.storage.local.set({
    serverUrl: $("serverUrl").value.trim().replace(/\/$/, ""),
    token: $("token").value.trim(),
    pageCap: Math.max(1, Math.min(100, parseInt($("pageCap").value, 10) || 5)),
  });
}

$("sync").addEventListener("click", async () => {
  await save();
  $("sync").disabled = true;
  $("cancel").disabled = false;
  $("status").textContent = "Starting…";
  await browser.runtime.sendMessage({type: "sync", fullScan: $("fullScan").checked});
});

$("cancel").addEventListener("click", async () => {
  await browser.runtime.sendMessage({type: "cancel"});
});

browser.runtime.onMessage.addListener((m) => {
  if (m.type === "progress") {
    $("status").textContent = m.text;
    if (m.done) { $("sync").disabled = false; $("cancel").disabled = true; }
  }
});

load();
```

- [ ] **Step 4: Smoke-check.** Sideload in Firefox (`about:debugging → This Firefox → Load Temporary Add-on → manifest.json`): popup opens, fields persist across close/reopen, Sync sends the message (background does not exist yet — expect nothing; this step only verifies the popup loads without console errors).

- [ ] **Step 5: Commit.**

```bash
git add "Lidl Parser/extension/manifest.json" "Lidl Parser/extension/popup.html" "Lidl Parser/extension/popup.js"
git commit -m "feat: extension scaffold with settings popup"
```

---

### Task 6: Sweep engine (background + content scripts)

**Files:**
- Create: `Lidl Parser/extension/background.js`, `content_history.js`, `content_detail.js`

**Interfaces:**
- Consumes: `storage.local` keys + `sync/cancel` messages (Task 5), Task 2 HTTP contract, Lidl URL shapes (`/mre/purchase-history?...&page=N`, `/mre/purchase-detail?...&t=TID`), tid regex `[?&]t=([\w-]+)`, detail marker `ΦΟΡΟΛΟΓΙΚΗ ΑΠΟΔΕΙΞΗ ΕΝΑΡΞΗ`, login sniff (URL lacks `mre/` and matches `login|sign|auth`).

- [ ] **Step 1: Create `content_history.js`.**

```js
// Runs on purchase-history pages: report tids + pager state, or logged-out.
(() => {
  const u = location.href.toLowerCase();
  if (!u.includes("mre/") && /login|sign|auth/.test(u)) {
    browser.runtime.sendMessage({type: "history", tabId: null, loggedOut: true, url: location.href});
    return;
  }
  const tids = [];
  const seen = new Set();
  for (const a of document.querySelectorAll("a[href*='purchase-detail']")) {
    const m = /[?&]t=([\w-]+)/.exec(a.getAttribute("href") || "");
    if (m && !seen.has(m[1])) { seen.add(m[1]); tids.push(m[1]); }
  }
  const hasNext = !!document.querySelector("a[href*='page=']");
  browser.runtime.sendMessage({type: "history", loggedOut: false, tids, hasNext, url: location.href});
})();
```

- [ ] **Step 2: Create `content_detail.js`.**

```js
// Runs on purchase-detail pages: wait for the receipt marker, send innerText.
(() => {
  const u = location.href.toLowerCase();
  if (!u.includes("mre/") && /login|sign|auth/.test(u)) {
    browser.runtime.sendMessage({type: "detail", loggedOut: true, url: location.href});
    return;
  }
  const MARK = "ΦΟΡΟΛΟΓΙΚΗ ΑΠΟΔΕΙΞΗ ΕΝΑΡΞΗ";
  const t0 = Date.now();
  const timer = setInterval(() => {
    const body = document.body ? document.body.innerText : "";
    if (body.includes(MARK)) {
      clearInterval(timer);
      browser.runtime.sendMessage({type: "detail", loggedOut: false, text: body});
    } else if (Date.now() - t0 > 20000) {
      clearInterval(timer);
      browser.runtime.sendMessage({type: "detail", loggedOut: false, text: "", timeout: true});
    }
  }, 500);
})();
```

- [ ] **Step 3: Create `background.js`.** Sequential sweep with throttle/abort/progress/cancel:

```js
let cancelFlag = false;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const HIST = (n) => `https://www.lidl-hellas.gr/mre/purchase-history?client_id=GreeceRetailClient&country_code=gr&language=el-GR&page=${n}`;
const DETAIL = (t) => `https://www.lidl-hellas.gr/mre/purchase-detail?client_id=GreeceRetailClient&country_code=gr&language=el-GR&t=${encodeURIComponent(t)}`;

function progress(text, done = false) {
  browser.runtime.sendMessage({type: "progress", text, done}).catch(() => {});
}

// Open url in a background tab, resolve with the content script's message.
function loadInTab(url, want, timeoutMs = 30000) {
  return new Promise(async (resolve) => {
    const tab = await browser.tabs.create({url, active: false});
    const timer = setTimeout(async () => {
      browser.tabs.onRemoved.removeListener(onGone);
      browser.runtime.onMessage.removeListener(onMsg);
      try { await browser.tabs.remove(tab.id); } catch (_) {}
      resolve({timeout: true});
    }, timeoutMs);
    function onGone(id) {
      if (id === tab.id) {
        clearTimeout(timer);
        browser.runtime.onMessage.removeListener(onMsg);
        resolve({closed: true});
      }
    }
    function onMsg(m) {
      if (m.type === want && m.tabId !== undefined ? true : m.type === want) {
        // content scripts message globally; first matching message wins
        clearTimeout(timer);
        browser.tabs.onRemoved.removeListener(onGone);
        browser.runtime.onMessage.removeListener(onMsg);
        browser.tabs.remove(tab.id).catch(() => {});
        resolve(m);
      }
    }
    browser.tabs.onRemoved.addListener(onGone);
    browser.runtime.onMessage.addListener(onMsg);
  });
}
```

Note: the `want` filter above is intentionally loose (first message of that type wins — the sweep awaits one page at a time, so no cross-talk). Simplify before finalizing: `if (m.type !== want) return;`.

```js
async function postJSON(base, token, path, body) {
  const r = await fetch(base + path, {
    method: "POST",
    headers: {"Content-Type": "application/json", "Authorization": "Bearer " + token},
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json();
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
    if (m.timeout || m.timeout === true) { progress(`Stopped at page ${page} (Lidl did not answer) — rerun later; resume is automatic.`, true); return; }
    let foundNew = false;
    for (const t of m.tids || []) {
      if (!seen.has(t)) { seen.add(t); tids.push(t); foundNew = true; }
    }
    if (fullScan && s.pageCap !== s.pageCap) {} // no-op guard removed in final code
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
  let done = 0, failed = [], skipped = tids.length - fresh.length;
  for (const t of fresh) {
    if (cancelFlag) break;
    progress(`Receipt ${done + 1}/${fresh.length} (skipped ${skipped})…`);
    const d = await loadInTab(DETAIL(t), "detail");
    if (d.loggedOut || d.closed || d.timeout || !d.text) {
      failed.push(t);
    } else {
      try {
        const r = await postJSON(base, s.token, "/api/ext/receipt", {tid: t, text: d.text});
        if (r.status === "duplicate") skipped += 1;
        else if (r.status !== "imported") failed.push(t);
      } catch (e) { failed.push(t); }
    }
    done += 1;
    if (fullScan) await sleep(1500 + Math.random() * 1500);
  }
  progress(`Done: ${done - failed.length} fetched • ${skipped} skipped • ${failed.length} failed${failed.length ? " (" + failed.slice(0, 3).join(", ") + (failed.length > 3 ? "…" : "") + ") — rerun to retry" : ""}. Review on the dashboard.`, true);
}

browser.runtime.onMessage.addListener((m) => {
  if (m.type === "sync") sweep(!!m.fullScan);
  if (m.type === "cancel") cancelFlag = true;
});
```

Remove the `if (fullScan && s.pageCap !== s.pageCap) {}` no-op line in the final file (left here only to show it must go).

- [ ] **Step 4: Manual smoke test.** Server from Task 4 running locally; sideload extension; set URL `http://127.0.0.1:5090` + token; Sync with cap 1 while logged OUT of Lidl → expect the "Log into lidl-hellas.gr" message; bad token → 401 message; unreachable URL → unreachable message. (Real logged-in sweep is Task 7 acceptance.)

- [ ] **Step 5: Commit.**

```bash
git add "Lidl Parser/extension/background.js" "Lidl Parser/extension/content_history.js" "Lidl Parser/extension/content_detail.js"
git commit -m "feat: extension sweep engine with throttle, abort, and progress"
```

---

### Task 7: Docs, acceptance, final green

**Files:**
- Modify: `Lidl Parser/README.md` (extension section)
- Modify: `docs/superpowers/specs/2026-09-28-firefox-extension-design.md` only if behavior deviated (note it in commit message)

**Interfaces:** none (docs + verification).

- [ ] **Step 1: README.** Append an "Extension sync (Firefox)" section: sideload steps (`about:debugging → This Firefox → Load Temporary Add-on`), where the token lives (Settings → Extension), URL config (LAN/VPN/remote), full-scan + cap explanation, self-signed HTTPS exception note, paste fallback pointer. Keep it under 30 lines.

- [ ] **Step 2: Manual acceptance** (maintainer, real Firefox + Gamma server): incremental Sync (expect fetched 0 / skipped N on a caught-up account) → one new receipt imports and appears for review → bad-token rejection → Cancel mid-sweep submits nothing partial-broken (server state consistent: `fetch_state` only has fully imported tids) → `docker compose up -d --build` succeeds with no browser packages; `/api/ext/status` 200 with token, 401 without.

- [ ] **Step 3: Final verification.** Run: `python -m pytest -q` from `Lidl Parser/` → all green. Run: `git status --short` → only intended files changed (plus the plan-specified deletions). No Playwright/`fetch/run`/`fetch/login` references remain: `grep -rn "playwright\|fetch/run\|fetch/login\|LIDL_PASS" "Lidl Parser/app.py" "Lidl Parser/requirements.txt" "Lidl Parser/Dockerfile"` returns nothing (tests excluded only if they assert absence).

- [ ] **Step 4: Commit.**

```bash
git add "Lidl Parser/README.md" docs/superpowers/specs/2026-09-28-firefox-extension-design.md
git commit -m "docs: extension sync guide and acceptance notes"
```

---

## Self-Review

**1. Spec coverage:** §1 architecture → Tasks 1–6; §2 decisions (full scraper/token/diff/MV2/remote URL) → Tasks 1,2,5,6 + URL field; cap + full-scan → Tasks 5,6; §4 data flow + §4a precautions (sequential/throttle/abort/resume/progress+cancel/backstop) → Task 6; §5 token/settings → Tasks 1,3; §6 errors → Tasks 2,6; §7 tests/rollout → Tasks 2,4,7; §8 non-goals respected (no Chrome/signing/pairing/server fallback/progress streaming — popup progress is coarse status text, not streaming).
**2. Placeholder scan:** no TBD/TODO/"similar to"; every code step ships concrete code; fixture reuse points at a real file.
**3. Type consistency:** `_ext_token()->str`, `_ext_authorized()->bool`; contract JSON shapes identical in Task 2 tests, implementation, and Task 6 callers; `storage.local` keys `{serverUrl,token,pageCap}` identical in Tasks 5–6.
**4. Review Focus:** all five lines have owning tests/checks (Tasks 1, 2, 2, 2, 7).
