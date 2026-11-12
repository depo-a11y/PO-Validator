"""A selling-session cart read as the order, before the brand's order confirmation (OC) exists.

OTB's cart export (one row per style-colour, the ordered units in a size grid, the unit wholesale) is what was
signed at the selling session. It is not an order confirmation: the brand can still amend quantities and prices
(GTC 3.3/3.4). So a draft built from it says UNCONFIRMED in its Checks, and every quantity and price cell it
takes from the cart is amber, "from cart, not confirmed". When the OC arrives it is the authority: draft from it
with `--prior <the cart-built PO>` (or `complete --po <the cart-built PO> --oc ...`) and the Changes sheet lists
every code, size, quantity and price the OC changed.

The size grid is every column between Size Grid / Available Sizes and Units, headed by its size. Several size
systems sit side by side, so a header repeats ('40' is an RTW size and a shoe size): each quantity is read under
its own header and must be a size of its row's Size Grid. A half size is printed run together, '345' for 34.5,
and read as one when its whole size is also a header. UNI is OS.

Gates, as the OC readers (otb.gate_problems): sizes sum to the row's Units and Units x Wholesale = Total
Wholesale, each where the cart has a value. Its Units and totals are formulas Excel never calculated, so the
export carries no value for them: they are recomputed from the sizes, and the document's `recomputed` says so
(a Checks row). A quantity that is not a whole number, a formula with no value in a size cell, a quantity under a
size outside the row's grid, a size twice on a row, a row with units and no Wholesale: problems, no draft.

Descriptive fields (Style Name, colour, composition, gender, subcategory) still come through oc.cart.load and the
Resolver, as for an OC drafted with its cart.
"""
import collections
import re

from . import cart as cartm
from .adapters import otb
from .adapters.sheet import Uncached, number, read_sheets, text
from .resolve import CART_OC, PRIOR
from .util import cost_currency, currency_off, iso_currency, money, norm_code, norm_size, size_key, size_match

READER = "cart"
UNCONFIRMED = "UNCONFIRMED"
READ_CHECK = "Cart quantities and prices can be read (sizes sum to Units, Units x Wholesale)"
COST_CHECK = "Cost multiplier for this selling session"
CHANGES_CHECK = "Changes the order confirmation makes to the PO drafted before it"
GRID_START = ("Size Grid", "Available Sizes")
UNITS, WHOLESALE, LINE_VALUE, CURRENCY = "Units", "Wholesale", "Total Wholesale", "Currency"


class CartOrderError(Exception):
    def __init__(self, problems):
        super().__init__("; ".join(problems[:5]))
        self.problems = problems


def cart_size(header, headers):
    """A grid header as a store size: '345' -> '34.5' when '34' is also a header, 'UNI' -> 'OS', else norm_size."""
    return cartm.half_size(header, headers)


def label_of(collection):
    """'S029 - MM WOMEN'S RTW CO-ED' -> 'MM': the word after the collection code, as otb.doc_label reads an OC."""
    m = re.match(r"\s*\S+\s+-\s+(\S+)", collection or "")
    return m.group(1) if m else None


def _header(rows):
    for i, row in enumerate(rows[:cartm.HEADER_SCAN]):
        names = [text(v) or "" for v in row]
        if all(n in names for n in cartm.REQUIRED):
            return i, names
    return None, None


def _details(sheets, session):
    """The DocuSign click date on the cart's DETAILS_<session> sheet, as 'YYYY-MM-DD', else None."""
    for name, rows in sheets:
        if name.upper() != f"DETAILS_{session}".upper():
            continue
        for row in rows:
            if row and text(row[0]) and text(row[0]).lower().startswith("docusign") and len(row) > 1:
                v = row[1]
                return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else text(v)
    return None


def read(path):
    """The cart as OC-shaped docs: (docs, cart). Raises cart.CartError for a file that is no cart and
    CartOrderError (with .problems) for a cart whose quantities or prices cannot be read."""
    cart = cartm.load(path)
    sheets = read_sheets(path)
    rows = next((rs for n, rs in sheets if n == cart["sheet"]), None)
    h, names = _header(rows or [])
    if h is None:
        raise CartOrderError([f"sheet '{cart['sheet']}': no cart header row"])
    missing = [n for n in (UNITS, WHOLESALE) if n not in names]
    starts = [names.index(n) for n in GRID_START if n in names]
    if missing or not starts:
        raise CartOrderError([f"sheet '{cart['sheet']}' row {h + 1}: no {' or '.join(missing or GRID_START)} column, "
                              "so no ordered quantities to read; this cart can describe an OC's lines, not be the order"])
    lo, hi = max(starts) + 1, names.index(UNITS)
    grid = [(j, names[j]) for j in range(lo, hi) if names[j]]
    headers = {n for _, n in grid}
    col = {n: names.index(n) for n in (UNITS, WHOLESALE, LINE_VALUE, CURRENCY) if n in names}
    problems, lines, empty, labels, currencies = [], [], [], set(), set()
    recomputed = ["totals"]
    for code in cart["duplicates"]:
        problems.append(f"{code} is on more than one cart row: as the order its units would be read from one row only")

    def num(r, row, j, what, printed_or_none=False):
        """The cell as a number. printed_or_none: a formula Excel never calculated is a value the cart does not
        print (None), not an unreadable cell."""
        v = row[j] if j < len(row) else None
        if printed_or_none and isinstance(v, Uncached):
            return None
        try:
            return number(v, ".")
        except ValueError as e:
            problems.append(f"cart row {r} {what} ({names[j]!r}): {e}")
            return None

    for code, c in sorted(cart["rows"].items(), key=lambda kv: kv[1]["row"]):
        r = c["row"]
        row = rows[r - 1]
        if text(row[names.index("Model")]) != c["model"]:
            raise CartOrderError([f"cart row {r}: the size grid reader and the cart reader disagree on the row"])
        size_grid = {cart_size(s, headers) for s in re.split(r"\s*,\s*", text(row[names.index("Size Grid")]) or "")
                     if s} if "Size Grid" in names else set()
        sizes = {}
        for j, head in grid:
            q = num(r, row, j, "size")
            if not q:
                continue
            s = cart_size(head, headers)
            if q < 0 or q != int(q):
                problems.append(f"cart row {r} {code}: size {s} quantity {q:g} is not a whole number")
                continue
            if size_grid and s not in size_grid:
                problems.append(f"cart row {r} {code}: {int(q)} unit(s) under size {s}, which is not in the row's "
                                f"Size Grid ({', '.join(sorted(size_grid, key=size_key))})")
                continue
            if s in sizes:
                problems.append(f"cart row {r} {code}: size {s} has quantities in two columns")
                continue
            sizes[s] = int(q)
        units = num(r, row, col[UNITS], "Units", printed_or_none=True)
        if not sizes and not units:
            empty.append(code)
            continue
        price = num(r, row, col[WHOLESALE], "Wholesale")
        if price is None:
            problems.append(f"cart row {r} {code}: units but no unit price in {WHOLESALE!r}")
            continue
        value = num(r, row, col[LINE_VALUE], "line value", printed_or_none=True) if LINE_VALUE in col else None
        qty = sum(sizes.values()) if units is None else units
        for missing, what in ((units, "line units"), (value, "line values")):
            if missing is None and what not in recomputed:
                recomputed.append(what)
        cur = text(row[col[CURRENCY]]) if CURRENCY in col and col[CURRENCY] < len(row) else None
        currencies.add(iso_currency(cur) or cur)
        if label_of(c["collection"]):
            labels.add(label_of(c["collection"]))
        lines.append(dict(
            line_no=r, style=c["model"], fabric=c["fabric"], colour=c["colour_code"], product_class="",
            sizes=sizes, qty=int(qty) if float(qty).is_integer() else qty, price=price,
            line_value=value if value is not None else round(qty * price, 2), gender=c["gender"],
            collection=c["collection"], deliver_from=None, deliver_to=None, rrp=None, reader=READER, page=None))
    if len(currencies) > 1:
        problems.append(f"cart rows in more than one currency: {', '.join(sorted(map(str, currencies)))}")
    doc = dict(doc=cart["label"], date=_details(sheets, cart["session"]), lines=lines, collection=None, pages=[],
               currency=next(iter(currencies), None), gross=None, issuer=None,
               label=" and ".join(sorted(labels)) if labels else None, gender=None,
               recomputed=recomputed, session=cart["session"], empty_rows=empty, unconfirmed=True)
    doc["total_qty"] = sum(sum(l["sizes"].values()) for l in lines)
    doc["net"] = round(sum(sum(l["sizes"].values()) * l["price"] for l in lines), 2)
    problems += otb.gate_problems([doc])
    if not lines and not problems:
        problems.append(f"{cart['label']}: no row has any units")
    if problems:
        raise CartOrderError(problems)
    return [doc], cart


def reader_detail(doc):
    signed = f", signed {doc['date']}" if doc.get("date") else ""
    empty = f"; {len(doc['empty_rows'])} cart row(s) with no units not drafted" if doc.get("empty_rows") else ""
    return (f"{UNCONFIRMED}: from {doc['doc']}{signed}, not an order confirmation. Read by the built-in cart reader, "
            f"no AI. {len(doc['lines'])} lines, {doc['total_qty']} units. Every quantity and price is the cart's, "
            f"shaded amber '{CART_OC}': the brand can still amend both. When the order confirmation arrives, build the "
            f"draft from it with this PO as the prior PO (--prior) to list what changed{empty}.")


def marked(line, key, detail):
    """(source, detail) for a quantity or price cell taken from a cart line. The detail of a value worked out from
    the cart's (cost, line total) says how; any other says only where in the cart it was read."""
    keep = f"; {detail}" if key in ("Variant Cost", "Total Order") else ""
    return CART_OC, f"from cart, not confirmed ({line['doc']} row {line['line_no']}){keep}"


def is_cart_line(line, source, key):
    """True for a cell a cart line gave the draft: anything the OC would have given, and the cost made from its price."""
    return line is not None and line.get("reader") == READER and (source == "OC" or key == "Variant Cost")


def from_cart(lines):
    return any(l.get("reader") == READER for l in lines)


def cost_check(brand_cfg):
    return dict(Check=COST_CHECK, Result="WARN",
                Detail=f"costed at {cost_currency(brand_cfg)} x {brand_cfg['cost_multiplier']}, the brand config's "
                       f"multiplier for {brand_cfg['season']}. A cart is one selling session, and two sessions of one "
                       "season have been costed at different multipliers: confirm this one with Colwyn before "
                       "finalize, which writes it as the cost of every product on this PO.")


def read_order(path, brand_cfg):
    """The cart as the order, with its checks: (docs | None, checks, cart | None). docs None means a FAIL."""
    from . import service
    try:
        docs, cart = read(path)
    except cartm.CartError as e:
        return None, [dict(Check="Linesheet/cart export can be read", Result="FAIL", Detail=str(e))], None
    except CartOrderError as e:
        return None, [dict(Check=READ_CHECK, Result="FAIL", Detail=p) for p in e.problems], None
    except ValueError as e:
        return None, [dict(Check="Linesheet/cart export can be read", Result="FAIL", Detail=str(e))], None
    row = dict(Check=service.READER_CHECK, Result="WARN", Detail=reader_detail(docs[0]))
    off = currency_off(docs, cost_currency(brand_cfg))
    if off:
        return None, [row, dict(Check=service.CURRENCY_CHECK, Result="FAIL",
                                Detail=service.currency_detail(off, brand_cfg))], None
    wrong = service.identity_problems(docs, brand_cfg, path, issuer=False)
    if wrong:
        return None, [row, dict(Check=service.IDENTITY_CHECK, Result="FAIL", Detail=wrong)], None
    return docs, [row, dict(Check=READ_CHECK, Result="PASS", Detail=f"{len(docs[0]['lines'])} lines")] \
        + service.recomputed_rows(docs) + [cost_check(brand_cfg)], cart


# ---- the written PO carries its source in its own header ----------------------------------------------------
# A PO drafted from a cart names its quantity and price columns "Order Qty (from cart <session>, not confirmed)" and
# so on. finalize reads its own header: it builds PU-merge, its new sizes and rollback, and Delivery only from a PO
# whose plain "Order Qty" and "Wholesale Price" columns are there. So the cart's state cannot be lost with a
# deleted Checks sheet, and a deleted column fails closed (refused, not read as zero).
CART_COLS = ("Order Qty", "Wholesale Price", "Total Order")
CONFIRMED_COLS = ("Order Qty", "Wholesale Price")
CART_HEAD = re.compile(r"^(Order Qty|Wholesale Price|Total Order) \(from (cart .+), not confirmed\)$")
REFUSED_CHECK = "Upload files that need the order confirmation"


def cart_column(col, source):
    return f"{col} (from {source}, not confirmed)"


def as_written(docs, header, rows, review, fills):
    """(header, rows, review, fills) to write: for a draft from a cart, its quantity and price columns renamed."""
    if not (docs and docs[0].get("unconfirmed")):
        return header, rows, review, fills
    ren = {c: cart_column(c, docs[0]["doc"]) for c in CART_COLS}
    return ([ren.get(h, h) for h in header], [{ren.get(k, k): v for k, v in r.items()} for r in rows],
            [dict(x, Column=ren.get(x["Column"], x["Column"])) for x in review],
            {(i, ren.get(k, k)): c for (i, k), c in fills.items()})


def po_source(header):
    """'cart <session>' when a PO's header carries a cart's quantity or price column, else None."""
    for h in header or []:
        m = CART_HEAD.match(str(h or ""))
        if m:
            return m.group(2)
    return None


def standard(row):
    """A read_po row with its cart columns also under their plain names."""
    out = dict(row)
    for k, v in row.items():
        m = CART_HEAD.match(str(k))
        if m:
            out[m.group(1)] = v
    return out


def aliases(col):
    """{plain name: column} for the cart columns of a header map {name: column}."""
    return {m.group(1): j for h, j in col.items() for m in [CART_HEAD.match(str(h))] if m}


UNCONFIRMED_TEXT = re.compile(r"^UNCONFIRMED: .*?\bfrom (cart [^,]+?)(?:,| not)")
REVIEW_CART_ROW = re.compile(r"\((cart \S+) row \d+\)")


def cart_signals(po_path):
    """[(where, 'cart <session>' | None)]: each independent sign in a PO workbook that it was drafted from a cart.
    Its header (the renamed quantity and price columns), an UNCONFIRMED row in a later sheet (the draft's or
    complete's Checks, finalize's checks), a 'cart, not confirmed' Source in a later sheet (the draft's Review). Any
    one is enough: a PO drafted before the header marker existed, or one whose columns were renamed back, still
    carries the other two. A PO with none of them is read as an order confirmation's."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(po_path, read_only=True)
    except Exception:
        return []
    out = []
    try:
        if not wb.worksheets:
            return []
        first = next(wb.worksheets[0].iter_rows(max_row=1, values_only=True), ())
        src = po_source(first)
        if src:
            out.append(("header", src))
        for ws in wb.worksheets[1:]:
            for row in ws.iter_rows(values_only=True):
                texts = [v for v in row if isinstance(v, str)]
                hit = next((m.group(1) for t in texts for m in [UNCONFIRMED_TEXT.match(t)] if m), None)
                if hit or CART_OC in texts:
                    hit = hit or next((m.group(1) for t in texts for m in [REVIEW_CART_ROW.search(t)] if m), None)
                    out.append((ws.title, hit))
                    break
        return out
    finally:
        wb.close()


def unconfirmed_session(po_path):
    """The cart a PO was drafted from ('cart <session>', or 'a cart' when no sign names it), else None."""
    signals = cart_signals(po_path)
    return next((s for _, s in signals if s), "a cart") if signals else None


def finalize_refusal(po_path, header, new, brand):
    """None for an order confirmation's PO. For a PO drafted from a cart (cart_signals), a WARN: finalize writes
    PU-new only. For any other PO without the plain Order Qty and Wholesale Price columns, a FAIL: nothing is made."""
    signals = cart_signals(po_path)
    if signals:
        src = next((s for _, s in signals if s), "a cart")
        where = ", ".join(dict.fromkeys(w for w, _ in signals))
        n = len({norm_code(r.get("Metafield: my_fields.manufacture_code")) for r in new})
        return dict(Check=REFUSED_CHECK, Result="WARN",
                    Detail=f"{UNCONFIRMED}: this PO was drafted from {src}, not an order confirmation (its {where} "
                           "say so). Refused: PU-merge, PU-merge-new-sizes, PU-merge-ROLLBACK and Delivery, which need "
                           f"the order confirmation's cost and quantities. PU-new creates {n} product(s) at "
                           f"{cost_currency(brand)} x {brand['cost_multiplier']}, a multiplier not confirmed for this "
                           "selling session. The order confirmation can drop codes, so a product created before it may "
                           "never ship. Whether to import PU-new before the order confirmation is Colwyn's decision. "
                           "When it arrives, draft from it with this PO as the prior PO.")
    missing = [c for c in CONFIRMED_COLS if c not in header]
    if missing:
        return dict(Check=REFUSED_CHECK, Result="FAIL",
                    Detail=f"this PO has no {' or '.join(repr(c) for c in missing)} column. PU-merge, its new sizes and "
                           "rollback, and Delivery need the order confirmation's quantities and prices from those "
                           "columns: put them back under exactly those names.")
    return None


CART_WORDING = {"OC totals reproduced (parsed = printed)": "Cart totals (the cart prints none)",
                "PO Order Qty and Total Order equal the OC": "PO Order Qty and Total Order equal the cart",
                "Qty per code+size equals the OC": "Qty per code+size equals the cart"}


def cart_wording(checks):
    """A draft from a cart compares the PO with the cart, not with an OC: say so."""
    return [dict(c, Check=CART_WORDING[c["Check"]],
                 Detail=str(c["Detail"]).replace(" confirmations,", " cart,", 1)) if c["Check"] in CART_WORDING else c
            for c in checks]


# ---- the OC arrives: what changed against the cart-built (prior) PO ----------------------------------------


def read_prior(prior_path):
    """{rows, name, typed: {code: (price, where)}} for a PO drafted before the OC, or {fail: check row}."""
    from pathlib import Path
    from .poxlsx import read_po
    name = Path(prior_path).name
    try:
        _, rows = read_po(prior_path)
    except Exception as e:
        return dict(fail=dict(Check=CHANGES_CHECK, Result="FAIL",
                              Detail=f"the prior PO {name} cannot be read: {type(e).__name__}: {e}"))
    rows = [standard(r) for r in rows]
    typed = {}
    for r in rows:
        code = norm_code(r.get("Metafield: my_fields.manufacture_code"))
        if code and str(r.get("Command") or "").strip().upper() == "NEW" and money(r.get("Variant Price")):
            typed.setdefault(code, (money(r.get("Variant Price")), f"{name} row {r['_row']}"))
    return dict(rows=rows, name=name, typed=typed)


def with_prior_price(resolved, code, typed):
    """A NEW product's retail price: the brand's RRP when it gives one, else the price typed in the prior PO."""
    price, _, why = resolved
    if price is None and typed and code in typed:
        value, where = typed[code]
        return value, PRIOR, f"retail price typed in the prior PO ({where}); check it against this order's cost"
    return resolved


def prior_sheet(prior, docs, brand_cfg):
    """({"Changes": rows}, check) for a draft from the OC built with a prior PO."""
    from .draft import oc_lines
    changed, check = changes(prior["rows"], oc_lines(docs, brand_cfg), cost_currency(brand_cfg),
                             f"prior PO ({prior['name']})", size_map=brand_cfg.get("size_map"))
    return {"Changes": changed}, check


def changes(prior_rows, lines, currency, prior_name="prior PO", who="the prior PO", size_map=None):
    """(rows for a Changes sheet, check) comparing a PO drafted before the OC (from the cart) with the OC lines.
    Per code and size: quantities; per code: the unit price; codes the OC adds or drops; retail prices typed into
    the prior PO's NEW rows. `who` names that PO in the notes ('this PO' when complete is run on it). Sizes compare
    by util.size_match, since the draft names a printed '40' as the store's 'IT 40'; the sheet shows the PO's name."""
    oc_q, oc_eur, oc_where, name = collections.Counter(), {}, {}, {}
    for l in lines:
        oc_eur.setdefault(l["code"], l["price"])
        oc_where.setdefault(l["code"], f"{l['doc']} line {l['line_no']}")
        for s, q in l["sizes_norm"].items():
            oc_q[(l["code"], size_match(s, size_map))] += q
            name.setdefault((l["code"], size_match(s, size_map)), s)
    po_q, po_eur, po_cmd, typed = collections.Counter(), {}, {}, {}
    for r in map(standard, prior_rows):
        code = norm_code(r.get("Metafield: my_fields.manufacture_code"))
        if not code:
            continue
        po_q[(code, size_match(r.get("Option2 Value"), size_map))] += int(money(r.get("Order Qty")) or 0)
        name[(code, size_match(r.get("Option2 Value"), size_map))] = norm_size(r.get("Option2 Value"))
        if money(r.get("Wholesale Price")) is not None:
            po_eur.setdefault(code, money(r.get("Wholesale Price")))
        po_cmd.setdefault(code, str(r.get("Command") or "").strip().upper())
        if po_cmd[code] == "NEW" and money(r.get("Variant Price")):
            typed.setdefault(code, money(r.get("Variant Price")))
    out = []

    def add(code, size, what, before, after, note=""):
        out.append({"Code": code, "Size": size, "Change": what, prior_name: before, "Order confirmation": after,
                    "Where": oc_where.get(code, ""), "Note": note})

    po_codes = set(po_cmd)
    for code in sorted(set(oc_eur) - po_codes):
        add(code, "", "added by the OC", "", sum(q for (c, _), q in oc_q.items() if c == code), f"not in {who}")
    for code in sorted(po_codes - set(oc_eur)):
        add(code, "", "dropped by the OC", sum(q for (c, _), q in po_q.items() if c == code), 0,
            f"in {who}, not on the OC")
    both = po_codes & set(oc_eur)
    for code, size in sorted({k for k in set(oc_q) | set(po_q) if k[0] in both}, key=lambda k: (k[0], size_key(k[1]))):
        if oc_q[(code, size)] != po_q[(code, size)]:
            add(code, name[(code, size)], "quantity", po_q[(code, size)], oc_q[(code, size)])
    for code in sorted(both):
        if code in po_eur and abs(po_eur[code] - oc_eur[code]) > 0.005:
            add(code, "", f"unit price ({currency})", po_eur[code], oc_eur[code],
                "cost = the OC's price x the brand multiplier")
    for code in sorted(typed):
        add(code, "", f"retail price typed in {who}", typed[code], "",
            "carried into the draft from the OC where its retail price is blank" if code in oc_eur
            else "code not on the OC")
    kinds = collections.Counter(x["Change"] for x in out if not x["Change"].startswith("retail"))
    units = (sum(po_q.values()), sum(oc_q.values()))
    detail = (f"{who} {units[0]} units, OC {units[1]} units; " +
              (", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) if kinds else "no quantity, price or code changed")
              + (f"; {len(typed)} retail price(s) typed in {who}" if typed else "")
              + ". See the Changes sheet.")
    return out, dict(Check=CHANGES_CHECK, Result="WARN" if kinds else "INFO", Detail=detail)
