"""The OC tool on a tiny synthetic catalog, history and PO (no real data)."""
import csv
import os

import openpyxl
import pandas as pd
import pytest

import validator_core as core
from oc import catalog as catm, history as histm, finalize, poxlsx
from oc.complete import run as complete_run
from oc.draft import build, oc_lines as draft_lines
from oc.resolve import Resolver
from oc.util import norm_size, join_key, sort_sizes

BRAND = dict(
    vendor="MAISON MARGIELA", adapter="otb", season="S/S 27", sku_prefix="MMSS27", sku_digits=3,
    cost_currency="EUR", cost_multiplier=1.5, margin_floor=2.25, price_round=50, status="Active",
    published_new=False, published_merge=True, new_sale="NEW", option1_name="Colour", option2_name="Size",
    title_tag="{vendor} - {title} |MARAIS",
    body_html="<p>Composition: {composition}</p> <p>Made in {made_in}</p> <p>SKU: {code}</p>",
    unknown="-", womens_prefix="Womens ", history=[], default_gender="MEN",
    product_class_types={"SNEAKERS": "SHOES SNEAKERS", "T-SHIRT": "CLOTHING T-SHIRTS SHORT SLEEVE"},
    colour_families={"BLACK": ["black"], "WHITE": ["white"]}, country_names={"IT": "Italy"},
)

CAT_COLS = list(catm.REQUIRED) + ["Body HTML", "Variant Barcodes", "Variant Inventory Qty",
                                  "Variant Country of Origin", "Variant HS Code", catm.MADE_IN, catm.SEASON,
                                  catm.NEW_SALE, "Created At"]


def _variant(pid, title, ptype, status, code, colour, size, sku, price, cmp_, cost, top, **kw):
    r = {c: "" for c in CAT_COLS}
    r.update({"ID": pid, "Variant ID": f"{pid}{size}", "Option1 Name": "Colour", "Option1 Value": colour,
              "Option2 Name": "Size", "Option2 Value": size, "Variant SKU": sku, "Variant Barcodes": sku,
              "Variant Price": price, "Variant Compare At Price": cmp_, "Variant Cost": cost,
              "Variant Country of Origin": "IT", "Variant Inventory Qty": "2", "Top Row": "true" if top else ""})
    if top:
        r.update({"Handle": title.lower().replace(" ", "-"), "Title": title, "Vendor": "MAISON MARGIELA",
                  "Type": ptype, "Tags": "MEN, F/W 26", "Status": status, catm.MCODE: code,
                  catm.SEASON: "F/W 26", catm.NEW_SALE: kw.get("new_sale", "NEW"), "Created At": "2025-01-01 10:00:00 +1100",
                  "Body HTML": "<p>Composition: 100% Calf Leather</p>"})
    return r


@pytest.fixture
def catalog_path(tmp_path):
    rows = []
    for i, size in enumerate(["40", "41", "42"]):
        rows.append(_variant("1", "Replica Sneaker in White", "MEN SHOES SNEAKERS", "Active", "S99WS0001P0001 101",
                             "WHITE", size, f"MMFW26{100 + i}", "800.00", "800.00", "310.00", i == 0, new_sale="CORE"))
    for i, size in enumerate(["40", "41"]):
        rows.append(_variant("2", "Replica Sneaker in White", "MEN SHOES SNEAKERS", "Archived", "S99WS0001P0001 101",
                             "WHITE", size, f"MMSS25{100 + i}", "900.00", "900.00", "400.00", i == 0))
    for i, size in enumerate(["S", "M"]):
        rows.append(_variant("3", "Logo Tee in Black", "MEN CLOTHING T-SHIRTS SHORT SLEEVE", "Active", "S98GC0003S00003 900",
                             "BLACK", size, f"MMFW26{200 + i}", "560.00", "800.00", "250.00", i == 0))
    p = tmp_path / "catalog.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CAT_COLS)
        w.writeheader()
        w.writerows(rows)
    return str(p)


def _docs():
    return [dict(doc="SS - 1", collection="MM MALE SHOES AVP", gender="MEN", total_qty=6, net=1200.0, lines=[
        dict(line_no=1, style="S99WS0001", fabric="P0001", colour="101", product_class="SNEAKERS",
             sizes={"41": 1, "43": 1}, qty=2, price=200.0, line_value=400.0),
        dict(line_no=2, style="S99WS0001", fabric="P0001", colour="900", product_class="SNEAKERS",
             sizes={"41": 2, "42": 2}, qty=4, price=200.0, line_value=800.0),
    ])]


def _hist():
    return histm.History([("F/W 26", [])])


def _tags():
    return core.load_tag_lookup()


def test_norm_size():
    assert norm_size("UNI") == "OS" and norm_size("03") == "3" and norm_size(48.0) == "48" and norm_size("48.0") == "48"
    assert sort_sizes(["XL", "S", "M"]) == ["S", "M", "XL"]
    assert join_key("S99WZ0004", "P0004", "T0004") == "S99WZ0004P0004 T0004"


def test_match_drops_archived_twin_and_fails_two_live(catalog_path, tmp_path):
    cat = catm.load(catalog_path)
    p, note, cands = cat.match("S99WS0001P0001 101")
    assert p["id"] == "1" and "ARCHIVED" in note and len(cands) == 2
    rows = list(csv.DictReader(open(catalog_path, encoding="utf-8-sig")))
    for r in rows:
        if r["ID"] == "2":
            r["Status"] = "Active" if r["Status"] else ""
    p2 = tmp_path / "two_live.csv"
    with open(p2, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CAT_COLS)
        w.writeheader()
        w.writerows(rows)
    p, note, _ = catm.load(str(p2)).match("S99WS0001P0001 101")
    assert p is None and note.startswith("AMBIGUOUS")


def test_catalog_contract_enforced(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("ID,Title\n1,x\n", encoding="utf-8-sig")
    with pytest.raises(catm.CatalogError):
        catm.load(str(p))


def test_draft_merge_and_new(catalog_path):
    cat = catm.load(catalog_path)
    rows, review, checks, fills = build(_docs(), BRAND, cat, _hist(), _tags())
    merge = [r for r in rows if r["Command"] == "MERGE"]
    new = [r for r in rows if r["Command"] == "NEW"]
    assert {r["Option2 Value"] for r in merge} == {"40", "41", "42", "43"}
    assert next(r for r in merge if r["Option2 Value"] == "43")["Variant SKU"].startswith("MMSS27")
    assert next(r for r in merge if r["Option2 Value"] == "41")["Variant SKU"] == "MMFW26101"
    assert {r["Title"] for r in new} == {"Replica Sneaker in 900"}
    assert new[0]["Type"] == "MEN SHOES SNEAKERS"
    assert new[0]["Variant Price"] == 800
    assert sum(r["Order Qty"] for r in rows) == 6
    assert not [c for c in checks if c["Result"] == "FAIL"]
    sources = {(x["Column"], x["Source"]) for x in review if x["Code"] == "S99WS0001P0001 900"}
    assert ("Title", "guess") in sources and ("Variant SKU", "rule") in sources  # stem is live, colour name is not


def test_sku_allocation_skips_catalog(catalog_path):
    cat = catm.load(catalog_path)
    cat.skus.add("MMSS27001")
    R = Resolver(BRAND, cat, _hist(), _tags())
    assert R.next_sku()[0] == "MMSS27002"
    assert R.next_sku()[0] == "MMSS27003"


def _row(eur, price, code="X 1"):
    return dict(eur=eur, price=price, type="MEN SHOES SNEAKERS", code=code)


def test_retail_respects_margin_floor(catalog_path):
    cat = catm.load(catalog_path)
    R = Resolver(BRAND, cat, histm.History([("F/W 26", [_row(100.0, 300.0), _row(100.0, 300.0)])]), _tags())
    price, src, why = R.retail("S99WS0001P1 1", 100.0, "MEN SHOES SNEAKERS")
    assert src == "rule" and price == 350 and price / R.cost(100.0) >= 2.25
    R = Resolver(BRAND, cat, histm.History([("F/W 26", [_row(100.0, 300.0)])]), _tags())
    price, src, why = R.retail("S99WS0001P1 1", 100.0, "MEN SHOES SNEAKERS")
    assert src == "thin" and price == 350  # a raise never makes a one-row price look better than red


def test_price_shading_by_evidence(catalog_path):
    cat = catm.load(catalog_path)
    hist = histm.History([("F/W 26", [_row(150.0, 600.0), _row(150.0, 600.0), _row(170.0, 700.0)]
                           + [_row(e, e * 4.0, f"Y {e}") for e in (101.0, 102.0, 103.0, 104.0, 105.0)])])
    docs = _docs()
    R = Resolver(BRAND, cat, hist, _tags(), draft_lines(docs))
    assert R.retail("S99WS0001P0001 555", 200.0, "MEN SHOES SNEAKERS")[1] == "catalog"   # same style+fabric sibling
    assert R.retail("S11ZZ0001P9 1", 200.0, "MEN SHOES SNEAKERS")[1] == "similar"        # other carryover style, same EUR
    assert R.retail("S11ZZ0001P9 1", 150.0, "MEN SHOES SNEAKERS")[1] == "history"        # 2 past rows
    assert R.retail("S11ZZ0001P9 1", 170.0, "MEN SHOES SNEAKERS")[1] == "thin"           # 1 past row
    assert R.retail("S11ZZ0001P9 1", 333.0, "MEN SHOES SNEAKERS")[1] == "fit"            # ratio
    from oc.resolve import COLOUR
    assert [COLOUR[s] for s in ("catalog", "similar", "history", "thin", "fit")] == ["green", "amber", "amber", "red", "red"]


def test_resolve_formula_rejects_unknown():
    raw = {2: [None, 5.0]}
    assert poxlsx.resolve_formula("=B2", raw, 2) == 5.0
    with pytest.raises(ValueError):
        poxlsx.resolve_formula("=B2*2", raw, 2)


def _hand_po(path, cat_rows):
    header = poxlsx.PO_HEADER
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for r in cat_rows:
        ws.append([r.get(h) if h else None for h in header])
    wb.save(path)


def test_complete_keeps_human_cells_and_finalize_is_safe(catalog_path, tmp_path):
    cat = catm.load(catalog_path)
    base = {"Vendor": "MAISON MARGIELA", "Status": "Active", "Option1 Name": "Colour", "Option2 Name": "Size",
            poxlsx.SEASON: "S/S 27", poxlsx.NEW_SALE: "NEW", "Wholesale Price": 200.0, "Variant Cost": 300.0,
            poxlsx.COST_PRICE: "EUR 200"}
    merge = [dict(base, Command="MERGE", Title="Replica Sneaker in White", Type="MEN SHOES SNEAKERS", Published=True,
                  **{"Option1 Value": "WHITE", "Option2 Value": s, "Variant SKU": sku, "Variant Price": 800.0,
                     poxlsx.CODE: "S99WS0001P0001 101", "Order Qty": q, "Total Order": q * 200.0,
                     poxlsx.MADE_IN: "-"})
             for s, sku, q in (("41", "MMFW26101", 1), ("43", None, 1))]
    new = [dict(base, Command="NEW", Title="Replica Sneaker in Black", Type="MEN SHOES SNEAKERS", Published=False,
                **{"Option1 Value": "BLACK", "Option2 Value": float(s), poxlsx.CODE: "S99WS0001P0001 900",
                   "Order Qty": q, "Total Order": q * 200.0, poxlsx.MADE_IN: "-", "Body HTML": "<p>hand</p>",
                   poxlsx.TITLE_TAG: "MAISON MARGIELA - Replica Sneaker in Black |MARAIS"})
           for s, q in (("41", 2), ("42", 2))]
    hand = tmp_path / "hand.xlsx"
    _hand_po(hand, merge + new)
    out = tmp_path / "po.xlsx"
    checks = complete_run(str(hand), _docs(), BRAND, cat, _hist(), _tags(), str(out))
    assert not [c for c in checks if c["Result"] == "FAIL"], checks
    _, rows = poxlsx.read_po(str(out))
    by = {(r["Command"], norm_size(r["Option2 Value"])): r for r in rows}
    assert by[("NEW", "41")]["Body HTML"] == "<p>hand</p>"
    assert by[("NEW", "41")]["Variant Price"] == 800
    assert by[("NEW", "41")]["_raw"]["Variant Compare At Price"].startswith("=P")
    assert by[("MERGE", "43")]["Variant SKU"].startswith("MMSS27")
    assert by[("MERGE", "41")]["Variant SKU"] == "MMFW26101"
    assert by[("MERGE", "41")][poxlsx.MADE_IN] == "Italy"
    review = openpyxl.load_workbook(out)["Review"]
    assert review.max_row > 1

    prefix = str(tmp_path / "x-")
    fchecks = finalize.run(str(out), BRAND, cat, prefix)
    assert not [c for c in fchecks if c["Result"] == "FAIL"], fchecks
    pu_merge = pd.read_excel(prefix + "PU-merge.xlsx")
    assert list(pu_merge.columns) == finalize.MERGE_PRODUCT_COLS
    assert set(pu_merge["Command"]) == {"UPDATE"} and pu_merge["ID"].notna().all()
    assert set(pu_merge["Tags Command"]) == {"MERGE"}
    rb = pd.read_excel(prefix + "PU-merge-ROLLBACK.xlsx", dtype=str)
    assert list(rb["Command"]) == ["UPDATE"] and list(rb["Tags Command"]) == ["DELETE"] and list(rb["Tags"]) == ["S/S 27"]
    assert list(rb[poxlsx.SEASON]) == ["F/W 26"]
    for forbidden in ("Title", "Handle", "Body HTML", "Variant Price", "Variant Cost", "Metafield: title_tag"):
        assert forbidden not in pu_merge.columns
    sizes = pd.read_excel(prefix + "PU-merge-new-sizes.xlsx", dtype=str)
    assert list(sizes["Option2 Value"]) == ["43"] and sizes["Variant SKU"][0].startswith("MMSS27")
    assert list(sizes["Command"]) == ["UPDATE"] and list(sizes["Variant Command"]) == ["MERGE"]
    assert list(sizes["Variant Cost"]) == ["310"]  # the product's live cost, not the PO's 300
    pu_new = pd.read_excel(prefix + "PU-new.xlsx", dtype=str)
    assert list(pu_new.columns) == core.COLUMNS_IN_ORDER
    assert list(pu_new["Option2 Value"]) == ["41", "42"]
    assert list(pu_new[poxlsx.MADE_IN]) == ["Italy", "Italy"]
    ws = openpyxl.load_workbook(prefix + "Delivery.xlsx")["Sheet 1"]
    hdr = [c.value for c in ws[1]]
    assert hdr == finalize.DELIVERY_HEADER
    assert ws.cell(2, hdr.index("Total Delivered") + 1).value.startswith('=SUMIF($V$1:$AZ$1,"<>Value"')
    assert ws.cell(2, hdr.index("Remaining") + 1).value == "=S2-T2"
    assert sum(ws.cell(r, hdr.index("Order Qty") + 1).value for r in range(2, ws.max_row + 1)) == 6


def test_complete_rejects_a_price_override_the_oc_does_not_confirm(catalog_path, tmp_path):
    cat = catm.load(catalog_path)
    hand = tmp_path / "hand.xlsx"
    _hand_po(hand, [])
    with pytest.raises(SystemExit):
        complete_run(str(hand), _docs(), BRAND, cat, _hist(), _tags(), str(tmp_path / "o.xlsx"),
                     price_overrides={"S99WS0001P0001 101": 999.0})


def _finalize_po(tmp_path, title="Replica Sneaker in Black", option1="BLACK"):
    base = {"Vendor": "MAISON MARGIELA", "Status": "Active", "Option1 Name": "Colour", "Option2 Name": "Size",
            poxlsx.SEASON: "S/S 27", poxlsx.NEW_SALE: "NEW", "Wholesale Price": 200.0, "Variant Cost": 300.0,
            poxlsx.COST_PRICE: "EUR 200", "Command": "NEW", "Title": title, "Type": "MEN SHOES SNEAKERS",
            "Published": False, "Option1 Value": option1, poxlsx.CODE: "S99WS0001P0001 900", poxlsx.MADE_IN: "Italy",
            "Body HTML": "<p>x</p>", "Variant Price": 800.0, "Variant Compare At Price": 800.0,
            poxlsx.TITLE_TAG: f"MAISON MARGIELA - {title} |MARAIS"}
    rows = [dict(base, **{"Option2 Value": s, "Variant SKU": f"MMSS2709{s}", "Order Qty": 2, "Total Order": 400.0})
            for s in ("41", "42")]
    po = tmp_path / "po.xlsx"
    _hand_po(po, rows)
    return str(po)


@pytest.mark.parametrize("title,option1,needle", [
    ("Replica Sneaker in 900", "BLACK", "raw style/fabric/colour code"),
    ("Sneakers in P0001", "BLACK", "raw style/fabric/colour code"),
    ("Replica Sneaker in Black", "", "Option1"),
])
def test_finalize_is_all_or_nothing(catalog_path, tmp_path, title, option1, needle):
    cat = catm.load(catalog_path)
    prefix = str(tmp_path / "x-")
    ok = finalize.run(_finalize_po(tmp_path), BRAND, cat, prefix)
    assert not [c for c in ok if c["Result"] == "FAIL"], ok
    assert all(os.path.exists(prefix + n) for n in ("PU-new.xlsx", "Delivery.xlsx"))
    bad = finalize.run(_finalize_po(tmp_path, title, option1), BRAND, cat, prefix)
    fails = [c["Check"] for c in bad if c["Result"] == "FAIL"]
    assert any(needle in f for f in fails), fails
    assert not any(os.path.exists(prefix + n) for n in finalize.OUTPUTS)
    assert os.path.exists(prefix + "finalize-checks.xlsx")


def test_raw_code_in_title():
    assert finalize.raw_code_in_title("Trench Coat in 114", "S98AH0001M00001 114") == ["114"]
    assert finalize.raw_code_in_title("Sandal in T0099", "S99WP0001P0001 T0099") == ["T0099"]
    assert finalize.raw_code_in_title("Box Card Holder 3Cc in Black", "SA9XX0001P0001 T0004") == []


def test_brand_config_lookup_order(tmp_path, monkeypatch):
    from oc import brand as brandm
    body = "\n".join(f"{k}: {v}" for k, v in dict(vendor="X", adapter="otb", season="S/S 27", sku_prefix="XX",
                                                    cost_multiplier=1.5, cost_currency="EUR", margin_floor=2.5).items())
    d = tmp_path / "brands"
    d.mkdir()
    (d / "zzbrand.local.yaml").write_text(body)
    monkeypatch.setenv("OC_BRAND_DIR", str(d))
    assert brandm.load("zzbrand")["_path"] == str(d / "zzbrand.local.yaml")
    other = tmp_path / "other.yaml"
    other.write_text(body.replace("vendor: X", "vendor: Y"))
    assert brandm.load("zzbrand", str(other))["vendor"] == "Y"
    monkeypatch.delenv("OC_BRAND_DIR")
    with pytest.raises(brandm.BrandError):
        brandm.load("zzbrand")
