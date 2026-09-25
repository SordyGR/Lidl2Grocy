import pandas as pd

# Read the barcodes Excel file with proper encoding
bc_path = "Barcodes.xlsx"
df_barcodes = pd.read_excel(bc_path)

print("=== BARCODES FILE STRUCTURE ===")
print(f"\nTotal rows: {len(df_barcodes)}")
print(f"\nColumns: {list(df_barcodes.columns)}")
print(f"\nColumn data types:")
for col in df_barcodes.columns:
    print(f"  - {col}: {df_barcodes[col].dtype}")

print("\n\n=== SAMPLE DATA (First 10 rows) ===")
# Convert all columns to string for safe display
display_df = df_barcodes.copy()
for col in display_df.columns:
    display_df[col] = display_df[col].astype(str)
print(display_df.head(10).to_string())
