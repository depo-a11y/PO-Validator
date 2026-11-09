import streamlit as st
import pandas as pd
import os
import re
import io

from typo_check import validate_typos

st.set_page_config(page_title="Excel Validator", layout="wide")
st.title("📦 Excel Product Validator")

uploaded_file = st.file_uploader("Drag and drop your Excel file here", type=["xlsx"])

# ---------------- GATE ----------------
# The validation and enrichment logic lives in validator_core.py so the OC tool
# (oc/) runs the same gate. This file only adds the Streamlit UI around it.

import validator_core as core
from validator_core import (
    ValidationStop,
    get_excel_row,
    assign_size_scale,
    extract_brand_color_id,
    split_t,
    run_transformations as _run_transformations,
)

columns_in_order = core.COLUMNS_IN_ORDER


def _stop(e):
    st.error(str(e))
    if e.rows:
        st.dataframe(pd.DataFrame(e.rows))
    st.stop()


def _gated(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValidationStop as e:
            _stop(e)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


validate_vendors = _gated(core.validate_vendors)
validate_size_scale = _gated(core.validate_size_scale)
validate_duplicate_skus = _gated(core.validate_duplicate_skus)
validate_tags_and_type = _gated(core.validate_tags_and_type)
validate_cost_currency_format = _gated(core.validate_cost_currency_format)
validate_data_and_log_errors = core.validate_data_and_log_errors
run_transformations = _gated(_run_transformations)


# Check product copy for misspellings (advisory: never stops the import)
def report_typos(df):
    """Surfaces likely typos in Title, Body HTML, colourway and SEO title.

    Deliberately does not call st.stop(). A wrongly flagged colour name must
    not block a PO import, so this warns and lets the buyer decide.
    """
    print("📝 Checking product copy for typos...")
    try:
        rows = validate_typos(df)
    except FileNotFoundError as e:
        st.warning(f"⚠️ Typo check skipped: {e}")
        print(f"WARNING: typo check skipped: {e}")
        return []

    if not rows:
        print("✅ No typos detected.")
        st.success("✅ No typos detected in product copy.")
        return []

    for r in rows:
        print(f"📝 POSSIBLE TYPO - Row {r['Row']} | {r['Column']}: {r['Details']}")
    st.warning(
        f"📝 {len(rows)} possible typo(s) in product copy. "
        "Review these before importing, then either fix the sheet or add the "
        "word to typo_allowlist.txt."
    )
    st.dataframe(pd.DataFrame(rows)[["Row", "Column", "Word", "Suggestion", "SKU"]],
                 use_container_width=True)
    return rows


# Check critical cells are not empty
def check_mandatory_empty_cells(df, columns_to_check):
    st.write("🔍 Scanning for empty cells...")
    errors = core.find_mandatory_empty_cells(df, columns_to_check)
    if errors:
        st.error(f"🛑 Found {len(errors)} empty mandatory cells")
        st.dataframe(pd.DataFrame(errors))
        st.stop()
    else:
        st.success("✅ No empty mandatory cells found")


if uploaded_file:

    df = pd.read_excel(uploaded_file)

    st.success("File loaded successfully ✅")
    st.write(f"Rows: {len(df)}")

    # ---------------- RUN PIPELINE ----------------

    with st.spinner("Running validations..."):

        validate_vendors(df)
        validate_duplicate_skus(df)
        
        validate_cost_currency_format(df)

        df = run_transformations(df)
        validate_size_scale(df)
        validate_tags_and_type(df)


        for col in columns_in_order:
            if col not in df.columns:
                df[col] = ""

        check_mandatory_empty_cells(df, columns_in_order)
        report_typos(df)
        errors = validate_data_and_log_errors(df)
        if errors:
            output = io.BytesIO()
            pd.DataFrame(errors).to_excel(output, index=False)
            output.seek(0)

            st.download_button(
                label="⬇️ Download Error Report",
                data=output,
                file_name="VALIDATION_ERRORS_REPORT.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            st.stop()

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
    total_errs=len(errors)
    st.info(f"Validation issues found: {total_errs}")
    if errors:
        output = io.BytesIO()
        pd.DataFrame(errors).to_excel(output, index=False)
        output.seek(0)

        st.download_button(
            label="⬇️ Download Error Report",
            data=output,
            file_name="VALIDATION_ERRORS_REPORT.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        st.stop()
