# Task 2 report — fetch_state table + idempotent single-receipt import

## Status
DONE

## Files created/modified
- Modified: `Lidl Parser/db.py` — appended `fetch_state (t_id PK, filename, fetched_at)` table to SCHEMA (after `price_history` index line), verbatim per brief.
- Modified: `Lidl Parser/fetch_lib.py` — APPENDED `pick_filename` + `import_kept_receipt` (plus `os as _os`, `pandas as _pd` imports); the 4 Task 1 helpers (`trim_receipt_text`, `receipt_body_date`, `filename_for_date`, `extract_total`) untouched.
- Created: `Lidl Parser/tests/test_fetch_import.py` — verbatim tests from brief (import+idempotent-rerun, pick-filename collision).

## Test commands + output summary
1. `cd "Lidl Parser" && python -m pytest tests/test_fetch_import.py -v` (before fix):
   - FAIL as expected: `ImportError: cannot import name 'import_kept_receipt' from 'fetch_lib'` (collection error, 0 items).
2. `cd "Lidl Parser" && python -m pytest tests/ -v` (after fix):
   - PASS: 6 passed (2 new Task 2 + 4 existing Task 1), 0 failed.

## Self-review notes
- `pick_filename` checks disk AND `receipts.filename`, increments seq via `filename_for_date` — matches spec signature exactly.
- `import_kept_receipt` idempotency: `INSERT OR IGNORE` into `receipts`, then `DELETE FROM items WHERE receipt_id` + re-insert, `INSERT OR IGNORE` into `fetch_state`; rerun yields 1 receipt row, 1 fetch_state row. Verified by test.
- Total guard: `ValueError` on empty parse and on `|parsed − stated| > 0.02`; uses `extract_total` from Task 1.
- `t_id=None` skips `fetch_state` insert (per spec); `t_id` returned in dict.
- Test overrides `db.DB_PATH` to tmp file; real `Lidl Parser/data/lidl.db` never touched. No commit (no git repo, per instructions); files left on disk.

## Concerns
None. Signatures kept verbatim for Tasks 3–4.
