"""Canonical semi-utils templates, portable metadata and branded rendering."""

import copy
import importlib.util
import numpy as np
import pytest
from ccraw import watermark, model, photo_metadata

TEMPLATE_IDS = {'standard1', 'standard2', 'center_logo', 'normal1', 'normal2', 'nikon_blur', 'blur'}


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


def test_default_and_brand_metadata_are_canonical():
    assert set(watermark.TEMPLATES) == TEMPLATE_IDS
    settings = watermark.defaults()
    assert settings['template'] == 'standard2' and not settings['enabled']
    assert settings['pipeline'] == watermark.template_pipeline('standard2')
    assert not hasattr(watermark, 'PRESETS') and not hasattr(watermark, 'lines')
    assert importlib.util.find_spec('ccraw.smart_watermark') is None
    assert watermark.identify_brand(camera()) == 'canon'
    assert watermark.identify_brand({'brand': 'NIKON CORPORATION'}) == 'nikon'
    assert watermark.identify_brand({'brand': 'Unknown manufacturer'}) == ''
    assert watermark.brand_logo('canon').width > watermark.brand_logo('canon').height
    exif = watermark.template_context(settings, camera(), (600, 900, 3))['exif']
    assert exif['CameraModelName'] == 'EOS R6' and exif['LensModel'] == 'RF 35mm F1.8'
    assert exif['FocalLengthIn35mmFormat'] == '35 mm' and exif['FNumber'] == '2.8'
    assert exif['ShutterSpeed'] == '1/500' and exif['ISO'] == '800'
    assert exif['DateTimeOriginal'] == '2025-07-12 18:05:06'


@pytest.mark.parametrize('template', sorted(TEMPLATE_IDS))
@pytest.mark.parametrize('photo', [camera(), {}], ids=['capture-metadata', 'missing-metadata'])
def test_every_current_template_executes_without_mutating_its_graph(template, photo):
    rgb = np.full((600, 900, 3), 0.37123, np.float32)
    settings = dict(
        watermark.defaults(),
        enabled=True,
        template=template,
        pipeline=watermark.template_pipeline(template),
    )
    before = copy.deepcopy(settings)
    original_pixels = rgb.copy()
    output = watermark.apply(rgb, settings, photo)
    assert output.dtype == np.float32 and np.isfinite(output).all()
    assert (output.shape[1], output.shape[0]) == watermark.dimensions(rgb.shape, settings, photo)
    assert output.shape[0] >= rgb.shape[0] and output.shape[1] >= rgb.shape[1]
    assert settings == before
    np.testing.assert_array_equal(
        rgb, original_pixels, err_msg='watermark rendering must not mutate the editor photo'
    )
    assert np.any(np.all(output == rgb[300, 450], axis=2)), (
        'the photograph must retain unquantized float pixels'
    )
    if template != 'normal2' or photo:
        assert output.shape != rgb.shape or np.any(output != rgb)
    if template == 'standard1':
        np.testing.assert_array_equal(output[:600, :900], rgb)
    elif template == 'center_logo':
        np.testing.assert_array_equal(output[:600, 12:912], rgb)


@pytest.mark.parametrize(
    'layout',
    [
        'square',
        'watermark_right_logo',
        'watermark_left_logo',
        'dark_watermark_right_logo',
        'dark_watermark_left_logo',
        'custom_watermark',
        'simple',
        'background_blur',
        'background_blur_with_white_border',
        'pure_white_margin',
    ],
)
def test_every_legacy_layout_has_a_canonical_graph_and_executes(layout):
    assert layout in watermark.LAYOUT_TYPES
    settings = dict(
        watermark.defaults(),
        enabled=True,
        template=layout,
        pipeline=watermark.template_pipeline(layout),
    )
    assert watermark.validate(settings) == settings
    rgb = np.full((600, 900, 3), 0.37123, np.float32)
    original_pixels = rgb.copy()
    output = watermark.apply(rgb, settings, camera())
    np.testing.assert_array_equal(rgb, original_pixels)
    assert output.dtype == np.float32 and np.isfinite(output).all()
    assert (output.shape[1], output.shape[0]) == watermark.dimensions(rgb.shape, settings, camera())
    assert output.shape != rgb.shape or np.any(output != rgb)


def test_branded_standard1_retains_the_original_float_photo():
    rgb = np.full((600, 900, 3), 0.37123, np.float32)
    settings = dict(
        watermark.defaults(),
        enabled=True,
        template='standard1',
        pipeline=watermark.template_pipeline('standard1'),
    )
    output = watermark.apply(rgb, settings, camera())
    np.testing.assert_array_equal(output[:600], rgb)
    footer = output[600:]
    assert np.any((footer[:, :, 0] > 0.7) & (footer[:, :, 1] < 0.3) & (footer[:, :, 2] < 0.3))


def test_template_recipe_is_portable_and_resolves_brand_per_photo(tmp_path):
    recipe = model.recipe()
    recipe['watermark'].update(enabled=True, brand='auto')
    model.save_project(tmp_path / 'template.ccraw', tmp_path / 'source.jpg', recipe)
    loaded = model.load_project(tmp_path / 'template.ccraw')[1]['watermark']
    assert loaded['template'] == 'standard2' and loaded['brand'] == 'auto'
    assert watermark.identify_brand(camera('Canon'), loaded['brand']) == 'canon'
    assert watermark.identify_brand(camera('SONY'), loaded['brand']) == 'sony'
    with pytest.raises(ValueError):
        watermark.validate(dict(loaded, brand='../logo'))


def test_template_selection_updates_recipe_and_preview(window):
    from test_ui import wait_until

    window.info['photo'] = camera()
    window.sidebar_button.setChecked(True)
    window.select_editor_section('watermark')
    editor = window.watermark_editor
    assert editor.layout_type.currentData() == 'standard2'
    editor.enabled.setChecked(True)
    editor.layout_type.setCurrentIndex(editor.layout_type.findData('standard1'))
    assert window.edits['watermark']['enabled']
    assert window.edits['watermark']['template'] == 'standard1'
    assert window.edits['watermark']['pipeline'] == watermark.template_pipeline('standard1')
    wait_until(lambda: not editor._preview_running and not editor._preview_timer.isActive())
    assert editor.preview.pixmap() is not None and not editor.preview.pixmap().isNull()
    editor.brand.setCurrentIndex(editor.brand.findData('nikon'))
    assert window.edits['watermark']['brand'] == 'nikon'
