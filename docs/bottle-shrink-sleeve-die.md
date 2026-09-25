# ProDough bottle shrink-sleeve die — locked template

**Template id:** `prodough_bottle_shrink_sleeve` · **live version:** 2
**Format:** `bottle_sleeve` (reverse / mirror-printed clear shrink film wrapping a bottle)

This die is registered once and reused for every bottle flavor. Flavor art
never triggers a re-measure — only a **new die revision** bumps
`template_version`. A superseded revision is never deleted: it moves into the
template's `superseded` list (`die_templates.get_superseded(template_id, version)`),
provenance block intact, so what a SKU was actually printed against stays
auditable. The canonical constants live in `die_templates.py`; a committed
JSON snapshot (`fixtures/dies/prodough_bottle_shrink_sleeve.template.json`)
mirrors it and a test locks the two together so they cannot drift.

---

## Hard-locked mm table — v2 (LIVE), the numeric source of truth

**Litho Flexo Grafics** converter layout **"95mmLF x 6.625inCL PLUS"**, locked
2026-09-25 directly from `PD_whey_bottle_apple_pie.pdf` — a converter/reference
layout (not flavor art), committed as the die fixture. Its **mm table, Dieline
OCG layer vector geometry, and live-text legend all agree**; nothing here was
measured off rendered artwork.

| Field | Value |
| --- | --- |
| Product ID | 95mmLF x 6.625inCL PLUS |
| Layflat (LF) | **95 mm** |
| Cut length (CL) | **6.625 in = 168.275 mm** |
| Useable / print area (W × H) | **192 × 164.275 mm** |
| Print width | 192 mm |
| Print height | 164.275 mm |
| Slit width | 197 mm |
| Clear strip (slit − print, right edge) | 5 mm |
| Vertical clear (cut length − print height) | 4 mm total |
| Horizontal breakdown (on the 197 mm slit) | 46 mm │ 95 mm layflat │ 56 mm |

The mm table is authoritative for all trim / spec / template constants.

### Provenance (documentation only — not a buy path)

Litho Flexo Grafics (A Resource Label Group Co.) · **Bill Pendleton** ·
801-994-1129 · bpendleton@lithoflexo.com · layout dated **2026-09-24** ·
locked for ArtProofing **2026-09-25**.

### ⚠️ Superseded — do not use

- **v1** (108mmLF x 5.625inCL PLUS, locked 2026-09-17) is a **different,
  separate die** — it was correct for its own converter layout, not wrong.
  It is preserved intact (mm table, panel map, spec, and its own full
  provenance block) via `die_templates.get_superseded('prodough_bottle_shrink_sleeve', 1)`.
  Do not average it with v2's numbers or treat it as the current die.
- An even earlier draft used **Impact Sleeves** dimensions (sleeve 107×125 mm,
  art 216×121 mm, 2.5 mm underlap). **Those are wrong and void.** They exist in
  code only inside v1's own archived `provenance.supersedes` note (unchanged
  from when v1 was live), and a test asserts they are absent from every
  *live, non-superseded* template field. Do not re-introduce them "to average."

---

## Panel map — derivation from the Dieline layer (not the label text)

The legend prints four face labels — **SIDE │ FRONT │ SIDE │ BACK** — over
only **three** dimensioned slit regions (46 │ 95 │ 56 mm). The fold that splits
the middle region into two faces is not named in the legend text; it was
found by reading the converter layout PDF's own **Dieline** OCG layer with
PyMuPDF (`proof_engine._extract_layer_rects`), the same way `_check_print_specs`
now reads it at proof time. Geometry (mm, PDF native page space, from the
converter layout `fixtures/dies/prodough_bottle_shrink_sleeve_litho_flexo_95LF_6.625inCL_2026-09-24_converter_layout.pdf`):

| Rectangle found on the Dieline layer | x-range (mm) | Size | Matches |
| --- | --- | --- | --- |
| Slit box | 31.32 → 228.32 | 197.00 × 168.275 mm | Slit Width × Cut Length ✓ |
| **Print-area box** | **32.32 → 224.32** | **192.00 × 164.275 mm** | Print Width × Print Height ✓ |
| Layflat box | 77.32 → 172.32 | 95.00 × (cut-length height) | Layflat ✓ |
| Underlap strip | 31.32 → 38.32 | 7.00 mm wide, full cut-length height | left-edge seam allowance |
| A single stroked (0-width) fold line | **x = 123.32** | — | splits the layflat box |

The layflat box's own edges (77.32, 172.32) are two of the four fold lines;
the file draws **one more** fold line as a plain stroke (not a filled
rectangle) at **x = 123.32 mm**, splitting the 95 mm layflat box into two
unequal panels (46.00 mm + 49.00 mm). Cross-checked against the **Text**
layer's actual label positions (`page.get_text('dict')` span bboxes,
converted to the same mm space):

| Label | Text bbox center (mm) | Falls inside region | Region center (mm) |
| --- | --- | --- | --- |
| SIDE (1st) | 54.32 | 31.32 → 77.32 (outer-left) | 54.32 — exact match |
| FRONT | 100.33 | 77.32 → 123.32 | 100.32 — exact match |
| SIDE (2nd) | 146.33 | 123.32 → 172.32 | 147.82 — within region |
| BACK | 192.33 | 172.32 → 224.32 (outer-right) | within region |

Every label's center falls inside its geometrically-derived region, and two
land on the region center to within rounding — that is the confirmation this
mapping is correct, not assumed. Going around the flat sheet left to right,
the faces are **SIDE → FRONT → SIDE → BACK**, with the seam/underlap sitting
between the tail of BACK (right edge, under the 5 mm clear-overlap strip) and
the head of the first SIDE (left edge, under the 7 mm underlap). The
"Layflat" 95 mm spec is the distance between the two fold lines that bound
that middle region (77.32 mm and 172.32 mm) — the pinch-flat width for
packing before the sleeve is applied and shrunk; it is **not** the width of
any single face.

Fractions below are of **print width** (192 mm — the useable print area,
**not** the 321.051 mm artboard; see "Die box vs artboard" below):

| Panel | mm (from print-area left edge) | Fraction of print width |
| --- | --- | --- |
| `SIDE` | 0.00 – 45.00 | 0.000000 – 0.234375 |
| `FRONT` | 45.00 – 91.00 | 0.234375 – 0.473958 |
| `SIDE2` | 91.00 – 140.00 | 0.473958 – 0.729167 |
| `BACK` | 140.00 – 192.00 | 0.729167 – 1.000000 |

`FRONT` is read by `die_templates.front_panel_fraction()` exactly as before —
the name `FRONT` is what the front-callout-vs-NFP check looks for, regardless
of how many other named panels surround it.

---

## Die box vs artboard

**The converter layout's page is not the trim.** Its MediaBox/TrimBox is
**321.051 × 250.0 mm** — it includes the dimension callouts, the color/print
legend ("Useable Print Area 164.275mm X 192mm", "46.00 mm", "56.00 mm", …),
and the vendor's contact footer, all outside the actual die. Comparing that
page size against the die's expected trim (192 × 164.275 mm, or 197 × 168.275
mm) would fail on **every** production file built on this layout — the whole
point of the layout PDF is to carry those annotations alongside the die.

Fix: `template['die_box_locator']` (`{'ocg_layer': 'Dieline', 'separations':
['Die', 'Die2']}`) tells `_check_print_specs` where to find the **real** trim.
When present, it calls `_extract_layer_rects(pdf_path, 'Dieline')` — which
walks the page's content stream (tracking the CTM and the marked-content
stack through a proper tokenizer that doesn't get confused by a text-showing
operator's string argument, e.g. the word "SIDE" or "197mm") and collects the
bounding box of every rectangle drawn while that layer is active — and checks
each candidate rectangle against `spec.accepted_trims_mm` (±2 mm, either
orientation). The first match becomes `dim_ref` for the dimension check,
**overriding** TrimBox/MediaBox; a note records that the die box was read
from the layer and that the oversized page is legitimate overhang, not a
defect. If no OCG layer of that name exists, or no candidate rectangle
matches an accepted size, the check **falls back to the original
TrimBox/MediaBox comparison unchanged** — this only activates for a template
that explicitly declares a `die_box_locator`; every other check path (no
template, a template without a locator, a non-bottle-sleeve format) is
untouched.

This is a general mechanism, not specific to one die revision — any future
template can set its own `die_box_locator` if its vendor layout has the same
"annotated artboard" shape.

---

## Fixtures

`fixtures/dies/`:

- **v2 (LIVE) primary — numeric, geometric, and visual truth, all in one file:**
  `prodough_bottle_shrink_sleeve_litho_flexo_95LF_6.625inCL_2026-09-24_converter_layout.pdf`
  — the actual converter layout (committed; `die_sha256` is a real hash, not
  `pending`). No separate PNG exists for v2 — the PDF alone carries the mm
  legend, the Dieline vector geometry, and the SIDE/FRONT/SIDE/BACK labels.
- **v1 (superseded) fixtures, kept for history:**
  `prodough_bottle_shrink_sleeve_litho_flexo_108LF_5.625inCL_2026-09-03.png` and
  `prodough_bottle_shrink_sleeve_SC_Shrink_Template_7_21_26_108LF.pdf`.

See `fixtures/dies/README.md`.

---

## What the proofer does with a bottle-sleeve job

A job is treated as a bottle shrink sleeve when the filename carries the `-BTL-`
token (every live draft is `WHY-BTL-*` / `PLT-BTL-*` / `BEF-BTL-*`), a
shrink/bottle-sleeve keyword, a matched spec row of that format, or a
`format=Bottle` picker.

- **Locked die loaded** — trim, panel map and mm constants come from the
  template (v2), not a per-file measure.
- **Print specs** — the file's real die box (read from the Dieline layer when
  present; TrimBox/MediaBox otherwise) is accepted when it matches the
  useable print area (192 × 164.275 mm) **or** the full slit × cut length
  (197 × 168.275 mm), ±2 mm. Die line is **required** (missing → CRITICAL).
- **Panel-aware read** — the FRONT panel is OCR'd in isolation and folded into
  the front-vs-NFP comparison.
- **Film substrate** — reverse/mirror-printed, so the vision + mirror-flip reads
  apply (it is never routed to the pouch / no-eyemark branch).
- **Trust checks unchanged** — GTIN, NFP, net weight vs ReadyDoc `fill_weight_g`,
  ingredients, claims all run as for any product.
- **Metadata + ingest** — the result and the ReadyDoc ingest payload carry
  `template_id`, `template_version` (2), `die_sha256`, and the mm constants.

The `-BTL-` guard: a bottle-sleeve file can **never** silently match a stick trim
row. Stick/film rows are dropped for a `-BTL-` file; if only a stick row exists
for that flavor, the matcher declines rather than check the wrong trim.

---

## Flow (Shaun / Lowry)

1. Shaun delivers a flavor PDF built on this die (or names it `*-BTL-*`).
2. Drop it into ArtProof.Live (or the batch) — the bottle format is detected
   automatically; no recalibration.
3. Proof runs the full trust check set against the locked die (v2).
4. On finish, the version files itself back to ReadyDoc Artwork ingest with the
   die metadata attached.

### Spec sheet

Bottle rows in the Master SKU sheet should use the **v2** Litho Flexo mm
(print 192 × 164.275, or CL/LF), **never** stick trim, **never** v1's
108×138.875, and **never** the void Impact 107×125. Set `die line required =
yes`. A worked column example is in `fixtures/dies/bottle_sleeve_spec_rows.csv`
(updated to v2's numbers). The proofer works from the locked template even if
a bottle row is missing, but a present row adds the GTIN/flavor match and the
spec-vs-file cross-check.

### After a Railway deploy

No ArtProof UI recalibration is required — the die is compiled into the code
and loads automatically for `-BTL-` jobs. The only optional one-time step is
updating bottle rows already in the Master SKU sheet from v1's trim
(218 × 138.875) to v2's (192 × 164.275) if any were entered against v1; it is
not required for the template to load or for the die checks to run.
