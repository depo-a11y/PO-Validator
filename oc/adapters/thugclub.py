"""THUG CLUB Corp. proforma invoice adapter (Thug Club, and its x Alpha Industries collaboration).

Thug Club (Seoul) confirms an order with a PROFORMA INVOICE: one per PDF ("Invoice # TC..." or
"INVOICE NUMBER: TC..."), USD, every line with its sizes, units, unit price and line total, and the
document's printed TOTAL UNITS / TOTAL PRICE ("Total Quantity" / "Total Amount"). Three templates have
been printed, so the columns are taken from the table header's own geometry, never from fixed x positions:

    portrait, the header stacked over three rows, an MSRP column, the material on the row under each
    line, 0 printed in an empty size cell, the header on page 1 only:
                    Product Name                           Size      MSRP  Unit WSP  Total WSP
        Style Code               Category Color HS Code COO          Units
                    Material                    OS  1  2  3  4      (USD)   (USD)     (USD)
        ZZ00TS0001  TEST TEE     T-Shirt  Black 6109... Korea  0  1  1  0   2  $100  $50.00  $100.00
                    Cotton 100%

    portrait, every cell centred under its header, blank empty size cells, no MSRP;

    landscape, one header row repeated on every page, the product name and composition wrapped onto
    the rows under the line, blank empty size cells.

The size cells (OS 1 2 3 4, OS 1 2 3 4 5, S M L XL 2XL) are the run of size tokens just left of Units on
one header row. A document has one table header, so every line's quantities are read against the header
printed above them on that page (a page with no header uses the one before it). Each quantity must sit
within TOL of a size cell's centre, one quantity per cell. Units, unit price, total and MSRP are the
values whose centre falls inside that column's header (its words plus the "(USD)" under or after them).

A row with values is an order line and must carry a style code in the Style Code column; a row with no
values is a wrapped part of the line above, and each of its pieces must start at the left edge of one of
that line's cells (the material under Product Name when the header stacks "Material" there, the product
name or composition otherwise). Anything else is a problem, never skipped.

The currency is the "(XXX)" printed under the money columns; the RRP is the MSRP column's amount with its
own printed currency (util.rrp_label decides which header word labels an RRP). The label is the
collaboration printed at the start of a line's product name ("TC x AI ..." -> "TC x AI"), else THUG CLUB.

Only a PROFORMA INVOICE with a size grid is an order. A THUG CLUB commercial invoice, packing list or
credit note (same table, other title) is the delivery or a refund, and a proforma whose table header has
every column but no size cells is a deposit request: each is refused as "not an order confirmation", and
so is a PDF that holds one beside a proforma. A proforma with no table header at all is a layout change. A
page that belongs to no proforma, or comes after a proforma's totals, is a problem.

Cells are split where words are more than CELL_GAP apart; on the real files a space is ~2pt and the
narrowest gap between two cells 6pt (colour to HS code, HS code to COO).
"""
import re

from .otb import GateError, gate_problems, xc  # noqa: F401  (GateError re-exported)
from ..util import iso_currency, rrp_label

ROW_TOL = 3.0
HEAD_SPAN = 7.0      # the stacked header rows sit within this of the Style Code row
CELL_GAP = 4.0       # words further apart than this are separate cells (a space glyph is ~2pt)
TOL = 3.0            # a quantity's centre within this of its size cell's centre
PAD = 4.0            # a value's centre may sit this far outside its column header
WRAP_TOL = 3.0       # a wrapped piece starts within this of the left edge of the cell it continues
STYLE = re.compile(r"^[A-Z0-9][A-Z0-9/-]{2,19}$")
SIZE = re.compile(r"^(OS|\d{1,2}|X{0,3}S|M|X{0,3}L|[2-5]XL)$")
MONEY = re.compile(r"^\$[\d,]+(\.\d\d)?$")
CUR = re.compile(r"^\(([A-Z]{3})\)$")
ISSUER = re.compile(r"(?im)^\s*(THUG CLUB CORP\b\.?)")
NUMBER = re.compile(r"(?i)\binvoice\s*(?:#|number\s*:)\s*([A-Z0-9][A-Z0-9-]{3,})")
TITLE = re.compile(r"(?i)\b(PROFORMA\s+INVOICE|COMMERCIAL\s+INVOICE|PACKING\s+LIST|CREDIT\s+NOTE)\b")
FOOTER = re.compile(r"(?i)^(total\s+(quantity|units|amount|price)|deposit|balance|payment\s+terms?\b)")
TOTAL_QTY = re.compile(r"(?i)^total\s+(?:quantity|units)\s+([\d,]+)$")
TOTAL_VAL = re.compile(r"(?i)^total\s+(?:amount|price)\s+(\$[\d,]+\.\d\d)$")
COLLAB = re.compile(r"(?i)^TC\s+[x×]\s+(\S+)")
REFUSED = "not an order confirmation"
HOUSE = "THUG CLUB"
# header labels, matched left to right on one header row (case-insensitive); MSRP is any util.rrp_label word
LABELS = (("style", ("style", "code")), ("product", ("product", "name")), ("hs", ("hs", "code")),
          ("price", ("unit", "wsp")), ("price", ("unit", "price")), ("total", ("total", "wsp")),
          ("category", ("category",)), ("colour", ("color",)), ("colour", ("colour",)),
          ("composition", ("composition",)), ("material", ("material",)), ("coo", ("coo",)),
          ("units", ("units",)), ("total", ("total",)), ("size", ("size",)))
TEXT_COLS = ("style", "category", "product", "colour", "composition", "hs", "coo")
VALUE_COLS = ("units", "rrp", "price", "total")
NEEDED = ("style", "category", "product", "colour", "units", "price", "total")


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
    return float(s.replace("$", "").replace(",", ""))


def _start(t):
    """(number, issuer, title) when a page opens a THUG CLUB document, else None."""
    who, num = ISSUER.search(t), NUMBER.search(t)
    if not who or not num:
        return None
    title = TITLE.search(t)
    return num.group(1), who.group(1).strip(), " ".join(title.group(1).upper().split()) if title else None


def parse(pdf_path):
    """Parse a THUG CLUB proforma invoice PDF. Returns (docs, problems)."""
    import pdfplumber

    docs, problems, refused, orphans, cur, any_text = [], [], [], [], None, False
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            t = page.extract_text() or ""
            words = page.extract_words()
            any_text = any_text or bool(words)
            start = _start(t)
            if start:
                number, issuer, title = start
                if title != "PROFORMA INVOICE":
                    refused.append(f"p{pno}: THUG CLUB {title or 'document'} {number} is {REFUSED}: only the "
                                   "PROFORMA INVOICE with sizes and prices is the order")
                    cur = "refused"
                    continue
                cur = _new_doc(number, issuer, t)
                docs.append(cur)
            elif cur is None:
                orphans.append(pno)
                continue
            elif cur == "refused":
                continue
            elif cur["_closed"]:
                problems.append(f"{cur['doc']} p{pno}: a page after the printed totals that starts no new "
                                "proforma invoice")
                continue
            if not words:
                problems.append(f"{cur['doc']} p{pno}: no text layer on this page (a scan?); this reader reads the "
                                "PDF's text")
                continue
            cur["pages"].append(pno)
            cur["order_date"] = cur["order_date"] or _date(r"order\s+date", t)
            probs = _page(cur, pno, rows_of(words), first=bool(start))
            if cur.get("_no_grid"):
                docs.remove(cur)
                refused.append(f"p{pno}: THUG CLUB PROFORMA INVOICE {cur['doc']} prints no size grid (sizes, units, "
                               f"unit price and total per line), so it is {REFUSED}: a deposit or other invoice")
                cur = "refused"
                continue
            problems += probs
    if not any_text:
        return [], ["the PDF has no text layer (a scan?); this reader reads the PDF's text, so nothing was read"]
    if docs or refused:
        problems += [f"p{p}: belongs to no THUG CLUB proforma invoice" for p in orphans]
    for d in docs:
        for key in ("_geom", "_cur", "_closed", "_no_grid"):
            d.pop(key, None)
        d["label"] = doc_label(d)
    return docs, refused + problems + gate_problems(docs)


def _date(label, t):
    m = re.search(r"(?i)" + label + r":?\s*(\d{4}-\d\d-\d\d)", t)
    return m.group(1) if m else None


def _new_doc(number, issuer, t):
    def g(pat):
        m = re.search(pat, t)
        return " ".join(m.group(1).split()) if m and m.group(1).strip() else None
    window = g(r"(?i)delivery window:?[ \t]*([^\n]*\S)") or g(r"(?i)delivery window:?[ \t]*\n[ \t]*([^\n]+)")
    season = re.match(r"TC(FW|SS)(\d\d)", number)
    return dict(doc=number, issuer=issuer, label=None, title="PROFORMA INVOICE",
                date=_date(r"invoice\s+date", t), order_date=_date(r"order\s+date", t),
                delivery_window=window, currency=None, collection=season.group(1) + season.group(2) if season else None,
                season=season.group(1) + season.group(2) if season else None, total_qty=None, net=None, gross=None,
                discount=None, gender=None, pages=[], lines=[], _geom=None, _closed=False, _cur=None)


def _page(d, pno, rows, first):
    """Read one page of a proforma into d. Returns problems."""
    problems = []
    head = next((i for i, (_, r) in enumerate(rows) if re.search(r"(?i)\bstyle code\b", text(r))), None)
    body = rows
    if head is not None:
        top = rows[head][0]
        block = [k for k, (tp, _) in enumerate(rows) if abs(tp - top) <= HEAD_SPAN]
        geom, probs = _header(d["doc"], pno, [rows[k][1] for k in block])
        if geom == "no sizes" and first:
            d["_no_grid"] = True
            return problems
        problems += probs if geom != "no sizes" else [f"{d['doc']} p{pno}: a table header with no size cells"]
        if not isinstance(geom, dict):
            return problems
        if not first and block[0] > 0:
            problems.append(f"{d['doc']} p{pno}: {block[0]} row(s) above the repeated table header")
        cur = iso_currency(geom["currency"])
        if d["currency"] and cur != d["currency"]:
            problems.append(f"{d['doc']} p{pno}: the table header prints {cur}, page {d['pages'][0]} printed "
                            f"{d['currency']}")
        d["currency"] = d["currency"] or cur
        d["_geom"], body = geom, rows[block[-1] + 1:]
    elif first:
        return [f"{d['doc']} p{pno}: no order table header (Style Code, sizes, Units, unit price, total) on the "
                "proforma's first page (layout changed)"]
    if d["_geom"] is None:
        return problems + [f"{d['doc']} p{pno}: no table header to read the lines by"]
    for _, row in body:
        problems += _row(d, pno, row)
    return problems


def _header(doc, pno, block):
    """The column geometry of one table header block, or None and why; "no sizes" when the header has every
    other column but no size cells (an invoice with no size grid)."""
    problems, labels, curs, sizes = [], {}, [], []
    for row in block:
        rest = []
        for w in row:
            (curs if CUR.match(w["text"]) else sizes if SIZE.match(w["text"]) else rest).append((w, row))
        i = 0
        while i < len(rest):
            w = rest[i][0]
            low = [x[0]["text"].casefold() for x in rest[i:i + 2]]
            hit = next(((k, len(ws)) for k, ws in LABELS if tuple(low[:len(ws)]) == ws), None)
            if hit is None and rrp_label(w["text"]):
                hit = ("rrp", 1)
            if hit is None:
                problems.append(f"{doc} p{pno}: table header word '{w['text']}' at x={w['x0']:.0f} is not one this "
                                "reader knows (layout changed)")
                i += 1
                continue
            key, n = hit
            ws = [x[0] for x in rest[i:i + n]]
            if key in labels and key != "size":
                problems.append(f"{doc} p{pno}: table header names {key} twice")
            labels[key] = dict(x0=min(x["x0"] for x in ws), x1=max(x["x1"] for x in ws), top=ws[0]["top"], cur=None)
            i += n
    missing = [k for k in NEEDED if k not in labels]
    if missing:
        return None, problems + [f"{doc} p{pno}: table header has no {', '.join(missing)} column"]
    if not sizes:
        return "no sizes", problems
    units = labels["units"]
    size_rows = {round(r[0]["top"]) for w, r in sizes}
    cells = sorted((w for w, _ in sizes), key=lambda w: w["x0"])
    if not cells or len(size_rows) != 1 or cells[-1]["x1"] >= units["x0"]:
        return None, problems + [f"{doc} p{pno}: no size cells on one header row left of Units "
                                 f"(found {' '.join(w['text'] for w in cells) or 'none'})"]
    if any(xc(b) - xc(a) < 2 * TOL for a, b in zip(cells, cells[1:])):
        problems.append(f"{doc} p{pno}: size cells closer than {2 * TOL:.0f}pt; a quantity could sit under two")
    for w, row in curs:
        same = [k for k, s in labels.items() if k in ("rrp", "price", "total") and abs(s["top"] - w["top"]) <= ROW_TOL
                and 0 <= w["x0"] - s["x1"] <= 6]
        under = [k for k, s in labels.items() if k in ("rrp", "price", "total") and s["x0"] - 1 <= xc(w) <= s["x1"] + 1]
        key = (same or under or [None])[0]
        if key is None or labels[key]["cur"]:
            problems.append(f"{doc} p{pno}: currency {w['text']} at x={w['x0']:.0f} is under no free money column")
            continue
        s = labels[key]
        s.update(cur=CUR.match(w["text"]).group(1), x0=min(s["x0"], w["x0"]), x1=max(s["x1"], w["x1"]))
    found = {labels[k]["cur"] for k in ("rrp", "price", "total") if k in labels}
    if None in found or len(found) != 1:
        problems.append(f"{doc} p{pno}: the money columns print currencies {sorted(map(str, found))}, need one each "
                        "and the same")
    mat = labels.get("material")
    if mat and abs(mat["x0"] - labels["product"]["x0"]) > 5:
        problems.append(f"{doc} p{pno}: Material is not stacked under Product Name")
    geom = dict(cells=[(w["text"], xc(w)) for w in cells], size_band=(cells[0]["x0"] - TOL, cells[-1]["x1"] + TOL),
                text={k: (labels[k]["x0"], labels[k]["x1"]) for k in TEXT_COLS if k in labels},
                values={k: (labels[k]["x0"] - PAD, labels[k]["x1"] + PAD) for k in VALUE_COLS if k in labels},
                currency=next(iter(found)) if len(found) == 1 else None,
                rrp_currency=labels["rrp"]["cur"] if "rrp" in labels else None, material=bool(mat))
    return geom, problems


def _cells(words):
    out = []
    for w in sorted(words, key=lambda w: w["x0"]):
        if out and w["x0"] - out[-1][-1]["x1"] <= CELL_GAP:
            out[-1].append(w)
        else:
            out.append([w])
    return [dict(x0=c[0]["x0"], x1=c[-1]["x1"], text=" ".join(w["text"] for w in c)) for c in out]


def _column(geom, c):
    """The text column a cell sits in: the most header overlap, else the nearest header edge."""
    def gap(span):
        return max(span[0] - c["x1"], c["x0"] - span[1], 0.0)

    def overlap(span):
        return min(span[1], c["x1"]) - max(span[0], c["x0"])
    cols = geom["text"]
    best = max(cols, key=lambda k: overlap(cols[k]))
    return best if overlap(cols[best]) > 0 else min(cols, key=lambda k: gap(cols[k]))


def _row(d, pno, row):
    geom, t = d["_geom"], text(row)
    if re.search(r"(?i)\bdiscount\b", t):
        return [f"{d['doc']} p{pno}: '{t[:60]}': a discount is not applied to any line; this reader does not apply "
                "discounts"]
    if d["_closed"] or FOOTER.match(t):
        d["_closed"] = True
        return _footer(d, pno, row, t)
    lo, hi = geom["size_band"]
    nums = {k: [] for k in ("size",) + VALUE_COLS}
    words = []
    for w in row:
        c = xc(w)
        inside = [k for k, (a, b) in geom["values"].items() if a <= c <= b]
        k = "size" if lo <= c <= hi else min(inside, key=lambda k: abs(c - sum(geom["values"][k]) / 2), default=None)
        (nums[k] if k else words).append(w)
    cells = _cells(words)
    if not any(nums.values()):
        return _wrap(d, pno, cells, t)
    cols = {}
    for c in cells:
        cols.setdefault(_column(geom, c), []).append(c)
    style = cols.get("style") or []
    if len(style) != 1 or not STYLE.match(style[0]["text"]):
        return [f"{d['doc']} p{pno}: values with no style code: '{t[:60]}'"]
    line, problems = _line(d, pno, style[0]["text"], cols, nums, geom)
    if line:
        d["lines"].append(line)
        d["_cur"] = dict(line=line, cells=[(k, c["x0"]) for k, cs in cols.items() for c in cs])
    else:
        d["_cur"] = None
    return problems


def _footer(d, pno, row, t):
    for key, pat, conv in (("total_qty", TOTAL_QTY, lambda s: int(s.replace(",", ""))), ("net", TOTAL_VAL, money)):
        m = pat.match(t)
        if m:
            if d[key] is not None:
                return [f"{d['doc']} p{pno}: printed total '{t}' twice"]
            d[key] = conv(m.group(1))
    lo, hi = d["_geom"]["text"]["style"]
    if (not FOOTER.match(t) and lo - 5 <= row[0]["x0"] <= hi and STYLE.match(row[0]["text"])
            and any(MONEY.match(w["text"]) for w in row)):
        return [f"{d['doc']} p{pno}: an order line after the printed totals: '{t[:60]}'"]
    return []


def _wrap(d, pno, cells, t):
    cur = d["_cur"]
    if cur is None:
        return [f"{d['doc']} p{pno}: '{t[:60]}' is neither an order line nor a wrapped part of one"]
    problems = []
    for c in cells:
        key = next((k for k, x0 in cur["cells"] if abs(c["x0"] - x0) <= WRAP_TOL), None)
        if key is None or key == "style":
            problems.append(f"{d['doc']} p{pno}: '{c['text']}' at x={c['x0']:.0f} under line "
                            f"{cur['line']['line_no']} {cur['line']['style']} starts under none of its cells")
            continue
        field = "composition" if key == "product" and d["_geom"]["material"] else FIELDS[key]
        cur["line"][field] = _join(cur["line"][field], c["text"])
        if field == "colour":
            cur["line"]["colour_name"] = cur["line"]["colour"]
    return problems


FIELDS = dict(category="product_class", product="description", colour="colour", composition="composition",
              hs="hs_code", coo="made_in")


def _join(a, b):
    if not a:
        return b
    return a + b if a.endswith("-") else f"{a} {b}"


def _line(d, pno, style, cols, nums, geom):
    n = len(d["lines"]) + 1
    tag = f"{d['doc']} p{pno} line {n} {style}"
    problems = []

    def one(key, pat, conv, need=True):
        ws = nums[key]
        if not ws:
            if need:
                problems.append(f"{tag}: no {key}")
            return None
        if len(ws) > 1 or not pat.match(ws[0]["text"]):
            problems.append(f"{tag}: {key} column holds '{' '.join(w['text'] for w in ws)}'")
            return None
        return conv(ws[0]["text"])
    units = one("units", re.compile(r"^\d+$"), int)
    price, total = one("price", MONEY, money), one("total", MONEY, money)
    rrp = one("rrp", MONEY, money, need=False)
    hit = {}
    for w in nums["size"]:
        if not w["text"].isdigit():
            problems.append(f"{tag}: size cell '{w['text']}' is not a number")
            continue
        best, dist = min(((s, abs(xc(w) - x)) for s, x in geom["cells"]), key=lambda t: t[1])
        if dist > TOL:
            problems.append(f"{tag}: qty {w['text']}@{xc(w):.0f} is {dist:.1f}pt from column {best}")
            continue
        if best in hit:
            problems.append(f"{tag}: two quantities under size {best}")
            continue
        hit[best] = int(w["text"])
    if None in (units, price, total):
        return None, problems

    def txt(key):
        return " ".join(c["text"] for c in cols.get(key, [])) or None
    return dict(line_no=n, style=style, fabric=None, colour=txt("colour"), colour_code=None,
                colour_name=txt("colour"), description=txt("product"), product_class=txt("category"),
                composition=txt("composition"), made_in=txt("coo"), hs_code=txt("hs"),
                sizes={s: q for s, q in hit.items() if q}, size_run=[s for s, _ in geom["cells"]],
                qty=units, price=price, line_value=total, msrp=rrp, rrp=rrp,
                rrp_currency=geom["rrp_currency"] if rrp is not None else None,
                deliver_from=d["delivery_window"], deliver_to=None, page=pno), problems


def doc_label(d):
    """The label the lines are for: the collaboration at the start of a product name ("TC x AI ..." gives
    "TC x AI"), else THUG CLUB; "TC x AI + THUG CLUB" when a proforma mixes them. Also applied to an AI read
    of a thugclub brand, so both readers are held to the same oc_label rule."""
    found = set()
    for l in d["lines"]:
        m = COLLAB.match((l.get("description") or "").strip())
        found.add(f"TC x {m.group(1).upper()}" if m else HOUSE)
    return " + ".join(sorted(found)) or None


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
        cur = d.get("currency") or "USD"
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
