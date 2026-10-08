"""Pure helpers for Lidl digital-receipt fetching (no browser, no DB)."""
import re

_START = re.compile(r"^\s*\d{1,2}\s+[^\W\d_]+\s+\d{4}\s*$")
_BODY_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})\s+\d{2}:\d{2}")
_TOTAL = re.compile(r"\u03a3\u03a5\u039d\u039f\u039b\u039f\s+([\d,]+)")
_MARK = re.compile(r"(?:MAPK|\u039c\u0391\u03a1\u039a)\s*:\s*(\d{9,20})")


def extract_mark(full_text: str):
    """Fiscal receipt id (MAPK/ΜΑΡΚ: digits) from the full page text.

    Lives in the footer *after* the trimmed body, so callers must pass
    the untrimmed text. Returns the digit string or None.
    """
    m = _MARK.search(full_text or "")
    return m.group(1) if m else None


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


def canonical_raw(conn, raw: str) -> str:
    """First-seen spelling wins: reuse an existing product raw that matches
    accent-/case-insensitively, so receipt spelling variance (ML/ml, accents)
    cannot fork duplicate product rows."""
    row = conn.execute("SELECT raw FROM products WHERE grnorm(raw)=grnorm(?) ORDER BY id LIMIT 1",
                       (raw,)).fetchone()
    return row[0] if row else raw


def write_receipt_rows(conn, df, *, filename: str, iso_date, seq: int,
                       total: float, card_last4, mark, store: str,
                       t_id, source_url=None) -> dict:
    """Shared receipt-writing core for every import pipeline.

    Front ends (Lidl kept-text, e-invoices, …) parse into a DataFrame with
    Product/Qty/Unit Price/VAT/Discount columns and call this: MAPK
    dedupe, receipts/items/products rows, fetch_state link. All pipelines
    share one catalog, one dedupe, one row shape.
    """
    from receipt_parser import smart_match, build_memory
    if mark:
        hit = conn.execute("SELECT filename FROM receipts WHERE mark=?", (mark,)).fetchone()
        if hit:
            if t_id is not None:
                conn.execute("INSERT OR IGNORE INTO fetch_state (t_id, filename) VALUES (?, ?)",
                             (t_id, hit["filename"]))
                conn.commit()
            return {"filename": hit["filename"], "items": 0, "total": 0.0,
                    "t_id": t_id, "duplicate": True, "mark": mark}
    cur = conn.cursor()
    cur.execute(
        """INSERT OR IGNORE INTO receipts (filename, receipt_date, receipt_seq, item_count, total, card_last4, mark, store, source_url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (filename, iso_date, seq, len(df), total, card_last4, mark, store, source_url))
    conn.commit()
    rid = cur.execute("SELECT id FROM receipts WHERE filename=?", (filename,)).fetchone()["id"]
    cur.execute("DELETE FROM items WHERE receipt_id=?", (rid,))
    mem = build_memory(_pd.read_sql("SELECT raw, clean, category FROM products", conn))
    for _, item in df.iterrows():
        raw = canonical_raw(conn, item["Product"])
        clean, cat, mtype, score = smart_match(raw, mem)
        tot = float(item["Qty"] * item["Unit Price"])
        cur.execute(
            """INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price,
                                  vat, discount, total, final, match_type, match_score)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, raw, clean, cat, float(item["Qty"]),
             float(item["Unit Price"]), float(item["VAT"]), float(item["Discount"]),
             round(tot, 2), round(tot - float(item["Discount"]), 2), mtype, float(score)))
        cur.execute("""INSERT OR IGNORE INTO products (raw, clean, category, needs_review)
                       VALUES (?,?,?,1)""", (raw, clean, cat))
    if t_id is not None:
        cur.execute("INSERT OR IGNORE INTO fetch_state (t_id, filename) VALUES (?, ?)", (t_id, filename))
    conn.commit()
    return {"filename": filename, "items": len(df), "total": total, "t_id": t_id, "mark": mark}


def import_kept_receipt(conn, kept_text: str, filename: str, t_id, mark=None) -> dict:
    from receipt_parser import parse_receipt, parse_receipt_date, extract_card_last4
    df = parse_receipt(kept_text, filename)
    if df.empty:
        raise ValueError(f"{filename}: no items parsed")
    parsed_total = round(float((df["Qty"] * df["Unit Price"] - df["Discount"]).sum()), 2)
    if abs(parsed_total - extract_total(kept_text)) > 0.02:
        raise ValueError(f"{filename}: total mismatch parsed={parsed_total}")
    iso_date, seq = parse_receipt_date(filename)
    card_last4 = extract_card_last4(kept_text)
    return write_receipt_rows(conn, df, filename=filename, iso_date=iso_date,
                              seq=seq, total=parsed_total, card_last4=card_last4,
                              mark=mark, store="Lidl", t_id=t_id,
                              source_url=None)
