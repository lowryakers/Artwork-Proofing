"""Five fixes from run 8109022a: the crash that zeroed out Apple Pie's checks,
8 false FALCPA criticals from prep-copy "milk", 37 prep blocks misreported as
UNKNOWN, false CRITICALs on ordinary overfill, and a vision-side oz->gram
conversion that silently drifted the Net Weight report column.

    python3 tests/test_fixes_run_8109022a.py

Exits non-zero on any failure. No test framework required.

Together these remove 13 of the 14 non-claims false findings in that run and
surface the one real defect the crash was hiding (Apple Pie: front declares
"0G Added Sugar", its own NFP reads "Includes <1g Added Sugars").
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

    # ═══════════════════════════════════════════════════════════════════════
    # FIX 5 — the crash: bare int() on a "<1"/"<5" panel amount took down the
    # whole file, not just one check. Priority 1: one file with zero coverage
    # is worse than any false positive.
    # ═══════════════════════════════════════════════════════════════════════
    check('_num_or_none("<1") -> 1.0', pe._num_or_none('<1') == 1.0)
    check('_num_or_none("< 5 g") -> 5.0', pe._num_or_none('< 5 g') == 5.0)
    check('_num_or_none(None) -> None', pe._num_or_none(None) is None)
    check('_num_or_none("n/a") -> None', pe._num_or_none('n/a') is None)
    check('_num_or_none(3) -> 3.0 (plain numbers pass through)', pe._num_or_none(3) == 3.0)

    check('_is_below("<1") is True', pe._is_below('<1'))
    check('_is_below(0) is False (an int is never a threshold form)', not pe._is_below(0))
    check('_is_below("1") is False (no "<")', not pe._is_below('1'))

    # The exact Apple Pie reproduction: vision NFP added_sugar_g == '<1' must
    # not crash _check_nfp, and the front/NFP contradiction it was hiding
    # (0G front vs <1g NFP) must now surface.
    vn = {'front_callout': {'calories': 120, 'protein_g': 25, 'added_sugar_g': 0},
         'nfp': {'calories': 120, 'protein_g': 25, 'added_sugar_g': '<1'}}
    out = pe._check_nfp('Nutrition Facts Includes <1g Added Sugars 1%',
                        front_text='120 Calories 25G Protein 0G Added Sugar', vision_nutrition=vn)
    check('"<1" added-sugar value does not crash _check_nfp',
          isinstance(out, dict))  # would have raised before reaching here
    check('the front-0g-vs-NFP-<1g contradiction now surfaces as a real finding',
          any('added sugar mismatch' in i['message'].lower() for i in out['issues']))
    check('the surfaced finding correctly quotes the NFP value',
          any('<1' in i['message'] for i in out['issues']))

    # Matching 0g/0g must stay silent — this is not a blanket "always flag
    # added sugar" rule, only a genuine contradiction.
    vn_ok = {'front_callout': {'calories': 120, 'protein_g': 25, 'added_sugar_g': 0},
            'nfp': {'calories': 120, 'protein_g': 25, 'added_sugar_g': 0}}
    out_ok = pe._check_nfp('x', front_text='y', vision_nutrition=vn_ok)
    check('matching 0g/0g added sugar stays silent',
          not any('added sugar mismatch' in i['message'].lower() for i in out_ok['issues']))

    # ═══════════════════════════════════════════════════════════════════════
    # FIX 1 — FALCPA critical fired on all 8 beef/plant SKUs: the prep-copy
    # strip anchored per-line, so "DIRECTIONS" on its own line left "Add water
    # or milk" on the STEP 1 line exposed, reading as an undeclared milk
    # ingredient on products that correctly carry no Contains: statement.
    # ═══════════════════════════════════════════════════════════════════════
    beef_txt = ('ingredients: grass fed beef protein, bovine collagen peptides, natural '
               'flavors, contains less than 2 percent of sea salt, guar gum\n'
               'directions\nstep 1 add water or milk to dotted line (7oz).\n'
               'step 2 shake well. do not top off.\nstep 3 enjoy your protein on the go!\n')
    out_beef = pe._check_fda(beef_txt, 'PD_beef_bottle_frosted_vanilla.pdf')
    check('prep-copy "water or milk" produces no critical Contains: finding on a beef bottle',
          not any(i['severity'] == 'critical' for i in out_beef['issues']))

    plant_txt = ('ingredients: pea protein, rice protein, mct oil powder, digestive enzyme '
                'blend (protease 400 hut, amylase 300 du, lactase 20 alu)\n'
                'directions\nstep 1 shake well.\nstep 2 enjoy chilled.\n')
    out_plant = pe._check_fda(plant_txt, 'PD_plant_bottle_cafe_mocha.pdf')
    check('"lactase" enzyme is not read as a milk ingredient',
          not any(i['severity'] == 'critical' for i in out_plant['issues']))

    # A real undeclared milk ingredient — on a product NOT filename-identified
    # as a whey product (so the OCR-fragmentation suppression in _check_fda
    # doesn't apply) — must still be caught, now as REVIEW with its own
    # evidence rather than a blind critical.
    real_milk_txt = (
        'ingredients: pea protein isolate, non-fat milk powder, natural flavors,\n'
        'stevia leaf extract, sunflower lecithin, sea salt, guar gum, xanthan gum,\n'
        'digestive enzyme blend (protease, amylase, lactase)\n'
        'supplement facts serving size one bottle servings per container one\n'
        'calories one hundred thirty total fat two grams saturated fat zero grams\n'
        'cholesterol zero milligrams sodium two hundred milligrams total\n'
        'carbohydrate four grams dietary fiber zero grams total sugars two grams\n'
        'added sugars zero grams protein twenty five grams net wt 1.23 oz 35g\n'
        'directions\nstep one shake well before opening.\nstep two enjoy chilled.\n'
        'manufactured for prodough nutrition company salt lake city utah\n')
    out_real = pe._check_fda(real_milk_txt, 'PD_plant_bottle_vanilla.pdf')
    check('a genuinely undeclared milk ingredient still flags',
          any('milk' in i['message'].lower() for i in out_real['issues']))
    check('the generic undeclared-allergen finding is REVIEW, not CRITICAL '
         '(named-product checks with a filename signal stay CRITICAL — this one has none)',
          any(i['severity'] == 'review' and 'milk' in i['message'].lower() for i in out_real['issues'])
          and not any(i['severity'] == 'critical' and 'milk' in i['message'].lower()
                     for i in out_real['issues']))
    check('the REVIEW finding names its own evidence (the matched text)',
          any('"' in i['message'] for i in out_real['issues'] if 'milk' in i['message'].lower()))

    # ═══════════════════════════════════════════════════════════════════════
    # FIX 3 — 37 of 38 prep blocks returned UNKNOWN: a single-serve bottle has
    # no batch/per-serving distinction, so its directions carry no mix
    # quantity and no yield. That is a complete answer, not a failed read.
    # ═══════════════════════════════════════════════════════════════════════
    single_serve_txt = ('directions\nstep 1 add water or milk to dotted line (7oz).\n'
                        'step 2 shake well. do not top off.\nstep 3 enjoy your protein on the go!')
    out_ss = pe._check_prep_block(single_serve_txt, {'servings_per_container': 1})
    check('single-serve bottle classifies as SINGLE-SERVE, not UNKNOWN',
          out_ss['classification'] == 'single-serve')
    check('single-serve status is OK (verified — a complete answer)', out_ss['status'] == 'OK')

    out_multi_unreadable = pe._check_prep_block('directions\nsome unreadable blur\n',
                                                {'servings_per_container': 4.5})
    check('a multi-serving pack with genuinely unreadable prep is still UNKNOWN',
          out_multi_unreadable['classification'] == 'UNKNOWN')

    check('SINGLE-SERVE never counts as not-fully-verified',
          'SINGLE-SERVE' not in pe._NOT_VERIFIED_STATUSES)

    # ═══════════════════════════════════════════════════════════════════════
    # FIX 2 — net weight UNDERSTATED (i.e. the package physically overfills)
    # is the safe regulatory direction and must not be CRITICAL like an
    # OVERSTATED (short-measure) declaration.
    # ═══════════════════════════════════════════════════════════════════════
    p_under = {'declared_net_weight_g': 36, 'serving_size_g': 36, 'servings_per_container': 1}
    out_under = pe._check_net_weight(p_under, fill_weight_g=37.44, fname='x.pdf', serving_vision_backed=True)
    check('overfill under the review threshold (4.0%) is silent — PASS',
          out_under['status'] == 'PASS')

    p_large = {'declared_net_weight_g': 35, 'serving_size_g': 35, 'servings_per_container': 1}
    out_large = pe._check_net_weight(p_large, fill_weight_g=37.63, fname='x.pdf', serving_vision_backed=True)
    check('a large overfill (7.5%, the one real out-of-family case) is REVIEW, never CRITICAL',
          out_large['status'] == 'REVIEW' and not any(i['severity'] == 'critical' for i in out_large['issues']))

    p_short = {'declared_net_weight_g': 40, 'serving_size_g': 40, 'servings_per_container': 1}
    out_short = pe._check_net_weight(p_short, fill_weight_g=37.0, fname='x.pdf', serving_vision_backed=True)
    check('an OVERSTATED (short-measure) declaration is still CRITICAL',
          out_short['status'] == 'CRITICAL'
          and any('overstated' in i['message'].lower() for i in out_short['issues']))

    # ═══════════════════════════════════════════════════════════════════════
    # FIX 4 — the vision prompt told the model to convert oz/lb to grams even
    # when a gram figure was already printed, silently drifting the reported
    # net weight (~0.1g, inside every tolerance, but wrong and misleading in
    # the report). The Tesseract/text path already preferred grams; it also
    # gets a free self-consistency check when both figures are on one line.
    # ═══════════════════════════════════════════════════════════════════════
    out_txt = pe._parse_panel_from_text('net wt. 1.23 oz (35g)')
    check('a printed gram figure is read verbatim, never a conversion of the oz figure',
          out_txt['net_weight_g'] == 35.0)
    check('a consistent oz/g line has no mismatch flag', not out_txt.get('net_weight_oz_mismatch'))

    out_bad = pe._parse_panel_from_text('net wt. 1.20 oz (35g)')
    check('a self-contradicting net-weight line is caught (1.20 oz = 34.0g, not 35)',
          out_bad.get('net_weight_oz_mismatch') == (1.2, 34.0))

    panel_bad = pe._merge_panel(out_bad, {})
    panel_bad['servings_per_container'] = 1
    panel_bad['serving_size_g'] = 35
    res_bad = pe._check_net_weight(panel_bad, fill_weight_g=None, fname='x.pdf')
    check('the self-contradicting line surfaces as a WARNING naming both figures',
          any(i['severity'] == 'warning' and '1.2' in i['message'] and '34' in i['message']
             for i in res_bad['issues']))

    # The vision JSON parser accepts and preserves the new conversion flag.
    parsed = pe._parse_vision_json(
        '{"panel": {"net_weight_g": 35, "net_weight_from_conversion": false}}')
    check('vision panel parser reads net_weight_from_conversion',
          parsed['panel'].get('net_weight_from_conversion') is False)
    parsed2 = pe._parse_vision_json(
        '{"panel": {"net_weight_g": 454, "net_weight_from_conversion": true}}')
    check('net_weight_from_conversion true is preserved (converted from oz/lb, no gram figure printed)',
          parsed2['panel'].get('net_weight_from_conversion') is True)

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All run-8109022a fixes pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
