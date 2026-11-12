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

Only a config read from a file (`load`) may name past POs by path. In a config
from text the history paths are never opened: anyone who can upload a config
could otherwise make the hosted app read any file on its server. `history` comes
back empty and `history_files` keeps each entry's label and bare file name, so
past POs uploaded on the page can still be matched to their season labels.

Which OCs a config accepts: `oc_supplier` names the issuer as printed and `oc_label` the
label, for an issuer with several (adidas AG: Y-3, Willy Chavarria, RS). Every reader
returns both per document and an OC that does not match the config is refused. fog needs
`oc_supplier` (one reader for FOG Essentials and Fear of God LLC); adidas and otb need
`oc_label` (otb's is the collection's first word: MM or MM6); for admilano and ai the
check runs when the key is set.

`adapter` picks the OC reader: a built-in reader for one known layout (`otb` Maison
Margiela/MM6, `admilano` AD Milano, `adidas` adidas AG, `fog` Fear of God) or `ai` (any
layout, read by Claude; oc/adapters/ai.py). An `ai` brand must name `oc_supplier`
(the company printed as the issuer on its OCs) and `join_key`.

`join_key` says how the store's manufacture_code is built from an OC line: a template
over {style}, {fabric}, {colour} and {colour_code}. The default, for otb, is
"{style}{fabric} {colour}" (e.g. S99ZZ0001P0001 101). {colour_code} looks the
OC's colour up in `colour_codes` (OC colour: store code, case-insensitive), for a brand
whose store code carries a short colour code where its OC prints the colour name.
The template is filled by plain substitution of exactly those four tokens, never by
str.format: a config can be an upload, and a format spec such as {style:>999999999}
would make the server build a huge string. Any other brace is refused at load.
title_tag and body_html are filled the same way, each from its own tokens (TEXT_TEMPLATES).
"""
import copy
import os
import re
from collections.abc import Mapping
from pathlib import Path

import yaml

from .util import norm_code, norm_size

BRANDS = Path(__file__).resolve().parent / "brands"

DEFAULTS = dict(
    status="Active", published_new=False, published_merge=True, new_sale="NEW",
    option1_name="Colour", option2_name="Size", sku_digits=3,
    title_tag="{vendor} - {title} |MARAIS",
    body_html="<p>Composition: {composition}</p> <p>Made in {made_in}</p> <p>SKU: {code}</p>",
    unknown="-", womens_prefix="Womens ", history=[], product_class_types={},
    colour_families={}, default_gender="MEN", country_names={},
)

REQUIRED = ["vendor", "adapter", "season", "sku_prefix", "cost_multiplier",
            "cost_currency", "margin_floor"]

FIXED = ("otb", "admilano", "adidas", "fog")   # built-in readers, one per known layout (oc/adapters/<name>.py)
ADAPTERS = FIXED + ("ai",)
JOIN_FIELDS = ("style", "fabric", "colour", "colour_code")
JOIN_TOKEN = re.compile(r"\{(style|fabric|colour|colour_code)\}")
DEFAULT_JOIN_KEY = "{style}{fabric} {colour}"
AI_NEEDS = {
    "oc_supplier": "oc_supplier: the company name printed as the issuer on this brand's order confirmations "
                   "(a list if it varies), so a PDF from another supplier is refused",
    "join_key": "join_key: how the store's manufacture_code is built from an OC line, from {style}, {fabric}, "
                "{colour} and {colour_code}, e.g. \"{style} {colour_code}\" (with colour_codes mapping each OC "
                "colour to its store code, e.g. GREY: GR)",
}


# Fixed readers that serve several brands: the config must say which one it accepts.
FIXED_NEEDS = {
    "otb": {"oc_label": "oc_label: the first word of this brand's OC collections (MM for Maison Margiela, MM6 for "
                        "MM6); one OTB reader serves both, so the config says which it accepts"},
    "fog": {"oc_supplier": "oc_supplier: the issuer printed on this brand's OCs (FOG Essentials or Fear of God LLC); "
                           "one reader serves both, so the config says which it accepts"},
    "adidas": {"oc_label": "oc_label: the label printed at the start of this brand's line descriptions (Y-3, "
                           "Willy Chavarria or RS); one adidas AG reader serves all three"},
}


class BrandError(Exception):
    pass


class ColourCodeError(Exception):
    def __init__(self, colours):
        self.colours = colours
        super().__init__(
            f"{len(colours)} OC colour(s) have no code in the brand config's colour_codes: {', '.join(colours)}. "
            "Add each to colour_codes as 'OC colour: store code' (e.g. GREY: GR) and build again.")


def norm_colour(colour):
    return " ".join(str(colour or "").split()).upper()


def join_fields(template):
    return JOIN_TOKEN.findall(template)


TEXT_TEMPLATES = {"title_tag": ("vendor", "title"), "body_html": ("composition", "made_in", "code")}


def _tokens(fields):
    return re.compile(r"\{(" + "|".join(fields) + r")\}")


def fill_text(brand, key, **values):
    """The brand's title_tag or body_html with exactly its own tokens replaced; any other text is copied as is."""
    return _tokens(TEXT_TEMPLATES[key]).sub(lambda m: str(values[m.group(1)]), brand[key])


def code_for(brand, style, fabric, colour):
    """The store's manufacture_code for an OC line, by the brand's join_key."""
    tpl = (brand or {}).get("join_key") or DEFAULT_JOIN_KEY
    vals = dict(style=style, fabric=fabric or "", colour=colour or "")
    if "colour_code" in join_fields(tpl):
        cc = (brand.get("colour_codes") or {}).get(norm_colour(colour))
        if cc is None:
            raise ColourCodeError([str(colour)])
        vals["colour_code"] = cc
    return norm_code(JOIN_TOKEN.sub(lambda m: str(vals[m.group(1)]), tpl))


def unmapped_colours(brand, docs):
    """OC colours the join_key needs a colour code for and colour_codes lacks, in OC order."""
    tpl = (brand or {}).get("join_key") or DEFAULT_JOIN_KEY
    if "colour_code" not in join_fields(tpl):
        return []
    codes = brand.get("colour_codes") or {}
    out = []
    for d in docs:
        for l in d["lines"]:
            c = str(l.get("colour") or "")
            if norm_colour(c) not in codes and c not in out:
                out.append(c)
    return out


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
    return _finish(yaml.safe_load(p.read_text()), str(p), p.name.split(".")[0], paths=True)


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


def _reader_keys(cfg, data, source):
    """adapter, oc_supplier, join_key and colour_codes, checked at load so a bad config never reaches a PDF."""
    if cfg["adapter"] not in ADAPTERS:
        raise BrandError(f"{source}: adapter must be one of {', '.join(ADAPTERS)}, got {cfg['adapter']!r}")
    if cfg["adapter"] == "ai":
        need = [k for k in AI_NEEDS if not data.get(k)]
        if need:
            raise BrandError(f"{source}: adapter: ai needs {' and '.join(need)}. Add "
                             + "; ".join(AI_NEEDS[k] for k in need) + ".")
    need = [k for k in FIXED_NEEDS.get(cfg["adapter"], {}) if not data.get(k)]
    if need:
        raise BrandError(f"{source}: adapter: {cfg['adapter']} needs {' and '.join(need)}. Add "
                         + "; ".join(FIXED_NEEDS[cfg["adapter"]][k] for k in need) + ".")
    for key, what in (("oc_supplier", "a company name"), ("oc_label", "a label name")):
        val = cfg.get(key)
        if val:
            val = [val] if isinstance(val, str) else list(val)
            if not all(isinstance(x, str) and x.strip() for x in val):
                raise BrandError(f"{source}: {key} is {what} or a list of them, got {cfg[key]!r}")
            cfg[key] = val
    tpl = cfg.get("join_key") or DEFAULT_JOIN_KEY
    if not isinstance(tpl, str):
        raise BrandError(f"{source}: join_key is a text template, got {tpl!r}")
    fields, rest = join_fields(tpl), JOIN_TOKEN.sub("", tpl)
    if "{" in rest or "}" in rest or not fields:
        bad = re.findall(r"\{[^{}]*\}?", rest)
        raise BrandError(f"{source}: join_key {tpl!r} may use only " + ", ".join("{" + f + "}" for f in JOIN_FIELDS)
                         + ", each written exactly so (no format specs, conversions or other braces)"
                         + (f"; not allowed: {', '.join(bad)}" if bad else ""))
    cfg["join_key"] = tpl
    codes = cfg.get("colour_codes") or {}
    if not isinstance(codes, Mapping):
        raise BrandError(f"{source}: colour_codes is a list of 'OC colour: store code' lines, got {codes!r}")
    if "colour_code" in fields and not codes:
        raise BrandError(f"{source}: join_key uses {{colour_code}} but there is no colour_codes table "
                         "(OC colour: store code, e.g. GREY: GR)")
    cfg["colour_codes"] = {norm_colour(k): str(v).strip() for k, v in codes.items()}
    sizes = cfg.get("size_map") or {}
    if not isinstance(sizes, Mapping) or not all(isinstance(x, (str, int)) and not isinstance(x, bool) and str(x).strip()
                                                 for kv in sizes.items() for x in kv):
        raise BrandError(f"{source}: size_map is a list of 'printed size: store size' lines, each a word or number "
                         f"(e.g. 31x34: 31), got {sizes!r}")
    cfg["size_map"] = {norm_size(k): norm_size(v) for k, v in sizes.items()}


def _numbers(cfg, source):
    """Numbers from a config, which can be an upload: checked so none can blow up memory or time. sku_digits is a
    format width (a huge one builds a huge string); the money rates must be ordinary positive numbers."""
    digits = cfg.get("sku_digits")
    if isinstance(digits, bool) or not isinstance(digits, int) or not 1 <= digits <= 12:
        raise BrandError(f"{source}: sku_digits is how many digits follow the SKU prefix, a whole number from 1 to "
                         f"12, got {digits!r}")
    for key in ("cost_multiplier", "margin_floor"):
        v = cfg.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v < 20:
            raise BrandError(f"{source}: {key} is a number above 0 and below 20, got {v!r}")
    cfg.pop("price_round", None)   # retail is never fitted or rounded since 2026-11-12; an old config's key is unused


def _text_templates(cfg, source):
    """title_tag and body_html may hold only their own {tokens}; any other brace is refused, as for join_key."""
    for key, fields in TEXT_TEMPLATES.items():
        tpl = cfg[key]
        if not isinstance(tpl, str):
            raise BrandError(f"{source}: {key} is a text template, got {tpl!r}")
        rest = _tokens(fields).sub("", tpl)
        if "{" in rest or "}" in rest:
            bad = re.findall(r"\{[^{}]*\}?", rest)
            raise BrandError(f"{source}: {key} {tpl!r} may use only " + ", ".join("{" + f + "}" for f in fields)
                             + ", each written exactly so (no format specs, conversions or other braces)"
                             + (f"; not allowed: {', '.join(bad)}" if bad else ""))


def _plain(v):
    if isinstance(v, Mapping):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def _finish(data, source, name, paths=False):
    if data is None:
        data = {}
    if not isinstance(data, Mapping):
        raise BrandError(f"{source}: a brand config is a set of 'key: value' lines, got {type(data).__name__}")
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(data)
    missing = [k for k in REQUIRED if cfg.get(k) in (None, "")]
    if missing:
        raise BrandError(f"{source}: missing {missing}")
    _reader_keys(cfg, data, source)
    _text_templates(cfg, source)
    _numbers(cfg, source)
    for h in cfg["history"]:
        if not isinstance(h, Mapping) or not h.get("path") or not h.get("label"):
            raise BrandError(f"{source}: each history entry needs a label and a path, got {h!r}")
        h["path"] = os.path.expanduser(h["path"])
    cfg["history_files"] = [dict(label=h["label"], file=os.path.basename(h["path"])) for h in cfg["history"]]
    if not paths:
        cfg["history"] = []
    cfg["_path"] = str(source)
    cfg["_name"] = name or "brand"
    return cfg
