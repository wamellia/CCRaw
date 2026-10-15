"""Vector disclosure affordances and model-aware hierarchy presentation."""

from functools import lru_cache
from ..resources import bundled_path

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, Slot
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPalette
from PySide6.QtWidgets import (
    QMenu,
    QPushButton,
    QProxyStyle,
    QStyle,
    QStyleFactory,
    QStyleOptionMenuItem,
    QStyleOptionViewItem,
    QStyledItemDelegate,
)
from shiboken6 import isValid


ASSETS = bundled_path('icons')


def disclosure_color(t):
    """A stronger disclosure accent than the dark theme's filled-button blue."""
    return '#79BFFF' if t.background == '#1E1E1E' else '#175EAD'


def disclosure_path(direction, t, disabled=False):
    variant = 'dark' if t.background == '#1E1E1E' else 'light'
    state = '-disabled' if disabled else ''
    return ASSETS / f'disclosure-{direction}-{variant}{state}.svg'


@lru_cache(maxsize=8)
def disclosure_icon(direction, t):
    result = QIcon(str(disclosure_path(direction, t)))
    result.addFile(str(disclosure_path(direction, t, True)), QSize(), QIcon.Mode.Disabled)
    return result


def set_disclosure(button, expanded, t=None):
    from .theme import tokens

    direction = 'down' if expanded else 'right'
    button.setProperty('disclosureDirection', direction)
    button.setArrowType(Qt.ArrowType.NoArrow)
    button.setIcon(disclosure_icon(direction, t or tokens()))
    button.setIconSize(QSize(16, 16))


class HierarchyVisuals(QObject):
    def __init__(self, tree):
        super().__init__(tree)
        self.tree = tree
        self.refreshing = False
        model = tree.model()
        model.rowsInserted.connect(self._model_changed)
        model.rowsRemoved.connect(self._model_changed)
        model.modelReset.connect(self._model_changed)

    @Slot()
    def _model_changed(self):
        self.refresh()

    def refresh(self, t=None):
        if getattr(self, 'refreshing', True) or not isValid(self.tree):
            return
        from .theme import tokens

        t = t or tokens()
        self.refreshing = True
        try:
            pending = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
            while pending:
                item = pending.pop()
                parent = (
                    item.childCount() > 0
                    or not bool(item.flags() & Qt.ItemFlag.ItemIsSelectable)
                    or item.childIndicatorPolicy() == item.ChildIndicatorPolicy.ShowIndicator
                )
                for column in range(self.tree.columnCount()):
                    font = item.font(column)
                    font.setBold(parent)
                    item.setFont(column, font)
                    item.setForeground(column, QColor(disclosure_color(t) if parent else t.text))
                    item.setBackground(column, QColor(t.recessed if parent else t.panel))
                pending.extend(item.child(i) for i in range(item.childCount()))
        finally:
            self.refreshing = False


class HierarchyItemDelegate(QStyledItemDelegate):
    """Qt item CSS masks BackgroundRole; draw that role with the native style."""

    def __init__(self, tree):
        super().__init__(tree)
        self.native_style = QStyleFactory.create('Fusion')
        self.native_style.setParent(self)

    def paint(self, painter, option, index):
        from .theme import tokens

        t = tokens()
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        background = (
            t.selection
            if option.state & QStyle.StateFlag.State_Selected
            else option.backgroundBrush.color().name()
        )
        if (
            option.state & QStyle.StateFlag.State_MouseOver
            and not option.state & QStyle.StateFlag.State_Selected
        ):
            background = t.hover
        painter.fillRect(option.rect, QColor(background))
        option.backgroundBrush = QBrush(QColor(background))
        option.palette.setColor(
            QPalette.ColorRole.Text, QColor(index.data(Qt.ItemDataRole.ForegroundRole).color())
        )
        option.palette.setColor(QPalette.ColorRole.Highlight, QColor(t.selection))
        option.palette.setColor(QPalette.ColorRole.HighlightedText, QColor(t.text))
        option.palette.setColor(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(t.disabled)
        )
        # Selection/hover are painted above; Fusion otherwise introduces a
        # platform gradient that masks the parent's explicit background.
        option.state &= ~(
            QStyle.StateFlag.State_Selected
            | QStyle.StateFlag.State_MouseOver
            | QStyle.StateFlag.State_HasFocus
        )
        option.rect = option.rect.adjusted(6, 0, -6, 0)
        self.native_style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget
        )
        if focused and option.state & QStyle.StateFlag.State_Enabled:
            painter.save()
            painter.setPen(QColor(t.focus))
            painter.drawRect(option.rect.adjusted(0, 1, -1, -2))
            painter.restore()


def style_hierarchy(tree, t=None):
    """Attach once; data roles, labels, selection and expansion stay untouched."""
    helper = getattr(tree, '_hierarchy_visuals', None)
    if helper is None:
        helper = tree._hierarchy_visuals = HierarchyVisuals(tree)
        tree.setItemDelegate(HierarchyItemDelegate(tree))
    helper.refresh(t=t)


class HierarchyMenuStyle(QProxyStyle):
    """Keep native check/action behavior while giving submenu parents a tone."""

    def __init__(self, parent):
        # A private base avoids transferring ownership of QApplication.style().
        super().__init__(QStyleFactory.create('Fusion'))
        self.setParent(parent)

    def sizeFromContents(self, content, option, size, widget=None):
        result = super().sizeFromContents(content, option, size, widget)
        if content == QStyle.ContentsType.CT_MenuItem:
            if option.menuItemType != QStyleOptionMenuItem.MenuItemType.Separator:
                result.setHeight(max(32, result.height() + 6))
                result.setWidth(result.width() + 12)
        return result

    def drawControl(self, element, option, painter, widget=None):
        if element == QStyle.ControlElement.CE_MenuItem:
            from .theme import tokens

            t = tokens()
            enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
            parent = option.menuItemType == QStyleOptionMenuItem.MenuItemType.SubMenu
            selected = enabled and bool(option.state & QStyle.StateFlag.State_Selected)
            foreground = (disclosure_color(t) if parent else t.text) if enabled else t.disabled
            painter.save()
            background = t.selection if selected else (t.recessed if parent else t.elevated)
            painter.fillRect(option.rect, QColor(background))
            if option.menuItemType == QStyleOptionMenuItem.MenuItemType.Separator:
                painter.setPen(QColor(t.border))
                painter.drawLine(
                    option.rect.left() + 8,
                    option.rect.center().y(),
                    option.rect.right() - 8,
                    option.rect.center().y(),
                )
                painter.restore()
                return
            painter.setPen(QColor(foreground))
            font = option.font
            font.setBold(
                parent or option.menuItemType == QStyleOptionMenuItem.MenuItemType.DefaultItem
            )
            painter.setFont(font)
            rtl = option.direction == Qt.LayoutDirection.RightToLeft
            icon_column = (
                max(22, option.maxIconWidth)
                if option.maxIconWidth or option.menuHasCheckableItems
                else 0
            )
            icon_rect = QRect(option.rect.left() + 8, option.rect.center().y() - 8, 16, 16)
            if rtl:
                icon_rect.moveRight(option.rect.right() - 8)
            mode = QIcon.Mode.Normal if enabled else QIcon.Mode.Disabled
            if not option.icon.isNull():
                option.icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter, mode)
            elif option.checked:
                if option.checkType == QStyleOptionMenuItem.CheckType.Exclusive:
                    painter.setBrush(QColor(foreground))
                    painter.drawEllipse(icon_rect.adjusted(4, 4, -4, -4))
                else:
                    painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, '\u2713')
            text_rect = option.rect.adjusted(12 + icon_column, 0, -26 if parent else -12, 0)
            if rtl:
                text_rect = option.rect.adjusted(26 if parent else 12, 0, -(12 + icon_column), 0)
            parts = option.text.split('\t', 1)
            text_flags = Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextShowMnemonic
            alignment = Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft
            if len(parts) == 2:
                shortcut_width = painter.fontMetrics().horizontalAdvance(parts[1]) + 18
                shortcut_rect = QRect(text_rect)
                shortcut_rect.setLeft(shortcut_rect.right() - shortcut_width)
                painter.drawText(
                    shortcut_rect,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    parts[1],
                )
                text_rect.setRight(shortcut_rect.left() - 12)
            painter.drawText(text_rect, text_flags | alignment, parts[0])
            if parent:
                arrow_rect = QRect(option.rect.right() - 23, option.rect.center().y() - 8, 16, 16)
                if rtl:
                    arrow_rect.moveLeft(option.rect.left() + 7)
                disclosure_icon('right', t).paint(
                    painter, arrow_rect, Qt.AlignmentFlag.AlignCenter, mode
                )
            painter.restore()
            return
        super().drawControl(element, option, painter, widget)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element in (
            QStyle.PrimitiveElement.PE_IndicatorArrowRight,
            QStyle.PrimitiveElement.PE_IndicatorArrowLeft,
        ):
            from .theme import tokens

            mode = (
                QIcon.Mode.Normal
                if option.state & QStyle.StateFlag.State_Enabled
                else QIcon.Mode.Disabled
            )
            rect = option.rect
            extent = min(16, rect.width(), rect.height())
            target = rect.adjusted(
                (rect.width() - extent) // 2, (rect.height() - extent) // 2, 0, 0
            )
            target.setSize(QSize(extent, extent))
            disclosure_icon('right', tokens()).paint(
                painter, target, Qt.AlignmentFlag.AlignCenter, mode
            )
            return
        super().drawPrimitive(element, option, painter, widget)


def style_menu(menu, app):
    shared = getattr(app, '_hierarchy_menu_style', None)
    if shared is None:
        shared = app._hierarchy_menu_style = HierarchyMenuStyle(app)
    if menu.style() is not shared and not getattr(menu, '_hierarchy_menu_attached', False):
        menu.setStyle(shared)
        menu._hierarchy_menu_attached = True
    for action in menu.actions():
        if action.menu():
            font = action.font()
            font.setBold(True)
            action.setFont(font)


class DisclosureFilter(QObject):
    def eventFilter(self, watched, event):
        # Context menus and newly created dropdowns also get the same treatment.
        if isinstance(watched, QPushButton) and event.type() == QEvent.Type.Show and watched.menu():
            watched.setProperty('hasMenu', True)
            watched.style().unpolish(watched)
            watched.style().polish(watched)
        if isinstance(watched, QMenu) and event.type() == QEvent.Type.Show:
            style_menu(watched, self.parent())
        if isinstance(watched, QMenu) and event.type() == QEvent.Type.Paint:
            # Qt's application-wide QWidget color rule intercepts CE_MenuItem
            # and replaces its palette. Paint from the unchanged QAction style
            # options so parent and disabled tones remain explicit. QMenu still
            # owns layout, keyboard navigation, selection and action dispatch.
            from .theme import tokens

            t = tokens()
            style = getattr(self.parent(), '_hierarchy_menu_style', None)
            if style is None:
                style_menu(watched, self.parent())
                style = self.parent()._hierarchy_menu_style
            painter = QPainter(watched)
            painter.setClipRegion(event.region())
            painter.fillRect(watched.rect(), QColor(t.elevated))
            painter.setPen(QColor(t.border))
            painter.drawRect(watched.rect().adjusted(0, 0, -1, -1))
            for action in watched.actions():
                if not action.isVisible():
                    continue
                option = QStyleOptionMenuItem()
                watched.initStyleOption(option, action)
                option.rect = watched.actionGeometry(action)
                if option.rect.intersects(watched.rect()):
                    style.drawControl(QStyle.ControlElement.CE_MenuItem, option, painter, watched)
            painter.end()
            return True
        return False


def install_disclosure_filter(app):
    if not hasattr(app, '_disclosure_filter'):
        app._disclosure_filter = DisclosureFilter(app)
        app.installEventFilter(app._disclosure_filter)
