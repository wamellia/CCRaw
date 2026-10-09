"""Image generation uses real HTTP against a local fixture, never a paid API."""

import base64
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image
import pytest
from ccraw import image_generation as gen


def picture():
    buffer = io.BytesIO()
    Image.new('RGB', (96, 64), '#2871ad').save(buffer, 'PNG')
    return buffer.getvalue()


def test_truncated_jpeg_is_rejected_before_completion():
    import numpy as np

    buffer = io.BytesIO()
    Image.fromarray(np.random.default_rng(5).integers(0, 256, (100, 100, 3), dtype='uint8')).save(
        buffer, 'JPEG'
    )
    with pytest.raises(gen.GenerationError):
        gen.image_format(buffer.getvalue()[:-100])


def test_uninitialized_reference_keeps_edits_and_adopts_camera_develop(monkeypatch):
    from ccraw import engine, model
    import numpy as np

    edits = model.recipe()
    edits['adjustments']['exposure'] = 1.2
    develop = dict(edits['develop'], mode='camera')
    metadata = {'develop': develop, 'white_balance': dict(edits['white_balance'], mode='camera')}
    captured = []
    monkeypatch.setattr(engine, 'load_image', lambda *a, **kw: (np.ones((30, 40, 3)), metadata))
    monkeypatch.setattr(
        engine, 'process', lambda source, recipe: captured.append(recipe) or source * 0.5
    )
    gen.prepare_reference('unopened.raw', edits, initialized=False)
    assert captured[0]['develop'] == develop
    assert captured[0]['adjustments']['exposure'] == 1.2
    assert edits['develop']['mode'] != 'camera'


class Service:
    def __init__(self):
        self.calls = []
        self.delay = 0
        self.status = 200
        self.url_result = False
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                service.calls.append((self.path, self.headers.get('Authorization'), payload))
                time.sleep(service.delay)
                if service.status != 200:
                    data = {'error': {'message': 'failed secret-key'}}
                elif service.url_result:
                    data = {'data': [{'url': service.base + '/result.png'}]}
                else:
                    data = {'data': [{'b64_json': base64.b64encode(picture()).decode()}]}
                body = json.dumps(data).encode()
                self.send_response(service.status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                service.calls.append((self.path, self.headers.get('Authorization'), None))
                body = picture()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def settings(self):
        return dict(gen.default_settings(), base_url=self.base + '/api/v3', key='secret-key')

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def service():
    instance = Service()
    yield instance
    instance.close()


@pytest.mark.parametrize('url_result', [False, True])
def test_generation_protocol_and_result_bytes(service, url_result):
    service.url_result = url_result
    client = gen.GenerationClient()
    result = client.generate(service.settings(), '自然光写真', 'data:image/jpeg;base64,abc')
    assert result == picture()
    endpoint, authorization, payload = service.calls[0]
    assert endpoint == '/api/v3/images/generations'
    assert authorization == 'Bearer secret-key'
    assert payload['image'].startswith('data:image/jpeg;base64,')
    assert payload['prompt'] == '自然光写真'
    assert payload['size'] == '2K' and payload['response_format'] == 'b64_json'
    assert payload['sequential_image_generation'] == 'disabled'
    if url_result:
        assert service.calls[1][1] is None  # never forward the API key to an image URL


def test_paid_request_is_not_retried_and_errors_hide_key(service):
    service.status = 429
    with pytest.raises(gen.GenerationError) as error:
        gen.GenerationClient().generate(service.settings(), '写真')
    assert 'secret-key' not in str(error.value)
    assert len(service.calls) == 1


def test_cancel_interrupts_active_request_and_never_returns_an_image(service):
    service.delay = 2
    client = gen.GenerationClient()
    results = []

    def work():
        try:
            client.generate(service.settings(), '写真')
        except Exception as error:
            results.append(error)

    worker = threading.Thread(target=work)
    worker.start()
    deadline = time.monotonic() + 2
    while not service.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    client.cancel()
    worker.join(1)
    assert not worker.is_alive()
    assert len(results) == 1 and isinstance(results[0], InterruptedError)


def test_settings_encrypt_keys_and_templates_survive_restart(tmp_path):
    path = tmp_path / 'settings.json'
    settings = dict(gen.default_settings(), key='secret-key')
    gen.save_settings(settings, path)
    assert 'secret-key' not in path.read_text() if __import__('os').name == 'nt' else True
    assert gen.load_settings(path)['key'] == 'secret-key'
    rows = gen.load_templates(tmp_path)
    assert len(rows) == 81 and len({t['id'] for t in rows}) == 81
    saved = gen.save_template('我的模板', '保留主体，调整为晨光', root=tmp_path)
    assert saved in gen.load_templates(tmp_path)


def test_history_is_durable_recovers_interrupted_jobs_and_has_atomic_results(tmp_path):
    store = gen.GenerationStore(tmp_path)
    task = store.enqueue('写真', '晨光', reference='', recipe=None, size='2K')
    store.update(task['id'], 'running')
    reopened = gen.GenerationStore(tmp_path)
    reopened.recover()
    assert reopened.get(task['id'])['status'] == 'cancelled'
    with pytest.raises(InterruptedError):
        reopened.save_result(task['id'], picture())
    task = reopened.enqueue('写真', '晨光', reference='', recipe=None, size='2K')
    path = reopened.save_result(task['id'], picture())
    assert path.is_file() and path.suffix == '.png'
    assert reopened.get(task['id'])['status'] == 'completed'
    assert Image.open(path).size == (96, 64)
    assert not list(tmp_path.rglob('*.tmp'))
    with pytest.raises(gen.GenerationError):
        reopened.save_result(task['id'], b'invalid image')
    assert path.read_bytes() == picture()


def test_reference_uses_edits_without_mutating_recipe_or_transmitting_exif(tmp_path):
    from ccraw import model

    path = tmp_path / 'original.jpg'
    Image.new('RGB', (300, 200), (70, 80, 90)).save(path)
    recipe = model.recipe()
    recipe['adjustments']['exposure'] = 1
    before = json.dumps(recipe, sort_keys=True)
    data = gen.prepare_reference(path, recipe)
    mime, encoded = data.split(',', 1)
    assert mime == 'data:image/jpeg;base64'
    image = Image.open(io.BytesIO(base64.b64decode(encoded)))
    assert image.size == (300, 200) and not image.getexif()
    assert image.getpixel((100, 100))[0] > 70
    assert json.dumps(recipe, sort_keys=True) == before


def test_user_can_remove_custom_templates_and_terminal_results(tmp_path):
    template = gen.save_template('自定义', '日落光线', root=tmp_path)
    gen.delete_template(template['id'], root=tmp_path)
    assert len(gen.load_templates(tmp_path)) == 81
    with pytest.raises(gen.GenerationError):
        gen.delete_template('pb-01', root=tmp_path)
    store = gen.GenerationStore(tmp_path)
    task = store.enqueue('日落', '日落光线')
    with pytest.raises(gen.GenerationError):
        store.delete(task['id'])
    path = store.save_result(task['id'], picture())
    preview = store.preview_path(store.get(task['id']))
    assert preview.is_file() and Image.open(preview).size == (96, 64)
    store.update(task['id'], 'cancelled')  # terminal tasks cannot be overwritten
    assert store.get(task['id'])['status'] == 'completed'
    store.delete(task['id'])
    assert not path.exists() and not preview.exists() and not store.history()


def test_result_commit_cleans_files_if_preview_write_fails(tmp_path, monkeypatch):
    store = gen.GenerationStore(tmp_path)
    task = store.enqueue('写真', '自然光')

    def fail(*_):
        raise OSError('disk unavailable')

    monkeypatch.setattr(store, 'write_preview', fail)
    with pytest.raises(OSError):
        store.save_result(task['id'], picture())
    assert store.get(task['id'])['status'] == 'queued'
    assert not list(store.results.iterdir()) and not list(store.previews.iterdir())
