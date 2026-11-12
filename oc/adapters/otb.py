"""OTB-group Order Confirmation adapter (Maison Margiela; MM6 shares the layout).

Ported from _outputs/2026-08-06-margiela-ss27-order-confirmation/parse_margiela_oc.py
with the hardcoded path removed. One PDF can bundle several confirmations (one
per collection), each with its own document number, line numbering and totals.

Each order line is a size matrix:

    1  S99WP0001  P0001  T0001  15/10/2026 15/02/2027  SANDAL
    41   42   43   44                    Tot. Qty   Price   Line value
     1    2    2    1                           6  100,00       600,00

Quantities are bucketed by the x-centre of the size header. Two layout traps:
shoe size scales run to x~190 (well past the RTW matrix), and bags/jewellery
style codes carry a letter in position 2 (SB1xx0000, SM1xx0000).

The OC prints only the ordered sizes; it does not carry the full size run.

Gates (all hard): each line's size cells sum to its printed Tot. Qty;
Tot. Qty x Price == Line value; per document the lines sum to the printed
Total order q.ty and Total net value. `gate_problems` re-derives all three from
the parsed data, so a mutated copy can be re-checked.

Number format, per document: S/S 26, F/W 25 and S/S 27 print 1.446,00 and
23/03/2026; F/W 26 prints 1,446.00 and 03/23/2026. Each confirmation's amounts
are read as printed and decide its format; amounts in both formats, or in
neither, refuse the confirmation. Dates come back day first either way.
"""
import re

LEFT_MAX = 380.0
TOL = 8.0
STYLE = re.compile(r"^[A-Z][A-Z0-9]{2}[A-Z]{2}\d{4}$")
X_TOTQ, X_PRICE, X_VAL = 412.0, 483.0, 588.0


class GateError(Exception):
    def __init__(self, problems):
        super().__init__(f"OC gate failed ({len(problems)}): " + "; ".join(problems[:5]))
        self.problems = problems


def xc(w):
    return (w["x0"] + w["x1"]) / 2.0


def eur(s):
    return float(s.replace(".", "").replace(",", "."))


AMOUNT = r"\d[\d.,]*[.,]\d\d"
COUNT = r"\d{1,3}(?:[.,]\d{3})+|\d+"
FORMATS = {"eu": (re.compile(r"\d{1,3}(?:\.\d{3})+,\d\d|\d+,\d\d"), ".", ","),
           "us": (re.compile(r"\d{1,3}(?:,\d{3})+\.\d\d|\d+\.\d\d"), ",", ".")}
NUMBER_FORMAT = "number format"   # the word service.py keys on: a format refusal is not a layout change
FORMAT_NAMES = {"eu": "1.234,56", "us": "1,234.56"}


def number_format(amounts):
    """'eu' (1.234,56) or 'us' (1,234.56) when every amount is printed in that one format; None when they mix
    formats or one is in neither. No amounts at all reads as 'eu', the format the reader always had."""
    kinds = {next((k for k, (pat, _, _) in FORMATS.items() if pat.fullmatch(a)), None) for a in amounts}
    return "eu" if not kinds else kinds.pop() if len(kinds) == 1 and None not in kinds else None


def money(s, fmt):
    _, thousands, decimal = FORMATS[fmt]
    return float(s.replace(thousands, "").replace(decimal, "."))


def count(s, fmt):
    """A printed quantity: digits grouped by the format's thousands mark only, else None."""
    _, thousands, decimal = FORMATS[fmt]
    return None if decimal in s else int(s.replace(thousands, ""))


def day_first(date, fmt):
    """'03/23/2026' in a 'us' document -> '23/03/2026'; None when it is not a date of that format."""
    m = re.fullmatch(r"(\d\d)/(\d\d)/(\d{4})", date or "")
    if not m:
        return date
    day, month = (m.group(2), m.group(1)) if fmt == "us" else (m.group(1), m.group(2))
    return f"{day}/{month}/{m.group(3)}" if 1 <= int(month) <= 12 and 1 <= int(day) <= 31 else None


def lines_of(page, ytol=2.0):
    out = {}
    for w in page.extract_words():
        out.setdefault(round(w["top"] / ytol) * ytol, []).append(w)
    return [(k, sorted(v, key=lambda w: w["x0"])) for k, v in sorted(out.items())]


def near(row, x, pat):
    c = [w for w in row if abs(xc(w) - x) < 18 and re.fullmatch(pat, w["text"])]
    return c[0]["text"] if c else None


def collection_gender(collection):
    c = (collection or "").upper()
    if "FEMALE" in c or "WOMEN" in c:
        return "WOMEN"
    if "MALE" in c or "MEN" in c:
        return "MEN"
    return None


def parse(pdf_path):
    """Parse an OTB order-confirmation PDF. Returns (docs, problems)."""
    import pdfplumber

    docs, problems = {}, []
    with pdfplumber.open(pdf_path) as pdf:
        cur = None
        for pno, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            m = re.search(r"Order confirmation\s+(SS\s*-\s*\S+|FW\s*-\s*\S+|[A-Z]{2}\s*-\s*\S+)\s+Of\s+(\d\d/\d\d/\d{4})", text)
            if m:
                cur = m.group(1).strip()
                d = docs.setdefault(cur, dict(doc=cur, date=m.group(2), lines=[],
                                              collection=None, pages=[], currency=None))
                d["pages"].append(pno)
            if cur is None:
                continue
            d = docs[cur]
            c = re.search(r"Collection\s+(.+)", text)
            if c and not d["collection"]:
                d["collection"] = c.group(1).strip()
            cur_m = re.search(r"Currency\s+(.+)", text)
            if cur_m and not d["currency"]:
                d["currency"] = cur_m.group(1).strip()
            pr = re.search(r"Proposal\s+(\d+)\s+Of\s+(\d\d/\d\d/\d{4})", text)
            if pr and not d.get("proposal"):
                d["proposal"] = pr.group(1)
            pa = re.search(r"Printed at\s+(\d\d/\d\d/\d{4})", text)
            if pa and not d.get("printed"):
                d["printed"] = pa.group(1)
            for key, pat in (("total_qty", r"Total order q\.ty\s+([\d.,]+)"),
                             ("gross", r"Total gross value\s+([\d.,]+)"),
                             ("net", r"Total net value\s+([\d.,]+)")):
                mm = re.search(pat, text)
                if mm:
                    d[key] = mm.group(1)   # as printed, read once the document's number format is known
            if "GENERAL TERMS" in text:
                continue

            rows = lines_of(page)
            heads = [i for i, (_, row) in enumerate(rows)
                     if len(row) >= 3 and row[0]["text"].isdigit()
                     and xc(row[0]) < 40 and STYLE.match(row[1]["text"])]
            for n, i in enumerate(heads):
                stop = heads[n + 1] if n + 1 < len(heads) else len(rows)
                head = rows[i][1]
                block = rows[i + 1:stop]
                tag = f"{cur} p{pno} line {head[0]['text']} {head[1]['text']}"

                dates = [w["text"] for w in head
                         if re.fullmatch(r"\d\d/\d\d/\d{4}", w["text"])]
                pclass = " ".join(w["text"] for w in head if xc(w) > 350)

                tq = price = val = None
                for _, row in block:
                    a = near(row, X_TOTQ, COUNT)
                    b = near(row, X_PRICE, AMOUNT)
                    c2 = near(row, X_VAL, AMOUNT)
                    if a and b and c2:
                        tq, price, val = a, b, c2
                if tq is None:
                    problems.append(f"{tag}: no totals")
                    continue

                groups = []
                for y, row in block:
                    left = [w for w in row if xc(w) < LEFT_MAX
                            and w["text"] not in ("Tot.", "Qty", "Price", "Line", "value")]
                    if left:
                        groups.append((y, left))
                if len(groups) < 2:
                    problems.append(f"{tag}: {len(groups)} left-band rows, need 2")
                    continue
                size_row, qty_row = groups[0][1], groups[1][1]
                size_cols = [(w["text"], xc(w)) for w in size_row]

                got = {}
                for w in qty_row:
                    if not w["text"].isdigit():
                        continue
                    best, dist = min(((s, abs(xc(w) - x)) for s, x in size_cols),
                                     key=lambda t: t[1])
                    if dist > TOL:
                        problems.append(f"{tag}: qty {w['text']}@{xc(w):.0f} is "
                                        f"{dist:.1f}pt from column {best}")
                        continue
                    got[best] = got.get(best, 0) + int(w["text"])

                d["lines"].append(dict(
                    line_no=int(head[0]["text"]), style=head[1]["text"],
                    fabric=head[2]["text"] if len(head) > 2 else None,
                    colour=head[3]["text"] if len(head) > 3 else None,
                    deliver_from=dates[0] if dates else None,
                    deliver_to=dates[1] if len(dates) > 1 else None,
                    product_class=pclass, sizes=got, qty=tq, price=price,
                    line_value=val, page=pno))

    out = list(docs.values())
    for d in out:
        d["gender"] = collection_gender(d["collection"])
        d["label"] = doc_label(d)
    unread = read_numbers(out)
    return out, problems + unread + gate_problems([d for d in out if not d.get("unread")])


def read_numbers(docs):
    """Read each document's amounts, quantities and dates in its own number format (in place). -> problems for
    the documents whose format cannot be told; those keep no lines and no totals, so nothing is guessed."""
    problems = []
    for d in docs:
        printed = [x for l in d["lines"] for x in (l["price"], l["line_value"])] + \
                  [d[k] for k in ("gross", "net") if d.get(k) is not None]
        fmt = number_format(printed)
        dates = [(d, k) for k in ("date", "printed") if d.get(k)] + \
                [(l, k) for l in d["lines"] for k in ("deliver_from", "deliver_to") if l.get(k)]
        counts = [c for c in [d.get("total_qty")] + [l["qty"] for l in d["lines"]] if c is not None]
        why = None
        if fmt is None:
            seen = {}
            for x in printed:
                seen.setdefault(next((k for k, (pat, _, _) in FORMATS.items() if pat.fullmatch(x)), None), x)
            why = "its amounts are printed " + " and ".join(
                f"as {FORMAT_NAMES[k]} ('{x}')" if k else f"in neither format ('{x}')" for k, x in seen.items())
        elif any(day_first(o[k], fmt) is None for o, k in dates):
            bad = next(o[k] for o, k in dates if day_first(o[k], fmt) is None)
            why = f"its amounts are printed as {FORMAT_NAMES[fmt]} but date '{bad}' is not a date in that format"
        elif any(count(c, fmt) is None for c in counts):
            bad = next(c for c in counts if count(c, fmt) is None)
            why = f"its amounts are printed as {FORMAT_NAMES[fmt]} but quantity '{bad}' is not"
        if why:
            problems.append(f"{d['doc']}: {NUMBER_FORMAT}: {why}, so the tool will not guess how to read it. "
                            "Ask Colwyn")
            d.update(unread=True, lines=[], total_qty=None, gross=None, net=None)
            continue
        for o, k in dates:
            o[k] = day_first(o[k], fmt)
        for k in ("gross", "net"):
            if d.get(k) is not None:
                d[k] = money(d[k], fmt)
        if d.get("total_qty") is not None:
            d["total_qty"] = count(d["total_qty"], fmt)
        for l in d["lines"]:
            l.update(qty=count(l["qty"], fmt), price=money(l["price"], fmt), line_value=money(l["line_value"], fmt))
    return problems


def doc_label(d):
    """The label a confirmation is for: its collection's first word ("MM" for Maison Margiela, "MM6" for MM6).
    Also applied to an AI read of an otb brand, so both readers are held to the same oc_label rule."""
    words = (d.get("collection") or "").split()
    return words[0] if words else None


NOT_ADDING_UP = ("Unless a line above could not be read, the confirmation's own lines do not add up to its printed "
                 "total: ask the brand for a corrected OC, or ask Colwyn")


def gate_problems(docs):
    """Re-derive the three gates from parsed data. [] means every gate holds. Money is worded in the
    document's own currency."""
    from ..util import iso_currency
    problems = []
    for d in docs:
        cur = (iso_currency(d.get("currency")) or "") + " "
        for l in d["lines"]:
            tag = f"{d['doc']} line {l['line_no']} {l['style']}"
            if sum(l["sizes"].values()) != l["qty"]:
                problems.append(f"{tag}: sizes sum {sum(l['sizes'].values())} != Tot. Qty {l['qty']}")
            if abs(l["qty"] * l["price"] - l["line_value"]) > 0.02:
                problems.append(f"{tag}: {l['qty']} x {l['price']} = {l['qty'] * l['price']:.2f} "
                                f"!= line value {l['line_value']}")
        if d.get("total_qty") is None or d.get("net") is None:
            problems.append(f"{d['doc']}: no printed totals")
            continue
        q = sum(sum(l["sizes"].values()) for l in d["lines"])
        v = sum(sum(l["sizes"].values()) * l["price"] for l in d["lines"])
        if q != d["total_qty"]:
            problems.append(f"{d['doc']}: its lines add up to {q} units, but it prints a total of {d['total_qty']}. "
                            f"{NOT_ADDING_UP}")
        if abs(v - d["net"]) > 0.02:
            problems.append(f"{d['doc']}: its lines add up to {cur.lstrip()}{v:,.2f}, but it prints a net total of "
                            f"{cur.lstrip()}{d['net']:,.2f}. {NOT_ADDING_UP}")
    return problems


def parse_checked(pdf_path):
    docs, problems = parse(pdf_path)
    if problems:
        raise GateError(problems)
    if not docs:
        raise GateError(["no order confirmations found"])
    return docs


def by_size_rows(docs):
    """One row per document/line/size, the extract's `by_size` sheet."""
    out = []
    for d in docs:
        for l in d["lines"]:
            for size, q in l["sizes"].items():
                out.append({"Order confirmation": d["doc"], "Collection": d["collection"],
                            "Line no.": l["line_no"], "Style": l["style"], "Fabric": l["fabric"],
                            "Colour": l["colour"], "Product Class": l["product_class"],
                            "Size": size, "Qty": q, "Price EUR": l["price"],
                            "Value EUR": q * l["price"]})
    return out


def totals(docs):
    lines = [l for d in docs for l in d["lines"]]
    return dict(docs=len(docs), lines=len(lines),
                units=sum(l["qty"] for l in lines),
                value=round(sum(l["line_value"] for l in lines), 2),
                printed_units=sum(d.get("total_qty") or 0 for d in docs),
                printed_value=round(sum(d.get("net") or 0 for d in docs), 2))
