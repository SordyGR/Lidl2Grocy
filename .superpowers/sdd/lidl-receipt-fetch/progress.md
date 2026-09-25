# SDD ledger — plan: docs/superpowers/plans/2026-09-23-lidl-receipt-fetch.md

## Setup
- No git repo in workspace (`git rev-parse` failed). Ruling: proceed without git — implementers SKIP all `git add/commit` steps and leave files on disk; task reviews read files directly instead of a diff package. Cost if wrong: none, files are the deliverable; user can `git init` later.
- No `model` param on subagent dispatches (harness forbids unless user requests). Cost if wrong: implementers inherit session model — slightly higher cost, same quality.
- pytest NOT installed (Python 3.13.14). Ruling: Task 1 implementer runs `pip install pytest` first. Cost if wrong: trivial.
- Workspace: `.superpowers/sdd/lidl-receipt-fetch/`. Fresh start, no prior ledger.

## Preflight conflict scan
| Pair / Task | Produces vs consumes | Finding |
|---|---|---|
| T1 → T2 | T1 produces 4 pure helpers w/ exact signatures; T2 imports them | Clean — names/signatures match plan text |
| T2 → T3 | T2 produces `pick_filename`, `import_kept_receipt`; T3 `cmd_paste` calls them | Clean |
| T3 → T4 | T3 creates `fetch_lidl.py` with `main()`/`cmd_paste`; T4 appends crawler + `is_login_redirect` | Clean, provided T3 keeps module import-safe (argparse only under `__main__`) — carried in T3 dispatch |
| T2 ↔ others | T2 appends `fetch_state` table to `db.py` SCHEMA; no other task touches `db.py` | Clean |
| T4 vs T1–T3 | T4 adds `playwright`+`pytest` to requirements; T1–T3 need pytest before that | RULED: T1 implementer pip-installs pytest first |
| Self-check T1 | tests vs code in brief | Clean — 4 tests, incl. ValueError cases |
| Self-check T2 | KEPT fixture (2 items, stacked discounts 0.10+0.06, kg line, total 3.77) vs assertions | Clean — 2.78+1.15-0.16=3.77 ✓ |
| Self-check T4 | `is_login_redirect` test vs impl (`mre/` absent + login/sign/auth present) | Clean |

## Tasks
- [x] Task 1: Trim / date / filename pure functions + tests — complete (review clean, 4 passed)
- [x] Task 2: fetch_state table + idempotent single-receipt import — complete (review clean, 6 passed)
- [x] Task 3: --paste fallback CLI — complete (review clean; 2 minors deferred: unused tmp_path in CLI test, empty fetch_state table in real DB)
- [x] Task 4: Playwright crawler — complete (spec ✅, quality Approved; 2 minors deferred: unused LOGIN_URL const, bare except in fetch_lidl.py)
- [x] Task 5: README + full-sync verification — complete (spec ✅, quality Approved; counts 58/906/empty verified)

## Rulings
- Ruling (Task 4): test_fetch_cli.py writes to the REAL db/receipts on every full-suite run (plan-mandated, cleanup was manual-once). Task 4's re-runs left 26-09-22.1/2.txt + CLITEST rows (receipts 60). My controller cleanup used LIKE '26-09-22.%' which also matched the REAL '26-09-22.txt' → deleted it + its 34 items. Restored via fixture re-import (trim→import with t_id=None): row identical (34 items, 82.73), counts back to 58 receipts / 906 items / fetch_state empty — verified. Cost if wrong: restored txt may differ in whitespace from the user's manual copy; DB values verified identical, and the website remains source of truth.
- Ruling (Task 5): to prevent recurrence, Task 5 implementer MUST add try/finally self-cleanup to test_fetch_cli.py (delete created file + its receipt/item rows + CLITEST fetch_state rows; assertion unchanged) and, after the final full-suite run, verify + report 58 receipts / 906 items / fetch_state clean. Deviates from plan-verbatim test — justified by the data-loss incident above.
- Ruling (finish): KEEP `.superpowers/sdd/lidl-receipt-fetch/` workspace instead of deleting it — the skill says delete after clean review because git is the record, but there is no git repo here, so the workspace (ledger, briefs, reports, reviews) is the ONLY record. Cost if wrong: ~50KB of scratch files remain; user can delete anytime.

## Completion
- Task 4: complete (spec ✅, quality Approved; 2 minors deferred → final review triaged LEAVE)
- Task 5: complete (spec ✅, quality Approved)
- Final review: CLEAN (no Critical/Important; minors triaged LEAVE; self-cleanup fix approved sound)

## Post-plan fixes (live login session, 2026-09-23)
- `fetch_lidl.py`: added `wait_for_login(page, timeout_s=300)` + headed-login wait branch in `cmd_sync` (fresh `--headed` run previously exited immediately instead of letting the user type credentials — spec §2 step 1 said "waits"). Headless + login wall still returns 3. +2 unit tests (10/10 green).
- `fetch_lidl.py` `collect_tids`: added `wait_for_selector("a[href*='purchase-detail']")` per page — first live run found 0 links racing JS render; probe showed 10 links on page 1.
- Live result: `--latest-only` imported t=1400070352026092258039 as `26-09-22.1.txt`, but it duplicated the manually saved `26-09-22.txt` (same 34 items / 82.73). Removed duplicate, pointed `fetch_state` at the manual file. Back to 58 receipts / 906 items.
- Known limitation: full sync (no `--latest-only`) will re-import history entries already saved manually under other filenames (fetch_state only knows t_ids it imported). Advised user to stick to `--latest-only` for now.
