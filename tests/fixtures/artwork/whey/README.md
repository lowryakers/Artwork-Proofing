# Whey protein bottle fixtures

30 whey protein bottle press proofs, as sent by the converter (runs `73096882` /
`7f8ce432`), plus the master rows to proof them against.

**The PDFs (147 MB) are not committed yet.** They are meant to live in Git LFS,
but `lfs.github.com` is blocked by the cloud environment's network policy, so the
upload was refused. Until they're here, `tests/test_end_to_end.py` skips this set
on its own (loudly) and still runs the pancake set. Once LFS is reachable: the
`.gitattributes` rule is `tests/fixtures/artwork/whey/*.pdf filter=lfs`; after
cloning, `git lfs install && git lfs pull`, or the harness reports each file as an
un-pulled pointer.

- `master_rows.csv` — the 30 `WHY-BTL-*` rows from the master export of
  2026-10-07, unmodified. The harness combines it with the pancake set's rows into
  one master list.
- `expected.json` — per file: SKU, GTIN, fill weight, and the converter's
  `Cust. Item Ref`. Verified for all 30: the decoded barcode is the master GTIN, and
  the item reference is the SKU that GTIN resolves to. Expected: zero CRITICAL and
  zero WARNING with spot colour and trim disabled. Add `serving_size_g`,
  `servings_per_container` (and optionally `serving_size_desc`,
  `declared_net_weight_g`, `overall_status`) to a file's `expect` to also assert the
  panel read and a CLEAN verdict.
- `defects.json` — `DEFECT_item_ref_mismatch`: the Double Chocolate master row's
  SKU is planted as `BEF-BTL-DCH`, so the barcode resolves to a different SKU than
  the sheet's `WHY-BTL-DCH`. Expect exactly one CRITICAL on `item_reference`.

**These files have no text layer at all** (0 fonts) — the technical block, item
reference included, is outlined too, so everything is read by OCR or vision.
File names are kept exactly as uploaded (including `Banans_Foster`, `Vaniila_Bean`).
