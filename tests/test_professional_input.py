"""Real codec and native CMS input precision, including signed gamut components."""

import hashlib

import numpy as np
import pytest
import tifffile

from ccraw import export_presets as ep
from ccraw.imaging import colorspaces as cs
from ccraw.imaging.io import load_image


@pytest.mark.parametrize('format', ['tiff', 'png', 'psd'])
def test_p3_16bit_input_preserves_precision_gamut_and_original(tmp_path, format):
    encoded = np.zeros((8, 513, 3), np.float32)
    encoded[..., 1] = np.linspace(0.1, 0.9, 513)
    encoded[0, 0] = [0, 1, 0]
    path = tmp_path / ('green.' + format)
    ep.export_rgb(path, encoded, {'format': format, 'bit_depth': 16, 'color_space': 'display_p3'})
    before = hashlib.sha256(path.read_bytes()).digest()
    image, info = load_image(path, preview_limit=0)
    quantized = np.rint(encoded * 65535) / 65535
    expected = cs.convert_linear(cs.decode(quantized, 'Display P3'), 'Display P3', 'sRGB')
    np.testing.assert_allclose(image, expected, atol=3e-4)
    assert image[0, 0, 0] < -0.1
    assert image[0, 0, 2] < -0.01
    assert len(np.unique(image[2, :, 1])) == 513
    assert info['source_bit_depth'] == 16
    assert info['icc_applied'] is True
    assert before == hashlib.sha256(path.read_bytes()).digest()


@pytest.mark.parametrize('orientation', range(1, 9))
def test_16bit_tiff_orientation_and_float_icc(tmp_path, orientation):
    encoded = np.linspace(0.05, 0.95, 18, dtype=np.float32).reshape(2, 3, 3)
    profile = cs.profile_bytes('Adobe RGB')
    data = np.rint(encoded * 65535).astype(np.uint16)
    path = tmp_path / 'oriented.tif'
    tifffile.imwrite(
        path,
        data,
        photometric='rgb',
        metadata=None,
        extratags=[(34675, 'B', len(profile), profile, False), (274, 'H', 1, orientation, False)],
    )
    expected = cs.convert_linear(
        cs.decode(data / np.float32(65535), 'Adobe RGB'), 'Adobe RGB', 'sRGB'
    )
    if orientation == 2:
        expected = expected[:, ::-1]
    if orientation == 3:
        expected = expected[::-1, ::-1]
    if orientation == 4:
        expected = expected[::-1]
    if orientation == 5:
        expected = expected.transpose(1, 0, 2)
    if orientation == 6:
        expected = np.rot90(expected, 3)
    if orientation == 7:
        expected = expected.transpose(1, 0, 2)[::-1, ::-1]
    if orientation == 8:
        expected = np.rot90(expected)
    image, info = load_image(path, preview_limit=0)
    np.testing.assert_allclose(image, expected, atol=3e-4)
    assert (info['height'], info['width']) == image.shape[:2]


def test_image_source_attributes_survive_views():
    from ccraw.imaging.io import ImageSource

    source = ImageSource(
        np.ones((4, 4, 3), np.float32),
        source_path='camera.dng',
        raw_path='camera.dng',
        camera_kelvin=5200,
        camera_neutral=[0.5, 1, 0.7],
        decode_info={'native': True},
    )
    view = source[::2]
    assert view.raw_path == source.raw_path
    assert view.camera_kelvin == 5200
    assert view.camera_neutral == [0.5, 1, 0.7]


def _camera_dng(path):
    # Genuine CFA RAW, decoded by the installed LibRaw rather than a mock loader.
    data = np.full((64, 96), 12000, np.uint16)
    data[::2, ::2] = 22000
    data[1::2, 1::2] = 8000
    matrix = (
        10000,
        10000,
        0,
        10000,
        0,
        10000,
        0,
        10000,
        10000,
        10000,
        0,
        10000,
        0,
        10000,
        0,
        10000,
        10000,
        10000,
    )
    tags = [
        (50706, 'B', 4, (1, 4, 0, 0), False),
        (50707, 'B', 4, (1, 1, 0, 0), False),
        (50708, 's', 0, 'CCRaw test camera', False),
        (33421, 'H', 2, (2, 2), False),
        (33422, 'B', 4, (0, 1, 1, 2), False),
        (50717, 'I', 1, 65535, False),
        (50714, 'H', 1, 0, False),
        (50721, '2i', 9, matrix, False),
        (50728, '2I', 3, (1, 2, 1, 1, 1, 2), False),
        (50778, 'H', 1, 21, False),
    ]
    tifffile.imwrite(path, data, photometric=32803, metadata=None, extratags=tags)
    return data


def test_real_camera_native_decoder_retains_unbalanced_sensor_channels(tmp_path):
    from ccraw.imaging.io import decode_camera_native

    path = tmp_path / 'sensor.dng'
    _camera_dng(path)
    before = hashlib.sha256(path.read_bytes()).digest()
    camera = decode_camera_native(path)
    assert camera.shape == (64, 96, 3)
    np.testing.assert_allclose(
        camera[8:-8, 8:-8].mean(axis=(0, 1)), np.array([22000, 12000, 8000]) / 65535, atol=2e-5
    )
    assert camera.decode_info['color_space'] == 'camera-native'
    np.testing.assert_allclose(camera.camera_neutral, [0.5, 1, 0.5])
    small = decode_camera_native(path, preview_shape=(16, 24))
    assert small.shape == (16, 24, 3)
    assert camera.raw_path == str(path.resolve())
    assert before == hashlib.sha256(path.read_bytes()).digest()


def test_real_raw_xyz_conversion_avoids_srgb_clipping(tmp_path):
    import rawpy

    path = tmp_path / 'sensor.dng'
    _camera_dng(path)
    with rawpy.imread(str(path)) as raw:
        xyz = (
            raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=True,
                output_bps=16,
                gamma=(1, 1),
                output_color=rawpy.ColorSpace.XYZ,
                half_size=False,
                user_flip=None,
                highlight_mode=rawpy.HighlightMode.Blend,
            ).astype(np.float32)
            / 65535
        )
    expected = (xyz @ np.linalg.inv(cs.rgb_to_xyz_matrix('sRGB')).T).astype(np.float32)
    source, info = load_image(path, preview_limit=0)
    np.testing.assert_allclose(source, expected, atol=2e-6)
    assert info['raw'] is True
    assert source.raw_path == str(path.resolve())
    assert info['decode_info']['color_space'] == 'XYZ D65 -> linear sRGB'


def test_p3_input_process2_export_and_reimport_roundtrip(tmp_path):
    from ccraw import engine, model

    encoded = np.zeros((12, 513, 3), np.float32)
    encoded[..., 1] = np.linspace(0.1, 1, 513)
    path = tmp_path / 'original.tif'
    ep.export_rgb(path, encoded, {'format': 'tiff', 'bit_depth': 16, 'color_space': 'display_p3'})
    before = hashlib.sha256(path.read_bytes()).digest()
    source, _ = load_image(path, preview_limit=0)
    recipe = model.recipe()
    recipe['process'] = 2
    result = engine.render_output(source, recipe, color_space='Display P3', intent='relative')
    destination = tmp_path / 'returned.tif'
    ep.export_rgb(
        destination, result, {'format': 'tiff', 'bit_depth': 16, 'color_space': 'display_p3'}
    )
    data = tifffile.imread(destination).astype(np.float32) / 65535
    np.testing.assert_allclose(data, encoded, atol=4e-4)
    assert len(np.unique(data[6, :, 1])) == 513
    reimported, _ = load_image(destination, preview_limit=0)
    np.testing.assert_allclose(reimported, source, atol=4e-4)
    assert before == hashlib.sha256(path.read_bytes()).digest()


def test_float_linear_icc_tiff_preserves_hdr_and_signed_components(tmp_path):
    rgb = np.array([[[-0.1, 0.2, 1.3], [0.125, 2.0, -0.05]]], np.float32)
    profile = cs.profile_bytes('sRGB', linear=True)
    path = tmp_path / 'float.tif'
    tifffile.imwrite(
        path,
        rgb,
        photometric='rgb',
        metadata=None,
        extratags=[(34675, 'B', len(profile), profile, False)],
    )
    actual, info = load_image(path, preview_limit=0)
    np.testing.assert_array_equal(actual, rgb)
    assert info['source_bit_depth'] == 32


@pytest.mark.parametrize('profiled', [False, True])
@pytest.mark.parametrize('format', ['tiff', 'png', 'psd'])
def test_mapped_input_normalization_icc_and_orientation_use_bounded_tiles(
    tmp_path, monkeypatch, profiled, format
):
    from ccraw import large_image
    from ccraw.imaging import io, cms

    encoded = np.zeros((130, 97, 3), np.uint16)
    encoded[..., 1] = np.arange(97, dtype=np.uint16)[None, :] * 600 + 7000
    encoded[0, 0] = [0, 65535, 0]
    path = tmp_path / ('mapped.' + format)
    profile = cs.profile_bytes('Display P3') if profiled else None
    tags = [(274, 'H', 1, 6, False)]
    if profile:
        tags.append((34675, 'B', len(profile), profile, False))
    if format == 'tiff':
        tifffile.imwrite(path, encoded, photometric='rgb', metadata=None, extratags=tags)
    elif format == 'png':
        import cv2

        if profiled:
            ep.export_rgb(
                path,
                encoded.astype(np.float32) / 65535,
                {'format': 'png', 'bit_depth': 16, 'color_space': 'display_p3'},
            )
        else:
            ok, data = cv2.imencode('.png', encoded[..., ::-1])
            assert ok
            path.write_bytes(data.tobytes())
    else:
        from ccraw.interchange.psd import write_psd

        write_psd(path, encoded.astype(np.float32) / 65535, icc_profile=profile)
    before = hashlib.sha256(path.read_bytes()).digest()
    monkeypatch.setattr(large_image, 'MAP_BYTES', 1024)
    monkeypatch.setattr(io, 'DECODE_TILE_PIXELS', 512)
    sizes = []
    if profiled:
        transform = cms.transform_icc

        def bounded(rgb, *args, **kwargs):
            sizes.append(rgb.shape[0] * rgb.shape[1])
            return transform(rgb, *args, **kwargs)

        monkeypatch.setattr(cms, 'transform_icc', bounded)
    else:
        decode = cs.decode

        def bounded(rgb, *args, **kwargs):
            sizes.append(rgb.shape[0] * rgb.shape[1])
            return decode(rgb, *args, **kwargs)

        monkeypatch.setattr(cs, 'decode', bounded)
    actual, _ = load_image(path, preview_limit=0)
    assert len(sizes) > 1 and max(sizes) <= 512
    chain = actual
    mapped = False
    while chain is not None:
        mapped |= isinstance(chain, np.memmap)
        chain = getattr(chain, 'base', None)
    assert mapped
    # Compute the reference without the instrumented decoder.
    linear = cs.decode(encoded.astype(np.float32) / 65535, 'Display P3' if profiled else 'sRGB')
    expected = cs.convert_linear(linear, 'Display P3', 'sRGB') if profiled else linear
    expected = np.rot90(expected, 3) if format == 'tiff' else expected
    np.testing.assert_allclose(actual, expected, atol=3e-4)
    assert before == hashlib.sha256(path.read_bytes()).digest()


def test_png_codec_memory_preflight_precedes_native_raster_decode(tmp_path, monkeypatch):
    from ccraw import large_image
    from ccraw.imaging import io
    import cv2

    path = tmp_path / 'memory.png'
    ok, data = cv2.imencode('.png', np.ones((40, 50, 3), np.uint16))
    assert ok
    path.write_bytes(data.tobytes())
    before = hashlib.sha256(path.read_bytes()).digest()
    monkeypatch.setattr(large_image, 'MAP_BYTES', 1024)
    monkeypatch.setattr(large_image, 'available_memory', lambda: 1024)
    monkeypatch.setattr(
        io.cv2, 'imdecode', lambda *args: pytest.fail('raster codec ran before memory admission')
    )
    with pytest.raises(ValueError, match='内存'):
        load_image(path, preview_limit=0)
    assert before == hashlib.sha256(path.read_bytes()).digest()
