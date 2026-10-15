"""Render policy, CPU reference and failure behavior of spatial acceleration."""

import importlib.util
import types

import cv2
import numpy as np


def test_spatial_acceleration_api_exists():
    assert importlib.util.find_spec('ccraw.gpu_spatial') is not None


def test_standalone_gaussian_preserves_cpu_reference():
    from ccraw import gpu_spatial

    x = np.random.default_rng(1).uniform(-2, 8, (239, 319)).astype(np.float32)
    expected = cv2.GaussianBlur(x, (0, 0), 1.17)
    np.testing.assert_array_equal(gpu_spatial.gaussian(x, 1.17), expected)


def test_nested_cpu_backend_inherits_outer_policy_and_scope_is_released():
    from ccraw import gpu_spatial

    seen = []

    @gpu_spatial.routed
    def nested(source, edits, backend=None):
        seen.append(gpu_spatial.enabled())

    @gpu_spatial.routed
    def render(source, edits, backend=None):
        nested(source, edits, types.SimpleNamespace(accelerated=False))

    backend = types.SimpleNamespace(accelerated=True, gpu_pointwise=False)
    render(None, {}, backend)
    assert seen == [True]
    assert gpu_spatial.enabled() is False
    assert backend._spatial_ops == ()
    render(None, {}, types.SimpleNamespace(accelerated=False, gpu_pointwise=False))
    assert seen == [True, False]


def test_scope_is_released_after_render_error():
    import pytest
    from ccraw import gpu_spatial

    @gpu_spatial.routed
    def render(source, edits, backend=None):
        raise ValueError('cancelled')

    with pytest.raises(ValueError, match='cancelled'):
        render(None, {}, types.SimpleNamespace(accelerated=True, gpu_pointwise=False))
    assert not gpu_spatial.enabled()


def test_cpu_environment_overrides_accelerated_backend(monkeypatch):
    from ccraw import gpu_spatial

    monkeypatch.setenv('CCRAW_COMPUTE', 'cpu')

    @gpu_spatial.routed
    def render(source, edits, backend=None):
        return gpu_spatial.enabled()

    assert not render(None, {}, types.SimpleNamespace(accelerated=True, gpu_pointwise=False))


def test_small_frame_never_uploads_for_detail_chain(monkeypatch):
    from ccraw import gpu_spatial

    def forbidden(*args):
        raise AssertionError('small frame uploaded')

    monkeypatch.setattr(cv2, 'UMat', forbidden)

    @gpu_spatial.routed
    def render(source, edits, backend=None):
        return gpu_spatial.chain(source, edits)

    x = np.zeros((240, 320, 3), np.float32)
    assert (
        render(
            x,
            {'clarity': 25, 'texture': 25},
            types.SimpleNamespace(accelerated=True, gpu_pointwise=False),
        )
        is None
    )


def test_readonly_reference_backend_does_not_need_telemetry_attributes():
    from ccraw import gpu_spatial

    class Reference:
        __slots__ = ()
        accelerated = False
        gpu_pointwise = False

    @gpu_spatial.routed
    def render(source, edits, backend=None):
        return source

    assert render(42, {}, Reference()) == 42


def test_worker_policy_is_isolated_between_threads():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from ccraw import gpu_spatial

    barrier = threading.Barrier(2)

    @gpu_spatial.routed
    def render(source, edits, backend=None):
        barrier.wait(timeout=5)
        return gpu_spatial.enabled()

    with ThreadPoolExecutor(max_workers=2) as pool:
        auto = pool.submit(render, None, {}, types.SimpleNamespace(accelerated=True))
        cpu = pool.submit(render, None, {}, types.SimpleNamespace(accelerated=False))
        assert auto.result(timeout=10) is True
        assert cpu.result(timeout=10) is False
    assert not gpu_spatial.enabled()


def test_spatial_telemetry_preserves_experimental_cuda_stage():
    from ccraw import compute, gpu_spatial

    @gpu_spatial.routed
    def render(source, edits, backend=None):
        compute.state.report('CUDAExecutionProvider', 'Example CUDA GPU', 'CuPy tonal')
        return source

    backend = types.SimpleNamespace(accelerated=True, gpu_pointwise=False, xp=object())
    try:
        assert render(42, {}, backend) == 42
        assert compute.state.snapshot()[:3] == (
            'CUDAExecutionProvider',
            'Example CUDA GPU',
            'CuPy tonal',
        )
    finally:
        compute.state.report('CPUExecutionProvider')
