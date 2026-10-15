"""Opt in with CCRAW_TEST_OPENCL=1; actual device parity, caches and fallback."""

import os
import types

import cv2
import numpy as np
import pytest

from ccraw import engine, gpu_spatial, model
from ccraw.imaging import spatial, wide_color

pytestmark = pytest.mark.skipif(
    os.environ.get('CCRAW_TEST_OPENCL') != '1',
    reason='native OpenCL hardware opt-in',
)


def backend():
    if not gpu_spatial.initialize()['available']:
        pytest.skip('No OpenCL GPU available')
    return types.SimpleNamespace(accelerated=True, gpu_pointwise=False)


@pytest.mark.parametrize('wide', [False, True])
@pytest.mark.parametrize('values', [(25, 30, 25), (-30, 25, -25)])
def test_detail_hook_matches_negative_hdr_reference_and_reports_gpu(wide, values):
    image = np.random.default_rng(552).uniform(-0.2, 3, (961, 1281, 3)).astype(np.float32)
    before = image.copy()
    a = dict(zip(('clarity', 'texture', 'sharpness'), values))
    module = wide_color if wide else spatial
    expected = module.spatial_details(image, a)

    @gpu_spatial.routed
    def render(source, edits, backend=None):
        return module.spatial_details(source, edits)

    active = backend()
    actual = render(image, a, active)
    assert active._spatial_ops
    np.testing.assert_allclose(actual, expected, rtol=2e-5, atol=2e-6)
    np.testing.assert_array_equal(image, before)
    if wide:
        assert actual.min() < 0 and actual.max() > 1


@pytest.mark.parametrize('wide', [False, True])
def test_cold_frame_primes_bands_and_slider_reuses_cpu_cache(wide):
    image = np.random.default_rng(331).uniform(-0.1, 1.5, (960, 1280, 3)).astype(np.float32)
    a = dict(clarity=25, texture=30, sharpness=25)
    module = wide_color if wide else spatial
    cache = engine.RenderCache()
    cache.bind(image, 1)

    @gpu_spatial.routed
    def render(source, edits, backend=None):
        return module.spatial_details(source, edits, cache=cache, basis_key='hardware')

    active = backend()
    first = render(image, a, active)
    assert active._spatial_ops
    assert all(key in cache._stages for key in ('clarity-band', 'texture-band', 'sharpen-band'))
    np.testing.assert_allclose(first, module.spatial_details(image, a), rtol=2e-5, atol=2e-6)
    changed = dict(a, sharpness=40)
    hits = cache.hits
    second = render(image, changed, active)
    assert not active._spatial_ops  # Cached bands avoid a complete upload/recompute.
    assert cache.hits >= hits + 3
    np.testing.assert_allclose(second, module.spatial_details(image, changed), rtol=2e-5, atol=2e-6)


def test_explicit_large_gaussian_preserves_odd_signed_plane():
    image = np.random.default_rng(11).uniform(-2, 8, (1537, 2049)).astype(np.float32)

    @gpu_spatial.routed
    def render(source, edits, backend=None):
        return gpu_spatial.gaussian(source, 17)

    active = backend()
    actual = render(image, {}, active)
    assert active._spatial_ops == ('Gaussian',)
    np.testing.assert_allclose(actual, cv2.GaussianBlur(image, (0, 0), 17), atol=4e-6, rtol=2e-5)


def test_native_failure_keeps_cpu_result_and_does_not_report_gpu(monkeypatch):
    image = np.ones((960, 1280, 3), np.float32)
    active = backend()
    a = dict(clarity=25, texture=25)
    expected = spatial.spatial_details(image, a)

    def failed_upload(*args):
        raise cv2.error('simulated device lost')

    @gpu_spatial.routed
    def render(source, edits, backend=None):
        return spatial.spatial_details(source, edits)

    monkeypatch.setattr(cv2, 'UMat', failed_upload)
    actual = render(image, a, active)
    np.testing.assert_array_equal(actual, expected)
    assert not active._spatial_ops
    assert 'device lost' in active._spatial_warning


@pytest.mark.parametrize('process', [1, 2])
def test_full_renderer_and_padded_region_preserve_signed_hdr_math(process):
    backend()  # Check hardware availability before running the actual pipeline.
    image = np.random.default_rng(4680).uniform(-0.05, 1.5, (961, 1281, 3)).astype(np.float32)
    recipe = model.recipe()
    recipe['process'] = process
    recipe['adjustments'].update(clarity=25, texture=25, sharpness=25)
    cpu = engine.Backend('cpu')
    auto = engine.Backend('auto')
    expected = engine.process(image, recipe, cpu, apply_crop=False)
    full = engine.process(image, recipe, auto, apply_crop=False)
    assert auto._spatial_ops
    np.testing.assert_allclose(full, expected, rtol=3e-5, atol=3e-5)
    rect = (50, 30, 1230, 930)
    part = engine.process_region(image, recipe, auto, rect=rect)
    assert auto._spatial_ops
    np.testing.assert_allclose(part, full[30:930, 50:1230], rtol=3e-5, atol=3e-5)
