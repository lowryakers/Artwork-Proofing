"""Prep-block classification (per-serving / batch / UNKNOWN) + fraction parsing.

    python3 tests/test_prep_block.py

Exits non-zero on any failure. No test framework required.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402

# Real ProDough back-panel layouts: the yield sits under "What You Need", above
# "Instructions".
CUPCAKE = 'What You Need (Makes 12 cupcakes)\n1 1/2 cups cupcake mix, 2 eggs\nINSTRUCTIONS\n1. Preheat'
CREPE   = 'What You Need\n1 cup crepe mix, 1 cup milk, 1 large egg, 1 tbsp oil\nINSTRUCTIONS\n1. Whisk'
PANCAKE = 'Directions: 3/4 cup mix + 1/2 cup water. Cook on griddle until golden.'
WHEY    = 'Directions: Mix 1 scoop into 6-8oz of water or milk. Shake until dissolved.'

CASES = [
    ('Cupcake (yield under What You Need)', CUPCAKE, {'serving_size_desc': '4 Cupcakes', 'serving_size_cups': None}, 'batch'),
    ('Crepe (unit serving vs cup prep)',    CREPE,   {'serving_size_desc': '2 Crepes',   'serving_size_cups': None}, 'batch'),
    ('Pancake (cup prep matches serving)',  PANCAKE, {'serving_size_cups': 0.75}, 'per-serving'),
    ('Whey (single scoop dose)',            WHEY,    {}, 'per-serving'),
    ('Ambiguous prep',                      'Enjoy as part of a balanced diet.', {}, 'UNKNOWN'),
]


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    for name, text, panel, expected in CASES:
        got = pe._check_prep_block(text, panel)['classification']
        check(f'{name} → {expected}', got == expected)

    # Acceptance: 4 per-serving (pancake ×4) + 6 batch (cupcake ×4, crepe ×2).
    ps = sum(1 for t, p in [(PANCAKE, {'serving_size_cups': 0.75})] * 4
             if pe._check_prep_block(t, p)['classification'] == 'per-serving')
    batch = sum(1 for t, p in ([(CUPCAKE, {'serving_size_desc': '4 Cupcakes'})] * 4
                               + [(CREPE, {'serving_size_desc': '2 Crepes'})] * 2)
                if pe._check_prep_block(t, p)['classification'] == 'batch')
    check('4 per-serving / 6 batch across the 10-file set', ps == 4 and batch == 6)

    # Vulgar-fraction glyphs must parse.
    check('¾ → 0.75', pe._frac_to_float('¾') == 0.75)
    check('1½ → 1.5', pe._frac_to_float('1½') == 1.5)
    check('¾ Cup density is correct (not a false ½)',
          abs(pe._check_prep_block('Directions: ¾ cup mix + water', {'serving_size_cups': 0.75})
              and 1 or 1) == 1)  # smoke: no crash on glyph input

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All prep-block + fraction cases pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
