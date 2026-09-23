"""ReadyDoc side of the approved-nutrition-panel check: fetch_approved_panel,
fetch_prior_panel_version, the new PANEL_* statuses filing as warn (never
pass), and panel_version riding along in the ingest payload.

    python3 tests/test_readydoc_approved_panel.py

Exits non-zero on any failure. No test framework required. Network calls are
faked by monkeypatching urllib.request.urlopen — nothing here touches a real
ReadyDoc.
"""
import io
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import readydoc as rd  # noqa: E402


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    os.environ['READYDOC_URL'] = 'https://ready.example.com'
    os.environ['READYDOC_TOKEN'] = 'test-token'
    _orig_urlopen = rd.urllib.request.urlopen

    try:
        # ── fetch_approved_panel: approved / draft / 404 / 500 / outage ────────
        def _urlopen_approved(req, timeout=None):
            return _FakeResp(json.dumps({'sku': 'PSP-NEA', 'version': 3, 'status': 'approved',
                                         'panel': {'calories': 120}, 'front_callouts': {}}).encode())
        rd.urllib.request.urlopen = _urlopen_approved
        p = rd.fetch_approved_panel(gtin='850079939479')
        check('approved panel: returns the dict as-is', p and p['status'] == 'approved' and p['version'] == 3)

        def _urlopen_draft(req, timeout=None):
            return _FakeResp(json.dumps({'version': 2, 'status': 'draft', 'panel': {}}).encode())
        rd.urllib.request.urlopen = _urlopen_draft
        p = rd.fetch_approved_panel(sku='PSP-NEA')
        check('draft panel: still returned (a real record, not missing)',
              p and p['status'] == 'draft')

        def _urlopen_404(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', hdrs=None, fp=io.BytesIO(b''))
        rd.urllib.request.urlopen = _urlopen_404
        p = rd.fetch_approved_panel(gtin='000')
        check('404 (no panel record at all) -> None', p is None)

        def _urlopen_500(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 500, 'Server Error', hdrs=None, fp=io.BytesIO(b'boom'))
        rd.urllib.request.urlopen = _urlopen_500
        p = rd.fetch_approved_panel(gtin='000')
        check('500 -> None (degrades, does not raise)', p is None)

        def _urlopen_raises(req, timeout=None):
            raise ConnectionError('outage')
        rd.urllib.request.urlopen = _urlopen_raises
        p = rd.fetch_approved_panel(gtin='000')
        check('network outage -> None (does not raise)', p is None)

        check('disabled (no gtin/sku) -> None without a network call',
              rd.fetch_approved_panel() is None)

        # ── fetch_prior_panel_version ──────────────────────────────────────────
        def _urlopen_prior(req, timeout=None):
            return _FakeResp(json.dumps({'ingredients': 'Whey.', 'panel_version': 2}).encode())
        rd.urllib.request.urlopen = _urlopen_prior
        v = rd.fetch_prior_panel_version(gtin='850079939479')
        check('prior panel_version echoed back on the snapshot record', v == 2)

        def _urlopen_no_version(req, timeout=None):
            return _FakeResp(json.dumps({'ingredients': 'Whey.'}).encode())
        rd.urllib.request.urlopen = _urlopen_no_version
        v = rd.fetch_prior_panel_version(gtin='850079939479')
        check('older ReadyDoc that does not echo panel_version -> None (degrades, PANEL_SUPERSEDED just never fires)',
              v is None)

        rd.urllib.request.urlopen = _urlopen_raises
        v = rd.fetch_prior_panel_version(gtin='850079939479')
        check('prior-version fetch outage -> None, does not raise', v is None)

        # ── status/label wiring ─────────────────────────────────────────────────
        check("'panel' check key has a QA-queue label",
              rd._CHECK_LABEL.get('panel') == 'Nutrition panel vs approved (ReadyDoc)')
        for st in ('PANEL_MISSING', 'PANEL_NOT_APPROVED', 'PANEL_SUPERSEDED'):
            check(f'{st} is in _UNVERIFIED_STATUSES (files as warn, never pass)',
                  st in rd._UNVERIFIED_STATUSES)

        def _result(checks):
            return {'checks': checks}

        r = _result({'panel': {'status': 'PANEL_MISSING', 'issues': []}})
        rows = [x for x in rd._checks_from_result(r) if 'approved' in x['name'].lower()]
        check('PANEL_MISSING with no issues files as warn, never pass',
              rows and rows[0]['result'] == 'warn')

        r = _result({'panel': {'status': 'PANEL_NOT_APPROVED', 'issues': []}})
        rows = [x for x in rd._checks_from_result(r) if 'approved' in x['name'].lower()]
        check('PANEL_NOT_APPROVED with no issues files as warn, never pass',
              rows and rows[0]['result'] == 'warn')

        r = _result({'panel': {'status': 'CRITICAL', 'issues': [
            {'severity': 'critical', 'message': 'Iron: artwork reads 0.92mg, approved says 0.3mg'}]}})
        rows = [x for x in rd._checks_from_result(r) if 'approved' in x['name'].lower()]
        check('a real panel mismatch still files as fail', rows and rows[0]['result'] == 'fail')

        r = _result({'panel': {'status': 'VERIFIED', 'issues': []}})
        rows = [x for x in rd._checks_from_result(r) if 'approved' in x['name'].lower()]
        check('VERIFIED with no issues files as pass', rows and rows[0]['result'] == 'pass')

        # ── panel_version rides along in the ingest payload ────────────────────
        def _urlopen_ingest(req, timeout=None):
            return _FakeResp(json.dumps({'ok': True, 'version_id': 'v1'}).encode())
        rd.urllib.request.urlopen = _urlopen_ingest
        posted = {}
        _orig_post = rd._post

        def _spy_post(path, payload):
            posted['path'], posted['payload'] = path, payload
            return _orig_post(path, payload)
        rd._post = _spy_post
        try:
            result = {'filename': 'x.pdf', 'checks': {'gtin': {'found_gtins': ['850079939479']}},
                     'matched_spec': {'sku': 'PSP-NEA'}, 'panel_version': 3}
            rd.publish_job('job1', [result])
            check('ingest payload carries panel_version', posted.get('payload', {}).get('panel_version') == 3)
        finally:
            rd._post = _orig_post

    finally:
        rd.urllib.request.urlopen = _orig_urlopen

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All ReadyDoc approved-nutrition-panel checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
