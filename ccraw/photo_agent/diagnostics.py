"""Source and frozen-package smoke verification, without external requests."""


def run(source, output):
    import asyncio
    import json
    from pathlib import Path
    import traceback
    import time

    from PIL import Image
    from PySide6.QtWidgets import QApplication
    from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
    from .store import Project
    from .models import LocalModels, digest
    from .analysis import index_project, suggestions
    from .editing import propose_local, apply_local
    from .runtime import run_turn
    from .ui import Launcher, AgentWindow
    from ..ui.theme import apply_theme

    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    report = {}

    class LocalProvider(LLMProvider):
        def __init__(self):
            super().__init__(provider_name='diagnostics')
            self.calls = 0

        def get_default_model(self):
            return 'local-diagnostic'

        async def chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    content=None,
                    tool_calls=[ToolCallRequest(id='report', name='album_report', arguments={})],
                )
            return LLMResponse(content='本地工具往返完成。')

    try:
        app = QApplication.instance() or QApplication([])
        app.setStyle('Fusion')
        apply_theme(app, 'dark')
        path = destination / 'project.ccrawagent'
        project = Project.open(path) if path.exists() else Project.create(path, '工程')
        original = digest(source)
        ids = project.import_paths([source])
        photo_id = ids[0] if ids else project.photos()[0]['id']
        models = LocalModels()
        report['scan'] = index_project(project, models=models)
        assert project.photo(photo_id)['status'] == 'ready'
        provider = LocalProvider()
        asyncio.run(
            run_turn(project, '整理', selected=[photo_id], provider=provider, models=models)
        )
        assert provider.calls == 2
        assert list((project.root / 'sessions').rglob('*.jsonl'))
        report['tool_roundtrip'] = provider.calls
        proposal = propose_local(project, photo_id)[0]
        derived = apply_local(project, proposal['id'])
        with Image.open(derived) as image:
            assert image.size == (
                project.photo(photo_id)['facts']['width'],
                project.photo(photo_id)['facts']['height'],
            )
        assert digest(source) == original
        launcher = Launcher()
        launcher.show()
        window = AgentWindow(project)
        window.report = suggestions(project)
        window.refresh_suggestions()
        window.show()
        app.processEvents()
        window.gallery.item(0).setSelected(True)
        window.open_editor()
        editor = window.editors[-1]
        deadline = time.monotonic() + 90
        while (
            editor.source is None
            or editor.loading
            or editor.render_running
            or editor.jobs
            or editor.timer.isActive()
        ):
            app.processEvents()
            time.sleep(0.02)
            if time.monotonic() > deadline:
                raise TimeoutError('Editor bridge timed out')
        assert editor.source_path == str(Path(source).resolve())
        window.grab().save(str(destination / 'agent.png'))
        launcher.grab().save(str(destination / 'launcher.png'))
        report.update(
            ok=True,
            editor_bridge=True,
            original_unchanged=True,
            semantic_model=models.clip_ready,
            face_model=models.face_ready,
            events=len(project.events()),
        )
        window.close()
        launcher.close()
        app.processEvents()
    except Exception:
        report.update(ok=False, error=traceback.format_exc())
    (destination / 'agent-report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8'
    )
    return 0 if report.get('ok') else 1
