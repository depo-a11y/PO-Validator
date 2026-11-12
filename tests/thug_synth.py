"""Synthetic THUG CLUB Corp. proforma invoices for the thugclub reader.

No real order data: every code, name, price and quantity is invented, and the address and bank lines are
placeholders. The text sits at the x positions measured on the two real templates the reader must cover:

    portrait: the header stacked over three rows with MSRP, the material on the row under each line, 0
    printed in an empty size cell, the header on page 1 only;
    landscape: one header row repeated on every page, the product name and composition wrapped onto the
    rows under a line, empty size cells left blank, a PAYMENT TERMS row above the totals.

Everything is drawn with oc_synth's writer (Helvetica, space glyphs drawn, so words split as on the real
PDFs).
"""
from oc_synth import Page, write

PORTRAIT_SIZES = ["OS", "1", "2", "3", "4"]
PORTRAIT_X = [368.65, 382.45, 396.25, 410.05, 423.85]
LANDSCAPE_SIZES = ["OS", "1", "2", "3", "4", "5"]
LANDSCAPE_X = [575.55, 592.2, 607.5, 621.15, 633.7, 646.1]

# (style, product name pieces, category, colour, composition pieces, {size: qty}, unit price, msrp)
PORTRAIT_LINES = [
    ("ZZ99JK0001", ["TEST DENIM JACKET"], "Jacket", "Blue", ["Cotton 100%"], {"2": 1, "3": 1}, 305.0, 640),
    ("ZZ99TS0002", ["TEST TEE"], "T-Shirt", "Black", ["Cotton 100%"], {"1": 2, "2": 3, "3": 2, "4": 1}, 52.25, 100),
    ("ZZ99AC0003", ["TEST CAP"], "Cap/Hat", "Black", ["Cotton 100%"], {"OS": 4}, 40.0, 95),
    ("ZZ99PT0004", ["TEST PANTS"], "Pants", "Black/White", ["Cotton 100% / Cowhide 100%"], {"1": 1, "2": 2}, 155.0,
     320),
]
LANDSCAPE_LINES = [
    ("ZZ99JK0101", ["TEST FIELD", "JACKET"], "JACKET", "BLACK", ["SHELL: COTTON 100%"], {"2": 1, "3": 1, "4": 1},
     313.0, None),
    ("ZZ99TS0102", ["TC TEST LOGO T-", "SHIRT"], "T-SHIRT", "CAMO",
     ["SHELL 1: COTTON 100% SHELL 2: COTTON 96%,", "SPAN 4%"], {"1": 2, "2": 4, "3": 4, "4": 2}, 37.0, None),
    ("ZZ99AC0103", ["TEST BEANIE"], "CAP", "BLACK", ["SHELL: COWHIDE 100%"], {"OS": 5}, 87.0, None),
    ("ZZ99PT0104", ["TEST WORK", "PANTS"], "PANTS", "BLACK",
     ["SHELL 1: COTTON 100% SHELL 2: COTTON 100%", "SHELL 3: COWHIDE 100%"], {"1": 1, "2": 2, "3": 1}, 302.0, None),
    ("ZZ99TS0105", ["TEST TANK TOP"], "T-SHIRT", "WHITE", ["SHELL: C94% / SP6%"], {"1": 2, "2": 2, "5": 1}, 49.0, None),
]


def usd(v):
    return f"${v:,.2f}"


def _issuer(p, x, top, number, title, landscape):
    p.text(x, top, "THUG CLUB CORP." if landscape else "THUG CLUB Corp.")
    p.text(x, top + 10, "1, Test-ro, Test-gu, Seoul, 00000, South Korea")
    p.text(x, top + 20, "Phone: +00-00-0000-0000")
    if landscape:
        p.text(350.6, 39.0, title, 12)
        p.text(494.4, top + 11, "INVOICE NUMBER:")
        p.text(625.0, top + 11, number)
        p.text(494.4, top + 28, "INVOICE DATE:")
        p.text(625.0, top + 28, "2099-09-02")
        p.text(494.4, top + 45, "ORDER DATE:")
        p.text(625.0, top + 45, "2099-07-08")
        p.text(494.4, top + 62, "DELIVERY WINDOW:")
        p.text(625.0, top + 62, "EARLY TO MID MARCH 2099")
    else:
        p.text(424.9, 182.6, "Invoice #")
        p.text(488.2, 182.1, number)
        p.text(10.9, 185.1, title, 12)
        p.text(415.7, 192.3, "Invoice Date")
        p.text(491.8, 191.9, "2099-03-14")
        p.text(10.2, 221.5, "Delivery Window")
        p.text(10.2, 231.2, "Mid to late August")
    p.text(x, top + 40 if not landscape else 216.7, "Ship-to")


def _portrait_header(p, sizes, xs, hs=True, word=None, no_sizes=False, currency="USD", msrp=True):
    p.text(391.0, 308.6, "Size")
    p.text(56.4, 308.6, "Product Name")
    if msrp:
        p.text(458.8, 308.6, "MSRP")
    p.text(489.1, 308.6, "Unit WSP")
    p.text(527.4, 308.6, "Total WSP")
    p.text(10.2, 313.3, "Style Code")
    p.text(203.8, 313.3, "Category")
    p.text(241.1, 313.3, "Color")
    if hs:
        p.text(283.7, 313.3, "HS Code")
        p.text(323.6, 313.3, "COO")
    if word:
        p.text(150.0, 313.3, word)
    p.text(434.2, 313.3, "Units")
    for xc in ((466.8, 502.2, 541.7) if msrp else (502.2, 541.7)):
        if currency:
            p.centre(xc, 318.5, f"({currency})")
    if not no_sizes:
        for s, x in zip(sizes, xs):
            p.centre(x, 318.5, s)
    p.text(56.2, 318.5, "Material")


def portrait_pdf(path, **kw):
    return write(path, portrait_pages(**kw))


def portrait_pages(lines=PORTRAIT_LINES, number="TCFW99P0001", title="PROFORMA INVOICE", sizes=None, hs=True,
                   shift=None, drop=None, bump_total=None, printed_units=None, printed_value=None, discount=False,
                   header_word=None, no_sizes=False, extra_page=False, cover=False, drop_last_page=False,
                   currency="USD", stray_wrap=None, values_no_style=False, per_page=3, no_header=False,
                   bad_style=None, after_totals=False):
    """Lines 1..per_page on page 1 under the only header, the rest and the totals on page 2.
    shift=(line, size, dx) moves one quantity; drop=(line, size) leaves one out; bump_total=line prints that
    line's total one dollar high; stray_wrap=line puts its material 20pt right of the Product Name column;
    values_no_style prints the second line's values with no style code; bad_style=(line, text) prints text in
    place of that line's style code; after_totals prints line 1 again under the bank details."""
    sizes = sizes or PORTRAIT_SIZES
    xs = PORTRAIT_X[:len(sizes)]
    p1, p2 = Page(595.2, 841.68), Page(595.2, 841.68)
    _issuer(p1, 10.2, 114.6, number, title, False)
    if not no_header:
        _portrait_header(p1, sizes, xs, hs=hs, word=header_word, no_sizes=no_sizes, currency=currency)
    units = value = 0
    tops = {}
    for n, (style, prod, cat, col, comp, qty, price, msrp) in enumerate(lines, 1):
        p, top = (p1, 328.4 + 19.5 * (n - 1)) if n <= per_page else (p2, 46.5 + 19.5 * (n - 1 - per_page))
        tops[n] = (p, top)
        if not (values_no_style and n == 2):
            p.text(10.2, top, bad_style[1] if bad_style and bad_style[0] == n else style)
        p.text(56.4, top, " ".join(prod))
        p.text(203.8, top, cat)
        p.text(241.1, top, col)
        if hs:
            p.text(283.7, top, "6100.00.0000", 5.5)   # the real face is narrower: its HS cell ends 6pt short of COO
            p.text(323.6, top, "South Korea")
        numeric = [s for s in sizes if s != "OS"]
        printed = {s: qty.get(s, 0) for s in (["OS"] if "OS" in qty else numeric)}
        if not no_sizes:
            for s, q in printed.items():
                if drop == (n, s):
                    continue
                dx = shift[2] if shift and shift[:2] == (n, s) else 0
                p.centre(xs[sizes.index(s)] + dx, top, str(q))
        u = sum(qty.values())
        total = u * price + (1 if bump_total == n else 0)
        units, value = units + u, value + u * price
        p.centre(441.25, top, str(u))
        if msrp:
            p.centre(467.2, top, f"${msrp}")
        p.centre(502.55, top, usd(price))
        p.centre(541.95, top, usd(total))
        p.text(56.2 + (20 if stray_wrap == n else 0), top + 10.3, " ".join(comp))
    last = p2
    top = 46.5 + 19.5 * max(0, len(lines) - per_page) + 2
    if discount:
        last.text(398.0, top, "Discount")
        last.centre(505.6, top, "$10.00")
        top += 9.8
    last.text(408.5, top, "Total Quantity")
    last.centre(505.6, top, str(units if printed_units is None else printed_units))
    last.text(409.9, top + 9.8, "Total Amount")
    last.centre(506.6, top + 9.8, usd(value if printed_value is None else printed_value))
    last.text(398.0, top + 19.5, "Deposit Due (30%)")
    last.centre(505.7, top + 19.5, usd(round(value * 0.3, 2)))
    last.text(10.2, top + 49.2, "Bank Name: TEST BANK")
    last.text(459.8, top + 49.2, "Order Date 2099-02-20")
    if after_totals:
        style, prod, cat, col, comp, qty, price, msrp = lines[0]
        last.text(10.2, top + 70, style)
        last.text(56.4, top + 70, " ".join(prod))
        last.centre(441.25, top + 70, str(sum(qty.values())))
        last.centre(502.55, top + 70, usd(price))
        last.centre(541.95, top + 70, usd(sum(qty.values()) * price))
    pages = [p1, p2]
    if drop_last_page:
        pages = [p1]
    if extra_page:
        p3 = Page(595.2, 841.68)
        p3.text(10.2, 50, "Terms of sale: test terms only")
        pages.append(p3)
    if cover:
        p0 = Page(595.2, 841.68)
        p0.text(10.2, 50, "Order recap for a test buyer")
        pages.insert(0, p0)
    return pages


def _landscape_header(p, top):
    for x, s in ((44.2, "STYLE CODE"), (97.5, "CATEGORY"), (153.5, "PRODUCT NAME"), (242.1, "COLOR"),
                 (346.1, "COMPOSITION"), (467.8, "HS CODE"), (531.4, "COO"), (656.3, "UNITS"),
                 (685.3, "UNIT PRICE (USD)"), (753.6, "TOTAL (USD)")):
        p.text(x, top, s)
    for s, x in zip(LANDSCAPE_SIZES, LANDSCAPE_X):
        p.centre(x, top, s)


def landscape_pdf(path, lines=LANDSCAPE_LINES, number="TCSS99P0001", title="PROFORMA INVOICE", per_page=(3, 2),
                  shift=None, printed_units=None, printed_value=None, drop_middle_page=False, payment_terms=True):
    """Lines spread over pages per `per_page`, each page under its own copy of the header; the totals after a
    PAYMENT TERMS row on the last page. Line 2's product name wraps mid-word ("T-" / "SHIRT")."""
    pages = [Page(841.92, 594.96) for _ in per_page]
    _issuer(pages[0], 39.7, 71.2, number, title, True)
    units = value = 0
    it = iter(enumerate(lines, 1))
    for k, (p, count) in enumerate(zip(pages, per_page)):
        htop = 282.0 if k == 0 else 36.7
        _landscape_header(p, htop)
        top = htop + 16.5
        for _ in range(count):
            n, (style, prod, cat, col, comp, qty, price, _msrp) = next(it)
            for x, s in ((44.2, style), (94.7, cat), (145.5, prod[0]), (225.7, col), (290.5, comp[0]),
                         (459.1, "6100.00.0000"), (515.3, "SOUTH KOREA")):
                p.text(x, top, s)
            for s, q in qty.items():
                dx = shift[2] if shift and shift[:2] == (n, s) else 0
                p.centre(LANDSCAPE_X[LANDSCAPE_SIZES.index(s)] + dx, top, str(q))
            u = sum(qty.values())
            units, value = units + u, value + u * price
            p.right(677.1, top, str(u))
            p.right(745.4, top, usd(price))
            p.right(797.2, top, usd(u * price))
            wraps = max(len(prod), len(comp)) - 1
            for j in range(1, wraps + 1):
                if j < len(prod):
                    p.text(145.5, top + 10.5 * j, prod[j])
                if j < len(comp):
                    p.text(290.5, top + 10.5 * j, comp[j])
            top += 16.5 + 10.5 * wraps
    last = pages[-1]
    if payment_terms:
        last.text(39.7, top + 4, "PAYMENT TERMS: 30% DEPOSIT UPON ORDER, 70% BEFORE SHIPMENT")
    last.text(44.2, top + 25, "TOTAL UNITS")
    last.right(797.2, top + 25, str(units if printed_units is None else printed_units))
    last.text(44.2, top + 42, "TOTAL PRICE")
    last.right(797.2, top + 42, usd(value if printed_value is None else printed_value))
    last.text(44.2, top + 59, "DEPOSIT DUE (30%)")
    last.right(797.2, top + 59, usd(round(value * 0.3, 2)))
    last.text(39.7, top + 85, "BANK NAME: TEST BANK")
    if drop_middle_page:
        pages = [pages[0], pages[-1]]
    return write(path, pages)


def two_docs_pdf(path, bump_second=False):
    """Two proformas in one PDF, each with its own totals; bump_second prints the second's units one high."""
    first = portrait_pages()
    second = portrait_pages(number="TCFW99P0002", lines=PORTRAIT_LINES[:2], per_page=1,
                            printed_units=sum(sum(l[5].values()) for l in PORTRAIT_LINES[:2]) + 1 if bump_second else None)
    return write(path, first + second)


def blank_pdf(path, n=1):
    """Pages with no text at all, as a scan with no text layer reads."""
    return write(path, [Page(595.2, 841.68) for _ in range(n)])
