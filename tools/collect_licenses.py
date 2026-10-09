"""Collect actual installed dependency licenses into a reproducible release tree."""

import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def collect(destination=None):
    destination = Path(destination or ROOT / 'licenses')
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for distribution in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name, version = distribution.metadata['Name'], distribution.version
        package = destination / (name.replace('/', '_') + '-' + version)
        texts = []
        for item in distribution.files or []:
            lower = str(item).lower()
            if '__pycache__' in Path(lower).parts or Path(lower).suffix in (
                '.py',
                '.pyc',
                '.pyo',
                '.pyd',
                '.dll',
                '.so',
                '.nbc',
                '.nbi',
            ):
                continue
            if not (
                any(part in ('licenses', 'license') for part in Path(lower).parts)
                or Path(lower).name.startswith(('license', 'copying', 'notice', 'copyright'))
            ):
                continue
            source = Path(distribution.locate_file(item))
            if not source.is_file() or source.stat().st_size > 4 * 1024 * 1024:
                continue
            relative = Path(
                *[part for part in Path(str(item)).parts if part not in ('..', '.', '')]
            )
            target = package / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            texts.append(
                dict(
                    path=target.relative_to(destination).as_posix(),
                    sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                )
            )
        # Preserve declared license metadata even for wheels without a license text.
        if not texts:
            package.mkdir(exist_ok=True)
            target = package / 'LICENSE-METADATA.txt'
            target.write_text(
                'Package: '
                + name
                + '\nVersion: '
                + version
                + '\nLicense: '
                + (
                    distribution.metadata.get('License-Expression')
                    or distribution.metadata.get('License')
                    or 'See upstream project metadata'
                )
                + '\n',
                encoding='utf-8',
            )
            texts.append(
                dict(
                    path=target.relative_to(destination).as_posix(),
                    sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                )
            )
        records.append(dict(name=name, version=version, files=texts))
    (destination / 'manifest.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    print(f'Collected license records for {len(records)} installed distributions.')
    return records


if __name__ == '__main__':
    collect()
