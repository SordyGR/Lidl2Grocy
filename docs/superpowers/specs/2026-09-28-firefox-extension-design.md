# Firefox Extension Scraper — Design (Option A)

Date: 2026-09-28 | Branch: `Gamma` (based on `main @ ca39917`) | Status: spec for review (not yet planned/implemented)

## 1. Background & intent

Server-side Lidl login (Playwright auto-login + local headed-Chromium fallback)
is scrapped. It failed as a class: Lidl SSO answers automation with "system
overload" refusals + 30-min cooldowns, and the headed fallback cannot run on a
headless server (the container-browser experiment on `beta` confirmed the whole
remote-desktop direction is a dead end). Effort goes into patching the tunnel
instead of the product.

New shape: a Firefox extension runs in the user's real, manually-logged-in
session (no bot fingerprint), scrapes purchase history + receipt details, and
POSTs to the Flask app. Flask keeps everything it does well: SQLite, receipt
parsing, review UI, Products hub, Grocy push. Server-side scraping is deleted.

## 2. Agreed decisions (do not re-litigate without cause)

- Scope: **full scraper** in the extension (history crawl + detail fetch), not
  per-page helper, not session-bridge.
- Auth: **shared bearer token** (`EXT_INGEST_TOKEN`), pasted once into
  extension settings. No pairing flow in v1.
- Sync: **diff-then-fetch** — extension collects tids, server says which are
  new, extension fetches details only for those.
- Scan modes: incremental by default (stop at first page with zero unseen
  tids, user-configurable page cap, default 5); **"Full history scan"
  checkbox** for first run / old accounts (ignores the cap, paginates until
  empty page / no next-page link, with sweep precautions per §4).
- Manifest **V2**, Firefox-only, repo-sideloaded via `about:debugging`
  (no Mozilla signing in v1). No Chrome port (YAGNI).
- Server location is **not assumed local**: free-form `http(s)://host:port`
  (LAN today, VPN/remote tomorrow). Token is a secret: HTTPS required when
  remote; self-signed certs need a one-time browser exception.

## 3. Architecture

- **Extension** (`extension/`): `popup.html/js` (server URL + token + page-cap
  field + Sync + full-scan checkbox + progress/cancel + status),
  `background.js` (orchestration, chunked `/need`, per-tid fetch + `/receipt`
  POST, sweep throttle/abort), `content_history.js` (tid regex
  on `purchase-detail` links), `content_detail.js` (`body.innerText` +
  login-redirect sniff). Permissions: `storage`, `*://*.lidl-hellas.gr/*`,
  `http://*/* + https://*/*` (covers LAN/VPN/remote URLs). Page cap, URL, and
  token persist in `storage.local`.
- **Flask ingest API** (new, token-gated):
  - `POST /api/ext/need` `{tids:[...]} → {new:[...]}` (diff vs `fetch_state`;
    client splits lists into chunks of ≤200 tids; server also tolerates
    larger bodies).
  - `POST /api/ext/receipt` `{tid, text} → {imported|duplicate|error}` —
    runs the existing `trim_receipt_text → import_kept_receipt` path, so
    filenames/dates/dedupe are unchanged; records `fetch_state` on success.
  - `GET /api/ext/status` `{ok,last_sync,receipts}` (failure display lives in the extension popup; failed tids are never recorded so the next `need` retries them)
  - Auth: `Authorization: Bearer <token>`, constant-time compare, 401 on
    mismatch, token never logged. `Access-Control-Allow-Origin: *` on
    `/api/ext/*` only.
- **Deletions on Gamma:** Playwright/`fetch_lidl.py` browser paths (file stays
  as `--paste` CLI only, or folds into `fetch_lib.py` — implementer's choice),
  `/fetch/run` + `/fetch/login` popup routes + templates, `LIDL_PROFILE_DIR`
  handling, `Dockerfile` apt browser libs + `playwright install chromium`,
  `requirements.txt` `playwright` pin, Playwright/login tests.
- **What stays:** SQLite schema, parse/review UI, Products hub, Grocy push,
  `/fetch/paste` manual fallback (escape hatch for Lidl redesigns).

## 4. Data flow

1. User logs into `lidl-hellas.gr` once in Firefox; sets page cap if desired
   (popup number field, default 5, persisted); clicks **Sync**.
2. Background fetches `history?page=1..N` with `fetch(credentials:include)`;
   content script extracts `t=` tids (existing alphanumeric-tid regex).
   Incremental stops at first page with zero unseen tids or the cap;
   full-scan ignores both stop conditions until empty page / no next link.
3. `POST /api/ext/need {tids}` → `{new}` (client chunks ≤200 tids).
4. Per new tid: fetch `detail?t=tid` (tab-fallback if login redirect),
   extract text, `POST /api/ext/receipt {tid, text}`.
5. Popup shows `fetched N • skipped M • failed K + first error` + dashboard
   link. Nothing auto-pushes to Grocy; review flow unchanged.

### 4a. Full-sweep precautions (many pages, one logged-in session)

A full scan is the heaviest thing the extension ever does to Lidl, so:
- **Sequential only** — one history page and one detail fetch at a time,
  never parallel.
- **Throttle** — random 1.5–3 s pause between page/detail requests during a
  full scan (incremental syncs of ≤cap pages need no delay).
- **Abort on first refusal** — login redirect, overload/block page, or HTTP
  429/403 mid-sweep stops immediately. Per-receipt imports already POSTed
  stand; tids collected but not yet fetched are dropped from memory and
  re-collected by the next Sync (history pages are cheap; the expensive
  detail fetches already done are never repeated thanks to the `/need`
  diff).
- **Resume is free** — anything not marked in `fetch_state` is returned as
  new by the next `/need`, so rerunning Sync after a cooldown resumes where
  the sweep stopped. No separate resume state.
- **Progress + cancel** — popup shows `page N • tids so far • new so far`
  with a Cancel button; cancel stops after the in-flight request. Fetched
  imports stand; the rest resumes on the next Sync.
- **Backstop ceiling** — 100 pages hard cap even in full-scan (practically
  unreachable; guards against a pager-loop bug, reported as
  "stopped at backstop — rerun to continue").

## 5. Token & settings

- `settings_store.py` += `EXT_INGEST_TOKEN` (env-seeded, DB-overridable like
  `GROCY_API_KEY`); auto-generate 32 random bytes on first boot if missing.
- Dashboard Settings → Extension section: server URL hint, token show/copy +
  regenerate. Extension popup stores URL + token in `storage.local`.

## 6. Error handling

- Lidl login redirect → abort: "Log into lidl-hellas.gr in this browser first".
- Unreachable server → status + "check server URL, container running?".
  Single attempt, no retry loop (same no-hammer rule).
- 401 → "Token rejected — copy it from Settings → Extension".
- Partial failures → per-tid results; failed tids stay out of `fetch_state`
  so next Sync retries (same semantics as old rc=4). `status` exposes
  `last_sync` + receipt count; per-tid failures are shown by the extension.
- Zero tids extracted → "Lidl page changed — paste fallback still works".

## 7. Testing & rollout

- New `tests/test_ext_ingest.py`: 401 paths, `need` diff vs seeded state,
  `receipt` import + dedupe + state record, oversized tid chunking, `status`.
  Old Playwright/login tests deleted with their code; full suite green.
  (Extension JS sweep behavior — cap, throttle, abort, cancel — is verified
  in manual acceptance below, not unit-tested; no JS harness in v1.)
- Manual acceptance (maintainer, on Gamma): sideload → URL + token →
  incremental Sync (expect skipped N) → full-scan trial → bad-token rejection
  → `docker compose up --build` without browser packages,
  `/api/ext/status` 200. Remote/VPN verified later by URL change only.
- Gamma-only; `main`/`beta` untouched until merge. Paste fallback stays.

## 8. Non-goals (v1)

Chrome port, Mozilla signing, pairing flow, server-side scraping fallback,
per-receipt progress streaming, extension-hosted parsing (server parses).
