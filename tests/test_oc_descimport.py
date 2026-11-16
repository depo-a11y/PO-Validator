"""The separate description import finalize writes beside PU-new (oc/descimport.py; Colwyn, 2026-11-16, option B).
An invented brand, codes and titles; no real order data, no network."""
import json
import types

import openpyxl
import pandas as pd
import pytest

import test_oc_describe as td
import validator_core
from oc import catalog as catm, descimport, describe, finalize, history as histm, poxlsx, service

BRAND = dict(td.BRAND, vendor="ACME STUDIO")
FINAL = {td.LOAFER: "Mesa Loafer in Black", td.TOTE: "Drift Tote in Sand"}   # titles a reviewer fixed
FAMILY = {td.LOAFER: "BLACK", td.TOTE: "BEIGE"}


@pytest.fixture(autouse=True)
def acme_is_approved(monkeypatch):
    monkeypatch.setattr(validator_core, "APPROVED_VENDORS", list(validator_core.APPROVED_VENDORS) + ["ACME STUDIO"])


def reviewed(tmp_path, edit=None, brand=BRAND, d=None):
    """The tool's draft of the invented OC as written, then reviewed in its PO sheet: retail prices typed, the two
    raw-code titles and their colour families fixed. `edit(ws)` may change the Descriptions sheet.
    -> (path, the draft's rows)."""
    cat = catm.load(td.write_catalog(tmp_path))
    from oc import draft as draftm
    rows, review, checks, fills = draftm.build(d or td.docs(), brand, cat, histm.History([("F/W 26", [])]),
                                               td.base._tags(), cart=td.cart())
    path = tmp_path / "reviewed.xlsx"
    poxlsx.write_po(str(path), poxlsx.PO_HEADER, rows, review, checks, fills)
    wb = openpyxl.load_workbook(path)
    ws = wb.worksheets[0]
    col = {c.value: c.column for c in ws[1]}
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, col["Command"]).value == "NEW":
            ws.cell(r, col["Variant Price"]).value = 500.0
            code = ws.cell(r, col[poxlsx.CODE]).value
            if code in FINAL:
                ws.cell(r, col["Title"]).value, ws.cell(r, col["Option1 Value"]).value = FINAL[code], FAMILY[code]
    if edit:
        edit(wb[poxlsx.DESCRIPTIONS])
    wb.save(path)
    return str(path), rows


def run(tmp_path, path, brand=BRAND):
    cat = catm.load(td.write_catalog(tmp_path))
    return finalize.run(path, brand, cat, str(tmp_path / "run-"))


def imported(tmp_path):
    p = tmp_path / f"run-{descimport.FILE}"
    if not p.exists():
        return None
    wb = openpyxl.load_workbook(p)
    ws = wb.active
    return ws.title, [c.value for c in ws[1]], [[c.value for c in row] for row in ws.iter_rows(min_row=2)]


def check(checks):
    (c,) = [c for c in checks if c["Check"] == descimport.CHECK]
    return c


def desc_cell(ws, code):
    r = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 2).value == code)
    return ws.cell(r, 4)


# ---- the creation upload is untouched --------------------------------------------------------------------
def test_pu_new_keeps_the_template_body_and_finalize_writes_the_import_beside_it(tmp_path):
    path, rows = reviewed(tmp_path)
    checks = run(tmp_path, path)
    assert not [c for c in checks if c["Result"] == "FAIL"], [c for c in checks if c["Result"] == "FAIL"]
    pu = pd.read_excel(tmp_path / "run-PU-new.xlsx", sheet_name="Products")
    assert all(b.startswith("<p>Composition: ") for b in pu["Body HTML"])   # the template, never the description
    sheet, head, body = imported(tmp_path)
    assert sheet == descimport.SHEET == "Products" and head == ["Variant SKU", "Command", "Body HTML"]
    assert [r[1] for r in body] == ["UPDATE"] * len(body)
    first = {}
    for r in rows:
        first.setdefault(r[poxlsx.CODE], r["Variant SKU"])
    assert [r[0] for r in body] == [first[td.JACKET]]   # one row per product, its first variant's SKU
    assert body[0][2].startswith("<p>ACME STUDIO's Orbit Field Jacket in Slate is a ")
    c = check(checks)
    assert c["Result"] == "WARN" and c["Detail"].startswith("1 of 4 NEW products written to PU-description.xlsx")
    assert "Command UPDATE" in c["Detail"] and "Import it after the products are created" in c["Detail"]


def test_pu_new_and_the_rest_are_cell_for_cell_what_they_are_without_the_import(tmp_path, monkeypatch):
    path, _ = reviewed(tmp_path)
    run(tmp_path, path)
    with_import = {n: pd.read_excel(tmp_path / f"run-{n}", sheet_name=None) for n in
                   ("PU-new.xlsx", "PU-merge.xlsx", "Delivery.xlsx") if (tmp_path / f"run-{n}").exists()}
    monkeypatch.setattr(descimport, "build", lambda *a, **k: ([], []))
    (tmp_path / "b").mkdir()
    run(tmp_path / "b", path)
    for n, frames in with_import.items():
        other = pd.read_excel(tmp_path / "b" / f"run-{n}", sheet_name=None)
        assert frames.keys() == other.keys() and all(frames[k].equals(other[k]) for k in frames), n
    assert "PU-new.xlsx" in with_import


def test_the_import_is_in_the_zip_and_in_the_outputs(tmp_path, monkeypatch):
    assert descimport.FILE in finalize.OUTPUTS and descimport.FILE in service.ZIP_MEMBERS
    path, _ = reviewed(tmp_path)
    import zipfile, io
    z, checks, _ = service.finalize(path, td.write_catalog(tmp_path), BRAND, when=service.now())
    assert z is not None, [c for c in checks if c["Result"] == "FAIL"]
    assert any(n.endswith(descimport.FILE) for n in zipfile.ZipFile(io.BytesIO(z)).namelist())


# ---- one edit for a title, and the gates ---------------------------------------------------------------
def test_a_title_fixed_in_review_is_swapped_into_the_description(tmp_path):
    def fix(ws):   # the loafer's description given a Composition line, its drafted title left as the draft wrote it
        c = desc_cell(ws, td.LOAFER)
        c.value = c.value.replace("<ul>\n", "<ul>\n<li>Composition: Upper: 100% Calf Leather</li>\n")
    path, _ = reviewed(tmp_path, fix)
    run(tmp_path, path)
    bodies = {r[0]: r[2] for r in imported(tmp_path)[2]}
    loafer = next(b for b in bodies.values() if "Loafer" in b)   # drafted with a raw colour code in its title
    assert "ACME STUDIO's Mesa Loafer in Black is a pair of loafers." in loafer and "Loafer in 900" not in loafer


def test_an_edited_description_that_kept_the_raw_code_is_held_back(tmp_path):
    def edit(ws):   # the drafted title edited away, but the colour code typed back in
        c = desc_cell(ws, td.LOAFER)
        c.value = (c.value.replace("Loafer in 900 is a pair of loafers.",
                                   "Mesa Loafer in Black is a pair of loafers, colour 900.")
                   .replace("<ul>\n", "<ul>\n<li>Composition: Upper: 100% Calf Leather</li>\n"))
    path, _ = reviewed(tmp_path, edit)
    checks = run(tmp_path, path)
    assert not any("Loafer" in r[2] for r in imported(tmp_path)[2])
    c = check(checks)
    assert c["Result"] == "WARN" and f"{td.LOAFER} 'Mesa Loafer in Black': its intro still holds a raw code" in c["Detail"]


def test_an_edited_description_is_kept_and_must_hold_the_final_title(tmp_path):
    def edit(ws):
        for code, title in ((td.LOAFER, "Mesa Loafer in Black"), (td.TOTE, "a tote")):
            ws.cell(desc_cell(ws, code).row, 4).value = (
                f"<p>ACME STUDIO's {title} is a classic piece, written by hand.</p>\n<ul>\n"
                "<li>Composition: 100% Leather</li>\n<li>Made in Italy</li>\n</ul><br><p>SKU: " + code + "</p>")
    path, _ = reviewed(tmp_path, edit)
    checks = run(tmp_path, path)
    bodies = [r[2] for r in imported(tmp_path)[2]]
    assert any("Mesa Loafer in Black is a classic piece, written by hand." in b for b in bodies)   # kept as typed
    c = check(checks)
    assert f"{td.TOTE} 'Drift Tote in Sand': its intro does not hold the final Title" in c["Detail"]
    assert not any("written by hand" in b and "a tote" in b for b in bodies)


@pytest.mark.parametrize("change,why", [
    (lambda b: b.replace("<p>ACME STUDIO's ", "<p>"), 'does not open with the intro "ACME STUDIO\'s ..."'),
    (lambda b: b.split("<br><p>SKU")[0], "its last line is not the SKU line"),
    (lambda b: b.replace("ZQ0001AB1P0001 101</p>", "ZQ9999ZZ9P9999 999</p>"), "its SKU line is not this product's code"),
    (lambda b: b.replace("<li>Composition: Shell: 100% Cotton &amp; Linen - Lining: 100% Cupro</li>",
                         "<li>Composition: -</li>"), "its Composition line is a placeholder"),
    (lambda b: b.replace("<li>Composition: Shell: 100% Cotton &amp; Linen - Lining: 100% Cupro</li>\n", ""),
     "it has no Composition line"),
    (lambda b: b.replace("<li>Made in Italy</li>", "<li>Made in -</li>"), "it says 'Made in -'"),
    (lambda b: b.replace("  <li>Zip fastening</li>", "  <li>Zip fastening for women</li>"),
     "it names a gender word outside the title"),
    (lambda b: b.replace("  <li>Zip fastening</li>", "  <li>Zip fastening for winter</li>"),
     "it names a season word outside the title"),
    (lambda b: b.replace("lightweight jacket.", "lightweight jacket. This sweater has a stand collar."),
     describe.ITEM_MISMATCH),
])
def test_a_description_that_fails_a_gate_is_held_back_and_named(tmp_path, change, why):
    def edit(ws):
        c = desc_cell(ws, td.JACKET)
        c.value = change(c.value)
    path, _ = reviewed(tmp_path, edit)
    checks = run(tmp_path, path)
    assert not [c for c in checks if c["Result"] == "FAIL"]   # never stops the upload files
    assert (tmp_path / "run-PU-new.xlsx").exists() and imported(tmp_path) is None   # the jacket was the only one
    c = check(checks)
    assert c["Result"] == "WARN" and c["Detail"].startswith("0 of 4 NEW products written")
    assert f"{td.JACKET} 'Orbit Field Jacket in Slate': " in c["Detail"] and why in c["Detail"]


def test_a_red_description_is_held_back(tmp_path):
    checks = run(tmp_path, reviewed(tmp_path)[0])
    c = check(checks)
    for code in (td.LOAFER, td.TOTE, td.SHIRT):
        assert f"{code} '" in c["Detail"].split("held back", 1)[1]
    assert "it has no Composition line" in c["Detail"]


def test_a_title_naming_another_kind_than_the_final_type_is_held_back():
    body = ("<p>ACME STUDIO's Loop Cap in Black is a T-shirt.</p>\n<ul>\n<li>Composition: 100% Cotton</li>\n</ul><br>"
            "<p>SKU: ZQ0005AB5P0005 900</p>")
    why = descimport.problems(body, "Loop Cap in Black", "ZQ0005AB5P0005 900", "ACME STUDIO", "MEN CLOTHING T-SHIRTS")
    assert why == [describe.TITLE_MISMATCH]
    assert descimport.problems(body.replace("T-shirt", "cap"), "Loop Cap in Black", "ZQ0005AB5P0005 900", "ACME STUDIO",
                               "MEN ACCS HATS CAPS") == []


def test_a_po_without_a_descriptions_sheet_writes_no_import(tmp_path):
    path, _ = reviewed(tmp_path)
    wb = openpyxl.load_workbook(path)
    del wb[poxlsx.DESCRIPTIONS]
    wb.save(path)
    checks = run(tmp_path, path)
    assert imported(tmp_path) is None and check(checks)["Result"] == "INFO"
    assert (tmp_path / "run-PU-new.xlsx").exists()


def test_a_held_config_writes_no_description_import(tmp_path):
    path, rows = reviewed(tmp_path)
    held = dict(BRAND, hold="waiting on the supplier")
    assert descimport.build(path, [r for r in rows if r["Command"] == "NEW"], held, str(tmp_path / "x-")) == ([], [])
    checks = run(tmp_path, path, held)
    assert imported(tmp_path) is None and checks[0]["Result"] == "FAIL"


def test_a_stale_import_is_removed_when_finalize_refuses(tmp_path):
    path, _ = reviewed(tmp_path)
    run(tmp_path, path)
    assert imported(tmp_path) is not None
    wb = openpyxl.load_workbook(path)
    ws = wb.worksheets[0]
    hdr = [c.value for c in ws[1]]
    for r in range(2, ws.max_row + 1):
        ws.cell(r, hdr.index("Variant Price") + 1).value = None
    wb.save(path)
    checks = run(tmp_path, path)
    assert [c for c in checks if c["Result"] == "FAIL"] and imported(tmp_path) is None


# ---- the page and the CLI -----------------------------------------------------------------------------
def test_the_page_names_the_description_import_and_its_order():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "pages" / "1_Order_confirmation_to_PO.py").read_text()
    step2 = src[src.index('st.info("Nothing has been imported.'):src.index("checks_table(f[\"checks\"])")]
    assert "PU-description" in step2 and "import after the products are created" in step2   # under the zip itself


def test_the_cli_finalize_names_the_import_and_its_order(tmp_path, capsys):
    import yaml
    from oc import cli
    path, _ = reviewed(tmp_path)
    cfg = tmp_path / "b.yaml"
    cfg.write_text(yaml.safe_dump({k: v for k, v in BRAND.items() if not k.startswith("_")}))
    cli.main(["finalize", "--po", path, "--catalog", td.write_catalog(tmp_path), "--brand", "x", "--brand-config",
              str(cfg), "--out-prefix", str(tmp_path / "cli-")])
    out = capsys.readouterr().out
    assert f"cli-{descimport.FILE}" in out and "import after the products are created" in out
    assert (tmp_path / f"cli-{descimport.FILE}").exists()


def test_a_product_of_several_sizes_is_one_import_row_on_its_first_sku(tmp_path):
    d = td.docs()
    jacket = d[0]["lines"][0]
    jacket.update(sizes={"M": 1, "L": 1}, qty=2, line_value=200.0)
    d[0]["total_qty"], d[0]["net"] = 5, 500.0
    path, rows = reviewed(tmp_path, d=d)
    run(tmp_path, path)
    skus = [r["Variant SKU"] for r in rows if r[poxlsx.CODE] == td.JACKET]
    assert len(skus) == 2 and [r[0] for r in imported(tmp_path)[2]] == skus[:1]
