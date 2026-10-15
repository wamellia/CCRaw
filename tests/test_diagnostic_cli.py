"""Grouped diagnostics retain the published application command-line routes."""

import importlib
import sys

import pytest

from ccraw import bootstrap


@pytest.mark.parametrize(
    'module,flag,inputs,expected',
    [
        ('ccraw.diagnostics', '--smoke-test', ['photo.png', 'out'], ('photo.png', 'out')),
        ('ccraw.diagnostics.merge', '--merge-test', ['out', 'photo.png'], ('out', 'photo.png')),
        (
            'ccraw.diagnostics.workflow',
            '--workflow-test',
            ['out', 'a.nef', 'b.nef'],
            ('out', 'a.nef', ['b.nef']),
        ),
        ('ccraw.diagnostics.release', '--release-test', ['out', 'a.nef'], ('out', ['a.nef'])),
        (
            'ccraw.photo_agent.diagnostics',
            '--agent-smoke-test',
            ['photos', 'out'],
            ('photos', 'out'),
        ),
    ],
)
def test_grouped_diagnostic_commands_keep_arguments_and_exit_code(
    monkeypatch, tmp_path, module, flag, inputs, expected
):
    import faulthandler

    calls = []
    target = importlib.import_module(module)
    monkeypatch.setattr(target, 'run', lambda *args: calls.append(args) or 7)
    monkeypatch.setattr(sys, 'argv', ['ccraw', flag, *inputs])
    monkeypatch.setattr(faulthandler, 'enable', lambda *args: None)
    monkeypatch.chdir(tmp_path)
    stdout, stderr = sys.stdout, sys.stderr
    try:
        with pytest.raises(SystemExit) as result:
            bootstrap.main()
        assert result.value.code == 7
        assert calls == [expected]
    finally:
        if sys.stdout is not stdout:
            sys.stdout.close()
        sys.stdout, sys.stderr = stdout, stderr
