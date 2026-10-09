# Task 2 brief — fetch_state table + idempotent single-receipt import
Source: `docs/superpowers/plans/2026-09-23-lidl-receipt-fetch.md`, Task 2 (verbatim).

**Files:**
- Modify: `Lidl Parser/db.py:9-62` (append `fetch_state` table to SCHEMA)
- Modify: `Lidl Parser/fetch_lib.py` (append `pick_filename` + `import_kept_receipt`)
- Create: `Lidl Parser/tests/test_fetch_import.py`

**Interfaces:**
- Consumes: `trim_receipt_text`, `receipt_body_date`, `filename_for_date`, `extract_total` from Task 1 (already in `Lidl Parser/fetch_lib.py` — keep them untouched); `get_db`, `parse_receipt`, `parse_receipt_date`, `smart_match`, `build_memory` (existing).
- Produces (used by Tasks 3–4, exact signatures — keep verbatim):
  - `pick_filename(receipt_dir: str, conn, yy: str, mm: str, dd: str) -> str` — first non-colliding name checking disk AND `receipts.filename`.
  - `import_kept_receipt(conn, kept_text: str, filename: str, t_id: str | None) -> dict` — returns `{"filename", "items", "total", "t_id"}`; raises `ValueError` on empty parse or total mismatch > 0.02.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_fetch_import.py
import os
import sqlite3
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, get_db
import db as dbmod
from fetch_lib import import_kept_receipt, pick_filename

KEPT = (
    "22 \u03a3\u03b5\u03c0 2026\nlogo\n"
    "TORTI.CHIPS SALT200G          2,78  13.00%\n"
    "    2 x          1,39      \n"
    "     \u03ad\u03ba\u03c0\u03c4\u03c9\u03c3\u03b7 Lidl Plus             -0,10\n"
    "     \u03ad\u03ba\u03c0\u03c4\u03c9\u03c3\u03b7                       -0,06\n"
    "LIMES                         1,15  13.00%\n"
    "  0,304 kg x 3,79   EUR/kg\n"
    "\u03a3\u03a5\u039d\u039f\u039b\u039f                               3,77\n"
    "0000   000000/00/00       01.01.26   12:00\n"
    "\u0395\u03a0\u0399\u03a3\u03a4\u03a1\u039f\u03a6\u0395\u03a3 \u0394\u0395\u039a\u03a4\u0395\u03a3\n"
)

def _mem_conn(tmp_path):
    dbmod.DB_PATH = str(tmp_path / "t.db")
    init_db()
    return get_db()

def test_import_and_idempotent_rerun(tmp_path):
    conn = _mem_conn(tmp_path)
    r1 = import_kept_receipt(conn, KEPT, "26-09-22.txt", "T1")
    assert r1["items"] == 2 and abs(r1["total"] - 3.77) < 0.005
    # stacked discounts (0.10 + 0.06) accumulate on the first item
    disc = conn.execute("SELECT discount FROM items WHERE raw LIKE 'TORTI%'").fetchone()[0]
    assert abs(disc - 0.16) < 0.005
    r2 = import_kept_receipt(conn, KEPT, "26-09-22.txt", "T1")
    assert r2["items"] == 2
    n = conn.execute("SELECT COUNT(*) FROM receipts WHERE filename='26-09-22.txt'").fetchone()[0]
    assert n == 1
    assert conn.execute("SELECT COUNT(*) FROM fetch_state WHERE t_id='T1'").fetchone()[0] == 1
    conn.close()

def test_pick_filename_avoids_collision(tmp_path):
    conn = _mem_conn(tmp_path)
    d = str(tmp_path / "rc")
    os.makedirs(d)
    open(os.path.join(d, "26-09-22.txt"), "w").close()
    assert pick_filename(d, conn, "26", "09", "22") == "26-09-22.1.txt"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "Lidl Parser" && python -m pytest tests/test_fetch_import.py -v`
Expected: FAIL with `no such table: fetch_state` or `cannot import name 'import_kept_receipt'`

- [ ] **Step 3: Schema change in db.py**

Append inside `SCHEMA` (after the `price_history` index line):

```sql
CREATE TABLE IF NOT EXISTS fetch_state (
    t_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    fetched_at TEXT DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: Append import functions to fetch_lib.py**

```python
import os as _os
import pandas as _pd


def pick_filename(receipt_dir: str, conn, yy: str, mm: str, dd: str) -> str:
    seq = 0
    while True:
        name = filename_for_date(yy, mm, dd, seq)
        exists_disk = _os.path.exists(_os.path.join(receipt_dir, name))
        exists_db = conn.execute(
            "SELECT 1 FROM receipts WHERE filename=?", (name,)).fetchone()
        if not exists_disk and not exists_db:
            return name
        seq += 1


def import_kept_receipt(conn, kept_text: str, filename: str, t_id) -> dict:
    from receipt_parser import (parse_receipt, parse_receipt_date,
                                smart_match, build_memory)
    df = parse_receipt(kept_text, filename)
    if df.empty:
        raise ValueError(f"{filename}: no items parsed")
    parsed_total = round(float((df["Qty"] * df["Unit Price"] - df["Discount"]).sum()), 2)
    if abs(parsed_total - extract_total(kept_text)) > 0.02:
        raise ValueError(f"{filename}: total mismatch parsed={parsed_total}")
    iso_date, seq = parse_receipt_date(filename)
    cur = conn.cursor()
    cur.execute(
        """INSERT OR IGNORE INTO receipts (filename, receipt_date, receipt_seq, item_count, total)
           VALUES (?, ?, ?, ?, ?)""",
        (filename, iso_date, seq, len(df), parsed_total))
    conn.commit()
    rid = cur.execute("SELECT id FROM receipts WHERE filename=?", (filename,)).fetchone()["id"]
    cur.execute("DELETE FROM items WHERE receipt_id=?", (rid,))
    mem = build_memory(_pd.read_sql("SELECT raw, clean, category FROM products", conn))
    for _, item in df.iterrows():
        clean, cat, mtype, score = smart_match(item["Product"], mem)
        tot = float(item["Qty"] * item["Unit Price"])
        cur.execute(
            """INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price,
                                  vat, discount, total, final, match_type, match_score)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, item["Product"], clean, cat, float(item["Qty"]),
             float(item["Unit Price"]), float(item["VAT"]), float(item["Discount"]),
             round(tot, 2), round(tot - float(item["Discount"]), 2), mtype, float(score)))
    if t_id is not None:
        cur.execute("INSERT OR IGNORE INTO fetch_state (t_id, filename) VALUES (?, ?)", (t_id, filename))
    conn.commit()
    return {"filename": filename, "items": len(df), "total": parsed_total, "t_id": t_id}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "Lidl Parser" && python -m pytest tests/ -v`
Expected: PASS (all Task 1 + Task 2 tests)

- [ ] **Step 6: Commit** — SKIPPED by controller ruling (no git repo). Leave files on disk.
