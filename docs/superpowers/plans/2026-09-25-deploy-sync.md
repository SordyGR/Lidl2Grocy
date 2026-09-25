# Deploy + Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the app in Docker on port 5090 with a settings page and dashboard-triggered Lidl sync.

**Architecture:** `settings` table with UI-over-env resolution; `/settings` page for Lidl/Grocy credentials; `/fetch/run` + `/api/fetch_status` around `cmd_sync` in a background thread; credential auto-fill login in `fetch_lidl.py` with paste fallback; `Dockerfile` + `compose.yml` + gunicorn.

**Tech Stack:** Flask, SQLite, Playwright (headless Chromium), gunicorn, Docker Compose, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-docker-sync-splits-design.md` (Sections 1–3)

## Global Constraints

- App code lives in `Lidl Parser/`; repo root is `C:\Users\sordy\Documents\OpenCodeProject1`.
- Run pytest from `Lidl Parser/`: `python -m pytest tests/ -q`.
- Follow existing test conventions (`tests/test_ui.py`): `tmp_path` + `monkeypatch.setattr(dbmod, "DB_PATH", ...)` + `init_db()` + `test_client`.
- Never commit `.env`, `data/`, `receipts/`, or personal exports (gitignored).
- Branch off `main`; one commit per task; push only when asked.

## Review Focus

- A password saved in settings must never render back into HTML or logs — expect no secret value in any response body.
- Two rapid "Sync" clicks must not launch two Playwright browsers — expect the second POST to report `running: true` without starting work.
- `SECRET_KEY` must stay env-only — expect no settings key and no form field for it.
- An unknown settings key POSTed by hand must be ignored, not stored — expect the settings table to contain only known keys.
- Grocy URL without `/api` suffix must be rejected with a hint, not stored silently — expect a flash and unchanged value.

---

### Task 1: Settings store + resolution

**Files:**
- Modify: `Lidl Parser/db.py` (schema + `get_setting`/`set_setting`)
- Create: `Lidl Parser/settings_store.py` (resolution: settings → env)
- Test: `Lidl Parser/tests/test_settings.py`

**Interfaces:**
- Consumes: `get_db` from `db.py`.
- Produces: `settings_store.get(key, default="") -> str`, `settings_store.set(key, value)`, `settings_store.KNOWN_KEYS = {"LIDL_USER","LIDL_PASS","GROCY_URL","GROCY_API_KEY"}` for Tasks 2–3.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_settings.py
def test_resolution_prefers_db_over_env(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s.db"))
    dbmod.init_db()
    monkeypatch.setenv("GROCY_URL", "https://env.example/api")
    import settings_store as ss
    assert ss.get("GROCY_URL") == "https://env.example/api"
    ss.set("GROCY_URL", "https://db.example/api")
    assert ss.get("GROCY_URL") == "https://db.example/api"


def test_unknown_key_rejected(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s2.db"))
    dbmod.init_db()
    import settings_store as ss
    ss.set("SECRET_KEY", "x")
    assert ss.get("SECRET_KEY") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_settings.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'settings_store'`

- [ ] **Step 3: Minimal implementation**

In `db.py` SCHEMA append:

```sql
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
```

Create `Lidl Parser/settings_store.py`:

```python
"""DB-backed settings with env fallback. UI value wins, env seeds."""
import os
import db as dbmod

KNOWN_KEYS = {"LIDL_USER", "LIDL_PASS", "GROCY_URL", "GROCY_API_KEY"}


def get(key, default=""):
    if key not in KNOWN_KEYS:
        return default
    try:
        conn = dbmod.get_db()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
    except Exception:
        row = None
    if row is not None and (row["value"] or "") != "":
        return row["value"]
    return os.environ.get(key, default)


def set(key, value):
    if key not in KNOWN_KEYS:
        return
    conn = dbmod.get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                 (key, value or ""))
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_settings.py -q`
Expected: PASS 2/2

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/db.py" "Lidl Parser/settings_store.py" "Lidl Parser/tests/test_settings.py"
git commit -m "feat: db-backed settings with env fallback"
```

### Task 2: Settings page (Lidl + Grocy)

**Files:**
- Modify: `Lidl Parser/app.py` (GET/POST `/settings`, Grocy reads via settings_store)
- Create: `Lidl Parser/templates/settings.html`
- Modify: `Lidl Parser/templates/base.html` (nav link)
- Test: `Lidl Parser/tests/test_settings_ui.py`

**Interfaces:**
- Consumes: `settings_store.get/set/KNOWN_KEYS` from Task 1.
- Produces: `/settings` page; Grocy credential source switch for Task 3 and later Grocy work.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_settings_ui.py
def test_settings_roundtrip_and_secret_redaction(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.post("/settings", data={"GROCY_URL": "https://g.example/api",
                                  "GROCY_API_KEY": "k", "LIDL_USER": "u",
                                  "LIDL_PASS": "p"}, follow_redirects=True)
    assert r.status_code == 200
    body = c.get("/settings").get_data(as_text=True)
    assert "https://g.example/api" in body
    assert ">p<" not in body and 'value="p"' not in body


def test_grocy_url_without_api_rejected(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s2.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post("/settings", data={"GROCY_URL": "https://g.example",
                              "GROCY_API_KEY": "k"}, follow_redirects=True)
    import settings_store as ss
    assert ss.get("GROCY_URL") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_settings_ui.py -q`
Expected: FAIL with 404 (no `/settings` route yet)

- [ ] **Step 3: Minimal implementation**

In `app.py` (needs `import settings_store` — name it `app_settings` to avoid
clashing with any `settings` symbol):

```python
import settings_store as app_settings

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        url = (request.form.get("GROCY_URL") or "").strip()
        if url and not url.rstrip("/").endswith("/api"):
            flash("Grocy URL must end with /api — value not saved")
        else:
            app_settings.set("GROCY_URL", url)
            app_settings.set("GROCY_API_KEY", (request.form.get("GROCY_API_KEY") or "").strip())
        app_settings.set("LIDL_USER", (request.form.get("LIDL_USER") or "").strip())
        app_settings.set("LIDL_PASS", request.form.get("LIDL_PASS") or "")
        flash("Settings saved")
        return redirect(url_for("settings"))
    vals = {k: app_settings.get(k) for k in ("LIDL_USER", "GROCY_URL", "GROCY_API_KEY")}
    vals["LIDL_PASS_SET"] = bool(app_settings.get("LIDL_PASS"))
    return render_template("settings.html", v=vals)
```

Password field renders empty always; placeholder shows "saved" when set.
Change `grocy_client.get_config` callers? No — instead patch `grocy.get_config`
to read via `settings_store` (import inside function to avoid cycles):

```python
def get_config():
    import settings_store as ss
    base_url = (ss.get("GROCY_URL") or "").strip().rstrip("/")
    api_key = (ss.get("GROCY_API_KEY") or "").strip()
    if not base_url or not api_key:
        raise GrocyConfigError(CONFIG_HINT)
    return base_url, api_key
```

`settings.html`: sections for Lidl (user + password + saved-indicator),
Grocy (url + key + link to `/api/grocy_status` test), plaintext note.
`base.html` nav: add `<a href="/settings">Settings</a>`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_settings_ui.py tests/test_settings.py -q`
Expected: PASS all

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/app.py" "Lidl Parser/grocy.py" "Lidl Parser/templates/settings.html" "Lidl Parser/templates/base.html" "Lidl Parser/tests/test_settings_ui.py"
git commit -m "feat: settings page for Lidl and Grocy credentials"
```

### Task 3: Dashboard sync button + status API

**Files:**
- Modify: `Lidl Parser/app.py` (`/fetch/run`, `/api/fetch_status`, dashboard context)
- Modify: `Lidl Parser/templates/dashboard.html` (button + status + poll JS)
- Test: `Lidl Parser/tests/test_fetch_ui.py`

**Interfaces:**
- Consumes: `fetch_lidl.cmd_sync` (mocked in tests); `fetch_state` table.
- Produces: running-sync UX; Task 4 plugs real auth underneath.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_fetch_ui.py
def test_fetch_run_single_flight(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "f.db"))
    dbmod.init_db()
    import app as appmod
    calls = []
    monkeypatch.setattr(appmod.fetch_lidl, "cmd_sync",
                        lambda **kw: calls.append(kw) or 0)
    appmod.app.testing = True
    c = appmod.app.test_client()
    r1 = c.post("/fetch/run")
    assert r1.get_json()["started"] is True
    r2 = c.post("/fetch/run")
    assert r2.get_json()["started"] is False
    assert len(calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fetch_ui.py -q`
Expected: FAIL with 404 (no `/fetch/run` yet)

- [ ] **Step 3: Minimal implementation**

In `app.py`:

```python
import threading
import fetch_lidl

_FETCH = {"running": False, "result": None, "error": None}

def _run_sync():
    try:
        _FETCH["result"] = fetch_lidl.cmd_sync(latest_only=True)
    except Exception as e:
        _FETCH["error"] = str(e)[:300]
    finally:
        _FETCH["running"] = False

@app.route("/fetch/run", methods=["POST"])
def fetch_run():
    if _FETCH["running"]:
        return jsonify({"started": False, "reason": "already running"})
    _FETCH.update({"running": True, "result": None, "error": None})
    threading.Thread(target=_run_sync, daemon=True).start()
    return jsonify({"started": True})

@app.route("/api/fetch_status")
def api_fetch_status():
    conn = get_db()
    last = conn.execute("SELECT MAX(fetched_at) AS at FROM fetch_state").fetchone()["at"]
    conn.close()
    return jsonify({"running": _FETCH["running"], "result": _FETCH["result"],
                    "error": _FETCH["error"], "last_sync": last})
```

`dashboard()`: pass `fetch_status=_FETCH`-derived values or let the page poll
`/api/fetch_status` directly — poll directly, no route change needed.
`dashboard.html`: Sync button POSTs via fetch, polls every 3s while running,
shows new/skipped/needs-review, reloads receipts list on done.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_fetch_ui.py -q`
Expected: PASS. Note: the background thread calls the mocked `cmd_sync`
synchronously enough for the `calls == 1` assertion (mock returns instantly).

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/app.py" "Lidl Parser/templates/dashboard.html" "Lidl Parser/tests/test_fetch_ui.py"
git commit -m "feat: dashboard receipt sync with status polling"
```

### Task 4: Stored-credential auto-fill login + paste fallback UI

**Files:**
- Modify: `Lidl Parser/fetch_lidl.py` (`ensure_login` with auto-fill attempt)
- Modify: `Lidl Parser/app.py` + `templates/dashboard.html` (paste box → `cmd_paste`)
- Test: `Lidl Parser/tests/test_fetch_auth.py` (credential resolution only)

**Interfaces:**
- Consumes: `settings_store.get("LIDL_USER"/"LIDL_PASS")`; `cmd_paste` from `fetch_lidl`.
- Produces: working headless login; nothing downstream.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_fetch_auth.py
def test_lidl_creds_from_settings_then_env(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "a.db"))
    dbmod.init_db()
    monkeypatch.setenv("LIDL_USER", "envuser")
    monkeypatch.delenv("LIDL_PASS", raising=False)
    import fetch_lidl as fl
    assert fl.get_lidl_creds() == ("envuser", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fetch_auth.py -q`
Expected: FAIL with `AttributeError` (no `get_lidl_creds` yet)

- [ ] **Step 3: Minimal implementation**

In `fetch_lidl.py`:

```python
def get_lidl_creds():
    import settings_store as ss
    return ss.get("LIDL_USER"), ss.get("LIDL_PASS")


def try_auto_login(page) -> bool:
    """Fill the Lidl login form with stored creds. Best-effort: returns
    False when the form is missing/changed so callers fall back."""
    user, pw = get_lidl_creds()
    if not user or not pw:
        return False
    try:
        page.goto(LOGIN_URL)
        for sel in ('input[type="email"]', 'input[name="email"]',
                    'input[type="text"]'):
            try:
                page.fill(sel, user, timeout=5000)
                break
            except Exception:
                continue
        for sel in ('input[type="password"]', 'input[name="password"]'):
            try:
                page.fill(sel, pw, timeout=5000)
                break
            except Exception:
                continue
        page.keyboard.press("Enter")
        page.wait_for_timeout(5000)
        return not is_login_redirect(page.url)
    except Exception:
        return False
```

In `cmd_sync`, after the login-redirect check fails in headless mode, call
`try_auto_login(page)` before giving up: on True continue to history, on
False return 3 with "LOGIN NEEDED" (UI shows paste fallback).
Dashboard paste box: `<details>` with textarea posting to new
`POST /fetch/paste` (`tid` optional) calling `cmd_paste` on a saved tempfile,
flashing the saved name. Test the route with a minimal pasted fixture reusing
`tests/data_paste.txt`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_fetch_auth.py -q`
Expected: PASS. Live check (needs real creds + network): run sync once,
confirm login succeeds or an honest "login needed" + paste path works.

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/fetch_lidl.py" "Lidl Parser/app.py" "Lidl Parser/templates/dashboard.html" "Lidl Parser/tests/test_fetch_auth.py"
git commit -m "feat: stored-credential auto-login with paste fallback"
```

### Task 5: Docker packaging + same-day ordering

**Files:**
- Create: `Lidl Parser/Dockerfile`, `Lidl Parser/compose.yml`, `Lidl Parser/.env.example`
- Modify: `Lidl Parser/requirements.txt` (add `gunicorn`), `Lidl Parser/app.py` (receipt_seq secondary sort in `receipts()` + dashboard `recent`)
- Test: `Lidl Parser/tests/test_receipt_order.py`

**Interfaces:**
- Consumes: nothing new. Produces: deployable unit.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_receipt_order.py
def test_same_day_receipts_order_by_seq(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "o.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date, receipt_seq) VALUES ('26-09-05.1.txt','2026-09-05',2)")
    conn.execute("INSERT INTO receipts (filename, receipt_date, receipt_seq) VALUES ('26-09-05.txt','2026-09-05',1)")
    conn.commit()
    conn.close()
    import app as appmod
    appmod.app.testing = True
    body = appmod.app.test_client().get("/receipts").get_data(as_text=True)
    assert body.index("26-09-05.txt") < body.index("26-09-05.1.txt")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_receipt_order.py -q`
Expected: FAIL (date-only ordering puts `.1` first or ties)

- [ ] **Step 3: Minimal implementation**

Change `receipts()` and dashboard `recent` ORDER BY to
`ORDER BY receipt_date DESC, receipt_seq DESC`
(`receipt_detail` is per-id, unaffected).

`Dockerfile`:

```dockerfile
FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install chromium --with-deps
COPY . .
CMD ["sh", "-c", "python migrate.py && gunicorn -w 1 --threads 4 -b 0.0.0.0:5090 app:app"]
```

`requirements.txt`: append `gunicorn>=23.0`.
`compose.yml`:

```yaml
services:
  lidl-parser:
    build: .
    ports: ["5090:5090"]
    env_file: [.env]
    volumes: ["./data:/app/data", "./receipts:/app/receipts"]
    restart: unless-stopped
```

`.env.example`: `LIDL_USER=`, `LIDL_PASS=`, `GROCY_URL=https://grocy.example.com/api`,
`GROCY_API_KEY=`, `SECRET_KEY=change-me`.

- [ ] **Step 4: Run tests + validate compose**

Run: `python -m pytest tests/test_receipt_order.py -q`
Expected: PASS
Run: `docker compose config` (from `Lidl Parser/`)
Expected: renders without errors. If no Docker on this machine, `pip check`
plus eyeball review stands in — note it in the commit message.

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/Dockerfile" "Lidl Parser/compose.yml" "Lidl Parser/.env.example" "Lidl Parser/requirements.txt" "Lidl Parser/app.py" "Lidl Parser/tests/test_receipt_order.py"
git commit -m "feat: docker packaging on 5090, same-day receipt ordering"
```
