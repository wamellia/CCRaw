"""Bounded local Lensfun-format XML import and exact-name camera/lens matching.

Original CCRaw implementation, with LightCraft's MIT lensdb model.rs as a design
reference (ArtCraft Team and LightCraft contributors). No Lensfun executable code
or database files are bundled. User-imported database licensing remains attached
to its source. Only rectilinear poly3, poly5 and PTLens distortion is applied.
Unsupported records fail explicitly; matching and focal interpolation never guess.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np


def _float(value, label, *, positive=False):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} requires a finite number') from exc
    if not math.isfinite(result) or abs(result) > 1e6 or (positive and result <= 0):
        raise ValueError(f'{label} requires a finite valid number')
    return result


def _text(node, tag):
    items = node.findall(tag)
    value = next((item.text for item in items if 'lang' not in item.attrib and item.text), None)
    return (value or (items[0].text if items else '') or '').strip()


def load_database(path) -> dict:
    """Read an explicitly imported XML file, capped at 16 MiB / 100k nodes / depth 32."""
    path = Path(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Lensfun XML exceeds 16 MiB size limit')
    data = path.read_bytes()
    # Reject UTF-16 and DTD/entity definitions before any XML expansion can occur.
    if b'\x00' in data or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Lensfun XML DOCTYPE and external entities are forbidden')
    depth, count = 0, 0
    try:
        for event, _node in ET.iterparse(io.BytesIO(data), events=('start', 'end')):
            if event == 'start':
                depth += 1
                count += 1
                if depth > 32 or count > 100000:
                    raise ValueError('Lensfun XML structure exceeds safety limits')
            else:
                depth -= 1
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f'invalid Lensfun XML: {exc}') from exc
    if root.tag != 'lensdatabase':
        raise ValueError('Lensfun XML root must be lensdatabase')
    cameras, lenses = [], []
    for camera in root.findall('camera'):
        cameras.append(
            {
                'maker': _text(camera, 'maker'),
                'model': _text(camera, 'model'),
                'mount': _text(camera, 'mount'),
                'cropfactor': _float(
                    _text(camera, 'cropfactor') or 1, 'camera cropfactor', positive=True
                ),
            }
        )
    coefficient_names = {'poly3': ('k1',), 'poly5': ('k1', 'k2'), 'ptlens': ('a', 'b', 'c')}
    for lens in root.findall('lens'):
        records = []
        for calibration in lens.findall('calibration'):
            for row in calibration.findall('distortion'):
                model = row.get('model', '')
                if model not in coefficient_names:
                    raise ValueError(f'Lensfun distortion model {model!r} is not supported')
                records.append(
                    {
                        'model': model,
                        'focal': _float(row.get('focal'), 'focal', positive=True),
                        'coefficients': tuple(
                            _float(row.get(key), key) for key in coefficient_names[model]
                        ),
                    }
                )
        lenses.append(
            {
                'maker': _text(lens, 'maker'),
                'model': _text(lens, 'model'),
                'mounts': tuple((node.text or '').strip() for node in lens.findall('mount')),
                'cropfactor': _float(
                    _text(lens, 'cropfactor') or 1, 'lens cropfactor', positive=True
                ),
                'distortion': records,
                'unsupported_corrections': tuple(
                    sorted(
                        {
                            node.tag
                            for cal in lens.findall('calibration')
                            for node in cal
                            if node.tag != 'distortion'
                        }
                    )
                ),
            }
        )
    return {'cameras': cameras, 'lenses': lenses, 'source': str(path.resolve())}


def _name(value):
    return ' '.join(str(value).casefold().split())


def match_lens(database, maker, model, *, camera_maker='', camera_model='') -> dict:
    """Exact normalized maker/model match, rejecting ambiguous or incompatible mounts."""
    matches = [
        lens
        for lens in database['lenses']
        if _name(lens['maker']) == _name(maker) and _name(lens['model']) == _name(model)
    ]
    if camera_maker or camera_model:
        cameras = [
            camera
            for camera in database['cameras']
            if _name(camera['maker']) == _name(camera_maker)
            and _name(camera['model']) == _name(camera_model)
        ]
        if len(cameras) != 1:
            raise ValueError('camera match is missing or ambiguous')
        camera = cameras[0]
        matches = [
            lens
            for lens in matches
            if camera['mount'] in lens['mounts']
            and camera['cropfactor'] >= lens['cropfactor'] - 0.05
        ]
    if len(matches) != 1:
        raise ValueError('lens match is missing, incompatible or ambiguous')
    return matches[0]


def interpolate_distortion(lens, focal) -> dict:
    """Linear interpolation within a same-model focal bracket; never extrapolate."""
    focal = _float(focal, 'focal', positive=True)
    records = sorted(lens['distortion'], key=lambda item: item['focal'])
    if not records:
        raise ValueError('lens has no supported distortion calibration')
    if focal < records[0]['focal'] - 1e-6 or focal > records[-1]['focal'] + 1e-6:
        raise ValueError('focal is outside the calibrated range')
    for record in records:
        if abs(focal - record['focal']) < 1e-6:
            return dict(record)
    for left, right in zip(records, records[1:]):
        if left['focal'] < focal < right['focal']:
            if left['model'] != right['model']:
                raise ValueError('cannot interpolate different distortion models')
            blend = (focal - left['focal']) / (right['focal'] - left['focal'])
            coefficients = tuple(
                a + blend * (b - a) for a, b in zip(left['coefficients'], right['coefficients'])
            )
            return {'model': left['model'], 'focal': focal, 'coefficients': coefficients}
    raise ValueError('ambiguous focal calibration range')


def radial_factor(radius, calibration):
    """Corrected-to-source radius ratio; r=1 at half the shorter frame side."""
    r = np.asarray(radius, np.float64)
    if not np.isfinite(r).all() or np.any(r < 0):
        raise ValueError('radius must be finite and nonnegative')
    values = tuple(_float(v, 'distortion coefficient') for v in calibration['coefficients'])
    model = calibration['model']
    if model == 'poly3' and len(values) == 1:
        (k1,) = values
        factor = 1 - k1 + k1 * r**2
        derivative = 1 - k1 + 3 * k1 * r**2
    elif model == 'poly5' and len(values) == 2:
        k1, k2 = values
        factor = 1 + k1 * r**2 + k2 * r**4
        derivative = 1 + 3 * k1 * r**2 + 5 * k2 * r**4
    elif model == 'ptlens' and len(values) == 3:
        a, b, c = values
        factor = a * r**3 + b * r**2 + c * r + 1 - a - b - c
        derivative = 4 * a * r**3 + 3 * b * r**2 + 2 * c * r + 1 - a - b - c
    else:
        raise ValueError('unsupported distortion model or invalid coefficient count')
    if not np.isfinite(factor).all() or np.any(factor <= 0.05) or np.any(factor > 10):
        raise ValueError('lens correction is invalid for this radius range')
    if np.any(derivative <= 0.01):
        raise ValueError('lens correction folds pixels in this radius range')
    return factor


def distortion_maps(shape, calibration):
    """Standalone profile inverse map for explicit caller-managed optical sampling."""
    h, w = shape[:2]
    if h < 1 or w < 1:
        raise ValueError('lens correction requires a positive frame shape')
    cx, cy = (w - 1) / 2, (h - 1) / 2
    y, x = np.mgrid[:h, :w].astype(np.float64)
    radius = np.hypot(x - cx, y - cy) / max(min(w, h) / 2, 0.5)
    factor = radial_factor(radius, calibration)
    return (cx + (x - cx) * factor).astype(np.float32), (cy + (y - cy) * factor).astype(np.float32)
