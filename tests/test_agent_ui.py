from PIL import Image

from ccraw.photo_agent.store import Project


def test_launcher_has_three_actions_and_recent_projects_at_compact_size(app, tmp_path, monkeypatch):
    from ccraw.photo_agent.ui import Launcher
    from PySide6.QtCore import QPoint

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'settings'))

    window = Launcher()
    window.resize(720, 440)
    window.show()
    app.processEvents()
    try:
        assert window.catalog_button.text() == '快捷编辑'
        assert window.agent_button.text() == 'Photo Agent 新建工程'
        assert window.open_agent_button.text() == '打开 Agent 工程'
        assert window.projects.isVisible() and window.project_search.isVisible()
        for control in (window.agent_button, window.open_agent_button, window.catalog_button):
            assert control.height() >= 40
            assert window.rect().contains(control.mapTo(window, control.rect().bottomRight()))
        right = window.project_search.mapTo(window, QPoint(0, 0)).x()
        assert right > window.agent_button.mapTo(window, window.agent_button.rect().topRight()).x()
        assert window.recent_list.count() == 0 and window.empty_state.isVisible()
    finally:
        window.close()


def test_agent_import_scan_selection_annotation_and_proposals(app, tmp_path, monkeypatch):
    from ccraw.photo_agent.ui import AgentWindow
    from ccraw.photo_agent.analysis import index_project
    from ccraw.photo_agent.editing import propose_local

    project = Project.create(tmp_path / 'project.ccrawagent', '相册')
    image = tmp_path / 'photo.png'
    Image.new('RGB', (120, 80), 'blue').save(image)
    ids = project.import_paths([image])
    index_project(project)
    window = AgentWindow(project)
    window.show()
    app.processEvents()
    assert window.gallery.count() == 1
    window.gallery.item(0).setSelected(True)
    assert window.selected_ids() == ids
    window.save_annotation(2, ['海边'])
    assert project.preferences()['annotation:' + ids[0]]['person_count'] == 2
    propose_local(project, ids[0])
    window.refresh_records()
    assert window.proposal_list.count() == 3
    assert window.apply_button.isEnabled()
    window.resize(1180, 780)
    window.tabs.setCurrentIndex(1)
    app.processEvents()
    for button in (window.send_button, window.apply_button, window.editor_button):
        assert button.isVisible()
        assert button.width() >= 75
        assert button.height() >= 30
    window.close()


def test_worker_cancel_does_not_leave_thread_running(app, tmp_path):
    from ccraw.photo_agent.ui import AgentWindow
    from tests.test_ui import wait_until

    window = AgentWindow(Project.create(tmp_path / 'p.ccrawagent', '项目'))
    window.start_work('测试', lambda cancel, emit: cancel.wait(2))
    assert window.worker is not None
    window.cancel_work()
    wait_until(lambda: window.worker is None)
    window.close()


def test_existing_editor_saves_recipe_back_to_agent_project(app, tmp_path):
    from ccraw.photo_agent.ui import AgentWindow
    from ccraw.photo_agent.analysis import index_project
    from test_ui import wait_until

    source = tmp_path / 'editable.png'
    Image.new('RGB', (240, 180), (60, 80, 100)).save(source)
    before = source.read_bytes()
    project = Project.create(tmp_path / 'editor.ccrawagent', '工程')
    photo_id = project.import_paths([source])[0]
    index_project(project)
    window = AgentWindow(project)
    window.gallery.item(0).setSelected(True)
    window.open_editor()
    editor = window.editors[0]
    wait_until(
        lambda: (
            editor.source is not None
            and not editor.loading
            and not editor.jobs
            and not editor.render_running
            and not editor.timer.isActive()
        ),
        # A freshly extracted source tree compiles native preview kernels once.
        timeout=45,
    )
    editor.edits['adjustments']['exposure'] = 0.25
    next(
        action for action in editor.menuBar().actions() if action.text() == '保存调整到 Photo Agent'
    ).trigger()
    assert project.preferences()['recipe:' + photo_id]['adjustments']['exposure'] == 0.25
    assert source.read_bytes() == before
    assert editor.edits == editor.saved_edits
    window.close()


def test_recommendations_follow_selection_and_clear_only_selected_photo(app, tmp_path):
    from ccraw.photo_agent.ui import AgentWindow
    from ccraw.photo_agent.analysis import index_project
    from ccraw.photo_agent.editing import propose_local
    from PySide6.QtCore import Qt

    project = Project.create(tmp_path / 'recommend.ccrawagent', '照片')
    sources = [tmp_path / 'first.png', tmp_path / 'second.png']
    for source in sources:
        Image.new('RGB', (120, 80), 'blue').save(source)
    ids = project.import_paths(sources)
    index_project(project)
    propose_local(project, ids[0])
    propose_local(project, ids[1])
    window = AgentWindow(project)
    try:
        assert window.proposal_list.count() == 0
        for row in (0, 1):
            window.gallery.clearSelection()
            window.gallery.item(row).setSelected(True)
            assert window.proposal_list.count() == 3
            assert all(
                window.proposal_list.item(i).data(Qt.ItemDataRole.UserRole)['photo_id'] == ids[row]
                for i in range(window.proposal_list.count())
            )
        window.clear_proposals_button.click()
        assert window.proposal_list.count() == 0
        assert not window.apply_button.isEnabled()
        window.gallery.clearSelection()
        window.gallery.item(0).setSelected(True)
        assert window.proposal_list.count() == 3
        window.gallery.clearSelection()
        assert window.proposal_list.count() == 0
    finally:
        window.close()


def test_selected_recommendations_are_not_hidden_by_other_photos_history(app, tmp_path):
    from ccraw.photo_agent.ui import AgentWindow
    from ccraw.photo_agent.analysis import index_project
    from ccraw.photo_agent.editing import propose_local

    project = Project.create(tmp_path / 'history.ccrawagent', '照片')
    sources = [tmp_path / 'a.png', tmp_path / 'b.png']
    for source in sources:
        Image.new('RGB', (120, 80), 'blue').save(source)
    ids = project.import_paths(sources)
    index_project(project)
    propose_local(project, ids[0])
    for index in range(105):
        project.proposal(ids[1], 'local', {'label': str(index)})
    window = AgentWindow(project)
    try:
        window.gallery.item(0).setSelected(True)
        assert window.proposal_list.count() == 3
    finally:
        window.close()
