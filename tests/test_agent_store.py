import json
from pathlib import Path

import pytest

from ccraw.photo_agent.store import Project


def test_project_import_is_explicit_deduplicated_and_persistent(tmp_path):
    source = tmp_path / 'a.png'
    source.write_bytes(b'photo')
    project = Project.create(tmp_path / 'trip.ccrawagent', '旅行')
    ids = project.import_paths([source, source])
    assert len(ids) == 1
    loaded = Project.open(project.path)
    assert loaded.photo(ids[0])['path'] == str(source.resolve())
    assert loaded.summary()['photos'] == 1
    other = Project.create(tmp_path / 'other.ccrawagent', '其他')
    with pytest.raises(ValueError):
        other.photo(ids[0])


def test_project_manifest_cannot_redirect_database_outside_project(tmp_path):
    project = Project.create(tmp_path / 'trip.ccrawagent', '旅行')
    data = json.loads(project.path.read_text(encoding='utf8'))
    data['data_directory'] = '../outside'
    project.path.write_text(json.dumps(data), encoding='utf8')
    with pytest.raises(ValueError):
        Project.open(project.path)


def test_events_preferences_and_history_are_separate_and_replay_is_read_only(tmp_path):
    project = Project.create(tmp_path / 'trip.ccrawagent', '旅行')
    project.preference('language', 'zh')
    project.message('user', '找日落')
    project.event('tool.finished', {'tool': 'search', 'count': 2})
    events = project.events()
    assert events[-1]['kind'] == 'tool.finished'
    assert project.messages()[-1]['content'] == '找日落'
    assert project.preferences() == {'language': 'zh'}
    assert project.events() == events
    assert not list(project.root.glob('derived/*'))


def test_replacing_and_clearing_recommendations_preserves_executing_and_completed(tmp_path):
    source = tmp_path / 'photo.png'
    source.write_bytes(b'photo')
    project = Project.create(tmp_path / 'editing.ccrawagent', '项目')
    photo_id = project.import_paths([source])[0]
    old = project.replace_proposals(photo_id, 'local', [{}, {}, {}])
    project.claim_proposal(old[0]['id'])
    project.proposal_status(old[1]['id'], 'done')
    project.replace_proposals(photo_id, 'local', [{'label': 'new'}])
    project.dismiss_proposals([photo_id])
    records = {p['id']: p for p in project.proposals()}
    assert records[old[0]['id']]['status'] == 'executing'
    assert records[old[1]['id']]['status'] == 'done'
    assert records[old[2]['id']]['status'] == 'superseded'
    assert not project.proposals(photo_ids=[photo_id], pending_only=True)


def test_invalid_batch_does_not_discard_previous_recommendations(tmp_path):
    source = tmp_path / 'photo.png'
    source.write_bytes(b'photo')
    project = Project.create(tmp_path / 'editing.ccrawagent', '项目')
    photo_id = project.import_paths([source])[0]
    project.replace_proposals(photo_id, 'local', [{'label': 'old'}])
    previous = project.proposals()
    with pytest.raises((TypeError, ValueError)):
        project.replace_proposals(photo_id, 'local', [{'label': 'new'}, {'invalid': object()}])
    assert project.proposals() == previous


def test_output_path_and_oversized_context_are_bounded(tmp_path):
    project = Project.create(tmp_path / 'trip.ccrawagent', '旅行')
    with pytest.raises(ValueError):
        project.output('../outside.png')
    with pytest.raises(ValueError):
        project.message('user', 'x' * 16001)
    assert Path(project.output('ok.png')).parent == project.root / 'derived'


@pytest.mark.parametrize(
    'changes', [{'id': '../../outside'}, {'name': []}, {'timezone': 'invalid/zone'}]
)
def test_manifest_validates_identity_and_timezone(tmp_path, changes):
    project = Project.create(tmp_path / 'valid.ccrawagent', '项目')
    manifest = dict(project.manifest, **changes)
    project.path.write_text(json.dumps(manifest), encoding='utf8')
    with pytest.raises(ValueError):
        Project.open(project.path)


def test_tampered_database_photo_id_cannot_overwrite_original_on_scan(tmp_path):
    from PIL import Image
    from ccraw.photo_agent.analysis import index_project

    source = tmp_path / 'original.jpg'
    Image.new('RGB', (1000, 900), 'red').save(source)
    before = source.read_bytes()
    project = Project.create(tmp_path / 'tampered.ccrawagent', '项目')
    photo_id = project.import_paths([source])[0]
    with project.connect() as db:
        db.execute('UPDATE photos SET id=? WHERE id=?', (str(source.with_suffix('')), photo_id))
    with pytest.raises(ValueError, match='照片标识'):
        index_project(project)
    assert source.read_bytes() == before
