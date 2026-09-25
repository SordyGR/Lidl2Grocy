# Grocy Product Link Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Link Lidl Parser products to existing Grocy products with manual review in Mappings UI.

**Architecture:** New read-only `grocy.py` client (requests, 5-min cache) + `grocy_id` columns on `products` + Mappings enrichment with Link/Unlink routes. No Grocy writes in Phase 1.

**Tech Stack:** Python 3, Flask>=3.0, SQLite3, rapidfuzz>=3.0, requests>=2.0, pytest>=8.0.

**Spec:** `docs/superpowers/specs/2026-09-24-grocy-link-design.md`

## Global Constraints

- Add only `requests>=2.0`; no other new dependencies.
- Config via env `GROCY_URL` (must include `/api`, e.g. `https://grocy.example.com/api`) and `GROCY_API_KEY`.
- Auth header is `GROCY-API-KEY` on every Grocy request.
- HTTP timeout is 10 seconds, one retry after 1s on network error.
- Manual review only: Phase 1 performs zero Grocy writes.
- Never log full API key; cache key uses key hash only.
- Follow existing patterns in `Lidl Parser/db.py`, `Lidl Parser/app.py`, `Lidl Parser/migrate.py`.

## Review Focus

- `GROCY_URL` with trailing slash vs without vs missing `/api` suffix — UI still renders, `grocy_status` reports `configured=false` with hint, no crash.
- Barcode with surrounding spaces or dashes (`" 520123 "` vs `"520123"`) — stripped before exact lookup, still scores 100.
- Greek names with different case/accents (`"ΓΑΛΑ"` vs `"gala fresh"`) — normalize upper/strip before fuzzy, threshold >=80.
- One barcode mapped to two Grocy ids — UI shows both with `barcode-collision`, no auto-pick.
- `grocy_id` linked then deleted in Grocy — next catalog refresh lists it in `orphans`, link is kept, push contract skips orphans in Phase 2.

---

## File Structure

- Create: `Lidl Parser/grocy.py` — config, errors, `_get`, `list_products`, `list_barcodes`, `fetch_catalog` (5-min cache), `suggest_grocy`, `find_orphans`.
- Modify: `Lidl Parser/db.py` — `SCHEMA` gains `grocy_id INTEGER`, `grocy_name TEXT`, `grocy_synced_at TEXT` + index; `init_db` stays idempotent.
- Modify: `Lidl Parser/app.py` — `mappings()` enrichment, `POST /mappings/link`, `POST /mappings/unlink`, `GET /api/grocy_status`.
- Modify: `Lidl Parser/templates/mappings.html` — linked badge, candidate select, datalist search, unconfigured banner.
- Modify: `Lidl Parser/requirements.txt` — append `requests>=2.0`.
- Create: `Lidl Parser/tests/test_grocy_match.py` — mocked client + matching + link round-trip tests.

Each file has one responsibility: `grocy.py` talks to Grocy, `db.py` owns schema, `app.py` owns routes, template owns rendering, tests pin behavior.

---

### Task 1: Grocy read-only client

**Files:**
- Create: `Lidl Parser/grocy.py`
- Test: `Lidl Parser/tests/test_grocy_match.py`

**Interfaces:**
- Consumes: env `GROCY_URL`, `GROCY_API_KEY`; `requests` library.
- Produces: `GrocyConfigError`, `GrocyApiError(status, body)`, `get_config()->tuple[str,str]`, `list_products(base_url, api_key)->list[dict]`, `list_barcodes(base_url, api_key)->list[dict]`, `fetch_catalog(base_url, api_key)->dict` with keys `products`, `by_id`, `names`, `barcode_to_ids`, `cached_at`.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_grocy_match.py
def test_get_config_missing_raises():
    import os
    from grocy import GrocyConfigError, get_config
    old_url = os.environ.pop("GROCY_URL", None)
    old_key = os.environ.pop("GROCY_API_KEY", None)
    try:
        try:
            get_config()
            assert False, "should raise"
        except GrocyConfigError:
            assert True
    finally:
        if old_url is not None:
            os.environ["GROCY_URL"] = old_url
        if old_key is not None:
            os.environ["GROCY_API_KEY"] = old_key
```

- [ ] **Step 1b: Pin Review Focus — URL normalization and orphans**

```python
def test_url_trailing_slash_normalized():
    import os
    from grocy import get_config
    os.environ["GROCY_URL"] = "https://x.example.com/api/"
    os.environ["GROCY_API_KEY"] = "k"
    base, _ = get_config()
    assert base == "https://x.example.com/api"


def test_find_orphans():
    from grocy import find_orphans
    catalog = {"by_id": {5: {"id": 5}}}
    assert find_orphans([5, 99], catalog) == [99]
```

- [ ] **Step 2: Run test to verify it fails (Task 1)**

Run: `pytest tests/test_grocy_match.py::test_get_config_missing_raises -v`
Expected: FAIL with "No module named 'grocy'" or "cannot import name"

- [ ] **Step 3: Write minimal implementation**

```python
"""Read-only Grocy API client for Phase 1 product linking."""
import os
import time
import hashlib
import requests

TIMEOUT = 10
_CACHE = {"key": None, "at": 0.0, "catalog": None}
CACHE_TTL = 300


class GrocyConfigError(Exception):
    pass


class GrocyApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"Grocy API {status}: {body[:200]}")
        self.status = status
        self.body = body


def get_config():
    base_url = os.environ.get("GROCY_URL", "").strip().rstrip("/")
    api_key = os.environ.get("GROCY_API_KEY", "").strip()
    if not base_url or not api_key:
        raise GrocyConfigError("Set GROCY_URL (with /api) + GROCY_API_KEY")
    return base_url, api_key


def _headers(api_key):
    return {"GROCY-API-KEY": api_key, "Accept": "application/json"}


def _get(base_url, api_key, path):
    url = base_url + path
    last_exc = None
    for _ in range(2):
        try:
            r = requests.get(url, headers=_headers(api_key), timeout=TIMEOUT)
            if r.status_code >= 400:
                raise GrocyApiError(r.status_code, r.text)
            return r.json()
        except GrocyApiError:
            raise
        except Exception as e:
            last_exc = e
            time.sleep(1)
    raise GrocyApiError(0, f"unreachable: {last_exc}")


def list_products(base_url, api_key):
    data = _get(base_url, api_key, "/objects/products")
    return data if isinstance(data, list) else []


def list_barcodes(base_url, api_key):
    try:
        data = _get(base_url, api_key, "/objects/product_barcodes")
    except GrocyApiError as e:
        if e.status == 404:
            return []
        raise
    return data if isinstance(data, list) else []


def fetch_catalog(base_url, api_key):
    h = hashlib.sha256(api_key.encode()).hexdigest()[:12]
    key = (base_url, h)
    now = time.time()
    if _CACHE["key"] == key and (now - _CACHE["at"]) < CACHE_TTL:
        return _CACHE["catalog"]
    products = list_products(base_url, api_key)
    barcodes = list_barcodes(base_url, api_key)
    by_id = {int(p["id"]): p for p in products if "id" in p}
    names = [str(p.get("name", "")) for p in products]
    barcode_to_ids = {}
    for b in barcodes:
        code = str(b.get("barcode", "")).strip()
        pid = b.get("product_id")
        if not code or pid is None:
            continue
        barcode_to_ids.setdefault(code, []).append(int(pid))
    catalog = {
        "products": products,
        "by_id": by_id,
        "names": names,
        "barcode_to_ids": barcode_to_ids,
        "cached_at": now,
    }
    _CACHE.update({"key": key, "at": now, "catalog": catalog})
    return catalog
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_grocy_match.py::test_get_config_missing_raises -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/grocy.py" "Lidl Parser/tests/test_grocy_match.py"
git commit -m "feat(grocy): add read-only client with config and catalog cache"
```

---

### Task 2: Suggest matching (barcode exact + fuzzy)

**Files:**
- Modify: `Lidl Parser/grocy.py`
- Test: `Lidl Parser/tests/test_grocy_match.py`

**Interfaces:**
- Consumes: `fetch_catalog()->dict` from Task 1; `rapidfuzz.process`, `rapidfuzz.fuzz`.
- Produces: `suggest_grocy(clean, barcode, catalog)->list[dict]` each `{grocy_id:int, grocy_name:str, reason:str, score:int}`, `find_orphans(linked_ids, catalog)->list[int]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_suggest_barcode_exact_wins():
    from grocy import suggest_grocy
    catalog = {
        "products": [{"id": 7, "name": "Fresh Milk"}, {"id": 9, "name": "Other"}],
        "by_id": {7: {"id": 7, "name": "Fresh Milk"}, 9: {"id": 9, "name": "Other"}},
        "names": ["Fresh Milk", "Other"],
        "barcode_to_ids": {"520123": [7]},
        "cached_at": 0,
    }
    out = suggest_grocy("TOTALLY DIFFERENT", " 520123 ", catalog)
    assert out[0]["grocy_id"] == 7
    assert out[0]["score"] == 100
    assert out[0]["reason"] == "barcode"


def test_suggest_fuzzy_threshold():
    from grocy import suggest_grocy
    catalog = {
        "products": [{"id": 3, "name": "Feta Cheese 400g"}],
        "by_id": {3: {"id": 3, "name": "Feta Cheese 400g"}},
        "names": ["Feta Cheese 400g"],
        "barcode_to_ids": {},
        "cached_at": 0,
    }
    out = suggest_grocy("Feta Cheese 400g", None, catalog)
    assert out and out[0]["grocy_id"] == 3
    out2 = suggest_grocy("zzzz unrelated qqq", None, catalog)
    assert out2 == []


def test_suggest_collision():
    from grocy import suggest_grocy
    catalog = {
        "products": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        "by_id": {1: {"id": 1, "name": "A"}, 2: {"id": 2, "name": "B"}},
        "names": ["A", "B"],
        "barcode_to_ids": {"111": [1, 2]},
        "cached_at": 0,
    }
    out = suggest_grocy("whatever", "111", catalog)
    assert len(out) == 2
    assert all(o["reason"] == "barcode-collision" for o in out)


def test_suggest_greek_case_insensitive():
    from grocy import suggest_grocy
    catalog = {
        "products": [{"id": 11, "name": "Milk Fresh"}],
        "by_id": {11: {"id": 11, "name": "Milk Fresh"}},
        "names": ["Milk Fresh"],
        "barcode_to_ids": {},
        "cached_at": 0,
    }
    out = suggest_grocy("milk fresh", None, catalog)
    assert out and out[0]["grocy_id"] == 11
    assert out[0]["reason"] == "fuzzy"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_grocy_match.py -v`
Expected: FAIL with "cannot import name 'suggest_grocy'"

- [ ] **Step 3: Write minimal implementation (append to `Lidl Parser/grocy.py`)**

```python
from rapidfuzz import process, fuzz


def _norm(s):
    if s is None:
        return ""
    return str(s).upper().replace(".", "").replace(",", "").strip()


def suggest_grocy(clean, barcode, catalog):
    by_id = catalog.get("by_id", {})
    bmap = catalog.get("barcode_to_ids", {})
    if barcode is not None:
        code = str(barcode).strip()
        if code and code in bmap:
            ids = bmap[code]
            if len(ids) == 1:
                pid = ids[0]
                return [{"grocy_id": pid, "grocy_name": str(by_id.get(pid, {}).get("name", "")), "reason": "barcode", "score": 100}]
            return [{"grocy_id": pid, "grocy_name": str(by_id.get(pid, {}).get("name", "")), "reason": "barcode-collision", "score": 100} for pid in ids]
    names = catalog.get("names", [])
    if not names or not _norm(clean):
        return []
    # map name -> id (first occurrence)
    name_to_id = {}
    for p in catalog.get("products", []):
        n = str(p.get("name", ""))
        if n not in name_to_id:
            name_to_id[n] = int(p["id"])
    hits = process.extract(_norm(clean), [_norm(n) for n in names], scorer=fuzz.token_sort_ratio, limit=3)
    out = []
    for norm_hit, score, idx in hits:
        if score >= 80:
            gname = names[idx]
            out.append({"grocy_id": name_to_id[gname], "grocy_name": gname, "reason": "fuzzy", "score": int(score)})
    return out


def find_orphans(linked_ids, catalog):
    by_id = catalog.get("by_id", {})
    return [i for i in linked_ids if i not in by_id]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_grocy_match.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/grocy.py" "Lidl Parser/tests/test_grocy_match.py"
git commit -m "feat(grocy): add barcode-exact and fuzzy suggest with collision handling"
```

---

### Task 3: DB migration for link columns

**Files:**
- Modify: `Lidl Parser/db.py:9-67`
- Test: `Lidl Parser/tests/test_grocy_match.py`

**Interfaces:**
- Consumes: existing `SCHEMA`, `init_db`, `get_db`.
- Produces: `products.grocy_id INTEGER NULL`, `products.grocy_name TEXT NULL`, `products.grocy_synced_at TEXT NULL`, index `idx_products_grocy`.

- [ ] **Step 1: Write the failing test**

```python
def test_products_has_grocy_columns(tmp_path):
    import sqlite3
    import db as dbmod
    dbmod.DB_PATH = str(tmp_path / "t.db")
    dbmod.init_db()
    conn = dbmod.get_db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
    conn.close()
    assert "grocy_id" in cols
    assert "grocy_name" in cols
    assert "grocy_synced_at" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_grocy_match.py::test_products_has_grocy_columns -v`
Expected: FAIL with assertion on `grocy_id`

- [ ] **Step 3: Write minimal implementation**

In `Lidl Parser/db.py`, extend `SCHEMA` string (after `updated_at` line inside `products` table, keep trailing comma valid — add columns before the closing `);`):

```sql
    grocy_id INTEGER,
    grocy_name TEXT,
    grocy_synced_at TEXT
```

And after the `products` table block add:

```sql
CREATE INDEX IF NOT EXISTS idx_products_grocy ON products(grocy_id);
```

Also make `init_db()` idempotent for existing DBs by executing after `executescript`:

```python
def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    for col, ddl in (
        ("grocy_id", "ALTER TABLE products ADD COLUMN grocy_id INTEGER"),
        ("grocy_name", "ALTER TABLE products ADD COLUMN grocy_name TEXT"),
        ("grocy_synced_at", "ALTER TABLE products ADD COLUMN grocy_synced_at TEXT"),
    ):
        cur = conn.execute("PRAGMA table_info(products)")
        have = [r[1] for r in cur.fetchall()]
        if col not in have:
            conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_grocy ON products(grocy_id)")
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_grocy_match.py::test_products_has_grocy_columns -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/db.py" "Lidl Parser/tests/test_grocy_match.py"
git commit -m "feat(db): add grocy link columns to products"
```

---

### Task 4: Flask routes (preview, link, unlink, status)

**Files:**
- Modify: `Lidl Parser/app.py:122-145` (`mappings`, add `mapping_link`, `mapping_unlink`, `api_grocy_status`)
- Test: `Lidl Parser/tests/test_grocy_match.py`

**Interfaces:**
- Consumes: `grocy.get_config`, `grocy.fetch_catalog`, `grocy.suggest_grocy`, `grocy.find_orphans`, `grocy.GrocyConfigError`, `grocy.GrocyApiError`; `db.get_db`.
- Produces: `GET /mappings` with `grocy_configured`, `grocy_names`, per-row `suggestions`; `POST /mappings/link`; `POST /mappings/unlink`; `GET /api/grocy_status->{configured,product_count,cached_at,orphans}`.

- [ ] **Step 1: Write the failing test**

```python
def test_grocy_status_unconfigured():
    import os
    old_url = os.environ.pop("GROCY_URL", None)
    old_key = os.environ.pop("GROCY_API_KEY", None)
    try:
        import app as appmod
        appmod.app.testing = True
        c = appmod.app.test_client()
        r = c.get("/api/grocy_status")
        assert r.status_code == 200
        assert r.get_json()["configured"] is False
    finally:
        if old_url is not None:
            os.environ["GROCY_URL"] = old_url
        if old_key is not None:
            os.environ["GROCY_API_KEY"] = old_key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_grocy_match.py::test_grocy_status_unconfigured -v`
Expected: FAIL with "has no attribute" or 404

- [ ] **Step 3: Write minimal implementation**

In `Lidl Parser/app.py` add import at top:

```python
import grocy as grocy_client
```

Replace `mappings()` body query `SELECT * FROM products` with explicit columns including grocy fields, and after fetching rows add:

```python
try:
    base_url, api_key = grocy_client.get_config()
    catalog = grocy_client.fetch_catalog(base_url, api_key)
    grocy_configured = True
    grocy_names = catalog["names"][:2000]
except Exception:
    catalog = None
    grocy_configured = False
    grocy_names = []
```

For each row dict, attach `suggestions = [] if row["grocy_id"] else grocy_client.suggest_grocy(row["clean"], row["barcode"], catalog)` when configured. Pass `grocy_configured` and `grocy_names` to `render_template`.

Add routes:

```python
@app.route("/mappings/link", methods=["POST"])
def mapping_link():
    raw = request.form["raw"]
    try:
        gid = int(request.form["grocy_id"])
    except (KeyError, ValueError):
        flash("Missing grocy_id")
        return redirect(url_for("mappings"))
    gname = request.form.get("grocy_name", "").strip()
    try:
        base_url, api_key = grocy_client.get_config()
        catalog = grocy_client.fetch_catalog(base_url, api_key)
        if gid not in catalog["by_id"]:
            flash(f"Grocy #{gid} not found (stale list)")
            return redirect(url_for("mappings"))
        if not gname:
            gname = str(catalog["by_id"][gid].get("name", ""))
    except Exception as e:
        flash(f"Grocy check failed: {e}")
        return redirect(url_for("mappings"))
    conn = get_db()
    conn.execute("UPDATE products SET grocy_id=?, grocy_name=?, grocy_synced_at=datetime('now') WHERE raw=?", (gid, gname, raw))
    conn.commit()
    conn.close()
    flash(f"Linked {raw} -> Grocy #{gid}")
    return redirect(url_for("mappings", q=request.form.get("_q", "")))


@app.route("/mappings/unlink", methods=["POST"])
def mapping_unlink():
    raw = request.form["raw"]
    conn = get_db()
    conn.execute("UPDATE products SET grocy_id=NULL, grocy_name=NULL, grocy_synced_at=NULL WHERE raw=?", (raw,))
    conn.commit()
    conn.close()
    flash(f"Unlinked {raw}")
    return redirect(url_for("mappings"))


@app.route("/api/grocy_status")
def api_grocy_status():
    try:
        base_url, api_key = grocy_client.get_config()
    except Exception:
        return jsonify({"configured": False})
    try:
        catalog = grocy_client.fetch_catalog(base_url, api_key)
        conn = get_db()
        linked = [r[0] for r in conn.execute("SELECT grocy_id FROM products WHERE grocy_id IS NOT NULL").fetchall()]
        conn.close()
        orphans = grocy_client.find_orphans(linked, catalog)
        return jsonify({"configured": True, "product_count": len(catalog["products"]), "cached_at": catalog["cached_at"], "orphans": orphans})
    except Exception as e:
        return jsonify({"configured": True, "error": str(e)[:200]}), 502
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_grocy_match.py::test_grocy_status_unconfigured -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/app.py" "Lidl Parser/tests/test_grocy_match.py"
git commit -m "feat(app): add grocy link routes and mappings enrichment"
```

---

### Task 5: Mappings UI + deps + link round-trip

**Files:**
- Modify: `Lidl Parser/templates/mappings.html:1-56`
- Modify: `Lidl Parser/requirements.txt:1-6`
- Test: `Lidl Parser/tests/test_grocy_match.py`

**Interfaces:**
- Consumes: `grocy_configured`, `grocy_names`, per-row `suggestions`, `grocy_id`, `grocy_name` from Task 4.
- Produces: rendered Link/Unlink controls, unconfigured banner, datalist search.

- [ ] **Step 1: Write the failing test**

```python
def test_link_unlink_roundtrip(tmp_path):
    import db as dbmod
    dbmod.DB_PATH = str(tmp_path / "t2.db")
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean, category) VALUES (?,?,?)", ("RAW1", "Clean 1", "Misc"))
    conn.commit()
    conn.close()
    import app as appmod
    appmod.app.testing = True
    import grocy as g
    g.fetch_catalog = lambda *a: {"products": [{"id": 5, "name": "Clean 1"}], "by_id": {5: {"id": 5, "name": "Clean 1"}}, "names": ["Clean 1"], "barcode_to_ids": {}, "cached_at": 0}
    import os
    os.environ["GROCY_URL"] = "http://x/api"
    os.environ["GROCY_API_KEY"] = "k"
    # point app db to tmp by patching get_db path via dbmod
    import db as d2
    appmod.get_db = d2.get_db
    c = appmod.app.test_client()
    r = c.post("/mappings/link", data={"raw": "RAW1", "grocy_id": "5", "grocy_name": "Clean 1", "_q": ""}, follow_redirects=False)
    assert r.status_code in (302, 303)
    conn = dbmod.get_db()
    row = conn.execute("SELECT grocy_id, grocy_name FROM products WHERE raw='RAW1'").fetchone()
    conn.close()
    assert row["grocy_id"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_grocy_match.py::test_link_unlink_roundtrip -v`
Expected: FAIL (link route missing or template error)

- [ ] **Step 3: Write minimal implementation**

Append to `Lidl Parser/requirements.txt`:

```
requests>=2.0
```

In `Lidl Parser/templates/mappings.html` after the `<h1>` add:

```html
{% if not grocy_configured %}
<p style="background:#ffe">Grocy not configured — set GROCY_URL (with /api) + GROCY_API_KEY to enable linking.</p>
{% endif %}
<datalist id="grocy-names">
{% for n in grocy_names %}<option value="{{ n }}">{% endfor %}
</datalist>
```

Extend the mappings table header row with `<th>Grocy</th>` and per-row cell:

```html
<td>
{% if r["grocy_id"] %}
<span>Grocy #{{ r["grocy_id"] }} {{ r["grocy_name"] }}</span>
<form method="post" action="/mappings/unlink" style="display:inline">
<input type="hidden" name="raw" value="{{ r["raw"] }}">
<button>Unlink</button>
</form>
{% else %}
<form method="post" action="/mappings/link">
<input type="hidden" name="raw" value="{{ r["raw"] }}">
<input type="hidden" name="_q" value="{{ q }}">
{% if r["suggestions"] %}
<select name="grocy_id">
{% for s in r["suggestions"] %}
<option value="{{ s["grocy_id"] }}">#{{ s["grocy_id"] }} {{ s["grocy_name"] }} ({{ s["reason"] }} {{ s["score"] }})</option>
{% endfor %}
</select>
{% else %}
<input name="grocy_id" placeholder="Grocy id" size="6">
{% endif %}
<input name="grocy_name" placeholder="Grocy name (optional)" size="14" list="grocy-names">
<button>Link</button>
</form>
{% endif %}
</td>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_grocy_match.py -v`
Expected: PASS (all 6 tests)

Run: `pytest -q`
Expected: PASS (no regressions in `test_fetch_*`)

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/templates/mappings.html" "Lidl Parser/requirements.txt" "Lidl Parser/tests/test_grocy_match.py"
git commit -m "feat(ui): add grocy link controls to mappings"
```
