"""Safe processor plumbing and float-preserving raster composition.

Pipeline dispatch follows semi-utils (Apache-2.0), commit b1cf1ce.
All image IO resolves supplied source/logo handles or bundled resource names.
"""

import json
from collections import OrderedDict
import hashlib
import threading
from abc import ABC, abstractmethod
from contextvars import ContextVar
from enum import Enum
from itertools import chain
from pathlib import Path

import cv2
import numpy as np
from PIL import Image as PILImage, ImageColor, ImageDraw as PILImageDraw

from . import large_image

ROOT = Path(__file__).parent / 'resources'
RENDER = ContextVar('watermark_render', default=None)
_FRAME_LAYERS = OrderedDict()
_FRAME_LAYER_LOCK = threading.RLock()
_FRAME_LAYER_BYTES = 0


def _layer_signature(image, state):
    """Identify photo/mask subgraphs; text layers remain specific to the frame."""
    memo = state.setdefault('layer_signatures', {})
    if id(image) in memo:
        return memo[id(image)]
    plan = image.plan
    if plan is None:
        return None
    kind = plan['kind']
    parts = [kind, image.size, image.mode]
    if kind == 'source':
        pixels = plan['pixels']
        if pixels.nbytes > 32 * 2**20:
            return None
        fingerprints = state.setdefault('source_fingerprints', {})
        if id(pixels) not in fingerprints:
            fingerprints[id(pixels)] = hashlib.blake2b(
                memoryview(np.ascontiguousarray(pixels)).cast('B'), digest_size=16
            ).digest()
        parts.extend((pixels.shape, pixels.dtype.str, fingerprints[id(pixels)]))
    elif kind == 'canvas':
        parts.append(tuple(plan['color']))
        for child, position, mask in plan['layers']:
            child_key = _layer_signature(child, state)
            mask_key = _layer_signature(mask, state) if isinstance(mask, Raster) else None
            if child_key is None or (mask is not None and mask_key is None):
                memo[id(image)] = None
                return None
            parts.append((child_key, tuple(position), mask_key))
    elif 'parent' in plan:
        parent_key = _layer_signature(plan['parent'], state)
        if parent_key is None:
            memo[id(image)] = None
            return None
        parts.append(parent_key)
    for key in ('radius', 'channel', 'amount', 'fill', 'resample', 'alpha_gamma'):
        if key in plan:
            parts.append((key, plan[key]))
    if 'box' in plan:
        parts.append(
            ('box', tuple(tuple(v) if isinstance(v, (list, tuple)) else v for v in plan['box']))
        )
    if 'alpha' in plan:
        alpha_key = (
            _layer_signature(plan['alpha'], state) if isinstance(plan['alpha'], Raster) else None
        )
        if alpha_key is None:
            memo[id(image)] = None
            return None
        parts.append(('alpha', alpha_key))
    result = tuple(parts)
    memo[id(image)] = result
    return result


class Direction(Enum):
    HORIZONTAL = 'horizontal'
    VERTICAL = 'vertical'
    DIAGONAL = 'diagonal'
    RADIAL = 'radial'


class Alignment(Enum):
    START = 'start'
    CENTER = 'center'
    END = 'end'
    TOP = 'start'
    MIDDLE = 'center'
    BOTTOM = 'end'
    LEFT = 'start'
    RIGHT = 'end'


def _parse_color(value):
    if isinstance(value, int):
        return (value, value, value, 255)
    if isinstance(value, (tuple, list)):
        return tuple(value) + (255,) if len(value) == 3 else tuple(value)
    text = str(value).strip()
    if ',' in text:
        return _parse_color([int(v) for v in text.strip('()').split(',')])
    return ImageColor.getcolor(text, 'RGBA')


def font_path(name):
    name = name or 'AlibabaPuHuiTi-2-45-Light.otf'
    if name == 'SpecialAlphabets P04 Regular.otf':
        # This requested font is absent from the upstream bundle too: load_font
        # follows the upstream missing-explicit-font fallback.
        return ROOT / name
    allowed = {p.name for p in ROOT.iterdir() if p.suffix.lower() in ('.ttf', '.otf')}
    if name not in allowed:
        raise ValueError('字体必须选择内置字体。')
    return ROOT / name


def allocation(size):
    w, h = map(int, size)
    if w < 1 or h < 1 or w * h > large_image.MAX_PIXELS:
        raise ValueError('水印处理尺寸无效或超过4亿像素。')
    return w, h


def decoration(pixels):
    rgb = np.asarray(pixels.convert('RGB'), dtype=np.float32) / 255
    state = RENDER.get()
    space = state.get('color_space', 'sRGB') if state else 'sRGB'
    if space != 'sRGB':
        from .imaging.colorspaces import convert

        rgb = convert(rgb, 'sRGB', space)
    return rgb


class Raster:
    def __init__(self, pil, rgb=None, size=None, shape_only=False):
        self.pil = pil
        self.rgb = rgb
        self._size = size or pil.size
        self.shape_only = shape_only
        self.plan = None
        self.owns_rgb = True

    @property
    def size(self):
        return self._size

    @property
    def width(self):
        return self._size[0]

    @property
    def height(self):
        return self._size[1]

    @property
    def mode(self):
        return self.pil.mode

    def __array__(self, dtype=None, copy=None):
        return np.array(self.pil, dtype=dtype, copy=True)

    def convert(self, mode):
        out = Raster(self.pil.convert(mode), self.rgb, self.size, self.shape_only)
        if mode != self.mode:
            out.pil.info.pop('watermark_glyph_bounds', None)
        out.plan = self.plan
        out.owns_rgb = self.owns_rgb
        return out

    def copy(self):
        out = Raster(
            self.pil.copy(),
            None if self.rgb is None else self.rgb.copy(),
            self.size,
            self.shape_only,
        )
        out.plan = None if self.plan is None else dict(self.plan)
        if out.plan and 'layers' in out.plan:
            out.plan['layers'] = list(out.plan['layers'])
        return out

    def resize(self, size, resample=PILImage.Resampling.LANCZOS):
        size = allocation(size)
        if self.plan is not None:
            return virtual('resize', size, self.mode, parent=self, resample=resample)
        if self.shape_only or (RENDER.get() or {}).get('geometry_only'):
            return Raster(self.pil.copy(), size=size, shape_only=True)
        pil = self.pil.resize(size, resample)
        rgb = None
        if self.rgb is not None:
            rgb = np.stack(
                [
                    np.asarray(PILImage.fromarray(self.rgb[:, :, i]).resize(size, resample))
                    for i in range(3)
                ],
                axis=2,
            )
        return Raster(pil, rgb)

    def crop(self, box):
        size = allocation((int(box[2] - box[0]), int(box[3] - box[1])))
        if self.plan is not None:
            return virtual('crop', size, self.mode, parent=self, box=box)
        if self.shape_only:
            return Raster(self.pil.copy(), size=size, shape_only=True)
        pil = self.pil.crop(box)
        rgb = None
        if self.rgb is not None:
            rgb = np.stack(
                [np.asarray(PILImage.fromarray(self.rgb[:, :, i]).crop(box)) for i in range(3)],
                axis=2,
            )
        return Raster(pil, rgb)

    def getchannel(self, key):
        return (
            virtual('channel', self.size, 'L', parent=self, channel=key)
            if self.plan is not None
            else self.pil.getchannel(key)
        )

    def split(self):
        return (
            tuple(self.getchannel(k) for k in self.mode)
            if self.plan is not None
            else self.pil.split()
        )

    def putalpha(self, alpha):
        if self.shape_only:
            return
        if self.plan is not None:
            self.plan = dict(self.plan, alpha=alpha)
            return
        alpha = alpha.pil if isinstance(alpha, Raster) else alpha
        if alpha.size != self.pil.size:
            alpha = alpha.resize(self.pil.size)
        self.pil.putalpha(alpha)
        self.pil.info.pop('watermark_glyph_bounds', None)

    def filter(self, operation):
        if self.plan is not None:
            return virtual(
                'blur',
                self.size,
                self.mode,
                parent=self,
                radius=float(getattr(operation, 'radius', 0)),
            )
        if self.shape_only:
            return self.copy()
        pil = self.pil.filter(operation)
        pil.info.pop('watermark_glyph_bounds', None)
        rgb = None
        if self.rgb is not None:
            radius = float(getattr(operation, 'radius', 0))
            rgb = cv2.GaussianBlur(self.rgb, (0, 0), radius) if radius > 0 else self.rgb.copy()
        return Raster(pil, rgb)

    def paste(self, image, box=(0, 0), mask=None):
        box = tuple(map(int, box))
        source = image if isinstance(image, Raster) else Raster(image)
        if self.plan is not None:
            if self.plan['kind'] != 'canvas':
                previous = self.copy()
                self.plan = {
                    'kind': 'canvas',
                    'color': (0, 0, 0, 0),
                    'layers': [(previous, (0, 0), None)],
                }
            self.plan['layers'].append((source, tuple(box), mask))
            return
        raster_mask = mask.pil if isinstance(mask, Raster) else mask
        if self.shape_only:
            return
        if source.shape_only:
            raise ValueError('尺寸规划图不能用于最终渲染。')
        x, y = map(int, box[:2])
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.width, x + source.width), min(self.height, y + source.height)
        if x1 > x0 and y1 > y0 and (self.rgb is not None or source.rgb is not None):
            if self.rgb is not None and not self.owns_rgb:
                copied = large_image.allocate(self.rgb.shape)
                for row, block in large_image.strips(self.rgb):
                    copied[row : row + len(block)] = block
                self.rgb = copied
                self.owns_rgb = True
            if self.rgb is None:
                self.rgb = decoration(self.pil)
            values = source.rgb if source.rgb is not None else decoration(source.pil)
            values = values[y0 - y : y1 - y, x0 - x : x1 - x]
            if raster_mask is None:
                self.rgb[y0:y1, x0:x1] = values
            else:
                alpha = (
                    np.asarray(
                        raster_mask.getchannel('A') if raster_mask.mode == 'RGBA' else raster_mask,
                        dtype=np.float32,
                    )
                    / 255
                )
                alpha = alpha[y0 - y : y1 - y, x0 - x : x1 - x, None]
                target = self.rgb[y0:y1, x0:x1]
                target[:] = target * (1 - alpha) + values * alpha
        self.pil.paste(source.pil, box, raster_mask)
        self.pil.info.pop('watermark_glyph_bounds', None)


def virtual(kind, size, mode='RGBA', **plan):
    result = Raster(PILImage.new(mode, (1, 1)), size=allocation(size))
    result.plan = {'kind': kind, **plan}
    return result


class VirtualDraw:
    def __init__(self, image):
        self.image = image

    def rounded_rectangle(self, box, radius=0, fill=255):
        self.image.plan = {'kind': 'rounded_mask', 'box': box, 'radius': radius, 'fill': fill}


def _has_photo(image):
    if not isinstance(image, Raster) or image.plan is None:
        return image.rgb is not None
    plan = image.plan
    if plan['kind'] == 'source':
        return True
    if 'parent' in plan and _has_photo(plan['parent']):
        return True
    if 'alpha' in plan and _has_photo(plan['alpha']):
        return True
    return any(_has_photo(layer[0]) for layer in plan.get('layers', []))


def materialize(image):
    """Rasterize decorations at their physical size, keeping photographic canvases lazy."""
    if not isinstance(image, Raster):
        return image
    if image.plan is None:
        return image.pil
    if _has_photo(image):
        raise ValueError('预览不能展开全尺寸照片画布。')
    plan = image.plan
    kind = plan['kind']
    if kind == 'canvas':
        out = PILImage.new(image.mode, image.size, plan['color'])
        for child, box, mask in plan['layers']:
            out.paste(materialize(child), box, materialize(mask) if mask is not None else None)
    elif kind == 'resize':
        out = materialize(plan['parent']).resize(
            image.size, plan.get('resample', PILImage.Resampling.LANCZOS)
        )
    elif kind == 'crop':
        out = materialize(plan['parent']).crop(plan['box'])
    elif kind == 'channel':
        out = materialize(plan['parent']).getchannel(plan['channel'])
    elif kind == 'blur':
        from PIL import ImageFilter

        out = materialize(plan['parent']).filter(ImageFilter.GaussianBlur(plan['radius']))
    elif kind == 'rounded_mask':
        out = PILImage.new('L', image.size, 0)
        PILImageDraw.Draw(out).rounded_rectangle(
            plan['box'], radius=plan['radius'], fill=plan['fill']
        )
    else:
        raise ValueError('未知预览布局。')
    if 'alpha' in plan:
        out.putalpha(materialize(plan['alpha']))
    return out


def _rgba(image):
    pil = materialize(image) if isinstance(image, Raster) else image
    values = np.asarray(pil.convert('RGBA'), dtype=np.float32) / 255
    values[:, :, :3] *= values[:, :, 3:4]
    return values


def _area(array, rect, size, target_size):
    """Place a layer on the supersampled live-preview coordinate grid."""
    x, y, width, height = rect
    full_w, full_h = size
    out_w, out_h = target_size
    dx = x * out_w / full_w
    dy = y * out_h / full_h
    dw = width * out_w / full_w
    dh = height * out_h / full_h
    rw = max(1, round(dw))
    rh = max(1, round(dh))
    values = cv2.resize(array, (rw, rh), interpolation=cv2.INTER_AREA)
    transform = np.array([[1, 0, dx], [0, 1, dy]], np.float64)
    return cv2.warpAffine(
        values,
        transform,
        (out_w, out_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )


def render_virtual(image, target_size):
    """Reuse immutable sublayers within one bounded preview evaluation."""
    state = RENDER.get()
    if state is None:
        return _render_virtual(image, target_size)
    cache = state.setdefault('preview_memo', OrderedDict())
    plan = image.plan
    key = (id(image), target_size)
    if plan and plan['kind'] == 'source' and 'alpha' not in plan and 'alpha_gamma' not in plan:
        key = ('source', id(plan['pixels']), image.size, target_size)
    if key in cache:
        cache.move_to_end(key)
        return cache[key][1]
    signature = _layer_signature(image, state)
    shared_key = (state.get('color_space', 'sRGB'), signature, target_size) if signature else None
    if shared_key:
        with _FRAME_LAYER_LOCK:
            cached = _FRAME_LAYERS.get(shared_key)
            if cached is not None:
                _FRAME_LAYERS.move_to_end(shared_key)
                return cached
    result = _render_virtual(image, target_size)
    result.setflags(write=False)
    if shared_key and result.nbytes <= 64 * 2**20:
        global _FRAME_LAYER_BYTES
        with _FRAME_LAYER_LOCK:
            if shared_key not in _FRAME_LAYERS:
                while _FRAME_LAYERS and _FRAME_LAYER_BYTES + result.nbytes > 64 * 2**20:
                    _, removed = _FRAME_LAYERS.popitem(last=False)
                    _FRAME_LAYER_BYTES -= removed.nbytes
                _FRAME_LAYERS[shared_key] = result
                _FRAME_LAYER_BYTES += result.nbytes
    budget = 64 * 2**20
    used = state.get('preview_memo_bytes', 0)
    while cache and used + result.nbytes > budget:
        _, (_, removed) = cache.popitem(last=False)
        used -= removed.nbytes
    if result.nbytes <= budget:
        cache[key] = (image, result)
        used += result.nbytes
    state['preview_memo_bytes'] = used
    return result


def _render_virtual(image, target_size):
    """Evaluate a photographic layout on a bounded preview grid."""
    if image.plan is None or not _has_photo(image):
        values = _rgba(image)
        return cv2.resize(values, target_size, interpolation=cv2.INTER_AREA)
    plan = image.plan
    kind = plan['kind']
    tw, th = target_size
    if kind == 'source':
        rgb = cv2.resize(plan['pixels'], target_size, interpolation=cv2.INTER_AREA)
        out = np.concatenate((rgb, np.ones((th, tw, 1), np.float32)), axis=2)
    elif kind == 'canvas':
        color = np.asarray(plan['color'], np.float32) / 255
        out = np.empty((th, tw, 4), np.float32)
        out[:] = color
        out[:, :, :3] *= out[:, :, 3:4]
        for child, box, mask in plan['layers']:
            child_size = (
                max(1, round(child.width * tw / image.width)),
                max(1, round(child.height * th / image.height)),
            )
            if child.plan is not None and child.plan['kind'] == 'source':
                pixels = child.plan['pixels']
                values = np.concatenate(
                    (pixels, np.ones((*pixels.shape[:2], 1), np.float32)), axis=2
                )
            else:
                values = render_virtual(child, child_size) if _has_photo(child) else _rgba(child)
            if mask is not None:
                if isinstance(mask, Raster) and _has_photo(mask):
                    mask_values = render_virtual(mask, values.shape[1::-1])[:, :, 3:4]
                else:
                    mask_pil = materialize(mask)
                    alpha = (
                        np.asarray(
                            mask_pil.getchannel('A') if mask_pil.mode == 'RGBA' else mask_pil,
                            dtype=np.float32,
                        )
                        / 255
                    )
                    alpha = cv2.resize(alpha, values.shape[1::-1], interpolation=cv2.INTER_AREA)[
                        :, :, None
                    ]
                    values = values.copy()
                    original = values[:, :, 3:4].copy()
                    values[:, :, :3] = (
                        np.divide(
                            values[:, :, :3],
                            original,
                            out=np.zeros_like(values[:, :, :3]),
                            where=original > 0,
                        )
                        * alpha
                    )
                    values[:, :, 3:4] = alpha
                    mask_values = None
                if mask_values is not None:
                    values = values.copy()
                    original = values[:, :, 3:4].copy()
                    values[:, :, :3] = (
                        np.divide(
                            values[:, :, :3],
                            original,
                            out=np.zeros_like(values[:, :, :3]),
                            where=original > 0,
                        )
                        * mask_values
                    )
                    values[:, :, 3:4] = mask_values
            integrated = _area(
                values, (box[0], box[1], child.width, child.height), image.size, target_size
            )
            out *= 1 - integrated[:, :, 3:4]
            out += integrated
    elif kind == 'resize':
        out = render_virtual(plan['parent'], target_size).copy()
    elif kind == 'crop':
        parent = plan['parent']
        scale = max(tw / image.width, th / image.height)
        parent_size = (max(1, round(parent.width * scale)), max(1, round(parent.height * scale)))
        values = render_virtual(parent, parent_size)
        x, y, _, _ = plan['box']
        x = int(round(x * scale))
        y = int(round(y * scale))
        out = cv2.resize(
            values[max(0, y) : max(0, y) + th, max(0, x) : max(0, x) + tw],
            target_size,
            interpolation=cv2.INTER_AREA,
        )
    elif kind == 'blur':
        out = render_virtual(plan['parent'], target_size)
        radius = plan['radius'] * tw / image.width
        if radius > 0:
            out = cv2.GaussianBlur(out, (0, 0), radius)
    elif kind == 'white_blend':
        out = render_virtual(plan['parent'], target_size).copy()
        amount = plan['amount']
        out[:, :, :3] = out[:, :, :3] * (1 - amount) + out[:, :, 3:4] * amount
    elif kind == 'channel':
        out = render_virtual(plan['parent'], target_size)
        channel = 'RGBA'.index(plan['channel'])
        gray = out[:, :, channel : channel + 1]
        out = np.concatenate((gray, gray, gray, np.ones_like(gray)), axis=2)
    else:
        raise ValueError('未知预览布局。')
    if ('alpha' in plan or 'alpha_gamma' in plan) and not out.flags.writeable:
        out = out.copy()
    if 'alpha' in plan:
        alpha_image = plan['alpha']
        if (
            isinstance(alpha_image, Raster)
            and alpha_image.plan
            and alpha_image.plan['kind'] == 'rounded_mask'
        ):
            p = alpha_image.plan
            mask = PILImage.new('L', target_size, 0)
            PILImageDraw.Draw(mask).rounded_rectangle(
                [(0, 0), target_size], radius=p['radius'] * tw / image.width, fill=p['fill']
            )
            alpha = np.asarray(mask, np.float32)[:, :, None] / 255
        elif isinstance(alpha_image, Raster) and _has_photo(alpha_image):
            alpha = render_virtual(alpha_image, target_size)[:, :, :1]
        else:
            alpha = cv2.resize(
                np.asarray(materialize(alpha_image), np.float32) / 255,
                target_size,
                interpolation=cv2.INTER_AREA,
            )[:, :, None]
        out[:, :, :3] *= alpha
        out[:, :, 3:4] = alpha
    if 'alpha_gamma' in plan:
        old = out[:, :, 3:4].copy()
        new = np.power(old, plan['alpha_gamma'])
        new[new < 0.01] = 0
        out[:, :, :3] *= np.divide(new, old, out=np.zeros_like(new), where=old > 0)
        out[:, :, 3:4] = new
    return out


def _region(image, box, cache):
    """Evaluate physical pixel strips with the same RGB/alpha rules as Raster.

    The photograph is sampled from its proxy only for the requested strip;
    photographic canvases are never allocated at their full output size.
    """
    x0, y0, x1, y1 = map(int, box)
    width = x1 - x0
    height = y1 - y0
    if image.plan is None or not _has_photo(image):
        key = id(image)
        if key not in cache:
            cache[key] = materialize(image).convert('RGBA')
        pil = cache[key].crop(box)
        return decoration(pil), pil
    plan = image.plan
    kind = plan['kind']
    if kind == 'source':
        pixels = plan['pixels']
        sh, sw = pixels.shape[:2]
        if (sw, sh) == image.size:
            rgb = pixels[y0:y1, x0:x1]
        else:
            # Pillow's floating channels keep the proxy interpolation independent
            # from the byte alpha/decoration companion.
            sx0 = x0 * sw / image.width
            sy0 = y0 * sh / image.height
            sx1 = x1 * sw / image.width
            sy1 = y1 * sh / image.height
            rgb = np.stack(
                [
                    np.asarray(
                        PILImage.fromarray(pixels[:, :, i]).resize(
                            (width, height), PILImage.Resampling.BILINEAR, box=(sx0, sy0, sx1, sy1)
                        )
                    )
                    for i in range(3)
                ],
                axis=2,
            )
        pil = PILImage.fromarray(
            np.round(np.clip(rgb, 0, 1) * 255).astype(np.uint8), 'RGB'
        ).convert('RGBA')
    elif kind == 'canvas':
        pil = PILImage.new('RGBA', (width, height), plan['color'])
        rgb = decoration(pil)
        for child, pos, mask in plan['layers']:
            cx, cy = map(int, pos[:2])
            a = max(x0, cx)
            b = max(y0, cy)
            c = min(x1, cx + child.width)
            d = min(y1, cy + child.height)
            if c <= a or d <= b:
                continue
            source, source_pil = _region(child, (a - cx, b - cy, c - cx, d - cy), cache)
            mask_pil = None
            if mask is not None:
                if isinstance(mask, Raster):
                    mask_box = (a - cx, b - cy, c - cx, d - cy)
                    mask_pil = (
                        _alpha_region(mask, mask_box, cache)
                        if mask.mode == 'RGBA'
                        else _gray_region(mask, mask_box, cache)
                    )
                else:
                    mask_pil = mask.crop((a - cx, b - cy, c - cx, d - cy))
                if mask_pil.mode == 'RGBA':
                    mask_pil = mask_pil.getchannel('A')
                alpha = np.asarray(mask_pil, np.float32)[:, :, None] / 255
                target = rgb[b - y0 : d - y0, a - x0 : c - x0]
                target[:] = target * (1 - alpha) + source * alpha
            else:
                rgb[b - y0 : d - y0, a - x0 : c - x0] = source
            pil.paste(source_pil, (a - x0, b - y0), mask_pil)
    elif kind == 'crop':
        px, py = plan['box'][:2]
        rgb, pil = _region(plan['parent'], (x0 + px, y0 + py, x1 + px, y1 + py), cache)
    elif kind == 'resize':
        parent = plan['parent']
        sx = parent.width / image.width
        sy = parent.height / image.height
        halo_x = int(np.ceil(4 * max(1, sx)))
        halo_y = int(np.ceil(4 * max(1, sy)))
        a = max(0, int(np.floor(x0 * sx)) - halo_x)
        b = max(0, int(np.floor(y0 * sy)) - halo_y)
        c = min(parent.width, int(np.ceil(x1 * sx)) + halo_x)
        d = min(parent.height, int(np.ceil(y1 * sy)) + halo_y)
        source, source_pil = _region(parent, (a, b, c, d), cache)
        resize_box = (x0 * sx - a, y0 * sy - b, x1 * sx - a, y1 * sy - b)
        resample = plan.get('resample', PILImage.Resampling.LANCZOS)
        rgb = np.stack(
            [
                np.asarray(
                    PILImage.fromarray(source[:, :, i]).resize(
                        (width, height), resample, box=resize_box
                    )
                )
                for i in range(3)
            ],
            axis=2,
        )
        pil = source_pil.resize((width, height), resample, box=resize_box)
    elif kind == 'blur':
        from PIL import ImageFilter

        parent = plan['parent']
        radius = plan['radius']
        constant = _constant_color(parent)
        if constant is not None and not _has_float(parent):
            pil = PILImage.new('RGBA', (width, height), (*constant, 255))
            rgb = decoration(pil)
            base = image.copy()
            base.plan.pop('alpha_gamma', None)
            base.plan.pop('alpha', None)
            pil.putalpha(_alpha_region(base, box, cache))
        else:
            halo = int(np.ceil(4 * radius + 10))
            a = max(0, x0 - halo)
            b = max(0, y0 - halo)
            c = min(image.width, x1 + halo)
            d = min(image.height, y1 + halo)
            source, source_pil = _region(parent, (a, b, c, d), cache)
            blurred_pil = source_pil.filter(ImageFilter.GaussianBlur(radius))
            # Raster has no floating buffer for a pure decoration shadow. Its RGB
            # companion comes from the filtered Pillow raster in that case.
            has_float = _has_float(parent)
            rgb = (
                cv2.GaussianBlur(source, (0, 0), radius)
                if radius > 0 and has_float
                else (source if has_float else decoration(blurred_pil))
            )
            rgb = rgb[y0 - b : y1 - b, x0 - a : x1 - a].copy()
            pil = blurred_pil.crop((x0 - a, y0 - b, x1 - a, y1 - b))
    elif kind == 'channel':
        if plan['channel'] == 'A':
            channel = _alpha_region(plan['parent'], box, cache)
        else:
            channel = _region(plan['parent'], box, cache)[1].getchannel(plan['channel'])
        pil = channel.convert('RGBA')
        rgb = decoration(pil)
    elif kind == 'white_blend':
        from PIL import Image as PillowImage

        rgb, pil = _region(plan['parent'], box, cache)
        amount = plan['amount']
        pil = PillowImage.blend(pil, PillowImage.new('RGBA', pil.size, 'white'), amount)
        rgb = rgb * (1 - amount) + amount
    else:
        raise ValueError('未知预览布局。')
    if 'alpha' in plan:
        mask = plan['alpha']
        alpha = _gray_region(mask, box, cache) if isinstance(mask, Raster) else mask.crop(box)
        pil.putalpha(alpha)
    if 'alpha_gamma' in plan:
        alpha = np.asarray(pil.getchannel('A'), np.float32) / 255
        alpha = np.power(alpha, plan['alpha_gamma'])
        alpha[alpha < 0.01] = 0
        pil.putalpha(PILImage.fromarray((alpha * 255).astype(np.uint8), 'L'))
    return rgb, pil


def _constant_color(image):
    if image.plan is None:
        return None
    p = image.plan
    if p['kind'] in ('crop', 'resize', 'blur'):
        return _constant_color(p['parent'])
    if p['kind'] != 'canvas':
        return None
    color = p['color'][:3]
    return color if all(_constant_color(layer[0]) == color for layer in p['layers']) else None


def _has_float(image):
    if image.plan is None:
        return image.rgb is not None
    plan = image.plan
    if plan['kind'] == 'source':
        return True
    if plan['kind'] == 'channel':
        return False
    if 'parent' in plan:
        return _has_float(plan['parent'])
    return any(_has_float(layer[0]) for layer in plan.get('layers', []))


def _mask_region(image, box):
    x0, y0, x1, y1 = map(int, box)
    p = image.plan
    mask = PILImage.new('L', (x1 - x0, y1 - y0), 0)
    coords = [(xy[0] - x0, xy[1] - y0) for xy in p['box']]
    PILImageDraw.Draw(mask).rounded_rectangle(coords, radius=p['radius'], fill=p['fill'])
    return mask


def _gray_region(image, box, cache):
    if image.plan and image.plan['kind'] == 'rounded_mask':
        return _mask_region(image, box)
    if image.plan and image.plan['kind'] == 'channel' and image.plan['channel'] == 'A':
        return _alpha_region(image.plan['parent'], box, cache)
    return _region(image, box, cache)[1].convert('L')


def _alpha_region(image, box, cache):
    """Compute masks in one channel without allocating a photographic RGB tile."""
    x0, y0, x1, y1 = map(int, box)
    size = (x1 - x0, y1 - y0)
    if image.plan is None:
        return (
            image.pil.getchannel('A').crop(box)
            if image.mode == 'RGBA'
            else PILImage.new('L', size, 255)
        )
    p = image.plan
    kind = p['kind']
    if kind == 'source':
        alpha = PILImage.new('L', size, 255)
    elif kind == 'canvas':
        alpha = PILImage.new('L', size, p['color'][3] if image.mode == 'RGBA' else 255)
        for child, pos, mask in p['layers']:
            cx, cy = map(int, pos[:2])
            a = max(x0, cx)
            b = max(y0, cy)
            c = min(x1, cx + child.width)
            d = min(y1, cy + child.height)
            if c <= a or d <= b:
                continue
            crop = (a - cx, b - cy, c - cx, d - cy)
            child_alpha = _alpha_region(child, crop, cache)
            if mask is None:
                mask_alpha = None
            elif isinstance(mask, Raster):
                mask_alpha = (
                    _alpha_region(mask, crop, cache)
                    if mask.mode == 'RGBA'
                    else _gray_region(mask, crop, cache)
                )
            else:
                mask_alpha = (
                    mask.getchannel('A').crop(crop) if mask.mode == 'RGBA' else mask.crop(crop)
                )
            alpha.paste(child_alpha, (a - x0, b - y0), mask_alpha)
    elif kind == 'crop':
        px, py = p['box'][:2]
        alpha = _alpha_region(p['parent'], (x0 + px, y0 + py, x1 + px, y1 + py), cache)
    elif kind == 'resize':
        parent = p['parent']
        sx = parent.width / image.width
        sy = parent.height / image.height
        hx = int(np.ceil(4 * max(1, sx)))
        hy = int(np.ceil(4 * max(1, sy)))
        a = max(0, int(np.floor(x0 * sx)) - hx)
        b = max(0, int(np.floor(y0 * sy)) - hy)
        c = min(parent.width, int(np.ceil(x1 * sx)) + hx)
        d = min(parent.height, int(np.ceil(y1 * sy)) + hy)
        alpha = _alpha_region(parent, (a, b, c, d), cache).resize(
            size,
            p.get('resample', PILImage.Resampling.LANCZOS),
            box=(x0 * sx - a, y0 * sy - b, x1 * sx - a, y1 * sy - b),
        )
    elif kind == 'blur':
        from PIL import ImageFilter

        radius = p['radius']
        halo = int(np.ceil(4 * radius + 10))
        a = max(0, x0 - halo)
        b = max(0, y0 - halo)
        c = min(image.width, x1 + halo)
        d = min(image.height, y1 + halo)
        alpha = (
            _alpha_region(p['parent'], (a, b, c, d), cache)
            .filter(ImageFilter.GaussianBlur(radius))
            .crop((x0 - a, y0 - b, x1 - a, y1 - b))
        )
    elif kind == 'white_blend':
        alpha = PILImage.blend(
            _alpha_region(p['parent'], box, cache), PILImage.new('L', size, 255), p['amount']
        )
    elif kind == 'channel':
        alpha = PILImage.new('L', size, 255)
    elif kind == 'rounded_mask':
        return PILImage.new('L', size, 255)
    else:
        return _region(image, box, cache)[1].getchannel('A')
    if 'alpha' in p:
        alpha = _gray_region(p['alpha'], box, cache)
    if 'alpha_gamma' in p:
        values = np.power(np.asarray(alpha, np.float32) / 255, p['alpha_gamma'])
        values[values < 0.01] = 0
        alpha = PILImage.fromarray((values * 255).astype(np.uint8), 'L')
    return alpha


def render_strips(image, target_size):
    """Downsample physical output rows without a full photographic canvas."""
    out_w, out_h = target_size
    full_w, full_h = image.size
    sy = full_h / out_h
    output = np.zeros((out_h, out_w, 3), np.float32)
    cache = {}
    for start in range(0, full_h, 32):
        end = min(full_h, start + 32)
        rgb, _ = _region(image, (0, start, full_w, end), cache)
        horizontal = cv2.resize(rgb, (out_w, end - start), interpolation=cv2.INTER_AREA)
        first = max(0, int(start / sy))
        last = min(out_h, int(np.ceil(end / sy)))
        for row in range(first, last):
            a = max(start, row * sy)
            b = min(end, (row + 1) * sy)
            lo = int(np.floor(a))
            hi = int(np.ceil(b))
            positions = np.arange(lo, hi, dtype=np.float64)
            weights = np.maximum(0, np.minimum(positions + 1, b) - np.maximum(positions, a))
            output[row] += (
                np.sum(horizontal[lo - start : hi - start] * weights[:, None, None], axis=0) / sy
            )
    return output


class ImageFactory:
    Image = Raster
    Resampling = PILImage.Resampling

    def new(self, mode, size, color=0):
        size = allocation(size)
        state = RENDER.get() or {}
        if state.get('preview_virtual') and not state.get('text_raster'):
            return virtual('canvas', size, mode, color=_parse_color(color), layers=[])
        shape = state.get('geometry_only', False) and not state.get('text_raster', False)
        return Raster(
            PILImage.new(mode, (1, 1) if shape else size, color), size=size, shape_only=shape
        )

    def fromarray(self, array, mode=None):
        return Raster(PILImage.fromarray(array, mode))

    def open(self, handle):
        state = RENDER.get() or {}
        if handle == 'source':
            return state['source'].copy()
        if handle in state.get('images', {}):
            return Raster(state['images'][handle].copy())
        name = str(handle)
        if (
            name.startswith('watermark-brand-')
            and name.endswith('.png')
            and Path(name).name == name
        ):
            with PILImage.open(ROOT / name) as image:
                return Raster(image.convert('RGBA'))
        raise ValueError('水印图像必须来自当前照片、内置标志或导入PNG。')


Image = ImageFactory()


class DrawFactory:
    def Draw(self, image):
        if isinstance(image, Raster) and image.plan is not None:
            return VirtualDraw(image)
        return PILImageDraw.Draw(image.pil if isinstance(image, Raster) else image)


ImageDraw = DrawFactory()


class PipelineContext(dict):
    def get(self, key, default=None):
        value = super().get(key, default)
        return default if value is None else value

    def getint(self, key, default=0):
        return int(self.get(key, default))

    def getcolor(self, key, default=None):
        return _parse_color(self.get(key, default))

    def getenum(self, key, default=None, enum=None):
        value = self.get(key, default)
        if enum is None or isinstance(value, enum):
            return value
        try:
            return enum[str(value).upper()]
        except KeyError:
            try:
                return enum(value)
            except ValueError:
                return default

    def get_buffer(self):
        if self.get('buffer_path') and not self.get('buffer_loaded'):
            self['buffer'] = [Image.open(p) for p in self['buffer_path']]
            self['buffer_loaded'] = True
        return self.get('buffer', [])

    def get_exif(self):
        return self.get('exif', {})

    def set(self, key, value):
        self[key] = value

    def update_buffer(self, value):
        self['buffer'] = value
        return self

    def save_buffer(self, *args, **kwargs):
        return self

    def success(self, *args):
        return self


class ImageProcessor(ABC):
    @abstractmethod
    def process(self, ctx):
        pass

    @abstractmethod
    def category(self):
        pass


def get_processor(name):
    from . import watermark_processors as processors

    for cls in vars(processors).values():
        if isinstance(cls, type) and issubclass(cls, ImageProcessor) and cls is not ImageProcessor:
            try:
                if cls().name() == name:
                    return cls
            except TypeError:
                pass
    return None


def start_process(data, input_path=None, output_path=None, initial_buffer=None):
    if input_path is not None or output_path is not None:
        raise ValueError('水印管线不接受文件路径。')
    nodes = [PipelineContext(dict(node)) for node in data]
    if initial_buffer is not None:
        nodes[0]['buffer'] = initial_buffer
        nodes[0]['buffer_loaded'] = not bool(nodes[0].get('buffer_path'))
    output = nodes[0].get_buffer()
    all_buffer = [output]
    last_merger = -1
    for index, node in enumerate(nodes):
        processor = get_processor(node['processor_name'])
        if processor is None:
            raise ValueError('未知水印处理器。')
        processor = processor()
        if 'select' in node:
            indexes = (
                json.loads(node['select']) if isinstance(node['select'], str) else node['select']
            )
            if any(type(i) is not int or i < 0 or i >= len(all_buffer) for i in indexes):
                raise ValueError('处理器输入索引无效。')
            node.update_buffer(list(chain.from_iterable(all_buffer[i] for i in indexes)))
        elif processor.category() != 'merger':
            node.update_buffer(output)
        else:
            node.update_buffer(list(chain.from_iterable(all_buffer[last_merger + 1 : index + 1])))
            last_merger = index
        processor.process(node)
        output = node.get_buffer()
        all_buffer.append(output)
    if not output:
        raise ValueError('水印处理器未生成图像。')
    return output[0]
