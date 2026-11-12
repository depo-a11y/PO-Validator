"""The "#3xxxxxx" ordering-platform reader (oc/adapters/p3m.py) on synthetic PDFs (tests/oc_synth_p3m.py).

Real PDFs run in test_oc_adapters.py when OC_FIXED_PDF_DIR is set, named P3M_*.pdf there."""
import pdfplumber
import pytest

import oc_synth
import oc_synth_p3m as S
from oc import brand as brandm
from oc import service
from oc.adapters import admilano, adidas, fog, otb, p3m


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    d = tmp_path_factory.mktemp("p3m")
    return {"grid": S.grid_pdf(str(d / "grid.pdf")), "inline": S.inline_pdf(str(d / "inline.pdf"))}


@pytest.fixture
def build(tmp_path):
    def make(kind="grid", **fault):
        return (S.grid_pdf if kind == "grid" else S.inline_pdf)(str(tmp_path / f"{kind}-fault.pdf"), **fault)
    return make


def problems(path):
    return p3m.parse(path)[1]


def lines(docs):
    return [l for d in docs for l in d["lines"]]


def brand(**kw):
    cfg = dict(vendor="TEST VENDOR", adapter="p3m", season="F/W 99", sku_prefix="ZX99", cost_multiplier=1.5,
               cost_currency="USD", margin_floor=2.0, oc_supplier="TEST Trading Co", join_key="{style}")
    cfg.update(kw)
    return brandm.from_mapping(cfg, "test config", "test")


# ---- reading ------------------------------------------------------------------------
@pytest.mark.parametrize("kind,want", [("grid", (1, 6, 33, 2425.0)), ("inline", (1, 3, 12, 166725.0))])
def test_both_layouts_reproduce_printed_totals(pdfs, kind, want):
    t = p3m.totals(p3m.parse_checked(pdfs[kind]))
    assert (t["docs"], t["lines"], t["units"], t["value"]) == want
    assert (t["printed_units"], t["printed_value"]) == want[2:]


def test_grid_fixture_puts_two_size_shapes_on_one_set_of_cells(pdfs):
    with pdfplumber.open(pdfs["grid"]) as pdf:
        words = pdf.pages[0].extract_words()
    xs = next(round(otb.xc(w), 1) for w in words if w["text"] == "XS")
    assert xs == next(round(otb.xc(w), 1) for w in words if w["text"] == "S-M")


def test_grid_reads_each_line_against_its_own_size_header(pdfs):
    ls = lines(p3m.parse_checked(pdfs["grid"]))
    assert ls[0]["sizes"] == {"S": 1, "M": 2, "L": 1}
    assert ls[1]["sizes"] == {"S-M": 5, "M-L": 5}
    assert ls[1]["size_run"] == ["XS-S", "S-M", "M-L"]


def test_grid_fixture_runs_full_cell_labels_together_at_default_tolerance(pdfs):
    with pdfplumber.open(pdfs["grid"]) as pdf:
        texts = {w["text"] for w in pdf.pages[1].extract_words()}
    assert "28x3429x3430x3431x34" in texts


def test_grid_splits_labels_that_fill_their_cells(pdfs):
    l = lines(p3m.parse_checked(pdfs["grid"]))[3]
    assert l["size_run"] == ["28x34", "29x34", "30x34", "31x34"]
    assert l["sizes"] == {"29x34": 1, "30x34": 2, "31x34": 1}


def test_a_label_cut_short_is_expanded_only_to_its_one_full_label(pdfs):
    assert lines(p3m.parse_checked(pdfs["grid"]))[5]["sizes"] == {"one size": 3}
    assert p3m.expand("one s…") == "one size"
    assert p3m.expand("XL…") == "XL…"


def test_grid_variant_block_runs_across_a_page_break(pdfs):
    l = lines(p3m.parse_checked(pdfs["grid"]))[2]
    assert (l["style"], l["colour"], l["sizes"], l["page"]) == ("ZZSO001_white", "white", {"S-M": 5, "M-L": 5}, 2)


def test_grid_fields(pdfs):
    d = p3m.parse_checked(pdfs["grid"])[0]
    assert (d["doc"], d["kind"], d["issuer"], d["label"], d["currency"], d["season"], d["date"]) == \
        ("3999001", "ORDER CONFIRMATION", "TEST Trading Co", None, "USD", "FW99", "Jan 1, 2099")
    assert (d["pages"], d["terms_pages"], d["deliver_from"], d["deliver_to"]) == ([1, 2], [3], "Jul 1", "Sep 30, 2099")
    l1, l2, l4, l5 = d["lines"][0], d["lines"][1], d["lines"][3], d["lines"][4]
    assert (l1["style"], l1["fabric"], l1["colour"], l1["made_in"], l1["product_class"], l1["composition"]) == \
        ("ZZTE001-001N-001", None, "BLACK", "USA", "T-SHIRT", "100% COTTON")
    assert (l1["rrp"], l1["rrp_currency"], l4["rrp"], l4["line_value"]) == (255.0, "USD", 1105.0, 1212.0)
    assert (l2["made_in"], l2["composition"]) == (None, "78% cotton, 20% polyester, 2% elastane")
    assert l4["meta"] == {"COLLECTION": "CORE", "PRINT/FABRIC": "Cotton", "CATEGORY": "Denim", "ORIGIN": "US"}
    assert (l5["style"], l5["colour"], l5["sizes"], l5["rrp"]) == ("ZZDN002", "Black Rinse", {"28x34": 1, "29x34": 1}, 1105.0)


def test_inline_fields_and_cover(pdfs):
    d = p3m.parse_checked(pdfs["inline"])[0]
    assert (d["doc"], d["layout"], d["issuer"], d["label"], d["currency"]) == \
        ("3999002", "inline", "TEST CO.,LTD.", None, "JPY")
    assert (d["cover_pages"], d["pages"], d["cover_qty"], d["cover_value"]) == ([1], [2, 3], 12, 166725.0)
    l = d["lines"][0]
    assert (l["style"], l["colour"], l["sizes"], l["made_in"], l["composition"]) == \
        ("ZZ8BL001", "BLACK", {"48": 1, "50": 1}, "CHINA", "COTTON100%")
    assert (l["rrp"], l["rrp_currency"], l["price"]) == (39999.0, "JPY", 12345.0)


def test_inline_wrapped_size_header_takes_its_second_quantity_row(pdfs):
    l = lines(p3m.parse_checked(pdfs["inline"]))[2]
    assert l["sizes"] == {"36": 1, "37": 2, "38": 2, "39": 1, "46": 1}
    assert l["size_run"][-2:] == ["46", "47"]
    assert l["composition"] == "UPPER:COTTON100% SOLE:RUBBER100%"


def test_inline_item_name_drawn_over_its_size_header(pdfs):
    l = lines(p3m.parse_checked(pdfs["inline"]))[2]
    assert l["description"] == "TEST original sole bleaching and washing textile Low-Top sneaker"
    assert l["size_run"][:2] == ["36", "37"]


# ---- gates ----------------------------------------------------------------------------
def test_sizes_not_summing_to_the_quantity_are_refused(build):
    assert any("sizes sum 4 != Tot. Qty 5" in p for p in problems(build(qty_off=(1, 1, 1))))


def test_quantity_times_price_not_the_amount_is_refused(build):
    probs = problems(build(amount_off=(1, 1, 10)))
    assert any("4 x 101.0 = 404.00 != line value 414.0" in p for p in probs)


def test_lines_not_summing_to_the_printed_units_are_refused(build):
    assert any("its lines add up to 33 units, but it prints a total of 34" in p for p in problems(build(units_off=1)))


def test_an_item_subtotal_the_lines_do_not_make_is_refused(build):
    assert any("TEST TEE: lines sum to 4 units / 404.00, its Subtotal prints 5 / 404.00" in p
               for p in problems(build(sub_off=1)))


def test_a_grand_total_below_the_subtotal_is_refused_not_spread(build):
    probs = problems(build(discount=200.0))
    assert any("Grand total 2,225.00 differs from the Subtotal 2,425.00" in p for p in probs)
    assert any("but it prints a net total of USD 2,225.00" in p for p in probs)


@pytest.mark.parametrize("fault,want", [(dict(cover_qty=(5, 6)), "the cover's QUANTITY 11 does not match the printed total 12"),
                                        (dict(cover_amount=1), "the cover's ORDER AMOUNT 1.0 does not match")])
def test_a_cover_that_disagrees_with_the_totals_is_refused(build, fault, want):
    assert any(want in p for p in problems(build("inline", **fault)))


def test_a_quantity_off_its_cell_is_refused(build):
    probs = problems(build(shift=(1, 1, "M", 9)))
    assert any("qty 2@" in p and "from the nearest size cell" in p for p in probs)


def test_a_missing_page_is_refused(build):
    probs = problems(build(drop_page=2))
    assert any("platform pages [1, 3] of 3; page(s) [2] missing" in p for p in probs)
    assert any("never reaches its Subtotal and Grand total" in p for p in probs)


def test_a_page_before_the_first_confirmation_is_refused(build):
    assert "p1: belongs to no confirmation (it comes before the first one)" in problems(build(loose=True))


def test_a_footer_naming_another_order_is_refused(build):
    assert any("p2: the page footer names order 3999999" in p for p in problems(build(footer_doc="3999999")))


def test_order_rows_after_the_grand_total_are_refused(build):
    assert "3999001 p3: order rows after the Grand total" in problems(build(late_rows=True))


def test_an_order_sheet_is_read_but_refused(build):
    docs, probs = p3m.parse(build(title="ORDER SHEET"))
    assert len(lines(docs)) == 6
    assert probs == ["3999001: an ORDER SHEET (the order as placed, before the brand confirmed it), not an ORDER "
                     "CONFIRMATION"]


def _twice(tmp_path):
    """One grid confirmation printed twice in one PDF (its pages repeated)."""
    import pypdf
    one, w = S.grid_pdf(str(tmp_path / "one.pdf")), pypdf.PdfWriter()
    for _ in range(2):
        for page in pypdf.PdfReader(one).pages:
            w.add_page(page)
    with open(tmp_path / "twice.pdf", "wb") as fh:
        w.write(fh)
    return str(tmp_path / "twice.pdf")


@pytest.mark.parametrize("make, needle", [(lambda b, t: _twice(t), "the confirmation starts again"),
                                          (lambda b, t: b(blank_ref=True), "the Ref. is blank"),
                                          (lambda b, t: b(sub_off=1), "its Subtotal prints"),
                                          (lambda b, t: b("inline", cover_qty=(5, 6)), "the cover's QUANTITY")])
def test_a_refusal_for_what_the_document_is_never_points_at_the_ai_reader(build, tmp_path, make, needle):
    """Review 2026-11-13-buyer-15 F2: the reader marks these problems NotLayout, so the service adds no AI hint."""
    cfg = brand() if needle != "the cover's QUANTITY" else brand(oc_supplier="SOSU CO.,LTD.", cost_currency="JPY")
    docs, checks, _ = service.read_oc(make(build, tmp_path), cfg)
    hit = [c["Detail"] for c in checks if c["Check"] == service.GATE_CHECK and needle in c["Detail"]]
    assert docs is None and hit and not any(service.AI_HINT in d for d in hit)


def test_a_bare_dollar_or_yen_srp_must_name_the_order_currency(build):
    """Review 2026-11-13-buyer-15 F5: a bare $ is USD and a bare ¥ is JPY; on a USD order a ¥ SRP is refused."""
    items = [it[:4] + ([tuple(c[:5]) + ("¥55" if c[3] == "ZZCP001_navy" else c[5],) for c in it[4]],) + it[5:]
             for it in S.GRID_ITEMS]
    assert any("ZZCP001_navy" in str(it) for it in items)
    assert any("unreadable SRP 'SRP: ¥55'" in p for p in problems(build(items=items)))
    assert not [p for p in problems(build()) if "SRP" in p]   # the fixture's bare $ SRPs on a USD order read


def test_a_blank_ref_is_refused(build):
    docs, probs = p3m.parse(build(blank_ref=True))
    assert probs == ["3999001 p2 line 6 TEST CAP navy: the Ref. is blank, so the line has no style code to join on"]
    assert lines(docs)[5]["style"] is None


@pytest.mark.parametrize("fault,want", [(dict(price_list="EUR"), "Price list 'EUR' but the money columns are in USD"),
                                        (dict(currency="Euro"), "Currency 'Euro' does not name USD")])
def test_currency_printed_two_ways_must_agree(build, fault, want):
    assert any(want in p for p in problems(build(**fault)))


def test_a_changed_table_header_says_the_layout_changed(build):
    probs = problems(build(header=("REFERENCE", "DESCRIPTION", "SIZES", "QTY", "UNIT", "PRICE", "AMOUNT")))
    assert any("no order-line table header" in p and "the layout changed" in p for p in probs)


def test_a_pdf_with_no_text_says_so(tmp_path):
    with pytest.raises(otb.GateError) as e:
        p3m.parse_checked(S.blank_pdf(str(tmp_path / "scan.pdf")))
    assert e.value.problems == ["the PDF has no text layer (a scan?); this reader reads the PDF's text"]


@pytest.mark.parametrize("build_other", [oc_synth.admilano_pdf, oc_synth.adidas_pdf, oc_synth.fog_pdf])
def test_other_layouts_are_not_read_as_p3m(tmp_path, build_other):
    with pytest.raises(otb.GateError, match="no order confirmations found"):
        p3m.parse_checked(build_other(str(tmp_path / "other.pdf")))


@pytest.mark.parametrize("mod", [otb, admilano, adidas, fog])
@pytest.mark.parametrize("kind", ["grid", "inline"])
def test_other_readers_do_not_read_p3m(pdfs, mod, kind):
    assert mod.parse(pdfs[kind])[0] == []


# ---- through the service: brand config, issuer, currency --------------------------------
def test_a_p3m_config_must_name_its_company():
    with pytest.raises(brandm.BrandError, match="adapter: p3m needs oc_supplier"):
        brand(oc_supplier=None)
    assert service.adapter(brand()) is p3m


def test_the_platform_prints_no_label_so_an_ai_read_gets_none(pdfs):
    assert p3m.doc_label(dict(issuer="TEST Trading Co", label="TEST", lines=[])) is None


def test_service_reads_a_p3m_brand(pdfs):
    docs, checks, info = service.read_oc(pdfs["grid"], brand())
    assert len(lines(docs)) == 6 and info is None
    assert checks[0]["Result"] == "INFO" and "ordering-platform layout" in checks[0]["Detail"]


def test_service_refuses_another_company(pdfs):
    docs, checks, _ = service.read_oc(pdfs["grid"], brand(oc_supplier="Other Company"))
    assert docs is None
    assert "3999001 is from 'TEST Trading Co', not Other Company" in checks[-1]["Detail"]


def test_service_refuses_a_label_the_platform_does_not_print(pdfs):
    docs, checks, _ = service.read_oc(pdfs["grid"], brand(oc_label="TEST"))
    assert docs is None and "is for label 'none the reader could find'" in checks[-1]["Detail"]


def test_service_refuses_another_currency(pdfs):
    docs, checks, _ = service.read_oc(pdfs["inline"], brand(oc_supplier="TEST CO., LTD."))
    assert docs is None and checks[-1]["Check"] == service.CURRENCY_CHECK and "3999002 is in JPY" in checks[-1]["Detail"]
    docs, _, _ = service.read_oc(pdfs["inline"], brand(oc_supplier="TEST CO., LTD.", cost_currency="JPY"))
    assert len(lines(docs)) == 3


def test_a_gate_failure_is_not_called_a_layout_change(build):
    _, checks, _ = service.read_oc(build(qty_off=(1, 1, 1)), brand())
    gate = [c for c in checks if c["Check"] == service.GATE_CHECK]
    assert gate and all(service.AI_HINT not in c["Detail"] for c in gate if "sizes sum" in c["Detail"])
