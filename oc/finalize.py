"""`oc finalize`: a reviewed PO -> PU-new, PU-merge (+ new sizes, + rollback) and the delivery file.

All or nothing: every check runs first, in memory. If any check FAILs, no upload
or delivery file is written (stale ones from an earlier run at the same prefix
are removed) and only the checks workbook is saved, so a failed gate can never
leave a half set of importable files behind.

PU-new   NEW rows through validator_core.run_gate, the same gate as app.py, plus
         blocks on a Title still holding a raw colour/fabric/style code and on a
         blank Option1 (what an unreviewed draft leaves behind).
PU-merge carryover, keyed on known product and variant IDs, so Command = UPDATE and
         Variant Command = UPDATE (both fail on a missing ID instead of creating).
         NOT co_stock's full column battery: a blank cell clears that field on
         import, and the PO leaves Body HTML, title_tag, HS code, GTIN and the
         variant metafields blank on MERGE rows. So the sheet carries only what
         this buy changes on a live product: the season metafield and season tag
         (on the product's first row) and the unit cost (every row).
         Cost rule (Colwyn, 2026-11-09): "OC should overwrite old season cost
         prices", on all sizes. A re-bought product gets Variant Cost = the OC's
         Wholesale Price x the brand's cost_multiplier on EVERY live variant,
         sizes not on the OC included, so it ends with one cost. A PO Variant Cost
         that disagrees with that is a FAIL. The Variant.cost_price metafield is
         not written on existing variants: the catalog export carries no variant
         metafields, so a rollback could not restore it.
         New sizes on live products go in a separate file (Command UPDATE, Variant
         Command MERGE) at the same OC cost. A rollback file restores the previous
         season, removes the tag where this run added it, and puts back each
         touched variant's prior Variant Cost from the catalog export.
         A product with no season in the export (custom.product_season is a
         required catalog column, so an empty cell means no metafield) gets an
         empty rollback season cell: Matrixify deletes a metafield on an empty
         cell, which is the exact undo of PU-merge setting the season (INFO names
         those products).
         The rollback undoes only the PU-merge of its own run: use the ROLLBACK
         from the same zip as the PU-merge you import, made before importing. Every
         file of a run carries one run id (its file-name prefix, also the workbook
         Title and the first finalize-checks row), so the two can be matched. A
         catalog that already looks imported is a WARN.
Delivery the brand delivery workbook shape, with a Total Delivered roll-up that
         absorbs future tranche columns and Remaining = Order Qty - Total Delivered.
"""
import collections
import itertools
import os
import re
import sys
from pathlib import Path

import openpyxl
import pandas as pd
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .poxlsx import (read_po, CODE, MADE_IN, SEASON, NEW_SALE, COST_PRICE, TITLE_TAG, AUD_FMT, money_format,
                     write_table, write_frame, append_text)
from .draft import PRICE_CHECK
from .util import blank, cost_currency, norm_code, norm_size, money, slug

REPO = Path(__file__).resolve().parent.parent

DELIVERY_HEADER = [
    "Title", "Vendor", "Type", "Option1 Value", "Option2 Value", "Variant SKU", "Variant Price",
    "Variant Compare At Price", "Variant Cost", "Metafield: my_fields.manufacture_code", "remove space",
    "FULLCODE", "Metafield: custom.product_season [single_line_text_field]",
    "Metafield: custom.made_in [single_line_text_field]", "Metafield: custom.new_sale [single_line_text_field]",
    "Variant Metafield: Variant.cost_price [single_line_text_field]", "Wholesale Price", "Total Order",
    "Order Qty", "Total Delivered", "Remaining", "1D", "Value", "2D", "Value",
]

ID_COMMAND = "UPDATE"
VARIANT_COMMAND = "UPDATE"
MERGE_PRODUCT_COLS = ["ID", "Command", "Tags Command", "Tags", SEASON]
ROLLBACK_COLS = ["ID", "Command", "Tags Command", "Tags", SEASON]
VARIANT_COST_COLS = ["Variant ID", "Variant Command", "Variant Cost"]
NEW_SIZE_COLS = ["ID", "Command", "Variant Command", "Option1 Name", "Option1 Value", "Option2 Name",
                 "Option2 Value", "Variant SKU", "Variant Barcode", "Variant Price", "Variant Compare At Price",
                 "Variant Cost", "Variant Inventory Tracker", "Variant HS Code", "Variant Country of Origin",
                 "Variant Metafield:custom.manufacture_code[single_line_text_field]",
                 "Variant Metafield: custom.season [single_line_text_field]",
                 "Variant Metafield: custom.new_sale [single_line_text_field]", COST_PRICE]

OUTPUTS = ["PU-new.xlsx", "PU-merge.xlsx", "PU-merge-new-sizes.xlsx", "PU-merge-ROLLBACK.xlsx", "Delivery.xlsx"]
RUN_CHECK = "Run id (in every file name of this run and in each workbook's Title)"


def run_id(out_prefix):
    """The run id: the file-name prefix without its trailing '-' ('2026-11-09-1432-margiela-ss27')."""
    return os.path.basename(str(out_prefix)).rstrip("-")


def run_check(out_prefix):
    rid = run_id(out_prefix)
    return dict(Check=RUN_CHECK, Result="INFO",
                Detail=f"{rid}. Import PU-merge only with the PU-merge-ROLLBACK of this same run id, kept from before "
                       f"the import: {rid}-PU-merge.xlsx goes with {rid}-PU-merge-ROLLBACK.xlsx. A ROLLBACK from "
                       "another run is built from that run's PO and catalog, so it can miss products or costs this "
                       "PU-merge changes.")


def save_checks(path, checks, out_prefix):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    wb.properties.title = run_id(out_prefix)
    write_table(wb, "Checks", checks, colour_key="Result")
    wb.save(path)


def _s(v):
    return "" if blank(v) else str(v).strip()


def _cmd(r):
    return _s(r.get("Command")).upper()


def _r2(v):
    v = money(v)
    return None if v is None else round(v, 2)


def run(po_path, brand, catalog, out_prefix):
    sys.path.insert(0, str(REPO))
    import validator_core as core

    _, rows = read_po(po_path)
    checks, writers = [run_check(out_prefix)], []
    new = [r for r in rows if _cmd(r) == "NEW"]
    merge = [r for r in rows if _cmd(r) == "MERGE"]
    other = [r for r in rows if _cmd(r) not in ("NEW", "MERGE")]
    if other:
        checks.append(dict(Check="Every PO row is NEW or MERGE", Result="FAIL",
                           Detail=f"rows {[r['_row'] for r in other]}"))

    for build in (_pu_new(new, brand, catalog, core, out_prefix),
                  _pu_merge(merge, brand, catalog, core, out_prefix),
                  _delivery(rows, out_prefix, cost_currency(brand))):
        c, w = build
        checks += c
        writers += w

    failed = [c for c in checks if c["Result"] == "FAIL"]
    if failed:
        removed = []
        for name in OUTPUTS:
            p = f"{out_prefix}{name}"
            if os.path.exists(p):
                os.remove(p)
                removed.append(os.path.basename(p))
        checks.append(dict(Check="All or nothing: no upload or delivery file written", Result="FAIL",
                           Detail=f"{len(failed)} check(s) failed"
                                  + (f"; removed stale {', '.join(removed)}" if removed else "")))
    else:
        for path, write, note in writers:
            write(path)
            checks.append(dict(Check="Written", Result="INFO", Detail=f"{path}: {note}"))
    save_checks(f"{out_prefix}finalize-checks.xlsx", checks, out_prefix)
    return checks


# ---- PU-new --------------------------------------------------------------------
def raw_code_in_title(title, code):
    """Tokens of the manufacture code (style, style+fabric, fabric, colour) left in a title."""
    code = norm_code(code)
    body, _, colour = code.partition(" ")
    parts = {p for p in (body, body[:9], body[9:], colour) if len(p) >= 2}
    toks = {t.upper() for t in re.split(r"[\s/,()]+", str(title or "")) if t}
    return sorted(parts & toks)


def _pu_new(new, brand, catalog, core, out_prefix):
    out = []
    recs = []
    for r in new:
        made = _s(r.get(MADE_IN))
        recs.append({
            "Command": "NEW", "Title": _s(r["Title"]), "Vendor": _s(r["Vendor"]), "Type": _s(r["Type"]),
            "Body HTML": _s(r.get("Body HTML")), "Status": _s(r["Status"]), "Published": r["Published"],
            "Option1 Name": _s(r["Option1 Name"]), "Option1 Value": _s(r["Option1 Value"]),
            "Option2 Name": _s(r["Option2 Name"]), "Option2 Value": norm_size(r["Option2 Value"]),
            "Variant SKU": _s(r["Variant SKU"]), "Variant Price": _r2(r["Variant Price"]),
            "Variant Compare At Price": _r2(r["Variant Compare At Price"]),
            "Variant Cost": _r2(r["Variant Cost"]),
            "Metafield: my_fields.manufacture_code": norm_code(r[CODE]),
            SEASON: _s(r.get(SEASON)), MADE_IN: "" if made == brand["unknown"] else made,
            NEW_SALE: _s(r.get(NEW_SALE)), COST_PRICE: _s(r.get(COST_PRICE)),
            "Variant Metafield: Variant.gtin [single_line_text_field]": _s(r.get("Variant Metafield: Variant.gtin [single_line_text_field]")),
            "HS Code": _s(r.get("Variant Metafield: HS Code")),
            "Metafield: title_tag [string]": _s(r.get(TITLE_TAG)),
        })
    if not recs:
        return out, []
    df = pd.DataFrame(recs)
    df_in = df.copy()

    raw = sorted({f"'{t}' ({', '.join(raw_code_in_title(t, c))})" for t, c in
                  zip(df["Title"], df["Metafield: my_fields.manufacture_code"]) if raw_code_in_title(t, c)})
    out.append(dict(Check="No NEW Title still holds a raw style/fabric/colour code", Result="FAIL" if raw else "PASS",
                    Detail=f"{len(raw)}: " + "; ".join(raw) if raw else f"{df['Title'].nunique()} titles"))
    no_opt = sorted({t for t, o in zip(df["Title"], df["Option1 Value"]) if not o})
    out.append(dict(Check="Every NEW row has an Option1 (colour family)", Result="FAIL" if no_opt else "PASS",
                    Detail=f"{len(no_opt)} products blank: " + "; ".join(no_opt) if no_opt else "all set"))
    unpriced = [r for r in new if _r2(r["Variant Price"]) is None]
    out.append(dict(Check=PRICE_CHECK, Result="FAIL" if unpriced else "PASS",
                    Detail=(f"fill the retail price: {len(unpriced)} NEW row(s) have none, PO rows "
                            + ", ".join(str(r["_row"]) for r in unpriced[:25]) + (" and more" if len(unpriced) > 25 else "")
                            + " (SKUs " + ", ".join(_s(r["Variant SKU"]) for r in unpriced[:8])
                            + (", ..." if len(unpriced) > 8 else "") + ")"
                            if unpriced else f"{len(new)} NEW rows priced")))
    if unpriced:
        return out, []

    report = f"{out_prefix}PU-new-VALIDATION_ERRORS_REPORT.xlsx"
    if os.path.exists(report):
        os.remove(report)
    try:
        df, errors, typos = core.run_gate(df, template_file=str(REPO / "expected_tags.xlsx"), report_path=report)
    except core.ValidationStop as e:
        out.append(dict(Check="PU-new passes the po-validator gate (validator_core.run_gate)", Result="FAIL",
                        Detail=f"{e} {e.rows[:5]}"))
        return out, []
    out.append(dict(Check="PU-new passes the po-validator gate (validator_core.run_gate)",
                    Result="FAIL" if errors else "PASS",
                    Detail=f"{len(df)} rows; hard stops: none; VALIDATION_ERRORS: {len(errors)}"
                           + (f" -> {report}" if errors else " (no report written)")))
    if typos:
        out.append(dict(Check="Typo check (advisory)", Result="WARN",
                        Detail=f"{len(typos)}: " + "; ".join(f"{t['Column']} '{t['Word']}'->{t['Suggestion']}" for t in typos[:12])))
    floor = brand["margin_floor"]
    ratios = (df["Variant Price"].astype(float) / df["Variant Cost"].astype(float))
    out.append(dict(Check=f"Margin >= {floor}x on every NEW row (independent recompute)",
                    Result="PASS" if (ratios >= floor).all() else "FAIL",
                    Detail=f"min {ratios.min():.3f}x, {int((ratios < floor).sum())} below"))
    clash = sorted(set(df["Variant SKU"]) & catalog.skus)
    out.append(dict(Check="PU-new SKUs absent from the catalog", Result="FAIL" if clash else "PASS",
                    Detail=f"{df['Variant SKU'].nunique()} SKUs, {len(clash)} in catalog"))
    tags = core.load_tag_lookup(str(REPO / "expected_tags.xlsx"))
    badt = sorted(set(df["Type"]) - set(tags))
    out.append(dict(Check="Every PU-new Type is in expected_tags.xlsx", Result="FAIL" if badt else "PASS",
                    Detail=", ".join(badt) or f"{df['Type'].nunique()} Types"))
    want = df.apply(lambda r: ", ".join(dict.fromkeys(tags.get(r["Type"], []) + [r["Vendor"], r[SEASON]])), axis=1)
    off = int((want != df["Tags"]).sum())
    out.append(dict(Check="Tags = expected_tags[Type] + Vendor + Season (catches the shared-list bug)",
                    Result="FAIL" if off else "PASS", Detail=f"{off} rows differ"))
    sub = "Metafield: custom.sub_category [single_line_text_field]"
    short = sorted({t for t, s in zip(df["Type"], df[sub]) if s != " ".join(t.split()[2:])})
    out.append(dict(Check="sub_category = the whole Type after gender and category", Result="FAIL" if short else "PASS",
                    Detail=", ".join(short) or f"{df[sub].nunique()} values, e.g. " + ", ".join(sorted(df[sub].unique())[:4])))
    by_title = df.groupby("Title")["Metafield: my_fields.manufacture_code"].nunique()
    multi = list(by_title[by_title > 1].index)
    out.append(dict(Check="One manufacture code per Title (Matrixify groups create rows by Title)",
                    Result="FAIL" if multi else "PASS", Detail=", ".join(multi) or f"{len(by_title)} products"))
    taken = sorted(t for t in by_title.index if slug(t) in catalog.handles)
    out.append(dict(Check="No PU-new title's handle is already live", Result="FAIL" if taken else "PASS",
                    Detail=", ".join(taken) or "none"))
    inv = df[core.INVENTORY_COLS].astype(float).abs().sum().sum()
    out.append(dict(Check="Every inventory column is zero", Result="PASS" if inv == 0 else "FAIL", Detail=f"sum {inv:g}"))
    scales = df.groupby("Title")["Metafield: custom.size_scale [single_line_text_field]"].unique()
    mixed = {t: list(v) for t, v in scales.items() if len(v) > 1}
    if mixed:
        out.append(dict(Check="One size_scale per product (product-level field: Matrixify keeps the first row's)",
                        Result="WARN", Detail="; ".join(f"{t}: {v}" for t, v in mixed.items())))
    long_tags = sorted({t for t in df["Metafield: title_tag [string]"] if len(t) > 60})
    out.append(dict(Check="title_tag <= 60 chars (validator check reads the wrong column, so measured here)",
                    Result="WARN" if long_tags else "PASS", Detail=f"{len(long_tags)} over 60"))
    if (df["Published"].astype(str).str.upper() == "FALSE").any():
        out.append(dict(Check="Published = FALSE has no Published Scope", Result="WARN",
                        Detail="Published = FALSE keeps them off the Online Store only. Matrixify defaults the scope "
                               "to global, so after import they may be on Point of Sale, Shop, Microsoft Copilot and "
                               "Meta AI and Muse. No sheet column sets Copilot or Meta AI, so check each product's "
                               "sales channels in Shopify after import, before stock lands."))
    blanked = int(((df_in[MADE_IN] == "") & (pd.Series([_s(r.get(MADE_IN)) for r in new]) == brand["unknown"])).sum())
    if blanked:
        out.append(dict(Check="made_in '-' placeholder not written to the store", Result="INFO",
                        Detail=f"{blanked} rows carry '{brand['unknown']}' on the PO; PU-new leaves custom.made_in empty instead"))
    final = df[core.COLUMNS_IN_ORDER]
    note = (f"{len(df)} rows, {df['Title'].nunique()} products, columns = app.py columns_in_order "
            f"({len(core.COLUMNS_IN_ORDER)})")
    return out, [(f"{out_prefix}PU-new.xlsx", lambda p: write_frame(p, final, "Products", run_id(out_prefix)), note)]


# ---- PU-merge ------------------------------------------------------------------
def _pu_merge(merge, brand, catalog, core, out_prefix):
    out, writers = [], []
    mdf = pd.DataFrame([{"Command": r["Command"]} for r in merge])
    bad = core.invalid_merge_commands(mdf) if len(mdf) else []
    out.append(dict(Check="co_stock gate: every carryover row is a MERGE row on the PO", Result="FAIL" if bad else "PASS",
                    Detail=f"{len(merge)} rows" + (f"; invalid: {bad}" if bad else "")))
    if not merge:
        return out, writers
    mult = brand["cost_multiplier"]
    products, new_sizes, problems, po_off = collections.OrderedDict(), [], [], []
    oc_costs = collections.defaultdict(set)
    for r in merge:
        code = norm_code(r[CODE])
        size = norm_size(r["Option2 Value"])
        eur = money(r.get("Wholesale Price"))
        cost = None if eur is None else round(eur * mult, 2)
        po = _r2(r.get("Variant Cost"))
        if cost is None or po is None or abs(po - cost) > 0.005:
            po_off.append(f"row {r['_row']} {code} size {size}: PO A${po} vs {cost_currency(brand)} {eur} x {mult} = A${cost}")
        p, note, _ = catalog.match(code)
        if p is None:
            problems.append(f"row {r['_row']} {code}: {note or 'no live product'}")
            continue
        products.setdefault(p["id"], (p, r))
        oc_costs[p["id"]].add(cost)
        if any(v["size"] == size for v in p["variants"]):
            continue
        sib = p["variants"]
        price = collections.Counter(x["price"] for x in sib).most_common(1)[0][0]
        cmp_ = collections.Counter(x["compare_at"] for x in sib).most_common(1)[0][0]
        hs = collections.Counter(x["hs"] for x in sib if x["hs"]).most_common(1)
        cty = collections.Counter(x["country"] for x in sib if x["country"]).most_common(1)
        names = p["option_names"]
        colour = collections.Counter(x["colour"] for x in sib if x["colour"]).most_common(1)[0][0]
        vals = {n.lower(): (colour if n.lower() in ("colour", "color") else size) for n in names}
        new_sizes.append({
            "ID": p["id"], "Command": ID_COMMAND, "Variant Command": "MERGE",
            "Option1 Name": names[0], "Option1 Value": vals[names[0].lower()],
            "Option2 Name": names[1], "Option2 Value": vals[names[1].lower()],
            "Variant SKU": _s(r["Variant SKU"]), "Variant Barcode": _s(r["Variant SKU"]),
            "Variant Price": price, "Variant Compare At Price": cmp_,
            "Variant Inventory Tracker": "shopify",
            "Variant HS Code": hs[0][0] if hs else "", "Variant Country of Origin": cty[0][0] if cty else "",
            "Variant Metafield:custom.manufacture_code[single_line_text_field]": p["raw_code"].strip(),
            "Variant Metafield: custom.season [single_line_text_field]": _s(r.get(SEASON)),
            "Variant Metafield: custom.new_sale [single_line_text_field]": p["new_sale"] or _s(r.get(NEW_SALE)),
            COST_PRICE: _s(r.get(COST_PRICE)), "_title": p["title"],
        })
    new_cost = {pid: next(iter(c)) for pid, c in oc_costs.items() if len(c) == 1 and None not in c}
    for n in new_sizes:
        n["Variant Cost"] = new_cost.get(n["ID"])

    out.append(dict(Check="Every MERGE row resolves to one live (non-archived) product",
                    Result="FAIL" if problems else "PASS", Detail="; ".join(problems) or f"{len(products)} products"))
    out.append(dict(Check=f"PO Variant Cost = Wholesale x {mult} on every MERGE row (the reviewed PO agrees with the OC cost)",
                    Result="FAIL" if po_off else "PASS",
                    Detail=f"{len(po_off)} rows: " + "; ".join(po_off) if po_off else f"{len(merge)} rows"))
    split_oc = [f"{products[pid][0]['title']} {sorted(c, key=str)}" for pid, c in oc_costs.items() if len(c) != 1]
    out.append(dict(Check="One OC cost per re-bought product", Result="FAIL" if split_oc else "PASS",
                    Detail="; ".join(split_oc) or f"{len(new_cost)} products"))
    archived = [p["id"] for p, _ in products.values() if p["status"].upper() == "ARCHIVED"]
    out.append(dict(Check="No MERGE target is ARCHIVED", Result="FAIL" if archived else "PASS", Detail=str(archived or "none")))
    split_live = [f"{p['title']} {sorted({round(v['cost'], 2) for v in p['variants'] if v['cost'] is not None})}"
                  for p, _ in products.values() if len({round(v["cost"], 2) for v in p["variants"] if v["cost"] is not None}) > 1]
    out.append(dict(Check="Re-bought products whose live sizes carry different costs today (the overwrite unifies them)",
                    Result="INFO", Detail=f"{len(split_live)}: " + "; ".join(split_live) if split_live else "none"))

    season = brand["season"]
    rows, rb = [], []
    for pid, (p, _) in products.items():
        tagged = season in [t.strip() for t in p["tags"].split(",")]
        for i, v in enumerate(p["variants"]):
            row = {"ID": pid, "Command": ID_COMMAND, "Variant ID": v["variant_id"], "Variant Command": VARIANT_COMMAND}
            back = dict(row)
            if i == 0:
                row.update({"Tags Command": "MERGE", "Tags": season, SEASON: season})
                back.update({"Tags Command": "MERGE" if tagged else "DELETE", "Tags": "" if tagged else season,
                             SEASON: p["season"]})
            row["Variant Cost"] = new_cost.get(pid)
            back["Variant Cost"] = v["cost"]
            rows.append(row)
            rb.append(back)

    blocks = [k for k, _ in itertools.groupby(x["ID"] for x in rows)]
    ok = all(x["ID"] and x["Variant ID"] and x["Command"] == "UPDATE" and x["Variant Command"] == "UPDATE"
             for x in rows + rb) \
        and len(blocks) == len(set(blocks)) \
        and all(r["ID"] for r in new_sizes) and all(r["Command"] == "UPDATE" and r["Variant Command"] == "MERGE" for r in new_sizes)
    out.append(dict(Check="ID-keyed sheets: ID + Variant ID on every row, Command and Variant Command UPDATE (fail on a "
                          "missing ID instead of creating), one block of rows per product, new sizes Variant Command MERGE",
                    Result="PASS" if ok else "FAIL",
                    Detail=f"PU-merge {len(rows)} rows on {len(blocks)} products, rollback {len(rb)} rows, "
                           f"new sizes {len(new_sizes)} rows"))

    out.append(_cost_overwrite_check(merge, mult, catalog, products, rows, new_sizes))

    nocost = [(p["title"], v["variant_id"]) for p, _ in products.values() for v in p["variants"] if v["cost"] is None]
    per = []
    for pid, (p, _) in products.items():
        if pid in new_cost:
            per.append((sum(v["available"] * (new_cost[pid] - v["cost"]) for v in p["variants"] if v["cost"] is not None),
                        sum(v["available"] for v in p["variants"]), p["title"]))
    out.append(dict(Check="Cost restatement on available stock (information: available units, Variant Inventory Qty, "
                          "x (OC cost - old cost))", Result="INFO",
                    Detail=f"{sum(u for _, u, _ in per):g} units on {len(per)} products restate by "
                           f"A${sum(x for x, _, _ in per):,.2f}"
                           + (f"; {len(nocost)} variants had no cost before, not counted" if nocost else "") + ": "
                           + "; ".join(f"{t} {u:g}u A${x:,.2f}" for x, u, t in sorted(per, key=lambda e: -abs(e[0])))))

    already = sum(1 for p, _ in products.values() if p["season"] == season)
    tagged = [p for p, _ in products.values() if season in [t.strip() for t in p["tags"].split(",")]]
    note = (f"{len(rows)} variant rows on {len(blocks)} products; sets custom.product_season = {season} "
            f"({len(blocks) - already} change) and adds the '{season}' tag (Tags Command MERGE keeps every existing tag) "
            f"on each product's first row, and Variant Cost = the OC cost on every variant. No Title, Handle, Body HTML, "
            f"price or variant metafield column.")
    frame = pd.DataFrame(rows, columns=MERGE_PRODUCT_COLS + VARIANT_COST_COLS)
    writers.append((f"{out_prefix}PU-merge.xlsx", lambda p: write_frame(p, frame, "Products", run_id(out_prefix)), note))

    rb_frame = pd.DataFrame(rb, columns=ROLLBACK_COLS + VARIANT_COST_COLS)
    unseasoned = [p["title"] for p, _ in products.values() if not p["season"]]
    if unseasoned:
        out.append(dict(Check="Re-bought products with no season in the catalog export", Result="INFO",
                        Detail=f"{len(unseasoned)}: {'; '.join(unseasoned)}. The rollback removes the season metafield, "
                               f"as before PU-merge: their rollback season cell is empty, and Matrixify deletes a "
                               f"metafield on an empty cell. PU-merge sets {season} on them."))
    out.append(_post_import_check(products, season, new_cost))
    out.append(dict(Check="Rollback for PU-merge", Result="INFO",
                    Detail=f"restores each product's previous custom.product_season from the catalog export "
                           f"({collections.Counter(p['season'] for p, _ in products.values() if p['season']).most_common()}"
                           + (f"; {len(unseasoned)} with none, cell empty: the metafield is removed" if unseasoned
                              else "")
                           + "), deletes the "
                           f"'{season}' tag on {len(blocks) - len(tagged)} products (already carried it before this run: "
                           + (", ".join(p["title"] for p in tagged) or "none")
                           + f") and puts back the prior Variant Cost on {len(rb)} variants"
                           + (f"; {len(nocost)} had no cost, so their rollback cell is blank (a blank Variant Cost "
                              f"cell clears the cost): " + ", ".join(f"{t} {vid}" for t, vid in nocost) if nocost else "")
                           + ". It does not remove the variants PU-merge-new-sizes adds."))
    writers.append((f"{out_prefix}PU-merge-ROLLBACK.xlsx",
                    lambda p: write_frame(p, rb_frame, "Products", run_id(out_prefix)),
                    f"{len(rb)} variant rows on {len(blocks)} products, Command UPDATE, Variant Command UPDATE"))

    if new_sizes:
        clash = [n["Variant SKU"] for n in new_sizes if n["Variant SKU"] in catalog.skus or not n["Variant SKU"]]
        out.append(dict(Check="New sizes on live products", Result="FAIL" if clash else "PASS",
                        Detail=f"{len(new_sizes)} variants on {len({n['ID'] for n in new_sizes})} products: "
                               + ", ".join(f"{n['Variant SKU']} {n['_title']} size {n['Option2 Value']} at OC cost "
                                           f"A${n['Variant Cost']}" for n in new_sizes)
                               + (f"; SKU problem: {clash}" if clash else "")))
        ns_frame = pd.DataFrame(new_sizes, columns=NEW_SIZE_COLS)
        writers.append((f"{out_prefix}PU-merge-new-sizes.xlsx",
                        lambda p: write_frame(p, ns_frame, "Products", run_id(out_prefix)),
                        f"{len(new_sizes)} variants, Command UPDATE, Variant Command MERGE"))
    return out, writers


def _post_import_check(products, season, new_cost):
    """Re-bought products whose catalog state already looks like PU-merge was imported.

    WARN, not FAIL: each sign also occurs before any import (a product already bought this
    season carries its season and tag; a carryover whose wholesale has not moved is already at
    the OC cost), and a FAIL there would block a correct run that the sheet cannot fix."""
    hits = []
    for pid, (p, _) in products.items():
        signs = []
        if p["season"] == season:
            signs.append(f"season already {season}")
        if season in [t.strip() for t in p["tags"].split(",")]:
            signs.append(f"'{season}' tag already on it")
        costs = [v["cost"] for v in p["variants"]]
        if pid in new_cost and costs and all(c is not None and abs(c - new_cost[pid]) <= 0.005 for c in costs):
            signs.append(f"every size already at the OC cost A${new_cost[pid]}")
        if signs:
            hits.append(f"{p['title']} ({', '.join(signs)})")
    check = "Catalog export taken before PU-merge was imported (else this run's rollback cannot undo PU-merge)"
    if not hits:
        return dict(Check=check, Result="PASS", Detail=f"{len(products)} re-bought products: none already carries "
                                                        f"{season}, its tag or the OC cost on every size")
    return dict(Check=check, Result="WARN",
                Detail=f"{len(hits)} of {len(products)} re-bought products already look imported: " + "; ".join(hits)
                       + ". This run's PU-merge-ROLLBACK puts back what this catalog holds, so on them it would not "
                         "undo a PU-merge that was already imported. Undo an imported PU-merge with the "
                         "PU-merge-ROLLBACK from the same zip as that PU-merge (the same run id), made before it was "
                         "imported, and do not use this one. If PU-merge has not been imported yet (a product "
                         "already bought this season, or a cost that has not changed), this is expected.")


def _cost_overwrite_check(merge, mult, catalog, products, rows, new_sizes):
    """Recomputed from the PO rows and the built sheets, not from the cost the builder chose."""
    want = collections.defaultdict(set)
    for r in merge:
        p, _, _ = catalog.match(norm_code(r[CODE]))
        eur = money(r.get("Wholesale Price"))
        if p is not None:
            want[p["id"]].add(None if eur is None else round(eur * mult, 2))
    owner = {v["variant_id"]: pid for pid, p in catalog.products.items() for v in p["variants"]}
    wrote = collections.defaultdict(collections.Counter)
    bad = []

    def off(pid, c):
        w = want.get(pid, set())
        return len(w) != 1 or None in w or c is None or abs(c - next(iter(w))) > 0.005

    for x in rows:
        if owner.get(x["Variant ID"]) != x["ID"]:
            bad.append(f"variant {x['Variant ID']} is not a variant of product {x['ID']}")
        wrote[x["ID"]][x["Variant ID"]] += 1
        if off(x["ID"], x["Variant Cost"]):
            bad.append(f"product {x['ID']} variant {x['Variant ID']} cost {x['Variant Cost']} vs OC {sorted(want.get(x['ID'], set()), key=str)}")
    for n in new_sizes:
        if off(n["ID"], n["Variant Cost"]):
            bad.append(f"new size {n['Variant SKU']} cost {n['Variant Cost']} vs OC {sorted(want.get(n['ID'], set()), key=str)}")
    for pid, (p, _) in products.items():
        live = {v["variant_id"] for v in p["variants"]}
        missing = live - set(wrote[pid])
        if missing:
            bad.append(f"{p['title']}: {len(missing)} live variants not in PU-merge")
        dup = [vid for vid, k in wrote[pid].items() if k > 1]
        if dup:
            bad.append(f"{p['title']}: variants written twice {dup}")
    stray = (set(wrote) | {n["ID"] for n in new_sizes}) - set(products)
    if stray:
        bad.append(f"products not re-bought are touched: {sorted(stray)}")
    n_var = sum(len(p["variants"]) for p, _ in products.values())
    return dict(Check="OC cost on every live variant of each re-bought product (PU-merge) and on each new size; "
                      "no other product touched", Result="FAIL" if bad else "PASS",
                Detail=f"{len(bad)}: " + "; ".join(bad[:20]) if bad else
                f"{n_var} variants on {len(products)} products + {len(new_sizes)} new sizes at the OC cost")


# ---- delivery ------------------------------------------------------------------
def _delivery(rows, out_prefix, currency="EUR"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet 1"
    append_text(ws, DELIVERY_HEADER)
    for c in ws[1]:
        c.font = Font(bold=True)
    col = {h: i + 1 for i, h in enumerate(DELIVERY_HEADER) if h not in ("Value",)}
    first_tranche = get_column_letter(DELIVERY_HEADER.index("1D") + 1)
    units = value = 0
    for i, r in enumerate(rows):
        n = i + 2
        code = norm_code(r[CODE])
        size = norm_size(r["Option2 Value"])
        nospace = code.replace(" ", "")
        qty = int(money(r.get("Order Qty")) or 0)
        tot = _r2(r.get("Total Order")) or 0
        units += qty
        value += tot
        append_text(ws, [
            _s(r["Title"]), _s(r["Vendor"]), _s(r["Type"]), _s(r["Option1 Value"]), size, _s(r["Variant SKU"]),
            _r2(r["Variant Price"]), _r2(r["Variant Compare At Price"]), _r2(r["Variant Cost"]),
            code, nospace, nospace + size, _s(r.get(SEASON)), _s(r.get(MADE_IN)), _s(r.get(NEW_SALE)),
            _s(r.get(COST_PRICE)), _r2(r["Wholesale Price"]), tot, qty,
        ])
        formulas = (
            f'=SUMIF(${first_tranche}$1:$AZ$1,"<>Value",{first_tranche}{n}:AZ{n})',
            f"={get_column_letter(col['Order Qty'])}{n}-{get_column_letter(col['Total Delivered'])}{n}",
        )
        for h, f in zip(("Total Delivered", "Remaining"), formulas):
            ws.cell(n, col[h]).value = f
        for h in ("Variant Price", "Variant Compare At Price", "Variant Cost"):
            ws.cell(n, col[h]).number_format = AUD_FMT
        for h in ("Wholesale Price", "Total Order"):
            ws.cell(n, col[h]).number_format = money_format(currency)
    ws.freeze_panes = "A2"
    wb.properties.title = run_id(out_prefix)
    note = (f"{len(rows)} rows, {units} units, {currency} {value:,.2f}; Total Delivered = SUMIF over {first_tranche}:AZ "
            "excluding 'Value' columns, so new tranche columns roll up without a formula edit")
    return [], [(f"{out_prefix}Delivery.xlsx", lambda p: wb.save(p), note)]
