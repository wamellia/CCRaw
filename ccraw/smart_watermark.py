"""EXIF footer layout inspired by semi-utils standard1, using CCRaw's export engine.

Brand images originate from leslievan/semi-utils (Apache-2.0); see THIRD_PARTY.md.
The layout and float-preserving integration are implemented for CCRaw.
"""

from functools import lru_cache
from pathlib import Path
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import resources
from .photo_metadata import clean

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
LAYOUTS = {'classic': '普通边框', 'smart': '智能参数'}


def identify_brand(photo, override='auto'):
    if override != 'auto':
        return override if override in BRANDS else ''
    brand = str(photo.get('brand') or '').lower()
    if not brand:
        brand = str(photo.get('body') or '').lower()
    aliases = [('lumix', 'panasonic'), ('om digital', 'olympus'), ('om system', 'olympus')]
    for alias, key in [*((k, k) for k in BRANDS), *aliases]:
        if re.search(r'(?<![a-z])' + re.escape(alias) + r'(?![a-z])', brand):
            return key
    return ''


@lru_cache(maxsize=16)
def brand_logo(brand):
    if brand not in BRANDS:
        return None
    path = Path(__file__).resolve().parent / 'resources' / f'watermark-brand-{brand}.png'
    if not path.is_file():
        return None
    with Image.open(path) as image:
        return image.convert('RGBA')


def metadata_blocks(settings, photo):
    if isinstance(photo, dict):
        photo = {key: value if value is not None else '' for key, value in photo.items()}
    photo = clean(photo)
    fields = settings['fields']
    left = [photo[key] for key in ('body', 'lens') if key in fields and photo.get(key)]
    if not left and 'brand' in fields and photo.get('brand'):
        left.append(photo['brand'])
    technical = ' · '.join(
        photo[key]
        for key in ('focal', 'aperture', 'shutter', 'iso')
        if key in fields and photo.get(key)
    )
    right = [technical] if technical else []
    if 'date' in fields and photo.get('date'):
        right.append(photo['date'])
    return left, right


@lru_cache(maxsize=64)
def font(size):
    value = ImageFont.truetype(str(resources.asset_path('NotoSansSC.ttf')), size)
    try:
        value.set_variation_by_axes([480])
    except (OSError, AttributeError):
        pass
    return value


def text_block(draw, rows, rectangle, foreground):
    if not rows:
        return
    x, y, width, height = rectangle
    size = max(2, round(height / max(2.8, len(rows) * 1.45)))
    minimum = max(2, round(size * 0.55))
    while size > minimum and any(draw.textlength(row, font=font(size)) > width for row in rows):
        size -= 1
    face = font(size)
    step = size * 1.5
    for index, original in enumerate(rows):
        text = original
        if draw.textlength(text, font=face) > width:
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if draw.textlength(text[:middle] + '…', font=face) <= width:
                    low = middle
                else:
                    high = middle - 1
            text = text[:low] + '…' if low else ''
        baseline = y + height / 2 + (index - (len(rows) - 1) / 2) * step
        draw.text((x, baseline), text, font=face, fill=foreground, anchor='lm')


def band(length, thickness, settings, photo):
    from .watermark import PRESETS, logo

    _, background, foreground = PRESETS[settings['preset']]
    strip = Image.new('RGB', (length, thickness), background)
    draw = ImageDraw.Draw(strip)
    left, right = metadata_blocks(settings, photo)
    emblems = []
    if settings['camera_logo']:
        emblems.append(logo(settings['camera_logo']).copy())
    elif 'brand' in settings['fields']:
        emblem = brand_logo(identify_brand(photo, settings['smart_brand']))
        if emblem is not None:
            emblems.append(emblem.copy())
    if settings['lens_logo']:
        emblems.append(logo(settings['lens_logo']).copy())
    margin = max(2, round(min(thickness * 0.18, length * 0.025)))
    x = margin
    for emblem in emblems:
        emblem.thumbnail(
            (max(1, length // (8 if len(emblems) > 1 else 5)), max(1, thickness - 2 * margin)),
            Image.Resampling.LANCZOS,
        )
        # Keep black wordmarks legible on dark frames while preserving brand colours.
        pixels = np.array(emblem)
        neutral = (
            pixels[:, :, :3].max(axis=2).astype(int) - pixels[:, :, :3].min(axis=2).astype(int)
        ) < 15
        if sum(background) < 300:
            neutral &= pixels[:, :, :3].max(axis=2) < 120
            pixels[neutral, :3] = foreground
            emblem = Image.fromarray(pixels)
        strip.paste(emblem, (x, (thickness - emblem.height) // 2), emblem)
        x += emblem.width + margin
    available = max(1, length - margin - x)
    if left and right and available >= thickness * 3:
        left_width = round((available - 2 * margin) * 0.5)
        divider = x + left_width + margin
        draw.line((divider, margin, divider, thickness - margin), fill=foreground, width=1)
        text_block(draw, left, (x, margin, left_width, thickness - 2 * margin), foreground)
        text_block(
            draw,
            right,
            (
                divider + margin,
                margin,
                max(1, length - divider - 2 * margin),
                thickness - 2 * margin,
            ),
            foreground,
        )
    else:
        text_block(draw, left + right, (x, margin, available, thickness - 2 * margin), foreground)
    return np.asarray(strip, dtype=np.float32) / 255
