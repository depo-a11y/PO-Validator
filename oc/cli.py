"""oc: Order Confirmation -> PO buy sheet -> PU (product upload) + delivery file.

    python -m oc draft    --oc OC.pdf --catalog export.csv --brand margiela --out PO-draft.xlsx
    python -m oc complete --po hand_PO.xlsx --oc OC.pdf --catalog export.csv --brand margiela --out PO.xlsx
    python -m oc finalize --po PO.xlsx --catalog export.csv --brand margiela --out-prefix <path-prefix>
    python -m oc reconcile --draft PO-draft.xlsx --po hand_PO.xlsx --oc OC.pdf --out accuracy.md

All inputs and outputs are files. Nothing here talks to Shopify, Matrixify or
Drive. Exit code 1 means a gate failed; read the Checks sheet or the message.
"""
import argparse
import sys
from pathlib import Path

from . import brand as brandm
from . import catalog as catm
from . import history as histm

REPO = Path(__file__).resolve().parent.parent


def _common(args):
    sys.path.insert(0, str(REPO))
    import validator_core
    b = brandm.load(args.brand)
    cat = catm.load(args.catalog)
    hist = histm.load(b["history"])
    tags = validator_core.load_tag_lookup(str(REPO / "expected_tags.xlsx"))
    return b, cat, hist, tags


def _adapter(b):
    if b["adapter"] == "otb":
        from .adapters import otb
        return otb
    raise SystemExit(f"unknown adapter {b['adapter']}")


def _fails(checks):
    return [c for c in checks if c["Result"] == "FAIL"]


def cmd_draft(args):
    from . import draft, poxlsx
    b, cat, hist, tags = _common(args)
    docs = _adapter(b).parse_checked(args.oc)
    rows, review, checks, fills = draft.build(docs, b, cat, hist, tags)
    poxlsx.write_po(args.out, poxlsx.PO_HEADER, rows, review, checks, fills)
    _print_checks(checks)
    print(f"-> {args.out} ({len(rows)} rows)")
    return 1 if _fails(checks) else 0


def cmd_complete(args):
    from . import complete
    b, cat, hist, tags = _common(args)
    docs = _adapter(b).parse_checked(args.oc)
    checks = complete.run(args.po, docs, b, cat, hist, tags, args.out,
                          price_overrides=_overrides(args.set_price))
    _print_checks(checks)
    print(f"-> {args.out}")
    return 1 if _fails(checks) else 0


def _overrides(items):
    out = {}
    for it in items or []:
        code, _, eur = it.rpartition("=")
        out[code.strip()] = float(eur)
    return out


def cmd_finalize(args):
    from . import finalize
    b, cat, hist, tags = _common(args)
    checks = finalize.run(args.po, b, cat, args.out_prefix)
    _print_checks(checks)
    return 1 if _fails(checks) else 0


def cmd_reconcile(args):
    from . import reconcile
    b = brandm.load(args.brand)
    docs = _adapter(b).parse_checked(args.oc)
    ok = reconcile.run(args.draft, args.po, docs, args.out, args.allow_price or [])
    print(f"-> {args.out}")
    return 0 if ok else 1


def _print_checks(checks):
    for c in checks:
        print(f"[{c['Result']:4}] {c['Check']}: {str(c['Detail'])[:300]}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m oc", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft", help="OC + catalog -> PO draft with Review and Checks sheets")
    d.add_argument("--oc", required=True)
    d.add_argument("--catalog", required=True)
    d.add_argument("--brand", required=True)
    d.add_argument("--out", required=True)
    d.set_defaults(fn=cmd_draft)
    c = sub.add_parser("complete", help="fill the blanks of a PO someone started by hand")
    c.add_argument("--po", required=True)
    c.add_argument("--oc", required=True)
    c.add_argument("--catalog", required=True)
    c.add_argument("--brand", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--set-price", action="append", metavar="CODE=EUR",
                   help="correct a wholesale price to the OC's (repeatable)")
    c.set_defaults(fn=cmd_complete)
    f = sub.add_parser("finalize", help="reviewed PO -> PU-new, PU-merge, delivery file")
    f.add_argument("--po", required=True)
    f.add_argument("--catalog", required=True)
    f.add_argument("--brand", required=True)
    f.add_argument("--out-prefix", required=True)
    f.set_defaults(fn=cmd_finalize)
    r = sub.add_parser("reconcile", help="compare a draft with a hand-built PO")
    r.add_argument("--draft", required=True)
    r.add_argument("--po", required=True)
    r.add_argument("--oc", required=True)
    r.add_argument("--brand", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--allow-price", action="append", metavar="CODE",
                   help="a code whose wholesale is known to differ (reported, not failed)")
    r.set_defaults(fn=cmd_reconcile)
    args = p.parse_args(argv)
    return args.fn(args)
