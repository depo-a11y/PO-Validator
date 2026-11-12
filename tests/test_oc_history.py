"""Past season POs: a code column whose header is cut short, and a PO that carries other labels' rows (backtest
2026-11-12: Y-3 FW25 PO, the Adidas POs). Synthetic data only."""
import openpyxl

import test_oc_service as svc
from oc import history, service


def _po(path, header, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return str(path)


def test_the_code_column_is_found_when_its_header_is_cut_short():
    assert history.code_column(["Title", "Metafield: my_fields.manu"]) == "Metafield: my_fields.manu"
    assert history.code_column(["Metafield: my_fields.manufacture_code [single_line_text_field]"])
    assert history.code_column(["Metafield: my_fields.man", "Metafield: my_fields"]) is None   # too short to be sure


def test_a_past_po_is_read_for_its_own_vendor_only(tmp_path):
    p = _po(tmp_path / "po.xlsx", ["Title", "Vendor", "Type", "Option2 Value", "Order Qty", "Metafield: my_fields.manu"],
            [["Test Cap in Black", "ZX ONE", "ACCS HATS", "OS", 2, "ZX0001"],
             ["Test Bag in Black", "ZX TWO", "BAGS SHOPPER & TOTE BAGS", "OS", 1, "ZX0002"],
             ["Test Tee in Black", None, "CLOTHING T-SHIRTS", "M", 1, "ZX0003"]])   # no vendor: kept
    item = [{"label": "F/W 99", "path": p}]
    assert sorted(history.load(item, vendor="zx one").by_code) == ["ZX0001", "ZX0003"]
    assert sorted(history.load(item).by_code) == ["ZX0001", "ZX0002", "ZX0003"]


def test_the_page_reads_past_pos_for_the_configs_vendor(tmp_path, monkeypatch):
    p = _po(tmp_path / "SS26_PO.xlsx", ["Title", "Vendor", "Type", "Option2 Value", "Metafield: my_fields.manufacture_code"],
            [["Old Sneaker in Black", "MAISON MARGIELA", "MEN SHOES SNEAKERS", "41", "S11ZZ0001P9 1"],
             ["Other Label Tee in Black", "ZX OTHER", "MEN CLOTHING T-SHIRTS", "M", "ZX0009"]])
    seen = {}
    real = history.load
    monkeypatch.setattr(service.histm, "load", lambda paths, *a, **k: seen.setdefault("h", real(paths, *a, **k)))
    service.draft("oc.pdf", svc._catalog(tmp_path), svc._cfg(), history_paths=[p])
    assert sorted(seen["h"].by_code) == ["S11ZZ0001P9 1"]
