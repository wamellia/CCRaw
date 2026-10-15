"""Precision, dependency isolation and bounded preview decoding regressions."""

import numpy as np
import pytest
from ccraw import engine, model, native_kernels
from ccraw.imaging import professional as pro, wide_color as wc


@pytest.mark.parametrize('process', [1, 2])
def test_geometry_drag_reuses_color_and_detail(process, monkeypatch):
    from ccraw.imaging import color

    src = np.random.default_rng(31).random((79, 121, 3), dtype=np.float32)
    recipe = model.recipe()
    recipe['process'] = process
    recipe['adjustments'].update(saturation=10, texture=15)
    recipe['professional']['detail']['amount'] = 20
    cache = engine.RenderCache()
    ops = wc if process == 2 else color
    counts = []
    original = ops.color_base

    def tracked(*args, **kwargs):
        counts.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(ops, 'color_base', tracked)
    # Legacy engine exposes the stable public color hooks.
    if process == 1:
        monkeypatch.setattr(engine, 'color_base', tracked)
    first = engine.process(src, recipe, cache=cache)
    for value in [4, 12, -15]:
        recipe['professional']['geometry']['horizontal'] = value
        actual = engine.process(src, recipe, cache=cache)
        assert len(counts) == 1, 'perspective must not invalidate unrelated color stages'
        np.testing.assert_allclose(actual, engine.process(src, recipe), atol=2e-6)
        counts.pop()  # remove the uncached reference call
    assert np.max(np.abs(actual - first)) > 0.01


@pytest.mark.parametrize('space', ['sRGB', 'Rec.2020'])
def test_oklab_keeps_reference_rounding_and_signed_gamut(space, monkeypatch):
    src = np.random.default_rng(6).uniform(-0.1, 1.5, (57, 91, 3)).astype(np.float32)
    lab = pro._lab(src, space)
    actual = pro._rgb(lab, space)
    monkeypatch.setattr(native_kernels, 'enabled', False)
    expected_lab = pro._lab(src, space)
    expected = pro._rgb(lab, space)
    np.testing.assert_allclose(lab, expected_lab, atol=3e-6)
    np.testing.assert_allclose(actual, expected, atol=3e-6)
    if space == 'Rec.2020':
        assert actual.min() < 0 and actual.max() > 1


@pytest.mark.parametrize('mode', ['linear', 'smooth'])
def test_wide_curve_matches_exact_analytic_reference_including_hdr(mode, monkeypatch):
    from ccraw import curves

    points = [[0.0, 0.0], [0.15, 0.21], [0.42, 0.38], [0.8, 0.88], [1.0, 1.0]]
    x = np.random.default_rng(7).uniform(-0.3, 1.8, (67, 101, 3)).astype(np.float32)
    xp, yp = np.asarray(points, np.float64).T
    inside = curves.evaluate(points, x, mode)
    expected = np.where(
        x < xp[0],
        yp[0] + (x - xp[0]) * (yp[1] - yp[0]) / (xp[1] - xp[0]),
        np.where(x > xp[-1], yp[-1] + (x - xp[-1]) * (yp[-1] - yp[-2]) / (xp[-1] - xp[-2]), inside),
    ).astype(np.float32)
    actual = wc._curve(x, points, mode)
    np.testing.assert_allclose(actual, expected, atol=2e-7)
    # The fallback must keep the same original spline, not a smaller lookup table.
    monkeypatch.setattr(native_kernels, 'enabled', False)
    np.testing.assert_array_equal(wc._curve(x, points, mode), expected)


@pytest.mark.parametrize('wide', [False, True])
def test_native_saturation_matches_reference_without_mutating_input(wide, monkeypatch):
    from ccraw.imaging.color import saturation

    src = (
        np.random.default_rng(36)
        .uniform(-0.1 if wide else 0, 1.4 if wide else 1, (61, 87, 3))
        .astype(np.float32)
    )
    original = src.copy()
    a = dict(saturation=31, vibrance=-23)
    run = (
        (lambda: wc._basic_color(src, dict(adjustments=a))) if wide else lambda: saturation(src, a)
    )
    actual = run()
    monkeypatch.setattr(native_kernels, 'enabled', False)
    np.testing.assert_allclose(actual, run(), atol=5e-7)
    np.testing.assert_array_equal(src, original)


@pytest.mark.parametrize('process', [1, 2])
def test_professional_amount_drag_reuses_oklab_source(process, monkeypatch):
    src = np.random.default_rng(62).random((69, 111, 3), dtype=np.float32)
    recipe = model.recipe()
    recipe['process'] = process
    recipe['professional']['detail']['amount'] = 20
    cache = engine.RenderCache()
    seen = []
    original = pro._lab

    def track(*args, **kwargs):
        seen.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(pro, '_lab', track)
    engine.process(src, recipe, cache=cache)
    for amount in [35, 65, 12]:
        recipe['professional']['detail']['amount'] = amount
        actual = engine.process(src, recipe, cache=cache)
        assert len(seen) == 1
        np.testing.assert_allclose(actual, engine.process(src, recipe), atol=2e-6)
        seen.pop()
    recipe['adjustments']['exposure'] = 0.5
    engine.process(src, recipe, cache=cache)
    assert len(seen) == 2
    assert cache._stored() <= cache.max_bytes


def test_geometry_without_ca_builds_one_map_for_three_channels(monkeypatch):
    src = np.random.default_rng(9).random((47, 65, 3), dtype=np.float32)
    recipe = model.recipe()
    recipe['professional']['geometry']['vertical'] = 15
    count = []
    original = pro.remap_map

    def track(*args, **kwargs):
        count.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(pro, 'remap_map', track)
    actual = pro.apply_geometry(src, recipe['professional'])
    assert len(count) == 1
    mx, my = original(src.shape, recipe['professional'])
    import cv2

    np.testing.assert_array_equal(
        actual, cv2.remap(src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    )


def test_preview_decode_cache_is_bounded_and_invalidates_changed_files(tmp_path):
    from ccraw.live_preview import DecodedPreviews
    from PIL import Image

    cache = DecodedPreviews(max_bytes=40000)
    path = tmp_path / 'photo.png'
    Image.new('RGB', (100, 60), (90, 40, 10)).save(path)
    first, info = cache.load(path, 64)
    again, second_info = cache.load(path, 64)
    assert again is first
    second_info['name'] = 'changed'
    assert info['name'] == 'photo.png'
    Image.new('RGB', (103, 62), (10, 40, 90)).save(path)
    updated, _ = cache.load(path, 64)
    assert updated is not first and float(updated[..., 2].mean()) > float(first[..., 2].mean())
    assert cache.bytes <= cache.max_bytes
    cache.clear()
    assert cache.bytes == 0


def test_large_tiff_preview_decodes_bounded_linear_regions(tmp_path, monkeypatch):
    import tifffile, cv2
    from ccraw.imaging import io

    rgb = np.random.default_rng(43).integers(0, 65536, (777, 1201, 3), dtype=np.uint16)
    path = tmp_path / 'large.tif'
    tifffile.imwrite(path, rgb, photometric='rgb')
    full, _ = io.load_image(path, 0)
    sizes = []
    original = io._decode_rgb

    def tracked(encoded, *args, **kwargs):
        sizes.append(encoded.shape[0] * encoded.shape[1])
        return original(encoded, *args, **kwargs)

    monkeypatch.setattr(io, '_decode_rgb', tracked)
    preview, info = io.load_image(path, 160)
    expected = cv2.resize(full, (160, 104), interpolation=cv2.INTER_AREA)
    np.testing.assert_allclose(preview, expected, atol=2e-6)
    assert max(sizes) < rgb.shape[0] * rgb.shape[1] // 2
    assert info['source_bit_depth'] == 16 and (info['width'], info['height']) == (1201, 777)


@pytest.mark.parametrize(
    'srcspace', ['sRGB', 'Rec.2020', 'Display P3', 'ProPhoto RGB', 'Adobe RGB']
)
@pytest.mark.parametrize(
    'dstspace', ['sRGB', 'Rec.2020', 'Display P3', 'ProPhoto RGB', 'Adobe RGB']
)
def test_fused_color_conversion_matches_three_reference_passes(srcspace, dstspace, monkeypatch):
    from ccraw.imaging import colorspaces as cs

    x = np.random.default_rng(18).uniform(-0.5, 2.0, (33, 57, 3)).astype(np.float32)
    # Exercise the native API even for equal-space transfers; public conversion is identity.
    m = np.linalg.solve(cs.rgb_to_xyz_matrix(dstspace, True), cs.rgb_to_xyz_matrix(srcspace, True))
    order = ('sRGB', 'Display P3', 'Adobe RGB', 'ProPhoto RGB', 'Rec.2020')
    actual = native_kernels.convert_rgb(x, m, order.index(srcspace), order.index(dstspace))
    if actual is None:
        pytest.skip('native backend unavailable')
    monkeypatch.setattr(native_kernels, 'enabled', False)
    expected = cs.encode((cs.decode(x, srcspace) @ m.T).astype(np.float32), dstspace)
    np.testing.assert_allclose(actual, expected, atol=2e-7, rtol=2e-7)


@pytest.mark.parametrize('process', [1, 2])
def test_fused_grading_preserves_reference_and_input(process, monkeypatch):
    from ccraw.imaging import color

    x = (
        np.random.default_rng(24)
        .uniform(-0.1 if process == 2 else 0, 1.7 if process == 2 else 1, (47, 61, 3))
        .astype(np.float32)
    )
    original = x.copy()
    grading = dict(shadows=[35, 40], midtones=[210, 38], highlights=[315, 28], balance=32)
    actual = native_kernels.grade(x, grading, wide=process == 2)
    if actual is None:
        pytest.skip('native backend unavailable')
    monkeypatch.setattr(native_kernels, 'enabled', False)
    expected = (wc if process == 2 else color).color_grade(x, grading)
    np.testing.assert_allclose(actual, expected, atol=6e-7)
    np.testing.assert_array_equal(x, original)


def test_wide_tonal_native_is_signed_and_matches_reference(monkeypatch):
    from ccraw.imaging.wide import WideBackend

    x = np.random.default_rng(16).uniform(-0.1, 1.8, (47, 63, 3)).astype(np.float32)
    a = model.recipe()['adjustments']
    a.update(
        exposure=0.6,
        temperature=35,
        tint=-22,
        contrast=20,
        shadows=35,
        highlights=-32,
        blacks=-17,
        whites=12,
    )
    actual = WideBackend().tonal(x, a)
    assert native_kernels.wide_tonal(x, a) is not None
    monkeypatch.setattr(native_kernels, 'enabled', False)
    expected = WideBackend().tonal(x, a)
    np.testing.assert_allclose(actual, expected, atol=6e-7)
    assert actual.min() < 0 and actual.max() > 1


def test_compute_budget_limits_already_loaded_blas():
    from threadpoolctl import threadpool_limits, threadpool_info
    from ccraw import performance

    with threadpool_limits(limits=4, user_api='blas'):
        performance.configure()
        pools = [p for p in threadpool_info() if p['user_api'] == 'blas']
        assert pools and all(p['num_threads'] == 1 for p in pools)


def test_profile_strength_reuses_fixed_transform(tmp_path, monkeypatch):
    from ccraw.imaging import profiles

    path = tmp_path / 'look.cube'
    path.write_text('LUT_1D_SIZE 2\n0.1 0.05 0.0\n0.9 0.95 1.0\n')
    recipe = model.recipe()
    recipe['professional']['profile']['path'] = str(path)
    src = np.random.default_rng(51).random((47, 81, 3), dtype=np.float32)
    cache = engine.RenderCache()
    seen = []
    original = profiles.CubeLUT.apply

    def tracked(*args, **kwargs):
        seen.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(profiles.CubeLUT, 'apply', tracked)
    for amount in [20, 50, 110]:
        recipe['professional']['profile']['amount'] = amount
        actual = engine.process(src, recipe, cache=cache)
        assert len(seen) == 1
        np.testing.assert_array_equal(actual, engine.process(src, recipe))
        seen.pop()


def test_red_eye_roi_matches_original_global_formula():
    src = np.random.default_rng(33).random((83, 127, 3), dtype=np.float32)
    eyes = [
        dict(center=[0.25, 0.6], radius=0.11, amount=75),
        dict(center=[0.27, 0.58], radius=0.07, amount=60),
    ]
    expected = src.copy()
    y, x = np.mgrid[:83, :127].astype(np.float32)
    for eye in eyes:
        center = np.asarray(eye['center'], np.float32)
        distance = np.hypot(x - center[0] * 126, y - center[1] * 82) / (eye['radius'] * 83)
        feather = 1 - pro._smooth(0.75, 1, distance)
        excess = np.maximum(expected[..., 0] - (expected[..., 1] + expected[..., 2]) * 0.5, 0)
        weight = pro._smooth(0.03, 0.15, excess) * feather * (eye['amount'] / 100)
        expected[..., 0] -= excess * weight
    np.testing.assert_array_equal(pro.apply_spatial(src, dict(red_eye=eyes)), expected)


def test_wide_hsl_drag_reuses_color_reference(monkeypatch):
    src = np.random.default_rng(31).random((51, 77, 3), dtype=np.float32)
    recipe = model.recipe()
    recipe['process'] = 2
    recipe['hsl'][1] = [10, 15, 12]
    cache = engine.RenderCache()
    seen = []
    original = wc._lab

    def tracked(rgb, *args, **kwargs):
        if rgb.shape == src.shape:
            seen.append(1)
        return original(rgb, *args, **kwargs)

    monkeypatch.setattr(wc, '_lab', tracked)
    for hue in [15, 25, 40]:
        recipe['hsl'][1][0] = hue
        actual = engine.process(src, recipe, cache=cache)
        assert len(seen) == 1
        np.testing.assert_allclose(actual, engine.process(src, recipe), atol=1e-6)
        seen.pop()


@pytest.mark.parametrize('process', [1, 2])
@pytest.mark.parametrize('tool', ['clarity', 'texture', 'sharpness'])
def test_spatial_amount_drag_reuses_filter_basis(process, tool, monkeypatch):
    import cv2

    src = np.random.default_rng(74).random((77, 113, 3), dtype=np.float32)
    recipe = model.recipe()
    recipe['process'] = process
    recipe['adjustments'][tool] = 15
    cache = engine.RenderCache()
    seen = []
    original = cv2.GaussianBlur

    def tracked(*args, **kwargs):
        seen.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(cv2, 'GaussianBlur', tracked)
    engine.process(src, recipe, cache=cache)
    initial = len(seen)
    for amount in [25, 50, 30]:
        recipe['adjustments'][tool] = amount
        actual = engine.process(src, recipe, cache=cache)
        assert len(seen) == initial
        expected = engine.process(src, recipe)
        np.testing.assert_array_equal(actual, expected)
        del seen[initial:]


def test_point_range_overlay_reuses_pre_edit_reference(monkeypatch):
    from ccraw.live_preview import point_range

    src = np.random.default_rng(42).random((43, 67, 3), dtype=np.float32)
    recipe = model.recipe()
    point = dict(
        rgb=[0.6, 0.4, 0.2],
        hue=10,
        hue_range=40,
        saturation_range=60,
        luminance_range=60,
        feather=50,
    )
    recipe['professional']['point_color'] = [point]
    cache = engine.RenderCache()
    cache.bind(src, 0.3)
    seen = []
    original = engine.render_output

    def tracked(*args, **kwargs):
        seen.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, 'render_output', tracked)
    first = point_range(src, recipe, point, engine.Backend('cpu'), 0.3, cache=cache)
    point['hue'] = 35
    second = point_range(src, recipe, point, engine.Backend('cpu'), 0.3, cache=cache)
    assert len(seen) == 1
    np.testing.assert_array_equal(first, second)
    recipe['adjustments']['exposure'] = 0.3
    point_range(src, recipe, point, engine.Backend('cpu'), 0.3, cache=cache)
    assert len(seen) == 2


@pytest.mark.parametrize('process', [1, 2])
def test_cancelled_frame_exits_before_downstream_color(process, monkeypatch):
    src = np.ones((37, 51, 3), np.float32) * 0.2
    recipe = model.recipe()
    recipe['process'] = process
    seen = []
    calls = []

    def check():
        calls.append(1)
        if len(calls) == 4:
            raise InterruptedError('obsolete gesture')

    monkeypatch.setattr(engine, 'color_base', lambda *a, **k: seen.append(1))
    monkeypatch.setattr(wc, 'color_base', lambda *a, **k: seen.append(1))
    with pytest.raises(InterruptedError):
        engine.process(src, recipe, check=check)
    assert not seen


def test_native_tone_weights_keep_float32_range_fields():
    x = np.random.default_rng(6).uniform(-0.03, 1.7, (47, 71, 3)).astype(np.float32)
    actual = native_kernels.tone_weights(x)
    if actual is None:
        pytest.skip('native backend unavailable')
    p = np.clip(x[..., 0] * 0.2126 + x[..., 1] * 0.7152 + x[..., 2] * 0.0722, 0, 1) ** 0.45
    expected = np.stack([(1 - p) ** 2, p**3, (1 - p) ** 6, p**6])
    np.testing.assert_allclose(actual, expected, atol=3e-7, rtol=5e-7)


@pytest.mark.parametrize(
    'values',
    [
        dict(horizontal=17),
        dict(vertical=-23, scale=122),
        dict(aspect=37, offset_x=-13, offset_y=24),
        dict(horizontal=40, vertical=-30, aspect=-15, scale=150, flip_x=True),
        dict(flip_y=True),
    ],
)
@pytest.mark.parametrize('channel', [0, 1, 2])
def test_native_geometry_maps_match_original_sampling(values, channel, monkeypatch):
    recipe = model.recipe()
    recipe['professional']['geometry'].update(values)
    recipe['professional']['optics'].update(distortion=-12, ca_red=17, ca_blue=-19)
    g, o = pro._geometry_values(recipe['professional'])
    native = native_kernels.remap_map((81, 137, 3), g, o, channel)
    if native is None:
        pytest.skip('native backend unavailable')
    monkeypatch.setattr(native_kernels, 'enabled', False)
    expected = pro.remap_map((81, 137, 3), recipe['professional'], channel)
    import cv2

    src = np.random.default_rng(33).random((81, 137, 3), dtype=np.float32)
    np.testing.assert_array_equal(
        cv2.remap(src, *native, cv2.INTER_LINEAR), cv2.remap(src, *expected, cv2.INTER_LINEAR)
    )


@pytest.mark.parametrize('mode', ['camera', 'look'])
def test_dcp_amount_drag_does_not_decode_the_camera_again(tmp_path, monkeypatch, mode):
    from ccraw.imaging import profiles, io

    resource = tmp_path / 'camera.dcp'
    resource.write_bytes(b'fixture')
    src = io.ImageSource(
        np.full((31, 47, 3), 0.2, np.float32), raw_path='camera.nef', camera_kelvin=5500
    )
    recipe = model.recipe()
    recipe['process'] = 2
    recipe['professional']['profile'].update(path=str(resource), mode=mode)
    seen = []

    class DCP:
        def apply_camera(self, native, **kwargs):
            seen.append('camera')
            return native * 1.1

        def apply_look(self, working, **kwargs):
            seen.append('look')
            return working * 1.1

    monkeypatch.setattr(profiles, 'load_dcp', lambda _: DCP())
    monkeypatch.setattr(
        io, 'decode_camera_native', lambda *a, **k: np.full(src.shape, 0.3, np.float32)
    )
    cache = engine.RenderCache()
    for amount in [20, 50, 120]:
        recipe['professional']['profile']['amount'] = amount
        actual = engine.process(src, recipe, cache=cache)
        assert len(seen) == 1
        np.testing.assert_array_equal(actual, engine.process(src, recipe))
        seen.pop()


def test_parallel_ufuncs_preserve_every_float32_bit():
    from ccraw import cpu_ops

    x = np.random.default_rng(35).uniform(-1.2, 1.7, (513, 771, 3)).astype(np.float32)
    original = x.copy()
    np.testing.assert_array_equal(cpu_ops.cbrt(x), np.cbrt(x))
    np.testing.assert_array_equal(cpu_ops.cube(x), x**3)
    np.testing.assert_array_equal(x, original)


def test_native_wide_hsl_parameters_match_double_interpolation():
    lab = np.random.default_rng(56).uniform(-0.4, 1.0, (47, 79, 3)).astype(np.float32)
    polar = np.stack([np.hypot(lab[..., 1], lab[..., 2]), np.arctan2(lab[..., 2], lab[..., 1])])
    controls = np.random.default_rng(31).uniform(-100, 100, (8, 3)).astype(np.float32)
    axis, values = wc._hue_data(controls)
    actual = native_kernels.hsl_parameters(lab, polar, axis, values)
    if actual is None:
        pytest.skip('native backend unavailable')
    shifts = wc._hue_controls(polar[1], controls)
    expected = lab.copy()
    expected[..., 0] *= 2 ** (shifts[..., 2] / 100)
    expected[..., 1] = polar[0] * np.maximum(0, 1 + shifts[..., 1] / 100)
    expected[..., 2] = polar[1] + np.deg2rad(shifts[..., 0] * 0.45)
    np.testing.assert_array_equal(actual, expected)


def test_icc_decode_tiles_run_in_parallel_with_independent_transforms(monkeypatch):
    import threading, time
    from ccraw.imaging import io, cms, colorspaces as cs

    x = np.random.default_rng(63).integers(0, 65536, (641, 1024, 3), dtype=np.uint16)
    source = cs.profile_bytes('Display P3')
    target = cs.profile_bytes('sRGB', linear=True)
    expected = cms.transform_icc(
        x.astype(np.float32) / 65535, source, target, black_point_compensation=False
    )
    ids = []
    original = cms.transform_icc

    def tracked(*args, **kwargs):
        ids.append(threading.get_ident())
        time.sleep(0.01)
        return original(*args, **kwargs)

    monkeypatch.setattr(cms, 'transform_icc', tracked)
    actual = io._decode_rgb(x, source, 65535)
    np.testing.assert_array_equal(actual, expected)
    assert len(set(ids)) >= 2


def test_bw_mixer_drag_reuses_hsv_source(monkeypatch):
    import cv2

    src = np.random.default_rng(73).random((61, 83, 3), dtype=np.float32)
    recipe = model.recipe()
    recipe['monochrome'] = True
    cache = engine.RenderCache()
    seen = []
    original = cv2.cvtColor

    def track(x, code, *args, **kwargs):
        if code == cv2.COLOR_RGB2HSV:
            seen.append(1)
        return original(x, code, *args, **kwargs)

    monkeypatch.setattr(cv2, 'cvtColor', track)
    for amount in [15, 35, 70]:
        recipe['professional']['bw_mixer'][0] = amount
        actual = engine.process(src, recipe, cache=cache)
        assert len(seen) == 1
        np.testing.assert_allclose(actual, engine.process(src, recipe), atol=5e-7)
        seen.pop()


@pytest.mark.parametrize(
    'srcspace,dstspace',
    [
        ('Rec.2020', 'sRGB'),
        ('ProPhoto RGB', 'Display P3'),
        ('sRGB', 'Adobe RGB'),
        ('Display P3', 'ProPhoto RGB'),
    ],
)
def test_fused_neutral_pixel_conversion_preserves_extended_grey(srcspace, dstspace, monkeypatch):
    from ccraw.imaging import colorspaces as cs

    rgb = np.repeat(np.linspace(-1, 2, 4097, dtype=np.float32)[:, None], 3, 1)
    actual = cs.convert(rgb, srcspace, dstspace)
    monkeypatch.setattr(native_kernels, 'enabled', False)
    expected = cs.convert(rgb, srcspace, dstspace)
    np.testing.assert_allclose(actual, expected, atol=2e-7, rtol=2e-7)


def test_native_geometry_matches_full_size_fractional_pixel_reference(monkeypatch):
    shape = (601, 1001, 3)
    recipe = model.recipe()
    pro_settings = recipe['professional']
    pro_settings['geometry'].update(
        horizontal=27.3, vertical=-17.8, scale=113.5, aspect=-16.2, offset_x=-14.7, offset_y=6.3
    )
    pro_settings['optics'].update(distortion=-11.5, ca_red=13.4, ca_blue=-17.7)
    rgb = np.random.default_rng(54).random(shape, dtype=np.float32)
    actual = pro.apply_geometry(rgb, pro_settings)
    monkeypatch.setattr(native_kernels, 'enabled', False)
    np.testing.assert_array_equal(actual, pro.apply_geometry(rgb, pro_settings))


def test_decode_budget_change_cannot_race_with_insertion(tmp_path, monkeypatch):
    import threading
    from ccraw.live_preview import DecodedPreviews

    first, second = tmp_path / 'first.png', tmp_path / 'second.png'
    first.write_bytes(b'first')
    second.write_bytes(b'second')
    monkeypatch.setattr(
        engine, 'load_image', lambda p, **kwargs: (np.ones((1, 1, 3), np.float32), {'name': p.name})
    )
    cache = DecodedPreviews(max_bytes=20)
    cache.load(first, 64)
    entered, release, updated = threading.Event(), threading.Event(), threading.Event()
    errors = []
    getter = DecodedPreviews.bytes.fget

    def measured(obj):
        if threading.current_thread().name == 'cache-insert' and not entered.is_set():
            entered.set()
            release.wait(3)
        return getter(obj)

    monkeypatch.setattr(DecodedPreviews, 'bytes', property(measured))

    def insert():
        try:
            cache.load(second, 64)
        except Exception as exc:
            errors.append(exc)

    def resize():
        cache.max_bytes = 8
        cache.clear()
        updated.set()

    worker = threading.Thread(target=insert, name='cache-insert')
    worker.start()
    assert entered.wait(3)
    updater = threading.Thread(target=resize)
    updater.start()
    updated.wait(0.2)
    release.set()
    worker.join(3)
    updater.join(3)
    assert not worker.is_alive() and not updater.is_alive()
    assert not errors and cache.bytes <= cache.max_bytes


@pytest.mark.parametrize('process', [1, 2])
def test_lens_vignette_amount_reuses_linear_source(process, monkeypatch):
    from ccraw.imaging import colorspaces as cs

    source = np.random.default_rng(38).random((61, 97, 3), dtype=np.float32)
    recipe = model.recipe()
    recipe['process'] = process
    cache = engine.RenderCache()
    seen = []
    target = pro if process == 1 else cs
    name = '_linear' if process == 1 else 'decode'
    original = getattr(target, name)

    def tracked(*args, **kwargs):
        seen.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, tracked)
    engine.process(source, recipe, cache=cache)
    seen.clear()
    for value in [15, 40, 65]:
        recipe['professional']['optics']['vignetting'] = value
        actual = engine.process(source, recipe, cache=cache)
        assert len(seen) == 1
        expected = engine.process(source, recipe)
        np.testing.assert_allclose(actual, expected, atol=5e-7)
        seen.pop()


@pytest.mark.parametrize('space', ['sRGB', 'Rec.2020'])
def test_fused_lens_vignette_keeps_double_gain_reference(space, monkeypatch):
    x = np.random.default_rng(28).uniform(-0.05, 1.4, (71, 97, 3)).astype(np.float32)
    settings = dict(optics=dict(vignetting=57, midpoint=34))
    actual = pro.apply_spatial(x, settings, space=space)
    assert native_kernels.lens_vignette(x, np.ones(x.shape[:2], np.float64), 50, space) is not None
    monkeypatch.setattr(native_kernels, 'enabled', False)
    np.testing.assert_allclose(actual, pro.apply_spatial(x, settings, space=space), atol=2e-7)
