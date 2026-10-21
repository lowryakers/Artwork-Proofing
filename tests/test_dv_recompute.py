"""%DV recomputed from the declared amount (21 CFR 101.9(d)(7)(ii), (c)(8)).

    python3 tests/test_dv_recompute.py

Chocolate Mousse passed clean on three runs (14, 19, 21 Oct) while its panel
printed Iron 1.1mg at 4%; the declared amount allows only 6%. The panel's %DV
cells were read but never recomputed.

Exits non-zero on any failure. No test framework, no network, no vision API.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402

# The whey Chocolate Mousse panel as printed (fixture Chocolate_Mousse_.pdf).
MOUSSE = {'total_fat_g': 0.5, 'total_fat_dv': 1, 'saturated_fat_g': 0, 'saturated_fat_dv': 0,
          'cholesterol_mg': '<5', 'cholesterol_dv': 1, 'sodium_mg': 260, 'sodium_dv': 11,
          'total_carbohydrate_g': 5, 'total_carbohydrate_dv': 2, 'dietary_fiber_g': 1, 'dietary_fiber_dv': 4,
          'added_sugar_g': 0, 'added_sugars_dv': 0, 'vitamin_d_mcg': 0, 'vitamin_d_dv': 0,
          'calcium_mg': 150, 'calcium_dv': 10, 'iron_mg': 1.1, 'iron_dv': 4, 'potassium_mg': 40,
          'potassium_dv': 0}
# BEF-BTL-DCH as printed.
BEEF_DCH = {'total_fat_g': 2, 'total_fat_dv': 3, 'saturated_fat_g': 1.5, 'saturated_fat_dv': 7,
            'cholesterol_mg': 0, 'cholesterol_dv': 0, 'sodium_mg': 340, 'sodium_dv': 15,
            'total_carbohydrate_g': 5, 'total_carbohydrate_dv': 2, 'dietary_fiber_g': 2, 'dietary_fiber_dv': 6,
            'added_sugar_g': 0, 'added_sugars_dv': 0, 'vitamin_d_mcg': 0.1, 'vitamin_d_dv': 0,
            'calcium_mg': 20, 'calcium_dv': 2, 'iron_mg': 2, 'iron_dv': 10, 'potassium_mg': 333,
            'potassium_dv': 8}
BEEF_MARKS = {'other_vm': [{'name': 'Magnesium', 'amount': 27, 'unit': 'mg', 'dv': 6}]}


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    def legal(key, amount):
        return pe._dv_legal(key, pe._amount_band(key, amount))

    # The worked examples.
    l, lo, hi = legal('iron', 1.1)
    check(f'Iron 1.1mg -> 5.83%-6.39%, only 6% ({lo:.2f}-{hi:.2f}, {l})', l == [6] and round(lo, 2) == 5.83)
    l, lo, hi = legal('total_carbohydrate', 4)
    check(f'Total Carbohydrate 4g -> 1.27%-1.64%, 1% or 2% ({l})', l == [1, 2] and round(lo, 2) == 1.27)
    for amt in (0.1, 0.2, 0.3):
        check(f'Iron {amt}mg: under 2% of the DV -> 0% accepted', 0 in legal('iron', amt)[0])
    check('Iron 2mg at 10% (11.1%, nearest 5% above 10%)', 10 in legal('iron', 2)[0])
    check('Potassium 333mg at 8%', 8 in legal('potassium', 333)[0])
    check('Magnesium 27mg at 6%', 6 in legal('magnesium', 27)[0])
    check('vitamin/mineral rounding: nearest 2% to 10%, 5% to 50%, 10% above',
          pe._dv_round('iron', 9.1) == 10 and pe._dv_round('iron', 10.0) == 10 and pe._dv_round('iron', 11.1) == 10
          and pe._dv_round('iron', 12.6) == 15 and pe._dv_round('iron', 56) == 60 and pe._dv_round('sodium', 11.4) == 11)
    check('declared-amount rules: sodium 140 -> nearest 5 below, 10 above; fat 0.5g steps under 5g',
          pe._declared_macro('sodium', 142) == 140 and pe._declared_macro('sodium', 137) == 135
          and pe._declared_macro('total_fat', 4.8) == 5.0 and pe._declared_macro('total_fat', 0.4) == 0.0
          and pe._declared_macro('cholesterol', 3) == '<5' and pe._declared_macro('dietary_fiber', 0.7) == '<1')

    # Acceptance 1: Chocolate Mousse -> exactly one CRITICAL, naming Iron, 4% and 6%.
    issues, rows = pe._check_dv_recompute(MOUSSE, {})
    check(f'Chocolate Mousse: exactly one CRITICAL, Iron 4% vs 6% ({[i["message"] for i in issues]})',
          len(issues) == 1 and issues[0]['severity'] == 'critical'
          and issues[0]['message'] == ('Panel declares Iron 1.1mg with %DV 4%. The declared amount yields '
                                       '5.83%-6.39%, which rounds to 6%. No value in the rounding band produces 4%.'))
    check('...every other row of the panel passes', sum(r['result'] == 'ok' for r in rows) == 10)
    # Acceptance 2: the corrected file.
    check('Chocolate Mousse corrected to 6% -> zero findings',
          pe._check_dv_recompute(dict(MOUSSE, iron_dv=6), {})[0] == [])
    # Acceptance 4: BEF-BTL-DCH, Magnesium included.
    issues, rows = pe._check_dv_recompute(BEEF_DCH, BEEF_MARKS)
    check(f'BEF-BTL-DCH: zero findings, 12 rows evaluated incl. Magnesium ({[i["message"] for i in issues]})',
          issues == [] and sum(r['result'] == 'ok' for r in rows) == 12
          and any(r['nutrient'] == 'Magnesium' and r['result'] == 'ok' for r in rows))
    # Acceptance 5: an unreadable amount is NOT EVALUATED, never a pass.
    issues, rows = pe._check_dv_recompute(dict(MOUSSE, iron_mg=None), {})
    iron = next(r for r in rows if r['nutrient'] == 'Iron')
    check('amount unreadable -> NOT EVALUATED, no finding', issues == [] and iron['result'] == 'not_evaluated'
          and iron['why'] == 'amount not read')
    issues, rows = pe._check_dv_recompute(dict(MOUSSE, iron_dv=None), {})
    check('%DV unreadable -> NOT EVALUATED',
          next(r for r in rows if r['nutrient'] == 'Iron')['result'] == 'not_evaluated')
    # A mismatch the row re-read does not reproduce is not reported.
    issues, rows = pe._check_dv_recompute(MOUSSE, {'dv_confirm': {'iron': {'amount': 1.1, 'dv': 6}}})
    check('re-read disagrees (reads 6%) -> NOT EVALUATED, no CRITICAL',
          issues == [] and next(r for r in rows if r['nutrient'] == 'Iron')['result'] == 'not_evaluated')
    issues, _ = pe._check_dv_recompute(MOUSSE, {'dv_confirm': {'iron': {'amount': 1.1, 'dv': 4}}})
    check('re-read reproduces 1.1mg / 4% -> CRITICAL', len(issues) == 1)
    # A unit the panel does not declare in is not compared.
    _, rows = pe._check_dv_recompute(BEEF_DCH, {'other_vm': [{'name': 'Magnesium', 'amount': 27, 'unit': 'g', 'dv': 6}]})
    check('other vitamin/mineral in an unexpected unit -> NOT EVALUATED',
          next(r for r in rows if r['nutrient'] == 'Magnesium')['result'] == 'not_evaluated')

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All %DV recompute checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
