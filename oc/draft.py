"""`oc draft`: OC + catalog (+ history, optional cart) -> a PO buy sheet draft.

Carryover lines (join key found on one live product) become MERGE rows carrying
the live ID, Handle, SKU, Title, Type and price; a size the live product lacks
becomes a MERGE row with a new SKU. Everything else is drafted as NEW rows.
Every drafted cell carries a source for the Review sheet.

A cart (oc.cart.load) supplies colour words, colour family, composition, made in,
gender and subcategory, matched on the full join key. Quantities and prices are
always the OC's; a cart wholesale that differs is reported, never used.
"""
import collections

from .poxlsx import PO_HEADER, CODE, MADE_IN, SEASON, NEW_SALE, COST_PRICE, TITLE_TAG, review_row
from .resolve import Resolver, COLOUR, split_code, worse
from .util import join_key, norm_size, sort_sizes, size_key, fmt_eur, slug


def oc_lines(docs, gender_of=None):
    out = []
    for d in docs:
        for l in d["lines"]:
            code = join_key(l["style"], l["fabric"], l["colour"])
            sizes = {}
            for s, q in l["sizes"].items():
                sizes[norm_size(s)] = sizes.get(norm_size(s), 0) + q
            out.append(dict(l, code=code, doc=d["doc"], collection=d["collection"],
                            gender=d.get("gender"), sizes_norm=sizes))
    return out


def build(docs, brand, catalog, history, tag_lookup, cart=None):
    lines = oc_lines(docs)
    dup = [c for c, n in collections.Counter(l["code"] for l in lines).items() if n > 1]
    R = Resolver(brand, catalog, history, tag_lookup, lines, cart)
    rows, cells, checks = [], [], []

    def cell(row, key, value, source, detail):
        row[key] = value
        cells.append((len(rows), key, value, source, detail))

    pending = []
    for l in lines:
        code, eur = l["code"], l["price"]
        product, note, cands = catalog.match(code)
        ambiguous = note.startswith("AMBIGUOUS")
        if ambiguous:
            checks.append(dict(Check="Join key resolves to one live product", Result="FAIL",
                               Detail=f"{code}: {note}"))
        elif note:
            checks.append(dict(Check="ARCHIVED twin dropped", Result="INFO", Detail=f"{code}: {note}"))

        common = {}
        if product is not None:
            live = {v["size"]: v for v in product["variants"]}
            run = sort_sizes(set(live) | set(l["sizes_norm"]))
            run_src, run_why = "catalog", f"live sizes of {product['title']} plus ordered sizes"
            ptype, title = product["type"], product["title"]
            colour = collections.Counter(v["colour"] for v in product["variants"] if v["colour"]).most_common(1)
            colour = colour[0][0] if colour else ""
            made, made_src, made_why = R.made_in(code, product)
            for size in run:
                v = live.get(size)
                row = {}
                rows_i = len(rows)
                cell(row, "Command", "MERGE", "catalog", f"join key {code} is live product {product['id']}")
                cell(row, "ID", product["id"], "catalog", "live product ID")
                cell(row, "Handle", product["handle"], "catalog", "live handle (review only; never imported)")
                cell(row, "Title", title, "catalog", "live title")
                cell(row, "Vendor", brand["vendor"], "rule", "brand config")
                cell(row, "Type", ptype, "catalog", "live type")
                cell(row, "Status", brand["status"], "rule", "brand config")
                cell(row, "Published", brand["published_merge"], "rule", "carryover convention")
                cell(row, "Option1 Name", brand["option1_name"], "rule", "house option order")
                cell(row, "Option1 Value", (v or {}).get("colour") or colour, "catalog", "live colour option")
                cell(row, "Option2 Name", brand["option2_name"], "rule", "house option order")
                cell(row, "Option2 Value", size, "catalog" if v else "OC",
                     "live variant" if v else "ordered size the live product does not have")
                if v:
                    cell(row, "Variant SKU", v["sku"], "catalog", "live SKU")
                    price = max(v["price"] or 0, v["compare_at"] or 0)
                    cell(row, "Variant Price", price, "catalog",
                         "live full price" + (f" (live is on sale at A${v['price']:g})" if (v["compare_at"] or 0) > (v["price"] or 0) else ""))
                else:
                    sku, s, why = R.next_sku()
                    cell(row, "Variant SKU", sku, s, why + " (new size on a live product)")
                    prices = collections.Counter(max(x["price"] or 0, x["compare_at"] or 0) for x in product["variants"])
                    price = prices.most_common(1)[0][0]
                    cell(row, "Variant Price", price, "catalog", "live full price of the product's other sizes")
                cell(row, "Variant Compare At Price", price, "rule", "compare-at = price on a buy sheet")
                _money_cells(cell, row, R, brand, eur, l["sizes_norm"].get(size, 0))
                cell(row, CODE, code, "OC", f"{l['doc']} line {l['line_no']}")
                cell(row, SEASON, brand["season"], "rule", "carryover is re-seasoned to the buy season")
                cell(row, MADE_IN, made, made_src, made_why)
                cell(row, NEW_SALE, product["new_sale"] or brand["new_sale"], "catalog", "live new_sale")
                rows.append(row)
            continue

        # ---- NEW: resolve now, emit after titles are de-duplicated -------------
        cname, cn_src, cn_why = R.colour_name(code)
        gender, pclass = R.gender_of(code, l["gender"]), R.product_class_of(code, l["product_class"])
        ptype, t_src, t_why = R.product_type(code, pclass, gender)
        title, ti_src, ti_why = R.title(code, pclass, cname)
        if worse(cn_src, ti_src) == cn_src and COLOUR[cn_src] != COLOUR[ti_src]:
            ti_src, ti_why = cn_src, f"{ti_why}; colour name: {cn_why}"
        pending.append(dict(
            l=l, code=code, eur=eur, gender=gender, ptype=(ptype, t_src, t_why), title=[title, ti_src, ti_why],
            fam=R.colour_family(code, cname), run=R.size_run(code, l["sizes_norm"], ptype),
            price=R.retail(code, eur, ptype), made=R.made_in(code), comp=R.composition(code)))

    _dedupe_titles(pending, R, brand, checks)
    for n in pending:
        l, code, eur = n["l"], n["code"], n["eur"]
        title, ti_src, ti_why = n["title"]
        ptype, t_src, t_why = n["ptype"]
        fam, f_src, f_why = n["fam"]
        run, run_src, run_why = n["run"]
        price, p_src, p_why = n["price"]
        made, made_src, made_why = n["made"]
        comp, comp_src, comp_why = n["comp"]
        for size in run:
            row = {}
            cell(row, "Command", "NEW", "catalog", f"join key {code} is not a live product")
            cell(row, "Title", title, ti_src, ti_why)
            cell(row, "Vendor", brand["vendor"], "rule", "brand config")
            cell(row, "Type", ptype, t_src, t_why)
            cell(row, "Body HTML", R.body_html(code, comp, made), "guess" if "guess" in (comp_src, made_src)
                 else "cart" if "cart" in (comp_src, made_src) else "catalog",
                 f"template; composition: {comp_why}; made in: {made_why}")
            cell(row, "Status", brand["status"], "rule", "brand config")
            cell(row, "Published", brand["published_new"], "rule", "new stock is created unpublished")
            cell(row, "Option1 Name", brand["option1_name"], "rule", "house option order")
            cell(row, "Option1 Value", fam, f_src, f_why)
            cell(row, "Option2 Name", brand["option2_name"], "rule", "house option order")
            cell(row, "Option2 Value", size, "OC" if size in l["sizes_norm"] else run_src,
                 "ordered size" if size in l["sizes_norm"] else run_why)
            sku, s_, why = R.next_sku()
            cell(row, "Variant SKU", sku, s_, why)
            cell(row, "Variant Price", price, p_src, p_why)
            cell(row, "Variant Compare At Price", price, "rule", "compare-at = price at creation")
            _money_cells(cell, row, R, brand, eur, l["sizes_norm"].get(size, 0))
            cell(row, CODE, code, "OC", f"{l['doc']} line {l['line_no']} ({l['product_class']})")
            cell(row, SEASON, brand["season"], "rule", "brand config")
            cell(row, MADE_IN, made, made_src, made_why)
            cell(row, NEW_SALE, brand["new_sale"], "rule", "new stock is created NEW")
            cell(row, TITLE_TAG, R.title_tag(title), ti_src, "house title_tag template over the drafted title")
            rows.append(row)

    if cart is not None:
        checks += cart_checks(lines, cart)
    checks = _draft_checks(docs, lines, rows, R, brand, catalog, tag_lookup, dup) + checks
    review = [review_row(i + 2, rows[i].get(CODE), rows[i].get("Option2 Value"), k, v, s, w)
              for i, k, v, s, w in cells]
    fills = {(i, k): COLOUR.get(s, "red") for i, k, v, s, w in cells}
    return rows, review, checks, fills


def cart_checks(lines, cart):
    """How the cart lines up with the OC. Zero shared codes means the wrong cart."""
    rows = cart["rows"]
    oc = {}
    for l in lines:
        oc.setdefault(l["code"], l)
    hit = [c for c in oc if c in rows]
    oc_only = sorted(c for c in oc if c not in rows)
    cart_only = sorted(c for c in rows if c not in oc)
    out = [dict(Check="Cart matches this OC", Result="PASS" if hit else "FAIL",
                Detail=f"{len(hit)} of {len(oc)} OC codes are in {cart['label']} ({len(rows)} cart rows, "
                       f"sheet '{cart['sheet']}')" + ("" if hit else "; wrong cart for this OC, nothing taken from it")),
           dict(Check="OC codes not in the cart", Result="INFO",
                Detail=f"{len(oc_only)} code(s), drafted without cart data" + (": " + ", ".join(oc_only) if oc_only else "")),
           dict(Check="Cart codes not on the OC", Result="INFO",
                Detail=f"{len(cart_only)} code(s), ignored" + (": " + ", ".join(cart_only) if cart_only else ""))]
    diff = [(c, rows[c]["wholesale"], oc[c]["price"]) for c in hit
            if rows[c]["wholesale"] is not None and abs(rows[c]["wholesale"] - oc[c]["price"]) > 0.005]
    if diff:
        out.append(dict(Check="Cart wholesale differs from the OC (the OC's is used)", Result="INFO",
                        Detail=f"{len(diff)} code(s): " + "; ".join(f"{c} cart {w:g} vs OC EUR {p:g}" for c, w, p in diff)))
    if cart["duplicates"]:
        out.append(dict(Check="One cart row per code", Result="WARN",
                        Detail=f"{len(cart['duplicates'])} code(s) on 2+ rows, first row used: " + ", ".join(cart["duplicates"])))
    return out


def _dedupe_titles(pending, R, brand, checks):
    """Matrixify groups NEW rows by Title, so two codes may never share one, and a
    title whose handle is already live would fail as a duplicate. Prefix one
    gender's product (brand `title_prefixes`, default the women's with
    `womens_prefix`); whatever still collides is a FAIL."""
    prefixes = brand.get("title_prefixes") or {"WOMEN": brand["womens_prefix"]}
    for _ in range(2):
        groups = collections.defaultdict(list)
        for n in pending:
            groups[slug(n["title"][0])].append(n)
        for key, ns in groups.items():
            taken = R.handle_taken(ns[0]["title"][0])
            if len(ns) < 2 and not taken:
                continue
            for n in ns:
                pre = prefixes.get(n["gender"] or "")
                if pre and not n["title"][0].startswith(pre):
                    n["title"] = [pre + n["title"][0], "rule",
                                  n["title"][2] + f"; '{pre.strip()}' prefix keeps it apart from a same-named product"]
    groups = collections.defaultdict(list)
    for n in pending:
        groups[slug(n["title"][0])].append(n)
    for key, ns in groups.items():
        if len(ns) > 1:
            checks.append(dict(Check="One manufacture code per NEW title", Result="FAIL",
                               Detail=f"'{ns[0]['title'][0]}' on " + ", ".join(n["code"] for n in ns)))
            for n in ns:
                n["title"][1] = "guess"
        if R.handle_taken(ns[0]["title"][0]):
            checks.append(dict(Check="NEW title does not collide with a live handle", Result="FAIL",
                               Detail=f"'{ns[0]['title'][0]}' -> /{key} already exists"))
            for n in ns:
                n["title"][1] = "guess"


def _money_cells(cell, row, R, brand, eur, qty):
    cell(row, "Variant Cost", R.cost(eur), "rule", f"EUR {eur:g} x {brand['cost_multiplier']} (season cost convention)")
    cell(row, COST_PRICE, fmt_eur(eur), "OC", "confirmed unit price")
    cell(row, "Wholesale Price", eur, "OC", "confirmed unit price")
    cell(row, "Order Qty", qty, "OC" if qty else "rule", "confirmed quantity" if qty else "size in the run, not ordered")
    cell(row, "Total Order", round(qty * eur, 2), "OC", "Order Qty x Wholesale Price")


def _draft_checks(docs, lines, rows, R, brand, catalog, tag_lookup, dup):
    from .adapters import otb
    t = otb.totals(docs)
    out = []
    ok = t["units"] == t["printed_units"] and abs(t["value"] - t["printed_value"]) < 0.01
    out.append(dict(Check="OC totals reproduced (parsed = printed)", Result="PASS" if ok else "FAIL",
                    Detail=f"{t['docs']} confirmations, {t['lines']} lines, {t['units']} units, EUR {t['value']:,.2f} "
                           f"(printed {t['printed_units']} / EUR {t['printed_value']:,.2f})"))
    units = sum(r["Order Qty"] for r in rows)
    value = round(sum(r["Total Order"] for r in rows), 2)
    ok = units == t["units"] and abs(value - t["value"]) < 0.01
    out.append(dict(Check="PO Order Qty and Total Order equal the OC", Result="PASS" if ok else "FAIL",
                    Detail=f"{units} units, EUR {value:,.2f}"))
    oc_q = collections.Counter()
    for l in lines:
        for s, q in l["sizes_norm"].items():
            oc_q[(l["code"], s)] += q
    po_q = collections.Counter()
    for r in rows:
        po_q[(r[CODE], norm_size(r["Option2 Value"]))] += r["Order Qty"]
    bad = [k for k in set(oc_q) | set(po_q) if oc_q[k] != po_q[k]]
    out.append(dict(Check="Qty per code+size equals the OC", Result="FAIL" if bad else "PASS",
                    Detail=f"{len(bad)} mismatches" + (f": {sorted(bad)[:5]}" if bad else "")))
    if dup:
        out.append(dict(Check="Each join key on one OC line", Result="WARN", Detail=f"repeated: {dup}"))
    new = [r for r in rows if r["Command"] == "NEW"]
    types = set(tag_lookup)
    badt = sorted({r["Type"] for r in new if r["Type"] not in types})
    out.append(dict(Check="Every NEW Type is in expected_tags.xlsx", Result="FAIL" if badt else "PASS",
                    Detail=", ".join(badt) or f"{len({r['Type'] for r in new})} Types"))
    mt = [r for r in rows if r["Command"] == "MERGE" and r["Type"] not in types]
    if mt:
        out.append(dict(Check="MERGE (live) Type is in expected_tags.xlsx", Result="WARN",
                        Detail=", ".join(sorted({f"{r['Title']}: {r['Type']}" for r in mt}))))
    low = [r for r in new if not r["Variant Price"] or r["Variant Price"] / r["Variant Cost"] < brand["margin_floor"]]
    out.append(dict(Check=f"NEW rows clear the {brand['margin_floor']}x margin", Result="FAIL" if low else "PASS",
                    Detail=f"{len(new) - len(low)} of {len(new)} rows"))
    lowm = sorted({(r[CODE], r["Title"], r["Variant Price"], r["Variant Cost"]) for r in rows
                   if r["Command"] == "MERGE" and r["Variant Price"] / r["Variant Cost"] < brand["margin_floor"]})
    if lowm:
        out.append(dict(Check=f"MERGE live price clears {brand['margin_floor']}x at this season's cost", Result="WARN",
                        Detail="; ".join(f"{t} A${p:g} / A${c:g} = {p / c:.2f}x" for _, t, p, c in lowm)))
    skus = [r["Variant SKU"] for r in rows]
    new_skus = [r["Variant SKU"] for r in rows if r["Variant SKU"].startswith(brand["sku_prefix"])]
    clash = [s for s in new_skus if s in catalog.skus]
    dupe = [s for s, n in collections.Counter(skus).items() if n > 1]
    out.append(dict(Check="SKUs unique in the sheet and new SKUs absent from the catalog",
                    Result="FAIL" if clash or dupe else "PASS",
                    Detail=f"{len(new_skus)} new SKUs ({min(new_skus, default='')}..{max(new_skus, default='')}); "
                           f"{len(clash)} in catalog, {len(dupe)} duplicated"))
    long_tags = sorted({r.get(TITLE_TAG) for r in new if len(r.get(TITLE_TAG) or "") > 60})
    out.append(dict(Check="title_tag <= 60 chars (the validator's own check never fires)", Result="WARN" if long_tags else "PASS",
                    Detail=f"{len(long_tags)} over 60" + (": " + " | ".join(long_tags[:8]) if long_tags else "")))
    by_title = collections.defaultdict(set)
    for r in new:
        by_title[r["Title"]].add(r[CODE])
    multi = {t: c for t, c in by_title.items() if len(c) > 1}
    out.append(dict(Check="One manufacture code per NEW title (Matrixify groups NEW rows by Title)",
                    Result="FAIL" if multi else "PASS", Detail=str(multi) if multi else f"{len(by_title)} titles"))
    sale = sorted({f"{p['title']} (A${v['price']:g} vs compare-at A${v['compare_at']:g})"
                   for l in lines for p in [catalog.match(l["code"])[0]] if p is not None
                   for v in p["variants"] if (v["compare_at"] or 0) > (v["price"] or 0)})
    if sale:
        out.append(dict(Check="Carryover products currently marked down", Result="WARN",
                        Detail="; ".join(sale) + ". The draft carries full price; the PU-merge does not touch price."))
    return out
