"""Stage only manifest-verified public Agent models for the Windows distribution."""

import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ccraw.photo_agent import models  # noqa: E402


def prepare(source=None, destination=None):
    source = Path(source) if source is not None else models.model_root(writable=True)
    destination = Path(destination) if destination is not None else ROOT / 'assets/agent-models'
    for spec in models.MANIFEST['files']:
        path = source / spec['name']
        if (
            not path.is_file()
            or path.stat().st_size != spec['bytes']
            or models.digest(path) != spec['sha256']
        ):
            raise ValueError(
                'Missing or invalid Agent model: '
                + spec['name']
                + '. Run python tools/models/agent_models.py before building.'
            )
    destination.mkdir(parents=True, exist_ok=True)
    for spec in models.MANIFEST['files']:
        target = destination / spec['name']
        if target.is_file() and models.digest(target) == spec['sha256']:
            continue
        temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            shutil.copyfile(source / spec['name'], temporary)
            if models.digest(temporary) != spec['sha256']:
                raise ValueError('Agent model changed while staging: ' + spec['name'])
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return dict(
        files=len(models.MANIFEST['files']), bytes=sum(s['bytes'] for s in models.MANIFEST['files'])
    )


if __name__ == '__main__':
    print(json.dumps(prepare()))
