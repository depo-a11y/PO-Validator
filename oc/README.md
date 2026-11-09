# oc: order confirmation to PO, PU and delivery file

Pure Python, files in and files out. Nothing here talks to Shopify, Matrixify or Drive.

    python -m oc draft    --oc OC.pdf --catalog export.csv --brand <brand> --out PO-draft.xlsx
    python -m oc complete --po hand_PO.xlsx --oc OC.pdf --catalog export.csv --brand <brand> --out PO.xlsx [--set-price "CODE=EUR"]
    python -m oc finalize --po PO.xlsx --catalog export.csv --brand <brand> --out-prefix <name>-
    python -m oc reconcile --draft PO-draft.xlsx --po hand_PO.xlsx --oc OC.pdf --brand <brand> --out accuracy.md

- `draft` builds the whole buy sheet from the OC. Every drafted cell is shaded green (OC or live
  catalog), amber (past POs or a brand rule) or red (a guess), and the `Review` sheet says why.
- `complete` fills the blanks of a PO someone started by hand and never changes a typed cell, except a
  wholesale price named with `--set-price`, which must equal the OC's.
- `finalize` turns a reviewed PO into `PU-new.xlsx` (through `validator_core.run_gate`, the same gate as
  `app.py`), `PU-merge.xlsx` (season + season tag on carryover, nothing else, Command UPDATE),
  `PU-merge-new-sizes.xlsx`, `PU-merge-ROLLBACK.xlsx` and `Delivery.xlsx`. It is all or nothing: if any check
  fails (the gate, a title still holding a raw code such as "in T2180", a blank colour family) no upload or
  delivery file is written, and exit code is 1.

**Inputs**

- OC PDF: one adapter per layout in `adapters/` (`otb`: Maison Margiela, MM6). Its three gates hard-fail.
- Catalog: a Matrixify Products export (CSV, UTF-8) for the brand's vendors, all statuses, with variants,
  `Variant Cost`, `Variant Country of Origin` and metafields in `my_fields`, `custom` and `global`. Required
  columns are `catalog.REQUIRED`.
- Brand config: `<brand>.local.yaml`, copied from `brands/example.yaml`. It holds the cost multiplier and past
  PO paths, so it never goes in git. Marais keeps the real ones outside the repo in
  `~/Code/marais/_work/oc-brands/`; point the tool there with `export OC_BRAND_DIR=~/Code/marais/_work/oc-brands`
  or pass `--brand-config <file>`. Without either, it falls back to the gitignored `oc/brands/`. Never commit a
  brand config, a catalog export or a PO while this repo is public.

`python tests/mutate_oc.py` re-runs the mutation harness for the guards.
