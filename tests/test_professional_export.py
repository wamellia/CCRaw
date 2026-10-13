"""Export and interchange contracts, using real files and independent image readers."""

import hashlib
import json
import struct
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from functools import lru_cache

import numpy as np
import pytest
import tifffile
from PIL import Image, ImageCms


@lru_cache(maxsize=1)
def icc():
    return ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()


def test_export_preset_roundtrip_and_true_depth_combinations(tmp_path):
    from ccraw import export_presets as ep

    preset = ep.defaults()
    preset.update(format='tiff', bit_depth=16, color_space='rec2020')
    ep.save(tmp_path / 'presets.json', {'Print': preset})
    assert ep.load(tmp_path / 'presets.json')['Print']['bit_depth'] == 16
    for bad in [
        dict(format='jpeg', bit_depth=16),
        dict(quality=float('nan')),
        dict(long_edge=-1),
        dict(unknown=True),
        dict(intent='guess'),
    ]:
        with pytest.raises(ValueError):
            ep.validate(bad)


@pytest.mark.parametrize(
    'template',
    [
        '../{stem}',
        '{stem}/file',
        'C:\\{stem}',
        '{stem.__class__}',
        '{unknown}',
        '{seq:999999d}',
        'CON',
    ],
)
def test_export_name_blocks_windows_path_and_format_injection(template):
    from ccraw import export_presets as ep

    with pytest.raises(ValueError):
        ep.file_name('photo.nef', dict(file_name_template=template))


def test_export_name_uses_sequence_date_and_unique_conflicts(tmp_path):
    from ccraw import export_presets as ep

    opts = dict(file_name_template='{stem}_{date}_{seq:04d}', format='tiff', bit_depth=16)
    assert ep.file_name('Photo.NEF', opts, 7, datetime(2026, 10, 12)) == 'Photo_20261012_0007.tif'
    (tmp_path / 'Photo_20261012_0007.tif').write_bytes(b'existing')
    assert (
        ep.output_path(tmp_path, 'Photo.NEF', opts, 7, datetime(2026, 10, 12)).name
        == 'Photo_20261012_0007-2.tif'
    )
    assert (tmp_path / 'Photo_20261012_0007.tif').read_bytes() == b'existing'


def test_metadata_policy_removes_gps_and_camera_without_mutating_source():
    from ccraw import export_presets as ep

    source = {
        'creator': ['A'],
        'copyright': 'Rights',
        'camera_model': 'Camera',
        'exif': {'Model': 'Camera'},
        'gps': {'latitude': 50},
        'location': 'Home',
        'title': 'Photo',
        'keywords': ['one'],
    }
    result = ep.apply_metadata_policy(source, 'all_except_camera', remove_location=True)
    assert result == {
        'creator': ['A'],
        'copyright': 'Rights',
        'title': 'Photo',
        'keywords': ['one'],
    }
    result['creator'].append('B')
    assert source['creator'] == ['A']
    assert ep.apply_metadata_policy(source, 'copyright') == {
        'creator': ['A'],
        'copyright': 'Rights',
    }
    assert ep.apply_metadata_policy(source, 'none') == {}


def test_output_sharpening_keeps_constant_and_strengthens_edge():
    from ccraw import export_presets as ep

    constant = np.full((30, 30, 3), 0.4, np.float32)
    np.testing.assert_allclose(ep.apply_output_sharpening(constant, 'matte', 'high'), constant)
    edge = constant.copy()
    edge[:, 15:] = 0.6
    sharpened = ep.apply_output_sharpening(edge, 'screen', 'standard')
    assert sharpened[15, 14, 0] < 0.4 and sharpened[15, 15, 0] > 0.6
    assert np.all(edge[:, :15] == 0.4)


@pytest.mark.parametrize('depth', [8, 16])
def test_flat_psd_has_real_planar_rgb_and_icc_decoded_independently(tmp_path, depth):
    from ccraw.interchange.psd import write_psd

    pixels = np.array(
        [[[0, 0.5, 1], [0.2, 0.7, 0.8]], [[0.1, 0.3, 0.9], [0.6, 0.4, 0.25]]], np.float32
    )
    path = tmp_path / 'image.psd'
    profile = icc()
    write_psd(path, pixels, bit_depth=depth, icc_profile=profile)
    data = path.read_bytes()
    signature, version, reserved, channels, height, width, actual_depth, mode = struct.unpack(
        '>4sH6sHIIHH', data[:26]
    )
    assert (signature, version, reserved, channels, height, width, actual_depth, mode) == (
        b'8BPS',
        1,
        b'\0' * 6,
        3,
        2,
        2,
        depth,
        3,
    )
    pos = 26
    color_len = struct.unpack_from('>I', data, pos)[0]
    pos += 4 + color_len
    resource_len = struct.unpack_from('>I', data, pos)[0]
    resource = data[pos + 4 : pos + 4 + resource_len]
    assert resource[:6] == b'8BIM\x04\x0f'
    assert struct.unpack_from('>I', resource, 8)[0] == len(profile)
    assert resource[12 : 12 + len(profile)] == profile
    pos += 4 + resource_len
    layer_len = struct.unpack_from('>I', data, pos)[0]
    pos += 4 + layer_len
    assert data[pos : pos + 2] == b'\0\0'
    decoded = (
        np.frombuffer(data[pos + 2 :], dtype='u1' if depth == 8 else '>u2')
        .reshape(3, 2, 2)
        .transpose(1, 2, 0)
    )
    np.testing.assert_array_equal(
        decoded, np.rint(pixels * ((1 << depth) - 1)).astype(np.uint8 if depth == 8 else np.uint16)
    )
    if depth == 8:
        with Image.open(path) as image:
            np.testing.assert_array_equal(np.asarray(image), decoded)


@pytest.mark.parametrize(
    'fmt,depth,ext',
    [('tiff', 16, '.tif'), ('png', 16, '.png'), ('png', 8, '.png'), ('jpeg', 8, '.jpg')],
)
def test_export_pixels_precision_profile_and_metadata(tmp_path, fmt, depth, ext):
    from ccraw import export_presets as ep

    pixels = np.linspace(0, 1, 513, dtype=np.float32)[None, :, None].repeat(3, 2)
    path = tmp_path / ('image' + ext)
    ep.export_rgb(
        path,
        pixels,
        dict(format=fmt, bit_depth=depth),
        icc_profile=icc(),
        metadata={'title': 'Example', 'gps': {'latitude': 3}},
        provenance={'source': 'photo.nef'},
    )
    if fmt == 'tiff':
        with tifffile.TiffFile(path) as image:
            assert image.pages[0].tags[34675].value == icc()
            assert json.loads(image.pages[0].description)['metadata']['title'] == 'Example'
            assert len(np.unique(image.asarray()[..., 0])) == 513
    elif fmt == 'png' and depth == 16:
        import cv2

        decoded = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
        assert decoded.dtype == np.uint16 and len(np.unique(decoded[..., 0])) == 513
        with Image.open(path) as image:
            assert image.info['icc_profile'] == icc()
    else:
        with Image.open(path) as image:
            assert image.info['icc_profile'] == icc()


def test_export_cancel_and_error_leave_existing_file_and_no_temp(tmp_path):
    from ccraw import export_presets as ep

    path = tmp_path / 'image.tif'
    path.write_bytes(b'existing')
    with pytest.raises(InterruptedError):
        ep.export_rgb(
            path,
            np.ones((3, 3, 3), np.float32),
            dict(format='tiff', bit_depth=16),
            icc_profile=icc(),
            cancelled=lambda: True,
        )
    assert path.read_bytes() == b'existing'
    with pytest.raises(ValueError):
        ep.export_rgb(
            path, np.full((3, 3, 3), np.nan), dict(format='tiff', bit_depth=16), icc_profile=icc()
        )
    assert sorted(p.name for p in tmp_path.iterdir()) == ['image.tif']


FOREIGN = """<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:r="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><r:RDF><r:Description xmlns:m="http://ns.adobe.com/xap/1.0/" xmlns:z="urn:foreign" xmlns:c="http://ns.adobe.com/camera-raw-settings/1.0/" m:Rating="2" z:custom="keep" c:Exposure2012="0.50"><z:history><r:Seq><r:li>old event</r:li></r:Seq></z:history></r:Description></r:RDF></x:xmpmeta>"""


def test_xmp_merge_roundtrip_unknown_fields_and_recipe(tmp_path):
    from ccraw.interchange import xmp

    raw = tmp_path / 'source.nef'
    raw.write_bytes(b'RAW original')
    original_hash = hashlib.sha256(raw.read_bytes()).hexdigest()
    sidecar = raw.with_suffix('.xmp')
    sidecar.write_text(FOREIGN, encoding='utf-8')
    metadata = {
        'title': '标题 & <one>',
        'caption': 'Caption',
        'creator': ['Alice', 'Bob'],
        'copyright': '© Alice',
        'location': 'Hong Kong',
        'keywords': ['a', 'b'],
        'rating': 5,
        'flag': 'pick',
    }
    xmp.write_sidecar(
        raw, metadata, recipe={'version': 6, 'process': 2, 'adjustments': {'exposure': 1.2}}
    )
    parsed = xmp.read(sidecar)
    assert parsed['metadata'] == metadata
    assert parsed['recipe']['adjustments']['exposure'] == 1.2
    tree = ET.fromstring(sidecar.read_bytes())
    desc = tree.find('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description')
    assert desc.get('{urn:foreign}custom') == 'keep'
    assert desc.get('{http://ns.adobe.com/camera-raw-settings/1.0/}Exposure2012') == '0.50'
    assert tree.find('.//{urn:foreign}history') is not None
    xmp.write_sidecar(raw, {'rating': 0, 'keywords': []})
    assert xmp.read(sidecar)['metadata']['title'] == metadata['title']
    assert xmp.read(sidecar)['metadata']['keywords'] == []
    assert xmp.read(sidecar)['recipe']['process'] == 2
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == original_hash


@pytest.mark.parametrize(
    'packet',
    [
        '<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///C:/secret">]><x/>',
        '<a>' * 80 + '</a>' * 80,
        '<rdf:RDF',
        '<x>' + 'a' * (4 * 1024 * 1024) + '</x>',
    ],
    ids=['entity', 'depth', 'malformed', 'size'],
)
def test_xmp_rejects_unbounded_unsafe_or_invalid_xml(packet):
    from ccraw.interchange import xmp

    with pytest.raises(ValueError):
        xmp.loads(packet)


def test_xmp_refuses_bad_existing_sidecar_without_overwriting(tmp_path):
    from ccraw.interchange import xmp

    raw = tmp_path / 'image.raw'
    raw.write_bytes(b'RAW')
    path = raw.with_suffix('.xmp')
    path.write_bytes(b'broken xml')
    with pytest.raises(ValueError):
        xmp.write_sidecar(raw, {'title': 'new'})
    assert path.read_bytes() == b'broken xml'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['image.raw', 'image.xmp']


def test_external_editor_argument_array_roundtrip_preserves_original(tmp_path):
    from ccraw.external_editor import prepare_handoff, launch, reload_if_changed

    raw = tmp_path / 'source.nef'
    raw.write_bytes(b'original')
    helper = tmp_path / 'helper.py'
    helper.write_text(
        'import sys, tifffile\np=sys.argv[1]\na=tifffile.imread(p)\na[:]=12345\ntifffile.imwrite(p,a,photometric="rgb")\n',
        encoding='utf-8',
    )
    handoff = prepare_handoff(
        raw,
        np.full((3, 4, 3), 0.5, np.float32),
        tmp_path,
        {'version': 6},
        bit_depth=16,
        icc_profile=icc(),
    )
    assert handoff.path != raw and handoff.path.suffix == '.tif'
    assert json.loads(handoff.provenance_path.read_text())['recipe'] == {'version': 6}
    assert reload_if_changed(handoff) is None
    process = launch(handoff, sys.executable, [str(helper), '{file}'])
    assert process.wait(timeout=10) == 0
    result = reload_if_changed(handoff)
    assert result['derived_from'] == str(raw.resolve())
    np.testing.assert_allclose(result['rgb'], 12345 / 65535, atol=1e-7)
    assert raw.read_bytes() == b'original'
    assert reload_if_changed(handoff) is None


def test_external_psd_copy_and_reject_shell_string(tmp_path):
    from ccraw.external_editor import prepare_handoff, launch

    raw = tmp_path / 'source.nef'
    raw.write_bytes(b'original')
    handoff = prepare_handoff(
        raw,
        np.zeros((2, 2, 3), np.float32),
        tmp_path,
        {'version': 6},
        format='psd',
        bit_depth=16,
        icc_profile=icc(),
    )
    assert handoff.path.read_bytes().startswith(b'8BPS')
    with pytest.raises(ValueError):
        launch(handoff, sys.executable, 'echo hello; danger')


@pytest.mark.parametrize('depth,compression', [(8, 0), (16, 0), (8, 1), (16, 1)])
def test_read_modified_psd_merged_rgb_raw_or_rle(tmp_path, depth, compression):
    from ccraw.interchange.psd import read_psd

    # Independent tiny composite: two pixels (1,2,3), (4,5,6).
    rows = (
        [bytes([1, 4]), bytes([2, 5]), bytes([3, 6])]
        if depth == 8
        else [b'\0\1\0\4', b'\0\2\0\5', b'\0\3\0\6']
    )
    header = struct.pack('>4sH6sHIIHH', b'8BPS', 1, b'\0' * 6, 3, 1, 2, depth, 3)
    if compression:
        packed = [bytes([len(row) - 1]) + row for row in rows]
        payload = b''.join(struct.pack('>H', len(row)) for row in packed) + b''.join(packed)
    else:
        payload = b''.join(rows)
    path = tmp_path / 'modified.psd'
    path.write_bytes(header + b'\0' * 12 + struct.pack('>H', compression) + payload)
    result = read_psd(path)
    np.testing.assert_allclose(
        result['rgb'], np.array([[[1, 2, 3], [4, 5, 6]]]) / ((1 << depth) - 1), atol=1e-8
    )
    assert result['bit_depth'] == depth


def test_xmp_preserves_localized_title_and_rejects_utf16_entity():
    from ccraw.interchange import xmp

    packet = FOREIGN.replace(
        '</r:Description>',
        '<dc:title xmlns:dc="http://purl.org/dc/elements/1.1/"><r:Alt><r:li xml:lang="fr">Titre</r:li></r:Alt></dc:title></r:Description>',
    )
    merged = xmp.merge(packet, {'title': 'New'})
    assert xmp.loads(merged)['metadata']['title'] == 'New'
    tree = ET.fromstring(merged)
    assert any(
        li.text == 'Titre'
        for li in tree.findall('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li')
    )
    with pytest.raises(ValueError):
        xmp.loads('<!DOCTYPE x [<!ENTITY x "data">]><x/>'.encode('utf-16'))


def test_xmp_approximate_crs_supported_values_only():
    from ccraw.interchange import xmp

    result = xmp.loads(xmp.merge(FOREIGN, {}, crs={'Exposure2012': 1.25, 'CropLeft': 0.1}))
    assert result['crs']['Exposure2012'] == '1.25'
    assert result['crs']['CropLeft'] == '0.1'
    for invalid in [{'Contrast': 3}, {'Exposure2012': float('nan')}, {'CropLeft': 2}]:
        with pytest.raises(ValueError):
            xmp.merge(FOREIGN, {}, crs=invalid)


def test_export_cancel_after_encode_preserves_destination(tmp_path):
    from ccraw import export_presets as ep

    target = tmp_path / 'out.png'
    target.write_bytes(b'existing')
    checks = []

    def cancel():
        checks.append(1)
        return len(checks) >= 3

    with pytest.raises(InterruptedError):
        ep.export_rgb(
            target,
            np.ones((2, 2, 3), np.float32),
            dict(format='png'),
            icc_profile=icc(),
            cancelled=cancel,
        )
    assert target.read_bytes() == b'existing'
    assert list(tmp_path.iterdir()) == [target]


def test_export_watermark_preset_is_validated_and_keeps_image_precision(tmp_path):
    from ccraw import export_presets as ep

    source = np.full((20, 30, 3), 0.12345, np.float32)
    opts = dict(
        format='tiff', bit_depth=16, watermark={'enabled': True, 'sides': ['bottom'], 'fields': []}
    )
    ep.export_rgb(tmp_path / 'border.tif', source, opts, icc_profile=icc())
    output = tifffile.imread(tmp_path / 'border.tif')
    assert output.shape == (23, 30, 3)
    np.testing.assert_array_equal(output[:20], np.full((20, 30, 3), 8090, np.uint16))
    for watermark in [{'size': float('nan')}, {'unknown': 1}, {'enabled': 'yes'}]:
        with pytest.raises(ValueError):
            ep.validate({'watermark': watermark})


@pytest.mark.parametrize('space', ['sRGB', 'Adobe RGB', 'Display P3', 'ProPhoto RGB', 'Rec.2020'])
def test_export_space_alias_profile_generation(tmp_path, space):
    from ccraw import export_presets as ep
    from ccraw.imaging import colorspaces

    ep.export_rgb(
        tmp_path / 'profile.tif',
        np.full((1, 1, 3), 0.5, np.float32),
        dict(format='tiff', bit_depth=16, color_space=space),
    )
    with tifffile.TiffFile(tmp_path / 'profile.tif') as file:
        assert file.pages[0].tags[34675].value == colorspaces.profile_bytes(space)


def test_malformed_preset_fields_raise_value_errors():
    from ccraw import export_presets as ep

    for options in [
        {'format': {}},
        {'color_space': []},
        {'file_name_template': []},
        {'bit_depth': True},
    ]:
        with pytest.raises(ValueError):
            ep.validate(options)


def test_external_handoffs_reserve_distinct_names_under_parallel_encoding(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from ccraw.external_editor import prepare_handoff

    raw = tmp_path / 'original.nef'
    raw.write_bytes(b'original')
    pixels = np.full((100, 100, 3), 0.5, np.float32)
    with ThreadPoolExecutor(max_workers=6) as pool:
        copies = list(
            pool.map(
                lambda _: prepare_handoff(raw, pixels, tmp_path, {}, icc_profile=icc()), range(12)
            )
        )
    assert len({copy.path for copy in copies}) == 12
    assert all(copy.path.is_file() and copy.provenance_path.is_file() for copy in copies)
    assert raw.read_bytes() == b'original'


def test_external_editor_rejects_batch_command_interpreter(tmp_path):
    from ccraw.external_editor import prepare_handoff, launch

    source = tmp_path / 'photo.nef'
    source.write_bytes(b'original')
    batch = tmp_path / 'danger.cmd'
    batch.write_text('@echo off\n', encoding='ascii')
    handoff = prepare_handoff(
        source, np.zeros((1, 1, 3), np.float32), tmp_path, {}, icc_profile=icc()
    )
    with pytest.raises(ValueError):
        launch(handoff, batch)


def test_psd_resolution_resource_uses_fixed_point_ppi(tmp_path):
    from ccraw.interchange.psd import write_psd

    path = tmp_path / 'dpi.psd'
    write_psd(path, np.zeros((1, 1, 3), np.float32), icc_profile=icc(), resolution=144.5)
    data = path.read_bytes()
    # ResolutionInfo resource 1005, empty Pascal name, sixteen-byte payload.
    marker = b'8BIM\x03\xed\0\0\0\0\0\x10'
    offset = data.index(marker) + len(marker)
    assert struct.unpack_from('>IHHIHH', data, offset) == (9469952, 1, 1, 9469952, 1, 1)


def test_external_cancel_cleans_both_reserved_outputs(tmp_path):
    from ccraw.external_editor import prepare_handoff

    source = tmp_path / 'source.nef'
    source.write_bytes(b'original')
    with pytest.raises(InterruptedError):
        prepare_handoff(
            source,
            np.zeros((1, 1, 3), np.float32),
            tmp_path,
            {},
            icc_profile=icc(),
            cancelled=lambda: True,
        )
    assert list(tmp_path.iterdir()) == [source]


def test_xmp_portable_label_capture_time_and_camera_fields_roundtrip():
    from ccraw.interchange import xmp

    metadata = {
        'label': 'Red',
        'date': '2026-10-12T10:20:30+08:00',
        'make': 'Nikon',
        'camera_model': 'Z 6',
        'lens_model': '50 mm',
        'iso': '100',
        'exposure_time': '1/100',
        'f_number': '2.8',
        'focal_length': '50/1',
    }
    result = xmp.loads(xmp.merge(FOREIGN, metadata))
    assert result['metadata'] == {'rating': 2, **metadata}


def test_export_rejects_fake_or_non_rgb_icc(tmp_path):
    from ccraw import export_presets as ep

    fake = b'\0' * 128
    gray = bytearray(icc())
    gray[16:20] = b'GRAY'
    for profile in (fake, bytes(gray)):
        with pytest.raises(ValueError):
            ep.export_rgb(
                tmp_path / 'invalid.tif',
                np.zeros((1, 1, 3), np.float32),
                dict(format='tiff', bit_depth=16),
                icc_profile=profile,
            )
    assert not list(tmp_path.iterdir())


def test_safe_filename_never_overwrites_source_and_handles_same_directory(tmp_path):
    from ccraw import export_presets as ep

    source = tmp_path / 'photo.tif'
    source.write_bytes(b'original')
    options = {'format': 'tiff', 'bit_depth': 16}
    assert ep.output_path(tmp_path, source, options).name == 'photo-2.tif'
    with pytest.raises(ValueError):
        ep.output_path(tmp_path, source, {**options, 'collision': 'overwrite'})
    assert ep.output_path(tmp_path, source, {**options, 'collision': 'skip'}) is None
    assert source.read_bytes() == b'original'


def test_external_handoff_honors_professional_output_settings(tmp_path):
    from ccraw.external_editor import prepare_handoff

    source = tmp_path / 'source.nef'
    source.write_bytes(b'original')
    handoff = prepare_handoff(
        source,
        np.full((20, 30, 3), 0.4, np.float32),
        tmp_path,
        {},
        icc_profile=icc(),
        export_options={
            'format': 'tiff',
            'bit_depth': 16,
            'resolution': 144,
            'watermark': {'enabled': True, 'sides': ['bottom'], 'fields': []},
        },
    )
    with tifffile.TiffFile(handoff.path) as image:
        assert image.asarray().shape == (23, 30, 3)
        assert image.pages[0].tags['XResolution'].value == (144, 1)


def test_export_accepts_import_preference_creator_string(tmp_path):
    from ccraw import export_presets
    from ccraw.interchange import xmp
    import tifffile

    path = tmp_path / 'creator.tif'
    export_presets.export_rgb(
        path,
        np.full((2, 3, 3), 0.4, np.float32),
        {'format': 'tiff', 'bit_depth': 16},
        metadata={'creator': 'Photographer', 'copyright': 'Copyright', 'keywords': []},
    )
    with tifffile.TiffFile(path) as file:
        packet = xmp.loads(bytes(file.pages[0].tags[700].value))
    assert packet['metadata']['creator'] == ['Photographer']
