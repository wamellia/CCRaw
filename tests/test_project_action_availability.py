"""Project controls reflect their actual selection and operation prerequisites."""

from pathlib import Path
import threading

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QInputDialog

from ccraw.photo_agent.store import Project
from ccraw.photo_agent.models import digest


def make_window(tmp_path, monkeypatch, count=0):
    from ccraw.photo_agent.ui import AgentWindow

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'settings'))
    project = Project.create(tmp_path / 'availability.ccrawagent', '图库')
    for index in range(count):
        source = tmp_path / f'{index}.png'
        Image.new('RGB', (80, 60), (index, 60, 90)).save(source)
        ref = project.import_paths([source])[0]
        project.update_photo(ref, facts={'width': 80, 'height': 60}, sha=digest(source))
    return AgentWindow(project)


def select(window, count):
    window.gallery.clearSelection()
    for row in range(count):
        window.gallery.item(row).setSelected(True)


def test_empty_project_disables_impossible_actions_but_keeps_creation(app, tmp_path, monkeypatch):
    window = make_window(tmp_path, monkeypatch)
    try:
        for key in ('auto_stack', 'prefix', 'missing', 'proxy', 'order', 'undo', 'redo'):
            assert not window.catalog_actions[key].isEnabled(), key
        for key in ('new_album', 'new_folder', 'new_smart', 'save_filter', 'backup'):
            assert window.catalog_actions[key].isEnabled(), key
        assert not window.annotation_button.isEnabled()
        assert not window.scan_button.isEnabled()
        assert not window.previous_page_button.isEnabled()
        assert not window.next_page_button.isEnabled()
        assert all(not button.isEnabled() for button in window.suggestion_decision_buttons)
        monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('空相册', True))
        window.catalog_actions['new_album'].trigger()
        album = window.catalog.albums()[0]['id']
        window.navigation.setCurrentIndex(window.navigation.findData('album:' + album))
        assert window.catalog_actions['edit_album'].isEnabled()
        assert not window.catalog_actions['order'].isEnabled()
    finally:
        window.close()


def test_existing_stack_disables_creation_but_keeps_management(app, tmp_path, monkeypatch):
    window = make_window(tmp_path, monkeypatch, 2)
    try:
        select(window, 2)
        assert window.catalog_actions['stack'].isEnabled()
        window.catalog.create_stack(window.selected_ids())
        window.update_catalog_controls()
        assert not window.catalog_actions['stack'].isEnabled()
        assert window.catalog_actions['stack_ops'].isEnabled()
    finally:
        window.close()


def test_selection_counts_and_busy_callback_guards_restore(app, tmp_path, monkeypatch):
    from test_ui import wait_until

    window = make_window(tmp_path, monkeypatch, 31)
    gate = threading.Event()
    try:
        for count in (0, 1, 2, 31):
            select(window, count)
            assert window.annotation_button.isEnabled() == (count == 1)
            assert window.editor_button.isEnabled() == (count == 1)
            assert window.recommend_button.isEnabled() == (1 <= count <= 30)
            assert window.catalog_actions['compare'].isEnabled() == (count == 2)
            assert window.catalog_actions['survey'].isEnabled() == (2 <= count <= 30)
        select(window, 1)
        ref = window.selected_ids()[0]
        window.start_work('等待', lambda cancel, emit: gate.wait(5))
        assert not window.editor_button.isEnabled()
        assert not window.annotation_button.isEnabled()
        window.rate_selected(5)
        window.flag_selected('pick')
        window.copy_selected()
        window.save_annotation(2, ['不应保存'])
        assert window.catalog.state(ref)['rating'] == 0
        assert window.catalog.state(ref)['flag'] == 'none'
        assert window.catalog.query()['total'] == 31
        assert 'annotation:' + ref not in window.project.preferences()
        gate.set()
        wait_until(lambda: window.worker is None)
        assert window.editor_button.isEnabled()
        assert window.annotation_button.isEnabled()
        assert window.recommend_button.isEnabled()
    finally:
        gate.set()
        wait_until(lambda: window.worker is None)
        window.close()


def test_missing_original_valid_proxy_policy_and_virtual_copy(app, tmp_path, monkeypatch):
    window = make_window(tmp_path, monkeypatch, 1)
    try:
        select(window, 1)
        ref = window.selected_ids()[0]
        source = Path(window.project.photo(ref)['path'])
        proxy = window.catalog.create_proxy(ref, max_edge=64)
        source.unlink()
        window.update_catalog_controls()
        assert window.editor_button.isEnabled()
        assert not window.catalog_actions['proxy'].isEnabled()
        assert not window.recommend_button.isEnabled()
        window.project.preference('ccraw.settings', {'project': {'proxy_policy': 'original'}})
        window.update_catalog_controls()
        assert not window.editor_button.isEnabled()
        window.project.preference('ccraw.settings', {'project': {'proxy_policy': 'auto'}})
        copy = window.catalog.create_virtual_copy(ref)
        window.refresh_gallery()
        window.gallery.clearSelection()
        for row in range(window.gallery.count()):
            if window.gallery.item(row).data(Qt.ItemDataRole.UserRole) == copy:
                window.gallery.item(row).setSelected(True)
        assert not window.editor_button.isEnabled()  # Each reference needs its own proxy.
        window.gallery.clearSelection()
        for row in range(window.gallery.count()):
            if window.gallery.item(row).data(Qt.ItemDataRole.UserRole) == ref:
                window.gallery.item(row).setSelected(True)
        Path(proxy['path']).unlink()
        window.update_catalog_controls()
        assert not window.editor_button.isEnabled()
    finally:
        window.close()


def test_virtual_copy_with_own_offline_proxy_and_proxy_dialog_options(app, tmp_path, monkeypatch):
    window = make_window(tmp_path, monkeypatch, 1)
    expected = [True, False, False, True, False]

    def inspect_dialog(dialog):
        combo = dialog.findChild(QComboBox)
        assert [combo.model().item(i).isEnabled() for i in range(5)] == expected
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QInputDialog, 'exec', inspect_dialog)
    try:
        select(window, 1)
        original = window.selected_ids()[0]
        window.proxy_dialog()
        copy = window.catalog.create_virtual_copy(original)
        window.catalog.create_proxy(copy, max_edge=64)
        Path(window.project.photo(original)['path']).unlink()
        window.refresh_gallery()
        window.gallery.clearSelection()
        for row in range(window.gallery.count()):
            if window.gallery.item(row).data(Qt.ItemDataRole.UserRole) == copy:
                window.gallery.item(row).setSelected(True)
        assert window.editor_button.isEnabled()
        expected[:] = [True] * 5
        window.proxy_dialog()
        window.gallery.clearSelection()
        for row in range(window.gallery.count()):
            if window.gallery.item(row).data(Qt.ItemDataRole.UserRole) == original:
                window.gallery.item(row).setSelected(True)
        expected[:] = [False, False, False, True, False]
        window.proxy_dialog()
    finally:
        window.close()


def test_proposal_buttons_follow_current_valid_pending_item(app, tmp_path, monkeypatch):
    from ccraw.photo_agent.editing import propose_local

    window = make_window(tmp_path, monkeypatch, 1)
    try:
        select(window, 1)
        ref = window.selected_ids()[0]
        propose_local(window.project, ref)
        window.refresh_proposals()
        assert window.apply_button.isEnabled() and window.preview_button.isEnabled()
        window.proposal_list.setCurrentRow(-1)
        assert not window.apply_button.isEnabled()
        assert not window.preview_button.isEnabled()
        window.proposal_list.setCurrentRow(0)
        proposal = window.proposal_list.currentItem().data(Qt.ItemDataRole.UserRole)
        window.project.proposal_status(proposal['id'], 'completed')
        window.update_proposal_controls()
        assert not window.apply_button.isEnabled()
        assert not window.preview_button.isEnabled()
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(
            QMessageBox, 'exec', lambda *a, **k: (_ for _ in ()).throw(AssertionError('dialog'))
        )
        window.apply_proposal()
        window.preview_proposal()
        window.refresh_proposals()
        assert window.apply_button.isEnabled()
        Path(window.project.photo(ref)['path']).unlink()
        window.update_proposal_controls()
        assert not window.apply_button.isEnabled()
        assert not window.preview_button.isEnabled()
    finally:
        window.close()


def test_gallery_page_edges_and_album_order_limit(app, tmp_path, monkeypatch):
    window = make_window(tmp_path, monkeypatch)
    try:
        with window.project.connect() as db:
            db.executemany(
                'INSERT INTO photos(id,path,imported_at) VALUES(?,?,?)',
                [(f'{i:032x}', f'C:/synthetic/{i}.png', i) for i in range(10001)],
            )
        window.result_ids = [f'{i:032x}' for i in range(201)]
        window.refresh_gallery()
        assert not window.previous_page_button.isEnabled()
        assert window.next_page_button.isEnabled()
        window.next_page_button.click()
        assert window.offset == 200 and window.gallery.count() == 1
        assert window.previous_page_button.isEnabled()
        assert not window.next_page_button.isEnabled()
        window.next_page_button.click()
        assert window.offset == 200
        window.previous_page_button.click()
        assert window.offset == 0 and window.gallery.count() == 200
        album = window.catalog.create_album('大相册')
        with window.project.connect() as db:
            db.executemany(
                'INSERT INTO album_members(album_id,photo_ref,position) VALUES(?,?,?)',
                [(album, f'{i:032x}', i) for i in range(10000)],
            )
        window.refresh_catalog_navigation()
        window.navigation.setCurrentIndex(window.navigation.findData('album:' + album))
        assert window.catalog_actions['order'].isEnabled()
        with window.project.connect() as db:
            db.execute('INSERT INTO album_members VALUES(?,?,?)', (album, f'{10000:032x}', 10000))
        window.update_catalog_controls()
        assert not window.catalog_actions['order'].isEnabled()
    finally:
        window.close()


def test_suggestion_decisions_and_paging_edges(app, tmp_path, monkeypatch):
    window = make_window(tmp_path, monkeypatch, 1)
    try:
        ref = window.project.photos()[0]['id']
        window.report = {'quality': [{'photo_id': ref, 'reasons': ['提示']} for _ in range(201)]}
        window.refresh_suggestions()
        assert not window.previous_suggestion_button.isEnabled()
        assert window.next_suggestion_button.isEnabled()
        assert all(not button.isEnabled() for button in window.suggestion_decision_buttons)
        window.suggestion_list.setCurrentRow(0)
        assert all(button.isEnabled() for button in window.suggestion_decision_buttons)
        window.suggestion_decision_buttons[0].click()
        assert (
            window.catalog.suggestion_decision('quality', {'photo_id': ref})['decision'] == 'keep'
        )
        window.next_suggestion_button.click()
        assert window.suggestion_offset == 200
        assert window.previous_suggestion_button.isEnabled()
        assert not window.next_suggestion_button.isEnabled()
    finally:
        window.close()
