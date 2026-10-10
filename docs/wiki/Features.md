# Features

Analysis of what Lidl2Grocy does today (v0.11.0, 242 tests).
Two skins everywhere: v1 default, v2 via `/ui/v2` cookie; light/dark/system
theme returns to the page you were on.

## Dashboard (`/`)

KPIs (receipts, items, spend, unmapped), monthly spend bars, category
doughnut with the **range total in the center** (tail beyond top-12 folds into
an "Other" slice so chart and number always agree), date-range presets,
recent receipts, top products, per-user totals. Paste-box fallback for manual
imports. Chart category exclusions leave counts/receipts/exports whole.

## Receipts (`/receipts`, `/receipts/<id>`)

- Line detail: clean name first (raw as reference), category, qty, unit price,
  **VAT %**, discount, final, match status.
- Card last-4 auto-detection → owner ("Paid with"), cash fallback.
- `.txt` download; repair actions: **Rescan cards**, **Resync names**
  (re-applies curated names to lines mapped before save-propagation existed).
- Splits: equal default across active users, exact per-line override behind a
  **Split** disclosure, whole-line assign, named inputs (no numeric ids).
- **Grocy push is always manual**: preview table, per-line status, safe abort
  when Grocy is unreachable, guarded force re-push. Full stock amounts are
  pushed (Grocy has no user concept); splits only drive local totals.

## Products (`/products`, `/products/<id>`)

- raw → clean catalog: inline/bulk edit, Greek-insensitive search, filters
  (unmapped, needs-review, linked, tracked), pagination.
- Grocy linking with fuzzy + barcode suggestions, stale-link flags, skip/track
  toggle. List shows a **badge only** (deep link to the Grocy product);
  the full workflow lives in the product mini-page, which also propagates
  clean/category back onto receipt lines on save.
- **Duplicates** review (`grnorm` grouping: case + accent insensitive): pick
  winner per group, lines fold over, Grocy link transfers.
- **Import/export**: xlsx catalog sharing with keep-local / keep-new /
  keep-both conflict review (`<raw> [from <file>]`), barcode-collision
  warnings. Grocy links are never shared.
- **Categories merge** review: Grocy group names win; map-the-rest UI.

## Prices & ABC (`/prices`)

Metric (spend/quantity/frequency) × top-N × **products/categories** toggle.
Pareto bars lead full-width with `#rank · Name (Class)` labels and the
cumulative-% line painted **on top**; trend chart and stats table below
(buys, qty, spent, min/max/last, ABC pill). Classes: A ≤ 80 %, B ≤ 95 %,
C rest. `analysis.xlsx` (lines + trends) and `analysis` API feed it.

## Settings (`/settings`)

Hidden API key (empty submission = unchanged), extension token show/copy/
regenerate, expandable Excluded-from-charts / Units & categories / Users &
cards sections, user rename + vacation mode (hidden from splits, history
kept) + guarded delete, card reassignment, Grocy-cached Units/Categories with
refresh, full SQLite backup download.
