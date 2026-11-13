"""Sportlux General Trading FZCO Order Confirmation adapter (Palm Angels).

The distributor confirms Palm Angels orders as a PDF titled "Order Confirmation", in EUR, one table row per
style and size. Two templates, told apart by the table header:

ss26, an ERP print, repeated with the address block on every page:

    Order No. : D-SO-999001
    SNo. | Style Code       | Description    | Category | Size | Qty. | Unit Price | Total Amount
    1    | ZXAA001S99JER001 | TEST LOGO TEE  | RTW      | S    | 2    | 43.21      | 86.42

fw26, an Excel print with Color and COO (country of origin) columns, no SNo. and no order number:

    Style Code         | Description | Category | Size | Color         | COO   | Qty. | Unit price | Total Amount
    ZXAA01W99JR019001  | TEST TEE    | RTW      | L    | BLACK - WHITE | ITALY | 2    | 241        | 482.00

Both draw the table's column rules (fw26 as thin rectangles over the whole table, ss26 as segments per row), and
centre each cell's words between them, so a long colour runs far past its header word. Columns are the bands
between the rules that cross the header row, the header words in each band must spell that template's titles
in order, and a word belongs to the band that holds it whole: a word across a rule, outside the table, or a row
whose own rule segments moved is refused, never moved to a neighbour.

The table body runs from the header to "Grand Total" (or the page footer). Every row in it must be an order row:
one code, a size, a whole quantity, a unit price and a total. The Style Code runs the colour code into the style:
its last four characters, when they are digits, are the colour code (`colour`, the join token) and the rest is
the style, so "ZXAA01W99JR019001" is style ZXAA01W99JR01, colour 9001. fw26 must print it (its Color column names
the colour, kept as `colour_name`); ss26 prints the style alone, so its lines carry no colour. Consecutive rows of
one style and colour form a line; a size printed twice starts a new one, because ss26 prints a style's colours as
consecutive blocks with nothing to tell them apart. Two blocks whose sizes do not repeat (XS S, then M L) read as
one line: no gate can see it, so every ss26 document carries a note saying so (`notes`, a Checks row). A line's
rows must share one unit price.

Gates: each row's Qty. x Unit price = its Total Amount (there is no printed quantity per style, so the line's
qty is the sum of its rows); ss26 row numbers run 1, 2, 3... with no gap; the lines sum to the printed Grand
Total units and to the Net Total value. A non-zero Discount is refused, not spread. Amounts are read only in the
form 1,234.56 or 1,234 (fw26 prints whole unit prices); any other form is a number-format refusal. An fw26 document
that repeats an earlier one line for line and total for total (the same confirmation printed twice: fw26 has no
order number to tell them apart) is refused.

Neither template prints a brand: the logo is Sportlux's and the descriptions name no label, so `label` is None
and a config holds its documents by `oc_supplier` (the issuer, the top row of the first page). The ss26 document
is its Order No.; fw26 prints none, so the document is named after its printed date.

Sportlux also sends PROFORMA INVOICE and INVOICE documents (same issuer, "DOCUMENT NO. : PI 999", a table headed
"SL. NO."). They print sizes and prices too, but they are payment requests: one for an advance on an order (which
can be a revision of it) and one per shipment, and nothing on them says which, so a shipment's proforma would draft
a PO for one delivery. They are refused by name.
"""
import re

from .otb import GateError, gate_problems, totals, xc  # noqa: F401  (GateError, totals re-exported)

ROW_TOL = 2.5
RULE_TOL = 1.5      # the two edges of one 0.6pt rule, and a row's own segments against the header's
FIT = 0.5           # a word may touch its rule by this much and still sit inside its column
TITLE = "Order Confirmation"
TEMPLATES = {
    "ss26": ("SNo.", "Style Code", "Description", "Category", "Size", "Qty.", "Unit Price", "Total Amount"),
    "fw26": ("Style Code", "Description", "Category", "Size", "Color", "COO", "Qty.", "Unit price", "Total Amount"),
}
HEADERS = {" ".join(cols): name for name, cols in TEMPLATES.items()}
FIELD = {"SNo.": "sno", "Style Code": "style", "Description": "description", "Category": "category",
         "Size": "size", "Color": "colour", "COO": "coo", "Qty.": "qty", "Unit Price": "price",
         "Unit price": "price", "Total Amount": "total"}
CODE = re.compile(r"^(?=.*\d)(?=.*[A-Z])[A-Z0-9]{8,}$")
COUNT = re.compile(r"^\d{1,3}(?:,\d{3})*$")
AMOUNT = re.compile(r"^\d{1,3}(?:,\d{3})*(?:\.\d\d)?$")
STOP = re.compile(r"^(?:Grand Total\b|This is an electronically generated|Page \d+ of \d+$)")
PAYMENT_TITLE = re.compile(r"^(PROFORMA INVOICE|INVOICE)$")
PAYMENT_NO = re.compile(r"DOCUMENT NO\.\s*:\s*(\S+(?: \d+)?)")
NO_TEXT = "the PDF has no text layer (a scan?); this reader reads the PDF's text, so nothing was read"
SS26_NOTE = ("{doc} prints no colour: two colour blocks of one style are told apart only where a size repeats, so "
             "blocks with different sizes (XS S, then M L) read as one line. Check each style's colours and sizes "
             "against the order")


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


def amount(s):
    return float(s.replace(",", ""))


def rules_across(edges, row):
    """x of each vertical rule that crosses `row` (the two edges of a thin rectangle count once)."""
    lo, hi = min(w["top"] for w in row), max(w["bottom"] for w in row)
    xs = sorted(e["x0"] for e in edges if e["orientation"] == "v" and e["top"] <= lo + 1 and e["bottom"] >= hi - 1)
    out = []
    for x in xs:
        if out and x - out[-1][-1] <= RULE_TOL:
            out[-1].append(x)
        else:
            out.append([x])
    return [sum(g) / len(g) for g in out]


def cells(row, rules):
    """Words of `row` per column band -> ({band: [words]}, [words outside every band or across a rule])."""
    out, stray = {}, []
    for w in row:
        k = next((i for i in range(len(rules) - 1) if rules[i] - FIT <= w["x0"] and w["x1"] <= rules[i + 1] + FIT),
                 None)
        if k is None:
            stray.append(w)
        else:
            out.setdefault(k, []).append(w)
    return out, stray


def _payment_doc(rows):
    """('PROFORMA INVOICE', 'PI 999') for a Sportlux payment document's page, else None."""
    texts = [text(r) for _, r in rows]
    title = next((t for t in texts if PAYMENT_TITLE.match(t)), None)
    number = next((m.group(1) for t in texts for m in [PAYMENT_NO.search(t)] if m), None)
    if title and number and any(t.startswith("SL. NO.") for t in texts):
        return title, number
    return None


def parse(pdf_path):
    """Parse a Sportlux order-confirmation PDF. Returns (docs, problems)."""
    import pdfplumber

    docs, problems, refused, orphans, cur, any_text = [], [], [], [], None, False
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            words = page.extract_words()
            any_text = any_text or bool(words)
            rows = rows_of(words)
            texts = [text(r) for _, r in rows]
            paid = _payment_doc(rows)
            if paid:
                msg = (f"{paid[0]} {paid[1]} is a payment request (Sportlux sends one for an advance and one per "
                       f"shipment), not the order confirmation, so it is refused; use the {TITLE} (sizes, prices and "
                       "totals of the whole order)")
                if not any(r.endswith(msg) for r in refused):
                    refused.append(f"p{pno}: {msg}")
                cur = "refused"
                continue
            head = next((i for i, t in enumerate(texts) if t in HEADERS), None)
            titled = TITLE in texts
            if head is None:
                if cur == "refused":
                    continue
                if cur is not None and cur["_open"]:
                    problems.append(f"{cur['doc']} p{pno}: no order table on this page, and the confirmation's "
                                    "totals are not printed yet")
                else:
                    orphans.append(pno)
                continue
            page_text = "\n".join(texts)
            order_no = _field(r"Order No\.\s*:\s*(\S+)", page_text)
            if isinstance(cur, dict) and cur["_open"] and order_no == cur["order_no"]:
                pass
            elif isinstance(cur, dict) and order_no is not None and order_no == cur["order_no"]:
                problems.append(f"{cur['doc']} p{pno}: a page of {cur['doc']} after its printed totals")
                continue
            elif titled:
                cur = _new_doc(rows, page_text, order_no, HEADERS[texts[head]], docs)
                docs.append(cur)
            else:
                orphans.append(pno)
                continue
            if HEADERS[texts[head]] != cur["template"]:
                problems.append(f"{cur['doc']} p{pno}: a {HEADERS[texts[head]]} table header on a "
                                f"{cur['template']} confirmation (layout changed)")
                continue
            cur["pages"].append(pno)
            problems += _page(cur, pno, rows, head, page.edges)
    if not any_text:
        return [], [NO_TEXT]
    if docs or refused:
        problems += [f"p{p}: belongs to no Sportlux order confirmation" for p in orphans]
    seen = {}
    for d in docs:
        problems += _lines(d, d.pop("_rows"))
        d.pop("_open")
        if d["template"] == "ss26":
            d["notes"].append(SS26_NOTE.format(doc=d["doc"]))
        elif d["order_no"] is None:
            sig = (d["total_qty"], d["net"], tuple((l["style"], l["colour"], tuple(l["sizes"].items()), l["price"])
                                                   for l in d["lines"]))
            if sig in seen:
                problems.append(f"{d['doc']}: repeats {seen[sig]} line for line and total for total (the same "
                                "confirmation printed twice?), so it is refused")
            seen.setdefault(sig, d["doc"])
        if d["discount"]:
            problems.append(f"{d['doc']}: Discount {d['discount']:,.2f} is not applied to any line; the lines sum "
                            "to the Grand Total, not the Net Total")
        elif d["gross"] is not None and d["net"] is not None and abs(d["gross"] - d["net"]) > 0.005:
            problems.append(f"{d['doc']}: Grand Total {d['gross']:,.2f} and Net Total {d['net']:,.2f} differ with "
                            "no discount printed")
    return docs, refused + problems + gate_problems(docs)


def _field(pat, t):
    m = re.search(pat, t, re.M)
    return " ".join(m.group(1).split()) if m and m.group(1).strip() else None


def _new_doc(rows, t, order_no, template, docs):
    title_top = next(top for top, r in rows if text(r) == TITLE)
    issuer = text(rows[0][1]) if rows[0][0] < title_top else None
    date = _field(r"Date\s*:\s*([^\n]*?)(?:\s+(?:Payment|Order|Currency)\b|$)", t) or None
    doc = order_no or f"{TITLE} of {date or 'no date'}"
    n = sum(1 for d in docs if d["doc"] == doc or d["doc"].startswith(doc + " #"))
    return dict(doc=doc if not n else f"{doc} #{n + 1}", order_no=order_no, template=template, issuer=issuer,
                label=None, date=date, currency=_field(r"Currency\s*:\s*([A-Z]{3})\b", t),
                payment_terms=_field(r"Payment Terms\s*:\s*([^\n]+)", t),
                delivery_terms=_field(r"Delivery [Tt]erms\s*:?\s*([^\n]+)", t),
                collection=None, season=None, gender=None, total_qty=None, gross=None, discount=None, net=None,
                pages=[], lines=[], notes=[], _rows=[], _open=True)


def _page(d, pno, rows, head, edges):
    """Read one page's order rows into d['_rows'] and any printed totals into d. -> problems."""
    cols = TEMPLATES[d["template"]]
    tag = f"{d['doc']} p{pno}"
    hrow = rows[head][1]
    rules = rules_across(edges, hrow)
    if len(rules) != len(cols) + 1:
        return [f"{tag}: {len(rules)} column rules across the table header, expected {len(cols) + 1} "
                "(layout changed)"]
    got, stray = cells(hrow, rules)
    titles = [" ".join(w["text"] for w in got.get(k, [])) for k in range(len(cols))]
    if stray or tuple(titles) != cols:
        return [f"{tag}: table header columns read {titles}, expected {list(cols)} (layout changed)"]
    problems, body = [], rows[head + 1:]
    stop = next((i for i, (_, r) in enumerate(body) if STOP.match(text(r))), len(body))
    for top, row in body[:stop]:
        own = rules_across(edges, row)
        if own and (len(own) != len(rules) or any(abs(a - b) > RULE_TOL for a, b in zip(own, rules))):
            problems.append(f"{tag}: the column rules of row '{text(row)[:40]}' are not the header's (layout changed)")
            continue
        r, probs = _row(d, tag, row, rules, cols)
        problems += probs
        if r:
            d["_rows"].append(dict(r, page=pno))
    if stop < len(body) and text(body[stop][1]).startswith("Grand Total"):
        problems += _totals(d, tag, [text(r) for _, r in body[stop:]])
    return problems


def _row(d, tag, row, rules, cols):
    """One table row -> (row dict | None, problems)."""
    got, stray = cells(row, rules)
    if stray:
        return None, [f"{tag}: {' '.join(w['text'] for w in stray)!r} sits across a column rule or outside the table "
                      f"in row '{text(row)[:40]}'"]
    v = {FIELD[c]: [w["text"] for w in got.get(k, []) if w["text"] != "€"] for k, c in enumerate(cols)}
    style = v["style"][0] if len(v["style"]) == 1 else None
    tag = f"{tag} row '{' '.join(v['sno'])}' {style or text(row)[:40]}" if "sno" in v else \
        f"{tag} {style} {' '.join(v['size'])}"
    one = {k: v[k][0] if len(v[k]) == 1 else None for k in ("qty", "price", "total")}
    missing = [k for k in ("description", "category", "size") + tuple(k for k in ("colour", "coo") if k in v)
               if not v[k]] + [k for k, x in one.items() if x is None]
    if style is None or not CODE.match(style):
        missing.insert(0, "style code")
    if "sno" in v and not (len(v["sno"]) == 1 and v["sno"][0].isdigit()):
        missing.insert(0, "row number")
    if missing:
        return None, [f"{tag}: no {', '.join(missing)} in this order row: '{text(row)[:60]}'"]
    colour_code = style[-4:] if style[-4:].isdigit() else None
    if "colour" in v and colour_code is None:
        return None, [f"{tag}: the Style Code does not end in a 4-digit colour code, which this template prints"]
    problems = [f"{d['doc']}: number format: {name} '{x}' is not printed as 1,234.56, so the tool will not guess how "
                "to read it. Ask Colwyn" for name, x in (("unit price", one["price"]), ("total", one["total"]))
                if not AMOUNT.match(x)]
    if not COUNT.match(one["qty"]):
        problems.append(f"{tag}: quantity '{one['qty']}' is not a whole number")
    if problems:
        return None, problems
    qty, price, total = int(one["qty"].replace(",", "")), amount(one["price"]), amount(one["total"])
    if abs(qty * price - total) > 0.02:
        problems.append(f"{tag}: {qty} x {price:g} = {qty * price:.2f} != line value {total:.2f}")
    return dict(sno=int(v["sno"][0]) if "sno" in v else None, style=style[:-4] if colour_code else style,
                colour_code=colour_code, size=" ".join(v["size"]),
                description=" ".join(v["description"]), category=" ".join(v["category"]),
                colour_name=" ".join(v["colour"]) if "colour" in v else None,
                coo=" ".join(v["coo"]) if "coo" in v else None, qty=qty, price=price, total=total), problems


def _totals(d, tag, texts):
    """'Grand Total 12 € 1,234.00', 'Discount € 0.00', 'Net Total € 1,234.00' -> d (in place)."""
    pats = (("Grand Total", r"Grand Total (\S+) (?:€ )?(\S+)"), ("Discount", r"Discount (?:€ )?(\S+)"),
            ("Net Total", r"Net Total (?:€ )?(\S+)"))
    problems = []
    for label, pat in pats:
        t = next((t for t in texts if t.startswith(label)), None)
        m = re.fullmatch(pat, t or "")
        if not m:
            problems.append(f"{tag}: no '{label}' row in the totals ({t!r})")
            continue
        bad = [x for x in m.groups()[-1:] if not AMOUNT.match(x)] + [x for x in m.groups()[:-1] if not COUNT.match(x)]
        if bad:
            problems.append(f"{d['doc']}: number format: {label} '{bad[0]}' is not printed as 1,234.56 or 1,234, so the "
                            "tool will not guess how to read it. Ask Colwyn")
            continue
        if label == "Grand Total":
            d["total_qty"], d["gross"] = int(m.group(1).replace(",", "")), amount(m.group(2))
        else:
            d["discount" if label == "Discount" else "net"] = amount(m.group(1))
    d["_open"] = False
    return problems


def _lines(d, rows):
    """Consecutive rows of one style -> lines; a size printed twice starts the next line. -> problems."""
    problems, run = [], []

    def close():
        if run:
            d["lines"].append(_line(d, len(d["lines"]) + 1, run))

    if d["template"] == "ss26":
        nums = [r["sno"] for r in rows]
        gap = next(((a, b) for a, b in zip([0] + nums, nums) if b != a + 1), None)
        if gap:
            problems.append(f"{d['doc']}: row numbers run from {gap[0]} to {gap[1]} (a row not read, or one printed "
                            "twice)")
    for r in rows:
        if run and (r["style"] != run[0]["style"] or r["colour_code"] != run[0]["colour_code"]
                    or r["colour_name"] != run[0]["colour_name"] or r["size"] in {x["size"] for x in run}):
            close()
            run = []
        if run and r["price"] != run[0]["price"]:
            problems.append(f"{d['doc']} p{r['page']} {r['style']} {r['size']}: unit price {r['price']:g}, but "
                            f"the line's earlier sizes are priced {run[0]['price']:g}; the PO takes one unit price per "
                            "line")
            continue
        run.append(r)
    close()
    return problems


def _line(d, n, run):
    first = run[0]
    made = list(dict.fromkeys(r["coo"] for r in run if r["coo"]))
    return dict(line_no=first["sno"] if first["sno"] is not None else n, style=first["style"], fabric=None,
                colour=first["colour_code"], colour_code=first["colour_code"], colour_name=first["colour_name"],
                made_in=" / ".join(made) or None,
                description=first["description"], product_class=first["description"], category=first["category"],
                sizes={r["size"]: r["qty"] for r in run if r["qty"]}, size_run=[r["size"] for r in run],
                qty=sum(r["qty"] for r in run), price=first["price"], line_value=round(sum(r["total"] for r in run), 2),
                rrp=None, rrp_currency=None, deliver_from=None, deliver_to=None, page=first["page"],
                rows=[r["sno"] for r in run] if first["sno"] is not None else None)


def doc_label(d):
    """None: a Sportlux confirmation prints no label (its logo is Sportlux's). Also applied to an AI read of a
    sportlux brand, so both readers are held to the same oc_label rule."""
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
        cur = d.get("currency") or "EUR"
        for l in d["lines"]:
            for size, q in l["sizes"].items():
                out.append({"Order confirmation": d["doc"], "Line no.": l["line_no"], "Style": l["style"],
                            "Colour": l["colour"], "Description": l["description"],
                            "Colour name": l["colour_name"], "Size": size,
                            "Qty": q, f"Price {cur}": l["price"], f"Value {cur}": q * l["price"]})
    return out
