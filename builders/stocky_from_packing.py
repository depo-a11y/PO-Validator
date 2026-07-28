#!/usr/bin/env python3
"""
stocky_from_packing.py — build a Stocky receiving CSV from Kering/McQueen
packing-list PDFs (the "ODPL-…" documents), mapping each line to its Marais
Variant SKU + cost via the brand delivery sheet.

It folds in a LOCATION-DISCREPANCY CHECK: a single receive's parcels can be
addressed to different Marais store locations (e.g. Mens → Royal Arcade,
Womens → Bourke St). Stocky receiving is per-location, so a multi-destination
receive must NOT be dropped into one lump. The tool always prints the
per-location split and loudly flags when >1 location is present.

Usage:
    python3 stocky_from_packing.py --delivery "AMQ FW26 DELIVERY.csv" \\
        ODPL-a.pdf ODPL-b.pdf [--out "AMQ FW26 RECEIVE_stocky.csv"] [--split]

Reusable, unit-tested pieces: parse_packing_text, resolve_location, tail,
load_mapping, build_placements, location_check.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --- Ship-to → canonical Marais Shopify location -----------------------------
# First matching rule wins; women before men so "WOMENS" never trips "MEN".
DEFAULT_LOCATION_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"WOMEN|BOURKE", re.I), "Marais - Women"),
    (re.compile(r"\bMEN|ROYAL\s*ARCADE", re.I), "Marais - Men"),
]

# Packing-list line item regexes (Kering ODPL layout).
#   line A:  <tariff8> <stealth10> <ean13> <qty> Pair
#   line C:  <style6> <part> <color> <size>
_RE_ITEM = re.compile(r"(\d{8})\s+(\d{10})\s+(\d{13})\s+(\d+)\s+Pair")
_RE_CODE = re.compile(r"(\d{6})\s+([A-Z0-9]{3,6})\s+(\d{3,4})\s+(\d{2}(?:\.\d)?)\b")
_RE_SHIPTO = re.compile(r"(MARAIS[^\n]*)", re.I)
_RE_PREP = re.compile(r"Packing List\s+(\d+)", re.I)
_RE_IPO = re.compile(r"IPO Invoice Ref\.\s*([\w-]+)", re.I)
_RE_UNITS = re.compile(r"Total number of Units Added\s*:\s*(\d+)", re.I)


@dataclass(frozen=True)
class PackLine:
    style: str
    part: str
    color: str
    size: str
    ean: str
    stealth10: str
    qty: int

    @property
    def mfc(self) -> str:              # manufacture code, e.g. 807881WHAEG9079
        return f"{self.style}{self.part}{self.color}"


@dataclass
class PackingList:
    prep: str
    ipo_ref: str
    ship_to: str
    lines: list[PackLine] = field(default_factory=list)
    stated_units: int | None = None

    @property
    def units(self) -> int:
        return sum(l.qty for l in self.lines)


@dataclass(frozen=True)
class Placement:
    sku: str
    cost: float
    qty: int
    size: str
    mfc: str
    ean: str
    ship_to: str
    location: str | None
    prep: str
    ipo_ref: str


@dataclass
class LocationReport:
    locations: list[str]                       # distinct resolved locations, sorted
    by_location: dict                          # location -> {"units": int, "skus": [(sku, qty), ...]}
    multi: bool                                # True => location discrepancy
    unmapped: list[str]                        # ship-to strings that resolved to no location


def tail(code: str) -> str:
    """Return a manufacture code from its first letter onward (drops the style
    prefix), so a source typo in the leading digits still joins. e.g.
    '53770WIAIH9061 ' and '553770WIAIH9061' both -> 'WIAIH9061'."""
    m = re.search(r"[A-Za-z].*", code.strip())
    return re.sub(r"\s+", "", m.group()) if m else code.strip()


def resolve_location(ship_to: str, rules=DEFAULT_LOCATION_RULES) -> str | None:
    for pat, loc in rules:
        if pat.search(ship_to or ""):
            return loc
    return None


def _money(s) -> float | None:
    if s is None:
        return None
    m = re.search(r"[\d.]+", str(s).replace(",", ""))
    return round(float(m.group()), 2) if m else None


def parse_packing_text(text: str) -> PackingList:
    """Parse one packing list's extracted text into a PackingList."""
    prep = _RE_PREP.search(text)
    ipo = _RE_IPO.search(text)
    ship = _RE_SHIPTO.search(text)
    units = _RE_UNITS.search(text)
    items = _RE_ITEM.findall(text)
    codes = _RE_CODE.findall(text)
    if len(items) != len(codes):
        raise ValueError(f"line/code count mismatch: {len(items)} items vs {len(codes)} codes")
    lines = [
        PackLine(style=st, part=pt, color=co, size=sz, ean=ean, stealth10=s10, qty=int(q))
        for (tar, s10, ean, q), (st, pt, co, sz) in zip(items, codes)
    ]
    return PackingList(
        prep=prep.group(1) if prep else "",
        ipo_ref=ipo.group(1) if ipo else "",
        ship_to=ship.group(1).strip() if ship else "",
        lines=lines,
        stated_units=int(units.group(1)) if units else None,
    )


def parse_packing_pdf(path: str) -> PackingList:
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    return parse_packing_text(text)


class Mapping:
    """(manufacture_code, size) -> (Variant SKU, Variant Cost). Falls back to a
    tail (part+color) match so a leading-digit typo in the delivery sheet still
    resolves; ambiguous tail matches raise rather than guess."""

    def __init__(self):
        self._full: dict = {}
        self._tail: dict = {}

    def add(self, mfc: str, size: str, sku: str, cost: float | None):
        self._full[(re.sub(r"\s+", "", mfc), str(size).strip())] = (sku, cost)
        self._tail.setdefault((tail(mfc), str(size).strip()), set()).add((sku, cost))

    def lookup(self, mfc: str, size: str):
        key = (re.sub(r"\s+", "", mfc), str(size).strip())
        if key in self._full:
            return self._full[key]
        cands = self._tail.get((tail(mfc), str(size).strip()))
        if not cands:
            return None
        if len(cands) > 1:
            raise ValueError(f"ambiguous mapping for {mfc} size {size}: {sorted(s for s, _ in cands)}")
        return next(iter(cands))


def _stripcols(headers):
    return {str(h).strip(): i for i, h in enumerate(headers)}


def load_mapping(path: str) -> Mapping:
    """Load the brand delivery sheet (.csv or .xlsx) into a Mapping. Requires
    columns (spaces tolerated): 'Variant SKU', 'Variant Cost', 'Option2 Value'
    (size) and a 'manufacture_code' metafield column."""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        import openpyxl
        wb = openpyxl.load_workbook(p, data_only=True)
        ws = wb.active
        rows = [[c.value for c in r] for r in ws.iter_rows()]
    else:
        with open(p, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
    H = _stripcols(rows[0])
    i_sku = H["Variant SKU"]
    i_size = H["Option2 Value"]
    i_cost = H.get("Variant Cost")
    i_mfc = next(i for h, i in H.items() if "manufacture_code" in h)
    m = Mapping()
    for r in rows[1:]:
        if not any(str(c).strip() for c in r if c is not None):
            continue
        sku = str(r[i_sku]).strip()
        if not sku:
            continue
        m.add(str(r[i_mfc]), str(r[i_size]), sku,
              _money(r[i_cost]) if i_cost is not None else None)
    return m


def build_placements(packing_lists: list[PackingList], mapping: Mapping,
                     rules=DEFAULT_LOCATION_RULES):
    """Map every packing-list line to a Placement. Returns (placements, errors)."""
    placements, errors = [], []
    for pl in packing_lists:
        if pl.stated_units is not None and pl.units != pl.stated_units:
            errors.append(f"PL {pl.prep}: parsed {pl.units} units != stated {pl.stated_units}")
        loc = resolve_location(pl.ship_to, rules)
        for ln in pl.lines:
            try:
                hit = mapping.lookup(ln.mfc, ln.size)
            except ValueError as e:
                errors.append(str(e)); continue
            if not hit:
                errors.append(f"no SKU for {ln.mfc} size {ln.size} (PL {pl.prep})"); continue
            sku, cost = hit
            placements.append(Placement(sku=sku, cost=cost, qty=ln.qty, size=ln.size,
                                        mfc=ln.mfc, ean=ln.ean, ship_to=pl.ship_to,
                                        location=loc, prep=pl.prep, ipo_ref=pl.ipo_ref))
    return placements, errors


def location_check(placements: list[Placement]) -> LocationReport:
    """The folded-in check: group received units by resolved location and flag
    when a single receive spans more than one location."""
    by = {}
    unmapped = []
    for p in placements:
        if p.location is None:
            if p.ship_to not in unmapped:
                unmapped.append(p.ship_to)
            continue
        b = by.setdefault(p.location, {"units": 0, "_skus": {}})
        b["units"] += p.qty
        b["_skus"][p.sku] = b["_skus"].get(p.sku, 0) + p.qty
    for loc, b in by.items():
        b["skus"] = sorted(b.pop("_skus").items())
    locations = sorted(by)
    return LocationReport(locations=locations, by_location=by,
                          multi=len(locations) > 1, unmapped=unmapped)


def _agg(placements, keyed_by_location=False):
    agg = {}
    for p in placements:
        k = (p.location, p.sku) if keyed_by_location else (p.sku,)
        a = agg.setdefault(k, {"sku": p.sku, "cost": p.cost, "qty": 0, "location": p.location})
        a["qty"] += p.qty
    return list(agg.values())


def write_stocky(placements, path):
    rows = sorted(_agg(placements), key=lambda a: a["sku"])
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Variant SKU", "Variant Cost", "Qty"])
        for a in rows:
            w.writerow([a["sku"], a["cost"], a["qty"]])
    return path


def write_stocky_split(placements, out_dir, stem):
    written = []
    rows = _agg(placements, keyed_by_location=True)
    locs = sorted({a["location"] for a in rows}, key=lambda x: (x is None, x))
    for loc in locs:
        tag = re.sub(r"[^A-Za-z0-9]+", "_", (loc or "UNMAPPED")).strip("_")
        path = Path(out_dir) / f"{stem}_{tag}.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Variant SKU", "Variant Cost", "Qty"])
            for a in sorted((a for a in rows if a["location"] == loc), key=lambda a: a["sku"]):
                w.writerow([a["sku"], a["cost"], a["qty"]])
        written.append(str(path))
    return written


def print_report(report: LocationReport, placements) -> None:
    total = sum(p.qty for p in placements)
    print(f"\nReceived {total} units across {len(report.locations)} location(s).")
    if report.multi:
        print("  ⚠️  LOCATION DISCREPANCY — parcels are bound for multiple locations.")
        print("      Stocky receiving is per-location: use --split (or receive per PO).")
    for loc in report.locations:
        b = report.by_location[loc]
        print(f"  • {loc}: {b['units']} units — " + ", ".join(f"{s}×{q}" for s, q in b["skus"]))
    if report.unmapped:
        print("  ⚠️  UNMAPPED ship-to (no location rule matched):")
        for s in report.unmapped:
            print(f"      - {s!r}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build a Stocky receiving CSV from packing-list PDFs.")
    ap.add_argument("pdfs", nargs="+", help="packing-list PDF path(s)")
    ap.add_argument("--delivery", required=True, help="brand delivery sheet (.csv/.xlsx) for SKU+cost")
    ap.add_argument("--out", default=None, help="output Stocky CSV (default alongside first PDF)")
    ap.add_argument("--split", action="store_true", help="also emit one CSV per location")
    a = ap.parse_args(argv)

    mapping = load_mapping(a.delivery)
    pls = [parse_packing_pdf(p) for p in a.pdfs]
    placements, errors = build_placements(pls, mapping)
    report = location_check(placements)

    out = a.out or str(Path(a.pdfs[0]).with_name("RECEIVE_stocky.csv"))
    write_stocky(placements, out)
    print(f"Stocky CSV -> {out}  ({len({p.sku for p in placements})} SKUs, "
          f"{sum(p.qty for p in placements)} units)")
    print_report(report, placements)
    if a.split:
        for w in write_stocky_split(placements, Path(out).parent, Path(out).stem):
            print(f"  split -> {w}")
    if errors:
        print("\nEXCEPTIONS (blocking — resolve before receiving):")
        for e in errors:
            print("  -", e)
        return 2
    # A location discrepancy is a warning, not a hard block, but signal it in the exit code.
    return 1 if report.multi else 0


if __name__ == "__main__":
    sys.exit(main())
