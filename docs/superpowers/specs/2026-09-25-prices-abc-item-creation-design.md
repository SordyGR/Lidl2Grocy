# UI Redesign: ERP-Style Item Creation + Prices Multi-Compare with ABC Analysis

Date: 2026-09-25 | Status: approved | Approach: A (upgrade in place)

## Intent

- Replace the cramped inline "Create in Grocy" form with a proper ERP-style
  new-item page prefilled from the receipt product.
- Upgrade the Prices tab from single-product trend to multi-product comparison
  with ABC (Pareto) analysis and graphs.
- Keep existing navigation, mental model, and Grocy-link semantics untouched.

## Decisions (from brainstorming)

- ABC metrics: spend, quantity, and frequency — all available via switcher.
- Creation form: full Grocy item card.
- Comparison display: overlaid trend chart + ABC Pareto bars + stat table.
- Selection: ABC top-N prefill + manual picks (search + chips), URL-persisted.

## Section 1 — ERP-style new-item page

- New route `GET /mappings/new-item?raw=...` rendering `item_new.html`.
- Card sections:
  - Identity: Grocy name (prefill `clean`), brand, barcode (prefill, editable),
    description.
  - Storage: location dropdown (`fetch_lookups`), min stock amount.
  - Units: purchase unit, stock unit dropdowns, purchase→stock factor (default 1).
  - Classification: product group dropdown (new `fetch_product_groups` in
    `grocy.py`), optional parent product, local category passthrough.
- `POST /mappings/new-item` performs one server-side transaction:
  1. Create product in Grocy via extended `create_product` (new optional fields:
     `min_stock_amount`, `qu_factor_purchase_to_stock`, `product_group_id`,
     `parent_product_id`, `description`).
  2. Attach barcode via existing `add_barcode`.
  3. Write `grocy_id`/`grocy_name` back to `products` (same semantics as
     current `mapping_create_in_grocy`).
- Mappings row keeps its Link dropdown; its "Create in Grocy" details block
  becomes a link to the new page.
- Failures: Grocy creation failure → re-render form with values + error banner,
  nothing written locally. Barcode-attach failure after creation → link anyway
  + warning flash (established pattern).

## Section 2 — Prices multi-compare + ABC

- `/prices`: single dropdown replaced by metric switcher
  (spend/quantity/frequency) + top-N input (default 5) + search-to-add field.
  Initial selection = ABC top-N; manual picks append as removable chips.
  Selection lives in URL params (`?metric=..&top=..&add=..`).
- Layout (top to bottom):
  1. Overlaid Chart.js line chart, one dataset per product (unit price over
     receipt dates).
  2. ABC Pareto panel: ranked metric bars + cumulative-% line (2nd axis),
     class colors (A ≤80% cum, B ≤95%, C rest), cutoffs in legend.
  3. Stat table: purchases, total qty, total spend, min/max/last unit price,
     ABC class for current metric.
- New endpoints:
  - `GET /api/price_history_multi?clean=a&clean=b...` → `{clean: [{d,p,q}]}`.
  - `GET /api/abc_analysis?metric=..&top=N` → ranked
    `{clean, value, cum_pct, class}` from `items` + dated receipts.
- ABC metric definitions: spend = SUM(final), quantity = SUM(qty),
  frequency = COUNT(*). One shared parameterized SQL builder.
- Chart.js stays (already loaded in `base.html`).

## Section 3 — Schema, errors, testing

- No DB migrations. Reads: `products`, `items`, `receipts`, Grocy API.
  Only write path: existing `products.grocy_id`/`grocy_name` link update.
- Grocy unreachable on new-item page → cached lookups + `stale` pattern (as in
  `mappings()`), else banner + disabled submit.
- ABC/trend endpoints return `[]` on empty data, never 500; empty chart state.
- Testing: manual — create+link against Grocy, push preview resolves `ready`;
  prices with 1 vs N products, each metric, fresh-DB empty states;
  `python -m py_compile` on touched files. No new test harness (none in repo).

## Files touched (planned)

- `app.py`: `item_new` GET/POST, `api_price_history_multi`, `api_abc_analysis`.
- `grocy.py`: `fetch_product_groups`, extended `create_product`.
- `templates/item_new.html` (new), `templates/mappings.html` (link swap),
  `templates/prices.html` (rewrite).
- No changes to `db.py`, receipt parsing, or push flow.
