"""Signed/HDR controls and matching frame/ROI math in encoded Rec.2020."""

import copy

import numpy as np
import pytest

from ccraw import engine, model


def _rgb():
    y, x = np.mgrid[:72, :96]
    return np.stack(
        [-0.3 + 0.03 * np.sin(x / 5), 0.2 + 0.01 * np.cos(y / 4), 1.6 + 0.05 * np.cos(x / 7)], -1
    ).astype(np.float32)


def test_neutral_controls_return_identical_object():
    from ccraw.imaging import wide_color as wc

    rgb = _rgb()
    recipe = model.recipe()
    assert wc.color_base(rgb, recipe) is rgb
    assert wc.color_grade(rgb, recipe['grading']) is rgb
    assert wc.finishing(rgb, recipe['effects']) is rgb
    assert wc.reduce_noise(rgb, recipe['adjustments']) is rgb
    assert wc.spatial_details(rgb, recipe['adjustments']) is rgb
    assert wc.dehaze(rgb, 0) is rgb


@pytest.mark.parametrize(
    'tool',
    [
        'saturation',
        'vibrance',
        'hsl',
        'calibration',
        'curve',
        'grading',
        'grain',
        'vignette',
        'denoise',
        'color_noise',
        'clarity',
        'texture',
        'sharpness',
        'dehaze',
    ],
)
def test_non_neutral_controls_preserve_signed_hdr_and_source(tool):
    from ccraw.imaging import wide_color as wc

    rgb = _rgb()
    before = rgb.copy()
    recipe = model.recipe()
    if tool in ('saturation', 'vibrance'):
        recipe['adjustments'][tool] = 5
        out = wc.color_base(rgb, recipe)
    elif tool == 'hsl':
        recipe['hsl'] = [[2, 3, 1]] * 8
        out = wc.color_base(rgb, recipe)
    elif tool == 'calibration':
        recipe['professional']['calibration']['red_hue'] = 2
        out = wc.color_base(rgb, recipe)
    elif tool == 'curve':
        recipe['curves']['RGB'] = [[0, 0], [0.5, 0.55], [1, 1]]
        out = wc.color_base(rgb, recipe)
    elif tool == 'grading':
        out = wc.color_grade(rgb, {'midtones': [30, 5]})
    elif tool in ('grain', 'vignette'):
        effects = copy.deepcopy(recipe['effects'])
        effects[tool] = 5
        out = wc.finishing(rgb, effects)
    else:
        recipe['adjustments'][tool] = 10
        out = wc.spatial_details(rgb, recipe['adjustments'])
    assert np.isfinite(out).all()
    assert out.dtype == np.float32
    assert out.min() < -0.05
    assert out.max() > 1.1
    assert not np.array_equal(out, rgb)
    np.testing.assert_array_equal(rgb, before)


@pytest.mark.parametrize('mode', ['linear', 'smooth'])
def test_curve_endpoint_tangents_extend_outside_sdr_domain(mode):
    from ccraw.imaging import wide_color as wc

    rgb = np.array([[[-1.0, 0.5, 2.0]]], np.float32)
    recipe = model.recipe()
    recipe['curves']['RGB'] = [[0, 0], [0.5, 0.25], [1, 1]]
    recipe['curve_mode'] = mode
    np.testing.assert_allclose(wc.color_base(rgb, recipe), [[[-0.5, 0.25, 2.5]]], atol=1e-6)


def test_finishing_roi_matches_global_frame_coordinates():
    from ccraw.imaging import wide_color as wc

    rgb = _rgb()
    effects = {'grain': 35, 'grain_size': 30, 'vignette': -30, 'midpoint': 45, 'feather': 60}
    full = wc.finishing(rgb, effects, [0.05, 0.1, 0.9, 0.95])
    area = engine.Area(96, 72, 21, 17, 69, 54)
    part = wc.finishing(rgb[17:54, 21:69], effects, [0.05, 0.1, 0.9, 0.95], area)
    np.testing.assert_allclose(part, full[17:54, 21:69], atol=1e-6)


@pytest.mark.parametrize('tool', ['denoise', 'color_noise', 'clarity', 'texture', 'sharpness'])
def test_spatial_halo_roi_matches_full_frame(tool):
    from ccraw.imaging import wide_color as wc

    rng = np.random.default_rng(83)
    rgb = _rgb() + rng.normal(0, 0.015, (72, 96, 3)).astype(np.float32)
    adjustments = model.recipe()['adjustments']
    adjustments[tool] = 30
    full = wc.spatial_details(rgb, adjustments)
    target = engine.Area(96, 72, 32, 26, 64, 46)
    area = target.grow(engine.spatial_margin(adjustments, rgb.shape))
    part = wc.spatial_details(
        rgb[area.y0 : area.y1, area.x0 : area.x1], adjustments, reference_shape=rgb.shape, area=area
    )
    np.testing.assert_allclose(part[area.inner(target)], full[26:46, 32:64], atol=2e-5)


def test_dehaze_roi_uses_shared_atmosphere_and_transmission():
    from ccraw.imaging import wide_color as wc

    rgb = _rgb()
    context = (
        np.array([0.8, 0.9, 1.0], np.float32),
        np.linspace(0.3, 1, 72 * 96, dtype=np.float32).reshape(72, 96),
    )
    full = wc.dehaze(rgb, 35, context)
    area = engine.Area(96, 72, 21, 17, 69, 54)
    part = wc.dehaze(rgb[17:54, 21:69], 35, context, area)
    np.testing.assert_allclose(part, full[17:54, 21:69], atol=1e-6)


def test_real_cfa_dcp_stage_applies_camera_neutral_once_with_independent_matrix_math(tmp_path):
    import hashlib
    import tifffile
    from test_professional_input import _camera_dng
    from ccraw.imaging.io import load_image, decode_camera_native
    from ccraw.imaging import colorspaces as cs
    from ccraw.imaging.profiles import profile_info

    source_path = tmp_path / 'sensor.dng'
    _camera_dng(source_path)
    source_hash = hashlib.sha256(source_path.read_bytes()).digest()
    source, _ = load_image(source_path, preview_limit=0)
    native = decode_camera_native(source_path)
    matrix = np.array([[1.0, 0.1, 0.05], [0.05, 1.0, 0.02], [0, 0.1, 1.1]], np.float64)
    profile = tmp_path / 'synthetic-camera.dcp'
    tifffile.imwrite(
        profile,
        np.zeros((2, 2, 3), np.uint8),
        photometric='rgb',
        metadata=None,
        extratags=[(50721, 'd', 9, matrix.ravel(), False), (50778, 'H', 1, 21, False)],
    )
    support = profile_info(profile)
    assert support['support']['matrix'] is True
    assert support['support']['complete'] is False
    assert support['unsupported_tags']
    # Independent von Kries / Bradford reference, with the known CFA as-shot
    # neutral [.5,1,.5]. This checks stage math, not real camera calibration.
    inverse = np.linalg.inv(matrix)
    neutral = np.array([0.5, 1, 0.5])
    white = inverse @ neutral
    bradford = np.array(
        [[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]]
    )
    adaptation = (
        np.linalg.inv(bradford)
        @ np.diag((bradford @ cs.D65) / (bradford @ (white / white[1])))
        @ bradford
    )
    xyz = (np.asarray(native) @ inverse.T) / white[1] @ adaptation.T
    expected = cs.encode(
        (xyz @ np.linalg.inv(cs.rgb_to_xyz_matrix('Rec.2020')).T).astype(np.float32), 'Rec.2020'
    )
    recipe = model.recipe()
    recipe['process'] = 2
    recipe['professional']['profile'].update(path=str(profile), mode='camera')
    actual = engine.render_output(source, recipe, color_space='Rec.2020')
    np.testing.assert_allclose(actual, expected, atol=2e-6)
    assert source_hash == hashlib.sha256(source_path.read_bytes()).digest()
    # An already developed photograph must not enter the native camera stage.
    with pytest.raises(ValueError, match='RAW'):
        engine.render_output(np.asarray(source), recipe, color_space='Rec.2020')


def test_display_icc_and_softproof_do_not_change_professional_export(window, tmp_path):
    import hashlib
    import tifffile
    from pathlib import Path
    from ccraw.imaging import colorspaces as cs
    from test_ui import wait_until

    original = hashlib.sha256(Path(window.source_path).read_bytes()).digest()
    options = {'format': 'tiff', 'bit_depth': 16, 'color_space': 'display_p3', 'intent': 'relative'}
    first = tmp_path / 'before.tif'
    window.start_export(str(first), export_options=options)
    wait_until(lambda: not window.exporting, timeout=45)
    monitor = tmp_path / 'display.icc'
    monitor.write_bytes(cs.profile_bytes('Adobe RGB'))
    proof = tmp_path / 'proof.icc'
    proof.write_bytes(cs.profile_bytes('Rec.2020'))
    window.setProperty('displayProfilePath', str(monitor))
    window.configure_softproof(
        {
            'enabled': True,
            'path': str(proof),
            'intent': 'relative',
            'gamut_warning': True,
            'paper_ink': True,
            'black_point_compensation': True,
        }
    )
    wait_until(lambda: not window.render_running and not window.timer.isActive(), timeout=45)
    second = tmp_path / 'after.tif'
    window.start_export(str(second), export_options=options)
    wait_until(lambda: not window.exporting, timeout=45)
    np.testing.assert_array_equal(tifffile.imread(first), tifffile.imread(second))
    with tifffile.TiffFile(second) as file:
        assert file.pages[0].tags[34675].value == cs.profile_bytes('Display P3')
    assert hashlib.sha256(Path(window.source_path).read_bytes()).digest() == original
