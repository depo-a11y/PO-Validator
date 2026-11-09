"""`oc complete`: fill the blanks of a PO someone started by hand.

Every human-entered cell is kept. Only empty cells (and '-' in made_in) are
filled, and only from the tool's resolvers; a wholesale price is changed only
when --set-price names the code. Every touched cell is listed in a Review
sheet and shaded in the PO itself. The workbook is edited in place on a copy,
so formulas, formats and column widths survive.
"""
import collections

import openpyxl
from openpyxl.utils import get_column_letter

from .draft import oc_lines, cart_checks
from .poxlsx import (FILLS, CODE, MADE_IN, COST_PRICE, TITLE_TAG, SEASON, NEW_SALE, PO_FORMULA,
                     resolve_formula, write_table, review_row, clean_text, as_text)
from .resolve import Resolver, COLOUR, AI_OC
from .util import blank, norm_code, norm_size, fmt_eur, money, slug


def run(po_path, docs, brand, catalog, history, tag_lookup, out, price_overrides=None, cart=None):
    price_overrides = {norm_code(k): v for k, v in (price_overrides or {}).items()}
    lines = oc_lines(docs, brand)
    by_code = {l["code"]: l for l in lines}
    R = Resolver(brand, catalog, history, tag_lookup, lines, cart)
    wb = openpyxl.load_workbook(po_path)
    ws = wb.worksheets[0]
    header = [c.value for c in ws[1]]
    col = {h: i + 1 for i, h in enumerate(header) if h}
    last = max(r for r in range(1, ws.max_row + 1) if any(ws.cell(r, c).value is not None for c in range(1, len(header) + 1)))
    data_rows = [r for r in range(2, last + 1)
                 if any(ws.cell(r, c).value is not None for c in range(1, len(header) + 1))]
    raw = {r: [ws.cell(r, c).value for c in range(1, len(header) + 1)] for r in range(1, last + 1)}

    def get(r, name):
        c = ws.cell(r, col[name])
        return resolve_formula(c.value, raw, r) if c.data_type == "f" else c.value

    review = []

    def put(r, name, value, source, why):
        c = ws.cell(r, col[name])
        old = c.value
        if old == value:
            return
        value = clean_text(value)
        c.value = value
        if not (isinstance(value, str) and PO_FORMULA.fullmatch(value)):   # only the compare-at '=P5' is a formula
            as_text(c)
        c.fill = FILLS["changed"] if source == "OC" else FILLS[COLOUR.get(source, "red")]
        raw[r][col[name] - 1] = value
        review.append(review_row(r, get(r, CODE), get(r, "Option2 Value"), name, value, source, why,
                                 old=old, include_old=True))

    # 1. wholesale corrections named on the command line (the OC is the authority)
    oc_eur = {l["code"]: l["price"] for l in lines}
    for code, eur in price_overrides.items():
        if code not in oc_eur or abs(eur - oc_eur[code]) > 0.005:
            raise SystemExit(f"--set-price {code}={eur:g} does not match the OC (EUR {oc_eur.get(code)})")
    for r in data_rows:
        code = norm_code(get(r, CODE))
        if code in price_overrides:
            eur = price_overrides[code]
            if abs(eur - oc_eur.get(code, -1)) > 0.005:
                raise SystemExit(f"--set-price {code}={eur:g} does not match the OC (EUR {oc_eur.get(code)})")
            qty = money(get(r, "Order Qty")) or 0
            why = f"OC confirms EUR {eur:g}; sheet had EUR {money(get(r, 'Wholesale Price')):g}"
            l = by_code[code]
            src, ai = (AI_OC, f"read by AI from {l['doc']} line {l['line_no']}; ") if l.get("reader") == "ai" else ("OC", "")
            put(r, "Wholesale Price", eur, src, ai + why)
            put(r, COST_PRICE, fmt_eur(eur), src, ai + why)
            put(r, "Variant Cost", R.cost(eur), src, ai + f"EUR {eur:g} x {brand['cost_multiplier']}")
            put(r, "Total Order", round(qty * eur, 2), src, ai + f"{qty:g} x EUR {eur:g}")

    # 2. SKUs: NEW rows first, then new sizes on MERGE rows
    for command in ("NEW", "MERGE"):
        for r in data_rows:
            if str(get(r, "Command")).strip().upper() == command and blank(get(r, "Variant SKU")):
                sku, s, why = R.next_sku()
                put(r, "Variant SKU", sku, s, why + ("" if command == "NEW" else " (new size on a live product)"))

    # 3. retail price + compare-at on NEW rows, one price per product
    priced = {}
    for r in data_rows:
        if str(get(r, "Command")).strip().upper() != "NEW":
            continue
        code = norm_code(get(r, CODE))
        if blank(get(r, "Variant Price")):
            if code not in priced:
                priced[code] = R.retail(code, money(get(r, "Wholesale Price")), get(r, "Type"))
            price, s, why = priced[code]
            put(r, "Variant Price", price, s, why)
        if blank(ws.cell(r, col["Variant Compare At Price"]).value):
            put(r, "Variant Compare At Price", f"={get_column_letter(col['Variant Price'])}{r}",
                "rule", "compare-at = price (a formula, as on the sheet's MERGE rows)")

    # 4. made_in where the catalog knows it
    for r in data_rows:
        cur = get(r, MADE_IN)
        if not (blank(cur) or str(cur).strip() == brand["unknown"]):
            continue
        code = norm_code(get(r, CODE))
        product = catalog.match(code)[0] if str(get(r, "Command")).strip().upper() == "MERGE" else None
        v, s, why = R.made_in(code, product)
        if s in ("catalog", "cart"):
            put(r, MADE_IN, v, s, why)

    checks = _checks(ws, col, data_rows, get, lines, R, brand, catalog, tag_lookup, review)
    if cart is not None:
        checks += cart_checks(lines, cart)
    write_table(wb, "Review", review, colour_key="Colour")
    write_table(wb, "Checks", checks, colour_key="Result")
    wb.save(out)
    return checks


def _checks(ws, col, data_rows, get, lines, R, brand, catalog, tag_lookup, review):
    out = []
    oc_q, oc_eur = collections.Counter(), {}
    for l in lines:
        oc_eur[l["code"]] = l["price"]
        for s, q in l["sizes_norm"].items():
            oc_q[(l["code"], s)] += q
    rows = [{h: get(r, h) for h in col} | {"_row": r} for r in data_rows]
    po_q = collections.Counter()
    for x in rows:
        po_q[(norm_code(x[CODE]), norm_size(x["Option2 Value"]))] += int(money(x["Order Qty"]) or 0)
    bad = sorted(k for k in set(oc_q) | set(po_q) if oc_q[k] != po_q[k])
    out.append(dict(Check="Qty per code+size equals the OC", Result="FAIL" if bad else "PASS",
                    Detail=f"{len(bad)} mismatches" + (f": {bad[:5]}" if bad else "")))
    units = sum(po_q.values())
    value = round(sum(money(x["Total Order"]) or 0 for x in rows), 2)
    oc_units, oc_value = sum(oc_q.values()), round(sum(l["line_value"] for l in lines), 2)
    out.append(dict(Check="Order Qty and Total Order equal the OC", Result="PASS" if (units, value) == (oc_units, oc_value) else "FAIL",
                    Detail=f"{units} units, EUR {value:,.2f} (OC {oc_units}, EUR {oc_value:,.2f})"))
    wrong = sorted({(norm_code(x[CODE]), money(x["Wholesale Price"])) for x in rows
                    if abs((money(x["Wholesale Price"]) or 0) - oc_eur.get(norm_code(x[CODE]), -1)) > 0.005})
    out.append(dict(Check="Wholesale equals the OC on every row", Result="FAIL" if wrong else "PASS",
                    Detail=str(wrong) if wrong else f"{len(oc_eur)} codes"))
    tot = [x for x in rows if abs((money(x["Total Order"]) or 0) - (money(x["Order Qty"]) or 0) * (money(x["Wholesale Price"]) or 0)) > 0.005]
    out.append(dict(Check="Total Order = Order Qty x Wholesale on every row", Result="FAIL" if tot else "PASS",
                    Detail=f"{len(tot)} rows off" + (f": {[x['_row'] for x in tot][:10]}" if tot else "")))
    mult = brand["cost_multiplier"]
    cst = [x for x in rows if abs((money(x["Variant Cost"]) or 0) - round((money(x["Wholesale Price"]) or 0) * mult, 2)) > 0.01]
    out.append(dict(Check=f"Variant Cost = Wholesale x {mult} on every row", Result="FAIL" if cst else "PASS",
                    Detail=f"{len(cst)} rows off" + (f": {[x['_row'] for x in cst][:10]}" if cst else "")))
    cpx = [x for x in rows if str(x[COST_PRICE]).strip() != fmt_eur(money(x["Wholesale Price"]) or 0)]
    out.append(dict(Check="cost_price metafield = 'EUR <wholesale>' on every row", Result="FAIL" if cpx else "PASS",
                    Detail=f"{len(cpx)} rows off" + (f": {[(x['_row'], x[COST_PRICE]) for x in cpx][:5]}" if cpx else "")))
    skus = [str(x["Variant SKU"]).strip() for x in rows]
    empty = [x["_row"] for x in rows if blank(x["Variant SKU"])]
    dupe = [s for s, n in collections.Counter(skus).items() if n > 1]
    filled = {rv["Value"] for rv in review if rv["Column"] == "Variant SKU"}
    clash = sorted(s for s in filled if s in catalog.skus)
    out.append(dict(Check="Every row has a SKU; SKUs unique; new SKUs absent from the catalog",
                    Result="FAIL" if empty or dupe or clash else "PASS",
                    Detail=f"{len(filled)} filled ({min(filled, default='')}..{max(filled, default='')}); "
                           f"{len(empty)} empty, {len(dupe)} duplicated, {len(clash)} already in catalog"))
    merge_skus = [x for x in rows if str(x["Command"]).strip().upper() == "MERGE" and str(x["Variant SKU"]).strip() not in filled]
    stale = []
    for x in merge_skus:
        p = catalog.match(x[CODE])[0]
        live = {v["size"]: v["sku"] for v in p["variants"]} if p else {}
        if live.get(norm_size(x["Option2 Value"])) != str(x["Variant SKU"]).strip():
            stale.append((x["_row"], x["Variant SKU"], live.get(norm_size(x["Option2 Value"]))))
    out.append(dict(Check="MERGE SKUs are the live SKU for that code+size", Result="FAIL" if stale else "PASS",
                    Detail=f"{len(stale)} differ" + (f": {stale[:5]}" if stale else "")))
    new = [x for x in rows if str(x["Command"]).strip().upper() == "NEW"]
    floor = brand["margin_floor"]
    low = [x for x in new if not money(x["Variant Price"]) or money(x["Variant Price"]) / money(x["Variant Cost"]) < floor]
    out.append(dict(Check=f"NEW rows clear the {floor}x margin", Result="FAIL" if low else "PASS",
                    Detail=f"{len(new) - len(low)} of {len(new)} rows"))
    types = set(tag_lookup)
    badt = sorted({x["Type"] for x in new if x["Type"] not in types})
    out.append(dict(Check="Every NEW Type is in expected_tags.xlsx", Result="FAIL" if badt else "PASS",
                    Detail=", ".join(badt) or f"{len({x['Type'] for x in new})} Types"))
    by_title = collections.defaultdict(set)
    for x in new:
        by_title[x["Title"]].add(norm_code(x[CODE]))
    multi = {t: sorted(c) for t, c in by_title.items() if len(c) > 1}
    out.append(dict(Check="One manufacture code per NEW title (Matrixify groups NEW rows by Title)",
                    Result="FAIL" if multi else "PASS", Detail=str(multi) if multi else f"{len(by_title)} titles"))
    taken = sorted(t for t in by_title if R.handle_taken(t))
    out.append(dict(Check="No NEW title's handle is already live", Result="FAIL" if taken else "PASS",
                    Detail=", ".join(taken) or "none"))
    long_tags = sorted({x[TITLE_TAG] for x in new if len(str(x[TITLE_TAG] or "")) > 60})
    out.append(dict(Check="title_tag <= 60 chars (the validator's own check never fires)", Result="WARN" if long_tags else "PASS",
                    Detail=f"{len(long_tags)} over 60" + (": " + " | ".join(long_tags) if long_tags else "")))
    mism = sorted({(x["Title"], x[TITLE_TAG]) for x in new if x[TITLE_TAG] and str(x["Title"]) not in str(x[TITLE_TAG])})
    if mism:
        out.append(dict(Check="title_tag contains the Title", Result="WARN",
                        Detail=f"{len(mism)} products: " + " | ".join(f"{t} -> {tt}" for t, tt in mism)))
    still = sorted({norm_code(x[CODE]) for x in rows if str(x[MADE_IN]).strip() in ("", brand["unknown"], "None")})
    out.append(dict(Check="made_in known", Result="INFO", Detail=f"{len(still)} codes still '{brand['unknown']}': {', '.join(still)}"))
    body_dash = sorted({norm_code(x[CODE]) for x in new if "Made in -" in str(x["Body HTML"] or "") and str(x[MADE_IN]).strip() not in ("", "-")})
    if body_dash:
        out.append(dict(Check="Body HTML 'Made in' line agrees with made_in", Result="WARN",
                        Detail=f"{len(body_dash)} codes now have made_in but Body HTML still says 'Made in -' (Body HTML left as entered)"))
    spaced = sum(1 for x in rows if str(x[MADE_IN]) != str(x[MADE_IN]).strip() or str(x[NEW_SALE]) != str(x[NEW_SALE]).strip())
    if spaced:
        out.append(dict(Check="No stray spaces in made_in / new_sale", Result="WARN",
                        Detail=f"{spaced} rows carry leading/trailing spaces (' Italy', 'NEW '); finalize strips them in the PU"))
    notes = sorted({(x["_row"], x["Tags Command"]) for x in rows if not blank(x.get("Tags Command"))
                    and str(x["Tags Command"]).strip().upper() not in ("MERGE", "REPLACE", "DELETE")})
    if notes:
        out.append(dict(Check="Tags Command holds only Matrixify values", Result="WARN",
                        Detail=f"notes in the column: {notes}; finalize never emits this column from the PO"))
    renamed, onsale, lowm = [], set(), set()
    for x in rows:
        if str(x["Command"]).strip().upper() != "MERGE":
            continue
        p = catalog.match(x[CODE])[0]
        if p is None:
            continue
        if p["title"] != x["Title"]:
            renamed.append((x["Title"], p["title"]))
        v = next((v for v in p["variants"] if v["size"] == norm_size(x["Option2 Value"])), None)
        if v and (v["compare_at"] or 0) > (v["price"] or 0):
            onsale.add(f"{p['title']} live A${v['price']:g} (compare-at A${v['compare_at']:g}); sheet A${money(x['Variant Price']):g}")
        if money(x["Variant Price"]) / money(x["Variant Cost"]) < floor:
            lowm.add(f"{x['Title']}: A${money(x['Variant Price']):g} / A${money(x['Variant Cost']):g} = {money(x['Variant Price']) / money(x['Variant Cost']):.2f}x")
    if renamed:
        r = sorted(set(renamed))
        out.append(dict(Check="MERGE titles match the live titles", Result="WARN",
                        Detail=f"{len(r)} renamed in the store since the sheet was made: " + " | ".join(f"sheet '{a}' / live '{b}'" for a, b in r)
                               + ". The PU never writes Title on MERGE rows."))
    if onsale:
        out.append(dict(Check="Carryover products currently marked down", Result="WARN", Detail="; ".join(sorted(onsale))))
    if lowm:
        out.append(dict(Check=f"MERGE price clears {floor}x at this season's cost", Result="WARN", Detail="; ".join(sorted(lowm))))
    return out
