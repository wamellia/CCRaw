"""Catalog actions preserve original bytes and commit reversible management facts."""

import hashlib
import importlib
import json
import threading

import numpy as np
import pytest
from PIL import Image

from ccraw.photo_agent.store import Project


@pytest.fixture
def data(tmp_path):
    project = Project.create(tmp_path / 'catalog.ccrawagent', '管理')
    ids = []
    for i in range(4):
        path = tmp_path / f'{i}.png'
        Image.new('RGB', (120, 80), (30 + i * 30, 60, 90)).save(path)
        ids += project.import_paths([path])
    return project, ids


def repository(project):
    try:
        return importlib.import_module('ccraw.catalog.repository').Catalog(project)
    except ModuleNotFoundError:
        pytest.fail('catalog repository must be implemented')


def test_bulk_state_is_atomic_undoable_and_bytes_safe(data):
    p, ids = data
    c = repository(p)
    before = [open(p.photo(i)['path'], 'rb').read() for i in ids]
    c.set_state(ids[:2], rating=4, flag='pick', color_label='red')
    assert c.state(ids[0])['rating'] == 4
    with pytest.raises(ValueError):
        c.set_state([ids[0], 'bad'], rating=1)
    assert c.state(ids[0])['rating'] == 4
    assert c.undo()
    assert c.state(ids[0])['rating'] == 0
    assert c.redo()
    assert c.state(ids[1])['flag'] == 'pick'
    assert before == [open(p.photo(i)['path'], 'rb').read() for i in ids]


def test_albums_smart_rules_remain_dynamic_and_parent_cycles_fail(data):
    p, ids = data
    c = repository(p)
    folder = c.create_album('旅行', kind='folder')
    ordinary = c.create_album('候选', parent=folder)
    c.add_members(ordinary, ids[:2])
    smart = c.create_album(
        '精选', kind='smart', rules={'all': [{'field': 'rating', 'op': 'ge', 'value': 4}]}
    )
    assert not c.query(album=smart)['photos']
    c.set_state([ids[1]], rating=5)
    assert [r['id'] for r in c.query(album=smart)['photos']] == [ids[1]]
    assert [r['id'] for r in c.query(album=ordinary)['photos']] == ids[:2]
    with pytest.raises(ValueError):
        c.update_album(folder, parent=ordinary)
    with pytest.raises(ValueError):
        c.update_album(smart, rules={'all': [{'field': 'album', 'op': 'eq', 'value': smart}]})


def test_rule_bounds_unknown_fields_and_sql_injection_rejected(data):
    p, ids = data
    c = repository(p)
    with pytest.raises(ValueError):
        c.query(rules={'all': [{'field': 'sql', 'op': 'eq', 'value': 'DROP TABLE photos'}]})
    value = {'field': 'rating', 'op': 'eq', 'value': 1}
    for _ in range(10):
        value = {'all': [value]}
    with pytest.raises(ValueError):
        c.query(rules=value)
    assert c.query(rules={'field': 'name', 'op': 'contains', 'value': "' OR 1=1 --"})['total'] == 0
    assert p.summary()['photos'] == len(ids)


def test_sort_filter_preset_stable_pagination(data):
    p, ids = data
    c = repository(p)
    c.set_state(ids[1:3], rating=5)
    first = c.query(sort='rating', descending=True, limit=1)
    second = c.query(sort='rating', descending=True, limit=1, offset=1)
    assert first['photos'][0]['id'] != second['photos'][0]['id']
    preset = c.save_filter('五星', {'field': 'rating', 'op': 'eq', 'value': 5}, sort='name')
    assert c.query(preset=preset)['total'] == 2
    c.set_state([ids[3]], rating=5)
    assert c.query(preset=preset)['total'] == 3


def test_keywords_append_remove_replace_and_metadata_mixed_values(data):
    p, ids = data
    c = repository(p)
    c.edit_metadata([ids[0]], {'title': 'A', 'copyright': '作者'})
    c.edit_metadata([ids[1]], {'title': 'B'})
    assert c.metadata_values(ids[:2])['title']['mixed']
    c.edit_keywords([ids[0]], ['旅行/海边'], mode='replace')
    c.edit_keywords(ids[:2], ['候选'], mode='append')
    assert c.metadata(ids[0])['keywords'] == ['候选', '旅行/海边']
    assert c.metadata(ids[1])['keywords'] == ['候选']
    c.edit_keywords(ids[:2], ['候选'], mode='remove')
    assert c.metadata(ids[1])['keywords'] == []
    c.edit_metadata(ids[:2], {'description': '共同说明'})
    assert c.metadata(ids[0])['title'] == 'A'
    assert c.metadata(ids[1])['title'] == 'B'


def test_stacks_copies_and_independent_history(data):
    from ccraw import model

    p, ids = data
    c = repository(p)
    stack = c.create_stack(ids[:2])
    c.update_stack(stack, cover=ids[1], collapsed=True)
    assert c.query()['total'] == 3
    c.update_stack(stack, collapsed=False)
    assert c.query()['total'] == 4
    copy = c.create_virtual_copy(ids[0], '另一种调色')
    assert p.photo(copy)['source_photo_id'] == ids[0]
    assert p.photo(copy)['path'] == p.photo(ids[0])['path']
    recipe = model.recipe()
    recipe['adjustments']['exposure'] = 1
    p.preference('recipe:' + copy, recipe)
    assert c.copy_history(copy)[-1]['recipe']['adjustments']['exposure'] == 1
    assert 'recipe:' + ids[0] not in p.preferences()
    assert p.proposal(copy, 'local', {'label': '副本方案'})


def test_soft_trash_restore_preserves_album_stack_copy(data):
    p, ids = data
    c = repository(p)
    album = c.create_album('保留')
    c.add_members(album, [ids[0]])
    c.set_state([ids[0]], removed=True)
    assert c.query()['total'] == 3
    assert c.query(trash=True)['total'] == 1
    assert c.query(album=album)['total'] == 0
    c.set_state([ids[0]], removed=False)
    assert c.query(album=album)['total'] == 1
    assert all(open(p.photo(i)['path'], 'rb').read() for i in ids)


def test_suggestion_decisions_and_cluster_overrides_survive_rescan(data):
    p, ids = data
    c = repository(p)
    group = {'photos': ids[:2], 'confidence': 'candidate'}
    c.decide_suggestion('similar', group, 'keep')
    assert c.suggestion_decision('similar', group)['decision'] == 'keep'
    groups = [
        {'id': 'person-a', 'photos': [ids[0]], 'faces': ['a:0']},
        {'id': 'person-b', 'photos': [ids[1]], 'faces': ['b:0']},
    ]
    c.correct_cluster('merge', ['person-a', 'person-b'])
    result = c.apply_cluster_corrections(groups)
    assert len(result) == 1 and set(result[0]['photos']) == set(ids[:2])
    assert len(c.apply_cluster_corrections(groups)) == 1
    c.undo()
    assert len(c.apply_cluster_corrections(groups)) == 2


def test_hash_relink_preserves_id_rejects_false_candidate(data, tmp_path):
    p, ids = data
    c = repository(p)
    old = p.photo(ids[0])
    content = open(old['path'], 'rb').read()
    sha = hashlib.sha256(content).hexdigest()
    p.update_photo(ids[0], facts={}, sha=sha)
    replacement = tmp_path / 'renamed.png'
    replacement.write_bytes(content)
    c.relink(ids[0], replacement)
    assert p.photo(ids[0])['path'] == str(replacement.resolve())
    with pytest.raises(ValueError, match='hash|指纹'):
        c.relink(ids[0], p.photo(ids[1])['path'])


def test_proxy_is_16bit_and_offline_source_fingerprint_checked(data):
    p, ids = data
    c = repository(p)
    proxy = c.create_proxy(ids[0], max_edge=100)
    import tifffile

    pixels = tifffile.imread(proxy['path'])
    assert pixels.dtype == np.uint16 and max(pixels.shape[:2]) == 100
    assert proxy['width'] == 120 and proxy['height'] == 80
    assert c.proxy_status(ids[0]) == 'ready'
    source = p.photo(ids[0])['path']
    import pathlib

    pathlib.Path(source).unlink()
    assert c.proxy_status(ids[0]) == 'offline'
    assert c.edit_source(ids[0], allow_proxy=True)['is_proxy']
    with pytest.raises(ValueError):
        c.edit_source(ids[0], allow_proxy=False)


def test_backup_uses_sqlite_and_reopens_committed_catalog(data, tmp_path):
    p, ids = data
    c = repository(p)
    c.set_state([ids[0]], rating=5)
    backup = tmp_path / 'backup.sqlite3'
    c.backup(backup)
    import sqlite3

    with sqlite3.connect(backup) as db:
        assert (
            db.execute('SELECT rating FROM photo_state WHERE photo_ref=?', (ids[0],)).fetchone()[0]
            == 5
        )


def test_suggestions_cover_beyond_old_limits_and_cancel(data):
    from ccraw.photo_agent.analysis import suggestions

    p, ids = data
    with p.connect() as db:
        db.execute('DELETE FROM photos')
        facts = json.dumps(
            {'phash': '0000000000000000', 'width': 10, 'height': 10, 'mean_rgb': [0, 0, 0]}
        )
        db.executemany(
            'INSERT INTO photos(id,path,status,facts) VALUES(?,?,?,?)',
            [(f'{i:032x}', f'C:/synthetic/{i}.png', 'ready', facts) for i in range(10001)],
        )
    report = suggestions(p)
    assert report['coverage']['analyzed'] == 10001
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        suggestions(p, cancel=cancel)


def test_sql_query_handles_100k_without_full_photo_materialization(data):
    p, ids = data
    c = repository(p)
    with p.connect() as db:
        db.execute('DELETE FROM photos')
        db.executemany(
            'INSERT INTO photos(id,path,size) VALUES(?,?,?)',
            [(f'{i:032x}', f'C:/generated/{i:06d}.png', i) for i in range(100000)],
        )
    result = c.query(sort='size', descending=True, limit=200, offset=200)
    assert result['total'] == 100000 and len(result['photos']) == 200
    assert result['photos'][0]['size'] == 99799


def test_batch_directory_relink_is_atomic_and_hash_validated(data, tmp_path):
    from pathlib import Path

    p, ids = data
    c = repository(p)
    folder = tmp_path / 'moved'
    folder.mkdir()
    for ref in ids:
        photo = p.photo(ref)
        content = Path(photo['path']).read_bytes()
        p.update_photo(ref, facts={}, sha=hashlib.sha256(content).hexdigest())
        (folder / Path(photo['path']).name).write_bytes(content)
    (folder / '2.png').write_bytes(b'wrong')
    original = [p.photo(ref)['path'] for ref in ids]
    with pytest.raises(ValueError):
        c.relink_prefix(tmp_path, folder)
    assert [p.photo(ref)['path'] for ref in ids] == original
    (folder / '2.png').write_bytes(open(original[2], 'rb').read())
    assert c.relink_prefix(tmp_path, folder) == 4
    assert all(Path(p.photo(ref)['path']).parent == folder for ref in ids)
    c.undo()
    assert [p.photo(ref)['path'] for ref in ids] == original


def test_proxy_stale_cleanup_and_explicit_low_resolution_export(data, tmp_path):
    from pathlib import Path

    p, ids = data
    c = repository(p)
    proxy = c.create_proxy(ids[0], max_edge=100)
    source = Path(p.photo(ids[0])['path'])
    Image.new('RGB', (120, 80), 'green').save(source)
    assert c.proxy_status(ids[0]) == 'stale'
    with pytest.raises(ValueError):
        c.export_proxy(ids[0], tmp_path / 'low.png')
    c.create_proxy(ids[0], max_edge=100)
    source.unlink()
    output = tmp_path / 'low.png'
    c.export_proxy(ids[0], output)
    with Image.open(output) as image:
        assert max(image.size) == 100
    c.remove_proxy(ids[0])
    assert c.proxy_status(ids[0]) == 'missing'
    c.undo()
    assert c.proxy_status(ids[0]) == 'offline'


def test_faces_over_3000_are_processed_and_scan_preserves_existing_features(data):
    from ccraw.photo_agent.analysis import cluster_faces

    faces = [
        {'id': f'{i:06d}:0', 'photo_id': f'{i:06d}', 'vector': [1.0, 0.0, 0.0]} for i in range(3001)
    ]
    groups = cluster_faces(faces)
    assert len(groups) == 1 and len(groups[0]['faces']) == 3001


def test_catalog_filters_are_shared_with_agent_search(data):
    from ccraw.photo_agent.search import search

    p, ids = data
    c = repository(p)
    for ref in ids:
        p.update_photo(ref, facts={'flags': []})
    c.set_state([ids[2]], rating=5, flag='pick')
    result = search(p, {'rating': 5, 'flag': 'pick'})
    assert [r['id'] for r in result['photos']] == [ids[2]]


def test_missing_models_do_not_erase_prior_features(data, monkeypatch):
    from ccraw.photo_agent.analysis import index_project
    from ccraw.photo_agent.models import LocalModels

    p, ids = data
    monkeypatch.setenv('CCRAW_AGENT_MODEL_DIR', str(p.root / 'no-models'))
    p.update_photo(
        ids[0],
        facts={'faces': [{'vector': [1, 0, 0]}], 'vector': [1, 0], 'scenes': [{'tag': '海边'}]},
    )
    models = LocalModels()
    assert not models.clip_ready and not models.face_ready
    index_project(p, models=models, force=True)
    facts = p.photo(ids[0])['facts']
    assert facts['faces'] == [{'vector': [1, 0, 0]}]
    assert facts['vector'] == [1, 0]
    assert facts['scenes'] == [{'tag': '海边'}]


def test_format_filter_uses_filename_suffix_even_in_dotted_directory(data):
    p, ids = data
    c = repository(p)
    with p.connect() as db:
        db.execute('UPDATE photos SET path=? WHERE id=?', ('C:/Folder.with.dots/photo.JPG', ids[0]))
    assert [
        r['id'] for r in c.query(rules={'field': 'format', 'op': 'eq', 'value': 'jpg'})['photos']
    ] == [ids[0]]


def test_undo_relink_keeps_later_analysis_facts(data, tmp_path):
    from pathlib import Path

    p, ids = data
    c = repository(p)
    photo = p.photo(ids[0])
    content = Path(photo['path']).read_bytes()
    p.update_photo(ids[0], facts={'old': True}, sha=hashlib.sha256(content).hexdigest())
    replacement = tmp_path / 'replacement.png'
    replacement.write_bytes(content)
    c.relink(ids[0], replacement)
    p.update_photo(ids[0], facts={'new': True}, sha=hashlib.sha256(content).hexdigest())
    c.undo()
    assert p.photo(ids[0])['facts'] == {'new': True}


def test_album_reorder_and_backup_purge_keep_originals(data, tmp_path):
    p, ids = data
    c = repository(p)
    album = c.create_album('自定义')
    c.add_members(album, ids)
    c.reorder_album(album, list(reversed(ids)))
    assert [r['id'] for r in c.query(album=album)['photos']] == list(reversed(ids))
    paths = [p.photo(ref)['path'] for ref in ids]
    c.set_state([ids[0]], removed=True)
    backup = tmp_path / 'before-purge.sqlite3'
    assert c.purge_removed([ids[0]], backup) == 1
    assert backup.is_file()
    with pytest.raises(ValueError):
        p.photo(ids[0])
    assert all(open(path, 'rb').read() for path in paths)


def test_auto_stacks_exclude_existing_and_removed_and_undo_as_one_command(data):
    p, ids = data
    c = repository(p)
    for i, ref in enumerate(ids):
        p.update_photo(ref, facts={'captured_at': f'2026-10-12T12:00:0{i}'})
    c.set_state([ids[-1]], removed=True)
    stacks = c.auto_stack(seconds=3)
    assert len(stacks) == 1 and len(c.stacks()[0]['photos']) == len(ids) - 1
    assert c.auto_stack(seconds=3) == []
    assert c.undo() and c.stacks() == []


def test_proxy_relocation_and_cleanup_preserve_live_and_undo_references(data):
    from pathlib import Path

    p, ids = data
    c = repository(p)
    original = c.create_proxy(ids[0], max_edge=100)
    relocated = c.relocate_proxy(ids[0])
    assert Path(relocated['path']).is_file() and relocated['path'] != original['path']
    assert c.cleanup_proxies() == []
    assert c.undo() and c.proxy(ids[0])['path'] == original['path']
    orphan = p.directory('proxies') / ('f' * 32 + '.tiff')
    orphan.write_bytes(b'orphan cache')
    assert c.cleanup_proxies() == [str(orphan)]
    assert Path(original['path']).is_file() and Path(relocated['path']).is_file()


def test_album_cover_must_be_member_and_initial_rating_undo_keeps_later_edit(data):
    from ccraw import model

    p, ids = data
    c = repository(p)
    album = c.create_album('封面')
    c.add_members(album, [ids[0]])
    with pytest.raises(ValueError):
        c.update_album(album, cover=ids[1])
    c.set_state([ids[0]], rating=4)
    p.preference('recipe:' + ids[0], model.recipe())
    assert c.undo()
    assert c.state(ids[0])['rating'] == 0 and c.state(ids[0])['edited_at'] > 0


def test_cluster_identity_survives_new_faces_and_face_split_preserves_membership(data):
    p, ids = data
    c = repository(p)
    groups = c.stabilize_clusters(
        [{'id': 'person-old', 'photos': [ids[0]], 'faces': [ids[0] + ':0']}]
    )
    rescanned = c.stabilize_clusters(
        [{'id': 'person-new', 'photos': ids[:2], 'faces': [ids[1] + ':0', ids[0] + ':0']}]
    )
    assert rescanned[0]['id'] == groups[0]['id']
    c.correct_cluster('split', [groups[0]['id']], faces=[ids[1] + ':0'])
    corrected = c.apply_cluster_corrections(rescanned)
    assert len(corrected) == 2
    assert corrected[0]['photos'] == [ids[0]]
    assert corrected[1]['photos'] == [ids[1]] and corrected[1]['faces'] == [ids[1] + ':0']


def test_legacy_annotations_can_be_cleared_without_resurrecting_search_tags(data):
    from ccraw.photo_agent.search import search

    p, ids = data
    c = repository(p)
    p.update_photo(ids[0], facts={'flags': [], 'person_count': 3})
    p.preference('annotation:' + ids[0], {'person_count': 2, 'tags': ['海边']})
    assert c.metadata(ids[0])['keywords'] == ['海边']
    c.edit_keywords([ids[0]], ['海边'], mode='remove')
    c.edit_metadata([ids[0]], {'person_count': None})
    assert c.metadata(ids[0])['keywords'] == []
    assert search(p, {'tags': ['海边']})['photos'] == []
    assert search(p, {'person_count': 3, 'strict_people': True})['photos'] == []


def test_burst_candidate_uses_sharpness_and_exposure_and_keeps_decision(data):
    from ccraw.photo_agent.analysis import suggestions

    p, ids = data
    c = repository(p)
    for i, ref in enumerate(ids[:3]):
        p.update_photo(
            ref,
            sha=str(i) * 64,
            facts={
                'captured_at': f'2026-10-12T12:00:0{i}',
                'phash': '0000000000000000',
                'width': 120,
                'height': 80,
                'mean_rgb': [60, 80, 100],
                'quality': {
                    'sharpness': 100 if i == 0 else 80,
                    'dark': 0,
                    'bright': 0.9 if i == 0 else 0,
                },
            },
        )
    report = suggestions(p)
    burst = next(g for g in report['similar'] if g.get('grouping') == 'burst')
    assert set(burst['photos']) == set(ids[:3])
    assert burst['best_candidate'] in ids[1:3]
    assert any('曝光' in reason for reason in burst['reasons'])
    c.decide_suggestion('similar', burst, 'keep')
    refreshed = suggestions(p)
    assert (
        next(g for g in refreshed['similar'] if g.get('grouping') == 'burst')['decision'] == 'keep'
    )
