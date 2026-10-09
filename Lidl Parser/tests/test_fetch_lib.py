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
    assert receipt_body_date(kept) == ("26", "01", "01")
    assert filename_for_date("26", "01", "01", 0) == "26-01-01.txt"
    assert filename_for_date("26", "01", "01", 1) == "26-01-01.1.txt"

def test_extract_total():
    assert extract_total(trim_receipt_text(FULL)) == 12.34

def test_trim_missing_markers_raises():
    import pytest
    with pytest.raises(ValueError):
        trim_receipt_text("just some page header\nno receipt here\n")
    with pytest.raises(ValueError):
        trim_receipt_text("22 \u03a3\u03b5\u03c0 2026\nlogo\nno end marker\n")
