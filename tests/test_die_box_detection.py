"""Die-box detection from a named PDF vector layer (_extract_layer_rects),
and _check_print_specs's use of it to avoid a false dimension-mismatch on a
converter layout whose page/TrimBox is an oversized artboard.

    python3 tests/test_die_box_detection.py

Exits non-zero on any failure. Needs PyMuPDF (a declared production
dependency — see requirements.txt); skips gracefully if it isn't installed
in this environment, same as _check_print_specs itself does.

Ground truth: fixtures/dies/prodough_bottle_shrink_sleeve_litho_flexo_95LF_
6.625inCL_2026-09-24_converter_layout.pdf is the actual vendor converter
layout for die v2. Its page (321.051 x 250.0 mm) includes dimension callouts
and a vendor legend — it is NOT the trim. The real print area (192 x
164.275 mm) and slit box (197 x 168.275 mm) exist only as vector rectangles
on the file's "Dieline" OCG layer, cross-checked against the file's own
live-text legend (see docs/bottle-shrink-sleeve-die.md for the full
derivation of how the SIDE/FRONT/SIDE/BACK panel folds were read off this
same layer).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe    # noqa: E402
import die_templates as dt   # noqa: E402

_FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fixtures', 'dies',
    'prodough_bottle_shrink_sleeve_litho_flexo_95LF_6.625inCL_2026-09-24_converter_layout.pdf')


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    try:
        import fitz  # noqa: F401
    except ImportError:
        print('[skip] die-box detection tests (pymupdf not installed in this environment)')
        return 0

    if not os.path.exists(_FIXTURE):
        check('converter-layout fixture is committed', False)
        print()
        print('FAILURES:\n  - converter-layout fixture is committed')
        return 1

    # ── _extract_layer_rects finds the real geometry, not the artboard ────────
    rects = pe._extract_layer_rects(_FIXTURE, 'Dieline')
    check('Dieline layer yields multiple rectangle candidates', len(rects) >= 3)

    def _has(w, h, tol=0.05):
        return any(abs((x1 - x0) - w) <= tol and abs((y1 - y0) - h) <= tol
                  for x0, y0, x1, y1 in rects)

    check('print-area box (192 x 164.275 mm) found on the Dieline layer', _has(192.0, 164.275))
    check('slit box (197 x 168.275 mm) found on the Dieline layer', _has(197.0, 168.275))
    check('layflat box (95 mm wide) found on the Dieline layer', _has(95.0, 168.275))
    # The full artboard/page frame is NOT what we want treated as the die box —
    # confirm detection doesn't collapse to just that one giant rectangle.
    check('the oversized artboard frame (321 x 250mm) is present but not the ONLY candidate',
          _has(321.05, 250.0, tol=0.1) and len(rects) > 1)

    # ── Degrades to [] gracefully — never raises ───────────────────────────────
    check('nonexistent file -> [] (no crash)',
          pe._extract_layer_rects('/no/such/file.pdf', 'Dieline') == [])
    check('layer name that does not exist -> [] (no crash)',
          pe._extract_layer_rects(_FIXTURE, 'NoSuchLayer') == [])

    # ── _check_print_specs end-to-end: the die box overrides the artboard ─────
    tpl = dt.get('prodough_bottle_shrink_sleeve')
    result = pe._check_print_specs(_FIXTURE, brand_config={'proof_type': 'press'},
                                   matched_spec={}, template=tpl)
    check('no dimension-mismatch CRITICAL on the oversized converter-layout artboard',
          not any(i['severity'] == 'critical' and 'dimension' in i['message'].lower()
                 for i in result['issues']))
    check('a note explains the die box was read from the Dieline layer, not the page',
          any('die box' in n.lower() and 'dieline' in n.lower() for n in result['notes']))
    check('a note confirms the dimensions match the locked die',
          any('match' in n.lower() and '192' in n for n in result['notes']))
    check('die line (Die/Die2 separations) detected — required and present',
          not any(i['severity'] == 'critical' and 'die line' in i['message'].lower()
                 for i in result['issues']))

    # ── Without a die_box_locator, behavior is unchanged (regression safety) ──
    # No template at all: no spec dims to compare against, so no dimension
    # issue fires either way — confirms the die-box path is not silently
    # invoked without an explicit locator.
    result_no_tpl = pe._check_print_specs(_FIXTURE, brand_config={'proof_type': 'press'},
                                          matched_spec={}, template=None)
    check('no template -> no die-box note (old behavior, unaffected)',
          not any('die box' in n.lower() for n in result_no_tpl['notes']))

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All die-box detection checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
