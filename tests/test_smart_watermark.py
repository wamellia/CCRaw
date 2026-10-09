import copy
import numpy as np
import pytest
from PySide6.QtTest import QTest

from ccraw import watermark, model, photo_metadata


def camera(make='Canon'):
    return photo_metadata.from_tags(
        dict(
            Make=make,
            Model='EOS R6',
            LensModel='RF 35mm F1.8',
            FNumber=2.8,
            ExposureTime=0.002,
            ISO=800,
            FocalLength=35,
            DateTimeOriginal='2025:07:12 18:05:06',
        )
    )


def test_smart_watermark_uses_brand_and_capture_parameters_without_missing_values():
    from ccraw.smart_watermark import identify_brand, metadata_blocks, brand_logo

    assert identify_brand(camera()) == 'canon'
    assert identify_brand({'brand': 'NIKON CORPORATION'}) == 'nikon'
    assert identify_brand({'brand': 'Unknown manufacturer'}) == ''
    assert brand_logo('canon').size[0] > brand_logo('canon').size[1]
    left, right = metadata_blocks(watermark.defaults(), camera())
    assert left == ['EOS R6', 'RF 35mm F1.8']
    assert right == ['35 mm · f/2.8 · 1/500 s · ISO 800', '2025-07-12 18:05:06']
    assert metadata_blocks(watermark.defaults(), {}) == ([], [])
    assert metadata_blocks(watermark.defaults(), {'body': None, 'iso': None}) == ([], [])


@pytest.mark.parametrize('preset', ['gallery', 'noir'])
def test_smart_watermark_keeps_float_photo_and_renders_brand(preset):
    rgb = np.full((600, 900, 3), 0.37123, np.float32)
    settings = dict(watermark.defaults(), enabled=True, layout='smart', preset=preset)
    output = watermark.apply(rgb, settings, camera())
    np.testing.assert_array_equal(output[:600], rgb)
    footer = output[600:]
    assert np.any((footer[:, :, 0] > 0.7) & (footer[:, :, 1] < 0.3) & (footer[:, :, 2] < 0.3))
    assert output.dtype == np.float32 and np.isfinite(output).all()
    assert watermark.validate(settings)['layout'] == 'smart'


def test_smart_recipe_is_portable_and_resolves_brand_per_photo(tmp_path):
    from ccraw.smart_watermark import identify_brand

    recipe = model.recipe()
    recipe['watermark'].update(enabled=True, layout='smart', smart_brand='auto')
    model.save_project(tmp_path / 'smart.ccraw', tmp_path / 'source.jpg', recipe)
    loaded = model.load_project(tmp_path / 'smart.ccraw')[1]['watermark']
    assert loaded['layout'] == 'smart' and loaded['smart_brand'] == 'auto'
    assert identify_brand(camera('Canon'), loaded['smart_brand']) == 'canon'
    assert identify_brand(camera('SONY'), loaded['smart_brand']) == 'sony'
    old = copy.deepcopy(recipe)
    old['watermark'].pop('layout')
    old['watermark'].pop('smart_brand')
    assert model.validate(old)['watermark']['layout'] == 'classic'
    with pytest.raises(ValueError):
        watermark.validate(dict(loaded, smart_brand='../logo'))


def test_smart_watermark_button_updates_recipe_and_preview(window):
    from test_ui import wait_until

    window.info['photo'] = camera()
    editor = window.watermark_editor
    assert editor.smart_button.text() == '智能水印'
    editor.smart_button.click()
    assert window.edits['watermark']['enabled']
    assert window.edits['watermark']['layout'] == 'smart'
    wait_until(lambda: not editor._preview_running and not editor._preview_timer.isActive())
    assert editor.preview.pixmap() is not None and not editor.preview.pixmap().isNull()
    editor.brand.setCurrentIndex(editor.brand.findData('nikon'))
    assert window.edits['watermark']['smart_brand'] == 'nikon'
    QTest.qWait(20)
