"""
ProDough Artwork Proofing Engine
Processes packaging PDFs through 5 checks:
  1. GTIN/barcode detection
  2. Front call-out vs Nutrition Facts Panel consistency
  3. Eyemark contrast
  4. Spelling / brand-name errors
  5. FDA compliance flags

Multi-brand support: pass brand_config={'brand_mode': 'generic', ...} to
run a lighter check set suitable for any brand's packaging artwork.
"""
import os
import re
import json
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from pyzbar.pyzbar import decode as _pyzbar_decode
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False

_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')

# ── In-memory job store ───────────────────────────────────────────────────────

_jobs: dict = {}
_jobs_lock = threading.Lock()


def create_job(filenames: list, brand_config: dict = None) -> str:
    job_id = str(uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[job_id] = {
            'id': job_id,
            'status': 'pending',
            'created': datetime.now().isoformat(),
            'filenames': filenames,
            'progress': 0,
            'current_file': '',
            'results': [],
            'summary': {},
            'error': None,
            'dismissals': {},
            'brand_config': brand_config or {},
        }
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        return dict(_jobs[job_id]) if job_id in _jobs else None


def list_jobs() -> list:
    with _jobs_lock:
        return [dict(j) for j in sorted(_jobs.values(), key=lambda x: x['created'], reverse=True)]


def set_dismissal(job_id: str, filename: str, check_name: str,
                  issue_index: int, dismissed: bool) -> bool:
    with _jobs_lock:
        if job_id not in _jobs:
            return False
        key = f"{filename}|{check_name}|{issue_index}"
        if dismissed:
            _jobs[job_id]['dismissals'][key] = True
        else:
            _jobs[job_id]['dismissals'].pop(key, None)
    _save_job_to_disk(job_id)
    return True


def _update_job(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


def start_job(job_id: str, pdf_paths: list, gtin_rows: list, work_dir: str,
              brand_config: dict = None, spec_rows: list = None):
    t = threading.Thread(
        target=_process_job,
        args=(job_id, pdf_paths, gtin_rows, work_dir, brand_config, spec_rows),
        daemon=True,
    )
    t.start()


# ── Disk persistence ──────────────────────────────────────────────────────────

def _save_job_to_disk(job_id: str):
    with _jobs_lock:
        if job_id not in _jobs:
            return
        job = dict(_jobs[job_id])
    job_file = os.path.join(_UPLOAD_DIR, job_id, 'job.json')
    try:
        with open(job_file, 'w') as f:
            json.dump(job, f)
    except Exception:
        pass


def load_jobs_from_disk():
    """Load previously saved jobs from disk. Called once at startup."""
    if not os.path.exists(_UPLOAD_DIR):
        return
    for entry in os.listdir(_UPLOAD_DIR):
        job_file = os.path.join(_UPLOAD_DIR, entry, 'job.json')
        if not os.path.exists(job_file):
            continue
        try:
            with open(job_file) as f:
                job = json.load(f)
            with _jobs_lock:
                if job.get('id') and job['id'] not in _jobs:
                    _jobs[job['id']] = job
        except Exception:
            pass


# Load history on import
load_jobs_from_disk()


# ── Main processing loop ──────────────────────────────────────────────────────

def _process_job(job_id: str, pdf_paths: list, gtin_rows: list, work_dir: str,
                 brand_config: dict = None, spec_rows: list = None):
    _update_job(job_id, status='running')
    total = len(pdf_paths)
    results = [None] * total
    completed = [0]  # mutable counter shared across threads

    def _run_one(i: int, pdf_path: str):
        fname = os.path.basename(pdf_path)
        try:
            return i, _proof_single(pdf_path, gtin_rows, work_dir,
                                    brand_config=brand_config, spec_rows=spec_rows)
        except Exception as exc:
            return i, {
                'filename': fname, 'error': str(exc), 'checks': {},
                'severity': 'error', 'critical_count': 0,
                'warning_count': 0, 'info_count': 0, 'img_web': None,
            }

    max_workers = min(total, max(1, (os.cpu_count() or 2)))
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run_one, i, p): i for i, p in enumerate(pdf_paths)}
            for fut in as_completed(futures):
                i, result = fut.result()
                results[i] = result
                completed[0] += 1
                _update_job(job_id,
                            current_file=f'{completed[0]} of {total} file{"s" if total > 1 else ""} done',
                            progress=int(completed[0] / total * 90))

        summary = _build_summary(results)
        _update_job(job_id, status='done', progress=100,
                    results=results, summary=summary, current_file='')
    except Exception as exc:
        _update_job(job_id, status='error', error=str(exc), progress=0)

    _save_job_to_disk(job_id)


# ── Film vs pouch detection ───────────────────────────────────────────────────

_FILM_KEYWORDS  = {'stick', 'sachet', 'flow', 'rollstock', 'film', 'sleeve',
                   'wrapper', 'wrap', 'stickpack', 'stick_pack', 'stick-pack'}
_POUCH_KEYWORDS = {'pouch', 'bag', 'zip', 'mylar', 'doypack', 'doypak',
                   'standup', 'stand_up', 'stand-up', 'canister', 'jar',
                   'bottle', 'tub', 'container'}


def _is_film_rollstock(fname: str, ocr_text: str = '') -> bool:
    """Return True only when the design is clearly identified as film/rollstock.
    Defaults to False (skip eyemark check) when format is ambiguous — the check
    is meaningless on pouches and should not produce false positives.
    """
    name = fname.lower()
    text = ocr_text.lower()

    # Explicit pouch/bag indicators → not film
    for kw in _POUCH_KEYWORDS:
        if kw in name:
            return False

    # Explicit film/stick-pack indicators → apply eyemark
    for kw in _FILM_KEYWORDS:
        if kw in name:
            return True

    # Fall back to OCR text hints
    if any(kw in text for kw in _POUCH_KEYWORDS):
        return False
    if any(kw in text for kw in _FILM_KEYWORDS):
        return True

    # Cannot determine — default to skipping so pouches don't generate false positives.
    # Rename the file to include "stick", "sachet", or "film" to enable this check.
    return False


# ── Spec row matching ─────────────────────────────────────────────────────────

_SPEC_GENERIC = {
    'whey', 'protein', 'powder', 'stick', 'sticks', 'pouch', 'pouches',
    'bag', 'bags', 'bar', 'bars', 'single', 'prodough', 'pro', 'dough',
    'pack', 'sachet', 'sachets', 'blend', 'mix', 'sport', 'sports',
    'plant', 'based', 'vegan',
    'the', 'a', 'an', 'and', 'of', 'with', 'for', 'to',
}


def _match_spec_row(gtin_list: list, fname: str, spec_rows: list,
                    pdf_text: str = '') -> dict:
    """Match a spec row by GTIN first, then by PDF text + filename keyword matching."""
    if not spec_rows:
        return {}

    # 1. Exact GTIN match
    for gtin in (gtin_list or []):
        gtin_str = str(gtin).strip()
        for row in spec_rows:
            if str(row.get('gtin', '')).strip() == gtin_str:
                return row

    # 2. Keyword match — check PDF text first, fall back to filename
    fname_lower = fname.lower()
    pdf_lower = pdf_text.lower()
    for row in spec_rows:
        flavor = str(row.get('flavor', '')).strip().lower()
        if not flavor:
            continue
        flavor_words = re.findall(r'[a-z0-9]+', flavor)
        keywords = [w for w in flavor_words if w not in _SPEC_GENERIC and len(w) > 1]
        if not keywords:
            continue
        if all(kw in pdf_lower or kw in fname_lower for kw in keywords):
            return row

    return {}


# ── Single-file proofing ──────────────────────────────────────────────────────

def _proof_single(pdf_path: str, gtin_rows: list, work_dir: str,
                  brand_config: dict = None, spec_rows: list = None) -> dict:
    brand_config = brand_config or {}
    fname = os.path.basename(pdf_path)
    stem = Path(pdf_path).stem

    # ── Convert PDF → PNG at 200 DPI (36% fewer pixels than 250 DPI) ──────────
    img_prefix = os.path.join(work_dir, stem)
    subprocess.run(
        ['pdftoppm', '-r', '200', '-png', '-singlefile', pdf_path, img_prefix],
        capture_output=True, check=True, timeout=120,
    )
    img_path = img_prefix + '.png'
    if not os.path.exists(img_path):
        candidates = sorted(
            f for f in os.listdir(work_dir)
            if f.startswith(stem) and f.endswith('.png')
        )
        if not candidates:
            raise FileNotFoundError(f'pdftoppm produced no PNG for {fname}')
        img_path = os.path.join(work_dir, candidates[0])

    # ── Native PDF text extraction + keep doc open for Check 7 ─────────────────
    fitz_doc = None
    native_text = ''
    try:
        import fitz as _fitz
        fitz_doc = _fitz.open(pdf_path)
        native_text = '\n'.join(fitz_doc[i].get_text() for i in range(len(fitz_doc)))
        # Keep fitz_doc open — passed to _check_print_specs to avoid double parse
    except Exception:
        pass

    # ── OCR (Tesseract) ────────────────────────────────────────────────────────
    # Skip Tesseract only when native text already contains key label signals
    # (NFP header, ingredients, net weight, etc.). This catches the press-proof
    # case where the editable form fields give 100+ words of native text but the
    # actual artwork content is outlined/vectorized and invisible to PyMuPDF —
    # without Tesseract all absence-based checks fire as false positives.
    _label_signals = [
        'nutrition facts', 'supplement facts', 'serving size', 'calories',
        'ingredients', 'contains:', 'net wt', 'net weight',
    ]
    _native_has_label = any(sig in native_text.lower() for sig in _label_signals)
    ocr_text = ''
    if not _native_has_label:
        try:
            r = subprocess.run(
                ['tesseract', img_path, 'stdout', '--oem', '1', '--psm', '3', '-l', 'eng'],
                capture_output=True, text=True, timeout=30,
            )
            ocr_text = r.stdout
        except Exception:
            pass

    # Merge: native text is authoritative where it exists; OCR fills the gaps
    combined_text = native_text + '\n' + ocr_text

    # ── Load image for visual checks ─────────────────────────────────────────
    img = None
    if PIL_AVAILABLE:
        try:
            img = Image.open(img_path).convert('RGB')
        except Exception:
            pass

    # Scan barcode stripes — primary GTIN source
    barcode_gtins = _scan_barcodes(img_path)
    if not barcode_gtins:
        # 200 DPI sometimes misses dense or small barcodes — retry at 350 DPI
        hi_prefix = img_prefix + '_hirez'
        try:
            subprocess.run(
                ['pdftoppm', '-r', '350', '-png', '-singlefile', pdf_path, hi_prefix],
                capture_output=True, timeout=60,
            )
            hi_path = hi_prefix + '.png'
            if not os.path.exists(hi_path):
                cands = sorted(f for f in os.listdir(work_dir)
                               if f.startswith(os.path.basename(hi_prefix)) and f.endswith('.png'))
                if cands:
                    hi_path = os.path.join(work_dir, cands[0])
            if os.path.exists(hi_path):
                barcode_gtins = _scan_barcodes(hi_path)
        except Exception:
            pass

    brand_mode = brand_config.get('brand_mode', 'prodough')

    # ── Match spec row from sheet ────────────────────────────────────────────
    matched_spec = _match_spec_row(barcode_gtins, fname, spec_rows or [], combined_text)

    # Wind direction: form override > spec sheet > nothing
    effective_wind = brand_config.get('wind_direction', '').strip()
    if not effective_wind and matched_spec.get('wind_direction'):
        effective_wind = matched_spec['wind_direction']

    if brand_mode == 'generic':
        is_film = brand_config.get('packaging_type', 'other') == 'stick'
        brand_name = brand_config.get('brand_name', '')
        checks = {
            'gtin':     _check_gtin(combined_text, fname, gtin_rows, barcode_gtins),
            'eyemark':  _check_eyemark(img, is_film, fname),
            'spelling': _check_spelling(combined_text, brand_name=brand_name),
            'fda':      _check_fda_light(combined_text, fname),
            'specs':    _check_print_specs(fitz_doc, brand_config, matched_spec),
        }
        if is_film or effective_wind:
            checks['wind'] = _check_wind_direction(combined_text, effective_wind)
    else:
        # ProDough mode
        is_film = _is_film_rollstock(fname, combined_text)
        checks = {
            'gtin':     _check_gtin(combined_text, fname, gtin_rows, barcode_gtins),
            'nfp':      _check_nfp(combined_text),
            'eyemark':  _check_eyemark(img, is_film, fname),
            'spelling': _check_spelling(combined_text),
            'fda':      _check_fda(combined_text, fname),
            'specs':    _check_print_specs(fitz_doc, brand_config, matched_spec),
        }
        checks['wind'] = _check_wind_direction(combined_text, effective_wind)

    # Close the PyMuPDF document now that all checks are done
    if fitz_doc is not None:
        try:
            fitz_doc.close()
        except Exception:
            pass

    all_issues = [i for c in checks.values() for i in c.get('issues', [])]
    crit  = [i for i in all_issues if i['severity'] == 'critical']
    warns = [i for i in all_issues if i['severity'] == 'warning']
    infos = [i for i in all_issues if i['severity'] == 'info']

    if crit:
        severity = 'critical'
    elif warns:
        severity = 'warning'
    elif infos:
        severity = 'info'
    else:
        severity = 'clean'

    return {
        'filename': fname,
        'img_path': img_path,
        'img_web': img_path,
        'ocr_preview': combined_text[:3000],
        'checks': checks,
        'severity': severity,
        'critical_count': len(crit),
        'warning_count': len(warns),
        'info_count': len(infos),
        'error': None,
        'matched_spec': matched_spec,
    }


# ── Barcode scanning ─────────────────────────────────────────────────────────

def _scan_barcodes(img_path: str) -> list:
    """Decode UPC-A / EAN-13 barcode stripes directly from the rendered PNG.
    Works on print-ready PDFs where text is outlined — reads the bar pattern,
    not OCR text.  Returns a list of 12-digit GTIN strings.
    """
    if not PYZBAR_AVAILABLE or not PIL_AVAILABLE:
        return []
    try:
        img = Image.open(img_path).convert('RGB')
        decoded = _pyzbar_decode(img)
        gtins = []
        for d in decoded:
            raw = d.data.decode('utf-8', errors='replace').strip()
            if not raw.isdigit():
                continue
            if len(raw) == 13 and raw.startswith('0'):
                raw = raw[1:]   # EAN-13 with leading zero → UPC-A 12-digit
            if len(raw) == 12:
                gtins.append(raw)
        return list(dict.fromkeys(gtins))  # deduplicate, preserve order
    except Exception:
        return []


# ── Check 1: GTIN / barcode ───────────────────────────────────────────────────

def _check_gtin(ocr_text: str, fname: str, gtin_rows: list,
                scanned_gtins: list = None) -> dict:
    issues, notes = [], []
    scanned_gtins = scanned_gtins or []

    # Primary: direct barcode-stripe decode (works on all PDFs, outlined or not)
    if scanned_gtins:
        found = scanned_gtins
        notes.append(
            f'Barcode decoded directly from artwork image: {", ".join(found)}. '
            'This reads the actual barcode stripes, not OCR text.'
        )
    else:
        # Fallback: OCR text search for human-readable digits below barcode
        raw12 = re.findall(r'\b(\d{12})\b', ocr_text)
        partial = re.findall(r'\b(\d{5,7})\s+(\d{4,7})\b', ocr_text)
        for a, b in partial:
            combined = a + b
            if len(combined) in (11, 12):
                raw12.append(combined)

        found = list({g for g in raw12 if g[:3] in ('850', '840', '860', '870', '880', '890', '012', '075', '049')})
        if not found:
            found = list(set(raw12))

        if found:
            notes.append(
                f'GTIN(s) found via OCR of human-readable digits: {", ".join(found)}. '
                'Barcode stripe scanning was unavailable or did not detect a barcode — '
                'verify this number matches the actual barcode on the artwork.'
            )
        else:
            issues.append({
                'severity': 'warning',
                'message': (
                    'No barcode detected on this artwork. The barcode scanner could not read the stripes '
                    'and no 12-digit number was found via OCR. Possible causes: barcode is very small, '
                    'heavily styled, cropped to the edge, or missing entirely. '
                    'Verify the barcode is present and correct on the actual artwork file.'
                ),
            })

    if gtin_rows and found:
        gtin_lookup = {str(r.get('gtin', '')).strip(): str(r.get('flavor', '')).strip().lower()
                       for r in gtin_rows if r.get('gtin')}
        # Search the actual PDF text content, not the filename
        pdf_text_lower = ocr_text.lower()
        # Generic words that carry no flavor/format identity
        _GENERIC = {
            'whey', 'protein', 'powder', 'stick', 'sticks', 'pouch', 'pouches',
            'bag', 'bags', 'bar', 'bars', 'single', 'prodough', 'pro', 'dough',
            'pack', 'sachet', 'sachets', 'blend', 'mix', 'sport', 'sports',
            'plant', 'based', 'vegan',
            'the', 'a', 'an', 'and', 'of', 'with', 'for', 'to',
        }
        for gtin in found:
            if gtin in gtin_lookup:
                expected_flavor = gtin_lookup[gtin]
                if not expected_flavor:
                    continue
                # Extract meaningful keywords from the master-list flavor name
                flavor_words = re.findall(r'[a-z0-9]+', expected_flavor)
                keywords = [w for w in flavor_words if w not in _GENERIC and len(w) > 1]
                if not keywords:
                    continue  # nothing meaningful to match against
                # Match against PDF text content (OCR + native), fall back to filename
                fname_lower = fname.lower()
                matched = [kw for kw in keywords if kw in pdf_text_lower or kw in fname_lower]
                if len(matched) < len(keywords):
                    missing = [kw for kw in keywords if kw not in pdf_text_lower and kw not in fname_lower]
                    issues.append({
                        'severity': 'warning',
                        'message': (
                            f'GTIN {gtin} is listed under "{expected_flavor}" in the master list, '
                            f'but the keyword(s) "{", ".join(missing)}" were not found in the artwork text '
                            f'or filename. Confirm this is the correct SKU.'
                        ),
                    })
                # If all keywords matched, the flavor lines up — no issue raised
            else:
                issues.append({
                    'severity': 'warning',
                    'message': f'GTIN {gtin} was not found in the uploaded master list. Verify it belongs to this SKU.',
                })

    return {'found_gtins': found, 'issues': issues, 'notes': notes}


# ── Check 2: Front call-outs vs NFP ──────────────────────────────────────────

def _check_nfp(ocr_text: str) -> dict:
    issues, notes = [], []
    tl = ocr_text.lower()

    has_nfp = 'nutrition facts' in tl or 'supplement facts' in tl
    if not has_nfp:
        notes.append(
            'Nutrition Facts (or Supplement Facts) panel not detected via OCR. '
            'Print-ready PDFs with text converted to outlines will not OCR — '
            'verify manually that the NFP is present and correctly formatted.'
        )

    cal_hits = re.findall(r'calories\s+(\d+)|(\d+)\s+calories', tl)
    calories = sorted({int(a or b) for a, b in cal_hits if 30 <= int(a or b) <= 800})

    prot_hits = re.findall(r'protein\s+(\d+)\s*g|(\d+)\s*g\s+protein', tl)
    proteins = sorted({int(a or b) for a, b in prot_hits if 0 < int(a or b) < 120})

    nw_hits = re.findall(r'net\s*wt\.?\s*([\d.]+)\s*g', tl)

    if len(set(calories)) > 1:
        diff = max(calories) - min(calories)
        if diff > 5:
            issues.append({
                'severity': 'warning',
                'message': (
                    f'Multiple calorie values detected ({", ".join(str(c) for c in calories)} cal). '
                    'If the front call-out and NFP show different values, this is an FDA labeling violation.'
                ),
            })

    if len(set(proteins)) > 1:
        if max(proteins) - min(proteins) > 1:
            issues.append({
                'severity': 'warning',
                'message': (
                    f'Multiple protein values detected ({", ".join(str(p) for p in proteins)}g). '
                    'Front call-out must match the NFP protein grams.'
                ),
            })

    if re.search(r'0\s*g\s+added\s+sugar', tl):
        if not re.search(r'added\s+sugars?\s+0\s*g|added\s+sugars?\s*\n?\s*0', tl):
            notes.append(
                '"0G Added Sugar" front claim detected. '
                'Verify the NFP shows 0g for Added Sugars.'
            )

    return {
        'has_nfp': has_nfp,
        'calories': calories,
        'proteins': proteins,
        'net_weights': nw_hits,
        'issues': issues,
        'notes': notes,
    }


# ── Check 3: Eyemark color ────────────────────────────────────────────────────
# Rule: eyemark MUST be solid black (#000000) or solid white (#FFFFFF).
# Any other color will cause unreliable photo-eye detection on the production line.

def _check_eyemark(img, is_film: bool = False, fname: str = '') -> dict:
    issues, notes = [], []

    if not is_film:
        notes.append(
            'Eyemark check skipped — not identified as film/rollstock. '
            'Include "stick", "sachet", "film", or "rollstock" in the filename to enable.'
        )
        return {'issues': issues, 'notes': notes, 'eyemark_color': None, 'skipped': True}

    if img is None:
        issues.append({
            'severity': 'warning',
            'message': 'Image unavailable — eyemark color check could not be performed.',
        })
        return {'issues': issues, 'notes': notes, 'eyemark_color': None}

    w, h = img.size
    cw = max(1, int(w * 0.10))  # corner width  — 10% of image width
    ch = max(1, int(h * 0.08))  # corner height — 8% of image height

    # Sample all 4 corners + center of each edge (6 regions total).
    # Eyemarks appear in different positions depending on press layout — scanning
    # all candidate regions finds them regardless of placement.
    regions = {
        'bottom-right': (w - cw, h - ch, w, h),
        'bottom-left':  (0,      h - ch, cw, h),
        'top-right':    (w - cw, 0,      w,  ch),
        'top-left':     (0,      0,      cw, ch),
        'bottom-center':(w // 2 - cw // 2, h - ch, w // 2 + cw // 2, h),
        'top-center':   (w // 2 - cw // 2, 0,      w // 2 + cw // 2, ch),
    }

    def _score_region(crop):
        """Return (eyemark_color, min_luma, max_luma, avg_luma, confidence).
        confidence is how extreme the luminance is (higher = more likely an eyemark).
        """
        pixels = list(crop.getdata())
        if not pixels:
            return None, 255, 0, 128, 0
        lumas = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels]
        mn, mx, avg = min(lumas), max(lumas), sum(lumas) / len(lumas)
        black_frac = sum(1 for l in lumas if l < 25) / len(lumas)
        white_frac = sum(1 for l in lumas if l > 230) / len(lumas)
        if black_frac >= 0.15:
            return 'black', mn, mx, avg, black_frac
        if white_frac >= 0.85:
            return 'white', mn, mx, avg, white_frac
        return 'none', mn, mx, avg, 0

    best_color = 'none'
    best_confidence = 0
    best_loc = ''
    best_lumas = (255, 0, 128)

    for loc, box in regions.items():
        crop = img.crop(box)
        color, mn, mx, avg, conf = _score_region(crop)
        if color in ('black', 'white') and conf > best_confidence:
            best_color, best_confidence, best_loc, best_lumas = color, conf, loc, (mn, mx, avg)

    eyemark_color = best_color
    min_luma, max_luma, avg_luma = best_lumas

    if eyemark_color == 'black':
        notes.append(
            f'✔ BLACK eyemark detected at {best_loc} (darkest pixel: {min_luma:.0f}/255) — OK. '
            'Solid black eyemark provides reliable photo-eye detection.'
        )
    elif eyemark_color == 'white':
        notes.append(
            f'✔ WHITE eyemark detected at {best_loc} (lightest pixel: {max_luma:.0f}/255) — OK. '
            'Solid white eyemark provides reliable photo-eye detection.'
        )
    else:
        if avg_luma < 85:
            color_desc = f'dark grey or a dark color (avg brightness {avg_luma:.0f}/255)'
        elif avg_luma < 170:
            color_desc = f'medium grey or a spot color (avg brightness {avg_luma:.0f}/255)'
        else:
            color_desc = f'light grey or a light color (avg brightness {avg_luma:.0f}/255)'

        issues.append({
            'severity': 'critical',
            'message': (
                f'Eyemark not detected as solid black or white in any corner or edge region — '
                f'the brightest candidate region was {color_desc}. '
                'The production line photo-eye sensor requires a solid BLACK (#000000) '
                'or solid WHITE (#FFFFFF) eyemark. A colored or grey eyemark WILL cause '
                'missed or false triggers on the bagger/sealer. '
                'Change the eyemark to pure black or pure white before going to press.'
            ),
        })

    return {'issues': issues, 'notes': notes, 'eyemark_color': eyemark_color}


# ── Check 4: Spelling / brand name ───────────────────────────────────────────
#
# Three tiers — higher tiers are warnings, lower tiers are notes:
#
# Tier 1 (critical) — Brand name & flavor names. These appear prominently on
#   the front panel and are the most visible errors to consumers and regulators.
#
# Tier 2 (warning) — Front call-out and marketing copy. Common misspellings in
#   claim language (protein, natural, artificial, nutritional, excellent).
#
# Tier 3 (note) — Body text / supporting copy. UK vs US spellings; patterns
#   that only appear in ingredient declarations or fine print.

_SPELLING_TIER1 = {
    # Brand name
    r'pro\s+dough':     'ProDough (one word, no space)',
    # Flavor names — most likely to appear in large display type
    r'\bcheescake\b':   'cheesecake',
    r'\bbanna\b':       'banana',
    r'\bchoclate\b':    'chocolate',
    r'\bvanila\b':      'vanilla',
    r'\bcarmel\b':      'caramel',
    r'\bcinamon\b':     'cinnamon',
    r'\bstrwberry\b':   'strawberry',
    r'\brasberry\b':    'raspberry',
    r'\braspbery\b':    'raspberry',
    r'\bpeanut\s+budder\b': 'peanut butter',
    r'\bcookeis\b':     'cookies',
    r'\bbrowny\b':      'brownie',
}

_SPELLING_TIER2 = {
    # Front call-out / marketing claims
    r'\bprotien\b':     'protein',
    r'\bnatrual\b':     'natural',
    r'\bartifical\b':   'artificial',
    r'\bnutrional\b':   'nutritional',
    r'\bexellent\b':    'excellent',
    r'\bsuplements?\b': 'supplement',
    r'\bbenifits?\b':   'benefit',
    r'\bvitiman\b':     'vitamin',
    r'\baminoacid\b':   'amino acid',
    r'\bglutamine\b':   'glutamine',   # common supplement term, worth checking
}

_SPELLING_TIER3 = {
    # Body / fine print — lower visibility, but still flagged
    r'\bingrediant':    'ingredient',
    r'\bpreservitive\b':'preservative',
    r'\bcolestrol\b':   'cholesterol',
    r'\bdietary\s+fible\b': 'dietary fiber',
    r'\bcontians\b':    'contains',
    r'\bdistribted\b':  'distributed',
    r'\bmanufactred\b': 'manufactured',
}


def _check_spelling(ocr_text: str, brand_name: str = 'ProDough') -> dict:
    """Tiered spelling check. Pass brand_name='ProDough' (default) for ProDough mode,
    or any other brand name for generic mode (skips ProDough-specific brand patterns).
    """
    issues, notes = [], []
    is_prodough = brand_name.strip().lower() == 'prodough'

    tier1 = _SPELLING_TIER1 if is_prodough else {k: v for k, v in _SPELLING_TIER1.items() if 'dough' not in k}
    for pattern, correction in tier1.items():
        if re.search(pattern, ocr_text, re.IGNORECASE):
            issues.append({
                'severity': 'critical',
                'message': (
                    f'[Tier 1 — Brand/Flavor] Misspelling detected: should be "{correction}". '
                    'Verify on the actual artwork file (OCR on outlined text can produce false positives).'
                ),
            })

    for pattern, correction in _SPELLING_TIER2.items():
        if re.search(pattern, ocr_text, re.IGNORECASE):
            issues.append({
                'severity': 'warning',
                'message': (
                    f'[Tier 2 — Call-Out/Claims] Possible misspelling: should be "{correction}". '
                    'Verify on the actual artwork file.'
                ),
            })

    for pattern, correction in _SPELLING_TIER3.items():
        if re.search(pattern, ocr_text, re.IGNORECASE):
            notes.append(
                f'[Tier 3 — Body Text] Possible misspelling: should be "{correction}". '
                'Verify on the actual artwork file.'
            )

    if re.search(r'\bflavour\b', ocr_text, re.IGNORECASE):
        notes.append('[Tier 3] UK spelling "flavour" detected — US labels should use "flavor".')

    if is_prodough:
        notes.append(
            'ProDough® wordmark: verify capital P, capital D, no space, and the ® symbol appear correctly. '
            'Social handles (@prodoughshop) and website URLs are excluded from this check.'
        )
    elif brand_name:
        notes.append(f"Verify brand name '{brand_name}' is spelled correctly throughout.")

    return {'issues': issues, 'notes': notes}


# ── Module-level FDA regex (shared between _check_fda and _check_fda_light) ───

_DISCLAIMER_RE = re.compile(
    r'(?:this\s+)?statement[s]?\s+ha(?:s|ve)\s+not\s+been\s+evaluated'
    r'.{0,400}?'
    r'not\s+intended\s+to\s+diagnose.{0,120}disease',
    re.IGNORECASE | re.DOTALL,
)

# ── Check 5: FDA compliance ───────────────────────────────────────────────────

_DISEASE_CLAIM_PATTERNS = [
    (r'\btreat[s]?\b.{0,40}(disease|disorder|condition|syndrome)',
     'disease treatment claim — prohibited on food/supplement labels without FDA approval'),
    (r'\bcure[s]?\b.{0,40}(disease|disorder|cancer|diabetes)',
     'disease cure claim — prohibited without FDA approval'),
    (r'\bprevent[s]?\b.{0,40}(disease|cancer|diabetes|heart disease|stroke)',
     'disease prevention claim — prohibited without FDA approval (or an approved health claim)'),
    (r'lower[s]?\s+(your\s+)?cholesterol\b',
     '"lowers cholesterol" — authorized health claim requiring specific FDA-approved language (21 CFR 101.75)'),
    (r'reduc[es]+\s+.{0,20}risk\s+of\s+(cancer|diabetes|heart|stroke)',
     'disease risk-reduction claim — requires an FDA-approved health claim (21 CFR 101.14)'),
    (r'\bdiagnose[s]?\b.{0,30}(disease|disorder)',
     'diagnostic claim — prohibited on food/supplement labels'),
]

_SF_CLAIM_PATTERNS = [
    r'supports?\s+(muscle|digestion|immunity|gut\s+health|joint|bone|brain|cognitive|energy|weight\s+management)',
    r'promotes?\s+(muscle|recovery|digestion|immunity|gut|joint|bone|brain|lean\s+muscle)',
    r'helps?\s+maintain\s+(muscle|energy|immunity|weight|gut)',
    r'improves?\s+(recovery|performance|endurance|strength|focus)',
    r'boosts?\s+(metabolism|energy|immunity|performance|focus)',
    r'enhances?\s+(performance|recovery|endurance)',
]


def _ocr_is_sparse(ocr_text: str) -> bool:
    words = [w for w in ocr_text.split() if len(w) > 2]
    return len(words) < 80


def _check_fda(ocr_text: str, fname: str) -> dict:
    issues, notes = [], []
    tl = ocr_text.lower()

    is_supplement = 'supplement facts' in tl
    sparse = _ocr_is_sparse(ocr_text)

    if sparse:
        notes.append(
            'Low OCR yield — this PDF likely uses text converted to outlines (standard for print-ready files). '
            'The absence-based checks below (NFP, ingredients, allergens, manufacturer info) cannot be reliably '
            'automated on outlined-text PDFs. Use the Manual Review page to zoom in and verify each element '
            'directly on the rendered artwork image.'
        )

    # ── Required label elements (absence-based — unreliable on outlined PDFs) ─
    # Only flag these if OCR has meaningful yield; otherwise they are noise.

    if not sparse:
        # NFP: accept explicit text OR numerical signals that only appear inside an NFP
        # (calories + protein both present → panel almost certainly exists even if
        #  "Nutrition Facts" header text was outlined and OCR-unreadable)
        _has_nfp_text    = 'nutrition facts' in tl or 'supplement facts' in tl
        # Either word alone is strong signal — outlined text often drops the "Facts" header
        # but OCR still reads nutritional values / row labels from the panel
        _has_nfp_numbers = (
            bool(re.search(r'\bcalories?\b', tl)) or
            bool(re.search(r'\bprotein\b', tl))
        )
        # Any single typical NFP row label is sufficient evidence the panel exists
        _nfp_row_signals = [r'\btotal\s*fat\b', r'\bsodium\b', r'\bcarbohydrate\b',
                            r'\bdietary\s*fiber\b', r'\b%\s*dv\b', r'%\s*daily\s*value',
                            r'\bserving\s*size\b', r'\bservings?\s*per\b',
                            r'\btotal\s*carb\b', r'\bsaturated\s*fat\b',
                            r'\btrans\s*fat\b', r'\bcholesterol\b', r'\bsugars?\b']
        _nfp_row_hits = sum(1 for p in _nfp_row_signals if re.search(p, tl))
        if not _has_nfp_text and not _has_nfp_numbers and _nfp_row_hits == 0:
            issues.append({
                'severity': 'warning',
                'message': (
                    'Nutrition Facts (or Supplement Facts) panel not detected via OCR. '
                    'Required on all packaged food and dietary supplement labels (21 CFR 101.9 / 101.36). '
                    'Verify manually on the actual artwork.'
                ),
            })

        # Ingredients: accept explicit label OR common ingredient words that only
        # appear inside an ingredient declaration on a food label
        _ingredient_signals = [
            r'\bingredient',
            r'\bwhey\b', r'\blecithin\b', r'\bstevia\b', r'\bsucralose\b',
            r'\bsunflower\b', r'\bcitric\s+acid\b', r'\bsoy\b', r'\bxanthan\b',
            r'\bnatural\s+flavor', r'\bartificial\s+flavor',
            r'\bmilk\s+powder\b', r'\bnon.fat\s+milk\b', r'\bskim\s+milk\b',
            r'\bcocoa\b', r'\bsalt\b', r'\bpotassium\b', r'\bvitamin\b',
        ]
        _has_ingredients = any(re.search(p, tl) for p in _ingredient_signals)
        if not _has_ingredients:
            issues.append({
                'severity': 'warning',
                'message': (
                    'Ingredient list not detected via OCR. '
                    'Required on virtually all packaged food labels (21 CFR 101.4). '
                    'Verify manually on the actual artwork.'
                ),
            })
    else:
        # Collapsed note for sparse OCR — avoid flooding the report with absence warnings
        notes.append(
            'Required label elements (NFP, ingredients, allergens, manufacturer info, net weight) '
            'could not be verified via OCR due to outlined text. '
            'Verify each of these is present and correctly formatted directly on the artwork file.'
        )

    # ── Allergen declaration ───────────────────────────────────────────────────
    has_allergen_stmt = any(re.search(p, tl) for p in
                            [r'contains?:', r'\ballergen\b', r'allergy\s+info', r'may\s+contain'])

    is_whey_product  = any(re.search(p, fname.lower() + ' ' + tl)
                          for p in [r'\bwhey\b', r'protein\s+stick', r'protein\s+powder',
                                    r'\bstick\b', r'\bsticks\b', r'\bpouch\b', r'\bpouches\b'])
    is_wheat_product = any(re.search(p, fname.lower())
                          for p in [r'\bpancake\b', r'\bdonut\b', r'\bflour\b', r'\bbreading\b'])

    # Milk evidence: explicit word, or milk-derived ingredients that OCR from outlined fonts
    _milk_in_ocr = bool(re.search(
        r'\bmilk\b|\bnon.fat\s+milk\b|\bmilk\s+powder\b|\bskim\s+milk\b|\bdairy\b|\bcasein\b', tl
    ))
    # If it's a whey/stick protein product, the product type implies milk is present.
    # Only fire if NEITHER the filename NOR any OCR evidence suggests milk/whey.
    # Filename containing "whey" identifies the product type but does NOT prove the
    # allergen is declared on the label — only check OCR/native text for that.
    if is_whey_product and not _milk_in_ocr and 'whey' not in tl:
        issues.append({
            'severity': 'warning',
            'message': (
                'Whey/milk protein product — "milk" allergen declaration not detected via OCR. '
                'FDA FALCPA (21 USC 343(w)) requires milk to be declared as a major food allergen. '
                'Verify the allergen statement is present on the actual artwork.'
            ),
        })
    elif is_wheat_product and 'wheat' not in tl:
        issues.append({
            'severity': 'warning',
            'message': (
                'Wheat-containing product type — "wheat" allergen not detected via OCR. '
                'FALCPA requires wheat to be declared as a major food allergen (21 USC 343(w)). '
                'Verify the allergen statement appears on the actual artwork.'
            ),
        })
    elif not is_whey_product and not is_wheat_product and not has_allergen_stmt and not sparse:
        issues.append({
            'severity': 'warning',
            'message': (
                'No allergen declaration (e.g., "Contains: Milk") detected via OCR. '
                'FALCPA requires declaration of the 9 major allergens. Verify manually.'
            ),
        })

    # ── Manufacturer / distributor info ───────────────────────────────────────
    if not sparse:
        _mfr_indicators = [
            'manufactured by', 'distributed by', 'produced by', 'manufactured for',
            'distributed for', 'packed by', 'bottled by',
            'llc', 'inc.', 'corp.', 'company', 'co.', 'group', 'enterprises',
            'nutrition', 'foods', 'labs', 'ops', 'industries', 'international',
        ]
        has_mfr_text = any(ind in tl for ind in _mfr_indicators)
        has_zip      = bool(re.search(r'\b\d{5}\b', ocr_text))
        has_street   = bool(re.search(
            r'\b\d+\s+[NSEW]\.?\s+\d+|\b\d+\s+\w+\s+(st|ave|blvd|dr|rd|ln|way|pkwy|court|ct)\b', tl
        ))

        if not (has_mfr_text or has_zip or has_street):
            issues.append({
                'severity': 'warning',
                'message': (
                    'Manufacturer or distributor name/address not detected via OCR. '
                    '21 CFR 101.5 requires the name and place of business of the manufacturer, packer, '
                    'or distributor. Verify it appears on the actual artwork.'
                ),
            })

    # ── Net weight ────────────────────────────────────────────────────────────
    # Accept explicit "Net Wt" text OR any gram/oz measurement.
    # Also skip the flag when the NFP panel is confirmed present — FDA requires
    # net weight on any label that carries a Nutrition Facts panel, so detecting
    # the NFP is sufficient evidence the declaration exists.
    _has_net_wt = (
        bool(re.search(r'net\s*wt\.?|net\s*weight', tl)) or
        bool(re.search(r'\b\d+\.?\d*\s*(?:oz|lbs?|pounds?|kg|g)\b', tl))
    )
    _nfp_confirmed = _has_nfp_text or _has_nfp_numbers or _nfp_row_hits >= 1
    if not sparse and not _has_net_wt and not _nfp_confirmed:
        issues.append({
            'severity': 'warning',
            'message': (
                'No "Net Wt" declaration or weight measurement detected. '
                'Net quantity of contents is required (15 USC 1453 / 21 CFR 101.105). Verify manually.'
            ),
        })
    elif re.search(r'net\s*wt\.?|net\s*weight', tl):
        has_g   = bool(re.search(r'net\s*wt.{0,20}\d+\s*g\b', tl))
        has_oz  = bool(re.search(r'\d+\.?\d*\s*(?:oz|lbs?|pounds?)\b', tl))
        if has_g and not has_oz:
            notes.append(
                'Net weight appears in grams only. '
                'US regulations generally require both metric (g) and US customary (oz) units. '
                'Verify with your legal/regulatory team.'
            )

    # ── % Daily Values footnote ───────────────────────────────────────────────
    if re.search(r'%\s*daily\s*value', tl) and 'based on a 2,000 calorie' not in tl and '2,000 calorie diet' not in tl:
        notes.append(
            '% Daily Values listed but the required footnote — '
            '"Percent Daily Values are based on a 2,000 calorie diet" — '
            'was not detected (21 CFR 101.9(d)(9)).'
        )

    # ── Disease claims ────────────────────────────────────────────────────────
    # Strip the required FDA disclaimer before scanning — it contains "treat",
    # "cure", "diagnose", and "prevent any disease" which would otherwise
    # trigger false positives on every label that correctly includes the disclaimer.
    disclaimer_present = bool(_DISCLAIMER_RE.search(tl))
    tl_no_disclaimer = _DISCLAIMER_RE.sub('', tl)

    if disclaimer_present:
        notes.append(
            'FDA required disclaimer detected — "This statement has not been evaluated by the FDA. '
            'This product is not intended to diagnose, treat, cure, or prevent any disease." '
            'Disclaimer text is excluded from disease claim scanning.'
        )

    for pattern, description in _DISEASE_CLAIM_PATTERNS:
        if re.search(pattern, tl_no_disclaimer):
            issues.append({
                'severity': 'critical',
                'message': (
                    f'Potential unauthorized disease claim detected: {description}. '
                    'Unapproved disease claims are prohibited (21 CFR 101.14 / FD&C Act 403(r)).'
                ),
            })

    # ── Structure/function claims + disclaimer ────────────────────────────────
    sf_found = [p for p in _SF_CLAIM_PATTERNS if re.search(p, tl)]
    if sf_found and is_supplement:
        if not disclaimer_present:
            issues.append({
                'severity': 'critical',
                'message': (
                    'Structure/function claims detected on a Supplement Facts product '
                    'but the required FDA disclaimer is missing. '
                    'Per 21 CFR 101.93, labels must include: '
                    '"These statements have not been evaluated by the Food and Drug Administration. '
                    'This product is not intended to diagnose, treat, cure, or prevent any disease."'
                ),
            })
    elif sf_found and not is_supplement:
        notes.append(
            'Structure/function-style claims ("supports...", "promotes...", etc.) on a conventional food label. '
            'Generally permissible but must not imply treatment or prevention of disease.'
        )

    # ── "All Natural" / "Natural" claim ──────────────────────────────────────
    if re.search(r'\ball\s+natural\b|\bnatural\s+ingredients?\b|\b100%\s+natural\b', tl):
        artificial_markers = [
            'artificial', 'synthetic', 'fd&c', 'fdc', 'acesulfame', 'sucralose',
            'aspartame', 'sodium benzoate', 'bht', 'bha', 'tbhq', 'carrageenan',
        ]
        if any(m in tl for m in artificial_markers):
            issues.append({
                'severity': 'critical',
                'message': (
                    '"Natural" or "All Natural" claim appears alongside what may be an artificial ingredient. '
                    'FDA\'s informal policy considers "natural" to mean nothing artificial or synthetic has been added.'
                ),
            })
        else:
            notes.append(
                '"All Natural" or "Natural Ingredients" claim detected. '
                'Ensure no artificial flavors, colors, or synthetic preservatives are present (FDA 2018 guidance).'
            )

    # ── Gluten-Free claim ─────────────────────────────────────────────────────
    if re.search(r'gluten[\s\-]?free', tl):
        if any(w in tl for w in ['wheat', 'barley', 'rye', 'triticale']):
            issues.append({
                'severity': 'critical',
                'message': (
                    '"Gluten-Free" claim detected alongside wheat/barley/rye in OCR text. '
                    'If the product contains these ingredients, the GF claim is prohibited (21 CFR 101.91).'
                ),
            })
        else:
            notes.append(
                '"Gluten-Free" claim detected. Under 21 CFR 101.91, product must contain < 20 ppm gluten. '
                'Ensure third-party testing records are on file.'
            )

    # ── Organic claim ─────────────────────────────────────────────────────────
    # Only flag standalone label-level claims; ingredient qualifiers (e.g., "Organic Brown Rice
    # Protein") are common in declarations and do not require the USDA seal.
    _organic_claim_pats = [
        r'\ball\s+organic\b', r'\b100%\s+organic\b', r'usda\s+organic',
        r'certified\s+organic', r'made\s+with\s+organic', r'organic\s+ingredients?\b',
    ]
    _has_organic_claim = any(re.search(p, tl) for p in _organic_claim_pats)
    if _has_organic_claim:
        if 'usda organic' not in tl and 'certified organic' not in tl:
            issues.append({
                'severity': 'warning',
                'message': (
                    '"Organic" label claim detected without apparent USDA Organic seal or '
                    '"Certified Organic" language. Organic claims require USDA NOP certification '
                    '(7 CFR Part 205).'
                ),
            })
    elif re.search(r'\borganic\b', tl):
        notes.append(
            '"Organic" word detected — likely as an ingredient qualifier '
            '(e.g., "Organic Whey Protein"). If used as a front-panel label claim, '
            'USDA NOP certification (7 CFR Part 205) is required.'
        )

    # ── Nutrient content claims ───────────────────────────────────────────────
    if re.search(r'high\s+protein|excellent\s+source\s+of\s+protein', tl):
        notes.append(
            '"High Protein" / "Excellent Source of Protein" claim: '
            'FDA requires ≥ 10g protein per RACC and ≥ 20% DV corrected for PDCAAS (21 CFR 101.54(e)).'
        )

    if re.search(r'good\s+source\s+of\s+protein', tl):
        notes.append('"Good Source of Protein" claim: FDA requires 10–19% DV (21 CFR 101.54(e)).')

    # ── Grass-Fed claim ───────────────────────────────────────────────────────
    if re.search(r'grass[\s\-]?fed', tl):
        notes.append(
            '"Grass-Fed" claim detected. Ensure your whey protein supplier can provide documentation. '
            'Third-party certification (e.g., AGA) is strongly recommended.'
        )

    # ── 100% protein source claim ─────────────────────────────────────────────
    if re.search(r'100%\s+(whey|plant|beef)\s+protein', tl):
        notes.append(
            '"100% [Whey/Plant/Beef] Protein" claim: verify there are no other protein sources in the formulation.'
        )

    # ── Sesame allergen (FASTER Act, Jan 2023) ────────────────────────────────
    if re.search(r'\bsesame\b|\btahini\b', tl):
        notes.append(
            'Sesame detected. Under the FASTER Act (effective Jan 1, 2023), sesame is the 9th major allergen '
            'and must be declared on US food labels (21 USC 321(qq)(2)).'
        )

    # ── Bioengineered food disclosure ─────────────────────────────────────────
    if any(re.search(p, tl) for p in [r'non[\s\-]?gmo', r'bioengineered', r'derived from bioengineering']):
        notes.append(
            'Non-GMO or Bioengineered (BE) disclosure detected. '
            'USDA\'s BE Food Disclosure Standard (7 CFR Part 66, effective Jan 2022) requires specific language. '
            '"Non-GMO" is not a compliant substitute for BE disclosure if the product is in fact BE.'
        )

    # ── "Made in USA" claim ───────────────────────────────────────────────────
    if re.search(r'made\s+in\s+(the\s+)?usa|made\s+in\s+america', tl):
        notes.append(
            '"Made in USA" claim detected. FTC requires "all or virtually all" US-origin content. '
            'If foreign-sourced ingredients are used, the claim may need to be qualified.'
        )

    # ── Caffeine / stimulants ─────────────────────────────────────────────────
    if re.search(r'\bcaffeine\b|\bguarana\b|\bgreen\s+tea\s+extract\b|\bgreen\s+coffee\b', tl):
        notes.append(
            'Caffeine or stimulant ingredient detected. '
            'Ensure caffeine amount is listed and total is within safe limits.'
        )

    return {'issues': issues, 'notes': notes}


# ── Check 5 (lightweight): FDA compliance for generic brands ─────────────────

def _check_fda_light(ocr_text: str, fname: str) -> dict:
    """Lightweight FDA check for generic brands.

    Runs: sparse note, disease claim scan, S/F claim + disclaimer check,
    net weight check, gluten-free conflict check.
    Skips all absence-based checks (NFP, ingredients, allergens, mfr address,
    % DV footnote, organic, natural, etc.) to avoid noise for non-ProDough artwork.
    """
    issues, notes = [], []
    tl = ocr_text.lower()

    is_supplement = 'supplement facts' in tl
    sparse = _ocr_is_sparse(ocr_text)

    if sparse:
        notes.append(
            'Low OCR yield — this PDF likely uses text converted to outlines (standard for print-ready files). '
            'Absence-based checks cannot be reliably automated on outlined-text PDFs. '
            'Verify required label elements directly on the rendered artwork image.'
        )

    # ── Disease claims ────────────────────────────────────────────────────────
    disclaimer_present = bool(_DISCLAIMER_RE.search(tl))
    tl_no_disclaimer = _DISCLAIMER_RE.sub('', tl)

    if disclaimer_present:
        notes.append(
            'FDA required disclaimer detected — "This statement has not been evaluated by the FDA. '
            'This product is not intended to diagnose, treat, cure, or prevent any disease." '
            'Disclaimer text is excluded from disease claim scanning.'
        )

    for pattern, description in _DISEASE_CLAIM_PATTERNS:
        if re.search(pattern, tl_no_disclaimer):
            issues.append({
                'severity': 'critical',
                'message': (
                    f'Potential unauthorized disease claim detected: {description}. '
                    'Unapproved disease claims are prohibited (21 CFR 101.14 / FD&C Act 403(r)).'
                ),
            })

    # ── Structure/function claims + disclaimer ────────────────────────────────
    sf_found = [p for p in _SF_CLAIM_PATTERNS if re.search(p, tl)]
    if sf_found and is_supplement:
        if not disclaimer_present:
            issues.append({
                'severity': 'critical',
                'message': (
                    'Structure/function claims detected on a Supplement Facts product '
                    'but the required FDA disclaimer is missing. '
                    'Per 21 CFR 101.93, labels must include: '
                    '"These statements have not been evaluated by the Food and Drug Administration. '
                    'This product is not intended to diagnose, treat, cure, or prevent any disease."'
                ),
            })
    elif sf_found and not is_supplement:
        notes.append(
            'Structure/function-style claims ("supports...", "promotes...", etc.) on a conventional food label. '
            'Generally permissible but must not imply treatment or prevention of disease.'
        )

    # ── Net weight ────────────────────────────────────────────────────────────
    _has_net_wt = (
        bool(re.search(r'net\s*wt|net\s*weight', tl)) or
        bool(re.search(r'\b\d+\.?\d*\s*g\b', tl)) or
        bool(re.search(r'\b\d+\.?\d*\s*(?:oz|lbs?|pounds?)\b', tl))
    )
    if not sparse and not _has_net_wt:
        issues.append({
            'severity': 'warning',
            'message': (
                'No "Net Wt" declaration or weight measurement detected. '
                'Net quantity of contents is required (15 USC 1453 / 21 CFR 101.105). Verify manually.'
            ),
        })
    elif re.search(r'net\s*wt|net\s*weight', tl):
        has_g  = bool(re.search(r'net\s*wt.{0,20}\d+\s*g\b', tl))
        has_oz = bool(re.search(r'\d+\.?\d*\s*(?:oz|lbs?|pounds?)\b', tl))
        if has_g and not has_oz:
            notes.append(
                'Net weight appears in grams only. '
                'US regulations generally require both metric (g) and US customary (oz) units. '
                'Verify with your legal/regulatory team.'
            )

    # ── Gluten-Free conflict check ────────────────────────────────────────────
    if re.search(r'gluten[\s\-]?free', tl):
        if any(w in tl for w in ['wheat', 'barley', 'rye', 'triticale']):
            issues.append({
                'severity': 'critical',
                'message': (
                    '"Gluten-Free" claim detected alongside wheat/barley/rye in OCR text. '
                    'If the product contains these ingredients, the GF claim is prohibited (21 CFR 101.91).'
                ),
            })
        else:
            notes.append(
                '"Gluten-Free" claim detected. Under 21 CFR 101.91, product must contain < 20 ppm gluten. '
                'Ensure third-party testing records are on file.'
            )

    notes.append(
        'FDA audit is in lightweight mode. Full regulatory review recommended before going to print.'
    )

    return {'issues': issues, 'notes': notes}


# ── Wind Direction check ──────────────────────────────────────────────────────

_WIND_LABELS = {
    '1': 'Wind 1 — Outwound, Across roll, Top first',
    '2': 'Wind 2 — Outwound, Across roll, Bottom first',
    '3': 'Wind 3 — Outwound, Around roll, Right side first',
    '4': 'Wind 4 — Outwound, Around roll, Left side first',
    '5': 'Wind 5 — Inwound, Across roll, Top first',
    '6': 'Wind 6 — Inwound, Across roll, Bottom first',
    '7': 'Wind 7 — Inwound, Around roll, Right side first',
    '8': 'Wind 8 — Inwound, Around roll, Left side first',
}

def _check_wind_direction(ocr_text: str, required_wind: str) -> dict:
    issues, notes = [], []
    required_wind = str(required_wind).strip()

    if not required_wind or required_wind not in _WIND_LABELS:
        return {'issues': [], 'notes': ['Wind direction check skipped (not specified).']}

    req_label = _WIND_LABELS[required_wind]
    tl = ocr_text.lower()
    detected_wind = None

    # 1. Explicit "Wind N" or "Winding N" or "Winding Direction: N"
    m = re.search(r'\bwind(?:ing)?\s*(?:direction\s*)?[:\-]?\s*([1-8])\b', tl)
    if m:
        detected_wind = m.group(1)

    # 2. Winding-box phrase: "RIGHT SIDE OFF N" / "LEFT SIDE OFF N" /
    #    "TOP FIRST N" / "BOTTOM FIRST N".
    #    Use .{0,30}? between each word so we tolerate interleaved OCR text
    #    (Tesseract often reads multi-column proof forms row-by-row, mixing
    #    winding-box words with adjacent nutrition-facts column text).
    #    "RIGHT   Serving size\nSIDE    1 Stick\nOFF     130\n3" still matches.
    if not detected_wind:
        # Tolerate interleaved OCR text (Tesseract reads multi-column proof forms
        # row-by-row, mixing winding-box words with adjacent NFP column text).
        # "RIGHT   Serving size\nSIDE    1 Stick\nOFF     130\n3" still matches.
        _DIR_PATTERNS = [
            r'right.{0,60}?side.{0,60}?off.{0,80}?\b([1-8])\b',
            r'left.{0,60}?side.{0,60}?off.{0,80}?\b([1-8])\b',
            r'top.{0,60}?first.{0,80}?\b([1-8])\b',
            r'bottom.{0,60}?first.{0,80}?\b([1-8])\b',
        ]
        for pat in _DIR_PATTERNS:
            m = re.search(pat, tl, re.DOTALL)
            if m:
                detected_wind = m.group(1)
                break

    # 3. "Outwound N" / "Inwound N"
    if not detected_wind:
        m = re.search(r'\b(in|out)wound\s*([1-8])\b', tl)
        if m:
            detected_wind = m.group(2)

    if detected_wind:
        if detected_wind == required_wind:
            notes.append(f'Wind direction confirmed: {req_label}.')
        else:
            issues.append({
                'severity': 'critical',
                'message': (
                    f'Wind direction mismatch — PDF specifies Wind {detected_wind} '
                    f'({_WIND_LABELS.get(detected_wind, "")}), '
                    f'but job requires {req_label}. Confirm with print supplier before going to press.'
                ),
            })
    else:
        issues.append({
            'severity': 'warning',
            'message': (
                f'Wind direction not detected in PDF — verify manually that the press proof '
                f'specifies {req_label}.'
            ),
        })

    return {'issues': issues, 'notes': notes}


# ── Check 7: Print Specifications (PDF vector data — no OCR) ──────────────────

_DIELINE_KEYWORDS = frozenset([
    'die', 'dieline', 'die line', 'die-line', 'die cut', 'diecut',
    'cutcontour', 'cut contour', 'thru-cut', 'thrucut', 'through cut',
    'score', 'perforation', 'perf', 'kiss cut', 'kisscut',
    'crease', 'fold', 'cutter guide',
])

_PROCESS_CS = frozenset([
    'cyan', 'magenta', 'yellow', 'black', 'cmyk', 'gray', 'grey',
    'none', 'all', 'devicecmyk', 'devicegray', 'devicergb',
    'registration', 'red', 'green', 'blue',
])


def _scan_pdf_structure(doc) -> tuple:
    """Single O(N) xref pass — returns (spot_colors, ocg_names, has_rgb).
    Replaces the three separate _extract_spot_colors / _get_ocg_names / RGB loops.
    """
    spots = set()
    ocg_names = []
    has_rgb = False

    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref, compressed=False)
        except Exception:
            continue

        # Spot / Pantone colors — /Separation and /DeviceN
        for m in re.finditer(r'/Separation\s+/([^\s/\[\]()<>{}]+)', obj):
            name = m.group(1)
            if name.lower() not in _PROCESS_CS:
                spots.add(name)
        for m in re.finditer(r'/DeviceN\s+\[([^\]]+)\]', obj):
            for nm in re.finditer(r'/([^\s/\[\]()<>{}]+)', m.group(1)):
                name = nm.group(1)
                if name.lower() not in _PROCESS_CS:
                    spots.add(name)

        # Optional Content Groups (layers)
        if '/Type /OCG' in obj:
            m = re.search(r'/Name\s*\(([^)]+)\)', obj)
            if not m:
                m = re.search(r'/Name\s+<([^>]+)>', obj)
            if m:
                ocg_names.append(m.group(1))

        # RGB content flag
        if not has_rgb and ('/DeviceRGB' in obj or '/CalRGB' in obj):
            has_rgb = True

    return sorted(spots), ocg_names, has_rgb


def _check_print_specs(fitz_doc, brand_config: dict = None,
                       matched_spec: dict = None) -> dict:
    """
    Read actual PDF vector data using an already-open PyMuPDF document.
    Checks dimensions, bleed, spot/Pantone colors, die lines, and RGB content.
    No OCR — all results are exact.
    If matched_spec is provided (from the Master SKU Spec Sheet), its values
    override brand_config for dimension/material/spot color validation.
    fitz_doc is managed by the caller — this function does NOT close it.
    """
    issues, notes = [], []
    brand_config = brand_config or {}
    matched_spec = matched_spec or {}
    proof_type = brand_config.get('proof_type', 'press')
    pts_to_mm = 25.4 / 72
    # Initialize so the return dict is always well-formed even if an exception fires early
    spot_colors = []
    mb_w = mb_h = tb_w = tb_h = bleed_mm = None

    if fitz_doc is None:
        return {'issues': [], 'notes': ['PDF structure check skipped — PyMuPDF not available.']}

    doc = fitz_doc

    try:
        page = doc[0]

        # ── Dimensions ────────────────────────────────────────────────────────
        mb = page.mediabox
        mb_w = round(mb.width  * pts_to_mm, 1)
        mb_h = round(mb.height * pts_to_mm, 1)

        # TrimBox — finished trim size (should always be present in print-ready files)
        tb_w = tb_h = None
        bleed_mm = None
        try:
            trim_raw = doc.xref_get_key(page.xref, 'TrimBox')
            if trim_raw and trim_raw[0] == 'array':
                pts = [float(x) for x in re.findall(r'[-\d.]+', trim_raw[1])]
                if len(pts) == 4:
                    tb_w = round((pts[2] - pts[0]) * pts_to_mm, 1)
                    tb_h = round((pts[3] - pts[1]) * pts_to_mm, 1)
                    bleed_h = round((mb_w - tb_w) / 2, 1)
                    bleed_v = round((mb_h - tb_h) / 2, 1)
                    bleed_mm = round(max(bleed_h, bleed_v), 1)
        except Exception:
            pass

        if tb_w and tb_h:
            notes.append(f'Trim size (TrimBox): {tb_w} × {tb_h} mm')
            notes.append(f'Full page (MediaBox): {mb_w} × {mb_h} mm')
            if bleed_mm and bleed_mm > 0:
                if bleed_mm < 2.5:
                    issues.append({'severity': 'warning',
                                   'message': f'Bleed is only {bleed_mm} mm (MediaBox vs TrimBox). '
                                              'Standard print bleed is 3 mm. Verify with your print supplier.'})
                else:
                    notes.append(f'Bleed: {bleed_mm} mm ✓')
            else:
                issues.append({'severity': 'warning',
                               'message': 'No bleed detected (TrimBox equals MediaBox). '
                                          'Print-ready files should have ≥ 3 mm bleed on all sides.'})
        else:
            notes.append(f'Page size (MediaBox): {mb_w} × {mb_h} mm')
            trimbox_sev = 'note' if proof_type == 'art' else 'warning'
            if trimbox_sev == 'warning':
                issues.append({'severity': 'warning',
                               'message': 'No TrimBox found. Print-ready PDFs must define a TrimBox '
                                          '(the finished trim size). Add a TrimBox in your design app before '
                                          'exporting (Illustrator: File → Document Setup → Trim Marks & Bleed).'})
            else:
                notes.append('No TrimBox found (art proof — no finishing panel expected). '
                             'Add a TrimBox before submitting press-ready files.')

        # Compare against expected spec dimensions (spec sheet overrides brand_config)
        spec_w = matched_spec.get('trim_width_mm') or brand_config.get('spec_width_mm')
        spec_h = matched_spec.get('trim_length_mm') or matched_spec.get('trim_height_mm') or brand_config.get('spec_height_mm')
        dim_ref = (tb_w, tb_h) if tb_w else (mb_w, mb_h)
        dim_label = 'trim' if tb_w else 'page'
        if spec_w and spec_h and dim_ref[0]:
            tol = 2.0  # mm
            fits = (
                (abs(dim_ref[0] - spec_w) <= tol and abs(dim_ref[1] - spec_h) <= tol) or
                (abs(dim_ref[0] - spec_h) <= tol and abs(dim_ref[1] - spec_w) <= tol)
            )
            if not fits:
                issues.append({'severity': 'critical',
                               'message': f'Dimension mismatch — {dim_label} size is '
                                          f'{dim_ref[0]} × {dim_ref[1]} mm, '
                                          f'spec requires {spec_w} × {spec_h} mm (±2 mm). '
                                          'Verify the artwork size with your print supplier before going to press.'})
            else:
                notes.append(f'Dimensions match spec: {spec_w} × {spec_h} mm ✓')

        # ── Single xref pass: spot colors, OCG layers, RGB flag ──────────────
        spot_colors, ocg_names, has_rgb = _scan_pdf_structure(doc)

        # ── Spot / Pantone colors ─────────────────────────────────────────────
        if spot_colors:
            notes.append(f'Spot colors in file: {", ".join(spot_colors)}')
        else:
            notes.append('No spot colors detected (process CMYK / RGB only).')

        # Validate required spot colors from spec sheet
        req_spots_raw = matched_spec.get('pms_spot_colors', '') or matched_spec.get('spot_colors', '')
        if req_spots_raw:
            req_spots = [s.strip() for s in req_spots_raw.split(',') if s.strip()]

            def _norm_pantone(name: str) -> str:
                # Strip trailing Pantone finish suffix (C=coated, U=uncoated, M=matte, CP, EC, etc.)
                return re.sub(r'\s+[CUMcum]{1,2}P?\s*$', '', name.strip()).strip().lower()

            def _spot_matches(req: str, file_colors: list) -> bool:
                req_n = _norm_pantone(req)
                for c in file_colors:
                    c_n = _norm_pantone(c)
                    # Exact normalized match, or either side is a substring of the other
                    if req_n == c_n or req_n in c_n or c_n in req_n:
                        return True
                return False

            for req in req_spots:
                if not _spot_matches(req, spot_colors):
                    issues.append({'severity': 'warning',
                                   'message': f'Required spot color "{req}" (from spec sheet) '
                                              'not found in the PDF. Verify color setup with '
                                              'your designer before sending to print.'})

        # ── Die line detection ────────────────────────────────────────────────
        die_spots  = [c for c in spot_colors
                      if any(kw in c.lower().replace('_', ' ') for kw in _DIELINE_KEYWORDS)]
        die_layers = [l for l in ocg_names
                      if any(kw in l.lower().replace('_', ' ') for kw in _DIELINE_KEYWORDS)]

        if die_spots:
            notes.append(f'Die line spot color detected: {", ".join(die_spots)} ✓')
        elif die_layers:
            notes.append(f'Die line layer detected: {", ".join(die_layers)} ✓')
        else:
            die_sev = 'critical' if matched_spec.get('die_line_required') else 'warning'
            issues.append({'severity': die_sev,
                           'message': 'No die line spot color or layer detected. '
                                      'Expected a spot color named "Dieline", "CutContour", "Die", '
                                      'or similar. Verify die lines are present and correctly labeled '
                                      'in the file before sending to print.'
                                      + (' Die line is required per the master spec sheet.' if die_sev == 'critical' else '')})

        # ── Material type ─────────────────────────────────────────────────────
        # Extract from native PDF text (press proof forms list material in the
        # finishing/job info panel as editable text, so PyMuPDF reads it exactly).
        pdf_text = '\n'.join(doc[i].get_text() for i in range(len(doc)))
        material_found = None

        _MATERIAL_KWS = ['pet', 'opp', 'bopp', 'foil', 'metallocene', 'soft touch',
                         'matte', 'gloss', 'kraft', 'paper', 'film', 'substrate',
                         'label', 'liner', 'shrink', 'laminate']

        def _looks_like_material(text: str) -> bool:
            tl2 = text.lower()
            return bool(re.search(r'#\d{3,4}', text)) or any(kw in tl2 for kw in _MATERIAL_KWS)

        # First try an explicit material code like "#781 PET Soft Touch on #858..."
        m = re.search(
            r'(#\d{3,4}[^\n]{5,80}(?:pet|opp|bopp|pe|pp|foil|metallocene|'
            r'soft\s*touch|matte|gloss|kraft|paper)[^\n]{0,60})',
            pdf_text, re.IGNORECASE
        )
        if m:
            material_found = m.group(1).strip()
        else:
            # Fallback: "Material Order: ..." or "Material: ..." lines
            m = re.search(
                r'material\s*(?:order\s*)?[:\-]?\s*([^\n]{5,120})',
                pdf_text, re.IGNORECASE
            )
            if m:
                candidate = m.group(1).strip().rstrip('.,;')
                # Only accept if it actually looks like a material (reject status stamps like "APPROVED")
                if _looks_like_material(candidate):
                    material_found = candidate

        if material_found:
            # Clean up: truncate if the regex grabbed too much surrounding text
            material_found = material_found[:120].strip()
            notes.append(f'Material: {material_found}')
            # Validate against required material — spec sheet takes priority over brand_config
            req_mat = (matched_spec.get('material') or brand_config.get('required_material', '')).strip()
            if req_mat:
                f_lo, r_lo = material_found.lower(), req_mat.lower()
                # Accept if either string contains the other — the regex sometimes captures
                # only part of a multi-line material spec (e.g. first line of "#781 PET Soft
                # Touch on\n#858 COS Web metallocene"), so a prefix match is a valid hit.
                if f_lo not in r_lo and r_lo not in f_lo:
                    issues.append({'severity': 'warning',
                                   'message': f'Material mismatch — file specifies "{material_found}", '
                                              f'but spec requires "{req_mat}". '
                                              'Confirm substrate with your print supplier before going to press.'})
        else:
            if proof_type == 'art':
                notes.append('Material type: not detected (art proof — no finishing panel expected).')
            else:
                notes.append('Material type: not detected in PDF text. '
                             'If this is a press proof, verify the material spec in the finishing section '
                             'of the proof form manually.')

        # ── Spec sheet match note ─────────────────────────────────────────────
        if matched_spec:
            flavor_label = matched_spec.get('flavor') or matched_spec.get('sku') or 'Unknown'
            sku_part = f" · SKU {matched_spec['sku']}" if matched_spec.get('sku') and matched_spec.get('sku') != matched_spec.get('flavor') else ''
            notes.append(f'Matched spec: {flavor_label}{sku_part}')
            if matched_spec.get('wind_direction'):
                wd = matched_spec['wind_direction']
                notes.append(f'Expected wind direction from spec sheet: {_WIND_LABELS.get(wd, wd)}')

        # ── Page count ────────────────────────────────────────────────────────
        if len(doc) > 1:
            issues.append({'severity': 'warning',
                           'message': f'PDF has {len(doc)} pages. Artwork files should be '
                                      'single-page. Verify only page 1 is the artwork and '
                                      'remove any blank, template, or approval pages.'})

    except Exception as e:
        issues.append({'severity': 'warning', 'message': f'Print spec analysis error: {e}'})

    return {
        'issues': issues,
        'notes': notes,
        'spot_colors': spot_colors,
        'dimensions': {
            'mediabox_mm': [mb_w, mb_h] if mb_w is not None else None,
            'trimbox_mm':  [tb_w, tb_h] if tb_w is not None else None,
            'bleed_mm':    bleed_mm,
        },
    }


# ── Summary ───────────────────────────────────────────────────────────────────

def _build_summary(results: list) -> dict:
    total = len(results)
    counts = {'clean': 0, 'info': 0, 'warning': 0, 'critical': 0, 'error': 0}
    for r in results:
        sev = r.get('severity', 'error')
        counts[sev] = counts.get(sev, 0) + 1

    total_crits = sum(r.get('critical_count', 0) for r in results)
    total_warns = sum(r.get('warning_count', 0) for r in results)

    fda_crits = []
    for r in results:
        for issue in r.get('checks', {}).get('fda', {}).get('issues', []):
            if issue['severity'] == 'critical':
                fda_crits.append({'file': r['filename'], 'message': issue['message']})

    return {
        'total_files': total,
        'severity_counts': counts,
        'total_critical_issues': total_crits,
        'total_warning_issues': total_warns,
        'fda_critical_issues': fda_crits,
    }
