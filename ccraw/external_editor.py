"""Safe external editor handoff using rendered copies and argv, never a shell.

The UI may poll ``reload_if_changed`` after a filesystem notification. A changed
copy is returned as a derived image with its provenance; it is never written to
the source photo or silently substituted into the source document.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np

from . import export_presets


@dataclass
class Handoff:
    path: Path
    original: Path
    provenance_path: Path
    fingerprint: str


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_handoff(
    original,
    rgb,
    directory,
    recipe,
    *,
    format='tiff',
    bit_depth=16,
    color_space='srgb',
    icc_profile=None,
    metadata=None,
    cancelled=None,
    export_options=None,
):
    original = Path(original).resolve()
    if not original.is_file():
        raise ValueError('source photograph does not exist')
    if format not in ('tiff', 'psd'):
        raise ValueError('external editor copy must be TIFF or PSD')
    options = dict(
        format=format,
        bit_depth=bit_depth,
        color_space=color_space,
        file_name_template='{stem}-external',
        collision='unique',
    )
    if export_options is not None:
        options = export_presets.validate(export_options)
        if options['format'] not in ('tiff', 'psd'):
            raise ValueError('external editor copy must be TIFF or PSD')
        options.update(file_name_template='{stem}-external', collision='unique')
        bit_depth, color_space = options['bit_depth'], options['color_space']
    destination = export_presets.output_path(directory, original, options)
    # Both files belong to the derived copy. Record complete recipe only here,
    # outside portable image metadata, for reliable application provenance.
    record = {
        'application': 'CCRaw external editor',
        'version': 1,
        'source': str(original),
        'source_sha256': _hash(original),
        'recipe': recipe,
        'output_space': color_space,
        'bit_depth': bit_depth,
    }
    packet = json.dumps(record, ensure_ascii=False, allow_nan=False)
    # Claim both sibling names exclusively before encoding. Parallel handoffs
    # must never replace another task's image or orphan provenance sidecar.
    base = destination
    for index in range(1, 1000000):
        destination = base if index == 1 else base.with_name(f'{base.stem}-{index}{base.suffix}')
        provenance_path = destination.with_suffix(destination.suffix + '.ccraw.json')
        try:
            destination.open('xb').close()
        except FileExistsError:
            continue
        try:
            provenance_path.open('xb').close()
        except FileExistsError:
            destination.unlink()
            continue
        except BaseException:
            destination.unlink()
            raise
        break
    else:
        raise ValueError('too many external editor copy collisions')
    try:
        export_presets.export_rgb(
            destination,
            rgb,
            options,
            icc_profile=icc_profile,
            metadata=metadata,
            provenance={'source': str(original)},
            cancelled=cancelled,
        )
        export_presets.atomic_write(
            provenance_path, lambda temp: temp.write_text(packet, encoding='utf-8'), cancelled
        )
    except BaseException:
        destination.unlink(missing_ok=True)
        provenance_path.unlink(missing_ok=True)
        raise
    return Handoff(destination.resolve(), original, provenance_path.resolve(), _hash(destination))


def launch(handoff, executable, arguments=None):
    if not isinstance(handoff, Handoff):
        raise ValueError('a prepared handoff is required')
    arguments = ['{file}'] if arguments is None else arguments
    if not isinstance(arguments, (list, tuple)) or len(arguments) > 64:
        raise ValueError('external editor arguments must be an array')
    if any(not isinstance(arg, str) or '\0' in arg or len(arg) > 32768 for arg in arguments):
        raise ValueError('invalid external editor argument')
    if not isinstance(executable, (str, Path)) or '\0' in str(executable):
        raise ValueError('invalid external editor executable')
    program = shutil.which(str(executable)) or str(Path(executable).resolve())
    if not Path(program).is_file():
        raise ValueError('external editor executable does not exist')
    # Forbid command interpreters even when configured through the UI.
    if Path(program).suffix.lower() in ('.bat', '.cmd', '.ps1', '.vbs') or Path(
        program
    ).stem.lower() in ('cmd', 'powershell', 'pwsh', 'sh', 'bash', 'wscript', 'cscript'):
        raise ValueError('configure an editor executable, not a command interpreter')
    if not handoff.path.is_file() or handoff.path.resolve() == handoff.original.resolve():
        raise ValueError('handoff copy is unavailable or aliases the source')
    argv = [program] + [arg.replace('{file}', str(handoff.path)) for arg in arguments]
    if not any('{file}' in arg for arg in arguments):
        argv.append(str(handoff.path))
    return subprocess.Popen(argv, shell=False, cwd=str(handoff.path.parent))


def reload_if_changed(handoff, loader=None):
    fingerprint = _hash(handoff.path)
    if fingerprint == handoff.fingerprint:
        return None
    if loader is None:
        if handoff.path.suffix.lower() in ('.tif', '.tiff'):
            import tifffile

            with tifffile.TiffFile(handoff.path) as image:
                pixels = image.asarray()
                profile = image.pages[0].tags.get(34675)
                profile = profile.value if profile else None
            if (
                pixels.ndim != 3
                or pixels.shape[-1] not in (3, 4)
                or pixels.dtype not in (np.uint8, np.uint16)
            ):
                raise ValueError('external TIFF must contain 8/16-bit RGB pixels')
            rgb = pixels[..., :3].astype(np.float32) / np.iinfo(pixels.dtype).max
            loaded = {'rgb': rgb, 'icc_profile': profile}
        else:
            from .interchange.psd import read_psd

            loaded = read_psd(handoff.path)
    else:
        loaded = loader(handoff.path)
    if not isinstance(loaded, dict) or 'rgb' not in loaded:
        raise ValueError('external image loader must return rgb and image metadata')
    export_presets.validate_rgb(loaded['rgb'])
    loaded.update(
        path=str(handoff.path),
        derived_from=str(handoff.original),
        provenance_path=str(handoff.provenance_path),
    )
    handoff.fingerprint = fingerprint
    return loaded
