"""Final artifact contracts for the unified current and build1521 pipelines."""

import copy

import cv2
import numpy as np
import pytest

from ccraw import watermark
from ccraw import watermark_runtime


PHOTO = dict(
    body='NIKON Z_7',
    brand='Nikon',
    lens='NIKKOR Z 35mm',
    focal='35 mm',
    focal_equivalent='52 mm',
    aperture='f/2.8',
    shutter='1/500 s',
    iso='ISO 800',
    date='2025-07-12 18:05:06',
    folder='Travel',
    filename='IMG_0123.NEF',
)


@pytest.mark.parametrize('key', watermark.LAYOUT_TYPES)
def test_every_layout_preview_is_the_area_filtered_float_export(key):
    # Nonconstant RGB exercises the shadow/rounding/blur alpha paths that a
    # uniform photo cannot reveal, and includes values beyond sRGB's range.
    y, x = np.mgrid[:137, :211].astype(np.float32)
    rgb = np.stack((x / 190 - 0.03, y / 120, 0.21723 + x / 400), axis=2)
    original = rgb.copy()
    settings = dict(
        watermark.defaults(), enabled=True, template=key, pipeline=watermark.template_pipeline(key)
    )
    exported = watermark.apply(rgb, settings, PHOTO)
    preview = watermark.preview(rgb, settings, PHOTO, limit=97)
    expected = cv2.resize(exported, preview.shape[1::-1], interpolation=cv2.INTER_AREA)
    np.testing.assert_allclose(preview, expected, atol=2e-6, rtol=0)
    np.testing.assert_array_equal(rgb, original)
    assert watermark.dimensions(rgb.shape, settings, PHOTO) == exported.shape[1::-1]


def test_default_standard2_proxy_uses_physical_pixels_without_full_photo_canvas(monkeypatch):
    settings = dict(watermark.defaults(), enabled=True)
    rgb = np.full((600, 900, 3), 0.37123, np.float32)
    exported = watermark.apply(rgb, settings, PHOTO)
    expected_size = (320, round(exported.shape[0] * 320 / exported.shape[1]))
    expected = cv2.resize(exported, expected_size, interpolation=cv2.INTER_AREA)
    create = watermark_runtime.PILImage.new

    def bounded(mode, size, *args, **kwargs):
        if mode in ('RGB', 'RGBA'):
            assert tuple(size) not in (rgb.shape[1::-1], exported.shape[1::-1])
        return create(mode, size, *args, **kwargs)

    monkeypatch.setattr(watermark_runtime.PILImage, 'new', bounded)
    proxy = np.full((60, 90, 3), 0.37123, np.float32)
    actual = watermark.preview(proxy, settings, PHOTO, limit=320, final_shape=rgb.shape)
    np.testing.assert_allclose(actual, expected, atol=2e-6, rtol=0)


def test_exif_overrides_feed_derived_legacy_values_brand_and_focal_switch():
    settings = dict(
        watermark.defaults(),
        equivalent_focal=False,
        metadata=dict(
            CameraModelName='R5',
            CameraMake='Canon',
            ISO=1600,
            FNumber=8,
            FocalLength=50,
            FocalLengthIn35mmFormat=200,
        ),
    )
    context = watermark.template_context(settings, PHOTO, (600, 900, 3))
    exif = context['exif']
    assert exif['CameraMake'] == 'Canon'
    assert exif['CameraMake_CameraModel'] == 'Canon R5'
    assert exif['Param'] == '50mm f/8 1/500s ISO1600'
    assert exif['FocalLengthIn35mmFormat'] == '50 mm'
    assert context['auto_logo']() == 'watermark-brand-canon.png'
    settings['equivalent_focal'] = True
    assert (
        watermark.template_context(settings, PHOTO, (600, 900, 3))['exif'][
            'FocalLengthIn35mmFormat'
        ]
        == '200 mm'
    )


@pytest.mark.parametrize(
    'node',
    [
        {'processor_name': 'resize', 'scale': float('nan')},
        {'processor_name': 'shadow', 'shadow_radius': True},
        {'processor_name': 'margin', 'bogus': 4},
        {'processor_name': 'rich_text', 'trim': 'false'},
        {'processor_name': 'image', 'path': ['C:/Users/private.png']},
        {'processor_name': 'image', 'path': 42},
        {'processor_name': 'blur', 'buffer_path': ['../private.png']},
        {'processor_name': 'alignment', 'offsets': '[1, 2]'},
        {'processor_name': 'alignment', 'select': '[-1]'},
        {'processor_name': 'rich_text', 'font_path': '../private.ttf'},
        {'processor_name': 'rich_text', 'text': '{{exif.__class__}}'},
        {'processor_name': 'rich_text', 'text': "{{'x' * 1000000000}}"},
    ],
)
def test_pipeline_rejects_invalid_types_unknown_properties_and_unsafe_handles(node):
    with pytest.raises(ValueError):
        watermark.validate(dict(watermark.defaults(), pipeline=[node]))


def test_dimension_preflight_rejects_huge_gradient_before_numpy_meshgrid(monkeypatch):
    settings = dict(
        watermark.defaults(),
        enabled=True,
        pipeline=[
            dict(
                processor_name='gradient_color',
                width=30000,
                height=30000,
                start_color='black',
                end_color='white',
            )
        ],
    )

    def forbid(*args, **kwargs):
        raise AssertionError('unbounded gradient grid allocated')

    monkeypatch.setattr(np, 'meshgrid', forbid)
    with pytest.raises(ValueError):
        watermark.apply(np.ones((4, 6, 3), np.float32), settings, PHOTO)


def test_builtin_fonts_and_variant_logo_handles_are_available():
    assert {
        'Roboto-Regular.ttf',
        'Roboto-Medium.ttf',
        'Roboto-Bold.ttf',
        'Roboto-Light.ttf',
    } <= set(watermark.FONTS)
    assert {
        'watermark-brand-sony_dark.png',
        'watermark-brand-panasonic2.png',
        'watermark-brand-olympus_white_gold.png',
    } <= set(watermark.RESOURCE_HANDLES)
    for name in watermark.FONTS:
        if name != 'SpecialAlphabets P04 Regular.otf':
            assert watermark_runtime.font_path(name).is_file()


def test_optional_watermark_position_can_be_removed_and_restored():
    settings = dict(
        watermark.defaults(),
        enabled=True,
        template='standard1',
        pipeline=watermark.template_pipeline('standard1'),
    )
    original = copy.deepcopy(settings['pipeline'][0]['left_bottom'])
    del settings['pipeline'][0]['left_bottom']
    output = watermark.apply(np.full((120, 180, 3), 0.3, np.float32), settings, PHOTO)
    settings['pipeline'][0]['left_bottom'] = original
    restored = watermark.apply(np.full((120, 180, 3), 0.3, np.float32), settings, PHOTO)
    assert output.shape == restored.shape
    assert not np.array_equal(output, restored)


def test_first_processor_can_load_a_selected_builtin_resource():
    rgb = np.zeros((100, 200, 3), np.float32)
    settings = dict(
        watermark.defaults(),
        enabled=True,
        pipeline=[
            dict(processor_name='blur', blur_radius=0, buffer_path=['watermark-brand-nikon.png'])
        ],
    )
    output = watermark.apply(rgb, settings, PHOTO)
    assert output.shape[1::-1] == watermark.brand_logo('nikon').size
    assert np.any(output > 0)


def test_explicit_segment_height_is_adjustable_without_changing_group_defaults():
    base = dict(
        processor_name='multi_rich_text',
        height=20,
        text_segments=[dict(text='Nikon', color='black')],
    )
    rgb = np.zeros((100, 200, 3), np.float32)
    settings = dict(watermark.defaults(), enabled=True, pipeline=[base])
    common = watermark.apply(rgb, settings, PHOTO)
    base['text_segments'][0]['height'] = 45
    larger = watermark.apply(rgb, settings, PHOTO)
    assert common.shape[0] == 20
    assert larger.shape[0] == 45


@pytest.mark.parametrize(
    'text',
    [
        "{{ 'a'.replace('a','aaaaa').replace('a','aaaaa').replace('a','aaaaa').replace('a','aaaaa').replace('a','aaaaa').replace('a','aaaaa').replace('a','aaaaa') }}",
        "{{ ('a'|replace('a','aaaaa')|replace('a','aaaaa')|replace('a','aaaaa')|replace('a','aaaaa')|replace('a','aaaaa')|replace('a','aaaaa')|replace('a','aaaaa')) }}",
    ],
)
def test_expression_expansion_is_bounded_before_allocating_large_strings(text):
    settings = dict(
        watermark.defaults(),
        enabled=True,
        pipeline=[dict(processor_name='rich_text', text=text, height=20)],
    )
    with pytest.raises(ValueError, match='过长'):
        watermark.apply(np.zeros((100, 200, 3), np.float32), settings, PHOTO)


def test_large_preview_cache_tracks_photo_and_mask_changes_without_aliasing():
    settings = dict(watermark.defaults(), enabled=True)
    rgb = np.full((80, 120, 3), 0.13, np.float32)
    first = watermark.preview(rgb, settings, PHOTO, limit=160, final_shape=(4000, 6000, 3))
    repeated = watermark.preview(
        rgb.copy(), settings, PHOTO, limit=160, final_shape=(4000, 6000, 3)
    )
    np.testing.assert_array_equal(first, repeated)
    saved = first.copy()
    rgb[:] = 0.8
    changed = watermark.preview(rgb, settings, PHOTO, limit=160, final_shape=(4000, 6000, 3))
    assert np.max(np.abs(changed - first)) > 0.5
    settings['pipeline'][0]['border_radius'] = 500
    rounded = watermark.preview(rgb, settings, PHOTO, limit=160, final_shape=(4000, 6000, 3))
    assert not np.array_equal(changed, rounded)
    settings['pipeline'][1]['shadow_color'] = 'red'
    red = watermark.preview(rgb, settings, PHOTO, limit=160, final_shape=(4000, 6000, 3))
    assert not np.array_equal(rounded, red)
    np.testing.assert_array_equal(first, saved)


def test_text_asset_cache_is_not_modified_by_in_place_overlay_processor():
    rgb = np.zeros((100, 200, 3), np.float32)
    text = dict(processor_name='rich_text', text='Watermark', height=25, color='white')
    settings = dict(watermark.defaults(), enabled=True, pipeline=[text])
    before = watermark.apply(rgb, settings, PHOTO)
    overlay = dict(
        settings,
        pipeline=[
            text,
            {
                'processor_name': 'watermark_with_timestamp',
                'height': 8,
                'text_segments': [{'text': 'Changed', 'color': 'red'}],
            },
        ],
    )
    changed = watermark.apply(rgb, overlay, PHOTO)
    assert not np.array_equal(before, changed)
    np.testing.assert_array_equal(watermark.apply(rgb, settings, PHOTO), before)


def test_explicit_logo_resource_can_disable_or_replace_an_imported_default():
    import base64
    import io
    from PIL import Image

    stream = io.BytesIO()
    Image.new('RGBA', (16, 16), 'red').save(stream, format='PNG')
    settings = dict(
        watermark.defaults(),
        enabled=True,
        template='standard1',
        pipeline=watermark.template_pipeline('standard1'),
    )
    settings['logos']['right'] = base64.b64encode(stream.getvalue()).decode('ascii')
    rgb = np.full((240, 360, 3), 0.4, np.float32)
    imported = watermark.apply(rgb, settings, PHOTO)
    settings['pipeline'][0]['right_logo'] = ''
    hidden = watermark.apply(rgb, settings, PHOTO)
    assert not np.array_equal(imported, hidden)
    settings['pipeline'][0]['right_logo'] = 'watermark-brand-canon.png'
    replaced = watermark.apply(rgb, settings, PHOTO)
    assert not np.array_equal(imported, replaced)
    assert not np.array_equal(hidden, replaced)


def test_old_smart_logo_roles_are_preserved_when_migrating_to_standard2():
    import base64
    import io
    from PIL import Image

    stream = io.BytesIO()
    Image.new('RGBA', (2, 2), 'red').save(stream, format='PNG')
    encoded = base64.b64encode(stream.getvalue()).decode('ascii')
    classic = watermark.validate({'layout': 'classic', 'camera_logo': encoded})
    smart = watermark.validate({'layout': 'smart', 'camera_logo': encoded})
    assert classic['logos']['left'] == encoded
    assert smart['logos']['right'] == encoded


def test_capture_metadata_is_literal_and_not_evaluated_as_another_template():
    rgb = np.zeros((30, 60, 3), np.float32)
    settings = dict(
        watermark.defaults(),
        enabled=True,
        pipeline=[
            {
                'processor_name': 'rich_text',
                'text': '{{exif.CameraModelName}}',
                'height': 25,
            }
        ],
    )
    actual = watermark.apply(rgb, settings, {'body': '{{vw(2)}}'})
    settings['pipeline'][0]['text'] = '{{"{{vw(2)}}"}}'
    expected = watermark.apply(rgb, settings, {})
    np.testing.assert_array_equal(actual, expected)
