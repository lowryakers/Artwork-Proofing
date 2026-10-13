# Beef protein bottle fixtures

Beef protein bottle press proofs, plus the master rows to proof them against.

**The PDFs live in Git LFS** (`.gitattributes`: `tests/fixtures/artwork/beef/*.pdf
filter=lfs`). After cloning, `git lfs install && git lfs pull`, or the harness
reports each file as an un-pulled pointer and skips this set (loudly).

| File | SKU | GTIN | Role |
|---|---|---|---|
| `PD_beef_bottle_cinnamon_sugar.pdf` | BEF-BTL-CSG | 850079939226 | clean — F-00069 v1.2, 1 bottle (35g), 130 cal |
| `PD_beef_bottle_double_chocolate.pdf` | BEF-BTL-DCH | 850079939219 | clean — F-00065 v1.2, 1 bottle (35g), 130 cal |
| `PD_beef_bottle_toffee_cream.pdf` | BEF-BTL-TFC | 850079939233 | clean — F-00068 v1.0, 1 bottle (33g), 130 cal |
| `PD_beef_bottle_frosted_vanilla.pdf` | BEF-BTL-VNL | 850079939240 | clean — F-00064 v1.1, 1 bottle (31g), 120 cal |
| `DEFECT_front_callout_calories_beef_toffee_cream.pdf` | BEF-BTL-TFC | 850079939233 | **defect, frozen** — not current artwork |

Cinnamon Sugar and Double Chocolate were hand-verified on 9 Oct against the approved
panels of 8 Oct. Toffee Cream and Frosted Vanilla are the designer's corrected files
(received 12 Oct), re-verified by Claude against the same reference values — **not
yet hand-verified**:

- Toffee Cream: differs from the defect file only in the calorie ring (100 → 130).
- Frosted Vanilla: `Total Sugars0g` → `Total Sugars 0g`, and the cake image no longer
  bleeds below the print area. Every value matches F-00064 v1.1: 120 cal, fat 1 g,
  sat 1 g, sodium 330 mg, carb 3 g, sugars 0 g, protein 25 g, calcium 10 mg,
  potassium 124 mg, net wt 1.09 oz (31 g), rings 25G / 0G / 120 / 100%, two spots
  (PANTONE 2915 C, 3005 C).

- `master_rows.csv` — the four `BEF-BTL-*` rows with the **post-reformulation**
  fill weights from the formulator (8 Oct). The live master export still carries the
  pre-reformulation weights, which produce four false net-weight CRITICALs. Formula
  ref / version are populated here and empty in the live export.
- `expected.json` — the four clean files: serving = net weight, 1 serving. Expected
  zero CRITICAL and zero WARNING (spot colour and trim disabled), and REVIEW only from
  the claims check ("supports digestion", DSHEA disclaimer).
- `defects.json` — `DEFECT_front_callout_calories`: a **file-level** defect, the real
  Toffee Cream artwork unmodified (nothing planted). Its front ring reads "100
  Calories Per Serving"; its own NFP and approved F-00068 v1.0 read 130. Expect
  exactly one CRITICAL — on the `front_callout` check, naming calories, 100 and 130 — and
  no other finding. Three of the four rings are correct, so a reader that binds a ring
  to the wrong value either misses this or fires on a correct ring.

Things that are correct on these labels and must never produce a finding:

- **No Contains line.** None of the formulas contains a major allergen. Coconut Milk
  Powder is in all four and is **not** a tree nut (FDA food allergen Q&A, 5th ed.,
  2025), nor is coconut milk dairy — a coconut-as-tree-nut or coconut-milk-as-milk
  finding is a bug.
- Magnesium, Polyunsaturated Fat and Monounsaturated Fat rows on the NFP are
  legitimate voluntary declarations.
