"""Fixed-layout OC adapters: AD Milano, adidas AG, Fear of God.

Synthetic PDFs (tests/oc_synth.py) always. Real PDFs only when OC_FIXED_PDF_DIR
points at a local folder of them, because order data is never committed to this
public repo."""
import copy
import glob
import os

import pdfplumber
import pytest

import oc_synth
from oc import service
from oc.adapters import admilano, adidas, fog, otb
from oc.brand import code_for
from oc.util import join_key

ADAPTERS = {"admilano": (admilano, oc_synth.admilano_pdf),
            "adidas": (adidas, oc_synth.adidas_pdf),
            "fog": (fog, oc_synth.fog_pdf)}


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    d = tmp_path_factory.mktemp("oc-synth")
    return {k: build(str(d / f"{k}.pdf")) for k, (_, build) in ADAPTERS.items()}


@pytest.fixture
def build(tmp_path):
    def make(name, **fault):
        return ADAPTERS[name][1](str(tmp_path / f"{name}-fault.pdf"), **fault)
    return make


def lines(docs):
    return [l for d in docs for l in d["lines"]]


def _bump_size(docs):
    l = docs[0]["lines"][0]
    l["sizes"][next(iter(l["sizes"]))] += 1


def _bump_price(docs):
    docs[0]["lines"][-1]["price"] += 1


def _drop_line(docs):
    docs[0]["lines"].pop()


def _bump_units(docs):
    docs[0]["total_qty"] += 1


def _bump_net(docs):
    docs[0]["net"] += 1


CORRUPTIONS = [_bump_size, _bump_price, _drop_line, _bump_units, _bump_net]


# ---- shared contract ------------------------------------------------------------
@pytest.mark.parametrize("name", ADAPTERS)
def test_service_names_each_adapter(name):
    assert service.adapter({"adapter": name}) is ADAPTERS[name][0]


@pytest.mark.parametrize("name,want", [("admilano", (1, 3, 14, 10550.0)), ("adidas", (1, 4, 29, 2260.0)),
                                       ("fog", (1, 3, 24, 430.0))])
def test_synthetic_reproduces_printed_totals(pdfs, name, want):
    mod = ADAPTERS[name][0]
    t = mod.totals(mod.parse_checked(pdfs[name]))
    assert (t["docs"], t["lines"], t["units"], t["value"]) == want
    assert (t["printed_units"], t["printed_value"]) == want[2:]


@pytest.mark.parametrize("name", ADAPTERS)
@pytest.mark.parametrize("other", ADAPTERS)
def test_each_reader_recognises_only_its_own_layout(pdfs, name, other):
    mod = ADAPTERS[name][0]
    if name == other:
        assert mod.parse_checked(pdfs[other])
        return
    with pytest.raises(otb.GateError, match="no order confirmations found"):
        mod.parse_checked(pdfs[other])


@pytest.mark.parametrize("name", ADAPTERS)
@pytest.mark.parametrize("corrupt", CORRUPTIONS, ids=lambda f: f.__name__)
def test_corrupting_a_parsed_doc_trips_the_shared_gate(pdfs, name, corrupt):
    docs = ADAPTERS[name][0].parse_checked(pdfs[name])
    assert otb.gate_problems(docs) == []
    bad = copy.deepcopy(docs)
    corrupt(bad)
    assert otb.gate_problems(bad)


# ---- AD Milano ------------------------------------------------------------------
def test_admilano_fixture_puts_two_size_shapes_on_one_set_of_columns(pdfs):
    with pdfplumber.open(pdfs["admilano"]) as pdf:
        words = pdf.pages[0].extract_words()
    xs = next(round(otb.xc(w), 1) for w in words if w["text"] == "XS")
    assert xs in [round(otb.xc(w), 1) for w in words if w["text"] == "S"]


def test_admilano_reads_each_line_against_its_own_size_header(pdfs):
    ls = lines(admilano.parse_checked(pdfs["admilano"]))
    assert ls[0]["sizes"] == {"XS": 1, "S": 2, "M": 2, "L": 1, "XL": 1}
    assert ls[1]["sizes"] == {"S": 2, "M": 1, "L": 1}
    assert ls[1]["size_run"] == ["S", "M", "L"]


def test_admilano_line_runs_across_a_page_break(pdfs):
    l = lines(admilano.parse_checked(pdfs["admilano"]))[2]
    assert (l["style"], l["fabric"], l["colour"], l["sizes"], l["price"], l["page"]) == \
        ("Z0000003", "LT001", "004", {"40": 1, "41": 1, "42": 1}, 200.0, 1)


def test_admilano_fields_and_join_key(pdfs):
    docs = admilano.parse_checked(pdfs["admilano"])
    d, l = docs[0], docs[0]["lines"][0]
    assert (d["doc"], d["date"], d["currency"], d["collection"], d["brand"]) == \
        ("9001", "01/02/2099", "EUR", "2099 SS Main", "Test Label")
    assert (l["colour_name"], l["description"], l["made_in"], l["product_line"]) == \
        ("BLACK", "TEST GARMENT 1 LONG NAME", "ITALY", "TS RTW")
    assert (l["deliver_from"], l["deliver_to"]) == ("01/03/2099", "01/05/2099")
    assert join_key(l["style"], l["fabric"], l["colour"]) == "Z0000001FB001 099"


def test_admilano_reads_decimal_comma_and_thousands_dot(pdfs):
    l = lines(admilano.parse_checked(pdfs["admilano"]))[0]
    assert (l["price"], l["line_value"]) == (1250.0, 8750.0)


def test_admilano_adds_quantity_over_units_of_measure(pdfs):
    assert admilano.parse_checked(pdfs["admilano"])[0]["total_qty"] == 14


def test_admilano_skips_the_terms_pages(pdfs):
    docs = admilano.parse_checked(pdfs["admilano"])
    assert docs[0]["pages"] == [1, 2]
    assert "Z0000009" not in {l["style"] for l in lines(docs)}


def test_admilano_quantity_off_its_column_is_refused(build):
    probs = admilano.parse(build("admilano", shift=(2, 0, 20)))[1]
    assert any("Z0000002: qty 2@" in p and "pt from column" in p for p in probs)


def test_admilano_size_with_no_quantity_is_refused(build):
    probs = admilano.parse(build("admilano", drop=(1, 4)))[1]
    assert any("size XL has no quantity under it" in p for p in probs)


def test_admilano_document_discount_is_refused_not_spread(build):
    with pytest.raises(otb.GateError) as e:
        admilano.parse_checked(build("admilano", discount=100.0))
    assert any("document discount 100.00" in p for p in e.value.problems)
    assert any("prints a net total of EUR 10,450.00" in p for p in e.value.problems)


def test_admilano_moved_table_header_is_refused(pdfs, monkeypatch):
    monkeypatch.setitem(admilano.HEADER, "Grid", 470.0)
    probs = admilano.parse(pdfs["admilano"])[1]
    assert probs == ["9001: table header 'Grid' at x=482.2, expected 470.0 (layout changed)"]


# ---- adidas ---------------------------------------------------------------------
def test_adidas_fixture_runs_words_together_at_default_tolerance(pdfs):
    with pdfplumber.open(pdfs["adidas"]) as pdf:
        squeezed = {w["text"] for w in pdf.pages[0].extract_words()}
    assert "Y-3TESTCAPBLACK" in squeezed


def test_adidas_reads_words_at_their_gaps(pdfs):
    ls = lines(adidas.parse_checked(pdfs["adidas"]))
    assert [l["description"] for l in ls] == ["Y-3 TEST CAP BLACK", "TEST TEE BLACK", "Y-3 TEST SHOE FTWWHT/FTWWHT",
                                             "TEST HOODIE BLACK"]


def test_adidas_size_cells_and_page_break(pdfs):
    ls = lines(adidas.parse_checked(pdfs["adidas"]))
    assert ls[0]["sizes"] == {"OSFM": 4}
    assert ls[2]["sizes"] == {"7": 1, "8": 2, "9": 3, "10": 2, "11": 1}
    assert (ls[2]["composition"], ls[2]["made_in"]) == ("U:LE L:TE S:RU", "VN")
    assert (ls[3]["page"], ls[3]["line_value"]) == (2, 1000.0)


def test_adidas_fields_and_join_key(pdfs):
    d = adidas.parse_checked(pdfs["adidas"])[0]
    assert (d["doc"], d["date"], d["collection"], d["currency"], d["net"]) == \
        ("900000001", "01.01.2099", "SYN_TEST_ORDER", "USD", 2260.0)
    l = d["lines"][0]
    assert (l["fabric"], l["colour"]) == (None, None)
    assert join_key(l["style"], l["fabric"], l["colour"]) == "ZZ0001"


def test_adidas_line_discount_is_refused(build):
    with pytest.raises(otb.GateError) as e:
        adidas.parse_checked(build("adidas", discount=(2, 5.0)))
    assert any("ZZ0002: 5.00% discount" in p for p in e.value.problems)


def test_adidas_size_cell_under_the_line_is_refused(build):
    probs = adidas.parse(build("adidas", stray=2))[1]
    assert any("ZZ0002: unexpected XL/1 right of the composition row" in p for p in probs)


def test_adidas_unreadable_size_cell_is_refused(build):
    probs = adidas.parse(build("adidas", bad_cell=(2, "S1")))[1]
    assert any("ZZ0002: size cell 'S1'" in p and "is not SIZE/QTY" in p for p in probs)


# ---- Fear of God -----------------------------------------------------------------
def test_fog_reads_each_line_against_its_own_size_header(pdfs):
    ls = lines(fog.parse_checked(pdfs["fog"]))
    assert ls[0]["sizes"] == {"XS": 2, "S": 6, "M": 6, "L": 3}
    assert ls[1]["sizes"] == {"S": 2, "M": 1, "L": 1}
    assert ls[0]["size_run"] == ["2XS", "XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL"]


def test_fog_size_header_at_the_foot_of_the_previous_page(pdfs):
    l = lines(fog.parse_checked(pdfs["fog"]))[2]
    assert (l["style"], l["sizes"], l["page"]) == ("ZZ00003", {"O/S": 3}, 2)


def test_fog_rebuilds_wrapped_cells_from_their_glyphs(pdfs):
    ls = lines(fog.parse_checked(pdfs["fog"]))
    assert [l["colour_name"] for l in ls] == ["WASHED INK BLACK", "BLACK/CREAM LOGO", "DEEP BROWN"]
    assert [l["composition"] for l in ls] == ["100% COTTON", "100% COTTON", "87% NYLON, 13% ELASTANE"]


def test_fog_blank_msrp_leaves_total_in_its_column(pdfs):
    ls = lines(fog.parse_checked(pdfs["fog"]))
    assert (ls[0]["price"], ls[0]["msrp"], ls[0]["line_value"]) == (10.0, 25.0, 170.0)
    assert (ls[2]["price"], ls[2]["msrp"], ls[2]["line_value"]) == (20.0, None, 60.0)


def test_fog_fields_and_join_key(pdfs):
    d = fog.parse_checked(pdfs["fog"])[0]
    assert (d["doc"], d["brand"], d["date"], d["currency"], d["collection"], d["gross"]) == \
        ("SO9999999", "TEST Essentials", "1/2/2099", "USD", "WINTER 2099", 430.0)
    l = d["lines"][0]
    assert join_key(l["style"], l["fabric"], l["colour"]) == "ZZ00001 0001"


def test_fog_quantity_off_its_column_is_refused(build):
    probs = fog.parse(build("fog", shift=(2, 0, 16)))[1]
    assert any("ZZ00002: qty 2@" in p and "pt from column" in p for p in probs)


def test_fog_missing_quantity_cell_is_refused(build):
    probs = fog.parse(build("fog", drop=(2, 2)))[1]
    assert any("ZZ00002: 2 quantities under a 3-size header S M L" in p for p in probs)


def test_fog_discount_is_refused_not_spread(build):
    with pytest.raises(otb.GateError) as e:
        fog.parse_checked(build("fog", discount=10.0))
    assert any("Discount Total 10.00" in p for p in e.value.problems)


def test_fog_one_reader_for_both_issuers(build):
    docs = fog.parse_checked(build("fog", brand="TEST of God LLC"))
    assert docs[0]["brand"] == "TEST of God LLC" and len(lines(docs)) == 3


# ---- the S/S 26 shapes (backtest 2026-11-12: every FOG S/S 26 and one Y-3 S/S 25 OC were refused) -----------
def _ss26(style, code, col, fabric=None):
    """One FOG line in an S/S 26 shape: `style` a str or a tuple of wrapped rows, `code` None for ': NAME'."""
    sizes, xs = ["S", "M", "L"], [304.0, 336.0, 368.0]
    return [(style, code, ["100% ", "COTTON"], col, sizes, xs, [1, 2, 1], 40.0, None)]


@pytest.mark.parametrize("line,style,colour,code,name", [
    pytest.param(_ss26(("ZX1SP0001", "01F"), None, ["FADED ", "BLACK"]), "ZX1SP000101F", None, None, "FADED BLACK",
                 id="wrapped apparel style, name only: the style carries the colourway"),
    pytest.param(_ss26(("ZX80A-10", "0EVA"), "250", ["BEIGE"]), "ZX80A-100EVA", "250", "250", "BEIGE",
                 id="wrapped footwear style with a printed colour code"),
    pytest.param(_ss26("ZX00266", None, ["IRON ", "GREY"]), "ZX00266", "IRON GREY", None, "IRON GREY",
                 id="one-row style, name only: the name is the colour, for colour_codes"),
])
def test_fog_reads_the_ss26_style_and_colour_cells(build, line, style, colour, code, name):
    docs = fog.parse_checked(build("fog", lines=line, order_alone=True, fabric="Knit"))
    (l,) = lines(docs)
    assert (l["style"], l["colour"], l["colour_code"], l["colour_name"]) == (style, colour, code, name)
    assert (l["fabric_name"], l["composition"]) == ("Knit", "100% COTTON")   # the fabric word stays out of the content
    # a fog config with no join_key falls back to "{style}{fabric} {colour}": the fabric word must not reach it
    assert l["fabric"] is None and code_for({}, l["style"], l["fabric"], l["colour"]) == f"{style} {colour or ''}".strip()
    assert docs[0]["doc"] == "SO9999999"   # not 558, the street number that follows ORDER # on its own row


def test_fog_refuses_a_colour_cell_with_an_unread_code(build):
    probs = fog.parse(build("fog", lines=_ss26("ZX00001", "A1", ["BLACK"])))[1]
    assert any("ZX00001: no colour code" in p for p in probs)


def test_adidas_other_text_in_the_size_band_of_a_composition_row_is_refused(build):
    probs = adidas.parse(build("adidas", stray=2, stray_text="NOTE"))[1]
    assert any("ZZ0002: unexpected NOTE right of the composition row" in p for p in probs)


def test_adidas_size_cell_wrapped_onto_the_composition_row_is_read(build):
    docs = adidas.parse_checked(build("adidas", wrap=3))   # line 3's 11/1 printed under its 7/1
    l = lines(docs)[2]
    assert l["sizes"] == {"7": 1, "8": 2, "9": 3, "10": 2, "11": 1} and l["size_run"] == ["7", "8", "9", "10", "11"]
    assert (l["composition"], l["made_in"]) == ("U:LE L:TE S:RU", "VN")


# ---- real PDFs, local only ------------------------------------------------------
REAL = os.environ.get("OC_FIXED_PDF_DIR")
GLOBS = {"admilano": "OC *.pdf", "adidas": "OrderConfirmation_*.pdf", "fog": "SO*_order_confirmation.pdf"}


@pytest.mark.skipif(not REAL, reason="set OC_FIXED_PDF_DIR to a local folder of real order-confirmation PDFs")
@pytest.mark.parametrize("name", ADAPTERS)
def test_real_pdfs_reproduce_printed_totals(name):
    mod = ADAPTERS[name][0]
    files = sorted(glob.glob(os.path.join(REAL, GLOBS[name])))
    assert files, f"no {GLOBS[name]} in {REAL}"
    for f in files:
        docs = mod.parse_checked(f)
        t = mod.totals(docs)
        assert t["units"] == t["printed_units"] and t["value"] == t["printed_value"], f
        assert otb.gate_problems(docs) == []
        for corrupt in CORRUPTIONS:
            bad = copy.deepcopy(docs)
            corrupt(bad)
            assert otb.gate_problems(bad), (f, corrupt.__name__)


@pytest.mark.skipif(not REAL, reason="set OC_FIXED_PDF_DIR to a local folder of real order-confirmation PDFs")
def test_real_pdfs_are_recognised_by_exactly_one_reader():
    for name, pat in GLOBS.items():
        for f in sorted(glob.glob(os.path.join(REAL, pat))):
            for other, (mod, _) in ADAPTERS.items():
                if other != name:
                    assert mod.parse(f)[0] == [], (f, other)
