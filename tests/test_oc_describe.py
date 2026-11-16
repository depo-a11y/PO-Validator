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
from oc import brand as brandm, catalog as catm, cli, describe, draft as draftm, history as histm, poxlsx, service
from oc.adapters import ai
from oc.agent import common, describe as job
from oc.resolve import AI_DRAFT, COLOUR, TOOL_DRAFT

JACKET = "ZQ0001AB1P0001 101"     # OC composition and made in, a cart row that disagrees
LOAFER = "ZQ0002CD2P0002 900"     # coded composition (red), made in printed as an unknown place
TOTE = "ZQ0003EF3P0003 T001"      # no composition anywhere (red), no made in
SHIRT = "ZQ0004GH4P0004 202"      # composition '-' (red), a season word in its colour
CODES = [JACKET, LOAFER, TOTE, SHIRT]

BRAND = dict(base.BRAND)   # the template body_html stays: the description is its own import (option B)
BRAND.update(vendor="Acme Studio", oc_label=["ACME"],
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
    return ag.one(review, code, describe.DESC_COLUMN)


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


def test_a_trailing_short_after_a_plural_noun_is_a_length():
    assert describe.title_kind("Loop Socks Short in Grey") == "accessory"
    assert describe.title_kind("Loop Knit Short in Black") == "bottom"
    assert describe.title_kind("Moto Cargo Pant in Black") == "bottom"
    assert describe.title_kind("Loop Hooded Sweater in Stone") == "knit"
    f = dict(code=TOTE, vendor="Acme", title=("Loop Socks Short in Grey", "catalog", ""), ptype=("MEN ACCS SOCKS", "x", ""),
             composition=[dict(value="100% Cotton", where="x", source="OC", html=False)], made_in=[], text=[])
    d = describe.draft(f, BRAND)
    assert describe.TITLE_MISMATCH not in d["red"] and "is a pair of socks." in describe.render(d)


def test_an_items_kind_is_its_head_noun():
    assert not describe.item_mismatch("This bag has a zip pocket.", "belt bag")
    assert not describe.item_mismatch("This short jacket has a stand collar.", "lightweight jacket")
    assert describe.item_kind("belt bag", last=True) == "bag" and describe.item_kind("belt bag") == "accessory"


def test_a_negation_is_a_word_the_quote_must_hold():
    ev = dict(item="coat", fields=dict(fabric_name="LINING 100% COTTON", description="LOOP COAT - NO LINING, UNLINED"))
    cats = lambda c: [k for k, _ in job.problems("bullets", c, ev, [])]
    assert job._words("Made with no lining") == ["made", "with", "no", "lining"]
    assert cats(claim("Made with no lining", "fabric_name", "LINING 100% COTTON")) == \
        ["words not in the quote", "negation not next to its word"]
    assert cats(claim("Made with no lining", "description", "NO LINING")) == []
    assert cats(claim("Unlined coat with cotton", "description", "LOOP COAT - NO LINING, UNLINED")) == \
        ["words not in the quote"]
    assert cats(claim("Unlined loop coat", "description", "LOOP COAT - NO LINING, UNLINED")) == []
    assert cats(claim("Not lined at all", "description", "NO LINING")) == \
        ["words not in the quote", "negation not next to its word"]
    assert "no lining" in job.SYSTEM and all(n in job.SYSTEM for n in job.NEGATORS[:-1])


@pytest.mark.parametrize("negator,text,quote", [
    ("no", "Made with no lining", "NO LOGO. LINING 100% COTTON"),
    ("not", "Not lined with wax", "LINED, NOT WAXED"),
    ("non", "Non-stretch cotton twill", "STRETCH COTTON TWILL, NON IRON"),
    ("nor", "Neither lined nor padded", "NEITHER LINED, PADDED NOR WAXED"),
    ("never", "Never washed cotton", "COTTON, WASHED, NEVER DYED"),
    ("without", "Made without lining", "LINING, WITHOUT POCKETS"),
    ("un", "Un-lined cotton coat", "COTTON LINED. UN-WASHED"),
])
def test_a_negation_must_stand_in_the_quote_next_to_the_word_it_governs(negator, text, quote):
    ev = dict(item="coat", fields=dict(fabric_name=quote))
    cats = lambda c: [k for k, _ in job.problems("bullets", c, ev, [])]
    assert negator in job._words(text) and negator in job._words(quote)   # every word is quoted, the negator apart
    assert cats(claim(text, "fabric_name", quote)) == ["negation not next to its word"]
    words = job._words(text)
    ev["fields"]["fabric_name"] = fixed = f"{quote}, {negator} {words[words.index(negator) + 1]}".upper()
    assert cats(claim(text, "fabric_name", fixed)) == []   # quoted next to the word it governs, it is grounded


def test_a_letter_after_an_apostrophe_keeps_its_capital_as_the_house_writes_it():
    assert describe.house_case("100% Coton D'Acme") == "100% Coton D'Acme"
    assert describe.house_case("100% COTON D'ACME") == "100% Coton D'Acme"
    assert describe.house_case("100% CUIR D\u2019ACME") == "100% Cuir D\u2019Acme"
    assert describe.house_case("SHELL: 100% COTTON - D'ACME TRIM: 100% WOOL") == "Shell: 100% Cotton - D'Acme Trim: 100% Wool"


def test_the_default_template_body_still_gets_a_description():
    assert BRAND["body_html"] == brandm.DEFAULTS["body_html"]   # option B: the template stays the default
    f = dict(code=TOTE, vendor="Acme", title=("Drift Tote in Sand", "catalog", ""),
             ptype=("MEN BAGS SHOPPER & TOTE BAGS", "x", ""), composition=[], made_in=[], text=[])
    n = dict(l=dict(doc="AC - 1", line_no=1), code=TOTE, title=list(f["title"]), ptype=f["ptype"],
             comp=("-", "guess", ""), made=("-", "guess", ""), row0=0)
    R = types.SimpleNamespace(b=brandm.from_mapping({k: v for k, v in BRAND.items() if not k.startswith("_")}, "t"),
                              cart_row=lambda code: None)
    assert R.b["body_html"] == brandm.DEFAULTS["body_html"]
    describe.run([n], R)
    assert n["body"][0].startswith("<p>ACME STUDIO's Drift Tote in Sand is a tote bag.")
    assert describe.review_rows([n])[0]["Column"] == describe.DESC_COLUMN


def test_a_run_that_sent_nothing_is_not_counted_against_the_cap(key):
    held = types.SimpleNamespace(b=dict(BRAND, hold="parked"))
    assert not service.describe_ran(job.run([], held, factory=Fake().factory))
    f = dict(code=TOTE, vendor="Acme", title=("Drift Tote in Sand", "catalog", ""), ptype=("MEN BAGS BACKPACKS", "x", ""),
             composition=[], made_in=[], text=[])
    full = describe.draft(f, BRAND)
    full["adjective"] = dict(text="small", field="description", by="tool", basis="x")
    full["sentence2"] = dict(text="It is small.", field="description", by="tool", basis="x")
    full["bullets"] = [dict(text=f"Bullet {i} here", field="description", by="tool", basis="x") for i in range(5)]
    fake = Fake()
    rows = job.run([dict(code=TOTE, describe=full)], types.SimpleNamespace(b=BRAND), factory=fake.factory)
    assert fake.calls == [] and not service.describe_ran(rows)
    pending = [dict(code=TOTE, describe=describe.draft(f, BRAND))]
    assert service.describe_ran(job.run(pending, types.SimpleNamespace(b=BRAND), factory=Fake().factory))


def test_the_page_help_says_where_the_sources_and_the_red_come_from():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "pages" / "1_Order_confirmation_to_PO.py").read_text()
    help_ = src[src.index("DESCRIBE_HELP = ("):src.index("DESCRIBE_SENDS = ")]
    for words in ("live catalog's same style and fabric", "the title is red", "a different kind of product than the Type",
                  "PU-description", "import after the products are created"):
        assert words in help_, words


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
def test_the_house_shape_in_order(tmp_path, no_key):
    rows, review, checks, fills = build(tmp_path)
    b = body(review, JACKET)
    assert b["Value"] == (
        "<p>ACME STUDIO's Orbit Field Jacket in Slate is a waxed lightweight jacket.</p>\n"
        "<ul>\n"
        "<li>Composition: Shell: 100% Cotton &amp; Linen - Lining: 100% Cupro</li>\n"
        "  <li>Slate colourway</li>\n"
        "  <li>Cupro lining</li>\n"
        "  <li>Waxed finish</li>\n"
        "  <li>Zip fastening</li>\n"
        "<li>Made in Italy</li>\n"
        "</ul><br><p>SKU: ZQ0001AB1P0001 101</p>")
    assert b["Source"] == TOOL_DRAFT and b["Colour"] == "amber"
    assert "gaps against the house shape: no second sentence" in b["Why"]


def test_composition_is_verbatim_and_the_other_source_is_named(tmp_path, no_key):
    b = body(build(tmp_path)[1], JACKET)
    assert "Composition: Shell: 100% Cotton &amp; Linen - Lining: 100% Cupro</li>" in b["Value"]   # spaces only
    assert "composition from the OC line (AC - 1 line 1)" in b["Why"]
    assert "says 'Shell: 100% Cotton', used the OC line (AC - 1 line 1) first" in b["Why"]


@pytest.mark.parametrize("value,why", [
    ("-", "placeholder"), ("", "no order-time source"), ("U:LE L:TE S:RU", "codes, not words"),
    ("100% CO", "codes, not words"), ("63% WOOL/16% NYLON/16% COTT", "adds up to 95%"),
    ("52% COTTON/24% NYLON/20% VISCOSE/4", "adds up to 96%"), ("1_x001b_0_x001b_0% COTTON", "garbled"),
    ("100% <b>Cotton</b>", "markup"),
])
def test_a_missing_or_unusable_composition_is_red_and_never_written(tmp_path, no_key, value, why):
    d = docs()
    d[0]["lines"][2]["composition"] = value
    b = body(build(tmp_path, d=d)[1], TOTE)
    assert "Composition" not in b["Value"].split("Made in")[0].replace("Composition needed", "")
    assert "<li>Composition" not in b["Value"]
    assert b["Source"] == "guess" and b["Colour"] == "red" and "Composition needed" in b["Why"] and why in b["Why"]


def test_a_joor_export_fabrication_is_the_composition_where_materials_is_blank(tmp_path, no_key):
    d = docs()
    d[0]["lines"][2]["fabrication"] = "BOILED WOOL GABARDINE"
    d[0]["lines"][0]["fabrication"] = "WAXED COTTON CANVAS"
    rev = build(tmp_path, d=d)[1]
    tote, jacket = body(rev, TOTE), body(rev, JACKET)
    assert "<li>Composition: Boiled Wool Gabardine</li>" in tote["Value"] and "fabrication" in tote["Why"]
    assert "Composition: Shell: 100% Cotton &amp; Linen" in jacket["Value"] and "Waxed Cotton" not in jacket["Value"]
    assert "WAXED COTTON CANVAS" not in jacket["Why"]   # a fallback that was not needed is not a disagreement


def test_the_sheet_reader_reads_a_fabrication_column(tmp_path):
    import test_oc_sheet as ts
    from oc.adapters import sheet
    rows = [ts.HEAD + ["Fabrication"]] + [r + ["Loopback  fleece"] for r in ts.ROWS]
    path = tmp_path / "oc.csv"
    with open(path, "w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    docs_ = sheet.parse_checked(str(path), ts.cfg(columns=dict(ts.COLUMNS, fabrication="Fabrication")))
    assert {l["fabrication"] for d in docs_ for l in d["lines"]} == {"Loopback fleece"}


def test_no_made_in_placeholder_and_an_unknown_place_is_named(tmp_path, no_key):
    rev = build(tmp_path)[1]
    for code in (LOAFER, TOTE):
        b = body(rev, code)
        assert "Made in" not in b["Value"] and "no Made in (no known country of origin)" in b["Why"]
    assert "prints 'Atlantis', not a country the tool knows" in body(rev, LOAFER)["Why"]


def test_part_bullets_from_a_labelled_composition_even_when_the_line_is_coded(tmp_path, no_key):
    b = body(build(tmp_path)[1], LOAFER)
    assert "  <li>Leather upper</li>\n  <li>Textile lining</li>\n  <li>Rubber sole</li>" in b["Value"]
    assert "is a pair of loafers." in b["Value"] and b["Colour"] == "red"


def test_the_title_is_verbatim_with_a_gender_word_and_no_other_gender_or_season_word(tmp_path, no_key):
    d = docs()
    d[0]["lines"].append(dict(d[0]["lines"][3], line_no=5, gender="WOMEN", sizes={"S": 1}, qty=1, line_value=100.0))
    d[0]["lines"][3]["gender"] = "MEN"
    d[0]["total_qty"], d[0]["net"] = 5, 500.0
    rev = build(tmp_path, d=d)[1]
    vals = {x["Value"] for x in rev if x["Code"] == SHIRT and x["Column"] == describe.DESC_COLUMN}
    titles = {x["Value"] for x in rev if x["Code"] == SHIRT and x["Column"] == "Title"}
    assert any(t.startswith("Womens ") for t in titles)
    for t in titles:
        assert any(re.search(rf"ACME STUDIO's {re.escape(t)} is a (?:long-sleeve )?shirt\.", v) for v in vals)
    for v in vals:
        intro = v.split("</p>")[0]
        rest = v[len(intro):]
        assert not describe.banned(rest) and "Summer Blue colourway" not in v
    assert any("colour bullet (season word)" in x["Why"] for x in rev if x["Code"] == SHIRT
               and x["Column"] == describe.DESC_COLUMN)


def test_an_adjective_and_features_only_from_this_products_printed_words(tmp_path, no_key):
    d = docs()
    d[0]["lines"][2]["description"] = "DRIFT TOTE"
    b = body(build(tmp_path, d=d)[1], TOTE)
    assert "is a tote bag.</p>" in b["Value"] and "no adjective" in b["Why"]
    assert "<li>" not in b["Value"]   # its title is red (a raw colour code), so not even a colour bullet


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


def _no_describe(monkeypatch):
    """The draft as 7ba21f0 makes it: no description step."""
    monkeypatch.setattr(describe, "run", lambda pending, R, describer=None: [])
    monkeypatch.setattr(describe, "review_rows", lambda pending: [])


def test_the_po_keeps_the_template_and_the_description_is_its_own_review_rows(tmp_path, no_key, monkeypatch):
    rows, review, checks, fills = build(tmp_path)
    _no_describe(monkeypatch)
    rows0, review0, checks0, fills0 = build(tmp_path)
    assert rows == rows0 and checks == checks0 and fills == fills0   # Body HTML: the template, cell for cell
    assert all(r["Body HTML"].startswith("<p>Composition: ") for r in rows if r["Command"] == "NEW")
    assert review[:len(review0)] == review0   # every PO cell's Review row, where it was
    extra = review[len(review0):]
    assert [x["Column"] for x in extra] == [describe.DESC_COLUMN] * 4 and {x["Code"] for x in extra} == set(CODES)
    for x in extra:
        po = rows[x["Row"] - 2]
        assert po[poxlsx.CODE] == x["Code"] and po is next(r for r in rows if r[poxlsx.CODE] == x["Code"])
        assert x["Value"].startswith("<p>ACME STUDIO's ") and x["Colour"] in ("amber", "red")


def test_the_workbook_writes_the_descriptions_sheet_last_keyed_and_shaded(tmp_path, no_key):
    import openpyxl
    rows, review, checks, fills = build(tmp_path)
    path = tmp_path / "draft.xlsx"
    poxlsx.write_po(str(path), poxlsx.PO_HEADER, rows, review, checks, fills)
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Sheet1", "Review", "Checks", poxlsx.DESCRIPTIONS]
    ws = wb[poxlsx.DESCRIPTIONS]
    assert [c.value for c in ws[1]] == poxlsx.DESCRIPTION_COLS
    got = {ws.cell(r, 2).value: [ws.cell(r, c).value for c in range(1, 7)] for r in range(2, ws.max_row + 1)}
    assert set(got) == set(CODES)
    first = {r[poxlsx.CODE]: r for r in reversed(rows)}
    for code, (sku, _, title, desc, src, why) in got.items():
        assert sku == first[code]["Variant SKU"] and title == first[code]["Title"]
        assert desc == body(review, code)["Value"] and src == body(review, code)["Source"] and why
    fill = {ws.cell(r, 2).value: ws.cell(r, 4).fill.fgColor.rgb for r in range(2, ws.max_row + 1)}
    assert fill[JACKET].endswith(poxlsx.FILLS["amber"].fgColor.rgb[-6:]) and fill[TOTE].endswith(
        poxlsx.FILLS["red"].fgColor.rgb[-6:])
    assert poxlsx.read_descriptions(str(path))[0]["Description"] == got[next(iter(got))][3]


# ---- the AI job ---------------------------------------------------------------------------------------------
def test_no_key_sends_nothing_and_the_draft_is_the_tools_own(tmp_path, no_key):
    fake = Fake()
    assert build(tmp_path, fake) == build(tmp_path)
    assert fake.keys == [] and fake.calls == []


def test_a_held_config_reads_and_sends_nothing(tmp_path, key):
    fake = Fake()
    held = dict(BRAND, hold="waiting on the supplier")
    R = types.SimpleNamespace(b=held)
    assert job.run([], R, factory=fake.factory)[0]["Result"] == "WARN"
    assert fake.keys == [] and fake.calls == []
    assert job.plan([describe.draft(dict(code=TOTE, vendor="A", title=("T in S", "catalog", ""),
                                         ptype=("MEN BAGS BACKPACKS", "x", ""), composition=[], made_in=[], text=[]),
                                    held)], held) == ([], {})


def test_the_ai_fills_only_the_missing_parts_and_python_inserts_the_rest(tmp_path, key):
    fake = Fake()
    rows, review, checks, _ = build(tmp_path, fake)
    b = body(review, JACKET)
    assert b["Value"].startswith("<p>ACME STUDIO's Orbit Field Jacket in Slate is a waxed lightweight jacket. "
                                 "This jacket has a zip front.</p>")
    assert "  <li>Zip front closure</li>\n<li>Made in Italy</li>" in b["Value"]
    assert b["Source"] == AI_DRAFT and b["Colour"] == "amber"
    assert "second sentence by AI from 'ZIP FRONT' in the description" in b["Why"]
    assert "adjective by tool" in b["Why"]   # the tool's own adjective stood: the AI's was not asked for
    (row,) = [c for c in checks if c["Check"] == job.CHECK]
    assert row["Result"] == "INFO" and "US$" in row["Detail"]
    assert "adjective not asked for" not in row["Detail"]


def test_each_listing_of_one_code_keeps_its_own_title_and_takes_the_ai_prose(tmp_path, key):
    d = docs()
    d[0]["lines"][3]["description"] = "OXFORD SHIRT - BUTTON DOWN COLLAR"
    d[0]["lines"].append(dict(d[0]["lines"][3], line_no=5, gender="WOMEN", sizes={"S": 1}, qty=1, line_value=100.0))
    d[0]["lines"][3]["gender"] = "MEN"
    d[0]["total_qty"], d[0]["net"] = 5, 500.0
    collar = claim("Button down collar detail", "description", "BUTTON DOWN COLLAR")
    fake = Fake({SHIRT: dict(adjective=NONE, sentence_2=NONE, bullets=[collar])})
    rows, review, checks, _ = build(tmp_path, fake, d=d)
    descs = [x for x in review if x["Code"] == SHIRT and x["Column"] == describe.DESC_COLUMN]
    titles = {rows[x["Row"] - 2]["Title"] for x in descs}
    assert len(descs) == 2 and len(titles) == 2 and any(t.startswith("Womens ") for t in titles)
    for x in descs:
        title = rows[x["Row"] - 2]["Title"]
        assert f"ACME STUDIO's {title} is a" in x["Value"] and "<li>Button down collar detail</li>" in x["Value"]
    (row,) = [c for c in checks if c["Check"] == job.CHECK]
    assert "2 bullets" in row["Detail"]   # the jacket's and the shirt's: one code on two listings counts once


def test_a_red_body_stays_red_with_ai_prose(tmp_path, key):
    fake = Fake({TOTE: dict(adjective=claim("cotton", "product_class", "TOTE"), sentence_2=NONE, bullets=[])})
    b = body(build(tmp_path, fake)[1], TOTE)
    assert b["Colour"] == "red"


@pytest.mark.parametrize("bad,cat", [
    (claim("Zip front for women", "description", "ZIP FRONT"), "gender word"),
    (claim("Zip front for SS26", "description", "ZIP FRONT"), "season word"),
    (claim("Zip front by Acme", "description", "ZIP FRONT"), "brand word"),
    (claim("Zip front made with AI", "description", "ZIP FRONT"), "AI word"),
    (claim("Zip front <b>closure</b>", "description", "ZIP FRONT"), "characters"),
    (claim("Zip front closure.", "description", "ZIP FRONT"), "bullet shape"),
    (claim("Made in Portugal", "description", "ZIP FRONT"), "labelled line"),
    (claim("Composition: 100% Cotton", "composition", "100% Cotton"), "labelled line"),
    (claim("Zip front closure", "description", "ZIP BACK"), "quote not in its field"),
    (claim("Zip front closure", "fabric_name", "ZIP FRONT"), "field"),
    (claim("Zip front in cashmere", "description", "ZIP FRONT"), "words not in the quote"),
    (claim("Two way zip front", "description", "ZIP FRONT"), "words not in the quote"),
    (claim("Zip " * 10 + "front", "description", "ZIP FRONT"), "word count"),
    (claim("Zip front ZQ0001AB1", "description", "ZIP FRONT"), "code-like token"),
])
def test_a_bullet_that_breaks_a_rule_is_dropped_and_never_shown(tmp_path, key, bad, cat):
    fake = Fake({JACKET: dict(adjective=NONE, sentence_2=NONE, bullets=[bad])})
    rows, review, checks, _ = build(tmp_path, fake)
    b = body(review, JACKET)
    assert bad["text"] not in b["Value"] and bad["text"] not in b["Why"]
    assert f"an AI bullet ({cat}" in b["Why"] or cat in b["Why"]
    (row,) = [c for c in checks if c["Check"] == job.CHECK]
    assert f"bullet: {cat}" in row["Detail"] and bad["text"] not in row["Detail"]


def test_an_ai_bullet_given_twice_is_used_once(tmp_path, key):
    d = docs()
    d[0]["lines"][2]["description"] = "DRIFT TOTE - TWIN HANDLES"
    twice = claim("Twin handles detail", "description", "TWIN HANDLES")
    b = body(build(tmp_path, Fake({TOTE: dict(adjective=NONE, sentence_2=NONE, bullets=[twice, twice])}), d=d)[1], TOTE)
    assert b["Value"].count("Twin handles detail") == 1 and "an AI bullet (repeats a bullet)" in b["Why"]


def test_a_live_same_style_and_fabric_composition_is_kept_as_html(tmp_path, no_key):
    p = write_catalog(tmp_path)
    r = base._variant("8", "Drift Tote in Black", "UNISEX BAGS SHOPPER & TOTE BAGS", "Active", "ZQ0003EF3P0003 900",
                      "BLACK", "OS", "ZXFW26800", "800.00", "800.00", "300.00", True)
    r.update(Vendor="Acme Studio", **{"Body HTML": "<ul><li>Composition: 100% Calf &amp; Lamb</li></ul>"})
    with open(p, "a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, fieldnames=base.CAT_COLS).writerow(r)
    rows, review, *_ = draftm.build(docs(), BRAND, catm.load(p), histm.History([("F/W 26", [])]), base._tags())
    b = body(review, TOTE)
    assert "<li>Composition: 100% Calf &amp; Lamb</li>" in b["Value"] and "same style+fabric" in b["Why"]


def test_grounding_is_per_product_never_the_whole_request(tmp_path, key):
    other = claim("Square toe shape", "description", "SQUARE TOE")   # a fact of no product here
    d = docs()
    d[0]["lines"][1]["description"] = "MESA LOAFER - SQUARE TOE"
    fake = Fake({JACKET: dict(adjective=NONE, sentence_2=NONE, bullets=[other])})
    b = body(build(tmp_path, fake, d=d)[1], JACKET)
    assert "Square toe" not in b["Value"] and "quote not in its field" in b["Why"]


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


def test_the_caption_names_exactly_what_the_request_sends(tmp_path, key):
    fake = Fake()
    build(tmp_path, fake)
    req = fake.calls[0]
    evs = sent(req)
    assert {p["code"] for p in evs} == set(CODES)
    for ev in evs:
        assert list(ev) == [k for k, _ in job.SENT]
        assert set(ev["fields"]) <= set(job.FIELDS)
    caption = job.sends()
    assert all(words in caption for _, words in job.SENT)
    assert "No prices, costs or quantities." in caption and "supplier names (oc_supplier)" in caption
    assert "the title goes as written and can carry them" in caption   # gender and season can ride in the title
    text = json.dumps(req)
    for leak in ("100.0", "ACME FW", "WOMEN", '"MEN"', "gender", "collection", "price", "qty"):
        assert leak not in text.split('"messages"')[1], leak
    assert req["model"] == "claude-opus-5-5" and req["output_config"]["effort"] == "medium"
    assert req["betas"] == [common.FALLBACK_BETA] and req["fallbacks"] == "default"
    assert req["system"][1]["cache_control"] == {"type": "ephemeral"} and req["system"][1]["text"] == job.FEW_SHOT
    assert all(c["system"] == req["system"] for c in fake.calls)


def test_every_limit_the_checks_enforce_is_stated_in_the_prompt():
    s = job.SYSTEM
    for cap in (job.ADJ_CAP, job.SENTENCE_CAP, job.BULLET_CAP, job.QUOTE_CAP):
        assert f"{cap} characters" in s
    for lo, hi in (job.ADJ_WORDS, job.SENTENCE_WORDS, job.BULLET_WORDS):
        assert f"{lo} to {hi}" in s
    assert all(w in s for w in job.GENERIC) and "- ' , . / & % ( )" in s
    for w in ("gender", "season", "brand", "ChatGPT", "code", "markup", "= + - or @", "Composition", "Made in", "SKU"):
        assert w in s


def test_a_failed_request_keeps_the_tools_descriptions(tmp_path, key):
    import anthropic
    err = anthropic.APIConnectionError(request=types.SimpleNamespace(method="POST", url="https://x"))
    fake = Fake(error=err)
    rows, review, checks, _ = build(tmp_path, fake)
    (row,) = [c for c in checks if c["Check"] == job.CHECK]
    assert row["Result"] == "WARN" and ag.SENTINEL not in row["Detail"]
    assert body(review, JACKET)["Source"] == TOOL_DRAFT


def test_a_refusal_or_bad_json_keeps_the_tools_descriptions(tmp_path, key):
    for msg in (ag.message(text="{}", stop="refusal", category="cyber"), ag.message(text="not json")):
        fake = Fake(reply=lambda kw, n, m=msg: m)
        rows, review, checks, _ = build(tmp_path, fake)
        assert [c["Result"] for c in checks if c["Check"] == job.CHECK] == ["WARN"]
        assert body(review, JACKET)["Source"] == TOOL_DRAFT


def _oc(monkeypatch):
    monkeypatch.setattr(service, "adapter", lambda cfg: types.SimpleNamespace(parse_checked=lambda p: docs()))


def test_the_service_drafts_prose_only_when_asked(tmp_path, key, monkeypatch):
    _oc(monkeypatch)
    fake = Fake()
    po, review, checks = service.draft("oc.pdf", write_catalog(tmp_path), BRAND, ai_factory=fake.factory)
    assert fake.calls == [] and not service.describe_ran(checks)
    assert set(review[review["Column"] == describe.DESC_COLUMN]["Source"]) <= {TOOL_DRAFT, "guess"}
    po, review, checks = service.draft("oc.pdf", write_catalog(tmp_path), BRAND, ai_factory=fake.factory,
                                       ai_describe=True)
    assert len(fake.calls) == 1 and service.describe_ran(checks)
    assert any("by AI from" in w for w in review[review["Column"] == describe.DESC_COLUMN]["Why"])


def _cfg(tmp_path):
    import yaml
    cfgp = tmp_path / "b.yaml"
    cfgp.write_text(yaml.safe_dump({k: v for k, v in BRAND.items() if not k.startswith("_")}))
    return str(cfgp)


def test_the_cli_flag_says_when_there_is_no_key(tmp_path, no_key, monkeypatch, capsys):
    _oc(monkeypatch)
    out, plain = tmp_path / "d.xlsx", tmp_path / "p.xlsx"
    args = ["draft", "--oc", "oc.pdf", "--catalog", write_catalog(tmp_path), "--brand", "x", "--brand-config",
            _cfg(tmp_path)]
    cli.main(args + ["--out", str(out), "--ai-describe"])
    text = capsys.readouterr().out
    assert "AI description drafting is off: no ANTHROPIC_API_KEY" in text and job.CHECK not in text
    cli.main(args + ["--out", str(plain)])
    assert ag.workbook(out.read_bytes()) == ag.workbook(plain.read_bytes())


def test_the_cli_flag_drafts_with_a_key(tmp_path, key, monkeypatch, capsys):
    _oc(monkeypatch)
    fake = Fake()
    monkeypatch.setattr(ai, "make_client", fake.factory)
    cli.main(["draft", "--oc", "oc.pdf", "--catalog", write_catalog(tmp_path), "--brand", "x", "--brand-config",
              _cfg(tmp_path), "--out", str(tmp_path / "d.xlsx"), "--ai-describe"])
    assert len(fake.calls) == 1 and f"[INFO] {job.CHECK}" in capsys.readouterr().out


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
def test_the_page_offers_the_description_box_off_by_default_saying_what_it_sends(tmp_path, monkeypatch, key):
    import test_oc_page as tp
    at = tp._page_with_brand(tmp_path, monkeypatch, "adapter: otb\noc_label: MM\noc_supplier: X Ltd\n")
    (box,) = [c for c in at.checkbox if c.label.startswith("Write the NEW descriptions' prose with AI")]
    assert box.value is False and "never writes the Composition line" in box.help
    assert [c.value for c in at.caption if c.value.startswith("Ticking the description box")] == [job.sends()]


def test_the_page_has_no_description_box_without_a_key(tmp_path, monkeypatch):
    import test_oc_page as tp
    monkeypatch.delenv(ai.KEY_NAME, raising=False)
    monkeypatch.setattr(ai, "api_key", lambda: None)
    at = tp._page_with_brand(tmp_path, monkeypatch, "adapter: otb\noc_label: MM\noc_supplier: X Ltd\n")
    assert not [c for c in at.checkbox if c.label.startswith("Write the NEW descriptions")]
    assert not [c for c in at.caption if c.value.startswith("Ticking the description box")]
