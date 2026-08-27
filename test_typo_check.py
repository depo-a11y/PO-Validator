import pandas as pd
import pytest

import typo_check
from typo_check import TypoChecker, tokenize, validate_typos

# A miniature catalog standing in for the real vocabulary.
VOCAB = {
    "mineral": 11, "indigo": 103, "bolan": 37, "bootcut": 34, "stretch": 26,
    "denim": 457, "sneaker": 736, "hoodie": 871, "calfskin": 175, "bag": 716,
    "necklace": 100, "strap": 43, "sweatshirt": 393, "black": 6547,
    "multicolour": 93, "naturel": 2, "browne": 2, "cjhq": 1, "mieral": 1,
}
DICTIONARY = {"mineral", "indigo", "stretch", "denim", "black", "natural", "brown", "bag",
              "curve", "painter", "sneak"}
ALLOWLIST = {"naturel", "browne", "cjhq"}


@pytest.fixture
def checker():
    return TypoChecker(VOCAB, DICTIONARY, ALLOWLIST)


def words(suspects):
    return [s.word for s in suspects]


class TestTokenize:
    def test_splits_on_punctuation(self):
        assert tokenize("Off-White/Patch") == ["Off", "White", "Patch"]

    def test_keeps_accented_letters_whole(self):
        # "Joséphine" must not split into "Jos" + "phine"
        assert tokenize("Joséphine Classic Candle") == ["Joséphine", "Classic", "Candle"]

    def test_drops_digits(self):
        assert tokenize("510006 Strech Wool") == ["Strech", "Wool"]


class TestFindsRealTypos:
    def test_catches_the_reported_typo(self, checker):
        found = checker.check("Stretch Denim Bolan Bootcut in Mieral Indigo")
        assert words(found) == ["Mieral"]
        assert found[0].suggestion == "mineral"

    def test_reports_the_most_common_neighbour(self, checker):
        assert checker.check("Jambo Sneeaker in Black")[0].suggestion == "sneaker"

    def test_catches_doubled_letter_typo(self, checker):
        assert words(checker.check("Bucket in Callfskin")) == ["Callfskin"]

    def test_is_case_insensitive(self, checker):
        assert words(checker.check("SHINY CALFKSIN")) == ["CALFKSIN"]


class TestSuppressesFalsePositives:
    def test_ignores_words_already_common_in_the_catalog(self, checker):
        assert checker.check("Stretch Denim Bolan Bootcut in Indigo") == []

    def test_ignores_real_english_words(self, checker):
        assert checker.check("Coat in Mineral Indigo") == []

    def test_ignores_plurals_of_catalog_words(self, checker):
        assert checker.check("Micro Ava Bags with Straps and Necklaces") == []

    def test_ignores_allowlisted_words(self, checker):
        assert checker.check("Tote Bag in Naturel") == []
        assert checker.check("Lanvin Cuff Bracelet Cjhq2H") == []

    def test_ignores_plurals_of_dictionary_words(self, checker):
        # the 1913 wordlist has "curve" and "painter" but neither plural
        assert checker.check("Soft Curves Coat") == []
        assert checker.check("Painters Jacket in Black") == []

    def test_still_flags_a_misspelling_whose_stem_is_a_word(self, checker):
        # stripping -es would make "Sneakes" look like the real word "sneak"
        assert words(checker.check("Jumbolace Low Sneakes")) == ["Sneakes"]

    def test_ignores_short_tokens(self, checker):
        assert checker.check("GV3 2cm Belt in Ru") == []

    def test_ignores_rare_words_with_no_common_neighbour(self, checker):
        assert checker.check("Kuroki Shibaura Jacket") == []


class TestValidateTypos:
    def test_reports_row_column_and_suggestion(self, checker, monkeypatch):
        monkeypatch.setattr(typo_check, "default_checker", lambda: checker)
        df = pd.DataFrame([
            {"Title": "Bootcut in Mieral Indigo", "Body HTML": "", "Option2 Value": "Mieral Indigo"},
            {"Title": "Bootcut in Mineral Indigo", "Body HTML": "", "Option2 Value": "Mineral Indigo"},
        ])
        rows = validate_typos(df)
        assert len(rows) == 2
        assert {r["Column"] for r in rows} == {"Title", "Option2 Value"}
        assert all(r["Row"] == 2 for r in rows)          # first data row is Excel row 2
        assert all(r["Type"] == "POSSIBLE TYPO" for r in rows)
        assert all("mineral" in r["Details"] for r in rows)

    def test_reports_each_word_once_per_cell(self, checker, monkeypatch):
        monkeypatch.setattr(typo_check, "default_checker", lambda: checker)
        df = pd.DataFrame([{"Title": "Mieral Indigo and Mieral Blue"}])
        assert len(validate_typos(df)) == 1

    def test_tolerates_missing_columns(self, checker, monkeypatch):
        monkeypatch.setattr(typo_check, "default_checker", lambda: checker)
        assert validate_typos(pd.DataFrame([{"Vendor": "CELINE"}])) == []


class TestShippedData:
    """The real vocabulary and allowlist, not the fixtures."""

    def test_default_checker_catches_the_reported_typo(self):
        found = typo_check.default_checker().check("Stretch Denim Bolan Bootcut in Mieral Indigo")
        assert words(found) == ["Mieral"]

    @pytest.mark.parametrize("title", [
        "Small Duty Free Tote Bag in Naturel",
        "Thom Browne 507 - 12K Gold in Black/Grey",
        "AMQ - VICT PEPLM DENIM JKT KUROKI KB",
        "Yes We \"Can\" T-Shirt in Foie Gras",
        "a bird wants to fly high Jacket in Black",
        "Joséphine Classic Candle",
        "Instrumental 3 Pairs Socks in Black",
        "Le Teckel Medium Bag in Terre D'Ombre",
    ])
    def test_known_good_titles_stay_clean(self, title):
        assert typo_check.default_checker().check(title) == []

    @pytest.mark.parametrize("title,expected", [
        ("Classic Logo Hoddie in Black", "Hoddie"),
        ("Emb Jeresy Top in Black", "Jeresy"),
        ("Shark Tooth Small Neckalce in Green", "Neckalce"),
        ("Amiri Dragon Overized Tee in Black", "Overized"),
        ("510006 Strech Wool Cardigan in Black", "Strech"),
        ("Paneka Suede Sneaker in Tapue/Pierre", "Tapue"),
    ])
    def test_known_bad_titles_are_caught(self, title, expected):
        assert expected in words(typo_check.default_checker().check(title))
