"""The catalog colour vote (oc/resolve.py _colour_votes) reads only what the brand's config says a code holds.

An empty colour code gives no vote: a colour-less join_key ("{style}"), or a reader that printed no colour, once
made every colour-less live code "share" it, so a NEW product took a green colour from an unrelated product
(Palm Angels F/W 26: every line BLACK; the bare-style probe of 2026-11-14). "Same fabric+colour" is read only where
the config says where the fabric is (resolve.vote_key): after the space in "{style} {fabric} {colour}", and after
the style in "{style}{fabric} {colour}" only for a reader whose style has one length (otb: 9, Margiela and MM6).
Diesel, Lemaire and Stone Island use that join_key with other style lengths and a sheet reader, so body[9:] there
is no fabric, and their votes count only as "same colour code". The AI title evidence reads codes the same way.
Invented codes throughout.
"""
import os

import pytest
import yaml

from oc import brand as brandm, catalog as catm, finalize, resolve, service
from oc.adapters import otb
from test_oc_tool import BRAND as TOOL_BRAND, _finalize_po, catalog_path  # noqa: F401
from test_oc_agent import Fake, base, build, _line, BRAND, key, one, past_po, sent_evidence, write_catalog  # noqa: F401


def _doc(*lines):
    return [dict(doc="SS - 1", collection="MM MALE RTW AVP", gender="MEN", currency="European Euro", label="MM",
                 lines=list(lines), total_qty=sum(l["qty"] for l in lines), net=sum(l["line_value"] for l in lines))]


def _live(pid, code, title="Field Jacket in Black", colour="BLACK"):
    return base._variant(pid, title, "MEN CLOTHING JACKETS LIGHTWEIGHT", "Active", code, colour, "M", f"ZXFW26{800 + int(pid)}",
                         "300.00", "300.00", "90.00", True)


def _draft(tmp_path, join_key, line, live, **brand):
    _, review, _, _ = build(tmp_path, None, d=_doc(line), cat_extra=live, brand=dict(BRAND, join_key=join_key, **brand))
    return review


def _colour(review, code):
    return one(review, code, "Option1 Value"), one(review, code, "Title")


def test_a_colourless_join_key_borrows_no_colour_from_an_unrelated_product(tmp_path):
    review = _draft(tmp_path, "{style}", _line(1, "ZT27PT0309", "", "", "PANTS", {"M": 1}, 100.0),
                    [_live("8", "ZT27JK0209")])
    fam, title = _colour(review, "ZT27PT0309")
    assert fam["Colour"] == resolve.RED and fam["Value"] == "" and fam["Source"] == "guess"
    assert "Black" not in title["Value"] and title["Colour"] == resolve.RED


def test_an_empty_colour_code_from_the_reader_gets_no_vote_however_many_live_codes_have_none(tmp_path):
    live = [_live(str(9 + i), f"ZXPA0000{i}FW26", title=f"Logo Hoodie {i} in Black") for i in range(3)]
    review = _draft(tmp_path, "{style} {colour}", _line(1, "ZXPA00009FW26", "", "", "HOODIE", {"M": 1}, 100.0), live)
    fam, title = _colour(review, "ZXPA00009FW26")
    assert fam["Colour"] == resolve.RED and fam["Value"] == "" and "BLACK" not in fam["Why"]
    assert "Black" not in title["Value"] and title["Colour"] == resolve.RED


def test_without_a_fabric_in_the_join_key_one_other_style_is_no_same_fabric_vote(tmp_path):
    review = _draft(tmp_path, "{style} {colour}", _line(1, "ZXSA00001", "", "001", "JACKET", {"M": 1}, 100.0),
                    [_live("8", "ZXSA00002 001")])
    fam, _ = _colour(review, "ZXSA00001 001")
    assert fam["Colour"] == resolve.RED and fam["Value"] == ""


def test_without_a_fabric_in_the_join_key_agreeing_styles_vote_as_the_same_colour_code(tmp_path):
    review = _draft(tmp_path, "{style} {colour}", _line(1, "ZXSA00001", "", "001", "JACKET", {"M": 1}, 100.0),
                    [_live("8", "ZXSA00002 001"), _live("9", "ZXSA00003 001")])
    fam, _ = _colour(review, "ZXSA00001 001")
    assert (fam["Value"], fam["Source"], fam["Colour"]) == ("BLACK", "catalog", resolve.GREEN)
    assert fam["Why"].startswith("same colour code 001 is BLACK on 2 of 2")


def test_a_fabric_after_the_space_is_compared_with_the_colour_code(tmp_path):
    review = _draft(tmp_path, "{style} {fabric} {colour}", _line(1, "ZXRO000001", "ZF", "09", "JACKET", {"M": 1}, 100.0),
                    [_live("8", "ZXRO000002 ZF 09")])
    fam, _ = _colour(review, "ZXRO000001 ZF 09")
    assert (fam["Value"], fam["Source"], fam["Colour"]) == ("BLACK", "catalog", resolve.GREEN)
    assert fam["Why"].startswith("same fabric+colour ZF 09 is BLACK on 1 of 1")


def test_a_fabric_after_the_space_with_no_colour_code_gets_no_vote(tmp_path):
    review = _draft(tmp_path, "{style} {fabric} {colour}", _line(1, "ZXRO000001", "ZF", "", "JACKET", {"M": 1}, 100.0),
                    [_live("8", "ZXRO000002 ZF"), _live("9", "ZXRO000003 ZF")])
    fam, _ = _colour(review, "ZXRO000001 ZF")
    assert fam["Colour"] == resolve.RED and fam["Value"] == ""


@pytest.mark.parametrize("adapter", ["sheet", "joor", "ai"])
@pytest.mark.parametrize("fabric", ["ZZ0123", "QQ9123"])
def test_a_reader_with_no_style_length_never_reads_a_fabric_after_character_9(tmp_path, adapter, fabric):
    """Diesel/Lemaire/Stone Island shape: a 6-character style and a 6-character fabric, read by a sheet reader. A
    live product of another style (same fabric, or another fabric sharing the body's last 3) is no same-fabric vote."""
    review = _draft(tmp_path, "{style}{fabric} {colour}", _line(1, "ZX0001", "ZZ0123", "BK001", "JACKET", {"M": 1}, 100.0),
                    [_live("8", f"ZX0002{fabric} BK001")], adapter=adapter)
    fam, _ = _colour(review, "ZX0001ZZ0123 BK001")
    assert fam["Colour"] == resolve.RED and fam["Value"] == "" and "same fabric" not in fam["Why"]


def test_an_otb_line_whose_style_is_not_nine_characters_reads_no_fabric(tmp_path):
    review = _draft(tmp_path, "{style}{fabric} {colour}", _line(1, "ZX0001", "ZZ0123", "BK001", "JACKET", {"M": 1}, 100.0),
                    [_live("8", "ZX0002QQ9123 BK001")])
    fam, _ = _colour(review, "ZX0001ZZ0123 BK001")
    assert fam["Colour"] == resolve.RED and fam["Value"] == ""


@pytest.mark.parametrize("join_key,adapter,body,colour,style,want", [
    (None, "otb", "S99WZ0004P0004", "T0004", None, ("T0004", "P0004")),
    ("{style}{fabric} {colour}", "otb", "S99WZ0004P0004", "T0004", "S99WZ0004", ("T0004", "P0004")),
    ("{style}{fabric} {colour}", "otb", "ZX0001ZZ0123", "BK001", "ZX0001", ("BK001", None)),
    ("{style}{fabric} {colour_code}", "otb", "S99WZ0004P0004", "BK", None, ("BK", "P0004")),
    (None, "sheet", "S99WZ0004P0004", "T0004", None, ("T0004", None)),
    ("{style}{fabric} {colour}", "joor", "ZX0001ZZ0123", "BK001", None, ("BK001", None)),
    ("{style}{fabric} {colour}", "ai", "S99WZ0004P0004", "T0004", "S99WZ0004", ("T0004", None)),
    ("{style} {fabric} {colour}", "sheet", "ZXRO000001", "ZF 09", None, ("ZF 09", "")),
    ("{style} {fabric} {colour}", "sheet", "ZXRO000001", "ZF", None, ("", None)),
    ("{style} {colour}", "otb", "ZXSA00001", "001", None, ("001", None)),
    ("{style} {colour_code}", "otb", "ZXSA00001", "BK", None, ("BK", None)),
    ("{style}", "otb", "ZT27PT0309", "", None, ("", None)),
])
def test_how_the_config_says_a_code_is_read(join_key, adapter, body, colour, style, want):
    brand = dict(adapter=adapter, **({} if join_key is None else {"join_key": join_key}))
    assert resolve.vote_key(brand)(body, colour, style) == want


def test_only_the_otb_reader_has_one_style_length_and_it_is_nine():
    assert resolve.STYLE_WIDTH == {"otb": 9}
    assert otb.STYLE.match("S99WZ0004") and otb.STYLE.match("SB1WZ0004")
    assert not otb.STYLE.match("S99WZ00041") and not otb.STYLE.match("S99WZ004")


def test_the_ai_evidence_reads_same_fabric_as_the_vote_does(tmp_path, key):
    """Review 2026-11-16-buyer-2 F6: with a join_key that has no fabric, another style's uses of the colour code are
    other-fabric evidence, never same_fabric_and_colour_code (the vote no longer counts them so)."""
    fake = Fake()
    hist = past_po(tmp_path, rows=[("NEW", "ZXSA00003 001", "F/W 26", "Field Coat in Black", "MEN X", "BLACK", "M")])
    build(tmp_path, fake, d=_doc(_line(1, "ZXSA00001", "", "001", "JACKET", {"M": 1}, 100.0)), hist=hist,
          cat_extra=[_live("8", "ZXSA00002 001")], brand=dict(BRAND, join_key="{style} {colour}"))
    (e,) = [e for req in fake.calls for e in sent_evidence(req)]
    assert e["same_fabric_and_colour_code"] == []
    assert e["same_colour_code_other_fabrics"] == dict(products=2, uses=[
        dict(colour_words="Black", colour_family="BLACK", catalog=1, past_pos=1)])


def test_an_empty_colour_code_is_named_as_such(tmp_path):
    cat = catm.load(write_catalog(tmp_path, [_live("8", "ZT27JK0209")]))
    R = resolve.Resolver(dict(BRAND, join_key="{style}"), cat, past_po(tmp_path), base._tags())
    name, src, why = R.colour_name("ZT27PT0309")
    assert (name, src) == ("", "guess") and why.startswith("ZT27PT0309 carries no colour code")
    assert R.colour_family("ZT27PT0309", name)[:2] == ("", "guess")


def test_the_ai_evidence_gets_no_colour_for_a_code_the_vote_reads_none_in(tmp_path, key):
    fake = Fake()
    build(tmp_path, fake, d=_doc(_line(1, "ZXRO000001", "ZF", "", "JACKET", {"M": 1}, 100.0)),
          cat_extra=[_live("8", "ZXRO000002 ZF")], brand=dict(BRAND, join_key="{style} {fabric} {colour}"))
    (e,) = [e for req in fake.calls for e in sent_evidence(req)]
    assert e["same_fabric_and_colour_code"] == [] and e["same_colour_code_other_fabrics"] == dict(products=0, uses=[])


# ---- every product a vote sees is counted (review 2026-11-16-buyer-2 F4) ------------------------------------------
def _blank(pid, code):
    return _live(pid, code, title=f"Field Jacket {pid}", colour="")


def test_blank_products_count_against_the_winner(tmp_path):
    review = _draft(tmp_path, "{style} {colour}", _line(1, "ZXSA00001", "", "001", "JACKET", {"M": 1}, 100.0),
                    [_live("8", "ZXSA00002 001"), _live("9", "ZXSA00003 001")] + [_blank(str(10 + i), f"ZXSA0001{i} 001")
                                                                                  for i in range(3)])
    fam, title = _colour(review, "ZXSA00001 001")
    assert fam["Colour"] == resolve.RED and fam["Value"] == "" and "Black" not in title["Value"]


def test_a_winner_with_blanks_beside_it_names_them(tmp_path):
    review = _draft(tmp_path, "{style} {colour}", _line(1, "ZXSA00001", "", "001", "JACKET", {"M": 1}, 100.0),
                    [_live(str(8 + i), f"ZXSA0000{2 + i} 001") for i in range(3)] + [_blank("12", "ZXSA00012 001")])
    fam, _ = _colour(review, "ZXSA00001 001")
    assert (fam["Value"], fam["Colour"]) == ("BLACK", resolve.GREEN)
    assert fam["Why"] == "same colour code 001 is BLACK on 3 of 4 product(s), 1 blank"


@pytest.mark.parametrize("second", [dict(title="Field Jacket in Dust", colour="DUST"), dict(title="Field Jacket 9", colour="")])
def test_the_same_fabric_needs_sixty_percent_of_its_products_too(tmp_path, second):
    review = _draft(tmp_path, "{style} {fabric} {colour}", _line(1, "ZXRO000001", "ZF", "09", "JACKET", {"M": 1}, 100.0),
                    [_live("8", "ZXRO000002 ZF 09"), _live("9", "ZXRO000003 ZF 09", **second)])
    fam, _ = _colour(review, "ZXRO000001 ZF 09")
    assert fam["Colour"] == resolve.RED and fam["Value"] == ""


def test_the_same_fabric_with_a_blank_beside_two_agreeing_names_it(tmp_path):
    review = _draft(tmp_path, "{style} {fabric} {colour}", _line(1, "ZXRO000001", "ZF", "09", "JACKET", {"M": 1}, 100.0),
                    [_live("8", "ZXRO000002 ZF 09"), _live("9", "ZXRO000003 ZF 09"), _blank("10", "ZXRO000004 ZF 09")])
    fam, _ = _colour(review, "ZXRO000001 ZF 09")
    assert fam["Why"] == "same fabric+colour ZF 09 is BLACK on 2 of 3 product(s), 1 blank" and fam["Colour"] == resolve.GREEN


def test_a_same_fabric_that_disagrees_is_not_overruled_by_other_fabrics(tmp_path):
    review = _draft(tmp_path, "{style}{fabric} {colour}", _line(1, "ZX0000001", "P0001", "963", "JACKET", {"M": 1}, 100.0),
                    [_live("8", "ZX0000002P0001 963"), _live("9", "ZX0000003P0001 963", title="Field Jacket in Navy",
                                                             colour="NAVY"),
                     _live("10", "ZX0000004P0002 963"), _live("11", "ZX0000005P0002 963")])
    fam, _ = _colour(review, "ZX0000001P0001 963")
    assert fam["Colour"] == resolve.RED and fam["Value"] == ""


def test_a_same_fabric_holding_only_blanks_leaves_the_vote_to_other_fabrics(tmp_path):
    """Kept on purpose, as before the denominator change: a blank says nothing, so it neither wins nor blocks."""
    review = _draft(tmp_path, "{style}{fabric} {colour}", _line(1, "ZX0000001", "P0001", "963", "JACKET", {"M": 1}, 100.0),
                    [_blank("8", "ZX0000002P0001 963"), _live("10", "ZX0000004P0002 963"), _live("11", "ZX0000005P0002 963")])
    fam, _ = _colour(review, "ZX0000001P0001 963")
    assert fam["Why"] == "same colour code 963 is BLACK on 2 of 2 product(s)" and fam["Colour"] == resolve.GREEN


@pytest.mark.parametrize("live,why", [
    (2, "colour code 001: no name on 60% of its 5 catalog products (3 untitled); the OC has no colour description"),
    (0, "colour code 001: no name on 60% of its 3 catalog products (3 untitled); the OC has no colour description")])
def test_a_colour_name_left_unvoted_by_blanks_says_so(tmp_path, live, why):
    extra = [_live(str(8 + i), f"ZXSA0000{2 + i} 001") for i in range(live)] + [_blank(str(10 + i), f"ZXSA0001{i} 001")
                                                                                for i in range(3)]
    cat = catm.load(write_catalog(tmp_path, extra))
    R = resolve.Resolver(dict(BRAND, join_key="{style} {colour}"), cat, past_po(tmp_path), base._tags())
    assert R.colour_name("ZXSA00001 001") == ("001", "guess", why)


# ---- a title with no colour words never imports (review 2026-11-16-buyer-2 F7) ---------------------------------
@pytest.mark.parametrize("title,fails", [("Replica Sneaker in ", True), ("Womens Replica Sneaker IN", True),
                                         ("in", True), ("Slip In in Black", False), ("Replica Sneaker in Satin", False),
                                         ("Replica Sneaker in Black", False)])
def test_finalize_fails_a_new_title_that_lost_its_colour_words(catalog_path, tmp_path, title, fails):
    """A person may fill Option1 by hand and leave the red "Pants in " title; Option1 alone let it through."""
    prefix = str(tmp_path / "x-")
    out = finalize.run(_finalize_po(tmp_path, title, "BLACK"), TOOL_BRAND, catm.load(catalog_path), prefix)
    (check,) = [c for c in out if c["Check"] == finalize.NO_COLOUR_CHECK]
    assert (check["Result"] == "FAIL") is fails, check
    if fails:
        assert f"'{title.strip()}'" in check["Detail"] and service.blocking([check]) == [check]
        assert not any(os.path.exists(prefix + n) for n in finalize.OUTPUTS)


# ---- the bar is the brand's own: colour_vote_bar, 0.6 unless the config says (slice 2c) ---------------------------
def _config(**kw):
    """A brand config loaded as the page loads one, with the colour code after the space unless kw says."""
    return brandm.from_mapping(dict({k: v for k, v in BRAND.items() if not k.startswith("_")},
                                    **{"join_key": "{style} {colour}", **kw}), "test config")


def _shared(black, navy=0, blank=0):
    """Live products of other styles sharing colour code 001: `black` BLACK, `navy` NAVY, `blank` with no colour."""
    return ([_live(str(20 + i), f"ZXSB0000{i} 001") for i in range(black)]
            + [_live(str(40 + i), f"ZXSC0000{i} 001", title=f"Field Jacket {40 + i} in Navy", colour="NAVY")
               for i in range(navy)]
            + [_blank(str(60 + i), f"ZXSD0000{i} 001") for i in range(blank)])


def _resolver(tmp_path, live, **kw):
    return resolve.Resolver(_config(**kw), catm.load(write_catalog(tmp_path, live)), past_po(tmp_path), base._tags())


def test_a_config_with_no_colour_vote_bar_loads_sixty_percent(tmp_path):
    assert _config()["colour_vote_bar"] == 0.6 and _config(colour_vote_bar=0.8)["colour_vote_bar"] == 0.8
    path = tmp_path / "zz.local.yaml"
    path.write_text(yaml.safe_dump(dict({k: v for k, v in BRAND.items() if not k.startswith("_")})) +
                    "colour_vote_bar: 0.8\n")
    assert brandm.load(str(path))["colour_vote_bar"] == 0.8


@pytest.mark.parametrize("bar,voted", [(None, True), (0.6, True), (0.8, False)])
def test_a_brand_at_eighty_percent_refuses_a_seventy_percent_winner_that_sixty_accepts(tmp_path, bar, voted):
    review = _draft(tmp_path, "{style} {colour}", _line(1, "ZXSA00001", "", "001", "JACKET", {"M": 1}, 100.0),
                    _shared(7, navy=3), **({} if bar is None else {"colour_vote_bar": bar}))
    fam, title = _colour(review, "ZXSA00001 001")
    if voted:
        assert (fam["Value"], fam["Source"], fam["Colour"]) == ("BLACK", "catalog", resolve.GREEN)
        assert fam["Why"] == "same colour code 001 is BLACK on 7 of 10 product(s)" and "Black" in title["Value"]
    else:
        assert fam["Colour"] == resolve.RED and fam["Value"] == "" and "Black" not in title["Value"]


@pytest.mark.parametrize("bar,black,navy", [(None, 3, 2), (0.8, 4, 1), (1, 5, 0)])
def test_a_winner_at_exactly_the_bar_is_voted(tmp_path, bar, black, navy):
    R = _resolver(tmp_path, _shared(black, navy=navy), **({} if bar is None else {"colour_vote_bar": bar}))
    assert R.colour_family("ZXSA00001 001", "") == (
        "BLACK", "catalog", f"same colour code 001 is BLACK on {black} of {black + navy} product(s)")


@pytest.mark.parametrize("bar", [0.5, 0.4, 0, -0.8, 1.01, 80, True, "0.8", "80%", None, [0.8]])
def test_a_colour_vote_bar_out_of_range_is_refused_at_load(bar):
    with pytest.raises(brandm.BrandError, match=r"colour_vote_bar is .* above 0\.5 and at most 1 .*, got "):
        _config(colour_vote_bar=bar)


@pytest.mark.parametrize("bar", [0.51, 0.8, 1, 1.0])
def test_a_colour_vote_bar_in_range_loads(bar):
    assert _config(colour_vote_bar=bar)["colour_vote_bar"] == bar


@pytest.mark.parametrize("bar,named,shown", [(None, 2, "60%"), (0.8, 1, "80%"), (0.675, 1, "67.5%")])
def test_the_why_states_the_brands_own_bar(tmp_path, bar, named, shown):
    """Blank products leave no name a vote: at the default bar 2 named of 5, at a brand's own bar one named product
    (another style's one product never decides), so it is the blanks, not the bar, that the Why names."""
    R = _resolver(tmp_path, _shared(named, blank=3), **({} if bar is None else {"colour_vote_bar": bar}))
    assert R.colour_name("ZXSA00001 001") == (
        "001", "guess", f"colour code 001: no name on {shown} of its {named + 3} catalog products (3 untitled); "
                        "the OC has no colour description")


def test_the_example_config_documents_the_bar_with_a_line_that_loads():
    text = open(os.path.join(os.path.dirname(brandm.__file__), "brands", "example.yaml")).read()
    (line,) = [x for x in text.splitlines() if x.startswith("# colour_vote_bar:")]
    assert brandm.from_text(text + line[2:] + "\n", "example.yaml")["colour_vote_bar"] == 0.8


# ---- a vote the brand's own bar refuses says so (review 2026-11-17-buyer-11 F1, F4) --------------------------------
def _bar_why(how, colour, said, share, shown):
    return f"{how} {colour} {said} on {share}, below the {shown} this brand's colour vote needs"


@pytest.mark.parametrize("bar,shown", [(0.8, "80%"), (0.675, "67.5%")])
@pytest.mark.parametrize("blank,share", [(0, "6 of 9 product(s)"), (2, "6 of 9 product(s), 2 blank")])
def test_a_vote_the_brands_bar_refuses_names_the_share_and_the_bar(tmp_path, bar, shown, blank, share):
    R = _resolver(tmp_path, _shared(6, navy=3 - blank, blank=blank), colour_vote_bar=bar)
    title = _bar_why("same colour code", "001", "titled 'Black'", share, shown)
    family = _bar_why("same colour code", "001", "is BLACK", share, shown)
    assert R.colour_name("ZXSA00001 001") == ("001", "guess", title + "; the OC has no colour description")
    assert R.colour_family("ZXSA00001 001", "001") == ("", "guess", f"no colour family for '001'; {family}")
    assert R.colour_family("ZXSA00001 001", "Black") == (
        "BLACK", "rule", f"'Black' matches the BLACK keyword list; {family}")


def test_at_the_default_bar_a_refused_vote_keeps_its_wording(tmp_path):
    R = _resolver(tmp_path, _shared(5, navy=4))
    assert R.colour_name("ZXSA00001 001") == (
        "001", "guess", "colour code 001 names disagree across 9 catalog products; the OC has no colour description")
    assert R.colour_family("ZXSA00001 001", "001") == ("", "guess", "no colour family for '001'")
    assert R.colour_family("ZXSA00001 001", "Black") == ("BLACK", "rule", "'Black' matches the BLACK keyword list")


def test_a_vote_left_to_one_product_never_blames_the_bar(tmp_path):
    R = _resolver(tmp_path, _shared(1), colour_vote_bar=0.8)
    assert R.colour_name("ZXSA00001 001") == (
        "001", "guess", "colour code 001 names disagree across 1 catalog products; the OC has no colour description")
    assert R.colour_family("ZXSA00001 001", "001") == ("", "guess", "no colour family for '001'")


@pytest.mark.parametrize("bar,voted", [(None, True), (0.8, False)])
def test_the_same_fabric_tier_needs_the_brands_bar_too(tmp_path, bar, voted):
    navy = [_live(str(40 + i), f"ZXRO00001{i} ZF 09", title=f"Field Jacket {40 + i} in Navy", colour="NAVY")
            for i in range(3)]
    live = [_live(str(20 + i), f"ZXRO00000{2 + i} ZF 09") for i in range(7)] + navy
    line = _line(1, "ZXRO000001", "ZF", "09", "JACKET", {"M": 1}, 100.0)
    review = _draft(tmp_path, "{style} {fabric} {colour}", line, live,
                    **({} if bar is None else {"colour_vote_bar": bar}))
    fam, _ = _colour(review, "ZXRO000001 ZF 09")
    if voted:
        assert (fam["Value"], fam["Colour"]) == ("BLACK", resolve.GREEN)
        assert fam["Why"] == "same fabric+colour ZF 09 is BLACK on 7 of 10 product(s)"
    else:
        assert (fam["Value"], fam["Colour"]) == ("", resolve.RED)
        assert fam["Why"].endswith(_bar_why("same fabric+colour", "ZF 09", "is BLACK", "7 of 10 product(s)", "80%"))


@pytest.mark.parametrize("bar,voted", [(None, False), (0.55, True)])
def test_a_bar_below_sixty_percent_accepts_what_sixty_refuses(tmp_path, bar, voted):
    R = _resolver(tmp_path, _shared(4, navy=3), **({} if bar is None else {"colour_vote_bar": bar}))
    want = ("BLACK", "catalog", "same colour code 001 is BLACK on 4 of 7 product(s)") if voted else (
        "", "guess", "no colour family for '001'")
    assert R.colour_family("ZXSA00001 001", "001") == want


@pytest.mark.parametrize("desc,want", [("Black", ("Black", "catalog")), ("black", ("Black", "catalog")),
                                       ("Navy", ("Navy", "cart"))])
def test_a_cart_colour_its_same_fabric_agrees_with_is_green(tmp_path, desc, want):
    """The cart's Color Description is the colour name; it is green only where the same fabric+colour's live title
    colour says the same (the vote runs with a cart row too)."""
    code = "ZXRO000001 ZF 09"
    cart = dict(label="cart.xlsx", rows={resolve.norm_code(code): dict(colour=desc, row=7)})
    R = resolve.Resolver(dict(BRAND, join_key="{style} {fabric} {colour}"),
                         catm.load(write_catalog(tmp_path, [_live("8", "ZXRO000002 ZF 09")])), past_po(tmp_path),
                         base._tags(), cart=cart)
    name, src, why = R.colour_name(code)
    assert (name, src) == want
    assert why == ("same fabric+colour ZF 09 titled 'Black' on 1 of 1 product(s); cart.xlsx row 7: Color Description "
                   "agrees" if src == "catalog" else f"cart.xlsx row 7: Color Description '{desc}'")


# ---- a cart row's Why names the brand's bar too; a bar under 60% that refuses says so (review 2026-11-17-buyer-12) --
def _same_fabric_live():
    """7 BLACK and 3 NAVY products of the same fabric+colour ZF 09 as the NEW code ZXRO000001 ZF 09."""
    navy = [_live(str(40 + i), f"ZXRO00001{i} ZF 09", title=f"Field Jacket {40 + i} in Navy", colour="NAVY")
            for i in range(3)]
    return [_live(str(20 + i), f"ZXRO00000{2 + i} ZF 09") for i in range(7)] + navy


def _with_cart(tmp_path, code, live, join_key, **kw):
    cart = dict(label="cart.xlsx", rows={resolve.norm_code(code): dict(colour="Black", row=7)})
    return resolve.Resolver(_config(**kw, **({} if join_key is None else {"join_key": join_key})),
                            catm.load(write_catalog(tmp_path, live)), past_po(tmp_path), base._tags(), cart=cart)


@pytest.mark.parametrize("bar", [None, 0.8])
def test_a_cart_colour_name_says_when_the_brands_bar_refused_its_same_fabric(tmp_path, bar):
    code = "ZXRO000001 ZF 09"
    R = _with_cart(tmp_path, code, _same_fabric_live(), "{style} {fabric} {colour}",
                   **({} if bar is None else {"colour_vote_bar": bar}))
    vote = "same fabric+colour ZF 09 titled 'Black' on 7 of 10 product(s)"
    agrees = ("Black", "catalog", f"{vote}; cart.xlsx row 7: Color Description agrees")
    refused = ("Black", "cart", f"cart.xlsx row 7: Color Description 'Black'; {vote}, below the 80% this brand's "
                                "colour vote needs")
    assert R.colour_name(code) == (agrees if bar is None else refused)


@pytest.mark.parametrize("tier", ["same colour code", "same fabric+colour"])
@pytest.mark.parametrize("bar", [None, 0.8])
def test_a_cart_colour_family_says_when_the_brands_bar_refused_the_catalog(tmp_path, tier, bar):
    same = tier == "same fabric+colour"
    code = "ZXRO000001 ZF 09" if same else "ZXSA00001 001"
    kw = {} if bar is None else {"colour_vote_bar": bar}
    R = _with_cart(tmp_path, code, _same_fabric_live() if same else _shared(7, navy=3),
                   "{style} {fabric} {colour}" if same else None, **kw)
    vote = f"{tier} {'ZF 09' if same else '001'} is BLACK on 7 of 10 product(s)"
    if bar is None:
        want = ("BLACK", "catalog", vote) if same else (
            "BLACK", "catalog", f"{vote}; cart.xlsx row 7: colour 'Black' agrees")
    else:
        want = ("BLACK", "cart", "cart.xlsx row 7: colour 'Black' matches the BLACK keyword list; "
                                 f"{vote}, below the 80% this brand's colour vote needs")
    assert R.colour_family(code, "Black") == want


def test_a_bar_under_sixty_percent_that_refuses_a_vote_says_so(tmp_path):
    R = _resolver(tmp_path, _shared(2, navy=1, blank=1), colour_vote_bar=0.55)
    share = "2 of 4 product(s), 1 blank"
    assert R.colour_name("ZXSA00001 001") == (
        "001", "guess", _bar_why("same colour code", "001", "titled 'Black'", share, "55%")
        + "; the OC has no colour description")
    assert R.colour_family("ZXSA00001 001", "001") == (
        "", "guess", "no colour family for '001'; " + _bar_why("same colour code", "001", "is BLACK", share, "55%"))
