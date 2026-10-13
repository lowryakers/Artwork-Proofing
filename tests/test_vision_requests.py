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
                ('Contains crop (_read_contains_crop)', lambda: pe._read_contains_crop(tmp, [0.3, 0.3, 0.6, 0.7])),
                ('front rings (_read_front_callouts)',
                 lambda: pe._read_front_callouts(tmp, [0.1, 0.2, 0.4, 0.8]))):
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
        # An empty crop read says why, per step (run 142db86d: silent on one of four).
        diag = []
        out = pe._read_nfp_panel(tmp, [0.3, 0.3, 0.6, 0.7], diag=diag)
        check(f'NFP crop: an empty read records why ({diag})',
              out == {} and diag and diag[0].startswith('panel located by vision')
              and any('B sent' in d and '0 fields read' in d for d in diag))
        # The panel mask never paints over the rings. Vision's panel box is
        # approximate and reaches into the ring column on the bottle sleeves (CI
        # runs 38305043657, 38308802272); only a geometrically located panel is
        # masked, and white background is not a panel.
        import base64 as _b64, io as _io
        from PIL import ImageDraw as _D
        ring_img = os.path.join(os.path.dirname(tmp), 'rings.png')
        im = Image.new('RGB', (1600, 1200), 'white')
        _D.Draw(im).ellipse((int(0.46 * 1600), int(0.30 * 1200), int(0.49 * 1600), int(0.34 * 1200)), fill='black')
        im.save(ring_img)
        n = len(bodies)
        pe._read_front_callouts(ring_img, [0.20, 0.20, 0.50, 0.80])
        sent = [c for b in bodies[n:] for m in b['messages'] for c in m['content'] if c.get('type') == 'image']
        dark = (Image.open(_io.BytesIO(_b64.b64decode(sent[0]['source']['data']))).convert('L').getextrema()[0]
                if sent else 255)
        check(f'front rings: a ring inside vision\'s panel box survives the mask (min px {dark})',
              dark < 60)
        # ...while a real panel (white rectangle on printed artwork) IS masked.
        im = Image.new('RGB', (1600, 1200), (190, 140, 90))
        d_ = _D.Draw(im)
        d_.rectangle((400, 300, 700, 900), fill='white')
        d_.text((420, 320), 'Calories 130', fill='black')
        d_.ellipse((1000, 400, 1100, 500), fill='black')
        im.save(ring_img)
        n = len(bodies)
        diag = []
        pe._read_front_callouts(ring_img, [0.25, 0.25, 0.4375, 0.75], diag=diag)
        sent = [Image.open(_io.BytesIO(_b64.b64decode(c['source']['data']))).convert('L')
                for b in bodies[n:] for m in b['messages'] for c in m['content'] if c.get('type') == 'image']
        # 2x2 tiles with 15% overlap: the top-left tile spans 0-57.5% of each axis.
        # The panel (x 407-701, y 300-900 of 1600x1200) falls in it at x 44-76%,
        # y 44-100%; the ring (x 1000-1100, y 400-500) only in the right-hand tiles.
        tl_ = sent[0] if sent else None
        panel_white = tl_ is not None and tl_.crop((int(tl_.width * 0.47), int(tl_.height * 0.47),
                                                   int(tl_.width * 0.73), int(tl_.height * 0.97))).getextrema()[0] > 200
        check(f'front rings: a located panel is masked and the ring beside it kept ({diag[:1]})',
              len(sent) == 4 and 'panel masked at' in diag[0] and panel_white
              and min(t.getextrema()[0] for t in sent[1:]) < 60)
        diag = []
        pe._read_front_callouts(tmp, [0.1, 0.2, 0.4, 0.8], diag=diag)
        check(f'front rings: the read records the artwork size, masking and what came back ({diag})',
              diag and diag[0].startswith('artwork ') and 'panel NOT masked' in diag[0]
              and any('rings:' in d or 'no usable JSON' in d for d in diag))
        diag = []
        pe._read_nfp_panel(tmp, None, diag=diag)
        check('NFP crop: no panel box is recorded as "panel NOT located"',
              diag and diag[0].startswith('panel NOT located by vision'))
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
