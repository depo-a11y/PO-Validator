"""AI reads held to their own lines (block grounding), page accounting, the brand's issuer and label for every
reader, and the guards around them. Synthetic data only: the layout texts below copy the reading ORDER of the
real layouts (checked against the fixed readers on the real PDFs), never their content."""
import copy
import csv
import types

import pytest

import oc_synth
import test_oc_tool as base
from oc import brand as brandm, cli, draft as draftm, service
from oc.adapters import adidas, ai, otb
from oc.resolve import AI_NEW_SIZE, COLOUR
from test_oc_ai import RESPONSE, PDF_LINES, FakeClient, _cfg, _mut, _refused, make_pdf, message


# ---- a multi-page synthetic PDF -------------------------------------------------------------
def make_pages_pdf(path, pages):
    """One A4 page of Helvetica text per list of lines."""
    def esc(t):
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    n = len(pages)
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [" + " ".join(f"{3 + 2 * k} 0 R" for k in range(n)) + f"] /Count {n} >>"]
    for k, lines in enumerate(pages):
        body = "BT /F1 9 Tf 12 TL 40 800 Td " + " ".join(f"({esc(t)}) Tj T*" for t in lines) + " ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {4 + 2 * k} 0 R "
                    f"/Resources << /Font << /F1 {3 + 2 * n} 0 R >> >> >>")
        objs.append(f"<< /Length {len(body)} >>\nstream\n{body}\nendstream")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)
    return str(path)


TERMS = ["GENERAL TERMS AND CONDITIONS OF SALE", "1. Example clause that carries no order lines at all."]


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(ai.KEY_NAME, "sk-ant-test")


@pytest.fixture
def pdf(tmp_path):
    return make_pdf(tmp_path / "oc.pdf", PDF_LINES)


@pytest.fixture
def two_pages(tmp_path):
    return make_pages_pdf(tmp_path / "two.pdf", [PDF_LINES, TERMS])


def _fails(e, check):
    return [c["Detail"] for c in e.checks if c["Check"] == check and c["Result"] == "FAIL"]


# ---- block grounding on the synthetic PDF ------------------------------------------------------
def _swap(a, b, keys=("sizes", "qty_text", "price_text", "line_value_text")):
    def f(d, doc, ls):
        for k in keys:
            ls[a][k], ls[b][k] = ls[b][k], ls[a][k]
    return f


BLOCK = {
    "numbers of lines 1 and 2 swapped": _swap(0, 1),
    "colours of lines 1 and 2 swapped": _swap(0, 1, ("colour",)),
    "fabric invented": lambda d, doc, ls: ls[2].update(fabric="100% Silk"),
    "colour invented": lambda d, doc, ls: ls[1].update(colour="Navy"),
    "a size moved to a label printed on another line": lambda d, doc, ls: ls[0]["sizes"][0].update(size="XS"),
    "a size label printed nowhere": lambda d, doc, ls: ls[0]["sizes"][2].update(size="XL"),
    "lines returned out of printed order": lambda d, doc, ls: ls.reverse(),
    "a price found only on another line": lambda d, doc, ls: (ls[1].update(price_text="EUR 120,00",
                                                                         line_value_text="EUR 360,00")),
}


@pytest.mark.parametrize("name", list(BLOCK))
def test_a_value_outside_its_own_line_is_refused(pdf, key, name):
    e = _refused(pdf, _mut(BLOCK[name]))
    assert _fails(e, ai.GROUNDING_CHECK), (name, e.checks)


def test_the_true_reading_passes_block_grounding(pdf, key):
    docs, warns, _ = ai.read(pdf, _cfg(), factory=FakeClient(message(RESPONSE)).factory)
    assert warns == [] and len(docs[0]["lines"]) == 3


def test_a_grounding_fail_names_the_line_and_what_is_missing(pdf, key):
    e = _refused(pdf, _mut(_swap(0, 1, ("colour",))))
    (d,) = _fails(e, ai.GROUNDING_CHECK)
    assert "SO-90001 line 1 (EXJK01): colour 'Black'" in d and "SO-90001 line 2 (EXTS02): colour 'Grey'" in d


def test_bold_glyphs_printed_twice_inside_a_line_are_still_found(tmp_path, key):
    lines = [l.replace("EUR 120,00 EUR 480,00", "EEUURR 112200,,0000 EUR 480,00") for l in PDF_LINES]
    bold = make_pdf(tmp_path / "bold.pdf", lines)
    docs, warns, _ = ai.read(bold, _cfg(), factory=FakeClient(message(RESPONSE)).factory)
    assert warns == [] and docs[0]["lines"][0]["price"] == 120.0


# ---- duplicates and omissions ----------------------------------------------------------------
def test_a_confirmation_returned_twice_is_refused(pdf, key):
    d = copy.deepcopy(RESPONSE)
    d["documents"].append(copy.deepcopy(d["documents"][0]))
    e = _refused(pdf, d)
    assert any("confirmation SO-90001 returned 2 times" in x for x in _fails(e, ai.PAGES_CHECK))


def test_a_line_returned_twice_is_refused_unless_printed_twice(pdf, key):
    def dup(d, doc, ls):
        ls.append(copy.deepcopy(ls[1]))
        doc.update(total_qty_text="12", net_total_text="EUR 2.853,00")
    e = _refused(pdf, _mut(dup))
    assert any("line EXTS02 100% Cotton Black returned 2 times but its style is printed 1 time(s)" in x
               for x in _fails(e, ai.PAGES_CHECK))
    twice = "\n".join(PDF_LINES[:6] + PDF_LINES[4:6] + PDF_LINES[6:])
    docs, fails, _ = ai.interpret(_mut(dup), _cfg(), twice)
    assert not [f for f in fails if f["Check"] == ai.PAGES_CHECK], fails


def test_every_page_must_be_in_one_confirmation_or_listed_as_without_lines(two_pages, key):
    ok = copy.deepcopy(RESPONSE)
    ok["pages_without_lines"] = [2]
    docs, warns, _ = ai.read(two_pages, _cfg(), factory=FakeClient(message(ok)).factory)
    assert docs and warns == []
    cases = {
        "page(s) [2] of 2 are in no confirmation": lambda d, doc, ls: None,
        "page(s) [1] given to more than one confirmation": lambda d, doc, ls: d.update(pages_without_lines=[1, 2]),
        "page(s) [3] do not exist (the PDF has 2)": lambda d, doc, ls: (d.update(pages_without_lines=[2]),
                                                                      doc.update(pages=[1, 3])),
        "lines on page(s) [2], which are not among its pages [1]": lambda d, doc, ls: (
            d.update(pages_without_lines=[2]), ls[2].update(page=2)),
    }
    for needle, f in cases.items():
        e = _refused(two_pages, _mut(f))
        assert any(needle in x for x in _fails(e, ai.PAGES_CHECK)), (needle, e.checks)


def test_a_style_must_be_on_the_page_the_model_names(two_pages, key):
    def wrong(d, doc, ls):
        d.update(pages_without_lines=[])
        doc.update(pages=[1, 2])
        ls[2].update(page=2)
    e = _refused(two_pages, _mut(wrong))
    assert any("style 'EXCP03' is not on page 2 after the previous line" in x for x in _fails(e, ai.GROUNDING_CHECK))


def test_more_than_60_pages_is_refused_before_anything_is_sent(tmp_path, key):
    big = make_pages_pdf(tmp_path / "big.pdf", [PDF_LINES] + [TERMS] * 60)
    fake = FakeClient(message(RESPONSE))
    with pytest.raises(ai.ReadRefused) as e:
        ai.read(big, _cfg(), factory=fake.factory)
    (c,) = e.value.checks
    assert c["Check"] == ai.READ_CHECK and "this PDF has 61 pages and the AI reader takes at most 60" in c["Detail"]
    assert fake.calls == [] and ai.MAX_PAGES == 60


def test_60_pages_is_still_read(tmp_path, key):
    d = copy.deepcopy(RESPONSE)
    d["pages_without_lines"] = list(range(2, 61))
    ok = make_pages_pdf(tmp_path / "ok.pdf", [PDF_LINES] + [TERMS] * 59)
    assert ai.read(ok, _cfg(), factory=FakeClient(message(d)).factory)[0]


# ---- reading order of each real layout, on synthetic text --------------------------------------
def L(style, fabric, colour, sizes, qty, price, value, page=1):
    return dict(line_no=None, style=style, fabric=fabric, colour=colour, product_class=None, description=None,
                composition=None, made_in=None, deliver_from=None, deliver_to=None,
                sizes=[dict(size=s, qty_text=q) for s, q in sizes], qty_text=qty, price_text=price,
                line_value_text=value, page=page)


def RAW(supplier, number, lines, qty, net, pages=(1,), cur="EUR", kind="order_confirmation"):
    return dict(supplier=supplier, document_kind=kind, has_size_breakdown=True, pages_without_lines=[],
                documents=[dict(number=number, label=None, date=None, collection=None, currency=cur,
                                total_qty_text=qty, net_total_text=net, gross_total_text=None, pages=list(pages),
                                lines=lines)])


LAYOUTS = {
    "FCW sales order": (["""ORDER CONFIRMATION Example Atelier Ltd
SO-91001 London
Document Number: SO-91001
Style: Fabric: Colour:
EXA101 100% Cotton Black XS S M L XL Qty Unit Price Total
Description: Material Content: COO: HTS:
Example Tee CHN - 1 2 1 - 4 € 80.00 € 320.00
Style: Fabric: Colour:
EXA102 100% Wool Grey ONE SIZE Qty Unit Price Total
Description: Material Content: COO: HTS:
Example Cap CHN 3 3 € 30.00 € 90.00
Total Qty 7 Net Value € 410.00"""], RAW("Example Atelier Ltd", "SO-91001", [
        L("EXA101", "100% Cotton", "Black", [("S", "1"), ("M", "2"), ("L", "1")], "4", "€ 80.00", "€ 320.00"),
        L("EXA102", "100% Wool", "Grey", [("ONE SIZE", "3")], "3", "€ 30.00", "€ 90.00")], "7", "€ 410.00")),
    "otb (Margiela)": (["""Order confirmation SS - 2027/99-01 Of 01/07/2026
Collection MM MALE RTW AVP Currency European Euro
Line no. Style Fabric Colour Deliver from/to Product Class
1 S99ZZ0001 P0001 101 15/10/2026 15/02/2027 JACKET
40 42 Tot. Qty Price Line value
1 1 2 100,00 200,00
2 S99ZZ0002 P0002 900 15/10/2026 15/02/2027 PANTS
XS S Tot. Qty Price Line value
2 1 3 50,00 150,00
Total order q.ty 5 Total net value 350,00
Branch of Example Atelier Ltd"""], RAW("Example Atelier Ltd", "SS - 2027/99-01", [
        L("S99ZZ0001", "P0001", "101", [("40", "1"), ("42", "1")], "2", "100,00", "200,00"),
        L("S99ZZ0002", "P0002", "900", [("XS", "2"), ("S", "1")], "3", "50,00", "150,00")], "5", "350,00")),
    "admilano": (["""Example Atelier Ltd ORDER PAYMENT DETAILS
Ord. N° 9001 ORDER CONFIRMATION
TSTSHO Z0000001 LT001 099 T001 5 PA EUR 1.250,00
TS Shoes TEST ANKLE BOOT BLACK Made-in ITALY
Size 36 37 38 39 40
Q.ty 1 1 1 1 1
WHL Price € 250
TSTTOP Z0000002 LT002 001 T002 2 PA EUR 300,00
TS Tops TEST SHIRT WHITE
Size S M
Q.ty 1 1
WHL Price € 150
Total value in order currency EUR 1.550,00 0,00 1.550,00"""], RAW("Example Atelier Ltd", "9001", [
        L("Z0000001", "LT001", "099", [(s, "1") for s in ("36", "37", "38", "39", "40")], "5", "€ 250", "EUR 1.250,00"),
        L("Z0000002", "LT002", "001", [("S", "1"), ("M", "1")], "2", "€ 150", "EUR 300,00")], "7", "1.550,00")),
    "adidas AG": (["""Example Atelier Ltd Order Confirmation Order Number : 900000001
Item Article Description Quantity Size/Quantity Unit Price Total Discount % Total incl. Discount
USD USD % USD
1 ZZ0001 Y-3 TEST CAP BLACK 4 OSFM/4 30.00 120.00 0.00 120.00
2 ZZ0002 TEST TEE BLACK 6 S/1 M/3 L/2 40.00 240.00 0.00 240.00
Quantity 10 Order Value 360.00"""], RAW("Example Atelier Ltd", "900000001", [
        L("ZZ0001", None, None, [("OSFM", "4")], "4", "30.00", "120.00"),
        L("ZZ0002", None, None, [("S", "1"), ("M", "3"), ("L", "2")], "6", "40.00", "240.00")], "10", "360.00",
        cur="USD")),
    "Fear of God (size header before the line, once at the foot of the previous page)": ([
        """Example Atelier Ltd ORDER # SO9000001 Order Confirmation
SELL STYLE FABRIC CONTENT COLOR UNITS PRICE MSRP TOTAL
XS S M L
1 RW00001 FLEECE 80% COTTON 0001 BLACK 4 1 2 1 $60.00 $120.00 $240.00
31 32 33""", """Example Atelier Ltd ORDER # SO9000001 Order Confirmation
2 RW00002 DENIM 100% COTTON 0002 INDIGO 3 1 1 1 $50.00 $100.00 $150.00
Total Units: 7 Total Amount: $390.00"""], RAW("Example Atelier Ltd", "SO9000001", [
        L("RW00001", "FLEECE", "0001", [("XS", "1"), ("S", "2"), ("M", "1")], "4", "$60.00", "$240.00"),
        L("RW00002", "DENIM", "0002", [("31", "1"), ("32", "1"), ("33", "1")], "3", "$50.00", "$150.00", page=2)],
        "7", "$390.00", pages=(1, 2), cur="USD")),
    "one table header for the page (a proforma)": (["""PROFORMA INVOICE Example Atelier Ltd
INVOICE NUMBER: TC-P0001
STYLE CODE CATEGORY PRODUCT NAME COLOR UNITS UNIT PRICE TOTAL
OS 1 2 3 4 (USD) (USD)
TC00001 JACKET EXAMPLE PUFFER BLACK 1 2 1 4 $490.50 $1,962.00
TC00002 CAP EXAMPLE CAP BLACK 5 5 $61.50 $307.50
TOTAL UNITS 9
TOTAL PRICE $2,269.50"""], RAW("Example Atelier Ltd", "TC-P0001", [
        L("TC00001", None, "BLACK", [("1", "1"), ("2", "2"), ("3", "1")], "4", "$490.50", "$1,962.00"),
        L("TC00002", None, "BLACK", [("OS", "5")], "5", "$61.50", "$307.50")], "9", "$2,269.50", cur="USD",
        kind="proforma_invoice")),
}


LAYOUTS["one style in two colours (a real fog order and Margiela both have one)"] = (["""ORDER # SO9000002
1 ZZ90433 FLEECE 0001 BLACK S M 2 1 1 $60.00 $120.00
2 ZZ90433 FLEECE 0002 GREY S M 2 1 1 $70.00 $140.00
Total Units: 4 Total Amount: $260.00"""], RAW("Example Atelier Ltd", "SO9000002", [
    L("ZZ90433", "FLEECE", "0001", [("S", "1"), ("M", "1")], "2", "$60.00", "$120.00"),
    L("ZZ90433", "FLEECE", "0002", [("S", "1"), ("M", "1")], "2", "$70.00", "$140.00")], "4", "$260.00", cur="USD"))


def _layout_cfg(raw):
    return dict(oc_supplier=["Example Atelier Ltd"], cost_currency=raw["documents"][0]["currency"], vendor="EX",
                _name="ex")


@pytest.mark.parametrize("name", list(LAYOUTS))
def test_each_real_layouts_reading_order_passes_a_true_reading(name):
    pages, raw = LAYOUTS[name]
    docs, fails, warns = ai.interpret(copy.deepcopy(raw), _layout_cfg(raw), pages)
    assert fails == [] and otb.gate_problems(docs) == []


@pytest.mark.parametrize("name", list(LAYOUTS))
def test_each_real_layout_refuses_numbers_swapped_between_its_lines(name):
    pages, raw = LAYOUTS[name]
    bad = copy.deepcopy(raw)
    a, b = bad["documents"][0]["lines"][:2]
    for k in ("sizes", "qty_text", "price_text", "line_value_text"):
        a[k], b[k] = b[k], a[k]
    _, fails, _ = ai.interpret(bad, _layout_cfg(raw), pages)
    assert ai.GROUNDING_CHECK in [f["Check"] for f in fails], name


# ---- an AI-read size the live product does not have is red ----------------------------------------
def _catalog(tmp_path):
    rows = [base._variant("1", "Replica Sneaker in White", "MEN SHOES SNEAKERS", "Active", "S99WS0001P0001 101",
                          "WHITE", s, f"ZXFW26{100 + i}", "800.00", "800.00", "310.00", i == 0)
            for i, s in enumerate(["40", "41", "42"])]
    return base._write_catalog(tmp_path / "catalog.csv", rows, base.CAT_COLS)


@pytest.mark.parametrize("ai_read,source,colour", [(True, AI_NEW_SIZE, "red"), (False, "OC", "green")])
def test_an_ordered_size_the_live_product_lacks(tmp_path, ai_read, source, colour):
    docs = base._docs()
    if ai_read:
        for l in docs[0]["lines"]:
            l["reader"] = "ai"
    cat = base.catm.load(_catalog(tmp_path))
    rows, review, checks, fills = draftm.build(docs, base.BRAND, cat, base._hist(), base._tags())
    sizes = {r["Value"]: r for r in review if r["Column"] == "Option2 Value" and r["Code"] == "S99WS0001P0001 101"}
    assert (sizes["43"]["Source"], sizes["43"]["Colour"]) == (source, colour)
    assert sizes["41"]["Source"] == "catalog"
    if ai_read:
        assert sizes["43"]["Why"].startswith("read by AI from SS - 1 line 1; ordered size the live product does not")
    assert COLOUR[AI_NEW_SIZE] == "red"


# ---- the brand's issuer and label, every reader --------------------------------------------------------
def test_fog_refuses_the_other_issuer(tmp_path):
    pdf = oc_synth.fog_pdf(str(tmp_path / "fog.pdf"))
    cfg = dict(base.BRAND, adapter="fog", cost_currency="USD", vendor="EXAMPLE OF GOD", _name="eg", oc_label=None)
    docs, checks, _ = service.read_oc(pdf, dict(cfg, oc_supplier=["TEST of God LLC"]))
    (f,) = service.fails(checks)
    assert docs is None and f["Check"] == service.IDENTITY_CHECK
    assert "is from 'TEST Essentials', not TEST of God LLC" in f["Detail"] and "EXAMPLE OF GOD (eg)" in f["Detail"]
    assert service.read_oc(pdf, dict(cfg, oc_supplier=["TEST Essentials"]))[0]


def test_adidas_refuses_another_label(tmp_path):
    pdf = oc_synth.adidas_pdf(str(tmp_path / "adidas.pdf"))
    cfg = dict(base.BRAND, adapter="adidas", cost_currency="USD")
    docs, checks, _ = service.read_oc(pdf, dict(cfg, oc_label=["RS"]))
    (f,) = service.fails(checks)
    assert docs is None and "is for label 'Y-3', not RS" in f["Detail"]
    assert service.read_oc(pdf, dict(cfg, oc_label=["y-3"]))[0]


@pytest.mark.parametrize("descs,label", [
    (["Y-3 CAP", "TEE", "Y-3 SHOE"], "Y-3"), (["WC ROSE TEE", "Willy Chavarria Meg", "FLEECE"], "Willy Chavarria"),
    (["RS STAN"], "RS"), (["Y-3 CAP", "RS STAN"], "RS + Y-3"), (["TEE", "HOODIE"], None), (["Y-30 X"], None)])
def test_adidas_label_comes_from_the_line_descriptions(descs, label):
    assert adidas.label_of([dict(description=d) for d in descs]) == label


def test_admilano_checks_issuer_and_label_only_when_set(tmp_path):
    pdf = oc_synth.admilano_pdf(str(tmp_path / "ad.pdf"))
    cfg = dict(base.BRAND, adapter="admilano", oc_label=None)
    assert service.read_oc(pdf, cfg)[0]
    assert service.read_oc(pdf, dict(cfg, oc_supplier=["AD Milano Srl"], oc_label=["Test Label"]))[0]
    _, checks, _ = service.read_oc(pdf, dict(cfg, oc_supplier=["Other Srl"]))
    assert "is from 'AD Milano Srl', not Other Srl" in service.fails(checks)[0]["Detail"]
    _, checks, _ = service.read_oc(pdf, dict(cfg, oc_label=["Other Label"]))
    assert "is for label 'Test Label', not Other Label" in service.fails(checks)[0]["Detail"]


def test_a_reader_without_a_parsed_issuer_needs_the_name_printed_on_the_pages(tmp_path, monkeypatch):
    printed = make_pdf(tmp_path / "otb.pdf", ["Order confirmation SS - 1", "Branch of EXAMPLE S.A.S."])
    docs = base._docs()
    monkeypatch.setattr(service, "adapter", lambda cfg: types.SimpleNamespace(parse_checked=lambda p: docs))
    assert service.read_oc(printed, dict(base.BRAND, oc_supplier=["Example S.A.S."]))[0] is docs
    _, checks, _ = service.read_oc(printed, dict(base.BRAND, oc_supplier=["Other SpA"]))
    assert "SS - 1 is from 'none of those names on its pages', not Other SpA" in service.fails(checks)[0]["Detail"]


def test_the_ai_reader_is_held_to_the_label_too(pdf, key):
    cfg = _cfg(oc_label=["Y-3"])
    docs, checks, _ = service.read_oc(pdf, cfg, ai_factory=FakeClient(message(RESPONSE)).factory)
    assert docs is None and "is for label 'none the reader could find', not Y-3" in service.fails(checks)[0]["Detail"]
    labelled = copy.deepcopy(RESPONSE)
    labelled["documents"][0]["label"] = "Y-3"
    assert service.read_oc(pdf, cfg, ai_factory=FakeClient(message(labelled)).factory)[0]


@pytest.mark.parametrize("name,missing", [("fog", "oc_supplier"), ("adidas", "oc_label"), ("otb", "oc_label")])
def test_fog_and_adidas_configs_must_say_which_brand_they_accept(name, missing):
    with pytest.raises(brandm.BrandError, match=f"adapter: {name} needs {missing}. Add {missing}: "):
        brandm.from_mapping(dict(base.BRAND, adapter=name, oc_label=None), "t")
    brandm.from_mapping(dict(base.BRAND, adapter="admilano", oc_label=None), "t")
    assert brandm.from_mapping(dict(base.BRAND, adapter="otb"), "t")["oc_label"] == ["MM"]
    cfg = brandm.from_mapping(dict(base.BRAND, adapter="adidas", oc_label="Y-3"), "t")
    assert cfg["oc_label"] == ["Y-3"]
    with pytest.raises(brandm.BrandError, match="oc_label is a label name or a list"):
        brandm.from_mapping(dict(base.BRAND, adapter="adidas", oc_label=[""]), "t")


# ---- the hint to read with AI, on every layout problem ------------------------------------------------
def test_every_layout_problem_of_a_fixed_reader_points_at_the_ai_box(monkeypatch):
    problems = ["9001: table header 'Size' at x=1, expected 2 (layout changed)",
                "9001 line 1 Z1: qty 2@300 is 40.0pt from column 36",
                "9001 line 1 Z1: sizes sum 4 != Tot. Qty 3",
                "9001 line 1 Z1: 1 x 2.0 = 2.00 != line value 3.0",
                "9001: parsed EUR 1.00, printed net 2.00",
                "9001: document discount 10.00 is not applied to any line; ask"]

    def parse_checked(path):
        raise otb.GateError(problems)
    monkeypatch.setattr(service, "adapter", lambda cfg: types.SimpleNamespace(parse_checked=parse_checked))
    _, checks, _ = service.read_oc("oc.pdf", dict(base.BRAND))
    got = [c["Detail"] for c in service.fails(checks)]
    assert got[:2] == [f"{p}. {service.AI_HINT}" for p in problems[:2]] and got[2:] == problems[2:]


# ---- what the page needs --------------------------------------------------------------------------------
def test_a_paid_ai_read_is_told_apart_from_one_that_sent_nothing():
    sent = [dict(Check=service.READER_CHECK, Result="WARN", Detail="read by AI (m) in 3 s, about US$0.10 ...")]
    assert service.ai_read_sent(sent)
    assert not service.ai_read_sent([dict(sent[0], Result="INFO", Detail="not read: ...")])
    assert not service.ai_read_sent([dict(sent[0], Result="INFO", Detail="read by the built-in reader for ...")])


def test_read_without_a_brand_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["read", "x.pdf"])
    assert e.value.code == 2 and "give the brand: --brand-config <file.yaml> or --brand <name>" in capsys.readouterr().err


@pytest.mark.parametrize("collection,ok", [("MM MALE RTW AVP", True), ("MM6 FEMALE RTW", False), (None, False)])
def test_an_ai_read_of_an_otb_brand_is_held_to_otbs_label_rule(pdf, key, collection, ok):
    """The AI's own free-text label is not used for an otb brand: the collection's first word is, as for otb."""
    cfg = _cfg(adapter="otb", join_key=None, colour_codes={}, oc_label="MM")
    read = copy.deepcopy(RESPONSE)
    read["documents"][0].update(collection=collection, label="Maison Margiela")
    docs, checks, _ = service.read_oc(pdf, cfg, reader="ai", ai_factory=FakeClient(message(read)).factory)
    assert (docs is not None) == ok, checks
    if not ok:
        assert "not MM" in service.fails(checks)[0]["Detail"]


def test_an_ai_read_of_an_adidas_brand_uses_the_description_prefixes(pdf, key):
    cfg = _cfg(adapter="adidas", join_key=None, colour_codes={}, oc_label="Y-3")
    read = copy.deepcopy(RESPONSE)
    for l in read["documents"][0]["lines"]:
        l["description"] = "Y-3 " + l["description"]
    assert service.read_oc(pdf, cfg, reader="ai", ai_factory=FakeClient(message(read)).factory)[0]
    plain = service.read_oc(pdf, cfg, reader="ai", ai_factory=FakeClient(message(RESPONSE)).factory)
    assert plain[0] is None and "is for label 'none the reader could find', not Y-3" in service.fails(plain[1])[0]["Detail"]


# ---- a confirmation dropped, its pages declared as having no order lines ----------------------------------
SECOND = [l.replace("SO-90001", "SO-90002").replace("EXJK01", "EXJK11").replace("EXTS02", "EXTS12")
          .replace("EXCP03", "EXCP13") for l in PDF_LINES]


@pytest.fixture
def two_confirmations(tmp_path):
    return make_pages_pdf(tmp_path / "two-ocs.pdf", [PDF_LINES, SECOND, TERMS])


def test_both_confirmations_read_pass(two_confirmations, key):
    both = copy.deepcopy(RESPONSE)
    second = copy.deepcopy(both["documents"][0])
    second.update(number="SO-90002", pages=[2])
    for l, s in zip(second["lines"], ("EXJK11", "EXTS12", "EXCP13")):
        l.update(style=s, page=2)
    both["documents"].append(second)
    both["pages_without_lines"] = [3]
    docs, warns, _ = ai.read(two_confirmations, _cfg(), factory=FakeClient(message(both)).factory)
    assert [d["doc"] for d in docs] == ["SO-90001", "SO-90002"]


def test_a_dropped_confirmation_is_refused_by_the_page_it_left_behind(two_confirmations, key):
    one = copy.deepcopy(RESPONSE)
    one["pages_without_lines"] = [2, 3]
    e = _refused(two_confirmations, one)
    got = _fails(e, ai.PAGES_CHECK)[0]
    assert "page 2 prints confirmation number 'SO-90002', which was not returned" in got
    assert "page 2 prints order lines (code 'EXJK11' and 7 amounts) but was listed as having none" in got
    assert "page 3" not in got


def test_a_page_of_lines_with_no_number_declared_empty_is_refused(tmp_path, key):
    rest = [l for l in SECOND if "SO-90002" not in l and "ORDER CONFIRMATION" not in l]
    pdf = make_pages_pdf(tmp_path / "cont.pdf", [PDF_LINES, rest, TERMS])
    one = copy.deepcopy(RESPONSE)
    one["pages_without_lines"] = [2, 3]
    got = _fails(_refused(pdf, one), ai.PAGES_CHECK)[0]
    assert "page 2 prints order lines (code 'EXJK11'" in got and "confirmation number" not in got


@pytest.mark.parametrize("terms", [
    ["Clause SO-12 of our terms applies to EUR 1.000,00 claims"],
    ["Ref EXJK99 applies above EUR 1.000,00"],
    ["Document Number: SO-90001 terms continued", "Penalty EUR 1.000,00 and EUR 2.000,00"],
])
def test_a_terms_page_is_not_mistaken_for_order_lines(tmp_path, key, terms):
    """A number of the same shape after other words, one code with one amount, or the returned confirmation's own
    header with amounts but no code: none is a dropped confirmation."""
    pdf = make_pages_pdf(tmp_path / "terms.pdf", [PDF_LINES, terms])
    one = copy.deepcopy(RESPONSE)
    one["pages_without_lines"] = [2]
    assert ai.read(pdf, _cfg(), factory=FakeClient(message(one)).factory)[0]


# ---- whole words, not parts of run-together text ---------------------------------------------------------
def test_a_colour_found_only_inside_other_words_is_refused():
    """'510 15/10/2026' holds '101' once spaces go, never as a word."""
    text = """Order confirmation SS - 2027/99-01 Of 01/07/2026
1 S99ZZ0001 P0001 510 15/10/2026 15/02/2027 JACKET
40 42 Tot. Qty Price Line value
1 1 2 100,00 200,00
Total order q.ty 2 Total net value 200,00 Example Atelier Ltd"""
    raw = RAW("Example Atelier Ltd", "SS - 2027/99-01",
              [L("S99ZZ0001", "P0001", "101", [("40", "1"), ("42", "1")], "2", "100,00", "200,00")], "2", "200,00")
    _, fails, _ = ai.interpret(raw, _layout_cfg(raw), [text])
    assert any("colour '101'" in f["Detail"] for f in fails if f["Check"] == ai.GROUNDING_CHECK)
    raw["documents"][0]["lines"][0]["colour"] = "510"
    assert ai.interpret(raw, _layout_cfg(raw), [text])[1] == []


@pytest.mark.parametrize("value,ok", [("VINTAGE BLACK", True), ("BLACK/CREAM LOGO", True), ("VINTAGE GREY", False),
                                      ("101", False), ("0031", True), ("20%", True)])
def test_a_wrapped_cell_is_spelled_from_whole_words_but_a_short_code_never_is(value, ok):
    block = ("rw01614 80% 0031 : 4 4 2 10 $85.00 $850.00 cotton, vintag 20% e black polyester "
             "nylon, black/ 13% cream elastane logo 1 x 01").split()
    assert ai.has_words(block, value) is ok


@pytest.mark.parametrize("label,ok", [("S", True), ("OSFM", True), ("15", False), ("XL", False), ("7.5/9", True)])
def test_a_size_is_a_word_or_the_head_of_a_size_cell(label, ok):
    words = "1 in2391 osfm/4 s/3 m/3 15/10/2026 7.5/9 8/9.5".split()
    assert ai.has_size(words, label) is ok


def test_words_run_together_by_the_default_extraction_are_read_from_the_tight_one():
    """adidas: the default extraction prints 'Y-3DADCAPBLACK', the tight one 'Y-3 DAD CAP BLACK'."""
    loose = "Example Atelier Ltd Order Number : 900000001\n1 ZZ0001 Y-3DADCAPBLACK 4 OSFM/4 30.00 120.00\n" \
            "Quantity 4 Order Value 120.00"
    tight = loose.replace("Y-3DADCAPBLACK", "Y-3 DAD CAP BLACK")
    raw = RAW("Example Atelier Ltd", "900000001", [L("ZZ0001", None, "BLACK", [("OSFM", "4")], "4", "30.00", "120.00")],
              "4", "120.00", cur="USD")
    assert ai.interpret(copy.deepcopy(raw), _layout_cfg(raw), [loose])[1] != []
    assert ai.interpret(copy.deepcopy(raw), _layout_cfg(raw), [loose], [tight])[1] == []
