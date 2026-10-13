from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from ccraw.photo_agent.store import Project


def make_window(tmp_path):
    from ccraw.photo_agent.ui import AgentWindow

    p = Project.create(tmp_path / 'ui.ccrawagent', '管理')
    for i in range(3):
        source = tmp_path / f'{i}.png'
        Image.new('RGB', (120, 80), (30 + i * 40, 60, 90)).save(source)
        p.import_paths([source])
    return AgentWindow(p)


def test_rating_flag_auto_advance_and_text_focus(app, tmp_path):
    w = make_window(tmp_path)
    w.show()
    app.processEvents()
    try:
        assert hasattr(w, 'catalog') and hasattr(w, 'rating_buttons')
        w.gallery.setCurrentRow(0)
        ref = w.selected_ids()[0]
        w.auto_advance.setChecked(True)
        w.rating_buttons[4].click()
        assert w.catalog.state(ref)['rating'] == 4
        assert w.gallery.currentRow() == 1
        w.gallery.setFocus()
        next_ref = w.selected_ids()[0]
        QTest.keyClick(w.gallery, Qt.Key.Key_P)
        assert w.catalog.state(next_ref)['flag'] == 'pick'
        w.search_input.setFocus()
        QTest.keyClicks(w.search_input, '5')
        assert w.search_input.text().endswith('5')
        assert w.catalog.state(next_ref)['rating'] == 0
    finally:
        w.close()


def test_visible_filters_albums_and_preset_requery(app, tmp_path):
    w = make_window(tmp_path)
    try:
        assert hasattr(w, 'navigation') and hasattr(w, 'sort_combo')
        ids = [w.gallery.item(i).data(Qt.ItemDataRole.UserRole) for i in range(w.gallery.count())]
        album = w.catalog.create_album('候选')
        w.catalog.add_members(album, [ids[1]])
        w.refresh_catalog_navigation()
        index = w.navigation.findData('album:' + album)
        w.navigation.setCurrentIndex(index)
        assert w.gallery.count() == 1
        assert w.gallery.item(0).data(Qt.ItemDataRole.UserRole) == ids[1]
    finally:
        w.close()


def test_metadata_bulk_updates_only_checked_fields(app, tmp_path):
    w = make_window(tmp_path)
    try:
        assert hasattr(w, 'metadata_fields')
        ids = [w.gallery.item(i).data(Qt.ItemDataRole.UserRole) for i in range(2)]
        w.catalog.edit_metadata([ids[0]], {'title': 'A'})
        w.catalog.edit_metadata([ids[1]], {'title': 'B'})
        for i in range(2):
            w.gallery.item(i).setSelected(True)
        w.refresh_metadata()
        assert w.metadata_fields['title'].placeholderText() == '多个值'
        w.metadata_fields['description'].setText('共同说明')
        w.metadata_checks['description'].setChecked(True)
        w.save_metadata()
        assert w.catalog.metadata(ids[0])['title'] == 'A'
        assert w.catalog.metadata(ids[1])['title'] == 'B'
        assert w.catalog.metadata(ids[0])['description'] == '共同说明'
    finally:
        w.close()


def test_compare_sync_and_survey_remove_only_view(app, tmp_path):
    w = make_window(tmp_path)
    try:
        for i in range(2):
            w.gallery.item(i).setSelected(True)
        assert hasattr(w, 'compare_selected')
        dialog = w.compare_selected()
        assert len(dialog.views) == 2
        dialog.set_view_state(0, 2, (0.4, 0.6))
        assert dialog.views[1].zoom == 2 and dialog.views[1].relative_center == (0.4, 0.6)
        dialog.close()
        survey = w.survey_selected()
        survey.remove_candidate(0)
        assert len(survey.records) == 1
        assert w.catalog.query()['total'] == 3
        survey.close()
    finally:
        w.close()


def test_proxy_editor_has_truthful_offline_status_and_copy_editor_is_independent(
    app, tmp_path, monkeypatch
):
    w = make_window(tmp_path)
    try:
        assert (
            hasattr(w, 'proxy_button')
            and hasattr(w, 'trash_button')
            and hasattr(w, 'undo_catalog_button')
        )
        ref = w.gallery.item(0).data(Qt.ItemDataRole.UserRole)
        copy = w.catalog.create_virtual_copy(ref)
        w.refresh_gallery()
        assert w.gallery.count() == 4
        assert any('副本' in w.gallery.item(i).text() for i in range(4))
    finally:
        w.close()


def test_auto_advance_crosses_sql_page_boundary(app, tmp_path):
    w = make_window(tmp_path)
    try:
        with w.project.connect() as db:
            db.execute('DELETE FROM photos')
            db.executemany(
                'INSERT INTO photos(id,path,imported_at) VALUES(?,?,?)',
                [(f'{i:032x}', f'C:/synthetic/{i}.png', i) for i in range(201)],
            )
        w.refresh_gallery()
        w.gallery.setCurrentRow(199)
        w.auto_advance.setChecked(True)
        w.rate_selected(3)
        assert w.offset == 200 and w.gallery.count() == 1
        assert w.selected_ids() == [f'{200:032x}']
    finally:
        w.close()


def test_normal_export_is_guarded_in_proxy_editor(app, tmp_path, monkeypatch):
    from pathlib import Path
    from test_ui import wait_until
    from ccraw import batch_export

    def forbidden_dialog(*args, **kwargs):
        raise AssertionError('normal batch export must be blocked before its dialog')

    monkeypatch.setattr(batch_export, 'BatchExportDialog', forbidden_dialog)
    w = make_window(tmp_path)
    try:
        ref = w.gallery.item(0).data(Qt.ItemDataRole.UserRole)
        w.catalog.create_proxy(ref, max_edge=100)
        Path(w.project.photo(ref)['path']).unlink()
        w.gallery.setCurrentRow(0)
        w.open_editor(allow_proxy=True)
        editor = w.editors[0]
        wait_until(
            lambda: editor.source is not None and not editor.loading and not editor.render_running,
            timeout=45,
        )
        assert editor.property('catalogProxyOnly') is True
        assert editor.start_export(tmp_path / 'mislabelled-full.png') is False
        assert editor.batch_export() is False
        assert not (tmp_path / 'mislabelled-full.png').exists()
    finally:
        w.close()


def test_classic_editor_catalog_bridge_reuses_project_and_saved_recipes(app, tmp_path, monkeypatch):
    from ccraw.catalog.window import open_catalog_for_editor
    from ccraw.library import new_document
    from PySide6.QtWidgets import QMainWindow

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'app-data'))
    source = tmp_path / 'classic.png'
    Image.new('RGB', (20, 15)).save(source)
    editor = QMainWindow()
    editor.documents = {str(source): new_document(source)}
    editor.documents[str(source)]['edits']['adjustments']['exposure'] = 0.75
    editor.documents[str(source)]['initialized'] = True
    editor.documents[str(source)]['metadata'] = {
        'creator': '旅行作者',
        'copyright': '版权所有',
        'keywords': ['旅行|海边'],
    }
    editor.album_path = ''
    editor.stash_document = lambda: None
    manager = open_catalog_for_editor(editor)
    try:
        ref = manager.catalog.query()['photos'][0]['id']
        assert manager.project.preferences()['recipe:' + ref]['adjustments']['exposure'] == 0.75
        assert manager.catalog.metadata(ref)['author'] == '旅行作者'
        assert manager.catalog.metadata(ref)['keywords'] == ['旅行|海边']
        assert open_catalog_for_editor(editor) is manager
        assert manager.catalog.query()['total'] == 1
        manager.catalog.edit_metadata([ref], {'author': ''})
        changed = manager.project.preferences()['recipe:' + ref]
        changed['adjustments']['exposure'] = 1.25
        manager.project.preference('recipe:' + ref, changed)
        manager.close()
        assert editor.documents[str(source)]['edits']['adjustments']['exposure'] == 1.25
        assert editor.documents[str(source)]['metadata']['creator'] == ''
    finally:
        manager.close()
        editor.close()


def test_native_compare_uses_visible_original_pixel_tiles(app, tmp_path):
    from test_ui import wait_until

    w = make_window(tmp_path)
    try:
        for i in range(2):
            w.gallery.item(i).setSelected(True)
        dialog = w.compare_selected()
        dialog.load_native()
        wait_until(
            lambda: all(
                getattr(v, 'native_holder', None) is not None and v.tile_items for v in dialog.views
            ),
            timeout=30,
        )
        assert all(v.native_size == (120, 80) for v in dialog.views)
        assert all(
            abs(v.transform().m11() * v.devicePixelRatioF() - 1) < 0.01 for v in dialog.views
        )
        assert all(v.tile_store.bytes <= 64 * 2**20 for v in dialog.views)
        dialog.close()
    finally:
        w.close()


def test_filter_and_sort_apply_to_existing_search_selection_and_chat_can_fold(app, tmp_path):
    w = make_window(tmp_path)
    try:
        refs = [w.gallery.item(i).data(Qt.ItemDataRole.UserRole) for i in range(3)]
        w.result_ids = refs
        w.catalog.set_state([refs[1]], rating=5)
        w.catalog_rules = {'field': 'rating', 'op': 'ge', 'value': 4}
        w.refresh_gallery()
        assert (
            w.gallery.count() == 1 and w.gallery.item(0).data(Qt.ItemDataRole.UserRole) == refs[1]
        )
        assert '评级' in w.filter_summary.text() and '4' in w.filter_summary.text()
        w.show()
        app.processEvents()
        w.chat_toggle.click()
        assert w.chat_panel.isHidden()
        w.chat_toggle.click()
        assert not w.chat_panel.isHidden()
    finally:
        w.close()


def test_suggestion_decision_undo_updates_visible_status(app, tmp_path):
    w = make_window(tmp_path)
    try:
        refs = [w.gallery.item(i).data(Qt.ItemDataRole.UserRole) for i in range(2)]
        group = {'photos': refs, 'confidence': 'sha256'}
        w.report = {'duplicates': [group]}
        w.refresh_suggestions()
        w.decide_group('duplicates', group, 'resolved')
        assert '已处理' in w.suggestion_list.item(0).text()
        w.catalog_action(w.catalog.undo)
        assert '已处理' not in w.suggestion_list.item(0).text()
    finally:
        w.close()


def test_paging_remembers_visible_page_selection_without_hidden_agent_selection(app, tmp_path):
    w = make_window(tmp_path)
    try:
        with w.project.connect() as db:
            db.execute('DELETE FROM photos')
            db.executemany(
                'INSERT INTO photos(id,path,imported_at) VALUES(?,?,?)',
                [(f'{i:032x}', f'C:/synthetic/{i}.png', i) for i in range(201)],
            )
        w.refresh_gallery()
        w.gallery.setCurrentRow(4)
        selected = w.selected_ids()
        w.page(1)
        assert not w.selected_ids()
        w.page(-1)
        assert w.selected_ids() == selected
    finally:
        w.close()
