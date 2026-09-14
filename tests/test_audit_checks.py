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

    # Run e14f11f4 — two independent numbered lists (pancake + waffle) must NOT
    # read as one duplicated [1,2,3,1,2,3].
    two = ('PANCAKE INSTRUCTIONS\n1. Mix batter\n2. Heat griddle\n3. Pour batter\n'
           'WAFFLE INSTRUCTIONS\n1. Mix batter\n2. Set waffle maker\n3. Pour into waffle maker')
    check('two separate instruction lists → no false positive', not pe._check_instruction_steps(two))

    # Anchored parse beats polluting numbers (Makes 24 / superseded 7).
    tp = pe._parse_panel_from_text(
        'Serving size 4 Cupcakes (63g)\nMakes 24 Cupcakes\nAbout 6 servings per container\nNet Wt 380g')
    merged = pe._merge_panel(tp, {'servings_per_container': 24.0, 'serving_size_g': 32.0, 'unit_count': 24.0})
    check('anchored text parse wins over vision structured servings',
          merged['servings_per_container'] == 6.0 and merged['serving_size_g'] == 63.0)

    # NFP-crop: bbox parse + validation, and graceful degrade with no bbox.
    import json as _json
    check('valid nfp_bbox parsed',
          pe._parse_vision_json(_json.dumps({'nfp_bbox': [0.1, 0.2, 0.5, 0.9]}))['nfp_bbox'] == [0.1, 0.2, 0.5, 0.9])
    check('out-of-range nfp_bbox rejected',
          pe._parse_vision_json(_json.dumps({'nfp_bbox': [0.1, 0.2, 1.5, 0.9]}))['nfp_bbox'] is None)
    check('degenerate nfp_bbox rejected',
          pe._parse_vision_json(_json.dumps({'nfp_bbox': [0.5, 0.5, 0.5, 0.9]}))['nfp_bbox'] is None)
    check('_read_nfp_panel degrades to {} with no bbox', pe._read_nfp_panel('/nope.png', None) == {})

    # Net carbs uses the crop's structured components over full-page text.
    r = pe._check_net_carbs('Total Net Carbs 16g\nTotal Carbohydrate 10g Sugar Alcohol 0g Erythritol',
                            serving_g=63,
                            nfp_vals={'total_carbohydrate_g': 42.0, 'dietary_fiber_g': 5.0, 'sugar_alcohol_g': 21.0})
    check('net carbs reconciles from crop values (42-5-21=16)', r == [])

    # Net-carb gate — suspect inputs downgrade CRITICAL to SUSPECT.
    r = pe._check_net_carbs('Total Net Carbs 16g\nTotal Carbohydrate 10g Dietary Fiber 1g '
                            'Sugar Alcohol 0g\nIngredients: Erythritol, Whey', serving_g=40)
    check('net carbs on suspect inputs → SUSPECT not CRITICAL',
          any(i['severity'] == 'suspect' for i in r) and not any(i['severity'] == 'critical' for i in r))

    # "Reduced Iron" in the ingredient statement is a standard ingredient, not a
    # comparative nutrition claim — must not be flagged.
    snap = pe._build_label_snapshot(
        'Ingredients: Enriched Flour (Reduced Iron, Niacin), Sugar, Salt.', {}, {})
    check('reduced iron in ingredients is NOT a comparative claim',
          not any('reduced iron' in c.lower() for c in snap['comparative_claims']))
    # A real comparative claim in marketing copy still fires.
    snap2 = pe._build_label_snapshot('10g more protein than the leading brand.\nIngredients: Whey.', {}, {})
    check('genuine comparative claim still detected', bool(snap2['comparative_claims']))

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All audit checks (E/D/7a/7b) pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
