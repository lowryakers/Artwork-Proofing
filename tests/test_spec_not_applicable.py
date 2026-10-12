"""A master-list spec of "NA" means the check does not apply to the SKU.

    python3 tests/test_spec_not_applicable.py

Run 142db86d: every beef shrink sleeve carries Eye mark = NA (39 of 156 SKUs;
78 more are blank). The eyemark check read "NA" as a colour and filed a
CRITICAL on all four — "spec requires NA ... change the eyemark fill to pure NA
(#000000)". A sentinel resolves to NOT_APPLICABLE: no finding, listed with the
disabled checks, never blocks CLEAN. An unparseable value is "could not
evaluate", and no message builds a sentence around it.

Exits non-zero on any failure. No test framework, no network, no vision API.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402
import app as A  # noqa: E402

HEAD = 'SKU,Product name,GTIN,Fill weight (g),Eye mark,Wind direction,Material,Trim length (mm)\n'


def _row(eye, wind='', material='PET', trim='164', fill='35'):
    return A._parse_master_csv(HEAD + f'BEF-X,Thing,850000000001,{fill},{eye},{wind},{material},{trim}\n')[0]


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    for raw in ('NA', 'N/A', 'n/a', 'none', 'None', 'Not applicable', '-', ''):
        r = _row(raw)
        check(f'eyemark spec {raw!r} -> not applicable, never a colour',
              'eye_mark_color' in r['_not_applicable'] and r['eye_mark_color'] == '')
    r = _row('Black')
    check('eyemark spec "Black" is a value', r['eye_mark_color'] == 'Black'
          and 'eye_mark_color' not in r['_not_applicable'])

    r = _row('NA', wind='NA', material='N/A', trim='NA')
    check('every spec column honours the sentinel (wind, material, trim)',
          {'wind_direction', 'material', 'trim_length_mm'} <= set(r['_not_applicable'])
          and r['trim_length_mm'] is None and 'trim_length_mm' not in r['_unparseable'])
    r = _row('NA', fill='NA')
    check('fill weight "NA" is a bad value (could not evaluate), not "does not apply"',
          r['_unparseable'].get('fill_weight_g') == 'NA' and 'fill_weight_g' not in r['_not_applicable'])
    r = _row('NA', trim='TBD')
    check('"TBD" is a value nobody has supplied yet -> unparseable, not dropped',
          r['_unparseable'].get('trim_length_mm') == 'TBD')

    # Engine: the NA check becomes NOT_APPLICABLE, which is neither run nor skipped.
    r = _row('NA', wind='NA')
    checks = {'eyemark': {'issues': [{'severity': 'critical', 'message':
                                      'Wrong eyemark color — detected WHITE but spec requires NA.'}]},
              'wind': {'issues': [], 'notes': []},
              'gtin': {'status': 'OK', 'issues': []}}
    pe._apply_not_applicable_spec(checks, r)
    check('eyemark NA -> NOT_APPLICABLE with no finding',
          checks['eyemark']['status'] == 'NOT_APPLICABLE' and checks['eyemark']['issues'] == [])
    check('wind NA -> NOT_APPLICABLE', checks['wind']['status'] == 'NOT_APPLICABLE')
    run, skipped = pe._completeness(checks)
    check('a NOT_APPLICABLE check is neither run nor skipped (does not block CLEAN)',
          run == ['gtin'] and skipped == [])
    check('...and is listed as not applicable', pe._not_applicable_checks(checks) == ['eyemark', 'wind'])
    check('a file whose only non-run checks are N/A is CLEAN',
          pe._file_severity([], [], [], [], [], skipped) == 'clean')

    # An unparseable eyemark spec is "could not evaluate", never echoed as a colour.
    out = pe._check_eyemark(None, True, 'x_stick.pdf', 'Purple', vision_eyemark={'color': 'white'})
    msg = ' '.join(i['message'] for i in out['issues'])
    check('eyemark spec "Purple" -> NOT_RUN, one review, no CRITICAL',
          out.get('status') == 'NOT_RUN' and [i['severity'] for i in out['issues']] == ['review'])
    check('...and the message says the value is unreadable instead of describing it',
          '"Purple" is not black or white' in msg and 'requires' not in msg and 'pure' not in msg)
    out = pe._check_eyemark(None, True, 'x_stick.pdf', 'black', vision_eyemark={'color': 'white'})
    check('a valid spec still catches a wrong eyemark as CRITICAL',
          [i['severity'] for i in out['issues']] == ['critical'])

    # The Summary sheet lists N/A checks in the disabled column.
    job = {'id': 't', 'created': '2026-10-12T00:00:00', 'dismissals': {}, 'summary': {},
           'results': [{'filename': 'f.pdf', 'severity': 'clean', 'checks_run': ['gtin'],
                        'checks_skipped': [], 'checks_disabled': ['spot_color'],
                        'checks_not_applicable': ['eyemark'], 'checks': {}}]}
    try:
        import openpyxl
        import io
        wb = openpyxl.load_workbook(io.BytesIO(A._generate_report(job).getvalue()))
        ws = wb.worksheets[0]
        cells = [str(c.value) for row in ws.iter_rows() for c in row if c.value]
        check('Summary sheet shows "eyemark (not applicable to this SKU)"',
              any('eyemark (not applicable to this SKU)' in c for c in cells))
    except ImportError:
        print('[skip] openpyxl not installed')

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All spec not-applicable checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
