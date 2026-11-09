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
  numbers that parse; and, when the PDF has a text layer, have every line's style,
  price and line value and every document's number and totals in that text
  (grounding). A PDF with no text layer is a WARN, never a pass.

What no gate catches: a quantity read under the wrong size. Sizes sum to the line,
lines to the document, and the codes are in the text either way. The Review sheet
marks every value from an AI read amber and the page says to check sizes by eye.

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

SETUP_CHECK = "AI reader is set up"
READ_CHECK = "AI reader could read the order confirmation"
SUPPLIER_CHECK = "Order confirmation is from this brand's supplier"
CURRENCY_CHECK = "Order confirmation is in the brand's cost currency"
SIZES_CHECK = "Order confirmation prints a quantity under each size"
NUMBERS_CHECK = "Every number the AI read is a printed amount the tool can parse"
GROUNDING_CHECK = "Every code and amount the AI read is in the PDF text"
KIND_CHECK = "Document is an order confirmation"
GATE_CHECK = "Order confirmation passes its gates (parsed = printed)"
READER_CHECK = "Order confirmation reader"

NO_KEY = f"The AI reader is not set up: the app has no {KEY_NAME} in its secrets. Ask Colwyn."
NO_SIZES = ("this document prints no sizes, e.g. a proforma; upload the order confirmation it refers to")
BUSY = "the AI service is busy or could not be reached; try again in a few minutes"
NOT_FOUND = "no order confirmations found"

KINDS = ["order_confirmation", "proforma_invoice", "invoice", "delivery_note", "other"]
_S = {"type": "string"}
_NS = {"type": ["string", "null"]}
_NI = {"type": ["integer", "null"]}
LINE_KEYS = ["line_no", "style", "fabric", "colour", "product_class", "description", "composition", "made_in",
             "deliver_from", "deliver_to", "sizes", "qty_text", "price_text", "line_value_text", "page"]
DOC_KEYS = ["number", "date", "collection", "currency", "total_qty_text", "net_total_text", "gross_total_text",
            "pages", "lines"]
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["supplier", "document_kind", "has_size_breakdown", "documents"],
    "properties": {
        "supplier": _S,
        "document_kind": {"type": "string", "enum": KINDS},
        "has_size_breakdown": {"type": "boolean"},
        "documents": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": DOC_KEYS,
            "properties": {
                "number": _S, "date": _NS, "collection": _NS, "currency": _NS,
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
                        "qty_text": _NS, "price_text": _NS, "line_value_text": _NS, "page": _NI,
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
per confirmation, in the order printed. A confirmation can run over several pages; a repeated page header does \
not start a new one.

total_qty_text is the confirmation's printed total quantity. net_total_text is its printed goods (net) value, \
the amount its lines add up to: not a deposit, an amount due, or a grand total that adds freight, duty or tax. \
gross_total_text is a printed gross value if there is one, else null.

currency is the ISO 4217 code of the prices (EUR for a euro sign or "Euro", USD for US dollars, GBP for pounds \
sterling), or null if none is shown.

supplier is the company that issued the document (the seller), as printed. document_kind says what the \
document is.

For each line: line_no as printed (null if lines are not numbered); style, fabric and colour as printed (fabric \
or colour null if the layout has no such field); product_class is the printed product class or category column, \
if any; description, composition (material content) and made_in (country of origin) as printed or null; \
deliver_from and deliver_to are the line's printed delivery dates, else null; page is the PDF page number the \
line's quantities are printed on."""

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
def pdf_text(path):
    """All pages' text layer, '' when there is none or it cannot be read."""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        return ""


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


# ---- reading ------------------------------------------------------------------------------
def suppliers_match(read, names):
    def norm(s):
        return re.sub(r"[\W_]+", "", str(s or "")).casefold()
    return bool(norm(read)) and norm(read) in {norm(n) for n in names}


def interpret(data, brand_cfg, text):
    """Model JSON -> (docs, fails, warns). docs have the otb shape plus reader='ai' on every line."""
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
    if kind != "order_confirmation":
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
                         gender=otb.collection_gender(d["collection"]), pages=d["pages"], lines=lines, reader="ai"))
    if bad:
        fails.append(_check(NUMBERS_CHECK, "FAIL", f"{len(bad)} value(s) the tool cannot read as a number: "
                                                   + "; ".join(bad[:12])))

    want = cost_currency(brand_cfg)
    off = currency_off(docs, want)
    if off:
        fails.append(_check(CURRENCY_CHECK, "FAIL", f"{'; '.join(off)}, but the brand's cost currency is {want}. "
                                                    "Check you picked the right brand and document."))

    if has_text_layer(text):
        hay, missing = Haystack(text), []
        for d in raw:
            if not hay.has(d["number"]):
                missing.append(f"document number '{d['number']}'")
            for field, what in (("total_qty_text", "total quantity"), ("net_total_text", "net total")):
                if d[field] is not None and not hay.has(d[field], amount=True):
                    missing.append(f"{d['number']} {what} '{d[field]}'")
            for i, l in enumerate(d["lines"], 1):
                tag = f"{d['number']} line {l['line_no'] or i}"
                if not hay.has(l["style"]):
                    missing.append(f"{tag} style '{l['style']}'")
                for field, what in (("price_text", "price"), ("line_value_text", "line value")):
                    if l[field] is not None and not hay.has(l[field], amount=True):
                        missing.append(f"{tag} {what} '{l[field]}'")
        if missing:
            fails.append(_check(GROUNDING_CHECK, "FAIL", f"{len(missing)} value(s) the AI read are not in the PDF's "
                                                         "text, so the read is not used: " + "; ".join(missing[:12])))
    else:
        warns.append(_check(GROUNDING_CHECK, "WARN", "the PDF has no text layer (a scan?), so the codes and amounts "
                                                     "the AI read could not be cross-checked against the PDF text"))

    if not bad:
        fails += [_check(GATE_CHECK, "FAIL", p) for p in otb.gate_problems(docs)]
    return docs, fails, warns


def read(pdf_path, brand_cfg, factory=None):
    """Read and check an OC with the AI reader. -> (docs, warn checks, info); raises ReadRefused."""
    if not brand_cfg.get("oc_supplier"):
        raise ReadRefused([_check(SETUP_CHECK, "FAIL", (
            "the brand config has no oc_supplier, which the AI reader needs to check the PDF comes from this "
            "brand's supplier. Add oc_supplier: the company name printed as the issuer on the brand's order "
            "confirmations."))])
    key = api_key()
    if not key:
        raise ReadRefused([_check(SETUP_CHECK, "FAIL", NO_KEY)])
    pdf = Path(pdf_path).read_bytes()
    text = pdf_text(pdf_path)
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
        docs, fails, warns = interpret(data, brand_cfg, text)
    except (KeyError, TypeError, AttributeError) as e:
        raise ReadRefused([_check(READ_CHECK, "FAIL", f"the AI's answer did not have the expected shape "
                                                      f"({type(e).__name__}: {e}); nothing from it was used")],
                          info) from None
    info["text_layer"] = has_text_layer(text)
    if fails:
        raise ReadRefused(fails + warns, info)
    return docs, warns, info


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
    grounded = ("every code and amount it read was found in the PDF text" if info.get("text_layer") else
                "the PDF has no text layer, so its codes and amounts could not be checked against the PDF text")
    return _check(READER_CHECK, "WARN", head + (
        f"Every quantity, size and price came from an AI reading. It passed the same gates as the built-in reader, "
        f"and {grounded}. A quantity read under the wrong size passes every gate, so check each line's sizes "
        "against the PDF."))
