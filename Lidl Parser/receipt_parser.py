"""Receipt parsing + product matching (fixed version of pipeline2.py).

Fixes vs old pipeline:
- kg lines: '0,590 kg x 14,79 EUR/kg' -> qty=0.59, unit_price=14.79
- 'N x price' quantity lines unchanged
- discount lines (with '-' or 'ekptosi') accumulated
- date parsing handles '26-08-16.1.txt' suffixes -> (iso_date, seq)
"""
import re
import pandas as pd
from rapidfuzz import process, fuzz

DEFAULT_CATEGORY = "Misc"

_CARD_RE = re.compile(r"\*+\s*(\d{4})\s*$")
_CARD_ANYWHERE_RE = re.compile(r"\*+\s*(\d{4})\b")
_CARD_WORD_RE = re.compile(r"(?:ΚΑΡΤ|CARD|VISA|MASTERCARD)[^\d]*(\d{4})", re.IGNORECASE)


def extract_card_last4(text):
    lines = text.splitlines()
    for line in lines:
        m = _CARD_RE.search(line.rstrip())
        if m:
            return m.group(1)
    for line in lines:
        m = _CARD_ANYWHERE_RE.search(line)
        if m:
            return m.group(1)
    for line in lines:
        m = _CARD_WORD_RE.search(line)
        if m:
            return m.group(1)
    return None


def normalize(text):
    if pd.isna(text):
        return ""
    return str(text).upper().replace(".", "").replace(",", "").replace("  ", " ").strip()


def parse_receipt(text, receipt_name=""):
    lines = text.splitlines()
    data = []
    current = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # 1) kg quantity line belongs to previous product, e.g. '0,590 kg x 14,79 EUR/kg'
        kg_match = re.search(r"([\d,]+)\s*kg\s*x\s*([\d,]+)", line, re.IGNORECASE)
        if kg_match and current:
            try:
                current["Qty"] = float(kg_match.group(1).replace(",", "."))
                current["Unit Price"] = float(kg_match.group(2).replace(",", "."))
            except ValueError:
                pass
            continue

        # 2) product line: '<name>  <price>  <VAT>%'
        product_match = re.search(r"(.+?)\s+([\d,]+)\s+(\d+(?:\.\d+)?)%", line)
        if product_match:
            if current:
                data.append(current)
            try:
                price = float(product_match.group(2).replace(",", "."))
            except ValueError:
                continue
            current = {
                "Receipt": receipt_name,
                "Product": product_match.group(1).strip(),
                "Qty": 1,
                "Unit Price": price,
                "VAT": float(product_match.group(3)),
                "Discount": 0.0,
            }
            continue

        # 3) piece quantity line: '4 x 1,29'
        if current:
            qty_match = re.search(r"(\d+)\s*x\s*([\d,]+)", line)
            if qty_match and "kg" not in line.lower():
                try:
                    current["Qty"] = int(qty_match.group(1))
                    current["Unit Price"] = float(qty_match.group(2).replace(",", "."))
                except ValueError:
                    pass
                continue

        # 4) discount line
        if current and ("-" in line or "κπτωση" in line or "έκπτωση" in line):
            d_match = re.search(r"-([\d,]+)", line)
            if d_match:
                try:
                    current["Discount"] += float(d_match.group(1).replace(",", "."))
                except ValueError:
                    pass

    if current:
        data.append(current)

    if not data:
        return pd.DataFrame(columns=["Receipt", "Product", "Qty", "Unit Price",
                                     "VAT", "Discount", "Total", "Final"])
    df = pd.DataFrame(data)
    df["Total"] = df["Qty"] * df["Unit Price"]
    df["Final"] = df["Total"] - df["Discount"]
    return df


def parse_receipt_date(filename):
    """'26-09-05.txt' -> ('2026-09-05', 1); '26-08-16.1.txt' -> ('2026-08-16', 1)."""
    m = re.search(r"(\d{2})-(\d{2})-(\d{2})(?:\.(\d+))?", filename)
    if not m:
        return None, 1
    yy, mm, dd, seq = m.groups()
    return f"20{yy}-{mm}-{dd}", int(seq) if seq else 1


def smart_match(product, memory):
    """memory: DataFrame with raw/clean/category/key columns. Returns (clean, category, type, score)."""
    key = normalize(product)
    exact = memory[memory["key"] == key]
    if not exact.empty:
        row = exact.iloc[0]
        return row["clean"], row.get("category", DEFAULT_CATEGORY), "exact", 100
    if not memory.empty:
        choices = memory["key"].tolist()
        match = process.extractOne(key, choices, scorer=fuzz.token_sort_ratio)
        if match:
            _, score, idx = match
            if score >= 87:
                row = memory.iloc[idx]
                return row["clean"], row.get("category", DEFAULT_CATEGORY), "fuzzy", score
    return product, DEFAULT_CATEGORY, "new", 0


def build_memory(products_df):
    """products_df: raw/clean/category[/barcode...]. Returns df with key column."""
    if products_df.empty:
        return pd.DataFrame(columns=["raw", "clean", "category", "key"])
    df = products_df.copy()
    if "category" not in df.columns:
        df["category"] = DEFAULT_CATEGORY
    df = df.dropna(subset=["raw"])
    df["key"] = df["raw"].apply(normalize)
    return df[["raw", "clean", "category", "key"]]
