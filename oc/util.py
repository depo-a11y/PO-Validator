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
