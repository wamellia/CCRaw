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
