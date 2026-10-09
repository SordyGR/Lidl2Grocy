# Card/User Splits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track who paid (card last-4 → user) and who owes what (per-item splits) with per-user dashboard totals.

**Architecture:** Parser extracts `card_last4` into new `receipts` column; `users` + `card_map` + `item_splits` tables; user/card management extends the `/settings` page from the deploy-sync plan; receipt page gains an Owner column with assign/split editor; dashboard shows per-user spend.

**Tech Stack:** Flask, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-docker-sync-splits-design.md` (Section 4)

## Global Constraints

- Requires the deploy-sync plan's `/settings` page (Task 2) — implement that plan first; this plan adds sections to its template and routes.
- App code lives in `Lidl Parser/`; run pytest from there: `python -m pytest tests/ -q`.
- Follow `tests/test_ui.py` conventions: `tmp_path` + `monkeypatch.setattr(dbmod, "DB_PATH", ...)` + `init_db()`.
- Only last-4 digits are ever stored; no full card numbers anywhere.
- Branch off `main`; one commit per task; push only when asked.

## Review Focus

- A cash receipt (no card line) must show "Cash" rather than crashing or showing a null owner — expect the paid-with line to read Cash.
- Split shares that don't sum to 1.0 must be rejected with the row preserved — expect a 400/redirect and unchanged splits.
- A receipt viewed before any users exist must not crash the seeding logic — expect all rows unassigned until users are added.
- Deleting a user with existing splits must not orphan rows or break totals — expect RESTRICT/block with a message, or reassignment prompt (implementer picks one, tests it).
- Two different cards sharing the same last-4 must not silently merge owners — expect the card_map note: last-4 collisions map to one user; document the limitation on the settings page.

---

### Task 1: Card extract + tables

**Files:**
- Modify: `Lidl Parser/db.py` (tables + column), `Lidl Parser/receipt_parser.py` (extract), `Lidl Parser/fetch_lib.py` (store on import), `Lidl Parser/app.py` upload path (store on upload)
- Test: `Lidl Parser/tests/test_card_extract.py`

**Interfaces:**
- Consumes: raw receipt text lines.
- Produces: `extract_card_last4(text) -> str | None`; `receipts.card_last4`; `users`, `card_map`, `item_splits` tables for Tasks 2–3.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_card_extract.py
def test_extracts_masked_last4():
    from receipt_parser import extract_card_last4
    assert extract_card_last4("xxx\n  ************1234  \n") == "1234"
    assert extract_card_last4("  41111111****1234  \n") == "1234"
    assert extract_card_last4("no card here\n") is None


def test_tables_exist(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "c.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(receipts)").fetchall()]
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "card_last4" in cols
    assert {"users", "card_map", "item_splits"} <= tables
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_card_extract.py -q`
Expected: FAIL with `ImportError` (no `extract_card_last4` yet)

- [ ] **Step 3: Minimal implementation**

In `receipt_parser.py`:

```python
import re as _re
_CARD_RE = _re.compile(r"\*+\s*(\d{4})\s*$")

def extract_card_last4(text):
    for line in text.splitlines():
        m = _CARD_RE.search(line.rstrip())
        if m:
            return m.group(1)
    return None
```

Note: `41111111****1234` matches (`\*+\s*(\d{4})\s*$` — asterisks then 4
digits at line end). Cash receipts return None.

In `db.py` SCHEMA append:

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS card_map (
    last4 TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS item_splits (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    share REAL NOT NULL CHECK (share > 0 AND share <= 1),
    PRIMARY KEY (item_id, user_id)
);
```

And in `init_db()`, after the grocy columns loop, add the column the same way:

```python
have_r = [r[1] for r in conn.execute("PRAGMA table_info(receipts)").fetchall()]
if "card_last4" not in have_r:
    conn.execute("ALTER TABLE receipts ADD COLUMN card_last4 TEXT")
```

Store on import: in `fetch_lib.import_kept_receipt`, after computing
`parsed_total`, set `card = extract_card_last4(kept_text)` and include
`card_last4` in the receipts INSERT. In `app.py` `upload()`, compute
`extract_card_last4(text)` per file and include it in its receipts INSERT.
Both need `ALTER TABLE`-safe columns — guaranteed by `init_db()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_card_extract.py -q`
Expected: PASS 2/2

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/db.py" "Lidl Parser/receipt_parser.py" "Lidl Parser/fetch_lib.py" "Lidl Parser/app.py" "Lidl Parser/tests/test_card_extract.py"
git commit -m "feat: card last-4 extract and user/split tables"
```

### Task 2: Users + card mapping on settings page

**Files:**
- Modify: `Lidl Parser/app.py` (`/settings` users/cards sections + CRUD routes), `Lidl Parser/templates/settings.html`
- Test: `Lidl Parser/tests/test_users_cards.py`

**Interfaces:**
- Consumes: `/settings` page + `users`/`card_map` tables from deploy-sync plan Task 2 and this plan's Task 1.
- Produces: user/card CRUD used by Task 3's receipt editor.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_users_cards.py
def test_user_crud_and_card_link(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "u.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post("/settings/users", data={"name": "Alice"}, follow_redirects=True)
    c.post("/settings/users", data={"name": "Bob"}, follow_redirects=True)
    c.post("/settings/cards", data={"last4": "1234", "user_id": "1"},
           follow_redirects=True)
    body = c.get("/settings").get_data(as_text=True)
    assert "Alice" in body and "1234" in body
    conn = dbmod.get_db()
    link = conn.execute("SELECT user_id FROM card_map WHERE last4='1234'").fetchone()
    conn.close()
    assert link["user_id"] == 1


def test_bad_last4_rejected(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "u2.db"))
    dbmod.init_db()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post("/settings/users", data={"name": "Alice"}, follow_redirects=True)
    r = c.post("/settings/cards", data={"last4": "93", "user_id": "1"},
               follow_redirects=True)
    assert r.status_code == 200
    conn = dbmod.get_db()
    n = conn.execute("SELECT COUNT(*) FROM card_map").fetchone()[0]
    conn.close()
    assert n == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_users_cards.py -q`
Expected: FAIL with 404 (no `/settings/users` yet)

- [ ] **Step 3: Minimal implementation**

```python
@app.route("/settings/users", methods=["POST"])
def settings_user_add():
    name = (request.form.get("name") or "").strip()
    if name:
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO users (name) VALUES (?)", (name,))
        conn.commit()
        conn.close()
    return redirect(url_for("settings"))


@app.route("/settings/cards", methods=["POST"])
def settings_card_link():
    last4 = (request.form.get("last4") or "").strip()
    try:
        uid = int(request.form.get("user_id") or "0")
    except ValueError:
        uid = 0
    if len(last4) == 4 and last4.isdigit() and uid > 0:
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO card_map (last4, user_id) VALUES (?, ?)",
                     (last4, uid))
        conn.commit()
        conn.close()
    else:
        flash("Card needs exactly 4 digits and a user")
    return redirect(url_for("settings"))
```

Extend `settings()` GET to pass `users` list + `cards` (joined with names).
`settings.html`: Users section (add form + list), Cards section (last4 input +
user dropdown + list), plus the documented last-4-collision note.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_users_cards.py -q`
Expected: PASS 2/2

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/app.py" "Lidl Parser/templates/settings.html" "Lidl Parser/tests/test_users_cards.py"
git commit -m "feat: users and card mapping on settings page"
```

### Task 3: Receipt split editor + paid-with line

**Files:**
- Modify: `Lidl Parser/app.py` (receipt detail context + `/receipts/<rid>/splits` POST), `Lidl Parser/templates/receipt_detail.html`
- Test: `Lidl Parser/tests/test_splits.py`

**Interfaces:**
- Consumes: `users`, `card_map`, `item_splits` from Tasks 1–2.
- Produces: per-item ownership data used by Task 4's dashboard totals.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_splits.py
def _seed(dbmod):
    conn = dbmod.get_db()
    conn.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob')")
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('r.txt','2026-09-05')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='r.txt'").fetchone()["id"]
    conn.execute("INSERT INTO items (receipt_id, raw, clean, final) VALUES (?,?,?,?)",
                 (rid, "MILK", "Milk", 10.0))
    iid = conn.execute("SELECT id FROM items WHERE receipt_id=?", (rid,)).fetchone()["id"]
    conn.commit()
    conn.close()
    return rid, iid


def test_whole_assign_and_split(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "sp.db"))
    dbmod.init_db()
    rid, iid = _seed(dbmod)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post(f"/receipts/{rid}/splits", data={"item_id": str(iid), "user_id": "1"},
           follow_redirects=True)
    conn = dbmod.get_db()
    rows = conn.execute("SELECT user_id, share FROM item_splits WHERE item_id=?", (iid,)).fetchall()
    assert [(r["user_id"], r["share"]) for r in rows] == [(1, 1.0)]
    c.post(f"/receipts/{rid}/splits", data={"item_id": str(iid), "shares": "1:0.6,2:0.4"},
           follow_redirects=True)
    rows = conn.execute("SELECT user_id, share FROM item_splits WHERE item_id=?", (iid,)).fetchall()
    conn.close()
    assert sorted((r["user_id"], r["share"]) for r in rows) == [(1, 0.6), (2, 0.4)]


def test_bad_shares_rejected(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "sp2.db"))
    dbmod.init_db()
    rid, iid = _seed(dbmod)
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post(f"/receipts/{rid}/splits", data={"item_id": str(iid), "shares": "1:0.6,2:0.3"},
           follow_redirects=True)
    conn = dbmod.get_db()
    n = conn.execute("SELECT COUNT(*) FROM item_splits WHERE item_id=?", (iid,)).fetchone()[0]
    conn.close()
    assert n == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_splits.py -q`
Expected: FAIL with 404 (no splits route yet)

- [ ] **Step 3: Minimal implementation**

```python
@app.route("/receipts/<int:rid>/splits", methods=["POST"])
def receipt_splits(rid):
    try:
        item_id = int(request.form.get("item_id") or "0")
    except ValueError:
        return redirect(url_for("receipt_detail", rid=rid))
    conn = get_db()
    ok = conn.execute("SELECT 1 FROM items WHERE id=? AND receipt_id=?",
                      (item_id, rid)).fetchone()
    if not ok:
        conn.close()
        return redirect(url_for("receipt_detail", rid=rid))
    if "user_id" in request.form and request.form.get("user_id"):
        try:
            uid = int(request.form["user_id"])
        except ValueError:
            uid = 0
        if uid > 0:
            conn.execute("DELETE FROM item_splits WHERE item_id=?", (item_id,))
            conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,1.0)",
                         (item_id, uid))
            conn.commit()
    elif request.form.get("shares"):
        parts = []
        try:
            for chunk in request.form["shares"].split(","):
                u, s = chunk.split(":")
                parts.append((int(u), float(s)))
        except ValueError:
            parts = []
        if parts and abs(sum(s for _, s in parts) - 1.0) < 0.001 and all(s > 0 for _, s in parts):
            conn.execute("DELETE FROM item_splits WHERE item_id=?", (item_id,))
            for u, s in parts:
                conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,?)",
                             (item_id, u, s))
            conn.commit()
        else:
            flash("Shares must be positive and sum to 1.0 — row unchanged")
    conn.close()
    return redirect(url_for("receipt_detail", rid=rid))
```

`receipt_detail()`: extend context with `users` list, per-item splits dict,
`paid_with` (card_last4 → owner name via card_map, or "Cash"), and
first-view seeding: for items with no splits rows and when users exist,
insert equal shares (`1.0/n`). Template: "Paid with" line + Owner column
(user select posting `user_id`, plus split text input posting `shares` as
`uid:share,uid:share`).

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_splits.py -q`
Expected: PASS 3/3

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/app.py" "Lidl Parser/templates/receipt_detail.html" "Lidl Parser/tests/test_splits.py"
git commit -m "feat: receipt split editor with paid-with line"
```

### Task 4: Dashboard per-user totals

**Files:**
- Modify: `Lidl Parser/app.py` (dashboard context), `Lidl Parser/templates/dashboard.html`
- Test: extend `Lidl Parser/tests/test_splits.py` (append, don't rewrite)

**Interfaces:**
- Consumes: `item_splits` + Task 3 seeding.
- Produces: finished feature; nothing downstream.

- [ ] **Step 1: Write the failing test** (append to `tests/test_splits.py`)

```python
def test_dashboard_user_totals(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "sp3.db"))
    dbmod.init_db()
    rid, iid = _seed(dbmod)
    conn = dbmod.get_db()
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,?)", (iid, 1, 0.6))
    conn.execute("INSERT INTO item_splits (item_id, user_id, share) VALUES (?,?,?)", (iid, 2, 0.4))
    conn.commit()
    conn.close()
    import app as appmod
    appmod.app.testing = True
    body = appmod.app.test_client().get("/").get_data(as_text=True)
    assert "Alice" in body and "6.0" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_splits.py::test_dashboard_user_totals -q`
Expected: FAIL (`"Alice" in body` false — no totals section yet)

- [ ] **Step 3: Minimal implementation**

In `dashboard()`, add:

```python
user_totals = query("""
    SELECT u.name AS n, ROUND(SUM(i.final * s.share), 2) AS total
    FROM item_splits s
    JOIN items i ON i.id = s.item_id
    JOIN users u ON u.id = s.user_id
    GROUP BY u.name ORDER BY total DESC
""")
```

Pass `user_totals=user_totals`; `dashboard.html` renders a small table when
non-empty. Unsplit items contribute to nobody — matches "first view seeds"
behavior from Task 3.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS all (no regressions in fetch/grocy/ui suites)

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/app.py" "Lidl Parser/templates/dashboard.html" "Lidl Parser/tests/test_splits.py"
git commit -m "feat: dashboard per-user spend totals"
```
