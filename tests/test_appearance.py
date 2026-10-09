"""Theme propagation, navigation and precise editing behavior."""

import copy
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel
from ccraw.ui.theme import apply_theme, tokens, LIGHT, DARK
from ccraw.enhance_dialog import ExportDialog
from test_ui import wait_until


@pytest.mark.parametrize('mode,expected', [('light', LIGHT), ('dark', DARK)])
def test_theme_reaches_painters_and_dialogs_without_changing_pixels(window, app, mode, expected):
    w = window
    wait_until(lambda: not w.jobs and not w.timer.isActive())
    before = copy.deepcopy(w.edits)
    pixels = w.rendered.copy()
    try:
        apply_theme(app, mode)
        for widget in (w.canvas, w.curve, w.exposure_curve, w.histogram, w.wheels['midtones']):
            assert tokens(widget) == expected
        dialog = ExportDialog(w, w.source_path, False)
        assert tokens(dialog) == expected
        assert not w.grab().isNull()
        assert not dialog.grab().isNull()
        dialog.reject()
        np.testing.assert_array_equal(w.rendered, pixels)
        assert w.edits == before
    finally:
        apply_theme(app, 'light')


def test_all_adjustment_pages_remain_accessible(window):
    w = window
    assert w.tool_select.count() == w.tabs.count() == 9
    for index in range(9):
        w.tool_select.setCurrentIndex(index)
        assert w.tabs.currentIndex() == index
    w.tabs.setCurrentIndex(0)
    assert w.tool_select.currentIndex() == 0
    assert not w.tabs.tabBar().isVisible()
    w.sidebar_button.click()
    assert not w.library.isVisible()
    assert not w.sidebar_action.isChecked()
    w.sidebar_action.trigger()
    assert w.library.isVisible() and w.sidebar_button.isChecked()
    w.sidebar_button.click()
    assert not w.library.isVisible() and not w.sidebar_action.isChecked()
    w.sidebar_action.trigger()
    assert w.library.isVisible()


def test_workspace_has_functional_copy_and_accessible_controls(window):
    w = window
    assert w.windowTitle() == 'CCRaw'
    for label in w.findChildren(QLabel):
        if label.isVisible():
            assert not any(
                term in label.text()
                for term in ('工作室', 'LANDSCAPE', 'TRAVEL COLLECTION', 'STUDIO')
            )
    control = w.controls['exposure']
    assert control.spin.accessibleName() == control.slider.accessibleName() == '曝光 EV'
    control.spin.setValue(0.35)
    wait_until(lambda: not w.timer.isActive() and not w.render_running)
    assert w.edits['adjustments']['exposure'] == pytest.approx(0.35)
    assert control.slider.value() == 7
    assert w.sidebar_button.accessibleName()
    QTest.keyClick(control.slider, Qt.Key_Right)
    assert w.edits['adjustments']['exposure'] == pytest.approx(0.4)


def test_all_workspaces_share_theme_controls_and_persist_selection(
    window, app, tmp_path, monkeypatch
):
    from PySide6.QtCore import QSettings
    from ccraw.ui import theme
    from ccraw.photo_agent.ui import Launcher, AgentWindow
    from ccraw.photo_agent.store import Project

    preferences = tmp_path / 'appearance.ini'
    monkeypatch.setattr(
        theme,
        'appearance_settings',
        lambda: QSettings(str(preferences), QSettings.Format.IniFormat),
        raising=False,
    )
    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'settings'))
    launcher = Launcher()
    agent = AgentWindow(Project.create(tmp_path / 'album.ccrawagent', '相册'))
    fresh = None
    launcher.show()
    agent.show()
    wait_until(lambda: not window.jobs and not window.timer.isActive())
    edits, pixels = copy.deepcopy(window.edits), window.rendered.copy()
    try:
        for owner, mode, expected in [
            (launcher, 'dark', DARK),
            (agent, 'light', LIGHT),
            (window, 'dark', DARK),
        ]:
            assert owner.appearance_button.isVisible()
            owner.appearance_button.appearance_menu.mode_actions[mode].trigger()
            app.processEvents()
            for other in (launcher, agent, window):
                assert tokens(other) == expected
                actions = other.appearance_button.appearance_menu.mode_actions
                assert actions[mode].isChecked()
                assert not actions['light' if mode == 'dark' else 'dark'].isChecked()
            assert window.appearance_group.checkedAction().text() == (
                '深色' if mode == 'dark' else '浅色'
            )
            assert (
                QSettings(str(preferences), QSettings.Format.IniFormat).value('appearance') == mode
            )
        fresh = Launcher()
        assert fresh.appearance_button.appearance_menu.mode_actions['dark'].isChecked()
        # The original editor menu must also update the visible controls everywhere.
        window.appearance_menu.mode_actions['light'].trigger()
        assert all(
            owner.appearance_button.appearance_menu.mode_actions['light'].isChecked()
            for owner in (launcher, agent, window, fresh)
        )
        assert theme.saved_appearance() == 'light'
        np.testing.assert_array_equal(window.rendered, pixels)
        assert window.edits == edits
    finally:
        if fresh:
            fresh.close()
        agent.close()
        launcher.close()
        apply_theme(app, 'light')


def test_saved_appearance_handles_unknown_preference_and_reloads_disk(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from ccraw.ui import theme

    preferences = tmp_path / 'appearance.ini'
    monkeypatch.setattr(
        theme,
        'appearance_settings',
        lambda: QSettings(str(preferences), QSettings.Format.IniFormat),
        raising=False,
    )
    assert theme.saved_appearance() == 'light'
    settings = QSettings(str(preferences), QSettings.Format.IniFormat)
    settings.setValue('appearance', 'dark')
    settings.sync()
    assert theme.saved_appearance() == 'dark'
    settings.setValue('appearance', 'unknown')
    settings.sync()
    assert theme.saved_appearance() == 'light'
