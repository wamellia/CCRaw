"""Settings navigation preserves page isolation and application/project persistence."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QMainWindow, QTreeWidget

from ccraw import image_generation, nl_edit
from ccraw.settings import SettingsService
from ccraw.ui.settings_dialog import show_settings


@pytest.fixture
def settings_dialog(tmp_path, app, monkeypatch):
    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'user'))
    service = SettingsService(tmp_path / 'settings.json', migrate=False)
    owner = QMainWindow()
    dialog = show_settings(owner, service=service)
    yield dialog, owner, service
    dialog.close()
    owner.close()


def test_directory_selects_distinct_services_and_voice_controls(settings_dialog):
    dialog, owner, service = settings_dialog
    navigation = dialog.findChild(QTreeWidget, 'settings-navigation')
    assert navigation is not None, 'Settings needs a visible two-level directory'
    for route, leaf in [
        ('models', 'ai'),
        ('generation', 'generation'),
        ('voice', 'voice'),
        ('local_models', 'local_models'),
        ('recovery', 'recovery'),
    ]:
        dialog.navigation_items[leaf].parent().setExpanded(False)
        assert show_settings(owner, route, service=service) is dialog
        page = dialog.tabs.currentWidget()
        assert page.property('settings_page') == leaf
        assert navigation.currentItem().data(0, Qt.ItemDataRole.UserRole) == leaf
        assert navigation.currentItem().parent() is not None
        assert navigation.currentItem().parent().isExpanded()
    assert dialog.pages['voice'][0].isAncestorOf(dialog.ai.voice)
    assert not dialog.pages['ai'][0].isAncestorOf(dialog.ai.voice)
    assert dialog.pages['generation'][0].isAncestorOf(dialog.generation.url)
    assert dialog.pages['local_models'][0].isAncestorOf(dialog.install_models_button)
    assert dialog.pages['maintenance'][0].findChildren(QGroupBox)


def test_project_scope_shows_applicable_pages_and_persists_only_overrides(settings_dialog):
    dialog, owner, service = settings_dialog

    class Project:
        def __init__(self):
            self.values = {}

        def preferences(self):
            return self.values

        def preference(self, key, value):
            self.values[key] = value

    project = Project()
    show_settings(owner, 'appearance', service=service, project_getter=lambda: project)
    dialog.scope.setCurrentIndex(dialog.scope.findData('project'))
    assert dialog.tabs.currentWidget().property('settings_page') in ('import', 'project')
    assert dialog.navigation_items['appearance'].isHidden()
    assert not dialog.navigation_items['import'].isHidden()
    show_settings(owner, 'project', service=service, project_getter=lambda: project)
    proxy = dialog.fields['project.proxy_policy']
    assert dialog.pages['project'][0].isAncestorOf(proxy)
    proxy.setCurrentIndex(proxy.findData('proxy'))
    preset = dialog.fields['import.raw_preset']
    preset.setCurrentIndex(preset.findData('vivid'))
    assert dialog.apply()
    overrides = service.project_overrides(project)
    assert overrides['project']['proxy_policy'] == 'proxy'
    assert overrides['import']['raw_preset'] == 'vivid'
    assert service.read()['project']['proxy_policy'] == 'auto'
    assert service.read()['import']['raw_preset'] == 'none'
    assert not dialog.fields['appearance.language'].isEnabled()
    # A direct model-settings entry must open its requested application page,
    # even if the singleton previously displayed project overrides.
    show_settings(owner, 'models', service=service, project_getter=lambda: project)
    assert dialog.scope.currentData() == 'application'
    assert dialog.tabs.currentWidget().property('settings_page') == 'ai'
    assert not dialog.navigation_items['appearance'].isHidden()


def test_page_reset_and_split_voice_save_do_not_reset_other_preferences(settings_dialog):
    dialog, owner, service = settings_dialog
    service.update(
        {
            'performance': {'backend': 'cpu', 'preview_limit': 4096},
            'recovery': {'backups': 9, 'delay_ms': 900},
        }
    )
    dialog.load()
    show_settings(owner, 'performance', service=service)
    dialog.reset_page()
    assert service.read()['performance']['backend'] == 'auto'
    assert service.read()['performance']['preview_limit'] == 4096
    show_settings(owner, 'autosave', service=service)
    dialog.reset_page()
    assert service.read()['recovery']['delay_ms'] == 500
    assert service.read()['recovery']['backups'] == 9
    dialog.ai.voice.setCurrentIndex(dialog.ai.voice.findData('en'))
    dialog.ai.auto_send.setChecked(False)
    dialog.generation.model.setText('separate-generation-model')
    assert dialog.apply()
    assert nl_edit.load_settings()['voice_language'] == 'en'
    assert nl_edit.load_settings()['auto_send'] is False
    assert image_generation.load_settings()['model'] == 'separate-generation-model'
    show_settings(owner, 'voice', service=service)
    dialog.reset_page()
    assert dialog.generation.model.text() == 'separate-generation-model'
    assert dialog.ai.voice.currentData() == 'auto'
