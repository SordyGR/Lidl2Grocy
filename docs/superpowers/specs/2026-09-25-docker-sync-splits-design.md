# Docker Deploy + In-UI Sync + Card/User Splits

Date: 2026-09-25 | Status: approved | Approach: A (monolith + compose)

## Intent

Run the whole Lidl Parser on a Debian server via Docker Compose with every
feature reachable from the site: dashboard-triggered Lidl sync (credentials
from settings/env/compose), Grocy config from settings, card-last-4 → user
mapping, and per-item receipt splitting between users.

## Decisions (from brainstorming)

- Lidl auth: auto-fill stored credentials headless; paste fallback in-UI.
- Splits: whole-item assign + proportional split; default equal shares.
- Container port: 5090. Volumes persist DB, Lidl profile, receipt texts.

## Section 1 — Docker packaging

- `Dockerfile` in `Lidl Parser/`: `python:3.13-slim-bookworm`, Playwright OS
  deps + `playwright install chromium`, `pip install -r requirements.txt` +
  `gunicorn`; startup runs `migrate.py`, serves `app:app` via gunicorn
  (1 worker + threads, SQLite-safe) on 5090.
- `compose.yml` in `Lidl Parser/`: service `lidl-parser`, `5090:5090`,
  `env_file: .env`, volumes `./data:/app/data`, `./receipts:/app/receipts`.
- `.env.example`: `LIDL_USER`, `LIDL_PASS`, `GROCY_URL`, `GROCY_API_KEY`,
  `SECRET_KEY`. Debian host needs only Docker + compose plugin.

## Section 2 — Settings + credentials

- New `settings(key PK, value)` table. Resolution: UI value → env/compose → empty.
- New `/settings` page (nav link): Lidl user/pass + test-login; Grocy URL/key +
  test via `/api/grocy_status`; users add/rename; card last-4 → user mapping.
- Plaintext-password note on page; `.env`/compose is the recommended path for
  secrets. `SECRET_KEY` env-only, never in DB.

## Section 3 — In-UI receipt sync

- Dashboard "Sync receipts" button + last-sync line (from `fetch_state`).
  `POST /fetch/run` starts `cmd_sync(latest_only=True)` in a background
  thread (single `running` flag lockout); `GET /api/fetch_status` polled for
  running/done, new/skipped/needs-review, error.
- Auth: stored-credential auto-fill headless; persistent profile in `data/`
  volume keeps later syncs login-free. Failure → "login needed" status +
  in-UI paste box (`cmd_paste` path).
- Same-day second receipt: `pick_filename` seq suffix (`26-09-05.1.txt`) +
  `t_id` dedup already prevent collisions; listings gain `receipt_seq`
  secondary sort; sync result names each imported file.

## Section 4 — Cards, users, splits

- New tables: `users(id, name UNIQUE)`, `card_map(last4 PK, user_id FK)`,
  `item_splits(item_id FK, user_id FK, share REAL, PK(item_id, user_id))`;
  new `receipts.card_last4` column. Only last-4 stored, never full numbers.
- Parser extracts last-4 from `*`-masked lines (`************1234`,
  `41111111****1234`); null when absent/cash.
- Receipt page: "Paid with ****1234 → owner" line; items gain Owner column
  (per-row user select = share 1.0, or split mode with per-user amounts
  summing to row total). First view seeds equal shares across users; per-row
  POST saves (mappings pattern).
- Dashboard per-user spend totals (SUM `final × share`). Grocy push unchanged.

## Files touched (planned)

- New: `Dockerfile`, `compose.yml`, `.env.example`, `templates/settings.html`.
- `db.py`: settings/users/card_map/item_splits tables + `receipts.card_last4`.
- `app.py`: `/settings` GET/POST, `/fetch/run`, `/api/fetch_status`,
  receipt splits GET/POST, dashboard user totals, `receipt_seq` sort.
- `receipt_parser.py` (+`fetch_lib.py` if text lives there): card-last4 extract.
- `fetch_lidl.py`: credential auto-fill login; reuse `cmd_sync`/`cmd_paste`.
- `requirements.txt`: add `gunicorn`. No changes to push flow or ABC/prices.
