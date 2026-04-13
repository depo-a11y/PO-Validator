import streamlit as st
import pandas as pd
import os
import re

st.set_page_config(page_title="Excel Validator", layout="wide")
st.title("📦 Excel Product Validator")

uploaded_file = st.file_uploader("Drag and drop your Excel file here", type=["xlsx"])

# ---------------- HELPERS ----------------

def get_excel_row(index):
    return index + 2


# (keep your existing functions EXACTLY as-is below this point)
# I trimmed repetitive ones for readability in chat,
# but in your real file you paste them unchanged:

# 👉 COPY ALL YOUR FUNCTIONS HERE:
# validate_vendors
# validate_size_scale
# validate_duplicate_skus
# validate_tags_and_type
# check_mandatory_empty_cells
# validate_data_and_log_errors
# validate_cost_currency_format
# run_transformations
# assign_size_scale

# ----------------------------------------
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
        "VEJA", "WALES BONNER", "SONG FOR THE MUTE", "Y-3"
    ]
    
    print("🏢 Validating Vendor names (Case-Sensitive)...")
    error_found = False
    for idx, row in df.iterrows():
        actual_vendor = str(row.get("Vendor", "")).strip()
        if actual_vendor not in approved_vendors:
            print(f"❌ INVALID VENDOR - Row {get_excel_row(idx)}: '{actual_vendor}'")
            error_found = True
            
    if error_found:
        st.error("\n🛑 Vendor casing or naming error. Please fix before proceeding.")
        st.stop()   
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
        st.error("\n🛑 Size Scale error detected. Process stopped.")
        st.stop()   
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
            st.error("\n🛑 Duplicate SKUs detected. Process stopped.")
            st.stop()  
    print("✅ SKU uniqueness check passed.")

def validate_tags_and_type(df, template_file="expected_tags.xlsx"):
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
            st.error(f"🛑 Error: {template_file} must have columns named 'Type' and 'Tags'.")
            st.stop()  

        tag_lookup = {str(r[type_col]).strip(): {t.strip() for t in str(r[tags_col]).split(',') if t.strip()} for _, r in template_df.iterrows()}

        for idx, row in df.iterrows():
            actual_type = str(row.get("Type", "")).strip()
            actual_tags = {t.strip() for t in str(row.get("Tags", "")).split(',') if t.strip()}
            
            if actual_type not in tag_lookup:
                print(f"❌ INVALID PRODUCT TYPE - Row {get_excel_row(idx)}: '{actual_type}'")
                st.error(f"❌ INVALID PRODUCT TYPE - Row {get_excel_row(idx)}: '{actual_type}'")
                st.stop()  
            
            required_tags = tag_lookup[actual_type]
            if not required_tags.issubset(actual_tags):
                print(f"❌ MISSING TAGS - Row {get_excel_row(idx)} | Type: '{actual_type}' | Missing: {list(required_tags - actual_tags)}")
                st.error(f"❌ MISSING TAGS - Row {get_excel_row(idx)} | Type: '{actual_type}' | Missing: {list(required_tags - actual_tags)}")
                st.stop()  
        print("✅ Tag/Type validation passed.")
    except Exception as e:
        st.error(f"❌ Fatal error reading {template_file}: {e}")
        st.stop() 

def check_mandatory_empty_cells(df, columns_to_check):
    """Exits if any mandatory cell is empty, excluding specific optional/auto-filled columns."""
    optional_cols = [
        "Metafield: custom.made_in [single_line_text_field]",
        "Variant Metafield: Variant.gtin [single_line_text_field]",
        "Variant HS Code",
        "Inventory Available: Defective", "Inventory Available: Marais Men Lux - Chadstone", 
        "Inventory Available: Marais Men - Chadstone", "Inventory Available: Marais Men - QV", 
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
    if error_found: 
        st.error("\n🛑 Execution stopped: Mandatory cells cannot be empty.")
        st.stop() 

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
        st.error("\n🛑 Cost currency format error. Please fix before proceeding.")
        st.stop() 
    print("✅ Cost currency format passed.")
def run_transformations(df):
    """Handles formatting, SKU/Season/Sale syncs, and Inventory auto-fill."""
    
    # Load Expected Tags Mapping
    template_file = "expected_tags.xlsx"
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
    inventory_cols = ["Inventory Available: Defective", "Inventory Available: Marais Men Lux - Chadstone", "Inventory Available: Marais Men - Chadstone", "Inventory Available: Marais Men - QV", "Inventory Available: Marais Women - Bourke", "Inventory Available: Marais Women - QV", "Inventory Available: Photoshoot", "Inventory Available: Warehouse"]
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

    # Local Price Calculation
    prices = df["Variant Price"].astype(str).str.replace(r'[^\d.]', '', regex=True)
    df["Metafield: custom.local_market_price [single_line_text_field]"] = (pd.to_numeric(prices, errors='coerce') * 1.15).round(2)

    df.rename(columns={"HS Code": "Variant HS Code"}, inplace=True)
    return df
if uploaded_file:

    df = pd.read_excel(uploaded_file)

    st.success("File loaded successfully ✅")
    st.write(f"Rows: {len(df)}")

    # ---------------- RUN PIPELINE ----------------

    if "Inventory Available: Marais Women - Chadstone" in df.columns:
        df.rename(
            columns={"Inventory Available: Marais Women - Chadstone":
                     "Inventory Available: Marais Men Lux - Chadstone"},
            inplace=True
        )

    with st.spinner("Running validations..."):

        validate_vendors(df)
        validate_duplicate_skus(df)
        # validate_size_scale(df)
        validate_cost_currency_format(df)

        df = run_transformations(df)

        validate_tags_and_type(df)

        columns_in_order = [
            "Command", "Title", "Vendor", "Type", "Tags", "Body HTML", "Status",
            "Published", "Option1 Name", "Option1 Value",
            "Option2 Name", "Option2 Value", "Variant SKU",
            "Variant Barcode", "Variant Price", "Variant Compare At Price",
            "Variant Cost"
        ]

        for col in columns_in_order:
            if col not in df.columns:
                df[col] = ""

        check_mandatory_empty_cells(df, columns_in_order)
        total_errs = validate_data_and_log_errors(df)

        output_name = "processed.xlsx"
        df[columns_in_order].to_excel(output_name, index=False)

    st.success("Processing complete 🎉")

    # ---------------- DOWNLOAD ----------------
    with open(output_name, "rb") as f:
        st.download_button(
            "⬇️ Download processed file",
            f,
            file_name=output_name
        )

    st.info(f"Validation issues found: {total_errs}")