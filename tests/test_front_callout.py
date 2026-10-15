"""Front call-out rings vs the Nutrition Facts panel (check `front_callout`).

    python3 tests/test_front_callout.py

Runs 142db86d and eccfd9c9: Toffee Cream's front ring reads "100 Calories Per
Serving", its NFP and approved panel read 130, and the run filed no CRITICAL.
Each ring is read as a unit (number + caption from the same badge) and is
compared only with the NFP field its own caption names. Apple Pie, the original
bug: an Added Sugar ring compared against NFP protein.

Exits non-zero on any failure. No test framework, no network, no vision API.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402

# Every NFP value from the panel crop, as the pipeline reports it when the crop read.
CROP = {f: 'nfp_crop' for f in ('calories', 'protein_g', 'added_sugar_g')}

NFP = {'calories': 130, 'protein_g': 26, 'added_sugar_g': 0}


def _rings(*pairs):
    return {'rings': [{'position': pos, 'number': n, 'caption': c} for pos, n, c in pairs]}


TOFFEE = _rings(('top-left', '26G', 'Protein Per Serving'), ('top-right', '0G', 'Added Sugar'),
                ('bottom-left', '100', 'Calories Per Serving'), ('bottom-right', '100%', 'Grass-Fed Beef'))


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    def crit(out):
        return [i for i in out['issues'] if i['severity'] == 'critical']

    def rev(out):
        return [i for i in out['issues'] if i['severity'] == 'review']

    # ── The defect ──────────────────────────────────────────────────────────
    out = pe._check_front_callout(TOFFEE, {}, NFP, vision_ran=True, nfp_sources=CROP)
    c = crit(out)
    check('Toffee Cream: exactly one CRITICAL, naming calories, 100 and 130',
          len(c) == 1 and 'calories' in c[0]['message'] and ' 100' in c[0]['message']
          and '130' in c[0]['message'] and not rev(out))
    check('...the three correct rings match or are not compared, and say so',
          [r['verdict'] for r in out['rings']] == ['match', 'match', 'MISMATCH', 'not compared (claim)'])
    check('...each ring records the caption it was bound to and its reader',
          all(r['reader'] == 'front_ring_read' and r['caption'] for r in out['rings'])
          and out['rings'][2]['field'] == 'calories' and out['rings'][2]['nfp_value'] == 130)
    check('...status FAIL (the check ran and found a mismatch)', out['status'] == 'FAIL')

    # ── Correct rings ───────────────────────────────────────────────────────
    ok = _rings(('top-left', '26G', 'Protein Per Serving'), ('top-right', '0G', 'Added Sugar'),
                ('bottom-left', '130', 'Calories Per Serving'), ('bottom-right', '100%', 'Grass-Fed Beef'))
    out = pe._check_front_callout(ok, {'calories': 130, 'protein_g': 26}, NFP, vision_ran=True, nfp_sources=CROP)
    check('a correct bottle: zero findings, status OK', out['issues'] == [] and out['status'] == 'OK')

    # ── Binding is by caption, never by position or order ───────────────────
    shuffled = {'rings': list(reversed(ok['rings']))}
    check('ring order does not change the result',
          pe._check_front_callout(shuffled, {}, NFP, vision_ran=True, nfp_sources=CROP)['issues'] == [])
    apple = _rings(('a', '25G', 'Protein Per Serving'), ('b', '1G', 'Added Sugar'),
                   ('c', '120', 'Calories Per Serving'))
    out = pe._check_front_callout(apple, {}, {'calories': 120, 'protein_g': 25, 'added_sugar_g': 1},
                                  vision_ran=True, nfp_sources=CROP)
    check('Apple Pie: the 1G Added Sugar ring is compared with added sugars, never with protein',
          out['issues'] == [] and out['rings'][1]['field'] == 'added_sugar_g')

    # ── NOT EVALUATED: only the ring that could not be read ─────────────────
    out = pe._check_front_callout(_rings(('a', '26G', None), ('b', '100', 'Calories Per Serving')),
                                  {}, NFP, vision_ran=True, nfp_sources=CROP)
    check('caption unreadable -> NOT EVALUATED for that ring only; the calorie ring still CRITICAL',
          len(rev(out)) == 1 and 'caption could not be read' in rev(out)[0]['message']
          and len(crit(out)) == 1 and out['status'] == 'UNVERIFIED')
    out = pe._check_front_callout(_rings(('a', None, 'Calories Per Serving')), {}, NFP, vision_ran=True, nfp_sources=CROP)
    check('number unreadable -> NOT EVALUATED', len(rev(out)) == 1 and 'number could not be read'
          in rev(out)[0]['message'] and out['status'] == 'UNVERIFIED')
    out = pe._check_front_callout(_rings(('a', '0G', 'Sugar')), {}, NFP, vision_ran=True, nfp_sources=CROP)
    check('"Sugar" (added or total?) -> NOT EVALUATED, never bound by guess',
          len(rev(out)) == 1 and 'does not name one' in rev(out)[0]['message'] and not crit(out))
    out = pe._check_front_callout(_rings(('a', '130', 'Calories Per Serving')), {}, {'protein_g': 26},
                                  vision_ran=True, nfp_sources=CROP)
    check('NFP field not read -> NOT EVALUATED', len(rev(out)) == 1 and 'was not read' in rev(out)[0]['message'])
    out = pe._check_front_callout(_rings(('a', '100', 'Calories Per Serving'), ('b', '130', 'Calories')),
                                  {}, NFP, vision_ran=True, nfp_sources=CROP)
    check('two calorie rings with different numbers -> NOT EVALUATED, not a pick',
          len(rev(out)) == 2 and not crit(out))
    out = pe._check_front_callout(_rings(('a', '26G', 'Protein Per Serving')), {'calories': 130}, NFP,
                                  vision_ran=True, nfp_sources=CROP)
    check('a calorie call-out only the full-page pass saw -> NOT EVALUATED, not compared',
          len(rev(out)) == 1 and 'only the full-page pass saw it' in rev(out)[0]['message'])

    # ── Did it run? Always one of three answers ─────────────────────────────
    out = pe._check_front_callout({'rings': []}, {}, NFP, vision_ran=True, nfp_sources=CROP)
    check('no rings on the artwork -> NOT_APPLICABLE, no finding',
          out['status'] == 'NOT_APPLICABLE' and out['issues'] == [])
    out = pe._check_front_callout({'rings': []}, {'calories': 100}, NFP, vision_ran=True, nfp_sources=CROP)
    check('ring read found none but the full page saw some -> NOT_RUN', out['status'] == 'NOT_RUN')
    out = pe._check_front_callout({}, {}, NFP, vision_ran=True, nfp_sources=CROP, diag=['error: Timeout'])
    check('ring read failed -> NOT_RUN, with the reason', out['status'] == 'NOT_RUN'
          and 'error: Timeout' in out['notes'][0])
    out = pe._check_front_callout(None, {}, NFP, vision_ran=False, nfp_sources=CROP)
    check('vision did not run -> NOT_RUN', out['status'] == 'NOT_RUN')
    for st, want in (('NOT_RUN', 'skipped'), ('UNVERIFIED', 'skipped'), ('OK', 'run'), ('FAIL', 'run')):
        run, skipped = pe._completeness({'front_callout': {'status': st}})
        check(f'status {st} -> {want}', (skipped if want == 'skipped' else run) == ['front_callout'])
    check('NOT_APPLICABLE -> listed as not applicable, neither run nor skipped',
          pe._completeness({'front_callout': {'status': 'NOT_APPLICABLE'}}) == ([], [])
          and pe._not_applicable_checks({'front_callout': {'status': 'NOT_APPLICABLE'}}) == ['front_callout'])

    # ── CRITICAL is not softened by a doubtful read elsewhere on the file ───
    toffee = pe._check_front_callout(TOFFEE, {}, NFP, vision_ran=True, nfp_sources=CROP)
    checks = {'front_callout': toffee,
              'netwt': {'issues': [{'severity': 'suspect', 'message': 'SUSPECT READ — serving size'}]}}
    pe._downgrade_criticals_on_suspect_file(checks)
    check('a suspect read elsewhere does not downgrade the calorie CRITICAL',
          len(crit(toffee)) == 1)

    # ── The ring read transcribes; binding is ours ──────────────────────────
    r = pe._parse_ring({'number': '<1G', 'caption': 'Added Sugar'})
    check('"<1G" Added Sugar is kept as the threshold form "<1"', r['value'] == '<1')
    check('"<1" ring equals "<1" NFP, and never equals 0',
          pe._same_amount('<1', '<1') and not pe._same_amount(0.0, '<1'))

    # ── The same tiled read copies the DSHEA disclaimer (mocked API) ────────
    import json, tempfile, types
    from PIL import Image
    tmp = os.path.join(tempfile.mkdtemp(), 'p.png')
    Image.new('RGB', (1600, 1200), 'white').save(tmp)
    reply = json.dumps({'rings': [{'position': 'a', 'number': '130', 'caption': 'Calories Per Serving'}],
                        'disclaimer': '*These statements have not been evaluated by the FDA.'})

    class _R:
        content, stop_reason = [types.SimpleNamespace(text=reply)], 'end_turn'
    prev = (pe.ANTHROPIC_AVAILABLE, pe._anthropic)
    os.environ.setdefault('ANTHROPIC_API_KEY', 'test')
    pe.ANTHROPIC_AVAILABLE = True
    pe._anthropic = types.SimpleNamespace(Anthropic=lambda api_key=None: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **k: _R())))
    try:
        got = pe._read_front_callouts(tmp, None)
        check('the tiled read returns the rings and the disclaimer it copied',
              len(got['rings']) == 1 and got['disclaimer'].startswith('*These statements'))
    finally:
        pe.ANTHROPIC_AVAILABLE, pe._anthropic = prev

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All front call-out checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
