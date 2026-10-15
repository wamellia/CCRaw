"""Professional controls participate in continuous preview transactions."""

import copy
import time
import threading
import numpy as np
import pytest
from PySide6.QtCore import QTimer
from test_ui import wait_until
from test_live_preview import drag_for
from ccraw import engine


@pytest.mark.parametrize('accept', [True, False])
def test_composite_mask_dialog_previews_before_accept_and_rolls_back(window, monkeypatch, accept):
    from ccraw.ui.mask_components import MaskComponentsDialog

    w = window
    w.tabs.setCurrentIndex(4)
    w.add_mask('radial')
    w.local_controls['exposure'].spin.setValue(-1)
    wait_until(lambda: not w.jobs and not w.timer.isActive(), timeout=60)
    before = copy.deepcopy(w.edits)
    baseline = w.rendered.copy()
    seen = []
    original = MaskComponentsDialog.exec

    def run(dialog):
        dialog.operation.setCurrentIndex(1)
        dialog.add()
        dialog.controls['opacity'].setValue(25)
        started = time.monotonic()

        def check():
            if time.monotonic() - started > 5:
                print(
                    'dialog preview timeout',
                    w.work.describe(),
                    w.timer.isActive(),
                    w._preview_dragging,
                    w.generation,
                    len(w.jobs),
                    w.test_messages,
                    flush=True,
                )
                dialog.reject()
                return
            if w.render_running or w.timer.isActive():
                QTimer.singleShot(20, check)
                return
            seen.append(w.rendered.copy())
            (dialog.accept if accept else dialog.reject)()

        QTimer.singleShot(100, check)
        return original(dialog)

    monkeypatch.setattr(MaskComponentsDialog, 'exec', run)
    w.edit_mask_components()
    wait_until(lambda: not w.jobs and not w.timer.isActive())
    assert seen and np.max(np.abs(seen[0] - baseline)) > 0.001
    assert w._preview_dragging == 0
    if accept:
        assert len(w.edits['masks'][0]['components']) == 1
        w.undo(-1)
        assert w.edits == before
    else:
        assert w.edits == before
        np.testing.assert_array_equal(w.rendered, baseline)


@pytest.mark.parametrize('process', [1, 2])
@pytest.mark.parametrize(
    'kind', ['geometry', 'optics', 'detail', 'calibration', 'point', 'bw', 'profile', 'eye']
)
def test_professional_groups_update_during_held_drag(window, tmp_path, process, kind):
    w = window
    w.process_select.setCurrentIndex(process - 1)
    w.tabs.setCurrentIndex(9)
    panel = w.professional_editor
    if kind in ('geometry', 'optics', 'detail', 'calibration'):
        group, index, key = {
            'geometry': ('geometry', 1, 'horizontal'),
            'optics': ('optics', 0, 'vignetting'),
            'detail': ('detail', 3, 'amount'),
            'calibration': ('calibration', 2, 'red_hue'),
        }[kind]
        control = panel.controls[group, key]
        panel.tabs.setCurrentIndex(index)
    elif kind == 'point':
        panel.add_color_sample([0.5, 0.5, 0.6])
        panel.tabs.setCurrentIndex(5)
        control = panel.point_controls['hue']
    elif kind == 'bw':
        w.mono_check.setChecked(True)
        panel.tabs.setCurrentIndex(4)
        control = panel.bw_controls[0]
    elif kind == 'profile':
        path = tmp_path / 'look.cube'
        path.write_text('LUT_1D_SIZE 2\n0.1 0.0 0.0\n0.8 1.0 0.9\n')
        panel.import_profile(path)
        panel.tabs.setCurrentIndex(6)
        control = panel.profile_amount
    else:
        panel.add_red_eye([0.5, 0.5])
        panel.tabs.setCurrentIndex(7)
        control = panel.eye_amount
    wait_until(lambda: not w.jobs and not w.timer.isActive())
    before = copy.deepcopy(w.edits)
    frames, _, queues = drag_for(w, control.slider, range(10, 55), interval=14)
    assert len(frames) >= 3 and max(queues, default=0) <= 1
    assert w._preview_dragging == 0 and not w.test_messages
    expected = engine.process(
        w.source,
        w.edits,
        apply_crop=False,
        detail_scale=max(0.1, w.source.shape[1] / w.info['width']),
    )
    np.testing.assert_allclose(w.rendered, expected, atol=2e-6)
    w.undo(-1)
    assert w.edits == before


def test_released_frame_is_cancelled_without_an_error_dialog(window, monkeypatch):
    w = window
    wait_until(lambda: not w.jobs and not w.timer.isActive())
    entered, release = threading.Event(), threading.Event()
    original = engine.process
    checks = []

    def slow(*args, **kwargs):
        if not entered.is_set():
            checks.append(kwargs.get('check'))
            entered.set()
            release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, 'process', slow)
    slider = w.controls['exposure'].slider
    slider.setSliderDown(True)
    slider.setValue(35)
    wait_until(entered.is_set)
    slider.setSliderDown(False)
    release.set()
    wait_until(lambda: not w.jobs and not w.timer.isActive())
    assert checks[0] is not None and not w.test_messages
    expected = original(w.source, w.edits, apply_crop=False)
    np.testing.assert_allclose(w.rendered, expected, atol=2e-6)


def test_initial_open_preempts_remaining_kernel_warmup(app, tmp_path, monkeypatch):
    from ccraw import native_kernels
    from ccraw.app import MainWindow
    from PIL import Image

    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'user'))
    monkeypatch.setenv('CCRAW_CACHE_DIR', str(tmp_path / 'cache'))
    path = tmp_path / 'image.png'
    Image.new('RGB', (60, 40), (80, 90, 100)).save(path)
    order = []

    def warm(i):
        order.append('warm-' + str(i))
        time.sleep(0.02)

    monkeypatch.setattr(
        native_kernels, 'warm_steps', lambda: [lambda i=i: warm(i) for i in range(5)]
    )
    original = engine.load_image

    def load(*args, **kwargs):
        order.append('load')
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, 'load_image', load)
    w = MainWindow()
    try:
        w.open_path(str(path))
        wait_until(lambda: w.rendered is not None and not w.jobs, timeout=30)
        assert order.index('load') < order.index('warm-4')
    finally:
        w.saved_edits = copy.deepcopy(w.edits)
        w.close()
        w.deleteLater()


def test_straighten_drag_keeps_pixel_transform_off_gui(window, monkeypatch):
    w = window
    w.tabs.setCurrentIndex(5)
    wait_until(lambda: not w.jobs and not w.timer.isActive(), timeout=60)
    threads = []
    gui = threading.get_ident()
    original = engine.crop_rotate

    def tracked(*args, **kwargs):
        if w._preview_dragging:
            threads.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, 'crop_rotate', tracked)
    frames, _, _ = drag_for(w, w.straighten.slider, range(5, 60), interval=14)
    assert len(frames) >= 3 and threads and gui not in threads
    assert w.final_view.isChecked()
