"""Selling-session cart (linesheet) input: the brand's export of what was put in
the cart, one row per style-colour, e.g. OTB's '<BRAND> SS27 AVP X MARAIS, ID <session>.xlsx'.

What the cart is for: the descriptive fields the order confirmation lacks
(colour description, composition, made in, category, gender). What it is not
for: quantities and prices. The OC can amend both, so they always come from the
OC; the cart's Wholesale is read only to report where it differs. A nonzero
Sugg. Retail is the brand's RRP, in the cart's Currency: a NEW product's retail
price only when that currency is AUD (Colwyn, 2026-11-12). OTB carts carry 0.

Rows are keyed by the full join key `<Model><Fabric> <Color Code>`, never by the
colour code alone: a Margiela numeric colour code names a different colour on
every fabric.

load(path) -> dict:
    {"path", "sheet", "label", "session",
     "rows": {join_key: {code, row, style_name, model, fabric, colour_code, colour,
                         gender, gender_raw, collection, category, subcategory,
                         materials, made_in, made_in_raw, size_grid, available_sizes,
                         wholesale, currency, rrp, rrp_currency}},
     "duplicates": [join keys on more than one row (first row kept)]}
"""
import re
import warnings
from pathlib import Path

from .util import iso_currency, join_key, money, norm_size

REQUIRED = ["Model", "Fabric", "Color Code", "Color Description"]
OPTIONAL = ["Style Name", "Gender", "Collection", "Category", "Subcategory", "Materials", "Made In",
            "Size Grid", "Available Sizes", "Wholesale", "Currency", "Sugg. Retail"]
GENDER = {"FEMALE": "WOMEN", "WOMAN": "WOMEN", "WOMEN": "WOMEN", "MALE": "MEN", "MAN": "MEN", "MEN": "MEN"}
HEADER_SCAN = 50


class CartError(Exception):
    pass


def _text(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def half_size(size, whole):
    """OTB prints a half size run together: '355' is 35.5 when 35 is also a size of the same grid. Else norm_size."""
    t = str(size).strip()
    m = re.fullmatch(r"(\d{2})5", t)
    return f"{m.group(1)}.5" if m and m.group(1) in whole else norm_size(t)


def _sizes(v):
    raw = [s.strip() for s in _text(v).split(",") if s.strip()]
    return [half_size(s, set(raw)) for s in raw]


HOUSE_WORDS = {"Tshirt": "T-Shirt", "Tshirts": "T-Shirts"}


def style_title(name):
    """A cart Style Name as a title stem: 'TABI SANDAL PUMP H80' -> 'Tabi Sandal Pump H80' (the heel code stays as
    the brand prints it), 'INSIDE OUT TSHIRT' -> 'Inside Out T-Shirt', each part of a hyphenated word capitalised."""
    words = ["-".join(p.capitalize() for p in w.split("-")) for w in _text(name).split()]
    return " ".join(HOUSE_WORDS.get(w, w) for w in words)


def made_in_name(raw):
    """'Made In Italy' / 'MADE IN CHINA' -> 'Italy' / 'China'; '' when blank."""
    t = re.sub(r"(?i)^made\s+in\s+", "", _text(raw)).strip()
    return " ".join(w.capitalize() for w in t.split())


def _header(ws):
    """(row number, {column name: index}) of the first row holding every REQUIRED name."""
    best = (0, None, [])
    for r, row in enumerate(ws.iter_rows(max_row=HEADER_SCAN, values_only=True), 1):
        names = [_text(v) for v in row]
        hit = sum(n in names for n in REQUIRED)
        if hit == len(REQUIRED):
            col = {}
            for i, n in enumerate(names):
                if n and n not in col:
                    col[n] = i
            return r, col
        if hit > best[0]:
            best = (hit, r, [n for n in REQUIRED if n not in names])
    return None, best


def load(path):
    import openpyxl
    try:
        # read_only mode reports some cart exports as 1x1, so load them in full.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as e:
        raise CartError(f"cannot read {Path(path).name} as an .xlsx workbook: {e}") from e
    found, nearest = None, (0, None, list(REQUIRED), None)
    for ws in wb.worksheets:
        r, col = _header(ws)
        if r:
            found = (ws, r, col)
            break
        if col[0] > nearest[0]:
            nearest = (*col, ws.title)
    if not found:
        hit, row, missing, sheet = nearest
        where = f"; nearest header is sheet '{sheet}' row {row}" if sheet else ""
        raise CartError(f"{Path(path).name}: no cart header row with {REQUIRED} "
                        f"in the first {HEADER_SCAN} rows of any sheet (missing {missing}{where})")
    ws, hdr_row, col = found

    head = {}
    for row in ws.iter_rows(max_row=hdr_row - 1, values_only=True):
        if row and _text(row[0]):
            head[_text(row[0]).lower()] = _text(row[1]) if len(row) > 1 else ""
    session = head.get("session code", "")
    label = f"cart {session}" if session else f"cart {Path(path).name}"

    def g(row, name):
        i = col.get(name)
        return row[i] if i is not None and i < len(row) else None

    rows, dups = {}, []
    for r, row in enumerate(ws.iter_rows(min_row=hdr_row + 1, values_only=True), hdr_row + 1):
        model, colour_code = _text(g(row, "Model")), _text(g(row, "Color Code"))
        if not model or not colour_code:
            continue
        fabric = _text(g(row, "Fabric"))
        code = join_key(model, fabric, colour_code)
        if code in rows:
            dups.append(code)
            continue
        gender_raw = _text(g(row, "Gender"))
        made_raw = _text(g(row, "Made In"))
        rows[code] = dict(
            code=code, row=r, style_name=_text(g(row, "Style Name")), model=model, fabric=fabric,
            colour_code=colour_code, colour=_text(g(row, "Color Description")),
            gender=GENDER.get(gender_raw.upper()), gender_raw=gender_raw,
            collection=_text(g(row, "Collection")), category=_text(g(row, "Category")),
            subcategory=_text(g(row, "Subcategory")), materials=_text(g(row, "Materials")),
            made_in=made_in_name(made_raw), made_in_raw=made_raw,
            size_grid=_sizes(g(row, "Size Grid")), available_sizes=_sizes(g(row, "Available Sizes")),
            wholesale=money(g(row, "Wholesale")), currency=_text(g(row, "Currency")),
            rrp=money(g(row, "Sugg. Retail")) or None, rrp_currency=iso_currency(_text(g(row, "Currency"))))
    if not rows:
        raise CartError(f"{Path(path).name}: sheet '{ws.title}' has a cart header on row {hdr_row} "
                        "but no style-colour rows under it")
    return dict(path=str(path), sheet=ws.title, label=label, session=session, rows=rows,
                duplicates=sorted(set(dups)))
