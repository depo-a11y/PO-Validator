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
  body's Made in line is left as entered), else from the template body's Made in line; only a country the tool knows.
  Both copies are read against the one value the draft filled them from, so custom.made_in fixed alone is one edit,
  and only a body line changed alone, or the two changed apart, holds (made_in_now);
- the colourway bullet, from the final title's colour words by the draft's own rule (colourway), keyed on the bullet
  the draft wrote (its "Colourway (as drafted)" column): put in where the draft wrote none, left out where the person
  removed one; a part the draft did not write so (typed or edited by hand) that still names a colour word the final
  title drops holds the product (old_colour): lines still as drafted and the lines the merge wrote are not read.
The Title and the item noun: a description still holding the drafted one takes the PO's; an edited one must agree.
Composition and Made in are a three-way merge whose base is the PO's own value at draft (merge): the PO and the
description draw on different sources at draft (the description the OC line first, the PO's template the cart, then
the live product), so only a PO value changed in review is a fix to carry.
- The PO's value as drafted: the description's line stands, drafted or edited (the gates judge it); a line it lacks
  gets the PO's, at its house place (Composition first in the list, Made in last, the list made when there is none).
- The PO's value changed: a line still holding the drafted value, or a placeholder, takes the PO's; a line it lacks
  gets it; an edited line is kept when it agrees and held back, both values named, when it does not. A PO value
  cleared to a placeholder while the description still has the line is held back. A Composition line that ends up
  other than drafted, by the merge or by hand, holds the product back while anything drawn from the drafted one is
  still there (stale_bullets).
A placeholder is never written. The Descriptions sheet carries every value the merge needs in its "(as drafted)"
columns; a sheet without them writes no import. A product whose description fails a gate (problems) is left out and
named in a WARN, and never stops the upload files. A config on hold writes nothing.
"""
import html
import re
import unicodedata

from . import describe
from .brand import hold_row
from .poxlsx import CODE, DESCRIPTION_COLS, MADE_IN, read_descriptions
from .util import blank, norm_code, title_colour

FILE = "PU-description.xlsx"
SHEET = "Products"
COLUMNS = ["Variant SKU", "Command", "Body HTML"]
COMMAND = "UPDATE"   # fails on a SKU not yet created; the hand sheets' blank Command is MERGE, which would create one
CHECK = (f"Description import ({FILE}: Variant SKU, Command UPDATE, Body HTML; import after the products are created)")
INTRO = re.compile(r"^\s*<p>(.*?)</p>", re.S)
COMPOSITION = re.compile(r"<li>\s*Composition:\s*(.*?)</li>", re.S)
MADE_IN_LINE = re.compile(r"<li>\s*Made in\b\s*(.*?)\s*</li>", re.S)
SKU_LINE = re.compile(r"<p>\s*SKU:\s*(.*?)</p>\s*$", re.S)
MADE_IN_DASH = re.compile(r"Made in\s*(?:[-–—_.?]+|n\W?a\W?|none|null|unknown|tb[acd])\s*<", re.I)
PLACEHOLDER = re.compile(r"[-–—_.?\s]+|n\W*a\W*|none|null|nan|unknown|tb[acd]", re.I)   # '--', 'n.a.', 'N/A'
COLOURWAY = re.compile(r"\n?[ \t]*<li>\s*([^<]*?)\s+colourway\s*</li>", re.I)
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
    Composition and Made in as it wrote them (None where it wrote no such line), the PO row's own composition and
    made in at draft (the composition read through the template; the made in as written, the one value the draft fills
    both custom.made_in and the template's Made in line from): the merge's base, the colourway bullet the draft wrote
    (None when it wrote none: a colour code, a red title, a banned word), and the drafted description whole."""
    lines = [rx.search(str(body or "")) for rx in (COMPOSITION, MADE_IN_LINE)]
    values = template_values(row.get("Body HTML"), brand["body_html"])
    raw = (values or {}).get("composition")
    bullet = COLOURWAY.search(str(body or ""))
    return ([row.get("Type")] + [m.group(1).strip() if m else None for m in lines]
            + [None if raw is None else _text(raw), _text(row.get(MADE_IN) if not blank(row.get(MADE_IN))
                                                          else (values or {}).get("made_in")) or None]
            + [f"{_text(bullet.group(1))} colourway" if bullet else None, body])


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
    """A value that says none: the draft's PLACEHOLDERS, a run of dashes, an 'n/a' of any spelling, the brand's own."""
    t = describe.squash(text)
    return t.casefold() in describe.PLACEHOLDERS or bool(PLACEHOLDER.fullmatch(t)) or \
        t == describe.squash(brand.get("unknown"))


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


def place(value, brand):
    """A made in as compared: '' for none or a placeholder, else the country name the tool knows, else the text."""
    t = _text(value)
    return "" if _placeholder(t, brand) else (describe.country(t, brand) or t)


def made_in_now(row, values, base, brand):
    """(the made in the reviewed PO gives, escaped, or None when it gives none; the problems), from its two copies:
    custom.made_in, which complete and PU-new treat as authoritative, and the template body's Made in line. Each is
    read against `base`, the one value the draft filled both from (draft.build writes both from n['made']), so the
    copy review changed is known:
    - custom.made_in empty then and now: the line is the source;
    - only custom.made_in changed: it is carried, unless it was cleared while the line still names a country;
    - only the line changed, or both to different values: the two disagree, and which is right cannot be told;
    - both changed to one value: it is carried.
    A brand template with no {made_in} slot leaves the PO one copy, custom.made_in: changed or not, it is the value;
    cleared, the PO gives none, and merge's cleared rule decides.
    Only a country the tool knows is written; any other value is named as that, never as a clear."""
    meta = place(None if blank(row.get(MADE_IN)) else row.get(MADE_IN), brand)
    line, was = place((values or {}).get("made_in"), brand), place(base, brand)
    same = lambda a, b: a.casefold() == b.casefold()
    if "made_in" not in (values or {}):
        now = meta
    elif not was and not meta:
        now = line
    elif same(line, was) and not meta:
        return None, [f"the PO's custom.made_in was cleared, its Body HTML Made in line still says '{line}': make "
                      "them agree"]
    elif same(line, was):
        now = meta
    elif same(meta, was) or not same(meta, line):
        return None, [f"the PO's custom.made_in says '{meta or '-'}', its Body HTML Made in line '{line or '-'}': "
                      "make them agree"]
    else:
        now = meta
    if now and not describe.country(now, brand):
        return None, [f"the PO's made in '{now}' is not a country the tool knows: fix it there"]
    return (_e(now) if now else None), []


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


def stale_bullets(body, drafted, final, item=""):
    """[(where, text)] the description still says from the drafted composition that the final one does not give: the
    tool's part bullets (describe.part_bullets: 'Cupro lining') the final composition does not give too, and any other
    bullet or intro sentence every word of which the drafted composition holds and the final one does not (the AI's
    own grounding rule, oc/agent/describe.ungrounded: a part the AI rested on the composition)."""
    from .agent.describe import ungrounded
    keep = {b for b, _ in describe.part_bullets(_text(final))}
    out = [("bullet", b) for b, _ in describe.part_bullets(_text(drafted))
           if b not in keep and re.search(rf"<li>\s*{re.escape(_e(b))}\s*</li>", body)]
    was, now = _text(drafted), _text(final)
    intro = INTRO.match(body)
    said = [("bullet", _text(x)) for x in re.findall(r"<li>(.*?)</li>", body, re.S)
            if not re.match(r"\s*(?:Composition:|Made in\b)", x)]
    said += [("intro", s.strip(" .")) for s in (_text(intro.group(1)).split(" is ", 1)[1:2][0].split(". ")
                                                if intro and " is " in _text(intro.group(1)) else [])]
    for where, t in said:
        if t and (where, t) not in out and not ungrounded(t, was, item) and ungrounded(t, now, item):
            out.append((where, t))
    return out


def colourway(body, drafted, final):
    """(body, problems): the colourway bullet brought to the final title by the draft's own rule (describe.draft: the
    title's colour words, none for a code with a digit or a banned word), keyed on `drafted`, the bullet the draft
    wrote (the "Colourway (as drafted)" column; None when it wrote none).
    - The draft wrote none and the body has none: the final title's goes in at the draft's place, right after the
      Composition line, else first in the list (a raw-code title fixed in review, a red title).
    - The body still holds the drafted bullet: it takes the final colour words, or goes when the final title gives none.
    - The draft wrote one and the body has none: the person removed it, so it stays out.
    - Any other colourway bullet is an edit, held when it disagrees with the final title."""
    rule = lambda c: c if c and not re.search(r"\d", c) and not describe.banned(f"{c} colourway") else ""
    new = rule(title_colour(final)[1])
    out = []
    found = list(COLOURWAY.finditer(body))
    if not found and not drafted and new:
        body = _after_composition(body, f"  <li>{_e(new)} colourway</li>")
    for m in reversed(found):
        said = _text(m.group(1))
        if said.casefold() == new.casefold():
            continue
        if drafted and f"{said} colourway".casefold() == _text(drafted).casefold():
            body = body[:m.start(1)] + _e(new) + body[m.end(1):] if new else body[:m.start()] + body[m.end():]
        else:
            out.append(f"its colourway bullet says '{said}', the final title's colour words "
                       f"'{new or 'none'}': make them agree")
    return body, out


def _after_composition(body, li):
    """`li` right after the Composition line, else first in the list (made, before the SKU line, when there is none)."""
    comp = COMPOSITION.search(body)
    return body[:comp.end()] + "\n" + li + body[comp.end():] if comp else _insert(body, li, True)


def _core(intro, title, item):
    """An intro with the tool's own swaps undone: its title, and in its first sentence the article and the item noun
    (swap_title, swap_item), so the drafted intro and the same intro after review fixed the title or the Type read
    alike, and only a hand edit tells them apart."""
    t = intro.replace(describe.squash(title), " ", 1)
    head, sep, rest = t.partition(" is ")
    if not sep:
        return t
    first, dot, tail = rest.partition(". ")
    first = re.sub(r"^an? ", "", first.rstrip("."))
    adj = _adjective(first, item) if item else None
    return head + sep + (first if adj is None else adj) + dot + tail


def _parts(body, title, item):
    """(the intro, as text; the intro with the tool's swaps undone; the list's lines; the rest) of a description."""
    body = str(body or "")
    intro = INTRO.match(body)
    lines = [_text(x) for x in re.findall(r"<li>(.*?)</li>", body, re.S)]
    rest = re.sub(r"<li>.*?</li>", " ", body[intro.end():] if intro else body, flags=re.S)
    text = _text(intro.group(1)) if intro else None
    return text, (_core(text, title, item) if intro else None), lines, _text(SKU_LINE.sub(" ", rest))


def old_colour(body, drafted, final, drafted_body, old_item=None, new_item=None):
    """Why the description still names a colour word the drafted title had and the final one drops, in a part the
    draft did not write so (a line typed or edited by hand, the drafted title moved out of the intro's frame), else
    ''. A part still as drafted rests on order fields review did not change (no tool or AI part rests on the title
    but the colourway bullet, which colourway handles), and the Composition and Made in lines come from the PO, so
    neither is read: 'Gold hardware' and '100% Camel Hair' stay true whatever the title's colour. A colour word can
    be a material, so the message asks for a check, never for the line to go.

    The colour is read as the title writes it. A colour gone from the title is matched three ways:
    - a hyphen compound ('Off-White', 'Two-Tone') matches a word that equals it ('off-white piping');
    - a single word ('Slate') matches a word that equals it, or one hyphen part of a word ('Slate-tone trims');
    - each part of a gone compound that the final title does not keep, of three letters or more ('off', 'two',
      'tone'), matches only a whole word with no hyphen ('Off white piping', 'Two tone piping'), never a hyphen
      part, so 'Zip-off hood', 'Two-way zip' and 'Gold-tone hardware' stay.
    A word character always blocks ('Slates', 'slate_trims'). A compound only respelt in the final title ('Off-White'
    as 'Off White') is not gone. What this still holds, and never writes: 'Off White' written with a space, then
    dropped, leaves 'off' gone on its own ('Zip-off hood' holds); a gone compound's part on its own ('Two front
    pockets' after 'Two-Tone Black' becomes 'Black'). Titles and text are read in one Unicode form (NFC), so a
    composed 'Rosé' and one spelt with a combining accent are the same word."""
    nfc = lambda t: unicodedata.normalize("NFC", t or "")
    tokens = lambda c: {t.casefold() for t in re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)*", nfc(c))
                        if "-" in t or len(t) >= 3}   # as the title writes them: 'two-tone' whole, never 'two'
    now = tokens(title_colour(final)[1])
    parts = {x for t in now for x in t.split("-")}
    gone = {t for t in tokens(title_colour(drafted)[1] if describe.squash(drafted) else "") - now
            if not set(t.split("-")) <= parts}   # 'Off-White' respelt 'Off White' is not gone
    if not gone:
        return ""
    whole = {g for g in gone if "-" in g}
    loose = gone - whole
    strict = {p for g in whole for p in g.split("-") if p not in parts and len(p) >= 3}
    intro, core, lines, rest = _parts(body, final, new_item)
    _, was_core, was_lines, was_rest = _parts(drafted_body, drafted, old_item) if drafted_body else (None, None, [], None)
    read = [x for x in (intro if core != was_core else None, rest if rest != was_rest else None) if x]
    read += [x for x in lines if x not in was_lines and not re.match(r"(?:Composition:|Made in\b)", x)
             and not COLOURWAY.fullmatch(f"<li>{_e(x)}</li>")]
    for x in read:
        said = [w.casefold() for w in re.findall(r"\w+(?:-\w+)*", nfc(x).replace(nfc(describe.squash(final)), " "))]
        hit = [g for g in sorted(whole | loose | strict) if any(w == g or (g in loose and g in w.split("-")) for w in said)]
        if hit:
            return (f"its '{x}' names '{hit[0]}', a colour word of the drafted title that the final title drops: "
                    "check whether it means the old colour")
    return ""


def reconcile(body, d, row, brand):
    """(body, problems): one description brought to the reviewed PO's final Title, item noun, Composition and Made in
    (see the module docstring); the problems name each edit that disagrees with the PO."""
    vendor, title, ptype = row.get("Vendor") or brand["vendor"], describe.squash(row.get("Title")), row.get("Type")
    out = []
    body, _ = swap_title(body, d.get("Title (as drafted)"), title, vendor)
    body, bad = colourway(body, d.get("Colourway (as drafted)"), title)
    out += bad
    old_type = d.get("Type (as drafted)")
    old = describe.item_noun(old_type, d.get("Title (as drafted)")) if describe.squash(old_type) else None
    new = describe.item_noun(ptype, title) if describe.squash(ptype) else None
    if old and not new:
        out.append(f"the final Type '{describe.squash(ptype)}' gives the tool no item noun to check the intro's "
                   f"'{old}' against")
    body = swap_item(body, vendor, title, old, new)
    values = template_values(row.get("Body HTML"), brand["body_html"])
    slots = [w for k, w in (("composition", "composition"), ("made_in", "made in")) if f"{{{k}}}" in brand["body_html"]]
    if values is None and slots:
        out.append(f"the PO's Body HTML no longer follows the brand's template ({brand['body_html']}), so its "
                   f"{' and '.join(slots)} cannot be read")
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
        if drafted and after and not same_text(drafted, after.group(1)):   # changed, by the merge or by hand
            out += [f"its {where} '{t}' comes from the drafted composition, which changed: remove it or make it agree"
                    for where, t in stale_bullets(body, drafted, after.group(1), new or "")]
    unread = values is None and "made in" in slots   # held by the one reason above, no second
    made, why = (None, []) if unread else made_in_now(row, values, d.get("PO made in (as drafted)"), brand)
    out += why
    if not why and not unread:
        same = lambda a, b: place(a, brand).casefold() == place(b, brand).casefold()
        body, how, said = merge(body, MADE_IN_LINE, d.get("Made in (as drafted)"),
                                place(d.get("PO made in (as drafted)"), brand), _text(made), made, same, "Made in ",
                                False, brand)
        if how == "cleared":
            out.append(f"the PO's made in was cleared, and the description still says 'Made in {said}'")
        if how == "disagrees":
            out.append(f"its Made in line says '{said}', the PO's made in '{_text(made)}': make them agree")
    stale = old_colour(body, d.get("Title (as drafted)"), title, d.get("Description (as drafted)"), old, new)
    if stale:
        out.append(stale)
    return body, out


def raw_codes(text, code):
    """The parts of the manufacture code (style, style+fabric, fabric, colour) that have a digit in them and stand as a
    word of `text`: a colour code left in an intro. A colour word such as BLACK in a code is no code."""
    body, _, colour = norm_code(code).partition(" ")
    parts = {p for p in (body, body[:9], body[9:], colour) if len(p) >= 2 and re.search(r"\d", p)}
    return sorted(parts & set(re.findall(r"[A-Z0-9]+", str(text or "").upper())))


def problems(body, title, code, vendor, ptype, brand=None):
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
    if not comp or _placeholder(_text(comp.group(1)), brand or {}):
        out.append("it has no Composition line" if not comp else "its Composition line is a placeholder")
    if MADE_IN_DASH.search(body):
        out.append("it says 'Made in -'")
    unknown = [_text(m) for m in MADE_IN_LINE.findall(body)
               if not _placeholder(_text(m), brand or {}) and not describe.country(_text(m), brand or {})]
    if unknown:
        out.append(f"its Made in line names '{unknown[0]}', not a country the tool knows")
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


def title_key(row):
    """A product's key: its Title as finalize groups PU-new's create rows (finalize._s: the text, stripped), so the
    import never merges two products PU-new creates apart."""
    v = row.get("Title")
    return "" if blank(v) else str(v).strip()


def sku_key(row):
    """A variant's Variant SKU as PU-new mints it (finalize._s: the text, stripped), the one key Matrixify joins the
    import on, so it is written from finalize's own row, never from a copy read for comparing."""
    v = row.get("Variant SKU")
    return "" if blank(v) else str(v).strip()


def _products(new_rows):
    """[(first PO row of each NEW product)] in PU-new's order: a product is a Title, keyed as finalize keys it."""
    seen, out = set(), []
    for r in new_rows:
        t = title_key(r)
        if t not in seen:
            seen.add(t)
            out.append(r)
    return out


def _nfc(row):
    """A copy of a Descriptions or PO row with every string value in one Unicode form (NFC), so a retype with a
    combining accent and the composed letter read as the same text in every comparison below."""
    return {k: unicodedata.normalize("NFC", v) if isinstance(v, str) else v for k, v in row.items()}


def _nfc_deep(v):
    """A copy of a brand config with every string in it, nested in dicts and lists too, in NFC."""
    if isinstance(v, str):
        return unicodedata.normalize("NFC", v)
    if isinstance(v, dict):
        return {_nfc_deep(k): _nfc_deep(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return type(v)(_nfc_deep(x) for x in v)
    return v


def build(po_path, new_rows, brand, out_prefix):
    """(checks, writers) for the description import of the NEW products in `new_rows` (finalize's PO rows). The
    Descriptions rows, the PO rows and the brand config are compared as NFC copies; finalize's own rows and brand are
    left as they are. Products are grouped on finalize's own Title key before the copies are taken, and each row's
    Variant SKU is written from finalize's own row as PU-new mints it (sku_key), never from its copy."""
    if hold_row(brand):
        return [], []
    brand = _nfc_deep(brand)
    sheet = read_descriptions(po_path)
    if sheet is None:
        return [dict(Check=CHECK, Result="INFO", Detail="this PO has no Descriptions sheet (a PO not drafted by the "
                                                        "tool), so no description import is written")], []
    sheet = [_nfc(d) for d in sheet]
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
    for raw in _products(new_rows):
        r = _nfc(raw)
        sku, code, title = describe.squash(r.get("Variant SKU")), norm_code(r.get(CODE)), describe.squash(r.get("Title"))
        d = by_sku.get(sku) or (by_code.get(code, [None])[0] if len(by_code.get(code, [])) == 1 else None)
        if d is None or not describe.squash(d.get("Description")):
            held.append((code, title, "no description drafted for it"))
            continue
        body, why = reconcile(str(d["Description"]), d, r, brand)
        why += problems(body, title, code, r.get("Vendor") or brand["vendor"], r.get("Type"), brand)
        if why:
            held.append((code, title, "; ".join(why)))
            continue
        written.append({"Variant SKU": sku_key(raw), "Command": COMMAND, "Body HTML": body})
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
