"""Reader provenance and the silent downgrade (run 9ffb350f).

The causal chain this pack breaks: master list 502 -> fill_weight None and no
approved panel -> _should_run_vision() False -> the geometric panel reader
never ran -> _parse_panel_from_text scraped 65g / 43g off the wrong column of
the layflat -> every check consumed them -> two files filed CLEAN with nothing
read from their panels.

    python3 tests/test_reader_provenance.py

Exits non-zero on any failure. No test framework, no network, no vision API.
"""
import io
import json
import os
import sys
import tempfile
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402
import app as A  # noqa: E402


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    # ── 1c. The scoped serving-size regex ───────────────────────────────────
    layflat = ('Nutrition Facts\nServing size 3/4 Cup (83g)\nAbout 5 servings per container\n'
               'PROTEIN OPTIONS 19G as packaged 23G with milk\n65g something in another column 43g')
    tp = pe._parse_panel_from_text(layflat)
    check('scoped regex reads 3/4 Cup (83g) -> 83', tp.get('serving_size_g') == 83.0)
    check('scoped regex reads the cup fraction -> 0.75', tp.get('serving_size_cups') == 0.75)
    check('65 (not inside a parenthesis after a measure word) can never match',
          pe._parse_panel_from_text('serving size 65g\n43g') == {})
    check('a unit-declared serving ("4 Cupcakes (63g)") reads grams but no cup value',
          pe._parse_panel_from_text('serving size 4 cupcakes (63g)') == {'serving_size_g': 63.0})
    check('mixed-number cups parse ("1 1/2 cups (120g)")',
          pe._parse_panel_from_text('serving size 1 1/2 cups (120g)').get('serving_size_cups') == 1.5)

    # ── 1a. The gate no longer depends on the master list ───────────────────
    _orig = pe._ocr_needs_vision
    pe._ocr_needs_vision = lambda *a, **k: False
    try:
        unscoped = 'nutrition facts calories 130 protein 25 contains: milk serving size 65g'
        check('_text_parse_is_panel_scoped is False with no anchored serving line',
              pe._text_parse_is_panel_scoped(unscoped) is False)
        check('gate escalates on an unscoped page even with NO fill weight and NO approved panel',
              pe._should_run_vision(unscoped, '', 'PD_pancake.pdf') is True)
        scoped = unscoped + '\nserving size 3/4 cup (88g)\nabout 5 servings per container'
        check('_text_parse_is_panel_scoped is True with exactly one anchored line each',
              pe._text_parse_is_panel_scoped(scoped) is True)
        two_panels = scoped + '\nserving size 1/2 cup (65g)\nabout 7 servings per container'
        check('two competing serving lines (a superseded panel) -> not scoped -> escalate',
              pe._text_parse_is_panel_scoped(two_panels) is False
              and pe._should_run_vision(two_panels, '', 'x.pdf') is True)
    finally:
        pe._ocr_needs_vision = _orig
    import inspect
    check('_should_run_vision no longer takes fill_weight / has_approved_panel',
          'fill_weight' not in inspect.signature(pe._should_run_vision).parameters
          and 'has_approved_panel' not in inspect.signature(pe._should_run_vision).parameters)

    # ── 1b / 2. page_regex never supplies the three panel fields; provenance ──
    merged = pe._merge_panel(tp, {})
    check('with no vision read, serving fields are ABSENT (not page_regex values)',
          merged.get('serving_size_g') is None and merged.get('servings_per_container') is None)
    check('the page-regex reads are quarantined under _page_regex for the provenance sheet',
          merged['_page_regex'] == {'serving_size_g': 83.0, 'serving_size_cups': 0.75,
                                    'servings_per_container': 5.0})
    merged2 = pe._merge_panel({'net_weight_g': 454.0, 'unit_count': 21.0}, {'serving_size_g': 88.0})
    check('front-panel fields keep page_regex provenance; vision fields get vision_struct',
          merged2['_src'] == {'net_weight_g': 'page_regex', 'unit_count': 'page_regex',
                              'declared_net_weight_g': 'page_regex', 'serving_size_g': 'vision_struct'})

    # ── NOT RUN: checks lacking a crop-sourced value never produce a verdict ──
    r = pe._check_net_weight({'declared_net_weight_g': 454}, fill_weight_g=454)
    check('net weight with no serving fields -> NOT_RUN (not UNVERIFIED, not PASS)',
          r['status'] == 'NOT_RUN')
    check('NOT_RUN note names both missing fields and points at Reader Provenance',
          any('serving size, servings per container' in n and 'Reader Provenance' in n
              for n in r['notes']))
    check('net weight with no serving fields and no fill stays UNVERIFIED (fill is the limit there)',
          pe._check_net_weight({'declared_net_weight_g': 454}, fill_weight_g=None)['status'] == 'UNVERIFIED')
    check('prep block with an empty panel -> NOT_RUN',
          pe._check_prep_block('Instructions\n1. Mix\n2. Cook', {})['status'] == 'NOT_RUN')
    check('prep block with a serving declaration but no classifiable signal -> UNKNOWN (unchanged)',
          pe._check_prep_block('Instructions\n1. Mix\n2. Cook',
                               {'serving_size_desc': '3/4 Cup'})['status'] == 'UNKNOWN')
    check('NOT_RUN is a not-verified status for completeness accounting',
          'NOT_RUN' in pe._NOT_VERIFIED_STATUSES)
    import readydoc as rd
    check('NOT_RUN never files to ReadyDoc as pass', 'NOT_RUN' in rd._UNVERIFIED_STATUSES)

    # ── 3a/3b. CLEAN means every check ran; checks_run / checks_skipped ──────
    res = {'checks': {'gtin': {'status': 'OK', 'issues': []},
                      'netwt': {'status': 'NOT_RUN', 'issues': []},
                      'prep': {'status': 'NOT_RUN', 'issues': []}}}
    pe._recount_result(res)
    check('zero findings from skipped checks -> INCOMPLETE, never CLEAN', res['severity'] == 'incomplete')
    check('checks_run / checks_skipped are recorded',
          res['checks_run'] == ['gtin'] and res['checks_skipped'] == ['netwt', 'prep'])
    res2 = {'checks': {'gtin': {'status': 'OK', 'issues': []}, 'netwt': {'status': 'PASS', 'issues': []}}}
    pe._recount_result(res2)
    check('every check ran and passed -> CLEAN', res2['severity'] == 'clean' and res2['checks_skipped'] == [])
    res3 = {'checks': {'gtin': {'status': 'OK', 'issues': [{'severity': 'warning', 'message': 'x'}]},
                       'netwt': {'status': 'NOT_RUN', 'issues': []}}}
    pe._recount_result(res3)
    check('a real WARNING still outranks INCOMPLETE', res3['severity'] == 'warning')
    res4 = {'checks': {'panel': {'status': 'PANEL_MISSING', 'issues': []}, 'gtin': {'status': 'OK', 'issues': []}}}
    pe._recount_result(res4)
    check('PANEL_MISSING (accurate, not a defect) still means the file is INCOMPLETE, not CLEAN',
          res4['severity'] == 'incomplete')
    s = pe._build_summary([dict(res, filename='a.pdf'), dict(res2, filename='b.pdf')])
    check('_build_summary counts incomplete files separately from clean',
          s['severity_counts']['incomplete'] == 1 and s['severity_counts']['clean'] == 1)

    # ── SUSPECT band recalibration: 105–130 g/cup ───────────────────────────
    for ss in (83, 88, 93):
        r = pe._check_net_weight({'serving_size_g': ss, 'serving_size_cups': 0.75,
                                  'servings_per_container': 5, 'declared_net_weight_g': 454},
                                 fill_weight_g=454)
        check(f'a correct {ss}g / 3/4 cup panel ({ss / 0.75:.0f} g/cup) is PASS with no density finding',
              r['status'] == 'PASS' and not r['issues'])
    r = pe._check_net_weight({'serving_size_g': 198, 'serving_size_cups': 0.75, 'serving_size_desc': '3/4 Cup',
                              'servings_per_container': 2.29, 'declared_net_weight_g': 454}, fill_weight_g=454)
    check('a clean 2x density misread (264 g/cup) is still SUSPECT',
          r['status'] == 'SUSPECT' and any('105–130' in i['message'] for i in r['issues']))

    # ── 3c. Master-list fetch retries transient failures with backoff ───────
    _orig_fetch, _orig_sleep = A._fetch_sheet_rows, A.time.sleep
    calls = []
    sleeps = []
    A.time.sleep = lambda s: sleeps.append(s)
    try:
        def _flaky(url):
            calls.append(url)
            if len(calls) < 3:
                raise urllib.error.HTTPError(url, 502, 'Bad Gateway', None, None)
            return [{'sku': 'X'}]
        A._fetch_sheet_rows = _flaky
        rows = A._fetch_sheet_rows_with_retry('https://x/master.csv')
        check('two 502s then success -> rows returned after 3 attempts',
              rows == [{'sku': 'X'}] and len(calls) == 3)
        check('backoff slept between attempts', sleeps == [1.0, 2.0])

        calls.clear()
        A._fetch_sheet_rows = lambda url: (_ for _ in ()).throw(
            urllib.error.HTTPError(url, 502, 'Bad Gateway', None, None))
        try:
            A._fetch_sheet_rows_with_retry('https://x/master.csv')
            check('three 502s -> raises', False)
        except urllib.error.HTTPError as e:
            check('three 502s -> raises the last error after 3 attempts', e.code == 502)

        calls.clear()
        def _auth_fail(url):
            calls.append(url)
            raise urllib.error.HTTPError(url, 401, 'Unauthorized', None, None)
        A._fetch_sheet_rows = _auth_fail
        try:
            A._fetch_sheet_rows_with_retry('https://x/master.csv')
        except urllib.error.HTTPError:
            pass
        check('a 401 (not transient) is not retried', len(calls) == 1)
    finally:
        A._fetch_sheet_rows, A.time.sleep = _orig_fetch, _orig_sleep

    # ── 3d. An unavailable master list aborts the run ────────────────────────
    _env = {k: os.environ.get(k) for k in ('GTIN_SHEET_URL', 'READYDOC_URL')}
    _cfg_path, _cache = A.GTIN_SHEET_CFG_PATH, dict(A._sheet_cache)
    tmp = tempfile.mkdtemp()
    A.GTIN_SHEET_CFG_PATH = os.path.join(tmp, 'cfg.json')
    try:
        os.environ['GTIN_SHEET_URL'] = 'https://app.powder-ops.com/api/products/master.csv?token=t'
        os.environ['READYDOC_URL'] = 'https://app.powder-ops.com'
        A._sheet_cache['last_error'] = 'HTTP Error 502: Bad Gateway'
        msg = A._master_list_blocker([], used_upload=False)
        check('configured master list + 0 rows -> run aborted with the fetch error named',
              msg is not None and 'aborted' in msg and '502' in msg and 'no per-file verdicts' in msg.lower())
        check('rows present -> not blocked', A._master_list_blocker([{'sku': 'X'}], False) is None)
        check('an uploaded GTIN file -> not blocked (no master list needed)',
              A._master_list_blocker([], used_upload=True) is None)
        os.environ.pop('GTIN_SHEET_URL')
        check('nothing configured -> not blocked here (the NOT CONFIGURED banner handles that)',
              A._master_list_blocker([], False) is None)
    finally:
        for k, v in _env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        A.GTIN_SHEET_CFG_PATH = _cfg_path
        A._sheet_cache.clear(); A._sheet_cache.update(_cache)

    # ── 2 / 3b. The workbook: Reader Provenance sheet + Checks run/skipped ───
    import openpyxl
    job = {'id': 'job1', 'created': '2026-10-05T00:00:00', 'dismissals': {}, 'summary': {},
           'results': [{
               'filename': 'PD_pancake_chocolate.pdf', 'severity': 'incomplete',
               'critical_count': 0, 'warning_count': 0, 'info_count': 0,
               'checks': {'gtin': {'status': 'OK', 'issues': [], 'notes': []},
                          'netwt': {'status': 'NOT_RUN', 'issues': [], 'notes': ['NOT RUN — x']}},
               'checks_run': ['gtin'], 'checks_skipped': ['netwt'],
               'panel_values': {'serving_size_g': None, 'serving_size_cups': None,
                                'servings_per_container': None, 'serving_size_desc': None,
                                'declared_net_weight_g': 454.0, 'unit_count': 21.0},
               'panel_provenance': {'serving_size_g': None, 'serving_size_cups': None,
                                    'servings_per_container': None, 'serving_size_desc': None,
                                    'declared_net_weight_g': 'page_regex', 'unit_count': 'page_regex'},
               'panel_page_regex': {'serving_size_g': 65.0},
           }]}
    buf = A._generate_report(job)
    wb = openpyxl.load_workbook(io.BytesIO(buf.getvalue()))
    check('workbook has a Reader Provenance sheet', 'Reader Provenance' in wb.sheetnames)
    ws = wb['Summary']
    hdrs = [ws.cell(row=4, column=c).value for c in range(1, 8)]
    check('Summary has Checks run / Checks skipped columns',
          hdrs[5] == 'Checks run' and hdrs[6] == 'Checks skipped')
    check('Summary row states INCOMPLETE and names the skipped check',
          ws.cell(row=5, column=2).value == 'INCOMPLETE' and 'netwt' in str(ws.cell(row=5, column=7).value))
    wp = wb['Reader Provenance']
    row = [wp.cell(row=5, column=c).value for c in range(1, 9)]
    check('provenance row marks absent serving fields as NOT RUN',
          'NOT RUN' in str(row[1]) and 'NOT RUN' in str(row[3]))
    check('provenance row labels a page_regex field as PAGE_REGEX, not data',
          'PAGE_REGEX' in str(row[5]))
    check('the unused page-regex serving candidate (65) is listed, flagged, and nowhere else',
          'serving_size_g=65.0' in str(row[7]))

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All reader-provenance checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
