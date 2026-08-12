# PO-Validator

<!-- marais-workflow-map -->
## Cross-repo workflow map
This repo is part of a multi-repo Shopify workflow (13 repos, 18 shared data entities). It is
the **pre-create validation gate**: it checks/enriches the product-import spreadsheet before
products, variants, tags, and metafields are created in Shopify.
**Before you create, rename, or delete a shared data object** (product, variant, product type,
price, inventory, SEO metafield, etc.), consult
`~/Code/marais/marais-workflow-map/entities/<entity>.md` and propagate to every repo it lists
under **On create / On update / On delete**. Overview + dependency matrix:
`~/Code/marais/marais-workflow-map/index.md`. After changing what this repo validates/reads/writes,
update its note at `~/Code/marais/marais-workflow-map/repos/po-validator.md`.
