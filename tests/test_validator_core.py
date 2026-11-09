"""Characterisation of the gate lifted out of app.py (validator_core)."""
import pandas as pd
import pytest

import validator_core as core


def _sheet(**over):
    row = {"Command": "NEW", "Title": "Tabi Ballerina in Black", "Vendor": "MAISON MARGIELA",
           "Type": "WOMEN SHOES BALLERINAS", "Body HTML": "<p>Composition: leather</p>", "Status": "Active",
           "Published": False, "Option1 Name": "Colour", "Option1 Value": "BLACK", "Option2 Name": "Size",
           "Option2 Value": "38", "Variant SKU": "MMSS27001", "Variant Price": 400, "Variant Compare At Price": 400,
           "Variant Cost": 150.0, "Metafield: my_fields.manufacture_code": "S99WZ0004P0004 T0004",
           "Metafield: custom.product_season [single_line_text_field]": "S/S 27",
           "Metafield: custom.new_sale [single_line_text_field]": "NEW",
           "Variant Metafield: Variant.cost_price [single_line_text_field]": "EUR 100",
           "Metafield: title_tag [string]": "MAISON MARGIELA - Tabi Ballerina in Black |MARAIS"}
    row.update(over)
    return pd.DataFrame([row])


def test_run_gate_clean_sheet():
    df, errors, _ = core.run_gate(_sheet(), typo_check=False)
    assert errors == []
    r = df.iloc[0]
    assert r["Variant Barcode"] == "MMSS27001"
    assert r["Metafield: custom.brand_color_id [single_line_text_field]"] == "T0004"
    assert r["Metafield: custom.size_scale [single_line_text_field]"] == "WOMEN SHOES EUROPE"
    assert r["Metafield: custom.local_market_price [single_line_text_field]"] == pytest.approx(540.0)
    assert r["Tags"].endswith("MAISON MARGIELA, S/S 27") and r["Tags"].startswith("NEW, WOMEN")
    assert r["Metafield: custom.sub_category [single_line_text_field]"] == "BALLERINAS"


def test_low_margin_is_an_error_not_a_stop():
    _, errors, _ = core.run_gate(_sheet(**{"Variant Price": 320}), typo_check=False)
    assert errors and errors[0]["Type"] == "LOW MARGIN" and errors[0]["Details"] == "2.13x"


@pytest.mark.parametrize("over,needle", [
    ({"Vendor": "Maison Margiela"}, "Vendor"),
    ({"Type": "WOMEN SHOES TABI"}, "INVALID PRODUCT TYPE"),
    ({"Metafield: my_fields.manufacture_code": "S99WZ0004P0004"}, "Brand Color ID"),
    ({"Variant Metafield: Variant.cost_price [single_line_text_field]": "100 EUR"}, "Cost currency"),
    ({"Title": ""}, "empty mandatory"),
])
def test_hard_stops(over, needle):
    with pytest.raises(core.ValidationStop) as e:
        core.run_gate(_sheet(**over), typo_check=False)
    assert needle.lower() in str(e.value).lower()


def test_duplicate_skus_stop():
    df = pd.concat([_sheet(), _sheet(**{"Option2 Value": "39"})], ignore_index=True)
    with pytest.raises(core.ValidationStop):
        core.run_gate(df, typo_check=False)


def test_invalid_merge_commands():
    assert core.invalid_merge_commands(pd.DataFrame({"Command": ["MERGE", "merge"]})) == []
    assert core.invalid_merge_commands(pd.DataFrame({"Command": ["MERGE", "NEW"]})) == ["NEW"]


def test_app_still_imports(monkeypatch):
    import importlib
    import sys
    sys.modules.pop("app", None)
    app = importlib.import_module("app")
    assert app.columns_in_order == core.COLUMNS_IN_ORDER
    assert app.assign_size_scale is core.assign_size_scale
