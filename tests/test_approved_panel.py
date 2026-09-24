"""Approved nutrition panel (ReadyDoc source of truth) — field-level diff,
status model (PANEL_MISSING / PANEL_NOT_APPROVED / VERIFIED / CRITICAL /
PANEL_SUPERSEDED), and the vision gate forcing a reliable read when a panel
is on file to compare against.

    python3 tests/test_approved_panel.py

Exits non-zero on any failure. No test framework required.

Why this check exists: on a 38-SKU bottle run, hand-transcribed panels
produced 13 SKUs with errors; only 5 were visible to internal arithmetic. The
other 8 were found by diffing against the source label by hand. This check
replaces "by hand" with an automated diff against the panel of record.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402


# A fully-specified approved panel matching PROMPT_2 Part A's example shape,
# reused across cases so each test only overrides what it's exercising.
_APPROVED_PANEL = {
    'serving_size_desc': '1 Bottle', 'serving_size_g': 35, 'servings_per_container': 1,
    'calories': 120,
    'total_fat_g': 0.5, 'total_fat_dv': 1,
    'saturated_fat_g': 0, 'saturated_fat_dv': 0,
    'trans_fat_g': 0,
    'cholesterol_mg': '<5', 'cholesterol_dv': 1,
    'sodium_mg': 260, 'sodium_dv': 11,
    'total_carbohydrate_g': 4, 'total_carbohydrate_dv': 1,
    'dietary_fiber_g': '<1', 'dietary_fiber_dv': 2,
    'total_sugars_g': 2,
    'added_sugars_g': 0, 'added_sugars_dv': 0,
    'protein_g': 25,
    'vitamin_d_mcg': 0, 'vitamin_d_dv': 0,
    'calcium_mg': 150, 'calcium_dv': 10,
    'iron_mg': 0.3, 'iron_dv': 0,
    'potassium_mg': 40, 'potassium_dv': 0,
    'ingredients': 'Whey Protein Isolate, Non-Fat Milk Powder.',
    'allergen_statement': 'Contains: Milk',
    'net_weight_g': 35,
}
_APPROVED_FRONT = {'protein_g': 25, 'calories': 120, 'added_sugar_g': 0, 'net_carbs_g': None}


def _approved(version=3, status='approved', panel=None, front=None):
    return {'sku': 'PSP-NEA', 'gtin': '850079939479', 'version': version, 'status': status,
            'panel': panel if panel is not None else dict(_APPROVED_PANEL),
            'front_callouts': front if front is not None else dict(_APPROVED_FRONT)}


def _matching_artwork():
    """Engine-shaped artwork values that agree with _APPROVED_PANEL exactly
    (added_sugar_g singular — the engine's own internal name)."""
    art = dict(_APPROVED_PANEL)
    art['added_sugar_g'] = art.pop('added_sugars_g')
    return art


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    # ── _parse_nfp_amount: "<1"/"<5" preserved, never coerced ─────────────────
    check('"<5" preserved as string', pe._parse_nfp_amount('<5') == '<5')
    check('"< 1" normalized to "<1"', pe._parse_nfp_amount('< 1') == '<1')
    check('plain number stays a number', pe._parse_nfp_amount(5) == 5)
    check('numeric string parses', pe._parse_nfp_amount('25g') == 25.0)
    check('null/None -> None', pe._parse_nfp_amount('null') is None and pe._parse_nfp_amount(None) is None)

    # ── _amounts_equal: exact match, no tolerance, threshold-string aware ─────
    check('"<5" == "<5"', pe._amounts_equal('<5', '<5') is True)
    check('"<5" != 5 (never coerced to the threshold number)', pe._amounts_equal('<5', 5) is False)
    check('5 == 5.0 (numeric type does not matter)', pe._amounts_equal(5, 5.0) is True)
    check('0.92 != 0.2', pe._amounts_equal(0.92, 0.2) is False)
    check('None never equals anything (caller skips None pairs itself)',
          pe._amounts_equal(None, 5) is False and pe._amounts_equal(5, None) is False)

    # ── Field-level diff: the exact two example messages from the spec ────────
    artwork = _matching_artwork()
    artwork['iron_mg'] = 0.92          # mg differs; %DV happens to match (both 0)
    issues, gaps = pe._diff_panel_fields(artwork, _APPROVED_PANEL, 3)
    check('Iron mismatch produces exactly one CRITICAL naming both values',
          len(issues) == 1 and issues[0]['severity'] == 'critical'
          and 'artwork reads 0.92mg 0%' in issues[0]['message']
          and 'approved panel v3 says 0.3mg 0%' in issues[0]['message'])
    check('no coverage gaps on a fully-read matching artwork', gaps == [])

    artwork2 = _matching_artwork()
    artwork2['sodium_dv'] = 21          # %DV differs; mg matches
    issues2, _ = pe._diff_panel_fields(artwork2, _APPROVED_PANEL, 3)
    check('Sodium %DV-only mismatch produces its own CRITICAL',
          len(issues2) == 1 and 'Sodium %DV: artwork reads 21%, approved panel v3 says 11%' == issues2[0]['message'])

    # A "<5" vs "<1" threshold change (cholesterol printed wrong) is a real
    # mismatch, not masked by both being "small."
    artwork3 = _matching_artwork()
    artwork3['cholesterol_mg'] = 5.0     # printed as a bare number, not "<5"
    issues3, _ = pe._diff_panel_fields(artwork3, _APPROVED_PANEL, 3)
    check('a bare number vs a "<5" threshold form is a mismatch, not a match',
          any('Cholesterol' in i['message'] for i in issues3))

    # Missing artwork read for a field the approved panel declares -> a gap
    # (review-level), never fabricated as a CRITICAL.
    artwork4 = _matching_artwork()
    del artwork4['calcium_mg']
    del artwork4['calcium_dv']
    issues4, gaps4 = pe._diff_panel_fields(artwork4, _APPROVED_PANEL, 3)
    check('unread approved field -> gap, not a CRITICAL', issues4 == [] and len(gaps4) >= 1)

    # ── Front call-out diff ────────────────────────────────────────────────────
    fi, fg = pe._diff_front_callouts({'calories': 120, 'protein_g': 20, 'added_sugar_g': 0},
                                     _APPROVED_FRONT)
    check('front protein mismatch -> CRITICAL naming both',
          any('Front Protein' in i['message'] and '20' in i['message'] and '25' in i['message'] for i in fi))
    # net_carbs_g null on the approved side -> never invented, always skipped.
    fi2, fg2 = pe._diff_front_callouts({'calories': 120, 'protein_g': 25, 'added_sugar_g': 0},
                                       _APPROVED_FRONT)
    check('approved front net_carbs_g:null is skipped, not compared', fi2 == [] and fg2 == [])

    # ── Ingredient / allergen statement diff ──────────────────────────────────
    r_fmt = pe._diff_text_statement('Ingredient statement',
                                    'Whey Protein Isolate,Non-Fat Milk Powder.',
                                    'Whey Protein Isolate, Non-Fat Milk Powder.', 3)
    check('whitespace-only ingredient difference -> review, not critical',
          len(r_fmt) == 1 and r_fmt[0]['severity'] == 'review')
    r_real = pe._diff_text_statement('Ingredient statement', 'Pea Protein, Rice Flour.',
                                     'Whey Protein Isolate, Non-Fat Milk Powder.', 3)
    check('genuine ingredient difference -> critical',
          len(r_real) == 1 and r_real[0]['severity'] == 'critical')
    r_missing = pe._diff_text_statement('Ingredient statement', '',
                                        'Whey Protein Isolate, Non-Fat Milk Powder.', 3)
    check('artwork could not read ingredients -> review, not critical, not silently skipped',
          len(r_missing) == 1 and r_missing[0]['severity'] == 'review')
    check('approved side null -> nothing to compare, no issue',
          pe._diff_text_statement('Ingredient statement', 'Whey.', None, 3) == [])

    # ── Full status model ──────────────────────────────────────────────────────
    # PANEL_MISSING — no record at all. Never CLEAN, never a false pass.
    r = pe._check_approved_panel({}, {}, '', '', None)
    check('no approved record -> PANEL_MISSING', r['status'] == 'PANEL_MISSING')
    check('PANEL_MISSING carries no panel_version', r['panel_version'] is None)

    # PANEL_NOT_APPROVED — a real record, still a draft. Blocks release either way.
    r = pe._check_approved_panel({}, {}, '', '', _approved(version=2, status='draft'))
    check('draft panel -> PANEL_NOT_APPROVED (not PANEL_MISSING)', r['status'] == 'PANEL_NOT_APPROVED')
    check('PANEL_NOT_APPROVED still carries the draft version', r['panel_version'] == 2)

    # VERIFIED — everything read and matches.
    art = _matching_artwork()
    front = dict(_APPROVED_FRONT)
    r = pe._check_approved_panel(art, front, _APPROVED_PANEL['ingredients'],
                                 'Milk', _approved(version=3))
    check('full match -> VERIFIED', r['status'] == 'VERIFIED')
    check('VERIFIED carries the matched version', r['panel_version'] == 3)

    # CRITICAL — artwork differs. One CRITICAL per differing field.
    bad_art = dict(art, protein_g=20)
    r = pe._check_approved_panel(bad_art, dict(front, protein_g=20),
                                 _APPROVED_PANEL['ingredients'], 'Milk', _approved(version=3))
    check('artwork differs -> CRITICAL (never VERIFIED)', r['status'] == 'CRITICAL')
    check('CRITICAL issues are all severity critical or review, never silently dropped',
          all(i['severity'] in ('critical', 'review') for i in r['issues'])
          and any(i['severity'] == 'critical' for i in r['issues']))

    # PANEL_SUPERSEDED — matches, but the panel moved on since this SKU's last
    # recorded proof. Never lets a stale match pass as still-current silently.
    r = pe._check_approved_panel(art, front, _APPROVED_PANEL['ingredients'], 'Milk',
                                 _approved(version=3), prior_panel_version=2)
    check('matches but panel revised since last proof -> PANEL_SUPERSEDED',
          r['status'] == 'PANEL_SUPERSEDED')
    r_same = pe._check_approved_panel(art, front, _APPROVED_PANEL['ingredients'], 'Milk',
                                      _approved(version=3), prior_panel_version=3)
    check('same prior version as current -> still VERIFIED (nothing changed)',
          r_same['status'] == 'VERIFIED')
    r_first = pe._check_approved_panel(art, front, _APPROVED_PANEL['ingredients'], 'Milk',
                                       _approved(version=3), prior_panel_version=None)
    check('no prior proof on record -> VERIFIED, not superseded', r_first['status'] == 'VERIFIED')

    # A real mismatch always outranks "superseded" — never hide a genuine
    # CRITICAL behind a softer re-proof note.
    r_both = pe._check_approved_panel(bad_art, dict(front, protein_g=20),
                                      _APPROVED_PANEL['ingredients'], 'Milk',
                                      _approved(version=3), prior_panel_version=2)
    check('a real mismatch wins over PANEL_SUPERSEDED', r_both['status'] == 'CRITICAL')

    # ── Vision gate: an approved panel on file forces a reliable read ─────────
    _orig = pe._ocr_needs_vision
    pe._ocr_needs_vision = lambda *a, **k: False
    try:
        _complete = 'nutrition facts calories 130 protein 25 contains: milk'
        check('gate: no approved panel + complete OCR -> vision skipped',
              pe._should_run_vision(_complete, '', 'x.pdf', None, has_approved_panel=False) is False)
        check('gate: an approved panel on file forces vision',
              pe._should_run_vision(_complete, '', 'x.pdf', None, has_approved_panel=True) is True)
    finally:
        pe._ocr_needs_vision = _orig

    # ── unverified-status set includes the new panel statuses ─────────────────
    check('PANEL_MISSING counts as not-fully-verified', 'PANEL_MISSING' in pe._NOT_VERIFIED_STATUSES)
    check('PANEL_NOT_APPROVED counts as not-fully-verified', 'PANEL_NOT_APPROVED' in pe._NOT_VERIFIED_STATUSES)
    check('PANEL_SUPERSEDED counts as not-fully-verified', 'PANEL_SUPERSEDED' in pe._NOT_VERIFIED_STATUSES)
    check('VERIFIED is NOT in the not-verified set', 'VERIFIED' not in pe._NOT_VERIFIED_STATUSES)

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All approved-nutrition-panel checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
