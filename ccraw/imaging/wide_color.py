"""Encoded Rec.2020 controls with signed float samples and no SDR pixel clamp.

Selections and control weights have bounded domains; image samples do not.
Oklab uses sign-preserving cube roots of linear sRGB converted from Rec.2020.
These controls are an independent appearance renderer, not a camera calibration.
ROI neighbourhood tools require the halo returned by spatial.spatial_margin;
positive dehaze additionally needs the full-frame atmosphere/transmission.
"""

from __future__ import annotations

import colorsys
from pathlib import Path

import cv2
import numpy as np

from .. import curves
from .. import native_kernels
from .. import gpu_spatial
from . import colorspaces as cs
from .professional import _lab, _rgb, apply_color

LUMA = np.array([0.2627, 0.6780, 0.0593], np.float32)
SPACE = 'Rec.2020'


def _hues(rgb):
    lab = _lab(rgb, SPACE)
    return lab, np.hypot(lab[..., 1], lab[..., 2]), np.arctan2(lab[..., 2], lab[..., 1])


def _hue_data(controls):
    # The existing eight UI swatches describe sRGB colours. Locate those same
    # colours in Oklab rather than treating Rec.2020 primaries as sRGB swatches.
    samples = np.array(
        [colorsys.hsv_to_rgb(h / 360, 1, 1) for h in (0, 30, 60, 120, 180, 240, 270, 300)],
        np.float32,
    )
    lab = _lab(samples)
    angles = np.mod(np.arctan2(lab[:, 2], lab[:, 1]), 2 * np.pi)
    order = np.argsort(angles)
    values = np.asarray(controls, np.float32)[order]
    axis = angles[order]
    axis = np.concatenate(([axis[-1] - 2 * np.pi], axis, [axis[0] + 2 * np.pi]))
    values = np.concatenate((values[-1:], values, values[:1]))
    return axis, values


def _hue_controls(hue, controls):
    axis, values = _hue_data(controls)
    hue = np.mod(hue, 2 * np.pi)
    fast = native_kernels.hue_shifts(hue, axis, values)
    if fast is not None:
        return fast
    return np.stack([np.interp(hue, axis, values[:, i]) for i in range(values.shape[-1])], -1)


def _curve(x, points, mode='linear'):
    if not points or [list(map(float, p)) for p in points] == curves.IDENTITY:
        return x
    result = native_kernels.curve_exact(x, points, mode)
    if result is not None:
        return result
    xp, yp = np.asarray(points, np.float64).T
    out = curves.evaluate(points, x, mode)
    # Existing smooth curves use endpoint secants as their endpoint tangents.
    left = yp[0] + (x - xp[0]) * (yp[1] - yp[0]) / (xp[1] - xp[0])
    right = yp[-1] + (x - xp[-1]) * (yp[-1] - yp[-2]) / (xp[-1] - xp[-2])
    return np.where(x < xp[0], left, np.where(x > xp[-1], right, out)).astype(np.float32)


def _basic_color(rgb, edits, cache=None, input_key=None):
    out = rgb
    a = edits.get('adjustments', {})
    sat, vib = a.get('saturation', 0), a.get('vibrance', 0)
    if sat or vib:
        fast = native_kernels.saturation(out, sat, vib, wide=True)
        if fast is not None:
            out = fast
        else:
            gray = (out @ LUMA)[..., None]
            spread = np.max(out, axis=-1, keepdims=True) - np.min(out, axis=-1, keepdims=True)
            amount = 1 + sat / 100 + vib / 100 * (1 - np.clip(spread, 0, 1))
            out = (gray + (out - gray) * amount).astype(np.float32)
    values = edits.get('hsl', [[0, 0, 0]] * 8)
    if np.any(values):
        from ..engine import _key

        key = _key(input_key, sat, vib, SPACE)

        def hsl():
            source_lab = (
                _lab(out, SPACE)
                if cache is None or input_key is None
                else cache.get('hsl-lab', key, lambda: _lab(out, SPACE))
            )

            def polar():
                return np.stack(
                    [
                        np.hypot(source_lab[..., 1], source_lab[..., 2]),
                        np.arctan2(source_lab[..., 2], source_lab[..., 1]),
                    ]
                )

            fields = (
                polar()
                if cache is None or input_key is None
                else cache.get('hsl-polar', key, polar)
            )
            axis, ordered = _hue_data(values)
            parameters = native_kernels.hsl_parameters(source_lab, fields, axis, ordered)
            if parameters is not None:
                lab = parameters
                chroma, hue = parameters[..., 1].copy(), parameters[..., 2].copy()
            else:
                lab = source_lab.copy()
                chroma, hue = fields[0].copy(), fields[1].copy()
                shifts = _hue_controls(hue, values)
                hue += np.deg2rad(shifts[..., 0] * 0.45)
                chroma *= np.maximum(0, 1 + shifts[..., 1] / 100)
                lab[..., 0] *= 2 ** (shifts[..., 2] / 100)
            lab[..., 1], lab[..., 2] = chroma * np.cos(hue), chroma * np.sin(hue)
            return _rgb(lab, SPACE)

        out = (
            hsl()
            if cache is None or input_key is None
            else cache.get('hsl-result', _key(key, values), hsl)
        )
    out = _curve(out, edits.get('tone_curve'))
    for channel, points in edits.get('curves', {}).items():
        if points == curves.IDENTITY:
            continue
        if channel == 'RGB':
            out = _curve(out, points, edits.get('curve_mode', 'linear'))
        else:
            changed = out.copy()
            index = 'RGB'.index(channel)
            changed[..., index] = _curve(out[..., index], points, edits.get('curve_mode', 'linear'))
            out = changed
    return out


def color_base(rgb, edits, cache=None, basis_key=None, input_key=None):
    """Rec.2020 saturation, Oklab hue, extended curves and native professional colour."""
    out = (
        _basic_color(rgb, edits)
        if cache is None
        else cache.get(
            'pre-professional', basis_key, lambda: _basic_color(rgb, edits, cache, input_key)
        )
    )
    pro = edits.get('professional', {})
    if pro:
        # BW is handled below using native luma, avoiding the SDR adapter's BW clamp.
        out = apply_color(out, dict(pro, monochrome=False), SPACE, cache=cache, basis_key=basis_key)
        profile = pro.get('profile', {})
        if profile.get('path') and profile.get('amount', 100):
            from .profiles import apply_profile, load_dcp

            if Path(profile['path']).suffix.lower() == '.dcp':
                if profile.get('mode') == 'camera':
                    raise ValueError('DCP 相机矩阵必须在 RAW 原生输入阶段应用。')
                linear = cs.decode(out, SPACE)
                changed = load_dcp(profile['path']).apply_look(linear, srcspace=SPACE)
                out = cs.encode(
                    linear + (changed - linear) * profile.get('amount', 100) / 100, SPACE
                )
            else:
                # Cube resources explicitly bound their declared domain. ICC uses
                # native float samples; neither conversion clips to encoded sRGB.
                encoded = cs.convert(out, SPACE, 'sRGB')
                from ..engine import _key

                out = cs.convert(
                    apply_profile(
                        encoded,
                        profile,
                        cache=cache,
                        basis_key=_key(
                            basis_key, pro.get('calibration'), pro.get('point_color'), SPACE
                        ),
                    ),
                    'sRGB',
                    SPACE,
                )
    if edits.get('monochrome', False):
        from ..engine import _key, _resource_stamp

        bw_key = _key(
            basis_key,
            pro.get('calibration'),
            pro.get('point_color'),
            pro.get('profile'),
            _resource_stamp(pro),
        )
        gray = (
            out @ LUMA if cache is None else cache.get('bw-gray', bw_key, lambda: out @ LUMA)
        ).copy()
        mixer = pro.get('bw_mixer', [0] * 8)
        if np.any(mixer):

            def polar():
                _, chroma, hue = _hues(out)
                return np.stack([chroma, hue])

            fields = polar() if cache is None else cache.get('bw-polar', bw_key, polar)
            chroma, hue = fields
            weights = _hue_controls(hue, np.asarray(mixer)[:, None])[..., 0] / 100
            gray += weights * chroma * 0.5
        out = np.repeat(gray[..., None], 3, -1).astype(np.float32)
    return out


def color_grade(rgb, grading):
    if not any(grading.get(zone, [0, 0])[1] for zone in ('shadows', 'midtones', 'highlights')):
        return rgb
    fast = native_kernels.grade(rgb, grading, wide=True)
    if fast is not None:
        return fast
    lum = rgb @ LUMA
    tone = np.clip(lum + grading.get('balance', 0) / 300, 0, 1)
    weights = [(1 - tone) ** 2, 2 * tone * (1 - tone), tone**2]
    protection = 0.2 + 0.8 * np.sin(np.pi * np.clip(lum, 0, 1))
    out = rgb.copy()
    for zone, weight in zip(('shadows', 'midtones', 'highlights'), weights):
        hue, strength = grading.get(zone, [0, 0])
        if strength:
            colour = np.asarray(colorsys.hsv_to_rgb(hue % 360 / 360, 1, 1), np.float32)
            colour -= colour @ LUMA
            out += (weight * protection * strength / 350)[..., None] * colour
    return out.astype(np.float32)


def finishing(rgb, effects, crop=None, area=None, cache=None):
    vignette, grain = effects.get('vignette', 0), effects.get('grain', 0)
    if not vignette and not grain:
        return rgb
    from .. import engine

    area = area or engine.Area(rgb.shape[1], rgb.shape[0], 0, 0, rgb.shape[1], rgb.shape[0])
    cache = cache if cache is not None else engine._UNCACHED
    h, w = area.height, area.width
    out = rgb.copy()
    if vignette:

        def edge():
            a, b, c, d = crop or [0, 0, 1, 1]
            y, x = np.ogrid[area.y0 : area.y1, area.x0 : area.x1]
            x = (x / max(w - 1, 1) - (a + c) / 2) / max((c - a) / 2, 0.001)
            y = (y / max(h - 1, 1) - (b + d) / 2) / max((d - b) / 2, 0.001)
            radius = np.hypot(x, y) / np.sqrt(2)
            midpoint = 0.05 + effects.get('midpoint', 50) / 100 * 0.75
            softness = 0.08 + effects.get('feather', 70) / 100 * 0.72
            weight = np.clip((radius - midpoint) / softness, 0, 1)
            return weight * weight * (3 - 2 * weight)

        weight = cache.alpha(
            engine._key(
                'wide-vignette', area, crop, effects.get('midpoint', 50), effects.get('feather', 70)
            ),
            edge,
        )
        if vignette < 0:
            out *= (2 ** (weight * vignette / 55))[..., None]
        else:
            out += (1 - out) * (weight * vignette / 170)[..., None]
    if grain:
        cells = int(1600 / (1 + effects.get('grain_size', 30) / 20))
        gh, gw = max(4, round(cells * h / max(h, w))), max(4, round(cells * w / max(h, w)))

        def noise():
            field = np.random.default_rng(17031).normal(0, 1, (gh, gw)).astype(np.float32)
            return (
                cv2.resize(field, (w, h), interpolation=cv2.INTER_LINEAR)
                if area.shape == (h, w)
                else engine.resize_region(field, (w, h), area)
            )

        field = cache.alpha(engine._key('wide-grain', area, gh, gw), noise)
        lum = np.clip(out @ LUMA, 0, 1)
        response = 0.35 + 0.65 * (4 * lum * (1 - lum))
        out += (field * response * grain / 2200)[..., None]
    return out.astype(np.float32)


def reduce_noise(rgb, a):
    luminance, colour = a.get('denoise', 0), a.get('color_noise', 0)
    if not luminance and not colour:
        return rgb
    lab = _lab(rgb, SPACE)
    if luminance:
        smooth = cv2.bilateralFilter(lab[..., 0], 7, max(0.01, luminance / 400), 3)
        lab[..., 0] += (smooth - lab[..., 0]) * luminance / 100
    if colour:
        for channel in (1, 2):
            smooth = cv2.bilateralFilter(lab[..., channel], 9, max(0.01, colour / 300), 4)
            lab[..., channel] += (smooth - lab[..., channel]) * colour / 100
    return _rgb(lab, SPACE)


def dehaze(rgb, amount, context=None, area=None):
    if not amount:
        return rgb
    if amount < 0:
        return (rgb * (1 + amount / 220) - amount / 220).astype(np.float32)
    from .. import engine
    from .spatial import dehaze_context

    air, transmission = context if context is not None else dehaze_context(rgb, amount)
    transmission = (
        cv2.resize(transmission, (rgb.shape[1], rgb.shape[0]))
        if area is None
        else engine.resize_region(transmission, (area.width, area.height), area)
    )
    return ((rgb - air) / np.maximum(transmission[..., None], 0.22) + air).astype(np.float32)


def spatial_details(
    rgb,
    a,
    detail_scale=1.0,
    reference_shape=None,
    dehaze_context=None,
    area=None,
    cache=None,
    basis_key=None,
):
    if not any(
        a.get(key, 0)
        for key in ('denoise', 'color_noise', 'dehaze', 'clarity', 'texture', 'sharpness')
    ):
        return rgb
    short = min((reference_shape or rgb.shape)[:2])
    accelerated = gpu_spatial.chain(
        rgb, a, detail_scale, reference_shape, wide=True,
        area=area, cache=cache, basis_key=basis_key,
    )
    if accelerated is not None:
        return accelerated
    from ..engine import _key

    def reuse(stage, key, fn):
        return fn() if cache is None else cache.get(stage, key, fn)

    key = _key(basis_key, a.get('denoise', 0), a.get('color_noise', 0))
    out = reuse('spatial-noise', key, lambda: reduce_noise(rgb, a))
    if a.get('dehaze', 0):
        out = dehaze(out, a['dehaze'], dehaze_context, area)
    key = _key(key, a.get('dehaze', 0), short, area)
    if a.get('clarity', 0):

        def clarity_band():
            lum = out @ LUMA
            return lum - gpu_spatial.gaussian(lum, max(1.0, short / 90))

        band = reuse('clarity-band', key, clarity_band)
        out = out + (band * a['clarity'] / 65)[..., None]
    key = _key(key, a.get('clarity', 0))
    if a.get('texture', 0):

        def texture_band():
            lum = out @ LUMA
            radius = max(0.65, short / 800)
            return gpu_spatial.gaussian(lum, radius) - gpu_spatial.gaussian(
                lum, radius * 3.5
            )

        band = reuse('texture-band', key, texture_band)
        out = out + (band * a['texture'] / 65)[..., None]
    key = _key(key, a.get('texture', 0), detail_scale)
    if a.get('sharpness', 0):

        def sharpen_band():
            blur = gpu_spatial.gaussian(out, max(0.45, detail_scale))
            delta = out - blur
            delta *= np.minimum(1, np.abs(delta) / 0.008)
            return delta

        delta = reuse('sharpen-band', key, sharpen_band)
        out = out + delta * a['sharpness'] / 40
    return out.astype(np.float32)
