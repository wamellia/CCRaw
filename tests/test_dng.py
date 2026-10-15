"""DNG reader conversion precision and real memory-map ownership."""

import numpy as np
import pytest
import tifffile

from ccraw import dng


@pytest.mark.parametrize('preview_limit', [None, 2])
def test_read_converts_full_and_area_preview_samples_exactly(tmp_path, preview_limit):
    values = np.arange(16, dtype=np.uint16).reshape(4, 4, 1) * 1000
    encoded = values + np.array([0, 100, 200], dtype=np.uint16)
    path = tmp_path / 'pixels.tif'
    tifffile.imwrite(path, encoded, photometric='rgb')
    actual = dng.read(path, preview_limit)
    samples = (
        encoded
        if preview_limit is None
        else np.array(
            [
                [[2500, 2600, 2700], [4500, 4600, 4700]],
                [[10500, 10600, 10700], [12500, 12600, 12700]],
            ],
            dtype=np.uint16,
        )
    )
    np.testing.assert_array_equal(actual, samples.astype(np.float32) / 65535)
    assert actual.dtype == np.float32


@pytest.mark.parametrize('outcome', ['success', 'format_error', 'resize_error'])
def test_read_closes_real_mapping_on_success_and_failure(tmp_path, monkeypatch, outcome):
    path = tmp_path / 'mapped.tif'
    invalid = outcome == 'format_error'
    tifffile.imwrite(
        path, np.zeros((5, 7, 3), dtype=np.float32 if invalid else np.uint16), photometric='rgb'
    )
    mappings = []
    memmap = tifffile.memmap

    def mapped(*args, **kwargs):
        result = memmap(*args, **kwargs)
        mappings.append(result)
        return result

    monkeypatch.setattr(tifffile, 'memmap', mapped)
    if invalid:
        with pytest.raises(ValueError, match='像素格式'):
            dng.read(path)
    elif outcome == 'resize_error':
        import cv2

        def failed_resize(*args, **kwargs):
            raise RuntimeError('resize failed')

        monkeypatch.setattr(cv2, 'resize', failed_resize)
        with pytest.raises(RuntimeError, match='resize failed'):
            dng.read(path, preview_limit=2)
    else:
        dng.read(path)
    assert mappings[0]._mmap.closed


def test_write_keeps_dimension_tags_and_capture_metadata(tmp_path):
    from ccraw import photo_metadata

    path = tmp_path / 'metadata.dng'
    photo = dict(body='Camera', iso='ISO 200')
    dng.write(path, np.full((9, 13, 3), 0.42, np.float32), photo, {'operation': 'focus'})
    assert dng.dimensions(path) == (13, 9)
    assert photo_metadata.embedded(path)['body'] == 'Camera'
    with tifffile.TiffFile(path) as file:
        tags = file.pages[0].tags
        assert tags[50720].value == (13, 9)
        assert tags[50829].value == (0, 0, 9, 13)
        assert tags[50708].value == dng.MODEL
