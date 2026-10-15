"""Visual semi-utils template settings with a coalesced, live export preview."""

import base64
import copy
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer, QSize, QEvent
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QWidget,
    QLabel,
    QCheckBox,
    QComboBox,
    QPushButton,
    QFileDialog,
    QDialogButtonBox,
    QScrollArea,
    QGroupBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QSizePolicy,
    QToolButton,
)
from . import watermark, engine, photo_metadata
from .widgets import qimage
from .watermark import BRANDS
from .watermark_graph import PipelineEditor
from .ui.disclosure import set_disclosure


METADATA_KEYS = (
    'CameraMake',
    'CameraModelName',
    'LensMake',
    'LensModel',
    'Artist',
    'Copyright',
    'DateTimeOriginal',
    'Date',
    'Filename',
    'Folder',
    'FocalLength',
    'FocalLengthIn35mmFormat',
    'FNumber',
    'ExposureTime',
    'ISO',
    'ImageWidth',
    'ImageHeight',
    'GeoInfo',
    'Custom',
    'body',
    'lens',
    'brand',
    'lens_brand',
    'date',
    'aperture',
    'shutter',
    'iso',
    'focal',
    'focal_equivalent',
    'filename',
    'folder',
)


class SettingsSection(QWidget):
    """Use the application's disclosure affordance for optional detailed settings."""

    def __init__(self, title, content, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        self.button = QToolButton()
        self.button.setText(title)
        self.button.setCheckable(True)
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setMinimumHeight(32)
        self.button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        set_disclosure(self.button, False)
        root.addWidget(self.button)
        self.content = content
        content.hide()
        root.addWidget(content)
        self.button.toggled.connect(self.toggle)

    def toggle(self, expanded):
        self.content.setVisible(expanded)
        set_disclosure(self.button, expanded)


class MetadataEditor(QWidget):
    changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._original = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(['参数名称', '覆盖值'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumWidth(0)
        self.table.setFixedHeight(150)
        self.table.cellChanged.connect(self._emit)
        root.addWidget(self.table)
        row = QHBoxLayout()
        self.key = QComboBox()
        self.key.setEditable(True)
        self.key.addItems(METADATA_KEYS)
        self.key.setMinimumWidth(0)
        self.key.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        add = QPushButton('添加')
        remove = QPushButton('删除')
        add.setFixedWidth(52)
        remove.setFixedWidth(52)
        add.clicked.connect(self.add)
        remove.clicked.connect(self.remove)
        row.addWidget(self.key, 1)
        row.addWidget(add)
        row.addWidget(remove)
        root.addLayout(row)

    def set_values(self, values):
        self._loading = True
        self._original = copy.deepcopy(values)
        self.table.setRowCount(len(values))
        for i, (key, value) in enumerate(values.items()):
            self.table.setItem(i, 0, QTableWidgetItem(str(key)))
            self.table.setItem(i, 1, QTableWidgetItem(str(value)))
        self._loading = False

    def values(self):
        values = {}
        for i in range(self.table.rowCount()):
            key_item, value_item = self.table.item(i, 0), self.table.item(i, 1)
            key = key_item.text().strip() if key_item else ''
            if not key:
                continue
            value = value_item.text() if value_item else ''
            original = self._original.get(key)
            if isinstance(original, (int, float)):
                try:
                    value = float(value)
                    if isinstance(original, int) and value.is_integer():
                        value = int(value)
                except ValueError:
                    pass
            values[key] = value
        return values

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit(self.values())

    def add(self):
        key = self.key.currentText().strip()
        if not key:
            return
        values = self.values()
        if key not in values:
            values[key] = ''
            self.set_values(values)
            self.table.setCurrentCell(self.table.rowCount() - 1, 1)
            self._emit()

    def remove(self):
        row = self.table.currentRow()
        if row >= 0:
            self._loading = True
            self.table.removeRow(row)
            self._loading = False
            self._emit()


class WatermarkEditor(QWidget):
    changed = Signal(dict)

    def __init__(self, owner, compact=False):
        super().__init__(owner)
        self.owner = owner
        self.compact = compact
        self.refreshing = True
        self._preview_running = False
        self._preview_pending = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._render_preview)
        self.settings = copy.deepcopy(owner.edits['watermark'])
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        self.enabled = QCheckBox('导出时启用水印')
        root.addWidget(self.enabled)
        layoutrow = QHBoxLayout()
        layoutrow.addWidget(QLabel('Layout.Type'))
        self.layout_type = QComboBox()
        self.layout_type.setMinimumWidth(140)
        self.layout_type.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for key, label in watermark.LAYOUT_TYPES.items():
            self.layout_type.addItem(label, key)
        layoutrow.addWidget(self.layout_type, 1)
        root.addLayout(layoutrow)
        columns = QVBoxLayout() if compact else QHBoxLayout()
        root.addLayout(columns, 1)
        self.preview = QLabel('水印预览')
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.preview.setMinimumSize(160 if compact else 240, 150 if compact else 200)
        if not compact:
            columns.addWidget(self.preview, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumWidth(0)
        self.form_box = QWidget()
        self.form_box.setMinimumWidth(0)
        self.form_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        form = QVBoxLayout(self.form_box)
        form.setContentsMargins(6, 6, 6, 6)
        form.setSpacing(8)
        self.scroll.setWidget(self.form_box)
        columns.addWidget(self.scroll, 2 if compact else 1)
        identity = QGroupBox('品牌、标志与拍摄参数')
        identityform = QFormLayout(identity)
        identityform.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        identityform.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.brand = QComboBox()
        self.brand.addItem('自动识别相机品牌', 'auto')
        for key, label in BRANDS.items():
            self.brand.addItem(label, key)
        identityform.addRow('相机品牌', self.brand)
        self.equivalent_focal = QCheckBox('使用 35mm 等效焦距')
        identityform.addRow(self.equivalent_focal)
        self.logo_labels = {}
        self.logo_clear_buttons = {}
        for key, title in [('left', '左标志'), ('right', '右标志'), ('center', '居中标志')]:
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            label = QLabel('模板 / 自动')
            label.setWordWrap(True)
            self.logo_labels[key] = label
            add = QPushButton('导入 PNG')
            clear = QPushButton('清除')
            self.logo_clear_buttons[key] = clear
            add.clicked.connect(lambda checked=False, key=key: self.choose_logo(key))
            clear.clicked.connect(lambda checked=False, key=key: self.clear_logo(key))
            row.addWidget(label, 1)
            row.addWidget(add)
            row.addWidget(clear)
            identityform.addRow(title, box)
        self.identity_section = SettingsSection('品牌、标志与拍摄参数', identity)
        form.addWidget(self.identity_section)
        metadata = QGroupBox('拍摄参数覆盖')
        metaroot = QVBoxLayout(metadata)
        self.metadata = MetadataEditor()
        metaroot.addWidget(self.metadata)
        metahint = QLabel(
            '自动读取照片参数；只添加需要替换的值。可编辑任意 EXIF 名称，文字模板可引用这些参数。'
        )
        metahint.setWordWrap(True)
        metaroot.addWidget(metahint)
        self.metadata_section = SettingsSection('拍摄参数覆盖', metadata)
        form.addWidget(self.metadata_section)
        self.pipeline_editor = PipelineEditor(compact=compact)
        form.addWidget(self.pipeline_editor)
        reset = QPushButton('恢复当前版式的默认配置')
        reset.clicked.connect(self.reset_template)
        form.addWidget(reset)
        form.addStretch()
        if compact:
            self.preview.setFixedHeight(150)
            columns.addWidget(self.preview, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.enabled.toggled.connect(self.update_preview)
        self.layout_type.currentIndexChanged.connect(self._template_changed)
        self.brand.currentIndexChanged.connect(self.update_preview)
        self.equivalent_focal.toggled.connect(self.update_preview)
        self.metadata.changed.connect(self.update_preview)
        self.pipeline_editor.changed.connect(self.update_preview)
        self.set_settings(self.settings)

    def values(self):
        return dict(
            enabled=self.enabled.isChecked(),
            template=self.layout_type.currentData(),
            pipeline=self.pipeline_editor.values(),
            brand=self.brand.currentData(),
            logos=copy.deepcopy(self.settings.get('logos', {})),
            metadata=self.metadata.values(),
            equivalent_focal=self.equivalent_focal.isChecked(),
        )

    def _template_changed(self, *_):
        if self.refreshing:
            return
        self.pipeline_editor.set_pipeline(
            watermark.template_pipeline(self.layout_type.currentData())
        )
        self.update_preview()

    def reset_template(self):
        self.pipeline_editor.set_pipeline(
            watermark.template_pipeline(self.layout_type.currentData())
        )
        self.update_preview()

    def set_settings(self, settings):
        self.refreshing = True
        settings = watermark.validate(settings)
        self.settings = copy.deepcopy(settings)
        self.enabled.setChecked(settings['enabled'])
        self.layout_type.setCurrentIndex(self.layout_type.findData(settings['template']))
        self.brand.setCurrentIndex(self.brand.findData(settings.get('brand', 'auto')))
        self.equivalent_focal.setChecked(settings.get('equivalent_focal', True))
        self.metadata.set_values(settings.get('metadata', {}))
        self.pipeline_editor.set_pipeline(
            settings['pipeline'] or watermark.template_pipeline(settings['template'])
        )
        self.form_box.setEnabled(self.owner.source is not None)
        for key, label in self.logo_labels.items():
            imported = bool(settings.get('logos', {}).get(key))
            label.setText('已导入 PNG' if imported else '模板 / 自动')
            self.logo_clear_buttons[key].setEnabled(imported)
        self.refreshing = False
        self.update_preview()

    def update_preview(self, *_):
        if self.refreshing:
            return
        settings = self.values()
        if settings != self.settings:
            self.settings = copy.deepcopy(settings)
            self.changed.emit(copy.deepcopy(settings))
        if self._preview_running:
            self._preview_pending = True
        elif (
            self.owner.rendered is not None
            and self.isVisible()
            and not self._preview_timer.isActive()
        ):
            self._preview_timer.start(40)
        if self.owner.rendered is None:
            self.preview.setText('打开照片后显示水印预览')

    def _render_preview(self):
        if self._preview_running:
            self._preview_pending = True
            return
        if self.owner.rendered is None or self.owner.closing:
            return
        self._preview_running = True
        self._preview_pending = False
        rgb = self.owner.rendered
        edits = copy.deepcopy(self.owner.edits)
        settings = copy.deepcopy(self.values())
        photo = copy.deepcopy(self.owner.info.get('photo', {}))
        path = getattr(self.owner, 'source_path', None)
        if path:
            photo = photo_metadata.watermark_context(photo, path)
        document = self.owner.document_token
        width = self.owner.info.get('width', rgb.shape[1])
        height = self.owner.info.get('height', rgb.shape[0])
        dpr = self.preview.devicePixelRatioF()
        preview_pixels = QSize(
            max(1, round(self.preview.width() * dpr)),
            max(1, round(self.preview.height() * dpr)),
        )
        preview_limit = min(2500, max(preview_pixels.width(), preview_pixels.height()))

        def current():
            return (
                not self.owner.closing
                and document == self.owner.document_token
                and settings == self.values()
            )

        def complete():
            self._preview_running = False
            if self._preview_pending and not self.owner.closing:
                self._preview_timer.start(0)

        def prepare():
            x0, y0, x1, y1 = engine.crop_bounds(edits, width, height)
            final_shape = (y1 - y0, x1 - x0, 3)
            if edits['rotation'] % 2:
                final_shape = (final_shape[1], final_shape[0], 3)
            image = engine.resize_limit(engine.crop_rotate(rgb, edits), preview_limit)
            return qimage(
                watermark.preview(
                    image, settings, photo, limit=preview_limit, final_shape=final_shape
                )
            )

        def done(image):
            complete()
            if not current():
                return
            pix = QPixmap.fromImage(image)
            pix = pix.scaled(
                preview_pixels,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pix.setDevicePixelRatio(dpr)
            self.preview.setPixmap(pix)
            self.status.setText('预览使用最终裁剪、旋转后的画面与导出尺寸计算水印。')

        def failed(message):
            complete()
            if current():
                self.status.setText(str(message))

        try:
            self.owner.job(prepare, done, failed)
        except Exception as exc:
            failed(exc)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_preview_timer'):
            self.update_preview()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, '_preview_timer'):
            self.update_preview()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange and hasattr(self, '_preview_timer'):
            self.update_preview()
        return result

    def choose_logo(self, key):
        path, _ = QFileDialog.getOpenFileName(self, '选择标志', '', 'PNG 标志 (*.png)')
        if not path:
            return
        try:
            file = Path(path)
            if file.stat().st_size > 2_000_000:
                raise ValueError('标志请使用小于 2MB 的 PNG。')
            data = base64.b64encode(file.read_bytes()).decode('ascii')
            watermark.logo(data)
            self.settings.setdefault('logos', {})[key] = data
            self.logo_labels[key].setText('已导入 PNG')
            self.logo_clear_buttons[key].setEnabled(True)
            self.changed.emit(copy.deepcopy(self.values()))
            self.update_preview()
        except Exception as exc:
            self.status.setText(str(exc))

    def clear_logo(self, key):
        self.settings.setdefault('logos', {})[key] = ''
        self.logo_labels[key].setText('模板 / 自动')
        self.logo_clear_buttons[key].setEnabled(False)
        self.changed.emit(copy.deepcopy(self.values()))
        self.update_preview()


class WatermarkDialog(QDialog):
    """A large preview of the same settings editor shown in the Watermark tab."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle('水印与边框')
        self.resize(1000, 760)
        root = QVBoxLayout(self)
        self.editor = WatermarkEditor(owner)
        self.form_box = self.editor.form_box
        root.addWidget(self.editor, 1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('应用')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(owner.source is not None)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.settings = copy.deepcopy(owner.edits['watermark'])

    def accept(self):
        if self.owner.source is None:
            return
        try:
            self.settings = watermark.validate(self.editor.values())
        except ValueError as exc:
            self.editor.status.setText(str(exc))
            return
        super().accept()
