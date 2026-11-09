"""AD Milano Srl Order Confirmation adapter (Ann Demeulemeester).

AD Milano's ERP prints one confirmation per PDF ("Ord. N° 9001"), landscape,
EUR with decimal commas ("1.250,00"), followed by pages of terms. Each order
line is a block:

    TSTSHO  Z0000001  LT001  099            T001   5 PA          EUR 1.250,00
    TS Shoes TEST ANKLE BOOT                 BLACK  Made-in ITALY Composition 100% LEATHER ...
             LONG NAME                                           (wrapped description)
    HS/HTS Code: 64000000
    Size     36   37   38   39   40
    Q.ty      1    1    1    1    1
    Range     1
    WHL Price € 250

(product line, item, item variant, colour code, size grid, net qty, UM, line
amount). A block may break across a page: the header on one page, the size grid
on the next. Rows are read as one stream across the order pages for that reason.

Size columns start at a fixed x and advance by a fixed pitch, so "XS S" puts XS
exactly where "S M" puts S. The header is read from the Size row of the same
block as the Q.ty row, every quantity must sit within TOL of one of that row's
cells, and every cell must carry exactly one quantity.

The document total is per unit of measure ("PA 13", "PZ 6"); total_qty is
their sum. The money total is "Total value in order currency" gross, discount,
net. The gate compares the lines to net, so a document discount is refused,
never spread over the lines.
"""
import re

from .otb import GateError, eur, gate_problems, xc  # noqa: F401  (GateError re-exported)

TOL = 6.0
ROW_TOL = 3.0
ITEM = re.compile(r"^[A-Z0-9]{6,}$")
AMOUNT = re.compile(r"^[\d.]+,\d\d$")
# column x0 bands, measured on the real layout (header "Line Item Item Colour ...")
X_ITEM, X_VARIANT, X_COLOUR, X_GRID = (70, 140), (140, 220), (220, 262), (470, 520)
X_DESC, X_CNAME, X_MADE = (70, 225), (225, 440), 440
HEADER = {"Line": 21.8, "Colour": 227.1, "Grid": 482.2, "Net": 556.7}


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


def band(row, lo, hi):
    return [w for w in row if lo <= w["x0"] < hi]


def is_line_head(row):
    return (len(row) >= 5 and row[0]["x0"] < 30 and re.fullmatch(r"[A-Z]{3,}", row[0]["text"])
            and any(X_ITEM[0] <= w["x0"] < X_ITEM[1] and ITEM.match(w["text"]) for w in row)
            and any(w["text"] == "EUR" for w in row) and AMOUNT.match(row[-1]["text"]))


def is_block_end(row):
    t = text(row)
    return t.startswith("Totals by order") or bool(re.search(r"\bLine Net Total Amount\b", t))


def _doc_fields(t):
    def g(pat):
        m = re.search(pat, t)
        return m.group(1).strip() if m else None
    win = re.search(r"Line Delivery Window\s+(\d\d/\d\d/\d{4})\s*-\s*(\d\d/\d\d/\d{4})", t)
    season, event = g(r"Season\s+(\d{4}\s+[A-Z]{2})"), g(r"Event\s+(.+?)\s+Date\b")
    return dict(date=g(r"\bDate\s+(\d\d/\d\d/\d{4})"), brand=g(r"\bBrand\s+(.+?)\s+Payments\b"),
                currency=g(r"\bCurrency\s+([A-Z]{3})\b"), season=season, event=event,
                collection=" ".join(x for x in (season, event) if x) or None,
                delivery_window=(win.group(1), win.group(2)) if win else (None, None))


def _header_problems(doc, row):
    have = {w["text"]: w["x0"] for w in row}
    return [f"{doc}: table header '{k}' at x={have.get(k)}, expected {x} (layout changed)"
            for k, x in HEADER.items() if k not in have or abs(have[k] - x) > 3]


def parse(pdf_path):
    """Parse an AD Milano order-confirmation PDF. Returns (docs, problems)."""
    import pdfplumber

    docs, problems = [], []
    with pdfplumber.open(pdf_path) as pdf:
        stream, d = [], None
        for pno, page in enumerate(pdf.pages, 1):
            t = page.extract_text() or ""
            m = re.search(r"Ord\. N°\s*(\d+)", t)
            if m and "ORDER CONFIRMATION" in t:
                d = dict(doc=m.group(1), lines=[], pages=[], total_qty=None, net=None, gross=None,
                         discount=None, gender=None, **_doc_fields(t))
                docs.append(d)
                stream = []
                d["_stream"] = stream
            if d is None or d.get("_done") or "GENERAL TERMS AND CONDITIONS" in t:
                continue
            d["pages"].append(pno)
            for _, row in rows_of(page.extract_words()):
                if re.fullmatch(r"page \d+", text(row)):
                    continue
                stream.append((pno, row))
                if text(row).startswith("Total value in order currency"):
                    d["_done"] = True
        for d in docs:
            problems += _read(d, d.pop("_stream"))
            d.pop("_done", None)
    return docs, problems + gate_problems(docs)


def _read(d, stream):
    problems, i = [], 0
    head = next((row for _, row in stream if row and row[0]["text"] == "Line" and "Grid" in text(row)), None)
    if head is None:
        return [f"{d['doc']}: no order-line table header"]
    problems += _header_problems(d["doc"], head)
    while i < len(stream):
        pno, row = stream[i]
        if is_line_head(row):
            j = i + 1
            while j < len(stream) and not is_line_head(stream[j][1]) and not is_block_end(stream[j][1]):
                j += 1
            line, probs = _line(d, len(d["lines"]) + 1, pno, row, stream[i + 1:j])
            problems += probs
            if line:
                d["lines"].append(line)
            i = j
            continue
        t = text(row)
        if t.startswith("Total value in order currency"):
            money = [eur(w["text"]) for w in row if AMOUNT.match(w["text"])]
            if len(money) == 3:
                d["gross"], d["discount"], d["net"] = money
        um = re.fullmatch(r"([A-Z]{2})\s+([\d.]+)", t)
        if um and i > 0 and _in_qty_totals(stream, i):
            d["total_qty"] = (d["total_qty"] or 0) + int(um.group(2).replace(".", ""))
        i += 1
    if d["discount"]:
        problems.append(f"{d['doc']}: document discount {d['discount']:,.2f} is not applied to any line; "
                        "the lines sum to the gross value, not the net")
    return problems


def _in_qty_totals(stream, i):
    """True when row i sits under 'Total Quantity UM Net ordered' and above 'Currency Gross value'."""
    for _, row in reversed(stream[:i]):
        t = text(row)
        if t.startswith("Total Quantity"):
            return True
        if not re.fullmatch(r"[A-Z]{2}\s+[\d.]+", t):
            return False
    return False


def _line(d, n, pno, head, block):
    tag = f"{d['doc']} p{pno} line {n}"
    problems = []

    def one(lo_hi, pat=None):
        ws = [w for w in band(head, *lo_hi) if pat is None or re.fullmatch(pat, w["text"])]
        return ws[0]["text"] if ws else None

    style = one(X_ITEM, ITEM.pattern)
    tag = f"{tag} {style}"
    qty = next((int(w["text"]) for w in head if 570 < w["x1"] < 590 and w["text"].isdigit()), None)
    um = next((w["text"] for w in head if 588 <= w["x0"] < 615 and re.fullmatch(r"[A-Z]{2}", w["text"])), None)
    if qty is None:
        problems.append(f"{tag}: no net quantity")
    desc, cname, rest, hs, prod_line = [], [], [], None, None
    pairs, price = [], None
    k = 0
    while k < len(block):
        _, row = block[k]
        first = row[0]["text"]
        if first == "HS/HTS":
            hs = row[-1]["text"]
        elif first == "Size":
            nxt = block[k + 1][1] if k + 1 < len(block) else []
            if not nxt or nxt[0]["text"] != "Q.ty":
                problems.append(f"{tag}: Size row with no Q.ty row under it")
            else:
                pairs.append(([w for w in row[1:]], [w for w in nxt[1:]]))
                k += 1
        elif first == "Q.ty":
            problems.append(f"{tag}: Q.ty row with no Size row above it")
        elif first == "WHL":
            num = "".join(w["text"] for w in row if w["x0"] > 63 and w["text"] != "€")
            try:
                price = eur(num)
            except ValueError:
                problems.append(f"{tag}: unreadable WHL price '{num}'")
        elif first == "Range":
            pass
        else:
            if k == 0:
                prod_line = " ".join(w["text"] for w in row if w["x0"] < X_DESC[0]) or None
            desc += [w["text"] for w in band(row, *X_DESC)]
            cname += [w["text"] for w in band(row, *X_CNAME)]
            rest += [w["text"] for w in row if w["x0"] >= X_MADE]
        k += 1
    if not pairs:
        problems.append(f"{tag}: no Size/Q.ty grid")
    if price is None:
        problems.append(f"{tag}: no WHL price")
    sizes, run = {}, []
    for size_row, qty_row in pairs:
        cells = [(w["text"], xc(w)) for w in size_row]
        run += [s for s, _ in cells]
        hit = {}
        for w in qty_row:
            if not w["text"].isdigit():
                problems.append(f"{tag}: Q.ty cell '{w['text']}' is not a number")
                continue
            best, dist = min(((s, abs(xc(w) - x)) for s, x in cells), key=lambda t: t[1])
            if dist > TOL:
                problems.append(f"{tag}: qty {w['text']}@{xc(w):.0f} is {dist:.1f}pt from column {best}")
                continue
            if best in hit:
                problems.append(f"{tag}: two quantities under size {best}")
                continue
            hit[best] = int(w["text"])
        for s, _ in cells:
            if s not in hit:
                problems.append(f"{tag}: size {s} has no quantity under it")
        for s, q in hit.items():
            if q:
                sizes[s] = sizes.get(s, 0) + q
    amount = eur(head[-1]["text"])
    made = re.search(r"Made-in\s*(.*?)\s*Composition\s*(.*?)\s*(?:PO Line|$)", " ".join(rest))
    if style is None or price is None or qty is None:
        return None, problems
    return dict(line_no=n, style=style, fabric=one(X_VARIANT), colour=one(X_COLOUR), colour_code=one(X_COLOUR),
                colour_name=" ".join(cname) or None, product_class=" ".join(desc) or None,
                description=" ".join(desc) or None, product_line=prod_line,
                product_line_code=head[0]["text"], grid=one(X_GRID), um=um, hs_code=hs,
                made_in=made.group(1) if made else None, composition=made.group(2) if made else None,
                sizes=sizes, size_run=run, qty=qty, price=price, line_value=amount,
                deliver_from=d["delivery_window"][0], deliver_to=d["delivery_window"][1], page=pno), problems


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
                out.append({"Order confirmation": d["doc"], "Collection": d["collection"],
                            "Line no.": l["line_no"], "Style": l["style"], "Fabric": l["fabric"],
                            "Colour": l["colour"], "Product Class": l["product_class"],
                            "Size": size, "Qty": q, f"Price {cur}": l["price"],
                            f"Value {cur}": q * l["price"]})
    return out


def totals(docs):
    lines = [l for d in docs for l in d["lines"]]
    return dict(docs=len(docs), lines=len(lines),
                units=sum(l["qty"] for l in lines),
                value=round(sum(l["line_value"] for l in lines), 2),
                printed_units=sum(d.get("total_qty") or 0 for d in docs),
                printed_value=round(sum(d.get("net") or 0 for d in docs), 2))
