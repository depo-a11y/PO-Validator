"""Past season POs for the brand: the Types and size runs of past products.

Each PO is the house 29/30-column buy sheet (`<Brand>_<SEASON>_PO.xlsx`). Paths come
from the brand config, newest first; nothing here is committed. A PO can carry other
labels' rows (the Adidas POs hold Y-3, Willy Chavarria, Song for the Mute and Wales
Bonner), so only rows of the config's vendor are read; a row with no Vendor is kept. Past retail prices are
not read: since 2026-11-12 a NEW product's price comes only from a brand RRP in AUD.
"""
import collections

import openpyxl

from .util import norm_code, norm_size, money, title_colour

MCODE = "Metafield: my_fields.manufacture_code"
MCODE_SHORT = "Metafield: my_fields.manu"   # Y-3 FW25 PO heads its code column with the name cut short here


def code_column(hdr):
    """The manufacture_code column: by its full name, with a type suffix, or cut short, never shorter than
    MCODE_SHORT."""
    return next((h for h in hdr if h and (h.startswith(MCODE) or (len(h) >= len(MCODE_SHORT) and MCODE.startswith(h)))),
                None)


class History:
    def __init__(self, seasons):
        # seasons: list of (label, rows) newest first
        self.seasons = seasons
        self.rows = [r for _, rs in seasons for r in rs]
        self.by_code = collections.defaultdict(list)
        for r in self.rows:
            self.by_code[r["code"]].append(r)

    def runs_for_type(self, ptype):
        """Counter of size runs (tuples) per product code for one Type, all seasons."""
        runs = collections.Counter()
        by = collections.defaultdict(set)
        for r in self.rows:
            if r["type"] == ptype and r["size"]:
                by[(r["season"], r["code"])].add(r["size"])
        for sizes in by.values():
            runs[tuple(sorted(sizes, key=_k))] += 1
        return runs


def _k(s):
    from .util import size_key
    return size_key(s)


def _load_one(path, label, vendor=None):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    hdr = [str(h).strip() if h is not None else None for h in next(it)]
    H = {h: i for i, h in enumerate(hdr) if h}

    def g(r, *keys):
        for k in keys:
            i = H.get(k)
            if i is not None and i < len(r):
                return r[i]
        return None

    rows, mcode, want = [], code_column(hdr), str(vendor or "").strip().upper()
    for r in it:
        if not any(v is not None for v in r):
            continue
        code = g(r, mcode)
        if not code:
            continue
        sold_by = str(g(r, "Vendor") or "").strip().upper()
        if want and sold_by and sold_by != want:
            continue
        title = g(r, "Title")
        stem, colour = title_colour(title)
        rows.append(dict(
            season=label, code=norm_code(code), title=title, stem=stem, colour_name=colour,
            type=str(g(r, "Type") or "").strip(), option1=str(g(r, "Option1 Value") or "").strip(),
            size=norm_size(g(r, "Option2 Value")), qty=money(g(r, "Order Qty")) or 0,
            command=str(g(r, "Command") or "").strip().upper()))
    wb.close()
    return rows


def load(paths, vendor=None):
    """paths: list of {label, path} newest first; vendor: the brand config's, whose rows alone are read."""
    seasons = []
    for item in paths:
        seasons.append((item["label"], _load_one(item["path"], item["label"], vendor)))
    return History(seasons)
