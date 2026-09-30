# Extension

## Status: in development — NOT on the Firefox Add-ons store

`Lidl Parser/extension/` is Manifest V2 (`manifest.json`, version 1.0),
sideload-only. Consequences:

- It **unloads when Firefox closes** — repeat Load Temporary Add-on after
  every restart.
- No auto-updates: re-clone/pull to get fixes.
- No store review constraints yet, but also no store trust signals.
- Store listing (signing + AMO review, MV3 assessment) is on the
  [roadmap](Roadmap.md) and gates v1.0.0.

## Install walkthrough (`about:debugging`)

1. `git clone https://github.com/SordyGR/Lidl2Grocy.git && cd Lidl2Grocy`
2. Server running (Docker `:5090` or local dev `:5000`).
3. Firefox → address bar → `about:debugging` → **This Firefox** →
   **Load Temporary Add-on…** → select
   `Lidl Parser/extension/manifest.json`.
4. Log into lidl-hellas.gr once in the same Firefox (normal tab).
5. Toolbar popup: **Server URL** (`http://<server-ip>:5090` LAN,
   `https://…` remote), **Token** (app Settings → Extension → Copy),
   **page cap** (default 5 daily, higher for catch-ups).
6. **Sync** (incremental) or tick **Full history scan** (first run on an old
   account). Review imports on the dashboard.

HTTPS with a self-signed cert: open the server URL once in Firefox and accept
the exception, otherwise the popup reports "Server unreachable".

## How a sync works

1. Content script on purchase-history pages collects transaction ids (`t=`).
2. `POST /api/ext/need {tids}` → server returns ids absent from `fetch_state`.
3. Detail pages load **sequentially** (1.5–3 s throttle, 100-page backstop,
   Cancel anytime, resume on next run); receipt text POSTs to
   `/api/ext/receipt {tid, text}`.
4. Server trims/parses, dedupes by **tid + fiscal MAPK**, stores text + lines,
   records `fetch_state`. `"duplicate"` counts as skipped, never as failure.
5. Nothing pushes to Grocy — every receipt awaits manual review.

## Ingest API (Bearer token)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/ext/status` | health: `{"ok", "last_sync", "receipts"}` (401 without token) |
| `POST /api/ext/need` | diff tids (≤ 5000) → `{"new": [...]}` |
| `POST /api/ext/receipt` | import → `imported` / `duplicate` / `error` |

Rule: sequential requests, abort on Lidl refusal, free resume — never hammer
Lidl (SSO throttles automation).

## Fallbacks (no extension)

Dashboard paste-box, `/upload` `.txt` files, or
`python fetch_lidl.py --paste f.txt [--tid …]`; headless-Chromium CLI
(`--latest-only`, `--max`, `--headed`, needs `playwright`, kept out of
requirements so Docker stays slim).
