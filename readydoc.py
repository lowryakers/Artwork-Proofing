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
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

_TIMEOUT = 15

# Map this engine's check keys onto names a human reads in the QA queue.
_CHECK_LABEL = {
    'gtin': 'GTIN / barcode',
    'nfp': 'Nutrition panel vs front',
    'eyemark': 'Eyemark',
    'spelling': 'Spelling & brand names',
    'fda': 'FDA compliance',
    'specs': 'Print specs & dimensions',
    'wind': 'Wind direction',
}

# The engine grades issues critical / warning / info; ReadyDoc stores
# pass / fail / warn. Info is a note, not a finding, so it does not travel.
_SEVERITY = {'critical': 'fail', 'warning': 'warn'}


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


def _checks_from_result(result: dict) -> list:
    """Flatten one file's check output into ReadyDoc's flat check list."""
    out = []
    for key, block in (result.get('checks') or {}).items():
        if not isinstance(block, dict):
            continue
        label = _CHECK_LABEL.get(key, key)
        issues = block.get('issues') or []
        graded = [i for i in issues if _SEVERITY.get(i.get('severity'))]
        if not graded:
            # No findings worth escalating is itself worth recording: a version
            # showing "eyemark: pass" is the evidence that it was looked at.
            if block.get('skipped'):
                continue
            out.append({'name': label, 'result': 'pass'})
            continue
        for issue in graded:
            out.append({
                'name': f"{label} — {str(issue.get('message', ''))[:80]}",
                'result': _SEVERITY[issue['severity']],
                'detail': str(issue.get('message', ''))[:1000],
            })
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
        }
        try:
            _post('/api/artwork/ingest', payload)
        except urllib.error.HTTPError as exc:
            body = ''
            try:
                body = exc.read().decode('utf-8')[:200]
            except Exception:
                pass
            print(f'[readydoc] {result.get("filename")}: HTTP {exc.code} {body}')
        except Exception as exc:
            print(f'[readydoc] {result.get("filename")}: {exc}')


def publish_job_async(job_id: str, results: list) -> None:
    """Fire and forget — the proofing run has already finished and been saved."""
    if not enabled():
        return
    threading.Thread(target=publish_job, args=(job_id, results), daemon=True).start()
