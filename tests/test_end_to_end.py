"""End-to-end: the four real converter files from run 9ffb350f through the REAL
pipeline, scored against expected.json and the seven defects.json scenarios.

    python3 tests/test_end_to_end.py
    ARTPROOF_E2E_REQUIRED=1 python3 tests/test_end_to_end.py   # CI: skips become failures

No mocks of the reader, no pre-extracted values, no synthetic images. What IS
stubbed, deliberately, is the reference data:

  * The master list. app._fetch_sheet_rows is replaced by a reader of the
    local master_rows.csv (mutated per scenario), then parsed by the exact
    production parser (app._parse_master_csv) and fetched through the exact
    production path (app._get_sheet_gtin_rows, with its retry). A network
    failure can never turn this suite green — and DEFECT_master_list_unavailable
    proves the 502 path end to end.
  * The approved panel record (ReadyDoc). Built from expected.json, mutated per
    scenario. Prior snapshots / prior panel versions are None, so local job
    history can't leak in.

Defects are planted in that reference data, not in the PDFs: all ProDough label
copy is converted to outlines, so there is no text in the files to edit.

Vision (the panel-crop reader) needs ANTHROPIC_API_KEY. Without it, every
assertion that depends on a panel read is reported SKIP — individually and
loudly — and the rest still run. ARTPROOF_E2E_REQUIRED=1 turns any skip into a
failure. Identical vision calls for the same PDF are memoised within a run so
every scenario sees the same read and differs only by its reference data.
"""
import copy
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

FIX = os.path.join(HERE, 'fixtures', 'artwork')
REQUIRED = bool(os.environ.get('ARTPROOF_E2E_REQUIRED'))
BRAND = {'brand_mode': 'prodough', 'proof_type': 'press', 'wind_direction': ''}
CHECK_KEY = {'gtin': 'gtin', 'net_weight': 'netwt', 'spot_color': 'specs',
             'fda_falcpa': 'fda', 'approved_panel': 'panel'}
NFP_FIELDS = ('serving_size_g', 'serving_size_cups', 'servings_per_container', 'serving_size_desc')
MASTER_URL = 'https://app.powder-ops.com/api/products/master.csv?token=e2e'


def _skip_all(reason):
    print(f'[{"FAIL" if REQUIRED else "skip"}] end-to-end: {reason}')
    return 1 if REQUIRED else 0


def _preflight():
    if not os.path.exists(os.path.join(FIX, 'expected.json')):
        return 'tests/fixtures/artwork/expected.json is missing'
    missing = [t for t in ('pdftoppm', 'tesseract') if not shutil.which(t)]
    if missing:
        return f'PDF/OCR toolchain missing: {", ".join(missing)}'
    try:
        import fitz  # noqa: F401
    except ImportError:
        return 'PyMuPDF (fitz) not installed'
    return None


class Scorer:
    def __init__(self, vision):
        self.vision = vision
        self.fails, self.skips, self.passes = [], [], 0
        self.lock = threading.Lock()

    def check(self, name, cond, needs_vision=False):
        with self.lock:
            if needs_vision and not self.vision:
                self.skips.append(name)
                print(f'[skip] {name}  (needs ANTHROPIC_API_KEY)')
                return
            print(f'[{"ok  " if cond else "FAIL"}] {name}')
            if cond:
                self.passes += 1
            else:
                self.fails.append(name)


def _master_csv(mutations_by_sku=None):
    """master_rows.csv, with per-SKU column overrides applied, as CSV text."""
    with open(os.path.join(FIX, 'master_rows.csv'), encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys())
    for r in rows:
        for col, val in (mutations_by_sku or {}).get(r['SKU'], {}).items():
            assert col in r, f'defects.json mutates unknown master column {col!r}'
            r[col] = val
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


def _fetch_master(A, csv_text=None, fail_with=None):
    """Fetch the master list through app's production path with the network
    replaced by the local CSV (or by a persistent failure). Returns
    (rows, attempts)."""
    attempts = []

    def _stub(url):
        attempts.append(url)
        if fail_with is not None:
            raise fail_with
        return A._parse_master_csv(csv_text)

    orig_fetch, orig_sleep = A._fetch_sheet_rows, A.time.sleep
    A._fetch_sheet_rows, A.time.sleep = _stub, (lambda s: None)
    A._sheet_cache.clear()
    A._sheet_cache.update({'rows': None, 'fetched_at': 0.0, 'url': ''})
    try:
        rows = A._get_sheet_gtin_rows(force=True)
    finally:
        A._fetch_sheet_rows, A.time.sleep = orig_fetch, orig_sleep
    return rows, attempts


def _install_engine_stubs(pe):
    """Approved panel per run (thread-local), no prior history, and memoised
    vision calls keyed by the source PDF."""
    local = threading.local()
    pe._fetch_approved_panel = lambda gtin=None, sku=None: getattr(local, 'approved', (None, 'not_found'))
    pe._fetch_prior_panel_version = lambda gtin=None, sku=None: None
    pe._fetch_prior_snapshot = lambda gtin=None, sku=None: None

    cache, locks, glock = {}, {}, threading.Lock()

    def _memo(fn, kind):
        def wrapped(img_path, *args):
            key = (kind, os.path.basename(img_path).split('_', 1)[-1], repr(args))
            with glock:
                lk = locks.setdefault(key, threading.Lock())
            with lk:
                if key not in cache:
                    cache[key] = fn(img_path, *args)
                return copy.deepcopy(cache[key])
        return wrapped
    pe._claude_vision_ocr = _memo(pe._claude_vision_ocr, 'vision')
    pe._read_nfp_panel = _memo(pe._read_nfp_panel, 'nfp_crop')
    return local


def _approved_record(expect, mutate=None):
    panel = {'serving_size_g': expect['serving_size_g'],
             'servings_per_container': expect['servings_per_container']}
    panel.update(mutate or {})
    return {'version': 1, 'status': 'approved', 'panel': panel, 'front_callouts': {}}


def _graded(res):
    return [(k, i) for k, c in (res.get('checks') or {}).items() if isinstance(c, dict)
            for i in c.get('issues', []) if i.get('severity') in ('critical', 'warning', 'suspect')]


def _criticals(res):
    return [(k, i) for k, i in _graded(res) if i['severity'] == 'critical']


def _run():
    why = _preflight()
    if why:
        return _skip_all(why)
    for k in ('READYDOC_URL', 'READYDOC_TOKEN', 'GTIN_SHEET_URL', 'MASTER_ALLOW_EXTERNAL'):
        os.environ.pop(k, None)
    os.environ['GTIN_SHEET_URL'] = MASTER_URL
    os.environ['READYDOC_URL'] = 'https://app.powder-ops.com'

    import app as A
    import proof_engine as pe
    A.GTIN_SHEET_CFG_PATH = os.path.join(tempfile.mkdtemp(), 'gtin_sheet_config.json')
    # The spot-colour check is off by default (DeviceCMYK separations on the
    # whey bottles cannot be compared honestly yet). These pancake files define
    # their separations in Lab, which IS comparable, so it is switched on here
    # — otherwise DEFECT_spot_color_off and the within-tolerance negative
    # control would test nothing. Trim stays at its default (disabled).
    pe.CHECK_SPOT_COLOR = True

    vision = bool(pe.ANTHROPIC_AVAILABLE)
    S = Scorer(vision)
    if not vision:
        print('[skip] vision unavailable (ANTHROPIC_API_KEY not set / anthropic not installed) — '
              'panel-read assertions will be reported individually as SKIP')

    with open(os.path.join(FIX, 'expected.json')) as f:
        expected = {e['file']: e for e in json.load(f)['files']}
    with open(os.path.join(FIX, 'defects.json')) as f:
        defects = json.load(f)['defects']
    by_sku = {e['sku']: e for e in expected.values()}

    local = _install_engine_stubs(pe)
    work = tempfile.mkdtemp(prefix='artproof_e2e_')

    # ── Build every scenario's inputs (reference data only) ────────────────
    base_rows, base_attempts = _fetch_master(A, _master_csv())
    S.check('master list loads from master_rows.csv through the production fetch path',
            len(base_rows) == 4 and len(base_attempts) == 1)

    jobs = []   # (scenario_id, file, rows, approved)
    for fname, e in expected.items():
        jobs.append(('CLEAN', fname, base_rows, (_approved_record(e['expect']), 'ok')))
    unavailable = None
    for d in defects:
        if d['sku'] == 'ALL':
            unavailable = d
            continue
        e = by_sku[d['sku']]
        rows, _ = _fetch_master(A, _master_csv({d['sku']: d['mutate'].get('master_row', {})}))
        appr = (_approved_record(e['expect'], d['mutate'].get('approved_panel')), 'ok')
        jobs.append((d['id'], e['file'], rows, appr))

    un_rows, un_attempts = _fetch_master(
        A, fail_with=urllib.error.HTTPError(MASTER_URL, 502, 'Bad Gateway', None, None))
    if unavailable:
        S.check('DEFECT_master_list_unavailable: the fetch retries 3 times, then yields no rows',
                un_rows == [] and len(un_attempts) == A._MASTER_FETCH_ATTEMPTS)
        blocker = A._master_list_blocker(un_rows, used_upload=False)
        S.check('DEFECT_master_list_unavailable: /upload aborts the run instead of emitting verdicts',
                bool(blocker) and 'aborted' in blocker and '502' in blocker)
        for fname in expected:
            jobs.append((unavailable['id'], fname, un_rows, (None, 'not_found')))

    # ── Run the real pipeline ────────────────────────────────────────────────
    # One run peaks around 3.7 GB (a 920x710mm sheet rasterised at 400 DPI is
    # 162 MP), so default to 2 concurrent runs; ARTPROOF_E2E_WORKERS overrides.
    workers = max(1, int(os.environ.get('ARTPROOF_E2E_WORKERS', '2')))
    def _one(job):
        sid, fname, rows, approved = job
        local.approved = approved
        t = time.time()
        res = pe._proof_single(os.path.join(FIX, fname), rows, work, brand_config=BRAND,
                               spec_rows=rows)
        return sid, fname, res, time.time() - t

    # Each run is printed the moment it finishes, so a timeout or a crash
    # part-way through a ~45-minute run still leaves every completed result.
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(_one, j) for j in jobs]):
            sid, fname, res, secs = fut.result()
            results.append((sid, fname, res, secs))
            print(f'  [{len(results)}/{len(jobs)}] {sid:<40} {fname:<50} '
                  f'{res.get("severity", "?").upper():<10} crit={res.get("critical_count")} '
                  f'warn={res.get("warning_count")} skipped={res.get("checks_skipped")} {secs:.0f}s',
                  flush=True)
            for k, i in _graded(res):
                print(f'      {i["severity"].upper():<8} [{k}] {i["message"][:150]}', flush=True)
    print(f'\n{len(results)} pipeline runs in {time.time() - t0:.0f}s\n')

    # ── Score: clean files ──────────────────────────────────────────────────
    clean_fp = 0
    clean_results = []
    for sid, fname, res, _ in results:
        if sid != 'CLEAN':
            continue
        clean_results.append(res)
        exp = expected[fname]['expect']
        vals, prov = res.get('panel_values') or {}, res.get('panel_provenance') or {}
        graded = _graded(res)
        clean_fp += len(graded)
        S.check(f'{fname}: zero CRITICAL / WARNING / SUSPECT findings (got {len(graded)})',
                not graded)
        S.check(f'{fname}: serving size {exp["serving_size_g"]:g}g, {exp["serving_size_cups"]:g} cup, '
                f'{exp["servings_per_container"]:g} servings (got {vals.get("serving_size_g")}, '
                f'{vals.get("serving_size_cups")}, {vals.get("servings_per_container")})',
                vals.get('serving_size_g') == exp['serving_size_g']
                and vals.get('serving_size_cups') == exp['serving_size_cups']
                and vals.get('servings_per_container') == exp['servings_per_container'],
                needs_vision=True)
        S.check(f'{fname}: serving size description reads "3/4 Cup" (got {vals.get("serving_size_desc")!r})',
                str(vals.get('serving_size_desc') or '').lower().startswith('3/4 cup'),
                needs_vision=True)
        S.check(f'{fname}: declared net weight {exp["declared_net_weight_g"]:g}g',
                vals.get('declared_net_weight_g') == exp['declared_net_weight_g'])
        S.check(f'{fname}: trim listed as disabled by configuration, not skipped '
                f'(disabled={res.get("checks_disabled")}, skipped={res.get("checks_skipped")})',
                res.get('checks_disabled') == ['trim_dimension']
                and 'trim_dimension' not in (res.get('checks_skipped') or []))
        S.check(f'{fname}: every nutrition-panel field sourced from nfp_crop (got {[prov.get(k) for k in NFP_FIELDS]})',
                all(prov.get(k) == 'nfp_crop' for k in NFP_FIELDS), needs_vision=True)
        S.check(f'{fname}: CLEAN, with every check run (got {res.get("severity")}, '
                f'skipped {res.get("checks_skipped")})',
                res.get('severity') == 'clean' and not res.get('checks_skipped'), needs_vision=True)

    # ── Score: defects ──────────────────────────────────────────────────────
    planted = caught = 0
    negative_ok = None
    for d in defects:
        runs = [r for r in results if r[0] == d['id']]
        exp = d['expect']
        if d['id'] == (unavailable or {}).get('id'):
            for _, fname, res, _ in runs:
                vals = res.get('panel_values') or {}
                want = expected[fname]['expect']
                S.check(f'{d["id"]} {fname}: not CLEAN (got {res.get("severity")})',
                        res.get('severity') != 'clean')
                S.check(f'{d["id"]} {fname}: INCOMPLETE (got {res.get("severity")})',
                        res.get('severity') == exp['overall_status'].lower(), needs_vision=True)
                S.check(f'{d["id"]} {fname}: serving size still reads {want["serving_size_g"]:g}g / '
                        f'{want["servings_per_container"]:g} with no master list (got '
                        f'{vals.get("serving_size_g")} / {vals.get("servings_per_container")})',
                        vals.get('serving_size_g') == want['serving_size_g']
                        and vals.get('servings_per_container') == want['servings_per_container'],
                        needs_vision=True)
            continue
        (_, fname, res, _), = runs
        crits = _criticals(res)
        vision_dep = exp.get('check') in ('approved_panel', 'fda_falcpa')
        if exp['critical_count'] == 0:
            g = _graded(res)
            negative_ok = not g
            S.check(f'{d["id"]}: NEGATIVE CONTROL — zero findings (got {[(k, i["message"][:60]) for k, i in g]})',
                    not g)
            continue
        planted += 1
        key = CHECK_KEY[exp['check']]
        hit = (len(crits) == 1 and crits[0][0] == key
               and exp['message_contains'].lower() in crits[0][1]['message'].lower())
        if hit:
            caught += 1
        S.check(f'{d["id"]}: exactly one CRITICAL, on check={exp["check"]}, containing '
                f'"{exp["message_contains"]}" (got {[(k, i["message"][:70]) for k, i in crits]})',
                hit, needs_vision=vision_dep)

    # ── Reader Provenance sheet in the real workbook ─────────────────────────
    import openpyxl
    job = {'id': 'e2e', 'created': time.strftime('%Y-%m-%dT%H:%M:%S'), 'dismissals': {},
           'summary': pe._build_summary(clean_results), 'results': clean_results}
    wb = openpyxl.load_workbook(io.BytesIO(A._generate_report(job).getvalue()))
    S.check('workbook carries a Reader Provenance sheet', 'Reader Provenance' in wb.sheetnames)
    if 'Reader Provenance' in wb.sheetnames:
        ws = wb['Reader Provenance']
        hdr = {ws.cell(row=4, column=c).value: c for c in range(1, ws.max_column + 1)}
        cells = [str(ws.cell(row=r, column=hdr[f]).value)
                 for r in range(5, 5 + len(clean_results)) for f in NFP_FIELDS if f in hdr]
        S.check('Reader Provenance sheet shows nfp_crop for every nutrition-panel field on all four files',
                len(cells) == 4 * len(NFP_FIELDS) and all('nfp_crop' in c for c in cells),
                needs_vision=True)

    # ── Score line ───────────────────────────────────────────────────────────
    print()
    print(f'SCORE: defects caught {caught} of {planted} planted; negative control '
          f'{"PASS" if negative_ok else "FAIL" if negative_ok is False else "n/a"}; '
          f'false positives on clean files: {clean_fp}'
          + ('' if vision else '   [vision unavailable — panel-read assertions skipped]'))
    print(f'{S.passes} passed, {len(S.fails)} failed, {len(S.skips)} skipped')
    if S.fails:
        print('FAILURES:', *S.fails, sep='\n  - ')
        return 1
    if S.skips and REQUIRED:
        print('ARTPROOF_E2E_REQUIRED is set and assertions were skipped.')
        return 1
    print('End-to-end run passes' + (' (with skips).' if S.skips else '.'))
    return 0


if __name__ == '__main__':
    sys.exit(_run())
