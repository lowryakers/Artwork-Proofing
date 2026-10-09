"""FALCPA Contains line vs the approved record.

    python3 tests/test_allergen_contains.py

When vision returns no Contains line, the artwork side is read from the label
text. The first "contains" there is often the ingredient list's "contains less
than 2% of: ..."; read as the declaration, it named no allergen and flagged
every approved allergen missing — a false CRITICAL on a correct label.

Exits non-zero on any failure. No test framework, no network, no vision API.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402

APPROVED = {'version': 1, 'status': 'approved', 'panel': {'allergens': ['Milk', 'Wheat']}}
APPROVED_MILK = {'version': 1, 'status': 'approved', 'panel': {'allergens': ['Milk']}}
LABEL = ('Ingredients: Oat Flour, Buttermilk Powder, Sorghum Flour, Tapioca Starch, contains less '
         'than 2% of: Aluminum Free Baking Powder, Baking Soda, Xanthan Gum\nContains: Milk\n')


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    def crit(issues):
        return [i for i in issues if i['severity'] == 'critical']

    out = pe._check_contains_vs_approved('Milk', APPROVED)
    check('vision Contains line missing an approved allergen -> one CRITICAL naming it',
          len(crit(out)) == 1 and 'Wheat' in out[0]['message'])

    out = pe._check_contains_vs_approved(None, APPROVED, LABEL)
    check('text fallback skips "contains less than 2% of" and reads the Contains line',
          len(crit(out)) == 1 and 'Wheat' in out[0]['message'] and '"Contains: Milk"' in out[0]['message'])

    out = pe._check_contains_vs_approved(None, APPROVED_MILK, LABEL)
    check('a correct label read from text raises nothing', out == [])

    out = pe._check_contains_vs_approved(None, APPROVED, 'Ingredients: Oat Flour, contains less than 2% of: Salt\n')
    check('no Contains declaration in the text -> review, not a CRITICAL',
          len(out) == 1 and out[0]['severity'] == 'review')

    out = pe._check_contains_vs_approved(None, APPROVED_MILK, 'CONTAINS MILK\n')
    check('an upper-case declaration without a colon still counts when it names an allergen',
          out == [])

    out = pe._check_contains_vs_approved('Milk, Wheat', APPROVED)
    check('a complete Contains line raises nothing', out == [])

    # Contains-line crop reader: orientation retries, and it only accepts a
    # statement that names an allergen. Mocked API, no network.
    import types
    from PIL import Image
    tmp = os.path.join(os.path.dirname(__file__), '_contains_tmp.png')
    Image.new('RGB', (1200, 900), 'white').save(tmp)
    replies = iter(['{"contains_statement": null}', '{"contains_statement": "Contains: Milk"}'])

    class _R:
        def __init__(s, t):
            s.content, s.stop_reason = [types.SimpleNamespace(text=t)], 'end_turn'
    prev = (pe.ANTHROPIC_AVAILABLE, pe.PIL_AVAILABLE, getattr(pe, '_anthropic', None), getattr(pe, 'Image', None))
    os.environ.setdefault('ANTHROPIC_API_KEY', 'test')
    pe.ANTHROPIC_AVAILABLE, pe.PIL_AVAILABLE, pe.Image = True, True, Image
    pe._anthropic = types.SimpleNamespace(Anthropic=lambda api_key=None: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **k: _R(next(replies)))))
    try:
        got = pe._read_contains_crop(tmp, [0.4, 0.4, 0.6, 0.6])
        check('Contains crop: unreadable straight, read rotated -> "Milk"', got == 'Milk')
        replies = iter(['{"contains_statement": "less than 2% of: salt"}'] * 3)
        check('Contains crop: a statement naming no allergen is not accepted',
              pe._read_contains_crop(tmp, [0.4, 0.4, 0.6, 0.6]) is None)
        check('Contains crop: no panel box -> None, no call', pe._read_contains_crop(tmp, None) is None)
    finally:
        pe.ANTHROPIC_AVAILABLE, pe.PIL_AVAILABLE, pe._anthropic, pe.Image = prev
        os.remove(tmp)

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All allergen Contains checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
