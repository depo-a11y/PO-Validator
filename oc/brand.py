"""Brand config lookup, first hit wins:

1. --brand-config <path to a .yaml>
2. $OC_BRAND_DIR/<brand>.local.yaml, then $OC_BRAND_DIR/<brand>.yaml
3. oc/brands/<brand>.local.yaml (gitignored), then oc/brands/<brand>.yaml

The .local file holds commercial data (cost multiplier, history PO paths) and
must never be committed while the repo is public. example.yaml shows the shape.
Marais keeps the real ones in ~/Code/marais/_work/oc-brands/ (set OC_BRAND_DIR).
"""
import os
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
    dirs = [Path(os.path.expanduser(os.environ["OC_BRAND_DIR"]))] if os.environ.get("OC_BRAND_DIR") else []
    dirs.append(BRANDS)
    for d in dirs:
        for cand in (d / f"{name}.local.yaml", d / f"{name}.yaml"):
            if cand.exists():
                return cand
    raise BrandError(f"no brand config for '{name}' in {', '.join(map(str, dirs))} "
                     f"(copy oc/brands/example.yaml to {name}.local.yaml there, or pass --brand-config)")


def load(name_or_path, config_path=None):
    p = Path(name_or_path)
    if config_path or not p.suffix:
        p = find(name_or_path, config_path)
    cfg = dict(DEFAULTS)
    cfg.update(yaml.safe_load(p.read_text()) or {})
    missing = [k for k in REQUIRED if cfg.get(k) in (None, "")]
    if missing:
        raise BrandError(f"{p}: missing {missing}")
    for h in cfg["history"]:
        h["path"] = os.path.expanduser(h["path"])
    cfg["_path"] = str(p)
    return cfg
