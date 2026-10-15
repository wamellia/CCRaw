from __future__ import annotations
import copy
import numpy as np
from ..curves import evaluate
from ..ui.theme import color, tokens
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import QWidget


class CurveEditor(QWidget):
    changed = Signal(list)
    selected = Signal(int, int)
    committed = Signal()
    preview_dragging = Signal(bool)

    def __init__(self):
        super().__init__()
        self.points = [[0.0, 0.0], [1.0, 1.0]]
        self.channel = 'RGB'
        self.mode = 'smooth'
        self.current_x = 128
        self.drag = None
        self.setMinimumHeight(215)
        self.setToolTip('在任意亮度位置按下并拖动 · 右键删除节点 · 输入/输出支持 0–255 精确调整')

    def area(self):
        return QRectF(18, 12, self.width() - 36, self.height() - 30)

    def screen(self, point):
        a = self.area()
        return QPointF(a.left() + point[0] * a.width(), a.bottom() - point[1] * a.height())

    def value(self, pos):
        a = self.area()
        return [
            float(np.clip((pos.x() - a.left()) / a.width(), 0, 1)),
            float(np.clip((a.bottom() - pos.y()) / a.height(), 0, 1)),
        ]

    def set_points(self, points, channel='RGB', mode='smooth'):
        self.points = copy.deepcopy(points)
        self.mode = mode
        self.channel = channel
        self.update()

    def hit(self, pos):
        for i, point in enumerate(self.points):
            if (self.screen(point) - pos).manhattanLength() < 16:
                return i
        return None

    def report(self, x):
        self.current_x = round(x * 255)
        self.selected.emit(self.current_x, round(float(evaluate(self.points, x, self.mode)) * 255))

    def insert(self, x, y):
        x = round(x * 255) / 255
        nearest = min(range(len(self.points)), key=lambda i: abs(self.points[i][0] - x))
        if abs(self.points[nearest][0] - x) < 0.001:
            self.points[nearest][1] = y
            return nearest
        if len(self.points) >= 256:
            return nearest
        self.points.append([x, y])
        self.points.sort()
        return next(i for i, p in enumerate(self.points) if p[0] == x)

    def set_output(self, x, y):
        self.insert(x / 255, y / 255)
        self.changed.emit(copy.deepcopy(self.points))
        self.report(x / 255)
        self.committed.emit()
        self.update()

    def mousePressEvent(self, event):
        if not self.area().contains(event.position()):
            return
        i = self.hit(event.position())
        if event.button() == Qt.MouseButton.RightButton:
            if i is not None and 0 < i < len(self.points) - 1:
                self.points.pop(i)
                self.changed.emit(copy.deepcopy(self.points))
                self.committed.emit()
                self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.drag is None:
            self.preview_dragging.emit(True)
        x, y = self.value(event.position())
        if i is None:
            i = self.insert(x, y)
        self.drag = i
        self.report(self.points[i][0])
        self.changed.emit(copy.deepcopy(self.points))
        self.update()

    def mouseDoubleClickEvent(self, event):
        self.mousePressEvent(event)
        self.mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        x, y = self.value(event.position())
        if self.drag is not None:
            # Lock the input tone while dragging: any brightness can be pulled
            # vertically without accidentally changing neighboring tone anchors.
            self.points[self.drag][1] = y
            self.changed.emit(copy.deepcopy(self.points))
            self.report(self.points[self.drag][0])
            self.update()
        else:
            self.report(x)

    def mouseReleaseEvent(self, _):
        if self.drag is not None:
            self.drag = None
            self.preview_dragging.emit(False)
            self.committed.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = self.area()
        p.fillRect(a, color('recessed', self))
        p.setPen(QPen(color('border', self), 1))
        for i in range(5):
            t = i / 4
            p.drawLine(
                QPointF(a.left() + t * a.width(), a.top()),
                QPointF(a.left() + t * a.width(), a.bottom()),
            )
            p.drawLine(
                QPointF(a.left(), a.top() + t * a.height()),
                QPointF(a.right(), a.top() + t * a.height()),
            )
        p.setPen(QPen(color('track', self), 1, Qt.PenStyle.DashLine))
        p.drawLine(a.bottomLeft(), a.topRight())
        path = QPainterPath(self.screen(self.points[0]))
        axis = np.linspace(0, 1, 512)
        for point in zip(axis, evaluate(self.points, axis, self.mode)):
            path.lineTo(self.screen(point))
        p.setPen(
            QPen(
                QColor(
                    tokens(self).active_track
                    if self.channel == 'RGB'
                    else {'R': '#CF7580', 'G': '#70A487', 'B': '#6C98CE'}[self.channel]
                ),
                2,
            )
        )
        p.drawPath(path)
        for point in self.points:
            p.setBrush(color('elevated', self))
            p.drawEllipse(self.screen(point), 4, 4)
