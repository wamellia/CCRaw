import json

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from ccraw.photo_agent.store import Project


def test_recent_projects_persist_sort_by_open_time_and_deduplicate(tmp_path, monkeypatch):
    from ccraw.photo_agent import recent

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'settings'))
    first = Project.create(tmp_path / 'a.ccrawagent', '夏日相册')
    second = Project.create(tmp_path / 'b.ccrawagent', '街拍')
    clock = iter((100, 200, 300))
    monkeypatch.setattr(recent.time, 'time', lambda: next(clock))
    recent.remember_project(first)
    recent.remember_project(second)
    first.manifest['name'] = '夏日旅行'
    recent.remember_project(first)
    records = recent.recent_projects()
    assert [r['name'] for r in records] == ['夏日旅行', '街拍']
    assert [r['opened'] for r in records] == [300, 200]
    assert [r['path'] for r in records] == [str(first.path), str(second.path)]
    assert (
        json.loads((tmp_path / 'settings' / 'recent-agent-projects.json').read_text('utf8'))[
            'projects'
        ]
        == records
    )


def test_invalid_recent_records_do_not_block_startup(tmp_path, monkeypatch):
    from ccraw.photo_agent import recent

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'settings'))
    folder = tmp_path / 'settings'
    folder.mkdir()
    history = folder / 'recent-agent-projects.json'
    history.write_text('{broken', encoding='utf8')
    assert recent.recent_projects() == []
    history.write_text(
        json.dumps(
            {
                'version': 1,
                'projects': [
                    {'path': str(tmp_path / 'good.ccrawagent'), 'name': '正常', 'opened': 10},
                    {'path': 'relative.ccrawagent', 'name': '相对路径', 'opened': 20},
                    {
                        'path': str(tmp_path / 'bad.ccrawagent'),
                        'name': '错误',
                        'opened': 'not a time',
                    },
                    {'path': str(tmp_path / 'bad.jpg'), 'name': '照片', 'opened': 30},
                    {
                        'path': str(tmp_path / 'bad.ccrawagent'),
                        'name': '错误',
                        'opened': float('nan'),
                    },
                    None,
                ],
            }
        ),
        encoding='utf8',
    )
    assert [r['name'] for r in recent.recent_projects()] == ['正常']


def test_recent_list_search_and_single_click_open_real_project(app, tmp_path, monkeypatch):
    from ccraw.photo_agent.ui import Launcher

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'settings'))
    first = Project.create(tmp_path / 'summer.ccrawagent', '夏日旅行')
    second = Project.create(tmp_path / 'CITY.ccrawagent', '街拍')
    launcher = Launcher()
    reopened = None
    try:
        one = launcher.load_agent(first.path)
        launcher.load_agent(second.path)
        assert launcher.recent_list.count() == 2
        launcher.load_agent(first.path)
        assert len(launcher.windows) == 2
        assert launcher.recent_list.item(0).data(Qt.ItemDataRole.UserRole)['path'] == str(
            first.path
        )
        reopened = Launcher()
        reopened.show()
        app.processEvents()
        assert reopened.recent_list.count() == 2
        reopened.project_search.setText('city')
        assert reopened.recent_list.count() == 1
        assert reopened.recent_list.item(0).data(Qt.ItemDataRole.UserRole)['path'] == str(
            second.path
        )
        reopened.project_search.setText('没有这个工程')
        assert reopened.recent_list.count() == 0 and reopened.empty_state.isVisible()
        reopened.project_search.clear()
        # A single actual mouse click opens the existing project and moves it to the top.
        QTest.mouseClick(
            reopened.recent_list.viewport(),
            Qt.MouseButton.LeftButton,
            pos=reopened.recent_list.visualItemRect(reopened.recent_list.item(1)).center(),
        )
        app.processEvents()
        assert reopened.recent_list.item(0).data(Qt.ItemDataRole.UserRole)['path'] == str(
            second.path
        )
        reopened.project_search.setText('夏日')
        assert reopened.recent_list.count() == 1
        assert reopened.recent_list.item(0).data(Qt.ItemDataRole.UserRole)['path'] == str(
            one.project.path
        )
    finally:
        if reopened:
            reopened.close()
        for window in launcher.windows[:]:
            window.close()
        launcher.close()
        app.processEvents()


def test_failed_open_does_not_add_recent_project(app, tmp_path, monkeypatch):
    from ccraw.photo_agent.ui import Launcher

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'settings'))
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    launcher = Launcher()
    try:
        assert launcher.load_agent(tmp_path / 'missing.ccrawagent') is None
        assert warnings
        assert launcher.recent_list.count() == 0
    finally:
        launcher.close()


def test_launcher_actions_create_open_agent_and_keep_quick_editor(app, tmp_path, monkeypatch):
    from ccraw.photo_agent.ui import Launcher
    from ccraw.app import MainWindow
    from PySide6.QtWidgets import QFileDialog
    from test_ui import wait_until

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'settings'))
    path = tmp_path / 'new.ccrawagent'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
    launcher = Launcher()
    try:
        launcher.agent_button.click()
        assert path.is_file() and launcher.windows[0].project.path == path
        launcher.open_agent_button.click()
        assert len(launcher.windows) == 1
        launcher.catalog_button.click()
        assert isinstance(launcher.windows[-1], MainWindow)
        assert launcher.windows[-1].source is None
        wait_until(lambda: not launcher.windows[-1].jobs, timeout=45)
    finally:
        for window in launcher.windows[:]:
            window.close()
        launcher.close()
        app.processEvents()


def test_launcher_live_theme_switch_updates_sidebar_and_readable_project_text(
    app, tmp_path, monkeypatch
):
    from ccraw.photo_agent.ui import Launcher
    from ccraw.photo_agent import recent
    from ccraw.ui.theme import apply_theme
    from PySide6.QtCore import QRect

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'settings'))
    project = Project.create(tmp_path / 'album.ccrawagent', '相册')
    recent.remember_project(project)
    apply_theme(app, 'dark')
    launcher = Launcher()
    launcher.show()
    app.processEvents()
    try:
        assert launcher.sidebar.grab().toImage().pixelColor(5, 5).lightness() < 80
        apply_theme(app, 'light')
        app.processEvents()
        assert launcher.sidebar.grab().toImage().pixelColor(5, 5).lightness() > 220
        text = launcher.recent_list.viewport().grab(QRect(64, 10, 120, 32)).toImage()
        assert any(
            text.pixelColor(x, y).lightness() < 80
            for x in range(text.width())
            for y in range(text.height())
        )
    finally:
        launcher.close()
        apply_theme(app, 'light')
