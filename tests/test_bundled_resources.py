"""Source and packaged builds resolve the same grouped application resources."""

from pathlib import Path

from PIL import Image, ImageFont
import pytest

from ccraw import resources


def test_grouped_assets_load_without_external_runtime_bundle(monkeypatch, tmp_path):
    monkeypatch.delenv('CCRAW_ASSET_DIR', raising=False)
    monkeypatch.setattr(resources, 'asset_root', lambda: tmp_path)
    font_path = resources.asset_path('NotoSansSC.ttf')
    assert font_path.parent.name == 'fonts'
    assert ImageFont.truetype(str(font_path), 16).getlength('CCRaw') > 0
    with Image.open(resources.asset_path('ccraw.png')) as image:
        assert image.width > 0 and image.height > 0
    assert resources.asset_path('OFL.txt').parent.name == 'licenses'


def test_bundled_path_keeps_relative_paths_inside_package():
    assert resources.bundled_path('native', 'lcms2.dll').is_file()
    with pytest.raises(ValueError):
        resources.bundled_path('..', '..', 'outside.txt')


def test_frozen_collector_contains_every_grouped_resource():
    from PyInstaller.utils.hooks import collect_data_files

    root = Path(__file__).resolve().parents[1]
    collected = {
        (Path(destination) / Path(source).name).as_posix()
        for source, destination in collect_data_files('ccraw', includes=['resources/**/*'])
    }
    files = {
        p.relative_to(root).as_posix() for p in (root / 'ccraw/resources').rglob('*') if p.is_file()
    }
    assert files <= collected
    assert 'ccraw/resources/generation/generation-templates.json' in collected
    assert 'ccraw/resources/watermark/watermark-template-standard2.json' in collected
