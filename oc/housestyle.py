"""House style for a NEW product's drafted title and colour words (draft.build calls these).

- Heel height as the brand's H-code, when the brand config says `heel_codes: true` (Maison Margiela: customers
  search "Tabi Ballerina H30"): '3cm' or '3cm Heel' in the stem becomes 'H30', the last word before
  'in <Colour>'. Only the attested codes H30, H50, H60 and H80 are written; 10cm and up stay as printed (the brand
  writes no H100). A NEW product of a style whose live colours say '3cm' then differs from them: MERGE titles are
  never rewritten.
- A cart colour word the brand config's `colour_spelling:` map names ('Antracite: Anthracite') is written as the
  map says. Only words the map names: a general spelling check would turn a brand's colour ('Militare') into another.
- When the title's product words came from the live catalog and the cart's Style Name says something else, the
  Why names the Style Name, so a reviewer sees both.
- A NEW title that is already the title of an ACTIVE live product is amber and a Checks WARN names the live handles:
  Matrixify makes a new handle, so the store would hold two products of one name.
Every change keeps the cell amber at least (source "rule"), never green.
"""
import collections
import re

from .cart import style_title
from .resolve import tidy_colour, worse
from .util import title_colour

HEEL_CODES = (30, 50, 60, 80)
HEEL = re.compile(r"(?i)(?<!\w)(\d+(?:[.,]\d)?)\s?cm(?:\s+heel)?(?!\w)")
LIVE_TITLE_CHECK = "NEW title is not already an ACTIVE live product's title"


def heel_code(stem):
    """('Tabi Ballerina H30', '3cm written H30') for 'Tabi Ballerina 3cm', else (stem, None)."""
    m = HEEL.search(stem)
    if not m:
        return stem, None
    mm = round(float(m.group(1).replace(",", ".")) * 10)
    if mm not in HEEL_CODES:
        return stem, None
    words = (stem[:m.start()] + " " + stem[m.end():]).split()
    return " ".join(words + [f"H{mm}"]), f"'{m.group(0)}' written H{mm} (house heel code)"


def colour_words(brand, name, source, why):
    """A cart colour description with the brand's colour_spelling map applied, whole words, any case."""
    if source != "cart" or not name:
        return name, source, why
    fixed, notes = name, []
    for wrong, right in (brand.get("colour_spelling") or {}).items():
        new = re.sub(rf"(?i)(?<!\w){re.escape(str(wrong))}(?!\w)", str(right), fixed)
        if new != fixed:
            fixed = new
            notes.append(f"'{wrong}' written '{right}' (brand colour_spelling)")
    if not notes:
        return name, source, why
    return fixed, worse(source, "rule"), f"{why}; {'; '.join(notes)}"


def new_title(brand, R, code, title, source, why):
    """A NEW product's drafted (title, source, why) in house style."""
    stem, colour = title_colour(title)
    notes = []
    if brand.get("heel_codes"):
        stem, note = heel_code(stem)
        if note:
            notes.append(note)
    row = R.cart_row(code)
    if row and row["style_name"] and source == "catalog" and style_title(row["style_name"]).casefold() != stem.casefold():
        why = f"{why}; the cart's Style Name is '{row['style_name']}'"
    if row and row["colour"] and colour and tidy_colour(row["colour"]).casefold() != colour.casefold():
        notes.append(f"colour words from the cart's '{row['colour']}'")
    if not notes:
        return title, source, why
    return (f"{stem} in {colour}" if colour else stem), worse(source, "rule"), f"{why}; {'; '.join(notes)}"


def mark_live_titles(pending, catalog, checks):
    """Shade amber, and WARN on, each NEW title that an ACTIVE live product already carries (any case)."""
    live = collections.defaultdict(list)
    for p in catalog.products.values():
        if p["status"].upper() == "ACTIVE" and p["title"]:
            live[p["title"].casefold()].append(p["handle"])
    hit = []
    for n in pending:
        handles = sorted(live.get(n["title"][0].casefold(), []))
        if handles:
            n["title"] = [n["title"][0], worse(n["title"][1], "rule"),
                          f"{n['title'][2]}; an ACTIVE live product already has this title: /{', /'.join(handles)}"]
            hit.append(f"'{n['title'][0]}' ({n['code']}): /{', /'.join(handles)}")
    if hit:
        checks.append(dict(Check=LIVE_TITLE_CHECK, Result="WARN",
                           Detail=f"{len(hit)} NEW title(s) already on ACTIVE live products, so the store would hold "
                                  "two products of one name: " + "; ".join(sorted(set(hit)))))
