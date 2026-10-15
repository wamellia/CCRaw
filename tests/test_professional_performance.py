"""Exact cached point edits and accelerated signed transfer functions."""

import copy

import numpy as np
import pytest

from ccraw import engine, model, native_kernels
from ccraw.imaging import professional as pro, colorspaces as cs


def test_native_point_adjustment_preserves_reference_precision():
    pytest.importorskip('numba')
    native_kernels.warm()
    assert callable(getattr(native_kernels, 'point_adjust', None))
    lab = np.random.default_rng(10).uniform(-0.4, 1.4, (42, 31, 3)).astype(np.float32)
    weight = np.random.default_rng(12).random(lab.shape[:2], dtype=np.float32)
    weight[::3] = 0
    original = lab.copy()
    for dh, ds, dl in ((72.0, 30.0, -40.0), (-90.0, -99.0, 85.0), (0.0, 0.0, 0.0)):
        expected = lab.copy()
        c = np.hypot(lab[..., 1], lab[..., 2]) * np.maximum(0, 1 + weight * ds / 100)
        h = np.arctan2(lab[..., 2], lab[..., 1]) + weight * np.deg2rad(dh * 0.6)
        expected[..., 0] += weight * dl / 100 * 0.2
        expected[..., 1], expected[..., 2] = c * np.cos(h), c * np.sin(h)
        actual = native_kernels.point_adjust(lab, weight, dh, ds, dl)
        np.testing.assert_allclose(actual[weight > 0], expected[weight > 0], atol=3e-7)
        np.testing.assert_array_equal(actual[weight == 0], original[weight == 0])
        np.testing.assert_array_equal(lab, original)


@pytest.mark.parametrize('process', [1, 2])
def test_drag_reuses_selection_and_invalidates_source_and_range(monkeypatch, process):
    source = np.random.default_rng(52).random((64, 96, 3), dtype=np.float32)
    edits = model.recipe()
    edits['process'] = process
    edits['professional']['point_color'] = [dict(rgb=[0.7, 0.3, 0.3], hue=15)]
    edits = model.validate(edits)
    calls = []
    original = pro._point_weight
    monkeypatch.setattr(pro, '_point_weight', lambda *a: (calls.append(1), original(*a))[1])
    cache = engine.RenderCache()
    first = engine.process(source, edits, cache=cache)
    edits['professional']['point_color'][0]['hue'] = 40
    second = engine.process(source, edits, cache=cache)
    assert len(calls) == 1, 'drag must reuse immutable selection coverage'
    assert not np.array_equal(first, second)
    expected = engine.process(source, edits)
    np.testing.assert_allclose(second, expected, atol=2e-6)
    count = len(calls)
    edits['professional']['point_color'][0]['hue_range'] = 75
    third = engine.process(source, edits, cache=cache)
    assert len(calls) == count + 1
    np.testing.assert_allclose(third, engine.process(source, edits), atol=2e-6)
    count = len(calls)
    edits['adjustments']['exposure'] = 0.7
    fourth = engine.process(source, edits, cache=cache)
    assert len(calls) == count + 1, 'upstream exposure changes the selection source'
    np.testing.assert_allclose(fourth, engine.process(source, edits), atol=2e-6)
    count = len(calls)
    edits['professional']['calibration']['red_hue'] = 30
    engine.process(source, edits, cache=cache)
    assert len(calls) == count + 1, 'calibration changes the point selection source'
    assert (
        cache._stored() + getattr(cache, '_wide_cache', engine.RenderCache(0))._stored()
        <= cache.max_bytes
    )


@pytest.mark.parametrize('space', ['sRGB', 'Rec.2020'])
def test_sparse_point_edits_match_dense_reference_and_leave_unselected_exact(space, monkeypatch):
    source = (
        np.random.default_rng(13)
        .uniform(-0.03 if space != 'sRGB' else 0, 1.4, (32, 41, 3))
        .astype(np.float32)
    )
    points = [
        dict(rgb=[0.7, 0.3, 0.3], hue=40, saturation=20, luminance=10),
        dict(rgb=[0.7, 0.3, 0.3], hue=-15, saturation=-30, luminance=-8),
    ]
    lab = pro._lab(source, space)
    changed = lab.copy()
    touched = np.zeros(source.shape[:2], bool)
    for point in points:
        weight = pro._point_weight(lab, point)
        c = np.hypot(changed[..., 1], changed[..., 2])
        h = np.arctan2(changed[..., 2], changed[..., 1])
        c *= np.maximum(0, 1 + weight * point['saturation'] / 100)
        h = h + weight * np.deg2rad(point['hue'] * 0.6)
        changed[..., 0] += weight * point['luminance'] / 100 * 0.2
        changed[..., 1], changed[..., 2] = c * np.cos(h), c * np.sin(h)
        touched |= weight > 0
    expected = np.where(touched[..., None], pro._rgb(changed, space), source)
    actual = pro.apply_color(source, dict(point_color=copy.deepcopy(points)), space)
    np.testing.assert_allclose(actual, expected, atol=2e-6)
    np.testing.assert_array_equal(actual[~touched], source[~touched])
    with monkeypatch.context() as m:
        m.setattr(native_kernels, 'enabled', False)
        fallback = pro.apply_color(source, dict(point_color=copy.deepcopy(points)), space)
        np.testing.assert_allclose(fallback, expected, atol=2e-6)
        np.testing.assert_array_equal(fallback[~touched], source[~touched])


def test_native_signed_transfers_match_float64_reference_and_fallback(monkeypatch):
    pytest.importorskip('numba')
    native_kernels.warm()
    assert native_kernels.enabled
    assert callable(getattr(native_kernels, 'transfer', None))
    rgb = np.random.default_rng(2).uniform(-0.2, 3, (29, 47, 3)).astype(np.float32)
    for space in cs.SPACES:
        decoded, encoded = cs.decode(rgb, space), cs.encode(rgb, space)
        with monkeypatch.context() as m:
            m.setattr(native_kernels, 'enabled', False)
            np.testing.assert_allclose(decoded, cs.decode(rgb, space), rtol=2e-7, atol=1e-7)
            np.testing.assert_allclose(encoded, cs.encode(rgb, space), rtol=2e-7, atol=1e-7)
        assert decoded.dtype == encoded.dtype == np.float32
        assert np.any(decoded < 0) and np.any(decoded > 1)


def test_native_matrix_matches_reference_with_signed_wide_pixels(monkeypatch):
    pytest.importorskip('numba')
    native_kernels.warm()
    assert callable(getattr(native_kernels, 'matrix', None))
    rgb = np.random.default_rng(11).uniform(-0.5, 4, (31, 23, 3)).astype(np.float32)
    for source in cs.SPACES:
        for target in cs.SPACES:
            transformed = cs.convert_linear(rgb, source, target)
            with monkeypatch.context() as m:
                m.setattr(native_kernels, 'enabled', False)
                np.testing.assert_allclose(
                    transformed, cs.convert_linear(rgb, source, target), rtol=2e-7, atol=1e-7
                )
