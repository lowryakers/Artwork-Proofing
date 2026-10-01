"""Three fixes from run c2beb887:

1. The master-URL host guard must derive its allowed host from READYDOC_URL,
   not refuse the correct host when READYDOC_URL is unset — covered by
   tests/test_master_sheet_config.py (the app.py side of this pack).
2. A protein-options strip ("19G as packaged / 23G replace water with milk /
   29G replace water with milk & add an egg") must never be read as the NFP
   panel's own protein value, and its chain must parse across newline
   separators, not just slashes.
3. A SUSPECT READ anywhere on a file must downgrade every other
   value-comparison CRITICAL on that file to SUSPECT.

    python3 tests/test_fixes_run_c2beb887.py

Exits non-zero on any failure. No test framework, no network, no vision API.
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

    # ── Item 2: protein-options strip ───────────────────────────────────────

    # The exact c2beb887 cinnamon_swirl layout — newline-separated, not slash.
    strip_text = (
        'PROTEIN OPTIONS\n'
        '19G  as packaged\n'
        '23G  Replace water with milk\n'
        '29G  Replace water with milk & add an egg\n'
    )

    chain_start = strip_text.index('19G')
    chain = pe._extract_protein_value_chain(strip_text[chain_start:])
    check('newline-separated chain extracts all three values (was: only the first)',
          [v for v, _ in chain] == [19, 23, 29])

    strip = pe._detect_protein_strip(strip_text)
    check('strip detected with as_packaged=19', strip is not None and strip['as_packaged'] == 19)
    check('strip as_prepared carries the two reconstituted values',
          strip is not None and sorted(strip['as_prepared']) == [23, 29])

    # The exact failure: NFP protein misread as 29 (the as-prepared "add an egg"
    # figure) when the panel's own Protein line, and the strip's own as-packaged
    # figure, both actually read 19.
    corrected, note = pe._correct_protein_from_strip(29, strip_text)
    check('contaminated NFP protein (29, an as-prepared strip value) corrected to 19',
          corrected == 19)
    check('a correction note is returned when something was actually wrong', bool(note))

    # A clean, uncontaminated read must be left alone.
    corrected2, note2 = pe._correct_protein_from_strip(19, strip_text)
    check('a correct NFP protein read (19) is left unchanged', corrected2 == 19)
    check('no note when nothing needed correcting', note2 is None)

    # No strip present at all -> never touches the value.
    corrected3, note3 = pe._correct_protein_from_strip(29, 'Protein 29g. No options strip here.')
    check('protein value left alone when no strip is present', corrected3 == 29 and note3 is None)

    # None in -> None out, no crash.
    corrected4, note4 = pe._correct_protein_from_strip(None, strip_text)
    check('None protein value passes through unchanged', corrected4 is None and note4 is None)

    # _check_protein_claims still fires correctly once the NFP protein is right:
    # the as-packaged value matches NFP (19==19, no issue), and the as-prepared
    # values carry a qualifier (skipped) — a clean file reports nothing.
    issues = pe._check_protein_claims(strip_text, 19)
    check('once the NFP protein is correct (19), the clean strip raises nothing', issues == [])

    # And if the as-packaged figure itself truly disagrees with the NFP (a real
    # label error, not a misread), that must still fire CRITICAL.
    issues = pe._check_protein_claims(strip_text, 20)
    check('a genuine as-packaged/NFP disagreement still fires CRITICAL',
          any(i['severity'] == 'critical' and '19G' in i['message'] for i in issues))

    # The original slash-separated format (PR #20) must still work after the
    # separator-regex change.
    slash_text = '20G Just Add Water / 24G Replace water with milk / 30G Replace water with milk & add an egg'
    chain_start = slash_text.index('20G')
    chain = pe._extract_protein_value_chain(slash_text[chain_start:])
    check('slash-separated chain (PR #20 format) still parses all three values',
          [v for v, _ in chain] == [20, 24, 30])

    # ── Item 3: a SUSPECT READ must downgrade other CRITICALs on the same file ──

    # Reproduce the exact c2beb887 shape: netwt flags a SUSPECT (misread serving
    # size), nfp flags CRITICALs built from a different, unrelated-looking read.
    checks = {
        'netwt': {'status': 'SUSPECT', 'issues': [
            {'severity': 'suspect', 'message': (
                'SUSPECT READ — serving 3/4 Cup = 198g implies 264 g/cup vs the ~130 g/cup '
                'ProDough norm (~2x off). A clean multiple like this usually means the cup '
                'fraction was misread.')},
        ]},
        'nfp': {'status': 'FAIL', 'issues': [
            {'severity': 'critical', 'message': 'Protein mismatch: front call-out shows 19.0g but NFP shows 29.0g.'},
            {'severity': 'critical', 'message': 'Protein value 19G on the label does not match the NFP protein of 29g.'},
        ]},
        'gtin': {'status': 'PASS', 'issues': [
            {'severity': 'critical', 'message': 'GTIN not found anywhere on the artwork.'},
        ]},
    }
    pe._downgrade_criticals_on_suspect_file(checks)
    check('nfp CRITICALs are downgraded to SUSPECT when netwt flagged a suspect read',
          all(i['severity'] == 'suspect' for i in checks['nfp']['issues']))
    check('downgraded message explains why, and keeps the original finding',
          all('downgraded from CRITICAL' in i['message'] and 'serving size' in i['message']
              and 'Protein' in i['message'] for i in checks['nfp']['issues']))
    check('the original netwt SUSPECT issue is untouched',
          checks['netwt']['issues'][0]['severity'] == 'suspect')
    check('a non-value-comparison check (gtin) is NOT downgraded — different failure mode',
          checks['gtin']['issues'][0]['severity'] == 'critical')

    # No SUSPECT anywhere on the file -> nothing is touched.
    checks2 = {
        'nfp': {'status': 'FAIL', 'issues': [
            {'severity': 'critical', 'message': 'Calorie mismatch: front 120 cal but NFP 130 cal.'},
        ]},
    }
    pe._downgrade_criticals_on_suspect_file(checks2)
    check('with no SUSPECT anywhere, a real CRITICAL is left alone',
          checks2['nfp']['issues'][0]['severity'] == 'critical')

    # End-to-end through _check_net_weight's own SUSPECT path (the literal
    # c2beb887 scenario: 3/4 cup misread as 198g against a 130g/cup norm).
    r = pe._check_net_weight(
        {'serving_size_g': 198, 'serving_size_cups': 0.75, 'serving_size_desc': '3/4 Cup',
         'servings_per_container': 2.29, 'declared_net_weight_g': 454},
        fill_weight_g=454)
    check('the real net-weight SUSPECT READ path still fires SUSPECT, not CRITICAL',
          r['status'] == 'SUSPECT' and any(i['severity'] == 'suspect' for i in r['issues']))

    # And _recount_result applies the same downgrade when issues are appended
    # post-hoc (cross-SKU collision runs after per-file severity is set).
    res = {'checks': {
        'netwt': {'status': 'SUSPECT', 'issues': [{'severity': 'suspect', 'message': 'SUSPECT READ — serving size.'}]},
        'nfp': {'status': 'FAIL', 'issues': [{'severity': 'critical', 'message': 'Cross-SKU value collision.'}]},
    }}
    pe._recount_result(res)
    check('_recount_result downgrades a post-hoc CRITICAL on a suspect file too',
          res['checks']['nfp']['issues'][0]['severity'] == 'suspect')
    check('_recount_result reports severity=suspect (never critical) for the file',
          res['severity'] == 'suspect' and res['critical_count'] == 0)

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All run-c2beb887 fixes pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
