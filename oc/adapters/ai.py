"""AI order-confirmation reader: any layout, read by Claude through the Anthropic Messages API.

The PDF goes to the API as a document block; the answer comes back as JSON held to
SCHEMA (structured outputs). The model transcribes, Python decides:

- Every number comes back as the text printed on the page (qty_text, price_text,
  line_value_text, total_qty_text, net_total_text) and is parsed here, strictly. The
  model never hands over a computed value, so qty x price = line value stays a test
  of the reading rather than a tautology.
- The docs it returns have the otb shape, and `otb.gate_problems` is the gate,
  unchanged: one gate for both readers.
- On top of the gates, an AI read must: print a quantity under a size on every
  line; be in the brand's cost currency; come from the brand's `oc_supplier`; give
  numbers that parse; and pass block grounding. Each line's style must be on the
  page the model names, after the previous line's, and its price, line value,
  fabric and colour must be in that line's part of the text (from its style to
  the next line's style); its size names in that part, the gap before it or the
  page's table header. Every page must belong to exactly one confirmation or be
  listed as a page with no order lines; no confirmation or line may be returned
  twice unless the PDF prints it twice. A PDF with no text layer is refused
  before anything is sent. Quantities and totals are too short to look up in the
  text: the gates check them by arithmetic.

What no gate catches: a quantity read under the wrong size. Sizes sum to the line,
lines to the document, and the codes are in the text either way. The Review sheet
marks every value from an AI read amber (an ordered size the live product does
not have red) and the page says to check every line against the PDF.

The API key comes from the environment or the app's secrets (ANTHROPIC_API_KEY) and is
never logged, stored or put in a message. `read` takes a `factory(key) -> client` so
tests run offline against a fake client.
"""
import base64
import json
import os
import re
import time
from pathlib import Path

from . import otb
from ..util import cost_currency, currency_off

MODEL = "claude-opus-5-5"
PRICE_USD_PER_MTOK = (4.0, 20.0)    # claude-opus-5-5 input, output (thinking is billed as output)
EFFORT = "high"
MAX_TOKENS = 64000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
KEY_NAME = "ANTHROPIC_API_KEY"
MAX_PAGES = 60

SETUP_CHECK = "AI reader is set up"
READ_CHECK = "AI reader could read the order confirmation"
SUPPLIER_CHECK = "Order confirmation is from this brand's supplier"
CURRENCY_CHECK = "Order confirmation is in the brand's cost currency"
SIZES_CHECK = "Order confirmation prints a quantity under each size"
NUMBERS_CHECK = "Every number the AI read is a printed amount the tool can parse"
GROUNDING_CHECK = "Each line's code, colour, price and value are in that line's part of the PDF, its size names near it"
PAGES_CHECK = "Every page and confirmation is read exactly once"
TEXT_CHECK = "The PDF has text to check the AI reading against"
KIND_CHECK = "Document is an order confirmation"
GATE_CHECK = "Order confirmation passes its gates (parsed = printed)"
READER_CHECK = "Order confirmation reader"

NO_KEY = f"The AI reader is not set up: the app has no {KEY_NAME} in its secrets. Ask Colwyn."
NO_SIZES = ("this document prints no sizes, e.g. a proforma; upload the order confirmation it refers to")
BUSY = "the AI service is busy or could not be reached; try again in a few minutes"
NOT_FOUND = "no order confirmations found"
NO_TEXT = "this PDF has no text to check the AI reading against (a scan?); ask Colwyn"
SHIPPED = ("invoice", "delivery_note")   # quantities shipped, not ordered: refused

KINDS = ["order_confirmation", "proforma_invoice", "invoice", "delivery_note", "other"]
_S = {"type": "string"}
_NS = {"type": ["string", "null"]}
_NI = {"type": ["integer", "null"]}
LINE_KEYS = ["line_no", "style", "fabric", "colour", "product_class", "description", "composition", "made_in",
             "deliver_from", "deliver_to", "sizes", "qty_text", "price_text", "line_value_text", "page"]
DOC_KEYS = ["number", "label", "date", "collection", "currency", "total_qty_text", "net_total_text",
            "gross_total_text", "pages", "lines"]
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["supplier", "document_kind", "has_size_breakdown", "pages_without_lines", "documents"],
    "properties": {
        "supplier": _S,
        "document_kind": {"type": "string", "enum": KINDS},
        "has_size_breakdown": {"type": "boolean"},
        "pages_without_lines": {"type": "array", "items": {"type": "integer"}},
        "documents": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": DOC_KEYS,
            "properties": {
                "number": _S, "label": _NS, "date": _NS, "collection": _NS, "currency": _NS,
                "total_qty_text": _NS, "net_total_text": _NS, "gross_total_text": _NS,
                "pages": {"type": "array", "items": {"type": "integer"}},
                "lines": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": LINE_KEYS,
                    "properties": {
                        "line_no": _NI, "style": _S, "fabric": _NS, "colour": _NS, "product_class": _NS,
                        "description": _NS, "composition": _NS, "made_in": _NS,
                        "deliver_from": _NS, "deliver_to": _NS,
                        "sizes": {"type": "array", "items": {
                            "type": "object", "additionalProperties": False, "required": ["size", "qty_text"],
                            "properties": {"size": _S, "qty_text": _S}}},
                        "qty_text": _NS, "price_text": _NS, "line_value_text": _NS, "page": {"type": "integer"},
                    }}},
            }}},
    },
}

SYSTEM = """You transcribe a fashion brand's order confirmation PDF into JSON for the buying team at Marais, a \
retailer in Melbourne. Marais is the customer on these documents, never the supplier.

Transcribe; never compute. Every number you return is the text printed on the page, character for character, \
in the document's own format: keep its thousands and decimal separators and any currency sign or code next to it. \
Never add, multiply, total, round, convert or correct anything. If a value is not printed, return null rather \
than working it out from other numbers.

Copy style, fabric and colour codes and document numbers exactly as printed.

Sizes: list a size for a line only where a quantity is printed under that size for that line. Leave out sizes \
whose cell is blank, "-" or 0. Never infer a size from a description and never spread a total across sizes. \
Read each quantity against the size heading directly above it. If the document prints no size grid at all (for \
example a proforma or invoice that gives one quantity per style), set has_size_breakdown to false and leave \
every line's sizes empty.

One PDF can hold several confirmations, each with its own number, line numbering and totals: return one entry \
per confirmation, in the order printed, each confirmation once. A confirmation can run over several pages; a \
repeated page header does not start a new one. Return each confirmation's lines in the order printed, each line \
once. pages lists the PDF pages (numbered from 1) that each confirmation is printed on; pages_without_lines lists \
every other page (terms and conditions, covers, bank details). Every page of the PDF goes in exactly one of those.

total_qty_text is the confirmation's printed total quantity. net_total_text is its printed goods (net) value, \
the amount its lines add up to: not a deposit, an amount due, or a grand total that adds freight, duty or tax. \
gross_total_text is a printed gross value if there is one, else null.

currency is the ISO 4217 code of the prices (EUR for a euro sign or "Euro", USD for US dollars, GBP for pounds \
sterling), or null if none is shown.

supplier is the company that issued the document (the seller), as printed. document_kind says what the \
document is. label is the brand or label a confirmation is for when the document prints one apart from the \
issuer (for example a label name at the start of every line description), else null.

For each line: line_no as printed (null if lines are not numbered); style, fabric and colour as printed (fabric \
or colour null if the layout has no such field); product_class is the printed product class or category column, \
if any; description, composition (material content) and made_in (country of origin) as printed or null; \
deliver_from and deliver_to are the line's printed delivery dates, else null; page is the PDF page number the \
line's style code is printed on."""

PROMPT = "Transcribe every order confirmation in this PDF."


class ReadRefused(otb.GateError):
    """An AI read that cannot be used. `checks` are the FAIL (and WARN) rows; `info` the reader info, or None
    when no request reached the API."""

    def __init__(self, checks, info=None):
        self.checks = checks
        self.info = info
        super().__init__([c["Detail"] for c in checks if c["Result"] == "FAIL"])


def _check(name, result, detail):
    return dict(Check=name, Result=result, Detail=detail)


# ---- the key and the client -------------------------------------------------------
def api_key():
    """The key from the environment, else the app's Streamlit secrets. Any failure means no key."""
    key = os.environ.get(KEY_NAME)
    if key:
        return key
    try:
        import streamlit as st
        key = st.secrets[KEY_NAME]
    except Exception:
        return None
    return str(key) if key else None


def make_client(key):
    import anthropic
    return anthropic.Anthropic(api_key=key)


def request(pdf_bytes):
    """The keyword arguments of the one Messages API call (beta endpoint, streamed)."""
    data = base64.standard_b64encode(pdf_bytes).decode("ascii")
    return dict(
        model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM,
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}},
            {"type": "text", "text": PROMPT}]}],
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=[FALLBACK_BETA], fallbacks="default",
    )


def call(client, pdf_bytes):
    with client.beta.messages.stream(**request(pdf_bytes)) as stream:
        return stream.get_final_message()


def _scrub(text, key):
    text = str(text)
    return text.replace(key, "[hidden]") if key else text


def api_error(e):
    """Plain-English detail for an SDK error, most specific class first."""
    try:
        import anthropic
    except ImportError:
        return f"the AI request failed ({type(e).__name__})"
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return (f"the AI service refused the app's API key ({type(e).__name__}). Ask Colwyn to check "
                f"{KEY_NAME} in the app's secrets.")
    if isinstance(e, (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError)):
        return f"{BUSY} ({type(e).__name__})"
    if isinstance(e, anthropic.BadRequestError):
        return f"the AI service rejected the request: {getattr(e, 'message', e)}"
    if isinstance(e, anthropic.APIStatusError):
        return f"the AI service answered with an error ({e.status_code}): {getattr(e, 'message', e)}"
    return f"the AI request failed ({type(e).__name__}: {e})"


# ---- the answer ----------------------------------------------------------------
def _usage(msg, seconds):
    u = getattr(msg, "usage", None)
    its = getattr(u, "iterations", None) or []
    if its:
        tin = sum(getattr(i, "input_tokens", 0) or 0 for i in its)
        tout = sum(getattr(i, "output_tokens", 0) or 0 for i in its)
    else:
        tin, tout = getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0
    cost = tin * PRICE_USD_PER_MTOK[0] / 1e6 + tout * PRICE_USD_PER_MTOK[1] / 1e6
    return dict(reader="ai", model=getattr(msg, "model", None), requested_model=MODEL, effort=EFFORT,
                input_tokens=tin, output_tokens=tout, seconds=round(seconds, 1), cost_usd=round(cost, 4),
                stop_reason=getattr(msg, "stop_reason", None))


def final_text(msg):
    """The answer's text, after the last fallback switch point (text before it is a declined partial)."""
    blocks = list(msg.content or [])
    start = max((i for i, b in enumerate(blocks) if getattr(b, "type", None) == "fallback"), default=-1) + 1
    return "".join(b.text for b in blocks[start:] if getattr(b, "type", None) == "text")


# ---- numbers -------------------------------------------------------------------------
_CUR = r"(?:US\$|[A-Z]{3}|[€$£¥])"
_MONEY = re.compile(r"(\d{1,3}(?:([.,'’])\d{3}(?:\2\d{3})*)?|\d+)([.,])(\d{2})")
_INT = re.compile(r"\d{1,3}([.,'’])\d{3}(?:\1\d{3})*")
DASH = {"-", "–", "—", ""}


def _bare(text):
    t = re.sub(r"\s+", "", str(text))
    t = re.sub(rf"^{_CUR}+", "", t)
    return re.sub(rf"{_CUR}+$", "", t)


def parse_money(text):
    """Printed amount -> float, or None when it is not one. A bare integer, or a 2-digit decimal part with an
    optional consistent thousands separator that differs from the decimal mark: '€ 1,488.60', '1.234,56',
    '315.610,00', 'EUR 100,00', '100'. '1,488' and '12.5' are ambiguous and come back None."""
    if text is None:
        return None
    t = _bare(text)
    if re.fullmatch(r"\d+", t):
        return float(t)
    m = _MONEY.fullmatch(t)
    if not m or (m.group(2) and m.group(2) == m.group(3)):
        return None
    return float(re.sub(r"\D", "", m.group(1)) + "." + m.group(4))


def parse_int(text):
    """Printed quantity -> int, or None: '12', '1,234', '1.234'."""
    if text is None:
        return None
    t = re.sub(r"\s+", "", str(text))
    if re.fullmatch(r"\d+", t):
        return int(t)
    return int(re.sub(r"\D", "", t)) if _INT.fullmatch(t) else None


# ---- grounding ------------------------------------------------------------------------
def pdf_pages(path, **extract):
    """Each page's text layer in pdfplumber's reading order; None when the PDF cannot be opened. `extract` goes to
    extract_text (x_tolerance=1 keeps words apart that the default runs together)."""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return [p.extract_text(**extract) or "" for p in pdf.pages]
    except Exception:
        return None


def pdf_text(path):
    """All pages' text layer, '' when there is none or it cannot be read."""
    return "\n".join(pdf_pages(path) or [])


def has_text_layer(text):
    return len(re.sub(r"\s+", "", text or "")) >= 20


def undouble(text):
    """'PPRROOFFOORRMMAA' -> 'PROFORMA', '44' -> '4': some PDFs print bold text by drawing each glyph twice,
    and the text layer then carries every character twice. Only whole words made of pairs are collapsed."""
    def one(w):
        return w[::2] if len(w) >= 2 and len(w) % 2 == 0 and all(w[i] == w[i + 1] for i in range(0, len(w), 2)) else w
    return re.sub(r"\S+", lambda m: one(m.group(0)), text)


def _flat(s):
    return re.sub(r"\s+", "", str(s)).casefold()


class Haystack:
    def __init__(self, text):
        self.hays = (_flat(text), _flat(undouble(text)))

    def has(self, needle, amount=False):
        n = _flat(_bare(needle) if amount else needle)
        return bool(n) and any(n in h for h in self.hays)


def _run_end(toks, i, flat):
    """End index of the run of words from toks[i] whose concatenation is `flat`, else -1."""
    acc = ""
    for j in range(i, len(toks)):
        acc += toks[j]
        if acc == flat:
            return j + 1
        if not flat.startswith(acc):
            return -1
    return -1


def find_run(toks, value, lo, hi):
    """First index in [lo, hi) where whole words spell `value` (spaces between them ignored), else -1. Never a
    part of a word: '101' is not in '510 15/10/2026'."""
    flat = _flat(value)
    if not flat:
        return -1
    return next((i for i in range(max(lo, 0), min(hi, len(toks))) if _run_end(toks, i, flat) > 0), -1)


def has_words(toks, value):
    """`value` as whole words of toks: one run of adjacent words; or, for a value with five or more letters, whole
    words in order that spell it with other words between them (a table cell wrapped over lines, even mid-word:
    'VINTAG' ... 'E BLACK'). A short code such as '101' must be one run, never pieced together."""
    flat = _flat(value)
    if not flat:
        return False
    if find_run(toks, value, 0, len(toks)) >= 0:
        return True
    if sum(c.isalpha() for c in flat) < 5:
        return False
    reach = {0}
    for t in toks:
        reach |= {k + len(t) for k in reach if flat.startswith(t, k)}
        if len(flat) in reach:
            return True
    return False


def has_size(toks, label):
    """A size name as a whole word, or as the head of a size/quantity cell ('S/3' holds S)."""
    lab = _flat(label)
    cell = re.compile(re.escape(lab) + r"/\d+")
    return bool(lab) and (any(t == lab or cell.fullmatch(t) for t in toks) or find_run(toks, label, 0, len(toks)) >= 0)


class Pages:
    """The PDF's words page by page, case-folded: from pdfplumber's default extraction and, when given, a tight
    one (x_tolerance=1, which keeps words apart where the default runs them together), each as printed and with
    doubled bold glyphs collapsed. Codes, colours and size names must be whole words or whole runs of words;
    amounts are matched with the spaces of their part removed ('€ 1,488.60'). Each line is judged on the variant
    that misses least overall."""

    def __init__(self, pages, tight=None):
        self.n = len(pages)
        self.variants = []
        for texts in [pages] + ([tight] if tight and len(tight) == len(pages) else []):
            for tidy in (lambda t: t, undouble):
                toks, starts = [], []
                for t in texts:
                    starts.append(len(toks))
                    toks += tidy(t).casefold().split()
                self.variants.append((toks, starts + [len(toks)]))

    def page_tokens(self, v, page):
        toks, starts = self.variants[v]
        return toks[starts[page - 1]:starts[page]] if 1 <= page <= self.n else []

    def misses(self, raw):
        """Per line (document order), what is not where it should be, on the variant that misses least."""
        lines = [(d, i, l) for d in raw for i, l in enumerate(d["lines"], 1)]
        best = None
        for v in range(len(self.variants)):
            got = self._misses(v, lines)
            if best is None or sum(map(len, got)) < sum(map(len, best)):
                best = got
        return lines, best

    def _misses(self, v, lines):
        toks, starts = self.variants[v]
        pos, found = 0, []
        for d, i, l in lines:
            page, j = l["page"], -1
            if 1 <= page <= self.n:
                j = find_run(toks, l["style"], max(pos, starts[page - 1]), starts[page])
            found.append(j)
            if j >= 0:
                pos = j + 1
        out = []
        for k, ((d, i, l), j) in enumerate(zip(lines, found)):
            miss = []
            if j < 0:
                out.append([f"style '{l['style']}' is not on page {l['page']} after the previous line"])
                continue
            end = next((x for x in found[k + 1:] if x >= 0), len(toks))
            block = toks[j:end]
            joined = "".join(block)
            for field in ("price_text", "line_value_text"):
                if l[field] is not None and _flat(_bare(l[field])) not in joined:
                    miss.append(f"{field.replace('_text', '').replace('_', ' ')} '{l[field]}'")
            for field in ("fabric", "colour"):
                if l[field] is not None and not has_words(block, l[field]):
                    miss.append(f"{field} '{l[field]}'")
            page_start = starts[l["page"] - 1]
            first = min([x for x in found if page_start <= x < starts[l["page"]]] or [j])
            prev = max([x for x in found[:k] if x >= 0] or [page_start])
            near = toks[min(prev, j):end] + toks[page_start:first]
            for sz in l["sizes"]:
                if not has_size(near, sz["size"]):
                    miss.append(f"size '{sz['size']}' (not near the line)")
            out.append(miss)
        return out


def _shape(text, exact_digits=False):
    r"""A regex for text of the same shape: digit runs as digits, letters as letters of their case, spaces as
    spaces, anything else as itself. 'SS - 2027/01-62-3009' -> 'SS\s+-\s+\d+/\d+-\d+-\d+'."""
    out = []
    for run in re.findall(r"\d+|[A-Z]+|[a-z]+|\s+|.", str(text)):
        if run.isdigit():
            out.append(rf"\d{{{len(run)}}}" if exact_digits else r"\d+")
        elif run.isspace():
            out.append(r"\s+")
        elif run.isalpha() and exact_digits:
            out.append(rf"[A-Z]{{{len(run)}}}" if run.isupper() else rf"[a-z]{{{len(run)}}}")
        else:
            out.append(re.escape(run))
    return "".join(out)


def _money_shape(raw):
    """The confirmation's amount format, from the amounts the AI returned: '1.446,00' -> dot thousands, comma
    decimals."""
    import collections
    marks = collections.Counter()
    for d in raw:
        for l in d["lines"]:
            for v in (l["price_text"], l["line_value_text"]):
                m = re.fullmatch(r"([\d.,'’]*?)([.,])\d{2}", _bare(v or ""))
                if m:
                    groups = set(re.findall(r"[.,'’]", m.group(1)))
                    marks[(m.group(2), next(iter(groups)) if groups else None)] += 1
    if not marks:
        return None
    dec = collections.Counter(d for d, _ in marks.elements()).most_common(1)[0][0]
    grp = next((g for (d, g) in marks if d == dec and g), None)
    body = rf"(?:\d{{1,3}}(?:{re.escape(grp)}\d{{3}})+|\d+)" if grp else r"\d+"
    return re.compile(body + re.escape(dec) + r"\d{2}")


def unclaimed_problems(data, pages):
    """Every page checked on its own against the PDF text, so a confirmation cannot vanish by having its pages
    declared empty or handed to a neighbour:
    - no page may print a confirmation number (shaped like a returned one, after the same words) that was not
      returned;
    - a page that prints order lines (a style-shaped code with two or more amounts in the confirmation's money
      format) may not be declared empty, nor given to a confirmation none of whose lines were read on it. A totals
      or terms page, with no style-shaped code, may go either way."""
    raw, out = data["documents"], []
    returned = {_flat(d["number"]) for d in raw}
    empty = set(data.get("pages_without_lines") or [])
    owner = {p: d["number"] for d in raw for p in d["pages"]}
    numbers = set()
    for d in raw:
        exact = r"\s*".join(re.escape(c) for c in _flat(d["number"]))
        for p in sorted(set(d["pages"])):
            m = re.search(exact, pages[p - 1], re.I) if 1 <= p <= len(pages) and exact else None
            if m:
                ctx = pages[p - 1][:m.start()].split()[-2:]
                numbers.add("".join(_shape(w) + r"\s+" for w in ctx) + "(?P<num>" + _shape(d["number"]) + r")(?!\w)")
                break
    styles = {_shape(l["style"], exact_digits=True) for d in raw for l in d["lines"] if l["style"]}
    money = _money_shape(raw)

    def order_lines(text):
        words = text.split()
        code = next((w for w in words if any(re.fullmatch(s, w) for s in styles)), None)
        amounts = [w for w in words if money and money.fullmatch(_bare(w))]
        return (code, len(amounts)) if code and len(amounts) >= 2 else None

    for p in range(1, len(pages) + 1):
        seen = set()
        for pat in numbers:
            for m in re.finditer(pat, pages[p - 1], re.I):
                num = m.group("num")
                if _flat(num) not in returned and _flat(num) not in seen:
                    seen.add(_flat(num))
                    where = (" (it was listed as a page with no order lines)" if p in empty else
                             f" (it was given to {owner[p]})" if p in owner else "")
                    out.append(f"page {p} prints confirmation number '{num}', which was not returned{where}")
    for p in sorted(empty):
        found = order_lines(pages[p - 1]) if 1 <= p <= len(pages) else None
        if found:
            out.append(f"page {p} prints order lines (code '{found[0]}' and {found[1]} amounts) but was listed as "
                       "having none")
    for d in raw:
        read_on = {l["page"] for l in d["lines"]}
        for p in sorted(set(d["pages"]) - read_on):
            found = order_lines(pages[p - 1]) if 1 <= p <= len(pages) else None
            if found:
                out.append(f"page {p} prints order lines (code '{found[0]}' and {found[1]} amounts) but none of "
                           f"{d['number']}'s lines were read on it")
    return out


# ---- reading ------------------------------------------------------------------------------
def suppliers_match(read, names):
    def norm(s):
        return re.sub(r"[\W_]+", "", str(s or "")).casefold()
    return bool(norm(read)) and norm(read) in {norm(n) for n in names}


def page_problems(data, pages):
    """Every page in exactly one confirmation or in pages_without_lines; no confirmation or line twice."""
    import collections
    raw, n, out = data["documents"], pages.n, []
    claims = collections.Counter(p for d in raw for p in d["pages"])
    claims.update(data.get("pages_without_lines") or [])
    twice = sorted(p for p, c in claims.items() if c > 1)
    outside = sorted(p for p in claims if not 1 <= p <= n)
    missing = sorted(set(range(1, n + 1)) - set(claims))
    if twice:
        out.append(f"page(s) {twice} given to more than one confirmation or also listed as having no order lines")
    if outside:
        out.append(f"page(s) {outside} do not exist (the PDF has {n})")
    if missing:
        out.append(f"page(s) {missing} of {n} are in no confirmation and not listed as pages with no order lines")
    numbers = collections.Counter(d["number"] for d in raw)
    out += [f"confirmation {k} returned {c} times" for k, c in numbers.items() if c > 1]
    for d in raw:
        off = sorted({l["page"] for l in d["lines"]} - set(d["pages"]))
        if off:
            out.append(f"{d['number']}: lines on page(s) {off}, which are not among its pages {d['pages']}")
        keys = collections.Counter((l["style"], l["fabric"], l["colour"]) for l in d["lines"])
        doc_toks = [t for p in sorted(set(d["pages"])) for t in pages.page_tokens(0, p)]
        for (style, fabric, colour), c in keys.items():
            flat = _flat(style)
            printed = sum(1 for i in range(len(doc_toks)) if flat and _run_end(doc_toks, i, flat) > 0)
            if c > 1 and printed < c:
                out.append(f"{d['number']}: line {style} {fabric or ''} {colour or ''} returned {c} times but its "
                           f"style is printed {printed} time(s)")
    return out


def interpret(data, brand_cfg, pages, tight=None):
    """Model JSON -> (docs, fails, warns). docs have the otb shape plus reader='ai' on every line.
    `pages` is the PDF's text page by page (a single string counts as one page); `tight` the same with words kept
    apart (pdfplumber x_tolerance=1)."""
    if isinstance(pages, str):
        pages = [pages]
    fails, warns = [], []
    names = brand_cfg["oc_supplier"]
    names = [names] if isinstance(names, str) else list(names)
    supplier, kind = data["supplier"], data["document_kind"]
    if not suppliers_match(supplier, names):
        fails.append(_check(SUPPLIER_CHECK, "FAIL", (
            f"the PDF was issued by '{supplier}', but the brand picked is {brand_cfg.get('vendor')} "
            f"({brand_cfg.get('_name', 'brand')}), whose order confirmations come from {' or '.join(names)}. "
            "Check you picked the right brand. If the brand is not in the list yet, see 'My brand is not in "
            "the list' under the brand picker.")))
    if kind in SHIPPED:
        fails.append(_check(KIND_CHECK, "FAIL", (
            f"the AI read this as a {kind.replace('_', ' ')}: its quantities are what was shipped, not what was "
            "ordered. Build the PO from the order confirmation.")))
    elif kind != "order_confirmation":
        warns.append(_check(KIND_CHECK, "WARN", (
            f"the AI read this as a {kind.replace('_', ' ')}, not an order confirmation. Build the PO only from "
            "the document that confirms what was ordered.")))
    raw = data["documents"]
    if not raw:
        fails.append(_check(GATE_CHECK, "FAIL", NOT_FOUND))
        return [], fails, warns

    no_sizes = [f"{d['number']} line {l['line_no'] or i}" for d in raw for i, l in enumerate(d["lines"], 1)
                if not l["sizes"]]
    if not data["has_size_breakdown"]:
        fails.append(_check(SIZES_CHECK, "FAIL", NO_SIZES))
    elif no_sizes:
        fails.append(_check(SIZES_CHECK, "FAIL", f"{len(no_sizes)} line(s) with no size: {', '.join(no_sizes[:10])}; "
                                                 + NO_SIZES))

    bad, docs = [], []
    hint = " (a packing list or delivery note carries no prices; upload the order confirmation)" \
        if kind == "delivery_note" else ""
    for d in raw:
        lines = []
        for i, l in enumerate(d["lines"], 1):
            tag = f"{d['number']} line {l['line_no'] or i} ({l['style']})"
            sizes = {}
            for s in l["sizes"]:
                if str(s["qty_text"]).strip() in DASH:
                    continue
                q = parse_int(s["qty_text"])
                if q is None:
                    bad.append(f"{tag}: size {s['size']} quantity '{s['qty_text']}'")
                elif s["size"] in sizes:
                    bad.append(f"{tag}: size {s['size']} listed twice")
                elif q:
                    sizes[s["size"]] = q
            vals = {}
            for field, parse, what in (("qty_text", parse_int, "quantity"), ("price_text", parse_money, "unit price"),
                                       ("line_value_text", parse_money, "line value")):
                v = parse(l[field])
                if v is None:
                    bad.append(f"{tag}: {what} " + (f"'{l[field]}'" if l[field] is not None else "not printed") + hint)
                vals[field] = v
            lines.append(dict(
                line_no=l["line_no"] if l["line_no"] is not None else i, style=l["style"], fabric=l["fabric"],
                colour=l["colour"], product_class=l["product_class"] or "", sizes=sizes, qty=vals["qty_text"],
                price=vals["price_text"], line_value=vals["line_value_text"], deliver_from=l["deliver_from"],
                deliver_to=l["deliver_to"], page=l["page"], description=l["description"],
                composition=l["composition"], made_in=l["made_in"], reader="ai"))
        tq, net = parse_int(d["total_qty_text"]), parse_money(d["net_total_text"])
        for v, field, what in ((tq, "total_qty_text", "total quantity"), (net, "net_total_text", "net total")):
            if v is None:
                bad.append(f"{d['number']}: {what} " + (f"'{d[field]}'" if d[field] is not None else "not printed"))
        docs.append(dict(doc=d["number"], date=d["date"], collection=d["collection"], currency=d["currency"],
                         total_qty=tq, net=net, gross=parse_money(d["gross_total_text"]),
                         gender=otb.collection_gender(d["collection"]), pages=d["pages"], lines=lines, reader="ai",
                         issuer=supplier, label=d.get("label")))
    if bad:
        fails.append(_check(NUMBERS_CHECK, "FAIL", f"{len(bad)} value(s) the tool cannot read as a number: "
                                                   + "; ".join(bad[:12])))

    want = cost_currency(brand_cfg)
    off = currency_off(docs, want)
    if off:
        fails.append(_check(CURRENCY_CHECK, "FAIL", f"{'; '.join(off)}, but the brand's cost currency is {want}. "
                                                    "Check you picked the right brand and document."))

    pg = Pages(pages, tight)
    dup = page_problems(data, pg) + unclaimed_problems(data, pages)
    if dup:
        fails.append(_check(PAGES_CHECK, "FAIL", "; ".join(dup[:10])))
    missing = []
    empty = set(data.get("pages_without_lines") or [])
    for d in raw:
        doc_hay = Haystack("\n".join(pages[p - 1] for p in sorted(set(d["pages"])) if 1 <= p <= pg.n))
        if not doc_hay.has(d["number"]):
            missing.append(f"{d['number']}: document number not on its pages")
        # a totals page with no order lines may be declared empty: the net total may be there, never on another
        # confirmation's pages (the gates check it by arithmetic as well)
        total_hay = Haystack("\n".join(pages[p - 1] for p in sorted(set(d["pages"]) | empty) if 1 <= p <= pg.n))
        if d["net_total_text"] is not None and not total_hay.has(d["net_total_text"], amount=True):
            missing.append(f"{d['number']}: net total '{d['net_total_text']}' not on its pages")
    lines, misses = pg.misses(raw)
    for (d, i, l), miss in zip(lines, misses):
        if miss:
            missing.append(f"{d['number']} line {l['line_no'] or i} ({l['style']}): " + ", ".join(miss))
    if missing:
        fails.append(_check(GROUNDING_CHECK, "FAIL", (
            f"{len(missing)} line(s) or document(s) where what the AI read is not in that part of the PDF text, so "
            "the read is not used: " + "; ".join(missing[:12]))))

    if not bad:
        fails += [_check(GATE_CHECK, "FAIL", p) for p in otb.gate_problems(docs)]
    return docs, fails, warns


def read(pdf_path, brand_cfg, factory=None):
    """Read and check an OC with the AI reader. -> (docs, warn checks, info); raises ReadRefused."""
    if not brand_cfg.get("oc_supplier"):
        raise ReadRefused([_check(SETUP_CHECK, "FAIL", (
            "the brand config has no oc_supplier, which the AI reader needs to check the PDF comes from this "
            "brand's supplier. Ask Colwyn to add oc_supplier (the company name printed as the issuer on the "
            "brand's order confirmations) to this brand's config."))])
    pages = pdf_pages(pdf_path)
    if pages is None:
        raise ReadRefused([_check(READ_CHECK, "FAIL", "the PDF cannot be opened; nothing was sent to the AI service")])
    if len(pages) > MAX_PAGES:
        raise ReadRefused([_check(READ_CHECK, "FAIL", (
            f"this PDF has {len(pages)} pages and the AI reader takes at most {MAX_PAGES}; nothing was sent. Split "
            "it into smaller PDFs (whole confirmations each) and read them one at a time."))])
    if not has_text_layer("".join(pages)):
        raise ReadRefused([_check(TEXT_CHECK, "FAIL", NO_TEXT)])
    key = api_key()
    if not key:
        raise ReadRefused([_check(SETUP_CHECK, "FAIL", NO_KEY)])
    pdf = Path(pdf_path).read_bytes()
    t0 = time.monotonic()
    try:
        msg = call((factory or make_client)(key), pdf)
    except Exception as e:
        raise ReadRefused([_check(READ_CHECK, "FAIL", _scrub(api_error(e), key))]) from None
    info = _usage(msg, time.monotonic() - t0)
    if msg.stop_reason == "refusal":
        cat = getattr(getattr(msg, "stop_details", None), "category", None)
        raise ReadRefused([_check(READ_CHECK, "FAIL", f"the AI declined to read this document (refusal, category "
                                                      f"{cat or 'not given'}); nothing from it was used")], info)
    if msg.stop_reason == "max_tokens":
        raise ReadRefused([_check(READ_CHECK, "FAIL", (
            f"the read stopped at the {MAX_TOKENS:,}-token output limit before it finished, so it is not used. "
            "Split the PDF into smaller files and read each one."))], info)
    if msg.stop_reason != "end_turn":
        raise ReadRefused([_check(READ_CHECK, "FAIL", f"the read stopped early ({msg.stop_reason}); not used")], info)
    try:
        data = json.loads(final_text(msg))
    except ValueError:
        raise ReadRefused([_check(READ_CHECK, "FAIL", "the AI's answer was not valid JSON; nothing from it was used")],
                          info) from None
    info.update(raw=data, supplier=data.get("supplier") if isinstance(data, dict) else None,
                document_kind=data.get("document_kind") if isinstance(data, dict) else None)
    try:
        docs, fails, warns = interpret(data, brand_cfg, pages, pdf_pages(pdf_path, x_tolerance=1))
    except (KeyError, TypeError, AttributeError) as e:
        raise ReadRefused([_check(READ_CHECK, "FAIL", f"the AI's answer did not have the expected shape "
                                                      f"({type(e).__name__}: {e}); nothing from it was used")],
                          info) from None
    if fails:
        raise ReadRefused(fails + warns, info)
    return docs, warns, info


CHECK_EVERY_LINE = "Check every line's code, colour, sizes, quantities and price against the PDF."


def reader_check(info, refused=False):
    """The 'Order confirmation reader' row for an AI read."""
    if info is None:
        return _check(READER_CHECK, "INFO", "not read: the AI reader was chosen, but nothing was sent to the AI "
                                            "service (see the FAIL rows)")
    cost = f"about US${info['cost_usd']:.2f}"
    fb = "" if info["model"] in (None, MODEL) else f" (answered by the fallback model {info['model']}; cost " \
                                                     f"estimated at {MODEL} prices)"
    head = (f"read by AI ({info['model'] or MODEL}{fb}) in {info['seconds']:.0f} s, {cost} "
            f"({info['input_tokens']:,} input + {info['output_tokens']:,} output tokens). ")
    if refused:
        return _check(READER_CHECK, "WARN", head + "The read was refused (see the FAIL rows) and nothing from it "
                                                   "was used; the request is still paid for.")
    return _check(READER_CHECK, "WARN", head + (
        "Every quantity, size and price came from an AI reading. It passed the same gates as the built-in reader. "
        "Each line's code, colour, price and line value were found in that line's part of the PDF text, and its "
        "size names near the line; quantities and totals are too short to look up, so they were checked by "
        "arithmetic only. "
        + CHECK_EVERY_LINE))
