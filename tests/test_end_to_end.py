"""End-to-end: real artwork PDFs through the REAL pipeline.

Every other test in this suite bypasses extraction — it hands a check a value
that was already extracted and tests arithmetic that was never wrong. Every
production failure has been in extraction. This harness runs
proof_engine._proof_single over the PDFs in tests/fixtures/artwork/ exactly as
/upload would, with the master list stubbed from a local JSON so a network
failure can neither turn it green nor red, and ReadyDoc disabled.

    python3 tests/test_end_to_end.py
    ARTPROOF_E2E_REQUIRED=1 python3 tests/test_end_to_end.py   # CI: skips become failures

Scores the run: defects caught out of defects planted, and false positives on
the clean files. That number is the only honest measure of whether the tool
works. Skips loudly (exit 0, or 1 with ARTPROOF_E2E_REQUIRED) when the fixture
PDFs, the PDF/OCR toolchain, or the vision API key are absent — it never
pretends to have run.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

FIXTURE_DIR = os.path.join(HERE, 'fixtures', 'artwork')
MANIFEST = os.path.join(FIXTURE_DIR, 'manifest.json')
MASTER = os.path.join(FIXTURE_DIR, 'master_list.json')

# manifest check names -> proof_engine check keys
CHECK_KEY = {'gtin': 'gtin', 'net_weight': 'netwt', 'spot_color': 'specs',
             'fda_falcpa': 'fda', 'approved_panel': 'panel', 'nfp': 'nfp', 'prep': 'prep'}


def _skip(reason: str) -> int:
    required = bool(os.environ.get('ARTPROOF_E2E_REQUIRED'))
    print(f'[{"FAIL" if required else "skip"}] end-to-end: {reason}')
    return 1 if required else 0


def _preflight(entries):
    present = [e for e in entries if os.path.exists(os.path.join(FIXTURE_DIR, e['file']))]
    if not present:
        return None, ('no artwork fixtures present in tests/fixtures/artwork/ — see the README '
                      'there for the nine PDFs to drop in')
    missing_tools = [t for t in ('pdftoppm', 'tesseract') if not shutil.which(t)]
    if missing_tools:
        return None, f'PDF/OCR toolchain missing: {", ".join(missing_tools)}'
    try:
        import fitz  # noqa: F401
    except ImportError:
        return None, 'PyMuPDF (fitz) not installed'
    if not os.environ.get('ANTHROPIC_API_KEY'):
        return None, ('ANTHROPIC_API_KEY not set — the panel crop reader needs it, and without '
                      'it every panel field is absent by design (NOT RUN), so expected serving '
                      'sizes cannot be met')
    with open(MASTER) as f:
        master = json.load(f)
    rows = master.get('rows') or []
    if any('FILL_ME' in str(v) for r in rows for v in r.values()):
        return None, 'master_list.json still contains FILL_ME placeholders — fill them from master.csv'
    return (present, rows), None


def _run():
    with open(MANIFEST) as f:
        entries = json.load(f)['fixtures']
    ready, why = _preflight(entries)
    if ready is None:
        return _skip(why)
    present, rows = ready

    # Never let ReadyDoc (or anything else on the network) into this run.
    for k in ('READYDOC_URL', 'READYDOC_TOKEN', 'GTIN_SHEET_URL'):
        os.environ.pop(k, None)
    import proof_engine as pe

    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    work_dir = tempfile.mkdtemp(prefix='artproof_e2e_')
    brand = {'brand_mode': 'prodough', 'proof_type': 'press', 'wind_direction': ''}
    planted = caught = 0
    clean_fp = 0
    for e in present:
        path = os.path.join(FIXTURE_DIR, e['file'])
        res = pe._proof_single(path, rows, work_dir, brand_config=brand, spec_rows=rows)
        checks = res.get('checks') or {}
        exp = e['expected']
        label = e['file']
        prov = res.get('panel_provenance') or {}
        vals = res.get('panel_values') or {}
        print(f'    {label}: severity={res.get("severity")} run={res.get("checks_run")} '
              f'skipped={res.get("checks_skipped")} panel={vals} provenance={prov}')

        if e['kind'] == 'clean':
            check(f'{label}: serving size read {exp["serving_size_g"]}g from the panel crop',
                  vals.get('serving_size_g') == exp['serving_size_g']
                  and prov.get('serving_size_g') == 'nfp_crop')
            check(f'{label}: servings per container read {exp["servings_per_container"]}',
                  vals.get('servings_per_container') == exp['servings_per_container'])
            graded = [i for c in checks.values() if isinstance(c, dict)
                      for i in c.get('issues', []) if i.get('severity') in ('critical', 'warning', 'suspect')]
            clean_fp += len(graded)
            check(f'{label}: {exp["findings"]} findings (got {len(graded)})', len(graded) == exp['findings'])
            check(f'{label}: not reported CLEAN unless every check ran',
                  res.get('severity') != 'clean' or not res.get('checks_skipped'))
        else:
            planted += 1
            key = CHECK_KEY[exp['check']]
            hit = [i for i in (checks.get(key) or {}).get('issues', []) if i.get('severity') == 'critical']
            if hit:
                caught += 1
            check(f'{label}: CRITICAL raised by check={exp["check"]}', bool(hit))
            others = [(k, i['message'][:70]) for k, c in checks.items() if k != key and isinstance(c, dict)
                      for i in c.get('issues', []) if i.get('severity') == 'critical']
            check(f'{label}: no CRITICAL from any other check (got {others})', not others)

    print()
    print(f'SCORE: defects caught {caught} of {planted} planted; '
          f'false positives on clean files: {clean_fp}')
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('End-to-end run passes.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
