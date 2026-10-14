import copy
import threading
import numpy as np
import pytest
from ccraw import model, engine, watermark, photo_metadata, denoise, selection
from ccraw.ai_dialog import reserve_copy


@pytest.mark.parametrize(
    'suffix', ['.CRW', '.CR2', '.CR3', '.NEF', '.NRW', '.RAF', '.RW2', '.RAW', '.ARW']
)
def test_requested_raw_formats(suffix):
    assert suffix.lower() in engine.RAW_EXTENSIONS
    assert '*' + suffix.lower() in engine.PHOTO_FILTER


def test_watermark_reads_capture_metadata_and_unknowns():
    p = photo_metadata.from_tags(
        dict(
            Make='Canon',
            Model='EOS R',
            LensModel='SIGMA 35mm F1.4',
            DateTimeOriginal='2025:03:02 14:05:06',
            FNumber=2.8,
            ExposureTime=0.002,
            ISO=800,
        )
    )
    assert p['date'] == '2025-03-02 14:05:06' and p['shutter'] == '1/500 s'
    assert p['aperture'] == 'f/2.8' and p['iso'] == 'ISO 800' and p['lens_brand'] == 'SIGMA'
    context = watermark.template_context(watermark.defaults(), p, (180, 260, 3))
    assert context['exif']['CameraModelName'] == 'EOS R'
    assert context['exif']['LensModel'] == 'SIGMA 35mm F1.4'
    assert context['exif']['ShutterSpeed'] == '1/500'
    assert context['exif']['ISO'] == '800'
    empty_exif = watermark.template_context(watermark.defaults(), {}, (180, 260, 3))['exif']
    assert all(value is not None and value != 'None' for value in empty_exif.values())


@pytest.mark.parametrize('side', ['top', 'bottom', 'left', 'right'])
@pytest.mark.parametrize('color_space', ['sRGB', 'Display P3', 'Adobe RGB'])
def test_pipeline_margins_preserve_original_float_pixels(side, color_space):
    rgb = np.random.default_rng(4).uniform(-0.15, 1.2, (180, 260, 3)).astype(np.float32)
    pad = 25
    settings = dict(
        watermark.defaults(),
        enabled=True,
        pipeline=[{'processor_name': 'margin', f'{side}_margin': pad, 'margin_color': '#faf9f6'}],
    )
    output = watermark.apply(rgb, settings, {}, color_space=color_space)
    y = pad if side == 'top' else 0
    x = pad if side == 'left' else 0
    assert (output.shape[1], output.shape[0]) == watermark.dimensions(rgb.shape, settings)
    np.testing.assert_array_equal(output[y : y + 180, x : x + 260], rgb)
    assert output.dtype == np.float32 and np.isfinite(output).all()


def test_watermark_project_roundtrip_and_old_migration(tmp_path):
    r = model.recipe()
    r['watermark'].update(
        enabled=True, template='standard1', pipeline=watermark.template_pipeline('standard1')
    )
    node = r['watermark']['pipeline'][0]
    node['left_top']['text_segments'].append(
        dict(text='{{exif.Artist}}', color='#135724', is_bold=False)
    )
    node['left_margin'] = '{{vw(4)}}'
    r['watermark']['metadata']['Artist'] = 'A photographer'
    r['watermark']['equivalent_focal'] = False
    model.save_project(tmp_path / 'photo.ccraw', tmp_path / 'source.RAF', r)
    assert model.load_project(tmp_path / 'photo.ccraw')[1]['watermark'] == r['watermark']
    for version in range(1, 5):
        old = copy.deepcopy(r)
        old['version'] = version
        old.pop('watermark')
        assert model.validate(old)['watermark'] == watermark.defaults()
    assert model.apply_look(r, model.extract_look(model.recipe()))['watermark'] == r['watermark']
    for bad in [
        dict(r['watermark'], template='unknown'),
        dict(r['watermark'], pipeline=[dict(processor_name='unavailable')]),
        dict(r['watermark'], logos={'left': 'broken'}),
    ]:
        with pytest.raises((ValueError, TypeError)):
            watermark.validate(bad)


@pytest.mark.parametrize('layout', ['classic', 'smart'])
def test_old_watermark_settings_migrate_to_canonical_standard2(layout):
    old = dict(
        enabled=True,
        layout=layout,
        preset='travel',
        sides=['left', 'bottom'],
        size=25.0,
        fields=['body', 'lens'],
        smart_brand='nikon',
        camera_logo='',
        lens_logo='',
    )
    migrated = watermark.validate(old)
    assert migrated['enabled'] is True and migrated['template'] == 'standard2'
    assert migrated['brand'] == 'nikon'
    assert migrated['pipeline'] == watermark.template_pipeline('standard2')
    assert set(migrated) == set(watermark.defaults())
    assert not {'preset', 'sides', 'size', 'layout', 'fields', 'smart_brand'} & set(migrated)
    recipe = model.recipe()
    recipe['watermark'] = old
    assert model.validate(recipe)['watermark'] == migrated
    for invalid in [
        dict(old, size=float('nan')),
        dict(old, sides=['unknown']),
        dict(old, camera_logo='broken'),
    ]:
        with pytest.raises((ValueError, TypeError)):
            watermark.validate(invalid)


def test_custom_transparent_logo_survives_recipe_and_renders(tmp_path):
    import base64, io
    from PIL import Image

    emblem = Image.new('RGBA', (40, 40), (250, 20, 10, 0))
    emblem.paste((250, 20, 10, 255), (10, 10, 30, 30))
    buffer = io.BytesIO()
    emblem.save(buffer, format='PNG')
    r = model.recipe()
    graph = watermark.template_pipeline('standard1')
    graph[0].update(
        left_logo='{{left_logo}}',
        right_logo='',
        bottom_margin=100,
        top_margin=10,
        left_margin=10,
        right_margin=10,
    )
    r['watermark'].update(
        enabled=True,
        template='standard1',
        pipeline=graph,
        logos={
            'left': base64.b64encode(buffer.getvalue()).decode('ascii'),
            'right': '',
            'center': '',
        },
    )
    model.save_project(tmp_path / 'logo.ccraw', tmp_path / 'original.CR3', r)
    settings = model.load_project(tmp_path / 'logo.ccraw')[1]['watermark']
    rgb = np.full((240, 320, 3), 0.37, np.float32)
    result = watermark.apply(rgb, settings, {})
    np.testing.assert_array_equal(result[10:250, 10:330], rgb)
    border = result[250:]
    assert np.any((border[:, :, 0] > 0.9) & (border[:, :, 1] < 0.1))
    np.testing.assert_allclose(border[-1, -1], 1, atol=1e-7)


def test_metadata_survives_generated_linear_dng(tmp_path):
    path = tmp_path / 'photo-去杂色.dng'
    photo = photo_metadata.from_tags(
        dict(Make='Nikon', Model='Z 6', LensModel='NIKKOR Z 50mm f/1.8 S', ISO=640)
    )
    rgb = np.full((64, 90, 3), 0.4, np.float32)
    engine.export_image(path, rgb, photo=photo, provenance=dict(operation='denoise'))
    decoded, info = engine.load_image(path, None)
    assert info['photo'] == photo and not info['raw']
    np.testing.assert_allclose(engine.to_srgb(decoded), rgb, atol=0.0001)


def test_copy_collision_never_overwrites_original(tmp_path):
    first = reserve_copy(tmp_path, 'photo', 'super')
    first.write_bytes(b'preserve')
    second = reserve_copy(tmp_path, 'photo', 'super')
    assert first.name == 'photo-增强.dng' and second.name == 'photo-增强-2.dng'
    assert first.read_bytes() == b'preserve'
    assert reserve_copy(tmp_path, 'photo', 'denoise').name == 'photo-去杂色.dng'


def test_real_ffdnet_reduces_noise_and_has_no_tile_seams():
    y, x = np.mgrid[0:301, 0:307]
    clean = np.stack(
        [0.2 + 0.5 * x / 306, 0.2 + 0.5 * y / 300, np.full_like(x, 0.4, dtype=float)], axis=2
    ).astype(np.float32)
    noisy = np.clip(
        clean + np.random.default_rng(33).normal(0, 25 / 255, clean.shape).astype(np.float32), 0, 1
    )
    ticks = []
    output, backend = denoise.process(noisy, 100 / 3, False, lambda n, t: ticks.append((n, t)))
    assert 'CPU' in backend and ticks[-1][0] == ticks[-1][1]
    assert np.mean((output - clean) ** 2) < np.mean((noisy - clean) ** 2) * 0.2
    whole = np.pad(noisy, ((0, 1), (0, 1), (0, 0)), mode='edge')
    sigma = denoise.noise_level(noisy) * (100 / 3) / 35
    assert abs(denoise.noise_level(noisy) - 25 / 255) < 0.012
    result = (
        denoise.session(False)
        .run(
            None,
            dict(
                image=whole.transpose(2, 0, 1)[None].copy(),
                sigma=np.full((1, 1, 1, 1), sigma, np.float32),
            ),
        )[0][0]
        .transpose(1, 2, 0)
    )
    np.testing.assert_allclose(output, np.clip(result[:301, :307], 0, 1), atol=2e-5)
    # A preview must use the full photo's noise estimate, with matching results
    # away from the patch boundary (the neural receptive field).
    patch = noisy[32:224, 32:224]
    preview, _ = denoise.process(patch, 100 / 3, False, noise_reference=denoise.noise_level(noisy))
    np.testing.assert_allclose(preview[32:-32, 32:-32], output[64:192, 64:192], atol=2e-5)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        denoise.process(noisy, cancel=cancel)
    cancel.clear()
    with pytest.raises(InterruptedError):
        denoise.process(noisy, progress=lambda n, t: cancel.set(), cancel=cancel)


def test_subject_is_background_inverse_and_depth_is_finite():
    rgb = np.random.default_rng(2).random((100, 140, 3), dtype=np.float32)
    subject, _ = selection.automatic(rgb, 'subject')
    background, _ = selection.automatic(rgb, 'background')
    np.testing.assert_allclose(subject + background, 1, atol=1e-6)
    near, provider = selection.automatic(rgb, 'foreground')
    assert near.shape == rgb.shape[:2] and np.isfinite(near).all() and 'CPU' in provider
    for kind in ('subject', 'foreground'):
        r = model.recipe()
        mask = model.new_mask(kind, 1)
        mask['raster'] = selection.encode(near)
        r['masks'] = [mask]
        assert model.validate(r)['masks'][0]['kind'] == kind
