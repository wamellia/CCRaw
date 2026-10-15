"""Aspect-correct before/after display for locally rendered edit proposals."""

from PySide6.QtCore import Qt, QSize, QEvent
from PySide6.QtGui import QPixmap, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QDialogButtonBox,
    QPushButton,
)


class PreviewImage(QLabel):
    def __init__(self, path):
        super().__init__()
        self.original = QPixmap(str(path))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(160, 120)
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.Base)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.scale_pixmap()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            self.scale_pixmap()
        return result

    def scale_pixmap(self):
        if not hasattr(self, 'original') or self.original.isNull():
            return
        dpr = self.devicePixelRatioF()
        size = QSize(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        pixmap = self.original.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(dpr)
        self.setPixmap(pixmap)


class PreviewDialog(QDialog):
    def __init__(self, before, after, parent=None, *, native=None):
        super().__init__(parent)
        self.setWindowTitle('方案预览')
        self.resize(960, 580)
        self.setMinimumSize(640, 380)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.images = []
        for title, path in [('当前调整', before), ('方案效果', after)]:
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            image = PreviewImage(path)
            self.images.append(image)
            column.addWidget(image, 1)
            row.addLayout(column, 1)
        layout.addLayout(row, 1)
        layout.addWidget(QLabel('适屏预览 · 应用方案时使用原片全分辨率'))
        if native is not None:
            self.native_button = QPushButton('加载原像素对比')
            self.native_button.clicked.connect(native)
            layout.addWidget(self.native_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
