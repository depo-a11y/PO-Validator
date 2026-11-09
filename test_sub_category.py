import importlib.util
from pathlib import Path
from unittest import mock

import pytest

# Runs streamlit in bare mode: file_uploader returns None, so the upload branch is skipped.
import app

HERE = Path(__file__).resolve().parent


class _Stop(Exception):
    pass


def load_builder():
    # The builder prompts for a file name at import; stop it there, after its functions are defined.
    spec = importlib.util.spec_from_file_location("new_stock_create_po", HERE / "builders" / "new_stock_create_po.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch("builtins.input", side_effect=_Stop):
        try:
            spec.loader.exec_module(module)
        except _Stop:
            pass
    return module


SPLITS = {"app": app.split_t, "builder": load_builder().split_t}


@pytest.mark.parametrize("copy", SPLITS)
@pytest.mark.parametrize("ptype, expected", [
    ("MEN SHOES DERBY & OXFORDS", ["MEN", "SHOES", "DERBY & OXFORDS"]),
    ("WOMEN BAGS SHOPPER & TOTE BAGS", ["WOMEN", "BAGS", "SHOPPER & TOTE BAGS"]),
    ("MEN CLOTHING JEANS STRAIGHT JEANS", ["MEN", "CLOTHING", "JEANS STRAIGHT JEANS"]),
    ("MEN BAGS SHOULDER BAGS", ["MEN", "BAGS", "SHOULDER BAGS"]),
    ("MEN SHOES BOOTS", ["MEN", "SHOES", "BOOTS"]),
    ("MEN SHOES", ["", "", ""]),
])
def test_sub_category_is_the_whole_remainder(copy, ptype, expected):
    assert SPLITS[copy](ptype).tolist() == expected
