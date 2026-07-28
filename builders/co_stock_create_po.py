import pandas as pd
import os
import sys

# === Ask for input file name ===
source_file = input("Enter the Excel or CSV file name (e.g. data.xlsx): ").strip()

if not os.path.exists(source_file):
    print(f"File '{source_file}' not found.")
    sys.exit()

# Detect file type
ext = os.path.splitext(source_file)[1].lower()
if ext in [".xls", ".xlsx"]:
    df = pd.read_excel(source_file)
elif ext == ".csv":
    df = pd.read_csv(source_file)
else:
    print("âŒ Unsupported file type. Use .xlsx, .xls, or .csv")
    sys.exit()

# === Check that Command column only has "NEW" ===
if "Command" in df.columns:
    invalid_commands = df.loc[~df["Command"].fillna("").str.upper().eq("MERGE"), "Command"]
    if not invalid_commands.empty:
        print("ERROR: The 'Command' column must only contain 'MERGE'.")
        print("   Found invalid values:")
        for value in invalid_commands.unique():
            print(f"   - {value}")
        sys.exit()
else:
    print("WARNING: No 'Command' column found. Skipping validation.")

# === Columns in exact order ===
columns_in_order = [
    "Tags Command","ID","Handle","Command", "Title", "Vendor", "Type", "Tags", 
    "Body HTML", "Status", "Published", "Option1 Name", "Option1 Value", "Option2 Name", 
    "Option2 Value", "Variant SKU", "Variant Barcode", "Variant Price", 
    "Variant Compare At Price", "Variant Cost",
    "Variant Inventory Tracker",
    "Metafield: my_fields.manufacture_code",
    "Metafield: my_fields.supplier_code [single_line_text_field]",
    "Metafield: custom.product_season [single_line_text_field]",
    "Metafield: custom.new_sale [single_line_text_field]",
    "Variant Metafield: Variant.cost_price [single_line_text_field]",
    "Variant Metafield:custom.manufacture_code[single_line_text_field]",
    "Variant Metafield: custom.season [single_line_text_field]",
    "Variant Metafield: custom.new_sale [single_line_text_field]",
    "Variant Metafield: Variant.gtin [single_line_text_field]",
    "Variant Country of Origin", "HS Code", "Metafield: title_tag"
]

# Rename 'COO' to 'Variant Country of Origin' if it exists
if "COO" in df.columns:
    df.rename(columns={"COO": "Variant Country of Origin"}, inplace=True)

# Fill Variant Inventory Tracker with "Shopify"
df["Variant Inventory Tracker"] = "Shopify"

# Ensure all columns exist, fill missing with blanks
for col in columns_in_order:
    if col not in df.columns:
        df[col] = ""

# Reorder columns exactly
df_ordered = df[columns_in_order]

# === Output file name (same base name + "_ordered.xlsx") ===
base_name = os.path.splitext(source_file)[0]
output_file = f"{base_name}_ordered.xlsx"

# Save output, overwriting if file exists
df_ordered.to_excel(output_file, index=False)

print(f"\nCreated Excel file (overwriting if exists): {output_file}")
print(f"Columns included ({len(df_ordered.columns)}):")
for col in df_ordered.columns:
    print(" -", col)