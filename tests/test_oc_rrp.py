"""A NEW product's retail price comes only from a brand RRP in AUD (Colwyn, 2026-11-12: "we can keep price blank
unless theres an rrp"). Synthetic data only; every multiplier is a round test value."""
import copy
import csv
import io
import zipfile

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
    assert all(r["Variant Compare At Price"] == f"=P{rows.index(r) + 2}" for r in new)  # copies its row's price
    cells = {(x["Column"]): x for x in review if x["Code"] == NEW_CODE
             and x["Column"] in ("Variant Price", "Variant Compare At Price")}
    return new, cells, {c["Check"]: c for c in checks}


# ---- where a NEW price may come from --------------------------------------------------------------
def test_no_rrp_leaves_both_price_cells_blank_and_red(tmp_path):
    new, cells, checks = _draft(tmp_path, _docs())
    assert {r["Variant Price"] for r in new} == {None}
    for col in ("Variant Price", "Variant Compare At Price"):
        assert (cells[col]["Source"], cells[col]["Colour"]) == (NEEDED, "red") and cells[col]["Why"].startswith(NO_RRP)
    assert cells["Variant Price"]["Why"] == NO_RRP
    assert NO_RRP == "price needed, no RRP from brand" and COLOUR[NEEDED] == "red"


def test_an_aud_rrp_on_the_oc_is_the_price(tmp_path):
    new, cells, checks = _draft(tmp_path, _docs(rrp=950.0, cur="AUD"))
    assert {r["Variant Price"] for r in new} == {950.0}
    assert cells["Variant Compare At Price"]["Why"] == "compare-at = price at creation (a formula)"
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


def _ai_pdf(tmp_path, printed):
    """The synthetic OC with line 1's RRP printed as `printed` (or not at all) right after its line value."""
    lines = [l.replace("EUR 480,00", f"EUR 480,00 {printed}") if printed else l for l in PDF_LINES]
    return make_pdf(tmp_path / "rrp.pdf", lines)


@pytest.mark.parametrize("printed,claimed,rrp,cur,note", [
    (None, "AUD", None, None, "the AI read an RRP 'AUD 980,00' that is not printed in this line's part of the PDF"),
    ("RRP 980,00", "AUD", 980.0, None, "the AI read the RRP '980,00' as AUD, but no AUD mark is printed"),
    ("RRP $980,00", "AUD", 980.0, None, "a bare $ names no currency"),
    ("RRP AUD 980,00", "AUD", 980.0, "AUD", None),
])
def test_an_ai_rrp_counts_only_as_far_as_the_pdf_prints_it(tmp_path, monkeypatch, printed, claimed, rrp, cur, note):
    monkeypatch.setenv(ai.KEY_NAME, "sk-ant-test")
    text = "AUD 980,00" if printed in (None, "RRP AUD 980,00") else printed.replace("RRP ", "")
    docs, warns, _ = _ai_read(_ai_pdf(tmp_path, printed), text if printed else "AUD 980,00", claimed)
    line = docs[0]["lines"][0]
    assert (line["rrp"], line["rrp_currency"]) == (rrp, cur)
    if note:
        assert note in line["rrp_note"]
    else:
        assert "rrp_note" not in line and "printed in its line as 'AUD 980,00' with a AUD mark" in line["rrp_found"]


def test_currency_marks_name_a_currency_only_when_unambiguous():
    marks = ai.currency_marks
    assert marks("rrp aud 980,00".split()) == {"AUD"} and marks(["a$980.00"]) == {"AUD"} and marks(["au$"]) == {"AUD"}
    assert marks("australian dollar".split()) == {"AUD"} and marks(["(aud)"]) == {"AUD"}
    assert marks(["$980.00"]) == set() and marks(["audio"]) == set() and marks(["us$10"]) == {"USD"}


def _ai_draft(tmp_path, monkeypatch, printed, claimed):
    monkeypatch.setenv(ai.KEY_NAME, "sk-ant-test")
    pdf = _ai_pdf(tmp_path, printed)

    def f(d, doc, ls):
        ls[0].update(rrp_text="AUD 980,00", rrp_currency=claimed)
    cfg = _cfg()
    po, review, checks = service.draft(pdf, _empty_catalog(tmp_path), cfg,
                                       ai_factory=FakeClient(message(_mut(f))).factory)
    return review[(review["Code"] == "EXJK01 GR") & (review["Column"] == "Variant Price")]


def _empty_catalog(tmp_path):
    p = tmp_path / "catalog.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        csv.DictWriter(fh, fieldnames=base.CAT_COLS).writeheader()
    return str(p)


def test_a_grounded_aud_rrp_from_an_ai_read_is_the_price_but_amber(tmp_path, monkeypatch):
    cells = _ai_draft(tmp_path, monkeypatch, "RRP AUD 980,00", "AUD")
    assert set(cells["Value"]) == {980.0} and set(cells["Source"]) == {"OC (AI read)"} and set(cells["Colour"]) == {"amber"}
    why = cells["Why"].iloc[0]
    assert why.startswith("read by AI from SO-90001 line 1; brand RRP A$980 (SO-90001 line 1, read by AI, printed in "
                          "its line as 'AUD 980,00' with a AUD mark)")


@pytest.mark.parametrize("printed", [None, "RRP 980,00", "RRP $980,00"])
def test_an_ungrounded_or_unmarked_ai_rrp_leaves_the_price_blank(tmp_path, monkeypatch, printed):
    cells = _ai_draft(tmp_path, monkeypatch, printed, "AUD")
    assert cells["Value"].isna().all() and set(cells["Colour"]) == {"red"}
    assert cells["Why"].iloc[0].startswith(NO_RRP)


# ---- complete and finalize ---------------------------------------------------------------------------------
def _hand(tmp_path, price):
    row = {"Command": "NEW", "Title": "Replica Sneaker in Black", "Vendor": "MAISON MARGIELA", "Status": "Active",
           "Type": "MEN SHOES SNEAKERS", "Published": False, "Option1 Name": "Colour", "Option1 Value": "BLACK",
           "Option2 Name": "Size", "Option2 Value": "41", "Variant SKU": "ZXSS27091", "Variant Price": price,
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
    assert f["Detail"] == "fill the retail price: 1 NEW row(s) have none, PO rows 2 (SKUs ZXSS27091)"
    assert not (tmp_path / "x-PU-new.xlsx").exists()
    assert "PU-new passes the po-validator gate (validator_core.run_gate)" not in by


def test_finalize_takes_a_price_a_person_filled_in(tmp_path):
    checks = finalize.run(str(_hand(tmp_path, 800.0)), base.BRAND, _catalog(tmp_path), str(tmp_path / "x-"))
    assert {c["Check"]: c for c in checks}["Every NEW row has a retail price"]["Result"] == "PASS"


def test_complete_marks_an_ai_read_aud_rrp_amber(tmp_path):
    docs = _docs(rrp=950.0, cur="AUD")
    for l in docs[0]["lines"]:
        l["reader"] = "ai"
    docs[0]["lines"][1]["rrp_found"] = "read by AI, printed in its line as 'AUD 950,00' with a AUD mark"
    out = tmp_path / "po.xlsx"
    complete.run(str(_hand(tmp_path, None)), docs, base.BRAND, _catalog(tmp_path), base._hist(), base._tags(), str(out))
    ws = openpyxl.load_workbook(out)["Review"]
    hdr = [c.value for c in ws[1]]
    rows = [dict(zip(hdr, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]
    (price,) = [r for r in rows if r["Column"] == "Variant Price"]
    assert (price["Value"], price["Source"], price["Colour"]) == (950, "OC (AI read)", "amber")
    assert price["Why"].startswith("read by AI from SS - 1 line 2; brand RRP A$950 (SS - 1 line 2, read by AI")
    po = openpyxl.load_workbook(out).worksheets[0]
    col = {c.value: c.column for c in po[1] if c.value}
    assert po.cell(2, col["Variant Price"]).fill.fgColor.rgb == "00FFEB9C"


# ---- an AI-read RRP must be labelled as one and not be the line's own price (review 2026-11-12-buyer-1) ----------
def _aud_read(tmp_path, monkeypatch, lines, rrp_text):
    """The synthetic OC billed in AUD (as Diesel and Kenzo bill), line 1's RRP read by the AI as `rrp_text`."""
    monkeypatch.setenv(ai.KEY_NAME, "sk-ant-test")
    pdf = make_pdf(tmp_path / "aud.pdf", [l.replace("EUR", "AUD") for l in lines])
    cfg = dict(_cfg(), cost_currency="AUD")

    def f(d, doc, ls):
        doc.update(currency="AUD", net_total_text=doc["net_total_text"].replace("EUR", "AUD"))
        for l in ls:
            l.update(price_text=l["price_text"].replace("EUR", "AUD"),
                     line_value_text=l["line_value_text"].replace("EUR", "AUD"))
        ls[0].update(rrp_text=rrp_text, rrp_currency="AUD")
    docs, _, _ = ai.read(pdf, cfg, factory=FakeClient(message(_mut(f))).factory)
    return docs[0]["lines"][0]


LABELLED = [l.replace("Qty Unit Price Total", "Qty Unit Price Total RRP", 1).replace("EUR 480,00", "EUR 480,00 EUR 980,00")
            for l in PDF_LINES]


@pytest.mark.parametrize("text,what", [("AUD 480,00", "line value"), ("AUD 120,00", "unit price")])
def test_an_ai_rrp_that_is_the_lines_own_price_or_value_is_never_used(tmp_path, monkeypatch, text, what):
    line = _aud_read(tmp_path, monkeypatch, LABELLED, text)
    assert (line["rrp"], line["rrp_currency"]) == (None, None)
    assert line["rrp_note"] == f"the AI read an RRP '{text}' that is this line's {what}; not used"
    line = _aud_read(tmp_path, monkeypatch, LABELLED, "AUD 980,00")   # the labelled RRP itself is kept
    assert (line["rrp"], line["rrp_currency"]) == (980.0, "AUD")


HEADER_LABEL = PDF_LINES[:2] + ["Qty Unit Price Total RRP"] + [l.replace("EUR 480,00", "EUR 480,00 EUR 980,00")
                                                               for l in PDF_LINES[2:]]


@pytest.mark.parametrize("lines,kept", [
    ([l.replace("EUR 480,00", "EUR 480,00 EUR 980,00") for l in PDF_LINES], False),   # an amount with no label
    (HEADER_LABEL, True),                                                              # label in the column header
    ([l.replace("EUR 480,00", "EUR 480,00 RRP EUR 980,00") for l in PDF_LINES], True),  # label in its line
])
def test_an_ai_rrp_needs_an_rrp_label_in_its_line_or_column_header(tmp_path, monkeypatch, lines, kept):
    line = _aud_read(tmp_path, monkeypatch, lines, "AUD 980,00")
    assert (line["rrp"], line["rrp_currency"]) == ((980.0, "AUD") if kept else (None, None))
    if not kept:
        assert line["rrp_note"] == ("the AI read an RRP 'AUD 980,00', but no RRP, MSRP, retail, suggested or "
                                    "recommended label is printed in its line or its column header; not used")


def test_an_rrp_label_is_a_retail_price_word_not_the_retailer():
    from oc.util import rrp_label
    assert all(map(rrp_label, ["RRP", "MSRP", "Retail", "Sugg.", "Recommended", "RRP(AUD)", "RetailPrice", "r.r.p."]))
    assert not any(map(rrp_label, ["Retailer", "Retailer:", "RETAILERS", "Price", "Total", "Wholesale", "Unit"]))


@pytest.mark.parametrize("word,read", [("MSRP", True), ("RETAIL", True), ("MARKUP", False)])
def test_fog_takes_its_rrp_only_from_a_column_labelled_as_one(tmp_path, monkeypatch, word, read):
    monkeypatch.setattr(oc_synth, "FOG_COLS", tuple((word if w == "MSRP" else w, x) for w, x in oc_synth.FOG_COLS))
    pdf = str(tmp_path / "fog.pdf")
    if read:
        lines = [l for d in fog.parse_checked(oc_synth.fog_pdf(pdf)) for l in d["lines"]]
        assert [l["rrp"] for l in lines] == [m for *_, m in oc_synth.FOG_LINES] and any(l["rrp"] for l in lines)
    else:
        with pytest.raises(fog.GateError, match="matches no free PRICE/TOTAL column"):  # an amount there is refused
            fog.parse_checked(oc_synth.fog_pdf(pdf))
        blank = [l[:-1] + (None,) for l in oc_synth.FOG_LINES]
        lines = [l for d in fog.parse_checked(oc_synth.fog_pdf(pdf, lines=blank)) for l in d["lines"]]
        assert {l["rrp"] for l in lines} == {None}


# ---- a NEW compare-at copies the price a person types (review 2026-11-12-buyer-1) ---------------------------------
def _typed_draft(tmp_path, price, cache):
    """The draft as Step 1 writes it; a person types `price` into each NEW row's Variant Price (only there), names
    the colour in the title and its family in Option1, and saves. `cache`: None as openpyxl saves (no cached values), else the value a
    spreadsheet program stored with each compare-at formula."""
    rows, review, checks, fills = draftm.build(base._docs(), base.BRAND, _catalog(tmp_path), base._hist(), base._tags())
    path = tmp_path / "draft.xlsx"
    poxlsx.write_po(str(path), poxlsx.PO_HEADER, rows, review, checks, fills)
    wb = openpyxl.load_workbook(path)
    ws = wb.worksheets[0]
    col = {c.value: c.column for c in ws[1] if c.value}
    new = [r for r in range(2, ws.max_row + 1) if ws.cell(r, col["Command"]).value == "NEW"]
    for r in new:
        assert ws.cell(r, col["Variant Compare At Price"]).data_type == "f"
        ws.cell(r, col["Variant Price"]).value = price
        ws.cell(r, col["Option1 Value"]).value = "BLACK"
        for key in ("Title", poxlsx.TITLE_TAG):
            ws.cell(r, col[key]).value = ws.cell(r, col[key]).value.replace(" in 900", " in Black")
    wb.save(path)
    if cache is not None:
        with zipfile.ZipFile(path) as z:
            parts = {n: z.read(n) for n in z.namelist()}
        xml = parts["xl/worksheets/sheet1.xml"].decode()
        for r in new:
            xml = xml.replace(f"<f>P{r}</f><v></v>", f"<f>P{r}</f><v>{cache}</v>", 1)
        assert xml.count("</f><v>") == len(new)
        parts["xl/worksheets/sheet1.xml"] = xml.encode()
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for n, b in parts.items():
                z.writestr(n, b)
    return path


@pytest.mark.parametrize("cache", [None, 950, 111])   # openpyxl-saved; cached as Excel saves it; a stale cache
def test_a_typed_new_price_reaches_compare_at_with_or_without_cached_values(tmp_path, cache):
    checks = finalize.run(str(_typed_draft(tmp_path, 950, cache)), base.BRAND, _catalog(tmp_path),
                          str(tmp_path / "x-"))
    assert not [c for c in checks if c["Result"] == "FAIL"], [c for c in checks if c["Result"] == "FAIL"]
    pu_new = pd.read_excel(tmp_path / "x-PU-new.xlsx", dtype=str)
    assert set(pu_new["Variant Price"]) == set(pu_new["Variant Compare At Price"]) == {"950"}


def test_only_the_rows_own_price_formula_is_saved_as_a_formula(tmp_path):
    rows = [{"Command": "NEW", "Variant Price": 950, "Variant Compare At Price": v, "Title": t}
            for v, t in (("=P2", "=P2"), ("=P2", "=A1"), ("=SUM(A1:A9)", "x"))]
    path = tmp_path / "po.xlsx"
    poxlsx.write_po(str(path), poxlsx.PO_HEADER, rows)
    ws = openpyxl.load_workbook(path).worksheets[0]
    col = {c.value: c.column for c in ws[1] if c.value}
    got = [(ws.cell(r, col["Variant Compare At Price"]).data_type, ws.cell(r, col["Title"]).data_type) for r in (2, 3, 4)]
    assert got == [("f", "s"), ("s", "s"), ("s", "s")]   # row 3's '=P2' points at another row's price: text
