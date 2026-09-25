# Lidl2Grocy

line 1

Grocery receipt tracker: pulls digital receipts from Lidl Hellas, parses and
maps products, links them to [Grocy](https://grocy.info), compares prices with
ABC analysis, and splits spending between users.

```
+---------------+                              +-------------------------+                                     +----------------------+                                  +---------------+
| User browser  |----------HTTP :5090----------|Flask/gunicorn app :5090 |-------------drives-sync-------------| Playwright Chromium  |---------purchase history---------|lidl-hellas.gr |
|               |                              |                         |                                     |                      |                                  |               |
+---------------+                              +-------------------------+---products/stock-API--+             +----------------------+                                  +---------------+
                                                                                                 |                  +------------+
                                                                                                 +----------------->| Grocy API  |
                                                                                                                    |            |
                                                                                                                    +------------+
```

## Setup (Docker, Debian server — recommended)

Prereqs on the server: Docker Engine + Compose plugin, nothing else.

```bash
git clone https://github.com/SordyGR/Lidl2Grocy.git
cd Lidl2Grocy/"Lidl Parser"
cp .env.example .env   # then fill in values (see below)
docker compose up -d --build
```

Open `http://<server-ip>:5090`.

`.env` values:

| Key | Meaning |
|-----|---------|
| `LIDL_USER` / `LIDL_PASS` | Lidl Hellas login, used for headless receipt sync |
| `GROCY_URL` | Grocy base URL **with** `/api` suffix |
| `GROCY_API_KEY` | Grocy API key |
| `SECRET_KEY` | Flask secret, change it |

Data (`./data`: SQLite DB + Lidl browser profile) and receipt texts
(`./receipts`) live in bind-mounted volumes — they survive restarts and
upgrades. Never commit `.env`, `data/`, or `receipts/` (gitignored).

## Setup (local dev, Windows)

```bash
cd "Lidl Parser"
pip install -r requirements.txt
playwright install chromium
python migrate.py   # one-time import (safe to re-run)
python app.py       # -> http://127.0.0.1:5000
```

## First run checklist

1. **Settings** page: enter Lidl username/password (or rely on `.env`), Grocy
   URL + API key. "Test" links verify each. Settings saved in-UI override
   `.env` values at runtime; `SECRET_KEY` stays env-only.
2. **Users + cards**: add the people you track (e.g. household members), then
   map each card's **last 4 digits** to its owner. Only last-4 digits are ever
   stored — never full card numbers.
3. **Dashboard → Sync receipts**: pulls new receipts headless. If Lidl blocks
   automation you'll get "login needed" — use the paste box fallback (paste
   the purchase-detail page text + transaction id).
4. **Mappings**: link each raw receipt product to a Grocy product (suggestions
   included), or create a full Grocy item via the ERP-style new-item page.
5. Open a receipt, check splits, **Push to Grocy**.

## Usage wiki

### Dashboard
KPIs (receipts, items, spend, unmapped), monthly spend, spend by category,
recent receipts, top products, per-user spend totals, and the Sync button
with live status (running → new/skipped/needs-review).

### Receipts & splits
Each receipt shows its lines plus a **Paid with** line (`****1236 → owner`
from your card mapping, or `Cash`). The Owner column assigns each line
wholly to one user or splits it proportionally (`1:0.6,2:0.4` shares must
sum to 1.0). Unvisited receipts default to equal shares across all users.
Splits drive the dashboard per-user totals; the Grocy push always sends full
stock amounts (Grocy has no user concept).

### Mappings (raw → clean) & ERP item creation
Search/filter the raw→clean product map (unmapped, linked/unlinked,
tracked/skipped), edit names/categories/barcodes inline or in bulk. Linking
attaches a local product to an existing Grocy product. **New-item page**
(`/mappings/new-item`) is the ERP-style flow: identity (name/brand/barcode/
description), storage (location, min stock), units (purchase/stock +
conversion factor), classification (product group, parent product) — creates
in Grocy, attaches the barcode, and links locally in one submit.

### Prices & ABC analysis
Pick a metric (spend / quantity / frequency) and top-N: the page seeds the
comparison from the ABC ranking, and the searchable dropdown adds more
products (menu stays open, click toggles). Overlaid price-trend chart on top,
Pareto bars + cumulative-% line below, stat table at the bottom. Classes:
A ≤ 80% cumulative, B ≤ 95%, C rest.

### Settings reference
Lidl credentials (password never rendered back), Grocy URL/key, users,
card→user map. Resolution order per key: UI-saved value → environment →
empty. Grocy URL must end with `/api`.

### Receipt lifecycle
```
           +-----------------+
           | Dashboard sync  |
           +-----------------+
                    |
              stored creds
                    |
           +----------------+
           |Lidl auto-login |
           +----------------+
                    |
              receipt text
                    |
       +--------------------------+
       |Parse items + card last-4 |
       +--------------------------+
                    |
             product memory
                    |
         +-------------------+
         |Match raw to Grocy |
         +-------------------+
          |                   |
     if missing          linked items
          |                   |
          v                   v
 +----------------------+  +------------------------+
 |ERP new-item creation |--->|Assign / split per user |
 +----------------------+  +------------------------+
                               |                   |
                         final x share       full amounts
                               |                   |
                               v                   v
                        +-------------+  +--------------------+
                        |Prices + ABC |  |Push to Grocy stock |
                        +-------------+  +--------------------+
```

## Troubleshooting
- **Sync says "login needed"**: Lidl changed or blocked the form — use the
  paste-box fallback on the dashboard.
- **Second receipt same day**: imports as `YY-MM-DD.N.txt`, ordered after the
  first; re-syncs skip already-seen transaction ids.
- **Grocy unreachable**: pages render with cached catalog + "stale" warning;
  pushes abort safely without partial sends (force re-push may duplicate).
- **Stale Grocy link**: mappings flag it ("stale link — relink?") when the id
  vanished from the Grocy catalog.
- **Port taken**: change `5090:5090` in `Lidl Parser/compose.yml`.

## Development
- Tests: `cd "Lidl Parser" && python -m pytest tests/ -q`
- Design docs live in `docs/superpowers/specs`, implementation plans in
  `docs/superpowers/plans`; diagrams as d2 sources in `docs/diagrams`.
