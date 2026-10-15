"""Windows startup, private user folders and supported compute providers."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ccraw import bootstrap, compute, host, white_balance


@pytest.mark.parametrize('platform', ['darwin', 'linux'])
def test_unsupported_platform_does_not_launch_editor(monkeypatch, capsys, platform):
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr(sys, 'argv', ['ccraw'])
    # Starting a real event loop would block; exercise the entry-point guard.
    monkeypatch.setitem(sys.modules, 'ccraw.app', SimpleNamespace(main=lambda: 0))
    assert bootstrap.main() == 2
    assert 'Windows' in capsys.readouterr().err


def test_windows_provider_plan_ignores_foreign_backend(monkeypatch):
    monkeypatch.setenv('CCRAW_COMPUTE', 'auto')
    assert (
        compute.provider_plan(
            ['CoreMLExecutionProvider', 'CPUExecutionProvider'], None, models=True
        )
        == []
    )


def test_windows_user_folders_have_standard_fallback(monkeypatch, tmp_path):
    for name in ('LOCALAPPDATA', 'CCRAW_DATA_DIR', 'CCRAW_CACHE_DIR'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    expected = tmp_path / 'AppData' / 'Local' / 'CCRaw'
    assert host.data_folder() == expected
    assert host.log_folder() == expected / 'logs'
    assert host.cache_folder() == expected / 'cache'
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'Local'))
    assert host.data_folder() == tmp_path / 'Local' / 'CCRaw'
    assert host.keys('Ctrl+Shift+Z') == 'Ctrl+Shift+Z'


def test_bundled_metadata_reader_is_optional_windows_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(white_balance.resources, 'asset_path', lambda _: tmp_path)
    assert white_balance.exiftool() is None
    executable = tmp_path / 'exiftool.exe'
    executable.write_bytes(b'')
    assert white_balance.exiftool() == [str(executable)]
