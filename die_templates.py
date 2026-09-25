"""Locked die / format templates for ProDough artwork proofing.

A template is registered ONCE from a confirmed die and reused for every flavor
printed on that die — flavor swaps never recalibrate. Only a new die revision
bumps ``template_version``. A superseded revision is never deleted: it moves
into that template's ``superseded`` list, provenance block intact, so the
history of what a SKU was actually printed against stays auditable.

The mm table on the LIVE (current-version) template is the numeric source of
truth for trim / spec / template constants. Where a template also ships a
converter layout PDF (``die_pdf``), that file's own vector layers — a Dieline
OCG layer and/or named ``Die``/``Die2`` separations — are the geometric
source the panel_map and die_box_locator were derived from; never invent a
size that reconciles a mismatch, document the delta in
docs/bottle-shrink-sleeve-die.md instead.

SUPERSEDED (v1 draft, before the Litho Flexo lock ever happened): an earlier
draft used Impact Sleeves dims (sleeve 107×125, art 216×121, 2.5 mm underlap).
Those are WRONG and void — see the die doc. They must not appear anywhere in
code except this note.

This module is the canonical Python source. ``fixtures/dies/*.template.json``
is a committed snapshot generated from ``to_json`` (a test locks them
together so they cannot drift).
"""
import hashlib
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


# ── ProDough bottle shrink-sleeve die (LIVE — v2, 95mmLF x 6.625inCL PLUS) ─────
# Hard-locked 2026-09-25 from the converter layout PDF itself
# (PD_whey_bottle_apple_pie.pdf — a converter/reference layout, not flavor
# art): the mm table, the Dieline OCG layer's vector geometry, and the file's
# own live-text legend all agree. Nothing here was measured off rendered
# artwork. Supersedes v1 (108mmLF x 5.625inCL, below), which stays intact in
# `superseded` — it was never wrong, this is simply a newer, separate die.
PRODOUGH_BOTTLE_SHRINK_SLEEVE = {
    'template_id': 'prodough_bottle_shrink_sleeve',
    'template_version': 2,
    'format': 'bottle_sleeve',
    'substrate': 'shrink_film',          # reverse / mirror-printed clear film
    'die_product_id': '95mmLF x 6.625inCL PLUS',
    # Primary fixture: the converter layout PDF itself — carries the Dieline
    # OCG layer, the Die/Die2 separations, AND the live-text legend, so it is
    # both the numeric and the geometric source of truth (no PNG needed).
    'die_layout_png': None,
    'die_pdf': 'fixtures/dies/prodough_bottle_shrink_sleeve_litho_flexo_95LF_6.625inCL_2026-09-24_converter_layout.pdf',
    'provenance': {
        'vendor': 'Litho Flexo Grafics (A Resource Label Group Co.)',
        'contact': 'Bill Pendleton',
        'phone': '801-994-1129',
        'email': 'bpendleton@lithoflexo.com',
        'layout_date': '2026-09-24',
        'locked_date': '2026-09-25',
        'note': ('Dimensions and panel geometry read directly from the converter '
                 'layout PDF’s own Dieline OCG layer, Die/Die2 separations, and '
                 'live-text legend — all three agree; nothing measured from rendered '
                 'artwork. See docs/bottle-shrink-sleeve-die.md for the SIDE/FRONT/'
                 'SIDE/BACK panel-fold derivation.'),
    },
    # Hard-locked millimetre table — the numeric source of truth. Matches the
    # converter layout's own live-text legend exactly.
    'mm': {
        'layflat': 95.0,                 # layflat (LF)
        'cut_length_in': 6.625,          # cut length (CL) in inches
        'cut_length': 168.275,           # cut length (CL) = 6.625 in
        'print_w': 192.0,                # print width (useable)
        'print_h': 164.275,              # print height (useable)
        'slit_w': 197.0,                 # slit width
        'clear_strip': 5.0,              # slit − print (clear strip, right edge)
        'clear_vertical_total': 4.0,     # cut length − print height (168.275 − 164.275)
        'side_left_slit': 46.0,          # left region on the 197 mm slit (layout)
        'side_right_slit': 56.0,         # right region on the 197 mm slit (layout)
    },
    # Panel / fold geometry — derived from the Dieline layer's OWN vector paths
    # (fixtures/dies/*_converter_layout.pdf), not from the label text and not
    # from hand-measurement. Full derivation, including how a 4-face legend
    # (SIDE|FRONT|SIDE|BACK) maps onto the layout's 3 dimensioned slit regions
    # (46|95|56), is in docs/bottle-shrink-sleeve-die.md. Fractions are of
    # PRINT WIDTH (192 mm, the useable print area).
    'panel_map': [
        {'name': 'SIDE',  'x0': 0.000000, 'x1': 0.234375},   # 45.00 mm
        {'name': 'FRONT', 'x0': 0.234375, 'x1': 0.473958},   # 46.00 mm
        {'name': 'SIDE2', 'x0': 0.473958, 'x1': 0.729167},   # 49.00 mm
        {'name': 'BACK',  'x0': 0.729167, 'x1': 1.000000},   # 52.00 mm
    ],
    # Spec-sheet expectations for this die. Primary trim is the useable print
    # area; the full slit × cut length is also accepted by the print-spec check.
    'spec': {
        'trim_width_mm': 192.0,          # useable print width
        'trim_height_mm': 164.275,       # useable print height
        'die_line_required': True,
        'accepted_trims_mm': [
            [192.0, 164.275],            # useable print area
            [197.0, 168.275],            # full slit × cut length
        ],
    },
    # Where to find the REAL trim box on a file whose page/TrimBox is an
    # oversized artboard (dimension callouts, color legend, vendor footer) —
    # see docs/bottle-shrink-sleeve-die.md, "Die box vs artboard." Read from
    # the named vector layer via _extract_layer_rects, matched against
    # spec.accepted_trims_mm; the print-spec check falls back to
    # TrimBox/MediaBox unchanged if no matching rectangle is found there.
    'die_box_locator': {
        'ocg_layer': 'Dieline',
        'separations': ['Die', 'Die2'],
    },
    # v1 stays intact — it was correct for its own die, this is simply a
    # newer, separate die revision. A test locks this history in place.
    'superseded': [
        {
            'template_version': 1,
            'format': 'bottle_sleeve',
            'substrate': 'shrink_film',
            'die_product_id': '108mmLF x 5.625inCL PLUS',
            'die_layout_png': 'fixtures/dies/prodough_bottle_shrink_sleeve_litho_flexo_108LF_5.625inCL_2026-09-03.png',
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
            'mm': {
                'layflat': 108.0,
                'cut_length_in': 5.625,
                'cut_length': 142.875,
                'print_w': 218.0,
                'print_h': 138.875,
                'slit_w': 223.0,
                'clear_strip': 5.0,
                'clear_vertical_total': 4.0,
                'side_left_slit': 51.0,
                'side_right_slit': 64.0,
            },
            'panel_map': [
                {'name': 'LEFT_WRAP',  'x0': 0.000, 'x1': 0.234},
                {'name': 'FRONT',      'x0': 0.234, 'x1': 0.729},
                {'name': 'RIGHT_WRAP', 'x0': 0.729, 'x1': 1.000},
            ],
            'spec': {
                'trim_width_mm': 218.0,
                'trim_height_mm': 138.875,
                'die_line_required': True,
                'accepted_trims_mm': [[218.0, 138.875], [223.0, 142.875]],
            },
        },
    ],
}


TEMPLATES = {t['template_id']: t for t in (PRODOUGH_BOTTLE_SHRINK_SLEEVE,)}


def get(template_id):
    """Return the LIVE (current-version) template dict for an id, or None."""
    return TEMPLATES.get(template_id)


def get_superseded(template_id, version):
    """A specific prior version of a template, from its `superseded` list, or
    None. For audit / re-proof-against-history use — never for new jobs."""
    tpl = TEMPLATES.get(template_id)
    if not tpl:
        return None
    for s in tpl.get('superseded', []):
        if s.get('template_version') == version:
            return s
    return None


def _sha_of(rel_path):
    try:
        with open(os.path.join(_HERE, rel_path or ''), 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except (OSError, TypeError):
        return None


def die_sha256(template):
    """SHA-256 of the primary fixture, or None when it is not yet committed.
    The mm table is the source of truth, so a missing fixture is 'pending',
    not an error — the hash locks the exact fixture once it's committed.
    Prefers die_layout_png when present, else falls back to die_pdf (v2's
    converter-layout PDF carries both the numeric legend and the vector
    geometry, so it has no separate PNG)."""
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
