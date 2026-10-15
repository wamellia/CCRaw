"""Process 2: linear Rec.2020 working RGB, encoded SDR controls, float output.

The input contract stays linear sRGB (including out-of-gamut signed components).
Working gamut is retained until output encoding; legacy process 1 remains unchanged.
The CPU path is deliberate: existing GPU graphs implement process 1 only.
"""

import copy
from pathlib import Path

import numpy as np

from . import colorspaces as cs
from .backend import Backend


class WideBackend(Backend):
    working_space = 'Rec.2020'

    def __init__(self):
        super().__init__('cpu')
        self.name = 'CPU · Rec.2020'

    def decode(self, rgb):
        return cs.decode(rgb, 'Rec.2020')

    def reference(self, rgb):
        return cs.encode(cs.convert_linear(rgb, 'Rec.2020', 'sRGB'), 'sRGB')

    def details(self, rgb, a, detail_scale=1.0, reference_shape=None):
        from . import wide_color

        spatial = wide_color.spatial_details(rgb, a, detail_scale, reference_shape)
        return wide_color.color_base(spatial, {'adjustments': a})

    def tonal(self, image, a, cache=None, basis_key=None):
        from .. import native_kernels

        fast = native_kernels.wide_tonal(image, a)
        if fast is not None:
            return fast
        x = np.asarray(image, np.float32).copy()
        temp, tint = a['temperature'] / 100, a['tint'] / 100
        x *= (
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
        if any(a[k] for k in ('shadows', 'highlights', 'blacks', 'whites')):
            lum = x @ np.array([0.2627, 0.6780, 0.0593], np.float32)
            p = np.clip(lum, 0, 1) ** 0.45
            stops = (a['shadows'] * (1 - p) ** 2 + a['highlights'] * p**3) / 65
            stops += (a['blacks'] * (1 - p) ** 6 + a['whites'] * p**6) / 85
            x *= (2**stops)[..., None]
        x = cs.encode(x, 'Rec.2020')
        if a['contrast']:
            x = (x - 0.5) * (1 + a['contrast'] / 125) + 0.5
        return x.astype(np.float32)


def _prepare(source, edits, detail_scale, cache):
    from .. import engine
    from .. import large_image

    large_image.validate_size(source.shape)
    # Matrix conversion currently materializes a full working frame. Reject the
    # request before NumPy's float64 intermediates can exhaust available memory.
    available = large_image.available_memory()
    if (
        source.nbytes > large_image.MAP_BYTES
        and available is not None
        and source.nbytes * 16 > available * 0.65
    ):
        raise ValueError('当前可用内存不足以完成宽色域显影；请提高可用内存或使用兼容显影。')
    clean = copy.deepcopy(edits)
    clean['process'] = 1
    clean['_rgb_space'] = 'Rec.2020'
    profile = clean.get('professional', {}).get('profile', {})
    dcp = Path(profile.get('path', '')).suffix.lower() == '.dcp' and profile.get('amount', 100) > 0
    stamp = None
    if dcp:
        path = Path(profile['path'])
        stat = path.stat()
        stamp = (str(path.resolve()), stat.st_mtime_ns, stat.st_size, profile)

    def input_rgb():
        def neutral_input():
            return cs.convert_linear(source, 'sRGB', 'Rec.2020')

        working = (
            neutral_input()
            if cache is None or not dcp
            else cache.get('working-neutral', 'linear-Rec.2020-neutral', neutral_input)
        )
        if not dcp:
            return working
        from .profiles import load_dcp
        from .io import decode_camera_native

        selected = load_dcp(profile['path'])
        mode, raw_path = profile.get('mode', 'auto'), getattr(source, 'raw_path', '')
        if mode == 'camera' and not raw_path:
            raise ValueError('DCP 相机矩阵需要可解码的原始 RAW；普通照片请选择外观模式。')
        cct = getattr(source, 'camera_kelvin', None) or 6504

        def calibrated():
            if raw_path and mode != 'look':
                native = decode_camera_native(raw_path, preview_shape=source.shape[:2])
                return selected.apply_camera(
                    native, cct=cct, camera_neutral=getattr(native, 'camera_neutral', (1, 1, 1))
                )
            return selected.apply_look(working, cct=cct, srcspace='Rec.2020')

        key = engine._key(stamp[:3], mode, raw_path, cct, source.shape)
        result = calibrated() if cache is None else cache.get('dcp-calibrated', key, calibrated)
        return (working + (result - working) * np.float32(profile.get('amount', 100) / 100)).astype(
            np.float32
        )

    if cache is None:
        working = input_rgb()
        inner = None
    else:
        cache.bind(source, detail_scale)
        working = cache.get('working-source', engine._key('linear-Rec.2020', stamp), input_rgb)
        if not hasattr(cache, '_wide_cache'):
            cache._wide_cache = engine.RenderCache(0, cache.alpha_bytes // 2)
        inner = cache._wide_cache
        inner.max_bytes = max(0, cache.max_bytes - cache._stored())
        if inner._stored() > inner.max_bytes:
            inner.clear()
    if dcp:
        clean['professional']['profile']['path'] = ''
    return working, clean, inner


def process(
    source, edits, output_space='sRGB', apply_crop=True, detail_scale=1, cache=None, check=None
):
    from .. import engine

    check = check or (lambda: None)
    check()
    working, clean, inner = _prepare(source, edits, detail_scale, cache)
    check()
    result = engine.process(
        working,
        clean,
        WideBackend(),
        apply_crop=apply_crop,
        detail_scale=detail_scale,
        cache=inner,
        _stream=False,
        _working_space='Rec.2020',
        check=check,
    )
    check()
    return np.ascontiguousarray(cs.convert(result, 'Rec.2020', output_space))


def process_region(source, edits, rect=None, detail_scale=1, cache=None, check=None):
    from .. import engine

    working, clean, inner = _prepare(source, edits, detail_scale, cache)
    result = engine.process_region(
        working,
        clean,
        WideBackend(),
        rect=rect,
        detail_scale=detail_scale,
        cache=inner,
        check=check,
        _working_space='Rec.2020',
    )
    return np.ascontiguousarray(cs.convert(result, 'Rec.2020', 'sRGB'))
