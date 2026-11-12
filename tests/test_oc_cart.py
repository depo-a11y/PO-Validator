"""The selling-session cart input, on a tiny synthetic cart, catalog and OC (no real data)."""
import csv

import openpyxl
import pytest

import validator_core as core
from oc import cart as cartm, catalog as catm, history as histm
from oc.complete import run as complete_run
from oc.draft import build
from oc import poxlsx
from oc.resolve import Resolver

BRAND = dict(
    vendor="MAISON MARGIELA", adapter="otb", season="S/S 27", sku_prefix="ZXSS27", sku_digits=3,
    cost_currency="EUR", cost_multiplier=1.5, margin_floor=2.25, price_round=50, status="Active",
    published_new=False, published_merge=True, new_sale="NEW", option1_name="Colour", option2_name="Size",
    title_tag="{vendor} - {title} |MARAIS",
    body_html="<p>Composition: {composition}</p> <p>Made in {made_in}</p> <p>SKU: {code}</p>",
    unknown="-", womens_prefix="Womens ", history=[], default_gender="WOMEN",
    product_class_types={"SNEAKERS": "SHOES SNEAKERS", "SPORTSJACKET": "CLOTHING JACKETS LIGHTWEIGHT"},
    subcategory_types={"RINGS": "ACCS JEWELRY RINGS"},
    colour_families={"BLACK": ["black"], "WHITE": ["white", "optic white"], "BEIGE": ["beige"],
                     "BROWN": ["coffee"]},
    country_names={"IT": "Italy"},
)

SNEAKER, JACKET_A, JACKET_B, RING, STRAY = ("S99WS0001P0001 900", "S98AA0002M00001 961", "S98AA0002M00002 961",
                                            "S97UQ0003P0003 951", "S96ZZ0004P0004 961")
CART_HEADER = ["Style Image", "Style Name", "Model", "Fabric", "Color Code", "Color Description", "Gender",
               "Collection", "Category", "Subcategory", "Materials", "Made In", "Size Grid", "Available Sizes",
               "S", "M", "S", "Units", "Currency", "Sugg. Retail", "Wholesale"]


def _cart_row(cls, model, fabric, colour, desc, gender, sub, materials, made, wholesale):
    return [None, cls, model, fabric, colour, desc, gender, "TEST AVP", "Apparel", sub, materials, made,
            "XS,S,M,L", "S,M", 1, 1, None, 2, "EUR", 0, wholesale]


CART_ROWS = [
    _cart_row("SNEAKERS", "S99WS0001", "P0001", "900", "Washed black", "Male", "Sneakers",
              "Upper: 100% Calf Leather", "Made In Italy", 210),
    _cart_row("SPORTSJACKET", "S98AA0002", "M00001", "961", "Beige", "Female", "Sportsjackets",
              "Primary Fabric: 100% Cotton", "", 300),
    _cart_row("SPORTSJACKET", "S98AA0002", "M00002", "961", "Optic white", "Female", "Sportsjackets",
              "Primary Fabric: 100% Nylon", "", 300),
    _cart_row("Minimal Logo 8mm", "S97UQ0003", "P0003", "951", "Shiny palladium", "Female", "Rings",
              "Body: 100% Silver", "MADE IN ITALY", 80),
    _cart_row("KEY RING", "S95XX0005", "P0005", "T8013", "Black", "Unisex", "Key Rings", "", "", 50),
]


def write_cart(path, rows=CART_ROWS, preamble=True, header=CART_HEADER):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DOOR 001-TEST"
    if preamble:
        for r in (["Brand", "TEST"], ["Season", "SS 2027"], ["Session Code", "TESTSES"], []):
            ws.append(r)
    ws.append(header)
    for r in rows:
        poxlsx.append_text(ws, r)            # a supplier's text cell, even one starting '='
    ws.append([None, None, None, None, None, None, None, None, None, None, None, None, None, None, 5, 5])  # totals
    wb.save(path)
    return str(path)


CAT_COLS = list(catm.REQUIRED) + ["Body HTML", "Variant Country of Origin", catm.MADE_IN, "Created At"]


def _catalog(tmp_path, body="<p>Composition: 100% Calf Leather</p>"):
    rows = []
    for i, size in enumerate(["40", "41", "42"]):
        r = {c: "" for c in CAT_COLS}
        r.update({"ID": "1", "Variant ID": f"1{size}", "Option1 Name": "Colour", "Option1 Value": "WHITE",
                  "Option2 Name": "Size", "Option2 Value": size, "Variant SKU": f"ZXFW26{100 + i}",
                  "Variant Price": "800.00", "Variant Compare At Price": "800.00", "Variant Cost": "310.00",
                  "Top Row": "true" if i == 0 else ""})
        if i == 0:
            r.update({"Handle": "replica-sneaker-in-white", "Title": "Replica Sneaker in White",
                      "Vendor": "MAISON MARGIELA", "Type": "MEN SHOES SNEAKERS", "Tags": "MEN", "Status": "Active",
                      catm.MCODE: "S99WS0001P0001 101", "Body HTML": body,
                      "Created At": "2025-01-01 10:00:00 +1100"})
        rows.append(r)
    p = tmp_path / "catalog.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CAT_COLS)
        w.writeheader()
        w.writerows(rows)
    return catm.load(str(p))


@pytest.fixture
def catalog(tmp_path):
    return _catalog(tmp_path)


def _line(no, code, cls, sizes, price):
    body, colour = code.split(" ")
    qty = sum(sizes.values())
    return dict(line_no=no, style=body[:9], fabric=body[9:], colour=colour, product_class=cls,
                sizes=sizes, qty=qty, price=price, line_value=qty * price)


def _docs():
    shoes = [_line(1, SNEAKER, "SNEAKERS", {"41": 1, "42": 1}, 200.0)]
    rtw = [_line(1, JACKET_A, "SPORTSJACKET", {"S": 1}, 300.0), _line(2, JACKET_B, "SPORTSJACKET", {"M": 1}, 300.0),
           _line(3, RING, "Minimal Logo 8mm", {"OS": 2}, 80.0), _line(4, STRAY, "SPORTSJACKET", {"S": 1}, 300.0)]
    return [dict(doc="SS - 1", collection="TEST MALE SHOES AVP", gender="MEN", lines=shoes,
                 total_qty=2, net=400.0),
            dict(doc="SS - 2", collection="TEST RTW AVP", gender=None, lines=rtw,
                 total_qty=5, net=sum(l["line_value"] for l in rtw))]


def _hist():
    return histm.History([("F/W 26", [dict(eur=e, price=e * 4.0, type="MEN SHOES SNEAKERS", code=f"Y {e}")
                                      for e in (101.0, 102.0, 103.0, 104.0, 105.0)])])


def _draft(catalog, cart):
    return build(_docs(), BRAND, catalog, _hist(), core.load_tag_lookup(), cart=cart)


def _first(rows, code):
    return next(r for r in rows if r[poxlsx.CODE] == code)


def _review(review, code, column):
    return next(x for x in review if x["Code"] == code and x["Column"] == column)


def _check(checks, name):
    return [c for c in checks if c["Check"] == name]


# ---- loading ------------------------------------------------------------------

def test_load_finds_header_by_content_and_keys_on_full_join_key(tmp_path):
    c = cartm.load(write_cart(tmp_path / "c.xlsx"))
    assert c["label"] == "cart TESTSES" and c["sheet"] == "DOOR 001-TEST"
    assert set(c["rows"]) == {SNEAKER, JACKET_A, JACKET_B, RING, "S95XX0005P0005 T8013"}
    assert c["rows"][SNEAKER]["row"] == 6 and c["rows"][SNEAKER]["gender"] == "MEN"
    assert c["rows"][JACKET_A]["colour"] == "Beige" and c["rows"][JACKET_B]["colour"] == "Optic white"
    assert c["rows"][RING]["made_in"] == "Italy" and c["rows"][SNEAKER]["made_in"] == "Italy"
    assert c["rows"]["S95XX0005P0005 T8013"]["gender"] is None
    assert c["rows"][SNEAKER]["wholesale"] == 210.0
    assert cartm.load(write_cart(tmp_path / "bare.xlsx", preamble=False))["label"] == "cart bare.xlsx"


def test_load_rejects_what_is_not_a_cart(tmp_path):
    txt = tmp_path / "notes.xlsx"
    txt.write_text("not a workbook")
    with pytest.raises(cartm.CartError, match="cannot read"):
        cartm.load(str(txt))
    with pytest.raises(cartm.CartError, match="Color Description"):
        cartm.load(write_cart(tmp_path / "nohdr.xlsx", header=[h for h in CART_HEADER if h != "Color Description"]))
    with pytest.raises(cartm.CartError, match="no style-colour rows"):
        cartm.load(write_cart(tmp_path / "empty.xlsx", rows=[]))


def test_duplicate_cart_rows_keep_the_first_and_warn(tmp_path, catalog):
    dup = list(CART_ROWS) + [_cart_row("SNEAKERS", "S99WS0001", "P0001", "900", "Coffee", "Male", "Sneakers",
                                       "", "", 200)]
    c = cartm.load(write_cart(tmp_path / "c.xlsx", rows=dup))
    assert c["duplicates"] == [SNEAKER] and c["rows"][SNEAKER]["colour"] == "Washed black"
    _, _, checks, _ = _draft(catalog, c)
    assert _check(checks, "One cart row per code")[0]["Result"] == "WARN"


# ---- the draft with a cart --------------------------------------------------------

def test_cart_fills_colour_composition_made_in_and_shades_amber(tmp_path, catalog):
    rows, review, checks, fills = _draft(catalog, cartm.load(write_cart(tmp_path / "c.xlsx")))
    r = _first(rows, SNEAKER)
    assert r["Title"] == "Replica Sneaker in Washed Black"           # live stem + cart colour words
    assert r["Option1 Value"] == "BLACK"
    assert r["Body HTML"].startswith("<p>Composition: Upper: 100% Calf Leather</p> <p>Made in Italy</p>")
    assert r[poxlsx.MADE_IN] == "Italy"
    for col in ("Title", "Option1 Value", "Body HTML", poxlsx.MADE_IN):
        x = _review(review, SNEAKER, col)
        assert x["Source"] == "cart" and x["Colour"] == "amber", (col, x)
        assert "cart TESTSES row 6" in x["Why"], (col, x["Why"])
    i = rows.index(r)
    assert fills[(i, "Option1 Value")] == "amber" and fills[(i, "Title")] == "amber"
    ring = _first(rows, RING)                                         # class is a name: Type from the cart subcategory
    assert ring["Type"] == "WOMEN ACCS JEWELRY RINGS" and _review(review, RING, "Type")["Source"] == "cart"
    assert ring["Title"] == "Minimal Logo 8mm in Shiny Palladium"


def test_colour_is_keyed_on_style_fabric_and_colour_never_the_bare_code(tmp_path, catalog):
    rows, review, _, _ = _draft(catalog, cartm.load(write_cart(tmp_path / "c.xlsx")))
    a, b, stray = _first(rows, JACKET_A), _first(rows, JACKET_B), _first(rows, STRAY)
    assert (a["Title"], a["Option1 Value"]) == ("Sportsjacket in Beige", "BEIGE")
    assert (b["Title"], b["Option1 Value"]) == ("Sportsjacket in Optic White", "WHITE")
    assert a["Body HTML"] != b["Body HTML"]
    # colour 961 on a fabric the cart does not carry: nothing from the cart, red
    assert stray["Title"] == "Sportsjacket in 961" and stray["Option1 Value"] == ""
    assert {x["Source"] for x in review if x["Code"] == STRAY} & {"cart"} == set()
    assert _review(review, STRAY, "Title")["Colour"] == "red"


def test_oc_wins_on_quantity_and_price_and_a_different_cart_wholesale_is_reported(tmp_path, catalog):
    rows, review, checks, _ = _draft(catalog, cartm.load(write_cart(tmp_path / "c.xlsx")))
    shoe = [r for r in rows if r[poxlsx.CODE] == SNEAKER]
    assert {r["Wholesale Price"] for r in shoe} == {200.0}
    assert {r["Variant Cost"] for r in shoe} == {300.0}
    assert {r[poxlsx.COST_PRICE] for r in shoe} == {"EUR 200"}
    assert sum(r["Order Qty"] for r in shoe) == 2 and sum(r["Total Order"] for r in shoe) == 400.0
    assert {x["Source"] for x in review if x["Column"] in ("Wholesale Price", "Order Qty", "Total Order")} <= {"OC", "rule"}
    info = _check(checks, "Cart wholesale differs from the OC (the OC's is used)")
    assert len(info) == 1 and info[0]["Result"] == "INFO"
    assert "S99WS0001P0001 900 cart 210 vs OC EUR 200" in info[0]["Detail"] and "S98AA" not in info[0]["Detail"]


def test_coverage_checks_count_both_sides(tmp_path, catalog):
    _, _, checks, _ = _draft(catalog, cartm.load(write_cart(tmp_path / "c.xlsx")))
    m = _check(checks, "Cart matches this OC")[0]
    assert m["Result"] == "PASS" and m["Detail"].startswith("4 of 5 OC codes are in cart TESTSES")
    oc_only = _check(checks, "OC codes not in the cart")[0]
    assert oc_only["Result"] == "INFO" and oc_only["Detail"].startswith("1 code(s)") and STRAY in oc_only["Detail"]
    cart_only = _check(checks, "Cart codes not on the OC")[0]
    assert cart_only["Result"] == "INFO" and cart_only["Detail"].startswith("1 code(s)")
    assert "S95XX0005P0005 T8013" in cart_only["Detail"]


def test_a_cart_for_another_order_fails_and_changes_nothing(tmp_path, catalog):
    other = [_cart_row("KEY RING", "S95XX0005", "P0005", "T8013", "Black", "Unisex", "Key Rings", "", "", 50)]
    rows, review, checks, fills = _draft(catalog, cartm.load(write_cart(tmp_path / "c.xlsx", rows=other)))
    m = _check(checks, "Cart matches this OC")
    assert len(m) == 1 and m[0]["Result"] == "FAIL" and "wrong cart" in m[0]["Detail"]
    assert not [x for x in review if x["Source"] == "cart"]
    base_rows, base_review, _, base_fills = _draft(catalog, None)
    assert rows == base_rows and review == base_review and fills == base_fills


def test_no_cart_adds_no_cart_checks(catalog):
    _, _, checks, _ = _draft(catalog, None)
    assert not [c for c in checks if "art" in c["Check"].split()[0]]


def test_a_garbled_colour_description_is_red(tmp_path, catalog):
    rows = [list(r) for r in CART_ROWS]
    rows[0][5] = "Peach Pur}e"
    _, review, _, _ = _draft(catalog, cartm.load(write_cart(tmp_path / "c.xlsx", rows=rows)))
    t = _review(review, SNEAKER, "Title")                            # live stem, so only the colour can make it red
    assert t["Value"] == "Replica Sneaker in Peach Pur}e"
    assert t["Colour"] == "red" and "unexpected character(s) }" in t["Why"]


def test_keyword_family_takes_the_first_colour_in_the_name(catalog):
    R = Resolver(BRAND, catalog, _hist(), core.load_tag_lookup())
    assert R.keyword_family("Turkish Coffee/Black") == "BROWN"
    assert R.keyword_family("Black/Coffee") == "BLACK"
    assert R.keyword_family("Blackened Berry") is None


def test_title_prefix_follows_the_brand_gender(tmp_path, catalog):
    docs = [dict(doc="SS - 1", collection="X", gender=g, total_qty=1, net=300.0,
                 lines=[_line(1, code, "SPORTSJACKET", {"S": 1}, 300.0)])
            for g, code in (("MEN", "S98AA0002M00001 961"), ("WOMEN", "S98AB0002M00001 961"))]
    rows = [list(r) for r in CART_ROWS] + [_cart_row("SPORTSJACKET", "S98AB0002", "M00001", "961", "Beige",
                                                     "Female", "Sportsjackets", "", "", 300)]
    cart = cartm.load(write_cart(tmp_path / "c.xlsx", rows=rows))
    tags = core.load_tag_lookup()
    women_first = build(docs, BRAND, catalog, _hist(), tags, cart=cart)[0]
    assert {r["Title"] for r in women_first} == {"Sportsjacket in Beige", "Womens Sportsjacket in Beige"}
    men_first = build(docs, dict(BRAND, title_prefixes={"MEN": "Mens "}), catalog, _hist(), tags, cart=cart)[0]
    assert {r["Title"] for r in men_first} == {"Mens Sportsjacket in Beige", "Sportsjacket in Beige"}


def test_complete_fills_made_in_from_the_cart(tmp_path, catalog):
    base = {"Vendor": "MAISON MARGIELA", "Status": "Active", "Option1 Name": "Colour", "Option2 Name": "Size",
            poxlsx.SEASON: "S/S 27", poxlsx.NEW_SALE: "NEW", "Command": "NEW", "Title": "Logo Ring in Silver",
            "Type": "WOMEN ACCS JEWELRY RINGS", "Published": False, "Option1 Value": "SILVER",
            poxlsx.CODE: RING, poxlsx.MADE_IN: "-", "Body HTML": "<p>x</p>", "Wholesale Price": 80.0,
            "Variant Cost": 120.0, poxlsx.COST_PRICE: "EUR 80", "Variant Price": 400.0,
            "Option2 Value": "OS", "Order Qty": 2, "Total Order": 160.0}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(poxlsx.PO_HEADER)
    ws.append([base.get(h) if h else None for h in poxlsx.PO_HEADER])
    hand = tmp_path / "hand.xlsx"
    wb.save(hand)
    docs = [dict(doc="SS - 2", collection="TEST RTW AVP", gender=None, total_qty=2, net=160.0,
                 lines=[_line(1, RING, "Minimal Logo 8mm", {"OS": 2}, 80.0)])]
    out = tmp_path / "po.xlsx"
    checks = complete_run(str(hand), docs, BRAND, catalog, _hist(), core.load_tag_lookup(), str(out),
                          cart=cartm.load(write_cart(tmp_path / "c.xlsx")))
    _, rows = poxlsx.read_po(str(out))
    assert rows[0][poxlsx.MADE_IN] == "Italy" and rows[0]["Body HTML"] == "<p>x</p>"
    assert _check(checks, "Cart matches this OC")[0]["Result"] == "PASS"


def test_cart_value_is_green_only_where_the_live_style_agrees(tmp_path, catalog):
    rows = [list(r) for r in CART_ROWS]
    cart = cartm.load(write_cart(tmp_path / "c.xlsx", rows=rows))
    R = Resolver(BRAND, catalog, _hist(), core.load_tag_lookup(), cart=cart)
    v, src, why = R.composition(SNEAKER)                              # live sibling says '100% Calf Leather'
    assert (v, src) == ("Upper: 100% Calf Leather", "cart") and "says '100% Calf Leather'" in why
    rows[0][10] = "100%  calf leather"
    R = Resolver(BRAND, catalog, _hist(), core.load_tag_lookup(),
                 cart=cartm.load(write_cart(tmp_path / "agree.xlsx", rows=rows)))
    v, src, why = R.composition(SNEAKER)
    assert (v, src) == ("100% Calf Leather", "catalog") and "Materials agrees" in why


# ---- cart text is data: never markup, never a formula ------------------------------

def test_cart_markup_is_not_used_and_cart_text_is_escaped(tmp_path, catalog):
    rows = [list(r) for r in CART_ROWS]
    rows[0][10] = "<script>alert(1)</script>"
    rows[0][11] = "Made in <b>Italy</b>"
    rows[1][10] = "Primary Fabric: Cotton & Silk"
    rows[1][11] = "Made In Italy & France"
    drows, review, _, _ = _draft(catalog, cartm.load(write_cart(tmp_path / "c.xlsx", rows=rows)))
    shoe = _first(drows, SNEAKER)
    assert "<script" not in shoe["Body HTML"] and "<b>" not in shoe["Body HTML"]
    assert shoe["Body HTML"].startswith("<p>Composition: -</p> <p>Made in -</p>")
    assert shoe[poxlsx.MADE_IN] == "-"
    for col in ("Body HTML", poxlsx.MADE_IN):
        x = _review(review, SNEAKER, col)
        assert x["Colour"] == "red" and "holds '<' or '>'" in x["Why"], (col, x)
    jacket = _first(drows, JACKET_A)
    assert jacket["Body HTML"].startswith("<p>Composition: Primary Fabric: Cotton &amp; Silk</p> "
                                          "<p>Made in Italy &amp; France</p>")
    assert jacket[poxlsx.MADE_IN] == "Italy & France"                  # the metafield is text, not HTML


def test_a_composition_read_from_live_html_is_not_escaped_twice(tmp_path):
    cat = _catalog(tmp_path, body="<p>Composition: 100% Calf &amp; Lamb Leather</p>")
    rows, _, _, _ = _draft(cat, None)
    assert _first(rows, SNEAKER)["Body HTML"].startswith("<p>Composition: 100% Calf &amp; Lamb Leather</p>")
    crows = [list(r) for r in CART_ROWS]
    crows[0][10] = "100% Calf & Lamb Leather"                        # the same words as plain text: agrees, green
    rows, review, _, _ = _draft(cat, cartm.load(write_cart(tmp_path / "c.xlsx", rows=crows)))
    assert _first(rows, SNEAKER)["Body HTML"].startswith("<p>Composition: 100% Calf &amp; Lamb Leather</p>")
    assert "Materials agrees" in _review(review, SNEAKER, "Body HTML")["Why"]


FORMULA_LIKE = ['=HYPERLINK("http://x","y")', "+61 3", "- Lining: 100% Viscose", "@home", "\tTabbed"]


def _read_back(path, sheet=None):
    wb = openpyxl.load_workbook(path)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    return {c.value: c.data_type for row in ws.iter_rows() for c in row if isinstance(c.value, str)}


def test_formula_like_text_is_written_and_read_back_as_the_same_text(tmp_path):
    assert [v[0] for v in FORMULA_LIKE] == ["\u003d", "\u002b", "\u002d", "\u0040", "\t"]  # ASCII triggers only
    hdr = ["Title", "Body HTML", "Variant SKU", "Option1 Value", "Variant Compare At Price"]
    rows = [dict(zip(hdr, [v, "<p>x</p>", f"SKU{i}", "BLACK", "=E2"])) for i, v in enumerate(FORMULA_LIKE)]
    review = [{"Code": "X 1", "Value": v, "Why": v} for v in FORMULA_LIKE]
    out = tmp_path / "po.xlsx"
    poxlsx.write_po(str(out), hdr, rows, review, [{"Check": "c", "Result": "INFO", "Detail": FORMULA_LIKE[0]}])
    for sheet in ("Sheet1", "Review", "Checks"):
        got = _read_back(out, sheet)
        for v in FORMULA_LIKE if sheet != "Checks" else FORMULA_LIKE[:1]:
            assert got.get(v) == "s", (sheet, v, got)                 # same text, saved as text
            assert "'" + v not in got
    assert _read_back(out)["=E2"] == "s"                               # not raw: even a ref is text
    _, back = poxlsx.read_po(str(out))
    assert [r["Title"] for r in back] == FORMULA_LIKE
    poxlsx.write_po(str(out), hdr, [dict(rows[0], Title="Bad\x01Title")])  # a control character is dropped, not fatal
    assert poxlsx.read_po(str(out))[1][0]["Title"] == "BadTitle"
    poxlsx.write_po(str(out), hdr, [dict(r, _raw=dict(r)) for r in rows], raw=True)
    wb = openpyxl.load_workbook(out)
    assert wb.active["E2"].data_type == "f" and wb.active["A2"].data_type == "s"   # raw keeps only a PO ref formula


def test_a_formula_in_the_cart_reaches_the_draft_and_complete_as_text(tmp_path, catalog):
    rows = [list(r) for r in CART_ROWS]
    rows[3][11] = '=HYPERLINK("http://x","Italy")'
    cart = cartm.load(write_cart(tmp_path / "c.xlsx", rows=rows))
    drows, review, checks, fills = _draft(catalog, cart)
    made = _first(drows, RING)[poxlsx.MADE_IN]
    assert made.startswith("=")
    out = tmp_path / "draft.xlsx"
    poxlsx.write_po(str(out), poxlsx.PO_HEADER, drows, review, checks, fills)
    assert _read_back(out)[made] == "s"
    assert _read_back(out, "Review")[made] == "s"
    base = {"Vendor": "MAISON MARGIELA", "Status": "Active", "Option1 Name": "Colour", "Option2 Name": "Size",
            poxlsx.SEASON: "S/S 27", poxlsx.NEW_SALE: "NEW", "Command": "NEW", "Title": "Logo Ring in Silver",
            "Type": "WOMEN ACCS JEWELRY RINGS", "Published": False, "Option1 Value": "SILVER",
            poxlsx.CODE: RING, poxlsx.MADE_IN: None, "Body HTML": "<p>x</p>", "Wholesale Price": 80.0,
            "Variant Cost": 120.0, poxlsx.COST_PRICE: "EUR 80", "Variant Price": 400.0,
            "Option2 Value": "OS", "Order Qty": 2, "Total Order": 160.0}
    wb = openpyxl.Workbook()
    wb.active.append(poxlsx.PO_HEADER)
    wb.active.append([base.get(h) if h else None for h in poxlsx.PO_HEADER])
    wb.save(tmp_path / "hand.xlsx")
    docs = [dict(doc="SS - 2", collection="TEST RTW AVP", gender=None, total_qty=2, net=160.0,
                 lines=[_line(1, RING, "Minimal Logo 8mm", {"OS": 2}, 80.0)])]
    complete_run(str(tmp_path / "hand.xlsx"), docs, BRAND, catalog, _hist(), core.load_tag_lookup(),
                 str(tmp_path / "po.xlsx"), cart=cart)
    assert _read_back(tmp_path / "po.xlsx")[made] == "s"


def test_write_frame_is_to_excel_without_formulas(tmp_path):
    import pandas as pd
    frame = pd.DataFrame({"Title": FORMULA_LIKE, "Variant Price": [1.5, 2, None, 4, 5],
                          "Order Qty": [1, 2, 3, 4, 5]})
    out = tmp_path / "pu.xlsx"
    poxlsx.write_frame(str(out), frame, "Products")
    assert {v: t for v, t in _read_back(out).items() if v in FORMULA_LIKE} == {v: "s" for v in FORMULA_LIKE}
    back = pd.read_excel(out, sheet_name="Products")
    assert list(back["Title"]) == FORMULA_LIKE and list(back.columns) == list(frame.columns)
    assert back["Variant Price"].isna().tolist() == [False, False, True, False, False]
    assert list(back["Order Qty"]) == [1, 2, 3, 4, 5]


def test_cart_family_agreeing_with_the_catalog_colour_code_stays_green(tmp_path):
    _catalog(tmp_path)
    # two live products on other fabrics call colour 961 BEIGE: a cross-fabric vote, green without a cart
    extra = []
    for pid, code in (("7", "S90AA0001M00009 961"), ("8", "S90AB0001M00008 961")):
        r = {c: "" for c in CAT_COLS}
        r.update({"ID": pid, "Variant ID": pid + "1", "Option1 Name": "Colour", "Option1 Value": "BEIGE",
                  "Option2 Name": "Size", "Option2 Value": "M", "Variant SKU": "X" + pid, "Variant Price": "500",
                  "Variant Compare At Price": "500", "Variant Cost": "200", "Top Row": "true", "Handle": "h" + pid,
                  "Title": f"Coat {pid} in Sand", "Vendor": "MAISON MARGIELA", "Type": "WOMEN CLOTHING COATS TRENCH",
                  "Tags": "", "Status": "Active", catm.MCODE: code})
        extra.append(r)
    with open(tmp_path / "catalog.csv", "a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, fieldnames=CAT_COLS).writerows(extra)
    cat = catm.load(str(tmp_path / "catalog.csv"))
    cart = cartm.load(write_cart(tmp_path / "c.xlsx"))
    _, review, _, _ = _draft(cat, cart)
    a, b = _review(review, JACKET_A, "Option1 Value"), _review(review, JACKET_B, "Option1 Value")
    assert (a["Value"], a["Colour"]) == ("BEIGE", "green") and "agrees" in a["Why"]     # cart Beige = catalog BEIGE
    assert (b["Value"], b["Colour"]) == ("WHITE", "amber") and "961 is BEIGE" in b["Why"]  # cart wins, says so


def test_write_frame_matches_to_excel_cell_for_cell(tmp_path):
    import pandas as pd
    frame = pd.DataFrame({
        "ID": ["8123456789012", "8123456789013", "8123456789014", "8123456789015"],
        "Blank": ["", "", None, float("nan")],
        "Mixed": ["", "x", None, "- Lining: 100% Viscose"],
        "Price": [1.5, None, 3.0, float("nan")],
        "Qty": [1, 2, 3, 4],
        "Int64": pd.array([1, None, 3, pd.NA], dtype="Int64"),
        "Flag": [True, False, True, False],
        "Code": ["S52AH0089M36273 115", "+61 3", "@home", "OS"],
    })
    ours, theirs = tmp_path / "ours.xlsx", tmp_path / "theirs.xlsx"
    poxlsx.write_frame(str(ours), frame, "Products")
    frame.to_excel(theirs, index=False, sheet_name="Products")

    def cells(path):
        ws = openpyxl.load_workbook(path)["Products"]
        return {(c.row, c.column): (c.value, c.data_type) for row in ws.iter_rows() for c in row
                if c.value is not None or c.data_type != "n"}
    assert cells(ours) == cells(theirs)
