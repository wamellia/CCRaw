import copy

import pytest

from ccraw import model


def test_professional_blocks_are_present_and_roundtrip():
    recipe = model.recipe()
    assert 'professional' in recipe
    recipe['professional']['geometry']['vertical'] = 20
    assert model.validate(recipe)['professional']['geometry']['vertical'] == 20


def test_legacy_recipe_gets_neutral_professional_defaults():
    recipe = model.recipe()
    recipe.pop('professional', None)
    migrated = model.validate(recipe)
    assert migrated['professional']['geometry']['scale'] == 100
    assert migrated['professional']['point_color'] == []
    assert migrated['professional']['detail']['amount'] == 0


@pytest.mark.parametrize('value', [float('nan'), 101, '12', True])
def test_professional_rejects_invalid_numbers(value):
    recipe = model.recipe()
    recipe.setdefault('professional', {})['geometry'] = {'vertical': value}
    with pytest.raises(ValueError):
        model.validate(recipe)


def test_professional_schema_rejects_unknown_fields():
    recipe = model.recipe()
    recipe['professional'] = {'run_shell': 'bad'}
    with pytest.raises(ValueError):
        model.validate(recipe)


def test_professional_defaults_not_shared():
    left, right = model.recipe(), model.recipe()
    left['professional']['bw_mixer'][0] = 25
    assert right['professional']['bw_mixer'][0] == 0


def test_component_mask_saved_without_losing_soft_brush():
    recipe = model.recipe()
    mask = model.new_mask('linear', 1)
    component = model.new_mask('luminance', 1)
    mask['components'] = [dict(op='intersect', mask=component)]
    recipe['masks'] = [mask]
    loaded = model.validate(copy.deepcopy(recipe))
    assert loaded['masks'][0]['components'][0]['op'] == 'intersect'
