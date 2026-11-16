# oc: order confirmation to PO, PU and delivery file

Pure Python, files in and files out. Nothing here talks to Shopify, Matrixify or Drive.

    python -m oc draft    --oc OC.pdf --catalog export.csv --brand <brand> --out PO-draft.xlsx [--ai-titles] [--ai-explain]
    python -m oc complete --po hand_PO.xlsx --oc OC.pdf --catalog export.csv --brand <brand> --out PO.xlsx [--set-price "CODE=EUR"]
    python -m oc finalize --po PO.xlsx --catalog export.csv --brand <brand> --out-prefix <name>- [--ai-explain]
    python -m oc reconcile --draft PO-draft.xlsx --po hand_PO.xlsx --oc OC.pdf --brand <brand> --out accuracy.md

- `draft` builds the whole buy sheet from the OC. Every drafted cell is shaded green (OC or live
  catalog), amber (past POs, a brand rule or an AI draft) or red (a guess, or a value a person must supply), and the
  `Review` sheet says why. `--ai-titles` and `--ai-explain` are the agent jobs (see "Agent jobs" below); with no API
  key they do nothing but say so.

**Retail price (Colwyn, 2026-11-12: "we can keep price blank unless theres an rrp").** A NEW product's `Variant
Price` and `Variant Compare At Price` are written only from a brand RRP in AUD: the OC line's own RRP column (fog
prints one as MSRP, in the document's currency; the AI reader returns `rrp_text` and `rrp_currency`; otb, admilano
and adidas print none), else a nonzero `Sugg. Retail` in the cart, in the cart's `Currency`. An RRP in another or an
unknown currency is never the price: the Review sheet names it. An RRP counts only under an RRP label
(`util.rrp_label`): RRP, MSRP, retail (not Retailer), sugg or suggested, recommended, also run into a neighbour
('RRP(AUD)', 'RetailPrice'); SRP, PVP and UVP only as whole words ('SRP:', '(UVP)'). 'Ticket Price' counts only as a
column header (`util.rrp_header`), never beside an amount. fog's RRP is the amount under the first word of the line's
own header that is a label (MSRP on FOG Essentials; no label, no RRP). An AI-read RRP is used only when its text is
printed in its line's part of the PDF, it is not the line's own unit price or line value (on an AUD OC those carry an
AUD mark too), and a label sits in one of two places (`Pages.rrp_labelled`): right before the amount in the same
printed line, with at most two currency marks or punctuation words between ('RRP AUD 120,00', 'RRP: A$ 120,00'); or
in the nearest column-header row above the amount's line on its page, or, when that page has none, the nearest one at
or above the document's first line. A column-header row has two or more column words (Qty, Quantity, Price, Total,
Value, Amount, Units, Wholesale, WHL, Gross, Net, Cost) and no amount; only the nearest one is read. Nothing else
counts: the issuer's name, an address or the terms are ruled out by position (they are neither right before the
amount nor a column-header row), not recognised as such. Otherwise the RRP is not used and the Review says why. With
no usable RRP the price is blank and red, "price needed, no RRP from brand", and the check "Every NEW row has a
retail price" is a FAIL fixed in the sheet. A NEW row's `Variant Compare At Price` is the formula `=P<row>`, its own
row's price, so a typed price is copied; `finalize` reads the formula, never a cached value, so it works whether the
sheet was last saved by Excel or by openpyxl. A brand RRP is never raised: under the margin floor it stays as given
and "NEW rows clear the Nx margin" is a FAIL, also fixed in the sheet, because Step 2's margin gate would refuse it.
`finalize` refuses any NEW row without a price ("fill the retail price", with its PO rows and SKUs). MERGE rows are
unchanged: PU-merge never writes price. Live siblings, other styles at the same cost, past POs and ratio fits no
longer price anything. The "same style, live sibling price" source was 10/10 right on the Margiela SS27 test and is
now off by Colwyn's rule of 2026-11-12.
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
  It is all or nothing: if any check fails (the gate, a title still holding a raw code such as "in T0004", a
  blank colour family, a title that lost its colour words such as "Pants in ") no upload or delivery file is
  written, and exit code is 1.

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
  its column) says to tick "Read the OC with AI"; the arithmetic gates, discounts, a number format the reader will
  not guess and a document the reader knows is not an order do not; a reader marks any other problem that is not a
  layout change (a placeholder price, an order printed twice, its own printed totals) as `otb.NotLayout`.
- More fixed readers, with the same gates and the same currency and identity refusals: `thugclub` (THUG CLUB Corp.
  proforma invoices, USD; `oc_label` THUG CLUB or a collaboration such as TC x AI; a commercial invoice, packing
  list or deposit proforma is refused as "not an order confirmation"), `joor` (the JOOR order print of any brand;
  `oc_supplier` is the brand contact's email domain, since JOOR prints the brand as a logo; a Pending order is a
  WARN; a wholesale under 1.00 or a Grand Total other than the Subtotal is refused), `p3m` (the "#3xxxxxx"
  ordering platform: Willy Chavarria, Gosha Rubchinskiy, Maison MIHARA YASUHIRO, Amiri, grid and SOSU inline
  layouts; `oc_supplier` is the selling company printed on page 1) and `sportlux` (the Palm Angels distributor's
  "Order Confirmation" PDFs, EUR, an ERP and an Excel template told apart by the table header; `oc_supplier` is
  the issuer printed at the top, since they print no label).
- OC spreadsheet: `adapter: sheet` reads a brand's OC sent as .xlsx, .xls (BIFF, or the HTML-table ".xls" some
  order desks export) or .csv, by the config's `sheet:` column mapping (see "Spreadsheet OCs" below).
- Matching and identity keys, any reader, off unless set: `match_ignore` (separator characters such as "-_ " left
  out of both codes, upper-cased, when an OC code is matched to a live product; a MERGE row carries the live code,
  a NEW row the OC's; two live codes that become equal stop the draft naming both), `oc_style_prefix` (every OC
  line's style must start with one, for a brand that shares an agency's issuer; it counts as an identity check),
  `join_key_alt` (a second template, tried only for a line whose `join_key` code finds no live product; it may
  also use `{style_alt}`, a sheet's `columns: style_alt`; each line joined so is an INFO check, printed -> live,
  and complete and reconcile key it the same way, `match_ignore` applied) and `size_map` (see Sizes).
- Config state, off unless set: `hold` (a sentence; every path that uses the config, read, draft, complete,
  finalize and the cart, refuses with it before reading or writing anything) and `source_kind` (a short text such
  as "SAP demand export" for a file that is not the brand's own OC: a WARN names it and says to confirm lines,
  quantities and prices with the supplier).
- Money: Wholesale Price, Total Order and the `Variant.cost_price` metafield are in the brand's `cost_currency`.
  The metafield is written and checked as `<cost_currency> <amount>` (`EUR 34`, `USD 150`), messages are worded in
  that currency, and the PO and Delivery file format those columns in it (EUR keeps the euro-sign format).
- Templates: `title_tag` may use `{vendor}` and `{title}`, `body_html` `{composition}`, `{made_in}` and `{code}`,
  `join_key` `{style}`, `{fabric}`, `{colour}` and `{colour_code}`. They are filled by plain substitution, never
  `str.format` (a config can be uploaded on the hosted page), and any other brace is refused when the config loads.
- Colour vote: a NEW product's colour words and colour family are voted from the live products carrying its colour
  code, the code's text after its first space. A code with none (a `{style}` join_key, or a reader that printed no
  colour) gets no vote, so the cell falls to the cart, the brand's keywords or red, never a borrowed green. One
  product decides only as "same fabric+colour", and only where the config says where the fabric is
  (`resolve.vote_key`): `{style} {fabric} {colour}` compares it inside the colour code; `{style}{fabric} {colour}`
  reads it after the style only when the reader's style has one length (`otb`: 9, Margiela and MM6), and not for
  a line whose style is another length. That join_key read by any other reader (Diesel, Lemaire and Stone Island
  use `sheet`) has no fabric, nor has any other join_key: those vote only as "same colour code" (2+ products, 60%
  agree). The AI title evidence (`oc/agent/titles.py`) reads codes the same way. A winner needs 60% of every
  product its tier saw, the same fabric's included, and a product with no title colour or colour family counts
  against it ("2 of 5, 3 blank" is no vote; the Why names the blank ones). A same fabric that disagrees is no
  vote, never overruled by other fabrics; one holding only blanks leaves the vote to them.
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

## Before the OC: the cart as the order

    python -m oc draft --cart CART.xlsx --catalog export.csv --brand <brand> --out PO-draft.xlsx
    python -m oc draft --oc OC.pdf --prior PO-draft.xlsx --catalog export.csv --brand <brand> --out PO-from-OC.xlsx
    python -m oc complete --po PO-draft.xlsx --oc OC.pdf --catalog export.csv --brand <brand> --out PO.xlsx

With no `--oc`, `oc/cartorder.py` reads the selling-session cart as the order: one line per row with units, the
quantities from its size grid (every column between Size Grid / Available Sizes and Units, read under its own
header, since a header such as 40 repeats across size systems; `355` is 35.5 when 35 is also a header; UNI is OS),
the unit price from Wholesale. OTB's Units and Total Wholesale are formulas Excel never calculated, so they are
recomputed and a Checks WARN says so; where a cart does carry them, sizes must sum to Units and Units x Wholesale
must equal Total Wholesale. A quantity under a size outside the row's Size Grid, a size in two columns, a quantity
that is not a whole number, a formula with no value in a size cell, a row with units and no Wholesale, or a code
on two rows: no draft. Currency and label are checked as for an OC (`oc_label` against the word after the
collection code, `MM` in `S0XX - MM WOMEN'S RTW CO-ED`).

The Checks say `UNCONFIRMED: from cart <session>, signed <date>, not an order confirmation`, every quantity and
price cell (and the cost made from it) is amber `cart, not confirmed`, and a WARN asks to confirm the cost
multiplier for this selling session. The written PO names its quantity and price columns `Order Qty (from cart
<session>, not confirmed)`, `Wholesale Price (...)` and `Total Order (...)`. That header is the marker: deleting
the Checks sheet does not make the PO look confirmed, and the page and service name the draft and its run
`...-from-cart-<session>-...`. A draft from a cart says "equal the cart" where an OC draft says "equal the OC".

Descriptive fields come from the cart as in "Cart" above: the title from the Style Name in house style (amber), the
Type also from the Subcategory read as an OC product class (`Sportsjackets` as SPORTSJACKET, `Ballet Shoes` as BALLET
SHOE) when `subcategory_types` has no entry, and a NEW style with no run anywhere takes the cart's Available Sizes.
Live same-style products still set the Type first; when the Type comes from the style family's past Types and the
cart's Subcategory maps to another, the Why names both ("check which"). Retail prices stay blank (OTB's Sugg. Retail
is empty), so finalize stops there by design.

A PO is read as drafted from a cart when any one of three signs is in the workbook: its renamed columns, an
`UNCONFIRMED: ... from cart <session>` row in a later sheet (the draft's or complete's Checks), or a `cart, not
confirmed` Source in a later sheet (the draft's Review). So a PO drafted before the renamed columns existed, or
one whose columns were renamed back, is still caught. Only a PO with plain headers and no Review or Checks left
carries no sign, and is read as an order confirmation's. finalize then writes PU-new only, with a WARN: PU-merge,
PU-merge-new-sizes, PU-merge-ROLLBACK and Delivery need the OC's cost and quantities; PU-new creates N products at
the cost currency x the multiplier, not confirmed for this session; the OC can drop codes, so a product created
before it may never ship; whether to import PU-new before the OC is Colwyn's decision. Any other PO without plain
`Order Qty` and `Wholesale Price` columns is a FAIL, so nothing is made. A cart PO is refused as a past season PO,
and `reconcile` reads a cart PO's renamed columns.

When the OC arrives it is the authority (GTC 3.3/3.4). `draft --oc ... --prior <the cart-built PO>` (on the page,
"PO drafted from the cart") drafts from the OC, carries a retail price typed into the prior PO's NEW rows where
the brand gives no RRP (amber, `prior PO`), and adds a Changes sheet: codes the OC adds or drops, quantity per
code and size (a printed 40 is the store's IT 40, as the draft named it), unit price, and the typed retail prices. `complete --po <the cart-built PO> --oc ...` writes the
same sheet in place of the PO's old Review and Checks, with its own UNCONFIRMED row, and keeps the PO's typed
cells, so its quantity checks FAIL and finalize keeps to PU-new until the PO is redrafted from the OC.

House style on NEW titles (`oc/housestyle.py`): with `heel_codes: true` in the brand config, a heel height in
centimetres becomes the brand's H-code as the last word before "in <Colour>" ('Tabi Ballerina 3cm' ->
'Tabi Ballerina H30'; H30, H50, H60, H80 only). A cart colour word the brand config's `colour_spelling:` map names
(`Antracite: Anthracite`) is written as the map says, amber; nothing else is respelled. A live stem that departs from
the cart's Style Name names it in the Why, and a NEW title that an ACTIVE live product already carries is amber with
a Checks WARN naming the live handles. MERGE titles are never rewritten.

## Web page

`pages/1_Order_confirmation_to_PO.py` runs `draft` and `finalize` for someone with no command line, inside the
po-validator Streamlit app (`streamlit run app.py`, then "Order confirmation to PO" in the sidebar). The work is
in `service.py`, which takes paths and returns bytes. Uploads sit in a temporary folder only while a step runs;
nothing is written into the repo and nothing talks to Shopify.

1. **Build draft PO** from the OC PDF, the catalog export, a brand, and optionally the linesheet/cart export and
   past season POs. With an API key, a box (off by default) lets AI draft the NEW titles, colour words and colour
   families, and a button under a refused build's errors asks the AI to explain it (see "Agent jobs"). It shows the checks, the red and amber cells grouped by code and column, and the draft to
   download with its shading. A FAIL a reviewer fixes in the sheet (a title already a live handle, two codes on
   one title, a Type not in `expected_tags.xlsx`, a NEW row under the margin floor) still gives the draft, and
   finalize checks each again. Any other FAIL (OC totals, a code on two live products, an unreadable file) gives
   no draft.
2. **Make upload files** from the checked PO. All or nothing, like `finalize`: either one zip holding PU-new,
   PU-merge, PU-merge-new-sizes, PU-merge-ROLLBACK, Delivery and finalize-checks, named
   `YYYY-MM-DD-HHMM-<brand>-<season>-PU-new.xlsx` and so on, or no zip and the failing checks first. The date and
   time are Melbourne's, so runs on one day get their own names unless made in the same minute.
   `YYYY-MM-DD-HHMM-<brand>-<season>` is the run id: the zip is `<run id>-uploads.zip`, and the page says which
   ROLLBACK goes with which PU-merge. With an API key a button under the failing checks asks the AI to explain them.

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
the retired `price_round` key is dropped, and any other top-level key no code reads (`brand.KEYS`) fails the load
naming it, so a misspelt `on_hold:` cannot load a brand unheld.

`.streamlit/secrets.toml` is gitignored. Never commit it.

## Spreadsheet OCs

    python -m oc read OC.xls --brand-config brand.yaml

`adapter: sheet` (oc/adapters/sheet.py) reads an OC spreadsheet by the brand config's `sheet:` block: which printed
header holds the style, colour (code and/or name), size, quantity, unit price, line value and currency, and,
optionally, the RRP, EAN, HS code, country of origin, description, gender and order number. Sizes are one row per
size (`columns: size` and `qty`), a matrix with one column per size (`size_columns`), or (size, qty, size price)
column groups as NuORDER exports them (`size_pairs`). The module docstring shows every key. A sheet brand needs
`join_key`; `oc_supplier` and `oc_label` need the column that prints them.

For exports that are not a plain order form: `select` (`{column, values}`: only rows whose column holds one of the
values are this brand's, the rest are counted in the Checks; `known` lists every value the brand's configs read
between them, and a row whose value is in none FAILs naming its rows, units and value), `header_rows` with
`stack_headers: true` (a header printed over two rows, each column's text joined), `price_from: line_value` (no
unit price printed: each size row's price is its line value / qty, and the Checks say the qty x price gate checked
nothing) and `columns: qty_gross` / `qty_cancelled` (gross - cancelled must equal the net qty on every row).

The header row is found by its text, not its position; a later repeat of it (a page break in an export) is skipped.
An unknown mapping key fails when the config loads, and a mapped header missing from the file fails the read naming
it, as does a formula Excel never calculated in a mapped column. The gates are the PDF readers': sizes sum to the
line, qty x unit price = the printed value (per row, or per line when `line_value_per: line` says each size row
repeats the line's subtotal, which is then taken once), and the lines reproduce the printed totals (a totals row by
its label, or totals cells, or `totals: none` when the brand prints none). Totals, line values or line quantities the
mapping does not read from the file are recomputed, and a WARN names which and why (declared none, not mapped, none
printed): compare the units and value with the brand's PDF or invoice. Every money and quantity cell must fit
`decimal`; a cell that contradicts it, or reads two ways ("1.234"), fails the read naming the cells. An RRP is read
only from a column printed as one (`util.rrp_header`) and is a retail price only in AUD; one that differs by size
leaves the retail blank and a WARN says so.

Identity: a spreadsheet rarely names its brand. `oc_supplier` and `oc_label` are checked against mapped issuer and
label columns, and `ean_prefixes` (the brand's GS1 company prefixes) against every EAN. When none of these can run,
the Checks carry a WARN that the brand was NOT checked, as they do for any reader whose config names no issuer or
label. EANs and HS codes fill the PO's
`Variant.gtin` and `HS Code` columns on NEW rows, on the row of the store's name for the size they were printed
against, and the Review names the printed size (two printed sizes that become one store size with different EANs
leave it blank, and the Review says so); country of origin stays
on the read (no PO column takes it). The `sheet:` block's own `size_map` renames sizes as the file is read; the
top-level `size_map` (see Sizes) applies after it, when sizes are matched to the store's.
On the page, a sheet brand's upload takes .xls, .xlsx or .csv and has no AI box.

## AI reader

    python -m oc read OC.pdf --brand-config brand.yaml [--reader ai] [--json read.json]
    python -m oc draft ... --reader ai

`oc/adapters/ai.py` sends the PDF to the Anthropic Messages API (`claude-opus-5-5`, effort high, streamed, with
server-side refusal fallback) and gets JSON back under a strict schema. A brand with `adapter: ai` is always read
this way; `--reader ai` (the page's "Read the OC with AI" box) forces it for an otb brand whose layout changed.
Nothing falls back from one reader to the other on its own.

The model transcribes and Python decides. Every number comes back as the printed text and is parsed here:
money needs a 2-digit decimal part or is a bare integer (`€ 2,377.40`, `1.234,56`, `EUR 100,00`, `100`), and
`2,377` or `12.5` is refused, never guessed. The docs have the otb shape and pass `otb.gate_problems` unchanged
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

## Agent jobs: the pipeline works, the agent supervises

Colwyn, 2026-11-12: "so ideally we have a pipeline that can run most of the heavy lifting but an agent will
supervise?" The pipeline does the work and owns the gates; the agent handles judgment and exceptions; a person
approves any store write. The agent never overrides a gate: it changes an input, the pipeline runs on, and what it
drafted meets the same checks as anything else.

Both jobs are on only when `ai.api_key()` returns a key (`oc/agent/common.connect` is the one gate). With no key
nothing is built or sent, the page shows no new box or text, and `draft` and `finalize` give exactly what they give
without these jobs (tests: `test_no_key_*`). The model, effort, beta and fallback are the AI reader's
(`claude-opus-5-5`, streamed, `server-side-fallback-2026-07-01`, `fallbacks="default"`), with effort medium, and the
key, client and error wording are reused from `oc/adapters/ai.py`.

### Drafting NEW titles, colour words and colour family (`oc/agent/titles.py`)

    python -m oc draft ... --ai-titles

On the page: "Draft NEW titles and colour words with AI (check every one)", shown only with a key, off by default,
with this line under it (review cases 2026-11-12-buyer-14 F4 and 2026-11-13-buyer-9): "Ticking it sends to Anthropic,
for each new product: its codes and OC line (product class, collection, gender, description, composition), the tool's
Type, its cart row, and the titles, Types, statuses, colour names and colour families the catalog and past POs hold
for its style and colour code. It also sends the brand's name and colour families, and up to 200 past-PO products of
any style (code, Type, colour family, title) as examples. No prices, costs or quantities." A test pins every field
of the request to that line (`test_a_title_request_carries_only_what_the_page_says_it_sends`).

It runs inside `draft.build`, after the NEW products are resolved and before the title de-dupe, so its titles meet
the same de-dupe, gender prefix, live-handle and title checks, and finalize's gates (raw code in a title, one
manufacture code per Title, live handle, blank Option1, `run_gate`) unchanged.

- **Evidence, not answers.** Per NEW product the model gets the OC line (style, fabric, colour code, product class,
  collection, gender, the resolved Type, description and composition when known), the cart row (Style Name, Color
  Description, Materials, Subcategory), the same style's products in the catalog (title, colour option, Type, status;
  same style+fabric first, 8 at most) and in past POs (6 at most), and every catalog and past-PO use of the same
  fabric+colour code and of the same colour code on other fabrics (colour words and colour family, with counts). It
  never sees the tool's own title, colour words or family. The prompt says Margiela numeric colour codes are
  fabric-specific and T/H codes mostly are not. With a brand's `match_ignore` (landing 2) the evidence and the
  hold-out compare codes as `catalog.match` does (`util.code_match`), body and colour apart; the hold-out also drops a
  past-PO product whose whole code matches, so with a space in `match_ignore` an OC code printed with the space and a
  past PO's space-less one are the same product. With `join_key_alt` (landing 3) the hold-out leaves an OC line out
  under both its join_key and its alternative code, whichever the draft joined it on. A code ordered for two genders
  (two listings, landing 3's merge_repeats) is sent once and both listings take the answer; it counts as one product.
  The tool's own NEW-product lookups (title stem,
  Type, colour name) still take codes as printed, so for such a brand the agent can see a live sibling the tool
  does not; its suggestion then waits in the Why where the tool's title holds a raw code.
- **Answer.** Per code, under a strict schema: `product_name`, `colour_words`, `colour_family` (one of the brand's
  `colour_families` keys, or empty) and `basis`. Python composes `<product_name> in <colour_words>`.
- **Checked in Python first.** No raw code (`finalize.raw_code_in_title`), no code-like token (T0123, M01234, H0123,
  123; `8cm`, `6.5mm`, `6CC` and `5AC` are words), no vendor word (Maison, Margiela, MM6), letters, digits, space
  and `/ & ' - . ,` only, at most 60 characters for a name and 40 for colour words, nothing starting `= + - @`, no
  gender prefix (the de-dupe adds one) and no gender word anywhere (women, womens, woman, men, mens, man, ladies,
  lady, female, male, unisex, with or without 's: landing 3's readers put the print's gender in the evidence), no
  word "in" in a name. Heel codes, for a brand with `heel_codes: true`:
  only H30, H50, H60 or H80, as the last words of the name, and only when the evidence prints that height (in cm or
  as the H-code, in a same-style title, the product class or the cart's Style Name); any other H-number is refused.
  A brand without `heel_codes` gets no H-code from the AI: one is refused, counted, never quoted. A rejected value
  falls back to the tool's own, and the Why says why it was rejected. The value is quoted only when it is plain text
  (no markup, link, e-mail address or phone number); the reasons never quote it, and name code-like tokens and odd
  characters only by count.
- **Never into a cell finalize would refuse.** Where the tool's own value would fail one of finalize's hard gates
  that an AI value could satisfy (a raw code in the title, a title another code shares or that is a live handle, as
  the de-dupe would leave it, or a blank colour family), the cell keeps the tool's value and its red, and the Why says
  "AI suggests '...': not filled in" for the person to copy. A refusal a person must fix stays one. (Review case
  2026-11-12-buyer-14 F1: on the Margiela replay, AI colour words had made 3 raw-code titles pass, all 3 wrong.)
- **Policy.** An AI value replaces the tool's product name, colour words or colour family only where the tool's source
  is not green. Green stays; an AI value that differs is named in the Why ("AI suggests ..."). The source is
  `AI-drafted, check`, amber, never green, and stays so through a gender prefix; `title_tag` follows the title. Type,
  prices, cost, quantities and sizes are never touched. (`policy="everywhere"` exists for the measurement harness only.)
- **House style and the live-title check.** House style (`oc/housestyle.py`: heel codes with `heel_codes: true`, the
  `colour_spelling:` map, the Style Name note) shapes the tool's own title and colour words, before the agent. Heel
  codes and colour spellings follow the brand config for AI values too (review case 2026-11-14-buyer-1): with
  `heel_codes: true` an AI height in cm is written as its H-code by the same `housestyle.heel_code` (10cm and up
  stay as printed) before the checks, and without it an AI H-code is refused; the `colour_spelling:` map respells AI
  colour words as it does the cart's. The prompt's heel paragraph follows `heel_codes` too. Brand words stay out of
  AI values by the agent's own check. When house style changes only the colour words, the tool's name part keeps its
  own source, so a green name stays green. The live-title check runs after the agent: an AI title an ACTIVE product
  already carries keeps its source (`AI-drafted, check`, or red), gains the reason in the Why and the WARN names the
  handles. Finalize does not refuse such a title (only a live handle is a gate), so the agent does not guard it.
- **Few-shot.** The NEW products of the brand config's past POs (code, Type, colour family, Title), one per code,
  never a product on this OC, only titles that pass the same checks, round robin over Types, at most 200, in a
  deterministic order. It sits after the instructions in the system prompt with a 5-minute `cache_control`, so it is
  byte-identical on every request of a run and chunk 2 onwards read it from the cache.
- **Chunks and failure.** 20 products per request, sent one after another. Busy, refused, not JSON, not the schema or
  left out of the answer: those products keep the tool's values and a WARN reads "AI title drafting did not run:
  <reason>". It never fails the draft. Success is an INFO row: products answered, values drafted, green values kept
  (and where the AI differed), values rejected by reason, the model, tokens (input, cache write, cache read, output)
  and US$.

### Explaining a refusal (`oc/agent/explain.py`)

    python -m oc draft    ... --ai-explain
    python -m oc finalize ... --ai-explain

Like AI reads (10), the page caps each browser session at 10 AI title runs and 20 explanations, so a stuck rerun
loop cannot spend without limit; at the cap the box is off and the page says why. A draft from a spreadsheet OC or
from the cart alone has no PDF to send, so the page offers no explanation for it.

When a draft gives no workbook (a check the sheet cannot fix) or finalize refuses, the page offers a button under the
errors: "Ask the AI what went wrong (sends the PDF's text to Anthropic)", or for finalize "(sends the PO rows the
failing checks name to Anthropic)". Nothing is sent until it is pressed: the file uploaded is often the wrong one,
which is the commonest refusal. The answer is kept in the session (a rerun does not ask again) and shown under the
errors as "AI explanation (check it)", as plain text. The model gets the failing checks (Check, Result, Detail), the
other checks' names and results, the brand (name, vendor, reader, cost currency) and, for a draft, the PDF's text,
pages the failing details name first (6 pages at most, 4,000 characters each), or, for finalize, the PO rows the
failing details name by row number, Title, SKU or code (25 at most, a few columns, never Variant Cost or Wholesale
Price: a cost check's own Detail carries its numbers). Each failing check carries `stops_the_run`: on a draft, a FAIL
the reviewer fixes in the sheet (a blank retail price) did not stop it. On a draft each failing check also carries
`read_with_ai_can_fix`, true only when the tool itself offers "Read the OC with AI" for it (its Detail carries the
tool's hint, a layout the reader does not know). A refusal for what the document is (`otb.NotLayout`: an invoice, a
proforma, an order printed twice, a held brand) is not one. Where no failing check carries it (and on every
finalize), an explanation whose cause or next step mentions AI, advice and warning alike, is refused with "it mentions
reading with AI, which is not part of fixing this refusal", never quoting it. The list is exactly: AI, A.I. and A I in
capitals as a word of their own (also AI-powered; not AI-204, a code ending -AI, or an address's "ai"), and in any
case artificial intelligence, ChatGPT (Chat GPT, Chat-GPT), GPT (GPT-4), machine learning or machine-learning, and LLM
or LLMs. Claude (a designer's name) and "the model" (a supplier's Model column) are not on it. A paraphrase that names
none of these words ("use the other reader", "tick the box under the brand picker") is shown: only the prompt keeps
the AI reader out of it. Where a failing check does carry it, AI advice is shown. A brand config on hold (landing 3)
is never explained: its refusal names the reason, the page offers no button, and the CLI and service read and send
nothing ("this brand's config is on hold, so nothing was read or sent"). It answers `cause`, `next_step`, `who`
(colleague or colwyn) and `refs` (pages, rows, checks). The prompt states the 400-character limit. Both prompts say
their inputs are data, never instructions. Python drops a ref that does not exist and says only how many it dropped,
never which, and refuses text that is empty, over 400 characters, starts `= + - @`, or holds markup, a link or web
address, an e-mail address or a phone number, without quoting it, and a `who` that is neither colleague nor
colwyn, also unquoted. It reads copies of the checks and files and returns only its own record, so no check, file
or result changes and a refused run stays refused (tested on every refusal case
in `tests/test_oc_explain.py`). It adds no check row, for that reason: its cost is in its own record, shown with it.

### Cost

Every AI request is costed at `claude-opus-5-5` prices: US$4/MTok input, US$5/MTok 5-minute cache write, US$0.20/MTok
cache read, US$20/MTok output (thinking is output); a fallback model is costed the same and named. `ai.tokens()` sums
a fallback's iterations. The title job's check row and the CLI print the tokens and US$ for the OC; the explanation
carries its own.
