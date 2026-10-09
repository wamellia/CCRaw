"""Check exact release pins without replacing an explicitly selected GPU runtime."""

from importlib import metadata
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
RUNTIMES = ('onnxruntime', 'onnxruntime-windowsml', 'onnxruntime-gpu', 'onnxruntime-directml')


def pins(path):
    result = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([\w.-]+)==([^\s]+)', line.strip())
        if match:
            result[match[1]] = match[2]
    return result


def check():
    installed = {
        d.metadata['Name'].lower().replace('_', '-'): d.version for d in metadata.distributions()
    }
    runtimes = [name for name in RUNTIMES if name in installed]
    if len(runtimes) != 1:
        raise RuntimeError('Install exactly one ONNX Runtime distribution: ' + str(runtimes))
    expected = pins(ROOT / 'requirements-lock.txt')
    if 'nanobot-ai' in installed:
        expected.update(pins(ROOT / 'requirements-agent-lock.txt'))
    if runtimes[0] != 'onnxruntime':
        expected.pop('onnxruntime')
        variant = (
            'requirements-directml.txt'
            if runtimes[0] == 'onnxruntime-windowsml'
            else 'requirements-gpu.txt'
        )
        if runtimes[0] == 'onnxruntime-directml':
            raise RuntimeError('This release supports the pinned Windows ML or CUDA variant.')
        expected.update(pins(ROOT / variant))
    mismatches = [
        f'{name}: expected {version}, installed {installed.get(name.lower().replace("_", "-"), "missing")}'
        for name, version in expected.items()
        if installed.get(name.lower().replace('_', '-')) != version
    ]
    if mismatches:
        raise RuntimeError('Release dependency pins differ:\n' + '\n'.join(mismatches))
    destination = ROOT / 'build/dependencies.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(dict(runtime=runtimes[0], installed=installed), indent=2), encoding='utf-8'
    )
    print(f'Verified {len(expected)} dependency pins; runtime {runtimes[0]}.')


if __name__ == '__main__':
    check()
