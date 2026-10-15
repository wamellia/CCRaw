"""Algorithm routing and UI completion contracts without neural runtimes."""

import threading
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QWidget

from ccraw import ai_dialog, enhance_dialog, model


@pytest.mark.parametrize(
    'kind,method', [('super', 0), ('super', 1), ('denoise', 0), ('denoise', 1), ('denoise', 2)]
)
@pytest.mark.parametrize('preview', [False, True])
def test_algorithm_dispatch_preserves_full_frame_and_preview_inputs(
    monkeypatch, kind, method, preview
):
    rgb = np.full((9, 11, 3), 0.4, np.float32)
    region = (2, 3, 8, 7) if preview else None
    before = rgb[3:7, 2:8].copy() if preview else None
    calls = []
    output = object()

    def algorithm(tag):
        def run(*args, **kwargs):
            calls.append((tag, args, kwargs))
            return output, tag

        return run

    monkeypatch.setattr(ai_dialog.restoration, 'super_resolution', algorithm('RRDB'))
    monkeypatch.setattr(ai_dialog.restoration, 'drunet_denoise', algorithm('DRUNet'))
    monkeypatch.setattr(ai_dialog.restoration, 'denoise', algorithm('NAFNet'))
    monkeypatch.setattr(ai_dialog.denoise, 'process', algorithm('FFDNet'))
    monkeypatch.setattr(ai_dialog.engine, 'super_resolve', algorithm('compact'))
    cancel = threading.Event()
    progress = lambda *args: None
    owner = SimpleNamespace(kind=kind, cancel_event=cancel)
    request = dict(method=method, scale=2, amount=70, cuda=False)
    result = ai_dialog.EnhancementDialog._run_algorithm(
        owner, rgb, request, progress, 0.01, region, before
    )
    selected = (
        ('RRDB', 'compact')[method] if kind == 'super' else ('DRUNet', 'NAFNet', 'FFDNet')[method]
    )
    assert result == (output, selected)
    tag, args, kwargs = calls[0]
    assert tag == selected
    crop_input = preview and (
        (kind == 'super' and method == 1) or (kind == 'denoise' and method == 2)
    )
    assert args[0] is (before if crop_input else rgb)
    assert args[-1] is cancel and args[-2] is progress
    if kind == 'super':
        assert args[1] == 2
        assert kwargs == ({'region': region} if method == 0 else {})
        if method == 1:
            assert args[2] == ':builtin:'
    else:
        assert args[1] == 70
        assert kwargs == (
            {'noise_reference': 0.01, 'region': region} if method < 2 else {'noise_reference': 0.01}
        )


@pytest.mark.parametrize('failure', [False, True])
def test_export_preview_restores_controls_after_success_and_failure(app, monkeypatch, failure):
    owner = QWidget()
    owner.edits = model.recipe()
    owner.backend_combo = QComboBox(owner)
    owner.backend_combo.addItem('CPU')
    pending = []
    owner.job = lambda *callbacks: pending.append(callbacks)
    dialog = enhance_dialog.ExportDialog(owner, 'unused.png', True)
    try:
        dialog.preview()
        save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
        assert not dialog.scale.isEnabled() and not save.isEnabled()
        work, success, failed = pending[0]
        if failure:
            failed('model failed')
            assert 'model failed' in dialog.status.text()
        else:
            patch = np.full((8, 8, 3), 0.3, np.float32)
            success((patch, patch, 'fake model'))
            assert not dialog.after.pixmap().isNull() and 'fake model' in dialog.status.text()
        assert dialog.scale.isEnabled() and dialog.method.isEnabled()
        assert dialog.preview_button.isEnabled() and save.isEnabled()
        assert not dialog.model_path.isEnabled() and not dialog.browse.isEnabled()
    finally:
        dialog.reject()
        owner.close()
