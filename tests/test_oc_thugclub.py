"""THUG CLUB Corp. proforma invoices: the thugclub reader. Synthetic PDFs only (tests/thug_synth.py).

The real PDFs are covered by test_oc_adapters.py when OC_FIXED_PDF_DIR points at a local folder of them, and
the real commercial invoices by the refusal test at the end of this file."""
import copy
import glob
import os

import pytest

import oc_synth
import test_oc_tool as base
import thug_synth as T
from oc import brand as brandm, service
from oc.adapters import admilano, adidas, fog, otb, thugclub

COLLAB = [("TBU", ["TC x ZZ TEST JACKET (RIVET VERSION)"], "Jacket", "Sage", ["TBU"], {"S": 1, "M": 2}, 107.0, 200),
          ("TBU", ["TC x ZZ TEST PARKA"], "Jacket", "Black", ["TBU"], {"M": 1, "L": 1}, 155.0, 300)]
BRAND = dict(base.BRAND, adapter="thugclub", cost_currency="USD", vendor="EXAMPLE CLUB", oc_label=["THUG CLUB"],
             _name="ex")


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    d = tmp_path_factory.mktemp("thug")
    return dict(portrait=T.portrait_pdf(str(d / "p.pdf")), landscape=T.landscape_pdf(str(d / "l.pdf")),
                collab=T.portrait_pdf(str(d / "c.pdf"), lines=COLLAB, sizes=["S", "M", "L", "XL", "2XL"], hs=False,
                                      number="TCFW99PZZ0001"))


@pytest.fixture
def pdf(tmp_path):
    def make(kind="portrait", **kw):
        build = dict(portrait=T.portrait_pdf, landscape=T.landscape_pdf, two=T.two_docs_pdf, blank=T.blank_pdf)[kind]
        return build(str(tmp_path / f"{kind}.pdf"), **kw)
    return make


def _units(doc, got, printed):
    return f"{doc}: its lines add up to {got} units, but it prints a total of {printed}. {otb.NOT_ADDING_UP}"


def _net(doc, got, printed):
    return f"{doc}: its lines add up to {got}, but it prints a net total of {printed}. {otb.NOT_ADDING_UP}"



def lines(docs):
    return [l for d in docs for l in d["lines"]]


def problems(path):
    return thugclub.parse(path)[1]


# ---- reads -------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind,want", [("portrait", (1, 4, 17, 1653.0)), ("landscape", (1, 5, 29, 3271.0)),
                                       ("collab", (1, 2, 5, 631.0))])
def test_synthetic_reproduces_printed_totals(pdfs, kind, want):
    t = thugclub.totals(thugclub.parse_checked(pdfs[kind]))
    assert (t["docs"], t["lines"], t["units"], t["value"]) == want
    assert (t["printed_units"], t["printed_value"]) == want[2:]


def test_portrait_quantities_sit_under_their_own_size_cells(pdfs):
    ls = lines(thugclub.parse_checked(pdfs["portrait"]))
    assert [l["sizes"] for l in ls] == [{"2": 1, "3": 1}, {"1": 2, "2": 3, "3": 2, "4": 1}, {"OS": 4}, {"1": 1, "2": 2}]
    assert ls[0]["size_run"] == ["OS", "1", "2", "3", "4"]


def test_landscape_reads_os_and_size_5_and_blank_cells(pdfs):
    ls = lines(thugclub.parse_checked(pdfs["landscape"]))
    assert ls[2]["sizes"] == {"OS": 5}
    assert ls[4]["sizes"] == {"1": 2, "2": 2, "5": 1}
    assert ls[0]["size_run"] == ["OS", "1", "2", "3", "4", "5"]


def test_collab_reads_its_own_size_header(pdfs):
    ls = lines(thugclub.parse_checked(pdfs["collab"]))
    assert [l["sizes"] for l in ls] == [{"S": 1, "M": 2}, {"M": 1, "L": 1}]
    assert ls[0]["size_run"] == ["S", "M", "L", "XL", "2XL"]


def test_portrait_header_on_page_one_serves_page_two(pdfs):
    l = lines(thugclub.parse_checked(pdfs["portrait"]))[3]
    assert (l["style"], l["page"], l["sizes"], l["price"]) == ("ZZ99PT0004", 2, {"1": 1, "2": 2}, 155.0)


def test_landscape_wrapped_name_and_composition_are_joined(pdfs):
    ls = lines(thugclub.parse_checked(pdfs["landscape"]))
    assert [l["description"] for l in ls] == ["TEST FIELD JACKET", "TC TEST LOGO T-SHIRT", "TEST BEANIE",
                                             "TEST WORK PANTS", "TEST TANK TOP"]
    assert ls[1]["composition"] == "SHELL 1: COTTON 100% SHELL 2: COTTON 96%, SPAN 4%"
    assert ls[3]["composition"] == "SHELL 1: COTTON 100% SHELL 2: COTTON 100% SHELL 3: COWHIDE 100%"
    assert ls[3]["page"] == 2


def test_portrait_material_row_is_the_composition(pdfs):
    ls = lines(thugclub.parse_checked(pdfs["portrait"]))
    assert [l["composition"] for l in ls] == ["Cotton 100%", "Cotton 100%", "Cotton 100%", "Cotton 100% / Cowhide 100%"]
    assert [l["description"] for l in ls] == ["TEST DENIM JACKET", "TEST TEE", "TEST CAP", "TEST PANTS"]


def test_line_fields(pdfs):
    l = lines(thugclub.parse_checked(pdfs["portrait"]))[3]
    assert (l["product_class"], l["colour"], l["made_in"], l["hs_code"], l["fabric"], l["colour_code"]) == \
        ("Pants", "Black/White", "South Korea", "6100.00.0000", None, None)
    assert (l["qty"], l["price"], l["line_value"]) == (3, 155.0, 465.0)


def test_msrp_is_the_rrp_with_its_own_currency(pdfs):
    pl = lines(thugclub.parse_checked(pdfs["portrait"]))
    assert [(l["rrp"], l["rrp_currency"]) for l in pl] == [(640.0, "USD"), (100.0, "USD"), (95.0, "USD"), (320.0, "USD")]
    ll = lines(thugclub.parse_checked(pdfs["landscape"]))
    assert {(l["rrp"], l["rrp_currency"]) for l in ll} == {(None, None)}


def test_doc_fields_issuer_and_label(pdfs):
    d = thugclub.parse_checked(pdfs["portrait"])[0]
    assert (d["doc"], d["issuer"], d["label"], d["currency"], d["collection"], d["date"], d["order_date"],
            d["delivery_window"], d["pages"]) == \
        ("TCFW99P0001", "THUG CLUB Corp.", "THUG CLUB", "USD", "FW99", "2099-03-14", "2099-02-20",
         "Mid to late August", [1, 2])
    d = thugclub.parse_checked(pdfs["landscape"])[0]
    assert (d["doc"], d["issuer"], d["label"], d["collection"], d["delivery_window"]) == \
        ("TCSS99P0001", "THUG CLUB CORP.", "THUG CLUB", "SS99", "EARLY TO MID MARCH 2099")


def test_collab_label_is_read_from_the_product_names(pdfs):
    d = thugclub.parse_checked(pdfs["collab"])[0]
    assert (d["label"], d["issuer"]) == ("TC x ZZ", "THUG CLUB Corp.")
    mixed = copy.deepcopy(d)
    mixed["lines"].append(dict(mixed["lines"][0], description="TEST TEE"))
    assert thugclub.doc_label(mixed) == "TC x ZZ + THUG CLUB"


def test_two_proformas_in_one_pdf_each_against_its_own_totals(pdf):
    docs = thugclub.parse_checked(pdf("two"))
    assert [(d["doc"], d["pages"], thugclub.totals([d])["units"]) for d in docs] == \
        [("TCFW99P0001", [1, 2], 17), ("TCFW99P0002", [3, 4], 10)]
    probs = problems(pdf("two", bump_second=True))
    assert probs == [_units("TCFW99P0002", 10, 11)]


# ---- every gate ---------------------------------------------------------------------------------
def test_quantity_off_its_column_is_refused(pdf):
    probs = problems(pdf(shift=(2, "3", 6)))
    assert any("ZZ99TS0002: qty 2@" in p and "pt from column" in p for p in probs)


def test_quantity_off_its_column_is_refused_in_landscape(pdf):
    probs = problems(pdf("landscape", shift=(2, "2", 5)))
    assert any("ZZ99TS0102: qty 4@" in p and "pt from column" in p for p in probs)


def test_two_quantities_pushed_under_one_size_fuse_and_trip_the_sizes_gate(pdf):
    # Two digits within TOL of one centre overlap, so pdfplumber reads them as one number ("32"), never as two
    # quantities under one size; the line's sizes then cannot sum to its printed units.
    probs = problems(pdf(shift=(2, "3", -13.8)))
    assert "TCFW99P0001 line 2 ZZ99TS0002: sizes sum 35 != Tot. Qty 8" in probs


def test_dropped_size_trips_the_sizes_gate(pdf):
    probs = problems(pdf(drop=(2, "2")))
    assert "TCFW99P0001 line 2 ZZ99TS0002: sizes sum 5 != Tot. Qty 8" in probs


def test_wrong_line_total_trips_the_value_gate(pdf):
    probs = problems(pdf(bump_total=1))
    assert any("ZZ99JK0001: 2 x 305.0 = 610.00 != line value 611.0" in p for p in probs)


def test_printed_units_and_value_are_gates(pdf):
    assert problems(pdf(printed_units=18)) == [_units("TCFW99P0001", 17, 18)]
    assert problems(pdf(printed_value=1654.0)) == [_net("TCFW99P0001", "USD 1,653.00", "USD 1,654.00")]
    assert problems(pdf("landscape", printed_units=30)) == [_units("TCSS99P0001", 29, 30)]


@pytest.mark.parametrize("corrupt", ["size", "price", "drop", "units", "net"])
def test_corrupting_a_parsed_doc_trips_the_shared_gate(pdfs, corrupt):
    docs = thugclub.parse_checked(pdfs["landscape"])
    assert otb.gate_problems(docs) == []
    d = copy.deepcopy(docs)[0]
    if corrupt == "size":
        d["lines"][0]["sizes"]["2"] += 1
    elif corrupt == "price":
        d["lines"][-1]["price"] += 1
    elif corrupt == "drop":
        d["lines"].pop()
    else:
        d["total_qty" if corrupt == "units" else "net"] += 1
    assert otb.gate_problems([d])


def test_a_missing_last_page_leaves_no_printed_totals(pdf):
    probs = problems(pdf(drop_last_page=True))
    assert "TCFW99P0001: no printed totals" in probs


def test_a_missing_middle_page_trips_the_totals(pdf):
    probs = problems(pdf("landscape", per_page=(2, 2, 1), drop_middle_page=True))
    assert probs == [_units("TCSS99P0001", 20, 29),
                     _net("TCSS99P0001", "USD 1,628.00", "USD 3,271.00")]


def test_a_page_after_the_totals_is_a_problem(pdf):
    assert problems(pdf(extra_page=True)) == ["TCFW99P0001 p3: a page after the printed totals that starts no new "
                                              "proforma invoice"]


def test_a_page_before_the_proforma_belongs_to_no_document(pdf):
    assert problems(pdf(cover=True)) == ["p1: belongs to no THUG CLUB proforma invoice"]


def test_a_discount_is_refused_not_spread(pdf):
    probs = problems(pdf(discount=True))
    assert any("a discount is not applied to any line" in p for p in probs)


# ---- layout and recognition ----------------------------------------------------------------------
def test_an_unknown_header_word_is_a_layout_change(pdf):
    probs = problems(pdf(header_word="Season"))
    assert probs[0] == "TCFW99P0001 p1: table header word 'Season' at x=150 is not one this reader knows (layout changed)"


def test_a_wrapped_piece_under_no_cell_is_refused(pdf):
    probs = problems(pdf(stray_wrap=2))
    assert any("'Cotton 100%' at x=76 under line 2 ZZ99TS0002 starts under none of its cells" in p for p in probs)


def test_values_with_no_style_code_are_refused(pdf):
    probs = problems(pdf(values_no_style=True))
    assert any(p.startswith("TCFW99P0001 p1: values with no style code: 'TEST TEE") for p in probs)


def test_a_word_in_the_style_column_that_is_no_code_is_refused(pdf):
    probs = problems(pdf(bad_style=(2, "see note")))
    assert any(p.startswith("TCFW99P0001 p1: values with no style code: 'see note TEST TEE") for p in probs)


def test_an_order_line_after_the_totals_is_refused(pdf):
    probs = problems(pdf(after_totals=True))
    assert probs == ["TCFW99P0001 p2: an order line after the printed totals: 'ZZ99JK0001 TEST DENIM JACKET 2 $305.00 "
                     "$610.00'"]


def test_a_money_column_with_no_currency_is_refused(pdf):
    probs = problems(pdf(currency=None))
    assert any("the money columns print currencies ['None'], need one each and the same" in p for p in probs)


def test_a_header_currency_other_than_usd_is_read_as_printed(pdf):
    assert thugclub.parse_checked(pdf(currency="EUR"))[0]["currency"] == "EUR"


def test_a_pdf_with_no_text_layer_says_so(pdf):
    assert problems(pdf("blank", n=2)) == ["the PDF has no text layer (a scan?); this reader reads the PDF's text, "
                                           "so nothing was read"]


@pytest.mark.parametrize("title", ["COMMERCIAL INVOICE", "PACKING LIST", "CREDIT NOTE"])
def test_other_thug_club_paperwork_is_refused_as_not_an_order(pdf, title):
    docs, probs = thugclub.parse(pdf(title=title))
    assert docs == [] and probs == [f"p1: THUG CLUB {title} TCFW99P0001 is not an order confirmation: only the "
                                    "PROFORMA INVOICE with sizes and prices is the order"]


def test_a_proforma_with_no_size_grid_is_refused_as_not_an_order(pdf):
    docs, probs = thugclub.parse(pdf(no_sizes=True))
    assert docs == [] and probs == ["p1: THUG CLUB PROFORMA INVOICE TCFW99P0001 prints no size grid (sizes, units, "
                                    "unit price and total per line), so it is not an order confirmation: a deposit "
                                    "or other invoice"]


def test_a_proforma_with_no_table_header_is_a_layout_change_not_a_refusal(pdf):
    probs = problems(pdf(no_header=True))
    assert probs[0] == ("TCFW99P0001 p1: no order table header (Style Code, sizes, Units, unit price, total) on the "
                        "proforma's first page (layout changed)")
    assert not any("not an order confirmation" in p for p in probs)


@pytest.mark.parametrize("build", [oc_synth.admilano_pdf, oc_synth.adidas_pdf, oc_synth.fog_pdf])
def test_other_layouts_are_not_read(tmp_path, build):
    assert thugclub.parse(build(str(tmp_path / "other.pdf"))) == ([], [])


@pytest.mark.parametrize("mod", [admilano, adidas, fog, otb])
def test_other_readers_find_nothing_in_a_thug_club_proforma(pdfs, mod):
    for kind in ("portrait", "landscape"):
        assert mod.parse(pdfs[kind])[0] == []


# ---- through the service -------------------------------------------------------------------------
def test_service_reads_it_with_the_thug_club_layout_row(pdfs):
    docs, checks, info = service.read_oc(pdfs["portrait"], BRAND)
    assert docs and info is None and not service.fails(checks)
    assert "THUG CLUB Corp. proforma invoice layout" in checks[0]["Detail"]


def test_service_holds_the_label_to_the_config(pdfs):
    docs, checks, _ = service.read_oc(pdfs["collab"], BRAND)
    (f,) = service.fails(checks)
    assert docs is None and f["Check"] == service.IDENTITY_CHECK
    assert "TCFW99PZZ0001 is for label 'TC x ZZ', not THUG CLUB" in f["Detail"]
    docs, checks, _ = service.read_oc(pdfs["collab"], dict(BRAND, oc_label=["THUG CLUB", "TC x ZZ"]))
    assert docs and not service.fails(checks)


def test_service_holds_the_issuer_to_the_config(pdfs):
    docs, checks, _ = service.read_oc(pdfs["landscape"], dict(BRAND, oc_supplier=["THUG CLUB Corp."]))
    assert docs and not service.fails(checks)
    docs, checks, _ = service.read_oc(pdfs["landscape"], dict(BRAND, oc_supplier=["Other Corp."]))
    assert docs is None and "is from 'THUG CLUB CORP.', not Other Corp." in service.fails(checks)[0]["Detail"]


def test_service_refuses_a_proforma_in_another_currency(pdf):
    docs, checks, _ = service.read_oc(pdf(currency="EUR"), BRAND)
    assert docs is None and [c["Check"] for c in service.fails(checks)] == [service.CURRENCY_CHECK]


def test_service_does_not_point_a_commercial_invoice_at_the_ai_reader(pdf):
    docs, checks, _ = service.read_oc(pdf(title="COMMERCIAL INVOICE"), BRAND)
    (f,) = service.fails(checks)
    assert docs is None and "not an order confirmation" in f["Detail"] and service.AI_HINT not in f["Detail"]


def test_service_points_a_layout_change_at_the_ai_reader(pdf):
    docs, checks, _ = service.read_oc(pdf(header_word="Season"), BRAND)
    assert docs is None and all(service.AI_HINT in f["Detail"] for f in service.fails(checks)
                                if "layout changed" in f["Detail"])


def test_a_thugclub_config_needs_oc_label():
    with pytest.raises(brandm.BrandError, match="adapter: thugclub needs oc_label"):
        brandm.from_mapping(dict(base.BRAND, adapter="thugclub", oc_label=None), "t")
    assert brandm.from_mapping(dict(base.BRAND, adapter="thugclub", oc_label="THUG CLUB"), "t")["oc_label"] == \
        ["THUG CLUB"]


# ---- real commercial invoices, local only ---------------------------------------------------------
REAL = os.environ.get("OC_FIXED_PDF_DIR")


@pytest.mark.skipif(not REAL, reason="set OC_FIXED_PDF_DIR to a local folder of real order-confirmation PDFs")
def test_real_thug_club_commercial_invoices_are_refused():
    files = sorted(glob.glob(os.path.join(REAL, "*THUGCLUB_*_CI*_MARAIS_*.pdf")))
    assert files, f"no THUG CLUB commercial invoice in {REAL}"
    for f in files:
        docs, probs = thugclub.parse(f)
        assert docs == [] and len(probs) == 1 and "COMMERCIAL INVOICE" in probs[0] \
            and "not an order confirmation" in probs[0], f
