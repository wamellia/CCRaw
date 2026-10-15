import base64
import io
import threading

import pytest
from PIL import Image

from ccraw.photo_agent.store import Project
from ccraw.photo_agent.analysis import index_project
from ccraw.photo_agent.editing import propose_local, apply_local, propose_external, execute_external


def setup_photo(tmp_path):
    source = tmp_path / 'photo.png'
    Image.new('RGB', (320, 220), (60, 100, 140)).save(source)
    project = Project.create(tmp_path / 'p.ccrawagent', '项目')
    photo_id = project.import_paths([source])[0]
    index_project(project)
    return project, photo_id, source


def test_local_diagnosis_offers_three_or_less_and_writes_full_size_derivative(tmp_path):
    project, photo_id, source = setup_photo(tmp_path)
    before = source.read_bytes()
    proposals = propose_local(project, photo_id)
    assert 1 <= len(proposals) <= 3
    path = apply_local(project, proposals[0]['id'])
    with Image.open(path) as image:
        image.load()
        assert image.size == (320, 220)
    assert source.read_bytes() == before
    assert project.derivatives()[0]['provenance']['source_sha256'] == project.photo(photo_id)['sha']
    with pytest.raises(ValueError):
        apply_local(project, proposals[0]['id'])


def test_changed_source_invalidates_local_proposal(tmp_path):
    project, photo_id, source = setup_photo(tmp_path)
    proposal = propose_local(project, photo_id)[0]
    Image.new('RGB', (320, 220), 'white').save(source)
    with pytest.raises(ValueError, match='原片'):
        apply_local(project, proposal['id'])
    assert project.derivatives() == []


def test_recommending_again_replaces_pending_batch_without_erasing_history(tmp_path):
    project, photo_id, _ = setup_photo(tmp_path)
    first = propose_local(project, photo_id)
    second = propose_local(project, photo_id)
    records = project.proposals()
    assert len([p for p in records if p['status'] == 'pending']) == 3
    assert {p['id'] for p in records if p['status'] == 'pending'} == {p['id'] for p in second}
    assert all(p['status'] == 'superseded' for p in records if p['id'] in {q['id'] for q in first})
    with pytest.raises(ValueError):
        project.claim_proposal(first[0]['id'])


def test_invalid_replacement_keeps_previous_recommendations(tmp_path):
    project, photo_id, _ = setup_photo(tmp_path)
    first = propose_local(project, photo_id)
    with pytest.raises(ValueError):
        propose_local(project, photo_id, [])
    assert {p['id'] for p in project.proposals() if p['status'] == 'pending'} == {
        p['id'] for p in first
    }


def test_external_execution_requires_exact_disclosure_and_is_single_use(tmp_path):
    project, photo_id, source = setup_photo(tmp_path)
    settings = dict(
        base_url='https://example.com/v1', model='test', key='secret', timeout=30, watermark=False
    )
    proposal = propose_external(project, photo_id, '水彩画', settings, unit_cost=1.2)
    assert proposal['payload']['provider'] == 'https://example.com/v1'
    assert proposal['payload']['cost'] == '预计 1.20 / 张（服务商实际结算）'
    assert 'secret' not in str(project.proposals())
    calls = []

    class Client:
        def generate(self, config, prompt, reference, size):
            calls.append(reference)
            data = io.BytesIO()
            Image.new('RGB', (400, 300), 'blue').save(data, format='PNG')
            return data.getvalue()

    with pytest.raises(ValueError):
        execute_external(
            project, proposal['id'], settings, approved_digest='wrong', client=Client()
        )
    assert calls == []
    path = execute_external(
        project,
        proposal['id'],
        settings,
        approved_digest=proposal['payload']['disclosure_digest'],
        client=Client(),
    )
    assert len(calls) == 1
    uploaded = base64.b64decode(calls[0].split(',')[1])
    with Image.open(io.BytesIO(uploaded)) as image:
        assert max(image.size) <= 2048 and not image.getexif()
    with Image.open(path) as image:
        assert image.size == (400, 300)
    with pytest.raises(ValueError):
        execute_external(
            project,
            proposal['id'],
            settings,
            approved_digest=proposal['payload']['disclosure_digest'],
            client=Client(),
        )
    assert len(calls) == 1


def test_restart_never_replays_external_execution(tmp_path):
    project, photo_id, _ = setup_photo(tmp_path)
    settings = dict(base_url='https://example.com', model='test', key='secret')
    proposal = propose_external(project, photo_id, 'watercolor', settings)
    project.claim_proposal(proposal['id'])
    project.recover()
    with pytest.raises(ValueError):
        project.claim_proposal(proposal['id'])
    assert project.proposals()[0]['status'] == 'interrupted'


def test_cancelled_local_edit_does_not_leave_a_derived_image(tmp_path):
    project, photo_id, _ = setup_photo(tmp_path)
    proposal = propose_local(project, photo_id)[0]
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        apply_local(project, proposal['id'], cancel)
    assert project.derivatives() == []


def test_external_upload_uses_the_exact_approved_bytes_even_if_reference_changes(
    tmp_path, monkeypatch
):
    project, photo_id, _ = setup_photo(tmp_path)
    settings = dict(base_url='https://example.com', model='test', key='secret')
    proposal = propose_external(project, photo_id, 'watercolor', settings)
    reference = project.root / 'previews' / proposal['payload']['reference']
    approved_bytes = reference.read_bytes()
    claim = project.claim_proposal

    def replace_after_claim(proposal_id):
        result = claim(proposal_id)
        reference.write_bytes(b'changed after validation')
        return result

    monkeypatch.setattr(project, 'claim_proposal', replace_after_claim)

    class Client:
        def generate(self, config, prompt, data, size):
            assert base64.b64decode(data.split(',', 1)[1]) == approved_bytes
            output = io.BytesIO()
            Image.new('RGB', (50, 50), 'red').save(output, format='PNG')
            return output.getvalue()

    execute_external(
        project,
        proposal['id'],
        settings,
        approved_digest=proposal['payload']['disclosure_digest'],
        client=Client(),
    )


def test_external_old_proposal_rejected_after_changed_original_is_reindexed(tmp_path):
    project, photo_id, source = setup_photo(tmp_path)
    settings = dict(base_url='https://example.com', model='test', key='secret')
    proposal = propose_external(project, photo_id, 'watercolor', settings)
    Image.new('RGB', (320, 220), 'white').save(source)
    index_project(project)

    class Client:
        def generate(self, *args):
            pytest.fail('Changed source must not upload')

    with pytest.raises(ValueError, match='原片'):
        execute_external(
            project,
            proposal['id'],
            settings,
            approved_digest=proposal['payload']['disclosure_digest'],
            client=Client(),
        )
