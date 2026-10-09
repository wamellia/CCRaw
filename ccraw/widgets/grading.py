from __future__ import annotations
import numpy as np
from ..ui.theme import color
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QConicalGradient, QRadialGradient
from PySide6.QtWidgets import QWidget


class ColorWheel(QWidget):
    changed = Signal(float, float)
    committed = Signal()
    activated = Signal()
    preview_dragging = Signal(bool)

    def __init__(self, title):
        super().__init__()
        self.title = title
        self.hue, self.saturation = 0.0, 0.0
        self.active = False
        self._dragging = False
        self.setMinimumSize(95, 140)
        self.setMaximumHeight(155)
        self.setToolTip('拖动色轮：方向控制色相，离中心越远饱和度越高；双击归零。')

    def center_radius(self):
        return QPointF(self.width() / 2, (self.height() - 32) / 2), min(
            self.width() - 20, self.height() - 48
        ) / 2

    def set_value(self, hue, saturation):
        self.hue, self.saturation = hue, saturation
        self.update()

    def choose(self, p):
        center, radius = self.center_radius()
        delta = p - center
        self.hue = float(np.degrees(np.arctan2(-delta.y(), delta.x())) % 360)
        self.saturation = float(min(100, np.hypot(delta.x(), delta.y()) / radius * 100))
        self.changed.emit(self.hue, self.saturation)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
            center, radius = self.center_radius()
            if np.hypot(e.position().x() - center.x(), e.position().y() - center.y()) <= radius + 6:
                if not self._dragging:
                    self.preview_dragging.emit(True)
                self._dragging = True
                self.choose(e.position())

    def mouseMoveEvent(self, e):
        if self._dragging:
            self.choose(e.position())

    def mouseReleaseEvent(self, _):
        if self._dragging:
            self._dragging = False
            self.preview_dragging.emit(False)
            self.committed.emit()

    def mouseDoubleClickEvent(self, _):
        if self._dragging:
            self._dragging = False
            self.preview_dragging.emit(False)
        self.saturation = 0.0
        self.changed.emit(self.hue, 0.0)
        self.committed.emit()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        center, radius = self.center_radius()
        gradient = QConicalGradient(center, 0)
        for i in range(7):
            gradient.setColorAt(i / 6, QColor.fromHsvF((i / 6) % 1, 0.48, 0.82))
        p.setPen(QPen(color('accent' if self.active else 'border', self), 2 if self.active else 1))
        p.setBrush(gradient)
        p.drawEllipse(center, radius, radius)
        radial = QRadialGradient(center, radius)
        radial.setColorAt(0, color('recessed', self))
        radial.setColorAt(1, QColor(239, 239, 241, 0))
        p.setBrush(radial)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(center, radius, radius)
        angle = np.deg2rad(self.hue)
        marker = center + QPointF(np.cos(angle), -np.sin(angle)) * radius * self.saturation / 100
        p.setPen(QPen(color('text', self), 4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(marker, 4, 4)
        p.setPen(QPen(color('elevated', self), 1.5))
        p.drawEllipse(marker, 4, 4)
        p.setPen(color('text' if self.active else 'secondary', self))
        p.drawText(
            QRectF(0, self.height() - 27, self.width(), 20),
            Qt.AlignmentFlag.AlignCenter,
            self.title,
        )
