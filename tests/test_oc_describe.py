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
    assert describe.phrases(describe.FEATURES, [("description", "ZIPPERED POUCH")], 3, "accessory") == ([], [])
    assert describe.phrases(describe.FEATURES, [("description", "ZIP POUCH")], 3, "accessory") == \
        ([("Zip fastening", "description", "zip")], [])
    f = dict(code=TOTE, vendor="Acme", title=("Crop Pant in Sand", "catalog", ""),
             ptype=("MEN CLOTHING PANTS CROPPED", "catalog", ""), composition=[], made_in=[],
             text=[("description", "CROPPED PANT")])
    d = describe.draft(f, BRAND)
    assert d["adjective"] is None and "is a pair of cropped pants." in describe.render(d)


def test_a_composition_is_written_in_title_case_as_the_house_does():
    for printed, written in (("60% ORGANIC COTTON 40% POLYAMIDE (PA)", "60% Organic Cotton 40% Polyamide (Pa)"),
                             ("SHELL: 100%  WOOL - LINING: 100% CUPRO", "Shell: 100% Wool - Lining: 100% Cupro"),
                             ("100%COTTON", "100%Cotton"), ("52% COTTON-BCI/48% NYLON-REC", "52% Cotton-Bci/48% Nylon-Rec"),
                             ("boiled wool gabardine", "Boiled Wool Gabardine")):
        assert describe.house_case(printed) == written
    f = dict(code=TOTE, vendor="Acme", title=("Drift Tote in Sand", "catalog", ""),
             ptype=("MEN BAGS SHOPPER & TOTE BAGS", "catalog", ""),
             composition=[dict(value="60% ORGANIC COTTON 40% POLYAMIDE (PA) & TRIM", where="the OC line", source="OC",
                               html=False)], made_in=[], text=[])
    d = describe.draft(f, BRAND)
    assert "<li>Composition: 60% Organic Cotton 40% Polyamide (Pa) &amp; Trim</li>" in describe.render(d)
    assert "printed '60% ORGANIC COTTON 40% POLYAMIDE (PA) & TRIM', written in Title Case" in describe.why(d)


def _kinded(ptype, text, fields=("description",)):
    f = dict(code=TOTE, vendor="Acme", title=("Drift Piece in Sand", "catalog", ""), ptype=(ptype, "catalog", ""),
             composition=[dict(value="100% Cotton", where="x", source="OC", html=False)], made_in=[],
             text=[(k, text) for k in fields])
    return describe.draft(f, BRAND)


@pytest.mark.parametrize("ptype,text,want,never", [
    ("MEN BAGS SHOULDER BAGS", "BELTED CARRYALL", [], ["Belted waist", "belted"]),
    ("WOMEN CLOTHING COATS", "BELTED WRAP COAT", ["Belted waist"], []),
    ("MEN CLOTHING JACKETS LIGHTWEIGHT", "WIDE LEG JACKET", [], ["Wide leg", "wide-leg"]),
    ("MEN CLOTHING PANTS TROUSERS", "WIDE LEG TROUSER", ["Wide leg"], []),
    ("MEN SHOES SNEAKERS", "LONG SLEEVE RUNNER", [], ["Long sleeves", "long-sleeve"]),
    ("MEN CLOTHING KNITWEARS SWEATERS", "HENLEY KNIT", ["Henley placket"], ["Crew neckline"]),
    ("MEN CLOTHING SWEATSHIRTS", "CREWNECK V-NECK SWEAT", [], ["Crew neckline", "V-neckline"]),
])
def test_a_feature_is_written_only_on_a_kind_that_has_it(ptype, text, want, never):
    d = _kinded(ptype, text)
    words = [b["text"] for b in d["bullets"]] + [(d["adjective"] or {}).get("text", "")]
    assert all(w in words for w in want) and not [w for w in never if any(w in x for x in words)]


def test_the_product_class_is_a_category_never_a_construction():
    d = _kinded("MEN CLOTHING SWEATSHIRTS", "CREWNECK", fields=("product_class", "style_name"))
    assert [b["text"] for b in d["bullets"]] == ["Sand colourway"] and d["adjective"] is None
    d = _kinded("MEN CLOTHING SWEATSHIRTS", "WAXED", fields=("fabric_name",))
    assert d["adjective"]["text"] == "waxed"   # a finish may come from the fabric name
    d = _kinded("MEN CLOTHING SWEATSHIRTS", "RELAXED FIT", fields=("fabric_name",))
    assert d["adjective"] is None   # a fit may not


def test_two_necklines_in_one_description_are_both_left_out():
    d = _kinded("MEN CLOTHING SWEATSHIRTS", "CREWNECK SWEAT WITH V-NECK INSERT")
    assert not [b for b in d["bullets"] if "neck" in b["text"].lower()]
    assert "bullets of the neck (the description names two)" in describe.why(d)


def test_an_ai_second_sentence_about_another_kind_of_product_keeps_the_body_red(key):
    f = dict(code=SHIRT, vendor="Acme Studio", title=("Loop Crew in Black", "catalog", ""),
             ptype=("MEN CLOTHING SWEATSHIRTS", "catalog", ""),
             composition=[dict(value="100% Cotton", where="x", source="OC", html=False)], made_in=[],
             text=[("description", "LOOP CREW - FINE KNIT SWEATER, RIBBED HEM")])
    s2 = claim("This sweater has a ribbed hem.", "description", "KNIT SWEATER, RIBBED HEM")
    for sentence, red in ((s2, True), (claim("This sweatshirt has a ribbed hem.", "description", "RIBBED HEM"), False)):
        pending = [dict(code=SHIRT, describe=describe.draft(f, BRAND))]
        fake = Fake({SHIRT: dict(adjective=NONE, sentence_2=sentence, bullets=[])})
        (row,) = job.run(pending, types.SimpleNamespace(b=BRAND), factory=fake.factory)
        d = pending[0]["describe"]
        assert d["sentence2"]["text"] == sentence["text"]
        assert (describe.source(d) == "guess") is red and (describe.ITEM_MISMATCH in describe.why(d)) is red
        assert ("check their Type" in row["Detail"]) is red
        assert d["item"] == "sweatshirt"   # the Type is never changed


def test_the_item_kind_of_a_sentence_is_its_subject():
    assert describe.item_mismatch("This sweater has a ribbed hem.", "sweatshirt")
    assert not describe.item_mismatch("This bomber has a ribbed hem.", "lightweight jacket")
    assert not describe.item_mismatch("This jacket has a shirt collar.", "lightweight jacket")
    assert not describe.item_mismatch("This pair of loafers sits on a stacked sole.", "pair of loafers")
    assert describe.item_mismatch("These sneakers sit on a stacked sole.", "pair of boots") is False   # one kind
    assert describe.item_mismatch("This tote has a zip pocket.", "pair of sneakers")


def test_a_claim_may_not_rest_on_the_title_or_the_context():
    f = dict(code=SHIRT, vendor="Acme Studio", title=("Crew Sweat in Black", "catalog", ""),
             ptype=("MEN CLOTHING SWEATSHIRTS", "catalog", ""), composition=[], made_in=[],
             text=[("product_class", "CREWNECK"), ("description", "CREW SWEAT")])
    ev = job.evidence(describe.draft(f, BRAND))
    assert ev["context"] == {"product_class": "CREWNECK"} and set(ev["fields"]) == {"description"}
    for field, quote in (("product_class", "CREWNECK"), ("title", "Crew Sweat")):
        assert [k for k, _ in job.problems("bullets", claim("Crew neckline shape", field, quote), ev, [])] == ["field"]


def test_the_article_follows_the_sound():
    for phrase, a in (("U-neck T-shirt", "a"), ("L-shaped bag", "an"), ("X-ray print tee", "an"), ("unisex coat", "a"),
                      ("euro-style bag", "a"), ("one-piece", "a"), ("oversized coat", "an"), ("upper", "an"),
                      ("hour bag", "an"), ("T-shirt", "a"), ("pair of sneakers", "a"), ("urban jacket", "an")):
        assert describe.article(phrase) == a, phrase
    f = dict(code=TOTE, vendor="Acme", title=("Loop Tee in Black", "catalog", ""), ptype=("MEN CLOTHING T-SHIRTS", "x", ""),
             composition=[], made_in=[], text=[])
    d = describe.draft(f, BRAND)
    d["adjective"] = dict(text="u-neck", field="description", by="tool", basis="x")
    assert "is a u-neck T-shirt." in describe.render(d)


@pytest.mark.parametrize("title,ptype,red", [
    ("Loop Cap in Black", "MEN CLOTHING T-SHIRTS", True),              # a cap the Type calls a T-shirt
    ("Loop Trousers in Black", "MEN CLOTHING KNITWEARS SWEATERS", True),
    ("Loop Tee in Black", "MEN CLOTHING T-SHIRTS", False),
    ("Womens Loop Tank Top in Black", "WOMEN CLOTHING T-SHIRTS", False),   # 'tank' and 'top' name parts: no kind
    ("Loop Belt Bag in Black", "UNISEX BAGS BELT BAGS", False),           # the head of the name: a bag, not a belt
    ("KNIT SWEATSHIRT - REV TRACK JKT in 101", "MEN CLOTHING SWEATSHIRTS", True),   # the name after ' - ' first
    ("KNIT JACKET - LOOP HOODIE in 101", "MEN CLOTHING HOODIES", False),
    ("Loop in Black", "MEN CLOTHING T-SHIRTS", False),                    # no kind in the title: nothing to check
    ("Loop Knit Short in Black", "MEN CLOTHING SHORTS", False),           # a title's singular 'Short' is shorts
    ("Loop Cargo Pant in Black", "MEN CLOTHING KNITWEARS SWEATERS", True),
    ("Padded Loop Overshirt in Black", "MEN CLOTHING JACKETS LIGHTWEIGHT", False),   # stores type it either way
])
def test_a_title_naming_another_kind_than_the_type_keeps_the_body_red(title, ptype, red):
    f = dict(code=TOTE, vendor="Acme", title=(title, "catalog", ""), ptype=(ptype, "catalog", ""),
             composition=[dict(value="100% Cotton", where="x", source="OC", html=False)], made_in=[], text=[])
    d = describe.draft(f, BRAND)
    assert (describe.TITLE_MISMATCH in d["red"]) is red and (describe.source(d) == "guess") is red
    assert d["title"] == title and d["item"] == describe.item_noun(ptype, title)   # neither is changed


def test_an_items_kind_is_its_head_noun():
    assert not describe.item_mismatch("This bag has a zip pocket.", "belt bag")
    assert not describe.item_mismatch("This short jacket has a stand collar.", "lightweight jacket")
    assert describe.item_kind("belt bag", last=True) == "bag" and describe.item_kind("belt bag") == "accessory"


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


def test_the_sheet_reader_reads_a_fabrication_column(tmp_path):
    import test_oc_sheet as ts
    from oc.adapters import sheet
    rows = [ts.HEAD + ["Fabrication"]] + [r + ["Loopback  fleece"] for r in ts.ROWS]
    path = tmp_path / "oc.csv"
    with open(path, "w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    docs_ = sheet.parse_checked(str(path), ts.cfg(columns=dict(ts.COLUMNS, fabrication="Fabrication")))
    assert {l["fabrication"] for d in docs_ for l in d["lines"]} == {"Loopback fleece"}


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


def test_a_missing_composition_alone_makes_the_body_red():
    f = dict(code=TOTE, vendor="Acme", title=("Drift Tote in Sand", "catalog", ""),
             ptype=("UNISEX BAGS SHOPPER & TOTE BAGS", "catalog", ""), composition=[],
             made_in=[dict(value="IT", where="x", source="OC")], text=[])
    d = describe.draft(f, BRAND)
    assert d["item"] and not d["composition"] and describe.source(d) == "guess"
    assert "<li>Composition" not in describe.render(d) and "Composition needed" in describe.why(d)


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


def test_run_gives_each_listing_of_a_code_the_prose_under_its_own_title(key):
    f = dict(code=SHIRT, vendor="Acme Studio", title=("Shirt in Black", "catalog", ""),
             ptype=("MEN CLOTHING SHIRTS LONG SLEEVE", "catalog", ""),
             composition=[dict(value="100% Cotton", where="x", source="OC", html=False)], made_in=[],
             text=[("description", "OXFORD SHIRT - BUTTON DOWN COLLAR")])
    pending = [dict(code=SHIRT, describe=describe.draft(f, BRAND)),
               dict(code=SHIRT, describe=describe.draft(dict(f, title=("Womens Shirt in Black", "rule", "")), BRAND))]
    collar = claim("Button down collar detail", "description", "BUTTON DOWN COLLAR")
    fake = Fake({SHIRT: dict(adjective=NONE, sentence_2=NONE, bullets=[collar])})
    (row,) = job.run(pending, types.SimpleNamespace(b=BRAND), factory=fake.factory)
    assert len(fake.calls) == 1 and len(sent(fake.calls[0])) == 1 and "1 bullet " in row["Detail"]
    for n, title in zip(pending, ("Shirt in Black", "Womens Shirt in Black")):
        assert f"ACME STUDIO's {title} is a" in n["body"][0] and "<li>Button down collar detail</li>" in n["body"][0]


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


