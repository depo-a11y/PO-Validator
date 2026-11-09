"""oc: Order Confirmation -> PO buy sheet -> PU (product upload) + delivery file.

    python -m oc draft    --oc OC.pdf --catalog export.csv --brand margiela --out PO-draft.xlsx [--cart CART.xlsx]
    python -m oc complete --po hand_PO.xlsx --oc OC.pdf --catalog export.csv --brand margiela --out PO.xlsx [--cart CART.xlsx]
    python -m oc finalize --po PO.xlsx --catalog export.csv --brand margiela --out-prefix <path-prefix>
    python -m oc reconcile --draft PO-draft.xlsx --po hand_PO.xlsx --oc OC.pdf --out accuracy.md
    python -m oc read     OC.pdf --brand-config brand.yaml [--reader ai] [--json out.json]

All inputs and outputs are files. Nothing here talks to Shopify, Matrixify or
Drive. The AI reader (`--reader ai`, or a brand config with adapter: ai) sends the
OC PDF to the Anthropic API and needs ANTHROPIC_API_KEY. Exit code 1 means a gate
failed; read the Checks sheet or the message. The brand config is found by
--brand-config, else $OC_BRAND_DIR, else oc/brands/.
"""
import argparse
import json
import sys
from pathlib import Path

from . import brand as brandm
from . import catalog as catm
from . import history as histm
from . import service

REPO = Path(__file__).resolve().parent.parent


def _common(args):
    sys.path.insert(0, str(REPO))
    import validator_core
    b = brandm.load(args.brand, args.brand_config)
    print(f"brand config: {b['_path']}")
    cat = catm.load(args.catalog)
    hist = histm.load(b["history"])
    tags = validator_core.load_tag_lookup(str(REPO / "expected_tags.xlsx"))
    return b, cat, hist, tags


def _read(args, b):
    """The OC through the brand's reader (or --reader ai), its reader and gate rows printed. Exits on a FAIL."""
    docs, checks, _ = service.read_oc(args.oc, b, getattr(args, "reader", None))
    _print_checks(checks)
    if docs is None:
        raise SystemExit(1)
    colours = brandm.unmapped_colours(b, docs)
    if colours:
        raise SystemExit(str(brandm.ColourCodeError(colours)))
    return docs, checks


def _cart(args):
    if not getattr(args, "cart", None):
        return None
    from . import cart as cartm
    try:
        c = cartm.load(args.cart)
    except cartm.CartError as e:
        raise SystemExit(f"cart: {e}")
    print(f"cart: {c['label']}, sheet '{c['sheet']}', {len(c['rows'])} style-colours")
    return c


def _fails(checks):
    return [c for c in checks if c["Result"] == "FAIL"]


def cmd_draft(args):
    from . import draft, poxlsx
    b, cat, hist, tags = _common(args)
    docs, rchecks = _read(args, b)
    rows, review, checks, fills = draft.build(docs, b, cat, hist, tags, cart=_cart(args))
    checks = rchecks + checks
    from .util import cost_currency
    poxlsx.write_po(args.out, poxlsx.PO_HEADER, rows, review, checks, fills, currency=cost_currency(b))
    _print_checks(checks)
    print(f"-> {args.out} ({len(rows)} rows)")
    return 1 if _fails(checks) else 0


def cmd_complete(args):
    from . import complete
    b, cat, hist, tags = _common(args)
    docs, _ = _read(args, b)
    checks = complete.run(args.po, docs, b, cat, hist, tags, args.out,
                          price_overrides=_overrides(args.set_price), cart=_cart(args))
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
    b = brandm.load(args.brand, args.brand_config)
    docs, _ = _read(args, b)
    ok = reconcile.run(args.draft, args.po, docs, args.out, args.allow_price or [], b)
    print(f"-> {args.out}")
    return 0 if ok else 1


def cmd_read(args):
    """Read and check an OC only: totals, problems and reader info (tokens, cost, seconds)."""
    from .adapters import otb
    b = brandm.load(args.brand_config) if args.brand_config and not args.brand else brandm.load(args.brand, args.brand_config)
    print(f"brand config: {b['_path']}")
    docs, checks, info = service.read_oc(args.oc, b, args.reader)
    _print_checks(checks)
    if docs is not None:
        t = otb.totals(docs)
        print(f"{t['docs']} confirmation(s), {t['lines']} lines, {t['units']} units, value {t['value']:,.2f} "
              f"(printed {t['printed_units']} units, {t['printed_value']:,.2f})")
        for d in docs:
            print(f"  {d['doc']}: {len(d['lines'])} lines, {d['total_qty']} units, net {d['net']:,.2f} {d['currency']}")
    if args.json:
        Path(args.json).write_text(json.dumps(dict(file=str(args.oc), reader=args.reader or b["adapter"],
                                                   ok=docs is not None and not _fails(checks), checks=checks,
                                                   info=info, docs=docs),
                                              indent=1, ensure_ascii=False, default=str))
        print(f"-> {args.json}")
    return 1 if docs is None or _fails(checks) else 0


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
    c.add_argument("--set-price", action="append", metavar="CODE=PRICE",
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
    rd = sub.add_parser("read", help="read and check an OC only: totals, problems, reader info")
    rd.add_argument("oc")
    rd.add_argument("--brand", help="brand name (else the --brand-config file)")
    rd.add_argument("--json", help="write the docs, checks and reader info (with the AI's raw answer) here")
    rd.set_defaults(fn=cmd_read)
    for sp in (d, c, r, rd):
        sp.add_argument("--reader", choices=["ai"], help="read the OC with the AI reader whatever the brand's adapter")
    for sp in (d, c):
        sp.add_argument("--cart", help="the brand's selling-session cart export (.xlsx): colour words, "
                                       "composition, made in; never quantities or prices")
    for sp in (d, c, f, r, rd):
        sp.add_argument("--brand-config", help="path to the brand .yaml (else $OC_BRAND_DIR, else oc/brands/)")
    args = p.parse_args(argv)
    return args.fn(args)
