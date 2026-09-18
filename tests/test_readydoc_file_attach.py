"""ReadyDoc ingest → preview/print-PDF file attach.

    python3 tests/test_readydoc_file_attach.py

Exits non-zero on any failure. No test framework required. Network calls are
faked by monkeypatching urllib.request.urlopen — nothing here touches a real
ReadyDoc.

Bug (Powder-Ops-FSQA protocol doc — Live gap 1): publish_job() POSTed the JSON
ingest and stopped. ReadyDoc's multipart route
(POST /api/artwork/ingest/:version_id/files) already exists and stores
kind=preview / print_pdf / proof_report, but the proofer never called it, so
Artwork cards showed a generic icon instead of the pack even after a
successful proof.
"""
import io
import json
import os
import sys
import tempfile
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


def _base_result(**over):
    r = {
        'filename': 'WHY-BTL-CPB.pdf',
        'checks': {'gtin': {'found_gtins': ['850012345678']}},
        'matched_spec': {'sku': 'WHY-BTL-CPB'},
        'snapshot': None,
        'format': None,
        'die_template': None,
    }
    r.update(over)
    return r


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    os.environ['READYDOC_URL'] = 'https://ready.example.com'
    os.environ['READYDOC_TOKEN'] = 'test-token'

    _orig_urlopen = rd.urllib.request.urlopen
    tmp_png = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    tmp_png.write(b'not a real png, just bytes')
    tmp_png.close()

    def _is_file_attach_url(url):
        return '/api/artwork/ingest/' in url and url.split('?')[0].rstrip('/').endswith('/files')

    try:
        # ── 1. Successful ingest + version_id → preview attach with kind=preview ──
        calls = []

        def fake_urlopen_ok(req, timeout=None):
            url = req.full_url
            if _is_file_attach_url(url):
                calls.append(('file', url, req.data, req.get_header('Content-type')))
                return _FakeResp(b'{"ok": true}')
            calls.append(('ingest', url, req.data))
            return _FakeResp(json.dumps({'ok': True, 'version_id': 'v123'}).encode())

        rd.urllib.request.urlopen = fake_urlopen_ok
        result = _base_result(img_path=tmp_png.name, pdf_path=None)
        rd.publish_job('job1', [result])

        ingest_calls = [c for c in calls if c[0] == 'ingest']
        file_calls = [c for c in calls if c[0] == 'file']
        check('JSON ingest posted once', len(ingest_calls) == 1)
        check('preview file attach posted to the version_id from ingest',
              len(file_calls) == 1 and '/api/artwork/ingest/v123/files' in file_calls[0][1])
        check('preview attach body carries kind=preview',
              b'name="kind"' in file_calls[0][2] and b'preview' in file_calls[0][2])
        check('preview attach is multipart/form-data',
              (file_calls[0][3] or '').lower().startswith('multipart/form-data'))
        check('no print_pdf attach when pdf_path is None', not any(b'print_pdf' in c[2] for c in file_calls))

        # ── 2. print_pdf attach when a pdf_path is present too ────────────────────
        calls.clear()
        tmp_pdf = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
        tmp_pdf.write(b'%PDF-1.4 fake')
        tmp_pdf.close()
        result2 = _base_result(img_path=tmp_png.name, pdf_path=tmp_pdf.name)
        rd.publish_job('job2', [result2])
        file_calls2 = [c for c in calls if c[0] == 'file']
        kinds = [c[2] for c in file_calls2]
        check('both preview and print_pdf attached when both paths exist',
              len(file_calls2) == 2
              and any(b'preview' in k for k in kinds)
              and any(b'print_pdf' in k for k in kinds))
        os.remove(tmp_pdf.name)

        # ── 3. Missing img_path → no file POST, JSON ingest still succeeds ────────
        calls.clear()
        result3 = _base_result(img_path=None, pdf_path=None)
        rd.publish_job('job3', [result3])
        check('missing img_path: JSON ingest still posted', any(c[0] == 'ingest' for c in calls))
        check('missing img_path: no file attach attempted', not any(c[0] == 'file' for c in calls))

        # A path that simply does not exist on disk behaves the same as None.
        calls.clear()
        result3b = _base_result(img_path='/no/such/file.png', pdf_path=None)
        rd.publish_job('job3b', [result3b])
        check('nonexistent img_path path: no file attach attempted', not any(c[0] == 'file' for c in calls))

        # ── 4. Ingest ok but no version_id in response → file attach skipped,
        #      no exception ────────────────────────────────────────────────────────
        calls.clear()

        def fake_urlopen_no_version(req, timeout=None):
            calls.append(('ingest', req.full_url, req.data))
            return _FakeResp(json.dumps({'ok': True}).encode())

        rd.urllib.request.urlopen = fake_urlopen_no_version
        result4 = _base_result(img_path=tmp_png.name, pdf_path=None)
        try:
            rd.publish_job('job4', [result4])
            check('no version_id in ingest response: publish_job does not raise', True)
        except Exception as exc:
            check(f'no version_id in ingest response: publish_job does not raise ({exc})', False)
        check('no version_id: no file attach attempted', not any('files' in c[1] for c in calls if len(c) > 1))

        # ── 5. File attach HTTP error → logged, swallowed, never raises ───────────
        def fake_urlopen_attach_fails(req, timeout=None):
            url = req.full_url
            if _is_file_attach_url(url):
                raise urllib.error.HTTPError(url, 503, 'Service Unavailable',
                                             hdrs=None, fp=io.BytesIO(b'R2 not configured'))
            return _FakeResp(json.dumps({'ok': True, 'version_id': 'v999'}).encode())

        rd.urllib.request.urlopen = fake_urlopen_attach_fails
        result5 = _base_result(img_path=tmp_png.name, pdf_path=None)
        try:
            rd.publish_job('job5', [result5])
            check('file attach HTTP error (503) is swallowed, never raises', True)
        except Exception as exc:
            check(f'file attach HTTP error (503) is swallowed, never raises ({exc})', False)

        # ── 6. JSON ingest itself fails → no file attach attempted, no raise ──────
        def fake_urlopen_ingest_fails(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 500, 'Server Error',
                                         hdrs=None, fp=io.BytesIO(b'boom'))

        rd.urllib.request.urlopen = fake_urlopen_ingest_fails
        result6 = _base_result(img_path=tmp_png.name, pdf_path=None)
        try:
            rd.publish_job('job6', [result6])
            check('ingest HTTP failure: publish_job does not raise', True)
        except Exception as exc:
            check(f'ingest HTTP failure: publish_job does not raise ({exc})', False)

    finally:
        rd.urllib.request.urlopen = _orig_urlopen
        os.remove(tmp_png.name)

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All ReadyDoc file-attach checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
