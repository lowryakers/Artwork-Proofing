"""Master-list source resolution: GTIN_SHEET_URL is the sole, authoritative
source. No Google Sheet fallback, no stale runtime file winning over the
environment, and a non-ReadyDoc URL is refused unless MASTER_ALLOW_EXTERNAL
is explicitly set.

    python3 tests/test_master_sheet_config.py

Exits non-zero on any failure. No test framework, no network, no Flask
server -- app.py is imported as a plain module and its helpers are called
directly, with GTIN_SHEET_CFG_PATH redirected to a scratch file so nothing
here touches the real uploads/ directory.
"""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as A  # noqa: E402


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    tmpdir = tempfile.mkdtemp()
    cfg_path = os.path.join(tmpdir, 'gtin_sheet_config.json')

    _orig_cfg_path = A.GTIN_SHEET_CFG_PATH
    _orig_allow_external = A.MASTER_ALLOW_EXTERNAL
    _orig_env = {k: os.environ.get(k) for k in ('GTIN_SHEET_URL', 'READYDOC_URL')}

    def _reset(env_url='', readydoc_url='https://app.powder-ops.com', allow_external=False,
               runtime_cfg=None):
        A.GTIN_SHEET_CFG_PATH = cfg_path
        A.MASTER_ALLOW_EXTERNAL = allow_external
        if os.path.exists(cfg_path):
            os.remove(cfg_path)
        if runtime_cfg is not None:
            with open(cfg_path, 'w') as f:
                json.dump(runtime_cfg, f)
        if env_url:
            os.environ['GTIN_SHEET_URL'] = env_url
        else:
            os.environ.pop('GTIN_SHEET_URL', None)
        if readydoc_url:
            os.environ['READYDOC_URL'] = readydoc_url
        else:
            os.environ.pop('READYDOC_URL', None)

    try:
        # ── Acceptance #1: baked default is empty, no URL in the repo ──────────
        default_path = os.path.join(A.BASE_DIR, 'gtin_default_config.json')
        with open(default_path) as f:
            default_cfg = json.load(f)
        check('gtin_default_config.json is {} (baked default emptied)',
              default_cfg == {})

        repo_files = ['app.py', 'gtin_default_config.json']
        leaked = []
        for fn in repo_files:
            with open(os.path.join(A.BASE_DIR, fn)) as f:
                if 'docs.google.com/spreadsheets/d/e/2PACX' in f.read():
                    leaked.append(fn)
        check('no baked Google Sheet publish URL remains in app.py / default config',
              not leaked)

        # ── Acceptance #2: env wins over a stale runtime file ──────────────────
        _reset(env_url='https://app.powder-ops.com/api/products/master.csv?token=abc',
               readydoc_url='https://app.powder-ops.com',
               runtime_cfg={'sheet_url': 'https://docs.google.com/spreadsheets/d/OLD/pubhtml',
                            'last_synced': '2024-01-01', 'row_count': 12})
        cfg = A._load_sheet_config()
        check('env URL wins over a stale runtime file',
              cfg.get('sheet_url') == 'https://app.powder-ops.com/api/products/master.csv?token=abc')
        check('sync metadata is still carried forward from the runtime file',
              cfg.get('last_synced') == '2024-01-01' and cfg.get('row_count') == 12)

        # ── Acceptance #3: _save_sheet_config never writes sheet_url ───────────
        _reset(env_url='', readydoc_url='https://app.powder-ops.com')
        A._save_sheet_config({'sheet_url': 'https://evil.example.com/sheet',
                               'last_synced': '2024-06-01', 'row_count': 5})
        with open(cfg_path) as f:
            on_disk = json.load(f)
        check("_save_sheet_config never persists 'sheet_url'",
              'sheet_url' not in on_disk)
        check('_save_sheet_config still persists last_synced/row_count',
              on_disk.get('last_synced') == '2024-06-01' and on_disk.get('row_count') == 5)

        # merges into existing content rather than clobbering it
        A._save_sheet_config({'row_count': 9})
        with open(cfg_path) as f:
            on_disk = json.load(f)
        check('_save_sheet_config merges rather than overwrites',
              on_disk.get('last_synced') == '2024-06-01' and on_disk.get('row_count') == 9)

        # ── Acceptance #4: no URL anywhere -> loud "NOT CONFIGURED" status ─────
        _reset(env_url='', readydoc_url='https://app.powder-ops.com')
        rows = A._get_sheet_gtin_rows()
        check('_get_sheet_gtin_rows() returns [] with nothing configured', rows == [])
        status = A._master_feed_status(rows, from_upload=False)
        check("status level is 'none' when nothing is configured", status['level'] == 'none')
        check('exact NOT CONFIGURED message text',
              status['message'] == (
                  'Master list NOT CONFIGURED — GTIN_SHEET_URL is unset on this service. '
                  '0 rows loaded. Every GTIN, spec and fill-weight check was skipped.'))

        # ── Acceptance #5: one source= log line per run, token redacted ────────
        _reset(env_url='https://app.powder-ops.com/api/products/master.csv?token=SECRET123',
               readydoc_url='https://app.powder-ops.com')
        buf = io.StringIO()
        _orig_stdout = sys.stdout
        sys.stdout = buf
        try:
            A._log_master_source_once([{'sku': 'A'}, {'sku': 'B'}])
        finally:
            sys.stdout = _orig_stdout
        line = buf.getvalue().strip()
        check("log line starts with '[master] source=env'", line.startswith('[master] source=env'))
        check('token is redacted in the logged url', 'SECRET123' not in line and 'token=***' in line)
        check('row count included in the logged line', 'rows=2' in line)

        _reset(env_url='', readydoc_url='https://app.powder-ops.com')
        buf = io.StringIO()
        sys.stdout = buf
        try:
            A._log_master_source_once([])
        finally:
            sys.stdout = _orig_stdout
        check("log line reports source=none when nothing is configured",
              buf.getvalue().strip().startswith('[master] source=none'))

        # ── Acceptance #6: non-ReadyDoc host rejected while MASTER_ALLOW_EXTERNAL is false ──
        _reset(env_url='https://docs.google.com/spreadsheets/d/e/2PACX-abc/pubhtml',
               readydoc_url='https://app.powder-ops.com', allow_external=False)
        cfg = A._load_sheet_config()
        check('non-ReadyDoc host is rejected by default', 'rejected_url' in cfg)
        check('rejected cfg carries a reject_reason', bool(cfg.get('reject_reason')))

        rows = A._get_sheet_gtin_rows()
        check('_get_sheet_gtin_rows() returns [] for a rejected URL (never fetches it)', rows == [])
        status = A._master_feed_status(rows, from_upload=False)
        check("status level is 'rejected' for a non-ReadyDoc URL", status['level'] == 'rejected')

        # same URL, MASTER_ALLOW_EXTERNAL=true -> accepted
        _reset(env_url='https://docs.google.com/spreadsheets/d/e/2PACX-abc/pubhtml',
               readydoc_url='https://app.powder-ops.com', allow_external=True)
        cfg = A._load_sheet_config()
        check('MASTER_ALLOW_EXTERNAL=true allows a non-ReadyDoc URL through',
              cfg.get('sheet_url') == 'https://docs.google.com/spreadsheets/d/e/2PACX-abc/pubhtml')

        # a ReadyDoc-hosted URL is accepted even with MASTER_ALLOW_EXTERNAL=false
        _reset(env_url='https://app.powder-ops.com/api/products/master.csv?token=abc',
               readydoc_url='https://app.powder-ops.com', allow_external=False)
        cfg = A._load_sheet_config()
        check('a ReadyDoc-hosted URL is accepted with MASTER_ALLOW_EXTERNAL=false',
              cfg.get('sheet_url') == 'https://app.powder-ops.com/api/products/master.csv?token=abc')

        # with READYDOC_URL unset there is no host to verify against -> refuse
        _reset(env_url='https://app.powder-ops.com/api/products/master.csv?token=abc',
               readydoc_url='', allow_external=False)
        cfg = A._load_sheet_config()
        check('an unverifiable URL (no READYDOC_URL configured) is refused, not accepted on faith',
              'rejected_url' in cfg)

        # a rejected runtime-file URL (no env var set) is also caught
        _reset(env_url='', readydoc_url='https://app.powder-ops.com', allow_external=False,
               runtime_cfg={'sheet_url': 'https://evil.example.com/sheet.csv'})
        cfg = A._load_sheet_config()
        check('a rejected URL stored in the runtime file is also refused',
              'rejected_url' in cfg)

    finally:
        A.GTIN_SHEET_CFG_PATH = _orig_cfg_path
        A.MASTER_ALLOW_EXTERNAL = _orig_allow_external
        for k, v in _orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        A._sheet_cache = {'rows': None, 'fetched_at': 0.0, 'url': ''}

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All master-list source-resolution checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
