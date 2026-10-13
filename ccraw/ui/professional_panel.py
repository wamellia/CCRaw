"""Recipe-backed professional controls, split from the main window layout."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTabWidget,
    QScrollArea,
    QLabel,
    QPushButton,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QMessageBox,
    QDoubleSpinBox,
    QLineEdit,
)

from ..domain import professional
from ..widgets import AdjustSlider


class ProfessionalPanel(QWidget):
    changed = Signal(dict)
    committed = Signal()
    sampleRequested = Signal()
    positionRequested = Signal()
    lensfunRequested = Signal(dict)
    bwAutoRequested = Signal()
    bwTargetRequested = Signal(bool)
    rangeRequested = Signal()
    constrainRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values = professional.defaults()
        self._loading = False
        self.controls = {}
        self.tabs = QTabWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        for group, title in [
            ('optics', '光学'),
            ('geometry', '几何'),
            ('calibration', '校准'),
            ('detail', '细节'),
        ]:
            page = self._page(title)
            for key, spec in professional.CONTROLS[group].items():
                slider = self._slider(page, spec[0], spec[1], spec[2], spec[4], spec[3])
                self.controls[group, key] = slider
                slider.changed.connect(lambda value, g=group, k=key: self._set(g, k, value))
            if group == 'geometry':
                self.flip_x, self.flip_y = QCheckBox('水平翻转'), QCheckBox('垂直翻转')
                for key, box in [('flip_x', self.flip_x), ('flip_y', self.flip_y)]:
                    page.addWidget(box)
                    box.toggled.connect(lambda value, k=key: self._set('geometry', k, value, True))
                self.constrain_button = QPushButton('约束裁切到有效图像')
                self.constrain_button.clicked.connect(self.constrainRequested)
                page.addWidget(self.constrain_button)
                self.geometry_grid = QCheckBox('显示几何参考网格')
                self.geometry_grid.setChecked(True)
                page.addWidget(self.geometry_grid)
            if group == 'optics':
                self.lens_database = None
                self.lens_combo = QComboBox()
                self.focal = QDoubleSpinBox()
                self.focal.setRange(1, 2000)
                self.focal.setValue(50)
                self.focal.setSuffix(' mm')
                load = QPushButton('导入本地 Lensfun XML…')
                load.clicked.connect(self._choose_lensfun)
                self.lens_apply = QPushButton('应用所选镜头校正')
                self.lens_apply.setEnabled(False)
                self.lens_apply.clicked.connect(self.apply_lensfun)
                for item in (load, self.lens_combo, self.focal, self.lens_apply):
                    page.addWidget(item)
                self.lens_status = QLabel('仅使用导入文件中的 poly3 / poly5 / PTLens 标定。')
                self.lens_status.setWordWrap(True)
                page.addWidget(self.lens_status)
            reset = QPushButton('重置本组')
            reset.clicked.connect(lambda _=False, g=group: self.reset_group(g))
            page.addWidget(reset)
            page.addStretch()

        page = self._page('黑白')
        automatic = QPushButton('自动黑白混色')
        automatic.clicked.connect(self.bwAutoRequested)
        self.bw_target = QCheckBox('在照片上拖动调整黑白色带')
        self.bw_target.toggled.connect(self.bwTargetRequested)
        page.addWidget(automatic)
        page.addWidget(self.bw_target)
        self.bw_controls = []
        for index, name in enumerate(('红', '橙', '黄', '绿', '青', '蓝', '紫', '洋红')):
            slider = self._slider(page, name, -100, 100, 1, 0)
            slider.changed.connect(lambda value, i=index: self._set_bw(i, value))
            self.bw_controls.append(slider)
        label = QLabel('在主色彩面板启用黑白后，八色混色生效。')
        label.setWordWrap(True)
        page.addWidget(label)
        page.addStretch()

        page = self._page('点颜色')
        self.sample_button = QPushButton('在原片上取样颜色')
        self.sample_button.clicked.connect(self.sampleRequested)
        self.sample_combo = QComboBox()
        self.sample_combo.currentIndexChanged.connect(self._refresh_point)
        self.sample_combo.currentIndexChanged.connect(self.rangeRequested)
        self.sample_name = QLineEdit()
        self.sample_name.setPlaceholderText('颜色样本名称')
        self.sample_name.setMaxLength(80)
        self.sample_name.editingFinished.connect(
            lambda: self._set_point('name', self.sample_name.text(), True)
        )
        remove = QPushButton('删除颜色样本')
        remove.clicked.connect(self.delete_color_sample)
        self.point_enabled = QCheckBox('启用当前样本')
        self.point_enabled.toggled.connect(lambda value: self._set_point('enabled', value, True))
        for item in (
            self.sample_button,
            self.sample_combo,
            self.sample_name,
            remove,
            self.point_enabled,
        ):
            page.addWidget(item)
        self.range_preview = QCheckBox('显示颜色范围')
        self.range_preview.toggled.connect(self.rangeRequested)
        page.addWidget(self.range_preview)
        self.point_controls = {}
        for key, label, low, high, default in [
            ('hue', '色相', -100, 100, 0),
            ('saturation', '饱和度', -100, 100, 0),
            ('luminance', '明度', -100, 100, 0),
            ('hue_range', '色相范围', 0, 100, 30),
            ('saturation_range', '饱和度范围', 0, 100, 50),
            ('luminance_range', '明度范围', 0, 100, 50),
            ('feather', '范围羽化', 0, 100, 50),
        ]:
            slider = self._slider(page, label, low, high, 1, default)
            slider.changed.connect(lambda value, k=key: self._set_point(k, value))
            self.point_controls[key] = slider
        page.addStretch()

        page = self._page('配置')
        load = QPushButton('导入 LUT / RGB ICC / DCP…')
        load.clicked.connect(self._choose_profile)
        clear = QPushButton('移除配置文件')
        clear.clicked.connect(self.clear_profile)
        self.profile_status = QLabel('未应用配置文件。')
        self.profile_status.setWordWrap(True)
        self.profile_amount = self._slider(page, '配置强度 %', 0, 200, 1, 100)
        self.profile_amount.changed.connect(lambda value: self._set('profile', 'amount', value))
        self.profile_mode = QComboBox()
        for text, mode in [
            ('自动：RAW 原生校色／普通照片外观', 'auto'),
            ('仅外观：色表与曲线', 'look'),
            ('相机校色：需要 RAW 原生 RGB', 'camera'),
        ]:
            self.profile_mode.addItem(text, mode)
        self.profile_mode.currentIndexChanged.connect(
            lambda _: self._set('profile', 'mode', self.profile_mode.currentData(), True)
        )
        page.addWidget(self.profile_mode)
        for item in (load, clear, self.profile_status):
            page.addWidget(item)
        page.addStretch()

        page = self._page('红眼')
        pick = QPushButton('在原片上标记瞳孔中心')
        pick.clicked.connect(self.positionRequested)
        self.eye_combo = QComboBox()
        self.eye_combo.currentIndexChanged.connect(self._refresh_eye)
        remove = QPushButton('删除当前瞳孔')
        remove.clicked.connect(self.delete_red_eye)
        for item in (pick, self.eye_combo, remove):
            page.addWidget(item)
        self.eye_radius = self._slider(page, '瞳孔半径 / 原片短边', 0.001, 0.2, 0.001, 0.02)
        self.eye_amount = self._slider(page, '红眼压暗', 0, 100, 1, 100)
        self.eye_radius.spin.setDecimals(3)
        self.eye_radius.changed.connect(lambda value: self._set_eye('radius', value))
        self.eye_amount.changed.connect(lambda value: self._set_eye('amount', value))
        page.addStretch()
        self.set_values(self._values)

    def _page(self, title):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, title)
        return layout

    def _slider(self, layout, label, low, high, step, default):
        slider = AdjustSlider(label, low, high, step)
        slider.default_value = default
        slider.setValue(default)
        slider.committed.connect(self.committed)
        layout.addWidget(slider)
        return slider

    def values(self):
        return copy.deepcopy(self._values)

    def set_bw_values(self, values):
        self._values['bw_mixer'] = list(values)
        self.set_values(self._values)
        self.changed.emit(self.values())
        self.committed.emit()

    def setRecipe(self, recipe):
        self.set_values(recipe.get('professional', professional.defaults()))

    def set_values(self, values):
        self._loading = True
        try:
            self._values = professional.validate(values)
            for (group, key), slider in self.controls.items():
                slider.setValue(self._values[group][key])
            for key, box in [('flip_x', self.flip_x), ('flip_y', self.flip_y)]:
                box.setChecked(self._values['geometry'][key])
            for index, slider in enumerate(self.bw_controls):
                slider.setValue(self._values['bw_mixer'][index])
            self.profile_amount.setValue(self._values['profile']['amount'])
            self.profile_mode.setCurrentIndex(
                self.profile_mode.findData(self._values['profile']['mode'])
            )
            path = self._values['profile']['path']
            self.profile_status.setText(
                getattr(self, '_profile_notice', {}).get(path, path or '未应用配置文件。')
            )
            self._selectors()
        finally:
            self._loading = False

    def _emit(self, commit=False):
        if self._loading:
            return
        self._values = professional.validate(self._values)
        self.changed.emit(self.values())
        if commit:
            self.committed.emit()

    def _set(self, group, key, value, commit=False):
        if not self._loading:
            self._values[group][key] = value
            self._emit(commit)

    def _set_bw(self, index, value):
        if not self._loading:
            self._values['bw_mixer'][index] = value
            self._emit()

    def reset_group(self, group):
        values = self.values()
        values[group] = professional.defaults()[group]
        if group == 'optics' and 'lensfun' in values:
            values['lensfun'] = None
            self.lens_status.setText('镜头标定已移除；手动光学控制已重置。')
        self.set_values(values)
        self._emit(True)

    def _selectors(self):
        for box, field, label in [
            (self.sample_combo, 'point_color', '颜色'),
            (self.eye_combo, 'red_eye', '瞳孔'),
        ]:
            previous = box.currentIndex()
            box.blockSignals(True)
            box.clear()
            box.addItems(
                [
                    self._values[field][i].get('name') or f'{label} {i + 1}'
                    for i in range(len(self._values[field]))
                ]
            )
            box.setCurrentIndex(min(max(0, previous), box.count() - 1))
            box.blockSignals(False)
        self.sample_button.setEnabled(len(self._values['point_color']) < 16)
        self._refresh_point()
        self._refresh_eye()

    def _refresh_point(self, *_):
        index = self.sample_combo.currentIndex()
        valid = 0 <= index < len(self._values['point_color'])
        self.sample_name.setText(self._values['point_color'][index]['name'] if valid else '')
        self.sample_name.setEnabled(valid)
        self.point_enabled.blockSignals(True)
        self.point_enabled.setChecked(valid and self._values['point_color'][index]['enabled'])
        self.point_enabled.setEnabled(valid)
        self.point_enabled.blockSignals(False)
        for key, slider in self.point_controls.items():
            slider.setEnabled(valid)
            slider.setValue(
                self._values['point_color'][index][key] if valid else slider.default_value
            )

    def add_color_sample(self, rgb):
        values = self.values()
        if len(values['point_color']) >= 16:
            raise ValueError('最多支持 16 个颜色样本。')
        values['point_color'].append({'rgb': [float(v) for v in rgb]})
        self.set_values(values)
        self.sample_combo.setCurrentIndex(len(values['point_color']) - 1)
        self._emit(True)

    def _set_point(self, key, value, commit=False):
        index = self.sample_combo.currentIndex()
        if not self._loading and index >= 0:
            self._values['point_color'][index][key] = value
            if key == 'name':
                self.sample_combo.setItemText(index, value or f'颜色 {index + 1}')
            self._emit(commit)

    def delete_color_sample(self):
        index = self.sample_combo.currentIndex()
        if index >= 0:
            del self._values['point_color'][index]
            self._selectors()
            self._emit(True)

    def _refresh_eye(self, *_):
        index = self.eye_combo.currentIndex()
        valid = 0 <= index < len(self._values['red_eye'])
        for key, slider in [('radius', self.eye_radius), ('amount', self.eye_amount)]:
            slider.setEnabled(valid)
            slider.setValue(self._values['red_eye'][index][key] if valid else slider.default_value)

    def add_red_eye(self, center):
        values = self.values()
        values['red_eye'].append(
            {'center': [float(v) for v in center], 'radius': 0.02, 'amount': 100}
        )
        self.set_values(values)
        self.eye_combo.setCurrentIndex(len(values['red_eye']) - 1)
        self._emit(True)

    def _set_eye(self, key, value):
        index = self.eye_combo.currentIndex()
        if not self._loading and index >= 0:
            self._values['red_eye'][index][key] = value
            self._emit()

    def delete_red_eye(self):
        index = self.eye_combo.currentIndex()
        if index >= 0:
            del self._values['red_eye'][index]
            self._selectors()
            self._emit(True)

    def _choose_profile(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '导入颜色配置', '', '配置文件 (*.cube *.icc *.icm *.dcp)'
        )
        if path:
            try:
                self.import_profile(path)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, '颜色配置', str(exc))

    def import_profile(self, path):
        from ..imaging.profiles import profile_info

        path = Path(path).resolve()
        info = profile_info(path)
        if info['type'] == 'dcp':
            labels = {
                'matrix': '相机矩阵',
                'forward_matrix': '正向矩阵',
                'dual_illuminant': '双光源插值',
                'tone_curve': '色调曲线',
                'hue_sat_map': '色相饱和度表',
                'look_table': '外观表',
                'baseline_exposure': '曝光补偿',
            }
            support = info.get('support', {})
            text = (
                'DCP：RAW 相机校色请使用 Process 2；普通照片只应用外观。'
                '相机矩阵假设单位模拟平衡／相机标定，不保证匹配厂商效果。\n'
                '可用项目：'
                + '、'.join(label for key, label in labels.items() if support.get(key))
                + '\n'
                '未支持标签：' + str(info.get('unsupported_tags', ()))
            )
            if not any(
                support.get(key)
                for key in ('tone_curve', 'hue_sat_map', 'look_table', 'baseline_exposure')
            ):
                text += '\n此文件没有外观项目；普通照片的外观模式不会产生改变。'
            if not hasattr(self, '_profile_notice'):
                self._profile_notice = {}
            self._profile_notice[str(path)] = text
            self.profile_status.setText(text)
        if info.get('supported') is False:
            raise ValueError('当前运行库不支持该配置文件。')
        self._values['profile']['path'] = str(path)
        if info['type'] != 'dcp':
            self.profile_status.setText(str(path))
        self._emit(True)
        return info

    def clear_profile(self):
        self._values['profile']['path'] = ''
        self.profile_status.setText('未应用配置文件。')
        self._emit(True)

    def _choose_lensfun(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入本地镜头标定', '', 'Lensfun XML (*.xml)')
        if path:
            try:
                self.import_lensfun(path)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, '镜头标定', str(exc))

    def import_lensfun(self, path):
        from ..imaging.lensfun_profiles import load_database

        database = load_database(path)
        self.lens_database = database
        self.lens_combo.clear()
        for lens in database['lenses']:
            self.lens_combo.addItem(lens['maker'] + ' ' + lens['model'], lens)
        self.lens_apply.setEnabled(bool(database['lenses']))
        self.lens_status.setText(f'已读取 {len(database["lenses"])} 个镜头；仅使用提供的标定范围。')
        return database

    def apply_lensfun(self):
        from ..imaging.lensfun_profiles import interpolate_distortion

        lens = self.lens_combo.currentData()
        if not lens:
            return
        try:
            calibration = interpolate_distortion(lens, self.focal.value())
            path = Path(self.lens_database['source'])
            packet = {
                'path': str(path),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'maker': lens['maker'],
                'model': lens['model'],
                'focal': self.focal.value(),
                'calibration': calibration,
            }
            self.lensfunRequested.emit(packet)
            self.lens_status.setText(f'标定已选择：{lens["model"]} · {calibration["model"]}')
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, '镜头标定', str(exc))
