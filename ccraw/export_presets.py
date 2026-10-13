"""Validated export presets and atomic encoding of target-encoded RGB pixels.

The renderer owns color conversion. ``export_rgb`` receives pixels already encoded
in the preset's output space; an injected ICC must describe those pixels. This
boundary also allows the legacy renderer to retain its existing numerical path.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import string
import struct
import tempfile
import zlib

import cv2
import numpy as np
from PIL import Image, PngImagePlugin


FORMATS = {'jpeg': (8,), 'png': (8, 16), 'tiff': (8, 16), 'psd': (8, 16)}
EXTENSIONS = {'jpeg': '.jpg', 'png': '.png', 'tiff': '.tif', 'psd': '.psd'}
SPACES = ('srgb', 'adobe_rgb', 'display_p3', 'prophoto', 'rec2020')
PROFILE_NAMES = {
    'srgb': 'sRGB',
    'adobe_rgb': 'Adobe RGB',
    'display_p3': 'Display P3',
    'prophoto': 'ProPhoto RGB',
    'rec2020': 'Rec.2020',
}
POLICIES = ('all', 'all_except_camera', 'copyright', 'none')
_RESERVED = re.compile(r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)


def defaults():
    return dict(
        format='jpeg',
        bit_depth=8,
        color_space='srgb',
        intent='relative',
        quality=95,
        long_edge=0,
        resolution=300,
        output_sharpening='none',
        sharpening_amount='standard',
        metadata_policy='all',
        remove_location=False,
        file_name_template='{stem}',
        collision='unique',
        watermark={},
    )


def _number(value, key, lower, upper, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{key} must be a number')
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f'{key} must be between {lower} and {upper}')
    if integer and int(value) != value:
        raise ValueError(f'{key} must be an integer')


def _template(template):
    if not isinstance(template, str) or not 1 <= len(template) <= 180:
        raise ValueError('file_name_template must be a bounded filename')
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', template) and ':' not in template:
        raise ValueError('filename template contains a path or forbidden character')
    try:
        parts = list(string.Formatter().parse(template))
    except ValueError as exc:
        raise ValueError('invalid filename template') from exc
    for literal, field, spec, conversion in parts:
        if re.search(r'[<>:"/\\|?*\x00-\x1f]', literal):
            raise ValueError('filename template contains a path or forbidden character')
        if field is None:
            continue
        if field not in ('stem', 'date', 'seq') or conversion:
            raise ValueError('filename placeholders are stem, date, seq only')
        if spec and (field != 'seq' or not re.fullmatch(r'0?[1-9]d', spec)):
            raise ValueError('sequence format must be a width of 1 to 9 digits')
    return template


def validate(options=None):
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError('export preset must be an object')
    out = defaults()
    unknown = set(options) - set(out)
    if unknown:
        raise ValueError(f'unknown export fields: {sorted(unknown)}')
    out.update(deepcopy(options))
    if isinstance(out['format'], str):
        out['format'] = {'jpg': 'jpeg', 'tif': 'tiff'}.get(
            out['format'].lower(), out['format'].lower()
        )
    if isinstance(out['color_space'], str):
        key = re.sub(r'[\s_.-]', '', out['color_space']).lower()
        out['color_space'] = {
            'srgb': 'srgb',
            'adobergb': 'adobe_rgb',
            'displayp3': 'display_p3',
            'p3': 'display_p3',
            'prophoto': 'prophoto',
            'prophotorgb': 'prophoto',
            'rec2020': 'rec2020',
            'bt2020': 'rec2020',
        }.get(key, out['color_space'])
    choices = dict(
        format=tuple(FORMATS),
        color_space=SPACES,
        intent=('perceptual', 'relative', 'absolute', 'saturation'),
        output_sharpening=('none', 'screen', 'matte', 'glossy'),
        sharpening_amount=('low', 'standard', 'high'),
        metadata_policy=POLICIES,
        collision=('unique', 'overwrite', 'skip'),
    )
    for key, values in choices.items():
        if not isinstance(out[key], str) or out[key] not in values:
            raise ValueError(f'{key} must be one of {values}')
    _number(out['bit_depth'], 'bit_depth', 8, 16, True)
    if out['bit_depth'] not in FORMATS[out['format']]:
        raise ValueError('encoder does not support this format / bit depth combination')
    out['bit_depth'] = int(out['bit_depth'])
    _number(out['quality'], 'quality', 1, 100, True)
    _number(out['long_edge'], 'long_edge', 0, 30000, True)
    _number(out['resolution'], 'resolution', 1, 2400)
    if not isinstance(out['remove_location'], bool):
        raise ValueError('remove_location must be a boolean')
    if not isinstance(out['watermark'], dict):
        raise ValueError('watermark must be an object')
    if out['watermark']:
        from . import watermark

        if set(out['watermark']) - set(watermark.defaults()):
            raise ValueError('unknown watermark field')
        if 'enabled' in out['watermark'] and not isinstance(out['watermark']['enabled'], bool):
            raise ValueError('watermark enabled must be a boolean')
        out['watermark'] = watermark.validate(out['watermark'])
    _template(out['file_name_template'])
    return out


def file_name(source, options=None, seq=1, date=None):
    opts = validate(options)
    _number(seq, 'seq', 1, 999999999, True)
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', Path(source).stem).rstrip(' .')
    date = date or datetime.now()
    name = opts['file_name_template'].format(stem=stem, date=date.strftime('%Y%m%d'), seq=int(seq))
    if (
        not name
        or name in ('.', '..')
        or name != name.rstrip(' .')
        or _RESERVED.match(name)
        or len(name) > 200
    ):
        raise ValueError('unsafe or reserved filename')
    return name + EXTENSIONS[opts['format']]


def output_path(directory, source, options=None, seq=1, date=None):
    opts = validate(options)
    candidate = Path(directory) / file_name(source, opts, seq, date)
    same_original = candidate.resolve() == Path(source).resolve()
    if candidate.exists() and Path(source).exists():
        same_original = same_original or candidate.samefile(source)
    if same_original and opts['collision'] == 'overwrite':
        raise ValueError('an export must not replace its original')
    if not candidate.exists() or opts['collision'] == 'overwrite':
        return candidate
    if opts['collision'] == 'skip':
        return None
    for i in range(2, 1000000):
        unique = candidate.with_name(f'{candidate.stem}-{i}{candidate.suffix}')
        if not unique.exists():
            return unique
    raise ValueError('too many filename collisions')


def atomic_write(path, writer, cancelled=None):
    """Write through a unique sibling and replace only after success/cancel check."""
    path = Path(path)
    if cancelled and cancelled():
        raise InterruptedError('export cancelled')
    fd, temporary = tempfile.mkstemp(
        prefix='.' + path.stem + '-', suffix=path.suffix, dir=path.parent
    )
    os.close(fd)
    temp = Path(temporary)
    try:
        writer(temp)
        if cancelled and cancelled():
            raise InterruptedError('export cancelled')
        with temp.open('r+b') as file:
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path


def save(path, presets):
    if not isinstance(presets, dict) or len(presets) > 256:
        raise ValueError('presets must be an object with at most 256 entries')
    clean = {}
    for name, opts in presets.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError('preset name must contain 1 to 100 characters')
        clean[name] = validate(opts)
    packet = json.dumps({'version': 1, 'presets': clean}, allow_nan=False, ensure_ascii=False)
    atomic_write(path, lambda temp: temp.write_text(packet, encoding='utf-8'))


def load(path):
    path = Path(path)
    if not path.exists():
        return {}
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('export preset file too large')
    try:
        packet = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('invalid export preset file') from exc
    if not isinstance(packet, dict) or packet.get('version') != 1:
        raise ValueError('unsupported export preset version')
    presets = packet.get('presets')
    if not isinstance(presets, dict) or len(presets) > 256:
        raise ValueError('invalid export presets')
    return {name: validate(opts) for name, opts in presets.items()}


def apply_metadata_policy(metadata, policy='all', remove_location=False):
    if policy not in POLICIES:
        raise ValueError('unknown metadata policy')
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError('metadata must be an object')
    out = deepcopy(metadata or {})
    if policy == 'none':
        return {}
    if policy == 'copyright':
        out = {k: v for k, v in out.items() if k in ('creator', 'copyright', 'rights', 'credit')}
    elif policy == 'all_except_camera':
        camera = {
            'exif',
            'camera',
            'camera_model',
            'body',
            'lens',
            'lens_model',
            'lens_brand',
            'aperture',
            'shutter',
            'iso',
            'focal',
            'brand',
            'make',
            'model',
            'serial_number',
            'exposure_time',
            'f_number',
            'focal_length',
        }
        out = {k: v for k, v in out.items() if k.lower() not in camera}
    if remove_location:
        out = {
            k: v
            for k, v in out.items()
            if not any(
                s in k.lower()
                for s in (
                    'gps',
                    'location',
                    'latitude',
                    'longitude',
                    'city',
                    'country',
                    'province',
                    'sublocation',
                )
            )
        }
        for key in ('exif', 'iptc'):
            if isinstance(out.get(key), dict):
                out[key] = apply_metadata_policy(out[key], 'all', True)
    return out


def validate_rgb(rgb):
    rgb = np.asarray(rgb)
    if (
        rgb.ndim != 3
        or rgb.shape[2] != 3
        or min(rgb.shape[:2]) < 1
        or max(rgb.shape[:2]) > 30000
        or rgb.size > 600_000_000
    ):
        raise ValueError('RGB image dimensions exceed supported bounds')
    if not np.issubdtype(rgb.dtype, np.floating) or not np.isfinite(rgb).all():
        raise ValueError('RGB pixels must be finite floating point encoded values')
    return np.ascontiguousarray(rgb, dtype=np.float32)


def apply_output_sharpening(rgb, target='none', amount='standard'):
    if target not in ('none', 'screen', 'matte', 'glossy') or amount not in (
        'low',
        'standard',
        'high',
    ):
        raise ValueError('invalid output sharpening option')
    rgb = validate_rgb(rgb)
    if target == 'none':
        return rgb.copy()
    sigma = {'screen': 0.5, 'matte': 1.0, 'glossy': 0.75}[target]
    strength = {'low': 0.35, 'standard': 0.65, 'high': 1.0}[amount]
    blur = cv2.GaussianBlur(rgb, (0, 0), sigma, borderType=cv2.BORDER_REFLECT_101)
    return np.ascontiguousarray(np.clip(rgb + strength * (rgb - blur), 0, 1), dtype=np.float32)


def _png_chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


def validate_icc_profile(profile):
    if not isinstance(profile, bytes) or not 132 <= len(profile) <= 16 * 1024 * 1024:
        raise ValueError('valid output RGB ICC bytes are required')
    size = struct.unpack_from('>I', profile)[0]
    if not 132 <= size <= len(profile) or profile[36:40] != b'acsp' or profile[16:20] != b'RGB ':
        raise ValueError('ICC profile must describe RGB data')
    count = struct.unpack_from('>I', profile, 128)[0]
    if count > 4096 or 132 + count * 12 > size:
        raise ValueError('invalid ICC tag table')
    for i in range(count):
        offset, length = struct.unpack_from('>II', profile, 136 + 12 * i)
        if offset < 128 or offset + length > size:
            raise ValueError('ICC tag extends beyond profile')
    return profile


def export_rgb(
    path, rgb, options=None, *, icc_profile=None, metadata=None, provenance=None, cancelled=None
):
    """Encode already target-encoded pixels. Metadata is embedded as CCRaw JSON,
    plus portable XMP in JPEG/PNG; TIFF uses its ImageDescription and XMP tag.
    Size and output sharpening operate after color encoding. An optional preset
    watermark adds borders through the existing watermark renderer; its sRGB
    margin colors are converted to the output space without touching the photo.
    """
    opts = validate(options)
    path = Path(path)
    suffixes = {
        'jpeg': ('.jpg', '.jpeg'),
        'png': ('.png',),
        'tiff': ('.tif', '.tiff'),
        'psd': ('.psd',),
    }
    if path.suffix.lower() not in suffixes[opts['format']]:
        raise ValueError('output extension does not match preset format')
    if cancelled and cancelled():
        raise InterruptedError('export cancelled')
    rgb = validate_rgb(rgb)
    if opts['long_edge'] and max(rgb.shape[:2]) > opts['long_edge']:
        scale = opts['long_edge'] / max(rgb.shape[:2])
        rgb = cv2.resize(
            rgb,
            (max(1, round(rgb.shape[1] * scale)), max(1, round(rgb.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
    rgb = apply_output_sharpening(rgb, opts['output_sharpening'], opts['sharpening_amount'])
    if opts['watermark'].get('enabled'):
        from . import watermark

        settings = opts['watermark']
        original_rgb = rgb
        rgb = watermark.apply(rgb, settings, metadata or {})
        if opts['color_space'] != 'srgb':
            from .imaging.colorspaces import convert

            rgb = convert(rgb, 'sRGB', PROFILE_NAMES[opts['color_space']])
            pad = max(1, round(min(original_rgb.shape[:2]) * settings['size'] / 100))
            left = pad if 'left' in settings['sides'] else 0
            top = pad if 'top' in settings['sides'] else 0
            rgb[top : top + original_rgb.shape[0], left : left + original_rgb.shape[1]] = (
                original_rgb
            )
        rgb = validate_rgb(rgb)
    if icc_profile is None:
        from .imaging.colorspaces import profile_bytes

        icc_profile = profile_bytes(PROFILE_NAMES[opts['color_space']])
    validate_icc_profile(icc_profile)
    clean = apply_metadata_policy(metadata, opts['metadata_policy'], opts['remove_location'])
    record = {'application': 'CCRaw export', 'version': 1, 'metadata': clean}
    if opts['metadata_policy'] not in ('none', 'copyright') and provenance:
        record['provenance'] = deepcopy(provenance)
    # TIFF ASCII ImageDescription requires escaped Unicode; JSON still roundtrips it.
    description = json.dumps(record, allow_nan=False, ensure_ascii=True)
    from .interchange.xmp import merge, FIELDS

    portable = {k: v for k, v in clean.items() if k in FIELDS}
    if isinstance(portable.get('creator'), str):
        portable['creator'] = [portable['creator']] if portable['creator'] else []
    xmp_packet = merge(None, portable) if clean else None
    depth = opts['bit_depth']
    pixels = np.rint(np.clip(rgb, 0, 1) * ((1 << depth) - 1)).astype(
        np.uint8 if depth == 8 else np.uint16
    )

    def encode(temp):
        fmt = opts['format']
        if fmt == 'psd':
            from .interchange.psd import write_psd

            write_psd(
                temp,
                rgb,
                bit_depth=depth,
                icc_profile=icc_profile,
                xmp_packet=xmp_packet,
                resolution=opts['resolution'],
            )
        elif fmt == 'tiff':
            import tifffile

            tags = [(34675, 'B', len(icc_profile), icc_profile, False)]
            if xmp_packet:
                packet = xmp_packet.encode('utf-8')
                tags.append((700, 'B', len(packet), packet, False))
            tifffile.imwrite(
                temp,
                pixels,
                photometric='rgb',
                metadata=None,
                description=description,
                resolution=(opts['resolution'], opts['resolution']),
                resolutionunit='INCH',
                extratags=tags,
            )
        elif fmt == 'png' and depth == 16:
            ok, encoded = cv2.imencode('.png', pixels[..., ::-1])
            if not ok:
                raise ValueError('PNG encoder failed')
            original = encoded.tobytes()
            # Insert chunks after IHDR, before IDAT; ICC bytes never pass an 8-bit bridge.
            chunks = _png_chunk(b'iCCP', b'CCRaw\0\0' + zlib.compress(icc_profile))
            ppm = round(opts['resolution'] / 0.0254)
            chunks += _png_chunk(b'pHYs', struct.pack('>IIB', ppm, ppm, 1))
            if clean:
                chunks += _png_chunk(b'iTXt', b'CCRaw\0\0\0\0\0' + description.encode('utf-8'))
                chunks += _png_chunk(
                    b'iTXt', b'XML:com.adobe.xmp\0\0\0\0\0' + xmp_packet.encode('utf-8')
                )
            temp.write_bytes(original[:33] + chunks + original[33:])
        else:
            image = Image.fromarray(pixels)
            kwargs = dict(icc_profile=icc_profile, dpi=(opts['resolution'], opts['resolution']))
            if fmt == 'jpeg':
                kwargs.update(quality=opts['quality'], subsampling=0)
                if xmp_packet:
                    kwargs['xmp'] = xmp_packet.encode('utf-8')
            elif clean:
                info = PngImagePlugin.PngInfo()
                info.add_itxt('CCRaw', description)
                info.add_itxt('XML:com.adobe.xmp', xmp_packet)
                kwargs['pnginfo'] = info
            image.save(temp, **kwargs)

    return atomic_write(path, encode, cancelled)
