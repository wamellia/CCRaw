from __future__ import annotations
import numpy as np
from ..ui.theme import color
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import QWidget


class Histogram(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(105)
        self.hist = None

    def set_image(self, rgb):
        values = (np.clip(rgb[::3, ::3], 0, 1) * 255).astype(np.uint8)
        self.hist = [np.bincount(values[..., c].ravel(), minlength=256) for c in range(3)]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), color('recessed', self))
        if self.hist is None:
            p.setPen(color('secondary', self))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'RGB 直方图')
            return
        peak = max(max(np.percentile(h, 99), 1) for h in self.hist)
        for h, channel_color in zip(self.hist, ['#CF7580', '#70A487', '#6C98CE']):
            path = QPainterPath(QPointF(0, self.height()))
            for i, val in enumerate(h):
                path.lineTo(
                    i * self.width() / 255, self.height() - min(val / peak, 1) * (self.height() - 8)
                )
            path.lineTo(self.width(), self.height())
            path.closeSubpath()
            c = QColor(channel_color)
            c.setAlpha(80)
            p.fillPath(path, c)
            p.setPen(QPen(QColor(channel_color), 1))
            p.drawPath(path)
