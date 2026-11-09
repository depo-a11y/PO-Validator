"""OTB order-confirmation gates. Synthetic data always; the real PDF only when
OC_PDF (and OC_GOLDEN for the by_size comparison) point at local files, because
order data is never committed to this public repo."""
import copy
import os

import openpyxl
import pytest

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
