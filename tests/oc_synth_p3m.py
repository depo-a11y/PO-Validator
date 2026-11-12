"""Synthetic "#3xxxxxx" ordering-platform (P3M) order confirmations, for tests/test_oc_p3m.py.

No real order data: every company, code, name, price and quantity is invented. Text sits at the x
positions measured on the real layouts: the grid layout's size cells at a fixed 22.4pt pitch from the
SIZES header (so "XS-S S-M M-L" shares its cells with "XXS XS S"), its colour row 3.5pt above the
quantities, labels that fill their cell and run together at the default x_tolerance, a label cut short
("one s…"); the inline layout's cover page, centred money columns, a size header that wraps onto a
second row and an item name drawn over its own size header.
"""
from oc_synth import Page, width, write

H = 842.0
SIZES_X, PITCH = 175.3, 22.4
X1 = dict(qty=452.9, price=503.5, amount=553.9)


def cell_x(k):
    return SIZES_X + (k + 0.5) * PITCH


GRID_ITEMS = [  # (name, fields, size labels, label font size, colours, subtotal)
    ("TEST TEE", [[("COLLECTION", "AW99 UNISEX")], [("CATEGORY", "T-SHIRT")], [("ORIGIN", "USA")]],
     ["XXS", "XS", "S", "M", "L", "XL", "XXL"], 6.72,
     [("BLACK", {"S": 1, "M": 2, "L": 1}, 101, "ZZTE001-001N-001", ["100% COTTON"], "$255")]),
    ("TEST SOCKS", [[("COLLECTION", "AW99 UNISEX")], [("CATEGORY", "ACCESSORIES")]],
     ["XS-S", "S-M", "M-L"], 6.72,
     [("black", {"S-M": 5, "M-L": 5}, 7, "ZZSO001_black", ["78% cotton, 20% polyester,", "2% elastane"], "$15"),
      ("white", {"S-M": 5, "M-L": 5}, 7, "ZZSO001_white", ["78% cotton"], "$15")]),
    ("TEST JEAN", [[("COLLECTION", "CORE"), ("PRINT/FABRIC", "Cotton")], [("CATEGORY", "Denim")], [("ORIGIN", "US")]],
     ["28x34", "29x34", "30x34", "31x34"], 7.6,
     [("Black Wax", {"29x34": 1, "30x34": 2, "31x34": 1}, 303, "ZZDN001", ["92% COTTON"], "$1,105"),
      ("Black Rinse", {"28x34": 1, "29x34": 1}, 303, "ZZDN002", ["92% COTTON"], "$1,105")]),
    ("TEST CAP", [[("COLLECTION", "AW99 ACCESSORIES")], [("CATEGORY", "HEADWEAR")], [("ORIGIN", "CHINA")]],
     ["one s…"], 6.72,
     [("navy", {"one s…": 3}, 21, "ZZCP001_navy", ["100% polyester"], "$55")]),
]
SPLIT_AT = (1, 1)   # item 2's second colour starts page 2: a page break inside one Variant block


def amount(v):
    """The platform's amount cell: a space for thousands ('1 212'), no decimals for a whole number."""
    whole = f"{int(v):,}".replace(",", " ")
    return whole if float(v).is_integer() else f"{v:,.2f}".replace(",", " ")


def _footer(p, doc, i, n, customer="TEST CUSTOMER"):
    p.text(33.3, 821.0, f"Order # {doc} / {customer}", 6.16)
    p.text(548.8, 821.0, f"{i} / {n}", 6.16)


def grid_pdf(path, title="ORDER CONFIRMATION", issuer="TEST Trading Co", doc="3999001", shift=None, qty_off=None,
             amount_off=None, sub_off=0, units_off=0, discount=0.0, drop_page=None, loose=False, blank_ref=False,
             price_list="USD", currency="Dollar", header=None, footer_doc=None, late_rows=False, items=GRID_ITEMS):
    """Four items over two pages and a terms page. Faults: shift=(item, colour, size, dx) moves one quantity;
    qty_off / amount_off=(item, colour, delta) misprint a QUANTITY / AMOUNT; sub_off misprints item 1's
    Subtotal units; units_off the document Subtotal's; discount lowers the Grand total; drop_page leaves a
    page out; loose puts a page of no confirmation first; blank_ref blanks item 4's Ref.; header replaces
    the table header words; footer_doc names another order in page 2's footer; late_rows prints an item on
    the terms page."""
    pages = [Page(595.0, H) for _ in range(3)]
    p = pages[0]
    p.text(437.4, 83.9, title, 9.0)
    p.text(39.7, 104.4, issuer, 6.72)
    rows = [("Order number", f"#{doc}"), ("Order date", "Jan 1, 2099"), ("Order type", "Pre-order"),
            ("Customer", "TEST CUSTOMER"), ("Currency", currency), ("Price list", price_list),
            ("Incoterm", "EXW - Ex Works"), ("Desired delivery", "Jul 1 to Sep 30, 2099"), ("Line", "UNISEX"),
            ("Season", "FW99")]
    for k, (key, val) in enumerate(rows):
        p.text(375.8, 104.4 + 11.75 * k, key, 6.72)
        p.text(443.0, 104.4 + 11.75 * k, val, 6.72)
    p.text(39.7, 117.3, "1 Test Street", 6.72)
    p.text(39.7, 256.8, "CUSTOMER BILLING ADDRESS", 6.72)
    p.text(297.4, 256.8, "CUSTOMER DELIVERY ADDRESS", 6.72)
    for word, x0, x1 in zip(header or ("REFERENCE", "DESCRIPTION", "SIZES", "QUANTITY", "UNIT", "PRICE", "AMOUNT"),
                            (40.8, 108.0, SIZES_X, None, 465.4, None, None), (None, None, None, 452.9, None, 503.4, 553.9)):
        if x1 is None:
            p.text(x0, 356.5, word, 6.72)
        else:
            p.right(x1, 356.5, word, 6.72)
    for x1 in (503.5, 553.9):
        p.right(x1, 364.7, "USD", 5.6)
    page, top, units, value = 0, 378.9, 0, 0.0
    for n, (name, fields, labels, lsize, colours, *_) in enumerate(items, 1):
        p = pages[page]
        p.text(106.9, top, name, 6.72)
        top += 11.3
        for frow in fields:
            for (label, val), x in zip(frow, (106.9, 314.2)):
                p.text(x, top, label, 4.48)
                p.text(x, top + 5.4, val, 6.16)
            top += 16.2
        top += 18.0
        p.text(40.8, top, "Variant", 6.72)
        for k, lab in enumerate(labels):
            p.centre(cell_x(k), top, lab, lsize)
        top += 12.8
        iq = iv = 0
        for c, (colour, sizes, price, ref, comp, srp) in enumerate(colours):
            if (n - 1, c) == SPLIT_AT:
                _footer(p, doc, 1, 3)
                page, top = 1, 40.0
                p = pages[page]
            qty = sum(sizes.values())
            q_print = qty + (qty_off[2] if qty_off and qty_off[:2] == (n, c + 1) else 0)
            v = qty * price
            v_print = v + (amount_off[2] if amount_off and amount_off[:2] == (n, c + 1) else 0)
            iq, iv = iq + qty, iv + v
            p.text(55.4, top, colour, 6.16)
            qt = top + 3.5
            for lab, q in sizes.items():
                dx = shift[3] if shift and shift[:3] == (n, c + 1, lab) else 0
                p.centre(cell_x(labels.index(lab)) + dx, qt, str(q), 6.72)
            p.right(X1["qty"], qt, str(q_print), 6.72)
            p.right(X1["price"], qt, amount(price), 6.72)
            p.right(X1["amount"], qt, amount(v_print), 6.72)
            p.text(55.4, qt + 3.8, "Ref." if blank_ref and n == 4 else f"Ref. {ref}", 6.16)
            rt = qt + 3.8 + 7.3
            for line in comp:
                p.text(55.4, rt, line, 6.16)
                rt += 7.3
            p.text(55.4, rt, f"SRP: {srp}", 6.16)
            top = rt + 12.4
        units, value = units + iq, value + iv
        p.text(40.8, top, "Subtotal", 6.72)
        p.right(X1["qty"], top, str(iq + (sub_off if n == 1 else 0)), 6.72)
        p.right(X1["amount"], top, amount(iv), 6.72)
        top += 15.1
    p = pages[1]
    p.text(383.6, top + 8, "Subtotal", 6.72)
    p.text(428.2, top + 8, str(units + units_off), 6.72)
    p.right(553.9, top + 8, f"${value:,.2f}", 6.72)
    p.text(383.6, top + 24, "Grand total", 6.72)
    p.right(553.9, top + 24, f"${value - discount:,.2f}", 6.72)
    p.text(383.6, top + 36, "Deposit 30%", 6.72)
    p.right(553.9, top + 36, f"${(value - discount) * 0.3:,.2f}", 6.72)
    p.text(39.7, top + 60, "DELIVERY TERMS Terms of sale accepted by the customer.", 6.72)
    _footer(p, footer_doc or doc, 2, 3)
    t = pages[2]
    t.text(39.7, 40.0, "TERMS OF SALE", 9.0)
    t.text(39.7, 60.0, "1. ORDERS Any order form becomes a firm order after written confirmation.", 6.72)
    if late_rows:
        t.text(40.8, 90.0, "Variant", 6.72)
        t.text(40.8, 110.0, "Subtotal", 6.72)
    _footer(t, doc, 3, 3)
    if loose:
        cover = Page(595.0, H)
        cover.text(39.7, 60.0, "FAX COVER SHEET for the attached order", 9.0)
        pages.insert(0, cover)
    if drop_page:
        pages.pop(drop_page - 1)
    return write(path, pages)


INLINE_ITEMS = [  # (name, label rows, lines: (style, colour, srp, {size: qty}, unit, comp, origin))
    ("TEST JACKET", [["44", "46", "48", "50", "52"]],
     [("ZZ8BL001", "BLACK", "39,999", {"48": 1, "50": 1}, 12345, "COTTON100%", "CHINA"),
      ("ZZ8BL001", "IVORY", "39,999", {"46": 1, "48": 2}, 12345, "COTTON100%", "CHINA")]),
    ("TEST original sole bleaching and washing textile Low-Top sneaker", [["36", "37", "38", "39", "40", "41", "42", "43", "44", "45"],
                                                   ["46", "47"]],
     [("ZZ1FW001", "WHITE", "41,111", {"36": 1, "37": 2, "38": 2, "39": 1, "46": 1}, 15000,
       "UPPER:COTTON100% SOLE:RUBBER100%", "CHINA")]),
]
IN_COLS = (("STYLE", 63.1), ("REFERENCE", 91.4), ("VARIANT", 148.1), ("SRP", 216.5), ("SIZES", 242.5),
           ("QTY", 405.3), ("UNIT", 439.0), ("TOTAL", 483.2))
IN_SIZE0, IN_PITCH = 248.4, 14.2


def inline_pdf(path, cover=True, cover_qty=(5, 7), cover_amount=None, doc="3999002", items=INLINE_ITEMS):
    """SOSU's print: a proforma cover, the confirmation page with both items, a totals page.
    cover_qty=(PCS, PRS) and cover_amount are what the cover prints."""
    pages = []
    units = sum(sum(s.values()) for _, _, ls in items for (_, _, _, s, *_ ) in ls)
    value = sum(sum(s.values()) * u for _, _, ls in items for (_, _, _, s, u, *_) in ls)
    if cover:
        c = Page(595.2, 841.8)
        c.text(63.1, 40.0, "TEST CO., LTD.", 9.0)
        c.text(63.1, 60.0, "1-2-3, TEST TOWN, TOKYO", 7.0)
        c.text(384.1, 90.0, "Order number", 7.0)
        c.text(470.0, 90.0, f"#{doc}", 7.0)
        c.text(63.1, 200.0, "PROFORMA INVOICE", 9.0)
        c.text(63.1, 212.0, "&", 9.0)
        c.text(63.1, 224.0, "EXPORT SALES CONTRACT", 9.0)
        c.text(63.1, 250.0, "COMMODITY TEST LABEL MEN'S COLLECTION", 7.0)
        c.text(63.1, 262.0, f"QUANTITY {cover_qty[0]}PCS {cover_qty[1]}PRS", 7.0)
        c.text(63.1, 274.0, "- DETAILS AS PER ATTACHED SHEET - ( ORDER CONFIRMATION & PROFORMA INVOICE)", 7.0)
        c.text(63.1, 286.0, f"ORDER AMOUNT ¥ {cover_amount if cover_amount is not None else value:,}", 7.0)
        pages.append(c)
    p = Page(595.2, 841.8)
    p.text(63.1, 139.7, "TEST CO.,LTD.", 7.0)
    p.text(435.4, 139.7, "ORDER CONFIRMATION", 7.0)
    p.text(63.1, 157.9, "1-2-3, TEST TOWN", 7.0)
    for k, (key, val) in enumerate((("Order number", f"#{doc}"), ("Order Date", "Jun 1, 2099"),
                                    ("Order type", "Pre-order"), ("Customer", "TEST CUSTOMER"),
                                    ("Currency", "Yen"), ("Price list", "JPY/WHOLESALE"))):
        p.text(384.1, 157.9 + 8.25 * k, key, 7.0)
        p.right(515.4, 157.9 + 8.25 * k, val, 7.0)
    geo = {}
    for word, x in IN_COLS:
        p.text(x, 409.0, word, 7.0)
        geo[word] = (x, x + width(word, 7.0))
    for word in ("SRP", "UNIT", "TOTAL"):
        p.centre(sum(geo[word]) / 2, 394.5, "JPY", 7.0)

    def mid(word):
        return sum(geo[word]) / 2

    top = 433.2
    for name, label_rows, lines in items:
        p.text(63.1, top, name, 7.0)
        for r, labels in enumerate(label_rows):
            for k, lab in enumerate(labels):
                p.centre(IN_SIZE0 + k * IN_PITCH, top + 13.8 * r, lab, 7.0)
        top += 13.8 * (len(label_rows) - 1) + 14.3
        for style, colour, srp, sizes, unit, comp, origin in lines:
            qty = sum(sizes.values())
            p.text(91.4, top, style, 7.0)
            p.text(148.1, top, colour, 7.0)
            p.right(239.2, top, srp, 7.0)
            for r, labels in enumerate(label_rows):
                for lab, q in sizes.items():
                    if lab in labels:
                        p.centre(IN_SIZE0 + labels.index(lab) * IN_PITCH, top + 13.8 * r, str(q), 7.0)
            p.centre(mid("QTY"), top, str(qty), 7.0)
            p.centre(mid("UNIT"), top, f"{unit:,}", 7.0)
            p.centre(mid("TOTAL"), top, f"{qty * unit:,}", 7.0)
            p.text(91.4, top + 13.8, comp, 7.0)
            p.text(91.4, top + 27.6, f"Origin: {origin}", 7.0)
            top += 55.0
    pages.append(p)
    t = Page(595.2, 841.8)
    t.text(355.8, 87.1, "Subtotal", 7.0)
    t.centre(mid("QTY"), 87.1, str(units), 7.0)
    t.right(503.6, 87.1, f"{value:,}", 7.0)
    t.text(355.8, 102.5, "Grand total", 7.0)
    t.text(450.2, 102.5, "JPY", 7.0)
    whole = f"{value:,}"
    t.text(463.1, 102.5, whole[:1], 7.0)
    t.text(463.1 + width(whole[:1], 7.0) + 1.7, 102.5, whole[1:], 7.0)
    t.text(63.1, 137.9, "PAYMENT AND DELIVERY", 7.0)
    t.text(63.1, 173.3, "TERMS AND CONDITIONS", 7.0)
    pages.append(t)
    return write(path, pages)


def blank_pdf(path):
    """One page with no text at all: what a scan looks like to the reader."""
    return write(path, [Page(595.0, H)])
