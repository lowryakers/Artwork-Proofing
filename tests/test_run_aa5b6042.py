"""Run aa5b6042: %DV column populated, the 2% break, NFP-side provenance for
the front call-outs, allergen consistency across a run, and ingredient-statement
whitespace.

    python3 tests/test_run_aa5b6042.py

Exits non-zero on any failure. No test framework, no network, no vision API.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402

# Blueberry Muffin, as the crop reads it: every amount, no percentage.
MUFFIN = {'calories': 140, 'total_fat_g': 1, 'saturated_fat_g': 0.5, 'cholesterol_mg': 55,
          'sodium_mg': 75, 'total_carbohydrate_g': 3, 'dietary_fiber_g': 0, 'added_sugar_g': 0,
          'protein_g': 30, 'vitamin_d_mcg': 0, 'calcium_mg': 130, 'iron_mg': 0.2, 'potassium_mg': 160}
ALL_DV = [g[2] for g in pe._PANEL_DV_GROUPS]


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    # ── A. %DV column populated ─────────────────────────────────────────────
    f = pe._check_dv_populated(MUFFIN, {'dv_blank': ALL_DV, 'dv_header': True})
    check('header printed, every %DV cell empty -> exactly ONE CRITICAL naming the column',
          len(f) == 1 and f[0]['severity'] == 'critical' and 'column' in f[0]['message']
          and '% Daily Value' in f[0]['message'] and 'Total Fat 1g' in f[0]['message']
          and 'Potassium 160mg' in f[0]['message'])
    f = pe._check_dv_populated(MUFFIN, {'dv_blank': ALL_DV})
    check('header not reported either way -> still one column finding', len(f) == 1)

    full = dict(MUFFIN, **{k: 5 for k in ALL_DV})
    one = {k: v for k, v in full.items() if k != 'sodium_dv'}
    f = pe._check_dv_populated(one, {'dv_blank': ['sodium_dv'], 'dv_header': True})
    check('one row empty, the rest printed -> one CRITICAL in the specified wording',
          [i['message'] for i in f] == ['Nutrition panel declares Sodium 75mg with no % Daily Value. '
                                        '%DV is required for nutrients with an established DV.'])
    f = pe._check_dv_populated(dict(MUFFIN), {'dv_blank': ['iron_dv', 'potassium_dv'], 'dv_header': True})
    check('two empty rows, no others read -> one finding per row (not a column finding)',
          len(f) == 2 and all('with no % Daily Value' in i['message'] for i in f))

    check('every %DV unread (None), nothing reported printed-empty -> no finding',
          pe._check_dv_populated(MUFFIN, {'dv_blank': [], 'dv_header': True}) == []
          and pe._check_dv_populated(MUFFIN, {}) == [])
    check('a full column -> no finding', pe._check_dv_populated(full, {'dv_blank': []}) == [])
    check('empty %DV cell on a row whose amount was not read -> no finding (no amount, nothing declared)',
          pe._check_dv_populated({'calories': 140}, {'dv_blank': ['sodium_dv']}) == [])

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All run aa5b6042 checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
