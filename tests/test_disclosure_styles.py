"""Hierarchy affordances must survive rebuilding, themes and device scales."""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu, QPushButton, QTreeWidget, QTreeWidgetItem, QWidget

from ccraw.ui import theme
from ccraw.ui.editor_groups import EditorSection


def pixels(image, target, tolerance=12):
    """Count a rendered color, allowing antialiased SVG stroke boundaries."""
    target = QColor(target)
    return sum(
        abs(image.pixelColor(x, y).red() - target.red()) <= tolerance
        and abs(image.pixelColor(x, y).green() - target.green()) <= tolerance
        and abs(image.pixelColor(x, y).blue() - target.blue()) <= tolerance
        for x in range(image.width())
        for y in range(image.height())
    )


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_tree_parent_roles_follow_insert_remove_and_theme(app, mode):
    theme.apply_theme(app, mode)
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    root = QTreeWidgetItem(tree, ['Parent'])
    root.setData(0, Qt.ItemDataRole.UserRole, 'album:user')
    theme.style_hierarchy(tree)
    assert not root.font(0).bold()
    child = QTreeWidgetItem(root, ['Child'])
    assert root.font(0).bold()
    assert not child.font(0).bold()
    assert root.foreground(0).color() != child.foreground(0).color()
    assert root.background(0).color() != child.background(0).color()
    original = root.foreground(0).color()
    theme.apply_theme(app, 'dark' if mode == 'light' else 'light')
    assert root.foreground(0).color() != original
    assert root.data(0, Qt.ItemDataRole.UserRole) == 'album:user'
    root.takeChild(0)
    assert not root.font(0).bold()
    tree.clear()
    group = QTreeWidgetItem(tree, ['Empty group'])
    group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
    theme.style_hierarchy(tree)
    assert group.font(0).bold()
    tree.close()


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_editor_header_has_vector_icons_and_toggles_by_full_click(app, mode):
    theme.apply_theme(app, mode)
    section = EditorSection('Section', QWidget())
    section.resize(320, 120)
    section.show()
    app.processEvents()
    assert section.header.arrowType() == Qt.ArrowType.NoArrow
    assert not section.header.icon().isNull()
    collapsed = section.header.icon().cacheKey()
    QTest.mouseClick(section.header, Qt.MouseButton.LeftButton, pos=QPoint(230, 16))
    assert section.header.isChecked() and section.content.isVisible()
    assert section.header.icon().cacheKey() != collapsed
    expanded = section.header.icon().cacheKey()
    theme.apply_theme(app, 'dark' if mode == 'light' else 'light')
    assert section.header.icon().cacheKey() != expanded
    assert section.header.isChecked()
    section.header.setEnabled(False)
    image = section.header.icon().pixmap(16, 16, QIcon.Mode.Disabled).toImage()
    assert pixels(image, theme.tokens().disabled) > 8
    section.close()


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_rendered_tree_arrows_change_orientation_and_menu_disabled_is_gray(app, mode):
    t = theme.apply_theme(app, mode)
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    tree.resize(260, 200)
    root = QTreeWidgetItem(tree, ['Parent'])
    child = QTreeWidgetItem(root, ['Child'])
    theme.style_hierarchy(tree)
    tree.show()
    app.processEvents()
    rect = tree.visualItemRect(root)
    arrow_rect = rect.adjusted(-tree.indentation(), 0, 0, 0)
    arrow_rect.setWidth(tree.indentation())
    closed = tree.viewport().grab(arrow_rect).toImage()
    assert pixels(closed, theme.disclosure_color(t)) > 8
    root.setExpanded(True)
    app.processEvents()
    opened = tree.viewport().grab(arrow_rect).toImage()
    assert opened != closed
    assert pixels(opened, theme.disclosure_color(t)) > 8
    assert tree.visualItemRect(child).isValid()
    tree.clearSelection()
    QTest.mouseMove(tree.viewport(), QPoint(240, 170))
    app.processEvents()
    parent_row = tree.viewport().grab(tree.visualItemRect(root)).toImage()
    child_row = tree.viewport().grab(tree.visualItemRect(child)).toImage()
    assert pixels(parent_row, t.recessed) > parent_row.width() * 5
    assert pixels(child_row, t.panel) > child_row.width() * 5
    menu = QMenu()
    menu.addAction('Normal child')
    submenu = menu.addMenu('Parent menu')
    submenu.addAction('Sub child')
    disabled = menu.addMenu('Disabled parent')
    disabled.setEnabled(False)
    unavailable = menu.addAction('Disabled action')
    unavailable.setEnabled(False)
    menu.show()
    app.processEvents()
    parent_image = menu.grab(menu.actionGeometry(submenu.menuAction())).toImage()
    disabled_image = menu.grab(menu.actionGeometry(disabled.menuAction())).toImage()
    assert pixels(parent_image, theme.disclosure_color(t)) > 12
    assert pixels(disabled_image, t.disabled) > 12
    assert pixels(disabled_image, theme.disclosure_color(t)) == 0
    assert submenu.menuAction().font().bold()
    assert not menu.actions()[0].font().bold()
    assert not disabled.isEnabled() and not unavailable.isEnabled()
    menu.close()
    tree.close()


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_disabled_primary_background_is_neutral(app, mode):
    t = theme.apply_theme(app, mode)
    button = QPushButton('Unavailable')
    button.setObjectName('primary')
    button.resize(180, 40)
    button.setEnabled(False)
    button.show()
    app.processEvents()
    image = button.grab().toImage()
    center = image.pixelColor(12, image.height() // 2)
    assert center == QColor(t.recessed)
    assert not button.isEnabled()
    button.close()


def test_menu_keyboard_cascade_checkmarks_and_click_dispatch_remain_native(app):
    theme.apply_theme(app, 'dark')
    menu = QMenu()
    ordinary = menu.addAction('Ordinary\tCtrl+1')
    ordinary.setCheckable(True)
    ordinary.setChecked(True)
    menu.addSeparator()
    submenu = menu.addMenu('Parent')
    child = submenu.addAction('Run child')
    disabled = menu.addAction('Unavailable')
    disabled.setEnabled(False)
    invoked = []
    child.triggered.connect(lambda: invoked.append('child'))
    disabled.triggered.connect(lambda: invoked.append('disabled'))
    menu.show()
    menu.setActiveAction(submenu.menuAction())
    QTest.keyClick(menu, Qt.Key.Key_Right)
    app.processEvents()
    assert submenu.isVisible()
    submenu.setActiveAction(child)
    QTest.keyClick(submenu, Qt.Key.Key_Return)
    assert invoked == ['child']
    assert ordinary.isChecked()
    menu.show()
    app.processEvents()
    QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(disabled).center())
    assert invoked == ['child']
    assert not disabled.isEnabled()
    menu.close()


def test_menu_parent_colors_update_after_theme_change_and_tree_disabled_arrow(app):
    theme.apply_theme(app, 'light')
    menu = QMenu()
    submenu = menu.addMenu('Parent')
    submenu.addAction('Leaf')
    menu.show()
    app.processEvents()
    light = menu.grab(menu.actionGeometry(submenu.menuAction())).toImage()
    theme.apply_theme(app, 'dark')
    app.processEvents()
    dark = menu.grab(menu.actionGeometry(submenu.menuAction())).toImage()
    assert pixels(light, theme.disclosure_color(theme.LIGHT)) > 20
    assert pixels(dark, theme.disclosure_color(theme.DARK)) > 20
    assert dark != light
    menu.close()
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    root = QTreeWidgetItem(tree, ['Parent'])
    QTreeWidgetItem(root, ['Child'])
    tree.setEnabled(False)
    theme.style_hierarchy(tree)
    tree.show()
    app.processEvents()
    rect = tree.visualItemRect(root).adjusted(-tree.indentation(), 0, 0, 0)
    rect.setWidth(tree.indentation())
    image = tree.viewport().grab(rect).toImage()
    assert pixels(image, theme.DARK.disabled) > 8
    assert pixels(image, theme.disclosure_color(theme.DARK)) == 0
    tree.close()
