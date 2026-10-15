import time

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from ccraw.photo_agent import editing
from ccraw.photo_agent.ui import AgentWindow
from test_agent_controls import photo_project


def test_preview_dialog_keeps_both_images_in_their_original_aspect_ratio(app, tmp_path):
    from ccraw.photo_agent.preview import PreviewDialog

    before, after = tmp_path / 'before.png', tmp_path / 'after.png'
    Image.new('RGB', (400, 200), 'red').save(before)
    Image.new('RGB', (100, 200), 'blue').save(after)
    dialog = PreviewDialog(before, after)
    try:
        dialog.resize(900, 500)
        dialog.show()
        QTest.qWait(60)
        for label, ratio in zip(dialog.images, [2, 0.5]):
            pixmap = label.pixmap()
            assert pixmap and abs(pixmap.width() / pixmap.height() - ratio) < 0.02
            assert pixmap.devicePixelRatioF() == label.devicePixelRatioF()
            size = pixmap.deviceIndependentSize()
            assert size.width() <= label.width() and size.height() <= label.height()
    finally:
        dialog.close()


def test_agent_preview_button_renders_in_worker_without_creating_a_derivative(
    app, tmp_path, monkeypatch
):
    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'settings'))
    project, ids = photo_project(tmp_path)
    editing.propose_local(project, ids[0], {'crop': [0.5, 0, 1, 1]})
    window = AgentWindow(project)
    try:
        window.show()
        window.gallery.setCurrentRow(0)
        assert window.preview_button.isEnabled()
        QTest.mouseClick(window.preview_button, Qt.MouseButton.LeftButton)
        deadline = time.monotonic() + 30
        while window.worker is not None:
            QTest.qWait(25)
            assert time.monotonic() < deadline
        assert window.preview_dialog.isVisible()
        assert project.derivatives() == []
        assert project.proposals(pending_only=True)
        window.preview_dialog.close()
    finally:
        window.close()


def test_agent_proposal_native_compare_renders_distinct_original_pixel_tiles(
    app, tmp_path, monkeypatch
):
    from test_ui import wait_until
    from ccraw.viewport import tile_array
    import numpy as np

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'settings'))
    project, ids = photo_project(tmp_path)
    proposal = editing.propose_local(project, ids[0], {'adjustments': {'exposure': 1}})[0]
    result = editing.preview_local(project, proposal['id'])
    window = AgentWindow(project)
    window.show()
    window.gallery.setCurrentRow(0)
    dialog = window.show_native_preview(result)
    try:
        assert dialog is not None
        dialog.load_native()
        wait_until(
            lambda: all(view.native_holder and view.tile_items for view in dialog.views), timeout=30
        )
        assert all(view.native_size == (200, 100) for view in dialog.views)
        assert all(
            abs(view.transform().m11() * view.devicePixelRatioF() - 1) < 0.01
            for view in dialog.views
        )
        means = [
            np.mean(tile_array(next(iter(view.tile_store.layer('edited', 'compare', 0).values()))))
            for view in dialog.views
        ]
        assert means[1] > means[0] + 15
        assert (
            project.derivatives() == []
            and project.get_proposal(proposal['id'])['status'] == 'pending'
        )
        window.gallery.clearSelection()
        assert window.show_native_preview(result) is None
    finally:
        dialog.close()
        wait_until(lambda: not dialog.jobs)
        window.close()
