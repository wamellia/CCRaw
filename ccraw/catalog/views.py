"""Aspect-correct candidate comparison with linked relative pan/zoom and Survey."""

from pathlib import Path
import threading

from PySide6.QtCore import Signal, QThread, QTimer
from PySide6.QtGui import QImage, QPixmap, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QGraphicsView,
    QGraphicsScene,
    QWidget,
    QGridLayout,
    QScrollArea,
)


class CandidateView(QGraphicsView):
    changed = Signal(float, object)

    def __init__(self, path):
        super().__init__()
        self.zoom, self.relative_center = 1.0, (0.5, 0.5)
        self.native_holder = None
        self.native_size = None
        self.tile_items = {}
        from ..viewport import TileStore

        self.tile_store = TileStore(max_bytes=64 * 2**20)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setMinimumSize(180, 160)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.pixmap = QPixmap(str(path))
        self.preview_item = None
        if not self.pixmap.isNull():
            self.preview_item = self.scene.addPixmap(self.pixmap)
            self.scene.setSceneRect(self.pixmap.rect())
        self.set_state(1, (0.5, 0.5))

    def set_image(self, rgb):
        import numpy as np

        data = np.ascontiguousarray(np.round(np.clip(rgb, 0, 1) * 255).astype(np.uint8))
        image = QImage(
            data.data, data.shape[1], data.shape[0], data.strides[0], QImage.Format.Format_RGB888
        ).copy()
        self.pixmap = QPixmap.fromImage(image)
        self.scene.clear()
        self.scene.addPixmap(self.pixmap)
        self.scene.setSceneRect(self.pixmap.rect())
        self.set_state(self.zoom, self.relative_center)

    def set_state(self, zoom, center):
        self.zoom = max(0.01, min(256, float(zoom)))
        self.relative_center = tuple(max(0, min(1, float(v))) for v in center)
        if self.pixmap.isNull() and not self.native_size:
            return
        width, height = self.native_size or (self.pixmap.width(), self.pixmap.height())
        self.resetTransform()
        fitted = min(
            max(1, self.viewport().width()) / width, max(1, self.viewport().height()) / height
        )
        self.scale(fitted * self.zoom, fitted * self.zoom)
        self.centerOn(width * self.relative_center[0], height * self.relative_center[1])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.set_state(self.zoom, self.relative_center)
        if self.native_holder:
            self.changed.emit(self.zoom, self.relative_center)

    def wheelEvent(self, event):
        self.set_state(
            self.zoom * (1.2 if event.angleDelta().y() > 0 else 1 / 1.2), self.relative_center
        )
        self.changed.emit(self.zoom, self.relative_center)
        event.accept()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if not self.pixmap.isNull():
            point = self.mapToScene(self.viewport().rect().center())
            width, height = self.native_size or (self.pixmap.width(), self.pixmap.height())
            self.relative_center = (
                max(0, min(1, point.x() / width)),
                max(0, min(1, point.y() / height)),
            )
            self.changed.emit(self.zoom, self.relative_center)

    def install_native(self, holder, recipe, size):
        self.native_holder, self.native_recipe, self.native_size = holder, recipe, size
        self.scene.setSceneRect(0, 0, *size)
        if self.preview_item:
            from PySide6.QtGui import QTransform

            self.preview_item.setTransform(
                QTransform.fromScale(size[0] / self.pixmap.width(), size[1] / self.pixmap.height())
            )
        fitted = min(
            max(1, self.viewport().width()) / size[0], max(1, self.viewport().height()) / size[1]
        )
        self.set_state(1 / max(fitted * self.devicePixelRatioF(), 0.0001), self.relative_center)

    def needed_tiles(self):
        from ..viewport import TILE, level_for, level_size
        import math

        rectangle = self.mapToScene(self.viewport().rect()).boundingRect()
        level = level_for(self.transform().m11() * self.devicePixelRatioF())
        width, height = level_size(*self.native_size, level)
        factor = 2**level
        left, top = max(0, rectangle.left() / factor), max(0, rectangle.top() / factor)
        right, bottom = (
            min(width, rectangle.right() / factor),
            min(height, rectangle.bottom() / factor),
        )
        indexes = [
            (x, y)
            for y in range(int(top // TILE), math.ceil(bottom / TILE))
            for x in range(int(left // TILE), math.ceil(right / TILE))
        ]
        return level, self.tile_store.missing('edited', 'compare', level, indexes)

    def install_tiles(self, result):
        for kind, key, level, tiles in result:
            self.tile_store.add(kind, key, level, tiles)
        level = result[0][2]
        if getattr(self, 'tile_level', None) != level:
            for item in self.tile_items.values():
                self.scene.removeItem(item)
            self.tile_items = {}
            self.tile_level = level
        layer = self.tile_store.layer('edited', 'compare', level)
        for index in list(self.tile_items):
            if index not in layer:
                self.scene.removeItem(self.tile_items.pop(index))
        for index, tile in layer.items():
            if index not in self.tile_items and tile.image is not None:
                item = self.scene.addPixmap(QPixmap.fromImage(tile.image))
                item.setPos(tile.x0 * 2**level, tile.y0 * 2**level)
                item.setScale(2**level)
                self.tile_items[index] = item


class _CompareJob(QThread):
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
        self.cancel = threading.Event()

    def run(self):
        try:
            self.result.emit(self.operation(self.cancel))
        except InterruptedError:
            pass
        except Exception as error:
            self.failed.emit(str(error)[:2000])


def preview_path(project, record):
    path = project.directory('previews') / str(record['facts'].get('preview', ''))
    if (
        path.is_file()
        and path.resolve().parent == project.directory('previews')
        and not path.is_symlink()
    ):
        return path
    source = Path(record['path'])
    if source.suffix.lower() in ('.jpg', '.jpeg', '.png', '.bmp', '.webp') and source.is_file():
        return source
    from .repository import Catalog

    proxy = Catalog(project).proxy(record['id'])
    return Path(proxy['path']) if proxy else path


class CompareDialog(QDialog):
    def __init__(
        self, project, records, parent=None, *, recipes=None, preview_paths=None, validator=None
    ):
        super().__init__(parent)
        self.project, self.records = project, list(records)
        from .. import model

        self.recipes = (
            [model.validate(recipe) for recipe in recipes] if recipes is not None else None
        )
        if self.recipes is not None and len(self.recipes) != len(self.records):
            raise ValueError('对比配方数量与照片数量不一致。')
        self.validator = validator
        self.jobs = []
        self.closing = False
        self.rendering = set()
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self.render_visible)
        self.setWindowTitle('对比候选')
        self.resize(1100, 720)
        layout = QVBoxLayout(self)
        self.locked = QCheckBox('同步缩放与相对中心')
        self.locked.setChecked(True)
        layout.addWidget(self.locked)
        row = QHBoxLayout()
        self.views = []
        for index, record in enumerate(records):
            column = QVBoxLayout()
            title = QLabel(record.get('copy_name') or Path(record['path']).name)
            title.setProperty('user_content', True)
            column.addWidget(title)
            view = CandidateView(
                preview_paths[index] if preview_paths else preview_path(project, record)
            )
            view.changed.connect(lambda zoom, center, i=index: self.set_view_state(i, zoom, center))
            self.views.append(view)
            column.addWidget(view, 1)
            row.addLayout(column, 1)
        layout.addLayout(row, 1)
        self.status = QLabel('适屏候选预览；滚轮缩放、拖动移动。原像素对比按下方按钮从原片渲染。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.native_button = QPushButton('加载原像素对比')
        self.native_button.clicked.connect(self.load_native)
        layout.addWidget(self.native_button)
        cancel = QPushButton('取消细节加载')
        cancel.clicked.connect(self.cancel_jobs)
        layout.addWidget(cancel)

    def set_view_state(self, index, zoom, center):
        self.views[index].set_state(zoom, center)
        if self.locked.isChecked():
            for i, view in enumerate(self.views):
                if i != index:
                    view.set_state(zoom, center)
        self.render_timer.start(80)

    def start_job(self, operation, callback, finish=None):
        job = _CompareJob(operation, self)
        self.jobs.append(job)
        job.result.connect(lambda result: callback(result) if not self.closing else None)
        job.failed.connect(self.status.setText)

        def completed():
            self.jobs.remove(job)
            job.deleteLater()
            if finish:
                finish()
            if self.closing and not self.jobs:
                self.close()

        job.finished.connect(completed)
        job.start()

    def cancel_jobs(self):
        self.render_timer.stop()
        for job in self.jobs:
            job.cancel.set()
        self.status.setText('已请求取消；解码中的原片将在当前解码步骤结束后停止。')

    def closeEvent(self, event):
        if self.jobs:
            self.closing = True
            self.cancel_jobs()
            event.ignore()
            return
        event.accept()

    def load_native(self):
        if self.jobs:
            return
        from .. import engine, model
        from ..viewport import DetailSource
        from .repository import Catalog

        self.native_button.setEnabled(False)
        self.status.setText('后台解码原片；完成后仅渲染可见区域的原像素瓦片。')

        def operation(cancel):
            result = []
            decoded = {}
            if self.validator:
                self.validator(cancel)
            for index, record in enumerate(self.records):
                if cancel.is_set():
                    raise InterruptedError()
                source = Catalog(self.project).edit_source(record['id'], allow_proxy=False)
                if source['path'] not in decoded:
                    decoded[source['path']] = engine.load_image(source['path'], 0)
                rgb, info = decoded[source['path']]
                if cancel.is_set():
                    raise InterruptedError()
                recipe = (
                    self.recipes[index]
                    if self.recipes is not None
                    else self.project.preferences().get('recipe:' + record['id'], model.recipe())
                )
                recipe = dict(recipe, develop=recipe.get('develop') or info.get('develop', {}))
                holder = DetailSource(source['path'])
                holder.full = rgb
                size = engine.display_size(recipe, (rgb.shape[1], rgb.shape[0]), True)
                result.append((holder, recipe, size))
            if self.validator:
                self.validator(cancel)
            return result

        def ready(result):
            for view, data in zip(self.views, result):
                view.install_native(*data)
            self.status.setText('原片已解码；原像素对比按可见区域渲染，滚轮与拖动可检查各处细节。')
            self.render_timer.start(0)

        self.start_job(operation, ready, lambda: self.native_button.setEnabled(True))

    def render_visible(self):
        from .. import engine, viewport

        if self.closing:
            return
        for index, view in enumerate(self.views):
            if not view.native_holder or index in self.rendering:
                continue
            level, indexes = view.needed_tiles()
            if not indexes:
                continue
            request = dict(
                edits=view.native_recipe,
                backend=engine.Backend('cpu'),
                level=level,
                final=True,
                blocks=[('edited', 'compare', block) for block in viewport.group(indexes, chunk=8)],
                proxy=view.native_holder.full,
                proxy_scale=1.0,
            )
            self.rendering.add(index)

            def finish(i=index):
                self.rendering.discard(i)
                if not self.closing:
                    self.render_timer.start(80)

            self.start_job(
                lambda cancel, h=view.native_holder, r=request: viewport.render(h, r, cancel),
                view.install_tiles,
                finish,
            )


class SurveyDialog(QDialog):
    def __init__(self, project, records, parent=None):
        super().__init__(parent)
        self.project, self.records = project, list(records)
        self.setWindowTitle('候选 Survey · 移除只影响本视图')
        self.resize(1000, 720)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll)
        layout.addWidget(QLabel('移除候选不会更改评级、相册、回收站或原片。'))
        self.refresh()

    def refresh(self):
        panel = QWidget()
        grid = QGridLayout(panel)
        for i, record in enumerate(self.records):
            column = QVBoxLayout()
            title = QLabel(record.get('copy_name') or Path(record['path']).name)
            title.setProperty('user_content', True)
            column.addWidget(title)
            column.addWidget(CandidateView(preview_path(self.project, record)))
            remove = QPushButton('移出本视图')
            remove.clicked.connect(lambda _checked=False, index=i: self.remove_candidate(index))
            column.addWidget(remove)
            grid.addLayout(column, i // 3, i % 3)
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.scroll.setWidget(panel)

    def remove_candidate(self, index):
        if 0 <= index < len(self.records):
            self.records.pop(index)
            self.refresh()
