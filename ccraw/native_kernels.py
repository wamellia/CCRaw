"""Curve lookup, HSL and signed RGB transforms without full-image temporaries.

The float64 interpolation used by np.interp is retained, then rounded to
float32 once. Kernels release the GIL, use bounded CPU threads and disable
fastmath. The optional backend warms in the image worker before editing.
"""

import logging
import colorsys
import os
import sys
import threading
import time
from functools import partial
import numpy as np
from . import performance

log = logging.getLogger(__name__)
_kernel_lock = threading.Lock()
JIT_CACHE = not getattr(sys, 'frozen', False)
os.environ.setdefault('NUMBA_NUM_THREADS', str(min(8, performance.THREADS)))
try:
    from numba import njit, prange, set_num_threads, get_num_threads

    enabled = True
except ImportError:
    enabled = False


if enabled:

    @njit(cache=JIT_CACHE, nogil=True, fastmath=False, inline='always')
    def _transfer_value(value, space, encode):
        v = abs(value)
        if space <= 1:
            if encode:
                y = v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
            else:
                y = v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        elif space == 2:
            y = v ** (256 / 563 if encode else 563 / 256)
        elif space == 3:
            if encode:
                y = v * 16 if v <= 1 / 512 else v ** (1 / 1.8)
            else:
                y = v / 16 if v <= 1 / 32 else v**1.8
        else:
            alpha, beta = 1.09929682680944, 0.018053968510807
            if encode:
                y = v * 4.5 if v < beta else alpha * v**0.45 - (alpha - 1)
            else:
                y = v / 4.5 if v < 4.5 * beta else ((v + alpha - 1) / alpha) ** (1 / 0.45)
        return -y if value < 0 else (y if value > 0 else 0.0)

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _convert_rgb(x, matrix, src, dst):
        out = np.empty_like(x)
        for i in prange(x.shape[0]):
            r = np.float64(np.float32(_transfer_value(np.float64(x[i, 0]), src, False)))
            g = (
                r
                if x[i, 1] == x[i, 0]
                else np.float64(np.float32(_transfer_value(np.float64(x[i, 1]), src, False)))
            )
            b = (
                r
                if x[i, 2] == x[i, 0]
                else (
                    g
                    if x[i, 2] == x[i, 1]
                    else np.float64(np.float32(_transfer_value(np.float64(x[i, 2]), src, False)))
                )
            )
            last_linear = np.float32(0)
            last_encoded = np.float32(0)
            for c in range(3):
                linear = np.float32(r * matrix[c, 0] + g * matrix[c, 1] + b * matrix[c, 2])
                encoded = (
                    last_encoded
                    if c and linear == last_linear
                    else np.float32(_transfer_value(np.float64(linear), dst, True))
                )
                out[i, c] = encoded
                last_linear, last_encoded = linear, encoded
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _wide_tonal(x, gains, controls, contrast):
        out = np.empty_like(x)
        sh, hi, bl, wh = controls
        ranged = sh != 0 or hi != 0 or bl != 0 or wh != 0
        factor = np.float32(1 + contrast / 125)
        for i in prange(x.shape[0]):
            r, g, b = (
                np.float32(x[i, 0] * gains[0]),
                np.float32(x[i, 1] * gains[1]),
                np.float32(x[i, 2] * gains[2]),
            )
            gain = np.float32(1)
            if ranged:
                lum = np.float32(
                    np.float32(
                        np.float32(r * np.float32(0.2627)) + np.float32(g * np.float32(0.6780))
                    )
                    + np.float32(b * np.float32(0.0593))
                )
                p = np.float32(min(np.float32(1), max(np.float32(0), lum)) ** np.float32(0.45))
                q = np.float32(1) - p
                stops = np.float32(
                    np.float32(
                        sh * np.float32(q ** np.float32(2)) + hi * np.float32(p ** np.float32(3))
                    )
                    / np.float32(65)
                )
                stops = np.float32(
                    stops
                    + np.float32(
                        np.float32(
                            bl * np.float32(q ** np.float32(6))
                            + wh * np.float32(p ** np.float32(6))
                        )
                        / np.float32(85)
                    )
                )
                gain = np.float32(np.float32(2) ** stops)
            for c in range(3):
                v = r if c == 0 else (g if c == 1 else b)
                v = np.float32(_transfer_value(np.float64(np.float32(v * gain)), 4, True))
                if contrast:
                    v = np.float32(np.float32(v - np.float32(0.5)) * factor + np.float32(0.5))
                out[i, c] = v
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _grade(x, colors, strengths, balance, luma, wide):
        out = np.empty_like(x)
        for i in prange(x.shape[0]):
            lum = np.float32(
                np.float32(np.float32(x[i, 0] * luma[0]) + np.float32(x[i, 1] * luma[1]))
                + np.float32(x[i, 2] * luma[2])
            )
            tone = min(np.float32(1), max(np.float32(0), np.float32(lum + balance)))
            v = min(np.float32(1), max(np.float32(0), lum))
            protection = np.float32(
                np.float32(0.2)
                + np.float32(np.float32(0.8) * np.sin(np.float32(np.float32(np.pi) * v)))
            )
            q = np.float32(1) - tone
            for c in range(3):
                value = x[i, c]
                for zone in range(3):
                    weight = (
                        np.float32(q * q)
                        if zone == 0
                        else (
                            np.float32(np.float32(np.float32(2) * tone) * q)
                            if zone == 1
                            else np.float32(tone * tone)
                        )
                    )
                    amount = np.float32(
                        np.float32(np.float32(weight * protection) * strengths[zone])
                        / np.float32(350)
                    )
                    value = np.float32(value + np.float32(amount * colors[zone, c]))
                out[i, c] = value if wide else min(np.float32(1), max(np.float32(0), value))
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _rgb8(x):
        out = np.empty(x.size, np.uint8)
        for i in prange(x.size):
            v = x[i]
            out[i] = (
                0
                if np.isnan(v) or v <= 0
                else (255 if v >= 1 else np.uint8(np.float32(v * np.float32(255))))
            )
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _tone_weights(x):
        out = np.empty((4, x.shape[0]), np.float32)
        for i in prange(x.shape[0]):
            lum = np.float32(
                np.float32(
                    np.float32(x[i, 0] * np.float32(0.2126))
                    + np.float32(x[i, 1] * np.float32(0.7152))
                )
                + np.float32(x[i, 2] * np.float32(0.0722))
            )
            p = np.float32(min(np.float32(1), max(np.float32(0), lum)) ** np.float32(0.45))
            q = np.float32(1) - p
            out[0, i] = q ** np.float32(2)
            out[1, i] = p ** np.float32(3)
            out[2, i] = q ** np.float32(6)
            out[3, i] = p ** np.float32(6)
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _remap_map(
        h, w, vertical, horizontal, aspect, offset_x, offset_y, zoom, flip_x, flip_y, distortion, ca
    ):
        maps = np.empty((2, h, w), np.float32)
        flags = np.zeros(h, np.uint8)
        cx, cy = (w - 1) / 2, (h - 1) / 2
        unit = max(w, h) / 2
        diagonal = max(np.hypot(cx, cy), 0.5)
        ox, oy = (
            np.float32(offset_x / 100 * w / unit * 0.5),
            np.float32(offset_y / 100 * h / unit * 0.5),
        )
        zx, zy = zoom * np.sqrt(aspect), zoom / np.sqrt(aspect)
        hx, vy = np.float32(0.22 * horizontal / 100), np.float32(0.22 * vertical / 100)
        for row in prange(h):
            invalid = np.uint8(0)
            for col in range(w):
                x = np.float32(
                    np.float32(np.float32(np.float32(col) - np.float32(cx)) / np.float32(unit)) - ox
                )
                y = np.float32(
                    np.float32(np.float32(np.float32(row) - np.float32(cy)) / np.float32(unit)) - oy
                )
                x, y = np.float32(np.float64(x) / zx), np.float32(np.float64(y) / zy)
                if flip_x:
                    x = -x
                if flip_y:
                    y = -y
                denominator = np.float32(
                    np.float32(np.float32(1) - np.float32(hx * x)) - np.float32(vy * y)
                )
                if denominator < 0.05:
                    invalid |= np.uint8(1)
                    maps[0, row, col] = 0
                    maps[1, row, col] = 0
                    continue
                x, y = np.float32(x / denominator), np.float32(y / denominator)
                xu, yu = np.float32(x * np.float32(unit)), np.float32(y * np.float32(unit))
                radius2 = np.float64(np.float32(np.float32(xu * xu) + np.float32(yu * yu))) / (
                    diagonal**2
                )
                radial = 1 - 0.2 * distortion / 100 * radius2
                if radial <= 0.05 or 1 - 0.6 * distortion / 100 * radius2 <= 0.01:
                    invalid |= np.uint8(2)
                factor = radial * (1 + 0.003 * ca / 100)
                maps[0, row, col] = cx + np.float64(xu) * factor
                maps[1, row, col] = cy + np.float64(yu) * factor
            flags[row] = invalid
        return maps, flags

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _saturation(x, saturation, vibrance, luma, wide):
        out = np.empty_like(x)
        for i in prange(x.shape[0]):
            r, g, b = x[i, 0], x[i, 1], x[i, 2]
            gray = np.float32(
                np.float32(np.float32(r * luma[0]) + np.float32(g * luma[1]))
                + np.float32(b * luma[2])
            )
            spread = np.float32(max(r, g, b) - min(r, g, b))
            if wide:
                spread = min(np.float32(1), max(np.float32(0), spread))
            amount = np.float32(
                np.float32(1 + saturation / 100)
                + np.float32(np.float32(vibrance / 100) * np.float32(1 - spread))
            )
            for c in range(3):
                value = np.float32(gray + np.float32(np.float32(x[i, c] - gray) * amount))
                out[i, c] = value if wide else min(np.float32(1), max(np.float32(0), value))
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _curve_exact(x, axis, values, slopes, smooth):
        out = np.empty(x.size, np.float32)
        for i in prange(x.size):
            v = np.float64(x[i])
            if v < axis[0]:
                y = values[0] + (v - axis[0]) * (values[1] - values[0]) / (axis[1] - axis[0])
            elif v > axis[-1]:
                y = values[-1] + (v - axis[-1]) * (values[-1] - values[-2]) / (axis[-1] - axis[-2])
            elif not smooth:
                y = _lookup(v, axis, values)
            else:
                lo, hi = 0, len(axis) - 1
                while hi - lo > 1:
                    mid = (hi + lo) // 2
                    if v >= axis[mid]:
                        lo = mid
                    else:
                        hi = mid
                h = axis[lo + 1] - axis[lo]
                t = min(1.0, max(0.0, (v - axis[lo]) / h))
                y = (
                    (2 * t**3 - 3 * t**2 + 1) * values[lo]
                    + (t**3 - 2 * t**2 + t) * h * slopes[lo]
                    + (-2 * t**3 + 3 * t**2) * values[lo + 1]
                    + (t**3 - t**2) * h * slopes[lo + 1]
                )
                y = min(1.0, max(0.0, y))
            out[i] = y
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _area_rows(x, offset, src_height, dst_height, begin, end):
        out = np.empty((end - begin, x.shape[1], x.shape[2]), np.float32)
        scale = src_height / dst_height
        for row in prange(end - begin):
            top, bottom = (row + begin) * scale, (row + begin + 1) * scale
            lo, hi = int(np.floor(top)), min(src_height, int(np.ceil(bottom)))
            for col in range(x.shape[1]):
                for c in range(x.shape[2]):
                    total = 0.0
                    for y in range(lo, hi):
                        weight = max(0.0, min(bottom, y + 1.0) - max(top, float(y))) / scale
                        total += np.float64(x[y - offset, col, c]) * weight
                    out[row, col, c] = total
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _point_adjust(lab, weights, hue, saturation, luminance):
        out = np.empty_like(lab)
        rotation = np.deg2rad(hue * 0.6)
        for i in prange(lab.shape[0]):
            w = weights[i]
            if w <= 0:
                out[i, 0] = lab[i, 0]
                out[i, 1] = lab[i, 1]
                out[i, 2] = lab[i, 2]
                continue
            c = np.hypot(lab[i, 1], lab[i, 2])
            h = np.arctan2(lab[i, 2], lab[i, 1])
            gain = np.float32(1) + np.float32(
                np.float32(w * np.float32(saturation)) / np.float32(100)
            )
            c = np.float32(c * max(np.float32(0), gain))
            shifted = np.float64(h) + np.float64(w) * rotation
            delta = np.float32(
                np.float32(np.float32(w * np.float32(luminance)) / np.float32(100))
                * np.float32(0.2)
            )
            out[i, 0] = np.float32(lab[i, 0] + delta)
            out[i, 1], out[i, 2] = c * np.cos(shifted), c * np.sin(shifted)
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _matrix(x, coefficients):
        out = np.empty_like(x)
        for i in prange(x.shape[0]):
            r, g, b = np.float64(x[i, 0]), np.float64(x[i, 1]), np.float64(x[i, 2])
            for c in range(3):
                out[i, c] = r * coefficients[c, 0] + g * coefficients[c, 1] + b * coefficients[c, 2]
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _transfer(x, space, encode):
        """Named RGB transfer functions, double reference math and signed output."""
        out = np.empty(x.size, np.float32)
        for i in prange(x.size):
            value = np.float64(x[i])
            v = abs(value)
            if space <= 1:  # sRGB / Display P3
                if encode:
                    y = v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
                else:
                    y = v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
            elif space == 2:  # Adobe RGB (1998)
                y = v ** (256 / 563 if encode else 563 / 256)
            elif space == 3:  # ProPhoto RGB
                if encode:
                    y = v * 16 if v <= 1 / 512 else v ** (1 / 1.8)
                else:
                    y = v / 16 if v <= 1 / 32 else v**1.8
            else:  # Rec.2020
                alpha, beta = 1.09929682680944, 0.018053968510807
                if encode:
                    y = v * 4.5 if v < beta else alpha * v**0.45 - (alpha - 1)
                else:
                    y = v / 4.5 if v < 4.5 * beta else ((v + alpha - 1) / alpha) ** (1 / 0.45)
            out[i] = -y if value < 0 else (y if value > 0 else 0)
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _uniform(x, table):
        out = np.empty(x.size, np.float32)
        scale = float(len(table) - 1)
        for i in prange(x.size):
            v = float(x[i])
            if np.isnan(v):
                out[i] = np.nan
            elif v <= 0:
                out[i] = table[0]
            elif v >= 1:
                out[i] = table[-1]
            else:
                position = v * scale
                index = int(position)
                out[i] = table[index] + (table[index + 1] - table[index]) * (position - index)
        return out

    @njit(cache=JIT_CACHE, nogil=True, fastmath=False)
    def _lookup(v, axis, table):
        if np.isnan(v):
            return np.nan
        if v <= axis[0]:
            return float(table[0])
        if v >= axis[-1]:
            return float(table[-1])
        low, high = 0, len(axis) - 1
        while high - low > 1:
            middle = (low + high) // 2
            if v >= axis[middle]:
                low = middle
            else:
                high = middle
        # Match np.interp's slope-first arithmetic and float64 intermediates.
        slope = (float(table[high]) - float(table[low])) / (axis[high] - axis[low])
        return slope * (v - axis[low]) + float(table[low])

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _sparse(x, axis, table):
        out = np.empty(x.size, np.float32)
        for i in prange(x.size):
            out[i] = _lookup(float(x[i]), axis, table)
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _hue_shifts(hue, axis, controls):
        out = np.empty((hue.size, controls.shape[1]), np.float64)
        for i in prange(hue.size):
            for c in range(controls.shape[1]):
                out[i, c] = _lookup(np.float64(hue[i]), axis, controls[:, c])
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _hsl_parameters(lab, polar, axis, controls):
        out = np.empty_like(lab)
        tau = np.float32(2 * np.pi)
        for i in prange(lab.shape[0]):
            chroma, hue = polar[0, i], polar[1, i]
            wrapped = np.float32(hue + tau) if hue < 0 else hue
            dh = _lookup(np.float64(wrapped), axis, controls[:, 0])
            ds = _lookup(np.float64(wrapped), axis, controls[:, 1])
            dl = _lookup(np.float64(wrapped), axis, controls[:, 2])
            out[i, 0] = np.float64(lab[i, 0]) * 2 ** (dl / 100)
            out[i, 1] = np.float64(chroma) * max(0.0, 1 + ds / 100)
            out[i, 2] = np.float64(hue) + np.deg2rad(dh * 0.45)
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _bw(hsv, gray, mix, axis):
        out = np.empty_like(gray)
        for i in prange(gray.size):
            hue = hsv[i, 0]
            adjustment = np.float32(0)
            if 0 <= hue < 360:
                index = 0
                while index < 7 and hue >= axis[index + 1]:
                    index += 1
                t = np.float32(
                    np.float32(hue - axis[index]) / np.float32(axis[index + 1] - axis[index])
                )
                weight = np.float32(
                    np.float32(0.5)
                    - np.float32(np.float32(0.5) * np.cos(np.float32(t * np.float32(np.pi))))
                )
                adjustment = np.float32(
                    np.float32(np.float32(1 - weight) * mix[index])
                    + np.float32(weight * mix[(index + 1) % 8])
                )
            delta = np.float32(np.float32(adjustment * hsv[i, 1]) * np.float32(0.25))
            out[i] = min(np.float32(1), max(np.float32(0), np.float32(gray[i] + delta)))
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _lens_vignette(linear, radial, coefficient, space, legacy):
        out = np.empty_like(linear)
        for i in prange(linear.shape[0]):
            gain = 2.0 ** (coefficient * radial[i])
            for c in range(3):
                v = np.float64(linear[i, c]) * gain
                if legacy:
                    v = _transfer_value(max(v, 0.0), 0, True)
                    out[i, c] = min(1.0, max(0.0, v))
                else:
                    out[i, c] = _transfer_value(np.float64(np.float32(v)), space, True)
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _hsl(hsv, axis, controls):
        out = np.empty_like(hsv)
        for i in prange(hsv.shape[0]):
            hue = float(hsv[i, 0])
            dh = _lookup(hue, axis, controls[:, 0]) * 0.45
            ds = _lookup(hue, axis, controls[:, 1]) / 100.0
            dv = _lookup(hue, axis, controls[:, 2]) / 100.0
            out[i, 0] = (hue + dh) % 360.0
            out[i, 1] = min(1.0, max(0.0, float(hsv[i, 1]) * (1.0 + ds)))
            out[i, 2] = min(1.0, max(0.0, float(hsv[i, 2]) * (2.0**dv)))
        return out

    @njit(cache=JIT_CACHE, nogil=True, parallel=True, fastmath=False)
    def _range_srgb(x, weights, controls, contrast):
        """One analytic pass; keep float32 stops and transfer-function rounding."""
        out = np.empty_like(x)
        factor = np.float32(1 + contrast / 125.0)
        sh, hi, bl, wh = controls
        for i in prange(x.shape[0]):
            stops = np.float32((sh * weights[0, i] + hi * weights[1, i]) / np.float32(65))
            stops = np.float32(
                stops + np.float32((bl * weights[2, i] + wh * weights[3, i]) / np.float32(85))
            )
            gain = np.float32(np.float32(2) ** stops)
            for c in range(3):
                v = max(np.float32(x[i, c] * gain), np.float32(0))
                if v <= np.float32(0.0031308):
                    v = np.float32(v * np.float32(12.92))
                else:
                    v = np.float32(
                        np.float32(1.055) * np.float32(v ** np.float32(1 / 2.4)) - np.float32(0.055)
                    )
                if contrast:
                    v = np.float32(np.float32(v - np.float32(0.5)) * factor + np.float32(0.5))
                out[i, c] = min(np.float32(1), max(np.float32(0), v))
        return out


def _threads():
    set_num_threads(min(8, performance.THREADS, get_num_threads()))


def _execute(kernel, *arguments):
    # Workqueue cannot accept simultaneous launches from different image workers.
    # Keep parallelism inside each kernel while serializing backend entry points.
    with _kernel_lock:
        _threads()
        return kernel(*arguments)


def interp(x, table, axis=None):
    if not enabled or x.dtype != np.float32:
        return None
    flat = np.ascontiguousarray(x).ravel()
    table = np.asarray(table, np.float64)
    result = (
        _execute(_uniform, flat, table)
        if axis is None
        else _execute(_sparse, flat, np.asarray(axis, np.float64), table)
    )
    return result.reshape(x.shape)


def hsl(hsv, axis, controls):
    if not enabled:
        return None
    return _execute(_hsl, hsv.reshape(-1, 3), np.asarray(axis, np.float64), controls).reshape(
        hsv.shape
    )


def hue_shifts(hue, axis, controls):
    if not enabled or hue.dtype != np.float32:
        return None
    values = np.asarray(controls, np.float32)
    return _execute(
        _hue_shifts, np.ascontiguousarray(hue).ravel(), np.asarray(axis, np.float64), values
    ).reshape((*hue.shape, values.shape[1]))


def hsl_parameters(lab, polar, axis, controls):
    if not enabled or lab.dtype != np.float32:
        return None
    return _execute(
        _hsl_parameters,
        np.ascontiguousarray(lab).reshape(-1, 3),
        np.ascontiguousarray(polar).reshape(2, -1),
        np.asarray(axis, np.float64),
        np.asarray(controls, np.float32),
    ).reshape(lab.shape)


def bw(hsv, gray, mix):
    if not enabled or hsv.dtype != np.float32:
        return None
    return _execute(
        _bw,
        np.ascontiguousarray(hsv).reshape(-1, 3),
        np.ascontiguousarray(gray).ravel(),
        np.clip(np.asarray(mix, np.float32), -100, 100) / 100,
        np.array([0, 30, 60, 120, 180, 240, 270, 300, 360], np.float32),
    ).reshape(gray.shape)


def lens_vignette(linear, radial, amount, space):
    if not enabled or linear.dtype != np.float32:
        return None
    from .imaging.colorspaces import canonical_name

    order = ('sRGB', 'Display P3', 'Adobe RGB', 'ProPhoto RGB', 'Rec.2020')
    return _execute(
        _lens_vignette,
        np.ascontiguousarray(linear).reshape(-1, 3),
        np.ascontiguousarray(radial, dtype=np.float64).ravel(),
        float(amount / 100 * 1.5),
        order.index(canonical_name(space)),
        space == 'sRGB',
    ).reshape(linear.shape)


def range_srgb(linear, weights, a):
    if not enabled:
        return None
    controls = np.asarray([a[k] for k in ('shadows', 'highlights', 'blacks', 'whites')], np.float32)
    return _execute(
        _range_srgb, linear.reshape(-1, 3), weights.reshape(4, -1), controls, float(a['contrast'])
    ).reshape(linear.shape)


def transfer(rgb, space, encode=False):
    if not enabled or rgb.dtype != np.float32:
        return None
    return _execute(_transfer, np.ascontiguousarray(rgb).ravel(), int(space), bool(encode)).reshape(
        rgb.shape
    )


def matrix(rgb, coefficients):
    if not enabled or rgb.dtype != np.float32:
        return None
    return _execute(
        _matrix,
        np.ascontiguousarray(rgb).reshape(-1, 3),
        np.ascontiguousarray(coefficients, dtype=np.float64),
    ).reshape(rgb.shape)


def point_adjust(lab, weights, hue, saturation, luminance):
    if not enabled or lab.dtype != np.float32:
        return None
    return _execute(
        _point_adjust,
        np.ascontiguousarray(lab).reshape(-1, 3),
        np.ascontiguousarray(weights).ravel(),
        float(hue),
        float(saturation),
        float(luminance),
    ).reshape(lab.shape)


def saturation(rgb, sat, vib, wide=False):
    if not enabled or rgb.dtype != np.float32:
        return None
    luma = np.array([0.2627, 0.6780, 0.0593] if wide else [0.2126, 0.7152, 0.0722], np.float32)
    return _execute(
        _saturation,
        np.ascontiguousarray(rgb).reshape(-1, 3),
        float(sat),
        float(vib),
        luma,
        bool(wide),
    ).reshape(rgb.shape)


def convert_rgb(rgb, matrix, src, dst):
    if not enabled or rgb.dtype != np.float32:
        return None
    return _execute(
        _convert_rgb,
        np.ascontiguousarray(rgb).reshape(-1, 3),
        np.asarray(matrix, np.float64),
        int(src),
        int(dst),
    ).reshape(rgb.shape)


def wide_tonal(rgb, a):
    if not enabled or rgb.dtype != np.float32:
        return None
    temp, tint = a['temperature'] / 100, a['tint'] / 100
    gains = (
        np.array(
            [
                2 ** (0.4 * temp + 0.15 * tint),
                2 ** (-0.15 * tint),
                2 ** (-0.4 * temp + 0.15 * tint),
            ],
            np.float32,
        )
        * 2 ** a['exposure']
    )
    controls = np.array([a[k] for k in ('shadows', 'highlights', 'blacks', 'whites')], np.float32)
    return _execute(
        _wide_tonal, np.ascontiguousarray(rgb).reshape(-1, 3), gains, controls, float(a['contrast'])
    ).reshape(rgb.shape)


def grade(rgb, grading, wide=False):
    if not enabled or rgb.dtype != np.float32:
        return None
    luma = np.array([0.2627, 0.6780, 0.0593] if wide else [0.2126, 0.7152, 0.0722], np.float32)
    colors = np.zeros((3, 3), np.float32)
    strengths = np.zeros(3, np.float32)
    for i, zone in enumerate(('shadows', 'midtones', 'highlights')):
        hue, strength = grading.get(zone, [0, 0])
        strengths[i] = strength
        color = np.array(colorsys.hsv_to_rgb(hue % 360 / 360, 1, 1), np.float32)
        color -= color @ luma if wide else np.dot(color, [0.2126, 0.7152, 0.0722])
        colors[i] = color
    return _execute(
        _grade,
        np.ascontiguousarray(rgb).reshape(-1, 3),
        colors,
        strengths,
        np.float32(grading.get('balance', 0) / 300),
        luma,
        bool(wide),
    ).reshape(rgb.shape)


def rgb8(rgb):
    if not enabled or rgb.dtype != np.float32:
        return None
    return _execute(_rgb8, np.ascontiguousarray(rgb).ravel()).reshape(rgb.shape)


def tone_weights(rgb):
    if not enabled or rgb.dtype != np.float32:
        return None
    return _execute(_tone_weights, np.ascontiguousarray(rgb).reshape(-1, 3)).reshape(
        (4, *rgb.shape[:-1])
    )


def remap_map(shape, g, o, channel=1):
    if not enabled:
        return None
    maps, flags = _execute(
        _remap_map,
        int(shape[0]),
        int(shape[1]),
        float(g['vertical']),
        float(g['horizontal']),
        float(2 ** (g['aspect'] / 100)),
        float(g['offset_x']),
        float(g['offset_y']),
        float(g['scale'] / 100),
        bool(g['flip_x']),
        bool(g['flip_y']),
        float(o['distortion']),
        float((o['ca_red'], 0, o['ca_blue'])[channel]),
    )
    if np.any(flags & 1):
        raise ValueError('perspective is invalid: projective horizon intersects output')
    if np.any(flags & 2):
        raise ValueError(
            'combined lens correction folds pixels; reduce distortion or increase scale'
        )
    return maps[0], maps[1]


def curve_exact(rgb, points, mode):
    if not enabled or rgb.dtype != np.float32:
        return None
    from .curves import spline_parameters

    axis, values, slopes = spline_parameters(points)
    return _execute(
        _curve_exact,
        np.ascontiguousarray(rgb).ravel(),
        axis,
        values,
        slopes,
        mode == 'smooth' and len(axis) > 2,
    ).reshape(rgb.shape)


def area_rows(rgb, offset, src_height, dst_height, begin, end):
    if not enabled:
        return None
    return _execute(
        _area_rows,
        np.ascontiguousarray(rgb),
        int(offset),
        int(src_height),
        int(dst_height),
        int(begin),
        int(end),
    )


_warmed = set()


def _warm_key(fn):
    # Uniform and sparse interpolation have different native signatures.
    return fn.func.__name__ + ':' + str(len(fn.args))


def _warm_step(fn):
    global enabled
    name = _warm_key(fn)
    if not enabled or name in _warmed:
        return
    try:
        started = time.perf_counter()
        fn()
        _warmed.add(name)
        log.debug('Native kernel %s warmed in %.3f s', name, time.perf_counter() - started)
    except Exception:
        enabled = False
        log.exception('Native preview kernels unavailable; using NumPy/NumExpr')


def warm_steps():
    """Short idle jobs yield between kernels to foreground reads and previews."""
    if not enabled:
        return []
    x = np.linspace(0, 1, 16, dtype=np.float32)
    rgb = np.zeros((2, 2, 3), np.float32)
    jobs = [
        partial(rgb8, rgb),
        partial(transfer, rgb, 4),
        partial(interp, x, np.linspace(0, 1, 4097)),
        partial(interp, x, np.array([0.0, 1.0]), np.array([0.0, 1.0])),
        partial(hsl, rgb, [0.0, 360.0], np.zeros((2, 3), np.float32)),
        partial(
            range_srgb,
            rgb,
            np.ones((4, 2, 2), np.float32),
            dict(shadows=0, highlights=0, blacks=0, whites=0, contrast=0),
        ),
        partial(matrix, rgb, np.eye(3)),
        partial(point_adjust, rgb, np.ones((2, 2), np.float32), 0, 0, 0),
        partial(saturation, rgb, 10, 10),
        partial(curve_exact, x, [[0, 0], [0.5, 0.6], [1, 1]], 'smooth'),
        partial(area_rows, np.zeros((4, 2, 3), np.float32), 0, 4, 2, 0, 2),
        partial(convert_rgb, rgb, np.eye(3), 0, 4),
        partial(
            wide_tonal,
            rgb,
            dict(
                exposure=0,
                temperature=0,
                tint=0,
                contrast=0,
                shadows=0,
                highlights=0,
                blacks=0,
                whites=0,
            ),
        ),
        partial(grade, rgb, {'midtones': [210, 30]}),
        partial(
            hue_shifts,
            np.zeros((2, 2), np.float32),
            np.array([0.0, 3.0, 6.0]),
            np.zeros((3, 3), np.float32),
        ),
        partial(
            hsl_parameters,
            rgb,
            np.zeros((2, 2, 2), np.float32),
            np.array([0.0, 3.0, 6.0]),
            np.zeros((3, 3), np.float32),
        ),
        partial(bw, rgb, np.zeros((2, 2), np.float32), np.zeros(8, np.float32)),
        partial(lens_vignette, rgb, np.zeros((2, 2), np.float64), 50, 'sRGB'),
        partial(tone_weights, rgb),
        partial(
            remap_map,
            (2, 2, 3),
            dict(
                vertical=0,
                horizontal=0,
                aspect=0,
                offset_x=0,
                offset_y=0,
                scale=100,
                flip_x=False,
                flip_y=False,
            ),
            dict(distortion=0, ca_red=0, ca_blue=0),
        ),
    ]
    return [partial(_warm_step, job) for job in jobs if _warm_key(job) not in _warmed]


def warm():
    for step in warm_steps():
        step()
