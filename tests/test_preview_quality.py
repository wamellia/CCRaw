"""Gesture coverage and full-detail interactive preview contracts."""

import copy
import numpy as np
import pytest
from PySide6.QtCore import Qt, QPoint, QEventLoop, QTimer
from PySide6.QtTest import QTest
from test_ui import wait_until, open_detail_photo, save_documents
from test_live_preview import drag_for
from ccraw import engine


@pytest.mark.parametrize('name,tab', [('curve', 1), ('exposure_curve', 0), ('wheel', 6)])
def test_curve_and_wheel_are_live_gestures_with_one_undo(window, name, tab):
    w = window
    w.tabs.setCurrentIndex(tab)
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    before = copy.deepcopy(w.edits)
    widget = w.wheels['midtones'] if name == 'wheel' else getattr(w, name)
    if name == 'wheel':
        center, radius = widget.center_radius()
        start = center.toPoint() + QPoint(round(radius * 0.35), 0)
    else:
        start = (
            widget.screen(0.5, 0.5).toPoint()
            if name == 'exposure_curve'
            else widget.screen([0.5, 0.5]).toPoint()
        )
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    assert w._preview_dragging == 1, 'all continuous editors must enter the live render transaction'
    frames = []
    original = w.canvas.set_image

    def shown(image):
        original(image)
        frames.append(w.rendered.copy())

    w.canvas.set_image = shown
    loop, timer = QEventLoop(), QTimer()
    timer.setInterval(20)
    step = 0

    def move():
        nonlocal step
        step += 1
        QTest.mouseMove(widget, start + QPoint(0, -step))
        if step == 30:
            timer.stop()
            loop.quit()

    timer.timeout.connect(move)
    timer.start()
    loop.exec()
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=start + QPoint(0, -30))
    w.canvas.set_image = original
    assert w._preview_dragging == 0
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    assert len(frames) >= 3
    assert np.max(np.abs(frames[0] - frames[-1])) > 0.001
    w.undo(-1)
    assert w.edits == before


def test_drag_never_reduces_the_preview_resolution(window, tmp_path):
    w = window
    open_detail_photo(w, tmp_path)
    w.canvas.fit()
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    shapes = []
    original = w.canvas.set_image

    def shown(image):
        shapes.append(w.rendered.shape)
        original(image)

    w.canvas.set_image = shown
    drag_for(w, w.controls['exposure'].slider, range(1, 50), interval=20)
    w.canvas.set_image = original
    assert shapes and all(shape == w.source.shape for shape in shapes)
    save_documents(w, tmp_path)


def test_zoomed_drag_updates_exact_original_pixels_before_release(window, tmp_path):
    w = window
    open_detail_photo(w, tmp_path)
    slider = w.controls['exposure'].slider
    slider.setSliderDown(True)
    slider.setValue(65)
    wait_until(
        lambda: not w.timer.isActive() and not w.render_running and w.detail_ready(), timeout=30
    )
    assert slider.isSliderDown()
    view = w.detail_view()
    assert view.level == 0 and w.canvas.detail is not None
    expected = engine.process(w.full_source, w.edits, w.backend, apply_crop=False, detail_scale=1.0)
    from test_ui import tile_pixels

    x0, y0, x1, y1 = w.live_detail_rect(view)
    tile = next(iter(w.canvas.detail.main.values()))
    assert (tile.x0, tile.y0, tile.width, tile.height) == (x0, y0, x1 - x0, y1 - y0)
    np.testing.assert_array_equal(
        tile_pixels(tile), np.clip(expected[y0:y1, x0:x1] * 255, 0, 255).astype(np.uint8)
    )
    slider.setSliderDown(False)
    wait_until(lambda: not w.timer.isActive() and not w.jobs, timeout=30)
    save_documents(w, tmp_path)


@pytest.mark.parametrize('kind', ['linear', 'radial', 'brush', 'heal', 'clone'])
def test_canvas_adjustments_update_before_release_and_save_one_stroke(window, kind):
    w = window
    if kind in ('heal', 'clone'):
        w.tabs.setCurrentIndex(8)
        w.retouch_controls['size'].setValue(12)
        w.retouch_tool.setCurrentIndex(1 if kind == 'clone' else 0)
        if kind == 'clone':
            QTest.mouseClick(
                w.canvas,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.AltModifier,
                pos=w.canvas.screen([0.2, 0.2]).toPoint(),
            )
    else:
        w.tabs.setCurrentIndex(4)
        w.add_mask(kind)
        w.local_controls['exposure'].spin.setValue(-1)
        w.overlay_check.setChecked(False)
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    before = copy.deepcopy(w.edits)
    start = w.canvas.screen([0.35, 0.35]).toPoint()
    finish = w.canvas.screen([0.7, 0.7]).toPoint()
    frames = []
    original = w.canvas.set_image

    def shown(image):
        original(image)
        frames.append(w.rendered.copy())

    w.canvas.set_image = shown
    QTest.mousePress(w.canvas, Qt.MouseButton.LeftButton, pos=start)
    assert w._preview_dragging == 1
    loop, timer = QEventLoop(), QTimer()
    timer.setInterval(20)
    step = 0

    def move():
        nonlocal step
        step += 1
        p = start + (finish - start) * step / 30
        QTest.mouseMove(w.canvas, p)
        if step == 30:
            timer.stop()
            loop.quit()

    timer.timeout.connect(move)
    timer.start()
    loop.exec()
    if kind in ('heal', 'clone'):
        assert len(w.edits['retouch']) == 0
    elif kind == 'brush':
        assert len(w.selected_mask()['strokes']) == 0
    assert len(frames) >= 3
    if kind != 'heal':
        assert np.max(np.abs(frames[0] - frames[-1])) > 0.001
    QTest.mouseRelease(w.canvas, Qt.MouseButton.LeftButton, pos=finish)
    w.canvas.set_image = original
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    if kind in ('heal', 'clone'):
        assert len(w.edits['retouch']) == 1
    elif kind == 'brush':
        assert len(w.selected_mask()['strokes']) == 1
    w.undo(-1)
    assert w.edits == before


def test_watermark_image_generation_is_coalesced_in_the_worker(window, monkeypatch):
    import threading
    from ccraw import watermark_dialog

    w = window
    w.tabs.setCurrentIndex(2)
    editor = w.watermark_editor
    wait_until(lambda: not w.jobs and not editor._preview_timer.isActive())
    threads = []
    original = watermark_dialog.qimage

    def tracked(rgb):
        threads.append(threading.get_ident())
        return original(rgb)

    monkeypatch.setattr(watermark_dialog, 'qimage', tracked)
    editor.enabled.setChecked(True)
    for value in range(8, 36):
        editor.size.setValue(value)
    wait_until(
        lambda: not w.jobs and not editor._preview_timer.isActive() and not editor._preview_running
    )
    assert threads and len(threads) <= 3
    assert all(t != threading.get_ident() for t in threads)
    assert editor.preview.pixmap() is not None
    assert w.edits['watermark']['size'] == 35


def test_keyboard_curve_repeats_are_one_live_transaction(window):
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    w = window
    w.tabs.setCurrentIndex(1)
    spin = w.curve_output.lineEdit()
    spin.setFocus()
    wait_until(lambda: not w.jobs and not w.timer.isActive())
    before = copy.deepcopy(w.edits)
    QTest.keyPress(spin, Qt.Key.Key_Up)
    assert w._preview_dragging == 1
    loop, timer = QEventLoop(), QTimer()
    timer.setInterval(20)
    frames = []
    original = w.canvas.set_image

    def shown(image):
        original(image)
        frames.append(w.rendered.copy())

    w.canvas.set_image = shown
    step = 0

    def repeat():
        nonlocal step
        step += 1
        QApplication.sendEvent(
            spin,
            QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier, '', True
            ),
        )
        if step == 25:
            timer.stop()
            loop.quit()

    timer.timeout.connect(repeat)
    timer.start()
    loop.exec()
    assert w._preview_dragging == 1 and len(frames) >= 3
    QTest.keyRelease(spin, Qt.Key.Key_Up)
    w.canvas.set_image = original
    assert w._preview_dragging == 0
    wait_until(lambda: not w.jobs and not w.timer.isActive())
    w.undo(-1)
    assert w.edits == before
