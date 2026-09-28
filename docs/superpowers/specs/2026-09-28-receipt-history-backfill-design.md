# Receipt History Backfill + Resume — Design

Date: 2026-09-28 | Approach A (approved) | Status: spec, awaiting review

## 1. Intent

- Said: passwordless noVNC login works (cookie clear + Ctrl+F5); page-1 receipts fetched. Goal: new accounts backfill the whole receipt history, then later syncs continue from where the last sync stopped.
- Assumed (confirmed): history spans multiple pages; `collect_tids` strands on page 1; `fetch_state` skips seen tids but has no cursor/progress.
- Decisions: cursor + progress resume; scale 50–500 receipts; one long background sync.
- Success: fresh account → every receipt imported across all pages; interrupted run → next Sync resumes from cursor; steady state → incremental sync fetches only new receipts and early-stops.

## 2. Traversal + cursor architecture

- Fix `collect_tids` (`Lidl Parser/fetch_lidl.py:215`): walk `HISTORY_URL ?page=N` with N incrementing until a page yields zero `purchase-detail` links. Drop dependence on the `a[href*='page=']` next-link selector. Order newest-first (page 1 is newest).
- Reuse the existing `/fetch/run` background thread (`Lidl Parser/app.py:33`). No new jobs or endpoints.
- Persist a `sync_state` cursor committed after every receipt import alongside the existing `fetch_state(t_id)` insert, so container kill / browser close resumes instead of rescanning.
- Incremental syncs run the same loop but early-stop after K consecutive already-seen tids (default K=10).

## 3. Components (schema + status)

- `db.py`: new table via existing `init_db()` pattern. Single-row cursor, e.g. `sync_state(key TEXT PRIMARY KEY, value TEXT)` holding `phase` (backfill|incremental), `last_page`, `last_tid`, `new_count`, `skipped_count`, `updated_at`. No separate migration script.
- `fetch_lidl.cmd_sync`: per-receipt commit of receipt + `fetch_state` + cursor (never one big transaction).
- `/api/fetch_status` (`Lidl Parser/app.py:86`): extend payload to `{running, phase, last_page, new_count, skipped_count, error, last_sync}`. Dashboard JS change is display-only progress text (e.g. "Backfilling… page 4, 120 new / 30 skipped").

## 4. Data flow

- Backfill (empty `fetch_state`): pages 1→N until empty page; fetch each unseen tid detail; import + commit receipt and cursor per receipt.
- Incremental (cursor/state exists): same walk; stop after K consecutive seen tids; unseen tids found anywhere in the walk are still fetched (late-appearing receipts covered).
- `max_pages` (`--max`) stays as manual cap for testing/chunking. `latest_only` retired from the dashboard path (currently stops after one import); kept as CLI shorthand for `max_pages=1` only.

## 5. Error handling

- Per-receipt failure: keep `needs-review` behavior (collect tid, continue, exit 4 with message). One bad receipt never aborts the backfill.
- Login redirect mid-run: existing code-3 path; headed manual login prompted at start only.
- Automation-overload (`overload` note): stop loop gracefully, cursor already committed, set `LOGIN_COOLDOWN_S`, status reports "paused at page P — retry after cooldown".
- Browser-close / container-kill: at most the in-flight receipt is lost; next Sync resumes from committed cursor.
- Empty history: success (code 0), unchanged.

## 6. Testing

- Unit, no browser: page-walking (multi-page tids → walks all; empty page → stops; missing next-link → still continues); cursor round-trip; early-stop after K consecutive seen.
- Integration (fake-Playwright pattern from `tests/test_beta_fixes.py`): 3-fake-page backfill imports all; kill-and-resume (pre-seeded `fetch_state` + cursor → only remainder fetched); incremental all-seen → early-stop with zero detail fetches.
- Regression: full `pytest` suite green; entrypoint/LF tests untouched.

## 7. Out of scope (YAGNI)

- No scheduler/daemon; user presses Sync. No separate backfill endpoint. No timestamp-based incremental. No retry queue beyond `needs-review`. No dashboard progress bar widget — text line only.
