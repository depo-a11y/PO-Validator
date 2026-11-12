"""A NEW product's retail price comes only from a brand RRP in AUD (Colwyn, 2026-11-12: "we can keep price blank
unless theres an rrp"). Synthetic data only; every multiplier is a round test value."""
import copy
import csv
import io

import openpyxl
import pandas as pd
import pytest

import oc_synth
import test_oc_tool as base
from oc import cart as cartm, catalog as catm, complete, draft as draftm, finalize, poxlsx, service
from oc.adapters import ai, fog
from oc.resolve import COLOUR, NEEDED, NO_RRP
from test_oc_ai import RESPONSE, FakeClient, _cfg, _mut, make_pdf, message, PDF_LINES
from test_oc_cart import CART_HEADER, CART_ROWS, write_cart

NEW_CODE = "S99WS0001P0001 900"   # the base docs' second line: not live in the base catalog, so drafted NEW


def _catalog(tmp_path):
    rows = [base._variant("1", "Replica Sneaker in White", "MEN SHOES SNEAKERS", "Active", "S99WS0001P0001 101",
                          "WHITE", s, f"ZXFW26{100 + i}", "800.00", "800.00", "310.00", i == 0)
            for i, s in enumerate(["40", "41", "42"])]
    return catm.load(base._write_catalog(tmp_path / "catalog.csv", rows, base.CAT_COLS))


def _docs(rrp=None, cur=None):
    docs = base._docs()
    if rrp is not None:
        docs[0]["lines"][1].update(rrp=rrp, rrp_currency=cur)
    return docs


def _draft(tmp_path, docs, brand=None, cart=None):
    rows, review, checks, fills = draftm.build(docs, brand or base.BRAND, _catalog(tmp_path), base._hist(),
                                               base._tags(), cart=cart)
    new = [r for r in rows if r["Command"] == "NEW"]
    cells = {(x["Column"]): x for x in review if x["Code"] == NEW_CODE
             and x["Column"] in ("Variant Price", "Variant Compare At Price")}
    return new, cells, {c["Check"]: c for c in checks}


# ---- where a NEW price may come from --------------------------------------------------------------
def test_no_rrp_leaves_both_price_cells_blank_and_red(tmp_path):
    new, cells, checks = _draft(tmp_path, _docs())
    assert {(r["Variant Price"], r["Variant Compare At Price"]) for r in new} == {(None, None)}
    for col in ("Variant Price", "Variant Compare At Price"):
        assert (cells[col]["Source"], cells[col]["Colour"], cells[col]["Why"]) == (NEEDED, "red", NO_RRP)
    assert NO_RRP == "price needed, no RRP from brand" and COLOUR[NEEDED] == "red"


def test_an_aud_rrp_on_the_oc_is_the_price(tmp_path):
    new, cells, checks = _draft(tmp_path, _docs(rrp=950.0, cur="AUD"))
    assert {(r["Variant Price"], r["Variant Compare At Price"]) for r in new} == {(950.0, 950.0)}
    assert cells["Variant Price"]["Source"] == "OC" and cells["Variant Price"]["Colour"] == "green"
    assert cells["Variant Price"]["Why"] == "brand RRP A$950 (SS - 1 line 2)"
    assert checks["Every NEW row has a retail price"]["Result"] == "PASS"


@pytest.mark.parametrize("cur,named", [("USD", "USD 950"), ("EUR", "EUR 950"), (None, "in an unknown currency 950"),
                                       ("Swiss Franc", "in an unknown currency 950")])
def test_an_rrp_in_another_or_unknown_currency_is_shown_never_used(tmp_path, cur, named):
    new, cells, checks = _draft(tmp_path, _docs(rrp=950.0, cur=cur))
    assert {r["Variant Price"] for r in new} == {None}
    why = cells["Variant Price"]["Why"]
    assert why.startswith(NO_RRP) and f"the brand's RRP is {named} (SS - 1 line 2), not an AUD price" in why
    assert cells["Variant Price"]["Colour"] == "red"


def _cart(tmp_path, retail, currency):
    rows = [list(r) for r in CART_ROWS]
    sneaker = rows[0]
    sneaker[CART_HEADER.index("Sugg. Retail")] = retail
    sneaker[CART_HEADER.index("Currency")] = currency
    return cartm.load(write_cart(tmp_path / "cart.xlsx", rows=rows))


def test_a_cart_sugg_retail_is_read_with_the_carts_currency(tmp_path):
    c = _cart(tmp_path, 950, "AUD")["rows"][NEW_CODE]
    assert (c["rrp"], c["rrp_currency"]) == (950.0, "AUD")
    c = _cart(tmp_path, 0, "AUD")["rows"][NEW_CODE]
    assert c["rrp"] is None  # OTB carts carry 0: that is no RRP


@pytest.mark.parametrize("currency,price,source", [("AUD", 950.0, "cart"), ("EUR", None, NEEDED), ("", None, NEEDED)])
def test_a_cart_rrp_is_the_price_only_in_aud(tmp_path, currency, price, source):
    new, cells, _ = _draft(tmp_path, _docs(), cart=_cart(tmp_path, 950, currency))
    assert {r["Variant Price"] for r in new} == {price}
    assert cells["Variant Price"]["Source"] == source
    if price is None:
        assert "Sugg. Retail" in cells["Variant Price"]["Why"]


def test_the_ocs_own_rrp_comes_before_the_carts(tmp_path):
    new, cells, _ = _draft(tmp_path, _docs(rrp=900.0, cur="AUD"), cart=_cart(tmp_path, 950, "AUD"))
    assert {r["Variant Price"] for r in new} == {900.0} and cells["Variant Price"]["Source"] == "OC"


def test_past_pos_and_live_siblings_never_price_a_new_product(tmp_path):
    """The same style+fabric is live at A$800 in the catalog: that used to be the price. Now it is blank."""
    new, cells, _ = _draft(tmp_path, _docs())
    assert {r["Variant Price"] for r in new} == {None}
    assert not hasattr(base._hist(), "ladder") and not hasattr(base._hist(), "ratio")


# ---- margin checks on blank and brand prices ------------------------------------------------------------
def test_a_blank_price_is_not_a_margin_failure(tmp_path):
    _, _, checks = _draft(tmp_path, _docs())
    price, margin = checks["Every NEW row has a retail price"], checks["NEW rows clear the 2.25x margin"]
    assert price["Result"] == "FAIL" and price["Detail"].startswith(NO_RRP)
    assert margin["Result"] == "PASS" and "blank rows have no price to check yet" in margin["Detail"]
    assert "Every NEW row has a retail price" in service.FIXABLE_IN_SHEET


def test_an_rrp_under_the_margin_floor_is_kept_and_flagged(tmp_path):
    """Cost is EUR 200 x 1.5 = A$300; a 2.25x floor wants A$675. A brand RRP of A$500 stays A$500."""
    new, cells, checks = _draft(tmp_path, _docs(rrp=500.0, cur="AUD"))
    assert {r["Variant Price"] for r in new} == {500.0}
    margin = checks["NEW rows clear the 2.25x margin"]
    assert margin["Result"] == "FAIL" and "kept as the brand gave them (never raised)" in margin["Detail"]
    assert f"{NEW_CODE} A$500 / A$300 = 1.67x" in margin["Detail"]
    assert service.blocking([margin]) == []  # fixable in the sheet: the draft is still handed over


# ---- the readers that print an RRP -----------------------------------------------------------------------
def test_fog_reads_its_msrp_as_an_rrp_in_the_documents_currency(tmp_path):
    docs = fog.parse_checked(oc_synth.fog_pdf(str(tmp_path / "fog.pdf")))
    lines = [l for d in docs for l in d["lines"]]
    assert [(l["rrp"], l["rrp_currency"]) for l in lines] == [(l["msrp"], "USD") for l in lines]
    assert any(l["rrp"] for l in lines)


def _ai_read(pdf, rrp_text, rrp_currency):
    def f(d, doc, ls):
        ls[0].update(rrp_text=rrp_text, rrp_currency=rrp_currency)
    return ai.read(pdf, _cfg(), factory=FakeClient(message(_mut(f))).factory)


def test_the_ai_reader_returns_an_rrp_with_its_currency(tmp_path, monkeypatch):
    monkeypatch.setenv(ai.KEY_NAME, "sk-ant-test")
    pdf = make_pdf(tmp_path / "rrp.pdf", [l.replace("EUR 480,00", "EUR 480,00 RRP AUD 980,00") for l in PDF_LINES])
    docs, _, _ = _ai_read(pdf, "AUD 980,00", "AUD")
    assert (docs[0]["lines"][0]["rrp"], docs[0]["lines"][0]["rrp_currency"]) == (980.0, "AUD")
    docs, _, _ = _ai_read(pdf, "AUD 980,00", None)
    assert docs[0]["lines"][0]["rrp_currency"] is None  # not shown: unknown, so never used as an AUD price
    for key in ("rrp_text", "rrp_currency"):
        assert key in ai.LINE_KEYS and key in ai.SCHEMA["properties"]["documents"]["items"]["properties"]["lines"][
            "items"]["required"]


def test_an_ai_rrp_must_be_printed_in_its_lines_part_of_the_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv(ai.KEY_NAME, "sk-ant-test")
    pdf = make_pdf(tmp_path / "plain.pdf", PDF_LINES)
    with pytest.raises(ai.ReadRefused) as e:
        _ai_read(pdf, "AUD 980,00", "AUD")
    assert any("rrp 'AUD 980,00'" in c["Detail"] for c in e.value.checks if c["Check"] == ai.GROUNDING_CHECK)


# ---- complete and finalize ---------------------------------------------------------------------------------
def _hand(tmp_path, price):
    row = {"Command": "NEW", "Title": "Replica Sneaker in Black", "Vendor": "MAISON MARGIELA", "Status": "Active",
           "Type": "MEN SHOES SNEAKERS", "Published": False, "Option1 Name": "Colour", "Option1 Value": "BLACK",
           "Option2 Name": "Size", "Option2 Value": "41", "Variant SKU": "MMSS27091", "Variant Price": price,
           poxlsx.CODE: NEW_CODE, "Order Qty": 2, "Wholesale Price": 200.0, "Total Order": 400.0,
           "Variant Cost": 300.0, poxlsx.COST_PRICE: "EUR 200", poxlsx.MADE_IN: "Italy", poxlsx.SEASON: "S/S 27",
           poxlsx.NEW_SALE: "NEW", "Body HTML": "<p>x</p>", poxlsx.TITLE_TAG: "MAISON MARGIELA - Replica Sneaker in Black |MARAIS"}
    hand = tmp_path / "hand.xlsx"
    base._hand_po(hand, [row])
    return hand


@pytest.mark.parametrize("rrp,cur,price", [(None, None, None), (950.0, "USD", None), (950.0, "AUD", 950)])
def test_complete_fills_a_blank_price_only_from_an_aud_rrp(tmp_path, rrp, cur, price):
    out = tmp_path / "po.xlsx"
    checks = complete.run(str(_hand(tmp_path, None)), _docs(rrp, cur), base.BRAND, _catalog(tmp_path), base._hist(),
                          base._tags(), str(out))
    ws = openpyxl.load_workbook(out).worksheets[0]
    col = {c.value: c.column for c in ws[1] if c.value}
    assert ws.cell(2, col["Variant Price"]).value == price
    assert (ws.cell(2, col["Variant Compare At Price"]).value is None) == (price is None)  # no formula over a blank
    by = {c["Check"]: c for c in checks}
    assert by["Every NEW row has a retail price"]["Result"] == ("PASS" if price else "FAIL")
    if not price:
        assert by["Every NEW row has a retail price"]["Detail"].startswith(NO_RRP + ": rows 2")
        assert by["NEW rows clear the 2.25x margin"]["Result"] == "PASS"


def test_finalize_refuses_a_blank_new_price_naming_rows_and_skus(tmp_path):
    prefix = str(tmp_path / "x-")
    checks = finalize.run(str(_hand(tmp_path, None)), base.BRAND, _catalog(tmp_path), prefix)
    by = {c["Check"]: c for c in checks}
    f = by["Every NEW row has a retail price"]
    assert f["Result"] == "FAIL"
    assert f["Detail"] == "fill the retail price: 1 NEW row(s) have none, PO rows 2 (SKUs MMSS27091)"
    assert not (tmp_path / "x-PU-new.xlsx").exists()
    assert "PU-new passes the po-validator gate (validator_core.run_gate)" not in by


def test_finalize_takes_a_price_a_person_filled_in(tmp_path):
    checks = finalize.run(str(_hand(tmp_path, 800.0)), base.BRAND, _catalog(tmp_path), str(tmp_path / "x-"))
    assert {c["Check"]: c for c in checks}["Every NEW row has a retail price"]["Result"] == "PASS"
