"""Brand config lookup, first hit wins:

1. --brand-config <path to a .yaml>
2. $OC_BRAND_DIR/<brand>.local.yaml, then $OC_BRAND_DIR/<brand>.yaml
3. oc/brands/<brand>.local.yaml (gitignored), then oc/brands/<brand>.yaml

The .local file holds commercial data (cost multiplier, history PO paths) and
must never be committed while the repo is public. example.yaml shows the shape.
Marais keeps the real ones in ~/Code/marais/_work/oc-brands/ (set OC_BRAND_DIR).

A config can also come without a file (the web page): `from_text` takes the
YAML itself, from an uploaded .yaml or a Streamlit secret, and `from_mapping`
takes a TOML table. All three paths apply the same DEFAULTS and REQUIRED keys,
and refuse a top-level key outside KEYS, naming it (a typo would be ignored).

Only a config read from a file (`load`) may name past POs by path. In a config
from text the history paths are never opened: anyone who can upload a config
could otherwise make the hosted app read any file on its server. `history` comes
back empty and `history_files` keeps each entry's label and bare file name, so
past POs uploaded on the page can still be matched to their season labels.

`source_kind` (optional text, e.g. "SAP demand export") says the file is not the brand's own order confirmation
and shows a WARN row naming it.

Which OCs a config accepts: `oc_supplier` names the issuer as printed and `oc_label` the
label, for an issuer with several (adidas AG: Y-3, Willy Chavarria, RS). Every reader
returns both per document and an OC that does not match the config is refused. fog needs
`oc_supplier` (one reader for FOG Essentials and Fear of God LLC); adidas, otb and thugclub
need `oc_label` (otb's is the collection's first word: MM or MM6; thugclub's is THUG CLUB or
a collaboration such as TC x AI); for admilano and ai the check runs when the key is set.
joor needs `oc_supplier`, the email domain of the brand
contact JOOR prints (the brand itself is a logo; joor reads no label). p3m (the "#3xxxxxx" ordering platform)
needs `oc_supplier`: its documents print the selling company and no separate label. `oc_style_prefix` (any
reader, checked when set): every OC line's style must start with one of them, so a brand sold
through an agency, whose issuer it shares with the agency's other brands, accepts only its own.

`adapter` picks the OC reader: a built-in reader for one known layout (`otb` Maison
Margiela/MM6, `admilano` AD Milano, `adidas` adidas AG, `fog` Fear of God, `thugclub` THUG CLUB
Corp. proforma invoices, `joor` the JOOR order print of any brand, `p3m` the "#3xxxxxx"
ordering platform: Willy Chavarria, Gosha Rubchinskiy, Maison MIHARA YASUHIRO, Amiri, `sportlux` the Palm
Angels distributor's order confirmations), `sheet` (an OC sent
as a spreadsheet, read by the config's `sheet:` column mapping; oc/adapters/sheet.py) or `ai` (any
layout, read by Claude; oc/adapters/ai.py). An `ai` brand must name `oc_supplier`
(the company printed as the issuer on its OCs) and `join_key`; a `sheet` brand `join_key` and `sheet:`.

`hold` (a sentence, off unless set) parks a config: every path that uses it (read, draft, complete, finalize)
refuses with that reason before reading or writing anything, e.g. until the store's codes and the OC's can be
joined. hold_row is the one check they share, so a held config fails closed everywhere.

`join_key` says how the store's manufacture_code is built from an OC line: a template
over {style}, {fabric}, {colour} and {colour_code}. The default, for otb, is
"{style}{fabric} {colour}" (e.g. S99ZZ0001P0001 101). {colour_code} looks the
OC's colour up in `colour_codes` (OC colour: store code, case-insensitive), for a brand
whose store code carries a short colour code where its OC prints the colour name.
The template is filled by plain substitution of exactly those four tokens, never by
str.format: a config can be an upload, and a format spec such as {style:>999999999}
would make the server build a huge string. Any other brace is refused at load.
title_tag and body_html are filled the same way, each from its own tokens (TEXT_TEMPLATES).

`match_ignore` (off unless set) is for a brand whose store codes and OC codes differ only in separators
("ZZ-0001 A1" printed, "ZZ0001 A1" live): characters such as "-_ " left out of both sides when an OC code
is matched to a live product (catalog.match), upper-cased. A MERGE row carries the live code; a NEW row the
OC's. Two live codes that become equal stop the draft, naming both. Letters and digits are refused.

`join_key_alt` (off unless set) is a second template, tried only for a line whose join_key code finds no live
product; it may also use {style_alt} (a sheet's style_alt column).

A config holds no season (Colwyn, 2026-11-16: "Colleague picks per run"). The colleague picks the season of each run
on the page (or `--season`), and `with_season` sets it on a copy of the config. A `season:` an old config still
carries is read and dropped, never used: a guessed season would re-season every re-bought live product on import.

`sku_prefix` is per season half: `S/S:` and `F/W:`, each the letters a new SKU starts with before the two digits of
the season picked (S/S: ZXSS makes ZXSS27001 in an S/S 27 run), or a MEN/WOMEN/UNISEX map of them for a brand
whose men's and women's SKUs differ. A product whose gender has no prefix of its own takes the default_gender's. Each
prefix ends in its half's season letters (S for S/S; F or W for F/W). A half with no prefix refuses that half's runs.
`sku_prefix_from` says, per season half, where that half's prefixes came from, as a sentence that starts "derived from
live, check" (from the brand's live SKUs) or says "not live, check" (kept from a hand-made PO, with no live SKUs of
that half to derive from). Every new SKU of that half is then amber under that mark, with the sentence in its Why and
in the draft's checks. A half with no sentence is a prefix the config sets by hand ("rule").
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

REQUIRED = ["vendor", "adapter", "sku_prefix", "cost_multiplier",
            "cost_currency", "margin_floor"]

OPTIONAL = ["oc_supplier", "oc_label", "oc_style_prefix", "join_key", "join_key_alt", "match_ignore", "size_map",
            "colour_codes", "colour_spelling", "heel_codes", "subcategory_types", "title_prefixes", "sheet",
            "source_kind", "hold", "price_round"]   # price_round: retired 2026-11-12, still in old configs, dropped
OPTIONAL += ["sku_prefix_from", "season"]   # season: picked per run since 2026-11-16; an old config's is dropped

KEYS = set(DEFAULTS) | set(REQUIRED) | set(OPTIONAL)

# built-in readers, one per known layout (oc/adapters/<name>.py)
FIXED = ("otb", "admilano", "adidas", "fog", "thugclub", "joor", "p3m", "sportlux")
FIXED += ("sheet",)   # built-in too: a spreadsheet OC of any layout, read by the brand config's sheet: mapping
ADAPTERS = FIXED + ("ai",)
JOIN_FIELDS = ("style", "fabric", "colour", "colour_code")
JOIN_TOKEN = re.compile(r"\{(style|fabric|colour|colour_code)\}")
ALT_TOKEN = re.compile(r"\{(style|style_alt|fabric|colour|colour_code)\}")   # join_key_alt may also use {style_alt}
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
    "sheet": {"join_key": "join_key: how the store's manufacture_code is built from a sheet line, e.g. \"{style} "
                          "{colour}\" ({colour} is the sheet's colour_code column when it is mapped)"},
    "thugclub": {"oc_label": "oc_label: THUG CLUB for the house line, and a collaboration's label as printed at the "
                             "start of its product names (TC x AI for Thug Club x Alpha Industries) to accept that "
                             "too; one THUG CLUB Corp. reader serves both"},
    "joor": {"oc_supplier": "oc_supplier: the email domain of the Brand Information contact on this brand's JOOR "
                            "orders (e.g. brand.com; a list if it varies; an agency's for an agency order); one JOOR "
                            "reader serves every brand on JOOR, which prints the brand only as a logo"},
    "p3m": {"oc_supplier": "oc_supplier: the company printed at the top left of this brand's order confirmations, "
                           "beside 'Order number #3...' (e.g. SOSU CO.,LTD. for Maison MIHARA YASUHIRO); one reader "
                           "serves every brand on that ordering platform"},
    "sportlux": {"oc_supplier": "oc_supplier: the issuer printed at the top of this brand's OCs (Sportlux General "
                                "Trading FZCO); its OCs print no label, so the issuer is what holds the config to them"},
}


HOLD_CHECK = "Brand config is not on hold"


def hold_row(cfg):
    """The FAIL row naming the reason when the config is on hold (`hold`), else None."""
    hold = (cfg or {}).get("hold")
    return dict(Check=HOLD_CHECK, Result="FAIL", Detail=f"This brand's config is on hold: {hold}") if hold else None


class BrandError(Exception):
    pass


class ColourCodeError(Exception):
    def __init__(self, colours):
        self.colours = colours
        super().__init__(
            f"{len(colours)} OC colour(s) have no code in the brand config's colour_codes: {', '.join(colours)}. "
            "Add each to colour_codes as 'OC colour: store code' (e.g. GREY: GR) and build again.")


# ---- the season of a run, and the SKU prefix it gives ------------------------------------------------------------
SEASON = re.compile(r"(S/S|F/W) (\d{2})")   # the store's own spelling of custom.product_season: S/S 27, F/W 26
LOOSE_SEASON = re.compile(r"\b(S/S|F/W|A/W)\s*(\d{2})\b")
GENDERS = ("MEN", "WOMEN", "UNISEX")
HALF_LETTERS = {"S/S": ("S",), "F/W": ("F", "W")}   # the season letters a half's SKU prefix ends in
DERIVED = "derived from live, check"   # the source of a new SKU whose prefix the config says was derived from live
NOT_LIVE = "not live, check"   # the source of a new SKU whose prefix no live SKU of its half shows (kept from a hand PO)
PREFIX_SHAPE = ("sku_prefix is a prefix per season half, each the letters of a new SKU before the season's two "
                "digits, on lines of their own: 'S/S: ZXSS' and 'F/W: ZXFW' (the draft adds the digits of the season "
                "picked for the run), or a MEN/WOMEN/UNISEX map of them where the brand's men's and women's SKUs "
                "differ")


def with_season(cfg, season):
    """A copy of the config for one run, with the season the colleague picked, in the store's spelling."""
    s = str(season if season is not None else "").strip()
    if not SEASON.fullmatch(s):
        raise BrandError(f"the season of a run is written as the store writes it, S/S 27 or F/W 26, got {season!r}")
    return dict(cfg, season=s)


def season_order(season):
    """(year, 0 for S/S or 1 for F/W and A/W) to sort seasons by, from any spelling the store holds; None for none."""
    m = LOOSE_SEASON.search(str(season or "").upper())
    return (int(m.group(2)), 0 if m.group(1) == "S/S" else 1) if m else None


def prefix_gender(cfg, gender=None):
    """The gender whose prefix a product of `gender` takes in the run's season: its own, else the default_gender's;
    None when the season's half has one prefix for every gender, or none."""
    pre = (cfg.get("sku_prefix") or {}).get(SEASON.fullmatch(cfg["season"]).group(1))
    if not isinstance(pre, Mapping):
        return None
    return gender if gender in pre else cfg["default_gender"]


def sku_prefix(cfg, gender=None):
    """(prefix, why) for a new SKU of a product of `gender` in the run's season: the config's prefix for the season's
    half (the default_gender's when the gender has none of its own) and the season's two digits. (None, why) when
    the config has no prefix for that half."""
    half, yy = SEASON.fullmatch(cfg["season"]).groups()
    pre = (cfg.get("sku_prefix") or {}).get(half)
    if pre is None:
        return None, f"the brand config has no SKU prefix for {half} seasons"
    g = prefix_gender(cfg, gender)
    if g is None:
        return pre + yy, f"the {half} prefix"
    why = "" if g == gender else f"; no {gender or 'gender'} prefix, so the {g} one (default_gender)"
    return pre[g] + yy, f"the {g} {half} prefix" + why


def prefix_mark(cfg):
    """(source, sentence) of the run's new SKUs, by sku_prefix_from for the season's half: (DERIVED, ...) or
    (NOT_LIVE, ...), else ("rule", None) for a prefix the config sets by hand."""
    note = (cfg.get("sku_prefix_from") or {}).get(SEASON.fullmatch(cfg["season"]).group(1))
    if note is None:
        return "rule", None
    return (DERIVED if note.startswith(DERIVED) else NOT_LIVE), note


def prefix_summary(cfg):
    """'ZXMS27 (MEN), ZXWS27 (WOMEN)' or 'ZXSS27': the prefixes new SKUs of the run take; None for no prefix."""
    pre = (cfg.get("sku_prefix") or {}).get(SEASON.fullmatch(cfg["season"]).group(1))
    if pre is None or not isinstance(pre, Mapping):
        return sku_prefix(cfg)[0]
    return ", ".join(f"{sku_prefix(cfg, g)[0]} ({g})" for g in GENDERS if g in pre)


def _sku_prefix(cfg, source):
    """sku_prefix checked at load (PREFIX_SHAPE). A one-string prefix ('ZXSS27') is refused: it holds one season, so
    it could not follow the season picked for a run. A prefix is 1 to 12 capital letters and digits (an upload cannot
    build a huge SKU) ending in its half's season letters, so an S/S run never writes F/W letters."""
    pre = cfg["sku_prefix"]
    if not isinstance(pre, Mapping) or not pre or any(k not in HALF_LETTERS for k in pre):
        raise BrandError(f"{source}: {PREFIX_SHAPE}; got {pre!r}")
    out = {}
    for half, v in pre.items():
        if isinstance(v, Mapping):
            if not v or any(g not in GENDERS for g in v):
                raise BrandError(f"{source}: sku_prefix {half} by gender names MEN, WOMEN or UNISEX, got {v!r}")
            if cfg["default_gender"] not in v:
                raise BrandError(f"{source}: sku_prefix {half} by gender must name the default_gender "
                                 f"({cfg['default_gender']}), whose prefix a product of another gender takes; "
                                 f"got {v!r}")
            v = dict(v)
        for p in (v.values() if isinstance(v, dict) else [v]):
            if not isinstance(p, str) or not re.fullmatch(r"[A-Z0-9]{1,12}", p):
                raise BrandError(f"{source}: sku_prefix {half} is 1 to 12 capital letters and digits, got {p!r}")
            if not p.endswith(HALF_LETTERS[half]):
                raise BrandError(f"{source}: sku_prefix {half} {p!r} must end in its season letters "
                                 f"({' or '.join(HALF_LETTERS[half])}), so a {half} run never writes another half's")
        out[half] = v
    cfg["sku_prefix"] = out
    notes = cfg.get("sku_prefix_from")
    if notes is None:
        return
    if not isinstance(notes, Mapping) or not notes or any(h not in out for h in notes):
        raise BrandError(f"{source}: sku_prefix_from says, per season half that has a prefix (S/S:, F/W:), where that "
                         f"half's prefixes came from; got {notes!r}")
    for half, note in notes.items():
        if not isinstance(note, str) or not (note.startswith(DERIVED) or NOT_LIVE in note):
            raise BrandError(f"{source}: sku_prefix_from {half} is a sentence that starts \"{DERIVED}\" or says "
                             f"\"{NOT_LIVE}\", got {note!r}")
    cfg["sku_prefix_from"] = dict(notes)


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


def code_for(brand, style, fabric, colour, tpl=None, style_alt=None):
    """The store's manufacture_code for an OC line, by the brand's join_key (or the template given, e.g.
    join_key_alt). None when the template needs a {style_alt} the line does not carry."""
    tpl = tpl or (brand or {}).get("join_key") or DEFAULT_JOIN_KEY
    if "{style_alt}" in tpl and not style_alt:
        return None
    vals = dict(style=style, fabric=fabric or "", colour=colour or "", style_alt=style_alt or "")
    if "colour_code" in ALT_TOKEN.findall(tpl):
        cc = (brand.get("colour_codes") or {}).get(norm_colour(colour))
        if cc is None:
            raise ColourCodeError([str(colour)])
        vals["colour_code"] = cc
    return norm_code(ALT_TOKEN.sub(lambda m: str(vals[m.group(1)]), tpl))


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
    hold = cfg.get("hold")
    if hold is not None and (not isinstance(hold, str) or not hold.strip()):
        raise BrandError(f"{source}: hold is the reason this brand's config must not be used yet, as a sentence "
                         f"(the page refuses every OC for the brand while it is set), got {hold!r}")
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
    ign = cfg.get("match_ignore")
    if ign is not None and (not isinstance(ign, str) or not ign or any(ch.isalnum() for ch in ign)):
        raise BrandError(f"{source}: match_ignore is the separator characters (no letters or digits) left out of "
                         f"both codes when matching the OC to live products, e.g. \"-_ \", got {ign!r}")
    for key, what in (("oc_supplier", "a company name"), ("oc_label", "a label name"),
                      ("oc_style_prefix", "a style-number prefix")):
        val = cfg.get(key)
        if val:
            val = [val] if isinstance(val, str) else list(val)
            if not all(isinstance(x, str) and x.strip() for x in val):
                raise BrandError(f"{source}: {key} is {what} or a list of them, got {cfg[key]!r}")
            cfg[key] = val
    kind = cfg.get("source_kind")
    if kind is not None and not (isinstance(kind, str) and kind.strip()):
        raise BrandError(f"{source}: source_kind is a short text naming what the file is "
                         f"(e.g. 'SAP demand export'), got {kind!r}")
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
    alt = cfg.get("join_key_alt")
    if alt is not None:
        rest = ALT_TOKEN.sub("", alt) if isinstance(alt, str) else "{"
        if "{" in rest or "}" in rest or not ALT_TOKEN.findall(alt) or alt == tpl:
            raise BrandError(f"{source}: join_key_alt is a second join template, tried only when join_key finds no "
                             "live product, from {style}, {style_alt}, {fabric}, {colour}, {colour_code}, and not "
                             f"join_key itself; got {alt!r}")
        fields = fields + ALT_TOKEN.findall(alt)
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
    if cfg["adapter"] == "sheet":
        from .adapters import sheet
        bad = sheet.check_mapping(cfg.get("sheet"), cfg)
        if bad:
            raise BrandError(f"{source}: " + "; ".join(bad))


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
    unknown = [k for k in data if k not in KEYS]
    if unknown:   # fail closed: a misspelt key would be ignored (`on_hold:` loaded a brand unheld)
        raise BrandError(f"{source}: unknown key{'s' if len(unknown) > 1 else ''} "
                         f"{', '.join(repr(k) for k in unknown)}: a typo? A brand config reads only the keys "
                         "oc/brands/example.yaml and oc/README.md describe, so nothing loads until it is fixed")
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(data)
    cfg.pop("season", None)   # picked per run (with_season); a season an old config carries is never used
    missing = [k for k in REQUIRED if cfg.get(k) in (None, "")]
    if missing:
        raise BrandError(f"{source}: missing {missing}")
    _reader_keys(cfg, data, source)
    _text_templates(cfg, source)
    _numbers(cfg, source)
    _sku_prefix(cfg, source)
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
