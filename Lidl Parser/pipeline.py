import pandas as pd
import os
import re
from datetime import datetime
from rapidfuzz import process, fuzz

# =====================
# CONFIG
# =====================
RECEIPT_FOLDER = "receipts"
OUTPUT_FOLDER = "output"
CATEGORY_FILE = "categories.csv"
PRODUCT_MAP_FILE = "product_map.csv"
HISTORY_FILE = "product_history.csv"


# =====================
# UTIL: NORMALIZATION
# =====================
def normalize(text):
    return (
        str(text)
        .upper()
        .replace(".", "")
        .replace(",", "")
        .replace("  ", " ")
        .strip()
    )


# =====================
# LOADERS
# =====================
def load_csv_safe(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, encoding="utf-8-sig", sep=None, engine="python")
    except Exception:
        return pd.DataFrame()


def load_product_map():
    df = load_csv_safe(PRODUCT_MAP_FILE)
    if df.empty:
        return pd.DataFrame(columns=["raw", "clean"])

    df.columns = [c.strip().lower() for c in df.columns]
    df["raw"] = df["raw"].astype(str).str.strip()
    df["clean"] = df["clean"].astype(str).str.strip()
    return df


# =====================
# RECEIPT PARSER
# =====================
def parse_receipt(text, receipt_name):
    lines = text.splitlines()
    data = []
    current = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Product line
        match = re.search(r'(.+?)\s+([\d,]+)\s+(\d+(?:\.\d+)?)%', line)
        if match:
            if current:
                data.append(current)

            current = {
                "Receipt": receipt_name,
                "Product": match.group(1).strip(),
                "Qty": 1,
                "Unit Price": float(match.group(2).replace(",", ".")),
                "VAT": float(match.group(3)),
                "Discount": 0
            }

        # Quantity line
        elif "x" in line and current:
            q_match = re.search(r'(\d+)\s*x\s*([\d,]+)', line)
            if q_match:
                current["Qty"] = int(q_match.group(1))
                current["Unit Price"] = float(q_match.group(2).replace(",", "."))

        # Discount line
        elif "-" in line and current:
            d_match = re.search(r'-([\d,]+)', line)
            if d_match:
                current["Discount"] += float(d_match.group(1).replace(",", "."))

    if current:
        data.append(current)

    if not data:
        return pd.DataFrame(columns=[
            "Receipt", "Product", "Qty",
            "Unit Price", "VAT", "Discount",
            "Total", "Final"
        ])

    df = pd.DataFrame(data)

    df["Total"] = df["Qty"] * df["Unit Price"]
    df["Final"] = df["Total"] - df["Discount"]

    return df

    return df


# =====================
# PRODUCT MEMORY
# =====================
def build_memory(mapping, history):
    df = pd.concat([mapping, history], ignore_index=True)
    if df.empty:
        return pd.DataFrame(columns=["raw", "clean", "key"])

    df = df.dropna(subset=["raw", "clean"])
    df["key"] = df["raw"].apply(normalize)
    return df


# =====================
# SMART MATCHING
# =====================
def smart_match(product, memory):
    key = normalize(product)

    # Exact match
    exact = memory[memory["key"] == key]
    if not exact.empty:
        return exact.iloc[0]["clean"], "exact", 100

    # Fuzzy match
    if not memory.empty:
        choices = memory["key"].tolist()

        match = process.extractOne(
            key,
            choices,
            scorer=fuzz.token_sort_ratio
        )

        if match:
            best_match, score, idx = match

            if score >= 87:
                return memory.iloc[idx]["clean"], "fuzzy", score

    # New product
    return product, "new", 0


# =====================
# CATEGORY
# =====================
def categorize(product, rules):
    if rules.empty:
        return "Other"

    product = normalize(product)

    for _, row in rules.iterrows():
        keyword = normalize(row.get("keyword", ""))
        if keyword and keyword in product:
            return row.get("category", "Other")

    return "Other"


# =====================
# HISTORY UPDATE
# =====================
def update_history(df):
    history = pd.DataFrame({
        "raw": df["Product"],
        "clean": df["Clean Product"],
        "date": df["Date"],
        "price": df["Unit Price"]
    })

    if os.path.exists(HISTORY_FILE):
        old = pd.read_csv(HISTORY_FILE, encoding="utf-8-sig")
        history = pd.concat([old, history], ignore_index=True)

    history.drop_duplicates().to_csv(
        HISTORY_FILE,
        index=False,
        encoding="utf-8-sig"
    )


# =====================
# PRICE TRACKING
# =====================
def build_price_trends(df):
    return df.groupby(
        ["Clean Product", "Date"]
    )["Unit Price"].agg(["mean", "min", "max"]).reset_index()


# =====================
# MAIN PIPELINE
# =====================
def run_pipeline():

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    categories = load_csv_safe(CATEGORY_FILE)
    product_map = load_product_map()
    history = load_csv_safe(HISTORY_FILE)

    memory = build_memory(product_map, history)

    all_data = []

    # Process receipts
    for file in os.listdir(RECEIPT_FOLDER):
        if file.endswith(".txt"):

            with open(os.path.join(RECEIPT_FOLDER, file), "r", encoding="utf-8") as f:
                text = f.read()

            df = parse_receipt(text, file)

            # 🔥 DEBUG CHECK (PUT IT HERE)
            if df.empty:
                print(f"⚠️ WARNING: No data parsed from {file}")
                continue

            date_match = re.search(r'(\d{2}-\d{2}-\d{2})', file)
            df["Date"] = date_match.group(1) if date_match else None

            all_data.append(df)

    if not all_data:
        print("No receipts found.")
        return

    final_df = pd.concat(all_data, ignore_index=True)

    # SMART MATCHING
    results = final_df["Product"].apply(
        lambda x: smart_match(x, memory)
    )

    final_df["Clean Product"] = results.apply(lambda x: x[0])
    final_df["Match Type"] = results.apply(lambda x: x[1])
    final_df["Match Score"] = results.apply(lambda x: x[2])

    # CATEGORY
    final_df["Category"] = final_df["Clean Product"].apply(
        lambda x: categorize(x, categories)
    )
    
    # =========================
    # 🔥 UNKNOWN PRODUCT EXPORT (PASTE HERE)
    # =========================
    unknown = final_df[
        final_df["Match Type"] == "new"
    ][["Product"]].drop_duplicates()

    if not unknown.empty:
        suggestions_df = unknown.copy()
        suggestions_df.columns = ["raw"]
        suggestions_df["clean"] = ""

        suggestions_df.to_csv(
            os.path.join(OUTPUT_FOLDER, "suggested_product_map.csv"),
            index=False,
            encoding="utf-8-sig"
        )
    
    # PRICE TRACKING
    price_trends = build_price_trends(final_df)

    # HISTORY UPDATE
    update_history(final_df)

    # NEW PRODUCTS EXPORT
    new_products = final_df[
        final_df["Match Type"] == "new"
    ]["Product"].drop_duplicates()

    if not new_products.empty:
        new_products.to_csv(
            os.path.join(OUTPUT_FOLDER, "new_products.csv"),
            index=False,
            encoding="utf-8-sig"
        )

    # EXPORT EXCEL
    output_file = os.path.join(
        OUTPUT_FOLDER,
        f"analysis_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.xlsx"
    )

    with pd.ExcelWriter(output_file) as writer:
        final_df.to_excel(writer, sheet_name="All Data", index=False)
        price_trends.to_excel(writer, sheet_name="Price Trends", index=False)

    print("✅ Done:", output_file)


# =====================
# RUN
# =====================
if __name__ == "__main__":
    run_pipeline()