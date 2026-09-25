"""One-shot migration: product_map.csv + Barcodes.xlsx + receipts/*.txt + product_history.csv -> SQLite."""
import os
import sys
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from db import init_db, get_db
from receipt_parser import parse_receipt, parse_receipt_date, smart_match, build_memory, DEFAULT_CATEGORY

PRODUCT_MAP = os.path.join(BASE_DIR, "product_map.csv")
HISTORY_CSV = os.path.join(BASE_DIR, "product_history.csv")
RECEIPT_FOLDER = os.path.join(BASE_DIR, "receipts")
BARCODES_XLSX = os.path.join(BASE_DIR, "Barcodes.xlsx")


def load_product_map():
    df = pd.read_csv(PRODUCT_MAP, encoding="utf-8-sig", sep=None, engine="python")
    df.columns = [c.strip().lower() for c in df.columns]
    for col in ("raw", "clean", "category"):
        if col not in df.columns:
            df[col] = ""
    df["raw"] = df["raw"].astype(str).str.strip()
    df["clean"] = df["clean"].astype(str).str.strip()
    df["category"] = df["category"].astype(str).str.strip()
    # drop the 4 placeholder rows with empty clean AND empty category
    df = df[~((df["clean"] == "") & (df["category"] == ""))]
    df = df[df["raw"] != ""]
    return df[["raw", "clean", "category"]]


def load_barcodes():
    """Barcodes.xlsx: col0=barcode, col1=clean name. Returns dict clean->barcode."""
    if not os.path.exists(BARCODES_XLSX):
        return {}
    try:
        df = pd.read_excel(BARCODES_XLSX, header=None)
        mapping = {}
        for _, row in df.iterrows():
            barcode, clean = str(row.iloc[0]).strip(), str(row.iloc[1]).strip()
            if barcode and clean and clean.lower() != "nan":
                mapping[clean] = barcode
        return mapping
    except Exception as e:
        print(f"Barcode load skipped: {e}")
        return {}


def migrate():
    init_db()
    conn = get_db()
    cur = conn.cursor()

    pmap = load_product_map()
    barcodes = load_barcodes()
    print(f"product_map rows: {len(pmap)}, barcodes: {len(barcodes)}")

    for _, r in pmap.iterrows():
        barcode = barcodes.get(r["clean"], None)
        cur.execute(
            """INSERT OR IGNORE INTO products (raw, clean, category, barcode)
               VALUES (?, ?, ?, ?)""",
            (r["raw"], r["clean"] or r["raw"],
             r["category"] or DEFAULT_CATEGORY, barcode),
        )
    # backfill barcodes where clean matches but raw insert already existed
    for clean, code in barcodes.items():
        cur.execute("UPDATE products SET barcode=? WHERE clean=? AND (barcode IS NULL OR barcode='')",
                    (code, clean))
    conn.commit()

    memory = build_memory(pd.read_sql("SELECT raw, clean, category FROM products", conn))

    # receipts
    files = sorted(f for f in os.listdir(RECEIPT_FOLDER) if f.endswith(".txt"))
    print(f"receipt files: {len(files)}")
    n_receipts = n_items = 0
    for fname in files:
        with open(os.path.join(RECEIPT_FOLDER, fname), "r", encoding="utf-8") as f:
            text = f.read()
        df = parse_receipt(text, fname)
        if df.empty:
            print(f"  skip (no items): {fname}")
            continue
        iso_date, seq = parse_receipt_date(fname)
        total = float((df["Qty"] * df["Unit Price"] - df["Discount"]).sum())
        cur.execute(
            """INSERT OR IGNORE INTO receipts (filename, receipt_date, receipt_seq, item_count, total)
               VALUES (?, ?, ?, ?, ?)""",
            (fname, iso_date, seq, len(df), round(total, 2)),
        )
        conn.commit()
        cur.execute("SELECT id FROM receipts WHERE filename=?", (fname,))
        rid = cur.fetchone()["id"]
        cur.execute("DELETE FROM items WHERE receipt_id=?", (rid,))  # idempotent re-run
        for _, item in df.iterrows():
            clean, cat, mtype, score = smart_match(item["Product"], memory)
            tot = float(item["Qty"] * item["Unit Price"])
            cur.execute(
                """INSERT INTO items (receipt_id, raw, clean, category, qty, unit_price,
                                      vat, discount, total, final, match_type, match_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rid, item["Product"], clean, cat, float(item["Qty"]),
                 float(item["Unit Price"]), float(item["VAT"]), float(item["Discount"]),
                 round(tot, 2), round(tot - float(item["Discount"]), 2), mtype, float(score)),
            )
        n_receipts += 1
        n_items += len(df)
    conn.commit()
    print(f"imported receipts: {n_receipts}, items: {n_items}")

    # legacy price history (keep for continuity, source='legacy_csv')
    if os.path.exists(HISTORY_CSV):
        cur.execute("DELETE FROM price_history WHERE source='legacy_csv'")
        h = pd.read_csv(HISTORY_CSV, encoding="utf-8-sig")
        h.columns = [c.strip().lower() for c in h.columns]
        rows = 0
        for _, r in h.iterrows():
            raw = str(r.get("raw", "")).strip()
            clean = str(r.get("clean", "")).strip()
            if not raw or clean in ("", "nan", "None"):
                continue
            cur.execute(
                "INSERT INTO price_history (raw, clean, category, date, price, source) VALUES (?, ?, ?, ?, ?, 'legacy_csv')",
                (raw, clean or raw, str(r.get("category", "")) or None,
                 str(r.get("date", "")), pd.to_numeric(r.get("price"), errors="coerce")),
            )
            rows += 1
        conn.commit()
        print(f"legacy price_history rows: {rows}")

    conn.close()
    print("Migration done.")


if __name__ == "__main__":
    migrate()
