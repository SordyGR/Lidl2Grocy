# Task 4 report — Playwright crawler wired to the import path

status: DONE

## Files created
- `Lidl Parser/tests/test_fetch_crawl.py` — exact test from the brief (login-redirect detection, 3 assertions).
- `Lidl Parser/.gitignore` — contains `data/.lidl-profile/`.

## Files modified
- `Lidl Parser/fetch_lidl.py` — appended crawler, kept `cmd_paste`/`main` behavior:
  - Constants `HISTORY_URL`, `DETAIL_URL`, `LOGIN_URL`, `PROFILE_DIR` (exact values from brief).
  - `is_login_redirect(url)` — exact predicate from brief.
  - `collect_tids(page, max_pages)` — collects `t=` ids via `a[href*='purchase-detail']` across pages (stops on empty page / page cap / missing next-page link).
  - `fetch_detail_text(page, tid)` — goto `DETAIL_URL`, wait for `text=ΦΟΡΟΛΟΓΙΚΗ ΑΠΟΔΕΙΞΗ ΕΝΑΡΞΗ`, return `page.inner_text("body")`.
  - `cmd_sync(latest_only, max_pages, headed)` — Playwright import is function-level (import-safe); calls `init_db()` first; persistent context at `PROFILE_DIR`; login wall prints `LOGIN NEEDED — rerun with --headed and log in once` and returns 3; otherwise skips tids in `fetch_state`, imports each via `trim_receipt_text` → `pick_filename` → `import_kept_receipt` → write txt, counts new/skipped/needs-review, `--latest-only` stops after first new import, prints exactly `new: <n>, skipped: <n>, needs-review: [...]`.
  - `main()` extended with `--latest-only` (store_true), `--max N` (dest `max_pages`, int, default 0 = all), `--headed` (store_true); `--paste`/`--tid` path unchanged; bare invocation still prints help / returns 2.
- `Lidl Parser/requirements.txt` — appended `playwright>=1.44` and `pytest>=8.0`.

## Files NOT touched (per brief)
`fetch_lib.py`, `db.py`, `tests/data_paste.txt`, earlier tests. No commit (no git repo — files left on disk).

## Test commands + output summary
1. `python -m pytest tests/test_fetch_crawl.py -v` (before fix): FAIL at collection — `ImportError: cannot import name 'is_login_redirect'` (expected).
2. `pip install playwright` → OK (playwright 1.63.0, greenlet, pyee; ~25 s). `python -m playwright install chromium` → OK (Chrome for Testing 153.0.8010.12 + FFmpeg + headless shell, ~195.6 MiB + extras, ~1–2 min).
3. `python -m pytest tests/test_fetch_crawl.py -v` (after fix): 1 passed.
4. `python -m pytest tests/ -v`: 8 passed (1 crawl + 1 cli + 2 import + 4 lib). Full suite re-run at end: 8 passed again.

## Step-5 live run (login wall — expected, no credentials, no login attempted)
- Command: `python fetch_lidl.py --latest-only`
- Stdout: `LOGIN NEEDED — rerun with --headed and log in once`
- Exit code: 3
- Before: receipts=59, fetch_state=1, receipt .txt files=59
- After: receipts=59, fetch_state=1, receipt .txt files=59 → ZERO writes.
- Step-6 rerun: identical stdout, exit 3, counts unchanged (idempotent).

## Self-review notes
- `import fetch_lidl` does NOT load playwright (`'playwright' in sys.modules` → False); `--paste` path verified working (exit 0).
- `cmd_sync` calls `init_db()` before launching the browser, so `fetch_state` always exists.
- Single summary-line print format matches the ruling exactly.
- `collect_tids` loop terminates on all paths (empty page → break; `max_pages` cap; no next-page link when uncapped).
- `context.close()` in `finally`; DB connection closed in `finally`.
- Bare `python fetch_lidl.py` (no flags) keeps old help/return-2 behavior; sync runs only when a sync flag is given.

## Concerns
- The `--paste` safety check during verification wrote `26-09-22.2.txt` + DB rows; fully rolled back (receipts/items/fetch_state rows deleted, file deleted) and zero-write counts re-verified before/after the live runs. A later full-suite re-run re-created `26-09-22.2.txt` via the pre-existing `test_fetch_cli.py` (which writes to the real DB on every run — pre-existing behavior, also source of the `CLITEST`/`26-09-22.1.txt` rows); final state receipts=60/fetch_state=1/files=60. No crawler code path wrote anything.
- Real logged-in sync still to be done by the user with `--headed`.
