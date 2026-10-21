"""Live-text path: read label copy from a PDF's text layer instead of OCR.

Print files from the converter are outlined (no text layer), so every label
read today goes through OCR or vision. When the designer also sends a
live-text PDF of the same artwork, its text layer gives the label copy
exactly — character for character — and the ingredient statement, %DV column,
"less than 2%" break and Contains line can be diffed against the approved panel
without a reader's guess in between.

A companion live-text PDF is a different file from the one that prints, so its
text describes the print file only if it is the same artwork. Two guards:

  layout   where both pages are the same size, they must render alike; a real
           difference (another version, a moved block) rejects the companion.
           A converter proof and a design export are different pages, so the
           layout is then "not comparable" — noted, not a rejection.
  content  each element the live text supplies (ingredient statement, Contains
           line, %DV column) must agree in content with what OCR/vision read
           off the print file itself; where they disagree the element is NOT
           EVALUATED with both versions shown (the caller does this).

Pixels cannot prove every character: outlined and live text rasterise a little
differently, about as much as one changed character does. Layout catches a
wrong version; content agreement catches a changed word or number; the live
text then supplies the exact characters. A print file that itself carries live
label copy needs neither guard.

Pairing: `X.pdf` is paired with `X_live.pdf` (also `-live`, `_livetext`,
`_live_text`, any case). A print file that itself carries live label copy is
read directly.
"""
import difflib
import os
import re

try:
    import fitz  # PyMuPDF
    FITZ_AVAILABLE = True
except ImportError:  # pragma: no cover
    FITZ_AVAILABLE = False

_COMPANION_RE = re.compile(r'^(?P<stem>.+?)[ _-]*(?:live[ _-]?text|live)$', re.I)

# Text-layer artefacts that are not printed characters: ligature glyphs, soft
# hyphens, non-breaking and zero-width spaces. Everything else is compared as is.
_ARTEFACTS = {'ﬀ': 'ff', 'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬃ': 'ffi', 'ﬄ': 'ffl',
              '­': '', ' ': ' ', '​': '', ' ': ' ', ' ': ' '}

# Max share of pixels that may differ (at 72 dpi) between the print render and
# the live render for the two to share a layout. Outlined vs live text of the
# same artwork measured 0.11%; a different version or moved block is far more.
LAYOUT_TOLERANCE = 0.01
_RENDER_DPI = 72


def clean(text: str) -> str:
    for k, v in _ARTEFACTS.items():
        text = text.replace(k, v)
    return text


def pair_files(paths: list) -> tuple:
    """(print_paths, {print_path: live_path}, unpaired_live_paths).

    A file named like a companion whose print file is not in the upload is
    returned as unpaired; the caller proofs it as an artwork in its own right
    (its own text layer is then read directly) and reports that it had no
    print file to pair with."""
    by_stem = {}
    for p in paths:
        stem, ext = os.path.splitext(os.path.basename(p))
        by_stem[(stem.lower(), ext.lower())] = p
    pairs, live_files = {}, set()
    for p in paths:
        stem, ext = os.path.splitext(os.path.basename(p))
        m = _COMPANION_RE.match(stem)
        if not m:
            continue
        target = by_stem.get((m.group('stem').lower(), ext.lower()))
        if target and target != p:
            pairs[target] = p
            live_files.add(p)
        else:
            live_files.add(p)
    unpaired = [p for p in paths if p in live_files and p not in pairs.values()]
    prints = [p for p in paths if p not in live_files] + unpaired
    return prints, pairs, unpaired


def has_label_text(pdf_path: str) -> bool:
    """True when the PDF's text layer carries label copy, not just die-template
    or proof-sheet text: an ingredient statement and a facts panel heading."""
    if not FITZ_AVAILABLE:
        return False
    try:
        with fitz.open(pdf_path) as d:
            t = clean(d[0].get_text()).lower()
        return 'ingredients' in t and ('nutrition facts' in t or 'supplement facts' in t)
    except Exception:
        return False


def _render_gray(pdf_path: str):
    from PIL import Image
    with fitz.open(pdf_path) as d:
        page = d[0]
        pix = page.get_pixmap(dpi=_RENDER_DPI, colorspace=fitz.csGRAY, alpha=False)
        size = (round(page.rect.width, 1), round(page.rect.height, 1))
    return Image.frombytes('L', (pix.width, pix.height), pix.samples), size


def compare_layout(print_pdf: str, live_pdf: str) -> tuple:
    """('match' | 'mismatch' | 'not_comparable', detail)."""
    try:
        from PIL import ImageChops
        a, size_a = _render_gray(print_pdf)
        b, size_b = _render_gray(live_pdf)
        if size_a != size_b:
            return 'not_comparable', (f'different pages ({size_a[0]:g} x {size_a[1]:g} pt print vs '
                                      f'{size_b[0]:g} x {size_b[1]:g} pt live), layout not compared')
        if a.size != b.size:
            b = b.resize(a.size)
        diff = ImageChops.difference(a, b).point(lambda p: 255 if p > 64 else 0)
        changed = diff.histogram()[255] / float(a.width * a.height)
        if changed <= LAYOUT_TOLERANCE:
            return 'match', f'layouts match ({changed:.2%} of pixels differ)'
        bbox = diff.getbbox()
        return 'mismatch', (f'{changed:.2%} of pixels differ (tolerance {LAYOUT_TOLERANCE:.0%}), within x '
                            f'{bbox[0] / a.width:.0%}-{bbox[2] / a.width:.0%}, y {bbox[1] / a.height:.0%}-'
                            f'{bbox[3] / a.height:.0%} of the page — not the same artwork')
    except Exception as e:
        return 'not_comparable', f'could not compare the renders ({type(e).__name__}: {e})'


# ── Extraction ────────────────────────────────────────────────────────────────

def _lines(page) -> list:
    """Text lines as (text, bbox, dir) from the text layer, artefacts cleaned."""
    out = []
    for block in page.get_text('dict').get('blocks', []):
        for line in block.get('lines', []):
            text = clean(''.join(span.get('text', '') for span in line.get('spans', [])))
            if text.strip():
                out.append({'text': text, 'bbox': tuple(line['bbox']), 'dir': tuple(line.get('dir', (1, 0))),
                            'block': id(block)})
    return out


def _upright(pt, d):
    """Rotate a point into the frame of text running along direction d, so
    rows and columns read the same for upside-down or sideways panels (pouch
    back panels print rotated 180 degrees)."""
    c, s = d
    x, y = pt
    return (c * x + s * y, -s * x + c * y)


def _center(b, d):
    return _upright(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), d)


def extract(pdf_path: str) -> dict:
    """{'text': full page text in reading order, 'lines': [...]} for page 1."""
    with fitz.open(pdf_path) as d:
        page = d[0]
        lines = _lines(page)
        text = clean(page.get_text())
    return {'text': text, 'lines': lines, 'source': os.path.basename(pdf_path)}


def _block_text(lines, block_id, d):
    """One text frame's lines joined in reading order. A line break is layout,
    not a character: it becomes one space, except after a hyphen that ends a
    line, which joins directly (and is reported by the caller)."""
    mine = sorted((ln for ln in lines if ln['block'] == block_id),
                  key=lambda ln: (round(_center(ln['bbox'], d)[1], 1), _center(ln['bbox'], d)[0]))
    out = ''
    for ln in mine:
        t = ln['text'].strip()
        if not out:
            out = t
        elif out.endswith('-'):
            out += t
        else:
            out += ' ' + t
    return out


_INGR_HEAD = re.compile(r'^\s*ingredients?\s*:\s*', re.I)
_CONTAINS_HEAD = re.compile(r'^\s*contains\s*:', re.I)


def ingredient_statement(lt: dict) -> str:
    """The ingredient statement exactly as printed, without its "Ingredients:"
    label, up to (not including) a Contains line in the same frame. None if
    the text layer has no ingredient statement."""
    lines = lt['lines']
    head = next((ln for ln in lines if _INGR_HEAD.match(ln['text'])), None)
    if head is None:
        head = next((ln for ln in lines if re.search(r'\bingredients?\s*:', ln['text'], re.I)), None)
    if head is None:
        return None
    frame = _block_text(lines, head['block'], head['dir'])
    m = re.search(r'\bingredients?\s*:\s*', frame, re.I)
    stmt = frame[m.end():] if m else frame
    cut = re.search(r'\s*\bcontains\s*:', stmt, re.I)
    if cut:
        stmt = stmt[:cut.start()]
    return stmt.strip()


def contains_line(lt: dict) -> str:
    """The FALCPA "Contains:" line exactly as printed (with its label), or
    None. "contains less than 2% of" is part of the ingredient statement."""
    for ln in lt['lines']:
        m = re.search(r'\bcontains\s*:\s*(?!.*less than)', ln['text'], re.I)
        if m and not re.search(r'less\s+than|or\s+less', ln['text'], re.I):
            frame = _block_text(lt['lines'], ln['block'], ln['dir'])
            k = re.search(r'\bcontains\s*:', frame, re.I)
            return frame[k.start():].strip() if k else ln['text'].strip()
    return None


# NFP row label -> engine field prefix (amount field, %DV field).
_DV_ROWS = (
    (r'^total\s+fat\b', 'total_fat'), (r'^saturated\s+fat\b', 'saturated_fat'),
    (r'^cholesterol\b', 'cholesterol'), (r'^sodium\b', 'sodium'),
    (r'^total\s+carbohydrates?\b', 'total_carbohydrate'), (r'^dietary\s+fiber\b', 'dietary_fiber'),
    (r'^includes\b.*\badded\s+sugars?\b', 'added_sugars'), (r'^vitamin\s+d\b', 'vitamin_d'),
    (r'^calcium\b', 'calcium'), (r'^iron\b', 'iron'), (r'^potassium\b', 'potassium'),
)


def dv_column(lt: dict) -> dict:
    """{field: '%DV as printed'} for each Nutrition Facts row that prints one,
    e.g. {'total_fat': '2%', 'sodium': '14%'}. Rows are rebuilt from text
    position (label on the left, %DV right-aligned on the same baseline), in
    the panel's own orientation. None if no panel heading is found."""
    lines = lt['lines']
    head = next((ln for ln in lines if re.search(r'nutrition\s+facts|supplement\s+facts', ln['text'], re.I)), None)
    if head is None:
        return None
    d = head['dir']
    hx0 = min(_upright((head['bbox'][0], head['bbox'][1]), d)[0], _upright((head['bbox'][2], head['bbox'][3]), d)[0])
    hy = _center(head['bbox'], d)[1]
    dvh = next((ln for ln in lines if ln['dir'] == d and re.search(r'%\s*daily\s+value', ln['text'], re.I)
                and _center(ln['bbox'], d)[1] > hy), None)
    foot = next((ln for ln in lines if ln['dir'] == d and re.search(r'the\s+%\s*daily\s+value', ln['text'], re.I)
                 and _center(ln['bbox'], d)[1] > hy), None)
    x_max = (max(_upright((dvh['bbox'][0], dvh['bbox'][1]), d)[0], _upright((dvh['bbox'][2], dvh['bbox'][3]), d)[0])
             if dvh else hx0 + 400)
    y_max = _center(foot['bbox'], d)[1] if foot else hy + 600
    inside = [ln for ln in lines if ln['dir'] == d
              and hy < _center(ln['bbox'], d)[1] < y_max
              and hx0 - 4 <= _center(ln['bbox'], d)[0] <= x_max + 4]
    # Group into rows by baseline, then read each row left to right.
    rows = []
    for ln in sorted(inside, key=lambda ln: _center(ln['bbox'], d)[1]):
        cy = _center(ln['bbox'], d)[1]
        if rows and abs(rows[-1][0] - cy) <= 2.0:
            rows[-1][1].append(ln)
        else:
            rows.append([cy, [ln]])
    out = {}
    for _, row in rows:
        text = ' '.join(ln['text'].strip() for ln in sorted(row, key=lambda ln: _center(ln['bbox'], d)[0]))
        m = re.search(r'(\d+(?:\.\d+)?\s*%)\s*$', text)
        if not m:
            continue
        for pat, field in _DV_ROWS:
            if re.search(pat, text.strip(), re.I) and field not in out:
                out[field] = m.group(1).replace(' ', '')
                break
    return out


# ── The "less than 2%" break ─────────────────────────────────────────────────

# "less than 2% of", "less than 2 percent of" (Apple Pie, run aa5b6042), "2% or less of".
_BREAK_RE = re.compile(r'(?:contains\s+)?(?:less\s+than\s+\d+\s*(?:%|percent\b)|\d+\s*(?:%|percent\b)\s+or\s+less)'
                       r'\s+of\s*:?', re.I)


def two_percent_break(statement: str) -> dict:
    """Where the "contains less than 2% of:" group starts: the break wording as
    printed, how many ingredients precede it, and the ingredient just before.
    None when the statement has no such group."""
    if not statement:
        return None
    m = _BREAK_RE.search(statement)
    if not m:
        return None
    before = _top_level_items(statement[:m.start()])
    return {'wording': m.group(0).strip(), 'position': len(before),
            'after': before[-1] if before else None,
            'major': before, 'minor': _top_level_items(statement[m.end():])}


def _top_level_items(text: str) -> list:
    out, buf, depth = [], '', 0
    for ch in text:
        if ch in '([':
            depth += 1
        elif ch in ')]':
            depth = max(0, depth - 1)
        if ch == ',' and depth == 0:
            if buf.strip():
                out.append(buf.strip())
            buf = ''
        else:
            buf += ch
    if buf.strip(' ,'):
        out.append(buf.strip(' ,'))
    return out


# ── Character-level diff ──────────────────────────────────────────────────────

def char_diff(artwork: str, approved: str, width: int = 24) -> list:
    """The exact differences between two strings, each as
    {'at': character index in the artwork, 'artwork': '...', 'approved': '...',
     'context': '...'}. Compared token by token (words, spaces and each
    punctuation mark), so a changed word reads as that word — "Guar" vs
    "Gellan" — not as scattered letters, and a lone "." or space still shows
    as itself. Nothing is normalised: every character counts."""
    tok = lambda s: re.findall(r'\w+|\s+|[^\w\s]', s)
    ta, tb = tok(artwork), tok(approved)
    starts, pos = [], 0
    for t in ta:
        starts.append(pos)
        pos += len(t)
    starts.append(pos)
    out = []
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == 'equal':
            continue
        c0, c1 = starts[a0], starts[a1]
        ctx = artwork[max(0, c0 - width):c0] + '[' + artwork[c0:c1] + ']' + artwork[c1:c1 + width]
        out.append({'at': c0, 'artwork': artwork[c0:c1], 'approved': ''.join(tb[b0:b1]), 'context': ctx})
    return out


def content_equal(a: str, b: str) -> bool:
    """Equal apart from case, spacing and punctuation — the line between a
    REVIEW-level formatting difference and a CRITICAL content difference."""
    norm = lambda s: re.sub(r'[^a-z0-9%]+', '', (s or '').lower())
    return norm(a) == norm(b)
