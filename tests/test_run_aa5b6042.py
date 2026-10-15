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

# Apple Pie, run aa5b6042: same ingredient set, Natural Flavors on the other
# side of the break.
APPLE_ART = ('Ingredients: Whey Protein Isolate (Milk), Water, Erythritol, MCT Oil Powder, Natural '
             'Flavors, contains less than 2 percent of Guar Gum, Himalayan Pink Salt, Cinnamon, '
             'Stevia Leaf Extract.')
APPLE_APPR = ('Ingredients: Whey Protein Isolate (Milk), Water, Erythritol, MCT Oil Powder, contains '
              'less than 2 percent of Natural Flavors, Guar Gum, Himalayan Pink Salt, Cinnamon, '
              'Stevia Leaf Extract.')
APPROVED = {'version': 3, 'status': 'approved', 'panel': {'ingredients': APPLE_APPR}}


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

    # ── B. The 2% break ─────────────────────────────────────────────────────
    import live_text as L
    b = L.two_percent_break(APPLE_ART)
    check('"contains less than 2 percent of" is recognised as the break',
          b and b['position'] == 5 and b['after'] == 'Natural Flavors')
    check('"2 percent or less of" too', L.two_percent_break('Salt, 2 percent or less of: Guar Gum')['position'] == 1)
    check('the ingredient list does not keep the break wording as an ingredient',
          'guar gum' in pe._ingredient_list(APPLE_ART)
          and not any('percent' in x for x in pe._ingredient_list(APPLE_ART)))
    check('revision snapshot finds the minor group under "percent" wording',
          pe._build_label_snapshot(APPLE_ART, {}, {})['ingredients_minor_from'] == 5)

    r = pe._check_approved_panel({}, {}, APPLE_ART, None, APPROVED)
    crit = [i for i in r['issues'] if i['severity'] == 'critical']
    brk = {e['element']: e for e in r['label_copy']}['2% break position']
    check('Apple Pie: an ingredient crossing the break vs approved -> exactly one CRITICAL naming it',
          len(crit) == 1 and 'Natural Flavors is above the "less than 2%" break on the artwork (2% or more)'
          in crit[0]['message'] and 'below it on the approved panel' in crit[0]['message'])
    check('...the break element is a mismatch listing the crossing ingredient',
          brk['result'] == 'mismatch' and brk['crossed'] == ['Natural Flavors'])
    r = pe._check_approved_panel({}, {}, APPLE_APPR, None, APPROVED)
    check('the same statement as approved -> no CRITICAL, break matches',
          not [i for i in r['issues'] if i['severity'] == 'critical']
          and {e['element']: e for e in r['label_copy']}['2% break position']['result'] == 'match')
    down = APPLE_APPR.replace('MCT Oil Powder, contains less than 2 percent of Natural Flavors',
                              'contains less than 2 percent of MCT Oil Powder, Natural Flavors')
    r = pe._check_approved_panel({}, {}, down, None, APPROVED)
    crit = [i for i in r['issues'] if i['severity'] == 'critical']
    check('crossing the other way (below on the artwork) -> CRITICAL naming it',
          len(crit) == 1 and 'MCT Oil Powder is below the "less than 2%" break on the artwork' in crit[0]['message'])

    # ── E. NFP-side provenance for the front call-outs ──────────────────────
    rings = {'rings': [{'position': 'a', 'number': '120', 'caption': 'Calories Per Serving'},
                       {'position': 'b', 'number': '25G', 'caption': 'Protein Per Serving'}]}
    nfp = {'calories': 120, 'protein_g': 25}
    out = pe._check_front_callout(rings, {}, nfp, vision_ran=True,
                                  nfp_sources={'calories': 'vision_full_page', 'protein_g': 'nfp_crop'})
    by = {r['field']: r for r in out['rings']}
    check('NFP value from the full-page pass -> NOT EVALUATED, never match, even when equal',
          by['calories']['verdict'] == 'NOT EVALUATED' and by['calories']['nfp_reader'] == 'vision_full_page'
          and any('vision_full_page' in i['message'] and i['severity'] == 'review' for i in out['issues']))
    check('...the crop-sourced ring still compares, and records nfp_crop',
          by['protein_g']['verdict'] == 'match' and by['protein_g']['nfp_reader'] == 'nfp_crop')
    check('...and the check is UNVERIFIED, not OK', out['status'] == 'UNVERIFIED')
    out = pe._check_front_callout(rings, {}, dict(nfp, calories=130), vision_ran=True,
                                  nfp_sources={'calories': 'vision_full_page', 'protein_g': 'nfp_crop'})
    check('a full-page value that DIFFERS is not a CRITICAL either (untrusted both ways)',
          not [i for i in out['issues'] if i['severity'] == 'critical'])
    out = pe._check_front_callout(rings, {}, nfp, vision_ran=True, nfp_sources={})
    check('reader not recorded -> NOT EVALUATED',
          all(r['verdict'] == 'NOT EVALUATED' for r in out['rings']))

    # ── D. A tightened panel crop of the wrong shape falls back ─────────────
    check('904x433 letterbox inside a tall 1046x3094 box -> rejected',
          pe._implausible_tightened_crop(904, 433, 1046, 3094) is not None)
    check('a tall tightened panel inside a tall box -> kept',
          pe._implausible_tightened_crop(1000, 2900, 1046, 3094) is None)
    check('a wide tabular panel inside a wide box -> kept (shape, not orientation)',
          pe._implausible_tightened_crop(1900, 800, 2000, 900) is None)
    check('right height but the box is tall and the region squat -> rejected on aspect',
          'aspect' in (pe._implausible_tightened_crop(1400, 1600, 1100, 3000) or ''))

    # ── C. Allergen consistency ─────────────────────────────────────────────
    whey = 'Whey Protein Isolate (Milk), Coconut Milk Powder, Natural Flavors, Erythritol, Guar Gum'
    beef = 'Beef Protein Isolate, Coconut Milk Powder, Natural Flavors, Cocoa Powder, Guar Gum'
    check('coconut maps to no allergen (not a tree nut, coconut milk is not milk)',
          pe._ingredient_allergens(['coconut milk powder', 'coconut', 'coconut cream']) == {})
    pw = pe._allergen_profile(whey, 'Milk, Tree Nuts')
    f = pe._check_declared_without_source(pw)
    check('Contains declares Tree Nuts, no ingredient sources it -> one WARNING, says coconut is not a tree nut',
          len(f) == 1 and f[0]['severity'] == 'warning' and 'declares Tree Nuts' in f[0]['message']
          and 'Coconut is not a tree nut' in f[0]['message'])
    check('Milk, sourced by whey -> no finding',
          pe._check_declared_without_source(pe._allergen_profile(whey, 'Milk')) == [])
    check('a sub-ingredient sources it ("Lecithin (Soy)") -> no finding',
          pe._check_declared_without_source(pe._allergen_profile(
              'Whey (Milk), Lecithin (Soy), Salt, Sugar', 'Milk, Soy')) == [])
    check('no ingredient statement read -> nothing (not evidence)',
          pe._check_declared_without_source(pe._allergen_profile('', 'Milk, Tree Nuts')) == [])

    def _res(name, stmt, contains):
        return {'filename': name, 'snapshot': {'ingredients_raw': stmt}, 'checks': {},
                'vision_allergens': {'contains_statement': contains} if contains else {}}
    rs = [_res('Coconut_Cream_.pdf', whey, 'Milk, Tree Nuts'), _res('beef_toffee.pdf', beef, None),
          _res('beef_vanilla.pdf', beef, None)]
    pe._flag_allergen_consistency(rs)
    w = {r['filename']: [i['message'] for i in r['checks'].get('fda', {}).get('issues', [])
                         if i['severity'] == 'warning'] for r in rs}
    check('same ingredient, declared differently across the run -> a WARNING on each side, naming '
          'both files and the ingredient',
          len(w['Coconut_Cream_.pdf']) == 1 and 'beef_toffee.pdf' in w['Coconut_Cream_.pdf'][0]
          and 'beef_vanilla.pdf' in w['Coconut_Cream_.pdf'][0]
          and 'Coconut Milk Powder' in w['Coconut_Cream_.pdf'][0]
          and len(w['beef_toffee.pdf']) == 1 and 'Coconut_Cream_.pdf' in w['beef_toffee.pdf'][0])
    check('...and not on every other shared ingredient (Natural Flavors, Guar Gum)',
          not any('Natural Flavors' in m or 'Guar Gum' in m for v in w.values() for m in v))
    check('...two beef files that agree say nothing about each other',
          not any('beef_vanilla.pdf' in m for m in w['beef_toffee.pdf']))
    rs = [_res('a.pdf', 'Whey Protein (Milk), Sugar, Salt', 'Milk'), _res('b.pdf', 'Whey Protein (Milk), Sugar, Cocoa', None)]
    pe._flag_allergen_consistency(rs)
    check('an allergen-mapped ingredient declared in one file and not the other -> WARNING',
          any('Whey Protein' in i['message'] and 'does not declare Milk' in i['message']
              for i in rs[1]['checks']['fda']['issues']))
    rs = [_res('a.pdf', beef, None), _res('b.pdf', beef, None)]
    pe._flag_allergen_consistency(rs)
    check('coconut on files that agree -> no finding anywhere', all(not r['checks'] for r in rs))

    # ── F. Ingredient-statement whitespace ──────────────────────────────────
    f = pe._ingredient_whitespace_issues(
        'Whey Protein Isolate (Milk), SteviaLeaf Extract, GuarGum, NaturalFlavors, MCT Oil '
        'Powder,Himalayan Pink Salt, Citric Acid,Stevia')
    check('every instance from the run named, in one WARNING',
          len(f) == 1 and f[0]['severity'] == 'warning'
          and all(x in f[0]['message'] for x in ('"SteviaLeaf"', '"GuarGum"', '"NaturalFlavors"',
                                                  '"Powder,Himalayan"', '"Acid,Stevia"')))
    check('a correctly spaced statement -> nothing',
          pe._ingredient_whitespace_issues('Whey Protein Isolate (Milk), Stevia Leaf Extract, Guar Gum, '
                                           'MCT Oil Powder, Himalayan Pink Salt') == [])
    check('all-caps words, pH, McIntosh, "1,000" and "(Milk)," -> nothing',
          pe._ingredient_whitespace_issues('DATEM, pH Adjuster, McIntosh Apple, Vitamin D (1,000 IU), '
                                           'Whey (Milk), Salt') == [])

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All run aa5b6042 checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
