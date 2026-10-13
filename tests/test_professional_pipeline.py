"""Professional CPU operator contracts with synthetic, independently derived images."""

import importlib

import numpy as np
import pytest


@pytest.fixture
def pro():
    try:
        return importlib.import_module('ccraw.imaging.professional')
    except ModuleNotFoundError:
        pytest.fail('professional CPU operators must be implemented')


def image():
    y, x = np.mgrid[:65, :81].astype(np.float32)
    return np.stack([x / 80, y / 64, (x + y) / 144], axis=-1)


def test_neutral_preserves_object_and_pixels(pro):
    src = image()
    settings = {'geometry': {'scale': 100}, 'detail': {'radius': 1, 'detail': 25}}
    assert pro.geometry_neutral(settings)
    assert pro.apply_color(src, settings) is src
    assert pro.apply_geometry(src, settings) is src
    assert pro.apply_spatial(src, settings) is src


def test_flips_have_exact_orientation(pro):
    src = image()
    np.testing.assert_array_equal(
        pro.apply_geometry(src, {'geometry': {'flip_x': True}}), src[:, ::-1]
    )
    np.testing.assert_array_equal(
        pro.apply_geometry(src, {'geometry': {'flip_y': True}}), src[::-1]
    )


@pytest.mark.parametrize(
    'field', ['vertical', 'horizontal', 'aspect', 'scale', 'offset_x', 'offset_y']
)
def test_geometry_moves_grid_and_retains_finite_shape(pro, field):
    src = image()
    value = 130 if field == 'scale' else 30
    out = pro.apply_geometry(src, {'geometry': {field: value}})
    assert out.shape == src.shape and out.dtype == np.float32
    assert np.isfinite(out).all()
    assert np.max(np.abs(src - out)) > 0.01


@pytest.mark.parametrize('field', ['distortion', 'ca_red', 'ca_blue'])
def test_optical_maps_act_on_channels(pro, field):
    src = image()
    out = pro.apply_geometry(src, {'optics': {field: 100}})
    assert np.max(np.abs(src - out)) > 0.0001
    if field == 'ca_red':
        np.testing.assert_array_equal(out[..., 1:], src[..., 1:])
    if field == 'ca_blue':
        np.testing.assert_array_equal(out[..., :2], src[..., :2])


def test_vignette_gain_is_radial_and_midpoint_changes_falloff(pro):
    src = np.full((65, 65, 3), 0.25, np.float32)
    out = pro.apply_spatial(src, {'optics': {'vignetting': 50, 'midpoint': 50}})
    assert abs(float(out[32, 32, 0]) - 0.25) < 1e-6
    assert out[0, 0, 0] > out[16, 16, 0] > out[32, 32, 0]
    wider = pro.apply_spatial(src, {'optics': {'vignetting': 50, 'midpoint': 10}})
    assert wider[16, 16, 0] > out[16, 16, 0]


def test_point_color_selects_red_preserving_blue_gray_and_disabled(pro):
    src = np.array([[[0.8, 0.1, 0.1], [0.1, 0.1, 0.8], [0.4, 0.4, 0.4]]], np.float32)
    sample = {'rgb': [0.8, 0.1, 0.1], 'hue': 30, 'saturation': 30, 'luminance': 25}
    out = pro.apply_color(src, {'point_color': [sample]})
    assert np.linalg.norm(out[0, 0] - src[0, 0]) > 0.05
    np.testing.assert_array_equal(out[0, 1:], src[0, 1:])
    assert pro.apply_color(src, {'point_color': [dict(sample, enabled=False)]}) is src


def test_point_hue_wrap_selects_both_sides_of_red(pro):
    src = np.array([[[0.8, 0.1, 0.12], [0.8, 0.12, 0.1]]], np.float32)
    out = pro.apply_color(src, {'point_color': [{'rgb': [0.8, 0.1, 0.1], 'luminance': 40}]})
    assert np.min(np.linalg.norm(out - src, axis=-1)) > 0.05


def test_calibration_preserves_neutral_and_changes_primary(pro):
    src = np.array([[[0.4, 0.4, 0.4], [0.8, 0.1, 0.1], [0.1, 0.8, 0.1]]], np.float32)
    out = pro.apply_color(src, {'calibration': {'red_hue': 60, 'red_saturation': 30}})
    np.testing.assert_allclose(out[0, 0], src[0, 0], atol=2e-6)
    assert np.linalg.norm(out[0, 1] - src[0, 1]) > 0.01


def test_shadows_tint_is_stronger_in_shadows(pro):
    src = np.array([[[0.2, 0.2, 0.2], [0.95, 0.95, 0.95]]], np.float32)
    out = pro.apply_color(src, {'calibration': {'shadows_tint': 70}})
    assert np.linalg.norm(out[0, 0] - src[0, 0]) > np.linalg.norm(out[0, 1] - src[0, 1])


def test_bw_mixer_only_runs_for_monochrome_and_weights_source_hue(pro):
    src = np.array([[[0.8, 0.1, 0.1], [0.1, 0.1, 0.8]]], np.float32)
    mix = [80, 0, 0, 0, 0, 0, 0, 0]
    assert pro.apply_color(src, {'bw_mixer': mix}) is src
    neutral = pro.apply_color(src, {'monochrome': True})
    adjusted = pro.apply_color(src, {'bw_mixer': mix, 'monochrome': True})
    np.testing.assert_allclose(adjusted[..., 0], adjusted[..., 1], atol=1e-7)
    assert adjusted[0, 0, 0] > neutral[0, 0, 0] + 0.02
    assert abs(float(adjusted[0, 1, 0] - neutral[0, 1, 0])) < 0.005


def test_red_eye_reduces_red_excess_only_inside_circle(pro):
    src = np.full((41, 41, 3), [0.8, 0.15, 0.15], np.float32)
    out = pro.apply_spatial(
        src, {'red_eye': [{'center': [0.5, 0.5], 'radius': 0.15, 'amount': 100}]}
    )
    assert out[20, 20, 0] < 0.25
    np.testing.assert_array_equal(out[0, 0], src[0, 0])


def test_denoise_reduces_noise_and_sharpen_preserves_flat_fields(pro):
    rng = np.random.default_rng(12)
    src = np.clip(0.5 + rng.normal(0, 0.04, (97, 97, 3)), 0, 1).astype(np.float32)
    out = pro.apply_spatial(src, {'detail': {'luminance': 100, 'color': 100}})
    assert np.std(out[5:-5, 5:-5]) < np.std(src[5:-5, 5:-5]) * 0.85
    flat = np.full((21, 31, 3), 0.4, np.float32)
    np.testing.assert_allclose(
        pro.apply_spatial(flat, {'detail': {'amount': 150}}), flat, atol=2e-6
    )


def test_sharpen_increases_edge_contrast_without_mutating_source(pro):
    src = np.full((65, 81, 3), 0.35, np.float32)
    src[:, 40:] = 0.65
    before = src.copy()
    out = pro.apply_spatial(src, {'detail': {'amount': 100, 'radius': 1.5}})
    assert out[32, 39, 0] < 0.35 and out[32, 40, 0] > 0.65
    np.testing.assert_array_equal(src, before)


def test_preview_scale_changes_original_pixel_sharpen_radius(pro):
    src = image()
    src[:, 40:] = 0.7
    edits = {'detail': {'amount': 100, 'radius': 3}}
    a = pro.apply_spatial(src, edits, scale=1)
    b = pro.apply_spatial(src, edits, scale=0.25)
    assert np.max(np.abs(a - b)) > 0.001


def test_defringe_only_desaturates_edge_fringe(pro):
    src = np.full((65, 65, 3), 0.9, np.float32)
    src[:, :32] = 0.05
    src[:, 32] = [0.5, 0.3, 0.5]
    src[10:20, 45:55] = [0.6, 0.1, 0.6]
    out = pro.apply_spatial(src, {'optics': {'defringe_purple': 100}})
    assert (out[32, 32, 0] - out[32, 32, 1]) < 0.2
    np.testing.assert_array_equal(out[15, 50], src[15, 50])


@pytest.mark.parametrize(
    'method,params',
    [
        ('apply_geometry', {'geometry': {'vertical': float('nan')}}),
        ('apply_spatial', {'detail': {'radius': float('inf'), 'amount': 20}}),
        ('apply_color', {'calibration': {'red_hue': float('nan')}}),
    ],
)
def test_nonfinite_controls_raise_actionable_error(pro, method, params):
    with pytest.raises(ValueError, match='finite'):
        getattr(pro, method)(image(), params)


def test_profile_is_delegated_by_root(pro):
    src = image()
    assert pro.apply_color(src, {'profile': {'path': 'separate-adapter.cube'}}) is src


def test_mask_mapping_matches_green_image_geometry(pro):
    import cv2

    src = image()
    settings = {'geometry': {'vertical': 35, 'horizontal': -15}, 'optics': {'distortion': 20}}
    mx, my = pro.remap_map(src.shape, settings)
    mask = cv2.remap(src[..., 1], mx, my, cv2.INTER_LINEAR)
    np.testing.assert_array_equal(mask, pro.apply_geometry(src, settings)[..., 1])


def test_lensfun_matches_interpolates_and_maps_local_profile(tmp_path):
    try:
        lens = importlib.import_module('ccraw.imaging.lensfun_profiles')
    except ModuleNotFoundError:
        pytest.fail('local Lensfun importer must be implemented')
    path = tmp_path / 'lenses.xml'
    path.write_text(
        """<lensdatabase version="2"><camera><maker>Test</maker><model>Body</model><mount>M</mount><cropfactor>1.5</cropfactor></camera><lens><maker>Test</maker><model>Zoom</model><mount>M</mount><cropfactor>1.5</cropfactor><calibration><distortion model="poly3" focal="20" k1=".02"/><distortion model="poly3" focal="40" k1=".04"/></calibration></lens></lensdatabase>"""
    )
    database = lens.load_database(path)
    match = lens.match_lens(database, 'Test', 'Zoom', camera_maker='Test', camera_model='Body')
    calibration = lens.interpolate_distortion(match, 30)
    assert calibration['model'] == 'poly3'
    assert calibration['coefficients'][0] == pytest.approx(0.03)
    np.testing.assert_allclose(
        lens.radial_factor(np.array([0.0, 0.5, 1.0]), calibration), [0.97, 0.9775, 1]
    )
    with pytest.raises(ValueError, match='range'):
        lens.interpolate_distortion(match, 200)
    with pytest.raises(ValueError, match='match'):
        lens.match_lens(database, 'Other', 'Unknown')


def test_lensfun_rejects_entities_and_unsupported_models(tmp_path):
    try:
        lens = importlib.import_module('ccraw.imaging.lensfun_profiles')
    except ModuleNotFoundError:
        pytest.fail('local Lensfun importer must be implemented')
    path = tmp_path / 'bad.xml'
    path.write_text('<!DOCTYPE x [<!ENTITY p SYSTEM "file:///private">]><lensdatabase/>')
    with pytest.raises(ValueError, match='DOCTYPE|entities'):
        lens.load_database(path)
    path.write_text(
        '<lensdatabase><lens><maker>T</maker><model>L</model><calibration><distortion model="fisheye" focal="10"/></calibration></lens></lensdatabase>'
    )
    with pytest.raises(ValueError, match='supported'):
        lens.load_database(path)


def test_lensfun_map_is_identity_for_zero_coefficients_and_rejects_folding():
    lens = importlib.import_module('ccraw.imaging.lensfun_profiles')
    y, x = np.mgrid[:21, :31].astype(np.float32)
    mx, my = lens.distortion_maps((21, 31), {'model': 'poly3', 'coefficients': [0]})
    np.testing.assert_array_equal(mx, x)
    np.testing.assert_array_equal(my, y)
    with pytest.raises(ValueError, match='fold|invalid'):
        lens.distortion_maps((65, 65), {'model': 'poly5', 'coefficients': [-0.4, 0]})


@pytest.mark.parametrize(
    'field,base',
    [
        ('detail', {'amount': 100, 'radius': 2}),
        ('masking', {'amount': 100, 'radius': 2}),
        ('luminance_detail', {'luminance': 100}),
        ('luminance_contrast', {'luminance': 100}),
        ('color_detail', {'color': 100}),
        ('color_smoothness', {'color': 100}),
    ],
)
def test_secondary_detail_controls_have_real_effect(pro, field, base):
    src = np.random.default_rng(24).random((65, 81, 3), dtype=np.float32)
    low = pro.apply_spatial(src, {'detail': dict(base, **{field: 0})})
    high = pro.apply_spatial(src, {'detail': dict(base, **{field: 100})})
    assert np.max(np.abs(low - high)) > 0.0001


def test_point_range_excludes_distant_lightness_and_neutral_sample_ignores_hue(pro):
    src = np.array([[[0.2, 0.2, 0.2], [0.8, 0.8, 0.8]]], np.float32)
    point = {'rgb': [0.2, 0.2, 0.2], 'luminance': 50, 'luminance_range': 2}
    out = pro.apply_color(src, {'point_color': [point]})
    assert np.all(out[0, 0] > src[0, 0])
    np.testing.assert_array_equal(out[0, 1], src[0, 1])


def test_combined_manual_warp_rejects_folding_before_sampling(pro):
    with pytest.raises(ValueError, match='fold|invalid'):
        pro.apply_geometry(image(), {'geometry': {'scale': 50}, 'optics': {'distortion': 100}})
