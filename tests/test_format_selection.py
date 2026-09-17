"""Bottle shrink-sleeve die: format selection, spec-row guard, locked constants.

    python3 tests/test_format_selection.py

Exits non-zero on any failure. No test framework required.

Ground truth (FINAL — Litho Flexo Grafics "108mmLF x 5.625inCL PLUS",
Bill Pendleton, 2026-09-03): layflat 108 mm, cut length 5.625 in = 142.875 mm,
print area 218 × 138.875 mm, slit 223 mm, 5 mm clear strip. The earlier Impact
Sleeves numbers (107×125 / art 216×121 / 2.5 mm underlap) are SUPERSEDED and must
not appear in the live template data.
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

    # ── Locked constants match the FINAL Litho Flexo table ────────────────────
    mm = T['mm']
    check('layflat 108 mm', mm['layflat'] == 108.0)
    check('cut length 5.625 in = 142.875 mm', mm['cut_length'] == 142.875 and mm['cut_length_in'] == 5.625)
    check('print area 218 × 138.875 mm', mm['print_w'] == 218.0 and mm['print_h'] == 138.875)
    check('slit width 223 mm', mm['slit_w'] == 223.0)
    check('clear strip 5 mm', mm['clear_strip'] == 5.0)
    check('die line required', T['spec']['die_line_required'] is True)
    check('format is bottle_sleeve', T['format'] == 'bottle_sleeve')
    check('template version present', isinstance(T['template_version'], int))

    # ── No superseded Impact numbers in the LIVE template data ────────────────
    # (They are allowed ONLY inside the provenance "supersedes" note.)
    live = json.dumps({k: v for k, v in T.items() if k != 'provenance'})
    for bad in ('107', '216', '125.0', '121.0', '2.5 mm', 'underlap'):
        check(f'Impact token "{bad}" absent from live template data', bad not in live)
    check('supersedes note names the Impact dims as void',
          'void' in T['provenance']['supersedes'].lower()
          and '107' in T['provenance']['supersedes'])

    # ── Panel map: FRONT is the 108 mm layflat centre ─────────────────────────
    names = [p['name'] for p in T['panel_map']]
    check('panel map is wrap|FRONT|wrap', names == ['LEFT_WRAP', 'FRONT', 'RIGHT_WRAP'])
    fr = dt.front_panel_fraction(T)
    # Folds sit at 51/218 and 159/218 of the print width.
    check('FRONT fraction ≈ 51/218 … 159/218',
          abs(fr[0] - 51 / 218) < 0.01 and abs(fr[1] - 159 / 218) < 0.01)
    b = dt.panel_bounds(T, 2180)
    check('panel_bounds scales to pixel width', b[1]['name'] == 'FRONT' and b[1]['x0'] == 510 and b[0]['x0'] == 0 and b[-1]['x1'] == 2180)

    # ── Fixture sha is 'pending' until the binary is committed (no crash) ──────
    check('die_sha256 is None when fixture absent (pending, not error)',
          dt.die_sha256(T) is None or isinstance(dt.die_sha256(T), str))
    check('mm_constants carries the reused numbers',
          dt.mm_constants(T)['layflat'] == 108.0 and dt.mm_constants(T)['cut_length'] == 142.875)

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
              'trim_width_mm': 218, 'trim_height_mm': 138.875, 'gtin': '222'}
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
        check('every CSV row uses Litho print trim (218 × 138.875), not Impact/stick',
              all(r['trim_width_mm'] == 218 and r['trim_height_mm'] == 138.875 for r in csv_rows))
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
