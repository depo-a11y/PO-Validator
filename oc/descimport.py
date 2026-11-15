"""The description import finalize writes beside PU-new: PU-description.xlsx (Colwyn, 2026-11-16, option B,
"Separate import").

The new-product upload keeps the brand's template Body HTML. This file is the reviewed house descriptions, imported
after PU-new has created the products, in the slot of the hand-made "<Brand> Description.xlsx": sheet Products,
columns Variant SKU, Command, Body HTML and no other (a blank cell clears a field in Matrixify). One row per NEW
product, keyed on the Variant SKU of its first variant as PU-new mints it.

Command is UPDATE on every row, the one deliberate change from the hand slot: the hand sheets carry no Command, which
Matrixify reads as MERGE, and MERGE creates a new product when the SKU is not found. UPDATE makes such a row fail, so
importing this file before PU-new can never create half-made products.

Each description comes from the reviewed PO's Descriptions sheet (oc/poxlsx.write_descriptions). A title fixed in
review is one edit: when the description still holds the drafted Title verbatim, the final Title from the PO replaces
it; when the person edited the description so that it no longer does, their text is kept and must hold the final
Title. A product whose description fails a gate (problems) is left out and named in a WARN, and never stops the
upload files. A config on hold writes nothing.
"""
import html
import re

from . import describe
from .brand import hold_row
from .poxlsx import CODE, read_descriptions
from .util import norm_code

FILE = "PU-description.xlsx"
SHEET = "Products"
COLUMNS = ["Variant SKU", "Command", "Body HTML"]
COMMAND = "UPDATE"   # fails on a SKU not yet created; the hand sheets' blank Command is MERGE, which would create one
CHECK = (f"Description import ({FILE}: Variant SKU, Command UPDATE, Body HTML; import after the products are created)")
INTRO = re.compile(r"^\s*<p>(.*?)</p>", re.S)
COMPOSITION = re.compile(r"<li>\s*Composition:\s*(.*?)</li>", re.S)
SKU_LINE = re.compile(r"<p>\s*SKU:\s*(.*?)</p>\s*$", re.S)
MADE_IN_DASH = re.compile(r"Made in\s*(?:-|–|—)\s*<", re.I)
TAGS = re.compile(r"<[^>]+>")


def _text(fragment):
    return describe.squash(html.unescape(TAGS.sub(" ", fragment or "")))


def swap_title(body, drafted, final):
    """(body, swapped): the drafted Title, verbatim and escaped as the draft wrote it, replaced by the final one."""
    old, new = html.escape(str(drafted or ""), quote=False), html.escape(str(final or ""), quote=False)
    if old and old != new and old in body:
        return body.replace(old, new), True
    return body, False


def problems(body, title, code, vendor, ptype):
    """Why a description cannot go in the import, else []: the house frame, a real Composition line, no 'Made in -',
    no gender or season word outside the title, the SKU line on the product's own code, the final Title in the intro,
    and the red checks re-run on the final Title and Type (the title's kind and an AI second sentence's against the
    Type's item noun)."""
    body = str(body or "").strip()
    out = []
    intro = INTRO.match(body)
    if not (intro and intro.group(1).startswith(html.escape(f"{str(vendor).upper()}'s ", quote=False))):
        out.append(f"it does not open with the intro \"{str(vendor).upper()}'s ...\"")
    sku = SKU_LINE.search(body)
    if not sku:
        out.append("its last line is not the SKU line")
    elif norm_code(_text(sku.group(1))) != norm_code(code):
        out.append("its SKU line is not this product's code")
    comp = COMPOSITION.search(body)
    if not comp or _text(comp.group(1)).casefold() in describe.PLACEHOLDERS:
        out.append("it has no Composition line" if not comp else "its Composition line is a placeholder")
    if MADE_IN_DASH.search(body):
        out.append("it says 'Made in -'")
    copy = _text(body[:sku.start()] if sku else body).replace(_text(html.escape(str(title), quote=False)), " ")
    word = describe.banned(copy)
    if word:
        out.append(f"it names a {word} outside the title")
    if not intro or html.escape(str(title), quote=False) not in intro.group(1):
        out.append("its intro does not hold the final Title")
    item = describe.item_noun(ptype, title) if describe.squash(ptype) else None
    named, mine = describe.title_kind(title), describe.item_kind(item, last=True)
    if named and mine and named != mine:
        out.append(describe.TITLE_MISMATCH)
    if intro and item:
        rest = _text(intro.group(1)).replace(str(title), " ", 1).split(". ", 1)
        if len(rest) > 1 and describe.item_mismatch(rest[1], item):
            out.append(describe.ITEM_MISMATCH)
    return out


def _products(new_rows):
    """[(first PO row of each NEW product)] in PU-new's order: a product is a Title."""
    seen, out = set(), []
    for r in new_rows:
        t = describe.squash(r.get("Title"))
        if t not in seen:
            seen.add(t)
            out.append(r)
    return out


def build(po_path, new_rows, brand, out_prefix):
    """(checks, writers) for the description import of the NEW products in `new_rows` (finalize's PO rows)."""
    if hold_row(brand):
        return [], []
    sheet = read_descriptions(po_path)
    if sheet is None:
        return [dict(Check=CHECK, Result="INFO", Detail="this PO has no Descriptions sheet (a PO not drafted by the "
                                                        "tool), so no description import is written")], []
    by_sku, by_code = {}, {}
    for d in sheet:
        by_sku.setdefault(describe.squash(d.get("Variant SKU")), d)
        by_code.setdefault(norm_code(d.get("Manufacture code")), []).append(d)
    written, held = [], []
    for r in _products(new_rows):
        sku, code, title = describe.squash(r.get("Variant SKU")), norm_code(r.get(CODE)), describe.squash(r.get("Title"))
        d = by_sku.get(sku) or (by_code.get(code, [None])[0] if len(by_code.get(code, [])) == 1 else None)
        if d is None or not describe.squash(d.get("Description")):
            held.append((code, title, "no description drafted for it"))
            continue
        body, _ = swap_title(str(d["Description"]), d.get("Title (as drafted)"), title)
        why = problems(body, title, code, r.get("Vendor") or brand["vendor"], r.get("Type"))
        if why:
            held.append((code, title, "; ".join(why)))
            continue
        written.append({"Variant SKU": sku, "Command": COMMAND, "Body HTML": body})
    total = len(written) + len(held)
    detail = (f"{len(written)} of {total} NEW products written to {FILE} (sheet {SHEET}, columns "
              f"{', '.join(COLUMNS)}), Command UPDATE: a row fails rather than creating a product if the file is "
              "imported before PU-new. Import it after the products are created")
    if held:
        detail += (f"; {len(held)} held back, fix the description in the PO's Descriptions sheet and finalize again: "
                   + "; ".join(f"{c} '{t}': {w}" for c, t, w in held))
    checks = [dict(Check=CHECK, Result="WARN" if held else "PASS", Detail=detail)]
    if not written:
        return checks, []

    def write(path):
        import pandas as pd
        from .finalize import run_id
        from .poxlsx import write_frame
        write_frame(path, pd.DataFrame(written, columns=COLUMNS), SHEET, run_id(out_prefix))
    return checks, [(f"{out_prefix}{FILE}", write, f"{len(written)} rows, Command {COMMAND}, import after PU-new")]
