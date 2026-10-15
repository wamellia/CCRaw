"""Revision-aware coalesced background saves and recipe-only recovery sessions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import closing
import copy
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote
import uuid

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from . import host
from .persistence import atomic_write_json, read_json_object


class RecoveryStore:
    def __init__(self, root=None):
        self.root = Path(root or host.data_folder() / 'recovery').resolve()

    def _path(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', identifier):
            raise ValueError('Invalid recovery session identifier')
        path = self.root / (identifier + '.json')
        if path.is_symlink() or (self.root.exists() and self.root.is_junction()):
            raise ValueError('Recovery storage must not be a link')
        return path

    def save(self, identifier, state):
        if not isinstance(state, dict):
            raise ValueError('Recovery state must be an object')
        atomic_write_json(
            self._path(identifier),
            {'version': 1, 'saved_at': time.time(), 'data': copy.deepcopy(state)},
            private=True,
            max_bytes=16 << 20,
        )

    def load(self, identifier):
        value = read_json_object(self._path(identifier), max_bytes=16 << 20)
        if value.get('version') != 1 or not isinstance(value.get('data'), dict):
            raise ValueError('Unsupported recovery session')
        return value['data']

    def discard(self, identifier):
        self._path(identifier).unlink(missing_ok=True)

    def sessions(self):
        if not self.root.exists():
            return []
        result = []
        for path in sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[
            :50
        ]:
            try:
                value = read_json_object(path, max_bytes=16 << 20)
                if value.get('version') == 1 and isinstance(value.get('data'), dict):
                    result.append(
                        {'id': path.stem, 'saved_at': value['saved_at'], 'state': value['data']}
                    )
            except (OSError, ValueError, KeyError):
                continue
        return result


class AutosaveController(QObject):
    changed = Signal(str, str)
    saved = Signal(object)
    _completed = Signal(object)

    def __init__(self, snapshot, write, parent=None, *, debounce_ms=500):
        super().__init__(parent)
        self.snapshot, self.write = snapshot, write
        self.revision = self.saved_revision = 0
        self.status, self.error = 'saved', ''
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ccraw-autosave')
        self._future, self._payload = None, None
        self._finished_revision = -1
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(debounce_ms)
        self.timer.timeout.connect(self._start)
        self._completed.connect(self._finish)
        self._shutdown = False

    def mark_dirty(self):
        self.revision += 1
        self.status, self.error = 'pending', ''
        self.changed.emit(self.status, '')
        self.timer.start()

    def _start(self):
        if self._future is not None and not self._future.done():
            return
        if self.saved_revision >= self.revision:
            return
        try:
            payload = copy.deepcopy(self.snapshot())
        except Exception as exc:
            self.status, self.error = 'failed', str(exc)
            self.changed.emit(self.status, self.error)
            return
        revision = self.revision
        self._payload = (revision, payload)
        self.status, self.error = 'saving', ''
        self.changed.emit(self.status, '')

        def work():
            try:
                self.write(payload)
                return revision, payload, ''
            except Exception as exc:
                return revision, payload, str(exc) or type(exc).__name__

        self._future = self._executor.submit(work)

        def completed(future):
            if self._shutdown:
                return
            try:
                self._completed.emit(future.result())
            except RuntimeError:
                # A finished worker may outlive its deleted Qt window briefly.
                pass

        self._future.add_done_callback(completed)

    @Slot(object)
    def _finish(self, result):
        revision, payload, error = result
        if revision <= self._finished_revision:
            return
        self._finished_revision = revision
        if error:
            self.status, self.error = 'failed', error
        else:
            self.saved_revision = max(self.saved_revision, revision)
            self.status, self.error = (
                ('saved' if self.saved_revision == self.revision else 'pending'),
                '',
            )
            self.saved.emit(payload)
            if self.saved_revision < self.revision:
                self.timer.start()
        self.changed.emit(self.status, self.error)

    def flush(self):
        """Finish the latest revision before a close/project switch. Errors retain dirty state."""
        self.timer.stop()
        while self.saved_revision < self.revision:
            if self._future is None or self._future.done():
                if self._future is not None:
                    self._finish(self._future.result())
                if self.status == 'failed':
                    # A user's explicit Save/Close retries a prior disk failure.
                    self._finished_revision = self.revision - 1
                self._start()
            if self._future is None:
                return False
            try:
                self._finish(self._future.result(timeout=15))
            except TimeoutError:
                self.status, self.error = 'failed', 'Saving did not finish within 15 seconds'
                self.changed.emit(self.status, self.error)
                return False
            if self.status == 'failed':
                return False
        return True

    def close(self):
        okay = self.flush()
        if okay:
            self.shutdown()
        return okay

    def shutdown(self):
        self._shutdown = True
        self._executor.shutdown(wait=False)


def backup_database(source, directory, *, keep=5):
    """SQLite backup API copies a consistent WAL snapshot; retention touches own filenames."""
    source, directory = Path(source).resolve(), Path(directory).resolve()
    if not source.is_file() or source.is_symlink() or not 1 <= keep <= 20:
        raise ValueError('Invalid database backup source or retention')
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or directory.is_junction():
        raise ValueError('Backup folder must not be a link')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    destination = directory / f'project-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3'
    temporary = destination.with_suffix('.tmp')
    try:
        uri = 'file:' + quote(source.as_posix(), safe='/:') + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=15)) as src:
            with closing(sqlite3.connect(temporary)) as dst:
                src.backup(dst, pages=128)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise OSError('Database backup failed its integrity check')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    backups = sorted(
        directory.glob('project-*.sqlite3'), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for old in backups[keep:]:
        if not old.is_symlink():
            old.unlink(missing_ok=True)
    return destination
