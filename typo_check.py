"""Spelling check for product copy, tuned to the Marais catalog.

A general spellchecker is useless here: the dictionary has no "bootcut", no
"bolan", no "ecru". What works is our own catalog as the reference vocabulary.
A word is suspect when it is rare in the catalog AND sits one edit away from a
word we use constantly, e.g. "Mieral" (x1) beside "mineral" (x11).

Advisory only. It reports, it never stops an import: a false positive on a
colour name must not block a PO.
"""
import gzip
import json
import re
from collections import namedtuple
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent
VOCAB_FILE = REPO / "typo_vocab.json"
DICTIONARY_FILE = REPO / "words_en.txt.gz"
ALLOWLIST_FILE = REPO / "typo_allowlist.txt"

# Columns carrying customer-facing prose.
TEXT_COLUMNS = [
    "Title",
    "Body HTML",
    "Option2 Value",
    "Metafield: title_tag [string]",
    "Metafield: title_tag",
]

MIN_WORD_LEN = 4         # shorter tokens are style-code fragments
MAX_RARE_COUNT = 2       # "rare in our catalog" means at most this many uses
MIN_SUGGESTION_COUNT = 5 # the word it might be must be genuinely established
MIN_RATIO = 4            # ...and this much more common than the suspect

ALPHABET = "abcdefghijklmnopqrstuvwxyz"
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

Suspect = namedtuple("Suspect", "word suggestion count suggestion_count")


def tokenize(text):
    """Words only: no digits, no punctuation, accented letters kept whole."""
    return _TOKEN_RE.findall(str(text))


def _edits1(word):
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    out = set()
    out |= {l + r[1:] for l, r in splits if r}                          # deletion
    out |= {l + r[1] + r[0] + r[2:] for l, r in splits if len(r) > 1}   # transposition
    out |= {l + c + r[1:] for l, r in splits if r for c in ALPHABET}    # substitution
    out |= {l + c + r for l, r in splits for c in ALPHABET}             # insertion
    out.discard(word)
    return out


def is_inflection(word, other):
    """True when one word is just a plural or tense of the other.

    "Bags" beside "bag" is a real plural, not a misspelling. Note the -er
    suffix is deliberately absent: it would excuse "Sweatshirter".
    """
    for short, long in ((word, other), (other, word)):
        if short in (long + "s", long + "es", long + "d", long + "ed", long + "ing"):
            return True
        if long.endswith("y") and short == long[:-1] + "ies":
            return True
        if long.endswith("e") and short in (long[:-1] + "ing", long + "d", long + "s"):
            return True
    return False


class TypoChecker:
    def __init__(self, vocab, dictionary, allowlist):
        self.vocab = vocab
        self.dictionary = dictionary
        self.allowlist = allowlist

    def is_known_word(self, lowered):
        """True when the word is ordinary English.

        The shipped wordlist is the 1913 Webster and carries no plurals, so
        "curves" and "painters" look unknown while "curve" and "painter" do
        not. Only a bare -s (and -ies) is stripped: stripping -es would turn
        the misspelling "Sneakes" into the real word "sneak" and lose it.
        """
        if lowered in self.dictionary:
            return True
        if lowered.endswith("ies") and lowered[:-3] + "y" in self.dictionary:
            return True
        return lowered.endswith("s") and lowered[:-1] in self.dictionary

    def _suggest(self, lowered, count):
        floor = max(MIN_SUGGESTION_COUNT, MIN_RATIO * max(count, 1))
        candidates = [(self.vocab[e], e) for e in _edits1(lowered)
                      if self.vocab.get(e, 0) >= floor]
        if not candidates:
            return None
        if any(is_inflection(lowered, word) for _, word in candidates):
            return None
        return max(candidates)

    def check(self, text):
        """Return the suspect words in one piece of text, in order, deduped."""
        found, seen = [], set()
        for word in tokenize(text):
            lowered = word.lower()
            if lowered in seen or len(lowered) < MIN_WORD_LEN:
                continue
            if lowered in self.allowlist or self.is_known_word(lowered):
                continue
            count = self.vocab.get(lowered, 0)
            if count > MAX_RARE_COUNT:
                continue
            suggestion = self._suggest(lowered, count)
            if suggestion:
                seen.add(lowered)
                found.append(Suspect(word, suggestion[1], count, suggestion[0]))
        return found


def _load_lines(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return {line.strip().lower() for line in handle
                if line.strip() and not line.startswith("#")}


@lru_cache(maxsize=1)
def default_checker():
    """The checker built from the shipped catalog vocabulary.

    Raises if the vocabulary is missing rather than quietly passing every row:
    a check that silently stops checking is worse than no check.
    """
    if not VOCAB_FILE.exists():
        raise FileNotFoundError(
            f"{VOCAB_FILE.name} is missing. Rebuild it with "
            "`python3 tools/build_typo_vocab.py`."
        )
    vocab = json.loads(VOCAB_FILE.read_text())["words"]
    dictionary = _load_lines(DICTIONARY_FILE) if DICTIONARY_FILE.exists() else set()
    allowlist = _load_lines(ALLOWLIST_FILE) if ALLOWLIST_FILE.exists() else set()
    return TypoChecker(vocab, dictionary, allowlist)


def get_excel_row(index):
    return index + 2


def validate_typos(df, columns=None):
    """Flag likely misspellings in product copy. Advisory: returns rows, never exits."""
    checker = default_checker()
    columns = [c for c in (columns or TEXT_COLUMNS) if c in df.columns]
    rows = []
    for idx, row in df.iterrows():
        for column in columns:
            for suspect in checker.check(row.get(column, "")):
                rows.append({
                    "Row": get_excel_row(idx),
                    "SKU": row.get("Variant SKU", ""),
                    "Type": "POSSIBLE TYPO",
                    "Column": column,
                    "Word": suspect.word,
                    "Suggestion": suspect.suggestion,
                    "Details": f"'{suspect.word}' (x{suspect.count} in catalog) "
                               f"looks like '{suspect.suggestion}' (x{suspect.suggestion_count})",
                })
    return rows
