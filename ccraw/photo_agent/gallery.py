"""Responsive photo cards with explicit, colour-preserving image rendering."""

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QImageReader, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QListWidget, QStyle, QStyledItemDelegate

from ..ui import theme

DETAIL_ROLE = Qt.ItemDataRole.UserRole + 1


def thumbnail(path):
    # Decode the bounded preview once; painting/resizing never reads the original.
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and max(size.width(), size.height()) > 512:
        reader.setScaledSize(size.scaled(QSize(512, 512), Qt.AspectRatioMode.KeepAspectRatio))
    return QPixmap.fromImage(reader.read())


class PhotoDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return self.parent().gridSize()

    def paint(self, painter, option, index):
        # Transparent list-item palettes do not reliably represent the app theme.
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        card = QRectF(option.rect).adjusted(6, 6, -6, -6)
        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(theme.color('accent' if selected else 'border'), 2 if selected else 1))
        painter.setBrush(theme.color('selection' if selected else 'hover' if hovered else 'panel'))
        painter.drawRoundedRect(card, 8, 8)
        side = max(1, card.width() - 20)
        frame = QRectF(card.left() + 10, card.top() + 10, side, side)
        painter.fillRect(frame, theme.color('canvas'))
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            # A single scale factor keeps every pixel of all four image edges visible.
            scale = min(frame.width() / pixmap.width(), frame.height() / pixmap.height())
            width, height = pixmap.width() * scale, pixmap.height() * scale
            target = QRectF(
                frame.center().x() - width / 2, frame.center().y() - height / 2, width, height
            )
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        else:
            painter.setPen(theme.color('secondary'))
            painter.drawText(frame, Qt.AlignmentFlag.AlignCenter, '暂无预览')
        painter.setFont(option.font)
        metrics = option.fontMetrics
        title_rect = QRectF(frame.left(), frame.bottom() + 8, frame.width(), metrics.height())
        painter.setPen(theme.color('text'))
        title = metrics.elidedText(
            str(index.data(Qt.ItemDataRole.DisplayRole) or ''),
            Qt.TextElideMode.ElideMiddle,
            int(title_rect.width()),
        )
        painter.drawText(
            title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title
        )
        detail_rect = title_rect.translated(0, metrics.height() + 4)
        painter.setPen(theme.color('secondary'))
        detail = metrics.elidedText(
            str(index.data(DETAIL_ROLE) or ''),
            Qt.TextElideMode.ElideRight,
            int(detail_rect.width()),
        )
        painter.drawText(
            detail_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, detail
        )
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme.color('focus'), 1, Qt.PenStyle.DotLine))
            painter.drawRoundedRect(card.adjusted(3, 3, -3, -3), 6, 6)
        painter.restore()


class PhotoGallery(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('agentGallery')
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setUniformItemSizes(True)
        self.setWordWrap(False)
        self.setSpacing(0)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setItemDelegate(PhotoDelegate(self))
        self.layout_timer = QTimer(self)
        self.layout_timer.setSingleShot(True)
        self.layout_timer.timeout.connect(self.reflow)
        self.reflow()

    def viewportEvent(self, event):
        result = super().viewportEvent(event)
        if event.type() == QEvent.Type.Resize and hasattr(self, 'layout_timer'):
            self.layout_timer.start(0)
        return result

    def reflow(self):
        # Keep Qt's item-layout inset and integer rounding inside the viewport.
        available = max(1, self.viewport().width() - 8)
        columns = max(1, available // 220)
        width = available // columns
        size = QSize(width, width + 2 * self.fontMetrics().height() + 12)
        if size != self.gridSize():
            self.setGridSize(size)
            self.doItemsLayout()
