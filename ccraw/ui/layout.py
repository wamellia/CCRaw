"""Screen-bounded geometry and wrapping action bars for all three workspaces."""

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSplitter, QWidget, QSizePolicy, QHBoxLayout, QLabel


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=8, spacing=6):
        super().__init__(parent)
        self.items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect, test):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row = area.x(), area.y(), 0
        for item in self.items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            size = item.sizeHint()
            width = min(size.width(), max(1, area.width()))
            if x > area.x() and x + width > area.right() + 1:
                x, y, row = area.x(), y + row + self.spacing(), 0
            if not test:
                item.setGeometry(QRect(x, y, width, size.height()))
            x += width + self.spacing()
            row = max(row, size.height())
        return y + row - rect.y() + margins.bottom()


def wrap_toolbar(widget):
    old = widget.layout()
    if not isinstance(old, QHBoxLayout):
        return
    children = []
    while old.count():
        item = old.takeAt(0)
        if item.widget():
            children.append(item.widget())
    temporary = QWidget()
    temporary.setLayout(old)
    flow = FlowLayout(widget)
    for child in children:
        if isinstance(child, QLabel):
            child.setMaximumWidth(300)
            child.setWordWrap(False)
            child.setMinimumWidth(
                min(300, child.fontMetrics().horizontalAdvance(child.text()) + 12)
            )
            child.setToolTip(child.text())
        flow.addWidget(child)
    widget.setMinimumHeight(48)
    widget.setMaximumHeight(16777215)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    temporary.deleteLater()


def clamp_window(window, available=None):
    screen = window.screen()
    available = available or screen.availableGeometry()
    # Include frame/title space at 150/200%; a 900x600 logical workspace remains
    # available when the screen permits, smaller screens can still scroll.
    width, height = max(320, available.width() - 16), max(240, available.height() - 40)
    window.setMinimumSize(min(900, width), min(600, height))
    window.resize(min(window.width(), width), min(window.height(), height))
    x = max(available.left(), min(window.x(), available.right() - window.width()))
    y = max(available.top(), min(window.y(), available.bottom() - window.height() - 24))
    window.move(x, y)
    for splitter in window.findChildren(QSplitter):
        for i in range(splitter.count()):
            child = splitter.widget(i)
            if splitter.orientation() == Qt.Orientation.Horizontal:
                child.setMinimumWidth(min(child.minimumWidth(), 280))
        sizes = splitter.sizes()
        if sizes and sum(sizes) > max(1, splitter.width()) * 2:
            factor = max(1, splitter.width()) / sum(sizes)
            splitter.setSizes([max(40, int(size * factor)) for size in sizes])
