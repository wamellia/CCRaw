"""Float32 linear-light RAW pipeline, masks and optional GPU (DirectML / CUDA) processing."""

from __future__ import annotations
from . import resources
import hashlib
import json
import logging
import weakref
from typing import NamedTuple
import cv2
import numpy as np
from PIL import Image
from . import white_balance, retouch, develop
from . import large_image, compute

log = logging.getLogger(__name__)

RAW_EXTENSIONS = {
    '.arw',
    '.sr2',
    '.srf',
    '.dng',
    '.nef',
    '.nrw',
    '.crw',
    '.cr2',
    '.cr3',
    '.raf',
    '.rw2',
    '.raw',
    '.orf',
}
RAW_FILTER = 'Sony (*.arw *.sr2 *.srf);;Canon (*.crw *.cr2 *.cr3);;Nikon (*.nef *.nrw);;Fujifilm (*.raf);;Panasonic (*.rw2 *.raw);;DNG (*.dng)'
PHOTO_FILTER = (
    '图片与工程 ('
    + ' '.join('*' + x for x in sorted(RAW_EXTENSIONS))
    + ' *.jpg *.jpeg *.png *.tif *.tiff *.ccraw *.ccrawalbum *.lumen *.lumenalbum);;'
    + RAW_FILTER
    + ';;所有文件 (*)'
)
MAX_EXPORT_PIXELS = large_image.MAX_PIXELS
Image.MAX_IMAGE_PIXELS = MAX_EXPORT_PIXELS


from .imaging.color import to_linear as to_linear


from .imaging.color import to_srgb as to_srgb


def resize_limit(image, limit):
    h, w = image.shape[:2]
    if limit and max(h, w) > limit:
        ratio = limit / max(h, w)
        return cv2.resize(
            image,
            (max(1, round(w * ratio)), max(1, round(h * ratio))),
            interpolation=cv2.INTER_AREA,
        )
    return image


class Area(NamedTuple):
    """Block [y0:y1, x0:x1] of a frame that is width × height pixels."""

    width: int
    height: int
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def shape(self):
        return self.y1 - self.y0, self.x1 - self.x0

    def grow(self, margin):
        return Area(
            self.width,
            self.height,
            max(0, self.x0 - margin),
            max(0, self.y0 - margin),
            min(self.width, self.x1 + margin),
            min(self.height, self.y1 + margin),
        )

    def inner(self, other):
        """Slices of ``other`` (contained in this area) relative to this area."""
        return (
            slice(other.y0 - self.y0, other.y1 - self.y0),
            slice(other.x0 - self.x0, other.x1 - self.x0),
        )


def _linear_taps(dst, src, start, stop):
    # cv2.resize INTER_LINEAR: centre-aligned double coordinates, edge samples clamped.
    position = (np.arange(start, stop, dtype=np.float64) + 0.5) * (1.0 / (dst / src)) - 0.5
    index = np.floor(position).astype(np.int64)
    fraction = position - index
    fraction[index < 0] = 0
    index[index < 0] = 0
    high = index >= src - 1
    fraction[high] = 0
    index[high] = src - 1
    fraction = fraction.astype(np.float32)
    return index, np.minimum(index + 1, src - 1), np.float32(1) - fraction, fraction


def resize_region(image, size, area):
    """``cv2.resize(image, size, INTER_LINEAR)[area]`` for a 2-D image without the full output."""
    width, height = size
    h, w = image.shape[:2]
    xs, xs1, a0, a1 = _linear_taps(width, w, area.x0, area.x1)
    ys, ys1, b0, b1 = _linear_taps(height, h, area.y0, area.y1)
    rows = np.unique(np.concatenate([ys, ys1]))
    source = np.asarray(image, np.float32)[rows]
    horizontal = source[:, xs] * a0 + source[:, xs1] * a1
    return (
        horizontal[np.searchsorted(rows, ys)] * b0[:, None]
        + horizontal[np.searchsorted(rows, ys1)] * b1[:, None]
    )


from .imaging.io import load_image as load_image


from .imaging.io import load_embedded as load_embedded


from .imaging.io import load_pillow as load_pillow


SPATIAL_KEYS = ('denoise', 'color_noise', 'dehaze', 'clarity', 'texture', 'sharpness')
TONAL_KEYS = (
    'exposure',
    'temperature',
    'tint',
    'contrast',
    'shadows',
    'highlights',
    'blacks',
    'whites',
)
GPU_TILE = 1024


def has_spatial(a):
    return any(a.get(k, 0) for k in SPATIAL_KEYS)


from .imaging.backend import Backend as Backend


from .imaging.spatial import dehaze_basis as dehaze_basis


from .imaging.spatial import dehaze_context as dehaze_context


from .imaging.spatial import dehaze as dehaze


from .imaging.spatial import reduce_noise as reduce_noise


from .imaging.spatial import spatial_details as spatial_details


from .imaging.spatial import spatial_margin as spatial_margin


from .imaging.color import saturation as saturation


from .imaging.spatial import details as details


from .imaging.color import monochrome as monochrome


from .imaging.color import color_stage as color_stage


from .imaging.color import color_base as color_base


from .imaging.color import apply_hsl as apply_hsl


from .imaging.color import apply_curves as apply_curves


from .imaging.color import uniform_interp as uniform_interp


from .imaging.spatial import mask_alpha as mask_alpha


def _key(*parts):
    return hashlib.blake2b(
        json.dumps(parts, sort_keys=True, default=str).encode('utf-8'), digest_size=16
    ).hexdigest()


class _Uncached:
    def bind(self, source, detail_scale):
        pass

    def get(self, stage, key, fn):
        return fn()

    def alpha(self, key, fn):
        return fn()


class RenderCache:
    """Stage results of the last render of one source image.

    A slider only re-runs the stages downstream of it: e.g. HSL changes reuse the
    repaired / developed base, tonal and spatial detail results.  Entries are
    bound to the source array's identity and never mutated by the pipeline.
    """

    STAGES = (
        'base',
        'gain',
        'weights',
        'tonal',
        'atmosphere',
        'detail',
        'chroma',
        'global',
        'reference',
    )

    def __init__(self, max_bytes=512 * 2**20, alpha_bytes=192 * 2**20):
        self.max_bytes, self.alpha_bytes = max_bytes, alpha_bytes
        self.hits = self.misses = 0
        self.clear()

    def clear(self):
        self._source, self._scale = None, None
        self._stages, self._alphas = {}, {}

    def bind(self, source, detail_scale):
        current = self._source() if self._source is not None else None
        if current is not source or self._scale != detail_scale:
            self.clear()
            self._source, self._scale = weakref.ref(source), detail_scale

    def _stored(self):
        source = self._source() if self._source is not None else None
        seen, total = set(), 0
        for _, value in self._stages.values():
            if value is not source and id(value) not in seen:
                seen.add(id(value))
                total += value.nbytes
        return total

    def get(self, stage, key, fn):
        entry = self._stages.get(stage)
        if entry is not None and entry[0] == key:
            self.hits += 1
            return entry[1]
        self.misses += 1
        value = fn()
        self._stages.pop(stage, None)
        # Large full-resolution results may be disk-mapped; they count toward the budget too.
        if isinstance(value, np.ndarray):
            source = self._source() if self._source is not None else None
            free = value is source or any(value is v for _, v in self._stages.values())
            if free or self._stored() + value.nbytes <= self.max_bytes:
                self._stages[stage] = (key, value)
        return value

    def alpha(self, key, fn):
        if key in self._alphas:
            self.hits += 1
            value = self._alphas.pop(key)
        else:
            self.misses += 1
            value = fn()
        self._alphas[key] = value
        while (
            len(self._alphas) > 1
            and sum(v.nbytes for v in self._alphas.values()) > self.alpha_bytes
        ):
            self._alphas.pop(next(iter(self._alphas)))
        return value


_UNCACHED = _Uncached()


def _mask_bounds(alpha):
    rows = np.flatnonzero(alpha.max(axis=1) > 0)
    if not len(rows):
        return None
    cols = np.flatnonzero(alpha[rows[0] : rows[-1] + 1].max(axis=0) > 0)
    return rows[0], rows[-1] + 1, cols[0], cols[-1] + 1


from .imaging.spatial import apply_local as apply_local


def process(
    source, edits, backend=None, apply_crop=True, detail_scale=1.0, _stream=True, cache=None
):
    backend = backend or Backend('cpu')
    large_image.validate_size(source.shape)
    if _stream and source.nbytes > large_image.MAP_BYTES:
        # a cached original-resolution render (e.g. 24 MP) keeps its stages when about
        # four of them fit the cache budget and memory; larger images stream strip by strip.
        cached = (
            cache is not None
            and source.nbytes * 4 <= cache.max_bytes
            and large_image.fits_in_memory(source.shape)
        )
        if not cached:
            return large_image.process(source, edits, backend, apply_crop, detail_scale, cache)
    cache = cache if cache is not None else _UNCACHED
    cache.bind(source, detail_scale)
    a = edits['adjustments']
    # Rotate at output only: mask / crop coordinates always refer to the original image.
    develop_settings = edits.get('develop', {})
    base_key = _key(
        edits.get('wb_gain', [1.0, 1.0, 1.0]),
        edits.get('white_balance', {}),
        edits.get('retouch', []),
        develop_settings,
    )

    def base():
        gain = np.asarray(edits.get('wb_gain', [1.0, 1.0, 1.0]), np.float32) * white_balance.gains(
            edits.get('white_balance', {})
        )
        repaired = develop.apply(retouch.apply(source, edits.get('retouch', [])), develop_settings)
        return repaired if np.all(gain == 1) else repaired * gain

    balanced = cache.get('base', base_key, base)
    tonal_key = _key(
        base_key,
        {k: a.get(k, 0) for k in TONAL_KEYS},
        backend.gpu_pointwise,
        getattr(backend, 'xp', np) is np,
    )
    color_key = _key(
        a.get('saturation', 0),
        a.get('vibrance', 0),
        edits['hsl'],
        edits['curves'],
        edits.get('tone_curve'),
        edits.get('curve_mode', 'linear'),
        edits.get('monochrome', False),
        edits.get('grading', {}),
        backend.gpu_pointwise,
    )
    if backend.gpu_pointwise and not has_spatial(a):
        x = cache.get(
            'global', _key(tonal_key, 'fused', color_key), lambda: backend.fused(balanced, edits)
        )
    else:
        basis_key = _key(base_key, {k: a.get(k, 0) for k in ('exposure', 'temperature', 'tint')})
        tonal = cache.get('tonal', tonal_key, lambda: backend.tonal(balanced, a, cache, basis_key))
        detail_key = _key(tonal_key, {k: a.get(k, 0) for k in SPATIAL_KEYS}, detail_scale)
        if has_spatial(a):
            context = None
            if a.get('dehaze', 0) > 0:
                atmosphere_key = _key(tonal_key, a.get('denoise', 0), a.get('color_noise', 0))
                basis = cache.get(
                    'atmosphere', atmosphere_key, lambda: dehaze_basis(reduce_noise(tonal, a))
                )
                context = dehaze_context(tonal, a['dehaze'], basis)
            tonal = cache.get(
                'detail',
                detail_key,
                lambda: spatial_details(tonal, a, detail_scale, dehaze_context=context),
            )
        if not backend.gpu_pointwise:
            chroma_key = _key(
                detail_key,
                a.get('saturation', 0),
                a.get('vibrance', 0),
                edits['hsl'],
                edits['curves'],
                edits.get('tone_curve'),
                edits.get('curve_mode', 'linear'),
                edits.get('monochrome', False),
            )
            chroma = cache.get('chroma', chroma_key, lambda: color_base(tonal, edits))
            x = cache.get(
                'global',
                _key(chroma_key, edits.get('grading', {})),
                lambda: color_grade(chroma, edits.get('grading', {})),
            )
        else:
            x = cache.get(
                'global', _key(detail_key, color_key), lambda: backend.color(tonal, edits)
            )
    masks = [m for m in edits['masks'] if m['enabled'] and any(m['adjustments'].values())]
    if masks:
        reference_key = _key(develop_settings)
        mask_reference = (
            cache.get(
                'reference',
                reference_key,
                lambda: np.clip(to_srgb(develop.apply(source, develop_settings)), 0, 1),
            )
            if any(m['kind'] == 'luminance' for m in masks)
            else None
        )
        x = x.copy()  # stage results are shared with the cache
        for mask in masks:
            geometry = {
                k: v for k, v in mask.items() if k not in ('adjustments', 'name', 'enabled')
            }
            alpha_key = _key(
                geometry, x.shape, reference_key if mask['kind'] == 'luminance' else ''
            )
            alpha = cache.alpha(alpha_key, lambda: mask_alpha(mask, x.shape, mask_reference))
            apply_local(x, alpha, mask['adjustments'], backend, detail_scale)
    x = finishing(x, edits.get('effects', {}), edits.get('crop'), cache=cache)
    if apply_crop:
        x = crop_rotate(x, edits)
    return np.ascontiguousarray(np.clip(x, 0, 1), dtype=np.float32)


def _gain(edits):
    return np.asarray(edits.get('wb_gain', [1.0, 1.0, 1.0]), np.float32) * white_balance.gains(
        edits.get('white_balance', {})
    )


def dehaze_context_for(source, edits, backend=None, detail_scale=1.0):
    """Dehaze statistics exactly as ``process(source, edits)`` derives them, or None."""
    a = edits['adjustments']
    if a.get('dehaze', 0) <= 0:
        return None
    backend = backend or Backend('cpu')
    gain = _gain(edits)
    balanced = develop.apply(
        retouch.apply(source, edits.get('retouch', [])), edits.get('develop', {})
    )
    if not np.all(gain == 1):
        balanced = balanced * gain
    tonal = np.ascontiguousarray(backend.tonal(balanced, a), dtype=np.float32)
    return dehaze_context(reduce_noise(tonal, a), a['dehaze'])


def process_region(
    source,
    edits,
    backend=None,
    rect=None,
    detail_scale=1.0,
    dehaze_context=None,
    check=None,
    cache=None,
):
    """``process(source, edits, apply_crop=False)[y0:y1, x0:x1]`` for ``rect = (x0, y0, x1, y1)``.

    Neighbourhood tools read a margin around the block; retouching, masks, vignette and grain are
    evaluated in frame coordinates, so the block matches the whole-frame render.  Dehaze uses the
    statistics of ``dehaze_context`` (by default those of the whole ``source``).  ``check`` is called
    between stages and may raise to abandon a stale render.
    """
    backend = backend or Backend('cpu')
    check = check or (lambda: None)
    h, w = source.shape[:2]
    target = Area(w, h, *(rect or (0, 0, w, h)))
    a = edits['adjustments']
    masks = [m for m in edits['masks'] if m['enabled'] and any(m['adjustments'].values())]
    margin = spatial_margin(a, (h, w), detail_scale) + max(
        [spatial_margin(m['adjustments'], (h, w), detail_scale) for m in masks], default=0
    )
    area = target.grow(margin)
    cache = cache if cache is not None else _UNCACHED
    cache.bind(source, detail_scale)
    develop_settings = edits.get('develop', {})
    if a.get('dehaze', 0) > 0 and dehaze_context is None:
        dehaze_context = dehaze_context_for(source, edits, backend, detail_scale)
    base_key = _key(
        area,
        edits.get('wb_gain'),
        edits.get('white_balance'),
        edits.get('retouch'),
        develop_settings,
    )

    def base():
        gain = _gain(edits)
        balanced = develop.apply(
            retouch.apply(source, edits.get('retouch', []), (area.x0, area.y0, area.x1, area.y1)),
            develop_settings,
        )
        return balanced if np.all(gain == 1) else balanced * gain

    balanced = cache.get('base', base_key, base)
    tonal_key = _key(
        base_key,
        {k: a.get(k, 0) for k in TONAL_KEYS},
        backend.gpu_pointwise,
        getattr(backend, 'xp', np) is np,
    )
    context_key = (
        None
        if dehaze_context is None
        else [
            hashlib.blake2b(np.ascontiguousarray(v).view(np.uint8), digest_size=16).hexdigest()
            for v in dehaze_context
        ]
    )
    detail_key = _key(tonal_key, {k: a.get(k, 0) for k in SPATIAL_KEYS}, detail_scale, context_key)
    color_key = _key(
        detail_key,
        a.get('saturation', 0),
        a.get('vibrance', 0),
        edits['hsl'],
        edits['curves'],
        edits.get('tone_curve'),
        edits.get('curve_mode', 'linear'),
        edits.get('monochrome', False),
    )
    check()
    if backend.gpu_pointwise and not has_spatial(a):
        x = cache.get(
            'global', _key(color_key, edits.get('grading')), lambda: backend.fused(balanced, edits)
        )
    else:
        basis_key = _key(base_key, {k: a.get(k, 0) for k in ('exposure', 'temperature', 'tint')})
        x = cache.get('tonal', tonal_key, lambda: backend.tonal(balanced, a, cache, basis_key))
        if has_spatial(a):
            check()
            tonal = x
            x = cache.get(
                'detail',
                detail_key,
                lambda: spatial_details(tonal, a, detail_scale, (h, w), dehaze_context, area),
            )
        check()
        tonal = x
        if not backend.gpu_pointwise:
            chroma = cache.get('chroma', color_key, lambda: color_base(tonal, edits))
            x = cache.get(
                'global',
                _key(color_key, edits.get('grading')),
                lambda: color_grade(chroma, edits.get('grading', {})),
            )
        else:
            x = cache.get(
                'global', _key(color_key, edits.get('grading')), lambda: backend.color(tonal, edits)
            )
    if masks:
        reference = (
            np.clip(
                to_srgb(
                    develop.apply(source[area.y0 : area.y1, area.x0 : area.x1], develop_settings)
                ),
                0,
                1,
            )
            if any(m['kind'] == 'luminance' for m in masks)
            else None
        )
        x = x.copy()
        for mask in masks:
            check()
            geometry = {
                k: v for k, v in mask.items() if k not in ('adjustments', 'name', 'enabled')
            }
            alpha_key = _key(
                geometry, area, develop_settings if mask['kind'] == 'luminance' else None
            )
            alpha = cache.alpha(alpha_key, lambda: mask_alpha(mask, (h, w), reference, area))
            apply_local(x, alpha, mask['adjustments'], backend, detail_scale, (h, w))
    x = finishing(x, edits.get('effects', {}), edits.get('crop'), area, cache)
    return np.ascontiguousarray(np.clip(x[area.inner(target)], 0, 1), dtype=np.float32)


def crop_bounds(edits, width, height):
    """Pixel rectangle (x0, y0, x1, y1) that ``crop_rotate`` keeps of a width × height frame."""
    if not edits.get('crop'):
        return 0, 0, width, height
    a, b, c, d = edits['crop']
    x0, y0 = min(width - 1, int(a * width)), min(height - 1, int(b * height))
    return (
        x0,
        y0,
        max(x0 + 1, min(width, round(c * width))),
        max(y0 + 1, min(height, round(d * height))),
    )


def display_size(edits, size, final=True):
    """Width × height of a frame of ``size`` after ``crop_rotate`` (``final``) or as is."""
    if not final:
        return tuple(size)
    x0, y0, x1, y1 = crop_bounds(edits, *size)
    return (y1 - y0, x1 - x0) if edits.get('rotation', 0) % 2 else (x1 - x0, y1 - y0)


def _straighten_matrix(angle, width, height):
    theta = np.deg2rad(abs(angle))
    scale = max(
        np.cos(theta) + (height / width) * np.sin(theta),
        np.cos(theta) + (width / height) * np.sin(theta),
    )
    return cv2.getRotationMatrix2D(((width - 1) / 2, (height - 1) / 2), angle, scale)


def display_block(render, edits, size, rect, final=True):
    """Block ``rect`` of ``crop_rotate(frame, edits)`` (``final``) or of the frame itself, where
    ``render((x0, y0, x1, y1))`` returns blocks of the width × height frame."""
    if not final:
        return render(tuple(rect))
    width, height = size
    cx0, cy0, cx1, cy1 = crop_bounds(edits, width, height)
    cw, ch = cx1 - cx0, cy1 - cy0
    dx0, dy0, dx1, dy1 = rect
    turns = edits.get('rotation', 0) % 4
    # Pre-rotation rectangle of np.rot90(x, -turns).
    ux0, uy0, ux1, uy1 = [
        (dx0, dy0, dx1, dy1),
        (dy0, ch - dx1, dy1, ch - dx0),
        (cw - dx1, ch - dy1, cw - dx0, ch - dy0),
        (cw - dy1, dx0, cw - dy0, dx1),
    ][turns]
    angle = edits.get('straighten', 0)
    if abs(angle) > 0.001:
        inverse = cv2.invertAffineTransform(_straighten_matrix(angle, cw, ch))
        corners = np.array([[ux0, uy0], [ux1 - 1, uy0], [ux0, uy1 - 1], [ux1 - 1, uy1 - 1]], float)
        points = corners @ inverse[:, :2].T + inverse[:, 2]
        # Bicubic taps reach one pixel before and two after the sample position.
        sx0 = int(np.clip(np.floor(points[:, 0].min()) - 2, 0, cw - 1))
        sy0 = int(np.clip(np.floor(points[:, 1].min()) - 2, 0, ch - 1))
        sx1 = int(np.clip(np.floor(points[:, 0].max()) + 4, sx0 + 1, cw))
        sy1 = int(np.clip(np.floor(points[:, 1].max()) + 4, sy0 + 1, ch))
        block = render((cx0 + sx0, cy0 + sy0, cx0 + sx1, cy0 + sy1))
        shifted = inverse.copy()
        shifted[:, 2] += inverse[:, :2] @ [ux0, uy0] - np.array([sx0, sy0], float)
        x = cv2.warpAffine(
            block,
            shifted,
            (ux1 - ux0, uy1 - uy0),
            flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_REPLICATE,
        )
    else:
        x = render((cx0 + ux0, cy0 + uy0, cx0 + ux1, cy0 + uy1))
    return np.ascontiguousarray(np.rot90(x, -turns))


def render_display(
    source,
    edits,
    backend,
    rect,
    final=False,
    detail_scale=1.0,
    kind='edited',
    dehaze_context=None,
    check=None,
    cache=None,
):
    """Displayed block ``rect`` of the edited photograph or of its developed original."""
    develop_settings = edits.get('develop', {})
    if kind == 'original':

        def render(r):
            return np.clip(
                to_srgb(develop.apply(source[r[1] : r[3], r[0] : r[2]], develop_settings)), 0, 1
            )
    else:

        def render(r):
            return process_region(
                source, edits, backend, r, detail_scale, dehaze_context, check, cache
            )

    return display_block(render, edits, (source.shape[1], source.shape[0]), rect, final)


from .imaging.color import color_grade as color_grade


from .imaging.color import finishing as finishing


def auto_tone(source, gains=None):
    sample = resize_limit(source, 500)
    if gains is not None:
        sample = sample * np.asarray(gains, dtype=np.float32)
    lum = sample[..., 0] * 0.2126 + sample[..., 1] * 0.7152 + sample[..., 2] * 0.0722
    dark, mid, light = np.percentile(lum, [5, 50, 98])
    if light < 1e-5:
        return dict(exposure=0.0, shadows=0.0, highlights=0.0, blacks=0.0, whites=0.0, contrast=0.0)
    exposure = float(np.clip(np.log2(0.18 / max(mid, 0.003)), -2.5, 2.5))
    exposure = min(exposure, float(np.log2(1.6 / max(light, 0.01))))
    high_after, low_after = light * 2**exposure, dark * 2**exposure
    return dict(
        exposure=round(exposure, 2),
        shadows=round(float(np.clip((0.05 - low_after) * 550, 0, 40))),
        highlights=round(float(np.clip((0.7 - high_after) * 65, -65, 0))),
        blacks=-5.0,
        whites=0.0,
        contrast=5.0,
    )


def sample_white_balance(source, position):
    h, w = source.shape[:2]
    x, y = round(position[0] * (w - 1)), round(position[1] * (h - 1))
    radius = max(2, round(min(h, w) / 150))
    patch = source[
        max(0, y - radius) : min(h, y + radius + 1), max(0, x - radius) : min(w, x + radius + 1)
    ]
    sample = np.median(patch, axis=(0, 1))
    if np.min(sample) < 0.002 or np.max(sample) >= 0.985:
        raise ValueError('这个区域太暗或已过曝，请选择有细节的中性灰／白色区域。')
    target = float(sample @ np.array([0.2126, 0.7152, 0.0722]))
    return np.clip(target / sample, 0.125, 8).astype(float).tolist()


def crop_rotate(image, edits):
    x = image
    if edits['crop']:
        x0, y0, x1, y1 = crop_bounds(edits, x.shape[1], x.shape[0])
        x = x[y0:y1, x0:x1]
    angle = edits.get('straighten', 0)
    if abs(angle) > 0.001:
        h, w = x.shape[:2]
        # Enlarge the rotated image to cover all output corners, preserving crop ratio.
        matrix = _straighten_matrix(angle, w, h)
        x = cv2.warpAffine(
            x, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
        )
    return np.ascontiguousarray(np.rot90(x, -edits['rotation']))


def super_resolve(rgb, scale=2, model_path=None, use_cuda=True, progress=None, cancel=None):
    h, w = rgb.shape[:2]
    if scale not in (1, 2, 4):
        raise ValueError('仅支持 1×、2× 或 4×。')
    if h * w * scale * scale > MAX_EXPORT_PIXELS:
        raise ValueError('放大后超过 4 亿像素，请先裁切或降低倍数。')
    if scale == 1:
        return rgb, '原始尺寸'
    if cancel is not None and cancel.is_set():
        raise InterruptedError('已取消增强')
    if model_path == ':quality:':
        from .restoration import super_resolution

        return super_resolution(rgb, scale, use_cuda, progress, cancel)
    if model_path == ':builtin:':
        path = resources.asset_path('models', 'realesr-general-x4v3.onnx')
        if not path.exists():
            raise FileNotFoundError('内置增强模型缺失，请重新解压完整软件包。')
        return onnx_super_resolve(rgb, scale, path, use_cuda, progress, cancel, native_scale=4)
    if model_path:
        return onnx_super_resolve(rgb, scale, model_path, use_cuda, progress, cancel)
    # Classical single-image iterative back projection (not a neural model).
    out = cv2.resize(rgb, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)
    for _ in range(3):
        error = rgb - cv2.resize(out, (w, h), interpolation=cv2.INTER_AREA)
        out += 0.65 * cv2.resize(error, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    return np.clip(out, 0, 1), 'Lanczos + 迭代反投影 · CPU'


def onnx_super_resolve(rgb, scale, path, use_cuda, progress=None, cancel=None, native_scale=None):
    sess = compute.session(path, use_cuda)
    inp = sess.get_inputs()
    if len(inp) != 1 or inp[0].type != 'tensor(float)' or len(inp[0].shape) != 4:
        raise ValueError('模型需为单输入 float32 NCHW RGB，范围 0–1。')
    if isinstance(inp[0].shape[1], int) and inp[0].shape[1] != 3:
        raise ValueError('模型输入需为 3 通道 RGB。')
    if any(isinstance(n, int) for n in inp[0].shape[2:]):
        raise ValueError('分块超分要求模型支持动态宽高，当前模型为固定尺寸。')
    h, w = rgb.shape[:2]
    out = large_image.allocate((h * scale, w * scale, 3))
    native = native_scale or scale
    tile, pad = 192, 40
    total = ((h + tile - 1) // tile) * ((w + tile - 1) // tile)
    done = 0
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            if cancel is not None and cancel.is_set():
                raise InterruptedError('已取消增强')
            ey, ex = min(h, y + tile), min(w, x + tile)
            sy, sx = max(0, y - pad), max(0, x - pad)
            ty, tx = min(h, ey + pad), min(w, ex + pad)
            block = rgb[sy:ty, sx:tx].transpose(2, 0, 1)[None].copy()
            predicted = sess.run(None, {inp[0].name: block})[0]
            expected = (1, 3, (ty - sy) * native, (tx - sx) * native)
            if predicted.shape != expected:
                raise ValueError(f'模型输出尺寸 {predicted.shape} 与所选 {scale}× 不匹配。')
            predicted = predicted[0].transpose(1, 2, 0)
            if not np.isfinite(predicted).all():
                raise ValueError('超分模型返回无效像素。')
            if native != scale:
                predicted = cv2.resize(
                    predicted, ((tx - sx) * scale, (ty - sy) * scale), interpolation=cv2.INTER_AREA
                )
            out[y * scale : ey * scale, x * scale : ex * scale] = predicted[
                (y - sy) * scale : (ey - sy) * scale, (x - sx) * scale : (ex - sx) * scale
            ]
            done += 1
            if progress:
                progress(done, total)
    label = 'Real-ESRGAN · ' if native_scale else 'ONNX · '
    provider = sess.get_providers()[0]
    if use_cuda and provider == 'CPUExecutionProvider':
        label += 'GPU 不可用，'
    return np.clip(out, 0, 1, out=out), label + provider


from .imaging.io import export_image as export_image
