"""Named RGB spaces, unclipped floating point transfer functions and ICC v4 profiles.

Chromaticities follow IEC 61966-2-1, Adobe RGB (1998), Display P3, ROMM RGB
and ITU-R BT.2020. Profiles are generated here, not redistributed vendor files.
The process-2 working space is linear Rec.2020; encoding is a separate operation.
"""

from __future__ import annotations

from functools import lru_cache
import struct

import numpy as np

D65 = np.array([0.9504559271, 1.0, 1.0890577508])
D50 = np.array([0.9642, 1.0, 0.8249])
SPACES = ('sRGB', 'Adobe RGB', 'Display P3', 'ProPhoto RGB', 'Rec.2020')
_PRIMARIES = {
    'sRGB': ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)),
    'Adobe RGB': ((0.64, 0.33), (0.21, 0.71), (0.15, 0.06)),
    'Display P3': ((0.68, 0.32), (0.265, 0.69), (0.15, 0.06)),
    'ProPhoto RGB': ((0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)),
    'Rec.2020': ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)),
}


def canonical_name(space: str) -> str:
    key = str(space).lower().replace(' ', '').replace('.', '').replace('-', '').replace('_', '')
    aliases = {
        'srgb': 'sRGB',
        'adobergb': 'Adobe RGB',
        'adobergb1998': 'Adobe RGB',
        'displayp3': 'Display P3',
        'p3': 'Display P3',
        'prophotorgb': 'ProPhoto RGB',
        'prophoto': 'ProPhoto RGB',
        'rec2020': 'Rec.2020',
        'bt2020': 'Rec.2020',
    }
    if key not in aliases:
        raise ValueError(f'Unsupported RGB space: {space}')
    return aliases[key]


def rgb_array(rgb):
    a = np.asarray(rgb, dtype=np.float32)
    if a.ndim < 1 or a.shape[-1] != 3 or not np.isfinite(a).all():
        raise ValueError('RGB must be finite with a final dimension of three')
    return a


def white_point(space='sRGB'):
    return (D50 if canonical_name(space) == 'ProPhoto RGB' else D65).copy()


def bradford(source_white, target_white):
    b = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
    return np.linalg.inv(b) @ np.diag((b @ target_white) / (b @ source_white)) @ b


@lru_cache(maxsize=10)
def rgb_to_xyz_matrix(space='sRGB', adapted_to_d50=False):
    name = canonical_name(space)
    p = np.array([[x / y, 1, (1 - x - y) / y] for x, y in _PRIMARIES[name]]).T
    w = white_point(name)
    m = p @ np.diag(np.linalg.solve(p, w))
    if adapted_to_d50:
        m = bradford(w, D50) @ m
    m.setflags(write=False)
    return m


def decode(rgb, space='sRGB'):
    """Encoded RGB -> linear RGB with sign extension, without clipping HDR values."""
    a = rgb_array(rgb)
    name = canonical_name(space)
    from .. import native_kernels

    result = native_kernels.transfer(
        a, ('sRGB', 'Display P3', 'Adobe RGB', 'ProPhoto RGB', 'Rec.2020').index(name)
    )
    if result is not None:
        return result
    x = np.abs(a).astype(np.float64)
    if name in ('sRGB', 'Display P3'):
        y = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    elif name == 'Adobe RGB':
        y = x ** (563 / 256)
    elif name == 'ProPhoto RGB':
        y = np.where(x <= 1 / 32, x / 16, x**1.8)
    else:
        alpha, beta = 1.09929682680944, 0.018053968510807
        y = np.where(x < 4.5 * beta, x / 4.5, ((x + alpha - 1) / alpha) ** (1 / 0.45))
    return (np.sign(a) * y).astype(np.float32)


def encode(rgb, space='sRGB'):
    """Linear RGB -> encoded RGB; use clipping only at the eventual SDR output."""
    a = rgb_array(rgb)
    name = canonical_name(space)
    from .. import native_kernels

    result = native_kernels.transfer(
        a, ('sRGB', 'Display P3', 'Adobe RGB', 'ProPhoto RGB', 'Rec.2020').index(name), True
    )
    if result is not None:
        return result
    x = np.abs(a).astype(np.float64)
    if name in ('sRGB', 'Display P3'):
        y = np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)
    elif name == 'Adobe RGB':
        y = x ** (256 / 563)
    elif name == 'ProPhoto RGB':
        y = np.where(x <= 1 / 512, x * 16, x ** (1 / 1.8))
    else:
        alpha, beta = 1.09929682680944, 0.018053968510807
        y = np.where(x < beta, x * 4.5, alpha * x**0.45 - (alpha - 1))
    return (np.sign(a) * y).astype(np.float32)


def convert_linear(rgb, srcspace='sRGB', dstspace='sRGB'):
    a = rgb_array(rgb)
    src, dst = canonical_name(srcspace), canonical_name(dstspace)
    if src == dst:
        return a.copy()
    m = np.linalg.solve(rgb_to_xyz_matrix(dst, True), rgb_to_xyz_matrix(src, True))
    from .. import native_kernels

    result = native_kernels.matrix(a, m)
    if result is not None:
        return result
    return (a @ m.T).astype(np.float32)


def convert(rgb, srcspace='sRGB', dstspace='sRGB'):
    a = rgb_array(rgb)
    src, dst = canonical_name(srcspace), canonical_name(dstspace)
    if src == dst:
        return a.copy()
    from .. import native_kernels

    order = ('sRGB', 'Display P3', 'Adobe RGB', 'ProPhoto RGB', 'Rec.2020')
    m = np.linalg.solve(rgb_to_xyz_matrix(dst, True), rgb_to_xyz_matrix(src, True))
    result = native_kernels.convert_rgb(a, m, order.index(src), order.index(dst))
    if result is not None:
        return result
    return encode(convert_linear(decode(a, src), src, dst), dst)


def _fixed(values):
    return b''.join(struct.pack('>i', round(float(v) * 65536)) for v in np.ravel(values))


def _mluc(text):
    data = text.encode('utf-16-be')
    return (
        b'mluc'
        + b'\0' * 4
        + struct.pack('>II', 1, 12)
        + b'enUS'
        + struct.pack('>II', len(data), 28)
        + data
    )


def _trc(name, linear):
    if linear:
        kind, params = 0, [1.0]
    elif name in ('sRGB', 'Display P3'):
        kind, params = 3, [2.4, 1 / 1.055, 0.055 / 1.055, 1 / 12.92, 0.04045]
    elif name == 'Adobe RGB':
        kind, params = 0, [563 / 256]
    elif name == 'ProPhoto RGB':
        kind, params = 3, [1.8, 1, 0, 1 / 16, 1 / 32]
    else:
        alpha, beta = 1.09929682680944, 0.018053968510807
        kind, params = 3, [1 / 0.45, 1 / alpha, (alpha - 1) / alpha, 1 / 4.5, 4.5 * beta]
    return b'para' + b'\0' * 4 + struct.pack('>HH', kind, 0) + _fixed(params)


@lru_cache(maxsize=16)
def profile_bytes(space='sRGB', linear=False):
    """Deterministic ICC v4 matrix/TRC profile for embedded output or linear CMS input."""
    name = canonical_name(space)
    matrix = rgb_to_xyz_matrix(name, True)
    xyz = lambda v: b'XYZ ' + b'\0' * 4 + _fixed(v)
    tags = [
        (b'desc', _mluc(name + (' (linear)' if linear else ''))),
        (b'cprt', _mluc('CCRaw generated profile; use freely under MIT license')),
        (b'wtpt', xyz(D50)),
        (b'chad', b'sf32' + b'\0' * 4 + _fixed(bradford(white_point(name), D50))),
    ]
    tags += [(sig, xyz(matrix[:, i])) for i, sig in enumerate((b'rXYZ', b'gXYZ', b'bXYZ'))]
    tags += [(sig, _trc(name, linear)) for sig in (b'rTRC', b'gTRC', b'bTRC')]
    header = bytearray(128)
    header[4:24] = b'lcms' + struct.pack('>I', 0x04300000) + b'mntrRGB XYZ '
    header[24:36] = struct.pack('>6H', 2026, 1, 1, 0, 0, 0)
    header[36:40], header[40:44] = b'acsp', b'MSFT'
    header[64:68], header[68:80], header[80:84] = struct.pack('>I', 1), _fixed(D50), b'CCRW'
    table, body = bytearray(struct.pack('>I', len(tags))), bytearray()
    offset = 132 + 12 * len(tags)
    for signature, data in tags:
        table.extend(signature + struct.pack('>II', offset + len(body), len(data)))
        body.extend(data)
        body.extend(b'\0' * (-len(body) % 4))
    header[:4] = struct.pack('>I', 128 + len(table) + len(body))
    return bytes(header + table + body)
