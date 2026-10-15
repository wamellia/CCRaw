"""Editable semi-utils pipelines, safe resource handles and float photo composition."""

import base64
import copy
import io
import json
import math
import re
from functools import lru_cache

import numpy as np
import cv2
from jinja2 import TemplateError, nodes
from jinja2.sandbox import ImmutableSandboxedEnvironment
from PIL import Image as PILImage

from . import large_image
from .watermark_runtime import (
    ROOT,
    FONT_ROOT,
    RENDER,
    Raster,
    decoration,
    font_path,
    start_process,
    virtual,
    render_strips,
    render_virtual,
)

BRANDS = {
    'canon': 'Canon',
    'nikon': 'Nikon',
    'sony': 'Sony',
    'fujifilm': 'Fujifilm',
    'panasonic': 'Panasonic',
    'ricoh': 'Ricoh',
    'pentax': 'Pentax',
    'olympus': 'Olympus',
    'leica': 'Leica',
    'hasselblad': 'Hasselblad',
    'dji': 'DJI',
    'apple': 'Apple',
    'huawei': 'Huawei',
}
TEMPLATES = {
    'standard2': '标准水印2',
    'standard1': '标准水印',
    'nikon_blur': '尼康专用背景模糊',
    'blur': '背景模糊',
    'normal1': '右下角参数',
    'normal2': '文件夹名 + 时间',
    'center_logo': 'Logo 居中',
}
LAYOUT_TYPES = dict(
    TEMPLATES,
    **{
        'watermark_left_logo': '旧版 normal · Logo 居左',
        'watermark_right_logo': '旧版 normal · Logo 居右',
        'dark_watermark_left_logo': '旧版黑红 · Logo 居左',
        'dark_watermark_right_logo': '旧版黑红 · Logo 居右',
        'custom_watermark': '旧版自定义配置',
        'square': '旧版 1:1 填充',
        'simple': '旧版简洁',
        'background_blur': '旧版背景模糊',
        'background_blur_with_white_border': '旧版背景模糊 + 白框',
        'pure_white_margin': '旧版白色边框',
    },
)
TEXT_DEFAULTS = {
    'text': '',
    'font_path': 'AlibabaPuHuiTi-2-45-Light.otf',
    'height': 100,
    'is_bold': False,
    'color': 'black',
    'trim': False,
}
PROCESSOR_DEFAULTS = {
    'blur': {'blur_radius': 5},
    'resize': {'scale': 1, 'width': None, 'height': None},
    'trim': {key: True for key in ('trim_left', 'trim_right', 'trim_top', 'trim_bottom')},
    'margin': {
        'left_margin': 0,
        'right_margin': 0,
        'top_margin': 0,
        'bottom_margin': 0,
        'margin_color': 'white',
    },
    'margin_with_ratio': {
        'ratio': '{{exif.ImageWidth}}:{{exif.ImageHeight}}',
        'margin_color': 'white',
    },
    'watermark': {
        'color': 'white',
        'delimiter_color': 'black',
        'delimiter_width': '{{vw(0.3)}}',
        'left_margin': 0,
        'right_margin': 0,
        'top_margin': 0,
        'bottom_margin': None,
        'middle_spacing': None,
        'right_alignment': 'right',
        'center_logo_height': 0,
        'left_logo': '',
        'right_logo': '',
        'center_logo': '',
        **{
            key: {'processor_name': 'rich_text', 'text': ''}
            for key in ('left_top', 'left_bottom', 'right_top', 'right_bottom')
        },
    },
    'watermark_with_timestamp': {
        'height': '{{vh(2)}}',
        'text_segments': [dict(TEXT_DEFAULTS)],
        'text_spacing': 0,
        'text_alignment': 'bottom',
    },
    'rounded_corner': {'border_radius': 10},
    'shadow': {'shadow_color': 'black', 'shadow_radius': 30},
    'crop': {'width': 0, 'height': 0, 'offset': '[0,0]'},
    'solid_color': {'width': '{{vw(100)}}', 'height': '{{vh(100)}}', 'color': 'white'},
    'gradient_color': {
        'width': '{{vw(100)}}',
        'height': '{{vh(100)}}',
        'start_color': 'black',
        'end_color': 'white',
        'direction': 'horizontal',
        'interpolate_method': 'linear',
    },
    'rich_text': dict(TEXT_DEFAULTS),
    'multi_rich_text': {
        'height': 100,
        'text_alignment': 'bottom',
        'text_spacing': 0,
        'text_segments': [dict(TEXT_DEFAULTS)],
    },
    'image': {'path': 'source'},
    'alignment': {
        'horizontal_alignment': 'center',
        'vertical_alignment': 'center',
        'background': '#ffffff00',
        'offsets': '[]',
        'weights': '[]',
    },
    'concat': {
        'alignment': 'bottom',
        'direction': 'horizontal',
        'spacing': 10,
        'background': '#ffffff00',
    },
    'legacy_watermark': {
        'logo_position': 'left',
        'logo_enable': True,
        'logo': '{{auto_logo()}}',
        'background': 'white',
        'delimiter_color': '#CBCBC9',
        'font_size': 1,
        'bold_font_size': 1,
        'font_path': 'AlibabaPuHuiTi-2-45-Light.otf',
        'bold_font_path': 'AlibabaPuHuiTi-2-85-Bold.otf',
        **{
            key: {
                'text': expr,
                'color': '#212121' if key.endswith('top') else '#424242',
                'is_bold': key.endswith('top'),
            }
            for key, expr in {
                'left_top': '{{exif.LensModel}}',
                'left_bottom': '{{exif.CameraModelName}}',
                'right_top': '{{exif.Param}}',
                'right_bottom': '{{exif.Datetime}}',
            }.items()
        },
    },
    'legacy_simple': {
        'font_size': 1,
        'bold_font_size': 1,
        'font_path': 'Roboto-Regular.ttf',
        'bold_font_path': 'Roboto-Medium.ttf',
        'model_color': '#D32F2F',
        'color': '#212121',
        'param_color': '#9E9E9E',
        'background': 'white',
    },
    'legacy_background': {
        'blur_radius': 35,
        'padding_percent': 0.18,
        'white_blend': 0.1,
        'white_margin': 0,
    },
    'legacy_shadow': {
        'shadow_radius': '{{[vw(100),vh(100)]|max / 512}}',
        'shadow_color': '#6B696A',
        'background': 'white',
    },
}


def identify_brand(photo, override='auto'):
    if override != 'auto':
        return override if override in BRANDS else ''
    identity = str(photo.get('brand') or photo.get('body') or photo.get('Make') or '').lower()
    for alias, key in [
        *((k, k) for k in BRANDS),
        ('lumix', 'panasonic'),
        ('om digital', 'olympus'),
        ('om system', 'olympus'),
    ]:
        if re.search(r'(?<![a-z])' + re.escape(alias) + r'(?![a-z])', identity):
            return key
    return ''


@lru_cache(maxsize=16)
def brand_logo(brand):
    if brand not in BRANDS:
        return None
    with PILImage.open(ROOT / f'watermark-brand-{brand}.png') as image:
        return image.convert('RGBA')


@lru_cache(maxsize=16)
def logo(data):
    try:
        raw = base64.b64decode(data, validate=True)
        with PILImage.open(io.BytesIO(raw)) as image:
            if image.format != 'PNG' or max(image.size) > 2048:
                raise ValueError('标志需要不超过 2048px 的 PNG。')
            return image.convert('RGBA')
    except (OSError, TypeError, base64.binascii.Error) as error:
        raise ValueError('标志需要不超过 2048px 的 PNG。') from error


def template_pipeline(key):
    if key in TEMPLATES:
        return json.loads((ROOT / f'watermark-template-{key}.json').read_text(encoding='utf-8'))
    if key not in LAYOUT_TYPES:
        raise ValueError('未知水印模板。')
    if key == 'square':
        return [{'processor_name': 'margin_with_ratio', 'ratio': '1:1'}]
    if key == 'pure_white_margin':
        return [
            {
                'processor_name': 'margin',
                **{
                    k: '{{[vh(3),vw(3)]|min}}'
                    for k in ('left_margin', 'right_margin', 'top_margin', 'bottom_margin')
                },
                'margin_color': 'white',
            }
        ]
    if key.startswith('background_blur'):
        return [
            {
                'processor_name': 'legacy_background',
                **copy.deepcopy(PROCESSOR_DEFAULTS['legacy_background']),
                'white_margin': '{{[vh(100),vw(100)]|min * 3 / 256}}'
                if key.endswith('white_border')
                else 0,
            }
        ]
    if key == 'simple':
        return [
            {
                'processor_name': 'legacy_simple',
                **copy.deepcopy(PROCESSOR_DEFAULTS['legacy_simple']),
            }
        ]
    node = {
        'processor_name': 'legacy_watermark',
        **copy.deepcopy(PROCESSOR_DEFAULTS['legacy_watermark']),
    }
    node['logo_position'] = 'right' if 'right' in key else 'left'
    if key.startswith('dark_'):
        node['background'] = '#212121'
        for pos in ('left_top', 'right_top'):
            node[pos]['color'] = '#D32F2F'
        for pos in ('left_bottom', 'right_bottom'):
            node[pos]['color'] = '#d4d1cc'
    if key == 'custom_watermark':
        node['logo_enable'] = False
        node['left_bottom']['color'] = node['right_bottom']['color'] = '#757575'
    return [
        node,
        {
            'processor_name': 'margin',
            'margin_color': node['background'],
            **{k: '{{[vh(3),vw(3)]|min}}' for k in ('top_margin', 'left_margin', 'right_margin')},
        },
    ]


def defaults():
    return {
        'enabled': False,
        'template': 'standard2',
        'pipeline': template_pipeline('standard2'),
        'brand': 'auto',
        'logos': {'left': '', 'right': '', 'center': ''},
        'metadata': {},
        'equivalent_focal': True,
    }


_NUMERIC = {
    'width',
    'height',
    'scale',
    'blur_radius',
    'left_margin',
    'right_margin',
    'top_margin',
    'bottom_margin',
    'delimiter_width',
    'middle_spacing',
    'center_logo_height',
    'center_height',
    'border_radius',
    'shadow_radius',
    'text_spacing',
    'spacing',
    'font_size',
    'bold_font_size',
    'padding_percent',
    'white_blend',
    'white_margin',
}
_PIXELS = _NUMERIC - {'scale', 'font_size', 'bold_font_size', 'padding_percent', 'white_blend'}
_HANDLES = {'source', 'logo:left', 'logo:right', 'logo:center', ''} | {
    p.name for p in ROOT.glob('watermark-brand-*.png')
}
RESOURCE_HANDLES = tuple(sorted(_HANDLES - {''}))
FONTS = tuple(
    sorted(p.name for p in FONT_ROOT.iterdir() if p.suffix.lower() in ('.ttf', '.otf'))
) + ('SpecialAlphabets P04 Regular.otf',)


class _TemplateEnvironment(ImmutableSandboxedEnvironment):
    intercepted_binops = frozenset({'*', '**'})

    def call_binop(self, context, operator, left, right):
        if (
            operator == '**'
            or not isinstance(left, (int, float))
            or not isinstance(right, (int, float))
        ):
            raise ValueError('水印表达式只允许数值乘法。')
        return left * right

    def call(self, context, obj, *args, **kwargs):
        if (
            isinstance(getattr(obj, '__self__', None), str)
            and getattr(obj, '__name__', '') == 'replace'
        ):
            text = obj.__self__
            old = args[0] if args else kwargs.get('old', '')
            new = args[1] if len(args) > 1 else kwargs.get('new', '')
            count = args[2] if len(args) > 2 else kwargs.get('count', -1)
            _replacement_size(text, old, new, count)
        result = super().call(context, obj, *args, **kwargs)
        if isinstance(result, str) and len(result) > 20000:
            raise ValueError('水印表达式结果过长。')
        return result

    def is_safe_callable(self, obj):
        return getattr(obj, '_watermark_function', False) or (
            isinstance(getattr(obj, '__self__', None), str)
            and getattr(obj, '__name__', '')
            in {'replace', 'split', 'partition', 'strip', 'lower', 'upper'}
        )

    def is_safe_attribute(self, obj, attr, value):
        return isinstance(obj, str) and attr in {
            'replace',
            'split',
            'partition',
            'strip',
            'lower',
            'upper',
        }


_ENV = _TemplateEnvironment()
_ENV.globals.clear()
_ENV.filters = {
    k: v
    for k, v in _ENV.filters.items()
    if k
    in {
        'default',
        'replace',
        'min',
        'max',
        'round',
        'lower',
        'upper',
        'trim',
        'join',
        'int',
        'float',
        'length',
        'string',
    }
}


def _replacement_size(text, old, new, count=-1):
    occurrences = str(text).count(str(old))
    if count is not None and int(count) >= 0:
        occurrences = min(occurrences, int(count))
    if len(str(text)) + occurrences * (len(str(new)) - len(str(old))) > 20000:
        raise ValueError('水印表达式结果过长。')


def _replace_filter(text, old, new, count=None):
    text, old, new = map(str, (text, old, new))
    count = -1 if count is None else int(count)
    _replacement_size(text, old, new, count)
    return text.replace(old, new, count)


def _join_filter(items, delimiter='', attribute=None):
    items = list(items)
    if len(items) > 20000:
        raise ValueError('水印表达式结果过长。')
    values = [
        str(item.get(attribute, '') if isinstance(item, dict) and attribute is not None else item)
        for item in items
    ]
    delimiter = str(delimiter)
    if sum(map(len, values)) + max(0, len(values) - 1) * len(delimiter) > 20000:
        raise ValueError('水印表达式结果过长。')
    return delimiter.join(values)


_ENV.filters['replace'] = _replace_filter
_ENV.filters['join'] = _join_filter


@lru_cache(maxsize=256)
def _compiled_template(text):
    return _ENV.from_string(text)


def _check_tree(value, depth=0):
    if depth > 12:
        raise ValueError('水印管线嵌套过深。')
    if isinstance(value, dict):
        if 'processor_name' in value and value['processor_name'] not in PROCESSOR_DEFAULTS:
            raise ValueError('未知水印处理器。')
        if 'processor_name' in value:
            allowed = set(PROCESSOR_DEFAULTS[value['processor_name']]) | {
                'processor_name',
                'select',
                'buffer_path',
                'save_buffer',
            }
            if value['processor_name'] == 'watermark':
                allowed.add('center_height')  # inert upstream spelling
            if set(value) - allowed:
                raise ValueError('未知水印处理器属性。')
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 100:
                raise ValueError('水印属性无效。')
            if (
                key in _NUMERIC
                and item is not None
                and not (isinstance(item, str) and '{{' in item)
            ):
                try:
                    if isinstance(item, bool) or not math.isfinite(float(item)) or float(item) < 0:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise ValueError('水印尺寸必须为有限的非负数。')
            if key in {'path', 'left_logo', 'right_logo', 'center_logo', 'logo'}:
                handles = item if key == 'path' and isinstance(item, list) else [item]
                if any(
                    not isinstance(v, str) or '{{' not in v and v not in _HANDLES for v in handles
                ):
                    raise ValueError('水印图像必须来自安全资源句柄。')
            if key == 'buffer_path':
                if not isinstance(item, list) or any(
                    not isinstance(v, str) or '{{' not in v and v not in _HANDLES for v in item
                ):
                    raise ValueError('管线输入必须来自安全资源句柄。')
            if key in {'font_path', 'bold_font_path'} and item:
                if '{{' in str(item) or font_path(item).name != item:
                    raise ValueError('字体必须选择内置字体。')
            if (
                key
                in {
                    'is_bold',
                    'trim',
                    'trim_left',
                    'trim_right',
                    'trim_top',
                    'trim_bottom',
                    'logo_enable',
                    'save_buffer',
                }
                and type(item) is not bool
            ):
                raise ValueError('水印开关需要布尔值。')
            if key in {'font_size', 'bold_font_size'} and item not in (1, 2, 3):
                raise ValueError('旧版字体等级需要 1、2 或 3。')
            if key == 'ratio' and not (isinstance(item, str) and '{{' in item):
                if not isinstance(item, str) or not re.fullmatch(r'[0-9.]+:[0-9.]+', item):
                    raise ValueError('填充比例需要正数 W:H。')
                try:
                    ratio_w, ratio_h = map(float, item.split(':'))
                    if (
                        not math.isfinite(ratio_w)
                        or not math.isfinite(ratio_h)
                        or ratio_w <= 0
                        or ratio_h <= 0
                    ):
                        raise ValueError()
                except ValueError:
                    raise ValueError('填充比例需要正数 W:H。')
            if (
                key == 'white_blend'
                and not (isinstance(item, str) and '{{' in item)
                and not 0 <= float(item) <= 1
            ):
                raise ValueError('白色混合比例需要 0–1。')
            if key in {'select', 'offset', 'offsets', 'weights'} and not (
                isinstance(item, str) and '{{' in item
            ):
                try:
                    array = json.loads(item) if isinstance(item, str) else item
                    if not isinstance(array, list):
                        raise ValueError()
                    if key == 'select' and any(type(i) is not int or i < 0 for i in array):
                        raise ValueError()
                    if key == 'offset' and (
                        len(array) > 2
                        or any(
                            isinstance(i, bool)
                            or not isinstance(i, (float, int))
                            or not math.isfinite(i)
                            for i in array
                        )
                    ):
                        raise ValueError()
                    if key == 'offsets' and any(
                        not isinstance(pair, list)
                        or len(pair) != 2
                        or any(
                            isinstance(i, bool)
                            or not isinstance(i, (float, int))
                            or not math.isfinite(i)
                            for i in pair
                        )
                        for pair in array
                    ):
                        raise ValueError()
                    if key == 'weights' and any(
                        isinstance(i, bool)
                        or not isinstance(i, (float, int))
                        or not math.isfinite(i)
                        for i in array
                    ):
                        raise ValueError()
                except (ValueError, TypeError):
                    raise ValueError('水印索引或偏移数组无效。')
            _check_tree(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 100:
            raise ValueError('水印列表过长。')
        for item in value:
            _check_tree(item, depth + 1)
    elif isinstance(value, str):
        if len(value) > 20000 or '__' in value or '{%' in value:
            raise ValueError('水印表达式无效或过长。')
        if '{{' in value:
            tree = _ENV.parse(value)
            if list(tree.find_all(nodes.Pow)):
                raise ValueError('水印表达式不支持幂运算。')
            for node in tree.find_all(nodes.Mul):
                if any(
                    isinstance(n, nodes.List)
                    or isinstance(n, nodes.Const)
                    and isinstance(n.value, str)
                    for n in (node.left, node.right)
                ):
                    raise ValueError('水印表达式不能重复文本或列表。')
    elif value is not None and not isinstance(value, (int, float, bool)):
        raise ValueError('水印属性类型无效。')
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('水印属性必须为有限数。')


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('水印设置无效。')
    if set(data) - {
        'enabled',
        'template',
        'pipeline',
        'brand',
        'logos',
        'metadata',
        'equivalent_focal',
        'layout',
        'smart_brand',
        'size',
        'sides',
        'preset',
        'fields',
        'camera_logo',
        'lens_logo',
    }:
        raise ValueError('未知水印设置属性。')
    if any(key in data and type(data[key]) is not bool for key in ('enabled', 'equivalent_focal')):
        raise ValueError('水印开关需要布尔值。')
    if 'size' in data and (
        isinstance(data['size'], bool)
        or not isinstance(data['size'], (int, float))
        or not math.isfinite(data['size'])
        or not 8 <= data['size'] <= 35
    ):
        raise ValueError('旧版边框宽度无效。')
    if 'sides' in data and (
        not isinstance(data['sides'], list)
        or any(v not in ('top', 'bottom', 'left', 'right') for v in data['sides'])
    ):
        raise ValueError('旧版水印位置无效。')
    key = data.get('template')
    if key is None:
        old = data.get('layout', 'classic')
        if old in ('classic', 'smart'):
            key = 'standard2'
        elif old in LAYOUT_TYPES:
            key = old
        else:
            raise ValueError('未知水印模板。')
    if key not in LAYOUT_TYPES:
        raise ValueError('未知水印模板。')
    result = {
        'enabled': bool(data.get('enabled', False)),
        'template': key,
        'pipeline': copy.deepcopy(data.get('pipeline', template_pipeline(key))),
        'brand': data.get('brand', data.get('smart_brand', 'auto')),
        'logos': copy.deepcopy(
            data.get(
                'logos',
                {
                    'left': data.get(
                        'lens_logo' if data.get('layout') == 'smart' else 'camera_logo', ''
                    ),
                    'right': data.get(
                        'camera_logo' if data.get('layout') == 'smart' else 'lens_logo', ''
                    ),
                    'center': '',
                },
            )
        ),
        'metadata': copy.deepcopy(data.get('metadata', {})),
        'equivalent_focal': bool(data.get('equivalent_focal', True)),
    }
    if result['brand'] not in ('auto', *BRANDS):
        raise ValueError('未知相机品牌。')
    if (
        not isinstance(result['pipeline'], list)
        or not 1 <= len(result['pipeline']) <= 64
        or any(not isinstance(n, dict) or 'processor_name' not in n for n in result['pipeline'])
    ):
        raise ValueError('水印管线需要 1–64 个处理器。')
    if not isinstance(result['logos'], dict) or set(result['logos']) - {'left', 'right', 'center'}:
        raise ValueError('标志位置无效。')
    for slot in ('left', 'right', 'center'):
        value = result['logos'].setdefault(slot, '')
        if not isinstance(value, str) or len(value) > 3_000_000:
            raise ValueError('标志文件过大。')
        if value:
            logo(value)
    if not isinstance(result['metadata'], dict) or len(result['metadata']) > 100:
        raise ValueError('元数据覆盖无效。')
    try:
        _check_tree(result['pipeline'])
        _check_tree(result['metadata'])
    except TemplateError as error:
        raise ValueError('水印表达式无效。') from error
    return result


def template_context(settings, photo, shape):
    """Map CCRaw metadata to current EXIF and legacy attribute names."""
    h, w = map(int, shape[:2])
    original = photo or {}
    overrides = settings.get('metadata', {})
    aliases = {
        'CameraModelName': 'body',
        'Model': 'body',
        'CameraMake': 'brand',
        'Make': 'brand',
        'LensModel': 'lens',
        'LensMake': 'lens_brand',
        'FNumber': 'aperture',
        'AperatureValue': 'aperture',
        'ShutterSpeed': 'shutter',
        'ShutterSpeedValue': 'shutter',
        'ExposureTime': 'shutter',
        'ISO': 'iso',
        'FocalLength': 'focal',
        'FocalLengthIn35mmFormat': 'focal_equivalent',
        'FocalLengthIn35mmFilm': 'focal_equivalent',
        'DateTimeOriginal': 'date',
        'CreateDate': 'date',
        'Datetime': 'date',
        'Filename': 'filename',
    }
    photo = dict(original)
    for alias, target in aliases.items():
        if alias in original and target not in original:
            photo[target] = original[alias]
        if alias in overrides and target not in overrides:
            photo[target] = overrides[alias]
    photo.update(overrides)
    for key in ('focal', 'focal_equivalent'):
        if key in photo and re.fullmatch(r'\d+(?:\.\d+)?', str(photo[key]).strip()):
            photo[key] = str(photo[key]).strip() + ' mm'

    def get(key):
        return str(photo.get(key) or '')[:2000]

    focal = (
        get('focal_equivalent')
        if settings.get('equivalent_focal', True) and get('focal_equivalent')
        else get('focal')
    )
    aperture = re.sub(r'^f\s*/\s*', '', get('aperture'), flags=re.I)
    shutter = re.sub(r'\s*s$', '', get('shutter')).replace(' ', '')
    iso = re.sub(r'^ISO\s*', '', get('iso'), flags=re.I)
    param = ' '.join(
        v
        for v in (
            focal.replace(' ', ''),
            ('f/' + aperture) if aperture else '',
            (shutter + 's') if shutter else '',
            ('ISO' + iso) if iso else '',
        )
        if v
    )
    exif = {
        'CameraModelName': get('body'),
        'Model': get('body'),
        'LensModel': get('lens'),
        'Make': get('brand'),
        'CameraMake': get('brand'),
        'LensMake': get('lens_brand'),
        'FocalLengthIn35mmFormat': focal,
        'FocalLength': get('focal'),
        'FNumber': aperture,
        'AperatureValue': aperture,
        'ShutterSpeed': shutter,
        'ShutterSpeedValue': shutter,
        'ISO': iso,
        'DateTimeOriginal': get('date'),
        'CreateDate': get('date'),
        'DigitalCreationDate': '',
        'DateCreated': '',
        'DateTimeCreated': '',
        'DigitalCreationDateTime': '',
        'FileInodeChangeDateTime': '',
        'FileAccessDateTime': '',
        'ImageWidth': w,
        'ImageHeight': h,
        'Param': param,
        'Datetime': get('date')[:19],
        'Date': get('date')[:10],
        'Filename': get('filename'),
        'GeoInfo': get('GeoInfo'),
        'TotalPixel': f'{w * h / 1_000_000:.2f} MP',
        'None': '',
        'Custom': get('Custom'),
    }
    for key, parts in {
        'LensMake_LensModel': ('lens_brand', 'lens'),
        'CameraModel_LensModel': ('body', 'lens'),
        'CameraMake_CameraModel': ('brand', 'body'),
    }.items():
        exif[key] = ' '.join(get(k) for k in parts if get(k))
    exif['Date_Filename'] = ' '.join(v for v in (get('date')[:10], get('filename')) if v)
    exif['Datetime_Filename'] = ' '.join(v for v in (get('date')[:19], get('filename')) if v)
    for key, value in photo.items():
        if key not in {
            *aliases,
            'body',
            'lens',
            'aperture',
            'shutter',
            'iso',
            'focal',
            'focal_equivalent',
            'brand',
            'lens_brand',
            'date',
            'filename',
            'folder',
        } and isinstance(value, (str, int, float)):
            exif[key] = str(value)[:2000]
    brand = identify_brand(photo, settings['brand'])

    def vh(percent):
        return int(h * float(percent) / 100)

    def vw(percent):
        return int(w * float(percent) / 100)

    def auto_logo():
        return f'watermark-brand-{brand}.png' if brand else ''

    for func in (vh, vw, auto_logo):
        func._watermark_function = True
    return {
        'exif': exif,
        'file_path': 'source',
        'file_dir': get('folder'),
        'vh': vh,
        'vw': vw,
        'auto_logo': auto_logo,
        **{
            f'{slot}_logo': f'logo:{slot}' if settings['logos'][slot] else ''
            for slot in ('left', 'right', 'center')
        },
    }


def _evaluate(value, context, scale=1, key=''):
    if isinstance(value, dict):
        return {k: _evaluate(v, context, scale, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_evaluate(v, context, scale, key) for v in value]
    if isinstance(value, str) and '{{' in value:
        value = _compiled_template(value).render(**context)
        if len(value) > 20000:
            raise ValueError('水印表达式结果过长。')
    if key in _NUMERIC and value is not None:
        try:
            number = float(value)
            if not math.isfinite(number) or number < 0:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise ValueError('水印尺寸必须为有限的非负数。')
        if key in _PIXELS:
            number *= scale
            return (
                max(1, int(number))
                if key in {'width', 'height', 'delimiter_width'} and number > 0
                else int(number)
            )
        return number
    if key in {'offset', 'offsets'} and scale != 1:
        array = json.loads(value) if isinstance(value, str) else value

        def scaled(item):
            return [scaled(v) for v in item] if isinstance(item, list) else int(float(item) * scale)

        return json.dumps(scaled(array))
    return value


def _render(
    rgb,
    settings,
    photo,
    *,
    shape=None,
    scale=1,
    color_space='sRGB',
    geometry=False,
    preview_size=None,
):
    shape = shape or rgb.shape
    h, w = map(int, shape[:2])
    if geometry:
        source = Raster(PILImage.new('RGB', (1, 1)), size=(w, h), shape_only=True)
    elif preview_size is not None:
        source = virtual('source', (w, h), 'RGB', pixels=np.asarray(rgb, dtype=np.float32))
    else:
        values = np.asarray(rgb, dtype=np.float32)
        if values.ndim != 3 or values.shape[2] != 3 or not large_image.finite(values):
            raise ValueError('照片像素无效。')
        source = Raster(
            PILImage.fromarray(np.round(np.clip(values, 0, 1) * 255).astype(np.uint8), 'RGB'),
            values,
        )
        source.owns_rgb = False
    images = {f'logo:{slot}': logo(data) for slot, data in settings['logos'].items() if data}
    token = RENDER.set(
        {
            'source': source,
            'images': images,
            'color_space': color_space,
            'geometry_only': geometry,
            'preview_virtual': preview_size is not None,
        }
    )
    try:
        context = template_context(settings, photo, shape)
        pipeline = copy.deepcopy(settings['pipeline'])

        def inject(node):
            if isinstance(node, dict):
                if node.get('processor_name') == 'watermark':
                    for slot in ('left', 'right', 'center'):
                        field = f'{slot}_logo'
                        automatic = field not in node or 'auto_logo' in str(node.get(field, ''))
                        if settings['logos'][slot] and automatic:
                            node[f'{slot}_logo'] = f'logo:{slot}'
                elif node.get('processor_name') == 'legacy_watermark':
                    slot = node.get('logo_position', 'left')
                    automatic = 'logo' not in node or 'auto_logo' in str(node.get('logo', ''))
                    if settings['logos'].get(slot) and automatic:
                        node['logo'] = f'logo:{slot}'
                for item in node.values():
                    inject(item)
            elif isinstance(node, list):
                for item in node:
                    inject(item)

        inject(pipeline)
        pipeline = _evaluate(pipeline, context, scale)

        def bind_context(node):
            if isinstance(node, dict):
                if 'processor_name' in node:
                    node['exif'] = context['exif']
                for key, value in node.items():
                    if key != 'exif':
                        bind_context(value)
            elif isinstance(node, list):
                for value in node:
                    bind_context(value)

        bind_context(pipeline)
        output = start_process(pipeline, initial_buffer=[source])
        if geometry:
            return output.size
        if preview_size is not None:
            if w * h > 2_000_000 and tuple(rgb.shape[:2]) != tuple(shape[:2]):
                # Large-photo live previews use a bounded supersampled proxy
                # grid. Typography retains its physical raster and coordinates;
                # photo blur/rounded masks are filtered on the proxy grid.
                sample = (preview_size[0] * 2, preview_size[1] * 2)
                values = render_virtual(output, sample)[:, :, :3]
                return cv2.resize(values, preview_size, interpolation=cv2.INTER_AREA)
            return render_strips(output, preview_size)
        return output.rgb if output.rgb is not None else decoration(output.pil)
    except (TemplateError, KeyError, TypeError, OverflowError, IndexError) as error:
        raise ValueError(f'水印管线无效：{error}') from error
    finally:
        RENDER.reset(token)


def dimensions(shape, settings, photo=None):
    settings = validate(settings)
    h, w = map(int, shape[:2])
    large_image.validate_size((h, w))
    if not settings['enabled']:
        return w, h
    return _render(None, settings, photo or {}, shape=shape, geometry=True)


def apply(rgb, settings, photo, *, color_space='sRGB'):
    settings = validate(settings)
    if not settings['enabled']:
        return rgb
    dimensions(rgb.shape, settings, photo)
    return _render(rgb, settings, photo, color_space=color_space)


def preview(rgb, settings, photo, limit=750, final_shape=None):
    settings = validate(settings)
    if not settings['enabled']:
        from .engine import resize_limit

        return resize_limit(rgb, limit)
    shape = final_shape or rgb.shape
    size = dimensions(shape, settings, photo)
    if not isinstance(limit, (int, float)) or not math.isfinite(limit) or limit <= 0:
        raise ValueError('预览尺寸必须为正数。')
    scale = min(1, float(limit) / max(size))
    target = (max(1, round(size[0] * scale)), max(1, round(size[1] * scale)))
    return _render(rgb, settings, photo, shape=shape, preview_size=target)
