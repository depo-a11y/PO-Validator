"""Spreadsheet order confirmations (.xlsx, .xls, .csv), read by the brand config's column mapping.

Some brands confirm an order as a spreadsheet: one exports an ".xls" that is an HTML table (not BIFF) and
repeats its header row on every printed page, NuORDER brands send an .xlsx with sizes in (size, qty) column
pairs, others a real BIFF .xls or a .csv. One reader serves them all; the brand config's `sheet:` block says
which printed header holds what (`check_mapping` validates it when the config loads):

    adapter: sheet
    join_key: "{style} {colour}"
    sheet:
      columns:                      # header text as printed; case and spacing are ignored
        style: Model                # required
        colour_code: Color ID       # colour_code and/or colour_name; join_key's {colour} is the code when mapped
        colour_name: Color Name
        size: Size                  # layout 1, one row per size: size and qty
        qty: Units
        price: Unit Cost            # required: the unit price in the OC's currency
        line_value: Total Cost      # optional: the printed value (see line_value_per)
        currency: Currency Cost     # or sheet.currency when no column carries it
        doc: Order Number           # optional: one confirmation per value, else one named after the file
        # optional: line_no, fabric, product_class, gender, collection, description, delivery, issuer, label,
        #           rrp, rrp_currency, ean, hs_code, origin
      size_columns: [XS, S, M]      # layout 2, a size matrix: one column per size, headed by the size
      size_pairs:                   # layout 3: (size, qty[, size price]) column groups, as NuORDER exports
        - [Size 1, Qty 1, Size price 1]
      size_map: {"0": OS}           # optional: printed size -> store size
      currency: USD                 # the OC's currency when no column carries it
      rrp_currency: AUD             # the RRP's currency when no column carries it (else the OC's currency)
      decimal: ","                  # "12,50" and "1.234,50" (default "." for "12.50" and "1,234.50"); every
                                    # money and quantity cell must fit it, so a file whose format changed fails
      line_value_per: row           # layout 1: row (each size row prints its own value) or line (every row of
                                    # a line repeats the line's subtotal, which is then taken once, never summed)
      sheet_name: Order             # optional: the worksheet (default the first that has the header row)
      totals: {label: Total, qty: Units, value: Total Cost}       # a printed totals row, found by its label
      totals: {qty_cell: "Summary!B7", value_cell: "Summary!B8"}  # or printed totals cells
      totals: none                  # or: the brand prints no totals (declared, so the Checks can say so)
      ean_prefixes: ["0000000"]     # optional: the brand's GS1 company prefix(es); every EAN must start with one
                                    # (an identity check for a file that names no brand)

The header row is the first row that holds every mapped header; a later row that repeats it (a page break in an
export) is skipped. A mapped header missing from the sheet, or a mapping key the reader does not know, is a FAIL
naming it.

Gates, as the PDF readers: each line's sizes sum to its qty (a mapped line-qty column in layouts 2 and 3); qty x
unit price = the printed line value, checked per row or per line as `line_value_per` says, so a money column
that repeats a group subtotal on every size row is never summed; the lines reproduce the printed totals. Totals,
line values or line quantities the mapping does not read from the file are recomputed from the lines, and each
document says which in `recomputed`, shown as a Checks row: such a gate had nothing printed to check against.
Every money and quantity cell must fit the declared number format; one that does not, or that reads two ways
("1.234" under decimal ","), fails naming the cell.

An RRP is taken only from a column whose header util.rrp_header accepts, in the currency its column, the
mapping or the document gives; the draft uses it as a retail price only when that currency is AUD.
EAN, HS code and origin are kept per size (`ean`, `hs_code`, `origin`: {size: value}).
"""
import csv
import html.parser
import io
import math
import os
import re
import warnings

from .otb import NOT_ADDING_UP, GateError, collection_gender, gate_problems, totals  # noqa: F401  (re-exported)
from ..util import iso_currency, rrp_header

COLUMN_KEYS = ("style", "colour_code", "colour_name", "fabric", "size", "qty", "price", "line_value", "currency",
               "doc", "line_no", "product_class", "gender", "collection", "description", "delivery", "issuer",
               "label", "rrp", "rrp_currency", "ean", "hs_code", "origin")
SHEET_KEYS = ("columns", "size_columns", "size_pairs", "size_map", "currency", "rrp_currency", "decimal",
              "line_value_per", "sheet_name", "totals", "ean_prefixes")
TOTALS_ROW_KEYS = ("label", "qty", "value")
TOTALS_CELL_KEYS = ("qty_cell", "value_cell")
MONEY_TOL = 0.02
TOTALS_NONE = "totals (the brand config says the brand prints none)"
TOTALS_UNMAPPED = "totals (the brand config maps no printed totals)"
LINE_VALUES = "line values (the brand config maps no line value column)"
LINE_QTY = "line quantities (none printed: each is the sum of its size cells, so the sizes-sum gate checked nothing)"
LAYOUT_HINT = ("If this is the right brand and file, its layout no longer matches the brand config's sheet: "
               "mapping; ask Colwyn to update the mapping.")
CELL = re.compile(r"^(?:(.+)!)?\$?([A-Za-z]{1,3})\$?(\d+)$")


class Uncached:
    """An .xlsx formula cell with no saved value: Excel never calculated it, so the sheet prints nothing there."""

    def __repr__(self):
        return "<formula with no saved value>"


UNCACHED = Uncached()


class FormatError(ValueError):
    """A cell that does not read under the brand config's number format (or reads two ways under it)."""


def norm(text):
    return " ".join(str(text if text is not None else "").split()).casefold()


def blank(v):
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and not v.strip())


# ---- the mapping -----------------------------------------------------------------
def check_mapping(sheet, cfg):
    """[] when the brand config's `sheet:` mapping is usable, else what is wrong with it."""
    if not isinstance(sheet, dict):
        return ["adapter: sheet needs a sheet: block mapping the OC's column headers (see oc/adapters/sheet.py)"]
    out = [f"sheet: unknown key {k!r} (known: {', '.join(SHEET_KEYS)})" for k in sheet if k not in SHEET_KEYS]
    cols = sheet.get("columns")
    if not isinstance(cols, dict):
        return out + ["sheet: columns: maps each field to its printed header, e.g. style: Model"]
    out += [f"sheet: columns: unknown key {k!r} (known: {', '.join(COLUMN_KEYS)})" for k in cols if k not in COLUMN_KEYS]
    out += [f"sheet: columns: {k} is a header text, got {v!r}" for k, v in cols.items()
            if not isinstance(v, str) or not v.strip()]
    if "style" not in cols:
        out.append("sheet: columns: needs style")
    if "price" not in cols:
        out.append("sheet: columns: needs price (the unit price)")
    if not ({"colour_code", "colour_name"} & set(cols)):
        out.append("sheet: columns: needs colour_code or colour_name")
    layouts = [n for n, on in (("columns: size", "size" in cols), ("size_columns", "size_columns" in sheet),
                               ("size_pairs", "size_pairs" in sheet)) if on]
    if len(layouts) != 1:
        out.append("sheet: give exactly one size layout: columns: size (one row per size, with columns: qty), "
                   f"size_columns (one column per size) or size_pairs; got {', '.join(layouts) or 'none'}")
    if "size" in cols and "qty" not in cols:
        out.append("sheet: columns: size needs columns: qty")
    sc = sheet.get("size_columns")
    if "size_columns" in sheet and (not isinstance(sc, list) or not sc or not all(isinstance(x, str) and x.strip()
                                                                                    for x in sc)):
        out.append(f"sheet: size_columns is a list of size headers, got {sc!r}")
    sp = sheet.get("size_pairs")
    if "size_pairs" in sheet and (not isinstance(sp, list) or not sp or not all(
            isinstance(p, list) and len(p) in (2, 3) and all(isinstance(x, str) and x.strip() for x in p) for p in sp)):
        out.append(f"sheet: size_pairs is a list of [size header, qty header] or [size, qty, size price], got {sp!r}")
    ep = sheet.get("ean_prefixes")
    if ep is not None and (not isinstance(ep, list) or not ep or not all(isinstance(x, str) and x.isdigit() for x in ep)):
        out.append(f"sheet: ean_prefixes is a list of GS1 company prefixes as quoted digits, got {ep!r}")
    if ep is not None and "ean" not in cols:
        out.append("sheet: ean_prefixes is set but columns: ean is not mapped")
    if "ean" in cols and "size" not in cols:
        out.append("sheet: columns: ean needs one row per size (columns: size): an EAN belongs to one size")
    if bool(cols.get("currency")) == bool(sheet.get("currency")):
        out.append("sheet: give the OC's currency once: columns: currency (a column) or currency: (e.g. EUR)")
    for key in ("currency", "rrp_currency"):
        if sheet.get(key) and not iso_currency(sheet[key]):
            out.append(f"sheet: {key} {sheet[key]!r} is not a currency code (e.g. EUR, USD, AUD)")
    if "rrp" in cols and isinstance(cols["rrp"], str) and not rrp_header(cols["rrp"].split()):
        out.append(f"sheet: columns: rrp {cols['rrp']!r} is not an RRP column header (RRP, MSRP, SRP, retail, ...): "
                   "an RRP is read only from a column printed as one")
    if ("rrp_currency" in cols or sheet.get("rrp_currency")) and "rrp" not in cols:
        out.append("sheet: an RRP currency is given but columns: rrp is not mapped")
    if sheet.get("decimal", ".") not in (".", ","):
        out.append(f"sheet: decimal is '.' or ',', got {sheet['decimal']!r}")
    per = sheet.get("line_value_per")
    if per is not None and per not in ("row", "line"):
        out.append(f"sheet: line_value_per is row or line, got {per!r}")
    if per == "row" and "size" not in cols:
        out.append("sheet: line_value_per: row needs one row per size (columns: size)")
    if per is not None and "line_value" not in cols:
        out.append("sheet: line_value_per is set but columns: line_value is not mapped")
    sm = sheet.get("size_map")
    if sm is not None and (not isinstance(sm, dict) or not all(isinstance(v, (str, int)) for v in sm.values())):
        out.append(f"sheet: size_map maps a printed size to the store's size, got {sm!r}")
    out += _check_totals(sheet.get("totals"))
    tpl = cfg.get("join_key") or ""
    if "{fabric}" in tpl and "fabric" not in cols:
        out.append("join_key uses {fabric} but sheet: columns: fabric is not mapped")
    for key, col in (("oc_supplier", "issuer"), ("oc_label", "label")):
        if cfg.get(key) and col not in cols:
            out.append(f"{key} is set but sheet: columns: {col} is not mapped: a spreadsheet prints no issuer or "
                       f"label the reader can find otherwise, so map the column that names it or remove {key}")
    return out


def _check_totals(t):
    if t is None or t == "none":
        return []
    if not isinstance(t, dict):
        return [f"sheet: totals is a mapping or none, got {t!r}"]
    keys = set(t)
    if keys == set(TOTALS_ROW_KEYS):
        return [f"sheet: totals: {k} is a text, got {t[k]!r}" for k in TOTALS_ROW_KEYS
                if not isinstance(t[k], str) or not t[k].strip()]
    if keys == set(TOTALS_CELL_KEYS):
        return [f"sheet: totals: {k} is a cell like B7 or Summary!B7, got {t[k]!r}" for k in TOTALS_CELL_KEYS
                if not isinstance(t[k], str) or not CELL.match(t[k].strip())]
    return [f"sheet: totals is {{label, qty, value}} (a totals row found by its label; qty and value are "
            f"column headers), {{qty_cell, value_cell}}, or none (the brand prints no totals), got keys {sorted(keys)}"]


# ---- reading the file ----------------------------------------------------------
class _Html(html.parser.HTMLParser):
    """Every <tr> of every table in document order, cells' text with whitespace collapsed; colspan keeps columns."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell, self.span = [], None, None, 1

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._close_row()
            self.row = []
        elif tag in ("td", "th"):
            self._close_cell()
            if self.row is None:
                self.row = []
            self.cell = []
            span = dict(attrs).get("colspan") or "1"
            self.span = min(int(span), 200) if span.isdigit() and int(span) > 0 else 1
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._close_cell()
        elif tag in ("tr", "table"):
            self._close_row()

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def _close_cell(self):
        if self.cell is not None:
            text = " ".join("".join(self.cell).split())
            self.row.extend([text] + [""] * (self.span - 1))
            self.cell = None

    def _close_row(self):
        self._close_cell()
        if self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def close(self):
        super().close()
        self._close_row()


def _text(data):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _html(data):
    p = _Html()
    p.feed(_text(data))
    p.close()
    return [("table", p.rows)]


def _csv(data):
    text = _text(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [("csv", [row for row in csv.reader(io.StringIO(text), dialect)])]


def _biff(data):
    import xlrd
    book = xlrd.open_workbook(file_contents=data)
    out = []
    for sh in book.sheets():
        rows = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                row.append(None if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR)
                           else cell.value)
            rows.append(row)
        out.append((sh.name, rows))
    return out


def _xlsx(data):
    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)   # openpyxl drops pictures and shapes; the cells are what count
        values = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        formulas = openpyxl.load_workbook(io.BytesIO(data), data_only=False)
    out = []
    for ws in values.worksheets:
        fws = formulas[ws.title]
        rows = []
        for vrow, frow in zip(ws.iter_rows(), fws.iter_rows()):
            rows.append([UNCACHED if v.value is None and f.data_type == "f" else v.value for v, f in zip(vrow, frow)])
        out.append((ws.title, rows))
    return out


BIFF_RAW = (b"\x09\x08", b"\x09\x00", b"\x09\x02", b"\x09\x04")


def read_sheets(path):
    """[(sheet name, rows)] for an .xlsx, a BIFF or HTML-table .xls, or a .csv, sniffed from the bytes (an .xls
    export is often HTML). Raises ValueError for anything else."""
    data = open(path, "rb").read()
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if data[:4] == b"PK\x03\x04":
        return _xlsx(data)
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" or data[:2] in BIFF_RAW:
        return _biff(data)
    if head.startswith(b"<") and re.search(rb"<(table|tr|html)\b", data[:65536], re.I):
        return _html(data)
    if os.path.splitext(path)[1].lower() in (".csv", ".txt", ".tsv") or path.lower().endswith(".csv"):
        return _csv(data)
    if head.startswith(b"%pdf"):
        raise ValueError("this is a PDF, not a spreadsheet: this brand's reader (adapter: sheet) reads the OC's "
                         ".xls, .xlsx or .csv")
    raise ValueError("not a spreadsheet this reader knows (.xlsx, a BIFF or HTML-table .xls, or .csv)")


# ---- numbers -------------------------------------------------------------------
def number(v, decimal):
    """A cell as a float, None when blank. Text must fit the declared format: decimal ',' takes '12,50' and
    '1.234,50', decimal '.' takes '12.50' and '1,234.50'. A separator that contradicts it ('12.50' under ',') or a
    cell that reads two ways under either format ('1.234' or '1,234': 1234 or 1.234) is a ValueError, never a guess."""
    if blank(v):
        return None
    if isinstance(v, Uncached):
        raise ValueError("a formula with no saved value (Excel never calculated it)")
    if isinstance(v, bool):
        raise ValueError(f"{v!r} is not a number")
    if isinstance(v, (int, float)):
        return float(v)
    t = re.sub(r"^[A-Za-z]{3}(?=[\s\d€$£¥-])|(?<=[\d\s])[A-Za-z]{3}$", "", str(v).strip())
    t = re.sub(r"[\s €$£¥']", "", t)
    if not re.fullmatch(r"-?\d[\d.,]*", t):
        raise ValueError(f"{v!r} is not a number")
    two = re.fullmatch(r"(-?\d{1,3})[.,](\d{3})", t)
    if two:   # '1.234' is 1234 grouped or 1.234 with a decimal point; the config cannot tell a changed format
        raise FormatError(f"{v!r} reads two ways ({two[1]}{two[2]} or {two[1]}.{two[2]}), so the brand config's "
                         f"decimal mark {decimal!r} cannot settle it")
    group = "," if decimal == "." else "."
    d, g = re.escape(decimal), re.escape(group)
    if re.fullmatch(rf"-?\d+({d}\d+)?", t):
        return float(t.replace(decimal, "."))
    if re.fullmatch(rf"-?\d{{1,3}}({g}\d{{3}})+({d}\d+)?", t):
        return float(t.replace(group, "").replace(decimal, "."))
    raise FormatError(f"{v!r} does not fit the brand config's decimal mark {decimal!r}: has the OC's number format "
                     "changed? Check the file, then ask Colwyn to update sheet: decimal")


def ident(v):
    """An identifier cell (EAN, HS code) as text: a number keeps no '.0' tail."""
    if blank(v) or isinstance(v, Uncached):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def text(v):
    return None if blank(v) or isinstance(v, Uncached) else " ".join(str(v).split())


# ---- the reader ----------------------------------------------------------------
def _headers(sheet):
    """Every header the mapping names, as printed in the config."""
    cols = sheet["columns"]
    names = list(cols.values()) + list(sheet.get("size_columns") or [])
    names += [h for p in sheet.get("size_pairs") or [] for h in p]
    t = sheet.get("totals") if isinstance(sheet.get("totals"), dict) else {}
    names += [t[k] for k in ("qty", "value") if "label" in t]
    return list(dict.fromkeys(names))


def _find_header(sheets, sheet):
    """(sheet name, rows, header row index, {normalised header: column}) or a problem naming what is missing."""
    want = {norm(h): h for h in _headers(sheet)}
    if sheet.get("sheet_name"):
        hit = [s for s in sheets if norm(s[0]) == norm(sheet["sheet_name"])]
        if not hit:
            return f"no worksheet named {sheet['sheet_name']!r} (the file has {', '.join(n for n, _ in sheets)})"
        sheets = hit
    best = None
    for name, rows in sheets:
        for i, row in enumerate(rows):
            got = {}
            for j, v in enumerate(row):
                if not isinstance(v, Uncached) and norm(v) in want:
                    got.setdefault(norm(v), []).append(j)
            if len(got) == len(want):
                twice = [want[k] for k, js in got.items() if len(js) > 1]
                if twice:
                    return f"{name} row {i + 1}: header(s) {', '.join(map(repr, twice))} printed in more than one column"
                return name, rows, i, {k: js[0] for k, js in got.items()}
            if got and (best is None or len(got) > best[0]):
                best = (len(got), name, i, [want[k] for k in want if k not in got])
    if best is None:
        return "no row of the sheet holds any of the mapped headers: " + ", ".join(map(repr, want.values()))
    return (f"no header row holds every mapped column: missing {', '.join(map(repr, best[3]))} "
            f"(closest: {best[1]} row {best[2] + 1})")


def parse(path, brand_cfg):
    """Read a spreadsheet OC by the brand config's sheet: mapping. Returns (docs, problems)."""
    sheet = (brand_cfg or {}).get("sheet")
    bad = check_mapping(sheet, brand_cfg or {})
    if bad:
        return [], bad
    try:
        sheets = read_sheets(path)
    except Exception as e:
        return [], [f"{os.path.basename(path)}: {e}" if isinstance(e, ValueError) else
                    f"{os.path.basename(path)} cannot be read as a spreadsheet: {type(e).__name__}: {e}"]
    found = _find_header(sheets, sheet)
    if isinstance(found, str):
        return [], [found]
    name, rows, h, pos = found
    return Reader(path, sheet, sheets, name, rows, h, pos).run()


class Reader:
    def __init__(self, path, sheet, sheets, name, rows, h, pos):
        self.path, self.sheet, self.sheets, self.name, self.rows, self.h, self.pos = \
            path, sheet, sheets, name, rows, h, pos
        self.cols = sheet["columns"]
        self.dec = sheet.get("decimal", ".")
        self.size_map = {norm(k): str(v) for k, v in (sheet.get("size_map") or {}).items()}
        self.problems, self.format_errors = [], []
        self.header = [norm(v) for v in rows[h]]

    def col(self, key):
        return self.pos[norm(self.cols[key])] if key in self.cols else None

    def cell(self, row, key=None, header=None):
        j = self.pos[norm(header)] if header is not None else self.col(key)
        return row[j] if j is not None and j < len(row) else None

    def num(self, r, row, key=None, header=None):
        head = header if header is not None else self.cols[key]
        v = self.cell(row, key, header)
        try:
            return number(v, self.dec)
        except FormatError:
            where = f"{self.name} row {r + 1} column {head!r}: {v!r}"
            if where not in self.format_errors:   # order lines were scanned up front; a totals row is not
                self.format_errors.append(where)
        except ValueError as e:
            self.problems.append(f"{self.name} row {r + 1} column {head!r}: {e}")
        return None

    def qty(self, r, row, key=None, header=None):
        q = self.num(r, row, key, header)
        if q is None:
            return 0
        if q < 0 or abs(q - round(q)) > 1e-9:
            head = header if header is not None else self.cols[key]
            self.problems.append(f"{self.name} row {r + 1} column {head!r}: quantity {q:g} is not a whole number")
            return 0
        return int(round(q))

    def size(self, v):
        t = text(v) if not isinstance(v, (int, float)) or isinstance(v, bool) else ident(v)
        return self.size_map.get(norm(t), t) if t is not None else None

    def repeated_header(self, row):
        return all(j < len(row) and norm(row[j]) == self.header[j] for j in self.pos.values())

    # ---- rows -> lines -------------------------------------------------------------
    def run(self):
        t = self.sheet.get("totals") if isinstance(self.sheet.get("totals"), dict) else {}
        label = norm(t.get("label")) if "label" in t else None
        data, printed, repeats = [], None, 0
        for r in range(self.h + 1, len(self.rows)):
            row = self.rows[r]
            if self.repeated_header(row):
                repeats += 1
                continue
            if ident(self.cell(row, "style")) is None:
                if label is not None and any(norm(v) == label for v in row if not isinstance(v, Uncached)):
                    if printed is not None:
                        self.problems.append(f"{self.name} row {r + 1}: a second totals row labelled {t['label']!r}")
                    printed = (self.num(r, row, header=t["qty"]), self.num(r, row, header=t["value"]), r)
                    continue
                if any(not blank(row[j]) for j in self._number_cols() if j < len(row)):
                    self.problems.append(f"{self.name} row {r + 1} has quantities or prices but no style "
                                         f"({self.cols['style']!r}): a subtotal row? Map it as totals or remove it")
                continue
            data.append(r)
        self._format_scan(data)
        if "qty_cell" in t:
            printed = self._printed_cells(t)
        docs = self._docs(data)
        if not docs and not self.problems:
            self.problems.append(f"{self.name}: no order lines under the header row {self.h + 1}")
        self._totals(docs, printed)
        for d in docs:
            d["header_repeats"] = repeats
            if "issuer" in self.cols and d["issuer"] is None:
                d["issuer"] = ""   # mapped but blank: an issuer the sheet does not print, never the PDF fallback
            self._anchor(d)
        if self.format_errors:   # every other problem may only echo these cells, so they stand alone
            n = len(self.format_errors)
            return [], [f"{n} money or quantity cell(s) do not read under the brand config's number format (decimal "
                          f"mark {self.dec!r}): " + "; ".join(self.format_errors[:4])
                          + (f"; and {n - 4} more" if n > 4 else "") + ". A separator that contradicts the format, or "
                          "a cell that reads two ways (1.234), is never guessed: has the OC's number format changed? "
                          "Check the file, then ask Colwyn to update sheet: decimal"]
        return docs, self.problems + gate_problems(docs)

    def _anchor(self, d):
        """The brand's GS1 prefixes against every EAN of the document: d["identity"] says what was checked, and an
        EAN under another prefix is an identity problem (the service shows it as one)."""
        prefixes = self.sheet.get("ean_prefixes")
        if not prefixes:
            return
        eans = [(l["line_no"], s, e) for l in d["lines"] for s, e in (l.get("ean") or {}).items()]
        off = [f"{d['doc']} line {n} size {s}: EAN {e} starts with none of the brand's GS1 prefixes "
               f"({', '.join(prefixes)})" for n, s, e in eans if not e.startswith(tuple(prefixes))]
        d["identity_problems"] = off[:5] + ([f"{len(off) - 5} more EANs like these"] if len(off) > 5 else [])
        if eans and not off:
            d["identity"] = f"all {len(eans)} EANs carry the brand's GS1 prefix ({', '.join(prefixes)})"

    def _format_scan(self, data):
        """Every mapped money and quantity cell of every order line against the number format, before any is used."""
        heads = [self.cols[k] for k in ("qty", "price", "line_value", "rrp") if k in self.cols]
        heads += list(self.sheet.get("size_columns") or [])
        heads += [h for p in self.sheet.get("size_pairs") or [] for h in p[1:]]
        for r in data:
            for h in heads:
                v = self.cell(self.rows[r], header=h)
                try:
                    number(v, self.dec)
                except FormatError:
                    self.format_errors.append(f"{self.name} row {r + 1} column {h!r}: {v!r}")
                except ValueError:
                    pass   # not a number at all: reported where the cell is read

    def _number_cols(self):
        keys = [k for k in ("qty", "price", "line_value") if k in self.cols]
        out = [self.col(k) for k in keys] + [self.pos[norm(h)] for h in self.sheet.get("size_columns") or []]
        return out + [self.pos[norm(p[1])] for p in self.sheet.get("size_pairs") or []]

    def _printed_cells(self, t):
        vals = []
        for key in TOTALS_CELL_KEYS:
            m = CELL.match(t[key].strip())
            name, letters, n = m.group(1), m.group(2).upper(), int(m.group(3))
            rows = self.rows if name is None else next((rs for s, rs in self.sheets if norm(s) == norm(name)), None)
            if rows is None:
                self.problems.append(f"totals: {key} {t[key]!r}: no worksheet named {name!r}")
                return None
            j = 0
            for ch in letters:
                j = j * 26 + ord(ch) - 64
            v = rows[n - 1][j - 1] if n - 1 < len(rows) and j - 1 < len(rows[n - 1]) else None
            try:
                got = number(v, self.dec)
            except ValueError as e:
                self.problems.append(f"totals: {key} {t[key]!r}: {e}")
                return None
            if got is None:
                self.problems.append(f"totals: {key} {t[key]!r} is empty")
                return None
            vals.append(got)
        return vals[0], vals[1], None

    def _docs(self, data):
        docs = {}
        stem = os.path.splitext(os.path.basename(self.path))[0]
        layout = "rows" if "size" in self.cols else "matrix"
        groups = {}
        for r in data:
            row = self.rows[r]
            doc = ident(self.cell(row, "doc")) if "doc" in self.cols else stem
            if doc is None:
                self.problems.append(f"{self.name} row {r + 1}: no {self.cols['doc']!r}")
                continue
            d = docs.setdefault(doc, self._new_doc(doc))
            self._doc_fields(d, r, row)
            key = (doc, ident(self.cell(row, "style")), ident(self.cell(row, "fabric")),
                   ident(self.cell(row, "colour_code")), text(self.cell(row, "colour_name")))
            if layout == "rows":
                groups.setdefault(key, []).append(r)
            else:
                groups[key + (r,)] = [r]
        n_by_doc = {}
        for key, rs in groups.items():
            d = docs[key[0]]
            n_by_doc[key[0]] = n_by_doc.get(key[0], 0) + 1
            line = self._line(d, key, rs, n_by_doc[key[0]], layout)
            if line is not None:
                d["lines"].append(line)
        return list(docs.values())

    def _new_doc(self, doc):
        return dict(doc=doc, date=None, lines=[], collection=None, pages=[], currency=self.sheet.get("currency"),
                    total_qty=None, net=None, gross=None, issuer=None, label=None, gender=None, recomputed=[],
                    notes=[], identity=None, identity_problems=[], sheet=self.name, rows=[])

    def _doc_fields(self, d, r, row):
        d["rows"].append(r + 1)
        for key, field in (("currency", "currency"), ("issuer", "issuer"), ("label", "label"),
                           ("collection", "collection")):
            if key not in self.cols:
                continue
            v = text(self.cell(row, key))
            if d[field] is None:
                d[field] = v
            elif v is not None and norm(v) != norm(d[field]) and field != "collection":
                self.problems.append(f"{d['doc']} row {r + 1}: {self.cols[key]!r} is {v!r}, but earlier rows of "
                                     f"this confirmation say {d[field]!r}")

    def _line(self, d, key, rs, n, layout):
        doc, style, fabric, code, name = key[:5]
        first = self.rows[rs[0]]
        tag = f"{doc} {style} {code or name}"
        prices = [(r, self.num(r, self.rows[r], "price")) for r in rs]
        price_set = sorted({p for _, p in prices if p is not None})
        unpriced = [r + 1 for r, p in prices if p is None]
        if unpriced and price_set:
            self.problems.append(f"{tag}: row(s) {', '.join(map(str, unpriced))} have no unit price in "
                                 f"{self.cols['price']!r}")
            return None
        if len(price_set) != 1:
            self.problems.append(f"{tag} (rows {', '.join(str(r + 1) for r in rs)}): " +
                                 (f"sizes priced differently ({', '.join(f'{p:g}' for p in price_set)}); the PO "
                                  "takes one unit price per line" if price_set else "no unit price"))
            return None
        price = price_set[0]
        line_no = self._line_no(d, rs, n)
        tag = f"{doc} line {line_no} {style}"
        sizes, per_size = {}, dict(ean={}, hs_code={}, origin={})
        if layout == "rows":
            qs = {r: self.qty(r, self.rows[r], "qty") for r in rs}
            line_value = self._rows_value(tag, rs, price, qs)
            for r in rs:
                row = self.rows[r]
                s, q = self.size(self.cell(row, "size")), qs[r]
                if s is None:
                    if q:
                        self.problems.append(f"{tag} row {r + 1}: quantity {q} with no size")
                    continue
                if s in sizes:
                    self.problems.append(f"{tag} row {r + 1}: size {s} twice on this line (a repeated page?)")
                    continue
                sizes[s] = q
                for k in per_size:
                    if k in self.cols:
                        v = ident(self.cell(row, k)) if k != "origin" else text(self.cell(row, k))
                        if v is not None:
                            per_size[k][s] = v
            qty = sum(sizes.values())
            self._recomputed(d, LINE_QTY)
        else:
            r = rs[0]
            for s, q, p in self._matrix(r, first):
                if q and p is not None and abs(p - price) > 0.005:
                    self.problems.append(f"{tag} row {r + 1}: size {s} is priced {p:g}, the line {price:g}")
                if s in sizes and q:
                    self.problems.append(f"{tag} row {r + 1}: size {s} twice on this line")
                    continue
                sizes[s] = sizes.get(s, 0) + q
            qty = self.qty(r, first, "qty") if "qty" in self.cols else sum(sizes.values())
            if "qty" not in self.cols:
                self._recomputed(d, LINE_QTY)
            for k in ("hs_code", "origin"):
                if k in self.cols:
                    v = ident(self.cell(first, k)) if k == "hs_code" else text(self.cell(first, k))
                    if v is not None:
                        per_size[k] = {s: v for s, q in sizes.items() if q}
            line_value = self.num(r, first, "line_value") if "line_value" in self.cols else None
            if "line_value" in self.cols and line_value is None:
                self.problems.append(f"{tag} row {r + 1}: no line value in {self.cols['line_value']!r}")
        sizes = {s: q for s, q in sizes.items() if q}
        if not sizes and not qty:
            return None
        if "line_value" not in self.cols:
            line_value = round(qty * price, 2)
            self._recomputed(d, LINE_VALUES)
        rrp, rrp_cur, rrp_note = self._rrp(tag, rs, d)
        gender_text = text(self.cell(first, "gender"))
        delivery = text(self.cell(first, "delivery"))
        return dict(line_no=line_no, style=style, fabric=fabric, colour=code if code is not None else name,
                    colour_code=code, colour_name=name, sizes=sizes, qty=qty, price=price,
                    line_value=line_value if line_value is not None else round(qty * price, 2),
                    product_class=text(self.cell(first, "product_class")),
                    gender=collection_gender(gender_text) if gender_text else None,
                    description=text(self.cell(first, "description")), deliver_from=delivery, deliver_to=None,
                    rrp=rrp, rrp_currency=rrp_cur, rrp_found=f"column {self.cols['rrp']!r}" if rrp else None,
                    rrp_note=rrp_note, rows=[r + 1 for r in rs],
                    **{k: v for k, v in per_size.items() if k in self.cols})

    @staticmethod
    def _recomputed(d, what):
        if what not in d["recomputed"]:
            d["recomputed"].append(what)

    def _line_no(self, d, rs, n):
        if "line_no" not in self.cols:
            return n
        got = [text(self.cell(self.rows[r], "line_no")) for r in rs]
        nums = [int(float(g)) for g in got if g is not None and re.fullmatch(r"\d+(\.0+)?", g)]
        return min(nums) if nums else (got[0] or n)

    def _rows_value(self, tag, rs, price, qs):
        """The printed value of a line laid out one row per size, gated per row or per line, never a sum of a
        repeated subtotal. None when the mapping has no line value column."""
        if "line_value" not in self.cols:
            return None
        per = self.sheet.get("line_value_per") or "row"
        vals = []
        for r in rs:
            v, q = self.num(r, self.rows[r], "line_value"), qs[r]
            if v is None:
                self.problems.append(f"{tag} row {r + 1}: no line value in {self.cols['line_value']!r}")
                return None
            if per == "row" and abs(q * price - v) > MONEY_TOL:
                self.problems.append(f"{tag} row {r + 1}: {q} x {price:g} = {q * price:.2f} != line value {v:g}")
            vals.append((q, v))
        if per == "row":
            return round(sum(v for _, v in vals), 2)
        if len({round(v, 2) for _, v in vals}) != 1:
            self.problems.append(f"{tag}: the rows print different line values ({', '.join(f'{v:g}' for _, v in vals)}), "
                                 "but line_value_per: line says each repeats the line's subtotal")
            return None
        return vals[0][1]   # taken once; gate_problems checks it against the line's qty x price

    def _matrix(self, r, row):
        """(size, qty, size price | None) for each size cell of a matrix or pair row."""
        out = []
        for h in self.sheet.get("size_columns") or []:
            out.append((self.size(h), self.qty(r, row, header=h), None))
        for p in self.sheet.get("size_pairs") or []:
            s, q = self.size(self.cell(row, header=p[0])), self.qty(r, row, header=p[1])
            price = self.num(r, row, header=p[2]) if len(p) == 3 else None
            if s is None:
                if q:
                    self.problems.append(f"{self.name} row {r + 1}: quantity {q} in {p[1]!r} with no size in {p[0]!r}")
                continue
            out.append((s, q, price))
        return out

    def _rrp(self, tag, rs, d):
        """(rrp, currency, note) for a line: one RRP for all its sizes, in its column's, the mapping's or the
        document's currency."""
        if "rrp" not in self.cols:
            return None, None, None
        vals, curs = set(), set()
        for r in rs:
            row = self.rows[r]
            v = self.num(r, row, "rrp")
            if v:
                vals.add(round(v, 2))
                cur = text(self.cell(row, "rrp_currency")) if "rrp_currency" in self.cols else None
                curs.add(cur or self.sheet.get("rrp_currency") or d.get("currency"))
        if not vals:
            return None, None, None
        if len(vals) > 1 or len(curs) > 1:
            note = (f"the sheet's RRP differs by size on {tag} ({', '.join(f'{v:g}' for v in sorted(vals))}"
                    f"{' in ' + ', '.join(sorted(map(str, curs))) if len(curs) > 1 else ''}); not used, so its retail "
                    "price is left blank for a person to type")
            d["notes"].append(note)
            return None, None, note
        return vals.pop(), curs.pop(), None

    # ---- totals ----------------------------------------------------------------
    def _totals(self, docs, printed):
        for d in docs:
            d["total_qty"] = sum(sum(l["sizes"].values()) for l in d["lines"])
            d["net"] = round(sum(sum(l["sizes"].values()) * l["price"] for l in d["lines"]), 2)
        if printed is None:
            for d in docs:
                d["recomputed"].insert(0, TOTALS_NONE if self.sheet.get("totals") == "none" else TOTALS_UNMAPPED)
            return
        q, v, r = printed
        where = f"{self.name} row {r + 1}" if r is not None else "totals cells"
        if q is None or v is None:
            self.problems.append(f"{where}: the totals row has no {'units' if q is None else 'value'}")
            return
        if len(docs) == 1:   # gate_problems compares the lines with these
            docs[0]["total_qty"], docs[0]["net"] = int(q) if float(q).is_integer() else q, v
            return
        units = sum(d["total_qty"] for d in docs)
        value = round(sum(d["net"] for d in docs), 2)
        tag = f"{len(docs)} confirmations together"
        if units != q:
            self.problems.append(f"{tag}: parsed {units} units, printed {q:g} ({where}). {NOT_ADDING_UP}")
        if abs(value - v) > MONEY_TOL:
            self.problems.append(f"{tag}: parsed {value:,.2f}, printed net {v:,.2f} ({where}). {NOT_ADDING_UP}")


def parse_checked(path, brand_cfg=None):
    docs, problems = parse(path, brand_cfg)
    if problems:
        raise GateError(problems)
    if not docs:
        raise GateError(["no order confirmations found"])
    return docs


def doc_label(d):
    """The label a confirmation is for: the mapped label column's value."""
    return d.get("label")


def reader_row(brand_cfg):
    cols = (brand_cfg.get("sheet") or {}).get("columns") or {}
    return (f"read by the built-in spreadsheet reader with this brand's column mapping (style: {cols.get('style')!r}, "
            f"price: {cols.get('price')!r}); no AI was used")
