"""Brand config lookup, first hit wins:

1. --brand-config <path to a .yaml>
2. $OC_BRAND_DIR/<brand>.local.yaml, then $OC_BRAND_DIR/<brand>.yaml
3. oc/brands/<brand>.local.yaml (gitignored), then oc/brands/<brand>.yaml

The .local file holds commercial data (cost multiplier, history PO paths) and
must never be committed while the repo is public. example.yaml shows the shape.
Marais keeps the real ones in ~/Code/marais/_work/oc-brands/ (set OC_BRAND_DIR).

A config can also come without a file (the web page): `from_text` takes the
YAML itself, from an uploaded .yaml or a Streamlit secret, and `from_mapping`
takes a TOML table. All three paths apply the same DEFAULTS and REQUIRED keys.
"""
import copy
import os
from collections.abc import Mapping
from pathlib import Path

import yaml

BRANDS = Path(__file__).resolve().parent / "brands"

DEFAULTS = dict(
    status="Active", published_new=False, published_merge=True, new_sale="NEW",
    option1_name="Colour", option2_name="Size", price_round=50, sku_digits=3,
    title_tag="{vendor} - {title} |MARAIS",
    body_html="<p>Composition: {composition}</p> <p>Made in {made_in}</p> <p>SKU: {code}</p>",
    unknown="-", womens_prefix="Womens ", history=[], product_class_types={},
    colour_families={}, default_gender="MEN", country_names={},
)

REQUIRED = ["vendor", "adapter", "season", "sku_prefix", "cost_multiplier",
            "cost_currency", "margin_floor"]


class BrandError(Exception):
    pass


def find(name, config_path=None):
    if config_path:
        p = Path(os.path.expanduser(config_path))
        if not p.exists():
            raise BrandError(f"--brand-config {p} does not exist")
        return p
    dirs = _dirs()
    for d in dirs:
        for cand in (d / f"{name}.local.yaml", d / f"{name}.yaml"):
            if cand.exists():
                return cand
    raise BrandError(f"no brand config for '{name}' in {', '.join(map(str, dirs))} "
                     f"(copy oc/brands/example.yaml to {name}.local.yaml there, or pass --brand-config)")


def _dirs():
    dirs = [Path(os.path.expanduser(os.environ["OC_BRAND_DIR"]))] if os.environ.get("OC_BRAND_DIR") else []
    dirs.append(BRANDS)
    return dirs


def local_configs():
    """{brand name: path} for every config `find` would reach, first hit wins. example.yaml is skipped."""
    out = {}
    for d in _dirs():
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.yaml")):
            name = p.name[:-len(".local.yaml")] if p.name.endswith(".local.yaml") else p.stem
            if name != "example" and name not in out:
                out[name] = find(name)
    return out


def load(name_or_path, config_path=None):
    p = Path(name_or_path)
    if config_path or not p.suffix:
        p = find(name_or_path, config_path)
    return _finish(yaml.safe_load(p.read_text()), str(p), p.name.split(".")[0])


def from_text(text, source, name=None):
    """A config from YAML text (an uploaded .yaml or a secret). `source` is shown in place of a path."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise BrandError(f"{source}: not valid YAML ({e})") from None
    return _finish(data, source, name)


def from_mapping(data, source, name=None):
    """A config from a mapping, e.g. a TOML table in Streamlit secrets."""
    return _finish(_plain(data), source, name)


def _plain(v):
    if isinstance(v, Mapping):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def _finish(data, source, name):
    if data is None:
        data = {}
    if not isinstance(data, Mapping):
        raise BrandError(f"{source}: a brand config is a set of 'key: value' lines, got {type(data).__name__}")
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(data)
    missing = [k for k in REQUIRED if cfg.get(k) in (None, "")]
    if missing:
        raise BrandError(f"{source}: missing {missing}")
    for h in cfg["history"]:
        if not isinstance(h, Mapping) or not h.get("path") or not h.get("label"):
            raise BrandError(f"{source}: each history entry needs a label and a path, got {h!r}")
        h["path"] = os.path.expanduser(h["path"])
    cfg["_path"] = str(source)
    cfg["_name"] = name or "brand"
    return cfg
