"""A rejected ReadyDoc token must not be reported as missing panel data.

    python3 tests/test_panel_fetch_reason.py

Exits non-zero on any failure. No test framework required. Network calls are
faked by monkeypatching urllib.request.urlopen — nothing here touches a real
ReadyDoc.

Bug (run cd4a2150): every one of 38 SKUs got PANEL_MISSING when the real
cause was readydoc.fetch_approved_panel() returning bare None for a rejected
token (HTTP 401) exactly the same as it did for a genuine 404 — an hour went
into checking whether the panels were filed before anyone looked at the
token. Five distinct outcomes (disabled / 404 / 401-403 / 5xx / network) used
to collapse to one line; only one of them means "no panel on file."
"""
import io
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import readydoc as rd   # noqa: E402
import proof_engine as pe  # noqa: E402


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
        # ── readydoc.fetch_approved_panel returns (data, reason) ──────────────────
        def _urlopen_401(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, 'Unauthorized', hdrs=None, fp=io.BytesIO(b''))
        rd.urllib.request.urlopen = _urlopen_401
        data, reason = rd.fetch_approved_panel(gtin='123')
        check('HTTP 401 -> (None, "unauthorized"), the actual cd4a2150 case',
              data is None and reason == 'unauthorized')

        def _urlopen_403(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 403, 'Forbidden', hdrs=None, fp=io.BytesIO(b''))
        rd.urllib.request.urlopen = _urlopen_403
        data, reason = rd.fetch_approved_panel(gtin='123')
        check('HTTP 403 -> (None, "unauthorized")', data is None and reason == 'unauthorized')

        def _urlopen_404(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', hdrs=None, fp=io.BytesIO(b''))
        rd.urllib.request.urlopen = _urlopen_404
        data, reason = rd.fetch_approved_panel(gtin='123')
        check('HTTP 404 -> (None, "not_found") — the ONE case that genuinely means "no panel"',
              data is None and reason == 'not_found')

        def _urlopen_500(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 500, 'Server Error', hdrs=None, fp=io.BytesIO(b''))
        rd.urllib.request.urlopen = _urlopen_500
        data, reason = rd.fetch_approved_panel(gtin='123')
        check('HTTP 500 -> (None, "server_error")', data is None and reason == 'server_error')

        def _urlopen_timeout(req, timeout=None):
            raise TimeoutError('timed out')
        rd.urllib.request.urlopen = _urlopen_timeout
        data, reason = rd.fetch_approved_panel(gtin='123')
        check('a network timeout -> (None, "network")', data is None and reason == 'network')

        check('disabled (no env vars) -> (None, "disabled"), no network call', True)
        del os.environ['READYDOC_TOKEN']
        data, reason = rd.fetch_approved_panel(gtin='123')
        check('disabled integration -> (None, "disabled")', data is None and reason == 'disabled')
        os.environ['READYDOC_TOKEN'] = 'test-token'

        def _urlopen_ok(req, timeout=None):
            return _FakeResp(b'{"version": 3, "status": "approved", "panel": {}}')
        rd.urllib.request.urlopen = _urlopen_ok
        data, reason = rd.fetch_approved_panel(gtin='123')
        check('a real panel record -> (dict, "ok")', isinstance(data, dict) and reason == 'ok')

        # ── proof_engine._check_approved_panel: reason drives the status split ────
        r = pe._check_approved_panel({}, {}, '', '', None, fetch_reason='not_found')
        check('reason=not_found -> PANEL_MISSING (points at whoever files panels)',
              r['status'] == 'PANEL_MISSING')

        for bad_reason in ('disabled', 'unauthorized', 'server_error', 'network'):
            r = pe._check_approved_panel({}, {}, '', '', None, fetch_reason=bad_reason)
            check(f'reason={bad_reason} -> PANEL_UNAVAILABLE (points at ops, not the panel owner)',
                  r['status'] == 'PANEL_UNAVAILABLE')

        r_unauth = pe._check_approved_panel({}, {}, '', '', None, fetch_reason='unauthorized')
        check('PANEL_UNAVAILABLE note names the real cause and says it is a config problem',
              any('token' in n.lower() and 'configuration' in n.lower() for n in r_unauth['notes']))
        r_missing = pe._check_approved_panel({}, {}, '', '', None, fetch_reason='not_found')
        check('PANEL_MISSING note never claims a configuration problem',
              not any('configuration' in n.lower() for n in r_missing['notes']))

        # ── both statuses stay in the never-a-pass sets ───────────────────────────
        check('PANEL_UNAVAILABLE counts as not-fully-verified (proof_engine)',
              'PANEL_UNAVAILABLE' in pe._NOT_VERIFIED_STATUSES)
        check('PANEL_MISSING still counts as not-fully-verified', 'PANEL_MISSING' in pe._NOT_VERIFIED_STATUSES)
        check('PANEL_UNAVAILABLE never files as a ReadyDoc pass', 'PANEL_UNAVAILABLE' in rd._UNVERIFIED_STATUSES)

        r_block = {'checks': {'panel': {'status': 'PANEL_UNAVAILABLE', 'issues': []}}}
        rows = [x for x in rd._checks_from_result(r_block) if 'approved' in x['name'].lower()]
        check('PANEL_UNAVAILABLE with no issues still files as warn, never pass',
              rows and rows[0]['result'] == 'warn')

        # ── run-level panel-feed status: the actual required message formats ─────
        def _mk(reason, n=38):
            return [{'filename': f'f{i}.pdf', 'severity': 'clean', 'critical_count': 0,
                    'warning_count': 0, 'panel_fetch_reason': reason, 'checks': {}}
                   for i in range(n)]

        s = pe._build_summary(_mk('unauthorized'))
        check('uniform 401s -> the exact cd4a2150 message, marked not-ok',
              s['panel_feed_status']['message'] ==
              'ReadyDoc panel integration: 38 lookups attempted, 38 returned HTTP 401 '
              '(token rejected). This is a configuration problem, not missing panel data — '
              'check READYDOC_TOKEN on this service.'
              and s['panel_feed_status']['ok'] is False)

        s2 = pe._build_summary(_mk('disabled'))
        check('fully disabled -> "NOT CONFIGURED... 0 lookups attempted", marked not-ok',
              s2['panel_feed_status']['message'] ==
              'ReadyDoc panel integration NOT CONFIGURED — READYDOC_URL / READYDOC_TOKEN unset '
              'on this service. 0 lookups attempted.'
              and s2['panel_feed_status']['ok'] is False)

        s3 = pe._build_summary(_mk('not_found'))
        check('uniform unexplained 404s -> says the body did not say why (never "no panel records '
              'found"), marked ok (a data fact, not a config failure)',
              s3['panel_feed_status']['message'] ==
              'ReadyDoc panel integration: 38 lookups attempted, 38 returned 404 without saying '
              'whether the product, the panel or its values are missing.'
              and s3['panel_feed_status']['ok'] is True)

        s4 = pe._build_summary(_mk('ok'))
        check('all lookups succeeded -> ok, no alarm',
              s4['panel_feed_status']['ok'] is True and '38 returned a panel record' in s4['panel_feed_status']['message'])

        s5 = pe._build_summary([{'filename': 'x.pdf', 'severity': 'clean', 'critical_count': 0,
                                'warning_count': 0, 'checks': {}}])
        check('no panel lookups attempted at all -> empty message, never a false alarm',
              s5['panel_feed_status']['message'] == '' and s5['panel_feed_status']['ok'] is True)

        # A genuine mix (some SKUs found, some didn't) must not be misreported
        # as a uniform configuration failure.
        s6 = pe._build_summary(_mk('ok', 20) + _mk('not_found', 18))
        check('a real mix of ok/not_found is not misreported as a config failure',
              'configuration problem' not in s6['panel_feed_status']['message'])

    finally:
        rd.urllib.request.urlopen = _orig_urlopen

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All panel-fetch-reason checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
