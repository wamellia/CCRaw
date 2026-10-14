import threading

from PySide6.QtWidgets import QMainWindow
from ccraw.scheduler import WorkState, Activity
from ccraw.ui.task_center import TaskCenter, active_tasks


def test_task_center_observes_existing_task_and_requests_cancel_without_replay(app):
    owner = QMainWindow()
    owner.work = WorkState(owner)
    owner.export_cancel = threading.Event()
    owner.work.begin(Activity.EXPORTING)
    dialog = TaskCenter(owner)
    try:
        assert any(row['id'] == (id(owner), 'exporting') for row in active_tasks())
        dialog.list.setCurrentItem(dialog.list.topLevelItem(0))
        assert dialog.cancel.isEnabled()
        dialog.cancel.click()
        assert owner.export_cancel.is_set() and owner.work.active(Activity.EXPORTING)
        owner.work.end(Activity.EXPORTING)
        dialog.refresh()
        assert not dialog.current and len(dialog.recent) == 1
        assert not dialog.cancel.isEnabled()
        dialog.close()
        assert not dialog.timer.isActive()
    finally:
        owner.deleteLater()


def test_task_center_close_leaves_uncancellable_decode_running(app):
    owner = QMainWindow()
    owner.work = WorkState(owner)
    owner.work.begin(Activity.LOADING)
    dialog = TaskCenter(owner)
    try:
        dialog.list.setCurrentItem(dialog.list.topLevelItem(0))
        assert not dialog.cancel.isEnabled()
        dialog.close()
        assert owner.work.active(Activity.LOADING)
    finally:
        owner.work.end(Activity.LOADING)
        owner.deleteLater()


def test_retained_closed_editor_does_not_block_device_preferences(app, tmp_path):
    from ccraw.ui.settings_dialog import install_settings, _busy
    from ccraw.settings import SettingsService

    owner = QMainWindow()
    owner.work = WorkState(owner)
    owner.work.closing = True
    binding = install_settings(
        owner, service=SettingsService(tmp_path / 'settings.json', migrate=False)
    )
    try:
        assert not _busy() and binding._last_backend == 'auto'
        owner.work.closing = False
        owner.work.begin(Activity.RENDER)
        assert _busy()
        owner.work.end(Activity.RENDER)
        assert not _busy()
    finally:
        owner.deleteLater()


def test_editor_close_drains_thumbnail_activity_even_when_callback_is_suppressed(
    app, tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from ccraw.app import MainWindow
    from test_ui import wait_until

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'user'))
    monkeypatch.setenv('CCRAW_CACHE_DIR', str(tmp_path / 'cache'))
    window = MainWindow()
    window.show()
    wait_until(lambda: not window.scheduler.jobs)
    window.scheduler.pool = SimpleNamespace(start=lambda *args: None)
    window.work.begin(Activity.THUMBNAILS)
    job = window.scheduler.submit(
        lambda: 1,
        lambda _: window.work.end(Activity.THUMBNAILS),
        lambda _: window.work.end(Activity.THUMBNAILS),
    )
    try:
        window.close()
        assert window.closing and window.work.active(Activity.THUMBNAILS)
        assert window.scheduler.active is job  # A running job still blocks device changes.
        job.run()
        wait_until(lambda: not window.isVisible())
        assert not window.work.active(Activity.THUMBNAILS)
        assert not window.scheduler.jobs and window.scheduler.active is None
    finally:
        window.deleteLater()
