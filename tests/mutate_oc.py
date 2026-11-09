"""Mutation harness for the OC tool's guards: python tests/mutate_oc.py

Each mutant breaks one guard; the suite must fail on every one. A mutant that
does not compile is reported BROKEN, never KILLED. Sources are restored in a
finally block and checked byte-identical at the end.
"""
import hashlib
import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MUTANTS = [
    ("oc/finalize.py", 'MERGE_PRODUCT_COLS = ["ID", "Command", "Tags Command", "Tags", SEASON]',
     'MERGE_PRODUCT_COLS = ["ID", "Command", "Tags Command", "Tags", SEASON, "Title"]', "PU-merge carries Title"),
    ("oc/adapters/otb.py", '            if sum(l["sizes"].values()) != l["qty"]:', "            if False:", "gate 1 removed"),
    ("oc/adapters/otb.py", '            if abs(l["qty"] * l["price"] - l["line_value"]) > 0.02:', "            if False:", "gate 2 removed"),
    ("oc/adapters/otb.py", '        if q != d["total_qty"]:', "        if False:", "gate 3 (units) removed"),
    ("oc/adapters/otb.py", '        if abs(v - d["net"]) > 0.02:', "        if False:", "gate 3 (value) removed"),
    ("oc/resolve.py", "        if price < floor - 1e-9:", "        if False:", "margin floor raise removed"),
    ("oc/catalog.py", '        live = [p for p in cands if p["status"].upper() != "ARCHIVED"]',
     "        live = list(cands)", "ARCHIVED twins kept"),
    ("oc/finalize.py", """            f'=SUMIF(${first_tranche}$1:$AZ$1,"<>Value",{first_tranche}{n}:AZ{n})',""",
     "            f'={first_tranche}{n}',", "Total Delivered points at one tranche"),
    ("oc/complete.py", "        if old == value:\n            return\n", "", "no-op fills logged (harmless: expect SURVIVED)"),
]


def digest(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


def main():
    files = {f for f, *_ in MUTANTS}
    before = {f: (ROOT / f).read_bytes() for f in files}
    sums = {f: digest(ROOT / f) for f in files}
    results = []
    try:
        for f, old, new, label in MUTANTS:
            p = ROOT / f
            src = before[f].decode()
            assert src.count(old) == 1, f"{label}: anchor not unique in {f}"
            p.write_text(src.replace(old, new))
            try:
                py_compile.compile(str(p), doraise=True)
            except py_compile.PyCompileError:
                results.append((label, "BROKEN"))
                p.write_bytes(before[f])
                continue
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
                               cwd=ROOT, capture_output=True, text=True)
            results.append((label, "KILLED" if r.returncode == 1 else "SURVIVED" if r.returncode == 0 else f"ERROR {r.returncode}"))
            p.write_bytes(before[f])
    finally:
        for f, b in before.items():
            (ROOT / f).write_bytes(b)
    for label, res in results:
        print(f"{res:9} {label}")
    assert all(digest(ROOT / f) == sums[f] for f in files), "sources not restored"
    print("sources restored byte-identical")


if __name__ == "__main__":
    main()
