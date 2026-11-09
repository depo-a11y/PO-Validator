"""Compare a drafted PO with a hand-built one (the answer key) and with the OC."""
import collections

from .draft import oc_lines
from .poxlsx import read_po, CODE
from .util import norm_size, norm_code, size_key


def _norm(v):
    return " ".join(str(v or "").split()).casefold()


def compare(draft_rows, hand_rows, docs, allow_price=(), brand=None):
    lines = oc_lines(docs, brand)
    oc_q, oc_eur = collections.Counter(), {}
    for l in lines:
        oc_eur[l["code"]] = l["price"]
        for s, q in l["sizes_norm"].items():
            oc_q[(l["code"], s)] += q

    def qty(rows):
        c = collections.Counter()
        for r in rows:
            c[(norm_code(r[CODE]), norm_size(r["Option2 Value"]))] += int(r.get("Order Qty") or 0)
        return c

    res = {}
    for name, rows in (("draft", draft_rows), ("hand", hand_rows)):
        q = qty(rows)
        keys = set(q) | set(oc_q)
        res[f"{name}_qty_mismatch"] = sorted(k for k in keys if q[k] != oc_q[k])
        res[f"{name}_units"] = sum(q.values())
        res[f"{name}_value"] = round(sum(float(r.get("Total Order") or 0) for r in rows), 2)
        eur = {}
        for r in rows:
            eur.setdefault(norm_code(r[CODE]), set()).add(float(r.get("Wholesale Price") or 0))
        res[f"{name}_price_diff"] = sorted((c, sorted(v), oc_eur.get(c)) for c, v in eur.items()
                                           if any(abs(x - oc_eur.get(c, -1)) > 0.005 for x in v))
    dq, hq = qty(draft_rows), qty(hand_rows)
    res["draft_vs_hand_qty"] = sorted(k for k in set(dq) | set(hq) if dq[k] != hq[k])
    res["oc_units"], res["oc_value"] = sum(oc_q.values()), round(sum(l["line_value"] for l in lines), 2)
    res["allowed_price"] = [d for d in res["hand_price_diff"] if d[0] in allow_price]
    res["unexpected_price"] = [d for d in res["hand_price_diff"] if d[0] not in allow_price]

    # ---- agreement on the hand PO's NEW rows ---------------------------------
    d_by = {}
    for r in draft_rows:
        d_by[(norm_code(r[CODE]), norm_size(r["Option2 Value"]))] = r
    d_codes = collections.defaultdict(list)
    for r in draft_rows:
        d_codes[norm_code(r[CODE])].append(r)
    hnew = [r for r in hand_rows if str(r["Command"]).strip().upper() == "NEW"]
    fields = {"Title": "Title", "Type": "Type", "Option1": "Option1 Value"}
    agree = {k: 0 for k in fields}
    dis = {k: [] for k in fields}
    run_rows = 0
    for r in hnew:
        code, size = norm_code(r[CODE]), norm_size(r["Option2 Value"])
        dr = d_by.get((code, size)) or (d_codes[code][0] if d_codes[code] else {})
        for k, col in fields.items():
            if _norm(dr.get(col)) == _norm(r.get(col)):
                agree[k] += 1
        if (code, size) in d_by:
            run_rows += 1
    # per product
    hcodes = collections.OrderedDict()
    for r in hnew:
        hcodes.setdefault(norm_code(r[CODE]), []).append(r)
    prod = {k: 0 for k in fields}
    run_prod = 0
    for code, rs in hcodes.items():
        dr = d_codes[code][0] if d_codes[code] else {}
        for k, col in fields.items():
            if _norm(dr.get(col)) == _norm(rs[0].get(col)):
                prod[k] += 1
            else:
                dis[k].append((code, rs[0].get(col), dr.get(col)))
        hs = [norm_size(x["Option2 Value"]) for x in rs]
        ds = [norm_size(x["Option2 Value"]) for x in d_codes[code]]
        if sorted(hs, key=size_key) == sorted(ds, key=size_key):
            run_prod += 1
        else:
            dis.setdefault("Size run", []).append((code, " ".join(sorted(hs, key=size_key)), " ".join(sorted(ds, key=size_key))))
    res["new_codes"] = list(hcodes)
    res["new_rows"] = len(hnew)
    res["new_products"] = len(hcodes)
    res["agree_rows"] = dict(agree, **{"Size run": run_rows})
    res["agree_products"] = dict(prod, **{"Size run": run_prod})
    res["disagree"] = dis
    extra = [k for k in d_by if k not in {(norm_code(r[CODE]), norm_size(r["Option2 Value"])) for r in hand_rows}]
    res["draft_extra_rows"] = sorted(extra)
    res["draft_commands"] = collections.Counter(r["Command"] for r in draft_rows)
    res["hand_commands"] = collections.Counter(str(r["Command"]).strip() for r in hand_rows)
    hcmd = {norm_code(r[CODE]): str(r["Command"]).strip() for r in hand_rows}
    dcmd = {norm_code(r[CODE]): r["Command"] for r in draft_rows}
    res["command_disagree"] = sorted(c for c in hcmd if hcmd[c] != dcmd.get(c))
    return res


def pct(n, d):
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def by_colour(draft_path, res):
    """Agreement split by the colour the draft gave the cell: is green trustworthy?"""
    import openpyxl
    ws = openpyxl.load_workbook(draft_path, read_only=True)["Review"]
    it = ws.iter_rows(values_only=True)
    hdr = next(it)
    colour = {}
    for row in it:
        x = dict(zip(hdr, row))
        colour.setdefault((x["Code"], x["Column"]), x["Colour"])
    out = {}
    for k, col in (("Title", "Title"), ("Type", "Type"), ("Option1", "Option1 Value")):
        bad = {c for c, _, _ in res["disagree"][k]}
        tally = collections.defaultdict(lambda: [0, 0])
        for code in res["new_codes"]:
            c = colour.get((code, col), "none")
            tally[c][1] += 1
            tally[c][0] += code not in bad
        out[k] = dict(tally)
    return out


def run(draft_path, hand_path, docs, out, allow_price=(), brand=None):
    _, d = read_po(draft_path)
    _, h = read_po(hand_path)
    r = compare(d, h, docs, set(allow_price), brand)
    r["by_colour"] = by_colour(draft_path, r)
    ok = not r["draft_qty_mismatch"] and not r["hand_qty_mismatch"] and not r["unexpected_price"]
    write_md(r, out, draft_path, hand_path)
    return ok


def write_md(r, out, draft_path, hand_path, extra=""):
    L = []
    a, n = r["agree_rows"], r["new_rows"]
    ap, np_ = r["agree_products"], r["new_products"]
    L.append("| Check | Result |\n|---|---|")
    L.append(f"| OC | {r['oc_units']} units, EUR {r['oc_value']:,.2f} |")
    L.append(f"| Draft vs OC, qty per code+size | {len(r['draft_qty_mismatch'])} mismatches; {r['draft_units']} units, EUR {r['draft_value']:,.2f} |")
    L.append(f"| Hand PO vs OC, qty per code+size | {len(r['hand_qty_mismatch'])} mismatches; {r['hand_units']} units, EUR {r['hand_value']:,.2f} |")
    L.append(f"| Draft vs hand PO, qty per code+size (zero rows count as 0) | {len(r['draft_vs_hand_qty'])} mismatches |")
    L.append(f"| Draft wholesale vs OC | {len(r['draft_price_diff'])} codes differ |")
    L.append(f"| Hand PO wholesale vs OC | {len(r['hand_price_diff'])} codes differ: "
             + "; ".join(f"{c} EUR {v} vs OC EUR {o:g}" for c, v, o in r["hand_price_diff"]) + " |")
    L.append(f"| MERGE / NEW split | draft {dict(r['draft_commands'])}, hand {dict(r['hand_commands'])}; "
             f"{len(r['command_disagree'])} codes disagree |")
    L.append("")
    L.append(f"**Agreement with the hand PO on its {n} NEW rows ({np_} products)**\n")
    L.append("| Field | Rows | Products |\n|---|---|---|")
    for k in ("Title", "Type", "Option1", "Size run"):
        L.append(f"| {k} | {pct(a[k], n)} | {pct(ap[k], np_)} |")
    L.append("")
    if r.get("by_colour"):
        L.append("\n**Is the colour coding honest?** Products agreeing with the hand PO, by the colour the draft gave the cell:\n")
        L.append("| Field | green | amber | red |\n|---|---|---|---|")
        for k, t in r["by_colour"].items():
            cells = [f"{t[c][0]}/{t[c][1]}" if c in t else "-" for c in ("green", "amber", "red")]
            L.append(f"| {k} | " + " | ".join(cells) + " |")
        L.append("")
    L.append("Row-level size run = the hand PO row's code+size exists in the draft. Product-level size run = the "
             "two size lists are identical. Text compares ignore case and repeated spaces.\n")
    if r["draft_extra_rows"]:
        L.append(f"Draft rows the hand PO does not have: {len(r['draft_extra_rows'])} (zero-qty sizes from a different run).\n")
    for k in ("Title", "Type", "Option1", "Size run"):
        rows = r["disagree"].get(k, [])
        if not rows:
            continue
        L.append(f"### {k}: {len(rows)} products disagree\n")
        L.append("| Code | Hand PO | Draft |\n|---|---|---|")
        for code, hv, dv in rows:
            L.append(f"| {code} | {hv} | {dv} |")
        L.append("")
    if extra:
        L.append(extra)
    open(out, "w").write("\n".join(L) + "\n")
    return "\n".join(L)
