"""Mutation harness for the OC tool's guards: python tests/mutate_oc.py

Each mutant breaks one guard; the suite must fail on every one. A mutant that
does not compile is reported BROKEN, never KILLED. Sources are restored in a
finally block and checked byte-identical at the end.

The unmutated suite must pass first, or every mutant would read KILLED. Sources
are restored from a snapshot taken at start, so run this on a committed copy
(git archive HEAD | tar -x -C <dir>), never in a tree someone else is editing.
"""
import hashlib
import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MUTANTS = [
    ("oc/finalize.py", 'MERGE_PRODUCT_COLS = ["ID", "Command", "Tags Command", "Tags", SEASON]',
     'MERGE_PRODUCT_COLS = ["ID", "Command", "Tags Command", "Tags", SEASON, "Title"]', "PU-merge carries Title"),
    ("oc/adapters/otb.py", '            if sum(l["sizes"].values()) != l["qty"]:', "            if False:", "gate 1 removed"),
    ("oc/adapters/otb.py", '            if abs(l["qty"] * l["price"] - l["line_value"]) > 0.02:', "            if False:", "gate 2 removed"),
    ("oc/adapters/otb.py", '        if q != d["total_qty"]:', "        if False:", "gate 3 (units) removed"),
    ("oc/adapters/otb.py", '        if abs(v - d["net"]) > 0.02:', "        if False:", "gate 3 (value) removed"),
    ("oc/resolve.py", "        if price < floor - 1e-9:", "        if False:", "margin floor raise removed"),
    ("oc/catalog.py", '        live = [p for p in cands if p["status"].upper() != "ARCHIVED"]',
     "        live = list(cands)", "ARCHIVED twins kept"),
    ("oc/finalize.py", """            f'=SUMIF(${first_tranche}$1:$AZ$1,"<>Value",{first_tranche}{n}:AZ{n})',""",
     "            f'={first_tranche}{n}',", "Total Delivered points at one tranche"),
    ("oc/finalize.py", "    return sorted(parts & toks)", "    return []", "raw-code title block removed"),
    ("oc/finalize.py", "    no_opt = sorted({t for t, o in zip(df[\"Title\"], df[\"Option1 Value\"]) if not o})",
     "    no_opt = []", "blank Option1 block removed"),
    ("oc/finalize.py", "    if failed:\n        removed = []", "    if False:\n        removed = []", "all-or-nothing removed"),
    ("oc/finalize.py", 'ID_COMMAND = "UPDATE"', 'ID_COMMAND = "MERGE"', "ID-keyed sheets back to Command MERGE"),
    ("validator_core.py", '" ".join(p[2:])', '" ".join(p[2:4])', "sub_category truncated again"),
    ("oc/resolve.py", '                src, detail = "similar", (', '                src, detail = "catalog", (',
     "other-style same-EUR price shaded green"),
    ("oc/resolve.py", '            src = ("history" if n >= 2 else "thin") if price else None',
     '            src = "history" if price else None', "one-row history price shaded amber"),
    ("oc/resolve.py", '                src, detail = "fit", f"EUR', '                src, detail = "history", f"EUR',
     "ratio-fit price shaded amber"),
    ("oc/finalize.py", '        n["Variant Cost"] = new_cost.get(n["ID"])',
     '        n["Variant Cost"] = next(round(x["cost"], 2) for x in catalog.products[n["ID"]]["variants"] if x["cost"] is not None)',
     "new sizes take the live cost, not the OC's (rule since f2ce816)"),
    ("oc/complete.py", "        if old == value:\n            return\n", "", "no-op fills logged (harmless: expect SURVIVED)"),
    # ---- cart ----
    ("oc/draft.py", 'Result="PASS" if hit else "FAIL"', 'Result="PASS" if hit else "INFO"', "wrong cart is not a FAIL"),
    ("oc/draft.py", '        code, eur = l["code"], l["price"]',
     '        code, eur = l["code"], (cart or {}).get("rows", {}).get(l["code"], {}).get("wholesale") or l["price"]',
     "cart wholesale used over the OC's"),
    ("oc/draft.py", "    if diff:\n", "    if False:\n", "cart-vs-OC wholesale INFO removed"),
    ("oc/resolve.py", "        return self._cart_rows.get(norm_code(code))",
     "        return self._cart_rows.get(norm_code(code)) or next("
     "(r for r in self._cart_rows.values() if r[\"colour_code\"] == split_code(code)[2]), None)",
     "cart colour falls back to the bare colour code"),
    ("oc/cart.py", "        code = join_key(model, fabric, colour_code)", '        code = join_key(model, "", colour_code)',
     "cart keyed without the fabric"),
    ("oc/resolve.py", '"cart": AMBER', '"cart": GREEN', "cart-sourced cells shaded green"),
    ("oc/resolve.py", "            if odd:", "            if False:", "garbled cart colour accepted"),
    ("oc/draft.py", '    prefixes = brand.get("title_prefixes") or {"WOMEN": brand["womens_prefix"]}',
     '    prefixes = {"WOMEN": brand["womens_prefix"]}', "brand title_prefixes ignored"),
    ("oc/resolve.py", '        if row and row["subcategory"]:', "        if False:", "cart subcategory Type removed"),
    ("oc/resolve.py", '        if sib and " ".join(html.unescape(sib[0]).split()).casefold() == " ".join(value.split()).casefold():',
     "        if sib:", "cart value green although the live style disagrees"),
    ("oc/resolve.py", "        if row is not None and fam and v == fam:", "        if False:",
     "cart family agreeing with the catalog's colour code shaded amber"),
    # ---- cart text is data ----
    ("oc/resolve.py", '        if "<" in value or ">" in value:', "        if False:", "cart markup used in Body HTML"),
    ("oc/resolve.py", "        comp = composition if composition_is_html else html.escape(str(composition), quote=False)",
     "        comp = composition", "cart composition not escaped"),
    ("oc/resolve.py", "made_in=html.escape(str(made_in), quote=False))", "made_in=made_in)", "made in not escaped"),
    ("oc/draft.py", 'R.body_html(code, comp, made, comp_src == "catalog")', "R.body_html(code, comp, made, False)",
     "live Body HTML composition escaped twice"),
    ("oc/poxlsx.py", '        cell.data_type = "s"', "        pass", "formula-like text saved as a formula"),
    ("oc/poxlsx.py", '    return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v', "    return v",
     "control characters not stripped"),
    ("oc/poxlsx.py", "resolve_formula(v, raw, r) if f else v", "resolve_formula(v, raw, r)", "read_po reads text as a formula"),
    ("oc/complete.py", "            as_text(c)", "            pass", "complete writes cart text as a formula"),
    ("oc/poxlsx.py", '        return v == "" if isinstance(v, str) else bool(pd.isna(v))',
     '        return False if isinstance(v, str) else bool(pd.isna(v))', "write_frame writes '' as an empty text cell"),
    # ---- catalog season and available stock (case 2026-11-09-buyer-8 F6) ----
    ("oc/catalog.py", '    SEASON: "Metafields",\n', "", "season column not required"),
    ("oc/catalog.py", '"Variant Compare At Price", AVAILABLE], "Inventory / Variants")',
     '"Variant Compare At Price"], "Inventory / Variants")', "Variant Inventory Qty not required"),
    ("oc/catalog.py", "available=money(r[AVAILABLE]) or 0", "available=0", "restatement ignores available stock"),
    ("oc/catalog.py", """                + f'. Make the export again in Matrixify with the preset "{EXPORT_PRESET}". If that preset is '
                  "not there, ask Colwyn.")""",
     """                + ". Export the brand's products again from Matrixify with these columns ticked.")""",
     "missing-column message asks for a hand-built export again (N3)"),
    # ---- no season in the export: an empty rollback cell deletes the metafield (case 2026-11-09-buyer-9 N2) ----
    ("oc/finalize.py", 'SEASON: p["season"]})', 'SEASON: p["season"] or "[IGNORE]"})',
     "no-season rollback cell [IGNORE] again (keeps the new season)"),
    ("oc/finalize.py", 'SEASON: p["season"]})', 'SEASON: p["season"] or season})',
     "no-season rollback cell writes this season"),
    ("oc/finalize.py", "    if unseasoned:\n        out.append(", "    if False:\n        out.append(", "no-season INFO removed"),
    ("oc/finalize.py", 'Check="Re-bought products with no season in the catalog export", Result="INFO"',
     'Check="Re-bought products with no season in the catalog export", Result="WARN"', "no-season note back to a WARN"),
    # ---- rollback built from a catalog exported after the import (F8) ----
    ("oc/finalize.py", "        if signs:\n            hits.append", "        if False:\n            hits.append",
     "post-import WARN removed"),
    ("oc/finalize.py", '        if p["season"] == season:\n            signs.append', "        if False:\n            signs.append",
     "post-import season sign removed"),
    ("oc/finalize.py", '        if season in [t.strip() for t in p["tags"].split(",")]:\n            signs.append',
     "        if False:\n            signs.append", "post-import tag sign removed"),
    ("oc/finalize.py", "        if pid in new_cost and costs and all(", "        if False and all(", "post-import cost sign removed"),
    ("oc/finalize.py", "all(c is not None and abs(c - new_cost[pid]) <= 0.005 for c in costs)",
     "any(c is not None and abs(c - new_cost[pid]) <= 0.005 for c in costs)", "post-import cost sign on any size, not every"),
    ("oc/finalize.py", '    return dict(Check=check, Result="WARN",', '    return dict(Check=check, Result="INFO",',
     "post-import signs reported as INFO"),
    # ---- one run id ties a PU-merge to its own rollback (case 2026-11-09-buyer-9 N1) ----
    # ---- F4: say where unpublished new stock still lands (case 2026-11-09-buyer-9) ----
    ("oc/draft.py", '"created Active and off the Online Store; it can still show in POS and other sales channels"',
     '"new stock is created unpublished"', "Review says new stock is created unpublished"),
    ("oc/finalize.py", '"to global, so after import they may be on Point of Sale, Shop, Microsoft Copilot and "',
     '"to global, so check POS visibility after import"', "Published Scope WARN names only POS"),
    ("pages/1_Order_confirmation_to_PO.py",
     '                "Use the PU-merge-ROLLBACK from the same zip as the PU-merge you import, made before importing. "\n',
     "", "page drops the same-zip rollback line"),
    ("oc/finalize.py", '"undo a PU-merge that was already imported. Undo an imported PU-merge with the "',
     '"undo a PU-merge that was already imported. "', "post-import WARN drops the same-zip rollback rule"),
    ("oc/service.py", '{when:%Y-%m-%d-%H%M}', '{when:%Y-%m-%d}', "file prefix without the time"),
    ("oc/service.py", 'datetime.datetime.now(ZoneInfo("Australia/Melbourne"))', "datetime.datetime.now()",
     "run time not Melbourne's"),
    ("oc/finalize.py", "    checks, writers = [run_check(out_prefix)], []", "    checks, writers = [], []",
     "run id row left out of the checks"),
    ("oc/finalize.py", 'lambda p: write_frame(p, rb_frame, "Products", run_id(out_prefix)),',
     'lambda p: write_frame(p, rb_frame, "Products"),', "ROLLBACK not stamped with the run id"),
    ("oc/finalize.py", 'lambda p: write_frame(p, frame, "Products", run_id(out_prefix)), note))',
     'lambda p: write_frame(p, frame, "Products"), note))', "PU-merge not stamped with the run id"),
    ("oc/poxlsx.py", "        wb.properties.title = title\n", "        pass\n", "write_frame drops the Title stamp"),
]


def digest(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


def _suite():
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
                          cwd=ROOT, capture_output=True, text=True)


def main():
    base = _suite()
    if base.returncode != 0:
        sys.exit("the unmutated suite does not pass, so no mutant can be judged:\n" + base.stdout[-2000:])
    files = {f for f, *_ in MUTANTS}
    before = {f: (ROOT / f).read_bytes() for f in files}
    sums = {f: digest(ROOT / f) for f in files}
    results = []
    try:
        for f, old, new, label in MUTANTS:
            p = ROOT / f
            src = before[f].decode()
            assert src.count(old) == 1, f"{label}: anchor not unique in {f}"
            p.write_text(src.replace(old, new))
            try:
                py_compile.compile(str(p), doraise=True)
            except py_compile.PyCompileError:
                results.append((label, "BROKEN"))
                p.write_bytes(before[f])
                continue
            r = _suite()
            results.append((label, "KILLED" if r.returncode == 1 else "SURVIVED" if r.returncode == 0 else f"ERROR {r.returncode}"))
            p.write_bytes(before[f])
    finally:
        for f, b in before.items():
            (ROOT / f).write_bytes(b)
    for label, res in results:
        print(f"{res:9} {label}")
    assert all(digest(ROOT / f) == sums[f] for f in files), "sources not restored"
    print("sources restored byte-identical")


if __name__ == "__main__":
    main()
