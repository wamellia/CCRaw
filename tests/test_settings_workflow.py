"""Unified application/project preferences and durable recovery behavior."""

import json
import sqlite3

import pytest
from PySide6.QtWidgets import QMainWindow, QPushButton, QWidget, QVBoxLayout, QLabel


def test_settings_atomic_scope_precedence_and_broadcast(tmp_path):
    from ccraw.settings import SettingsService

    service = SettingsService(tmp_path / 'settings.json', migrate=False)
    events = []
    service.changed.connect(events.append)
    service.update({'appearance': {'language': 'en'}, 'import': {'other_preset': 'vivid'}})
    assert service.read()['appearance']['language'] == 'en'
    assert (
        service.read(project={'import': {'other_preset': 'neutral'}})['import']['other_preset']
        == 'neutral'
    )
    assert service.read()['import']['other_preset'] == 'vivid'
    assert events
    assert SettingsService(service.path, migrate=False).read()['appearance']['language'] == 'en'
    before = service.path.read_bytes()
    with pytest.raises(ValueError):
        service.update({'performance': {'render_memory_mib': float('nan')}})
    assert service.path.read_bytes() == before
    with pytest.raises(ValueError):
        service.read(project={'appearance': {'language': 'zh'}})


def test_share_settings_has_no_nested_credentials_or_prompts(tmp_path, monkeypatch):
    from ccraw.settings import SettingsService

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path))
    service = SettingsService(tmp_path / 'settings.json', migrate=False)
    from ccraw import nl_edit, image_generation

    model = nl_edit.default_settings()
    model['providers']['openai']['key'] = 'SECRET_API_KEY_123'
    nl_edit.save_settings(model)
    image_generation.save_settings(dict(image_generation.default_settings(), key='SECRET_OTHER'))
    text = json.dumps(service.share())
    assert 'SECRET' not in text and 'dpapi:' not in text
    assert 'provider' in text


def test_cache_cleanup_waits_for_tasks_and_keeps_sources_proxies_and_leases(tmp_path):
    from ccraw.settings import CacheManager

    root = tmp_path / 'cache'
    for name in ('thumbnails', 'previews', 'proxies', 'models'):
        (root / name).mkdir(parents=True)
        (root / name / 'a.dat').write_bytes(b'1234')
    original = tmp_path / 'original.raw'
    original.write_bytes(b'original')
    busy = [True]
    manager = CacheManager(root, busy=lambda: busy[0])
    manager.protect(root / 'previews' / 'a.dat')
    result = manager.clear()
    assert result['deferred']
    assert (root / 'thumbnails' / 'a.dat').exists()
    busy[0] = False
    manager.drain()
    assert not (root / 'thumbnails' / 'a.dat').exists()
    assert (root / 'previews' / 'a.dat').exists()
    assert (root / 'proxies' / 'a.dat').exists() and (root / 'models' / 'a.dat').exists()
    assert original.read_bytes() == b'original'


def test_deferred_cache_budget_keeps_the_requested_budget(tmp_path):
    from ccraw.settings import CacheManager

    folder = tmp_path / 'thumbnails'
    folder.mkdir()
    for index in range(3):
        (folder / f'{index}.dat').write_bytes(b'1234')
    busy = [True]
    cache = CacheManager(tmp_path, busy=lambda: busy[0])
    cache.clear(budget_bytes=8)
    busy[0] = False
    cache.drain()
    assert sum(path.stat().st_size for path in folder.iterdir()) == 8


def test_recovery_restore_validates_recipe_and_preserves_original(tmp_path, app, monkeypatch):
    from ccraw.settings import SettingsService
    from ccraw.ui.settings_dialog import install_settings
    from ccraw import model

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'user'))
    source = tmp_path / 'source.raw'
    source.write_bytes(b'unchanged raw')

    class Editor(QMainWindow):
        def __init__(self):
            super().__init__()
            self.documents = {}
            self.source = None
            self.source_path = ''

        def add_documents(self, paths):
            for path in paths:
                self.documents[path] = {}

        def open_path(self, path):
            self.opened = path

    editor = Editor()
    binding = install_settings(
        editor, service=SettingsService(tmp_path / 'prefs.json', migrate=False)
    )
    recipe = model.recipe()
    recipe['adjustments']['exposure'] = 1.25
    binding.restore_recovery(
        {'source_path': str(source), 'project_path': '', 'recipe': recipe, 'snapshots': []}
    )
    assert editor.opened == str(source.resolve())
    assert editor.documents[editor.opened]['initialized']
    assert editor.documents[editor.opened]['edits']['adjustments']['exposure'] == 1.25
    assert source.read_bytes() == b'unchanged raw'
    with pytest.raises(ValueError):
        binding.restore_recovery({'source_path': str(source), 'recipe': {'bad': object()}})
    with pytest.raises(ValueError):
        binding.restore_recovery(
            {'source_path': str(source), 'project_path': str(source), 'recipe': recipe}
        )
    editor.close()


def test_import_defaults_only_first_import_and_exact_camera_matching(tmp_path):
    from ccraw.settings import SettingsService, apply_import_defaults, import_preview
    from ccraw import model

    service = SettingsService(tmp_path / 'settings.json', migrate=False)
    service.update(
        {
            'import': {
                'raw_preset': 'neutral',
                'other_preset': 'vivid',
                'camera_rules': [{'camera': 'Exact Camera', 'preset': 'vivid'}],
                'metadata': {'keywords': ['Studio']},
            }
        }
    )
    defaults = service.read()['import']
    result = apply_import_defaults(model.recipe(), {'raw': False}, defaults)
    assert result['adjustments']['vibrance'] > 0
    preview = import_preview(model.recipe(), {'raw': False}, defaults)
    assert preview['preset'] == 'vivid'
    assert preview['metadata']['keywords'] == ['Studio']
    assert apply_import_defaults(result, {'raw': True}, defaults, initialized=True) == result
    unknown = import_preview(model.recipe(), {'raw': True, 'camera_model': 'Unknown'}, defaults)
    assert unknown['preset'] == 'neutral' and not unknown['camera_rule']


def test_recovery_revision_merge_failure_and_retry(tmp_path, app):
    from ccraw.recovery import RecoveryStore, AutosaveController

    store = RecoveryStore(tmp_path / 'recovery')
    state = {'value': 0}
    saved = []

    def write(payload):
        if payload['value'] == 3:
            raise OSError('disk full')
        saved.append(payload['value'])
        store.save('session', payload)

    control = AutosaveController(lambda: dict(state), write, debounce_ms=20)
    state['value'] = 1
    control.mark_dirty()
    state['value'] = 2
    control.mark_dirty()
    assert control.flush()
    assert saved == [2] and control.saved_revision == control.revision
    state['value'] = 3
    control.mark_dirty()
    assert not control.flush()
    assert control.status == 'failed' and control.saved_revision < control.revision
    state['value'] = 4
    control.mark_dirty()
    assert control.flush()
    assert store.load('session')['value'] == 4


def test_sqlite_backup_is_consistent_and_retention_is_bounded(tmp_path):
    from ccraw.recovery import backup_database

    source = tmp_path / 'project.sqlite3'
    with sqlite3.connect(source) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE example (value TEXT)')
        db.execute("INSERT INTO example VALUES ('kept')")
    folder = tmp_path / 'backups'
    for _ in range(4):
        target = backup_database(source, folder, keep=2)
    assert len(list(folder.glob('*.sqlite3'))) == 2
    with sqlite3.connect(target) as db:
        assert db.execute('SELECT value FROM example').fetchone()[0] == 'kept'


def test_live_language_changes_preserve_combobox_ids_and_dynamic_counts(app):
    from ccraw.i18n import translate, localize_window
    from PySide6.QtWidgets import QComboBox

    window = QMainWindow()
    body = QWidget()
    layout = QVBoxLayout(body)
    button = QPushButton('保存工程')
    label = QLabel('选片集 · 12 张 / 已选 3 张')
    combo = QComboBox()
    combo.addItem('原片', 'stable-source')
    for widget in (button, label, combo):
        layout.addWidget(widget)
    window.setCentralWidget(body)
    localize_window(window, 'en')
    assert button.text() == 'Save project'
    assert '12' in label.text() and '3' in label.text() and '张' not in label.text()
    assert combo.currentData() == 'stable-source'
    localize_window(window, 'zh')
    assert button.text() == '保存工程'
    assert translate('保存失败：disk full', 'en').startswith('Save failed:')
    window.deleteLater()


def test_unified_dialog_is_singleton_and_window_geometry_is_bounded(tmp_path, app):
    from ccraw.settings import SettingsService
    from ccraw.ui.settings_dialog import install_settings, show_settings, clamp_window
    from PySide6.QtCore import QRect

    service = SettingsService(tmp_path / 'settings.json', migrate=False)
    a, b = QMainWindow(), QMainWindow()
    for window in (a, b):
        window.setCentralWidget(QWidget())
        install_settings(window, service=service)
    first = show_settings(a, 'appearance', service=service)
    second = show_settings(b, 'performance', service=service)
    assert first is second
    assert first.tabs.currentWidget().property('settings_page') == 'performance'
    a.resize(1600, 1040)
    clamp_window(a, available=QRect(0, 0, 1280, 680))
    assert a.width() <= 1280 and a.height() <= 680
    assert a.minimumHeight() <= 600
    first.close()
    a.deleteLater()
    b.deleteLater()


def test_show_does_not_override_explicit_resize(tmp_path, app):
    from ccraw.settings import SettingsService
    from ccraw.ui.settings_dialog import install_settings

    window = QMainWindow()
    install_settings(window, service=SettingsService(tmp_path / 'settings.json', migrate=False))
    window.show()
    window.resize(1180, 780)
    app.processEvents()
    assert window.size().width() == 1180 and window.size().height() == 780
    window.close()


def test_repeated_theme_broadcast_applies_stylesheet_once(app, monkeypatch):
    from ccraw.ui import theme

    theme.apply_theme(app, 'light')
    applied = []
    original = app.setStyleSheet

    def set_style(value):
        applied.append(value)
        original(value)

    monkeypatch.setattr(app, 'setStyleSheet', set_style)
    theme.apply_theme(app, 'dark')
    button = theme.AppearanceButton(None)
    theme.apply_theme(app, 'dark')
    monkeypatch.setattr(theme, 'system_appearance', lambda: 'dark')
    theme.apply_theme(app, 'system')
    assert len(applied) == 1
    assert button.appearance_menu.mode_actions['system'].isChecked()
    assert app.property('appearanceMode') == 'system'
    # An external stylesheet change still requires reconciliation.
    original(theme.STYLE)
    theme.apply_theme(app, 'dark')
    assert len(applied) == 2
    button.deleteLater()


def test_workspace_restores_tabs_sidebar_splitter_and_stable_sort(tmp_path, app):
    from ccraw.settings import SettingsService
    from ccraw.ui.settings_dialog import install_settings
    from PySide6.QtWidgets import QSplitter, QTabWidget, QCheckBox, QComboBox
    from PySide6.QtCore import Qt

    service = SettingsService(tmp_path / 'settings.json', migrate=False)

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            body = QWidget()
            layout = QVBoxLayout(body)
            self.sidebar_button = QCheckBox('Sidebar')
            self.sidebar_button.setChecked(True)
            layout.addWidget(self.sidebar_button)
            self.sort_combo = QComboBox()
            self.sort_combo.addItem('Capture', 'capture')
            self.sort_combo.addItem('Rating', 'rating')
            layout.addWidget(self.sort_combo)
            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.setObjectName('main-panels')
            self.tabs = QTabWidget()
            self.tabs.addTab(QWidget(), 'A')
            self.tabs.addTab(QWidget(), 'B')
            splitter.addWidget(self.tabs)
            splitter.addWidget(QWidget())
            layout.addWidget(splitter)
            self.setCentralWidget(body)
            self.splitter = splitter

    first = Window()
    binding = install_settings(first, service=service)
    first.show()
    app.processEvents()
    first.tabs.setCurrentIndex(1)
    first.sidebar_button.setChecked(False)
    first.sort_combo.setCurrentIndex(1)
    first.splitter.setSizes([250, 450])
    assert binding.save_workspace()
    second = Window()
    other = install_settings(second, service=service)
    assert second.tabs.currentIndex() == 1
    assert not second.sidebar_button.isChecked()
    assert second.sort_combo.currentData() == 'rating'
    assert second.splitter.sizes()[1] > second.splitter.sizes()[0]
    other.reset_workspace()
    assert second.tabs.currentIndex() == 0 and second.sidebar_button.isChecked()
    assert second.sort_combo.currentData() == 'capture'
    first.close()
    second.close()


def test_workspace_store_bounds_and_offscreen_recovery(tmp_path, app):
    from ccraw.settings import WorkspaceStore, SettingsService
    from ccraw.ui.settings_dialog import install_settings

    store = WorkspaceStore(tmp_path / 'workspace.json')
    store.save(
        'QMainWindow',
        {
            'geometry': [50000, 50000, 1500, 1000],
            'tabs': {},
            'splitters': {},
            'sidebar': True,
            'view': {},
        },
    )
    with pytest.raises(ValueError):
        store.save('QMainWindow', {'geometry': [0, 0, float('nan'), 700]})
    window = QMainWindow()
    install_settings(window, service=SettingsService(tmp_path / 'settings.json', migrate=False))
    available = window.screen().availableGeometry()
    assert available.contains(window.geometry().topLeft())
    assert window.width() <= available.width() and window.height() <= available.height()
    window.close()


def test_system_theme_and_legacy_appearance_share_service(tmp_path, app, monkeypatch):
    from ccraw.ui import theme
    from ccraw.settings import SettingsService

    service = SettingsService(tmp_path / 'settings.json', migrate=False)
    monkeypatch.setattr('ccraw.settings.get_service', lambda: service)
    monkeypatch.setattr(theme, 'system_appearance', lambda: 'dark')
    theme.set_appearance('system')
    assert service.read()['appearance']['theme'] == 'system'
    assert theme.saved_appearance() == 'system'
    assert theme.tokens() == theme.DARK
    assert app.property('appearanceResolved') == 'dark'
    monkeypatch.setattr(theme, 'system_appearance', lambda: 'light')
    theme.refresh_system_theme(app)
    assert theme.tokens() == theme.LIGHT
    theme.set_appearance('light')


def test_settings_singleton_rebinds_project_scope(tmp_path, app):
    from ccraw.settings import SettingsService
    from ccraw.ui.settings_dialog import show_settings

    class Project:
        def preferences(self):
            return {}

    service = SettingsService(tmp_path / 'settings.json', migrate=False)
    launcher, agent = QMainWindow(), QMainWindow()
    first = show_settings(launcher, service=service)
    project = Project()
    second = show_settings(agent, service=service, project_getter=lambda: project)
    assert second is first
    assert second.project_getter() is project
    assert second.scope.findData('project') >= 0
    assert second.owner() is agent
    second.close()
    launcher.deleteLater()
    agent.deleteLater()


def test_project_settings_dialog_writes_only_project_scopes(tmp_path, app):
    from ccraw.settings import SettingsService
    from ccraw.ui.settings_dialog import show_settings

    class Project:
        def __init__(self):
            self.values = {}

        def preferences(self):
            return self.values

        def preference(self, key, value):
            self.values[key] = value

    project = Project()
    service = SettingsService(tmp_path / 'settings.json', migrate=False)
    owner = QMainWindow()
    dialog = show_settings(owner, service=service, project_getter=lambda: project)
    dialog.scope.setCurrentIndex(dialog.scope.findData('project'))
    proxy = dialog.fields['project.proxy_policy']
    proxy.setCurrentIndex(proxy.findData('proxy'))
    assert dialog.apply()
    assert service.project_overrides(project)['project']['proxy_policy'] == 'proxy'
    assert service.read()['project']['proxy_policy'] == 'auto'
    assert not dialog.fields['appearance.language'].isEnabled()
    dialog.close()
    owner.close()


def test_local_model_download_checks_hash_and_keeps_unsaved_preferences(tmp_path, app, monkeypatch):
    import hashlib
    from ccraw.settings import SettingsService
    from ccraw.ui.settings_dialog import show_settings
    from ccraw.photo_agent import models
    from test_ui import wait_until

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'user'))
    data = b'model bytes'
    monkeypatch.setattr(
        models,
        'MANIFEST',
        {
            'files': [
                {
                    'name': 'tiny.onnx',
                    'bytes': len(data),
                    'sha256': hashlib.sha256(data).hexdigest(),
                }
            ]
        },
    )

    def install(cancel, progress):
        models.model_root().mkdir(parents=True, exist_ok=True)
        (models.model_root() / 'tiny.onnx').write_bytes(data)
        progress(100, 'tiny.onnx')

    monkeypatch.setattr(models, 'install_models', install)
    owner = QMainWindow()
    dialog = show_settings(
        owner, 'local_models', service=SettingsService(tmp_path / 'settings.json', migrate=False)
    )
    language = dialog.fields['appearance.language']
    language.setCurrentIndex(language.findData('en'))
    dialog.install_models()
    wait_until(lambda: dialog._model_future.done() and '已安装' in dialog.local_models.text())
    assert language.currentData() == 'en'
    dialog.close()
    owner.close()


def test_deferred_backend_uses_latest_preferences_and_display_clears_tiles(
    tmp_path, app, monkeypatch
):
    from ccraw.settings import SettingsService
    from ccraw.ui.settings_dialog import install_settings
    from ccraw.scheduler import WorkState, Activity
    from types import SimpleNamespace

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'user'))
    service = SettingsService(tmp_path / 'settings.json', migrate=False)
    window = QMainWindow()
    window.work = WorkState(window)
    window.source = object()
    window.rendered = None
    window.tile_clears = 0

    def clear():
        window.tile_clears += 1

    window.tiles = SimpleNamespace(clear=clear)
    window.changed = lambda: None
    binding = install_settings(window, service=service)
    window.work.begin(Activity.EXPORTING)
    service.update({'performance': {'backend': 'cpu'}})
    service.update({'performance': {'backend': 'auto'}})
    window.work.end(Activity.EXPORTING)
    binding.poll()
    assert binding._last_backend == 'auto'
    before = window.tile_clears
    service.update({'display': {'bpc': False}})
    assert window.tile_clears > before
    window.close()


def test_native_editor_autosaves_and_restores_without_exporting(window, tmp_path):
    import copy
    from pathlib import Path
    from test_ui import wait_until
    from ccraw.app import MainWindow

    before = Path(window.source_path).read_bytes()
    window.edits['adjustments']['exposure'] = 0.75
    window.changed()
    binding = window._settings_binding
    assert binding.flush()
    recovered = binding.store.load(binding.session)
    assert recovered['recipe']['adjustments']['exposure'] == 0.75
    other = MainWindow()
    try:
        other._settings_binding.restore_recovery(recovered)
        wait_until(lambda: other.rendered is not None and not other.render_running)
        assert other.edits['adjustments']['exposure'] == 0.75
        assert Path(window.source_path).read_bytes() == before
        assert not list(tmp_path.glob('*.tiff')) and not list(tmp_path.glob('*.jpg'))
    finally:
        other.saved_edits = copy.deepcopy(other.edits)
        other.saved_snapshots = copy.deepcopy(other.snapshots)
        wait_until(lambda: not other.loading and not other.render_running)
        other.close()


def test_live_language_retains_template_filter_business_category(app, tmp_path):
    from ccraw.generation_panel import TemplateBrowser
    from ccraw import i18n
    from types import SimpleNamespace
    from PySide6.QtWidgets import QComboBox

    panel = QWidget()
    panel.store = SimpleNamespace(root=tmp_path)
    panel.mode = QComboBox()
    panel.mode.addItem('Creative', 'creative')
    browser = TemplateBrowser(panel)
    original = browser.items.count()
    assert original > 0
    i18n.localize_window(browser, 'en')
    browser.filter()
    assert browser.items.count() == original
    browser.deleteLater()


def test_three_window_english_labels_have_catalog_coverage(tmp_path, app, monkeypatch):
    import re
    from ccraw import i18n
    from ccraw.app import MainWindow
    from ccraw.photo_agent.launcher import Launcher
    from ccraw.photo_agent.ui import AgentWindow
    from ccraw.photo_agent.store import Project
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QTabWidget, QLineEdit
    from ccraw.ui.settings_dialog import show_settings

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'user'))
    monkeypatch.setattr(MainWindow, 'job', lambda *_args, **_kwargs: None)
    windows = [
        Launcher(),
        MainWindow(),
        AgentWindow(Project.create(tmp_path / 'p.ccrawagent', '用户工程名')),
    ]
    windows.append(show_settings(windows[0]))
    unknown = set()
    for window in windows:
        i18n.localize_window(window, 'en')
        for widget in window.findChildren(QWidget):
            if isinstance(widget, (QLabel, QAbstractButton)) and not widget.property(
                'user_content'
            ):
                if re.search('[\u4e00-\u9fff]', widget.text()):
                    unknown.add(widget.property('_i18n_source_text') or widget.text())
            if (
                isinstance(widget, QComboBox)
                and not widget.isEditable()
                and not widget.property('user_content')
            ):
                for index in range(widget.count()):
                    if re.search('[\u4e00-\u9fff]', widget.itemText(index)):
                        unknown.add(
                            widget.property(f'_i18n_source_item{index}') or widget.itemText(index)
                        )
            if isinstance(widget, QTabWidget):
                for index in range(widget.count()):
                    if re.search('[\u4e00-\u9fff]', widget.tabText(index)):
                        unknown.add(
                            widget.property(f'_i18n_source_tab{index}') or widget.tabText(index)
                        )
            if isinstance(widget, QLineEdit) and re.search(
                '[\u4e00-\u9fff]', widget.placeholderText()
            ):
                unknown.add(widget.property('_i18n_source_placeholder') or widget.placeholderText())
        window.close()
    assert not unknown, '\n'.join(sorted(unknown))
