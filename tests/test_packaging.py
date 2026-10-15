"""one version number drives the window, installer and portable package."""

import re
from pathlib import Path
from ccraw import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_version_is_single_sourced():
    assert re.fullmatch(r'\d+\.\d+\.\d+', __version__)
    iss = (ROOT / 'installer.iss').read_text(encoding='utf-8')
    assert f'#define AppVersion "{__version__}"' in iss
    assert '{#AppVersion}' in iss and 'AppVersion=1.' not in iss
    # CCRaw has its own installer identity, independent from the upstream app.
    assert 'AppId={{BD85AE22-87A4-4B74-9C52-B975FEE1052C}' in iss
    package = (ROOT / 'tools' / 'packaging' / 'package_windows.py').read_text(encoding='utf-8')
    assert 'from ccraw import __version__' in package and '1.2.2' not in package
    assert not (ROOT / 'CHANGELOG.md').exists()
    assert not (ROOT / 'TEST_REPORT.md').exists()
    assert not (ROOT / 'SECURITY.md').exists()


def test_installer_replaces_previous_runtime_files():
    iss = (ROOT / 'installer.iss').read_text(encoding='utf-8')
    assert 'Type: filesandordirs; Name: "{app}\\_internal"' in iss


def test_frozen_builds_include_shared_control_resources():
    from PyInstaller.utils.hooks import collect_data_files

    collected = collect_data_files('ccraw', includes=['resources/**/*'])
    for relative in (
        'icons/check.svg',
        'icons/chevron.svg',
        'icons/chevron-dark.svg',
        'icons/chevron-up.svg',
        'icons/chevron-up-dark.svg',
        'generation/generation-templates.json',
        'generation/generation-01.jpg',
        'licenses/Photo-Butler-LICENSE.txt',
    ):
        resource = Path('ccraw/resources') / relative
        assert any(
            Path(source).name == resource.name and Path(destination) == resource.parent
            for source, destination in collected
        )
    assert "collect_data_files('ccraw', includes=['resources/**/*'])" in (
        ROOT / 'CCRaw.spec'
    ).read_text(encoding='utf8')
