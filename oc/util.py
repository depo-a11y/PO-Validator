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


def fmt_eur(x):
    return f"EUR {int(x)}" if float(x).is_integer() else f"EUR {x:.2f}"


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
