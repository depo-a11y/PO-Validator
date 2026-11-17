"""The separate description import finalize writes beside PU-new (oc/descimport.py; Colwyn, 2026-11-16, option B).
An invented brand, codes and titles; no real order data, no network."""
import json
import re
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


def reviewed(tmp_path, edit=None, brand=BRAND, d=None, cart=td.cart):
    """The tool's draft of the invented OC as written, then reviewed in its PO sheet: retail prices typed, the two
    raw-code titles and their colour families fixed. `edit(ws)` may change the Descriptions sheet; `cart()` is the
    cart drafted with (None: none). -> (path, the draft's rows)."""
    cat = catm.load(td.write_catalog(tmp_path))
    from oc import draft as draftm
    rows, review, checks, fills = draftm.build(d or td.docs(), brand, cat, histm.History([("F/W 26", [])]),
                                               td.base._tags(), cart=cart() if cart else None)
    path = tmp_path / "reviewed.xlsx"
    poxlsx.write_po(str(path), poxlsx.PO_HEADER, rows, review, checks, fills, brand=brand)
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


def po_edit(path, code, column, fn):
    """A review edit on the PO sheet: `fn(value)` into `column` of every NEW row of `code`."""
    wb = openpyxl.load_workbook(path)
    ws = wb.worksheets[0]
    col = {c.value: c.column for c in ws[1]}
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, col[poxlsx.CODE]).value == code and ws.cell(r, col["Command"]).value == "NEW":
            ws.cell(r, col[column]).value = fn(ws.cell(r, col[column]).value)
    wb.save(path)


def by_code(tmp_path):
    """{manufacture code: Body HTML} of the import, read off each description's SKU line."""
    got = imported(tmp_path)
    return {} if got is None else {r[2].rsplit("SKU: ", 1)[1][:-len("</p>")]: r[2] for r in got[2]}


def cart_with(**jacket):
    """The invented cart, its jacket row changed (materials='' leaves the PO's template composition '-' at draft)."""
    def make():
        c = td.cart()
        c["rows"] = dict(c["rows"], **{td.JACKET: dict(c["rows"][td.JACKET], **jacket)})
        return c
    return make


NO_PO_VALUES = cart_with(materials="", made_in="", made_in_raw="")   # the PO gives the jacket none, from the draft on


def typed(path, code, composition, made_in=None):
    """The person types the composition (and a made in) into the PO's template body, as they do today."""
    po_edit(path, code, "Body HTML", lambda v: v.replace("Composition: -", f"Composition: {composition}"))
    if made_in:
        po_edit(path, code, poxlsx.MADE_IN, lambda v: made_in)


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
    (lambda b: b.replace("<li>Composition: Shell: 100% Cotton &amp; Linen - Lining: 100% Cupro</li>",
                         "<li>Composition: --</li>"), "its Composition line is a placeholder"),
    (lambda b: b.replace("<li>Composition: Shell: 100% Cotton &amp; Linen - Lining: 100% Cupro</li>",
                         "<li>Composition: N/A</li>"), "its Composition line is a placeholder"),
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
    path, _ = reviewed(tmp_path, edit, cart=NO_PO_VALUES)   # the PO gives nothing to put right what the gates judge
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
    assert "it has no Composition line" in c["Detail"] and "fix it there" not in c["Detail"]   # '-' asks for no fix


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


# ---- every field the description restates comes from the reviewed PO (review case 2026-11-16-buyer-5) -----------
OC_COMPOSITION = "<li>Composition: Shell: 100% Cotton &amp; Linen - Lining: 100% Cupro</li>"   # the cart's: Shell only


def test_a_po_left_as_drafted_leaves_the_descriptions_own_lines(tmp_path):
    path, _ = reviewed(tmp_path)   # the PO's template says the cart's 'Shell: 100% Cotton', the description the OC's
    run(tmp_path, path)
    jacket = by_code(tmp_path)[td.JACKET]
    assert OC_COMPOSITION in jacket and "<li>Made in Italy</li>" in jacket and "  <li>Cupro lining</li>" in jacket


def test_a_composition_changed_on_the_po_is_written(tmp_path):
    path, _ = reviewed(tmp_path)
    po_edit(path, td.JACKET, "Body HTML", lambda v: v.replace("Composition: Shell: 100% Cotton",
                                                              "Composition: SHELL: 100% LINEN - LINING: 100% CUPRO"))
    run(tmp_path, path)
    jacket = by_code(tmp_path)[td.JACKET]
    assert "<ul>\n<li>Composition: Shell: 100% Linen - Lining: 100% Cupro</li>\n" in jacket
    assert jacket.count("Composition") == 1 and "  <li>Cupro lining</li>" in jacket   # the new one gives it too


def test_a_composition_the_po_replaced_with_a_stale_part_bullet_is_held_back(tmp_path):
    path, _ = reviewed(tmp_path)
    po_edit(path, td.JACKET, "Body HTML", lambda v: v.replace("Composition: Shell: 100% Cotton", "Composition: 100% LINEN"))
    checks = run(tmp_path, path)
    assert td.JACKET not in by_code(tmp_path)
    assert ("its bullet 'Cupro lining' comes from the drafted composition, which changed: remove it or make it agree"
            in check(checks)["Detail"])


def test_a_composition_edited_by_hand_with_a_stale_part_bullet_is_held_back(tmp_path):
    def edit(ws):   # the PO left as drafted; the person rewrote the description's line and kept the lining bullet
        c = desc_cell(ws, td.JACKET)
        c.value = re.sub(r"Composition: [^<]*", "Composition: 100% wool", c.value)
    path, _ = reviewed(tmp_path, edit)
    checks = run(tmp_path, path)
    assert td.JACKET not in by_code(tmp_path)
    assert ("its bullet 'Cupro lining' comes from the drafted composition, which changed: remove it or make it agree"
            in check(checks)["Detail"])


def test_a_part_the_ai_rested_on_the_drafted_composition_goes_stale_with_it():
    body = ("<p>ACME STUDIO's Loop Shirt in Black is a shirt. Its shell is cotton and linen.</p>\n<ul>\n"
            "<li>Composition: 100% Wool</li>\n  <li>Cotton and linen shell</li>\n  <li>Zip fastening</li>\n"
            "  <li>Cupro lining</li>\n</ul><br><p>SKU: ZQ0008AB8P0008 900</p>")
    drafted = "Shell: 100% Cotton &amp; Linen - Lining: 100% Cupro"
    assert descimport.stale_bullets(body, drafted, "100% Wool", "shirt") == [
        ("bullet", "Cupro lining"), ("bullet", "Cotton and linen shell"), ("intro", "Its shell is cotton and linen")]
    assert descimport.stale_bullets(body, drafted, "Shell: 100% Cotton & Linen - Lining: 100% Cupro", "shirt") == []


def test_a_composition_cleared_on_the_po_is_held_back(tmp_path):
    path, _ = reviewed(tmp_path)
    po_edit(path, td.JACKET, "Body HTML", lambda v: v.replace("Composition: Shell: 100% Cotton", "Composition: -"))
    checks = run(tmp_path, path)
    assert td.JACKET not in by_code(tmp_path)
    assert ("the PO's composition was cleared, and the description still says 'Shell: 100% Cotton & Linen - Lining: "
            "100% Cupro'") in check(checks)["Detail"]


def test_a_composition_typed_into_the_po_goes_into_a_description_that_had_none(tmp_path):
    path, _ = reviewed(tmp_path)
    run(tmp_path, path)
    assert td.TOTE not in by_code(tmp_path)   # the OC printed no composition: held back
    typed(path, td.TOTE, "100% COTTON CANVAS")
    typed(path, td.LOAFER, "Upper: 100% Calf Leather")
    run(tmp_path, path)
    got = by_code(tmp_path)
    assert got[td.TOTE] == ("<p>ACME STUDIO's Drift Tote in Sand is a tote bag.</p>\n<ul>\n"
                            "<li>Composition: 100% Cotton Canvas</li>\n  <li>Sand colourway</li>\n"
                            f"</ul><br><p>SKU: {td.TOTE}</p>")   # one edit; the fixed title's colour as the draft puts it
    assert got[td.LOAFER].startswith("<p>ACME STUDIO's Mesa Loafer in Black is a pair of loafers.</p>\n<ul>\n"
                                     "<li>Composition: Upper: 100% Calf Leather</li>\n  <li>")   # first in the list


@pytest.mark.parametrize("line,kept", [("SHELL: 100% LINEN - LINING: 100% CUPRO", True), ("100% Wool", False)])
def test_an_edited_composition_is_kept_when_it_agrees_with_the_po_and_held_back_when_not(tmp_path, line, kept):
    def edit(ws):
        c = desc_cell(ws, td.JACKET)
        c.value = re.sub(r"Composition: [^<]*", f"Composition: {line}", c.value)
    path, _ = reviewed(tmp_path, edit)
    po_edit(path, td.JACKET, "Body HTML", lambda v: v.replace("Composition: Shell: 100% Cotton",
                                                              "Composition: Shell: 100% Linen - Lining: 100% Cupro"))
    checks = run(tmp_path, path)
    if kept:
        assert f"<li>Composition: {line}</li>" in by_code(tmp_path)[td.JACKET]   # as typed
    else:
        assert td.JACKET not in by_code(tmp_path)
        assert (f"{td.JACKET} 'Orbit Field Jacket in Slate': its Composition line says '100% Wool', the PO's Body HTML "
                "'Shell: 100% Linen - Lining: 100% Cupro': make them agree") in check(checks)["Detail"]


def test_a_placeholder_on_the_po_is_never_written_and_a_bad_composition_there_is_named(tmp_path):
    path, _ = reviewed(tmp_path, cart=NO_PO_VALUES)   # '-' on the PO at draft, and still
    run(tmp_path, path)
    jacket = by_code(tmp_path)[td.JACKET]   # the PO gives none: the description's own lines stand, for the gates
    assert "<li>Composition: Shell: 100% Cotton &amp; Linen - Lining: 100% Cupro</li>" in jacket
    assert "<li>Made in Italy</li>" in jacket and "Composition: -" not in jacket and "Made in -" not in jacket
    po_edit(path, td.JACKET, "Body HTML", lambda v: v.replace("Composition: -", "Composition: 60% Cotton 30% Linen"))
    checks = run(tmp_path, path)
    assert td.JACKET not in by_code(tmp_path)
    assert ("the PO's Body HTML composition '60% Cotton 30% Linen' adds up to 90%, so the source may have cut it off: "
            "fix it there") in check(checks)["Detail"]


def made_in_both(path, code, meta, line):
    """Review sets the PO's custom.made_in and its template body's Made in line."""
    po_edit(path, code, poxlsx.MADE_IN, lambda v: meta)
    po_edit(path, code, "Body HTML", lambda v: re.sub(r"Made in [^<]*", f"Made in {line}", v))


def test_made_in_comes_from_the_pos_custom_made_in_before_its_body(tmp_path):
    path, _ = reviewed(tmp_path)
    made_in_both(path, td.JACKET, "PT", "Portugal")
    run(tmp_path, path)
    jacket = by_code(tmp_path)[td.JACKET]
    assert "\n<li>Made in Portugal</li>\n</ul>" in jacket and "Italy" not in jacket
    made_in_both(path, td.JACKET, "Spain", "Itlay")   # both changed, to two values: which is right is not known
    checks = run(tmp_path, path)
    assert td.JACKET not in by_code(tmp_path)
    assert ("the PO's custom.made_in says 'Spain', its Body HTML Made in line 'Itlay': make them agree"
            in check(checks)["Detail"])


def test_custom_made_in_fixed_alone_is_one_edit(tmp_path):
    path, _ = reviewed(tmp_path)
    po_edit(path, td.JACKET, poxlsx.MADE_IN, lambda v: "PT")   # the template body still says Made in Italy, as drafted
    checks = run(tmp_path, path)
    jacket = by_code(tmp_path)[td.JACKET]
    assert "<li>Made in Portugal</li>" in jacket and "Italy" not in jacket and "custom.made_in" not in check(checks)["Detail"]


def test_a_made_in_changed_only_in_the_template_body_holds_the_product(tmp_path):
    path, _ = reviewed(tmp_path)
    po_edit(path, td.JACKET, "Body HTML", lambda v: v.replace("Made in Italy", "Made in Portugal"))
    checks = run(tmp_path, path)
    assert td.JACKET not in by_code(tmp_path)
    assert ("the PO's custom.made_in says 'Italy', its Body HTML Made in line 'Portugal': make them agree"
            in check(checks)["Detail"])


@pytest.mark.parametrize("cleared", ["-", "n.a.", None])
def test_custom_made_in_cleared_while_the_body_line_names_a_country_holds_the_product(tmp_path, cleared):
    path, _ = reviewed(tmp_path)
    po_edit(path, td.JACKET, poxlsx.MADE_IN, lambda v: cleared)   # the template body still says Made in Italy
    checks = run(tmp_path, path)
    assert td.JACKET not in by_code(tmp_path)
    assert ("the PO's custom.made_in was cleared, its Body HTML Made in line still says 'Italy': make them agree"
            in check(checks)["Detail"])


def test_a_custom_made_in_empty_at_draft_and_still_reads_the_body_line(tmp_path):
    path, _ = reviewed(tmp_path, cart=NO_PO_VALUES)   # custom.made_in '-' and 'Made in -' at draft
    po_edit(path, td.JACKET, "Body HTML", lambda v: v.replace("Made in -", "Made in Spain"))
    run(tmp_path, path)
    assert "<li>Made in Spain</li>" in by_code(tmp_path)[td.JACKET]


def test_a_po_body_off_its_template_gives_one_reason_whatever_its_made_in():
    code = "ZQ0006AB6P0006 900"
    body = (f"<p>ACME STUDIO's Loop Cape in Black is a coat.</p>\n<ul>\n<li>Composition: 100% Wool</li>\n"
            f"<li>Made in Italy</li>\n</ul><br><p>SKU: {code}</p>")
    d = {"Title (as drafted)": "Loop Cape in Black", "Type (as drafted)": "MEN CLOTHING COATS",
         "Composition (as drafted)": "100% Wool", "Made in (as drafted)": "Italy", "PO composition (as drafted)": "100% Wool",
         "PO made in (as drafted)": "Italy", "Colourway (as drafted)": "Black colourway", "Description (as drafted)": body}
    for meta in ("Italy", "Itlay", "-"):
        row = {"Title": "Loop Cape in Black", "Type": "MEN CLOTHING COATS", "Vendor": "ACME STUDIO", poxlsx.MADE_IN: meta,
               "Body HTML": "<p>All wool, made in a mill.</p>"}
        assert descimport.reconcile(body, d, row, BRAND)[1] == [
            f"the PO's Body HTML no longer follows the brand's template ({BRAND['body_html']}), so its composition and "
            "made in cannot be read"], meta


NO_MADE_IN_SLOT = ["<p>Composition: {composition}</p> <p>SKU: {code}</p>", "<p>{code}</p>"]


@pytest.mark.parametrize("template", NO_MADE_IN_SLOT)
@pytest.mark.parametrize("meta,made,why", [
    (None, "Italy", None),   # untouched
    ("PT", "Portugal", None),   # custom.made_in fixed
    ("-", None, "the PO's made in was cleared, and the description still says 'Made in Italy'"),
    ("Itlay", None, "the PO's made in 'Itlay' is not a country the tool knows: fix it there"),
])
def test_a_template_with_no_made_in_slot_reads_custom_made_in_alone(tmp_path, template, meta, made, why):
    brand = dict(BRAND, body_html=template)
    path, _ = reviewed(tmp_path, brand=brand)
    if meta:
        po_edit(path, td.JACKET, poxlsx.MADE_IN, lambda v: meta)
    checks = run(tmp_path, path, brand)
    detail = check(checks)["Detail"]
    assert "Made in line" not in detail and "no longer follows" not in detail
    if made:
        assert f"<li>Made in {made}</li>" in by_code(tmp_path)[td.JACKET]   # written end to end
    else:
        assert td.JACKET not in by_code(tmp_path) and why in detail
        assert ("cleared" in detail) == ("cleared" in why)   # an unknown country is never a clear


@pytest.mark.parametrize("meta,made", [(None, "Italy"), ("PT", "Portugal")])
def test_a_code_only_template_with_a_body_off_it_still_writes_made_in_from_custom_made_in(tmp_path, meta, made):
    brand = dict(BRAND, body_html="<p>{code}</p>")   # a <p> body would match: the code slot takes anything
    path, _ = reviewed(tmp_path, brand=brand)
    po_edit(path, td.JACKET, "Body HTML", lambda v: f"<div>{v}</div>")
    if meta:
        po_edit(path, td.JACKET, poxlsx.MADE_IN, lambda v: meta)
    checks = run(tmp_path, path, brand)
    assert f"<li>Made in {made}</li>" in by_code(tmp_path)[td.JACKET]   # written: the template holds nothing unread
    assert td.JACKET not in check(checks)["Detail"].split("held back", 1)[-1]


def test_a_composition_only_template_off_its_body_gives_one_reason_and_still_reads_custom_made_in():
    code = "ZQ0006AB6P0006 900"
    brand = dict(BRAND, body_html="<p>Composition: {composition}</p> <p>SKU: {code}</p>")
    body = (f"<p>ACME STUDIO's Loop Cape in Black is a coat.</p>\n<ul>\n<li>Composition: 100% Wool</li>\n"
            f"<li>Made in Italy</li>\n</ul><br><p>SKU: {code}</p>")
    d = {"Title (as drafted)": "Loop Cape in Black", "Type (as drafted)": "MEN CLOTHING COATS",
         "Composition (as drafted)": "100% Wool", "Made in (as drafted)": "Italy", "PO composition (as drafted)": "100% Wool",
         "PO made in (as drafted)": "Italy", "Colourway (as drafted)": "Black colourway", "Description (as drafted)": body}
    row = {"Title": "Loop Cape in Black", "Type": "MEN CLOTHING COATS", "Vendor": "ACME STUDIO", poxlsx.MADE_IN: "PT",
           "Body HTML": "<div>All wool.</div>"}
    out, why = descimport.reconcile(body, d, row, brand)
    assert why == [f"the PO's Body HTML no longer follows the brand's template ({brand['body_html']}), so its "
                   "composition cannot be read"]
    assert "<li>Made in Portugal</li>" in out   # the template holds no made in: custom.made_in is still read


def test_the_draft_fills_both_made_in_copies_from_one_value(tmp_path):
    """The single base ('PO made in (as drafted)') rests on this: draft.build writes custom.made_in and the template
    body's Made in line from one value, so at draft the two copies never differ."""
    from oc import draft as draftm
    cat = catm.load(td.write_catalog(tmp_path))
    for cart in (td.cart, PORTUGAL, NO_PO_VALUES, None):
        rows, *_ = draftm.build(td.docs(), BRAND, cat, histm.History([("F/W 26", [])]), td.base._tags(),
                                cart=cart() if cart else None)
        new = [r for r in rows if r["Command"] == "NEW"]
        assert new
        for r in new:
            line = descimport.template_values(r["Body HTML"], BRAND["body_html"])["made_in"]
            assert descimport.place(r[poxlsx.MADE_IN], BRAND) == descimport.place(line, BRAND), r[poxlsx.CODE]


@pytest.mark.parametrize("value,why", [
    ("Itlay", "the PO's made in 'Itlay' is not a country the tool knows: fix it there"),
    ("Itlay-meta", "the PO's made in 'Itlay' is not a country the tool knows: fix it there"),
    ("x", "the PO's made in 'x' is not a country the tool knows: fix it there"),
    ("--", "the PO's made in was cleared, and the description still says 'Made in Italy'"),
    ("n.a.", "the PO's made in was cleared, and the description still says 'Made in Italy'"),
])
def test_a_made_in_on_the_po_is_written_only_as_a_country_the_tool_knows(tmp_path, value, why):
    path, _ = reviewed(tmp_path)
    if value.endswith("-meta"):   # custom.made_in alone changed, the template line still as drafted
        po_edit(path, td.JACKET, poxlsx.MADE_IN, lambda v: value[:-len("-meta")])
    else:
        made_in_both(path, td.JACKET, value, value)
    checks = run(tmp_path, path)
    assert td.JACKET not in by_code(tmp_path) and why in check(checks)["Detail"]
    assert ("not a country the tool knows" in check(checks)["Detail"]) == ("cleared" not in why)   # '--', n.a.: none
    if "cleared" not in why:   # an unknown country is named as that, never as a clear
        assert "cleared" not in check(checks)["Detail"]


@pytest.mark.parametrize("line,why", [
    ("Made in --", "it says 'Made in -'"), ("Made in \u2014-", "it says 'Made in -'"),
    ("Made in n.a.", "it says 'Made in -'"), ("Made in N/A", "it says 'Made in -'"),
    ("Made in x", "its Made in line names 'x', not a country the tool knows"),
])
def test_a_made_in_line_that_says_none_or_no_known_country_is_held_back(line, why):
    body = (f"<p>ACME STUDIO's Loop Cap in Black is a cap.</p>\n<ul>\n<li>Composition: 100% Cotton</li>\n<li>{line}</li>"
            "\n</ul><br><p>SKU: ZQ0005AB5P0005 900</p>")
    assert why in descimport.problems(body, "Loop Cap in Black", "ZQ0005AB5P0005 900", "ACME STUDIO",
                                      "MEN ACCS HATS CAPS", BRAND)
    assert descimport.problems(body.replace(line, "Made in Italy"), "Loop Cap in Black", "ZQ0005AB5P0005 900",
                               "ACME STUDIO", "MEN ACCS HATS CAPS", BRAND) == []


def test_a_made_in_the_po_gives_goes_last_in_the_list(tmp_path):
    path, _ = reviewed(tmp_path)
    typed(path, td.LOAFER, "Upper: 100% Calf Leather")
    po_edit(path, td.LOAFER, "Body HTML", lambda v: v.replace("Made in -", "Made in Spain"))   # custom.made_in '-'
    run(tmp_path, path)
    assert by_code(tmp_path)[td.LOAFER].endswith(f"  <li>Rubber sole</li>\n<li>Made in Spain</li>\n</ul><br><p>SKU: "
                                                 f"{td.LOAFER}</p>")


@pytest.mark.parametrize("line,kept", [("Made in SPAIN", True), ("Made in France", False)])
def test_an_edited_made_in_is_kept_when_it_agrees_with_the_po_and_held_back_when_not(tmp_path, line, kept):
    def edit(ws):
        c = desc_cell(ws, td.JACKET)
        c.value = c.value.replace("<li>Made in Italy</li>", f"<li>{line}</li>")
    path, _ = reviewed(tmp_path, edit)
    made_in_both(path, td.JACKET, "Spain", "Spain")
    checks = run(tmp_path, path)
    if kept:
        assert f"<li>{line}</li>" in by_code(tmp_path)[td.JACKET]
    else:
        assert td.JACKET not in by_code(tmp_path)
        assert "its Made in line says 'France', the PO's made in 'Spain': make them agree" in check(checks)["Detail"]


PORTUGAL = cart_with(made_in="Portugal", made_in_raw="Made in Portugal")   # the OC says IT, the cart Portugal


def test_a_made_in_the_po_left_as_drafted_leaves_the_descriptions_own_and_the_draft_says_why(tmp_path):
    path, _ = reviewed(tmp_path, cart=PORTUGAL)   # the PO's custom.made_in and template say Portugal
    why = next(d["Why"] for d in poxlsx.read_descriptions(path) if d["Manufacture code"] == td.JACKET)
    assert "says Portugal, used the OC line (AC - 1 line 1) first" in why
    assert "the custom.made_in column says 'Portugal', the description Italy: make them agree" in why
    run(tmp_path, path)
    assert "<li>Made in Italy</li>" in by_code(tmp_path)[td.JACKET]


def test_a_made_in_changed_on_the_po_is_written(tmp_path):
    path, _ = reviewed(tmp_path, cart=PORTUGAL)
    made_in_both(path, td.JACKET, "Spain", "Spain")
    run(tmp_path, path)
    jacket = by_code(tmp_path)[td.JACKET]
    assert "<li>Made in Spain</li>" in jacket and "Italy" not in jacket and "Portugal" not in jacket


def test_a_made_in_cleared_on_the_po_is_held_back(tmp_path):
    path, _ = reviewed(tmp_path, cart=PORTUGAL)
    po_edit(path, td.JACKET, poxlsx.MADE_IN, lambda v: "-")
    po_edit(path, td.JACKET, "Body HTML", lambda v: v.replace("Made in Portugal", "Made in -"))
    checks = run(tmp_path, path)
    assert td.JACKET not in by_code(tmp_path)
    assert "the PO's made in was cleared, and the description still says 'Made in Italy'" in check(checks)["Detail"]


def test_the_draft_says_why_a_composition_differs_from_the_pos(tmp_path):
    path, _ = reviewed(tmp_path)
    why = next(d["Why"] for d in poxlsx.read_descriptions(path) if d["Manufacture code"] == td.JACKET)
    assert "composition: cart TEST row 7: Materials says 'Shell: 100% Cotton', used the OC line (AC - 1 line 1) first" in why


def test_a_descriptions_sheet_without_the_drafted_values_writes_no_import(tmp_path):
    path, _ = reviewed(tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb[poxlsx.DESCRIPTIONS]
    ws.delete_cols(poxlsx.DESCRIPTION_COLS.index("PO composition (as drafted)") + 1, 2)
    wb.save(path)
    checks = run(tmp_path, path)
    assert imported(tmp_path) is None and (tmp_path / "run-PU-new.xlsx").exists()
    c = check(checks)
    assert c["Result"] == "WARN" and c["Detail"].startswith("no description import is written")
    assert "PO composition (as drafted), PO made in (as drafted)" in c["Detail"] and "Draft the PO again" in c["Detail"]


def test_a_final_title_the_person_typed_in_is_not_swapped_again(tmp_path):
    longer = "Waxed Orbit Field Jacket in Slate"   # the final Title holds the drafted one

    def edit(ws):
        c = desc_cell(ws, td.JACKET)
        c.value = c.value.replace("STUDIO's Orbit Field Jacket in Slate is", f"STUDIO's {longer} is")
    path, _ = reviewed(tmp_path, edit)
    po_edit(path, td.JACKET, "Title", lambda v: longer)
    run(tmp_path, path)
    assert by_code(tmp_path)[td.JACKET].startswith(f"<p>ACME STUDIO's {longer} is a waxed lightweight jacket.</p>")


@pytest.mark.parametrize("intro", [
    "<p>ACME STUDIO's Waxed Orbit Field Jacket in Slate is a jacket.</p>",   # a title the PO does not have
    "<p>ACME STUDIO's Orbit Field Jacket in Slate is a jacket. The Orbit Field Jacket in Slate is waxed.</p>",   # twice
])
def test_an_intro_without_the_final_title_once_in_its_frame_is_held_back(tmp_path, intro):
    def edit(ws):
        c = desc_cell(ws, td.JACKET)
        c.value = re.sub(r"^<p>.*?</p>", intro, c.value)
    path, _ = reviewed(tmp_path, edit)
    checks = run(tmp_path, path)
    assert td.JACKET not in by_code(tmp_path)
    assert ("its intro does not hold the final Title once, as \"ACME STUDIO's Orbit Field Jacket in Slate is ...\""
            in check(checks)["Detail"])


def test_the_item_noun_follows_a_type_fixed_on_the_po(tmp_path):
    path, _ = reviewed(tmp_path)
    po_edit(path, td.JACKET, "Type", lambda v: "MEN CLOTHING JACKETS BOMBER")
    typed(path, td.LOAFER, "Upper: 100% Calf Leather")
    po_edit(path, td.LOAFER, "Type", lambda v: "MEN SHOES SNEAKERS")
    run(tmp_path, path)
    got = by_code(tmp_path)
    assert got[td.JACKET].startswith("<p>ACME STUDIO's Orbit Field Jacket in Slate is a waxed bomber jacket.</p>")
    assert got[td.LOAFER].startswith("<p>ACME STUDIO's Mesa Loafer in Black is a pair of sneakers.</p>")


def test_the_item_swap_keeps_the_adjective_and_takes_the_article_by_sound():
    body = "<p>ACME STUDIO's Loop Drop in Gold is a ring. It is cast.</p>\n<p>SKU: ZQ0007AB7P0007 900</p>"
    swap = lambda b, old, new: descimport.swap_item(b, "ACME STUDIO", "Loop Drop in Gold", old, new)
    assert swap(body, "ring", "earring") == body.replace("is a ring.", "is an earring.")
    assert swap(body.replace("a ring", "a cast ring"), "ring", "earring") == body.replace("a ring", "a cast earring")
    assert swap(body, "ring", "pair of earrings") == body.replace("a ring", "a pair of earrings")
    assert swap(body.replace("a ring", "a gold piece"), "ring", "earring") == body.replace("a ring", "a gold piece")


@pytest.mark.parametrize("phrase,kept", [("a waxed field jacket", True), ("a waxed shirt", False)])
def test_an_edited_intro_item_is_kept_when_it_is_the_types_kind_and_held_back_when_not(tmp_path, phrase, kept):
    def edit(ws):
        c = desc_cell(ws, td.JACKET)
        c.value = c.value.replace("is a waxed lightweight jacket.", f"is {phrase}.")
    path, _ = reviewed(tmp_path, edit)
    checks = run(tmp_path, path)
    if kept:
        assert f"is {phrase}." in by_code(tmp_path)[td.JACKET]
    else:
        assert td.JACKET not in by_code(tmp_path)
        assert ("its intro names another kind of product than the final Type's item noun 'lightweight jacket'"
                in check(checks)["Detail"])


def test_a_po_that_cannot_be_read_or_a_type_with_no_item_noun_holds_a_description_back():
    code = "ZQ0006AB6P0006 900"
    body = (f"<p>ACME STUDIO's Loop Cape in Black is a coat.</p>\n<ul>\n<li>Composition: 100% Wool</li>\n</ul><br>"
            f"<p>SKU: {code}</p>")
    d = {"Title (as drafted)": "Loop Cape in Black", "Type (as drafted)": "MEN CLOTHING COATS",
         "Composition (as drafted)": "100% Wool", "Colourway (as drafted)": "Black colourway"}   # removed by hand
    row = {"Title": "Loop Cape in Black", "Type": "MEN CLOTHING CAPES", "Vendor": "ACME STUDIO", poxlsx.MADE_IN: "-",
           "Body HTML": f"<p>Composition: 100% Wool</p> <p>Made in -</p> <p>SKU: {code}</p>"}
    assert descimport.reconcile(body, d, row, BRAND) == (body, [
        "the final Type 'MEN CLOTHING CAPES' gives the tool no item noun to check the intro's 'coat' against"])
    row.update(Type="MEN CLOTHING COATS", **{"Body HTML": "<p>All wool.</p>"})
    assert descimport.reconcile(body, d, row, BRAND)[1] == [   # one reason, and no made in line read as '-'
        f"the PO's Body HTML no longer follows the brand's template ({BRAND['body_html']}), so its composition and "
        "made in cannot be read"]
    made_only = dict(BRAND, body_html="<p>Made in {made_in}</p> <p>SKU: {code}</p>")
    assert descimport.reconcile(body, d, row, made_only)[1] == [
        f"the PO's Body HTML no longer follows the brand's template ({made_only['body_html']}), so its made in cannot "
        "be read"]


def test_the_pos_values_are_read_through_the_brands_template():
    t = BRAND["body_html"]
    assert descimport.template_values("<p>Composition: 100% Wool &amp; Silk</p><p>Made in IT</p>\n<p>SKU: X 1</p>", t) \
        == {"composition": "100% Wool &amp; Silk", "made_in": "IT"}
    assert descimport.template_values("<p>100% Wool</p>", t) is None
    assert descimport.template_values("<p>Wool</p> X 1", "<p>{composition}</p> {code}") == {"composition": "Wool"}


def test_a_rerun_that_writes_no_import_leaves_no_earlier_one(tmp_path):
    path, _ = reviewed(tmp_path)
    run(tmp_path, path)
    assert imported(tmp_path) is not None
    wb = openpyxl.load_workbook(path)
    del wb[poxlsx.DESCRIPTIONS]
    wb.save(path)
    checks = run(tmp_path, path)   # the same prefix, a run that succeeds and writes no import
    assert not [c for c in checks if c["Result"] == "FAIL"] and imported(tmp_path) is None


# ---- the colourway bullet, and the drafted colour anywhere else (review case 2026-11-16-buyer-10) --------------
def test_the_colourway_bullet_follows_a_colour_fixed_in_the_title(tmp_path):
    path, _ = reviewed(tmp_path)
    po_edit(path, td.JACKET, "Title", lambda v: "Orbit Field Jacket in Navy")
    run(tmp_path, path)
    jacket = by_code(tmp_path)[td.JACKET]
    assert jacket.startswith("<p>ACME STUDIO's Orbit Field Jacket in Navy is a waxed lightweight jacket.</p>")
    assert "\n  <li>Navy colourway</li>\n" in jacket and "Slate" not in jacket


def test_the_colourway_bullet_goes_when_the_final_title_gives_no_colour_words(tmp_path):
    path, _ = reviewed(tmp_path)
    po_edit(path, td.JACKET, "Title", lambda v: "Orbit Field Jacket")
    run(tmp_path, path)
    jacket = by_code(tmp_path)[td.JACKET]
    assert "colourway" not in jacket and "Slate" not in jacket
    assert "<li>Composition: Shell: 100% Cotton &amp; Linen - Lining: 100% Cupro</li>\n  <li>Cupro lining</li>" in jacket


@pytest.mark.parametrize("final,want", [
    ("Loop Cap in Navy", "  <li>Navy colourway</li>"), ("Loop Cap in 4N0", None), ("Loop Cap", None),
    ("Loop Cap in Black", "  <li>Black colourway</li>"),
])
def test_the_colourway_bullet_follows_the_drafts_own_rule(final, want):
    body = "<ul>\n<li>Composition: 100% Cotton</li>\n  <li>Black colourway</li>\n  <li>Zip fastening</li>\n</ul>"
    out, bad = descimport.colourway(body, "Black colourway", final)
    assert bad == [] and out == (body.replace("  <li>Black colourway</li>", want) if want else
                                 body.replace("\n  <li>Black colourway</li>", ""))


def test_a_raw_code_title_fixed_in_review_gets_its_colourway_bullet(tmp_path):
    path, _ = reviewed(tmp_path)   # 'Loafer in 900' (a colour code: the draft gave no bullet) is 'Mesa Loafer in Black'
    typed(path, td.LOAFER, "Upper: 100% Calf Leather")
    run(tmp_path, path)
    assert by_code(tmp_path)[td.LOAFER].startswith(
        "<p>ACME STUDIO's Mesa Loafer in Black is a pair of loafers.</p>\n<ul>\n<li>Composition: Upper: 100% Calf "
        "Leather</li>\n  <li>Black colourway</li>\n  <li>Leather upper</li>")


@pytest.mark.parametrize("final", ["Orbit Field Jacket in Slate", "Orbit Field Jacket in Navy"])
def test_a_colourway_bullet_the_person_removed_is_not_put_back(tmp_path, final):
    def edit(ws):
        c = desc_cell(ws, td.JACKET)
        c.value = c.value.replace("\n  <li>Slate colourway</li>", "")
    path, _ = reviewed(tmp_path, edit)
    po_edit(path, td.JACKET, "Title", lambda v: final)
    run(tmp_path, path)
    assert "colourway" not in by_code(tmp_path)[td.JACKET]


def test_no_colourway_bullet_goes_in_for_colour_words_the_draft_would_drop():
    body = "<ul>\n<li>Composition: 100% Cotton</li>\n  <li>Zip fastening</li>\n</ul><br><p>SKU: X 1</p>"
    for final in ("Loop Cap in Summer Black", "Loop Cap in 4N0", "Loop Cap"):   # a season word, a code, none
        assert descimport.colourway(body, None, final) == (body, [])
    assert descimport.colourway("<p>SKU: X 1</p>", None, "Loop Cap in Black")[0] == \
        "<ul>\n  <li>Black colourway</li>\n</ul><br><p>SKU: X 1</p>"


def test_a_red_title_with_colour_words_gets_its_colourway_bullet_from_the_stored_fact(tmp_path):
    f = dict(code=td.TOTE, vendor="ACME STUDIO", title=("Drift Tote in Sand", "guess", "a red title"),
             ptype=("MEN BAGS SHOPPER & TOTE BAGS", "catalog", ""), made_in=[], text=[],
             composition=[dict(value="100% Cotton", where="x", source="OC", html=False)])
    drafted = describe.render(describe.draft(f, BRAND))
    assert "colourway" not in drafted   # the draft writes none for a red title, colour words or not
    row = {"Type": "MEN BAGS SHOPPER & TOTE BAGS", poxlsx.MADE_IN: "-",
           "Body HTML": f"<p>Composition: 100% Cotton</p> <p>Made in -</p> <p>SKU: {td.TOTE}</p>"}
    assert descimport.as_drafted(drafted, row, BRAND)[-2] is None   # the fact the sheet stores: no bullet
    out, bad = descimport.colourway(drafted, None, "Drift Tote in Sand")   # the title unchanged in review
    assert bad == [] and "<li>Composition: 100% Cotton</li>\n  <li>Sand colourway</li>" in out


def test_the_red_title_case_end_to_end_through_the_sheet(tmp_path):
    def edit(ws):   # the jacket as a red title is drafted: no bullet written, the column blank
        c = desc_cell(ws, td.JACKET)
        c.value = c.value.replace("\n  <li>Slate colourway</li>", "")
        ws.cell(c.row, poxlsx.DESCRIPTION_COLS.index("Colourway (as drafted)") + 1).value = None
    path, _ = reviewed(tmp_path, edit)
    run(tmp_path, path)
    assert ("<li>Composition: Shell: 100% Cotton &amp; Linen - Lining: 100% Cupro</li>\n  <li>Slate colourway</li>"
            in by_code(tmp_path)[td.JACKET])


def test_a_colourway_bullet_the_person_added_that_disagrees_is_held_back(tmp_path):
    def edit(ws):   # the loafer's draft wrote none (its title a colour code); the person typed one in
        c = desc_cell(ws, td.LOAFER)
        c.value = c.value.replace("<ul>\n", "<ul>\n  <li>Ink colourway</li>\n")
    path, _ = reviewed(tmp_path, edit)   # the review fixes the title to 'Mesa Loafer in Black'
    typed(path, td.LOAFER, "Upper: 100% Calf Leather")
    checks = run(tmp_path, path)
    assert td.LOAFER not in by_code(tmp_path)
    assert ("its colourway bullet says 'Ink', the final title's colour words 'Black': make them agree"
            in check(checks)["Detail"])


def test_a_descriptions_sheet_without_the_colourway_column_writes_no_import(tmp_path):
    path, _ = reviewed(tmp_path)
    wb = openpyxl.load_workbook(path)
    wb[poxlsx.DESCRIPTIONS].delete_cols(poxlsx.DESCRIPTION_COLS.index("Colourway (as drafted)") + 1)
    wb.save(path)
    checks = run(tmp_path, path)
    assert imported(tmp_path) is None and "lacks the column(s) Colourway (as drafted)" in check(checks)["Detail"]


def test_an_edited_colourway_bullet_that_disagrees_with_the_final_title_is_held_back(tmp_path):
    def edit(ws):
        c = desc_cell(ws, td.JACKET)
        c.value = c.value.replace("<li>Slate colourway</li>", "<li>Ink colourway</li>")
    path, _ = reviewed(tmp_path, edit)
    checks = run(tmp_path, path)
    assert td.JACKET not in by_code(tmp_path)
    assert ("its colourway bullet says 'Ink', the final title's colour words 'Slate': make them agree"
            in check(checks)["Detail"])


def test_the_drafted_colour_left_anywhere_else_holds_the_product(tmp_path):
    def edit(ws):   # a bullet naming the colour, as an AI part quoting the order's colour would
        c = desc_cell(ws, td.JACKET)
        c.value = c.value.replace("  <li>Zip fastening</li>", "  <li>Zip fastening in slate tone</li>")
    path, _ = reviewed(tmp_path, edit)
    po_edit(path, td.JACKET, "Title", lambda v: "Orbit Field Jacket in Navy")
    checks = run(tmp_path, path)
    assert td.JACKET not in by_code(tmp_path)
    assert ("its 'Zip fastening in slate tone' names 'slate', a colour word of the drafted title that the final title "
            "drops: check whether it means the old colour") in check(checks)["Detail"]


INTRO_SLATE = "<p>ACME STUDIO's Orbit Field Jacket in Slate is a waxed lightweight jacket.{}</p>\n<ul>\n" \
    "<li>Composition: 100% Cotton</li>\n  <li>Zip fastening</li>\n</ul><br><p>SKU: X 1</p>"


@pytest.mark.parametrize("item", ["coat", "lightweight jacket"])
def test_an_intro_as_drafted_is_not_read_after_the_tool_swaps_its_title_and_item(item):
    drafted = INTRO_SLATE.format(" Its slate tone suits any coat.")   # the draft's own words, from the order
    now = drafted.replace("in Slate is a waxed lightweight jacket.", f"in Navy is a waxed {item}.")
    assert descimport.old_colour(now, "Orbit Field Jacket in Slate", "Orbit Field Jacket in Navy", drafted,
                                 "lightweight jacket", item) == ""


@pytest.mark.parametrize("item", ["coat", "lightweight jacket"])
def test_an_intro_edited_by_hand_to_name_the_dropped_colour_holds(item):
    drafted = INTRO_SLATE.format(" Its tone suits any coat.")
    now = INTRO_SLATE.format(" Its slate tone suits any coat.").replace(
        "in Slate is a waxed lightweight jacket.", f"in Navy is a waxed {item}.")
    assert "names 'slate'" in descimport.old_colour(now, "Orbit Field Jacket in Slate", "Orbit Field Jacket in Navy",
                                                    drafted, "lightweight jacket", item)


def test_a_type_and_title_fixed_together_leave_a_drafted_intro_sentence_alone(tmp_path):
    def edit(ws):   # as if the draft wrote a second sentence that names the colour
        c = desc_cell(ws, td.JACKET)
        c.value = c.value.replace("lightweight jacket.</p>", "lightweight jacket. Its slate tone suits any coat.</p>")
        ws.cell(c.row, poxlsx.DESCRIPTION_COLS.index("Description (as drafted)") + 1).value = c.value
    path, _ = reviewed(tmp_path, edit)
    po_edit(path, td.JACKET, "Title", lambda v: "Orbit Field Jacket in Navy")
    po_edit(path, td.JACKET, "Type", lambda v: "MEN CLOTHING JACKETS BOMBER")
    run(tmp_path, path)
    assert by_code(tmp_path)[td.JACKET].startswith(
        "<p>ACME STUDIO's Orbit Field Jacket in Navy is a waxed bomber jacket. Its slate tone suits any coat.</p>")


def test_an_article_the_item_swap_changes_does_not_make_the_intro_read():
    drafted = INTRO_SLATE.format(" Its slate tone suits any coat.").replace("a waxed lightweight jacket", "a lightweight jacket")
    now = drafted.replace("in Slate is a lightweight jacket.", "in Navy is an umbrella.")   # 'a' became 'an'
    assert descimport.old_colour(now, "Orbit Field Jacket in Slate", "Orbit Field Jacket in Navy", drafted,
                                 "lightweight jacket", "umbrella") == ""


def test_a_one_sentence_intro_whose_adjective_is_a_dropped_colour_word_does_not_read():
    drafted = INTRO_SLATE.format("").replace("Jacket in Slate is a waxed", "Jacket in Washed Slate is a washed")
    now = drafted.replace("in Washed Slate is a washed lightweight jacket.", "in Slate is a washed bomber jacket.")
    assert descimport.old_colour(now, "Orbit Field Jacket in Washed Slate", "Orbit Field Jacket in Slate", drafted,
                                 "lightweight jacket", "bomber jacket") == ""


@pytest.mark.parametrize("line,held", [("Slate-tone trims", True), ("Blue-slate piping", True), ("Slates", False)])
def test_a_dropped_colour_word_in_a_hyphen_compound_holds(line, held):
    drafted = INTRO_SLATE.format("")
    now = drafted.replace("Jacket in Slate is", "Jacket in Navy is").replace("  <li>Zip fastening</li>",
                                                                             f"  <li>{line}</li>")
    got = descimport.old_colour(now, "Orbit Field Jacket in Slate", "Orbit Field Jacket in Navy", drafted)
    assert ("names 'slate'" in got) == held


@pytest.mark.parametrize("drafted,final,line,held", [
    ("Loop Coat in Off-White", "Loop Coat in White", "Zip-off hood", False),   # 'off' is no colour of its own
    ("Loop Coat in Two-Tone Black", "Loop Coat in Black", "Two-way zip", False),
    ("Loop Coat in Two-Tone Black", "Loop Coat in Black", "Gold-tone hardware", False),
    ("Loop Coat in Off-White", "Loop Coat in White", "Off-white piping", True),   # the compound whole
    ("Loop Coat in Two-Tone Black", "Loop Coat in Black", "Two-tone piping", True),
    ("Loop Coat in Slate", "Loop Coat in Navy", "Slate-tone trims", True),   # a single word as one part
    ("Loop Coat in Slate", "Loop Coat in Navy", "Blue-slate piping", True),
    ("Loop Coat in Slate", "Loop Coat in Navy", "Slates", False),   # a word character blocks
    ("Loop Coat in Off-White", "Loop Coat in Off White", "Off-white piping", False),   # respelt, not gone
    # Accepted residue: 'Off White' written with a space leaves 'off' gone on its own, so this holds (and never writes)
    ("Loop Coat in Off White", "Loop Coat in White", "Zip-off hood", True),
])
def test_a_dropped_colour_is_read_as_the_title_writes_it(drafted, final, line, held):
    was = (f"<p>ACME STUDIO's {drafted} is a coat.</p>\n<ul>\n<li>Composition: 100% Wool</li>\n"
           "  <li>Zip fastening</li>\n</ul><br><p>SKU: X 1</p>")
    now = was.replace(drafted, final).replace("  <li>Zip fastening</li>", f"  <li>{line}</li>")
    assert bool(descimport.old_colour(now, drafted, final, was)) == held


def test_the_sheet_keeps_the_drafted_description_whole(tmp_path):
    path, _ = reviewed(tmp_path)   # the draft as written, its Descriptions sheet not edited
    sheet = poxlsx.read_descriptions(path)
    assert sheet and all(d["Description (as drafted)"] == d["Description"] for d in sheet)


def test_a_drafted_line_naming_the_old_colour_is_not_held_when_the_title_colour_is_fixed(tmp_path):
    def edit(ws):   # as if the draft wrote a line from the order's own words that names the colour
        c = desc_cell(ws, td.JACKET)
        c.value = c.value.replace("  <li>Zip fastening</li>", "  <li>Zip fastening</li>\n  <li>Slate tone shell</li>")
        ws.cell(c.row, poxlsx.DESCRIPTION_COLS.index("Description (as drafted)") + 1).value = c.value
    path, _ = reviewed(tmp_path, edit)
    po_edit(path, td.JACKET, "Title", lambda v: "Orbit Field Jacket in Navy")
    run(tmp_path, path)
    jacket = by_code(tmp_path)[td.JACKET]
    assert "  <li>Slate tone shell</li>" in jacket and "  <li>Navy colourway</li>" in jacket


@pytest.mark.parametrize("drafted,final,line", [
    ("Loop Boot in Black Leather", "Loop Boot in Black", "  <li>Leather upper</li>"),
    ("Loop Tote in Natural", "Loop Tote in Ecru", "  <li>Natural leather trims</li>"),
    ("Loop Ring in Gold", "Loop Ring in Silver", "  <li>Gold hardware</li>"),
    ("Loop Coat in Camel", "Loop Coat in Brown", "<li>Composition: 100% Camel Hair</li>"),
])
def test_a_colour_word_in_a_line_as_drafted_or_reconciled_from_the_po_does_not_hold(drafted, final, line):
    body = (f"<p>ACME STUDIO's {drafted} is a coat.</p>\n<ul>\n<li>Composition: 100% Wool</li>\n{line}\n</ul><br>"
            "<p>SKU: X 1</p>")
    now = body.replace(drafted, final)
    if line.startswith("<li>Composition:"):   # the merge wrote this line from the PO
        body, now = body.replace(line + "\n", ""), now.replace("<li>Composition: 100% Wool</li>", line[:-5] + "</li>")
    assert descimport.old_colour(now, drafted, final, body) == ""


@pytest.mark.parametrize("edit", [
    lambda b: b.replace("  <li>Zip fastening</li>", "  <li>Slate trims</li>"),   # typed by hand
    lambda b: b.replace("</ul><br>", "</ul><br><p>Also the Orbit Field Jacket in Slate.</p>"),   # the drafted title, moved
    lambda b: b.replace("is a waxed lightweight jacket.", "is a waxed lightweight jacket in slate."),   # the intro edited
])
def test_a_dropped_colour_word_in_a_part_the_draft_did_not_write_so_holds(edit):
    drafted = ("<p>ACME STUDIO's Orbit Field Jacket in Slate is a waxed lightweight jacket.</p>\n<ul>\n"
               "<li>Composition: 100% Cotton</li>\n  <li>Zip fastening</li>\n</ul><br><p>SKU: X 1</p>")
    now = edit(drafted.replace("Jacket in Slate is", "Jacket in Navy is"))
    assert "names 'slate'" in descimport.old_colour(now, "Orbit Field Jacket in Slate", "Orbit Field Jacket in Navy",
                                                    drafted)


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
