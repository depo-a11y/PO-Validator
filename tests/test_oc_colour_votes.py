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
import pytest

from oc import catalog as catm, resolve
from oc.adapters import otb
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
