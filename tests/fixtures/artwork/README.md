# Artwork fixtures for `tests/test_end_to_end.py`

Real press PDFs, run through the real pipeline — no mocks, no pre-extracted
values, no synthetic images. The harness scores every run: defects caught out
of defects planted, and false positives on the clean files.

**This folder ships without the PDFs** (they are not in the repo yet). Drop in:

| File | What it is |
|---|---|
| `pancake_buttermilk_clean.pdf` | verified-correct press file — serving 93g, about 5 servings |
| `pancake_chocolate_clean.pdf` | verified-correct — 83g, about 5 |
| `pancake_cinnamon_swirl_clean.pdf` | verified-correct — 88g, about 5 |
| `pancake_pumpkin_clean.pdf` | verified-correct — 88g, about 5 |
| `DEFECT_gtin_wrong.pdf` | a clean file with the barcode GTIN changed |
| `DEFECT_netweight_overstated.pdf` | a clean file with the declared net weight raised above the fill |
| `DEFECT_spot_color_off.pdf` | a clean file with one spot-color separation rebuilt in a visibly different color |
| `DEFECT_allergen_missing.pdf` | a clean file with the `Contains:` line removed |
| `DEFECT_serving_size_mismatch.pdf` | a clean file whose serving size disagrees with the approved panel |

Then fill every `FILL_ME` in `master_list.json` from the ReadyDoc `master.csv`
rows for these four SKUs (GTIN, SKU, spot-color names and hexes). The harness
refuses to score while any `FILL_ME` remains, and skips loudly (exit 0) when the
PDFs or the PDF toolchain are absent. Set `ARTPROOF_E2E_REQUIRED=1` in CI to
turn those skips into failures.
