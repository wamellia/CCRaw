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
