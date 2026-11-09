"""Reading and writing the house PO buy sheet, plus its Review and Checks sheets."""
import re

import openpyxl
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import PatternFill, Font
from openpyxl.utils import get_column_letter

from .resolve import COLOUR

# Header of Maison Margiela_SS27_PO.xlsx, including its one unheaded column
# (between Variant Compare At Price and Variant Cost). None = unheaded.
PO_HEADER = [
    "Tags Command", "ID", "Handle", "Command", "Title", "Vendor", "Type", "Body HTML", "Status",
    "Published", "Option1 Name", "Option1 Value", "Option2 Name", "Option2 Value", "Variant SKU",
    "Variant Price", "Variant Compare At Price", None, "Variant Cost",
    "Metafield: my_fields.manufacture_code",
    "Metafield: custom.product_season [single_line_text_field]",
    "Metafield: custom.made_in [single_line_text_field]",
    "Metafield: custom.new_sale [single_line_text_field]",
    "Variant Metafield: Variant.cost_price [single_line_text_field]",
    "Wholesale Price", "Total Order", "Order Qty",
    "Variant Metafield: Variant.gtin [single_line_text_field]", "Variant Metafield: HS Code",
    "Metafield: title_tag [string]",
]

AUD_FMT = '_("$"* #,##0.00_);_("$"* \\(#,##0.00\\);_("$"* "-"??_);_(@_)'
EUR_FMT = '_([$€-2]\\ * #,##0.00_);_([$€-2]\\ * \\(#,##0.00\\);_([$€-2]\\ * "-"??_);_(@_)'
AUD_COLS = {"Variant Price", "Variant Compare At Price", "Variant Cost"}
EUR_COLS = {"Wholesale Price", "Total Order"}

FILLS = {"green": PatternFill("solid", fgColor="C6EFCE"),
         "amber": PatternFill("solid", fgColor="FFEB9C"),
         "red": PatternFill("solid", fgColor="FFC7CE"),
         "changed": PatternFill("solid", fgColor="BDD7EE")}

CODE = "Metafield: my_fields.manufacture_code"
MADE_IN = "Metafield: custom.made_in [single_line_text_field]"
SEASON = "Metafield: custom.product_season [single_line_text_field]"
NEW_SALE = "Metafield: custom.new_sale [single_line_text_field]"
COST_PRICE = "Variant Metafield: Variant.cost_price [single_line_text_field]"
TITLE_TAG = "Metafield: title_tag [string]"


# openpyxl saves any string starting '=' as a formula, and Excel treats these leads as one.
FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
PO_FORMULA = re.compile(r"=\$?[A-Z]{1,3}\$?\d+")   # the one formula shape a PO carries (resolve_formula)


def clean_text(v):
    """A string minus the control characters openpyxl refuses; anything else unchanged."""
    return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v


def as_text(cell):
    """Save a string that starts like a formula as text. Not with a leading
    apostrophe: openpyxl keeps it in the value, Excel shows it and Matrixify would
    import it. data_type 's' keeps the visible text exactly as it was."""
    if isinstance(cell.value, str) and cell.value.startswith(FORMULA_LEAD):
        cell.data_type = "s"
    return cell


def append_text(ws, values, keep_formula=None):
    """ws.append for data: every string is written as text, except a value
    keep_formula(value) accepts (a formula the sheet means to carry)."""
    ws.append([clean_text(v) for v in values])
    for c in ws[ws.max_row]:
        if not (keep_formula and isinstance(c.value, str) and keep_formula(c.value)):
            as_text(c)


def write_frame(path, frame, sheet_name="Sheet1"):
    """DataFrame.to_excel(path, index=False, sheet_name=...) without the formula
    hole: pandas hands strings to openpyxl, which saves '=...' as a formula.
    Blank (NaN/None/NA) cells stay empty, as with to_excel."""
    import pandas as pd
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    append_text(ws, [str(c) for c in frame.columns])
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in frame.itertuples(index=False, name=None):
        append_text(ws, [None if (not isinstance(v, str) and pd.isna(v)) else v for v in row])
    wb.save(path)


def col_letter(header, name):
    return get_column_letter(header.index(name) + 1)


def resolve_formula(value, ws_values, row):
    """Resolve the only formula shapes a PO carries: '=P2' style same-sheet refs.

    Anything else raises, so an unexpected formula can never be read as text.
    """
    if not (isinstance(value, str) and value.startswith("=")):
        return value
    m = re.fullmatch(r"=\$?([A-Z]{1,3})\$?(\d+)", value.strip())
    if not m:
        raise ValueError(f"row {row}: unsupported formula {value!r}")
    col = openpyxl.utils.column_index_from_string(m.group(1))
    return ws_values[int(m.group(2))][col - 1]


def read_po(path, sheet=None):
    """Read a PO buy sheet. Returns (header, rows); each row is a dict keyed by
    header (unheaded columns keyed '_col<N>'), plus '_row' (Excel row number)
    and '_raw' (cell values as stored, formulas unresolved)."""
    wb = openpyxl.load_workbook(path)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    header = [c.value for c in ws[1]]
    while header and header[-1] is None:
        header.pop()
    keys = [h if h is not None else f"_col{i + 1}" for i, h in enumerate(header)]
    raw, formula = {}, {}
    for r in range(2, ws.max_row + 1):
        cells = [ws.cell(r, c) for c in range(1, len(header) + 1)]
        raw[r] = [c.value for c in cells]
        formula[r] = [c.data_type == "f" for c in cells]
    rows = []
    for r, vals in raw.items():
        if all(v is None for v in vals):
            continue
        # only a formula cell is resolved: text that merely starts with '=' stays text
        d = {k: resolve_formula(v, raw, r) if f else v for k, v, f in zip(keys, vals, formula[r])}
        d["_row"], d["_raw"] = r, dict(zip(keys, vals))
        rows.append(d)
    return header, rows


def write_po(path, header, rows, review=None, checks=None, fills=None, title="Sheet1", raw=False):
    """rows: list of dicts keyed like read_po. fills: {(row_index, key): colour}."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    keys = [h if h is not None else f"_col{i + 1}" for i, h in enumerate(header)]
    append_text(ws, header)
    for c in ws[1]:
        c.font = Font(bold=True)
    for i, r in enumerate(rows):
        src = r.get("_raw") if raw else r
        append_text(ws, [(src.get(k) if src else None) for k in keys], PO_FORMULA.fullmatch if raw else None)
        excel_row = i + 2
        for j, h in enumerate(header):
            cell = ws.cell(excel_row, j + 1)
            if h in AUD_COLS or (h is None and j > 0 and header[j - 1] in AUD_COLS):
                cell.number_format = AUD_FMT
            elif h in EUR_COLS:
                cell.number_format = EUR_FMT
    if fills:
        for (i, key), colour in fills.items():
            ws.cell(i + 2, keys.index(key) + 1).fill = FILLS[colour]
    ws.freeze_panes = "A2"
    if review is not None:
        write_table(wb, "Review", review, colour_key="Colour")
    if checks is not None:
        write_table(wb, "Checks", checks, colour_key="Result")
    wb.save(path)


def write_table(wb, name, rows, colour_key=None):
    ws = wb.create_sheet(name)
    if not rows:
        ws.append(["(none)"])
        return ws
    cols = list(rows[0].keys())
    append_text(ws, cols)
    for c in ws[1]:
        c.font = Font(bold=True)
    result_colour = {"PASS": "green", "WARN": "amber", "FAIL": "red", "INFO": None}
    for r in rows:
        append_text(ws, [r.get(c) for c in cols])
        if colour_key and colour_key in cols:
            v = r.get(colour_key)
            colour = v if v in FILLS else result_colour.get(v)
            if colour:
                ws.cell(ws.max_row, cols.index(colour_key) + 1).fill = FILLS[colour]
    ws.freeze_panes = "A2"
    for j, c in enumerate(cols):
        ws.column_dimensions[get_column_letter(j + 1)].width = min(60, max(10, len(str(c)) + 2))
    return ws


def review_row(excel_row, code, size, column, value, source, detail, old=None, include_old=False):
    d = {"Row": excel_row, "Code": code, "Size": size, "Column": column}
    if include_old:
        d["Was"] = old
    d.update({"Value": value, "Source": source, "Colour": COLOUR.get(source, "red"), "Why": detail})
    return d
