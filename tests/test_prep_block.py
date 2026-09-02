"""Prep-block classification (per-serving vs batch).

    python3 tests/test_prep_block.py

Exits non-zero on any failure. No test framework required.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402


# (case, prep text, panel, expected classification)
CASES = [
    ('Pancake per-serving',
     'Directions: Mix 1/2 cup mix with 1/3 cup water. Cook until golden.',
     {'serving_size_cups': 0.5}, 'per-serving'),
    ('Cupcake batch (yield)',
     'Directions: Combine 1 1/2 cups mix with 2 eggs and 1/2 cup oil. Makes 12 cupcakes.',
     {'serving_size_cups': 0.5}, 'batch'),
    ('Crepe batch (quantity mismatch)',
     'Directions: Whisk 1 cup mix, 1 cup milk, 1 egg, 1 tbsp oil until smooth.',
     {'serving_size_cups': 0.29}, 'batch'),
    ('Protein powder per-serving (scoop)',
     'Directions: Mix 1 scoop into 6-8oz of water or milk. Shake until dissolves.',
     {}, 'per-serving'),
    ('No prep block',
     'Nutrition Facts Calories 130 Protein 25g Ingredients: Whey.',
     {}, None),
]


def _run():
    failures = []
    for name, text, panel, expected in CASES:
        got = pe._check_prep_block(text, panel)['classification']
        ok = got == expected
        print(f'[{"ok  " if ok else "FAIL"}] {name:38s} -> {got} (want {expected})')
        if not ok:
            failures.append(f'{name}: got {got}, expected {expected}')

    # Acceptance: a batch prep block must not be reported as needing revision.
    batch = pe._check_prep_block(
        'Directions: Combine 1 1/2 cups mix. Makes 12 cupcakes.', {'serving_size_cups': 0.5})
    if batch['classification'] != 'batch' or 'does NOT require' not in ' '.join(batch['notes']):
        failures.append('batch prep must be classified batch and marked as not needing prep edits')

    print()
    if failures:
        print('FAILURES:')
        for f in failures:
            print('  -', f)
        return 1
    print('All prep-block classification cases pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
