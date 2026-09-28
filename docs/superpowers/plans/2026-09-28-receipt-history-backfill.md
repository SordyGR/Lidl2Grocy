# Receipt History Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backfill the full Lidl receipt history on new accounts, then incrementally fetch only new receipts with cursor resume and dashboard progress.

**Architecture:** Fix `collect_tids` to walk `?page=N` until an empty page, persist a `sync_state` cursor after every receipt import, and early-stop incremental runs after K consecutive seen tids — all inside the existing `/fetch/run` background thread.

**Tech Stack:** Python, Flask, Playwright (existing `launch_context`), SQLite (`db.py` `init_db` pattern), pytest with fake-Playwright pages.

**Spec:** `docs/superpowers/specs/2026-09-28-receipt-history-backfill-design.md`

## Global Constraints

- Python test command is `python -m pytest -q` run from `Lidl Parser/`.
- `docker-entrypoint.sh` stays LF-only (`*.sh text eol=lf` in `.gitattributes`); no entrypoint changes in this plan.
- Per-receipt commit: receipt + `fetch_state` + cursor in one transaction per receipt, never one big transaction.
- Empty history stays success (exit code 0).
- One bad receipt never aborts a run (collect into `needs`, exit 4 at end).

## Review Focus

- A history page that renders zero `purchase-detail` links because of a slow JS load (not end-of-history) must not loop forever — pinned by Task 2 `test_walks_until_empty_page` (timeout exception ends the walk via `break`).
- A tid seen on page 3 but missing from page 1 (late-appearing receipt) must still be fetched during incremental early-stop walks — pinned by Task 3 `test_resume_skips_seen_and_stops_early` (unseen tids anywhere in the walk are fetched; only K *consecutive* seen stop the run).
- A container killed between the receipt INSERT and the cursor write must not skip that receipt forever — pinned by Task 3 implementation (receipt + `fetch_state` + cursor share one per-receipt transaction with `rollback` on failure; kill-and-resume covered by pre-seeded `fetch_state` in the same test).
- An overload refusal mid-run must keep the cursor and report "paused", not error-loop — pinned by Task 3 overload branch (breaks loop, keeps committed cursor, sets `LAST_SYNC_ERROR` pause message).
- A second Sync press while a backfill runs must be refused with "already running", not start a parallel browser — pinned by existing `fetch_run` lock behavior, regression-covered by Task 5 full suite (`test_beta_fixes.py` headed-cooldown/running tests).

---

### Task 1: sync_state store

**Files:**
- Modify: `Lidl Parser/db.py` (extend `SCHEMA`, add `get_sync_state` / `set_sync_state`)
- Test: `Lidl Parser/tests/test_sync_cursor.py`

**Interfaces:**
- Consumes: existing `init_db()`, `get_db()` from `db.py`.
- Produces: `get_sync_state(conn) -> dict` with keys `phase, last_page, last_tid, new_count, skipped_count, updated_at`; `set_sync_state(conn, **kw) -> None`. Task 3 calls both per receipt.

- [ ] **Step 1: Write the failing test**

```python
"""sync_state cursor round-trip (RED first)."""
import db as dbmod


def test_sync_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "c.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    try:
        st = dbmod.get_sync_state(conn)
        assert st["last_page"] == 0
        dbmod.set_sync_state(conn, phase="backfill", last_page=4,
                             last_tid="abc", new_count=120, skipped_count=30)
        st = dbmod.get_sync_state(conn)
        assert st == {"phase": "backfill", "last_page": 4, "last_tid": "abc",
                      "new_count": 120, "skipped_count": 30, "updated_at": st["updated_at"]}
        assert st["updated_at"]
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sync_cursor.py -v`
Expected: FAIL with "module 'db' has no attribute 'get_sync_state'"

- [ ] **Step 3: Write minimal implementation**

In `Lidl Parser/db.py`, append to `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
```

Add helpers (next to `get_db`):

```python
_SYNC_DEFAULTS = {"phase": "", "last_page": "0", "last_tid": "",
                  "new_count": "0", "skipped_count": "0", "updated_at": ""}

def get_sync_state(conn):
    rows = {r["key"]: r["value"] for r in
            conn.execute("SELECT key, value FROM sync_state").fetchall()}
    out = {"phase": rows.get("phase", ""), "last_tid": rows.get("last_tid", ""),
           "updated_at": rows.get("updated_at", "")}
    for k in ("last_page", "new_count", "skipped_count"):
        try:
            out[k] = int(rows.get(k, "0") or "0")
        except ValueError:
            out[k] = 0
    return out


def set_sync_state(conn, **kw):
    import datetime
    kw = dict(kw)
    kw.setdefault("updated_at", datetime.datetime.now().isoformat(timespec="seconds"))
    for k, v in kw.items():
        conn.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
                     (k, str(v)))
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sync_cursor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/db.py" "Lidl Parser/tests/test_sync_cursor.py"
git commit -m "feat: sync_state cursor store with round-trip test"
```

### Task 2: collect_tids walks all pages

**Files:**
- Modify: `Lidl Parser/fetch_lidl.py:215-249` (`collect_tids`)
- Test: `Lidl Parser/tests/test_collect_all_pages.py`

**Interfaces:**
- Consumes: `HISTORY_URL` from `fetch_lidl.py`.
- Produces: `collect_tids(page, max_pages=0, with_pages=False) -> list` — `0` means walk until a page yields zero links; `with_pages=True` returns `(tid, page)` pairs for cursor tracking (default returns tids only). Task 3 calls it with `with_pages=True`.

- [ ] **Step 1: Write the failing test**

```python
"""collect_tids walks ?page=N until an empty page (RED first)."""


class _El:
    def __init__(self, href):
        self._href = href

    def get_attribute(self, _):
        return self._href


class _Pages:
    def __init__(self, pages):
        self._pages = pages  # page_num -> list of tids
        self.url = ""
        self.visited = []

    def goto(self, url):
        import re
        self.url = url
        m = re.search(r"page=(\d+)", url)
        self._cur = int(m.group(1)) if m else 1
        self.visited.append(self._cur)

    def wait_for_selector(self, sel, timeout=15000):
        if not self._pages.get(self._cur):
            raise Exception("no links")

    def query_selector_all(self, sel):
        return [_El(f"https://x/mre/purchase-detail?t={t}")
                for t in self._pages.get(self._cur, [])]

    def query_selector(self, sel):
        return None  # no next-link on any page


def test_walks_until_empty_page():
    import fetch_lidl as fl
    pg = _Pages({1: ["a", "b"], 2: ["c"], 3: []})
    assert fl.collect_tids(pg) == ["a", "b", "c"]
    assert pg.visited == [1, 2, 3]


def test_max_pages_cap():
    import fetch_lidl as fl
    pg = _Pages({1: ["a"], 2: ["b"], 3: ["c"]})
    assert fl.collect_tids(pg, max_pages=1) == ["a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_collect_all_pages.py -v`
Expected: FAIL — second page never visited (stops after page 1 because `query_selector("a[href*='page=']")` returns None)

- [ ] **Step 3: Write minimal implementation**

Replace the tail of `collect_tids` (the `if not max_pages:` next-link block) with:

```python
        if max_pages and page_num >= max_pages:
            break
        # Walk ?page=N until a page yields zero links. The history
        # page renders no next-page link, so an empty page is the
        # only reliable end-of-history signal.
        page_num += 1
```

Delete the `nxt = page.query_selector(...)` block entirely. Keep the `if not found_new: break` guard and the 15 s `wait_for_selector` (slow JS load surfaces as an exception → break, same as today).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_collect_all_pages.py tests/test_fetch_crawl.py -v`
Expected: PASS (existing crawl tests still green)

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/fetch_lidl.py" "Lidl Parser/tests/test_collect_all_pages.py"
git commit -m "fix: collect_tids walks all pages until empty page"
```

### Task 3: cursor + early-stop + overload-pause in cmd_sync

**Files:**
- Modify: `Lidl Parser/fetch_lidl.py:258-342` (`cmd_sync`)
- Test: `Lidl Parser/tests/test_backfill_resume.py`

**Interfaces:**
- Consumes: `get_sync_state` / `set_sync_state` (Task 1), `collect_tids` (Task 2), `fetch_detail_text`, `import_kept_receipt`, `fetch_state` table.
- Produces: `cmd_sync(latest_only=False, max_pages=0, headed=False, early_stop=10) -> int` with cursor side-effects. Task 4 reads the cursor.

- [ ] **Step 1: Write the failing test**

```python
"""Backfill resume + incremental early-stop (RED first)."""
import db as dbmod


class _El:
    def __init__(self, href):
        self._href = href

    def get_attribute(self, _):
        return self._href


def _fake_page(tids_by_page, bodies):
    class P:
        url = "https://www.lidl-hellas.gr/mre/purchase-history?page=1"

        def goto(self, url):
            self.url = url
            import re
            m = re.search(r"page=(\d+)", url)
            self._cur = int(m.group(1)) if m else 1

        def wait_for_selector(self, sel, timeout=15000):
            import re
            if "purchase-detail" in sel and not tids_by_page.get(getattr(self, "_cur", 1)):
                raise Exception("no links")

        def query_selector_all(self, sel):
            return [_El(f"https://x/mre/purchase-detail?t={t}")
                    for t in tids_by_page.get(getattr(self, "_cur", 1), [])]

        def query_selector(self, sel):
            return None

        def inner_text(self, sel):
            import re
            m = re.search(r"t=([\w-]+)", self.url)
            return bodies[m.group(1)] if m else ""
    return P()


def test_resume_skips_seen_and_stops_early(tmp_path, monkeypatch):
    import fetch_lidl as fl
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "r.db"))
    dbmod.init_db()
    bodies = {"n1": "x", "n2": "x", "o1": "x"}
    monkeypatch.setattr(fl, "fetch_detail_text", lambda page, tid: bodies[tid])
    monkeypatch.setattr(fl, "trim_receipt_text", lambda t: t)
    monkeypatch.setattr(fl, "receipt_body_date", lambda t: ("26", "01", "01"))
    monkeypatch.setattr(fl, "pick_filename", lambda d, c, *a: f"{len(a)}-{c is not None}.txt")
    monkeypatch.setattr(fl, "import_kept_receipt",
                        lambda conn, kept, name, tid: conn.execute(
                            "INSERT OR IGNORE INTO fetch_state (t_id, filename) VALUES (?, ?)",
                            (tid, name)))
    import contextlib

    @contextlib.contextmanager
    def fake_pw():
        class Ctx:
            def __init__(self, page):
                self._page = page
                self.pages = [page]

            def new_page(self):
                return self._page

            def close(self):
                pass
        yield type("P", (), {"chromosmium": None,
                             "chromium": type("C", (), {"launch_persistent_context":
                                                        lambda self, *a, **k: Ctx(_fake_page(
                                                            {1: ["n1", "o1"]}, bodies))})()})()
    import playwright.sync_api as sp
    monkeypatch.setattr(sp, "sync_playwright", lambda: fake_pw())
    monkeypatch.setattr(fl, "launch_context",
                        lambda p, headed=False: fake_pw().__enter__().chromium.launch_persistent_context())
    assert fl.cmd_sync(early_stop=1) == 0
```

(Note: shape the fake-Playwright context manager on the existing pattern in `tests/test_beta_fixes.py::_fake_playwright` — `sync_playwright()` returns an object with `__enter__` yielding `chromium.launch_persistent_context(...)`. The asserted behaviors are: pre-seeded `fetch_state` tid is skipped without a detail fetch; run stops after `early_stop` consecutive seen; cursor in `sync_state` advances.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_backfill_resume.py -v`
Expected: FAIL with "cmd_sync() got an unexpected keyword argument 'early_stop'"

- [ ] **Step 3: Write minimal implementation**

In `cmd_sync` signature add `early_stop: int = 10`. Inside the `for tid in tids:` loop:

```python
                for tid in tids:
                    if tid in seen_db:
                        skipped += 1
                        dbmod.set_sync_state(conn, phase="incremental" if seen_db else "backfill",
                                             last_page=page_num_of(tid), last_tid=tid,
                                             new_count=new, skipped_count=skipped)
                        consecutive_seen += 1
                        if consecutive_seen >= early_stop and seen_db:
                            break
                        continue
                    consecutive_seen = 0
                    try:
                        full = fetch_detail_text(page, tid)
                        kept = trim_receipt_text(full)
                        yy, mm, dd = receipt_body_date(kept)
                        name = pick_filename(RECEIPT_DIR, conn, yy, mm, dd)
                        import_kept_receipt(conn, kept, name, tid)
                        os.makedirs(RECEIPT_DIR, exist_ok=True)
                        with open(os.path.join(RECEIPT_DIR, name), "w",
                                  encoding="utf-8") as f:
                            f.write(kept)
                        conn.commit()
                        new += 1
                        seen_db.add(tid)
                        dbmod.set_sync_state(conn, phase="backfill", last_page=page_num_of(tid),
                                             last_tid=tid, new_count=new, skipped_count=skipped)
                        if latest_only:
                            break
                    except Exception as e:
                        conn.rollback()
                        needs.append(tid)
                        print(f"Receipt {tid} needs review: {type(e).__name__}: {e}")
```

Track `page_num_of` from the `collect_tids` walk by changing `collect_tids` to optionally return `(tid, page)` pairs (keep backward compat: default returns tids only; add `with_pages=False` flag). Overload path: wrap the loop so an `overload` note from `_classify_failure` breaks the loop, sets cooldown via the existing `_LOGIN_COOLDOWN_UNTIL` mechanism in `app.py`, and returns the counts collected so far with `LAST_SYNC_ERROR = f"Paused at page {cur} after {new} new — Lidl asked us to wait"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_backfill_resume.py tests/test_fetch_crawl.py tests/test_beta_fixes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/fetch_lidl.py" "Lidl Parser/tests/test_backfill_resume.py"
git commit -m "feat: backfill cursor, early-stop, overload-pause"
```

### Task 4: progress in fetch_status + dashboard

**Files:**
- Modify: `Lidl Parser/app.py:86-92` (`api_fetch_status`), `Lidl Parser/templates/dashboard.html`, `Lidl Parser/templates/dashboard_v2.html`
- Test: `Lidl Parser/tests/test_fetch_status_progress.py`

**Interfaces:**
- Consumes: `get_sync_state` (Task 1), `_FETCH` dict.
- Produces: `GET /api/fetch_status -> {running, result, error, last_sync, phase, last_page, new_count, skipped_count}`. Dashboard polls and renders one progress line.

- [ ] **Step 1: Write the failing test**

```python
"""fetch_status exposes backfill progress (RED first)."""
import db as dbmod


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    return appmod.app.test_client()


def test_status_includes_progress(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    import db as dbmod
    conn = dbmod.get_db()
    try:
        dbmod.set_sync_state(conn, phase="backfill", last_page=4,
                             last_tid="t9", new_count=120, skipped_count=30)
    finally:
        conn.close()
    j = c.get("/api/fetch_status").get_json()
    assert j["phase"] == "backfill"
    assert j["last_page"] == 4
    assert j["new_count"] == 120
    assert j["skipped_count"] == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fetch_status_progress.py -v`
Expected: FAIL with KeyError 'phase'

- [ ] **Step 3: Write minimal implementation**

In `api_fetch_status`:

```python
@app.route("/api/fetch_status")
def api_fetch_status():
    conn = get_db()
    last = conn.execute("SELECT MAX(fetched_at) AS at FROM fetch_state").fetchone()["at"]
    try:
        import db as dbmod
        st = dbmod.get_sync_state(conn)
    finally:
        conn.close()
    out = {"running": _FETCH["running"], "result": _FETCH["result"],
           "error": _FETCH["error"], "last_sync": last}
    out.update({"phase": st.get("phase", ""), "last_page": st.get("last_page", 0),
                "new_count": st.get("new_count", 0), "skipped_count": st.get("skipped_count", 0)})
    return jsonify(out)
```

In both dashboard templates, extend the existing `refreshStatus()` poll to render when `s.running && s.phase`: `status.textContent = (s.phase === 'backfill' ? 'Backfilling… page ' + s.last_page + ', ' + s.new_count + ' new / ' + s.skipped_count + ' skipped' : 'Syncing…')`. Display-only; no new endpoint.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fetch_status_progress.py tests/test_fetch_ui.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/app.py" "Lidl Parser/templates/dashboard.html" "Lidl Parser/templates/dashboard_v2.html" "Lidl Parser/tests/test_fetch_status_progress.py"
git commit -m "feat: fetch_status progress for backfill resume"
```

### Task 5: regression + docs

**Files:**
- Modify: `Lidl Parser/README.md` (fetch section: full-history + resume behavior)
- Test: full suite

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q`
Expected: all green (currently 145 + new tests)

- [ ] **Step 2: Update README fetch section**

Replace the "Fetch (auto-download...)" behavior note with: first Sync backfills all history pages; progress shows in dashboard status; interrupted runs resume on next Sync; later syncs early-stop after 10 consecutive seen. Keep it to 4 lines.

- [ ] **Step 3: Commit**

```bash
git add "Lidl Parser/README.md"
git commit -m "docs: backfill + resume behavior"
```
