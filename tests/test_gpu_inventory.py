"""Hardware detection, selected mode and actual execution are different facts."""

import sys
from types import SimpleNamespace

from ccraw import compute, engine, gpu_spatial


def test_auto_cuda_provider_is_selected_without_requiring_cupy(monkeypatch):
    monkeypatch.setenv('CCRAW_COMPUTE', 'auto')
    monkeypatch.delenv('CCRAW_EXPERIMENTAL_CUPY', raising=False)
    monkeypatch.setattr(
        compute,
        'preferred_adapter',
        lambda: (0, 'Example GPU', 8 * 2**30, compute.NVIDIA, '1.2.3.4'),
    )
    monkeypatch.setitem(
        sys.modules,
        'onnxruntime',
        SimpleNamespace(
            get_available_providers=lambda: ['CUDAExecutionProvider', 'CPUExecutionProvider']
        ),
    )
    backend = engine.Backend('auto')
    assert backend.gpu_pointwise
    assert backend.gpu_label == 'CUDA'
    assert 'Example GPU' in backend.device_summary()
    assert '8 GB' in backend.device_summary()
    assert '待验证' in backend.device_summary()


def test_summary_does_not_initialize_opencl_from_gui(monkeypatch):
    monkeypatch.setattr(
        gpu_spatial, 'initialize', lambda: (_ for _ in ()).throw(AssertionError('GUI probe'))
    )
    backend = engine.Backend('auto')
    assert isinstance(backend.device_summary(), str)
    assert isinstance(backend.device_tooltip(), str)


def test_explicit_cpu_and_environment_cpu_never_enable_spatial(monkeypatch):
    assert not engine.Backend('cpu').accelerated
    monkeypatch.setenv('CCRAW_COMPUTE', 'cpu')
    backend = engine.Backend('auto')
    assert backend.mode == 'cpu'
    assert not backend.accelerated
    assert backend.device_summary() == 'CPU · 手动模式'
