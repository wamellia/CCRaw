"""Regression coverage for clipped icons and directly accessible editing tools."""

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtTest import QTest
from ccraw.ui.icons import icon, PATHS
from ccraw.ui.theme import apply_theme, LIGHT, DARK
from PySide6.QtGui import QColor


@pytest.mark.parametrize('ratio', [1.0, 1.25, 1.5, 2.0, 3.0])
@pytest.mark.parametrize('name', list(PATHS))
def test_icon_has_clear_padding_on_every_edge(app, name, ratio):
    image = icon(name).pixmap(QSize(22, 22), ratio).toImage()
    assert not image.isNull()
    ink = [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 32
    ]
    assert ink
    xs, ys = zip(*ink)
    assert min(xs) > 0 and min(ys) > 0
    assert max(xs) < image.width() - 1 and max(ys) < image.height() - 1
    assert max(xs) - min(xs) >= image.width() * 0.45
    assert max(ys) - min(ys) >= image.height() * 0.45


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_tools_are_visible_and_clickable_at_minimum_window_size(window, app, mode):
    w = window
    try:
        apply_theme(app, mode)
        w.resize(1180, 780)
        app.processEvents()
        assert len(w.tool_buttons) == w.tabs.count() == 9
        for index, button in enumerate(w.tool_buttons):
            assert button.isVisible() and button.text()
            assert button.width() >= button.minimumSizeHint().width()
            assert button.height() >= 36
            assert not button.icon().isNull()
            QTest.mouseClick(button, Qt.LeftButton)
            assert w.tabs.currentIndex() == index
            assert button.isChecked()
            assert sum(b.isChecked() for b in w.tool_buttons) == 1
            w.tool_select.setCurrentIndex((index + 1) % 9)
            assert w.tool_buttons[(index + 1) % 9].isChecked()
        for button in (
            w.open_button,
            w.save_button,
            w.export_button,
            w.sidebar_button,
            w.undo_button,
            w.redo_button,
        ):
            assert button.text() and button.height() >= 36
            assert button.width() >= button.minimumSizeHint().width()
        w.tabs.setCurrentIndex(0)
    finally:
        apply_theme(app, 'light')


def test_every_tool_symbol_has_a_nonempty_render(app):
    for name in PATHS:
        assert not icon(name).pixmap(QSize(22, 22)).isNull()


def test_compact_inspector_keeps_tools_and_histogram_available(window, app):
    w = window
    w.resize(1180, 780)
    app.processEvents()
    assert not w.histogram_panel.isVisible()
    assert w.tabs.height() >= 235
    QTest.mouseClick(w.histogram_button, Qt.LeftButton)
    assert w.histogram_panel.isVisible()
    QTest.mouseClick(w.histogram_button, Qt.LeftButton)
    assert not w.histogram_panel.isVisible()
    w.resize(1600, 1040)
    app.processEvents()
    assert w.histogram_panel.isVisible()


def test_edit_history_buttons_follow_pending_edit_undo_and_redo(window):
    w = window
    assert not w.undo_button.isEnabled() and not w.redo_button.isEnabled()
    w.controls['exposure'].spin.setValue(0.5)
    assert w.undo_button.isEnabled() and not w.redo_button.isEnabled()
    QTest.mouseClick(w.undo_button, Qt.LeftButton)
    assert w.edits['adjustments']['exposure'] == 0
    assert not w.undo_button.isEnabled() and w.redo_button.isEnabled()
    QTest.mouseClick(w.redo_button, Qt.LeftButton)
    assert w.edits['adjustments']['exposure'] == 0.5
    assert w.undo_button.isEnabled() and not w.redo_button.isEnabled()


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_color_swatches_keep_their_full_click_area_after_theme_change(window, app, mode):
    w = window
    try:
        apply_theme(app, mode)
        w.resize(1180, 780)
        w.tabs.setCurrentIndex(1)
        w.tabs.widget(1).ensureWidgetVisible(w.color_select)
        app.processEvents()
        for index, button in enumerate(w.swatch_buttons):
            assert button.width() == button.height() == 36
            QTest.mouseClick(button, Qt.LeftButton)
            assert w.color_select.currentIndex() == index
            assert button.isChecked()
            QTest.mouseClick(button, Qt.LeftButton)
            assert button.isChecked() and w.swatch_group.checkedId() == index
            assert sum(b.isChecked() for b in w.swatch_buttons) == 1
            button.clearFocus()
            app.processEvents()
            image = button.grab().toImage()
            ratio = image.devicePixelRatio()
            assert image.pixelColor(round(2 * ratio), round(18 * ratio)) == QColor(
                (DARK if mode == 'dark' else LIGHT).text
            )
    finally:
        apply_theme(app, 'light')
