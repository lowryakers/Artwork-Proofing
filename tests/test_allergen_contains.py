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

    # SUSPECT downgrade: a doubted number elsewhere on the file softens
    # value-comparison criticals, never an allergen finding — pinned by issue
    # kind, so it holds even under a downgradable check key ('panel').
    allergen = pe._check_contains_vs_approved('Milk', APPROVED)[0]
    allergen['kind'] = pe._FALCPA_ISSUE_KIND
    stmt_diff = {'severity': 'critical', 'kind': pe._FALCPA_ISSUE_KIND,
                 'message': 'Allergen statement differs from the approved panel v1.'}
    nfp_crit = {'severity': 'critical', 'message': 'Calorie mismatch: front call-out shows 110 cal'}
    checks = {'netwt': {'issues': [{'severity': 'suspect', 'message': 'SUSPECT READ — serving size'}]},
              'fda': {'issues': [allergen]},
              'panel': {'issues': [stmt_diff]},
              'nfp': {'issues': [nfp_crit]}}
    pe._downgrade_criticals_on_suspect_file(checks)
    check('suspect downgrade leaves the FALCPA Contains-line CRITICAL a CRITICAL',
          allergen['severity'] == 'critical')
    check('...and the approved-panel allergen statement CRITICAL, under a downgradable key',
          stmt_diff['severity'] == 'critical')
    check('...while still downgrading a value-comparison CRITICAL on the same file',
          nfp_crit['severity'] == 'suspect')

    # Orientation: an upside-down panel crop is turned upright before vision
    # reads it (Tesseract orientation detection — local, repeatable). A
    # synthetic panel of real text; skipped where tesseract is absent.
    import shutil
    if shutil.which('tesseract'):
        from PIL import Image as _I, ImageDraw as _D, ImageFont as _F
        try:
            font = _F.truetype('DejaVuSans.ttf', 34)
        except OSError:
            font = _F.load_default()
        page = _I.new('RGB', (900, 1200), 'white')
        d = _D.Draw(page)
        for k, line in enumerate(['Nutrition Facts', 'About 5 servings per container',
                                  'Serving size 3/4 Cup (88g)', 'Calories 300', 'Total Fat 2.5g',
                                  'Total Carbohydrate 56g', 'Total Sugars 10g',
                                  'Includes 0g Added Sugars', 'Protein 19g', 'Contains: Milk',
                                  'Ingredients: Oat Flour, Buttermilk Powder, Rice Flour']):
            d.text((40, 40 + k * 95), line, fill='black', font=font)
        flipped = page.rotate(180)
        fixed = pe._upright(flipped)
        check('orientation: an upside-down panel crop is turned upright',
              fixed.tobytes() == page.tobytes())
        check('orientation: an upright panel crop is left as it is',
              pe._upright(page).tobytes() == page.tobytes())

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
