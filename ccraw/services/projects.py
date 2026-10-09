"""Versioned project and preset persistence, with legacy read compatibility."""

import os
from pathlib import Path
from ..branding import PROJECT_IDENTITIES, PRESET_IDENTITIES
from ..domain.recipes import validate, validate_snapshots, extract_look, apply_look, recipe
from ..persistence import atomic_write_json, read_json_object


def save_project(path, source, edits, snapshots=None):
    path, source = Path(path), Path(source).resolve()
    try:
        relative = os.path.relpath(source, path.parent.resolve())
    except ValueError:
        relative = str(source)
    atomic_write_json(
        path,
        dict(
            application='CCRaw',
            source=relative,
            edits=validate(edits),
            snapshots=validate_snapshots(snapshots or []),
        ),
    )


def load_project(path, include_snapshots=False):
    path = Path(path)
    data = read_json_object(path)
    if data.get('application') not in PROJECT_IDENTITIES:
        raise ValueError('这不是支持的 CCRaw 工程。')
    if not isinstance(data.get('source'), str) or not isinstance(data.get('edits'), dict):
        raise ValueError('工程缺少原片路径或编辑配方。')
    result = (str((path.parent / data['source']).resolve()), validate(data['edits']))
    return (*result, validate_snapshots(data.get('snapshots', []))) if include_snapshots else result


def save_preset(path, name, edits):
    atomic_write_json(
        path,
        dict(application='CCRaw preset', version=1, name=str(name)[:80], look=extract_look(edits)),
        max_bytes=1024 * 1024,
    )


def load_preset(path):
    data = read_json_object(path, max_bytes=1024 * 1024)
    if data.get('application') not in PRESET_IDENTITIES or data.get('version') != 1:
        raise ValueError('请选择 CCRaw 或兼容旧版的预设文件。')
    if not isinstance(data.get('look'), dict):
        raise ValueError('预设缺少编辑配方。')
    clean = apply_look(recipe(), data['look'])
    return dict(
        name=str(data.get('name', ''))[:80], look=extract_look(clean), subtitle='自定义风格'
    )
