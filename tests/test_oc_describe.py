"""The house description (oc/describe.py) and its AI prose job (oc/agent/describe.py), offline: an invented brand,
invented codes and text, a fake Anthropic client. No real order data, no network."""
import copy
import csv
import json
import re
import sys
import types

import pytest

import test_oc_agent as ag
import test_oc_tool as base
from oc import brand as brandm, catalog as catm, cli, describe, draft as draftm, history as histm, service
from oc.adapters import ai
from oc.agent import common, describe as job
from oc.resolve import AI_DRAFT, COLOUR, TOOL_DRAFT

JACKET = "ZQ0001AB1P0001 101"     # OC composition and made in, a cart row that disagrees
LOAFER = "ZQ0002CD2P0002 900"     # coded composition (red), made in printed as an unknown place
TOTE = "ZQ0003EF3P0003 T001"      # no composition anywhere (red), no made in
SHIRT = "ZQ0004GH4P0004 202"      # composition '-' (red), a season word in its colour
CODES = [JACKET, LOAFER, TOTE, SHIRT]

BRAND = {k: v for k, v in base.BRAND.items() if k != "body_html"}
BRAND.update(vendor="Acme Studio", oc_label=["ACME"], body_html=None,
             product_class_types={"ORBIT FIELD JACKET": "CLOTHING JACKETS LIGHTWEIGHT", "LOAFER": "SHOES LOAFERS",
                                  "TOTE": "BAGS SHOPPER & TOTE BAGS",
                                  "SHIRT": ["CLOTHING SHIRTS LONG SLEEVE", "CLOTHING TOPS SHIRTS"]},
             colour_families={"BLACK": ["black"], "GREY": ["slate", "grey"], "BEIGE": ["sand"], "BLUE": ["blue"]},
             country_names={"IT": "Italy"}, _name="acme", _path="t")


def _line(n, code, pclass, sizes, **kw):
    style, rest = code.split(" ")
    q = sum(sizes.values())
    return dict(line_no=n, style=style[:9], fabric=style[9:], colour=rest, product_class=pclass, sizes=sizes, qty=q,
                price=100.0, line_value=q * 100.0, **kw)


def docs():
    lines = [_line(1, JACKET, "ORBIT FIELD JACKET", {"M": 1}, description="ORBIT FIELD JACKET - WAXED, ZIP FRONT",
                   composition="Shell:  100% Cotton & Linen -  Lining: 100% Cupro", made_in="IT"),
             _line(2, LOAFER, "LOAFER", {"41": 1}, description="MESA LOAFER", composition="U:LE L:TE S:RU",
                   made_in="Atlantis"),
             _line(3, TOTE, "TOTE", {"OS": 1}),
             _line(4, SHIRT, "SHIRT", {"M": 1}, composition="-")]
    return [dict(doc="AC - 1", collection="ACME FW", gender="MEN", currency="European Euro", label="ACME",
                 lines=lines, total_qty=4, net=400.0)]


def write_catalog(tmp_path):
    r = base._variant("9", "Other Coat in Black", "MEN CLOTHING COATS", "Active", "ZQ0099ZZ9P0099 900", "BLACK", "M",
                      "ZXFW26900", "800.00", "800.00", "300.00", True)
    r["Vendor"] = "Acme Studio"
    p = tmp_path / "catalog.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=base.CAT_COLS)
        w.writeheader()
        w.writerow(r)
    return str(p)


def cart():
    row = dict(code=JACKET, row=7, style_name="ORBIT FIELD JACKET", model="ZQ0001AB1", fabric="P0001",
               colour_code="101", colour="Slate", gender="MEN", gender_raw="Male", collection="", category="",
               subcategory="", materials="Shell: 100% Cotton", made_in="Italy", made_in_raw="Made in Italy",
               size_grid=[], available_sizes=[], wholesale=None, currency="EUR", rrp=None, rrp_currency=None)
    shirt = dict(row, code=SHIRT, row=8, colour="Summer Blue", style_name="SHIRT", materials="", made_in="",
                 made_in_raw="")
    return dict(path="cart.xlsx", sheet="s", label="cart TEST", session="TEST", rows={JACKET: row, SHIRT: shirt},
                duplicates=[])


def build(tmp_path, fake=None, brand=BRAND, d=None, with_cart=True):
    cat = catm.load(write_catalog(tmp_path))
    describer = None if fake is None else (lambda p, R: job.run(p, R, factory=fake.factory))
    return draftm.build(d or docs(), brand, cat, histm.History([("F/W 26", [])]), base._tags(),
                        cart=cart() if with_cart else None, describer=describer)


def body(review, code):
    return ag.one(review, code, "Body HTML")


# ---- the fake AI ------------------------------------------------------------------------------------------
def claim(text, field="", quote=""):
    return dict(text=text, field=field, quote=quote)


NONE = claim("")
DEFAULT = {
    JACKET: dict(adjective=claim("waxed", "description", "WAXED"),
                 sentence_2=claim("This jacket has a zip front.", "description", "ZIP FRONT"),
                 bullets=[claim("Zip front closure", "description", "ZIP FRONT")]),
    LOAFER: dict(adjective=NONE, sentence_2=NONE, bullets=[]),
    TOTE: dict(adjective=NONE, sentence_2=NONE, bullets=[]),
    SHIRT: dict(adjective=NONE, sentence_2=NONE, bullets=[]),
}


def sent(req):
    return json.loads(req["messages"][0]["content"][0]["text"].split("\n", 2)[2])["products"]


class Fake(ag.Fake):
    def __init__(self, answers=None, reply=None, error=None):
        super().__init__(reply=reply, error=error)
        self.answers = {**DEFAULT, **(answers or {})}

    def _stream(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        if self.reply:
            return ag.Stream(self.reply(kw, len(self.calls)))
        prods = [dict(code=p["code"], **self.answers[p["code"]]) for p in sent(kw) if p["code"] in self.answers]
        return ag.Stream(ag.message(dict(products=prods)))


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(ai.KEY_NAME, ag.SENTINEL)


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv(ai.KEY_NAME, raising=False)
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets={}))


# ---- the parts, one function at a time ----------------------------------------------------------------------
@pytest.mark.parametrize("value,why", [
    ("-", "placeholder"), (" n/a ", "placeholder"), ("U:LE L:TE S:RU", "codes, not words"), ("100% CO", "codes"),
    ("63% WOOL/16% NYLON/16% COTT", "adds up to 95%"), ("52% COTTON/24% NYLON/20% VISCOSE/4", "adds up to 96%"),
    ("1_x001b_0_x001b_0% COTTON", "garbled"), ("100%\x00 Cotton", "garbled"), ("100% <b>Cotton</b>", "markup"),
    ("60% COTTON 40% WOOL/5", "ends in a number"),
])
def test_an_unusable_composition_says_why(value, why):
    assert why in describe.composition_problem(value)


@pytest.mark.parametrize("value", ["100% Cotton", "COTTON100%", "Leather", "Upper: 100% Calf - Lining: 100% Goat",
                                   "100% COTON  100% CUIR", "Exterior: 99.9% COTTON 0.1% ELASTANE",
                                   "100% Calf &amp; Lamb"])
def test_a_usable_composition_passes_whole(value):
    assert describe.composition_problem(value) == ""


def test_phrases_are_read_as_whole_words_and_an_adjective_never_repeats_the_item():
    assert describe.phrases(describe.FEATURES, [("description", "ZIPPERED POUCH")], 3) == []
    assert describe.phrases(describe.FEATURES, [("description", "ZIP POUCH")], 3) == \
        [("Zip fastening", "description", "zip")]
    f = dict(code=TOTE, vendor="Acme", title=("Crop Pant in Sand", "catalog", ""),
             ptype=("MEN CLOTHING PANTS CROPPED", "catalog", ""), composition=[], made_in=[],
             text=[("description", "CROPPED PANT")])
    d = describe.draft(f, BRAND)
    assert d["adjective"] is None and "is a pair of cropped pants." in describe.render(d)


def test_countries_from_codes_names_and_the_config():
    assert [describe.country(v, BRAND) for v in ("IT", "ITA", "PORTUGAL", "Made in China", "tr", "Atlantis", "-")] \
        == ["Italy", "Italy", "Portugal", "China", "Turkey", None, None]
    assert describe.country("XX", dict(country_names={"XX": "Elsewhere"})) == "Elsewhere"


def test_gender_and_season_words():
    for t in ("For women", "a men's fit", "Unisex cut", "SS26 drop", "F/W season", "Ideal for winter", "Since 2026"):
        assert describe.banned(t), t
    for t in ("Mensa loafer", "Falls to the knee", "Summerland tote", "Romantic shape"):
        assert describe.banned(t) == "", t


def test_a_claim_must_rest_on_a_quote_from_its_own_field():
    ev = dict(item="jacket", fields=dict(description="ORBIT JACKET - ZIP FRONT", title="Orbit Jacket in Slate"))
    ok = claim("Zip front closure", "description", "ZIP FRONT")
    assert job.problems("bullets", ok, ev, []) == []
    cats = lambda c: [k for k, _ in job.problems("bullets", c, ev, [])]
    assert cats(claim("Zip front closure", "fabric_name", "ZIP FRONT")) == ["field"]
    assert cats(claim("Zip front closure", "description", "BUTTON FRONT")) == ["quote not in its field"]
    assert cats(claim("Zip front in cashmere", "description", "ZIP FRONT")) == ["words not in the quote"]
    assert cats(claim("Zip front closure", "title", "ZIP FRONT")) == ["quote not in its field"]


# ---- the shape --------------------------------------------------------------------------------------------


def test_every_type_in_expected_tags_has_an_item_noun():
    rests = {describe.type_rest(t) for t in base._tags() if t and t != "nan"}
    assert rests and not [r for r in rests if describe.item_noun(r, "") is None]


def test_a_type_without_an_item_noun_gives_no_intro_and_a_red_cell():
    f = dict(code=TOTE, vendor="Acme", title=("Drift Tote in Sand", "catalog", ""), ptype=("", "guess", ""),
             composition=[dict(value="100% Cotton", where="x", source="OC", html=False)], made_in=[], text=[])
    d = describe.draft(f, BRAND)
    assert describe.render(d).startswith("<ul>") and describe.source(d) == "guess"
    assert "the Type is blank" in describe.why(d)


def test_never_green_and_a_red_title_keeps_the_body_red():
    f = dict(code=TOTE, vendor="Acme", title=("Drift Tote in Sand", "catalog", ""),
             ptype=("UNISEX BAGS SHOPPER & TOTE BAGS", "catalog", ""),
             composition=[dict(value="100% Cotton", where="x", source="catalog", html=True)],
             made_in=[dict(value="IT", where="x", source="OC")], text=[])
    d = describe.draft(f, BRAND)
    assert COLOUR[describe.source(d)] == "amber"
    d["title_src"] = "guess"
    assert describe.source(d) == "guess" and "the title is red" in describe.why(d)


def test_a_live_products_composition_line_is_kept_as_html():
    f = dict(code=TOTE, vendor="Acme", title=("Drift Tote in Sand", "catalog", ""), ptype=("MEN BAGS BACKPACKS", "x", ""),
             composition=[dict(value="100% Calf &amp; Lamb", where="x", source="catalog", html=True)], made_in=[],
             text=[])
    assert "<li>Composition: 100% Calf &amp; Lamb</li>" in describe.render(describe.draft(f, BRAND))


# ---- the AI job ---------------------------------------------------------------------------------------------


def test_a_held_config_reads_and_sends_nothing(tmp_path, key):
    fake = Fake()
    held = dict(BRAND, hold="waiting on the supplier")
    R = types.SimpleNamespace(b=held)
    assert job.run([], R, factory=fake.factory)[0]["Result"] == "WARN"
    assert fake.keys == [] and fake.calls == []
    assert job.plan([describe.draft(dict(code=TOTE, vendor="A", title=("T in S", "catalog", ""),
                                         ptype=("MEN BAGS BACKPACKS", "x", ""), composition=[], made_in=[], text=[]),
                                    held)], held) == ([], {})


def test_the_stem_rule_matches_forms_of_one_word():
    assert job.ungrounded("Zipped front with padding", "ZIP FRONT, PADDED", "jacket") == []
    assert job.ungrounded("Zipped front in wool", "ZIP FRONT", "jacket") == ["wool"]


def test_the_invented_examples_pass_every_check_and_name_no_brand():
    blocks = re.findall(r"Evidence: (\{.*?\})\nAnswer: (\{.*?\})\n", job.FEW_SHOT, re.S)
    assert len(blocks) == 3
    for e, a in blocks:
        ev, an = json.loads(e), json.loads(a)
        for kind in ("adjective", "sentence_2"):
            if an[kind]["text"]:
                assert job.problems(kind, an[kind], ev, job.brand_words(BRAND)) == []
        for c in an["bullets"]:
            assert job.problems("bullets", c, ev, job.brand_words(BRAND)) == []
        assert set(ev) == {k for k, _ in job.SENT}


def test_every_limit_the_checks_enforce_is_stated_in_the_prompt():
    s = job.SYSTEM
    for cap in (job.ADJ_CAP, job.SENTENCE_CAP, job.BULLET_CAP, job.QUOTE_CAP):
        assert f"{cap} characters" in s
    for lo, hi in (job.ADJ_WORDS, job.SENTENCE_WORDS, job.BULLET_WORDS):
        assert f"{lo} to {hi}" in s
    assert all(w in s for w in job.GENERIC) and "- ' , . / & % ( )" in s
    for w in ("gender", "season", "brand", "ChatGPT", "code", "markup", "= + - or @", "Composition", "Made in", "SKU"):
        assert w in s


def _oc(monkeypatch):
    monkeypatch.setattr(service, "adapter", lambda cfg: types.SimpleNamespace(parse_checked=lambda p: docs()))


def _cfg(tmp_path):
    import yaml
    cfgp = tmp_path / "b.yaml"
    cfgp.write_text(yaml.safe_dump({k: v for k, v in BRAND.items() if not k.startswith("_")}))
    return str(cfgp)


def test_the_sheet_reader_reads_a_composition_column_whitespace_collapsed(tmp_path):
    import test_oc_sheet as ts
    from oc.adapters import sheet
    rows = [ts.HEAD + ["Materials"]] + [r + ["Shell: 100%  Cotton"] for r in ts.ROWS]
    path = tmp_path / "oc.csv"
    with open(path, "w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    docs = sheet.parse_checked(str(path), ts.cfg(columns=dict(ts.COLUMNS, composition="Materials")))
    assert {l["composition"] for d in docs for l in d["lines"]} == {"Shell: 100% Cotton"}
    plain = sheet.parse_checked(str(path), ts.cfg())
    assert not [l for d in plain for l in d["lines"] if "composition" in l]


# ---- the page ------------------------------------------------------------------------------------------------


