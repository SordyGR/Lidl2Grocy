# Lidl Parser — DB + Web UI

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

## Fetch (auto-download from Lidl Hellas)

```
pip install -r requirements.txt
playwright install chromium
python fetch_lidl.py --latest-only   # daily use
python fetch_lidl.py --max 5         # cap history pages
python fetch_lidl.py --paste pasted.txt --tid 14000...  # manual fallback, no login
```

First run opens a browser: log in once at lidl-hellas.gr, then rerun. Session persists in `data/.lidl-profile/` (git-ignored).
