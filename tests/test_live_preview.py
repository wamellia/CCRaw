"""Continuous drag feedback, bounded work, and exact refinement after release."""

import copy
import time
import threading

import numpy as np
from PySide6.QtCore import QEventLoop, QTimer

from test_ui import app, window, wait_until  # noqa: F401
from ccraw import engine


def drag_for(window, slider, values, interval=12, pause_at=None):
    """Drive slider-down signals in a real Qt event loop, without releasing per step."""
    loop = QEventLoop()
    tick = QTimer()
    tick.setInterval(interval)
    heartbeat = QTimer()
    heartbeat.setInterval(10)
    beats, frames, queues = [], [], []
    original = window.canvas.set_image
    started = time.perf_counter()

    def shown(image):
        original(image)
        frames.append(
            (
                time.perf_counter() - started,
                window.canvas.image.cacheKey(),
                float(window.rendered.mean()),
            )
        )

    window.canvas.set_image = shown
    heartbeat.timeout.connect(lambda: beats.append(time.perf_counter()))
    remaining = iter(values)
    step = 0

    def move():
        nonlocal step
        value = next(remaining, None)
        if value is None:
            tick.stop()
            loop.quit()
            return
        slider.setValue(value)
        queues.append(len(window.scheduler.queue))
        step += 1
        if pause_at == step:
            tick.stop()
            QTimer.singleShot(700, tick.start)

    slider.setSliderDown(True)
    tick.timeout.connect(move)
    heartbeat.start()
    tick.start()
    loop.exec()
    heartbeat.stop()
    held_frames = list(frames)
    slider.setSliderDown(False)
    window.canvas.set_image = original
    wait_until(
        lambda: not window.timer.isActive() and not window.render_running and not window.jobs
    )
    return held_frames, beats, queues


def test_image_changes_repeatedly_before_slider_release(window):
    w = window
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    frames, _, _ = drag_for(w, w.controls['exposure'].slider, range(1, 81))
    assert len(frames) >= 5, 'continuous input must not postpone all preview frames until release'
    assert len({key for _, key, _ in frames}) >= 5
    assert np.ptp([value for _, _, value in frames]) > 0.01, (
        'actual pixels must change during the drag'
    )
    assert frames[0][0] < 0.4
    expected = engine.process(
        w.source,
        w.edits,
        apply_crop=False,
        detail_scale=max(0.1, w.source.shape[1] / w.info['width']),
    )
    np.testing.assert_allclose(w.rendered, expected, atol=1e-5)


def test_slow_render_still_presents_frames_and_does_not_queue_every_change(window, monkeypatch):
    w = window
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    original = engine.process

    def slow(*args, **kwargs):
        time.sleep(0.075)
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, 'process', slow)
    frames, _, queues = drag_for(w, w.controls['contrast'].slider, range(1, 76), interval=16)
    assert len(frames) >= 3, 'completed interactive frames must not all be discarded by newer input'
    assert max(queues, default=0) <= 1, (
        'slider events must coalesce rather than build a frame backlog'
    )
    expected = original(
        w.source,
        w.edits,
        apply_crop=False,
        detail_scale=max(0.1, w.source.shape[1] / w.info['width']),
    )
    np.testing.assert_allclose(w.rendered, expected, atol=1e-5)


def test_one_held_drag_is_one_undo_even_with_a_long_pause(window):
    w = window
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    before = copy.deepcopy(w.edits)
    drag_for(w, w.controls['saturation'].slider, range(1, 25), pause_at=12)
    w.undo(-1)
    assert w.edits['adjustments'] == before['adjustments']


def test_preview_image_preparation_stays_off_the_gui_thread(window, monkeypatch):
    from ccraw import live_preview

    w = window
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    gui_thread = threading.get_ident()
    threads = []
    original = live_preview.qimage

    def tracked(rgb):
        threads.append(threading.get_ident())
        return original(rgb)

    monkeypatch.setattr(live_preview, 'qimage', tracked)
    drag_for(w, w.controls['exposure'].slider, range(1, 40))
    assert threads and all(t != gui_thread for t in threads)


def test_local_mask_and_spatial_edit_refine_to_the_same_full_preview(window):
    w = window
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    w.controls['clarity'].spin.setValue(25)
    w.tabs.setCurrentIndex(4)
    w.add_mask('radial')
    w.overlay_check.setChecked(True)
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    frames, _, _ = drag_for(w, w.local_controls['exposure'].slider, range(1, 55))
    assert len(frames) >= 3
    expected = engine.process(
        w.source,
        w.edits,
        apply_crop=False,
        detail_scale=max(0.1, w.source.shape[1] / w.info['width']),
    )
    np.testing.assert_allclose(w.rendered, expected, atol=1e-5)
    assert w.canvas.overlay is not None
    assert w.canvas.overlay.size() == w.canvas.image.size()


def test_local_drag_never_falls_back_to_gui_image_conversion_for_newer_values(window, monkeypatch):
    from PySide6.QtGui import QImage

    w = window
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    w.tabs.setCurrentIndex(4)
    w.add_mask('radial')
    w.overlay_check.setChecked(True)
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    process = engine.process
    shown = w.canvas.set_image
    held_inputs = []
    slider = w.local_controls['exposure'].slider

    def slow(*args, **kwargs):
        time.sleep(0.035)
        return process(*args, **kwargs)

    def track(image):
        if slider.isSliderDown():
            held_inputs.append(image)
        shown(image)

    monkeypatch.setattr(engine, 'process', slow)
    monkeypatch.setattr(w.canvas, 'set_image', track)
    drag_for(w, slider, range(1, 50))
    assert len(held_inputs) >= 3
    assert all(isinstance(image, QImage) for image in held_inputs), (
        'newer mask values must not invalidate prepared UI data'
    )


def test_real_qt_mouse_drag_changes_pixels_before_mouse_release(window):
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QStyle, QStyleOptionSlider

    w = window
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    slider = w.controls['exposure'].slider
    option = QStyleOptionSlider()
    slider.initStyleOption(option)
    start = (
        slider.style()
        .subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, slider
        )
        .center()
    )
    frames = []
    original = w.canvas.set_image

    def shown(image):
        original(image)
        if slider.isSliderDown():
            frames.append(float(w.rendered.mean()))

    w.canvas.set_image = shown
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=start)
    assert slider.isSliderDown()
    loop, timer = QEventLoop(), QTimer()
    timer.setInterval(16)
    step = 0
    end = start

    def move():
        nonlocal step, end
        step += 1
        end = QPoint(min(slider.width() - 4, start.x() + step * 2), start.y())
        QTest.mouseMove(slider, end)
        if step == 35:
            timer.stop()
            loop.quit()

    timer.timeout.connect(move)
    timer.start()
    loop.exec()
    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=end)
    w.canvas.set_image = original
    wait_until(lambda: not w.timer.isActive() and not w.jobs)
    assert len(frames) >= 3
    assert np.ptp(frames) > 0.01
