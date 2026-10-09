"""Gestures interrupted by panels and presentation changes must recover."""

import copy
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from test_ui import wait_until, open_detail_photo, save_documents, tile_pixels
from ccraw import engine
import numpy as np


@pytest.mark.parametrize('name,tab', [('curve', 1), ('exposure_curve', 0), ('wheel', 6)])
def test_tab_switch_closes_a_held_custom_gesture_and_restores_undo(window, name, tab):
    w = window
    w.tabs.setCurrentIndex(tab)
    wait_until(lambda: not w.jobs and not w.timer.isActive())
    before = copy.deepcopy(w.edits)
    widget = w.wheels['shadows'] if name == 'wheel' else getattr(w, name)
    if name == 'wheel':
        center, radius = widget.center_radius()
        start = center.toPoint() + QPoint(round(radius * 0.4), 0)
    else:
        start = (
            widget.screen(0.5, 0.5).toPoint()
            if name == 'exposure_curve'
            else widget.screen([0.5, 0.5]).toPoint()
        )
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(widget, start + QPoint(0, -20))
    assert w._preview_dragging == 1
    w.tabs.setCurrentIndex(7)
    assert w._preview_dragging == 0
    QTest.mouseRelease(w.tabs.currentWidget(), Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    wait_until(lambda: not w.jobs and not w.timer.isActive())
    w.undo(-1)
    assert w.edits == before


def test_paused_original_pixel_drag_refreshes_split_and_compare_without_another_move(
    window, tmp_path
):
    w = window
    open_detail_photo(w, tmp_path)
    slider = w.controls['exposure'].slider
    slider.setSliderDown(True)
    slider.setValue(50)
    wait_until(lambda: w.detail_ready() and not w.jobs and not w.timer.isActive(), timeout=30)
    w.split_check.setChecked(True)
    wait_until(lambda: w.detail_ready() and not w.jobs and not w.timer.isActive(), timeout=30)
    assert slider.isSliderDown() and w.canvas.detail.before
    w.show_original(True)
    wait_until(lambda: w.detail_ready() and not w.jobs and not w.timer.isActive(), timeout=30)
    x0, y0, x1, y1 = w.live_detail_rect(w.detail_view())
    tile = next(iter(w.canvas.detail.main.values()))
    expected = engine.render_display(
        w.full_source, w.edits, w.backend, (x0, y0, x1, y1), kind='original'
    )
    np.testing.assert_array_equal(
        tile_pixels(tile), np.clip(expected * 255, 0, 255).astype(np.uint8)
    )
    w.show_original(False)
    slider.setSliderDown(False)
    wait_until(lambda: not w.jobs and not w.timer.isActive(), timeout=30)
    save_documents(w, tmp_path)
