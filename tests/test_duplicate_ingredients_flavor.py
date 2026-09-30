"""Duplicate ingredient statements — flavor-aware comparison (Check 7.4).

    python3 tests/test_duplicate_ingredients_flavor.py

Exits non-zero on any failure. No test framework required.

Bug (run cd4a2150): 13 duplicate-statement warnings, 12 of them expected. On a
flavor line built from one base where the flavor comes entirely from "Natural
Flavors", an identical statement between two SKUs is the normal state of the
product line, not a defect — flagging every one buried the one real question:
a NAMED flavor-defining ingredient (cocoa powder, ground cinnamon, a fruit
powder, a nut butter) present in a SKU whose own flavor name does not suggest
it. The count went from 8 to 13 when a spelling fix ("CocoaPowder" ->
"Cocoa Powder") made two statements match exactly that previously differed by
a space — the check was working, the artwork got cleaner, and the noise grew.
That is the wrong direction; this fixes it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402


def _mk(fname, ingredients):
    return {'filename': fname, 'snapshot': {'ingredients': ingredients}, 'checks': {}}


def _issues(res):
    return res['checks']['ingredients']['issues']


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    # ── The real finding: maple_donut and neapolitan share Cocoa Powder ────────
    ings = ['whey protein isolate', 'cocoa powder', 'natural flavors', 'sea salt', 'guar gum']
    maple = _mk('PD_bottle_maple_donut.pdf', list(ings))
    neapolitan = _mk('PD_bottle_neapolitan.pdf', list(ings))
    pe._flag_duplicate_ingredients([maple, neapolitan])

    check('maple_donut gets a WARNING (cocoa is not suggested by "maple donut")',
          any(i['severity'] == 'warning' for i in _issues(maple)))
    check('the WARNING names the actual ingredient (cocoa powder)',
          any('cocoa powder' in i['message'].lower() for i in _issues(maple)
             if i['severity'] == 'warning'))
    check('the WARNING is never escalated to critical — this is a question, not a verdict',
          not any(i['severity'] == 'critical' for i in _issues(maple)))
    check('neapolitan (where cocoa IS expected) stays INFO, not WARNING',
          all(i['severity'] == 'info' for i in _issues(neapolitan)))
    check('this run reports exactly ONE actionable finding for this pair (on maple_donut only)',
          sum(1 for i in _issues(maple) if i['severity'] == 'warning') == 1
          and not any(i['severity'] == 'warning' for i in _issues(neapolitan)))

    # ── The expected case: identical base-flavor statement, no named ingredient ──
    base_ings = ['whey protein isolate', 'natural flavors', 'sea salt', 'guar gum', 'stevia leaf extract']
    birthday = _mk('PD_bottle_birthday_cake.pdf', list(base_ings))
    bananas = _mk('PD_bottle_bananas_foster.pdf', list(base_ings))
    pe._flag_duplicate_ingredients([birthday, bananas])
    check('identical statement differing only in flavor (no named ingredient) -> INFO on BOTH',
          all(i['severity'] == 'info' for i in _issues(birthday))
          and all(i['severity'] == 'info' for i in _issues(bananas)))
    check('never a WARNING for the pure "Natural Flavors only" case — this is the normal state',
          not any(i['severity'] == 'warning' for i in _issues(birthday) + _issues(bananas)))

    # ── A named ingredient legitimately shared by two matching flavor names ──────
    # (e.g. "double chocolate" and "neapolitan" both plausibly explain cocoa) —
    # must not be a false positive just because the ingredient is "named."
    choc_ings = ['whey protein isolate', 'cocoa powder', 'natural flavors', 'sea salt']
    dchoc = _mk('PD_bottle_double_chocolate.pdf', list(choc_ings))
    neap2 = _mk('PD_bottle_neapolitan.pdf', list(choc_ings))
    pe._flag_duplicate_ingredients([dchoc, neap2])
    check('cocoa shared between two chocolate-suggesting flavor names -> INFO, no false positive',
          all(i['severity'] == 'info' for i in _issues(dchoc))
          and all(i['severity'] == 'info' for i in _issues(neap2)))

    # ── Ingredient order alone (no wording difference) is still the expected case ──
    ordered_a = _mk('PD_bottle_sugar_cookie.pdf',
                    ['whey protein isolate', 'natural flavors', 'sea salt', 'guar gum'])
    ordered_b = _mk('PD_bottle_salted_caramel.pdf',
                    ['whey protein isolate', 'sea salt', 'natural flavors', 'guar gum'])
    pe._flag_duplicate_ingredients([ordered_a, ordered_b])
    check('a reordered-but-otherwise-identical statement is still INFO',
          all(i['severity'] == 'info' for i in _issues(ordered_a)))
    check('the "identical apart from order" wording is used, not "identical"',
          any('apart from ingredient order' in i['message'] for i in _issues(ordered_a)))

    # ── Two genuinely different SKUs (below the overlap threshold) — no finding ──
    different_a = _mk('PD_bottle_vanilla.pdf',
                      ['whey protein isolate', 'natural flavors', 'sea salt'])
    different_b = _mk('PD_bottle_chocolate.pdf',
                      ['pea protein', 'rice protein', 'cocoa powder', 'stevia', 'sunflower lecithin'])
    pe._flag_duplicate_ingredients([different_a, different_b])
    check('genuinely different ingredient lists produce no duplicate finding at all',
          'ingredients' not in different_a['checks'] and 'ingredients' not in different_b['checks'])

    # ── _sku_flavor_name / _named_flavor_mismatches unit-level behavior ─────────
    check('flavor name strips common prefixes and separators',
          pe._sku_flavor_name('PD_bottle_maple_donut.pdf') == 'maple donut')
    check('flavor name handles a plain filename with no prefix',
          pe._sku_flavor_name('neapolitan.pdf') == 'neapolitan')

    check('cocoa powder flagged when flavor name is unrelated',
          pe._named_flavor_mismatches(['cocoa powder', 'sea salt'], 'maple donut') == ['cocoa powder'])
    check('cocoa powder NOT flagged when flavor name suggests it',
          pe._named_flavor_mismatches(['cocoa powder', 'sea salt'], 'double chocolate') == [])
    check('generic ingredients (no named flavor component) never flag',
          pe._named_flavor_mismatches(['whey protein isolate', 'natural flavors', 'sea salt',
                                       'guar gum'], 'maple donut') == [])
    check('"natural flavors" itself is never treated as a named flavor component',
          pe._named_flavor_mismatches(['natural flavors', 'organic natural flavors'],
                                      'anything at all') == [])

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All flavor-aware duplicate-ingredient checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
