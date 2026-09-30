# Architecture

## Components

- **Flask app** (`Lidl Parser/app.py`, ~60 routes): Jinja UI (v1 + `_v2`
  skins), JSON APIs for charts/extension, Grocy client calls.
- **SQLite** (`db.py`, `data/lidl.db`): single-file DB with migrations in
  `init_db()`; `LIDL_DB` env override for isolated runs/tests.
- **Parser** (`receipt_parser.py`, `fetch_lib.py`): trims page text to the
  fiscal body, extracts date/total/card-last-4/**MAPK**, fuzzy-matches
  products against catalog memory (`smart_match`).
- **Grocy client** (`grocy.py`): catalog caching with stale fallback,
  fuzzy/barcode suggestions, stock push + purchase-date backdate.
- **Extension** (`extension/`): MV2 content + background sweep (see
  [Extension](Extension.md)).

## Key tables

`products(raw UNIQUE, clean, category, barcode, grocy_id, …)` ·
`receipts(filename UNIQUE, receipt_date, total, card_last4, mark)` ·
`items(receipt_id FK, raw, clean, category, qty, unit_price, vat, discount,
total, final, match_type)` · `users / card_map / item_splits` ·
`fetch_state(t_id PK, filename)` · `units / categories` (local seed + Grocy
cache) · `grocy_pushes` · `settings` (UI-over-env resolution).

## Receipt lifecycle

extension/CLI/paste/upload → trim → parse → match → store lines + text →
map raw→clean (edits propagate to lines) → split per user → manual Grocy push.

## Dedupe design (two keys)

1. **tid** (Lidl URL token) in `fetch_state` — cheap pre-diff via `/need`.
2. **MAPK** (fiscal id, `MAPK: <digits>` footer) in `receipts.mark UNIQUE` —
   authoritative at import, covers paste/upload/CLI paths that never saw a
   tid. Saved `.txt` files append the MAPK line (trimmed body would lose it).

## Standing rules

- Receipt lines reach Grocy **only via manual per-receipt review**.
- Never hammer Lidl: sequential fetches, throttles, single attempts.
- Grocy links are never shared between users; barcode collisions warn only.
- Analysis exclusion is one rule (KPIs/charts/ABC); counts and exports stay
  absolute.
