"""Front call-out values found only by the loose window (whey bottles, PR #30).

    python3 tests/test_front_callout_not_evaluated.py

A number in the 120 characters before "calories" / "protein" is not a call-out
bound to the word. On the 30 whey bottles it read the dieline's "95.00 mm
Layflat" as a 95-calorie call-out (true 120) and filed a false CRITICAL on 22
correct labels. Such a value can only mark the comparison NOT EVALUATED.

Exits non-zero on any failure. No test framework, no network, no vision API.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402

NFP = 'Nutrition Facts Serving size 1 bottle (34g) Calories 120 Total Fat 0.5g Protein 25g'


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    def crit(out):
        return [i for i in out['issues'] if i['severity'] == 'critical']

    def not_eval(out, what):
        return [i for i in out['issues'] if i['severity'] == 'review'
                and i['message'].startswith(f'NOT EVALUATED — front {what} call-out')]

    # The whey case: a dieline dimension sits inside the window before
    # "Calories Per Serving"; no call-out bound to the word was read.
    out = pe._check_nfp(NFP, front_text='95.00 mm Layflat\nGrass-Fed Calories Per Serving')
    check('a loose-window front calorie value raises no CRITICAL', not crit(out))
    check('...it reports the calorie comparison NOT EVALUATED for a person to check',
          len(not_eval(out, 'calorie')) == 1 and '95' in not_eval(out, 'calorie')[0]['message'])
    check('...and records the set-aside value', out['front_callout']['unconfirmed'].get('calories') == [95])
    check('...and does not treat it as a front call-out value', out['front_callout']['calories'] == [])

    out = pe._check_nfp(NFP, front_text='Desc 9 mm protein blend')
    check('a loose-window front protein value raises no CRITICAL', not crit(out))
    check('...it reports the protein comparison NOT EVALUATED', len(not_eval(out, 'protein')) == 1)

    # A loose value that simply repeats the NFP is not worth a person's time.
    out = pe._check_nfp(NFP, front_text='Grass-Fed 120 Calories Per Serving whey')
    check('a call-out bound to the word still counts as the front value',
          out['front_callout']['calories'] == [120])
    out = pe._check_nfp(NFP, front_text='120 whey Calories Per Serving')
    check('a loose value equal to the NFP value raises nothing',
          not crit(out) and not not_eval(out, 'calorie'))

    # A call-out bound to the word still decides the comparison.
    out = pe._check_nfp(NFP, front_text='110 Calories Per Serving')
    check('a bound front call-out that contradicts the NFP is still a CRITICAL',
          any('Calorie mismatch: front call-out shows 110' in i['message'] for i in crit(out)))
    check('...with no NOT EVALUATED alongside it', not not_eval(out, 'calorie'))

    # Side-by-side rings interleave in OCR. On the beef bottles the "100%
    # Grass-Fed" ring lost its superscript % and read "100 / Calories Per /
    # Grass-F" — the neighbouring ring's caption — a false 100-cal CRITICAL on
    # Cinnamon Sugar and Frosted Vanilla. A number whose caption lines carry
    # another ring's caption is not bound to calories.
    beef_nfp = NFP.replace('120', '130')
    out = pe._check_nfp(beef_nfp, front_text='0%\n130\nSTEP 3\nProtein 25g\n100\nCalories Per\nGrass-F\nServing Beef')
    check('a ring number whose caption lines carry a competing caption raises no CRITICAL',
          not crit(out))
    check('...and is not taken as the front call-out value', out['front_callout']['calories'] == [])
    out = pe._check_nfp(beef_nfp, front_text='100\n\nCalories Per\n\nGrass-F\n\nEnjoy your protein')
    check('...also when OCR puts blank lines between the ring lines (as the pipeline text does)',
          not crit(out) and out['front_callout']['calories'] == [])
    out = pe._check_nfp(beef_nfp, front_text='100\nCalories\nPer Serving')
    check('a ring number with only its calorie caption is still bound, and still a CRITICAL',
          any('front call-out shows 100' in i['message'] for i in crit(out)))

    # A vision read of the call-out overrides the text reads entirely.
    vn = {'front_callout': {'calories': 120, 'protein_g': 25}, 'nfp': {'calories': 120, 'protein_g': 25}}
    out = pe._check_nfp(NFP, front_text='95.00 mm Layflat\nGrass-Fed Calories Per Serving',
                        vision_nutrition=vn)
    check('with a vision call-out read, the loose value is ignored',
          not crit(out) and not not_eval(out, 'calorie'))
    vn_bad = {'front_callout': {'calories': 110}, 'nfp': {'calories': 120}}
    out = pe._check_nfp(NFP, front_text='', vision_nutrition=vn_bad)
    check('a vision call-out that contradicts the NFP is still a CRITICAL',
          any('front call-out shows 110' in i['message'] for i in crit(out)))

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All front call-out NOT EVALUATED checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
