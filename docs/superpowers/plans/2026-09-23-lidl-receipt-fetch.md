# Lidl Receipt Auto-Fetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `fetch_lidl.py` that logs in via saved browser session, downloads missing Lidl Hellas digital receipts, and imports them into `lidl.db` using the existing parser.

**Architecture:** New `Lidl Parser/fetch_lib.py` holds pure trim/name/import functions (no browser); new `Lidl Parser/fetch_lidl.py` is the CLI that wires Playwright crawling to those functions. Reuses `parse_receipt`, `smart_match`, `build_memory` exactly as `/upload` does.

**Tech Stack:** Python, Playwright (Chromium), SQLite, pandas, rapidfuzz, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-lidl-receipt-fetch-design.md`

## Global Constraints

- No password is ever stored, logged, or passed as a CLI arg — login happens by the user in the real Lidl page; only session cookies persist in git-ignored `Lidl Parser/data/.lidl-profile/`.
- Filenames follow the existing scheme exactly: body date `22.09.26` → `26-09-22.txt`, same-day extras → `26-09-22.1.txt`, `.2.txt`, `.3.txt`.
- Kept text block starts at the Greek date line (e.g. `22 Σεπ 2026`) and ends inclusive at the `ΕΠΙΣΤΡΟΦΕΣ...ΔΕΚΤΕΣ` line.
- Re-runs are idempotent: `INSERT OR IGNORE` on `receipts.filename`, items delete+reinsert per receipt, `fetch_state(t_id)` pre-check.
- Parsed total vs `ΣΥΝΟΛΟ` line mismatch > 0.02 € flags the receipt for review instead of silently importing a wrong number.

## Review Focus

- Lidl Plus stacked discounts (`έκπτωση` + `έκπτωση Lidl Plus` on consecutive lines) must accumulate on one item, not split or drop — expect both summed into `Discount`.
- kg lines with extra spaces (`0,304 kg x 3,79   EUR/kg`) must set qty=0.304, unit=3.79 — expect exact floats.
- Two receipts with the same body date must not overwrite — expect `.1` suffix on the second.
- Expired login must stop with a LOGIN NEEDED message and import nothing — expect zero DB writes.
- Detail page without a recognizable date-line or end-marker must save txt but skip DB — expect file on disk, no receipt row.

---

### Task 1: Trim / date / filename pure functions + tests

**Files:**
- Create: `Lidl Parser/fetch_lib.py`
- Create: `Lidl Parser/tests/test_fetch_lib.py`

**Interfaces:**
- Consumes: nothing (pure stdlib `re`).
- Produces (used by Tasks 2–4, exact signatures):
  - `trim_receipt_text(full_text: str) -> str` — raises `ValueError` if start/end markers missing.
  - `receipt_body_date(kept_text: str) -> tuple[str, str, str]` — returns `(yy, mm, dd)` from `22.09.26` body stamp; raises `ValueError` if absent.
  - `filename_for_date(yy: str, mm: str, dd: str, seq: int) -> str` — `seq=0` → `26-09-22.txt`, `seq>=1` → `26-09-22.1.txt`.
  - `extract_total(kept_text: str) -> float` — first `ΣΥΝΟΛΟ <amount>`; raises `ValueError` if absent.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_fetch_lib.py
from fetch_lib import trim_receipt_text, receipt_body_date, filename_for_date, extract_total

FULL = (
    "Lidl Hellas - \u0393\u03b9\u03b1\u03c4\u03af \u0391\u03be\u03af\u03b6\u03b5\u03b9.\n"
    "22 \u03a3\u03b5\u03c0 2026\n\nlogo\n"
    "\u03a3\u0395\u0399\u03a1\u0391: 070305   \u0391\u03a1\u0399\u0398\u039c\u039f\u03a3: 476903\n"
    "0703   058039/05/11       22.09.26   20:56\n"
    "\u03a3\u03a5\u039d\u039f\u039b\u039f                               82,72\n"
    "\u0395\u03a0\u0399\u03a3\u03a4\u03a1\u039f\u03a6\u0395\u03a3 \u03a3\u0395 \u0395\u03a3\u03a9\u03a1\u039f\u03a5\u03a7\u0391 \u0394\u0395 \u0393\u0399\u039d\u039f\u039d\u03a4\u0391\u0399 \u0394\u0395\u039a\u03a4\u0395\u03a3\n"
    "\u0395\u03be\u03b1\u03c1\u03b3\u03c5\u03c1\u03ce\u03b8\u03b7\u03ba\u03b1\u03bd \u03ba\u03bf\u03c5\u03c0\u03cc\u03bd\u03b9\u03b1\n"
)

def test_trim_keeps_date_through_epistrofes():
    kept = trim_receipt_text(FULL)
    assert kept.startswith("22 \u03a3\u03b5\u03c0 2026")
    assert kept.rstrip().endswith("\u0394\u0395\u039a\u03a4\u0395\u03a3")
    assert "\u03ba\u03bf\u03c5\u03c0\u03cc\u03bd\u03b9\u03b1" not in kept
    assert kept.count("Lidl Hellas") == 0

def test_body_date_and_filename():
    kept = trim_receipt_text(FULL)
    assert receipt_body_date(kept) == ("26", "09", "22")
    assert filename_for_date("26", "09", "22", 0) == "26-09-22.txt"
    assert filename_for_date("26", "09", "22", 1) == "26-09-22.1.txt"

def test_extract_total():
    assert extract_total(trim_receipt_text(FULL)) == 82.72

def test_trim_missing_markers_raises():
    import pytest
    with pytest.raises(ValueError):
        trim_receipt_text("just some page header\nno receipt here\n")
    with pytest.raises(ValueError):
        trim_receipt_text("22 \u03a3\u03b5\u03c0 2026\nlogo\nno end marker\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "Lidl Parser" && python -m pytest tests/test_fetch_lib.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fetch_lib'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Pure helpers for Lidl digital-receipt fetching (no browser, no DB)."""
import re

_START = re.compile(r"^\s*\d{1,2}\s+[^\W\d_]+\s+\d{4}\s*$")
_BODY_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})\s+\d{2}:\d{2}")
_TOTAL = re.compile(r"\u03a3\u03a5\u039d\u039f\u039b\u039f\s+([\d,]+)")


def trim_receipt_text(full_text: str) -> str:
    lines = full_text.splitlines()
    start = next((i for i, ln in enumerate(lines) if _START.match(ln)), None)
    if start is None:
        raise ValueError("start date line not found")
    end = next((i for i, ln in enumerate(lines[start:], start)
                if ln.strip().startswith("\u0395\u03a0\u0399\u03a3\u03a4\u03a1\u039f\u03a6\u0395\u03a3")), None)
    if end is None:
        raise ValueError("end marker not found")
    return "\n".join(lines[start:end + 1]) + "\n"


def receipt_body_date(kept_text: str) -> tuple:
    m = _BODY_DATE.search(kept_text)
    if not m:
        raise ValueError("body date not found")
    dd, mm, yy = m.groups()
    return (yy, mm, dd)


def filename_for_date(yy: str, mm: str, dd: str, seq: int) -> str:
    base = f"{yy}-{mm}-{dd}"
    return f"{base}.txt" if seq == 0 else f"{base}.{seq}.txt"


def extract_total(kept_text: str) -> float:
    m = _TOTAL.search(kept_text)
    if not m:
        raise ValueError("total not found")
    return float(m.group(1).replace(",", "."))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd "Lidl Parser" && python -m pytest tests/test_fetch_lib.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/fetch_lib.py" "Lidl Parser/tests/test_fetch_lib.py"
git commit -m "feat: add receipt trim/date/filename helpers"
```

---

### Task 2: fetch_state table + idempotent single-receipt import

**Files:**
- Modify: `Lidl Parser/db.py:9-62` (append `fetch_state` table to SCHEMA)
- Modify: `Lidl Parser/fetch_lib.py` (append `pick_filename` + `import_kept_receipt`)
- Create: `Lidl Parser/tests/test_fetch_import.py`

**Interfaces:**
- Consumes: `trim_receipt_text`, `receipt_body_date`, `filename_for_date`, `extract_total` from Task 1; `get_db`, `parse_receipt`, `parse_receipt_date`, `smart_match`, `build_memory` (existing).
- Produces (used by Tasks 3–4, exact signatures):
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
    "0703   058039/05/11       22.09.26   20:56\n"
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

- [ ] **Step 6: Commit**

```bash
git add "Lidl Parser/db.py" "Lidl Parser/fetch_lib.py" "Lidl Parser/tests/test_fetch_import.py"
git commit -m "feat: add fetch_state and idempotent receipt import"
```

---

### Task 3: --paste fallback CLI (no browser)

**Files:**
- Create: `Lidl Parser/fetch_lidl.py`
- Create: `Lidl Parser/tests/test_fetch_cli.py`

**Interfaces:**
- Consumes: `trim_receipt_text`, `receipt_body_date`, `pick_filename`, `import_kept_receipt` from Tasks 1–2; `init_db`, `get_db` (existing).
- Produces (used by Task 4): CLI `python fetch_lidl.py --paste <file> [--tid T]` printing `saved <filename> (<n> items, <total> EUR)`.

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

Fixture `Lidl Parser/tests/data_paste.txt` = full Ctrl+A page text for the 22-Sep receipt (header + kept block + coupons footer), created from the user's paste before running.

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
Expected: PASS, and `receipts/26-09-22*.txt` written (delete the test-written file + DB rows for `CLITEST` afterwards to keep the DB clean)

- [ ] **Step 5: Commit**

```bash
git add "Lidl Parser/fetch_lidl.py" "Lidl Parser/tests/test_fetch_cli.py" "Lidl Parser/tests/data_paste.txt"
git commit -m "feat: add --paste fallback import CLI"
```

---

### Task 4: Playwright crawler wired to the import path

**Files:**
- Modify: `Lidl Parser/fetch_lidl.py` (append crawl commands)
- Modify: `Lidl Parser/requirements.txt` (append `playwright>=1.44` and `pytest>=8.0`)
- Create: `Lidl Parser/.gitignore` (if missing, add `data/.lidl-profile/`)

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: `python fetch_lidl.py [--latest-only] [--max N] [--headed]` syncing missing receipts then printing `new / skipped / needs-review`.
- Produces (pure helper, tested): `is_login_redirect(url: str) -> bool` — True when the browser landed on the login flow instead of `mre/`.

- [ ] **Step 1: Write the failing test**

```python
# Lidl Parser/tests/test_fetch_crawl.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fetch_lidl import is_login_redirect

def test_login_redirect_detection():
    assert is_login_redirect("https://www.lidl-hellas.gr/user-api/login?step=login&redirect=/")
    assert not is_login_redirect("https://www.lidl-hellas.gr/mre/purchase-history?client_id=GreeceRetailClient&country_code=gr&language=el-GR&page=1")
    assert not is_login_redirect("https://www.lidl-hellas.gr/mre/purchase-detail?client_id=GreeceRetailClient&t=14000")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "Lidl Parser" && python -m pytest tests/test_fetch_crawl.py -v`
Expected: FAIL with `cannot import name 'is_login_redirect'`

- [ ] **Step 3: Install browser dep (one-time)**

Run: `pip install playwright && playwright install chromium`
Expected: install succeeds

- [ ] **Step 2: Append crawler to fetch_lidl.py**

```python
HISTORY_URL = ("https://www.lidl-hellas.gr/mre/purchase-history"
               "?client_id=GreeceRetailClient&country_code=gr&language=el-GR&page={page}")
DETAIL_URL = ("https://www.lidl-hellas.gr/mre/purchase-detail"
              "?client_id=GreeceRetailClient&country_code=gr&language=el-GR&t={tid}")
LOGIN_URL = "https://www.lidl-hellas.gr/user-api/login?step=login&redirect=/"
PROFILE_DIR = os.path.join(BASE_DIR, "data", ".lidl-profile")


def is_login_redirect(url: str) -> bool:
    return "mre/" not in url and ("login" in url or "sign" in url or "auth" in url)
```

Add functions `collect_tids(page, max_pages)`, `fetch_detail_text(page, tid)`, `cmd_sync(latest_only, max_pages, headed)`:
  - launch persistent context at PROFILE_DIR; goto HISTORY_URL page 1; if `is_login_redirect(page.url)`, print `LOGIN NEEDED — rerun with --headed and log in once` and return 3 (importing nothing);
  - collect `t=` ids via `a[href*='purchase-detail']` across pages;
  - skip tids already in `fetch_state`;
  - per tid: goto DETAIL_URL, wait for `text=ΦΟΡΟΛΟΓΙΚΗ ΑΠΟΔΕΙΞΗ ΕΝΑΡΞΗ`, `page.inner_text("body")` → `trim_receipt_text` → `pick_filename` → write txt → `import_kept_receipt` → count new/skipped/needs-review;
  - `--latest-only` stops after the first successful new import.

- [ ] **Step 4: Run crawler unit test plus live verify latest-only**

Run: `cd "Lidl Parser" && python -m pytest tests/test_fetch_crawl.py -v`
Expected: PASS

Run: `cd "Lidl Parser" && python fetch_lidl.py --latest-only`
Expected: prints `new: 0|1, skipped: N, needs-review: [...]`; newest receipt present in app dashboard

- [ ] **Step 5: Verify idempotency**

Run: `cd "Lidl Parser" && python fetch_lidl.py --latest-only` again
Expected: prints `new: 0`

- [ ] **Step 6: Commit**

```bash
git add "Lidl Parser/fetch_lidl.py" "Lidl Parser/requirements.txt" "Lidl Parser/.gitignore" "Lidl Parser/tests/test_fetch_crawl.py"
git commit -m "feat: add Playwright receipt sync crawler"
```

---

### Task 5: README + full-sync verification

**Files:**
- Modify: `Lidl Parser/README.md` (append Fetch section)
- Test: none (verification commands only)

**Interfaces:**
- Consumes: Tasks 1–4 complete.

- [ ] **Step 1: Append README Fetch section**

```markdown
## Fetch (auto-download from Lidl Hellas)

pip install -r requirements.txt
playwright install chromium
python fetch_lidl.py --latest-only   # daily use
python fetch_lidl.py --max 5         # cap history pages
python fetch_lidl.py --paste pasted.txt --tid 14000...  # manual fallback, no login
```

First run opens a browser: log in once at lidl-hellas.gr, then rerun. Session persists in `data/.lidl-profile/` (git-ignored).

- [ ] **Step 2: Run full test suite**

Run: `cd "Lidl Parser" && python -m pytest tests/ -v`
Expected: PASS, all tests green

- [ ] **Step 3: Run full sync dry check**

Run: `cd "Lidl Parser" && python fetch_lidl.py --max 2`
Expected: only genuinely new receipts imported; second run imports 0

- [ ] **Step 4: Commit**

```bash
git add "Lidl Parser/README.md"
git commit -m "docs: document receipt auto-fetch"
```
