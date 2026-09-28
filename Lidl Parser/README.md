# Lidl Parser — DB + Web UI

> Full setup guide and usage wiki: [repo-root README](../README.md).

## Run
```
pip install -r requirements.txt
python migrate.py   # one-time import (safe to re-run)
python app.py       # -> http://127.0.0.1:5000
```

## Structure
- `app.py` — Flask frontend (dashboard, receipts, upload, mappings, prices)
- `db.py` — SQLite schema (`data/lidl.db`)
- `receipt_parser.py` — fixed parser (kg lines, `26-08-16.1.txt` dates, discounts)
- `migrate.py` — imports `product_map.csv` + `Barcodes.xlsx` + `receipts/*.txt` + `product_history.csv`
- `templates/`, `static/` — browser UI

## DB tables
- `products(raw UNIQUE, clean, category, barcode, brand, unit, weight, notes, is_active, ...)`
- `receipts(filename UNIQUE, receipt_date ISO, receipt_seq, store, item_count, total)`
- `items(receipt_id FK, raw, clean, category, qty, unit_price, vat, discount, total, final, match_type, match_score)`
- `price_history(raw, clean, category, date, price, source)` — legacy CSV continuity

## Verified migration
54 receipts, 833 items, total 2369.95 €, 810 exact / 4 fuzzy / 19 new.
Receipt `26-09-05.txt` parses to exactly 41.91 € (matches paper total).
KG fix: `0,590 kg x 14,79` -> qty=0.59, unit=14.79 (old code got qty=1, unit=8.73).

## Fetch (Firefox extension)

Server-side Lidl login is removed. Receipts arrive via the Firefox extension
(`extension/`, Manifest V2, sideload at `about:debugging → This Firefox →
Load Temporary Add-on`):

1. Log into lidl-hellas.gr once in Firefox (normal browsing).
2. Open the extension popup, set Server URL (`http://host:5090`), paste the
   token from dashboard Settings → Extension, set the page cap (default 5).
3. Sync = incremental catch-up. Check "Full history scan" for a first run
   against an old account (sequential requests, 1.5–3 s throttle, 100-page
   backstop, Cancel anytime; rerunning resumes automatically).
4. Review imported receipts on the dashboard; nothing auto-pushes to Grocy.

Remote/VPN servers: use HTTPS; a self-signed cert needs a one-time browser
exception in Firefox. Manual fallback: dashboard paste form, or
`python fetch_lidl.py --paste pasted.txt --tid 14000...`.
