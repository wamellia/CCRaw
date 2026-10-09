"""Float32 linear-light RAW pipeline, masks and optional GPU (DirectML / CUDA) processing."""

from __future__ import annotations
import cv2
import numpy as np
from ..model import COLORS
from .. import curves as tone_curves
from .. import large_image, cpu_ops, native_kernels

from .. import engine as pipeline


def to_linear(rgb):
    if rgb.nbytes > large_image.MAP_BYTES and rgb.shape[0] > large_image.STRIP_ROWS:
        out = large_image.allocate(rgb.shape)
        for y, block in large_image.strips(rgb):
            out[y : y + len(block)] = pipeline.to_linear(block)
        return out
    if rgb.dtype == np.float32:
        result = cpu_ops.linear(rgb)
        if result is not None:
            return result
    return np.where(
        rgb <= 0.04045, rgb / 12.92, np.maximum((rgb + 0.055) / 1.055, 0) ** 2.4
    ).astype(np.float32)


def to_srgb(rgb):
    if rgb.nbytes > large_image.MAP_BYTES and rgb.shape[0] > large_image.STRIP_ROWS:
        out = large_image.allocate(rgb.shape)
        for y, block in large_image.strips(rgb):
            out[y : y + len(block)] = pipeline.to_srgb(block)
        return out
    if rgb.dtype == np.float32:
        result = cpu_ops.srgb(rgb, contrast=None)
        if result is not None:
            return result
    rgb = np.maximum(rgb, 0)
    return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055).astype(
        np.float32
    )


def saturation(x, a):
    if not a.get('saturation', 0) and (not a.get('vibrance', 0)):
        return x
    lum = x[..., 0:1] * 0.2126 + x[..., 1:2] * 0.7152 + x[..., 2:3] * 0.0722
    amount = 1 + a.get('saturation', 0) / 100
    if a.get('vibrance', 0):
        spread = np.max(x, axis=2, keepdims=True) - np.min(x, axis=2, keepdims=True)
        amount = amount + a.get('vibrance', 0) / 100 * (1 - spread)
    return np.clip(lum + (x - lum) * amount, 0, 1)


def monochrome(x):
    lum = x[..., 0] * 0.2126 + x[..., 1] * 0.7152 + x[..., 2] * 0.0722
    return np.repeat(lum[..., None], 3, axis=2)


def color_stage(x, edits):
    """CPU reference for the pointwise color graph (after spatial details)."""
    return pipeline.color_grade(pipeline.color_base(x, edits), edits.get('grading', {}))


def color_base(x, edits):
    """Color stages before grading, reusable while a wheel is dragged."""
    x = pipeline.saturation(x, edits['adjustments'])
    x = pipeline.apply_hsl(x, edits['hsl'])
    x = pipeline.apply_curves(
        x, edits['curves'], edits.get('curve_mode', 'linear'), edits.get('tone_curve')
    )
    if edits.get('monochrome', False):
        x = pipeline.monochrome(x)
    return x


def apply_hsl(rgb, values):
    if not np.any(values):
        return rgb
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2HSV)
    hue = hsv[..., 0].copy()
    centers = [c[1] for c in COLORS] + [360]
    controls = np.asarray(list(values) + [values[0]], dtype=np.float32)
    result = native_kernels.hsl(hsv, centers, controls)
    if result is not None:
        return cv2.cvtColor(result, cv2.COLOR_HSV2RGB)
    dh = np.interp(hue, centers, controls[:, 0]) * 0.45
    ds = np.interp(hue, centers, controls[:, 1]) / 100
    dv = np.interp(hue, centers, controls[:, 2]) / 100
    hsv[..., 0] = (hue + dh) % 360
    hsv[..., 1] = np.clip(hsv[..., 1] * (1 + ds), 0, 1)
    hsv[..., 2] = np.clip(hsv[..., 2] * 2**dv, 0, 1)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)


def apply_curves(rgb, curves, mode='linear', tone=None):
    x = rgb
    if tone and tone != tone_curves.IDENTITY:
        axis = np.linspace(0, 1, 4097)
        x = pipeline.uniform_interp(
            x, tone_curves.rgb_table(dict(curves=curves, curve_mode=mode, tone_curve=tone), axis)
        )
        curves = {c: p for c, p in curves.items() if c != 'RGB'}
    for channel, points in curves.items():
        if points == [[0.0, 0.0], [1.0, 1.0]]:
            continue
        axis = np.linspace(0, 1, 4097) if mode == 'smooth' else np.asarray(points)[:, 0]
        values = tone_curves.evaluate(points, axis, mode)
        if channel == 'RGB':
            if mode == 'smooth':
                x = pipeline.uniform_interp(x, values)
            else:
                result = native_kernels.interp(x, values, axis)
                x = result if result is not None else np.interp(x, axis, values).astype(np.float32)
        else:
            x = x.copy()
            c = 'RGB'.index(channel)
            if mode == 'smooth':
                x[..., c] = pipeline.uniform_interp(x[..., c], values)
            else:
                result = native_kernels.interp(x[..., c], values, axis)
                x[..., c] = result if result is not None else np.interp(x[..., c], axis, values)
    return x


def uniform_interp(x, values):
    """The same piecewise-linear 4097-knot curve with O(1) interval lookup.

    Multiplying float32 by 4096 is exact. Interpolation remains float64, as in
    np.interp, then rounds once to float32. No coarser LUT or 8-bit quantization.
    """
    result = native_kernels.interp(x, values)
    if result is not None:
        return result
    position = np.clip(x, 0, 1) * (len(values) - 1)
    index = np.minimum(position.astype(np.int32), len(values) - 2)
    result = cpu_ops.interp(np.clip(x, 0, 1), values, index)
    if result is not None:
        return result
    fraction = position.astype(np.float64) - index
    return (values[index] + (values[index + 1] - values[index]) * fraction).astype(np.float32)


def color_grade(rgb, grading):
    """Three tonal wheels with luminance-neutral tint vectors and soft weights."""
    if not any((grading.get(z, [0, 0])[1] for z in ('shadows', 'midtones', 'highlights'))):
        return rgb
    result = cpu_ops.grade(rgb, grading)
    if result is not None:
        return result
    import colorsys

    lum = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    tone = np.clip(lum + grading.get('balance', 0) / 300, 0, 1)
    weights = [(1 - tone) ** 2, 2 * tone * (1 - tone), tone**2]
    out = rgb.copy()
    protection = 0.2 + 0.8 * np.sin(np.pi * np.clip(lum, 0, 1))
    for zone, weight in zip(('shadows', 'midtones', 'highlights'), weights):
        hue, strength = grading.get(zone, [0, 0])
        if not strength:
            continue
        color = np.asarray(colorsys.hsv_to_rgb(hue % 360 / 360, 1, 1), np.float32)
        color -= np.dot(color, [0.2126, 0.7152, 0.0722])
        out += (weight * protection * strength / 350)[..., None] * color
    return np.clip(out, 0, 1)


def finishing(rgb, settings, crop=None, area=None, cache=None):
    """Vignette and grain in frame coordinates; ``rgb`` may be the block ``area`` of the frame."""
    vignette, grain = (settings.get('vignette', 0), settings.get('grain', 0))
    if not vignette and (not grain):
        return rgb
    area = area or pipeline.Area(rgb.shape[1], rgb.shape[0], 0, 0, rgb.shape[1], rgb.shape[0])
    cache = cache if cache is not None else pipeline._UNCACHED
    h, w = (area.height, area.width)
    x = rgb.copy()
    if vignette:

        def vignette_edge():
            a, b, c, d = crop or [0, 0, 1, 1]
            yy, xx = np.ogrid[area.y0 : area.y1, area.x0 : area.x1]
            nx = (xx / max(w - 1, 1) - (a + c) / 2) / max((c - a) / 2, 0.001)
            ny = (yy / max(h - 1, 1) - (b + d) / 2) / max((d - b) / 2, 0.001)
            radius = np.sqrt(nx * nx + ny * ny) / 1.41421356
            midpoint = 0.05 + settings.get('midpoint', 50) / 100 * 0.75
            softness = 0.08 + settings.get('feather', 70) / 100 * 0.72
            edge = np.clip((radius - midpoint) / softness, 0, 1)
            return edge * edge * (3 - 2 * edge)

        edge = cache.alpha(
            pipeline._key(
                'vignette-edge',
                area,
                crop,
                settings.get('midpoint', 50),
                settings.get('feather', 70),
            ),
            vignette_edge,
        )
        result = cpu_ops.vignette(x, edge, vignette)
        if result is not None:
            x = result
        elif vignette < 0:
            x *= (2 ** (edge * vignette / 55))[..., None]
        else:
            x += (1 - x) * (edge * vignette / 170)[..., None]
    if grain:
        cells = int(1600 / (1 + settings.get('grain_size', 30) / 20))
        gh, gw = (max(4, round(cells * h / max(h, w))), max(4, round(cells * w / max(h, w))))

        def grain_field():
            noise = np.random.default_rng(17031).normal(0, 1, (gh, gw)).astype(np.float32)
            return (
                cv2.resize(noise, (w, h), interpolation=cv2.INTER_LINEAR)
                if area.shape == (h, w)
                else pipeline.resize_region(noise, (w, h), area)
            )

        noise = cache.alpha(pipeline._key('grain-field', area, gh, gw), grain_field)
        lum = np.mean(x, axis=2)
        response = 0.35 + 0.65 * (4 * lum * (1 - lum))
        x += (noise * response * grain / 2200)[..., None]
    return np.clip(x, 0, 1)
