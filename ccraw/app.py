from __future__ import annotations
from . import resources
import copy
import sys
from pathlib import Path
from PySide6.QtCore import QEvent, QObject, QThreadPool, QTimer, Signal, QSettings
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget
from . import engine, model
from .scheduler import JobScheduler, WorkState, WorkStateAccess
from .widgets import AdjustSlider
from .workspace import WorkspaceMixin
from .editing_tools import EditingToolsMixin
from .resolution import ResolutionMixin
from .library import LibraryMixin
from .auto_masks import AutoMaskMixin
from .workflow import WorkflowMixin
from .nl_panel import NaturalLanguageMixin
from . import performance
from . import live_preview, native_kernels

from .ui.theme import STYLE as STYLE, apply_theme
from .ui.components import ComputeStatusBar
from .ui.panels import PanelsMixin
from .ui.preview import PreviewMixin
from .ui.documents import DocumentsMixin
from .ui.editing import EditingMixin


class MainWindow(
    PanelsMixin,
    PreviewMixin,
    DocumentsMixin,
    EditingMixin,
    WorkStateAccess,
    WorkflowMixin,
    NaturalLanguageMixin,
    LibraryMixin,
    ResolutionMixin,
    AutoMaskMixin,
    EditingToolsMixin,
    WorkspaceMixin,
    QMainWindow,
):
    export_progress = Signal(int, str)

    def __init__(self):
        performance.configure()
        super().__init__()
        # Work state and the job queue exist before any mixin touches a legacy flag.
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(performance.MAX_THREADS)
        self.work = WorkState(self)
        self.scheduler = JobScheduler(self.pool, self)
        self.scheduler.drained.connect(lambda: QTimer.singleShot(0, self.close))
        self.render_cache = engine.RenderCache()
        self.preview_buffers = live_preview.PreviewBuffers()
        self.preview_quality = live_preview.PreviewQuality()
        self._preview_dragging = 0
        self._preview_epoch = 0
        self._preview_started = 0.0
        font = resources.asset_path('NotoSansSC.ttf')
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
        self.setWindowTitle('CCRaw')
        self.setWindowIcon(QIcon(str(resources.asset_path('ccraw.ico'))))
        self.resize(1600, 1040)
        self.setMinimumSize(1180, 780)
        self.source = None
        self.source_path = ''
        self.info = {}
        self.edits = model.recipe()
        self.saved_edits = copy.deepcopy(self.edits)
        self.history = model.History(self.edits)
        self.project_path = ''
        self.rendered = None
        self.generation = 0
        self.export_progress.connect(self.update_export_progress)
        self.current_mask = -1
        self.refreshing = False
        self.comparing = False
        self.backend = engine.Backend()
        self.setStatusBar(ComputeStatusBar(self))
        self.compute_timer = QTimer(self)
        self.compute_timer.setInterval(250)
        self.compute_timer.timeout.connect(self.statusBar().refresh_mode)
        self.compute_timer.start()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.render)
        self.history_timer = QTimer(self)
        self.history_timer.setSingleShot(True)
        self.history_timer.setInterval(550)
        self.history_timer.timeout.connect(self.commit)
        self.controls = {}
        self.local_controls = {}
        self.init_workspace()
        self.init_resolution()
        self.init_library()
        self.init_natural_language()
        self.build_ui()
        for control in self.findChildren(AdjustSlider):
            control.dragging.connect(self.preview_drag)
        for control in self.findChildren(QWidget):
            signal = getattr(control, 'preview_dragging', None)
            if signal is not None:
                signal.connect(self.preview_drag)
                live_preview.GestureGuard(control)
        self.canvas.stroke_changed.connect(self.changed)
        self.canvas.committed.connect(self.commit)
        self.input_gestures = live_preview.InputGestures(self)
        self.input_gestures.dragging.connect(self.preview_drag)
        self.input_gestures.committed.connect(self.commit)
        # Compile/load native kernels before a first curve gesture, off the GUI.
        self.job(native_kernels.warm, lambda _: None, priority=-2)
        self.shortcuts()
        self.refresh()


class FileOpenEvents(QObject):
    """macOS delivers Finder “Open With”, Dock drops and ``open -a`` as QFileOpenEvent, not argv."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.paths = []
        # Several files opened together arrive as separate events; import them as one batch.
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.flush)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FileOpen and event.file():
            self.paths.append(event.file())
            self.timer.start()
            return True
        return False

    def flush(self):
        paths, self.paths = self.paths, []
        if paths:
            self.window.import_paths(paths)


def main():
    from . import logs

    logs.configure()
    logs.describe_system()
    app = QApplication(sys.argv)
    from . import ai_worker

    app.aboutToQuit.connect(ai_worker.shutdown)
    app.setApplicationName('CCRaw')
    app.setStyle('Fusion')
    apply_theme(app, QSettings('CCRaw', 'CCRaw').value('appearance', 'light'))
    window = MainWindow()
    if sys.platform == 'darwin':
        app.installEventFilter(FileOpenEvents(window))
    window.show()
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        QTimer.singleShot(100, lambda: window.open_path(sys.argv[1]))
    return app.exec()
