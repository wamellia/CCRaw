"""Wire contracts shared by text and image provider request adapters."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ccraw import image_generation, nl_edit


@pytest.fixture
def endpoint():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            calls.append((self.command, self.path, dict(self.headers.items()), body))
            result = b'{"ok":true}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(result)))
            self.end_headers()
            self.wfile.write(result)

        do_GET = do_POST = respond

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/provider?request=one', calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('payload', [None, False, 0, {}, {'prompt': '晨光', 'watermark': False}])
def test_text_and_image_transports_share_json_wire_contract(endpoint, payload):
    url, calls = endpoint
    headers = {'Authorization': 'Bearer fixture-key', 'X-Request': 'text'}
    assert nl_edit._http(url, payload, headers) == {'ok': True}
    assert (
        image_generation.GenerationClient().request(url, payload, 'fixture-key') == b'{"ok":true}'
    )
    assert len(calls) == 2
    for method, target, received_headers, body in calls:
        assert method == ('GET' if payload is None else 'POST')
        assert target == '/provider?request=one'
        assert received_headers['User-Agent'] == 'CCRaw'
        assert received_headers['Authorization'] == 'Bearer fixture-key'
        if payload is None:
            assert body == b'' and 'Content-Type' not in received_headers
        else:
            assert received_headers['Content-Type'] == 'application/json'
            assert json.loads(body) == payload
    assert headers == {'Authorization': 'Bearer fixture-key', 'X-Request': 'text'}


@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
def test_multipart_text_excludes_non_text_anthropic_blocks_and_keeps_usage(provider, monkeypatch):
    settings = nl_edit.default_settings()
    settings['provider'] = provider
    settings['providers'][provider].update(model='fixture', key='fixture-key')
    blocks = [{'type': 'text', 'text': '{"value":'}, {'type': 'text', 'text': '1}'}]
    if provider == 'anthropic':
        data = {
            'content': [{'type': 'thinking', 'text': 'private reasoning'}, *blocks],
            'usage': {'input_tokens': 2, 'cache_read_input_tokens': 3, 'output_tokens': 4},
        }
    else:
        data = {
            'choices': [{'message': {'content': blocks}}],
            'usage': {'prompt_tokens': 5, 'completion_tokens': 4},
        }
    calls = []

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        return data

    monkeypatch.setattr(nl_edit, '_post', post)
    usage = {}
    messages = [{'role': 'system', 'content': 'Rules'}, {'role': 'user', 'content': 'Edit'}]
    assert nl_edit.complete(nl_edit.active(settings), messages, usage=usage) == '{"value":1}'
    assert usage == {'input': 5, 'output': 4, 'total': 9}
    assert len(calls) == 1
    assert messages == [{'role': 'system', 'content': 'Rules'}, {'role': 'user', 'content': 'Edit'}]
