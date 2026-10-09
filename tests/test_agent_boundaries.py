import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from ccraw.photo_agent.store import Project
from ccraw.photo_agent.plugins import bounded_result, photo_registry, PluginRegistry, Capability
from ccraw.photo_agent.editing import propose_external, execute_external
from test_agent_editing import setup_photo


def test_tool_result_is_complete_bounded_json_and_reports_omissions():
    result = bounded_result({'events': [{'photos': ['f' * 32] * 10000}] * 200})
    assert len(json.dumps(result, ensure_ascii=False)) <= 5500
    assert result['events_omitted'] > 0
    assert result['events'][0]['photos_omitted'] > 0


def test_photo_tools_reject_unselected_and_foreign_photo_ids(tmp_path):
    project, photo_id, _ = setup_photo(tmp_path)
    registry = photo_registry(project, (), threading.Event(), None)
    with pytest.raises(ValueError, match='选中'):
        registry.tools['recommend_edits'].handler(photo_id)
    registry = photo_registry(project, ('foreign',), threading.Event(), None)
    with pytest.raises(ValueError, match='不属于'):
        registry.tools['diagnose_photo'].handler('foreign')
    assert project.proposals() == []


def test_proposal_claim_is_atomic_between_workers(tmp_path):
    project, photo_id, _ = setup_photo(tmp_path)
    proposal = project.proposal(photo_id, 'local', {})

    def claim(_):
        try:
            project.claim_proposal(proposal)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(claim, range(2))).count(True) == 1


def test_changed_provider_prevents_upload_and_preserves_original(tmp_path):
    project, photo_id, original = setup_photo(tmp_path)
    settings = dict(base_url='https://example.com', model='test', key='secret')
    proposal = propose_external(project, photo_id, 'watercolor', settings)

    class Client:
        def generate(self, *args):
            pytest.fail('Changed disclosure must not upload')

    with pytest.raises(ValueError, match='发生变化'):
        execute_external(
            project,
            proposal['id'],
            dict(settings, model='other'),
            approved_digest=proposal['payload']['disclosure_digest'],
            client=Client(),
        )
    assert project.proposals()[0]['status'] == 'pending'


def test_plugin_requires_declared_tool_ownership():
    registry = PluginRegistry()
    with pytest.raises(ValueError):
        registry.register(Capability('malformed', ('unknown',), ()), [])


def test_cancelling_inflight_llm_turn_records_cancelled_job(tmp_path):
    pytest.importorskip('nanobot')
    from nanobot.providers.base import LLMProvider
    from ccraw.photo_agent.runtime import run_turn

    class Slow(LLMProvider):
        def get_default_model(self):
            return 'slow'

        async def chat(self, *args, **kwargs):
            await asyncio.sleep(20)

    project = Project.create(tmp_path / 'cancel.ccrawagent', '取消')
    cancel = threading.Event()

    async def scenario():
        task = asyncio.create_task(
            run_turn(project, '整理', provider=Slow(provider_name='test'), cancel=cancel)
        )
        await asyncio.sleep(0.2)
        cancel.set()
        with pytest.raises(InterruptedError):
            await asyncio.wait_for(task, 3)

    asyncio.run(scenario())
    assert any(event['kind'] == 'job.cancelled' for event in project.events())
