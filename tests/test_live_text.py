"""Label copy vs the approved panel, by path (live text / OCR), and the run summary.

    python3 tests/test_live_text.py

The live-text path is on hold (LIVE_TEXT_ENABLED off by default) until the
designer can supply live-text PDFs; these tests turn it on locally so it stays
working while it waits. Everything else here — the label-copy elements on the
OCR path, the "less than 2%" break, the seeded-panel tag and the JSON run
summary — is live.

Exits non-zero on any failure. No test framework, no network, no vision API.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import proof_engine as pe  # noqa: E402
import live_text as L  # noqa: E402
import live_text_fixtures as F  # noqa: E402

APPROVED = {'version': 3, 'status': 'approved', 'front_callouts': {}, 'panel': {
    'ingredients': F.INGREDIENTS, 'allergen_statement': F.CONTAINS,
    'total_fat_dv': 2, 'saturated_fat_dv': 5, 'cholesterol_dv': 0, 'sodium_dv': 14,
    'total_carbohydrate_dv': 1, 'dietary_fiber_dv': 0, 'added_sugars_dv': 0, 'vitamin_d_dv': 0,
    'calcium_dv': 2, 'iron_dv': 0, 'potassium_dv': 4}}
CROP_DV = {k: v for k, v in APPROVED['panel'].items() if k.endswith('_dv')}


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    tmp = tempfile.mkdtemp()
    P = lambda name: os.path.join(tmp, name)

    # ── Extraction from the text layer ──────────────────────────────────────
    for rot in (0, 180):
        lt = L.extract(F.make_label(P(f'r{rot}_live.pdf'), rotate=rot))
        tag = 'upright' if rot == 0 else 'upside down (pouch back panel)'
        check(f'{tag}: ingredient statement exact, line wraps joined',
              L.ingredient_statement(lt) == F.INGREDIENTS)
        check(f'{tag}: Contains line exact', L.contains_line(lt) == F.CONTAINS)
        check(f'{tag}: %DV column, row by row', L.dv_column(lt) == {
            'total_fat': '2%', 'saturated_fat': '5%', 'cholesterol': '0%', 'sodium': '14%',
            'total_carbohydrate': '1%', 'dietary_fiber': '0%', 'added_sugars': '0%', 'vitamin_d': '0%',
            'calcium': '2%', 'iron': '0%', 'potassium': '4%'})
    check('2% break: after ingredient 5 (MCT Oil Powder)',
          L.two_percent_break(F.INGREDIENTS) == {'wording': 'contains less than 2% of:', 'position': 5,
                                                 'after': 'MCT Oil Powder'})
    check('ligature glyphs are text-layer artefacts, not characters', L.clean('Layﬂat') == 'Layflat')

    # ── Pairing and layout ──────────────────────────────────────────────────
    prints, pairs, unpaired = L.pair_files(['/u/A.pdf', '/u/A_live.pdf', '/u/B.pdf', '/u/C-LiveText.pdf'])
    check('X.pdf pairs with X_live.pdf; an orphan companion is proofed on its own',
          pairs == {'/u/A.pdf': '/u/A_live.pdf'} and unpaired == ['/u/C-LiveText.pdf']
          and prints == ['/u/A.pdf', '/u/B.pdf', '/u/C-LiveText.pdf'])
    live = F.make_label(P('a_live.pdf'))
    printed = F.outline(live, P('a.pdf'))
    check('an outlined print file has no label text', not L.has_label_text(printed))
    check('outlined print vs its live file: layouts match', L.compare_layout(printed, live)[0] == 'match')
    other = F.make_label(P('b_live.pdf'), dv_rows=F.DV_ROWS[:6])
    check('another version: layout mismatch', L.compare_layout(printed, other)[0] == 'mismatch')

    # ── With the path switched on ───────────────────────────────────────────
    prev = pe.LIVE_TEXT_ENABLED
    pe.LIVE_TEXT_ENABLED = True
    try:
        own = pe._acquire_live_text(live)
        check('a print file with its own label copy is read directly', own and own['mode'] == 'own')
        comp = pe._acquire_live_text(printed, live)
        check('an outlined print + matching companion -> companion', comp and comp['mode'] == 'companion')
        rej = pe._acquire_live_text(printed, other)
        check('an outlined print + other-version companion -> rejected, with why',
              rej['mode'] == 'rejected' and 'not the same artwork' in rej['note'])

        def panel(live_info, ingredients_raw=F.INGREDIENTS, contains='Milk', approved=APPROVED):
            lc = pe._label_copy_from_live(live_info, ingredients_raw, contains, dict(CROP_DV))
            return pe._check_approved_panel({}, {}, ingredients_raw, contains, approved, label_copy=lc)

        r = panel(own)
        els = {e['element']: e for e in r['label_copy']}
        check('matching label: every element on the live_text path, every element a match',
              all(e['path'] == 'live_text' and e['result'] == 'match' for e in els.values())
              and not [i for i in r['issues'] if i['severity'] in ('critical', 'review')])

        # Character-level differences.
        appr = dict(APPROVED, panel=dict(APPROVED['panel'], ingredients=F.INGREDIENTS + '.'))
        r = panel(own, approved=appr)
        i = [x for x in r['issues'] if 'Ingredient statement' in x['message']]
        check('a trailing period only -> REVIEW, naming the character',
              len(i) == 1 and i[0]['severity'] == 'review' and 'approved "."' in i[0]['message'])
        appr = dict(APPROVED, panel=dict(APPROVED['panel'],
                                         ingredients=F.INGREDIENTS.replace('Natural Flavor', 'Natural Flavors')))
        r = panel(own, approved=appr)
        i = [x for x in r['issues'] if x['severity'] == 'critical']
        check('a changed word -> CRITICAL, showing the characters',
              len(i) == 1 and 'artwork "Flavor" vs approved "Flavors"' in i[0]['message']
              and 'character for character' in i[0]['message'])
        moved = F.INGREDIENTS.replace('MCT Oil Powder, contains less than 2% of: Cocoa Powder (processed with '
                                      'alkali)', 'MCT Oil Powder, Cocoa Powder (processed with alkali), contains '
                                      'less than 2% of:')
        appr = dict(APPROVED, panel=dict(APPROVED['panel'], ingredients=moved))
        r = panel(own, approved=appr)
        brk = {e['element']: e for e in r['label_copy']}['2% break position']
        crit = [x for x in r['issues'] if x['severity'] == 'critical']
        check('2% break in a different place -> one CRITICAL (with the statement), positions named',
              brk['result'] == 'mismatch' and len(crit) == 1 and 'after ingredient 5' in crit[0]['message']
              and 'after ingredient 6' in crit[0]['message'])
        nolive = F.make_label(P('nc_live.pdf'), contains=None)
        r = panel(pe._acquire_live_text(nolive), contains=None)
        crit = [x for x in r['issues'] if x['severity'] == 'critical']
        check('no Contains line in the live text, approved has one -> CRITICAL (a finding, not a failed read)',
              len(crit) == 1 and 'missing from the artwork' in crit[0]['message'])

        # Companion: trusted only where it agrees with the print file's own read.
        r = panel(comp)
        check('companion agreeing with the print file -> live_text path, all match',
              all(e['path'] == 'live_text' and e['result'] == 'match' for e in r['label_copy']))
        r = panel(comp, ingredients_raw=F.INGREDIENTS.replace('Guar Gum', 'Gellan Gum'))
        e = {x['element']: x for x in r['label_copy']}['Ingredient statement']
        check('companion disagreeing with the print file -> NOT EVALUATED, both versions shown',
              e['result'] == 'not_evaluated' and '[Guar]' in e['detail'] and '"Gellan"' in e['detail'])
        crop = dict(CROP_DV, sodium_dv=15)
        lc = pe._label_copy_from_live(comp, F.INGREDIENTS, 'Milk', crop)
        check('%DV: a row the print file reads differently -> NOT EVALUATED for that row only',
              set(lc['dv']['unverified']) == {'sodium_dv'})
    finally:
        pe.LIVE_TEXT_ENABLED = prev

    # ── Switched off (the default): the OCR path, untouched ─────────────────
    check('LIVE_TEXT_ENABLED is off by default', pe._env_flag('LIVE_TEXT_ENABLED', False) is False
          or os.environ.get('LIVE_TEXT_ENABLED'))
    check('off: a print file with its own text layer is not read through it',
          pe._acquire_live_text(live) is None)

    # ── OCR path: the same elements, compared in content ────────────────────
    r = pe._check_approved_panel(dict(CROP_DV), {}, F.INGREDIENTS, 'Milk', APPROVED)
    check('OCR path: four label-copy elements, each on the ocr path, all match',
          [e['element'] for e in r['label_copy']] == ['Ingredient statement', '2% break position',
                                                     'Allergen statement', '%DV column']
          and all(e['path'] == 'ocr' and e['result'] == 'match' for e in r['label_copy']))
    r = pe._check_approved_panel(dict(CROP_DV), {}, moved, 'Milk', APPROVED)
    check('OCR path: the 2% break is compared too',
          {e['element']: e for e in r['label_copy']}['2% break position']['result'] == 'mismatch')
    r = pe._check_approved_panel(dict(CROP_DV, sodium_dv=15), {}, F.INGREDIENTS, 'Milk', APPROVED)
    check('OCR path: a %DV row that differs -> CRITICAL, element mismatch',
          any('Sodium %DV' in i['message'] and i['severity'] == 'critical' for i in r['issues'])
          and {e['element']: e for e in r['label_copy']}['%DV column']['result'] == 'mismatch')

    # ── Seeded panel values ─────────────────────────────────────────────────
    seeded = dict(APPROVED, source='PANEL_SEED.csv')
    r = pe._check_approved_panel(dict(CROP_DV, sodium_dv=15), {}, F.INGREDIENTS, 'Milk', seeded)
    i = [x for x in r['issues'] if x['severity'] == 'critical']
    check('a finding against a seeded panel says so',
          i and all('seeded from PANEL_SEED.csv' in x['message'] and x.get('seeded') for x in i)
          and r['panel_source'] == 'PANEL_SEED.csv')

    # ── Run summary JSON ────────────────────────────────────────────────────
    res = {'filename': 'toffee.pdf', 'matched_spec': {'sku': 'BEF-BTL-TFC'},
           'checks': {'gtin': {'status': 'OK', 'issues': [], 'found_gtins': ['850079939233']},
                      'panel': r,
                      'front_callout': {'status': 'NOT_RUN', 'issues': []},
                      'eyemark': {'status': 'NOT_APPLICABLE', 'issues': []}},
           'live_text': {'mode': 'none'}}
    res['check_paths'] = pe._check_paths(res['checks'], None)
    job = {'id': 'j1', 'created': '2026-10-15', 'results': [res, {'filename': 'bad.pdf', 'error': 'boom'}]}
    out = pe.run_summary(job)
    rows = {(x['file'], x['check'], x['element']): x for x in out['rows']}
    check('summary: one row per check, with SKU, GTIN and path',
          rows[('toffee.pdf', 'gtin', None)]['sku'] == 'BEF-BTL-TFC'
          and rows[('toffee.pdf', 'gtin', None)]['gtin'] == '850079939233'
          and rows[('toffee.pdf', 'gtin', None)]['path'] == 'barcode')
    check('summary: results are fail / not_run / not_applicable / pass, never pass for an unrun check',
          rows[('toffee.pdf', 'panel', None)]['result'] == 'fail'
          and rows[('toffee.pdf', 'front_callout', None)]['result'] == 'not_run'
          and rows[('toffee.pdf', 'eyemark', None)]['result'] == 'not_applicable'
          and rows[('toffee.pdf', 'gtin', None)]['result'] == 'pass')
    check('summary: one row per label-copy element, with its path',
          rows[('toffee.pdf', 'panel', '%DV column')]['result'] == 'mismatch'
          and rows[('toffee.pdf', 'panel', '%DV column')]['path'] == 'ocr'
          and rows[('toffee.pdf', 'panel', None)]['panel_source'] == 'PANEL_SEED.csv')
    check('summary: an errored file is a row, not a silence',
          rows[('bad.pdf', None, None)]['result'] == 'error')
    check('summary is JSON-serialisable', bool(json.dumps(out, default=str)))

    import app as A
    with pe._jobs_lock:
        pe._jobs['j1'] = job
    try:
        resp = A.app.test_client().get('/summary/j1.json')
        check('GET /summary/<job>.json serves it', resp.status_code == 200
              and resp.get_json()['rows'] and resp.get_json()['job_id'] == 'j1')
    finally:
        with pe._jobs_lock:
            pe._jobs.pop('j1', None)

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All label-copy, live-text and run-summary checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
