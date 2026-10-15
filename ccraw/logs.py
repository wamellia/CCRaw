"""Rotating diagnostic log for the desktop application.

Windowed builds have no console, so GPU fallbacks, job failures and state
transitions are written to %LOCALAPPDATA%\\CCRaw\\logs\\ccraw.log.

Set CCRAW_LOG_LEVEL=DEBUG to include scheduler transitions.
"""

import faulthandler
import logging
import logging.handlers
import os
import re
import sys

_crash_stream = None


def redact(text, secrets=()):
    """Remove credentials and private paths from text before support sharing."""
    text = str(text)
    for value in secrets:
        if value:
            text = text.replace(str(value), '[redacted]')
    text = re.sub(r'(?i)(https?://)[^/\s:@]+:[^/\s@]+@', r'\1[redacted]@', text)
    text = re.sub(r'(?i)(bearer\s+)[^\s\"\',;]+', r'\1[redacted]', text)
    text = re.sub(
        r'(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*)[^&\s\"\',;]+',
        r'\1[redacted]',
        text,
    )
    text = re.sub(r'(?i)\b[A-Z]:[\\/][^\r\n\"\'<>|]*', '[private-path]', text)
    text = re.sub(r'(?<![:\w])/(?:Users|home|tmp|private|var)/[^\s\"\'<>]+', '[private-path]', text)
    return text


class PrivacyFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def folder():
    from .host import log_folder

    return log_folder()


def configure(level=None, filename='ccraw.log'):
    """Rotating log plus faulthandler output; the AI worker process uses its own files."""
    global _crash_stream
    root = logging.getLogger()
    if any(getattr(h, '_ccraw', False) for h in root.handlers):
        return
    level = level or os.environ.get('CCRAW_LOG_LEVEL', 'INFO').upper()
    try:
        target = folder()
        target.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            target / filename, maxBytes=2 * 2**20, backupCount=3, encoding='utf-8'
        )
        # Native crashes (e.g. a GPU driver fault) are appended by faulthandler.
        crash = (
            'crash.log' if filename == 'ccraw.log' else 'crash-' + filename.replace('ccraw-', '')
        )
        crash_path = target / crash
        if crash_path.exists() and crash_path.stat().st_size > 2 * 2**20:
            crash_path.replace(crash_path.with_suffix('.previous.log'))
        _crash_stream = open(crash_path, 'a', encoding='utf-8', buffering=1)
        faulthandler.enable(_crash_stream)
    except OSError:
        handler = logging.StreamHandler(sys.stderr)
    handler._ccraw = True
    handler.setFormatter(
        PrivacyFormatter('%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s')
    )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))
    from . import __version__

    logging.getLogger('ccraw').info(
        'CCRaw %s %s · Python %s · pid %d',
        __version__,
        'starting' if filename == 'ccraw.log' else 'AI worker',
        sys.version.split()[0],
        os.getpid(),
    )


def describe_system():
    """One log line per GPU with its driver, plus the ONNX Runtime build."""
    log = logging.getLogger('ccraw')
    try:
        from . import compute, winml
        import onnxruntime as ort

        log.info(
            'Windows build %s · onnxruntime %s · providers %s',
            winml.windows_build(),
            ort.__version__,
            ', '.join(ort.get_available_providers()),
        )
        for index, name, dedicated, vendor, *driver in compute.dxgi_adapters():
            log.info(
                'GPU %d: %s · vendor 0x%04X · %d MiB · driver %s',
                index,
                name,
                vendor,
                dedicated // 2**20,
                driver[0] if driver else '?',
            )
    except Exception:
        log.warning('system description failed', exc_info=True)
