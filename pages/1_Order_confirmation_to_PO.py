"""Order confirmation (OC) PDF -> draft PO -> upload and delivery files, for a buyer with no command line.

The work is done by oc/service.py; this file is only the page around it. Uploads are
kept in st.session_state so Step 2 reuses the brand and catalog from Step 1, and are
written to a temporary folder only while a step runs.
"""
import io
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oc import brand as brandm, service  # noqa: E402

st.set_page_config(page_title="Order confirmation to PO", layout="wide")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FILL = {"FAIL": "#FFC7CE", "WARN": "#FFEB9C", "PASS": "#C6EFCE"}
ORDER = {"FAIL": 0, "WARN": 1, "PASS": 2, "INFO": 3}
UPLOAD = "Upload a brand .yaml file"
S = st.session_state


# ---- helpers -------------------------------------------------------------------
def secret_brands():
    """(brands this viewer may use, why the saved brands are hidden, whether sign-in is set up)."""
    try:
        table, viewers, can_login = st.secrets.get("oc_brands"), st.secrets.get("oc_viewers"), "auth" in st.secrets
    except Exception:
        return {}, None, False
    email = st.user.get("email")
    brands, why = service.secret_brands_for(table, viewers, email)
    return brands, why, can_login and not email


def brand_options(saved):
    opts = {}
    for name, value in saved.items():
        opts[f"{name} (saved in the app)"] = ("secret", name, value)
    for name, path in brandm.local_configs().items():
        opts[f"{name} (on this computer)"] = ("file", name, path)
    opts[UPLOAD] = ("upload", None, None)
    return opts


def load_brand(kind, name, value):
    if kind == "secret":
        source = f"app secrets, [oc_brands] {name}"
        if isinstance(value, str):
            return brandm.from_text(value, source, name)
        return brandm.from_mapping(value, source, name)
    if kind == "file":
        return brandm.load(str(value))
    up_name, data = value
    return brandm.from_text(data.decode("utf-8"), f"uploaded file {up_name}", up_name.split(".")[0])


def save(folder, name, data):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(path):
        n += 1
        path = f"{stem} ({n}){ext}"
    Path(path).write_bytes(data)
    return path


def safe_name(name):
    return re.sub(r"[/\\\x00]", "_", os.path.basename(name)) or "file"


def checks_table(checks):
    df = pd.DataFrame(checks, columns=["Check", "Result", "Detail"])
    df["Detail"] = df["Detail"].astype(str)
    df = df.iloc[sorted(range(len(df)), key=lambda i: ORDER.get(df["Result"].iat[i], 9))]
    styled = df.style.map(lambda v: f"background-color: {FILL[v]}; color: #000" if v in FILL else "",
                          subset=["Result"])
    st.dataframe(styled, hide_index=True, width="stretch",
                 column_config={"Detail": st.column_config.TextColumn(width="large")})


def show_fails(checks):
    for c in service.fails(checks):
        st.error(f"**{c['Check']}**: {c['Detail']}")


def rows_label(rows):
    rows = sorted(set(int(r) for r in rows))
    out, start = [], rows[0]
    for a, b in zip(rows, rows[1:] + [None]):
        if b != a + 1:
            out.append(str(start) if start == a else f"{start}-{a}")
            start = b
    return ", ".join(out)


def _text(v):
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)


def review_groups(review):
    """The Review sheet in three tables: red cells and amber cells that rest on evidence, one line per
    product, column and reason; and amber cells a house rule or the brand config filled, one line per
    column and reason (thousands of cells such as Vendor or Status on every row)."""
    df = review[review["Colour"].isin(["red", "amber"])].copy()
    df["Value"] = df["Value"].map(_text)
    df["Code"] = df["Code"].map(_text)
    rule = (df["Colour"] == "amber") & (df["Source"] == "rule")
    keys = ["Colour", "Code", "Column", "Value", "Why"]
    g = df[~rule].groupby(keys, sort=False)["Row"].agg(rows_label).reset_index().rename(columns={"Row": "PO rows"})
    g = g[["PO rows", "Code", "Column", "Value", "Why", "Colour"]]
    r = df[rule].groupby(["Column", "Why"], sort=False).agg(
        Rows=("Row", "size"), Values=("Value", lambda v: ", ".join(dict.fromkeys(v))[:120])).reset_index()
    return g[g["Colour"] == "red"].drop(columns="Colour"), g[g["Colour"] == "amber"].drop(columns="Colour"), r


def and_join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


GROUP_COLS = {"Why": st.column_config.TextColumn(width="large"), "Value": st.column_config.TextColumn(width="medium")}


# ---- page ----------------------------------------------------------------------
st.title("Order confirmation to PO")
st.write(
    "Turns a brand's order confirmation (OC) into a draft PO. You check the draft in Excel, then this page "
    "turns the checked PO into the product upload files and the delivery file. Nothing on this page changes "
    "Shopify: it only makes files."
)

st.header("Brand and catalog")
col1, col2 = st.columns(2)
cfg = None
with col1:
    saved, hidden_why, offer_login = secret_brands()
    opts = brand_options(saved)
    labels = list(opts)
    choice = st.selectbox("Brand", labels, index=labels.index(S["brand_choice"]) if S.get("brand_choice") in labels else 0,
                          help="The brand config holds the season, SKU prefix, cost rule and past POs.")
    S["brand_choice"] = choice
    kind, name, value = opts[choice]
    if kind == "upload":
        up = st.file_uploader("Brand config (.yaml)", type=["yaml", "yml"], key="w_brand")
        if up is not None:
            S["brand_upload"] = (up.name, up.getvalue())
        elif S.get("brand_upload"):
            st.caption(f"Using {S['brand_upload'][0]}, uploaded earlier. Upload another to replace it.")
        value = S.get("brand_upload")
    if value is not None:
        try:
            cfg = load_brand(kind, name, value)
        except brandm.BrandError as e:
            st.error(f"This brand config cannot be used: {e}")
        except UnicodeDecodeError:
            st.error("This brand config is not a text (.yaml) file.")
    if cfg:
        st.caption(f"Brand config: {cfg['_path']}. {cfg['vendor']}, season {cfg['season']}.")
    if hidden_why:
        st.caption(f"Brands saved in the app are hidden: {hidden_why}. You can still upload a brand .yaml file.")
        if offer_login and st.button("Sign in to use the saved brands"):
            st.login()
with col2:
    up = st.file_uploader("Catalog export (Matrixify Products CSV)", type=["csv"], key="w_catalog",
                          help="The brand's products from Matrixify: all statuses, with variants and metafields.")
    if up is not None:
        S["catalog"] = (up.name, up.getvalue())
    elif S.get("catalog"):
        st.caption(f"Using {S['catalog'][0]}, uploaded earlier. Upload another to replace it.")

# ---- step 1 --------------------------------------------------------------------
st.header("Step 1. Build the draft PO")
oc_up = st.file_uploader("Order confirmation (PDF)", type=["pdf"], key="w_oc")
can_cart = service.cart_supported()
cart_up = st.file_uploader(
    "Linesheet/cart export (optional, improves titles and colours)", type=["xlsx"], key="w_cart",
    disabled=not can_cart, help=None if can_cart else "This version cannot read a linesheet yet.")
hist_ups = st.file_uploader(
    "Past season POs (optional, used for retail prices)", type=["xlsx"], accept_multiple_files=True, key="w_hist",
    help="The brand's PO buy sheets from past seasons. If you add any, they replace the list in the brand config.")
if cfg:
    if hist_ups:
        st.caption(f"{len(hist_ups)} past season PO(s) uploaded. They replace the brand config's list.")
    else:
        note = service.plan_history(cfg)[1]
        (st.warning if note["Result"] == "WARN" else st.caption)(f"Past season POs: {note['Detail'].rstrip('.')}.")

need = [n for n, ok in (("a brand", cfg), ("the catalog export", S.get("catalog")), ("the order confirmation", oc_up))
        if not ok]
if need:
    st.caption("To build the draft, add " + and_join(need) + ".")
if st.button("Build draft PO", type="primary", disabled=bool(need)):
    S.pop("draft", None)
    with st.spinner("Reading the order confirmation and building the draft. This can take a minute."):
        try:
            with tempfile.TemporaryDirectory(prefix="oc-page-") as tmp:
                hist_paths = [save(os.path.join(tmp, "history"), safe_name(f.name), f.getvalue())
                              for f in hist_ups or []]
                po, review, checks = service.draft(
                    save(tmp, "oc.pdf", oc_up.getvalue()), save(tmp, "catalog.csv", S["catalog"][1]), cfg,
                    cart_path=save(tmp, "cart.xlsx", cart_up.getvalue()) if cart_up else None,
                    history_paths=hist_paths or None)
            S["draft"] = dict(po=po, review=review, checks=checks, file=service.file_prefix(cfg) + "PO-draft.xlsx",
                              made_from=f"{oc_up.name} and {S['catalog'][0]}, brand config {cfg['_path']}")
        except Exception as e:
            st.error("Something went wrong building the draft. Send Colwyn the message below.")
            st.exception(e)

if S.get("draft"):
    d = S["draft"]
    st.caption(f"Draft built from {d['made_from']}.")
    if d["po"] is None:
        st.error("No draft was made. These problems are in the inputs, not in the sheet, so editing a draft "
                 "would not fix them. Check you picked the right brand and files. If you did, send Colwyn "
                 "the checks below.")
        show_fails(d["checks"])
    else:
        red_df, amber_df, rule_df = review_groups(d["review"])
        n_red, n_amber = len(red_df), len(amber_df)
        st.download_button("Download the draft PO (.xlsx)", d["po"], file_name=d["file"], mime=XLSX,
                           type="primary", on_click="ignore")
        st.info(f"Next: open the draft in Excel, fix every red cell ({n_red} values, listed below), check every "
                f"amber cell ({n_amber} values below, plus the house rules), save it, then upload it in Step 2. "
                "The Review sheet in the file says where each value came from.")
        fixable = service.fails(d["checks"])
        if fixable:
            st.warning("Step 2 will refuse the PO until these are also fixed in the sheet:")
            show_fails(d["checks"])
        red, amber, rules, everything = st.tabs([
            f"Red: a guess, fix it ({n_red})", f"Amber: check it ({n_amber})",
            f"Amber: filled by a house rule ({int(rule_df['Rows'].sum()) if len(rule_df) else 0} cells)",
            "All checks"])
        with red:
            st.dataframe(red_df, hide_index=True, width="stretch", column_config=GROUP_COLS)
        with amber:
            st.dataframe(amber_df, hide_index=True, width="stretch", column_config=GROUP_COLS)
        with rules:
            st.caption("Values the brand config or a house convention sets, such as Vendor, Status and cost. "
                       "Glance down the list once rather than cell by cell.")
            st.dataframe(rule_df, hide_index=True, width="stretch", column_config=GROUP_COLS)
        with everything:
            checks_table(d["checks"])
    if d["po"] is None:
        checks_table(d["checks"])

# ---- step 2 --------------------------------------------------------------------
st.header("Step 2. Make the upload and delivery files")
st.write("Upload the PO you checked and saved. Every check has to pass. If any check fails you get no files "
         "at all, so a half-made set can never be imported.")
po_up = st.file_uploader("Checked PO (.xlsx)", type=["xlsx"], key="w_po")
need2 = [n for n, ok in (("a brand", cfg), ("the catalog export", S.get("catalog")), ("the checked PO", po_up))
         if not ok]
if need2:
    st.caption("To make the files, add " + and_join(need2) + ".")
if st.button("Make upload files", type="primary", disabled=bool(need2)):
    S.pop("final", None)
    with st.spinner("Checking the PO and making the files."):
        try:
            when = service.now()
            with tempfile.TemporaryDirectory(prefix="oc-page-") as tmp:
                zbytes, checks, errors = service.finalize(
                    save(tmp, safe_name(po_up.name), po_up.getvalue()), save(tmp, "catalog.csv", S["catalog"][1]), cfg,
                    when=when)
            prefix = service.file_prefix(cfg, when)
            S["final"] = dict(zip=zbytes, checks=checks, errors=errors, file=service.zip_name(prefix),
                              run=prefix.rstrip("-"),
                              made_from=f"{po_up.name} and {S['catalog'][0]}, brand config {cfg['_path']}")
        except Exception as e:
            st.error("Something went wrong making the files. Send Colwyn the message below.")
            st.exception(e)

if S.get("final"):
    f = S["final"]
    st.caption(f"Checked {f['made_from']}.")
    if f["zip"] is None:
        st.error(f"No files were made: {len(service.fails(f['checks']))} check(s) failed. Fix these in the PO, "
                 "save it and upload it again.")
        show_fails(f["checks"])
        if not f["errors"].empty:
            st.write("Rows the po-validator gate rejected. Row numbers are rows of PU-new, which holds only the PO's "
                     "NEW rows, so find them by SKU.")
            st.dataframe(f["errors"], hide_index=True, width="stretch")
    else:
        names = zipfile.ZipFile(io.BytesIO(f["zip"])).namelist()
        st.success("No check failed.")
        st.download_button("Download the upload and delivery files (.zip)", f["zip"], file_name=f["file"],
                           mime="application/zip", type="primary", on_click="ignore")
        st.write("Inside: " + ", ".join(names) + ".")
        st.info("Nothing has been imported. Importing creates and changes products in the store, so check with "
                "Colwyn before you import. PU-new creates the new products. PU-merge changes three things on each "
                "re-bought product: its season, its season tag and the cost of every size (to this order's cost). "
                "PU-merge-new-sizes adds the ordered sizes a product lacks, at that cost. The order is PU-new, then "
                "PU-merge, then PU-merge-new-sizes. Keep PU-merge-ROLLBACK: it puts the season, the tag and the old "
                "costs back. "
                "Use the PU-merge-ROLLBACK from the same zip as the PU-merge you import, made before importing. "
                f"Every file in this zip carries run id {f['run']}. If you "
                "change the PO and make the files again, import the new zip's PU-merge and keep the new zip's "
                "ROLLBACK, because an older ROLLBACK misses whatever the change added. Never use a ROLLBACK made "
                "after importing PU-merge: it is built from a catalog that already holds the new values, so it puts "
                "nothing back. Read the amber checks below first.")
    checks_table(f["checks"])
