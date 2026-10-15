"""Ordered mask set operations; source masks are copied so edits never alias."""

import copy
from PySide6.QtCore import Signal

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QComboBox,
    QListWidget,
    QPushButton,
    QDialogButtonBox,
    QDoubleSpinBox,
    QCheckBox,
    QLabel,
    QGroupBox,
    QFormLayout,
    QScrollArea,
    QWidget,
)


class MaskComponentsDialog(QDialog):
    changed = Signal(object)

    def __init__(self, mask, masks, parent=None):
        super().__init__(parent)
        self.setWindowTitle('组合蒙版')
        self.resize(480, 640)
        self.parts = copy.deepcopy(mask.get('components', []))
        self.sources = copy.deepcopy(masks)
        layout = QVBoxLayout(self)
        self.sources_combo = QComboBox()
        self.sources_combo.addItems([m['name'] for m in masks])
        self.operation = QComboBox()
        self.operation.addItems(['添加', '减去', '交集'])
        row = QHBoxLayout()
        row.addWidget(self.sources_combo, 1)
        row.addWidget(self.operation)
        self.add_button = QPushButton('添加组件')
        self.add_button.setObjectName('primary')
        self.add_button.clicked.connect(self.add)
        row.addWidget(self.add_button)
        layout.addLayout(row)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.refresh)
        self.list.setMinimumHeight(100)
        layout.addWidget(self.list, 1)
        self.empty_state = QLabel('选择已有蒙版和运算方式，添加第一个组件。')
        self.empty_state.setObjectName('subtle')
        self.empty_state.setWordWrap(True)
        layout.addWidget(self.empty_state)
        self.properties = QWidget()
        property_layout = QVBoxLayout(self.properties)
        property_layout.setContentsMargins(0, 0, 0, 0)
        appearance = QGroupBox('组件外观')
        appearance.setProperty('settings_group', True)
        appearance_form = QFormLayout(appearance)
        self.geometry_group = QGroupBox('位置与形状')
        self.geometry_group.setProperty('settings_group', True)
        geometry_form = QFormLayout(self.geometry_group)
        self.controls = {}
        for key, label, low, high in [
            ('opacity', '组件不透明度', 0, 100),
            ('feather', '组件羽化', 0, 100),
            ('sx', '起点 X', 0, 1),
            ('sy', '起点 Y', 0, 1),
            ('ex', '终点 X', 0, 1),
            ('ey', '终点 Y', 0, 1),
        ]:
            spin = QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setDecimals(3 if high == 1 else 1)
            spin.setSingleStep(0.01 if high == 1 else 1)
            spin.valueChanged.connect(lambda value, k=key: self.edit(k, value))
            self.controls[key] = spin
            (geometry_form if len(key) == 2 else appearance_form).addRow(label, spin)
        self.invert = QCheckBox('反选组件')
        self.invert.toggled.connect(lambda v: self.edit('invert', v))
        appearance_form.addRow(self.invert)
        property_layout.addWidget(appearance)
        property_layout.addWidget(self.geometry_group)
        property_scroll = QScrollArea()
        property_scroll.setWidgetResizable(True)
        property_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        property_scroll.setWidget(self.properties)
        layout.addWidget(property_scroll, 1)
        row = QHBoxLayout()
        for name, label, callback in [
            ('up_button', '上移', lambda: self.move(-1)),
            ('down_button', '下移', lambda: self.move(1)),
            ('remove_button', '删除', self.remove),
        ]:
            b = QPushButton(label)
            b.clicked.connect(callback)
            row.addWidget(b)
            setattr(self, name, b)
        layout.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.rebuild()

    def rebuild(self, index=0):
        self.list.clear()
        self.list.addItems(
            [
                f'{dict(add="添加", subtract="减去", intersect="交集")[p["op"]]} · {p["mask"]["name"]}'
                for p in self.parts
            ]
        )
        self.list.setCurrentRow(min(index, len(self.parts) - 1))
        self.refresh()

    def add(self):
        index = self.sources_combo.currentIndex()
        if index >= 0 and len(self.parts) < 64:
            m = copy.deepcopy(self.sources[index])
            m.pop('components', None)
            self.parts.append(
                dict(op=['add', 'subtract', 'intersect'][self.operation.currentIndex()], mask=m)
            )
            self.rebuild(len(self.parts) - 1)
            self.changed.emit(copy.deepcopy(self.parts))

    def refresh(self, *_):
        index = self.list.currentRow()
        m = self.parts[index]['mask'] if index >= 0 else None
        self.empty_state.setVisible(not self.parts)
        self.properties.setVisible(m is not None)
        self.geometry_group.setVisible(bool(m and m['kind'] in ('linear', 'radial')))
        self.add_button.setEnabled(bool(self.sources) and len(self.parts) < 64)
        self.remove_button.setEnabled(index >= 0)
        self.up_button.setEnabled(index > 0)
        self.down_button.setEnabled(0 <= index < len(self.parts) - 1)
        for key, spin in self.controls.items():
            spin.blockSignals(True)
            spin.setEnabled(m is not None)
            if m:
                spin.setValue(
                    m['start' if key[0] == 's' else 'end'][int(key[1] == 'y')]
                    if len(key) == 2
                    else m[key]
                )
            spin.blockSignals(False)
        self.invert.blockSignals(True)
        self.invert.setEnabled(m is not None)
        self.invert.setChecked(bool(m and m['invert']))
        self.invert.blockSignals(False)

    def edit(self, key, value):
        index = self.list.currentRow()
        if index < 0:
            return
        m = self.parts[index]['mask']
        if key in ('sx', 'sy', 'ex', 'ey'):
            m['start' if key[0] == 's' else 'end'][int(key[1] == 'y')] = value
        else:
            m[key] = value
        self.changed.emit(copy.deepcopy(self.parts))

    def remove(self):
        index = self.list.currentRow()
        if index >= 0:
            self.parts.pop(index)
            self.rebuild(index)
            self.changed.emit(copy.deepcopy(self.parts))

    def move(self, delta):
        i = self.list.currentRow()
        if 0 <= i + delta < len(self.parts):
            self.parts[i], self.parts[i + delta] = self.parts[i + delta], self.parts[i]
            self.rebuild(i + delta)
            self.changed.emit(copy.deepcopy(self.parts))
