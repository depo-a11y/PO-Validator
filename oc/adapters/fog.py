"""Fear of God Order Confirmation adapter (FOG Essentials and Fear of God LLC).

Both issuers print the same sales-order layout ("ORDER # SO9999999"), USD, one
confirmation per PDF. Every order line repeats the table header and carries its
own size header:

    SELL STYLE FABRIC CONTENT COLOR GROUP SIZES & UNITS   UNITS PRICE MSRP TOTAL
    PRD
                                   2XS XS S M L XL 2XL 3XL 4XL
         ZZ00001  100%     0001 :   0  2 6 6 3 0  0   0   0   17 $10.00 $25.00 $170.00
                  COTTON   WASHE
                           D INK
                           BLACK

The size header changes from line to line (2XS..4XL, 2XS..3XL, XS..XL, 28..38,
O/S) and is spread across the same band, so a cell of one shape sits near a
different size of another. Each line's quantities are mapped to its own size
header only: there must be one quantity per header cell, each within TOL of
its cell. A size header at the foot of a page belongs to the line at the top
of the next, so rows are read as one stream across pages, minus each page's
repeated address block.

FOG Essentials prints MSRP; Fear of God LLC leaves it blank. Amounts are
assigned to PRICE / MSRP / TOTAL by the x-centre of this line's header, so a
missing MSRP cannot shift TOTAL into its place. The RRP is the amount under the
header word that labels one (util.rrp_label: MSRP here); a header with no such
word gives no RRP.

Fabric content and colour wrap inside narrow columns, sometimes mid-word
("WASHE" / "D INK" / "BLACK"). The PDF draws the space glyphs, so each cell is
rebuilt from its characters line by line with no separator: "WASHED INK BLACK".
The colour code (0001) is the join key; the name is informational.

S/S 26 OCs share the grid and differ in the cells. The STYLE cell can wrap:
100SP0001 over 01F (apparel) and ZX80A-10 over 0EVA (footwear) are the store's
100SP000101F and ZX80A-100EVA, so a style is every word in its column, joined.
A colour code is only what is printed before the colon ("0293 :", "250 :").
The join token `colour` is that code; with none printed it is None when the
style wrapped (the wrapped style carries the colourway) and the printed name
when it did not (Summer 2026 ": GREY", for a join_key on {colour_code}).
These OCs print ORDER # alone, the number at the end of an address row.

Zero quantities are printed for unordered sizes. `sizes` keeps the ordered
sizes; `size_run` keeps the printed header. The gate compares the lines to
Total Amount, so a non-zero Discount Total is refused.
"""
import re

from .otb import GateError, gate_problems, xc  # noqa: F401  (GateError re-exported)
from ..util import rrp_label

TOL = 4.0
ROW_TOL = 3.0
STYLE = re.compile(r"^[A-Z]{2}\d{4,6}$")
OLD_STYLE = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9-]{4,}$")   # S/S 26 heads: 100SP0001, ZX80A-10, ZX00266
X_STYLE = (65, 115)
X_FAB, X_COL = (115, 210), (210, 290)
X_FABRIC, X_CONTENT = (115, 155), (155, 210)   # Spring 2026 prints a fabric word ("Knit") left of the content
# That word is `fabric_name`, never `fabric`: `fabric` is a join field (a code in the store's key, otb's P0001), and
# the default join_key "{style}{fabric} {colour}" would otherwise build 192SP266941FKnit for a config without one.
X_SIZES_LO = 280
CURRENCIES = {"US Dollar": "USD", "Euro": "EUR"}


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


def cell_text(chars, lo, hi):
    return "".join(c["text"] for c in sorted(chars, key=lambda c: c["x0"]) if lo <= c["x0"] < hi)


def squash(s):
    return " ".join(s.split()) or None


def header_end(rows, gap=20.0):
    """Top of the last row of the address block that heads every page.

    The block runs from FROM / SHIP TO / BILL TO down to the first vertical gap
    wider than `gap`; SHIP TERMS closes it on most documents but not all."""
    i = next((k for k, (_, r) in enumerate(rows) if text(r).startswith("FROM SHIP TO BILL TO")), None)
    if i is None:
        return None
    end = rows[i][0]
    for top, _ in rows[i + 1:]:
        if top - end > gap:
            break
        end = top
    return end


def parse(pdf_path):
    """Parse a Fear of God order-confirmation PDF. Returns (docs, problems)."""
    import pdfplumber

    docs, problems, d = [], [], None
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            t = page.extract_text() or ""
            m = re.search(r"ORDER #\s*(SO\d+)", t) or ("ORDER #" in t and re.search(r"\b(SO\d{6,})\b", t))
            if not m or "Order Confirmation" not in t:
                continue
            rows = rows_of(page.extract_words())
            if d is None or d["doc"] != m.group(1):
                d = _new_doc(m.group(1), t, rows)
                docs.append(d)
            if d["_done"]:
                continue
            d["pages"].append(pno)
            top_end = header_end(rows)
            if top_end is None:
                problems.append(f"{d['doc']} p{pno}: no FROM / SHIP TO / BILL TO block heading the page")
                continue
            for top, row in rows:
                if top <= top_end:
                    continue
                if text(row).startswith("Total Units:"):
                    d["_done"] = True
                    break
                lo, hi = min(w["top"] for w in row) - 0.5, max(w["top"] for w in row) + 0.5
                d["_stream"].append((pno, row, [c for c in page.chars if lo <= c["top"] <= hi]))
            _totals(d, t)
    for d in docs:
        problems += _read(d, d.pop("_stream"))
        d.pop("_done")
        if d["discount"]:
            problems.append(f"{d['doc']}: Discount Total {d['discount']:,.2f} is not applied to any line; "
                            "the lines sum to the Subtotal, not the Total Amount")
    return docs, problems + gate_problems(docs)


def _new_doc(doc, t, rows):
    def g(pat):
        m = re.search(pat, t)
        return m.group(1).strip() if m else None

    def after(head, lo, hi, stop=None):
        """Words in [lo, hi) on the row under `head`, or on every row down to `stop`."""
        i = next((k for k, (_, r) in enumerate(rows) if text(r).startswith(head)), None)
        if i is None:
            return None
        below = rows[i + 1:i + 2] if stop is None else \
            [r for r in rows[i + 1:]][:next((k for k, (_, r) in enumerate(rows[i + 1:]) if text(r).startswith(stop)),
                                            len(rows))]
        return " ".join(w["text"] for _, r in below for w in r if lo <= w["x0"] < hi) or None

    brand = after("FROM SHIP TO BILL TO", 0, 210)
    season = after("ORDER DATE", 635, 700, stop="CANCEL DATE")
    cur = after("CANCEL DATE", 100, 210)
    return dict(doc=doc, brand=brand, issuer=brand, label=None, date=after("ORDER DATE", 0, 100), start_ship=after("ORDER DATE", 100, 200),
                cancel_date=after("CANCEL DATE", 0, 100), currency=CURRENCIES.get(cur, cur),
                collection=season, season=season, customer_po=g(r"Customer PO #\s*(\S+)"),
                ship_terms=g(r"SHIP TERMS:\s*(.+)"), total_qty=None, net=None, gross=None, discount=None,
                gender=None, pages=[], lines=[], _stream=[], _done=False)


def _totals(d, t):
    for key, pat in (("total_qty", r"Total Units:\s*([\d,]+)"), ("gross", r"Subtotal:\s*(\$[\d,]+\.\d\d)"),
                     ("discount", r"Discount Total:\s*(-?\$[\d,]+\.\d\d)"),
                     ("net", r"Total Amount:\s*(\$[\d,]+\.\d\d)")):
        m = re.search(pat, t)
        if m:
            v = m.group(1).replace("-", "")
            d[key] = int(v.replace(",", "")) if key == "total_qty" else money(v)


def _read(d, stream):
    problems, cols, sizes_row, state, cur = [], None, None, None, None

    def close():
        if cur:
            line, probs = _finish(d, cur)
            problems.extend(probs)
            if line:
                d["lines"].append(line)

    for pno, row, chars in stream:
        t = text(row)
        if t.startswith("SELL STYLE"):
            close()
            cur, state = None, "prd"
            cols = {w["text"]: xc(w) for w in row}
            cols["UNITS_X0"] = max(w["x0"] for w in row if w["text"] == "UNITS")
            continue
        if state == "prd":
            state = "sizes" if t == "PRD" else None
            if state is None:
                problems.append(f"{d['doc']} p{pno}: expected PRD under the table header, got '{t[:40]}'")
            continue
        if state == "sizes":
            sizes_row, state = row, "line"
            stray = [w["text"] for w in row if not (X_SIZES_LO <= w["x0"] and w["x1"] < cols["UNITS_X0"])]
            if stray:
                problems.append(f"{d['doc']} p{pno}: size header carries {stray} outside the size band")
            continue
        if state == "line":
            style = next((w for w in row if X_STYLE[0] <= w["x0"] < X_STYLE[1] and is_style(w["text"])), None)
            if style is None:
                problems.append(f"{d['doc']} p{pno}: expected an order line under the size header, got '{t[:40]}'")
                state = None
                continue
            cur = dict(n=len(d["lines"]) + 1, pno=pno, row=row, sizes_row=sizes_row, cols=cols,
                       style=[style["text"]], fabric=[cell_text(chars, *X_FABRIC)], fab=[cell_text(chars, *X_CONTENT)],
                       col=[cell_text(chars, *X_COL)])
            state = "cont"
            continue
        if state == "cont":
            wrap = [w for w in row if X_STYLE[0] <= w["x0"] < X_STYLE[1]]
            cur["style"] += [w["text"] for w in wrap]   # the style cell wrapped (100SP0001 over 01F)
            row = [w for w in row if not any(w is x for x in wrap)]
            stray = [w["text"] for w in row if not (X_FAB[0] <= w["x0"] < X_COL[1])]
            if stray:
                problems.append(f"{d['doc']} p{pno}: unexpected {stray} under line {cur['n']}")
            cur["fabric"].append(cell_text(chars, *X_FABRIC))
            cur["fab"].append(cell_text(chars, *X_CONTENT))
            cur["col"].append(cell_text(chars, *X_COL))
    close()
    return problems


def is_style(text):
    return bool(STYLE.match(text) or OLD_STYLE.match(text))


def _finish(d, cur):
    row, cols, n = cur["row"], cur["cols"], cur["n"]
    style = "".join(cur["style"])
    tag = f"{d['doc']} p{cur['pno']} line {n} {style}"
    problems = []
    code = next((w["text"] for w in row if X_COL[0] <= w["x0"] < X_COL[1] - 40 and re.fullmatch(r"\d{2,5}", w["text"])),
                None)
    raw = "".join(cur["col"])
    col_text = re.sub(r"^\s*(\d{2,5})?\s*:\s*", "", raw)
    name_only = code is None and re.match(r"\s*:", raw) is not None and squash(col_text) is not None
    colour = code if code else None if len(cur["style"]) > 1 else squash(col_text)
    if code is None and not name_only:
        problems.append(f"{tag}: no colour code")
    units_x0 = cols["UNITS_X0"]
    qtys = [w for w in row if X_SIZES_LO <= w["x0"] and w["x1"] < units_x0 - 2]
    units = [w for w in row if abs(xc(w) - cols["UNITS"]) <= 8 and w["x0"] >= units_x0 - 10 and w["text"].isdigit()]
    amounts = {}
    rrp_col = next((k for k in cols if rrp_label(k)), None)
    names = tuple(k for k in ("PRICE", rrp_col, "TOTAL") if k)
    for w in row:
        if w["text"].startswith("$"):
            name, dist = min(((k, abs(xc(w) - cols[k])) for k in names), key=lambda t: t[1])
            if dist > 15 or name in amounts:
                problems.append(f"{tag}: amount {w['text']}@{xc(w):.0f} matches no free {'/'.join(names)} column")
                continue
            amounts[name] = money(w["text"])
    for name in ("PRICE", "TOTAL"):
        if name not in amounts:
            problems.append(f"{tag}: no {name}")
    if len(units) != 1:
        problems.append(f"{tag}: {len(units)} UNITS cells")
    cells = [(w["text"], xc(w)) for w in cur["sizes_row"]]
    if len(qtys) != len(cells):
        problems.append(f"{tag}: {len(qtys)} quantities under a {len(cells)}-size header "
                        f"{' '.join(s for s, _ in cells)}")
    hit = {}
    for w in qtys:
        if not w["text"].isdigit():
            problems.append(f"{tag}: quantity cell '{w['text']}' is not a number")
            continue
        best, dist = min(((s, abs(xc(w) - x)) for s, x in cells), key=lambda t: t[1])
        if dist > TOL:
            problems.append(f"{tag}: qty {w['text']}@{xc(w):.0f} is {dist:.1f}pt from column {best}")
            continue
        if best in hit:
            problems.append(f"{tag}: two quantities under size {best}")
            continue
        hit[best] = int(w["text"])
    if (code is None and not name_only) or len(units) != 1 or "PRICE" not in amounts or "TOTAL" not in amounts:
        return None, problems
    return dict(line_no=n, style=style, fabric=None, fabric_name=squash("".join(cur["fabric"])), colour=colour,
                colour_code=code,
                colour_name=squash(col_text), composition=squash("".join(cur["fab"])), product_class=None,
                sizes={s: q for s, q in hit.items() if q}, size_run=[s for s, _ in cells],
                qty=int(units[0]["text"]), price=amounts["PRICE"], msrp=amounts.get(rrp_col),
                rrp=amounts.get(rrp_col), rrp_currency=d["currency"],
                line_value=amounts["TOTAL"], deliver_from=d["start_ship"], deliver_to=d["cancel_date"],
                page=cur["pno"]), problems


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
