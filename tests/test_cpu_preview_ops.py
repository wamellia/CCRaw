"""Numerical equivalence, cache dependencies and optional acceleration fallback."""

import numpy as np
import pytest
from ccraw import engine, model, cpu_ops, native_kernels


@pytest.mark.parametrize(
    'settings',
    [
        dict(),
        dict(exposure=1.2, contrast=60, temperature=40, tint=-25),
        dict(shadows=60, highlights=-40, blacks=-20, whites=30),
        dict(saturation=40, vibrance=20, dehaze=55),
    ],
)
def test_fused_pipeline_matches_numpy_float32_reference(settings, monkeypatch):
    source = np.random.default_rng(25).uniform(0, 1.3, (137, 193, 3)).astype(np.float32)
    edits = model.recipe()
    edits['adjustments'].update(settings)
    edits['curves']['RGB'] = [[0.0, 0.0], [0.3, 0.42], [0.7, 0.65], [1.0, 1.0]]
    edits['curve_mode'] = 'smooth'
    edits['grading']['midtones'] = [210, 40]
    mask = model.new_mask('radial', 1)
    mask['adjustments']['exposure'] = 0.5
    edits['masks'].append(mask)
    actual = engine.process(source, edits, engine.Backend('cpu'))
    monkeypatch.setattr(cpu_ops, 'ne', None)
    monkeypatch.setattr(native_kernels, 'enabled', False)
    expected = engine.process(source, edits, engine.Backend('cpu'))
    np.testing.assert_allclose(actual, expected, atol=8e-7, rtol=1e-6)
    actual8 = np.clip(actual * 255, 0, 255).astype(np.uint8).astype(int)
    expected8 = np.clip(expected * 255, 0, 255).astype(np.uint8).astype(int)
    assert np.max(np.abs(actual8 - expected8)) <= 1


def test_uniform_curve_keeps_the_same_4097_knots_and_float_rounding():
    source = np.random.default_rng(1).random((97, 113, 3), dtype=np.float32)
    axis = np.linspace(0, 1, 4097)
    table = np.sin(axis * np.pi / 2)
    expected = np.interp(source, axis, table).astype(np.float32)
    np.testing.assert_array_equal(engine.uniform_interp(source, table), expected)


def test_region_cache_reuses_upstream_stages_for_grading_and_invalidates_geometry():
    source = np.random.default_rng(9).random((220, 330, 3), dtype=np.float32)
    edits = model.recipe()
    edits['curves']['RGB'] = [[0.0, 0.0], [0.5, 0.6], [1.0, 1.0]]
    cache = engine.RenderCache()
    region = (33, 41, 250, 180)
    first = engine.process_region(source, edits, rect=region, cache=cache)
    misses = cache.misses
    edits['grading']['shadows'] = [210, 30]
    second = engine.process_region(source, edits, rect=region, cache=cache)
    assert cache.misses - misses == 1, 'a wheel change must reuse base, tonal and color stages'
    expected = engine.process(source, edits, apply_crop=False)[41:180, 33:250]
    np.testing.assert_array_equal(second, expected)
    assert np.max(np.abs(first - second)) > 0.001
    moved = (40, 50, 220, 170)
    actual = engine.process_region(source, edits, rect=moved, cache=cache)
    np.testing.assert_array_equal(
        actual, engine.process(source, edits, apply_crop=False)[50:170, 40:220]
    )


def test_dehaze_amount_reuses_its_atmospheric_basis_without_changing_the_filter():
    source = np.random.default_rng(13).random((101, 153, 3), dtype=np.float32)
    edits = model.recipe()
    cache = engine.RenderCache()
    for amount in [10, 35, 70, 25]:
        edits['adjustments']['dehaze'] = amount
        actual = engine.process(source, edits, cache=cache)
        np.testing.assert_array_equal(actual, engine.process(source, edits))
    assert 'atmosphere' in cache._stages and cache.hits >= 9


def test_effect_fields_reuse_geometry_and_keep_grain_deterministic():
    source = np.random.default_rng(42).random((113, 157, 3), dtype=np.float32)
    edits = model.recipe()
    cache = engine.RenderCache()
    edits['effects'].update(vignette=-40, grain=30, grain_size=20)
    edits['crop'] = [0.1, 0.2, 0.9, 0.85]
    for updates in [
        dict(),
        dict(vignette=40),
        dict(grain=70),
        dict(midpoint=20),
        dict(grain_size=60),
    ]:
        edits['effects'].update(updates)
        actual = engine.process(source, edits, cache=cache)
        np.testing.assert_array_equal(actual, engine.process(source, edits))
    edits['crop'] = [0.2, 0.1, 0.8, 0.9]
    np.testing.assert_array_equal(
        engine.process(source, edits, cache=cache), engine.process(source, edits)
    )


def test_native_hsl_matches_numpy_for_all_eight_colors(monkeypatch):
    rgb = np.random.default_rng(82).random((139, 197, 3), dtype=np.float32)
    values = np.random.default_rng(24).uniform(-100, 100, (8, 3)).tolist()
    actual = engine.apply_hsl(rgb, values)
    monkeypatch.setattr(native_kernels, 'enabled', False)
    expected = engine.apply_hsl(rgb, values)
    np.testing.assert_allclose(actual, expected, atol=5e-7, rtol=1e-6)


def test_native_sparse_curves_match_numpy_on_strided_channels_and_knots():
    x = np.random.default_rng(22).random((43, 79, 3), dtype=np.float32)[..., 1]
    axis = np.array([0.0, 0.1, 0.25, 0.61, 0.9, 1.0])
    table = np.array([0.0, 0.2, 0.3, 0.7, 0.8, 1.0])
    result = native_kernels.interp(x, table, axis)
    if result is None:
        pytest.skip('optional native kernel is unavailable')
    np.testing.assert_array_equal(result, np.interp(x, axis, table).astype(np.float32))


def test_shadow_controls_reuse_exact_luminance_weights_and_invalidate_gains():
    source = np.random.default_rng(49).uniform(0, 1.4, (117, 173, 3)).astype(np.float32)
    edits = model.recipe()
    cache = engine.RenderCache()
    edits['adjustments'].update(shadows=35, highlights=-30, blacks=-20, whites=15)
    engine.process(source, edits, cache=cache)
    gain = cache._stages['gain'][1]
    weights = cache._stages['weights'][1]
    for key, value in [('shadows', 70), ('highlights', 25), ('blacks', -55), ('whites', 60)]:
        edits['adjustments'][key] = value
        actual = engine.process(source, edits, cache=cache)
        np.testing.assert_array_equal(actual, engine.process(source, edits))
        assert cache._stages['gain'][1] is gain
        assert cache._stages['weights'][1] is weights
    edits['adjustments']['exposure'] = 1.2
    np.testing.assert_array_equal(
        engine.process(source, edits, cache=cache), engine.process(source, edits)
    )
    assert cache._stages['weights'][1] is not weights
    weights = cache._stages['weights'][1]
    edits['white_balance']['temperature'] = 6200
    np.testing.assert_array_equal(
        engine.process(source, edits, cache=cache), engine.process(source, edits)
    )
    assert cache._stages['weights'][1] is not weights


@pytest.mark.parametrize(
    'settings',
    [
        dict(shadows=100, highlights=-100, blacks=-100, whites=100),
        dict(shadows=-100, highlights=100, blacks=100, whites=-100, contrast=-100),
        dict(shadows=70, highlights=40, blacks=60, whites=100, exposure=2, contrast=100),
    ],
)
def test_native_range_and_transfer_pass_matches_float32_fallback(settings, monkeypatch):
    source = np.random.default_rng(51).uniform(-0.005, 1.5, (113, 157, 3)).astype(np.float32)
    a = model.adjustments()
    a.update(settings)
    actual = engine.Backend('cpu').tonal(source, a)
    monkeypatch.setattr(native_kernels, 'enabled', False)
    expected = engine.Backend('cpu').tonal(source, a)
    np.testing.assert_allclose(actual, expected, atol=5e-7, rtol=1e-6)
