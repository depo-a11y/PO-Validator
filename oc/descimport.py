"""The description import finalize writes beside PU-new: PU-description.xlsx (Colwyn, 2026-11-16, option B,
"Separate import").

The new-product upload keeps the brand's template Body HTML. This file is the reviewed house descriptions, imported
after PU-new has created the products, in the slot of the hand-made "<Brand> Description.xlsx": sheet Products,
columns Variant SKU, Command, Body HTML and no other (a blank cell clears a field in Matrixify). One row per NEW
product, keyed on the Variant SKU of its first variant as PU-new mints it.

Command is UPDATE on every row, the one deliberate change from the hand slot: the hand sheets carry no Command, which
Matrixify reads as MERGE, and MERGE creates a new product when the SKU is not found. UPDATE makes such a row fail, so
importing this file before PU-new can never create half-made products.

Each description comes from the reviewed PO's Descriptions sheet (oc/poxlsx.write_descriptions), brought to the
reviewed PO's final values for every field it restates (reconcile), so a fix made on the PO is one edit:
- the Title, in the intro's "{VENDOR}'s {Title} is" frame;
- the item noun the final Type gives (with its article), in the same frame;
- the Composition line, from the PO's template Body HTML, which the person reviews and PU-new imports;
- the Made in line, from the PO's custom.made_in, which complete and PU-new treat as authoritative (the template
  body's Made in line is left as entered), else from the template body's Made in line.
The Title and the item noun: a description still holding the drafted one takes the PO's; an edited one must agree.
Composition and Made in are a three-way merge whose base is the PO's own value at draft (merge): the PO and the
description draw on different sources at draft (the description the OC line first, the PO's template the cart, then
the live product), so only a PO value changed in review is a fix to carry.
- The PO's value as drafted: the description's line stands, drafted or edited (the gates judge it); a line it lacks
  gets the PO's, at its house place (Composition first in the list, Made in last, the list made when there is none).
- The PO's value changed: a line still holding the drafted value, or a placeholder, takes the PO's; a line it lacks
  gets it; an edited line is kept when it agrees and held back, both values named, when it does not. A PO value
  cleared to a placeholder while the description still has the line is held back. A composition the merge replaces
  holds the product back while a bullet drawn from the drafted one ("Cupro lining") is still there.
A placeholder is never written. The Descriptions sheet carries every value the merge needs in its "(as drafted)"
columns; a sheet without them writes no import. A product whose description fails a gate (problems) is left out and
named in a WARN, and never stops the upload files. A config on hold writes nothing.
"""
import html
import re

from . import describe
from .brand import hold_row
from .poxlsx import CODE, DESCRIPTION_COLS, MADE_IN, read_descriptions
from .util import blank, norm_code

FILE = "PU-description.xlsx"
SHEET = "Products"
COLUMNS = ["Variant SKU", "Command", "Body HTML"]
COMMAND = "UPDATE"   # fails on a SKU not yet created; the hand sheets' blank Command is MERGE, which would create one
CHECK = (f"Description import ({FILE}: Variant SKU, Command UPDATE, Body HTML; import after the products are created)")
INTRO = re.compile(r"^\s*<p>(.*?)</p>", re.S)
COMPOSITION = re.compile(r"<li>\s*Composition:\s*(.*?)</li>", re.S)
MADE_IN_LINE = re.compile(r"<li>\s*Made in\b\s*(.*?)\s*</li>", re.S)
SKU_LINE = re.compile(r"<p>\s*SKU:\s*(.*?)</p>\s*$", re.S)
MADE_IN_DASH = re.compile(r"Made in\s*(?:-|–|—)\s*<", re.I)
TAGS = re.compile(r"<[^>]+>")
TEMPLATE_TOKEN = re.compile(r"\{(composition|made_in|code)\}")
AS_DRAFTED = [c for c in DESCRIPTION_COLS if c.endswith("(as drafted)")]


def _text(fragment):
    return describe.squash(html.unescape(TAGS.sub(" ", fragment or "")))


def _e(text):
    return html.escape(str(text or ""), quote=False)


def frame(vendor, title):
    """The start of the house intro, escaped as the body holds it: "{VENDOR}'s {Title} is "."""
    return _e(f"{str(vendor).upper()}'s {title} is ")


def as_drafted(body, row, brand):
    """The Descriptions sheet's "(as drafted)" values after the Title: the PO row's Type, the drafted description's
    Composition and Made in as it wrote them (None where it wrote no such line), and the PO row's own composition and
    made in at draft, read as finalize reads them (template_values, po_made_in): the merge's base."""
    lines = [rx.search(str(body or "")) for rx in (COMPOSITION, MADE_IN_LINE)]
    values = template_values(row.get("Body HTML"), brand["body_html"])
    raw = (values or {}).get("composition")
    return ([row.get("Type")] + [m.group(1).strip() if m else None for m in lines]
            + [None if raw is None else _text(raw), _text(po_made_in(row, values, brand)) or None])


def swap_title(body, drafted, final, vendor):
    """(body, swapped): in the intro's "{VENDOR}'s {Title} is" frame only, the drafted Title replaced by the final one.
    Nowhere else, and not when the frame holds the final Title already: a person who typed it in, a final Title holding
    the drafted one ("Waxed " + the drafted), would get its prefix twice."""
    m = INTRO.match(body)
    old = frame(vendor, drafted)
    if not (m and describe.squash(drafted) and m.group(1).startswith(old)):
        return body, False
    return body[:m.start(1)] + frame(vendor, final) + body[m.start(1) + len(old):], True


def _adjective(phrase, item):
    """What stands before the item noun in the intro's '[adjective] {item}' ('pair of [adjective] {noun}'), else None
    when the phrase does not end in that item."""
    pair = item.startswith("pair of ")
    noun = item[len("pair of "):] if pair else item
    if pair and not phrase.startswith("pair of "):
        return None
    head = phrase[len("pair of "):] if pair else phrase
    if head == noun:
        return ""
    return head[:-len(noun) - 1] if head.endswith(" " + noun) else None


def swap_item(body, vendor, title, old, new):
    """The intro's item noun, in its "{VENDOR}'s {Title} is a [adjective] {item}." frame, from the drafted Type's to the
    final Type's, the article by sound. A frame that does not end its first sentence in the drafted item is left as
    it is (problems then checks the item the person wrote)."""
    m = INTRO.match(body)
    head = frame(vendor, title)
    if not (old and new and old != new and m and m.group(1).startswith(head)):
        return body
    at = m.start(1) + len(head)
    phrase = re.match(r"an? (.+?)\.(?=\s|$)", body[at:m.end(1)])
    adj = _adjective(phrase.group(1), old) if phrase else None
    if adj is None:
        return body
    noun = new[len("pair of "):] if new.startswith("pair of ") else new
    words = (adj + " " if adj else "") + noun
    words = f"pair of {words}" if new.startswith("pair of ") else words
    return body[:at] + f"{describe.article(words)} {words}" + body[at + phrase.end(1):]


def template_values(body, template):
    """{'composition': ..., 'made_in': ...} as the PO's Body HTML fills the brand's template (escaped, as the body
    holds them; a token the template lacks is missing), or None when the body no longer follows the template, the
    whitespace between its parts aside."""
    out, seen, pos = [], set(), 0
    loose = lambda lit: r"\s*".join(re.escape(w) for w in re.split(r"\s+", lit))
    for m in TEMPLATE_TOKEN.finditer(template):
        out.append(loose(template[pos:m.start()]))
        out.append(f"(?P={m.group(1)})" if m.group(1) in seen else f"(?P<{m.group(1)}>.*?)")
        seen.add(m.group(1))
        pos = m.end()
    out.append(loose(template[pos:]))
    m = re.fullmatch("".join(out), str(body or "").strip(), re.S)
    return None if m is None else {k: v for k, v in m.groupdict().items() if k != "code"}


def _placeholder(text, brand):
    return text.casefold() in describe.PLACEHOLDERS or text == describe.squash(brand.get("unknown"))


def po_composition(values, brand):
    """(the PO's composition as the Composition line writes it, in the house's Title Case, or None when the PO gives
    none: no {composition} in the template, or a placeholder; why the PO's own value cannot be written, else '')."""
    raw = (values or {}).get("composition")
    text = _text(raw)
    if raw is None or _placeholder(text, brand):
        return None, ""
    why = describe.composition_problem(text)
    return (None, f"the PO's Body HTML composition '{text}' {why}: fix it there") if why else \
        (_e(describe.house_case(text)), "")


def po_made_in(row, values, brand):
    """The country the reviewed PO gives, escaped: its custom.made_in, which complete and PU-new treat as authoritative,
    else its template body's Made in line; None when neither names one."""
    for v in (None if blank(row.get(MADE_IN)) else row.get(MADE_IN), (values or {}).get("made_in")):
        text = _text(v)
        if not _placeholder(text, brand):
            return _e(describe.country(text, brand) or text)
    return None


def _insert(body, li, first):
    """`li` at its house place in the description's list (first, else last); the list made, before the SKU line, when
    there is none."""
    if "</ul>" not in body:
        sku = SKU_LINE.search(body)
        at = sku.start() if sku else len(body)
        return body[:at] + f"<ul>\n{li}\n</ul><br>" + body[at:]
    if first and "<ul>" in body:
        at = body.index("<ul>") + len("<ul>")
        return body[:at] + "\n" + li + body[at:]
    at = body.rindex("</ul>")
    return body[:at].rstrip() + "\n" + li + "\n" + body[at:]


def restate(body, rx, drafted, final, same, label, first):
    """(body, the line's text when it disagrees with the PO's, else '') for one line the description restates from the
    PO. The PO gives none (`final` None): the line is left for the gates. No line: the PO's goes in at its house place.
    A line that agrees with the PO's is kept as written; one still holding the drafted value, or a placeholder, takes
    the PO's; any other is an edit that disagrees."""
    if final is None:
        return body, ""
    m = rx.search(body)
    if m is None:
        return _insert(body, f"<li>{label}{final}</li>", first), ""
    now = m.group(1).strip()
    if same(now, final):
        return body, ""
    if now == str(drafted or "").strip() or _text(now).casefold() in describe.PLACEHOLDERS:
        return body[:m.start(1)] + final + body[m.start(1) + len(m.group(1).rstrip()):], ""
    return body, _text(now)


def _norm(text, brand):
    """A PO value compared with its base: '' for none or a placeholder, else its words, case aside."""
    t = _text(text)
    return "" if _placeholder(t, brand) else t.casefold()


def merge(body, rx, drafted, base, now, final, same, label, first, brand):
    """(body, '' | 'cleared' | 'disagrees', the description's line) for one line the description restates from the
    PO, a three-way merge on the PO's value: `base` at draft, `now` at finalize (text), `final` the line the PO gives
    now (escaped; None when it gives none to write). The PO's value as drafted: the description's line stands, and a
    line it lacks gets the PO's. Changed: restate. Cleared while the description still has the line: 'cleared'."""
    line = rx.search(body)
    if _norm(now, brand) == _norm(base, brand):
        return (body if line else restate(body, rx, drafted, final, same, label, first)[0]), "", ""
    if final is None:
        return body, "cleared" if line and not _norm(now, brand) else "", _text(line.group(1)) if line else ""
    body, bad = restate(body, rx, drafted, final, same, label, first)
    return body, "disagrees" if bad else "", bad


def stale_bullets(body, drafted, final):
    """The bullets the drafted composition gave (describe.part_bullets: 'Cupro lining') that the description still
    carries and the PO's composition does not give."""
    keep = {b for b, _ in describe.part_bullets(_text(final))}
    return [b for b, _ in describe.part_bullets(_text(drafted))
            if b not in keep and re.search(rf"<li>\s*{re.escape(_e(b))}\s*</li>", body)]


def reconcile(body, d, row, brand):
    """(body, problems): one description brought to the reviewed PO's final Title, item noun, Composition and Made in
    (see the module docstring); the problems name each edit that disagrees with the PO."""
    vendor, title, ptype = row.get("Vendor") or brand["vendor"], describe.squash(row.get("Title")), row.get("Type")
    out = []
    body, _ = swap_title(body, d.get("Title (as drafted)"), title, vendor)
    old_type = d.get("Type (as drafted)")
    old = describe.item_noun(old_type, d.get("Title (as drafted)")) if describe.squash(old_type) else None
    new = describe.item_noun(ptype, title) if describe.squash(ptype) else None
    if old and not new:
        out.append(f"the final Type '{describe.squash(ptype)}' gives the tool no item noun to check the intro's "
                   f"'{old}' against")
    body = swap_item(body, vendor, title, old, new)
    values = template_values(row.get("Body HTML"), brand["body_html"])
    if values is None and "{composition}" in brand["body_html"]:
        out.append("the PO's Body HTML no longer follows the brand's template, so its composition cannot be read")
    elif values is not None:
        raw, base = _text(values.get("composition")), d.get("PO composition (as drafted)")
        comp, why = po_composition(values, brand)
        before = COMPOSITION.search(body)
        if why and (not before or _norm(raw, brand) != _norm(base, brand)):
            out.append(why)
        same_text = lambda a, b: _text(a).casefold() == _text(b).casefold()
        drafted = d.get("Composition (as drafted)")
        body, how, said = merge(body, COMPOSITION, drafted, base, raw, comp, same_text, "Composition: ", True, brand)
        if how == "cleared":
            out.append(f"the PO's composition was cleared, and the description still says '{said}'")
        if how == "disagrees":
            out.append(f"its Composition line says '{said}', the PO's Body HTML '{_text(comp)}': make them agree")
        after = COMPOSITION.search(body)
        if before and after and not same_text(before.group(1), after.group(1)):   # the merge replaced it
            out += [f"its bullet '{b}' comes from the drafted composition, which the PO's replaced: remove it or make "
                    "it agree" for b in stale_bullets(body, drafted, after.group(1))]
    made = po_made_in(row, values, brand)
    place = lambda t: (describe.country(_text(t), brand) or _text(t)).casefold()
    body, how, said = merge(body, MADE_IN_LINE, d.get("Made in (as drafted)"), d.get("PO made in (as drafted)"),
                            _text(made), made, lambda a, b: place(a) == place(b), "Made in ", False, brand)
    if how == "cleared":
        out.append(f"the PO's made in was cleared, and the description still says 'Made in {said}'")
    if how == "disagrees":
        out.append(f"its Made in line says '{said}', the PO's made in '{_text(made)}': make them agree")
    return body, out


def raw_codes(text, code):
    """The parts of the manufacture code (style, style+fabric, fabric, colour) that have a digit in them and stand as a
    word of `text`: a colour code left in an intro. A colour word such as BLACK in a code is no code."""
    body, _, colour = norm_code(code).partition(" ")
    parts = {p for p in (body, body[:9], body[9:], colour) if len(p) >= 2 and re.search(r"\d", p)}
    return sorted(parts & set(re.findall(r"[A-Z0-9]+", str(text or "").upper())))


def problems(body, title, code, vendor, ptype):
    """Why a description cannot go in the import, else []: the house frame, a real Composition line, no 'Made in -',
    no gender or season word outside the title, the SKU line on the product's own code, the final Title once in the
    intro, in its frame, and no raw code left in it (after the swap: an edited description that kept a coded title),
    and the red checks re-run on the final Title and Type (the title's kind, the intro's item and an AI second
    sentence's against the Type's item noun)."""
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
    if not intro or intro.group(1).count(_e(title)) != 1 or not intro.group(1).startswith(frame(vendor, title)):
        out.append(f"its intro does not hold the final Title once, as \"{str(vendor).upper()}'s {title} is ...\"")
    if intro and raw_codes(_text(intro.group(1)), code):   # after the final-Title swap: an edited intro kept the code
        out.append("its intro still holds a raw code: put the final Title in it")
    item = describe.item_noun(ptype, title) if describe.squash(ptype) else None
    named, mine = describe.title_kind(title), describe.item_kind(item, last=True)
    if named and mine and named != mine:
        out.append(describe.TITLE_MISMATCH)
    if intro and item:
        rest = _text(intro.group(1)).replace(str(title), " ", 1).split(". ", 1)
        said = describe.item_kind(rest[0].split(" is ", 1)[-1], last=True)
        if said and mine and said != mine:
            out.append(f"its intro names another kind of product than the final Type's item noun '{item}'")
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
    missing = [c for c in AS_DRAFTED if sheet and c not in sheet[0]]
    if missing:
        return [dict(Check=CHECK, Result="WARN", Detail=f"no description import is written: this PO's Descriptions "
                     f"sheet lacks the column(s) {', '.join(missing)}, which finalize needs to tell what review "
                     "changed from what was drafted. Draft the PO again with this tool")], []
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
        body, why = reconcile(str(d["Description"]), d, r, brand)
        why += problems(body, title, code, r.get("Vendor") or brand["vendor"], r.get("Type"))
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
