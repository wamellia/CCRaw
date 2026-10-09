"""Explicit verified installation of optional local Photo Agent models."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ccraw.photo_agent.models import install_models, LocalModels


def main():
    if '--verify-only' not in sys.argv:
        install_models(progress=lambda percent, text: print(f'{percent}% {text}'))
    models = LocalModels()
    if not models.clip_ready or not models.face_ready:
        print('Local Photo Agent models are missing or failed checksum verification.')
        return 1
    print('Verified CLIP, tokenizer, YuNet and SFace.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
