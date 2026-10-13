from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QCheckBox,
    QDialogButtonBox,
    QScrollArea,
    QWidget,
)

from ..domain.sync import SECTIONS, DEFAULT


class SyncDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('同步调整')
        self.resize(360, 540)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        rows = QVBoxLayout(page)
        self.controls = {}
        for key, label in SECTIONS.items():
            control = QCheckBox(label)
            control.setChecked(key in DEFAULT)
            self.controls[key] = control
            rows.addWidget(control)
        scroll.setWidget(page)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def sections(self):
        return [key for key, control in self.controls.items() if control.isChecked()]
