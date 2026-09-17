# ProDough bottle shrink-sleeve die — locked template

**Template id:** `prodough_bottle_shrink_sleeve` · **version:** 1
**Format:** `bottle_sleeve` (reverse / mirror-printed clear shrink film wrapping a bottle)

This die is registered once and reused for every bottle flavor. Flavor art
never triggers a re-measure — only a **new die revision** bumps
`template_version`. The canonical constants live in `die_templates.py`; a
committed JSON snapshot (`fixtures/dies/prodough_bottle_shrink_sleeve.template.json`)
mirrors it and a test locks the two together so they cannot drift.

---

## Hard-locked mm table — the numeric source of truth

FINAL dimensions from **Litho Flexo Grafics** layout **"108mmLF x 5.625inCL PLUS"**.

| Field | Value |
| --- | --- |
| Product ID | 108mmLF x 5.625inCL PLUS |
| Layflat (LF) | **108 mm** |
| Cut length (CL) | **5.625 in = 142.875 mm** |
| Useable / print area (W × H) | **218 × 138.875 mm** |
| Print width | 218 mm |
| Print height | 138.875 mm |
| Slit width | 223 mm |
| Clear strip (slit − print, right edge) | 5 mm |
| Vertical clear (cut length − print height) | 4 mm total |
| Horizontal breakdown (on the 223 mm slit) | ~51 mm │ 108 mm layflat │ ~64 mm |

The mm table is authoritative for all trim / spec / template constants.

### Provenance (documentation only — not a buy path)

Litho Flexo Grafics (A Resource Label Group Co.) · **Bill Pendleton** ·
801-994-1129 · bpendleton@lithoflexo.com · layout dated **2026-09-03** ·
locked for ArtProofing **2026-09-17**.

### ⚠️ Superseded — do not use

An earlier draft used **Impact Sleeves** dimensions (sleeve 107×125 mm, art
216×121 mm, 2.5 mm underlap). **Those are wrong and void.** They appear in code
only inside the template's `provenance.supersedes` note, and a test asserts they
are absent from the live template data. Do not re-introduce them "to average."

---

## Panel map

The Litho layout draws two fold lines bounding the 108 mm layflat centre. Across
the **print width (218 mm)** the split is `51 | 108 | 59 = 218` mm (the layout's
`51 | 108 | 64` figures are on the 223 mm slit; the extra 5 mm on the right is
the clear strip). That puts the folds at `51/218 ≈ 0.234` and `159/218 ≈ 0.729`.

| Panel | Fraction of print width | Notes |
| --- | --- | --- |
| `LEFT_WRAP` | 0.000 – 0.234 | ~51 mm, wraps to back |
| `FRONT` | 0.234 – 0.729 | **108 mm layflat centre — the display face** |
| `RIGHT_WRAP` | 0.729 – 1.000 | ~59 mm print (64 mm on slit), wraps to back |

`FRONT` is the layflat centre — the face seen when the flattened tube lies flat —
so the front-callout-vs-NFP check reads the front from there instead of the
stick-style left half (which on a 3-panel sleeve is the seam, not the front).

### PDF vs mm conflict rule

`SC_Shrink_Template_7_21_26_108LF.pdf` is a related **108LF-family** artboard and
may carry panel labels (LEFT/FRONT/RIGHT/REAR). It is a **secondary** fixture. If
its page size or drawn art size conflicts with the Litho Flexo mm table above,
**prefer the mm table**, record the delta here, and do **not** invent a third
size. As of this lock the PDF has not been re-measured against the Litho table;
the panel geometry above is derived from the Litho layout, not the PDF.

---

## Fixtures

Commit the binaries into `fixtures/dies/` with these exact names (the loader
computes their sha256 automatically once present; until then the sha reads
`pending`):

- **Primary (numeric + visual truth):**
  `prodough_bottle_shrink_sleeve_litho_flexo_108LF_5.625inCL_2026-09-03.png`
- **Secondary (related 108LF artboard, labels only):**
  `prodough_bottle_shrink_sleeve_SC_Shrink_Template_7_21_26_108LF.pdf`

See `fixtures/dies/README.md`.

---

## What the proofer does with a bottle-sleeve job

A job is treated as a bottle shrink sleeve when the filename carries the `-BTL-`
token (every live draft is `WHY-BTL-*` / `PLT-BTL-*` / `BEF-BTL-*`), a
shrink/bottle-sleeve keyword, a matched spec row of that format, or a
`format=Bottle` picker.

- **Locked die loaded** — trim, panel map and mm constants come from the
  template, not a per-file measure.
- **Print specs** — the file trim is accepted when it matches the useable print
  area (218 × 138.875 mm) **or** the full slit × cut length (223 × 142.875 mm),
  ±2 mm. Die line is **required** (missing → CRITICAL).
- **Panel-aware read** — the FRONT panel is OCR'd in isolation and folded into
  the front-vs-NFP comparison.
- **Film substrate** — reverse/mirror-printed, so the vision + mirror-flip reads
  apply (it is never routed to the pouch / no-eyemark branch).
- **Trust checks unchanged** — GTIN, NFP, net weight vs ReadyDoc `fill_weight_g`,
  ingredients, claims all run as for any product.
- **Metadata + ingest** — the result and the ReadyDoc ingest payload carry
  `template_id`, `template_version`, `die_sha256`, and the mm constants.

The `-BTL-` guard: a bottle-sleeve file can **never** silently match a stick trim
row. Stick/film rows are dropped for a `-BTL-` file; if only a stick row exists
for that flavor, the matcher declines rather than check the wrong trim.

---

## Flow (Shaun / Lowry)

1. Shaun delivers a flavor PDF built on this die (or names it `*-BTL-*`).
2. Drop it into ArtProof.Live (or the batch) — the bottle format is detected
   automatically; no recalibration.
3. Proof runs the full trust check set against the locked die.
4. On finish, the version files itself back to ReadyDoc Artwork ingest with the
   die metadata attached.

### Spec sheet

Bottle rows in the Master SKU sheet should use the Litho Flexo mm (print
218 × 138.875, or CL/LF), **never** stick trim and **never** the superseded
Impact 107×125. Set `die line required = yes`. A worked column example is in
`fixtures/dies/bottle_sleeve_spec_rows.csv`. The proofer works from the locked
template even if a bottle row is missing, but a present row adds the GTIN/flavor
match and the spec-vs-file cross-check.

### After a Railway deploy

No ArtProof UI recalibration is required — the die is compiled into the code and
loads automatically for `-BTL-` jobs. The only optional one-time step is adding
bottle rows to the Master SKU sheet (above); it is not required for the template
to load or for the die checks to run.
