import pandas as pd
import os
import sys
import re
from pathlib import Path

# Shared tag template lives at the po-validator repo root (this file sits in builders/)
EXPECTED_TAGS = str(Path(__file__).resolve().parent.parent / "expected_tags.xlsx")

def get_excel_row(index):
    """Converts pandas index to Excel row number (1-based, plus 1 for header)."""
    return index + 2

def validate_vendors(df):
    """Strictly validates Vendor names against the approved list (Case-Sensitive)."""
    approved_vendors = [
        "ALEXANDER MCQUEEN", "ALAIA", "AMI PARIS", "AMIRI", "AUTRY", "BALENCIAGA", 
        "BALMAIN", "CASABLANCA", "NOIR KEI NINOMIYA", "CFCL", "CHLOE", "DIESEL", 
        "DOUBLET", "DOVER STREET MARKET", "ENTIRE STUDIOS", "EGONLAB", 
        "FENG CHEN WANG", "FEAR OF GOD", "FEAR OF GOD ESSENTIALS", "JIL SANDER", 
        "KENZO", "KIDSUPER", "LOEWE", "LEMAIRE", "MARK GONG", "MAISON MARGIELA", 
        "MAISON MIHARA YASUHIRO", "PALM ANGELS", "POST ARCHIVE FACTION", 
        "REPRESENT", "REMAGINER", "RHUDE", "RICK OWENS", "RICK OWENS x Moncler", 
        "RICK OWENS DRKSHDW", "SACAI", "STONE ISLAND", "TAION", "THUG CLUB", 
        "VOWELS", "WE11DONE", "WILLY CHAVARRIA", "WOOYOUNGMI", "UNDERCOVER",
        "VEJA", "WALES BONNER", "SONG FOR THE MUTE", "Y-3", "HOKA"
    ]
    
    print("🏢 Validating Vendor names (Case-Sensitive)...")
    error_found = False
    for idx, row in df.iterrows():
        actual_vendor = str(row.get("Vendor", "")).strip()
        if actual_vendor not in approved_vendors:
            print(f"❌ INVALID VENDOR - Row {get_excel_row(idx)}: '{actual_vendor}'")
            error_found = True
            
    if error_found:
        sys.exit("\n🛑 Vendor casing or naming error. Please fix before proceeding.")
    print("✅ Vendor validation passed.")

def validate_size_scale(df):
    """Validates Size Scale against the approved 7 values (Case-Sensitive)."""
    approved_scales = ["Standard", "EU", "FR", "IT", "ONE_SIZE", "Numeric", "Waist","BELTS MEN'S CM","BELTS WOMEN'S CM"]
    col_name = "Metafield: custom.size_scale [single_line_text_field]"
    
    print("📏 Validating Size Scales...")
    error_found = False
    for idx, row in df.iterrows():
        actual_scale = str(row.get(col_name, "")).strip()
        if actual_scale not in approved_scales:
            print(f"❌ INVALID SIZE SCALE - Row {get_excel_row(idx)}: '{actual_scale}' (Must be: {approved_scales})")
            error_found = True
            
    if error_found:
        sys.exit("\n🛑 Size Scale error detected. Process stopped.")
    print("✅ Size Scale validation passed.")

def validate_duplicate_skus(df):
    """Exits if duplicate Variant SKUs are found."""
    sku_col = "Variant SKU"
    if sku_col in df.columns:
        duplicates = df[df.duplicated(subset=[sku_col], keep=False)]
        if not duplicates.empty:
            print("\n👯 DUPLICATE SKUS FOUND:")
            unique_dupes = duplicates[sku_col].unique()
            for sku in unique_dupes:
                rows = [get_excel_row(i) for i in duplicates.index[duplicates[sku_col] == sku]]
                print(f"❌ SKU: '{sku}' appears on Rows: {rows}")
            sys.exit("\n🛑 Duplicate SKUs detected. Process stopped.")
    print("✅ SKU uniqueness check passed.")

def validate_tags_and_type(df, template_file=EXPECTED_TAGS):
    """Strictly validates Product Type and Tags against a template file."""
    if not os.path.exists(template_file):
        print(f"⚠️ Warning: '{template_file}' not found. Skipping tag validation.")
        return

    print("🏷️  Validating Product Types and Tags...")
    try:
        template_df = pd.read_excel(template_file)
        template_df.columns = [str(c).strip() for c in template_df.columns]
        type_col = 'Type' if 'Type' in template_df.columns else 'Product Type'
        tags_col = 'Tags'
        
        if type_col not in template_df.columns or tags_col not in template_df.columns:
            sys.exit(f"🛑 Error: {template_file} must have columns named 'Type' and 'Tags'.")

        tag_lookup = {str(r[type_col]).strip(): {t.strip() for t in str(r[tags_col]).split(',') if t.strip()} for _, r in template_df.iterrows()}

        for idx, row in df.iterrows():
            actual_type = str(row.get("Type", "")).strip()
            actual_tags = {t.strip() for t in str(row.get("Tags", "")).split(',') if t.strip()}
            
            if actual_type not in tag_lookup:
                print(f"❌ INVALID PRODUCT TYPE - Row {get_excel_row(idx)}: '{actual_type}'")
                sys.exit()
            
            required_tags = tag_lookup[actual_type]
            if not required_tags.issubset(actual_tags):
                print(f"❌ MISSING TAGS - Row {get_excel_row(idx)} | Type: '{actual_type}' | Missing: {list(required_tags - actual_tags)}")
                sys.exit()
        print("✅ Tag/Type validation passed.")
    except Exception as e:
        sys.exit(f"❌ Fatal error reading {template_file}: {e}")

def check_mandatory_empty_cells(df, columns_to_check):
    """Exits if any mandatory cell is empty, excluding specific optional/auto-filled columns."""
    optional_cols = [
        "Metafield: custom.made_in [single_line_text_field]",
        "Variant Metafield: Variant.gtin [single_line_text_field]",
        "Variant HS Code",
        "Inventory Available: Defective",
        "Inventory Available: Marais Men - QV",
        "Inventory Available: Marais Women - Bourke", "Inventory Available: Marais Women - QV", 
        "Inventory Available: Photoshoot", "Inventory Available: Warehouse", 
        "Variant Inventory Tracker", "Variant Metafield: Variant.cost_price [single_line_text_field]"
    ]
    
    print("🔍 Scanning for empty cells...")
    error_found = False
    for col in columns_to_check:
        if col in optional_cols: continue
        missing_mask = df[col].astype(str).str.strip().eq("") | df[col].isna()
        if missing_mask.any():
            error_found = True
            for idx in df.index[missing_mask]:
                print(f"❌ EMPTY CELL - Row {get_excel_row(idx)}: Column '{col}' is missing a value.")
    if error_found: sys.exit("\n🛑 Execution stopped: Mandatory cells cannot be empty.")

def validate_data_and_log_errors(df):
    """Flags margin errors and title tag length issues."""
    errors = []
    print("💰 Checking Price Margins and SEO Title lengths...")
    for idx, row in df.iterrows():
        try:
            price = float(str(row.get("Variant Price", 0)).replace(',', '').replace('$', ''))
            cost = float(str(row.get("Variant Cost", 0)).replace(',', '').replace('$', ''))
            vendor_lower = str(row.get("Vendor", "")).strip().lower()
            if cost > 0:
                threshold = 2.2 if vendor_lower in ["veja", "taion","trudon","creed","dior"] else 2.5
                if (price / cost) < threshold:
                    errors.append({"Row": get_excel_row(idx), "SKU": row.get("Variant SKU"), "Type": "LOW MARGIN", "Details": f"{round(price/cost, 2)}x"})
        except: pass

        title_tag = str(row.get("Metafield: title_tag", ""))
        if len(title_tag) > 60:
            errors.append({"Row": get_excel_row(idx), "SKU": row.get("Variant SKU"), "Type": "SEO TITLE TOO LONG", "Details": f"{len(title_tag)} chars"})

    if errors:
        pd.DataFrame(errors).to_excel("VALIDATION_ERRORS_REPORT.xlsx", index=False)
    return len(errors)

def validate_cost_currency_format(df):
    """Ensures Variant Cost Metafield is in format 'CURRENCY [space] VALUE' (e.g., EUR 150)."""
    col_name = "Variant Metafield: Variant.cost_price [single_line_text_field]"
    if col_name not in df.columns:
        return

    print("💶 Validating Cost Currency format...")
    error_found = False
    # Pattern: 3 uppercase letters, a space, then numbers (allowing decimals)
    pattern = r'^[A-Z]{3}\s\d+(\.\d{1,2})?$'
    
    for idx, row in df.iterrows():
        val = str(row.get(col_name, "")).strip()
        if val == "nan" or val == "": continue # Skip if empty/optional
        
        if not re.match(pattern, val):
            print(f"❌ INVALID COST FORMAT - Row {get_excel_row(idx)}: '{val}' (Expected format: 'EUR 123' or 'USD 123.45')")
            error_found = True
            
    if error_found:
        sys.exit("\n🛑 Cost currency format error. Please fix before proceeding.")
    print("✅ Cost currency format passed.")

def run_transformations(df):
    """Handles formatting, SKU/Season/Sale syncs, and Inventory auto-fill."""
    
    # Load Expected Tags Mapping
    template_file = EXPECTED_TAGS
    tag_lookup = {}
    if os.path.exists(template_file):
        temp_df = pd.read_excel(template_file)
        # Create a dictionary: { "Type": ["Tag1", "Tag2"] }
        tag_lookup = {
            str(r.get('Type', r.get('Product Type'))).strip(): 
            [t.strip() for t in str(r.get('Tags', '')).split(',') if t.strip()]
            for _, r in temp_df.iterrows()
        }
    def generate_all_tags(row):
        product_type = str(row.get("Type", "")).strip()
        vendor = str(row.get("Vendor", "")).strip()
        season = str(row.get("Metafield: custom.product_season [single_line_text_field]", "")).strip()
        
        # 1. Start with tags from the Excel mapping
        final_tags = tag_lookup.get(product_type, [])
        
        # 2. Add Vendor and Season tags
        if vendor: final_tags.append(vendor)
        if season: final_tags.append(season)
        
        # 3. Clean up: Remove duplicates and join with commas
        # We use dict.fromkeys to preserve order while removing duplicates
        return ", ".join(list(dict.fromkeys(final_tags)))

    # Apply the automation
    df["Tags"] = df.apply(generate_all_tags, axis=1)

    # ... (keep your existing sync codes and price logic) ...
    
    def split_t(val):
        p = str(val).split()
        return pd.Series([p[0], p[1], " ".join(p[2:4])]) if len(p) >= 3 else pd.Series(["", "", ""])
    df[["Metafield: custom.gender [single_line_text_field]", "Metafield: custom.category [single_line_text_field]", "Metafield: custom.sub_category [single_line_text_field]"]] = df["Type"].apply(split_t)

    # Auto-fill Inventory
    inventory_cols = ["Inventory Available: Defective", "Inventory Available: Marais Men - QV", "Inventory Available: Marais Women - Bourke", "Inventory Available: Marais Women - QV", "Inventory Available: Photoshoot", "Inventory Available: Warehouse"]
    for col in inventory_cols: df[col] = 0
    df["Variant Inventory Tracker"] = "shopify"

    # Sync Codes
    m_code = "Metafield: my_fields.manufacture_code"
    if m_code in df.columns:
        df["Metafield: my_fields.supplier_code [single_line_text_field]"] = df[m_code]
        df["Variant Metafield:custom.manufacture_code[single_line_text_field]"] = df[m_code]
        df["Metafield: custom.brand_color_id [single_line_text_field]"] = df[m_code].astype(str).apply(lambda x: x.split()[-1] if " " in x.strip() else "")

    # Sync Season, Sale, and SKU to Barcode
    df["Variant Metafield: custom.season [single_line_text_field]"] = df.get("Metafield: custom.product_season [single_line_text_field]", "")
    df["Variant Metafield: custom.new_sale [single_line_text_field]"] = df.get("Metafield: custom.new_sale [single_line_text_field]", "")
    df["Variant Barcode"] = df.get("Variant SKU", "")
    
    if m_code in df.columns and "Option2 Value" in df.columns:
        df["FULLCODE"] = (df[m_code].astype(str) + df["Option2 Value"].astype(str)).str.replace(" ", "", regex=False)

    # Local Price Calculation — LMP standard = 1.35 × Compare At Price (fallback: Variant Price)
    _lmp_price = pd.to_numeric(df["Variant Price"].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce')
    _lmp_compare = pd.to_numeric(df.get("Variant Compare At Price", pd.Series(index=df.index, dtype="object")).astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce')
    _lmp_base = _lmp_compare.where(_lmp_compare.notna() & (_lmp_compare > 0), _lmp_price)
    df["Metafield: custom.local_market_price [single_line_text_field]"] = (_lmp_base * 1.35).round(2)

    df.rename(columns={"HS Code": "Variant HS Code"}, inplace=True)
    return df

#work in progress
def assign_size_scale(row):
    """
    Automatically assigns size scales based on Gender, Category, 
    and the numerical range of the Size (Option2 Value).
    """
    # 1. Extract and Clean Variables
    size_raw = str(row.get("Option2 Value", "")).strip().upper()
    gender = str(row.get("Metafield: custom.gender [single_line_text_field]", "")).strip().upper()
    category = str(row.get("Metafield: custom.category [single_line_text_field]", "")).strip().upper()
    sub_cat = str(row.get("Metafield: custom.sub_category [single_line_text_field]", "")).strip().upper()

    # Extract the first number found in the size string (e.g., '42.5' from 'EU 42.5')
    try:
        numbers = re.findall(r"\d+\.?\d*", size_raw)
        size_num = float(numbers[0]) if numbers else None
    except:
        size_num = None

    # 2. OVERRIDE: Global One Size check (If the literal size is OS)
    if size_raw in ["OS", "ONE SIZE", "U", "UNI", "NS"]:
        return "ONE_SIZE"

    # 3. BELT LOGIC
    if "BELT" in sub_cat or "BELT" in category:
        # We already checked for 'OS' above, so we assume these are sized belts
        return "BELTS MEN'S CM" if gender == "MEN" else "BELTS WOMEN'S CM"

    # 4. MEN'S SHOE LOGIC (Range Based)
    if gender == "MEN" and ("SHOE" in category or "FOOTWEAR" in category or "SHOE" in sub_cat):
        if size_num is not None:
            # US Scale (3 - 14)
            if 3 <= size_num <= 14:
                return "SHOES US MEN"
            
            # JAPAN Scale (24 - 29)
            if 24 <= size_num <= 29:
                return "SHOES MEN’S JAPAN"
            
            # EU Scale (35 - 47)
            if 35 <= size_num <= 47:
                return "MEN SHOES EUROPE"
        
        # Fallback for Men's Shoes if no number is found
        return "MEN SHOES EUROPE"

    # 5. WOMEN'S SHOE LOGIC
    if gender == "WOMEN" and ("SHOE" in category or "FOOTWEAR" in category or "SHOE" in sub_cat):
        # We can add Women's US/EU ranges here later if needed
        return "WOMEN SHOES EUROPE"

    # 6. JEANS LOGIC
    if "JEANS" in sub_cat or "DENIM" in sub_cat:
        return "MEN'S JEANS" if gender == "MEN" else "WOMEN'S JEANS"

    # 7. CLOTHING LOGIC (Fallback to IT/FR for now)
    clothing_keywords = ["CLOTHING", "KNITWEAR", "OUTERWEAR", "READY TO WEAR"]
    if any(k in category for k in clothing_keywords) or any(k in sub_cat for k in clothing_keywords):
        return "CLOTHING MEN'S IT/FR" if gender == "MEN" else "CLOTHING WOMEN’S IT/FR"

    # 8. FINAL FALLBACK
    return "ONE_SIZE"


# === Execution ===

# For ide use
user_input = input("Enter file name (without .xlsx): ").strip()
file_name = f"{user_input}.xlsx"
if not os.path.exists(file_name): sys.exit(f"❌ File '{file_name}' not found.")
df = pd.read_excel(file_name)


validate_vendors(df)
validate_duplicate_skus(df)
validate_size_scale(df)

validate_cost_currency_format(df)

df = run_transformations(df)

validate_tags_and_type(df) 

columns_in_order = [
    "Command", "Title", "Vendor", "Type", "Tags", "Body HTML", "Status", "Published", "Option1 Name", "Option1 Value", 
    "Option2 Name", "Option2 Value", "Variant SKU", "Variant Barcode", "Variant Price", "Variant Compare At Price", "Variant Cost",
    "Inventory Available: Defective",
    "Inventory Available: Marais Men - QV", "Inventory Available: Marais Women - Bourke", "Inventory Available: Marais Women - QV",
    "Inventory Available: Photoshoot", "Inventory Available: Warehouse", "Variant Inventory Tracker", 
    "Metafield: my_fields.manufacture_code", "Metafield: my_fields.supplier_code [single_line_text_field]", 
    "Variant Metafield:custom.manufacture_code[single_line_text_field]", "Metafield: custom.brand_color_id [single_line_text_field]", 
    "Metafield: custom.product_season [single_line_text_field]", "Variant Metafield: custom.season [single_line_text_field]", 
    "Metafield: custom.gender [single_line_text_field]", "Metafield: custom.category [single_line_text_field]", 
    "Metafield: custom.sub_category [single_line_text_field]", "Metafield: custom.size_scale [single_line_text_field]", 
    "Metafield: custom.local_market_price [single_line_text_field]", "Metafield: custom.made_in [single_line_text_field]", 
    "Metafield: custom.new_sale [single_line_text_field]", "Variant Metafield: custom.new_sale [single_line_text_field]", 
    "FULLCODE", "Wholesale Price", "Variant Metafield: Variant.cost_price [single_line_text_field]",
    "Variant Metafield: Variant.gtin [single_line_text_field]", "Variant HS Code", "Metafield: title_tag"
]

for col in columns_in_order:
    if col not in df.columns: df[col] = ""

check_mandatory_empty_cells(df, columns_in_order)
total_errs = validate_data_and_log_errors(df)

output_name = f"{user_input}_ordered.xlsx"
df[columns_in_order].to_excel(output_name, index=False)
print(f"\n✨ Process Complete! Saved: {output_name}. Validation issues: {total_errs}")