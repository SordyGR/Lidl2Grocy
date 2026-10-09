# ERP New-Item Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an ERP-style `/mappings/new-item` page that creates a full Grocy product and links it locally in one submit.

**Architecture:** Extend `grocy.py` with product-group lookup and richer creation payload; add GET/POST routes in `app.py` following the existing `mapping_create_in_grocy` pattern; new `item_new.html` template in card sections; replace the row-level "Create in Grocy" details block with a link.

**Tech Stack:** Flask (existing app), Jinja2, Grocy REST API (`/objects/products`, `/objects/product_barcodes`, `/objects/product_groups`, `/objects/locations`, `/objects/quantity_units`), SQLite (no migrations).

**Spec:** `docs/superpowers/specs/2026-09-25-prices-abc-item-creation-design.md` (Section 1)

## Global Constraints

- App lives in `Lidl Parser/` — all paths below are relative to it.
- No DB migrations; only write path is `UPDATE products SET grocy_id/grocy_name/grocy_synced_at`.
- Directory is not a git repo — there are no commit steps; each task ends with a verification run instead.
- No test harness in repo — verification uses Flask `test_client` scripts and `py_compile`, not pytest.
- Grocy-touching POST paths cannot be fully verified without a live Grocy; those steps verify form rendering + validation, and mark the live check explicit.

## Review Focus

- Barcode with surrounding whitespace must be stripped before POST or Grocy rejects it — expect stored barcode to equal the trimmed input.
- `location_id`/`qu_*` dropdowns left on their placeholder must send nothing, not `""` or `0` — expect those keys absent from the Grocy payload.
- A raw value containing spaces or non-ASCII must round-trip through the `?raw=` query param — expect the form to prefill correctly.
- Double-submit must not create duplicate Grocy products — expect the POST to redirect (PRG) so refresh is a harmless GET.
- Grocy down at form render must still show the form with a banner, not a 500 — expect HTTP 200 with the warning present.

---

### Task 1: Grocy client — product groups + richer creation payload

**Files:**
- Modify: `grocy.py` (append after `add_barcode`, ~line 259)
- Verify: `python -m py_compile grocy.py` + import check

**Interfaces:**
- Consumes: existing `_get`, `_post` helpers in `grocy.py`.
- Produces: `fetch_product_groups(base_url, api_key) -> list` for Task 2;
  extended `create_product(..., min_stock_amount=None, qu_factor=None, product_group_id=None, parent_product_id=None, description=None) -> int` for Task 2.

- [ ] **Step 1: Append the new functions to `grocy.py`**

```python
def fetch_product_groups(base_url, api_key):
    groups = _get(base_url, api_key, "/objects/product_groups")
    return groups if isinstance(groups, list) else []


def create_product(base_url, api_key, name, location_id, qu_purchase, qu_stock,
                   min_stock_amount=None, qu_factor=None, product_group_id=None,
                   parent_product_id=None, description=None):
    payload = {"name": (name or "").strip()}
    if location_id is not None:
        payload["location_id"] = int(location_id)
    if qu_purchase is not None:
        payload["qu_id_purchase"] = int(qu_purchase)
    if qu_stock is not None:
        payload["qu_id_stock"] = int(qu_stock)
    if min_stock_amount is not None and str(min_stock_amount).strip() != "":
        payload["min_stock_amount"] = float(min_stock_amount)
    if qu_factor is not None and str(qu_factor).strip() != "":
        payload["qu_factor_purchase_to_stock"] = float(qu_factor)
    if product_group_id is not None:
        payload["product_group_id"] = int(product_group_id)
    if parent_product_id is not None:
        payload["parent_product_id"] = int(parent_product_id)
    if description:
        payload["description"] = description.strip()
    data = _post(base_url, api_key, "/objects/products", payload)
    return int(data.get("created_object_id"))
```

Keep the old 6-argument call working: all new params default to `None`, so the
existing `mapping_create_in_grocy` call at `app.py:488` is unaffected.

- [ ] **Step 2: Compile-check and signature-check**

Run: `python -m py_compile grocy.py && python -c "import inspect, grocy; print(inspect.signature(grocy.create_product)); print(inspect.signature(grocy.fetch_product_groups))"`
Expected: compiles silently; signatures show the new optional params and `(base_url, api_key)`.

### Task 2: Routes — GET form + POST create-and-link

**Files:**
- Modify: `app.py` (add routes after `mapping_create_in_grocy`, ~line 502)
- Verify: `test_client` GET checks

**Interfaces:**
- Consumes: `grocy.fetch_product_groups`, extended `grocy.create_product`, `grocy.add_barcode`, `grocy.fetch_lookups`, `grocy.get_config` from Task 1; `get_db` from `db.py`.
- Produces: `GET /mappings/new-item?raw=..` and `POST /mappings/new-item` for Task 3's form.

- [ ] **Step 1: Add the GET route**

```python
@app.route("/mappings/new-item")
def mapping_new_item():
    raw = request.args.get("raw", "").strip()
    conn = get_db()
    prod = conn.execute("SELECT * FROM products WHERE raw=?", (raw,)).fetchone()
    conn.close()
    prod = dict(prod) if prod else {"raw": raw, "clean": raw, "brand": "",
                                   "barcode": "", "category": "Misc"}
    grocy_error, groups = None, []
    lookups = {"locations": [], "quantity_units": []}
    try:
        base_url, api_key = grocy_client.get_config()
        grocy_configured = True
        try:
            lookups = grocy_client.fetch_lookups(base_url, api_key)
            groups = grocy_client.fetch_product_groups(base_url, api_key)
        except Exception as e:
            grocy_error = f"Grocy lookups unavailable: {str(e)[:150]}"
    except grocy_client.GrocyConfigError:
        grocy_configured = False
    return render_template("item_new.html", prod=prod,
                           grocy_configured=grocy_configured,
                           grocy_error=grocy_error,
                           locations=lookups["locations"],
                           quantity_units=lookups["quantity_units"],
                           groups=groups)
```

- [ ] **Step 2: Add the POST route**

```python
@app.route("/mappings/new-item", methods=["POST"])
def mapping_new_item_create():
    raw = request.form.get("raw", "").strip()
    name = request.form.get("name", "").strip()
    if not raw or not name:
        flash("Missing product name")
        return redirect(url_for("mapping_new_item", raw=raw))

    def _opt(key):
        v = (request.form.get(key) or "").strip()
        if v == "":
            return None
        try:
            return int(v)
        except ValueError:
            return None

    def _dec(key):
        v = (request.form.get(key) or "").strip()
        return v or None

    barcode = (request.form.get("barcode") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    try:
        base_url, api_key = grocy_client.get_config()
        gid = grocy_client.create_product(
            base_url, api_key, name, _opt("location_id"),
            _opt("qu_purchase"), _opt("qu_stock"),
            min_stock_amount=_dec("min_stock"), qu_factor=_dec("qu_factor"),
            product_group_id=_opt("product_group_id"),
            parent_product_id=_opt("parent_product_id"),
            description=description)
        if barcode:
            try:
                grocy_client.add_barcode(base_url, api_key, gid, barcode)
            except Exception as e:
                flash(f"Created Grocy #{gid} but barcode failed: {e}")
        conn = get_db()
        conn.execute("UPDATE products SET grocy_id=?, grocy_name=?, grocy_synced_at=datetime('now') WHERE raw=?",
                     (gid, name, raw))
        conn.commit()
        conn.close()
        flash(f"Created Grocy #{gid} and linked {raw}")
    except Exception as e:
        flash(f"Grocy create failed: {e}")
        return redirect(url_for("mapping_new_item", raw=raw))
    return redirect(url_for("mappings"))
```

PRG pattern (redirect after POST) covers the double-submit Review Focus line.

- [ ] **Step 3: Verify routes render without Grocy**

Run: `python -c "import app as a; c=a.app.test_client(); r=c.get('/mappings/new-item?raw=TESTX'); print(r.status_code, b'Grocy not configured' in r.data or b'Grocy lookups unavailable' in r.data)"`
Expected: `200 True` (form renders with banner since no Grocy configured here).
Then: `python -c "import app as a; c=a.app.test_client(); r=c.post('/mappings/new-item', data={'raw':'','name':''}); print(r.status_code, r.headers.get('Location'))"`
Expected: `302` redirect back to the form (validation path, no Grocy call).

### Task 3: Template — item card form + mappings link swap

**Files:**
- Create: `templates/item_new.html`
- Modify: `templates/mappings.html:99-121` (replace "Create in Grocy" details block with link)
- Verify: render check via `test_client`, visual check in browser

**Interfaces:**
- Consumes: template vars from Task 2 (`prod`, `grocy_configured`, `grocy_error`, `locations`, `quantity_units`, `groups`).
- Produces: the finished page; nothing downstream.

- [ ] **Step 1: Create `templates/item_new.html`**

```html
{% extends "base.html" %}
{% block content %}
<h1>New Grocy item</h1>
{% if not grocy_configured %}
<p class="notice-warn">Grocy not configured — set GROCY_URL (with /api) + GROCY_API_KEY.</p>
{% elif grocy_error %}
<p class="notice-err">{{ grocy_error }}</p>
{% endif %}
<form method="post" action="/mappings/new-item">
<input type="hidden" name="raw" value="{{ prod["raw"] }}">
<fieldset><legend>Identity</legend>
<label>Grocy name <input name="name" value="{{ prod["clean"] }}" size="30" required></label>
<label>Brand <input name="brand" value="{{ prod["brand"] or "" }}" size="16"></label>
<label>Barcode <input name="barcode" value="{{ prod["barcode"] or "" }}" size="16"></label>
<label>Description <input name="description" size="40" placeholder="optional"></label>
</fieldset>
<fieldset><legend>Storage</legend>
<label>Location <select name="location_id"><option value="">location…</option>
{% for l in locations %}<option value="{{ l["id"] }}">{{ l["name"] }}</option>{% endfor %}</select></label>
<label>Min stock <input name="min_stock" size="6" placeholder="0"></label>
</fieldset>
<fieldset><legend>Units</legend>
<label>Purchase unit <select name="qu_purchase"><option value="">purchase unit…</option>
{% for u in quantity_units %}<option value="{{ u["id"] }}">{{ u["name"] }}</option>{% endfor %}</select></label>
<label>Stock unit <select name="qu_stock"><option value="">stock unit…</option>
{% for u in quantity_units %}<option value="{{ u["id"] }}">{{ u["name"] }}</option>{% endfor %}</select></label>
<label>Purchase→stock factor <input name="qu_factor" size="6" placeholder="1"></label>
</fieldset>
<fieldset><legend>Classification</legend>
<label>Product group <select name="product_group_id"><option value="">group…</option>
{% for g in groups %}<option value="{{ g["id"] }}">{{ g["name"] }}</option>{% endfor %}</select></label>
<label>Parent product id <input name="parent_product_id" size="6" placeholder="optional"></label>
</fieldset>
<button {% if not grocy_configured %}disabled{% endif %}>Create + link</button>
<a href="/mappings">Cancel</a>
</form>
{% endblock %}
```

Placeholder `<option value="">` entries submit `""`, which Task 2's `_opt`
maps to `None` (Review Focus line 2).

- [ ] **Step 2: Swap the mappings row block for a link**

In `templates/mappings.html`, replace lines 99–121 (`{% if grocy_configured %}<details>…</details>{% endif %}`) with:

```html
{% if grocy_configured %}
<a href="/mappings/new-item?raw={{ r["raw"]|urlencode }}">Create in Grocy…</a>
{% endif %}
```

- [ ] **Step 3: Verify render + live check**

Run: `python -m py_compile app.py grocy.py`
Expected: silent.
Run: `python -c "import app as a; c=a.app.test_client(); r=c.get('/mappings'); print(r.status_code)"`
Expected: `200`.
Then open `http://127.0.0.1:5000/mappings/new-item?raw=<a real raw>` in the
browser: sections prefilled, dropdowns populated (or banner if Grocy down).
Live Grocy check (needs configured Grocy): submit once, confirm flash
"Created Grocy #N and linked …", row shows Grocy #N, push preview resolves
`ready`.
