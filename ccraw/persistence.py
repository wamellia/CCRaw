"""Bounded JSON reads and durable, atomic local file replacement."""

import json
import os
from pathlib import Path
import tempfile


def atomic_write_json(path, data, *, private=False, max_bytes=32 * 1024 * 1024):
    path = Path(path)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if len(payload.encode('utf-8')) > max_bytes:
        raise ValueError('JSON document exceeds its size limit.')
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            if private and os.name != 'nt':
                os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json_object(path, max_bytes=32 * 1024 * 1024):
    with Path(path).open('rb') as stream:
        payload = stream.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError('JSON document exceeds its size limit.')
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError('Invalid JSON document.') from error
    if not isinstance(data, dict):
        raise ValueError('JSON document must be an object.')
    return data
