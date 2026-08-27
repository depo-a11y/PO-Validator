"""Rebuild the catalog vocabulary used by typo_check.

Reads the live product export kept by marais-seo-dashboard and writes word
frequencies to typo_vocab.json. Run after a catalog refresh:

    python3 tools/build_typo_vocab.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO.parent / "marais-seo-dashboard" / "raw" / "products.json"
OUT = REPO / "typo_vocab.json"

sys.path.insert(0, str(REPO))
from typo_check import tokenize  # noqa: E402


def build(source):
    products = json.loads(Path(source).read_text())
    freq = Counter()
    for p in products:
        for field in ("title", "description_tag"):
            for word in tokenize(p.get(field) or ""):
                freq[word.lower()] += 1
    return products, freq


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    if not source.exists():
        sys.exit(f"Source export not found: {source}")
    products, freq = build(source)
    OUT.write_text(json.dumps({
        "source": str(source),
        "products": len(products),
        "words": dict(sorted(freq.items())),
    }, indent=0))
    print(f"{len(products)} products -> {len(freq)} distinct words -> {OUT.name}")
