"""Reproduce a portable ZIP from its verified installer and original ZIP metadata."""

import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import zipfile
import zlib


def digest(path):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def create(archive):
    archive = Path(archive)
    fields = (
        'filename',
        'date_time',
        'compress_type',
        'create_system',
        'create_version',
        'extract_version',
        'reserved',
        'volume',
        'internal_attr',
        'external_attr',
        'file_size',
        'CRC',
    )
    with zipfile.ZipFile(archive) as source:
        entries = []
        for info in source.infolist():
            if info.is_dir():
                raise ValueError('Portable archives contain files only.')
            entry = {name: getattr(info, name) for name in fields}
            entry.update(
                extra=base64.b64encode(info.extra).decode('ascii'),
                comment=base64.b64encode(info.comment).decode('ascii'),
            )
            entries.append(entry)
        comment = base64.b64encode(source.comment).decode('ascii')
    return dict(
        version=1,
        python='.'.join(map(str, sys.version_info[:3])),
        zlib=zlib.ZLIB_RUNTIME_VERSION,
        compression_level=4,
        comment=comment,
        sha256=digest(archive),
        bytes=archive.stat().st_size,
        entries=entries,
    )


def rebuild(root, recipe, output):
    root, output = Path(root).resolve(), Path(output)
    if recipe['version'] != 1 or recipe['zlib'] != zlib.ZLIB_RUNTIME_VERSION:
        raise ValueError('The ZIP recipe needs its original zlib runtime.')
    temporary = output.with_name(output.name + '.tmp')
    if output.exists() or temporary.exists():
        raise ValueError('Refusing to replace an existing archive.')
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(
            temporary,
            'w',
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=recipe['compression_level'],
            allowZip64=True,
        ) as target:
            target.comment = base64.b64decode(recipe['comment'], validate=True)
            for entry in recipe['entries']:
                name = PurePosixPath(entry['filename'])
                if (
                    name.is_absolute()
                    or name.parts[0] != 'CCRaw-Windows'
                    or '..' in name.parts
                    or '\\' in str(name)
                ):
                    raise ValueError('Invalid portable resource path.')
                path = root.joinpath(*name.parts[1:]).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise ValueError('Installer resource missing or changed: ' + str(name))
                size = path.stat().st_size
                if 'file_size' in entry and size != entry['file_size']:
                    raise ValueError('Installer resource size differs: ' + str(name))
                info = zipfile.ZipInfo(entry['filename'], tuple(entry['date_time']))
                info.file_size = size
                for key in (
                    'compress_type',
                    'create_system',
                    'create_version',
                    'extract_version',
                    'reserved',
                    'volume',
                    'internal_attr',
                    'external_attr',
                    'file_size',
                ):
                    if key in entry:
                        setattr(info, key, entry[key])
                info.extra = base64.b64decode(entry['extra'], validate=True)
                info.comment = base64.b64decode(entry['comment'], validate=True)
                info._compresslevel = recipe['compression_level']
                with path.open('rb') as source, target.open(info, 'w') as destination:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
                if 'CRC' in entry and info.CRC != entry['CRC']:
                    raise ValueError('Installer resource CRC differs: ' + str(name))
        if temporary.stat().st_size != recipe['bytes'] or digest(temporary) != recipe['sha256']:
            raise ValueError('Rebuilt portable ZIP does not match the original archive.')
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return dict(files=len(recipe['entries']), bytes=output.stat().st_size, sha256=recipe['sha256'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    make = sub.add_parser('create')
    make.add_argument('archive', type=Path)
    make.add_argument('recipe', type=Path)
    restore = sub.add_parser('rebuild')
    restore.add_argument('root', type=Path)
    restore.add_argument('recipe', type=Path)
    restore.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.action == 'create':
        data = create(args.archive)
        args.recipe.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        print(json.dumps({key: data[key] for key in ('bytes', 'sha256', 'python', 'zlib')}))
    else:
        print(
            json.dumps(
                rebuild(args.root, json.loads(args.recipe.read_text(encoding='utf-8')), args.output)
            )
        )


if __name__ == '__main__':
    main()
