"""The OC steps for a caller with no command line (the Streamlit page), UI-free.

    draft(oc_path, catalog_path, brand_cfg, cart_path=None, history_paths=None, reader=None)
        -> (po_xlsx_bytes | None, review_df, checks)
    finalize(po_path, catalog_path, brand_cfg, when=None) -> (zip_bytes | None, checks, gate_errors_df)

Same steps as `python -m oc draft` and `python -m oc finalize`. Inputs are paths;
everything written goes to a temporary folder and comes back as bytes, so
nothing lands in the repo.

Two differences from the CLI, both for a hosted app:
- Past season POs. The brand config lists them as paths on one laptop. Here
  `history_paths` (uploaded files) replaces the config's list, and config paths
  that do not exist are skipped with a WARN check instead of a crash. A config
  from text (an upload or the app's secrets) never has its paths opened at all
  (see oc/brand.py). Either way a "Past season POs" check says what the retail
  prices were built from.
- Bad inputs (an OC that fails its gates, a catalog missing columns, an
  unreadable linesheet) come back as FAIL checks rather than exceptions.

Brands saved in the app's secrets are offered only to a signed-in viewer on the
`oc_viewers` allowlist (secret_brands_for).

draft returns no workbook when a FAIL is one a reviewer cannot fix in the sheet
(see FIXABLE_IN_SHEET). finalize is all or nothing: any FAIL, no zip.

The OC reader is the brand config's `adapter` (otb or ai); `reader="ai"` forces the AI
reader for an otb brand whose layout changed. Nothing falls back from one reader to the
other on its own. Either way the Checks carry an "Order confirmation reader" row: INFO
for otb, WARN for AI (model, time, cost, and the instruction to check sizes by eye).
"""
import collections
import csv
import datetime
import importlib
import io
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import openpyxl
import pandas as pd

from . import brand as brandm
from . import catalog as catm
from . import finalize as finalizem
from . import history as histm
from . import poxlsx
from .util import cost_currency, currency_off, slug

REPO = Path(__file__).resolve().parent.parent

# Draft FAILs that a reviewer clears by editing the PO (finalize checks each again).
# Matched as prefixes. Any other FAIL, including a check renamed later, stops the draft.
FIXABLE_IN_SHEET = (
    "NEW title does not collide with a live handle",
    "One manufacture code per NEW title",
    "Every NEW Type is in expected_tags.xlsx",
    "NEW rows clear the ",
)

ZIP_MEMBERS = finalizem.OUTPUTS + ["finalize-checks.xlsx"]
HISTORY_CHECK = "Past season POs (retail price history)"
READER_CHECK = "Order confirmation reader"
CURRENCY_CHECK = "Order confirmation is in the brand's cost currency"
GATE_CHECK = "Order confirmation passes its gates (parsed = printed)"
AI_HINT = "If this is the right brand and its layout changed, tick 'Read the OC with AI' and build again."
LAYOUTS = {"otb": "the OTB layout (Maison Margiela, MM6)", "admilano": "the AD Milano layout (Ann Demeulemeester)",
           "adidas": "the adidas AG layout (Y-3, Willy Chavarria, RS)",
           "fog": "the Fear of God layout (FOG Essentials, Fear of God LLC)"}


def fixed_reader_row(name):
    return _check(READER_CHECK, "INFO", f"read by the built-in reader for {LAYOUTS[name]}, which reads the PDF's "
                                        "text by position; no AI was used")
RED_WARNING = "Retail prices for new styles will mostly come back red."

SEASON_RE = re.compile(r"\b(S/S|F/W|A/W)\s*(\d{2})\b")
FILE_SEASON_RE = re.compile(r"(?<![A-Z])(SS|FW|AW)[\s_-]?(\d{2})(?!\d)", re.I)


def fails(checks):
    return [c for c in checks if c["Result"] == "FAIL"]


def blocking(checks):
    return [c for c in fails(checks) if not str(c["Check"]).startswith(FIXABLE_IN_SHEET)]


def _check(name, result, detail):
    return dict(Check=name, Result=result, Detail=detail)


def adapter(brand_cfg):
    """The reader module for the config's adapter: one of brand.ADAPTERS."""
    name = brand_cfg["adapter"]
    if name not in brandm.ADAPTERS:
        raise brandm.BrandError(f"unknown adapter '{name}'")
    return importlib.import_module(f".adapters.{name}", __package__)


def read_oc(oc_path, brand_cfg, reader=None, ai_factory=None):
    """The OC as docs, by the config's reader or `reader="ai"`. -> (docs | None, checks, info).

    docs None means a FAIL among the checks. info is the AI reader's (model, tokens, seconds, cost and its raw
    answer), None for a fixed reader. `ai_factory(key) -> client` replaces the Anthropic client (tests).
    Whatever the reader, an OC whose currency (normalised: otb prints "European Euro") is not the brand's
    cost_currency is refused."""
    if (reader or brand_cfg["adapter"]) == "ai":
        from .adapters import ai
        try:
            docs, warns, info = ai.read(oc_path, brand_cfg, factory=ai_factory)
        except ai.ReadRefused as e:
            return None, [ai.reader_check(e.info, refused=True)] + e.checks, e.info
        return docs, [ai.reader_check(info)] + warns, info
    try:
        reader_mod = adapter(brand_cfg)
    except brandm.BrandError as e:
        return None, [_check("Brand config names a known OC layout", "FAIL", str(e))], None
    row = fixed_reader_row(brand_cfg["adapter"])
    try:
        docs = reader_mod.parse_checked(oc_path)
    except Exception as e:
        problems = getattr(e, "problems", None) or [f"{type(e).__name__}: {e}"]
        return None, [row] + [_check(GATE_CHECK, "FAIL", f"{p}. {AI_HINT}" if p == "no order confirmations found" else p)
                              for p in problems], None
    off = currency_off(docs, cost_currency(brand_cfg))
    if off:
        return None, [row, _check(CURRENCY_CHECK, "FAIL", currency_detail(off, brand_cfg))], None
    return docs, [row], None


def currency_detail(off, brand_cfg):
    return (f"{'; '.join(off)}, but the brand's cost currency is {cost_currency(brand_cfg)}. "
            "Check you picked the right brand and document.")


def cart_supported():
    try:
        from . import cart
    except ImportError:
        return False
    return hasattr(cart, "load") and hasattr(cart, "CartError")


def _core():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    import validator_core
    return validator_core


# ---- naming --------------------------------------------------------------------
def now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Australia/Melbourne"))
    except Exception:
        return datetime.datetime.now()


def file_prefix(brand_cfg, when=None):
    """'2026-11-09-1432-margiela-ss27-': Melbourne date and time, brand, season.

    The time keeps two runs on one day from sharing file names. The prefix without its
    trailing '-' is the run id (finalize.run_id) that every file of one run carries."""
    when = when or now()
    season = re.sub(r"[^a-z0-9]", "", str(brand_cfg["season"]).lower())
    return f"{when:%Y-%m-%d-%H%M}-{slug(brand_cfg.get('_name') or brand_cfg['vendor'])}-{season}-"


def zip_name(prefix):
    return prefix + "uploads.zip"


# ---- past season POs -----------------------------------------------------------
def season_key(label):
    m = SEASON_RE.search(str(label or "").upper())
    return (int(m.group(2)), 0 if m.group(1) == "S/S" else 1) if m else None


def paths_ignored(brand_cfg):
    """True for a config from text: it names past POs, but their paths are never opened."""
    return bool(brand_cfg.get("history_files")) and not brand_cfg.get("history")


def history_status(brand_cfg):
    """(present, missing): the config's past POs found on this computer and not found.
    Every past PO of a config from text counts as missing."""
    if paths_ignored(brand_cfg):
        return [], list(brand_cfg["history_files"])
    hist = brand_cfg.get("history") or []
    return ([h for h in hist if os.path.exists(h["path"])],
            [h for h in hist if not os.path.exists(h["path"])])


def label_upload(path, brand_cfg):
    """Season label for an uploaded past PO: the config's label for a file of the same name,
    else the most common value of its season column, else a season in its file name."""
    base = os.path.basename(path)
    named = brand_cfg.get("history_files") or [dict(label=h["label"], file=os.path.basename(h["path"]))
                                               for h in brand_cfg.get("history") or []]
    for h in named:
        if h["file"] == base:
            return h["label"], "brand config"
    seasons = collections.Counter()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        it = wb.worksheets[0].iter_rows(values_only=True)
        hdr = [str(h).strip() if h is not None else None for h in next(it, ())]
        if poxlsx.SEASON in hdr:
            i = hdr.index(poxlsx.SEASON)
            for r in it:
                v = r[i] if i < len(r) else None
                if season_key(v):
                    seasons[str(v).strip()] += 1
    finally:
        wb.close()
    if seasons:
        return seasons.most_common(1)[0][0], "season column"
    m = FILE_SEASON_RE.search(base)
    if m:
        return f"{dict(SS='S/S', FW='F/W', AW='A/W')[m.group(1).upper()]} {m.group(2)}", "file name"
    return None, None


def plan_history(brand_cfg, history_paths=None):
    """The {label, path} list the draft uses, newest first, and the check row that says so."""
    if history_paths:
        items, unlabelled = [], []
        for p in history_paths:
            label, how = label_upload(p, brand_cfg)
            if label is None:
                unlabelled.append(os.path.basename(p))
                label = Path(p).stem
            items.append(dict(label=label, path=p))
        items.sort(key=lambda h: season_key(h["label"]) or (-1, -1), reverse=True)
        detail = f"{len(items)} uploaded, used instead of the brand config's list"
        if unlabelled:
            return items, _check(HISTORY_CHECK, "WARN", detail + f"; no season found in {', '.join(unlabelled)}, "
                                                            "so they are read last")
        return items, _check(HISTORY_CHECK, "INFO", detail)
    present, missing = history_status(brand_cfg)
    if paths_ignored(brand_cfg):
        return [], _check(HISTORY_CHECK, "WARN",
                          f"the brand config names {len(missing)} ({', '.join(h['label'] for h in missing)}), but a "
                          "config from an upload or the app's secrets never opens files on the server. "
                          f"{RED_WARNING} Upload them as past season POs to fix this.")
    if missing:
        return present, _check(HISTORY_CHECK, "WARN",
                               f"{len(missing)} of {len(missing) + len(present)} in the brand config are not on this "
                               f"computer ({', '.join(h['label'] for h in missing)}). {RED_WARNING} "
                               "Upload them as past season POs to fix this.")
    if not present:
        return [], _check(HISTORY_CHECK, "WARN", f"none in the brand config and none uploaded. {RED_WARNING}")
    return present, _check(HISTORY_CHECK, "INFO", f"{len(present)} from the brand config")


def _history_detail(check, hist):
    seasons = [(label, len(rows)) for label, rows in hist.seasons]
    empty = [label for label, n in seasons if not n]
    check = dict(check, Detail=check["Detail"] + ": " + ", ".join(f"{label} ({n} rows)" for label, n in seasons)
                 if seasons else check["Detail"])
    if empty:
        check.update(Result="WARN", Detail=check["Detail"] + f". No manufacture codes in {', '.join(empty)}: "
                                                             "is that a PO buy sheet?")
    return check


# ---- who may use the brands saved in the app ---------------------------------------
def secret_brands_for(table, viewers, email):
    """({name: config}, reason): the brands saved in the app's secrets that this viewer may use.

    Offered only when an `oc_viewers` allowlist exists and the viewer's sign-in email is on
    it (case-insensitive, whole addresses). Otherwise no brands, and `reason` says why."""
    if not table:
        return {}, None
    if isinstance(viewers, str):
        viewers = [viewers]
    allowed = {str(v).strip().lower() for v in viewers or [] if str(v).strip()}
    if not allowed:
        return {}, "no viewer allowlist (oc_viewers) is set in the app's secrets"
    if not email or not str(email).strip():
        return {}, "you are not signed in"
    if str(email).strip().lower() not in allowed:
        return {}, f"{email} is not on the viewer allowlist"
    return dict(table), None


# ---- step 1 --------------------------------------------------------------------
def draft(oc_path, catalog_path, brand_cfg, cart_path=None, history_paths=None, reader=None, ai_factory=None):
    """OC + catalog (+ linesheet, + past POs) -> (draft PO xlsx bytes | None, Review DataFrame, checks)."""
    from . import draft as draftm
    empty = pd.DataFrame()
    core = _core()
    try:
        items, hcheck = plan_history(brand_cfg, history_paths)
        hist = histm.load(items)
    except Exception as e:
        return None, empty, [_check(HISTORY_CHECK, "FAIL", f"could not read a past PO: {type(e).__name__}: {e}")]
    pre = [_history_detail(hcheck, hist)]
    try:
        cat = catm.load(catalog_path)
    except catm.CatalogError as e:
        return None, empty, pre + [_check("Catalog export has the columns the tool needs", "FAIL", str(e))]
    except (UnicodeDecodeError, csv.Error) as e:
        return None, empty, pre + [_check("Catalog export is a Matrixify CSV", "FAIL", f"{type(e).__name__}: {e}")]
    docs, rchecks, _ = read_oc(oc_path, brand_cfg, reader, ai_factory)
    pre += rchecks
    if docs is None:
        return None, empty, pre
    colours = brandm.unmapped_colours(brand_cfg, docs)
    if colours:
        return None, empty, pre + [_check("Every OC colour has a code in the brand config (colour_codes)", "FAIL",
                                          str(brandm.ColourCodeError(colours)))]
    cart = None
    if cart_path:
        try:
            from . import cart as cartm
        except ImportError:
            return None, empty, pre + [_check("Linesheet/cart export can be read", "FAIL",
                                              "this version of the tool cannot read a linesheet yet; "
                                              "build the draft without one")]
        try:
            cart = cartm.load(cart_path)
        except cartm.CartError as e:
            return None, empty, pre + [_check("Linesheet/cart export can be read", "FAIL", str(e))]
    tags = core.load_tag_lookup(str(REPO / "expected_tags.xlsx"))
    rows, review, checks, fills = draftm.build(docs, brand_cfg, cat, hist, tags, cart=cart)
    checks = pre + checks
    with tempfile.TemporaryDirectory(prefix="oc-draft-") as tmp:
        out = os.path.join(tmp, "PO-draft.xlsx")
        poxlsx.write_po(out, poxlsx.PO_HEADER, rows, review, checks, fills, currency=cost_currency(brand_cfg))
        data = Path(out).read_bytes()
    return (None if blocking(checks) else data), pd.DataFrame(review), checks


# ---- step 2 --------------------------------------------------------------------
def finalize(po_path, catalog_path, brand_cfg, when=None):
    """Checked PO -> (zip of the upload and delivery files | None, checks, PU-new gate errors).

    All or nothing: if any check FAILs there is no zip at all. `when` names the run: pass the
    same value to zip_name(file_prefix(brand_cfg, when)) so the zip carries the run id too."""
    prefix = file_prefix(brand_cfg, when)
    with tempfile.TemporaryDirectory(prefix="oc-finalize-") as tmp:
        try:
            cat = catm.load(catalog_path)
        except catm.CatalogError as e:
            return None, [_check("Catalog export has the columns the tool needs", "FAIL", str(e))], pd.DataFrame()
        try:
            checks = finalizem.run(po_path, brand_cfg, cat, os.path.join(tmp, prefix))
        except Exception as e:
            return None, [_check("The PO can be read", "FAIL", f"{type(e).__name__}: {e}")], pd.DataFrame()
        checks = [dict(c, Detail=str(c["Detail"]).replace(tmp + os.sep, "")) for c in checks]
        report = os.path.join(tmp, prefix + "PU-new-VALIDATION_ERRORS_REPORT.xlsx")
        errors = pd.read_excel(report) if os.path.exists(report) else pd.DataFrame()
        if fails(checks):
            return None, checks, errors
        finalizem.save_checks(os.path.join(tmp, prefix + "finalize-checks.xlsx"), checks, prefix)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ZIP_MEMBERS:
                p = os.path.join(tmp, prefix + name)
                if os.path.exists(p):
                    z.write(p, prefix + name)
        return buf.getvalue(), checks, errors
