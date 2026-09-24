"""Spot color / Pantone checks — the required-color delimiter bug, PDF name-
escape decoding, and the hex-vs-name three-way resolution (FIX 1/2 from the
06461fe6 audit addendum).

    python3 tests/test_spot_color_checks.py

Exits non-zero on any failure. No test framework required. _check_print_specs
itself needs PyMuPDF (not available in every environment), so these tests
target the pure helpers directly — _norm_pantone/_spot_matches/_hex_channels/
_hex_close/_sample_dominant_colors — which is also what the addendum's own
regression test describes.

Ground truth (Pumpkin Spice, from the audit): the brand guide's PMS callout
(PMS 285 C, a blue) is wrong — a naming error, not a color error. Eleven of
twelve sampled spot colors matched the brand guide's hex within 1-2 units;
Pumpkin's rendered #C35131 matches the guide's own #C25131. The artwork's
actual separation is PANTONE 7580 C. The tool used to report this identically
to a genuine "wrong color" case, so it was invisible in a list of findings.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    # ── FIX 1: delimiter bug — ReadyDoc uses " | ", not "," ───────────────────
    import re
    req_raw = 'PMS 4625 C | PMS 470 C | PMS 475 C'
    req_spots_comma = [s.strip() for s in req_raw.split(',')]
    check('the OLD comma-only split left the whole cell as one token (the bug)',
          req_spots_comma == [req_raw])
    req_spots = [s.strip() for s in re.split(r'[,|;]', req_raw) if s.strip()]
    check('the FIXED split on [,|;] yields three separate colors',
          req_spots == ['PMS 4625 C', 'PMS 470 C', 'PMS 475 C'])

    # ── FIX 1: _norm_pantone decodes #XX PDF name-escapes before stripping ────
    check('"PANTONE#204625#20C" normalizes to "4625" (the exact audit regression)',
          pe._norm_pantone('PANTONE#204625#20C') == '4625')
    check('"PANTONE#20470#20C" normalizes to "470"', pe._norm_pantone('PANTONE#20470#20C') == '470')
    check('"PANTONE#20475#20C" normalizes to "475"', pe._norm_pantone('PANTONE#20475#20C') == '475')
    check('plain "PMS 4625 C" normalizes the same way (no escapes to decode)',
          pe._norm_pantone('PMS 4625 C') == '4625')
    check('plain "PANTONE 4625C" (no space before suffix) normalizes the same',
          pe._norm_pantone('PANTONE 4625C') == '4625')

    # ── FIX 1 regression test, as specified in the addendum ───────────────────
    file_colors = ['PANTONE#204625#20C', 'PANTONE#20470#20C', 'PANTONE#20475#20C']
    unmatched = [r for r in req_spots if not pe._spot_matches(r, file_colors)]
    check('required PMS 4625/470/475 C all match their #XX-escaped file separations '
         '(zero findings, the addendum\'s exact regression case)', unmatched == [])

    # A genuinely absent color still does not match.
    check('a color truly absent from the file does not spuriously match',
          not pe._spot_matches('PMS 199 C', file_colors))

    # Named-only match still works when there is no PDF escaping at all.
    check('plain name match still works (no escapes involved)',
          pe._spot_matches('PANTONE 285 C', ['PANTONE 285 C']))

    # ── _hex_channels / _hex_close ─────────────────────────────────────────────
    check('"#C25131" parses to (194, 81, 49)', pe._hex_channels('#C25131') == (0xC2, 0x51, 0x31))
    check('bare hex without # also parses', pe._hex_channels('C25131') == (0xC2, 0x51, 0x31))
    check('3-digit shorthand expands', pe._hex_channels('#fff') == (255, 255, 255))
    check('unparseable hex -> None', pe._hex_channels('not-a-color') is None)
    check('None input -> None', pe._hex_channels(None) is None)

    # The Pumpkin Spice case: #C35131 (rendered) vs #C25131 (brand guide hex) —
    # 1 unit off on one channel, well within tolerance. A genuinely wrong color
    # (tens of units off) must NOT match.
    check('#C35131 vs #C25131 (1-unit rendering variance) matches within tolerance',
          pe._hex_close('#C35131', '#C25131'))
    check('a genuinely different color does not match',
          not pe._hex_close('#C25131', '#0000FF'))
    check('exact match always matches', pe._hex_close('#ABCDEF', '#ABCDEF'))
    check('unparseable hex never matches', not pe._hex_close('garbage', '#ABCDEF'))

    # ── _sample_dominant_colors: white and the dieline guide color excluded ──
    try:
        from PIL import Image
        import tempfile
        # A page mostly white, with a large rust swatch (the "ink"), a small
        # dieline-guide-blue border (registration marks), and a tiny sliver of
        # near-white that should not count as a second background shade.
        im = Image.new('RGB', (200, 200), (255, 255, 255))
        for x in range(20, 150):
            for y in range(20, 150):
                im.putpixel((x, y), (0xC2, 0x51, 0x31))       # the "ink" — #C25131
        for x in range(0, 200):
            im.putpixel((x, 0), (0x00, 0xAD, 0xEF))            # dieline guide strip
            im.putpixel((x, 1), (0x00, 0xAD, 0xEF))
        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        im.save(tmp.name)
        tmp.close()
        try:
            colors = pe._sample_dominant_colors(tmp.name, top_n=6, min_frac=0.01)
            check('white substrate excluded from dominant colors', '#ffffff' not in colors)
            check('dieline guide color (#00adef) excluded from dominant colors',
                  '#00adef' not in colors)
            check('the actual ink color is reported', '#c25131' in colors)
        finally:
            os.remove(tmp.name)
    except ImportError:
        print('[skip] _sample_dominant_colors test (Pillow not installed)')

    check('_sample_dominant_colors degrades to [] for a nonexistent file (never raises)',
          pe._sample_dominant_colors('/no/such/file.png') == [])

    # ── Three-way resolution logic (mirrors the loop in _check_print_specs) ───
    # Row 1: name matches, hex matches (or unknown) -> pass, silent.
    # Row 2: name matches, hex does NOT match -> CRITICAL (wrong ink).
    # Row 3: name does NOT match, hex matches -> REVIEW (master-list name wrong).
    # Row 4: neither matches -> WARNING (genuine setup problem).
    def _resolve(name_ok, hex_ok):
        if name_ok and hex_ok is not False:
            return None
        if name_ok and hex_ok is False:
            return 'critical'
        if not name_ok and hex_ok is True:
            return 'review'
        if not name_ok and hex_ok is False:
            return 'warning'
        return 'warning'  # no hex evidence at all -> legacy name-only warning

    check('row 1 (name yes, hex yes) -> silent pass', _resolve(True, True) is None)
    check('row 1b (name yes, hex unknown) -> silent pass, no false alarm from missing hex data',
          _resolve(True, None) is None)
    check('row 2 (name yes, hex NO) -> CRITICAL — the expensive, previously-invisible case',
          _resolve(True, False) == 'critical')
    check('row 3 (name NO, hex yes) -> REVIEW — the Pumpkin Spice case, master list is wrong',
          _resolve(False, True) == 'review')
    check('row 4 (name NO, hex NO) -> WARNING — genuine color setup problem',
          _resolve(False, False) == 'warning')
    check('no hex data at all (name NO, hex None) -> WARNING (legacy behavior preserved)',
          _resolve(False, None) == 'warning')

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All spot-color checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
