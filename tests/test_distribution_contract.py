"""Portable identity, legacy projects, safe persistence and privacy boundaries."""

import json
import os
import re
import subprocess
import sys
import pytest
from ccraw import host, model


def test_release_identity_is_independent():
    from ccraw import __version__, branding

    assert re.fullmatch(r'\d+\.\d+\.\d+', __version__)
    assert branding.NAME == 'CCRaw'


def test_data_and_cache_can_be_isolated_without_user_profile(monkeypatch, tmp_path):
    monkeypatch.setenv('CCRAW_DATA_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('CCRAW_CACHE_DIR', str(tmp_path / 'cache'))
    assert host.data_folder() == tmp_path / 'state'
    assert host.cache_folder() == tmp_path / 'cache'
    assert host.log_folder() == tmp_path / 'state' / 'logs'


def test_asset_override_is_used_for_models_and_metadata(monkeypatch, tmp_path):
    from ccraw import resources

    monkeypatch.setenv('CCRAW_ASSET_DIR', str(tmp_path))
    assert resources.asset_path('models', 'skyseg.onnx') == tmp_path / 'models' / 'skyseg.onnx'
    with pytest.raises(ValueError):
        resources.asset_path('..', 'outside')


def test_base_font_and_license_are_available_without_optional_models(monkeypatch, tmp_path):
    from ccraw import resources
    from PIL import ImageFont

    monkeypatch.delenv('CCRAW_ASSET_DIR', raising=False)
    monkeypatch.setattr(resources, 'asset_root', lambda: tmp_path)
    assert resources.asset_path('OFL.txt').is_file()
    font = ImageFont.truetype(str(resources.asset_path('NotoSansSC.ttf')), 16)
    assert font.getlength('CCRaw 图像') > 0


@pytest.mark.parametrize('identity', ['LUMEN RAW', 'Lumen ARW', 'CCRaw'])
def test_legacy_project_load_preserves_recipe_and_new_save_identity(tmp_path, identity):
    raw = tmp_path / 'photo.png'
    raw.write_bytes(b'photo')
    recipe = model.recipe()
    recipe['adjustments']['exposure'] = 0.75
    project = tmp_path / 'legacy.lumen'
    project.write_text(
        json.dumps(dict(application=identity, source='photo.png', edits=recipe)), encoding='utf-8'
    )
    source, edits = model.load_project(project)
    assert source == str(raw.resolve()) and edits == recipe
    new = tmp_path / 'photo.ccraw'
    model.save_project(new, raw, edits)
    assert json.loads(new.read_text(encoding='utf-8'))['application'] == 'CCRaw'


def test_legacy_preset_keeps_its_look(tmp_path):
    recipe = model.recipe()
    recipe['grading']['midtones'] = [210, 35]
    old = tmp_path / 'look.lumenpreset'
    old.write_text(
        json.dumps(
            dict(
                application='Lumen preset',
                version=1,
                name='Old look',
                look=model.extract_look(recipe),
            )
        )
    )
    preset = model.load_preset(old)
    assert preset['look']['grading']['midtones'] == [210, 35]


def test_atomic_write_failure_keeps_previous_project_and_removes_temporary(monkeypatch, tmp_path):
    from ccraw.persistence import atomic_write_json

    path = tmp_path / 'project.ccraw'
    path.write_text('previous')

    def fail(*args):
        raise OSError('replace unavailable')

    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError):
        atomic_write_json(path, {'value': 2})
    assert path.read_text() == 'previous'
    assert list(tmp_path.iterdir()) == [path]


def test_json_reader_rejects_oversized_or_nonobject_documents(tmp_path):
    from ccraw.persistence import read_json_object

    path = tmp_path / 'data.json'
    path.write_text('[]')
    with pytest.raises(ValueError):
        read_json_object(path)
    path.write_text('{"long":"abcdefgh"}')
    with pytest.raises(ValueError):
        read_json_object(path, max_bytes=4)


def test_atomic_writer_limits_final_utf8_bytes_before_changing_file(tmp_path):
    from ccraw.persistence import atomic_write_json

    path = tmp_path / 'saved.json'
    path.write_text('previous')
    with pytest.raises(ValueError):
        atomic_write_json(path, {'value': '中文' * 10}, max_bytes=30)
    assert path.read_text() == 'previous'
    assert list(tmp_path.iterdir()) == [path]


def test_log_redaction_includes_tracebacks_and_url_credentials():
    from ccraw.logs import redact

    message = 'File "C:\\Users\\private-user\\Photos\\secret.jpg" Authorization: Bearer sk-private-secret '
    message += 'https://account:password@example.test/x?api_key=hidden-key'
    clean = redact(message)
    assert all(
        value not in clean
        for value in ('private-user', 'secret.jpg', 'sk-private-secret', 'password', 'hidden-key')
    )


@pytest.mark.parametrize(
    'data', [[], {'providers': []}, {'provider': ['invalid']}, {'reasoning': []}, {'reasoning': {}}]
)
def test_malformed_optional_settings_cannot_prevent_startup(tmp_path, data):
    from ccraw import nl_edit

    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    settings = nl_edit.load_settings(path)
    assert settings['provider'] == nl_edit.default_settings()['provider']


def test_malformed_success_response_does_not_display_configured_key(monkeypatch):
    from ccraw import nl_edit

    key = 'private-test-token'
    service = nl_edit.active(nl_edit.default_settings())
    service.update(api='openai', key=key)
    monkeypatch.setattr(nl_edit, '_post', lambda *a, **kw: {'error': {'message': key}})
    with pytest.raises(nl_edit.ServiceError) as error:
        nl_edit.complete(service, [{'role': 'user', 'content': 'Edit the photo'}])
    assert key not in str(error.value)


@pytest.mark.parametrize(
    'url', ['http://remote.example.test/v1', 'https://user:secret@example.test/v1']
)
def test_remote_plaintext_and_userinfo_are_rejected_before_network(url):
    from ccraw.nl_edit import validate_endpoint

    with pytest.raises(ValueError):
        validate_endpoint(url)


@pytest.mark.parametrize(
    'url',
    [
        'http://localhost:11434',
        'http://127.0.0.1:1234/v1',
        'http://[::1]:8080/v1',
        'http://192.168.1.10:8000/v1',
        'https://api.example.test/v1',
    ],
)
def test_explicit_local_or_tls_endpoints_remain_available(url):
    from ccraw.nl_edit import validate_endpoint

    assert validate_endpoint(url) == url


def test_version_command_does_not_start_qt():
    result = subprocess.run(
        [sys.executable, '-m', 'ccraw', '--version'], capture_output=True, text=True, timeout=10
    )
    from ccraw import __version__

    assert result.returncode == 0 and result.stdout.strip() == f'CCRaw {__version__}'


def test_legacy_album_reopens_and_saves_as_ccraw(window, tmp_path, monkeypatch):
    from test_ui import wait_until

    w = window
    monkeypatch.setattr(w, 'confirm_close_library', lambda: True)
    recipe = model.recipe()
    recipe['adjustments']['exposure'] = 0.65
    album = tmp_path / 'old.lumenalbum'
    album.write_text(
        json.dumps(
            dict(
                application='Lumen album',
                version=1,
                documents=[
                    dict(source=w.source_path, edits=recipe, snapshots=[], initialized=True)
                ],
            )
        ),
        encoding='utf-8',
    )
    w.open_album(album)
    wait_until(lambda: not w.loading and not w.jobs and not w.timer.isActive())
    assert w.edits['adjustments']['exposure'] == 0.65
    target = tmp_path / 'new.ccrawalbum'
    assert w.save_album(path=target)
    assert json.loads(target.read_text(encoding='utf-8'))['application'] == 'CCRaw album'
