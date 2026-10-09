"""Native template browser and serialized image-generation queue."""

from __future__ import annotations

import copy
from collections import deque
from functools import lru_cache
from pathlib import Path
import shutil
import threading

from PySide6.QtCore import QObject, Qt, QSize, Signal
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from . import engine, image_generation as gen, nl_edit

STATUS = dict(
    queued='排队',
    preparing='准备参考图',
    running='等待生成',
    saving='保存结果',
    completed='已完成',
    failed='失败',
    cancelled='已取消',
)


@lru_cache(maxsize=256)
def thumbnail(path, size=96):
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    original = reader.size()
    if original.isValid():
        reader.setScaledSize(original.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio))
    return QPixmap.fromImage(reader.read())


class GenerationSettings(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle('生成设置')
        self.resize(660, 380)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.url = QLineEdit(settings['base_url'])
        self.model = QLineEdit(settings['model'])
        self.key = QLineEdit(settings['key'])
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.timeout = QSpinBox()
        self.timeout.setRange(10, 900)
        self.timeout.setValue(settings['timeout'])
        self.timeout.setSuffix(' 秒')
        self.watermark = QCheckBox('服务商水印')
        self.watermark.setChecked(settings['watermark'])
        for label, widget in [
            ('接口地址', self.url),
            ('模型 / 接入点', self.model),
            ('API Key', self.key),
            ('超时', self.timeout),
            ('', self.watermark),
        ]:
            form.addRow(label, widget)
        layout.addLayout(form)
        note = QLabel(
            'Seedream 或兼容 /images/generations 的服务。\n'
            '生成按服务商计费；提示词与勾选的参考图会发送到该接口。'
        )
        note.setWordWrap(True)
        note.setObjectName('subtle')
        layout.addWidget(note)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        layout.addWidget(buttons)

    def values(self):
        return dict(
            base_url=self.url.text().strip(),
            model=self.model.text().strip(),
            key=self.key.text().strip(),
            timeout=self.timeout.value(),
            watermark=self.watermark.isChecked(),
        )

    def validate(self):
        try:
            nl_edit.validate_endpoint(self.url.text().strip())
            if not self.model.text().strip():
                raise ValueError('请填写模型名称或接入点 ID。')
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.accept()


class TemplateBrowser(QDialog):
    def __init__(self, panel):
        super().__init__(panel)
        self.setWindowTitle('生成主题')
        available = self.screen().availableGeometry()
        self.resize(min(860, available.width() - 48), min(760, available.height() - 64))
        self.selected = None
        self.all_templates = gen.load_templates(panel.store.root)
        layout = QVBoxLayout(self)
        self.modes = QTabBar()
        self.modes.addTab('创意模板生成')
        self.modes.addTab('风格化生成')
        self.modes.setExpanding(True)
        self.modes.setCurrentIndex(1 if panel.mode.currentData() == 'style' else 0)
        layout.addWidget(self.modes)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索主题')
        row.addWidget(self.search, 1)
        self.category = QComboBox()
        row.addWidget(self.category)
        layout.addLayout(row)
        self.items = QListWidget()
        self.items.setViewMode(QListWidget.ViewMode.IconMode)
        self.items.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.items.setMovement(QListWidget.Movement.Static)
        self.items.setIconSize(QSize(140, 140))
        self.items.setGridSize(QSize(166, 188))
        self.items.setWordWrap(True)
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.items, 1)
        row = QHBoxLayout()
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setFixedSize(180, 180)
        row.addWidget(self.image)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(180)
        row.addWidget(self.preview, 1)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.remove = QPushButton('删除自定义模板')
        self.remove.clicked.connect(self.delete_selected)
        row.addWidget(self.remove)
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        self.use = use = QPushButton('使用主题')
        use.setObjectName('primary')
        use.clicked.connect(self.use_selected)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(use)
        layout.addLayout(row)
        self.search.textChanged.connect(self.filter)
        self.category.currentTextChanged.connect(self.filter)
        self.items.currentItemChanged.connect(self.show_prompt)
        self.items.itemDoubleClicked.connect(self.use_selected)
        self.modes.currentChanged.connect(self.mode_changed)
        self.mode_changed()

    def mode_changed(self, *_):
        mode = ('creative', 'style')[self.modes.currentIndex()]
        self.templates = [t for t in self.all_templates if t.get('mode', 'creative') == mode]
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItems(
            ['全部'] + sorted({t.get('category', '自定义') for t in self.templates})
        )
        self.category.blockSignals(False)
        self.filter()

    def filter(self, *_):
        query, category = self.search.text().strip().casefold(), self.category.currentText()
        self.items.clear()
        for template in self.templates:
            if query not in (template['name'] + ' ' + template.get('category', '')).casefold():
                continue
            if category != '全部' and template.get('category', '自定义') != category:
                continue
            item = QListWidgetItem(template['name'])
            item.setData(Qt.ItemDataRole.UserRole, template)
            item.setToolTip(template['prompt'])
            path = gen.RESOURCES / template.get('preview', '')
            if path.is_file():
                item.setIcon(QIcon(thumbnail(path, 140)))
            self.items.addItem(item)
        if self.items.count():
            self.items.setCurrentRow(0)
        else:
            self.preview.clear()
            self.image.clear()
        self.use.setEnabled(bool(self.items.count()))

    def show_prompt(self, current, *_):
        self.preview.setPlainText(
            current.data(Qt.ItemDataRole.UserRole)['prompt'] if current else ''
        )
        self.remove.setEnabled(
            bool(current and current.data(Qt.ItemDataRole.UserRole)['id'].startswith('custom-'))
        )
        path = (
            gen.RESOURCES / current.data(Qt.ItemDataRole.UserRole).get('preview', '')
            if current
            else None
        )
        self.image.setPixmap(
            thumbnail(path, 180)
        ) if path and path.is_file() else self.image.clear()

    def use_selected(self, *_):
        item = self.items.currentItem()
        if item:
            self.selected = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def delete_selected(self):
        item = self.items.currentItem()
        if not item:
            return
        template = item.data(Qt.ItemDataRole.UserRole)
        if (
            QMessageBox.question(
                self,
                '删除模板',
                template['name'],
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            gen.delete_template(template['id'], root=self.parent().store.root)
            self.all_templates = gen.load_templates(self.parent().store.root)
            self.mode_changed()
        except (OSError, ValueError) as error:
            self.preview.setPlainText(str(error))


class Relay(QObject):
    stage = Signal(str, str)
    done = Signal(str, object)


class GenerationPanel(QScrollArea):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.store = gen.GenerationStore()
        self.store.recover()
        self.settings = gen.load_settings()
        self.pending = deque()
        self.current = self.client = None
        self.closed = False
        self.busy = False
        self.external = []
        self.template_name = ''
        self.selected_template = None
        self.mode_state = {}
        self.active_mode = 'creative'
        self.subject_text = ''
        self.relay = Relay(self)
        self.relay.stage.connect(self.show_stage)
        self.relay.done.connect(self.finished)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        self.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 4, 4)
        layout.setSpacing(8)
        row = QHBoxLayout()
        self.service = QLabel('')
        self.service.setObjectName('subtle')
        row.addWidget(self.service, 1)
        settings = QPushButton('设置')
        settings.clicked.connect(self.configure)
        row.addWidget(settings)
        layout.addLayout(row)
        self.mode = QComboBox()
        self.mode.addItem('创意模板生成', 'creative')
        self.mode.addItem('风格化生成', 'style')
        self.mode.addItem('自定义生成', 'custom')
        layout.addWidget(self.mode)
        self.template_actions = QWidget()
        row = QHBoxLayout(self.template_actions)
        row.setContentsMargins(0, 0, 0, 0)
        self.browse = QPushButton('选择创意模板')
        self.browse.clicked.connect(self.browse_templates)
        save = QPushButton('保存模板')
        save.clicked.connect(self.save_template)
        row.addWidget(self.browse)
        row.addWidget(save)
        layout.addWidget(self.template_actions)
        self.template_label = QLabel('')
        self.template_label.setWordWrap(True)
        self.template_label.hide()
        self.effect_preview = QLabel()
        self.effect_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.effect_preview.setFixedSize(64, 64)
        self.effect_preview.hide()
        row = QHBoxLayout()
        row.addWidget(self.effect_preview)
        row.addWidget(self.template_label, 1)
        layout.addLayout(row)
        self.subject = QComboBox()
        self.subject.hide()
        self.subject.currentIndexChanged.connect(self.subject_changed)
        layout.addWidget(self.subject)
        row = QHBoxLayout()
        row.addWidget(QLabel('提示词'), 1)
        self.clear = QPushButton('清空')
        self.clear.setToolTip('清空当前模板选择和提示词')
        self.clear.clicked.connect(self.clear_generation)
        row.addWidget(self.clear)
        layout.addLayout(row)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText('描述要生成的图像')
        self.prompt.setFixedHeight(140)
        layout.addWidget(self.prompt)
        self.reference = QComboBox()
        for label, data in [
            ('当前照片', 'current'),
            ('选中照片', 'selected'),
            ('外部图片', 'external'),
            ('无参考图', 'none'),
        ]:
            self.reference.addItem(label, data)
        layout.addWidget(self.reference)
        self.pick = QPushButton('选择参考图')
        self.pick.clicked.connect(self.pick_reference)
        self.pick.hide()
        layout.addWidget(self.pick)
        self.consent = QCheckBox('发送参考图')
        self.consent.setToolTip('发送当前编辑后的参考图；不包含原始 RAW 或拍摄元数据')
        layout.addWidget(self.consent)
        row = QHBoxLayout()
        self.size = QComboBox()
        self.size.addItems(['2K', '4K'])
        self.size.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.count = QSpinBox()
        self.count.setRange(1, 4)
        self.count.setSuffix(' 张')
        self.count.setToolTip('每张参考图的生成张数；无参考图时为输出张数')
        row.addWidget(self.size, 1)
        row.addWidget(self.count, 1)
        layout.addLayout(row)
        self.actions = QWidget(self)
        self.actions.setAutoFillBackground(True)
        row = QHBoxLayout(self.actions)
        row.setContentsMargins(0, 6, 4, 4)
        self.generate = QPushButton('生成')
        self.generate.setObjectName('primary')
        self.generate.clicked.connect(self.enqueue)
        self.cancel = QPushButton('取消')
        self.cancel.setEnabled(False)
        self.cancel.setToolTip('取消等待及排队任务；已提交的服务商任务可能继续运行并计费')
        self.cancel.clicked.connect(self.cancel_all)
        row.addWidget(self.generate, 1)
        row.addWidget(self.cancel)
        self.setViewportMargins(0, 0, 0, 48)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.hide()
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        row.addWidget(QLabel('生成记录'), 1)
        history = QPushButton('查看全部')
        history.clicked.connect(self.show_history)
        row.addWidget(history)
        layout.addLayout(row)
        self.tasks = QListWidget()
        self.tasks.setIconSize(QSize(48, 48))
        self.tasks.setMinimumHeight(130)
        self.tasks.setMaximumHeight(190)
        self.tasks.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tasks.currentItemChanged.connect(self.selection_changed)
        self.tasks.itemDoubleClicked.connect(self.open_result)
        layout.addWidget(self.tasks)
        row = QHBoxLayout()
        self.open = QPushButton('打开结果')
        self.open.clicked.connect(self.open_result)
        self.export = QPushButton('另存为')
        self.export.clicked.connect(self.export_result)
        row.addWidget(self.open)
        row.addWidget(self.export)
        layout.addLayout(row)
        layout.addStretch()
        self.reference.currentIndexChanged.connect(self.reference_changed)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.update_service()
        self.refresh_tasks()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'actions'):
            self.actions.setGeometry(0, self.height() - 48, self.width(), 48)

    def update_service(self):
        self.service.setText(
            '已配置'
            if self.settings.get('key') or nl_edit._is_local(self.settings['base_url'])
            else '未配置'
        )
        self.service.setToolTip(self.settings['model'])

    def configure(self):
        dialog = GenerationSettings(self, self.settings)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                gen.save_settings(dialog.values())
                self.settings = dialog.values()
                self.update_service()
            except OSError as error:
                self.status.setText('设置未保存：' + str(error))

    def browse_templates(self):
        if self.active_mode == 'custom':
            return
        dialog = TemplateBrowser(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.use_template(dialog.selected)

    def use_template(self, template):
        mode = template.get('mode', 'creative')
        self.mode.setCurrentIndex(self.mode.findData(mode))
        self.selected_template = copy.deepcopy(template)
        self.template_name = template['name']
        self.prompt.setPlainText(template['prompt'])
        self.subject_text = ''
        self.update_template_preview()

    def mode_changed(self, *_):
        self.mode_state[self.active_mode] = dict(
            prompt=self.prompt.toPlainText(),
            template=copy.deepcopy(self.selected_template),
            name=self.template_name,
            subject=self.subject.currentIndex(),
            subject_text=self.subject_text,
        )
        self.active_mode = self.mode.currentData()
        state = self.mode_state.get(self.active_mode, {})
        self.selected_template = state.get('template')
        self.template_name = state.get('name', '')
        self.subject_text = state.get('subject_text', '')
        self.prompt.setPlainText(state.get('prompt', ''))
        self.browse.setText('选择风格' if self.active_mode == 'style' else '选择创意模板')
        self.template_actions.setVisible(self.active_mode != 'custom')
        self.update_template_preview(state.get('subject', 0))

    def clear_generation(self):
        self.selected_template = None
        self.template_name = ''
        self.subject_text = ''
        self.mode_state.pop(self.active_mode, None)
        self.prompt.clear()
        self.update_template_preview()
        self.prompt.setFocus()

    def update_template_preview(self, subject_index=0):
        template = self.selected_template or {}
        self.template_label.setText(self.template_name)
        self.template_label.setVisible(bool(self.template_name) and self.active_mode != 'custom')
        path = gen.RESOURCES / template.get('preview', '')
        available = path.is_file() and self.active_mode != 'custom'
        self.effect_preview.setVisible(available)
        if available:
            self.effect_preview.setPixmap(thumbnail(path, 64))
        else:
            self.effect_preview.clear()
        choices = template.get('subject_choices', [])
        self.subject.blockSignals(True)
        self.subject.clear()
        if choices:
            self.subject.addItem('选择拍摄主体', '')
            for choice in choices:
                self.subject.addItem(choice['label'], choice['prompt'])
            self.subject.setCurrentIndex(max(0, min(subject_index, len(choices))))
        self.subject.blockSignals(False)
        self.subject.setVisible(bool(choices) and self.active_mode != 'custom')

    def subject_changed(self, *_):
        value = self.subject.currentData() or ''
        old = self.subject_text or '{拍摄主体}'
        text = self.prompt.toPlainText()
        if old in text:
            text = text.replace(old, value or '{拍摄主体}')
        elif value:
            text += '\n拍摄主体：' + value
        self.prompt.setPlainText(text)
        self.subject_text = value

    def save_template(self):
        name, accepted = QInputDialog.getText(self, '保存模板', '名称', text=self.template_name)
        if accepted:
            try:
                source = self.selected_template or {}
                prompt = self.prompt.toPlainText()
                template = gen.save_template(
                    name,
                    prompt,
                    root=self.store.root,
                    mode=self.active_mode,
                    requires_reference=source.get('requires_reference', False),
                    subject_choices=source.get('subject_choices')
                    if '{拍摄主体}' in prompt
                    else None,
                )
                self.selected_template = template
                self.template_name = template['name']
                self.subject_text = ''
                self.update_template_preview()
                self.status.setText('模板已保存')
            except (ValueError, OSError) as error:
                self.status.setText(str(error))

    def reference_changed(self, *_):
        mode = self.reference.currentData()
        self.pick.setVisible(mode == 'external')
        self.consent.setVisible(mode != 'none')
        self.consent.setChecked(False)

    def pick_reference(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '参考图', '', engine.PHOTO_FILTER)
        if paths:
            self.external = paths
            self.pick.setText(f'参考图 · {len(paths)} 张')
            self.pick.setToolTip('\n'.join(paths))
            self.consent.setChecked(False)

    def references(self):
        mode = self.reference.currentData()
        if mode == 'none':
            return [('', None, True)]
        if not self.consent.isChecked():
            raise gen.GenerationError('请勾选“发送参考图”，或选择“无参考图”。')
        paths = (
            [self.owner.source_path]
            if mode == 'current'
            else self.owner.selected_paths()
            if mode == 'selected'
            else self.external
        )
        if not paths or any(not path or not Path(path).is_file() for path in paths):
            raise gen.GenerationError('请选择参考图。')
        result = []
        for path in paths:
            path = str(Path(path).resolve())
            document = self.owner.documents.get(path, {})
            edits = self.owner.edits if path == self.owner.source_path else document.get('edits')
            initialized = path == self.owner.source_path or document.get('initialized', False)
            result.append((path, copy.deepcopy(edits), initialized))
        return result

    def enqueue(self):
        try:
            prompt = self.prompt.toPlainText().strip()
            if not prompt or len(prompt) > 12000:
                raise gen.GenerationError('请输入提示词（最多 12000 字）。')
            template = self.selected_template or {}
            if '{拍摄主体}' in prompt:
                raise gen.GenerationError('请选择拍摄主体，或在提示词中填写主体。')
            if template.get('subject_choices') and not self.subject.currentData():
                raise gen.GenerationError('请选择拍摄主体。')
            if template.get('requires_reference') and self.reference.currentData() == 'none':
                raise gen.GenerationError('此模板需要参考图，请选择当前照片或外部图片。')
            references = self.references()
            if len(references) * self.count.value() + len(self.pending) + int(self.busy) > 32:
                raise gen.GenerationError('生成队列最多 32 张。')
            nl_edit.validate_endpoint(self.settings['base_url'])
            if not self.settings['model'].strip():
                raise gen.GenerationError('请在生成设置中填写模型。')
            if not self.settings.get('key') and not nl_edit._is_local(self.settings['base_url']):
                raise gen.GenerationError('请在生成设置中填写 API Key。')
            for path, edits, initialized in references:
                for _ in range(self.count.value()):
                    task = self.store.enqueue(
                        self.template_name or prompt[:30],
                        prompt,
                        reference=path,
                        recipe=edits,
                        size=self.size.currentText(),
                        initialized=initialized,
                        mode=self.active_mode,
                    )
                    self.pending.append((task, copy.deepcopy(self.settings)))
            self.refresh_tasks()
            self.next_task()
        except (ValueError, OSError) as error:
            self.status.setText(str(error))

    def next_task(self):
        if self.closed or self.busy or not self.pending:
            return
        task, settings = self.pending.popleft()
        identifier = task['id']
        self.current = identifier
        self.client = client = gen.GenerationClient()
        self.busy = True
        self.cancel.setEnabled(True)
        self.progress.show()
        self.status.setText('准备参考图' if task['reference'] else '等待生成')
        store, relay = self.store, self.relay

        def stage(status):
            client.check_cancel()
            store.update(identifier, status)
            relay.stage.emit(identifier, status)

        def work():
            error = None
            try:
                reference = None
                if task['reference']:
                    stage('preparing')
                    reference = gen.prepare_reference(
                        task['reference'],
                        task['recipe'],
                        client.cancelled,
                        initialized=bool(task['initialized']),
                    )
                stage('running')
                data = client.generate(settings, task['prompt'], reference, task['size'])
                stage('saving')
                store.save_result(identifier, data)
            except Exception as failure:
                error = failure
                from .logs import redact

                store.update(
                    identifier,
                    'cancelled' if isinstance(failure, InterruptedError) else 'failed',
                    redact(str(failure), [settings.get('key', '')]),
                )
            finally:
                try:
                    relay.done.emit(identifier, error)
                except RuntimeError:
                    pass

        threading.Thread(target=work, daemon=True, name='ccraw-generation').start()

    def show_stage(self, identifier, stage):
        if not self.closed and identifier == self.current:
            self.status.setText(STATUS[stage] + f' · 排队 {len(self.pending)} 张')
            self.refresh_tasks()

    def finished(self, identifier, error):
        if self.closed or identifier != self.current:
            return
        self.busy = False
        self.current = self.client = None
        self.cancel.setEnabled(False)
        self.progress.hide()
        if error is not None and not isinstance(error, InterruptedError):
            for task, _ in self.pending:
                self.store.update(task['id'], 'cancelled', '上一任务失败')
            self.pending.clear()
            self.status.setText(self.store.get(identifier)['message'])
        else:
            self.status.setText('已取消' if error else '生成完成')
        self.refresh_tasks()
        self.next_task()

    def cancel_all(self):
        if self.client:
            self.client.cancel()
        if self.current:
            self.store.update(self.current, 'cancelled')
        for task, _ in self.pending:
            self.store.update(task['id'], 'cancelled')
        self.pending.clear()
        self.cancel.setEnabled(False)
        self.status.setText('已取消等待；已提交任务可能继续计费')
        self.refresh_tasks()

    def shutdown(self):
        self.closed = True
        self.cancel_all()

    def refresh_tasks(self):
        current = self.tasks.currentItem()
        selected = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.tasks.clear()
        for task in self.store.history(40):
            item = QListWidgetItem(task['title'] + '\n' + STATUS[task['status']])
            item.setData(Qt.ItemDataRole.UserRole, task['id'])
            item.setToolTip(task['message'] or task['title'])
            path = self.store.preview_path(task)
            if path:
                item.setIcon(QIcon(thumbnail(path, 48)))
            self.tasks.addItem(item)
            if task['id'] == selected:
                self.tasks.setCurrentItem(item)
        if self.tasks.currentRow() < 0 and self.tasks.count():
            self.tasks.setCurrentRow(0)
        self.selection_changed()

    def selected_task(self):
        item = self.tasks.currentItem()
        return self.store.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def selected_result(self):
        task = self.selected_task()
        return self.store.result_path(task) if task else None

    def selection_changed(self, *_):
        ready = self.selected_result() is not None
        self.open.setEnabled(ready)
        self.export.setEnabled(ready)

    def open_result(self, *_):
        path = self.selected_result()
        if path:
            self.owner.import_paths([str(path)])

    def export_result(self):
        path = self.selected_result()
        if path:
            self.copy_result(path, self)

    def copy_result(self, path, parent):
        target, _ = QFileDialog.getSaveFileName(
            parent, '另存为', str(Path.home() / path.name), f'生成图片 (*{path.suffix})'
        )
        if not target:
            return
        target = Path(target)
        if target.suffix.lower() != path.suffix:
            target = target.with_suffix(path.suffix)
        try:
            if path.resolve() != target.resolve():
                shutil.copyfile(path, target)
            self.status.setText('已保存：' + str(target))
        except OSError as error:
            self.status.setText('保存失败：' + str(error))

    def show_history(self):
        dialog = HistoryDialog(self)
        dialog.exec()


class HistoryDialog(QDialog):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.setWindowTitle('生成记录')
        self.resize(840, 640)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.items = QListWidget()
        self.items.setIconSize(QSize(56, 56))
        self.items.setMaximumWidth(260)
        self.items.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.preview = QLabel('')
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(180, 180)
        row.addWidget(self.items, 1)
        row.addWidget(self.preview, 2)
        layout.addLayout(row, 1)
        self.prompt = QPlainTextEdit()
        self.prompt.setReadOnly(True)
        self.prompt.setFixedHeight(130)
        layout.addWidget(self.prompt)
        row = QHBoxLayout()
        self.reuse = QPushButton('复用提示词')
        self.reuse.clicked.connect(self.restore_prompt)
        self.open = QPushButton('打开结果')
        self.open.clicked.connect(self.open_result)
        self.export = QPushButton('另存为')
        self.export.clicked.connect(self.export_result)
        self.remove = QPushButton('删除记录')
        self.remove.clicked.connect(self.delete_task)
        for button in (self.reuse, self.open, self.export, self.remove):
            row.addWidget(button)
        layout.addLayout(row)
        self.items.currentItemChanged.connect(self.show_task)
        for task in panel.store.history(500):
            item = QListWidgetItem(task['title'] + '\n' + STATUS[task['status']])
            item.setData(Qt.ItemDataRole.UserRole, task['id'])
            item.setToolTip(task['message'])
            path = panel.store.preview_path(task)
            if path:
                item.setIcon(QIcon(thumbnail(path, 56)))
            self.items.addItem(item)
        if self.items.count():
            self.items.setCurrentRow(0)
        else:
            self.show_task(None)

    def show_task(self, item, *_):
        self.task = self.panel.store.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        self.path = self.panel.store.result_path(self.task) if self.task else None
        self.prompt.setPlainText(self.task['prompt'] if self.task else '')
        self.preview.clear()
        if self.path:
            self.preview.setPixmap(
                thumbnail(self.panel.store.preview_path(self.task) or self.path, 400)
            )
        else:
            self.preview.setText(
                self.task['message'] or STATUS[self.task['status']] if self.task else '暂无记录'
            )
        self.reuse.setEnabled(self.task is not None)
        self.open.setEnabled(self.path is not None)
        self.export.setEnabled(self.path is not None)
        linked = self.path and str(self.path) in self.panel.owner.documents
        self.remove.setEnabled(
            bool(
                self.task
                and self.task['status'] not in gen.ACTIVE
                and not linked
                and self.task['id'] != self.panel.current
            )
        )
        self.remove.setToolTip('请先从选片集中移除该图片' if linked else '删除记录及保存的生成图片')

    def restore_prompt(self):
        if self.task:
            self.panel.mode.setCurrentIndex(
                self.panel.mode.findData(self.task.get('mode', 'creative'))
            )
            self.panel.selected_template = None
            self.panel.subject_text = ''
            self.panel.prompt.setPlainText(self.task['prompt'])
            self.panel.template_name = self.task['title']
            self.panel.update_template_preview()
            self.accept()

    def open_result(self):
        if self.path:
            self.panel.owner.import_paths([str(self.path)])
            self.accept()

    def export_result(self):
        if self.path:
            self.panel.copy_result(self.path, self)

    def delete_task(self):
        if (
            not self.task
            or self.task['status'] in gen.ACTIVE
            or self.task['id'] == self.panel.current
            or (self.path and str(self.path) in self.panel.owner.documents)
        ):
            return
        if (
            QMessageBox.question(
                self,
                '删除生成记录',
                '删除此记录及保存的生成图片？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.panel.store.delete(self.task['id'])
            row = self.items.currentRow()
            self.items.takeItem(row)
            self.show_task(self.items.currentItem())
            self.panel.refresh_tasks()
        except (ValueError, OSError) as error:
            self.prompt.setPlainText(str(error))
