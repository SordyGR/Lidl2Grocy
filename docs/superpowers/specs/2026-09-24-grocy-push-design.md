# Grocy Stock Push — Phase 2 Design

Date: 2026-09-24
Status: draft for review
Scope: Phase 2 only (per-receipt stock push). Depends on Phase 1 link spec
(`docs/superpowers/specs/2026-09-24-grocy-link-design.md`).

## 1. Intent

Push parsed Lidl receipt lines to the live Grocy server as stock purchases,
using the persisted Phase 1 `products.grocy_id` links. Manual per-receipt
review only; nothing pushes automatically.

Success criteria:
- Receipt detail page shows a per-item Grocy preview (ready vs excluded reason).
- User can toggle per-product track / never-track in Mappings; flagged-off
  products are silently excluded from every push.
- Confirmed push creates one Grocy stock entry per eligible item, backdated
  to the receipt date, with per-unit net price.
- Re-pushing the same receipt is blocked unless forced; partial failures are
  reported per item and the receipt stays force-re-pushable.
- Items with no link, orphaned links, or flagged-off products are never pushed.

Constraints:
- Stack: Flask + SQLite + requests (already added in Phase 1). No new deps.
- Auth/config: same `GROCY_URL` (with `/api`) + `GROCY_API_KEY` + `GROCY-API-KEY` header.
- No Grocy product creation in this phase (unlinked-and-tracked items are
  reported, not created).
- `amount` = Lidl `qty` as-is; correct units are the user's responsibility
  (Grocy-side `qu_id_stock` matching is out of scope).

## 2. Architecture

Extend `Lidl Parser/grocy.py` with a write path:

- `add_stock(base_url, api_key, product_id, amount, price, note) -> list[dict]`
  — `POST /stock/products/{id}/add` with
  `{amount, price, transaction_type: "purchase", note}`.
  `best_before_date` is omitted so Grocy product defaults apply.
  Returns the StockLogEntry list; caller reads `stock_id` from entries.
- `set_purchased_date(base_url, api_key, entry_id, date_iso) -> None`
  — `PUT /stock/entry/{entryId}` with `{purchased_date: date_iso}`.
  Skipped when the receipt has no date.

`app.py` routes:

- `GET /receipts/<rid>/grocy-preview` — HTML fragment (rendered inside
  `receipt_detail.html`) plus JSON when `Accept: application/json`.
  One row per item: raw, clean, qty, final, grocy_id/name, amount, price,
  status.
- `POST /receipts/<rid>/grocy-push` — form fields: `force` (`"1"` to re-push).
  Pushes eligible items one by one, writes the push log, redirects to the
  receipt page with a flashed summary.

`db.py` migration (idempotent, same pattern as Phase 1):

```sql
ALTER TABLE products ADD COLUMN grocy_track INTEGER DEFAULT 1;
CREATE TABLE IF NOT EXISTS grocy_pushes (
    receipt_id INTEGER PRIMARY KEY REFERENCES receipts(id) ON DELETE CASCADE,
    pushed_at TEXT DEFAULT (datetime('now')),
    pushed_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    errors TEXT DEFAULT '[]'
);
```

`app.py` Mappings addition:

- `POST /mappings/track` — params `raw`, `track` (`"1"`/`"0"`) — sets
  `products.grocy_track`. Rendered as a Track/Skip toggle per row.

## 3. Components

### 3.1 Eligibility resolver

`resolve_push_items(receipt_id) -> list[dict]` (in `app.py` or a small
`grocy_push.py`; prefer `app.py`-local helper to match Phase 1 patterns):

- Join `items` to `products` on `items.raw = products.raw`.
- Per item row produce:
  - `ready` — `products.grocy_id` set, `grocy_track=1`, id in fresh catalog.
  - `excluded-flagged` — `grocy_track=0` (silent in counts, shown in preview).
  - `excluded-no-link` — no `products` row or `grocy_id IS NULL`.
  - `orphan-link` — `grocy_id` not in fresh catalog.
- Orphan check uses `fetch_catalog()` (with its stale fallback); a stale
  catalog marks the preview `stale:true` but still resolves.

### 3.2 Payload builder

Per `ready` item:

- `amount` = `items.qty` (float, as stored by the parser).
- `price` = `round(items.final / items.qty, 2)` when `qty != 0`, else `0.0`
  (net of discounts; Grocy stores price per stock unit).
- `note` = `f"Lidl {receipt_filename} {receipt_date or ''}"`.strip().
- `transaction_type` = `"purchase"`.

### 3.3 Push executor

For each `ready` item in order:

1. `entries = add_stock(...)`; take the first entry's `stock_id`.
2. If `receipt_date` is set and `stock_id` present: `set_purchased_date(...)`.
   A backdate failure is recorded against the item but does not roll back
   the added stock.
3. Any exception → record `{raw, error: str(e)[:200]}`, continue.

After all items: `INSERT OR REPLACE INTO grocy_pushes
(receipt_id, pushed_at, pushed_count, skipped_count, errors)`
with `pushed_at=datetime('now')`. Non-force push when a row exists for the
receipt is rejected before any API call with "already pushed at X".

## 4. Data Flow

1. User opens `/receipts/<rid>` → sees Grocy preview table (statuses) +
   Push button (+ Force checkbox when already pushed).
2. Preview fetches catalog once per request (cached 5 min, Phase 1 behavior).
3. `POST /receipts/<rid>/grocy-push` → reject if push log exists and no
   `force`; else resolve → push loop → write log → flash
   `"Pushed N, skipped M, failed K"` → redirect to receipt page.
4. Phase 1 Mappings stays the link/flag cockpit; receipt pages are push-only.

## 5. Error Handling

- Missing API config → preview shows unconfigured banner; push route flashes
  the shared `CONFIG_HINT` and redirects (no API calls).
- Per-item Grocy 400 (e.g. unknown product id raced deletion) → item marked
  failed, loop continues; receipt log stores the errors JSON.
- Network failure mid-push → items already pushed stay pushed; log records
  partial counts; force re-push is the documented recovery (may duplicate
  already-pushed lines — stated in the flash message).
- `qty = 0` or missing → item treated as failed with `"bad quantity"`,
  never sent.
- `PUT` backdate failure → stock stays with today's date; error recorded.
- `request.form` access uses `.get()` with flash paths (Phase 1 deferred
  minor: no bare `request.form["raw"]` KeyErrors on new routes).

## 6. Testing

New `Lidl Parser/tests/test_grocy_push.py` (mocked `requests`/`grocy`
helpers, temp SQLite via monkeypatched `db.DB_PATH`):

- Price math: kg line (`qty=0.59, final=8.73`) → `amount=0.59, price=14.79`;
  discounted piece line nets discount into unit price.
- Flagged-off product excluded from push calls, counted as skipped.
- Unlinked and orphan items excluded, never call `add_stock`.
- `POST /add` then `PUT /entry` sequence called with `purchased_date`
  = receipt date; `stock_id` taken from add response.
- Receipt without date skips backdate (add called, edit not called).
- Second push without `force` blocked with no API calls; with `force=1`
  re-pushes.
- Backdate failure still counts item as pushed with recorded error.
- Full suite (`pytest -q` from `Lidl Parser/`) stays green.

Manual QA: against the live server push one small receipt, verify stock
entries, dates, and prices in Grocy UI, then force re-push and confirm the
duplicate warning wording.

## 7. Out of Scope

- Creating missing Grocy products during push.
- Quantity-unit conversion / `qu_id` handling.
- Consume/shopping-list/chores integration.
- Bulk multi-receipt push queue.
- Undoing a push in Grocy.
