"""Unified, scoped application preferences. Credentials retain the existing DPAPI stores."""

from __future__ import annotations

import copy
from contextlib import contextmanager
import json
from pathlib import Path
import re
import threading

from PySide6.QtCore import QObject, Signal

from . import host
from .persistence import atomic_write_json, read_json_object

VERSION = 1
DEFAULTS = {
    'version': VERSION,
    'general': {'confirm_remote': True, 'offer_recovery': True},
    'appearance': {'theme': 'light', 'language': 'zh'},
    # Existing preview/cache caps are preserved during the first migration.
    'performance': {
        'backend': 'auto',
        'render_memory_mib': 512,
        'thumbnail_disk_mib': 2048,
        'preview_limit': 1600,
    },
    'import': {
        'raw_preset': 'none',
        'other_preset': 'none',
        'camera_rules': [],
        'metadata': {'keywords': [], 'copyright': '', 'creator': ''},
    },
    'display': {'profile_path': '', 'automatic': True, 'intent': 'relative', 'bpc': True},
    'external': {'executable': '', 'arguments': ['{file}'], 'format': 'tiff16'},
    'recovery': {'autosave': True, 'delay_ms': 500, 'backups': 5},
    'project': {'proxy_policy': 'auto', 'album_sort': 'capture'},
}
PROJECT_SECTIONS = frozenset(('import', 'project'))
_SERVICES = {}


def _merge(base, patch):
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if key not in result:
            raise ValueError(f'Unknown setting: {key}')
        if isinstance(result[key], dict):
            if not isinstance(value, dict):
                raise ValueError(f'Settings section {key} must be an object')
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _validate(data):
    a, p = data['appearance'], data['performance']
    if a['theme'] not in ('light', 'dark', 'system') or a['language'] not in ('zh', 'en'):
        raise ValueError('Unsupported appearance or language')
    if p['backend'] not in ('auto', 'cpu'):
        raise ValueError('Backend must be auto or cpu')
    if p['render_memory_mib'] not in (0, 512, 1024, 2048, 4096):
        raise ValueError('Render memory budget must be automatic/512/1024/2048/4096 MiB')
    for key, low, high in (('thumbnail_disk_mib', 64, 65536), ('preview_limit', 0, 8192)):
        value = p[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f'Invalid performance setting {key}')
    imports = data['import']
    allowed = ('none', 'neutral', 'vivid', 'monochrome')
    if imports['raw_preset'] not in allowed or imports['other_preset'] not in allowed:
        raise ValueError('Unsupported import preset')
    rules = imports['camera_rules']
    if not isinstance(rules, list) or len(rules) > 64:
        raise ValueError('Camera rules must be a list of at most 64 entries')
    for rule in rules:
        if (
            not isinstance(rule, dict)
            or set(rule) != {'camera', 'preset'}
            or not isinstance(rule['camera'], str)
            or not 0 < len(rule['camera']) <= 200
            or rule['preset'] not in allowed
        ):
            raise ValueError('A camera rule needs an exact camera name and supported preset')
    metadata = imports['metadata']
    if (
        not isinstance(metadata['keywords'], list)
        or len(metadata['keywords']) > 100
        or not all(isinstance(k, str) and len(k) <= 100 for k in metadata['keywords'])
    ):
        raise ValueError('Metadata keywords exceed their bounds')
    for value in (
        metadata['copyright'],
        metadata['creator'],
        data['display']['profile_path'],
        data['external']['executable'],
    ):
        if not isinstance(value, str) or len(value) > 4096 or '\0' in value:
            raise ValueError('Invalid settings text/path')
    if data['display']['intent'] not in ('relative', 'perceptual', 'absolute', 'saturation'):
        raise ValueError('Unsupported display intent')
    args = data['external']['arguments']
    if (
        not isinstance(args, list)
        or len(args) > 32
        or not all(isinstance(s, str) and len(s) <= 4096 for s in args)
    ):
        raise ValueError('External arguments require a bounded string array')
    if data['external']['format'] not in ('tiff8', 'tiff16', 'psd8', 'psd16'):
        raise ValueError('Unsupported external interchange format')
    recovery = data['recovery']
    if not isinstance(recovery['delay_ms'], int) or not 100 <= recovery['delay_ms'] <= 10000:
        raise ValueError('Autosave delay must be 100–10000 ms')
    if not isinstance(recovery['backups'], int) or not 1 <= recovery['backups'] <= 20:
        raise ValueError('Recovery retention must be 1–20')
    for section, key in (
        ('general', 'confirm_remote'),
        ('display', 'automatic'),
        ('general', 'offer_recovery'),
        ('display', 'bpc'),
        ('recovery', 'autosave'),
    ):
        if not isinstance(data[section][key], bool):
            raise ValueError('Boolean setting required')
    if not data['general']['confirm_remote']:
        raise ValueError('External execution requires cost/upload confirmation')
    if data['project']['proxy_policy'] not in ('auto', 'original', 'proxy'):
        raise ValueError('Unsupported project proxy policy')
    if data['project']['album_sort'] not in ('capture', 'name', 'rating'):
        raise ValueError('Unsupported album ordering')
    json.dumps(data, allow_nan=False)
    return data


def redact_settings(value):
    if isinstance(value, dict):
        return {
            k: redact_settings(v)
            for k, v in value.items()
            if k.lower()
            not in ('key', 'api_key', 'token', 'password', 'secret', 'prompt', 'messages')
        }
    if isinstance(value, list):
        return [redact_settings(v) for v in value]
    return value


class SettingsService(QObject):
    changed = Signal(object)
    failed = Signal(str)

    def __init__(self, path=None, *, migrate=True):
        super().__init__()
        self.path = Path(path or host.data_folder() / 'settings.json')
        self._lock = threading.RLock()
        self.error = ''
        try:
            stored = read_json_object(self.path, max_bytes=2 << 20)
            if stored.get('version', 1) > VERSION:
                raise ValueError('Preferences were saved by a newer application')
            self._data = _validate(_merge(DEFAULTS, stored))
        except FileNotFoundError:
            self._data = copy.deepcopy(DEFAULTS)
            if migrate:
                from PySide6.QtCore import QSettings

                appearance = QSettings('CCRaw', 'CCRaw').value('appearance', 'light')
                if appearance in ('light', 'dark', 'system'):
                    self._data['appearance']['theme'] = appearance
        except (OSError, ValueError, TypeError) as exc:
            self._data = copy.deepcopy(DEFAULTS)
            self.error = str(exc)

    def read(self, project=None):
        with self._lock:
            result = copy.deepcopy(self._data)
        if project:
            if not isinstance(project, dict) or set(project) - PROJECT_SECTIONS:
                raise ValueError('Project preferences may override only import/project settings')
            result = _validate(_merge(result, project))
        return result

    def update(self, patch):
        with self._lock:
            candidate = _validate(_merge(self._data, patch))
            try:
                atomic_write_json(self.path, candidate, private=True, max_bytes=2 << 20)
            except (OSError, ValueError) as exc:
                self.error = str(exc)
                self.failed.emit(self.error)
                raise
            self._data, self.error = candidate, ''
        self.changed.emit(copy.deepcopy(candidate))
        return self.read()

    def reset(self, section):
        if section not in DEFAULTS or section == 'version':
            raise ValueError('Unknown settings page')
        return self.update({section: copy.deepcopy(DEFAULTS[section])})

    def share(self):
        from . import nl_edit, image_generation

        return redact_settings(
            dict(
                self.read(), ai=nl_edit.load_settings(), generation=image_generation.load_settings()
            )
        )

    def project_overrides(self, project):
        return project.preferences().get('ccraw.settings', {}) if project is not None else {}

    def update_project(self, project, patch):
        old = self.project_overrides(project)
        self.read(project=patch)  # scope/type validation without changing global defaults
        candidate = copy.deepcopy(old)
        for key, value in patch.items():
            candidate[key] = _merge(candidate.get(key, copy.deepcopy(DEFAULTS[key])), value)
        project.preference('ccraw.settings', candidate)
        self.changed.emit(self.read(project=candidate))
        return candidate


def get_service():
    path = (host.data_folder() / 'settings.json').resolve()
    if path not in _SERVICES:
        _SERVICES[path] = SettingsService(path)
    return _SERVICES[path]


class WorkspaceStore:
    """Bounded UI state, separate from preferences and photograph recovery recipes."""

    _locks = {}

    def __init__(self, path=None):
        self.path = Path(path or host.data_folder() / 'workspace.json').resolve()
        self.lock = self._locks.setdefault(str(self.path), threading.RLock())
        self.error = ''

    @staticmethod
    def validate(state):
        if not isinstance(state, dict) or set(state) - {
            'geometry',
            'tabs',
            'splitters',
            'sidebar',
            'view',
        }:
            raise ValueError('Unsupported workspace state')
        geometry = state.get('geometry')
        if geometry is not None and (
            not isinstance(geometry, list)
            or len(geometry) != 4
            or any(isinstance(n, bool) or not isinstance(n, int) for n in geometry)
            or any(abs(n) > 100000 for n in geometry[:2])
            or not all(128 <= n <= 16384 for n in geometry[2:])
        ):
            raise ValueError('Invalid workspace geometry')
        for section, limit in (('tabs', 64), ('splitters', 32), ('view', 32)):
            values = state.get(section, {})
            if (
                not isinstance(values, dict)
                or len(values) > limit
                or not all(isinstance(k, str) and len(k) <= 200 for k in values)
            ):
                raise ValueError('Invalid workspace controls')
        if any(
            isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 128
            for n in state.get('tabs', {}).values()
        ):
            raise ValueError('Invalid workspace tab')
        for sizes in state.get('splitters', {}).values():
            if (
                not isinstance(sizes, list)
                or not 1 <= len(sizes) <= 16
                or any(
                    isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 20000
                    for n in sizes
                )
            ):
                raise ValueError('Invalid workspace splitter')
        if 'sidebar' in state and not isinstance(state['sidebar'], bool):
            raise ValueError('Invalid workspace visibility')
        for value in state.get('view', {}).values():
            if (
                not isinstance(value, (str, bool, int, type(None)))
                or isinstance(value, str)
                and len(value) > 200
                or isinstance(value, int)
                and not -100000 <= value <= 100000
            ):
                raise ValueError('Workspace view values must be stable scalar IDs')
        return copy.deepcopy(state)

    def _read(self):
        try:
            value = read_json_object(self.path, max_bytes=128 << 10)
            if (
                value.get('version') != 1
                or not isinstance(value.get('windows'), dict)
                or len(value['windows']) > 64
            ):
                raise ValueError('Unsupported workspace file')
            return value
        except FileNotFoundError:
            return {'version': 1, 'windows': {}}

    def load(self, kind):
        with self.lock:
            try:
                return self.validate(self._read()['windows'].get(kind, {}))
            except (ValueError, OSError, TypeError) as exc:
                self.error = str(exc)
                return {}

    def save(self, kind, state):
        if not isinstance(kind, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', kind):
            raise ValueError('Invalid workspace window type')
        state = self.validate(state)
        with self.lock:
            data = self._read()
            data['windows'][kind] = state
            if len(data['windows']) > 64:
                raise ValueError('Workspace window count exceeds its limit')
            atomic_write_json(self.path, data, private=True, max_bytes=128 << 10)
            self.error = ''

    def discard(self, kind):
        with self.lock:
            try:
                data = self._read()
            except (OSError, ValueError):
                data = {'version': 1, 'windows': {}}
            data['windows'].pop(kind, None)
            atomic_write_json(self.path, data, private=True, max_bytes=128 << 10)


def import_preview(recipe, info, defaults, *, initialized=False):
    result = copy.deepcopy(recipe)
    preset = defaults['raw_preset' if info.get('raw') else 'other_preset']
    camera = info.get('camera_model')
    matched = False
    for rule in defaults['camera_rules']:
        if camera and camera == rule['camera']:
            preset, matched = rule['preset'], True
            break
    if not initialized:
        if preset == 'vivid':
            result['adjustments'].update(contrast=12.0, vibrance=18.0, saturation=4.0)
        elif preset == 'neutral':
            result['adjustments'].update(contrast=-8.0, saturation=-4.0)
        elif preset == 'monochrome':
            result['monochrome'] = True
    metadata = {} if initialized else copy.deepcopy(defaults['metadata'])
    return {
        'recipe': result,
        'metadata': metadata,
        'preset': preset,
        'camera_rule': matched,
        'applied': not initialized,
    }


def apply_import_defaults(recipe, info, defaults, *, initialized=False):
    return import_preview(recipe, info, defaults, initialized=initialized)['recipe']


class CacheManager:
    """Only thumbnail/preview/render caches, never source/proxy/model/active mappings."""

    CATEGORIES = ('thumbnails', 'previews', 'render')

    def __init__(self, root=None, *, busy=lambda: False):
        self.root = Path(root or host.cache_folder()).resolve()
        self.busy = busy
        self.protected = set()
        self.pending = False
        self._pending_budget = 0

    def protect(self, path):
        self.protected.add(Path(path).resolve())

    def release(self, path):
        self.protected.discard(Path(path).resolve())

    @contextmanager
    def lease(self, path):
        self.protect(path)
        try:
            yield
        finally:
            self.release(path)

    def _files(self):
        for name in self.CATEGORIES:
            directory = self.root / name
            if not directory.is_dir() or directory.is_symlink() or directory.is_junction():
                continue
            for item in directory.rglob('*'):
                if (
                    item.is_file()
                    and not item.is_symlink()
                    and not item.is_junction()
                    and item.resolve().is_relative_to(directory.resolve())
                    and not any(
                        parent.is_symlink() or parent.is_junction()
                        for parent in item.parents
                        if parent != self.root
                    )
                ):
                    yield item

    def usage(self):
        counts = {k: 0 for k in self.CATEGORIES}
        for path in self._files():
            try:
                counts[path.relative_to(self.root).parts[0]] += path.stat().st_size
            except OSError:
                pass
        return {'root': str(self.root), 'bytes': counts, 'protected': len(self.protected)}

    def clear(self, *, budget_bytes=0):
        if isinstance(budget_bytes, bool) or not isinstance(budget_bytes, int) or budget_bytes < 0:
            raise ValueError('Cache budget must be a nonnegative byte count')
        if self.busy():
            self.pending = True
            self._pending_budget = budget_bytes
            return {'deferred': True, 'deleted': 0, 'bytes': 0}
        files = sorted(self._files(), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        deleted = removed = 0
        for path in files:
            if total <= budget_bytes:
                break
            if path.resolve() in self.protected:
                continue
            try:
                size = path.stat().st_size
                path.unlink()
                total -= size
                removed += size
                deleted += 1
            except OSError:
                continue
        self.pending = False
        return {'deferred': False, 'deleted': deleted, 'bytes': removed}

    def drain(self):
        return (
            self.clear(budget_bytes=self._pending_budget)
            if self.pending and not self.busy()
            else None
        )
