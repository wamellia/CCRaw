"""Project-first welcome screen with local, searchable recent Agent projects."""

from datetime import datetime
import hashlib
import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QIcon, QPen
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import resources
from ..ui import icons, theme
from . import recent
from .store import Project


class ProjectDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(100, max(78, option.fontMetrics.height() * 2 + 38))

    def paint(self, painter, option, index):
        record = index.data(Qt.ItemDataRole.UserRole)
        if not record:
            return
        t = theme.tokens()
        row = QRectF(option.rect).adjusted(0, 3, 0, -3)
        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(
            theme.color('selection' if selected else 'hover' if hovered else 'background')
        )
        painter.drawRoundedRect(row, 6, 6)
        badge = QRectF(row.left() + 14, row.center().y() - 18, 36, 36)
        colours = ('#516DC1', '#9764B8', '#478B83', '#A57647', '#9D5982', '#567AA3')
        colour = colours[hashlib.sha256(record['path'].encode('utf8')).digest()[0] % len(colours)]
        painter.setBrush(theme.color('disabled') if not Path(record['path']).is_file() else colour)
        painter.drawRoundedRect(badge, 8, 8)
        face = QFont(option.font)
        face.setPixelSize(15)
        face.setBold(True)
        painter.setFont(face)
        painter.setPen('white')
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, record['name'][0].upper())
        x = badge.right() + 14
        y = row.top() + 14
        timestamp = datetime.fromtimestamp(record['opened']).strftime('%Y-%m-%d %H:%M')
        small = QFont(option.font)
        small.setPixelSize(12)
        painter.setFont(small)
        metrics = painter.fontMetrics()
        time_width = metrics.horizontalAdvance(timestamp)
        time_rect = QRectF(row.right() - time_width - 14, y + 1, time_width, 24)
        painter.setPen(t.secondary)
        painter.drawText(
            time_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, timestamp
        )
        painter.setFont(face)
        painter.setPen(t.text)
        title_rect = QRectF(x, y, max(1, time_rect.left() - x - 16), 24)
        title = painter.fontMetrics().elidedText(
            record['name'], Qt.TextElideMode.ElideRight, int(title_rect.width())
        )
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter, title)
        painter.setFont(small)
        painter.setPen(t.secondary)
        path_rect = QRectF(x, y + 26, max(1, row.right() - x - 14), 20)
        path = record['path']
        if not Path(path).is_file():
            path = '工程文件不存在 · ' + path
        path = painter.fontMetrics().elidedText(
            path, Qt.TextElideMode.ElideMiddle, int(path_rect.width())
        )
        painter.drawText(path_rect, Qt.AlignmentFlag.AlignVCenter, path)
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme.color('focus'), 1, Qt.PenStyle.DotLine))
            painter.drawRoundedRect(row.adjusted(2, 2, -2, -2), 6, 6)
        painter.restore()


class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        from .ui import ensure_font

        ensure_font()
        self.windows = []
        self.setWindowTitle('CCRaw')
        self.setWindowIcon(QIcon(str(resources.asset_path('ccraw.ico'))))
        self.resize(1040, 640)
        self.setMinimumSize(720, 440)
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.sidebar = QWidget()
        self.sidebar.setObjectName('launcherSidebar')
        self.sidebar.setFixedWidth(264)
        left = QVBoxLayout(self.sidebar)
        left.setContentsMargins(20, 34, 20, 24)
        left.setSpacing(10)
        brand = QHBoxLayout()
        brand.setSpacing(12)
        mark = QLabel()
        mark.setPixmap(self.windowIcon().pixmap(QSize(42, 42), self.devicePixelRatioF()))
        title = QLabel('CCRaw')
        title.setStyleSheet('font-size: 24px; font-weight: 600;')
        brand.addWidget(mark)
        brand.addWidget(title, 1)
        left.addLayout(brand)
        left.addSpacing(30)
        self.agent_button = self.action(
            'Photo Agent 新建工程', self.new_agent, 'plus', primary=True
        )
        self.open_agent_button = self.action('打开 Agent 工程', self.open_agent, 'open')
        self.catalog_button = self.action('快捷编辑', self.open_catalog, 'retouch')
        for control in (self.agent_button, self.open_agent_button, self.catalog_button):
            left.addWidget(control)
        left.addStretch()
        self.appearance_button = theme.AppearanceButton(self.sidebar)
        left.addWidget(self.appearance_button)
        layout.addWidget(self.sidebar)
        self.projects = QWidget()
        right = QVBoxLayout(self.projects)
        right.setContentsMargins(28, 30, 28, 24)
        right.setSpacing(14)
        self.project_search = QLineEdit()
        self.project_search.setPlaceholderText('搜索工程')
        self.project_search.setAccessibleName('搜索工程')
        self.project_search.setClearButtonEnabled(True)
        self.project_search.setMinimumHeight(40)
        self.search_action = self.project_search.addAction(
            icons.icon('search', self), QLineEdit.ActionPosition.LeadingPosition
        )
        right.addWidget(self.project_search)
        header = QLabel('最近工程')
        header.setObjectName('section')
        right.addWidget(header)
        self.recent_list = QListWidget()
        self.recent_list.setObjectName('recentProjects')
        self.recent_list.setItemDelegate(ProjectDelegate(self.recent_list))
        self.recent_list.setUniformItemSizes(True)
        self.recent_list.setMouseTracking(True)
        self.recent_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.recent_list.setCursor(Qt.CursorShape.PointingHandCursor)
        self.recent_list.setAccessibleName('最近的 Photo Agent 工程')
        self.recent_list.itemClicked.connect(self.open_recent)
        self.recent_list.itemActivated.connect(self.open_recent)
        self.recent_stack = QStackedWidget()
        self.recent_stack.addWidget(self.recent_list)
        self.empty_state = QLabel('暂无最近工程')
        self.empty_state.setObjectName('subtle')
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_stack.addWidget(self.empty_state)
        right.addWidget(self.recent_stack, 1)
        layout.addWidget(self.projects, 1)
        self.setCentralWidget(root)
        self.project_search.textChanged.connect(self.refresh_recent)
        self.update_style()
        self.refresh_recent()

    def action(self, text, callback, symbol, *, primary=False):
        control = QPushButton(text)
        control.setMinimumHeight(46)
        if primary:
            control.setObjectName('primary')
        control.setStyleSheet('text-align: left; padding: 10px 12px; font-size: 14px;')
        icons.set_icon(control, symbol, primary)
        control.clicked.connect(callback)
        return control

    def update_style(self):
        t = theme.tokens()
        if getattr(self, '_theme', None) == t:
            return
        self._theme = t
        self.sidebar.setStyleSheet(f'QWidget#launcherSidebar {{ background: {t.panel}; }}')
        self.recent_list.setStyleSheet('QListWidget#recentProjects { padding: 0; }')
        self.project_search.setStyleSheet('QLineEdit { padding: 8px 10px; font-size: 14px; }')
        for control in (self.agent_button, self.open_agent_button, self.catalog_button):
            icons.set_icon(control, control.property('symbol'), control.property('filled'))
        self.search_action.setIcon(icons.icon('search'))
        self.recent_list.viewport().update()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.PaletteChange,
        ) and hasattr(self, '_theme'):
            self.update_style()

    def refresh_recent(self, *_):
        query = self.project_search.text().strip().casefold()
        self.recent_list.clear()
        for record in recent.recent_projects():
            if query and query not in (record['name'] + '\n' + record['path']).casefold():
                continue
            item = QListWidgetItem(record['name'])
            item.setData(Qt.ItemDataRole.UserRole, record)
            item.setToolTip(record['path'])
            self.recent_list.addItem(item)
        empty = self.recent_list.count() == 0
        self.empty_state.setText('未找到工程' if query else '暂无最近工程')
        self.recent_stack.setCurrentWidget(self.empty_state if empty else self.recent_list)

    def open_recent(self, item):
        record = item.data(Qt.ItemDataRole.UserRole)
        if record:
            self.load_agent(record['path'])

    def remember(self, project):
        try:
            recent.remember_project(project)
        except (OSError, ValueError):
            logging.getLogger(__name__).warning('Recent project history could not be saved')
        self.refresh_recent()

    def retain(self, window):
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.windows.append(window)
        window.destroyed.connect(
            lambda: self.windows.remove(window) if window in self.windows else None
        )
        window.show()
        return window

    def open_catalog(self, path=None):
        from ..app import MainWindow

        window = self.retain(MainWindow())
        if isinstance(path, (str, Path)):
            QTimer.singleShot(0, lambda: window.open_path(str(path)))
        return window

    def new_agent(self):
        path, _ = QFileDialog.getSaveFileName(
            self, '新建 Agent 工程', '', 'CCRaw Agent (*.ccrawagent)'
        )
        if path:
            self.load_agent(path, create=True)

    def open_agent(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '打开 Agent 工程', '', 'CCRaw Agent (*.ccrawagent)'
        )
        if path:
            self.load_agent(path)

    def load_agent(self, path, *, create=False):
        from .ui import ACTIVE, AgentWindow

        key = str(Path(path).resolve())
        if key in ACTIVE:
            window = ACTIVE[key]
            window.show()
            window.raise_()
            window.activateWindow()
            self.remember(window.project)
            return window
        try:
            project = Project.create(path, Path(path).stem) if create else Project.open(path)
            project.recover()
            window = self.retain(AgentWindow(project))
            ACTIVE[str(project.path)] = window
            self.remember(project)
            return window
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, '工程', str(error))
