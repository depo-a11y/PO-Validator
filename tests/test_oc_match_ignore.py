"""match_ignore: a brand's separator characters left out of both codes when an OC code is matched to a live
product (catalog.match), for stores whose codes drop the OC's hyphens, underscores or spaces. Off unless set."""
import csv

import pytest

from oc import brand as brandm
from oc import catalog as catm
from oc import complete, poxlsx, reconcile, service
from oc.draft import build
from oc.poxlsx import CODE
from test_oc_tool import BRAND, CAT_COLS, _hist, _tags, _variant


@pytest.fixture
def cat(tmp_path):
    rows = []
    for pid, code, status in (("1", "ZZ0001 A1", "Active"), ("2", "ZZ-0002 B1", "Active"), ("3", "ZZ0002 B1", "Active"),
                              ("4", "ZZ0003 C1", "Archived"), ("5", "ZZ-0003 C1", "Active")):
        for i, size in enumerate(["S", "M"]):
            rows.append(_variant(pid, f"Test Tee {pid} in Black", "MEN CLOTHING T-SHIRTS SHORT SLEEVE", status, code,
                                 "BLACK", size, f"ZXFW26{pid}{i}", "300.00", "300.00", "87.37", i == 0))
    p = tmp_path / "catalog.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CAT_COLS)
        w.writeheader()
        w.writerows(rows)
    return catm.load(str(p))


def _docs(style, colour):
    return [dict(doc="D1", collection="TEST", gender="MEN", total_qty=1, net=10.0, currency="EUR", label=None,
                 lines=[dict(line_no=1, style=style, fabric=None, colour=colour, product_class="T-SHIRT",
                             sizes={"S": 1}, qty=1, price=10.0, line_value=10.0)])]


def test_without_the_key_a_code_matches_only_as_written(cat):
    assert cat.match("ZZ-0001 A1") == (None, "", [])
    assert cat.match("ZZ-0002 B1")[0]["id"] == "2"


def test_the_key_leaves_its_characters_out_of_both_sides_and_ignores_case(cat):
    assert cat.match("ZZ-0001 A1", "-")[0]["id"] == "1"
    assert cat.match("zz_0001_a1", "-_ ")[0]["id"] == "1"
    assert cat.match("ZZ-0001 A1", "_")[0] is None


def test_two_live_codes_that_become_equal_are_ambiguous_and_neither_is_picked(cat):
    p, note, cands = cat.match("ZZ-0002 B1", "-")
    assert p is None and sorted(c["id"] for c in cands) == ["2", "3"]
    assert note == ("AMBIGUOUS: the catalog codes ZZ-0002 B1 and ZZ0002 B1 are both ZZ-0002 B1 once '-' is left out; "
                    "fix the store's codes, nothing is picked")


def test_an_archived_code_that_becomes_equal_is_a_twin_not_a_rival(cat):
    p, note, _ = cat.match("ZZ0003 C1", "-")
    assert (p["id"], note) == ("5", "dropped 1 ARCHIVED twin(s)")


def test_a_draft_with_the_key_merges_on_the_live_code(cat):
    rows, review, checks, _ = build(_docs("ZZ-0001", "A1"), dict(BRAND, match_ignore="-"), cat, _hist(), _tags())
    assert {(r["Command"], r[CODE]) for r in rows} == {("MERGE", "ZZ0001 A1")}
    why = {(x["Source"], x["Why"]) for x in review if x["Column"] == CODE}
    assert why == {("catalog", "D1 line 1; the OC prints ZZ-0001 A1, this live code once '-' is left out")}


def test_a_draft_with_the_key_takes_the_live_products_size_names_through_the_size_map(cat):
    docs = _docs("ZZ-0001", "A1")
    docs[0]["lines"][0]["sizes"] = {"SMALL": 1}
    rows, _, _, _ = build(docs, dict(BRAND, match_ignore="-", size_map={"SMALL": "S"}), cat, _hist(), _tags())
    assert {(r["Command"], r[CODE], r["Option2 Value"], r["Order Qty"]) for r in rows} == {
        ("MERGE", "ZZ0001 A1", "S", 1), ("MERGE", "ZZ0001 A1", "M", 0)}


def test_a_match_ignore_draft_passes_its_checks_writes_a_workbook_and_completes(cat, tmp_path, monkeypatch):
    """Review 2026-11-13-buyer-15 F1: the MERGE row carries the live code, so every OC-vs-PO check compares the OC
    line under that code: the draft, service.draft (a workbook, nothing blocking), complete and reconcile."""
    brand = dict(BRAND, match_ignore="-")
    _, _, checks, _ = build(_docs("ZZ-0001", "A1"), brand, cat, _hist(), _tags())
    assert {c["Check"]: c["Result"] for c in checks}["Qty per code+size equals the OC"] == "PASS"
    monkeypatch.setattr(service, "read_oc", lambda *a, **k: (_docs("ZZ-0001", "A1"), [], None))
    po, _, checks = service.draft(str(tmp_path / "oc.pdf"), str(tmp_path / "catalog.csv"), brand)
    assert po is not None and service.blocking(checks) == []
    out = tmp_path / "po.xlsx"
    out.write_bytes(po)
    done = {c["Check"]: c["Result"] for c in complete.run(str(out), _docs("ZZ-0001", "A1"), brand, cat, _hist(),
                                                            _tags(), str(tmp_path / "done.xlsx"))}
    assert done["Qty per code+size equals the OC"] == "PASS" and done["Wholesale equals the OC on every row"] == "PASS"
    _, rows = poxlsx.read_po(str(out))
    res = reconcile.compare(rows, rows, _docs("ZZ-0001", "A1"), brand=brand)
    assert res["hand_qty_mismatch"] == [] and res["hand_price_diff"] == []


def test_a_draft_without_the_key_is_unchanged(cat):
    rows, _, _, _ = build(_docs("ZZ-0001", "A1"), BRAND, cat, _hist(), _tags())
    assert {(r["Command"], r[CODE]) for r in rows} == {("NEW", "ZZ-0001 A1")}


def test_a_draft_with_two_live_codes_for_one_line_fails_naming_both(cat):
    _, _, checks, _ = build(_docs("ZZ-0002", "B1"), dict(BRAND, match_ignore="-"), cat, _hist(), _tags())
    fail = [c["Detail"] for c in checks if c["Check"] == "Join key resolves to one live product"]
    assert fail and "ZZ-0002 B1 and ZZ0002 B1" in fail[0]
    assert all(c["Result"] == "FAIL" for c in checks if c["Check"] == "Join key resolves to one live product")


@pytest.mark.parametrize("value", ["A-", "-0", "", 5])
def test_the_key_takes_separators_only(value):
    with pytest.raises(brandm.BrandError, match="match_ignore is the separator characters"):
        brandm.from_mapping(dict(BRAND, match_ignore=value), "t")


def test_the_key_loads_with_separators():
    assert brandm.from_mapping(dict(BRAND, match_ignore="-_ "), "t")["match_ignore"] == "-_ "
