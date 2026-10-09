"""Exact curve lookup and HSL transforms without full-image temporaries.

The float64 interpolation used by np.interp is retained, then rounded to
float32 once. Kernels release the GIL, use bounded CPU threads and disable
fastmath. The optional backend warms in the image worker before editing.
"""

import logging
import os
import sys
import numpy as np
from . import performance

log = logging.getLogger(__name__)
JIT_CACHE = not getattr(sys, 'frozen', False)
os.environ.setdefault('NUMBA_NUM_THREADS', str(min(8, performance.THREADS)))
try:
    from numba import njit, prange, set_num_threads, get_num_threads

    enabled = True
except ImportError:
    enabled = False


if enabled:

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


def interp(x, table, axis=None):
    if not enabled or x.dtype != np.float32:
        return None
    _threads()
    flat = np.ascontiguousarray(x).ravel()
    table = np.asarray(table, np.float64)
    result = (
        _uniform(flat, table)
        if axis is None
        else _sparse(flat, np.asarray(axis, np.float64), table)
    )
    return result.reshape(x.shape)


def hsl(hsv, axis, controls):
    if not enabled:
        return None
    _threads()
    return _hsl(hsv.reshape(-1, 3), np.asarray(axis, np.float64), controls).reshape(hsv.shape)


def range_srgb(linear, weights, a):
    if not enabled:
        return None
    _threads()
    controls = np.asarray([a[k] for k in ('shadows', 'highlights', 'blacks', 'whites')], np.float32)
    return _range_srgb(
        linear.reshape(-1, 3), weights.reshape(4, -1), controls, float(a['contrast'])
    ).reshape(linear.shape)


def warm():
    global enabled
    if not enabled:
        return
    try:
        x = np.linspace(0, 1, 16, dtype=np.float32)
        table = np.linspace(0, 1, 4097)
        interp(x, table)
        interp(x, np.array([0.0, 1.0]), np.array([0.0, 1.0]))
        hsl(np.zeros((2, 2, 3), np.float32), [0.0, 360.0], np.zeros((2, 3), np.float32))
        range_srgb(
            np.zeros((2, 2, 3), np.float32),
            np.ones((4, 2, 2), np.float32),
            dict(shadows=0, highlights=0, blacks=0, whites=0, contrast=0),
        )
    except Exception:
        enabled = False
        log.exception('Native preview kernels unavailable; using NumPy/NumExpr')
