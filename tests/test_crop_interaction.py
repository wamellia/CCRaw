"""Crop movement preserves geometry, boundaries, live feedback and undo."""

import copy
import numpy as np
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest, QSignalSpy
from ccraw.widgets.canvas import Canvas
from test_ui import wait_until


@pytest.fixture
def crop_canvas(app):
    canvas = Canvas()
    canvas.resize(680, 520)
    canvas.set_image(QImage(360, 240, QImage.Format.Format_RGB32))
    canvas.tool = 'crop'
    canvas.crop = [0.2, 0.25, 0.6, 0.7]
    canvas.show()
    app.processEvents()
    yield canvas
    canvas.close()


@pytest.mark.parametrize('delta', [(40, 0), (0, 40), (-35, -30)])
def test_drag_inside_crop_translates_without_resizing(crop_canvas, delta):
    c = crop_canvas
    c.ratio = 1.5  # a ratio lock must affect drawing, never movement
    before = np.array(c.crop)
    a = c.screen([0.32, 0.4]).toPoint()
    b = a + QPoint(*delta)
    geometry = QSignalSpy(c.geometry_changed)
    commits = QSignalSpy(c.committed)
    QTest.mousePress(c, Qt.LeftButton, pos=a)
    QTest.mouseMove(c, b)
    assert geometry.count() > 0
    live = np.array(geometry.at(geometry.count() - 1)[0]).ravel()
    shift = np.array(delta) / [c.image_rect().width(), c.image_rect().height()]
    np.testing.assert_allclose(live, before + np.tile(shift, 2), atol=1e-9)
    np.testing.assert_allclose(c.crop, live)
    assert c.cursor().shape() == Qt.SizeAllCursor
    assert commits.count() == 0
    QTest.mouseRelease(c, Qt.LeftButton, pos=b)
    assert commits.count() == 1
    assert c.start is None


@pytest.mark.parametrize('target,edges', [([0, 0], (0, 0)), ([1, 1], (1, 1))])
def test_crop_stops_at_image_edges_without_shrinking(crop_canvas, target, edges):
    c = crop_canvas
    before = np.array(c.crop)
    QTest.mousePress(c, Qt.LeftButton, pos=c.screen([0.4, 0.475]).toPoint())
    QTest.mouseMove(c, c.screen(target).toPoint())
    result = np.array(c.crop)
    np.testing.assert_allclose(result[2:] - result[:2], before[2:] - before[:2])
    np.testing.assert_allclose(result[:2] if target == [0, 0] else result[2:], edges)
    assert np.min(result) >= 0 and np.max(result) <= 1
    QTest.mouseRelease(c, Qt.LeftButton, pos=c.screen(target).toPoint())


def test_release_position_and_middle_button_do_not_resize_crop(crop_canvas):
    c = crop_canvas
    before = np.array(c.crop)
    a = c.screen([0.4, 0.45]).toPoint()
    QTest.mousePress(c, Qt.LeftButton, pos=a)
    QTest.mouseRelease(c, Qt.LeftButton, pos=a + QPoint(20, 15))
    np.testing.assert_allclose(np.array(c.crop)[2:] - np.array(c.crop)[:2], before[2:] - before[:2])
    assert c.crop != before.tolist()
    moved = copy.deepcopy(c.crop)
    QTest.mousePress(c, Qt.MiddleButton, pos=a)
    QTest.mouseMove(c, a + QPoint(25, 20))
    QTest.mouseRelease(c, Qt.MiddleButton, pos=a + QPoint(25, 20))
    assert c.crop == moved
    assert c.offset.x() == 25 and c.offset.y() == 20


def test_zoomed_panned_crop_uses_image_coordinates_and_returns_from_boundary(crop_canvas):
    c = crop_canvas
    c.zoom = 1.75
    c.offset = QPointF(-30, 20)
    before = np.array(c.crop)
    a = c.screen([0.4, 0.475]).toPoint()
    QTest.mousePress(c, Qt.LeftButton, pos=a)
    QTest.mouseMove(c, c.screen([0.85, 0.85]).toPoint())
    np.testing.assert_allclose(np.array(c.crop)[2:] - np.array(c.crop)[:2], before[2:] - before[:2])
    QTest.mouseMove(c, a)
    np.testing.assert_allclose(c.crop, before)
    QTest.mouseRelease(c, Qt.LeftButton, pos=a)


def test_drag_outside_existing_crop_still_draws_with_locked_ratio(crop_canvas):
    c = crop_canvas
    c.ratio = 1.5
    geometry = QSignalSpy(c.geometry_changed)
    QTest.mousePress(c, Qt.LeftButton, pos=c.screen([0.8, 0.1]).toPoint())
    QTest.mouseMove(c, c.screen([0.65, 0.3]).toPoint())
    assert geometry.count() > 0
    rect = np.array(geometry.at(geometry.count() - 1)[0])
    width, height = abs(rect[1] - rect[0]) * [c.image_rect().width(), c.image_rect().height()]
    assert width / height == pytest.approx(1.5)
    assert c.cursor().shape() == Qt.CrossCursor
    QTest.mouseRelease(c, Qt.LeftButton, pos=c.screen([0.65, 0.3]).toPoint())


def test_other_mouse_buttons_cannot_interrupt_a_crop_move(crop_canvas):
    c = crop_canvas
    a = c.screen([0.4, 0.45]).toPoint()
    commits = QSignalSpy(c.committed)
    QTest.mousePress(c, Qt.LeftButton, pos=a)
    QTest.mousePress(c, Qt.MiddleButton, pos=a)
    QTest.mouseMove(c, a + QPoint(20, 15))
    QTest.mouseRelease(c, Qt.MiddleButton, pos=a + QPoint(20, 15))
    QTest.mouseRelease(c, Qt.LeftButton, pos=a + QPoint(20, 15))
    assert commits.count() == 1 and c.start is None and c.pan is None
    assert c.offset == QPointF()


def test_crop_move_updates_live_without_image_render_and_is_one_undo_step(window):
    w = window
    w.tabs.setCurrentIndex(5)
    w.edits['crop'] = [0.2, 0.2, 0.7, 0.7]
    w.update_display()
    w.commit()
    wait_until(lambda: not w.jobs and not w.timer.isActive())
    before = copy.deepcopy(w.edits)
    pixels = w.rendered.copy()
    generation = w.generation
    index = w.history.index
    a = w.canvas.screen([0.4, 0.4]).toPoint()
    QTest.mousePress(w.canvas, Qt.LeftButton, pos=a)
    for dx in (10, 20, 30):
        QTest.mouseMove(w.canvas, a + QPoint(dx, 15))
    assert w.edits['crop'] != before['crop']
    np.testing.assert_allclose(
        np.array(w.edits['crop'])[2:] - np.array(w.edits['crop'])[:2], [0.5, 0.5]
    )
    assert w.canvas.crop == w.edits['crop']
    assert w.generation == generation and not w.timer.isActive()
    assert w.history.index == index and w._preview_dragging == 1
    np.testing.assert_array_equal(w.rendered, pixels)
    QTest.mouseRelease(w.canvas, Qt.LeftButton, pos=a + QPoint(30, 15))
    assert w._preview_dragging == 0 and w.history.index == index + 1
    moved = copy.deepcopy(w.edits)
    w.undo(-1)
    assert w.edits == before
    w.undo(1)
    assert w.edits == moved
    w.confirm_crop()
    assert w.final_view.isChecked() and w.canvas.tool == 'view'


def test_switching_tools_ends_crop_move_and_keeps_undo_balanced(window):
    w = window
    w.tabs.setCurrentIndex(5)
    w.edits['crop'] = [0.2, 0.2, 0.7, 0.7]
    w.update_display()
    w.commit()
    before = copy.deepcopy(w.edits)
    a = w.canvas.screen([0.4, 0.4]).toPoint()
    QTest.mousePress(w.canvas, Qt.LeftButton, pos=a)
    QTest.mouseMove(w.canvas, a + QPoint(20, 15))
    w.tabs.setCurrentIndex(0)
    assert w._preview_dragging == 0 and w.canvas.start is None
    w.undo(-1)
    assert w.edits == before
