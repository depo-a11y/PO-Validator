"""Small shared helpers: size normalisation, ordering, money, text."""
import math
import re

ALPHA = ["XXXS", "XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "3XL", "4XL"]


def norm_size(s):
    """Supplier/sheet size -> store size. UNI->OS, '03'->'3', 48.0->'48'."""
    if s is None:
        return ""
    if isinstance(s, float):
        if math.isnan(s):
            return ""
        if s.is_integer():
            return str(int(s))
        return str(s)
    if isinstance(s, int):
        return str(s)
    t = str(s).strip().upper()
    if t in ("UNI", "U", "ONE SIZE", "TU", "NS"):
        return "OS"
    if re.fullmatch(r"\d+\.0+", t):
        t = t.split(".")[0]
    if re.fullmatch(r"0\d", t):
        t = t[1:]
    return t


ONE_SIZE = {"OS", "OSFM", "O/S", "F"}   # printed one-size names; "F" only ever matched, never rewritten on its own


def size_alias(s):
    """The form two printed names of one size share, for comparison only: OS, OSFM, O/S and F; 'IT 42' and '42';
    'S - M' and 'S-M'. A size is never written in this form."""
    t = re.sub(r"\s*-\s*", "-", norm_size(s))
    if t in ONE_SIZE:
        return "OS"
    m = re.fullmatch(r"IT\s*(\d+(?:\.5)?)", t)
    return m.group(1) if m else t


def store_sizes(ordered, known, size_map=None):
    """{printed size: qty} -> {store size: qty}. A printed size goes through the brand's size_map first, then keeps
    its own name if the store knows it, else takes the one known store name of the same size (size_alias). `known`
    is a list of sets of live size names, closest first (the product's own, its siblings', its kind of item's); the
    first set that knows the size decides. When a set holds two names for it, nothing is rewritten."""
    out = {}
    for size, q in ordered.items():
        t = norm_size((size_map or {}).get(norm_size(size), size))
        for names in known:
            if t in names:
                break
            same = {n for n in names if size_alias(n) == size_alias(t)}
            if same:
                t = same.pop() if len(same) == 1 else t
                break
        out[t] = out.get(t, 0) + q
    return out


def one_name_per_size(run, ordered):
    """A size run with one name per size. Where siblings spell a size two ways (one live product 'IT 40', another
    '40'), the name the ordered sizes use wins: their own name if ordered, else the same form (bare or not)."""
    bare = [norm_size(s) == size_alias(s) for s in ordered]
    want_bare = bare.count(True) >= bare.count(False)
    groups = {}
    for s in run:
        groups.setdefault(size_alias(s), []).append(s)
    pick = {a: next((n for n in g if n in ordered), next((n for n in g if (norm_size(n) == a) == want_bare), g[0]))
            for a, g in groups.items()}
    return [s for s in run if pick[size_alias(s)] == s]


def size_clashes(sizes):
    """Groups of two or more names in `sizes` that are one size: [['42', 'IT 42'], ...]."""
    groups = {}
    for s in sizes:
        groups.setdefault(size_alias(s), set()).add(norm_size(s))
    return [sorted(g) for g in groups.values() if len(g) > 1]


def size_key(s):
    s = norm_size(s)
    if s in ALPHA:
        return (1, ALPHA.index(s), s)
    try:
        return (0, float(s), s)
    except ValueError:
        return (2, 0, s)


def sort_sizes(sizes):
    return sorted({norm_size(s) for s in sizes if norm_size(s)}, key=size_key)


def norm_code(code):
    return re.sub(r"\s+", " ", str(code or "").strip().upper())


def join_key(style, fabric, colour):
    return norm_code(f"{style}{fabric or ''} {colour or ''}")


def money(x):
    """Parse '$1,234.00' / 1234 / 'EUR 123' -> float, None when blank."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return None if (isinstance(x, float) and math.isnan(x)) else float(x)
    t = re.sub(r"[^\d.\-]", "", str(x))
    if t in ("", ".", "-"):
        return None
    return float(t)


def blank(v):
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return str(v).strip() == ""


def fmt_money(x, currency):
    """The Variant.cost_price form, '<currency> <amount>': 'EUR 34', 'USD 150.50'."""
    return f"{currency} {int(x)}" if float(x).is_integer() else f"{currency} {x:.2f}"


def fmt_eur(x):
    return fmt_money(x, "EUR")


# Printed currency names the readers meet, upper-cased. A bare '$' or '¥' names no one currency, so it is not here.
CURRENCY_NAMES = {"EURO": "EUR", "EUROS": "EUR", "EUROPEAN EURO": "EUR", "€": "EUR", "US DOLLAR": "USD",
                  "US DOLLARS": "USD", "U.S. DOLLAR": "USD", "UNITED STATES DOLLAR": "USD", "US$": "USD",
                  "POUND STERLING": "GBP", "BRITISH POUND": "GBP", "£": "GBP", "JAPANESE YEN": "JPY",
                  "AUSTRALIAN DOLLAR": "AUD"}


def iso_currency(text):
    """'European Euro' (otb's printed text), 'EUR', '€' -> 'EUR'. None when it names no currency the tool knows."""
    t = " ".join(str(text or "").split()).upper()
    return t if re.fullmatch(r"[A-Z]{3}", t) else CURRENCY_NAMES.get(t)


RRP_LABEL = re.compile(r"rrp|msrp|recommended|sugg|retail(?!er)")
RRP_WORDS = {"srp", "pvp", "uvp"}   # whole words only: suggested retail, precio de venta al publico, Preisempfehlung


def rrp_label(word):
    """A printed word that labels a recommended retail price: RRP, MSRP, Retail, Sugg., Recommended, also run into
    their neighbours ('RRP(AUD)', 'RetailPrice'); SRP, PVP and UVP as whole words ('SRP:', '(UVP)'). Not
    'Retailer', which names the buyer."""
    w = str(word).casefold()
    return bool(RRP_LABEL.search(re.sub(r"[^a-z]", "", w))) or bool(RRP_WORDS & set(re.split(r"[^a-z]+", w)))


def rrp_header(words):
    """A column-header row that names an RRP column: a word rrp_label accepts, or 'Ticket Price' ('Ticket' alone
    is not a price)."""
    flat = [re.sub(r"[^a-z]", "", str(w).casefold()) for w in words]
    return any(map(rrp_label, words)) or any((a + b).startswith("ticketprice") for a, b in zip(flat, flat[1:] + [""]))


def cost_currency(brand):
    """The brand's cost_currency as an ISO code, the currency every OC for it must be in."""
    raw = (brand or {}).get("cost_currency") or "EUR"
    return iso_currency(raw) or str(raw).strip().upper()


def currency_off(docs, want):
    """['SO-1 is in USD', ...] for each document whose currency, normalised, is not `want`."""
    return [f"{d['doc']} is in {d.get('currency') or 'no currency the reader could find'}" for d in docs
            if iso_currency(d.get("currency")) != want]


def title_colour(title):
    """'Tabi Ballerina in Moka' -> ('Tabi Ballerina', 'Moka')."""
    if not title:
        return None, None
    m = re.match(r"^(.*?)\s+in\s+(.+)$", str(title).strip())
    if not m:
        return str(title).strip(), None
    return m.group(1).strip(), m.group(2).strip()


def slug(title):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(title).lower())).strip("-")
