"""Grouped catalog controls execute the existing catalog operations."""

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QPushButton

from ccraw.photo_agent.store import Project


def make_window(tmp_path, monkeypatch):
    from ccraw.photo_agent.ui import AgentWindow

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'settings'))
    project = Project.create(tmp_path / 'grouped.ccrawagent', '图库')
    for index in range(3):
        source = tmp_path / f'{index}.png'
        Image.new('RGB', (120, 80)).save(source)
        project.import_paths([source])
    return AgentWindow(project)


def test_grouped_management_has_all_actions_and_no_button_pile(app, tmp_path, monkeypatch):
    window = make_window(tmp_path, monkeypatch)
    try:
        assert set(window.catalog_menus) == {'albums', 'selection', 'files', 'history'}
        assert len(window.catalog_actions) == 28
        assert not any(window.tabs.tabText(i) == '相册管理' for i in range(window.tabs.count()))
        assert not any(
            b.text() in {'本地模型', '模型设置'} for b in window.findChildren(QPushButton)
        )
        assert len(window.import_menu.actions()) == 2
        assert not window.catalog_actions['compare'].isEnabled()
        window.gallery.item(0).setSelected(True)
        assert window.catalog_actions['copy'].isEnabled()
        assert window.catalog_actions['trash'].isEnabled()
        window.gallery.item(1).setSelected(True)
        assert window.catalog_actions['compare'].isEnabled()
        assert not window.catalog_actions['copy'].isEnabled()
        window.worker = object()
        window.update_catalog_controls()
        assert not window.catalog_actions['trash'].isEnabled()
        window.worker = None
    finally:
        window.worker = None
        window.close()


def test_real_menu_actions_copy_trash_restore_and_undo(app, tmp_path, monkeypatch):
    window = make_window(tmp_path, monkeypatch)
    try:
        window.gallery.setCurrentRow(0)
        ref = window.selected_ids()[0]
        window.catalog_actions['copy'].trigger()
        assert window.catalog.query()['total'] == 4
        window.catalog_actions['trash'].trigger()
        assert ref in {p['id'] for p in window.catalog.query(trash=True)['photos']}
        window.catalog_actions['undo'].trigger()
        assert ref not in {p['id'] for p in window.catalog.query(trash=True)['photos']}
        window.catalog_actions['redo'].trigger()
        window.navigation.setCurrentIndex(window.navigation.findData('trash'))
        window.gallery.setCurrentRow(0)
        assert window.catalog_actions['restore'].isEnabled()
        assert not window.catalog_actions['trash'].isEnabled()
        window.catalog_actions['restore'].trigger()
        assert ref not in {p['id'] for p in window.catalog.query(trash=True)['photos']}
    finally:
        window.close()


def test_tree_navigation_preserves_album_parent_and_filter_queries(app, tmp_path, monkeypatch):
    window = make_window(tmp_path, monkeypatch)
    try:
        refs = [window.gallery.item(i).data(Qt.ItemDataRole.UserRole) for i in range(3)]
        folder = window.catalog.create_album('旅行', kind='folder')
        album = window.catalog.create_album('海边', parent=folder)
        window.catalog.add_members(album, [refs[1]])
        window.catalog.set_state([refs[2]], rating=5)
        preset = window.catalog.save_filter('五星', {'field': 'rating', 'op': 'eq', 'value': 5})
        window.refresh_catalog_navigation()
        item = window.catalog_navigation_items['album:' + album]
        assert item.parent() is window.catalog_navigation_items['album:' + folder]
        window.catalog_navigation_tree.setCurrentItem(item)
        assert window.selected_ids() == []
        assert window.gallery.count() == 1
        assert window.gallery.item(0).data(Qt.ItemDataRole.UserRole) == refs[1]
        window.catalog_navigation_tree.setCurrentItem(
            window.catalog_navigation_items['preset:' + preset]
        )
        assert window.gallery.item(0).data(Qt.ItemDataRole.UserRole) == refs[2]
        monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('新相册', True))
        window.catalog_actions['new_album'].trigger()
        assert any(a['name'] == '新相册' for a in window.catalog.albums())
    finally:
        window.close()


def test_compact_workspace_keeps_photos_and_selection_controls_inside(app, tmp_path, monkeypatch):
    from ccraw.photo_agent.analysis import index_project

    window = make_window(tmp_path, monkeypatch)
    index_project(window.project)
    window.refresh_gallery()
    window.resize(980, 680)
    window.show()
    app.processEvents()
    try:
        window.gallery.setCurrentRow(0)
        assert window.gallery.height() >= 160
        assert window.catalog_navigation_tree.isVisible()
        for control in (
            window.editor_button,
            window.annotation_button,
            window.recommend_button,
            window.selection_more_button,
            window.rating_menu_button,
        ):
            assert control.isVisible()
            assert control.height() >= 30
            assert window.rect().contains(control.mapTo(window, control.rect().bottomRight()))
        assert window.recommend_button.isEnabled()
        window.gallery.clearSelection()
        assert not window.recommend_button.isEnabled()
        assert window.proposal_list.count() == 0
    finally:
        window.close()


def test_compare_and_stack_actions_operate_on_the_current_selection(app, tmp_path, monkeypatch):
    window = make_window(tmp_path, monkeypatch)
    try:
        for index in range(2):
            window.gallery.item(index).setSelected(True)
        refs = set(window.selected_ids())
        window.catalog_actions['compare'].trigger()
        assert len(window.catalog_dialogs) == 1
        assert len(window.catalog_dialogs[0].views) == 2
        window.catalog_dialogs[0].close()
        window.catalog_actions['stack'].trigger()
        assert set(window.catalog.stacks()[0]['photos']) == refs
        assert window.catalog_actions['stack_ops'].isEnabled()
    finally:
        window.close()


def test_navigation_refresh_and_selection_keep_current_language(app, tmp_path, monkeypatch):
    from ccraw import i18n

    window = make_window(tmp_path, monkeypatch)
    previous_language = i18n.language()
    try:
        i18n.set_language('en')
        monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('常规', True))
        window.catalog_actions['new_album'].trigger()
        tree = window.catalog_navigation_tree
        assert tree.topLevelItem(0).text(0) == 'Photos'
        assert tree.topLevelItem(1).text(0) == 'Albums and folders'
        assert window.catalog_navigation_items['all'].text(0) == i18n.translate('全部照片')
        assert window.catalog_navigation_items['all'].data(0, Qt.ItemDataRole.UserRole) == 'all'
        album = next(a for a in window.catalog.albums() if a['name'] == '常规')
        item = window.catalog_navigation_items['album:' + album['id']]
        assert item.text(0) == '常规'
        assert item.data(0, Qt.ItemDataRole.UserRole) == 'album:' + album['id']
        window.gallery.setCurrentRow(0)
        assert window.selection_label.text() == '1 photos selected'
        window.gallery.clearSelection()
        assert window.selection_label.text() == 'Select photos to edit or organize'
    finally:
        i18n.set_language(previous_language)
        window.close()
