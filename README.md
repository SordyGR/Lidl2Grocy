# Lidl2Grocy

Grocery receipt tracker: pulls digital receipts from Lidl Hellas, parses and
maps products, links them to [Grocy](https://grocy.info), compares prices with
ABC analysis, and splits spending between users.

```
+---------------+                              +-------------------------+                                     +---------------+
| Firefox +     |----------HTTPS :5090---------|Flask/gunicorn app :5090 |----------products/stock-API--------->| Grocy API  |
| extension     |   Bearer token ingest API    |                         |                                     +---------------+
| (your login,  |                              |                         |
| real session) |                              |                         |
+-------+-------+                              +-------------------------+
        | purchase history + receipt details
        v
+---------------+
|lidl-hellas.gr |
+---------------+
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
| `GROCY_URL` | Grocy base URL **with** `/api` suffix |
| `GROCY_API_KEY` | Grocy API key |
| `SECRET_KEY` | Flask secret, change it |

Data (`./data`: SQLite DB) and receipt texts (`./receipts`) live in
bind-mounted volumes — they survive restarts and upgrades. Never commit
`.env`, `data/`, or `receipts/` (gitignored).

## Setup (local dev, Windows)

```bash
cd "Lidl Parser"
pip install -r requirements.txt
python migrate.py   # one-time import (safe to re-run)
python app.py       # -> http://127.0.0.1:5000
```

## First run checklist

1. **Settings** page: enter Grocy URL + API key. "Test" link verifies it.
   Copy the **Extension token** (Settings → Extension) for step 3. Settings
   saved in-UI override `.env` values at runtime; `SECRET_KEY` stays
   env-only.
2. **Users + cards**: add the people you track (e.g. household members), then
   map each card's **last 4 digits** to its owner. Only last-4 digits are ever
   stored — never full card numbers.
3. **Firefox extension** (`Lidl Parser/extension/`, see walkthrough below):
   sideload it, log into lidl-hellas.gr in Firefox, paste the server URL +
   token, Sync. New receipts land as reviewable imports.
4. **Mappings**: link each raw receipt product to a Grocy product (suggestions
   included), or create a full Grocy item via the ERP-style new-item page.
5. Open a receipt, check splits, **Push to Grocy**.

## Extension install & testing walkthrough

You need: the server running (Docker or local dev, either is fine) and
Firefox on any machine that can reach it (LAN, VPN, or remote URL).

**Install (once):**
1. Check out this branch: `git clone https://github.com/SordyGR/Lidl2Grocy.git`
   then `cd Lidl2Grocy` and `git checkout Gamma` (until Gamma merges, the
   extension only exists here).
2. In Firefox open `about:debugging` → This Firefox → Load Temporary Add-on
   → pick `Lidl Parser/extension/manifest.json`. (Temporary sideloads unload
   when Firefox closes — repeat this step after a restart.)
3. Log into lidl-hellas.gr once, in the same Firefox (normal browsing).
4. Open the extension popup: Server URL = your server
   (`http://<server-ip>:5090` on LAN; `https://…` remote), Token = dashboard
   Settings → Extension → Copy. Page cap: default 5 is daily-use; raise it
   for catch-ups.

**Sync:**
- **Sync** = incremental catch-up (stops at the first page with nothing new,
  or the page cap).
- **Full history scan** (checkbox) = first run against an old account: walks
  every history page (sequential requests, 1.5–3 s throttle, 100-page
  backstop). Cancel anytime — finished receipts stay imported, the rest
  resumes on the next Sync.
- If a run reports failures, just Sync again later; failed receipts retry
  automatically. Nothing ever auto-pushes to Grocy — review first.

**Verify the server API by hand (optional):**

```bash
curl -s http://<server-ip>:5090/api/ext/status                 # → 401 {"error": ...}
curl -s -H "Authorization: Bearer <token>" http://<server-ip>:5090/api/ext/status
# → {"last_sync": ..., "ok": true, "receipts": N}
```

**Run the automated suite:**

```bash
cd "Lidl Parser"
python -m pytest -q        # 120 passed expected on Gamma
```

Manual fallback never goes away: dashboard paste form, or
`python fetch_lidl.py --paste pasted.txt --tid 14000...`.

**Local headless sync (no extension, no Docker):** for local runs the CLI
keeps its Chromium path. One-time setup (NOT in requirements.txt, so the
Docker image stays slim):

```bash
pip install playwright && playwright install chromium
python fetch_lidl.py --latest-only   # daily use (headless)
python fetch_lidl.py --max 5         # cap history pages
python fetch_lidl.py --headed        # opens a browser once; log in, rerun
```

Credentials come from Settings/`LIDL_USER` + encrypted `LIDL_PASS` (or
`.env`); the signed-in session persists in `data/.lidl-profile/`
(gitignored). Note Lidl sometimes refuses automated sign-in ("system
overload") — wait ~30 min and try once, or use the extension instead.

## Usage wiki

### Dashboard
KPIs (receipts, items, spend, unmapped), monthly spend, spend by category,
recent receipts, top products, per-user spend totals. Receipts arrive via
the Firefox extension (or the paste-box fallback below the KPIs).

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
Extension token (show/copy/regenerate — paste it into the Firefox extension),
Grocy URL/key, users, card→user map. Resolution order per key: UI-saved
value → environment → empty. Grocy URL must end with `/api`.

### Receipt lifecycle
```
          +----------------------+
          | Firefox extension    |
          | Sync (your session)  |
          +----------------------+
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
- **Extension says "Log into lidl-hellas.gr first"**: open Lidl in the same
  Firefox and sign in, then Sync again.
- **Extension says "Token rejected"**: re-copy it from Settings → Extension
  (it changes on Regenerate).
- **Extension says "Server unreachable"**: wrong URL, container down, or —
  on HTTPS with a self-signed cert — open the server URL once in Firefox and
  accept the exception.
- **Sync reports failures**: Sync again later; failed receipts retry
  automatically and never duplicate.
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
