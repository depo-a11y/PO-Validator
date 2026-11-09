"""oc/service.py (the web page's engine) and brand configs without a file. Synthetic data only."""
import csv
import datetime
import io
import os
import types
import zipfile

import openpyxl
import pytest
import yaml

import test_oc_tool as base
from oc import brand as brandm, catalog as catm, finalize as finalizem, poxlsx, service
from oc.adapters import otb

WHEN = datetime.datetime(2026, 11, 9, 14, 32)
PREFIX = "2026-11-09-1432-mm-ss27-"
RUN = PREFIX.rstrip("-")


def _cfg(**kw):
    return dict(base.BRAND, _name="mm", _path="test config", **kw)


def _catalog(tmp_path, extra=()):
    rows = []
    for i, size in enumerate(["40", "41", "42"]):
        rows.append(base._variant("1", "Replica Sneaker in White", "MEN SHOES SNEAKERS", "Active", "S99WS0001P0001 101",
                                  "WHITE", size, f"MMFW26{100 + i}", "800.00", "800.00", "310.00", i == 0))
    rows += list(extra)
    p = tmp_path / "catalog.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=base.CAT_COLS)
        w.writeheader()
        w.writerows(rows)
    return str(p)


@pytest.fixture
def oc_stub(monkeypatch):
    """The OTB PDF parser replaced by synthetic docs (a real OC PDF is never committed)."""
    state = {"docs": base._docs(), "error": None}

    def parse_checked(path):
        if state["error"]:
            raise state["error"]
        return state["docs"]

    monkeypatch.setattr(service, "adapter", lambda cfg: types.SimpleNamespace(parse_checked=parse_checked))
    return state


def _past_po(path, season, code="S11ZZ0001P9 1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([poxlsx.CODE, poxlsx.SEASON, "Title", "Type", "Option2 Value", "Wholesale Price", "Variant Price"])
    ws.append([code, season, "Old Sneaker in Black", "MEN SHOES SNEAKERS", "41", 200.0, 900.0])
    wb.save(path)
    return str(path)


# ---- draft ---------------------------------------------------------------------
def test_draft_returns_a_shaded_workbook_that_opens(tmp_path, oc_stub):
    po, review, checks = service.draft("oc.pdf", _catalog(tmp_path), _cfg())
    wb = openpyxl.load_workbook(io.BytesIO(po))
    assert wb.sheetnames == ["Sheet1", "Review", "Checks"]
    ws = wb["Sheet1"]
    assert [c.value for c in ws[1]][:len(poxlsx.PO_HEADER)] == poxlsx.PO_HEADER
    assert sum(ws.cell(r, poxlsx.PO_HEADER.index("Order Qty") + 1).value for r in range(2, ws.max_row + 1)) == 6
    fills = {ws.cell(r, c).fill.fgColor.rgb for r in range(2, ws.max_row + 1) for c in range(1, ws.max_column + 1)}
    assert {"00FFC7CE", "00C6EFCE"} <= fills  # red guesses and green facts survive the round trip
    assert set(review["Colour"]) >= {"red", "green"} and len(review) == wb["Review"].max_row - 1
    first = [c.value for c in wb["Checks"][2]]
    assert first[:2] == [service.HISTORY_CHECK, "WARN"] and service.RED_WARNING in first[2]
    assert not service.fails(checks)


def test_draft_withholds_the_workbook_on_a_fail_the_sheet_cannot_fix(tmp_path, oc_stub):
    oc_stub["docs"][0]["total_qty"] = 7  # printed total no longer matches the lines
    po, review, checks = service.draft("oc.pdf", _catalog(tmp_path), _cfg())
    assert po is None
    assert [c["Check"] for c in service.blocking(checks)] == ["OC totals reproduced (parsed = printed)"]


def test_draft_keeps_the_workbook_on_a_fail_the_reviewer_fixes(tmp_path, oc_stub):
    # The draft names the 900 colourway "Replica Sneaker in 900"; a live product already holds that handle.
    taken = base._variant("9", "Replica Sneaker in 900", "MEN SHOES SNEAKERS", "Active", "S00XX0001P0001 1",
                          "BLACK", "41", "MMFW26900", "800.00", "800.00", "310.00", True)
    po, review, checks = service.draft("oc.pdf", _catalog(tmp_path, [taken]), _cfg())
    assert [c["Check"] for c in service.fails(checks)] == ["NEW title does not collide with a live handle"]
    assert service.blocking(checks) == [] and po is not None


def test_draft_turns_an_oc_gate_error_into_fail_checks(tmp_path, oc_stub):
    oc_stub["error"] = otb.GateError(["SS - 1 line 1: sizes sum 4 != Tot. Qty 3", "SS - 1: no printed totals"])
    po, review, checks = service.draft("oc.pdf", _catalog(tmp_path), _cfg())
    assert po is None and review.empty
    first, totals = oc_stub["error"].problems
    assert [c["Detail"] for c in service.fails(checks)] == [first, f"{totals}. {service.AI_HINT}"]


def test_draft_reports_a_catalog_missing_columns(tmp_path, oc_stub):
    bad = tmp_path / "bad.csv"
    bad.write_text("ID,Title\n1,x\n", encoding="utf-8-sig")
    po, _, checks = service.draft("oc.pdf", str(bad), _cfg())
    assert po is None and "missing columns" in service.fails(checks)[0]["Detail"]


def test_draft_reports_an_unreadable_linesheet(tmp_path, oc_stub, monkeypatch):
    cartm = pytest.importorskip("oc.cart")

    def refuse(path):
        raise cartm.CartError("no Model column: is this the cart export?")

    monkeypatch.setattr(cartm, "load", refuse)
    po, _, checks = service.draft("oc.pdf", _catalog(tmp_path), _cfg(), cart_path=str(tmp_path / "cart.xlsx"))
    assert po is None
    assert [(c["Check"], c["Detail"]) for c in service.fails(checks)] == [
        ("Linesheet/cart export can be read", "no Model column: is this the cart export?")]


# ---- past season POs ------------------------------------------------------------
def test_missing_config_history_is_a_visible_warning_not_a_crash(tmp_path, oc_stub):
    cfg = _cfg(history=[{"label": "F/W 26", "path": str(tmp_path / "gone.xlsx")}])
    assert service.history_status(cfg) == ([], cfg["history"])
    po, _, checks = service.draft("oc.pdf", _catalog(tmp_path), cfg)
    h = checks[0]
    assert po is not None and h["Check"] == service.HISTORY_CHECK and h["Result"] == "WARN"
    assert "1 of 1" in h["Detail"] and "F/W 26" in h["Detail"] and service.RED_WARNING in h["Detail"]


def test_uploaded_past_pos_replace_the_config_list_newest_first(tmp_path, oc_stub):
    up = tmp_path / "up"
    up.mkdir()
    paths = [_past_po(up / "a.xlsx", "S/S 26"), _past_po(up / "b.xlsx", "F/W 26"),
             _past_po(up / "Brand_SS25_PO.xlsx", None), _past_po(up / "Kept_Name.xlsx", None)]
    cfg = _cfg(history=[{"label": "F/W 24", "path": "/nowhere/Kept_Name.xlsx"}])
    items, check = service.plan_history(cfg, paths)
    assert [h["label"] for h in items] == ["F/W 26", "S/S 26", "S/S 25", "F/W 24"]
    assert check["Result"] == "INFO" and "4 uploaded" in check["Detail"]
    po, _, checks = service.draft("oc.pdf", _catalog(tmp_path), cfg, history_paths=paths)
    assert checks[0]["Result"] == "INFO" and "F/W 26 (1 rows)" in checks[0]["Detail"]


def test_an_upload_with_no_season_is_read_last_and_flagged(tmp_path):
    p = _past_po(tmp_path / "mystery.xlsx", None)
    items, check = service.plan_history(_cfg(), [p, _past_po(tmp_path / "x.xlsx", "S/S 25")])
    assert [h["label"] for h in items] == ["S/S 25", "mystery"]
    assert check["Result"] == "WARN" and "mystery.xlsx" in check["Detail"]


# ---- finalize ------------------------------------------------------------------
def _full_po(tmp_path, new_title="Replica Sneaker in Black"):
    """One NEW product (two sizes) and one carryover with an existing size and a new size."""
    new_po = base._finalize_po(tmp_path, new_title)
    _, new_rows = poxlsx.read_po(new_po)
    merge = {"Vendor": "MAISON MARGIELA", "Status": "Active", "Option1 Name": "Colour", "Option2 Name": "Size",
             poxlsx.SEASON: "S/S 27", poxlsx.NEW_SALE: "NEW", "Wholesale Price": 200.0, "Variant Cost": 300.0,
             poxlsx.COST_PRICE: "EUR 200", "Command": "MERGE", "Title": "Replica Sneaker in White",
             "Type": "MEN SHOES SNEAKERS", "Published": True, "Option1 Value": "WHITE", "Variant Price": 800.0,
             poxlsx.CODE: "S99WS0001P0001 101", poxlsx.MADE_IN: "Italy", "Order Qty": 1, "Total Order": 200.0}
    rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in new_rows]
    rows += [dict(merge, **{"Option2 Value": "41", "Variant SKU": "MMFW26101"}),
             dict(merge, **{"Option2 Value": "43", "Variant SKU": "MMSS27101"})]
    po = tmp_path / "full_po.xlsx"
    base._hand_po(po, rows)
    return str(po)


def test_finalize_zips_every_file_with_dated_names(tmp_path):
    z, checks, errors = service.finalize(_full_po(tmp_path), _catalog(tmp_path), _cfg(), when=WHEN)
    assert not service.fails(checks) and errors.empty
    zf = zipfile.ZipFile(io.BytesIO(z))
    assert zf.namelist() == [PREFIX + n for n in ("PU-new.xlsx", "PU-merge.xlsx", "PU-merge-new-sizes.xlsx",
                                                   "PU-merge-ROLLBACK.xlsx", "Delivery.xlsx", "finalize-checks.xlsx")]
    pu_new = openpyxl.load_workbook(io.BytesIO(zf.read(PREFIX + "PU-new.xlsx")))["Products"]
    assert pu_new.max_row == 3
    sizes = openpyxl.load_workbook(io.BytesIO(zf.read(PREFIX + "PU-merge-new-sizes.xlsx")))["Products"]
    row = dict(zip([c.value for c in sizes[1]], [c.value for c in sizes[2]]))
    assert sizes.max_row == 2 and row["Option2 Value"] == "43" and row["Variant SKU"] == "MMSS27101"
    ck = openpyxl.load_workbook(io.BytesIO(zf.read(PREFIX + "finalize-checks.xlsx")))["Checks"]
    details = [str(r[2].value) for r in ck.iter_rows(min_row=2)]
    assert any(d.startswith(PREFIX + "PU-new.xlsx: ") for d in details)
    assert not any("oc-finalize-" in d for d in details)  # no temporary folder leaks into the file
    scope = next(c for c in checks if c["Check"] == "Published = FALSE has no Published Scope")
    assert scope["Result"] == "WARN"
    assert ("after import they may be on Point of Sale, Shop, Microsoft Copilot and Meta AI and Muse. No sheet column "
            "sets Copilot or Meta AI") in scope["Detail"]


def test_finalize_gives_no_zip_and_the_fails_when_a_gate_fails(tmp_path):
    z, checks, _ = service.finalize(_full_po(tmp_path, "Replica Sneaker in 900"), _catalog(tmp_path), _cfg(), when=WHEN)
    assert z is None
    names = [c["Check"] for c in service.fails(checks)]
    assert "No NEW Title still holds a raw style/fabric/colour code" in names
    assert "All or nothing: no upload or delivery file written" in names


def test_finalize_refuses_even_if_files_were_left_behind(tmp_path, monkeypatch):
    def leaky_run(po_path, brand, catalog, out_prefix):
        for name in finalizem.OUTPUTS:
            open(out_prefix + name, "wb").write(b"x")
        return [dict(Check="A gate", Result="FAIL", Detail="broken")]

    monkeypatch.setattr(finalizem, "run", leaky_run)
    z, checks, _ = service.finalize(_full_po(tmp_path), _catalog(tmp_path), _cfg(), when=WHEN)
    assert z is None and service.fails(checks) == [dict(Check="A gate", Result="FAIL", Detail="broken")]


@pytest.mark.parametrize("col", [catm.SEASON, catm.AVAILABLE])
def test_finalize_gives_no_zip_for_a_catalog_without_the_season_or_available_column(tmp_path, col):
    rows = list(csv.DictReader(open(_catalog(tmp_path), encoding="utf-8-sig")))
    short = base._write_catalog(tmp_path / "short.csv", rows, [c for c in base.CAT_COLS if c != col])
    z, checks, _ = service.finalize(_full_po(tmp_path), short, _cfg(), when=WHEN)
    assert z is None and [c["Check"] for c in checks] == ["Catalog export has the columns the tool needs"]
    assert f"'{col}' (Matrixify Products export" in checks[0]["Detail"]
    assert 'with the preset "OC catalog: Margiela + MM6". If that preset is not there, ask Colwyn.' in checks[0]["Detail"]


def test_finalize_reports_an_unreadable_po_as_a_fail(tmp_path):
    bad = tmp_path / "po.xlsx"
    bad.write_bytes(b"not a workbook")
    z, checks, _ = service.finalize(str(bad), _catalog(tmp_path), _cfg(), when=WHEN)
    assert z is None and service.fails(checks)[0]["Check"] == "The PO can be read"


def test_file_prefix():
    assert service.file_prefix(_cfg(), WHEN) == PREFIX
    assert service.file_prefix(dict(base.BRAND), WHEN) == "2026-11-09-1432-maison-margiela-ss27-"
    assert service.zip_name(PREFIX) == RUN + "-uploads.zip"


def test_now_is_melbourne_time():
    assert str(service.now().tzinfo) == "Australia/Melbourne"


def test_two_runs_on_one_day_never_share_a_file_name(tmp_path):
    a, _, _ = service.finalize(_full_po(tmp_path), _catalog(tmp_path), _cfg(), when=WHEN)
    b, _, _ = service.finalize(_full_po(tmp_path), _catalog(tmp_path), _cfg(), when=WHEN + datetime.timedelta(minutes=1))
    na, nb = (zipfile.ZipFile(io.BytesIO(z)).namelist() for z in (a, b))
    assert len(na) == len(nb) == 6 and not set(na) & set(nb)
    assert all(n.startswith("2026-11-09-1433-mm-ss27-") for n in nb)


def test_one_run_id_on_every_file_of_the_zip_and_first_in_the_checks(tmp_path):
    z, checks, _ = service.finalize(_full_po(tmp_path), _catalog(tmp_path), _cfg(), when=WHEN)
    zf = zipfile.ZipFile(io.BytesIO(z))
    for name in zf.namelist():
        assert name.startswith(RUN + "-")
        assert openpyxl.load_workbook(io.BytesIO(zf.read(name))).properties.title == RUN, name
    ck = openpyxl.load_workbook(io.BytesIO(zf.read(PREFIX + "finalize-checks.xlsx")))["Checks"]
    first = [c.value for c in ck[2]]
    assert first[:2] == [finalizem.RUN_CHECK, "INFO"] and first[2].startswith(RUN + ". ")
    assert f"{RUN}-PU-merge.xlsx goes with {RUN}-PU-merge-ROLLBACK.xlsx" in first[2]
    assert checks[0] == dict(Check=finalizem.RUN_CHECK, Result="INFO", Detail=first[2])


# ---- brand config without a file -----------------------------------------------
REQ = dict(vendor="X", adapter="otb", season="S/S 27", sku_prefix="XX", cost_multiplier=1.5,
           cost_currency="EUR", margin_floor=2.5)


def _yaml(d):
    return "\n".join(f"{k}: {v}" for k, v in d.items())


@pytest.mark.parametrize("key", brandm.REQUIRED)
def test_brand_from_text_rejects_a_missing_required_key(key):
    with pytest.raises(brandm.BrandError, match=key):
        brandm.from_text(_yaml({k: v for k, v in REQ.items() if k != key}), "uploaded file x.yaml")


def test_brand_from_text_applies_defaults_and_names_its_source():
    cfg = brandm.from_text(_yaml(REQ) + "\nhistory:\n  - {label: F/W 26, path: ~/po.xlsx}", "app secrets", "x")
    assert cfg["status"] == "Active" and cfg["option1_name"] == "Colour" and cfg["_path"] == "app secrets"
    assert cfg["history"] == [] and cfg["history_files"] == [{"label": "F/W 26", "file": "po.xlsx"}]
    hist = brandm.from_text(_yaml(REQ), "s")["history"]
    assert hist == [] and hist is not brandm.DEFAULTS["history"]


@pytest.mark.parametrize("text", ["just a sentence", "- a\n- list", "vendor: [unclosed", ""])
def test_brand_from_text_rejects_what_is_not_a_config(text):
    with pytest.raises(brandm.BrandError):
        brandm.from_text(text, "uploaded file x.yaml")


def test_brand_from_mapping_takes_a_toml_table():
    table = types.MappingProxyType(dict(REQ, colour_families=types.MappingProxyType({"BLACK": ("black",)})))
    cfg = brandm.from_mapping(table, "app secrets", "x")
    assert cfg["colour_families"] == {"BLACK": ["black"]} and cfg["vendor"] == "X"
    with pytest.raises(brandm.BrandError, match="margin_floor"):
        brandm.from_mapping({k: v for k, v in REQ.items() if k != "margin_floor"}, "app secrets")


def test_local_configs_lists_the_brand_dir_and_skips_the_example(tmp_path, monkeypatch):
    (tmp_path / "aa.local.yaml").write_text(_yaml(REQ))
    (tmp_path / "bb.yaml").write_text(_yaml(REQ))
    (tmp_path / "example.yaml").write_text(_yaml(REQ))
    monkeypatch.setenv("OC_BRAND_DIR", str(tmp_path))
    found = brandm.local_configs()
    assert found["aa"] == tmp_path / "aa.local.yaml" and found["bb"] == tmp_path / "bb.yaml"
    assert "example" not in found
    assert brandm.load(str(found["aa"]))["_name"] == "aa"


# ---- history paths in a config from text are never opened -----------------------
def test_a_config_from_text_never_opens_its_history_paths(tmp_path, oc_stub, monkeypatch):
    server_file = _past_po(tmp_path / "server_file.xlsx", "F/W 26")  # stands in for any file on the host
    text = yaml.safe_dump(dict(base.BRAND, history=[{"label": "F/W 26", "path": server_file}]))
    cfg = brandm.from_text(text, "uploaded file x.yaml", "mm")
    touched = []
    real_open, real_exists = openpyxl.load_workbook, os.path.exists
    monkeypatch.setattr(openpyxl, "load_workbook", lambda p, *a, **k: (touched.append(str(p)), real_open(p, *a, **k))[1])
    monkeypatch.setattr(os.path, "exists", lambda p: (touched.append(str(p)), real_exists(p))[1])
    po, _, checks = service.draft("oc.pdf", _catalog(tmp_path), cfg)
    assert server_file not in touched and po is not None
    assert checks[0]["Result"] == "WARN" and "never opens files" in checks[0]["Detail"]
    assert cfg["history"] == [] and cfg["history_files"] == [{"label": "F/W 26", "file": "server_file.xlsx"}]
    upload = tmp_path / "up"
    upload.mkdir()
    same_name = _past_po(upload / "server_file.xlsx", None)  # uploaded on the page: labelled from the config
    assert service.plan_history(cfg, [same_name])[0] == [{"label": "F/W 26", "path": same_name}]


def test_a_config_file_still_reads_its_history_paths(tmp_path):
    past = _past_po(tmp_path / "past.xlsx", "F/W 26")
    f = tmp_path / "mm.local.yaml"
    f.write_text(yaml.safe_dump(dict(REQ, history=[{"label": "F/W 26", "path": past}])))
    cfg = brandm.load(str(f))
    assert cfg["history"] == [{"label": "F/W 26", "path": past}] and service.history_status(cfg) == (cfg["history"], [])


# ---- who sees the brands saved in the app --------------------------------------------
SAVED = {"mm": "vendor: X"}


def test_saved_brands_are_offered_to_a_viewer_on_the_allowlist():
    assert service.secret_brands_for(SAVED, [" Buyer@Example.com "], "buyer@example.COM") == (SAVED, None)
    assert service.secret_brands_for(SAVED, "buyer@example.com", "buyer@example.com") == (SAVED, None)


@pytest.mark.parametrize("viewers,email,why", [
    (["buyer@example.com"], "someone@example.com", "someone@example.com is not on the viewer allowlist"),
    (["buyer@example.com"], None, "you are not signed in"),
    (["buyer@example.com"], "", "you are not signed in"),
    ("buyer@example.com", "b", "b is not on the viewer allowlist"),  # a bare string is one address, not letters
    (None, "buyer@example.com", "no viewer allowlist (oc_viewers) is set in the app's secrets"),
    ([], "buyer@example.com", "no viewer allowlist (oc_viewers) is set in the app's secrets"),
    (["  "], "buyer@example.com", "no viewer allowlist (oc_viewers) is set in the app's secrets"),
])
def test_saved_brands_are_hidden_otherwise(viewers, email, why):
    assert service.secret_brands_for(SAVED, viewers, email) == ({}, why)


def test_no_saved_brands_needs_no_reason():
    assert service.secret_brands_for(None, None, None) == ({}, None)
