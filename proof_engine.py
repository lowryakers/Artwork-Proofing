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
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pdfplumber as _pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False

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
            'confirmations': {},
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
            _jobs[job_id].setdefault('confirmations', {}).pop(key, None)
        else:
            _jobs[job_id]['dismissals'].pop(key, None)
    _save_job_to_disk(job_id)
    _append_feedback_log(job_id, filename, check_name, issue_index, 'dismissed' if dismissed else 'unreviewed')
    return True


def set_confirmation(job_id: str, filename: str, check_name: str,
                     issue_index: int, confirmed: bool) -> bool:
    with _jobs_lock:
        if job_id not in _jobs:
            return False
        key = f"{filename}|{check_name}|{issue_index}"
        if confirmed:
            _jobs[job_id].setdefault('confirmations', {})[key] = True
            _jobs[job_id].get('dismissals', {}).pop(key, None)
        else:
            _jobs[job_id].setdefault('confirmations', {}).pop(key, None)
    _save_job_to_disk(job_id)
    _append_feedback_log(job_id, filename, check_name, issue_index, 'confirmed' if confirmed else 'unreviewed')
    return True


def _append_feedback_log(job_id: str, filename: str, check_name: str,
                         issue_index: int, action: str):
    """Persist confirm/dismiss decisions for learning analysis."""
    log_file = os.path.join(_UPLOAD_DIR, 'feedback_log.json')
    entry = {
        'timestamp': datetime.now().isoformat(),
        'action': action,
        'job_id': job_id,
        'filename': filename,
        'check': check_name,
        'issue_index': issue_index,
    }
    try:
        if os.path.exists(log_file):
            with open(log_file) as f:
                data = json.load(f)
        else:
            data = {'entries': []}
        data['entries'].append(entry)
        with open(log_file, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass


def get_feedback_patterns() -> list:
    """Return patterns from feedback log — frequent dismissals suggest false positives."""
    log_file = os.path.join(_UPLOAD_DIR, 'feedback_log.json')
    if not os.path.exists(log_file):
        return []
    try:
        with open(log_file) as f:
            data = json.load(f)
        entries = data.get('entries', [])
    except Exception:
        return []

    from collections import Counter
    dismissed = Counter(e['check'] for e in entries if e.get('action') == 'dismissed')
    confirmed = Counter(e['check'] for e in entries if e.get('action') == 'confirmed')
    _labels = {
        'gtin': 'GTIN / Barcode', 'nfp': 'NFP', 'eyemark': 'Eyemark',
        'spelling': 'Spelling', 'fda': 'FDA Audit Risk',
        'wind': 'Wind Direction', 'specs': 'Print Specs',
    }
    patterns = []
    for check, count in dismissed.items():
        if count >= 3:
            patterns.append({
                'check': check,
                'label': _labels.get(check, check),
                'dismissed': count,
                'confirmed': confirmed.get(check, 0),
            })
    return sorted(patterns, key=lambda x: x['dismissed'], reverse=True)


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
    results = []
    total = len(pdf_paths)

    try:
        for i, pdf_path in enumerate(pdf_paths):
            fname = os.path.basename(pdf_path)
            _update_job(job_id, current_file=fname, progress=int(i / total * 90))
            try:
                result = _proof_single(pdf_path, gtin_rows, work_dir, brand_config=brand_config,
                                       spec_rows=spec_rows)
            except Exception as exc:
                result = {
                    'filename': fname,
                    'error': str(exc),
                    'checks': {},
                    'severity': 'error',
                    'critical_count': 0,
                    'warning_count': 0,
                    'info_count': 0,
                    'img_web': None,
                }
            results.append(result)

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
    'the', 'a', 'an', 'and', 'of', 'with', 'for', 'to',
}


def _match_spec_row(gtin_list: list, fname: str, spec_rows: list) -> dict:
    """Match a spec row by GTIN first, then by filename keyword matching."""
    if not spec_rows:
        return {}

    # 1. Exact GTIN match
    for gtin in (gtin_list or []):
        gtin_str = str(gtin).strip()
        for row in spec_rows:
            if str(row.get('gtin', '')).strip() == gtin_str:
                return row

    # 2. Keyword match against filename
    fname_lower = fname.lower()
    for row in spec_rows:
        flavor = str(row.get('flavor', '')).strip().lower()
        if not flavor:
            continue
        flavor_words = re.findall(r'[a-z0-9]+', flavor)
        keywords = [w for w in flavor_words if w not in _SPEC_GENERIC and len(w) > 1]
        if not keywords:
            continue
        if all(kw in fname_lower for kw in keywords):
            return row

    return {}


# ── Single-file proofing ──────────────────────────────────────────────────────

def _proof_single(pdf_path: str, gtin_rows: list, work_dir: str,
                  brand_config: dict = None, spec_rows: list = None) -> dict:
    brand_config = brand_config or {}
    fname = os.path.basename(pdf_path)
    stem = Path(pdf_path).stem

    # ── Convert PDF → PNG ────────────────────────────────────────────────────
    img_prefix = os.path.join(work_dir, stem)
    subprocess.run(
        ['pdftoppm', '-r', '400', '-png', '-singlefile', pdf_path, img_prefix],
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

    # ── Load image + preprocess for OCR ─────────────────────────────────────
    # Load first so we can enhance before running Tesseract
    img = None
    img_ocr_base = None  # auto-cropped version used for all OCR passes
    img_for_ocr = img_path  # default: use raw PNG if PIL unavailable
    if PIL_AVAILABLE:
        try:
            from PIL import ImageEnhance, ImageFilter, ImageChops as _IChops
            img = Image.open(img_path).convert('RGB')

            # Auto-crop to the packaging die cut — removes the white bleed/border
            # that pdftoppm adds around the artwork. Without this, the packaging
            # content occupies only a fraction of the rendered image and OCR
            # resolution per character is too low for small label text.
            img_ocr_base = img
            try:
                _gray_c = img.convert('L')
                _white_ref = Image.new('L', img.size, 255)
                _diff_c = _IChops.difference(_gray_c, _white_ref)
                _bbox_c = _diff_c.getbbox()
                if _bbox_c:
                    _pad_c = max(20, int(min(img.width, img.height) * 0.015))
                    _box_c = (
                        max(0, _bbox_c[0] - _pad_c),
                        max(0, _bbox_c[1] - _pad_c),
                        min(img.width,  _bbox_c[2] + _pad_c),
                        min(img.height, _bbox_c[3] + _pad_c),
                    )
                    _cw = _box_c[2] - _box_c[0]
                    _ch = _box_c[3] - _box_c[1]
                    # Only crop when the artwork noticeably smaller than the full page
                    if _cw < img.width * 0.88 or _ch < img.height * 0.88:
                        img_ocr_base = img.crop(_box_c)
            except Exception:
                pass

            # Grayscale + contrast boost + sharpen → significantly improves
            # Tesseract accuracy on small label text and mixed backgrounds
            _gray = img_ocr_base.convert('L')
            _gray = ImageEnhance.Contrast(_gray).enhance(1.8)
            _gray = ImageEnhance.Sharpness(_gray).enhance(2.5)
            _enhanced_path = img_prefix + '_ocr.png'
            _gray.save(_enhanced_path)
            img_for_ocr = _enhanced_path
        except Exception:
            pass

    # ── Native text: pdfplumber (column-aware, spatially sorted) ────────────
    # pdfplumber crops the page into left/right halves before extracting text,
    # so the NFP column and ingredient column are never interleaved.
    pdfplumber_text = ''
    if PDFPLUMBER_AVAILABLE:
        try:
            with _pdfplumber.open(pdf_path) as _plumb_doc:
                for _pg in _plumb_doc.pages:
                    _pw, _ph = _pg.width, _pg.height
                    _left_crop  = _pg.crop((0,       0, _pw / 2, _ph))
                    _right_crop = _pg.crop((_pw / 2, 0, _pw,     _ph))
                    pdfplumber_text += (_left_crop.extract_text()  or '') + '\n'
                    pdfplumber_text += (_right_crop.extract_text() or '') + '\n'
        except Exception:
            pass

    # ── Native text: PyMuPDF fallback ────────────────────────────────────────
    native_text = ''
    page_rotation = 0
    try:
        import fitz as _fitz
        _doc = _fitz.open(pdf_path)
        native_text = '\n'.join(_doc[i].get_text() for i in range(len(_doc)))
        page_rotation = _doc[0].rotation if len(_doc) > 0 else 0
        _doc.close()
    except Exception:
        pass

    # ── OCR: region-based (left half + right half) with PSM 6 ───────────────
    # Replaces the old PSM 3 full-page pass. PSM 3 interleaved columns and
    # cut off the bottom of the ingredient column ("Contains: Milk" was lost).
    # PSM 6 (uniform block) on each half reads each column independently.
    ocr_left = ocr_right = ''
    if PIL_AVAILABLE:
        try:
            _ocr_full = Image.open(img_for_ocr)
            _ow, _oh  = _ocr_full.size
            _left_img  = _ocr_full.crop((0,        0, _ow // 2, _oh))
            _right_img = _ocr_full.crop((_ow // 2, 0, _ow,      _oh))
            _left_path  = img_prefix + '_ocr_left.png'
            _right_path = img_prefix + '_ocr_right.png'
            _left_img.save(_left_path)
            _right_img.save(_right_path)
            _rl = subprocess.run(
                ['tesseract', _left_path,  'stdout', '--oem', '3', '--psm', '6', '-l', 'eng'],
                capture_output=True, text=True, timeout=90,
            )
            ocr_left = _rl.stdout
            _rr = subprocess.run(
                ['tesseract', _right_path, 'stdout', '--oem', '3', '--psm', '6', '-l', 'eng'],
                capture_output=True, text=True, timeout=90,
            )
            ocr_right = _rr.stdout
        except Exception:
            pass

    # ── OCR: PSM 11 sparse — full-page catchall ──────────────────────────────
    # Catches any isolated text the region passes miss (e.g. single-column
    # layouts, rotated text, corner stamps).
    ocr_sparse = ''
    try:
        r2 = subprocess.run(
            ['tesseract', img_for_ocr, 'stdout', '--oem', '3', '--psm', '11', '-l', 'eng'],
            capture_output=True, text=True, timeout=90,
        )
        ocr_sparse = r2.stdout
    except Exception:
        pass

    # ── OCR: inverted halves — catches white text on colored backgrounds ────────
    # ProDough badge callouts (e.g. "117 Calories", "25G Protein") use white
    # text inside colored circles. We invert both halves independently so badges
    # on either the front or back panel are captured.
    ocr_inv_left = ocr_inv_right = ''
    if PIL_AVAILABLE:
        try:
            from PIL import ImageOps as _ImageOps
            _inv_left = _ImageOps.invert(_left_img.convert('RGB'))
            _inv_left_path = img_prefix + '_ocr_inv_left.png'
            _inv_left.save(_inv_left_path)
            _ri = subprocess.run(
                ['tesseract', _inv_left_path, 'stdout', '--oem', '3', '--psm', '11', '-l', 'eng'],
                capture_output=True, text=True, timeout=90,
            )
            ocr_inv_left = _ri.stdout
        except Exception:
            pass
        try:
            _inv_right = _ImageOps.invert(_right_img.convert('RGB'))
            _inv_right_path = img_prefix + '_ocr_inv_right.png'
            _inv_right.save(_inv_right_path)
            _rr2 = subprocess.run(
                ['tesseract', _inv_right_path, 'stdout', '--oem', '3', '--psm', '11', '-l', 'eng'],
                capture_output=True, text=True, timeout=90,
            )
            ocr_inv_right = _rr2.stdout
        except Exception:
            pass

    # ── OCR: binary threshold — gives Tesseract the cleanest possible input ───
    # Adaptive thresholding converts the image to pure B&W which Tesseract reads
    # more reliably than grayscale on gradient/complex backgrounds.
    ocr_binary = ''
    if PIL_AVAILABLE and img_ocr_base is not None:
        try:
            _bin_img = img_ocr_base.convert('L')
            _bin_img = _bin_img.point(lambda p: 255 if p > 140 else 0, '1').convert('L')
            _bin_path = img_prefix + '_ocr_bin.png'
            _bin_img.save(_bin_path)
            _rb = subprocess.run(
                ['tesseract', _bin_path, 'stdout', '--oem', '3', '--psm', '11', '-l', 'eng'],
                capture_output=True, text=True, timeout=90,
            )
            ocr_binary = _rb.stdout
        except Exception:
            pass

    combined_text = (
        pdfplumber_text + '\n' +
        native_text     + '\n' +
        ocr_left        + '\n' +
        ocr_right       + '\n' +
        ocr_sparse      + '\n' +
        ocr_inv_left    + '\n' +
        ocr_inv_right   + '\n' +
        ocr_binary
    )

    # Scan barcode stripes directly from rendered image (primary GTIN source)
    barcode_gtins = _scan_barcodes(img_path)

    brand_mode = brand_config.get('brand_mode', 'prodough')

    # ── Match spec row from sheet ────────────────────────────────────────────
    matched_spec = _match_spec_row(barcode_gtins, fname, spec_rows or [])

    # Wind direction: form override > spec sheet > nothing
    effective_wind = brand_config.get('wind_direction', '').strip()
    if not effective_wind and matched_spec.get('wind_direction'):
        effective_wind = matched_spec['wind_direction']

    # Required eyemark color: spec sheet > brand_config
    required_eyemark = (
        matched_spec.get('eye_mark_color') or
        matched_spec.get('eyemark color') or
        matched_spec.get('eyemark_color') or
        matched_spec.get('eye mark color') or
        matched_spec.get('eyemark') or
        brand_config.get('required_eyemark_color', '')
    ).strip().lower() if matched_spec else brand_config.get('required_eyemark_color', '').strip().lower()

    if brand_mode == 'generic':
        is_film = brand_config.get('packaging_type', 'other') == 'stick'
        brand_name = brand_config.get('brand_name', '')
        checks = {
            'gtin':     _check_gtin(combined_text, fname, gtin_rows, barcode_gtins),
            'eyemark':  _check_eyemark(img, is_film, fname, required_eyemark),
            'spelling': _check_spelling_generic(combined_text, brand_name),
            'fda':      _check_fda_light(combined_text, fname),
            'specs':    _check_print_specs(pdf_path, brand_config, matched_spec),
        }
        if is_film:
            checks['wind'] = _check_wind_direction(combined_text, effective_wind)
    else:
        is_film = _is_film_rollstock(fname, combined_text)
        proof_type = brand_config.get('proof_type', 'press')
        checks = {
            'gtin':     _check_gtin(combined_text, fname, gtin_rows, barcode_gtins),
            'nfp':      _check_nfp(combined_text, front_text=ocr_left + '\n' + ocr_inv_left + '\n' + ocr_inv_right),
            'eyemark':  _check_eyemark(img, is_film, fname, required_eyemark),
            'spelling': _check_spelling(combined_text, fname),
            'fda':      _check_fda(combined_text, fname),
        }
        # Print Specs and Wind Direction are press-proof checks — skip for art proofs
        if proof_type != 'art':
            checks['specs'] = _check_print_specs(pdf_path, brand_config, matched_spec)
            if effective_wind:
                checks['wind'] = _check_wind_direction(combined_text, effective_wind)

    # Inject spec GTIN so the UI can show detected-vs-spec comparison
    if matched_spec:
        checks['gtin']['spec_gtin'] = str(matched_spec.get('gtin', '')).strip()

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
        'ocr_text': combined_text,
        'checks': checks,
        'severity': severity,
        'critical_count': len(crit),
        'warning_count': len(warns),
        'info_count': len(infos),
        'error': None,
        'matched_spec': matched_spec,
        'page_rotation': page_rotation,
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
        # Common abbreviations found in filenames — expand before keyword matching
        _ABBREVS = {
            'pb':   'peanut butter',
            'choc': 'chocolate',
            'van':  'vanilla',
            'straw': 'strawberry',
            'lem':  'lemon',
            'cinn': 'cinnamon',
            'bday': 'birthday',
            'btrscotch': 'butterscotch',
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

                # Expand abbreviations in filename before matching.
                # Replace underscores/hyphens with spaces first so \b word boundaries
                # work correctly (regex treats _ as a word character).
                fname_lower = fname.lower()
                fname_expanded = re.sub(r'[_\-]+', ' ', fname_lower)
                for abbr, expansion in _ABBREVS.items():
                    fname_expanded = re.sub(r'\b' + re.escape(abbr) + r'\b', expansion, fname_expanded)

                matched = [kw for kw in keywords
                           if kw in pdf_text_lower or kw in fname_lower or kw in fname_expanded]
                required = (len(keywords) + 1) // 2  # majority threshold — ceil(N/2)
                if len(matched) < required:
                    missing = [kw for kw in keywords
                               if kw not in pdf_text_lower and kw not in fname_lower and kw not in fname_expanded]
                    # 0 of N matched → almost certainly the wrong SKU → critical
                    # Some matched → possible mismatch or OCR gap → warning
                    sev = 'critical' if len(matched) == 0 else 'warning'
                    issues.append({
                        'severity': sev,
                        'message': (
                            f'GTIN {gtin} is listed under "{expected_flavor}" in the master list, '
                            f'but only {len(matched)} of {len(keywords)} identifying keyword(s) '
                            f'("{", ".join(missing)}" missing) were found. '
                            + ('This barcode appears to be for a different SKU — verify before printing.'
                               if sev == 'critical' else 'Confirm this is the correct SKU.')
                        ),
                    })
                # Majority matched — flavor lines up, no issue raised
            else:
                issues.append({
                    'severity': 'warning',
                    'message': f'GTIN {gtin} was not found in the uploaded master list. Verify it belongs to this SKU.',
                })

    return {'found_gtins': found, 'issues': issues, 'notes': notes}


# ── Check 2: Front call-outs vs NFP ──────────────────────────────────────────

def _check_nfp(ocr_text: str, front_text: str = '') -> dict:
    issues, notes = [], []
    tl = ocr_text.lower()
    # For front callout badge detection, use left-half OCR if provided (more
    # accurate than full combined text which mixes front + back panel).
    fl = front_text.lower() if front_text.strip() else tl

    # Use multi-signal detection — outlined-text PDFs often OCR row labels/numbers
    # even when the "Nutrition Facts" header text doesn't render. Avoid false flags
    # on sparse (all-outlined) files where nothing OCRs at all.
    sparse = _ocr_is_sparse(ocr_text)

    _nfp_row_signals = [r'\btotal\s*fat\b', r'\bsodium\b', r'\bcarbohydrate\b',
                        r'\bdietary\s*fiber\b', r'\b%\s*dv\b', r'%\s*daily\s*value',
                        r'\bserving\s*size\b', r'\bservings?\s*per\b',
                        r'\btotal\s*carb\b', r'\bsaturated\s*fat\b',
                        r'\btrans\s*fat\b', r'\bcholesterol\b', r'\bsugars?\b']

    has_nfp_header  = 'nutrition facts' in tl or 'supplement facts' in tl
    has_nfp_numbers = bool(re.search(r'\bcalories?\b', tl)) or bool(re.search(r'\bprotein\b', tl))
    nfp_row_hits    = sum(1 for p in _nfp_row_signals if re.search(p, tl))
    has_nfp         = has_nfp_header or has_nfp_numbers or nfp_row_hits >= 1

    if not has_nfp:
        if sparse:
            notes.append(
                'Nutrition Facts panel could not be verified via OCR — text appears to be converted '
                'to outlines (standard for print-ready files). Verify the NFP is present directly on '
                'the artwork.'
            )
        else:
            issues.append({
                'severity': 'warning',
                'message': (
                    'Nutrition Facts (or Supplement Facts) panel not detected via OCR. '
                    'Verify the NFP is present and correctly formatted on the artwork.'
                ),
            })

    # NFP-format patterns: label BEFORE value ("Calories 120", "Protein 25g")
    nfp_cal_hits  = re.findall(r'calories\s+(\d+)', tl)
    nfp_calories  = sorted({int(c) for c in nfp_cal_hits  if 30 <= int(c) <= 800})
    nfp_prot_hits = re.findall(r'protein\s+(\d+)\s*g', tl)
    nfp_proteins  = sorted({int(p) for p in nfp_prot_hits if 0 < int(p) < 120})
    nfp_zero_sugar = bool(re.search(r'added\s+sugars?\s+0\s*g|added\s+sugars?\s*\n?\s*0', tl))

    # Front callout format — circular badge callouts show the number on one line
    # and the label on the next, so we need multi-strategy detection.
    _fc_cals, _fc_prots = set(), set()

    # Strategy 1: inline "117 cal/calories" or "25g protein" on same line
    # Run on fl (left-half / front-panel text) for accuracy
    for _v in re.findall(r'\b(\d{2,3})\s*cal(?:ories?)?\b(?!\s*\d)', fl):
        if 30 <= int(_v) <= 800: _fc_cals.add(int(_v))
    for _v in re.findall(r'\b(\d+)\s*g\s+protein\b', fl):
        if 0 < int(_v) < 120: _fc_prots.add(int(_v))

    # Strategy 2: multi-line badge format — standalone number on its own line
    # followed within 6 lines by the keyword ("117\nCalories\nPer Serving")
    _lines = fl.split('\n')
    for _i, _line in enumerate(_lines):
        _s = _line.strip()
        _ctx_fwd = ' '.join(l.strip() for l in _lines[_i + 1: _i + 7])
        # Standalone number (e.g. "117") — but skip "100" if next lines show "%"
        # (catches "100% Grass-Fed Whey" badges being misread as 100 calories)
        _m = re.match(r'^(\d{2,3})$', _s)
        if _m:
            _v = int(_m.group(1))
            _next_few = ' '.join(l.strip() for l in _lines[_i + 1: _i + 3])
            _is_pct = '%' in _next_few or 'percent' in _next_few.lower()
            if not _is_pct and 30 <= _v <= 800 and re.search(r'\bcal(?:ories?)?\b', _ctx_fwd):
                _fc_cals.add(_v)
        # Standalone "NNg" or "NN" followed by "g" on next line (e.g. "25g" or "25G")
        _mg = re.match(r'^(\d+)g$', _s)
        if _mg:
            _v = int(_mg.group(1))
            if 0 < _v < 120 and re.search(r'\bprotein\b', _ctx_fwd):
                _fc_prots.add(_v)
        # "25" on one line, "g" or "grams" on next — OCR sometimes splits the unit
        _mn = re.match(r'^(\d{1,2})$', _s)
        if _mn:
            _v = int(_mn.group(1))
            _next = _lines[_i + 1].strip().lower() if _i + 1 < len(_lines) else ''
            if _next in ('g', 'grams') and 0 < _v < 120:
                if re.search(r'\bprotein\b', _ctx_fwd):
                    _fc_prots.add(_v)

    # Strategy 3: reverse window — "calories" keyword with a number in the 120
    # chars BEFORE it; protein keyword with number in 60 chars before it.
    for _km in re.finditer(r'\bcal(?:ories?)?\b', fl):
        _before = fl[max(0, _km.start() - 120): _km.start()]
        for _vm in re.finditer(r'\b(\d{2,3})\b', _before):
            _vi = int(_vm.group(1))
            if 30 <= _vi <= 800:
                # Skip if this number is immediately followed by "%" (percentage claim)
                _trail = _before[_vm.end(): _vm.end() + 4].lstrip()
                if _trail.startswith('%'):
                    continue
                _fc_cals.add(_vi)
    for _km in re.finditer(r'\bprotein\b', fl):
        _before = fl[max(0, _km.start() - 60): _km.start()]
        for _v in re.findall(r'\b(\d{1,2})\b', _before):
            _vi = int(_v)
            if 5 < _vi < 120:
                _fc_prots.add(_vi)

    front_calories  = sorted(_fc_cals)
    front_proteins  = sorted(_fc_prots)
    front_zero_sugar = bool(re.search(r'\b0\s*g\s*\n?\s*added\s+sugar|\b0\s+added\s+sugar', fl))

    # Combined lists — used by the mismatch checks below
    calories = sorted(set(nfp_calories) | set(front_calories))
    proteins = sorted(set(nfp_proteins) | set(front_proteins))

    nw_hits = re.findall(r'net\s*wt\.?\s*([\d.]+)\s*g', tl)

    # Dual-column NFPs ("Amount per serving" + "As Prepared") legitimately show
    # two different calorie/protein values — don't flag as a mismatch.
    # Also suppress when preparation/directions text is present (powder products).
    has_dual_column = bool(re.search(
        r'as\s+prepared|when\s+prepared|with\s+(?:whole\s+|2%\s+|skim\s+)?milk|'
        r'prepared\s+with|per\s+serving\s+prepared|with\s+water|mix(?:ed)?\s+with|'
        r'directions|serving\s+suggestion|prepared\s+product',
        tl
    ))

    if len(set(calories)) > 1 and not has_dual_column:
        diff = max(calories) - min(calories)
        if diff > 1:
            issues.append({
                'severity': 'warning',
                'message': (
                    f'Calorie mismatch: front call-out shows {min(calories)} cal but NFP shows {max(calories)} cal. '
                    'Front panel and NFP must declare identical calorie counts (FDA labeling requirement).'
                ),
            })

    if len(set(proteins)) > 1 and not has_dual_column:
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
        'has_nfp_header': has_nfp_header,
        'calories': calories,
        'proteins': proteins,
        'net_weights': nw_hits,
        'front_callout': {
            'calories': front_calories,
            'proteins': front_proteins,
            'zero_sugar': front_zero_sugar,
        },
        'nfp_data': {
            'calories': nfp_calories,
            'proteins': nfp_proteins,
            'zero_sugar': nfp_zero_sugar,
        },
        'issues': issues,
        'notes': notes,
    }


# ── Check 3: Eyemark color ────────────────────────────────────────────────────
# Rule: eyemark MUST be solid black (#000000) or solid white (#FFFFFF).
# Any other color will cause unreliable photo-eye detection on the production line.

def _check_eyemark(img, is_film: bool = False, fname: str = '',
                   required_color: str = '') -> dict:
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

    # Detect barcode location so we can exclude it from the eyemark scan.
    # Barcode white spaces score identically to a solid white patch — we must
    # mask the barcode region or we'll always mis-detect it as the eyemark.
    _barcode_zones = []  # list of (x0, y0, x1, y1) exclusion rectangles
    if PYZBAR_AVAILABLE:
        try:
            _bc_decoded = _pyzbar_decode(img.convert('RGB'))
            for _bc in _bc_decoded:
                _r = _bc.rect
                _margin = max(30, int(min(w, h) * 0.06))
                _barcode_zones.append((
                    max(0,  _r.left  - _margin),
                    max(0,  _r.top   - _margin),
                    min(w,  _r.left  + _r.width  + _margin),
                    min(h,  _r.top   + _r.height + _margin),
                ))
        except Exception:
            pass

    def _in_barcode(xx, yy):
        """Return True if tile at (xx,yy) overlaps any barcode exclusion zone."""
        for zx0, zy0, zx1, zy1 in _barcode_zones:
            if xx < zx1 and xx + tw > zx0 and yy < zy1 and yy + tw > zy0:
                return True
        return False

    tw = max(25, int(min(w, h) * 0.055))   # tile side length
    bx = max(tw, int(w  * 0.16))            # left/right border scan depth
    by = max(tw, int(h  * 0.13))            # top/bottom border scan depth
    step = max(tw // 2, 12)

    gray = img.convert('L')
    best_black = 0   # best score for a solid-dark tile
    best_white = 0   # best score for a solid-light tile
    avg_luma = 128   # fallback for the "else" color description branch

    def _em_score(px):
        if len(px) < 4:
            return 0, None
        mn, mx = min(px), max(px)
        # Pixel range > 90 → mixed content (barcode, graphics) — not a solid eyemark
        if mx - mn > 90:
            return 0, None
        avg = sum(px) / len(px)
        uniformity = 1.0 - (mx - mn) / 90.0
        if avg < 45:
            return (1.0 - avg / 45.0) * uniformity, 'black'
        if avg > 210:
            return ((avg - 210.0) / 45.0) * uniformity, 'white'
        return 0, None

    def _scan_zone(x0, y0, x1, y1):
        nonlocal best_black, best_white, avg_luma
        for yy in range(y0, max(y0 + 1, y1 - tw + 1), step):
            for xx in range(x0, max(x0 + 1, x1 - tw + 1), step):
                if _in_barcode(xx, yy):
                    continue
                px = list(gray.crop((xx, yy, min(xx + tw, x1), min(yy + tw, y1))).getdata())
                sc, col = _em_score(px)
                if col == 'black' and sc > best_black:
                    best_black = sc
                    avg_luma = sum(px) / max(1, len(px))
                elif col == 'white' and sc > best_white:
                    best_white = sc

    _scan_zone(0,       0,       w,      by)       # top strip
    _scan_zone(0,       h - by,  w,      h)        # bottom strip
    _scan_zone(0,       0,       bx,     h)        # left strip
    _scan_zone(w - bx,  0,       w,      h)        # right strip

    # Asymmetric decision: solid dark patches in the artwork border are rare and
    # almost always intentional (eyemark, crop mark). White regions are common
    # (paper background, NFP label). Prefer black; only report white if there is
    # no dark candidate at all and the white signal is very strong.
    if best_black >= 0.20:
        eyemark_color = 'black'
    elif best_white >= 0.85 and best_black < 0.10:
        eyemark_color = 'white'
    else:
        eyemark_color = 'none'

    req = required_color.lower().strip() if required_color else ''

    if eyemark_color in ('black', 'white'):
        if req and eyemark_color != req:
            # Detected color doesn't match what the spec requires
            issues.append({
                'severity': 'critical',
                'message': (
                    f'Wrong eyemark color — detected {eyemark_color.upper()} '
                    f'but spec requires {req.upper()}. '
                    f'The production line photo-eye sensor is calibrated for a {req.upper()} eyemark. '
                    f'Change the eyemark fill to pure {req.upper()} (#{"FFFFFF" if req=="white" else "000000"}) '
                    'before going to press.'
                ),
            })
        else:
            notes.append(
                f'✔ {eyemark_color.upper()} eyemark detected — OK. '
                'Solid eyemark provides reliable photo-eye detection.'
            )
    else:
        # Describe the actual color so the designer knows exactly what to fix
        if avg_luma < 85:
            color_desc = f'dark grey or a dark color (avg brightness {avg_luma:.0f}/255)'
        elif avg_luma < 170:
            color_desc = f'medium grey or a spot color (avg brightness {avg_luma:.0f}/255)'
        else:
            color_desc = f'light grey or a light color (avg brightness {avg_luma:.0f}/255)'

        issues.append({
            'severity': 'critical',
            'message': (
                f'Eyemark is not solid black or solid white — detected as {color_desc}. '
                'The production line photo-eye sensor requires a solid BLACK (#000000) '
                'or solid WHITE (#FFFFFF) eyemark. A colored or grey eyemark WILL cause '
                'missed or false triggers on the bagger/sealer. '
                'Change the eyemark to pure black or pure white before going to press.'
            ),
        })

    return {'issues': issues, 'notes': notes, 'eyemark_color': eyemark_color, 'required_color': req}


# ── Check 4: Spelling / brand name ───────────────────────────────────────────

_MISSPELLINGS = {
    # Only check for the space variant — OCR can't reliably read the ® symbol
    # from outlined-text PDFs, so the (?!®) check produces constant false positives.
    r'pro\s+dough':              'ProDough (should be one word, no space)',
    r'\bcheescake\b':            'cheesecake',
    r'\bbanna\b':                'banana',
    r'\bchoclate\b':             'chocolate',
    r'\bvanila\b':               'vanilla',
    r'\bcarmel\b':               'caramel',
    r'\bcinamon\b':              'cinnamon',
    r'\bstrwberry\b':            'strawberry',
    r'\brasberry\b':             'raspberry',
    r'\braspbery\b':             'raspberry',
    r'\bprotien\b':              'protein',
    r'\bingrediant\b':           'ingredient',
    r'\bartifical\b':            'artificial',
    r'\bnatrual\b':              'natural',
    r'\bexellent\b':             'excellent',
    r'\bnutrional\b':            'nutritional',
}


def _check_spelling(ocr_text: str, fname: str) -> dict:
    issues, notes = [], []

    for pattern, correction in _MISSPELLINGS.items():
        if re.search(pattern, ocr_text, re.IGNORECASE):
            issues.append({
                'severity': 'warning',
                'message': (
                    f'Possible misspelling detected → should be "{correction}". '
                    'OCR on outlined-text PDFs can produce false positives; verify on the actual artwork file.'
                ),
            })

    if re.search(r'\bflavour\b', ocr_text, re.IGNORECASE):
        notes.append('UK spelling "flavour" detected. US market labels should use "flavor".')

    notes.append(
        'ProDough® wordmark check: verify capital P, capital D, no space, and the ® symbol appear correctly. '
        'Social handles (@prodoughshop) and website URLs (prodoughshop.com) are excluded from this check.'
    )

    _tl_spell = ocr_text.lower()
    _brand_found = bool(re.search(r'\bprodough\b', _tl_spell))
    return {'issues': issues, 'notes': notes, 'brand_found': _brand_found}


# ── Generic brand spelling check ─────────────────────────────────────────────

# Generic food misspellings — same as _MISSPELLINGS but without ProDough-specific patterns
_GENERIC_MISSPELLINGS = {
    r'\bcheescake\b':  'cheesecake',
    r'\bbanna\b':      'banana',
    r'\bchoclate\b':   'chocolate',
    r'\bvanila\b':     'vanilla',
    r'\bcarmel\b':     'caramel',
    r'\bcinamon\b':    'cinnamon',
    r'\bstrwberry\b':  'strawberry',
    r'\brasberry\b':   'raspberry',
    r'\braspbery\b':   'raspberry',
    r'\bprotien\b':    'protein',
    r'\bingrediant':   'ingredient',
    r'\bartifical\b':  'artificial',
    r'\bnatrual\b':    'natural',
    r'\bexellent\b':   'excellent',
    r'\bnutrional\b':  'nutritional',
}


def _check_spelling_generic(ocr_text: str, brand_name: str) -> dict:
    issues, notes = [], []

    for pattern, correction in _GENERIC_MISSPELLINGS.items():
        if re.search(pattern, ocr_text, re.IGNORECASE):
            issues.append({
                'severity': 'warning',
                'message': (
                    f'Possible misspelling detected → should be "{correction}". '
                    'OCR on outlined-text PDFs can produce false positives; verify on the actual artwork file.'
                ),
            })

    if re.search(r'\bflavour\b', ocr_text, re.IGNORECASE):
        notes.append('UK spelling "flavour" detected. US market labels should use "flavor".')

    if brand_name:
        notes.append(
            f"verify brand name '{brand_name}' is spelled correctly throughout"
        )

    return {'issues': issues, 'notes': notes}


# ── Module-level FDA regex (shared between _check_fda and _check_fda_light) ───

# Primary disclaimer pattern (well-formatted text)
_DISCLAIMER_RE = re.compile(
    r'(?:this\s+)?statement[s]?\s+ha(?:s|ve)\s+not\s+been\s+evaluated'
    r'.{0,400}?'
    r'not\s+intended\s+to\s+diagnose.{0,120}disease',
    re.IGNORECASE | re.DOTALL,
)
# Fallback: catch OCR-garbled disclaimer — the second sentence alone is distinctive
_DISCLAIMER_TAIL_RE = re.compile(
    r'not\s+intended\s+to\s+diagnose[,.]?\s*treat[,.]?\s*cure[,.]?\s*'
    r'(?:or\s+)?prevent\s+any\s+disease',
    re.IGNORECASE | re.DOTALL,
)

# ── Check 5: FDA compliance ───────────────────────────────────────────────────

_DISEASE_CLAIM_PATTERNS = [
    # Negative lookahead blocks the FDA disclaimer "treat, cure, or prevent any disease"
    # (where "treat" is immediately followed by ", cure" or ", or prevent")
    (r'\btreat[s]?\b(?!\s*[,;.]?\s*(?:cure|prevent|or\s+prevent)).{0,40}(?:disease|disorder|condition|syndrome)',
     'disease treatment claim — prohibited on food/supplement labels without FDA approval'),
    # "cure" in the disclaimer is always followed by ", or prevent" — exclude that
    (r'\bcure[s]?\b(?!\s*[,;.]?\s*(?:or\s+)?prevent).{0,40}(?:disease|disorder|cancer|diabetes)',
     'disease cure claim — prohibited without FDA approval'),
    # Only match specific named diseases, not "any disease" (the disclaimer wording)
    (r'\bprevent[s]?\b.{0,30}(?:cancer\b|diabetes\b|heart\s+disease\b|stroke\b)',
     'disease prevention claim — prohibited without FDA approval (or an approved health claim)'),
    (r'lower[s]?\s+(?:your\s+)?cholesterol\b',
     '"lowers cholesterol" — authorized health claim requiring specific FDA-approved language (21 CFR 101.75)'),
    (r'reduc[es]+\s+.{0,20}risk\s+of\s+(?:cancer|diabetes|heart|stroke)',
     'disease risk-reduction claim — requires an FDA-approved health claim (21 CFR 101.14)'),
    # "diagnose" in the disclaimer is always followed by ", treat" — exclude that
    (r'\bdiagnose[s]?\b(?!\s*[,;.]?\s*(?:treat|cure|prevent)).{0,30}(?:disease|disorder)',
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

    # Initialize here so they're always defined — used inside the 'if not sparse' block below
    _has_nfp_text    = False
    _has_nfp_numbers = False
    _nfp_row_hits    = 0

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
            r'\bsunflower\b', r'\bcitric\s+acid\b', r'\bxanthan\b',
            r'\bnatural\s+flavor', r'\bartificial\s+flavor',
            r'\bmilk\s+powder\b', r'\bnon.fat\s+milk\b', r'\bskim\s+milk\b',
            r'\bcocoa\b', r'\bpotassium\b', r'\bvitamin\b',
            r'\bprotein\s+isolate\b', r'\bprotein\s+concentrate\b',
            r'\bmct\s+oil\b', r'\bguar\s+gum\b', r'\bsea\s+salt\b',
            r'\bmonk\s+fruit\b', r'\bstevia\s+leaf\b', r'\bcoconut\s+(milk|powder|flour|oil)\b',
            r'\bcinnamon\b', r'\btumeric\b|\bturmeric\b',
            r'\bless\s+than\s+\d', r'\bcontains\s+less\s+than\b',
        ]
        _has_ingredients = any(re.search(p, tl) for p in _ingredient_signals)
        # Suppress the absence warning when the NFP is detected — on multi-panel flat
        # layouts (pouches, bags) the ingredient text is often outlined or positioned
        # outside the OCR region, but if we can read the NFP the ingredient list is
        # almost certainly present. A missing ingredient panel is a visual defect that
        # human review catches immediately.
        _nfp_found = _has_nfp_text or _has_nfp_numbers or _nfp_row_hits >= 2
        if not _has_ingredients and not _nfp_found:
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
    # "Contains:" is the FALCPA-required declaration. "Allergy Warning" / "may contain"
    # are voluntary cross-contact advisories — they do NOT satisfy the FALCPA requirement.
    # Require "contains" immediately followed by a known allergen word so that
    # ingredient phrases like "contains less than 2% of..." don't give false passes.
    _ALLERGEN_WORDS = (
        r'milk|dairy|whey|wheat|peanut|soy|egg|fish|shellfish|'
        r'tree\s*nut|sesame|almond|cashew|walnut|pecan|hazelnut|pistachio|macadamia'
    )
    # Primary: standard "Contains: [allergen]" — accept colon, semicolon, comma, dash, or nothing
    # (OCR commonly misreads colons as semicolons or drops them entirely)
    has_contains_stmt = bool(re.search(
        r'\bcontains?\s*[:.;,\-]?\s*(?:' + _ALLERGEN_WORDS + r')', tl
    ))
    # Fallback 1: "contains" within 6 non-letter characters of allergen word —
    # catches OCR artifacts like "contains|milk", "contains—milk", stray glyphs, etc.
    if not has_contains_stmt:
        has_contains_stmt = bool(re.search(
            r'\bcontains?[^a-z]{0,6}(?:' + _ALLERGEN_WORDS + r')', tl
        ))
    # Fallback 2: match "ontains: [allergen]" (without leading "C") —
    # multi-column label layouts cause Tesseract to corrupt the "C" in "Contains"
    # with adjacent NFP column text (e.g., OCR reads "r|30 ontains: Milk")
    if not has_contains_stmt:
        has_contains_stmt = bool(re.search(
            r'\bontains?\s*[:.;,\-]?\s*(?:' + _ALLERGEN_WORDS + r')', tl
        ))
    has_advisory = bool(re.search(
        r'\ballergy\b|\ballergen\b|allergy\s+info|allergy\s+warning|may\s+contain', tl
    ))

    fname_tl = fname.lower() + ' ' + tl

    # Filename-based product-type signals (primary: filename is authoritative for product type)
    is_whey_product     = bool(re.search(r'\bwhey\b', fname_tl))
    is_non_dairy_milk   = bool(re.search(  # only suppresses the milk-specific check
        r'\bbeef\b|\bcollagen\b|\bbovine\b|\bplant\b|\bpea\s+protein\b|\brice\s+protein\b|\bvegan\b',
        fname_tl))
    is_wheat_product    = bool(re.search(r'\bpancake\b|\bflour\b|\bbreading\b', fname.lower()))
    is_peanut_product   = bool(re.search(r'\bpeanut\b|\bpb\b', fname.lower()))
    _TREE_NUTS = r'\balmond\b|\bcashew\b|\bwalnut\b|\bpecan\b|\bhazelnut\b|\bpistachio\b|\bmacadamia\b'
    is_tree_nut_product = bool(re.search(_TREE_NUTS, fname_tl))

    # Per-allergen OCR presence
    _milk_in_ocr      = bool(re.search(r'\bmilk\b|\bnon.fat\s+milk\b|\bmilk\s+powder\b|\bskim\s+milk\b|\bdairy\b|\bcasein\b|\bwhey\b|\blactose\b', tl))
    _peanut_in_ocr    = bool(re.search(r'\bpeanut\b', tl))
    _tree_nut_in_ocr  = bool(re.search(_TREE_NUTS + r'|\btree\s+nut\b', tl))
    _soy_in_ocr       = bool(re.search(r'\bsoy(?:bean)?\b|\bsoy\s+lecithin\b', tl))
    _egg_in_ocr       = bool(re.search(r'\begg\b|\begg\s+white\b|\balbumin\b', tl))
    _fish_in_ocr      = bool(re.search(r'\bfish\b|\bsalmon\b|\btuna\b|\bpollock\b|\bcod\b|\btilapia\b', tl))
    _shellfish_in_ocr = bool(re.search(r'\bshrimp\b|\bcrab\b|\blobster\b|\bscallop\b|\bclam\b|\bshellfish\b', tl))
    _sesame_in_ocr    = bool(re.search(r'\bsesame\b|\btahini\b', tl))
    _wheat_in_ocr     = bool(re.search(r'\bwheat\b|\bgluten\b', tl))

    # Any of the 9 FALCPA major allergens found anywhere in OCR text
    _any_allergen_in_ocr = (
        _milk_in_ocr or _peanut_in_ocr or _tree_nut_in_ocr or _soy_in_ocr or
        _egg_in_ocr or _fish_in_ocr or _shellfish_in_ocr or _sesame_in_ocr or _wheat_in_ocr
    )

    # ── Fallback 3: "contains:" declaration marker + allergen within 200 chars ─
    # Handles OCR fragmentation where column-layout labels split "Contains:"
    # and the allergen word across lines or OCR passes. "Contains less than X%"
    # never uses a colon, so matching "contains:" is specific to the declaration.
    if not has_contains_stmt:
        for _cm in re.finditer(r'\bcontains?\s*:\s*', tl):
            _window = tl[_cm.end():_cm.end() + 200]
            if re.search(r'(?:' + _ALLERGEN_WORDS + r')', _window):
                has_contains_stmt = True
                break

    # ── Fallback 4: "contains:" present and any allergen detected elsewhere in text ─
    # Last resort when OCR splits the declaration across pages/passes completely.
    if not has_contains_stmt and _any_allergen_in_ocr:
        if re.search(r'\bcontains?\s*:', tl):
            has_contains_stmt = True

    # ── Fallback 5: known-allergen product where that allergen IS visible in OCR ─
    # Step 1 (below) already fires a critical flag when the allergen is MISSING from
    # OCR entirely. If the allergen IS present in OCR, the ingredient list references
    # it — meaning the Contains statement very likely exists but was OCR-fragmented.
    # Suppress the general "no Contains" warning to avoid double-flagging compliant files.
    if not has_contains_stmt:
        if (is_whey_product    and not is_non_dairy_milk and _milk_in_ocr) or \
           (is_wheat_product   and _wheat_in_ocr)   or \
           (is_peanut_product  and _peanut_in_ocr)  or \
           (is_tree_nut_product and _tree_nut_in_ocr):
            has_contains_stmt = True

    # ── Step 1: Filename-based checks ─────────────────────────────────────────
    # When the filename identifies a known allergen product but OCR can't find
    # the corresponding allergen ingredient — flag that as a critical miss.
    specific_allergen_flagged = False
    if is_whey_product and not is_non_dairy_milk and not _milk_in_ocr and 'whey' not in tl:
        specific_allergen_flagged = True
        issues.append({
            'severity': 'critical',
            'message': (
                'Whey/milk protein product — "milk" allergen not detected in OCR. '
                'FALCPA requires a "Contains: Milk" statement. '
                'Verify the allergen declaration is present on the artwork.'
            ),
        })
    elif is_wheat_product and not _wheat_in_ocr:
        specific_allergen_flagged = True
        issues.append({
            'severity': 'critical',
            'message': (
                'Wheat-containing product — "wheat" allergen not detected in OCR. '
                'FALCPA requires a "Contains: Wheat" statement. '
                'Verify the allergen declaration appears on the artwork.'
            ),
        })
    elif is_peanut_product and not _peanut_in_ocr:
        specific_allergen_flagged = True
        issues.append({
            'severity': 'critical',
            'message': (
                'Peanut product — "peanut" allergen not detected in OCR. '
                'FALCPA requires a "Contains: Peanuts" statement. '
                'Verify the allergen declaration appears on the artwork.'
            ),
        })
    elif is_tree_nut_product and not _tree_nut_in_ocr:
        specific_allergen_flagged = True
        issues.append({
            'severity': 'critical',
            'message': (
                'Tree nut product — tree nut allergen not detected in OCR. '
                'FALCPA requires a "Contains: Tree Nuts" statement. '
                'Verify the allergen declaration appears on the artwork.'
            ),
        })

    # ── Step 2: Contains: declaration check for ALL products ──────────────────
    # If any FALCPA major allergen is found in OCR (from ingredient list), the
    # product MUST have an explicit "Contains: [allergen]" statement.
    # This check runs regardless of product type — not just filename-identified ones.
    # If no allergen is found in OCR at all, we can't determine allergen status
    # (could be allergen-free product), so only flag advisory mismatches.
    if not sparse and not specific_allergen_flagged and not has_contains_stmt:
        if _any_allergen_in_ocr:
            issues.append({
                'severity': 'critical',
                'message': (
                    'Allergen ingredients detected in product text but no "Contains: [allergen]" '
                    'declaration found. FALCPA requires an explicit "Contains:" statement '
                    'whenever a major allergen is present. '
                    'Verify the declaration is present and readable on the artwork.'
                ),
            })
        elif has_advisory:
            issues.append({
                'severity': 'critical',
                'message': (
                    'Cross-contact advisory detected (e.g., "Allergy Warning" / "Made in a facility...") '
                    'but no "Contains:" allergen declaration found. The advisory does NOT satisfy FALCPA — '
                    'a "Contains: [allergens]" statement is still required. Add the declaration.'
                ),
            })

    # ── Manufacturer / distributor info ───────────────────────────────────────
    # Multi-signal: any ONE of these is sufficient evidence of a manufacturer block.
    # OCR frequently garbles small bottom-of-label text, so cast a wide net.
    if not sparse:
        _mfr_phrases = [
            'manufactured by', 'distributed by', 'produced by', 'manufactured for',
            'distributed for', 'packed by', 'bottled by', 'made by',
        ]
        _mfr_entity_words = [
            'llc', 'inc.', 'inc', 'corp.', 'corp', 'company', 'co.',
            'group', 'enterprises', 'nutrition', 'foods', 'labs', 'ops',
            'industries', 'international', 'brands', 'holdings',
        ]
        _US_STATES = (
            r'\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|'
            r'MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|'
            r'TN|TX|UT|VT|VA|WA|WV|WI|WY)\b'
        )
        has_mfr_phrase = any(ind in tl for ind in _mfr_phrases)
        has_entity     = any(ind in tl for ind in _mfr_entity_words)
        has_zip        = bool(re.search(r'\b\d{5}\b', ocr_text))
        has_state_zip  = bool(re.search(_US_STATES + r'\s*\d{5}', ocr_text))
        has_street     = bool(re.search(
            r'\b\d+\s+[NSEW]\.?\s+\d+|\b\d+\s+\w+\s+(st|ave|blvd|dr|rd|ln|way|pkwy|court|ct)\b', tl
        ))

        _mfr_signals = sum([has_mfr_phrase, has_entity, has_zip, has_state_zip, has_street])
        if _mfr_signals == 0:
            issues.append({
                'severity': 'warning',
                'message': (
                    'Manufacturer or distributor name/address not detected via OCR. '
                    'Verify the manufacturer name and place of business appear on the artwork.'
                ),
            })

    # ── Net weight ────────────────────────────────────────────────────────────
    # Accept explicit "Net Wt" text OR any weight measurement. Also accept unit-only
    # patterns (e.g. "lb" / "oz" without a preceding number) — outlined display text
    # often OCRs partially, returning the unit but not the number.
    _has_net_wt = (
        bool(re.search(r'net\s*wt\.?|net\s*weight', tl)) or
        bool(re.search(r'\b\d+\.?\d*\s*(?:oz|lbs?|pounds?|kg|g)\b', tl)) or
        bool(re.search(r'\b(?:oz|lbs?|pounds?)\b', tl))
    )
    if not sparse and not _has_net_wt:
        issues.append({
            'severity': 'warning',
            'message': (
                'No "Net Wt" declaration or weight measurement detected. '
                'Verify the net weight appears on the artwork.'
            ),
        })
    elif re.search(r'net\s*wt\.?|net\s*weight', tl):
        has_g   = bool(re.search(r'net\s*wt.{0,20}\d+\s*g\b', tl))
        has_oz  = bool(re.search(r'\d+\.?\d*\s*oz\b', tl))
        has_lbs = bool(re.search(r'\d+\.?\d*\s*lbs?\b', tl))
        if has_g and not has_oz and not has_lbs:
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
    # Detect the FDA required disclaimer in full or any OCR-garbled fragment.
    # When detected, skip disease claim scanning entirely — the disclaimer by
    # definition means the label is NOT making disease claims. Scanning after
    # stripping is unreliable because OCR can drop words (e.g. "treat any disease"
    # without "cure" still triggers the pattern even after partial stripping).
    _disclaimer_fragments = [
        _DISCLAIMER_RE,
        _DISCLAIMER_TAIL_RE,
        re.compile(r'not\s+intended\s+to\s+(?:diagnose|treat|cure|prevent)', re.IGNORECASE),
        re.compile(r'has\s+not\s+been\s+evaluated\s+by', re.IGNORECASE),
        re.compile(r'these\s+statements?\s+ha(?:s|ve)\s+not\s+been\s+evaluated', re.IGNORECASE),
        re.compile(r'food\s+and\s+drug\s+administration', re.IGNORECASE),
    ]
    disclaimer_present = any(p.search(tl) for p in _disclaimer_fragments)

    if disclaimer_present:
        notes.append(
            'FDA required disclaimer detected — disease claim scanning skipped '
            '(the disclaimer indicates no disease claims are being made).'
        )
    else:
        for pattern, description in _DISEASE_CLAIM_PATTERNS:
            if re.search(pattern, tl, re.IGNORECASE):
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
    # Only flag front-panel label claims, not ingredient qualifiers like
    # "Organic Brown Rice Flour" which are common and don't require the USDA seal.
    _organic_claim_pats = [
        r'\ball\s+organic\b', r'\b100%\s+organic\b', r'usda\s+organic',
        r'certified\s+organic', r'made\s+with\s+organic', r'organic\s+ingredients?\b',
    ]
    if any(re.search(p, tl) for p in _organic_claim_pats):
        if 'usda organic' not in tl and 'certified organic' not in tl:
            issues.append({
                'severity': 'warning',
                'message': (
                    '"Organic" label claim detected without apparent USDA Organic seal or '
                    '"Certified Organic" language. Organic claims require USDA NOP certification '
                    '(7 CFR Part 205).'
                ),
            })

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

    _allergens_detected = []
    if _milk_in_ocr:     _allergens_detected.append('Milk/Dairy')
    if _soy_in_ocr:      _allergens_detected.append('Soy')
    if _wheat_in_ocr:    _allergens_detected.append('Wheat')
    if _egg_in_ocr:      _allergens_detected.append('Egg')
    if _peanut_in_ocr:   _allergens_detected.append('Peanuts')
    if _tree_nut_in_ocr: _allergens_detected.append('Tree Nuts')
    if _fish_in_ocr:     _allergens_detected.append('Fish')
    if _shellfish_in_ocr: _allergens_detected.append('Shellfish')
    if _sesame_in_ocr:   _allergens_detected.append('Sesame')
    return {
        'issues': issues,
        'notes': notes,
        'allergens_found': _allergens_detected,
        'has_contains': has_contains_stmt,
        'sparse': sparse,
    }


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
    disclaimer_present = bool(_DISCLAIMER_RE.search(tl)) or bool(_DISCLAIMER_TAIL_RE.search(tl))
    tl_no_disclaimer = _DISCLAIMER_RE.sub('', tl)
    tl_no_disclaimer = _DISCLAIMER_TAIL_RE.sub('', tl_no_disclaimer)

    if disclaimer_present:
        notes.append(
            'FDA required disclaimer detected — disclaimer text excluded from disease claim scanning.'
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
        bool(re.search(r'net\s*wt\.?|net\s*weight', tl)) or
        bool(re.search(r'\b\d+\.?\d*\s*(?:oz|lbs?|pounds?|kg|g)\b', tl))
    )
    if not sparse and not _has_net_wt:
        issues.append({
            'severity': 'warning',
            'message': (
                'No "Net Wt" declaration or weight measurement detected. '
                'Net quantity of contents is required (15 USC 1453 / 21 CFR 101.105). Verify manually.'
            ),
        })
    elif re.search(r'net\s*wt\.?|net\s*weight', tl):
        has_g   = bool(re.search(r'net\s*wt.{0,20}\d+\s*g\b', tl))
        has_oz  = bool(re.search(r'\d+\.?\d*\s*oz\b', tl))
        has_lbs = bool(re.search(r'\d+\.?\d*\s*lbs?\b', tl))
        if has_g and not has_oz and not has_lbs:
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
        _DIR_PATTERNS = [
            r'right.{0,30}?side.{0,30}?off.{0,15}?\b([1-8])\b',
            r'left.{0,30}?side.{0,30}?off.{0,15}?\b([1-8])\b',
            r'top.{0,30}?first.{0,15}?\b([1-8])\b',
            r'bottom.{0,30}?first.{0,15}?\b([1-8])\b',
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
        notes.append(
            f'Wind direction not detected in PDF — verify manually that the press proof '
            f'specifies {req_label}.'
        )

    return {
        'issues': issues,
        'notes': notes,
        'detected_direction': detected_wind,
        'required_direction': required_wind,
        'required_label': req_label,
    }


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


def _extract_spot_colors(doc) -> set:
    """
    Scan every xref object for /Separation and /DeviceN colorspace arrays
    and return a set of human-readable spot color names.
    """
    spots = set()
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref, compressed=False)
        except Exception:
            continue
        # /Separation /ColorName ...
        for m in re.finditer(r'/Separation\s+/([^\s/\[\]()<>{}]+)', obj):
            name = m.group(1)
            if name.lower() not in _PROCESS_CS:
                spots.add(name)
        # /DeviceN [/Color1 /Color2 ...] — pick non-process names
        for m in re.finditer(r'/DeviceN\s+\[([^\]]+)\]', obj):
            for nm in re.finditer(r'/([^\s/\[\]()<>{}]+)', m.group(1)):
                name = nm.group(1)
                if name.lower() not in _PROCESS_CS:
                    spots.add(name)
    return spots


def _get_ocg_names(doc) -> list:
    """Return Optional Content Group (layer) names from the document."""
    names = []
    try:
        for xref in range(1, doc.xref_length()):
            try:
                obj = doc.xref_object(xref, compressed=False)
            except Exception:
                continue
            if '/Type /OCG' in obj:
                m = re.search(r'/Name\s*\(([^)]+)\)', obj)
                if not m:
                    m = re.search(r'/Name\s+<([^>]+)>', obj)  # hex string
                if m:
                    names.append(m.group(1))
    except Exception:
        pass
    return names


def _check_print_specs(pdf_path: str, brand_config: dict = None,
                       matched_spec: dict = None) -> dict:
    """
    Read actual PDF vector data using PyMuPDF.
    Checks dimensions, bleed, spot/Pantone colors, die lines, and RGB content.
    No OCR — all results are exact.
    If matched_spec is provided (from the Master SKU Spec Sheet), its values
    override brand_config for dimension/material/spot color validation.
    """
    issues, notes = [], []
    brand_config = brand_config or {}
    matched_spec = matched_spec or {}
    proof_type = brand_config.get('proof_type', 'press')
    pts_to_mm = 25.4 / 72

    try:
        import fitz
    except ImportError:
        return {'issues': [], 'notes': ['PDF structure check skipped — pymupdf not installed.']}

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return {'issues': [{'severity': 'warning',
                            'message': f'Could not open PDF for structure analysis: {e}'}], 'notes': []}

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
                notes.append(f'Bleed: {bleed_mm} mm')
            else:
                notes.append('No bleed detected (TrimBox equals MediaBox).')
        else:
            notes.append(f'Page size (MediaBox): {mb_w} × {mb_h} mm')
            if proof_type != 'art':
                notes.append('No TrimBox found. Add a TrimBox before submitting press-ready files.')
            else:
                notes.append('No TrimBox found (art proof — no finishing panel expected). '
                             'Add a TrimBox before submitting press-ready files.')

        # Compare against expected spec dimensions (spec sheet overrides brand_config)
        spec_w = matched_spec.get('trim_width_mm') or brand_config.get('spec_width_mm')
        spec_h = matched_spec.get('trim_height_mm') or brand_config.get('spec_height_mm')
        dim_ref = (tb_w, tb_h) if tb_w else (mb_w, mb_h)
        dim_label = 'trim' if tb_w else 'page'
        if spec_w and spec_h and dim_ref[0]:
            tol = 2.0  # mm
            fits = (
                (abs(dim_ref[0] - spec_w) <= tol and abs(dim_ref[1] - spec_h) <= tol) or
                (abs(dim_ref[0] - spec_h) <= tol and abs(dim_ref[1] - spec_w) <= tol)
            )
            if fits:
                notes.append(f'Dimensions match spec: {spec_w} × {spec_h} mm ✓')
            else:
                # ≥1.9× scale difference indicates a multi-panel flat/die-cut layout where
                # the TrimBox captures all panels unfolded (front+back+gusset) while the spec
                # stores the finished single-panel size. Flag as a note, not a critical error.
                scale = max(dim_ref[0] / spec_w, dim_ref[1] / spec_h,
                            dim_ref[0] / spec_h, dim_ref[1] / spec_w)
                if scale >= 1.9:
                    notes.append(
                        f'File {dim_label} size ({dim_ref[0]} × {dim_ref[1]} mm) appears to be a '
                        f'multi-panel layout — spec finished size is {spec_w} × {spec_h} mm. '
                        'Verify overall layout dimensions with your print supplier.'
                    )
                else:
                    issues.append({'severity': 'critical',
                                   'message': f'Dimension mismatch — {dim_label} size is '
                                              f'{dim_ref[0]} × {dim_ref[1]} mm, '
                                              f'spec requires {spec_w} × {spec_h} mm (±2 mm). '
                                              'Verify the artwork size with your print supplier before going to press.'})

        # ── Spot / Pantone colors ─────────────────────────────────────────────
        spot_colors = sorted(_extract_spot_colors(doc))
        if spot_colors:
            notes.append(f'Spot colors in file: {", ".join(spot_colors)}')
        else:
            notes.append('No spot colors detected (process CMYK / RGB only).')

        # Validate required spot colors from spec sheet
        req_spots_raw = matched_spec.get('spot_colors', '')
        if req_spots_raw:
            # Filter out blank/placeholder values like "PMS --", "--", "-", "N/A", "TBD"
            _PLACEHOLDER = re.compile(r'^[-–—]+$|^n/?a$|^tbd$|^none$|^pms\s*[-–—]+$', re.I)
            req_spots = [
                s.strip() for s in req_spots_raw.split(',')
                if s.strip() and not _PLACEHOLDER.match(s.strip())
            ]

            def _norm_pantone(name: str) -> str:
                # Normalize prefix: "PANTONE" and "PMS" both reduced to just the number/name
                n = re.sub(r'^(pantone|pms)\s*', '', name.strip(), flags=re.I)
                # Strip trailing finish suffix (C=coated, U=uncoated, M=matte, CP, EC, etc.)
                n = re.sub(r'\s+[CUMcum]{1,2}P?\s*$', '', n).strip()
                return n.lower()

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
        ocg_names  = _get_ocg_names(doc)
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

        # ── RGB content ───────────────────────────────────────────────────────
        has_rgb = False
        for xref in range(1, doc.xref_length()):
            try:
                obj = doc.xref_object(xref, compressed=False)
                if '/DeviceRGB' in obj or '/CalRGB' in obj:
                    has_rgb = True
                    break
            except Exception:
                continue
        if has_rgb:
            notes.append('RGB objects detected in PDF (may include ICC profiles or embedded images — '
                         'verify CMYK output intent with your print supplier if color accuracy is critical).')
        else:
            notes.append('Color mode: CMYK — no RGB content detected ✓')

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
            # Normalize whitespace (collapse newlines/extra spaces) before comparing so that
            # a material string split across two PDF text lines still matches the spec value.
            _norm = lambda s: re.sub(r'\s+', ' ', s).strip()
            if req_mat and _norm(req_mat).lower() not in _norm(material_found).lower():
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
    finally:
        doc.close()

    return {
        'issues': issues,
        'notes': notes,
        'spot_colors': spot_colors if 'spot_colors' in locals() else [],
        'dimensions': {
            'mediabox_mm': [mb_w, mb_h] if 'mb_w' in locals() else None,
            'trimbox_mm':  [tb_w, tb_h] if tb_w else None,
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
