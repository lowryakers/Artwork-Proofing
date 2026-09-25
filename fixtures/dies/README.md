# Die fixtures

Locked die artboards + layouts referenced by `die_templates.py`. The mm table in
`die_templates.py` (and `docs/bottle-shrink-sleeve-die.md`) is the numeric source
of truth; these files are the vector / visual fixtures and the sha256 anchor.

## prodough_bottle_shrink_sleeve

**LIVE — v2**: Litho Flexo Grafics **"95mmLF x 6.625inCL PLUS"** (Bill
Pendleton, 2026-09-24).

| File | Role |
| --- | --- |
| `prodough_bottle_shrink_sleeve_litho_flexo_95LF_6.625inCL_2026-09-24_converter_layout.pdf` | **Primary — committed.** The actual converter layout PDF: carries the mm legend, the Dieline OCG layer's vector geometry, and the Die/Die2 separations all in one file. `die_sha256` is a real hash (not `pending`). |

**Superseded — v1** (kept, not deleted; see
`die_templates.get_superseded('prodough_bottle_shrink_sleeve', 1)`):
Litho Flexo Grafics "108mmLF x 5.625inCL PLUS" (Bill Pendleton, 2026-09-03).

| File | Role |
| --- | --- |
| `prodough_bottle_shrink_sleeve_litho_flexo_108LF_5.625inCL_2026-09-03.png` | v1 primary — the Litho Flexo layout (numeric + visual truth). |
| `prodough_bottle_shrink_sleeve_SC_Shrink_Template_7_21_26_108LF.pdf` | v1 secondary — related 108LF-family artboard, panel labels only. |

> The v1 binaries were not reachable from the session that locked that die, so
> they were documented rather than committed at the time; if they land later, no
> code change is needed. v2's fixture **is** committed — it was the same file
> the die-lock request attached.

### Generated snapshot

`prodough_bottle_shrink_sleeve.template.json` is generated from `die_templates.py`
— regenerate after any template change:

```
python3 die_templates.py
```

A test (`tests/test_format_selection.py`) fails if the snapshot drifts from the
Python source.

### Superseded

- **v1** (108mmLF x 5.625inCL) — a different, separate die; not wrong, just
  superseded by v2. Kept intact, provenance and all, in the live template's
  `superseded` list. Do not average its numbers with v2's.
- An earlier-still draft used Impact Sleeves dims (107×125 / art 216×121 /
  2.5 mm underlap). **Void.** Exists only inside v1's own archived
  `provenance.supersedes` note. Do not commit Impact PNGs here.
