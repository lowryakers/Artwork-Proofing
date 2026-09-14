"""Fixtures for the nutrition-panel vs net-weight reconciliation check.

Real cases from the proofing pass described in the change brief. Run directly:

    python3 tests/test_net_weight.py

Exits non-zero on any failure. No test framework required.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402


# (case, serving_size_g, servings_per_container, declared_net_g, actual_fill_g, expected_status)
FIXTURES = [
    ('Pancake, old panel',              65, 7,   454, 454, 'PASS'),
    ('Pancake, new panel as delivered', 98, 7,   454, 454, 'FAIL'),  # servings not updated
    ('Pancake, corrected',              98, 4.5, 454, 454, 'PASS'),  # 2.9%, within tolerance
    ('Crepe, old on-pack',              18, 12,  454, 454, 'FAIL'),  # serving weight ~1/2
    ('Crepe, revised panel',            36, 6,   454, 454, 'FAIL'),  # error survived the revision
    ('Crepe, corrected',                38, 12,  454, 454, 'PASS'),
    ('Cupcake, current artwork',        60, 12,  720, 380, 'FAIL'),  # passes vs declared, fails vs actual
    ('Cupcake, corrected',              63, 6,   380, 380, 'PASS'),
]


def _run():
    failures = []
    for name, ss, spc, declared, fill, expected in FIXTURES:
        res = pe._check_net_weight(
            {'serving_size_g': ss, 'servings_per_container': spc,
             'declared_net_weight_g': declared},
            fill_weight_g=fill,
        )
        got = res['status']
        ok = got == expected
        marker = 'ok  ' if ok else 'FAIL'
        print(f'[{marker}] {name:34s} implied={res["implied_total_g"]:>6}  {got:10s} (want {expected})')
        if not ok:
            failures.append(f'{name}: got {got}, expected {expected}')

    # The self-consistent cupcake row is the one the tool could not previously catch.
    cup = pe._check_net_weight(
        {'serving_size_g': 60, 'servings_per_container': 12, 'declared_net_weight_g': 720},
        fill_weight_g=380)
    if cup['status'] != 'FAIL':
        failures.append('cupcake self-consistent row must FAIL against real fill weight')

    # Missing fill weight must degrade to UNVERIFIED, not crash or silently pass.
    unv = pe._check_net_weight(
        {'serving_size_g': 60, 'servings_per_container': 12, 'declared_net_weight_g': 720})
    if unv['status'] != 'UNVERIFIED':
        failures.append(f'missing fill weight must be UNVERIFIED, got {unv["status"]}')

    # Corrected pancake must not false-positive on serving-rounding.
    pan = pe._check_net_weight(
        {'serving_size_g': 98, 'servings_per_container': 4.5, 'declared_net_weight_g': 454},
        fill_weight_g=454)
    if pan['status'] != 'PASS':
        failures.append(f'corrected pancake (98x4.5 vs 454) must PASS, got {pan["status"]}')

    print()
    if failures:
        print('FAILURES:')
        for f in failures:
            print('  -', f)
        return 1
    print('All net-weight reconciliation fixtures pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
