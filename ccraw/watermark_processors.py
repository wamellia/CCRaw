"""Adapted semi-utils processor algorithms (Apache-2.0).
Source: leslievan/semi-utils b1cf1ce655e9452a78a88f465d4387f84cf6740e.
Application imports, logging, entry scripts and unrestricted IO are removed.
Rendering classes retain upstream formulas; float buffers are supplied by CCRaw.
"""

import json
import re
from functools import lru_cache
from abc import ABC
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional, List, Tuple
import numpy as np
from PIL import ImageFont, ImageFilter, Image as PILImage, ImageDraw as PILImageDraw
from .watermark_runtime import (
    Image,
    ImageDraw,
    PipelineContext,
    ImageProcessor,
    Direction,
    Alignment,
    _parse_color,
    start_process,
    get_processor,
    font_path,
    RENDER,
    allocation,
    virtual,
)

BASE_FONT_SIZE = 512


@lru_cache(maxsize=32)
def load_font(path):
    try:
        return ImageFont.truetype(str(font_path(path)), BASE_FONT_SIZE)
    except OSError:
        return ImageFont.load_default()


@lru_cache(maxsize=8)
def _glyph(text, path, color):
    font = load_font(path)
    bbox = font.getbbox(text)
    metrics = font.getmetrics()
    size = allocation((max(1, int(bbox[2] - bbox[0])), metrics[0] + abs(metrics[1])))
    image = PILImage.new('RGBA', size, (0, 0, 0, 0))
    PILImageDraw.Draw(image).text((0, 0), text, font=font, fill=color)
    image.info['watermark_glyph_size'] = image.size
    image.info['watermark_glyph_bounds'] = {}
    return image


class FilterProcessor(ImageProcessor, ABC):
    def category(self) -> str:
        return 'filter'


class BlurFilter(FilterProcessor):
    def process(self, ctx: PipelineContext):
        radius = ctx.getint('blur_radius', 5)
        buffer = []
        for img in ctx.get_buffer():
            if img.mode != 'RGB':
                img = img.convert('RGB')
            ret_img = img.filter(ImageFilter.GaussianBlur(radius=radius))
            buffer.append(ret_img)
        ctx.update_buffer(buffer).save_buffer(self.name()).success()

    def name(self) -> str:
        return 'blur'


class ResizeFilter(FilterProcessor):
    def process(self, ctx: PipelineContext):
        width, height = (ctx.get('width'), ctx.get('height'))
        scale = ctx.get('scale')
        buffer = []
        for img in ctx.get_buffer():
            if width and height:
                target_size = (int(width), int(height))
            else:
                if width:
                    scale_f = float(width) / img.width
                elif height:
                    scale_f = float(height) / img.height
                elif scale:
                    scale_f = float(scale)
                else:
                    ctx.set('success', False)
                    return
                target_size = (max(1, int(img.width * scale_f)), max(1, int(img.height * scale_f)))
            ret_img = img.resize(target_size, resample=Image.Resampling.LANCZOS)
            buffer.append(ret_img)
        ctx.update_buffer(buffer).save_buffer(self.name()).success()

    def name(self) -> str:
        return 'resize'


class TrimFilter(FilterProcessor):
    threshold: float = (10.0,)
    padding: int = 0

    def process(self, ctx: PipelineContext):
        buffer = []
        for image in ctx.get_buffer():
            if image.shape_only:
                buffer.append(image)
                continue
            if image.height * image.width == 0:
                continue
            bbox = self.get_foreground_bbox(
                image,
                trim_left=ctx.get('trim_left', True),
                trim_right=ctx.get('trim_right', True),
                trim_top=ctx.get('trim_top', True),
                trim_bottom=ctx.get('trim_bottom', True),
            )
            buffer.append(image.crop(bbox))
        ctx.update_buffer(buffer).save_buffer(self.name()).success()

    def name(self) -> str:
        return 'trim'

    def _get_background_color(self, img_array: np.ndarray) -> np.ndarray:
        """取四角像素均值作为背景色"""
        corners = np.array([img_array[0, 0], img_array[0, -1], img_array[-1, 0], img_array[-1, -1]])
        return np.mean(corners, axis=0)

    def _shrink_bbox(
        self, diff: np.ndarray, threshold: float, width: int, height: int
    ) -> Tuple[int, int, int, int]:
        """
        从四个方向向内收缩边界框

        Args:
            diff: 每个像素与背景的差异矩阵 shape: (height, width)
            threshold: 差异阈值
            width: 图像宽度
            height: 图像高度

        Returns:
            (left, right, top, bottom) 收缩后的边界
        """
        exceeds = diff > threshold
        col_exceeds = np.any(exceeds, axis=0)
        row_exceeds = np.any(exceeds, axis=1)
        if not np.any(col_exceeds):
            return (0, width, 0, height)
        left = int(np.argmax(col_exceeds))
        right = int(width - np.argmax(col_exceeds[::-1]))
        top = int(np.argmax(row_exceeds))
        bottom = int(height - np.argmax(row_exceeds[::-1]))
        return (left, right, top, bottom)

    def get_foreground_bbox(
        self,
        image: Image.Image,
        threshold: float = 10.0,
        padding: int = 0,
        trim_left: bool = True,
        trim_right: bool = True,
        trim_top: bool = True,
        trim_bottom: bool = True,
    ) -> Tuple[int, int, int, int]:
        cached = image.pil.info.get('watermark_glyph_bounds')
        cache_key = (threshold, padding, trim_left, trim_right, trim_top, trim_bottom)
        if image.pil.info.get('watermark_glyph_size') != image.size:
            cached = None
        if cached is not None and cache_key in cached:
            return cached[cache_key]
        img_array = np.array(image, dtype=np.float32)
        if img_array.ndim == 2:
            img_array = img_array[:, :, np.newaxis]
        height, width, channels = img_array.shape
        background_color = self._get_background_color(img_array)
        diff = np.sqrt(np.sum((img_array - background_color) ** 2, axis=-1))
        left, right, top, bottom = self._shrink_bbox(diff, threshold, width, height)
        if not trim_left:
            left = 0
        if not trim_right:
            right = width
        if not trim_top:
            top = 0
        if not trim_bottom:
            bottom = height
        left = max(0, left - padding)
        top = max(0, top - padding)
        right = min(width, right + padding)
        bottom = min(height, bottom + padding)
        result = (left, top, right, bottom)
        if cached is not None:
            cached[cache_key] = result
        return result


class MarginFilter(FilterProcessor):
    def process(self, ctx: PipelineContext):
        left_margin = ctx.getint('left_margin', 0)
        right_margin = ctx.getint('right_margin', 0)
        top_margin = ctx.getint('top_margin', 0)
        bottom_margin = ctx.getint('bottom_margin', 0)
        color = ctx.get('margin_color', 'white')
        buffer = []
        for img in ctx.get_buffer():
            original_width, original_height = img.size
            new_width = original_width + left_margin + right_margin
            new_height = original_height + top_margin + bottom_margin
            new_img = Image.new(img.mode, (new_width, new_height), color)
            offset_x = left_margin
            offset_y = top_margin
            new_img.paste(img, (offset_x, offset_y))
            buffer.append(new_img)
        ctx.update_buffer(buffer).save_buffer(self.name()).success()

    def name(self) -> str:
        return 'margin'


class MarginWithRatioFilter(FilterProcessor):
    ratio_pattern = re.compile('[0-9.]+:[0-9.]+')
    ratio_threshold = 0.01

    def process(self, ctx: PipelineContext):
        buffer = ctx.get_buffer()
        if not buffer:
            return
        real_ratio = (
            1.0 * int(ctx.get_exif().get('ImageWidth')) / int(ctx.get_exif().get('ImageHeight'))
        )
        if 'ratio' in ctx:
            if not isinstance(
                ctx.get('ratio'), str
            ) or not MarginWithRatioFilter.ratio_pattern.fullmatch(ctx.get('ratio')):
                raise ValueError('填充比例需要正数 W:H。')
            ratio_w, ratio_h = ctx.get('ratio').split(':')
            if not float(ratio_w) > 0 or not float(ratio_h) > 0:
                raise ValueError('填充比例需要正数 W:H。')
            real_ratio = 1.0 * float(ratio_w) / float(ratio_h)
        img = buffer[0]
        cur_ratio = 1.0 * img.width / img.height
        if cur_ratio - real_ratio > MarginWithRatioFilter.ratio_threshold:
            new_h = int(img.width / real_ratio)
            pad_vertical = new_h - img.height
            ctx.set('top_margin', pad_vertical / 2)
            ctx.set('bottom_margin', pad_vertical - pad_vertical / 2)
        elif cur_ratio - real_ratio < -MarginWithRatioFilter.ratio_threshold:
            new_w = int(img.height * real_ratio)
            pad_horizontal = new_w - img.width
            ctx.set('left_margin', pad_horizontal / 2)
            ctx.set('right_margin', pad_horizontal - pad_horizontal / 2)
        else:
            return
        MarginFilter().process(ctx)
        ctx.save_buffer(self.name()).success()

    def name(self) -> str:
        return 'margin_with_ratio'


class WatermarkFilter(FilterProcessor):
    def process(self, ctx: PipelineContext):
        img = ctx.get_buffer()[0]
        color = ctx.get('color', 'white')
        delimiter_color = ctx.get('delimiter_color', 'black')
        delimiter_width = max(1, ctx.getint('delimiter_width', int(img.width * 0.003)))
        left_margin = ctx.getint('left_margin', 0)
        right_margin = ctx.getint('right_margin', 0)
        top_margin = ctx.getint('top_margin', 0)
        bottom_margin = ctx.getint('bottom_margin', int(img.height * 0.12))
        middle_spacing = ctx.getint('middle_spacing', int(bottom_margin * 0.05))
        right_alignment = ctx.getenum('right_alignment', Alignment.RIGHT, Alignment)
        for key in ('left_top', 'left_bottom', 'right_top', 'right_bottom'):
            if not ctx.get(key):
                ctx[key] = {'processor_name': 'rich_text', 'text': ''}
        for t_s in [
            ctx.get('left_top'),
            ctx.get('left_bottom'),
            ctx.get('right_top'),
            ctx.get('right_bottom'),
        ]:
            if 'height' not in t_s:
                t_s['height'] = int(bottom_margin * 0.3)
        left_top = start_process([ctx.get('left_top')])
        left_bottom = start_process([ctx.get('left_bottom')])
        right_top = start_process([ctx.get('right_top')])
        right_bottom = start_process([ctx.get('right_bottom')])
        left_logo = (
            Image.open(ctx.get('left_logo')).convert('RGBA') if ctx.get('left_logo') else None
        )
        right_logo = (
            Image.open(ctx.get('right_logo')).convert('RGBA') if ctx.get('right_logo') else None
        )
        center_logo = (
            Image.open(ctx.get('center_logo')).convert('RGBA') if ctx.get('center_logo') else None
        )
        center_logo_height = ctx.getint('center_logo_height')
        canvas_width = img.width + left_margin + right_margin
        canvas_height = img.height + top_margin + bottom_margin
        common_spacing = int(0.02 * canvas_width)
        canvas = Image.new('RGBA', (canvas_width, canvas_height), color)
        canvas.paste(img, (left_margin, top_margin), mask=img if img.mode == 'RGBA' else None)
        footer_start_y = top_margin + img.height
        left_logo_width = 0
        if left_logo:
            logo_size = canvas_height - footer_start_y
            left_logo = left_logo.resize((logo_size, logo_size), Image.Resampling.LANCZOS)
            canvas.paste(
                left_logo,
                (left_margin, footer_start_y),
                mask=left_logo if left_logo.mode == 'RGBA' else None,
            )
            left_logo_width = logo_size
        if center_logo:
            logo_height = (
                center_logo_height if center_logo_height else canvas_height - footer_start_y
            )
            resize_ctx = PipelineContext({'buffer': [center_logo], 'height': logo_height})
            ResizeFilter().process(resize_ctx)
            center_logo = resize_ctx.get_buffer()[0]
            center_x = (canvas.width - center_logo.width) // 2
            center_y = footer_start_y + (canvas.height - footer_start_y - center_logo.height) // 2
            canvas.paste(
                center_logo,
                (center_x, center_y),
                mask=center_logo if center_logo.mode == 'RGBA' else None,
            )
        elem_height = (
            max(left_top.height + left_bottom.height, right_top.height + right_bottom.height)
            + middle_spacing
        )
        elem_margin = int((bottom_margin - elem_height) / 2)
        l_x = left_margin + left_logo_width + common_spacing
        right_content_end_x = canvas_width - right_margin
        bottom_dist_lt = elem_margin + left_bottom.height + middle_spacing + left_top.height
        lt_y = canvas_height - bottom_dist_lt
        bottom_dist_lb = elem_margin + left_bottom.height
        lb_y = canvas_height - bottom_dist_lb
        rt_y = lt_y + left_top.height - right_top.height
        rt_x = right_content_end_x - right_top.width - common_spacing
        rb_y = lb_y + left_bottom.height - right_bottom.height
        rb_x = right_content_end_x - right_bottom.width - common_spacing
        if Alignment.LEFT == right_alignment:
            rt_x = rb_x = min(rt_x, rb_x)
        canvas.paste(left_top, (l_x, lt_y), mask=left_top if left_top.mode == 'RGBA' else None)
        canvas.paste(
            left_bottom, (l_x, lb_y), mask=left_bottom if left_bottom.mode == 'RGBA' else None
        )
        canvas.paste(right_top, (rt_x, rt_y), mask=right_top if right_top.mode == 'RGBA' else None)
        canvas.paste(
            right_bottom, (rb_x, rb_y), mask=right_bottom if right_bottom.mode == 'RGBA' else None
        )
        if right_logo:
            logo_size = elem_height
            delimiter = Image.new('RGBA', (delimiter_width, int(logo_size * 1.1)), delimiter_color)
            delimiter_x = (
                canvas_width
                - right_margin
                - max(right_top.width, right_bottom.width)
                - 2 * common_spacing
                - delimiter.width
            )
            delimiter_y = int(footer_start_y + elem_margin - logo_size * 0.05)
            canvas.paste(delimiter, (delimiter_x, delimiter_y), mask=delimiter)
            right_logo = right_logo.resize((logo_size, logo_size), Image.Resampling.LANCZOS)
            right_logo_x = delimiter_x - common_spacing - logo_size
            right_logo_y = footer_start_y + elem_margin
            canvas.paste(
                right_logo,
                (right_logo_x, right_logo_y),
                mask=right_logo if right_logo.mode == 'RGBA' else None,
            )
        ctx.update_buffer([canvas]).save_buffer(self.name()).success()

    def name(self) -> str:
        return 'watermark'


class WatermarkWithTimestampFilter(FilterProcessor):
    def process(self, ctx: PipelineContext):
        img = ctx.get_buffer()[0]
        if 'height' not in ctx:
            ctx.set('height', int(img.height * 0.02))
        multi_text_processor = get_processor('multi_rich_text')
        if multi_text_processor:
            multi_text_processor().process(ctx)
        else:
            raise RuntimeError('multi_rich_text processor not found')
        text = ctx.get_buffer()[0]
        text_x = int(img.width * 0.93) - text.width
        text_y = int(img.height * 0.95)
        img.paste(text, (text_x, text_y), mask=text)
        ctx.update_buffer([img]).save_buffer(self.name()).success()

    def name(self) -> str:
        return 'watermark_with_timestamp'


class RoundedCornerFilter(FilterProcessor):
    def process(self, ctx: PipelineContext):
        radius = ctx.getint('border_radius', 10)
        buffer = []
        for img in ctx.get_buffer():
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            width, height = img.size
            mask = Image.new('L', (width, height), 0)
            draw = ImageDraw.Draw(mask)
            draw.rounded_rectangle([(0, 0), (width, height)], radius=radius, fill=255)
            output = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            output.paste(img, (0, 0))
            output.putalpha(mask)
            buffer.append(output)
        ctx.update_buffer(buffer).save_buffer(self.name()).success()

    def name(self) -> str:
        return 'rounded_corner'


class ShadowFilter(FilterProcessor):
    def process(self, ctx: PipelineContext):
        shadow_color = ctx.getcolor('shadow_color', (0, 0, 0, 180))
        shadow_radius = ctx.getint('shadow_radius', 30)
        falloff = 1.5
        buffer = []
        for img in ctx.get_buffer():
            if img.mode != 'RGBA':
                original_img = img.convert('RGBA')
            else:
                original_img = img
            w, h = original_img.size
            if shadow_radius <= 0:
                buffer.append(img)
                continue
            padding = int(shadow_radius * 2)
            full_width = w + padding * 2
            full_height = h + padding * 2
            if (RENDER.get() or {}).get('geometry_only'):
                buffer.append(Image.new('RGBA', (full_width, full_height), (0, 0, 0, 0)))
                continue
            background = Image.new('RGBA', (full_width, full_height), (0, 0, 0, 0))
            shadow_layer = Image.new('RGBA', (w, h), shadow_color)
            shadow_layer.putalpha(original_img.getchannel('A'))
            background.paste(shadow_layer, (padding, padding))
            shadow_blurred = background.filter(ImageFilter.GaussianBlur(shadow_radius))
            shadow_blurred = self._apply_alpha_falloff(shadow_blurred, falloff)
            shadow_blurred.paste(original_img, (padding, padding), mask=original_img)
            buffer.append(shadow_blurred)
        ctx.update_buffer(buffer).save_buffer(self.name()).success()

    def process2(self, ctx: PipelineContext):
        shadow_color = ctx.getcolor('shadow_color', (0, 0, 0, 255))
        shadow_radius = ctx.getint('shadow_radius', 30)
        buffer = []
        for img in ctx.get_buffer():
            if img.mode != 'RGBA':
                original_img = img.convert('RGBA')
            else:
                original_img = img
            w, h = original_img.size
            if shadow_radius <= 0:
                buffer.append(img)
                continue
            padding = shadow_radius * 3
            full_width = w + padding * 2
            full_height = h + padding * 2
            background = Image.new('RGBA', (full_width, full_height), (0, 0, 0, 0))
            shadow_layer = Image.new('RGBA', (w, h), shadow_color)
            shadow_layer.putalpha(original_img.getchannel('A'))
            background.paste(shadow_layer, (padding, padding))
            shadow_in_process = background.filter(ImageFilter.GaussianBlur(shadow_radius))
            r, g, b, a = shadow_in_process.split()
            a = a.point(lambda p: int(p * (p / 255.0) * 1.2))
            shadow_in_process.putalpha(a)
            shadow_in_process.paste(original_img, (padding, padding), mask=original_img)
            buffer.append(shadow_in_process)
        ctx.update_buffer(buffer).save_buffer(self.name()).success()

    def _apply_alpha_falloff(self, img: Image.Image, gamma: float) -> Image.Image:
        """
        对 Alpha 通道应用幂函数衰减
        公式: new_alpha = (alpha / 255) ^ gamma * 255
        gamma > 1 时，低透明度像素会被压制得更低，边缘更干净
        """
        if img.plan is not None:
            img.plan = dict(img.plan, alpha_gamma=gamma)
            return img
        r, g, b, a = img.split()
        alpha_array = np.array(a, dtype=np.float32) / 255.0
        alpha_array = np.power(alpha_array, gamma)
        alpha_array[alpha_array < 0.01] = 0
        new_alpha = Image.fromarray((alpha_array * 255).astype(np.uint8), mode='L')
        img.putalpha(new_alpha)
        return img

    def name(self) -> str:
        return 'shadow'


class CropFilter(FilterProcessor):
    def process(self, ctx: PipelineContext):
        width = ctx.getint('width', 0)
        height = ctx.getint('height', 0)
        offset = (
            json.loads(ctx.get('offset', '[]'))
            if isinstance(ctx.get('offset', '[]'), str)
            else ctx.get('offset', [])
        )
        buffer = []
        for img in ctx.get_buffer():
            img_width, img_height = img.size
            if width <= 0:
                width = img_width
            if height <= 0:
                height = img_height
            left = (img_width - width) // 2
            top = (img_height - height) // 2
            offset_x = offset[0] if len(offset) > 0 else 0
            offset_y = offset[1] if len(offset) > 1 else 0
            left += offset_x
            top += offset_y
            left = max(0, min(left, img_width - width))
            top = max(0, min(top, img_height - height))
            right = left + width
            bottom = top + height
            cropped_img = img.crop((left, top, right, bottom))
            buffer.append(cropped_img)
        ctx.update_buffer(buffer).save_buffer(self.name()).success()

    def name(self) -> str:
        return 'crop'


@dataclass
class TextSegment:
    text: str
    font_path: Optional[str] = None
    height: int = 100
    is_bold: bool = False
    color: str = 'black'
    trim: bool = False

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    @staticmethod
    def from_dict(data: dict):
        return TextSegment(
            text=data.get('text'),
            font_path=data.get('font_path', None),
            height=int(data.get('height', 100)),
            color=data.get('color', 'black'),
            is_bold=data.get('is_bold', False),
            trim=data.get('trim', False),
        )

    @staticmethod
    def from_dicts(data: List[dict]):
        return [TextSegment.from_dict(datum) for datum in data]


class Generator(ImageProcessor, ABC):
    def category(self) -> str:
        return 'generator'


class SolidColorGenerator(Generator):
    def process(self, ctx: PipelineContext):
        width, height = (ctx.getint('width'), ctx.getint('height'))
        color = ctx.get('color')
        image = Image.new('RGBA', (width, height), color)
        ctx.update_buffer([image]).success()

    def name(self) -> str:
        return 'solid_color'


class InterpolateMethod(Enum):
    LINEAR = 'linear'
    EASE_IN = 'ease_in'
    EASE_OUT = 'ease_out'
    EASE_IN_OUT = 'ease_in_out'


def _easing_linear(t: np.ndarray) -> np.ndarray:
    """线性"""
    return t


def _easing_ease_in(t: np.ndarray) -> np.ndarray:
    """缓入"""
    return t * t


def _easing_ease_out(t: np.ndarray) -> np.ndarray:
    """缓出"""
    return 1 - (1 - t) ** 2


def _easing_ease_in_out(t: np.ndarray) -> np.ndarray:
    """缓入缓出"""
    return np.where(t < 0.5, 2 * t * t, 1 - (-2 * t + 2) ** 2 / 2)


EASING_FUNCTIONS = {
    InterpolateMethod.LINEAR: _easing_linear,
    InterpolateMethod.EASE_IN: _easing_ease_in,
    InterpolateMethod.EASE_OUT: _easing_ease_out,
    InterpolateMethod.EASE_IN_OUT: _easing_ease_in_out,
}


def _draw_gradient_numpy(
    width: int,
    height: int,
    start_rgba: tuple,
    end_rgba: tuple,
    direction: Direction,
    method: InterpolateMethod = InterpolateMethod.LINEAR,
) -> Image.Image:
    """NumPy 加速的渐变绘制"""
    width, height = allocation((width, height))
    if (RENDER.get() or {}).get('geometry_only'):
        return Image.new('RGBA', (width, height), start_rgba)
    x = np.arange(width)
    y = np.arange(height)
    xx, yy = np.meshgrid(x, y)
    if direction == Direction.HORIZONTAL:
        t = xx / (width - 1) if width > 1 else np.zeros_like(xx, dtype=float)
    elif direction == Direction.VERTICAL:
        t = yy / (height - 1) if height > 1 else np.zeros_like(yy, dtype=float)
    elif direction == Direction.DIAGONAL:
        t = (
            (xx + yy) / (width + height - 2)
            if width + height > 2
            else np.zeros_like(xx, dtype=float)
        )
    elif direction == Direction.RADIAL:
        cx, cy = (width / 2, height / 2)
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        max_dist = np.sqrt(cx**2 + cy**2)
        t = np.clip(dist / max_dist, 0, 1)
    else:
        t = np.zeros((height, width), dtype=float)
    easing_func = EASING_FUNCTIONS.get(method, _easing_linear)
    t = easing_func(t.astype(float))
    start = np.array(start_rgba, dtype=float)
    end = np.array(end_rgba, dtype=float)
    t = t[:, :, np.newaxis]
    pixels = start + (end - start) * t
    pixels = np.clip(pixels, 0, 255).astype(np.uint8)
    return Image.fromarray(pixels, mode='RGBA')


class GradientColorGenerator(Generator):
    def process(self, ctx: PipelineContext):
        width, height = (ctx.get('width'), ctx.get('height'))
        start_color = ctx.get('start_color')
        end_color = ctx.get('end_color')
        direction = ctx.getenum('direction', Direction.HORIZONTAL, Direction)
        method = ctx.getenum('interpolate_method', InterpolateMethod.LINEAR, InterpolateMethod)
        start_rgba = _parse_color(start_color)
        end_rgba = _parse_color(end_color)
        image = _draw_gradient_numpy(width, height, start_rgba, end_rgba, direction, method)
        ctx.update_buffer([image]).save_buffer(self.name()).success()

    def name(self) -> str:
        return 'gradient_color'


class RichTextGenerator(Generator):
    @staticmethod
    def generate(segment: TextSegment) -> Image.Image:
        text = ' ' if not segment.text or segment.text == '' else segment.text
        from .watermark_runtime import Raster

        image = Raster(_glyph(text, segment.font_path, _parse_color(segment.color)).copy())
        pipeline = [
            {
                'processor_name': 'trim',
                'trim_top': segment.trim,
                'trim_bottom': segment.trim,
                'save_buffer': False,
            },
            {
                'processor_name': 'resize',
                'height': segment.height * 1.13 if segment.is_bold else segment.height,
                'save_buffer': False,
            },
        ]
        from .watermark_runtime import start_process

        return start_process(pipeline, input_path=None, output_path=None, initial_buffer=[image])

    def process(self, ctx: PipelineContext):
        img = RichTextGenerator.generate(TextSegment.from_dict(ctx))
        ctx.update_buffer([img]).save_buffer(self.name()).success()

    def name(self) -> str:
        return 'rich_text'


class MultiRichTextGenerator(Generator):
    def process(self, ctx: PipelineContext):
        raw_segments = ctx.get('text_segments')
        text_segments: List[TextSegment] = TextSegment.from_dicts(raw_segments)
        text_alignment = ctx.get('text_alignment')
        text_spacing = ctx.getint('text_spacing')
        height = ctx.get('height', 100)
        text_images = []
        for raw, segment in zip(raw_segments, text_segments):
            segment.height = raw.get('height', height)
            context = PipelineContext(asdict(segment))
            context.set('save_buffer', False)
            RichTextGenerator().process(context)
            text_images.extend(context.get_buffer())
        from .watermark_runtime import start_process

        pipeline = [
            {
                'processor_name': 'concat',
                'alignment': text_alignment,
                'spacing': text_spacing,
                'save_buffer': False,
            }
        ]
        result = start_process(pipeline, initial_buffer=text_images)
        ctx.update_buffer([result]).save_buffer(self.name()).success()

    def name(self) -> str:
        return 'multi_rich_text'


class ImageLoader(Generator):
    def process(self, ctx: PipelineContext):
        if isinstance(ctx.get('path'), str):
            ctx.update_buffer([Image.open(ctx.get('path'))]).success()
        elif isinstance(ctx.get('path'), list):
            ctx.update_buffer([Image.open(path) for path in ctx.get('path')])

    def name(self) -> str:
        return 'image'


def _calc_offset(size: int, max_size: int, alignment: Alignment) -> int:
    """根据对齐方式计算偏移量"""
    align_value = alignment.value
    if align_value == 'start':
        return 0
    elif align_value == 'center':
        return (max_size - size) // 2
    elif align_value == 'end':
        return max_size - size
    return 0


class Merger(ImageProcessor, ABC):
    def category(self) -> str:
        return 'merger'


class AlignmentMerger(Merger):
    def process(self, ctx: PipelineContext):
        buffer: List[Image] = ctx.get_buffer()
        horizontal_alignment = ctx.getenum('horizontal_alignment', Alignment.CENTER, Alignment)
        vertical_alignment = ctx.getenum('vertical_alignment', Alignment.CENTER, Alignment)
        background: tuple = ctx.getcolor('background', (255, 255, 255, 0))
        offsets = (
            json.loads(ctx.get('offsets', '[]'))
            if isinstance(ctx.get('offsets', '[]'), str)
            else ctx.get('offsets', [])
        )
        weights = (
            json.loads(ctx.get('weights', '[]'))
            if isinstance(ctx.get('weights', '[]'), str)
            else ctx.get('weights', [])
        )
        if not buffer:
            return
        max_width = max((img.width for img in buffer))
        max_height = max((img.height for img in buffer))
        canvas = Image.new('RGBA', (max_width, max_height), background)
        if weights:
            missing_count = len(buffer) - len(weights)
            weights = weights + [0] * missing_count
            combined = zip(weights, buffer)
            sorted_combined = sorted(combined, key=lambda x: x[0], reverse=False)
            buffer = [item for _, item in sorted_combined]
        for i, img in enumerate(buffer):
            offset_x, offset_y = offsets[i] if i < len(offsets) else (0, 0)
            offset_x, offset_y = (-offset_x, -offset_y)
            img_x = _calc_offset(img.width, max_width, horizontal_alignment)
            img_y = _calc_offset(img.height, max_height, vertical_alignment)
            img_x += offset_x
            img_y += offset_y
            paste_img = img if img.mode == 'RGBA' else img.convert('RGBA')
            canvas.paste(paste_img, (img_x, img_y), paste_img)
        ctx.update_buffer([canvas]).save_buffer(self.name()).success()

    def name(self) -> str:
        return 'alignment'


class ConcatMerger(Merger):
    def process(self, ctx: PipelineContext):
        buffer = ctx.get_buffer()
        alignment = ctx.getenum('alignment', Alignment.BOTTOM, Alignment)
        direction = ctx.getenum('direction', Direction.HORIZONTAL, Direction)
        spacing = ctx.getint('spacing', 10)
        background = ctx.getcolor('background', (255, 255, 255, 0))
        images = [img.convert('RGBA') if img.mode == 'RGB' else img for img in buffer]
        if direction == Direction.HORIZONTAL:
            total_width = sum((img.width for img in images)) + spacing * (len(images) - 1)
            max_height = max((img.height for img in images))
            canvas_size = (total_width, max_height)
        else:
            max_width = max((img.width for img in images))
            total_height = sum((img.height for img in images)) + spacing * (len(images) - 1)
            canvas_size = (max_width, total_height)
        canvas = Image.new('RGBA', canvas_size, background)
        current_pos = 0
        for img in images:
            if direction == Direction.HORIZONTAL:
                y_offset = _calc_offset(img.height, max_height, alignment)
                canvas.paste(img, (current_pos, y_offset), img)
                current_pos += img.width + spacing
            else:
                x_offset = _calc_offset(img.width, max_width, alignment)
                canvas.paste(img, (x_offset, current_pos), img)
                current_pos += img.height + spacing
        ctx.update_buffer([canvas]).save_buffer(self.name()).success()

    def name(self) -> str:
        return 'concat'


# build1521 helpers. These retain the legacy normalized 1000px footer,
# padding and placement formulas; only file IO and deprecated Pillow APIs
# are adapted. Photo pixels continue through Raster, without an 8-bit roundtrip.
def _legacy_size(level, bold=False):
    table = (260, 290, 320) if bold else (240, 250, 300)
    return table[int(level) - 1] if int(level) in (1, 2, 3) else table[0]


def _legacy_text(text, ctx, color='black', bold=False):
    name = ctx.get(
        'bold_font_path' if bold else 'font_path',
        'AlibabaPuHuiTi-2-85-Bold.otf' if bold else 'AlibabaPuHuiTi-2-45-Light.otf',
    )
    font = ImageFont.truetype(
        str(font_path(name)),
        _legacy_size(ctx.get('bold_font_size' if bold else 'font_size', 1), bold),
    )
    text = str(text or '   ')
    bbox = font.getbbox(text)
    token = RENDER.set(dict(RENDER.get() or {}, text_raster=True))
    try:
        out = Image.new('RGBA', (max(1, bbox[2]), max(1, bbox[3])), (0, 0, 0, 0))
        ImageDraw.Draw(out).text((0, 0), text, fill=_parse_color(color), font=font)
    finally:
        RENDER.reset(token)
    return out


def _pad(image, size, locations='tb', color=(0, 0, 0, 0)):
    size = max(0, int(size))
    left = size if 'l' in locations else 0
    top = size if 't' in locations else 0
    out = Image.new(
        'RGBA',
        (
            image.width + left + (size if 'r' in locations else 0),
            image.height + top + (size if 'b' in locations else 0),
        ),
        color,
    )
    out.paste(image, (left, top))
    return out


def _merge(images, vertical=False, align='center'):
    if vertical:
        size = (max(i.width for i in images), sum(i.height for i in images))
    else:
        size = (sum(i.width for i in images), max(i.height for i in images))
    out = Image.new('RGBA', size, (0, 0, 0, 0))
    position = 0
    for img in images:
        if vertical:
            offset = (
                0
                if align == 'start'
                else (size[0] - img.width if align == 'end' else (size[0] - img.width) // 2)
            )
            out.paste(img, (offset, position))
            position += img.height
        else:
            offset = (
                0
                if align == 'start'
                else (size[1] - img.height if align == 'end' else (size[1] - img.height) // 2)
            )
            out.paste(img, (position, offset))
            position += img.width
    return out


def _legacy_resize_height(image, height):
    height = max(1, int(height))
    return image.resize(
        (max(1, round(image.width * height / image.height)), height), Image.Resampling.LANCZOS
    )


def _legacy_append(canvas, images, right=False, start=False):
    x = canvas.width - (200 if start else 0) if right else (200 if start else 0)
    for image in reversed(images) if right else images:
        if image is None:
            continue
        image = _legacy_resize_height(image, canvas.height)
        if right:
            x -= image.width + 200
        canvas.paste(image, (x, 0))
        if not right:
            x += image.width + 200


class LegacyWatermarkFilter(FilterProcessor):
    def name(self):
        return 'legacy_watermark'

    def process(self, ctx):
        img = ctx.get_buffer()[0]
        level = ctx.getint('font_size', 1) + ctx.getint('bold_font_size', 1)
        ratio = (0.04 if img.width / img.height >= 1 else 0.09) + 0.02 * level
        padding_ratio = (0.52 if img.width / img.height >= 1 else 0.7) - 0.04 * level
        bg = ctx.get('background', 'white')
        footer = Image.new('RGBA', (int(1000 / ratio), 1000), bg)
        columns = []
        for side in ('left', 'right'):
            top = ctx.get(side + '_top', {})
            bottom = ctx.get(side + '_bottom', {})
            columns.append(
                _merge(
                    [
                        _legacy_text(
                            top.get('text'),
                            ctx,
                            top.get('color', '#212121'),
                            top.get('is_bold', True),
                        ),
                        Image.new('RGBA', (10, 100), bg),
                        _legacy_text(
                            bottom.get('text'),
                            ctx,
                            bottom.get('color', '#424242'),
                            bottom.get('is_bold', False),
                        ),
                    ],
                    True,
                    'start',
                )
            )
        left, right = columns
        maximum = max(left.height, right.height)
        left = _pad(left, int(maximum * padding_ratio), 'tb')
        right = _pad(right, int(maximum * padding_ratio), 't')
        right = _pad(right, left.height - right.height, 'b')
        emblem = (
            Image.open(ctx['logo']).convert('RGBA')
            if ctx.get('logo') and ctx.get('logo_enable', True)
            else None
        )
        if emblem:
            emblem = _pad(emblem, int(padding_ratio * emblem.height))
        if ctx.get('logo_enable', True) and ctx.get('logo_position', 'left') == 'left':
            _legacy_append(
                footer,
                [Image.new('RGBA', (20, 1000), (0, 0, 0, 0)), emblem, left],
                start=emblem is None,
            )
            _legacy_append(footer, [right], right=True)
        elif ctx.get('logo_enable', True):
            line = Image.new(
                'RGBA',
                (20, 1000),
                ctx.get('delimiter_color', '#CBCBC9') if emblem else (0, 0, 0, 0),
            )
            if emblem:
                line = _pad(line, int(padding_ratio * line.height * 0.8))
            _legacy_append(footer, [left], start=True)
            _legacy_append(footer, [emblem, line, right], right=True)
        else:
            _legacy_append(footer, [left], start=True)
            _legacy_append(footer, [right], right=True)
        footer = footer.resize(
            (img.width, max(1, round(footer.height * img.width / footer.width))),
            Image.Resampling.LANCZOS,
        )
        canvas = Image.new('RGB', (img.width, img.height + footer.height), bg)
        canvas.paste(img, (0, 0), img if img.mode == 'RGBA' else None)
        canvas.paste(footer, (0, img.height), footer)
        ctx.update_buffer([canvas])


class LegacySimpleFilter(FilterProcessor):
    def name(self):
        return 'legacy_simple'

    def process(self, ctx):
        img = ctx.get_buffer()[0]
        exif = ctx.get_exif()
        color = ctx.get('color', '#212121')
        first = _merge(
            [
                _legacy_text('Shot on', ctx, color),
                Image.new('RGBA', (100, 20), (0, 0, 0, 0)),
                _legacy_text(
                    str(exif.get('CameraModelName', '')).replace('/', ' ').replace('_', ' '),
                    ctx,
                    ctx.get('model_color', '#D32F2F'),
                    True,
                ),
                Image.new('RGBA', (100, 20), (0, 0, 0, 0)),
                _legacy_text(str(exif.get('Make', '')).split(' ')[0], ctx, color, True),
            ],
            False,
            'end',
        )
        second = _legacy_text(exif.get('Param', ''), ctx, ctx.get('param_color', '#9E9E9E'))
        text = _merge([first, Image.new('RGBA', (20, 100), (0, 0, 0, 0)), second], True)
        ratio = 0.16 if img.width / img.height >= 1 else 0.1
        text = _legacy_resize_height(text, int(img.height * ratio * 0.5))
        footer_h = max(1, int(img.height * ratio))
        footer = Image.new('RGBA', (img.width, footer_h), ctx.get('background', 'white'))
        footer.paste(text, ((img.width - text.width) // 2, (footer_h - text.height) // 2), text)
        canvas = Image.new(
            'RGB', (img.width, img.height + footer_h), ctx.get('background', 'white')
        )
        canvas.paste(img, (0, 0), img if img.mode == 'RGBA' else None)
        canvas.paste(footer, (0, img.height), footer)
        ctx.update_buffer([canvas])


class LegacyBackgroundFilter(FilterProcessor):
    def name(self):
        return 'legacy_background'

    def process(self, ctx):
        source = ctx.get_buffer()[0]
        margin = ctx.getint('white_margin', 0)
        photo = _pad(source, margin, 'tblr', 'white') if margin else source
        background = source.convert('RGB').filter(
            ImageFilter.GaussianBlur(ctx.getint('blur_radius', 35))
        )
        percent = float(ctx.get('padding_percent', 0.18))
        blend = float(ctx.get('white_blend', 0.1))
        if not 0 <= blend <= 1:
            raise ValueError('白色混合比例需要 0–1。')
        background = background.resize(
            (int(photo.width * (1 + percent)), int(photo.height * (1 + percent))),
            Image.Resampling.BICUBIC,
        )
        if background.plan is not None:
            background = virtual(
                'white_blend', background.size, background.mode, parent=background, amount=blend
            )
        elif not background.shape_only:
            from PIL import Image as PILImage

            background.pil = PILImage.blend(
                background.pil, PILImage.new('RGB', background.pil.size, 'white'), blend
            )
            if background.rgb is not None:
                background.rgb = background.rgb * (1 - blend) + blend
        background.paste(
            photo,
            (int(photo.width * percent / 2), int(photo.height * percent / 2)),
            photo if photo.mode == 'RGBA' else None,
        )
        ctx.update_buffer([background])


class LegacyShadowFilter(FilterProcessor):
    def name(self):
        return 'legacy_shadow'

    def process(self, ctx):
        photo = ctx.get_buffer()[0]
        radius = ctx.getint('shadow_radius', max(photo.size) // 512)
        shadow = Image.new('RGB', photo.size, ctx.get('shadow_color', '#6B696A'))
        shadow = _pad(shadow, 2 * radius, 'tblr', ctx.get('background', 'white')).convert('RGB')
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius))
        shadow.paste(photo, (radius, radius), photo if photo.mode == 'RGBA' else None)
        ctx.update_buffer([shadow])
