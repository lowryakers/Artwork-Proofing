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

    # Check D — Net carbs recomputed from the CURRENT panel. A CRITICAL requires
    # the reliable crop-sourced components (20-6-8=6 ≠ front 5).
    r = pe._check_net_carbs('Total Net Carbs 5g',
                            nfp_vals={'total_carbohydrate_g': 20, 'dietary_fiber_g': 6, 'sugar_alcohol_g': 8})
    check('Check D flags net-carb mismatch from crop values (front 5 vs recomputed 6)',
          any(i['severity'] == 'critical' and 'net carbs' in i['message'].lower() for i in r))
    check('Check D passes when front matches recompute',
          not pe._check_net_carbs('Total Net Carbs 6g',
                                  nfp_vals={'total_carbohydrate_g': 20, 'dietary_fiber_g': 6, 'sugar_alcohol_g': 8}))
    # A mismatch from the UNRELIABLE full-page text (no crop values) must NOT be a
    # CRITICAL — it downgrades to SUSPECT.
    r2 = pe._check_net_carbs('Total Net Carbs 5g\nTotal Carbohydrate 20g Dietary Fiber 6g Sugar Alcohol 8g')
    check('Check D text-only mismatch → SUSPECT not CRITICAL',
          any(i['severity'] == 'suspect' for i in r2) and not any(i['severity'] == 'critical' for i in r2))

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

    # NFP-crop mirror handling: a mirror-printed panel (film) reads empty straight,
    # so the crop is flipped and re-read. Guarded — needs Pillow.
    try:
        import types as _types
        from PIL import Image as _PImg
        _prev_pil, _prev_img, _prev_anth, _prev_avail = (
            pe.PIL_AVAILABLE, getattr(pe, 'Image', None),
            getattr(pe, '_anthropic', None), pe.ANTHROPIC_AVAILABLE)
        pe.PIL_AVAILABLE = True
        pe.Image = _PImg
        pe.ANTHROPIC_AVAILABLE = True
        os.environ.setdefault('ANTHROPIC_API_KEY', 'test')
        _tmp = os.path.join(os.path.dirname(__file__), '_mirror_tmp.png')
        _PImg.new('RGB', (1600, 2400), 'white').save(_tmp)
        _seq = ['{"serving_size_g":null,"servings_per_container":null,"calories":null}',
                '{"serving_size_g":98,"servings_per_container":4.5,"calories":300,"protein_g":19,"total_carbohydrate_g":24}']
        _n = {'i': 0}

        class _B:
            def __init__(s, t):
                s.text = t

        class _Resp:
            def __init__(s, t):
                s.content = [_B(t)]

        class _Msgs:
            def create(s, **k):
                t = _seq[min(_n['i'], len(_seq) - 1)]
                _n['i'] += 1
                return _Resp(t)
        pe._anthropic = _types.SimpleNamespace(Anthropic=lambda api_key=None: _types.SimpleNamespace(messages=_Msgs()))
        _res = pe._read_nfp_panel(_tmp, [0.27, 0.74, 0.52, 0.95])
        check('mirror-printed crop recovered via flip retry',
              _res.get('servings_per_container') == 4.5 and _res.get('serving_size_g') == 98.0)
        os.remove(_tmp)
        pe.PIL_AVAILABLE, pe.ANTHROPIC_AVAILABLE = _prev_pil, _prev_avail
        pe.Image, pe._anthropic = _prev_img, _prev_anth
    except ImportError:
        print('[skip] mirror flip-retry test (Pillow not installed)')

    # Net carbs uses the crop's structured components over full-page text.
    r = pe._check_net_carbs('Total Net Carbs 16g\nTotal Carbohydrate 10g Sugar Alcohol 0g Erythritol',
                            serving_g=63,
                            nfp_vals={'total_carbohydrate_g': 42.0, 'dietary_fiber_g': 5.0, 'sugar_alcohol_g': 21.0})
    check('net carbs reconciles from crop values (42-5-21=16)', r == [])

    # A missed sugar-alcohol line (read 0) where the front sits below total−fiber
    # must be SUSPECT, not a CRITICAL — robust even when the ingredient text is
    # mirrored/unreadable (pumpkin: crop total 45, fiber 3, sugar alc 0, front 12).
    r_sa = pe._check_net_carbs('Total Net Carbs 12g',
                               nfp_vals={'total_carbohydrate_g': 45, 'dietary_fiber_g': 3, 'sugar_alcohol_g': 0})
    check('missed sugar-alcohol line → SUSPECT not CRITICAL',
          any(i['severity'] == 'suspect' for i in r_sa) and not any(i['severity'] == 'critical' for i in r_sa))

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
