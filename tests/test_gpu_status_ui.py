"""Device presentation must distinguish hardware, selected mode and active work."""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMainWindow, QLabel, QComboBox

from ccraw import compute, engine, i18n
from ccraw.app import MainWindow
from ccraw.ui.components import ComputeStatusBar
from ccraw.ui.panels import PanelsMixin


class FakeBackend:
    def __init__(self, mode='auto'):
        self.mode = mode
        self.name = 'CPU · NumPy / OpenCV'
        self.warning = ''
        self.route = 'CPU'

    def device_summary(self):
        if self.mode == 'cpu':
            return 'CPU · 手动 CPU 模式'
        return f'GPU：NVIDIA GeForce RTX 4060 Laptop GPU · 8.0 GiB\n当前路径：{self.route}'

    def device_tooltip(self):
        return self.device_summary() + '\n部分处理仍由 CPU 完成。'


@pytest.fixture(autouse=True)
def restore_compute_state():
    previous = compute.state.snapshot()
    yield
    compute.state.report(*previous)


@pytest.fixture
def startup_window(app, monkeypatch, tmp_path):
    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'user'))
    monkeypatch.setenv('CCRAW_CACHE_DIR', str(tmp_path / 'cache'))
    monkeypatch.setattr(engine, 'Backend', FakeBackend)
    w = MainWindow()
    w.show()
    app.processEvents()
    yield w
    w.close()
    w.deleteLater()
    app.processEvents()


def test_auto_device_visible_before_photo_and_fits_compact_inspector(startup_window, app):
    w = startup_window
    w.resize(1180, 780)
    app.processEvents()
    label = w.backend_label
    assert label.isVisible()
    assert label.wordWrap()
    assert 'RTX 4060' in label.text() and '8.0 GiB' in label.text()
    assert 'CPU' in label.text()
    # Existing screen clamping allows a 280 px inspector on small displays.
    assert label.width() >= 250
    assert label.height() >= label.heightForWidth(label.width())
    assert label.parentWidget().rect().contains(label.geometry())
    assert w.editor_reset_button.isVisible()


def test_gpu_summary_keeps_professional_tools_usable_in_short_window(startup_window, app):
    w = startup_window
    w.resize(1240, 670)
    app.processEvents()
    w.tabs.setCurrentIndex(9)
    app.processEvents()
    assert w.editor_categories.height() >= 175
    assert w.height() <= 670
    label = w.backend_label
    assert label.isVisible() and label.height() >= label.heightForWidth(label.width())
    assert label.parentWidget().rect().contains(label.geometry())


def test_auto_summary_refreshes_on_poll_and_mode_change(startup_window, app):
    w = startup_window
    w.backend.route = 'GPU+CPU · OpenCL'
    compute.state.report('OpenCL', 'NVIDIA GeForce RTX 4060 Laptop GPU', 'OpenCL · 空间处理')
    w.statusBar().refresh_mode()
    assert 'OpenCL' in w.backend_label.text()
    assert w.statusBar().mode_label.is_gpu
    assert w.statusBar().mode_label.text() == 'GPU+CPU'
    assert '部分处理仍由 CPU 完成' in w.statusBar().mode_label.toolTip()
    compute.state.report('CUDAExecutionProvider', 'AI GPU', 'CUDA AI inference')
    w.statusBar().refresh_mode()
    assert 'CUDAExecutionProvider' in w.statusBar().mode_label.toolTip()
    assert 'AI GPU' in w.statusBar().mode_label.toolTip()
    w.backend_combo.setCurrentIndex(1)
    app.processEvents()
    assert w.backend.mode == 'cpu'
    assert 'CPU' in w.backend_label.text()
    # Another worker's global GPU report must not override this window's mode.
    compute.state.report('DmlExecutionProvider', 'another GPU', 'DirectML')
    w.statusBar().refresh_mode()
    assert w.statusBar().mode_label.text() == 'CPU'
    assert not w.statusBar().mode_label.is_gpu
    w.backend_combo.setCurrentIndex(0)
    assert 'RTX 4060' in w.backend_label.text()


def test_footer_does_not_claim_azure_provider_is_gpu(app):
    bar = ComputeStatusBar()
    compute.state.report('AzureExecutionProvider', '', 'Azure')
    bar.refresh_mode()
    assert not bar.mode_label.is_gpu
    assert bar.mode_label.text() == 'CPU'
    bar.deleteLater()


def test_legacy_backend_fallback_and_localized_dynamic_summary(app):
    class Harness(PanelsMixin, QMainWindow):
        pass

    w = Harness()
    w.backend = SimpleNamespace(name='CPU · NumPy / OpenCV', warning='已回退 CPU')
    w.backend_combo = QComboBox()
    w.backend_combo.addItems(['自动加速', 'CPU'])
    w.backend_label = QLabel()
    w.setStatusBar(ComputeStatusBar(w))
    w.refresh_backend_status()
    assert 'CPU' in w.backend_label.text()
    assert '已回退 CPU' in w.backend_label.toolTip()
    assert (
        i18n.translate('当前路径：GPU+CPU · 专用显存', 'en')
        == 'Current route: GPU+CPU · Dedicated VRAM'
    )
    w.deleteLater()


def test_settings_performance_shows_owner_device_details(startup_window):
    from ccraw.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(startup_window)
    dialog.select_page('performance')
    dialog.show()
    assert 'RTX 4060' in dialog.device.text()
    assert '8.0 GiB' in dialog.device.text()
    assert '部分处理仍由 CPU 完成' in dialog.device.text()
    dialog.close()
    dialog.deleteLater()


def test_settings_without_photo_renderer_still_shows_detected_gpu(app, monkeypatch, tmp_path):
    from ccraw.ui.settings_dialog import SettingsDialog
    from ccraw import gpu_spatial

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'user'))
    monkeypatch.setenv('CCRAW_CACHE_DIR', str(tmp_path / 'cache'))
    monkeypatch.setattr(engine, 'Backend', FakeBackend)
    monkeypatch.setattr(
        gpu_spatial, 'initialize', lambda: (_ for _ in ()).throw(AssertionError('GUI OpenCL probe'))
    )
    owner = QMainWindow()
    dialog = SettingsDialog(owner)
    assert not hasattr(owner, 'backend')
    assert 'RTX 4060' in dialog.device.text() and '8.0 GiB' in dialog.device.text()
    assert dialog._device_backend.mode == 'auto'
    dialog.close()
    dialog.deleteLater()
    owner.deleteLater()


@pytest.mark.parametrize('result', ['accepted', 'failed'])
def test_preview_callbacks_refresh_device_route(startup_window, monkeypatch, result):
    import numpy as np

    w = startup_window
    w.source = np.zeros((12, 16, 3), np.float32)
    callbacks = {}
    monkeypatch.setattr(
        w, 'job', lambda work, done, error, **_: callbacks.update(done=done, error=error)
    )
    errors = []
    monkeypatch.setattr(w, 'error', errors.append)
    w.render()
    w.backend.route = 'GPU+CPU · OpenCL' if result == 'accepted' else 'CPU · OpenCL 回退'
    if result == 'accepted':
        callbacks['done'](
            SimpleNamespace(
                original=None,
                detail_only=True,
                histogram=np.zeros((3, 256)),
                detail=None,
                navigator=None,
                seconds=0,
            )
        )
        assert 'OpenCL' in w.backend_label.text()
    else:
        callbacks['error']('test failure')
        assert '回退' in w.backend_label.text()
        assert errors
    assert not w.render_running


def test_real_backend_device_messages_translate_to_english():
    import re

    messages = [
        'NVIDIA GeForce RTX 4060 Laptop GPU · 专用显存约 8 GB\n自动 · OpenCL + CPU',
        '驱动：32.0.15.9649\nONNX 后端：AzureExecutionProvider, CPUExecutionProvider',
        '滤镜设备：NVIDIA GeForce RTX 4060 Laptop GPU\nGPU 步骤：细节',
        '滤镜：按图像大小与操作选择；尚未执行 GPU 滤镜',
        '部分处理仍由 CPU 完成；小图与缓存命中保持 CPU 快速路径。',
        'ONNX 没有可用的 GPU 后端；像素操作使用 CPU，滤镜按成本选择设备。',
        'CPU · 手动模式\n手动 CPU 模式；滤镜和像素操作使用 CPU。',
        'DirectML（待验证）',
    ]
    for message in messages:
        assert not re.search('[\u4e00-\u9fff]', i18n.translate(message, 'en'))


def test_english_compute_controls_fit_compact_row(startup_window, app):
    w = startup_window
    w.resize(1180, 780)
    w._settings_binding.service.update({'appearance': {'language': 'en'}})
    app.processEvents()
    assert w.backend_combo.currentText() == 'Auto'
    assert w.editor_reset_button.text() == 'Reset group'
    button = w.editor_reset_button
    assert button.fontMetrics().horizontalAdvance(button.text()) + 12 <= button.width()
