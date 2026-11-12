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

from .brand import ColourCodeError, code_for, unmapped_colours
from .poxlsx import PO_HEADER, CODE, MADE_IN, SEASON, NEW_SALE, COST_PRICE, TITLE_TAG, review_row, col_letter
from .resolve import Resolver, COLOUR, AI_OC, AI_NEW_SIZE, NO_RRP, split_code, worse

PRICE_CHECK = "Every NEW row has a retail price"
from .util import norm_size, sort_sizes, size_key, fmt_money, cost_currency, slug, store_sizes, size_clashes, \
    one_name_per_size

PRICE_COL = col_letter(PO_HEADER, "Variant Price")
SIZE_NAMES_CHECK = "Each size has one name per product"


def _live_size_names(R, code, vendor):
    """The store's size names a NEW product may take: its live same-fabric, then same-style siblings' ({name: the
    sibling}), and the brand-wide count of live products carrying each name, for a rename only a clear majority
    supports. A product's Type is no guide: one live dress in IT sizes renamed four bare-sized ones (2026-11-13)."""
    sib = []
    for level in ("fabric", "style"):
        names = {}
        for p in R.siblings(code, level):
            if p["status"].upper() != "ARCHIVED":
                for v in p["variants"]:
                    if v["size"]:
                        names.setdefault(v["size"], f"live sibling {p['title']}")
        sib.append(names)
    counts = collections.Counter()
    for p in R.cat.products.values():
        if p["status"].upper() != "ARCHIVED" and (p.get("vendor") or "").upper() == (vendor or "").upper():
            counts.update({v["size"] for v in p["variants"] if v["size"]})
    return sib, counts


def oc_lines(docs, brand=None):
    """One dict per OC line, keyed by `code`: the store's manufacture_code by the brand's join_key."""
    missing = unmapped_colours(brand, docs)
    if missing:
        raise ColourCodeError(missing)
    out = []
    for d in docs:
        for l in d["lines"]:
            code = code_for(brand, l["style"], l["fabric"], l["colour"])
            sizes = {}
            for s, q in l["sizes"].items():
                sizes[norm_size(s)] = sizes.get(norm_size(s), 0) + q
            out.append(dict(l, code=code, doc=d["doc"], collection=d["collection"],
                            gender=d.get("gender"), sizes_norm=sizes))
    return out


def build(docs, brand, catalog, history, tag_lookup, cart=None):
    lines = oc_lines(docs, brand)
    dup = [c for c, n in collections.Counter(l["code"] for l in lines).items() if n > 1]
    R = Resolver(brand, catalog, history, tag_lookup, lines, cart)
    rows, cells, checks = [], [], []
    cur = {}

    def cell(row, key, value, source, detail):
        l = cur.get("line")
        if source == "OC" and l is not None and l.get("reader") == "ai":
            source, detail = AI_OC, f"read by AI from {l['doc']} line {l['line_no']}; {detail}"
        row[key] = value
        cells.append((len(rows), key, value, source, detail))

    pending = []
    for l in lines:
        cur["line"] = l
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
            l["sizes_norm"], l["size_notes"] = store_sizes(l["sizes_norm"], [{s: f"the live product {product['title']}"
                                                                     for s in live}], brand.get("size_map"))
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
                if v:
                    cell(row, "Option2 Value", size, *(("rule", l["size_notes"][size]) if size in l["size_notes"]
                                                       else ("catalog", "live variant")))
                elif l.get("reader") == "ai":
                    cell(row, "Option2 Value", size, AI_NEW_SIZE,
                         f"read by AI from {l['doc']} line {l['line_no']}; ordered size the live product does not "
                         "have: check it against the PDF")
                else:
                    cell(row, "Option2 Value", size, "OC", "ordered size the live product does not have")
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
        sib, counts = _live_size_names(R, code, brand.get("vendor"))
        l["sizes_norm"], l["size_notes"] = store_sizes(l["sizes_norm"], sib, brand.get("size_map"), counts)
        pending.append(dict(
            l=l, code=code, eur=eur, gender=gender, ptype=(ptype, t_src, t_why), title=[title, ti_src, ti_why],
            fam=R.colour_family(code, cname), run=R.size_run(code, l["sizes_norm"], ptype),
            price=R.retail(code, l), made=R.made_in(code), comp=R.composition(code)))

    _dedupe_titles(pending, R, brand, checks)
    for n in pending:
        l, code, eur = n["l"], n["code"], n["eur"]
        cur["line"] = l
        title, ti_src, ti_why = n["title"]
        ptype, t_src, t_why = n["ptype"]
        fam, f_src, f_why = n["fam"]
        run, run_src, run_why = n["run"]
        run = one_name_per_size(run, l["sizes_norm"])   # siblings that spell a size two ways give it one name
        price, p_src, p_why = n["price"]
        made, made_src, made_why = n["made"]
        comp, comp_src, comp_why = n["comp"]
        for size in run:
            row = {}
            cell(row, "Command", "NEW", "catalog", f"join key {code} is not a live product")
            cell(row, "Title", title, ti_src, ti_why)
            cell(row, "Vendor", brand["vendor"], "rule", "brand config")
            cell(row, "Type", ptype, t_src, t_why)
            cell(row, "Body HTML", R.body_html(code, comp, made, comp_src == "catalog"), "guess" if "guess" in (comp_src, made_src)
                 else "cart" if "cart" in (comp_src, made_src) else "catalog",
                 f"template; composition: {comp_why}; made in: {made_why}")
            cell(row, "Status", brand["status"], "rule", "brand config")
            cell(row, "Published", brand["published_new"], "rule",
                 "created Active and off the Online Store; it can still show in POS and other sales channels")
            cell(row, "Option1 Name", brand["option1_name"], "rule", "house option order")
            cell(row, "Option1 Value", fam, f_src, f_why)
            cell(row, "Option2 Name", brand["option2_name"], "rule", "house option order")
            cell(row, "Option2 Value", size, *(("rule", l["size_notes"][size]) if size in l["size_notes"] else
                                               ("OC", "ordered size") if size in l["sizes_norm"] else (run_src, run_why)))
            sku, s_, why = R.next_sku()
            cell(row, "Variant SKU", sku, s_, why)
            cell(row, "Variant Price", price, p_src, p_why)
            compare = f"={PRICE_COL}{len(rows) + 2}"   # this row's Variant Price, so a typed price is copied
            if price is None:
                cell(row, "Variant Compare At Price", compare, p_src,
                     f"{p_why}; this cell copies Variant Price (a formula): type the price there")
            else:
                cell(row, "Variant Compare At Price", compare, "rule", "compare-at = price at creation (a formula)")
            _money_cells(cell, row, R, brand, eur, l["sizes_norm"].get(size, 0))
            cell(row, CODE, code, "OC", f"{l['doc']} line {l['line_no']} ({l['product_class']})")
            cell(row, SEASON, brand["season"], "rule", "brand config")
            cell(row, MADE_IN, made, made_src, made_why)
            cell(row, NEW_SALE, brand["new_sale"], "rule", "new stock is created NEW")
            cell(row, TITLE_TAG, R.title_tag(title), ti_src, "house title_tag template over the drafted title")
            rows.append(row)

    names = collections.defaultdict(set)
    for r in rows:
        names[r.get(CODE)].add(r.get("Option2 Value"))
    clash = [f"{c}: " + ", ".join(" and ".join(g) for g in size_clashes(ss)) for c, ss in names.items() if size_clashes(ss)]
    if clash:
        checks.append(dict(Check=SIZE_NAMES_CHECK, Result="WARN", Detail=(
            f"{len(clash)} product(s) carry one size under two names, because the live product holds both: "
            + "; ".join(clash) + ". Keep the store's name and move the quantity onto it.")))
    if cart is not None:
        checks += cart_checks(lines, cart, brand)
    checks = _draft_checks(docs, lines, rows, R, brand, catalog, tag_lookup, dup) + checks
    review = [review_row(i + 2, rows[i].get(CODE), rows[i].get("Option2 Value"), k, v, s, w)
              for i, k, v, s, w in cells]
    fills = {(i, k): COLOUR.get(s, "red") for i, k, v, s, w in cells}
    return rows, review, checks, fills


def cart_checks(lines, cart, brand=None):
    """How the cart lines up with the OC. Zero shared codes means the wrong cart."""
    cur = cost_currency(brand)
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
                        Detail=f"{len(diff)} code(s): " + "; ".join(f"{c} cart {w:g} vs OC {cur} {p:g}" for c, w, p in diff)))
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
    cur = cost_currency(brand)
    cell(row, "Variant Cost", R.cost(eur), "rule", f"{cur} {eur:g} x {brand['cost_multiplier']} (season cost convention)")
    cell(row, COST_PRICE, fmt_money(eur, cur), "OC", "confirmed unit price")
    cell(row, "Wholesale Price", eur, "OC", "confirmed unit price")
    cell(row, "Order Qty", qty, "OC" if qty else "rule", "confirmed quantity" if qty else "size in the run, not ordered")
    cell(row, "Total Order", round(qty * eur, 2), "OC", "Order Qty x Wholesale Price")


def _draft_checks(docs, lines, rows, R, brand, catalog, tag_lookup, dup):
    from .adapters import otb
    t = otb.totals(docs)
    cur = cost_currency(brand)
    out = []
    ok = t["units"] == t["printed_units"] and abs(t["value"] - t["printed_value"]) < 0.01
    out.append(dict(Check="OC totals reproduced (parsed = printed)", Result="PASS" if ok else "FAIL",
                    Detail=f"{t['docs']} confirmations, {t['lines']} lines, {t['units']} units, {cur} {t['value']:,.2f} "
                           f"(printed {t['printed_units']} / {cur} {t['printed_value']:,.2f})"))
    units = sum(r["Order Qty"] for r in rows)
    value = round(sum(r["Total Order"] for r in rows), 2)
    ok = units == t["units"] and abs(value - t["value"]) < 0.01
    out.append(dict(Check="PO Order Qty and Total Order equal the OC", Result="PASS" if ok else "FAIL",
                    Detail=f"{units} units, {cur} {value:,.2f}"))
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
    floor = brand["margin_floor"]
    blank = [r for r in new if r["Variant Price"] is None]
    out.append(dict(Check=PRICE_CHECK, Result="FAIL" if blank else "PASS",
                    Detail=(f"{NO_RRP}: {len(blank)} of {len(new)} rows, {len({r[CODE] for r in blank})} products. "
                            "Type each retail price into the sheet." if blank else f"{len(new)} rows priced")))
    priced = [r for r in new if r["Variant Price"] is not None]
    low = sorted({(r[CODE], r["Variant Price"], r["Variant Cost"]) for r in priced
                  if r["Variant Price"] / r["Variant Cost"] < floor})
    out.append(dict(Check=f"NEW rows clear the {floor}x margin", Result="FAIL" if low else "PASS",
                    Detail=(f"{len(low)} brand RRP(s) under the floor, kept as the brand gave them (never raised): "
                            + "; ".join(f"{c} A${p:g} / A${k:g} = {p / k:.2f}x" for c, p, k in low)
                            + ". Step 2's margin gate refuses them: agree a price, or ask Colwyn about the floor."
                            if low else f"{len(priced)} priced rows" + (f"; {len(blank)} blank rows have no price "
                                                                        "to check yet" if blank else ""))))
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
    multi = {t: sorted(c) for t, c in by_title.items() if len(c) > 1}
    out.append(dict(Check="One manufacture code per NEW title (Matrixify groups NEW rows by Title)",
                    Result="FAIL" if multi else "PASS", Detail=str(multi) if multi else f"{len(by_title)} titles"))
    sale = sorted({f"{p['title']} (A${v['price']:g} vs compare-at A${v['compare_at']:g})"
                   for l in lines for p in [catalog.match(l["code"])[0]] if p is not None
                   for v in p["variants"] if (v["compare_at"] or 0) > (v["price"] or 0)})
    if sale:
        out.append(dict(Check="Carryover products currently marked down", Result="WARN",
                        Detail="; ".join(sale) + ". The draft carries full price; the PU-merge does not touch price."))
    return out
