from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QCheckBox,
    QDialogButtonBox,
    QScrollArea,
    QWidget,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
)

from ..domain.sync import SECTIONS, DEFAULT


GROUPS = (
    ('light', '光影', ('adjustments', 'curves')),
    (
        'color',
        '色彩',
        (
            'hsl',
            'grading',
            'monochrome',
            'professional.calibration',
            'professional.point_color',
            'professional.bw_mixer',
            'professional.profile',
            'white_balance',
            'develop',
        ),
    ),
    ('detail', '细节', ('professional.detail',)),
    (
        'composition',
        '构图',
        ('professional.optics', 'professional.lensfun', 'professional.geometry', 'crop'),
    ),
    ('local', '局部', ('masks', 'retouch', 'professional.red_eye')),
    ('effects', '效果', ('effects', 'watermark')),
)


class GroupSelectionCheck(QCheckBox):
    """A mixed child selection is displayed but never a user-toggle destination."""

    def nextCheckState(self):
        self.setCheckState(
            Qt.CheckState.Unchecked
            if self.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )


class SyncDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('同步调整')
        self.resize(600, 680)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        text = QLabel('选择需要同步的参数组')
        header.addWidget(text, 1)
        selection = QToolButton()
        selection.setText('选择')
        selection.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(selection)
        menu.addAction('恢复默认选择', self.restore_defaults)
        menu.addAction('选择全部', lambda: self._select(SECTIONS))
        menu.addAction('清空选择', lambda: self._select(()))
        selection.setMenu(menu)
        header.addWidget(selection)
        layout.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        rows = QVBoxLayout(page)
        self.controls = {}
        self.group_checks = {}
        self.group_keys = {key: parts for key, _, parts in GROUPS}
        for group, title, parts in GROUPS:
            box = QGroupBox()
            box.setProperty('settings_group', True)
            body = QVBoxLayout(box)
            check = GroupSelectionCheck(title)
            check.setTristate(True)
            check.setAccessibleName(title)
            check.setToolTip('勾选或取消本组全部参数')
            check.checkStateChanged.connect(lambda state, g=group: self._select_group(g, state))
            self.group_checks[group] = check
            body.addWidget(check)
            grid = QGridLayout()
            for index, key in enumerate(parts):
                control = QCheckBox(SECTIONS[key])
                control.setChecked(key in DEFAULT)
                control.toggled.connect(lambda _, g=group: self._refresh_group(g))
                self.controls[key] = control
                grid.addWidget(control, index // 2, index % 2)
            body.addLayout(grid)
            rows.addWidget(box)
        rows.addStretch()
        for group in self.group_checks:
            self._refresh_group(group)
        scroll.setWidget(page)
        layout.addWidget(scroll, 1)
        note = QLabel('默认保留每张照片的白平衡、裁切和局部调整。')
        note.setObjectName('subtle')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _select_group(self, group, state):
        if state == Qt.CheckState.PartiallyChecked:
            return
        for key in self.group_keys[group]:
            self.controls[key].setChecked(state == Qt.CheckState.Checked)
        self._refresh_group(group)

    def _refresh_group(self, group):
        selected = sum(self.controls[key].isChecked() for key in self.group_keys[group])
        state = (
            Qt.CheckState.Checked
            if selected == len(self.group_keys[group])
            else Qt.CheckState.PartiallyChecked
            if selected
            else Qt.CheckState.Unchecked
        )
        check = self.group_checks[group]
        check.blockSignals(True)
        check.setCheckState(state)
        check.blockSignals(False)

    def _select(self, keys):
        for key, widget in self.controls.items():
            widget.setChecked(key in keys)

    def restore_defaults(self):
        self._select(DEFAULT)

    def sections(self):
        return [key for key in SECTIONS if self.controls[key].isChecked()]
