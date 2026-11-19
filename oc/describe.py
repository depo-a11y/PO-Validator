"""A NEW product's Body HTML in the house shape (Colwyn, 2026-11-13: "C, exactly as live"), drafted with no key:

    <p>{VENDOR}'s {Title} is a {adjective} {item}. {Sentence 2}.</p>
    <ul>
    <li>Composition: {composition, the order-time text in Title Case}</li>
      <li>{feature}</li>            (the house has 4 to 6, 5 the mode)
    <li>Made in {Country}</li>
    </ul><br><p>SKU: {manufacture code}</p>

It writes only what an order-time fact grounds, and leaves out what none does:
- the vendor as the config stores it (capitals, as live since May 2026) and the Title verbatim, a gender word in it
  included (the storefront's title-in-description check needs it). No other gender or season word anywhere
  (Colwyn, 2026-10-13): a drafted part holding one is dropped;
- the item noun from the Type (ITEMS, the gender word left out; "pair of" for what is sold in pairs). A Type ITEMS
  does not know gives no intro, and the cell is red;
- an adjective only where a phrase of ADJECTIVES is printed in this product's own OC description (a material or
  finish also in its fabric name), and only on a kind of product it can describe (a waist on bottoms, dresses and
  outerwear; a neckline or sleeves on tops, dresses and outerwear; a leg on bottoms). Never from the product class or
  the cart's Style Name, which name a category ("Crewneck"), and never two of one group (two necklines). No
  order-time field is a second sentence, so the tool writes none: the intro may be one sentence;
- Composition from the first usable source in this order: the OC line's composition, its fabrication (the fabric in
  words, where a JOOR export prints no composition), the cart's Materials, the live same style+fabric product's
  Composition line. The text is taken whole (lesson composition-line-must-be-read-whole), whitespace collapsed and
  written in Title Case, as live Composition lines are (str.title; the printed text is in the Why); a live product's
  line is kept as it is. A missing, placeholder, garbled, coded or cut-off value is never written and the cell is red;
  sources that disagree are both named in the Why;
- feature bullets, each from a fact: the title's colour words ("{Colour} colourway", AU spelling), one material
  given for a labelled part of the composition ("Rubber sole"), and FEATURES phrases, under the adjectives' rules;
- Made in only for a country the tool knows (the brand config's country_names, then COUNTRIES), never "Made in -".

The cell is never green: amber "tool draft, check", or the least trusted of its parts, so a red title or Type keeps
it red, and red when the Composition line or the intro is missing. The Why names each source and every gap against
the house shape. AI prose (oc/agent/describe.py) may fill the adjective, second sentence and bullets the tool could
not ground; nothing else. An AI second sentence whose subject is another kind of product than the item noun (a
sweater where the Type says sweatshirt) keeps the body red, and so does a title naming another kind than the item
noun (a cap the Type calls a T-shirt): the Type and the title are checked, never changed.

The description is not the PO's Body HTML, which keeps the brand's template (Colwyn, 2026-11-16, option B: "Separate
import"). It is drafted beside it, one Review row per product (review_rows), which the workbook writes as its
Descriptions sheet, and finalize turns into the separate description import (oc/descimport.py).
"""
import html
import re
import unicodedata

from .resolve import AI_DRAFT, COLOUR, RANK, RED, TOOL_DRAFT
from .util import title_colour

MAX_BULLETS = 6
HOUSE_BULLETS = (4, 6)
GENDERS = ("MEN", "WOMEN", "UNISEX")

# Type without its gender -> the item noun; a tuple is (default, {a word in the title: the noun it means}).
ITEMS = {
    "ACCS BELTS": "belt", "ACCS GLOVES": "pair of gloves", "ACCS HATS BEANIES": "beanie", "ACCS HATS CAPS": "cap",
    "ACCS HATS HATS": "hat", "ACCS HOME": "home piece", "ACCS JEWELRY BRACELETS": "bracelet",
    "ACCS JEWELRY EARRINGS": ("earring", {"earrings": "pair of earrings"}),
    "ACCS JEWELRY PENDANTS & NECKLACES": ("necklace", {"pendant": "pendant"}), "ACCS JEWELRY RINGS": "ring",
    "ACCS KEYRINGS & CHAIN": ("keyring", {"chain": "chain"}), "ACCS LOCKS": "lock", "ACCS SCARVES": "scarf",
    "ACCS SOCKS": "pair of socks", "ACCS STRAPS": "strap", "ACCS SUNGLASSES": "pair of sunglasses",
    "ACCS TECH": "tech accessory", "ACCS TOYS": "toy", "ACCS UMBRELLAS": "umbrella",
    "ACCS WALLETS & POUCHES": ("wallet", {"pouch": "pouch", "card holder": "card holder",
                                          "cardholder": "cardholder", "coin purse": "coin purse"}),
    "BAGS BACKPACKS": "backpack", "BAGS BELT BAGS": "belt bag", "BAGS BUCKET BAGS": "bucket bag",
    "BAGS CLUTCHES": "clutch", "BAGS MINI & CROSSBODY": ("crossbody bag", {"mini": "mini bag"}),
    "BAGS SHOPPER & TOTE BAGS": ("tote bag", {"shopper": "shopper"}), "BAGS SHOULDER BAGS": "shoulder bag",
    "CLOTHING ACTIVEWEAR & UNDERWEAR": ("piece", {"briefs": "pair of briefs", "boxers": "pair of boxers",
                                                  "bra": "bra", "leggings": "pair of leggings",
                                                  "bodysuit": "bodysuit", "tank": "tank top"}),
    "CLOTHING COATS": "coat", "CLOTHING COATS DOUBLE BREASTED": "double-breasted coat",
    "CLOTHING COATS FUR & SHEARLING": "coat", "CLOTHING COATS KNEE-LENGTH": "knee-length coat",
    "CLOTHING COATS PARKA": "parka", "CLOTHING COATS PUFFER": "puffer coat",
    "CLOTHING COATS SHEARLING": "shearling coat", "CLOTHING COATS TRENCH": "trench coat",
    "CLOTHING COATS WOOL": "wool coat", "CLOTHING DRESSES": "dress", "CLOTHING HOODIES": "hoodie",
    "CLOTHING JACKETS BLAZER": "blazer", "CLOTHING JACKETS BOMBER": "bomber jacket",
    "CLOTHING JACKETS BOMBER JACKETS": "bomber jacket", "CLOTHING JACKETS DENIM": "denim jacket",
    "CLOTHING JACKETS DENIM JACKETS": "denim jacket", "CLOTHING JACKETS DOWN JACKETS": "down jacket",
    "CLOTHING JACKETS LEATHER": "leather jacket", "CLOTHING JACKETS LEATHER JACKETS": "leather jacket",
    "CLOTHING JACKETS LIGHTWEIGHT": "lightweight jacket", "CLOTHING JACKETS PUFFER": "puffer jacket",
    "CLOTHING JACKETS SPORTS & WINDBREAKERS": ("jacket", {"windbreaker": "windbreaker", "track": "track jacket"}),
    "CLOTHING JACKETS SUIT & BLAZERS": ("blazer", {"suit": "suit jacket"}),
    "CLOTHING JACKETS TWEED": "tweed jacket", "CLOTHING JACKETS VEST": "vest", "CLOTHING JACKETS VESTS": "vest",
    "CLOTHING JEANS": "pair of jeans", "CLOTHING JEANS BAGGY JEANS": "pair of baggy jeans",
    "CLOTHING JEANS BARREL JEANS": "pair of barrel jeans", "CLOTHING JEANS BOOTCUT JEANS": "pair of bootcut jeans",
    "CLOTHING JEANS FLARED JEANS": "pair of flared jeans",
    "CLOTHING JEANS HIGH WAISTED JEANS": "pair of high-waisted jeans",
    "CLOTHING JEANS RELAXED JEANS": "pair of relaxed jeans", "CLOTHING JEANS SKINNY JEANS": "pair of skinny jeans",
    "CLOTHING JEANS SLIM JEANS": "pair of slim jeans", "CLOTHING JEANS STRAIGHT JEANS": "pair of straight jeans",
    "CLOTHING JEANS TAPERED JEANS": "pair of tapered jeans", "CLOTHING JEANS WIDE JEANS": "pair of wide jeans",
    "CLOTHING KNITWEARS CARDIGANS": "cardigan", "CLOTHING KNITWEARS CROPPED KNITWEAR": "cropped knit",
    "CLOTHING KNITWEARS POLO": "knitted polo", "CLOTHING KNITWEARS SWEATERS": "sweater",
    "CLOTHING KNITWEARS TURTLENECK": "turtleneck", "CLOTHING KNITWEARS TWEED": "tweed knit",
    "CLOTHING PANTS CARGOS": "pair of cargo pants", "CLOTHING PANTS CASUAL PANTS": "pair of pants",
    "CLOTHING PANTS CHINOS": "pair of chinos", "CLOTHING PANTS CROPPED": "pair of cropped pants",
    "CLOTHING PANTS DRAWSTRING": "pair of drawstring pants", "CLOTHING PANTS LEGGINGS": "pair of leggings",
    "CLOTHING PANTS SWEATPANTS": "pair of sweatpants", "CLOTHING PANTS TRACK PANTS": "pair of track pants",
    "CLOTHING PANTS TROUSERS": "pair of trousers", "CLOTHING PANTS WIDE": "pair of wide-leg pants",
    "CLOTHING POLOS": "polo shirt", "CLOTHING SHIRTS": "shirt", "CLOTHING SHIRTS DENIM": "denim shirt",
    "CLOTHING SHIRTS KNIT": "knit shirt", "CLOTHING SHIRTS LONG SLEEVE": "long-sleeve shirt",
    "CLOTHING SHIRTS SHORT SLEEVE": "short-sleeve shirt", "CLOTHING SHORTS": "pair of shorts",
    "CLOTHING SKIRTS": "skirt", "CLOTHING SWEATSHIRTS": "sweatshirt", "CLOTHING T-SHIRTS": "T-shirt",
    "CLOTHING T-SHIRTS LONG SLEEVE": "long-sleeve T-shirt", "CLOTHING T-SHIRTS SHORT SLEEVE": "T-shirt",
    "CLOTHING T-SHIRTS TANK TOPS": "tank top", "CLOTHING TANK TOPS": "tank top", "CLOTHING TOPS BLOUSES": "blouse",
    "CLOTHING TOPS POLO": "polo top", "CLOTHING TOPS SHIRTS": "shirt", "CLOTHING TOPS TANK TOP": "tank top",
    "SHOES BALLERINAS": "pair of ballerinas", "SHOES BOOTS": "pair of boots",
    "SHOES DERBY & OXFORDS": ("pair of derby shoes", {"oxford": "pair of oxford shoes",
                                                      "oxfords": "pair of oxford shoes"}),
    "SHOES ESPADRILLES": "pair of espadrilles", "SHOES FLATS": "pair of flats", "SHOES LOAFERS": "pair of loafers",
    "SHOES MARY JANE": "pair of Mary Janes", "SHOES PUMPS": "pair of pumps", "SHOES SANDALS": "pair of sandals",
    "SHOES SNEAKERS": "pair of sneakers",
}

# What kind of thing a Type is: a claim about a waist, a leg or a neckline is written only on a kind that has one.
GARMENT = frozenset({"top", "bottom", "dress", "outerwear"})
UPPER = frozenset({"top", "dress", "outerwear"})
WAIST = frozenset({"bottom", "dress", "outerwear"})
ANY = GARMENT | {"footwear", "bag", "accessory"}
DESC, MATERIAL = ("description",), ("description", "fabric_name")   # the fields a claim may be read from


def _e(keys, result, kinds=ANY, fields=DESC, group=None):
    return keys, result, frozenset(kinds), fields, group


# A phrase printed in this product's own description (or, for a material or finish, its fabric name) -> the
# adjective it grounds, only on the kinds listed. Never the product class or the cart's Style Name: they name a
# category ("Crewneck"), not how the product is made. Two phrases of one group (two necklines, two leg shapes)
# contradict each other, so neither is written.
ADJECTIVES = (
    _e(("relaxed fit", "relaxed-fit"), "relaxed-fit", GARMENT, group="fit"),
    _e(("regular fit", "regular-fit"), "regular-fit", GARMENT, group="fit"),
    _e(("slim fit", "slim-fit"), "slim-fit", GARMENT, group="fit"),
    _e(("oversized", "oversize"), "oversized", GARMENT, group="fit"), _e(("boxy",), "boxy", GARMENT, group="fit"),
    _e(("cropped",), "cropped", GARMENT), _e(("padded",), "padded", GARMENT | {"bag"}),
    _e(("quilted",), "quilted", GARMENT | {"bag"}), _e(("knitted", "knit"), "knitted", GARMENT | {"accessory"}, MATERIAL),
    _e(("woven",), "woven", ANY, MATERIAL), _e(("ribbed",), "ribbed", GARMENT | {"accessory"}),
    _e(("washed",), "washed", GARMENT | {"bag", "accessory"}, MATERIAL), _e(("distressed",), "distressed"),
    _e(("embroidered",), "embroidered"), _e(("printed",), "printed"), _e(("reversible",), "reversible", ANY - {"footwear"}),
    _e(("hooded",), "hooded", {"top", "outerwear"}), _e(("zipped", "zip-up", "zip up"), "zip-up", GARMENT | {"bag"}),
    _e(("belted",), "belted", WAIST), _e(("pleated",), "pleated", GARMENT | {"bag"}),
    _e(("waxed",), "waxed", ANY, MATERIAL), _e(("coated",), "coated", ANY, MATERIAL),
    _e(("brushed",), "brushed", ANY, MATERIAL),
    _e(("double breasted", "double-breasted"), "double-breasted", UPPER, group="breasted"),
    _e(("single breasted", "single-breasted"), "single-breasted", UPPER, group="breasted"),
    _e(("wide leg", "wide-leg"), "wide-leg", {"bottom"}, group="leg"),
    _e(("straight leg", "straight-leg"), "straight-leg", {"bottom"}, group="leg"),
    _e(("tapered",), "tapered", {"bottom"}, group="leg"), _e(("flared",), "flared", {"bottom"}, group="leg"),
    _e(("high waisted", "high-waisted"), "high-waisted", {"bottom"}),
    _e(("sleeveless",), "sleeveless", UPPER, group="sleeve"),
    _e(("long sleeve", "long-sleeve", "long sleeved", "long-sleeved"), "long-sleeve", UPPER, group="sleeve"),
    _e(("short sleeve", "short-sleeve", "short sleeved", "short-sleeved"), "short-sleeve", UPPER, group="sleeve"),
    _e(("lightweight",), "lightweight", ANY, MATERIAL),
)
# The same, for feature bullets.
FEATURES = (
    _e(("relaxed fit", "relaxed-fit"), "Relaxed fit", GARMENT, group="fit"),
    _e(("regular fit", "regular-fit"), "Regular fit", GARMENT, group="fit"),
    _e(("slim fit", "slim-fit"), "Slim fit", GARMENT, group="fit"),
    _e(("oversized", "oversize"), "Oversized fit", GARMENT, group="fit"),
    _e(("boxy",), "Boxy fit", GARMENT, group="fit"), _e(("cropped",), "Cropped length", GARMENT),
    _e(("padded",), "Padded construction", GARMENT | {"bag"}),
    _e(("quilted",), "Quilted construction", GARMENT | {"bag"}),
    _e(("ribbed",), "Ribbed texture", GARMENT | {"accessory"}),
    _e(("garment dyed", "garment-dyed"), "Garment-dyed finish", GARMENT, MATERIAL),
    _e(("washed",), "Washed finish", GARMENT | {"bag", "accessory"}, MATERIAL),
    _e(("distressed",), "Distressed finish"), _e(("embroidered", "embroidery"), "Embroidered detailing"),
    _e(("logo",), "Logo detailing"), _e(("graphic",), "Graphic print", GARMENT | {"bag", "accessory"}),
    _e(("reversible",), "Reversible design", ANY - {"footwear"}),
    _e(("zip", "zipped", "zip-up", "zip up", "full zip"), "Zip fastening"),
    _e(("buttoned", "button-up", "button up", "button down", "button-down"), "Button fastening", GARMENT),
    _e(("drawstring",), "Drawstring detail"),
    _e(("double breasted", "double-breasted"), "Double-breasted front", UPPER, group="breasted"),
    _e(("v neck", "v-neck"), "V-neckline", UPPER, group="neck"),
    _e(("crewneck", "crew neck", "crew-neck"), "Crew neckline", UPPER, group="neck"),
    _e(("turtleneck", "roll neck", "rollneck"), "Roll neck", UPPER, group="neck"),
    _e(("mock neck", "mockneck"), "Mock neck", UPPER, group="neck"),
    _e(("henley",), "Henley placket", UPPER, group="neck"),
    _e(("pleated",), "Pleated detailing", GARMENT | {"bag"}), _e(("belted",), "Belted waist", WAIST),
    _e(("waxed",), "Waxed finish", ANY, MATERIAL), _e(("coated",), "Coated finish", ANY, MATERIAL),
    _e(("brushed",), "Brushed finish", ANY, MATERIAL),
    _e(("wide leg", "wide-leg"), "Wide leg", {"bottom"}, group="leg"),
    _e(("straight leg", "straight-leg"), "Straight leg", {"bottom"}, group="leg"),
    _e(("tapered",), "Tapered leg", {"bottom"}, group="leg"), _e(("flared",), "Flared leg", {"bottom"}, group="leg"),
    _e(("high waisted", "high-waisted"), "High-waisted fit", {"bottom"}),
    _e(("long sleeve", "long-sleeve", "long sleeved", "long-sleeved"), "Long sleeves", UPPER, group="sleeve"),
    _e(("short sleeve", "short-sleeve", "short sleeved", "short-sleeved"), "Short sleeves", UPPER, group="sleeve"),
    _e(("sleeveless",), "Sleeveless cut", UPPER, group="sleeve"),
    _e(("lightweight",), "Lightweight feel", ANY, MATERIAL),
)
# A noun naming what a product is -> its kind; an AI second sentence whose subject is of another kind than the Type's
# item noun contradicts the intro, so the body stays red ("top", "tank" and the like are left out: also parts).
ITEM_KINDS = {k: kind for kind, words in (
    ("outerwear", "jacket jackets jkt coat coats parka parkas blazer blazers bomber bombers trench trenches blouson "
                  "blousons windbreaker windbreakers anorak anoraks gilet gilets vest vests puffer puffers overcoat"),
    ("knit", "sweater sweaters jumper jumpers pullover pullovers cardigan cardigans turtleneck turtlenecks knit knits"),
    ("top", "shirt shirts t-shirt t-shirts tee tees polo polos blouse blouses sweatshirt sweatshirts hoodie hoodies "
            "henley henleys"),
    ("bottom", "pants pnt trousers jeans shorts skirt skirts chinos leggings joggers sweatpants cargos"),
    ("dress", "dress dresses"),
    ("footwear", "sneaker sneakers boot boots sandal sandals loafer loafers shoe shoes pump pumps mule mules ballerina "
                 "ballerinas flats espadrilles derby derbies oxford oxfords trainer trainers slide slides"),
    ("bag", "bag bags tote totes backpack backpacks clutch clutches shopper shoppers"),
    ("accessory", "belt belts cap caps hat hats beanie beanies scarf scarves glove gloves sock socks ring rings "
                  "necklace necklaces bracelet bracelets earring earrings wallet wallets keyring keyrings sunglasses "
                  "pouch pouches"),
) for k in words.split()}
# Singular forms a title uses for a garment sold as a pair ('Knit Short', 'Cargo Pant'); read in titles only, where the
# head noun is the last word, never in a sentence ('a short jacket'). An overshirt is left out: stores type it as a
# shirt or as a jacket.
TITLE_KINDS = dict(ITEM_KINDS, short="bottom", pant="bottom", trouser="bottom", legging="bottom", jogger="bottom",
                   sweatpant="bottom", cargo="bottom")
ITEM_MISMATCH = "the AI's text names a different item than the Type: check the Type"
TITLE_MISMATCH = "the title names a different kind of product than the Type: check the Type"
TEXT_FEATURES_MAX = 3
# A labelled part of a composition -> the bullet's last word ("Rubber sole"); adidas footwear prints part codes.
PARTS = {"upper": "upper", "lining": "lining", "main lining": "lining", "inner lining": "lining", "sole": "sole",
         "outsole": "sole", "insole": "insole", "metallic parts": "hardware", "metal parts": "hardware",
         "hardware": "hardware"}
PART_ORDER = ("upper", "lining", "insole", "sole", "hardware")
SECTION = re.compile(r"(?i)(?:^|(?<=[\s,;/\-]))(" + "|".join(sorted(map(re.escape, PARTS), key=len, reverse=True))
                     + r")\s*:\s*(.*?)\s*(?=(?:[\s,;/\-]+[A-Za-z][A-Za-z ]{0,24}\s*:)|$)")
ONE_MATERIAL = re.compile(r"(?i)^100\s*%\s*([^\W\d_][^\W\d_ \-]*(?:[ \-][^\W\d_]+){0,3})[\s.,;\-]*$")
ADIDAS_PARTS = re.compile(r"^U:([A-Z]{2}(?:,[A-Z]{2})*)\s+L:([A-Z]{2}(?:,[A-Z]{2})*)\s+S:([A-Z]{2}(?:,[A-Z]{2})*)$")
ADIDAS_CODES = {"TE": "Textile", "LE": "Leather", "RU": "Rubber", "SY": "Synthetic"}

# Composition values that are no composition: never written (lesson composition-line-must-be-read-whole: a literal
# '-' passes every non-blank check).
PLACEHOLDERS = {"", "-", "–", "—", ".", "?", "0", "n/a", "na", "nan", "none", "null", "tbc", "tba", "tbd", "unknown"}
OOXML_ESCAPE = re.compile(r"_x[0-9A-Fa-f]{4}_")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")

GENDER_WORDS = re.compile(r"(?i)(?<![\w-])(?:men|mens|men's|women|womens|women's|woman|woman's|man|man's|ladies|"
                          r"lady|female|male|unisex|menswear|womenswear|gentlemen|gents|boys?|girls?|"
                          r"gender[- ]neutral)(?![\w-])")
SEASON_WORDS = re.compile(r"(?i)(?<![\w/])(?:s/s|f/w|a/w|ss\s?\d{2}|fw\s?\d{2}|aw\s?\d{2}|pre[- ]?fall|resort|cruise|"
                          r"spring|summer|autumn|winter|fall[ /-]winter|seasons?|(?:19|20)\d{2})(?![\w/])")

COUNTRIES = (   # (name written in "Made in", ISO 2, ISO 3)
    ("Albania", "AL", "ALB"), ("Armenia", "AM", "ARM"), ("Australia", "AU", "AUS"), ("Austria", "AT", "AUT"),
    ("Bangladesh", "BD", "BGD"), ("Belarus", "BY", "BLR"), ("Belgium", "BE", "BEL"),
    ("Bosnia and Herzegovina", "BA", "BIH"), ("Brazil", "BR", "BRA"), ("Bulgaria", "BG", "BGR"),
    ("Cambodia", "KH", "KHM"), ("Canada", "CA", "CAN"), ("China", "CN", "CHN"), ("Colombia", "CO", "COL"),
    ("Croatia", "HR", "HRV"), ("Czech Republic", "CZ", "CZE"), ("Denmark", "DK", "DNK"), ("Egypt", "EG", "EGY"),
    ("Estonia", "EE", "EST"), ("Ethiopia", "ET", "ETH"), ("Finland", "FI", "FIN"), ("France", "FR", "FRA"),
    ("Georgia", "GE", "GEO"), ("Germany", "DE", "DEU"), ("Greece", "GR", "GRC"), ("Hong Kong", "HK", "HKG"),
    ("Hungary", "HU", "HUN"), ("India", "IN", "IND"), ("Indonesia", "ID", "IDN"), ("Ireland", "IE", "IRL"),
    ("Italy", "IT", "ITA"), ("Japan", "JP", "JPN"), ("Kenya", "KE", "KEN"), ("Laos", "LA", "LAO"),
    ("Latvia", "LV", "LVA"), ("Lithuania", "LT", "LTU"), ("Madagascar", "MG", "MDG"), ("Malaysia", "MY", "MYS"),
    ("Mauritius", "MU", "MUS"), ("Mexico", "MX", "MEX"), ("Moldova", "MD", "MDA"), ("Mongolia", "MN", "MNG"),
    ("Morocco", "MA", "MAR"), ("Myanmar", "MM", "MMR"), ("Nepal", "NP", "NPL"), ("Netherlands", "NL", "NLD"),
    ("New Zealand", "NZ", "NZL"), ("North Macedonia", "MK", "MKD"), ("Norway", "NO", "NOR"),
    ("Pakistan", "PK", "PAK"), ("Peru", "PE", "PER"), ("Philippines", "PH", "PHL"), ("Poland", "PL", "POL"),
    ("Portugal", "PT", "PRT"), ("Romania", "RO", "ROU"), ("Serbia", "RS", "SRB"), ("Slovakia", "SK", "SVK"),
    ("Slovenia", "SI", "SVN"), ("South Korea", "KR", "KOR"), ("Spain", "ES", "ESP"), ("Sri Lanka", "LK", "LKA"),
    ("Sweden", "SE", "SWE"), ("Switzerland", "CH", "CHE"), ("Taiwan", "TW", "TWN"), ("Thailand", "TH", "THA"),
    ("Tunisia", "TN", "TUN"), ("Turkey", "TR", "TUR"), ("Ukraine", "UA", "UKR"), ("United Kingdom", "GB", "GBR"),
    ("United States", "US", "USA"), ("Vietnam", "VN", "VNM"),
)
COUNTRY_NAMES = {k: name for name, a2, a3 in COUNTRIES for k in (name.upper(), a2, a3)}
COUNTRY_NAMES.update({"KOREA": "South Korea", "REPUBLIC OF KOREA": "South Korea", "UK": "United Kingdom",
                      "GREAT BRITAIN": "United Kingdom", "U.S.A.": "United States", "TURKIYE": "Turkey",
                      "TÜRKIYE": "Turkey", "VIET NAM": "Vietnam", "ITALIA": "Italy", "ESPANA": "Spain",
                      "ESPAÑA": "Spain", "CZECHIA": "Czech Republic"})


# ---- small helpers ------------------------------------------------------------------------
def squash(v):
    return " ".join(str(v if v is not None else "").split())


def banned(text):
    """'gender word' or 'season word' when `text` holds one (Colwyn's rule for the copy), else ''."""
    if GENDER_WORDS.search(text or ""):
        return "gender word"
    if SEASON_WORDS.search(text or ""):
        return "season word"
    return ""


def type_rest(ptype):
    """'MEN CLOTHING COATS' -> 'CLOTHING COATS': the Type with its gender word left out."""
    words = squash(ptype).upper().split(" ")
    return " ".join(words[1:] if words and words[0] in GENDERS else words)


def item_noun(ptype, title):
    """The item noun the Type means, a title word choosing among a Type's alternatives; None for a Type ITEMS lacks."""
    entry = ITEMS.get(type_rest(ptype))
    if entry is None or isinstance(entry, str):
        return entry
    default, alts = entry
    t = squash(title).lower()
    for word in sorted(alts, key=len, reverse=True):
        if re.search(r"(?<![\w-])" + re.escape(word) + r"(?![\w-])", t):
            return alts[word]
    return default


LETTER_AN = set("aefhilmnorsx")   # letters whose name starts with a vowel sound: an L-shaped, an X-ray, a U-neck


def article(phrase):
    """'a' or 'an' by sound: a letter read as a letter by its name (a U-neck, an L-shaped), a "you" or "wun" sound
    takes 'a' (a unisex, a euro-style, a one-piece), a silent h takes 'an' (an hour)."""
    w = phrase.split(" ", 1)[0].lower()
    letter = re.match(r"([a-z])(?:-|$)", w)
    if letter:
        return "an" if letter.group(1) in LETTER_AN else "a"
    if re.match(r"(?:uni|use|usu|uti|ura|ure|uro|eu|ewe|one|once)", w):
        return "a"
    return "an" if w[:1] in "aeiou" or w.startswith(("hour", "honest", "heir")) else "a"


def country(value, brand):
    """The country name 'Made in' writes for a printed value (a code, a name, 'Made in Italy'), else None."""
    v = re.sub(r"(?i)^made\s+in\s+", "", squash(value)).strip(" .")
    if not v:
        return None
    names = {str(k).upper(): str(n) for k, n in ((brand or {}).get("country_names") or {}).items()}
    return names.get(v.upper()) or COUNTRY_NAMES.get(v.upper())


def house_case(text):
    """A composition as the house writes it: the order-time text, whitespace collapsed, in Title Case
    (str.title: '100% COTTON-BCI' -> '100% Cotton-Bci', "D'ACME" -> "D'Acme", the capital after the apostrophe
    kept as live writes it). Live Composition lines are the source text so cased."""
    return squash(text).title()


def composition_problem(text):
    """Why a composition value cannot be written as the Composition line, else ''."""
    raw = unicodedata.normalize("NFC", html.unescape(str(text if text is not None else "")))
    t = squash(raw)
    if t.casefold() in PLACEHOLDERS:
        return "is blank or a placeholder"
    if OOXML_ESCAPE.search(raw) or CONTROL.search(raw):
        return "is garbled in the source file (escape codes or control characters)"
    if "<" in t or ">" in t:
        return "holds '<' or '>' (markup, not text)"
    if not re.search(r"[^\W\d_]{3}", t):
        return "is written in codes, not words"
    total = sum(float(x.replace(",", ".")) for x in PERCENT.findall(t))
    if PERCENT.search(t) and total < 99.5:
        return f"adds up to {total:g}%, so the source may have cut it off"
    if re.search(r"[/+,\-]\s*\d+(?:[.,]\d+)?$", t):
        return "ends in a number with no material, so the source may have cut it off"
    return ""


def type_kind(ptype):
    """'top', 'bottom', 'dress', 'outerwear', 'footwear', 'bag' or 'accessory' for a Type, else None."""
    r = type_rest(ptype)
    for prefix, kind in (("SHOES", "footwear"), ("BAGS", "bag"), ("ACCS", "accessory"), ("CLOTHING DRESSES", "dress"),
                         ("CLOTHING COATS", "outerwear"), ("CLOTHING JACKETS", "outerwear"), ("CLOTHING JEANS", "bottom"),
                         ("CLOTHING PANTS", "bottom"), ("CLOTHING SHORTS", "bottom"), ("CLOTHING SKIRTS", "bottom"),
                         ("CLOTHING", "top")):
        if r.startswith(prefix):
            return kind
    return None


def phrases(table, texts, limit, kind):
    """([(result, field, phrase)], [groups left out]): each table entry for this kind of product whose phrase is
    printed, as whole words, in one of the fields it may be read from, in the order the texts print them, one per
    result, at most `limit`. A group with two different results (a crew neck and a V-neck) is left out whole."""
    found = []
    for field, text in texts:
        low = squash(text).lower()
        for keys, result, kinds, fields, group in table:
            if field not in fields or kind not in kinds:
                continue
            for k in keys:
                m = re.search(r"(?<![\w-])" + re.escape(k) + r"(?![\w-])", low)
                if m:
                    found.append((m.start(), field, result, k, group))
                    break
    groups = {}
    for _, _, result, _, group in found:
        if group:
            groups.setdefault(group, set()).add(result)
    clash = sorted(g for g, rs in groups.items() if len(rs) > 1)
    out, seen = [], set()
    for field in dict.fromkeys(f for f, _ in texts):
        for _, f, result, k, group in sorted(x for x in found if x[1] == field):
            if result not in seen and group not in clash:
                seen.add(result)
                out.append((result, f, k))
    return out[:limit], clash


def item_kind(text, words=None, last=False, table=None):
    """The kind of the first product noun among `words` words of `text` (all of it when None), or with `last` of the
    last one, the head of a name ('Belt Bag' is a bag), else None."""
    table = table or ITEM_KINDS
    toks = re.findall(r"[a-z]+(?:-[a-z]+)*", str(text or "").lower())
    kinds = [table[t] for t in (toks[:words] if words else toks) if t in table]
    return (kinds[-1] if last else kinds[0]) if kinds else None


def item_mismatch(sentence, item):
    """True when a second sentence's subject (a product noun in its first six words) is another kind than the item
    noun's."""
    mine, theirs = item_kind(item, last=True), item_kind(sentence, 6)
    return bool(mine and theirs and mine != theirs)


def title_kind(title):
    """The kind of product the title's name part names (the words before ' in <colour>'), else None. In a
    'CATEGORY - NAME' string the name after ' - ' is read first ('KNIT SWEATSHIRT - TRACK JKT' is a jacket). A trailing
    'Short' after a plural noun is its length ('Socks Short' are socks); after anything else it is shorts."""
    parts = (title_colour(title)[0] or "").split(" - ")
    for part in parts[1:] + parts[:1]:
        toks = re.findall(r"[a-z]+(?:-[a-z]+)*", part.lower())
        if len(toks) >= 2 and toks[-1] == "short" and toks[-2] in ITEM_KINDS and toks[-2].endswith("s"):
            return ITEM_KINDS[toks[-2]]   # 'Socks Short': a length word after a plural noun, not a pair of shorts
        kind = item_kind(part, last=True, table=TITLE_KINDS)
        if kind:
            return kind
    return None


def part_bullets(value):
    """[(bullet, part)] for the labelled parts of a composition given as one material: 'Lining: 100% Cupro' ->
    'Cupro lining'; adidas 'U:LE L:TE S:RU' -> 'Leather upper', 'Textile lining', 'Rubber sole'."""
    t = unicodedata.normalize("NFC", squash(html.unescape(str(value or ""))))
    out = {}
    m = ADIDAS_PARTS.match(t)
    if m:
        for part, codes in zip(("upper", "lining", "sole"), m.groups()):
            name = ADIDAS_CODES.get(codes)
            if name:
                out[part] = f"{name} {part}"
    else:
        for label, rest in SECTION.findall(t):
            part = PARTS[label.lower()]
            one = ONE_MATERIAL.match(rest)
            if one and part not in out:
                material = one.group(1).strip()
                out[part] = f"{material[:1].upper()}{material[1:].lower()} {part}"
    return [(out[p], p) for p in PART_ORDER if p in out]


# ---- the facts of one NEW product ----------------------------------------------------------
def facts(n, R):
    """What one of draft.build's pending NEW products gives its description: the title and Type cells, and each
    order-time candidate (value, where, source) in priority order. The OC line first (read by AI: amber), then the
    resolver's own composition and made in (the cart's Materials and Made In, or the live same style+fabric
    product's), then the text fields an adjective or feature may be read from."""
    from .resolve import AI_OC
    l, code = n["l"], n["code"]
    oc_src = AI_OC if l.get("reader") == "ai" else "cart" if l.get("reader") == "cart" else "OC"
    where = f"the OC line ({l.get('doc')} line {l.get('line_no')})"
    comp, made = [], []
    if squash(l.get("composition")):
        comp.append(dict(value=str(l["composition"]), where=where, source=oc_src, html=False))
    if squash(l.get("fabrication")):   # the fabric in words, where the order prints no composition (JOOR exports)
        comp.append(dict(value=str(l["fabrication"]), where=f"{where}, fabrication", source=oc_src, html=False,
                         fallback=True))
    value, src, why = n["comp"]
    if src != "guess":
        comp.append(dict(value=str(value), where=why, source=src, html=src == "catalog"))
    origin = l.get("made_in") or l.get("origin")
    if isinstance(origin, dict):
        vals = {squash(v) for v in origin.values() if squash(v)}
        origin = vals.pop() if len(vals) == 1 else None
    if squash(origin):
        made.append(dict(value=str(origin), where=where, source=oc_src))
    value, src, why = n["made"]
    if src != "guess" and squash(value) and squash(value) != R.b.get("unknown"):
        made.append(dict(value=str(value), where=why, source=src))
    row = R.cart_row(code)
    text = [(k, l.get(k)) for k in ("description", "product_class", "fabric_name")]
    text += [("style_name", row and row.get("style_name"))]
    return dict(code=code, vendor=R.b["vendor"], title=tuple(n["title"]), ptype=tuple(n["ptype"]),
                composition=comp, made_in=made, text=[(k, squash(v)) for k, v in text if squash(v)],
                made_column=tuple(n["made"]), unknown=R.b.get("unknown"))


# ---- the draft -----------------------------------------------------------------------------
def draft(f, brand):
    """The description of one product as parts, from its facts: what the tool grounds, nothing else."""
    title, t_src, _ = f["title"]
    _, colour = title_colour(title)
    d = dict(code=f["code"], vendor=f["vendor"], title=title, title_src=t_src, type_src=f["ptype"][1],
             item=None, adjective=None, sentence2=None, bullets=[], composition=None, made_in=None, notes=[],
             red=[], fields=dict(f["text"]), dropped=[])
    d["item"] = item_noun(f["ptype"][0], title) if squash(f["ptype"][0]) else None
    if d["item"] is None:
        d["red"].append(f"no intro: the Type '{f['ptype'][0]}' gives the tool no item noun" if squash(f["ptype"][0])
                        else "no intro: the Type is blank, so the tool has no item noun")
    named, mine = title_kind(title), item_kind(d["item"], last=True)
    if named and mine and named != mine:
        d["red"].append(TITLE_MISMATCH)
    _composition(d, f)
    _made_in(d, f, brand)
    kind = type_kind(f["ptype"][0])
    if d["item"]:
        item_words = set(re.findall(r"[a-z]+", d["item"].lower()))
        adj, clash = phrases(ADJECTIVES, f["text"], 4, kind)
        adj = [(a, field, k) for a, field, k in adj
               if not set(re.findall(r"[a-z]+", a)) & item_words][:2]
        d["dropped"] += [f"adjectives of the {g} (the description names two)" for g in clash]
        if adj:
            d["adjective"] = dict(text=" ".join(a for a, _, _ in adj), field=adj[0][1], by="tool",
                                  basis=" and ".join(f"'{k}' in the {field.replace('_', ' ')}" for _, field, k in adj))
    if colour and COLOUR.get(t_src, RED) != RED and not re.search(r"\d", colour):
        b = f"{colour} colourway"
        if banned(b):
            d["dropped"].append(f"colour bullet ({banned(b)})")
        else:
            d["bullets"].append(dict(text=b, field="title", by="tool", basis="the title's colour words"))
    for cand in f["composition"]:
        parts = part_bullets(cand["value"])
        if parts:
            for b, part in parts:
                d["bullets"].append(dict(text=b, field="composition", by="tool",
                                         basis=f"the {part} in the composition from {cand['where']}"))
            break
    feats, clash = phrases(FEATURES, f["text"], TEXT_FEATURES_MAX, kind)
    d["dropped"] += [f"bullets of the {g} (the description names two)" for g in clash if
                     f"adjectives of the {g} (the description names two)" not in d["dropped"]]
    for b, field, k in feats:
        d["bullets"].append(dict(text=b, field=field, by="tool", basis=f"'{k}' in the {field.replace('_', ' ')}"))
    seen, kept = set(), []
    for b in d["bullets"]:
        if b["text"].casefold() not in seen and len(kept) < MAX_BULLETS:
            seen.add(b["text"].casefold())
            kept.append(b)
    d["bullets"] = kept
    return d


def _composition(d, f):
    usable, skipped = [], []
    for c in f["composition"]:
        why = composition_problem(c["value"])
        (skipped if why else usable).append((c, why))
    if usable:
        c = usable[0][0]
        d["composition"] = dict(value=c["value"], where=c["where"], source=c["source"], html=c["html"],
                                text=squash(c["value"]) if c["html"] else house_case(c["value"]))
        same = squash(html.unescape(c["value"])).casefold()
        for o, _ in usable[1:]:
            if not o.get("fallback") and squash(html.unescape(o["value"])).casefold() != same:
                d["notes"].append(f"composition: {o['where']} says '{squash(html.unescape(o['value']))}', "
                                  f"used {c['where']} first (priority: the OC line, the cart, the live product)")
        for o, why in skipped:
            if not o.get("fallback"):
                d["notes"].append(f"composition from {o['where']} not used: it {why}")
        return
    if skipped:
        d["red"].append("Composition needed: " + "; ".join(
            f"{o['where']} gives '{squash(html.unescape(o['value']))[:120]}', which {why}" for o, why in skipped)
            + "; type the line yourself (first bullet, '<li>Composition: ...</li>')")
    else:
        d["red"].append("Composition needed: no order-time source gives one (the OC line prints none, no cart row, "
                        "no live same style+fabric product); type the line yourself (first bullet, "
                        "'<li>Composition: ...</li>')")


def _made_in(d, f, brand):
    named = [(country(m["value"], brand), m) for m in f["made_in"]]
    known = [(c, m) for c, m in named if c]
    for c, m in named:
        if not c:
            d["notes"].append(f"made in: {m['where']} prints '{squash(m['value'])}', not a country the tool knows")
    if known:
        c, m = known[0]
        d["made_in"] = dict(value=c, where=m["where"], source=m["source"])
        for o, om in known[1:]:
            if o != c:
                d["notes"].append(f"made in: {om['where']} says {o}, used {m['where']} first")
    col = f.get("made_column")
    if col:
        v = squash(col[0])
        if d["made_in"] and v in ("", squash(f.get("unknown"))):
            d["notes"].append(f"the custom.made_in column is '{v}': type {d['made_in']['value']} there too if right")
        elif d["made_in"] and v and country(v, brand) not in (None, d["made_in"]["value"]):
            d["notes"].append(f"the custom.made_in column says '{v}', the description {d['made_in']['value']}: "
                              "make them agree")


# ---- the cell --------------------------------------------------------------------------------
def gaps(d):
    """Every way the draft falls short of the house shape (measure.md 2.1)."""
    out = []
    if d["item"] and not d["adjective"]:
        out.append("no adjective")
    if d["item"] and not d["sentence2"]:
        out.append("no second sentence")
    n = len(d["bullets"])
    if n < HOUSE_BULLETS[0]:
        out.append(f"{n} feature bullet{'s' if n != 1 else ''} (the house has {HOUSE_BULLETS[0]} to "
                   f"{HOUSE_BULLETS[1]})")
    if not d["made_in"]:
        out.append("no Made in (no known country of origin)")
    return out


def render(d):
    """The Body HTML: house layout, every text part escaped (a Composition line read from a live product's Body HTML
    is HTML already and kept as it is)."""
    e = lambda s: html.escape(str(s), quote=False)
    parts = []
    if d["item"]:
        item = d["item"]
        adj = (d["adjective"] or {}).get("text")
        if adj:
            item = f"pair of {adj} {item[len('pair of '):]}" if item.startswith("pair of ") else f"{adj} {item}"
        intro = f"{d['vendor'].upper()}'s {d['title']} is {article(item)} {item}."
        if d["sentence2"]:
            intro += " " + d["sentence2"]["text"]
        parts.append(f"<p>{e(intro)}</p>")
    lines = []
    if d["composition"]:
        c = d["composition"]
        lines.append(f"<li>Composition: {c['text'] if c['html'] else e(c['text'])}</li>")
    lines += [f"  <li>{e(b['text'])}</li>" for b in d["bullets"]]
    if d["made_in"]:
        lines.append(f"<li>Made in {e(d['made_in']['value'])}</li>")
    sku = f"<p>SKU: {e(d['code'])}</p>"
    parts.append("<ul>\n" + "\n".join(lines) + "\n</ul><br>" + sku if lines else sku)
    return "\n".join(parts)


def source(d):
    """Never green: the draft's own (amber, AI-drafted when any part is the AI's), the least trusted of the title and
    Type it is built from, and red ('guess') when the Composition line or the intro is missing."""
    ai = d["title_src"] == AI_DRAFT or any(x and x.get("by") == "AI" for x in [d["adjective"], d["sentence2"]]
                                            + d["bullets"])
    src = AI_DRAFT if ai else TOOL_DRAFT
    for part in (d["title_src"], d["type_src"]):
        if RANK[COLOUR.get(part, RED)] > RANK[COLOUR[src]]:
            src = part
    return "guess" if d["red"] else src


def why(d):
    parts = []
    by = sorted({x["by"] for x in [d["adjective"], d["sentence2"]] + d["bullets"] if x})
    head = "house description, " + ("AI-drafted and tool draft" if "AI" in by else "tool draft") + ": check it"
    parts.append(head)
    if d["red"]:
        parts += d["red"]
    if d["item"]:
        parts.append(f"item '{d['item']}' from the Type")
    if d["composition"]:
        c = d["composition"]
        raw = squash(html.unescape(c["value"]))
        parts.append(f"composition from {c['where']}" + ("" if c["html"] or raw == c["text"] else
                                                         f", printed '{raw}', written in Title Case as the house does"))
    if d["made_in"]:
        parts.append(f"made in {d['made_in']['value']} from {d['made_in']['where']}")
    for name, x in (("adjective", d["adjective"]), ("second sentence", d["sentence2"])):
        if x:
            parts.append(f"{name} by {x['by']} from {x['basis']}")
    for i, b in enumerate(d["bullets"], 1):
        parts.append(f"bullet {i} by {b['by']} from {b['basis']}")
    parts += d["notes"]
    parts += [f"dropped: {x}" for x in d["dropped"]]
    g = gaps(d)
    if g:
        parts.append("gaps against the house shape: " + ", ".join(g))
    if COLOUR.get(d["title_src"], RED) == RED:
        parts.append("the title is red: fix it, then the intro, which holds it verbatim")
    parts.append("the intro holds the Title verbatim: change both together")
    return "; ".join(parts)


def cell(d):
    """(Body HTML, source, why) for draft.build's Body HTML cell."""
    return render(d), source(d), why(d)


def structure(d):
    """The draft's shape with no text (the backtest's skeleton.jsonl row)."""
    return dict(intro=bool(d["item"]), adjective=bool(d["adjective"]), adjective_by=(d["adjective"] or {}).get("by"),
                sentence2=bool(d["sentence2"]), bullets=len(d["bullets"]),
                bullets_by=[b["by"] for b in d["bullets"]], bullet_kinds=[b["field"] for b in d["bullets"]],
                composition=bool(d["composition"]),
                composition_source=(d["composition"] or {}).get("source"), made_in=bool(d["made_in"]),
                made_in_source=(d["made_in"] or {}).get("source"), red=len(d["red"]), gaps=gaps(d),
                shading=COLOUR.get(source(d), RED), source=source(d))


# ---- where the description goes (Colwyn, 2026-11-16, option B: "Separate import") -----------------------
DESC_COLUMN = "Description"   # its Review rows' Column; the workbook writes them as the Descriptions sheet


def review_rows(pending):
    """One Review row per NEW product (each listing of a code is its own product), pointing at its first PO row."""
    from .poxlsx import review_row
    return [review_row(n["row0"] + 2, n["code"], None, DESC_COLUMN, *n["body"]) for n in pending if n.get("body")]


# ---- the step draft.build runs ------------------------------------------------------------------
def run(pending, R, describer=None):
    """Draft every pending NEW product's house description in place (n['describe'] the parts, n['body'] the cell),
    then let `describer(pending, R)` (oc/agent/describe.run) add AI prose. -> the check rows the AI job adds (none
    with no describer or no key). The PO's Body HTML keeps the brand's template whatever this drafts (option B)."""
    for n in pending:
        n["describe"] = draft(facts(n, R), R.b)
        n["body"] = cell(n["describe"])
    if describer is None or not pending:
        return []
    return describer(pending, R)
