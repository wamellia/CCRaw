import threading

import pytest

from ccraw.photo_agent.plugins import photo_registry
from test_agent_controls import photo_project


def test_agent_catalog_tools_are_selected_bounded_and_undoable(tmp_path):
    project, ids = photo_project(tmp_path, 2)
    registry = photo_registry(project, ids[:1], threading.Event(), None)
    assert registry.tools['query_catalog'].handler(limit=20)['total'] == 2
    with pytest.raises(ValueError, match='选中'):
        registry.tools['set_photo_state'].handler(photo_ids=ids, rating=5)
    registry.tools['set_photo_state'].handler(photo_ids=ids[:1], rating=5)
    result = registry.tools['query_catalog'].handler(
        rules={'field': 'rating', 'op': 'eq', 'value': 5}
    )
    assert result['photos'][0]['id'] == ids[0]
    assert registry.tools['catalog_history'].handler(operation='undo')['changed']
    assert not registry.tools['query_catalog'].handler(
        rules={'field': 'rating', 'op': 'eq', 'value': 5}
    )['photos']


def test_agent_catalog_metadata_changes_only_project_and_dynamic_album(tmp_path):
    project, ids = photo_project(tmp_path)
    registry = photo_registry(project, ids, threading.Event(), None)
    from pathlib import Path

    original = Path(project.photo(ids[0])['path']).read_bytes()
    album = registry.tools['create_album'].handler(
        name='精选', kind='smart', rules={'field': 'rating', 'op': 'ge', 'value': 4}
    )
    registry.tools['set_photo_state'].handler(photo_ids=ids, rating=4)
    assert registry.tools['query_catalog'].handler(album=album['id'])['total'] == len(ids)
    registry.tools['edit_photo_metadata'].handler(photo_ids=ids, metadata={'title': 'Beach'})
    assert Path(project.photo(ids[0])['path']).read_bytes() == original
