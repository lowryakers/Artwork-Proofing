# End-to-end artwork fixtures

Four real ProDough converter files, as sent by the printer, plus the reference data to
proof them against. These are the exact files from run `9ffb350f`.

## Files

| File | SKU | GTIN |
|---|---|---|
| `86974_PD_pancake_buttermilk_26.10.02-ok.pdf` | PPM-BM | 850030869746 |
| `72614_PD_pancake_chocolate_26.10.02-ok.pdf` | PPM-C | 850046726149 |
| `72600_PD_pancake_cinnamon_swirl_26.10.02-ok.pdf` | PPM-CS | 850046726002 |
| `72603_PD_pancake_pumpkin_26.10.02-ok.pdf` | PPM-PS | 850046726033 |

- `master_rows.csv` — the real master-list rows for these four SKUs. Replaces the
  `FILL_ME` template. Stub the master-list fetch from this file so a network failure
  cannot turn the suite green.
- `expected.json` — expected output per clean file. All four must come back `CLEAN`
  with zero findings.
- `defects.json` — seven scenarios, six of which must produce exactly one CRITICAL.

## Why there are no `DEFECT_*.pdf` files

**All label copy in these files is converted to outlines.** `pdftotext` returns ~12,000
characters per file and not one of them is label copy — it is the die template layer
("10\"", "254 mm", "Front"). There is no text to edit, and re-typesetting a press file
would produce a fixture that no longer behaves like the real thing.

So defects are planted in the **reference data** instead. A mutated master row or
approved-panel record exercises the full pipeline — rasterize, locate the panel, read it,
compare, report — and is exactly reproducible with no binary editing. The only defect
class this cannot cover is one that lives purely inside the artwork with no external
counterpart, and there is no such check in the suite.

## One negative control

`DEFECT_spot_color_within_tolerance` must produce **zero** findings. Without it, the suite
can be satisfied by widening tolerances until nothing fails. Keep it.
