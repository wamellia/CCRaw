import numpy as np

from ccraw import engine, model


def test_professional_geometry_is_rendered_and_region_matches():
    image = np.zeros((30, 40, 3), np.float32)
    image[:, :20, 0] = 0.4
    recipe = model.recipe()
    recipe['professional']['geometry']['flip_x'] = True
    output = engine.process(image, recipe, apply_crop=False)
    assert output[:, -10:, 0].mean() > output[:, :10, 0].mean()
    np.testing.assert_allclose(
        engine.process_region(image, recipe, rect=(5, 4, 20, 18)), output[4:18, 5:20], atol=2e-5
    )


def test_component_intersection_uses_both_masks():
    mask = model.new_mask('linear', 1)
    mask.update(start=[0, 0], end=[1, 0])
    part = model.new_mask('linear', 2)
    part.update(start=[0, 0], end=[0, 1])
    mask['components'] = [dict(op='intersect', mask=part)]
    combined = engine.mask_alpha(mask, (20, 20, 3))
    assert combined[1, 18] < 0.1
    assert combined[18, 18] > 0.8


def test_agent_professional_patch_is_discoverable_and_applied():
    from ccraw.photo_agent.controls import prepare, describe

    assert 'professional' in describe()['sections']
    recipe = prepare(model.recipe(), {'professional': {'optics': {'distortion': 12}}})
    assert recipe['professional']['optics']['distortion'] == 12


def test_professional_ui_is_live(app):
    from ccraw.app import MainWindow

    window = MainWindow()
    try:
        assert hasattr(window, 'professional_editor')
        window.source = np.full((24, 30, 3), 0.3, np.float32)
        window.info = {'width': 30, 'height': 24}
        slider = window.professional_editor.controls[('geometry', 'vertical')]
        slider.setValue(10)
        slider.changed.emit(10)
        assert window.edits['professional']['geometry']['vertical'] == 10
    finally:
        window.source = None
        window.close()
