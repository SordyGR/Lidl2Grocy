# Lidl2Grocy

Grocery receipt tracker: pulls digital receipts from Lidl Hellas, parses and
maps products, links them to [Grocy](https://grocy.info), compares prices with
ABC analysis, and splits spending between users.

**Current version: 0.9.0** (see [Versioning](#versioning)). Full wiki:
[`docs/wiki/Home.md`](docs/wiki/Home.md) — including the
[SWOT analysis](docs/wiki/SWOT.md) and [roadmap](docs/wiki/Roadmap.md).

> **Extension status:** the Firefox extension is still **in development** and
> **not listed on the Firefox Add-ons store**. Install it as a temporary
> sideload — [walkthrough below](#firefox-extension-dev-status--install).

```
+---------------+                              +-------------------------+
| Firefox +     |----------HTTPS :5090---------|Flask app :5090          |---products/stock-API---> Grocy API
| extension     |   Bearer token ingest API    |  (SQLite, Jinja, v1+v2) |
| (your login,  |                              |                         |
| real session) |                              +-------------------------+
+-------+-------+
        | purchase history + receipt details
        v
+---------------+
|lidl-hellas.gr |
+---------------+
```

## Features

- **Receipt ingest** — Firefox extension sync (incremental + full history scan)
  into a token-gated API; paste-box and `.txt` upload fallbacks; local
  headless-Chromium CLI (`fetch_lidl.py`). Dedupe by Lidl transaction id **and**
  the fiscal **MAPK** receipt id, so re-syncs never duplicate.
- **Receipts** — line detail (clean name, category, qty, unit price, VAT %,
  discount, final), card last-4 detection, `.txt` download, rescan/repair
  actions (cards, names), per-user splits (equal default, exact per-line
  override), manual Grocy push with preview — **nothing auto-pushes**.
- **Products (raw → clean)** — curated catalog with Grocy linking (fuzzy +
  barcode suggestions), duplicate detection/merge, ERP-style Grocy item
  creation, xlsx export/import with conflict review, category merge review.
- **Prices & ABC** — product *and* category Pareto with ABC classes, rank
  badges, cumulative line, price trends, `analysis.xlsx` export.
- **Dashboard** — KPIs, monthly spend, category doughnut with range total,
  top products, per-user totals, chart category exclusions.
- **Settings** — hidden API key, expandable sections, user rename/vacation/
  guarded delete, card reassignment, cached Units/Categories with Grocy
  refresh, DB backup. Two skins (v1 default, v2 via `/ui/v2`) ×
  light/dark/system theme.

## Setup (Docker — prebuilt image, no build needed)

Prereqs: Docker Engine + Compose plugin, nothing else.

```bash
mkdir lidl2grocy && cd lidl2grocy
curl -O https://raw.githubusercontent.com/SordyGR/Lidl2Grocy/main/Lidl%20Parser/compose.yml
curl -O https://raw.githubusercontent.com/SordyGR/Lidl2Grocy/main/Lidl%20Parser/.env.example
cp .env.example .env   # then fill in values (see below)
docker compose up -d
```

Open `http://<server-ip>:5090`. Pin a version with `TAG=v0.9.0 docker compose
up -d` (defaults to `latest`).

Developers (local build): check out the repo and run
`docker compose -f compose.yml -f compose.dev.yml up -d --build`
from `Lidl Parser/`. First publish needs repo Settings → Actions →
General → Workflow permissions → **Read and write**.

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

Isolated DB for experiments: `LIDL_DB=/tmp/x.db python fetch_lidl.py …`.
Tests: `cd "Lidl Parser" && python -m pytest -q` (185 passed).

## First run checklist

1. **Settings**: enter Grocy URL + API key ("Test" verifies it). Copy the
   **Extension token** for step 3. In-UI values override `.env` at runtime;
   `SECRET_KEY` stays env-only.
2. **Users + cards**: add household members, map each card's **last 4 digits**
   to its owner. Only last-4 digits are ever stored.
3. **Firefox extension** (sideload, see below): log into lidl-hellas.gr in
   Firefox, paste server URL + token, Sync. Imports are reviewable, never
   automatic.
4. **Products**: map raw names to clean names (or import a shared catalog),
   link to Grocy products.
5. Open a receipt, check splits, **Push to Grocy**.

## Firefox extension (dev status + install)

⚠️ **In development — not on the Firefox Add-ons store.** It runs as a
Manifest V2 temporary add-on: it unloads when Firefox closes, so repeat the
load step after a restart. Store listing (signing, AMO review) is tracked on
the [roadmap](docs/wiki/Roadmap.md).

**Install (once per Firefox session):**
1. `git clone https://github.com/SordyGR/Lidl2Grocy.git && cd Lidl2Grocy`
2. Firefox → open `about:debugging` → **This Firefox** → **Load Temporary
   Add-on…** → pick `Lidl Parser/extension/manifest.json`.
3. Log into lidl-hellas.gr once, in the same Firefox (normal browsing).
4. Extension popup: Server URL (`http://<server-ip>:5090` on LAN, `https://…`
   remote), Token = Settings → Extension → Copy. Page cap default 5 for daily
   use; raise for catch-ups.

**Sync modes:**
- **Sync** = incremental catch-up (stops at first page with nothing new).
- **Full history scan** = first run against an old account: sequential
  requests, 1.5–3 s throttle, 100-page backstop, Cancel anytime, resume on
  next Sync.
- Failures retry on the next Sync and never duplicate (tid + MAPK dedupe).

HTTPS with a self-signed cert needs a one-time browser exception in Firefox.

## Versioning

[SemVer](https://semver.org/) `MAJOR.MINOR.PATCH`. The root `VERSION` file is
the source of truth; releases are tags `vX.Y.Z` on `main`.

| Bump | When |
|------|------|
| MAJOR | Breaking change (DB rebuild required, API/route removal) |
| MINOR | New feature (page, endpoint, extension capability) |
| PATCH | Bug fix, no behavior contract change |

Pre-1.0 (`0.x`): DB schema and ingest API may still evolve; every MINOR
documents migrations in the release notes. `1.0.0` = extension store-listed
(or stable sideload) + frozen ingest API + green fresh-install. The extension
`manifest.json` version tracks app `MAJOR.MINOR`.

## Troubleshooting

- **"Log into lidl-hellas.gr first"**: sign in on the site in the same
  Firefox, Sync again.
- **"Token rejected"**: re-copy from Settings → Extension (changes on
  Regenerate).
- **"Server unreachable"**: wrong URL, container down, or accept the
  self-signed cert exception once.
- **Failures / duplicates**: Sync again; MAPK dedupe skips seen receipts.
  Old rows mapped before a fix: **Receipts → Resync names**.
- **Grocy unreachable**: pages render cached + "stale" warning; pushes abort
  safely (force re-push may duplicate).
- **Port taken**: change `5090:5090` in `Lidl Parser/compose.yml`.

## Development

- Tests: `cd "Lidl Parser" && python -m pytest -q`
- Design docs: `docs/superpowers/specs`, plans: `docs/superpowers/plans`
- Local app notes: [`Lidl Parser/README.md`](Lidl%20Parser/README.md)
