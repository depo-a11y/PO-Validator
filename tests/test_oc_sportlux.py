"""The Sportlux reader (Palm Angels order confirmations): both templates, every gate, the payment documents.

Synthetic PDFs only, built here on oc_synth's writer with the column rules the real prints draw (ss26: one segment
per cell row; fw26: thin filled rectangles over the whole table). Every code, name, price and quantity is invented.
Real PDFs only when OC_FIXED_PDF_DIR points at a local folder of them."""
import glob
import os

import pdfplumber
import pytest

import oc_synth
import test_oc_tool as base
from oc import brand as brandm, service
from oc.adapters import adidas, admilano, fog, otb, sportlux
from oc.draft import oc_lines
from oc_synth import Page, width, write

ISSUER = "TEST General Trading FZCO"
SS_X = [15.8, 50.3, 151.6, 340.1, 406.3, 443.9, 478.5, 521.6, 583.2]                # measured on the real ss26 print
FW_X = [34.7, 104.8, 224.0, 279.2, 309.6, 390.0, 439.6, 470.6, 516.5, 573.8]         # and on the fw26 print
SS_LINES = [  # (style, description, category, [(size, qty)], unit price as printed)
    ("ZXED001S99FAB001", "TEST DOWN JACKET", "RTW", [("S", 1), ("M", 2), ("L", 1)], "1,234.56"),
    ("ZXAA001S99JER001", "TEST LOGO TEE", "RTW", [("XS", 2), ("S", 3)], "43.21"),
    ("ZXAA001S99JER001", "TEST LOGO TEE", "RTW", [("XS", 1), ("S", 1), ("M", 2)], "43.21"),
    ("ZXLB001S99FAB001", "TEST CAP", "ACC", [("O/S", 3)], "21.09"),
    ("ZXIC001S99MAT001", "TEST SLIDERS", "SHOES", [("41", 1), ("42", 1)], "33.33"),
]
FW_LINES = [  # (style, description, category, [(size, qty)], colour, COO, unit price as printed)
    ("ZXAA01W99JR019001", "TEST TEE", "RTW", [("L", 2), ("M", 3), ("S", 2), ("XL", 1)], "BLACK - OFF WHITE",
     "TURKEY", "241"),
    ("ZXBB03W99FL079002", "TEST HOODIE", "RTW", [("L", 1), ("M", 2)], "INK BLACK - OFF WHITE", "CHINA", "263"),
    ("ZXEH01W99FB029003", "TEST BOMBER JACKET", "RTW", [("M", 1)], "DARK BLUE - OFF WHITE", "ITALY", "1,213"),
]


def money(v):
    return f"{v:,.2f}"


def rows_of_lines(lines, fw=False):
    out = []
    for line in lines:
        style, desc, cat, sizes, *rest = line
        price = rest[-1]
        for size, q in sizes:
            r = dict(sno=str(len(out) + 1), style=style, desc=desc, cat=cat, size=size, qty=str(q), price=price,
                     total=money(q * float(price.replace(",", ""))))
            if fw:
                r.update(colour=rest[0], coo=rest[1])
            out.append(r)
    return out


def vline(p, x, top, bottom):
    p.ops.append(f"0.5 w {x:.2f} {p.h - top:.2f} m {x:.2f} {p.h - bottom:.2f} l S")


def vrule(p, x, top, bottom):
    """A cell border as Excel draws it: a 0.6pt filled rectangle."""
    p.ops.append(f"{x - 0.3:.2f} {p.h - bottom:.2f} 0.60 {bottom - top:.2f} re f")


def centre_in(p, xs, k, top, words, size=None):
    p.centre((xs[k] + xs[k + 1]) / 2, top, words, size)


def _totals(p, top, x_label, xs, units, value, discount, net, euro, size=None):
    e = "€ " if euro else ""
    p.text(x_label, top, "Grand Total", size)
    centre_in(p, xs, len(xs) - 4, top, units, size)
    centre_in(p, xs, len(xs) - 2, top, f"{e}{value}", size)
    p.text(x_label + 8, top + 17, "Discount", size)
    centre_in(p, xs, len(xs) - 2, top + 17, f"{e}{discount}", size)
    p.text(x_label + 6, top + 34, "Net Total", size)
    centre_in(p, xs, len(xs) - 2, top + 34, f"{e}{net}", size)


def ss26_pdf(path, lines=SS_LINES, split=(4, 3), order_no="D-SO-999001", edit=None, drop_page=None, shift=None,
             rule_dx=None, header_dx=None, grand=None, discount="0.00", net=None, extra_page=False, blank_page=None,
             repeat_last=False, fw_header_page=None):
    """The ERP template: SNo. column, Order No. on every page, rule segments per row. `split` is the rows on each
    page but the last; `edit(rows)` changes the printed cells; shift=(row, column, dx) moves one cell;
    rule_dx=(row, dx) moves one row's own rule segments; header_dx=(title, dx) moves one column title; grand=(units, value),
    net override the printed totals; extra_page adds a page with no table after the totals; blank_page=n puts a
    page with no table before page n of the table; repeat_last prints the last page twice; fw_header_page=n heads
    page n with the fw26 column titles."""
    rows = rows_of_lines(lines)
    if edit:
        edit(rows)
    units = sum(int(r["qty"]) for r in rows)
    value = money(sum(float(r["total"].replace(",", "")) for r in rows))
    chunks, i = [], 0
    for n in split:
        chunks.append(rows[i:i + n])
        i += n
    chunks.append(rows[i:])
    pages = []
    for c, chunk in enumerate(chunks, 1):
        if blank_page == c:
            pages.append(_ss_head(Page(612, 792, 8), order_no))
        p = _ss_head(Page(612, 792, 8), order_no)
        for x in SS_X:
            vline(p, x, 236.7, 253.7)
        if fw_header_page == c:
            p.words(24.0, 241.0, list(sportlux.TEMPLATES["fw26"]), 6)
        for k, title in enumerate(sportlux.TEMPLATES["ss26"] if fw_header_page != c else ()):
            dx = header_dx[1] if header_dx and header_dx[0] == title else 0
            p.centre((SS_X[k] + SS_X[k + 1]) / 2 + dx, 241.0, title)
        top = 258.0
        for r in chunk:
            n = int(r["sno"]) if r["sno"].isdigit() else 0
            for x in SS_X:
                dx = rule_dx[1] if rule_dx and rule_dx[0] == n else 0
                vline(p, x + dx, top - 4.3, top + 12.7)
            for k, key in enumerate(("sno", "style", "desc", "cat", "size", "qty", "price", "total")):
                dx = shift[2] if shift and shift[:2] == (n, key) else 0
                p.centre((SS_X[k] + SS_X[k + 1]) / 2 + dx, top, r[key])
            top += 17.0
        if c == len(chunks):
            g = grand or (str(units), value)
            _totals(p, top + 2, 397.4, SS_X, g[0], g[1], discount, net or value, euro=False)
        p.text(156.7, 769.8, "This is an electronically generated document and no signature is required.")
        if drop_page != c:
            pages.append(p)
    if repeat_last:
        pages.append(pages[-1])
    if extra_page:
        pages.append(_ss_head(Page(612, 792, 8), order_no))
    return write(path, pages)


def _ss_head(p, order_no):
    p.text(17.7, 20.2, ISSUER, 14)
    p.text(17.7, 37.2, "1 Test Street, Test Zone,")
    p.text(17.7, 58.5, "VAT No - 000000000000000")
    p.text(244.1, 108.7, "Order Confirmation", 12)
    p.text(17.7, 136.1, "Bill To:")
    p.text(392.1, 136.1, "Date :")
    p.text(468.6, 136.1, "1/2/2099")
    p.text(17.7, 146.7, "TEST CUSTOMER")
    if order_no:
        p.text(392.1, 150.7, "Order No. :")
        p.text(468.6, 150.7, order_no)
    p.text(392.1, 165.4, "Payment Terms:")
    p.text(468.6, 165.4, "TEST TERMS")
    p.text(392.1, 194.7, "Currency :")
    p.text(468.6, 194.7, "EUR")
    p.text(392.1, 209.3, "Delivery terms")
    p.text(468.6, 209.3, "EX-WORKS")
    return p


def fw26_pdf(path, lines=FW_LINES, edit=None, grand=None, discount="0.00", net=None, issuer=ISSUER.upper(),
             copies=1, then=None):
    """The Excel template: Color and COO columns, no SNo. and no order number, rules as thin rectangles over
    the whole table, unit prices printed whole, € before the totals. copies=2 prints the page twice; `then` adds a
    second confirmation of those lines on its own page."""
    pages = [_fw26_page(lines, edit, grand, discount, net, issuer)] * copies
    return write(path, pages + ([_fw26_page(then, None, None, "0.00", None, issuer)] if then else []))


def _fw26_page(lines, edit, grand, discount, net, issuer):
    rows = rows_of_lines(lines, fw=True)
    if edit:
        edit(rows)
    units = sum(int(r["qty"]) for r in rows)
    value = money(sum(float(r["total"].replace(",", "")) for r in rows))
    p, s = Page(595.2, 841.68, 6.4), 6.4
    p.text(36.8, 32.0, issuer, 10.2)
    p.text(36.0, 47.9, "1 Test Street, Test Zone", 5.2)
    p.text(257.6, 96.6, "Order Confirmation", 12.8)
    p.text(36.1, 120.9, "Bill To:")
    p.text(441.0, 120.9, "Date: 1 March 2099")
    p.text(36.1, 130.1, "Company Name: Test Customer")
    p.text(441.0, 130.1, "Payment Terms: TEST TERMS")
    p.text(441.0, 139.4, "Currency: EUR")
    p.text(441.0, 148.6, "Delivery Terms: EX-WORKS")
    p.text(441.0, 157.8, "Delivery Window:")
    bottom = 208.7 + 8.3 * len(rows)
    for x in FW_X:
        vrule(p, x, 194.2, bottom)
    for k, title in enumerate(sportlux.TEMPLATES["fw26"]):
        centre_in(p, FW_X, k, 198.3, title, s)
    top = 208.7
    for r in rows:
        for k, key in enumerate(("style", "desc", "cat", "size", "colour", "coo", "qty", "price", "total")):
            centre_in(p, FW_X, k, top, r[key], s)
        top += 8.3
    g = grand or (str(units), value)
    _totals(p, bottom + 4, 400.1, FW_X, g[0], g[1], discount, net or value, euro=True, size=s)
    p.text(509.7, 810.2, "Page 1 of 1")
    return p


def payment_pdf(path, title="PROFORMA INVOICE", number="PI 999"):
    p = Page(595.2, 841.68, 6.4)
    p.text(226.5, 26.2, ISSUER.upper(), 10)
    p.text(262.1, 79.6, title, 10)
    p.text(22.2, 100.7, "BILL TO:")
    p.text(408.6, 100.7, "DATE 1-Jan-2099")
    p.text(408.6, 111.4, f"DOCUMENT NO. : {number}")
    p.text(408.6, 122.4, "ORDER NO.: TEST ORDER")
    p.text(408.6, 133.2, "PAYMENT TERM: 100% ADVANCE")
    p.text(25.4, 193.2, "SL. NO. STYLE DESCRIPTION CATEGORY BARCODE TTL QTY PRICE TOTAL AMOUNT")
    p.text(33.8, 212.2, "1 ZXAA001S99JER0019001 S TEST LOGO TEE RTW 0000000000000 1 € 43.21 € 43.21")
    return write(path, [p])


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    d = tmp_path_factory.mktemp("sportlux")
    return dict(ss26=ss26_pdf(str(d / "ss26.pdf")), fw26=fw26_pdf(str(d / "fw26.pdf")))


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "fault.pdf")


def lines(docs):
    return [l for d in docs for l in d["lines"]]


def problems(pdf):
    return sportlux.parse(pdf)[1]


# ---- both templates read ----------------------------------------------------------------------------------------
def test_ss26_reproduces_the_printed_totals_across_three_pages(pdfs):
    docs = sportlux.parse_checked(pdfs["ss26"])
    (d,) = docs
    t = sportlux.totals(docs)
    assert (t["lines"], t["units"], t["value"], t["printed_units"], t["printed_value"]) == (5, 18, 5457.06, 18, 5457.06)
    assert (d["doc"], d["template"], d["date"], d["currency"], d["pages"]) == ("D-SO-999001", "ss26", "1/2/2099",
                                                                               "EUR", [1, 2, 3])
    assert (d["gross"], d["discount"], d["net"], d["payment_terms"], d["delivery_terms"]) == \
        (5457.06, 0.0, 5457.06, "TEST TERMS", "EX-WORKS")


def test_ss26_a_size_printed_again_starts_the_next_colour_of_a_style(pdfs):
    ls = lines(sportlux.parse_checked(pdfs["ss26"]))
    assert [l["style"] for l in ls] == ["ZXED001S99FAB001", "ZXAA001S99JER001", "ZXAA001S99JER001",
                                        "ZXLB001S99FAB001", "ZXIC001S99MAT001"]
    assert [l["sizes"] for l in ls[1:3]] == [{"XS": 2, "S": 3}, {"XS": 1, "S": 1, "M": 2}]
    assert [l["line_no"] for l in ls] == [1, 4, 6, 9, 10]
    assert ls[2]["rows"] == [6, 7, 8] and ls[2]["page"] == 2   # rows 6-7 on page 2, row 8 on page 3


def test_ss26_line_fields_and_thousands(pdfs):
    l = lines(sportlux.parse_checked(pdfs["ss26"]))[0]
    assert (l["price"], l["qty"], l["line_value"], l["sizes"]) == (1234.56, 4, 4938.24, {"S": 1, "M": 2, "L": 1})
    assert (l["description"], l["product_class"], l["category"], l["colour"], l["colour_name"], l["made_in"]) == \
        ("TEST DOWN JACKET", "TEST DOWN JACKET", "RTW", None, None, None)
    assert lines(sportlux.parse_checked(pdfs["ss26"]))[3]["sizes"] == {"O/S": 3}


def test_fw26_reads_colour_and_origin_and_whole_unit_prices(pdfs):
    docs = sportlux.parse_checked(pdfs["fw26"])
    (d,) = docs
    t = sportlux.totals(docs)
    assert (t["lines"], t["units"], t["value"], t["printed_units"], t["printed_value"]) == (3, 12, 3930.0, 12, 3930.0)
    assert (d["doc"], d["order_no"], d["template"], d["date"]) == ("Order Confirmation of 1 March 2099", None, "fw26",
                                                                   "1 March 2099")
    ls = d["lines"]
    assert [(l["colour_name"], l["made_in"], l["price"]) for l in ls] == [
        ("BLACK - OFF WHITE", "TURKEY", 241.0), ("INK BLACK - OFF WHITE", "CHINA", 263.0),
        ("DARK BLUE - OFF WHITE", "ITALY", 1213.0)]
    assert ls[0]["sizes"] == {"L": 2, "M": 3, "S": 2, "XL": 1} and ls[0]["line_no"] == 1 and ls[0]["rows"] is None
    assert [(l["style"], l["colour"], l["colour_code"]) for l in ls] == [
        ("ZXAA01W99JR01", "9001", "9001"), ("ZXBB03W99FL07", "9002", "9002"), ("ZXEH01W99FB02", "9003", "9003")]


def test_fw26_a_long_colour_runs_past_its_header_word_and_stays_in_its_column(pdfs):
    with pdfplumber.open(pdfs["fw26"]) as pdf:
        words = pdf.pages[0].extract_words()
    colour, coo = (next(otb.xc(w) for w in words if w["text"] == t) for t in ("Color", "COO"))
    white = max(w["x1"] for w in words if w["text"] == "WHITE")
    assert white > (colour + coo) / 2   # a band halfway between header words would cut the colour
    assert lines(sportlux.parse_checked(pdfs["fw26"]))[1]["colour_name"] == "INK BLACK - OFF WHITE"


def test_issuer_read_and_no_label(pdfs):
    assert [sportlux.parse_checked(pdfs[k])[0]["issuer"] for k in ("ss26", "fw26")] == [ISSUER, ISSUER.upper()]
    assert [sportlux.parse_checked(pdfs[k])[0]["label"] for k in ("ss26", "fw26")] == [None, None]
    assert sportlux.doc_label({"lines": []}) is None


def test_the_join_code_is_style_then_colour_code(pdfs):
    cfg = brandm.from_mapping(dict(base.BRAND, adapter="sportlux", oc_supplier=ISSUER, oc_label=None,
                                   join_key="{style} {colour}"), "t")
    codes = [l["code"] for l in oc_lines(sportlux.parse_checked(pdfs["fw26"]), cfg)]
    assert codes == ["ZXAA01W99JR01 9001", "ZXBB03W99FL07 9002", "ZXEH01W99FB02 9003"]
    ss = [l["code"] for l in oc_lines(sportlux.parse_checked(pdfs["ss26"]), cfg)]
    assert ss[:2] == ["ZXED001S99FAB001", "ZXAA001S99JER001"]   # ss26 prints no colour: the style alone


def test_an_ss26_code_that_carries_a_colour_code_is_split(path):
    def colour(rows):
        for r in rows[:3]:
            r["style"] = "ZXED001S99FAB0019004"
    l = lines(sportlux.parse_checked(ss26_pdf(path, edit=colour)))[0]
    assert (l["style"], l["colour"], l["colour_code"], l["colour_name"]) == ("ZXED001S99FAB001", "9004", "9004", None)


def test_an_fw26_style_code_with_no_colour_code_is_refused(path):
    probs = problems(fw26_pdf(path, edit=_row(1, style="ZXAA01W99JR01X")))
    assert any("ZXAA01W99JR01X L: the Style Code does not end in a 4-digit colour code" in p for p in probs)


def test_ss26_documents_carry_a_note_on_colour_blocks_and_fw26_none(pdfs):
    assert sportlux.parse_checked(pdfs["ss26"])[0]["notes"] == [sportlux.SS26_NOTE.format(doc="D-SO-999001")]
    assert sportlux.parse_checked(pdfs["fw26"])[0]["notes"] == []
    docs, checks, _ = service.read_oc(pdfs["ss26"], _cfg())
    assert {"Check": service.NOTES_CHECK, "Result": "WARN", "Detail": sportlux.SS26_NOTE.format(doc="D-SO-999001")} \
        in checks


def test_ss26_colour_blocks_whose_sizes_do_not_repeat_read_as_one_line(path):
    """The limit the note states: nothing printed tells the two blocks apart."""
    style = ("ZXAA001S99JER001", "TEST LOGO TEE", "RTW")
    docs = sportlux.parse_checked(ss26_pdf(path, lines=[style + ([("XS", 1), ("S", 1)], "43.21"),
                                                        style + ([("M", 1), ("L", 1)], "43.21")], split=(2,)))
    assert [l["sizes"] for l in lines(docs)] == [{"XS": 1, "S": 1, "M": 1, "L": 1}] and docs[0]["notes"]


def test_two_colour_codes_of_one_style_are_two_lines_even_under_one_colour_name(path):
    two = [("ZXAA01W99JR019001", "TEST TEE", "RTW", [("S", 1), ("M", 1)], "BLACK", "TURKEY", "241"),
           ("ZXAA01W99JR019004", "TEST TEE", "RTW", [("L", 1)], "BLACK", "TURKEY", "241")]
    ls = lines(sportlux.parse_checked(fw26_pdf(path, lines=two)))
    assert [(l["style"], l["colour"], l["sizes"]) for l in ls] == [("ZXAA01W99JR01", "9001", {"S": 1, "M": 1}),
                                                                   ("ZXAA01W99JR01", "9004", {"L": 1})]


def test_an_fw26_confirmation_printed_twice_is_refused(path):
    docs, probs = sportlux.parse(fw26_pdf(path, copies=2))
    assert [d["doc"] for d in docs] == ["Order Confirmation of 1 March 2099", "Order Confirmation of 1 March 2099 #2"]
    assert ("Order Confirmation of 1 March 2099 #2: repeats Order Confirmation of 1 March 2099 line for line and "
            "total for total (the same confirmation printed twice?), so it is refused") in probs


def test_an_fw26_confirmation_printed_twice_is_not_pointed_at_the_ai_reader(path):
    docs, checks, _ = service.read_oc(fw26_pdf(path, copies=2), _cfg())
    (f,) = service.fails(checks)
    assert docs is None and "line for line and total for total" in f["Detail"] and service.AI_HINT not in f["Detail"]


def test_two_different_fw26_confirmations_in_one_pdf_are_both_read(path):
    docs = sportlux.parse_checked(fw26_pdf(path, then=FW_LINES[:2]))
    assert [sportlux.totals([d])["units"] for d in docs] == [12, 11]


# ---- gates ------------------------------------------------------------------------------------------------------
def _row(n, **cells):
    def edit(rows):
        rows[n - 1].update(cells)
    return edit


def test_a_row_whose_qty_times_price_is_not_its_total_is_refused(path):
    probs = problems(ss26_pdf(path, edit=_row(2, total="91.11")))
    assert any("row '2' ZXED001S99FAB001: 2 x 1234.56 = 2469.12 != line value 91.11" in p for p in probs)


def test_fw26_row_gate(path):
    probs = problems(fw26_pdf(path, edit=_row(3, total="483.00")))
    assert any("ZXAA01W99JR019001 S: 2 x 241 = 482.00 != line value 483.00" in p for p in probs)


@pytest.mark.parametrize("grand,net,needle", [
    (("19", "5,457.06"), None, "its lines add up to 18 units, but it prints a total of 19"),
    (("18", "9,876.54"), "9,876.54", "its lines add up to EUR 5,457.06, but it prints a net total of EUR 9,876.54"),
    (None, "5,300.01", "Grand Total 5,457.06 and Net Total 5,300.01 differ with no discount printed"),
])
def test_lines_must_reproduce_the_printed_totals(path, grand, net, needle):
    assert any(needle in p for p in problems(ss26_pdf(path, grand=grand, net=net)))


def test_a_discount_is_refused_not_spread(path):
    with pytest.raises(otb.GateError) as e:
        sportlux.parse_checked(ss26_pdf(path, discount="57.07", net="5,399.99"))
    assert any("Discount 57.07 is not applied to any line" in p for p in e.value.problems)
    assert all(service.NOT_LAYOUT.search(p) for p in e.value.problems)


@pytest.mark.parametrize("cells,needle", [
    (dict(price="43,21", total="86,42"), "number format: unit price '43,21'"),
    (dict(total="1.234"), "number format: total '1.234'"),
])
def test_an_amount_not_printed_as_1234_56_is_a_number_format_refusal(path, cells, needle):
    probs = problems(ss26_pdf(path, edit=_row(4, **cells)))
    assert any(needle in p for p in probs) and any(service.NOT_LAYOUT.search(p) for p in probs)


def test_a_row_not_read_breaks_the_row_numbers(path):
    def drop(rows):
        rows.pop(4)
    probs = problems(ss26_pdf(path, edit=drop, split=(4, 2)))
    assert any("D-SO-999001: row numbers run from 4 to 6" in p for p in probs)


def test_a_missing_page_is_refused(path):
    probs = problems(ss26_pdf(path, drop_page=2))
    assert any("row numbers run from 4 to 8" in p for p in probs)
    assert any("its lines add up to 13 units, but it prints a total of 18" in p for p in probs)


def test_a_missing_last_page_leaves_no_printed_totals(path):
    probs = problems(ss26_pdf(path, drop_page=3))
    assert "D-SO-999001: no printed totals" in probs


def test_a_page_with_no_table_before_the_totals_is_refused(path):
    probs = problems(ss26_pdf(path, blank_page=2))
    assert any("D-SO-999001 p2: no order table on this page" in p for p in probs)


def test_a_page_after_the_totals_belongs_to_no_confirmation(path):
    assert "p4: belongs to no Sportlux order confirmation" in problems(ss26_pdf(path, extra_page=True))


def test_a_page_printed_again_after_the_totals_is_refused(path):
    assert "D-SO-999001 p4: a page of D-SO-999001 after its printed totals" in problems(ss26_pdf(path, repeat_last=True))


def test_a_page_in_the_other_template_is_a_layout_change(path):
    probs = problems(ss26_pdf(path, fw_header_page=2))
    assert "D-SO-999001 p2: a fw26 table header on a ss26 confirmation (layout changed)" in probs


def test_a_cell_across_a_column_rule_is_refused_not_moved(path):
    probs = problems(ss26_pdf(path, shift=(2, "qty", 18)))
    assert any("'2' sits across a column rule or outside the table" in p for p in probs)


def test_a_row_whose_rules_moved_is_refused(path):
    probs = problems(ss26_pdf(path, rule_dx=(3, 6)))
    assert any("the column rules of row '3 ZXED001S99FAB001" in p and "(layout changed)" in p for p in probs)


def test_a_header_word_across_a_column_rule_is_a_layout_change(path):
    probs = problems(ss26_pdf(path, header_dx=("Size", -22)))   # the titles still read in order, one is off its column
    assert sum("table header columns read" in p and "(layout changed)" in p for p in probs) == 3   # each page


def test_a_line_priced_two_ways_is_refused(path):
    probs = problems(ss26_pdf(path, edit=_row(3, price="1,199.99", total="1,199.99")))
    assert any("ZXED001S99FAB001 L: unit price 1199.99, but the line's earlier sizes are priced 1234.56" in p
               for p in probs)


def test_an_order_row_with_a_missing_cell_is_refused(path):
    probs = problems(ss26_pdf(path, edit=_row(5, size="")))
    assert any("row '5' ZXAA001S99JER001: no size in this order row" in p for p in probs)


# ---- wrong documents ------------------------------------------------------------------------------------------
def test_a_refused_payment_request_is_not_pointed_at_the_ai_reader(path):
    docs, checks, _ = service.read_oc(payment_pdf(path), _cfg())
    (f,) = service.fails(checks)
    assert docs is None and "is a payment request" in f["Detail"] and service.AI_HINT not in f["Detail"]


@pytest.mark.parametrize("title,number", [("PROFORMA INVOICE", "PI 999"), ("INVOICE", "D-SI-999999")])
def test_a_proforma_or_invoice_is_refused_by_name(path, title, number):
    docs, probs = sportlux.parse(payment_pdf(path, title, number))
    assert docs == [] and probs == [f"p1: {title} {number} is a payment request (Sportlux sends one for an advance "
                                    "and one per shipment), not the order confirmation, so it is refused; use the Order "
                                    "Confirmation (sizes, prices and totals of the whole order)"]


@pytest.mark.parametrize("build", [oc_synth.admilano_pdf, oc_synth.adidas_pdf, oc_synth.fog_pdf, oc_synth.otb_pdf])
def test_another_layout_is_no_confirmation_and_no_problem(tmp_path, build):
    assert sportlux.parse(build(str(tmp_path / "other.pdf"))) == ([], [])


@pytest.mark.parametrize("other", [admilano, adidas, fog, otb], ids=lambda m: m.__name__.rsplit(".", 1)[-1])
@pytest.mark.parametrize("template", ["ss26", "fw26"])
def test_no_other_reader_takes_either_template(pdfs, other, template):
    assert other.parse(pdfs[template])[0] == []


def test_a_pdf_with_no_text_is_refused_saying_so(path):
    p = Page(612, 792)
    vline(p, 100, 100, 200)
    assert sportlux.parse(write(path, [p])) == ([], [sportlux.NO_TEXT])


# ---- through the service ----------------------------------------------------------------------------------------
def _cfg(**kw):
    return {**base.BRAND, "adapter": "sportlux", "oc_supplier": ["Test General Trading FZCO"], "oc_label": None, **kw}


def test_the_service_reads_both_templates_and_holds_them_to_the_issuer(pdfs):
    for k in ("ss26", "fw26"):
        docs, checks, _ = service.read_oc(pdfs[k], _cfg())
        assert docs and not service.fails(checks) and "the Sportlux layout" in checks[0]["Detail"]
    docs, checks, _ = service.read_oc(pdfs["fw26"], _cfg(oc_supplier=["Other Trading FZCO"]))
    (f,) = service.fails(checks)
    assert docs is None and f["Check"] == service.IDENTITY_CHECK
    assert "is from 'TEST GENERAL TRADING FZCO', not Other Trading FZCO" in f["Detail"]


def test_a_config_that_names_a_label_is_refused_because_none_is_printed(pdfs):
    docs, checks, _ = service.read_oc(pdfs["ss26"], _cfg(oc_label=["Palm Angels"]))
    assert docs is None and "is for label 'none the reader could find'" in service.fails(checks)[0]["Detail"]


def test_the_service_refuses_another_currency(pdfs):
    docs, checks, _ = service.read_oc(pdfs["ss26"], _cfg(cost_currency="USD"))
    assert docs is None and [c["Check"] for c in service.fails(checks)] == [service.CURRENCY_CHECK]


def test_a_sportlux_config_must_name_its_issuer():
    with pytest.raises(brandm.BrandError, match="adapter: sportlux needs oc_supplier"):
        brandm.from_mapping(dict(base.BRAND, adapter="sportlux", oc_label=None), "t")


# ---- real PDFs, local only --------------------------------------------------------------------------------------
REAL = os.environ.get("OC_FIXED_PDF_DIR")
PAYMENT_GLOBS = ("PI *.pdf", "* PI [0-9]*.pdf", "INV-D-SI-*.pdf")


@pytest.mark.skipif(not REAL, reason="set OC_FIXED_PDF_DIR to a local folder of real order-confirmation PDFs")
def test_real_sportlux_payment_documents_are_refused_by_name():
    files = sorted({f for g in PAYMENT_GLOBS for f in glob.glob(os.path.join(REAL, g))})
    assert files, f"no Sportlux proforma or invoice in {REAL}"
    for f in files:
        docs, probs = sportlux.parse(f)
        assert docs == [] and probs and all("is a payment request" in p for p in probs), f
