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
  `app.py`), `PU-merge.xlsx`, `PU-merge-new-sizes.xlsx`, `PU-merge-ROLLBACK.xlsx` and `Delivery.xlsx`. PU-merge
  changes three things on each re-bought product: the season, the season tag, and `Variant Cost` on every live
  size (the OC's wholesale x the brand's cost multiplier, sizes not on the OC included), keyed on ID and Variant
  ID with Command and Variant Command UPDATE. It does not rewrite the `Variant.cost_price` metafield on existing
  sizes. New sizes take the same cost. The rollback puts back each product's previous season, removes the season
  tag where this run added it, and puts back each size's previous cost. A product with no season in the catalog
  had no season metafield (the column is required, so an empty cell means none), so its rollback season cell is
  empty: Matrixify deletes a metafield on an empty cell, which puts it back as it was before PU-merge, and an INFO
  names it. The rollback undoes only the PU-merge of its own run: use the ROLLBACK from the same
  zip (or `--out-prefix`) as the PU-merge you import, made before importing. If the PO is changed and finalize run
  again before the import, import the new run's PU-merge and keep the new run's ROLLBACK; an older ROLLBACK misses
  whatever the change added, such as a re-bought product. Every file of a run carries one run id: its file-name
  prefix, which is also each workbook's Title and the first row of finalize-checks. A catalog that already carries
  the season, its tag or the OC cost is a WARN (its ROLLBACK would put nothing back).
  It is all or nothing: if any check fails (the gate, a title still holding a raw code such as "in T2180", a
  blank colour family) no upload or delivery file is written, and exit code is 1.

**Inputs**

- OC PDF: read by the brand config's `adapter`. `otb` (Maison Margiela, MM6) reads the text layer by position;
  its three gates hard-fail. `ai` reads any layout (see "AI reader" below) and is held to the same gates.
- Catalog: a Matrixify Products export (CSV, UTF-8) for the brand's vendors, all statuses, with variants,
  `Variant Cost`, `Variant Inventory Qty`, `Variant Country of Origin` and metafields in `my_fields`, `custom` and
  `global`. Required columns are `catalog.REQUIRED`, including `custom.product_season` (the rollback restores it)
  and `Variant Inventory Qty` (the cost restatement counts available units). Make it with the saved Matrixify
  export preset "OC catalog: Margiela + MM6" (ask Colwyn if it is not there). A missing column fails the load,
  naming the column and its Matrixify group, and says to make the export again with that preset.
- Brand config: `<brand>.local.yaml`, copied from `brands/example.yaml`. It holds the cost multiplier and past
  PO paths, so it never goes in git. Marais keeps the real ones outside the repo in
  `~/Code/marais/_work/oc-brands/`; point the tool there with `export OC_BRAND_DIR=~/Code/marais/_work/oc-brands`
  or pass `--brand-config <file>`. Without either, it falls back to the gitignored `oc/brands/`. Never commit a
  brand config, a catalog export or a PO while this repo is public.

`python tests/mutate_oc.py` re-runs the mutation harness for the guards.

## Cart (linesheet)

    python -m oc draft    ... --cart CART.xlsx
    python -m oc complete ... --cart CART.xlsx

The OC carries codes, quantities and prices but no colour words, composition or names. The brand's
selling-session cart export (OTB: `<BRAND> SS27 AVP X MARAIS, ID <session>.xlsx`) fills part of that gap.
`oc/cart.py` reads it: the header row is found by content in the first 50 rows of any sheet (it must hold
`Model`, `Fabric`, `Color Code`, `Color Description`), one row per style-colour, keyed by the full join key
`<Model><Fabric> <Color Code>`. A Margiela numeric colour code means a different colour on every fabric, so
nothing is ever looked up by the colour code alone. An unreadable file, a missing header or no rows raise
`CartError`.

What the draft takes from the cart, shaded amber with the cart's session and row in the Review sheet:

- title colour words (`<Product> in <Color Description>`, words capitalised); a description with a stray
  character (`Peach Pur}e`) is red;
- colour family: the brand's `colour_families` keywords on that description, the keyword nearest the start
  winning (`Black/Camel` is BLACK). The live catalog's own family for the same fabric+colour still comes first;
- composition (`Materials`) and made in (`Made In`, often blank) for the Body HTML and made_in;
- gender (Female/Male) when the OC has none, the Style Name when the OC has no product class, and Type from
  the brand's `subcategory_types` table when the product class gives none.

Where the live same style+fabric product says the same thing, the cell stays green. The product-name part of a
new title still comes from the catalog (same style+fabric, then same style), else the class name in red.

Never from the cart: quantities, wholesale and retail. Its Sugg. Retail is ignored (OTB sends 0) and a Wholesale
that differs from the OC is an INFO check. Coverage checks: OC codes not in the cart and cart codes not on the OC
(INFO, with counts), and a cart sharing no code with the OC is a FAIL (wrong cart).

Cart text is data, never markup or a formula. A Materials or Made In value holding `<` or `>` is not used
(red, with the reason); other text is HTML-escaped into the Body HTML, while a composition read out of a
live product's Body HTML is already HTML and is left alone. Every sheet written through `oc/poxlsx.py`
(`write_po`, `write_table`, `write_frame`) saves a string starting `=`, `+`, `-`, `@`, tab or CR as text
with its visible value unchanged (no leading apostrophe), and `read_po` resolves only real formula cells.

Brand config keys the cart uses: `colour_families`, `subcategory_types` (cart Subcategory to Type without gender),
`title_prefixes` (which gender's product gets a prefix when two NEW titles collide; default `{WOMEN: "Womens "}`).
A `product_class_types` or `subcategory_types` value may be a list (first valid Type for the gender wins) or a
full Type starting MEN, WOMEN or UNISEX.

## Web page

`pages/1_Order_confirmation_to_PO.py` runs `draft` and `finalize` for someone with no command line, inside the
po-validator Streamlit app (`streamlit run app.py`, then "Order confirmation to PO" in the sidebar). The work is
in `service.py`, which takes paths and returns bytes. Uploads sit in a temporary folder only while a step runs;
nothing is written into the repo and nothing talks to Shopify.

1. **Build draft PO** from the OC PDF, the catalog export, a brand, and optionally the linesheet/cart export and
   past season POs. It shows the checks, the red and amber cells grouped by code and column, and the draft to
   download with its shading. A FAIL a reviewer fixes in the sheet (a title already a live handle, two codes on
   one title, a Type not in `expected_tags.xlsx`, a NEW row under the margin floor) still gives the draft, and
   finalize checks each again. Any other FAIL (OC totals, a code on two live products, an unreadable file) gives
   no draft.
2. **Make upload files** from the checked PO. All or nothing, like `finalize`: either one zip holding PU-new,
   PU-merge, PU-merge-new-sizes, PU-merge-ROLLBACK, Delivery and finalize-checks, named
   `YYYY-MM-DD-HHMM-<brand>-<season>-PU-new.xlsx` and so on, or no zip and the failing checks first. The date and
   time are Melbourne's, so runs on one day get their own names unless made in the same minute.
   `YYYY-MM-DD-HHMM-<brand>-<season>` is the run id: the zip is `<run id>-uploads.zip`, and the page says which
   ROLLBACK goes with which PU-merge.

**Brand config**, in the order the picker lists them:

- Saved in the app: `[oc_brands]` in Streamlit secrets, one entry per brand, the value being the brand's .yaml
  pasted whole as a TOML multi-line string (a TOML table with the same keys also works). Shape:
  `.streamlit/secrets.example.toml`. They are listed only to a viewer whose sign-in email is in `oc_viewers`
  (whole addresses, any case). Streamlit knows that email only when the app signs viewers in itself with
  `st.login`, which needs an `[auth]` section in secrets and the Authlib package (`Authlib>=1.3.2`, installed by
  `requirements.txt`): since Streamlit 1.42, Community Cloud does not pass a Community Cloud viewer's email to the
  app. With no `oc_viewers` or no sign-in, saved brands stay hidden and the page says why. This is a second
  layer: keep the hosted app private, with its viewer list limited to staff.
- On this computer: `$OC_BRAND_DIR`, then the gitignored `oc/brands/`, the same lookup as the CLI.
- Uploaded: a brand .yaml file.

The page names the config it uses (source, vendor, season), never its contents.

**Past season POs** set most retail prices for new styles. Only a config file on the computer running the app may
name them by path. A config from secrets or an upload never has its paths opened, because an upload could
otherwise make the app read any file on its server; upload the past POs on the page instead. Uploads replace the
config's list. Each is labelled with the config's label for a file of the same name, else its season column, else
a season in its file name, and they are read newest first. When past POs are missing the page warns before the
run, and the draft's first check says retail prices will mostly come back red.

`.streamlit/secrets.toml` is gitignored. Never commit it.

## AI reader

    python -m oc read OC.pdf --brand-config brand.yaml [--reader ai] [--json read.json]
    python -m oc draft ... --reader ai

`oc/adapters/ai.py` sends the PDF to the Anthropic Messages API (`claude-opus-5-5`, effort high, streamed, with
server-side refusal fallback) and gets JSON back under a strict schema. A brand with `adapter: ai` is always read
this way; `--reader ai` (the page's "Read the OC with AI" box) forces it for an otb brand whose layout changed.
Nothing falls back from one reader to the other on its own.

The model transcribes and Python decides. Every number comes back as the printed text and is parsed here:
money needs a 2-digit decimal part or is a bare integer (`€ 1,488.60`, `1.234,56`, `EUR 100,00`, `100`), and
`1,488` or `12.5` is refused, never guessed. The docs have the otb shape and pass `otb.gate_problems` unchanged
(sizes sum to the line, qty x price = line value, lines sum to the printed totals). On top of that, a read is
refused when: a line has no sizes (a proforma prints none: upload the order confirmation), the currency is not the
brand's `cost_currency`, the issuer is not one of the brand's `oc_supplier` names (case and punctuation ignored),
a number does not parse, or (when the PDF has a text layer) a line's style, price or line value, or a document's
number or totals, is not in the PDF text. Bold text drawn twice (`PPRROOFFOORRMMAA`) is matched too. A PDF with
no text layer is a WARN: nothing could be cross-checked. A document the AI calls anything but an order
confirmation is a WARN.

What no gate catches: a quantity read under the wrong size. So every value from an AI read is source
`OC (AI read)` in the Review sheet, shaded amber (never green), its Why reads "read by AI from <doc> line <n>",
and the Checks carry an "Order confirmation reader" WARN with the model, seconds and cost, telling the reviewer to
check each line's sizes against the PDF. The otb reader's row is an INFO.

The key is `ANTHROPIC_API_KEY`: the environment first, else the app's Streamlit secrets (top level of
`.streamlit/secrets.toml`, see `secrets.example.toml`). It is never logged or shown; with no key the read is a
FAIL saying to ask Colwyn. SDK errors become plain FAILs (key refused / busy, try again / request rejected),
as do a refusal, the 64,000-token output limit and an answer that is not JSON. A refused read still reports what
it cost.

An `ai` brand config needs `oc_supplier` and `join_key`; the config fails to load without them, saying what each
is. `join_key` builds the store's manufacture_code from an OC line: a template over `{style}`, `{fabric}`,
`{colour}` and `{colour_code}`. The default (otb) is `{style}{fabric} {colour}`, today's Margiela rule. A brand
whose store code ends in a short colour code where its OC prints the colour name uses `{style} {colour_code}` with
`colour_codes: {GREY: GR, ...}`; an OC colour missing from that table stops the draft, naming every one.

Tests run offline against a fake client and a synthetic PDF (`tests/test_oc_ai.py`). `OC_AI_LIVE=1` runs the one
real-API test, which costs money.
