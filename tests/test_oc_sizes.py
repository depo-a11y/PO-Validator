"""One size, one name: a printed size takes the store's name for it, matched against live variants, never rewritten
blindly (backtest 2026-11-12: 'IT 42' beside '42', OSFM beside OS). Synthetic data only."""
import pytest
import yaml

import test_oc_tool as base
from oc import brand as brandm, catalog as catm, draft as draftm
from oc.util import one_name_per_size, size_alias, size_clashes, store_sizes

CODE = "S99WP0001P0001 101"


def test_size_alias_is_a_comparison_form_only():
    assert {size_alias(s) for s in ("OS", "OSFM", "O/S", "F")} == {"OS"}
    assert size_alias("IT 42") == size_alias("42") == "42" and size_alias("IT 42.5") == "42.5"
    assert size_alias("S - M") == size_alias("S-M") == "S-M" and size_alias("XS") == "XS"


W = "where"


@pytest.mark.parametrize("ordered,known,size_map,quorum,want", [
    ({"42": 1, "44": 2}, [{"IT 42": W, "IT 44": W}], None, None, {"IT 42": 1, "IT 44": 2}),
    ({"OSFM": 3}, [{"OS": W}], None, None, {"OS": 3}),
    ({"O/S": 3}, [{}, {"OS": W}], None, None, {"OS": 3}),                     # the first level that knows it decides
    ({"F": 3}, [{"OS": W}], None, None, {"OS": 3}),
    ({"F": 3}, [{"S": W, "M": W}, {}], None, None, {"F": 3}),                 # no OS among its sizes: F stays F
    ({"S-M": 1}, [{"S - M": W, "L - XL": W}], None, None, {"S - M": 1}),      # the live product's own spelling
    ({"42": 1}, [{"42": W, "IT 42": W}], None, None, {"42": 1}),              # a known name is kept as printed
    ({"OSFM": 1}, [{"OS": W, "O/S": W}], None, None, {"OSFM": 1}),            # two store names: nothing is rewritten
    ({"31X34": 2, "31X32": 1}, [{"31": W, "32": W}], {"31X34": "31", "31X32": "31"}, None, {"31": 3}),   # size_map first
    ({"42": 1}, [{"IT 42": W}, {"42": W}], None, None, {"IT 42": 1}),
    ({"OSFM": 3}, [{}, {}], None, {"OS": 40, "OSFM": 1}, {"OS": 3}),          # a clear brand-wide majority renames
    ({"40": 1}, [{}, {}], None, {"IT 40": 9}, {"40": 1}),                      # nine products are a handful
    ({"40": 1}, [{}, {}], None, {"40": 500, "IT 40": 30}, {"40": 1}),          # the printed name is the majority
    ({"F": 2}, [{}, {}], None, {"OS": 12, "F": 3}, {"F": 2}),                  # 12 of 15 is no clear majority
])
def test_store_sizes_takes_the_store_name_for_a_printed_size(ordered, known, size_map, quorum, want):
    assert store_sizes(ordered, known, size_map, quorum)[0] == want


def test_a_renamed_size_says_what_was_printed_and_why():
    assert store_sizes({"42": 1}, [{"IT 42": "the live product Test Pant"}])[1] == {
        "IT 42": "printed '42', named 'IT 42' as on the live product Test Pant"}
    assert store_sizes({"OSFM": 3}, [{}], quorum={"OS": 40})[1] == {
        "OS": "printed 'OSFM', named 'OS' as on 40 of the brand's 40 live products with this size"}
    assert store_sizes({"31X34": 1}, [{"31": "p"}], {"31X34": "31"})[1] == {
        "31": "printed '31X34', named '31' by the brand's size_map"}
    assert store_sizes({"40": 1}, [{"40": "p"}])[1] == {}


def test_a_run_spelled_two_ways_keeps_the_ordered_sizes_names():
    assert one_name_per_size(["36", "IT 36", "38", "IT 38", "40", "IT 40"], {"40": 1}) == ["36", "38", "40"]
    assert one_name_per_size(["IT 36", "36", "IT 40", "40"], {"IT 40": 1}) == ["IT 36", "IT 40"]
    assert one_name_per_size(["OS", "OSFM"], {"OS": 2}) == ["OS"] and one_name_per_size(["S", "M"], {"S": 1}) == ["S", "M"]


def test_size_clashes_names_one_size_spelled_twice():
    assert size_clashes(["42", "IT 42", "44", "OS", "OSFM"]) == [["42", "IT 42"], ["OS", "OSFM"]]
    assert size_clashes(["40", "41", "S - M"]) == []


# ---- the draft --------------------------------------------------------------------------------------------
def _catalog(tmp_path, products):
    """products: (id, code, sizes, status)."""
    rows = []
    for pid, code, sizes, status in products:
        pid = str(pid)
        for i, s in enumerate(sizes):
            rows.append(base._variant(pid, f"Test Pant {pid} in Black", "MEN CLOTHING PANTS TROUSERS", status, code, "BLACK",
                                      s, f"ZXFW26{pid}{i:02d}", "800.00", "800.00", "300.00", i == 0))
    return catm.load(base._write_catalog(tmp_path / "catalog.csv", rows))


def _docs(code, sizes):
    style, fabric, colour = code[:9], code[9:14], code.split()[1]
    q = sum(sizes.values())
    return [dict(doc="SS - 1", collection="MM MALE RTW AVP", gender="MEN", total_qty=q, net=q * 200.0,
                 currency="European Euro", label="MM", lines=[
        dict(line_no=1, style=style, fabric=fabric, colour=colour, product_class="PANTS", sizes=sizes, qty=q,
             price=200.0, line_value=q * 200.0)])]


def _draft(tmp_path, products, sizes, code=CODE, brand=None, review=False):
    rows, cells, checks, fills = draftm.build(_docs(code, sizes), brand or base.BRAND, _catalog(tmp_path, products),
                                              base._hist(), base._tags())
    mine = [r for r in rows if r[draftm.CODE] == code]
    got = ({r["Option2 Value"]: r["Order Qty"] for r in mine}, {r["Command"] for r in mine},
           {c["Check"]: c for c in checks})
    sizes = {(x["Value"], x["Source"], x["Colour"], x["Why"]) for x in cells
             if x["Code"] == code and x["Column"] == "Option2 Value"}
    return got + (sizes,) if review else got


PANTS = dict(base.BRAND, product_class_types=dict(base.BRAND["product_class_types"], PANTS="CLOTHING PANTS TROUSERS"))


def test_a_carryover_takes_its_live_size_names_and_says_so(tmp_path):
    got, cmd, checks, cells = _draft(tmp_path, [("1", CODE, ["IT 42", "IT 44"], "Active")], {"42": 2, "44": 1},
                                     review=True)
    assert cmd == {"MERGE"} and got == {"IT 42": 2, "IT 44": 1}   # no stray 42 or 44 beside the live sizes
    assert draftm.SIZE_NAMES_CHECK not in checks
    assert ("IT 42", "rule", "amber", "printed '42', named 'IT 42' as on the live product Test Pant 1 in Black") in cells


def test_a_new_colour_takes_its_live_siblings_size_names_and_says_so(tmp_path):
    sib = ("1", "S99WP0001P0001 900", ["IT 42", "IT 44", "IT 46"], "Active")
    got, cmd, _, cells = _draft(tmp_path, [sib], {"42": 2, "44": 1}, review=True)
    assert cmd == {"NEW"} and set(got) == {"IT 42", "IT 44", "IT 46"} and got["IT 42"] == 2   # 3 rows, not 5
    assert ("IT 42", "rule", "amber", "printed '42', named 'IT 42' as on live sibling Test Pant 1 in Black") in cells
    assert not any(c[1] == "OC" for c in cells)   # a renamed size is never shown green as read from the OC


def test_one_live_product_of_the_same_type_never_renames_a_size(tmp_path):
    """Review 2026-11-13: four new dresses were drafted IT 38/40/42 because the one live dress of their Type was."""
    other = ("1", "S98ZZ0001P0001 101", ["IT 38", "IT 40", "IT 42"], "Active")   # same Type, another style
    got, cmd, _, cells = _draft(tmp_path, [other], {"40": 1}, brand=PANTS, review=True)
    assert cmd == {"NEW"} and got == {"40": 1} and {c[1] for c in cells} == {"OC"}


@pytest.mark.parametrize("n,want", [(10, {"OS": 3}), (3, {"F": 3})])
def test_a_one_size_f_becomes_os_only_where_the_brands_live_items_clearly_say_so(tmp_path, n, want):
    others = [(10 + k, f"S98ZZ{k:04d}P0001 101", ["OS"], "Active") for k in range(n)]   # other styles, all OS
    got, _, _ = _draft(tmp_path, others, {"F": 3}, brand=PANTS)
    assert {s: q for s, q in got.items() if q} == want


def test_siblings_that_spell_sizes_two_ways_give_a_new_colour_one_name_each(tmp_path):
    """Backtest F/W 25: a new colour of a style whose live siblings name sizes 'IT 36..' and '36..' got 14 rows."""
    it = ("1", "S99WP0001P0002 470", ["IT 36", "IT 38", "IT 40"], "Active")
    bare = ("2", "S99WP0001P0003 965", ["36", "38", "40"], "Active")
    got, cmd, checks = _draft(tmp_path, [it, bare], {"40": 1})
    assert cmd == {"NEW"} and got == {"36": 0, "38": 0, "40": 1} and draftm.SIZE_NAMES_CHECK not in checks


def test_two_live_names_for_one_size_are_warned(tmp_path):
    got, cmd, checks = _draft(tmp_path, [("1", CODE, ["42", "IT 42", "IT 44"], "Active")], {"42": 2})
    assert cmd == {"MERGE"} and got == {"42": 2, "IT 42": 0, "IT 44": 0}
    w = checks[draftm.SIZE_NAMES_CHECK]
    assert w["Result"] == "WARN" and f"{CODE}: 42 and IT 42" in w["Detail"]


def test_a_brand_size_map_applies_before_matching(tmp_path):
    brand = dict(base.BRAND, size_map={"31X34": "31"})
    got, cmd, _ = _draft(tmp_path, [("1", CODE, ["30", "31", "32"], "Active")], {"31X34": 2}, brand=brand)
    assert cmd == {"MERGE"} and got["31"] == 2 and "31X34" not in got


def _load(tmp_path, **extra):
    cfg = dict(vendor="ZX BRAND", adapter="otb", oc_label="ZX", season="S/S 99", sku_prefix="ZXSS99", sku_digits=3,
               cost_currency="EUR", cost_multiplier=1.5, margin_floor=2.0, **extra)
    p = tmp_path / "zx.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return brandm.load(str(p))


def test_size_map_is_text_or_numbers_checked_at_load(tmp_path):
    assert _load(tmp_path, size_map={"31x34": 31, "o/s": "OS"})["size_map"] == {"31X34": "31", "O/S": "OS"}
    assert _load(tmp_path)["size_map"] == {}
    for bad in (["31x34", "31"], {"31x34": ["31"]}, {"31x34": True}, {"31x34": " "}):
        with pytest.raises(brandm.BrandError, match="size_map is a list of 'printed size: store size' lines"):
            _load(tmp_path, size_map=bad)
