"""Order Confirmation adapter for the B2B wholesale platform whose orders are numbered "#3xxxxxx" (P3M).

The platform's name is printed nowhere on its documents, so the family is named after its order numbers.
Several brands sell on it, each under its own company (Willy Chavarria; GRUNOVIS FZCO for Gosha
Rubchinskiy; SOSU CO.,LTD. for Maison MIHARA YASUHIRO; Atelier Luxury Group, LLC for Amiri). The company
heads page 1 beside "Order number #3999001", "Order date", "Currency Dollar", "Price list USD", "Season".
One reader serves them all, so a brand config names its company in `oc_supplier`. The documents print
no label apart from the company: `label` is None.

Two table layouts, told apart by the table header:

grid (REFERENCE DESCRIPTION SIZES QUANTITY UNIT PRICE AMOUNT, currency codes under the money columns):

    KEEP TEST TEE                                  item name, then small labels over values:
    COLLECTION / AW99 UNISEX  CATEGORY / T-SHIRT  ORIGIN / USA   (ORIGIN may be missing)
    Variant XXS XS S M L XL XXL                    this item's own size header
      BLACK                                        colour, 3.5pt above its quantity row
                      1  2  1           4   101   404       sizes, QUANTITY, UNIT PRICE, AMOUNT
      Ref. ZZTE001-001N-001                        the style reference (blank on some lines)
      100% COTTON...
      SRP: $255                                    the brand's suggested retail price
      (another colour: colour, quantities, Ref., composition, SRP)
    Subtotal 4 404                                 the item's units and value

inline (STYLE REFERENCE VARIANT SRP SIZES QTY UNIT TOTAL, currency codes over SRP, UNIT and TOTAL),
SOSU's own print of a platform order:

    ONE TEST JACKET                    44 46 48 50 52     item name and its size header on one row
      ZZ8BL001  BLACK  39,999        1  1     2  12,345  24,690
      COTTON100%
      Origin: CHINA

SOSU puts a PROFORMA INVOICE & EXPORT SALES CONTRACT cover in front, with the same order number, its
QUANTITY (PCS + PRS) and ORDER AMOUNT; the cover is the document's page and both figures must agree with
the printed totals. A long inline size header wraps (36 .. 45 / 46 47) and so do that item's quantities:
the second quantity row is the first text row under the line, mapped to the second header row.

Size cells: grid cells start at the SIZES header and advance PITCH points, so "XS-S S-M M-L" puts its
cells exactly where "XS S M" puts its own, and labels that fill their cell run together in the text
layer ("28x3429x34"). Words are read at x_tolerance=1, which splits those at their 1.4pt gap; each label
must sit in one cell, centred. Every quantity is mapped to the size header of its own item, within TOL
of a cell that carries a label, one quantity per cell. A label the platform cut short ("one s…") is
expanded only when exactly one known full label starts with it.

Numbers print with a space or a comma for thousands, and the text layer splits some of them ("1 231",
"9,8 76,500"): the pieces of one cell are joined only when they sit closer than GAP and read as one
number.

Gates: each line's sizes sum to its quantity; quantity x unit price = amount; each grid item's lines sum
to its Subtotal; the lines sum to the document Subtotal (units) and Grand total / TOTAL (value). A Grand
total other than the Subtotal is a discount or charge the reader does not spread over lines, and is
refused. The platform numbers its pages ("Order # 3999001 / CUSTOMER 1 / 2"); every grid page carries one
and every number from 1 to the last must be there. Pages after the Grand total are the document's terms;
a page before the first document belongs to none and is refused. An ORDER SHEET (the order as placed,
before the brand confirmed it) prints the same layout and is read, but refused.

The RRP is the amount after the SRP label (grid) or under the SRP header (inline), both accepted by
util.rrp_label; its currency is the document's (inline: the code printed over the SRP column).
"""
import math
import re

from .otb import GateError, NotLayout, collection_gender, gate_problems, xc  # noqa: F401  (GateError re-exported)
from ..util import rrp_header, rrp_label

X_TOL = 1
ROW_TOL = 1.5
TOL = 4.0
GAP = 3.5
PITCH = 22.4
FOOT = 30.0
TITLE = re.compile(r"\bORDER (CONFIRMATION|SHEET)\b")
ORDER_NO = re.compile(r"Order number #(\d{6,8})\b")
GRID = ("REFERENCE", "DESCRIPTION", "SIZES", "QUANTITY", "UNIT", "PRICE", "AMOUNT")
INLINE = ("STYLE", "REFERENCE", "VARIANT", "SRP", "SIZES", "QTY", "UNIT", "TOTAL")
NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?")
ISO = {"USD", "EUR", "JPY", "GBP", "AUD", "CAD", "HKD", "CNY", "CHF", "SGD", "KRW", "NZD", "AED", "SEK", "DKK"}
NAMES = {"DOLLAR": {"USD", "AUD", "CAD", "HKD", "SGD", "NZD"}, "YEN": {"JPY"}, "EURO": {"EUR"},
         "POUND": {"GBP"}, "YUAN": {"CNY"}, "FRANC": {"CHF"}, "WON": {"KRW"}, "DIRHAM": {"AED"}}
SYMBOLS = {"€": "EUR", "£": "GBP"}
SRP_SYMBOLS = dict(SYMBOLS, **{"$": "USD", "¥": "JPY"})   # a bare $ or ¥ on an SRP names USD or JPY only
FULL_SIZES = ("one size",)


def rows_of(words, tol=ROW_TOL):
    rows = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and w["top"] - rows[-1][0] <= tol:
            rows[-1][1].append(w)
        else:
            rows.append([w["top"], [w]])
    return [(t, sorted(r, key=lambda w: w["x0"])) for t, r in rows]


def flow(page):
    """Words at x_tolerance=1, each with `i`, its place in the order the page draws them."""
    words = page.extract_words(x_tolerance=X_TOL, use_text_flow=True)
    for i, w in enumerate(words):
        w["i"] = i
    return words


def text(row):
    return " ".join(w["text"] for w in row)


def clusters(words):
    """Words left to right, split where the gap is GAP or wider: the cells of one printed row."""
    out = []
    for w in sorted(words, key=lambda w: w["x0"]):
        if out and w["x0"] - out[-1][-1]["x1"] < GAP:
            out[-1].append(w)
        else:
            out.append([w])
    return out


def raw(cell):
    return "".join(w["text"] for w in cell)


def centre(cell):
    return (cell[0]["x0"] + cell[-1]["x1"]) / 2


def number(cell):
    """One printed number from the words of one cell ('1' '231', '9,8' '76,500', '$12,345.00', 'JPY' '9' ...).
    None when they do not read as one number."""
    s = re.sub(r"^(?:[A-Z]{3}|[$¥€£])", "", raw(cell))
    return float(s.replace(",", "")) if NUM.fullmatch(s) else None


def expand(label):
    if not label.endswith("…"):
        return label
    hits = [f for f in FULL_SIZES if f.casefold().startswith(label[:-1].casefold())]
    return hits[0] if len(hits) == 1 else label


def header_kind(row):
    t = tuple(w["text"] for w in row)
    return "grid" if t == GRID else "inline" if t == INLINE else None


def page_kind(t):
    if "PROFORMA INVOICE" in t and "EXPORT SALES CONTRACT" in t and ORDER_NO.search(t):
        return "cover"
    if TITLE.search(t) and ORDER_NO.search(t):
        return "head"
    return None


def footer(rows, height):
    """(order number, page i, of n) from the platform's page footer, or None when the page has none."""
    foot = sorted((w for top, r in rows if top > height - FOOT for w in r), key=lambda w: (w["top"], w["x0"]))
    m = re.match(r"Order # (\d+) /", " ".join(w["text"] for w in foot if w["x0"] < 300))
    n = re.fullmatch(r"(\d+)/(\d+)", "".join(w["text"] for w in foot if w["x0"] >= 300))
    return (m.group(1), int(n.group(1)), int(n.group(2))) if m and n else None


def parse(pdf_path):
    """Parse a P3M order-confirmation PDF. Returns (docs, problems)."""
    import pdfplumber

    docs, problems, d, loose = [], [], None, []
    with pdfplumber.open(pdf_path) as pdf:
        pages = [(pno, p.height, p.extract_text(x_tolerance=X_TOL) or "", rows_of(flow(p))) for pno, p in
                 enumerate(pdf.pages, 1)]
    for pno, height, t, rows in pages:
        kind = page_kind(t)
        num = ORDER_NO.search(t).group(1) if kind else None
        if kind == "cover":
            d = _new_doc(num, problems)
            docs.append(d)
            d["cover_pages"].append(pno)
            _cover(d, t)
            continue
        if kind == "head":
            if d is None or d["doc"] != num:
                d = _new_doc(num, problems)
                docs.append(d)
            elif d["kind"] is not None:
                problems.append(NotLayout(f"{num} p{pno}: the confirmation starts again; a PDF may carry an order once"))
                d["terms_pages"].append(pno)
                continue
            _footer(d, pno, rows, height)
            _head(d, pno, height, t, rows)
            continue
        if d is None:
            loose.append(pno)
            continue
        _footer(d, pno, rows, height)
        if d["_state"] == "done":
            d["terms_pages"].append(pno)
            if re.search(r"(?m)^(Variant|Ref\.|Subtotal|SUBTOTAL)\b", t):
                problems.append(f"{d['doc']} p{pno}: order rows after the Grand total")
            continue
        d["pages"].append(pno)
        _body(d, pno, height, rows, start=0)
    blank = [pno for pno, _, _, rows in pages if not rows]
    if docs:
        problems += [f"p{p}: belongs to no confirmation (it comes before the first one)" for p in loose if p not in blank]
        problems += [f"p{p}: no text layer (a scan?); this reader reads the PDF's text" for p in blank]
    elif pages and len(blank) == len(pages):
        problems.append("the PDF has no text layer (a scan?); this reader reads the PDF's text")
    for d in docs:
        problems += _finish(d)
    return docs, problems + gate_problems(docs)


def _new_doc(num, problems):
    return dict(doc=num, kind=None, layout=None, issuer=None, label=None, date=None, order_type=None,
                customer=None, currency=None, currency_name=None, price_list=None, incoterm=None, season=None,
                collection=None, line=None, gender=None, deliver_from=None, deliver_to=None, pages=[],
                cover_pages=[], terms_pages=[], lines=[], total_qty=None, gross=None, net=None, cover_qty=None,
                cover_value=None, commodity=None, _state="body", _stream=[], _footers=[], _problems=problems,
                _cols=None, _srp_cur=None, _adjust=[])


def _footer(d, pno, rows, height):
    f = footer(rows, height)
    if f:
        d["_footers"].append((pno,) + f)


def _cover(d, t):
    d["issuer"] = t.strip().splitlines()[0].strip()
    q = re.search(r"QUANTITY\s*:?\s*(\d[\d,]*)?\s*PCS\s*(\d[\d,]*)?\s*PRS", t)
    if q:
        d["cover_qty"] = sum(int(x.replace(",", "")) for x in q.groups() if x)
    a = re.search(r"ORDER AMOUNT\s*:?\s*[¥$€£]?\s*([\d,]+(?:\.\d\d)?)", t)
    d["cover_value"] = float(a.group(1).replace(",", "")) if a else None
    c = re.search(r"COMMODITY\s*:?\s*(.+)", t)
    d["commodity"] = c.group(1).strip() if c else None


def _head(d, pno, height, t, rows):
    problems = d["_problems"]
    d["kind"] = TITLE.search(t).group(0)
    d["pages"].append(pno)
    i_title = next(k for k, (_, r) in enumerate(rows) if TITLE.search(text(r)))
    left = next((r for _, r in rows[i_title:] if any(w["x1"] < 300 for w in r)), [])
    d["issuer"] = " ".join(w["text"] for w in left if w["x1"] < 300) or None
    right = {}
    for _, r in rows:
        m = re.match(r"(Order date|Order Date|Order type|Customer ref\.|Customer|Currency|Price list|Incoterm|"
                     r"Desired delivery|Line|Season)\s+(.+)", " ".join(w["text"] for w in r if w["x0"] >= 370))
        if m:
            right.setdefault(m.group(1).lower(), m.group(2).strip())
    d.update(date=right.get("order date"), order_type=right.get("order type"), customer=right.get("customer"),
             currency_name=right.get("currency"), price_list=right.get("price list"),
             incoterm=right.get("incoterm"), season=right.get("season"), line=right.get("line"))
    d["collection"] = d["season"]
    d["gender"] = collection_gender(d["line"])
    if right.get("desired delivery"):
        frm, _, to = right["desired delivery"].partition(" to ")
        d["deliver_from"], d["deliver_to"] = frm.strip(), to.strip() or None
    k = next((k for k, (_, r) in enumerate(rows) if header_kind(r)), None)
    if k is None:
        problems.append(f"{d['doc']} p{pno}: no order-line table header ({' '.join(GRID)}, or {' '.join(INLINE)}); "
                        "the layout changed")
        d["_state"] = "done"
        return
    d["layout"] = header_kind(rows[k][1])
    d["_cols"] = {w["text"]: w for w in rows[k][1]}
    grid = d["layout"] == "grid"
    _currency(d, rows[k + 1][1] if grid and k + 1 < len(rows) else rows[k - 1][1] if k else [], pno)
    _body(d, pno, height, rows, start=k + 1 + grid)


def _currency(d, row, pno):
    """The ISO code printed over (inline) or under (grid) the money columns, held to Currency and Price list."""
    problems, cols = d["_problems"], d["_cols"]
    found = {}
    for name in ("PRICE", "AMOUNT") if d["layout"] == "grid" else ("SRP", "UNIT", "TOTAL"):
        hit = [w["text"] for w in row if abs(xc(w) - xc(cols[name])) <= 12 or abs(w["x1"] - cols[name]["x1"]) <= 3]
        found[name] = hit[0] if len(hit) == 1 else None
    codes = set(found.values())
    if len(codes) != 1 or not codes <= ISO:
        problems.append(f"{d['doc']} p{pno}: no one currency code printed with the money columns "
                        f"({', '.join(f'{k} {v}' for k, v in found.items())})")
        return
    cur = d["currency"] = codes.pop()
    d["_srp_cur"] = found.get("SRP")
    listed = ISO & set(re.findall(r"[A-Z]{3}", d["price_list"] or ""))
    if listed and listed != {cur}:
        problems.append(f"{d['doc']}: Price list '{d['price_list']}' but the money columns are in {cur}")
    if cur not in NAMES.get((d["currency_name"] or "").upper(), ()):
        problems.append(f"{d['doc']}: Currency '{d['currency_name']}' does not name {cur}, the money columns' "
                        "currency")


def _body(d, pno, height, rows, start):
    """This page's table rows into the document's stream; the totals once the table ends."""
    if d["layout"] is None:
        return
    for top, row in rows[start:]:
        if top > height - FOOT or d["_state"] == "done":
            continue
        first = row[0]["text"]
        if d["_state"] == "body":
            if first in ("Subtotal", "SUBTOTAL") and row[0]["x0"] > 300:
                d["_state"] = "totals"
                nums = [number(c) for c in clusters(row[1:])]
                if len(nums) == 2 and None not in nums and nums[0].is_integer():
                    d["total_qty"], d["gross"] = int(nums[0]), nums[1]
                else:
                    d["_problems"].append(f"{d['doc']} p{pno}: unreadable document Subtotal '{text(row)}'")
            else:
                d["_stream"].append((pno, top, row))
            continue
        m = re.match(r"(Grand total|TOTAL)\b", text(row))
        if not m:
            d["_adjust"].append(text(row))
            continue
        cells = clusters(row[len(m.group(1).split()):])
        code = re.match(r"[A-Z]{3}", raw(cells[0])) if cells else None
        d["net"] = number(cells[0]) if len(cells) == 1 else None
        if d["net"] is None:
            d["_problems"].append(f"{d['doc']} p{pno}: unreadable {m.group(1)} '{text(row)}'")
        if code and code.group(0) != d["currency"]:
            d["_problems"].append(f"{d['doc']}: {m.group(1)} printed in {code.group(0)}, the money columns in "
                                  f"{d['currency']}")
        d["_state"] = "done"


def _finish(d):
    problems = []
    if d["kind"] is None:
        problems.append(f"{d['doc']}: a cover with no confirmation pages after it")
    if d["kind"] == "ORDER SHEET":
        problems.append(f"{d['doc']}: an ORDER SHEET (the order as placed, before the brand confirmed it), not an "
                        "ORDER CONFIRMATION")
    if d["layout"] == "grid":
        problems += _grid(d)
    elif d["layout"] == "inline":
        problems += _inline(d)
    if d["layout"] and d["_state"] != "done":
        problems.append(f"{d['doc']}: the table never reaches its Subtotal and Grand total (a page missing?)")
    if d["gross"] is not None and d["net"] is not None and abs(d["gross"] - d["net"]) > 0.005:
        problems.append(f"{d['doc']}: Grand total {d['net']:,.2f} differs from the Subtotal {d['gross']:,.2f} "
                        f"({'; '.join(d['_adjust']) or 'nothing printed between them'}); a discount or charge is "
                        "not spread over the lines")
    if d["cover_pages"]:
        for name, v, want in (("QUANTITY", d["cover_qty"], d["total_qty"]), ("ORDER AMOUNT", d["cover_value"], d["net"])):
            if v is None or want is None or abs(v - want) > 0.005:
                problems.append(NotLayout(f"{d['doc']}: the cover's {name} {v} does not match the printed total {want}"))
    problems += _pages(d)
    for k in [k for k in d if k.startswith("_")]:
        d.pop(k)
    return problems


def _pages(d):
    """Every platform page number from 1 to the last, once each, all naming this order."""
    out, seen = [], {}
    for pno, num, i, n in d["_footers"]:
        if num != d["doc"]:
            out.append(f"{d['doc']} p{pno}: the page footer names order {num}")
        seen.setdefault(n, []).append(i)
    if d["layout"] == "grid":
        bare = sorted(set(d["pages"]) - {f[0] for f in d["_footers"]})
        if bare:
            out.append(f"{d['doc']}: page(s) {bare} carry no platform page number")
    for n, got in seen.items():
        if len(seen) > 1 or sorted(got) != list(range(1, n + 1)):
            missing = sorted(set(range(1, n + 1)) - set(got))
            out.append(f"{d['doc']}: platform pages {sorted(got)} of {n}"
                       + (f"; page(s) {missing} missing" if missing else ""))
    return out


def _money(row, cols, names, centred):
    """A quantity row's cells assigned to the named columns, right-aligned (grid) or centred (inline);
    returns ({name: cell}, the other cells)."""
    got, rest = {}, []
    for c in clusters(row):
        name = next((nm for nm in names if (abs(centre(c) - xc(cols[nm])) <= 6 if centred
                                            else abs(c[-1]["x1"] - cols[nm]["x1"]) <= 2.5)), None)
        if name and name not in got:
            got[name] = c
        else:
            rest.append(c)
    return got, rest


def _map(tag, cells, where, problems):
    """{label: qty} for quantity cells; where(x) -> (label, distance) of the size cell at x-centre x."""
    hit = {}
    for c in cells:
        q, x = number(c), centre(c)
        if q is None or not q.is_integer():
            problems.append(f"{tag}: quantity cell '{raw(c)}' is not a whole number")
            continue
        label, dist = where(x)
        if label is None or dist > TOL:
            problems.append(f"{tag}: qty {int(q)}@{x:.0f} is {dist:.1f}pt from the nearest size cell {label}")
            continue
        if label in hit:
            problems.append(f"{tag}: two quantities under size {label}")
            continue
        hit[label] = int(q)
    return hit


def _line(d, item, tag, pno, style, colour, sizes, run, cells, extra, problems):
    vals = {}
    for name, c in zip(("qty", "price", "line_value"), cells):
        vals[name] = number(c)
        if vals[name] is None:
            problems.append(f"{tag}: unreadable {name} '{raw(c)}'")
    if vals["qty"] is not None and not vals["qty"].is_integer():
        problems.append(f"{tag}: quantity {vals['qty']} is not a whole number")
    if None in vals.values() or not vals["qty"].is_integer():
        return None
    line = dict(line_no=len(d["lines"]) + 1, style=style, fabric=None, colour=colour, colour_code=None,
                colour_name=colour, sizes={s: q for s, q in sizes.items() if q}, size_run=run, qty=int(vals["qty"]),
                price=vals["price"], line_value=vals["line_value"], page=pno, deliver_from=d["deliver_from"],
                deliver_to=d["deliver_to"], description=" ".join(item["name"]) or None, **extra)
    d["lines"].append(line)
    item["lines"].append(line)
    return line


def _grid(d):
    """Items of the grid layout: name, labelled fields, one or more Variant blocks, Subtotal."""
    problems, cols, stream = [], d["_cols"], d["_stream"]
    ref_x, desc_x, sizes_x = cols["REFERENCE"]["x0"], cols["DESCRIPTION"]["x0"], cols["SIZES"]["x0"]
    item, i = None, 0

    def is_qty_row(row):
        return all(w["x0"] >= sizes_x - 8 and re.fullmatch(r"[\d,.]+", w["text"]) for w in row)

    def at_ref(row, word):
        return row[0]["text"] == word and abs(row[0]["x0"] - ref_x) < 6

    while i < len(stream):
        pno, top, row = stream[i]
        if at_ref(row, "Subtotal"):
            if item is None:
                problems.append(f"{d['doc']} p{pno}: Subtotal with no item above it")
            else:
                problems += _item_subtotal(d, item, pno, row)
            item, i = None, i + 1
            continue
        if item is None:
            item = dict(name=[], meta={}, labels=None, lines=[], fields=None, variants=0)
        if at_ref(row, "Variant"):
            j = i + 1
            while j < len(stream) and not (at_ref(stream[j][2], "Variant") or at_ref(stream[j][2], "Subtotal")):
                j += 1
            item["variants"] += 1
            problems += _variant(d, item, pno, row, stream[i + 1:j], is_qty_row)
            i = j
            continue
        if item["variants"]:
            problems.append(f"{d['doc']} p{pno}: unexpected row '{text(row)[:50]}' after the item's lines")
        elif is_qty_row(row):
            problems.append(f"{d['doc']} p{pno}: quantities '{text(row)}' outside any Variant block")
        elif all(w["bottom"] - w["top"] < 5.2 for w in row):
            item["fields"] = [(w["text"], w["x0"]) for w in row]
        elif item["fields"]:
            for w in row:
                lab = max((f for f in item["fields"] if f[1] <= w["x0"] + 1), key=lambda f: f[1], default=None)
                if lab is None:
                    problems.append(f"{d['doc']} p{pno}: '{w['text']}' under no field label")
                else:
                    item["meta"].setdefault(lab[0], []).append(w["text"])
        elif abs(row[0]["x0"] - desc_x) < 4:
            item["name"].append(text(row))
        else:
            problems.append(f"{d['doc']} p{pno}: unexpected row '{text(row)[:50]}' in an item heading")
        i += 1
    if item is not None:
        problems.append(f"{d['doc']}: the last item ({' '.join(item['name'])}) has no Subtotal")
    return problems


def _grid_labels(d, tag, row, problems):
    """{cell index: label} for a Variant row: each label inside one PITCH-wide cell from SIZES, centred."""
    origin, cells = d["_cols"]["SIZES"]["x0"], {}
    for w in row:
        k = math.floor((xc(w) - origin) / PITCH)
        if k < 0 or w["x0"] < origin + k * PITCH - 1.5 or w["x1"] > origin + (k + 1) * PITCH + 1.5:
            problems.append(f"{tag}: size header word '{w['text']}'@{w['x0']:.0f} is not inside one size cell")
            continue
        cells.setdefault(k, []).append(w)
    out = {}
    for k, ws in sorted(cells.items()):
        if abs(centre(ws) - (origin + (k + 0.5) * PITCH)) > 2:
            problems.append(f"{tag}: size label '{text(ws)}' is not centred in its cell (layout changed)")
        out[k] = expand(text(ws))
    return out


def _variant(d, item, pno, vrow, rows, is_qty_row):
    """The colour lines under one Variant row: colour, quantity row, Ref., composition, SRP."""
    problems, cols = [], d["_cols"]
    sizes_x, origin = cols["SIZES"]["x0"], cols["SIZES"]["x0"]
    name = " ".join(item["name"])
    stray = [w["text"] for w in vrow[1:] if w["x0"] < sizes_x - 8]
    if stray:
        problems.append(f"{d['doc']} p{pno} {name}: {stray} between Variant and the size header")
    labels = _grid_labels(d, f"{d['doc']} p{pno} {name}", [w for w in vrow[1:] if w["x0"] >= sizes_x - 8], problems)
    qrows = [k for k, (_, _, r) in enumerate(rows) if is_qty_row(r)]
    if not qrows:
        return problems + [f"{d['doc']} p{pno} {name}: a Variant row with no quantities under it"]
    starts = []
    for k in qrows:
        p, top, r = rows[k]
        c = [m for m, (pp, t, rr) in enumerate(rows) if pp == p and top - 6 <= t < top and not is_qty_row(rr)]
        starts.append(c[0] if c else k)
        if not c:
            problems.append(f"{d['doc']} p{p} {name}: quantities '{text(r)}' with no colour beside them")
    if starts[0] != 0:
        problems.append(f"{d['doc']} p{rows[0][0]} {name}: '{text(rows[0][2])[:50]}' between Variant and the first "
                        "colour")

    def where(x):
        k = math.floor((x - origin) / PITCH)
        return labels.get(k), abs(x - (origin + (k + 0.5) * PITCH))

    for n, k in enumerate(qrows):
        p, _, qrow = rows[k]
        end = starts[n + 1] if n + 1 < len(qrows) else len(rows)
        colour = " ".join(text(r) for _, _, r in rows[starts[n]:k]) or None
        after = [r for _, _, r in rows[k + 1:end]]
        tag = f"{d['doc']} p{p} line {len(d['lines']) + 1} {name} {colour}"
        if not after or after[0][0]["text"] != "Ref." or sum(r[0]["text"] == "Ref." for r in after) != 1:
            problems.append(f"{tag}: expected one Ref. row right under the quantities")
        style = " ".join(w["text"] for w in after[0][1:]) if after and after[0][0]["text"] == "Ref." else ""
        if not style:
            problems.append(NotLayout(f"{tag}: the Ref. is blank, so the line has no style code to join on"))
        srp = [r for r in after[1:] if rrp_label(r[0]["text"])]
        comp = [text(r) for r in after[1:] if not rrp_label(r[0]["text"])]
        rrp = None
        if len(srp) > 1:
            problems.append(f"{tag}: {len(srp)} SRP rows")
        elif srp:
            rrp = number(srp[0][1:])
            named = re.findall(r"[A-Z]{3}|[€£$¥]", raw(srp[0][1:]))
            if rrp is None or any(SRP_SYMBOLS.get(x, x) != d["currency"] for x in named):
                problems.append(f"{tag}: unreadable SRP '{text(srp[0])}'")
        spill = [w["text"] for r in after for w in r if w["x0"] >= sizes_x - 8]
        if spill:
            problems.append(f"{tag}: {spill} right of the colour's text rows")
        got, rest = _money(qrow, cols, ("QUANTITY", "PRICE", "AMOUNT"), centred=False)
        for nm in ("QUANTITY", "PRICE", "AMOUNT"):
            if nm not in got:
                problems.append(f"{tag}: no {nm} in the quantity row")
        late = [raw(c) for c in rest if c[-1]["x1"] >= cols["QUANTITY"]["x0"]]
        if late:
            problems.append(f"{tag}: {late} in no column")
        sizes = _map(tag, [c for c in rest if c[-1]["x1"] < cols["QUANTITY"]["x0"]], where, problems)
        if len(got) < 3:
            continue
        meta = {k: " ".join(v) for k, v in item["meta"].items()}
        _line(d, item, tag, p, style or None, colour, sizes, list(labels.values()),
              (got["QUANTITY"], got["PRICE"], got["AMOUNT"]),
              dict(product_class=meta.get("CATEGORY"), category=meta.get("CATEGORY"), made_in=meta.get("ORIGIN"),
                   item_collection=meta.get("COLLECTION"), meta=meta, composition=" ".join(comp) or None,
                   rrp=rrp, msrp=rrp, rrp_currency=d["currency"] if rrp is not None else None,
                   rrp_found=f"SRP {raw(srp[0][1:])}" if rrp is not None else None), problems)
    return problems


def _item_subtotal(d, item, pno, row):
    nums = [number(c) for c in clusters(row[1:])]
    name = " ".join(item["name"])
    if len(nums) != 2 or None in nums:
        return [f"{d['doc']} p{pno}: unreadable item Subtotal '{text(row)}' ({name})"]
    q, v = sum(l["qty"] for l in item["lines"]), sum(l["line_value"] for l in item["lines"])
    if q != nums[0] or abs(v - nums[1]) > 0.02:
        return [NotLayout(f"{d['doc']} p{pno}: {name}: lines sum to {q} units / {v:,.2f}, its Subtotal prints "
                          f"{int(nums[0])} / {nums[1]:,.2f}")]
    return []


def _inline(d):
    """Items of SOSU's inline layout: name + size header row (wrapping onto label-only rows), then one row per
    colour with its text rows under it."""
    problems, cols, stream = [], d["_cols"], d["_stream"]
    style_x, ref_x, sizes_x, qty_x0 = (cols["STYLE"]["x0"], cols["REFERENCE"]["x0"], cols["SIZES"]["x0"],
                                       cols["QTY"]["x0"])
    item, line, i = None, None, 0

    def zone(row):
        return [w for w in row if sizes_x - 4 <= w["x0"] < qty_x0 - 8]

    while i < len(stream):
        pno, top, row = stream[i]
        first = row[0]
        right = [w for w in row if w["x0"] >= qty_x0 - 8]
        i += 1
        if abs(first["x0"] - style_x) < 3:
            if right:
                problems.append(f"{d['doc']} p{pno}: amounts on an item row '{text(row)[:50]}'")
            name, labels = _item_row(row, sizes_x)
            if [w for w in labels if w not in zone(row)] or any(a["x1"] > b["x0"] for a, b in zip(labels, labels[1:])):
                problems.append(f"{d['doc']} p{pno}: no size header can be told from the item name '{text(row)[:60]}'")
            item = dict(name=[w["text"] for w in name], labels=[clusters(labels)], lines=[])
            line = None
            continue
        if item is not None and not item["lines"] and row == zone(row):
            item["labels"].append(clusters(row))
            continue
        if abs(first["x0"] - ref_x) < 3 and right:
            if item is None:
                problems.append(f"{d['doc']} p{pno}: a line '{text(row)[:40]}' before any item row")
                continue
            nxt = stream[i][2] if (len(item["labels"]) > 1 and i < len(stream) and stream[i][0] == pno
                                   and abs(stream[i][2][0]["x0"] - style_x) >= 3
                                   and not any(w["x0"] >= qty_x0 - 8 for w in stream[i][2])) else None
            line = _inline_line(d, item, pno, row, nxt, problems)
            if nxt is not None:
                rest = [w for w in nxt if w not in zone(nxt) or not w["text"].isdigit()]
                if rest:
                    stream[i] = (pno, stream[i][1], rest)
                else:
                    i += 1
            continue
        if abs(first["x0"] - ref_x) < 3 and line is not None:
            o = re.fullmatch(r"Origin:\s*(.+)", text(row))
            if o:
                line["made_in"] = o.group(1)
            else:
                line["composition"] = " ".join(x for x in (line["composition"], text(row)) if x)
            continue
        problems.append(f"{d['doc']} p{pno}: unexpected row '{text(row)[:50]}'")
    return problems


def _item_row(row, sizes_x):
    """(name words, size label words) of an inline item row. A name too long for its cell runs on under the
    labels; it is drawn before them, so the labels start where the drawing order jumps back left."""
    drawn = sorted(row, key=lambda w: w["i"])
    jump = next((k for k in range(1, len(drawn)) if drawn[k]["x0"] < drawn[k - 1]["x1"] - 1), None)
    if jump is not None:
        return drawn[:jump], sorted(drawn[jump:], key=lambda w: w["x0"])
    return [w for w in row if w["x0"] < sizes_x - 4], [w for w in row if w["x0"] >= sizes_x - 4]


def _inline_line(d, item, pno, row, nxt, problems):
    cols = d["_cols"]
    var_x, sizes_x = cols["VARIANT"]["x0"], cols["SIZES"]["x0"]
    left = [w for w in row if w["x0"] < sizes_x - 4]
    style = " ".join(w["text"] for w in left if w["x0"] < var_x - 2)
    mid = clusters([w for w in left if w["x0"] >= var_x - 2])
    srp = mid[-1] if mid and number(mid[-1]) is not None and mid[-1][-1]["x1"] > cols["SRP"]["x0"] else None
    colour = " ".join(w["text"] for c in mid if c is not srp for w in c) or None
    tag = f"{d['doc']} p{pno} line {len(d['lines']) + 1} {style} {colour}"
    got, rest = _money([w for w in row if w["x0"] >= sizes_x - 4], cols, ("QTY", "UNIT", "TOTAL"), centred=True)
    for nm in ("QTY", "UNIT", "TOTAL"):
        if nm not in got:
            problems.append(f"{tag}: no {nm}")
    late = [raw(c) for c in rest if c[-1]["x1"] >= cols["QTY"]["x0"] - 2]
    if late:
        problems.append(f"{tag}: {late} in no column")
    sizes = {}
    rows = [[c for c in rest if c[-1]["x1"] < cols["QTY"]["x0"] - 2]]
    if nxt is not None:
        rows.append(clusters([w for w in nxt if sizes_x - 4 <= w["x0"] < cols["QTY"]["x0"] - 8 and w["text"].isdigit()]))
    for cells, labels in zip(rows, item["labels"]):
        named = [(" ".join(w["text"] for w in c), centre(c)) for c in labels]

        def where(x, named=named):
            return min(((s, abs(x - lx)) for s, lx in named), key=lambda t: t[1], default=(None, math.inf))
        for s, q in _map(tag, cells, where, problems).items():
            if s in sizes:
                problems.append(f"{tag}: size {s} printed twice in the header")
            sizes[s] = q
    if srp is None:
        problems.append(f"{tag}: no SRP")
    if not style:
        problems.append(f"{tag}: no style reference")
    if len(got) < 3 or not style:
        return None
    rrp = number(srp) if srp and rrp_header([cols["SRP"]["text"]]) else None
    run = [" ".join(w["text"] for w in c) for labels in item["labels"] for c in labels]
    return _line(d, item, tag, pno, style, colour, sizes, run, (got["QTY"], got["UNIT"], got["TOTAL"]),
                 dict(product_class=None, category=None, made_in=None, item_collection=None, meta={},
                      composition=None, rrp=rrp, msrp=rrp, rrp_currency=d["_srp_cur"] if rrp is not None else None,
                      rrp_found=f"SRP {d['_srp_cur']} {raw(srp)}" if rrp is not None else None), problems)


def doc_label(d):
    """The platform prints no label apart from the issuing company, so an AI read of a P3M brand gets none either."""
    return None


def parse_checked(pdf_path):
    docs, problems = parse(pdf_path)
    if problems:
        raise GateError(problems)
    if not docs:
        raise GateError(["no order confirmations found"])
    return docs


def by_size_rows(docs):
    out = []
    for d in docs:
        cur = d.get("currency") or "?"
        for l in d["lines"]:
            for size, q in l["sizes"].items():
                out.append({"Order confirmation": d["doc"], "Collection": d["collection"],
                            "Line no.": l["line_no"], "Style": l["style"], "Colour": l["colour"],
                            "Description": l["description"], "Size": size, "Qty": q,
                            f"Price {cur}": l["price"], f"Value {cur}": q * l["price"]})
    return out


def totals(docs):
    lines = [l for d in docs for l in d["lines"]]
    return dict(docs=len(docs), lines=len(lines),
                units=sum(l["qty"] for l in lines),
                value=round(sum(l["line_value"] for l in lines), 2),
                printed_units=sum(d.get("total_qty") or 0 for d in docs),
                printed_value=round(sum(d.get("net") or 0 for d in docs), 2))
