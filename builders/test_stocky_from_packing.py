"""Tests for stocky_from_packing. Run with pytest, or directly:
    python3 test_stocky_from_packing.py
"""
from pathlib import Path

import stocky_from_packing as S

HERE = Path(__file__).resolve().parent
DOWNLOADS = Path.home() / "Downloads"

MENS_TXT = """Page 1 / 1
Packing List 26001400758
IPO Invoice Ref. 1128-3260001545
Consignee (Ship-to 0000754800)
ALEXANDER MCQUEEN MARAIS - MENS (ROYAL ARCADE)
Total number of parcels : 1 Total number of Units Added : 3
Parcel 210001390001638923
Customs tariff code SKU ID EAN PO ID Quantity
Model / Part / Color / Size Description
64039996 8139653611 3667316012638 2 Pair
LEATHER UPPER AND RUBBER SOLE
807881 WHAEG 9079 43
SNEAKER
64039996 8139653638 3667316012652 1 Pair
LEATHER UPPER AND RUBBER SOLE
807881 WHAEG 9079 44
SNEAKER
"""

WOMENS_TXT = """Packing List 26001400770
IPO Invoice Ref. 1128-3260001547
Consignee (Ship-to 0000754801)
ALEXANDER MCQUEEN MARAIS - WOMENS (BOURKE ST)
Total number of Units Added : 1
64039998 8139652844 3667316011860 1 Pair
LEATHER UPPER AND RUBBER SOLE
553770 WIAIH 9182 37
SNEAKER
"""


def test_parse_basic():
    pl = S.parse_packing_text(MENS_TXT)
    assert pl.prep == "26001400758"
    assert pl.ipo_ref == "1128-3260001545"
    assert "MARAIS - MENS (ROYAL ARCADE)" in pl.ship_to
    assert pl.stated_units == 3
    assert len(pl.lines) == 2
    l0 = pl.lines[0]
    assert (l0.style, l0.part, l0.color, l0.size) == ("807881", "WHAEG", "9079", "43")
    assert l0.ean == "3667316012638" and l0.qty == 2
    assert l0.mfc == "807881WHAEG9079"
    assert pl.units == 3


def test_parse_rejects_unbalanced():
    bad = MENS_TXT + "64039996 8139653654 3667316012676 1 Pair\n"  # item with no code line
    try:
        S.parse_packing_text(bad)
    except ValueError:
        return
    raise AssertionError("expected ValueError on item/code mismatch")


def test_resolve_location():
    assert S.resolve_location("ALEXANDER MCQUEEN MARAIS - MENS (ROYAL ARCADE)") == "Marais - Men"
    assert S.resolve_location("MARAIS - WOMENS (BOURKE ST)") == "Marais - Women"
    # "WOMENS" must not be misread as men
    assert S.resolve_location("WOMENS") == "Marais - Women"
    assert S.resolve_location("MARAIS - KIDS (SOMEWHERE)") is None


def test_tail_absorbs_leading_typo():
    assert S.tail("53770WIAIH9061 ") == S.tail("553770WIAIH9061") == "WIAIH9061"


def test_mapping_full_and_tail_fallback():
    m = S.Mapping()
    m.add("53770WIAIH9061", "35", "AMQWF20137", 263.2)   # note the leading-5 typo
    # exact (normalized) hit
    assert m.lookup("53770WIAIH9061", "35") == ("AMQWF20137", 263.2)
    # corrected code still resolves via tail fallback
    assert m.lookup("553770WIAIH9061", "35") == ("AMQWF20137", 263.2)
    assert m.lookup("807881WHAEG9079", "43") is None


def test_mapping_ambiguous_tail_raises():
    m = S.Mapping()
    m.add("111111WIAIH9061", "35", "SKU_A", 1.0)
    m.add("222222WIAIH9061", "35", "SKU_B", 2.0)
    try:
        m.lookup("999999WIAIH9061", "35")  # not a full hit; tail matches two SKUs
    except ValueError:
        return
    raise AssertionError("expected ambiguity ValueError")


def _mens_mapping():
    m = S.Mapping()
    m.add("807881WHAEG9079", "43", "AMQFW26005", 506.12)
    m.add("807881WHAEG9079", "44", "AMQFW26006", 506.12)
    m.add("553770WIAIH9182", "37", "AMQWF20145", 429.02)
    return m


def test_build_placements_single_location():
    pl = S.parse_packing_text(MENS_TXT)
    placements, errors = S.build_placements([pl], _mens_mapping())
    assert errors == []
    assert {p.sku for p in placements} == {"AMQFW26005", "AMQFW26006"}
    assert all(p.location == "Marais - Men" for p in placements)
    rep = S.location_check(placements)
    assert rep.multi is False
    assert rep.locations == ["Marais - Men"]
    assert rep.by_location["Marais - Men"]["units"] == 3


def test_location_check_flags_multi():
    pls = [S.parse_packing_text(MENS_TXT), S.parse_packing_text(WOMENS_TXT)]
    placements, errors = S.build_placements(pls, _mens_mapping())
    assert errors == []
    rep = S.location_check(placements)
    assert rep.multi is True
    assert rep.locations == ["Marais - Men", "Marais - Women"]
    assert rep.by_location["Marais - Women"]["units"] == 1
    assert rep.by_location["Marais - Men"]["units"] == 3


def test_stated_units_oracle_blocks():
    tampered = MENS_TXT.replace("Total number of Units Added : 3", "Total number of Units Added : 9")
    pl = S.parse_packing_text(tampered)
    _, errors = S.build_placements([pl], _mens_mapping())
    assert any("!= stated" in e for e in errors)


def test_location_check_unmapped():
    pl = S.parse_packing_text(WOMENS_TXT.replace("MARAIS - WOMENS (BOURKE ST)", "MARAIS - KIDS (X)"))
    m = S.Mapping(); m.add("553770WIAIH9182", "37", "AMQWF20145", 429.02)
    placements, _ = S.build_placements([pl], m)
    rep = S.location_check(placements)
    assert rep.unmapped and "KIDS" in rep.unmapped[0]


def test_integration_real_mcqueen_pdfs():
    pdfs = sorted(DOWNLOADS.glob("ODPL-*.pdf"))
    delivery = HERE / "AMQ FW26 DELIVERY_updated.csv"
    if len(pdfs) < 2 or not delivery.exists():
        print("  [skip] real PDFs / delivery sheet not present")
        return
    mapping = S.load_mapping(str(delivery))
    pls = [S.parse_packing_pdf(str(p)) for p in pdfs]
    placements, errors = S.build_placements(pls, mapping)
    assert errors == [], errors
    assert sum(p.qty for p in placements) == 6
    rep = S.location_check(placements)
    assert rep.multi is True
    assert set(rep.locations) == {"Marais - Men", "Marais - Women"}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    raise SystemExit(0 if passed == len(fns) else 1)
