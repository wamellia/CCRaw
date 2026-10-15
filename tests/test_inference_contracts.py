"""Model-free contracts for restoration routing and speech decoding."""

import threading
from types import SimpleNamespace

import numpy as np
import pytest

from ccraw import denoise, restoration, speech


@pytest.mark.parametrize(
    'algorithm', [denoise.process, restoration.denoise, restoration.drunet_denoise]
)
def test_zero_strength_returns_an_independent_copy_without_loading_a_model(monkeypatch, algorithm):
    rgb = np.random.default_rng(15).random((7, 9, 3), dtype=np.float32)

    def forbidden(*args, **kwargs):
        pytest.fail('zero strength loaded a model')

    monkeypatch.setattr(denoise, 'session', forbidden)
    monkeypatch.setattr(restoration, 'session', forbidden)
    result, name = algorithm(rgb, 0, False)
    np.testing.assert_array_equal(result, rgb)
    assert not np.shares_memory(result, rgb) and '强度 0' in name
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        algorithm(rgb, 0, False, cancel=cancelled)


@pytest.mark.parametrize(
    'algorithm,kind', [(restoration.denoise, 'denoise'), (restoration.drunet_denoise, 'drunet')]
)
def test_restoration_preserves_crop_noise_provider_and_progress_hooks(monkeypatch, algorithm, kind):
    rgb = np.full((9, 11, 3), 0.4, np.float32)
    region = (2, 3, 8, 7)
    progress = lambda *args: None
    cancel = threading.Event()
    calls = []

    def tiled(source, model, scale, cuda, callback, stop, noise=None, region=None):
        calls.append((source, model, scale, cuda, callback, stop, noise, region))
        return np.full((4, 6, 3), 0.2, np.float32), 'TestProvider'

    monkeypatch.setattr(restoration, 'tiled', tiled)
    # Keep lookup dynamic: callers replace the module hook to supply a shared reference.
    monkeypatch.setattr(denoise, 'noise_level', lambda source: 0.01)
    result, label = algorithm(rgb, 70, False, progress, cancel, region=region)
    source, model, scale, cuda, callback, stop, noise, crop = calls[0]
    assert source is rgb and model == kind and scale == 1 and cuda is False
    assert callback is progress and stop is cancel and crop == region
    # At this reference NAFNet uses 70% model + 30% original: .2*.7 + .4*.3 = .26.
    expected = 0.2 if kind == 'drunet' else 0.26
    np.testing.assert_allclose(result, expected, atol=3e-8)
    assert noise == (0.02 if kind == 'drunet' else None)
    assert label.endswith('TestProvider')
    with pytest.raises(ValueError, match='噪声参考'):
        algorithm(rgb, 70, False, noise_reference=np.nan)


def test_speech_ctc_repeats_blanks_and_special_tokens_preserve_word_boundaries():
    recognizer = speech.Recognizer.__new__(speech.Recognizer)
    recognizer.languages = {'auto': 0, 'en': 3}
    recognizer.with_itn = 1
    recognizer.tokens = {1: '▁Hello', 2: '<|en|>', 3: '▁world', 4: '!'}
    recognizer.features = lambda samples: np.zeros((2, 560), np.float32)
    ids = [2, 1, 1, 0, 1, 3, 3, 4]
    logits = np.zeros((1, len(ids) + 4, 5), np.float32)
    logits[0, np.arange(len(ids)) + 4, ids] = 1
    requests = []

    def run(outputs, inputs):
        requests.append(inputs)
        return [logits]

    recognizer.session = SimpleNamespace(run=run)
    assert recognizer.transcribe(np.zeros(1600, np.int16), 'en') == 'Hello Hello world!'
    assert requests[0]['language'].tolist() == [3]
    assert requests[0]['x_length'].tolist() == [2]
    assert requests[0]['x'].dtype == np.float32
