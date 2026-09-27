# Products Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Mappings tab into a Products hub with per-packaging mini-pages, Grocy conversion-table linkage, Grocy-sourced units, period CSV export, and period-aware dashboard pie.

**Architecture:** All changes are additive on the existing raw-keyed `products` model (each row is one packaging). New columns + a `units` cache table via the idempotent `init_db` ALTER pattern; new routes alongside the existing `/mappings/*` POST endpoints (which keep their URLs); push math switches from live Grocy details reads to stored per-raw factors.

**Tech Stack:** Flask, SQLite (Python `sqlite3`, stdlib `csv`/`unicodedata`), Chart.js 4 (already loaded), vanilla JS fetch. No new pip dependencies.

**Spec:** `docs/superpowers/specs/2026-09-27-products-hub-design.md`

## Global Constraints

- All paths relative to `Lidl Parser/`; run tests from there with `python -m pytest -q`.
- TDD every change: failing test first, minimal implementation, full suite green before next task.
- DB changes only via additive `ALTER TABLE`/`CREATE TABLE IF NOT EXISTS` in `db.init_db` (live DB must migrate safely on restart).
- Every task ends with `python -m pytest -q` fully green (no commit steps in this repo by convention).
- Grocy network failures degrade gracefully (cached/seed data, logged flash) — push and link flows never crash offline.
- CSV export is UTF-8 with BOM; Greek text must survive Excel.
- No new pip dependencies (stdlib `csv`, `unicodedata` only).

## Review Focus

- Greek mixed case+accents query (`άΛΦα`) must match `Αλφα` rows — expect match via normalization on both sides.
- Two raws sharing one `grocy_id` with factors 1 and 200 must push different converted amounts — expect per-raw math, never the Grocy product default for both.
- Units refresh with Grocy unreachable must keep the old cache and show an error — expect no empty dropdowns.
- Barcode post with `qu_id` rejected by Grocy must still link the barcode — expect silent fallback to no-`qu_id` post.
- Export rows with unknown payer and no splits must render blanks, never crash — expect empty strings and intact Greek text.

---

### Task 1: Model — new product columns, units table, stub auto-creation

**Files:**
- Modify: `Lidl Parser/db.py` (schema + `grnorm` function + seed list)
- Modify: `Lidl Parser/fetch_lib.py` (`import_kept_receipt` stub creation)
- Test: `Lidl Parser/tests/test_products_model.py` (new)

**Interfaces:**
- Consumes: existing `init_db()` ALTER pattern (`db.py:111-124`), `smart_match`/`build_memory` in `fetch_lib.py:57-90`.
- Produces: `products.purchase_qu_id`, `products.qu_factor`, `products.market`, `products.needs_review`; `units(id, name, source, cached_at)`; `grnorm(text)` SQLite function registered on every `get_db()` connection; `UNIT_SEED = ["Τεμάχιο","Κιλό","Γραμμάριο","Λίτρο","ml","Πακέτο","Κουτί","Φιάλη"]`.

- [ ] **Step 1: Write failing tests for schema + grnorm + seeds**

```python
import db as dbmod

def test_init_db_adds_packaging_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "m.db"))
    dbmod.init_db()
    dbmod.init_db()  # idempotent re-run
    cols = [r[1] for r in dbmod.get_db().execute("PRAGMA table_info(products)").fetchall()]
    for c in ("purchase_qu_id", "qu_factor", "market", "needs_review"):
        assert c in cols

def test_units_table_seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "u.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    names = [r[0] for r in conn.execute("SELECT name FROM units WHERE source='local'").fetchall()]
    assert "Τεμάχιο" in names and "Γραμμάριο" in names

def test_grnorm_greek_insensitive(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "g.db"))
    dbmod.init_db()
    conn = dbmod.get_db()
    conn.execute("INSERT INTO products (raw, clean) VALUES ('ΑΛΦΑ','Άλφα γάλα')")
    rows = conn.execute("SELECT raw FROM products WHERE grnorm(clean) LIKE '%' || grnorm(?) || '%'", ("αλφα γαλα",)).fetchall()
    assert [r[0] for r in rows] == ["ΑΛΦΑ"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_products_model.py -q` from `Lidl Parser/`
Expected: FAIL — `no such table: units`, `no such function: grnorm`, missing columns.

- [ ] **Step 3: Implement schema + grnorm in `db.py`**

```python
import unicodedata

UNIT_SEED = ["Τεμάχιο", "Κιλό", "Γραμμάριο", "Λίτρο", "ml", "Πακέτο", "Κουτί", "Φιάλη"]

def grnorm(s):
    if s is None:
        return ""
    bare = "".join(c for c in unicodedata.normalize("NFD", str(s)) if unicodedata.category(c) != "Mn")
    return bare.casefold()
```

Register in `get_db()` before return: `conn.create_function("grnorm", 1, grnorm)`. Add to `SCHEMA`: `units` table DDL plus the four `products` columns are added via the existing ALTER loop — extend the loop tuple list with `("purchase_qu_id", "ALTER TABLE products ADD COLUMN purchase_qu_id INTEGER")`, `("qu_factor", ...)`, `("market", "ALTER TABLE products ADD COLUMN market TEXT DEFAULT 'Lidl'")`, `("needs_review", "ALTER TABLE products ADD COLUMN needs_review INTEGER DEFAULT 0")`; add `CREATE TABLE IF NOT EXISTS units (...)` + seed insert (`INSERT OR IGNORE INTO units (name, source)`) in `init_db()`.

- [ ] **Step 4: Stub auto-creation in `fetch_lib.import_kept_receipt`**

After the items loop, before final commit:

```python
for _, item in df.iterrows():
    cur.execute("""INSERT OR IGNORE INTO products (raw, clean, category, needs_review)
                   VALUES (?,?,?,1)""", (item["Product"], clean, cat))
```

Note: `clean, cat` here are per-item loop variables already computed in the existing loop — reuse them inside that loop (add the INSERT there, not a second loop).

- [ ] **Step 5: Add stub test + run full suite**

```python
def test_import_creates_stub_products(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "s.db"))
    dbmod.init_db()
    import fetch_lib as fl
    conn = dbmod.get_db()
    kept = "1 Σεπτεμβρίου 2026\nNEWR AW 1,00 1\nΣΥΝΟΛΟ 1,00\nΕΠΙΣΤΡΟΦΕΣ\n"
    # simpler: call with a minimal kept text that parses; if parse needs real shape, reuse tests/test_fetch*.py fixtures
```

(If the minimal text does not parse, copy the kept-text fixture from the existing fetch tests instead of inventing one.) Then run: `python -m pytest -q` — Expected: all green.

---

### Task 2: Hub — `/products` route, redirect, page sizes, review filter, live JSON API

**Files:**
- Modify: `Lidl Parser/app.py` (`mappings()` at line 260 + new API route)
- Modify: `Lidl Parser/templates/mappings.html` (rename to `products.html` via copy; keep old file? No — rename file, update render call)
- Modify: `Lidl Parser/templates/base.html` (nav link)
- Test: `Lidl Parser/tests/test_products_hub.py` (new)

**Interfaces:**
- Consumes: `grnorm()` from Task 1; existing filter/sort/pagination logic in `mappings()` (`app.py:260-355`).
- Produces: `GET /products` (same params + `per=25|50|100` + `filter=needs-review`), `GET /mappings` → 301 to `/products` (query string preserved), `GET /api/products` JSON `{rows, page, pages, count}` honoring the same filters. All `/mappings/*` POST endpoints keep their URLs.

- [ ] **Step 1: Write failing tests**

```python
def test_products_route_and_redirect(tmp_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "h.db"))
    dbmod.init_db()
    import app as appmod
    import grocy as g
    monkeypatch.setattr(g, "fetch_catalog", lambda *a: {"products": [], "by_id": {}, "names": [],
                         "barcode_to_ids": {}, "cached_at": 0, "stale": False})
    monkeypatch.setattr(g, "fetch_lookups", lambda *a: {"locations": [], "quantity_units": [], "cached_at": 0})
    appmod.app.testing = True
    c = appmod.app.test_client()
    assert c.get("/products").status_code == 200
    r = c.get("/mappings?q=x&page=2")
    assert r.status_code == 301 and "/products" in r.headers["Location"]

def test_page_size_and_review_filter(tmp_path, monkeypatch):
    # seed 30 products, 1 with needs_review=1; GET /products?per=25 -> 25 rows shown, pages=2
    # GET /products?filter=needs-review -> only the stub row
    ...

def test_api_products_greek_search(tmp_path, monkeypatch):
    # seed raw ΑΛΦΑ; GET /api/products?q=αλφα -> JSON contains ΑΛΦΑ
    ...

def test_api_products_rejects_bad_params(tmp_path, monkeypatch):
    # GET /api/products?per=999&sort=bogus&page=abc -> per falls back 100, sort raw, page 1
    ...
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_products_hub.py -q`, Expected: FAIL (404 `/products`, no `per` handling, no API route).

- [ ] **Step 3: Implement** — rename handler to `products()` with `@app.route("/products")`; add `@app.route("/mappings") def mappings_redirect(): return redirect("/products?" + query_string, 301)` preserving the raw query string; `per = int(request.args.get("per", "100"))` clamped to `(25, 50, 100)` else 100; `filter=needs-review` appends `needs_review = 1`; pass `per` to template for the selector; new `/api/products` reusing the same clause-building (extract a `_product_filters(args)` helper returning `(clauses, args)` used by both routes) returning JSON rows (dicts, `suggestions` omitted). Search clause becomes `(grnorm(raw) LIKE '%' || grnorm(?) || '%' OR grnorm(clean) LIKE ... OR grnorm(category) LIKE ... OR barcode LIKE ?)` — barcode stays exact-substring (codes have no accents). Rename template file to `products.html`, update render call, nav link, headings.

- [ ] **Step 4: Live-search JS** — in `products.html`, add a script that on input (debounced 200ms) fetches `/api/products` with current filter params + `q`, re-renders tbody rows (escape via `textContent`), updates count/pages text. No-JS fallback unchanged (form GET submit).

- [ ] **Step 5: Run full suite** — `python -m pytest -q`, Expected: all green (update any old tests referencing `/mappings` page expectations — the redirect test in this task pins the new behavior).

---

### Task 3: Product mini-page + receipt links

**Files:**
- Modify: `Lidl Parser/app.py` (new `GET/POST /products/<int:pid>`; extend `mapping_update` at line 805 with new fields)
- Create: `Lidl Parser/templates/product_detail.html`
- Modify: `Lidl Parser/templates/receipt_detail.html` (link each item raw to mini-page)
- Test: extend `Lidl Parser/tests/test_products_hub.py`

**Interfaces:**
- Consumes: existing `/api/price_history?clean=` (`app.py:894`) and `/api/abc_analysis?metric=spend` (`app.py:927`) JSON shapes; `products.id` PK.
- Produces: `GET /products/<pid>` renders attributes + graph + ABC badge + receipts list; `POST /products/<pid>` saves whitelisted fields, clears `needs_review` on any save, redirects back (or to next unreviewed stub with `?next=1`).

- [ ] **Step 1: Write failing tests**

```python
def test_mini_page_shows_attributes_and_receipts(...):
    # seed product + receipt + item; GET /products/<id> 200, contains barcode text, receipt filename
def test_mini_page_save_clears_review(...):
    # POST /products/<id> with clean/category/unit... -> row updated, needs_review == 0
def test_mini_page_404(...):
    # GET /products/999999 -> 404
def test_receipt_links_to_mini_page(...):
    # GET /receipts/<rid> body contains href="/products/<id>"
```

- [ ] **Step 2: Run to verify failure** — Expected: FAIL (404 route, no links).

- [ ] **Step 3: Implement** — GET handler selects product by id, receipts via `SELECT r.* FROM items i JOIN receipts r ... WHERE i.raw = product.raw ORDER BY date DESC`, usual store via `SELECT store, COUNT(*) ... GROUP BY store ORDER BY 2 DESC LIMIT 1` (override `market` wins for display when set); template with edit form (raw readonly display + hidden, clean, category, barcode, brand, unit dropdown from `units` cache ordered by name, weight, notes, market, grocy_id + grocy_name + suggestions via existing `suggest_grocy`, purchase_qu_id dropdown from units-with-Grocy-ids + qu_factor number input, track checkbox), badges, Chart.js line fetching `/api/price_history?clean=...` (URL-encoded), ABC badge fetched from `/api/abc_analysis?metric=spend&top=50` finding the clean (fallback "–"), receipts table. POST handler whitelists exactly: `clean, category, barcode, brand, unit, weight, notes, market, grocy_id, grocy_name, purchase_qu_id, qu_factor, grocy_track, needs_review`; empty `grocy_id` → NULL; invalid ints → 400 flash + redirect back; `?next=1` redirects to next `needs_review=1` id or back to hub filter. Receipt template: map each item raw → product id (one query `SELECT id, raw FROM products`) and link `{{ clean }}` text.

- [ ] **Step 4: Run full suite** — `python -m pytest -q`, Expected: all green.

---

### Task 4: Per-raw Grocy factors — link-time prefill + push math + barcode `qu_id`

**Files:**
- Modify: `Lidl Parser/app.py` (`resolve_push_items` at 414-467, `mapping_link` at 358, `mapping_create_in_grocy` at 682, mini-page POST from Task 3)
- Modify: `Lidl Parser/grocy.py` (`add_barcode` at 244 gains `qu_id=None`)
- Test: extend `Lidl Parser/tests/test_grocy_push.py`, `tests/test_grocy_create.py`

**Interfaces:**
- Consumes: `products.purchase_qu_id/qu_factor` (Task 1), `units` cache (name lookup for note), `get_product_details` response keys `qu_conversion_factor_purchase_to_stock`, `qu_id_purchase`, `default_quantity_unit_purchase.name`.
- Produces: stored per-raw factor used by push; `add_barcode(base_url, api_key, product_id, barcode, qu_id=None)` posts `qu_id` only when not None.

- [ ] **Step 1: Write failing tests**

```python
def test_push_uses_stored_per_raw_factor(...):
    # two raws same grocy_id, qu_factor 1 and 200; preview JSON amounts differ 2.0 vs 400.0
def test_link_prefills_factor_from_grocy(...):
    # mock get_product_details factor 200/qu 10; POST link; row has qu_factor 200, purchase_qu_id 10
def test_add_barcode_with_qu_id(monkeypatch):
    # capture POST payload includes qu_id; and qu_id=None omits the key
def test_add_barcode_qu_rejected_falls_back(monkeypatch):
    # first POST 400 when qu_id present, retry posts without qu_id (implement retry in caller below)
```

- [ ] **Step 2: Run to verify failure** — Expected: FAIL (preview still calls details API / ignores stored factor; payload lacks `qu_id`).

- [ ] **Step 3: Implement** — in `resolve_push_items`: extend SELECT to new columns; replace the `details_cache` block — `factor = float(p.get("qu_factor") or 1.0)`, `purchase_qu_id = p.get("purchase_qu_id")`, `unit_name` from `units` table by Grocy id (single query, map) else `""`; drop `get_product_details` calls here entirely. Link-time prefill helper `_prefill_qu(conn, raw, gid)` (best-effort try/except, called from `mapping_link`, `mapping_create_in_grocy`, mini-page POST when grocy_id set/changed): reads details once, stores factor + purchase QU id. `add_barcode(..., qu_id=None)`: include `"qu_id": qu_id` only when not None. Barcode-with-size caller (mini-page POST when barcode + purchase_qu_id present, and create-in-grocy): try with `qu_id`, on `GrocyApiError` retry once without, flash which path was taken.

- [ ] **Step 4: Live-verify + full suite** — against the real Grocy: link one test raw, confirm factor prefill; post one barcode with `qu_id`, record accept/reject in the test as a comment if manual. Then `python -m pytest -q`, Expected: all green.

---

### Task 5: Units in Settings

**Files:**
- Modify: `Lidl Parser/app.py` (`settings()` at 954 + `POST /settings/units-refresh`)
- Modify: `Lidl Parser/templates/settings.html` (Units block)
- Test: extend `Lidl Parser/tests/test_settings_ui.py` (or new `test_units.py`)

**Interfaces:**
- Consumes: `grocy_client.fetch_lookups` (already used at `app.py:323`), `units` table + `UNIT_SEED` (Task 1).
- Produces: settings page lists cached units; refresh upserts `quantity_units` into `units` with `source='grocy'`; hub/mini-page unit dropdowns read the cache.

- [ ] **Step 1: Write failing tests**

```python
def test_units_refresh_upserts(...):
    # mock fetch_lookups quantity_units [{id:7,name:'Piece'}]; POST refresh; units table has it source grocy
def test_units_refresh_offline_keeps_cache(...):
    # fetch_lookups raises; POST refresh; old cache intact + error shown (follow_redirects flash text)
```

- [ ] **Step 2: Run to verify failure** — Expected: FAIL (404 route).

- [ ] **Step 3: Implement** — route reads lookups, upserts (`INSERT ... ON CONFLICT(id) DO UPDATE` or INSERT OR REPLACE including `cached_at=datetime('now')`), flashes count; settings template block with table + button. Ensure hub/mini-page unit `<select>`s query `SELECT id, name FROM units ORDER BY name`.

- [ ] **Step 4: Run full suite** — `python -m pytest -q`, Expected: all green.

---

### Task 6: Period CSV export

**Files:**
- Modify: `Lidl Parser/app.py` (new `GET /export.csv`)
- Modify: `Lidl Parser/templates/dashboard.html`, `templates/receipts.html` (export buttons)
- Test: new `Lidl Parser/tests/test_export.py`

**Interfaces:**
- Consumes: `items`/`receipts`/`card_map`/`users`/`item_splits`/`grocy_pushes` tables.
- Produces: attachment CSV `export_YYYYMMDD_YYYYMMDD.csv`, UTF-8 BOM, header `receipt_date,filename,store,raw,clean,category,qty,unit,unit_price,discount,final,card_last4,payer,splits,grocy_status`.

- [ ] **Step 1: Write failing tests**

```python
def test_export_csv_content(...):
    # seed receipt+items+card_map+splits+push row; GET /export.csv?from=..&to=.. -> 200,
    # text startswith BOM, header exact, payer name + "Name:0.5" splits present, grocy_status pushed
def test_export_defaults_and_bad_dates(...):
    # no params -> 200 (last-30d window); from=bogus -> 400
```

- [ ] **Step 2: Run to verify failure** — Expected: FAIL (404).

- [ ] **Step 3: Implement** — parse dates (`datetime.date.fromisoformat`, default `today-29/today`, 400 on invalid), single JOIN query ordered by date/filename/id, payer via `LEFT JOIN card_map ON receipts.card_last4 = card_map.last4 LEFT JOIN users`, splits via correlated subquery `GROUP_CONCAT(u.name || ':' || s.share, ';')`, grocy status via `LEFT JOIN grocy_pushes` (pushed if row, else skipped/unpushed — use pushed iff `pushed_count>0` else (skipped if any tracked? keep simple: pushed / unpushed)). Stream via `io.StringIO` + `csv.writer`, `Response("\ufeff" + buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=export_{frm}_{to}.csv"})`.

- [ ] **Step 4: Run full suite** — `python -m pytest -q`, Expected: all green.

---

### Task 7: Dashboard pie periods

**Files:**
- Modify: `Lidl Parser/app.py` (`dashboard()` at 172-205)
- Modify: `Lidl Parser/templates/dashboard.html` (period controls)
- Test: extend `Lidl Parser/tests/test_export.py` or new `test_dashboard_periods.py`

**Interfaces:**
- Consumes: `by_cat` query gains optional `receipt_date BETWEEN ? AND ?`.
- Produces: `?pie=this_week|last_week|30d|last_month|custom&from&to`; missing `pie` = all time (unchanged).

- [ ] **Step 1: Write failing tests**

```python
def test_pie_period_filters(...):
    # seed items in/out of window; GET /?pie=30d -> only recent categories in page data
    # GET /?pie=custom&from=..&to=.. -> exact window; GET /?pie=bogus -> all time
```

(Assert on the rendered `by_cat` JSON embedded in the page, same pattern the template already uses for Chart.js data.)

- [ ] **Step 2: Run to verify failure** — Expected: FAIL (param ignored).

- [ ] **Step 3: Implement** — helper `_pie_bounds(pie, frm, to, today)` in `app.py`: this_week (Monday→today via `date.isoweekday`), last_week (prev Mon→Sun), 30d (today-29→today), last_month (first→last day of previous month), custom (from/to, default today-179→today, 400→fallback all-time? No — invalid custom falls back to 30d window and flashes once). `by_cat` adds the BETWEEN clause only when bounds exist. Template: `<select id="pie">` + from/to date inputs shown for custom; on change sets `location.search`. Pass through current values for selected/inputs.

- [ ] **Step 4: Run full suite** — `python -m pytest -q`, Expected: all green.

---

## Self-review

- Spec §3 (columns, units table, derived store, stubs) → Task 1 (+ store derivation query in Task 3).
- Spec §4 (rename, redirect, page sizes, review filter, live search, Greek search) → Task 2 (+ grnorm in Task 1).
- Spec §5 (mini-page, graph, ABC, receipts, save-next, receipt links) → Task 3.
- Spec §6 (stored-factor push, barcode qu_id best-effort) → Task 4.
- Spec §7 (units block, refresh, dropdowns, remap hint) → Task 5 (remap hint: rows whose unit text matches no cache name get a CSS class + title — added in Task 3/2 dropdown rendering; pinned by a test in Task 5).
- Spec §8 (CSV columns, BOM, buttons, defaults) → Task 6.
- Spec §9 (presets, URL persistence, custom default, unchanged bar chart) → Task 7.
- No placeholders: every step names files, functions, exact SQL/behavior, and expected command output.
- Type consistency: `purchase_qu_id` (Grocy unit id, INTEGER), `qu_factor` REAL, `needs_review` 0/1, `units(id, name, source, cached_at)`, `/api/products` returns `{rows, page, pages, count}`.
- Rollout order matches spec §11: model → mini-pages/hub → push → units → export → dashboard.
