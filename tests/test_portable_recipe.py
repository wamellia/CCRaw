"""Cloud assembly preserves the already tested portable archive byte for byte."""

import zipfile

import pytest

from tools.packaging.portable_recipe import create, rebuild


def test_rebuilt_archive_is_byte_identical_despite_different_file_timestamps(tmp_path):
    root = tmp_path / 'original'
    root.mkdir()
    data = {'CCRaw.exe': b'executable' * 1000, '数据.json': b'payload', 'empty': b''}
    for name, value in data.items():
        (root / name).write_bytes(value)
    archive = tmp_path / 'original.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=4) as file:
        for name in data:
            file.write(root / name, 'CCRaw-Windows/' + name)
    recipe = create(archive)
    extracted = tmp_path / 'installer'
    extracted.mkdir()
    for name, value in data.items():
        (extracted / name).write_bytes(value)
    rebuilt = tmp_path / 'rebuilt.zip'
    rebuild(extracted, recipe, rebuilt)
    assert rebuilt.read_bytes() == archive.read_bytes()


def test_recipe_rejects_resource_outside_the_installed_application(tmp_path):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as file:
        file.writestr('CCRaw-Windows/../outside', b'outside')
    recipe = create(archive)
    (tmp_path / 'outside').write_bytes(b'outside')
    root = tmp_path / 'installer'
    root.mkdir()
    with pytest.raises(ValueError, match='path'):
        rebuild(root, recipe, tmp_path / 'rebuilt.zip')
    assert not (tmp_path / 'rebuilt.zip').exists()
