"""Local recent-project history, separate from photo facts and project contents."""

import math
import os
from pathlib import Path
import time

from .. import host
from ..persistence import atomic_write_json, read_json_object


def history_path():
    return host.data_folder() / 'recent-agent-projects.json'


def recent_projects():
    try:
        data = read_json_object(history_path(), max_bytes=256 * 1024)
    except (OSError, ValueError):
        return []
    if data.get('version') != 1 or not isinstance(data.get('projects'), list):
        return []
    records = []
    for record in data['projects']:
        if not isinstance(record, dict):
            continue
        path, name, opened = (record.get(key) for key in ('path', 'name', 'opened'))
        if (
            not isinstance(path, str)
            or '\x00' in path
            or not Path(path).is_absolute()
            or Path(path).suffix.lower() != '.ccrawagent'
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(opened, (int, float))
            or isinstance(opened, bool)
            or not math.isfinite(opened)
            or not 0 <= opened <= 253402214400
        ):
            continue
        records.append(dict(path=path, name=name[:120], opened=opened))
    unique = {}
    for record in sorted(records, key=lambda value: value['opened'], reverse=True):
        unique.setdefault(os.path.normcase(record['path']), record)
    return list(unique.values())[:100]


def remember_project(project):
    path = str(project.path.resolve())
    records = [
        record
        for record in recent_projects()
        if os.path.normcase(record['path']) != os.path.normcase(path)
    ]
    records.insert(0, dict(path=path, name=project.manifest['name'], opened=time.time()))
    atomic_write_json(
        history_path(), dict(version=1, projects=records[:100]), private=True, max_bytes=256 * 1024
    )
