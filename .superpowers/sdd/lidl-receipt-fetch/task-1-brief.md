# Task 1 brief — Trim / date / filename pure functions + tests
Source: `docs/superpowers/plans/2026-09-23-lidl-receipt-fetch.md`, Task 1 (verbatim).

**Files:**
- Create: `Lidl Parser/fetch_lib.py`
- Create: `Lidl Parser/tests/test_fetch_lib.py`

**Interfaces:**
- Consumes: nothing (pure stdlib `re`).
- Produces (used by Tasks 2–4, exact signatures — keep these names/types verbatim):
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
    "\u03a3\u0395\u0399\u03a1\u0391: 000000   \u0391\u03a1\u0399\u0398\u039c\u039f\u03a3: 000000\n"
    "0000   000000/00/00       01.01.26   12:00\n"
    "\u03a3\u03a5\u039d\u039f\u039b\u039f                               12,34\n"
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
    assert extract_total(trim_receipt_text(FULL)) == 12.34

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

- [ ] **Step 5: Commit** — SKIPPED by controller ruling (no git repo). Leave files on disk.
