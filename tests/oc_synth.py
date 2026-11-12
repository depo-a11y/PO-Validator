"""Synthetic order-confirmation PDFs for the fixed-layout adapters.

No real order data: every code, name, price and quantity here is invented. The
text is placed at the x positions measured on each real layout, so the parsers
run against the same geometry: AD Milano's fixed-pitch size grid, adidas's
words separated by a 2.2pt gap and no space glyph, FOG's per-line size headers
and its wrapped cells with their space glyphs drawn.

The writer needs nothing beyond pdfminer (which pdfplumber brings): Helvetica,
WinAnsi, one content stream per page.
"""
from pdfminer.fontmetrics import FONT_METRICS

_W = FONT_METRICS["Helvetica"][1]


def width(s, size):
    return sum(_W.get(c, 556) for c in s) * size / 1000.0


def _esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class Page:
    def __init__(self, w, h, size=7.0):
        self.w, self.h, self.size, self.ops = w, h, size, []

    def text(self, x, top, s, size=None):
        """Draw s with its left edge at x and its top at `top` (pdfplumber's coordinates)."""
        size = size or self.size
        y = self.h - top - 0.793 * size
        self.ops.append(f"BT /F1 {size:.1f} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({_esc(s)}) Tj ET")

    def centre(self, xc, top, s, size=None):
        self.text(xc - width(s, size or self.size) / 2, top, s, size)

    def right(self, x1, top, s, size=None):
        self.text(x1 - width(s, size or self.size), top, s, size)

    def words(self, x, top, ws, gap, size=None):
        """Words one after another, `gap` points apart, with no space glyph between them."""
        size = size or self.size
        for w in ws:
            self.text(x, top, w, size)
            x += width(w, size) + gap
        return x


def write(path, pages):
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for p in pages:
        body = "\n".join(p.ops).encode("cp1252")
        objs.append(b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream")
        objs.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /Font << /F1 3 0 R >> >> "
                     "/Contents %d 0 R >>" % (p.w, p.h, len(objs))).encode())
        kids.append(len(objs))
    objs[1] = ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(f"{k} 0 R" for k in kids), len(kids))).encode()
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for n, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % n + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    with open(path, "wb") as f:
        f.write(bytes(out))
    return path


# ---- AD Milano ----------------------------------------------------------------
AD_SIZE_X = [101.6 + 49.3 * k for k in range(12)]       # fixed origin, fixed pitch
AD_LINES = [  # (product line, line name, item, variant, colour, colour name, UM, sizes, qtys, price)
    ("TSTRTW", "TS RTW", "Z0000001", "FB001", "099", "BLACK", "PZ", ["XS", "S", "M", "L", "XL"], [1, 2, 2, 1, 1], "1.250"),
    ("TSTRTW", "TS RTW", "Z0000002", "FB002", "D0001", "OFF WHITE", "PZ", ["S", "M", "L"], [2, 1, 1], "300"),
    ("TSTSHO", "TS Shoes", "Z0000003", "LT001", "004", "CHALK", "PA", ["40", "41", "42"], [1, 1, 1], "200"),
]


def _eur(v):
    whole, cents = f"{v:.2f}".split(".")
    return f"{int(whole):,}".replace(",", ".") + "," + cents


def admilano_pdf(path, shift=None, drop=None, discount=0.0, lines=AD_LINES):
    """Three lines: line 2's grid is S M L at the x of line 1's XS S M, and line 3's
    grid sits on page 2, under a header and description printed on page 1.
    shift=(line, cell, dx) moves one quantity; drop=(line, cell) leaves one out."""
    p1, p2, p3 = (Page(841.9, 595.3) for _ in range(3))
    p1.text(340, 20, "ORDER CONFIRMATION")
    p1.text(20, 40, "AD Milano Srl ORDER PAYMENT DETAILS")
    p1.text(20, 60, "Season 2099 SS Event Main Date 01/02/2099")
    p1.text(20, 70, "Brand Test Label Payments 30 DAYS")
    p1.text(20, 80, "Ord. N° 9001 Support")
    p1.text(20, 90, "Customer TEST CUSTOMER Currency EUR")
    p1.text(20, 100, "Line Delivery Window 01/03/2099 - 01/05/2099")
    for words, x in (("Line", 21.8), ("Item", 78.0), ("Item", 149.1), ("Colour", 227.1), ("S/S Variant", 262.5),
                     ("Tag", 446.8), ("Grid", 482.2), ("Net Q.ty", 556.7), ("WHL Amount Total", 759.3)):
        p1.text(x, 238.6, words)
    pages, top, total = [p1, p2], 247.2, 0.0
    units = {}
    for n, (pl, pname, item, var, col, cname, um, sizes, qtys, price) in enumerate(lines, 1):
        unit = float(price.replace(".", "").replace(",", "."))
        amount = unit * sum(qtys)
        total += amount
        units[um] = units.get(um, 0) + sum(qtys)
        pg = p1
        for word, x in ((pl, 23.2), (item, 78.0), (var, 149.1), (col, 227.1), ("T001", 482.2), (um, 590.6),
                        ("EUR", 746.4)):
            pg.text(x, top, word)
        pg.right(585.7, top, str(sum(qtys)))
        pg.right(813.6, top, _eur(amount))
        pg.text(23.2, top + 8.1, pname)
        pg.text(78.0, top + 8.1, f"TEST GARMENT {n}")
        pg.text(227.1, top + 8.1, cname)
        pg.text(446.8, top + 8.1, "Made-in")
        pg.text(482.2, top + 8.1, "ITALY")
        pg.text(523.3, top + 8.1, "Composition 100% CO")
        pg.text(734.0, top + 8.1, "PO Line")
        pg.text(78.0, top + 16.1, "LONG NAME")
        if n == len(lines):           # the rest of the last line's block runs onto page 2
            pg, top = p2, 13.3
        else:
            top += 24.2
        pg.text(27.1, top, "HS/HTS Code: 61000000")
        pg.text(26.9, top + 15.4, "Size")
        pg.text(26.9, top + 23.7, "Q.ty")
        for k, (s, q) in enumerate(zip(sizes, qtys)):
            pg.centre(AD_SIZE_X[k], top + 15.6, s)
            if drop == (n, k):
                continue
            dx = shift[2] if shift and shift[:2] == (n, k) else 0
            pg.centre(AD_SIZE_X[k] + dx, top + 24.2, str(q))
        pg.text(27.2, top + 40.1, "Range")
        pg.centre(AD_SIZE_X[0], top + 40.3, "1")
        pg.text(27.2, top + 48.4, "WHL Price")
        pg.text(92.9, top + 50.3, "€")
        pg.text(98.8, top + 49.8, price)
        top += 72.8
    p2.text(496.2, top, "Line Net Total Amount WHL Amount Total")
    p2.text(494.9, top + 8.1, f"TSTRTW TS RTW EUR {_eur(total)} EUR {_eur(total)}")
    p2.text(21.8, top + 24.5, "Totals by order")
    p2.text(34.0, top + 32.6, "Total Quantity UM Net ordered")
    y = top + 40.6
    for um, q in units.items():
        p2.text(157.2, y, um)
        p2.right(267.9, y, str(q))
        y += 8.1
    p2.text(147.7, y + 7, "Currency Gross value Discounts value Net value + VAT")
    p2.text(34.3, y + 15.1, f"Total value in order currency EUR {_eur(total)} {_eur(discount)} {_eur(total - discount)}")
    p2.text(797.6, 558.1, "page 2")
    p3.text(20, 20, "Terms and Conditions")
    p3.text(20, 30, "GENERAL TERMS AND CONDITIONS OF SALE")
    p3.text(20, 40, "TSTRTW Z0000009 FB009 099 T001 9 PZ EUR 9.999,00")
    pages.append(p3)
    return write(path, pages)


# ---- adidas -----------------------------------------------------------------
GAP = 2.2   # the real layout's word gap: wider than 1pt, narrower than pdfplumber's default 3pt
AD_X1 = dict(qty=320.3, price=601.4, gross=672.2, disc=731.8, net=802.6)
ADIDAS_LINES = [  # (article, description words, cells, price, composition words)
    ("ZZ0001", ["Y-3", "TEST", "CAP", "BLACK"], ["OSFM/4"], 30.0, ["/", "100%", "COTTON", "/", "CN"]),
    ("ZZ0002", ["TEST", "TEE", "BLACK"], ["S/1", "M/3", "L/2"], 40.0, ["/", "100%", "COTTON", "/", "TH"]),
    ("ZZ0003", ["Y-3", "TEST", "SHOE", "FTWWHT/FTWWHT"], ["7/1", "8/2", "9/3", "10/2", "11/1"], 100.0,
     ["/", "U:LE", "L:TE", "S:RU", "/", "VN"]),
    ("ZZ0004", ["TEST", "HOODIE", "BLACK"], ["S/5", "M/5"], 100.0, ["/", "100%", "COTTON", "/", "CN"]),
]


def _usd(v):
    return f"{v:,.2f}"


def _adidas_header(p, top):
    p.words(8.2, top, ["Item"], GAP, 8)
    p.words(47.1, top, ["Article"], GAP, 8)
    p.words(158.6, top, ["Description"], GAP, 8)
    p.words(283.7, top, ["Quantity"], GAP, 8)
    p.words(403.0, top, ["Size", "/", "Quantity"], GAP, 8)
    p.words(554.5, top, ["Unit", "Price"], GAP, 8)
    p.words(617.8, top, ["Total", "Amount"], GAP, 8)
    p.words(692.5, top, ["Discount"], GAP, 8)
    p.words(748.3, top, ["Total", "Amount"], GAP, 8)
    p.words(10.0, top + 12, ["No."], GAP, 8)
    p.words(53.8, top + 12, ["No"], GAP, 8)
    p.words(290.4, top + 12, ["Total"], GAP, 8)
    for x, w in ((565.8, "USD"), (635.4, "USD"), (706.1, "%"), (765.7, "USD")):
        p.text(x, top + 24, w, 8)


def _adidas_footer(p):
    p.words(2.9, 543.5, ["adidas", "AG", "is", "established", "in", "Germany"], GAP, 8)
    p.words(2.9, 555.5, ["The", "general", "terms", "and", "conditions", "shall", "apply."], GAP, 8)


def adidas_pdf(path, discount=None, stray=None, bad_cell=None, wrap=None, lines=ADIDAS_LINES, stray_text="XL/1"):
    """Three lines on page 1 (the third's composition row on page 2) and one on page 2.
    discount=(line, pct); stray=line: a size cell on that line's composition row;
    bad_cell=(line, text): that line's first cell printed as `text`; wrap=line: that line's last
    cell printed on its composition row instead (as on the real Y-3 S/S 25 OC)."""
    p1, p2 = Page(841.92, 595.32, 8), Page(841.92, 595.32, 8)
    p1.words(11.4, 7.8, ["adidas", "AG"], GAP)
    p1.words(11.4, 19.8, ["Luxury", "Fashion", "Sales"], GAP)
    for p, n in ((p1, 1), (p2, 2)):
        p.words(478.9, 47.4, ["page"], GAP)
        p.words(530.2, 49.2, ["Order", "Confirmation"], GAP)
        p.words(476.4, 59.4, [f"{n}/", "2"], GAP)
    for top, label, value in ((90.7, ["Customer", "Number"], "100000001"), (102.7, ["Order", "Number"], "900000001"),
                              (114.7, ["Date", "of", "Order"], "01.01.2099"),
                              (126.7, ["Customer", "Order", "Number"], "SYN_TEST_ORDER"),
                              (138.7, ["Request", "Delivery", "Date"], "01.06.2099")):
        p1.words(396.8, top, label, GAP)
        p1.text(501.7, top, ":")
        p1.text(506.3, top, value)
    _adidas_header(p1, 203.4)
    _adidas_header(p2, 123.9)
    tops = {1: (p1, 272.9), 2: (p1, 315.6), 3: (p1, 358.3), 4: (p2, 193.5)}
    units = value = 0
    for n, (art, desc, cells, price, comp) in enumerate(lines, 1):
        p, top = tops[n]
        cells = list(cells)
        if bad_cell and bad_cell[0] == n:
            cells[0] = bad_cell[1]
        qty = sum(int(c.rpartition("/")[2]) for c in lines[n - 1][2])
        gross = qty * price
        pct = discount[1] if discount and discount[0] == n else 0.0
        net = round(gross * (1 - pct / 100), 2)
        units, value = units + qty, value + net
        p.right(25.5, top, str(n))
        p.text(45.4, top, art)
        p.words(93.6, top, desc, GAP)
        p.right(AD_X1["qty"], top, str(qty))
        p.words(336.1, top, cells[:-1] if wrap == n else cells, 4.5)
        for key, v in (("price", price), ("gross", gross), ("disc", pct), ("net", net)):
            p.right(AD_X1[key], top, _usd(v))
        cp, ct = (p2, 165.0) if n == 3 else (p, top + 14.2)
        cp.words(95.9, ct, comp, GAP)
        if stray == n:
            cp.text(336.1, ct, stray_text)
        if wrap == n:
            cp.text(336.1, ct, cells[-1])
    p2.words(93.6, 492.7, ["T60", "1", "10", "days", "1%,", "net", "60", "days", "Quantity"], GAP)
    p2.right(320.3, 492.7, str(units))
    p2.words(560.4, 492.7, ["Order", "Value"], GAP)
    p2.right(802.5, 492.7, _usd(value))
    _adidas_footer(p1)
    _adidas_footer(p2)
    p2.words(650.4, 558.5, ["Total", "amount", "incl.", "VAT:"], GAP)
    p2.right(802.4, 558.5, _usd(value))
    return write(path, [p1, p2])


# ---- Fear of God ------------------------------------------------------------
FOG_COLS = (("SELL", 41.6), ("STYLE", 73.6), ("FABRIC", 121.6), ("CONTENT", 161.6), ("COLOR", 213.6),
            ("GROUP", 253.6), ("SIZES", 407.5), ("&", 427.7), ("UNITS", 434.8), ("UNITS", 583.2),
            ("PRICE", 625.9), ("MSRP", 674.2), ("TOTAL", 720.7))
FOG_X = dict(units=594.0, price=636.0, msrp=684.0, total=732.0)
FOG_LINES = [  # (style, colour code, fabric lines, colour lines, sizes, x-centres, qtys, price, msrp)
    ("ZZ00001", "0001", ["100% ", "COTTON"], ["WASHE", "D INK ", "BLACK"],
     ["2XS", "XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL"], [304.0 + 32 * k for k in range(9)],
     [0, 2, 6, 6, 3, 0, 0, 0, 0], 10.0, 25.0),
    ("ZZ00002", "0002", ["100% ", "COTTON"], ["BLACK/", "CREAM ", "LOGO"],
     ["S", "M", "L"], [304.0, 336.0, 368.0], [2, 1, 1], 50.0, 130.0),
    ("ZZ00003", "0003", ["87% ", "NYLON, ", "13% ", "ELASTANE"], ["DEEP ", "BROWN"],
     ["O/S"], [432.0], [3], 20.0, None),
]


def _fog_page_header(p, brand, order_alone=False):
    p.text(333.0, 50.5, "Order Confirmation")
    p.text(40.8, 72.7, "FROM")
    p.text(220.8, 72.7, "SHIP TO")
    p.text(400.8, 72.7, "BILL TO")
    p.text(41.6, 88.0, brand)
    p.text(221.6, 88.0, "Test Customer")
    if order_alone:   # S/S 26: ORDER # on its own row, the number at the end of an address row
        p.text(648.7, 91.2, "ORDER #")
        p.text(644.6, 104.0, "SO9999999")
        p.text(41.6, 96.0, "558 Test Street")
        p.text(41.6, 112.0, "Test Country")
        p.text(586.4, 121.7, "Customer PO # 12345678")
        p.text(586.4, 137.0, "SHIP TERMS: EXW TEST")
        return
    p.text(586.4, 91.2, "ORDER # SO9999999")
    p.text(41.6, 96.0, "1 Test Street")
    p.text(586.4, 106.4, "Customer PO # 12345678")
    p.text(41.6, 112.0, "Test Country")
    p.text(586.4, 121.7, "SHIP TERMS: EXW TEST")


def _fog_block(p, top, sizes, xs):
    for word, x in FOG_COLS:
        p.text(x, top, word)
    p.text(41.6, top + 7.0, "PRD")
    for s, x in zip(sizes, xs):
        p.centre(x, top + 15.1, s)


def fog_pdf(path, shift=None, drop=None, discount=0.0, brand="TEST Essentials", lines=FOG_LINES, order_alone=False,
            fabric=None):
    """Lines 1 and 2 on page 1; line 3's table header and size header at the foot of
    page 1, its row on page 2 under the repeated address block, and no MSRP.
    Line 2's S M L header sits at the x of line 1's 2XS XS S. The S/S 26 shapes: a style given as a
    tuple of rows is printed wrapped, a colour code of None prints ': NAME', `fabric` prints a fabric word
    left of the content, and order_alone prints ORDER # on its own row."""
    p1, p2 = Page(792, 612), Page(792, 612)
    _fog_page_header(p1, brand, order_alone)
    _fog_page_header(p2, brand, order_alone)
    for word, x in (("ORDER DATE", 47.0), ("START SHIP DATE", 122.1), ("SEASON", 644.1), ("CONTACT", 701.0)):
        p1.text(x, 193.7, word)
    p1.text(41.6, 209.8, "1/2/2099")
    p1.text(113.6, 209.8, "2/3/2099")
    p1.text(641.6, 209.8, "WINTER")
    p1.text(641.6, 217.8, "2099")
    for word, x in (("CANCEL DATE", 45.0), ("CURRENCY", 134.6), ("FACTOR", 644.3), ("GROUP", 705.7)):
        p1.text(x, 237.8, word)
    p1.text(41.6, 253.9, "3/4/2099")
    p1.text(113.6, 253.9, "US Dollar")
    where = [(p1, 297.8, p1, 327.9), (p1, 376.0, p1, 406.1), (p1, 520.0, p2, 193.6)]
    units = value = 0
    for n, ((style, code, fab, col, sizes, xs, qtys, price, msrp), (hp, ht, lp, lt)) in enumerate(zip(lines, where), 1):
        _fog_block(hp, ht, sizes, xs)
        for k, part in enumerate((style,) if isinstance(style, str) else style):
            lp.text(73.6, lt + 7.0 * k, part)
        lp.text(213.6, lt, f"{code} : " if code is not None else ": ")
        if fabric:
            lp.text(121.6, lt, fabric)
        for k, (x, q) in enumerate(zip(xs, qtys)):
            if drop == (n, k):
                continue
            dx = shift[2] if shift and shift[:2] == (n, k) else 0
            lp.centre(x + dx, lt, str(q))
        qty = sum(qtys)
        units, value = units + qty, value + qty * price
        lp.centre(FOG_X["units"], lt, str(qty))
        lp.centre(FOG_X["price"], lt, f"${price:,.2f}")
        if msrp:
            lp.centre(FOG_X["msrp"], lt, f"${msrp:,.2f}")
        lp.centre(FOG_X["total"], lt, f"${qty * price:,.2f}")
        for k, f in enumerate(fab):
            lp.text(161.6, lt + 7.0 * k, f)
        for k, c in enumerate(col):
            lp.text(213.6, lt + 7.0 * (k + 1), c)
    for k, (label, v) in enumerate((("Total Units:", str(units)), ("Subtotal:", f"${value:,.2f}"),
                                    ("Discount Total:", f"${discount:,.2f}"), ("Tax Total:", "$0.00"),
                                    ("Total Amount:", f"${value - discount:,.2f}"))):
        p2.text(542.5, 300 + 16 * k, label)
        p2.right(750.3, 300 + 16 * k, v)
    p2.text(41.6, 400, "No Changes can be made to Sales Orders after 5 days of buyer signing order confirmations.")
    return write(path, [p1, p2])


# ---- OTB (Maison Margiela, MM6) -------------------------------------------------
OTB_LINES = [  # (style, fabric, colour, product class, sizes, qtys, price)
    ("S99WS0001", "P0001", "T0001", "SNEAKERS", ["41", "42", "43"], [1, 2, 1], 1250.0),
    ("S99WR0002", "P0002", "H0002", "MOCASSIN", ["40", "43"], [2, 2], 312.5),
]
OTB_DATES = ("15/01/2099", "01/02/2099", "15/07/2099", "15/10/2099")   # confirmation, printed, deliver from, to


def otb_amount(v, fmt):
    whole, cents = f"{v:.2f}".split(".")
    return f"{int(whole):,}".replace(",", ".") + "," + cents if fmt == "eu" else f"{int(whole):,}.{cents}"


def otb_date(day_first, fmt):
    d, m, y = day_first.split("/")
    return f"{m}/{d}/{y}" if fmt == "us" else day_first


def otb_pdf(path, fmt="eu", odd=None, dates=OTB_DATES, lines=OTB_LINES):
    """One confirmation in `fmt` ('eu' 1.234,56 and 23/03/2026, 'us' 1,234.56 and 03/23/2026), at the x positions
    of the real layout. `odd`: line 1's value printed in that other format. `dates` are given day first."""
    p = Page(612, 792)
    conf, printed, frm, to = (otb_date(x, fmt) for x in dates)
    p.text(40.0, 40.0, f"Printed at {printed}")
    p.text(40.0, 60.0, f"Order confirmation SS - 2099/01-62-0001 Of {conf}")
    p.text(40.0, 80.0, "Agent/Seller TS0001 TEST S.A. Currency European Euro")
    p.text(40.0, 100.0, "Collection MM MALE SHOES AVP")
    top, units, value = 150.0, 0, 0.0
    for n, (style, fab, col, pclass, sizes, qtys, price) in enumerate(lines, 1):
        p.text(19.4, top, str(n))
        for x, word in ((98.4, style), (189.0, fab), (222.1, col), (254.7, frm), (305.2, to), (359.6, pclass)):
            p.text(x, top, word)
        for x, word in ((385.3, "Tot."), (402.2, "Qty"), (477.0, "Price"), (566.3, "Line"), (584.9, "value")):
            p.text(x, top + 15.5, word)
        for k, (s, q) in enumerate(zip(sizes, qtys)):
            xc = 14.9 + 21.55 * k
            p.centre(xc, top + 16.5, s)
            p.centre(xc, top + 29.3, str(q))
        qty = sum(qtys)
        units, value = units + qty, value + qty * price
        p.right(413.9, top + 28.1, str(qty))
        p.right(495.0, top + 28.1, otb_amount(price, fmt))
        p.right(603.8, top + 28.1, otb_amount(qty * price, odd if odd and n == 1 else fmt))
        top += 44.0
    p.text(40.0, top + 20, f"Total order q.ty {units}")
    p.text(40.0, top + 32, f"Total gross value {otb_amount(value, fmt)}")
    p.text(40.0, top + 44, f"Total net value {otb_amount(value, fmt)}")
    return write(path, [p])
