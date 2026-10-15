"""One settings dialog, live application broadcasts and window recovery bindings."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
import uuid
import weakref
from shiboken6 import isValid

from PySide6.QtCore import QObject, QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QKeySequence, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QSplitter,
)

from .. import host, nl_edit, image_generation, compute, engine
from .. import i18n
from ..settings import DEFAULTS, CacheManager, WorkspaceStore, get_service
from ..recovery import AutosaveController, RecoveryStore, backup_database
from ..scheduler import Activity
from .layout import clamp_window, wrap_toolbar
from . import theme

_DIALOGS = weakref.WeakKeyDictionary()
_BINDINGS = weakref.WeakSet()
SETTINGS_DIRECTORY = [
    ('工作区', [('general', '常规'), ('appearance', '外观与语言'), ('project', '工程偏好')]),
    ('照片与色彩', [('import', '导入'), ('display', '显示与色彩')]),
    (
        '性能与存储',
        [('performance', '计算'), ('preview_cache', '预览与缓存'), ('maintenance', '维护与诊断')],
    ),
    (
        'AI 与服务',
        [
            ('ai', '对话与修图'),
            ('generation', '图像生成'),
            ('voice', '语音'),
            ('local_models', '本地模型'),
        ],
    ),
    ('工作流', [('external', '外部编辑')]),
    ('保存与恢复', [('autosave', '自动保存'), ('backup', '工程备份'), ('recovery', '会话恢复')]),
]
PAGES = [page for _, children in SETTINGS_DIRECTORY for page in children]
PAGE_ALIASES = {'models': 'ai', 'compute': 'performance', 'cache': 'preview_cache'}
FIELD_PAGES = {
    'performance.thumbnail_disk_mib': 'preview_cache',
    'performance.preview_limit': 'preview_cache',
    'recovery.autosave': 'autosave',
    'recovery.delay_ms': 'autosave',
    'recovery.backups': 'backup',
}
PROJECT_PAGES = frozenset(('import', 'project'))
PAGE_GROUPS = {
    'general': '启动与确认',
    'appearance': '界面偏好',
    'project': '浏览与代理',
    'import': '默认风格',
    'display': '色彩管理',
    'performance': '计算资源',
    'preview_cache': '预览预算',
    'maintenance': '缓存维护',
    'ai': '模型服务',
    'generation': '生成服务',
    'voice': '语音输入',
    'local_models': '模型安装',
    'external': '交换设置',
    'autosave': '自动保存策略',
    'backup': '备份策略',
    'recovery': '可恢复会话',
}


def _directory_bytes(directory):
    """Read occupancy only; skip directory links and avoid traversing source trees."""
    directory = Path(directory)
    if not directory.is_dir() or directory.is_symlink() or directory.is_junction():
        return 0
    total = 0
    for parent, children, files in os.walk(directory, followlinks=False):
        children[:] = [
            name
            for name in children
            if not (Path(parent) / name).is_junction() and not (Path(parent) / name).is_symlink()
        ]
        for name in files:
            path = Path(parent) / name
            try:
                if not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
    return total


def _busy():
    for binding in tuple(_BINDINGS):
        window = binding.window()
        if window is None or not isValid(window):
            continue
        work = getattr(window, 'work', None)
        # A retained editor can be marked "closing" after all jobs drained.
        # Only actual activities may block changing the shared compute device.
        if (work is not None and any(work.active(activity) for activity in Activity)) or getattr(
            window, 'worker', None
        ):
            return True
        panel = getattr(window, 'generation_panel', None)
        if panel is not None and getattr(panel, 'busy', False):
            return True
    return False


class SettingsDialog(QDialog):
    model_progress = Signal(int, str)
    model_finished = Signal(str)

    def __init__(self, owner, service=None, project_getter=None):
        super().__init__(owner)
        self.service = service or get_service()
        self.owner = weakref.ref(owner)
        self._model_future = None
        self._model_busy = False
        self._model_cancel = threading.Event()
        self._model_hashes = {}
        self.model_progress.connect(self._model_progress)
        self.model_finished.connect(self._model_finished)
        self.project_getter = project_getter or (lambda: None)
        self.setWindowTitle('设置')
        self.resize(1040, 740)
        self.fields = {}
        layout = QVBoxLayout(self)
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel('设置范围'))
        self.scope = QComboBox()
        self.scope.addItem('应用默认值', 'application')
        if self.project_getter() is not None:
            self.scope.addItem('工程覆盖', 'project')
        scope_row.addWidget(self.scope)
        scope_row.addStretch()
        share = QPushButton('分享非秘密设置')
        share.clicked.connect(self.share)
        scope_row.addWidget(share)
        layout.addLayout(scope_row)
        self.scope_hint = QLabel()
        self.scope_hint.setWordWrap(True)
        self.scope_hint.setObjectName('subtle')
        layout.addWidget(self.scope_hint)
        content = QSplitter(Qt.Orientation.Horizontal)
        content.setObjectName('settings-content')
        content.setChildrenCollapsible(False)
        self.navigation = QTreeWidget()
        self.navigation.setObjectName('settings-navigation')
        self.navigation.setAccessibleName('设置目录')
        self.navigation.setHeaderHidden(True)
        self.navigation.setRootIsDecorated(True)
        self.navigation.setIndentation(18)
        self.navigation.setMinimumWidth(180)
        content.addWidget(self.navigation)
        # Preserve the current-page API used by existing settings entry points.
        self.tabs = QStackedWidget()
        self.tabs.setObjectName('settings-pages')
        content.addWidget(self.tabs)
        content.setStretchFactor(1, 1)
        content.setSizes([210, 800])
        layout.addWidget(content, 1)
        self.pages = {}
        self.page_layouts = {}
        self.navigation_items = {}
        for group_title, children in SETTINGS_DIRECTORY:
            group = QTreeWidgetItem(self.navigation, [group_title])
            group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            group.setExpanded(True)
            for key, title in children:
                item = QTreeWidgetItem(group, [title])
                item.setData(0, Qt.ItemDataRole.UserRole, key)
                self.navigation_items[key] = item
        for key, title in PAGES:
            page = QScrollArea()
            page.setWidgetResizable(True)
            page.setProperty('settings_page', key)
            body = QWidget()
            body_layout = QVBoxLayout(body)
            heading = QLabel(title)
            heading.setObjectName('settings-page-title')
            body_layout.addWidget(heading)
            self.page_layouts[key] = body_layout
            form = self._group(key, PAGE_GROUPS[key])
            body_layout.addStretch()
            page.setWidget(body)
            self.tabs.addWidget(page)
            self.pages[key] = (page, form)
        theme.style_hierarchy(self.navigation)
        self.navigation.currentItemChanged.connect(self._navigate)
        self.tabs.currentChanged.connect(self._sync_navigation)
        self._build()
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        footer = QHBoxLayout()
        reset = self.reset_button = QPushButton('恢复当前页默认值')
        reset.clicked.connect(self.reset_page)
        footer.addWidget(reset)
        footer.addStretch()
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons)
        layout.addLayout(footer)
        self.scope.currentIndexChanged.connect(self.load)
        self.load()
        clamp_window(self)
        i18n.localize_window(self, self.service.read()['appearance']['language'])

    def _group(self, page, title):
        group = QGroupBox(title)
        group.setProperty('settings_group', page)
        form = QFormLayout(group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        body = self.page_layouts[page]
        if body.count() and body.itemAt(body.count() - 1).spacerItem():
            body.insertWidget(body.count() - 1, group)
        else:
            body.addWidget(group)
        return form

    def _navigate(self, item, previous=None):
        if item is not None:
            key = item.data(0, Qt.ItemDataRole.UserRole)
            if key in self.pages:
                self.tabs.setCurrentWidget(self.pages[key][0])

    def _sync_navigation(self, *_):
        key = self.tabs.currentWidget().property('settings_page')
        self.navigation.setCurrentItem(self.navigation_items[key])
        if hasattr(self, 'reset_button'):
            self.reset_button.setEnabled(key not in ('maintenance', 'local_models', 'recovery'))

    def select_page(self, key):
        key = PAGE_ALIASES.get(key, key)
        if self.scope.currentData() == 'project' and key not in PROJECT_PAGES:
            key = 'import'
        if key in self.pages:
            self.tabs.setCurrentWidget(self.pages[key][0])
            self._sync_navigation()

    def _update_scope(self):
        project = self.scope.currentData() == 'project'
        for key, item in self.navigation_items.items():
            item.setHidden(project and key not in PROJECT_PAGES)
        for index in range(self.navigation.topLevelItemCount()):
            item = self.navigation.topLevelItem(index)
            item.setHidden(all(item.child(i).isHidden() for i in range(item.childCount())))
        i18n.set_message(
            self.scope_hint,
            (
                '仅覆盖当前工程的导入与工程偏好；其他设置使用应用默认值。'
                if project
                else '应用默认值用于所有工作区；工程可单独覆盖导入与工程偏好。'
            ),
        )
        self.select_page(self.tabs.currentWidget().property('settings_page'))

    def _field(self, section, key, label, widget):
        path = section + '.' + key
        self.fields[path] = widget
        self.pages[FIELD_PAGES.get(path, section)][1].addRow(label, widget)
        widget.setAccessibleName(label or widget.text())
        return widget

    def _combo(self, section, key, label, choices):
        widget = QComboBox()
        for title, value in choices:
            widget.addItem(title, value)
        return self._field(section, key, label, widget)

    def _spin(self, section, key, label, minimum, maximum, suffix=''):
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setSuffix(suffix)
        return self._field(section, key, label, widget)

    def _check(self, section, key, label):
        return self._field(section, key, '', QCheckBox(label))

    def _text(self, section, key, label):
        return self._field(section, key, label, QLineEdit())

    def _build(self):
        self._check('general', 'offer_recovery', '启动时提示恢复会话')
        reset_layout = QPushButton('恢复默认布局')
        reset_layout.clicked.connect(self.reset_layout)
        self._group('general', '工作区布局').addRow('', reset_layout)
        confirmation = self._check('general', 'confirm_remote', '外部执行前确认费用与上传')
        confirmation.setProperty('settings_readonly', True)
        confirmation.setEnabled(False)
        self._combo(
            'appearance',
            'theme',
            '外观',
            [('浅色', 'light'), ('深色', 'dark'), ('跟随系统', 'system')],
        )
        self._combo('appearance', 'language', '界面语言', [('中文', 'zh'), ('English', 'en')])
        self._combo('performance', 'backend', '计算设备', [('自动', 'auto'), ('CPU', 'cpu')])
        self._combo(
            'performance',
            'render_memory_mib',
            '渲染内存预算',
            [('自动', 0), ('512 MiB', 512), ('1 GiB', 1024), ('2 GiB', 2048), ('4 GiB', 4096)],
        )
        self._spin('performance', 'thumbnail_disk_mib', '缩略图磁盘预算', 64, 65536, ' MiB')
        self._combo(
            'performance',
            'preview_limit',
            '实时适屏预览上限',
            [('自动', 0), ('1024', 1024), ('1600', 1600), ('2048', 2048), ('4096', 4096)],
        )
        self.device = QLabel()
        self.device.setWordWrap(True)
        self.pages['performance'][1].addRow('实际设备', self.device)
        self.cache = CacheManager(busy=_busy)
        self.storage = QLabel()
        self.storage.setWordWrap(True)
        self._group('preview_cache', '存储用量').addRow('缓存目录', self.storage)
        maintenance = self.pages['maintenance'][1]
        clear = QPushButton('清理缓存')
        clear.clicked.connect(self.clear_cache)
        maintenance.addRow('', clear)
        diagnostics_form = self._group('maintenance', '诊断与任务')
        diagnostics = QPushButton('复制诊断')
        diagnostics.clicked.connect(self.copy_diagnostics)
        diagnostics_form.addRow('', diagnostics)
        tasks = QPushButton('任务')
        from .task_center import show_tasks

        tasks.clicked.connect(lambda: show_tasks(self.owner() or self))
        diagnostics_form.addRow('', tasks)
        logs = QPushButton('打开日志')
        logs.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(host.log_folder())))
        )
        diagnostics_form.addRow('', logs)
        presets = [('无', 'none'), ('中性', 'neutral'), ('鲜艳', 'vivid'), ('黑白', 'monochrome')]
        self._combo('import', 'raw_preset', 'RAW 默认风格', presets)
        self._combo('import', 'other_preset', '其他图片默认风格', presets)
        self.rules = QTextEdit()
        self.rules.setMaximumHeight(130)
        self._group('import', '相机规则').addRow('按相机规则', self.rules)
        self.rules.setPlaceholderText('[{"camera":"Exact Camera","preset":"neutral"}]')
        self.keywords = QLineEdit()
        self.creator, self.copyright = QLineEdit(), QLineEdit()
        metadata = self._group('import', '默认元数据')
        metadata.addRow('关键词', self.keywords)
        metadata.addRow('作者', self.creator)
        metadata.addRow('版权', self.copyright)
        preview = QPushButton('实际导入预览')
        preview.clicked.connect(self.import_preview)
        metadata.addRow('', preview)
        for key, label, choices in (
            (
                'proxy_policy',
                '代理策略',
                [('自动', 'auto'), ('原片优先', 'original'), ('代理优先', 'proxy')],
            ),
            (
                'album_sort',
                '相册排序',
                [('拍摄时间', 'capture'), ('文件名', 'name'), ('评级', 'rating')],
            ),
        ):
            widget = QComboBox()
            for title, value in choices:
                widget.addItem(title, value)
            self.fields['project.' + key] = widget
            widget.setAccessibleName(label)
            self.pages['project'][1].addRow(label, widget)
        self._check('display', 'automatic', '自动检测')
        self._text('display', 'profile_path', '显示器 ICC')
        browse = QPushButton('选择 ICC…')
        browse.clicked.connect(self.browse_icc)
        self.pages['display'][1].addRow('', browse)
        self._combo(
            'display',
            'intent',
            '渲染意图',
            [
                ('相对色度', 'relative'),
                ('感知', 'perceptual'),
                ('绝对色度', 'absolute'),
                ('饱和度', 'saturation'),
            ],
        )
        self._check('display', 'bpc', '黑点补偿')
        from ..nl_panel import SettingsDialog as ProviderDialog
        from ..generation_panel import GenerationSettings

        self.ai = ProviderDialog(self, nl_edit.load_settings())
        self.generation = GenerationSettings(self, image_generation.load_settings())
        for child, key in ((self.ai, 'ai'), (self.generation, 'generation')):
            child.setWindowFlags(Qt.WindowType.Widget)
            child.setMinimumWidth(0)
            for box in child.findChildren(QDialogButtonBox):
                box.hide()
            self.pages[key][1].addRow(child)
        # These remain ProviderDialog references so its existing values/save path
        # owns voice persistence. takeRow avoids deleting the live controls.
        provider_form = self.ai.findChild(QFormLayout)
        voice_form = self.pages['voice'][1]
        for widget in (self.ai.voice, self.ai.auto_send):
            row = provider_form.takeRow(widget)
            if row.labelItem is not None and row.labelItem.widget() is not None:
                voice_form.addRow(row.labelItem.widget(), widget)
            else:
                voice_form.addRow(widget)
        self.local_models = QLabel()
        self.local_models.setWordWrap(True)
        self.pages['local_models'][1].addRow('本地模型', self.local_models)
        self.install_models_button = QPushButton('下载本地模型')
        self.install_models_button.clicked.connect(self.install_models)
        self.pages['local_models'][1].addRow('', self.install_models_button)
        self._text('external', 'executable', '可执行程序')
        external_browse = QPushButton('选择程序…')
        external_browse.clicked.connect(self.browse_executable)
        self.pages['external'][1].addRow('', external_browse)
        self._text('external', 'arguments', '参数数组')
        self._combo(
            'external',
            'format',
            '交换格式',
            [
                ('TIFF 8-bit', 'tiff8'),
                ('TIFF 16-bit', 'tiff16'),
                ('PSD 8-bit', 'psd8'),
                ('PSD 16-bit', 'psd16'),
            ],
        )
        self._check('recovery', 'autosave', '自动保存')
        self._spin('recovery', 'delay_ms', '保存延迟', 100, 10000, ' ms')
        self._spin('recovery', 'backups', '数据库备份保留数', 1, 20)
        backup = self.backup_button = QPushButton('备份当前工程')
        backup.clicked.connect(self.backup)
        backup.setEnabled(self.project_getter() is not None)
        self.pages['backup'][1].addRow('', backup)
        self.sessions = QLabel()
        self.sessions.setProperty('user_content', True)
        self.sessions.setWordWrap(True)
        self.pages['recovery'][1].addRow('恢复会话', self.sessions)
        self.recovery_session = QComboBox()
        self.recovery_session.setProperty('user_content', True)
        self.pages['recovery'][1].addRow('', self.recovery_session)
        row = QHBoxLayout()
        self.restore_button = QPushButton('恢复所选会话')
        self.restore_button.clicked.connect(self.restore_session)
        row.addWidget(self.restore_button)
        self.discard_button = QPushButton('丢弃所选会话')
        self.discard_button.clicked.connect(self.discard_session)
        row.addWidget(self.discard_button)
        self.pages['recovery'][1].addRow(row)

    def load(self):
        project = self.project_getter() if self.scope.currentData() == 'project' else None
        values = self.service.read(project=self.service.project_overrides(project))
        for path, widget in self.fields.items():
            section, key = path.split('.')
            value = values[section][key]
            if isinstance(widget, QCheckBox):
                widget.setChecked(value)
            elif isinstance(widget, QComboBox):
                widget.setCurrentIndex(max(0, widget.findData(value)))
            elif isinstance(widget, QSpinBox):
                widget.setValue(value)
            else:
                widget.setText(json.dumps(value) if isinstance(value, list) else value)
            widget.setEnabled(
                (project is None or section in ('import', 'project'))
                and not widget.property('settings_readonly')
            )
        imports = values['import']
        for widget in (
            self.rules,
            self.keywords,
            self.creator,
            self.copyright,
            self.ai,
            self.generation,
            self.ai.voice,
            self.ai.auto_send,
        ):
            widget.setEnabled(
                project is None
                or widget in (self.rules, self.keywords, self.creator, self.copyright)
            )
        self.rules.setPlainText(json.dumps(imports['camera_rules'], ensure_ascii=False))
        self.keywords.setText(', '.join(imports['metadata']['keywords']))
        self.creator.setText(imports['metadata']['creator'])
        self.copyright.setText(imports['metadata']['copyright'])
        provider, device, detail, warning = compute.state.snapshot()
        active = {
            'CPUExecutionProvider': 'CPU',
            'DmlExecutionProvider': 'GPU · DirectML',
            'CUDAExecutionProvider': 'GPU · CUDA',
            'TensorrtExecutionProvider': 'GPU · TensorRT',
        }.get(provider, provider)
        owner = self.owner()
        backend = getattr(owner, 'backend', None)
        if backend is None:
            # Welcome / Agent windows have no photo renderer. Hardware inventory
            # does not initialize OpenCL or construct an ONNX render session.
            mode = self.fields['performance.backend'].currentData()
            backend = getattr(self, '_device_backend', None)
            if backend is None or backend.mode != mode:
                backend = self._device_backend = engine.Backend(mode=mode)
        tooltip = getattr(backend, 'device_tooltip', None)
        device_text = (
            tooltip()
            if callable(tooltip)
            else '\n'.join(value for value in (active, device, detail, warning) if value)
        )
        i18n.set_message(self.device, device_text)
        usage = self.cache.usage()
        from ..photo_agent import models

        memory = 0
        for binding in tuple(_BINDINGS):
            window = binding.window()
            if window is None or not isValid(window):
                continue
            cache = getattr(window, 'render_cache', None)
            if cache is not None:
                memory += cache._stored() + sum(value.nbytes for value in cache._alphas.values())
            memory += getattr(getattr(window, 'tiles', None), 'bytes', 0)
        storage = [
            i18n.translate('内存缓存') + f': {memory / 2**20:.1f} MiB',
            usage['root']
            + '\n'
            + ', '.join(f'{k}: {v / 2**20:.1f} MiB' for k, v in usage['bytes'].items()),
            i18n.translate('模型文件')
            + f': {_directory_bytes(models.model_root()) / 2**20:.1f} MiB · {models.model_root()}',
        ]
        current_project = self.project_getter()
        if current_project is not None and hasattr(current_project, 'root'):
            for folder, label in [('previews', '工程预览'), ('proxies', '独立代理')]:
                path = current_project.root / folder
                storage.append(
                    i18n.translate(label) + f': {_directory_bytes(path) / 2**20:.1f} MiB · {path}'
                )
        self.storage.setProperty('user_content', True)
        self.storage.setText('\n'.join(storage))
        self._refresh_local_models()
        sessions = RecoveryStore().sessions()
        self.sessions.setText(
            '\n'.join(str(s['state'].get('source_path', s['id'])) for s in sessions)
            or i18n.translate('没有可恢复的会话')
        )
        self.recovery_session.clear()
        for session in sessions:
            self.recovery_session.addItem(
                str(session['state'].get('source_path', session['id'])), session['id']
            )
        self.restore_button.setEnabled(bool(sessions))
        self.discard_button.setEnabled(bool(sessions))
        self._update_scope()

    def _refresh_local_models(self):
        from ..photo_agent import models

        rows = []
        purposes = {
            'face_detection_yunet_2023mar.onnx': '人脸检测',
            'face_recognition_sface_2021dec.onnx': '匿名人物聚类',
            'text_model_quantized.onnx': '语义检索',
            'vision_model_quantized.onnx': '场景分析',
            'tokenizer.json': '文本分词',
        }
        for spec in models.MANIFEST['files']:
            path = models.model_root() / spec['name']
            installed = False
            if path.is_file() and path.stat().st_size == spec['bytes']:
                identity = (str(path), path.stat().st_size, path.stat().st_mtime_ns)
                if identity not in self._model_hashes:
                    self._model_hashes[identity] = models.digest(path)
                installed = self._model_hashes[identity] == spec['sha256']
            rows.append(
                f'{spec["name"]} · {spec["bytes"] / 2**20:.1f} MiB · '
                + ('已安装' if installed else '未安装')
                + ' · '
                + purposes.get(spec['name'], '本地模型')
                + ' · CPU'
            )
        i18n.set_message(self.local_models, '\n'.join(rows) + '\n' + str(models.model_root()))

    def values(self):
        result = self.service.read()
        for path, widget in self.fields.items():
            section, key = path.split('.')
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QComboBox):
                value = widget.currentData()
            elif isinstance(widget, QSpinBox):
                value = widget.value()
            else:
                value = widget.text().strip()
                if path == 'external.arguments':
                    value = json.loads(value)
            result[section][key] = value
        result['import']['camera_rules'] = json.loads(self.rules.toPlainText() or '[]')
        result['import']['metadata'] = dict(
            keywords=[k.strip() for k in self.keywords.text().split(',') if k.strip()],
            creator=self.creator.text(),
            copyright=self.copyright.text(),
        )
        return result

    def apply(self):
        try:
            values = self.values()
            if self.scope.currentData() == 'project':
                self.service.update_project(
                    self.project_getter(),
                    {'import': values['import'], 'project': values['project']},
                )
            else:
                # Validate the entire general settings patch before touching credentials.
                from ..settings import _validate

                _validate(values)
                if values['display']['profile_path']:
                    from ..imaging.cms import load_profile

                    load_profile(values['display']['profile_path'], rgb_only=True)
                nl_edit.save_settings(self.ai.values())
                image_generation.save_settings(self.generation.values())
                self.service.update(values)
            self.status.setText(i18n.translate('设置已保存'))
            i18n.localize_window(self, self.service.read()['appearance']['language'])
        except (OSError, ValueError, TypeError) as exc:
            self.status.setText(i18n.translate('设置未保存：') + str(exc))
            return False
        return True

    def reset_page(self):
        key = self.tabs.currentWidget().property('settings_page')
        if self.scope.currentData() == 'project' and key not in PROJECT_PAGES:
            return
        if key in ('maintenance', 'local_models', 'recovery'):
            return
        if key in ('ai', 'voice', 'generation'):
            defaults = nl_edit.default_settings()
            if key == 'ai':
                self.ai.current = None
                self.ai.settings = defaults
                self.ai.provider.setCurrentIndex(self.ai.provider.findData(defaults['provider']))
                self.ai.provider_changed()
                self.ai.timeout.setValue(defaults['timeout'])
                self.ai.attach.setChecked(defaults['attach_image'])
                self.ai.reasoning.setCurrentIndex(self.ai.reasoning.findData(defaults['reasoning']))
            elif key == 'voice':
                self.ai.auto_send.setChecked(defaults['auto_send'])
                self.ai.voice.setCurrentIndex(self.ai.voice.findData(defaults['voice_language']))
            else:
                generation = image_generation.default_settings()
                self.generation.url.setText(generation['base_url'])
                self.generation.model.setText(generation['model'])
                self.generation.key.setText(generation['key'])
                self.generation.timeout.setValue(generation['timeout'])
                self.generation.watermark.setChecked(generation['watermark'])
            self.status.setText(i18n.translate('默认值已恢复；点击应用保存'))
            return
        patch = {}
        for path in self.fields:
            section, name = path.split('.')
            if FIELD_PAGES.get(path, section) == key:
                patch.setdefault(section, {})[name] = copy.deepcopy(DEFAULTS[section][name])
        if key == 'import':
            patch['import'] = copy.deepcopy(DEFAULTS['import'])
        if self.scope.currentData() == 'project':
            self.service.update_project(self.project_getter(), patch)
        else:
            self.service.update(patch)
        self.load()

    def reset_layout(self):
        owner = self.owner()
        binding = getattr(owner, '_settings_binding', None)
        if binding is not None:
            binding.reset_workspace()

    def share(self):
        path, _ = QFileDialog.getSaveFileName(
            self, i18n.translate('分享非秘密设置'), 'CCRaw-settings.json', 'JSON (*.json)'
        )
        if path:
            from ..persistence import atomic_write_json

            atomic_write_json(path, self.service.share(), private=True)

    def clear_cache(self):
        result = self.cache.clear()
        self.status.setText(
            i18n.translate('缓存清理已推迟到任务结束' if result['deferred'] else '缓存已清理')
        )
        for binding in tuple(_BINDINGS):
            binding.cache.pending |= result['deferred']
            if not result['deferred']:
                binding.clear_memory_cache()

    def copy_diagnostics(self):
        from ..logs import redact

        record = {'compute': compute.state.snapshot(), 'windows': []}
        for binding in tuple(_BINDINGS):
            window = binding.window()
            if window is not None:
                work = getattr(window, 'work', None)
                record['windows'].append(
                    {'kind': type(window).__name__, 'state': getattr(work, 'phase', 'idle')}
                )
        QApplication.clipboard().setText(redact(json.dumps(record, ensure_ascii=False)))

    def import_preview(self):
        from ..settings import import_preview
        from .. import model

        owner = self.owner()
        preview = import_preview(
            model.recipe(), getattr(owner, 'info', {}), self.values()['import']
        )
        self.status.setText(
            json.dumps(
                {
                    'preset': preview['preset'],
                    'camera_rule': preview['camera_rule'],
                    'metadata': preview['metadata'],
                },
                ensure_ascii=False,
            )
        )

    def browse_icc(self):
        path, _ = QFileDialog.getOpenFileName(self, 'ICC', '', 'ICC (*.icc *.icm)')
        if path:
            from ..imaging.cms import load_profile

            try:
                load_profile(path, rgb_only=True)
                self.fields['display.profile_path'].setText(path)
            except ValueError as exc:
                self.status.setText(str(exc))

    def browse_executable(self):
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.translate('可执行程序'), '', 'Programs (*.exe);;All (*)'
        )
        if path:
            self.fields['external.executable'].setText(path)

    def backup(self):
        project = self.project_getter()
        if project is None:
            return
        try:
            path = backup_database(
                project.database,
                project.root / 'backups',
                keep=self.service.read()['recovery']['backups'],
            )
            self.status.setText(str(path))
        except (OSError, ValueError) as exc:
            self.status.setText(i18n.translate('保存失败：') + str(exc))

    def install_models(self):
        if self._model_busy:
            self._model_cancel.set()
            self.status.setText(i18n.translate('正在取消模型下载…'))
            return
        from ..photo_agent import models

        self._model_cancel.clear()
        self._model_busy = True
        self.install_models_button.setText(i18n.translate('取消下载'))
        self._model_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='ccraw-model-setup'
        )
        executor = self._model_executor
        self.destroyed.connect(
            lambda _=None, cancel=self._model_cancel: (cancel.set(), executor.shutdown(wait=False))
        )

        def work():
            error = ''
            try:
                models.install_models(self._model_cancel, self.model_progress.emit)
            except Exception as exc:
                from ..logs import redact

                error = redact(str(exc))
            try:
                self.model_finished.emit(error)
            except RuntimeError:
                executor.shutdown(wait=False)

        self._model_future = self._model_executor.submit(work)

    def _model_progress(self, percent, filename):
        self.status.setText(i18n.translate('下载本地模型') + f' · {percent}% · {filename}')

    def _model_finished(self, error):
        self._model_busy = False
        self._model_executor.shutdown(wait=False)
        self.install_models_button.setText(i18n.translate('下载本地模型'))
        self.status.setText(error or i18n.translate('本地模型校验完成'))
        self._refresh_local_models()
        if not error:
            for binding in tuple(_BINDINGS):
                window = binding.window()
                if window is not None and isValid(window) and hasattr(window, 'models'):
                    # Keep in-flight scans on their current model/session snapshot.
                    window._models_refresh_pending = True

    def restore_session(self):
        identifier = self.recovery_session.currentData()
        if not identifier:
            return
        try:
            state = RecoveryStore().load(identifier)
            editor = self.owner()
            if not hasattr(editor, 'open_path'):
                from ..app import MainWindow

                editor = MainWindow()
                owner = self.owner()
                if not hasattr(owner, '_recovery_editors'):
                    owner._recovery_editors = []
                owner._recovery_editors.append(editor)
                editor.show()
            install_settings(editor).restore_recovery(state)
            self.status.setText(i18n.translate('会话已恢复；原片未修改'))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.setText(i18n.translate('恢复失败：') + str(exc))

    def discard_session(self):
        identifier = self.recovery_session.currentData()
        if identifier:
            RecoveryStore().discard(identifier)
            self.load()


def show_settings(window, page='general', *, service=None, project_getter=None):
    service = (
        service
        or getattr(getattr(window, '_settings_binding', None), 'service', None)
        or get_service()
    )
    dialog = _DIALOGS.get(service)
    if dialog is None or not isValid(dialog):
        getter = project_getter or getattr(
            getattr(window, '_settings_binding', None), 'project_getter', None
        )
        dialog = SettingsDialog(window, service, getter)
        _DIALOGS[service] = dialog
    else:
        dialog.owner = weakref.ref(window)
        dialog.project_getter = (
            project_getter
            or getattr(getattr(window, '_settings_binding', None), 'project_getter', None)
            or (lambda: None)
        )
        project = dialog.project_getter()
        index = dialog.scope.findData('project')
        dialog.scope.blockSignals(True)
        if project is not None and index < 0:
            dialog.scope.addItem('工程覆盖', 'project')
        elif project is None and index >= 0:
            dialog.scope.removeItem(index)
        dialog.scope.blockSignals(False)
        dialog.backup_button.setEnabled(project is not None)
        dialog.load()
    requested_page = PAGE_ALIASES.get(page, page)
    if requested_page in dialog.pages and requested_page not in PROJECT_PAGES:
        dialog.scope.setCurrentIndex(dialog.scope.findData('application'))
    dialog.select_page(requested_page)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


class WindowSettingsBinding(QObject):
    def __init__(self, window, service, project_getter=None):
        super().__init__(window)
        self.window, self.service = weakref.ref(window), service
        self.project_getter = project_getter or (lambda: getattr(window, 'project', None))
        for name in ('file_label', 'project_title', 'project_name'):
            label = getattr(window, name, None)
            if isinstance(label, QLabel):
                label.setProperty('user_content', True)
        if self.project_getter() is not None:
            window.setProperty('user_content_title', True)
        self.cache = CacheManager(busy=_busy)
        self.store = RecoveryStore()
        self.session = uuid.uuid4().hex
        self.autosave = AutosaveController(
            self.snapshot, self.write, self, debounce_ms=service.read()['recovery']['delay_ms']
        )
        self.autosave.changed.connect(self.save_status)
        self.autosave.saved.connect(self.saved)
        self.window().destroyed.connect(lambda: self.autosave.shutdown())
        self._initial_recovery = False
        self._last_backend = None
        self._last_display = None
        self._screen_handle = None
        self._pending_preferences = None
        self.workspace = WorkspaceStore(service.path.with_name('workspace.json'))
        self._workspace_ready = False
        self._workspace_default = None
        self._workspace_last = None
        self.workspace_timer = QTimer(self)
        self.workspace_timer.setSingleShot(True)
        self.workspace_timer.setInterval(500)
        self.workspace_timer.timeout.connect(self.save_workspace)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.service.changed.connect(self.apply)
        self.window().installEventFilter(self)
        _BINDINGS.add(self)
        # A newly constructed window inherits the application palette. Startup
        # applies persisted appearance once; subsequent broadcasts update all windows.
        self.apply(service.read(), initial=True)

    def snapshot(self):
        window = self.window()
        if window is None:
            raise OSError('Editor closed before saving')
        return {
            'source_path': getattr(window, 'source_path', ''),
            'project_path': getattr(window, 'project_path', ''),
            'recipe': copy.deepcopy(getattr(window, 'edits', {})),
            'snapshots': copy.deepcopy(getattr(window, 'snapshots', [])),
            'revision': self.autosave.revision,
        }

    @staticmethod
    def _controls(window, control_type, prefix):
        result = {}
        anonymous = 0
        for widget in window.findChildren(control_type):
            if widget.window() is not window:
                continue
            name = widget.objectName()
            if not name:
                name = f'{prefix}-{anonymous}'
                anonymous += 1
            result[name] = widget
        return result

    def workspace_snapshot(self):
        window = self.window()
        if window is None:
            return {}
        rectangle = window.normalGeometry() if window.isMaximized() else window.geometry()
        from PySide6.QtWidgets import QAbstractButton

        state = {
            'geometry': list(rectangle.getRect()),
            'splitters': {
                name: widget.sizes()
                for name, widget in self._controls(window, QSplitter, 'splitter').items()
            },
            'tabs': {
                name: widget.currentIndex()
                for name, widget in self._controls(window, QTabWidget, 'tabs').items()
                if widget.currentIndex() >= 0
            },
            'view': {},
        }
        sidebar = getattr(window, 'sidebar_button', None)
        if isinstance(sidebar, QAbstractButton):
            state['sidebar'] = sidebar.isChecked()
        for name in (
            'sort_combo',
            'sort_descending',
            'view_combo',
            'view_mode',
            'thumbnail_size',
            'auto_advance',
        ):
            widget = getattr(window, name, None)
            if isinstance(widget, QComboBox):
                value = widget.currentData()
                if isinstance(value, (str, bool, int, type(None))):
                    state['view'][name] = value
            elif isinstance(widget, QAbstractButton):
                state['view'][name] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                state['view'][name] = widget.value()
        return state

    def setup_workspace(self):
        window = self.window()
        self._workspace_default = self.workspace_snapshot()
        saved = self.workspace.load(type(window).__name__)
        if saved:
            self.restore_workspace(saved)
        self._workspace_ready = True
        self._workspace_last = self.workspace_snapshot()
        for splitter in self._controls(window, QSplitter, 'splitter').values():
            splitter.splitterMoved.connect(self.workspace_changed)
        for tabs in self._controls(window, QTabWidget, 'tabs').values():
            tabs.currentChanged.connect(self.workspace_changed)
        for name in (
            'sidebar_button',
            'sort_combo',
            'sort_descending',
            'view_combo',
            'view_mode',
            'thumbnail_size',
            'auto_advance',
        ):
            widget = getattr(window, name, None)
            for signal in ('toggled', 'currentIndexChanged', 'valueChanged'):
                changed = getattr(widget, signal, None)
                if changed is not None:
                    changed.connect(self.workspace_changed)
                    break

    def workspace_changed(self, *_):
        if self._workspace_ready:
            self.workspace_timer.start()

    def save_workspace(self):
        window = self.window()
        if window is None or not self._workspace_ready:
            return True
        self.workspace_timer.stop()
        state = self.workspace_snapshot()
        if state == self._workspace_last:
            return True
        try:
            self.workspace.save(type(window).__name__, state)
            self._workspace_last = state
            return True
        except (OSError, ValueError) as exc:
            self.service.error = str(exc)
            window.statusBar().showMessage(i18n.translate('布局未保存：') + str(exc))
            return False

    def restore_workspace(self, state):
        window = self.window()
        if window is None:
            return
        state = WorkspaceStore.validate(state)
        self._workspace_ready = False
        if 'geometry' in state:
            window.setGeometry(*state['geometry'])
        clamp_window(window)
        for name, widget in self._controls(window, QSplitter, 'splitter').items():
            sizes = state.get('splitters', {}).get(name)
            if sizes is not None and len(sizes) == widget.count() and sum(sizes) > 0:
                total = max(1, sum(sizes))
                extent = (
                    widget.width()
                    if widget.orientation() == Qt.Orientation.Horizontal
                    else widget.height()
                )
                extent = max(1, extent)
                widget.setSizes([max(0, int(size * extent / total)) for size in sizes])
        for name, widget in self._controls(window, QTabWidget, 'tabs').items():
            index = state.get('tabs', {}).get(name)
            if index is not None and widget.count():
                widget.setCurrentIndex(min(index, widget.count() - 1))
        sidebar = getattr(window, 'sidebar_button', None)
        if sidebar is not None and 'sidebar' in state:
            sidebar.setChecked(state['sidebar'])
        for name, value in state.get('view', {}).items():
            widget = getattr(window, name, None)
            if isinstance(widget, QComboBox):
                index = widget.findData(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QSpinBox) and isinstance(value, int):
                widget.setValue(value)
        self._workspace_ready = True

    def reset_workspace(self):
        window = self.window()
        if window is None or self._workspace_default is None:
            return
        self.workspace.discard(type(window).__name__)
        self.restore_workspace(self._workspace_default)
        self._workspace_last = None
        self.save_workspace()

    def write(self, payload):
        window = self.window()
        if not payload['source_path']:
            return
        # Recipe-only crash recovery is always separate from proxy caches.
        self.store.save(self.session, payload)
        if payload['project_path']:
            from .. import model, branding

            target = Path(payload['project_path']).resolve()
            if (
                target == Path(payload['source_path']).resolve()
                or target.suffix.lower() not in branding.PROJECT_SUFFIXES
            ):
                raise ValueError(
                    'Autosave target must be a recipe project, separate from the original'
                )
            model.save_project(
                payload['project_path'],
                payload['source_path'],
                payload['recipe'],
                payload['snapshots'],
            )
        callback = getattr(window, 'settings_save_document', None) if window is not None else None
        if callback is not None:
            callback(payload)

    def note_edit(self):
        window = self.window()
        if (
            window is not None
            and getattr(window, 'source', None) is not None
            and self.service.read()['recovery']['autosave']
        ):
            self.autosave.mark_dirty()

    def flush(self):
        return self.autosave.flush()

    def restore_recovery(self, state):
        from .. import model

        if not isinstance(state, dict) or not isinstance(state.get('source_path'), str):
            raise ValueError('Recovery source path is missing')
        source = Path(state['source_path']).resolve()
        if not source.is_file():
            raise ValueError('Recovery source file is missing')
        project_path = state.get('project_path', '')
        if not isinstance(project_path, str):
            raise ValueError('Recovery project path is invalid')
        if project_path:
            from .. import branding

            target = Path(project_path).resolve()
            if target == source or target.suffix.lower() not in branding.PROJECT_SUFFIXES:
                raise ValueError('Recovery target must be a separate recipe project')
        try:
            recipe = model.validate(state.get('recipe'))
            snapshots = model.validate_snapshots(state.get('snapshots', []))
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError('Recovery recipe is invalid') from exc
        window = self.window()
        if window is None or not hasattr(window, 'add_documents') or not self.flush():
            raise OSError('Editor is unavailable or previous edits could not be saved')
        work = getattr(window, 'work', None)
        if work is not None:
            from ..scheduler import Activity

            if not work.can_start(Activity.LOADING):
                raise OSError('Wait for the current image task before recovery')
        window.add_documents([str(source)])
        window.documents[str(source)].update(
            edits=copy.deepcopy(recipe),
            saved_edits=copy.deepcopy(recipe),
            snapshots=copy.deepcopy(snapshots),
            saved_snapshots=copy.deepcopy(snapshots),
            initialized=True,
            project=project_path,
        )
        window.open_path(str(source))

    def saved(self, payload):
        window = self.window()
        if (
            window is not None
            and payload['project_path']
            and window.source_path == payload['source_path']
        ):
            window.saved_edits = copy.deepcopy(payload['recipe'])
            window.saved_snapshots = copy.deepcopy(payload['snapshots'])

    def save_status(self, status, error):
        window = self.window()
        if window is None:
            return
        text = {
            'saved': '已保存',
            'saving': '保存中…',
            'pending': '等待保存',
            'failed': '保存失败：',
        }[status]
        text = i18n.translate(text) + error
        window.setProperty('saveStatus', status)
        if hasattr(window, 'state_label'):
            i18n.set_message(
                window.state_label,
                {
                    'saved': '已保存',
                    'saving': '保存中…',
                    'pending': '等待保存',
                    'failed': '保存失败：',
                }[status]
                + error,
            )
        if status == 'failed':
            window.statusBar().showMessage(text)

    def clear_memory_cache(self):
        window = self.window()
        if window is not None and not _busy():
            cache = getattr(window, 'render_cache', None)
            if cache is not None:
                cache.clear()
            decoded = getattr(window, 'decoded_previews', None)
            if decoded is not None:
                decoded.clear()
            tiles = getattr(window, 'tiles', None)
            if tiles is not None:
                tiles.clear()

    def apply(self, values, *, initial=False):
        window = self.window()
        if window is None:
            return
        self._pending_preferences = None
        if not initial:
            theme.apply_theme(QApplication.instance(), values['appearance']['theme'])
        i18n.set_language(values['appearance']['language'])
        self.autosave.timer.setInterval(values['recovery']['delay_ms'])
        performance = values['performance']
        budget = performance['render_memory_mib'] or 512
        if hasattr(window, 'render_cache'):
            window.render_cache.max_bytes = budget * 2**20
        if hasattr(window, 'decoded_previews'):
            window.decoded_previews.max_bytes = min(96, max(0, budget // 4)) * 2**20
            # Atomic budget/clear operations do not hold the cache lock during decoding.
            window.decoded_previews.clear()
        self.cache.clear(budget_bytes=performance['thumbnail_disk_mib'] * 2**20)
        window.setProperty('previewLimit', performance['preview_limit'] or 2048)
        if performance['backend'] != self._last_backend:
            if _busy():
                self._pending_preferences = values
            else:
                os.environ['CCRAW_COMPUTE'] = performance['backend']
                if hasattr(window, 'backend'):
                    window.backend = engine.Backend(mode=performance['backend'])
                self._last_backend = performance['backend']
        if hasattr(window, 'nl_settings'):
            window.nl_settings = nl_edit.load_settings()
            if hasattr(window, 'nl_update_service'):
                window.nl_update_service()
        generation = getattr(window, 'generation_panel', None)
        if generation is not None:
            generation.settings = image_generation.load_settings()
            generation.update_service()
        if hasattr(window, 'refresh_provider'):
            window.refresh_provider()
        window.setProperty('displayProfilePath', values['display']['profile_path'])
        window.setProperty('displayAutomaticICC', values['display']['automatic'])
        window.setProperty('displayIntent', values['display']['intent'])
        window.setProperty('displayBPC', values['display']['bpc'])
        if values['display'] != self._last_display:
            self._last_display = copy.deepcopy(values['display'])
            self.refresh_display()

    def refresh_display(self, *_):
        window = self.window()
        if window is None or getattr(window, 'source', None) is None:
            return
        if hasattr(window, 'tiles'):
            window.tiles.clear()
        if hasattr(window, 'update_display') and getattr(window, 'rendered', None) is not None:
            window.update_display()
        if hasattr(window, 'changed'):
            window.changed()

    def attach_screen(self):
        window = self.window()
        if window is None:
            return
        handle = window.windowHandle()
        if handle is not None and handle is not self._screen_handle:
            self._screen_handle = handle
            handle.screenChanged.connect(self.refresh_display)
        if self._workspace_default is not None:
            for name, widget in self._controls(window, QSplitter, 'splitter').items():
                initial = self._workspace_default['splitters'].get(name, [])
                if not sum(initial) and sum(widget.sizes()):
                    self._workspace_default['splitters'][name] = widget.sizes()

    def poll(self):
        window = self.window()
        if window is None:
            return
        i18n.localize_window(window, self.service.read()['appearance']['language'])
        theme.refresh_system_theme(QApplication.instance())
        result = self.cache.drain()
        if result:
            self.clear_memory_cache()
        if self._pending_preferences is not None and not _busy():
            pending, self._pending_preferences = self._pending_preferences, None
            self.apply(pending)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Close and not self.flush():
            event.ignore()
            self.save_status('failed', self.autosave.error)
            return True
        if event.type() == QEvent.Type.Close:
            self.save_workspace()
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self.workspace_changed()
        if event.type() == QEvent.Type.ScreenChangeInternal:
            QTimer.singleShot(0, lambda: clamp_window(watched))
        if event.type() in (QEvent.Type.Show, QEvent.Type.ScreenChangeInternal):
            QTimer.singleShot(0, self.attach_screen)
        return False


def install_settings(window, project_getter=None, *, service=None):
    """Call once after building any Launcher/Agent/editor window; safe to call repeatedly.

    Editors call binding.note_edit() after accepted edits and binding.flush() before
    switching documents. Optional window.settings_save_document(payload) persists
    the shared document repository; it runs on the serial autosave worker.
    """
    if hasattr(window, '_settings_binding'):
        binding = window._settings_binding
        if project_getter is not None:
            binding.project_getter = project_getter
        return binding
    binding = WindowSettingsBinding(window, service or get_service(), project_getter)
    window._settings_binding = binding
    action = QAction('设置', window)
    action.setShortcut(QKeySequence('Ctrl+,'))
    action.triggered.connect(lambda: show_settings(window))
    window.addAction(action)
    window.settings_action = action
    button = QPushButton('设置')
    button.setAccessibleName('设置')
    button.clicked.connect(lambda: show_settings(window))
    window.settings_button = button
    top = window.findChild(QWidget, 'topbar')
    if top is not None and top.layout() is not None:
        top.layout().addWidget(button)
        wrap_toolbar(top)
    elif hasattr(window, 'sidebar') and window.sidebar.layout() is not None:
        window.sidebar.layout().addWidget(button)
    else:
        # Agent's action row is a layout directly inside its central widget.
        central = window.centralWidget()
        outer = central.layout() if central is not None else None
        row = outer.itemAt(0).layout() if outer and outer.count() else None
        if isinstance(row, QHBoxLayout):
            row.addWidget(button)
            holder = QWidget()
            item = outer.takeAt(0)
            holder.setLayout(item.layout())
            wrap_toolbar(holder)
            outer.insertWidget(0, holder)
        else:
            window.menuBar().addAction(action)
            button.deleteLater()
    for splitter in window.findChildren(QSplitter):
        if splitter.orientation() == Qt.Orientation.Horizontal:
            splitter.setChildrenCollapsible(True)
    clamp_window(window)
    binding.setup_workspace()
    i18n.localize_window(window, binding.service.read()['appearance']['language'])
    return binding
