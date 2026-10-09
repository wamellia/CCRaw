import asyncio
import json
import threading

from PIL import Image
import pytest

nanobot = pytest.importorskip('nanobot', reason='requires optional Photo Agent runtime')
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest

from ccraw.photo_agent.store import Project
from ccraw.photo_agent.analysis import index_project
from ccraw.photo_agent.runtime import run_turn
from ccraw.photo_agent.plugins import capabilities, TOOL_NAMES


class ScriptedProvider(LLMProvider):
    def __init__(self):
        super().__init__(provider_name='test')
        self.calls = []

    def get_default_model(self):
        return 'test-model'

    async def chat(self, messages, tools=None, **kwargs):
        self.calls.append((messages, tools))
        if len(self.calls) == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id='call1', name='album_report', arguments={})],
            )
        assert any(m['role'] == 'tool' for m in messages)
        return LLMResponse(content='已分析当前工程。')


def test_reuses_actual_nanobot_loop_with_only_photo_tools_and_persisted_trace(tmp_path):
    project = Project.create(tmp_path / 'p.ccrawagent', '项目')
    image = tmp_path / 'a.png'
    Image.new('RGB', (50, 50), 'blue').save(image)
    project.import_paths([image])
    index_project(project)
    provider = ScriptedProvider()
    reply = asyncio.run(run_turn(project, '整理当前工程', provider=provider))
    assert reply == '已分析当前工程。'
    assert len(provider.calls) == 2
    assert {t['function']['name'] for t in provider.calls[0][1]} == set(TOOL_NAMES)
    assert not {'exec', 'write_file', 'spawn', 'web_fetch'} & set(TOOL_NAMES)
    assert project.messages()[-1]['content'] == reply
    assert any(e['kind'] == 'tool.finished' for e in project.events())
    assert any(e['kind'] == 'runtime.SessionTurnPersisted' for e in project.events())
    assert set(capabilities()) == {'organize', 'retrieve', 'edit'}
    session_files = list((project.root / 'sessions').rglob('*.jsonl'))
    assert session_files and '整理当前工程' in session_files[0].read_text(encoding='utf8')


def test_cancelled_turn_never_calls_provider(tmp_path):
    project = Project.create(tmp_path / 'p.ccrawagent', '项目')
    cancel = threading.Event()
    cancel.set()
    provider = ScriptedProvider()
    with pytest.raises(InterruptedError):
        asyncio.run(run_turn(project, '整理', provider=provider, cancel=cancel))
    assert provider.calls == []


def test_bounded_context_contains_facts_and_selected_ids_not_other_projects(tmp_path):
    from ccraw.photo_agent.runtime import factual_context

    project = Project.create(tmp_path / 'p.ccrawagent', '当前')
    project.preference('language', 'zh')
    context = factual_context(project, [])
    parsed = json.loads(context)
    assert parsed['summary']['photos'] == 0
    assert parsed['selected'] == []
    assert len(context) <= 8000
    assert str(tmp_path) not in context


def test_existing_model_configuration_drives_real_http_tool_roundtrip(tmp_path, monkeypatch):
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from ccraw import nl_edit

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, body))
            message = {'role': 'assistant', 'content': '整理完成。'}
            reason = 'stop'
            if len(requests) == 1:
                message = {
                    'role': 'assistant',
                    'content': None,
                    'tool_calls': [
                        {
                            'id': 'local1',
                            'type': 'function',
                            'function': {'name': 'album_report', 'arguments': '{}'},
                        }
                    ],
                }
                reason = 'tool_calls'
            response = dict(
                id='reply',
                object='chat.completion',
                created=1,
                model='photo-test',
                choices=[{'index': 0, 'message': message, 'finish_reason': reason}],
            )
            if body.get('stream'):
                delta = dict(message)
                for tool in delta.get('tool_calls', []):
                    tool['index'] = 0
                response['object'] = 'chat.completion.chunk'
                response['choices'] = [{'index': 0, 'delta': delta, 'finish_reason': reason}]
                data = ('data: ' + json.dumps(response) + '\n\ndata: [DONE]\n\n').encode()
            else:
                data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header(
                'Content-Type', 'text/event-stream' if body.get('stream') else 'application/json'
            )
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = nl_edit.default_settings()
    settings['provider'] = 'custom'
    settings['providers']['custom'].update(
        base_url=f'http://127.0.0.1:{server.server_port}/v1', model='photo-test', key='test-only'
    )
    monkeypatch.setattr(nl_edit, 'load_settings', lambda: settings)
    try:
        project = Project.create(tmp_path / 'http.ccrawagent', 'HTTP')
        assert asyncio.run(run_turn(project, '整理')) == '整理完成。'
        assert len(requests) == 2
        assert requests[0][0] == '/v1/chat/completions'
        assert any(message['role'] == 'tool' for message in requests[1][1]['messages'])
        assert {t['function']['name'] for t in requests[0][1]['tools']} == set(TOOL_NAMES)
        assert 'test-only' not in str(project.events())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize('level,expected', [('max', 'max'), ('off', 'low'), ('default', None)])
def test_anthropic_reuses_shared_reasoning_in_actual_request_body(monkeypatch, level, expected):
    from ccraw import nl_edit
    from ccraw.photo_agent.runtime import configured_provider
    from nanobot.utils.llm_runtime import LLMRuntime

    settings = nl_edit.default_settings()
    settings['provider'] = 'anthropic'
    settings['reasoning'] = level
    settings['providers']['anthropic'].update(model='claude-opus-5-5', key='test-only')
    monkeypatch.setattr(nl_edit, 'load_settings', lambda: settings)
    provider, service = configured_provider()
    runtime = LLMRuntime.capture(provider, service['model'], context_window_tokens=8192)
    kwargs = provider._build_kwargs(
        [{'role': 'user', 'content': '整理'}],
        None,
        runtime.model,
        4096,
        0.1,
        runtime.generation.reasoning_effort,
        None,
    )
    assert kwargs.get('output_config', {}).get('effort') == expected


def test_session_manager_never_selects_global_session_root(tmp_path, monkeypatch):
    import nanobot.session.manager as manager

    def global_root(*args):
        pytest.fail('Session storage must be explicitly project-local')

    monkeypatch.setattr(manager, 'get_runtime_subdir', global_root)
    project = Project.create(tmp_path / 'private.ccrawagent', '工程')
    asyncio.run(run_turn(project, '整理', provider=ScriptedProvider()))
    assert list((project.root / 'sessions').rglob('*.jsonl'))


def test_moving_project_keeps_actual_agent_conversation_history(tmp_path):
    import shutil

    first = tmp_path / 'before'
    first.mkdir()
    project = Project.create(first / 'portable.ccrawagent', '工程')
    asyncio.run(run_turn(project, '记住这是一组日落照片', provider=ScriptedProvider()))
    second = tmp_path / 'after'
    shutil.move(str(first), str(second))
    moved = Project.open(second / 'portable.ccrawagent')
    provider = ScriptedProvider()
    asyncio.run(run_turn(moved, '继续整理', provider=provider))
    assert any(
        message.get('role') == 'user' and '记住这是一组日落照片' in str(message.get('content'))
        for message in provider.calls[0][0]
    )
