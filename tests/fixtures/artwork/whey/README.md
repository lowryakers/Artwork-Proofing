# Whey protein bottle fixtures

30 whey protein bottle press proofs, as sent by the converter (runs `73096882` /
`7f8ce432`), plus the master rows to proof them against.

**The PDFs live in Git LFS** (`.gitattributes`: `tests/fixtures/artwork/whey/*.pdf
filter=lfs`). After cloning, `git lfs install && git lfs pull`, or the harness
reports each file as an un-pulled pointer and skips this set (loudly).

- `master_rows.csv` — the 30 `WHY-BTL-*` rows from the master export of
  2026-10-07, unmodified. The harness combines it with the pancake set's rows into
  one master list.
- `expected.json` — per file (28; Blueberry Muffin and Chocolate Mousse are defects, below): SKU, GTIN,
  fill weight, and the converter's `Cust. Item Ref`. Verified for all 30: the decoded barcode is the master GTIN, and
  the item reference is the SKU that GTIN resolves to. Expected: zero CRITICAL and
  zero WARNING with spot colour and trim disabled — except where a file lists
  `expected_warnings` (hand-verified on the artwork at 300–600 dpi):
  - Coconut Cream prints `Contains: Milk, Tree Nuts` with no tree-nut source
    (coconut is not a tree nut, FDA 2025).
  - 21 files carry ingredient-statement spacing defects — `SteviaLeaf` (10),
    `GuarGum`, `NaturalFlavors`, `CocoaPowder`, `Cinnamon,Stevia`, `Salt,Stevia`,
    `Powder,Himalayan`, `Acid,Stevia`, `Cinnamon,Cocoa`, `DU,Lactase`, `A&`,
    `2percent`, `Amylase300`, `300DU`, `ALU)v`. Each file's `spacing_note` lists
    its own. Not spacing, so not asserted: Snickerdoodle prints "Red A" for
    "Reb A", and Fruity Cereal prints "Reb A & M" where the others print
    "Reb A & Reb M". Add `serving_size_g`,
  `servings_per_container` (and optionally `serving_size_desc`,
  `declared_net_weight_g`, `overall_status`) to a file's `expect` to also assert the
  panel read and a CLEAN verdict.
- `defects.json` — `DEFECT_item_ref_mismatch`: the Double Chocolate master row's
  SKU is planted as `BEF-BTL-DCH`, so the barcode resolves to a different SKU than
  the sheet's `WHY-BTL-DCH`. Expect exactly one CRITICAL on `item_reference`.
  `DEFECT_dv_column_empty`: a **file-level** defect, `Blueberry_Muffin.pdf`
  unmodified. Every % Daily Value cell on its Nutrition Facts panel is empty while
  the `% Daily Value*` header and footnote are printed (verified at 800 dpi, run
  `aa5b6042`). Expect exactly one CRITICAL on `nfp` naming the whole column, and no
  other finding. The other 29 were checked by hand and carry a full %DV column.
  `DEFECT_dv_iron_chocolate_mousse`: a **file-level** defect, `Chocolate_Mousse_.pdf`
  unmodified. Its panel prints Iron 1.1mg at 4%; 1.1mg declares 1.05–1.15mg, 5.83–6.39%
  of 18mg, which rounds to 6% across the band. Expect exactly one CRITICAL on `nfp`
  naming Iron, 4% and 6%. Hand-checked across all 30: the only %DV that the declared
  amount cannot produce.

**These files have no text layer at all** (0 fonts) — the technical block, item
reference included, is outlined too, so everything is read by OCR or vision.
File names are kept exactly as uploaded (including `Banans_Foster`, `Vaniila_Bean`).
