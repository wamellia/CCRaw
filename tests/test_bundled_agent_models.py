"""Desktop builds read bundled models without writing into the application folder."""

import hashlib
import sys

import pytest

from ccraw.photo_agent import models


@pytest.fixture
def model_paths(monkeypatch, tmp_path):
    user = tmp_path / 'user' / 'agent-models'
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    specs = [
        dict(name=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        for name, data in [('image.onnx', b'image-model'), ('tokens.json', b'tokens')]
    ]
    monkeypatch.setattr(models, 'MANIFEST', {'files': specs})
    monkeypatch.setattr(models.host, 'data_folder', lambda: user.parent)
    monkeypatch.setattr(models, 'asset_path', lambda *args: bundle, raising=False)
    monkeypatch.delenv('CCRAW_AGENT_MODEL_DIR', raising=False)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    for name, data in [('image.onnx', b'image-model'), ('tokens.json', b'tokens')]:
        (bundle / name).write_bytes(data)
    return user, bundle


def test_fresh_desktop_uses_complete_bundle_without_creating_user_directory(model_paths):
    user, bundle = model_paths
    assert models.model_root() == bundle
    assert not user.exists()


def test_download_destination_remains_in_user_data(model_paths):
    user, _ = model_paths
    assert models.model_root(writable=True) == user


def test_explicit_model_override_never_falls_back_to_bundle(model_paths, monkeypatch, tmp_path):
    override = tmp_path / 'override'
    monkeypatch.setenv('CCRAW_AGENT_MODEL_DIR', str(override))
    assert models.model_root() == override


def test_complete_user_install_takes_precedence(model_paths):
    user, bundle = model_paths
    user.mkdir(parents=True)
    for path in bundle.iterdir():
        (user / path.name).write_bytes(path.read_bytes())
    assert models.model_root() == user


def test_incomplete_bundle_does_not_claim_to_supply_models(model_paths):
    user, bundle = model_paths
    (bundle / 'image.onnx').unlink()
    assert models.model_root() == user


def test_source_mode_keeps_user_model_directory(model_paths, monkeypatch):
    user, _ = model_paths
    monkeypatch.setattr(sys, 'frozen', False)
    assert models.model_root() == user


def test_staging_copies_only_public_manifest_files(model_paths, tmp_path):
    from tools.packaging.prepare_agent_models import prepare

    _, source = model_paths
    (source / 'settings.json').write_text('private provider configuration', encoding='utf-8')
    destination = tmp_path / 'staged'
    result = prepare(source, destination)
    assert result == {'files': 2, 'bytes': 17}
    assert {p.name for p in destination.iterdir()} == {'image.onnx', 'tokens.json'}
    assert (destination / 'image.onnx').read_bytes() == b'image-model'


def test_staging_rejects_corrupt_models_before_creating_destination(model_paths, tmp_path):
    from tools.packaging.prepare_agent_models import prepare

    _, source = model_paths
    (source / 'image.onnx').write_bytes(b'corrupt')
    destination = tmp_path / 'staged'
    with pytest.raises(ValueError, match='image.onnx'):
        prepare(source, destination)
    assert not destination.exists()
