# Grocy Stock Push Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push receipt lines to Grocy as backdated stock purchases with per-product track flags and idempotent push log.

**Architecture:** Extend `grocy.py` with POST write helpers, add `grocy_track` + `grocy_pushes` to `db.py`, add preview/push/track routes to `app.py`, extend `receipt_detail.html` and `mappings.html`. No new dependencies.

**Tech Stack:** Python 3, Flask>=3.0, SQLite3, requests>=2.0, pytest>=8.0.

**Spec:** `docs/superpowers/specs/2026-09-24-grocy-push-design.md`

## Global Constraints

- No new dependencies beyond the existing Flask/SQLite/requests/rapidfuzz set.
- Auth via env `GROCY_URL` (with `/api`) + `GROCY_API_KEY`, header `GROCY-API-KEY`.
- Manual review only: no automatic push during upload or import.
- Never create Grocy products in this phase; unlinked items are reported, not created.
- `amount` = Lidl `qty` as-is; unit matching is the user's responsibility.
- New `request.form` access uses `.get()` with flash paths, never bare `request.form["key"]`.
- Tests run from `Lidl Parser/` (`python -m pytest -q`).

## Review Focus

- Item with `qty = 0` — never sent to Grocy, recorded as failed with `bad quantity`.
- Force re-push of an already-pushed receipt — proceeds and may duplicate Grocy entries; flash message must say so.
- Backdate `PUT` fails after a successful add — stock stays with today's date, error recorded, item still counts as pushed.
- Product deleted in Grocy between preview and push — per-item 400 recorded, remaining items continue.
- Add response entry without `stock_id` — backdate skipped, item still counts as pushed.

---

## File Structure

- Modify: `Lidl Parser/grocy.py` — add `_post`, `add_stock`, `set_purchased_date`. Reads stay untouched.
- Modify: `Lidl Parser/db.py` — `products.grocy_track` column, `grocy_pushes` table, idempotent `init_db` backfill.
- Modify: `Lidl Parser/app.py` — `resolve_push_items` helper, `GET /receipts/<rid>/grocy-preview`, `POST /receipts/<rid>/grocy-push`, `POST /mappings/track`.
- Modify: `Lidl Parser/templates/receipt_detail.html` — preview table, push button, force checkbox, already-pushed banner.
- Modify: `Lidl Parser/templates/mappings.html` — Track/Skip toggle per row.
- Create: `Lidl Parser/tests/test_grocy_push.py` — all Phase 2 tests, mocked HTTP, temp SQLite.

Each file keeps one responsibility: `grocy.py` speaks HTTP, `db.py` owns schema, `app.py` owns routes and the resolver, templates render, tests pin behavior.

---

### Task 1: Grocy write path

**Files:**
- Modify: `Lidl Parser/grocy.py`
- Test: `Lidl Parser/tests/test_grocy_push.py`

**Interfaces:**
- Consumes: `get_config`, `_headers`, `TIMEOUT`, `GrocyApiError` (existing in `grocy.py`).
- Produces: `_post(base_url, api_key, path, payload)->Any`, `add_stock(base_url, api_key, product_id, amount, price, note)->list[dict]`, `set_purchased_date(base_url, api_key, entry_id, date_iso)->None`.

- [ ] **Step 1: Write the failing tests**

```python
# Lidl Parser/tests/test_grocy_push.py
def test_add_stock_posts_purchase(monkeypatch):
    import grocy as g
    calls = {}

    class Resp:
        status_code = 200
        text = "ok"
        def json(self):
            return [{"id": 1, "product_id": 7, "stock_id": "44"}]

    def fake_post(url, headers=None, timeout=None, json=None):
        calls["url"] = url
        calls["payload"] = json
        return Resp()

    monkeypatch.setattr(g.requests, "post", fake_post)
    out = g.add_stock("http://x/api", "k", 7, 2.0, 1.29, "Lidl 26-09-05.txt")
    assert out[0]["stock_id"] == "44"
    assert calls["url"] == "http://x/api/stock/products/7/add"
    assert calls["payload"]["transaction_type"] == "purchase"
    assert calls["payload"]["amount"] == 2.0
    assert calls["payload"]["price"] == 1.29
    assert "best_before_date" not in calls["payload"]


def test_add_stock_400_raises(monkeypatch):
    import grocy as g

    class Resp:
        status_code = 400
        text = "Not existing product"

    monkeypatch.setattr(g.requests, "post", lambda *a, **k: Resp())
    try:
        g.add_stock("http://x/api", "k", 999, 1, 1.0, "n")
        assert False, "should raise"
    except g.GrocyApiError as e:
        assert e.status == 400


def test_set_purchased_date_puts(monkeypatch):
    import grocy as g
    calls = {}

    class Resp:
        status_code = 200
        text = "ok"
        def json(self):
            return []

    def fake_put(url, headers=None, timeout=None, json=None):
        calls["url"] = url
        calls["payload"] = json
        return Resp()

    monkeypatch.setattr(g.requests, "put", fake_put)
    g.set_purchased_date("http://x/api", "k", "44", "2026-09-05")
    assert calls["url"] == "http://x/api/stock/entry/44"
    assert calls["payload"] == {"purchased_date": "2026-09-05"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_grocy_push.py -q`
Expected: FAIL with `cannot import name 'add_stock'` (and `set_purchased_date`)

- [ ] **Step 3: Write minimal implementation (append to `Lidl Parser/grocy.py`)**

```python
def _post(base_url, api_key, path, payload):
    url = base_url + path
    last_exc = None
    for _ in range(2):
        try:
            r = requests.post(url, headers=_headers(api_key), timeout=TIMEOUT, json=payload)
            if r.status_code >= 400:
                raise GrocyApiError(r.status_code, r.text)
            return r.json()
        except GrocyApiError:
            raise
        except Exception as e:
            last_exc = e
            time.sleep(1)
    raise GrocyApiError(0, f"unreachable: {last_exc}")


def _put(base_url, api_key, path, payload):
    url = base_url + path
    last_exc = None
    for _ in range(2):
        try:
            r = requests.put(url, headers=_headers(api_key), timeout=TIMEOUT, json=payload)
            if r.status_code >= 400:
                raise GrocyApiError(r.status_code, r.text)
            return r.json() if r.text else None
        except GrocyApiError:
            raise
        except Exception as e:
            last_exc = e
            time.sleep(1)
    raise GrocyApiError(0, f"unreachable: {last_exc}")


def add_stock(base_url, api_key, product_id, amount, price, note):
    payload = {
        "amount": amount,
        "price": price,
        "transaction_type": "purchase",
        "note": note,
    }
    data = _post(base_url, api_key, f"/stock/products/{int(product_id)}/add", payload)
    return data if isinstance(data, list) else []


def set_purchased_date(base_url, api_key, entry_id, date_iso):
    _put(base_url, api_key, f"/stock/entry/{entry_id}", {"purchased_date": date_iso})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_grocy_push.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Skip commit (no git repo in workspace; progress tracked in the plan ledger)**

---

### Task 2: Track flag and push log schema

**Files:**
- Modify: `Lidl Parser/db.py`
- Test: `Lidl Parser/tests/test_grocy_push.py`

**Interfaces:**
- Consumes: existing `SCHEMA`, `init_db`, `get_db` in `db.py`.
- Produces: `products.grocy_track INTEGER DEFAULT 1`, `grocy_pushes(receipt_id PK, pushed_at, pushed_count, skipped_count, errors)` with index-free single-row-per-receipt log.

- [ ] **Step 1: Write the failing tests**

```python
def test_push_schema(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "p.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
    assert "grocy_track" in cols
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "grocy_pushes" in tables
    conn.execute("INSERT INTO receipts (filename) VALUES ('r.txt')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='r.txt'").fetchone()["id"]
    conn.execute("INSERT INTO grocy_pushes (receipt_id, pushed_count, skipped_count, errors) VALUES (?,?,?,?)",
                 (rid, 3, 1, '[]'))
    conn.commit()
    row = conn.execute("SELECT pushed_count FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    assert row["pushed_count"] == 3


def test_track_defaults_to_one(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "p2.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('R','C')")
    conn.commit()
    row = conn.execute("SELECT grocy_track FROM products WHERE raw='R'").fetchone()
    conn.close()
    assert row["grocy_track"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_grocy_push.py::test_push_schema tests/test_grocy_push.py::test_track_defaults_to_one -q`
Expected: FAIL with assertion on `grocy_track`

- [ ] **Step 3: Write minimal implementation**

In `Lidl Parser/db.py` `SCHEMA`, inside the `products` table after the
`grocy_synced_at TEXT` line, add:

```sql
    grocy_track INTEGER DEFAULT 1
```

(change the `grocy_synced_at TEXT` line to end with a comma). After the
`fetch_state` table block (end of `SCHEMA`), add:

```sql
CREATE TABLE IF NOT EXISTS grocy_pushes (
    receipt_id INTEGER PRIMARY KEY REFERENCES receipts(id) ON DELETE CASCADE,
    pushed_at TEXT DEFAULT (datetime('now')),
    pushed_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    errors TEXT DEFAULT '[]'
);
```

In `init_db()`, extend the backfill loop with:

```python
        ("grocy_track", "ALTER TABLE products ADD COLUMN grocy_track INTEGER DEFAULT 1"),
```

(`executescript` covers fresh DBs including the new table; the loop covers
existing DBs. Do NOT put the `grocy_pushes` CREATE inside the loop.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_grocy_push.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Skip commit (no git repo in workspace; progress tracked in the plan ledger)**

---

### Task 3: Resolver, preview, push, and track routes

**Files:**
- Modify: `Lidl Parser/app.py`
- Test: `Lidl Parser/tests/test_grocy_push.py`

**Interfaces:**
- Consumes: `grocy_client.add_stock`, `grocy_client.set_purchased_date`, `grocy_client.get_config`, `grocy_client.fetch_catalog`, `grocy_client.suggest_grocy` (not used here), `grocy_client.find_orphans`, `grocy_client.CONFIG_HINT`; `db.get_db`.
- Produces: `resolve_push_items(receipt_id)->(items, catalog_or_None, grocy_configured, grocy_error)`, `GET /receipts/<rid>/grocy-preview`, `POST /receipts/<rid>/grocy-push`, `POST /mappings/track`.

Resolver item dict keys: `item_id, raw, clean, qty, final, grocy_id, grocy_name, amount, price, status` where status is one of `ready`, `excluded-flagged`, `excluded-no-link`, `orphan-link`, `bad-quantity`. Price rule: `round(final/qty, 2)` if `qty` is nonzero else `0.0` with status `bad-quantity`.

- [ ] **Step 1: Write the failing tests**

```python
def _seed_receipt(dbmod, catalog):
    conn = dbmod.get_db()
    conn.execute("INSERT INTO receipts (filename, receipt_date) VALUES ('26-09-05.txt','2026-09-05')")
    rid = conn.execute("SELECT id FROM receipts WHERE filename='26-09-05.txt'").fetchone()["id"]
    conn.execute("INSERT INTO products (raw, clean, grocy_id, grocy_name, grocy_track) VALUES ('MILK','Milk',7,'Milk',1)")
    conn.execute("INSERT INTO products (raw, clean, grocy_track) VALUES ('SOAP','Soap',0)")
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, qty, unit_price, discount, total, final)
                    VALUES (?, 'MILK','Milk',2,1.29,0.0,2.58,2.58)""", (rid,))
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, qty, unit_price, discount, total, final)
                    VALUES (?, 'SOAP','Soap',1,0.99,0.0,0.99,0.99)""", (rid,))
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, qty, unit_price, discount, total, final)
                    VALUES (?, 'NEWM','New M',1,5.0,0.0,5.0,5.0)""", (rid,))
    conn.execute("""INSERT INTO items (receipt_id, raw, clean, qty, unit_price, discount, total, final)
                    VALUES (?, 'ZERO','Zero',0,1.0,0.0,0.0,0.0)""", (rid,))
    conn.commit()
    conn.close()
    return rid


def test_preview_statuses(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "v.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get(f"/receipts/{rid}/grocy-preview", headers={"Accept": "application/json"})
    assert r.status_code == 200
    body = r.get_json()
    by_raw = {i["raw"]: i for i in body["items"]}
    assert by_raw["MILK"]["status"] == "ready"
    assert by_raw["MILK"]["price"] == 1.29
    assert by_raw["SOAP"]["status"] == "excluded-flagged"
    assert by_raw["NEWM"]["status"] == "excluded-no-link"
    assert by_raw["ZERO"]["status"] == "bad-quantity"


def test_push_adds_backdates_logs(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "w.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    added, edited = [], []
    monkeypatch.setattr(g, "add_stock", lambda *a: (added.append(a), [{"stock_id": "44"}])[1])
    monkeypatch.setattr(g, "set_purchased_date", lambda *a: edited.append(a))
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.post(f"/receipts/{rid}/grocy-push", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert len(added) == 1
    assert added[0][2] == 7
    assert edited and edited[0][3] == "2026-09-05"
    conn = dbmod.get_db()
    log = conn.execute("SELECT pushed_count, skipped_count FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    assert log["pushed_count"] == 1
    assert log["skipped_count"] == 3
    r2 = c.post(f"/receipts/{rid}/grocy-push", follow_redirects=True)
    assert len(added) == 1
    assert "duplicate" in r2.get_data(as_text=True).lower()
    r3 = c.post(f"/receipts/{rid}/grocy-push", data={"force": "1"}, follow_redirects=False)
    assert len(added) == 2


def test_push_item_400_continues(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "e.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    def boom(*a):
        raise g.GrocyApiError(400, "Not existing product")
    monkeypatch.setattr(g, "add_stock", boom)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post(f"/receipts/{rid}/grocy-push", follow_redirects=False)
    conn = dbmod.get_db()
    import json as _json
    log = conn.execute("SELECT pushed_count, errors FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    assert log["pushed_count"] == 0
    assert "MILK" in log["errors"]


def test_backdate_failure_still_pushed(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "b.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    monkeypatch.setattr(g, "add_stock", lambda *a: [{"stock_id": "44"}])
    def boom(*a):
        raise g.GrocyApiError(400, "bad entry")
    monkeypatch.setattr(g, "set_purchased_date", boom)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post(f"/receipts/{rid}/grocy-push", follow_redirects=False)
    conn = dbmod.get_db()
    log = conn.execute("SELECT pushed_count, errors FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    assert log["pushed_count"] == 1
    assert "backdate" in log["errors"].lower() or "MILK" in log["errors"]


def test_missing_stock_id_skips_backdate(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    monkeypatch.setattr(g, "add_stock", lambda *a: [{"id": 1}])
    edited = []
    monkeypatch.setattr(g, "set_purchased_date", lambda *a: edited.append(a))
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post(f"/receipts/{rid}/grocy-push", follow_redirects=False)
    assert edited == []
    conn = dbmod.get_db()
    log = conn.execute("SELECT pushed_count FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    assert log["pushed_count"] == 1


def test_track_toggle(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "t.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('R','C')")
    conn.commit()
    conn.close()
    import app as appmod
    appmod.app.testing = True
    c = appmod.app.test_client()
    c.post("/mappings/track", data={"raw": "R", "track": "0"}, follow_redirects=False)
    conn = dbmod.get_db()
    row = conn.execute("SELECT grocy_track FROM products WHERE raw='R'").fetchone()
    conn.close()
    assert row["grocy_track"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_grocy_push.py -q`
Expected: FAIL with 404s on `/grocy-preview` (routes missing) — `test_preview_statuses` fails first with `KeyError`/`404`

- [ ] **Step 3: Write minimal implementation**

In `Lidl Parser/app.py` add after the `api_grocy_status` route:

```python
def resolve_push_items(receipt_id):
    conn = get_db()
    r = conn.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,)).fetchone()
    items = conn.execute("SELECT * FROM items WHERE receipt_id=?", (receipt_id,)).fetchall()
    prods = {p["raw"]: dict(p) for p in conn.execute("SELECT raw, grocy_id, grocy_name, grocy_track FROM products").fetchall()}
    conn.close()
    grocy_error = None
    try:
        base_url, api_key = grocy_client.get_config()
        catalog = grocy_client.fetch_catalog(base_url, api_key)
        grocy_configured = True
        if catalog.get("stale"):
            grocy_error = "Grocy unreachable — showing cached list."
    except grocy_client.GrocyConfigError:
        return r, [], None, False, grocy_client.CONFIG_HINT
    except Exception as e:
        return r, [], None, True, f"Grocy unreachable: {str(e)[:150]}"
    out = []
    for i in items:
        d = dict(i)
        p = prods.get(d["raw"])
        qty = float(d.get("qty") or 0)
        if not qty:
            d.update({"grocy_id": None, "grocy_name": "", "amount": 0, "price": 0.0, "status": "bad-quantity"})
        elif p is None or p.get("grocy_id") is None:
            d.update({"grocy_id": None, "grocy_name": "", "amount": qty,
                        "price": round(float(d.get("final") or 0) / qty, 2), "status": "excluded-no-link"})
        elif not p.get("grocy_track", 1):
            d.update({"grocy_id": p["grocy_id"], "grocy_name": p.get("grocy_name") or "",
                        "amount": qty, "price": round(float(d.get("final") or 0) / qty, 2),
                        "status": "excluded-flagged"})
        elif int(p["grocy_id"]) not in catalog["by_id"]:
            d.update({"grocy_id": p["grocy_id"], "grocy_name": p.get("grocy_name") or "",
                        "amount": qty, "price": round(float(d.get("final") or 0) / qty, 2),
                        "status": "orphan-link"})
        else:
            d.update({"grocy_id": int(p["grocy_id"]),
                        "grocy_name": str(catalog["by_id"][int(p["grocy_id"])].get("name", "")),
                        "amount": qty, "price": round(float(d.get("final") or 0) / qty, 2),
                        "status": "ready"})
        out.append(d)
    return r, out, catalog, grocy_configured, grocy_error


@app.route("/receipts/<int:rid>/grocy-preview")
def grocy_preview(rid):
    import json as _json
    r, resolved, catalog, configured, err = resolve_push_items(rid)
    if not r:
        return "Not found", 404
    if _json_wants_json():
        return jsonify({"configured": configured, "error": err,
                        "stale": bool(catalog and catalog.get("stale")),
                        "items": resolved})
    return render_template("receipt_detail.html", r=r,
                           items=_receipt_items(rid), preview=resolved,
                           grocy_configured=configured, grocy_error=err,
                           push_log=_push_log(rid))


def _receipt_items(rid):
    conn = get_db()
    rows = conn.execute("SELECT * FROM items WHERE receipt_id=?", (rid,)).fetchall()
    conn.close()
    return rows


def _push_log(rid):
    conn = get_db()
    row = conn.execute("SELECT * FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _json_wants_json():
    return "application/json" in (request.headers.get("Accept", ""))


@app.route("/receipts/<int:rid>/grocy-push", methods=["POST"])
def grocy_push(rid):
    import json as _json
    force = request.form.get("force") == "1"
    conn = get_db()
    existing = conn.execute("SELECT * FROM grocy_pushes WHERE receipt_id=?", (rid,)).fetchone()
    conn.close()
    if existing and not force:
        flash(f"Already pushed at {existing['pushed_at']} — use Force to re-push (may duplicate entries in Grocy).")
        return redirect(url_for("receipt_detail", rid=rid))
    r, resolved, catalog, configured, err = resolve_push_items(rid)
    if not r:
        return "Not found", 404
    if not configured:
        flash(grocy_client.CONFIG_HINT)
        return redirect(url_for("receipt_detail", rid=rid))
    try:
        base_url, api_key = grocy_client.get_config()
    except Exception as e:
        flash(f"Grocy check failed: {e}")
        return redirect(url_for("receipt_detail", rid=rid))
    note = f"Lidl {r['filename']} {r['receipt_date'] or ''}".strip()
    pushed, skipped, errors = 0, 0, []
    for d in resolved:
        if d["status"] != "ready":
            skipped += 1
            continue
        try:
            entries = grocy_client.add_stock(base_url, api_key, d["grocy_id"], d["amount"], d["price"], note)
            stock_id = (entries or [{}])[0].get("stock_id")
            if r["receipt_date"] and stock_id:
                try:
                    grocy_client.set_purchased_date(base_url, api_key, stock_id, r["receipt_date"])
                except Exception as e:
                    errors.append({"raw": d["raw"], "error": f"backdate failed: {str(e)[:150]}"})
            pushed += 1
        except Exception as e:
            errors.append({"raw": d["raw"], "error": str(e)[:200]})
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO grocy_pushes (receipt_id, pushed_at, pushed_count, skipped_count, errors)
           VALUES (?, datetime('now'), ?, ?, ?)""",
        (rid, pushed, skipped, _json.dumps(errors)))
    conn.commit()
    conn.close()
    flash(f"Pushed {pushed}, skipped {skipped}, failed {len([e for e in errors if 'backdate' not in e.get('error','')])}."
          + (" Backdate issues: " + "; ".join(e["raw"] for e in errors if "backdate" in e.get("error", "")) if any("backdate" in e.get("error", "") for e in errors) else ""))
    return redirect(url_for("receipt_detail", rid=rid))


@app.route("/mappings/track", methods=["POST"])
def mapping_track():
    raw = request.form.get("raw", "")
    track = 1 if request.form.get("track") == "1" else 0
    if not raw:
        flash("Missing raw")
        return redirect(url_for("mappings"))
    conn = get_db()
    conn.execute("UPDATE products SET grocy_track=? WHERE raw=?", (track, raw))
    conn.commit()
    conn.close()
    flash(f"{'Tracking' if track else 'Skipping'} {raw} for Grocy push")
    return redirect(url_for("mappings", q=request.form.get("_q", "")))
```

Also update the existing `receipt_detail` route to pass preview context:

```python
@app.route("/receipts/<int:rid>")
def receipt_detail(rid):
    conn = get_db()
    r = conn.execute("SELECT * FROM receipts WHERE id=?", (rid,)).fetchone()
    items = conn.execute("SELECT * FROM items WHERE receipt_id=?", (rid,)).fetchall()
    conn.close()
    if not r:
        return "Not found", 404
    try:
        _, preview, catalog, configured, err = resolve_push_items(rid)
    except Exception:
        preview, configured, err, catalog = [], False, None, None
    return render_template("receipt_detail.html", r=r, items=items, preview=preview,
                           grocy_configured=configured, grocy_error=err,
                           push_log=_push_log(rid))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_grocy_push.py -q`
Expected: PASS (11 passed: 3 Task-1 + 2 Task-2 + 6 Task-3)

- [ ] **Step 5: Skip commit (no git repo in workspace; progress tracked in the plan ledger)**

---

### Task 4: Receipt and mappings UI

**Files:**
- Modify: `Lidl Parser/templates/receipt_detail.html`
- Modify: `Lidl Parser/templates/mappings.html`
- Test: `Lidl Parser/tests/test_grocy_push.py`

**Interfaces:**
- Consumes: `preview` (list of resolver dicts), `grocy_configured`, `grocy_error`, `push_log` from Task 3; per-row `grocy_track` needs adding to the mappings query — extend Task 3's `mappings()` `SELECT *` (already selects all columns, so `grocy_track` arrives automatically; template reads `r["grocy_track"]` with default tracked when NULL).

- [ ] **Step 1: Write the failing test**

```python
def test_receipt_page_shows_preview_and_push(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "u.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    catalog = {"products": [{"id": 7, "name": "Milk"}], "by_id": {7: {"id": 7, "name": "Milk"}},
               "names": ["Milk"], "barcode_to_ids": {}, "cached_at": 0, "stale": False}
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: catalog)
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    rid = _seed_receipt(dbmod, catalog)
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get(f"/receipts/{rid}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Grocy" in html
    assert "grocy-push" in html
    assert "ready" in html
    assert "excluded-flagged" in html


def test_mappings_shows_track_toggle(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "m.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, grocy_track) VALUES ('R','C',0)")
    conn.commit()
    conn.close()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                                                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.setenv("GROCY_URL", "http://x/api")
    monkeypatch.setenv("GROCY_API_KEY", "k")
    appmod.app.testing = True
    c = appmod.app.test_client()
    r = c.get("/mappings")
    assert r.status_code == 200
    assert "mappings/track" in r.get_data(as_text=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_grocy_push.py::test_receipt_page_shows_preview_and_push tests/test_grocy_push.py::test_mappings_shows_track_toggle -q`
Expected: FAIL with assertion on `grocy-push` (template lacks it)

- [ ] **Step 3: Write minimal implementation**

In `Lidl Parser/templates/receipt_detail.html`, after the existing items
table (`{% endfor %}` + `</table>`), append:

```html
<h2>Grocy push</h2>
{% if push_log %}
<p>Already pushed at {{ push_log["pushed_at"] }} — {{ push_log["pushed_count"] }} pushed, {{ push_log["skipped_count"] }} skipped.</p>
{% endif %}
{% if not grocy_configured %}
<p style="background:#ffe">Grocy not configured — set GROCY_URL (with /api) + GROCY_API_KEY to enable push.</p>
{% elif grocy_error %}
<p style="background:#fee">Grocy error — {{ grocy_error }}</p>
{% endif %}
{% if preview %}
<table><tr><th>Raw</th><th>Grocy</th><th>Amount</th><th>Price</th><th>Status</th></tr>
{% for p in preview %}<tr>
<td>{{ p["raw"] }}</td>
<td>{% if p["grocy_id"] %}#{{ p["grocy_id"] }} {{ p["grocy_name"] }}{% else %}—{% endif %}</td>
<td>{{ p["amount"] }}</td><td>{{ "%.2f"|format(p["price"]) }}</td><td>{{ p["status"] }}</td>
</tr>{% endfor %}
</table>
<form method="post" action="/receipts/{{ r["id"] }}/grocy-push">
{% if push_log %}<label><input type="checkbox" name="force" value="1"> Force re-push (may duplicate entries in Grocy)</label>{% endif %}
<button>Push to Grocy</button>
</form>
{% endif %}
```

In `Lidl Parser/templates/mappings.html`, in the Grocy `<td>` cell, after
the Unlink form block, add:

```html
<form method="post" action="/mappings/track" style="display:inline">
<input type="hidden" name="raw" value="{{ r["raw"] }}">
<input type="hidden" name="_q" value="{{ q }}">
{% if r["grocy_track"] == 0 %}
<input type="hidden" name="track" value="1"><button>Track</button>
{% else %}
<input type="hidden" name="track" value="0"><button>Skip</button>
{% endif %}
</form>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_grocy_push.py -q`
Expected: PASS (13 passed)

Run: `python -m pytest -q`
Expected: PASS (full suite green, Phase 1 + Phase 2)

- [ ] **Step 5: Skip commit (no git repo in workspace; progress tracked in the plan ledger)**
