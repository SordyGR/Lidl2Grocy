import pandas as pd
import os
from tkinter import Tk, filedialog

# Select Excel file
Tk().withdraw()
file_path = filedialog.askopenfilename(title="Select Excel file")

# Output folder
output_folder = r"C:\Users\sordy\Desktop\Lidl Parser\receipts"
os.makedirs(output_folder, exist_ok=True)

# Load Excel
xls = pd.ExcelFile(file_path)

for sheet_name in xls.sheet_names:
    # Only process sheets with date-like names
    if len(sheet_name) == 8 and sheet_name[2] == "-" and sheet_name[5] == "-":
        df = pd.read_excel(xls, sheet_name=sheet_name, usecols=[0])

        # Drop empty rows
        col = df.iloc[:, 0].dropna().astype(str)

        # Save to txt
        output_path = os.path.join(output_folder, f"{sheet_name}.txt")

        with open(output_path, "w", encoding="utf-8") as f:
            for line in col:
                f.write(line + "\n")

        print(f"Saved: {output_path}")

print("✅ Done!")