import os
import re
from datetime import datetime

import pandas as pd
from rapidfuzz import process, fuzz

# =====================================================
# CONFIG
# =====================================================

RECEIPT_FOLDER = "receipts"
OUTPUT_FOLDER = "output"

PRODUCT_MAP_FILE = "product_map.csv"
HISTORY_FILE = "product_history.csv"

DEFAULT_CATEGORY = "Διάφορα (Μη κατηγοριοποιημένα)"

# =====================================================
# NORMALIZATION
# =====================================================

def normalize(text):
    if pd.isna(text):
        return ""
    
    return (
        str(text)
        .upper()
        .replace(".", "")
        .replace(",", "")
        .replace("  ", " ")
        .strip()
    )

# =====================================================
# FILE LOADERS
# =====================================================

def load_csv_safe(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    
    try:
        return pd.read_csv(
            path,
            encoding="utf-8-sig",
            sep=None,
            engine="python"
        )
    except Exception as e:
        print(f"⚠️ Failed loading {path}: {e}")
        return pd.DataFrame()

def load_product_map():
    df = load_csv_safe(PRODUCT_MAP_FILE)
    
    if df.empty:
        return pd.DataFrame(
            columns=["raw", "clean", "category"]
        )
    
    df.columns = [c.strip().lower() for c in df.columns]
    
    if "category" not in df.columns:
        df["category"] = DEFAULT_CATEGORY
    
    df["raw"] = df["raw"].astype(str).str.strip()
    df["clean"] = df["clean"].astype(str).str.strip()
    df["category"] = df["category"].astype(str).str.strip()
    
    return df

# =====================================================
# RECEIPT PARSER
# =====================================================

def parse_receipt(text, receipt_name):
    lines = text.splitlines()
    
    data = []
    current = None
    
    for line in lines:
        line = line.strip()
        
        if not line:
            continue
        
        # Product line
        product_match = re.search(
            r"(.+?)\s+([\d,]+)\s+(\d+(?:\.\d+)?)%",
            line
        )
        
        if product_match:
            if current:
                data.append(current)
            
            current = {
                "Receipt": receipt_name,
                "Product": product_match.group(1).strip(),
                "Qty": 1,
                "Unit Price": float(
                    product_match.group(2).replace(",", ".")
                ),
                "VAT": float(product_match.group(3)),
                "Discount": 0
            }
            continue
        
        # Quantity line
        if current:
            qty_match = re.search(
                r"(\d+)\s*x\s*([\d,]+)",
                line
            )
            
            if qty_match:
                current["Qty"] = int(
                    qty_match.group(1)
                )
                current["Unit Price"] = float(
                    qty_match.group(2).replace(",", ".")
                )
                continue
        
        # Discount line
        if current and "-" in line:
            discount_match = re.search(
                r"-([\d,]+)",
                line
            )
            
            if discount_match:
                current["Discount"] += float(
                    discount_match.group(1).replace(",", ".")
                )
    
    if current:
        data.append(current)
    
    if not data:
        return pd.DataFrame(
            columns=[
                "Receipt",
                "Product",
                "Qty",
                "Unit Price",
                "VAT",
                "Discount",
                "Total",
                "Final"
            ]
        )
    
    df = pd.DataFrame(data)
    
    df["Total"] = df["Qty"] * df["Unit Price"]
    df["Final"] = df["Total"] - df["Discount"]
    
    return df

# =====================================================
# MEMORY
# =====================================================

def build_memory(product_map, history):
    frames = [product_map]
    
    if not history.empty:
        required = {"raw", "clean"}
        
        if required.issubset(history.columns):
            frames.append(history)
    
    df = pd.concat(
        frames,
        ignore_index=True
    )
    
    if df.empty:
        return pd.DataFrame(
            columns=[
                "raw",
                "clean",
                "category",
                "key"
            ]
        )
    
    if "category" not in df.columns:
        df["category"] = DEFAULT_CATEGORY
    
    df = df.dropna(
        subset=["raw", "clean"]
    )
    
    df["key"] = df["raw"].apply(
        normalize
    )
    
    return df[
        ["raw", "clean", "category", "key"]
    ]

# =====================================================
# SMART MATCH
# =====================================================

def smart_match(product, memory):
    key = normalize(product)
    
    exact = memory[
        memory["key"] == key
    ]
    
    if not exact.empty:
        row = exact.iloc[0]
        return (
            row["clean"],
            row["category"],
            "exact",
            100
        )
    
    if not memory.empty:
        choices = memory["key"].tolist()
        
        match = process.extractOne(
            key,
            choices,
            scorer=fuzz.token_sort_ratio
        )
        
        if match:
            _, score, idx = match
            
            if score >= 87:
                row = memory.iloc[idx]
                return (
                    row["clean"],
                    row["category"],
                    "fuzzy",
                    score
                )
    
    return (
        product,
        DEFAULT_CATEGORY,
        "new",
        0
    )

# =====================================================
# HISTORY
# =====================================================

def update_history(df):
    history = pd.DataFrame({
        "raw": df["Product"],
        "clean": df["Clean Product"],
        "category": df["Category"],
        "date": df["Date"],
        "price": df["Unit Price"]
    })
    
    if os.path.exists(HISTORY_FILE):
        old = pd.read_csv(
            HISTORY_FILE,
            encoding="utf-8-sig"
        )
        history = pd.concat(
            [old, history],
            ignore_index=True
        )
    
    history.drop_duplicates().to_csv(
        HISTORY_FILE,
        index=False,
        encoding="utf-8-sig"
    )

# =====================================================
# PRICE TRACKING
# =====================================================

def build_price_trends(df):
    return (
        df.groupby(
            ["Clean Product", "Date"]
        )["Unit Price"]
        .agg(["mean", "min", "max"])
        .reset_index()
    )

# =====================================================
# MAIN
# =====================================================

def run_pipeline():
    os.makedirs(
        OUTPUT_FOLDER,
        exist_ok=True
    )
    
    product_map = load_product_map()
    history = load_csv_safe(HISTORY_FILE)
    
    memory = build_memory(
        product_map,
        history
    )
    
    all_data = []
    
    for file in os.listdir(RECEIPT_FOLDER):
        if not file.endswith(".txt"):
            continue
        
        path = os.path.join(
            RECEIPT_FOLDER,
            file
        )
        
        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:
            text = f.read()
        
        df = parse_receipt(
            text,
            file
        )
        
        if df.empty:
            print(
                f"⚠️ No products parsed from {file}"
            )
            continue
        
        date_match = re.search(
            r"(\d{2}-\d{2}-\d{2})",
            file
        )
        
        df["Date"] = (
            date_match.group(1)
            if date_match
            else None
        )
        
        all_data.append(df)
    
    if not all_data:
        print("No receipts found.")
        return
    
    final_df = pd.concat(
        all_data,
        ignore_index=True
    )
    
    results = final_df["Product"].apply(
        lambda x: smart_match(
            x,
            memory
        )
    )
    
    final_df["Clean Product"] = (
        results.apply(lambda x: x[0])
    )
    
    final_df["Category"] = (
        results.apply(lambda x: x[1])
    )
    
    final_df["Match Type"] = (
        results.apply(lambda x: x[2])
    )
    
    final_df["Match Score"] = (
        results.apply(lambda x: x[3])
    )
    
    unknown = final_df[
        final_df["Match Type"] == "new"
    ][["Product"]].drop_duplicates()
    
    if not unknown.empty:
        suggestions = unknown.copy()
        suggestions.columns = ["raw"]
        suggestions["clean"] = ""
        suggestions["category"] = ""
        
        suggestions.to_csv(
            os.path.join(
                OUTPUT_FOLDER,
                "suggested_product_map.csv"
            ),
            index=False,
            encoding="utf-8-sig",
            sep=";"
        )
    
    price_trends = build_price_trends(
        final_df
    )
    
    update_history(final_df)
    
    output_file = os.path.join(
        OUTPUT_FOLDER,
        f"analysis_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.xlsx"
    )
    
    with pd.ExcelWriter(output_file) as writer:
        final_df.to_excel(
            writer,
            sheet_name="All Data",
            index=False
        )
        price_trends.to_excel(
            writer,
            sheet_name="Price Trends",
            index=False
        )
    
    print(
        f"✅ Done: {output_file}"
    )

if __name__ == "__main__":
    run_pipeline()