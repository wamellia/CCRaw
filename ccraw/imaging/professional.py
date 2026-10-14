"""Independent encoded-sRGB CPU reference for the professional editor.

Neutral settings return the input object; changed stages never mutate it. This
adapter does not recover RAW gamut. Profiles are handled by the profile adapter.

Design reference: LightCraft, ArtCraft Team and contributors (MIT), pipeline
optics.rs / geometry.rs / colorops.rs. This implementation is original NumPy/OpenCV
code; no Rust source or Lensfun implementation code is copied. Point selection uses
the published Oklab transform, frozen source selections and circular hue.
"""

from __future__ import annotations

import math

import cv2
import numpy as np


def _number(settings, key, default=0, lo=-100, hi=100):
    value = float(settings.get(key, default))
    if not math.isfinite(value):
        raise ValueError(f'{key} must be finite')
    return float(np.clip(value, lo, hi))


def _input(rgb):
    if rgb.ndim != 3 or rgb.shape[-1] != 3 or not rgb.shape[0] or not rgb.shape[1]:
        raise ValueError('professional input must be a nonempty H x W x 3 RGB array')
    if not np.isfinite(rgb).all():
        raise ValueError('professional input pixels must be finite')
    return np.ascontiguousarray(rgb, dtype=np.float32)


def _linear(rgb):
    from .. import cpu_ops

    if rgb.dtype == np.float32:
        result = cpu_ops.linear(rgb)
        if result is not None:
            return result
    return np.where(
        rgb <= 0.04045, rgb / 12.92, np.maximum((rgb + 0.055) / 1.055, 0) ** 2.4
    ).astype(np.float32)


def _encoded(rgb):
    from .. import cpu_ops

    if rgb.dtype == np.float32:
        result = cpu_ops.srgb(rgb)
        if result is not None:
            return result
    rgb = np.maximum(rgb, 0)
    return np.clip(
        np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055), 0, 1
    ).astype(np.float32)


_M1 = np.array(
    [
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005],
    ],
    np.float32,
)
_M2 = np.array(
    [
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ],
    np.float32,
)
_M1_INV = np.linalg.inv(_M1)
_M2_INV = np.linalg.inv(_M2)


def _lab(rgb, space='sRGB'):
    from . import colorspaces as cs
    from .. import cpu_ops

    linear = (
        _linear(rgb) if space == 'sRGB' else cs.convert_linear(cs.decode(rgb, space), space, 'sRGB')
    )
    return cpu_ops.cbrt(linear @ _M1.T) @ _M2.T


def _rgb(lab, space='sRGB'):
    from . import colorspaces as cs
    from .. import cpu_ops

    linear = cpu_ops.cube(lab @ _M2_INV.T) @ _M1_INV.T
    return (
        _encoded(linear)
        if space == 'sRGB'
        else cs.encode(cs.convert_linear(linear, 'sRGB', space), space)
    )


def _smooth(a, b, value):
    t = np.clip((value - a) / max(b - a, 1e-8), 0, 1)
    return t * t * (3 - 2 * t)


def _wrap(h):
    return (h + np.pi) % (2 * np.pi) - np.pi


def _selection(distance, width, feather):
    return 1 - _smooth(width * (1 - feather), width, distance)


def _calibration(rgb, settings, space='sRGB'):
    tint = _number(settings, 'shadows_tint')
    matrix = np.eye(3, dtype=np.float32)
    axis = np.ones(3, np.float32) / np.sqrt(3)
    for index, name in enumerate(('red', 'green', 'blue')):
        angle = np.deg2rad(_number(settings, name + '_hue') * 0.2)
        sat = 1 + _number(settings, name + '_saturation') / 100
        primary = np.eye(3, dtype=np.float32)[index] - 1 / 3
        rotated = primary * np.cos(angle) + np.cross(axis, primary) * np.sin(angle)
        delta = rotated * sat - primary
        basis = np.eye(3, dtype=np.float32)[index] - 1 / 3
        matrix += np.outer(delta, basis)
    if np.array_equal(matrix, np.eye(3, dtype=np.float32)) and tint == 0:
        return rgb
    from . import colorspaces as cs

    linear = _linear(rgb) if space == 'sRGB' else cs.decode(rgb, space)
    result = linear @ matrix.T
    if tint:
        y = linear @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        strength = tint / 100 * 0.04 * (1 - _smooth(0.04, 0.6, y))
        result += strength[..., None] * np.array([0.5, -0.2973, 0.5], np.float32)
    return _encoded(result) if space == 'sRGB' else cs.encode(result, space)


def point_mask(rgb, point, space='sRGB'):
    """Coverage of one point selection, using exactly the edit's Oklab ranges."""
    return _point_weight(_lab(_input(rgb), space), point)


def _point_weight(lab, point):
    sample = np.asarray(point.get('rgb', []), np.float32)
    if sample.shape != (3,) or not np.isfinite(sample).all():
        raise ValueError('point_color rgb sample must contain three finite values')
    sl, sa, sb = _lab(np.clip(sample, 0, 1))
    sc, sh = np.hypot(sa, sb), np.arctan2(sb, sa)
    l = lab[..., 0]
    c, h = np.hypot(lab[..., 1], lab[..., 2]), np.arctan2(lab[..., 2], lab[..., 1])
    feather = max(0.01, _number(point, 'feather', 50, 0, 100) / 100)
    wh = np.deg2rad(max(0.5, _number(point, 'hue_range', 30, 0, 100)))
    wc = 0.01 + 0.003 * _number(point, 'saturation_range', 50, 0, 100)
    wl = 0.01 + 0.007 * _number(point, 'luminance_range', 50, 0, 100)
    weight = _selection(np.abs(c - sc), wc, feather) * _selection(np.abs(l - sl), wl, feather)
    if sc >= 0.02:
        weight *= _selection(np.abs(_wrap(h - sh)), wh, feather) * _smooth(0.005, 0.025, c)
    return weight.astype(np.float32)


def bw_target_band(rgb):
    """Nearest of the eight visible hue controls; neutral pixels are not targets."""
    hsv = cv2.cvtColor(np.asarray(rgb, np.float32).reshape(1, 1, 3), cv2.COLOR_RGB2HSV)[0, 0]
    if hsv[1] < 0.05:
        return None
    distance = np.abs((hsv[0] - np.array([0, 30, 60, 120, 180, 240, 270, 300]) + 180) % 360 - 180)
    return int(np.argmin(distance))


def automatic_bw(rgb):
    """A conservative photo-dependent start, without changing the source image."""
    sample = np.ascontiguousarray(
        rgb[:: max(1, rgb.shape[0] // 256), :: max(1, rgb.shape[1] // 256)], np.float32
    )
    hsv = cv2.cvtColor(np.clip(sample, 0, 1), cv2.COLOR_RGB2HSV)
    gray = sample @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    values = []
    for hue in (0, 30, 60, 120, 180, 240, 270, 300):
        distance = np.abs((hsv[..., 0] - hue + 180) % 360 - 180)
        weights = np.maximum(0, 1 - distance / 35) * hsv[..., 1]
        total = float(weights.sum())
        mean = float((gray * weights).sum() / total) if total > 1 else 0.5
        values.append(float(np.clip((0.5 - mean) * 80, -35, 35)) if total > 1 else 0.0)
    return values


def apply_color(
    rgb: np.ndarray, professional: dict, space='sRGB', cache=None, basis_key=None
) -> np.ndarray:
    """Primary calibration → immutable-source point colour → optional BW mixer.

    BW needs the colour source before the legacy monochrome stage; zero BW values
    use legacy encoded-sRGB luminance to preserve its neutral appearance. Root calls
    the profile adapter separately. Point weights never drift after earlier edits.
    """
    from ..engine import _key

    calibration = professional.get('calibration', {})
    source_key = _key(basis_key, calibration, space)
    out = (
        _calibration(rgb, calibration, space)
        if cache is None
        else cache.get('point-source', source_key, lambda: _calibration(rgb, calibration, space))
    )
    points = professional.get('point_color', [])
    if len(points) > 16:
        raise ValueError('point_color supports at most 16 samples')
    active = []
    for point in points:
        if point.get('enabled', True):
            values = [_number(point, key) for key in ('hue', 'saturation', 'luminance')]
            if any(values):
                active.append((point, values))
    if active:
        source_lab = (
            _lab(_input(out), space)
            if cache is None
            else cache.get('point-lab', source_key, lambda: _lab(_input(out), space))
        )
        result_lab = source_lab.copy()
        touched = np.zeros(source_lab.shape[:2], bool)
        for index, (point, (dh, ds, dl)) in enumerate(active):
            selection = {
                key: point.get(key)
                for key in ('rgb', 'hue_range', 'saturation_range', 'luminance_range', 'feather')
            }
            weight = (
                _point_weight(source_lab, point)
                if cache is None
                else cache.get(
                    'point-weight-' + str(index),
                    _key(source_key, selection),
                    lambda: _point_weight(source_lab, point),
                )
            )
            selected = weight > 0
            if not np.any(selected):
                continue
            from .. import native_kernels

            adjusted = native_kernels.point_adjust(result_lab, weight, dh, ds, dl)
            if adjusted is not None:
                result_lab = adjusted
                touched |= selected
                continue
            values = result_lab[selected]
            coverage = weight[selected]
            old_c = np.hypot(values[:, 1], values[:, 2])
            old_h = np.arctan2(values[:, 2], values[:, 1])
            new_c = old_c * np.maximum(0, 1 + coverage * ds / 100)
            new_h = old_h + coverage * np.deg2rad(dh * 0.6)
            values[:, 0] += coverage * dl / 100 * 0.2
            values[:, 1] = new_c * np.cos(new_h)
            values[:, 2] = new_c * np.sin(new_h)
            result_lab[selected] = values
            touched |= selected
        if np.any(touched):
            out = out.copy()
            out[touched] = _rgb(result_lab[touched], space)
    if professional.get('monochrome', False):
        mix = np.asarray(professional.get('bw_mixer', [0] * 8), np.float32)
        if mix.shape != (8,) or not np.isfinite(mix).all():
            raise ValueError('bw_mixer must contain eight finite values')
        bw_key = _key(source_key, points)

        def luminance():
            return out @ np.array([0.2126, 0.7152, 0.0722], np.float32)

        gray = luminance() if cache is None else cache.get('bw-gray', bw_key, luminance)
        if np.any(mix):

            def color_hsv():
                return cv2.cvtColor(_input(out), cv2.COLOR_RGB2HSV)

            hsv = color_hsv() if cache is None else cache.get('bw-hsv', bw_key, color_hsv)
            from .. import native_kernels

            fast = native_kernels.bw(hsv, gray, mix)
            if fast is not None:
                return np.repeat(fast[..., None], 3, axis=-1)
            hue, sat = hsv[..., 0], hsv[..., 1]
            centers = [0, 30, 60, 120, 180, 240, 270, 300, 360]
            adjustment = np.zeros_like(gray)
            bounded = np.clip(mix, -100, 100) / 100
            for i in range(8):
                t = np.clip((hue - centers[i]) / (centers[i + 1] - centers[i]), 0, 1)
                weight = 0.5 - 0.5 * np.cos(t * np.pi)
                selected = (hue >= centers[i]) & (hue < centers[i + 1])
                adjustment += selected * ((1 - weight) * bounded[i] + weight * bounded[(i + 1) % 8])
            gray = np.clip(gray + adjustment * sat * 0.25, 0, 1)
        out = np.repeat(gray[..., None], 3, axis=-1).astype(np.float32)
    return out


def _geometry_values(pro):
    g, o = pro.get('geometry', {}), pro.get('optics', {})
    geometry = {
        key: _number(g, key) for key in ('vertical', 'horizontal', 'aspect', 'offset_x', 'offset_y')
    }
    geometry['scale'] = _number(g, 'scale', 100, 50, 150)
    geometry['flip_x'], geometry['flip_y'] = bool(g.get('flip_x')), bool(g.get('flip_y'))
    optical = {key: _number(o, key) for key in ('distortion', 'ca_red', 'ca_blue')}
    return geometry, optical


def geometry_neutral(pro: dict) -> bool:
    """True when no manual perspective, flip, distortion or lateral CA moves pixels."""
    g, o = _geometry_values(pro)
    return (
        not any(value for key, value in g.items() if key != 'scale')
        and g['scale'] == 100
        and not any(o.values())
        and not pro.get('lensfun')
    )


def constrained_crop(shape, pro):
    """Largest bounded rectangle inside the corrected image, with a sampling guard.

    Use a bounded grid and the linear-time maximal-rectangle histogram algorithm.
    The caller runs this on its image worker; returned coordinates are normalized.
    """
    h, w = shape[:2]
    ratio = min(1.0, 256 / max(h, w))
    gh, gw = max(4, round(h * ratio)), max(4, round(w * ratio))
    valid = np.ones((gh, gw), bool)
    for channel in (0, 1, 2):
        mx, my = remap_map((gh, gw, 3), pro, channel)
        valid &= (mx >= 0) & (my >= 0) & (mx <= gw - 1) & (my <= gh - 1)
    if valid.all():
        return [0.0, 0.0, 1.0, 1.0]
    heights = np.zeros(gw, int)
    best_area, best = 0, None
    for row in range(gh):
        heights = np.where(valid[row], heights + 1, 0)
        stack = []
        for column in range(gw + 1):
            height = int(heights[column]) if column < gw else 0
            start = column
            while stack and stack[-1][1] > height:
                left, previous = stack.pop()
                area = (column - left) * previous
                if area > best_area:
                    best_area, best = area, (left, row - previous + 1, column, row + 1)
                start = left
            if not stack or stack[-1][1] < height:
                stack.append((start, height))
    if best is None or best[2] - best[0] <= 4 or best[3] - best[1] <= 4:
        raise ValueError('当前几何变换没有可用裁切范围；请减小透视或偏移。')
    x0, y0, x1, y1 = best
    return [(x0 + 2) / gw, (y0 + 2) / gh, (x1 - 2) / gw, (y1 - 2) / gh]


def remap_map(shape, pro: dict, channel: int = 1):
    """Return output-to-source float32 maps; channel=1 is the mask/green map."""
    g, o = _geometry_values(pro)
    h, w = shape[:2]
    if h < 1 or w < 1 or channel not in (0, 1, 2):
        raise ValueError('invalid geometry shape or channel')
    if not pro.get('lensfun'):
        from .. import native_kernels

        maps = native_kernels.remap_map(shape, g, o, channel)
        if maps is not None:
            return maps
    cx, cy = (w - 1) / 2, (h - 1) / 2
    unit = max(w, h) / 2
    y, x = np.mgrid[:h, :w].astype(np.float32)
    x = (x - cx) / unit - g['offset_x'] / 100 * w / unit * 0.5
    y = (y - cy) / unit - g['offset_y'] / 100 * h / unit * 0.5
    aspect = 2 ** (g['aspect'] / 100)
    zoom = g['scale'] / 100
    x /= zoom * np.sqrt(aspect)
    y /= zoom / np.sqrt(aspect)
    if g['flip_x']:
        x = -x
    if g['flip_y']:
        y = -y
    denominator = 1 - 0.22 * g['horizontal'] / 100 * x - 0.22 * g['vertical'] / 100 * y
    if np.min(denominator) < 0.05:
        raise ValueError('perspective is invalid: projective horizon intersects output')
    x, y = x / denominator, y / denominator
    diagonal = max(np.hypot(cx, cy), 0.5)
    radius2 = ((x * unit) ** 2 + (y * unit) ** 2) / diagonal**2
    radial = 1 - 0.2 * o['distortion'] / 100 * radius2
    derivative = 1 - 0.6 * o['distortion'] / 100 * radius2
    if np.any(radial <= 0.05) or np.any(derivative <= 0.01):
        raise ValueError(
            'combined lens correction folds pixels; reduce distortion or increase scale'
        )
    ca = (o['ca_red'], 0, o['ca_blue'])[channel]
    factor = radial * (1 + 0.003 * ca / 100)
    if pro.get('lensfun'):
        from .lensfun_profiles import radial_factor

        radius = np.hypot(x * unit, y * unit) / max(min(w, h) / 2, 0.5)
        factor *= radial_factor(radius, pro['lensfun']['calibration'])
    return (cx + x * unit * factor).astype(np.float32), (cy + y * unit * factor).astype(np.float32)


def apply_geometry(rgb: np.ndarray, professional: dict, cache=None) -> np.ndarray:
    """Single inverse-map bilinear sample with black fill, unchanged output extent.

    Caller applies existing crop/rotation separately, and may use remap_map for masks.
    """
    if geometry_neutral(professional):
        return rgb
    src = _input(rgb)
    g, o = _geometry_values(professional)
    no_shape_change = (
        not any(g[key] for key in ('vertical', 'horizontal', 'aspect', 'offset_x', 'offset_y'))
        and g['scale'] == 100
    )
    if no_shape_change and not any(o.values()) and not professional.get('lensfun'):
        return np.ascontiguousarray(
            src[:: (-1 if g['flip_y'] else 1), :: (-1 if g['flip_x'] else 1)]
        ).copy()
    if not o['ca_red'] and not o['ca_blue']:
        if cache is None:
            mx, my = remap_map(src.shape, professional)
        else:
            from ..engine import _key

            key = _key(
                src.shape,
                professional.get('geometry'),
                o['distortion'],
                professional.get('lensfun'),
            )
            maps = cache.get(
                'remap-shared', key, lambda: np.stack(remap_map(src.shape, professional), -1)
            )
            mx, my = maps[..., 0], maps[..., 1]
        return cv2.remap(
            src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0
        )
    out = np.empty_like(src)
    for channel in range(3):
        ca = (o['ca_red'], 0, o['ca_blue'])[channel]
        if (
            no_shape_change
            and not g['flip_x']
            and not g['flip_y']
            and not o['distortion']
            and not ca
            and not professional.get('lensfun')
        ):
            out[..., channel] = src[..., channel]
            continue
        if cache is None:
            mx, my = remap_map(src.shape, professional, channel)
        else:
            from ..engine import _key

            key = _key(
                src.shape,
                professional.get('geometry'),
                professional.get('optics'),
                professional.get('lensfun'),
            )
            maps = cache.get(
                'remap-' + str(channel),
                key,
                lambda: np.stack(remap_map(src.shape, professional, channel), -1),
            )
            mx, my = maps[..., 0], maps[..., 1]
        out[..., channel] = cv2.remap(
            src[..., channel],
            mx,
            my,
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
    return out


def source_point(point, shape, pro):
    """Same green-channel inverse geometry as remap_map, evaluated at one point."""
    g, o = _geometry_values(pro)
    h, w = shape[:2]
    cx, cy, unit = (w - 1) / 2, (h - 1) / 2, max(w, h) / 2
    x = (point[0] * (w - 1) - cx) / unit - g['offset_x'] / 100 * w / unit * 0.5
    y = (point[1] * (h - 1) - cy) / unit - g['offset_y'] / 100 * h / unit * 0.5
    aspect, zoom = 2 ** (g['aspect'] / 100), g['scale'] / 100
    x /= zoom * np.sqrt(aspect)
    y /= zoom / np.sqrt(aspect)
    x *= -1 if g['flip_x'] else 1
    y *= -1 if g['flip_y'] else 1
    denominator = 1 - 0.22 * g['horizontal'] / 100 * x - 0.22 * g['vertical'] / 100 * y
    if denominator <= 0.05:
        raise ValueError('无效透视位置。')
    x, y = x / denominator, y / denominator
    r2 = ((x * unit) ** 2 + (y * unit) ** 2) / max(np.hypot(cx, cy), 0.5) ** 2
    factor = 1 - 0.2 * o['distortion'] / 100 * r2
    if pro.get('lensfun'):
        from .lensfun_profiles import radial_factor

        factor *= float(
            radial_factor(
                np.hypot(x * unit, y * unit) / max(min(w, h) / 2, 0.5),
                pro['lensfun']['calibration'],
            )
        )
    return [(cx + x * unit * factor) / max(w - 1, 1), (cy + y * unit * factor) / max(h - 1, 1)]


def display_point(point, shape, pro):
    """Invert a nonfolding optical/projective mapping with bounded Newton iterations."""
    target = np.asarray(point, np.float64)
    estimate = target.copy()
    for _ in range(12):
        current = np.asarray(source_point(estimate, shape, pro))
        error = current - target
        if np.max(np.abs(error)) < 1e-7:
            break
        d = 1e-5
        jacobian = np.column_stack(
            [
                (np.asarray(source_point(estimate + axis * d, shape, pro)) - current) / d
                for axis in np.eye(2)
            ]
        )
        try:
            estimate -= np.linalg.solve(jacobian, error)
        except np.linalg.LinAlgError:
            break
    return estimate.tolist()


def warp_mask(alpha, pro):
    if geometry_neutral(pro):
        return alpha
    mx, my = remap_map(alpha.shape, pro)
    return cv2.remap(alpha, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def _blur(plane, radius):
    from ..gpu_spatial import gaussian

    return gaussian(plane, max(0.15, radius), borderType=cv2.BORDER_REFLECT_101)


def _detail(rgb, settings, scale, space='sRGB', cache=None, basis_key=None):
    amount = _number(settings, 'amount', 0, 0, 150)
    radius = _number(settings, 'radius', 1, 0.5, 3) * scale
    detail = _number(settings, 'detail', 25, 0, 100) / 100
    masking = _number(settings, 'masking', 0, 0, 100) / 100
    lum = _number(settings, 'luminance', 0, 0, 100) / 100
    ld = _number(settings, 'luminance_detail', 50, 0, 100) / 100
    lc = _number(settings, 'luminance_contrast', 0, 0, 100) / 100
    color = _number(settings, 'color', 0, 0, 100) / 100
    cd = _number(settings, 'color_detail', 50, 0, 100) / 100
    cs = _number(settings, 'color_smoothness', 50, 0, 100) / 100
    if not (amount or lum or color):
        return rgb
    from ..engine import _key

    def reuse(stage, key, fn):
        return fn() if cache is None else cache.get(stage, key, fn)

    input_key = _key(basis_key, space, scale)
    lab = reuse('detail-lab', input_key, lambda: _lab(_input(rgb), space)).copy()
    original_l = lab[..., 0]
    sigma = max(0.35, 1.5 * scale)
    l_key = _key(input_key, lum, ld, lc)

    def denoised_l():
        if not lum:
            return original_l.copy()
        low = _blur(original_l, sigma)
        residual = original_l - low
        structure = np.abs(cv2.Laplacian(low, cv2.CV_32F))
        protect = 1 - _smooth(0.008 + (1 - ld) * 0.02, 0.05 + (1 - ld) * 0.07, structure)
        return original_l - residual * lum * 0.9 * protect * (1 - lc * 0.6)

    lab[..., 0] = reuse('detail-luminance', l_key, denoised_l)
    if color:
        for channel in (1, 2):
            plane = lab[..., channel]
            smooth = _blur(plane, max(0.35, (1 + cs * 2.5) * scale))
            edge = 1 - _smooth(
                0.03 + (1 - cd) * 0.08, 0.12 + (1 - cd) * 0.12, np.abs(plane - smooth)
            )
            lab[..., channel] = plane + (smooth - plane) * color * edge
    if amount:
        l = lab[..., 0]
        bands = reuse(
            'detail-bands',
            _key(l_key, radius),
            lambda: np.stack([l - _blur(l, radius), l - _blur(l, max(0.15, radius * 0.45))]),
        )
        band, fine = bands
        sharpen = band * (1 - detail) + fine * detail
        edge_mask = _smooth(0.001, 0.005 + masking * 0.045, np.abs(band))
        weight = 1 - masking + masking * edge_mask
        lab[..., 0] += sharpen * (amount / 100) * 1.5 * weight
    return _rgb(lab, space)


def apply_spatial(
    rgb: np.ndarray,
    professional: dict,
    scale: float = 1.0,
    space='sRGB',
    cache=None,
    basis_key=None,
) -> np.ndarray:
    """Linear radial gain → guarded defringe → scaled detail → normalized red-eye.

    ``scale`` is preview pixels per original pixel; radii shrink by that ratio. Tiles
    require caller-managed halo of ceil(4 * max(3, 3.5) * scale) pixels.
    """
    scale = float(scale)
    if not math.isfinite(scale) or scale <= 0 or scale > 16:
        raise ValueError('scale must be finite and within (0, 16]')
    optics = professional.get('optics', {})
    vig = _number(optics, 'vignetting')
    midpoint = _number(optics, 'midpoint', 50, 0, 100)
    purple = _number(optics, 'defringe_purple', 0, 0, 100) / 100
    green = _number(optics, 'defringe_green', 0, 0, 100) / 100
    out = rgb
    if vig:
        src = _input(out)
        h, w = src.shape[:2]
        from ..engine import _key

        def field():
            y, x = np.ogrid[:h, :w]
            y, x = y.astype(np.float32), x.astype(np.float32)
            cx, cy = (w - 1) / 2, (h - 1) / 2
            radius = np.hypot(x - cx, y - cy) / max(np.hypot(cx, cy), 0.5)
            return radius ** (1 + midpoint / 100 * 6)

        radial = (
            field() if cache is None else cache.alpha(_key('lens-vignette', h, w, midpoint), field)
        )

        def linear():
            if space == 'sRGB':
                return _linear(src)
            from . import colorspaces as cs

            return cs.decode(src, space)

        decoded = (
            linear()
            if cache is None
            else cache.get('lens-vignette-linear', _key(basis_key, space), linear)
        )
        from .. import native_kernels

        fast = native_kernels.lens_vignette(decoded, radial, vig, space)
        if fast is not None:
            out = fast
        else:
            gain = 2 ** (vig / 100 * 1.5 * radial)
            if space == 'sRGB':
                out = _encoded(decoded * gain[..., None])
            else:
                from . import colorspaces as cs

                out = cs.encode(decoded * gain[..., None], space)
    if purple or green:
        src = _input(out)
        hsv = cv2.cvtColor(src, cv2.COLOR_RGB2HSV)
        lum = src @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        edge = np.hypot(cv2.Sobel(lum, cv2.CV_32F, 1, 0), cv2.Sobel(lum, cv2.CV_32F, 0, 1))
        edge = cv2.dilate(edge, np.ones((3, 3), np.uint8))
        guard = _smooth(0.15, 0.5, edge) * (1 - _smooth(0.6, 0.9, hsv[..., 1]))
        hp = np.abs((hsv[..., 0] - 300 + 180) % 360 - 180)
        hg = np.abs((hsv[..., 0] - 120 + 180) % 360 - 180)
        weight = guard * (purple * (1 - _smooth(15, 55, hp)) + green * (1 - _smooth(15, 50, hg)))
        out = (src + (lum[..., None] - src) * np.clip(weight, 0, 1)[..., None]).astype(np.float32)
    from ..engine import _key, _spatial_professional

    detail_basis = _key(basis_key, _spatial_professional(professional))
    out = _detail(out, professional.get('detail', {}), scale, space, cache, detail_basis)
    eyes = professional.get('red_eye', [])
    if len(eyes) > 64:
        raise ValueError('red_eye supports at most 64 circles')
    eye_output = False
    for eye in eyes:
        amount = _number(eye, 'amount', 100, 0, 100) / 100
        if not amount or not eye.get('enabled', True):
            continue
        center = np.asarray(eye.get('center', []), np.float32)
        if center.shape != (2,) or not np.isfinite(center).all():
            raise ValueError('red-eye center must contain two finite normalized coordinates')
        radius = _number(eye, 'radius', 0.02, 0.00001, 1)
        if not eye_output:
            out = _input(out).copy()
            eye_output = True
        h, w = out.shape[:2]
        cx, cy = center[0] * (w - 1), center[1] * (h - 1)
        extent = radius * min(h, w)
        x0, x1 = max(0, int(np.floor(cx - extent))), min(w, int(np.ceil(cx + extent)) + 1)
        y0, y1 = max(0, int(np.floor(cy - extent))), min(h, int(np.ceil(cy + extent)) + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        src = out[y0:y1, x0:x1]
        y, x = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        distance = np.hypot(x - cx, y - cy) / extent
        feather = 1 - _smooth(0.75, 1, distance)
        target = (src[..., 1] + src[..., 2]) * 0.5
        excess = np.maximum(src[..., 0] - target, 0)
        weight = _smooth(0.03, 0.15, excess) * feather * amount
        src[..., 0] -= excess * weight
    return out
