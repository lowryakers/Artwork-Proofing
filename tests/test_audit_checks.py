"""Audit-driven checks: cross-SKU collision (E), net carbs (D), superseded NFP
(7a), duplicate instruction steps (7b).

    python3 tests/test_audit_checks.py

Exits non-zero on any failure. No test framework required.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    # Check E — Pancake Chocolate front 320 cal vs own NFP 290; Buttermilk NFP is
    # 320. The collision names Buttermilk as the source.
    choc = {'filename': 'PD_pancake_chocolate.pdf', 'checks': {},
            'snapshot': {'front_callout': {'calories': 320}, 'nfp': {'calories': 290}}}
    butter = {'filename': 'PD_pancake_buttermilk.pdf', 'checks': {},
              'snapshot': {'front_callout': {'calories': 320}, 'nfp': {'calories': 320}}}
    pe._flag_cross_sku_collisions([choc, butter])
    coll = choc['checks'].get('nfp', {}).get('issues', [])
    check('Check E names Buttermilk as the collision source',
          any('buttermilk' in i['message'].lower() and i['severity'] == 'critical' for i in coll))

    # Check D — Net carbs recomputed from the CURRENT panel.
    bad = ('Total Net Carbs 5g\nNutrition Facts Total Carbohydrate 20g '
           'Dietary Fiber 6g Sugar Alcohol 8g')   # 20-6-8 = 6 ≠ 5
    r = pe._check_net_carbs(bad)
    check('Check D flags net-carb mismatch (front 5 vs recomputed 6)',
          any(i['severity'] == 'critical' and 'net carbs' in i['message'].lower() for i in r))
    good = ('Total Net Carbs 6g\nTotal Carbohydrate 20g Dietary Fiber 6g Sugar Alcohol 8g')
    check('Check D passes when front matches recompute', not pe._check_net_carbs(good))

    # 7a — superseded NFP: artwork 7 servings vs approved 4.5.
    r = pe._check_superseded_nfp({'servings_per_container': 7},
                                 {'approved_servings_per_container': 4.5})
    check('7a superseded NFP → CRITICAL', any(i['severity'] == 'critical' for i in r))
    check('7a no false positive when matching',
          not pe._check_superseded_nfp({'servings_per_container': 4.5},
                                       {'approved_servings_per_container': 4.5}))
    check('7a degrades quietly with no approved reference',
          not pe._check_superseded_nfp({'servings_per_container': 7}, {}))

    # 7b — duplicated / out-of-sequence instruction steps.
    dupe = 'Instructions\n1. Preheat\n2. Mix\n3. Bake\n2. Mix\n3. Bake'
    r = pe._check_instruction_steps(dupe)
    check('7b flags repeated step numbers', any('repeated' in i['message'].lower() for i in r))
    clean = 'Instructions\n1. Preheat\n2. Mix\n3. Bake'
    check('7b clean sequence → no flag', not pe._check_instruction_steps(clean))

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All audit checks (E/D/7a/7b) pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
