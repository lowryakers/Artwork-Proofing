"""Locked die / format templates for ProDough artwork proofing.

A template is registered ONCE from a confirmed die and reused for every flavor
printed on that die — flavor swaps never recalibrate. Only a new die revision
bumps ``template_version``.

The mm table below is the numeric source of truth for trim / spec / template
constants. The Litho Flexo layout PNG is the primary visual+numeric fixture; the
SC_Shrink_Template_7_21_26_108LF PDF is a related 108LF-family artboard that may
help panel labels but is NOT the source of truth if it conflicts — prefer the mm
table, document the delta in docs/bottle-shrink-sleeve-die.md, never invent a
third size (and never re-introduce the superseded Impact numbers "to average").

SUPERSEDED: an earlier draft used Impact Sleeves dims (sleeve 107×125, art
216×121, 2.5 mm underlap). Those are WRONG and void — see the die doc. They must
not appear anywhere in code except this note.

This module is the canonical Python source. ``fixtures/dies/*.template.json`` is
a committed snapshot generated from ``to_json`` (a test locks them together so
they cannot drift).
"""
import hashlib
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


# ── ProDough bottle shrink-sleeve die (FINAL — Litho Flexo Grafics) ────────────
# Hard-locked 2026-09-17 from the Litho Flexo Grafics layout "108mmLF x
# 5.625inCL PLUS" (Bill Pendleton, 09/03/26). Provenance only — not a vendor
# buy path.
PRODOUGH_BOTTLE_SHRINK_SLEEVE = {
    'template_id': 'prodough_bottle_shrink_sleeve',
    'template_version': 1,
    'format': 'bottle_sleeve',
    'substrate': 'shrink_film',          # reverse / mirror-printed clear film
    'die_product_id': '108mmLF x 5.625inCL PLUS',
    # Primary fixture: the Litho Flexo layout (numeric + visual truth).
    'die_layout_png': 'fixtures/dies/prodough_bottle_shrink_sleeve_litho_flexo_108LF_5.625inCL_2026-09-03.png',
    # Secondary, related artboard (same 108LF family) — labels only, NOT mm SoT.
    'die_pdf': 'fixtures/dies/prodough_bottle_shrink_sleeve_SC_Shrink_Template_7_21_26_108LF.pdf',
    'provenance': {
        'vendor': 'Litho Flexo Grafics (A Resource Label Group Co.)',
        'contact': 'Bill Pendleton',
        'phone': '801-994-1129',
        'email': 'bpendleton@lithoflexo.com',
        'layout_date': '2026-09-03',
        'locked_date': '2026-09-17',
        'note': 'Dimensions confirmed for ArtProofing die lock. Not a vendor PO / quote task.',
        'supersedes': 'Impact Sleeves 107×125 / art 216×121 / 2.5 mm underlap (WRONG — void).',
    },
    # Hard-locked millimetre table — the numeric source of truth.
    'mm': {
        'layflat': 108.0,                # layflat (LF)
        'cut_length_in': 5.625,          # cut length (CL) in inches
        'cut_length': 142.875,           # cut length (CL) = 5.625 in
        'print_w': 218.0,                # print width (useable)
        'print_h': 138.875,              # print height (useable)
        'slit_w': 223.0,                 # slit width
        'clear_strip': 5.0,              # slit − print (clear strip, right edge)
        'clear_vertical_total': 4.0,     # cut length − print height (142.875 − 138.875)
        'side_left_slit': 51.0,          # left region on the 223 mm slit (layout)
        'side_right_slit': 64.0,         # right region on the 223 mm slit (layout)
    },
    # Panel / fold geometry taken directly from the Litho Flexo layout: two fold
    # lines bound the 108 mm layflat centre; the remainder wraps to the back.
    # Fractions are of PRINT WIDTH (218 mm). The layout's side regions are quoted
    # on the 223 mm slit (51 | 108 | 64); after removing the 5 mm right clear
    # strip the print-relative split is 51 | 108 | 59 = 218 mm, giving the folds
    # at 51/218 and 159/218. FRONT is the 108 mm layflat centre (the display face
    # when the tube is laid flat). See docs/bottle-shrink-sleeve-die.md.
    'panel_map': [
        {'name': 'LEFT_WRAP',  'x0': 0.000, 'x1': 0.234},   # ~51 mm
        {'name': 'FRONT',      'x0': 0.234, 'x1': 0.729},   # 108 mm layflat centre
        {'name': 'RIGHT_WRAP', 'x0': 0.729, 'x1': 1.000},   # ~59 mm print (64 mm slit)
    ],
    # Spec-sheet expectations for this die. Primary trim is the useable print
    # area; the full slit × cut length is also accepted by the print-spec check.
    'spec': {
        'trim_width_mm': 218.0,          # useable print width
        'trim_height_mm': 138.875,       # useable print height
        'die_line_required': True,
        'accepted_trims_mm': [
            [218.0, 138.875],            # useable print area
            [223.0, 142.875],            # full slit × cut length
        ],
    },
}


TEMPLATES = {t['template_id']: t for t in (PRODOUGH_BOTTLE_SHRINK_SLEEVE,)}


def get(template_id):
    """Return the locked template dict for an id, or None."""
    return TEMPLATES.get(template_id)


def _sha_of(rel_path):
    try:
        with open(os.path.join(_HERE, rel_path or ''), 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except (OSError, TypeError):
        return None


def die_sha256(template):
    """SHA-256 of the primary fixture (the Litho Flexo layout PNG), or None when
    it is not yet committed. The mm table is the source of truth, so a missing
    fixture is 'pending', not an error — the hash locks the exact fixture once
    Lowry commits it. Falls back to the secondary PDF if the PNG is absent."""
    return _sha_of(template.get('die_layout_png')) or _sha_of(template.get('die_pdf'))


def fixture_shas(template):
    """SHA-256 of every registered fixture (None where not yet committed)."""
    return {
        'die_layout_png': _sha_of(template.get('die_layout_png')),
        'die_pdf': _sha_of(template.get('die_pdf')),
    }


def panel_bounds(template, width):
    """Named panel pixel x-ranges for a flat-art image of the given pixel width."""
    out = []
    for p in template.get('panel_map', []):
        out.append({'name': p['name'],
                    'x0': int(round(p['x0'] * width)),
                    'x1': int(round(p['x1'] * width))})
    return out


def front_panel_fraction(template):
    """(x0, x1) fractional bounds of the FRONT panel, or None."""
    for p in template.get('panel_map', []):
        if p['name'] == 'FRONT':
            return (p['x0'], p['x1'])
    return None


def mm_constants(template):
    """The subset of constants worth recording in job metadata / ReadyDoc ingest."""
    mm = template.get('mm', {})
    return {
        'layflat': mm.get('layflat'),
        'cut_length': mm.get('cut_length'),
        'print_w': mm.get('print_w'), 'print_h': mm.get('print_h'),
        'slit_w': mm.get('slit_w'), 'clear_strip': mm.get('clear_strip'),
    }


def to_json(template):
    """Stable JSON snapshot of a template (fixture shas resolved at call time)."""
    snap = {k: v for k, v in template.items()}
    snap['die_sha256'] = die_sha256(template)
    snap['fixture_shas'] = fixture_shas(template)
    return json.dumps(snap, indent=2, sort_keys=True)


if __name__ == '__main__':
    # Regenerate the committed JSON snapshot(s).
    out_dir = os.path.join(_HERE, 'fixtures', 'dies')
    os.makedirs(out_dir, exist_ok=True)
    for tid, tpl in TEMPLATES.items():
        with open(os.path.join(out_dir, f'{tid}.template.json'), 'w') as fh:
            fh.write(to_json(tpl) + '\n')
    print(f'Wrote {len(TEMPLATES)} template snapshot(s) to {out_dir}')
