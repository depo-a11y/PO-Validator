"""Field resolvers shared by `draft` (whole PO from the OC) and `complete`
(fill the blanks of a PO someone started by hand).

Every resolver returns (value, source, detail). Sources, in trust order:
  OC, catalog  -> green   (read from the confirmation or the live store, same style)
  cart, history, rule, similar -> amber (the selling-session cart, past POs on 2+ rows,
                           a brand rule, or a different live style at the same wholesale)
  thin, fit, guess -> red (one past row, a fitted ratio, or nothing to go on)
"""
import collections
import html
import math
import re

from .util import norm_code, norm_size, sort_sizes, size_key, title_colour, slug

GREEN, AMBER, RED = "green", "amber", "red"
GENDERS = ("MEN", "WOMEN", "UNISEX")
# A colour description is copied into a title; anything outside these is a garbled export ('Peach Pur}e').
CLEAN_COLOUR = re.compile(r"[^\w\s/&'.,()+-]")
COLOUR = {"OC": GREEN, "catalog": GREEN, "cart": AMBER, "history": AMBER, "rule": AMBER, "similar": AMBER,
          "thin": RED, "fit": RED, "guess": RED}
RANK = {GREEN: 0, AMBER: 1, RED: 2}


def worse(a, b):
    """The less trusted of two sources (b on a tie)."""
    return a if RANK[COLOUR[a]] > RANK[COLOUR[b]] else b


def split_code(code):
    """'S99WZ0004P0004 T0004' -> ('S99WZ0004', 'S99WZ0004P0004', 'T0004')."""
    code = norm_code(code)
    body, _, colour = code.partition(" ")
    return body[:9], body, colour


class Resolver:
    def __init__(self, brand, catalog, history, tag_lookup, oc_lines=None, cart=None):
        self.b = brand
        self.cat = catalog
        self.hist = history
        self.types = set(t for t in tag_lookup if t and t != "nan")
        self.oc = {}                       # code -> OC line (with doc)
        for l in oc_lines or []:
            self.oc[l["code"]] = l
        self.cart = cart or {}
        self._cart_rows = self.cart.get("rows", {})
        self._next_sku = None
        self.used_skus = set()

    # ---- siblings ---------------------------------------------------------
    def _rank(self, ps):
        return sorted(ps, key=lambda p: (p["status"].upper() == "ARCHIVED", -_created(p)))

    def siblings(self, code, level="fabric"):
        style, body, colour = split_code(code)
        key = body if level == "fabric" else style
        out = [p for p in self.cat.style_family(key) if p["code"] != norm_code(code)]
        if level == "style":
            out = [p for p in out if not p["code"].startswith(body)]
        return self._rank(out)

    def history_siblings(self, code, level="fabric"):
        style, body, _ = split_code(code)
        key = body if level == "fabric" else style
        return [r for c, rs in self.hist.by_code.items() if c.startswith(key) and c != norm_code(code) for r in rs]

    # ---- cart ---------------------------------------------------------------
    def cart_row(self, code):
        """The cart row for this exact style+fabric+colour, or None. Never matched on
        the colour code alone (fabric-scoped colour codes)."""
        return self._cart_rows.get(norm_code(code))

    def cart_why(self, row, what):
        return f"{self.cart['label']} row {row['row']}: {what}"

    def gender_of(self, code, oc_gender):
        """OC gender, else the cart's (Female/Male); Unisex and blank stay None."""
        if oc_gender:
            return oc_gender
        row = self.cart_row(code)
        return row["gender"] if row else None

    def product_class_of(self, code, oc_class):
        if (oc_class or "").strip():
            return oc_class
        row = self.cart_row(code)
        return row["style_name"] if row else oc_class

    # ---- colour -------------------------------------------------------------
    def _colour_votes(self, code, field):
        """Votes for a colour attribute from products sharing the colour code.

        Same fabric+colour first. Across fabrics only when the catalog's own uses
        of that colour code agree (at least 2 products, >= 60%): Margiela's numeric RTW colour codes
        mean different colours on different fabrics, its T-codes do not.
        """
        _, body, colour = split_code(code)
        fabric = body[9:]
        same, cross = collections.Counter(), collections.Counter()
        for c, ps in self.cat.by_code.items():
            cb, _, cc = c.partition(" ")
            if cc != colour:
                continue
            for p in ps:
                v = p[field]
                if v:
                    (same if cb[9:] == fabric else cross)[v.upper() if field == "colour_family" else v] += 1
        if same:
            v, n = same.most_common(1)[0]
            return v, n, sum(same.values()), "same fabric+colour"
        if cross:
            v, n = cross.most_common(1)[0]
            if n >= 2 and n / sum(cross.values()) >= 0.6:
                return v, n, sum(cross.values()), "same colour code"
        return None, 0, sum(cross.values()), None

    def colour_name(self, code):
        _, body, colour = split_code(code)
        row = self.cart_row(code)
        if row and row["colour"]:
            name = tidy_colour(row["colour"])
            odd = sorted(set(CLEAN_COLOUR.findall(name)))
            if odd:
                return name, "guess", self.cart_why(row, f"Color Description '{row['colour']}' carries "
                                                         f"unexpected character(s) {' '.join(odd)}; retype it")
            v, n, tot, how = self._colour_votes(code, "title_colour")
            if v and how == "same fabric+colour" and v.casefold() == name.casefold():
                return v, "catalog", (f"{how} {colour} titled '{v}' on {n} of {tot} product(s); "
                                      + self.cart_why(row, "Color Description agrees"))
            return name, "cart", self.cart_why(row, f"Color Description '{row['colour']}'")
        v, n, tot, how = self._colour_votes(code, "title_colour")
        if v:
            return v, "catalog", f"{how} {colour} titled '{v}' on {n} of {tot} product(s)"
        why = f"colour code {colour} not in the catalog" if not tot else \
            f"colour code {colour} names disagree across {tot} catalog products"
        return colour, "guess", why + "; the OC has no colour description"

    def colour_family(self, code, colour_name):
        """Same fabric+colour in the catalog first (the house's own call on this
        fabric's colour). With a cart row, its colour description through the brand
        keywords comes next, ahead of other fabrics' use of the colour code."""
        _, _, colour = split_code(code)
        v, n, tot, how = self._colour_votes(code, "colour_family")
        row = self.cart_row(code)
        if v and (how == "same fabric+colour" or row is None):
            return v, "catalog", f"{how} {colour} is {v} on {n} of {tot} product(s)"
        fam = self.keyword_family(colour_name)
        if row is not None and fam and v == fam:
            return v, "catalog", f"{how} {colour} is {v} on {n} of {tot} product(s); " + \
                self.cart_why(row, f"colour '{colour_name}' agrees")
        if row is not None and fam:
            other = f"; other fabrics' {colour} is {v} in the catalog" if v and v != fam else ""
            return fam, "cart", self.cart_why(row, f"colour '{colour_name}' matches the {fam} keyword list{other}")
        if v:
            return v, "catalog", f"{how} {colour} is {v} on {n} of {tot} product(s)"
        if fam:
            return fam, "rule", f"'{colour_name}' matches the {fam} keyword list"
        return "", "guess", f"no colour family for '{colour_name}'"

    def keyword_family(self, colour_name):
        """Family whose keyword appears earliest in the name, so 'Black/Camel' is
        BLACK and 'Turkish Coffee/Black' is BROWN; a tie keeps config order."""
        low = (colour_name or "").lower()
        best = None
        for fam_name, words in self.b["colour_families"].items():
            for w in words:
                m = re.search(r"\b" + re.escape(w.lower()) + r"\b", low)
                if m and (best is None or m.start() < best[0]):
                    best = (m.start(), fam_name)
        return best[1] if best else None

    # ---- type ---------------------------------------------------------------
    def product_type(self, code, product_class, gender):
        for level, label in (("fabric", "same style+fabric"), ("style", "same style")):
            for p in self.siblings(code, level):
                if p["type"] in self.types:
                    return p["type"], "catalog", f"{label}: {p['title']} ({p['code']})"
            for r in self.history_siblings(code, level):
                if r["type"] in self.types:
                    return r["type"], "history", f"{label} in {r['season']} PO: {r['title']}"
        style, body, _ = split_code(code)
        fam = style[:5]
        cnt = collections.Counter(p["type"] for p in self.cat.style_family(fam) if p["type"] in self.types)
        cnt.update(r["type"] for c, rs in self.hist.by_code.items() if c.startswith(fam) for r in rs if r["type"] in self.types)
        if cnt:
            (t, n), = cnt.most_common(1)
            share = n / sum(cnt.values())
            if share >= 0.6 and (gender is None or t.startswith(gender + " ")):
                return t, "history", f"style family {fam}: {t} on {n} of {sum(cnt.values())} products/rows"
        pc = (product_class or "").strip().upper()
        g = gender or self.b["default_gender"]
        t, rest = self._table_type(self.b["product_class_types"].get(pc), g)
        if t:
            return t, "rule", f"product class '{product_class}' -> {rest} (brand table, gender {g})"
        row = self.cart_row(code)
        if row and row["subcategory"]:
            t, rest = self._table_type(self.b.get("subcategory_types", {}).get(row["subcategory"].upper()), g)
            if t:
                return t, "cart", self.cart_why(row, f"Subcategory '{row['subcategory']}' -> {rest} "
                                                     f"(brand subcategory_types, gender {g})")
        return "", "guess", f"no Type for product class '{product_class}'" + \
            (f" or cart subcategory '{row['subcategory']}'" if row and row["subcategory"] else "")

    def _table_type(self, entry, gender):
        """A brand-table entry is a Type without gender, a list of them (first valid
        for the gender wins), or a full Type starting with MEN/WOMEN/UNISEX."""
        for rest in ([entry] if isinstance(entry, str) else entry or []):
            t = rest if rest.split(" ", 1)[0] in GENDERS else f"{gender} {rest}"
            if t in self.types:
                return t, rest
        return None, None

    # ---- title --------------------------------------------------------------
    def title(self, code, product_class, colour_name):
        for level, label in (("fabric", "same style+fabric"), ("style", "same style")):
            stems = collections.Counter()
            first = {}
            for p in self.siblings(code, level):
                if p["status"].upper() == "ARCHIVED" or not p["stem"]:
                    continue
                stems[p["stem"]] += 1
                first.setdefault(p["stem"], p)
            if not stems:
                for p in self.siblings(code, level):
                    if p["stem"]:
                        stems[p["stem"]] += 1
                        first.setdefault(p["stem"], p)
            if stems:
                stem = max(stems, key=lambda s: (stems[s], -list(first).index(s)))
                return f"{stem} in {colour_name}", "catalog", f"{label}: stem of '{first[stem]['title']}'"
        stem = " ".join(w.capitalize() for w in re.split(r"\s+", (product_class or "").strip()) if w)
        return f"{stem} in {colour_name}", "guess", f"product class '{product_class}' title-cased"

    # ---- sizes --------------------------------------------------------------
    def size_run(self, code, ordered, ptype):
        ordered = set(sort_sizes(ordered))
        for level, label in (("fabric", "same style+fabric"), ("style", "same style")):
            live = [p for p in self.siblings(code, level) if p["status"].upper() != "ARCHIVED"]
            run = {v["size"] for p in live for v in p["variants"] if v["size"]}
            if run:
                return sort_sizes(run | ordered), "catalog", \
                    f"{label}: sizes stocked across {len(live)} live product(s) ({live[0]['title']}...)"
        runs = self.hist.runs_for_type(ptype) if ptype else collections.Counter()
        fits = [(n, r) for r, n in runs.items() if ordered <= set(r)]
        if fits:
            n, r = max(fits, key=lambda t: (t[0], len(t[1])))
            return sort_sizes(r), "history", f"most common {ptype} run holding the ordered sizes ({n} past products)"
        cat_runs = collections.Counter()
        for p in self.cat.products.values():
            if p["type"] == ptype and p["status"].upper() != "ARCHIVED":
                cat_runs[tuple(sort_sizes(v["size"] for v in p["variants"]))] += 1
        fits = [(n, r) for r, n in cat_runs.items() if ordered <= set(r)]
        if fits:
            n, r = max(fits, key=lambda t: (t[0], len(t[1])))
            return sort_sizes(r), "catalog", f"most common live {ptype} run holding the ordered sizes ({n} products)"
        return sort_sizes(ordered), "OC", "ordered sizes only (no run found)"

    # ---- price --------------------------------------------------------------
    def cost(self, eur):
        return round(eur * self.b["cost_multiplier"], 2)

    def floor_price(self, eur):
        return self.cost(eur) * self.b["margin_floor"]

    def retail(self, code, eur, ptype):
        step = self.b["price_round"]
        price = src = detail = None
        for p in self.siblings(code, "fabric"):
            if p["status"].upper() == "ARCHIVED":
                continue
            sib_eur = self.oc.get(p["code"], {}).get("price") or self.hist.wholesale_of(p["code"])
            prices = [max(v["price"] or 0, v["compare_at"] or 0) for v in p["variants"]]
            if sib_eur is not None and abs(sib_eur - eur) < 0.005 and prices:
                price = collections.Counter(prices).most_common(1)[0][0]
                src, detail = "catalog", f"same style+fabric {p['title']} at EUR {sib_eur:g} sells at A${price:g}"
                break
        if price is None:
            same = collections.Counter()
            for c, l in self.oc.items():
                if c == norm_code(code) or abs(l["price"] - eur) > 0.005:
                    continue
                p, _, _ = self.cat.match(c)
                if p is not None:
                    same.update(max(v["price"] or 0, v["compare_at"] or 0) for v in p["variants"][:1])
            if same:
                price, n = same.most_common(1)[0]
                src, detail = "similar", (f"{n} carryover line(s) of other styles on this OC at EUR {eur:g} "
                                          f"sell at A${price:g} today")
        if price is None:
            price, detail, n = self.hist.ladder(eur)
            src = ("history" if n >= 2 else "thin") if price else None
        if price is None:
            from .history import category_of
            ratio, why = self.hist.ratio(category_of(ptype))
            if ratio is None:
                ratio, why = self.hist.ratio()
            if ratio is not None:
                price = math.ceil(eur * ratio / step) * step
                src, detail = "fit", f"EUR {eur:g} x {ratio:.2f} ({why}), rounded up to {step}"
        if price is None:
            return None, "guess", "no price history"
        floor = self.floor_price(eur)
        if price < floor - 1e-9:
            new = math.ceil(floor / step) * step
            return new, worse(src, "rule"), (f"{detail}; raised from A${price:g} to clear the "
                                             f"{self.b['margin_floor']}x margin (floor A${floor:.2f})")
        return price, src, detail

    # ---- made in / composition ---------------------------------------------
    def made_in_of(self, p):
        if p["made_in"]:
            return p["made_in"], f"{p['title']}: custom.made_in"
        if p["countries"] and len(p["countries"]) == 1:
            iso = next(iter(p["countries"]))
            name = self.b["country_names"].get(iso)
            if name:
                return name, f"{p['title']}: Variant Country of Origin {iso} on every variant"
        return None, None

    def made_in(self, code, product=None):
        if product is not None:
            v, why = self.made_in_of(product)
            if v:
                return v, "catalog", why
        sib = next(((v, f"same style+fabric {why}") for p in self.siblings(code, "fabric")
                    for v, why in [self.made_in_of(p)] if v), None)
        row = self.cart_row(code)
        if row and row["made_in"]:
            return self._cart_or_agreed(row, row["made_in"], f"Made In '{row['made_in_raw']}'", sib)
        if sib:
            return sib[0], "catalog", sib[1]
        return self.b["unknown"], "guess", "made-in not known for this style"

    def composition(self, code):
        sib = next(((m.group(1).strip(), f"same style+fabric {p['title']} description")
                    for p in self.siblings(code, "fabric")
                    for m in [re.search(r"Composition:\s*([^<\n]+)", p["body"] or "")] if m), None)
        row = self.cart_row(code)
        if row and row["materials"]:
            return self._cart_or_agreed(row, row["materials"], "Materials", sib)
        if sib:
            return sib[0], "catalog", sib[1]
        return self.b["unknown"], "guess", "no composition source (no linesheet supplied)"

    def _cart_or_agreed(self, row, value, what, sib):
        """The cart's value; green only when the live same style+fabric says the same.
        Cart text is supplier text bound for Body HTML: a value holding '<' or '>' is
        markup, so it is not used and the cell is red."""
        if "<" in value or ">" in value:
            return self.b["unknown"], "guess", self.cart_why(row, f"{what} '{value}' holds '<' or '>' "
                                                                  "(markup, not text); not used, retype it")
        if sib and " ".join(html.unescape(sib[0]).split()).casefold() == " ".join(value.split()).casefold():
            return sib[0], "catalog", f"{sib[1]}; " + self.cart_why(row, f"{what} agrees")
        return value, "cart", self.cart_why(row, what) + (f"; {sib[1]} says '{sib[0]}'" if sib else "")

    # ---- SKU ----------------------------------------------------------------
    def next_sku(self):
        prefix, width = self.b["sku_prefix"], self.b["sku_digits"]
        if self._next_sku is None:
            self._next_sku = self.cat.max_sku_number(prefix) + 1
        while True:
            sku = f"{prefix}{self._next_sku:0{width}d}"
            self._next_sku += 1
            if sku not in self.cat.skus and sku not in self.used_skus:
                self.used_skus.add(sku)
                return sku, "rule", f"next free {prefix} number after the catalog's highest"

    # ---- fixed text ---------------------------------------------------------
    def title_tag(self, title):
        return self.b["title_tag"].format(vendor=self.b["vendor"], title=title)

    def body_html(self, code, composition, made_in, composition_is_html=False):
        """Fill the brand's Body HTML template. Text is escaped; a composition read out
        of a live product's Body HTML (source catalog) is already HTML and is not."""
        comp = composition if composition_is_html else html.escape(str(composition), quote=False)
        return self.b["body_html"].format(code=html.escape(str(code), quote=False), composition=comp,
                                          made_in=html.escape(str(made_in), quote=False))

    def handle_taken(self, title):
        return slug(title) in self.cat.handles


def tidy_colour(desc):
    """'Off white' -> 'Off White', 'Berry/ Clear White' -> 'Berry/Clear White'."""
    t = re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", str(desc or "").strip()))
    return re.sub(r"(^|[\s/-])(\w)", lambda m: m.group(1) + m.group(2).upper(), t)


def _created(p):
    return p.get("created_key", 0)
