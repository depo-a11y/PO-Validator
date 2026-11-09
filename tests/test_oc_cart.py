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
    vendor="MAISON MARGIELA", adapter="otb", season="S/S 27", sku_prefix="MMSS27", sku_digits=3,
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
        ws.append(r)
    ws.append([None, None, None, None, None, None, None, None, None, None, None, None, None, None, 5, 5])  # totals
    wb.save(path)
    return str(path)


CAT_COLS = list(catm.REQUIRED) + ["Body HTML", "Variant Country of Origin", catm.MADE_IN, "Created At"]


@pytest.fixture
def catalog(tmp_path):
    rows = []
    for i, size in enumerate(["40", "41", "42"]):
        r = {c: "" for c in CAT_COLS}
        r.update({"ID": "1", "Variant ID": f"1{size}", "Option1 Name": "Colour", "Option1 Value": "WHITE",
                  "Option2 Name": "Size", "Option2 Value": size, "Variant SKU": f"MMFW26{100 + i}",
                  "Variant Price": "800.00", "Variant Compare At Price": "800.00", "Variant Cost": "310.00",
                  "Top Row": "true" if i == 0 else ""})
        if i == 0:
            r.update({"Handle": "replica-sneaker-in-white", "Title": "Replica Sneaker in White",
                      "Vendor": "MAISON MARGIELA", "Type": "MEN SHOES SNEAKERS", "Tags": "MEN", "Status": "Active",
                      catm.MCODE: "S99WS0001P0001 101", "Body HTML": "<p>Composition: 100% Calf Leather</p>",
                      "Created At": "2025-01-01 10:00:00 +1100"})
        rows.append(r)
    p = tmp_path / "catalog.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CAT_COLS)
        w.writeheader()
        w.writerows(rows)
    return catm.load(str(p))


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
