"""Typed, reusable editors for semi-utils values; no JSON entry is required."""

import copy
import ast
import json
import re

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLineEdit,
    QComboBox,
    QDoubleSpinBox,
    QPushButton,
    QColorDialog,
    QCheckBox,
    QSizePolicy,
    QLabel,
)
from .watermark import FONTS


ENUMS = {
    'horizontal_alignment': ('left', 'center', 'right'),
    'vertical_alignment': ('top', 'middle', 'bottom'),
    'text_alignment': ('top', 'middle', 'bottom'),
    'right_alignment': ('left', 'right'),
    'alignment': ('top', 'middle', 'bottom'),
    'logo_position': ('left', 'right'),
    'direction': ('horizontal', 'vertical', 'diagonal', 'radial'),
    'interpolate_method': ('linear', 'ease_in', 'ease_out', 'ease_in_out'),
}
NUMERIC = {
    'blur_radius',
    'scale',
    'width',
    'height',
    'left_margin',
    'right_margin',
    'top_margin',
    'bottom_margin',
    'delimiter_width',
    'middle_spacing',
    'center_logo_height',
    'border_radius',
    'shadow_radius',
    'text_spacing',
    'spacing',
    'ImageWidth',
    'ImageHeight',
    'padding_percent',
    'white_blend',
    'white_margin',
}
ARRAYS = {'select', 'offset', 'offsets', 'weights'}
TEXT_SOURCES = {
    'Model': '{{exif.CameraModelName}}',
    'Make': '{{exif.CameraMake}}',
    'LensModel': '{{exif.LensModel}}',
    'Param': '{{exif.Param}}',
    'Datetime': '{{exif.Datetime}}',
    'Date': '{{exif.Date}}',
    'Custom': None,
    'None': '',
    'LensMakeLensModel': '{{exif.LensMake_LensModel}}',
    'CameraModelLensModel': '{{exif.CameraModel_LensModel}}',
    'TotalPixel': '{{exif.TotalPixel}}',
    'CameraMakeCameraModel': '{{exif.CameraMake_CameraModel}}',
    'Filename': '{{exif.Filename}}',
    'DateFilename': '{{exif.Date_Filename}}',
    'DatetimeFilename': '{{exif.Datetime_Filename}}',
    'GeoInfo': '{{exif.GeoInfo}}',
}


class DimensionEditor(QWidget):
    """Pixels, original-photo vh/vw units, auto or an editable Jinja expression."""

    changed = Signal(object)

    def __init__(self, value=0, parent=None):
        super().__init__(parent)
        self._loading = False
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.number = QDoubleSpinBox()
        self.number.setRange(-1_000_000, 1_000_000)
        self.number.setDecimals(4)
        self.number.setKeyboardTracking(False)
        self.number.setMinimumWidth(65)
        self.expression = QLineEdit()
        self.expression.setPlaceholderText('{{vh(4)}} 或其他模板表达式')
        self.unit = QComboBox()
        for label, key in [
            ('px / 数值', 'px'),
            ('vh %', 'vh'),
            ('vw %', 'vw'),
            ('表达式', 'expr'),
            ('自动', 'auto'),
        ]:
            self.unit.addItem(label, key)
        self.unit.setMinimumWidth(76)
        row.addWidget(self.number, 1)
        row.addWidget(self.expression, 1)
        row.addWidget(self.unit)
        self.number.valueChanged.connect(self._emit)
        self.expression.editingFinished.connect(self._emit)
        self.unit.currentIndexChanged.connect(self._mode)
        self.set_value(value)

    def _mode(self, *_):
        mode = self.unit.currentData()
        self.number.setVisible(mode in ('px', 'vh', 'vw'))
        self.expression.setVisible(mode == 'expr')
        if mode == 'expr' and not self.expression.text():
            self.expression.setText(str(self.number.value()))
        self._emit()

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit(self.value())

    def value(self):
        mode = self.unit.currentData()
        number = self.number.value()
        number = int(number) if number.is_integer() else number
        if mode == 'auto':
            return None
        if mode == 'expr':
            return self.expression.text()
        if mode in ('vh', 'vw'):
            return '{{' + mode + '(' + str(number) + ')}}'
        return number

    def set_value(self, value, emit=False):
        self._loading = True
        match = re.fullmatch(r'\s*\{\{\s*(vh|vw)\(\s*([-+\d.]+)\s*\)\s*\}\}\s*', str(value))
        mode = 'px'
        if value is None:
            mode = 'auto'
        elif match:
            mode = match[1]
            self.number.setValue(float(match[2]))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            self.number.setValue(value)
        else:
            mode = 'expr'
            self.expression.setText(str(value))
        self.unit.setCurrentIndex(self.unit.findData(mode))
        self._mode()
        self._loading = False
        if emit:
            self._emit()


class ColorEditor(QWidget):
    changed = Signal(object)

    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.original = value
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.text = QLineEdit(
            ', '.join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)
        )
        self.button = QPushButton('颜色')
        self.button.setMaximumWidth(62)
        row.addWidget(self.text, 1)
        row.addWidget(self.button)
        self.text.editingFinished.connect(lambda: self.changed.emit(self.value()))
        self.button.clicked.connect(self.choose)

    def value(self):
        text = self.text.text().strip()
        if isinstance(self.original, (tuple, list)) and ',' in text:
            try:
                return [int(v.strip()) for v in text.strip('()[]').split(',')]
            except ValueError:
                pass
        return text

    def choose(self):
        from .watermark_runtime import _parse_color

        try:
            color = QColor(*_parse_color(self.value()))
        except (ValueError, TypeError):
            color = QColor('white')
        picked = QColorDialog.getColor(
            color, self, '选择颜色与透明度', QColorDialog.ColorDialogOption.ShowAlphaChannel
        )
        if picked.isValid():
            rgba = (picked.red(), picked.green(), picked.blue(), picked.alpha())
            # Pillow expects RRGGBBAA, unlike Qt's ARGB HexArgb spelling.
            self.text.setText(
                ', '.join(map(str, rgba))
                if isinstance(self.original, (list, tuple))
                else '#' + ''.join(f'{c:02x}' for c in rgba)
            )
            self.changed.emit(self.value())


class ArrayEditor(QWidget):
    """Visual ordered numeric rows, including offset coordinate pairs."""

    changed = Signal(object)

    def __init__(self, value, key='', parent=None):
        super().__init__(parent)
        self.string_value = isinstance(value, str)
        self.key = key
        self.raw_expression = None
        try:
            self.items = json.loads(value) if self.string_value else copy.deepcopy(value)
        except (ValueError, TypeError):
            try:
                self.items = self.parse_expression(value)
            except (ValueError, SyntaxError, TypeError):
                self.items = []
                self.raw_expression = str(value)
        if not isinstance(self.items, list):
            self.items = []
        self.single_pair = key == 'offset'
        if self.single_pair:
            self.items = [self.items[:2] if len(self.items) >= 2 else [0, 0]]
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(4)
        self._build()

    @staticmethod
    def parse_expression(value):
        text = str(value).strip()
        if text.startswith('{{') and text.endswith('}}'):
            text = text[2:-2].strip()
        else:
            text = re.sub(r'\{\{\s*(.*?)\s*\}\}', r'\1', text)
        tree = ast.parse(text, mode='eval')

        def decode(node):
            if isinstance(node, (ast.List, ast.Tuple)):
                return [decode(child) for child in node.elts]
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return node.value
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ('vh', 'vw')
                and len(node.args) == 1
                and not node.keywords
            ):
                number = decode(node.args[0])
                if isinstance(number, (int, float)):
                    return '{{' + node.func.id + '(' + str(number) + ')}}'
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                value = decode(node.operand)
                if isinstance(value, (int, float)):
                    return -value if isinstance(node.op, ast.USub) else value
                match = re.fullmatch(r'\{\{(vh|vw)\(([-+\d.]+)\)\}\}', value)
                if match:
                    number = float(match[2]) * (-1 if isinstance(node.op, ast.USub) else 1)
                    return '{{' + match[1] + '(' + str(number) + ')}}'
            raise ValueError('Complex array expression')

        value = decode(tree.body)
        if not isinstance(value, list):
            raise ValueError('Expected array expression')
        return value

    def _build(self):
        while self.root.count():
            item = self.root.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self.raw_expression is not None:
            formula = QLineEdit(self.raw_expression)
            formula.setToolTip('此复合表达式已完整保留。可直接修改表达式，或切换到可视化数值。')
            formula.editingFinished.connect(lambda: self._formula(formula.text()))
            self.root.addWidget(formula)
            switch = QPushButton(
                '改用可视化坐标' if self.key in ('offset', 'offsets') else '改用可视化数值'
            )
            switch.clicked.connect(self.use_rows)
            self.root.addWidget(switch)
            return
        for i, value in enumerate(self.items):
            box = QWidget()
            block = QVBoxLayout(box)
            block.setContentsMargins(0, 0, 0, 0)
            block.setSpacing(3)
            values = value if isinstance(value, list) else [value]
            for j, number in enumerate(values):
                row = QHBoxLayout()
                if isinstance(value, list):
                    row.addWidget(QLabel('X' if j == 0 else 'Y'))
                control = DimensionEditor(number)
                control.changed.connect(
                    lambda n, i=i, j=j, pair=isinstance(value, list): self._edit(i, j, n, pair)
                )
                row.addWidget(control, 1)
                block.addLayout(row)
            row = QHBoxLayout()
            row.addStretch()
            for label, delta in [('↑', -1), ('↓', 1), ('−', 0)]:
                if self.single_pair:
                    break
                button = QPushButton(label)
                button.setFixedWidth(30)
                button.setAccessibleName(
                    {'↑': '向上移动数值', '↓': '向下移动数值', '−': '删除数值'}[label]
                )
                button.clicked.connect(
                    lambda checked=False, i=i, delta=delta: self._change(i, delta)
                )
                row.addWidget(button)
            block.addLayout(row)
            self.root.addWidget(box)
        if not self.single_pair:
            add = QPushButton('＋ 添加坐标' if self.key == 'offsets' else '＋ 添加数值')
            add.clicked.connect(self.add)
            self.root.addWidget(add)

    def _edit(self, i, j, value, pair):
        if pair:
            self.items[i][j] = value
        else:
            self.items[i] = value
        self.changed.emit(self.value())

    def _formula(self, text):
        self.raw_expression = text
        self.changed.emit(text)

    def use_rows(self):
        self.raw_expression = None
        self.items = [[0, 0]] if self.key in ('offset', 'offsets') else []
        self._build()
        self.changed.emit(self.value())

    def _change(self, i, delta):
        if self.single_pair:
            return
        if delta == 0:
            self.items.pop(i)
        elif 0 <= i + delta < len(self.items):
            self.items[i], self.items[i + delta] = self.items[i + delta], self.items[i]
        self._build()
        self.changed.emit(self.value())

    def add(self):
        self.items.append([0, 0] if self.key in ('offset', 'offsets') else 0)
        self._build()
        self.changed.emit(self.value())

    def value(self):
        if self.raw_expression is not None:
            return self.raw_expression
        value = self.items[0] if self.single_pair else self.items
        if not self.string_value:
            return copy.deepcopy(value)

        def expression(item):
            if isinstance(item, list):
                return '[' + ', '.join(expression(child) for child in item) + ']'
            if isinstance(item, str) and item.startswith('{{') and item.endswith('}}'):
                return item[2:-2].strip()
            return json.dumps(item)

        text = expression(value)
        return '{{' + text + '}}' if re.search(r'\b(vh|vw)\(', text) else json.dumps(value)


class ResourceEditor(QWidget):
    changed = Signal(object)

    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.list_value = isinstance(value, list)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.controls = []
        for handle in value if self.list_value else [value]:
            self.add(handle)
        if self.list_value:
            button = QPushButton('＋ 添加资源')
            button.clicked.connect(lambda: self.add('source', emit=True))
            self.root.addWidget(button)

    def add(self, handle, emit=False):
        from .watermark import RESOURCE_HANDLES

        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        control = QComboBox()
        control.setEditable(True)
        control.addItems(['', '{{auto_logo()}}', *RESOURCE_HANDLES])
        control.setCurrentText('' if handle is None else str(handle))
        control.setMinimumWidth(0)
        control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        control.currentTextChanged.connect(lambda *_: self.changed.emit(self.value()))
        self.controls.append(control)
        row.addWidget(control, 1)
        if self.list_value:
            remove = QPushButton('−')
            remove.setFixedWidth(30)
            remove.clicked.connect(lambda: self.remove(box, control))
            row.addWidget(remove)
        self.root.insertWidget(len(self.controls) - 1, box)
        if emit:
            self.changed.emit(self.value())

    def remove(self, box, control):
        self.controls.remove(control)
        box.deleteLater()
        self.changed.emit(self.value())

    def value(self):
        values = [control.currentText() for control in self.controls]
        return values if self.list_value else values[0]


class RatioEditor(QWidget):
    changed = Signal(object)
    ORIGINAL = '{{exif.ImageWidth}}:{{exif.ImageHeight}}'

    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.original = value
        self._loading = True
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.preset = QComboBox()
        for label, ratio in [
            ('原始照片比例', self.ORIGINAL),
            ('1:1', '1:1'),
            ('3:2', '3:2'),
            ('4:3', '4:3'),
            ('16:9', '16:9'),
            ('自定义比例', 'custom'),
            ('保留模板表达式', 'expr'),
        ]:
            self.preset.addItem(label, ratio)
        root.addWidget(self.preset)
        self.custom = QWidget()
        row = QHBoxLayout(self.custom)
        row.setContentsMargins(0, 0, 0, 0)
        self.width = QDoubleSpinBox()
        self.height = QDoubleSpinBox()
        for label, control in [('宽', self.width), ('高', self.height)]:
            row.addWidget(QLabel(label))
            control.setRange(0.001, 10000)
            control.setDecimals(3)
            control.setValue(1)
            control.setKeyboardTracking(False)
            control.valueChanged.connect(self._emit)
            row.addWidget(control, 1)
        root.addWidget(self.custom)
        self.expression = QLineEdit(str(value))
        self.expression.editingFinished.connect(self._emit)
        root.addWidget(self.expression)
        index = self.preset.findData(value)
        if index < 0:
            match = re.fullmatch(r'\s*([\d.]+)\s*:\s*([\d.]+)\s*', str(value))
            if match:
                self.width.setValue(float(match[1]))
                self.height.setValue(float(match[2]))
                index = self.preset.findData('custom')
            else:
                index = self.preset.findData('expr')
        self.preset.setCurrentIndex(index)
        self.preset.currentIndexChanged.connect(self._mode)
        self._mode()
        self._loading = False

    def _mode(self, *_):
        self.custom.setVisible(self.preset.currentData() == 'custom')
        self.expression.setVisible(self.preset.currentData() == 'expr')
        self._emit()

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit(self.value())

    def value(self):
        mode = self.preset.currentData()
        if mode == 'custom':
            return f'{self.width.value():g}:{self.height.value():g}'
        if mode == 'expr':
            return self.expression.text()
        return mode


def value_control(key, value, parent=None):
    """Return a QWidget and its value-change signal."""
    if isinstance(value, bool):
        control = QCheckBox('启用')
        control.setChecked(value)
        return control, control.toggled
    if key == 'ratio':
        control = RatioEditor(value, parent)
        return control, control.changed
    if key in ARRAYS:
        control = ArrayEditor(value, key, parent)
        return control, control.changed
    if key in {'path', 'buffer_path', 'logo', 'left_logo', 'right_logo', 'center_logo'}:
        control = ResourceEditor(value, parent)
        return control, control.changed
    if key in ENUMS:
        control = QComboBox()
        control.setEditable(True)
        control.addItems(ENUMS[key])
        control.setCurrentText(str(value))
        return control, control.currentTextChanged
    if key in ('font_path', 'bold_font_path'):
        control = QComboBox()
        control.addItem('处理器默认字体', None)
        for name in FONTS:
            control.addItem(name, name)
        if value and str(value) not in FONTS:
            control.addItem(str(value), str(value))
        control.setCurrentIndex(control.findData(value) if value else 0)
        control._font_enum = True
        control.setToolTip('内置字体；SpecialAlphabets 字体在上游包中缺失，会使用替代字体。')
        return control, control.currentIndexChanged
    if key in ('font_size', 'bold_font_size'):
        control = QComboBox()
        control.addItems(['1', '2', '3'])
        control.setCurrentText(str(value))
        control._integer_enum = True
        return control, control.currentTextChanged
    if 'color' in key or key == 'background':
        control = ColorEditor(value, parent)
        return control, control.changed
    if key in NUMERIC or isinstance(value, (int, float)):
        control = DimensionEditor(value, parent)
        return control, control.changed
    control = QLineEdit('' if value is None else str(value))
    control.setMinimumWidth(40)
    if key == 'text':
        control.setToolTip('支持 Jinja 模板，例如 {{exif.CameraModelName}}；保留原始表达式。')
    control._value_changed = lambda: control.text()
    return control, control.editingFinished


def signal_value(control, args):
    if getattr(control, '_font_enum', False):
        return control.currentData()
    value = args[0] if args else control.text()
    return int(value) if getattr(control, '_integer_enum', False) else value
