"""Create CCRaw's own offline asset archive and its SHA-256 manifest."""

import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ccraw import __version__


def main():
    output = ROOT / 'release-assets'
    output.mkdir(exist_ok=True)
    archive = output / f'CCRaw-{__version__}-RuntimeAssets.zip'
    excluded = {'runtime-assets.json', 'speech-assets.json'}
    files = [
        p
        for p in sorted((ROOT / 'assets').rglob('*'))
        if p.is_file() and p.name not in excluded and '__pycache__' not in p.parts
    ]
    records = []
    for path in files:
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        records.append(
            dict(path=path.relative_to(ROOT).as_posix(), bytes=path.stat().st_size, sha256=digest)
        )
    temporary = archive.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=4) as bundle:
        for path in files:
            bundle.write(path, path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(temporary) as bundle:
        if problem := bundle.testzip():
            raise ValueError('Corrupt resource: ' + problem)
    temporary.replace(archive)
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    manifest = dict(
        version=__version__,
        bundle=dict(file=archive.name, url='', bytes=archive.stat().st_size, sha256=digest),
        files=records,
    )
    (ROOT / 'assets/runtime-assets.json').write_text(
        json.dumps(manifest, indent=2), encoding='utf-8'
    )
    print(
        json.dumps(
            dict(
                archive=archive.name,
                files=len(records),
                bytes=archive.stat().st_size,
                sha256=digest,
            )
        )
    )


if __name__ == '__main__':
    main()
