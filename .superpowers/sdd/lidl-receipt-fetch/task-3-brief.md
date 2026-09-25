# Task 3 brief — --paste fallback CLI (no browser)
Source: `docs/superpowers/plans/2026-09-23-lidl-receipt-fetch.md`, Task 3 (verbatim, plus controller rulings at the end).

**Files:**
- Create: `Lidl Parser/fetch_lidl.py`
- Create: `Lidl Parser/tests/test_fetch_cli.py`
- Fixture `Lidl Parser/tests/data_paste.txt` ALREADY EXISTS (created by controller from the user's real 22-Sep Ctrl+A paste — do NOT recreate or modify it).

**Interfaces:**
- Consumes: `trim_receipt_text`, `receipt_body_date`, `pick_filename`, `import_kept_receipt` from Tasks 1–2 (in `Lidl Parser/fetch_lib.py` — do NOT modify); `init_db`, `get_db` (existing).
- Produces (used by Task 4 — keep `cmd_paste(path, t_id)` and `main(argv)` exactly as below, and keep module import-safe: argparse only under `__main__`): CLI `python fetch_lidl.py --paste <file> [--tid T]` printing `saved <filename> (<n> items, <total> EUR)`.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_fetch_cli.py
import subprocess, os, sys
def test_paste_cli(tmp_path):
    src = os.path.join(os.path.dirname(__file__), "data_paste.txt")
    assert os.path.exists(src), "fixture missing"
    r = subprocess.run([sys.executable, "fetch_lidl.py", "--paste", src, "--tid", "CLITEST"],
                       cwd=os.path.join(os.path.dirname(__file__), ".."),
                       capture_output=True, text=True, timeout=120)
    assert "saved 26-09-22" in r.stdout, r.stdout + r.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "Lidl Parser" && python -m pytest tests/test_fetch_cli.py -v`
Expected: FAIL with `can't open file 'fetch_lidl.py'`

- [ ] **Step 3: Write minimal CLI**

```python
"""Fetch Lidl Hellas digital receipts. Fallback: --paste <file>."""
import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from db import init_db, get_db
from fetch_lib import (trim_receipt_text, receipt_body_date,
                       pick_filename, import_kept_receipt)

RECEIPT_DIR = os.path.join(BASE_DIR, "receipts")


def cmd_paste(path: str, t_id) -> dict:
    init_db()
    with open(path, "r", encoding="utf-8") as f:
        full = f.read()
    kept = trim_receipt_text(full)
    yy, mm, dd = receipt_body_date(kept)
    conn = get_db()
    try:
        name = pick_filename(RECEIPT_DIR, conn, yy, mm, dd)
        res = import_kept_receipt(conn, kept, name, t_id)
    finally:
        conn.close()
    os.makedirs(RECEIPT_DIR, exist_ok=True)
    with open(os.path.join(RECEIPT_DIR, name), "w", encoding="utf-8") as f:
        f.write(kept)
    print(f"saved {name} ({res['items']} items, {res['total']:.2f} EUR)")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--paste", help="path to full Ctrl+A page text file")
    ap.add_argument("--tid", default=None)
    args = ap.parse_args(argv)
    if args.paste:
        cmd_paste(args.paste, args.tid)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "Lidl Parser" && python -m pytest tests/test_fetch_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit** — SKIPPED by controller ruling (no git repo). Leave files on disk.

## Controller rulings (binding, override the plan where they conflict)
1. This test runs against the REAL `Lidl Parser/data/lidl.db` and `receipts/` by design (it is the fallback-path integration check). Afterwards you MUST clean up so the user's data is byte-identical to before: delete ONLY the newly written receipt file named in the `saved ...` output (never the pre-existing `26-09-22.txt`), then in sqlite3 delete that receipt's items + receipt rows and the `fetch_state` row with `t_id='CLITEST'`. Verify with: `SELECT COUNT(*) FROM receipts` equals the pre-test count (58) and `SELECT COUNT(*) FROM fetch_state WHERE t_id='CLITEST'` is 0 and no new `26-09-22.*.txt` file remains. Record the before/after counts in your report. Cost if wrong: test residue pollutes the user's real grocery history.
2. If the fixture's parsed total disagrees with its ΣΥΝΟΛΟ by more than 0.02 (ValueError from `import_kept_receipt`), do NOT edit the fixture — report BLOCKED with the two numbers.
