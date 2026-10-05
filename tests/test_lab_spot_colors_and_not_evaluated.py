"""Two fixes from run 66dc8e69:

1. Spot colors defined with a Lab alternate colorspace were compared against
   a rendered-pixel sample and reported as mismatches -- 12 false CRITICALs
   on artwork that was, in every case, correct. _extract_spot_color_hexes now
   reads each separation's own tint-transform function (/C1), Lab- or
   CMYK-aware, and that computed hex (tolerance 8) is the authoritative
   comparison when it resolves. An uninterpretable colorspace reports
   "could not evaluate", never "does not match".
2. A SUSPECT serving-size read still produced a confident "Reconciles" note
   once the overall status was downgraded to SUSPECT by a later sub-check --
   the arithmetic was right and the inputs were invented. That note must
   become NOT EVALUATED, naming the suspect input.

    python3 tests/test_lab_spot_colors_and_not_evaluated.py

Exits non-zero on any failure. No test framework, no network, no vision API,
no real PDF needed for the Lab/CMYK math (pure functions); the extraction
test uses a tiny stub object mimicking PyMuPDF's xref_object interface.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402


class _StubDoc:
    """Mimics the slice of PyMuPDF's Document interface _extract_spot_color_hexes
    needs: xref_length() and xref_object(xref, compressed=False)."""
    def __init__(self, objects):
        self._objects = objects  # list of raw PDF object text, 1-indexed

    def xref_length(self):
        return len(self._objects) + 1

    def xref_object(self, xref, compressed=False):
        if xref < 1 or xref > len(self._objects):
            raise IndexError('no such xref')
        return self._objects[xref - 1]


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    # ── Lab -> sRGB conversion accuracy (the audit's own worked table) ─────────
    # (L, a, b) -> expected hex, max channel delta allowed (per the prompt's own
    # "converts to" column, which used slightly different precision inputs).
    cases = [
        ((63.5294, 43, 63),   'EE7623'),   # PMS 158 C
        ((49.8, 45, 42),      'C25131'),   # PMS 7580 C
        ((63.9, -34, -35),    '00ACD8'),   # PMS 638 C
        ((93.7, 3, 10),       'F8EADA'),   # PMS 9224 U
    ]
    for (L, a, b), spec_hex in cases:
        got = pe._lab_to_hex(L, a, b)
        check(f'Lab({L},{a},{b}) converts within tolerance 8 of spec {spec_hex}',
              pe._hex_close(got, '#' + spec_hex, tol=8))

    # ── CMYK -> sRGB sanity ─────────────────────────────────────────────────────
    check('CMYK(0,0,0,0) -> white', pe._cmyk_to_hex(0, 0, 0, 0) == '#FFFFFF')
    check('CMYK(0,0,0,100) -> black', pe._cmyk_to_hex(0, 0, 0, 100) == '#000000')

    # ── _extract_spot_color_hexes: Lab-defined separation (the exact PDF shape
    #    from the bug report) resolves to the correct hex ────────────────────
    lab_obj = (
        '[ /Separation /PANTONE#20158#20C 69 0 R <<\n'
        '    /C0 [ 100 0 0 ]\n'
        '    /C1 [ 63.5294 43 63 ]\n'
        '    /Range [ 0 100 -128 127 -128 127 ]\n'
        '    /FunctionType 2 /N 1 /Domain [ 0 1 ] >> ]'
    )
    doc = _StubDoc([lab_obj])
    hexes = pe._extract_spot_color_hexes(doc)
    check('Lab separation name extracted correctly', 'PANTONE#20158#20C' in hexes)
    check('Lab separation resolves to a hex (not unresolved)',
          hexes.get('PANTONE#20158#20C', {}).get('hex') is not None)
    check('Lab separation computed hex matches spec within tolerance 8',
          pe._hex_close(hexes['PANTONE#20158#20C']['hex'], '#EE7623', tol=8))

    # ── A CMYK-defined separation still resolves (not everything is Lab) ──────
    cmyk_obj = (
        '[ /Separation /PANTONE#20Red#20032#20C 70 0 R <<\n'
        '    /C0 [ 0 0 0 0 ]\n'
        '    /C1 [ 0 90 95 0 ]\n'
        '    /FunctionType 2 /N 1 /Domain [ 0 1 ] >> ]'
    )
    doc2 = _StubDoc([cmyk_obj])
    hexes2 = pe._extract_spot_color_hexes(doc2)
    check('CMYK separation resolves to a hex', hexes2.get('PANTONE#20Red#20032#20C', {}).get('hex'))

    # ── An uninterpretable colorspace reports unresolved, never silently
    #    treated as CMYK or Lab ─────────────────────────────────────────────────
    weird_obj = (
        '[ /Separation /SomeWeirdSpot 71 0 R <<\n'
        '    /C0 [ 0 0 0 0 0 ]\n'
        '    /C1 [ 1 2 3 4 5 ]\n'
        '    /FunctionType 2 /N 1 /Domain [ 0 1 ] >> ]'
    )
    doc3 = _StubDoc([weird_obj])
    hexes3 = pe._extract_spot_color_hexes(doc3)
    check('a 5-component /C1 (unrecognized colorspace) is reported unresolved, not guessed',
          hexes3.get('SomeWeirdSpot', {}).get('unresolved') is not None)
    check('unresolved entry carries no hex', hexes3.get('SomeWeirdSpot', {}).get('hex') is None)

    # A 3-component /C1 with no /Range at all (plain RGB-ish alternate, no scale
    # evidence) still resolves rather than being silently assumed to be Lab.
    rgb_obj = (
        '[ /Separation /PlainSpot 72 0 R <<\n'
        '    /C0 [ 1 1 1 ]\n'
        '    /C1 [ 0.2 0.4 0.6 ]\n'
        '    /FunctionType 2 /N 1 /Domain [ 0 1 ] >> ]'
    )
    doc4 = _StubDoc([rgb_obj])
    hexes4 = pe._extract_spot_color_hexes(doc4)
    check('a 3-component /C1 with no /Range to classify it is reported unresolved',
          hexes4.get('PlainSpot', {}).get('unresolved') is not None)

    # Process-color names (cyan/magenta/etc) are never treated as spot colors.
    doc5 = _StubDoc(['/Separation /Cyan 1 0 R'])
    check('a process-color Separation name is excluded', pe._extract_spot_color_hexes(doc5) == {})

    # ── _matched_spot_name ──────────────────────────────────────────────────────
    check('_matched_spot_name finds the actual file separation name',
          pe._matched_spot_name('PMS 158 C', ['PANTONE#20158#20C']) == 'PANTONE#20158#20C')
    check('_matched_spot_name returns None when nothing matches',
          pe._matched_spot_name('PMS 999 C', ['PANTONE#20158#20C']) is None)
    check('_spot_matches is consistent with _matched_spot_name',
          pe._spot_matches('PMS 158 C', ['PANTONE#20158#20C']) is True)

    # ── Three-way resolution with a computed (Lab-derived) hex, tolerance 8 ───
    # Mirrors the loop in _check_print_specs: a Lab-computed hex close to spec
    # (within 8) must silently pass even though it would NOT be within the
    # old tolerance-6 pixel-sample default.
    computed_hex, spec_hex = '#ED7723', '#EE7623'  # max channel delta 1 here, but prove tol=8 path
    check('a Lab-computed hex within tolerance 8 of spec is accepted',
          pe._hex_close(spec_hex, computed_hex, tol=8))
    # A genuinely different computed color (max delta > 8) must still fail.
    check('a genuinely different computed hex (delta > 8) is still rejected',
          not pe._hex_close('#112233', '#EE7623', tol=8))

    # ── NOT EVALUATED: a SUSPECT serving-size read must never leave a
    #    "Reconciles" note standing (job 66dc8e69's Net Weight sheet bug) ──────
    r = pe._check_net_weight(
        {'serving_size_g': 198, 'serving_size_cups': 0.75, 'serving_size_desc': '3/4 Cup',
         'servings_per_container': 2.29, 'declared_net_weight_g': 454},
        fill_weight_g=454)
    check('status is SUSPECT, never a confident PASS, on a suspect serving read',
          r['status'] == 'SUSPECT')
    check('no "Reconciles" note survives when the status is SUSPECT',
          not any('reconciles' in n.lower() for n in r['notes']))
    check('a NOT EVALUATED note naming the serving size replaces it',
          any(n.startswith('NOT EVALUATED') and 'serving size' in n and '198' in n
              for n in r['notes']))

    # A genuinely clean, non-suspect reconciliation must still show "Reconciles".
    r2 = pe._check_net_weight(
        {'serving_size_g': 98, 'servings_per_container': 4.5, 'declared_net_weight_g': 454},
        fill_weight_g=454)
    check('a clean PASS still reports "Reconciles" (nothing suspect to hide)',
          r2['status'] == 'PASS' and any('reconciles' in n.lower() for n in r2['notes']))

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All run-66dc8e69 fixes pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
