"""Brand config: oc/brands/<brand>.local.yaml (gitignored), else <brand>.yaml.

The .local file holds commercial data (cost multiplier, history PO paths) and
must never be committed while the repo is public. example.yaml shows the shape.
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


def load(name_or_path):
    p = Path(name_or_path)
    if not p.suffix:
        for cand in (BRANDS / f"{name_or_path}.local.yaml", BRANDS / f"{name_or_path}.yaml"):
            if cand.exists():
                p = cand
                break
        else:
            raise BrandError(f"no brand config for '{name_or_path}' in {BRANDS} "
                             f"(copy example.yaml to {name_or_path}.local.yaml)")
    cfg = dict(DEFAULTS)
    cfg.update(yaml.safe_load(p.read_text()) or {})
    missing = [k for k in REQUIRED if cfg.get(k) in (None, "")]
    if missing:
        raise BrandError(f"{p}: missing {missing}")
    for h in cfg["history"]:
        h["path"] = os.path.expanduser(h["path"])
    cfg["_path"] = str(p)
    return cfg
