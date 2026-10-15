"""The merge worker owns its reserved output through success, error and cancellation."""

import threading

import cv2
import numpy as np
import pytest
from PIL import Image

from ccraw import dng, engine, merge, merge_dialog, model


def records(tmp_path):
    result = []
    for index in range(2):
        path = tmp_path / f'frame-{index}.png'
        Image.new('RGB', (48, 32), (80, 120, 160)).save(path)
        result.append(dict(path=str(path), edits=model.recipe(), initialized=True))
    return result


def test_preview_worker_does_not_reserve_a_destination(tmp_path):
    inputs = records(tmp_path)
    result = merge_dialog.run_request(
        inputs, dict(kind='focus', preview=True, align=False), tmp_path / 'absent'
    )
    assert result['image'].shape == (32, 48, 3)
    assert 'path' not in result and not list(tmp_path.glob('*.dng'))


def test_worker_exports_a_separate_readable_copy_and_keeps_recipe(tmp_path):
    inputs = records(tmp_path)
    inputs[0]['edits']['watermark']['enabled'] = True
    result = merge_dialog.run_request(
        inputs, dict(kind='focus', requested_reference=0, preview=False, align=False), tmp_path
    )
    assert 'image' not in result
    assert result['watermark']['enabled']
    assert dng.is_rendered(result['path'])
    actual = dng.read(result['path'])
    expected = engine.to_linear(np.full((32, 48, 3), (80 / 255, 120 / 255, 160 / 255), np.float32))
    np.testing.assert_allclose(actual, expected, atol=1 / 65535)


@pytest.mark.parametrize('cancelled', [False, True])
def test_worker_removes_partial_or_cancelled_copy_without_touching_existing_files(
    tmp_path, monkeypatch, cancelled
):
    inputs = records(tmp_path)
    original = tmp_path / 'frame-0-景深合成.dng'
    original.write_bytes(b'existing output')
    cancel = threading.Event()
    export = engine.export_image

    def interrupted_export(path, *args, **kwargs):
        if cancelled:
            export(path, *args, **kwargs)
            cancel.set()
        else:
            path.write_bytes(b'incomplete DNG')
            raise OSError('disk failure')

    monkeypatch.setattr(engine, 'export_image', interrupted_export)
    with pytest.raises(InterruptedError if cancelled else OSError):
        merge_dialog.run_request(
            inputs,
            dict(kind='focus', requested_reference=0, preview=False, align=False),
            tmp_path,
            cancel=cancel,
        )
    assert original.read_bytes() == b'existing output'
    assert list(tmp_path.glob('*.dng')) == [original]


def test_low_texture_alignment_reuses_reference_and_keeps_forward_transform(monkeypatch):
    # Two smooth blobs do not supply the ten SIFT matches required for an
    # affine, so both translated frames must exercise real ECC registration.
    yy, xx = np.mgrid[:180, :240]
    light = (
        0.2
        + 0.5 * np.exp(-((xx - 85) ** 2 + (yy - 78) ** 2) / 1200)
        + 0.15 * np.exp(-((xx - 168) ** 2 + (yy - 123) ** 2) / 650)
    )
    image = np.repeat(light[:, :, None], 3, axis=2).astype(np.float32)
    images = [image] + [
        cv2.warpAffine(
            image,
            np.float32([[1, 0, x], [0, 1, y]]),
            (240, 180),
            borderMode=cv2.BORDER_REFLECT,
        )
        for x, y in [(5, -3), (-4, 2)]
    ]
    reference_reads = []
    normalize = merge.normalized

    def normalized(value):
        if value is image:
            reference_reads.append(value)
        return normalize(value)

    monkeypatch.setattr(merge, 'normalized', normalized)
    matrices = merge.stack_transforms(images, 0)
    np.testing.assert_allclose(matrices[1][:2, 2], [-5, 3], atol=0.06)
    np.testing.assert_allclose(matrices[2][:2, 2], [4, -2], atol=0.06)
    assert len(reference_reads) == 2  # One SIFT pass and one shared ECC reference.
