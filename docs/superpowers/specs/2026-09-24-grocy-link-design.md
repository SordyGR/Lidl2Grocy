# Grocy Product Link — Phase 1 Design

Date: 2026-09-24
Status: draft for review
Scope: Phase 1 only (match/link). Phase 2 (stock push) is out of scope.

## 1. Intent

Link existing Lidl Parser `products` (SQLite `data/lidl.db`) to existing
Grocy server products (names + barcodes present) where names differ slightly.

Success criteria:
- Every Lidl product row can store `grocy_id`, `grocy_name`, `grocy_synced_at`.
- Mappings UI shows link state + barcode-exact / fuzzy-name suggestions.
- User can Link / Unlink manually. No auto-push to Grocy in Phase 1.
- Phase 2 can read `grocy_id` directly without re-matching.

Constraints:
- Stack: Flask + SQLite + pandas + rapidfuzz (already in `requirements.txt`).
- Add only `requests>=2.0` for Grocy HTTP.
- Config via env: `GROCY_URL` (e.g. `https://grocy.example.com/api`), `GROCY_API_KEY`.
- Auth: header `GROCY-API-KEY` (Grocy REST convention).
- Manual review only. No background sync.

## 2. Architecture

New read-only client `Lidl Parser/grocy.py`:

- `get_config() -> (base_url, api_key)` — strips trailing `/`, raises `GrocyConfigError` if missing.
- `list_products() -> list[dict]` — `GET /objects/products`.
- `list_barcodes() -> list[dict]` — `GET /objects/product_barcodes` (fields: `product_id`, `barcode`).
- `fetch_catalog() -> {products, barcodes_by_product, barcode_to_product_id}` — 5-min in-memory cache.
- Timeout 10s, one retry on network error, surfaces `GrocyApiError(status, body)`.

DB migration (via `db.py` `init_db()` + `migrate.py` safe re-run):

```sql
ALTER TABLE products ADD COLUMN grocy_id INTEGER;
ALTER TABLE products ADD COLUMN grocy_name TEXT;
ALTER TABLE products ADD COLUMN grocy_synced_at TEXT;
CREATE INDEX IF NOT EXISTS idx_products_grocy ON products(grocy_id);
```

`app.py` changes:
- `GET /mappings` — enriches rows with `suggestions` (top-3) when `grocy_id IS NULL`.
- `POST /mappings/link` — params `raw`, `grocy_id`, `grocy_name` — sets link + timestamp.
- `POST /mappings/unlink` — params `raw` — clears link fields.
- `GET /api/grocy_status` — returns `{configured, product_count, cached_at}` for UI banner.

No changes to `receipt_parser.py`, `pipeline.py`, receipt import flow.

## 3. Components

### 3.1 Catalog fetch

- Join Grocy `products.id/name` with `product_barcodes` on `product_id`.
- Lidl side: `SELECT raw, clean, category, barcode, grocy_id, grocy_name FROM products ORDER BY raw LIMIT 500` (existing Mappings query extended).
- Cache key: `(base_url, api_key_hash)` — never log full key.

### 3.2 Suggest function

`grocy_match.py` (or inside `grocy.py`):

```
suggest_grocy(clean, barcode, catalog) -> [{grocy_id, grocy_name, reason, score}]
```

1. Barcode exact: `barcode.strip()` in `barcode_to_product_id` → `[{score:100, reason:'barcode'}]`.
2. Else fuzzy: `rapidfuzz.process.extract(clean, grocy_names, scorer=token_sort_ratio, limit=3)` filtered `score >= 80`.
3. Else `[]`.
4. If barcode maps to 2+ Grocy ids (collision) → return all with `reason:'barcode-collision'`, force manual pick.

Reuse existing `normalize()` semantics (upper, strip `./,`) for fair Greek-name comparison.

### 3.3 Mappings UI (`templates/mappings.html`)

Per row:
- If linked: green badge `Grocy #<id> <name>` + Unlink button.
- If unlinked: candidate `<select>` (value `grocy_id`, label `name + reason/score`) + free search input (HTML datalist of Grocy names for manual browse) + Link button.
- Top banner when unconfigured: `Set GROCY_URL + GROCY_API_KEY to enable linking`.
- Existing `q` / `filter` behaviour unchanged.

## 4. Data Flow

1. User opens `/mappings`.
2. Server calls `fetch_catalog()` (cached 5 min) if configured.
3. For each Lidl row with `grocy_id IS NULL`, compute `suggest_grocy()`.
4. User picks candidate or datalist entry → `POST /mappings/link`.
5. Server validates `grocy_id` exists in fresh catalog (guards stale cache) → `UPDATE products SET grocy_id=?, grocy_name=?, grocy_synced_at=datetime('now') WHERE raw=?`.
6. Unlink clears fields. No Grocy writes.
7. Phase 2 contract: `items.clean` → `products.clean` → `products.grocy_id` is the push key. Items with no link are skipped in push preview.

## 5. Error Handling

- Missing env → Mappings renders normally, banner shown, `grocy_status.configured=false`.
- 401/403 → flash `Grocy auth failed (check API key)`, no DB write, log status only.
- 404 on `product_barcodes` (older Grocy) → fallback to name-only matching, flash once.
- Timeout/network → single retry after 1s → flash `Grocy unreachable`, serve stale cache if `<30 min` old with `stale:true` flag, else empty suggestions.
- Stale `grocy_id` (deleted in Grocy) → Link validation rejects; existing links flagged on next catalog refresh via `grocy_status.orphans[]` (list only, no auto-delete).
- Barcode collision → no auto-pick, UI shows warning.

## 6. Testing

New `Lidl Parser/tests/test_grocy_match.py` (mocked `requests`, no live server):

- Barcode exact wins over fuzzy.
- Greek variant fuzzy suggests (`normalize` parity with `receipt_parser.py`).
- Threshold `<80` returns empty.
- Collision returns multiple with `barcode-collision`.
- Link/unlink round-trip persists `grocy_id` + timestamp in temp SQLite.
- `GET /mappings` with missing env still 200, `configured=false`.

Manual QA:
- Point at live Grocy demo or user server, verify 3 linked + 1 unlinked + 1 collision renders correctly.

## 7. Out of Scope (Phase 2)

- `POST /stock/products/{id}/add`, product creation, purchase price/qty mapping.
- `grocy_pushes` idempotency table, receipt-detail push button, bulk queue.
- Quantity-unit / location auto-creation, best-before defaults.
- Write paths to Grocy (`POST/PUT/DELETE /objects/*` except reads).
