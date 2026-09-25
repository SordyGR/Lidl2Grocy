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


def receipt_body_date(kept_text: str) -> tuple[str, str, str]:
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
