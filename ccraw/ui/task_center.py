"""GUI-thread view of existing workers; never starts or replays a task."""

from collections import deque

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QLabel,
)

from ..scheduler import Activity, LABELS
from .. import i18n


def active_tasks():
    rows = []
    seen = set()

    def add(owner, kind, label, cancel=None):
        key = (id(owner), kind)
        if key not in seen:
            seen.add(key)
            rows.append(dict(id=key, window=type(owner).__name__, label=label, cancel=cancel))

    for window in QApplication.topLevelWidgets():
        work = getattr(window, 'work', None)
        if work is not None:
            for activity in Activity:
                if work.active(activity):
                    cancel = None
                    if activity == Activity.EXPORTING and hasattr(window, 'export_cancel'):
                        cancel = window.export_cancel.set
                    elif activity == Activity.DETAIL and hasattr(window, 'cancel_detail'):
                        cancel = window.cancel_detail
                    add(window, activity.value, LABELS[activity], cancel)
        if getattr(window, 'worker', None) is not None:
            add(window, 'agent', '工程后台任务', window.cancel_work)
        generation = getattr(window, 'generation_panel', None)
        if generation is not None and (generation.busy or generation.pending):
            add(window, 'generation', '图像生成', generation.cancel_all)
        if getattr(window, '_model_busy', False):
            add(window, 'models', '下载本地模型', window._model_cancel.set)
        if getattr(window, 'jobs', None) and hasattr(window, 'cancel_jobs'):
            add(window, 'compare', '原像素对比', window.cancel_jobs)
    return rows


class TaskCenter(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.translate('任务'))
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        self.list = QTreeWidget()
        self.list.setHeaderLabels([i18n.translate(text) for text in ('任务', '窗口', '状态')])
        self.list.setRootIsDecorated(False)
        self.list.setColumnWidth(0, 240)
        layout.addWidget(self.list, 1)
        self.status = QLabel()
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.cancel = QPushButton(i18n.translate('取消所选任务'))
        self.cancel.clicked.connect(self.cancel_selected)
        row.addWidget(self.cancel)
        close = QPushButton(i18n.translate('关闭'))
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.current, self.recent = {}, deque(maxlen=100)
        self.list.currentItemChanged.connect(self.selection_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.refresh)
        self.refresh()
        self.timer.start()

    def refresh(self):
        selected = self.list.currentItem()
        selected = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
        tasks = {row['id']: row for row in active_tasks()}
        for key, row in self.current.items():
            if key not in tasks:
                self.recent.append({k: row[k] for k in ('id', 'window', 'label')})
        self.current = tasks
        self.list.clear()
        for row in [*tasks.values(), *reversed(self.recent)]:
            running = row['id'] in tasks
            item = QTreeWidgetItem(
                [
                    i18n.translate(row['label']),
                    row['window'],
                    i18n.translate('运行中' if running else '已结束'),
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, row['id'])
            self.list.addTopLevelItem(item)
            if row['id'] == selected:
                self.list.setCurrentItem(item)
        self.selection_changed()

    def selection_changed(self, *_):
        item = self.list.currentItem()
        row = self.current.get(item.data(0, Qt.ItemDataRole.UserRole)) if item else None
        self.cancel.setEnabled(bool(row and row['cancel']))

    def cancel_selected(self):
        item = self.list.currentItem()
        row = self.current.get(item.data(0, Qt.ItemDataRole.UserRole)) if item else None
        if row and row['cancel']:
            try:
                row['cancel']()
                self.status.setText(i18n.translate('已请求取消'))
            except RuntimeError:
                pass  # The task's owning QObject may have closed since the last tick.
        self.refresh()

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)


def show_tasks(owner):
    dialog = getattr(owner, '_task_center', None)
    if dialog is None:
        dialog = TaskCenter(owner)
        owner._task_center = dialog
    dialog.timer.start()
    dialog.refresh()
    dialog.show()
    dialog.raise_()
    return dialog
