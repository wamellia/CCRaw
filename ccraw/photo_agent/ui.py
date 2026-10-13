"""Two workspaces, cancellable background work and explicit edit approval."""

import asyncio
import copy
import html
import json
from pathlib import Path
import threading
import weakref

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSignalBlocker
from PySide6.QtGui import QIcon, QAction, QFontDatabase
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QPlainTextEdit,
    QLineEdit,
    QProgressBar,
    QInputDialog,
    QMenu,
    QApplication,
)

from .. import resources, nl_edit, model, image_generation
from ..ui.theme import AppearanceButton
from . import analysis, editing, search
from .models import LocalModels, install_models
from .runtime import run_turn
from .gallery import PhotoGallery, DETAIL_ROLE, thumbnail
from .launcher import Launcher as Launcher
from ..catalog.window import CatalogWindowMixin

ACTIVE = weakref.WeakValueDictionary()


def ensure_font():
    if 'Noto Sans SC' not in QFontDatabase.families():
        font = resources.asset_path('NotoSansSC.ttf')
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))


class Worker(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(str, object)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
        self.cancel = threading.Event()

    def run(self):
        try:
            self.result.emit(self.operation(self.cancel, self.progress.emit))
        except InterruptedError as error:
            self.failed.emit(str(error))
        except Exception as error:
            from ..logs import redact

            self.failed.emit(redact(str(error))[:2000])


def button(text, callback, *, primary=False):
    widget = QPushButton(text)
    widget.setMinimumHeight(36)
    widget.setMinimumWidth(80)
    if primary:
        widget.setObjectName('primary')
    widget.clicked.connect(callback)
    return widget


class AgentWindow(CatalogWindowMixin, QMainWindow):
    def __init__(self, project):
        super().__init__()
        ensure_font()
        self.project = project
        self.worker = None
        self.client = None
        self.models = None
        self.editors = []
        self.preview_dialog = None
        self.closing = False
        self.offset = 0
        self.page_selections = {}
        self.page_currents = {}
        self.gallery_offset = 0
        self.result_ids = None
        self.report = None
        self.evidence = {}
        self.busy_controls = []
        self.setWindowTitle(f'{project.manifest["name"]} · Photo Agent · CCRaw')
        self.setWindowIcon(QIcon(str(resources.asset_path('ccraw.ico'))))
        self.resize(1440, 940)
        self.setMinimumSize(1180, 780)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        top = QHBoxLayout()
        self.title = QLabel(project.manifest['name'])
        self.title.setProperty('user_content', True)
        self.title.setStyleSheet('font-size: 20px; font-weight: 600;')
        top.addWidget(self.title, 1)
        for text, callback in [
            ('导入照片', self.import_files),
            ('导入文件夹', self.import_folder),
            ('重新扫描', self.scan),
            ('本地模型', self.setup_models),
            ('模型设置', self.settings),
        ]:
            control = button(text, callback)
            top.addWidget(control)
            self.busy_controls.append(control)
        self.appearance_button = AppearanceButton(self)
        top.addWidget(self.appearance_button)
        self.chat_toggle = button('收起对话', self.toggle_chat)
        top.addWidget(self.chat_toggle)
        layout.addLayout(top)
        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        left = QWidget()
        left.setMinimumWidth(520)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        self.photo_splitter = QSplitter(Qt.Orientation.Vertical)
        self.photo_splitter.setChildrenCollapsible(False)
        self.photo_splitter.setHandleWidth(6)
        photo_panel = QWidget()
        photo_layout = QVBoxLayout(photo_panel)
        photo_layout.setContentsMargins(0, 0, 0, 0)
        finder = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setMaxLength(1000)
        self.search_input.setPlaceholderText('找图：去年夏天海边的日落，画面中有两个人')
        self.search_input.returnPressed.connect(self.local_search)
        finder.addWidget(self.search_input, 1)
        find = button('找图', self.local_search, primary=True)
        finder.addWidget(find)
        self.busy_controls.append(find)
        finder.addWidget(button('全部', self.show_all))
        photo_layout.addLayout(finder)
        self.gallery = PhotoGallery()
        self.gallery.setMinimumHeight(160)
        self.gallery.itemDoubleClicked.connect(lambda _: self.open_editor())
        self.gallery.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.gallery.customContextMenuRequested.connect(self.photo_menu)
        photo_layout.addWidget(self.gallery, 1)
        pages = QHBoxLayout()
        pages.addWidget(button('上一页', lambda: self.page(-1)))
        self.count_label = QLabel()
        pages.addWidget(self.count_label, 1)
        pages.addWidget(button('下一页', lambda: self.page(1)))
        photo_layout.addLayout(pages)
        photo_actions = QHBoxLayout()
        self.editor_button = button('照片编辑', self.open_editor)
        photo_actions.addWidget(self.editor_button)
        annotate = button('标注人数 / 标签', self.annotation)
        photo_actions.addWidget(annotate)
        recommend = button('推荐修图', self.recommend)
        photo_actions.addWidget(recommend)
        self.busy_controls.extend((annotate, recommend, self.editor_button))
        photo_layout.addLayout(photo_actions)
        self.photo_splitter.addWidget(photo_panel)
        self.tabs = QTabWidget()
        self.suggestion_list = QListWidget()
        self.suggestion_list.itemClicked.connect(self.select_group)
        self.suggestion_offset = 0
        suggestion_panel = QWidget()
        suggestion_layout = QVBoxLayout(suggestion_panel)
        suggestion_layout.addWidget(self.suggestion_list)
        suggestion_actions = QHBoxLayout()
        suggestion_actions.addWidget(button('上一组建议', lambda: self.page_suggestions(-1)))
        self.suggestion_count = QLabel()
        suggestion_actions.addWidget(self.suggestion_count, 1)
        suggestion_actions.addWidget(button('下一组建议', lambda: self.page_suggestions(1)))
        suggestion_layout.addLayout(suggestion_actions)
        decisions = QHBoxLayout()
        for label, decision in [
            ('保留建议', 'keep'),
            ('忽略建议', 'ignore'),
            ('标记已处理', 'resolved'),
        ]:
            decisions.addWidget(
                button(
                    label,
                    lambda _checked=False, value=decision: self.current_suggestion_decision(value),
                )
            )
        suggestion_layout.addLayout(decisions)
        self.tabs.addTab(suggestion_panel, '整理建议')
        proposal_panel = QWidget()
        proposal_layout = QVBoxLayout(proposal_panel)
        self.proposal_list = QListWidget()
        self.proposal_list.currentItemChanged.connect(self.update_proposal_controls)
        proposal_layout.addWidget(self.proposal_list)
        self.apply_button = button('查看并应用方案', self.apply_proposal, primary=True)
        self.preview_button = button('预览方案', self.preview_proposal)
        self.clear_proposals_button = button('清空当前方案', self.clear_proposals)
        proposal_actions = QHBoxLayout()
        proposal_actions.addWidget(self.apply_button, 1)
        proposal_actions.addWidget(self.preview_button)
        proposal_actions.addWidget(self.clear_proposals_button)
        proposal_layout.addLayout(proposal_actions)
        self.busy_controls.append(self.clear_proposals_button)
        self.busy_controls.append(self.preview_button)
        self.tabs.addTab(proposal_panel, '编辑方案')
        self.derivative_list = QListWidget()
        self.derivative_list.itemDoubleClicked.connect(self.open_derivative)
        self.tabs.addTab(self.derivative_list, '派生版本')
        self.trace = QTextBrowser()
        self.tabs.addTab(self.trace, '执行记录')
        self.tabs.setMinimumHeight(140)
        self.photo_splitter.addWidget(self.tabs)
        self.photo_splitter.setSizes([620, 210])
        self.photo_splitter.setStretchFactor(0, 3)
        self.photo_splitter.setStretchFactor(1, 1)
        left_layout.addWidget(self.photo_splitter)
        splitter.addWidget(left)
        right = QWidget()
        self.chat_panel = right
        right.setMinimumWidth(340)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        self.provider_label = QLabel()
        self.provider_label.setWordWrap(True)
        right_layout.addWidget(self.provider_label)
        self.chat = QTextBrowser()
        self.chat.setOpenExternalLinks(False)
        right_layout.addWidget(self.chat, 1)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText('整理这个相册 / 找图 / 调整选中的照片')
        self.input.setMaximumHeight(120)
        right_layout.addWidget(self.input)
        send_row = QHBoxLayout()
        self.send_button = button('发送', self.send, primary=True)
        self.cancel_button = button('取消', self.cancel_work)
        self.cancel_button.setEnabled(False)
        send_row.addWidget(self.send_button, 1)
        send_row.addWidget(self.cancel_button)
        right_layout.addLayout(send_row)
        splitter.addWidget(right)
        splitter.setSizes([920, 420])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(12)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        self.setCentralWidget(root)
        self.install_catalog(photo_layout)
        from ..ui.settings_dialog import install_settings

        install_settings(self, project_getter=lambda: self.project)
        self.gallery.itemSelectionChanged.connect(self.refresh_proposals)
        self.refresh_gallery()
        self.refresh_records()
        self.refresh_chat()
        self.refresh_provider()
        self.statusBar().showMessage('导入照片开始扫描；原片保留，编辑保存为派生版本。')

    def selected_ids(self):
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.gallery.selectedItems()]

    def start_work(self, name, operation, done=None):
        if self.worker is not None:
            self.statusBar().showMessage('请等待当前任务完成或取消。')
            return False
        worker = Worker(operation, self)
        self.worker = worker
        self.send_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        for control in self.busy_controls:
            control.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.statusBar().showMessage(name)
        worker.progress.connect(self.on_progress)
        worker.failed.connect(lambda text: self.statusBar().showMessage(text))
        worker.result.connect(lambda value: done(value) if done else None)

        def finished():
            self.worker = None
            self.client = None
            worker.deleteLater()
            self.send_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.progress_bar.hide()
            for control in self.busy_controls:
                control.setEnabled(True)
            self.refresh_gallery()
            self.refresh_records()
            self.refresh_chat()
            if self.closing:
                QTimer.singleShot(0, self.close)

        worker.finished.connect(finished)
        worker.start()
        return True

    def on_progress(self, kind, value):
        if kind == 'scan':
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(value['percent'])
            self.statusBar().showMessage(value['text'])
        elif kind == 'progress':
            self.statusBar().showMessage(value.get('text', '')[:300])
        elif kind == 'iteration':
            self.statusBar().showMessage(f'Agent · 第 {value["iteration"] + 1} 轮')
        elif kind == 'tool.finished':
            result = value['result']
            if value['name'] == 'search_photos':
                self.result_ids = [photo['id'] for photo in result['photos']]
                self.offset = 0
                self.refresh_gallery(result)
            elif value['name'] == 'album_report':
                self.report = dict(
                    result['suggestions'], coverage=result['coverage'], features=result['features']
                )
                self.refresh_suggestions()
            elif value['name'] == 'preview_edit':
                self.show_preview(result)
            self.refresh_records()

    def cancel_work(self):
        if self.worker:
            self.worker.cancel.set()
            if self.client:
                self.client.cancel()
            self.statusBar().showMessage('正在取消当前任务…')

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '导入照片', '', '照片 (*)')
        if paths:
            self.import_photos(paths)

    def import_folder(self):
        path = QFileDialog.getExistingDirectory(self, '导入文件夹')
        if path:
            self.import_photos(Path(path))

    def import_photos(self, paths):
        def operation(cancel, emit):
            items = []
            if isinstance(paths, Path):
                for item in paths.rglob('*'):
                    if cancel.is_set():
                        raise InterruptedError('导入已取消。')
                    if not item.is_symlink() and item.is_file():
                        items.append(item)
                    if len(items) > 100_000:
                        raise ValueError('导入文件过多，请分工程处理。')
            else:
                items = paths
            self.project.import_paths(items)
            return self.scan_operation(cancel, emit)

        self.start_work('导入并扫描照片', operation, self.scanned)

    def scan_operation(self, cancel, emit):
        if self.models is None:
            self.models = LocalModels()
        result = analysis.index_project(
            self.project,
            cancel,
            lambda percent, text: emit('scan', dict(percent=percent, text=text)),
            models=self.models,
        )
        report = (
            analysis.suggestions(
                self.project,
                cancel=cancel,
                progress=lambda percent, text: emit('scan', {'percent': percent, 'text': text}),
            )
            if not cancel.is_set()
            else (
                self.report
                or {'coverage': {'analyzed': 0, 'total': self.project.summary()['photos']}}
            )
        )
        if not cancel.is_set():
            service = nl_edit.active(nl_edit.load_settings())
            if service.get('model'):
                try:
                    asyncio.run(
                        run_turn(
                            self.project,
                            '扫描已完成，请整理当前工程，概述建议。',
                            cancel=cancel,
                            emit=emit,
                            models=self.models,
                        )
                    )
                except (ValueError, InterruptedError) as error:
                    result['agent_error'] = str(error)
        return result, report

    def scan(self):
        self.start_work('扫描照片', self.scan_operation, self.scanned)

    def scanned(self, result):
        counts, self.report = result
        self.refresh_suggestions()
        self.project.message(
            'system',
            f'扫描完成：分析 {counts["analyzed"]} 张，未变化 {counts["skipped"]} 张，失败 {counts["failed"]} 张。整理建议覆盖 {self.report["coverage"]["analyzed"]} / {self.report["coverage"]["total"]} 张；人物簇与质量提示需要人工判断。',
        )
        self.statusBar().showMessage(
            counts.get('agent_error')
            or f'扫描完成 · 新分析 {counts["analyzed"]} · 未变化 {counts["skipped"]} · 失败 {counts["failed"]}'
        )

    def setup_models(self):
        if (
            QMessageBox.question(
                self,
                '本地模型',
                '下载本地语义检索与匿名人物聚类模型（约 186 MiB）。模型文件保存在本机；照片不会上传。',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def operation(cancel, emit):
            install_models(
                cancel, lambda percent, text: emit('scan', dict(percent=percent, text=text))
            )
            self.models = LocalModels()
            return self.scan_operation(cancel, emit)

        self.start_work('下载本地模型', operation, self.scanned)

    def settings(self):
        from ..ui.settings_dialog import show_settings

        return show_settings(self, 'models')

    def refresh_provider(self):
        service = nl_edit.active(nl_edit.load_settings())
        self.provider_label.setText(
            f'{service["label"]} · {service["model"] or "未选择模型"}\n对话发送指令与工程摘要；照片分析在本地执行。'
        )

    def send(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        selected = tuple(self.selected_ids())

        def operation(cancel, emit):
            if self.models is None:
                self.models = LocalModels()
            return asyncio.run(
                run_turn(
                    self.project,
                    text,
                    selected=selected,
                    cancel=cancel,
                    emit=emit,
                    models=self.models,
                )
            )

        if self.start_work('Agent 正在处理', operation):
            self.input.clear()

    def local_search(self):
        if self.worker:
            return
        query = search.parse_query(
            self.search_input.text(), timezone=self.project.manifest['timezone']
        )
        query['rules'] = self.catalog_rules
        query['sort'] = self.sort_combo.currentData()
        query['descending'] = self.sort_descending.isChecked()
        if self.catalog_album:
            query['album'] = self.catalog_album
        if self.navigation.currentData() == 'edited':
            query['edited'] = True
        elif self.navigation.currentData() == 'missing':
            query['rules'] = {
                'all': [self.catalog_rules, {'field': 'status', 'op': 'eq', 'value': 'missing'}]
            }

        def operation(cancel, emit):
            if self.models is None:
                self.models = LocalModels()
            return search.search(self.project, query, models=self.models)

        def found(result):
            self.result_ids = [photo['id'] for photo in result['photos']]
            self.offset = 0
            self.refresh_gallery(result)
            self.statusBar().showMessage(f'找到 {len(self.result_ids)} 张候选 · 人数未知会单独标明')

        self.start_work('检索照片', operation, found)

    def show_all(self):
        self.result_ids = None
        self.offset = 0
        self.refresh_gallery()

    def page(self, step):
        count = self.catalog_query(limit=1, offset=0)['total']
        self.offset = max(0, min(max(0, (count - 1) // 200 * 200), self.offset + step * 200))
        self.refresh_gallery()

    def refresh_gallery(self, result=None):
        selected = set(self.selected_ids())
        current = self.gallery.currentItem()
        current_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.page_selections[self.gallery_offset] = selected
        self.page_currents[self.gallery_offset] = current_id
        if self.gallery_offset != self.offset:
            selected = self.page_selections.get(self.offset, set())
            current_id = self.page_currents.get(self.offset)
        self.gallery_offset = self.offset
        blocker = QSignalBlocker(self.gallery)
        self.gallery.clear()
        page = self.catalog_query() if hasattr(self, 'catalog') else None
        records = page['photos'] if page else self.project.photos(200, self.offset)
        if result:
            self.evidence = {p['id']: p['evidence'] for p in result.get('photos', [])}
        evidence = self.evidence if self.result_ids is not None else {}
        for photo in records:
            facts = photo['facts']
            item = QListWidgetItem(
                Path(photo['path']).name
                + (' · ' + photo.get('copy_name', '虚拟副本') if photo.get('is_copy') else '')
            )
            item.setData(Qt.ItemDataRole.UserRole, photo['id'])
            width, height = facts.get('width'), facts.get('height')
            detail = f'{width} × {height}' if width and height else '待扫描'
            state = (
                photo
                if 'rating' in photo
                else self.catalog.state(photo['id'])
                if hasattr(self, 'catalog')
                else {}
            )
            flags = {'pick': '保留', 'reject': '拒绝', 'none': ''}
            detail += (
                ' · '
                + ('★' * state.get('rating', 0) or '未评级')
                + (' · ' + flags.get(state.get('flag'), '') if state.get('flag') != 'none' else '')
            )
            if state.get('color_label'):
                detail += ' · ' + {
                    'red': '红色',
                    'yellow': '黄色',
                    'green': '绿色',
                    'blue': '蓝色',
                    'purple': '紫色',
                }.get(state['color_label'], state['color_label'])
            if photo.get('status') == 'missing':
                detail += ' · 原片缺失'
            item.setData(DETAIL_ROLE, detail)
            item.setToolTip(
                '\n'.join(
                    [
                        Path(photo['path']).name,
                        photo['id'],
                        *facts.get('flags', []),
                        *evidence.get(photo['id'], []),
                    ]
                )
            )
            preview = self.project.root / 'previews' / str(facts.get('preview', ''))
            if (
                preview.is_file()
                and preview.resolve().parent == (self.project.root / 'previews').resolve()
            ):
                item.setData(Qt.ItemDataRole.DecorationRole, thumbnail(preview))
            self.gallery.addItem(item)
            item.setSelected(photo['id'] in selected)
            if photo['id'] == current_id:
                self.gallery.setCurrentItem(item)
        summary = self.project.summary()
        count = page['total'] if page else summary['photos']
        self.count_label.setText(
            f'{count} 张 · 已索引 {summary["indexed"]} · {self.offset + 1 if count else 0}–{min(count, self.offset + 200)}'
        )
        blocker.unblock()
        self.refresh_proposals()
        if hasattr(self, 'metadata_fields'):
            self.refresh_metadata()

    def refresh_chat(self):
        self.chat.clear()
        for message in self.project.messages():
            label = {'user': '你', 'assistant': 'Photo Agent', 'system': '工程'}[message['role']]
            self.chat.append(
                f'<b>{label}</b><p style="white-space:pre-wrap">{html.escape(message["content"])}</p>'
            )
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    def refresh_suggestions(self):
        self.suggestion_list.clear()
        if self.report is None:
            return
        labels = {
            'duplicates': '完全重复',
            'similar': '相似候选',
            'quality': '质量提示',
            'events': '事件',
            'people': '匿名人物簇',
        }
        groups = [
            (kind, label, group)
            for kind, label in labels.items()
            for group in self.report.get(kind, [])
        ]
        self.suggestion_offset = min(self.suggestion_offset, max(0, (len(groups) - 1) // 200 * 200))
        self.suggestion_count.setText(
            f'{len(groups)} 条 · {self.suggestion_offset + 1 if groups else 0}–{min(len(groups), self.suggestion_offset + 200)}'
        )
        for kind, label, group in groups[self.suggestion_offset : self.suggestion_offset + 200]:
            ids = group.get('photos', [group['photo_id']] if 'photo_id' in group else [])
            details = group.get('label') or ' · '.join(group.get('reasons', []))
            if group.get('best_candidate'):
                details += (
                    ' · 推荐：' + Path(self.project.photo(group['best_candidate'])['path']).name
                )
            item = QListWidgetItem(
                f'{label} · {len(ids)} 张' + (f' · {details}' if details else '')
            )
            item.setData(Qt.ItemDataRole.UserRole, ids)
            item.setData(Qt.ItemDataRole.UserRole + 1, (kind, group))
            if group.get('decision'):
                item.setText(
                    item.text()
                    + ' · '
                    + {'keep': '保留建议', 'ignore': '忽略', 'resolved': '已处理'}[
                        group['decision']
                    ]
                )
            item.setToolTip(json.dumps(group, ensure_ascii=False, indent=2)[:3000])
            self.suggestion_list.addItem(item)
        coverage = self.report.get('coverage', {})
        self.suggestion_list.setToolTip(
            f'整理建议分析 {coverage.get("analyzed", 0)} / {coverage.get("total", 0)} 张；建议需要人工判断。'
        )

    def page_suggestions(self, step):
        self.suggestion_offset = max(0, self.suggestion_offset + step * 200)
        self.refresh_suggestions()

    def current_suggestion_decision(self, decision):
        item = self.suggestion_list.currentItem()
        if item:
            data = item.data(Qt.ItemDataRole.UserRole + 1)
            if data:
                self.decide_group(data[0], data[1], decision)

    def select_group(self, item):
        self.result_ids = item.data(Qt.ItemDataRole.UserRole)
        self.offset = 0
        self.refresh_gallery()
        for index in range(self.gallery.count()):
            self.gallery.item(index).setSelected(True)

    def refresh_proposals(self):
        self.proposal_list.clear()
        for proposal in self.project.proposals(photo_ids=self.selected_ids(), pending_only=True):
            item = QListWidgetItem(
                proposal['payload']['label']
                + ' · '
                + Path(self.project.photo(proposal['photo_id'])['path']).name
            )
            item.setData(Qt.ItemDataRole.UserRole, proposal)
            self.proposal_list.addItem(item)
        if self.proposal_list.count():
            self.proposal_list.setCurrentRow(0)
        self.apply_button.setEnabled(self.worker is None and self.proposal_list.count() > 0)
        self.update_proposal_controls()
        self.clear_proposals_button.setEnabled(
            self.worker is None and self.proposal_list.count() > 0
        )

    def update_proposal_controls(self, *_):
        if not hasattr(self, 'preview_button'):
            return
        current = self.proposal_list.currentItem()
        self.preview_button.setEnabled(
            self.worker is None
            and current is not None
            and current.data(Qt.ItemDataRole.UserRole)['kind'] == 'local'
        )

    def photo_menu(self, position):
        if self.gallery.itemAt(position) is None:
            return
        menu = QMenu(self)
        copy_ids = menu.addAction('复制选中照片 ID')
        copy_ids.setEnabled(bool(self.selected_ids()))
        copy_ids.triggered.connect(
            lambda: QApplication.clipboard().setText('\n'.join(self.selected_ids()))
        )
        menu.exec(self.gallery.viewport().mapToGlobal(position))

    def clear_proposals(self):
        if self.worker is not None:
            return
        self.project.dismiss_proposals(self.selected_ids())
        self.refresh_records()

    def refresh_records(self):
        self.refresh_proposals()
        self.derivative_list.clear()
        for derivative in self.project.derivatives():
            item = QListWidgetItem(Path(derivative['path']).name)
            item.setData(Qt.ItemDataRole.UserRole, derivative['path'])
            self.derivative_list.addItem(item)
        self.trace.setPlainText(
            '\n'.join(
                f'{e["seq"]} · {e["kind"]} · {json.dumps(e["payload"], ensure_ascii=False)[:1200]}'
                for e in self.project.events()
            )
        )

    def recommend(self):
        ids = self.selected_ids()
        if not 1 <= len(ids) <= 30:
            self.statusBar().showMessage('请选择 1–30 张照片。')
            return
        self.start_work(
            '本地诊断并创建方案',
            lambda cancel, emit: editing.propose_batch(self.project, ids, cancel=cancel),
            lambda _: self.tabs.setCurrentIndex(1),
        )

    def preview_proposal(self):
        item = self.proposal_list.currentItem()
        if self.worker is not None or item is None:
            return
        proposal = item.data(Qt.ItemDataRole.UserRole)
        if proposal['kind'] != 'local':
            self.statusBar().showMessage('外部方案需要服务商生成结果，不能本地预览。')
            return
        self.start_work(
            '正在渲染方案预览',
            lambda cancel, emit: editing.preview_local(self.project, proposal['id'], cancel=cancel),
            self.show_preview,
        )

    def show_preview(self, result):
        from .preview import PreviewDialog

        if result['photo_id'] not in self.selected_ids():
            return
        if self.preview_dialog is not None:
            self.preview_dialog.close()
        folder = self.project.directory('previews')
        paths = [folder / result[key] for key in ('before', 'after')]
        if any(p.is_symlink() or p.resolve().parent != folder for p in paths):
            return
        self.preview_dialog = PreviewDialog(
            *paths, self, native=lambda: self.show_native_preview(result)
        )
        self.preview_dialog.show()

    def show_native_preview(self, result):
        from ..catalog.views import CompareDialog

        if result['photo_id'] not in self.selected_ids():
            return None
        proposal = self.project.get_proposal(result['proposal_id'])
        if proposal['kind'] != 'local' or proposal['status'] != 'pending':
            return None
        record = self.project.photo(result['photo_id'])
        expected = proposal['payload']['source_sha256']

        def validate_source(cancel):
            photo = editing.check_original(self.project, result['photo_id'], cancel)
            if (
                photo['sha'] != expected
                or self.project.get_proposal(proposal['id'])['status'] != 'pending'
            ):
                raise ValueError('方案或原片已失效，请重新创建预览。')

        base = self.project.preferences().get('recipe:' + record['id'], model.recipe())
        folder = self.project.directory('previews')
        paths = [folder / result[key] for key in ('before', 'after')]
        if any(path.is_symlink() or path.resolve().parent != folder for path in paths):
            return None
        dialog = CompareDialog(
            self.project,
            [dict(record, copy_name='当前调整'), dict(record, copy_name='方案效果')],
            self,
            recipes=[base, proposal['payload']['recipe']],
            preview_paths=paths,
            validator=validate_source,
        )
        dialog.setWindowTitle('方案原像素对比')
        self.catalog_dialogs.append(dialog)
        if self.preview_dialog is not None:
            self.preview_dialog.close()
        self.preview_dialog = dialog
        from ..ui.layout import clamp_window

        clamp_window(dialog)
        dialog.show()
        return dialog

    def annotation(self):
        if len(self.selected_ids()) != 1:
            self.statusBar().showMessage('请选择一张照片。')
            return
        count, ok = QInputDialog.getInt(self, '标注人数', '画面中的人数（-1 表示未知）', -1, -1, 50)
        if not ok:
            return
        tags, ok = QInputDialog.getText(self, '标注标签', '标签（用逗号分隔）')
        if ok:
            self.save_annotation(
                None if count < 0 else count,
                [t.strip() for t in tags.replace('，', ',').split(',') if t.strip()][:12],
            )

    def save_annotation(self, count, tags):
        ids = self.selected_ids()
        if len(ids) != 1:
            raise ValueError('请选择一张照片。')
        if count is not None and (type(count) is not int or not 0 <= count <= 50):
            raise ValueError('人数标注无效。')
        tags = [str(tag)[:100] for tag in tags[:12]]
        self.project.preference('annotation:' + ids[0], dict(person_count=count, tags=tags))
        self.catalog.edit_metadata(ids, {'person_count': count})
        self.catalog.edit_keywords(ids, tags, mode='replace')
        self.project.event(
            'photo.annotated', {'photo_id': ids[0], 'person_count': count, 'tags': tags}
        )

    def apply_proposal(self):
        item = self.proposal_list.currentItem()
        if self.worker or item is None:
            return
        proposal = item.data(Qt.ItemDataRole.UserRole)
        payload = proposal['payload']
        if proposal['kind'] == 'local':
            details = (
                f'{payload["label"]}\nProvider：CCRaw Photo Engine\n上传：无 · 费用：本地处理\n全分辨率 16 位 TIFF 派生版本\n\n'
                + f'裁切：{payload["recipe"]["crop"] or "完整画面"}\n旋转：{payload["recipe"]["rotation"] * 90}°\n拉直：{payload["recipe"]["straighten"]}°\n蒙版：{len(payload["recipe"]["masks"])} 个'
            )
        else:
            upload = payload['upload']
            details = f'Provider：{payload["provider"]}\n模型：{payload["model"]}\n上传：{upload["content"]}\n尺寸：{upload["dimensions"]} · {upload["bytes"]:,} 字节\n费用：{payload["cost"]}\n\n提示词：{payload["prompt"]}'
        dialog = QMessageBox(QMessageBox.Icon.Question, '应用编辑方案', details, parent=self)
        if proposal['kind'] == 'local':
            dialog.setDetailedText(json.dumps(payload['recipe'], ensure_ascii=False, indent=2))
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        dialog.button(QMessageBox.StandardButton.Ok).setText('确认并执行')
        if dialog.exec() != QMessageBox.StandardButton.Ok:
            return
        if proposal['kind'] == 'local':
            operation = lambda cancel, emit: editing.apply_local(
                self.project, proposal['id'], cancel
            )
        else:
            settings = image_generation.load_settings()
            self.client = image_generation.GenerationClient()
            client = self.client
            operation = lambda cancel, emit: editing.execute_external(
                self.project,
                proposal['id'],
                settings,
                approved_digest=payload['disclosure_digest'],
                client=client,
                cancel=cancel,
            )
        self.start_work(
            '创建派生版本',
            operation,
            lambda path: self.statusBar().showMessage(f'已保存派生版本：{Path(path).name}'),
        )

    def open_derivative(self, item):
        self.open_editor(paths=[item.data(Qt.ItemDataRole.UserRole)])

    def open_editor(self, *, paths=None, allow_proxy=False):
        from ..app import MainWindow

        project = self.project
        policy = (
            project.preferences()
            .get('ccraw.settings', {})
            .get('project', {})
            .get('proxy_policy', 'auto')
        )
        explicit_proxy = allow_proxy
        allow_proxy = allow_proxy or policy in ('auto', 'proxy')
        records = [project.photo(i) for i in self.selected_ids()] if paths is None else []
        if records:
            if len({p['path'] for p in records}) != len(records):
                self.statusBar().showMessage('原片与其虚拟副本请分别打开，确保配方独立。')
                return
            try:
                sources = [
                    self.catalog.edit_source(
                        p['id'],
                        allow_proxy=allow_proxy,
                        prefer_proxy=policy == 'proxy' and not explicit_proxy,
                    )
                    for p in records
                ]
            except ValueError as error:
                self.statusBar().showMessage(str(error))
                return
            for record, source in zip(records, sources):
                record['editor_path'] = source['path']
                record['is_proxy'] = source['is_proxy']
        paths = paths or [p.get('editor_path', p['path']) for p in records]
        if not paths:
            self.statusBar().showMessage('请选择照片。')
            return
        editor = MainWindow()
        from ..ui.settings_dialog import install_settings

        install_settings(editor, project_getter=lambda: project)
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.editors.append(editor)
        editor.destroyed.connect(
            lambda: self.editors.remove(editor) if editor in self.editors else None
        )
        editor.add_documents(paths)
        preferences = project.preferences()
        for photo in records:
            recipe = preferences.get('recipe:' + photo['id'])
            metadata = self.catalog.metadata(photo['id'])
            document = editor.documents[photo.get('editor_path', photo['path'])]
            document['metadata'] = {
                **{
                    key: metadata[key]
                    for key in (
                        'title',
                        'description',
                        'copyright',
                        'location',
                        'keywords',
                        'person_count',
                    )
                    if metadata[key] not in ('', None, [])
                    or key in metadata['overridden']
                    or (key == 'keywords' and '_keywords' in metadata['overridden'])
                },
            }
            if metadata['author'] or 'author' in metadata['overridden']:
                document['metadata']['creator'] = metadata['author']
            if recipe:
                document.update(
                    edits=model.validate(recipe),
                    saved_edits=model.validate(recipe),
                    snapshots=model.validate_snapshots(
                        preferences.get('snapshots:' + photo['id'], [])
                    ),
                    saved_snapshots=model.validate_snapshots(
                        preferences.get('snapshots:' + photo['id'], [])
                    ),
                    initialized=True,
                )
        if records:
            action = QAction('保存调整到 Photo Agent', editor)

            by_editor_path = {p.get('editor_path', p['path']): p['id'] for p in records}

            def autosave(payload):
                ref = by_editor_path.get(payload['source_path'])
                if ref and payload.get('recipe'):
                    project.preference('recipe:' + ref, model.validate(payload['recipe']))
                    project.preference('snapshots:' + ref, payload.get('snapshots', []))

            editor.settings_save_document = autosave

            def save():
                # An older recovery write may still be storing its recipe in
                # the executor. Drain it before the explicit latest commit so
                # it cannot overwrite the user's newly saved adjustments.
                binding = getattr(editor, '_settings_binding', None)
                if binding is not None and not binding.flush():
                    editor.statusBar().showMessage('自动保存未完成，请重试保存。')
                    return
                editor.stash_document()
                for photo in records:
                    document = editor.documents.get(photo.get('editor_path', photo['path']))
                    if document and document['initialized']:
                        project.preference(
                            'recipe:' + photo['id'], model.validate(document['edits'])
                        )
                        project.preference('snapshots:' + photo['id'], document['snapshots'])
                        document['saved_edits'] = copy.deepcopy(document['edits'])
                        document['saved_snapshots'] = copy.deepcopy(document['snapshots'])
                editor.saved_edits = copy.deepcopy(editor.edits)
                editor.saved_snapshots = copy.deepcopy(editor.snapshots)
                editor.statusBar().showMessage('已保存到 Agent 工程；原片保留。')

            action.triggered.connect(save)
            editor.menuBar().addAction(action)
        editor.show()
        if any(p.get('is_proxy') for p in records):
            editor.setProperty('catalogProxyOnly', True)

            def blocked_export(*_args, **_kwargs):
                editor.statusBar().showMessage(
                    '原片缺失；全分辨率导出已禁用。请在 Agent 的代理操作中明确输出低分辨率副本。'
                )
                return False

            editor.start_export = blocked_export
            editor.batch_export = blocked_export
            editor.setWindowTitle(editor.windowTitle() + ' · 离线代理编辑（非全分辨率输出）')
            editor.statusBar().showMessage(
                '原片缺失；当前为 16 位代理，调整仍保存到原照片引用。正常全分辨率输出需重关联原片。'
            )
        editor.open_path(paths[0])

    def closeEvent(self, event):
        for dialog in list(getattr(self, 'catalog_dialogs', [])):
            if dialog.isVisible() and not dialog.close():
                event.ignore()
                return
        if self.worker:
            self.closing = True
            self.cancel_work()
            event.ignore()
            return
        # Existing editor windows keep their own unsaved-edit and job guards.
        for editor in list(self.editors):
            if editor.isVisible() and not editor.close():
                event.ignore()
                return
        ACTIVE.pop(str(self.project.path), None)
        event.accept()

    def toggle_chat(self):
        visible = self.chat_panel.isHidden()
        self.chat_panel.setVisible(visible)
        self.chat_toggle.setText('收起对话' if visible else '展开对话')
