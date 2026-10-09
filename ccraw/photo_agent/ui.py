"""Two workspaces, cancellable background work and explicit edit approval."""

import asyncio
import copy
import html
import json
from pathlib import Path
import threading
import weakref

from PySide6.QtCore import Qt, QSize, QThread, Signal, QTimer
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
    QAbstractItemView,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QPlainTextEdit,
    QLineEdit,
    QProgressBar,
    QInputDialog,
    QDialog,
)

from .. import resources, nl_edit, model, image_generation
from . import analysis, editing, search
from .models import LocalModels, install_models
from .runtime import run_turn
from .store import Project

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


class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        ensure_font()
        self.windows = []
        self.setWindowTitle('CCRaw')
        self.setWindowIcon(QIcon(str(resources.asset_path('ccraw.ico'))))
        self.resize(860, 460)
        self.setMinimumSize(720, 380)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(40, 32, 40, 32)
        title = QLabel('CCRaw')
        title.setStyleSheet('font-size: 32px; font-weight: 600;')
        layout.addWidget(title)
        row = QHBoxLayout()
        self.catalog_button = button('照片编辑', self.open_catalog)
        self.agent_button = button('Photo Agent', self.new_agent, primary=True)
        for card in (self.catalog_button, self.agent_button):
            card.setMinimumHeight(150)
            card.setStyleSheet(
                'font-size: 24px; font-weight: 600; min-height:150px; max-height:180px;'
            )
            row.addWidget(card)
        layout.addLayout(row, 1)
        actions = QHBoxLayout()
        actions.addWidget(QLabel('RAW · 调色 · 蒙版 · 裁切 · 导出'))
        actions.addStretch()
        actions.addWidget(button('打开 Agent 工程', self.open_agent))
        layout.addLayout(actions)
        self.setCentralWidget(root)

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
        key = str(Path(path).resolve())
        if key in ACTIVE:
            ACTIVE[key].show()
            ACTIVE[key].raise_()
            ACTIVE[key].activateWindow()
            return ACTIVE[key]
        try:
            project = Project.create(path, Path(path).stem) if create else Project.open(path)
            project.recover()
            window = self.retain(AgentWindow(project))
            ACTIVE[str(project.path)] = window
            return window
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, '工程', str(error))


class AgentWindow(QMainWindow):
    def __init__(self, project):
        super().__init__()
        ensure_font()
        self.project = project
        self.worker = None
        self.client = None
        self.models = None
        self.editors = []
        self.closing = False
        self.offset = 0
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
        layout.addLayout(top)
        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
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
        left_layout.addLayout(finder)
        self.gallery = QListWidget()
        self.gallery.setViewMode(QListWidget.ViewMode.IconMode)
        self.gallery.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.gallery.setMovement(QListWidget.Movement.Static)
        self.gallery.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.gallery.setIconSize(QSize(180, 126))
        self.gallery.setGridSize(QSize(210, 168))
        self.gallery.setSpacing(4)
        self.gallery.itemDoubleClicked.connect(lambda _: self.open_editor())
        left_layout.addWidget(self.gallery, 1)
        pages = QHBoxLayout()
        pages.addWidget(button('上一页', lambda: self.page(-1)))
        self.count_label = QLabel()
        pages.addWidget(self.count_label, 1)
        pages.addWidget(button('下一页', lambda: self.page(1)))
        left_layout.addLayout(pages)
        photo_actions = QHBoxLayout()
        self.editor_button = button('照片编辑', self.open_editor)
        photo_actions.addWidget(self.editor_button)
        annotate = button('标注人数 / 标签', self.annotation)
        photo_actions.addWidget(annotate)
        recommend = button('推荐修图', self.recommend)
        photo_actions.addWidget(recommend)
        self.busy_controls.extend((annotate, recommend, self.editor_button))
        left_layout.addLayout(photo_actions)
        self.tabs = QTabWidget()
        self.suggestion_list = QListWidget()
        self.suggestion_list.itemClicked.connect(self.select_group)
        self.tabs.addTab(self.suggestion_list, '整理建议')
        proposal_panel = QWidget()
        proposal_layout = QVBoxLayout(proposal_panel)
        self.proposal_list = QListWidget()
        proposal_layout.addWidget(self.proposal_list)
        self.apply_button = button('查看并应用方案', self.apply_proposal, primary=True)
        proposal_layout.addWidget(self.apply_button)
        self.tabs.addTab(proposal_panel, '编辑方案')
        self.derivative_list = QListWidget()
        self.derivative_list.itemDoubleClicked.connect(self.open_derivative)
        self.tabs.addTab(self.derivative_list, '派生版本')
        self.trace = QTextBrowser()
        self.tabs.addTab(self.trace, '执行记录')
        self.tabs.setMaximumHeight(270)
        left_layout.addWidget(self.tabs)
        splitter.addWidget(left)
        right = QWidget()
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
        layout.addWidget(splitter, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(12)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        self.setCentralWidget(root)
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
        report = analysis.suggestions(self.project)
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
        from ..nl_panel import SettingsDialog

        dialog = SettingsDialog(self, nl_edit.load_settings())
        # Agent sends bounded text context; image upload belongs to edit approval.
        dialog.attach.hide()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            nl_edit.save_settings(dialog.values())
            self.refresh_provider()

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
        count = (
            len(self.result_ids)
            if self.result_ids is not None
            else self.project.summary()['photos']
        )
        self.offset = max(0, min(max(0, (count - 1) // 200 * 200), self.offset + step * 200))
        self.refresh_gallery()

    def refresh_gallery(self, result=None):
        selected = set(self.selected_ids())
        self.gallery.clear()
        records = (
            self.project.photos(200, self.offset)
            if self.result_ids is None
            else [self.project.photo(i) for i in self.result_ids[self.offset : self.offset + 200]]
        )
        if result:
            self.evidence = {p['id']: p['evidence'] for p in result.get('photos', [])}
        evidence = self.evidence if self.result_ids is not None else {}
        for photo in records:
            facts = photo['facts']
            item = QListWidgetItem(Path(photo['path']).name[:28])
            item.setData(Qt.ItemDataRole.UserRole, photo['id'])
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
                item.setIcon(QIcon(str(preview)))
            self.gallery.addItem(item)
            item.setSelected(photo['id'] in selected)
        summary = self.project.summary()
        count = len(self.result_ids) if self.result_ids is not None else summary['photos']
        self.count_label.setText(
            f'{count} 张 · 已索引 {summary["indexed"]} · {self.offset + 1 if count else 0}–{min(count, self.offset + 200)}'
        )

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
        for kind, label in labels.items():
            for group in self.report.get(kind, [])[:200]:
                ids = group.get('photos', [group['photo_id']] if 'photo_id' in group else [])
                details = group.get('label') or ' · '.join(group.get('reasons', []))
                item = QListWidgetItem(
                    f'{label} · {len(ids)} 张' + (f' · {details}' if details else '')
                )
                item.setData(Qt.ItemDataRole.UserRole, ids)
                item.setToolTip(json.dumps(group, ensure_ascii=False, indent=2)[:3000])
                self.suggestion_list.addItem(item)
        coverage = self.report.get('coverage', {})
        self.suggestion_list.setToolTip(
            f'整理建议分析 {coverage.get("analyzed", 0)} / {coverage.get("total", 0)} 张；建议需要人工判断。'
        )

    def select_group(self, item):
        self.result_ids = item.data(Qt.ItemDataRole.UserRole)
        self.offset = 0
        self.refresh_gallery()
        for index in range(self.gallery.count()):
            self.gallery.item(index).setSelected(True)

    def refresh_records(self):
        self.proposal_list.clear()
        for proposal in self.project.proposals():
            if proposal['status'] != 'pending':
                continue
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
        if len(ids) != 1:
            self.statusBar().showMessage('请选择一张照片。')
            return
        self.start_work(
            '本地诊断并创建方案',
            lambda cancel, emit: editing.propose_local(self.project, ids[0]),
            lambda _: self.tabs.setCurrentIndex(1),
        )

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
                + json.dumps(payload['recipe']['adjustments'], ensure_ascii=False, indent=2)
            )
        else:
            upload = payload['upload']
            details = f'Provider：{payload["provider"]}\n模型：{payload["model"]}\n上传：{upload["content"]}\n尺寸：{upload["dimensions"]} · {upload["bytes"]:,} 字节\n费用：{payload["cost"]}\n\n提示词：{payload["prompt"]}'
        dialog = QMessageBox(QMessageBox.Icon.Question, '应用编辑方案', details, parent=self)
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

    def open_editor(self, *, paths=None):
        from ..app import MainWindow

        project = self.project
        records = [project.photo(i) for i in self.selected_ids()] if paths is None else []
        paths = paths or [p['path'] for p in records]
        if not paths:
            self.statusBar().showMessage('请选择照片。')
            return
        editor = MainWindow()
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.editors.append(editor)
        editor.destroyed.connect(
            lambda: self.editors.remove(editor) if editor in self.editors else None
        )
        editor.add_documents(paths)
        preferences = project.preferences()
        for photo in records:
            recipe = preferences.get('recipe:' + photo['id'])
            if recipe:
                document = editor.documents[photo['path']]
                document.update(
                    edits=model.validate(recipe),
                    saved_edits=model.validate(recipe),
                    initialized=True,
                )
        if records:
            action = QAction('保存调整到 Photo Agent', editor)

            def save():
                editor.stash_document()
                for photo in records:
                    document = editor.documents.get(photo['path'])
                    if document and document['initialized']:
                        project.preference(
                            'recipe:' + photo['id'], model.validate(document['edits'])
                        )
                        document['saved_edits'] = copy.deepcopy(document['edits'])
                        document['saved_snapshots'] = copy.deepcopy(document['snapshots'])
                editor.saved_edits = copy.deepcopy(editor.edits)
                editor.saved_snapshots = copy.deepcopy(editor.snapshots)
                editor.statusBar().showMessage('已保存到 Agent 工程；原片保留。')

            action.triggered.connect(save)
            editor.menuBar().addAction(action)
        editor.show()
        editor.open_path(paths[0])

    def closeEvent(self, event):
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
