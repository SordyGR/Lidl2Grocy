# Products Hub — Design Spec (2026-09-27)

## 1. Intent

Evolve the Lidl Parser "Mappings" tab into a **Products** hub: every raw
packaging gets a mini-page with attributes, Grocy linkage, price history,
and receipt references; units are standardized from Grocy; the dashboard
gains period-aware charts plus a period CSV export for the finance app.
The app stays the pre-Grocy dashboard, including future multi-store
receipts. Success: a new receipt's unmapped products appear as triageable
stub pages; same-Grocy-item packagings push correct converted amounts;
one CSV answers "who paid, bought for whom, when" for any period.

Prior approach decision: evolve the raw-keyed model (Option A), no new
packaging table — each `products` row (keyed by UNIQUE `raw`) already
represents one packaging.

## 2. Locked decisions

1. **Multi-packaging = multiple raws, one `grocy_id`.** Grocy holds one
   product whose conversion table holds both sizes; our side stores the
   per-raw purchase unit + factor and converts at push time.
2. **Push math uses stored per-raw factors** (`qty × qu_factor`,
   `price = final / amount`), prefilled from Grocy at link time. No more
   per-push product-details reads.
3. **Units come from Grocy** (`quantity_units`) when reachable; a local
   seed list covers offline. Legacy free-text units are never silently
   rewritten.
4. **CSV, not Excel** for the period export (no new dependencies).
5. **ABC rank = existing definition**: spend Pareto via
   `/api/abc_analysis`, A ≤80% cumulative, B ≤95%, C rest.
6. **Store stays receipt-level** (default 'Lidl'); product shows its usual
   store derived from receipts with manual override.
7. **Live search is progressive enhancement** over the current
   server-rendered table, not a rewrite.

## 3. Data model (all additive, `init_db` ALTER pattern)

- `products` gains:
  - `purchase_qu_id INTEGER` — Grocy quantity-unit id for this packaging.
  - `qu_factor REAL` — purchase→stock factor (e.g. 200 piece→grams).
  - `market TEXT DEFAULT 'Lidl'` — manual override; blank means derived.
  - `needs_review INTEGER DEFAULT 0` — set on auto-created stubs.
- New `units` table: (`id`, `name`, `source` grocy|local, `cached_at`).
- Receipt import auto-creates stub `products` rows for unknown raws
  (`raw`, `clean`, `category` from `smart_match`, `needs_review=1`,
  `INSERT OR IGNORE` on UNIQUE `raw`).
- Derived usual store: most frequent `receipts.store` over the raw's
  items; `market` override wins when set.

## 4. Products hub (`/products`, `/mappings` 301-redirects)

- Nav/headings/titles renamed; all URLs under `/products`.
- Table behavior unchanged (sort, filters, track toggle, bulk edit,
  create-in-Grocy, full-catalog link dropdown), plus:
  - Page-size selector: 25 / 50 / 100 (default 100).
  - `link` filter gains "needs review" (`needs_review=1`); saving any
    field on a stub clears the flag.
  - As-you-type search: `GET /api/products?q=&...` (same filters as the
    page) returns JSON rows; JS (~200ms debounce) re-renders the tbody;
    no-JS fallback is the current full-page GET.
  - **Greek-insensitive search**: a registered SQLite scalar function
    (NFD strip combining marks + casefold, applied to both column and
    query) makes Α=α=ά, ι=ί=ϊ=ΐ, ο=ό, etc. Used by hub query and API.

## 5. Product mini-page (`/products/<id>`)

- Edit form: raw, clean, category, barcode, brand, unit (dropdown from
  units cache), weight, notes, market (override/derived display), Grocy
  link (id + name + suggestions), purchase QU + factor, track toggle.
- Status badges: linked / skipped / needs-review.
- PriceGraph: per-clean history via existing price-history source,
  Chart.js line (same data as Prices page).
- ABC badge: spend-metric lookup via `/api/abc_analysis` by clean name.
- "Found in" list: `items JOIN receipts WHERE items.raw=?` with date,
  file, qty, price; each links to its receipt.
- Save returns to the page; "Save & next unreviewed" triages stubs.
- Receipt detail rows link each item to its mini-page by raw (stubs
  always exist post-import, so no dead links).

## 6. Grocy packaging push + barcode→size

- Resolver uses stored per-raw `qu_factor`/`purchase_qu_id`; raws sharing
  a `grocy_id` each push their own size. Receipt note keeps purchase
  qty/unit for traceability. Review-before-push, flagged-off exclusion,
  backdate, and no-best-before are unchanged.
- `add_barcode` additionally posts `qu_id` = the raw's `purchase_qu_id`
  so scans default to the right size. Verified against the live Grocy at
  implementation time: if this Grocy version rejects `qu_id`, post
  without it and log — push never fails on this.

## 7. Units in Settings

- New "Units" block: cached-unit table (name, Grocy id, source),
  "Refresh from Grocy" button (upserts `units` from `quantity_units`,
  reports count; failure keeps old cache and shows Grocy's error).
- Offline seed list (τεμάχιο, kg, g, L, ml, …) always present.
- Hub bulk-edit and mini-page unit dropdowns read the cache. Raws with
  unmatched legacy text show a "remap" hint, keep working.

## 8. Period CSV export

- `GET /export.csv?from=&to=` (defaults: last 30 days). One row per
  item: receipt date, filename, store, raw, clean, category, qty, unit,
  unit price, discount, final, card last-4, payer (`card_map`, blank if
  unknown), splits (`name:share;…` from `item_splits`, blank if none),
  receipt Grocy status (pushed / skipped / unpushed).
- UTF-8 with BOM (Greek text opens cleanly in Excel),
  `Content-Disposition: attachment`. Buttons on dashboard (next to period
  controls) and Receipts.

## 9. Dashboard pie periods

- By-category pie gains period controls: this week / last week /
  last 30 days / last month / custom range (default 180-days-ago →
  today). Selection lives in the URL (`?pie=30d`, `?pie=custom&from&to`).
  No `pie` param keeps today's all-time behavior (no regression).
- Monthly bar chart scope unchanged (all time).

## 10. Testing

- pytest from `Lidl Parser/` (`python -m pytest -q`), TDD per change:
  migration idempotence, per-raw factor push math incl. shared grocy_id,
  Greek-normalized search (ά→α etc.), stub auto-creation + review flag,
  units refresh incl. offline fallback, CSV content/encoding, pie period
  filtering, `/mappings` redirect, receipt→mini-page links.
- Live Grocy checks (barcode `qu_id`, units pull) run against the real
  instance during implementation; failures there degrade gracefully.

## 11. Rollout order

Sections 3+4+5 depend on §3's columns; suggested implementation order:
§3 model + stubs → §5 mini-pages → §4 hub (rename/search/pagination) →
§6 push → §7 units → §8 export → §9 dashboard. Each shippable alone.
