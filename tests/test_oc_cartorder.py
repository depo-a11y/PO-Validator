"""A selling-session cart drafted as the order before its OC exists (oc/cartorder.py). Synthetic data only:
every code, name, price and quantity here is invented."""
import csv
import datetime

import openpyxl
import pytest
import yaml

import test_oc_cart as cartt
import validator_core as core
from oc import cart as cartm, cartorder, catalog as catm, cli, complete, finalize, history as histm, housestyle
from oc import poxlsx, service
from oc.draft import build
from oc.resolve import CART_OC, COLOUR

BRAND = dict(cartt.BRAND, oc_label="ZZ", heel_codes=True,
             product_class_types={"SPORTSJACKET": "CLOTHING JACKETS LIGHTWEIGHT", "BALLET SHOE": "SHOES BALLERINAS",
                                  "SHOULDER BAG": "BAGS SHOULDER BAGS", "SNEAKERS": "SHOES SNEAKERS"},
             colour_families=dict(cartt.BRAND["colour_families"], NAVY=["navy"], GREEN=["green", "olive"],
                                  GREY=["grey", "anthracite"]))

JACKET, SHOE, BAG, CARRY, EMPTY, NEWCODE = ("S98AA0101M00101 Q101", "S98WZ0102P0102 Q202", "SB8ZZ0103P0103 Q303",
                                            "S99WS0001P0001 Q404", "S98AA0104M00104 Q404", "S98AA0105M00105 Q505")
SIZES = ["38", "40", "42", "40", "41", "415", "42", "UNI"]     # an RTW grid, a shoe grid with a half size, UNI
HEADER = (["Style Image", "Style Name", "Model", "Fabric", "Color Code", "Color Description", "Gender", "Collection",
           "Category", "Subcategory", "Materials", "Made In", "Size Grid", "Available Sizes"] + SIZES
          + ["Units", "Currency", "Sugg. Retail", "Total Sugg. Retail", "Wholesale", "Total Wholesale"])
G0 = HEADER.index("Available Sizes") + 1                          # first size column (0-based)
RTW40, SHOE40, SHOE41, SHOE415, UNI = G0 + 1, G0 + 3, G0 + 4, G0 + 5, G0 + 7
FORMULA = object()


def crow(name, code, desc, gender, coll, sub, grid, avail, qty, wholesale, made="", units=FORMULA, total=FORMULA,
         currency="EUR       "):
    body, colour = code.split(" ")
    return dict(name=name, model=body[:9], fabric=body[9:], colour=colour, desc=desc, gender=gender, coll=coll,
                sub=sub, grid=grid, avail=avail, qty=qty, wholesale=wholesale, made=made, units=units, total=total,
                currency=currency)


ROWS = [
    crow("LINING TEST JACKET", JACKET, "Navy", "Female", "S0Z1 - ZZ WOMEN'S RTW TEST", "Sportsjackets", "38,40,42",
         "38,40,42", {RTW40: 2}, 300),
    crow("TABI TEST H30", SHOE, "Black Oyster", "Female", "S0Z2 - ZZ WOMEN'S SHOES TEST", "Ballet Shoes",
         "40,41,415,42", "40,41,415,42", {SHOE40: 1, SHOE415: 1}, 200, made="Made In Italy"),
    crow("BOX TEST BAG", BAG, "Olive Green", "Unisex", "S0Z3 - ZZ BAGS TEST", "Shoulder Bag", "UNI", "UNI",
         {UNI: 1}, 500, made="Made In Italy"),
    crow("REPLICA", CARRY, "White", "Male", "S0Z4 - ZZ MEN'S SHOES TEST", "Sneakers", "40,41,415,42", "",
         {SHOE41: 2}, 210),
    crow("NOT ORDERED", EMPTY, "Navy", "Female", "S0Z1 - ZZ WOMEN'S RTW TEST", "Sportsjackets", "38,40,42", "", {}, 300),
]


def write(path, rows=ROWS, header=HEADER, details=True):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DOOR 001-TEST"
    for r in (["Brand", "TEST"], ["Season", "SS 2027"], ["Session Code", "TESTCART"], []):
        ws.append(r)
    ws.append(header)
    col = {h: i for i, h in enumerate(header)}
    for x in rows:
        n = ws.max_row + 1
        vals = [None] * len(header)
        for h, k in (("Style Name", "name"), ("Model", "model"), ("Fabric", "fabric"), ("Color Code", "colour"),
                     ("Color Description", "desc"), ("Gender", "gender"), ("Collection", "coll"),
                     ("Subcategory", "sub"), ("Materials", "made"), ("Made In", "made"), ("Size Grid", "grid"),
                     ("Available Sizes", "avail"), ("Currency", "currency"), ("Wholesale", "wholesale")):
            if h in col:
                vals[col[h]] = x[k]
        if "Materials" in col:
            vals[col["Materials"]] = "Body: 100% Test Fibre"
        for j, q in x["qty"].items():
            vals[j] = q
        if "Units" in col:
            vals[col["Units"]] = f"=SUM(O{n}:V{n})" if x["units"] is FORMULA else x["units"]
        if "Total Wholesale" in col:
            vals[col["Total Wholesale"]] = f"=PRODUCT(W{n},AA{n})" if x["total"] is FORMULA else x["total"]
        ws.append(vals)
    if details:
        d = wb.create_sheet("DETAILS_TESTCART")
        d.append(["Salessession ID", "TESTCART"])
        d.append(["Docusign Click date", datetime.datetime(2026, 1, 2, 3, 4)])
    wb.save(path)
    return str(path)


def catalog_csv(tmp_path, extra=()):
    """One live carryover product (CARRY), plus `extra` products: (id, handle, title, code, type, status[, sizes])."""
    rows = []
    products = [("1", "replica-test-in-white", "Replica Test in White", CARRY, "MEN SHOES SNEAKERS", "Active")]
    for n, (pid, handle, title, code, ptype, status, *sizes) in enumerate(products + list(extra)):
        for i, size in enumerate(sizes[0] if sizes else ["40", "41", "42"]):
            r = {c: "" for c in cartt.CAT_COLS}
            r.update({"ID": pid, "Variant ID": f"{pid}{size}", "Option1 Name": "Colour", "Option1 Value": "WHITE",
                      "Option2 Name": "Size", "Option2 Value": size, "Variant SKU": f"ZXFW26{n}{10 + i}",
                      "Variant Price": "800.00", "Variant Compare At Price": "800.00", "Variant Cost": "310.00",
                      "Top Row": "true" if i == 0 else ""})
            if i == 0:
                r.update({"Handle": handle, "Title": title, "Vendor": BRAND["vendor"], "Type": ptype,
                          "Tags": ptype.split(" ")[0], "Status": status, catm.MCODE: code,
                          "Created At": "2025-01-01 10:00:00 +1100"})
            rows.append(r)
    p = tmp_path / "catalog.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cartt.CAT_COLS)
        w.writeheader()
        w.writerows(rows)
    return str(p)


def _check(checks, name):
    return [c for c in checks if c["Check"] == name]


def _draft(tmp_path, brand=BRAND, rows=ROWS, extra=(), hist=None):
    docs, cart = cartorder.read(write(tmp_path / "cart.xlsx", rows))
    cat = catm.load(catalog_csv(tmp_path, extra))
    return build(docs, brand, cat, hist or histm.History([]), core.load_tag_lookup(), cart=cart)


def _po(tmp_path, name="cart-po.xlsx"):
    po, review, checks = service.draft(None, catalog_csv(tmp_path), BRAND, cart_path=write(tmp_path / "cart.xlsx"))
    assert po is not None, service.blocking(checks)
    p = tmp_path / name
    p.write_bytes(po)
    return str(p), review, checks


def _line(code, sizes, price, no):
    body, colour = code.split(" ")
    q = sum(sizes.values())
    return dict(line_no=no, style=body[:9], fabric=body[9:], colour=colour, product_class="", sizes=sizes, qty=q,
                price=price, line_value=q * price)


def _oc():
    lines = [_line(JACKET, {"40": 1, "42": 1}, 300.0, 1), _line(SHOE, {"40": 1, "41.5": 1}, 210.0, 2),
             _line(CARRY, {"41": 2}, 210.0, 3), _line(NEWCODE, {"40": 1}, 300.0, 4)]
    return [dict(doc="SS - 1", collection="ZZ TEST", gender=None, lines=lines,
                 total_qty=sum(l["qty"] for l in lines), net=sum(l["line_value"] for l in lines))]


# ---- reading the cart as the order -------------------------------------------------------------------

def test_the_cart_reads_as_one_unconfirmed_document(tmp_path):
    (d,), cart = cartorder.read(write(tmp_path / "c.xlsx"))
    by = {f"{l['style']}{l['fabric']} {l['colour']}": l for l in d["lines"]}
    assert set(by) == {JACKET, SHOE, BAG, CARRY} and d["empty_rows"] == [EMPTY]
    assert by[JACKET]["sizes"] == {"40": 2}                # '40' read under the RTW column, not the shoe one
    assert by[SHOE]["sizes"] == {"40": 1, "41.5": 1}       # '415' is a half size: 41 is also a header
    assert by[BAG]["sizes"] == {"OS": 1} and by[CARRY]["sizes"] == {"41": 2}
    assert by[SHOE]["price"] == 200.0 and by[SHOE]["line_value"] == 400.0 and by[SHOE]["reader"] == "cart"
    assert by[JACKET]["gender"] == "WOMEN" and by[BAG]["gender"] is None and by[JACKET]["line_no"] == 6
    assert (d["doc"], d["label"], d["date"], d["currency"]) == ("cart TESTCART", "ZZ", "2026-01-02", "EUR")
    assert (d["total_qty"], d["net"]) == (7, 600.0 + 400.0 + 500.0 + 420.0)
    assert d["recomputed"] == ["totals", "line units", "line values"]
    assert cart["rows"][SHOE]["available_sizes"] == ["40", "41", "41.5", "42"]


def test_printed_units_and_line_values_are_gated_and_not_recomputed(tmp_path):
    ok = [dict(r, units=sum(r["qty"].values()), total=sum(r["qty"].values()) * r["wholesale"]) for r in ROWS]
    (d,), _ = cartorder.read(write(tmp_path / "ok.xlsx", ok))
    assert d["recomputed"] == ["totals"]
    bad = [dict(ok[0], units=3)] + ok[1:]
    with pytest.raises(cartorder.CartOrderError, match=r"sizes sum 2 != Tot\. Qty 3"):
        cartorder.read(write(tmp_path / "units.xlsx", bad))
    bad = [dict(ok[0], total=599)] + ok[1:]
    with pytest.raises(cartorder.CartOrderError, match="!= line value 599"):
        cartorder.read(write(tmp_path / "value.xlsx", bad))


@pytest.mark.parametrize("change, problem", [
    (dict(qty={SHOE40: 1, RTW40 - 1: 1}), "under size 38, which is not in the row's Size Grid"),
    (dict(qty={SHOE40: 1, RTW40: 1}), "size 40 has quantities in two columns"),
    (dict(qty={SHOE40: 1.5}), "is not a whole number"),
    (dict(qty={SHOE40: "=1+1"}), "a formula with no saved value"),
    (dict(wholesale=None), "units but no unit price"),
])
def test_a_cart_row_the_order_cannot_rest_on_is_a_problem(tmp_path, change, problem):
    rows = [ROWS[0], dict(ROWS[1], **change)] + ROWS[2:]
    with pytest.raises(cartorder.CartOrderError) as e:
        cartorder.read(write(tmp_path / "c.xlsx", rows))
    assert any(problem in p for p in e.value.problems), e.value.problems


def test_a_code_on_two_cart_rows_and_a_cart_without_units_are_refused(tmp_path):
    with pytest.raises(cartorder.CartOrderError, match="on more than one cart row"):
        cartorder.read(write(tmp_path / "dup.xlsx", ROWS + [ROWS[0]]))
    with pytest.raises(cartorder.CartOrderError, match="no Units"):
        cartorder.read(write(tmp_path / "nounits.xlsx", header=[h if h != "Units" else "Pieces" for h in HEADER]))


def test_half_sizes_need_their_whole_size_in_the_same_grid():
    assert cartm.half_size("415", {"41", "42"}) == "41.5"
    assert cartm.half_size("415", {"40"}) == "415"
    assert cartm.half_size("UNI", set()) == "OS"


def test_read_order_says_unconfirmed_and_flags_the_multiplier(tmp_path):
    docs, checks, cart = cartorder.read_order(write(tmp_path / "c.xlsx"), BRAND)
    assert docs and cart
    (rd,) = _check(checks, service.READER_CHECK)
    assert rd["Result"] == "WARN"
    assert rd["Detail"].startswith("UNCONFIRMED: from cart TESTCART, signed 2026-01-02, not an order confirmation.")
    assert _check(checks, cartorder.READ_CHECK)[0]["Result"] == "PASS"
    assert _check(checks, service.TOTALS_CHECK)[0]["Result"] == "WARN"
    (cost,) = _check(checks, cartorder.COST_CHECK)
    assert cost["Result"] == "WARN" and "EUR x 1.5" in cost["Detail"]


def test_read_order_refuses_another_currency_or_label(tmp_path):
    usd = [dict(r, currency="USD") for r in ROWS]
    docs, checks, _ = cartorder.read_order(write(tmp_path / "usd.xlsx", usd), BRAND)
    assert docs is None and _check(checks, service.CURRENCY_CHECK)[0]["Result"] == "FAIL"
    docs, checks, _ = cartorder.read_order(write(tmp_path / "c.xlsx"), dict(BRAND, oc_label="YY"))
    assert docs is None and "label 'ZZ'" in _check(checks, service.IDENTITY_CHECK)[0]["Detail"]


# ---- the draft ---------------------------------------------------------------------------------------

QTY, WHS, TOT = (cartorder.cart_column(c, "cart TESTCART") for c in cartorder.CART_COLS)


def _edit(path, fn):
    """Apply fn(ws, header, row) to every data row of a PO's first sheet."""
    wb = openpyxl.load_workbook(path)
    ws = wb.worksheets[0]
    hdr = [c.value for c in ws[1]]
    for r in range(2, ws.max_row + 1):
        fn(ws, hdr, r)
    wb.save(path)


def _price_new_rows(ws, hdr, r):
    if ws.cell(r, hdr.index("Command") + 1).value == "NEW":
        cost = ws.cell(r, hdr.index("Variant Cost") + 1).value
        ws.cell(r, hdr.index("Variant Price") + 1).value = -(-cost * 2.5 // 50) * 50


def _priced_cart_po(tmp_path):
    po, _, _ = _po(tmp_path)
    _edit(po, _price_new_rows)
    return po


def test_every_quantity_and_price_cell_is_amber_from_cart_not_confirmed(tmp_path):
    po, review, checks = _po(tmp_path)
    review = review.to_dict("records")
    assert COLOUR[CART_OC] == "amber"
    assert not [x for x in review if x["Source"] == "OC"]
    for col in (QTY, WHS, TOT, poxlsx.COST_PRICE, "Variant Cost", poxlsx.CODE):
        cells = [x for x in review if x["Column"] == col and x["Code"] == SHOE
                 and (col != QTY or x["Size"] in ("40", "41.5"))]   # a run size not ordered is 0 by rule
        assert cells and all(x["Source"] == CART_OC and x["Colour"] == "amber" for x in cells), col
        assert all(x["Why"].startswith("from cart, not confirmed (cart TESTCART row 7)") for x in cells), col
    cost = next(x for x in review if x["Column"] == "Variant Cost" and x["Code"] == SHOE)
    assert "EUR 200 x 1.5" in cost["Why"]
    header, rows = poxlsx.read_po(po)
    assert QTY in header and WHS in header and TOT in header and "Order Qty" not in header
    assert cartorder.po_source(header) == "cart TESTCART" and cartorder.unconfirmed_session(po) == "cart TESTCART"
    assert sum(cartorder.standard(r)["Order Qty"] or 0 for r in rows) == 7
    assert not _check(checks, "Cart matches this OC")
    names = {c["Check"] for c in checks}
    assert {"Cart totals (the cart prints none)", "PO Order Qty and Total Order equal the cart",
            "Qty per code+size equals the cart"} <= names
    assert not names & {"OC totals reproduced (parsed = printed)", "PO Order Qty and Total Order equal the OC",
                        "Qty per code+size equals the OC"}


def test_new_styles_take_title_type_colour_and_run_from_the_cart(tmp_path):
    rows, review, checks, _ = _draft(tmp_path)
    first = {}
    for r in rows:
        first.setdefault(r[poxlsx.CODE], r)
    why = {(x["Code"], x["Column"]): x for x in review}
    j, s, b, c = first[JACKET], first[SHOE], first[BAG], first[CARRY]
    assert (j["Command"], j["Title"], j["Type"], j["Option1 Value"]) == \
        ("NEW", "Lining Test Jacket in Navy", "WOMEN CLOTHING JACKETS LIGHTWEIGHT", "NAVY")
    assert why[(JACKET, "Title")]["Source"] == "cart"
    assert "read as product class 'SPORTSJACKET'" in why[(JACKET, "Type")]["Why"]
    assert [r["Option2 Value"] for r in rows if r[poxlsx.CODE] == JACKET] == ["38", "40", "42"]
    assert any("Available Sizes" in x["Why"] for x in review if x["Code"] == JACKET and x["Column"] == "Option2 Value")
    assert (s["Title"], s["Type"], s["Option1 Value"]) == ("Tabi Test H30 in Black Oyster", "WOMEN SHOES BALLERINAS",
                                                          "BLACK")
    assert [r["Option2 Value"] for r in rows if r[poxlsx.CODE] == SHOE] == ["40", "41", "41.5", "42"]
    assert b["Type"] == "WOMEN BAGS SHOULDER BAGS" and c["Command"] == "MERGE" and c["ID"] == "1"
    assert all(r["Variant Price"] is None for r in rows if r["Command"] == "NEW")
    assert _check(checks, "Every NEW Type is in expected_tags.xlsx")[0]["Result"] == "PASS"


def test_brand_subcategory_types_win_over_reading_the_subcategory_as_a_class(tmp_path):
    brand = dict(BRAND, subcategory_types={"SPORTSJACKETS": "CLOTHING JACKETS BLAZER"})
    rows, _, _, _ = _draft(tmp_path, brand)
    assert next(r for r in rows if r[poxlsx.CODE] == JACKET)["Type"] == "WOMEN CLOTHING JACKETS BLAZER"


def test_a_style_family_type_the_cart_contradicts_shows_both(tmp_path):
    hist = histm.History([("F/W 26", [dict(code="S98AA0999M00999 Q1", type="WOMEN CLOTHING JACKETS DENIM",
                                           season="F/W 26", size="40", title="Old Jacket in Blue", eur=1.0,
                                           price=1.0)])])
    rows, review, _, _ = _draft(tmp_path, hist=hist)
    assert next(r for r in rows if r[poxlsx.CODE] == JACKET)["Type"] == "WOMEN CLOTHING JACKETS DENIM"
    x = next(x for x in review if x["Code"] == JACKET and x["Column"] == "Type")
    assert x["Colour"] == "amber" and x["Why"].startswith("style family S98AA")
    assert "the cart's Subcategory 'Sportsjackets' says WOMEN CLOTHING JACKETS LIGHTWEIGHT: check which" in x["Why"]


def test_a_new_type_left_blank_is_named_not_counted(tmp_path):
    rows = [dict(ROWS[0], sub="Mystery")] + ROWS[1:]
    _, _, checks, _ = _draft(tmp_path, rows=rows)
    (t,) = _check(checks, "Every NEW Type is in expected_tags.xlsx")
    assert t["Result"] == "FAIL" and t["Detail"] == "(blank)"


def test_a_new_title_an_active_live_product_already_has_is_amber_and_warned(tmp_path):
    extra = [("7", "old-lining-jacket", "Lining Test Jacket in Navy", "S97AA0007M00007 Q7",
              "WOMEN CLOTHING JACKETS LIGHTWEIGHT", "Active"),
             ("8", "draft-bag", "Box Test Bag in Olive Green", "S97AA0008M00008 Q8", "WOMEN BAGS SHOULDER BAGS",
              "Draft")]
    rows, review, checks, _ = _draft(tmp_path, extra=extra)
    x = next(x for x in review if x["Code"] == JACKET and x["Column"] == "Title")
    assert x["Colour"] == "amber" and "/old-lining-jacket" in x["Why"]
    (w,) = _check(checks, housestyle.LIVE_TITLE_CHECK)
    assert w["Result"] == "WARN" and "/old-lining-jacket" in w["Detail"] and "draft-bag" not in w["Detail"]


# ---- house style ----------------------------------------------------------------------------------------

def test_heel_heights_become_house_heel_codes():
    assert housestyle.heel_code("Tabi Ballerina 3cm")[0] == "Tabi Ballerina H30"
    assert housestyle.heel_code("Tabi Ankle Boots 3cm Heel")[0] == "Tabi Ankle Boots H30"
    assert housestyle.heel_code("Tabi 8 cm Boot")[0] == "Tabi Boot H80"
    assert housestyle.heel_code("Pump 10cm") == ("Pump 10cm", None)
    assert housestyle.heel_code("Tabi Ballerina H30") == ("Tabi Ballerina H30", None)


def test_style_names_in_house_style():
    assert cartm.style_title("INSIDE OUT TSHIRT") == "Inside Out T-Shirt"
    assert cartm.style_title("LACE-UP  DERBY H30") == "Lace-Up Derby H30"


def test_a_live_stem_with_a_heel_in_cm_is_written_as_a_heel_code_on_new_titles_only(tmp_path):
    extra = [("2", "tabi-test-3cm-in-red", "Tabi Test 3cm in Red", "S98WZ0102P0102 Q999", "WOMEN SHOES BALLERINAS",
              "Active")]
    docs, cart = cartorder.read(write(tmp_path / "cart.xlsx"))
    cat = catm.load(catalog_csv(tmp_path, extra))
    for brand, want in ((BRAND, "Tabi Test H30 in Black Oyster"),
                        (dict(BRAND, heel_codes=False), "Tabi Test 3cm in Black Oyster")):
        out, review, _, _ = build(docs, brand, cat, histm.History([]), core.load_tag_lookup(), cart=cart)
        assert next(r for r in out if r[poxlsx.CODE] == SHOE)["Title"] == want
        x = next(x for x in review if x["Code"] == SHOE and x["Column"] == "Title")
        if brand["heel_codes"]:
            assert x["Source"] == "rule" and "written H30" in x["Why"]
        else:
            assert "the cart's Style Name is 'TABI TEST H30'" in x["Why"]


def test_only_the_brands_colour_spelling_map_changes_cart_colour_words(tmp_path):
    brand = dict(BRAND, colour_spelling={"Antracite": "Anthracite"})
    assert housestyle.colour_words(brand, "Antracite", "cart", "w") == \
        ("Anthracite", "rule", "w; 'Antracite' written 'Anthracite' (brand colour_spelling)")
    assert housestyle.colour_words(brand, "Militare", "cart", "w") == ("Militare", "cart", "w")
    assert housestyle.colour_words(BRAND, "Antracite", "cart", "w") == ("Antracite", "cart", "w")
    assert housestyle.colour_words(brand, "Antracite", "catalog", "w") == ("Antracite", "catalog", "w")
    rows = [dict(ROWS[0], desc="Antracite")] + ROWS[1:]
    out, review, _, _ = _draft(tmp_path, brand, rows=rows)
    r = next(r for r in out if r[poxlsx.CODE] == JACKET)
    assert r["Title"] == "Lining Test Jacket in Anthracite" and r["Option1 Value"] == "GREY"
    x = next(x for x in review if x["Code"] == JACKET and x["Column"] == "Title")
    assert x["Colour"] == "amber" and "colour words from the cart's 'Antracite'" in x["Why"]


# ---- finalize: a PO drafted from a cart makes PU-new only ----------------------------------------------------

def test_a_priced_cart_po_writes_pu_new_only(tmp_path):
    po = _priced_cart_po(tmp_path)
    checks = finalize.run(po, BRAND, catm.load(catalog_csv(tmp_path)), str(tmp_path / "run-"))
    assert not service.fails(checks), service.fails(checks)
    (w,) = _check(checks, cartorder.REFUSED_CHECK)
    assert w["Result"] == "WARN" and w["Detail"].startswith("UNCONFIRMED: this PO was drafted from cart TESTCART")
    for words in ("Refused: PU-merge, PU-merge-new-sizes, PU-merge-ROLLBACK and Delivery",
                  "PU-new creates 3 product(s) at EUR x 1.5", "may never ship", "is Colwyn's decision"):
        assert words in w["Detail"], words
    assert "can go ahead" not in w["Detail"]
    written = sorted(x.name for x in tmp_path.glob("run-*.xlsx"))
    assert written == ["run-PU-new.xlsx", "run-finalize-checks.xlsx"]


def _rename(path, names):
    """Rename header cells of a PO's first sheet: {old: new}."""
    def fn(ws, hdr, r):
        if r == 2:
            for old, new in names.items():
                ws.cell(1, hdr.index(old) + 1).value = new
    _edit(path, fn)


def _drop(path, *sheets):
    wb = openpyxl.load_workbook(path)
    for name in sheets:
        del wb[name]
    wb.save(path)


def _finalize(tmp_path, po, prefix):
    checks = finalize.run(po, BRAND, catm.load(catalog_csv(tmp_path)), str(tmp_path / prefix))
    return checks, sorted(x.name for x in tmp_path.glob(f"{prefix}*.xlsx"))


PLAIN = {QTY: "Order Qty", WHS: "Wholesale Price", TOT: "Total Order"}


def test_the_cart_header_alone_survives_deleted_checks_and_review(tmp_path):
    po = _priced_cart_po(tmp_path)
    _drop(po, "Checks", "Review")
    checks, written = _finalize(tmp_path, po, "a-")
    (w,) = _check(checks, cartorder.REFUSED_CHECK)
    assert w["Result"] == "WARN" and "(its header say so)" in w["Detail"]
    assert written == ["a-PU-new.xlsx", "a-finalize-checks.xlsx"]


@pytest.mark.parametrize("plain, drop, where", [(False, (), "header, Review, Checks"), (True, (), "Review, Checks"),
                                                (True, ("Review",), "Checks"), (True, ("Checks",), "Review")])
def test_a_cart_po_with_plain_headers_is_refused_by_its_checks_or_review(tmp_path, plain, drop, where):
    """A PO drafted before the header marker, or with its columns renamed back, still says cart in its other sheets."""
    po = _priced_cart_po(tmp_path)
    if plain:
        _rename(po, PLAIN)
    _drop(po, *drop)
    checks, written = _finalize(tmp_path, po, "r-")
    (w,) = _check(checks, cartorder.REFUSED_CHECK)
    assert w["Result"] == "WARN" and w["Detail"].startswith("UNCONFIRMED: this PO was drafted from cart TESTCART")
    assert f"(its {where} say so)" in w["Detail"], w["Detail"]
    assert written == ["r-PU-new.xlsx", "r-finalize-checks.xlsx"], written
    assert cartorder.unconfirmed_session(po) == "cart TESTCART"


def test_a_po_with_no_cart_sign_left_is_read_as_an_oc_po(tmp_path):
    """The agreed limit: plain headers and no Review or Checks sheet carry no sign of the cart."""
    po = _priced_cart_po(tmp_path)
    _rename(po, PLAIN)
    _drop(po, "Checks", "Review")
    assert cartorder.cart_signals(po) == [] and cartorder.unconfirmed_session(po) is None
    checks, _ = _finalize(tmp_path, po, "n-")
    assert not _check(checks, cartorder.REFUSED_CHECK)


def test_a_po_missing_a_confirmed_column_with_no_cart_sign_fails_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "read_oc", lambda *a, **k: (_oc(), [], None))
    po, _, _ = service.draft(str(tmp_path / "oc.pdf"), catalog_csv(tmp_path), BRAND)
    p = tmp_path / "oc-po.xlsx"
    p.write_bytes(po)
    _edit(str(p), _price_new_rows)
    _rename(str(p), {"Order Qty": "Qty"})
    checks, written = _finalize(tmp_path, str(p), "f-")
    (w,) = _check(checks, cartorder.REFUSED_CHECK)
    assert w["Result"] == "FAIL" and w["Detail"].startswith("this PO has no 'Order Qty' column")
    assert written == ["f-finalize-checks.xlsx"]
    _drop(str(p), "Checks", "Review")
    _rename(str(p), {"Qty": "Order Qty", "Wholesale Price": "Unit Cost"})
    checks, written = _finalize(tmp_path, str(p), "g-")
    assert _check(checks, cartorder.REFUSED_CHECK)[0]["Result"] == "FAIL" and written == ["g-finalize-checks.xlsx"]


def test_reconcile_reads_a_cart_pos_renamed_quantities(tmp_path, monkeypatch):
    from oc import reconcile
    cart_po, _, _ = _po(tmp_path)
    monkeypatch.setattr(service, "read_oc", lambda *a, **k: (_oc(), [], None))
    po, _, _ = service.draft(str(tmp_path / "oc.pdf"), catalog_csv(tmp_path), BRAND)
    oc_po = tmp_path / "oc-po.xlsx"
    oc_po.write_bytes(po)
    reconcile.run(str(oc_po), cart_po, _oc(), str(tmp_path / "acc.md"), brand=BRAND)
    hand = next(l for l in (tmp_path / "acc.md").read_text().splitlines() if l.startswith("| Hand PO vs OC, qty"))
    assert "; 7 units, EUR 1,920.00 |" in hand, hand


def test_a_cart_po_is_refused_as_a_past_season_po(tmp_path):
    cart_po, _, _ = _po(tmp_path)
    with pytest.raises(ValueError, match="drafted from cart TESTCART before its order confirmation"):
        histm.load([dict(label="S/S 27", path=cart_po)])
    po, _, checks = service.draft(None, catalog_csv(tmp_path), BRAND, cart_path=write(tmp_path / "c2.xlsx"),
                                  history_paths=[cart_po])
    assert po is None and "drafted from cart TESTCART" in checks[0]["Detail"] and checks[0]["Result"] == "FAIL"


def test_a_po_drafted_from_the_oc_is_not_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "read_oc", lambda *a, **k: (_oc(), [], None))
    po, _, _ = service.draft(str(tmp_path / "oc.pdf"), catalog_csv(tmp_path), BRAND)
    p = tmp_path / "oc-po.xlsx"
    p.write_bytes(po)
    assert cartorder.unconfirmed_session(str(p)) is None
    _edit(str(p), _price_new_rows)
    checks = finalize.run(str(p), BRAND, catm.load(catalog_csv(tmp_path)), str(tmp_path / "oc-"))
    assert not _check(checks, cartorder.REFUSED_CHECK)
    assert any("PU-merge" in c["Check"] for c in checks)


def test_service_names_a_cart_draft_and_its_run_and_zips_pu_new_only(tmp_path):
    when = datetime.datetime(2026, 1, 2, 3, 4)
    po, _, checks = service.draft(None, catalog_csv(tmp_path), BRAND, cart_path=write(tmp_path / "c.xlsx"))
    assert service.draft_file(BRAND, checks, when) == "2026-01-02-0304-maison-margiela-ss27-from-cart-testcart-PO-draft.xlsx"
    assert service.draft_file(BRAND, [], when) == "2026-01-02-0304-maison-margiela-ss27-PO-draft.xlsx"
    priced = _priced_cart_po(tmp_path)
    assert service.run_prefix(priced, BRAND, when) == "2026-01-02-0304-maison-margiela-ss27-from-cart-testcart-"
    z, checks, _ = service.finalize(priced, catalog_csv(tmp_path), BRAND, when=when)
    import io
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(z)).namelist()
    assert names == ["2026-01-02-0304-maison-margiela-ss27-from-cart-testcart-PU-new.xlsx",
                     "2026-01-02-0304-maison-margiela-ss27-from-cart-testcart-finalize-checks.xlsx"]


# ---- the OC arrives ---------------------------------------------------------------------------------------

def test_prior_changes_list_what_the_oc_changed(tmp_path):
    prior_path, _, _ = _po(tmp_path)

    def price(ws, hdr, r):
        if ws.cell(r, hdr.index(poxlsx.CODE) + 1).value == JACKET:
            ws.cell(r, hdr.index("Variant Price") + 1).value = 1200
    _edit(prior_path, price)
    prior = cartorder.read_prior(prior_path)
    assert prior["typed"][JACKET][0] == 1200.0 and prior["typed"][JACKET][1].startswith("cart-po.xlsx row ")
    (sheet, check) = cartorder.prior_sheet(prior, _oc(), BRAND)
    got = {(x["Code"], x["Size"], x["Change"]): (x["prior PO (cart-po.xlsx)"], x["Order confirmation"], x["Note"])
           for x in sheet["Changes"]}
    assert got == {(NEWCODE, "", "added by the OC"): ("", 1, "not in the prior PO"),
                   (BAG, "", "dropped by the OC"): (1, 0, "in the prior PO, not on the OC"),
                   (JACKET, "40", "quantity"): (2, 1, ""), (JACKET, "42", "quantity"): (0, 1, ""),
                   (SHOE, "", "unit price (EUR)"): (200.0, 210.0, "cost = the OC's price x the brand multiplier"),
                   (JACKET, "", "retail price typed in the prior PO"):
                       (1200.0, "", "carried into the draft from the OC where its retail price is blank")}
    assert check["Result"] == "WARN" and check["Check"] == cartorder.CHANGES_CHECK
    assert "1 added by the OC, 1 dropped by the OC, 2 quantity, 1 unit price (EUR)" in check["Detail"]
    same, check = cartorder.changes([], [], "EUR")
    assert same == [] and check["Result"] == "INFO"
    assert cartorder.read_prior(str(tmp_path / "nope.xlsx"))["fail"]["Result"] == "FAIL"


def test_a_size_the_draft_named_the_stores_way_is_no_change_when_the_oc_confirms_it(tmp_path):
    """Review 2026-11-13-buyer-12 F1: the jacket is live in IT 38/IT 40/IT 42, the cart orders 2 x 40 (drafted as
    IT 40) and the OC confirms 2 x 40. The Changes sheet, complete and reconcile see one size, not a move."""
    from oc import reconcile
    extra = [("2", "lining-test-jacket-in-navy", "Lining Test Jacket in Navy", JACKET, "WOMEN CLOTHING JACKETS",
              "Active", ["IT 38", "IT 40", "IT 42"])]
    cat = catalog_csv(tmp_path, extra)
    po, _, checks = service.draft(None, cat, BRAND, cart_path=write(tmp_path / "cart.xlsx"))
    prior = tmp_path / "cart-po.xlsx"
    prior.write_bytes(po)
    rows = [cartorder.standard(r) for r in poxlsx.read_po(str(prior))[1]]
    assert {(r["Option2 Value"], r["Order Qty"]) for r in rows if r[poxlsx.CODE] == JACKET} >= {("IT 40", 2)}
    lines = [_line(JACKET, {"40": 2}, 300.0, 1), _line(SHOE, {"40": 1, "41.5": 1}, 200.0, 2),
             _line(BAG, {"UNI": 1}, 500.0, 3), _line(CARRY, {"41": 2}, 210.0, 4)]
    oc = [dict(doc="SS - 1", collection="ZZ TEST", gender=None, lines=lines,
               total_qty=sum(l["qty"] for l in lines), net=sum(l["line_value"] for l in lines))]
    sheet, check = cartorder.prior_sheet(cartorder.read_prior(str(prior)), oc, BRAND)
    assert sheet["Changes"] == [] and check["Result"] == "INFO" and "no quantity, price or code changed" in check["Detail"]
    out = complete.run(str(prior), oc, BRAND, catm.load(cat), histm.History([]), core.load_tag_lookup(),
                       str(tmp_path / "done.xlsx"))
    assert _check(out, "Qty per code+size equals the OC")[0]["Result"] == "PASS"
    assert reconcile.compare(rows, rows, oc, brand=BRAND)["hand_qty_mismatch"] == []
    oc_it = [dict(oc[0], lines=[_line(JACKET, {"IT 40": 2}, 300.0, 1)] + lines[1:])]   # an OC printing the IT name
    assert reconcile.compare(rows, rows, oc_it, brand=BRAND)["hand_qty_mismatch"] == []
    raw = poxlsx.read_po(str(prior))[1]
    qty = next(k for k in raw[0] if str(k).startswith("Order Qty"))   # the cart PO's renamed quantity column
    moved = [dict(r, **{qty: 0}) if r["Option2 Value"] == "IT 40" else r for r in raw]
    sheet, _ = cartorder.prior_sheet(dict(rows=moved, name="x.xlsx", typed={}), oc, BRAND)
    assert [(x["Code"], x["Size"], x["prior PO (x.xlsx)"], x["Order confirmation"]) for x in sheet["Changes"]] == [
        (JACKET, "IT 40", 0, 2)]   # a real change keeps the store's name for the size


def test_draft_from_the_oc_with_the_cart_po_as_prior_writes_changes_and_carries_typed_prices(tmp_path, monkeypatch):
    prior, _, _ = _po(tmp_path)

    def price(ws, hdr, r):
        if ws.cell(r, hdr.index(poxlsx.CODE) + 1).value == JACKET:
            ws.cell(r, hdr.index("Variant Price") + 1).value = 1200
    _edit(prior, price)
    monkeypatch.setattr(service, "read_oc", lambda *a, **k: (_oc(), [], None))
    po, review, checks = service.draft(str(tmp_path / "oc.pdf"), catalog_csv(tmp_path), BRAND, prior_path=prior)
    out = tmp_path / "oc-draft.xlsx"
    out.write_bytes(po)
    assert openpyxl.load_workbook(out).sheetnames == ["Sheet1", "Review", "Checks", "Changes"]
    assert _check(checks, cartorder.CHANGES_CHECK)[0]["Result"] == "WARN"
    header, rows = poxlsx.read_po(str(out))
    assert "Order Qty" in header and cartorder.po_source(header) is None
    assert {r["Variant Price"] for r in rows if r[poxlsx.CODE] == JACKET} == {1200}
    x = next(x for x in review.to_dict("records") if x["Code"] == JACKET and x["Column"] == "Variant Price")
    assert x["Source"] == "prior PO" and x["Colour"] == "amber" and "typed in the prior PO" in x["Why"]
    assert {r["Variant Price"] for r in rows if r[poxlsx.CODE] == NEWCODE} == {None}
    po, _, checks = service.draft(str(tmp_path / "oc.pdf"), catalog_csv(tmp_path), BRAND,
                                  prior_path=str(tmp_path / "nope.xlsx"))
    assert po is None and _check(checks, cartorder.CHANGES_CHECK)[0]["Result"] == "FAIL"


def test_complete_on_a_cart_po_replaces_its_sheets_and_lists_what_the_oc_changed(tmp_path):
    prior, _, _ = _po(tmp_path)
    cat = catm.load(catalog_csv(tmp_path))
    checks = complete.run(prior, _oc(), BRAND, cat, histm.History([]), core.load_tag_lookup(),
                          str(tmp_path / "done.xlsx"))
    assert openpyxl.load_workbook(tmp_path / "done.xlsx").sheetnames == ["Sheet1", "Review", "Checks", "Changes"]
    (c,) = _check(checks, cartorder.CHANGES_CHECK)
    assert c["Detail"].startswith("this PO 7 units, OC 7 units") and "with this PO as --prior" in c["Detail"]
    assert _check(checks, "Qty per code+size equals the OC")[0]["Result"] == "FAIL"
    assert cartorder.unconfirmed_session(str(tmp_path / "done.xlsx")) == "cart TESTCART"
    (u,) = _check(checks, "PO drafted from a cart")
    assert u["Result"] == "WARN" and u["Detail"].startswith("UNCONFIRMED: from cart TESTCART, not an order confirmation")
    done = str(tmp_path / "done.xlsx")
    _rename(done, PLAIN)
    assert [w for w, _ in cartorder.cart_signals(done)] == ["Checks"]   # complete's own Checks keep the cart's sign
    plain = tmp_path / "plain.xlsx"
    _, rows = poxlsx.read_po(prior)
    poxlsx.write_po(str(plain), poxlsx.PO_HEADER, [cartorder.standard(r) for r in rows])
    complete.run(str(plain), _oc(), BRAND, cat, histm.History([]), core.load_tag_lookup(), str(tmp_path / "p.xlsx"))
    assert "Changes" not in openpyxl.load_workbook(tmp_path / "p.xlsx").sheetnames


def test_complete_names_a_blank_new_type_instead_of_crashing(tmp_path):
    prior, _, _ = _po(tmp_path)

    def blank_type(ws, hdr, r):
        if ws.cell(r, hdr.index(poxlsx.CODE) + 1).value == JACKET:
            ws.cell(r, hdr.index("Type") + 1).value = None
    _edit(prior, blank_type)
    checks = complete.run(prior, _oc(), BRAND, catm.load(catalog_csv(tmp_path)), histm.History([]),
                          core.load_tag_lookup(), str(tmp_path / "done.xlsx"))
    assert _check(checks, "Every NEW Type is in expected_tags.xlsx")[0]["Detail"] == "(blank)"


def test_the_cli_drafts_from_a_cart_alone_and_prints_each_check_once(tmp_path, capsys):
    cfg = tmp_path / "zz.local.yaml"
    cfg.write_text(yaml.safe_dump(BRAND))
    out = tmp_path / "po.xlsx"
    rc = cli.main(["draft", "--cart", write(tmp_path / "c.xlsx"), "--catalog", catalog_csv(tmp_path), "--brand", "zz",
                   "--brand-config", str(cfg), "--out", str(out)])
    assert rc == 1 and out.exists()            # the blank retail prices FAIL, by design
    printed = capsys.readouterr().out
    assert printed.count("UNCONFIRMED: from cart TESTCART") == 1
    assert cartorder.unconfirmed_session(str(out)) == "cart TESTCART"
    with pytest.raises(SystemExit, match="give --oc, or --cart"):
        cli.main(["draft", "--catalog", catalog_csv(tmp_path), "--brand", "zz", "--brand-config", str(cfg),
                  "--out", str(out)])


def test_service_without_an_oc_or_a_cart_drafts_nothing(tmp_path):
    po, _, checks = service.draft(None, catalog_csv(tmp_path), BRAND)
    assert po is None and _check(checks, service.READER_CHECK)[0]["Result"] == "FAIL"
