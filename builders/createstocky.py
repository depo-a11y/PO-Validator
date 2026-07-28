import pandas as pd
import os

# === Ask for Excel file ===
source_file = input("Enter the Excel file name (e.g. data.xlsx): ").strip()

if not os.path.exists(source_file):
    print(f"File '{source_file}' not found.")
    exit()

# Read Excel
df = pd.read_excel(source_file)

# Ask for number (e.g. 2 â†’ 2D)
num = input("Enter a number (e.g. 2): ").strip()
d_col = f"{num}D"

# Required columns
required_cols = ["Variant SKU", "Variant Cost", d_col]

# Validate columns
missing = [c for c in required_cols if c not in df.columns]
if missing:
    print(f"Missing columns: {', '.join(missing)}")
    print("Available columns:", ", ".join(str(c) for c in df.columns))
    exit()

# Filter rows where Dx column has a non-empty value
filtered_df = df[df[d_col].notna() & (df[d_col].astype(str).str.strip() != "")]

# Keep only 3 columns
result = filtered_df[["Variant SKU", "Variant Cost", d_col]]

# Save as CSV
output_file = os.path.splitext(source_file)[0] + f"_{d_col}_filtered.csv"
result.to_csv(output_file, index=False)

print(f"Created new CSV file: {output_file}")
print(f"Rows kept: {len(result)}")
print("Columns included:")
for col in result.columns:
    print(" -", col)