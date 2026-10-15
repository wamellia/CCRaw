"""UI regrouping keeps saved positions and local model lifecycle intact."""

import threading
from types import SimpleNamespace
import pytest


def test_old_workspace_restores_library_generation_and_professional_route(window):
    from test_ui import wait_until

    window._settings_binding.restore_workspace(
        {'tabs': {'tabs-0': 9, 'tabs-1': 5, 'tabs-2': 1, 'tabs-3': 1}}
    )
    assert window.library_tabs.currentIndex() == 1
    assert window.instruction_tabs.currentIndex() == 1
    assert window.editor_tool_active('point_color')
    snapshot = window._settings_binding.workspace_snapshot()
    assert snapshot['tabs']['tabs-2'] == 1 and snapshot['tabs']['tabs-3'] == 1
    assert 'editorCategoryTabs' in snapshot['tabs']
    wait_until(lambda: not window.jobs and not window.timer.isActive())


@pytest.mark.parametrize('entry', ['scan', 'chat', 'search'])
def test_settings_model_install_refreshes_agent_without_mutating_inflight_worker(
    app, tmp_path, monkeypatch, entry
):
    from ccraw.photo_agent import models, ui
    from ccraw.photo_agent.store import Project
    from ccraw.ui.settings_dialog import SettingsDialog

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'user'))
    monkeypatch.setenv('CCRAW_CACHE_DIR', str(tmp_path / 'cache'))
    present = [False]
    monkeypatch.setattr(models.LocalModels, '_present', lambda *_: present[0])
    monkeypatch.setattr(ui.nl_edit, 'active', lambda *_: {'model': '', 'label': 'Local'})
    captured = []
    monkeypatch.setattr(
        ui.analysis,
        'index_project',
        lambda *args, **kwargs: captured.append(kwargs['models']) or {'analyzed': 0},
    )

    async def run_turn(*args, **kwargs):
        captured.append(kwargs['models'])
        return 'Done'

    monkeypatch.setattr(ui, 'run_turn', run_turn)
    monkeypatch.setattr(
        ui.search,
        'search',
        lambda *args, **kwargs: captured.append(kwargs['models']) or {'photos': []},
    )
    project = Project.create(tmp_path / 'project.ccrawagent', '测试')
    # Search is unavailable in an empty project. Exercise model refresh through
    # an actual searchable workspace rather than bypassing that UI guard.
    if entry == 'search':
        from PIL import Image

        photo = tmp_path / 'photo.jpg'
        Image.new('RGB', (32, 24)).save(photo)
        project.import_paths([photo])
    window = ui.AgentWindow(project)
    dialog = SettingsDialog(window)
    try:
        window.models = models.LocalModels()
        previous = window.models
        assert not previous.clip_ready and not previous.face_ready
        window.worker = SimpleNamespace()
        present[0] = True
        dialog._model_executor = SimpleNamespace(shutdown=lambda **_: None)
        dialog._model_finished('')
        assert window.models is previous
        window.worker = None
        if entry == 'scan':
            window.scan_operation(threading.Event(), lambda *_: None)
        else:
            monkeypatch.setattr(
                window,
                'start_work',
                lambda label, operation, *args: (
                    operation(threading.Event(), lambda *_: None) or True
                ),
            )
            if entry == 'chat':
                window.input.setPlainText('整理照片')
                window.send()
            else:
                window.search_input.setText('sunset')
                window.local_search()
        assert captured[-1].clip_ready and captured[-1].face_ready
        assert window.models is not previous
    finally:
        window.worker = None
        dialog.close()
        window.close()
