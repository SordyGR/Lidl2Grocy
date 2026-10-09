# Task 3 report — --paste fallback CLI (no browser)

**Status:** DONE

## Files created (in `Lidl Parser/`, nothing else touched)
- `fetch_lidl.py` (1380 bytes) — verbatim CLI from the brief: `cmd_paste(path, t_id)`, `main(argv)`, argparse only under `__main__`.
- `tests/test_fetch_cli.py` (472 bytes) — verbatim failing-test-first from the brief.
- NOT modified: `fetch_lib.py`, `db.py`, `tests/data_paste.txt` (only read). No commit (no git repo, per instructions). All `_tmp_*.py` scratch scripts deleted.

## Test commands + output summary
1. `cd "Lidl Parser" && python -m pytest tests/test_fetch_cli.py -v` (before CLI existed) → **1 FAILED** as expected: `can't open file 'fetch_lidl.py'` (Errno 2).
2. Same command after writing `fetch_lidl.py` → **1 passed** (0.80s).
3. Final regression `python -m pytest tests/ -v` → **7 passed** (test_fetch_cli 1, test_fetch_import 2, test_fetch_lib 4), 1.35s.
4. Import-safety check (`import fetch_lidl`; `main([])`) → `cmd_paste`/`main` present, `main([])` prints help and returns 2, no side effects at import.

## Ruling 1 — DB residue cleanup (before/after)
- Before any test run: `receipts` = **58**; `fetch_state` table did not exist yet; `receipts/26-09-22*` = only `26-09-22.txt`.
- Each CLI-test run wrote `receipts/26-09-22.1.txt` (34 items, `t_id='CLITEST'`, receipts 58→59). Cleaned up **twice** (once after the Step-4 pass, once after the final full-suite run), identically each time: deleted only `26-09-22.1.txt`, deleted its 34 `items` rows + 1 `receipts` row + 1 `fetch_state` row (`t_id='CLITEST'`).
- After (verified): `SELECT COUNT(*) FROM receipts` = **58**; `SELECT COUNT(*) FROM fetch_state WHERE t_id='CLITEST'` = **0**; `26-09-22*` files = only the original `26-09-22.txt` (intact). `saved` output named `26-09-22.1.txt`, confirming `pick_filename` collision-avoidance against the pre-existing `26-09-22.txt`.

## Ruling 2 — fixture total check (no fixture edit)
- Label ΣΥΝΟΛΟ = 12.34, parsed Σ(Qty×Unit Price − Discount) = 12.35, diff = **0.01 ≤ 0.02** → no ValueError, no BLOCKED.

## Self-review notes
- `cmd_paste(path, t_id)` and `main(argv)` signatures match the brief exactly (Task 4 dependency preserved).
- Module is import-safe: argparse/`raise SystemExit` only under `if __name__ == "__main__"`.
- `sys.path.insert(0, BASE_DIR)` present so `db`/`fetch_lib` imports work from the subprocess cwd used by the test.
- Receipt file is written only after a successful DB import; `conn.close()` in `finally`.
- Existing Task 1–2 tests use tmp-path DBs (`_mem_conn`), so the full-suite run caused no other real-DB writes.

## Concerns
- Minor: `fetch_state` table did not exist before this task; `init_db()` (called by `cmd_paste`) created it. It is now present but **empty (0 rows)** — schema only, no data residue. Harmless, but recorded since "byte-identical" strictly covers rows/files, both of which match.
- None other. The `sqlite3.Row` → `dict` TypeError seen in a scratch inspection script was a bug in the throwaway script, not in deliverables; scratch scripts are deleted.
