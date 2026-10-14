"""Live localization preserves navigation keys and user-named projects/albums."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QGroupBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout


def test_tree_and_group_titles_translate_without_changing_user_items_or_routes(app):
    from ccraw import i18n

    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    group = QGroupBox('颜色与精度')
    layout.addWidget(group)
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    layout.addWidget(tree)
    built_in = QTreeWidgetItem(tree, ['常规'])
    built_in.setData(0, Qt.ItemDataRole.UserRole, 'general')
    album = QTreeWidgetItem(tree, ['常规'])
    album.setData(0, Qt.ItemDataRole.UserRole, 'album:123')
    album.setData(0, Qt.ItemDataRole.UserRole + 31, True)
    tree.setCurrentItem(built_in)
    i18n.localize_window(dialog, 'en')
    assert built_in.text(0) == 'General' and group.title() == 'Color and precision'
    assert album.text(0) == '常规'
    assert (
        tree.currentItem() is built_in and built_in.data(0, Qt.ItemDataRole.UserRole) == 'general'
    )
    i18n.localize_window(dialog, 'zh')
    assert built_in.text(0) == '常规' and group.title() == '颜色与精度'
    dialog.close()
