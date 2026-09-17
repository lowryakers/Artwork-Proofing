# Die fixtures

Locked die artboards + layouts referenced by `die_templates.py`. The mm table in
`die_templates.py` (and `docs/bottle-shrink-sleeve-die.md`) is the numeric source
of truth; these files are the vector / visual fixtures and the sha256 anchor.

## prodough_bottle_shrink_sleeve

FINAL die: **Litho Flexo Grafics "108mmLF x 5.625inCL PLUS"** (Bill Pendleton,
2026-09-03). Commit these binaries with the exact names below — the loader hashes
them automatically, and until they land the template's `die_sha256` reads
`pending` (that is expected, not an error):

| File | Role |
| --- | --- |
| `prodough_bottle_shrink_sleeve_litho_flexo_108LF_5.625inCL_2026-09-03.png` | **Primary** — the Litho Flexo layout (numeric + visual truth). |
| `prodough_bottle_shrink_sleeve_SC_Shrink_Template_7_21_26_108LF.pdf` | **Secondary** — related 108LF-family artboard, panel labels only. NOT the mm source of truth if it conflicts. |

> These binaries were not reachable from the session that locked the die, so they
> are documented here rather than committed. Drop them in at the paths above and
> commit; no code change is needed.

### Generated snapshot

`prodough_bottle_shrink_sleeve.template.json` is generated from `die_templates.py`
— regenerate after any template change:

```
python3 die_templates.py
```

A test (`tests/test_format_selection.py`) fails if the snapshot drifts from the
Python source.

### Superseded

An earlier draft used Impact Sleeves dims (107×125 / art 216×121 / 2.5 mm
underlap). **Void.** Do not commit Impact PNGs here.
