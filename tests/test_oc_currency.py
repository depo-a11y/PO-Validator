"""Five readers, one currency rule, and brand templates filled by substitution. Synthetic data only."""
import copy
import csv
import importlib
import io
import types

import openpyxl
import pytest

import oc_synth
import test_oc_tool as base
import thug_synth
from oc import brand as brandm, cli, complete, finalize, poxlsx, service
from oc.adapters import otb
from oc.resolve import Resolver
from oc.util import fmt_eur, fmt_money, iso_currency
from test_oc_ai import PDF_LINES, make_pdf

USD = dict(base.BRAND, vendor="EXAMPLE USD BRAND", sku_prefix="EXUS27", cost_currency="USD", _name="ex", _path="t")
FIXED = {"admilano": oc_synth.admilano_pdf, "adidas": oc_synth.adidas_pdf, "fog": oc_synth.fog_pdf,
         "thugclub": thug_synth.portrait_pdf}
LAYOUT_WORDS = {"otb": "Maison Margiela, MM6", "admilano": "AD Milano", "adidas": "adidas AG", "fog": "Fear of God",
                "thugclub": "THUG CLUB Corp.", "joor": "JOOR order print"}


def _catalog(tmp_path):
    rows = [base._variant("1", "Replica Sneaker in White", "MEN SHOES SNEAKERS", "Active", "S99WS0001P0001 101",
                          "WHITE", s, f"ZXFW26{100 + i}", "800.00", "800.00", "310.00", i == 0)
            for i, s in enumerate(["40", "41", "42"])]
    return base._write_catalog(tmp_path / "catalog.csv", rows, base.CAT_COLS)


def _stub(monkeypatch, docs):
    monkeypatch.setattr(service, "adapter", lambda cfg: types.SimpleNamespace(parse_checked=lambda p: docs))


def _usd_docs(currency="US Dollar"):
    docs = base._docs()
    docs[0]["currency"] = currency
    return docs


# ---- five readers ---------------------------------------------------------------------
@pytest.mark.parametrize("name", brandm.ADAPTERS)
def test_every_reader_loads_and_the_service_knows_it(name):
    extra = dict(ai=dict(oc_supplier="X Ltd", join_key="{style}"), fog=dict(oc_supplier="X Ltd"),
                 adidas=dict(oc_label="Y-3"), thugclub=dict(oc_label="THUG CLUB"), joor=dict(oc_supplier="x.test"),
                 p3m=dict(oc_supplier="X Ltd"),
                 sheet=dict(join_key="{style} {colour}", sheet=dict(
                     currency="EUR", columns=dict(style="Style", colour_code="Colour", size="Size", qty="Qty",
                                                  price="Price", label="Label")))).get(name, {})
    cfg = brandm.from_mapping(dict(base.BRAND, adapter=name, **extra), "t")
    assert cfg["adapter"] == name
    assert service.adapter(cfg) is importlib.import_module(f"oc.adapters.{name}")
    assert brandm.ADAPTERS == ("otb", "admilano", "adidas", "fog", "thugclub", "joor", "p3m", "sheet", "ai")


def test_an_unknown_reader_is_refused_by_the_service_too():
    with pytest.raises(brandm.BrandError, match="unknown adapter 'pdfminer'"):
        service.adapter({"adapter": "pdfminer"})


@pytest.mark.parametrize("name", list(LAYOUT_WORDS))
def test_a_fixed_reader_on_a_layout_it_does_not_know_says_no_ai_and_points_at_the_ai_box(tmp_path, name):
    pdf = make_pdf(tmp_path / "other.pdf", PDF_LINES)
    docs, checks, info = service.read_oc(pdf, dict(base.BRAND, adapter=name))
    assert docs is None and info is None
    row = checks[0]
    assert row["Check"] == service.READER_CHECK and row["Result"] == "INFO"
    assert LAYOUT_WORDS[name] in row["Detail"] and "no AI was used" in row["Detail"]
    assert [c["Detail"] for c in checks if c["Result"] == "FAIL"] == ["no order confirmations found. " + service.AI_HINT]


@pytest.mark.parametrize("name", list(FIXED))
def test_the_cli_reads_each_fixed_layout_through_the_service(tmp_path, capsys, name):
    pdf = FIXED[name](str(tmp_path / f"{name}.pdf"))
    cfg = tmp_path / f"{name}.yaml"
    cur = "EUR" if name == "admilano" else "USD"
    who = dict(adidas="oc_label: Y-3\n", fog="oc_supplier: TEST Essentials\n", thugclub="oc_label: THUG CLUB\n").get(name, "")
    cfg.write_text(f"vendor: X\nadapter: {name}\nseason: S/S 27\nsku_prefix: XX\ncost_multiplier: 1.5\n"
                   f"cost_currency: {cur}\nmargin_floor: 2.5\n{who}")
    assert cli.main(["read", pdf, "--brand-config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert f"[INFO] Order confirmation reader: read by the built-in reader for" in out and LAYOUT_WORDS[name] in out


# ---- currency -------------------------------------------------------------------------------
@pytest.mark.parametrize("text,code", [("European Euro", "EUR"), ("Euro", "EUR"), ("EUR", "EUR"), (" eur ", "EUR"),
                                       ("€", "EUR"), ("US Dollar", "USD"), ("USD", "USD"), ("US$", "USD"),
                                       ("$", None), ("Swiss Franc", None), ("", None), (None, None)])
def test_printed_currency_text_is_normalised(text, code):
    assert iso_currency(text) == code


def test_cost_price_text_carries_the_currency():
    assert fmt_money(150, "USD") == "USD 150" and fmt_money(150.5, "USD") == "USD 150.50"
    assert fmt_eur(34) == "EUR 34" and fmt_eur(34.25) == "EUR 34.25"


@pytest.mark.parametrize("currency,brand_cur,ok", [
    ("European Euro", "EUR", True), ("EUR", "eur", True), ("US Dollar", "USD", True), ("USD", "EUR", False),
    ("European Euro", "USD", False), (None, "EUR", False), ("Swiss Franc", "EUR", False)])
def test_every_fixed_reader_refuses_an_oc_in_another_currency(monkeypatch, currency, brand_cur, ok):
    docs = _usd_docs(currency)
    _stub(monkeypatch, docs)
    got, checks, _ = service.read_oc("oc.pdf", dict(base.BRAND, cost_currency=brand_cur))
    if ok:
        assert got is docs and not service.fails(checks)
        return
    (f,) = service.fails(checks)
    assert got is None and f["Check"] == service.CURRENCY_CHECK
    assert f"SS - 1 is in {currency or 'no currency the reader could find'}" in f["Detail"]
    assert f"the brand's cost currency is {brand_cur.upper()}" in f["Detail"]


def test_a_real_usd_layout_is_refused_for_a_eur_brand(tmp_path):
    pdf = oc_synth.fog_pdf(str(tmp_path / "fog.pdf"))
    fog_cfg = dict(base.BRAND, adapter="fog", oc_supplier=["TEST Essentials"], oc_label=None)
    docs, checks, _ = service.read_oc(pdf, dict(fog_cfg, cost_currency="EUR"))
    assert docs is None and [c["Check"] for c in service.fails(checks)] == [service.CURRENCY_CHECK]
    docs, checks, _ = service.read_oc(pdf, dict(fog_cfg, cost_currency="USD"))
    assert docs and not service.fails(checks)


def test_a_usd_draft_writes_and_words_every_amount_in_usd(tmp_path, monkeypatch):
    _stub(monkeypatch, _usd_docs())
    po, review, checks = service.draft("oc.pdf", _catalog(tmp_path), USD)
    assert po is not None
    cp = review[review["Column"] == poxlsx.COST_PRICE]
    assert set(cp["Value"]) == {"USD 200"}
    cost_why = set(review[review["Column"] == "Variant Cost"]["Why"])
    assert cost_why == {"USD 200 x 1.5 (season cost convention)"}
    by = {c["Check"]: c["Detail"] for c in checks}
    assert "6 units, USD 1,200.00 (printed 6 / USD 1,200.00)" in by["OC totals reproduced (parsed = printed)"]
    assert by["PO Order Qty and Total Order equal the OC"] == "6 units, USD 1,200.00"
    blob = review.to_csv() + str(checks)
    assert "EUR" not in blob
    ws = openpyxl.load_workbook(io.BytesIO(po))["Sheet1"]
    for col in ("Wholesale Price", "Total Order"):
        fmt = ws.cell(2, poxlsx.PO_HEADER.index(col) + 1).number_format
        assert '"USD "' in fmt and "€" not in fmt
    assert openpyxl.load_workbook(io.BytesIO(po))["Sheet1"].cell(
        2, poxlsx.PO_HEADER.index(poxlsx.COST_PRICE) + 1).value == "USD 200"


def test_a_eur_draft_keeps_the_euro_format():
    assert poxlsx.money_format("EUR") == poxlsx.EUR_FMT


def test_complete_checks_and_writes_cost_price_in_the_brand_currency(tmp_path):
    cat = base.catm.load(_catalog(tmp_path))
    row = {"Command": "MERGE", "Title": "Replica Sneaker in White", "Vendor": "EXAMPLE USD BRAND",
           "Type": "MEN SHOES SNEAKERS", "Option2 Value": "41", "Variant SKU": "ZXFW26101", "Variant Price": 800.0,
           poxlsx.CODE: "S99WS0001P0001 101", "Order Qty": 1, "Wholesale Price": 200.0, "Total Order": 200.0,
           "Variant Cost": 300.0, poxlsx.MADE_IN: "Italy"}

    def run(cost_price, **kw):
        hand = tmp_path / "hand.xlsx"
        base._hand_po(hand, [dict(row, **{poxlsx.COST_PRICE: cost_price})])
        out = tmp_path / "po.xlsx"
        checks = complete.run(str(hand), _usd_docs(), USD, cat, base._hist(), base._tags(), str(out), **kw)
        return {c["Check"]: c for c in checks}, out
    ok, _ = run("USD 200")
    assert ok["cost_price metafield = 'USD <wholesale>' on every row"]["Result"] == "PASS"
    assert "USD" in ok["Order Qty and Total Order equal the OC"]["Detail"]
    bad, _ = run("EUR 200")
    assert bad["cost_price metafield = 'USD <wholesale>' on every row"]["Result"] == "FAIL"
    _, out = run("USD 150", price_overrides={"S99WS0001P0001 101": 200.0})
    ws = openpyxl.load_workbook(out).worksheets[0]
    assert ws.cell(2, poxlsx.PO_HEADER.index(poxlsx.COST_PRICE) + 1).value == "USD 200"


def test_finalize_words_and_formats_the_delivery_file_in_the_brand_currency(tmp_path):
    po = base._finalize_po(tmp_path)
    cat = base.catm.load(_catalog(tmp_path))
    prefix = str(tmp_path / "x-")
    checks = finalize.run(po, dict(USD, vendor="MAISON MARGIELA"), cat, prefix)
    note = next(c["Detail"] for c in checks if "Delivery.xlsx" in str(c["Detail"]))
    assert " USD " in note and "EUR" not in note
    ws = openpyxl.load_workbook(prefix + "Delivery.xlsx")["Sheet 1"]
    hdr = [c.value for c in ws[1]]
    assert '"USD "' in ws.cell(2, hdr.index("Wholesale Price") + 1).number_format


def test_the_gates_word_money_in_the_documents_currency():
    d = _usd_docs()
    d[0]["net"] = 1201.0
    tail = f". {otb.NOT_ADDING_UP}"
    assert otb.gate_problems(d) == ["SS - 1: its lines add up to USD 1,200.00, but it prints a net total of USD 1,201.00" + tail]
    d[0]["currency"] = None
    assert otb.gate_problems(d) == ["SS - 1: its lines add up to 1,200.00, but it prints a net total of 1,201.00" + tail]
    d[0]["currency"] = "European Euro"
    assert otb.gate_problems(d) == ["SS - 1: its lines add up to EUR 1,200.00, but it prints a net total of EUR 1,201.00" + tail]


# ---- title_tag and body_html ------------------------------------------------------------------
@pytest.mark.parametrize("key,tpl", [
    ("title_tag", "{vendor:>999999999} - {title}"), ("title_tag", "{vendor} - {title!r}"),
    ("title_tag", "{vendor.upper} - {title}"), ("title_tag", "{vendor} - {title[0]}"),
    ("title_tag", "{{vendor}} - {title}"), ("title_tag", "{vendor} - {titel}"), ("title_tag", "{vendor} - {}"),
    ("body_html", "<p>{composition:>99999999}</p>"), ("body_html", "<p>{code!s}</p>"),
    ("body_html", "<p>{made_in.__class__}</p>"), ("body_html", "<p>{title}</p>"), ("body_html", "<p>{code</p>"),
])
def test_a_text_template_with_any_other_brace_is_refused_at_load(key, tpl):
    with pytest.raises(brandm.BrandError) as e:
        brandm.from_mapping(dict(base.BRAND, **{key: tpl}), "uploaded file x.yaml")
    assert f"{key} {tpl!r} may use only" in str(e.value)
    assert "no format specs, conversions or other braces" in str(e.value)


def test_the_default_and_plain_templates_load():
    cfg = brandm.from_mapping(dict(base.BRAND), "t")
    assert cfg["title_tag"] == brandm.DEFAULTS["title_tag"] and cfg["body_html"] == brandm.DEFAULTS["body_html"]
    brandm.from_mapping(dict(base.BRAND, title_tag="{title} | {vendor}", body_html="<p>{code}</p>"), "t")


def test_text_templates_are_filled_by_substitution_never_by_format():
    """Even a template that skipped the load check is copied literally, never interpreted."""
    b = dict(base.BRAND, title_tag="{vendor:>30}|{title}|{vendor}", body_html="{code!r}|{composition}|{made_in}")
    R = Resolver(b, types.SimpleNamespace(handles=set()), base._hist(), [])
    assert R.title_tag("Tee") == "{vendor:>30}|Tee|MAISON MARGIELA"
    assert R.body_html("A<1", "Cotton & Silk", "Italy") == "{code!r}|Cotton &amp; Silk|Italy"


def test_the_default_templates_fill_as_before():
    assert all(base.BRAND[k] == brandm.DEFAULTS[k] for k in brandm.TEXT_TEMPLATES)
    R = Resolver(dict(base.BRAND), types.SimpleNamespace(handles=set()), base._hist(), [])
    assert R.title_tag("Replica Sneaker in White") == "MAISON MARGIELA - Replica Sneaker in White |MARAIS"
    assert R.body_html("S99 1", "100% Calf", "Italy") == \
        "<p>Composition: 100% Calf</p> <p>Made in Italy</p> <p>SKU: S99 1</p>"
