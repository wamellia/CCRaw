"""Precision and format contracts for the process-2 color path."""

import struct
from pathlib import Path

import numpy as np
import pytest


def test_builtin_spaces_roundtrip_keeps_extended_linear_values():
    from ccraw.imaging import colorspaces as cs

    rgb = np.array([[-0.02, 0.18, 1.4], [0.7, 0.1, 0.02]], dtype=np.float32)
    for name in ('sRGB', 'Adobe RGB', 'Display P3', 'ProPhoto RGB', 'Rec.2020'):
        converted = cs.convert_linear(rgb, 'Rec.2020', name)
        np.testing.assert_allclose(cs.convert_linear(converted, name, 'Rec.2020'), rgb, atol=5e-7)
        np.testing.assert_allclose(cs.decode(cs.encode(rgb, name), name), rgb, atol=5e-7)
    assert np.array_equal(cs.convert(rgb), rgb)


def test_named_conversion_matches_independent_srgb_rec2020_red_reference():
    from ccraw.imaging import colorspaces as cs

    # IEC sRGB and BT.2020 D65 chromaticities, independent published matrix result.
    got = cs.convert_linear(np.array([[1, 0, 0]], np.float32), 'sRGB', 'Rec.2020')
    np.testing.assert_allclose(got, [[0.6274039, 0.0690973, 0.0163914]], atol=2e-6)
    with pytest.raises(ValueError):
        cs.convert(got, dstspace='unknown')


def test_native_icc_float_transform_retains_more_than_eight_bit_precision():
    from ccraw.imaging import colorspaces as cs, cms

    assert cms.available(), cms.status()
    ramp = np.linspace(0.01, 0.95, 4096, dtype=np.float32)
    rgb = np.repeat(ramp[:, None], 3, axis=1)
    for name in ('sRGB', 'Adobe RGB', 'Display P3', 'ProPhoto RGB', 'Rec.2020'):
        data = cs.profile_bytes(name)
        assert data[36:40] == b'acsp'
        converted = cms.transform_icc(rgb, cs.profile_bytes('sRGB'), data)
        assert converted.dtype == np.float32
        assert len(np.unique(converted[:, 0])) > 4000
        expected = cs.convert(rgb, 'sRGB', name)
        np.testing.assert_allclose(converted, expected, atol=3e-4)
    assert np.array_equal(cms.transform_icc(rgb, data, data), rgb)


def test_icc_validation_and_uint16_are_explicit():
    from ccraw.imaging import colorspaces as cs, cms

    with pytest.raises(ValueError):
        cms.transform_icc(np.ones((2, 3)), b'bad', cs.profile_bytes('sRGB'))
    rgb = np.arange(1500, dtype=np.uint16).reshape(500, 3)
    out = cms.transform_icc_u16(rgb, cs.profile_bytes('sRGB'), cs.profile_bytes('Adobe RGB'))
    assert out.dtype == np.uint16
    assert len(np.unique(out[:, 0])) > 450
    with pytest.raises(ValueError):
        cms.transform_icc(
            np.array([[np.nan, 0, 0]]), cs.profile_bytes('sRGB'), cs.profile_bytes('sRGB')
        )


def test_proof_flags_wide_gamut_green_and_preserves_grey():
    from ccraw.imaging import colorspaces as cs, cms

    rgb = np.array([[0, 1, 0], [0.5, 0.5, 0.5]], np.float32)
    result = cms.soft_proof(rgb, cs.profile_bytes('Rec.2020'), cs.profile_bytes('sRGB'))
    assert result.gamut_mask.tolist() == [True, False]
    np.testing.assert_allclose(result.rgb[1], rgb[1], atol=3e-4)
    assert np.isfinite(result.rgb).all()
    for intent in ('perceptual', 'relative', 'absolute', 'saturation'):
        assert (
            cms.soft_proof(
                rgb,
                cs.profile_bytes('Rec.2020'),
                cs.profile_bytes('sRGB'),
                intent=intent,
                paper_ink=True,
            ).rgb.shape
            == rgb.shape
        )


def cube(size=2, scale=1):
    return 'LUT_3D_SIZE %d\n' % size + '\n'.join(
        f'{r / (size - 1) * scale} {g / (size - 1) * scale} {b / (size - 1) * scale}'
        for b in range(size)
        for g in range(size)
        for r in range(size)
    )


def test_cube_red_fastest_order_trilinear_and_amount(tmp_path):
    from ccraw.imaging import profiles

    path = tmp_path / 'half.cube'
    path.write_text(cube(scale=0.5))
    rgb = np.array([[0.3, 0.4, 0.7], [1, 0, 0]], np.float32)
    lut = profiles.load_cube(path)
    np.testing.assert_allclose(lut.apply(rgb), rgb * 0.5, atol=1e-7)
    np.testing.assert_allclose(
        profiles.apply_profile(rgb, {'path': str(path), 'amount': 50}), rgb * 0.75, atol=1e-7
    )
    path.write_text(cube())
    assert np.array_equal(profiles.apply_profile(rgb, {'path': str(path), 'amount': 100}), rgb)
    assert np.array_equal(profiles.apply_profile(rgb, {'path': '', 'amount': 100}), rgb)


def test_cube_one_dimensional_domain_and_invalid_input(tmp_path):
    from ccraw.imaging import profiles

    lut = profiles.parse_cube(
        'LUT_1D_SIZE 2\nDOMAIN_MIN -1 -1 -1\nDOMAIN_MAX 1 1 1\n0 0 0\n1 1 1\n'
    )
    np.testing.assert_allclose(lut.apply(np.array([[0, -1, 1]], np.float32)), [[0.5, 0, 1]])
    for bad in (
        'LUT_3D_SIZE 2\n0 0 0',
        'LUT_3D_SIZE 999\n',
        'LUT_1D_SIZE 2\n0 0 0\nnan 1 1',
        'LUT_1D_SIZE 2\nDOMAIN_MAX 0 1 1\n0 0 0\n1 1 1',
        'LUT_1D_SIZE 2\nLUT_3D_SIZE 2\n',
        'UNKNOWN 2\n',
    ):
        with pytest.raises(ValueError):
            profiles.parse_cube(bad)


def dcp_fixture(tags):
    """Small classic little-endian TIFF, independently encoded test resource."""
    entries, payload = [], bytearray()
    offset = 8 + 2 + 12 * len(tags) + 4
    for tag, (kind, values) in sorted(tags.items()):
        fmt = {3: 'H', 4: 'I', 11: 'f', 12: 'd'}[kind]
        data = struct.pack('<' + fmt * len(values), *values)
        value = data.ljust(4, b'\0') if len(data) <= 4 else struct.pack('<I', offset + len(payload))
        entries.append(struct.pack('<HHI', tag, kind, len(values)) + value)
        if len(data) > 4:
            payload.extend(data)
    return (
        b'II*\0'
        + struct.pack('<I', 8)
        + struct.pack('<H', len(tags))
        + b''.join(entries)
        + b'\0' * 4
        + payload
    )


def test_dcp_matrix_tone_dual_illuminant_and_support_report():
    from ccraw.imaging import profiles

    data = dcp_fixture(
        {
            50721: (12, [1, 0, 0, 0, 1, 0, 0, 0, 1]),
            50722: (12, [2, 0, 0, 0, 2, 0, 0, 0, 2]),
            50778: (3, [17]),
            50779: (3, [21]),
            50940: (11, [0, 0, 0.5, 0.25, 1, 1]),
            65000: (4, [1]),
        }
    )
    dcp = profiles.parse_dcp(data)
    assert dcp.support['matrix'] and dcp.support['tone_curve']
    assert dcp.support['dual_illuminant']
    assert 65000 in dcp.unsupported_tags
    np.testing.assert_allclose(dcp.color_matrix(2856), np.eye(3), atol=1e-7)
    np.testing.assert_allclose(dcp.color_matrix(6504), np.eye(3) * 2, atol=1e-7)
    assert dcp.tone_curve.shape == (3, 2)
    with pytest.raises(ValueError):
        profiles.parse_dcp(b'II*\0' + b'\xff' * 12)
    with pytest.raises(ValueError):
        profiles.parse_dcp(dcp_fixture({50721: (12, [0] * 9)}))


def test_dcp_camera_matrix_requires_camera_pixels_and_look_table_is_applied(tmp_path):
    from ccraw.imaging import profiles, colorspaces as cs

    tags = {
        50721: (12, [1, 0, 0, 0, 1, 0, 0, 0, 1]),
        50964: (12, cs.rgb_to_xyz_matrix('ProPhoto RGB').ravel().tolist()),
        50981: (4, [1, 2, 1]),
        50982: (11, [0, 1, 1, 60, 1, 1]),
        51109: (12, [0]),
    }
    data = dcp_fixture(tags)
    dcp = profiles.parse_dcp(data)
    assert dcp.support['look_table'] and dcp.support['forward_matrix']
    # Matrix is a real RGB -> XYZ transform; forward white-balanced camera red
    # is ProPhoto red and this explicit look shifts red to yellow.
    got = dcp.apply_camera(np.array([[0.5, 0, 0]], np.float32), dstspace='ProPhoto RGB')
    np.testing.assert_allclose(got, [[0.5, 0.5, 0]], atol=1e-6)
    path = tmp_path / 'camera.dcp'
    path.write_bytes(data)
    with pytest.raises(ValueError, match='camera'):
        profiles.apply_profile(np.ones((1, 3), np.float32), {'path': str(path), 'amount': 100})


def test_dcp_rejects_table_count_invalid_encoding_and_malformed_tone():
    from ccraw.imaging import profiles

    matrix = {50721: (12, [1, 0, 0, 0, 1, 0, 0, 0, 1])}
    for tags in (
        {50981: (4, [2, 2, 1]), 50982: (11, [0, 1, 1])},
        {50981: (4, [1, 2, 1]), 50982: (11, [0, 1, 1, 0, 1, 1]), 51108: (4, [2])},
        {50940: (11, [0, 0, 0.5, 0.5, 0.4, 1])},
    ):
        with pytest.raises(ValueError):
            profiles.parse_dcp(dcp_fixture(matrix | tags))


def test_identity_cube_preserves_every_float_bit_at_common_grid_size():
    from ccraw.imaging import profiles

    rgb = np.random.default_rng(123).random((31, 37, 3), dtype=np.float32)
    out = profiles.parse_cube(cube(17)).apply(rgb)
    assert np.array_equal(out, rgb)


def test_gamut_alarm_does_not_mark_real_white_or_magenta():
    from ccraw.imaging import cms, colorspaces as cs

    rgb = np.array([[1, 1, 1], [1, 0, 1], [0, 1, 0], [0.5, 0.5, 0.5]], np.float32)
    got = cms.soft_proof(rgb, cs.profile_bytes('sRGB'), cs.profile_bytes('sRGB'))
    assert not got.gamut_mask.any()


def test_dcp_huesat_linear_and_srgb_value_encoding_and_negative_handling():
    from ccraw.imaging import profiles

    # Hue +60 degrees at saturated values; half encoded V is decoded to linear.
    tags = {
        50721: (12, [1, 0, 0, 0, 1, 0, 0, 0, 1]),
        50937: (4, [1, 2, 1]),
        50938: (11, [0, 1, 1, 60, 1, 0.5]),
        51107: (4, [1]),
    }
    dcp = profiles.parse_dcp(dcp_fixture(tags))
    rgb = np.array([[1, 0, 0], [-0.1, 0.5, 0.2]], np.float32)
    got = dcp.huesat1.apply(rgb)
    np.testing.assert_allclose(got[0], [0.21404114, 0.21404114, 0], atol=1e-7)
    assert np.array_equal(got[1], rgb[1])


def test_dcp_extended_camera_header_and_big_endian_numeric_tags():
    from ccraw.imaging import profiles

    data = dcp_fixture({50721: (12, [1, 0, 0, 0, 1, 0, 0, 0, 1])})
    dcp = profiles.parse_dcp(data[:2] + b'RC' + data[4:])
    assert dcp.support['matrix']
    big = b'MM\0*' + struct.pack('>I', 8) + struct.pack('>H', 1)
    big += struct.pack('>HHII', 50721, 12, 9, 26) + b'\0' * 4
    big += struct.pack('>9d', 1, 0, 0, 0, 1, 0, 0, 0, 1)
    np.testing.assert_array_equal(profiles.parse_dcp(big).color_matrix(), np.eye(3))


def test_export_schema_space_aliases_and_float_overflow_are_guarded():
    from ccraw.imaging import colorspaces as cs, profiles

    assert cs.canonical_name('adobe_rgb') == 'Adobe RGB'
    assert cs.canonical_name('display_p3') == 'Display P3'
    assert cs.canonical_name('prophoto_rgb') == 'ProPhoto RGB'
    with pytest.raises(ValueError):
        profiles.parse_cube('LUT_1D_SIZE 2\n0 0 0\n1e99 1 1')
    with pytest.raises(ValueError):
        profiles.parse_cube('LUT_1D_SIZE 2\nDOMAIN_MIN -1e308 0 0\n0 0 0\n1 1 1')


def test_identity_icc_does_not_bypass_native_profile_validation():
    from ccraw.imaging import cms

    fake = bytearray(132)
    fake[:4] = struct.pack('>I', 132)
    fake[16:24] = b'RGB XYZ '
    fake[36:40] = b'acsp'
    with pytest.raises(ValueError):
        cms.transform_icc(np.ones((1, 3), np.float32), bytes(fake), bytes(fake))


def test_installed_external_rgb_and_cmyk_profiles_without_redistribution():
    from ccraw.imaging import cms, colorspaces as cs

    directory = Path('C:/Windows/System32/spool/drivers/color')
    adobe, printer = directory / 'AdobeRGB1998.icc', directory / 'CoatedFOGRA39.icc'
    if not adobe.is_file() or not printer.is_file():
        pytest.skip('Optional locally installed RGB and CMYK ICC reference profiles absent')
    rgb = np.array([[1, 1, 1], [0.5, 0.5, 0.5], [1, 0, 0]], np.float32)
    got = cms.transform_icc(rgb, cs.profile_bytes('sRGB'), cms.load_profile(adobe))
    # ICC colorants use 16.16 fixed point. Compare linear values: encoded gamma
    # magnifies the expected quantization near a zero channel of saturated red.
    np.testing.assert_allclose(
        cs.decode(got, 'Adobe RGB'),
        cs.convert_linear(cs.decode(rgb), 'sRGB', 'Adobe RGB'),
        atol=5e-5,
    )
    proof = cms.soft_proof(rgb, cs.profile_bytes('sRGB'), cms.load_profile(printer))
    paper = cms.soft_proof(rgb, cs.profile_bytes('sRGB'), cms.load_profile(printer), paper_ink=True)
    assert proof.gamut_mask.tolist() == [False, False, True]
    assert np.isfinite(paper.rgb).all()
    assert np.max(np.abs(paper.rgb - proof.rgb)) > 0.01


def test_dcp_hostile_tiff_numeric_table_type_has_validation_error():
    from ccraw.imaging import profiles

    data = bytearray(
        dcp_fixture(
            {
                50721: (12, [1, 0, 0, 0, 1, 0, 0, 0, 1]),
                50981: (4, [1, 2, 1]),
                50982: (11, [0, 1, 1, 0, 1, 1]),
            }
        )
    )
    # Change the dimensions TIFF type to ASCII. Parser must report validation
    # failure rather than leaking numpy TypeError or AttributeError into UI.
    struct.pack_into('<H', data, 10 + 12 + 2, 2)
    with pytest.raises(ValueError):
        profiles.parse_dcp(bytes(data))
