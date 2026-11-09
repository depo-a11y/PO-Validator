"""Gate A logic shared by the Streamlit app (app.py) and the OC tool (oc/).

Lifted from app.py so there is one gate with two interfaces. The functions keep
their names, inputs and outputs; the only change is that a hard stop raises
ValidationStop instead of calling st.error() + st.stop(), and the caller decides
how to show it. app.py turns ValidationStop back into st.error + st.stop.

Known defects are kept as they were in app.py, on purpose (fix only if they block
a run): the title_tag length check reads "Metafield: title_tag" while the output
column is "Metafield: title_tag [string]", and generate_all_tags appends to the
shared lookup list.

split_t is main's b2a7c9e version, at module level so app.split_t (re-exported)
and test_sub_category.py reach it: sub_category is the whole remainder of Type
after gender and category.
"""
import os
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
EXPECTED_TAGS = "expected_tags.xlsx"

APPROVED_VENDORS = [
    "ALEXANDER MCQUEEN", "ALAIA", "AMI PARIS", "AMIRI", "AUTRY", "BALENCIAGA",
    "BALMAIN", "CASABLANCA", "NOIR KEI NINOMIYA", "CFCL", "CHLOE", "DIESEL",
    "DOUBLET", "DROLE DE MONSIEUR", "DSM KEI NINOMIYA", "ENTIRE STUDIOS", "EGONLAB",
    "FENG CHEN WANG", "FEAR OF GOD", "FEAR OF GOD ESSENTIALS", "GOSHA RUBCHINSKIY", "JIL SANDER", "JUUNJ",
    "KENZO", "KIDSUPER", "LOEWE", "LEMAIRE", "MAISON MARGIELA",
    "MAISON MIHARA YASUHIRO", "PALM ANGELS", "POST ARCHIVE FACTION",
    "REPRESENT", "REMAGINE", "RHUDE", "RICK OWENS", "RICK OWENS DRKSHDW", "SACAI", "STONE ISLAND", "TAION", "THUG CLUB",
    "VOWELS", "WE11DONE", "WILLY CHAVARRIA", "WOOYOUNGMI", "UNDERCOVER",
    "VEJA", "WALES BONNER", "SONG FOR THE MUTE", "Y-3", "WALD", "HOUSE OF ERRORS", "MM6 MAISON MARGIELA", "UGG", "HOKA", "PHANTACI"
]

APPROVED_SCALES = [
    "CLOTHING WOMEN’S IT/FR",
    "ONE_SIZE",
    "CLOTHING MEN'S STANDARD NUMERIC",
    "CLOTHING MEN'S STANDARD",
    "CLOTHING MEN'S IT/FR",
    "WOMEN'S JEANS",
    "WOMEN SHOES EUROPE",
    "MEN SHOES EUROPE",
    "CLOTHING WOMEN’S STANDARD",
    "MEN'S JEANS",
    "CLOTHING WOMEN'S STANDARD NUMERIC",
    "BELTS WOMEN'S CM",
    "BELTS MEN'S CM",
    "SHOES US MEN",
    "WOMEN SHOES US",
    "SHOES MEN’S JAPAN"
]

COLUMNS_IN_ORDER = [
    "Command", "Title", "Vendor", "Type", "Tags", "Body HTML", "Status", "Published", "Option1 Name", "Option1 Value",
    "Option2 Name", "Option2 Value", "Variant SKU", "Variant Barcode", "Variant Price", "Variant Compare At Price", "Variant Cost",
    "Inventory Available: Defective", "Inventory Available: Marais Men - Chadstone",
    "Inventory Available: Marais Men - QV", "Inventory Available: Marais Women - Bourke", "Inventory Available: Marais Women - QV",
    "Inventory Available: Photoshoot", "Inventory Available: Warehouse", "Variant Inventory Tracker",
    "Metafield: my_fields.manufacture_code", "Metafield: my_fields.supplier_code [single_line_text_field]",
    "Variant Metafield:custom.manufacture_code[single_line_text_field]", "Metafield: custom.brand_color_id [single_line_text_field]",
    "Metafield: custom.product_season [single_line_text_field]", "Variant Metafield: custom.season [single_line_text_field]",
    "Metafield: custom.gender [single_line_text_field]", "Metafield: custom.category [single_line_text_field]",
    "Metafield: custom.sub_category [single_line_text_field]", "Metafield: custom.size_scale [single_line_text_field]",
    "Metafield: custom.local_market_price [single_line_text_field]", "Metafield: custom.made_in [single_line_text_field]",
    "Metafield: custom.new_sale [single_line_text_field]", "Variant Metafield: custom.new_sale [single_line_text_field]", "Variant Metafield: Variant.cost_price [single_line_text_field]",
    "Variant Metafield: Variant.gtin [single_line_text_field]", "Variant HS Code", "Metafield: title_tag [string]"
]

OPTIONAL_COLS = [
    "Metafield: custom.made_in [single_line_text_field]",
    "Variant Metafield: Variant.gtin [single_line_text_field]",
    "Variant HS Code",
    "Inventory Available: Defective",
    "Inventory Available: Marais Men - Chadstone", "Inventory Available: Marais Men - QV",
    "Inventory Available: Marais Women - Bourke", "Inventory Available: Marais Women - QV",
    "Inventory Available: Photoshoot", "Inventory Available: Warehouse",
    "Variant Inventory Tracker", "Variant Metafield: Variant.cost_price [single_line_text_field]"
]

INVENTORY_COLS = ["Inventory Available: Defective", "Inventory Available: Marais Men - Chadstone", "Inventory Available: Marais Men - QV", "Inventory Available: Marais Women - Bourke", "Inventory Available: Marais Women - QV", "Inventory Available: Photoshoot", "Inventory Available: Warehouse"]


class ValidationStop(Exception):
    """A hard stop. `rows` optionally carries a table of offending rows."""

    def __init__(self, message, rows=None):
        super().__init__(message)
        self.rows = rows or []


def get_excel_row(index):
    """Converts pandas index to Excel row number (1-based, plus 1 for header)."""
    return index + 2


def _tags_path(template_file):
    return template_file if os.path.exists(template_file) else str(HERE / os.path.basename(template_file))


def validate_vendors(df):
    """Strictly validates Vendor names against the approved list (Case-Sensitive)."""
    print("🏢 Validating Vendor names (Case-Sensitive)...")
    bad = []
    for idx, row in df.iterrows():
        actual_vendor = str(row.get("Vendor", "")).strip()
        if actual_vendor not in APPROVED_VENDORS:
            print(f"❌ INVALID VENDOR - Row {get_excel_row(idx)}: '{actual_vendor}'")
            bad.append({"Row": get_excel_row(idx), "Vendor": actual_vendor})
    if bad:
        raise ValidationStop("\n🛑 Vendor casing or naming error. Please fix before proceeding.", bad)
    print("✅ Vendor validation passed.")


def validate_size_scale(df):
    """Validates Size Scale against the approved values (Case-Sensitive)."""
    col_name = "Metafield: custom.size_scale [single_line_text_field]"
    print("📏 Validating Size Scales...")
    bad = []
    for idx, row in df.iterrows():
        actual_scale = str(row.get(col_name, "")).strip()
        if actual_scale not in APPROVED_SCALES:
            bad.append({"Row": get_excel_row(idx), "Size scale": actual_scale})
    if bad:
        last = bad[-1]
        raise ValidationStop(f"❌ INVALID SIZE SCALE - Row {last['Row']}: '{last['Size scale']}' (Must be: {APPROVED_SCALES})", bad)
    print("✅ Size Scale validation passed.")


def validate_duplicate_skus(df):
    """Stops if duplicate Variant SKUs are found."""
    sku_col = "Variant SKU"
    if sku_col in df.columns:
        duplicates = df[df.duplicated(subset=[sku_col], keep=False)]
        if not duplicates.empty:
            print("\n👯 DUPLICATE SKUS FOUND:")
            bad = []
            for sku in duplicates[sku_col].unique():
                rows = [get_excel_row(i) for i in duplicates.index[duplicates[sku_col] == sku]]
                print(f"❌ SKU: '{sku}' appears on Rows: {rows}")
                bad.append({"SKU": sku, "Rows": rows})
            raise ValidationStop("\n🛑 Duplicate SKUs detected. Process stopped.", bad)
    print("✅ SKU uniqueness check passed.")


def load_tag_lookup(template_file=EXPECTED_TAGS):
    """{Type: [tags]} from expected_tags.xlsx, as run_transformations reads it."""
    path = _tags_path(template_file)
    if not os.path.exists(path):
        return {}
    temp_df = pd.read_excel(path)
    return {
        str(r.get('Type', r.get('Product Type'))).strip():
        [t.strip() for t in str(r.get('Tags', '')).split(',') if t.strip()]
        for _, r in temp_df.iterrows()
    }


def validate_tags_and_type(df, template_file=EXPECTED_TAGS):
    """Strictly validates Product Type and Tags against a template file."""
    path = _tags_path(template_file)
    if not os.path.exists(path):
        print(f"⚠️ Warning: '{template_file}' not found. Skipping tag validation.")
        return

    print("🏷️  Validating Product Types and Tags...")
    try:
        template_df = pd.read_excel(path)
        template_df.columns = [str(c).strip() for c in template_df.columns]
        type_col = 'Type' if 'Type' in template_df.columns else 'Product Type'
        tags_col = 'Tags'

        if type_col not in template_df.columns or tags_col not in template_df.columns:
            raise ValidationStop(f"🛑 Error: {template_file} must have columns named 'Type' and 'Tags'.")

        tag_lookup = {str(r[type_col]).strip(): {t.strip() for t in str(r[tags_col]).split(',') if t.strip()} for _, r in template_df.iterrows()}

        for idx, row in df.iterrows():
            actual_type = str(row.get("Type", "")).strip()
            actual_tags = {t.strip() for t in str(row.get("Tags", "")).split(',') if t.strip()}

            if actual_type not in tag_lookup:
                print(f"❌ INVALID PRODUCT TYPE - Row {get_excel_row(idx)}: '{actual_type}'")
                raise ValidationStop(f"❌ INVALID PRODUCT TYPE - Row {get_excel_row(idx)}: '{actual_type}'")

            required_tags = tag_lookup[actual_type]
            if not required_tags.issubset(actual_tags):
                msg = f"❌ MISSING TAGS - Row {get_excel_row(idx)} | Type: '{actual_type}' | Missing: {list(required_tags - actual_tags)}"
                print(msg)
                raise ValidationStop(msg)
        print("✅ Tag/Type validation passed.")
    except ValidationStop:
        raise
    except Exception as e:
        raise ValidationStop(f"❌ Fatal error reading {template_file}: {e}")


def find_mandatory_empty_cells(df, columns_to_check):
    """[{Row, Column}] for every empty mandatory cell (optional columns skipped)."""
    errors = []
    for col in columns_to_check:
        if col in OPTIONAL_COLS:
            continue
        missing_mask = df[col].astype(str).str.strip().eq("") | df[col].isna()
        if missing_mask.any():
            for idx in df.index[missing_mask]:
                errors.append({"Row": get_excel_row(idx), "Column": col})
    return errors


def check_mandatory_empty_cells(df, columns_to_check):
    errors = find_mandatory_empty_cells(df, columns_to_check)
    if errors:
        raise ValidationStop(f"🛑 Found {len(errors)} empty mandatory cells", errors)


def margin_threshold(vendor):
    vendor_lower = str(vendor).strip().lower()
    if vendor_lower in ["veja", "taion", "trudon", "creed", "dior", "represent"]:
        return 2.19
    if vendor_lower == "maison margiela":
        return 2.25
    return 2.5


def validate_data_and_log_errors(df, report_path="VALIDATION_ERRORS_REPORT.xlsx"):
    """Flags margin errors and title tag length issues."""
    errors = []
    print("💰 Checking Price Margins and SEO Title lengths...")
    for idx, row in df.iterrows():
        try:
            price = float(str(row.get("Variant Price", 0)).replace(',', '').replace('$', ''))
            cost = float(str(row.get("Variant Cost", 0)).replace(',', '').replace('$', ''))
            if cost > 0:
                threshold = margin_threshold(row.get("Vendor", ""))
                if (price / cost) < threshold:
                    errors.append({"Row": get_excel_row(idx), "SKU": row.get("Variant SKU"), "Type": "LOW MARGIN", "Details": f"{round(price/cost, 2)}x"})
        except:
            pass

        title_tag = str(row.get("Metafield: title_tag", ""))
        if len(title_tag) > 60:
            errors.append({"Row": get_excel_row(idx), "SKU": row.get("Variant SKU"), "Type": "SEO TITLE TOO LONG", "Details": f"{len(title_tag)} chars"})

    if errors and report_path:
        pd.DataFrame(errors).to_excel(report_path, index=False)
    return errors


def validate_cost_currency_format(df):
    """Ensures Variant Cost Metafield is in format 'CURRENCY [space] VALUE' (e.g., EUR 150)."""
    col_name = "Variant Metafield: Variant.cost_price [single_line_text_field]"
    if col_name not in df.columns:
        return

    print("💶 Validating Cost Currency format...")
    pattern = r'^[A-Z]{3}\s\d+(\.\d{1,2})?$'
    bad = []
    for idx, row in df.iterrows():
        val = str(row.get(col_name, "")).strip()
        if val == "nan" or val == "":
            continue
        if not re.match(pattern, val):
            print(f"❌ INVALID COST FORMAT - Row {get_excel_row(idx)}: '{val}' (Expected format: 'EUR 123' or 'USD 123.45')")
            bad.append({"Row": get_excel_row(idx), "Value": val})
    if bad:
        raise ValidationStop("\n🛑 Cost currency format error. Please fix before proceeding.", bad)
    print("✅ Cost currency format passed.")


def assign_size_scale(row):
    """
    Automatically assigns size scales based on Gender, Category,
    and the numerical range of the Size (Option2 Value).
    """
    size_raw = str(row.get("Option2 Value", "")).strip().upper()
    gender = str(row.get("Metafield: custom.gender [single_line_text_field]", "")).strip().upper()
    category = str(row.get("Metafield: custom.category [single_line_text_field]", "")).strip().upper()
    sub_cat = str(row.get("Metafield: custom.sub_category [single_line_text_field]", "")).strip().upper()

    try:
        numbers = re.findall(r"\d+\.?\d*", size_raw)
        size_num = float(numbers[0]) if numbers else None
    except:
        size_num = None

    alpha_sizes = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "3XL", "4XL"]
    is_alpha = any(a in size_raw for a in alpha_sizes)

    if size_raw in ["OS", "ONE SIZE", "U", "UNI", "NS"]:
        return "ONE_SIZE"

    if "BELT" in sub_cat or "BELT" in category:
        return "BELTS MEN'S CM" if gender == "MEN" else "BELTS WOMEN'S CM"

    if gender == "MEN" and ("SHOE" in category or "FOOTWEAR" in category or "SHOE" in sub_cat):
        if size_num is not None:
            if 3 <= size_num <= 14:
                return "SHOES US MEN"
            if 24 <= size_num <= 29:
                return "SHOES MEN’S JAPAN"
            if 35 <= size_num <= 47:
                return "MEN SHOES EUROPE"
        return "MEN SHOES EUROPE"

    if gender == "WOMEN" and ("SHOE" in category or "FOOTWEAR" in category or "SHOE" in sub_cat):
        if size_num is not None:
            if 34 <= size_num <= 42:
                return "WOMEN SHOES EUROPE"
            if 4 <= size_num <= 12:
                return "WOMEN SHOES US"
        return "WOMEN SHOES EUROPE"

    if "JEANS" in sub_cat or "DENIM" in sub_cat:
        return "MEN'S JEANS" if gender == "MEN" else "WOMEN'S JEANS"

    clothing_keywords = ["CLOTHING", "KNITWEAR", "OUTERWEAR", "READY TO WEAR"]
    if any(k in category for k in clothing_keywords) or any(k in sub_cat for k in clothing_keywords):
        if is_alpha:
            return "CLOTHING MEN'S STANDARD" if gender == "MEN" else "CLOTHING WOMEN’S STANDARD"
        if size_num is not None:
            if gender == "WOMEN" and 32 <= size_num <= 50:
                return "CLOTHING WOMEN’S IT/FR"
            if gender == "MEN" and 44 <= size_num <= 60:
                return "CLOTHING MEN'S IT/FR"
            if 26 <= size_num <= 40:
                return "MEN'S JEANS" if gender == "MEN" else "WOMEN'S JEANS"
        return "CLOTHING MEN'S STANDARD" if gender == "MEN" else "CLOTHING WOMEN’S STANDARD"

    return "ONE_SIZE"


def extract_brand_color_id(x):
    x = str(x).strip()
    parts = x.split()
    if len(parts) < 2:
        raise ValueError(f"Invalid manufacture_code format: '{x}' (expected at least 2 parts)")
    return parts[-1]


def split_t(val):
    p = str(val).split()
    return pd.Series([p[0], p[1], " ".join(p[2:])]) if len(p) >= 3 else pd.Series(["", "", ""])


def run_transformations(df, template_file=EXPECTED_TAGS):
    """Handles formatting, SKU/Season/Sale syncs, and Inventory auto-fill."""
    tag_lookup = load_tag_lookup(template_file)

    def generate_all_tags(row):
        product_type = str(row.get("Type", "")).strip()
        vendor = str(row.get("Vendor", "")).strip()
        season = str(row.get("Metafield: custom.product_season [single_line_text_field]", "")).strip()
        final_tags = tag_lookup.get(product_type, [])
        if vendor: final_tags.append(vendor)
        if season: final_tags.append(season)
        return ", ".join(list(dict.fromkeys(final_tags)))

    df["Tags"] = df.apply(generate_all_tags, axis=1)

    df[["Metafield: custom.gender [single_line_text_field]", "Metafield: custom.category [single_line_text_field]", "Metafield: custom.sub_category [single_line_text_field]"]] = df["Type"].apply(split_t)

    df["Metafield: custom.size_scale [single_line_text_field]"] = df.apply(assign_size_scale, axis=1)

    for col in INVENTORY_COLS:
        df[col] = 0
    df["Variant Inventory Tracker"] = "shopify"

    m_code = "Metafield: my_fields.manufacture_code"
    if m_code in df.columns:
        df["Metafield: my_fields.supplier_code [single_line_text_field]"] = df[m_code]
        df["Variant Metafield:custom.manufacture_code[single_line_text_field]"] = df[m_code]
    try:
        df["Metafield: custom.brand_color_id [single_line_text_field]"] = df[m_code].apply(extract_brand_color_id)
    except ValueError as e:
        raise ValidationStop(f"🛑 Brand Color ID Error: {e}")

    df["Variant Metafield: custom.season [single_line_text_field]"] = df.get("Metafield: custom.product_season [single_line_text_field]", "")
    df["Variant Metafield: custom.new_sale [single_line_text_field]"] = df.get("Metafield: custom.new_sale [single_line_text_field]", "")
    df["Variant Barcode"] = df.get("Variant SKU", "")

    if m_code in df.columns and "Option2 Value" in df.columns:
        df["FULLCODE"] = (df[m_code].astype(str) + df["Option2 Value"].astype(str)).str.replace(" ", "", regex=False)

    _lmp_price = pd.to_numeric(df["Variant Price"].astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce')
    _lmp_compare = pd.to_numeric(df.get("Variant Compare At Price", pd.Series(index=df.index, dtype="object")).astype(str).str.replace(r'[^\d.]', '', regex=True), errors='coerce')
    _lmp_base = _lmp_compare.where(_lmp_compare.notna() & (_lmp_compare > 0), _lmp_price)
    df["Metafield: custom.local_market_price [single_line_text_field]"] = (_lmp_base * 1.35).round(2)

    df.rename(columns={"HS Code": "Variant HS Code"}, inplace=True)
    return df


def run_gate(df, template_file=EXPECTED_TAGS, report_path=None, typo_check=True):
    """The whole Gate A in app.py's order, for callers with no UI.

    Returns (processed_df, errors, typo_rows). Raises ValidationStop on a hard
    stop. `errors` is the margin/title list app.py turns into
    VALIDATION_ERRORS_REPORT.xlsx; a non-empty list means the sheet does not pass.
    """
    validate_vendors(df)
    validate_duplicate_skus(df)
    validate_cost_currency_format(df)
    df = run_transformations(df, template_file)
    validate_size_scale(df)
    validate_tags_and_type(df, template_file)
    for col in COLUMNS_IN_ORDER:
        if col not in df.columns:
            df[col] = ""
    check_mandatory_empty_cells(df, COLUMNS_IN_ORDER)
    typo_rows = []
    if typo_check:
        from typo_check import validate_typos
        try:
            typo_rows = validate_typos(df)
        except FileNotFoundError:
            typo_rows = []
    errors = validate_data_and_log_errors(df, report_path)
    return df, errors, typo_rows


# ---- carryover (MERGE) path, from builders/co_stock_create_po.py --------------

CO_STOCK_COLUMNS = [
    "Tags Command", "ID", "Handle", "Command", "Title", "Vendor", "Type", "Tags",
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


def invalid_merge_commands(df):
    """Command values other than MERGE (the co_stock gate). [] means it passes."""
    if "Command" not in df.columns:
        return None
    bad = df.loc[~df["Command"].fillna("").astype(str).str.upper().eq("MERGE"), "Command"]
    return list(bad.unique())
