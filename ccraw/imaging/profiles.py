"""Validated Cube LUTs and a bounded, explicitly partial standard DCP adapter.

DCP camera matrices require demosaiced camera-native RGB and camera neutral;
applying a matrix to already developed sRGB/Rec.2020 is explicitly rejected.
The adapter supports 3-channel matrices, reciprocal-CCT interpolation, exposure,
HSV tables with linear/sRGB value encoding and hue-preserving tone curves.
It does not claim Adobe rendering equivalence or implement private/triple-light,
spectral illuminant, reduction, calibration-signature or enhanced XMP profiles.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from itertools import product
from pathlib import Path
import shlex
import struct

import numpy as np

from . import colorspaces as cs

MAX_RESOURCE_BYTES = 64 << 20


def _read(path):
    p = Path(path)
    if p.stat().st_size > MAX_RESOURCE_BYTES:
        raise ValueError('Color resource exceeds 64 MiB')
    return p.read_bytes()


@dataclass(frozen=True)
class CubeLUT:
    size: int
    dimensions: int
    domain_min: np.ndarray
    domain_max: np.ndarray
    data: np.ndarray
    title: str = ''

    def apply(self, rgb):
        a = cs.rgb_array(rgb)
        shape, flat = a.shape, a.reshape(-1, 3)
        coordinate = np.clip((flat - self.domain_min) / (self.domain_max - self.domain_min), 0, 1)
        coordinate = coordinate.astype(np.float64) * (self.size - 1)
        lower = np.floor(coordinate).astype(np.int32)
        upper = np.minimum(lower + 1, self.size - 1)
        fraction = coordinate - lower
        if self.dimensions == 1:
            channels = np.arange(3)
            out = (
                self.data[lower, channels] * (1 - fraction) + self.data[upper, channels] * fraction
            )
        else:
            out = np.zeros_like(flat, dtype=np.float64)
            for corner in product((0, 1), repeat=3):
                index = np.where(corner, upper, lower)
                weight = np.prod(np.where(corner, fraction, 1 - fraction), axis=1)
                # Cube rows change red fastest: data is indexed [blue, green, red].
                out += self.data[index[:, 2], index[:, 1], index[:, 0]] * weight[:, None]
        return out.astype(np.float32).reshape(shape)


@lru_cache(maxsize=8)
def parse_cube(text):
    if not isinstance(text, str) or len(text) > MAX_RESOURCE_BYTES:
        raise ValueError('Cube LUT must be bounded UTF-8 text')
    size, dimensions, title = None, None, ''
    minimum, maximum, rows, seen = np.zeros(3), np.ones(3), [], set()
    for line_number, line in enumerate(text.splitlines(), 1):
        try:
            parts = shlex.split(line, comments=True)
        except ValueError as exc:
            raise ValueError(f'Invalid cube quoting at line {line_number}') from exc
        if not parts:
            continue
        key = parts[0]
        if key in ('TITLE', 'LUT_1D_SIZE', 'LUT_3D_SIZE', 'DOMAIN_MIN', 'DOMAIN_MAX'):
            if key in seen or rows:
                raise ValueError(f'Duplicate or misplaced Cube header {key}')
            seen.add(key)
            if key == 'TITLE':
                if len(parts) != 2:
                    raise ValueError('Cube TITLE must contain one string')
                title = parts[1]
            elif key.startswith('LUT_'):
                if size is not None or len(parts) != 2:
                    raise ValueError('Combined 1D/3D Cube LUTs are not supported')
                try:
                    size = int(parts[1])
                except ValueError as exc:
                    raise ValueError('Invalid Cube size') from exc
                dimensions = 1 if key == 'LUT_1D_SIZE' else 3
                if size < 2 or size > (65536 if dimensions == 1 else 129):
                    raise ValueError('Cube LUT dimension exceeds supported bounds')
            else:
                try:
                    value = np.array([float(p) for p in parts[1:]])
                except ValueError as exc:
                    raise ValueError('Invalid Cube domain') from exc
                if (
                    value.shape != (3,)
                    or not np.isfinite(value).all()
                    or np.any(np.abs(value) > np.finfo(np.float32).max / 2)
                ):
                    raise ValueError('Cube domain must have three finite coordinates')
                if key == 'DOMAIN_MIN':
                    minimum = value
                else:
                    maximum = value
        else:
            if size is None or len(parts) != 3:
                raise ValueError(f'Unknown or malformed Cube line {line_number}')
            try:
                row = [float(p) for p in parts]
            except ValueError as exc:
                raise ValueError(f'Invalid Cube row {line_number}') from exc
            if not np.isfinite(row).all() or np.any(np.abs(row) > np.finfo(np.float32).max):
                raise ValueError('Cube entries must be finite')
            rows.append(row)
            if len(rows) > size**dimensions:
                raise ValueError('Cube has more rows than its dimension')
    if size is None or len(rows) != size**dimensions or np.any(maximum <= minimum):
        raise ValueError('Missing Cube size, invalid domain or incorrect entry count')
    data = np.asarray(rows, np.float32)
    if dimensions == 3:
        data = data.reshape(size, size, size, 3)
    for arr in (minimum, maximum, data):
        arr.setflags(write=False)
    return CubeLUT(size, dimensions, minimum, maximum, data, title)


def load_cube(path):
    try:
        return parse_cube(_read(path).decode('utf-8-sig'))
    except UnicodeDecodeError as exc:
        raise ValueError('Cube LUT must be UTF-8 text') from exc


def _tiff_tags(data):
    """Classic TIFF/DCP IFD0 only. Reject duplicate, cyclic and oversized structures."""
    if not isinstance(data, bytes) or len(data) < 8 or len(data) > MAX_RESOURCE_BYTES:
        raise ValueError('Invalid DCP resource size')
    order = {'II': '<', 'MM': '>'}.get(data[:2].decode('ascii', errors='ignore'))
    if order is None or struct.unpack_from(order + 'H', data, 2)[0] not in (42, 0x4352):
        raise ValueError('DCP must use a classic TIFF or camera-profile header')
    offset = struct.unpack_from(order + 'I', data, 4)[0]
    if offset < 8 or offset + 2 > len(data):
        raise ValueError('DCP IFD outside resource')
    count = struct.unpack_from(order + 'H', data, offset)[0]
    if count > 4096 or offset + 2 + 12 * count + 4 > len(data):
        raise ValueError('DCP IFD is truncated or oversized')
    tags = {}
    formats = {
        1: ('B', 1),
        2: ('B', 1),
        3: ('H', 2),
        4: ('I', 4),
        5: ('I', 8),
        7: ('B', 1),
        9: ('i', 4),
        10: ('i', 8),
        11: ('f', 4),
        12: ('d', 8),
    }
    for i in range(count):
        pos = offset + 2 + i * 12
        tag, kind, n = struct.unpack_from(order + 'HHI', data, pos)
        if tag in tags or kind not in formats or n > 4_000_000:
            raise ValueError('Duplicate, unsupported TIFF type or excessive DCP tag')
        fmt, item_size = formats[kind]
        length = n * item_size
        start = pos + 8 if length <= 4 else struct.unpack_from(order + 'I', data, pos + 8)[0]
        if start + length > len(data) or (length > 4 and start < 8):
            raise ValueError('DCP tag extends outside resource')
        raw = data[start : start + length]
        if kind in (2, 7):
            value = raw
        else:
            number = n * 2 if kind in (5, 10) else n
            value = np.frombuffer(raw, dtype=order + fmt, count=number).astype(np.float64)
            if kind in (5, 10):
                rationals = value.reshape(-1, 2)
                if np.any(rationals[:, 1] == 0):
                    raise ValueError('DCP contains a zero rational denominator')
                value = rationals[:, 0] / rationals[:, 1]
            if not np.isfinite(value).all():
                raise ValueError('DCP numerical data must be finite')
        tags[tag] = value
    return tags


def _matrix(tags, tag):
    value = tags.get(tag)
    if value is None:
        return None
    if not isinstance(value, np.ndarray) or value.size != 9:
        raise ValueError('Only three-channel DCP matrices are supported')
    matrix = value.reshape(3, 3)
    if np.linalg.cond(matrix) > 1e8 or not np.isfinite(matrix).all():
        raise ValueError('DCP contains a singular or unstable matrix')
    matrix.setflags(write=False)
    return matrix


def _scalar(tags, tag, default):
    value = tags.get(tag)
    if value is None:
        return default
    if not isinstance(value, np.ndarray) or len(value) != 1:
        raise ValueError(f'DCP tag {tag} requires one numeric value')
    return float(value[0])


_TEMPERATURES = {
    3: 2856.0,
    17: 2856.0,
    18: 4874.0,
    19: 6774.0,
    20: 5503.0,
    21: 6504.0,
    22: 7504.0,
    23: 5003.0,
    24: 3200.0,
}


def _rgb_hsv(rgb):
    a = np.asarray(rgb, np.float64)
    maximum, minimum = a.max(-1), a.min(-1)
    delta = maximum - minimum
    safe = np.where(delta > 1e-12, delta, 1)
    channel = a.argmax(-1)
    h = np.select(
        [channel == 0, channel == 1],
        [(a[..., 1] - a[..., 2]) / safe, (a[..., 2] - a[..., 0]) / safe + 2],
        (a[..., 0] - a[..., 1]) / safe + 4,
    )
    h = np.where(delta > 1e-12, h / 6 % 1, 0)
    saturation = np.divide(delta, maximum, out=np.zeros_like(delta), where=maximum > 1e-12)
    return np.stack((h, saturation, maximum), -1)


def _hsv_rgb(hsv):
    h, s, v = np.moveaxis(hsv, -1, 0)
    # Triangular RGB basis yields periodic hue without six branches.
    phases = np.array([0.0, 4.0, 2.0])
    basis = np.clip(np.abs((h[..., None] * 6 + phases) % 6 - 3) - 1, 0, 1)
    return v[..., None] * (1 - s[..., None] + s[..., None] * basis)


@dataclass(frozen=True)
class HueSatTable:
    data: np.ndarray  # [value, hue, saturation, (hue degrees, sat scale, val scale)]
    encoding: int = 0

    def apply(self, rgb):
        a = cs.rgb_array(rgb)
        hsv = _rgb_hsv(np.maximum(a, 0))
        vcount, hcount, scount = self.data.shape[:3]
        values = hsv[..., 2]
        if self.encoding:
            values = cs.encode(np.repeat(values[..., None], 3, axis=-1))[..., 0]
        coordinates = np.stack(
            (
                np.clip(values, 0, 1) * (vcount - 1),
                hsv[..., 0] * hcount,
                np.clip(hsv[..., 1], 0, 1) * (scount - 1),
            ),
            -1,
        )
        lower = np.floor(coordinates).astype(int)
        upper = lower + 1
        lower[..., 0] = np.minimum(lower[..., 0], vcount - 1)
        upper[..., 0] = np.minimum(upper[..., 0], vcount - 1)
        lower[..., 1] %= hcount
        upper[..., 1] %= hcount
        upper[..., 2] = np.minimum(upper[..., 2], scount - 1)
        fraction = coordinates - np.floor(coordinates)
        shifts = np.zeros_like(hsv)
        for corner in product((0, 1), repeat=3):
            index = np.where(corner, upper, lower)
            weight = np.prod(np.where(corner, fraction, 1 - fraction), axis=-1)
            shifts += self.data[index[..., 0], index[..., 1], index[..., 2]] * weight[..., None]
        hsv[..., 0] = (hsv[..., 0] + shifts[..., 0] / 360) % 1
        hsv[..., 1] = np.clip(hsv[..., 1] * shifts[..., 1], 0, 1)
        adjusted_v = values * shifts[..., 2]
        if self.encoding:
            adjusted_v = cs.decode(np.repeat(adjusted_v[..., None], 3, axis=-1))[..., 0]
        hsv[..., 2] = adjusted_v
        result = _hsv_rgb(hsv).astype(np.float32)
        # HSV is undefined for negative components: preserve those pixels explicitly.
        # Matrix roundoff at zero (e.g. -1e-17) must not disable a valid red pixel.
        return np.where(np.any(a < -1e-7, axis=-1)[..., None], a, result)


def _table(tags, dimensions_tag, data_tag, encoding_tag):
    dims, values = tags.get(dimensions_tag), tags.get(data_tag)
    if dims is None and values is None:
        return None
    if (
        not isinstance(dims, np.ndarray)
        or not isinstance(values, np.ndarray)
        or len(dims) != 3
        or np.any(dims != np.floor(dims))
    ):
        raise ValueError('DCP table needs integral dimensions and data')
    h, s, v = (int(i) for i in dims)
    if h < 1 or s < 2 or v < 1 or h * s * v > 1_000_000 or values.size != h * s * v * 3:
        raise ValueError('Invalid DCP table dimensions or entry count')
    encoding = _scalar(tags, encoding_tag, 0)
    if encoding not in (0, 1):
        raise ValueError('Unsupported DCP table encoding')
    if np.any(np.abs(values) > np.finfo(np.float32).max):
        raise ValueError('DCP table values exceed floating point bounds')
    table = values.reshape(v, h, s, 3).astype(np.float32)
    if np.any(table[..., 1:] < 0):
        raise ValueError('DCP saturation/value scales cannot be negative')
    table.setflags(write=False)
    return HueSatTable(table, int(encoding))


@dataclass(frozen=True)
class DCPProfile:
    matrix1: np.ndarray
    matrix2: np.ndarray | None
    forward1: np.ndarray | None
    forward2: np.ndarray | None
    temperatures: tuple[float | None, float | None]
    tone_curve: np.ndarray | None
    huesat1: HueSatTable | None
    huesat2: HueSatTable | None
    look_table: HueSatTable | None
    baseline_exposure: float
    unsupported_tags: tuple[int, ...]
    support: dict
    original_bytes: bytes

    def _weight(self, cct):
        if not np.isfinite(cct) or cct <= 0:
            raise ValueError('DCP illuminant temperature must be positive and finite')
        t1, t2 = self.temperatures
        if t1 is None or t2 is None or t1 == t2:
            if self.matrix2 is not None:
                raise ValueError('Dual DCP illuminants have unsupported spectral/CCT definitions')
            return 0.0
        return float(np.clip((1 / cct - 1 / t1) / (1 / t2 - 1 / t1), 0, 1))

    def color_matrix(self, cct=6504):
        weight = self._weight(cct)
        matrix = (
            self.matrix1
            if self.matrix2 is None
            else (1 - weight) * self.matrix1 + weight * self.matrix2
        )
        if np.linalg.cond(matrix) > 1e8:
            raise ValueError('Interpolated DCP matrix is singular')
        return matrix.copy()

    def apply_look(self, rgb, *, srcspace='Rec.2020', cct=6504):
        """Apply only profile tables/tone/exposure to existing *linear* working RGB."""
        a = cs.convert_linear(rgb, srcspace, 'ProPhoto RGB')
        a = a * np.float32(2**self.baseline_exposure)
        table = self.huesat1
        if table is not None and self.huesat2 is not None:
            w = self._weight(cct)
            table = HueSatTable((1 - w) * table.data + w * self.huesat2.data, table.encoding)
        if table is not None:
            a = table.apply(a)
        if self.look_table is not None:
            a = self.look_table.apply(a)
        if self.tone_curve is not None:
            minimum, maximum = a.min(-1), a.max(-1)
            lo = np.interp(minimum, self.tone_curve[:, 0], self.tone_curve[:, 1])
            hi = np.interp(maximum, self.tone_curve[:, 0], self.tone_curve[:, 1])
            fraction = np.divide(
                a - minimum[..., None],
                (maximum - minimum)[..., None],
                out=np.zeros_like(a),
                where=(maximum > minimum)[..., None],
            )
            mapped = lo[..., None] + fraction * (hi - lo)[..., None]
            # The normalized DCP tone curve is not an HDR or negative extrapolator.
            a = np.where(((minimum >= 0) & (maximum <= 1))[..., None], mapped, a)
        return cs.convert_linear(a, 'ProPhoto RGB', srcspace)

    def apply_camera(
        self,
        camera_rgb,
        *,
        cct=6504,
        camera_neutral=(1.0, 1.0, 1.0),
        dstspace='Rec.2020',
        apply_look=True,
    ):
        """Camera-native linear RGB -> linear destination; neutral is unbalanced WB RGB.

        Assumes identity analog balance/camera calibration. These DNG image-specific
        transforms are not stored in a standalone DCP and must be handled upstream.
        """
        a = cs.rgb_array(camera_rgb)
        neutral = np.asarray(camera_neutral, np.float64)
        if neutral.shape != (3,) or not np.isfinite(neutral).all() or np.any(neutral <= 0):
            raise ValueError('DCP camera neutral requires three positive finite values')
        weight = self._weight(cct)
        if self.forward1 is not None:
            forward = self.forward1
            if self.forward2 is not None:
                forward = forward * (1 - weight) + self.forward2 * weight
            xyz = (a / neutral) @ forward.T
        else:
            inverse = np.linalg.inv(self.color_matrix(cct))
            white = inverse @ neutral
            if white[1] <= 0 or np.any(white <= 0):
                raise ValueError('DCP matrix and camera neutral do not describe a valid white')
            adaptation = cs.bradford(white / white[1], cs.D50)
            xyz = (a @ inverse.T) / white[1] @ adaptation.T
        rgb = (xyz @ np.linalg.inv(cs.rgb_to_xyz_matrix(dstspace, True)).T).astype(np.float32)
        return self.apply_look(rgb, srcspace=dstspace, cct=cct) if apply_look else rgb


@lru_cache(maxsize=8)
def parse_dcp(data):
    tags = _tiff_tags(data)
    matrix1, matrix2 = _matrix(tags, 50721), _matrix(tags, 50722)
    if matrix1 is None:
        raise ValueError('DCP requires ColorMatrix1 for a three-channel camera')
    forward1, forward2 = _matrix(tags, 50964), _matrix(tags, 50965)
    if forward2 is not None and forward1 is None:
        raise ValueError('DCP ForwardMatrix2 requires ForwardMatrix1')
    illuminants = tuple(int(_scalar(tags, tag, 0)) for tag in (50778, 50779))
    temperatures = tuple(_TEMPERATURES.get(i) for i in illuminants)
    tone = tags.get(50940)
    if tone is not None:
        if not isinstance(tone, np.ndarray) or tone.size < 4 or tone.size % 2 or tone.size > 131072:
            raise ValueError('DCP tone curve needs bounded x/y pairs')
        tone = tone.reshape(-1, 2)
        if (
            np.any(np.diff(tone[:, 0]) <= 0)
            or np.any(np.diff(tone[:, 1]) < 0)
            or np.any(tone < 0)
            or np.any(tone > 1)
            or not np.allclose(tone[[0, -1]], [[0, 0], [1, 1]], atol=1e-6)
        ):
            raise ValueError('DCP tone curve must be monotonic from (0,0) to (1,1)')
        tone.setflags(write=False)
    huesat1 = _table(tags, 50937, 50938, 51107)
    huesat2 = _table(tags, 50937, 50939, 51107) if 50939 in tags else None
    look = _table(tags, 50981, 50982, 51108)
    exposure = _scalar(tags, 51109, 0)
    if not -20 <= exposure <= 20:
        raise ValueError('DCP baseline exposure is outside a safe finite range')
    known = {
        50721,
        50722,
        50778,
        50779,
        50964,
        50965,
        50937,
        50938,
        50939,
        50940,
        50981,
        50982,
        51107,
        51108,
        51109,
        50708,
        50709,
        50932,
        50933,
        50934,
        50941,
        50942,
        50936,
    }
    unsupported = tuple(sorted(set(tags) - known))
    support = {
        'matrix': True,
        'forward_matrix': forward1 is not None,
        'dual_illuminant': matrix2 is not None and all(temperatures),
        'tone_curve': tone is not None,
        'hue_sat_map': huesat1 is not None,
        'look_table': look is not None,
        'baseline_exposure': 51109 in tags,
        'complete': not unsupported and (matrix2 is None or all(temperatures)),
        'adobe_equivalent': False,
        'requires_camera_native_rgb': True,
        'tone_method': 'hue-preserving piecewise linear normalized RGB',
        'limitations': [
            'image-specific analog balance/calibration handled upstream',
            'private and triple illuminant tags are not applied',
            'negative RGB is preserved through HSV tables',
            'extended values bypass the normalized tone curve',
            'validated synthetic fixtures; no licensed camera corpus',
        ],
    }
    return DCPProfile(
        matrix1,
        matrix2,
        forward1,
        forward2,
        temperatures,
        tone,
        huesat1,
        huesat2,
        look,
        exposure,
        unsupported,
        support,
        data,
    )


def load_dcp(path):
    return parse_dcp(_read(path))


def profile_info(path):
    p = Path(path)
    if p.suffix.lower() == '.cube':
        lut = load_cube(p)
        return {
            'type': 'cube',
            'dimensions': lut.dimensions,
            'size': lut.size,
            'title': lut.title,
            'supported': True,
        }
    if p.suffix.lower() == '.dcp':
        dcp = load_dcp(p)
        return {'type': 'dcp', 'support': dcp.support, 'unsupported_tags': dcp.unsupported_tags}
    if p.suffix.lower() in ('.icc', '.icm'):
        from . import cms

        cms.load_profile(p, rgb_only=True)
        return {'type': 'icc', 'supported': cms.available(), 'runtime': cms.status()}
    raise ValueError('Supported profile resources are .cube, .dcp, .icc and .icm')


def apply_profile(rgb, profile=None, *, cache=None, basis_key=None):
    """Encoded RGB LUT/ICC operation. For DCP use explicit camera or look methods.

    An imported ICC describes the encoded input; target_space defaults to sRGB.
    Cube LUTs operate on supplied encoded values;
    processing linear Rec.2020 must deliberately encode/decode around this stage.
    """
    a = cs.rgb_array(rgb)
    profile = profile or {}
    amount = profile.get('amount', 100)
    if (
        isinstance(amount, bool)
        or not isinstance(amount, (int, float))
        or not np.isfinite(amount)
        or not 0 <= amount <= 200
    ):
        raise ValueError('Profile amount must be finite and between 0 and 200 percent')
    path = profile.get('path', '')
    if not path or amount == 0 or profile.get('enabled', True) is False:
        return a.copy()

    def transform():
        suffix = Path(path).suffix.lower()
        if suffix == '.cube':
            return load_cube(path).apply(a)
        if suffix in ('.icc', '.icm'):
            from . import cms

            return cms.transform_icc(
                a,
                cms.load_profile(path, rgb_only=True),
                cs.profile_bytes(profile.get('target_space', 'sRGB')),
                intent=profile.get('intent', 'relative'),
            )
        if suffix == '.dcp':
            raise ValueError(
                'DCP matrix requires camera-native RGB; use load_dcp().apply_camera(), '
                'or explicitly apply_look() to linear working RGB'
            )
        raise ValueError('Unsupported profile resource extension')

    if cache is not None:
        from ..engine import _key

        resource = Path(path).resolve()
        stat = resource.stat()
        settings = {k: v for k, v in profile.items() if k != 'amount'}
        key = _key(basis_key, str(resource), stat.st_mtime_ns, stat.st_size, settings)
        out = cache.get('profile-transform', key, transform)
    else:
        out = transform()
    return (a + (out - a) * np.float32(amount / 100)).astype(np.float32)
