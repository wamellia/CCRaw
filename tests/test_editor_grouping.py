"""Regrouped editor navigation must preserve recipe and canvas behavior."""

import copy

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest

from test_ui import wait_until


def test_six_categories_keep_all_controls_reachable_in_compact_window(window):
    w = window
    assert len(w.tool_buttons) == 6
    assert [button.text() for button in w.tool_buttons] == [
        '光影',
        '色彩',
        '细节',
        '构图',
        '局部',
        '效果',
    ]
    w.resize(1080, 720)
    QApplication.processEvents()
    assert all(button.isVisible() for button in w.tool_buttons)
    for key, control in [
        ('curves', w.curve),
        ('white_balance', w.wb_button),
        ('grading', w.grading_controls[0]),
        ('geometry', w.professional_editor.geometry_grid),
        ('red_eye', w.professional_editor.eye_combo),
        ('watermark', w.watermark_editor),
    ]:
        w.select_editor_section(key)
        QApplication.processEvents()
        assert control.isVisible()
    assert not w.professional_editor.tabs.isVisible()


def test_local_and_composition_sections_route_canvas_without_stale_tools(window):
    w = window
    assert hasattr(w, 'select_editor_section')
    w.select_editor_section('crop')
    assert w.canvas.tool == 'crop'
    w.select_editor_section('geometry')
    assert w.canvas.tool == 'view' and w.canvas.grid_visible
    w.select_editor_section('masks')
    w.add_mask('radial')
    assert w.canvas.tool == 'radial' and not w.canvas.grid_visible
    w.select_editor_section('retouch')
    assert w.canvas.tool == 'heal'
    w.select_editor_section('red_eye')
    w.professional_sampling('eye')
    assert w.canvas.tool == 'color'
    w.select_editor_section('color')
    assert w.canvas.tool == 'view' and w._professional_sample is None
    w.select_editor_section('bw_mixer')
    w.professional_editor.bw_target.setChecked(True)
    assert w.canvas.tool == 'bw-target'
    w.select_editor_section('point_color')
    assert w.canvas.tool == 'view'


def test_section_reset_preserves_other_edits_and_undo(window):
    w = window
    w.controls['exposure'].spin.setValue(1)
    w.controls['saturation'].spin.setValue(25)
    w.commit()
    before = copy.deepcopy(w.edits)
    assert hasattr(w, 'select_editor_section')
    w.select_editor_section('color')
    w.reset_editor_section()
    assert w.edits['adjustments']['saturation'] == 0
    assert w.edits['adjustments']['exposure'] == 1
    w.undo(-1)
    assert w.edits == before
    wait_until(lambda: not w.jobs and not w.timer.isActive())


def test_category_controls_disable_for_ai_while_navigation_remains_available(window):
    from ccraw.scheduler import Activity

    w = window
    assert hasattr(w, 'select_editor_section')
    w.work.begin(Activity.AI)
    try:
        w.refresh_access()
        w.select_editor_section('calibration')
        assert not w.professional_editor.controls['calibration', 'red_hue'].isEnabled()
        assert not w.editor_reset_button.isEnabled()
        assert all(button.isEnabled() for button in w.tool_buttons)
    finally:
        w.work.end(Activity.AI)
        w.refresh_access()
    assert w.professional_editor.controls['calibration', 'red_hue'].isEnabled()


def test_basic_detail_reset_retains_precision_detail_settings(window):
    w = window
    w.controls['clarity'].spin.setValue(25)
    w.professional_editor.controls['detail', 'amount'].spin.setValue(35)
    w.commit()
    before = copy.deepcopy(w.edits)
    w.select_editor_section('detail')
    w.reset_editor_section()
    assert w.edits['adjustments']['clarity'] == 0
    assert w.edits['professional']['detail']['amount'] == 35
    w.undo(-1)
    assert w.edits == before


def test_legacy_visibility_request_reveals_curve_in_light_category(window):
    w = window
    w.tabs.setCurrentIndex(1)
    w.tabs.widget(1).ensureWidgetVisible(w.curve)
    QApplication.processEvents()
    assert w.editor_categories.currentIndex() == 0
    assert w.editor_tool_active('curves') and w.curve.isVisible()


def test_collapsing_active_section_clears_canvas_mode(window):
    w = window
    w.select_editor_section('crop')
    assert w.canvas.tool == 'crop'
    w.editor_groups.sections['crop'].header.click()
    assert w.canvas.tool == 'view'
    assert not w.editor_reset_button.isEnabled()
    w.editor_groups.sections['crop'].header.click()
    assert w.canvas.tool == 'crop' and w.editor_reset_button.isEnabled()


def test_changing_section_finishes_active_mask_stroke_once(window):
    w = window
    w.select_editor_section('masks')
    w.add_mask('brush')
    start = w.canvas.screen([0.4, 0.4]).toPoint()
    QTest.mousePress(w.canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(w.canvas, start + QPoint(15, 10))
    assert w._preview_dragging == 1 and w.canvas.stroke is not None
    w.select_editor_section('retouch')
    assert w._preview_dragging == 0 and w.canvas.stroke is None
    assert len(w.edits['masks'][0]['strokes']) == 1
    assert w.edits['retouch'] == []
    QTest.mouseRelease(w.canvas, Qt.MouseButton.LeftButton, pos=start)
    assert len(w.edits['masks'][0]['strokes']) == 1
