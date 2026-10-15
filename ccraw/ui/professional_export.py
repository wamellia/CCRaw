"""Professional export settings with honest encoder depth and output profiles."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Signal, QStandardPaths
from PySide6.QtWidgets import (
    QWidget,
    QFormLayout,
    QComboBox,
    QSpinBox,
    QLineEdit,
    QCheckBox,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QTabWidget,
    QLabel,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QScrollArea,
    QGroupBox,
)

from .. import export_presets as ep
from ..enhance_dialog import ExportDialog


class ExportSettings(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.color_space = QComboBox()
        self.color_space.addItems(list(ep.PROFILE_NAMES.values()))
        self.bit_depth = QComboBox()
        self.bit_depth.addItems(['8', '16'])
        self.intent = QComboBox()
        for label, key in [
            ('相对色度', 'relative'),
            ('感知', 'perceptual'),
            ('绝对色度', 'absolute'),
            ('饱和度', 'saturation'),
        ]:
            self.intent.addItem(label, key)
        self.long_edge = QSpinBox()
        self.long_edge.setRange(0, 30000)
        self.long_edge.setSpecialValueText('原始尺寸')
        self.resolution = QSpinBox()
        self.resolution.setRange(1, 2400)
        self.resolution.setValue(300)
        self.resolution.setSuffix(' ppi')
        self.output_sharpening = QComboBox()
        for label, key in [
            ('无', 'none'),
            ('屏幕', 'screen'),
            ('哑光纸', 'matte'),
            ('光面纸', 'glossy'),
        ]:
            self.output_sharpening.addItem(label, key)
        self.sharpening_amount = QComboBox()
        for label, key in [('低', 'low'), ('标准', 'standard'), ('高', 'high')]:
            self.sharpening_amount.addItem(label, key)
        self.sharpening_amount.setCurrentIndex(1)
        self.naming = QLineEdit('{stem}-CCRaw')
        self.naming.setToolTip('支持 {stem}、{date}、{seq:03d}；模板只能生成文件名。')
        self.metadata_policy = QComboBox()
        for label, key in [
            ('全部', 'all'),
            ('去除相机信息', 'all_except_camera'),
            ('仅版权与作者', 'copyright'),
            ('不附带', 'none'),
        ]:
            self.metadata_policy.addItem(label, key)
        self.remove_location = QCheckBox('移除 GPS 与地点')
        for title, rows in (
            (
                '颜色与精度',
                (
                    ('输出色域', self.color_space),
                    ('每通道位深', self.bit_depth),
                    ('渲染意图', self.intent),
                ),
            ),
            ('尺寸与分辨率', (('输出长边', self.long_edge), ('分辨率', self.resolution))),
            (
                '细节与命名',
                (
                    ('输出锐化', self.output_sharpening),
                    ('锐化强度', self.sharpening_amount),
                    ('文件名模板', self.naming),
                ),
            ),
            ('元数据策略', (('保留信息', self.metadata_policy), ('', self.remove_location))),
        ):
            group = QGroupBox(title)
            group.setProperty('settings_group', True)
            form = QFormLayout(group)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            for label, widget in rows:
                form.addRow(label, widget)
            layout.addWidget(group)
        self.output_sharpening.currentIndexChanged.connect(self._sharpening_changed)
        self._sharpening_changed()
        self.set_format('jpeg')

    def _sharpening_changed(self, *_):
        self.sharpening_amount.setEnabled(self.output_sharpening.currentData() != 'none')

    def set_format(self, fmt):
        self.output_format = fmt
        previous = self.bit_depth.currentText()
        self.bit_depth.blockSignals(True)
        self.bit_depth.clear()
        self.bit_depth.addItems([str(n) for n in ep.FORMATS.get(fmt, (16,))])
        default = '16' if fmt in ('tiff', 'psd', 'dng') else '8'
        self.bit_depth.setCurrentText(
            previous
            if previous in [str(n) for n in ep.FORMATS.get(fmt, (16,))] and fmt != 'tiff'
            else default
        )
        self.bit_depth.blockSignals(False)
        self.bit_depth.setEnabled(fmt != 'jpeg' and fmt != 'dng')
        self.color_space.setEnabled(fmt != 'dng')
        self.intent.setEnabled(fmt != 'dng')

    def options(self):
        if self.output_format == 'dng':
            return None
        return ep.validate(
            dict(
                format=self.output_format,
                bit_depth=int(self.bit_depth.currentText()),
                color_space=self.color_space.currentText(),
                intent=self.intent.currentData(),
                long_edge=self.long_edge.value(),
                resolution=self.resolution.value(),
                output_sharpening=self.output_sharpening.currentData(),
                sharpening_amount=self.sharpening_amount.currentData(),
                file_name_template=self.naming.text(),
                metadata_policy=self.metadata_policy.currentData(),
                remove_location=self.remove_location.isChecked(),
            )
        )

    def set_options(self, options):
        opts = ep.validate(options)
        self.set_format(opts['format'])
        self.color_space.setCurrentText(ep.PROFILE_NAMES[opts['color_space']])
        self.bit_depth.setCurrentText(str(opts['bit_depth']))
        for key in ('intent', 'output_sharpening', 'sharpening_amount', 'metadata_policy'):
            box = getattr(self, key)
            box.setCurrentIndex(box.findData(opts[key]))
        self.long_edge.setValue(opts['long_edge'])
        self.resolution.setValue(round(opts['resolution']))
        self.naming.setText(opts['file_name_template'])
        self.remove_location.setChecked(opts['remove_location'])


class ProfessionalExportDialog(ExportDialog):
    proofRequested = Signal(dict)

    def __init__(self, parent, source, enhance=False, *, preset_path=None):
        super().__init__(parent, source, enhance)
        self.setWindowTitle('专业导出与交换')
        self.format.addItem('PSD · 合并 RGB · 8/16-bit')
        for index, title in enumerate(('JPEG', 'PNG', 'TIFF', 'DNG · 线性成片', 'PSD · 合并 RGB')):
            self.format.setItemText(index, title)
        self.preset_path = (
            Path(preset_path)
            if preset_path
            else Path(
                QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
            )
            / 'export-presets.json'
        )
        try:
            self.presets = ep.load(self.preset_path)
        except ValueError:
            self.presets = {}
        self.preset_combo = QComboBox()
        self.preset_combo.addItem('自定义设置')
        self.preset_combo.addItems(list(self.presets))
        save = QPushButton('保存为预设…')
        save.clicked.connect(self._save_prompt)
        row = QHBoxLayout()
        row.addWidget(self.preset_combo, 1)
        row.addWidget(save)
        self.layout().insertLayout(1, row)
        tabs = QTabWidget()
        self.settings = ExportSettings(self)
        tabs.addTab(self.settings, '输出')
        for key in (
            'color_space',
            'bit_depth',
            'intent',
            'long_edge',
            'resolution',
            'output_sharpening',
            'sharpening_amount',
            'naming',
            'metadata_policy',
            'remove_location',
        ):
            setattr(self, key, getattr(self.settings, key))
        metadata = QWidget()
        form = QFormLayout(metadata)
        self.metadata_fields = {}
        for key, label in [
            ('title', '标题'),
            ('caption', '说明'),
            ('creator', '作者'),
            ('copyright', '版权'),
            ('location', '地点'),
            ('keywords', '关键词（逗号分隔）'),
            ('label', '颜色标签'),
        ]:
            entry = QLineEdit()
            form.addRow(label, entry)
            self.metadata_fields[key] = entry
        self.rating = QSpinBox()
        self.rating.setRange(0, 5)
        form.addRow('评级', self.rating)
        note = QLabel('XMP：通用元数据可交换；CRS 参数为近似；CCRaw 配方为专有编辑。')
        note.setWordWrap(True)
        form.addRow(note)
        read = QPushButton('读取 XMP sidecar…')
        read.setEnabled(parent is not None and hasattr(parent, 'import_xmp'))
        read.clicked.connect(lambda: parent.import_xmp() if parent is not None else None)
        form.addRow(read)
        tabs.addTab(metadata, '元数据')
        external = QWidget()
        form = QFormLayout(external)
        self.action = QComboBox()
        for text, key in [
            ('导出成片', 'export'),
            ('交给外部编辑器（TIFF / PSD）', 'external'),
            ('写入原片的 XMP sidecar', 'xmp'),
        ]:
            self.action.addItem(text, key)
        self.executable = QLineEdit()
        browse = QPushButton('选择程序…')
        browse.clicked.connect(self._choose_program)
        row = QHBoxLayout()
        row.addWidget(self.executable, 1)
        row.addWidget(browse)
        self.arguments = QLineEdit('["{file}"]')
        self.arguments.setToolTip('JSON 参数数组，例如 ["--edit", "{file}"]。')
        self.editor_path = self.preset_path.with_name('external-editor.json')
        from ..settings import get_service

        stored = get_service().read()['external']
        self.executable.setText(stored['executable'])
        self.arguments.setText(json.dumps(stored['arguments'], ensure_ascii=False))
        self.external_format = stored['format']
        if (
            not stored['executable']
            and self.editor_path.exists()
            and self.editor_path.stat().st_size <= 16384
        ):
            try:
                stored = json.loads(self.editor_path.read_text(encoding='utf-8'))
                if isinstance(stored, dict) and isinstance(stored.get('executable'), str):
                    self.executable.setText(stored['executable'])
                    self.arguments.setText(
                        json.dumps(stored.get('arguments', ['{file}']), ensure_ascii=False)
                    )
            except (ValueError, OSError):
                pass
        form.addRow('操作', self.action)
        form.addRow('编辑器程序', row)
        form.addRow('程序参数', self.arguments)
        tabs.addTab(external, '交换')
        proof = QWidget()
        form = QFormLayout(proof)
        self.proof_enabled = QCheckBox('启用显示软打样')
        self.proof_path = QLineEdit()
        browse = QPushButton('选择目标 ICC…')
        browse.clicked.connect(self._choose_proof)
        row = QHBoxLayout()
        row.addWidget(self.proof_path, 1)
        row.addWidget(browse)
        self.proof_paper = QCheckBox('模拟纸白与墨黑')
        self.proof_gamut = QCheckBox('显示超出目标色域')
        self.proof_bpc = QCheckBox('黑点补偿')
        self.proof_bpc.setChecked(True)
        apply = QPushButton('应用显示打样')
        apply.clicked.connect(self._apply_proof)
        form.addRow(self.proof_enabled)
        form.addRow('打样配置', row)
        form.addRow(self.proof_paper)
        form.addRow(self.proof_gamut)
        form.addRow(self.proof_bpc)
        form.addRow(apply)
        note = QLabel('打样只改变显示，不写入照片配方，也不改变正常导出。')
        note.setWordWrap(True)
        form.addRow(note)
        tabs.addTab(proof, '软打样')
        self.layout().insertWidget(3, tabs)
        self.format.currentIndexChanged.connect(self._format_changed)
        self.preset_combo.currentTextChanged.connect(
            lambda name: self.load_preset(name) if name in self.presets else None
        )
        self._format_changed()
        self.action.currentIndexChanged.connect(self._action_changed)
        self.buttons.accepted.disconnect()
        self.buttons.accepted.connect(self._confirm)
        # Keep confirmation visible when a high-DPI screen cannot fit the form.
        # The content can scroll independently of the title and action buttons.
        root = self.layout()
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 8, 0)
        while root.count() > 2:
            item = root.takeAt(1)
            if item.widget() is not None:
                body.addWidget(item.widget())
            elif item.layout() is not None:
                body.addLayout(item.layout())
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.content_scroll.setWidget(content)
        root.insertWidget(1, self.content_scroll, 1)
        available = self.screen().availableGeometry()
        self.setMinimumWidth(min(self.minimumWidth(), max(320, available.width() - 32)))
        self.resize(min(680, available.width() - 32), min(720, available.height() - 64))

    def _format_changed(self, *_):
        self.settings.set_format(('jpeg', 'png', 'tiff', 'dng', 'psd')[self.format.currentIndex()])
        self.quality.setEnabled(self.format.currentIndex() == 0)

    def _action_changed(self, *_):
        if self.action.currentData() == 'external':
            fmt = 'psd' if self.external_format.startswith('psd') else 'tiff'
            self.format.setCurrentIndex(4 if fmt == 'psd' else 2)
            self.settings.bit_depth.setCurrentText(
                '16' if self.external_format.endswith('16') else '8'
            )

    def options(self):
        opts = self.settings.options()
        if opts is not None:
            opts['quality'] = self.quality.value()
        return opts

    def metadata(self):
        out = {}
        for key, field in self.metadata_fields.items():
            text = field.text().strip()
            if text:
                out[key] = (
                    [s.strip() for s in text.split(',') if s.strip()]
                    if key in ('creator', 'keywords')
                    else text
                )
        out['rating'] = self.rating.value()
        return out

    def editor_arguments(self):
        try:
            args = json.loads(self.arguments.text())
        except ValueError as exc:
            raise ValueError('编辑器参数必须是 JSON 字符串数组。') from exc
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise ValueError('编辑器参数必须是 JSON 字符串数组。')
        return args

    def _confirm(self):
        try:
            opts = self.options()
            if self.action.currentData() == 'external':
                if not opts or opts['format'] not in ('tiff', 'psd'):
                    raise ValueError('外部编辑请选择 TIFF 或 PSD。')
                if not Path(self.executable.text()).is_file():
                    raise ValueError('请选择编辑器可执行程序。')
                self.editor_arguments()
                self.save_editor()
            self.accept()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, '专业导出', str(exc))

    def _save_prompt(self):
        name, accepted = QInputDialog.getText(self, '保存导出预设', '预设名称')
        if accepted and name.strip():
            try:
                self.save_preset(name.strip())
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, '导出预设', str(exc))

    def save_preset(self, name):
        opts = self.options()
        if opts is None:
            raise ValueError('DNG 使用原有线性成片导出，不保存为 RGB 预设。')
        self.presets[name] = opts
        self.preset_path.parent.mkdir(parents=True, exist_ok=True)
        ep.save(self.preset_path, self.presets)
        if self.preset_combo.findText(name) < 0:
            self.preset_combo.addItem(name)
        self.preset_combo.setCurrentText(name)

    def load_preset(self, name):
        opts = ep.validate(self.presets[name])
        self.format.setCurrentIndex(('jpeg', 'png', 'tiff', 'dng', 'psd').index(opts['format']))
        self.settings.set_options(opts)
        self.quality.setValue(opts['quality'])

    def _choose_program(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择外部编辑器', '', '程序 (*.exe)')
        if path:
            self.executable.setText(path)

    def save_editor(self):
        executable = self.executable.text().strip()
        if not Path(executable).is_file():
            raise ValueError('请选择编辑器可执行程序。')
        packet = json.dumps(
            {'executable': executable, 'arguments': self.editor_arguments()}, ensure_ascii=False
        )
        if len(packet.encode('utf-8')) > 16384:
            raise ValueError('外部编辑器配置过大。')
        self.editor_path.parent.mkdir(parents=True, exist_ok=True)
        ep.atomic_write(self.editor_path, lambda path: path.write_text(packet, encoding='utf-8'))

    def _choose_proof(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择打样配置', '', 'ICC (*.icc *.icm)')
        if path:
            self.proof_path.setText(path)

    def _apply_proof(self):
        options = {
            'enabled': self.proof_enabled.isChecked(),
            'path': self.proof_path.text(),
            'intent': self.intent.currentData(),
            'paper_ink': self.proof_paper.isChecked(),
            'black_point_compensation': self.proof_bpc.isChecked(),
            'gamut_warning': self.proof_gamut.isChecked(),
        }
        if options['enabled']:
            try:
                from ..imaging.cms import load_profile

                load_profile(options['path'])
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, '软打样', str(exc))
                return
        self.proofRequested.emit(options)
