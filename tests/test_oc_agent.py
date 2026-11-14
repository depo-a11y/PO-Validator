"""Job 1, AI title drafting (oc/agent/titles.py), offline: a fake Anthropic client, a synthetic catalog, OC and past
PO. No real order data, no network."""
import copy
import io
import json
import re
import sys
import types

import openpyxl
import pytest

import test_oc_tool as base
from oc import catalog as catm, cli, draft as draftm, history as histm, housestyle, poxlsx, service
from oc.adapters import ai
from oc.agent import common, titles
from oc.resolve import AI_DRAFT, COLOUR

SENTINEL = "sk-ant-api03-SENTINEL-must-never-leak-0042"
SNEAKER_900 = "S99WS0001P0001 900"       # name green (live same style+fabric), colour words and family red
NEW_STYLE_101 = "S97XX0009P0001 101"     # name red (no sibling), colour words and family green (same fabric+colour)
BOOT_T0013 = "S96BT0001P0099 T0013"      # name green ("Tabi Ankle Boots 8cm"), colour red
BOOT_PAST = "S96BT0002P0099 T0049"       # name red; a past PO titles the same style "... 8cm ..."
NEW_CODES = [SNEAKER_900, NEW_STYLE_101, BOOT_T0013, BOOT_PAST]


# ---- the synthetic world ------------------------------------------------------------------
def write_catalog(tmp_path, extra=()):
    rows = []
    for i, size in enumerate(["40", "41", "42"]):
        rows.append(base._variant("1", "Replica Sneaker in White", "MEN SHOES SNEAKERS", "Active", "S99WS0001P0001 101",
                                  "WHITE", size, f"ZXFW26{100 + i}", "800.00", "800.00", "310.00", i == 0))
    rows.append(base._variant("2", "Replica Sneaker in White", "MEN SHOES SNEAKERS", "Archived", "S99WS0001P0001 101",
                              "WHITE", "40", "ZXSS25100", "900.00", "900.00", "400.00", True))
    rows.append(base._variant("3", "Logo Tee in Black", "MEN CLOTHING T-SHIRTS SHORT SLEEVE", "Active",
                              "S98GC0003S00003 900", "BLACK", "M", "ZXFW26200", "560.00", "800.00", "250.00", True))
    rows.append(base._variant("4", "Tabi Ankle Boots 8cm in Neutral", "WOMEN SHOES BOOTS", "Active",
                              "S96BT0001P0099 T0049", "BEIGE", "38", "ZXFW26300", "1500.00", "1500.00", "600.00", True))
    rows += list(extra)
    p = tmp_path / "catalog.csv"
    import csv
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=base.CAT_COLS)
        w.writeheader()
        w.writerows(rows)
    return str(p)


BRAND = dict(base.BRAND, colour_families={"BLACK": ["black"], "WHITE": ["white"], "BEIGE": ["beige", "neutral"]},
             product_class_types={"SNEAKERS": "SHOES SNEAKERS", "ANKLE BOOT": "SHOES BOOTS"}, _name="mm", _path="t",
             heel_codes=True)


def _line(n, style, fabric, colour, pclass, sizes, price):
    q = sum(sizes.values())
    return dict(line_no=n, style=style, fabric=fabric, colour=colour, product_class=pclass, sizes=sizes, qty=q,
                price=price, line_value=q * price)


def docs():
    men = [_line(1, "S99WS0001", "P0001", "101", "SNEAKERS", {"41": 1}, 200.0),
           _line(2, "S99WS0001", "P0001", "900", "SNEAKERS", {"41": 2, "42": 2}, 200.0),
           _line(3, "S97XX0009", "P0001", "101", "SNEAKERS", {"42": 1}, 200.0)]
    women = [_line(1, "S96BT0001", "P0099", "T0013", "ANKLE BOOT", {"38": 1}, 500.0),
             _line(2, "S96BT0002", "P0099", "T0049", "ANKLE BOOT", {"39": 2}, 500.0)]
    out = []
    for doc, coll, gender, lines in (("SS - 1", "MM MALE SHOES AVP", "MEN", men),
                                     ("SS - 2", "MM FEMALE SHOES AVP", "WOMEN", women)):
        out.append(dict(doc=doc, collection=coll, gender=gender, currency="European Euro", label="MM", lines=lines,
                        total_qty=sum(l["qty"] for l in lines), net=sum(l["line_value"] for l in lines)))
    return out


def past_po(tmp_path, rows=None):
    """A past season PO: one NEW product on a style the catalog lacks (titled with a height), one on the OC."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Command", poxlsx.CODE, poxlsx.SEASON, "Title", "Type", "Option1 Value", "Option2 Value"])
    for r in rows or [("NEW", "S96BT0002P0099 T0013", "F/W 26", "Tabi Ankle Boots 8cm in Black", "WOMEN SHOES BOOTS",
                       "BLACK", "38"),
                      ("NEW", "S11ZZ0001P9999 900", "F/W 26", "Old Logo Sneaker in Black", "MEN SHOES SNEAKERS",
                       "BLACK", "41")]:
        ws.append(list(r))
    p = tmp_path / "past.xlsx"
    wb.save(p)
    return histm.load([dict(label="F/W 26", path=str(p))])


DEFAULT = {
    SNEAKER_900: dict(product_name="Replica Leather Sneaker", colour_words="Black", colour_family="BLACK",
                      basis="name from the live style; colour from other fabrics"),
    NEW_STYLE_101: dict(product_name="Paint Sneaker", colour_words="Optic White", colour_family="WHITE",
                        basis="cart style name; colour from the same fabric and colour"),
    BOOT_T0013: dict(product_name="Tabi Ankle Boots H80", colour_words="Black", colour_family="BLACK",
                     basis="same style live title 8cm; T0013 is black"),
    BOOT_PAST: dict(product_name="Tabi Ankle Boots H80", colour_words="Neutral", colour_family="BEIGE",
                    basis="past PO title of the same style with 8cm"),
}


def message(data=None, text=None, stop="end_turn", category=None, model=ai.MODEL, usage=(1000, 0, 0, 500)):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="thinking", thinking=""),
                 types.SimpleNamespace(type="text", text=text if text is not None else json.dumps(data))],
        stop_reason=stop, model=model,
        stop_details=types.SimpleNamespace(type="refusal", category=category, explanation=None) if category else None,
        usage=types.SimpleNamespace(input_tokens=usage[0], cache_creation_input_tokens=usage[1],
                                    cache_read_input_tokens=usage[2], output_tokens=usage[3], iterations=None))


class Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.msg


def sent_codes(req):
    body = req["messages"][0]["content"][0]["text"]
    return [p["code"] for p in json.loads(body.split("\n", 1)[1])["products"]]


def sent_evidence(req):
    return json.loads(req["messages"][0]["content"][0]["text"].split("\n", 1)[1])["products"]


class Fake:
    """Answers each request from `answers` (code -> fields), or by `reply(request, n)`. Records every request."""

    def __init__(self, answers=None, reply=None, error=None):
        self.answers = {**DEFAULT, **(answers or {})}
        self.reply, self.error = reply, error
        self.calls, self.keys = [], []
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=self._stream))

    def factory(self, key):
        self.keys.append(key)
        return self

    def _stream(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        if self.reply:
            return Stream(self.reply(kw, len(self.calls)))
        prods = [dict(code=c, **self.answers[c]) for c in sent_codes(kw) if c in self.answers]
        return Stream(message(dict(products=prods)))


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(ai.KEY_NAME, SENTINEL)


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv(ai.KEY_NAME, raising=False)
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets={}))


def build(tmp_path, fake=None, policy="non-green", hist=None, cat_extra=(), d=None, brand=BRAND):
    cat = catm.load(write_catalog(tmp_path, cat_extra))
    hist = hist if hist is not None else past_po(tmp_path)
    titler = None if fake is None else (lambda p, R: titles.run(p, R, factory=fake.factory, policy=policy))
    return draftm.build(d or docs(), brand, cat, hist, base._tags(), titler=titler)


def cells(review, code, column):
    return [x for x in review if x["Code"] == code and x["Column"] == column]


def one(review, code, column):
    vals = {(x["Value"], x["Source"], x["Colour"], x["Why"]) for x in cells(review, code, column)}
    assert len(vals) == 1, vals
    return dict(zip(("Value", "Source", "Colour", "Why"), next(iter(vals))))


def agent_row(checks):
    rows = [c for c in checks if c["Check"] == titles.CHECK]
    assert len(rows) == 1, checks
    return rows[0]


# ---- the key gate -----------------------------------------------------------------------------
def test_no_key_sends_nothing_and_the_draft_is_exactly_the_tools_own(tmp_path, no_key):
    fake = Fake()
    with_titler = build(tmp_path, fake)
    without = build(tmp_path)
    assert fake.keys == [] and fake.calls == []
    assert with_titler == without          # rows, review, checks and fills, every value
    assert not [c for c in with_titler[2] if c["Check"] == titles.CHECK]


def test_no_key_the_service_draft_is_cell_for_cell_the_same(tmp_path, no_key, monkeypatch):
    monkeypatch.setattr(service, "adapter", lambda cfg: types.SimpleNamespace(parse_checked=lambda p: docs()))
    fake = Fake()
    a = service.draft("oc.pdf", write_catalog(tmp_path), BRAND, ai_factory=fake.factory, ai_titles=True)
    b = service.draft("oc.pdf", write_catalog(tmp_path), BRAND, ai_factory=fake.factory, ai_titles=False)
    assert fake.calls == [] and fake.keys == []
    assert a[2] == b[2] and a[1].equals(b[1]) and workbook(a[0]) == workbook(b[0])
    assert service.agent_ready() is False


def workbook(data):
    wb = openpyxl.load_workbook(io.BytesIO(data))
    return [(ws.title, c.coordinate, c.value, c.fill.fgColor.rgb if c.fill.fill_type else None)
            for ws in wb.worksheets for row in ws.iter_rows() for c in row]


def test_the_gate_is_the_key_and_the_client_gets_it(key):
    fake = Fake()
    client, k = common.connect(fake.factory)
    assert client is fake and fake.keys == [SENTINEL] and k == SENTINEL


# ---- what is drafted, and how it is shaded ------------------------------------------------------
def test_an_ai_value_never_fills_a_cell_finalize_would_refuse(tmp_path, key):
    # review buyer-14 F1: the tool's title is 'X in <colour code>' and its Option1 is blank; the AI answer leaves both
    # as they were, red, with its suggestion in the Why, and finalize still refuses them
    fake = Fake()
    rows, review, checks, fills = build(tmp_path, fake)
    t = one(review, SNEAKER_900, "Title")
    assert t["Value"] == "Replica Sneaker in 900" and (t["Source"], t["Colour"]) == ("guess", "red")
    assert ("AI suggests 'Replica Sneaker in Black': not filled in, because the tool's title holds the raw code 900, "
            "which finalize refuses") in t["Why"]
    assert "AI suggests product name 'Replica Leather Sneaker'" in t["Why"]
    f = one(review, SNEAKER_900, "Option1 Value")
    assert (f["Value"], f["Source"], f["Colour"]) == ("", "guess", "red")
    assert "AI suggests 'BLACK': not filled in, because the colour family is blank, which finalize refuses" in f["Why"]
    assert one(review, SNEAKER_900, poxlsx.TITLE_TAG)["Colour"] == "red"
    assert "4 AI value(s) left in the Why for a person to type" in agent_row(checks)["Detail"]   # 900 and T0013: title, family
    # a non-green cell finalize would accept is still drafted, amber
    n = one(review, NEW_STYLE_101, "Title")
    assert (n["Value"], n["Source"], n["Colour"]) == ("Paint Sneaker in White", AI_DRAFT, "amber")
    for r in rows:
        if r["Command"] == "NEW":
            r["Variant Price"] = 800.0
    po = tmp_path / "po.xlsx"
    poxlsx.write_po(str(po), poxlsx.PO_HEADER, rows)
    from oc import finalize
    fchecks = finalize.run(str(po), BRAND, catm.load(write_catalog(tmp_path)), str(tmp_path / "x-"))
    fails = {c["Check"]: str(c["Detail"]) for c in fchecks if c["Result"] == "FAIL"}
    assert "'Replica Sneaker in 900' (900)" in fails["No NEW Title still holds a raw style/fabric/colour code"]
    assert "Replica Sneaker in 900" in fails["Every NEW row has an Option1 (colour family)"]


def test_a_title_another_code_shares_or_a_live_handle_is_left_for_a_person(tmp_path, key):
    two = [dict(doc="SS - 1", collection="MM MALE SHOES AVP", gender="MEN", currency="European Euro", label="MM",
                lines=[_line(1, "S97XX0009", "P0001", "101", "SNEAKERS", {"42": 1}, 200.0),
                       _line(2, "S97XX0010", "P0001", "101", "SNEAKERS", {"42": 1}, 200.0)], total_qty=2, net=400.0)]
    names = {"S97XX0009P0001 101": dict(product_name="Paint Sneaker", colour_words="", colour_family="", basis="b"),
             "S97XX0010P0001 101": dict(product_name="Court Sneaker", colour_words="", colour_family="", basis="b")}
    rows, review, checks, fills = build(tmp_path, Fake(answers=names), d=two)
    for code, ai_title in (("S97XX0009P0001 101", "Paint Sneaker in White"), ("S97XX0010P0001 101", "Court Sneaker in White")):
        t = one(review, code, "Title")
        assert t["Value"] == "Sneakers in White" and t["Colour"] == "red"
        assert f"AI suggests '{ai_title}': not filled in, because the tool's title is shared with another code" in t["Why"]
    live = base._variant("9", "Sneakers in White", "MEN SHOES SNEAKERS", "Active", "S00XX0001P0009 1", "WHITE", "41",
                         "ZXFW26900", "800.00", "800.00", "310.00", True)
    rows, review, checks, fills = build(tmp_path, Fake(), cat_extra=[live])
    t = one(review, NEW_STYLE_101, "Title")
    assert t["Value"] == "Sneakers in White" and t["Colour"] == "red"
    assert "not filled in, because the tool's title is a live product's handle, which finalize refuses" in t["Why"]


def test_a_cell_left_for_a_person_never_suggests_its_own_failing_value(tmp_path, key):
    live = base._variant("9", "Sneakers in White", "MEN SHOES SNEAKERS", "Active", "S00XX0001P0009 1", "WHITE", "41",
                         "ZXFW26900", "800.00", "800.00", "310.00", True)
    same = {NEW_STYLE_101: dict(product_name="  sneakers ", colour_words="", colour_family="", basis="b")}
    rows, review, checks, fills = build(tmp_path, Fake(answers=same), cat_extra=[live])
    t = one(review, NEW_STYLE_101, "Title")
    assert t["Value"] == "Sneakers in White" and t["Colour"] == "red"
    assert "AI suggests" not in t["Why"] and "not filled in" not in t["Why"]
    left = lambda checks: int(re.search(r"(\d+) AI value\(s\) left in the Why", agent_row(checks)["Detail"])[1])
    rows, review, other, fills = build(tmp_path, Fake(), cat_extra=[live])
    assert "AI suggests 'Paint Sneaker in White': not filled in" in one(review, NEW_STYLE_101, "Title")["Why"]
    assert left(checks) == left(other) - 1
    # a part the AI repeats is not suggested either; a part it changes still is
    for name, shown in (("replica   SNEAKER", None), ("Replica Leather Sneaker", "Replica Leather Sneaker")):
        rows, review, checks, fills = build(tmp_path, Fake(answers={
            SNEAKER_900: dict(product_name=name, colour_words="", colour_family="", basis="b")}), policy="everywhere")
        t = one(review, SNEAKER_900, "Title")
        assert t["Value"] == "Replica Sneaker in 900" and t["Colour"] == "red"
        if shown:
            assert f"AI suggests product name '{shown}': not filled in" in t["Why"]
        else:
            assert "AI suggests" not in t["Why"] and "not filled in" not in t["Why"]


def test_a_green_value_is_kept_and_a_different_ai_value_named_in_the_why(tmp_path, key):
    rows, review, checks, fills = build(tmp_path, Fake())
    t = one(review, NEW_STYLE_101, "Title")
    assert t["Value"] == "Paint Sneaker in White" and t["Source"] == AI_DRAFT  # name drafted, colour words kept
    assert "AI suggests colour words 'Optic White'" in t["Why"]
    f = one(review, NEW_STYLE_101, "Option1 Value")
    assert (f["Value"], f["Source"], f["Colour"]) == ("WHITE", "catalog", "green")
    b = one(review, BOOT_T0013, "Title")   # house style wrote the live 8cm as H80; the raw colour code is left for a person
    assert b["Value"] == "Tabi Ankle Boots H80 in T0013" and "'8cm' written H80 (house heel code)" in b["Why"]
    assert "AI suggests 'Tabi Ankle Boots H80 in Black': not filled in" in b["Why"]


def test_the_measurement_policy_everywhere_also_replaces_green(tmp_path, key):
    rows, review, checks, fills = build(tmp_path, Fake(), policy="everywhere")
    assert one(review, NEW_STYLE_101, "Title")["Value"] == "Paint Sneaker in Optic White"
    # ... but never a cell finalize would refuse
    b = one(review, BOOT_T0013, "Title")
    assert b["Value"] == "Tabi Ankle Boots H80 in T0013" and "AI suggests 'Tabi Ankle Boots H80 in Black'" in b["Why"]
    t = one(review, SNEAKER_900, "Title")
    assert t["Value"] == "Replica Sneaker in 900" and "AI suggests 'Replica Leather Sneaker in Black'" in t["Why"]
    assert titles.run.__defaults__[-1] == "non-green"


def test_a_title_keeping_a_red_part_of_the_tool_stays_red(tmp_path, key):
    # the AI drafts the name but gives no colour words: the colour part is still the tool's raw code, so the title is
    # left for a person (the suggestion would still hold the code, so only the name is suggested)
    rows, review, checks, fills = build(tmp_path, Fake(answers={
        SNEAKER_900: dict(product_name="Replica Leather Sneaker", colour_words="", colour_family="", basis="b")}),
        policy="everywhere")
    t = one(review, SNEAKER_900, "Title")
    assert t["Value"] == "Replica Sneaker in 900" and (t["Source"], t["Colour"]) == ("guess", "red")
    assert "AI suggests product name 'Replica Leather Sneaker': not filled in, because the tool's title holds" in t["Why"]
    assert one(review, SNEAKER_900, poxlsx.TITLE_TAG)["Colour"] == "red"
    # AI colour words on the tool's guessed name, a title finalize would accept: drafted, but red while the name is a guess
    rows, review, checks, fills = build(tmp_path, Fake(answers={
        NEW_STYLE_101: dict(product_name="", colour_words="Optic White", colour_family="WHITE", basis="b")}),
        policy="everywhere")
    t = one(review, NEW_STYLE_101, "Title")
    assert t["Value"] == "Sneakers in Optic White" and t["Colour"] == "red"
    assert "the product name is still the tool's (guess)" in t["Why"]


def test_an_ai_value_never_turns_green_even_when_it_matches(tmp_path, key):
    rows, review, checks, fills = build(tmp_path, Fake(answers={
        NEW_STYLE_101: dict(product_name="Sneakers", colour_words="White", colour_family="WHITE", basis="b")}))
    for col in ("Title", poxlsx.TITLE_TAG):
        assert one(review, NEW_STYLE_101, col)["Colour"] == "amber"


def test_only_title_colour_words_and_family_change(tmp_path, key):
    before = build(tmp_path)
    after = build(tmp_path, Fake())
    touched = {"Title", "Option1 Value", poxlsx.TITLE_TAG}
    for r0, r1 in zip(before[0], after[0]):
        assert {k: v for k, v in r0.items() if k not in touched} == {k: v for k, v in r1.items() if k not in touched}
    assert len(before[0]) == len(after[0])


def test_the_heel_code_needs_a_height_in_the_evidence(tmp_path, key):
    rows, review, checks, fills = build(tmp_path, Fake(answers={
        NEW_STYLE_101: dict(product_name="Paint Sneaker H30", colour_words="White", colour_family="WHITE", basis="b")}))
    assert one(review, BOOT_PAST, "Title")["Value"] == "Tabi Ankle Boots H80 in Neutral"   # past PO says 8cm
    t = one(review, NEW_STYLE_101, "Title")
    assert t["Value"] == "Sneakers in White" and "heel code H30 has no height in the evidence" in t["Why"]
    assert "product name: heel code without a height x1" in agent_row(checks)["Detail"]


def test_a_heel_code_sits_right_before_in(tmp_path, key):
    rows, review, checks, fills = build(tmp_path, Fake(answers={BOOT_PAST: dict(
        product_name="Tabi H80 Ankle Boots", colour_words="Neutral", colour_family="BEIGE", basis="b")}))
    t = one(review, BOOT_PAST, "Title")
    assert t["Value"] == "Ankle Boot in Neutral" and "heel code H80 is not right before ' in'" in t["Why"]


BAD_NAMES = [
    ("Replica T0123 Sneaker", "'Replica T0123 Sneaker' rejected (1 code-like token(s)"),
    ("Replica M01234", "'Replica M01234' rejected (1 code-like token(s)"),
    ("Sneaker P0001", "raw code P0001 from the manufacture code"),
    ("S97XX0009 Sneaker", "raw code S97XX0009 from the manufacture code"),
    ("Maison Margiela Sneaker", "brand word(s) MAISON, MARGIELA"),
    ("Margiela Paint Sneaker", "brand word(s) MARGIELA"),
    ("Sneaker <b>Bold</b>", "outside letters, digits"),
    ("=HYPERLINK(1)", "starts with '='"),
    ("-Sneaker", "starts with '-'"),
    ("+Sneaker", "starts with '+'"),
    ("@Sneaker", "starts with '@'"),
    ("Sneaker " + "x" * 60, "longer than 60 characters"),
    ("Paint Sneaker H55", "heel code H55 is not one of H30, H50, H60, H80"),
    ("Paint Sneaker H100", "heel code H100 is not one of"),
    ("Womens Paint Sneaker", "starts with 'Womens'"),
    ("Sneaker in Leather", "holds the word 'in'"),
    ("Paint Sneaker 123", "'Paint Sneaker 123' rejected (1 code-like token(s)"),
    ("Paint Sneaker see evil-shop.com", "holds a link or a web address"),
    ("Sneaker 0400 123 456", "holds a phone number"),
]


@pytest.mark.parametrize("bad,reason", BAD_NAMES)
def test_a_bad_product_name_falls_back_to_the_tools_with_the_reason(tmp_path, key, bad, reason):
    rows, review, checks, fills = build(tmp_path, Fake(answers={
        NEW_STYLE_101: dict(product_name=bad, colour_words="White", colour_family="WHITE", basis="b")}))
    t = one(review, NEW_STYLE_101, "Title")
    assert t["Value"] == "Sneakers in White" and reason in t["Why"], t
    assert t["Source"] != AI_DRAFT and "rejected" in t["Why"]
    assert "1 AI value(s) rejected" in agent_row(checks)["Detail"] or "rejected by the tool's checks" in agent_row(checks)["Detail"]


def test_a_rejected_value_holding_a_link_is_never_echoed(tmp_path, key):
    rows, review, checks, fills = build(tmp_path, Fake(answers={NEW_STYLE_101: dict(
        product_name="Sneaker see evil-shop.com", colour_words="White", colour_family="WHITE", basis="b")}))
    t = one(review, NEW_STYLE_101, "Title")
    assert "AI product name (not shown) rejected (holds a link or a web address" in t["Why"] and "evil" not in t["Why"]


ECHO_PROBES = ["see https://evil.example/x1", "<b>evil1</b>", "evil9.example", "evil1@example.com"]


@pytest.mark.parametrize("field", ["product_name", "colour_words", "colour_family"])
@pytest.mark.parametrize("probe", ECHO_PROBES)
def test_a_rejected_title_part_or_family_is_never_echoed(tmp_path, key, field, probe):
    ans = dict(product_name="Paint Sneaker", colour_words="White", colour_family="WHITE", basis="b")
    ans[field] = probe
    rows, review, checks, fills = build(tmp_path, Fake(answers={NEW_STYLE_101: ans}))
    cell = one(review, NEW_STYLE_101, "Option1 Value" if field == "colour_family" else "Title")
    assert "(not shown) rejected (" in cell["Why"], cell
    assert "evil" not in json.dumps([rows, review, checks], default=str).lower()
    assert not {"<", ">", "@"} & set(cell["Why"]), cell   # not even the characters it was rejected for


BAD_COLOURS = [("900", "raw code 900"), ("T0013", "raw code T0013") , ("Black H80", "heel code H80 in the colour words"),
               ("Maison Black", "brand word(s) MAISON"), ("Black" + "k" * 40, "longer than 40 characters"),
               ("=Black", "starts with '='"), ("Black (Shiny)", "outside letters, digits")]


@pytest.mark.parametrize("bad,reason", BAD_COLOURS)
def test_bad_colour_words_fall_back(tmp_path, key, bad, reason):
    code = SNEAKER_900 if bad != "T0013" else BOOT_T0013
    rows, review, checks, fills = build(tmp_path, Fake(answers={
        code: dict(product_name="Replica Sneaker", colour_words=bad, colour_family="BLACK", basis="b")}))
    t = one(review, code, "Title")
    assert t["Source"] != AI_DRAFT and reason in t["Why"] and "rejected" in t["Why"], t


def test_a_colour_family_outside_the_brands_is_rejected(tmp_path, key):
    rows, review, checks, fills = build(tmp_path, Fake(answers={
        SNEAKER_900: dict(product_name="Replica Sneaker", colour_words="Black", colour_family="NOIR", basis="b")}))
    f = one(review, SNEAKER_900, "Option1 Value")
    assert f["Value"] == "" and f["Source"] == "guess" and "AI colour family 'NOIR' rejected (not one of the brand's colour families)" in f["Why"]


def test_titles_from_ai_meet_the_same_dedupe_and_handle_checks(tmp_path, key):
    taken = base._variant("9", "Paint Sneaker in White", "MEN SHOES SNEAKERS", "Active", "S00XX0001P0009 1",
                          "WHITE", "41", "ZXFW26900", "800.00", "800.00", "310.00", True)
    rows, review, checks, fills = build(tmp_path, Fake(), cat_extra=[taken])
    t = one(review, NEW_STYLE_101, "Title")
    assert t["Value"] == "Paint Sneaker in White" and t["Colour"] == "red"
    assert any(c["Check"] == "NEW title does not collide with a live handle" and c["Result"] == "FAIL" for c in checks)


def _pair(*lines):
    return [dict(doc=f"SS - {i}", collection=c, gender=g, currency="European Euro", label="MM", lines=[l], total_qty=1,
                 net=200.0) for i, (c, g, l) in enumerate(lines, 1)]


def _active_twin(title, handle):
    """An ACTIVE product of another style already carrying `title`, under a handle the title would not make."""
    twin = base._variant("9", title, "MEN SHOES SNEAKERS", "Active", "S00XX0001P0009 1", "WHITE", "41", "ZXFW26900",
                         "800.00", "800.00", "310.00", True)
    twin["Handle"] = handle
    return twin


def test_an_ai_title_already_on_an_active_product_keeps_its_source_and_both_reasons(tmp_path, key):
    rows, review, checks, fills = build(tmp_path, Fake(), cat_extra=[_active_twin("Paint Sneaker in White", "zz-twin")])
    t = one(review, NEW_STYLE_101, "Title")
    assert (t["Value"], t["Source"], t["Colour"]) == ("Paint Sneaker in White", AI_DRAFT, "amber")
    assert "AI-drafted from" in t["Why"] and "an ACTIVE live product already has this title: /zz-twin" in t["Why"]
    (w,) = [c for c in checks if c["Check"] == housestyle.LIVE_TITLE_CHECK]
    assert w["Result"] == "WARN" and f"'Paint Sneaker in White' ({NEW_STYLE_101}): /zz-twin" in w["Detail"]
    assert not [c for c in checks if c["Result"] == "FAIL" and "handle" in c["Check"]]   # a WARN, not a gate
    # an AI title still red for a part of the tool's keeps red; the same check names it
    rows, review, checks, fills = build(tmp_path, Fake(answers={NEW_STYLE_101: dict(
        product_name="", colour_words="Optic White", colour_family="WHITE", basis="b")}), policy="everywhere",
        cat_extra=[_active_twin("Sneakers in Optic White", "zz-twin-2")])
    t = one(review, NEW_STYLE_101, "Title")
    assert (t["Value"], t["Colour"]) == ("Sneakers in Optic White", "red")
    assert "the product name is still the tool's" in t["Why"] and "already has this title: /zz-twin-2" in t["Why"]


def test_ai_titles_run_on_a_draft_from_the_cart_alone(tmp_path, key):
    import test_oc_cartorder as tco
    from oc import cartorder
    docs, cart = cartorder.read(tco.write(tmp_path / "cart.xlsx"))
    extra = [("7", "zz-live-jacket", "Shell Test Jacket in Navy", "S97AA0007M00007 Q7",
              "WOMEN CLOTHING JACKETS LIGHTWEIGHT", "Active")]
    fake = Fake(answers={tco.JACKET: dict(product_name="Shell Test Jacket", colour_words="Navy", colour_family="NAVY",
                                          basis="b")})
    rows, review, checks, fills = draftm.build(
        docs, tco.BRAND, catm.load(tco.catalog_csv(tmp_path, extra)), histm.History([]), base._tags(), cart=cart,
        titler=lambda p, R: titles.run(p, R, factory=fake.factory))
    ev = {e["code"]: e for req in fake.calls for e in sent_evidence(req)}
    assert tco.JACKET in ev and ev[tco.JACKET]["cart"] is not None and ev[tco.JACKET]["oc_line"]["product_class"] == ""
    t = next(x for x in review if x["Code"] == tco.JACKET and x["Column"] == "Title")
    assert (t["Value"], t["Source"], t["Colour"]) == ("Shell Test Jacket in Navy", AI_DRAFT, "amber")
    assert "/zz-live-jacket" in t["Why"]
    assert [c["Result"] for c in checks if c["Check"] == housestyle.LIVE_TITLE_CHECK] == ["WARN"]


def test_a_gender_prefix_keeps_the_ai_source(tmp_path, key):
    same = dict(product_name="Tabi Ankle Boots", colour_words="", colour_family="", basis="b")
    d = _pair(("MM MALE SHOES AVP", "MEN", _line(1, "S97XX0009", "P0001", "101", "SNEAKERS", {"42": 1}, 200.0)),
              ("MM FEMALE SHOES AVP", "WOMEN", _line(1, "S95YY0001", "P0001", "101", "SNEAKERS", {"38": 1}, 200.0)))
    rows, review, checks, fills = build(tmp_path, Fake(answers={"S97XX0009P0001 101": same, "S95YY0001P0001 101": same}), d=d)
    w = one(review, "S95YY0001P0001 101", "Title")
    assert w["Value"] == "Womens Tabi Ankle Boots in White" and w["Source"] == AI_DRAFT and w["Colour"] == "amber"
    assert "'Womens' prefix keeps it apart" in w["Why"]
    m = one(review, "S97XX0009P0001 101", "Title")
    assert m["Value"] == "Tabi Ankle Boots in White" and m["Source"] == AI_DRAFT
    # two women's products the AI gave one title: the prefix cannot part them, so both go red and the draft FAILs
    d = _pair(("MM FEMALE SHOES AVP", "WOMEN", _line(1, "S95YY0001", "P0001", "101", "SNEAKERS", {"38": 1}, 200.0)),
              ("MM FEMALE SHOES AVP", "WOMEN", _line(1, "S95YY0002", "P0001", "101", "ANKLE BOOT", {"38": 1}, 200.0)))
    rows, review, checks, fills = build(tmp_path, Fake(answers={"S95YY0001P0001 101": same, "S95YY0002P0001 101": same}), d=d)
    assert {one(review, c, "Title")["Value"] for c in ("S95YY0001P0001 101", "S95YY0002P0001 101")} == \
        {"Womens Tabi Ankle Boots in White"}
    assert {one(review, c, "Title")["Colour"] for c in ("S95YY0001P0001 101", "S95YY0002P0001 101")} == {"red"}
    assert any(c["Check"] == "One manufacture code per NEW title" and c["Result"] == "FAIL" for c in checks)


def test_a_gender_prefix_keeps_an_ai_title_red_when_a_tool_part_is_still_a_guess(tmp_path, key):
    men = [_line(1, "S97XX0009", "P0001", "101", "SNEAKERS", {"42": 1}, 200.0)]
    women = [_line(1, "S95YY0001", "P0001", "101", "SNEAKERS", {"38": 1}, 200.0)]
    d = [dict(doc=n, collection=c, gender=g, currency="European Euro", label="MM", lines=ls, total_qty=1, net=200.0)
         for n, c, g, ls in (("SS - 1", "MM MALE SHOES AVP", "MEN", men), ("SS - 2", "MM FEMALE SHOES AVP", "WOMEN", women))]
    colour_only = dict(product_name="", colour_words="Optic White", colour_family="WHITE", basis="b")
    rows, review, checks, fills = build(tmp_path, Fake(answers={"S97XX0009P0001 101": colour_only,
                                                                "S95YY0001P0001 101": colour_only}),
                                        policy="everywhere", d=d)
    w = one(review, "S95YY0001P0001 101", "Title")
    assert w["Value"] == "Womens Sneakers in Optic White" and "'Womens' prefix keeps it apart" in w["Why"]
    assert (w["Source"], w["Colour"]) == ("guess", "red")   # the name is still the tool's guess: the prefix keeps it red


def _men_women_clash():
    d = docs()
    d[0]["lines"] = d[0]["lines"][1:2]
    d[0]["total_qty"], d[0]["net"] = 4, 800.0
    d[1]["lines"] = d[1]["lines"][:1]
    d[1]["total_qty"], d[1]["net"] = 1, 500.0
    return d


# ---- the request ------------------------------------------------------------------------------
def _many(n):
    lines = [_line(i + 1, f"S97XX{i:04d}", "P0001", "101", f"SNEAKER {chr(65 + i)}", {"42": 1}, 200.0)
             for i in range(n)]   # one product class each, so no two of the tool's titles are shared
    return [dict(doc="SS - 1", collection="MM MALE SHOES AVP", gender="MEN", currency="European Euro", label="MM",
                 lines=lines, total_qty=n, net=200.0 * n)]


def test_the_few_shot_block_is_byte_identical_on_every_chunk_and_cached(tmp_path, key):
    fake = Fake(reply=lambda req, n: message(dict(products=[])))
    build(tmp_path, fake, d=_many(titles.CHUNK * 2 + 3))
    assert len(fake.calls) == 3
    blocks = [c["system"][1] for c in fake.calls]
    assert all(b == blocks[0] for b in blocks) and blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "Old Logo Sneaker in Black" in blocks[0]["text"]
    assert all(c["system"][0] == {"type": "text", "text": titles.system(BRAND)} for c in fake.calls)
    assert [len(sent_codes(c)) for c in fake.calls] == [titles.CHUNK, titles.CHUNK, 3]


def test_the_request_is_pinned(tmp_path, key):
    fake = Fake()
    build(tmp_path, fake)
    (req,) = fake.calls
    assert req["model"] == "claude-opus-5-5" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert req["fallbacks"] == "default" and req["output_config"]["effort"] == titles.EFFORT
    assert "thinking" not in req and req["max_tokens"] == titles.MAX_TOKENS
    s = req["output_config"]["format"]["schema"]
    item = s["properties"]["products"]["items"]
    assert s["additionalProperties"] is False and item["additionalProperties"] is False
    assert item["properties"]["colour_family"]["enum"] == ["BLACK", "WHITE", "BEIGE", ""]
    assert sorted(item["required"]) == ["basis", "code", "colour_family", "colour_words", "product_name"]
    assert req["messages"][-1]["role"] == "user"


def test_the_few_shot_never_holds_a_product_on_this_oc(tmp_path, key):
    hist = past_po(tmp_path, rows=[
        ("NEW", SNEAKER_900, "F/W 26", "Leaked Sneaker in Black", "MEN SHOES SNEAKERS", "BLACK", "41"),
        ("NEW", "S11ZZ0001P9999 900", "F/W 26", "Old Logo Sneaker in Black", "MEN SHOES SNEAKERS", "BLACK", "41")])
    fake = Fake()
    build(tmp_path, fake, hist=hist)
    block = fake.calls[0]["system"][1]["text"]
    assert "Old Logo Sneaker in Black" in block and "Leaked Sneaker" not in block and SNEAKER_900 not in block


def test_few_shot_examples_follow_the_house_rules_and_are_capped(tmp_path):
    rows = [("NEW", f"S1{i:07d}P1 900", "F/W 26", f"Tee Number {chr(65 + i % 26)}{chr(65 + i // 26 % 26)} in Black",
             f"MEN CLOTHING TYPE {i % 7}", "BLACK", "M") for i in range(400)]
    rows += [("NEW", "S20000001P1 900", "F/W 26", "Maison Margiela Tee in Black", "MEN X", "BLACK", "M"),
             ("NEW", "S20000002P1 900", "F/W 26", "Mens Tee in Black", "MEN X", "BLACK", "M"),
             ("NEW", "S20000003P1 900", "F/W 26", "Tee in Black", "MEN X", "NOIR", "M"),
             ("MERGE", "S20000004P1 900", "F/W 26", "Carry Tee in Black", "MEN X", "BLACK", "M")]
    hist = past_po(tmp_path, rows=rows)
    ex = titles.examples(hist, BRAND, [])
    assert len(ex) == titles.FEW_SHOT_CAP and len({e[1] for e in ex}) == 7
    assert not [e for e in ex if e[1] == "MEN X"]
    assert ex == titles.examples(hist, BRAND, [])


def test_the_model_gets_evidence_never_the_tools_answer(tmp_path, key):
    fake = Fake()
    build(tmp_path, fake)
    ev = {e["code"]: e for e in sent_evidence(fake.calls[0])}
    assert set(ev) == set(NEW_CODES)
    e = ev[SNEAKER_900]
    assert set(e) == {"code", "oc_line", "cart", "same_style_products", "same_style_past_pos",
                      "same_fabric_and_colour_code", "same_colour_code_other_fabrics"}
    assert e["oc_line"] == dict(style="S99WS0001", fabric="P0001", colour_code="900", product_class="SNEAKERS",
                                collection="MM MALE SHOES AVP", gender="MEN", type="MEN SHOES SNEAKERS",
                                description=None, composition="100% Calf Leather")
    assert [p["title"] for p in e["same_style_products"]] == ["Replica Sneaker in White", "Replica Sneaker in White"]
    assert e["same_style_products"][0]["status"] == "Active"
    assert e["same_colour_code_other_fabrics"] == dict(products=2, uses=[
        dict(colour_words="Black", colour_family="BLACK", catalog=1, past_pos=1)])
    assert ev[NEW_STYLE_101]["same_fabric_and_colour_code"] == [
        dict(colour_words="White", colour_family="WHITE", catalog=2, past_pos=0)]
    assert ev[BOOT_PAST]["same_style_past_pos"][0]["title"] == "Tabi Ankle Boots 8cm in Black"
    body = fake.calls[0]["messages"][0]["content"][0]["text"]
    assert "Replica Sneaker in 900" not in body and "Sneakers in White" not in body


def test_a_title_request_carries_only_what_the_page_says_it_sends(tmp_path, key):
    # The page's TITLES_SENDS and the README name every field below. A new field is sent only once they name it too.
    fake = Fake()
    build(tmp_path, fake)
    for req in fake.calls:
        for e in sent_evidence(req):
            assert set(e) == {"code", "oc_line", "cart", "same_style_products", "same_style_past_pos",
                              "same_fabric_and_colour_code", "same_colour_code_other_fabrics"}
            assert set(e["oc_line"]) == {"style", "fabric", "colour_code", "product_class", "collection", "gender", "type",
                                         "description", "composition"}
            assert e["cart"] is None or set(e["cart"]) == {"style_name", "color_description", "materials", "subcategory"}
            assert all(set(p) == {"code", "title", "colour_option", "type", "status"} for p in e["same_style_products"])
            assert all(set(p) == {"season", "code", "title", "type", "colour_family"} for p in e["same_style_past_pos"])
            for u in e["same_fabric_and_colour_code"] + e["same_colour_code_other_fabrics"]["uses"]:
                assert set(u) == {"colour_words", "colour_family", "catalog", "past_pos"}
        brand, words, fams, _, intro, cols, *examples = req["system"][1]["text"].splitlines()
        assert brand.startswith("Brand: ") and fams.startswith("Colour families") and words.startswith("Brand words")
        assert cols == "manufacture code | Type | colour family | Title" and len(examples) <= titles.FEW_SHOT_CAP == 200


# ---- house style follows the brand config for AI values too (review 2026-11-14-buyer-1) ----------------------
MULE = "S95ZZ0001P0001 101"   # invented: a heeled style whose OC product class prints its height


def _mule():
    return _pair(("MM FEMALE SHOES AVP", "WOMEN", _line(1, "S95ZZ0001", "P0001", "101", "GLIDE MULE 3CM", {"38": 1},
                                                         200.0)))


def _mule_answer(name, colour="White"):
    return Fake(answers={MULE: dict(product_name=name, colour_words=colour, colour_family="WHITE", basis="b")})


def test_with_heel_codes_an_ai_height_in_cm_is_written_as_the_house_heel_code(tmp_path, key):
    rows, review, checks, fills = build(tmp_path, _mule_answer("Glide Mule 3cm"), d=_mule())
    t = one(review, MULE, "Title")
    assert (t["Value"], t["Source"]) == ("Glide Mule H30 in White", AI_DRAFT)
    assert "product name by AI, '3cm' written H30 (house heel code)" in t["Why"]
    rows, review, checks, fills = build(tmp_path, _mule_answer("Glide Mule 10cm"), d=_mule())
    assert one(review, MULE, "Title")["Value"] == "Glide Mule 10cm in White"   # 10cm and up stay as printed
    rows, review, checks, fills = build(tmp_path, _mule_answer("Glide Mule 5cm"), d=_mule())   # no 5cm in the evidence
    t = one(review, MULE, "Title")
    assert t["Source"] != AI_DRAFT and "heel code H50 has no height in the evidence" in t["Why"]


def test_without_heel_codes_an_ai_heel_code_is_rejected_and_never_echoed_in_its_reason(tmp_path, key):
    plain = dict(BRAND, heel_codes=False)
    rows, review, checks, fills = build(tmp_path, _mule_answer("Glide Mule H30"), d=_mule(), brand=plain)
    t = one(review, MULE, "Title")
    assert (t["Value"], t["Source"]) == ("Glide Mule 3cm in White", "guess")   # the tool's value stays
    reason = t["Why"].split("rejected (", 1)[1].split("); the tool's kept", 1)[0]
    assert reason == "1 heel code(s), but this brand writes heel heights as printed (no heel_codes)"
    assert "product name: heel code, brand writes heights as printed x1" in agent_row(checks)["Detail"]
    rows, review, checks, fills = build(tmp_path, _mule_answer("Glide Mule 3cm"), d=_mule(), brand=plain)
    t = one(review, MULE, "Title")
    assert (t["Value"], t["Source"]) == ("Glide Mule 3cm in White", AI_DRAFT) and "house heel code" not in t["Why"]


def test_ai_colour_words_take_the_brands_colour_spelling(tmp_path, key):
    brand = dict(BRAND, colour_spelling={"Whyte": "White"})
    fake = Fake(answers={NEW_STYLE_101: dict(product_name="Paint Sneaker", colour_words="Optic Whyte",
                                             colour_family="WHITE", basis="b")})
    rows, review, checks, fills = build(tmp_path, fake, policy="everywhere", brand=brand)
    t = one(review, NEW_STYLE_101, "Title")
    assert (t["Value"], t["Source"]) == ("Paint Sneaker in Optic White", AI_DRAFT)
    assert "colour words by AI, 'Whyte' written 'White' (brand colour_spelling)" in t["Why"]


def test_the_prompts_heel_paragraph_follows_heel_codes(tmp_path, key):
    for heel, has, hasnt in ((True, "only H30, H50, H60 or H80", "never as an H-code"),
                             (False, "never as an H-code", "only H30, H50, H60 or H80")):
        fake = Fake()
        build(tmp_path, fake, brand=dict(BRAND, heel_codes=heel))
        text = " ".join(fake.calls[0]["system"][0]["text"].split())
        assert has in text and hasnt not in text and "Maison Margiela shoe" not in text
        assert "Prefer the cart's Color Description" not in text


def test_a_green_name_stays_green_when_house_style_only_respells_the_colour(tmp_path, key):
    import test_oc_cart as tc
    from oc import cart as cartm
    rows = [list(r) for r in tc.CART_ROWS]
    rows[0][5] = "Wasched black"   # an invented misspelling the brand config corrects
    brand = dict(tc.BRAND, colour_spelling={"Wasched": "Washed"})
    cart = cartm.load(tc.write_cart(tmp_path / "c.xlsx", rows))
    fake = Fake(answers={tc.SNEAKER: dict(product_name="Court Sneaker", colour_words="Washed Black",
                                          colour_family="BLACK", basis="b")})
    out = draftm.build(tc._docs(), brand, tc._catalog(tmp_path), histm.History([]), base._tags(), cart=cart,
                       titler=lambda p, R: titles.run(p, R, factory=fake.factory))
    t = next(x for x in out[1] if x["Code"] == tc.SNEAKER and x["Column"] == "Title")
    assert t["Value"] == "Replica Sneaker in Washed Black"   # the live stem, green, kept
    assert "AI suggests product name 'Court Sneaker'" in t["Why"] and "'Wasched' written 'Washed'" in t["Why"]


def test_with_match_ignore_the_agent_matches_codes_as_catalog_match_does(tmp_path, key):
    """Landing 2's match_ignore: an OC code and the store's code differ only by the brand's separators. The agent's
    evidence and its hold-out compare codes the way catalog.match does (code_match). Invented codes only."""
    tee = ("MEN CLOTHING T-SHIRTS SHORT SLEEVE", "BLACK", "M")
    live = base._variant("8", "Shell Test Tee in Black", tee[0], "Active", "ZQ0001AB1 K1", "BLACK", "M", "ZXFW26800",
                         "300.00", "300.00", "90.00", True)
    hist = past_po(tmp_path, rows=[("NEW", "ZQ0001AB1 K1", "F/W 26", "Shell Test Tee in Black", *tee),
                                   ("NEW", "ZQ0007AB1 K1", "F/W 26", "Wide Test Tee in Black", *tee)])
    d = [dict(doc="SS - 1", collection="MM MALE RTW AVP", gender="MEN", currency="European Euro", label="MM",
              lines=[_line(1, "ZQ-0001", "AB1", "K1", "T-SHIRT", {"M": 1}, 100.0),
                     _line(2, "ZQ-0001", "AB1", "K2", "T-SHIRT", {"M": 1}, 100.0)], total_qty=2, net=200.0)]
    seen = {}
    for ignore in ("", "-"):
        fake = Fake()
        rows, review, checks, fills = build(tmp_path, fake, hist=hist, cat_extra=[live], d=copy.deepcopy(d),
                                            brand=dict(BRAND, match_ignore=ignore) if ignore else BRAND)
        ev = {e["code"]: e for req in fake.calls for e in sent_evidence(req)}
        block = fake.calls[0]["system"][1]["text"]
        seen[ignore] = (ev, block)
    ev, block = seen["-"]
    assert set(ev) == {"ZQ-0001AB1 K2"}                       # the carryover matched its live code, so only one NEW
    assert [p["code"] for p in ev["ZQ-0001AB1 K2"]["same_style_products"]] == ["ZQ0001AB1 K1"]
    assert "ZQ0001AB1 K1" not in block and "ZQ0007AB1 K1" in block   # the OC's own product is never an example
    ev, block = seen[""]                                       # without the key both codes are new and match nothing
    assert set(ev) == {"ZQ-0001AB1 K1", "ZQ-0001AB1 K2"} and not ev["ZQ-0001AB1 K2"]["same_style_products"]


@pytest.mark.parametrize("ignore", ["", "-", " ", "- "])
def test_colour_evidence_holds_with_any_match_ignore_and_none_without_a_colour(tmp_path, key, ignore):
    """Review 2026-11-14-buyer-2 F1: a space in match_ignore once joined every code's body to its colour, so a colour
    no product carries came back as every product's colour. Codes split on the raw string first. Invented codes."""
    brand = dict(BRAND, match_ignore=ignore) if ignore else BRAND
    plain = base._variant("8", "Bare Test Tote", "MEN BAGS TOTE BAGS", "Active", "ZQ0009AB9", "BLACK", "OS",
                          "ZXFW26809", "300.00", "300.00", "90.00", True)   # a store code with no colour part

    def colour_evidence(colour):
        d = [dict(doc="SS - 1", collection="MM MALE RTW AVP", gender="MEN", currency="European Euro", label="MM",
                  lines=[_line(1, "ZQ0002", "AB1", colour, "T-SHIRT", {"M": 1}, 100.0)], total_qty=1, net=100.0)]
        fake = Fake()
        build(tmp_path, fake, d=d, brand=brand, cat_extra=[plain])
        (e,) = [e for req in fake.calls for e in sent_evidence(req)]
        return e["same_fabric_and_colour_code"], e["same_colour_code_other_fabrics"]
    assert colour_evidence("K2") == ([], dict(products=0, uses=[]))      # a colour no product carries
    assert colour_evidence("") == ([], dict(products=0, uses=[]))        # no colour on the code: no colour evidence
    same, other = colour_evidence("101")                                  # a colour the catalog carries still counts
    assert same == [] and other["products"] >= 1 and {u["colour_words"] for u in other["uses"]} == {"White"}


def test_ai_titles_through_service_draft_complete_and_reconcile_with_match_ignore(tmp_path, key, monkeypatch):
    """One probe of the seam with landing 2's match_ignore through the three entry points. Invented codes only."""
    from oc import complete, reconcile
    brand = dict(BRAND, match_ignore="-")
    live = base._variant("8", "Shell Test Sneaker in Black", "MEN SHOES SNEAKERS", "Active", "ZQ0001AB1 K1", "BLACK",
                         "42", "ZXFW26800", "300.00", "300.00", "90.00", True)
    cat_path = write_catalog(tmp_path, [live])

    def oc():
        return [dict(doc="SS - 1", collection="MM MALE SHOES AVP", gender="MEN", currency="European Euro", label="MM",
                     lines=[_line(1, "ZQ-0001", "AB1", "K1", "SNEAKERS", {"42": 1}, 100.0),
                            _line(2, "ZQ-0001", "AB1", "K2", "SNEAKERS", {"42": 1}, 100.0)], total_qty=2, net=200.0)]
    monkeypatch.setattr(service, "read_oc", lambda *a, **k: (oc(), [], None))
    fake = Fake(answers={"ZQ-0001AB1 K2": dict(product_name="Shell Test Sneaker", colour_words="Ink Black",
                                               colour_family="BLACK", basis="b")})
    po, review, checks = service.draft(str(tmp_path / "oc.pdf"), cat_path, brand, ai_titles=True,
                                       ai_factory=fake.factory)
    assert po is not None and not service.blocking(checks), service.blocking(checks)
    out = tmp_path / "po.xlsx"
    out.write_bytes(po)
    _, rows = poxlsx.read_po(str(out))
    # the tool's own NEW-product lookup takes codes as printed, so it finds no sibling and its title keeps the raw
    # colour code; the agent's evidence finds the live sibling, and its suggestion waits in the Why (the F1 rule)
    assert {(r["Command"], r[poxlsx.CODE], r["Title"]) for r in rows} == {
        ("MERGE", "ZQ0001AB1 K1", "Shell Test Sneaker in Black"), ("NEW", "ZQ-0001AB1 K2", "Sneakers in K2")}
    t = review[(review["Code"] == "ZQ-0001AB1 K2") & (review["Column"] == "Title")].iloc[0]
    assert t["Colour"] == "red" and "AI suggests 'Shell Test Sneaker in Ink Black': not filled in" in t["Why"]
    done = {c["Check"]: c["Result"] for c in complete.run(str(out), oc(), brand, catm.load(cat_path), histm.History([]),
                                                           base._tags(), str(tmp_path / "done.xlsx"))}
    assert done["Qty per code+size equals the OC"] == "PASS"
    assert reconcile.compare(rows, rows, oc(), brand=brand)["hand_qty_mismatch"] == []


def test_the_system_prompt_states_the_house_rules():
    s = " ".join(titles.system(BRAND).split())
    for phrase in ("only H30, H50, H60 or H80", "never H100", "Never guess a height",
                   "name a different colour on every fabric", "Never a brand or label name",
                   "the tool adds \"Womens \" or \"Mens \" itself", "evidence, not an answer"):
        assert phrase in s, phrase


# ---- failure never blocks ------------------------------------------------------------------------
def _sdk_error(name):
    import anthropic
    import httpx2
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    if name == "connection":
        return anthropic.APIConnectionError(request=req)
    return anthropic.RateLimitError(f"slow down {SENTINEL}", response=httpx2.Response(429, request=req), body=None)


@pytest.mark.parametrize("fake,needle", [
    (lambda: Fake(error=_sdk_error("rate")), "the AI service is busy or could not be reached"),
    (lambda: Fake(error=_sdk_error("connection")), "the AI service is busy or could not be reached"),
    (lambda: Fake(error=RuntimeError(f"socket {SENTINEL}")), "the AI request failed (RuntimeError"),
    (lambda: Fake(reply=lambda r, n: message(stop="refusal", category="cyber", text="")), "the AI declined (refusal, category cyber)"),
    (lambda: Fake(reply=lambda r, n: message(text="not json")), "the AI's answer was not valid JSON"),
    (lambda: Fake(reply=lambda r, n: message(dict(items=[]))), "did not have the expected shape"),
    (lambda: Fake(reply=lambda r, n: message(stop="max_tokens", text="{")), "the answer stopped early (max_tokens)"),
])
def test_a_failure_leaves_the_tools_values_and_warns(tmp_path, key, fake, needle):
    f = fake()
    rows, review, checks, fills = build(tmp_path, f)
    plain = build(tmp_path)
    assert rows == plain[0] and review == plain[1] and fills == plain[3]
    row = agent_row(checks)
    assert row["Result"] == "WARN" and row["Detail"].startswith("AI title drafting did not run: ") and needle in row["Detail"]
    assert SENTINEL not in row["Detail"]
    assert [c for c in checks if c["Check"] != titles.CHECK] == plain[2]


def test_products_left_out_of_an_answer_keep_the_tools_values_and_warn(tmp_path, key):
    answers = {c: v for c, v in DEFAULT.items() if c != BOOT_PAST}
    f = Fake()
    f.answers = answers
    rows, review, checks, fills = build(tmp_path, f)
    row = agent_row(checks)
    assert row["Result"] == "WARN" and row["Detail"].startswith(
        "AI title drafting did not run for 1 of 4 NEW products: the AI's answer left them out")
    assert "For the rest: 3 of 4 NEW products answered" in row["Detail"]
    assert one(review, BOOT_PAST, "Title")["Source"] != AI_DRAFT
    assert one(review, NEW_STYLE_101, "Title")["Source"] == AI_DRAFT


def test_one_failed_chunk_leaves_the_others_drafted(tmp_path, key):
    def reply(req, n):
        if n == 2:
            return message(stop="refusal", text="")
        return message(dict(products=[dict(code=c, product_name=f"Paint Sneaker {chr(65 + int(c[5:9]))}",
                                           colour_words="White", colour_family="WHITE", basis="b")
                                      for c in sent_codes(req)]))
    rows, review, checks, fills = build(tmp_path, Fake(reply=reply), d=_many(titles.CHUNK + 2))
    row = agent_row(checks)
    assert row["Result"] == "WARN" and row["Detail"].startswith(
        f"AI title drafting did not run for 2 of {titles.CHUNK + 2} NEW products: the AI declined")
    drafted = {x["Code"] for x in review if x["Column"] == "Title" and x["Source"] == AI_DRAFT}
    assert len(drafted) == titles.CHUNK


def test_an_error_inside_the_job_restores_the_tools_values(tmp_path, key, monkeypatch):
    real = titles.judge

    def judge(a, ev, brand, code):
        if code == BOOT_PAST:
            raise KeyError("boom")
        return real(a, ev, brand, code)
    monkeypatch.setattr(titles, "judge", judge)
    rows, review, checks, fills = build(tmp_path, Fake())
    plain = build(tmp_path)
    assert rows == plain[0] and review == plain[1]
    assert agent_row(checks)["Detail"].startswith("AI title drafting did not run: the tool hit an error (KeyError")


def test_an_error_after_a_chunk_was_applied_puts_every_value_back(tmp_path, key, monkeypatch):
    real, d = titles.judge, _many(titles.CHUNK + 2)
    last = sorted(f"{l['style']}{l['fabric']} {l['colour']}" for l in d[0]["lines"])[-1]

    def judge(a, ev, brand, code):
        if code == last:
            raise KeyError("boom")
        return real(a, ev, brand, code)

    def reply(req, n):
        return message(dict(products=[dict(code=c, product_name=f"Paint Sneaker {chr(65 + int(c[5:9]))}",
                                           colour_words="White", colour_family="WHITE", basis="b")
                                      for c in sent_codes(req)]))
    monkeypatch.setattr(titles, "judge", judge)
    rows, review, checks, fills = build(tmp_path, Fake(reply=reply), d=d)
    plain = build(tmp_path, d=d)
    assert rows == plain[0] and review == plain[1] and fills == plain[3]
    assert agent_row(checks)["Detail"].startswith("AI title drafting did not run: the tool hit an error (KeyError")


def test_a_restore_after_an_error_leaves_no_ai_mark_for_the_de_dupe(tmp_path, key, monkeypatch):
    # women's product answered first and applied, then an error on the men's: everything must go back, including the
    # mark that tells the de-dupe's gender prefix to keep an AI source
    women = [_line(1, "S95YY0001", "P0001", "101", "SNEAKERS", {"38": 1}, 200.0)]
    men = [_line(1, "S97XX0009", "P0001", "101", "SNEAKERS", {"42": 1}, 200.0)]
    d = [dict(doc=n, collection=c, gender=g, currency="European Euro", label="MM", lines=ls, total_qty=1, net=200.0)
         for n, c, g, ls in (("SS - 2", "MM FEMALE SHOES AVP", "WOMEN", women), ("SS - 1", "MM MALE SHOES AVP", "MEN", men))]
    monkeypatch.setattr(titles, "CHUNK", 1)
    real, calls = titles.judge, []

    def judge(a, ev, brand, code):
        calls.append(code)
        if len(calls) == 2:
            raise KeyError("boom")
        return real(a, ev, brand, code)
    monkeypatch.setattr(titles, "judge", judge)
    answer = dict(product_name="Paint Sneaker", colour_words="", colour_family="", basis="b")
    rows, review, checks, fills = build(tmp_path, Fake(answers={"S95YY0001P0001 101": answer,
                                                                "S97XX0009P0001 101": answer}), d=d)
    plain = build(tmp_path, d=d)
    assert calls == ["S95YY0001P0001 101", "S97XX0009P0001 101"]
    assert rows == plain[0] and review == plain[1] and fills == plain[3]
    w = one(review, "S95YY0001P0001 101", "Title")
    assert w["Value"] == "Womens Sneakers in White" and w["Source"] == "rule"


def test_the_check_row_counts_and_costs_cache_reads_and_writes(tmp_path, key):
    def reply(req, n):
        prods = [dict(code=c, **DEFAULT[c]) for c in sent_codes(req) if c in DEFAULT]
        return message(dict(products=prods), usage=(2000, 6000, 0, 900) if n == 1 else (2000, 0, 6000, 900))
    rows, review, checks, fills = build(tmp_path, Fake(reply=reply))
    row = agent_row(checks)
    assert row["Result"] == "INFO"
    cost = (2000 * 4 + 6000 * 5 + 0 * 0.2 + 900 * 20) / 1e6
    assert f"about US${cost:.2f}" in row["Detail"] and "2,000 input + 6,000 cache write + 0 cache read + 900 output" in row["Detail"]
    assert "4 of 4 NEW products answered" in row["Detail"] and "claude-opus-5-5" in row["Detail"]
    u = common.Usage()
    u.add(reply({"messages": [{"content": [{"text": "x\n" + json.dumps(dict(products=[]))}]}]}, 2))
    assert u.t == dict(input_tokens=2000, cache_write_tokens=0, cache_read_tokens=6000, output_tokens=900)
    assert u.cost_usd == round((2000 * 4 + 6000 * 0.2 + 900 * 20) / 1e6, 4)


def test_a_fallback_iteration_is_summed_into_the_cost():
    msg = types.SimpleNamespace(model="claude-opus-4-8", usage=types.SimpleNamespace(iterations=[
        types.SimpleNamespace(input_tokens=100, cache_creation_input_tokens=1000, cache_read_input_tokens=0, output_tokens=10),
        types.SimpleNamespace(input_tokens=100, cache_creation_input_tokens=None, cache_read_input_tokens=1000,
                              output_tokens=20)]))
    assert ai.tokens(msg) == dict(input_tokens=200, cache_write_tokens=1000, cache_read_tokens=1000, output_tokens=30)
    u = common.Usage()
    u.add(msg)
    assert "fallback model claude-opus-4-8" in u.text()


def test_the_key_never_reaches_a_check_or_the_review(tmp_path, key):
    rows, review, checks, fills = build(tmp_path, Fake(error=RuntimeError(f"echo {SENTINEL}")))
    assert SENTINEL not in json.dumps([rows, review, checks], default=str)


# ---- the service and the CLI ----------------------------------------------------------------------
def test_the_service_drafts_titles_only_when_asked(tmp_path, key, monkeypatch):
    monkeypatch.setattr(service, "adapter", lambda cfg: types.SimpleNamespace(parse_checked=lambda p: docs()))
    fake = Fake()
    po, review, checks = service.draft("oc.pdf", write_catalog(tmp_path), BRAND, ai_factory=fake.factory)
    assert fake.calls == [] and not [c for c in checks if c["Check"] == titles.CHECK]
    po, review, checks = service.draft("oc.pdf", write_catalog(tmp_path), BRAND, ai_factory=fake.factory,
                                       ai_titles=True)
    assert len(fake.calls) == 1 and agent_row(checks)["Result"] == "INFO"
    assert AI_DRAFT in set(review["Source"]) and service.agent_ready() is True


def test_the_cli_flag_says_when_there_is_no_key(tmp_path, no_key, monkeypatch, capsys):
    monkeypatch.setattr(service, "adapter", lambda cfg: types.SimpleNamespace(parse_checked=lambda p: docs()))
    cfgp = tmp_path / "b.yaml"
    import yaml
    cfgp.write_text(yaml.safe_dump({k: v for k, v in BRAND.items() if not k.startswith("_")}))
    out = tmp_path / "d.xlsx"
    cli.main(["draft", "--oc", "oc.pdf", "--catalog", write_catalog(tmp_path), "--brand", "x", "--brand-config",
              str(cfgp), "--out", str(out), "--ai-titles"])
    text = capsys.readouterr().out
    assert "AI title drafting is off: no ANTHROPIC_API_KEY" in text and titles.CHECK not in text
    plain = tmp_path / "p.xlsx"
    cli.main(["draft", "--oc", "oc.pdf", "--catalog", write_catalog(tmp_path), "--brand", "x", "--brand-config",
              str(cfgp), "--out", str(plain)])
    assert workbook(out.read_bytes()) == workbook(plain.read_bytes())


def test_the_cli_flag_drafts_with_a_key(tmp_path, key, monkeypatch, capsys):
    monkeypatch.setattr(service, "adapter", lambda cfg: types.SimpleNamespace(parse_checked=lambda p: docs()))
    fake = Fake()
    monkeypatch.setattr(ai, "make_client", fake.factory)
    cfgp = tmp_path / "b.yaml"
    import yaml
    cfgp.write_text(yaml.safe_dump({k: v for k, v in BRAND.items() if not k.startswith("_")}))
    cli.main(["draft", "--oc", "oc.pdf", "--catalog", write_catalog(tmp_path), "--brand", "x", "--brand-config",
              str(cfgp), "--out", str(tmp_path / "d.xlsx"), "--ai-titles"])
    assert len(fake.calls) == 1 and f"[INFO] {titles.CHECK}" in capsys.readouterr().out
