from __future__ import annotations
import numpy as np
from ..ui.theme import color
from PySide6.QtCore import Qt, Signal, QEvent, QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import QWidget


from .image import qimage


class Canvas(QWidget):
    opened = Signal()
    dropped = Signal(str)
    geometry_changed = Signal(object)
    stroke_finished = Signal(dict)
    preview_dragging = Signal(bool)
    stroke_changed = Signal()
    committed = Signal()
    zoom_changed = Signal(str)
    sampled = Signal(object)
    clone_sampled = Signal(object)
    color_sampled = Signal(object)
    viewport_changed = Signal()
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.image = None
        self.reference_size = None
        self.to_source = None
        self.to_display = None
        self.brush_scale = 1.0
        self.overlay = None
        self.zoom = 1.0
        self.offset = QPointF()
        self.tool = 'view'
        self.clone_source = None
        self.clone_offset = None
        self.crop = None
        self.crop_drag_origin = None
        self.ratio = None
        self.brush_radius = 0.045
        self.erase = False
        self.start = None
        self.end = None
        self.stroke = None
        self.pan = None
        self.pointer = None
        self.mask_geometry = None
        self.before = None
        self.split = False
        self.split_position = 0.5
        self.split_drag = False
        self.clipping = None
        self.show_shadows = self.show_highlights = False
        self.detail = None
        self.setMinimumSize(400, 380)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_image(self, rgb):
        self.image = rgb if isinstance(rgb, QImage) else qimage(rgb)
        self.update()

    def set_before(self, rgb, enabled=False):
        self.before = (
            (rgb if isinstance(rgb, QImage) else qimage(rgb))
            if enabled and rgb is not None
            else None
        )
        self.split = enabled
        self.update()

    def set_detail(self, layer):
        """Original-resolution tiles drawn over the preview (``viewport.DetailLayer`` or None)."""
        self.detail = layer
        self.update()

    def set_clipping(self, rgb, shadows=False, highlights=False):
        self.clipping = None
        self.show_shadows, self.show_highlights = shadows, highlights
        if shadows or highlights:
            data = np.zeros((*rgb.shape[:2], 4), np.uint8)
            if shadows:
                data[np.max(rgb, axis=2) <= 0.005] = [75, 134, 255, 210]
            if highlights:
                data[np.max(rgb, axis=2) >= 0.995] = [255, 75, 90, 210]
            self.clipping = QImage(
                data.data,
                data.shape[1],
                data.shape[0],
                data.strides[0],
                QImage.Format.Format_RGBA8888,
            ).copy()
        self.update()

    def set_prepared_clipping(self, image, shadows=False, highlights=False):
        self.clipping = image
        self.show_shadows, self.show_highlights = shadows, highlights
        self.update()

    def set_overlay(self, alpha):
        if alpha is None:
            self.overlay = None
        else:
            data = np.zeros((*alpha.shape, 4), np.uint8)
            data[..., :3] = [155, 122, 255]
            data[..., 3] = np.clip(alpha * 105, 0, 105).astype(np.uint8)
            self.overlay = QImage(
                data.data,
                data.shape[1],
                data.shape[0],
                data.strides[0],
                QImage.Format.Format_RGBA8888,
            ).copy()
        self.update()

    def image_rect(self):
        if self.image is None:
            return QRectF()
        iw, ih = self.reference_size or (self.image.width(), self.image.height())
        scale = min((self.width() - 64) / iw, (self.height() - 64) / ih) * self.zoom
        w, h = iw * scale, ih * scale
        return QRectF(
            (self.width() - w) / 2 + self.offset.x(),
            (self.height() - h) / 2 + self.offset.y(),
            w,
            h,
        )

    def view_pos(self, p):
        r = self.image_rect()
        return [
            float(np.clip((p.x() - r.x()) / max(1, r.width()), 0, 1)),
            float(np.clip((p.y() - r.y()) / max(1, r.height()), 0, 1)),
        ]

    def pos(self, p):
        point = self.view_pos(p)
        return np.clip(self.to_source(point), 0, 1).tolist() if self.to_source else point

    def screen(self, p):
        if self.to_display:
            p = self.to_display(p)
        r = self.image_rect()
        return QPointF(r.x() + p[0] * r.width(), r.y() + p[1] * r.height())

    def inside_crop(self, position):
        if self.tool != 'crop' or self.crop is None or not self.image_rect().contains(position):
            return False
        x, y = self.pos(position)
        return self.crop[0] <= x <= self.crop[2] and self.crop[1] <= y <= self.crop[3]

    def crop_cursor(self, position):
        if self.tool == 'crop':
            self.setCursor(
                Qt.CursorShape.SizeAllCursor
                if self.crop_drag_origin is not None or self.inside_crop(position)
                else Qt.CursorShape.CrossCursor
            )

    def move_crop(self, position):
        self.end = self.pos(position)
        left, top, right, bottom = self.crop_drag_origin
        # Always translate from the press snapshot, never from the previous
        # frame: boundary clamping cannot accumulate drift or resize the box.
        dx = min(1 - right, max(-left, self.end[0] - self.start[0]))
        dy = min(1 - bottom, max(-top, self.end[1] - self.start[1]))
        moved = [left + dx, top + dy, right + dx, bottom + dy]
        if moved != self.crop:
            self.crop = moved
            self.geometry_changed.emit([moved[:2], moved[2:]])

    def fit(self):
        self.zoom, self.offset = 1.0, QPointF()
        self.zoom_changed.emit('适应窗口')
        self.viewport_changed.emit()
        self.update()

    def actual_size(self):
        if self.image:
            iw, ih = self.reference_size or (self.image.width(), self.image.height())
            fit = min((self.width() - 64) / iw, (self.height() - 64) / ih)
            self.zoom = 1 / (fit * self.devicePixelRatioF())
            self.offset = QPointF()
            self.zoom_changed.emit('原图 100%')
            self.viewport_changed.emit()
            self.update()

    def wheelEvent(self, e):
        if self.image is None:
            return
        self.zoom_by(1.15 if e.angleDelta().y() > 0 else 1 / 1.15, e.position())

    def zoom_by(self, factor, position):
        old_zoom = self.zoom
        iw, ih = self.reference_size or (self.image.width(), self.image.height())
        fit = min((self.width() - 64) / iw, (self.height() - 64) / ih)
        self.zoom = float(np.clip(self.zoom * factor, 0.3, max(12, 8 / fit)))
        center = QPointF(self.width() / 2, self.height() / 2)
        relative = position - center
        self.offset = relative - (relative - self.offset) * (self.zoom / old_zoom)
        hint = '中键平移'
        self.zoom_changed.emit(
            f'原图 {fit * self.zoom * self.devicePixelRatioF() * 100:.0f}% · {hint}'
        )
        self.viewport_changed.emit()
        self.update()

    def event(self, e):
        # Native zoom gestures use the same viewport transform as the mouse wheel.
        if e.type() == QEvent.Type.NativeGesture and self.image is not None:
            if e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                self.zoom_by(max(0.2, 1 + e.value()), e.position())
                return True
            if e.gestureType() == Qt.NativeGestureType.SmartZoomNativeGesture:
                self.fit()
                return True
        return super().event(e)

    def mousePressEvent(self, e):
        if self.image is None:
            if e.button() == Qt.MouseButton.LeftButton:
                self.opened.emit()
            return
        if self.start is not None or self.pan is not None or self.split_drag:
            return  # another button must not replace an active gesture
        if (
            e.button() == Qt.MouseButton.LeftButton
            and self.tool in ('sample', 'color')
            and self.image_rect().contains(e.position())
        ):
            (self.sampled if self.tool == 'sample' else self.color_sampled).emit(
                self.pos(e.position())
            )
            return
        if e.button() == Qt.MouseButton.LeftButton and self.split:
            line = self.image_rect().left() + self.split_position * self.image_rect().width()
            if abs(e.position().x() - line) < 20:
                self.split_drag = True
                return
        if e.button() == Qt.MouseButton.MiddleButton or (
            self.tool == 'view' and e.button() == Qt.MouseButton.LeftButton
        ):
            self.pan = e.position()
            return
        if e.button() != Qt.MouseButton.LeftButton or not self.image_rect().contains(e.position()):
            return
        if self.tool == 'clone' and e.modifiers() & Qt.KeyboardModifier.AltModifier:
            self.clone_source = self.pos(e.position())
            self.clone_offset = None
            self.clone_sampled.emit(self.clone_source)
            self.update()
            return
        if self.tool == 'clone' and self.clone_source is None:
            self.clone_sampled.emit(None)
            return
        self.start = self.pos(e.position())
        self.end = self.start[:]
        self.crop_drag_origin = self.crop[:] if self.inside_crop(e.position()) else None
        self.preview_dragging.emit(True)
        self.crop_cursor(e.position())
        if self.tool in ('brush', 'heal', 'clone'):
            self.stroke = dict(points=[self.start], radius=self.brush_radius, erase=self.erase)
            if self.tool in ('heal', 'clone'):
                self.stroke['kind'] = self.tool
            if self.tool == 'clone':
                if self.clone_offset is None:
                    self.clone_offset = [a - b for a, b in zip(self.clone_source, self.start)]
                self.stroke['offset'] = self.clone_offset[:]
        self.update()

    def mouseMoveEvent(self, e):
        self.pointer = e.position()
        if self.split_drag:
            self.split_position = self.view_pos(e.position())[0]
        elif self.pan is not None:
            self.offset += e.position() - self.pan
            self.pan = e.position()
            self.viewport_changed.emit()
        elif self.crop_drag_origin is not None:
            self.move_crop(e.position())
        elif self.start is not None:
            self.end = self.pos(e.position())
            if self.tool == 'crop' and self.ratio:
                r = self.image_rect()
                dx = self.end[0] - self.start[0]
                dy = abs(dx) * r.width() / (self.ratio * r.height())
                sign = 1 if self.end[1] >= self.start[1] else -1
                dy = min(dy, 1 - self.start[1] if sign > 0 else self.start[1])
                self.end[1] = self.start[1] + sign * dy
                self.end[0] = (
                    self.start[0]
                    + (1 if dx >= 0 else -1) * dy * self.ratio * r.height() / r.width()
                )
            if self.stroke is not None:
                if np.linalg.norm(np.array(self.end) - self.stroke['points'][-1]) > 0.001:
                    self.stroke['points'].append(self.end)
                    self.stroke_changed.emit()
            elif self.tool in ('crop', 'linear', 'radial'):
                self.geometry_changed.emit([self.start, self.end])
        self.crop_cursor(e.position())
        self.update()

    def mouseReleaseEvent(self, e):
        if self.start is not None and e is not None and e.button() != Qt.MouseButton.LeftButton:
            return
        if self.split_drag:
            self.split_drag = False
            return
        if self.pan is not None:
            self.pan = None
            self.viewport_changed.emit()
            return
        if self.crop_drag_origin is not None:
            if e is not None:
                self.move_crop(e.position())
        elif self.start is not None:
            if self.stroke is not None:
                self.stroke_finished.emit(self.stroke)
            elif (
                self.tool in ('crop', 'linear', 'radial')
                and np.linalg.norm(np.array(self.end) - self.start) > 0.01
            ):
                self.geometry_changed.emit([self.start, self.end])
        active = self.start is not None
        self.start = self.end = self.stroke = None
        self.crop_drag_origin = None
        if active:
            self.preview_dragging.emit(False)
            # The completed stroke/geometry was committed while the gesture was
            # still active. Commit once after exiting the transaction.
            self.committed.emit()
        if e is not None:
            self.crop_cursor(e.position())
        self.update()

    def resizeEvent(self, event):
        self.viewport_changed.emit()
        super().resizeEvent(event)

    def leaveEvent(self, _):
        self.pointer = None
        self.update()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and e.mimeData().urls()[0].isLocalFile():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if len(paths) > 1:
            self.files_dropped.emit(paths)
        elif paths:
            self.dropped.emit(paths[0])

    def draw_tiles(self, p, r, tiles, clipping=False):
        """Draw finished detail tiles that fall inside the widget over the preview."""
        width, height = self.detail.size
        dpr = self.devicePixelRatioF()
        sx, sy = r.width() * dpr / width, r.height() * dpr / height
        ox, oy = r.x() * dpr, r.y() * dpr
        view = QRectF(self.rect())
        p.save()
        # Neighbouring tiles share edges snapped to whole device pixels: no uncovered seam
        # column between them, and at 100 % each tile is a 1:1 copy without resampling.
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for tile in tiles.values():
            if tile.image is None:
                continue
            left, top = round(ox + tile.x0 * sx), round(oy + tile.y0 * sy)
            right, bottom = (
                round(ox + (tile.x0 + tile.width) * sx),
                round(oy + (tile.y0 + tile.height) * sy),
            )
            target = QRectF(left / dpr, top / dpr, (right - left) / dpr, (bottom - top) / dpr)
            if not target.intersects(view):
                continue
            p.drawImage(target, tile.image)
            if clipping:
                for shown, warning in (
                    (self.show_shadows, tile.shadows),
                    (self.show_highlights, tile.highlights),
                ):
                    if shown and warning is not None:
                        p.drawImage(target, warning)
        p.restore()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), color('canvas', self))
        if self.image is None:
            box = QRectF(self.width() / 2 - 195, self.height() / 2 - 100, 390, 200)
            p.setPen(QPen(color('border', self), 1, Qt.PenStyle.DashLine))
            p.setBrush(color('canvas', self))
            p.drawRoundedRect(box, 14, 14)
            p.setPen(color('text', self))
            font = p.font()
            font.setPixelSize(18)
            p.setFont(font)
            p.drawText(box.adjusted(0, 26, 0, -95), Qt.AlignmentFlag.AlignCenter, '打开照片')
            font.setPixelSize(14)
            p.setFont(font)
            p.setPen(color('secondary', self))
            p.drawText(
                box.adjusted(0, 82, 0, -45),
                Qt.AlignmentFlag.AlignCenter,
                '点击打开，或将原片拖到这里',
            )
            p.setPen(color('secondary', self))
            p.drawText(
                box.adjusted(0, 132, 0, -16),
                Qt.AlignmentFlag.AlignCenter,
                '',
            )
            return
        r = self.image_rect()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(r, self.image)
        if self.clipping is not None:
            p.drawImage(r, self.clipping)
        if self.detail is not None:
            self.draw_tiles(p, r, self.detail.main, True)
        if self.split and self.before is not None:
            split_x = r.left() + self.split_position * r.width()
            p.save()
            p.setClipRect(
                QRectF(r.left(), r.top(), r.width() * self.split_position, r.height()),
                Qt.ClipOperation.IntersectClip,
            )
            p.drawImage(r, self.before)
            if self.detail is not None and self.detail.before is not None:
                self.draw_tiles(p, r, self.detail.before)
            p.restore()
            p.setPen(QPen(QColor('#f1f2e8'), 1.5))
            p.drawLine(QPointF(split_x, r.top()), QPointF(split_x, r.bottom()))
            p.setBrush(QColor('#242827'))
            p.drawEllipse(QPointF(split_x, r.center().y()), 13, 13)
            p.drawText(
                QRectF(split_x - 12, r.center().y() - 10, 24, 20), Qt.AlignmentFlag.AlignCenter, '↔'
            )
            for text, at in [('原片', r.left() + 12), ('调整后', r.right() - 70)]:
                rect = QRectF(at, r.top() + 12, 58, 24)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(18, 21, 24, 200))
                p.drawRoundedRect(rect, 4, 4)
                p.setPen(QColor('#e1e4dd'))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        if self.overlay is not None:
            p.drawImage(r, self.overlay)
        # Round only the four corner pixels. A full rounded clipping path makes
        # Qt composite every image pixel through a mask on each preview frame.
        # The small corner paths retain native fast image/tile painting at 100%.
        corner = QPainterPath(QPointF(0, 0))
        corner.lineTo(8, 0)
        corner.cubicTo(3.58, 0, 0, 3.58, 0, 8)
        corner.closeSubpath()
        for point, sx, sy in (
            (r.topLeft(), 1, 1),
            (r.topRight(), -1, 1),
            (r.bottomLeft(), 1, -1),
            (r.bottomRight(), -1, -1),
        ):
            p.save()
            p.translate(point)
            p.scale(sx, sy)
            p.fillPath(corner, color('canvas', self))
            p.restore()
        crop = self.crop
        if self.tool == 'crop' and self.start is not None and self.crop_drag_origin is None:
            crop = [
                min(self.start[0], self.end[0]),
                min(self.start[1], self.end[1]),
                max(self.start[0], self.end[0]),
                max(self.start[1], self.end[1]),
            ]
        if crop:
            area = QRectF(self.screen(crop[:2]), self.screen(crop[2:]))
            shadow = QPainterPath()
            shadow.addRect(r)
            hole = QPainterPath()
            hole.addRect(area)
            p.fillPath(shadow.subtracted(hole), QColor(0, 0, 0, 150))
            p.setPen(QPen(QColor('#e0e5ef'), 1))
            p.drawRect(area)
            for t in (1 / 3, 2 / 3):
                p.drawLine(
                    QPointF(area.x() + t * area.width(), area.top()),
                    QPointF(area.x() + t * area.width(), area.bottom()),
                )
                p.drawLine(
                    QPointF(area.left(), area.y() + t * area.height()),
                    QPointF(area.right(), area.y() + t * area.height()),
                )
        geom = [self.start, self.end] if self.start is not None else self.mask_geometry
        if geom and self.tool in ('linear', 'radial'):
            a, b = self.screen(geom[0]), self.screen(geom[1])
            p.setPen(QPen(color('accent', self), 2, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            if self.tool == 'radial':
                p.drawEllipse(QRectF(a, b).normalized())
            else:
                p.drawLine(a, b)
            p.drawEllipse(a, 5, 5)
            p.drawEllipse(b, 5, 5)
        if self.stroke:
            p.setPen(
                QPen(
                    QColor(183, 151, 255, 120),
                    max(2, self.brush_radius * self.brush_scale * min(r.width(), r.height()) * 2),
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            path = QPainterPath(self.screen(self.stroke['points'][0]))
            for pt in self.stroke['points'][1:]:
                path.lineTo(self.screen(pt))
            p.drawPath(path)
        if self.pointer and self.tool in ('brush', 'heal', 'clone') and r.contains(self.pointer):
            radius = self.brush_radius * self.brush_scale * min(r.width(), r.height())
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor('#ffffff'), 1))
            p.drawEllipse(self.pointer, radius, radius)
        if self.tool == 'clone' and self.clone_source is not None:
            source = self.clone_source
            if self.pointer and self.clone_offset is not None:
                source = [a + b for a, b in zip(self.pos(self.pointer), self.clone_offset)]
            mark = self.screen(source)
            p.setPen(QPen(QColor('#dcc48e'), 1.5))
            p.drawLine(mark - QPointF(9, 0), mark + QPointF(9, 0))
            p.drawLine(mark - QPointF(0, 9), mark + QPointF(0, 9))
            p.drawEllipse(mark, 5, 5)
