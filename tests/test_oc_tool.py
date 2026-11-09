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

CAT_COLS = list(dict.fromkeys(list(catm.REQUIRED) + ["Body HTML", "Variant Barcodes", "Variant Inventory Qty",
                                                       "Variant Country of Origin", "Variant HS Code", catm.MADE_IN,
                                                       catm.SEASON, catm.NEW_SALE, "Created At"]))

MERGE_SHEET_COLS = ["ID", "Command", "Tags Command", "Tags", poxlsx.SEASON, "Variant ID", "Variant Command", "Variant Cost"]


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
        poxlsx.append_text(ws, [r.get(h) if h else None for h in header])  # as the tool writes a PO: text stays text
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
    pu_merge = pd.read_excel(prefix + "PU-merge.xlsx", dtype=str)
    assert list(pu_merge.columns) == MERGE_SHEET_COLS
    assert set(pu_merge["Command"]) == {"UPDATE"} and pu_merge["ID"].notna().all()
    assert list(pu_merge["Tags Command"].fillna("")) == ["MERGE", "", ""]
    rb = pd.read_excel(prefix + "PU-merge-ROLLBACK.xlsx", dtype=str).fillna("")
    assert list(rb["Command"]) == ["UPDATE"] * 3 and list(rb["Tags Command"]) == ["DELETE", "", ""]
    assert list(rb["Tags"]) == ["S/S 27", "", ""] and list(rb[poxlsx.SEASON]) == ["F/W 26", "", ""]
    for forbidden in ("Title", "Handle", "Body HTML", "Variant Price", "Variant SKU", poxlsx.COST_PRICE,
                      "Metafield: title_tag"):
        assert forbidden not in pu_merge.columns and forbidden not in rb.columns
    sizes = pd.read_excel(prefix + "PU-merge-new-sizes.xlsx", dtype=str)
    assert list(sizes["Option2 Value"]) == ["43"] and sizes["Variant SKU"][0].startswith("MMSS27")
    assert list(sizes["Command"]) == ["UPDATE"] and list(sizes["Variant Command"]) == ["MERGE"]
    assert list(sizes["Variant Cost"]) == ["300"]  # the OC cost (EUR 200 x 1.5), not the siblings' live 310
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


def _finalize_po(tmp_path, title="Replica Sneaker in Black", option1="BLACK", body="<p>x</p>"):
    base = {"Vendor": "MAISON MARGIELA", "Status": "Active", "Option1 Name": "Colour", "Option2 Name": "Size",
            poxlsx.SEASON: "S/S 27", poxlsx.NEW_SALE: "NEW", "Wholesale Price": 200.0, "Variant Cost": 300.0,
            poxlsx.COST_PRICE: "EUR 200", "Command": "NEW", "Title": title, "Type": "MEN SHOES SNEAKERS",
            "Published": False, "Option1 Value": option1, poxlsx.CODE: "S99WS0001P0001 900", poxlsx.MADE_IN: "Italy",
            "Body HTML": body, "Variant Price": 800.0, "Variant Compare At Price": 800.0,
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


# ---- carryover cost: the OC overwrites the old season cost on every size (Colwyn, 2026-11-09) ----
LIVE_COSTS = {"40": ("310.00", "2"), "41": ("305.50", "1"), "42": ("312.25", "3")}


def _split_rows():
    """Product 1's live sizes carry three different costs and available counts; product 3 is not re-bought."""
    rows = []
    for i, (size, (cost, qty)) in enumerate(LIVE_COSTS.items()):
        r = _variant("1", "Replica Sneaker in White", "MEN SHOES SNEAKERS", "Active", "S99WS0001P0001 101",
                     "WHITE", size, f"MMFW26{100 + i}", "800.00", "800.00", cost, i == 0)
        r[catm.AVAILABLE] = qty
        rows.append(r)
    for i, size in enumerate(["S", "M"]):
        rows.append(_variant("3", "Logo Tee in Black", "MEN CLOTHING T-SHIRTS SHORT SLEEVE", "Active", "S98GC0003S00003 900",
                             "BLACK", size, f"MMFW26{200 + i}", "560.00", "800.00", "250.00", i == 0))
    return rows


def _write_catalog(path, rows, cols=CAT_COLS):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return str(path)


@pytest.fixture
def split_catalog_path(tmp_path):
    return _write_catalog(tmp_path / "split.csv", _split_rows())


def _merge_po(tmp_path, po_cost=300.0, cost_price="EUR 200"):
    base = {"Command": "MERGE", "Vendor": "MAISON MARGIELA", "Status": "Active", "Title": "Replica Sneaker in White",
            "Type": "MEN SHOES SNEAKERS", "Published": True, "Option1 Name": "Colour", "Option1 Value": "WHITE",
            "Option2 Name": "Size", poxlsx.CODE: "S99WS0001P0001 101", poxlsx.SEASON: "S/S 27", poxlsx.NEW_SALE: "CORE",
            poxlsx.MADE_IN: "Italy", "Variant Price": 800.0, "Variant Compare At Price": 800.0,
            "Wholesale Price": 200.0, "Variant Cost": po_cost, poxlsx.COST_PRICE: cost_price, "Order Qty": 1,
            "Total Order": 200.0}
    rows = [dict(base, **{"Option2 Value": s, "Variant SKU": sku}) for s, sku in (("41", "MMFW26101"), ("43", "MMSS27001"))]
    po = tmp_path / "merge-po.xlsx"
    _hand_po(po, rows)
    return str(po)


def test_oc_cost_overwrites_every_variant_of_a_rebought_product(split_catalog_path, tmp_path):
    cat = catm.load(split_catalog_path)
    prefix = str(tmp_path / "x-")
    checks = finalize.run(_merge_po(tmp_path), BRAND, cat, prefix)
    assert not [c for c in checks if c["Result"] == "FAIL"], checks
    by = {c["Check"]: c for c in checks}

    m = pd.read_excel(prefix + "PU-merge.xlsx", dtype=str).fillna("")
    assert list(m.columns) == MERGE_SHEET_COLS
    assert list(m["ID"]) == ["1", "1", "1"]
    assert list(m["Variant ID"]) == ["140", "141", "142"]  # 40 and 42 are not on the OC and still take its cost
    assert list(m["Variant Cost"]) == ["300", "300", "300"]  # EUR 200 x 1.5
    assert list(m["Command"]) == ["UPDATE"] * 3 and list(m["Variant Command"]) == ["UPDATE"] * 3
    assert list(m["Tags"]) == ["S/S 27", "", ""] and list(m[poxlsx.SEASON]) == ["S/S 27", "", ""]
    assert not {"3", "2"} & set(m["ID"]) and not {"3S", "3M"} & set(m["Variant ID"])

    sizes = pd.read_excel(prefix + "PU-merge-new-sizes.xlsx", dtype=str)
    assert list(sizes["Option2 Value"]) == ["43"] and list(sizes["Variant Cost"]) == ["300"]

    rb = pd.read_excel(prefix + "PU-merge-ROLLBACK.xlsx").fillna("")
    assert list(rb.columns) == MERGE_SHEET_COLS
    assert [str(x) for x in rb["Variant ID"]] == ["140", "141", "142"]
    assert list(rb["Variant Cost"]) == [310.0, 305.5, 312.25]  # each variant's own prior cost
    assert list(rb["Variant Command"]) == ["UPDATE"] * 3 and list(rb[poxlsx.SEASON]) == ["F/W 26", "", ""]
    assert not {3, "3"} & set(rb["ID"])

    overwrite = next(c for k, c in by.items() if k.startswith("OC cost on every live variant"))
    assert overwrite["Result"] == "PASS"
    split = next(c for k, c in by.items() if "carry different costs" in k)
    assert split["Result"] == "INFO" and "Replica Sneaker in White" in split["Detail"]
    restate = next(c for k, c in by.items() if k.startswith("Cost restatement"))
    assert restate["Result"] == "INFO" and restate["Detail"].startswith("6 units on 1 products restate by A$-62.25")
    assert not [c for c in checks if c["Result"] == "WARN"], [c for c in checks if c["Result"] == "WARN"]


def test_po_cost_that_disagrees_with_wholesale_x_multiplier_fails(split_catalog_path, tmp_path):
    cat = catm.load(split_catalog_path)
    prefix = str(tmp_path / "x-")
    checks = finalize.run(_merge_po(tmp_path, po_cost=310.0), BRAND, cat, prefix)
    fails = [c["Check"] for c in checks if c["Result"] == "FAIL"]
    assert any(f.startswith("PO Variant Cost = Wholesale x 1.5") for f in fails), fails
    assert not any(os.path.exists(prefix + n) for n in finalize.OUTPUTS)


def test_cost_overwrite_check_names_each_failure(split_catalog_path):
    cat = catm.load(split_catalog_path)
    merge = [{poxlsx.CODE: "S99WS0001P0001 101", "Wholesale Price": 200.0}]
    products = {"1": (cat.products["1"], None)}

    def rows(costs, ids=("140", "141", "142"), pid="1"):
        return [{"ID": pid, "Variant ID": v, "Variant Cost": c} for v, c in zip(ids, costs)]

    def result(r, new=()):
        return finalize._cost_overwrite_check(merge, 1.5, cat, products, r, list(new))

    assert result(rows([300.0] * 3))["Result"] == "PASS"
    assert "not in PU-merge" in result(rows([300.0] * 2, ("140", "141")))["Detail"]
    assert "cost 310.0" in result(rows([300.0, 310.0, 300.0]))["Detail"]
    assert "not a variant of product 1" in result(rows([300.0] * 3) + rows([300.0], ("3S",)))["Detail"]
    assert "not re-bought are touched: ['3']" in result(rows([300.0] * 3) + rows([300.0], ("3S",), "3"))["Detail"]
    assert "new size MMSS27001" in result(rows([300.0] * 3), [{"ID": "1", "Variant SKU": "MMSS27001", "Variant Cost": 310.0}])["Detail"]


def _cells(ws, col):
    hdr = [c.value for c in ws[1]]
    return [(ws.cell(r, hdr.index(col) + 1).value, ws.cell(r, hdr.index(col) + 1).data_type) for r in range(2, ws.max_row + 1)]


def test_text_that_starts_like_a_formula_is_written_as_text(catalog_path, split_catalog_path, tmp_path):
    title, body = '=HYPERLINK("http://x.test","Replica Sneaker in Black")', "=1+2"
    prefix = str(tmp_path / "n-")
    checks = finalize.run(_finalize_po(tmp_path, title=title, body=body), BRAND, catm.load(catalog_path), prefix)
    assert not [c for c in checks if c["Result"] == "FAIL"], checks
    new = openpyxl.load_workbook(prefix + "PU-new.xlsx")["Products"]
    assert _cells(new, "Title") == [(title, "s")] * 2 and _cells(new, "Body HTML") == [(body, "s")] * 2
    d = openpyxl.load_workbook(prefix + "Delivery.xlsx")["Sheet 1"]
    assert _cells(d, "Title") == [(title, "s")] * 2
    assert [t for _, t in _cells(d, "Total Delivered") + _cells(d, "Remaining")] == ["f"] * 4  # the sheet's own formulas

    cat = catm.load(split_catalog_path)
    cat.products["1"]["season"] = "=F/W 26"
    prefix = str(tmp_path / "m-")
    checks = finalize.run(_merge_po(tmp_path, cost_price="=EUR 200"), dict(BRAND, season="=S/S 27"), cat, prefix)
    assert not [c for c in checks if c["Result"] == "FAIL"], checks
    m = openpyxl.load_workbook(prefix + "PU-merge.xlsx")["Products"]
    assert _cells(m, "Tags")[0] == ("=S/S 27", "s") and _cells(m, poxlsx.SEASON)[0] == ("=S/S 27", "s")
    rb = openpyxl.load_workbook(prefix + "PU-merge-ROLLBACK.xlsx")["Products"]
    assert _cells(rb, poxlsx.SEASON)[0] == ("=F/W 26", "s")
    ns = openpyxl.load_workbook(prefix + "PU-merge-new-sizes.xlsx")["Products"]
    assert _cells(ns, poxlsx.COST_PRICE) == [("=EUR 200", "s")]


# ---- the catalog's season and available stock (reviewer case 2026-11-09-buyer-8 F6) ----
@pytest.mark.parametrize("col,group", [(catm.SEASON, "Metafields"), (catm.AVAILABLE, "Inventory / Variants")])
def test_a_catalog_without_the_season_or_available_column_fails_naming_it_and_its_export_group(tmp_path, col, group):
    path = _write_catalog(tmp_path / "short.csv", _split_rows(), [c for c in CAT_COLS if c != col])
    with pytest.raises(catm.CatalogError) as e:
        catm.load(path)
    assert f"missing columns: '{col}' (Matrixify Products export, {group} group)." in str(e.value)


def _by(checks, prefix):
    return next(c for c in checks if c["Check"].startswith(prefix))


def test_a_product_with_no_season_gets_ignore_in_the_rollback_never_an_empty_cell(tmp_path):
    rows = _split_rows()
    rows[0][catm.SEASON] = ""
    prefix = str(tmp_path / "x-")
    checks = finalize.run(_merge_po(tmp_path), BRAND, catm.load(_write_catalog(tmp_path / "c.csv", rows)), prefix)
    assert not [c for c in checks if c["Result"] == "FAIL"], checks
    rb = openpyxl.load_workbook(prefix + "PU-merge-ROLLBACK.xlsx")["Products"]
    assert _cells(rb, poxlsx.SEASON)[0] == ("[IGNORE]", "s")  # an empty cell would delete the metafield
    m = openpyxl.load_workbook(prefix + "PU-merge.xlsx")["Products"]
    assert _cells(m, poxlsx.SEASON)[0] == ("S/S 27", "s")
    warn = _by(checks, "Re-bought products with no season in the catalog export")
    assert warn["Result"] == "WARN" and warn["Detail"].startswith("1: Replica Sneaker in White. ")
    info = _by(checks, "Rollback for PU-merge")["Detail"]
    assert "('', " not in info and "[]; 1 with none keep S/S 27, cell [IGNORE])" in info


def test_restatement_counts_available_units(split_catalog_path, tmp_path):
    checks = finalize.run(_merge_po(tmp_path), BRAND, catm.load(split_catalog_path), str(tmp_path / "x-"))
    restate = _by(checks, "Cost restatement on available stock")
    assert "on hand" not in restate["Check"] and restate["Detail"].startswith("6 units on 1 products")


# ---- a catalog exported after PU-merge was imported (F8) ----
POST_IMPORT = "Catalog export taken before PU-merge was imported"
SIGNS = {"season": "season already S/S 27", "tag": "'S/S 27' tag already on it",
         "cost": "every size already at the OC cost A$300.0"}


def _imported(rows, signs=tuple(SIGNS)):
    """Product 1 as the export reads after PU-merge is imported, or with only some of its marks."""
    out = []
    for r in rows:
        r = dict(r)
        if r["ID"] == "1":
            if "cost" in signs:
                r["Variant Cost"] = "300.00"
            if r["Top Row"] == "true" and "season" in signs:
                r[catm.SEASON] = "S/S 27"
            if r["Top Row"] == "true" and "tag" in signs:
                r["Tags"] += ", S/S 27"
        out.append(r)
    return out


def _rollback(prefix):
    return pd.read_excel(prefix + "PU-merge-ROLLBACK.xlsx").fillna("")


def test_a_catalog_exported_after_the_import_warns_and_its_rollback_undoes_nothing(tmp_path):
    rows = _split_rows()
    first, rerun, late = (str(tmp_path / f"{n}-") for n in ("first", "rerun", "late"))
    pre = _write_catalog(tmp_path / "pre.csv", rows)
    for prefix in (first, rerun):  # a genuine re-run before the import is unchanged
        checks = finalize.run(_merge_po(tmp_path), BRAND, catm.load(pre), prefix)
        assert _by(checks, POST_IMPORT)["Result"] == "PASS"
        assert not [c for c in checks if c["Result"] in ("FAIL", "WARN")], checks
    assert _rollback(first).equals(_rollback(rerun))
    assert list(_rollback(first)["Variant Cost"]) == [310.0, 305.5, 312.25]
    assert _rollback(first)["Tags Command"][0] == "DELETE"

    checks = finalize.run(_merge_po(tmp_path), BRAND, catm.load(_write_catalog(tmp_path / "post.csv", _imported(rows))),
                          late)
    assert not [c for c in checks if c["Result"] == "FAIL"], checks
    warn = _by(checks, POST_IMPORT)
    assert warn["Result"] == "WARN"
    assert warn["Detail"].startswith(f"1 of 1 re-bought products already look imported: Replica Sneaker in White "
                                     f"({', '.join(SIGNS.values())}). ")
    assert "Keep and use the first PU-merge-ROLLBACK" in warn["Detail"]
    rb = _rollback(late)  # why: built from the imported state, it puts back the OC cost and keeps the tag
    assert list(rb["Variant Cost"]) == [300.0] * 3 and rb["Tags Command"][0] == "MERGE" and rb[poxlsx.SEASON][0] == "S/S 27"


@pytest.mark.parametrize("sign", SIGNS)
def test_each_post_import_sign_alone_warns(tmp_path, sign):
    cat = catm.load(_write_catalog(tmp_path / "c.csv", _imported(_split_rows(), (sign,))))
    warn = _by(finalize.run(_merge_po(tmp_path), BRAND, cat, str(tmp_path / "x-")), POST_IMPORT)
    assert warn["Result"] == "WARN" and f"Replica Sneaker in White ({SIGNS[sign]}). " in warn["Detail"]


def test_one_size_already_at_the_oc_cost_is_not_a_post_import_sign(tmp_path):
    rows = _split_rows()
    rows[0]["Variant Cost"] = "300.00"
    cat = catm.load(_write_catalog(tmp_path / "c.csv", rows))
    assert _by(finalize.run(_merge_po(tmp_path), BRAND, cat, str(tmp_path / "x-")), POST_IMPORT)["Result"] == "PASS"
