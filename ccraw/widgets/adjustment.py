from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSlider, QDoubleSpinBox


class AdjustSlider(QWidget):
    changed = Signal(float)
    committed = Signal()
    dragging = Signal(bool)

    def __init__(self, label, minimum=-100, maximum=100, step=1):
        super().__init__()
        self.factor = 1 / step
        self.default_value = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 4)
        outer.setSpacing(4)
        row = QHBoxLayout()
        self.title = QLabel(label)
        row.addWidget(self.title)
        row.addStretch()
        self.spin = QDoubleSpinBox()
        self.title.setBuddy(self.spin)
        self.spin.setAccessibleName(label)
        self.spin.setRange(minimum, maximum)
        self.spin.setDecimals(2 if step < 1 else 0)
        self.spin.setSingleStep(step)
        # Include the sign and the widest range endpoint, even at fractional DPI.
        metrics = self.spin.fontMetrics()
        samples = [self.spin.textFromValue(v) for v in (minimum, maximum, 0)]
        self.spin.setFixedWidth(max(72, max(metrics.horizontalAdvance(s) for s in samples) + 24))
        self.spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.spin.setKeyboardTracking(False)
        row.addWidget(self.spin)
        outer.addLayout(row)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setAccessibleName(label)
        if label in ('色温偏移', '色温  K'):
            self.slider.setProperty('parameter', 'temperature')
        elif label == '色调':
            self.slider.setProperty('parameter', 'tint')
        self.slider.setTracking(True)
        self.slider.setRange(round(minimum * self.factor), round(maximum * self.factor))
        outer.addWidget(self.slider)
        self.slider.valueChanged.connect(self._slide)
        self.spin.valueChanged.connect(self._spin)
        self.slider.sliderPressed.connect(lambda: self.dragging.emit(True))
        self.slider.sliderReleased.connect(lambda: self.dragging.emit(False))
        self.slider.sliderReleased.connect(self.committed)
        self.spin.editingFinished.connect(self.committed)
        self.slider.setToolTip('拖动调整；双击数值可直接输入')
        self.slider.mouseDoubleClickEvent = self.reset_value

    def reset_value(self, _):
        self.spin.setValue(max(self.spin.minimum(), self.default_value))
        self.committed.emit()

    def _slide(self, value):
        self.spin.blockSignals(True)
        self.spin.setValue(value / self.factor)
        self.spin.blockSignals(False)
        self.changed.emit(value / self.factor)

    def _spin(self, value):
        self.slider.blockSignals(True)
        self.slider.setValue(round(value * self.factor))
        self.slider.blockSignals(False)
        self.changed.emit(value)

    def setValue(self, value):
        self.slider.blockSignals(True)
        self.spin.blockSignals(True)
        self.slider.setValue(round(value * self.factor))
        self.spin.setValue(value)
        self.slider.blockSignals(False)
        self.spin.blockSignals(False)
