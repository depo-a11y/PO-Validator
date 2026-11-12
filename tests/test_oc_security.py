"""The page listens on this laptop only, and numbers in a config (which can be an upload) cannot blow up."""
import fnmatch
import tomllib
from pathlib import Path

import pytest

import test_oc_tool as base
from oc import brand as brandm

ROOT = Path(__file__).resolve().parent.parent


def test_streamlit_is_pinned_to_this_laptop():
    cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text())
    assert cfg["server"]["address"] == "127.0.0.1"
    assert cfg["server"]["headless"] is True and cfg["browser"]["gatherUsageStats"] is False


def test_only_secrets_toml_is_ignored_so_the_pinned_config_is_committed():
    patterns = [l.strip() for l in (ROOT / ".gitignore").read_text().splitlines() if l.strip() and not l.startswith("#")]
    def ignored(path):
        return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(Path(path).name, p) for p in patterns)
    assert ignored(".streamlit/secrets.toml") and not ignored(".streamlit/config.toml")


@pytest.mark.parametrize("key,value", [
    ("sku_digits", 10 ** 9), ("sku_digits", 13), ("sku_digits", 0), ("sku_digits", True), ("sku_digits", 2.5),
    ("sku_digits", "3"), ("cost_multiplier", 0), ("cost_multiplier", -1.5), ("cost_multiplier", 20),
    ("cost_multiplier", float("inf")), ("cost_multiplier", float("nan")), ("cost_multiplier", True),
    ("cost_multiplier", "1.5"), ("margin_floor", 0), ("margin_floor", 25.0), ("margin_floor", float("nan")),
])
def test_an_unsafe_number_in_a_config_is_refused_at_load(key, value):
    with pytest.raises(brandm.BrandError, match=key):
        brandm.from_mapping(dict(base.BRAND, **{key: value}), "uploaded file x.yaml")


@pytest.mark.parametrize("key,value", [("sku_digits", 1), ("sku_digits", 12), ("cost_multiplier", 1.5),
                                       ("cost_multiplier", 2), ("margin_floor", 19.9)])
def test_ordinary_numbers_load(key, value):
    assert brandm.from_mapping(dict(base.BRAND, **{key: value}), "t")[key] == value


def test_the_retired_price_round_key_is_dropped():
    cfg = brandm.from_mapping(dict(base.BRAND, price_round=10 ** 9), "t")
    assert "price_round" not in cfg and "price_round" not in brandm.DEFAULTS
