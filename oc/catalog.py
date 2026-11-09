"""Catalog input: a Matrixify Products export (CSV) for the brand's vendors.

Contract (the colleague runs the same saved Matrixify export template):
UTF-8 with BOM; one row per variant; `Top Row` = true on a product's first row,
and product-level fields are filled only there, so they are forward-filled by ID.
Required columns are listed in REQUIRED, and a missing one fails the load naming
the Matrixify column group it is ticked in (GROUP). The product-level join key is
`Metafield: my_fields.manufacture_code [single_line_text_field]`. The season and
`Variant Inventory Qty` are required because finalize's rollback restores the
season from this export and its cost restatement counts available units from it.

Variant-level metafields are NOT in the export, which is why the MERGE upload
never emits a variant-metafield column for an existing variant (a blank cell
clears the field on import).
"""
import csv
import collections
import re

from .util import norm_code, norm_size, money, title_colour

MCODE = "Metafield: my_fields.manufacture_code [single_line_text_field]"
MADE_IN = "Metafield: custom.made_in [single_line_text_field]"
SEASON = "Metafield: custom.product_season [single_line_text_field]"
NEW_SALE = "Metafield: custom.new_sale [single_line_text_field]"
AVAILABLE = "Variant Inventory Qty"

# Each required column and the column group of the Matrixify Products export it is ticked in.
GROUP = {
    **dict.fromkeys(["ID", "Handle", "Title", "Vendor", "Type", "Tags", "Status", "Top Row"], "Basic Columns"),
    **dict.fromkeys(["Variant ID", "Option1 Name", "Option1 Value", "Option2 Name", "Option2 Value", "Variant SKU",
                     "Variant Price", "Variant Compare At Price", AVAILABLE], "Inventory / Variants"),
    "Variant Cost": "Variant Cost",
    MCODE: "Metafields",
    SEASON: "Metafields",
}
REQUIRED = list(GROUP)

PRODUCT_FIELDS = ["Created At", "Handle", "Title", "Body HTML", "Vendor", "Type", "Tags", "Status",
                  "Option1 Name", "Option2 Name", MCODE, MADE_IN, SEASON, NEW_SALE,
                  "Metafield: title_tag [string]", "Metafield: custom.sub_category [single_line_text_field]",
                  "Metafield: custom.local_market_price [single_line_text_field]"]


class CatalogError(Exception):
    pass


class Catalog:
    def __init__(self, products, path=None):
        self.products = products          # id -> product dict (with "variants")
        self.path = path
        self.by_code = collections.defaultdict(list)
        for p in products.values():
            if p["code"]:
                self.by_code[p["code"]].append(p)
        self.skus = {v["sku"] for p in products.values() for v in p["variants"] if v["sku"]}
        self.handles = {p["handle"] for p in products.values()}

    # ---- matching -----------------------------------------------------------
    def match(self, code):
        """Live product for a join key. Returns (product | None, note, candidates).

        ARCHIVED twins are dropped. Two or more non-archived products on one key
        is a hard failure: the caller must not pick one.
        """
        cands = self.by_code.get(norm_code(code), [])
        live = [p for p in cands if p["status"].upper() != "ARCHIVED"]
        if len(live) == 1:
            note = "" if len(cands) == 1 else f"dropped {len(cands) - 1} ARCHIVED twin(s)"
            return live[0], note, cands
        if len(live) > 1:
            return None, "AMBIGUOUS: " + ", ".join(f"{p['id']} {p['status']} {p['title']}" for p in live), cands
        if cands:
            return None, "only ARCHIVED products carry this code", cands
        return None, "", cands

    def style_family(self, prefix, exclude_archived=False):
        out = []
        for code, ps in self.by_code.items():
            if code.startswith(prefix):
                out.extend(p for p in ps if not (exclude_archived and p["status"].upper() == "ARCHIVED"))
        return out

    def max_sku_number(self, prefix):
        best = 0
        pat = re.compile(re.escape(prefix) + r"(\d+)$")
        for s in self.skus:
            m = pat.match(s)
            if m:
                best = max(best, int(m.group(1)))
        return best


def _size_and_colour(names, values):
    size = colour = None
    for n, v in zip(names, values):
        n = (n or "").strip().lower()
        if n == "size":
            size = v
        elif n in ("colour", "color"):
            colour = v
    return size, colour


def load(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise CatalogError(
                "catalog export is missing columns: "
                + "; ".join(f"'{c}' (Matrixify Products export, {GROUP[c]} group)" for c in missing)
                + ". Export the brand's products again from Matrixify with these columns ticked.")
        rows = list(reader)

    products = {}
    cur = {}
    for r in rows:
        pid = r["ID"].strip()
        if not pid:
            raise CatalogError("catalog row with no product ID")
        if r.get("Top Row", "").strip().lower() == "true" or pid not in products:
            cur = {k: r.get(k, "") for k in PRODUCT_FIELDS}
            p = products.setdefault(pid, dict(
                id=pid, handle=cur["Handle"].strip(), title=cur["Title"].strip(),
                vendor=cur["Vendor"].strip(), type=cur["Type"].strip(),
                tags=cur["Tags"], status=cur["Status"].strip(), body=cur.get("Body HTML", ""),
                code=norm_code(cur[MCODE]), raw_code=cur[MCODE],
                made_in=(cur.get(MADE_IN) or "").strip(),
                season=(cur.get(SEASON) or "").strip(),
                new_sale=(cur.get(NEW_SALE) or "").strip(),
                option_names=[cur["Option1 Name"].strip(), cur["Option2 Name"].strip()],
                created_key=int(re.sub(r"\D", "", (cur.get("Created At") or "")[:19]) or 0),
                variants=[]))
        p = products[pid]
        names = [r["Option1 Name"] or p["option_names"][0], r["Option2 Name"] or p["option_names"][1]]
        size, colour = _size_and_colour(names, [r["Option1 Value"], r["Option2 Value"]])
        p["variants"].append(dict(
            variant_id=r["Variant ID"].strip(), sku=r["Variant SKU"].strip(),
            barcode=(r.get("Variant Barcodes") or r.get("Variant Barcode") or "").strip(),
            option1=r["Option1 Value"], option2=r["Option2 Value"],
            size=norm_size(size) if size is not None else "", colour=colour,
            price=money(r["Variant Price"]), compare_at=money(r["Variant Compare At Price"]),
            cost=money(r["Variant Cost"]), available=money(r[AVAILABLE]) or 0,
            country=(r.get("Variant Country of Origin") or "").strip(),
            hs=(r.get("Variant HS Code") or "").strip()))
    for p in products.values():
        p["stem"], p["title_colour"] = title_colour(p["title"])
        p["colour_family"] = collections.Counter(v["colour"] for v in p["variants"] if v["colour"]).most_common(1)[0][0] \
            if any(v["colour"] for v in p["variants"]) else None
        p["countries"] = collections.Counter(v["country"] for v in p["variants"] if v["country"])
    return Catalog(products, path)
