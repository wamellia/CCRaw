import threading

import numpy as np
from PIL import Image
import pytest

from ccraw import model
from ccraw.photo_agent import editing
from ccraw.photo_agent.store import Project
from ccraw.photo_agent.plugins import photo_registry
from ccraw.photo_agent.models import digest


def photo_project(tmp_path, count=1):
    project = Project.create(tmp_path / 'project.ccrawagent', '测试')
    ids = []
    for i in range(count):
        path = tmp_path / f'{i}.png'
        image = np.zeros((100, 200, 3), np.uint8)
        image[:, :100] = (50, 70, 90)
        image[:, 100:] = (110, 140, 170)
        Image.fromarray(image).save(path)
        photo_id = project.import_paths([path])[0]
        project.update_photo(
            photo_id,
            facts={'width': 200, 'height': 100, 'flags': []},
            sha=digest(path),
            status='ready',
        )
        ids.append(photo_id)
    return project, ids


def test_agent_geometry_masks_and_channel_curves_reach_the_existing_engine(tmp_path):
    project, ids = photo_project(tmp_path)
    proposal = editing.propose_local(
        project,
        ids[0],
        {
            'crop': [0.5, 0, 0.75, 1],
            'rotation': 1,
            'curves': {'R': [[0, 0], [0.5, 0.6], [1, 1]]},
            'masks': [
                {
                    'kind': 'radial',
                    'start': [0.55, 0.1],
                    'end': [0.9, 0.9],
                    'adjustments': {'exposure': 0.3},
                }
            ],
        },
    )[0]
    recipe = proposal['payload']['recipe']
    assert recipe['crop'] == [0.5, 0, 0.75, 1] and recipe['rotation'] == 1
    assert recipe['curves']['R'][1] == [0.5, 0.6]
    assert recipe['masks'][0]['kind'] == 'radial'
    original = digest(project.photo(ids[0])['path'])
    with Image.open(editing.apply_local(project, proposal['id'])) as result:
        assert result.size == (100, 50)
    assert digest(project.photo(ids[0])['path']) == original


@pytest.mark.parametrize(
    'patch',
    [
        {'not_a_tool': 1},
        {'adjustments': {'exposure': 999}},
        {'adjustments': {'exposure': True}},
        {'adjustments': {'exposure': float('nan')}},
        {'crop': [0.8, 0, 0.2, 1]},
        {'rotation': 0.5},
        {'curves': {'R': [[0, 0], [0.5, 0.5], [0.5, 0.8], [1, 1]]}},
        {'masks': [{'kind': 'radial', 'start': [2, 0], 'adjustments': {'exposure': 1}}]},
        {'masks': [{'kind': 'linear', 'luminance_range': [0, 50], 'adjustments': {'exposure': 1}}]},
        {'masks': [{'kind': 'brush', 'strokes': [], 'adjustments': {'exposure': 1}}]},
        {'curve': [[0, 0], [255, 255]], 'curves': {'RGB': [[0, 0], [1, 1]]}},
        {'adjustments': {'exposure': 10**1000}},
    ],
)
def test_invalid_agent_patch_is_rejected_without_replacing_pending_proposals(tmp_path, patch):
    project, ids = photo_project(tmp_path)
    first = editing.propose_local(project, ids[0])
    with pytest.raises(ValueError):
        editing.propose_local(project, ids[0], patch)
    assert {p['id'] for p in project.proposals(pending_only=True)} == {p['id'] for p in first}


def test_control_discovery_reports_ranges_and_the_saved_recipe(tmp_path):
    from ccraw.photo_agent.controls import describe

    project, ids = photo_project(tmp_path)
    recipe = model.recipe()
    recipe['adjustments']['exposure'] = 0.75
    project.preference('recipe:' + ids[0], recipe)
    registry = photo_registry(project, ids, threading.Event(), None)
    result = registry.tools['get_edit_controls'].handler(photo_id=ids[0])
    assert result['current']['exposure'] == 0.75
    assert result['schema']['properties']['exposure']['minimum'] == -5
    assert 'crop' in describe(recipe)['sections']


def test_batch_preparation_validates_all_sources_before_transaction(tmp_path):
    project, ids = photo_project(tmp_path, 2)
    first = [editing.propose_local(project, photo_id) for photo_id in ids]
    Image.new('RGB', (200, 100), 'white').save(project.photo(ids[1])['path'])
    with pytest.raises(ValueError, match='原片'):
        editing.propose_batch(project, ids, {'adjustments': {'exposure': 1}})
    assert {p['id'] for p in project.proposals(pending_only=True)} == {
        p['id'] for group in first for p in group
    }


def test_batch_is_bounded_by_the_ui_selection_and_preserves_individual_recipes(tmp_path):
    project, ids = photo_project(tmp_path, 2)
    base = model.recipe()
    base['crop'] = [0.1, 0.1, 0.9, 0.9]
    project.preference('recipe:' + ids[1], base)
    registry = photo_registry(project, ids[:1], threading.Event(), None)
    with pytest.raises(ValueError):
        registry.tools['propose_batch_adjustment'].handler(
            photo_ids=ids, patch={'adjustments': {'exposure': 1}}
        )
    result = editing.propose_batch(project, ids, {'adjustments': {'exposure': 1}})
    assert len(result) == 2
    assert result[1]['payload']['recipe']['crop'] == base['crop']
    assert project.derivatives() == []


def test_preview_is_cached_aspect_correct_and_does_not_apply_the_proposal(tmp_path, monkeypatch):
    project, ids = photo_project(tmp_path)
    proposal = editing.propose_local(project, ids[0], {'crop': [0.5, 0, 1, 1]})[0]
    result = editing.preview_local(project, proposal['id'], max_edge=512)
    assert result['cached'] is False
    folder = project.directory('previews')
    with (
        Image.open(folder / result['before']) as before,
        Image.open(folder / result['after']) as after,
    ):
        assert before.size == (200, 100) and after.size == (100, 100)
    assert project.get_proposal(proposal['id'])['status'] == 'pending'
    assert project.derivatives() == []
    monkeypatch.setattr(
        editing.engine, 'load_image', lambda *a, **k: pytest.fail('Cache should avoid decoding')
    )
    assert editing.preview_local(project, proposal['id'], max_edge=512)['cached'] is True


def test_cancelled_and_foreign_preview_does_not_read_or_render(tmp_path):
    project, ids = photo_project(tmp_path, 2)
    proposal = editing.propose_local(project, ids[1])[0]
    registry = photo_registry(project, ids[:1], threading.Event(), None)
    with pytest.raises(ValueError):
        registry.tools['preview_edit'].handler(proposal_id=proposal['id'])
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        editing.preview_local(project, proposal['id'], cancel=cancel)


def test_preview_cache_recovers_damage_and_rejects_changed_original(tmp_path):
    project, ids = photo_project(tmp_path)
    proposal = editing.propose_local(project, ids[0])[0]
    result = editing.preview_local(project, proposal['id'])
    (project.directory('previews') / result['after']).write_bytes(b'not a JPEG')
    rebuilt = editing.preview_local(project, proposal['id'])
    assert rebuilt['cached'] is False
    with Image.open(project.directory('previews') / rebuilt['after']) as image:
        image.load()
    Image.new('RGB', (200, 100), 'white').save(project.photo(ids[0])['path'])
    with pytest.raises(ValueError, match='原片'):
        editing.preview_local(project, proposal['id'])


def test_batch_database_failure_rolls_back_every_pending_replacement(tmp_path):
    import sqlite3

    project, ids = photo_project(tmp_path, 2)
    old = [p['id'] for photo_id in ids for p in editing.propose_local(project, photo_id)]
    with project.connect() as db:
        db.execute(
            'CREATE TRIGGER fail_second BEFORE INSERT ON proposals '
            f"WHEN NEW.photo_id='{ids[1]}' BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match='test failure'):
        editing.propose_batch(project, ids, {'adjustments': {'exposure': 1}})
    assert {p['id'] for p in project.proposals(pending_only=True)} == set(old)


def test_discovery_never_truncates_schema_when_current_masks_are_large():
    from ccraw.photo_agent.controls import describe
    from ccraw.photo_agent.plugins import bounded_result

    recipe = model.recipe()
    for i in range(32):
        mask = model.new_mask('brush', i + 1)
        mask['strokes'] = [{'points': [[0.5, 0.5]] * 100, 'radius': 0.1}]
        recipe['masks'].append(mask)
    result = bounded_result(describe(recipe, 'masks'))
    schema = result['schema']['items']['properties']
    assert schema['kind']['enum'] == ['linear', 'radial', 'luminance', 'brush']
    assert schema['strokes']['items']['required'] == ['points', 'radius']
    assert result['current_omitted'] > 0
