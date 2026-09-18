"""
File finished proofing jobs into ReadyDoc.

Why this exists: ReadyDoc holds the artwork version history the QA team reads —
which revision is current, what the checks said, who released it. That history
should not depend on anyone remembering to record it. This service already
receives every file Shaun sends and every proof Mike returns, and already
rasterises and checks them, so it is the natural place for the record to come
from. The version files itself as a side effect of the check.

Entirely optional and fails quietly. With READYDOC_URL or READYDOC_TOKEN unset,
publish_job() is a no-op and proofing behaves exactly as it did before. A
network error is logged and swallowed — a ReadyDoc outage must never fail a
proofing run that otherwise succeeded.

Env:
  READYDOC_URL    base URL, e.g. https://start.powder-ops.com
  READYDOC_TOKEN  same value as PRODUCT_MASTER_TOKEN on the ReadyDoc service
"""
import json
import mimetypes
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

_TIMEOUT = 15

# Map this engine's check keys onto names a human reads in the QA queue.
_CHECK_LABEL = {
    'gtin': 'GTIN / barcode',
    'netwt': 'Nutrition panel vs net weight',
    'nfp': 'Nutrition panel vs front',
    'eyemark': 'Eyemark',
    'spelling': 'Spelling & brand names',
    'fda': 'FDA compliance',
    'prep': 'Prep block type',
    'ingredients': 'Ingredient statement changes',
    'claims': 'Claims review',
    'specs': 'Print specs & dimensions',
    'wind': 'Wind direction',
}

# The engine grades issues critical / warning / suspect / review / info;
# ReadyDoc stores only pass / fail / warn (server clamps anything else to warn
# anyway). Info is a note, not a finding, so it does not travel. suspect and
# review are real findings — "the tool's read is doubtful" or "needs a human
# look" — and must never be silently dropped into a false pass.
_SEVERITY = {'critical': 'fail', 'warning': 'warn', 'suspect': 'warn', 'review': 'warn'}

# Check-block statuses that mean "not evaluated" — must file as warn, never
# pass, even when the block carries no graded issues.
_UNVERIFIED_STATUSES = {'UNVERIFIED', 'UNKNOWN', 'SUSPECT'}


def enabled() -> bool:
    return bool(os.environ.get('READYDOC_URL') and os.environ.get('READYDOC_TOKEN'))


def _post(path: str, payload: dict) -> dict:
    base = os.environ['READYDOC_URL'].rstrip('/')
    token = os.environ['READYDOC_TOKEN']
    req = urllib.request.Request(
        f'{base}{path}?token={urllib.parse.quote(token)}',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'artwork-proofing'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode('utf-8') or '{}')


def _get(path: str, params: dict) -> dict:
    base = os.environ['READYDOC_URL'].rstrip('/')
    token = os.environ['READYDOC_TOKEN']
    q = dict(params or {})
    q['token'] = token
    url = f'{base}{path}?' + urllib.parse.urlencode({k: v for k, v in q.items() if v})
    req = urllib.request.Request(url, headers={'User-Agent': 'artwork-proofing'}, method='GET')
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode('utf-8') or '{}')


def _post_multipart(path: str, fields: dict, file_path: str, file_field: str = 'files') -> dict:
    """POST a single file as multipart/form-data (stdlib only — no new deps).

    Mirrors _post's auth (READYDOC_TOKEN query param). `fields` are sent as
    plain form fields alongside the file (ReadyDoc's ingest/files route reads
    `kind` this way); `file_field` matches the multer field name on that route.
    """
    base = os.environ['READYDOC_URL'].rstrip('/')
    token = os.environ['READYDOC_TOKEN']
    boundary = 'readydoc-boundary-' + os.urandom(16).hex()

    with open(file_path, 'rb') as fh:
        file_bytes = fh.read()
    filename = os.path.basename(file_path)
    content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'

    body = bytearray()
    for key, value in (fields or {}).items():
        body += f'--{boundary}\r\n'.encode()
        body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        body += f'{value}\r\n'.encode()
    body += f'--{boundary}\r\n'.encode()
    body += (f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{filename}"\r\n').encode()
    body += f'Content-Type: {content_type}\r\n\r\n'.encode()
    body += file_bytes
    body += f'\r\n--{boundary}--\r\n'.encode()

    req = urllib.request.Request(
        f'{base}{path}?token={urllib.parse.quote(token)}',
        data=bytes(body),
        headers={
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'User-Agent': 'artwork-proofing',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode('utf-8') or '{}')


def _attach_file(version_id: str, kind: str, file_path) -> None:
    """Attach one file (preview PNG or print PDF) to a just-ingested version.

    Best-effort: a missing path, a 503 (ReadyDoc's R2 storage not configured),
    or any other failure is logged and swallowed — the JSON ingest already
    succeeded, and a thumbnail is a nice-to-have, never a reason to fail the
    proof run.
    """
    if not file_path or not os.path.exists(file_path):
        return
    try:
        _post_multipart(f'/api/artwork/ingest/{version_id}/files', {'kind': kind}, file_path)
    except urllib.error.HTTPError as exc:
        body = ''
        try:
            body = exc.read().decode('utf-8')[:200]
        except Exception:
            pass
        print(f'[readydoc] file attach ({kind}): HTTP {exc.code} {body}')
    except Exception as exc:
        print(f'[readydoc] file attach ({kind}): {exc}')


def fetch_prior_snapshot(gtin=None, sku=None) -> dict:
    """Return the most recent stored label snapshot for this product from
    ReadyDoc's artwork history, or None. Never raises — a ReadyDoc outage or a
    first-ever proof simply yields no prior version to compare against.

    Endpoint assumption: GET /api/artwork/snapshot?gtin=&sku= returning
    {"snapshot": {...}} (or the snapshot object directly). Adjust here if the
    ReadyDoc read route differs.
    """
    if not enabled() or (not gtin and not sku):
        return None
    try:
        data = _get('/api/artwork/snapshot', {'gtin': gtin, 'sku': sku})
    except Exception as exc:
        print(f'[readydoc] snapshot fetch: {exc}')
        return None
    if isinstance(data, dict):
        snap = data.get('snapshot', data)
        return snap if isinstance(snap, dict) and snap.get('ingredients') is not None else None
    return None


def _checks_from_result(result: dict) -> list:
    """Flatten one file's check output into ReadyDoc's flat check list.

    Never files a false pass: an issue whose severity we don't recognize warns
    rather than vanishing, and a check block left UNVERIFIED/UNKNOWN/SUSPECT
    (no graded issues, so nothing below would otherwise fire) files as warn —
    "not evaluated" must never read as "evaluated and fine."
    """
    out = []
    for key, block in (result.get('checks') or {}).items():
        if not isinstance(block, dict):
            continue
        label = _CHECK_LABEL.get(key, key)
        issues = block.get('issues') or []
        graded = []
        for issue in issues:
            sev = issue.get('severity')
            mapped = _SEVERITY.get(sev)
            if mapped is None and sev not in (None, 'info'):
                # A severity this map doesn't know about yet is not "nothing" —
                # warn rather than let an unrecognized finding disappear.
                mapped = 'warn'
            if mapped:
                graded.append((issue, mapped))

        if graded:
            for issue, mapped in graded:
                out.append({
                    'name': f"{label} — {str(issue.get('message', ''))[:80]}",
                    'result': mapped,
                    'detail': str(issue.get('message', ''))[:1000],
                })
            continue

        if block.get('skipped'):
            continue

        status = str(block.get('status', '')).upper()
        if status in _UNVERIFIED_STATUSES:
            # Lead with the limitation, not reassurance. Prefer the engine's own
            # "not verified" note when it left one; else a generic fallback.
            note = next((n for n in (block.get('notes') or [])
                        if 'not verified' in str(n).lower()), None)
            detail = str(note or f'{label} could not be fully evaluated — not verified.')[:1000]
            out.append({'name': label, 'result': 'warn', 'detail': detail})
            continue

        # No findings worth escalating is itself worth recording: a version
        # showing "eyemark: pass" is the evidence that it was looked at.
        out.append({'name': label, 'result': 'pass'})
    return out


def _gtin_of(result: dict):
    """The barcode actually decoded off the artwork, if there was exactly one.

    Only a single unambiguous decode is used to identify the product. Two
    different barcodes on one file is a finding, not an identification.
    """
    found = (result.get('checks', {}).get('gtin', {}) or {}).get('found_gtins') or []
    uniq = {str(g).strip() for g in found if str(g).strip()}
    return uniq.pop() if len(uniq) == 1 else None


def publish_job(job_id: str, results: list) -> None:
    """Send every identifiable file in a finished job. Never raises."""
    if not enabled():
        return

    for result in results or []:
        if not isinstance(result, dict) or result.get('error'):
            continue
        gtin = _gtin_of(result)
        sku = (result.get('matched_spec') or {}).get('sku')
        # Without a barcode or a matched spec row there is nothing to attach the
        # version to. Filing it against a guess would be worse than not filing.
        if not gtin and not sku:
            continue

        payload = {
            # Per FILE, not per job: one job can carry a pouch and a stick, and
            # they are different products with different version histories.
            'job_id': f"{job_id}:{result.get('filename', '')}",
            'gtin': gtin,
            'sku': sku,
            'summary': f"Proofed {result.get('filename', '')} — severity {result.get('severity', 'unknown')}",
            'checks': _checks_from_result(result),
            # The label-content snapshot so a later revision can be compared against
            # this version (Checks 7 & 8). Additive — ignored by older ReadyDoc.
            'snapshot': result.get('snapshot'),
            # Which locked die this flavor was proofed against (template id +
            # version + fixture sha + mm constants). Additive — ignored by older
            # ReadyDoc; present only for bottle-sleeve (and future templated) jobs.
            'format': result.get('format'),
            'die_template': result.get('die_template'),
        }
        try:
            resp = _post('/api/artwork/ingest', payload)
        except urllib.error.HTTPError as exc:
            body = ''
            try:
                body = exc.read().decode('utf-8')[:200]
            except Exception:
                pass
            print(f'[readydoc] {result.get("filename")}: HTTP {exc.code} {body}')
            continue
        except Exception as exc:
            print(f'[readydoc] {result.get("filename")}: {exc}')
            continue

        # Attach the rendered preview (and the source print PDF, when available)
        # to the version just ingested, so the Artwork board shows the actual
        # pack instead of a generic icon. Best-effort — see _attach_file.
        version_id = (resp or {}).get('version_id')
        if not version_id:
            print(f'[readydoc] {result.get("filename")}: ingest ok, no version_id '
                 f'in response — skipping file attach')
            continue
        _attach_file(version_id, 'preview', result.get('img_path'))
        _attach_file(version_id, 'print_pdf', result.get('pdf_path'))


def publish_job_async(job_id: str, results: list) -> None:
    """Fire and forget — the proofing run has already finished and been saved."""
    if not enabled():
        return
    threading.Thread(target=publish_job, args=(job_id, results), daemon=True).start()
