"""OTB order-confirmation gates. Synthetic data always; the real PDF only when
OC_PDF (and OC_GOLDEN for the by_size comparison) point at local files, because
order data is never committed to this public repo."""
import copy
import glob
import os

import openpyxl
import pytest

import oc_synth
import test_oc_tool as base
from oc import service
from oc.adapters import otb


def _docs():
    return [dict(doc="SS - 1", collection="MM MALE SHOES AVP", total_qty=7, net=1850.0, lines=[
        dict(line_no=1, style="S99WS0001", fabric="P0001", colour="101", product_class="SNEAKERS",
             sizes={"41": 1, "42": 2}, qty=3, price=200.0, line_value=600.0),
        dict(line_no=2, style="S99WR0002", fabric="P0002", colour="H0002", product_class="MOCASSIN",
             sizes={"40": 2, "43": 2}, qty=4, price=312.5, line_value=1250.0),
    ])]


def test_gates_pass_on_consistent_data():
    assert otb.gate_problems(_docs()) == []


def test_perturbed_size_quantity_fails():
    d = _docs()
    d[0]["lines"][0]["sizes"]["42"] += 1
    probs = otb.gate_problems(d)
    assert any("sizes sum 4 != Tot. Qty 3" in p for p in probs)
    assert any("parsed 8 units, printed 7" in p for p in probs)


def test_perturbed_price_fails():
    d = _docs()
    d[0]["lines"][1]["price"] = 313.0
    probs = otb.gate_problems(d)
    assert any("!= line value 1250.0" in p for p in probs)


def test_each_gate_fires_alone():
    # gate 1 only: Tot. Qty and value moved together, the size cells did not
    d = _docs()
    l = d[0]["lines"][0]
    l["qty"], l["line_value"] = 4, 800.0
    assert [p for p in otb.gate_problems(d)] == ["SS - 1 line 1 S99WS0001: sizes sum 3 != Tot. Qty 4"]
    # gate 2 only: printed line value wrong, everything else consistent
    d = _docs()
    d[0]["lines"][1]["line_value"] = 1251.0
    assert len(otb.gate_problems(d)) == 1 and "line value 1251.0" in otb.gate_problems(d)[0]
    # gate 3 only: printed document totals wrong
    d = _docs()
    d[0]["total_qty"] = 8
    assert otb.gate_problems(d) == ["SS - 1: parsed 7 units, printed 8"]
    d = _docs()
    d[0]["net"] = 1851.0
    assert len(otb.gate_problems(d)) == 1 and "printed net 1,851.00" in otb.gate_problems(d)[0]


def test_missing_printed_totals_fails():
    d = _docs()
    del d[0]["net"]
    assert otb.gate_problems(d) == ["SS - 1: no printed totals"]


def test_collection_gender():
    assert otb.collection_gender("MM FEMALE SHOES AVP") == "WOMEN"
    assert otb.collection_gender("MM MALE RTW AVP") == "MEN"
    assert otb.collection_gender("MM JEWELLERY AVP") is None


PDF = os.environ.get("OC_PDF")
GOLDEN = os.environ.get("OC_GOLDEN")


@pytest.mark.skipif(not PDF, reason="set OC_PDF to a local order-confirmation PDF")
def test_real_pdf_reproduces_printed_totals():
    docs = otb.parse_checked(PDF)
    t = otb.totals(docs)
    assert t["units"] == t["printed_units"] and t["value"] == t["printed_value"]
    mutated = copy.deepcopy(docs)
    first = mutated[0]["lines"][0]
    first["sizes"][next(iter(first["sizes"]))] += 1
    assert otb.gate_problems(mutated)
    mutated = copy.deepcopy(docs)
    mutated[0]["lines"][0]["price"] += 1
    assert otb.gate_problems(mutated)
    assert otb.gate_problems(docs) == []


@pytest.mark.skipif(not (PDF and GOLDEN), reason="set OC_PDF and OC_GOLDEN (an extract with a by_size sheet)")
def test_real_pdf_by_size_equals_golden():
    rows = otb.by_size_rows(otb.parse_checked(PDF))
    ws = openpyxl.load_workbook(GOLDEN, read_only=True)["by_size"]
    it = ws.iter_rows(values_only=True)
    hdr = next(it)
    gold = [dict(zip(hdr, r)) for r in it]
    assert len(rows) == len(gold)
    for a, b in zip(rows, gold):
        assert {k: (float(v) if isinstance(v, (int, float)) else v) for k, v in a.items()} == \
               {k: (float(v) if isinstance(v, (int, float)) else v) for k, v in b.items()}


# ---- number format, decided per confirmation (F/W 26 OTB OCs print 1,234.56 and 03/23/2026) ----------
def test_otb_reads_either_number_format_to_the_same_confirmation(tmp_path):
    eu = otb.parse(oc_synth.otb_pdf(str(tmp_path / "eu.pdf"), "eu"))
    us = otb.parse(oc_synth.otb_pdf(str(tmp_path / "us.pdf"), "us"))
    assert eu == us and eu[1] == []
    d = us[0][0]
    assert (d["date"], d["printed"], d["total_qty"], d["gross"], d["net"]) == ("15/01/2099", "01/02/2099", 8, 6250.0,
                                                                                6250.0)
    assert [(l["qty"], l["price"], l["line_value"], l["deliver_from"], l["deliver_to"]) for l in d["lines"]] == [
        (4, 1250.0, 5000.0, "15/07/2099", "15/10/2099"), (4, 312.5, 1250.0, "15/07/2099", "15/10/2099")]


@pytest.mark.parametrize("fmt,odd", [("eu", "us"), ("us", "eu")])
def test_otb_refuses_a_confirmation_printed_in_two_number_formats(tmp_path, fmt, odd):
    path = oc_synth.otb_pdf(str(tmp_path / "odd.pdf"), fmt, odd=odd)
    docs, problems = otb.parse(path)
    assert len(problems) == 1 and problems[0].startswith("SS - 2099/01-62-0001: number format: its amounts are "
                                                         "printed as ")
    assert "1.234,56 (" in problems[0] and "1,234.56 (" in problems[0] and problems[0].endswith(
        "so the tool will not guess how to read it. Ask Colwyn")
    assert docs[0]["lines"] == [] and docs[0]["net"] is None
    _, checks, _ = service.read_oc(path, dict(base.BRAND))   # not a layout change: no hint to read it with AI
    assert [c["Detail"] for c in service.fails(checks)] == problems


@pytest.mark.parametrize("fmt,deliver_from,shown", [("us", "07/15/2099", "15/07/2099"), ("eu", "15/13/2099", "15/13/2099")])
def test_otb_refuses_a_date_that_is_not_in_its_amounts_format(tmp_path, fmt, deliver_from, shown):
    dates = ("15/01/2099", "01/02/2099", deliver_from, "15/10/2099")
    _, problems = otb.parse(oc_synth.otb_pdf(str(tmp_path / "d.pdf"), fmt, dates=dates))
    name = otb.FORMAT_NAMES[fmt]
    assert problems == [f"SS - 2099/01-62-0001: number format: its amounts are printed as {name} but date '{shown}' "
                        "is not a date in that format, so the tool will not guess how to read it. Ask Colwyn"]


def test_number_format_is_one_format_or_none():
    nf = otb.number_format
    assert nf([]) == "eu" and nf(["1.446,00", "828,00"]) == "eu" and nf(["828.00", "17,780.00"]) == "us"
    assert nf(["1.446,00", "828.00"]) is None and nf(["1,446,00"]) is None and nf(["1.446.00"]) is None
    assert otb.count("1.334", "eu") == 1334 and otb.count("1,334", "us") == 1334
    assert otb.count("1,334", "eu") is None and otb.count("1.334", "us") is None


def test_otb_refuses_a_quantity_grouped_in_the_other_format():
    raw = [dict(doc="SS - 1", total_qty="1,007", gross="1.250,00", net="1.250,00",
                lines=[dict(qty="5", price="250,00", line_value="1.250,00", deliver_from=None, deliver_to=None)])]
    assert otb.read_numbers(raw) == ["SS - 1: number format: its amounts are printed as 1.234,56 but quantity '1,007' "
                                     "is not, so the tool will not guess how to read it. Ask Colwyn"]
    assert raw[0]["lines"] == [] and raw[0]["total_qty"] is None


OTB_DIR = os.environ.get("OC_OTB_DIR")


@pytest.mark.skipif(not OTB_DIR, reason="set OC_OTB_DIR to a local folder of real OTB order-confirmation PDFs")
def test_real_otb_pdfs_of_every_season_reproduce_printed_totals():
    files = sorted(glob.glob(os.path.join(OTB_DIR, "*.pdf")))
    assert files, f"no PDFs in {OTB_DIR}"
    for f in files:
        t = otb.totals(otb.parse_checked(f))
        assert t["units"] == t["printed_units"] and t["value"] == t["printed_value"], f
