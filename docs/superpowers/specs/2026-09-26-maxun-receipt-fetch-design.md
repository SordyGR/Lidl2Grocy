# Maxun Receipt Fetch — Design Spec (2026-09-26)

## 1. Intent

Fetch Lidl Hellas digital receipts via Maxun as the primary backend, keeping
the existing Playwright crawler (`Lidl Parser/fetch_lidl.py`) as an automatic
fallback. Everything downstream of text acquisition — trim, parse, match,
import — is shared and untouched. Success: unattended runs import new receipts
with verified totals; any Maxun failure still yields receipts via Playwright.

Prior spec: `2026-09-23-lidl-receipt-fetch-design.md` (Playwright approach A,
direct-API fallback B). This spec adds approach C (Maxun primary) without
removing A.

## 2. Locked decisions

1. **Maxun's role:** Maxun is the scraper tool, not the codebase. No Maxun
   fork; Lidl2Grocy talks to it over HTTP.
2. **Coexistence:** Maxun primary, Playwright fallback (not replacement).
   The fallback must keep working unattended, which rules out a push/webhook
   design where a failed Maxun run triggers nothing.
3. **Maxun returns raw receipt text**, not structured rows. Both backends feed
   the identical trim → parse → import chain, so the fallback is cheap and
   `parse_receipt` stays the single tested source of truth.
4. **Pull, with adaptive fallback:** Lidl2Grocy drives (`/fetch/run` calls
   Maxun's API, waits, imports; on failure runs Playwright). Maxun's own
   scheduling may be adopted later for unattended runs; the pull path stays
   regardless so the fallback guarantee holds.

## 3. Architecture & components

New `Lidl Parser/fetch_maxun.py` — same contract as `fetch_lidl.cmd_sync`
(exit-code int, prints summary). It differs only in how it obtains text.

- **Orchestrator `run_fetch(source="auto")`** in `fetch_maxun.py`, the single
  fallback site. Used by `app.py._run_sync()` and by the CLI. `source` in
  `auto` | `maxun` | `playwright`:
  - `playwright` → `fetch_lidl.cmd_sync(latest_only=True)` directly.
  - otherwise → try Maxun; on any failure log the reason and fall back to
    `fetch_lidl.cmd_sync(latest_only=True)`.
- **Maxun client** (stdlib `urllib.request`, no new pip dependency):
  - `POST {MAXUN_URL}/api/robots/{MAXUN_ROBOT_ID}/runs`, header
    `x-api-key: {MAXUN_API_KEY}` → `{ runId }` (both routes verified in
    Maxun source at commit `8fca592`: trigger in `server/src/api/record.ts`,
    `x-api-key` auth in `server/src/middlewares/api.ts`).
  - Poll `GET {MAXUN_URL}/api/robots/{id}/runs/{runId}` (~5 s interval,
    ~180 s bound) until terminal. Exact run-result payload shape for the
    extracted text field is confirmed against the live instance during
    implementation; the client parses defensively (missing text field =
    failure → fallback).
- **Robot (lives in Maxun, not this repo):** login step with stored Lidl
  credentials → `purchase-history?client_id=GreeceRetailClient&country_code=gr&language=el-GR`
  → scrape-list latest `purchase-detail?t=…` link → scrape receipt container
  as one text field. Retrained in Maxun's UI; no deploy needed for selector
  drift.
- **Shared import chain (untouched):** `trim_receipt_text` →
  `receipt_body_date` → `pick_filename` → `import_kept_receipt(conn, kept,
  filename, t_id)`; receipt file written to `receipts/<filename>` exactly as
  the Playwright path does (import first, then write file, mirroring
  `cmd_sync`/`cmd_paste` order).

Untouched: `fetch_lib.py`, `receipt_parser.py`, `db.py` schema, `grocy.py`,
`migrate.py`, `Transformer.py`, `pipeline.py`.

Modified:
- `app.py` `_run_sync()` — call `run_fetch(source from settings)` instead of
  `fetch_lidl.cmd_sync` directly.
- `settings_store.py` — extend `KNOWN_KEYS` with `FETCH_SOURCE`,
  `MAXUN_URL`, `MAXUN_API_KEY`, `MAXUN_ROBOT_ID` (`set()` silently ignores
  unknown keys, so this extension is required, not optional). UI-over-env
  resolution unchanged.
- `fetch_lidl.py` CLI — add `--source maxun|playwright|auto` (default `auto`)
  so cron runs one command; `/settings` page gains fields for the new keys.

## 4. Data flow

1. Trigger — UI button (`/fetch/run`, unchanged) or cron →
   `run_fetch(source)`.
2. If `source == playwright`, jump to step 7.
3. `POST` run to Maxun, poll to terminal.
4. Extract raw receipt text + its `t=` id from the run result.
5. `fetch_state` pre-check on `t_id` (skip if already imported).
6. Shared chain: trim → date → `pick_filename` → `import_kept_receipt` →
   write `receipts/<filename>`.
7. Fallback: `fetch_lidl.cmd_sync(latest_only=True)`.
8. One summary naming which backend served and why the other was skipped.

## 5. Error handling & idempotency

- Every Maxun failure mode — unreachable host, 401/403, run failed, poll
  timeout, missing text field, `ValueError` from trim/date/total — logs its
  reason and falls back. No exception type bypasses the fallback.
- The €0.02 total check inside `import_kept_receipt` (parsed total vs
  `ΣΥΝΟΛΟ` line) is the newline-fidelity gate: mangled Maxun text is
  rejected by the shared path, never silently imported.
- Idempotent re-runs: `fetch_state` pre-check, `pick_filename` (disk + DB),
  `INSERT OR IGNORE` + items delete+reinsert.
- Login expiry on the Maxun side surfaces as run failure → Playwright
  fallback → existing `LOGIN NEEDED` flow if the profile session is also
  dead. No password handling in this repo on either path.
- Trim/parse failures → receipt not written to DB, listed as needs-review,
  consistent with current behavior.
- Known limitation (carried forward): Maxun path is **latest-only**; history
  backfill stays Playwright-only. Full sync can re-import manually saved
  receipts under other filenames because `fetch_state` only knows `t_id`s it
  imported — unchanged by this spec.

## 6. Credentials & Maxun hosting

- Lidl credentials for the Maxun path live in Maxun's credential store
  (entered once in Maxun UI; Maxun source confirms credential extraction and
  grouping into passwords/emails, plus cookie capture in `maxun-core`).
  Lidl2Grocy never sees them on that path. The Playwright path keeps using
  `LIDL_USER`/`LIDL_PASS` + `data/.lidl-profile/` as today.
- `MAXUN_API_KEY` via `settings_store` (UI wins, env seeds); never logged.
- `MAXUN_URL` makes Cloud vs self-hosted a config value, not a code branch.
  Phase 1 validates on Maxun Cloud (no self-hosting commitment); self-hosting
  via a compose addition is a later, optional step. Maxun runs headless
  server-side with stored creds either way, which is what makes this
  unattended-safe.

## 7. Testing

Follow the existing `tests/test_fetch_*.py` pattern (mocked HTTP via
`urllib`, monkeypatched import chain):

1. Maxun success → shared chain called with trimmed text + `t_id`.
2. Maxun unreachable → Playwright `cmd_sync` invoked.
3. Run failed / poll timeout / missing text → fallback invoked.
4. Trim `ValueError` on Maxun text → fallback invoked.
5. `FETCH_SOURCE=playwright` → Maxun never contacted.
6. Live: `--latest-only` output diffed against a known-good `receipts/*.txt`
   (reference: `26-09-05.txt` → 41.91 €); second run imports 0.
7. Pre-implementation spike (throwaway): record one receipt robot in Maxun,
   pull text, run the §7 table from the 2026-09-23 discussion (trim, date,
   total, `parse_receipt` non-empty, €0.02 reconciliation, byte-diff). The
   spike decides nothing about code; it validates the raw-text assumption.

## 8. Rollout

1. Spike passes → implement `fetch_maxun.py` + settings keys + `app.py`
   wiring + tests.
2. `pip` unchanged (stdlib only); `migrate.py` untouched (no schema change).
3. `/settings` UI fields for `FETCH_SOURCE` (default `auto`), `MAXUN_URL`,
   `MAXUN_API_KEY`, `MAXUN_ROBOT_ID`; robot setup steps in README Fetch
   section.
4. Cron (container or host) invokes the CLI with default `auto` source for
   unattended runs.
5. Rollback: set `FETCH_SOURCE=playwright` — Maxun code path fully bypassed.
