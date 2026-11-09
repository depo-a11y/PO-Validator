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
    ("app.py", "columns_in_order = core.COLUMNS_IN_ORDER\n",
     "columns_in_order = core.COLUMNS_IN_ORDER\n"
     "split_t = lambda val: core.split_t(\" \".join(str(val).split()[:4]))\n",
     "app.split_t a truncating copy, not the gate's (rebase onto b2a7c9e)"),
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
    # ---- AI reader (oc/adapters/ai.py) ----
    ("oc/adapters/ai.py", "        if missing:\n            fails.append(_check(GROUNDING_CHECK",
     "        if False:\n            fails.append(_check(GROUNDING_CHECK", "AI: grounding misses ignored"),
    ("oc/adapters/ai.py", "    else:\n        warns.append(_check(GROUNDING_CHECK, \"WARN\",",
     "    elif False:\n        warns.append(_check(GROUNDING_CHECK, \"WARN\",", "AI: no text layer passes silently"),
    ("oc/adapters/ai.py", "        self.hays = (_flat(text), _flat(undouble(text)))", "        self.hays = (_flat(text),)",
     "AI: doubled bold glyphs not collapsed"),
    ("oc/adapters/ai.py", "        return w[::2] if len(w) >= 2 and len(w) % 2 == 0 and all(",
     "        return w[::2] if len(w) >= 2 or all(", "AI: every word halved, not only doubled ones"),
    ("oc/adapters/ai.py", "        n = _flat(_bare(needle) if amount else needle)", "        n = _flat(needle)",
     "AI: amounts grounded with their currency mark"),
    ("oc/adapters/ai.py", "    if not suppliers_match(supplier, names):", "    if False:", "AI: supplier check skipped"),
    ("oc/adapters/ai.py", '        return re.sub(r"[\\W_]+", "", str(s or "")).casefold()',
     '        return str(s or "").casefold()', "AI: supplier match minds punctuation"),
    ("oc/adapters/ai.py", "    if not bad:\n        fails += [_check(GATE_CHECK",
     "    if False:\n        fails += [_check(GATE_CHECK", "AI: returns before gate_problems"),
    ("oc/adapters/ai.py", "    elif no_sizes:", "    elif False:", "AI: a line with empty sizes accepted"),
    ("oc/adapters/ai.py", '    if not data["has_size_breakdown"]:', "    if False:", "AI: has_size_breakdown ignored"),
    ("oc/adapters/ai.py", "    if off:\n        fails.append(_check(CURRENCY_CHECK",
     "    if False:\n        fails.append(_check(CURRENCY_CHECK", "AI: currency check skipped"),
    ("oc/adapters/ai.py", '    if kind != "order_confirmation":', "    if False:", "AI: document kind WARN removed"),
    ("oc/adapters/ai.py", '                if str(s["qty_text"]).strip() in DASH:', "                if False:",
     "AI: a '-' size cell refused instead of left out"),
    ("oc/adapters/ai.py", '                elif s["size"] in sizes:', "                elif False:", "AI: a size listed twice accepted"),
    ("oc/adapters/ai.py", '(\\d{1,3}(?:([.,\'’])\\d{3}(?:\\2\\d{3})*)?|\\d+)([.,])(\\d{2})")',
     '(\\d{1,3}(?:([.,\'’])\\d{3}(?:\\2\\d{3})*)?|\\d+)([.,])(\\d{1,2})")', "AI: money takes a 1-digit decimal part"),
    ("oc/adapters/ai.py", "    if not m or (m.group(2) and m.group(2) == m.group(3)):", "    if not m:",
     "AI: money takes the same mark as thousands and decimal"),
    ("oc/adapters/ai.py", '    if msg.stop_reason == "max_tokens":', "    if False:", "AI: max_tokens read not refused as such"),
    ("oc/adapters/ai.py", '    if msg.stop_reason == "refusal":', "    if False:", "AI: refusal not refused as such"),
    ("oc/adapters/ai.py", '    if msg.stop_reason != "end_turn":', "    if False:", "AI: any other stop reason accepted"),
    ("oc/adapters/ai.py", '    return text.replace(key, "[hidden]") if key else text', "    return text", "AI: key not scrubbed"),
    ("oc/adapters/ai.py",
     '            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}},\n',
     "", "AI: request carries no PDF"),
    ("oc/adapters/ai.py", 'output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},',
     'output_config={"format": {"type": "json_schema", "schema": SCHEMA}},', "AI: effort left to the default"),
    ("oc/adapters/ai.py", 'output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},',
     'output_config={"effort": EFFORT},', "AI: no structured output schema"),
    ("oc/adapters/ai.py", "    with client.beta.messages.stream(**request(pdf_bytes)) as stream:\n        return stream.get_final_message()",
     "    return client.beta.messages.create(**request(pdf_bytes))", "AI: request not streamed"),
    ("oc/adapters/ai.py", '        betas=[FALLBACK_BETA], fallbacks="default",\n', "", "AI: refusal fallback dropped"),
    ("oc/adapters/ai.py", '    return _check(READER_CHECK, "WARN", head + (', '    return _check(READER_CHECK, "INFO", head + (',
     "AI: reader row an INFO, not a WARN"),
    ("oc/adapters/ai.py", 'MODEL = "claude-opus-5-5"', 'MODEL = "claude-sonnet-5-5"', "AI: another model"),
    # ---- AI reads in the draft, complete and the service ----
    ("oc/resolve.py", 'COLOUR = {"OC": GREEN, AI_OC: AMBER,', 'COLOUR = {"OC": GREEN, AI_OC: GREEN,', "AI-read cells green"),
    ("oc/draft.py", '        if source == "OC" and l is not None and l.get("reader") == "ai":', "        if False:",
     "AI-read cells keep source OC (green)"),
    ("oc/complete.py", 'if l.get("reader") == "ai" else ("OC", "")', 'if False else ("OC", "")',
     "complete marks an AI-read price as OC"),
    ("oc/service.py", '    if (reader or brand_cfg["adapter"]) == "ai":', '    if brand_cfg["adapter"] == "ai":',
     "reader='ai' ignored for an otb brand"),
    ("oc/service.py", 'f"{p}. {AI_HINT}" if p == "no order confirmations found" else p', "p",
     "no-confirmation FAIL without the AI hint"),
    ("oc/service.py", "    if colours:\n        return None, empty, pre + [_check(\"Every OC colour",
     "    if False:\n        return None, empty, pre + [_check(\"Every OC colour", "unmapped colour FAIL removed"),
    # ---- the brand's join key (oc/brand.py) ----
    ("oc/brand.py", '    tpl = (brand or {}).get("join_key") or DEFAULT_JOIN_KEY\n    vals = dict(',
     "    tpl = DEFAULT_JOIN_KEY\n    vals = dict(", "join_key template ignored"),
    ("oc/brand.py", "            if norm_colour(c) not in codes and c not in out:", "            if False:",
     "unmapped OC colours not listed"),
    ("oc/brand.py", '    cfg["colour_codes"] = {norm_colour(k): str(v).strip() for k, v in codes.items()}',
     '    cfg["colour_codes"] = {str(k): str(v).strip() for k, v in codes.items()}', "colour_codes keys not case-folded"),
    ("oc/brand.py", '    if "{" in rest or "}" in rest or not fields:', "    if not fields:",
     "join_key braces beyond the four tokens accepted at load"),
    ("oc/brand.py", '    fields, rest = join_fields(tpl), JOIN_TOKEN.sub("", tpl)\n',
     '    import string\n    fields = [f for _, f, _, _ in string.Formatter().parse(tpl) if f is not None]\n'
     '    rest = "" if set(fields) <= set(JOIN_FIELDS) else "{"\n',
     "join_key checked by str.format's parser again (accepts specs and conversions)"),
    ("oc/brand.py", "    return norm_code(JOIN_TOKEN.sub(lambda m: str(vals[m.group(1)]), tpl))",
     "    return norm_code(tpl.format(**vals))", "join_key filled by str.format again"),
    ("oc/brand.py", "        if need:\n            raise BrandError", "        if False:\n            raise BrandError",
     "adapter: ai without oc_supplier/join_key accepted"),
    ("oc/brand.py", '    if cfg["adapter"] not in ADAPTERS:', "    if False:", "unknown adapter accepted at load"),
    ("oc/brand.py", "DEFAULT_JOIN_KEY = \"{style}{fabric} {colour}\"", "DEFAULT_JOIN_KEY = \"{style} {fabric} {colour}\"",
     "default join_key differs from today's rule"),
    # ---- the page ----
    ("pages/1_Order_confirmation_to_PO.py", "    use_ai = st.checkbox(AI_BOX, value=False,",
     "    use_ai = st.checkbox(AI_BOX, value=True,", "page: AI box ticked by default"),
    ("pages/1_Order_confirmation_to_PO.py",
     "        (st.warning if rd[\"Result\"] == \"WARN\" else st.caption)(f\"Order confirmation {rd['Detail']}\")",
     "        pass", "page: no line saying which reader was used"),
    ("pages/1_Order_confirmation_to_PO.py", "- `oc_supplier`: the company name printed on the brand's OCs\n", "",
     "page: brand-not-listed help drops oc_supplier"),
    # ---- fixed-layout readers: AD Milano, adidas AG, Fear of God ----
    ("oc/adapters/admilano.py", '        cells = [(w["text"], xc(w)) for w in size_row]',
     '        cells = d.setdefault("_cells", [(w["text"], xc(w)) for w in size_row])',
     "admilano: one size map for the document, not per line"),
    ("oc/adapters/admilano.py", "TOL = 6.0", "TOL = 60.0", "admilano: quantity-to-column tolerance widened"),
    ("oc/adapters/admilano.py", '    amount = eur(head[-1]["text"])',
     '    amount = float(head[-1]["text"].replace(".", "").replace(",", ""))',
     "admilano: decimal comma read as a thousands mark"),
    ("oc/adapters/admilano.py", '            if d is None or d.get("_done") or "GENERAL TERMS AND CONDITIONS" in t:',
     '            if d is None or d.get("_done") or not m or "GENERAL TERMS AND CONDITIONS" in t:',
     "admilano: continuation page skipped"),
    ("oc/adapters/admilano.py", "            if s not in hit:", "            if False:",
     "admilano: size cell with no quantity accepted"),
    ("oc/adapters/admilano.py", '    if d["discount"]:', "    if False:", "admilano: document discount not named"),
    ("oc/adapters/admilano.py", '            d["total_qty"] = (d["total_qty"] or 0) + int(um.group(2).replace(".", ""))',
     '            d["total_qty"] = int(um.group(2).replace(".", ""))', "admilano: total qty from one UM only"),
    ("oc/adapters/admilano.py", '    problems += _header_problems(d["doc"], head)', "    pass",
     "admilano: moved table header accepted"),
    ("oc/adapters/adidas.py", "X_TOL = 1", "X_TOL = 3", "adidas: default x_tolerance runs words together"),
    ("oc/adapters/adidas.py", "        if stray:", "        if False:", "adidas: size cell under the line accepted"),
    ("oc/adapters/adidas.py", "    if disc is not None and num(disc) != 0:", "    if False:", "adidas: line discount not named"),
    ("oc/adapters/adidas.py", '            if "adidas AG" not in t or "Order Confirmation" not in t:',
     '            if "adidas AG" not in t or "Order Confirmation" not in t or "Customer Number" not in t:',
     "adidas: continuation page skipped"),
    ("oc/adapters/adidas.py", 'CELL = re.compile(r"^(.+)/(\\d+)$")', 'CELL = re.compile(r"^(.+?)/?(\\d+)$")',
     "adidas: size cell without a slash accepted"),
    ("oc/adapters/fog.py", '            sizes_row, state = row, "line"', '            sizes_row, state = sizes_row or row, "line"',
     "fog: first size header reused for every line"),
    ("oc/adapters/fog.py", "TOL = 4.0", "TOL = 40.0", "fog: quantity-to-column tolerance widened"),
    ("oc/adapters/fog.py", "    if len(qtys) != len(cells):", "    if False:", "fog: missing quantity cell accepted"),
    ("oc/adapters/fog.py",
     '            name, dist = min(((k, abs(xc(w) - cols[k])) for k in ("PRICE", "MSRP", "TOTAL")), key=lambda t: t[1])',
     '            name, dist = ("PRICE", "MSRP", "TOTAL")[len(amounts)], 0',
     "fog: amounts assigned by order, so a blank MSRP shifts TOTAL"),
    ("oc/adapters/fog.py", '    col_text = "".join(cur["col"])', '    col_text = " ".join(cur["col"])',
     "fog: wrapped colour lines joined with a space"),
    ("oc/adapters/fog.py", '            if not m or "Order Confirmation" not in t:',
     '            if not m or "Order Confirmation" not in t or "ORDER DATE" not in t:', "fog: continuation page skipped"),
    ("oc/adapters/fog.py", "                if top <= top_end:\n                    continue",
     "                if False:\n                    continue", "fog: repeated page header read as order rows"),
    ("oc/adapters/fog.py", '        if d["discount"]:', "        if False:", "fog: Discount Total not named"),
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
