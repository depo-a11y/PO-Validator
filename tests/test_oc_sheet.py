"""The spreadsheet OC reader (oc/adapters/sheet.py): .xlsx, BIFF and HTML-table .xls, .csv, by a column mapping.

Synthetic data only: every code, name, EAN and amount here is invented, and every file is built in the test.
Real order confirmations are read only when OC_SHEET_DIR points at a local folder of them (see the end)."""
import copy
import csv
import glob
import os
import re
import struct
from pathlib import Path

import openpyxl
import pytest
import yaml

import test_oc_tool as base
from oc import brand as brandm, catalog as catm, service
from oc.adapters import otb, sheet

HEAD = ["Order", "Line", "Style", "Colour ID", "Colour", "Size", "Units", "Unit Price", "Value", "Currency", "RRP",
        "RRP Currency", "EAN", "HS Code", "Origin", "Name", "Group"]
ROWS = [
    ["SO1", 3, "ZZ100AA001", "0101", "INK", "S", 1, 100.0, 100.0, "EUR", 500.0, "AUD", "2000000000017", "11112222",
     "ITALY", "T-SHIRT", "Women's RTW"],
    ["SO1", 2, "ZZ100AA001", "0101", "INK", "M", 2, 100.0, 200.0, "EUR", 500.0, "AUD", "2000000000024", "11112222",
     "ITALY", "T-SHIRT", "Women's RTW"],
    ["SO1", 1, "ZZ200BB002", "0202", "SAND", "0", 3, 50.5, 151.5, "EUR", 250.0, "AUD", "2000000000031", "33334444",
     "SPAIN", "SMALL BAG", "Men's Bags"],
    ["SO2", 1, "ZZ300CC003", "0303", "MOSS", "L", 1, 80.0, 80.0, "EUR", 400.0, "AUD", "2000000000048", "11112222",
     "PORTUGAL", "T-SHIRT", "Men's RTW"],
]
COLUMNS = dict(doc="Order", line_no="Line", style="Style", colour_code="Colour ID", colour_name="Colour", size="Size",
               qty="Units", price="Unit Price", line_value="Value", currency="Currency", rrp="RRP",
               rrp_currency="RRP Currency", ean="EAN", hs_code="HS Code", origin="Origin", description="Name",
               product_class="Name", gender="Group")


def cfg(columns=None, drop=(), extra=None, **sheet_keys):
    cols = dict(COLUMNS if columns is None else columns)
    for k in drop:
        cols.pop(k, None)
    s = dict(columns=cols, size_map={"0": "OS"})
    s.update(sheet_keys)
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    data.update(vendor="TEST BRAND", adapter="sheet", join_key="{style} {colour}", sheet=s, **(extra or {}))
    return brandm.from_mapping(data, "test config", "test")


# ---- writers -------------------------------------------------------------------
def write_xlsx(path, rows, extra_sheets=()):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Order"
    for r in rows:
        ws.append(r)
    for name, cells in extra_sheets:
        s = wb.create_sheet(name)
        for ref, v in cells.items():
            s[ref] = v
    wb.save(path)
    return str(path)


def _cell_text(v, comma):
    if isinstance(v, float):
        t = f"{v:.2f}"
        return t.replace(".", ",") if comma else t
    return "" if v is None else str(v)


def write_html(path, rows, pages=1, comma=False):
    """An HTML-table '.xls' export: the header's lead cell spans the two cells under it, and the header row is
    repeated on every page."""
    head, body = rows[0], rows[1:]
    per = max(1, -(-len(body) // pages))
    out = ["<html><head><meta charset='UTF-8'><style>td {border: 0}</style></head><body>"]
    for p in range(pages):
        out.append("<div class='page'><table class='order-table'>")
        for r in [head] + body[p * per:(p + 1) * per]:
            cells = "".join(f"<td>{_cell_text(v, comma)}</td>" for v in r)
            lead = "<td colspan='2'>Ref</td>" if r is head else "<td>a</td><td>b</td>"
            out.append(f"<tr>{lead}{cells}</tr>")
        out.append("</table></div>")
    out.append("</body></html>")
    Path(path).write_text("\r\n".join(out), encoding="utf-8")
    return str(path)


def _rec(rid, data):
    return struct.pack("<HH", rid, len(data)) + data


def write_biff(path, rows):
    """A BIFF8 workbook stream (an Excel 97 .xls without the OLE2 wrapper, which xlrd reads the same way)."""
    def ustr(s, width):
        return struct.pack("<" + width + "B", len(s), 1) + s.encode("utf-16-le")
    eof = _rec(0x000A, b"")
    head = _rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0005, 0, 0, 0, 0)) + _rec(0x0042, struct.pack("<H", 1200))
    sheet_rec = lambda off: _rec(0x0085, struct.pack("<IBB", off, 0, 0) + ustr("Order", "B"))
    cells = b""
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if v is None:
                continue
            cells += _rec(0x0203, struct.pack("<HHHd", r, c, 0, float(v))) if isinstance(v, (int, float)) else \
                _rec(0x0204, struct.pack("<HHH", r, c, 0) + ustr(str(v), "H"))
    body = _rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0010, 0, 0, 0, 0)) + \
        _rec(0x0200, struct.pack("<IIHHH", 0, len(rows), 0, max(map(len, rows)), 0)) + cells + eof
    glob_len = len(head) + len(sheet_rec(0)) + len(eof)
    Path(path).write_bytes(head + sheet_rec(glob_len) + eof + body)
    return str(path)


def write_csv(path, rows, delimiter=","):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, delimiter=delimiter).writerows(rows)
    return str(path)


WRITERS = {"xlsx": (write_xlsx, ".xlsx"), "html": (write_html, ".xls"), "biff": (write_biff, ".xls"),
           "csv": (write_csv, ".csv")}


def make(tmp_path, fmt="xlsx", rows=None, name="order", **kw):
    w, ext = WRITERS[fmt]
    return w(tmp_path / f"{name}{ext}", [HEAD] + copy.deepcopy(ROWS if rows is None else rows), **kw)


def lines(docs):
    return [l for d in docs for l in d["lines"]]


def problems(path, config):
    with pytest.raises(otb.GateError) as e:
        sheet.parse_checked(path, config)
    return e.value.problems


def summary(docs):
    return [(d["doc"], d["currency"], d["total_qty"], d["net"],
             [(l["line_no"], l["style"], l["colour"], l["colour_name"], l["sizes"], l["qty"], l["price"],
               l["line_value"], l["rrp"], l["rrp_currency"], l.get("ean"), l.get("hs_code"), l.get("origin"),
               l["gender"], l["product_class"]) for l in d["lines"]]) for d in docs]


# ---- one reading, four formats ---------------------------------------------------------
EXPECTED = [
    ("SO1", "EUR", 6, 451.5, [
        (2, "ZZ100AA001", "0101", "INK", {"S": 1, "M": 2}, 3, 100.0, 300.0, 500.0, "AUD",
         {"S": "2000000000017", "M": "2000000000024"}, {"S": "11112222", "M": "11112222"}, {"S": "ITALY", "M": "ITALY"},
         "WOMEN", "T-SHIRT"),
        (1, "ZZ200BB002", "0202", "SAND", {"OS": 3}, 3, 50.5, 151.5, 250.0, "AUD", {"OS": "2000000000031"},
         {"OS": "33334444"}, {"OS": "SPAIN"}, "MEN", "SMALL BAG")]),
    ("SO2", "EUR", 1, 80.0, [
        (1, "ZZ300CC003", "0303", "MOSS", {"L": 1}, 1, 80.0, 80.0, 400.0, "AUD", {"L": "2000000000048"},
         {"L": "11112222"}, {"L": "PORTUGAL"}, "MEN", "T-SHIRT")]),
]


@pytest.mark.parametrize("fmt", WRITERS)
def test_every_format_gives_the_same_confirmations(tmp_path, fmt):
    docs = sheet.parse_checked(make(tmp_path, fmt), cfg())
    assert summary(docs) == EXPECTED
    assert [d["recomputed"] for d in docs] == [[sheet.TOTALS_UNMAPPED, sheet.LINE_QTY]] * 2


def test_codes_stored_as_numbers_read_without_a_decimal_tail(tmp_path):
    rows = [[1001, 7, 90001, 101, "INK", 38, 1, 100, 100, "EUR", None, None, None, None, None, "T-SHIRT", None]]
    docs = sheet.parse_checked(make(tmp_path, "biff", rows=rows), cfg(drop=("rrp", "rrp_currency")))
    (l,) = lines(docs)
    assert (docs[0]["doc"], l["line_no"], l["style"], l["colour"], l["sizes"]) == ("1001", 7, "90001", "101", {"38": 1})


def test_an_html_export_repeats_its_header_on_every_page_and_each_repeat_is_skipped(tmp_path):
    docs = sheet.parse_checked(make(tmp_path, "html", pages=3), cfg())
    assert summary(docs) == EXPECTED
    assert docs[0]["header_repeats"] == 2


def test_an_html_export_with_decimal_commas_and_currency_codes_in_amounts(tmp_path):
    rows = copy.deepcopy(ROWS)
    rows[0][7] = "EUR 100,00"
    path = make(tmp_path, "html", rows=rows, comma=True)
    assert summary(sheet.parse_checked(path, cfg(decimal=","))) == EXPECTED


def test_a_csv_with_semicolons(tmp_path):
    path = make(tmp_path, "csv", delimiter=";")
    assert summary(sheet.parse_checked(path, cfg())) == EXPECTED


def test_the_header_is_found_below_a_title_block_and_in_the_named_worksheet(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "Notes"
    wb.active.append(["Style", "nothing here"])
    ws = wb.create_sheet("Lines")
    ws.append(["ORDER CONFIRMATION"])
    ws.append([])
    for r in [HEAD] + ROWS:
        ws.append(r)
    wb.save(tmp_path / "titled.xlsx")
    docs = sheet.parse_checked(str(tmp_path / "titled.xlsx"), cfg(sheet_name="Lines"))
    assert summary(docs) == EXPECTED


def test_without_a_doc_column_the_file_is_one_confirmation_named_after_it(tmp_path):
    rows = [r for r in ROWS if r[0] == "SO1"]
    docs = sheet.parse_checked(make(tmp_path, rows=rows, name="SEASON_OC"), cfg(drop=("doc",)))
    assert [d["doc"] for d in docs] == ["SEASON_OC"]


# ---- size layouts --------------------------------------------------------------------
MATRIX = [["Style", "Colour", "XS", "S", "M", "Qty", "Price", "Value"],
          ["ZZ100AA001", "INK", 1, 2, None, 3, 100, 300],
          ["ZZ300CC003", "MOSS", None, None, 1, 1, 80, 80]]
MATRIX_CFG = dict(columns=dict(style="Style", colour_name="Colour", qty="Qty", price="Price", line_value="Value"),
                  size_columns=["XS", "S", "M"], currency="EUR")


def matrix_cfg(**kw):
    s = copy.deepcopy(MATRIX_CFG)
    s.update(kw)
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    data.update(vendor="TEST BRAND", adapter="sheet", join_key="{style} {colour}", sheet=s)
    return brandm.from_mapping(data, "test config", "test")


def test_a_size_matrix_one_column_per_size(tmp_path):
    docs = sheet.parse_checked(write_xlsx(tmp_path / "m.xlsx", MATRIX), matrix_cfg())
    assert [(l["style"], l["colour"], l["sizes"], l["qty"], l["line_value"]) for l in lines(docs)] == \
        [("ZZ100AA001", "INK", {"XS": 1, "S": 2}, 3, 300.0), ("ZZ300CC003", "MOSS", {"M": 1}, 1, 80.0)]


def test_a_size_matrix_whose_sizes_do_not_add_up_to_its_qty_fails(tmp_path):
    rows = copy.deepcopy(MATRIX)
    rows[1][5] = 4
    rows[1][7] = 400
    assert any(": sizes sum 3 != Tot. Qty 4" in p for p in problems(write_xlsx(tmp_path / "m.xlsx", rows), matrix_cfg()))


PAIRS = [["Style Number", "Color", "Wholesale (USD)", "M.S.R.P. (USD)", "Size 1", "Qty 1", "Size price 1", "Size 2",
          "Qty 2", "Size price 2", "Total Units", "Total Price (USD)"],
         ["ZZ1", "BLACK INK", 37, 97, "1", 2, 37, "2", "", 37, "=F2+I2", "=F2*G2+I2*J2"],
         ["ZZ2", "IVORY", 83, 211, "28", "", 83, "30", 1, 83, "=F3+I3", "=F3*G3+I3*J3"]]
PAIRS_CFG = dict(columns=dict(style="Style Number", colour_name="Color", price="Wholesale (USD)",
                              rrp="M.S.R.P. (USD)"),
                 size_pairs=[["Size 1", "Qty 1", "Size price 1"], ["Size 2", "Qty 2", "Size price 2"]],
                 currency="USD", rrp_currency="USD")


def pairs_cfg(**kw):
    s = copy.deepcopy(PAIRS_CFG)
    for k, v in kw.items():
        if k == "columns":
            s["columns"].update(v)
        else:
            s[k] = v
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    data.update(vendor="TEST BRAND", adapter="sheet", join_key="{style} {colour}", sheet=s, cost_currency="USD")
    return brandm.from_mapping(data, "test config", "test")


def test_nuorder_size_pairs_with_their_totals_left_as_formulas_excel_never_calculated(tmp_path):
    path = write_xlsx(tmp_path / "nu.xlsx", PAIRS)
    docs = sheet.parse_checked(path, pairs_cfg())
    assert [(l["style"], l["sizes"], l["qty"], l["price"], l["line_value"], l["rrp"], l["rrp_currency"])
            for l in lines(docs)] == [("ZZ1", {"1": 2}, 2, 37.0, 74.0, 97.0, "USD"),
                                      ("ZZ2", {"30": 1}, 1, 83.0, 83.0, 211.0, "USD")]
    assert docs[0]["recomputed"] == [sheet.TOTALS_UNMAPPED, sheet.LINE_QTY, sheet.LINE_VALUES]


def test_a_mapped_formula_column_with_no_saved_value_fails_naming_it(tmp_path):
    path = write_xlsx(tmp_path / "nu.xlsx", PAIRS)
    got = problems(path, pairs_cfg(columns=dict(qty="Total Units")))
    assert any("'Total Units'" in p and "formula with no saved value" in p for p in got), got


def test_a_size_priced_apart_from_its_line_fails(tmp_path):
    rows = copy.deepcopy(PAIRS)
    rows[1][6] = 41
    assert any("size 1 is priced 41, the line 37" in p for p in problems(write_xlsx(tmp_path / "nu.xlsx", rows),
                                                                         pairs_cfg()))


# ---- totals ------------------------------------------------------------------------------
TOTAL_ROW = [None] * 6 + [7, None, 531.5] + [None] * 8


def with_total(row):
    return copy.deepcopy(ROWS) + [row]


def total_cfg():
    return cfg(totals=dict(label="Grand total", qty="Units", value="Value"))


def test_printed_totals_row_reproduced_and_nothing_recomputed(tmp_path):
    row = list(TOTAL_ROW)
    row[4] = "Grand total"
    docs = sheet.parse_checked(make(tmp_path, rows=with_total(row)), total_cfg())
    assert [d["recomputed"] for d in docs] == [[sheet.LINE_QTY]] * 2
    assert summary(docs) == EXPECTED


@pytest.mark.parametrize("col, value, want", [(6, 8, "parsed 7 units, printed 8"),
                                              (8, 532.5, "parsed 531.50, printed net 532.50")])
def test_printed_totals_row_not_reproduced_fails(tmp_path, col, value, want):
    row = list(TOTAL_ROW)
    row[4] = "Grand total"
    row[col] = value
    got = problems(make(tmp_path, rows=with_total(row)), total_cfg())
    assert any(want in p and "confirmations together" in p and p.endswith(otb.NOT_ADDING_UP) for p in got), got


def test_a_number_format_refusal_is_not_called_a_layout_change(tmp_path):
    rows = copy.deepcopy(ROWS)
    rows[0][7], rows[0][8] = "4321.50", "4321.50"
    docs, checks, _ = service.read_oc(make(tmp_path, "html", rows=rows, comma=True), cfg(decimal=","))
    fails = [c["Detail"] for c in checks if c["Result"] == "FAIL"]
    assert docs is None and any("number format (decimal mark" in d for d in fails)
    assert not any(sheet.LAYOUT_HINT in d or service.AI_HINT in d for d in fails)


def test_a_grand_total_that_is_not_reproduced_is_not_called_a_layout_change(tmp_path):
    row = list(TOTAL_ROW)
    row[4], row[6] = "Grand total", 8
    docs, checks, _ = service.read_oc(make(tmp_path, rows=with_total(row)), total_cfg())
    fails = [c["Detail"] for c in checks if c["Check"] == service.GATE_CHECK]
    assert docs is None and any("parsed 7 units, printed 8" in d for d in fails)
    assert not any(sheet.LAYOUT_HINT in d for d in fails)


def test_printed_totals_cells_for_one_confirmation(tmp_path):
    rows = [r for r in ROWS if r[0] == "SO1"]
    config = cfg(totals=dict(qty_cell="Summary!B1", value_cell="Summary!B2"))
    ok = write_xlsx(tmp_path / "ok.xlsx", [HEAD] + rows, [("Summary", {"B1": 6, "B2": 451.5})])
    docs = sheet.parse_checked(ok, config)
    assert (docs[0]["total_qty"], docs[0]["net"], docs[0]["recomputed"]) == (6, 451.5, [sheet.LINE_QTY])
    bad = write_xlsx(tmp_path / "bad.xlsx", [HEAD] + rows, [("Summary", {"B1": 6, "B2": 461.5})])
    assert any("SO1: its lines add up to EUR 451.50, but it prints a net total of EUR 461.50" in p
               for p in problems(bad, config))


def test_a_totals_cell_left_as_an_uncalculated_formula_fails(tmp_path):
    rows = [r for r in ROWS if r[0] == "SO1"]
    path = write_xlsx(tmp_path / "f.xlsx", [HEAD] + rows, [("Summary", {"B1": "=SUM(Order!G2:G4)", "B2": 451.5})])
    got = problems(path, cfg(totals=dict(qty_cell="Summary!B1", value_cell="Summary!B2")))
    assert any("qty_cell" in p and "no saved value" in p for p in got), got


def test_a_sheet_with_no_printed_totals_says_so_in_the_checks(tmp_path):
    docs, checks, _ = service.read_oc(make(tmp_path), cfg())
    assert docs is not None
    (row,) = [c for c in checks if c["Check"] == service.TOTALS_CHECK]
    assert row["Result"] == "WARN"
    assert row["Detail"].startswith("Recomputed from the lines, so nothing printed checked them: totals (the brand "
                                    "config maps no printed totals) and line quantities (none printed: each is the sum "
                                    "of its size cells, so the sizes-sum gate checked nothing) for all 2 confirmations.")
    assert "not printed by the brand" not in row["Detail"]
    docs, checks, _ = service.read_oc(make(tmp_path, rows=[r for r in ROWS if r[0] == "SO2"]),
                                      cfg(drop=("line_value",), totals="none"))
    (row,) = [c for c in checks if c["Check"] == service.TOTALS_CHECK]
    assert row["Detail"].startswith("Recomputed from the lines, so nothing printed checked them: totals (the brand "
                                    "config says the brand prints none) and line quantities")
    assert "line values (the brand config maps no line value column) for SO2." in row["Detail"]


def test_printed_totals_are_not_called_recomputed(tmp_path):
    row = list(TOTAL_ROW)
    row[4] = "Grand total"
    docs, checks, _ = service.read_oc(make(tmp_path, rows=with_total(row)), total_cfg())
    (rec,) = [c for c in checks if c["Check"] == service.TOTALS_CHECK]
    assert docs is not None and "totals (" not in rec["Detail"] and "line quantities" in rec["Detail"]


def test_a_matrix_with_its_line_qty_printed_recomputes_no_line_quantity(tmp_path):
    docs = sheet.parse_checked(write_xlsx(tmp_path / "m.xlsx", MATRIX), matrix_cfg(totals="none"))
    assert docs[0]["recomputed"] == [sheet.TOTALS_NONE]


# ---- the money gates -------------------------------------------------------------------------
def test_a_row_whose_value_is_not_qty_times_price_fails(tmp_path):
    rows = copy.deepcopy(ROWS)
    rows[1][8] = 210.0
    got = problems(make(tmp_path, rows=rows), cfg())
    assert any("2 x 100 = 200.00 != line value 210" in p for p in got), got


def subtotal_rows():
    rows = copy.deepcopy(ROWS)
    rows[0][8] = rows[1][8] = 300.0   # the line's subtotal printed on each of its size rows
    return rows


def test_a_subtotal_repeated_on_every_size_row_is_taken_once_never_summed(tmp_path):
    docs = sheet.parse_checked(make(tmp_path, rows=subtotal_rows()), cfg(line_value_per="line"))
    assert summary(docs) == EXPECTED
    assert lines(docs)[0]["line_value"] == 300.0


def test_a_repeated_subtotal_read_as_row_values_fails(tmp_path):
    got = problems(make(tmp_path, rows=subtotal_rows()), cfg())
    assert any("1 x 100 = 100.00 != line value 300" in p for p in got), got


def test_line_values_per_line_that_differ_between_rows_fail(tmp_path):
    got = problems(make(tmp_path), cfg(line_value_per="line"))
    assert any("print different line values (100, 200)" in p for p in got), got


def test_sizes_of_one_line_priced_differently_fail(tmp_path):
    rows = copy.deepcopy(ROWS)
    rows[1][7], rows[1][8] = 117.0, 234.0
    got = problems(make(tmp_path, rows=rows), cfg())
    assert any("sizes priced differently (100, 117)" in p for p in got), got


def test_a_size_row_with_no_price_fails(tmp_path):
    rows = copy.deepcopy(ROWS)
    rows[1][7] = None
    got = problems(make(tmp_path, rows=rows), cfg())
    assert any("row(s) 3 have no unit price in 'Unit Price'" in p for p in got), got


def test_the_same_size_twice_on_a_line_fails(tmp_path):
    rows = copy.deepcopy(ROWS)
    rows.insert(1, copy.deepcopy(rows[0]))
    rows[1][1] = 9
    got = problems(make(tmp_path, rows=rows), cfg())
    assert any("size S twice on this line" in p for p in got), got


@pytest.mark.parametrize("qty, want", [("two", "'two' is not a number"), (1.5, "quantity 1.5 is not a whole number")])
def test_a_quantity_that_is_not_a_whole_number_fails(tmp_path, qty, want):
    rows = copy.deepcopy(ROWS)
    rows[3][6] = qty
    got = problems(make(tmp_path, rows=rows), cfg())
    assert any("column 'Units'" in p and want in p for p in got), got


def test_a_row_with_numbers_but_no_style_fails(tmp_path):
    rows = copy.deepcopy(ROWS) + [[None] * 6 + [7, None, 531.5] + [None] * 8]
    got = problems(make(tmp_path, rows=rows), cfg())
    assert any("has quantities or prices but no style" in p for p in got), got


def test_corrupting_a_good_reading_trips_the_gates(tmp_path):
    docs = sheet.parse_checked(make(tmp_path), cfg())
    assert otb.gate_problems(docs) == []
    for corrupt in (lambda ds: ds[0]["lines"][0]["sizes"].update(S=2), lambda ds: ds[0]["lines"][1].update(price=51),
                    lambda ds: ds[0]["lines"].pop(), lambda ds: ds[1].update(total_qty=2),
                    lambda ds: ds[1].update(net=81)):
        bad = copy.deepcopy(docs)
        corrupt(bad)
        assert otb.gate_problems(bad)


# ---- the mapping -------------------------------------------------------------------------------
def test_a_mapped_column_missing_from_the_sheet_fails_naming_it(tmp_path):
    got = problems(make(tmp_path), cfg(columns=dict(COLUMNS, hs_code="Tariff")))
    assert got == ["no header row holds every mapped column: missing 'Tariff' (closest: Order row 1)"]


@pytest.mark.parametrize("where, bad, want", [
    ("columns", dict(colour_group="X"), "sheet: columns: unknown key 'colour_group'"),
    ("sheet", dict(size_colums=["S"]), "sheet: unknown key 'size_colums'"),
    ("sheet", dict(totals=dict(label="Total", qty="Units", amount="Value")), "sheet: totals is {label, qty, value}"),
    ("sheet", dict(totals="never"), "sheet: totals is a mapping or none"),
    ("sheet", dict(ean_prefixes=["84x"]), "ean_prefixes is a list of GS1 company prefixes as quoted digits"),
    ("sheet", dict(line_value_per="size"), "line_value_per is row or line"),
    ("sheet", dict(size_columns=["S", "M"]), "give exactly one size layout"),
])
def test_an_unknown_or_bad_mapping_key_fails_at_load(where, bad, want):
    with pytest.raises(brandm.BrandError, match=re.escape(want)):
        cfg(columns=dict(COLUMNS, **bad)) if where == "columns" else cfg(**bad)


def test_an_ean_needs_one_row_per_size():
    with pytest.raises(brandm.BrandError, match="an EAN belongs to one size"):
        matrix_cfg(columns=dict(MATRIX_CFG["columns"], ean="EAN"))


def test_an_rrp_column_must_be_printed_as_one():
    with pytest.raises(brandm.BrandError, match=re.escape("columns: rrp 'Price list' is not an RRP column header")):
        cfg(columns=dict(COLUMNS, rrp="Price list"))
    assert cfg(columns=dict(COLUMNS, rrp="Retail Price"))["sheet"]["columns"]["rrp"] == "Retail Price"


def test_a_sheet_brand_needs_a_join_key_and_a_mapping():
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    with pytest.raises(brandm.BrandError, match="adapter: sheet needs join_key"):
        brandm.from_mapping(dict(data, adapter="sheet", sheet=dict(columns=COLUMNS)), "t")
    with pytest.raises(brandm.BrandError, match="adapter: sheet needs a sheet: block"):
        brandm.from_mapping(dict(data, adapter="sheet", join_key="{style}"), "t")


def test_the_currency_is_given_once():
    with pytest.raises(brandm.BrandError, match="give the OC's currency once"):
        cfg(currency="EUR")
    with pytest.raises(brandm.BrandError, match="give the OC's currency once"):
        cfg(drop=("currency",))


def test_a_brand_config_file_round_trip(tmp_path):
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    data.update(adapter="sheet", join_key="{style} {colour}", sheet=dict(columns=COLUMNS, size_map={"0": "OS"}))
    p = tmp_path / "brandx.local.yaml"
    p.write_text(yaml.safe_dump(data))
    assert summary(sheet.parse_checked(make(tmp_path), brandm.load(str(p)))) == EXPECTED


# ---- the service -------------------------------------------------------------------------------
def test_the_service_hands_the_brand_config_to_the_sheet_reader(tmp_path):
    docs, checks, info = service.read_oc(make(tmp_path), cfg())
    assert summary(docs) == EXPECTED and info is None
    assert checks[0]["Check"] == service.READER_CHECK and checks[0]["Result"] == "INFO"
    assert checks[0]["Detail"].startswith("read by the built-in spreadsheet reader")


def test_a_sheet_that_no_longer_fits_its_mapping_points_at_the_mapping_not_the_ai_box(tmp_path):
    docs, checks, _ = service.read_oc(make(tmp_path), cfg(columns=dict(COLUMNS, hs_code="Tariff")))
    assert docs is None
    (fail,) = [c for c in checks if c["Result"] == "FAIL"]
    assert sheet.LAYOUT_HINT in fail["Detail"] and service.AI_HINT not in fail["Detail"]


def test_a_pdf_given_to_a_sheet_brand_is_refused(tmp_path):
    pdf = tmp_path / "oc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%fake\n")
    docs, checks, _ = service.read_oc(str(pdf), cfg())
    assert docs is None and any("this is a PDF, not a spreadsheet" in c["Detail"] for c in checks)


def test_a_sheet_in_another_currency_is_refused(tmp_path):
    rows = copy.deepcopy(ROWS)
    for r in rows:
        r[9] = "USD"
    docs, checks, _ = service.read_oc(make(tmp_path, rows=rows), cfg())
    assert docs is None
    assert [c["Result"] for c in checks if c["Check"] == service.CURRENCY_CHECK] == ["FAIL"]


def test_one_confirmation_in_two_currencies_fails(tmp_path):
    rows = copy.deepcopy(ROWS)
    rows[2][9] = "USD"
    got = problems(make(tmp_path, rows=rows), cfg())
    assert any("'Currency' is 'USD', but earlier rows of this confirmation say 'EUR'" in p for p in got), got


ISSUER_HEAD = HEAD + ["Supplier", "Line label"]


def issuer_rows(issuer, label="LB"):
    return [r + [issuer, label] for r in copy.deepcopy(ROWS)]


def test_oc_supplier_on_a_sheet_brand_needs_its_issuer_column():
    with pytest.raises(brandm.BrandError, match="oc_supplier is set but neither sheet: columns: issuer nor sheet: fields: issuer is mapped"):
        cfg(extra=dict(oc_supplier="Test Brand SpA"))
    with pytest.raises(brandm.BrandError, match="oc_label is set but neither sheet: columns: label nor sheet: fields: label is mapped"):
        cfg(extra=dict(oc_label="LB"))


@pytest.mark.parametrize("issuer, label, ok, want", [
    ("Test Brand SpA", "LB", True, None),
    ("Other House Ltd", "LB", False, "SO1 is from 'Other House Ltd', not Test Brand SpA"),
    (None, "LB", False, "SO1 is from '', not Test Brand SpA"),
    ("Test Brand SpA", "XY", False, "SO1 is for label 'XY', not LB"),
])
def test_the_sheet_issuer_and_label_are_checked(tmp_path, issuer, label, ok, want):
    path = write_xlsx(tmp_path / "o.xlsx", [ISSUER_HEAD] + issuer_rows(issuer, label))
    config = cfg(columns=dict(COLUMNS, issuer="Supplier", label="Line label"),
                 extra=dict(oc_supplier="Test Brand SpA", oc_label="LB"))
    docs, checks, _ = service.read_oc(path, config)
    assert (docs is not None) == ok
    if not ok:
        (row,) = [c for c in checks if c["Check"] == service.IDENTITY_CHECK]
        assert want in row["Detail"], row["Detail"]


def test_a_reader_whose_parse_takes_no_brand_config_still_gets_the_path_alone(monkeypatch):
    seen = []
    monkeypatch.setattr(service, "adapter", lambda c: type("M", (), {"parse_checked": staticmethod(
        lambda path: seen.append(path) or base._docs())}))
    docs, checks, _ = service.read_oc("x.pdf", dict(base.BRAND))
    assert seen == ["x.pdf"] and docs is not None


# ---- into the draft -------------------------------------------------------------------------------
def _catalog(tmp_path):
    rows = [base._variant("1", "Old Bag in Black", "MEN BAGS MINI & CROSSBODY", "Active", "ZZ900XX009 0909", "BLACK",
                          "OS", "ZXSS27900", "900.00", "900.00", "300.00", True)]
    p = tmp_path / "catalog.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=base.CAT_COLS)
        w.writeheader()
        w.writerows(rows)
    return str(p)


def _po_rows(po_bytes, tmp_path):
    p = tmp_path / "draft.xlsx"
    p.write_bytes(po_bytes)
    ws = openpyxl.load_workbook(p).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    return [dict(zip(rows[0], r)) for r in rows[1:]], openpyxl.load_workbook(p)


GTIN = "Variant Metafield: Variant.gtin [single_line_text_field]"
HS = "Variant Metafield: HS Code"


def test_a_sheet_oc_drafts_with_its_eans_hs_codes_line_gender_and_aud_rrp(tmp_path):
    po, review, checks = service.draft(make(tmp_path, rows=ROWS[:2]), _catalog(tmp_path), cfg())
    assert po is not None, [c for c in checks if c["Result"] == "FAIL"]
    rows, wb = _po_rows(po, tmp_path)
    by_size = {r["Option2 Value"]: r for r in rows}
    assert (by_size["S"][GTIN], by_size["M"][GTIN]) == ("2000000000017", "2000000000024")
    assert by_size["S"][HS] == "11112222"
    assert {r["Type"] for r in rows} == {"WOMEN CLOTHING T-SHIRTS SHORT SLEEVE"}
    assert {r["Variant Price"] for r in rows} == {500}
    why = {(r["Size"], r["Column"]): r["Why"] for r in review.to_dict("records")}
    assert why[("S", GTIN)] == "EAN printed for size S on SO1 line 2"
    assert why[("S", "Variant Price")] == "brand RRP A$500 (SO1 line 2, column 'RRP')"
    (tot,) = [c for c in checks if c["Check"] == "OC totals reproduced (parsed = printed)"]
    assert tot["Result"] == "INFO" and "SO1 print(s) no totals" in tot["Detail"]


def test_an_rrp_in_another_currency_leaves_the_retail_price_blank(tmp_path):
    rows = copy.deepcopy(ROWS[:2])
    for r in rows:
        r[11] = "EUR"
    po, review, checks = service.draft(make(tmp_path, rows=rows), _catalog(tmp_path), cfg())
    rows_, _ = _po_rows(po, tmp_path)
    assert {r["Variant Price"] for r in rows_} == {None}
    why = {(r["Size"], r["Column"]): r["Why"] for r in review.to_dict("records")}
    assert "the brand's RRP is EUR 500 (SO1 line 2, column 'RRP'), not an AUD price" in why[("S", "Variant Price")]


# ---- packaging ---------------------------------------------------------------------------------
def test_requirements_install_xlrd_for_biff_xls():
    have = [re.split(r"[<>=]", line.strip())[0].lower() for line in (Path(__file__).resolve().parent.parent /
                                                                      "requirements.txt").read_text().splitlines()]
    assert "xlrd" in have


def test_the_page_offers_no_ai_box_for_a_sheet_brand(tmp_path, monkeypatch):
    import test_oc_page as page
    mapping = yaml.safe_dump(dict(adapter="sheet", join_key="{style} {colour}",
                                  sheet=dict(columns=COLUMNS, size_map={"0": "OS"})))
    at = page._page_with_brand(tmp_path, monkeypatch, mapping)
    assert not at.exception
    assert not [c for c in at.checkbox if c.label.startswith("Read the OC with AI")]


# ---- number format, every cell (review 2026-11-12-buyer-10 finding 1) ------------------------------
@pytest.mark.parametrize("cell, decimal, want", [
    ("12,50", ",", 12.5), ("1.234,50", ",", 1234.5), ("1.234.567", ",", 1234567.0), ("EUR 12,50", ",", 12.5),
    ("12.50", ".", 12.5), ("1,234.50", ".", 1234.5), ("1234.567", ".", 1234.567), (12.5, ",", 12.5), ("7", ",", 7.0),
])
def test_a_cell_that_fits_the_declared_number_format(cell, decimal, want):
    assert sheet.number(cell, decimal) == want


@pytest.mark.parametrize("cell, decimal, want", [
    ("4321.50", ",", "does not fit the brand config's decimal mark ','"),
    ("1,234.50", ",", "does not fit the brand config's decimal mark ','"),
    ("4321,50", ".", "does not fit the brand config's decimal mark '.'"),
    ("1.234,50", ".", "does not fit the brand config's decimal mark '.'"),
    ("1.234", ",", "reads two ways (1234 or 1.234)"), ("1,234", ".", "reads two ways (1234 or 1.234)"),
    ("1.234", ".", "reads two ways (1234 or 1.234)"), ("1,234", ",", "reads two ways (1234 or 1.234)"),
])
def test_a_cell_that_contradicts_or_cannot_settle_the_number_format_fails(cell, decimal, want):
    with pytest.raises(ValueError, match=re.escape(want)):
        sheet.number(cell, decimal)


def test_an_oc_whose_money_switched_to_decimal_points_fails_naming_the_cell(tmp_path):
    rows = copy.deepcopy(ROWS)
    rows[0][7], rows[0][8] = "4321.50", "4321.50"   # this brand's sheets print decimal commas
    got = problems(make(tmp_path, "html", rows=rows, comma=True), cfg(decimal=","))
    assert got == ["2 money or quantity cell(s) do not read under the brand config's number format (decimal mark ','): "
                   "table row 2 column 'Unit Price': '4321.50'; table row 2 column 'Value': '4321.50'. A separator "
                   "that contradicts the format, or a cell that reads two ways (1.234), is never guessed: has the "
                   "OC's number format changed? Check the file, then ask Colwyn to update sheet: decimal"]


# ---- identity (finding 2) ---------------------------------------------------------------------------------
def test_a_totals_row_in_another_number_format_fails_naming_it(tmp_path):
    row = list(TOTAL_ROW)
    row[4], row[8] = "Grand total", "531.50"
    path = make(tmp_path, "html", rows=with_total(row), comma=True)
    config = cfg(decimal=",", totals=dict(label="Grand total", qty="Units", value="Value"))
    got = problems(path, config)
    assert len(got) == 1 and got[0].startswith("1 money or quantity cell(s) do not read under the brand config's "
                                               "number format (decimal mark ','): table row 6 column 'Value': '531.50'.")
    row[8] = "531,50"
    assert summary(sheet.parse_checked(make(tmp_path, "html", rows=with_total(row), comma=True), config)) == EXPECTED


def test_a_sheet_brand_with_nothing_to_check_its_identity_says_so(tmp_path):
    docs, checks, _ = service.read_oc(make(tmp_path), cfg())
    (row,) = [c for c in checks if c["Check"] == service.IDENTITY_CHECK]
    assert docs is not None and row["Result"] == "WARN"
    assert row["Detail"].startswith("NOT checked for all 2 confirmations: the brand config names no issuer")
    assert "would be drafted as TEST BRAND" in row["Detail"]


def test_a_style_prefix_checks_a_sheet_brands_identity(tmp_path):
    docs, checks, _ = service.read_oc(make(tmp_path), cfg(extra=dict(oc_style_prefix=["ZZ"])))
    assert docs is not None and not [c for c in checks if c["Check"] == service.IDENTITY_CHECK]
    docs, checks, _ = service.read_oc(make(tmp_path), cfg(extra=dict(oc_style_prefix=["ZZ1"])))
    (row,) = [c for c in checks if c["Check"] == service.IDENTITY_CHECK]
    assert docs is None and row["Result"] == "FAIL" and "SO2 has 1 style(s) not starting ZZ1 (ZZ300CC003)" in row["Detail"]


def test_the_brands_ean_prefix_anchors_its_identity(tmp_path):
    docs, checks, _ = service.read_oc(make(tmp_path), cfg(ean_prefixes=["2000000"]))
    assert docs is not None and not [c for c in checks if c["Check"] == service.IDENTITY_CHECK]
    assert docs[0]["identity"] == "all 3 EANs carry the brand's GS1 prefix (2000000)"


def test_an_ean_under_another_prefix_is_refused_as_another_brand(tmp_path):
    rows = copy.deepcopy(ROWS)
    rows[1][12] = "3000000000024"
    docs, checks, _ = service.read_oc(make(tmp_path, rows=rows), cfg(ean_prefixes=["2000000"]))
    assert docs is None
    (row,) = [c for c in checks if c["Check"] == service.IDENTITY_CHECK]
    assert row["Result"] == "FAIL"
    assert "SO1 line 2 size M: EAN 3000000000024 starts with none of the brand's GS1 prefixes (2000000)" in row["Detail"]
    assert "Check you picked the right brand" in row["Detail"]
    docs = sheet.parse_checked(make(tmp_path, rows=rows), cfg(ean_prefixes=["2000000"]))
    assert docs[0]["identity"] is None and docs[1]["identity"] == "all 1 EANs carry the brand's GS1 prefix (2000000)"


def test_a_document_with_no_ean_is_not_anchored_by_the_prefix(tmp_path):
    rows = copy.deepcopy(ROWS)
    rows[3][12] = None
    docs, checks, _ = service.read_oc(make(tmp_path, rows=rows), cfg(ean_prefixes=["2000000"]))
    (row,) = [c for c in checks if c["Check"] == service.IDENTITY_CHECK]
    assert row["Result"] == "WARN" and row["Detail"].startswith("NOT checked for SO2:")


def test_ean_prefixes_need_the_ean_column():
    with pytest.raises(brandm.BrandError, match="ean_prefixes is set but columns: ean is not mapped"):
        cfg(drop=("ean",), ean_prefixes=["2000000"])


def test_a_fixed_reader_brand_with_no_issuer_or_label_gets_the_warning_too(monkeypatch):
    monkeypatch.setattr(service, "adapter", lambda c: type("M", (), {"parse_checked": staticmethod(
        lambda path: base._docs())}))
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    docs, checks, _ = service.read_oc("x.pdf", dict(data, adapter="admilano"))
    assert [c["Result"] for c in checks if c["Check"] == service.IDENTITY_CHECK] == ["WARN"]
    docs, checks, _ = service.read_oc("x.pdf", dict(base.BRAND))
    assert not [c for c in checks if c["Check"] == service.IDENTITY_CHECK]


# ---- guards the reviewer found untested (finding 5) ------------------------------------------------------------
def test_a_mapped_header_printed_in_two_columns_fails(tmp_path):
    head = HEAD[:-1] + ["Units"]   # 'Group' printed as a second 'Units'
    path = write_xlsx(tmp_path / "o.xlsx", [head] + copy.deepcopy(ROWS))
    got = problems(path, cfg(drop=("gender",)))
    assert got == ["Order row 1: header(s) 'Units' printed in more than one column"]


def test_a_size_printed_twice_in_one_matrix_row_fails(tmp_path):
    rows = [MATRIX[0][:5] + ["S"] + MATRIX[0][5:], MATRIX[1][:5] + [1] + MATRIX[1][5:]]
    rows[1][6], rows[1][8] = 4, 400
    config = matrix_cfg(size_columns=["XS", "S", "M"])
    path = write_xlsx(tmp_path / "m.xlsx", rows)
    got = problems(path, config)
    assert got == ["Order row 1: header(s) 'S' printed in more than one column"]
    pairs = [["Style", "Colour", "Price", "Size 1", "Qty 1", "Size 2", "Qty 2"], ["ZZ1", "INK", 10, "S", 1, "S", 2]]
    s = dict(columns=dict(style="Style", colour_name="Colour", price="Price"), currency="EUR",
             size_pairs=[["Size 1", "Qty 1"], ["Size 2", "Qty 2"]])
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    got = problems(write_xlsx(tmp_path / "p.xlsx", pairs),
                   brandm.from_mapping(dict(data, adapter="sheet", join_key="{style}", sheet=s), "t"))
    assert any("size S twice on this line" in p for p in got), got


def test_a_blank_line_value_in_a_mapped_matrix_column_fails(tmp_path):
    rows = copy.deepcopy(MATRIX)
    rows[2][7] = None
    got = problems(write_xlsx(tmp_path / "m.xlsx", rows), matrix_cfg())
    assert any("row 3: no line value in 'Value'" in p for p in got), got


def test_an_rrp_that_differs_by_size_leaves_retail_blank_and_the_checks_say_so(tmp_path):
    rows = copy.deepcopy(ROWS[:2])
    rows[1][10] = 520.0
    path = make(tmp_path, rows=rows)
    docs, checks, _ = service.read_oc(path, cfg())
    assert lines(docs)[0]["rrp"] is None
    (row,) = [c for c in checks if c["Check"] == service.NOTES_CHECK]
    assert row["Result"] == "WARN"
    assert row["Detail"] == ("the sheet's RRP differs by size on SO1 line 2 ZZ100AA001 (500, 520); not used, so its "
                             "retail price is left blank for a person to type")
    po, review, _ = service.draft(path, _catalog(tmp_path), cfg())
    rows_, _ = _po_rows(po, tmp_path)
    assert {r["Variant Price"] for r in rows_} == {None}


# ---- platforms, header-block fields, footers, selectors, split columns (oc-readers-sheet2) --------------------------
def joor_file(path, brand="TEST HOUSE", status="Approved", sizes=("S", "M", "L"), lines=None, units=None, subtotal=None,
              discount=0, currency="EUR", rrp_currency="AUD"):
    """A JOOR-layout export: brand at B2, label: value pairs above the header, a footer of labelled totals."""
    lines = lines or [("TEE", "ZZ9001.AA01", "BLACK", "001", (1, 2, 0), 40.0, 120.0),
                      ("COAT", "ZZ9002.BB02", "NAVY", "002", (0, 1, 1), 150.5, 420.0)]
    head = (["Style Image", "Style Name", "Style Number", "Color", "Color Code", "Color Comment", "Style Comment",
             "Materials", "Fabrication", "Country of Origin"] + list(sizes)
            + [f"Sugg. Retail ({rrp_currency})", f"WholeSale ({currency})", "Item Discount", "Units", f"Total ({currency})"])
    rows = [[], [None, brand, None, None, "Order Status:", status], [None, "Rep", None, None, "Created Date:", "01/02/2099"],
            [None, "rep@example.com", None, None, "PO Number:", "77001"]] + [[] for _ in range(15)] + [head, ["CUSTOMER"]]
    total_q = total_v = 0
    for name, style, colour, code, qs, price, rrp in lines:
        q = sum(qs)
        total_q += q
        total_v += q * price
        rows.append([None, name, style, colour, code, None, None, None, "COTTON", None] + [x or None for x in qs]
                    + [rrp, price, None, q, q * price])
    pad = [None] * 9
    rows += [pad + ["Total:"] + [None] * len(sizes) + [None, None, None, "Units:", units if units is not None else total_q],
             pad + [None] * (len(sizes) + 4) + ["Subtotal:", subtotal if subtotal is not None else total_v],
             pad + [None] * (len(sizes) + 4) + ["Order Discount:", discount],
             pad + [None] * (len(sizes) + 4) + ["Shipping:", 0], pad + [None] * (len(sizes) + 4) + ["Tax:", 0],
             pad + [None] * (len(sizes) + 4) + ["Total:", total_v - discount], [None, "Order last modified:", "02/10/2099"]]
    return write_xlsx(path, rows)


def platform_cfg(sheet_keys=None, **extra):
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    s = dict(platform="joor", currency="EUR", rrp_currency="AUD")
    s.update(sheet_keys or {})
    data.update(vendor="TEST BRAND", adapter="sheet", join_key="{style} {colour}", oc_supplier="TEST HOUSE", sheet=s, **extra)
    return brandm.from_mapping(data, "test config", "test")


def test_a_joor_export_reads_by_the_platform_mapping_with_its_sizes_from_the_header(tmp_path):
    docs, checks, _ = service.read_oc(joor_file(tmp_path / "j.xlsx"), platform_cfg())
    assert [c["Result"] for c in checks] == ["INFO"], checks
    assert "on the joor platform layout" in checks[0]["Detail"]
    (d,) = docs
    assert (d["doc"], d["issuer"], d["total_qty"], d["net"], d["recomputed"]) == ("77001", "TEST HOUSE", 5, 421.0, [])
    assert [(l["style"], l["colour"], l["sizes"], l["qty"], l["price"], l["line_value"], l["rrp"], l["rrp_currency"])
            for l in d["lines"]] == [("ZZ9001.AA01", "001", {"S": 1, "M": 2}, 3, 40.0, 120.0, 120.0, "AUD"),
                                     ("ZZ9002.BB02", "002", {"M": 1, "L": 1}, 2, 150.5, 301.0, 420.0, "AUD")]
    other = joor_file(tmp_path / "k.xlsx", sizes=("36", "38", "U"))
    assert {s for l in sheet.parse_checked(other, platform_cfg())[0]["lines"] for s in l["sizes"]} == {"36", "38", "U"}


@pytest.mark.parametrize("fault, want", [
    (dict(status="Pending"), "Order Status: 'Pending', not Approved: this file is not a confirmed order"),
    (dict(discount=25), "Order Discount: 25.00 is not applied to any line"),
    (dict(units=6), "77001: its lines add up to 5 units, but it prints a total of 6"),
    (dict(subtotal=431.0), "77001: its lines add up to EUR 421.00, but it prints a net total of EUR 431.00"),
    (dict(currency="USD"), "missing 'WholeSale (EUR)', 'Total (EUR)'"),
])
def test_a_joor_export_that_is_not_the_confirmed_order_as_printed_fails(tmp_path, fault, want):
    got = problems(joor_file(tmp_path / "j.xlsx", **fault), platform_cfg())
    assert any(want in p for p in got), got


def test_a_joor_export_from_another_brand_is_refused(tmp_path):
    docs, checks, _ = service.read_oc(joor_file(tmp_path / "j.xlsx", brand="OTHER HOUSE"), platform_cfg())
    (row,) = [c for c in checks if c["Check"] == service.IDENTITY_CHECK]
    assert docs is None and "77001 is from 'OTHER HOUSE', not TEST HOUSE" in row["Detail"]


def test_a_brand_overrides_the_platform_column_by_column(tmp_path):
    config = platform_cfg(dict(columns=dict(colour_code="Color", colour_name="Color Code", rrp=None), rrp_currency=None))
    cols = sheet.resolve(config["sheet"])["columns"]
    assert (cols["colour_code"], cols["colour_name"], "rrp" in cols, cols["price"]) == ("Color", "Color Code", False,
                                                                                       "WholeSale (EUR)")
    (l, _) = sheet.parse_checked(joor_file(tmp_path / "j.xlsx"), config)[0]["lines"]
    assert (l["colour"], l["colour_name"], l["rrp"]) == ("BLACK", "001", None)


def test_an_unknown_platform_or_a_missing_placeholder_fails_at_load():
    with pytest.raises(brandm.BrandError, match="platform 'nuorderx' is not one the reader knows"):
        platform_cfg(dict(platform="nuorderx"))
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    with pytest.raises(brandm.BrandError, match="the platform's headers need rrp_currency"):
        brandm.from_mapping(dict(data, adapter="sheet", join_key="{style}", sheet=dict(platform="joor", currency="EUR")), "t")


def order_sheet_file(path, discount_label="Discount 0% (EUR)", discount=0, total_q=None):
    head = [None, "Image", "Season / Line", "Category", "Product name", "Product model", "Fabric name", "Made in", "Bulk",
            "XS\nXS", "S\nS", "M\nM", "TALL XL", "Total QTY", "Wholesale price", "Suggested retail price",
            "Wholesale total", "Suggested retail total"]
    rows = [[], [None, "TEST HOUSE", None, None, None, None, None, None, "ORDER SHEET"], [],
            [None, "Order number", "#880001", None, "Line", "-"], [None, "Order date", "29/01/2099"],
            [None, "Customer", "MARAIS", None, None, None, None, discount_label, discount], [], head,
            [None] * 14 + ["EUR", "EUR", "EUR", "EUR"],
            [None, None, "FW", "Jersey", "TEE", "ZZ-AW-001-01", "JERSEY", "Portugal", None, 0, 2, 1, 1, 4, 50.0, 140.0, 200,
             560],
            [None, None, "FW", "Knit", "POLO", "ZZ-AW-002-03", "WOOL", "Italy", None, 1, 1, 0, 0, 2, 75.5, 210.0, 151,
             420],
            [None, "TOTAL", None, None, None, None, None, None, 0, None, None, None, None, total_q or 6, None, None, 351,
             980]]
    return write_xlsx(path, rows)


def order_sheet_cfg():
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    data.update(vendor="TEST BRAND", adapter="sheet", join_key="{style}", oc_supplier="TEST HOUSE",
                sheet=dict(platform="order_sheet", currency="EUR", sheet_name="Order"))
    return brandm.from_mapping(data, "test config", "test")


def test_an_order_sheet_export_with_a_currency_row_and_doubled_size_headers(tmp_path):
    docs, checks, _ = service.read_oc(order_sheet_file(tmp_path / "o.xlsx"), order_sheet_cfg())
    (d,) = docs
    assert (d["doc"], d["issuer"], d["total_qty"], d["net"]) == ("#880001", "TEST HOUSE", 6, 351.0)
    assert [(l["style"], l["colour"], l["sizes"], l["line_value"]) for l in d["lines"]] == \
        [("ZZ-AW-001-01", None, {"S": 2, "M": 1, "TALL XL": 1}, 200.0), ("ZZ-AW-002-03", None, {"XS": 1, "S": 1}, 151.0)]


@pytest.mark.parametrize("fault, want", [
    (dict(discount_label="Discount 10% (EUR)", discount=35), "no amount right of 'Discount 0% (EUR)'"),
    (dict(total_q=7), "#880001: its lines add up to 6 units, but it prints a total of 7"),
])
def test_an_order_sheet_with_a_discount_or_another_total_fails(tmp_path, fault, want):
    got = problems(order_sheet_file(tmp_path / "o.xlsx", **fault), order_sheet_cfg())
    assert any(want in p for p in got), got


SEL_HEAD = ["line", "style", "fabric", "colour", "size", "qty", "price"]
SEL_ROWS = [["AA", "AA100", "F1", "09", "S", 1, 10], ["AA", "AA100", "F1", "09", "M", 2, 10],
            ["BB", "BB200", "F2", "11", "L", 3, 20], ["CC", "CC300", "F3", "12", "OS", 1, 30]]


def select_cfg(values, **sheet_keys):
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    s = dict(columns=dict(style="style", fabric="fabric", colour_code="colour", size="size", qty="qty", price="price"),
             currency="EUR", select=dict(column="line", values=values), **sheet_keys)
    return brandm.from_mapping(dict(data, adapter="sheet", join_key="{style} {fabric} {colour}", sheet=s), "t")


def test_select_reads_only_this_brands_lines_and_says_what_it_left(tmp_path):
    path = write_xlsx(tmp_path / "s.xlsx", [SEL_HEAD] + SEL_ROWS)
    docs, checks, _ = service.read_oc(path, select_cfg(["AA", "CC"]))
    assert [(l["style"], l["qty"]) for l in lines(docs)] == [("AA100", 3), ("CC300", 1)]
    (note,) = [c for c in checks if c["Check"] == service.NOTES_CHECK]
    assert note["Detail"] == "1 order rows of other lines (BB; 3 units, value 60.00) left out: this brand config reads only AA, CC ('line')"


def test_a_row_with_no_selector_value_fails_and_select_never_meets_printed_totals(tmp_path):
    rows = copy.deepcopy(SEL_ROWS)
    rows[2][0] = None
    got = problems(write_xlsx(tmp_path / "s.xlsx", [SEL_HEAD] + rows), select_cfg(["AA"]))
    assert any("row 4 has no 'line', so it is no one's line" in p for p in got), got
    with pytest.raises(brandm.BrandError, match="select cannot be used with printed totals"):
        select_cfg(["AA"], totals=dict(qty_cell="B9", value_cell="C9"))


def test_columns_can_take_part_of_a_cell(tmp_path):
    head = ["Style", "Color", "Size", "Qty", "Price", "Model"]
    rows = [head, ["AB0001 CD0002", "GR001 : FERNWOOD", "S", 1, 10, "XXXXXZ1234"]]
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    s = dict(columns=dict(style={"header": "Style", "split": " ", "part": 1}, fabric={"header": "Style", "split": " ", "part": 2},
                          colour_code={"header": "Color", "split": " : ", "part": 1},
                          colour_name={"header": "Color", "split": " : ", "part": 2},
                          description={"header": "Model", "from": 6, "to": 8},
                          product_class={"header": "Style", "remove": "0"}, size="Size", qty="Qty", price="Price"),
             currency="EUR")
    config = brandm.from_mapping(dict(data, adapter="sheet", join_key="{style}{fabric} {colour}", sheet=s), "t")
    (l,) = lines(sheet.parse_checked(write_xlsx(tmp_path / "p.xlsx", rows), config))
    assert (l["style"], l["fabric"], l["colour"], l["colour_name"], l["description"], l["product_class"]) == \
        ("AB0001", "CD0002", "GR001", "FERNWOOD", "Z12", "AB1 CD2")
    with pytest.raises(brandm.BrandError, match="columns: qty is a header text"):
        brandm.from_mapping(dict(data, adapter="sheet", join_key="{style}", sheet=dict(s, columns=dict(
            s["columns"], qty={"header": "Qty", "from": 2}))), "t")


def test_a_join_key_without_a_colour_needs_no_colour_column():
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    s = dict(columns=dict(style="Style", size="Size", qty="Qty", price="Price"), currency="EUR")
    assert brandm.from_mapping(dict(data, adapter="sheet", join_key="{style}", sheet=s), "t")["adapter"] == "sheet"
    with pytest.raises(brandm.BrandError, match="needs colour_code or colour_name"):
        brandm.from_mapping(dict(data, adapter="sheet", join_key="{style} {colour}", sheet=s), "t")


@pytest.mark.parametrize("bad, want", [
    (dict(fields=dict(doc={"label": "PO:"}), columns=dict(doc="Order")), "the order number is given twice"),
    (dict(fields=dict(store={"cell": "B2"})), "sheet: fields: unknown key 'store'"),
    (dict(fields=dict(doc={"label": "PO:", "require": ["A"]})), "sheet: fields: doc is {label: text}"),
    (dict(header_rows=9), "header_rows is a whole number from 1 to 5"),
    (dict(size_columns={"from": "X"}), "size_columns is a list of size headers or {after: header}"),
    (dict(footer_labels="Total:"), "footer_labels is a list of label texts"),
])
def test_bad_new_mapping_keys_fail_at_load(bad, want):
    with pytest.raises(brandm.BrandError, match=re.escape(want)):
        cfg(**{k: v for k, v in bad.items() if k != "columns"}, columns=dict(COLUMNS, **bad.get("columns", {})))


# ---- a report that prints no unit price and some headers one row up ------------------------------------------------
DEM_HEAD = [[None, None, None, None, "Net qty", "Net value"],
            ["Doc", "Style colour", "Size", "Customer PO", None, "EUR"]]
DEM_ROWS = [["D1", "ZZ100AA0101", "S", "X", 1, 20.5], ["D1", "ZZ100AA0101", "M", "X", 2, 41.0],
            ["D1", "ZZ100AA0101", "L", "X", 0, 0.0], ["D2", "ZZ200BB0202", "S", "Y", 0, 0.0]]


def demand_cfg(**sheet_keys):
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    s = dict(columns=dict(doc="Doc", style="Style colour", size="Size", qty="Net qty", line_value="Net value EUR"),
             currency="EUR", header_rows=2, stack_headers=True, price_from="line_value", totals="none", **sheet_keys)
    return brandm.from_mapping(dict(data, adapter="sheet", join_key="{style}", sheet=s), "t")


def test_stacked_headers_and_a_unit_price_from_the_line_value(tmp_path):
    docs, checks, _ = service.read_oc(write_xlsx(tmp_path / "d.xlsx", DEM_HEAD + DEM_ROWS), demand_cfg())
    d1, d2 = docs
    (l,) = d1["lines"]
    assert (l["style"], l["sizes"], l["price"], l["qty"], l["line_value"]) == ("ZZ100AA0101", {"S": 1, "M": 2}, 20.5, 3, 61.5)
    assert d2["lines"] == []   # a document whose every size was cancelled has no line
    assert sheet.UNIT_PRICES in d1["recomputed"] and sheet.UNIT_PRICES not in d2["recomputed"]


@pytest.mark.parametrize("fault, want", [
    (dict(row=1, value=25.0), "sizes priced differently"),
])
def test_a_derived_unit_price_still_has_to_agree_across_a_line_and_fit_cents(tmp_path, fault, want):
    rows = copy.deepcopy(DEM_ROWS)
    rows[fault["row"]][4:6] = [fault.get("qty", rows[fault["row"]][4]), fault["value"]]
    got = problems(write_xlsx(tmp_path / "d.xlsx", DEM_HEAD + rows), demand_cfg())
    assert any(want in p for p in got), got


def test_a_derived_unit_price_that_does_not_fit_cents_fails_the_row_gate(tmp_path):
    rows = [["D1", "ZZ100AA0101", "S", "X", 10, 0.07]]
    got = problems(write_xlsx(tmp_path / "d.xlsx", DEM_HEAD + rows), demand_cfg())
    assert any("10 x 0.01 = 0.10 != line value 0.07" in p for p in got), got


def test_a_select_value_no_config_knows_is_valued_from_the_line_value_when_no_price_is_printed(tmp_path):
    """Review 2026-11-13-buyer-17 F1: price_from: line_value maps no price column, so the units a `select` value
    outside `known` would drop are valued from the printed line value, and the colleague reads a FAIL, not a KeyError."""
    rows = DEM_ROWS + [["D2", "ZZ200BB0202", "M", "Y", 2, 30.0]]
    got = problems(write_xlsx(tmp_path / "d.xlsx", DEM_HEAD + rows),
                   demand_cfg(select=dict(column="Customer PO", values=["X"], known=["X"])))
    assert ("Order: 'Customer PO' 'Y' is in no brand config's select (known: X): 2 rows, 2 units, EUR 30.00 would be "
            "dropped. Add it to the config of the brand that sells it, and to known in each") in got, got


def test_size_columns_are_found_after_a_stacked_header(tmp_path):
    head = [[None, None, "Cost", None, None], ["Doc", "Style", None, "S", "M"]]
    rows = [["D1", "ZZ100AA0101", 12.0, 1, 2]]
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    s = dict(columns=dict(doc="Doc", style="Style", price="Cost"), size_columns=dict(after="Cost"), currency="EUR",
             header_rows=2, stack_headers=True, totals="none")
    config = brandm.from_mapping(dict(data, adapter="sheet", join_key="{style}", sheet=s), "t")
    docs, _, _ = service.read_oc(write_xlsx(tmp_path / "d.xlsx", head + rows), config)
    assert [l["sizes"] for l in lines(docs)] == [{"S": 1, "M": 2}]


def test_a_skipped_row_that_carries_a_value_fails(tmp_path):
    rows = copy.deepcopy(DEM_ROWS)
    rows[2][5] = 12.5   # the L row has no net quantity but a value
    got = problems(write_xlsx(tmp_path / "d.xlsx", DEM_HEAD + rows), demand_cfg())
    assert any("row 5: no net quantity" in p and "a value of 12.5" in p for p in got), got


def test_gross_minus_cancelled_must_equal_net_where_the_file_prints_all_three(tmp_path):
    head = [[None, None, None, "Gross", "Cancelled", "Net qty", "Net value"],
            ["Doc", "Style colour", "Size", None, None, None, "EUR"]]
    rows = [["D1", "ZZ100AA0101", "S", 3, 1, 2, 41.0], ["D1", "ZZ100AA0101", "M", 4, 4, 0, 0.0]]
    cols = dict(doc="Doc", style="Style colour", size="Size", qty="Net qty", line_value="Net value EUR",
                qty_gross="Gross", qty_cancelled="Cancelled")
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    s = dict(columns=cols, currency="EUR", header_rows=2, stack_headers=True, price_from="line_value", totals="none")
    config = brandm.from_mapping(dict(data, adapter="sheet", join_key="{style}", sheet=s), "t")
    docs, _, _ = service.read_oc(write_xlsx(tmp_path / "ok.xlsx", head + rows), config)
    assert [l["sizes"] for l in lines(docs)] == [{"S": 2}]
    rows[1][3] = 5   # gross 5 - cancelled 4 != net 0
    got = problems(write_xlsx(tmp_path / "bad.xlsx", head + rows), config)
    assert any("gross 5 - cancelled 4 != net 0" in p for p in got), got


def test_source_kind_shows_an_order_source_row_and_a_cancelled_document_a_note(tmp_path):
    path = write_xlsx(tmp_path / "d.xlsx", DEM_HEAD + DEM_ROWS)
    plain = [c["Check"] for c in service.read_oc(path, demand_cfg())[1]]
    assert service.SOURCE_CHECK not in plain
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    s = dict(columns=dict(doc="Doc", style="Style colour", size="Size", qty="Net qty", line_value="Net value EUR"),
             currency="EUR", header_rows=2, stack_headers=True, price_from="line_value", totals="none")
    config = brandm.from_mapping(dict(data, adapter="sheet", join_key="{style}", sheet=s,
                                      source_kind="demand export"), "t")
    _, checks, _ = service.read_oc(path, config)
    (src,) = [c for c in checks if c["Check"] == service.SOURCE_CHECK]
    assert src["Result"] == "WARN" and src["Detail"].startswith("demand export: not an order confirmation from the brand")
    (note,) = [c for c in checks if c["Check"] == service.NOTES_CHECK]
    assert "D2 has no line with a quantity" in note["Detail"]


def test_a_blank_source_kind_fails_at_load():
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    with pytest.raises(brandm.BrandError, match="source_kind is a short text"):
        brandm.from_mapping(dict(data, adapter="sheet", join_key="{style}", source_kind=" ",
                                 sheet=dict(columns=dict(style="Style", size="Size", qty="Qty", price="P"))), "t")


def test_a_stacked_header_missing_from_its_rows_fails_naming_it(tmp_path):
    head = [list(DEM_HEAD[0]), list(DEM_HEAD[1])]
    head[0][4] = "Gross qty"
    got = problems(write_xlsx(tmp_path / "d.xlsx", head + DEM_ROWS), demand_cfg())
    assert any("'Net qty'" in p for p in got), got


@pytest.mark.parametrize("bad, want", [
    (dict(price_from="qty"), "price_from is line_value"),
    (dict(columns=dict(COLUMNS, size="Size"), price_from="line_value"), "price_from and columns: price both"),
    (dict(drop=("line_value",), price_from="line_value"), "price_from needs columns: size, qty and line_value"),
    (dict(line_value_per="line", price_from="line_value"), "with line_value_per: row"),
    (dict(stack_headers=True), "stack_headers is true or false, and needs header_rows of 2 or more"),
    (dict(header_rows=2, stack_headers="yes"), "stack_headers is true or false"),
])
def test_bad_stack_and_price_from_keys_fail_at_load(bad, want):
    bad = dict(bad)
    cols = bad.pop("columns", None)
    drop = bad.pop("drop", ())
    with pytest.raises(brandm.BrandError, match=re.escape(want)):
        cfg(columns=cols, drop=("price",) + tuple(drop) if "columns" not in bad and cols is None else drop, **bad)


# ---- real order confirmations, local only ------------------------------------------------------------
REAL = os.environ.get("OC_SHEET_DIR")


def _pdf_totals(pdf):
    import pdfplumber
    with pdfplumber.open(pdf) as f:
        text = "\n".join(p.extract_text() or "" for p in f.pages)
    out = {}
    for m in re.finditer(r"Order Number:\s*(\S+)\s*\n(?:.*\n){0,3}?Total cost per order number:\s*([\d.,]+)\s*€?\s*\n"
                         r"Total units per order number:\s*(\d+)", text):
        out[m.group(1)] = (int(m.group(3)), float(m.group(2).replace(".", "").replace(",", ".")))
    return out


@pytest.mark.skipif(not REAL, reason="set OC_SHEET_DIR to a local folder of <brand>/<order confirmation> spreadsheets "
                                     "and OC_BRAND_DIR to their brand configs")
def test_real_sheets_pass_their_gates_and_match_their_pdf_twins():
    folders = [d for d in sorted(Path(REAL).iterdir()) if d.is_dir()]
    assert folders, f"no <brand> folders in {REAL}"
    for d in folders:
        config = brandm.load(d.name)
        files = [f for f in sorted(d.iterdir()) if f.suffix.lower() in (".xls", ".xlsx", ".csv")]
        assert files, d
        for f in files:
            docs = sheet.parse_checked(str(f), config)
            assert otb.gate_problems(docs) == [], f
            bad = copy.deepcopy(docs)
            bad[0]["lines"][0]["qty"] += 1
            assert otb.gate_problems(bad), f
            twin = f.with_suffix(".pdf")
            if twin.exists():
                printed = _pdf_totals(str(twin))
                if printed:
                    assert {d_["doc"]: (d_["total_qty"], round(d_["net"], 2)) for d_ in docs} == printed, f


# ---- an order form in sections ---------------------------------------------------------
FORM = [["SAMPLE ORDER FORM"],
        ["Style", "Colour", "Price", "S", "M", None, "Units", "Total"],
        ["QQ100", "SLATE", 20, 1, 2, None, 3, 60],
        ["QQ200", "SLATE", 30, None, None, None, 0, 0],
        [None, None, None, None, None, "FOOTWEAR"],
        ["Style", "Colour", "Price", 40, 41.5, 42, "Units", "Total"],
        ["QQ300", "OCHRE", 50, None, 1, 1, 2, 100],
        [None, None, None, None, None, None, 5, 160]]


def form_cfg(**kw):
    s = dict(columns=dict(style="Style", colour_name="Colour", price="Price", qty="Units", line_value="Total"),
             size_columns=dict(after="Price"), currency="EUR", sections=True,
             totals=dict(unlabelled=True, qty="Units", value="Total"))
    s.update(kw)
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    return brandm.from_mapping(dict(data, adapter="sheet", join_key="{style} {colour}", sheet=s), "t")


def test_an_order_form_in_sections_reads_each_under_its_own_header_and_holds_all_to_the_grand_total(tmp_path):
    (d,) = sheet.parse_checked(write_xlsx(tmp_path / "f.xlsx", FORM), form_cfg())
    assert [(l["line_no"], l["style"], l["sizes"], l["qty"], l["line_value"]) for l in d["lines"]] == \
        [(1, "QQ100", {"S": 1, "M": 2}, 3, 60.0), (2, "QQ300", {"41.5": 1, "42": 1}, 2, 100.0)]
    assert (d["total_qty"], d["net"]) == (5, 160)
    assert not [x for x in d["recomputed"] if "total" in x.lower()], d["recomputed"]


@pytest.mark.parametrize("cell, value, want", [
    (6, 6, "its lines add up to 5 units, but it prints a total of 6"),
    (7, 170, "its lines add up to EUR 160.00, but it prints a net total of EUR 170.00")])
def test_an_order_form_whose_grand_total_is_not_reproduced_fails(tmp_path, cell, value, want):
    rows = copy.deepcopy(FORM)
    rows[-1][cell] = value
    got = problems(write_xlsx(tmp_path / "f.xlsx", rows), form_cfg())
    assert any(want in p for p in got), got


def test_an_order_form_with_a_totals_row_in_two_sections_or_two_in_one_fails(tmp_path):
    rows = copy.deepcopy(FORM)
    rows.insert(4, [None, None, None, None, None, None, 3, 60])
    got = problems(write_xlsx(tmp_path / "f.xlsx", rows), form_cfg())
    assert any("a totals row in 2 sections (rows 5, 9); one order prints one" in p for p in got), got
    rows = copy.deepcopy(FORM) + [[None, None, None, None, None, None, 5, 160]]
    got = problems(write_xlsx(tmp_path / "f.xlsx", rows), form_cfg())
    assert any("row 9: a second totals row (rows 8 and 9 both have numbers and no style)" in p for p in got), got


def test_a_section_with_nothing_ordered_is_quiet_but_a_form_with_nothing_ordered_fails(tmp_path):
    rows = copy.deepcopy(FORM)
    rows[2][3:5], rows[2][6:8], rows[-1][6:8] = [None, None], [0, 0], [2, 100]
    (d,) = sheet.parse_checked(write_xlsx(tmp_path / "f.xlsx", rows), form_cfg())
    assert [l["style"] for l in d["lines"]] == ["QQ300"]
    rows[6][4:8] = [None, None, 0, 0]
    got = problems(write_xlsx(tmp_path / "f.xlsx", rows[:-1]), form_cfg(totals="none"))
    assert got == ["Order: no order lines under any of the 2 header rows"]


@pytest.mark.parametrize("bad, want", [
    (dict(sections="yes"), "sections is true or false"),
    (dict(totals=dict(qty_cell="B9", value_cell="C9")), "sections take a totals row (label or unlabelled)"),
    (dict(totals=dict(unlabelled="yes", qty="Units", value="Total")), "totals: unlabelled is true"),
    (dict(totals=dict(unlabelled=True, qty="Units", value="")), "totals: value is a text"),
])
def test_bad_section_keys_fail_at_load(bad, want):
    with pytest.raises(brandm.BrandError, match=re.escape(want)):
        form_cfg(**bad)


def test_a_blank_order_form_every_line_at_zero_is_no_confirmation(tmp_path):
    rows = [MATRIX[0], ["ZZ100AA001", "INK", None, None, None, 0, 100, 0]]
    assert problems(write_xlsx(tmp_path / "m.xlsx", rows), matrix_cfg()) == ["Order: no order lines under the header row 1"]


def test_a_bad_line_in_a_later_section_is_gated_once_under_its_order_line_number(tmp_path):
    rows = copy.deepcopy(FORM)
    rows[6][7], rows[-1][7] = 90, 150
    assert problems(write_xlsx(tmp_path / "f.xlsx", rows), form_cfg()) == \
        ["f line 2 QQ300: 2 x 50.0 = 100.00 != line value 90.0", f"f: its lines add up to EUR 160.00, but it prints a net total of EUR 150.00. {otb.NOT_ADDING_UP}"]


def test_the_grand_total_columns_need_not_be_mapped(tmp_path):
    config = form_cfg(columns=dict(style="Style", colour_name="Colour", price="Price", qty="Units"))
    (d,) = sheet.parse_checked(write_xlsx(tmp_path / "f.xlsx", FORM), config)
    assert (d["total_qty"], d["net"], [l["line_value"] for l in d["lines"]]) == (5, 160, [60.0, 100.0])


# ---- a printed gender: a line's own column, else the order's name -------------------------------------------------
@pytest.mark.parametrize("printed, want", [
    ("Man", "MEN"), ("Woman", "WOMEN"), ("Women", "WOMEN"), ("MENS-EDIT-K", "MEN"), ("WOMENS-EDIT-Q", "WOMEN"),
    ("Men's Bags", "MEN"), ("FEMALE", "WOMEN"), ("Unisex", None), ("MEN / WOMEN", None), ("GERMANY", None),
    ("ROMAN BLIND", None), ("", None), (None, None)])
def test_a_printed_gender_is_read_as_a_word(printed, want):
    assert sheet.gender_of(printed) == want


def test_an_order_name_above_a_joor_header_genders_its_lines_and_the_platform_fields_stay(tmp_path):
    from oc import draft
    path = joor_file(tmp_path / "j.xlsx")
    wb = openpyxl.load_workbook(path)
    wb.active["E6"], wb.active["F6"] = "Order Name:", "WOMENS-EDIT-Q"
    wb.save(path)
    config = platform_cfg(dict(fields=dict(gender=dict(label="Order Name:"))))
    (d,) = sheet.parse_checked(str(path), config)
    assert (d["doc"], d["issuer"], d["gender"]) == ("77001", "TEST HOUSE", "WOMEN")
    assert {l["gender"] for l in draft.oc_lines([d], config)} == {"WOMEN"}


def test_a_line_s_own_gender_column_wins_over_the_order_name(tmp_path):
    from oc import draft
    rows = copy.deepcopy(ROWS)
    rows[3][16] = "Unisex"
    path = write_xlsx(tmp_path / "g.xlsx", [HEAD] + rows, extra_sheets=[("Info", {"B2": "MENS-EDIT-K"})])
    config = cfg(fields=dict(gender=dict(cell="Info!B2")))
    got = {(d["doc"], l["style"]): l["gender"] for d in sheet.parse_checked(str(path), config)
           for l in draft.oc_lines([d], config)}
    assert got == {("SO1", "ZZ100AA001"): "WOMEN", ("SO1", "ZZ200BB002"): "MEN", ("SO2", "ZZ300CC003"): "MEN"}


@pytest.mark.parametrize("status", [dict(label="Order Status:"), None])
def test_a_brand_cannot_drop_the_platform_s_confirmed_order_gate(status):
    with pytest.raises(brandm.BrandError, match="fields: status must keep a require list"):
        platform_cfg(dict(fields=dict(status=status)))
    assert platform_cfg(dict(fields=dict(status=dict(label="Order Status:", require=["Shipped"]))))


# ---- join_key_alt: a second code form, tried only when join_key finds no live product -----------------------------
ALT_HEAD = ["Model", "Colour", "Size", "Qty", "Price"]


def alt_cfg(**extra):
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    data.update(dict(adapter="sheet", join_key="{style} {colour}", join_key_alt="{style_alt} {colour}"), **extra)
    data["sheet"] = dict(columns=dict(style=dict(header="Model", **{"from": 4}), style_alt="Model", colour_code="Colour",
                                      size="Size", qty="Qty", price="Price"), currency="EUR", totals="none")
    return brandm.from_mapping(data, "test config", "test")


def alt_catalog(tmp_path, products):
    rows = [base._variant(pid, title, "MEN CLOTHING T-SHIRTS SHORT SLEEVE", "Active", code, "BLACK", s,
                          f"ZXFW26{pid}{i}", "300.00", "300.00", "60.00", i == 0)
            for pid, title, code in products for i, s in enumerate(["S", "M"])]
    return base._write_catalog(tmp_path / "catalog.csv", rows, base.CAT_COLS)


def test_join_key_alt_joins_a_line_whose_join_key_finds_no_live_product_and_never_one_that_does(tmp_path):
    rows = [ALT_HEAD, ["QQ-AB100", "C01", "S", 1, 40.0], ["QQ-AB200", "C02", "M", 2, 50.0]]
    path = write_xlsx(tmp_path / "a.xlsx", rows)
    cat = alt_catalog(tmp_path, [("901", "Short Tee in Black", "AB100 C01"), ("902", "Long Tee in Black", "QQ-AB100 C01"),
                                 ("903", "Other Tee in Black", "QQ-AB200 C02")])
    po, review, checks = service.draft(str(path), cat, alt_cfg())
    by = {c["Check"]: c for c in checks}
    assert by["Joined by join_key_alt (join_key finds no live product)"]["Detail"] == "AB200 C02 -> QQ-AB200 C02"
    ids = review[review["Column"] == "ID"]
    assert sorted(set(zip(ids["Code"], ids["Value"].astype(str)))) == [("AB100 C01", "901"), ("QQ-AB200 C02", "903")]


def test_join_key_alt_onto_two_live_products_fails_and_a_bad_alt_template_fails_at_load(tmp_path):
    path = write_xlsx(tmp_path / "a.xlsx", [ALT_HEAD, ["QQ-AB300", "C03", "S", 1, 40.0]])
    cat = alt_catalog(tmp_path, [("904", "Tee One in Black", "QQ-AB300 C03"), ("905", "Tee Two in Black", "QQ-AB300 C03")])
    _, _, checks = service.draft(str(path), cat, alt_cfg())
    fails = [c for c in checks if c["Check"] == "Join key resolves to one live product"]
    assert len(fails) == 1 and fails[0]["Result"] == "FAIL" and fails[0]["Detail"].startswith("QQ-AB300 C03: AMBIGUOUS")
    for bad in ("{style_alt} {colour", "{style} {colour}", "{model} {colour}"):
        with pytest.raises(brandm.BrandError, match="join_key_alt is a second join template"):
            alt_cfg(join_key_alt=bad)
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    with pytest.raises(brandm.BrandError, match=re.escape("needs style_alt (join_key_alt uses {style_alt})")):
        brandm.from_mapping(dict(data, adapter="sheet", join_key="{style} {colour}", join_key_alt="{style_alt} {colour}",
                                 sheet=dict(columns=dict(style="Model", colour_code="Colour", size="Size", qty="Qty",
                                                         price="Price"), currency="EUR")), "t")


def test_complete_and_reconcile_key_a_line_by_the_code_join_key_alt_found(tmp_path):
    from oc import complete, reconcile
    from oc.poxlsx import CODE, COST_PRICE, MADE_IN
    path = write_xlsx(tmp_path / "a.xlsx", [ALT_HEAD, ["QQ-AB200", "C02", "M", 2, 50.0]])
    config = alt_cfg()
    cat = catm.load(alt_catalog(tmp_path, [("903", "Other Tee in Black", "QQ-AB200 C02")]))
    docs = sheet.parse_checked(str(path), config)
    rows = [{"Command": "MERGE", "Title": "Other Tee in Black", "Vendor": config["vendor"], "Option2 Value": s,
             "Type": "MEN CLOTHING T-SHIRTS SHORT SLEEVE", "Variant SKU": f"ZXFW26903{i}", "Variant Price": 300.0,
             CODE: "QQ-AB200 C02", "Order Qty": q, "Wholesale Price": 50.0, "Total Order": q * 50.0, "Variant Cost": 75.0,
             MADE_IN: "Italy", COST_PRICE: "EUR 50"} for i, (s, q) in enumerate([("S", 0), ("M", 2)])]
    hand = tmp_path / "hand.xlsx"
    base._hand_po(hand, rows)
    checks = complete.run(str(hand), docs, config, cat, base._hist(), base._tags(), str(tmp_path / "po.xlsx"))
    assert {c["Check"]: c["Result"] for c in checks}["Qty per code+size equals the OC"] == "PASS"
    res = reconcile.compare(rows, rows, docs, brand=config)
    assert (res["draft_qty_mismatch"], res["hand_qty_mismatch"]) == ([], [])


def known_cfg(values, known):
    data = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    s = dict(columns=dict(style="style", fabric="fabric", colour_code="colour", size="size", qty="qty", price="price"),
             currency="EUR", select=dict(column="line", values=values, known=known))
    return brandm.from_mapping(dict(data, adapter="sheet", join_key="{style} {fabric} {colour}", sheet=s), "t")


def test_a_select_value_no_brand_config_knows_fails_naming_its_units_and_value(tmp_path):
    path = write_xlsx(tmp_path / "s.xlsx", [SEL_HEAD] + SEL_ROWS)
    assert problems(path, known_cfg(["AA"], ["AA", "CC"])) == [
        "Order: 'line' 'BB' is in no brand config's select (known: AA, CC): 1 rows, 3 units, EUR 60.00 would be dropped. "
        "Add it to the config of the brand that sells it, and to known in each"]
    assert [l["style"] for l in lines(sheet.parse_checked(path, known_cfg(["AA"], ["AA", "BB", "CC"])))] == ["AA100"]
    with pytest.raises(brandm.BrandError, match=re.escape("select: known must hold every value this config reads")):
        known_cfg(["AA", "BB"], ["AA", "CC"])
