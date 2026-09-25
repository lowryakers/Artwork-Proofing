"""Bottle shrink-sleeve die: format selection, spec-row guard, locked constants.

    python3 tests/test_format_selection.py

Exits non-zero on any failure. No test framework required.

Ground truth (LIVE — v2, Litho Flexo Grafics "95mmLF x 6.625inCL PLUS",
Bill Pendleton, 2026-09-24, derived from the converter layout PDF's own
Dieline OCG layer + Die/Die2 separations + live-text legend): layflat 95 mm,
cut length 6.625 in = 168.275 mm, print area 192 × 164.275 mm, slit 197 mm,
5 mm clear strip. This supersedes v1 (108mmLF x 5.625inCL, 2026-09-03), which
stays intact in the template's `superseded` list rather than being deleted.
The earlier-still Impact Sleeves numbers (107×125 / art 216×121 / 2.5 mm
underlap) are void and must not appear anywhere except as an archived note
inside v1's own preserved provenance.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import die_templates as dt   # noqa: E402
import proof_engine as pe    # noqa: E402


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    T = dt.get('prodough_bottle_shrink_sleeve')

    # ── Locked constants match the LIVE (v2) converter-layout table ───────────
    mm = T['mm']
    check('template_version is 2 (live)', T['template_version'] == 2)
    check('layflat 95 mm', mm['layflat'] == 95.0)
    check('cut length 6.625 in = 168.275 mm', mm['cut_length'] == 168.275 and mm['cut_length_in'] == 6.625)
    check('print area 192 × 164.275 mm', mm['print_w'] == 192.0 and mm['print_h'] == 164.275)
    check('slit width 197 mm', mm['slit_w'] == 197.0)
    check('clear strip 5 mm', mm['clear_strip'] == 5.0)
    check('die line required', T['spec']['die_line_required'] is True)
    check('format is bottle_sleeve', T['format'] == 'bottle_sleeve')
    check('die_box_locator names the Dieline OCG layer',
          T.get('die_box_locator', {}).get('ocg_layer') == 'Dieline')

    # ── No stray Impact numbers in the LIVE (non-superseded) template data ────
    # (Impact is void everywhere except v1's own archived provenance note,
    # checked separately below — that string legitimately contains "107".)
    live = json.dumps({k: v for k, v in T.items() if k not in ('provenance', 'superseded')})
    for bad in ('107', '216', '125.0', '121.0', '2.5 mm', 'underlap'):
        check(f'Impact token "{bad}" absent from live (non-superseded) template data', bad not in live)
    # And no v1 (108LF) numbers leak into the live v2 data either — a
    # supersession must actually replace the numbers, not just bump a version.
    for bad in ('108.0', '142.875', '218.0', '138.875', '223.0'):
        check(f'v1 (108LF) token "{bad}" absent from live v2 template data', bad not in live)

    # ── v1 preserved intact, provenance block and all ─────────────────────────
    v1 = dt.get_superseded('prodough_bottle_shrink_sleeve', 1)
    check('v1 (108LF) is preserved via get_superseded, not deleted', v1 is not None)
    check('v1 keeps its own real numbers', v1['mm']['layflat'] == 108.0 and v1['mm']['cut_length'] == 142.875)
    check('v1 keeps its own provenance block (contact, dates)',
          v1['provenance']['contact'] == 'Bill Pendleton' and v1['provenance']['layout_date'] == '2026-09-03')
    check("v1's provenance still names the Impact dims as void (kept, same as before)",
          'void' in v1['provenance']['supersedes'].lower() and '107' in v1['provenance']['supersedes'])
    check('no dangling second live template_id from the supersession',
          list(dt.TEMPLATES.keys()) == ['prodough_bottle_shrink_sleeve'])

    # ── Panel map: derived from the Dieline layer's own fold geometry ─────────
    # (converter layout PDF: print-area box 32.32→224.32mm; layflat box
    # 77.32→172.32mm; a stroked fold line at 123.32mm splits FRONT from a
    # second SIDE — see docs/bottle-shrink-sleeve-die.md for the full
    # derivation, cross-checked against the SIDE/FRONT/SIDE/BACK label
    # positions in the Text layer.)
    names = [p['name'] for p in T['panel_map']]
    check('panel map is SIDE|FRONT|SIDE2|BACK (4 faces, not 3)',
          names == ['SIDE', 'FRONT', 'SIDE2', 'BACK'])
    fr = dt.front_panel_fraction(T)
    # FRONT spans print-area 77.32->123.32mm = 45.00->91.00mm from the print
    # area's own left edge, i.e. 45/192 .. 91/192 of print width.
    check('FRONT fraction is 45/192 .. 91/192 of print width',
          abs(fr[0] - 45 / 192) < 0.001 and abs(fr[1] - 91 / 192) < 0.001)
    b = dt.panel_bounds(T, 1920)
    check('panel_bounds scales to pixel width (SIDE|FRONT|SIDE2|BACK, edge to edge)',
          b[0]['x0'] == 0 and b[-1]['x1'] == 1920
          and b[0]['name'] == 'SIDE' and b[1]['name'] == 'FRONT'
          and b[2]['name'] == 'SIDE2' and b[3]['name'] == 'BACK')
    check('panel widths sum to the full print width (450+460+490+520 = 1920px @10px/mm)',
          (b[0]['x1']-b[0]['x0']) + (b[1]['x1']-b[1]['x0'])
          + (b[2]['x1']-b[2]['x0']) + (b[3]['x1']-b[3]['x0']) == 1920)

    # ── Fixture sha is real this time — the converter layout PDF is committed ──
    check('die_sha256 is a real hash (the converter-layout PDF is committed, not pending)',
          isinstance(dt.die_sha256(T), str) and len(dt.die_sha256(T)) == 64)
    check('mm_constants carries the LIVE (v2) numbers',
          dt.mm_constants(T)['layflat'] == 95.0 and dt.mm_constants(T)['cut_length'] == 168.275)

    # ── Committed JSON snapshot has not drifted from the Python source ─────────
    snap_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'fixtures', 'dies', 'prodough_bottle_shrink_sleeve.template.json')
    try:
        with open(snap_path) as fh:
            on_disk = fh.read().strip()
        check('committed JSON snapshot matches die_templates.to_json (no drift)',
              on_disk == dt.to_json(T).strip())
    except OSError:
        check('committed JSON snapshot present', False)

    # ── Format detection ──────────────────────────────────────────────────────
    check('-BTL- token → bottle sleeve', pe._is_bottle_sleeve('WHY-BTL-CPB_cinnamon.pdf'))
    check('PLT-BTL / BEF-BTL → bottle sleeve',
          pe._is_bottle_sleeve('PLT-BTL-VAN.pdf') and pe._is_bottle_sleeve('BEF-BTL-CHOC.pdf'))
    check('"shrink sleeve" phrase → bottle sleeve', pe._is_bottle_sleeve('cupcake_shrink_sleeve.pdf'))
    check('stick file is NOT a bottle sleeve', not pe._is_bottle_sleeve('WHY-STK-CPB_stick.pdf'))
    check('plain pouch is NOT a bottle sleeve', not pe._is_bottle_sleeve('powder_pouch.pdf'))
    # "btl" must be a delimited token, not any substring (e.g. "bottleneck").
    check('bare "bottle" (no -btl-/sleeve) is NOT auto bottle_sleeve',
          not pe._is_bottle_sleeve('rigid_bottle_label.pdf'))

    # Bottle sleeve is film substrate (gets the vision / mirror reads) …
    check('bottle sleeve routes to film substrate', pe._is_film_rollstock('WHY-BTL-CPB.pdf') is True)
    # … but a plain rigid bottle stays pouch (no eyemark false positives).
    check('plain bottle stays non-film', pe._is_film_rollstock('rigid_bottle_label.pdf') is False)
    # Stick still film, pouch still not.
    check('stick still film', pe._is_film_rollstock('PD_pancake_stick.pdf') is True)
    check('pouch still not film', pe._is_film_rollstock('PD_powder_pouch.pdf') is False)

    # ── Template selection ────────────────────────────────────────────────────
    sel = pe._select_die_template('WHY-BTL-CPB_cinnamon.pdf')
    check('selects bottle die for -BTL- file', bool(sel) and sel['template_id'] == 'prodough_bottle_shrink_sleeve')
    check('no template for a stick file', pe._select_die_template('WHY-STK-CPB_stick.pdf') is None)
    check('format=Bottle picker selects the die',
          bool(pe._select_die_template('unlabeled.pdf', brand_config={'format': 'Bottle'})))
    check('matched bottle-sleeve spec row selects the die',
          bool(pe._select_die_template('unlabeled.pdf',
                                       matched_spec={'packaging_type': 'Bottle Shrink Sleeve'})))

    # ── Spec-row guard: -BTL- can never silently match a stick trim ───────────
    stick = {'flavor': 'Cinnamon Bun', 'sku': 'WHY-STK-CPB', 'packaging_type': 'Stick Pack',
             'trim_width_mm': 60, 'trim_height_mm': 180, 'gtin': '111'}
    bottle = {'flavor': 'Cinnamon Bun', 'sku': 'WHY-BTL-CPB', 'packaging_type': 'Bottle Shrink Sleeve',
              'trim_width_mm': 192, 'trim_height_mm': 164.275, 'gtin': '222'}
    rows = [stick, bottle]

    check('-BTL- file with both rows → bottle row',
          pe._match_spec_row([], 'WHY-BTL-CPB_cinnamon_bun.pdf', rows).get('sku') == 'WHY-BTL-CPB')
    check('-BTL- file with ONLY a stick row → declines (no wrong trim)',
          pe._match_spec_row([], 'WHY-BTL-CPB_cinnamon_bun.pdf', [stick]) == {})
    check('stick file still matches the stick row',
          pe._match_spec_row([], 'WHY-STK-CPB_cinnamon_bun_stick.pdf', rows).get('sku') == 'WHY-STK-CPB')
    check('barcode GTIN remains authoritative for the bottle',
          pe._match_spec_row(['222'], 'WHY-BTL-CPB_cinnamon_bun.pdf', rows).get('sku') == 'WHY-BTL-CPB')
    check('bottle spec row classifies as bottle_sleeve', pe._spec_format(bottle) == 'bottle_sleeve')
    check('stick spec row classifies as film', pe._spec_format(stick) == 'film')

    # ── Works from the committed CSV fixture ──────────────────────────────────
    import csv
    csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'fixtures', 'dies', 'bottle_sleeve_spec_rows.csv')
    csv_rows = []
    try:
        with open(csv_path, newline='') as fh:
            for r in csv.DictReader(fh):
                csv_rows.append({
                    'flavor': r['Flavor'].strip().lower(),
                    'sku': r['SKU'].strip(),
                    'gtin': (r.get('GTIN/Barcode') or '').strip(),
                    'packaging_type': r['Packaging Type'].strip(),
                    'trim_height_mm': float(r['Trim Length (mm)']),
                    'trim_width_mm': float(r['Trim Width (mm)']),
                    'die_line_required': r['Die Line Required'].strip().lower() in ('yes', 'y', 'true', '1'),
                })
        check('CSV fixture parsed (3 bottle rows)', len(csv_rows) == 3)
        check('every CSV row is bottle_sleeve format',
              all(pe._spec_format(r) == 'bottle_sleeve' for r in csv_rows))
        check('every CSV row uses the LIVE Litho print trim (192 × 164.275), not v1/Impact/stick',
              all(r['trim_width_mm'] == 192 and r['trim_height_mm'] == 164.275 for r in csv_rows))
        # A -BTL- file resolves to its bottle row from the fixture …
        m = pe._match_spec_row([], 'WHY-BTL-CPB_cinnamon_protein_bun.pdf', csv_rows)
        check('CSV: -BTL- file matches its bottle row', m.get('sku') == 'WHY-BTL-CPB')
        # … and adding a stick row for that flavor never diverts it to the stick.
        with_stick = csv_rows + [stick]
        m2 = pe._match_spec_row([], 'WHY-BTL-CPB_cinnamon_protein_bun.pdf', with_stick)
        check('CSV: -BTL- still picks bottle even with a stick row present',
              m2.get('sku') == 'WHY-BTL-CPB')
    except (OSError, KeyError, ValueError) as exc:
        check(f'CSV fixture usable ({exc})', False)

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All bottle shrink-sleeve format-selection checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
