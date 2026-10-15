import copy
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_ui import wait_until
from ccraw import exposure_curve, model
from ccraw.app import MainWindow
from ccraw.ai_dialog import EnhancementDialog


def test_close_drains_active_job_and_drops_queued_previews(app):
    import threading

    w = MainWindow()
    w.show()
    release = threading.Event()
    started = threading.Event()
    calls = []

    def first():
        started.set()
        release.wait(3)
        return 1

    w.job(first, lambda r: calls.append('active result'))
    wait_until(started.is_set)
    w.job(lambda: calls.append('queued work'), lambda r: None)
    w.close()
    assert w.closing and w.isVisible()
    release.set()
    wait_until(lambda: not w.isVisible())
    assert not calls and not w.queued_jobs


def test_categories_have_inline_watermark_and_light_curves(app):
    w = MainWindow()
    w.show()
    assert [w.editor_categories.tabText(i) for i in range(6)] == [
        '光影',
        '色彩',
        '细节',
        '构图',
        '局部',
        '效果',
    ]
    assert w.editor_categories.widget(0).isAncestorOf(w.curve)
    assert w.editor_categories.widget(5).isAncestorOf(w.watermark_editor)
    w.tabs.setCurrentIndex(2)
    assert not w.watermark_editor.enabled.isEnabled()
    assert w.pool.maxThreadCount() == 32
    assert all(a.text() != '水印' for a in w.menuBar().actions())
    w.close()


def test_curve_sliders_undo_and_inline_watermark(window):
    w = window
    w.tabs.setCurrentIndex(0)
    w.tabs.widget(0).ensureWidgetVisible(w.exposure_curve)
    w.controls['shadows'].spin.setValue(25)
    w.commit()
    assert w.exposure_curve.values['shadows'] == 25
    baseline = copy.deepcopy(w.edits['adjustments'])
    a = w.exposure_curve.screen(0.65, float(exposure_curve.evaluate(baseline, 0.65))).toPoint()
    b = w.exposure_curve.screen(
        0.65, float(exposure_curve.evaluate(baseline, 0.65)) + 0.07
    ).toPoint()
    QTest.mousePress(w.exposure_curve, Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(w.exposure_curve, b)
    QTest.mouseRelease(w.exposure_curve, Qt.MouseButton.LeftButton, pos=b)
    assert w.edits['adjustments']['highlights'] > baseline['highlights']
    assert abs(w.controls['highlights'].spin.value() - w.edits['adjustments']['highlights']) <= 0.5
    assert w.edits['curves'] == model.recipe()['curves']
    w.undo(-1)
    assert w.edits['adjustments'] == baseline
    assert w.exposure_curve.values == baseline
    w.tabs.setCurrentIndex(2)
    w.watermark_editor.enabled.setChecked(True)
    editor = w.watermark_editor
    before = copy.deepcopy(w.edits['watermark'])
    editor.layout_type.setCurrentIndex(editor.layout_type.findData('standard1'))
    assert w.edits['watermark']['enabled'] and w.edits['watermark']['template'] == 'standard1'
    w.undo(-1)
    assert w.edits['watermark'] == before
    assert editor.layout_type.currentData() == before['template']
    assert editor.pipeline_editor.values() == before['pipeline']
    for kind in ('super', 'denoise'):
        dialog = EnhancementDialog(w, kind)
        assert dialog.method.currentIndex() == 0 and dialog.method.count() == (
            2 if kind == 'super' else 3
        )
        dialog.reject()
