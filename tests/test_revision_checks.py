"""Revision-comparison checks: ingredient drift (Check 7) and claims (Check 8).

    python3 tests/test_revision_checks.py

Exits non-zero on any failure. No test framework required.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402


def _snap(text, vn=None):
    return pe._build_label_snapshot(text, {}, vn or {})


def _run():
    failures = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            failures.append(name)

    # 7.0 — run 142db86d, Double Chocolate: the baseline (older reader) had
    # swept the brand paragraph into the last ingredient; the current read
    # stopped at "Guar Gum" correctly, and the drift check called the
    # improvement a dropped descriptor (WARNING).
    dch = ('Ingredients: Grass Fed Beef Protein Isolate, Cocoa Powder, Xanthan Gum, Guar Gum\n'
           'Amazing taste made with premium ingredients for real results whether you re building muscle')
    cur = pe._build_label_snapshot(dch, {}, {}, reader_source='vision')
    check('the statement stops before the brand paragraph',
          cur['ingredients'][-1] == 'guar gum')
    old = dict(cur, ingredients=cur['ingredients'][:-1] + [
        'guar gum amazing taste made with premium ingredients for real results whether you re building muscle'])
    old.pop('ingredients_reader')
    r = pe._check_ingredient_drift(cur, old)
    check('a baseline with no recorded reader -> could not evaluate, no WARNING',
          not r['issues'] and not r['compared'] and 'Could not evaluate' in r['notes'][0]
          and 'unrecorded (older) reader' in r['notes'][0])
    r = pe._check_ingredient_drift(cur, dict(old, ingredients_reader={'version': 1, 'source': 'vision'}))
    check('a baseline from an older reader version -> could not evaluate',
          not r['issues'] and 'reader v1 (vision)' in r['notes'][0])
    r = pe._check_ingredient_drift(cur, dict(old, ingredients_reader=dict(cur['ingredients_reader'])))
    check('the same reader on both sides still compares (and still sees a real change)',
          r['compared'] and r['issues'])

    # 7.1 — headline ingredient order change is flagged.
    r = pe._check_ingredient_drift(
        _snap('Ingredients: Cocoa, Natural Flavors, Whey Protein Isolate, Stevia'),
        _snap('Ingredients: Whey Protein Isolate, Cocoa, Natural Flavors, Stevia'))
    check('7.1 order change flagged',
          any('moved from #1 to #3' in i['message'] for i in r['issues']))

    # 7.2 — dropped "Organic" descriptor is CRITICAL.
    r = pe._check_ingredient_drift(
        _snap('Ingredients: Brown Rice Flour, Sugar, Salt'),
        _snap('Ingredients: Organic Brown Rice Flour, Sugar, Salt'))
    check('7.2 dropped Organic is critical',
          any(i['severity'] == 'critical' and 'organic' in i['message'].lower() for i in r['issues']))

    # 7.2b — a non-claim dropped descriptor is a warning, not critical.
    r = pe._check_ingredient_drift(
        _snap('Ingredients: Non-Fat Milk Powder, Sugar'),
        _snap('Ingredients: Non-Fat Dried Milk Powder, Sugar'))
    check('7.2b dropped Dried is warning',
          any(i['severity'] == 'warning' and 'dried' in i['message'].lower() for i in r['issues']))

    # 7.4 — identical ingredient statements across two SKUs flag both.
    a = {'filename': 'cinnamon.pdf', 'checks': {},
         'snapshot': _snap('Ingredients: Pea Protein, Rice Protein, Cocoa, Salt, Stevia')}
    b = {'filename': 'pumpkin.pdf', 'checks': {},
         'snapshot': _snap('Ingredients: Pea Protein, Rice Protein, Cocoa, Salt, Stevia')}
    pe._flag_duplicate_ingredients([a, b])
    check('7.4 duplicate flavors flagged (both files)',
          bool(a['checks'].get('ingredients', {}).get('issues'))
          and bool(b['checks'].get('ingredients', {}).get('issues')))

    # 8.2 — front callout quoting a dropped NFP column is CRITICAL.
    prior = _snap('Nutrition Facts Protein 20g Protein Plus 27g',
                  vn={'front_callout': {'protein_g': 27}, 'nfp': {'protein_g': 20}})
    cur = _snap('Nutrition Facts Protein 20g',
                vn={'front_callout': {'protein_g': 27}, 'nfp': {'protein_g': 20}})
    r = pe._check_claims(cur, prior)
    check('8.2 dropped-column callout is critical',
          any(i['severity'] == 'critical' and 'unsubstantiated' in i['message'] for i in r['issues']))

    # 8.3 — structure/function claims are listed for review (REVIEW severity, not judged).
    r = pe._check_claims(_snap('Supports Digestion and Supports Immunity.'))
    check('8.3 structure/function listed as REVIEW',
          any(i['severity'] == 'review' and 'labeling review' in i['message'].lower()
              for i in r['issues']))

    # 8.5 (Check H) — DSHEA disclaimer surfaces as REVIEW on a conventional food.
    r = pe._check_claims(_snap(
        'These statements have not been evaluated by the Food and Drug Administration.'))
    check('Check H DSHEA disclaimer → REVIEW',
          any(i['severity'] == 'review' and 'dshea' in i['message'].lower() for i in r['issues']))

    # 8.4 — comparative nutrition language is flagged.
    r = pe._check_claims(_snap('Now with 10g more protein than the leading brand.'))
    check('8.4 comparative language flagged',
          any('Comparative nutrition language' in i['message'] for i in r['issues']))

    # No prior version → drift comparison degrades to a note, not a crash.
    r = pe._check_ingredient_drift(_snap('Ingredients: Whey, Cocoa'), None)
    check('no-prior degrades to note', r['compared'] is False and bool(r['notes']))

    print()
    if failures:
        print('FAILURES:', *failures, sep='\n  - ')
        return 1
    print('All revision-comparison checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
