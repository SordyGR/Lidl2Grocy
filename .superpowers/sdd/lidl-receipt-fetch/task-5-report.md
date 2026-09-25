# Task 5 report — README + full-sync verification (+ test self-cleanup fix)

Status: DONE

## Files modified
- `Lidl Parser/README.md` — appended `## Fetch (auto-download from Lidl Hellas)` section (pip/playwright install, `--latest-only`, `--max 5`, `--paste` fallback, login/profile note). Pre-existing `## Verified migration` section preserved (an intermediate edit had clobbered it; restored and re-verified by full file read).
- `Lidl Parser/tests/test_fetch_cli.py` — added self-cleanup per controller ruling 1. No new files created. Commit step skipped (no git repo, per brief); files left on disk.

## Test fix detail (`test_fetch_cli.py`)
- `saved 26-09-22` assertion kept byte-identical: `assert "saved 26-09-22" in r.stdout, r.stdout + r.stderr`.
- Created filename parsed from stdout via `re.search(r"saved (\S+)", r.stdout)`; both file deletion and per-name DB-row deletion gated on `re.fullmatch(r"26-09-22\.\d+\.txt", name)` — the real `26-09-22.txt` can never match, so it can never be deleted.
- `finally` block always runs: deletes the receipt file if present, deletes `items` + `receipts` rows for the parsed name, deletes `fetch_state` rows with `t_id='CLITEST'`. Parse/validate lives in `try` so a subprocess failure still cleans up via the `CLITEST` key and the original error propagates unmasked.
- Post-cleanup assertion: `fetch_state` contains no `CLITEST` row.

## Commands + output summaries
1. `cd "Lidl Parser" && python -m pytest tests/ -v` → **8 passed in 1.41s** (test_paste_cli, test_login_redirect_detection, test_import_and_idempotent_rerun, test_pick_filename_avoids_collision, 4x test_fetch_lib). Exit 0.
2. `cd "Lidl Parser" && python fetch_lidl.py --max 2` → stdout: `LOGIN NEEDED — rerun with --headed and log in once`; **exit code 3**; zero writes (no login attempted, per ruling 2).
   - Before: receipts 58, items 906, fetch_state 0, receipts/ files 58.
   - After: receipts 58, items 906, fetch_state 0, receipts/ files 58.
3. Final state check (ruling 3): `SELECT COUNT(*) FROM receipts` = **58**, `SELECT COUNT(*) FROM items` = **906**, `fetch_state` **empty**, `receipts/` contains exactly one `26-09-22*` file (**`26-09-22.txt`**), 58 files total. Verified both immediately after the test suite (proves the new self-cleanup works) and again after the `--max 2` run. No cleanup deviations needed.

## Self-review notes
- README Fetch section content matches the brief verbatim (wrapped in a code fence; brief's own markdown fence omitted the opening fence, so one was added for correct rendering).
- Test fix uses only stdlib (`re`, `sqlite3`, `subprocess`, `os`, `sys`); DB path derived from test location, no hard-coded absolute paths; FK pragma enabled before deletes.
- All DB-count evidence comes from executed `sqlite3` queries, not copied text.

## Concerns
- None. Real user data untouched throughout; all counts at exactly 58 / 906 / empty with the single genuine `26-09-22.txt` intact.
