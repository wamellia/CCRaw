"""Native generation queue acceptance; all images come from a local test server."""

import copy

import pytest
from PySide6.QtWidgets import QDialog
from test_image_generation import Service, picture
from test_ui import wait_until


@pytest.fixture
def generation_window(request, monkeypatch, tmp_path):
    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'user'))
    return request.getfixturevalue('window')


def test_instruction_tabs_and_template_selection(generation_window):
    from ccraw.generation_panel import TemplateBrowser

    w = generation_window
    assert [w.instruction_tabs.tabText(i) for i in range(2)] == ['修图', '生成']
    p = w.generation_panel
    browser = TemplateBrowser(p)
    assert browser.items.count() == 50
    browser.search.setText('雨夜')
    assert browser.items.count() == 2
    browser.use_selected()
    assert browser.result() == QDialog.DialogCode.Accepted
    p.use_template(browser.selected)
    assert p.prompt.toPlainText() == browser.selected['prompt']
    assert not p.consent.isChecked()
    browser.close()


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_generation_controls_fit_compact_sidebar(generation_window, app, mode):
    from PySide6.QtWidgets import QPushButton, QComboBox, QSpinBox, QWidget
    from ccraw.ui.theme import apply_theme

    w = generation_window
    try:
        apply_theme(app, mode)
        w.resize(1180, 780)
        w.library_tabs.setCurrentIndex(1)
        w.instruction_tabs.setCurrentIndex(1)
        app.processEvents()
        p = w.generation_panel
        assert p.widget().width() <= p.viewport().width()
        for control in p.widget().findChildren(QWidget):
            if isinstance(control, (QPushButton, QComboBox, QSpinBox)) and control.isVisible():
                assert control.width() >= control.minimumSizeHint().width()
                assert control.height() >= 36
    finally:
        apply_theme(app, 'light')


def test_batch_generation_preserves_edits_and_imports_result(generation_window):
    w = generation_window
    p = w.generation_panel
    server = Service()
    try:
        p.settings = server.settings()
        p.reference.setCurrentIndex(p.reference.findData('none'))
        p.prompt.setPlainText('室内自然光')
        p.count.setValue(2)
        original, edits = w.source_path, copy.deepcopy(w.edits)
        p.enqueue()
        wait_until(lambda: not p.busy and not p.pending)
        tasks = p.store.history()
        assert len(tasks) == 2 and all(t['status'] == 'completed' for t in tasks)
        assert len(server.calls) == 2 and all('image' not in call[2] for call in server.calls)
        assert w.source_path == original and w.edits == edits
        assert all(p.store.result_path(t).read_bytes() == picture() for t in tasks)
        p.tasks.setCurrentRow(0)
        p.open_result()
        wait_until(lambda: not w.loading and not w.render_running)
        assert w.source_path == str(p.selected_result())
    finally:
        p.shutdown()
        server.close()


def test_reference_requires_consent_and_cancel_discards_late_result(generation_window):
    p = generation_window.generation_panel
    server = Service()
    server.delay = 2
    try:
        p.settings = server.settings()
        p.prompt.setPlainText('保持面部，柔和自然光')
        p.count.setValue(2)
        p.enqueue()
        assert not server.calls and not p.store.history()
        assert '发送参考图' in p.status.text()
        p.consent.setChecked(True)
        p.enqueue()
        wait_until(lambda: bool(server.calls))
        assert server.calls[0][2]['image'].startswith('data:image/jpeg;base64,')
        p.cancel_all()
        wait_until(lambda: not p.busy)
        assert len(server.calls) == 1
        assert all(t['status'] == 'cancelled' for t in p.store.history())
        assert not list(p.store.results.iterdir())
        assert p.generate.isEnabled()
    finally:
        p.shutdown()
        server.close()


def test_unopened_selected_photos_snapshot_initialization_and_synced_edits(
    generation_window, tmp_path, monkeypatch
):
    from PIL import Image

    w = generation_window
    path = str(tmp_path / 'unopened.png')
    Image.new('RGB', (100, 100)).save(path)
    w.add_documents([path])
    w.documents[path]['edits']['adjustments']['exposure'] = 0.8
    monkeypatch.setattr(w, 'selected_paths', lambda: [path, w.source_path])
    p = w.generation_panel
    p.reference.setCurrentIndex(p.reference.findData('selected'))
    p.consent.setChecked(True)
    references = p.references()
    assert references[0][2] is False and references[1][2] is True
    assert references[0][1]['adjustments']['exposure'] == 0.8
    w.documents[path]['edits']['adjustments']['exposure'] = 0
    assert references[0][1]['adjustments']['exposure'] == 0.8


def test_network_wait_keeps_gui_and_photo_preview_responsive(generation_window):
    from PySide6.QtCore import QTimer
    import numpy as np

    w = generation_window
    p = w.generation_panel
    server = Service()
    server.delay = 2
    ticks = []
    timer = QTimer(w)
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(1))
    try:
        p.settings = server.settings()
        p.reference.setCurrentIndex(p.reference.findData('none'))
        p.prompt.setPlainText('日落')
        p.enqueue()
        wait_until(lambda: bool(server.calls))
        before = w.rendered.copy()
        timer.start()
        w.controls['exposure'].spin.setValue(0.4)
        w.commit()
        wait_until(
            lambda: (
                len(ticks) >= 5 and not w.render_running and not np.array_equal(before, w.rendered)
            ),
            timeout=1.5,
        )
        assert p.busy and w.edits['adjustments']['exposure'] == 0.4
    finally:
        timer.stop()
        p.cancel_all()
        wait_until(lambda: not p.busy)
        server.close()


def test_cancelled_task_cannot_be_deleted_until_worker_finishes(generation_window, monkeypatch):
    from ccraw.generation_panel import HistoryDialog
    import threading

    p = generation_window.generation_panel
    entered, release = threading.Event(), threading.Event()
    original = p.store.save_result

    def slow_save(identifier, data):
        entered.set()
        release.wait(5)
        return original(identifier, data)

    monkeypatch.setattr(p.store, 'save_result', slow_save)
    server = Service()
    dialog = None
    try:
        p.settings = server.settings()
        p.reference.setCurrentIndex(p.reference.findData('none'))
        p.prompt.setPlainText('自然光')
        p.enqueue()
        wait_until(entered.is_set)
        identifier = p.current
        p.cancel_all()
        dialog = HistoryDialog(p)
        assert not dialog.remove.isEnabled()
        dialog.delete_task()
        assert p.store.get(identifier)['status'] == 'cancelled'
        release.set()
        wait_until(lambda: not p.busy)
        assert p.store.get(identifier)['status'] == 'cancelled'
        assert not list(p.store.results.iterdir())
        dialog.show_task(dialog.items.currentItem())
        assert dialog.remove.isEnabled()
    finally:
        release.set()
        if dialog:
            dialog.close()
        p.shutdown()
        server.close()
