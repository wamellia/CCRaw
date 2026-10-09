"""Float32 linear-light RAW pipeline, masks and optional GPU (DirectML / CUDA) processing."""

from __future__ import annotations
import cv2
import numpy as np
from .. import selection
from .. import cpu_ops

from .. import engine as pipeline


def dehaze_basis(rgb):
    """Amount-independent atmospheric light and eroded normalized dark channel."""
    small = pipeline.resize_limit(rgb, 900)
    dark = cv2.erode(np.min(small, axis=2), np.ones((15, 15), np.uint8))
    ids = np.argpartition(dark.ravel(), max(0, dark.size - max(1, dark.size // 500)))[
        -max(1, dark.size // 500) :
    ]
    air = np.maximum(np.max(small.reshape(-1, 3)[ids], axis=0), 0.35)
    norm = np.min(small / air, axis=2)
    dark = cv2.erode(norm, np.ones((15, 15), np.uint8))
    return np.concatenate((air, np.asarray(dark.shape, np.float32), dark.ravel()))


def dehaze_context(rgb, amount, basis=None):
    """Atmospheric light and transmission; cached basis preserves the exact filter."""
    basis = pipeline.dehaze_basis(rgb) if basis is None else basis
    air = basis[:3]
    dark = basis[5:].reshape(tuple(basis[3:5].astype(int)))
    transmission = 1 - min(0.9, amount / 110) * dark
    return (air, cv2.GaussianBlur(transmission, (0, 0), 7))


def dehaze(rgb, amount, context=None, area=None):
    air, transmission = context if context is not None else pipeline.dehaze_context(rgb, amount)
    if area is None:
        transmission = cv2.resize(transmission, (rgb.shape[1], rgb.shape[0]))
    else:
        transmission = pipeline.resize_region(transmission, (area.width, area.height), area)
    result = cpu_ops.dehaze(rgb, air, transmission)
    if result is not None:
        return result
    return np.clip((rgb - air) / np.maximum(transmission[..., None], 0.22) + air, 0, 1)


def reduce_noise(x, a):
    """Luminance and color noise reduction (Lab bilateral filters)."""
    if a.get('denoise', 0) > 0 or a.get('color_noise', 0) > 0:
        lab = cv2.cvtColor(x, cv2.COLOR_RGB2Lab)
        if a.get('denoise', 0) > 0:
            smooth = cv2.bilateralFilter(lab[..., 0], 7, max(1.0, a.get('denoise', 0) / 4), 3)
            mix = a.get('denoise', 0) / 100
            lab[..., 0] = lab[..., 0] * (1 - mix) + smooth * mix
        if a.get('color_noise', 0) > 0:
            mix = a.get('color_noise', 0) / 100
            for c in (1, 2):
                smooth = cv2.bilateralFilter(
                    lab[..., c], 9, max(1.0, a.get('color_noise', 0) / 3), 4
                )
                lab[..., c] = lab[..., c] * (1 - mix) + smooth * mix
        x = np.clip(cv2.cvtColor(lab, cv2.COLOR_Lab2RGB), 0, 1)
    return x


def spatial_details(rgb, a, detail_scale=1.0, reference_shape=None, dehaze_context=None, area=None):
    """Neighbourhood tools. ``reference_shape`` keeps radii of a padded region equal to the full frame;
    a block of a frame (``area``) takes its dehaze statistics from ``dehaze_context``."""
    x = np.ascontiguousarray(rgb, dtype=np.float32)
    short = min((reference_shape or x.shape)[:2])
    x = pipeline.reduce_noise(x, a)
    if a.get('dehaze', 0) > 0:
        x = pipeline.dehaze(x, a.get('dehaze', 0), dehaze_context, area)
    elif a.get('dehaze', 0) < 0:
        x = x * (1 + a.get('dehaze', 0) / 220) - a.get('dehaze', 0) / 220
    if a.get('clarity', 0):
        lum = cv2.cvtColor(x, cv2.COLOR_RGB2GRAY)
        blur = cv2.GaussianBlur(lum, (0, 0), max(1.0, short / 90))
        x = np.clip(x + ((lum - blur) * a.get('clarity', 0) / 65)[..., None], 0, 1)
    if a.get('texture', 0):
        lum = cv2.cvtColor(x, cv2.COLOR_RGB2GRAY)
        radius = max(0.65, short / 800)
        fine = cv2.GaussianBlur(lum, (0, 0), radius)
        coarse = cv2.GaussianBlur(lum, (0, 0), radius * 3.5)
        band = np.clip(fine - coarse, -0.08, 0.08)
        x = np.clip(x + (band * a['texture'] / 65)[..., None], 0, 1)
    if a.get('sharpness', 0) > 0:
        blur = cv2.GaussianBlur(x, (0, 0), max(0.45, detail_scale))
        delta = x - blur
        delta *= np.minimum(1, np.abs(delta) / 0.008)
        x = np.clip(x + delta * a.get('sharpness', 0) / 40, 0, 1)
    return x


def spatial_margin(a, shape, detail_scale=1.0):
    """Pixels of context a padded region needs so spatial_details matches the full frame."""
    short = min(shape[:2])
    margin = 0.0
    if a.get('denoise', 0) > 0 or a.get('color_noise', 0) > 0:
        margin += 5
    if a.get('clarity', 0):
        margin += 4 * max(1.0, short / 90)
    if a.get('texture', 0):
        margin += 4 * 3.5 * max(0.65, short / 800)
    if a.get('sharpness', 0) > 0:
        margin += 4 * max(0.45, detail_scale)
    return int(np.ceil(margin)) + 4 if margin else 0


def details(rgb, a, detail_scale=1.0, reference_shape=None):
    return pipeline.saturation(pipeline.spatial_details(rgb, a, detail_scale, reference_shape), a)


def mask_alpha(mask, shape, reference=None, area=None):
    """Coverage of a mask over a frame of ``shape``, or only over the block ``area`` of it."""
    h, w = shape[:2]
    area = area or pipeline.Area(w, h, 0, 0, w, h)
    ah, aw = area.shape
    feather = mask['feather'] / 100
    if mask['kind'] in ('sky', 'person', 'background', 'color', 'subject', 'foreground'):
        alpha = selection.raster_alpha(mask, shape, area)
        for stroke in mask.get('strokes', []):
            brush = dict(
                mask, kind='brush', opacity=100, invert=False, strokes=[dict(stroke, erase=False)]
            )
            paint = pipeline.mask_alpha(brush, shape, area=area)
            alpha = alpha * (1 - paint) if stroke.get('erase') else np.maximum(alpha, paint)
        if mask['invert']:
            alpha = 1 - alpha
        return alpha * (mask['opacity'] / 100)
    if mask['kind'] == 'luminance':
        if reference is None or reference.shape[:2] != (ah, aw):
            raise ValueError('亮度范围蒙版需要原片亮度作为参考。')
        lum = reference[..., 0] * 0.2126 + reference[..., 1] * 0.7152 + reference[..., 2] * 0.0722
        lo, hi = np.array(mask.get('luminance_range', [50.0, 100.0])) / 100
        falloff = max(0.0001, mask.get('range_falloff', 20.0) / 100)
        left = np.clip((lum - lo + falloff) / falloff, 0, 1)
        right = np.clip((hi + falloff - lum) / falloff, 0, 1)
        alpha = left * left * (3 - 2 * left) * right * right * (3 - 2 * right)
    elif mask['kind'] == 'brush':
        alpha = np.zeros((ah, aw), np.float32)
        for stroke in mask['strokes']:
            radius = max(1.0, stroke['radius'] * min(w, h))
            points = [(p[0] * (w - 1), p[1] * (h - 1)) for p in stroke['points']]
            stamps = []
            for i, p in enumerate(points):
                if i == 0:
                    stamps.append(p)
                else:
                    prev = points[i - 1]
                    count = max(
                        1, int(np.hypot(p[0] - prev[0], p[1] - prev[1]) / max(1, radius * 0.25))
                    )
                    stamps.extend(
                        (
                            (
                                prev[0] + (p[0] - prev[0]) * j / count,
                                prev[1] + (p[1] - prev[1]) * j / count,
                            )
                            for j in range(1, count + 1)
                        )
                    )
            for cx, cy in stamps:
                x0, x1 = (max(area.x0, int(cx - radius)), min(area.x1, int(cx + radius + 2)))
                y0, y1 = (max(area.y0, int(cy - radius)), min(area.y1, int(cy + radius + 2)))
                if x0 >= x1 or y0 >= y1:
                    continue
                yy, xx = np.ogrid[y0:y1, x0:x1]
                distance = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / radius
                stamp = np.clip((1 - distance) / max(feather, 0.01), 0, 1)
                region = alpha[y0 - area.y0 : y1 - area.y0, x0 - area.x0 : x1 - area.x0]
                if stroke['erase']:
                    region *= 1 - stamp
                else:
                    np.maximum(region, stamp, out=region)
    else:
        yy, xx = np.ogrid[area.y0 : area.y1, area.x0 : area.x1]
        xx, yy = (xx / max(w - 1, 1), yy / max(h - 1, 1))
        sx, sy = mask['start']
        ex, ey = mask['end']
        if mask['kind'] == 'linear':
            dx, dy = (ex - sx, ey - sy)
            alpha = np.clip(((xx - sx) * dx + (yy - sy) * dy) / max(dx * dx + dy * dy, 1e-06), 0, 1)
            alpha = alpha * alpha * (3 - 2 * alpha)
        else:
            cx, cy = ((sx + ex) / 2, (sy + ey) / 2)
            rx, ry = (max(abs(ex - sx) / 2, 0.001), max(abs(ey - sy) / 2, 0.001))
            distance = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
            alpha = np.clip((1 - distance) / max(feather, 0.01), 0, 1)
    alpha = alpha.astype(np.float32)
    if mask['invert']:
        alpha = 1 - alpha
    return alpha * (mask['opacity'] / 100)


def apply_local(x, alpha, a, backend, detail_scale=1.0, reference_shape=None):
    """Blend one local adjustment in place, computing only the mask's padded bounding box.

    ``reference_shape`` is the whole frame when ``x`` is a block of it."""
    bounds = pipeline._mask_bounds(alpha)
    if bounds is None:
        return x
    reference_shape = reference_shape or x.shape
    y0, y1, x0, x1 = bounds
    h, w = x.shape[:2]
    if a.get('dehaze', 0) > 0 or (y1 - y0) * (x1 - x0) > 0.6 * h * w:
        py0, py1, px0, px1 = (0, h, 0, w)
    else:
        margin = pipeline.spatial_margin(a, reference_shape, detail_scale)
        py0, py1 = (max(0, y0 - margin), min(h, y1 + margin))
        px0, px1 = (max(0, x0 - margin), min(w, x1 + margin))
    patch = x[py0:py1, px0:px1]
    local = pipeline.details(
        backend.tonal(pipeline.to_linear(patch), a),
        a,
        detail_scale,
        reference_shape=reference_shape,
    )
    local = local[y0 - py0 : y1 - py0, x0 - px0 : x1 - px0]
    weight = alpha[y0:y1, x0:x1, None]
    region = x[y0:y1, x0:x1]
    cpu_ops.blend(region, local, weight)
    return x
