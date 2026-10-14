"""Measured OpenCL neighbourhood work with render-scoped policy and CPU fallback.

UMat alone does not guarantee acceleration. Explicit Gaussian kernel dimensions
are required by OpenCV's OpenCL gate; standalone transfers are limited to large
single-channel filters. Detail chains keep intermediate fields on the device.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import wraps
import logging
import threading

import cv2
import numpy as np

from . import compute

log = logging.getLogger(__name__)
_local = threading.local()
_init_lock = threading.Lock()
_execution_lock = threading.Lock()
_status = None
_memory = (0, 0)
MIN_CHAIN_PIXELS = 1_000_000
MIN_GAUSSIAN_PIXELS = 3_000_000
MAX_PIXELS = 20_000_000


@dataclass
class _Scope:
    backend: object
    active: bool
    operations: set = field(default_factory=set)
    device: str = ''
    warning: str = ''
    failed: bool = False


def initialize():
    """Probe once from backend setup or a worker; no kernel compilation."""
    global _status, _memory
    if _status is None:
        with _init_lock:
            if _status is None:
                result = dict(available=False, device='', warning='')
                try:
                    if cv2.ocl.haveOpenCL():
                        cv2.ocl.setUseOpenCL(True)
                        device = cv2.ocl.Device_getDefault()
                        if cv2.ocl.useOpenCL() and device.type() & cv2.ocl.DEVICE_TYPE_GPU:
                            result.update(available=True, device=device.name())
                            _memory = (int(device.globalMemSize()), int(device.maxMemAllocSize()))
                    if not result['available']:
                        result['warning'] = '没有可用的 OpenCL GPU；邻域工具使用 CPU。'
                except Exception as exc:
                    result['warning'] = 'OpenCL 检测失败：' + str(exc)[:120]
                _status = result
    return dict(_status)


def status():
    """Cheap cached UI state; never initialize drivers from status polling."""
    return dict(_status or dict(available=False, device='', warning=''))


def enabled():
    scope = getattr(_local, 'scope', None)
    return bool(scope is not None and scope.active and not scope.failed)


def routed(fn):
    """Inherit the user's outer backend through internal CPU/wide rendering calls."""

    @wraps(fn)
    def render(*args, **kwargs):
        if getattr(_local, 'scope', None) is not None:
            return fn(*args, **kwargs)
        backend = kwargs.get('backend', args[2] if len(args) > 2 else None)
        scope = _Scope(
            backend,
            bool(getattr(backend, 'accelerated', False)) and compute.requested_mode() != 'cpu',
        )
        _local.scope = scope
        completed = False
        try:
            result = fn(*args, **kwargs)
            completed = True
            return result
        finally:
            del _local.scope
            if backend is not None:
                try:
                    backend._spatial_ops = tuple(sorted(scope.operations))
                    backend._spatial_device = scope.device
                    backend._spatial_warning = scope.warning
                except (AttributeError, TypeError):
                    pass  # Reference/testing backends may forbid telemetry fields.
            if completed:
                if scope.operations:
                    compute.state.report(
                        'OpenCLExecutionProvider',
                        scope.device,
                        'OpenCL 邻域细节 · ' + ' / '.join(sorted(scope.operations)),
                        scope.warning,
                    )
                elif (
                    not getattr(backend, 'gpu_pointwise', False)
                    and getattr(backend, 'xp', np) is np
                ):
                    compute.state.report('CPUExecutionProvider', warning=scope.warning)

    return render


def _ready(image, buffers):
    if not enabled() or image.dtype != np.float32 or image.ndim not in (2, 3):
        return False
    pixels = int(np.prod(image.shape[:2]))
    if not pixels or pixels > MAX_PIXELS or (image.ndim == 3 and image.shape[2] != 3):
        return False
    info = initialize()
    if not info['available']:
        return False
    total, allocation = _memory
    # One frame at a time bounds the peak across preview/export workers.
    if total <= 0 or allocation <= 0 or image.nbytes * buffers > total * 0.35:
        return False
    if image.nbytes > allocation * 0.75:
        return False
    try:
        if not getattr(_local, 'opencl_ready', False):
            cv2.ocl.setUseOpenCL(True)
            device = cv2.ocl.Device_getDefault()
            if not cv2.ocl.useOpenCL() or not device.type() & cv2.ocl.DEVICE_TYPE_GPU:
                return False
            if device.name() != info['device']:
                return False
            _local.opencl_ready = True
        return cv2.ocl.useOpenCL()
    except Exception as exc:
        _failure(exc)
        return False


def _failure(exc):
    scope = getattr(_local, 'scope', None)
    if scope is not None:
        scope.failed = True
        scope.warning = 'OpenCL 邻域工具失败，已回退 CPU：' + str(exc)[:120]
    log.warning('OpenCL spatial operation fell back to CPU: %s', exc)


def _record(operation):
    scope = getattr(_local, 'scope', None)
    if scope is not None:
        scope.operations.add(operation)
        scope.device = status()['device']


def _blur(image, sigma, border=cv2.BORDER_REFLECT_101):
    size = round(float(sigma) * 8 + 1) | 1
    return cv2.GaussianBlur(image, (size, size), sigma, borderType=border)


def gaussian(image, sigma, borderType=cv2.BORDER_REFLECT_101):
    """Transfer-inclusive win for large C1 radii, otherwise exact CPU reference."""
    if (
        image.ndim == 2
        and image.size >= MIN_GAUSSIAN_PIXELS
        and sigma >= 12
        and borderType in (cv2.BORDER_REFLECT_101, cv2.BORDER_REPLICATE, cv2.BORDER_CONSTANT)
        and (round(float(sigma) * 8 + 1) | 1) <= min(511, *image.shape)
        and _ready(image, 6)
    ):
        try:
            with _execution_lock:
                result = _blur(cv2.UMat(np.ascontiguousarray(image)), sigma, borderType).get()
            _record('Gaussian')
            return result
        except Exception as exc:
            _failure(exc)
    return cv2.GaussianBlur(image, (0, 0), sigma, borderType=borderType)


def chain(
    rgb,
    a,
    detail_scale=1.0,
    reference_shape=None,
    *,
    wide=False,
    area=None,
    cache=None,
    basis_key=None,
):
    """Develop >=2 detail tools in one transfer; prime matching CPU band caches.

    Cached bands stay on the established CPU route for inexpensive slider changes.
    Noise/dehaze remain their existing implementations and are not fused here.
    """
    clarity, texture, sharpness = (a.get(k, 0) for k in ('clarity', 'texture', 'sharpness'))
    sharpen = bool(sharpness) if wide else sharpness > 0
    if (
        sum((bool(clarity), bool(texture), sharpen)) < 2
        or any(a.get(k, 0) for k in ('denoise', 'color_noise', 'dehaze'))
        or rgb.shape[0] * rgb.shape[1] < MIN_CHAIN_PIXELS
        or not _ready(rgb, 12)
    ):
        return None
    from .engine import _key

    short = min((reference_shape or rgb.shape)[:2])
    radii = []
    if clarity:
        radii.append(max(1.0, short / 90))
    if texture:
        radii.append(max(0.65, short / 800) * 3.5)
    if sharpen:
        radii.append(max(0.45, detail_scale))
    # Unsupported oversized kernels can silently fall back inside OpenCV.
    if any((round(radius * 8 + 1) | 1) > min(rgb.shape[:2]) for radius in radii):
        return None
    if clarity and (round(max(1.0, short / 90) * 8 + 1) | 1) > 511:
        return None
    if texture and (round(max(0.65, short / 800) * 3.5 * 8 + 1) | 1) > 511:
        return None
    if sharpen and (round(max(0.45, detail_scale) * 8 + 1) | 1) > 129:
        return None
    key = _key(basis_key, a.get('denoise', 0), a.get('color_noise', 0))
    key = (
        _key(key, a.get('dehaze', 0), short, area)
        if wide
        else _key(key, a.get('dehaze', 0), area, short)
    )
    stages = []
    if clarity:
        stages.append(('clarity-band', key))
    key = _key(key, clarity)
    if texture:
        stages.append(('texture-band', key))
    key = _key(key, texture, detail_scale)
    if sharpen:
        stages.append(('sharpen-band', key))
    contains = getattr(cache, 'contains', None)
    if cache is not None and hasattr(cache, 'max_bytes') and contains is None:
        return None
    if contains is not None and any(contains(stage, key) for stage, key in stages):
        return None
    retain_bands = contains is not None
    try:
        with _execution_lock:
            x = cv2.UMat(np.ascontiguousarray(rgb))
            bands = []

            def luma(image):
                if not wide:
                    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
                red, green, blue = cv2.split(image)
                return cv2.add(
                    cv2.addWeighted(red, 0.2627, green, 0.6780, 0), cv2.multiply(blue, 0.0593)
                )

            def clip(image):
                return cv2.min(cv2.max(image, (0, 0, 0, 0)), (1, 1, 1, 1))

            def apply_band(image, band, amount):
                result = cv2.addWeighted(image, 1, cv2.merge((band, band, band)), amount / 65, 0)
                return result if wide else clip(result)

            if clarity:
                lum = luma(x)
                band = cv2.subtract(lum, _blur(lum, max(1.0, short / 90)))
                if retain_bands:
                    bands.append(band.get())
                x = apply_band(x, band, clarity)
            if texture:
                lum = luma(x)
                radius = max(0.65, short / 800)
                band = cv2.subtract(_blur(lum, radius), _blur(lum, radius * 3.5))
                if not wide:
                    band = cv2.min(cv2.max(band, -0.08), 0.08)
                if retain_bands:
                    bands.append(band.get())
                x = apply_band(x, band, texture)
            if sharpen:
                delta = cv2.subtract(x, _blur(x, max(0.45, detail_scale)))
                factor = cv2.min(
                    cv2.multiply(cv2.absdiff(delta, (0, 0, 0, 0)), (125, 125, 125, 125)),
                    (1, 1, 1, 1),
                )
                band = cv2.multiply(delta, factor)
                if retain_bands:
                    bands.append(band.get())
                x = cv2.addWeighted(x, 1, band, sharpness / 40, 0)
                if not wide:
                    x = clip(x)
            result = x.get()
        if retain_bands:
            for (stage, key), band in zip(stages, bands):
                cache.get(stage, key, lambda band=band: band)
        _record('Rec.2020 细节' if wide else '细节')
        return result
    except Exception as exc:
        _failure(exc)
        return None
