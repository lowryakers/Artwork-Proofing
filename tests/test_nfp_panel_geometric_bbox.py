"""Geometric panel localization (item 1b, run cd4a2150 / c2beb887): the
Nutrition Facts panel is locatable as the large white-filled region on the
layflat, distinguishable from an adjacent (non-white or differently-bordered)
protein-options strip. _largest_white_region_bbox_frac tightens a loose,
padded bounding box down to that region via connected-component flood fill
-- robust to internal black rule lines/text, unlike a "largest solid white
rectangle" search, which a real Nutrition Facts table would shatter.

    python3 tests/test_nfp_panel_geometric_bbox.py

Exits non-zero on any failure. Requires Pillow; skips cleanly (exit 0) if it
is not installed, same convention as tests/test_spot_color_checks.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402


def _run():
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print('[skip] test_nfp_panel_geometric_bbox (Pillow not installed)')
        return 0

    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    W, H = 400, 300
    im = Image.new('RGB', (W, H), (40, 120, 90))  # non-white package background
    draw = ImageDraw.Draw(im)

    # The "panel": a white rectangle with internal black rule lines (simulating
    # a real Nutrition Facts table) — must NOT shatter the find into a sliver.
    panel_box = (50, 50, 250, 220)
    draw.rectangle(panel_box, fill=(255, 255, 255))
    for y in range(70, 220, 25):
        draw.line((60, y, 240, y), fill=(0, 0, 0), width=2)

    # The "protein options strip": a SEPARATE white block, outside the panel,
    # with a visible gap between the two (not touching/overlapping).
    strip_box = (50, 240, 250, 280)
    draw.rectangle(strip_box, fill=(255, 255, 255))

    refined = pe._largest_white_region_bbox_frac(im, min_frac=0.05)
    check('a white region is found', refined is not None)
    if refined:
        x0, y0, x1, y1 = refined
        found_box = (x0 * W, y0 * H, x1 * W, y1 * H)
        # The panel region should be recovered (allowing a little slack for
        # downsampling), and critically must NOT extend down into the strip.
        check('found region starts near the panel\'s left/top edge',
              abs(found_box[0] - panel_box[0]) < 15 and abs(found_box[1] - panel_box[1]) < 15)
        check('found region ends near the panel\'s right/bottom edge, not the strip',
              abs(found_box[2] - panel_box[2]) < 15 and abs(found_box[3] - panel_box[3]) < 15)
        check('the strip (y=240-280) is excluded from the found region',
              found_box[3] < strip_box[1])

    # A page with no meaningful white region (all background, tiny speck) ->
    # None, so the caller falls back to its original (unrefined) crop.
    im2 = Image.new('RGB', (W, H), (40, 120, 90))
    draw2 = ImageDraw.Draw(im2)
    draw2.rectangle((10, 10, 15, 15), fill=(255, 255, 255))  # tiny speck, well under min_frac
    refined2 = pe._largest_white_region_bbox_frac(im2, min_frac=0.15)
    check('a tiny/no white region returns None (caller falls back safely)', refined2 is None)

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All geometric panel-bbox checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
