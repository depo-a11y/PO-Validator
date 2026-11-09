"""Past season POs for the brand: retail ladder, Type runs, colour names.

Each PO is the house 29/30-column buy sheet (`<Brand>_<SEASON>_PO.xlsx`) with
Wholesale Price (supplier currency) next to Variant Price (AUD). Paths come from
the brand config, newest first; nothing here is committed.
"""
import collections
import statistics

import openpyxl

from .util import norm_code, norm_size, money, title_colour

MCODE = "Metafield: my_fields.manufacture_code"


class History:
    def __init__(self, seasons):
        # seasons: list of (label, rows) newest first
        self.seasons = seasons
        self.rows = [r for _, rs in seasons for r in rs]
        self.by_code = collections.defaultdict(list)
        for r in self.rows:
            self.by_code[r["code"]].append(r)

    def ladder(self, eur):
        """Most common retail for this exact wholesale in the newest season that has it."""
        for label, rs in self.seasons:
            c = collections.Counter(r["price"] for r in rs
                                    if r["eur"] is not None and abs(r["eur"] - eur) < 0.005 and r["price"])
            if c:
                (price, n), = c.most_common(1)
                return price, f"{label}: EUR {eur:g} sold at A${price:g} on {n} row(s)"
        return None, None

    def ratio(self, category=None):
        """Median retail/wholesale in the newest season, optionally for one category."""
        for label, rs in self.seasons:
            vals = [r["price"] / r["eur"] for r in rs if r["eur"] and r["price"]
                    and (category is None or category_of(r["type"]) == category)]
            if len(vals) >= 5:
                return statistics.median(vals), f"{label} median retail/wholesale" + (f" for {category}" if category else "")
        return None, None

    def wholesale_of(self, code):
        vals = [r["eur"] for r in self.by_code.get(norm_code(code), []) if r["eur"]]
        return vals[0] if vals else None

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


def category_of(ptype):
    p = str(ptype or "").split()
    return p[1] if len(p) > 1 else None


def _load_one(path, label):
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

    rows = []
    for r in it:
        if not any(v is not None for v in r):
            continue
        code = g(r, MCODE)
        if not code:
            continue
        title = g(r, "Title")
        stem, colour = title_colour(title)
        rows.append(dict(
            season=label, code=norm_code(code), title=title, stem=stem, colour_name=colour,
            type=str(g(r, "Type") or "").strip(), option1=str(g(r, "Option1 Value") or "").strip(),
            size=norm_size(g(r, "Option2 Value")), qty=money(g(r, "Order Qty")) or 0,
            eur=money(g(r, "Wholesale Price")), price=money(g(r, "Variant Price")),
            command=str(g(r, "Command") or "").strip().upper()))
    wb.close()
    return rows


def load(paths):
    """paths: list of {label, path} newest first."""
    seasons = []
    for item in paths:
        seasons.append((item["label"], _load_one(item["path"], item["label"])))
    return History(seasons)
