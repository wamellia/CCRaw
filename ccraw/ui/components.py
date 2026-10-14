from __future__ import annotations
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QStatusBar, QSizePolicy
from .. import compute, i18n


class ToolPage(QWidget):
    """Non-scrolling page for tools whose own subpages already scroll.

    Keep the same content accessor as QScrollArea for shared workflow states.
    """

    def __init__(self):
        super().__init__()
        self._content = QWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._content)

    def widget(self):
        return self._content


class ModeBadge(QLabel):
    """Compact active processing backend indicator."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_gpu = False


class ComputeStatusBar(QStatusBar):
    """Three balanced cells keep the device badge at the real window center."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizeGripEnabled(False)
        self._message = ''
        panel = QWidget(self)
        row = QHBoxLayout(panel)
        row.setContentsMargins(9, 1, 9, 1)
        row.setSpacing(5)
        self.message_label = QLabel()
        self.message_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.mode_label = ModeBadge()
        self.mode_label.setAlignment(Qt.AlignCenter)
        self.mode_label.setFixedWidth(82)
        self.state_label = QLabel()
        self.state_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.state_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.message_label, 1)
        row.addWidget(self.mode_label)
        row.addWidget(self.state_label, 1)
        self.addPermanentWidget(panel, 1)
        self.mode_label.setText('CPU')

    def showMessage(self, message, timeout=0):
        self._message = str(message)
        self.message_label.setToolTip(self._message)
        self._elide()
        if timeout:
            QTimer.singleShot(
                timeout, lambda: self.clearMessage() if self._message == message else None
            )

    def currentMessage(self):
        return self._message

    def clearMessage(self):
        self.showMessage('')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._elide)

    def _elide(self):
        self.message_label.setText(
            self.message_label.fontMetrics().elidedText(
                self._message, Qt.ElideRight, max(20, self.message_label.width() - 8)
            )
        )

    def refresh_mode(self):
        provider, device, detail, warning = compute.state.snapshot()
        window = self.parentWidget()
        backend = getattr(window, 'backend', None)
        combo = getattr(window, 'backend_combo', None)
        manual_cpu = getattr(backend, 'mode', None) == 'cpu' or (
            combo is not None and combo.currentIndex() == 1
        )
        gpu = not manual_cpu and provider in {
            'DmlExecutionProvider',
            'CUDAExecutionProvider',
            'TensorrtExecutionProvider',
            'NvTensorRtRtxExecutionProvider',
            'OpenCL',
            'OpenCLExecutionProvider',
            'OpenCVOpenCL',
        }
        self.mode_label.is_gpu = gpu
        self.mode_label.setText('GPU+CPU' if gpu else 'CPU')
        tooltip = getattr(backend, 'device_tooltip', None)
        if callable(tooltip):
            text = tooltip()
            if not manual_cpu:
                recent = ' · '.join(value for value in (provider, device, detail) if value)
                text += '\n最近计算步骤：' + recent
                if warning:
                    text += '\n' + warning
        elif manual_cpu:
            text = '手动 CPU 模式'
        else:
            text = ('当前设备：' + device + '\n' if gpu else '') + detail
            if warning:
                text += '\n' + warning
            text += '\n最近计算步骤；部分处理仍由 CPU 完成。'
        self.mode_label.setToolTip(i18n.translate(text))
        self.mode_label.update()
        refresh = getattr(window, 'refresh_backend_status', None)
        if callable(refresh):
            refresh(refresh_badge=False)


def note(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setObjectName('subtle')
    return label


def heading(text):
    label = QLabel(text)
    label.setObjectName('section')
    return label
