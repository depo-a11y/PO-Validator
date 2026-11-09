"""`oc finalize`: a reviewed PO -> PU-new, PU-merge (+ new sizes) and the delivery file.

PU-new   NEW rows through validator_core.run_gate, the same gate as app.py.
PU-merge carryover, under the co_stock rule (Command MERGE on every row, IDs
         present) but NOT co_stock's full column battery: a blank cell clears that
         field on import, and the PO leaves Body HTML, title_tag, HS code, GTIN and
         the variant metafields blank on MERGE rows. So the sheet carries only what
         this buy changes on a live product (the season metafield and season tag)
         and never Title, Handle, price or cost. New sizes on live products go in a
         separate file, because they need variant columns the other rows must not carry.
Delivery the brand delivery workbook shape, with a Total Delivered roll-up that
         absorbs future tranche columns and Remaining = Order Qty - Total Delivered.
"""
import collections
import os
import sys
from pathlib import Path

import openpyxl
import pandas as pd
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .poxlsx import read_po, CODE, MADE_IN, SEASON, NEW_SALE, COST_PRICE, TITLE_TAG, AUD_FMT, EUR_FMT, write_table
from .util import blank, norm_code, norm_size, money, slug

REPO = Path(__file__).resolve().parent.parent

DELIVERY_HEADER = [
    "Title", "Vendor", "Type", "Option1 Value", "Option2 Value", "Variant SKU", "Variant Price",
    "Variant Compare At Price", "Variant Cost", "Metafield: my_fields.manufacture_code", "remove space",
    "FULLCODE", "Metafield: custom.product_season [single_line_text_field]",
    "Metafield: custom.made_in [single_line_text_field]", "Metafield: custom.new_sale [single_line_text_field]",
    "Variant Metafield: Variant.cost_price [single_line_text_field]", "Wholesale Price", "Total Order",
    "Order Qty", "Total Delivered", "Remaining", "1D", "Value", "2D", "Value",
]

MERGE_PRODUCT_COLS = ["ID", "Command", "Tags Command", "Tags", SEASON]
NEW_SIZE_COLS = ["ID", "Command", "Variant Command", "Option1 Name", "Option1 Value", "Option2 Name",
                 "Option2 Value", "Variant SKU", "Variant Barcode", "Variant Price", "Variant Compare At Price",
                 "Variant Cost", "Variant Inventory Tracker", "Variant HS Code", "Variant Country of Origin",
                 "Variant Metafield:custom.manufacture_code[single_line_text_field]",
                 "Variant Metafield: custom.season [single_line_text_field]",
                 "Variant Metafield: custom.new_sale [single_line_text_field]", COST_PRICE]


def _s(v):
    return "" if blank(v) else str(v).strip()


def _cmd(r):
    return _s(r.get("Command")).upper()


def run(po_path, brand, catalog, out_prefix):
    sys.path.insert(0, str(REPO))
    import validator_core as core

    _, rows = read_po(po_path)
    checks = []
    new = [r for r in rows if _cmd(r) == "NEW"]
    merge = [r for r in rows if _cmd(r) == "MERGE"]
    other = [r for r in rows if _cmd(r) not in ("NEW", "MERGE")]
    if other:
        checks.append(dict(Check="Every PO row is NEW or MERGE", Result="FAIL",
                           Detail=f"rows {[r['_row'] for r in other]}"))

    checks += _pu_new(new, brand, catalog, core, out_prefix)
    checks += _pu_merge(merge, brand, catalog, core, out_prefix)
    checks += _delivery(rows, out_prefix)
    write_checks = f"{out_prefix}finalize-checks.xlsx"
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    write_table(wb, "Checks", checks, colour_key="Result")
    wb.save(write_checks)
    return checks


# ---- PU-new --------------------------------------------------------------------
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
            "Variant SKU": _s(r["Variant SKU"]), "Variant Price": money(r["Variant Price"]),
            "Variant Compare At Price": money(r["Variant Compare At Price"]),
            "Variant Cost": money(r["Variant Cost"]),
            "Metafield: my_fields.manufacture_code": norm_code(r[CODE]),
            SEASON: _s(r.get(SEASON)), MADE_IN: "" if made == brand["unknown"] else made,
            NEW_SALE: _s(r.get(NEW_SALE)), COST_PRICE: _s(r.get(COST_PRICE)),
            "Variant Metafield: Variant.gtin [single_line_text_field]": _s(r.get("Variant Metafield: Variant.gtin [single_line_text_field]")),
            "HS Code": _s(r.get("Variant Metafield: HS Code")),
            "Metafield: title_tag [string]": _s(r.get(TITLE_TAG)),
        })
    df = pd.DataFrame(recs)
    df_in = df.copy()
    report = f"{out_prefix}PU-new-VALIDATION_ERRORS_REPORT.xlsx"
    if os.path.exists(report):
        os.remove(report)
    try:
        df, errors, typos = core.run_gate(df, template_file=str(REPO / "expected_tags.xlsx"), report_path=report)
    except core.ValidationStop as e:
        out.append(dict(Check="PU-new passes the po-validator gate (validator_core.run_gate)", Result="FAIL",
                        Detail=f"{e} {e.rows[:5]}"))
        return out
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
    by_title = df.groupby("Title")["Metafield: my_fields.manufacture_code"].nunique()
    multi = list(by_title[by_title > 1].index)
    out.append(dict(Check="One manufacture code per Title (Matrixify groups create rows by Title)",
                    Result="FAIL" if multi else "PASS", Detail=", ".join(multi) or f"{len(by_title)} products"))
    taken = sorted(t for t in by_title.index if slug(t) in catalog.handles)
    out.append(dict(Check="No PU-new title's handle is already live", Result="FAIL" if taken else "PASS",
                    Detail=", ".join(taken) or "none"))
    inv = df[core.INVENTORY_COLS].astype(float).abs().sum().sum()
    out.append(dict(Check="Every inventory column is zero", Result="PASS" if inv == 0 else "FAIL", Detail=f"sum {inv:g}"))
    trunc = sorted({f"{t} -> '{s}'" for t, s in zip(df["Type"], df["Metafield: custom.sub_category [single_line_text_field]"])
                    if len(t.split()) > 4})
    if trunc:
        out.append(dict(Check="sub_category from split_t (known bug, not fixed: it does not stop the gate)",
                        Result="WARN", Detail=f"{len(trunc)} Types truncated: " + "; ".join(trunc)))
    scales = df.groupby("Title")["Metafield: custom.size_scale [single_line_text_field]"].unique()
    mixed = {t: list(v) for t, v in scales.items() if len(v) > 1}
    if mixed:
        out.append(dict(Check="One size_scale per product (product-level field: Matrixify keeps the first row's)",
                        Result="WARN", Detail="; ".join(f"{t}: {v}" for t, v in mixed.items())))
    long_tags = sorted({t for t in df["Metafield: title_tag [string]"] if len(t) > 60})
    out.append(dict(Check="title_tag <= 60 chars (validator check reads the wrong column, so measured here)",
                    Result="WARN" if long_tags else "PASS", Detail=f"{len(long_tags)} over 60"))
    blanked = int(((df_in[MADE_IN] == "") & (pd.Series([_s(r.get(MADE_IN)) for r in new]) == brand["unknown"])).sum())
    if blanked:
        out.append(dict(Check="made_in '-' placeholder not written to the store", Result="INFO",
                        Detail=f"{blanked} rows carry '{brand['unknown']}' on the PO; PU-new leaves custom.made_in empty instead"))
    path = f"{out_prefix}PU-new.xlsx"
    df[core.COLUMNS_IN_ORDER].to_excel(path, index=False, sheet_name="Products")
    out.append(dict(Check="PU-new written", Result="INFO", Detail=f"{path}: {len(df)} rows, "
                    f"{df['Title'].nunique()} products, columns = app.py columns_in_order ({len(core.COLUMNS_IN_ORDER)})"))
    return out


# ---- PU-merge ------------------------------------------------------------------
def _pu_merge(merge, brand, catalog, core, out_prefix):
    out = []
    mdf = pd.DataFrame([{"Command": r["Command"]} for r in merge])
    bad = core.invalid_merge_commands(mdf) if len(mdf) else []
    out.append(dict(Check="co_stock gate: Command = MERGE on every carryover row", Result="FAIL" if bad else "PASS",
                    Detail=f"{len(merge)} rows" + (f"; invalid: {bad}" if bad else "")))
    products, new_sizes, problems = collections.OrderedDict(), [], []
    restate = collections.Counter()
    for r in merge:
        code = norm_code(r[CODE])
        p, note, _ = catalog.match(code)
        if p is None:
            problems.append(f"row {r['_row']} {code}: {note or 'no live product'}")
            continue
        products.setdefault(p["id"], (p, r))
        v = next((v for v in p["variants"] if v["size"] == norm_size(r["Option2 Value"])), None)
        if v is None:
            sib = p["variants"]
            price = collections.Counter(x["price"] for x in sib).most_common(1)[0][0]
            cmp_ = collections.Counter(x["compare_at"] for x in sib).most_common(1)[0][0]
            hs = collections.Counter(x["hs"] for x in sib if x["hs"]).most_common(1)
            cty = collections.Counter(x["country"] for x in sib if x["country"]).most_common(1)
            names = p["option_names"]
            colour = collections.Counter(x["colour"] for x in sib if x["colour"]).most_common(1)[0][0]
            size = norm_size(r["Option2 Value"])
            vals = {n.lower(): (colour if n.lower() in ("colour", "color") else size) for n in names}
            new_sizes.append({
                "ID": p["id"], "Command": "MERGE", "Variant Command": "MERGE",
                "Option1 Name": names[0], "Option1 Value": vals[names[0].lower()],
                "Option2 Name": names[1], "Option2 Value": vals[names[1].lower()],
                "Variant SKU": _s(r["Variant SKU"]), "Variant Barcode": _s(r["Variant SKU"]),
                "Variant Price": price, "Variant Compare At Price": cmp_,
                "Variant Cost": money(r["Variant Cost"]), "Variant Inventory Tracker": "shopify",
                "Variant HS Code": hs[0][0] if hs else "", "Variant Country of Origin": cty[0][0] if cty else "",
                "Variant Metafield:custom.manufacture_code[single_line_text_field]": p["raw_code"].strip(),
                "Variant Metafield: custom.season [single_line_text_field]": _s(r.get(SEASON)),
                "Variant Metafield: custom.new_sale [single_line_text_field]": p["new_sale"] or _s(r.get(NEW_SALE)),
                COST_PRICE: _s(r.get(COST_PRICE)),
            })
        else:
            delta = (money(r["Variant Cost"]) or 0) - (v["cost"] or 0)
            restate["variants"] += 1
            restate["changed"] += abs(delta) > 0.005
            restate["units"] += v["on_hand"]
            restate["value"] += delta * v["on_hand"]
    out.append(dict(Check="Every MERGE row resolves to one live (non-archived) product", Result="FAIL" if problems else "PASS",
                    Detail="; ".join(problems) or f"{len(products)} products"))
    archived = [p["id"] for p, _ in products.values() if p["status"].upper() == "ARCHIVED"]
    out.append(dict(Check="No MERGE target is ARCHIVED", Result="FAIL" if archived else "PASS", Detail=str(archived or "none")))
    season = brand["season"]
    rows = [{"ID": p["id"], "Command": "MERGE", "Tags Command": "MERGE", "Tags": season, SEASON: season}
            for p, _ in products.values()]
    path = f"{out_prefix}PU-merge.xlsx"
    pd.DataFrame(rows, columns=MERGE_PRODUCT_COLS).to_excel(path, index=False, sheet_name="Products")
    already = sum(1 for p, _ in products.values() if p["season"] == season)
    out.append(dict(Check="PU-merge written", Result="INFO",
                    Detail=f"{path}: {len(rows)} products; sets custom.product_season = {season} "
                           f"({len(rows) - already} change) and adds the '{season}' tag (Tags Command MERGE keeps every "
                           "existing tag). No Title, Handle, Body HTML, price, cost or variant column."))
    ids_ok = all(r["ID"] for r in rows) and all(r["Command"] == "MERGE" for r in rows)
    out.append(dict(Check="PU-merge: ID present and Command MERGE on every row", Result="PASS" if ids_ok else "FAIL",
                    Detail=f"{len(rows)} rows"))
    if new_sizes:
        clash = [n["Variant SKU"] for n in new_sizes if n["Variant SKU"] in catalog.skus or not n["Variant SKU"]]
        path2 = f"{out_prefix}PU-merge-new-sizes.xlsx"
        pd.DataFrame(new_sizes, columns=NEW_SIZE_COLS).to_excel(path2, index=False, sheet_name="Products")
        out.append(dict(Check="New sizes on live products", Result="FAIL" if clash else "PASS",
                        Detail=f"{path2}: {len(new_sizes)} variants on {len({n['ID'] for n in new_sizes})} products "
                               + ", ".join(f"{n['Variant SKU']} size {n['Option2 Value']}" for n in new_sizes)
                               + (f"; SKU problem: {clash}" if clash else "")))
    out.append(dict(Check="Carryover unit cost (decision, not written)", Result="WARN",
                    Detail=f"the PO's Variant Cost differs from live on {restate['changed']} of {restate['variants']} existing "
                           f"variants; writing it would restate {restate['units']:g} units on hand by A${restate['value']:,.2f}. "
                           "PU-merge leaves live cost alone."))
    return out


# ---- delivery ------------------------------------------------------------------
def _delivery(rows, out_prefix):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet 1"
    ws.append(DELIVERY_HEADER)
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
        tot = money(r.get("Total Order")) or 0
        units += qty
        value += tot
        ws.append([
            _s(r["Title"]), _s(r["Vendor"]), _s(r["Type"]), _s(r["Option1 Value"]), size, _s(r["Variant SKU"]),
            money(r["Variant Price"]), money(r["Variant Compare At Price"]), money(r["Variant Cost"]),
            code, nospace, nospace + size, _s(r.get(SEASON)), _s(r.get(MADE_IN)), _s(r.get(NEW_SALE)),
            _s(r.get(COST_PRICE)), money(r["Wholesale Price"]), tot, qty,
            f'=SUMIF(${first_tranche}$1:$AZ$1,"<>Value",{first_tranche}{n}:AZ{n})',
            f"={get_column_letter(col['Order Qty'])}{n}-{get_column_letter(col['Total Delivered'])}{n}",
        ])
        for h in ("Variant Price", "Variant Compare At Price", "Variant Cost"):
            ws.cell(n, col[h]).number_format = AUD_FMT
        for h in ("Wholesale Price", "Total Order"):
            ws.cell(n, col[h]).number_format = EUR_FMT
    ws.freeze_panes = "A2"
    path = f"{out_prefix}Delivery.xlsx"
    wb.save(path)
    return [dict(Check="Delivery file written", Result="INFO",
                 Detail=f"{path}: {len(rows)} rows, {units} units, EUR {value:,.2f}; Total Delivered = SUMIF over "
                        f"{first_tranche}:AZ excluding 'Value' columns, so new tranche columns roll up without a formula edit")]
