"""Net-weight reconciliation — Checks A (exact), B (back-calc), C (±5%).

    python3 tests/test_net_weight.py

Exits non-zero on any failure. No test framework required.
Ground truth (from the audited run): pancake 454g, cupcake 380g, crepe 454g fill.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402


def _r(ss, spc, dnw, fill):
    return pe._check_net_weight(
        {'serving_size_g': ss, 'servings_per_container': spc, 'declared_net_weight_g': dnw},
        fill_weight_g=fill)


def _has(res, needle):
    return any(needle in i['message'].lower() for i in res['issues'])


def _sev(res, sev):
    return any(i['severity'] == sev for i in res['issues'])


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    # Pancake bad: 98×7=686 declared, 454 fill — Check A (overstated) + Check B (derived).
    r = _r(98, 7, 686, 454)
    check('pancake 686/454 → CRITICAL', r['status'] == 'CRITICAL')
    check('pancake → back-calc (derived) flagged', _has(r, 'derived'))
    check('pancake → overstated called out', _has(r, 'overstated'))

    # Cupcake bad: 63×6=378 declared, 380 fill — 0.5% gap MUST still be CRITICAL.
    r = _r(63, 6, 378, 380)
    check('cupcake 378/380 → CRITICAL (0.5% not hidden)', r['status'] == 'CRITICAL')
    check('cupcake → back-calc (derived) flagged', _has(r, 'derived'))

    # Correct files must pass Check B (no exact match) and reconcile.
    check('pancake 98×4.5 vs 454 → PASS', _r(98, 4.5, 454, 454)['status'] == 'PASS')
    check('crepe 38×12=456 vs 454 → PASS', _r(38, 12, 454, 454)['status'] == 'PASS')

    # Crepe with a misread servings (7 instead of 12): net weight is right, so it
    # is a SUSPECT READ, never a CRITICAL.
    r = _r(38, 7, 454, 454)
    check('crepe misread(7) → SUSPECT not CRITICAL', r['status'] == 'SUSPECT' and not _sev(r, 'critical'))

    # Back-calculation is caught even with NO fill weight (Check B needs none).
    r = _r(98, 7, 686, None)
    check('pancake 686 (no fill) → back-calc still CRITICAL', r['status'] == 'CRITICAL' and _has(r, 'derived'))

    # No fill + a non-derived, self-consistent panel → UNVERIFIED, not reassurance.
    r = _r(98, 4.5, 454, None)
    check('no fill, clean panel → UNVERIFIED', r['status'] == 'UNVERIFIED')
    check('UNVERIFIED note leads with the limitation, not reconciliation',
          any('not verified' in n.lower() for n in r['notes'])
          and not any('reconciles to the declared' in n.lower() for n in r['notes']))

    # Regression (job ba9d3aee): an unreadable panel WITH a fill weight must not
    # crash and must never default to PASS.
    empty = {'serving_size_g': None, 'servings_per_container': None,
             'declared_net_weight_g': None, 'unit_count': None,
             'serving_size_cups': None, 'serving_size_desc': ''}
    r = pe._check_net_weight(empty, fill_weight_g=380)
    check('unreadable panel + fill → UNVERIFIED (no crash, never PASS)', r['status'] == 'UNVERIFIED')

    # A fill-weight PASS must NOT rest on a Tesseract-only serving read (the
    # arithmetic-twin false-PASS hole). Reconciles, but OCR-only → SUSPECT.
    r_ocr = pe._check_net_weight({'serving_size_g': 63, 'servings_per_container': 6,
                                  'declared_net_weight_g': 380}, fill_weight_g=380,
                                 serving_vision_backed=False)
    check('reconciling fill-weight PASS on OCR-only serving → SUSPECT', r_ocr['status'] == 'SUSPECT')
    r_vis = pe._check_net_weight({'serving_size_g': 63, 'servings_per_container': 6,
                                  'declared_net_weight_g': 380}, fill_weight_g=380,
                                 serving_vision_backed=True)
    check('reconciling fill-weight PASS on vision-backed serving → PASS', r_vis['status'] == 'PASS')

    # The vision gate FORCES vision when a fill weight is on file (net-weight
    # verdict in play) — even if Tesseract read everything else.
    _orig = pe._ocr_needs_vision
    pe._ocr_needs_vision = lambda *a, **k: False   # pretend Tesseract read the nutrition
    try:
        _complete = 'nutrition facts calories 130 protein 25 contains: milk'
        check('gate: no fill + complete OCR → vision skipped',
              pe._should_run_vision(_complete, '', 'PD_cupcake.pdf', None) is False)
        check('gate: fill on file forces vision',
              pe._should_run_vision(_complete, '', 'PD_cupcake.pdf', 380.0) is True)
    finally:
        pe._ocr_needs_vision = _orig

    # Single-serving stick pack: net weight == serving × 1 is correct by
    # definition, NOT a back-calculation — must not fire Check B.
    stick = _r(35, 1, 35, None)
    check('single-serving stick not flagged as back-calc',
          not _has(stick, 'derived'))
    check('single-serving stick w/ matching fill → PASS',
          pe._check_net_weight({'serving_size_g': 35, 'servings_per_container': 1,
                                'declared_net_weight_g': 35}, fill_weight_g=35)['status'] == 'PASS')

    # Partial panel (serving read, servings missing) + fill → not PASS.
    r = pe._check_net_weight(
        {'serving_size_g': 63, 'servings_per_container': None, 'declared_net_weight_g': 380,
         'unit_count': 24, 'serving_size_cups': None, 'serving_size_desc': '4 Cupcakes'},
        fill_weight_g=380)
    check('partial panel + fill → not PASS', r['status'] in ('UNVERIFIED', 'SUSPECT', 'CRITICAL'))

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All net-weight (A/B/C) checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
