# Task 4 review — Playwright crawler wired to the import path

Reviewer role only. No files edited (except this review), no tests re-run, no live site hit.
Code read directly (no git repo).

## Sources read
- `.superpowers/sdd/lidl-receipt-fetch/task-4-brief.md` (spec + 4 controller rulings)
- `.superpowers/sdd/lidl-receipt-fetch/task-4-report.md` (implementer report)
- `Lidl Parser/fetch_lidl.py` (150 lines, full read)
- `Lidl Parser/tests/test_fetch_crawl.py` (8 lines, full read)
- `Lidl Parser/requirements.txt`, `Lidl Parser/.gitignore`
- Negative checks: greps for crawl/playwright symbols in `fetch_lib.py`, `db.py`,
  `tests/test_fetch_cli.py`, `tests/test_fetch_import.py`, `tests/test_fetch_lib.py`
  (all no matches); directory listings of `Lidl Parser/` and `Lidl Parser/tests/`.

## Spec compliance: ✅
Every checkable item matches the brief verbatim:

- `is_login_redirect` predicate exact: `fetch_lidl.py:23-24`
  `"mre/" not in url and ("login" in url or "sign" in url or "auth" in url)` —
  matches brief Step 4.
- Constants exact: `HISTORY_URL`, `DETAIL_URL`, `LOGIN_URL`, `PROFILE_DIR`
  (`fetch_lidl.py:15-20`) match brief values; `PROFILE_DIR` = `BASE_DIR/data/.lidl-profile`.
- `collect_tids(page, max_pages)` uses `a[href*='purchase-detail']`, extracts `[?&]t=(\d+)`
  (`fetch_lidl.py:37-39`); terminates on all paths: `max_pages` cap (`:32`),
  empty page (`:44-45`), cap re-check (`:46-47`), no next-page link when uncapped (`:51-53`).
- `fetch_detail_text` gotos `DETAIL_URL`, waits for
  `text=ΦΟΡΟΛΟΓΙΚΗ ΑΠΟΔΕΙΞΗ ΕΝΑΡΞΗ` (timeout 15000), returns `page.inner_text("body")`
  (`fetch_lidl.py:58-61`).
- `cmd_sync` calls `init_db()` first (`:66`); persistent context at `PROFILE_DIR` (`:70-71`);
  login wall prints `LOGIN NEEDED — rerun with --headed and log in once` (`:76`) and
  returns 3 (`:77`) before any fetch_state/import work; otherwise skips tids in
  `fetch_state` (`:81-86`), per-tid `trim_receipt_text → receipt_body_date →
  pick_filename → import_kept_receipt → write txt` (`:88-96`), counts
  new/skipped/needs-review, `--latest-only` breaks after first successful new import
  (`:99-100`), prints exactly one summary line
  `print(f"new: {new}, skipped: {skipped}, needs-review: {needs}")` (`:107`).
- `main()` adds `--latest-only` (store_true), `--max` (dest `max_pages`, int, default 0),
  `--headed` (store_true) (`:134-137`); `--paste`/`--tid` path unchanged (`:139-141`);
  bare run prints help / returns 2 (`:145-146`).
- `requirements.txt` appends `playwright>=1.44` and `pytest>=8.0`; `.gitignore`
  contains `data/.lidl-profile/`; `tests/test_fetch_crawl.py` is the exact 3-assertion
  test from the brief.
- Binding lens: no credentials/args/logging anywhere (grep for
  `password|credential|secret|token` in `fetch_lidl.py` hits only the two
  `playwright` lines); `import fetch_lidl` has no top-level playwright import —
  the only playwright import is function-level inside `cmd_sync` (`:65`);
  `context.close()` in `finally` (`:105-106`), `conn.close()` in `finally` (`:103-104`).
- Untouched files: no crawl symbols in `fetch_lib.py`, `db.py`, or the three earlier
  test files; no contrary evidence of modification to `tests/data_paste.txt`
  (static review cannot diff without a baseline, but nothing references it).
- Residue/DB-count side story in the report (59→60, `CLITEST` rows) is per-instruction
  out of scope; Task 5 carries the self-cleanup fix. Judged code only.

No missing or deviating spec item. Verdict: ✅.

## Task quality: Approved (2 Minor findings)

- Minor — `Lidl Parser/fetch_lidl.py:19`: `LOGIN_URL` constant is defined but never
  referenced (login wall checks `page.url` via `is_login_redirect` instead of
  navigating to `LOGIN_URL`). Dead code only; harmless, could be used or removed.
- Minor — `Lidl Parser/fetch_lidl.py:101-102`: `except Exception: needs.append(tid)`
  swallows the error without logging the tid's reason. Matches the brief's
  needs-review counting, but a one-line stderr/log would make real-sync triage
  much easier. No behavior change requested.

No Critical or Important findings. No security, import-safety, termination, or
resource-leak defects found.
