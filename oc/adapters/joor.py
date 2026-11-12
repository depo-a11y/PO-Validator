"""JOOR order print adapter: one reader for every brand that sends Marais its JOOR order as the confirmation.

JOOR prints every brand's order the same way. Each page is headed by the retailer code, the order status
(Pending, Approved), "Page k of N", "PO#: 99000001" and four columns: Order, Brand, Shipping and Billing
Information. The body is one block per style:

    TEST JACKET                                                           (name)
    Style #ZZ-A1 | TEST COLLECTION       Wholesale: EUR 100.13  Sugg. Retail: EUR 250.41
    Silhouette: JACKET                                                    (attribute rows, optional)
          Colors                                                Total
                         XS   S   M   L  XL  Qty
          BLACK Z01            1   2   1       4      EUR 400.52
          WHITE Z02        1   1               2      EUR 200.26
                           1   2   2   1   0   6  Total EUR 600.78    (a style with two or more colours)

then "Total Quantity", "Subtotal" and "Grand Total" after the last style, and the brand's terms on further
pages of the same order (a "Cancelled Styles" list can come first; it is not read as order lines). A style
whose sizes do not fit prints "Colors Units Total" instead, with a "Sizes" row and a "Qty" row under each
colour. One PDF can carry several orders, each numbered "Page 1 of N" onwards with its own totals; a page
outside that numbering, or a gap in it, is refused.

The size header changes from style to style and is laid out from a fixed origin at a fixed pitch, so the
same x is XS on one style and 44 on the next. Each colour's quantities are read against its own style's
header only, every quantity within TOL of exactly one header cell and no cell twice. A header can wrap
("One" over "Size"): words stacked in one column are one cell. Rows are grouped into blocks by vertical
gap, because a colour's name, quantities and amount sit at slightly different heights when one of them
wraps (the currency over the amount, a long colour name over two rows). A style with two or more colours
prints a Total row per size; it must equal the sum of its colours, size by size, which is the only check
here that sees a quantity read under the wrong size. A one-colour style has no such row.

The colour cell is the colour name, then its code when the brand prints one ("BLACK Z01", "ZZA NOIR ZZA");
a last word with a digit in it, or repeating the first, is the code (`colour`, `colour_code`), the rest is
`colour_name`. With no code ("CHARCOAL") `colour` is the name.

Identity. JOOR prints the brand as a logo, which has no text. What the text names is the brand's contact
in Brand Information, so `issuer` is the domain of that contact's email (brand.com), which for an order
placed through an agency is the agency's. `label` is None: the label is not in the text layer.

The Sugg. Retail is an RRP (util.rrp_label) in the currency printed beside it; 0.00 is no RRP. A wholesale
under 1.00 (JOOR prints re-orders at 0.01) is refused: it is no price, and the draft writes the OC's price as
the cost of every size. A Grand Total
other than the Subtotal is refused, not spread over the lines. The status is read per document: Pending
(not yet approved by the brand in JOOR) is read and reported by `reader_warnings`; any status but Pending and
Approved is refused.
"""
import re

from .otb import GateError, NotLayout, collection_gender, gate_problems, xc  # noqa: F401  (GateError re-exported)
from ..util import rrp_label

TOL = 4.0
ROW_TOL = 3.0
GAP = 11.0       # rows closer than this are one block (a colour's wrapped cells, a wrapped header, a comment)
HEAD_GAP = 11.0  # the page head's rows are 7pt apart; the body starts further down
LEFT = 140.0     # names, attribute rows and totals start left of this; the colour table right of it
COLUMNS = ("Order", "Brand", "Shipping", "Billing")
HEADINGS = "Order Information Brand Information Shipping Information Billing Information"
MONEY = re.compile(r"^\d{1,3}(?:,\d{3})*\.\d\d$")
CUR = re.compile(r"^[A-Z]{3}$")
EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
PAGE = re.compile(r"\bPage (\d+) of (\d+)\b")
STATUSES = ("Pending", "Approved")
PENDING_CHECK = "Order is approved by the brand in JOOR"


def rows_of(words, tol=ROW_TOL):
    rows = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and w["top"] - rows[-1][0] <= tol:
            rows[-1][1].append(w)
        else:
            rows.append([w["top"], [w]])
    return [(t, sorted(r, key=lambda w: w["x0"])) for t, r in rows]


def text(row):
    return " ".join(w["text"] for w in row)


def money(s):
    return float(s.replace(",", ""))


def col_text(chars, lo, hi):
    """The text of the chars whose x0 is in [lo, hi), a line per baseline: a word run into the next column
    ('contact@brand.comTEST') is cut at the column edge."""
    lines = {}
    for c in chars:
        if lo <= c["x0"] < hi:
            lines.setdefault(round(c["top"]), []).append(c)
    out = []
    for _, cs in sorted(lines.items()):
        out.append(" ".join("".join(c["text"] for c in sorted(cs, key=lambda c: c["x0"])).split()))
    return [s for s in out if s]


def page_head(rows, chars):
    """The page's JOOR head, or None when the page has none. -> dict(page, of, po, doc, status, retailer,
    end, columns) where `end` is the top of the head's last row."""
    i = next((k for k, (_, r) in enumerate(rows) if text(r) == HEADINGS), None)
    if i is None:
        return None
    above = rows[:i]
    pg = next(((k, PAGE.search(text(r))) for k, (_, r) in enumerate(above) if PAGE.search(text(r))), None)
    po_row = next((r for _, r in above if any(w["text"] == "PO#:" for w in r)), None)
    if pg is None or po_row is None:
        return None
    k, m = pg
    status_row = above[k + 1][1] if k + 1 < len(above) else []
    po_x = next(w["x1"] for w in po_row if w["text"] == "PO#:")
    po = " ".join(w["text"] for w in po_row if w["x0"] > po_x)
    num = re.search(r"(\d{6,})$", po)
    end = rows[i][0]
    for top, _ in rows[i + 1:]:
        if top - end > HEAD_GAP:
            break
        end = top
    heads = {w["text"]: w["x0"] for w in rows[i][1] if w["text"] in COLUMNS}
    bounds = [heads[c] for c in COLUMNS] + [10_000]
    block = [c for c in chars if rows[i][0] + ROW_TOL < c["top"] <= end + ROW_TOL]
    columns = {name: col_text(block, bounds[n], bounds[n + 1]) for n, name in enumerate(COLUMNS)}
    return dict(page=int(m.group(1)), of=int(m.group(2)), po=po, doc=num.group(1) if num else None,
                status=" ".join(w["text"] for w in status_row if w["x0"] > 300) or None,
                retailer=" ".join(w["text"] for w in status_row if w["x1"] < 300) or None,
                end=end, columns=columns)


def _field(lines, label):
    """'Created: 01/02/2099' -> '01/02/2099'; continuation lines without a label are joined to it."""
    out, on = [], False
    for s in lines:
        m = re.match(r"([A-Z][A-Za-z ]+):\s*(.*)", s)
        if m:
            on = m.group(1) == label
            if on:
                out.append(m.group(2))
        elif on:
            out.append(s)
    return " ".join(out).strip() or None


def _new_doc(head):
    order, brand = head["columns"]["Order"], head["columns"]["Brand"]
    emails = [m.group(1).lower() for s in brand for m in EMAIL.finditer(s)]
    created = _field(order, "Created")
    return dict(doc=head["doc"], po=head["po"], status=head["status"], retailer=head["retailer"],
                issuer=emails[0] if emails else None, label=None, brand_contact=" ".join(brand) or None,
                date=created, created=created, start_ship=_field(order, "Start Ship"),
                complete_ship=_field(order, "Complete Ship"), terms=_field(order, "Terms"),
                ship_with=_field(order, "Ship With"), order_type=None, currency=None, collection=None,
                gender=None, total_qty=None, gross=None, net=None, comments=None, cancelled=[], of=head["of"],
                pages=[], lines=[], _stream=[], _seen=[], _done=False)


def parse(pdf_path):
    """Parse a JOOR order print. Returns (docs, problems)."""
    import pdfplumber

    docs, problems, d, foreign, words_seen = [], [], None, [], 0
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            words = page.extract_words()
            words_seen += len(words)
            rows = rows_of(words)
            head = page_head(rows, page.chars)
            if head is None:
                foreign.append(pno)
                d = None
                continue
            if head["doc"] is None:
                problems.append(f"p{pno}: no order number after PO# ('{head['po']}')")
                d = None
                continue
            if d is None or d["doc"] != head["doc"] or head["page"] == 1:
                if head["page"] != 1:
                    problems.append(f"{head['doc']} p{pno}: starts at Page {head['page']} of {head['of']}; "
                                    f"page(s) 1-{head['page'] - 1} of that order are not in the PDF")
                if any(x["doc"] == head["doc"] for x in docs):
                    problems.append(NotLayout(f"{head['doc']} p{pno}: this order is printed twice in the PDF"))
                d = _new_doc(head)
                docs.append(d)
            elif head["page"] != d["_seen"][-1] + 1 or head["of"] != d["of"]:
                problems.append(f"{d['doc']} p{pno}: Page {head['page']} of {head['of']} follows Page "
                                f"{d['_seen'][-1]} of {d['of']}; a page of the order is missing or out of order")
            d["_seen"].append(head["page"])
            d["pages"].append(pno)
            if head["status"] != d["status"]:
                problems.append(f"{d['doc']} p{pno}: status '{head['status']}', page 1 says '{d['status']}'")
            body = [(pno, top, r) for top, r in rows if top > head["end"]]
            d["_stream"].append(body)
    if not docs:
        if not words_seen:
            return [], ["the PDF has no text layer (a scan?); this reader reads printed text by position"]
        return [], []
    problems += [f"p{p}: no JOOR page head (Page k of N, PO#, Order/Brand/Shipping/Billing Information); "
                 "the page belongs to no order" for p in foreign]
    for d in docs:
        seen = d.pop("_seen")
        if seen[-1] != d["of"]:
            problems.append(f"{d['doc']}: the PDF stops at Page {seen[-1]} of {d['of']}; "
                            f"page(s) {seen[-1] + 1}-{d['of']} are missing")
        if d["status"] not in STATUSES:
            problems.append(NotLayout(f"{d['doc']}: JOOR status '{d['status']}' is neither Pending nor Approved"))
        if d["issuer"] is None:
            problems.append(f"{d['doc']}: no email in Brand Information, so the brand cannot be checked")
        problems += _read(d, d.pop("_stream"))
        d.pop("_done")
        d.pop("_cancelled", None)
        colls = list(dict.fromkeys(l["line_collection"] for l in d["lines"] if l["line_collection"]))
        d["collection"] = "; ".join(colls) or None
        genders = {collection_gender(c) for c in colls}
        d["gender"] = genders.pop() if len(genders) == 1 else None
        if d["net"] is not None and d["gross"] is not None and abs(d["net"] - d["gross"]) > 0.005:
            problems.append(f"{d['doc']}: Grand Total {d['currency']} {d['net']:,.2f} is not the Subtotal "
                            f"{d['gross']:,.2f} (a discount or charge); it is not applied to any line")
    return docs, problems + gate_problems(docs)


def blocks_of(pages):
    """[(pno, [rows])]: the body's rows grouped by vertical gap, never across a page."""
    out = []
    for body in pages:
        last = None
        for pno, top, row in body:
            if last is None or top - last > GAP:
                out.append((pno, []))
            out[-1][1].append((top, row))
            last = top
    return out


def is_style_row(row):
    return len(row) > 1 and row[0]["text"] == "Style" and row[1]["text"].startswith("#") and row[0]["x0"] < LEFT


def _read(d, pages):
    problems, state, name, style = [], "names", [], None
    for pno, rows in blocks_of(pages):
        ws = [w for _, r in rows for w in r]
        t = " ".join(text(r) for _, r in rows)
        tag = f"{d['doc']} p{pno}"
        if d["_done"]:
            if t.startswith("Cancelled Styles:"):
                d["_cancelled"] = True
            for _, r in rows:
                if is_style_row(r) and d.get("_cancelled"):
                    d["cancelled"].append(r[1]["text"][1:])
                elif is_style_row(r):
                    problems.append(f"{tag}: a style after the order's Grand Total")
            continue
        if re.search(r"\b(Total Quantity|Subtotal|Grand Total):", t):
            if style:
                problems += _close(d, style)
                style = None
            problems += _totals(d, tag, rows)
            state = "totals"
            continue
        if state == "totals":
            if all(w["x1"] < 340 for w in ws):
                d["comments"] = " ".join(x for x in (d["comments"], t) if x)
                continue
            problems.append(f"{tag}: unexpected '{t[:50]}' between the order totals")
            continue
        k = next((k for k, (_, r) in enumerate(rows) if is_style_row(r)), None)
        if k is not None:
            if style:
                problems += _close(d, style)
            style, probs = _style(d, tag, rows[k:], name + [text(r) for _, r in rows[:k]])
            problems += probs
            name, state = [], "attrs"
            continue
        if state == "names" and not d["lines"] and style is None and re.match(r"Order Type:", t) \
                and all(w["x0"] > 300 for w in ws):
            d["order_type"] = t.split(":", 1)[1].strip()
            continue
        if all(w["x0"] < LEFT for w in (r[0] for _, r in rows)) and not any(w["text"] == "Colors" for w in ws):
            if state == "attrs":
                for _, r in rows:
                    m = re.match(r"([A-Za-z][A-Za-z -]*?):\s*(.*)", text(r))
                    if not m:
                        problems.append(f"{tag} {style['style']}: '{text(r)[:50]}' under the style is not "
                                        "'Name: value'")
                        continue
                    style["attributes"][m.group(1)] = m.group(2)
                continue
            if style:
                problems += _close(d, style)
                style = None
            state = "names"
            name.append(t)
            continue
        if style is None:
            problems.append(f"{tag}: '{t[:60]}' is outside any style")
            continue
        if rows[0][1][0]["text"] == "Comment" and rows[0][1][0]["x0"] >= LEFT:
            style["comment"] = " ".join(x for x in (style.get("comment"), t.split(" ", 1)[-1]) if x)
            continue
        if any(w["text"] == "Colors" for w in ws):
            problems += _header(style, tag, rows)
            state = "table"
            continue
        if state != "table":
            problems.append(f"{tag} {style['style']}: '{t[:60]}' before the style's Colors header")
            continue
        problems += _entry(d, style, tag, pno, rows)
    if style:
        problems += _close(d, style)
    return problems


def _style(d, tag, rows, name):
    """The 'Style #CODE | collection  Wholesale: CUR 1.00  Sugg. Retail: CUR 2.00' block (Wholesale can wrap
    onto the next row)."""
    toks = [x for _, r in rows for w in r for x in re.split(r"(?<=\S)(?=Wholesale:)", w["text"])]
    code = toks[1][1:]
    tag = f"{tag} {code}"
    problems = []
    wi = next((k for k, w in enumerate(toks) if w == "Wholesale:"), None)
    bar = next((k for k, w in enumerate(toks) if w == "|"), None)
    coll = " ".join(toks[bar + 1:wi]) or None if bar is not None and wi is not None else None
    price = cur = rrp = rrp_cur = rrp_found = None
    if wi is None or wi + 2 >= len(toks) or not CUR.match(toks[wi + 1]) or not MONEY.match(toks[wi + 2]):
        problems.append(f"{tag}: no 'Wholesale: CUR 0.00'")
    else:
        cur, price = toks[wi + 1], money(toks[wi + 2])
        if price < 1:
            problems.append(NotLayout(f"{tag}: wholesale {cur} {price:,.2f} is a placeholder, not a price; the draft "
                                      "would write it as the cost of every size"))
        rest = toks[wi + 3:]
        k = next((k for k, w in enumerate(rest) if rrp_label(w)), None)
        if k is not None:
            after = [w for w in rest[k:] if not rrp_label(w)]
            if len(after) == 2 and CUR.match(after[0]) and MONEY.match(after[1]):
                label = " ".join(w for w in rest[k:] if rrp_label(w))
                if money(after[1]):
                    rrp, rrp_cur, rrp_found = money(after[1]), after[0], f"{label} {after[0]} {after[1]}"
            else:
                problems.append(f"{tag}: unreadable retail price '{' '.join(rest[k:])}'")
        elif rest:
            problems.append(f"{tag}: '{' '.join(rest)}' after the wholesale price")
    if cur and d["currency"] is None:
        d["currency"] = cur
    elif cur and cur != d["currency"]:
        problems.append(f"{tag}: wholesale in {cur}, the order is in {d['currency']}")
    return dict(style=code, tag=tag, name=" ".join(name) or None, line_collection=coll, price=price, currency=cur,
                rrp=rrp, rrp_currency=rrp_cur, rrp_found=rrp_found, attributes={}, header=None, colours=[],
                subtotals=[], wide=None), problems


def _cells(words):
    """Header words -> [(label, x-centre, x0, x1)]; words stacked in one column ('One' over 'Size') are one."""
    cells = []
    for w in sorted(words, key=lambda w: (w["x0"], w["top"])):
        hit = next((c for c in cells if w["x0"] < c[3] and w["x1"] > c[2]), None)
        if hit:
            hit[4].append(w)
            hit[2], hit[3] = min(hit[2], w["x0"]), max(hit[3], w["x1"])
        else:
            cells.append([None, None, w["x0"], w["x1"], [w]])
    return [(" ".join(x["text"] for x in sorted(c[4], key=lambda x: x["top"])), (c[2] + c[3]) / 2, c[2], c[3])
            for c in cells]


def _header(style, tag, rows):
    """'Colors [sizes] Total' + '[sizes] Qty', or 'Colors Units Total' for a style printed with Sizes/Qty rows."""
    ws = [w for _, r in rows for w in r]
    colors = next(w for w in ws if w["text"] == "Colors")
    total = [w for w in ws if w["text"] == "Total" and w["x0"] > colors["x1"]]
    qty = [w for w in ws if w["text"] in ("Qty", "Units")]
    tag = f"{tag} {style['style']}"
    if len(total) != 1 or len(qty) != 1:
        return [f"{tag}: Colors header '{' '.join(w['text'] for w in ws)}' has no single Qty/Units and Total"]
    qty, total = qty[0], total[0]
    sizes = _cells([w for w in ws if colors["x1"] < w["x0"] and w["x1"] < qty["x0"]])
    if qty["text"] == "Units":
        if sizes:
            return [f"{tag}: a Units header with size cells {[s for s, *_ in sizes]}"]
    elif not sizes:
        return [f"{tag}: no size cells left of Qty"]
    hdr = dict(qty=qty, total=total, sizes=sizes, wide=qty["text"] == "Units",
               left=min([s[2] for s in sizes] + [qty["x0"]]) - 6)
    if style["header"] and (style["header"]["wide"], [s for s, *_ in style["header"]["sizes"]]) != \
            (hdr["wide"], [s for s, *_ in sizes]):
        return [f"{tag}: a second Colors header with other sizes"]
    style["header"] = hdr
    return []


def _qty_cells(tag, words, cells, what):
    """Quantities under size cells: each within TOL of exactly one cell, no cell twice. -> ({size: q}, problems)"""
    got, problems = {}, []
    for w in words:
        if not w["text"].isdigit():
            problems.append(f"{tag}: {what} cell '{w['text']}' is not a number")
            continue
        best, dist = min(((s, abs(xc(w) - x)) for s, x, *_ in cells), key=lambda t: t[1])
        if dist > TOL:
            problems.append(f"{tag}: qty {w['text']}@{xc(w):.0f} is {dist:.1f}pt from column {best}")
            continue
        if best in got:
            problems.append(f"{tag}: two quantities under size {best}")
            continue
        got[best] = int(w["text"])
    return got, problems


def _amount(tag, words, d):
    """'CUR 1,234.00' right of the Qty column (the two can sit on two rows). -> (value | None, problems)"""
    toks = [w["text"] for w in sorted(words, key=lambda w: (w["top"], w["x0"]))]
    if len(toks) != 2 or not CUR.match(toks[0]) or not MONEY.match(toks[1]):
        return None, [f"{tag}: line value '{' '.join(toks)}' is not 'CUR 0.00'"]
    if toks[0] != d["currency"]:
        return None, [f"{tag}: line value in {toks[0]}, the order is in {d['currency']}"]
    return money(toks[1]), []


def _entry(d, style, tag, pno, rows):
    hdr = style["header"]
    tag = f"{tag} {style['style']}"
    if hdr is None:
        return [f"{tag}: colour rows with no Colors header"]
    ws = [w for _, r in rows for w in r]
    lead = ws and min(ws, key=lambda w: w["x0"])
    if lead and lead["text"] in ("Sizes", "Qty") and hdr["wide"]:
        return _wide(style, tag, rows, lead["text"])
    qtyw = [w for w in ws if hdr["qty"]["x0"] - 3 <= w["x0"] and w["x1"] <= hdr["qty"]["x1"] + 3]
    money_ws = [w for w in ws if w["x0"] > hdr["qty"]["x1"] + 3 and w["text"] != "Total"]
    is_total = any(w["text"] == "Total" and w["x0"] > hdr["qty"]["x1"] for w in ws)
    band = [w for w in ws if hdr["left"] <= w["x0"] and w["x1"] < hdr["qty"]["x0"] - 2]
    colour_ws = [w for w in ws if w["x0"] >= LEFT and w["x1"] < hdr["left"]]
    problems = []
    if len(qtyw) != 1 or not qtyw[0]["text"].isdigit():
        problems.append(f"{tag}: {len(qtyw)} cells under {hdr['qty']['text']} ({' '.join(w['text'] for w in qtyw)})")
    value, probs = _amount(tag, money_ws, d)
    problems += probs
    if hdr["wide"] and band:
        problems.append(f"{tag}: '{' '.join(w['text'] for w in band)}' between the colour and Units")
    sizes, probs = ({}, []) if hdr["wide"] else _qty_cells(tag, band, hdr["sizes"], "size")
    problems += probs
    if is_total:
        if colour_ws:
            problems.append(f"{tag}: a Total row with '{' '.join(w['text'] for w in colour_ws)}' in the colour column")
        if not problems:
            style["subtotals"].append(dict(sizes=sizes, qty=int(qtyw[0]["text"]), value=value, colours=len(style["colours"])))
        return problems
    if not colour_ws:
        problems.append(f"{tag}: a quantity row with no colour")
    if problems:
        return problems
    words = " ".join(w["text"] for w in sorted(colour_ws, key=lambda w: (round(w["top"]), w["x0"]))).split()
    code = words[-1] if len(words) > 1 and (re.search(r"\d", words[-1]) or words[-1] == words[0]) else None
    cname = " ".join(words[1:-1] if code and words[0] == code and len(words) > 2 else words[:-1] if code else words)
    style["colours"].append(dict(colour=code or cname, colour_code=code, colour_name=cname, sizes=sizes,
                                 qty=int(qtyw[0]["text"]), line_value=value, page=pno, runs=[]))
    return []


def _wide(style, tag, rows, kind):
    """A 'Sizes ...' row or the 'Qty ...' row under it, for the last colour of a Units-header style."""
    if not style["colours"]:
        return [f"{tag}: a {kind} row before any colour"]
    col = style["colours"][-1]
    ws = [w for _, r in rows for w in r]
    label = min(ws, key=lambda w: w["x0"])
    cells = [w for w in ws if w is not label]
    if kind == "Sizes":
        if col["runs"] and col["runs"][-1][1] is None:
            return [f"{tag}: two Sizes rows with no Qty row between them"]
        col["runs"].append(([(w["text"], xc(w)) for w in cells], None))
        return []
    if not col["runs"] or col["runs"][-1][1] is not None:
        return [f"{tag}: a Qty row with no Sizes row above it"]
    got, problems = _qty_cells(tag, cells, col["runs"][-1][0], "size")
    col["runs"][-1] = (col["runs"][-1][0], got)
    for s, q in got.items():
        col["sizes"][s] = col["sizes"].get(s, 0) + q
    return problems


def _close(d, style):
    """The style's colours become lines; its printed Total rows must equal the colours above them."""
    tag, problems = style["tag"], []
    if style["header"] is None:
        return [f"{tag}: no Colors header"]
    if not style["colours"]:
        return [f"{tag}: no colour rows"]
    if style["price"] is None:
        return []
    if style["header"]["wide"]:
        for c in style["colours"]:
            if not c["runs"] or any(q is None for _, q in c["runs"]):
                problems.append(f"{tag} {c['colour']}: no Sizes and Qty rows under the colour")
        run = [s for c in style["colours"][:1] for cells, _ in c["runs"] for s, _ in cells]
    else:
        run = [s for s, *_ in style["header"]["sizes"]]
    done = 0
    for st in style["subtotals"]:
        cols = style["colours"][done:st["colours"]]
        done = st["colours"]
        want = {s: sum(c["sizes"].get(s, 0) for c in cols) for s in run}
        got = {s: st["sizes"].get(s, 0) for s in run}
        if want != got or st["qty"] != sum(c["qty"] for c in cols) or \
                abs(st["value"] - sum(c["line_value"] for c in cols)) > 0.02:
            problems.append(NotLayout(f"{tag}: the style's Total row prints {got}, {st['qty']}, {st['value']:,.2f}; its "
                                      f"colours add up to {want}, {sum(c['qty'] for c in cols)}, "
                                      f"{sum(c['line_value'] for c in cols):,.2f}"))
    if done not in (0, len(style["colours"])):
        problems.append(f"{tag}: colour rows after the style's Total row")
    attrs = style["attributes"]
    for c in style["colours"]:
        d["lines"].append(dict(
            line_no=len(d["lines"]) + 1, style=style["style"], fabric=None, colour=c["colour"],
            colour_code=c["colour_code"], colour_name=c["colour_name"], description=style["name"],
            product_class=attrs.get("Silhouette") or style["name"], line_collection=style["line_collection"],
            attributes=dict(attrs), made_in=attrs.get("Country of Origin"), composition=None,
            sizes={s: q for s, q in c["sizes"].items() if q}, size_run=run, qty=c["qty"], price=style["price"],
            line_value=c["line_value"], rrp=style["rrp"], rrp_currency=style["rrp_currency"],
            rrp_found=style["rrp_found"], deliver_from=d["start_ship"], deliver_to=d["complete_ship"],
            page=c["page"]))
    return problems


def _totals(d, tag, rows):
    problems = []
    for _, r in rows:
        t = text(r)
        m = re.search(r"Total Quantity:\s*([\d,]+)$", t)
        if m:
            d["total_qty"] = int(m.group(1).replace(",", ""))
            com = " ".join(w["text"] for w in r if w["x1"] < 340)
            com = re.sub(r"^Order Comments:\s*", "", com)
            d["comments"] = com or None
            continue
        m = re.search(r"\b(Subtotal|Grand Total):\s*([A-Z]{3})\s+(\S+)$", t)
        if m and MONEY.match(m.group(3)):
            if m.group(2) != d["currency"]:
                problems.append(f"{tag}: {m.group(1)} in {m.group(2)}, the order is in {d['currency']}")
            d["gross" if m.group(1) == "Subtotal" else "net"] = money(m.group(3))
            if m.group(1) == "Grand Total":
                d["_done"] = True
            continue
        problems.append(f"{tag}: unreadable totals row '{t[:60]}'")
    return problems


def reader_warnings(docs):
    """[(check, detail)]: a WARN for each order JOOR prints as Pending (the brand has not approved it)."""
    return [(PENDING_CHECK, f"{d['doc']} is Pending in JOOR: the brand has not approved it yet, so its quantities "
                            "and prices can still change. Check the draft against the approved order.")
            for d in docs if d.get("status") == "Pending"]


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
        cur = d.get("currency") or "EUR"
        for l in d["lines"]:
            for size, q in l["sizes"].items():
                out.append({"Order confirmation": d["doc"], "Collection": l.get("line_collection"),
                            "Line no.": l["line_no"], "Style": l["style"], "Colour": l["colour"],
                            "Colour name": l["colour_name"], "Size": size, "Qty": q,
                            f"Price {cur}": l["price"], f"Value {cur}": q * l["price"]})
    return out


def totals(docs):
    lines = [l for d in docs for l in d["lines"]]
    return dict(docs=len(docs), lines=len(lines),
                units=sum(l["qty"] for l in lines),
                value=round(sum(l["line_value"] for l in lines), 2),
                printed_units=sum(d.get("total_qty") or 0 for d in docs),
                printed_value=round(sum(d.get("net") or 0 for d in docs), 2))
