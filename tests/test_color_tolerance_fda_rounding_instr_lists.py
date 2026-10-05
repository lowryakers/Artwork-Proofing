"""Three fixes from run b172bb64:

1. The color tolerance didn't land because the master list's hex_spot_colors
   values carry a literal "HEX " prefix ("HEX EE7623"), which _hex_channels
   couldn't parse at all -- every comparison silently failed regardless of
   tolerance width. Fixed, plus a proper tiered tolerance (<=8 pass, 9-20
   WARNING, >20 CRITICAL) replacing the old binary match/no-match for a
   computed (Lab/CMYK-derived) hex.
2. A servings-per-container figure is itself rounded per 21 CFR 101.9(b)(8)
   ("About N servings"); net ÷ serving routinely does not land on that
   rounded number even on a correctly filled, correctly labeled pack. That
   must be treated as a PASS, not a suspect read or a finding.
3. Two independent numbered instruction lists (pancake + waffle), each
   correctly numbered 1..N, were read as one damaged [1,2,3,1,2,3] list
   whenever something sat between them that _INSTR_HEADER didn't recognize
   as a header -- an intervening non-step line was erasing the "inside a
   numbered list" state the restart-at-1 list-boundary detection depends on.

    python3 tests/test_color_tolerance_fda_rounding_instr_lists.py

Exits non-zero on any failure. No test framework, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402


class _StubDoc:
    def __init__(self, objects):
        self._objects = objects

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

    # ── Item 1a: the literal "HEX " prefix parsing bug ──────────────────────
    check('"HEX EE7623" parses the same as "EE7623"',
          pe._hex_channels('HEX EE7623') == pe._hex_channels('EE7623'))
    check('"hex ee7623" (lowercase) also parses', pe._hex_channels('hex ee7623') == (0xEE, 0x76, 0x23))
    check('a bare hex with no prefix still parses', pe._hex_channels('#EE7623') == (0xEE, 0x76, 0x23))
    check('_hex_close now matches a "HEX "-prefixed spec value against a computed hex',
          pe._hex_close('HEX EE7623', '#ED7723', tol=8))

    # ── Item 1b: tiered tolerance on the computed-hex delta ─────────────────
    check('_hex_channel_delta computes the max per-channel delta',
          pe._hex_channel_delta('#A45A30', '#A55B2A') == 6)
    check('_hex_channel_delta returns None for an unparseable input',
          pe._hex_channel_delta('not a color', '#A55B2A') is None)

    # All twelve pairs from the b172bb64 run must be within delta 8 (pass tier).
    pairs = [
        ('A6483E', 'A74840'), ('FFA16A', 'FFA16E'), ('EEE4D0', 'EFE2CE'),
        ('4F2C1D', '502C1F'), ('A55B2A', 'A45A30'), ('F3D0B3', 'F2CEB4'),
        ('00ADD8', '00ACD8'), ('94DAE9', '96DAEA'), ('F9EBDA', 'F8EADA'),
        ('ED7723', 'EE7623'), ('C35131', 'C25131'), ('EFDBB4', 'F2DAB2'),
    ]
    for computed, spec in pairs:
        delta = pe._hex_channel_delta(spec, computed)
        check(f'{computed} vs {spec}: delta {delta} is within the pass tier (<=8)',
              delta is not None and delta <= 8)

    # A genuinely different color (delta > 20) must still be CRITICAL-tier.
    check('a genuinely wrong color (delta > 20) is in the critical tier',
          pe._hex_channel_delta('#112233', '#EE7623') > 20)
    # A middling delta (9-20) is the WARNING tier.
    mid_delta = pe._hex_channel_delta('#EE7623', '#E06A2A')
    check('a middling delta lands in the 9-20 WARNING band', 9 <= mid_delta <= 20)

    # ── Item 1, end-to-end through _extract_spot_color_hexes + a "HEX "-
    #    prefixed spec value (the exact real-world shape) ───────────────────
    lab_obj = (
        '[ /Separation /PANTONE#20158#20C 69 0 R <<\n'
        '    /C0 [ 100 0 0 ]\n'
        '    /C1 [ 63.5294 43 63 ]\n'
        '    /Range [ 0 100 -128 127 -128 127 ]\n'
        '    /FunctionType 2 /N 1 /Domain [ 0 1 ] >> ]'
    )
    doc = _StubDoc([lab_obj])
    hexes = pe._extract_spot_color_hexes(doc)
    computed_hex = hexes['PANTONE#20158#20C']['hex']
    req_hex = 'HEX EE7623'  # the literal master-list cell format from the bug report
    delta = pe._hex_channel_delta(req_hex, computed_hex)
    check('end-to-end: computed Lab hex vs a "HEX "-prefixed spec value resolves a real, small delta',
          delta is not None and delta <= 8)

    # ── Item 2: FDA servings-per-container rounding (21 CFR 101.9(b)(8)) ────
    check('_fda_round_servings rounds to nearest 0.5 in the 2-5 range',
          pe._fda_round_servings(4.63) == 4.5)
    check('_fda_round_servings rounds to nearest whole above 5',
          pe._fda_round_servings(5.47) == 5.0)
    check('_fda_round_servings handles None/zero gracefully',
          pe._fda_round_servings(None) is None and pe._fda_round_servings(0) is None)

    # The exact chocolate scenario: net 454g / serving 83g = 5.47 -> rounds to
    # declared "About 5". Must be PASS, not a finding.
    r = pe._check_net_weight(
        {'serving_size_g': 83, 'servings_per_container': 5, 'declared_net_weight_g': 454},
        fill_weight_g=454)
    check('FDA-rounding-explained shortfall is PASS', r['status'] == 'PASS')
    check('no CRITICAL/SUSPECT/REVIEW issues on an FDA-rounding-explained shortfall',
          all(i['severity'] not in ('critical', 'suspect', 'review') for i in r['issues']))
    check('a note cites 21 CFR 101.9(b)(8)', any('101.9(b)(8)' in n for n in r['notes']))
    check('the misleading "Reconciles: ...(within 5% of fill)" note does not appear',
          not any(n.startswith('Reconciles:') for n in r['notes']))

    # A genuine misread/error (not explained by FDA rounding) must still fire.
    # 98 x 7 = 686 vs fill 454: exact servings = 454/98 = 4.63 -> rounds to 4.5,
    # not the declared 7 -- rounding does NOT explain this one.
    r2 = pe._check_net_weight(
        {'serving_size_g': 98, 'servings_per_container': 7, 'declared_net_weight_g': 686},
        fill_weight_g=454)
    check('a genuine mismatch NOT explained by FDA rounding still fires CRITICAL',
          r2['status'] == 'CRITICAL')

    # A clean, already-within-tolerance reconciliation still reports normally.
    r3 = pe._check_net_weight(
        {'serving_size_g': 98, 'servings_per_container': 4.5, 'declared_net_weight_g': 454},
        fill_weight_g=454)
    check('a clean PASS (already within 5%) is unaffected and still shows "Reconciles"',
          r3['status'] == 'PASS' and any('reconciles' in n.lower() for n in r3['notes']))

    # ── Item 3: two numbered lists must not merge into one damaged list ─────
    # A header _INSTR_HEADER does NOT recognize (no "instructions"/"directions"/
    # "to prepare" keyword) sits between the two lists -- this used to erase
    # the "inside a numbered list" state the restart-at-1 split depends on.
    text = (
        'PANCAKES\n'
        '1. Mix batter\n'
        '2. Heat griddle\n'
        '3. Pour batter\n'
        'WAFFLES\n'
        '1. Mix batter\n'
        '2. Set waffle maker\n'
        '3. Pour into waffle maker'
    )
    check('two correctly-numbered lists separated by an unrecognized header -> no false positive',
          pe._check_instruction_steps(text) == [])

    # The pre-existing recognized-header case must still work (no regression).
    text2 = (
        'PANCAKE INSTRUCTIONS\n1. Mix batter\n2. Heat griddle\n3. Pour batter\n'
        'WAFFLE INSTRUCTIONS\n1. Mix batter\n2. Set waffle maker\n3. Pour into waffle maker'
    )
    check('two lists with a recognized header still segment correctly',
          pe._check_instruction_steps(text2) == [])

    # A genuinely damaged list -- a repeated step number or an out-of-sequence
    # read that is NOT a restart back to 1 -- must still be caught (restarting
    # at 1 is treated as a new, legitimate list by design; that ambiguity
    # with real damage is a separate, pre-existing tradeoff this fix does not
    # touch, so test the unambiguous damage shapes instead).
    repeated_step = 'Instructions\n1. Mix\n2. Heat\n2. Heat\n3. Pour'
    check('a repeated step number (no restart to 1) still flags',
          any('repeated' in i['message'].lower() for i in pe._check_instruction_steps(repeated_step)))
    out_of_seq = 'Instructions\n1. Mix\n3. Heat\n2. Pour'
    check('an out-of-sequence read (no restart to 1) still flags',
          any('out of sequence' in i['message'].lower() for i in pe._check_instruction_steps(out_of_seq)))

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All run-b172bb64 fixes pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
