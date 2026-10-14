import copy

import numpy as np
import pytest

from ccraw import engine, model


def test_wide_output_does_not_clip_p3_to_srgb():
    from ccraw.imaging import colorspaces as cs

    p3 = np.broadcast_to(np.array([0.1, 0.95, 0.1], np.float32), (7, 9, 3)).copy()
    source = cs.convert_linear(cs.decode(p3, 'Display P3'), 'Display P3', 'sRGB')
    recipe = model.recipe()
    recipe['process'] = 2
    out = engine.render_output(source, recipe, color_space='Display P3')
    np.testing.assert_allclose(out, p3, atol=5e-5)
    assert engine.process(source, recipe).min() < 0


def test_professional_neutral_keeps_legacy_mono_exact():
    source = np.random.default_rng(8).random((8, 10, 3), dtype=np.float32)
    recipe = model.recipe()
    recipe['monochrome'] = True
    expected = engine.monochrome(engine.to_srgb(source))
    np.testing.assert_array_equal(engine.process(source, recipe), expected)


def test_component_group_opacity_applies_to_all_components():
    m = model.new_mask('linear', 1)
    m.update(opacity=20, start=[0, 0], end=[1, 0])
    part = model.new_mask('linear', 2)
    part.update(start=[1, 0], end=[0, 0])
    m['components'] = [{'op': 'add', 'mask': part}]
    assert engine.mask_alpha(m, (8, 10, 3)).max() <= 0.200001


def test_selective_sync_keeps_camera_and_supports_explicit_geometry():
    from ccraw.domain.sync import transfer

    a, b = model.recipe(), model.recipe()
    a['professional']['detail']['radius'] = 2
    a['professional']['geometry']['vertical'] = 30
    b['white_balance']['kelvin'] = 5400
    b['white_balance']['camera_kelvin'] = 5000
    b['crop'] = [0.1, 0.1, 0.9, 0.9]
    expected = copy.deepcopy(b)
    out = transfer(a, b, ['professional.detail'])
    assert out['professional']['detail']['radius'] == 2
    assert out['crop'] == expected['crop'] and out['white_balance'] == expected['white_balance']
    assert out['professional']['geometry']['vertical'] == 0
    assert transfer(a, b, ['professional.geometry'])['professional']['geometry']['vertical'] == 30
    with pytest.raises(ValueError):
        transfer(a, b, ['unknown'])


def test_agent_point_and_red_eye_schema():
    from ccraw.photo_agent.controls import prepare

    out = prepare(
        model.recipe(),
        {
            'professional': {
                'point_color': [{'rgb': [0.1, 0.2, 0.3], 'hue': 20}],
                'red_eye': [{'center': [0.3, 0.4]}],
            }
        },
    )
    assert out['professional']['point_color'][0]['hue'] == 20
    assert out['professional']['red_eye'][0]['amount'] == 100
    assert prepare(out, {'process': 2})['process'] == 2
    with pytest.raises(ValueError):
        prepare(out, {'process': 3})


def test_agent_composes_geometry_and_luminance_masks():
    from ccraw.photo_agent.controls import prepare

    out = prepare(
        model.recipe(),
        {
            'masks': [
                {
                    'kind': 'radial',
                    'start': [0.1, 0.1],
                    'end': [0.9, 0.9],
                    'adjustments': {'exposure': 0.3},
                    'components': [
                        {
                            'op': 'intersect',
                            'mask': {'kind': 'luminance', 'luminance_range': [30, 80]},
                        }
                    ],
                }
            ]
        },
    )
    mask = out['masks'][0]
    assert mask['components'][0]['mask']['luminance_range'] == [30, 80]
    assert mask['adjustments']['exposure'] == 0.3


@pytest.mark.parametrize('process', [1, 2])
def test_professional_halo_region_matches_frame(process):
    source = np.random.default_rng(28).random((160, 170, 3), dtype=np.float32)
    recipe = model.recipe()
    recipe['process'] = process
    recipe['professional']['detail'].update(amount=65, radius=2.3, luminance=30, color=25)
    frame = engine.process(source, recipe, apply_crop=False)
    tile = engine.process_region(source, recipe, rect=(62, 54, 92, 88))
    np.testing.assert_allclose(tile, frame[54:88, 62:92], atol=2e-5)


def test_optical_point_mapping_matches_raster():
    from ccraw.imaging.professional import source_point, display_point, remap_map

    recipe = model.recipe()['professional']
    recipe['geometry'].update(vertical=30, horizontal=-20, flip_x=True, aspect=10)
    recipe['optics']['distortion'] = 10
    mx, my = remap_map((50, 70, 3), recipe)
    p = [30 / 69, 20 / 49]
    mapped = source_point(p, (50, 70, 3), recipe)
    np.testing.assert_allclose(mapped, [mx[20, 30] / 69, my[20, 30] / 49], atol=1e-6)
    np.testing.assert_allclose(display_point(mapped, (50, 70, 3), recipe), p, atol=1e-6)


def test_constrained_crop_excludes_invalid_geometric_border():
    from ccraw.imaging.professional import constrained_crop, remap_map

    pro = model.recipe()['professional']
    pro['geometry'].update(vertical=35, horizontal=-20)
    shape = (400, 600, 3)
    crop = constrained_crop(shape, pro)
    x0, y0, x1, y1 = map(float, crop)
    assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    for channel in (0, 1, 2):
        mx, my = remap_map(shape, pro, channel)
        region = np.s_[round(y0 * 400) : round(y1 * 400), round(x0 * 600) : round(x1 * 600)]
        assert mx[region].min() >= 0 and mx[region].max() <= 599
        assert my[region].min() >= 0 and my[region].max() <= 399


def test_wide_luminance_component_region_matches_frame():
    source = np.random.default_rng(83).random((64, 77, 3), dtype=np.float32)
    recipe = model.recipe()
    recipe['process'] = 2
    mask = model.new_mask('radial', 1)
    component = model.new_mask('luminance', 2)
    component['luminance_range'] = [20, 65]
    mask['components'] = [{'op': 'intersect', 'mask': component}]
    mask['adjustments']['exposure'] = 0.7
    recipe['masks'] = [mask]
    frame = engine.process(source, recipe, apply_crop=False)
    tile = engine.process_region(source, recipe, rect=(13, 12, 61, 49))
    np.testing.assert_allclose(tile, frame[12:49, 13:61], atol=2e-5)


def test_wide_cache_total_budget_and_clear():
    source = np.random.default_rng(13).random((16, 19, 3), dtype=np.float32)
    recipe = model.recipe()
    recipe['process'] = 2
    cache = engine.RenderCache(max_bytes=source.nbytes * 3)
    for amount in (10, 20, -20):
        recipe['adjustments']['saturation'] = amount
        engine.process(source, recipe, cache=cache)
        assert cache._stored() + cache._wide_cache._stored() <= cache.max_bytes
    inner = cache._wide_cache
    cache.clear()
    assert cache._stored() == 0 and inner._stored() == 0
    assert not hasattr(cache, '_wide_cache')


def test_mask_cache_rejects_single_oversized_entry():
    cache = engine.RenderCache(alpha_bytes=64)
    value = np.ones((8, 8), np.float32)
    assert cache.alpha('oversized', lambda: value) is value
    assert not cache._alphas
    for index in range(5):
        cache.alpha(index, lambda: np.ones((2, 2), np.float32))
    assert sum(v.nbytes for v in cache._alphas.values()) <= 64


def test_catalog_relink_updates_real_editor_document_and_recipe(window, tmp_path):
    from pathlib import Path
    from ccraw.catalog.manager import open_catalog_for_editor
    from test_ui import wait_until
    from PySide6.QtCore import Qt

    editor = window
    original = editor.source_path
    manager = open_catalog_for_editor(editor)
    photo = manager.catalog.query()['photos'][0]
    moved = tmp_path / 'moved' / Path(original).name
    moved.parent.mkdir()
    Path(original).rename(moved)
    manager.catalog.relink(photo['id'], moved, confirm_name_size=True)
    recipe = manager.project.preferences()['recipe:' + photo['id']]
    recipe['adjustments']['exposure'] = 1.25
    manager.project.preference('recipe:' + photo['id'], recipe)
    manager.close()
    wait_until(lambda: not editor.loading and not editor.render_running)
    editor.library_structure_dirty = False
    assert original not in editor.documents
    assert editor.source_path == str(moved)
    assert editor.documents[str(moved)]['path'] == str(moved)
    assert editor.edits['adjustments']['exposure'] == 1.25
    assert editor.filmstrip.item(0).data(Qt.ItemDataRole.UserRole) == str(moved)
    manager = open_catalog_for_editor(editor)
    assert manager.catalog.query()['total'] == 1
    manager.close()
    wait_until(lambda: not editor.loading and not editor.render_running)


def test_export_confirmation_remains_visible_with_scrollable_options(app):
    from PySide6.QtWidgets import QDialogButtonBox
    from ccraw.ui.professional_export import ProfessionalExportDialog

    dialog = ProfessionalExportDialog(None, '')
    dialog.resize(620, 470)
    dialog.show()
    app.processEvents()
    assert dialog.content_scroll.verticalScrollBar().maximum() > 0
    assert dialog.content_scroll.widget().isAncestorOf(dialog.format)
    assert dialog.content_scroll.widget().isAncestorOf(dialog.settings)
    assert dialog.format.isVisible() and dialog.settings.isVisible()
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert save.isVisible() and save.height() >= 30
    assert save.mapTo(dialog, save.rect().bottomRight()).y() < dialog.height()
    dialog.close()


def test_wide_memory_preflight_rejects_before_conversion(monkeypatch):
    from ccraw import large_image
    from ccraw.imaging import colorspaces as cs

    source = np.zeros((3, 4, 3), np.float32)
    recipe = model.recipe()
    recipe['process'] = 2
    monkeypatch.setattr(large_image, 'MAP_BYTES', 1)
    monkeypatch.setattr(large_image, 'available_memory', lambda: source.nbytes)
    monkeypatch.setattr(
        cs, 'convert_linear', lambda *_: pytest.fail('conversion precedes memory gate')
    )
    with pytest.raises(ValueError, match='内存'):
        engine.process(source, recipe)


def test_point_range_is_the_same_selection_as_the_edit():
    from ccraw.imaging.professional import point_mask, apply_color, automatic_bw, bw_target_band

    source = np.array([[[0.8, 0.1, 0.1], [0.1, 0.1, 0.8], [0.4, 0.4, 0.4]]], np.float32)
    point = dict(rgb=[0.8, 0.1, 0.1], luminance=30)
    weight = point_mask(source, point)
    changed = apply_color(source, dict(point_color=[point]))
    assert weight[0, 0] > 0.99 and weight[0, 1] == 0 and weight[0, 2] == 0
    np.testing.assert_array_equal(changed[:, 1:], source[:, 1:])
    assert bw_target_band([0.8, 0.1, 0.1]) == 0
    assert bw_target_band([0.4, 0.4, 0.4]) is None
    assert len(automatic_bw(source)) == 8


def test_black_white_target_drag_is_live_and_undoable(window):
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt, QPoint
    from test_ui import wait_until

    w = window
    w.tabs.setCurrentIndex(9)
    w.professional_editor.tabs.setCurrentIndex(4)
    w.professional_editor.bw_target.setChecked(True)
    before = copy.deepcopy(w.edits)
    position = w.canvas.screen([0.8, 0.2]).toPoint()
    QTest.mousePress(w.canvas, Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseMove(w.canvas, position + QPoint(0, -40))
    assert w.edits['monochrome']
    assert w.edits['professional']['bw_mixer'] != before['professional']['bw_mixer']
    QTest.mouseRelease(w.canvas, Qt.MouseButton.LeftButton, pos=position + QPoint(0, -40))
    wait_until(lambda: not w.render_running and not w.timer.isActive())
    w.undo(-1)
    assert w.edits == before


def test_compact_professional_page_has_real_editing_space(window, app):
    w = window
    w.resize(1240, 670)
    app.processEvents()
    w.tabs.setCurrentIndex(9)
    assert all(button.isVisible() and button.height() >= 36 for button in w.tool_buttons)
    assert w.editor_categories.height() >= 175
    assert not w.professional_editor.tabs.isVisible()
    w.tool_select.setCurrentIndex(3)
    w.select_editor_section('crop')
    assert w.editor_categories.currentIndex() == 3 and w.canvas.tool == 'crop'
    w.resize(1240, 900)
    app.processEvents()
    assert all(button.isVisible() for button in w.tool_buttons)
    assert not w.tool_select.isVisible()
