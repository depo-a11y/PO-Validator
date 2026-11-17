"""The season is picked per run, never read from a brand config, and new SKUs take a prefix per season half and
gender, marked when it was derived from live SKUs (register row oc-tool-season-picker-and-derived-prefix; Colwyn,
2026-11-16: "Colleague picks per run"; SKU prefixes derived from live, not his question). Synthetic data only."""
import copy
import csv
import io

import openpyxl
import pandas as pd
import pytest
import yaml
from streamlit.testing.v1 import AppTest

import test_oc_tool as base
from oc import brand as brandm, catalog as catm, cli, finalize, poxlsx, service
from oc.draft import build
from oc.resolve import COLOUR, Resolver

SPLIT = {"S/S": {"MEN": "ZQMS", "WOMEN": "ZQWS"}, "F/W": "ZQFW"}
FROM_SS = "derived from live, check: MEN ZQMS26, WOMEN ZQWS26 (synthetic)"
FROM_FW = "derived from live, check: ZQFW26 (synthetic)"
FROM = {"S/S": FROM_SS, "F/W": FROM_FW}
HAND = "from the S/S 27 hand PO, not live, check: its NEW SKUs use ZQMS27 and ZQWS27; no live S/S SKU (synthetic)"
NEW_CODE = "S99WS0001P0001 900"
WOMEN_CODE = "S99WS0002P0002 900"


def _brand(season="S/S 27", **kw):
    b = {k: v for k, v in base.BRAND.items() if k != "season"}
    b.update({"sku_prefix": copy.deepcopy(SPLIT), **kw})
    return brandm.with_season(b, season) if season else b


def _docs(women=False):
    d = base._docs()
    if women:
        d[0]["lines"].append(dict(line_no=3, style="S99WS0002", fabric="P0002", colour="900", product_class="SNEAKERS",
                                  sizes={"38": 1}, qty=1, price=200.0, line_value=200.0, gender="WOMEN"))
        d[0]["total_qty"], d[0]["net"] = 7, 1400.0
    return d


def _catalog(tmp_path, seasons=None, extra=()):
    rows = []
    for i, size in enumerate(["40", "41", "42"]):
        rows.append(base._variant("1", "Replica Sneaker in White", "MEN SHOES SNEAKERS", "Active", "S99WS0001P0001 101",
                                  "WHITE", size, f"ZXFW26{100 + i}", "800.00", "800.00", "310.00", i == 0))
    for r in extra:
        rows.append(r)
    if seasons:
        for r, s in zip([r for r in rows if r["Top Row"] == "true"], seasons):
            r[catm.SEASON] = s
    p = tmp_path / "catalog.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=base.CAT_COLS)
        w.writeheader()
        w.writerows(rows)
    return str(p)


def _archived(pid, season, sku):
    r = base._variant(pid, f"Old Boot {pid} in Black", "MEN SHOES BOOTS", "Archived", f"S99WS09{pid}P0009 900",
                      "BLACK", "41", sku, "700.00", "700.00", "300.00", True)
    r[catm.SEASON] = season
    return r


def _yaml(**kw):
    d = dict(vendor="X", adapter="otb", oc_label="MM", sku_prefix={"S/S": "XS", "F/W": "XF"}, cost_multiplier=1.5,
             cost_currency="EUR", margin_floor=2.5)
    d.update(kw)
    return yaml.safe_dump({k: v for k, v in d.items() if v is not None})


def _draft(tmp_path, brand, docs=None, cat=None):
    cat = catm.load(cat or _catalog(tmp_path))
    return build(docs or _docs(), brand, cat, base._hist(), base._tags())


def _sku(rows, code, size=None):
    return [r["Variant SKU"] for r in rows if r[poxlsx.CODE] == code and (size is None or r["Option2 Value"] == size)]


# ---- the brand config carries no season -----------------------------------------------------------------------
def test_a_config_loads_without_a_season_and_ignores_one_it_still_carries():
    assert "season" not in brandm.REQUIRED
    assert "season" not in brandm.from_text(_yaml(), "t")
    old = brandm.from_text(_yaml(season="S/S 27"), "t")   # an old config's season is read and dropped, never used
    assert "season" not in old


@pytest.mark.parametrize("season", ["S/S 27", "F/W 26"])
def test_the_season_of_a_run_is_set_on_a_copy_in_the_stores_spelling(season):
    cfg = brandm.from_text(_yaml(), "t")
    run = brandm.with_season(cfg, season)
    assert run["season"] == season and "season" not in cfg


@pytest.mark.parametrize("season", ["SS27", "S/S27", "s/s 27", "Spring 27", "A/W 26", "S/S 2027", "", None, " "])
def test_a_season_in_any_other_spelling_is_refused(season):
    with pytest.raises(brandm.BrandError, match="S/S 27"):
        brandm.with_season(brandm.from_text(_yaml(), "t"), season)


# ---- the SKU prefix: per season half, per gender where the store splits ---------------------------------------
def test_a_plain_sku_prefix_is_refused_because_it_carries_one_season():
    with pytest.raises(brandm.BrandError, match=r"S/S.*F/W.*season picked") as e:
        brandm.from_text(_yaml(sku_prefix="ZXSS27"), "t")
    assert "'ZXSS27'" in str(e.value)


@pytest.mark.parametrize("prefix", [{"S/S": "ZXFW"}, {"F/W": "ZXSS"}, {"F/W": {"MEN": "ZXFW", "WOMEN": "ZXWS"}}])
def test_a_half_whose_prefix_ends_in_the_other_halfs_letters_is_refused(prefix):
    with pytest.raises(brandm.BrandError, match="season letters"):
        brandm.from_text(_yaml(sku_prefix=prefix), "t")


@pytest.mark.parametrize("prefix", [{"S/S": "ZX-SS"}, {"S/S": "zxss"}, {"S/S": "ZX" * 6 + "SS"}, {"S/S": 27},
                                    {"S/S": ""}, {"S/S": None}, {}, {"Spring": "ZXSS"}, {"S/S": {}},
                                    {"S/S": {"MEN": "ZXMS", "KIDS": "ZXKS"}}, {"S/S": ["ZXSS"]}])
def test_a_malformed_sku_prefix_is_refused(prefix):
    with pytest.raises(brandm.BrandError, match="sku_prefix"):
        brandm.from_text(_yaml(sku_prefix=prefix), "t")


def test_a_gender_map_must_name_the_default_gender():
    with pytest.raises(brandm.BrandError, match="default_gender"):
        brandm.from_text(_yaml(sku_prefix={"S/S": {"WOMEN": "ZXWS"}}), "t")
    cfg = brandm.from_text(_yaml(sku_prefix={"S/S": {"WOMEN": "ZXWS"}}, default_gender="WOMEN"), "t")
    assert cfg["sku_prefix"] == {"S/S": {"WOMEN": "ZXWS"}}


def test_sku_prefix_from_marks_each_half_on_its_own():
    marks = {"S/S": HAND, "F/W": "derived from live, check: XF26 (synthetic)"}
    assert brandm.from_text(_yaml(sku_prefix_from=marks), "t")["sku_prefix_from"] == marks
    for bad in (FROM_SS, "", 3, ["x"], {}, {"S/S": "taken from the hand PO"}, {"S/S": ""}, {"S/S": 3},
                {"Spring": FROM_SS}):
        with pytest.raises(brandm.BrandError, match="sku_prefix_from"):
            brandm.from_text(_yaml(sku_prefix_from=bad), "t")
    with pytest.raises(brandm.BrandError, match="sku_prefix_from"):   # a mark for a half with no prefix
        brandm.from_text(_yaml(sku_prefix={"S/S": "XS"}, sku_prefix_from={"F/W": FROM_FW}), "t")


@pytest.mark.parametrize("season,gender,want", [("S/S 27", "MEN", "ZQMS27"), ("S/S 27", "WOMEN", "ZQWS27"),
                                                ("F/W 26", "MEN", "ZQFW26"), ("F/W 26", "WOMEN", "ZQFW26"),
                                                ("S/S 30", "UNISEX", "ZQMS30"), ("S/S 30", None, "ZQMS30")])
def test_the_prefix_follows_the_picked_season_and_the_gender(season, gender, want):
    prefix, why = brandm.sku_prefix(_brand(season), gender)
    assert prefix == want
    assert ("default_gender" in why) == (gender in ("UNISEX", None))


def test_a_half_with_no_prefix_gives_none_and_says_why():
    prefix, why = brandm.sku_prefix(_brand("F/W 27", sku_prefix={"S/S": "ZQSS"}), "MEN")
    assert prefix is None and "F/W" in why


# ---- the seasons offered: the store's own spellings, from live values -----------------------------------------
def test_the_seasons_offered_are_the_live_ones_and_the_two_after_the_newest_newest_first():
    live = ["F/W 26", "S/S 26", "F/W 26", "PSSS99", "REWARDSS98", "", None, "A/W 14", "S/S 25 ", "SS27"]
    assert catm.offered_seasons(live) == ["F/W 27", "S/S 27", "F/W 26", "S/S 26", "S/S 25"]
    assert catm.offered_seasons(["S/S 27", "F/W 25"]) == ["S/S 28", "F/W 27", "S/S 27", "F/W 25"]
    assert catm.offered_seasons(["PSSS99", ""]) == []


def test_the_seasons_a_catalog_offers_come_from_its_season_column(tmp_path):
    p = _catalog(tmp_path, extra=[_archived("7", "S/S 25", "ZXSS25700")])
    assert catm.load(p).offered_seasons() == ["F/W 27", "S/S 27", "F/W 26", "S/S 25"]
    with open(p, encoding="utf-8-sig") as fh:
        assert catm.offered_seasons(catm.live_seasons(fh.read())) == ["F/W 27", "S/S 27", "F/W 26", "S/S 25"]


# ---- the draft --------------------------------------------------------------------------------------------------
def test_the_draft_stamps_the_picked_season_on_new_and_rebought_rows(tmp_path):
    rows, review, checks, _ = _draft(tmp_path, _brand("F/W 27"))
    assert {r[poxlsx.SEASON] for r in rows} == {"F/W 27"} and {r["Command"] for r in rows} == {"NEW", "MERGE"}
    why = {(x["Code"].split()[-1], x["Why"]) for x in review if x["Column"] == poxlsx.SEASON}
    assert why == {("900", "the season picked for this run"),
                   ("101", "the season picked for this run: a re-bought product takes the buy season, as on the "
                           "hand-made POs")}


def test_a_config_season_never_reaches_the_draft(tmp_path):
    cfg = brandm.from_mapping(dict({k: v for k, v in base.BRAND.items() if not k.startswith("_")},
                                   sku_prefix=SPLIT, season="S/S 27"), "t")
    rows, _, _, _ = _draft(tmp_path, brandm.with_season(cfg, "F/W 27"))
    assert {r[poxlsx.SEASON] for r in rows} == {"F/W 27"}


def test_the_season_check_names_the_rebought_products_that_change_season(tmp_path):
    rows, _, checks, _ = _draft(tmp_path, _brand("S/S 27"))
    (c,) = [c for c in checks if c["Check"] == "Season of this order (picked for the run)"]
    assert c["Result"] == "INFO"
    new = sum(r["Command"] == "NEW" for r in rows)
    assert c["Detail"].startswith(f"S/S 27, picked for this run, on {new} NEW rows and 4 MERGE rows (1 re-bought "
                                  "product)")
    assert "F/W 26 (1)" in c["Detail"] and "move to S/S 27 when PU-merge is imported" in c["Detail"]


def test_a_season_older_than_a_rebought_products_live_one_is_a_warn(tmp_path):
    cat = _catalog(tmp_path, extra=[_archived("7", "S/S 26", "ZXSS26700")])
    _, _, checks, _ = _draft(tmp_path, _brand("S/S 26"), cat=cat)
    (c,) = [c for c in checks if c["Check"] == "Season of this order (picked for the run)"]
    assert c["Result"] == "WARN"
    assert "1 re-bought product is live in a later season than S/S 26 (F/W 26 (1)): check the season picked" in c["Detail"]


def _run(rows, prefix):
    """The drafted SKUs of one prefix, which must count 001, 002, ... in the draft."""
    got = sorted(r["Variant SKU"] for r in rows if r["Variant SKU"].startswith(prefix))
    assert got == [f"{prefix}{i:03d}" for i in range(1, len(got) + 1)], got
    return got


def test_new_skus_take_the_prefix_of_the_picked_season_and_the_products_gender(tmp_path):
    rows, _, _, _ = _draft(tmp_path, _brand("S/S 27"), docs=_docs(women=True))
    men = _run(rows, "ZQMS27")
    assert set(_sku(rows, NEW_CODE)) | set(_sku(rows, "S99WS0001P0001 101", "43")) == set(men) and len(men) >= 3
    assert _sku(rows, WOMEN_CODE) == _run(rows, "ZQWS27") == ["ZQWS27001"]
    rows, _, _, _ = _draft(tmp_path, _brand("F/W 27"), docs=_docs(women=True))
    assert set(_run(rows, "ZQFW27")) == set(_sku(rows, NEW_CODE) + _sku(rows, WOMEN_CODE)
                                           + _sku(rows, "S99WS0001P0001 101", "43"))


def test_the_default_gender_never_overrides_a_types_gender(tmp_path):
    """default_gender is only for a product whose gender has no prefix: a MEN Type keeps the MEN prefix."""
    rows, _, _, _ = _draft(tmp_path, _brand("S/S 27", default_gender="WOMEN"), docs=_docs(women=True))
    men = _run(rows, "ZQMS27")
    assert set(_sku(rows, NEW_CODE)) | set(_sku(rows, "S99WS0001P0001 101", "43")) == set(men)
    assert _sku(rows, WOMEN_CODE) == _run(rows, "ZQWS27") == ["ZQWS27001"]


def test_each_prefix_counts_on_from_its_own_highest_live_number(tmp_path):
    cat = catm.load(_catalog(tmp_path))
    cat.skus.update({"ZQWS27007", "ZQMS26050"})
    R = Resolver(_brand("S/S 27"), cat, base._hist(), base._tags())
    assert [R.next_sku(g)[0] for g in ("WOMEN", "MEN", "WOMEN", "MEN")] == ["ZQWS27008", "ZQMS27001", "ZQWS27009",
                                                                            "ZQMS27002"]


def test_a_gender_with_no_prefix_of_its_own_takes_the_default_genders_and_says_so(tmp_path):
    cat = catm.load(_catalog(tmp_path))
    sku, src, why = Resolver(_brand("S/S 27"), cat, base._hist(), base._tags()).next_sku("UNISEX")
    assert sku == "ZQMS27001" and "no UNISEX prefix, so the MEN one (default_gender)" in why


def test_a_derived_prefix_is_amber_and_marked_check_on_every_new_sku(tmp_path):
    rows, review, checks, fills = _draft(tmp_path, _brand("S/S 27", sku_prefix_from=FROM), docs=_docs(women=True))
    sku = [x for x in review if x["Column"] == "Variant SKU" and x["Value"].startswith("ZQ")]
    men = len(_run(rows, "ZQMS27"))
    assert len(sku) == men + 1 and {(x["Source"], x["Colour"]) for x in sku} == {("derived from live, check", "amber")}
    assert {x["Why"] for x in sku if x["Value"].startswith("ZQWS")} == {
        f"next free ZQWS27 number after the catalog's highest; the WOMEN S/S prefix: {FROM_SS}"}
    assert COLOUR[brandm.DERIVED] == "amber"
    (c,) = [c for c in checks if c["Check"] == "SKU prefix of new SKUs"]
    assert c["Result"] == "WARN" and c["Detail"] == (
        f"ZQMS27 (MEN, {men} SKUs), ZQWS27 (WOMEN, 1 SKU) for S/S 27: {FROM_SS}")


def test_a_half_kept_from_a_hand_po_is_never_marked_derived_from_live(tmp_path):
    """MM6's S/S prefix has no live S/S SKU behind it: its SKUs say where it came from, not "derived from live"."""
    brand = _brand("S/S 27", sku_prefix_from={"S/S": HAND, "F/W": FROM_FW})
    rows, review, checks, _ = _draft(tmp_path, brand, docs=_docs(women=True))
    sku = [x for x in review if x["Column"] == "Variant SKU" and x["Value"].startswith("ZQ")]
    assert {(x["Source"], x["Colour"]) for x in sku} == {("not live, check", "amber")}
    assert all(f"prefix: {HAND}" in x["Why"] and "derived" not in x["Why"] for x in sku)
    (c,) = [c for c in checks if c["Check"] == "SKU prefix of new SKUs"]
    assert c["Result"] == "WARN" and c["Detail"].endswith(f"for S/S 27: {HAND}") and "derived" not in c["Detail"]
    assert COLOUR[brandm.NOT_LIVE] == "amber"
    _, review, _, _ = _draft(tmp_path, dict(brand, season="F/W 27"))   # the other half keeps its own mark
    assert {x["Source"] for x in review if x["Column"] == "Variant SKU" and x["Value"].startswith("ZQFW")} == {
        "derived from live, check"}


def test_a_prefix_the_config_sets_by_hand_stays_a_rule(tmp_path):
    rows, review, checks, _ = _draft(tmp_path, _brand("S/S 27"))
    assert {x["Source"] for x in review if x["Column"] == "Variant SKU" and x["Value"].startswith("ZQ")} == {"rule"}
    (c,) = [c for c in checks if c["Check"] == "SKU prefix of new SKUs"]
    men = len(_run(rows, "ZQMS27"))
    assert c["Result"] == "INFO" and c["Detail"] == f"ZQMS27 (MEN, {men} SKUs) for S/S 27, set in the brand config"


# ---- service: no draft without a season the catalog offers and a prefix for its half ---------------------------
def _cfg(season="S/S 27", **kw):
    b = _brand(season, **kw)
    return dict(b, _name="mm", _path="test config")


@pytest.fixture
def oc_stub(monkeypatch):
    import types
    monkeypatch.setattr(service, "adapter",
                        lambda cfg: types.SimpleNamespace(parse_checked=lambda path: base._docs()))


def test_no_draft_without_a_season_picked(tmp_path, oc_stub):
    po, _, checks = service.draft("oc.pdf", _catalog(tmp_path), _cfg(season=None))
    assert po is None
    assert checks[-1] == dict(Check=service.SEASON_CHECK, Result="FAIL", Detail=(
        "No season is picked for this run. Pick the season of this order: the draft writes it on every row, "
        "re-bought products included, and PU-merge moves each re-bought product to it."))


def test_no_draft_for_a_season_the_catalog_does_not_offer(tmp_path, oc_stub):
    po, _, checks = service.draft("oc.pdf", _catalog(tmp_path), _cfg(season="S/S 30"))
    assert po is None
    assert checks[-1] == dict(Check=service.SEASON_CHECK, Result="FAIL", Detail=(
        "S/S 30 is not a season this catalog offers: F/W 27, S/S 27, F/W 26 (the seasons its products carry and the "
        "two after the newest). Pick one of them."))


def test_no_draft_when_the_config_has_no_prefix_for_the_seasons_half(tmp_path, oc_stub):
    po, _, checks = service.draft("oc.pdf", _catalog(tmp_path), _cfg(season="F/W 27", sku_prefix={"S/S": "ZQSS"}))
    assert po is None
    assert checks[-1] == dict(Check=service.PREFIX_CHECK, Result="FAIL", Detail=(
        "The brand config has no SKU prefix for F/W seasons (none could be derived from the brand's live SKUs), so "
        "new SKUs cannot be drafted for F/W 27. Ask Colwyn."))


def test_a_draft_with_a_season_the_catalog_offers_is_made(tmp_path, oc_stub):
    po, review, checks = service.draft("oc.pdf", _catalog(tmp_path), _cfg(season="F/W 27"))
    assert po is not None
    ws = openpyxl.load_workbook(io.BytesIO(po))["Sheet1"]
    col = poxlsx.PO_HEADER.index(poxlsx.SEASON) + 1
    assert {ws.cell(r, col).value for r in range(2, ws.max_row + 1)} == {"F/W 27"}
    assert "-mm-fw27-" in service.draft_file(_cfg(season="F/W 27"), checks)


# ---- finalize: the season comes from the PO, and a pick must agree with it ----------------------------------------
def _po(tmp_path, seasons=("S/S 27", "S/S 27")):
    rows = []
    for (s, sku), season in zip((("41", "ZXFW26101"), ("43", "ZXSS27001")), seasons):
        r = {"Command": "MERGE", "Vendor": "MAISON MARGIELA", "Status": "Active", "Title": "Replica Sneaker in White",
             "Type": "MEN SHOES SNEAKERS", "Published": True, "Option1 Name": "Colour", "Option1 Value": "WHITE",
             "Option2 Name": "Size", poxlsx.CODE: "S99WS0001P0001 101", poxlsx.SEASON: season, poxlsx.NEW_SALE: "CORE",
             poxlsx.MADE_IN: "Italy", "Variant Price": 800.0, "Variant Compare At Price": 800.0,
             "Wholesale Price": 200.0, "Variant Cost": 300.0, poxlsx.COST_PRICE: "EUR 200", "Order Qty": 1,
             "Total Order": 200.0, "Option2 Value": s, "Variant SKU": sku}
        rows.append(r)
    po = tmp_path / "po.xlsx"
    base._hand_po(po, rows)
    return str(po)


def _fin(tmp_path, po, brand):
    return finalize.run(po, brand, catm.load(_catalog(tmp_path)), str(tmp_path / "f-"))


def test_finalize_takes_the_season_from_the_po_when_none_is_picked(tmp_path):
    checks = _fin(tmp_path, _po(tmp_path), _brand(season=None))
    assert not [c for c in checks if c["Result"] == "FAIL"], checks
    m = pd.read_excel(tmp_path / "f-PU-merge.xlsx", dtype=str).fillna("")
    assert list(m[poxlsx.SEASON]) == ["S/S 27", "", ""] and list(m["Tags"]) == ["S/S 27", "", ""]


def test_finalize_refuses_a_po_with_two_seasons_or_a_blank_one(tmp_path):
    for seasons, said in ((("S/S 27", "F/W 26"), "F/W 26 (1 row), S/S 27 (1 row)"),
                          (("S/S 27", ""), "(blank) (1 row), S/S 27 (1 row)")):
        checks = _fin(tmp_path, _po(tmp_path, seasons), _brand(season=None))
        (c,) = [c for c in checks if c["Check"] == finalize.SEASON_CHECK]
        assert c["Result"] == "FAIL" and c["Detail"].startswith(f"the PO's season column holds {said}: ")
        assert not (tmp_path / "f-PU-merge.xlsx").exists()


def test_finalize_refuses_a_picked_season_the_po_does_not_carry(tmp_path):
    checks = _fin(tmp_path, _po(tmp_path), _brand(season="F/W 27"))
    (c,) = [c for c in checks if c["Check"] == finalize.SEASON_CHECK]
    assert c["Result"] == "FAIL" and c["Detail"] == (
        "the season picked is F/W 27 but every PO row says S/S 27: pick S/S 27, or upload the PO drafted for F/W 27")


def test_finalize_refuses_a_po_season_the_catalog_does_not_offer(tmp_path):
    checks = _fin(tmp_path, _po(tmp_path, ("SS27", "SS27")), _brand(season=None))
    (c,) = [c for c in checks if c["Check"] == finalize.SEASON_CHECK]
    assert c["Result"] == "FAIL" and c["Detail"] == (
        "the PO's season SS27 is not a season this catalog offers: F/W 27, S/S 27, F/W 26. Correct the season column")


def test_the_run_prefix_names_the_pos_season_when_none_is_picked(tmp_path):
    import datetime
    when = datetime.datetime(2026, 11, 9, 14, 32)
    assert service.run_prefix(_po(tmp_path), _cfg(season=None), when) == "2026-11-09-1432-mm-ss27-"


# ---- the command line --------------------------------------------------------------------------------------------
def _cli_cfg(tmp_path):
    p = tmp_path / "b.yaml"
    p.write_text(yaml.safe_dump(dict({k: v for k, v in base.BRAND.items() if not k.startswith("_") and k != "season"},
                                     sku_prefix=SPLIT)))
    return str(p)


def test_the_cli_draft_needs_a_season(tmp_path, oc_stub, capsys):
    args = ["draft", "--oc", "oc.pdf", "--catalog", _catalog(tmp_path), "--brand", "x", "--brand-config",
            _cli_cfg(tmp_path), "--out", str(tmp_path / "d.xlsx")]
    with pytest.raises(SystemExit) as e:
        cli.main(args)
    assert e.value.code == 2 and "--season" in capsys.readouterr().err
    with pytest.raises(SystemExit, match="S/S 30 is not a season this catalog offers"):
        cli.main(args + ["--season", "S/S 30"])
    cli.main(args + ["--season", "F/W 27"])
    ws = openpyxl.load_workbook(tmp_path / "d.xlsx")["Sheet1"]
    col = poxlsx.PO_HEADER.index(poxlsx.SEASON) + 1
    assert {ws.cell(r, col).value for r in range(2, ws.max_row + 1)} == {"F/W 27"}


# ---- the page ------------------------------------------------------------------------------------------------------
PAGE = base.os.path.join(base.os.path.dirname(base.os.path.dirname(base.os.path.abspath(base.__file__))), "pages",
                         "1_Order_confirmation_to_PO.py")
PAGE_YAML = ("vendor: X\nadapter: otb\noc_label: MM\ncost_multiplier: 1.5\ncost_currency: EUR\nmargin_floor: 2.5\n"
             "sku_prefix:\n  S/S:\n    MEN: ZQMS\n    WOMEN: ZQWS\n  F/W: ZQFW\n")


def _page(tmp_path, monkeypatch, extra="", catalog=True, text=PAGE_YAML, brands_made=False):
    d = tmp_path / "brands"
    if not brands_made:
        d.mkdir()
    (d / "aaa.local.yaml").write_text(text + extra)
    monkeypatch.setenv("OC_BRAND_DIR", str(d))
    at = AppTest.from_file(PAGE, default_timeout=60)
    if catalog:
        at.session_state["catalog"] = ("catalog.csv", open(_catalog(tmp_path, extra=[
            _archived("7", "S/S 25", "ZXSS25700")]), "rb").read())
    at.run()
    (box,) = [s for s in at.selectbox if s.label == "Brand"]
    box.select("aaa (on this computer)").run()
    assert not at.exception
    return at


def _season_box(at):
    (box,) = [s for s in at.selectbox if s.label == "Season of this order"]
    return box


def test_the_season_box_starts_empty_and_offers_the_catalogs_seasons(tmp_path, monkeypatch):
    at = _page(tmp_path, monkeypatch)
    box = _season_box(at)
    assert box.value is None and box.index is None and box.proto.placeholder == "Choose the season"
    assert box.options == ["F/W 27", "S/S 27", "F/W 26", "S/S 25"]
    assert any(c.value.startswith("To build the draft, add ") and "the season of this order" in c.value
               for c in at.caption)
    assert not any("season S/S" in c.value or "season F/W" in c.value for c in at.caption)   # no config season shown


def test_the_season_box_waits_for_the_catalog(tmp_path, monkeypatch):
    at = _page(tmp_path, monkeypatch, catalog=False)
    box = _season_box(at)
    assert box.disabled and box.options == []


def test_build_waits_for_a_season_and_the_draft_gets_the_one_picked(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(service, "draft", lambda oc, cat, cfg, **kw: seen.append(cfg.get("season")) or
                        (None, pd.DataFrame(), [dict(Check="A gate", Result="FAIL", Detail="x")]))
    at = _page(tmp_path, monkeypatch, extra=f"sku_prefix_from:\n  S/S: \"{FROM_SS}\"\n  F/W: \"{FROM_FW}\"\n")
    at.file_uploader(key="w_oc").set_value(("oc.pdf", b"%PDF-1.4", "application/pdf")).run()
    (build_btn,) = [b for b in at.button if b.label == "Build draft PO"]
    assert build_btn.disabled and any(c.value == "To build the draft, add the season of this order." for c in at.caption)
    _season_box(at).select("S/S 27").run()
    assert not at.exception
    assert any(w.value == f"New SKUs for S/S 27 start ZQMS27 (MEN), ZQWS27 (WOMEN): {FROM_SS}" for w in at.warning)
    (build_btn,) = [b for b in at.button if b.label == "Build draft PO"]
    assert not build_btn.disabled
    build_btn.click().run()
    assert not at.exception and seen == ["S/S 27"]


def test_a_season_whose_half_has_no_prefix_is_an_error_on_the_page(tmp_path, monkeypatch):
    at = _page(tmp_path, monkeypatch, text=PAGE_YAML.replace("  F/W: ZQFW\n", ""))
    _season_box(at).select("F/W 27").run()
    assert any(e.value.startswith("X has no SKU prefix for F/W seasons") for e in at.error)


# ---- a pick belongs to one brand, catalog and order (review 2026-11-16-buyer-9 F1) -----------------------------------
OTHER_CATALOG = ("other.csv", b"ID,Metafield: custom.product_season [single_line_text_field]\n1,F/W 26\n2,S/S 26\n")


def _picked(tmp_path, monkeypatch, extra_brand=None):
    """The page with a brand, the catalog, an OC and S/S 27 picked; the pick is kept by a rerun that changes nothing."""
    if extra_brand:
        d = tmp_path / "brands"
        d.mkdir()
        (d / "bbb.local.yaml").write_text(PAGE_YAML)
    at = _page(tmp_path, monkeypatch, brands_made=bool(extra_brand))
    at.file_uploader(key="w_oc").set_value(("oc.pdf", b"%PDF-1.4 one", "application/pdf")).run()
    _season_box(at).select("S/S 27").run()
    at.run()
    assert _season_box(at).value == "S/S 27" and not any("the season of this order" in c.value for c in at.caption)
    return at


def _cleared(at):
    assert not at.exception
    assert _season_box(at).value is None
    assert any(c.value.startswith("To build the draft, add ") and "the season of this order" in c.value
               for c in at.caption)


def test_switching_brand_clears_the_season(tmp_path, monkeypatch):
    at = _picked(tmp_path, monkeypatch, extra_brand=True)
    (box,) = [s for s in at.selectbox if s.label == "Brand"]
    box.select("bbb (on this computer)").run()
    _cleared(at)


def test_a_new_brand_file_clears_the_season(tmp_path, monkeypatch):
    at = _page(tmp_path, monkeypatch)
    (box,) = [s for s in at.selectbox if s.label == "Brand"]
    box.select("Upload a brand .yaml file").run()
    at.file_uploader(key="w_brand").set_value(("a.yaml", PAGE_YAML.encode(), "text/yaml")).run()
    at.file_uploader(key="w_oc").set_value(("oc.pdf", b"%PDF-1.4 one", "application/pdf")).run()
    _season_box(at).select("S/S 27").run()
    at.run()
    assert _season_box(at).value == "S/S 27"
    at.file_uploader(key="w_brand").set_value(("b.yaml", PAGE_YAML.replace("vendor: X", "vendor: Y").encode(),
                                               "text/yaml")).run()
    _cleared(at)


def test_a_new_catalog_clears_the_season(tmp_path, monkeypatch):
    at = _picked(tmp_path, monkeypatch)
    at.file_uploader(key="w_catalog").set_value(OTHER_CATALOG + ("text/csv",)).run()
    _cleared(at)


def test_a_new_order_confirmation_clears_the_season(tmp_path, monkeypatch):
    at = _picked(tmp_path, monkeypatch)
    at.file_uploader(key="w_oc").set_value(("oc.pdf", b"%PDF-1.4 two", "application/pdf")).run()
    _cleared(at)


def test_a_new_cart_clears_the_season(tmp_path, monkeypatch):
    at = _picked(tmp_path, monkeypatch)
    at.file_uploader(key="w_cart").set_value(("cart.xlsx", b"PK cart", "application/octet-stream")).run()
    _cleared(at)


# ---- the season the order confirmation prints --------------------------------------------------------------------
@pytest.mark.parametrize("text,want", [("SS - 2099/01-62-0001", "S/S 99"), ("FW - 2098/02-61-0001", "F/W 98"),
                                       ("FW99", "F/W 99"), ("PRE-SS99", "S/S 99"), ("2099 SS", "S/S 99"),
                                       ("AW25", "F/W 25"), ("S/S 26", "S/S 26"), ("SS - 1", None), ("3999001", None),
                                       ("cart ZXCART", None), (None, None), ("CLASS 27", None),
                                       ("fw99", "F/W 99"), ("Ss 99", "S/S 99"), ("pre-ss98", "S/S 98"),
                                       ("2099 fw", "F/W 99")])   # p3m passes its Season text through as printed
def test_the_season_an_order_confirmation_prints(text, want):
    from oc.draft import printed_season
    assert printed_season(text) == want


def _printed_docs(doc="FW - 2098/01-62-0001", season=None):
    d = base._docs()
    d[0]["doc"] = doc
    if season:
        d[0]["season"] = season
    return d


def test_a_pick_the_order_confirmation_disagrees_with_is_a_warn(tmp_path):
    _, _, checks, _ = _draft(tmp_path, _brand("S/S 27"), docs=_printed_docs())
    (c,) = [c for c in checks if c["Check"] == "Season printed on the order confirmation"]
    assert c["Result"] == "WARN" and c["Detail"] == (
        "the season picked is S/S 27, but FW - 2098/01-62-0001 prints F/W 98. Check the pick before importing: every "
        "row carries it, and PU-merge moves each re-bought product to it")
    _, _, checks, _ = _draft(tmp_path, _brand("F/W 98"), docs=_printed_docs())
    (c,) = [c for c in checks if c["Check"] == "Season printed on the order confirmation"]
    assert (c["Result"], c["Detail"]) == ("PASS", "F/W 98, as picked, on 1 confirmation")
    _, _, checks, _ = _draft(tmp_path, _brand("F/W 26"), docs=_printed_docs(doc="3999001", season="SS99"))
    (c,) = [c for c in checks if c["Check"] == "Season printed on the order confirmation"]
    assert c["Result"] == "WARN" and "3999001 prints S/S 99" in c["Detail"]   # the reader's own season field
    _, _, checks, _ = _draft(tmp_path, _brand("F/W 26"))   # "SS - 1" prints no season the tool can read: no row
    assert not [c for c in checks if c["Check"] == "Season printed on the order confirmation"]


def test_the_page_shows_a_printed_season_warn_beside_the_draft(tmp_path, monkeypatch):
    at = _page(tmp_path, monkeypatch)
    warn = dict(Check="Season printed on the order confirmation", Result="WARN", Detail="the season picked is S/S 27, "
                "but FW - 2098/01 prints F/W 98. Check the pick")
    at.session_state["draft"] = dict(po=None, review=pd.DataFrame(), file="x.xlsx", made_from="oc.pdf",
                                     checks=[warn, dict(Check="A gate", Result="FAIL", Detail="x")])
    at.run()
    assert any(w.value == "Season: " + warn["Detail"] for w in at.warning)


# ---- complete and the command line also give new SKUs their gender's prefix and check the season --------------------
def test_complete_gives_a_blank_women_sku_the_women_prefix(tmp_path):
    from oc.complete import run as complete_run
    row = {"Command": "NEW", "Title": "Replica Sneaker in Black", "Vendor": "MAISON MARGIELA", "Status": "Active",
           "Type": "WOMEN SHOES SNEAKERS", "Published": False, "Option1 Name": "Colour", "Option1 Value": "BLACK",
           "Option2 Name": "Size", "Option2 Value": "41", "Variant SKU": None, "Variant Price": 900.0,
           poxlsx.CODE: NEW_CODE, "Order Qty": 1, "Wholesale Price": 200.0, "Total Order": 200.0,
           "Variant Cost": 300.0, poxlsx.COST_PRICE: "EUR 200", poxlsx.MADE_IN: "Italy", poxlsx.SEASON: "S/S 27",
           poxlsx.NEW_SALE: "NEW", "Body HTML": "<p>x</p>", poxlsx.TITLE_TAG: "x"}
    hand = tmp_path / "hand.xlsx"
    base._hand_po(hand, [row, dict(row, **{"Option2 Value": "42", "Type": "MEN SHOES SNEAKERS"})])
    out = tmp_path / "done.xlsx"
    docs = base._docs()
    docs[0]["lines"] = [dict(line_no=1, style="S99WS0001", fabric="P0001", colour="900", product_class="SNEAKERS",
                             sizes={"41": 1, "42": 1}, qty=2, price=200.0, line_value=400.0)]
    docs[0]["total_qty"], docs[0]["net"] = 2, 400.0
    complete_run(str(hand), docs, _brand("S/S 27"), catm.load(_catalog(tmp_path)), base._hist(), base._tags(), str(out))
    ws = openpyxl.load_workbook(out).worksheets[0]
    hdr = [c.value for c in ws[1]]
    got = {ws.cell(r, hdr.index("Type") + 1).value: ws.cell(r, hdr.index("Variant SKU") + 1).value
           for r in range(2, ws.max_row + 1)}
    assert got == {"WOMEN SHOES SNEAKERS": "ZQWS27001", "MEN SHOES SNEAKERS": "ZQMS27001"}


def test_the_cli_complete_refuses_a_season_the_catalog_does_not_offer_or_a_half_with_no_prefix(tmp_path, oc_stub):
    args = ["complete", "--po", str(tmp_path / "no-po.xlsx"), "--oc", "oc.pdf", "--catalog", _catalog(tmp_path),
            "--brand", "x", "--brand-config", _cli_cfg(tmp_path), "--out", str(tmp_path / "c.xlsx")]
    with pytest.raises(SystemExit, match="S/S 30 is not a season this catalog offers"):
        cli.main(args + ["--season", "S/S 30"])
    cfg = tmp_path / "ss-only.yaml"
    cfg.write_text(yaml.safe_dump(dict(yaml.safe_load(open(_cli_cfg(tmp_path))), sku_prefix={"S/S": "ZQSS"})))
    args[args.index("--brand-config") + 1] = str(cfg)
    with pytest.raises(SystemExit, match="no SKU prefix for F/W seasons"):
        cli.main(args + ["--season", "F/W 27"])


# ---- the remaining guards, each with its own failure (review 2026-11-16-buyer-9 F3: every changed guard a mutant) ----
def test_a_one_prefix_half_is_named_on_the_page_as_the_config_sets_it(tmp_path, monkeypatch):
    at = _page(tmp_path, monkeypatch)
    _season_box(at).select("F/W 27").run()
    assert any(c.value == "New SKUs for F/W 27 start ZQFW27, as the brand config sets them." for c in at.caption)


@pytest.mark.parametrize("data", [b"ID,Title\n1,x\n", b"\xff\xfe\x00not utf-8"])
def test_a_catalog_with_no_season_to_offer_is_an_error_not_a_crash(tmp_path, monkeypatch, data):
    at = _page(tmp_path, monkeypatch, catalog=False)
    at.session_state["catalog"] = ("catalog.csv", data)
    at.run()
    assert not at.exception and _season_box(at).options == []
    assert any(e.value.startswith("This catalog export has no season in the store's spelling") for e in at.error)


def test_the_cli_refuses_a_season_spelt_another_way(tmp_path, oc_stub):
    with pytest.raises(SystemExit, match=r"--season: the season of a run is written as the store writes it"):
        cli.main(["draft", "--oc", "oc.pdf", "--catalog", _catalog(tmp_path), "--brand", "x", "--brand-config",
                  _cli_cfg(tmp_path), "--out", str(tmp_path / "d.xlsx"), "--season", "SS27"])


def test_a_rebought_product_already_in_the_picked_season_is_not_moving(tmp_path):
    _, _, checks, _ = _draft(tmp_path, _brand("F/W 26"))   # the live product is F/W 26
    (c,) = [c for c in checks if c["Check"] == "Season of this order (picked for the run)"]
    assert c["Result"] == "INFO" and "move to" not in c["Detail"] and "(1 re-bought product)" in c["Detail"]


def test_a_draft_with_no_new_sku_has_no_prefix_row(tmp_path):
    d = base._docs()
    d[0]["lines"] = [dict(d[0]["lines"][0], sizes={"41": 1, "42": 1}, qty=2, line_value=400.0)]
    d[0]["total_qty"], d[0]["net"] = 2, 400.0
    rows, _, checks, _ = _draft(tmp_path, _brand("S/S 27"), docs=d)
    assert {r["Command"] for r in rows} == {"MERGE"} and not [r for r in rows if r["Variant SKU"].startswith("ZQ")]
    assert not [c for c in checks if c["Check"] == "SKU prefix of new SKUs"]


def test_a_sku_for_a_half_with_no_prefix_is_never_made_up(tmp_path):
    R = Resolver(_brand("F/W 27", sku_prefix={"S/S": "ZQSS"}), catm.load(_catalog(tmp_path)), base._hist(), base._tags())
    with pytest.raises(ValueError, match="no SKU prefix for F/W seasons"):
        R.next_sku("MEN")


def test_an_unreadable_po_with_no_season_picked_is_a_fail_not_a_crash(tmp_path):
    bad = tmp_path / "po.xlsx"
    bad.write_bytes(b"not a workbook")
    z, checks, _ = service.finalize(str(bad), _catalog(tmp_path), _cfg(season=None), when=None)
    assert z is None and [c["Check"] for c in service.fails(checks)] == ["The PO can be read"]


# ---- the printed season comes only from the sources the README names (review 2026-11-17-buyer-1 F1) ------------------
def _rows_named(checks):
    return [c for c in checks if c["Check"] == "Season printed on the order confirmation"]


@pytest.mark.parametrize("session,name", [("FW98 DROP SS99", "c.xlsx"), (None, "order_FW98_SS99.xlsx")])
def test_a_cart_label_is_never_a_printed_season(tmp_path, session, name):
    """A cart-only draft's doc is "cart <session or file name>": a season-like word there is not the OC's season."""
    import test_oc_cartorder as tco
    from oc import cartorder
    path = tco.write(tmp_path / name)
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    for row in ws.iter_rows(max_row=4):
        if row[0].value == "Session Code":
            if session:
                row[1].value = session
            else:
                ws.delete_rows(row[0].row)
            break
    wb.save(path)
    docs, cart = cartorder.read(path)
    assert ("FW98" in docs[0]["doc"]) and docs[0]["doc"].startswith("cart ")
    _, _, checks, _ = build(docs, brandm.with_season(dict(tco.BRAND), "S/S 27"), catm.load(tco.catalog_csv(tmp_path)),
                            base._hist(), base._tags(), cart=cart)
    assert not _rows_named(checks)


def test_another_readers_document_number_is_never_a_printed_season(tmp_path):
    docs = _printed_docs(doc="SS - 2099/01-62-0001")
    assert not _rows_named(_draft(tmp_path, _brand("S/S 27", adapter="joor", oc_supplier="x.example"), docs=docs)[2])
    assert _rows_named(_draft(tmp_path, _brand("S/S 27"), docs=docs)[2])[0]["Result"] == "WARN"   # otb: read


def test_an_ai_read_document_number_is_never_a_printed_season(tmp_path):
    docs = _printed_docs()
    docs[0]["reader"] = "ai"
    for l in docs[0]["lines"]:
        l["reader"] = "ai"
    assert not _rows_named(_draft(tmp_path, _brand("S/S 27"), docs=docs)[2])


def test_an_otb_document_that_is_no_document_number_prints_no_season(tmp_path):
    for doc in ("PROFORMA FW98 1", "REORDER SS - 2099/01", "FW98"):
        assert not _rows_named(_draft(tmp_path, _brand("S/S 27"), docs=_printed_docs(doc=doc))[2]), doc


# ---- case is not meaning: a season or a Type typed in lower or mixed case (review 2026-11-17-buyer-3) ---------------
@pytest.mark.parametrize("season,want", [("s/s 98", (98, 0)), ("F/w 98", (98, 1)), ("a/w 98", (98, 1)),
                                         ("S/S 98", (98, 0)), ("", None), ("PSSS99", None)])
def test_a_live_season_sorts_in_any_case(season, want):
    """A product's custom.product_season is free text: a lower-case one still sorts against the season picked."""
    assert brandm.season_order(season) == want


@pytest.mark.parametrize("ptype,want", [("WOMEN SHOES SNEAKERS", "WOMEN"), ("women shoes sneakers", "WOMEN"),
                                        ("Men Clothing Shirts", "MEN"), ("unisex bags", "UNISEX"), ("", None),
                                        (None, None), ("KIDS SHOES", None)])
def test_a_types_gender_is_read_in_any_case(ptype, want):
    """complete reads a hand-typed Type: 'Women Shoes' takes the WOMEN prefix, not the default_gender's."""
    from oc.draft import sku_gender
    assert sku_gender(ptype) == want
