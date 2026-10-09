"""Template-driven image generation, bounded HTTP and local task persistence."""

from __future__ import annotations

import base64
import copy
from contextlib import contextmanager
import http.client
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import time
import urllib.parse
import uuid

from PIL import Image, ImageOps
from . import host, nl_edit
from .persistence import atomic_write_json, read_json_object

RESOURCES = Path(__file__).parent / 'resources'
MAX_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 36_000_000
ACTIVE = ('queued', 'preparing', 'running', 'saving')


class GenerationError(ValueError):
    pass


def data_root():
    return host.data_folder() / 'image-generation'


def default_settings():
    return dict(
        base_url='https://ark.cn-beijing.volces.com/api/v3',
        model='doubao-seedream-4-5-251128',
        key='',
        timeout=180,
        watermark=True,
    )


def load_settings(path=None):
    result = default_settings()
    try:
        data = read_json_object(path or data_root() / 'settings.json', max_bytes=1024 * 1024)
    except (OSError, ValueError):
        return result
    for field, limit in (('base_url', 500), ('model', 200)):
        if isinstance(data.get(field), str):
            result[field] = data[field][:limit]
    result['key'] = nl_edit.unprotect_key(str(data.get('key', '')))
    result['watermark'] = bool(data.get('watermark', True))
    try:
        result['timeout'] = min(900, max(10, int(data.get('timeout', 180))))
    except (TypeError, ValueError):
        pass
    return result


def save_settings(settings, path=None):
    data = copy.deepcopy(settings)
    data['key'] = nl_edit.protect_key(str(data.get('key', '')))
    atomic_write_json(path or data_root() / 'settings.json', data, private=True)


def valid_subject_choices(choices):
    return (
        isinstance(choices, list)
        and len(choices) <= 12
        and all(
            isinstance(choice, dict)
            and isinstance(choice.get('label'), str)
            and 0 < len(choice['label']) <= 80
            and isinstance(choice.get('prompt'), str)
            and 0 < len(choice['prompt']) <= 400
            for choice in choices
        )
    )


def load_templates(root=None, *, mode=None):
    builtins = read_json_object(RESOURCES / 'generation-templates.json')['templates']
    try:
        custom = read_json_object(
            Path(root or data_root()) / 'templates.json', max_bytes=2 * 1024 * 1024
        ).get('templates', [])
    except (OSError, ValueError):
        custom = []
    templates = builtins + [
        dict(t, mode=t.get('mode', 'creative'))
        for t in custom
        if isinstance(t, dict)
        and isinstance(t.get('id'), str)
        and t['id'].startswith('custom-')
        and isinstance(t.get('name'), str)
        and isinstance(t.get('prompt'), str)
        and isinstance(t.get('category', '自定义'), str)
        and isinstance(t.get('preview', ''), str)
        and len(t['name']) <= 80
        and len(t['prompt']) <= 12000
        and t.get('mode', 'creative') in ('creative', 'style')
        and isinstance(t.get('requires_reference', False), bool)
        and valid_subject_choices(t.get('subject_choices', []))
    ]
    return [t for t in templates if t.get('mode', 'creative') == mode] if mode else templates


def save_template(
    name,
    prompt,
    category='自定义',
    root=None,
    *,
    mode='creative',
    requires_reference=False,
    subject_choices=None,
):
    name, prompt = name.strip(), prompt.strip()
    if not name or len(name) > 80 or not prompt or len(prompt) > 12000:
        raise GenerationError('模板需要名称（最多 80 字）和提示词（最多 12000 字）。')
    if mode not in ('creative', 'style'):
        raise GenerationError('请选择创意模板或风格化生成。')
    choices = [] if subject_choices is None else subject_choices
    if not isinstance(requires_reference, bool) or not valid_subject_choices(choices):
        raise GenerationError('模板的参考图或主体选项无效。')
    root = Path(root or data_root())
    custom = [t for t in load_templates(root) if t['id'].startswith('custom-')]
    if len(custom) >= 100:
        raise GenerationError('最多保存 100 个自定义模板。')
    template = dict(
        id='custom-' + uuid.uuid4().hex,
        name=name,
        prompt=prompt,
        category=category,
        preview='',
        mode=mode,
        requires_reference=requires_reference,
    )
    if choices:
        template['subject_choices'] = copy.deepcopy(choices)
    atomic_write_json(
        root / 'templates.json',
        {'templates': custom + [template]},
        private=True,
        max_bytes=2 * 1024 * 1024,
    )
    return template


def delete_template(identifier, root=None):
    if not identifier.startswith('custom-'):
        raise GenerationError('内置模板不能删除。')
    root = Path(root or data_root())
    custom = [
        t for t in load_templates(root) if t['id'].startswith('custom-') and t['id'] != identifier
    ]
    atomic_write_json(
        root / 'templates.json', {'templates': custom}, private=True, max_bytes=2 * 1024 * 1024
    )


def prepare_reference(path, recipe=None, cancel=None, *, initialized=True):
    """Render an edited reference off the GUI thread; omit all EXIF and raw data."""
    from . import engine, model
    import numpy as np

    if cancel and cancel.is_set():
        raise InterruptedError()
    source, info = engine.load_image(path, preview_limit=2048)
    edits = copy.deepcopy(recipe) if recipe is not None else model.recipe()
    if recipe is None or not initialized:
        edits['develop'] = copy.deepcopy(info.get('develop', edits['develop']))
        edits['white_balance'] = copy.deepcopy(info.get('white_balance', edits['white_balance']))
    rgb = engine.process(source, edits)
    if cancel and cancel.is_set():
        raise InterruptedError()
    h, w = rgb.shape[:2]
    if min(h, w) < 15 or not 1 / 16 <= w / h <= 16:
        raise GenerationError('参考图宽高至少 15 像素，宽高比需要在 1:16 至 16:1 之间。')
    image = Image.fromarray(np.clip(rgb * 255 + 0.5, 0, 255).astype('uint8'))
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=94, subsampling=0)
    payload = buffer.getvalue()
    if len(payload) > 10 * 1024 * 1024:
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG', quality=85)
        payload = buffer.getvalue()
    if len(payload) > 10 * 1024 * 1024:
        raise GenerationError('参考图压缩后仍超过 10 MB。')
    return 'data:image/jpeg;base64,' + base64.b64encode(payload).decode('ascii')


def image_format(data):
    if not data or len(data) > MAX_BYTES:
        raise GenerationError('生成图片为空或超过 32 MB。')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format not in ('PNG', 'JPEG', 'WEBP')
                or image.width * image.height > MAX_PIXELS
            ):
                raise GenerationError('生成图片格式不支持或像素过大。')
            extension = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}[image.format]
            image.verify()
        # JPEG verify checks headers only. Decode pixels before committing a
        # successful paid result; truncated images must stop the remaining batch.
        with Image.open(io.BytesIO(data)) as image:
            image.load()
        return extension
    except (OSError, SyntaxError, Image.DecompressionBombError, ValueError) as error:
        raise GenerationError('生成服务没有返回有效图片。') from error


class GenerationClient:
    """One paid POST, no automatic retry. Cancellation shuts down its socket."""

    def __init__(self):
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.connection = self.sock = None

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            if self.connection is not None:
                self.connection.close()

    def check_cancel(self):
        if self.cancelled.is_set():
            raise InterruptedError('已取消生成。')

    def request(self, url, payload=None, key='', timeout=180):
        # Some Windows socket reads survive close(). Keep UI cancellation bounded
        # even in that case; the abandoned network call can never deliver a result.
        done = threading.Event()
        result = []

        def work():
            try:
                result.append((True, self._request(url, payload, key, timeout)))
            except Exception as error:
                result.append((False, error))
            finally:
                done.set()

        threading.Thread(target=work, daemon=True, name='ccraw-generation-http').start()
        while not done.wait(0.05):
            self.check_cancel()
        self.check_cancel()
        success, value = result[0]
        if not success:
            raise value
        return value

    def _request(self, url, payload=None, key='', timeout=180):
        self.check_cancel()
        try:
            nl_edit.validate_endpoint(url)
        except ValueError as error:
            raise GenerationError(str(error)) from None
        parsed = urllib.parse.urlsplit(url)
        connection_type = (
            http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        )
        connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
        try:
            with self.lock:
                self.connection = connection
            connection.connect()
            with self.lock:
                self.sock = connection.sock
            self.check_cancel()
            headers = {'User-Agent': 'CCRaw'}
            body = None
            if payload is not None:
                headers['Content-Type'] = 'application/json'
                body = json.dumps(payload).encode('utf-8')
            if key:
                headers['Authorization'] = 'Bearer ' + key
            target = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
            connection.request('POST' if payload is not None else 'GET', target, body, headers)
            response = connection.getresponse()
            chunks, total = [], 0
            while True:
                self.check_cancel()
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES * 2:
                    raise GenerationError('生成服务返回内容过大。')
                chunks.append(chunk)
            self.check_cancel()
            data = b''.join(chunks)
            if response.status != 200:
                from .logs import redact

                # Never follow redirects with credentials and never retry a paid request.
                detail = redact(data[:2000].decode('utf-8', 'replace'), [key] if key else [])
                raise GenerationError(f'生成服务返回 HTTP {response.status}：{detail}')
            return data
        except (OSError, http.client.HTTPException) as error:
            self.check_cancel()
            from .logs import redact

            raise GenerationError(
                '生成请求失败：' + redact(str(error), [key] if key else [])
            ) from None
        finally:
            connection.close()
            with self.lock:
                self.connection = self.sock = None

    def generate(self, settings, prompt, reference=None, size='2K'):
        prompt = prompt.strip()
        if not prompt or len(prompt) > 12000:
            raise GenerationError('请输入提示词（最多 12000 字）。')
        if size not in ('2K', '4K'):
            raise GenerationError('请选择 2K 或 4K 尺寸。')
        model = settings.get('model', '').strip()
        if not model:
            raise GenerationError('请在生成设置中填写模型名称或接入点 ID。')
        url = settings['base_url'].strip().rstrip('/')
        if not url.endswith('/images/generations'):
            url += '/images/generations'
        nl_edit.validate_endpoint(url)
        if not settings.get('key') and not nl_edit._is_local(url):
            raise GenerationError('请在生成设置中填写 API Key。')
        payload = dict(
            model=model,
            prompt=prompt,
            size=size,
            response_format='b64_json',
            watermark=bool(settings.get('watermark', True)),
        )
        if any(part in model for part in ('4-5', '4-0', '5-0-lite', '4.5', '4.0', '5.0-lite')):
            payload['sequential_image_generation'] = 'disabled'
        if reference:
            payload['image'] = reference
        body = self.request(url, payload, settings.get('key', ''), settings.get('timeout', 180))
        try:
            result = json.loads(body)
            entries = result.get('data', [])
            if (
                not isinstance(entries, list)
                or len(entries) != 1
                or not isinstance(entries[0], dict)
            ):
                raise ValueError('Expected exactly one output image')
            entry = entries[0]
            if entry.get('b64_json'):
                encoded = entry['b64_json']
                if encoded.startswith('data:'):
                    encoded = encoded.split(',', 1)[1]
                image = base64.b64decode(encoded, validate=True)
            elif entry.get('url'):
                # CDN requests intentionally carry no API authentication header.
                image = self.request(entry['url'], timeout=settings.get('timeout', 180))
            else:
                raise ValueError('No image in response')
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise GenerationError('生成服务返回格式不正确或没有生成图片。') from error
        self.check_cancel()
        image_format(image)
        return image


class GenerationStore:
    """Every operation uses its own connection, safe across GUI/worker threads."""

    def __init__(self, root=None):
        self.root = Path(root or data_root()).resolve()
        self.results = self.root / 'results'
        self.results.mkdir(parents=True, exist_ok=True)
        self.previews = self.root / 'previews'
        self.previews.mkdir(exist_ok=True)
        if os.name != 'nt':
            self.root.chmod(0o700)
        self.database = self.root / 'history.sqlite'
        self.lock = threading.RLock()
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL,
                reference TEXT NOT NULL, recipe TEXT, size TEXT NOT NULL,
                status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT '', created REAL NOT NULL)""")
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(tasks)')}
            if 'initialized' not in columns:
                connection.execute(
                    'ALTER TABLE tasks ADD COLUMN initialized INTEGER NOT NULL DEFAULT 1'
                )
            if 'mode' not in columns:
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN mode TEXT NOT NULL DEFAULT 'creative'"
                )

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def enqueue(
        self,
        title,
        prompt,
        *,
        reference='',
        recipe=None,
        size='2K',
        initialized=True,
        mode='creative',
    ):
        if mode not in ('creative', 'style', 'custom'):
            raise GenerationError('请选择生成模式。')
        identifier = uuid.uuid4().hex
        with self.lock, self.connect() as connection:
            connection.execute(
                'INSERT INTO tasks '
                '(id,title,prompt,reference,recipe,size,status,message,result,created,initialized,mode) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    identifier,
                    title[:80],
                    prompt[:12000],
                    str(reference),
                    json.dumps(recipe, ensure_ascii=False) if recipe is not None else None,
                    size,
                    'queued',
                    '',
                    '',
                    time.time(),
                    int(initialized),
                    mode,
                ),
            )
        return self.get(identifier)

    def get(self, identifier):
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM tasks WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise GenerationError('找不到生成任务。')
        task = dict(row)
        task['recipe'] = json.loads(task['recipe']) if task['recipe'] else None
        return task

    def history(self, limit=100):
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    'SELECT id,title,status,message,result,created,mode FROM tasks ORDER BY created DESC LIMIT ?',
                    (max(1, min(500, limit)),),
                )
            ]

    def update(self, identifier, status, message=''):
        if status not in (*ACTIVE, 'completed', 'failed', 'cancelled'):
            raise ValueError('Unknown generation status')
        with self.lock, self.connect() as connection:
            connection.execute(
                'UPDATE tasks SET status=?,message=? WHERE id=? '
                "AND status IN ('queued','preparing','running','saving')",
                (status, str(message)[:2000], identifier),
            )

    def recover(self):
        with self.lock, self.connect() as connection:
            connection.execute(
                "UPDATE tasks SET status='cancelled',message='上次会话已结束' "
                "WHERE status IN ('queued','preparing','running','saving')"
            )

    def result_path(self, task):
        name = task.get('result', '')
        path = (self.results / name).resolve()
        return path if name and path.parent == self.results and path.is_file() else None

    def save_result(self, identifier, data):
        extension = image_format(data)
        # Small display copies are prepared by the worker. History browsing never
        # needs to decode dozens of full-resolution outputs on the GUI thread.
        with Image.open(io.BytesIO(data)) as image:
            image.thumbnail((400, 400))
            image = ImageOps.exif_transpose(image).convert('RGB')
            buffer = io.BytesIO()
            image.save(buffer, 'JPEG', quality=90)
            preview_data = buffer.getvalue()
        with self.lock:
            task = self.get(identifier)
            if task['status'] == 'cancelled':
                raise InterruptedError('任务已取消。')
            if task['status'] not in ACTIVE:
                raise GenerationError('任务已结束。')
            path = self.results / (identifier + extension)
            descriptor, name = tempfile.mkstemp(prefix='.', suffix='.tmp', dir=self.results)
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, path)
                self.write_preview(identifier, preview_data)
                with self.connect() as connection:
                    connection.execute(
                        "UPDATE tasks SET status='completed',message='',result=? WHERE id=?",
                        (path.name, identifier),
                    )
            except Exception:
                path.unlink(missing_ok=True)
                (self.previews / (identifier + '.jpg')).unlink(missing_ok=True)
                raise
            finally:
                Path(name).unlink(missing_ok=True)
            return path

    def write_preview(self, identifier, data):
        descriptor, name = tempfile.mkstemp(prefix='.', suffix='.tmp', dir=self.previews)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
            os.replace(name, self.previews / (identifier + '.jpg'))
        finally:
            Path(name).unlink(missing_ok=True)

    def preview_path(self, task):
        path = (self.previews / (task['id'] + '.jpg')).resolve()
        return path if path.parent == self.previews and path.is_file() else None

    def delete(self, identifier):
        with self.lock:
            task = self.get(identifier)
            if task['status'] in ACTIVE:
                raise GenerationError('请先取消生成任务。')
            path = self.result_path(task)
            if path:
                path.unlink()
            preview = self.preview_path(task)
            if preview:
                preview.unlink()
            with self.connect() as connection:
                connection.execute('DELETE FROM tasks WHERE id=?', (identifier,))
