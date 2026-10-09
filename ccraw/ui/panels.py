from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QScrollArea,
    QComboBox,
    QCheckBox,
    QListWidget,
    QSpinBox,
    QSplitter,
    QGridLayout,
    QButtonGroup,
    QToolButton,
    QSizePolicy,
)
from .. import model
from ..widgets import AdjustSlider, Canvas, CurveEditor, Histogram
from ..exposure_curve import ExposureCurve
from ..watermark_dialog import WatermarkEditor

from .components import note, heading
from .icons import set_icon
from .theme import style_swatch


class PanelsMixin:
    def button(self, text, callback, primary=False):
        b = QPushButton(text)
        b.clicked.connect(callback)
        if primary:
            b.setObjectName('primary')
        return b

    def build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        top = QWidget()
        top.setObjectName('topbar')
        top.setFixedHeight(64)
        bar = QHBoxLayout(top)
        bar.setContentsMargins(16, 0, 16, 0)
        bar.setSpacing(8)
        brand = QLabel('CCRaw')
        brand.setObjectName('brand')
        bar.addWidget(brand)
        self.sidebar_button = self.button('侧栏', lambda: None)
        self.sidebar_button.setCheckable(True)
        self.sidebar_button.setChecked(True)
        self.sidebar_button.setToolTip('显示侧栏')
        self.sidebar_button.setAccessibleName('显示侧栏')
        set_icon(self.sidebar_button, 'sidebar')
        bar.addSpacing(16)
        bar.addWidget(self.sidebar_button)
        self.undo_button = self.button('撤销', lambda: self.undo(-1))
        self.undo_button.setToolTip('撤销 (Ctrl+Z)')
        set_icon(self.undo_button, 'undo')
        bar.addWidget(self.undo_button)
        self.redo_button = self.button('重做', lambda: self.undo(1))
        self.redo_button.setToolTip('重做 (Ctrl+Shift+Z)')
        set_icon(self.redo_button, 'redo')
        bar.addWidget(self.redo_button)
        bar.addStretch()
        self.open_button = self.button('打开', self.open_file)
        set_icon(self.open_button, 'open')
        bar.addWidget(self.open_button)
        self.save_button = self.button('保存工程', self.save)
        set_icon(self.save_button, 'save')
        bar.addWidget(self.save_button)
        self.export_button = self.button('导出', self.export, True)
        set_icon(self.export_button, 'export', True)
        bar.addWidget(self.export_button)
        outer.addWidget(top)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.library = self.build_library()
        self.sidebar_button.toggled.connect(self.library.setVisible)
        splitter.addWidget(self.library)
        left = QWidget()
        l = QVBoxLayout(left)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(0)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(16, 8, 16, 8)
        toolbar.setSpacing(8)
        self.split_check = QCheckBox('前后对比')
        self.split_check.toggled.connect(self.update_display)
        toolbar.addWidget(self.split_check)
        self.compare = QPushButton('原片')
        self.compare.setToolTip('按住查看原片')
        self.compare.pressed.connect(lambda: self.show_original(True))
        self.compare.released.connect(lambda: self.show_original(False))
        toolbar.addWidget(self.compare)
        self.final_view = QCheckBox('成片预览')
        self.final_view.toggled.connect(self.update_display)
        toolbar.addWidget(self.final_view)
        toolbar.addStretch()
        l.addLayout(toolbar)
        self.canvas = Canvas()
        self.canvas.opened.connect(self.open_file)
        self.canvas.dropped.connect(self.open_path)
        self.canvas.files_dropped.connect(self.import_paths)
        self.canvas.viewport_changed.connect(self.viewport_changed)
        self.canvas.geometry_changed.connect(self.geometry_changed)
        self.canvas.stroke_finished.connect(self.stroke_finished)
        self.canvas.sampled.connect(self.pick_wb)
        l.addWidget(self.canvas, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(18, 12, 18, 12)
        self.file_label = note('尚未打开原片')
        footer.addWidget(self.file_label, 1)
        self.zoom_label = note('适应窗口')
        footer.addWidget(self.zoom_label)
        footer.addWidget(self.button('适应', self.canvas.fit))
        footer.addWidget(self.button('100%', self.canvas.actual_size))
        self.canvas.zoom_changed.connect(self.zoom_label.setText)
        l.addLayout(footer)
        splitter.addWidget(left)
        right = QWidget()
        right.setObjectName('inspector')
        right.setMinimumWidth(380)
        right.setMaximumWidth(420)
        r = QVBoxLayout(right)
        r.setContentsMargins(16, 16, 16, 12)
        r.setSpacing(8)
        heading_row = QHBoxLayout()
        heading_row.addWidget(heading('调整'), 1)
        self.histogram_button = self.button('直方图', lambda: None)
        self.histogram_button.setCheckable(True)
        self.histogram_button.setChecked(True)
        self.histogram_button.setToolTip('显示或收起直方图与溢出提示')
        heading_row.addWidget(self.histogram_button)
        self.auto_button = self.button('自动', self.automatic)
        self.auto_button.setToolTip('根据原片亮度分布设置曝光、暗部、亮部与对比度')
        heading_row.addWidget(self.auto_button)
        r.addLayout(heading_row)
        self.histogram_panel = QWidget()
        histogram_layout = QVBoxLayout(self.histogram_panel)
        histogram_layout.setContentsMargins(0, 0, 0, 0)
        histogram_layout.setSpacing(4)
        self.histogram = Histogram()
        histogram_layout.addWidget(self.histogram)
        warning_row = QHBoxLayout()
        self.shadow_warning = QCheckBox('暗部溢出')
        self.highlight_warning = QCheckBox('高光溢出')
        self.shadow_warning.toggled.connect(self.update_display)
        self.highlight_warning.toggled.connect(self.update_display)
        warning_row.addWidget(self.shadow_warning)
        warning_row.addStretch()
        warning_row.addWidget(self.highlight_warning)
        histogram_layout.addLayout(warning_row)
        r.addWidget(self.histogram_panel)
        self.histogram_button.toggled.connect(self.histogram_panel.setVisible)
        self._compact_inspector = False
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self.tab_changed)
        self.tabs.tabBar().hide()
        self.tool_select = QComboBox()
        self.tool_select.setAccessibleName('调整工具')
        self.tool_select.hide()
        self.tool_grid = QGridLayout()
        self.tool_grid.setSpacing(6)
        r.addLayout(self.tool_grid)
        r.addWidget(self.tabs, 1)
        self.build_basic()
        self.build_color()
        self.build_watermark()
        self.build_details()
        self.build_masks()
        self.build_crop()
        self.build_grading()
        self.build_effects()
        self.build_retouch()
        self.tool_select.addItems([self.tabs.tabText(i) for i in range(self.tabs.count())])
        self.tool_select.currentIndexChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.tool_select.setCurrentIndex)
        self.build_tool_navigation()
        backend_row = QHBoxLayout()
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(['自动加速', 'CPU'])
        self.backend_combo.setToolTip('计算设备')
        self.backend_combo.currentIndexChanged.connect(self.backend_changed)
        backend_row.addWidget(self.backend_combo, 1)
        backend_row.addWidget(self.button('重置', self.reset_edits))
        r.addLayout(backend_row)
        self.backend_label = note(self.backend.name)
        self.backend_label.hide()

        splitter.addWidget(right)
        splitter.setSizes([240, 940, 420])
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter, 1)
        outer.addWidget(self.build_filmstrip())
        self.setCentralWidget(central)
        self.statusBar().showMessage('就绪')
        self.state_label = self.statusBar().state_label
        self.state_label.setText('')
        self.statusBar().refresh_mode()
        self.build_menus()

    def build_tool_navigation(self):
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        symbols = [
            'light',
            'color',
            'watermark',
            'detail',
            'mask',
            'crop',
            'grading',
            'effects',
            'retouch',
        ]
        self.tool_buttons = []
        for index, symbol in enumerate(symbols):
            button = QToolButton()
            button.setObjectName('toolNavigation')
            button.setText(self.tabs.tabText(index))
            button.setAccessibleName(button.text())
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setCheckable(True)
            button.setMinimumHeight(38)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            set_icon(button, symbol)
            self.tool_group.addButton(button, index)
            self.tool_buttons.append(button)
        for position, index in enumerate((0, 1, 3, 4, 5, 6, 7, 8, 2)):
            self.tool_grid.addWidget(self.tool_buttons[index], position // 3, position % 3)
        self.tool_group.idClicked.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(lambda index: self.tool_buttons[index].setChecked(True))
        self.tool_buttons[self.tabs.currentIndex()].setChecked(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'histogram_button'):
            compact = self.height() < 900
            if compact != self._compact_inspector:
                self._compact_inspector = compact
                self.histogram_button.setChecked(not compact)
                self.exposure_curve.setFixedHeight(120 if compact else 165)

    def panel(self, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 8, 16)
        layout.setSpacing(12)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)
        return layout

    def add_adjustment(self, layout, label, key, low=-100, high=100, step=1, local=False):
        control = AdjustSlider(label, low, high, step)
        control.changed.connect(lambda value, k=key, loc=local: self.adjust(k, value, loc))
        control.committed.connect(self.commit)
        (self.local_controls if local else self.controls)[key] = control
        layout.addWidget(control)

    def build_basic(self):
        l = self.panel('光影')
        l.addWidget(heading('曝光与动态范围'))
        self.exposure_curve = ExposureCurve()
        self.exposure_curve.changed.connect(self.exposure_curve_changed)
        self.exposure_curve.committed.connect(self.commit)
        l.addWidget(self.exposure_curve)
        for pair in (
            (('exposure', '曝光 EV'), ('contrast', '对比度')),
            (('shadows', '暗部'), ('highlights', '亮部')),
            (('blacks', '黑色'), ('whites', '白色')),
        ):
            row = QHBoxLayout()
            for key, title in pair:
                column = QVBoxLayout()
                self.add_adjustment(
                    column,
                    title,
                    key,
                    -5 if key == 'exposure' else -100,
                    5 if key == 'exposure' else 100,
                    0.05 if key == 'exposure' else 1,
                )
                row.addLayout(column, 1)
            l.addLayout(row)
        self.build_develop_profile(l)
        l.addWidget(heading('白平衡'))
        row = QHBoxLayout()
        self.wb_button = QPushButton('吸管取样')
        self.wb_button.setCheckable(True)
        self.wb_button.toggled.connect(self.toggle_wb)
        row.addWidget(self.wb_button)
        row.addWidget(self.button('还原相机白平衡', self.reset_wb))
        l.addLayout(row)
        self.build_camera_wb(l)
        self.add_adjustment(l, '色温偏移', 'temperature')
        self.add_adjustment(l, '色调', 'tint')
        l.addStretch()

    def build_color(self):
        l = self.panel('色彩')
        l.addWidget(heading('全局色彩'))
        self.mono_check = QCheckBox('黑白处理')
        self.mono_check.toggled.connect(self.monochrome_changed)
        l.addWidget(self.mono_check)
        self.add_adjustment(l, '饱和度', 'saturation')
        self.add_adjustment(l, '自然饱和度', 'vibrance')
        l.addWidget(heading('色彩混合器'))
        swatches = QHBoxLayout()
        swatches.setSpacing(4)
        self.swatch_buttons = []
        self.swatch_group = QButtonGroup(self)
        self.swatch_group.setExclusive(True)
        for index, (title, _, swatch) in enumerate(model.COLORS):
            b = QPushButton('')
            b.setFixedSize(36, 36)
            b.setToolTip(title)
            b.setAccessibleName(title)
            b.setCheckable(True)
            self.swatch_group.addButton(b, index)
            b.setChecked(index == 0)
            style_swatch(b, swatch)
            b.clicked.connect(lambda checked=False, i=index: self.color_select.setCurrentIndex(i))
            swatches.addWidget(b)
            self.swatch_buttons.append(b)
        l.addLayout(swatches)
        self.color_select = QComboBox()
        for title, _, _ in model.COLORS:
            self.color_select.addItem(title)
        self.color_select.currentIndexChanged.connect(self.refresh_hsl)
        l.addWidget(self.color_select)
        self.hsl_controls = []
        for index, title in enumerate(['色相', '饱和度', '明度']):
            c = AdjustSlider(title)
            c.changed.connect(lambda value, i=index: self.change_hsl(i, value))
            c.committed.connect(self.commit)
            l.addWidget(c)
            self.hsl_controls.append(c)
        self.build_curves(l)
        l.addStretch()

    def build_curves(self, l):
        l.addWidget(heading('RGB 与通道曲线'))
        self.channel = QComboBox()
        self.channel.addItems(['RGB', 'R', 'G', 'B'])
        self.channel.currentTextChanged.connect(self.refresh_curve)
        l.addWidget(self.channel)
        self.curve = CurveEditor()
        self.curve.changed.connect(self.curve_changed)
        self.curve.committed.connect(self.commit)
        l.addWidget(self.curve)
        values = QHBoxLayout()
        self.curve_input = QSpinBox()
        self.curve_output = QSpinBox()
        for title, control in [('输入', self.curve_input), ('输出', self.curve_output)]:
            control.setRange(0, 255)
            control.setKeyboardTracking(False)
            values.addWidget(QLabel(title))
            values.addWidget(control)
        self.curve.selected.connect(self.curve_selection)
        self.curve_input.valueChanged.connect(lambda v: self.curve.report(v / 255))
        self.curve_output.valueChanged.connect(
            lambda v: self.curve.set_output(self.curve_input.value(), v)
        )
        l.addLayout(values)
        self.curve_mode = QComboBox()
        self.curve_mode.addItems(['平滑曲线', '分段直线'])
        self.curve_mode.currentIndexChanged.connect(self.curve_mode_changed)
        l.addWidget(self.curve_mode)
        l.addWidget(self.button('重置当前通道', self.reset_curve))

    def build_watermark(self):
        l = self.panel('水印')
        l.addWidget(heading('水印与边框'))
        self.watermark_editor = WatermarkEditor(self, compact=True)
        self.watermark_editor.changed.connect(self.watermark_changed)
        l.addWidget(self.watermark_editor)
        l.addWidget(self.button('打开大图水印预览…', self.configure_watermark))
        l.addStretch()

    def build_details(self):
        l = self.panel('细节')
        l.addWidget(heading('质感'))
        self.add_adjustment(l, '去薄雾', 'dehaze')
        self.add_adjustment(l, '清晰度', 'clarity')
        self.add_adjustment(l, '纹理', 'texture')
        self.add_adjustment(l, '锐化', 'sharpness', 0, 100)
        l.addWidget(self.button('AI 超分辨率…', lambda: self.open_ai('super'), True))
        l.addWidget(self.button('AI 去杂色…', lambda: self.open_ai('denoise')))
        l.addWidget(heading('降噪'))
        self.add_adjustment(l, '明度降噪', 'denoise', 0, 100)
        self.add_adjustment(l, '彩色杂点', 'color_noise', 0, 100)
        l.addStretch()

    def build_masks(self):
        l = self.panel('蒙版')
        l.addWidget(heading('局部调整'))
        row = QHBoxLayout()
        for title, kind in [
            ('画笔', 'brush'),
            ('渐变', 'linear'),
            ('径向', 'radial'),
            ('亮度', 'luminance'),
        ]:
            row.addWidget(self.button(title, lambda checked=False, k=kind: self.add_mask(k)))
        l.addLayout(row)
        self.build_auto_masks(l)
        self.mask_list = QListWidget()
        self.mask_list.setFixedHeight(105)
        self.mask_list.currentRowChanged.connect(self.select_mask)
        l.addWidget(self.mask_list)
        row = QHBoxLayout()
        self.overlay_check = QCheckBox('显示范围')
        self.overlay_check.setChecked(True)
        self.overlay_check.toggled.connect(self.update_overlay)
        self.enabled_check = QCheckBox('启用')
        self.enabled_check.setChecked(True)
        self.enabled_check.toggled.connect(lambda v: self.mask_property('enabled', v))
        self.invert_check = QCheckBox('反选')
        self.invert_check.toggled.connect(lambda v: self.mask_property('invert', v))
        row.addWidget(self.overlay_check)
        row.addWidget(self.enabled_check)
        row.addWidget(self.invert_check)
        l.addLayout(row)
        row = QHBoxLayout()
        self.erase_check = QCheckBox('橡皮擦')
        self.erase_check.toggled.connect(lambda v: setattr(self.canvas, 'erase', v))
        row.addWidget(self.erase_check)
        row.addStretch()
        row.addWidget(self.button('删除蒙版', self.delete_mask))
        l.addLayout(row)
        self.brush_size = AdjustSlider('画笔大小', 1, 40)
        self.brush_size.setValue(9)
        self.brush_size.default_value = 9
        self.brush_size.changed.connect(lambda v: setattr(self.canvas, 'brush_radius', v / 200))
        l.addWidget(self.brush_size)
        self.opacity = AdjustSlider('不透明度', 0, 100)
        self.opacity.default_value = 100
        self.opacity.changed.connect(lambda v: self.mask_property('opacity', v))
        l.addWidget(self.opacity)
        self.feather = AdjustSlider('羽化', 0, 100)
        self.feather.default_value = 50
        self.feather.changed.connect(lambda v: self.mask_property('feather', v))
        l.addWidget(self.feather)
        self.range_box = QWidget()
        range_layout = QVBoxLayout(self.range_box)
        range_layout.setContentsMargins(0, 0, 0, 0)
        self.range_controls = {}
        for key, title in [('low', '亮度下限'), ('high', '亮度上限'), ('falloff', '过渡范围')]:
            c = AdjustSlider(title, 0, 100)
            c.changed.connect(lambda value, k=key: self.range_value(k, value))
            c.committed.connect(self.commit)
            self.range_controls[key] = c
            range_layout.addWidget(c)
        l.addWidget(self.range_box)
        for key, title in [
            ('exposure', '局部曝光 EV'),
            ('shadows', '局部暗部'),
            ('highlights', '局部亮部'),
            ('temperature', '局部色温'),
            ('saturation', '局部饱和度'),
            ('clarity', '局部清晰度'),
            ('sharpness', '局部锐化'),
        ]:
            self.add_adjustment(
                l,
                title,
                key,
                -5 if key == 'exposure' else (0 if key == 'sharpness' else -100),
                5 if key == 'exposure' else 100,
                0.05 if key == 'exposure' else 1,
                True,
            )
        l.addStretch()

    def build_crop(self):
        l = self.panel('裁切')
        l.addWidget(heading('重新构图'))
        self.crop_ratio = QComboBox()
        self.crop_ratio.addItems(
            ['自由比例', '原片比例', '1 : 1', '3 : 2', '4 : 3', '16 : 9', '2 : 3', '9 : 16']
        )
        self.crop_ratio.currentIndexChanged.connect(self.ratio_changed)
        l.addWidget(self.crop_ratio)
        l.addWidget(self.button('确认裁切', self.confirm_crop, True))
        l.addWidget(self.button('重新裁切', lambda: self.final_view.setChecked(False)))
        l.addWidget(self.button('清除裁切', self.clear_crop))
        l.addWidget(self.button('顺时针旋转 90°', self.rotate))
        self.straighten = AdjustSlider('水平校正  °', -15, 15, 0.1)
        self.straighten.changed.connect(self.straighten_changed)
        self.straighten.committed.connect(self.commit)
        l.addWidget(self.straighten)
        l.addStretch()

    def shortcuts(self):
        for shortcut, fn in [
            ('Ctrl+O', self.open_file),
            ('Ctrl+S', self.save),
            ('Ctrl+E', self.export),
            ('Ctrl+Z', lambda: self.undo(-1)),
            ('Ctrl+Shift+Z', lambda: self.undo(1)),
            ('Ctrl+0', self.canvas.fit),
            ('J', self.toggle_clipping),
            ('Return', self.confirm_crop),
            ('Enter', self.confirm_crop),
            ('Y', lambda: self.split_check.setChecked(not self.split_check.isChecked())),
            ('Esc', lambda: self.wb_button.setChecked(False)),
        ]:
            action = QAction(self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(fn)
            self.addAction(action)
