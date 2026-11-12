"""The JOOR order print reader (oc/adapters/joor.py), on synthetic PDFs (tests/oc_synth.joor_pdf).

Real JOOR prints run in test_oc_adapters.py when OC_FIXED_PDF_DIR names a local folder of them
(named "JOOR *.pdf"), because order data is never committed to this public repo."""
import pdfplumber
import pytest

import test_oc_tool as base
import oc_synth
from oc import brand as brandm
from oc import service
from oc.adapters import admilano, adidas, fog, joor, otb


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    return oc_synth.joor_pdf(str(tmp_path_factory.mktemp("joor") / "joor.pdf"))


@pytest.fixture
def build(tmp_path):
    def make(**fault):
        return oc_synth.joor_pdf(str(tmp_path / "joor-fault.pdf"), **fault)
    return make


def lines(docs):
    return [l for d in docs for l in d["lines"]]


def problems_of(path):
    return joor.parse(path)[1]


# ---- reading ----------------------------------------------------------------------
def test_reproduces_the_printed_totals_of_both_orders(pdf):
    docs = joor.parse_checked(pdf)
    t = joor.totals(docs)
    assert (t["docs"], t["lines"], t["units"], t["value"]) == (2, 8, 21, 1679.37)
    assert (t["printed_units"], t["printed_value"]) == (21, 1679.37)
    assert [(d["doc"], d["total_qty"], d["gross"], d["net"]) for d in docs] == \
        [("99000001", 17, 1638.13, 1638.13), ("99000002", 4, 41.24, 41.24)]


def test_fixture_puts_two_size_shapes_on_one_set_of_columns(pdf):
    with pdfplumber.open(pdf) as p:
        ws = p.pages[0].extract_words()
    assert round(otb.xc(next(w for w in ws if w["text"] == "XS")), 1) == \
        round(otb.xc(next(w for w in ws if w["text"] == "44")), 1)


def test_each_style_reads_its_own_size_header(pdf):
    ls = lines(joor.parse_checked(pdf))
    assert (ls[0]["sizes"], ls[0]["size_run"]) == ({"S": 1, "M": 2, "L": 1}, ["XS", "S", "M", "L", "XL"])
    assert (ls[2]["sizes"], ls[2]["size_run"]) == ({"48": 2, "50": 1}, ["44", "46", "48", "50", "52"])


def test_a_header_wrapped_over_two_rows_is_one_size(pdf):
    ls = lines(joor.parse_checked(pdf))
    assert [(l["sizes"], l["size_run"]) for l in ls[3:5]] == [({"One Size": 2}, ["One Size"]),
                                                              ({"One Size": 1}, ["One Size"])]


def test_sizes_and_qty_rows_under_a_units_header(pdf):
    l = lines(joor.parse_checked(pdf))[5]
    assert (l["style"], l["sizes"], l["size_run"], l["qty"], l["line_value"], l["page"]) == \
        ("ZZ-A4", {"36": 2, "37": 1}, ["35", "35.5", "36", "36.5", "37"], 3, 600.69, 2)


def test_wholesale_wrapped_under_a_long_collection(pdf):
    l = lines(joor.parse_checked(pdf))[6]
    assert (l["style"], l["price"], l["rrp"]) == ("ZZ-A5", 30.29, 70.49)
    assert l["line_collection"] == "TEST APPAREL - A LONG COLLECTION NAME THAT RUNS PAST THE WHOLESALE COLUMN"


def test_a_collection_run_into_wholesale_is_split_from_it(pdf):
    with pdfplumber.open(pdf) as p:
        assert "(2099_TEST)Wholesale:" in {w["text"] for w in p.pages[0].extract_words()}
    l = lines(joor.parse_checked(pdf))[2]
    assert (l["line_collection"], l["price"], l["rrp"]) == ("TEST MAIN (2099_TEST)", 50.17, 125.43)


def test_colour_name_and_code(pdf):
    ls = lines(joor.parse_checked(pdf))
    assert [(l["colour"], l["colour_code"], l["colour_name"]) for l in ls] == [
        ("Z01", "Z01", "BLACK"), ("Z02", "Z02", "WHITE"), ("ZZA", "ZZA", "NOIR"),
        ("Z9Z99", "Z9Z99", "Z9Z99-Pearl Grey"), ("CHARCOAL", None, "CHARCOAL"), ("Z73", "Z73", "BRUN"),
        ("9Z999", "9Z999", "9Z999-Black"), ("Z76N", "Z76N", "NAVY")]
    assert all(l["fabric"] is None for l in ls)


def test_issuer_is_the_brand_contacts_email_domain_and_no_label_is_read(pdf):
    with pdfplumber.open(pdf) as p:
        assert "contact.person@brand.testTEST" in {w["text"] for w in p.pages[0].extract_words()}
    docs = joor.parse_checked(pdf)
    assert [(d["issuer"], d["label"]) for d in docs] == [("brand.test", None)] * 2
    assert docs[0]["brand_contact"] == "Contact: Test Person contact.person@brand.test"


def test_sugg_retail_is_an_rrp_in_its_printed_currency_and_zero_is_none(pdf):
    ls = lines(joor.parse_checked(pdf))
    assert [(l["rrp"], l["rrp_currency"]) for l in ls] == [
        (250.41, "EUR"), (250.41, "EUR"), (125.43, "EUR"), (850.47, "AUD"), (850.47, "AUD"), (None, None),
        (70.49, "EUR"), (30.53, "AUD")]
    assert ls[3]["rrp_found"] == "Sugg. Retail: AUD 850.47"


def test_document_fields(pdf):
    d1, d2 = joor.parse_checked(pdf)
    assert (d1["doc"], d1["po"], d1["status"], d1["pages"], d1["currency"], d1["cancelled"]) == \
        ("99000001", "99000001", "Pending", [1, 2, 3], "EUR", ["ZZ-A9"])
    assert (d2["doc"], d2["po"], d2["status"], d2["pages"]) == ("99000002", "ZZ9 - 99000002", "Approved", [4])
    assert (d1["created"], d1["start_ship"], d1["complete_ship"], d1["terms"]) == \
        ("01/02/2099", "02/03/2099", "03/04/2099", "TEST TERMS")
    l = d1["lines"][0]
    assert (l["deliver_from"], l["deliver_to"], l["product_class"], l["made_in"], l["description"]) == \
        ("02/03/2099", "03/04/2099", "JACKET", "TESTLAND", "TEST JACKET")
    assert d1["lines"][3]["description"] == "TEST SCARF"


def test_by_size_rows(pdf):
    rows = joor.by_size_rows(joor.parse_checked(pdf))
    assert len(rows) == 16 and rows[0] == {"Order confirmation": "99000001", "Collection": "TEST MAIN", "Line no.": 1,
                                           "Style": "ZZ-A1", "Colour": "Z01", "Colour name": "BLACK", "Size": "S",
                                           "Qty": 1, "Price EUR": 100.13, "Value EUR": 100.13}


# ---- gates ------------------------------------------------------------------------
def test_sizes_not_summing_to_the_printed_qty_are_refused(build):
    with pytest.raises(otb.GateError) as e:
        joor.parse_checked(build(qty_off=(0, 0, 1)))
    assert any("ZZ-A1: sizes sum 4 != Tot. Qty 5" in p for p in e.value.problems)


def test_qty_times_price_not_the_line_value_is_refused(build):
    with pytest.raises(otb.GateError) as e:
        joor.parse_checked(build(value_off=(1, 0, 10.0)))
    assert any("ZZ-A2: 3 x 50.17 = 150.51 != line value 160.51" in p for p in e.value.problems)


def test_lines_not_summing_to_the_printed_totals_are_refused(build):
    probs = problems_of(build(units_off=1, value_off_doc=5.0))
    assert f"99000001: its lines add up to 17 units, but it prints a total of 18. {otb.NOT_ADDING_UP}" in probs
    assert (f"99000001: its lines add up to EUR 1,638.13, but it prints a net total of EUR 1,643.13. "
            f"{otb.NOT_ADDING_UP}") in probs


def test_a_grand_total_other_than_the_subtotal_is_refused_not_spread(build):
    probs = problems_of(build(grand_off=-35.0))
    assert "99000001: Grand Total EUR 1,603.13 is not the Subtotal 1,638.13 (a discount or charge); it is not " \
           "applied to any line" in probs
    assert all(service.NOT_LAYOUT.search(p) for p in probs)


def test_a_style_total_row_that_disagrees_with_its_colours_is_refused(build):
    probs = problems_of(build(subtotal_off=(0, 1)))
    assert any("ZZ-A1: the style's Total row prints" in p for p in probs)


def test_a_quantity_off_its_column_is_refused(build):
    probs = problems_of(build(shift=(0, 0, 2, 9)))
    assert any("ZZ-A1: qty 2@" in p and "pt from column" in p for p in probs)


def test_a_quantity_moved_onto_an_empty_neighbour_passes_every_sum_but_the_style_total_row(build):
    probs = problems_of(build(shift=(0, 0, 3, oc_synth.JOOR_PITCH)))
    assert probs == ["99000001 p1 ZZ-A1: the style's Total row prints {'XS': 1, 'S': 2, 'M': 2, 'L': 1, 'XL': 0}, 6, "
                     "600.78; its colours add up to {'XS': 1, 'S': 2, 'M': 2, 'L': 0, 'XL': 1}, 6, 600.78"]


def test_a_quantity_moved_onto_a_filled_neighbour_is_refused(build):
    probs = problems_of(build(shift=(0, 0, 1, oc_synth.JOOR_PITCH)))
    assert any("ZZ-A1: sizes sum 13 != Tot. Qty 4" in p for p in probs)


def test_two_quantities_under_one_size_are_refused(build):
    assert any("ZZ-A1: two quantities under size M" in p for p in problems_of(build(extra_qty=(0, 0, 2))))


def test_a_quantity_off_its_column_in_a_sizes_row_is_refused(build):
    probs = problems_of(build(shift=(3, 0, 2, 9)))
    assert any("ZZ-A4: qty 2@" in p and "pt from column" in p for p in probs)


def test_a_sizes_row_with_no_qty_row_is_refused(build):
    assert any("ZZ-A4 Z73: no Sizes and Qty rows under the colour" in p for p in problems_of(build(no_qty_row=True)))


def test_a_placeholder_wholesale_is_refused(build):
    assert any("ZZ-A2: wholesale EUR 0.01 is a placeholder, not a price" in p for p in problems_of(build(placeholder=1)))


def test_a_wholesale_in_another_currency_is_refused(build):
    assert any("ZZ-A2: wholesale in USD, the order is in EUR" in p for p in problems_of(build(price_cur=(1, "USD"))))


def test_a_line_value_in_another_currency_is_refused(build):
    assert any("ZZ-A1: line value in USD, the order is in EUR" in p
               for p in problems_of(build(amount_cur=(0, 1, "USD"))))


# ---- pages and documents ------------------------------------------------------------------
def test_a_missing_middle_page_is_refused(build):
    probs = problems_of(build(drop_page=2))
    assert any("99000001 p2: Page 3 of 3 follows Page 1 of 3" in p for p in probs)


def test_a_missing_last_page_is_refused(build):
    assert "99000001: the PDF stops at Page 2 of 3; page(s) 3-3 are missing" in problems_of(build(drop_page=3))


def test_a_missing_first_page_is_refused(build):
    assert any("starts at Page 2 of 3" in p for p in problems_of(build(drop_page=1)))


def test_a_page_of_no_order_is_refused(build):
    assert any(p.startswith("p2: no JOOR page head") and "belongs to no order" in p
               for p in problems_of(build(foreign=True)))


def test_an_order_printed_twice_is_refused(build):
    assert any("99000002 p5: this order is printed twice" in p for p in problems_of(build(twice=True)))


def test_a_style_after_the_grand_total_is_refused_unless_cancelled(build):
    assert any("a style after the order's Grand Total" in p for p in problems_of(build(after_total=True)))


def test_a_pdf_with_no_text_layer_says_so(tmp_path):
    path = oc_synth.write(str(tmp_path / "scan.pdf"), [oc_synth.Page(595, 842)])
    assert joor.parse(path) == ([], ["the PDF has no text layer (a scan?); this reader reads printed text by position"])


@pytest.mark.parametrize("other", [oc_synth.admilano_pdf, oc_synth.adidas_pdf, oc_synth.fog_pdf])
def test_another_layout_is_not_a_joor_order(tmp_path, other):
    path = other(str(tmp_path / "other.pdf"))
    assert joor.parse(path) == ([], [])
    with pytest.raises(otb.GateError, match="no order confirmations found"):
        joor.parse_checked(path)


@pytest.mark.parametrize("mod", [otb, admilano, adidas, fog])
def test_other_readers_find_no_order_in_a_joor_print(pdf, mod):
    assert mod.parse(pdf)[0] == []


# ---- status ----------------------------------------------------------------------
def test_pending_orders_are_read_and_warned_approved_ones_are_not(pdf):
    docs = joor.parse_checked(pdf)
    assert joor.reader_warnings(docs) == [(joor.PENDING_CHECK, "99000001 is Pending in JOOR: the brand has not approved "
                                                               "it yet, so its quantities and prices can still change. "
                                                               "Check the draft against the approved order.")]


def test_a_page_with_another_status_is_refused(build):
    assert "99000001 p2: status 'Approved', page 1 says 'Pending'" in problems_of(build(status_p2="Approved"))


@pytest.mark.parametrize("fault, needle", [(dict(placeholder=1), "is a placeholder, not a price"),
                                           (dict(twice=True), "this order is printed twice"),
                                           (dict(status="Cancelled"), "is neither Pending nor Approved"),
                                           (dict(subtotal_off=(0, 1)), "the style's Total row prints")])
def test_a_refusal_for_what_the_print_is_never_points_at_the_ai_reader(build, fault, needle):
    """Review 2026-11-13-buyer-15 F2: the reader marks these problems NotLayout, so the service adds no AI hint."""
    cfg = brandm.from_mapping(dict(base.BRAND, adapter="joor", oc_supplier="brand.test"), "t")
    docs, checks, _ = service.read_oc(build(**fault), cfg)
    hit = [c["Detail"] for c in checks if c["Check"] == service.GATE_CHECK and needle in c["Detail"]]
    assert docs is None and hit and not any(service.AI_HINT in d for d in hit)


def test_a_status_other_than_pending_or_approved_is_refused(build):
    assert "99000001: JOOR status 'Cancelled' is neither Pending nor Approved" in problems_of(build(status="Cancelled"))


# ---- through the service and a brand config -------------------------------------------------------
def _cfg(**extra):
    cfg = {k: v for k, v in base.BRAND.items() if k != "oc_label"}
    cfg.update(dict(adapter="joor", cost_currency="EUR", oc_supplier="brand.test"), **extra)
    return brandm.from_mapping(cfg, "t")


def test_a_joor_config_needs_oc_supplier():
    with pytest.raises(brandm.BrandError, match="adapter: joor needs oc_supplier"):
        brandm.from_mapping(dict(base.BRAND, adapter="joor"), "t")


def test_the_service_reads_a_joor_print_with_a_pending_warning(pdf):
    docs, checks, info = service.read_oc(pdf, _cfg())
    assert len(docs) == 2 and info is None
    assert [(c["Check"], c["Result"]) for c in checks] == [(service.READER_CHECK, "INFO"),
                                                           (joor.PENDING_CHECK, "WARN")]
    assert "JOOR order print" in checks[0]["Detail"] and checks[1]["Detail"].startswith("99000001 is Pending")


def test_the_service_refuses_another_brands_joor_print(pdf):
    docs, checks, _ = service.read_oc(pdf, _cfg(oc_supplier="other.test"))
    assert docs is None
    assert checks[-1]["Check"] == service.IDENTITY_CHECK and "99000001 is from 'brand.test'" in checks[-1]["Detail"]


def test_a_joor_config_with_an_oc_label_refuses_because_joor_reads_none(pdf):
    docs, checks, _ = service.read_oc(pdf, _cfg(oc_label="TEST"))
    assert docs is None and "label 'none the reader could find'" in checks[-1]["Detail"]


def test_a_style_prefix_the_lines_carry_passes(pdf):
    docs, _, _ = service.read_oc(pdf, _cfg(oc_style_prefix=["XX-", "ZZ-"]))
    assert len(docs) == 2


def test_a_style_prefix_tells_apart_brands_sharing_an_agency(pdf):
    docs, checks, _ = service.read_oc(pdf, _cfg(oc_style_prefix="ZZ-B"))
    assert docs is None and checks[-1]["Check"] == service.IDENTITY_CHECK
    assert checks[-1]["Detail"].startswith("99000001 has 7 style(s) not starting ZZ-B (ZZ-A1, ZZ-A1, ZZ-A2); "
                                           "99000002 has 1 style(s) not starting ZZ-B (ZZ-A10)")


def test_an_empty_style_prefix_is_refused_at_load():
    with pytest.raises(brandm.BrandError, match="oc_style_prefix is a style-number prefix"):
        _cfg(oc_style_prefix=[""])


def test_the_service_refuses_a_joor_print_in_another_currency(pdf):
    docs, checks, _ = service.read_oc(pdf, _cfg(cost_currency="USD"))
    assert docs is None and checks[-1]["Check"] == service.CURRENCY_CHECK


def test_a_layout_problem_points_at_the_ai_box(build):
    docs, checks, _ = service.read_oc(build(drop_page=2), _cfg())
    fails = [c["Detail"] for c in checks if c["Result"] == "FAIL"]
    assert docs is None and fails and all(f.endswith(service.AI_HINT) for f in fails if "follows Page" in f)
