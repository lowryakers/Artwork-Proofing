"""ReadyDoc ingest severity mapping — a check graded suspect/review, or left
UNVERIFIED with no graded issues, must never file as ReadyDoc 'pass'.

    python3 tests/test_readydoc_severity.py

Exits non-zero on any failure. No test framework required.

Bug (Powder-Ops-FSQA protocol doc, Live gap 2): _checks_from_result only graded
issues whose severity was in {'critical', 'warning'}. A net-weight SUSPECT READ
(or a claims/labeling 'review' issue) has neither, so it fell out of `graded`
and the fallback wrote result:'pass' — a doubtful or unverified read filed into
ReadyDoc as if it had been checked and was fine.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import readydoc as rd  # noqa: E402


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    def _result(checks):
        return {'checks': checks}

    def _only(rows, name_substr):
        return [r for r in rows if name_substr.lower() in r['name'].lower()]

    # ── suspect-only issues → warn, never pass ────────────────────────────────
    r = _result({'netwt': {'status': 'SUSPECT', 'issues': [
        {'severity': 'suspect', 'message': 'SUSPECT READ — reconciles but OCR-only serving.'}]}})
    rows = _only(rd._checks_from_result(r), 'net weight')
    check('suspect-only issue → warn (not pass)', rows and all(x['result'] == 'warn' for x in rows))
    check('suspect issue never files as pass', not any(x['result'] == 'pass' for x in rows))

    # ── review-only issues → warn ──────────────────────────────────────────────
    r = _result({'claims': {'status': 'REVIEW', 'issues': [
        {'severity': 'review', 'message': 'Comparative claim needs a human look.'}]}})
    rows = _only(rd._checks_from_result(r), 'claims')
    check('review-only issue → warn', rows and all(x['result'] == 'warn' for x in rows))

    # ── empty issues + status UNVERIFIED → warn, with a "not verified" detail ──
    r = _result({'netwt': {'status': 'UNVERIFIED', 'issues': [],
                          'notes': ['NOT VERIFIED — no fill weight supplied for this SKU.']}})
    rows = _only(rd._checks_from_result(r), 'net weight')
    check('empty issues + UNVERIFIED status → warn', rows and rows[0]['result'] == 'warn')
    check('UNVERIFIED detail leads with the limitation',
          'not verified' in rows[0].get('detail', '').lower())

    # UNKNOWN / SUSPECT block statuses are covered by the same rule.
    for bad_status in ('UNKNOWN', 'SUSPECT'):
        r = _result({'prep': {'status': bad_status, 'issues': []}})
        rows = _only(rd._checks_from_result(r), 'prep')
        check(f'empty issues + {bad_status} status → warn', rows and rows[0]['result'] == 'warn')

    # ── empty issues + no bad status → pass ───────────────────────────────────
    r = _result({'eyemark': {'status': 'PASS', 'issues': []}})
    rows = _only(rd._checks_from_result(r), 'eyemark')
    check('empty issues, clean status → pass', rows and rows[0]['result'] == 'pass')

    r = _result({'gtin': {'issues': []}})  # no status key at all
    rows = _only(rd._checks_from_result(r), 'gtin')
    check('empty issues, no status key → pass', rows and rows[0]['result'] == 'pass')

    # ── critical → fail; warning → warn (unchanged) ───────────────────────────
    r = _result({'gtin': {'status': 'FAIL', 'issues': [
        {'severity': 'critical', 'message': 'GTIN mismatch.'}]}})
    rows = _only(rd._checks_from_result(r), 'gtin')
    check('critical issue → fail', rows and rows[0]['result'] == 'fail')

    r = _result({'specs': {'status': 'WARN', 'issues': [
        {'severity': 'warning', 'message': 'Material mismatch.'}]}})
    rows = _only(rd._checks_from_result(r), 'print specs')
    check('warning issue → warn', rows and rows[0]['result'] == 'warn')

    # ── info issues alone are still not findings — degrade to the status check,
    #    not a fabricated escalation, but also never silently "pass" if the
    #    block itself is unverified. ────────────────────────────────────────────
    r = _result({'specs': {'status': 'PASS', 'issues': [
        {'severity': 'info', 'message': 'Bleed: 3mm.'}]}})
    rows = _only(rd._checks_from_result(r), 'print specs')
    check('info-only issues do not escalate to warn/fail', rows and rows[0]['result'] == 'pass')

    r = _result({'netwt': {'status': 'UNVERIFIED', 'issues': [
        {'severity': 'info', 'message': 'Some note.'}], 'notes': []}})
    rows = _only(rd._checks_from_result(r), 'net weight')
    check('info-only issues + UNVERIFIED status → still warn, not pass',
          rows and rows[0]['result'] == 'warn')

    # ── an unrecognized severity never disappears into a false pass ──────────
    r = _result({'nfp': {'status': 'FLAGGED', 'issues': [
        {'severity': 'some_future_severity', 'message': 'New severity the map does not know.'}]}})
    rows = _only(rd._checks_from_result(r), 'front')
    check('unrecognized severity → warn (never dropped to a fake pass)',
          rows and rows[0]['result'] == 'warn')

    # ── skipped blocks are still skipped entirely (unchanged) ────────────────
    r = _result({'wind': {'status': 'SKIPPED', 'issues': [], 'skipped': True}})
    rows = _only(rd._checks_from_result(r), 'wind')
    check('skipped block emits no row', rows == [])

    # ── mixed block: one critical + one suspect → both rows present ──────────
    r = _result({'netwt': {'status': 'CRITICAL', 'issues': [
        {'severity': 'critical', 'message': 'Overstated net weight.'},
        {'severity': 'suspect', 'message': 'Serving read looks off.'}]}})
    rows = _only(rd._checks_from_result(r), 'net weight')
    check('mixed severities: two rows, fail + warn',
          len(rows) == 2 and {r['result'] for r in rows} == {'fail', 'warn'})

    # ── _SEVERITY map itself ───────────────────────────────────────────────────
    check('_SEVERITY maps suspect → warn', rd._SEVERITY.get('suspect') == 'warn')
    check('_SEVERITY maps review → warn', rd._SEVERITY.get('review') == 'warn')
    check('_SEVERITY maps critical → fail', rd._SEVERITY.get('critical') == 'fail')
    check('_SEVERITY maps warning → warn', rd._SEVERITY.get('warning') == 'warn')
    check('_SEVERITY never emits a value outside pass/fail/warn',
          set(rd._SEVERITY.values()) <= {'pass', 'fail', 'warn'})

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All ReadyDoc severity-mapping checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
