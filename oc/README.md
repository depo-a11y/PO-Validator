# oc: order confirmation to PO, PU and delivery file

Pure Python, files in and files out. Nothing here talks to Shopify, Matrixify or Drive.

    python -m oc draft    --oc OC.pdf --catalog export.csv --brand <brand> --out PO-draft.xlsx
    python -m oc complete --po hand_PO.xlsx --oc OC.pdf --catalog export.csv --brand <brand> --out PO.xlsx [--set-price "CODE=EUR"]
    python -m oc finalize --po PO.xlsx --catalog export.csv --brand <brand> --out-prefix <name>-
    python -m oc reconcile --draft PO-draft.xlsx --po hand_PO.xlsx --oc OC.pdf --brand <brand> --out accuracy.md

- `draft` builds the whole buy sheet from the OC. Every drafted cell is shaded green (OC or live
  catalog), amber (past POs or a brand rule) or red (a guess, or a value a person must supply), and the
  `Review` sheet says why.

**Retail price (Colwyn, 2026-11-12: "we can keep price blank unless theres an rrp").** A NEW product's `Variant
Price` and `Variant Compare At Price` are written only from a brand RRP in AUD: the OC line's own RRP column (fog
prints one as MSRP, in the document's currency; the AI reader returns `rrp_text` and `rrp_currency`; otb, admilano
and adidas print none), else a nonzero `Sugg. Retail` in the cart, in the cart's `Currency`. An RRP in another or an
unknown currency is never the price: the Review sheet names it. An RRP counts only under an RRP label (RRP, MSRP,
Retail, Sugg., Recommended; not Retailer): fog's is the amount under the header word that is one, and an AI-read RRP
needs the label printed in its line or its column header, its text printed in its line, and must not be the line's
own unit price or line value (on an AUD OC those carry an AUD mark too), else it is not used and the Review says
why. With no usable RRP the price is blank and red, "price needed, no RRP from brand", and the check "Every NEW row
has a retail price" is a FAIL fixed in the sheet. A NEW row's `Variant Compare At Price` is the formula `=P<row>`,
its own row's price, so a typed price is copied; `finalize` reads the formula, never a cached value, so it works
whether the sheet was last saved by Excel or by openpyxl. A brand RRP is never raised: under the margin floor it
stays as given and "NEW rows clear the Nx margin" is a FAIL, also fixed in the sheet, because Step 2's margin gate
would refuse it. `finalize` refuses any NEW row without a price ("fill the retail price", with its PO rows and
SKUs). MERGE rows are unchanged: PU-merge never writes price. Live siblings, other styles at the same cost, past POs
and ratio fits no longer price anything. The "same style, live sibling price" source was 10/10 right on the Margiela
SS27 test and is now off by Colwyn's rule of 2026-11-12.
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

- OC PDF: read by the brand config's `adapter`. The built-in readers read the text layer by position, one known
  layout each: `otb` (Maison Margiela, MM6), `admilano` (AD Milano / Ann Demeulemeester), `adidas` (adidas AG: Y-3,
  Willy Chavarria, RS) and `fog` (FOG Essentials, Fear of God LLC). `ai` reads any layout (see "AI reader" below).
  All share `otb.gate_problems`, whose three gates hard-fail. Each has read every season to hand (backtest
  2026-11-12): otb decides each confirmation's number format from its own amounts (1.234,56 and 23/03/2026, or
  F/W 26's 1,234.56 and 03/23/2026) and refuses one that mixes them, without the AI hint. A confirmation whose
  own lines do not add up to its printed total is refused with the figures and "ask the brand for a corrected OC,
  or ask Colwyn"; fog reads the S/S 26
  cells (a style wrapped over two rows, a colour name with no code, ORDER # alone on its row); adidas reads a size
  cell wrapped onto the composition row when the line's Tot. Qty needs it. Whatever the reader, an OC whose currency is not the
  brand's `cost_currency` is refused (printed names are normalised: otb's "European Euro" is EUR), and so is one
  from another issuer or label: every reader returns both per document (fog the issuer; admilano the issuer and
  its Brand field; adidas "adidas AG" and the label at the start of the line descriptions, Y-3, Willy Chavarria
  or RS, never the free-text Customer Order Number; otb the collection's first word). The config's `oc_supplier`
  and `oc_label` say what it accepts: fog needs `oc_supplier`, adidas and otb need `oc_label` (otb: MM or MM6);
  admilano is checked when the key is set. An AI read of a fixed-layout brand takes its label by that reader's own
  rule (otb: the collection's first word; adidas: the description prefixes). otb, which does not parse its issuer,
  passes the issuer check when one of the names is printed on the document's pages. Every layout problem of a fixed reader (no confirmation found, a moved header, a quantity off
  its column) says to tick "Read the OC with AI"; the arithmetic gates and discounts do not.
- Money: Wholesale Price, Total Order and the `Variant.cost_price` metafield are in the brand's `cost_currency`.
  The metafield is written and checked as `<cost_currency> <amount>` (`EUR 34`, `USD 150`), messages are worded in
  that currency, and the PO and Delivery file format those columns in it (EUR keeps the euro-sign format).
- Templates: `title_tag` may use `{vendor}` and `{title}`, `body_html` `{composition}`, `{made_in}` and `{code}`,
  `join_key` `{style}`, `{fabric}`, `{colour}` and `{colour_code}`. They are filled by plain substitution, never
  `str.format` (a config can be uploaded on the hosted page), and any other brace is refused when the config loads.
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

Never from the cart: quantities and wholesale. Its Sugg. Retail is a NEW product's price only when nonzero and the
cart's Currency is AUD (OTB sends 0, in EUR), and a Wholesale that differs from the OC is an INFO check. Coverage checks: OC codes not in the cart and cart codes not on the OC
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

**Sizes** keep one name per product. A printed size keeps its name when the store knows it, else takes the store's
name for the same size (IT 42 = 42; OSFM, O/S, F and OS; S-M = S - M) from the live product or its live same-style
siblings, and never when they hold two names for it. Past those, only a clear brand-wide majority renames (90% of the
brand's live products carrying that size, and at least 10 of them); a product's Type is no guide (one IT-sized
dress once renamed four bare-sized ones). A brand's `size_map` (printed: store) applies first. A renamed size is
amber with both names ("printed '40', named 'IT 40' as on ..."), never green as read from the OC. "Each size has one
name per product" WARNs on any product that still has two.

**Past season POs** feed the Type and the size run of new styles, never their price. Only the config vendor's rows
are read (an Adidas PO also holds Y-3, Willy Chavarria, Song for the Mute and Wales Bonner rows), and a code column
headed `Metafield: my_fields.manu`, cut short, is still found. Only a config file on the
computer running the app may
name them by path. A config from secrets or an upload never has its paths opened, because an upload could
otherwise make the app read any file on its server; upload the past POs on the page instead. Uploads replace the
config's list. Each is labelled with the config's label for a file of the same name, else its season column, else
a season in its file name, and they are read newest first. When past POs are missing the page warns before the
run, and the draft's first check says Types and size runs will rest on the catalog alone, and to ask Colwyn to fix
the brand file or upload every past PO for the brand (an upload replaces the whole list, so uploading only the
missing one would drop the rest).

**Local use.** The page runs on one laptop (Colwyn, 2026-11-12). `.streamlit/config.toml` binds it to 127.0.0.1,
headless, so nobody else on the network can open it or spend on the API key; hosting must revisit that file, with
a viewer allowlist and a per-user cap on AI reads. A brand config's numbers are checked at load: `sku_digits` is a
whole number from 1 to 12 (it is a format width), `cost_multiplier` and `margin_floor` are above 0 and below 20,
and the retired `price_round` key is dropped.

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
a number does not parse, or the AI calls it an invoice or delivery note (shipped quantities, not ordered; a
proforma is a WARN). Before anything is sent, a PDF over 60 pages or with no text layer (a scan) is refused.

Block grounding, on the words of each page in reading order (pdfplumber's default extraction and a tight one,
x_tolerance=1, which keeps apart words the default runs together, e.g. adidas descriptions; each also with bold
drawn twice, `PPRROOFFOORRMMAA`, collapsed): each line's style must be on the page the model names, after the
previous line's; its price and line value must be in that line's part of the text (from its style to the next
line's style), matched with spaces removed; its fabric and colour must be whole words there, never part of a word
('101' is not in '510 15/10/2026'), or for a value of five or more letters whole words in order that spell it (a
cell wrapped over lines, even mid-word); and each size name must be a word or the head of a size cell ('S/3') near
the line: in that part, in the gap before it, or in the page's table header. Every PDF page must belong to exactly
one confirmation or be listed as a page with no order lines, and every page is then checked on its own, so a
confirmation cannot vanish by having its pages declared empty or given to a neighbour: no page may print a number
shaped like a returned confirmation number, after the same words, that was not returned; and a page printing order
lines (a style-shaped code with two or more amounts in the confirmation's money format) may be neither declared empty
nor given to a confirmation none of whose lines were read on it. A totals or terms page may go either way. A confirmation returned twice, a line
returned more often than its style is printed, or a line on a page outside its confirmation is refused. Quantities
and totals are too short to look up in the text: only the gates' arithmetic checks them. Checked against the fixed readers on the real
PDFs, style -> price -> value -> next style holds on every line of otb (88), admilano (54), adidas (119) and fog
(52); fog prints 3 lines' size header before the style, hence the size-name region. True transcriptions of all 25
real files (those plus 3 FCW sales orders) raise no FAIL, whether pages without lines are given to their
confirmation or declared empty; dropping any of the Margiela OC's 7 confirmations (its pages declared empty or given
to a neighbour), or any page with lines, is refused.

What no gate catches: a quantity read under the wrong size. So every value from an AI read is source
`OC (AI read)` in the Review sheet, shaded amber (never green), its Why reads "read by AI from <doc> line <n>";
an ordered size the live product does not have is red. The Checks carry an "Order confirmation reader" WARN with
the model, seconds and cost, saying what was checked and telling the reviewer to check every line's code, colour,
sizes, quantities and price against the PDF. A fixed reader's row is an INFO. The page allows 10 AI reads per
browser session, and its AI box is off for a brand whose config has no `oc_supplier`.

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
