"""adidas AG Luxury Fashion Sales Order Confirmation adapter (Y-3, Willy Chavarria, RS).

One confirmation per PDF ("Order Number : 900000001"), USD, landscape. Each
order line is one row plus a composition row under it:

    1  ZZ0003  Y-3 TEST SHOE FTWWHT/FTWWHT  9  7/1 8/2 9/3 10/2 11/1  100.00  900.00  0.00  900.00
               / U:LE L:LE S:RU / VN

(item no., article, description, quantity total, size/quantity cells, unit
price, total without discount, discount %, total incl. discount). The
composition row of the last line on a page can land at the top of the next.

The words are set with no space glyph, only a ~2.2pt gap, so pdfplumber's
default x_tolerance (3) runs a whole description into one token
("Y-3TESTCAPBLACK"). Words are read at x_tolerance=1, which splits at the gaps
and nowhere else.

Each size cell carries its own label ("S/3"), so a quantity cannot drift into
a neighbouring size the way a separate header row allows. Every token in the
size band must still read as SIZE/QTY, and nothing may sit in that band on a
composition row, or the line is refused rather than guessed.

The article number is unique per colourway: no fabric or colour code is
printed, and the line's fabric and colour are None. A non-zero discount is
refused; line_value is the printed total incl. discount, so the gate also fails.
"""
import re

from .otb import GateError, gate_problems  # noqa: F401  (GateError re-exported)

X_TOL = 1
ROW_TOL = 3.0
ARTICLE = re.compile(r"^[A-Z0-9]{6}$")
CELL = re.compile(r"^(.+)/(\d+)$")
MONEY = re.compile(r"^[\d,]+\.\d\d$")
X_DESC = (85, 300)
X_SIZES = (328, 560)
# right edges (x1) of the numeric columns, measured on the real layout
X1_QTY, X1_PRICE, X1_GROSS, X1_DISC, X1_NET = 320.3, 601.4, 672.2, 731.8, 802.6
X1_TOL = 6


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


def num(s):
    return float(s.replace(",", ""))


def at(row, x1, pat=MONEY):
    c = [w["text"] for w in row if abs(w["x1"] - x1) <= X1_TOL and re.fullmatch(pat, w["text"])]
    return c[0] if c else None


def is_line_head(row):
    return (len(row) >= 4 and row[0]["x1"] < 30 and row[0]["text"].isdigit()
            and 40 <= row[1]["x0"] < 50 and bool(ARTICLE.match(row[1]["text"])))


def parse(pdf_path):
    """Parse an adidas AG order-confirmation PDF. Returns (docs, problems)."""
    import pdfplumber

    docs, problems, d = [], [], None
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            rows = rows_of(page.extract_words(x_tolerance=X_TOL))
            t = " ".join(text(r) for _, r in rows)
            if "adidas AG" not in t or "Order Confirmation" not in t:
                continue
            m = re.search(r"(?<!Customer )Order Number\s*:\s*(\d+)", t)
            if m and (d is None or d["doc"] != m.group(1)):
                d = _new_doc(m.group(1), t)
                docs.append(d)
            if d is None:
                continue
            d["pages"].append(pno)
            body = False
            for _, row in rows:
                rt = text(row)
                if rt.startswith("Item Article Description"):
                    body = "header"
                    continue
                if body == "header":
                    if re.fullmatch(r"[A-Z]{3}( [A-Z]{3}| %)+", rt):
                        d["currency"] = d["currency"] or row[0]["text"]
                        body = True
                    continue
                if not body or "adidas AG is established" in rt:
                    body = False if "adidas AG is established" in rt else body
                    continue
                d["_stream"].append((pno, row))
            q = re.search(r"\bQuantity\s+([\d,]+)\s+Order\s+Value\s+([\d,]+\.\d\d)", t)
            if q:
                d["total_qty"], d["net"] = int(q.group(1).replace(",", "")), num(q.group(2))
            v = re.search(r"Total amount incl\. VAT:\s*([\d,]+\.\d\d)", t)
            if v:
                d["total_incl_vat"] = num(v.group(1))
    for d in docs:
        problems += _read(d, d.pop("_stream"))
        d["issuer"], d["label"] = "adidas AG", doc_label(d)
    return docs, problems + gate_problems(docs)


# The label a line is for, printed at the start of its description ("Y-3 DAD CAP", "WC ROSE TEE",
# "Willy Chavarria Meg", "RS STAN"). The free-text Customer Order Number is not used: it is typed by the
# account and one real confirmation carries no label in it at all.
LABELS = (("Y-3", re.compile(r"Y-3\b")), ("Willy Chavarria", re.compile(r"(?:WC|Willy Chavarria)\b")),
          ("RS", re.compile(r"RS\b")))


def doc_label(d):
    """Also applied to an AI read of an adidas brand, so both readers are held to the same oc_label rule."""
    return label_of(d["lines"])


def label_of(lines):
    """'Y-3', 'Willy Chavarria' or 'RS' from the line descriptions; 'Y-3 + RS' when a confirmation mixes labels,
    None when no line names one."""
    found = sorted({name for l in lines for name, pat in LABELS if pat.match((l.get("description") or "").strip())})
    return " + ".join(found) or None


def _new_doc(doc, t):
    def g(pat):
        m = re.search(pat, t)
        return m.group(1) if m else None
    return dict(doc=doc, date=g(r"Date of Order\s*:\s*([\d.]+)"),
                collection=g(r"Customer Order Number\s*:\s*(\S+)"),
                customer_number=g(r"Customer Number\s*:\s*(\d+)"),
                request_delivery=g(r"Request Delivery Date\s*:\s*([\d.]+)"),
                currency=None, total_qty=None, net=None, gross=None, total_incl_vat=None,
                gender=None, pages=[], lines=[], _stream=[])


def _read(d, stream):
    problems, i = [], 0
    while i < len(stream):
        pno, row = stream[i]
        if not is_line_head(row):
            if not re.search(r"\bQuantity\s+[\d,]+\s+Order\s+Value\b", text(row)):
                problems.append(f"{d['doc']} p{pno}: row outside any order line: {text(row)[:60]}")
            i += 1
            continue
        j = i + 1
        while j < len(stream) and not is_line_head(stream[j][1]) \
                and not re.search(r"\bOrder\s+Value\b", text(stream[j][1])):
            j += 1
        line, probs = _line(d, pno, row, [r for _, r in stream[i + 1:j]])
        problems += probs
        if line:
            d["lines"].append(line)
        i = j
    return problems


def _line(d, pno, head, cont):
    style = head[1]["text"]
    tag = f"{d['doc']} p{pno} line {head[0]['text']} {style}"
    problems = []
    desc = " ".join(w["text"] for w in head[2:] if w["x0"] >= X_DESC[0] and w["x1"] <= X_DESC[1])
    qty = at(head, X1_QTY, r"[\d,]+")
    price, gross, disc, net = at(head, X1_PRICE), at(head, X1_GROSS), at(head, X1_DISC), at(head, X1_NET)
    for name, v in (("quantity total", qty), ("unit price", price), ("total amount", gross),
                    ("discount %", disc), ("total incl. discount", net)):
        if v is None:
            problems.append(f"{tag}: no {name}")
    sizes = {}
    for w in head:
        if not (X_SIZES[0] <= w["x0"] < X_SIZES[1]):
            continue
        m = CELL.match(w["text"])
        if not m:
            problems.append(f"{tag}: size cell '{w['text']}'@{w['x0']:.0f} is not SIZE/QTY")
            continue
        if m.group(1) in sizes:
            problems.append(f"{tag}: size {m.group(1)} printed twice")
            continue
        sizes[m.group(1)] = int(m.group(2))
    if not sizes:
        problems.append(f"{tag}: no size cells")
    comp = []
    for row in cont:
        stray = [w["text"] for w in row if w["x0"] >= X_SIZES[0]]
        if stray:
            problems.append(f"{tag}: unexpected {' '.join(stray)} right of the composition row")
        comp += [w["text"] for w in row if w["x0"] < X_SIZES[0]]
    if disc is not None and num(disc) != 0:
        problems.append(f"{tag}: {disc}% discount; this reader does not apply discounts")
    if None in (qty, price, net):
        return None, problems
    raw = " ".join(comp)
    cm = re.fullmatch(r"/\s*(.*?)\s*/\s*([A-Z]{2})", raw)
    return dict(line_no=int(head[0]["text"]), style=style, fabric=None, colour=None, colour_code=None,
                description=desc, product_class=desc or None,
                composition=cm.group(1) if cm else (raw or None), made_in=cm.group(2) if cm else None,
                sizes={s: q for s, q in sizes.items() if q}, size_run=list(sizes),
                qty=int(qty.replace(",", "")), price=num(price), line_value=num(net),
                gross_value=num(gross) if gross else None, discount_pct=num(disc) if disc else None,
                deliver_from=d["request_delivery"], deliver_to=None, page=pno), problems


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
                            "Line no.": l["line_no"], "Style": l["style"], "Description": l["description"],
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
