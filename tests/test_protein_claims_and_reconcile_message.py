"""Protein-options strip on pancake bottle BACK panels (Check I), and naming
the cause when serving size and servings-per-container diverge since the last
proof (Check C message improvement).

    python3 tests/test_protein_claims_and_reconcile_message.py

Exits non-zero on any failure. No test framework required.

Ground truth: pancake BACK panels carry "PROTEIN OPTIONS 20G Just Add Water /
24G Replace water with milk / 30G Replace water with milk & add an egg". The
front badge (19G, matches the NFP) is untouched and out of scope here. Of the
four pancake files, three (pumpkin, chocolate, cinnamon_swirl) declare an
NFP protein of 19g against the strip's unqualified 20G — a real CRITICAL.
buttermilk's NFP is 20g, matching the strip exactly — no finding.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402

_STRIP = ('PROTEIN OPTIONS 20G Just Add Water / 24G Replace water with milk / '
         '30G Replace water with milk & add an egg')


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    # ═══════════════════════════════════════════════════════════════════════
    # Check I — whole-label protein claims
    # ═══════════════════════════════════════════════════════════════════════

    # ── The three real findings (NFP 19g vs strip 20G) ─────────────────────────
    for fname in ('PD_pancake_pumpkin.pdf', 'PD_pancake_chocolate.pdf',
                 'PD_pancake_cinnamon_swirl.pdf'):
        issues = pe._check_protein_claims(_STRIP, nfp_protein_g=19)
        check(f'{fname}: strip 20G vs NFP 19g -> exactly one CRITICAL',
              len(issues) == 1 and issues[0]['severity'] == 'critical')
        check(f'{fname}: the finding names both the label value and the NFP value',
              '20' in issues[0]['message'] and '19' in issues[0]['message'])
        check(f'{fname}: the finding names WHERE on the label it appears (the segment text)',
              'Just Add Water' in issues[0]['message'])

    # ── buttermilk: strip 20G matches NFP 20g exactly -> no finding ────────────
    issues_bm = pe._check_protein_claims(_STRIP, nfp_protein_g=20)
    check('buttermilk: strip 20G vs NFP 20g -> no finding', issues_bm == [])

    # ── The as-prepared values (24G, 30G) are ignored entirely, always ─────────
    # Even against an NFP that would make them "wrong" if compared.
    issues_extreme = pe._check_protein_claims(_STRIP, nfp_protein_g=999)
    check('24G "replace water with milk" is never compared, regardless of NFP',
          not any('24' in i['message'] for i in issues_extreme))
    check('30G "replace water with milk & add an egg" is never compared, regardless of NFP',
          not any('30' in i['message'] for i in issues_extreme))
    check('only the unqualified 20G is ever a finding for this strip',
          all('20' in i['message'] for i in issues_extreme))

    # ── The front callout / NFP itself must never be a false positive ─────────
    check('a plain NFP "Protein 25g" row (no qualifier) compared to itself -> no finding',
          pe._check_protein_claims('Nutrition Facts Protein 25g Total Fat 2g', nfp_protein_g=25) == [])
    check('a front "PROTEIN 19G PER SERVING" badge matching the NFP -> no finding',
          pe._check_protein_claims('PROTEIN 19G PER SERVING', nfp_protein_g=19) == [])
    check('an ingredient-list mention of "Whey Protein Isolate" near an unrelated '
         'number is never mistaken for a protein claim',
          pe._check_protein_claims('Ingredients: Whey Protein Isolate, Net Wt 454g, Sea Salt',
                                   nfp_protein_g=25) == [])

    # ── No NFP protein reference at all -> degrades to no findings, never crashes ──
    check('no NFP protein reference -> no findings (nothing to compare against)',
          pe._check_protein_claims(_STRIP, nfp_protein_g=None) == [])

    # ── Individual qualifier phrases, tested directly ──────────────────────────
    check('"as prepared" is recognized as an as-prepared qualifier',
          pe._check_protein_claims('PROTEIN 40G as prepared', nfp_protein_g=20) == [])
    check('"just add water" (as-packaged) is NOT an as-prepared qualifier — still compared',
          len(pe._check_protein_claims('PROTEIN 40G just add water', nfp_protein_g=20)) == 1)
    check('"per serving" (as-packaged) is NOT an as-prepared qualifier — still compared',
          len(pe._check_protein_claims('PROTEIN 40G per serving', nfp_protein_g=20)) == 1)
    check('a bare value with no qualifier text at all is still compared',
          len(pe._check_protein_claims('PROTEIN 40G', nfp_protein_g=20)) == 1)

    # ═══════════════════════════════════════════════════════════════════════
    # Check C — name the cause when serving size / servings diverge unevenly
    # ═══════════════════════════════════════════════════════════════════════

    # Force the CRITICAL "does not reconcile" branch (fill differs from
    # declared by more than the 0.5g exact-match tolerance, so this doesn't
    # land in the separate SUSPECT-read branch).
    panel = {'serving_size_g': 88, 'servings_per_container': 4.5, 'declared_net_weight_g': 454}
    prior = {'serving_size_g': 98, 'servings_per_container': 4.5}
    res = pe._check_net_weight(panel, fill_weight_g=520, fname='PD_pancake_pumpkin.pdf',
                               serving_vision_backed=True, prior_snapshot=prior)
    check('serving size changed, servings unchanged -> CRITICAL status',
          res['status'] == 'CRITICAL')
    _crit_msgs = [i['message'] for i in res['issues'] if i['severity'] == 'critical']
    check('the message states the exact required diagnosis',
          any('Serving size changed 98g → 88g since the last proof, but servings per '
             'container is unchanged at 4.5. One of the two did not follow the other.' in m
             for m in _crit_msgs))

    # Reverse: servings-per-container changed, serving size unchanged.
    panel_r = {'serving_size_g': 98, 'servings_per_container': 3, 'declared_net_weight_g': 454}
    prior_r = {'serving_size_g': 98, 'servings_per_container': 4.5}
    res_r = pe._check_net_weight(panel_r, fill_weight_g=520, fname='x.pdf',
                                 serving_vision_backed=True, prior_snapshot=prior_r)
    check('reverse case (servings changed, serving size unchanged) names the correct field',
          any('Servings per container changed 4.5 → 3 since the last proof, but serving size '
             'is unchanged at 98g' in i['message']
             for i in res_r['issues'] if i['severity'] == 'critical'))

    # Both changed — the specific diagnosis must not fire (it would misattribute
    # the cause); falls back to the generic / ratio-based message.
    panel_both = {'serving_size_g': 60, 'servings_per_container': 3, 'declared_net_weight_g': 454}
    prior_both = {'serving_size_g': 98, 'servings_per_container': 4.5}
    res_both = pe._check_net_weight(panel_both, fill_weight_g=520, fname='x.pdf',
                                    serving_vision_backed=True, prior_snapshot=prior_both)
    check('when BOTH serving size and servings changed, the specific diagnosis does not fire',
          not any('did not follow the other' in i['message']
                 for i in res_both['issues'] if i['severity'] == 'critical'))

    # No prior snapshot at all — must degrade gracefully, never crash, and the
    # base "does not reconcile" message still fires.
    res_none = pe._check_net_weight(panel, fill_weight_g=520, fname='x.pdf',
                                    serving_vision_backed=True, prior_snapshot=None)
    check('no prior snapshot at all -> degrades gracefully, still reports CRITICAL',
          res_none['status'] == 'CRITICAL'
          and any('does not reconcile' in i['message']
                 for i in res_none['issues'] if i['severity'] == 'critical'))

    # A real short-measure (overstated) CRITICAL is completely unaffected by
    # any of this — prior_snapshot is simply not consulted for Check A.
    panel_short = {'declared_net_weight_g': 500, 'serving_size_g': 500, 'servings_per_container': 1}
    res_short = pe._check_net_weight(panel_short, fill_weight_g=400, fname='x.pdf',
                                     serving_vision_backed=True, prior_snapshot=prior)
    check('an unrelated short-measure CRITICAL (Check A) is unaffected by prior_snapshot',
          res_short['status'] == 'CRITICAL'
          and any('OVERSTATED' in i['message'] for i in res_short['issues']))

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All protein-claims and reconcile-message checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
