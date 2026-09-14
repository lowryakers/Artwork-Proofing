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

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All net-weight (A/B/C) checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
