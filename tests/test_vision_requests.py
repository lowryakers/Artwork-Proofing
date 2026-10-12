"""Vision calls build valid requests with the installed Anthropic SDK.

    python3 tests/test_vision_requests.py

The other vision tests mock the client with `create(**k)`, which accepts any
keyword — so when anthropic 1.x removed the `temperature` keyword (a
TypeError before any request is sent), every unit test still passed and every
vision read failed in CI (run 37983733775: 45 of 45). This drives the three
call sites through the REAL SDK with an in-process transport that records the
request instead of sending it: no network, no key, no cost.

Exits non-zero on any failure.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    try:
        import anthropic
        import httpx2 as httpx
    except ImportError:
        try:
            import anthropic
            import httpx
        except ImportError:
            print('[skip] anthropic SDK not installed')
            return 0
    from PIL import Image

    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={
            'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'model': 'claude-sonnet-4-6',
            'content': [{'type': 'text', 'text': '{}'}], 'stop_reason': 'end_turn',
            'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}})

    real = anthropic.Anthropic

    class _Recording:
        @staticmethod
        def Anthropic(api_key=None, **kw):
            return real(api_key=api_key or 'test', max_retries=0,
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    tmp = os.path.join(tempfile.mkdtemp(), 'page.png')
    Image.new('RGB', (1600, 1200), 'white').save(tmp)
    prev = (pe.ANTHROPIC_AVAILABLE, pe.PIL_AVAILABLE, pe._anthropic)
    os.environ.setdefault('ANTHROPIC_API_KEY', 'test')
    pe.ANTHROPIC_AVAILABLE, pe.PIL_AVAILABLE, pe._anthropic = True, True, _Recording
    try:
        for name, call in (
                ('full-page read (_claude_vision_ocr)', lambda: pe._claude_vision_ocr(tmp)),
                ('NFP crop (_read_nfp_panel)', lambda: pe._read_nfp_panel(tmp, [0.3, 0.3, 0.6, 0.7])),
                ('Contains crop (_read_contains_crop)', lambda: pe._read_contains_crop(tmp, [0.3, 0.3, 0.6, 0.7]))):
            n = len(bodies)
            out = call()
            sent = bodies[n:]
            status = str((out or {}).get('status', '')) if isinstance(out, dict) else ''
            check(f'{name}: the SDK accepted the call and sent a request '
                  f'({len(sent)} sent{", status " + status if status else ""})',
                  len(sent) >= 1 and not status.startswith('error'))
            check(f'{name}: every request carries temperature 0',
                  bool(sent) and all(b.get('temperature') == 0 for b in sent))
            check(f'{name}: model is claude-sonnet-4-6',
                  bool(sent) and all(b.get('model') == 'claude-sonnet-4-6' for b in sent))
    finally:
        pe.ANTHROPIC_AVAILABLE, pe.PIL_AVAILABLE, pe._anthropic = prev

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print(f'All vision request checks pass (anthropic {anthropic.__version__}).')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
