"""Float32 linear-light RAW pipeline, masks and optional GPU (DirectML / CUDA) processing."""

from __future__ import annotations
import os
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageCms, ImageOps
from .. import white_balance, develop, dng, photo_metadata
from .. import large_image

from .. import engine as pipeline


def load_image(path, preview_limit=1600, develop_reference=False):
    """RAW remains linear. Export decodes again at full resolution.

    The camera develop reference is derived for previews, or on request for a full decode
    (batch export of photos that were never opened)."""
    path = Path(path)
    info = dict(name=path.name, format=path.suffix[1:].upper(), path=str(path.resolve()))
    if path.suffix.lower() == '.dng' and dng.is_rendered(path):
        width, height = dng.dimensions(path)
        rgb = dng.read(path, preview_limit)
        info.update(
            width=width, height=height, raw=False, note='CCRaw 16-bit 线性 DNG · 已应用编辑'
        )
        info['photo'] = photo_metadata.embedded(path)
    elif path.suffix.lower() in pipeline.RAW_EXTENSIONS:
        import rawpy

        with rawpy.imread(str(path)) as raw:
            try:
                raw.unpack()
            except rawpy.LibRawFileUnsupportedError:
                return pipeline.load_embedded(path, preview_limit, info)
            info['width'], info['height'] = (
                (raw.sizes.height, raw.sizes.width)
                if raw.sizes.flip in (5, 6)
                else (raw.sizes.width, raw.sizes.height)
            )
            info['raw'] = True
            tags, warning = white_balance.metadata(path)
            info['white_balance'] = white_balance.from_metadata(tags, white_balance.estimate(raw))
            info['camera_model'] = tags.get('Model', '')
            info['photo'] = photo_metadata.from_tags(tags)
            info['wb_warning'] = warning
            info['camera_wb_gains'] = list(raw.camera_whitebalance)
            rgb = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=True,
                output_bps=16,
                gamma=(1, 1),
                output_color=rawpy.ColorSpace.sRGB,
                half_size=bool(preview_limit),
                user_flip=None,
                highlight_mode=rawpy.HighlightMode.Blend,
            ).astype(np.float32)
            rgb /= 65535
            if preview_limit or develop_reference:
                curve, label = develop.camera_curve(raw, rgb, path)
                info['develop'] = dict(mode='camera', curve=curve, source=label)
        info['note'] = 'LibRaw · 相机白平衡 · 线性 sRGB · 16-bit 解码'
    elif path.suffix.lower() in {'.tif', '.tiff', '.png'}:
        arr = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if arr is None:
            raise ValueError('无法解码这张图片。')
        if arr.dtype == np.uint16:
            if arr.ndim == 2:
                arr = np.repeat(arr[..., None], 3, axis=2)
            rgb = pipeline.to_linear(arr[..., :3][..., ::-1].astype(np.float32) / 65535)
            info.update(
                width=rgb.shape[1],
                height=rgb.shape[0],
                raw=False,
                note='16-bit 输入按 sRGB 解读（建议先转换非 sRGB 文件）',
            )
            info['photo'] = photo_metadata.embedded(path) or photo_metadata.from_tags(
                white_balance.metadata(path)[0]
            )
        else:
            return pipeline.load_pillow(path, preview_limit)
    else:
        return pipeline.load_pillow(path, preview_limit)
    large_image.validate_size((info['height'], info['width']))
    return (np.ascontiguousarray(pipeline.resize_limit(rgb, preview_limit)), info)


def load_embedded(path, limit, info):
    """Sensor data LibRaw cannot decode (e.g. Nikon "High Efficiency" NEF): open the camera's
    full-size embedded JPEG instead, as an 8-bit sRGB photograph."""
    import rawpy
    from .. import previews

    with rawpy.imread(str(path)) as raw:
        preview = previews.libraw_preview(raw)
        width, height = (raw.sizes.width, raw.sizes.height)
    if preview is None or preview.shape[0] * preview.shape[1] < 0.5 * width * height:
        size = f'{preview.shape[1]} × {preview.shape[0]}' if preview is not None else '无'
        raise ValueError(
            f'内置 LibRaw 无法解码这个文件的传感器数据（例如尼康“高效率”压缩），文件内嵌的预览（{size}）也不足以编辑。请在相机中改用无损压缩 RAW，或先用厂商软件转换为 DNG / TIFF。'
        )
    tags, warning = white_balance.metadata(path)
    info.update(
        width=preview.shape[1],
        height=preview.shape[0],
        raw=False,
        embedded=True,
        format=info['format'] + ' · 内嵌 JPEG',
        camera_model=tags.get('Model', ''),
        wb_warning=warning,
        note='内置 LibRaw 不支持这种 RAW 压缩（如尼康“高效率”），已改用相机内嵌的全尺寸 JPEG：8 位 sRGB，相机白平衡与风格已应用',
    )
    info['photo'] = photo_metadata.from_tags(tags)
    large_image.validate_size(preview.shape)
    image = Image.fromarray(np.ascontiguousarray(preview))
    if limit:
        image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
    rgb = np.asarray(image).astype(np.float32)
    rgb /= 255
    return (pipeline.to_linear(rgb), info)


def load_pillow(path, limit):
    with Image.open(path) as src:
        large_image.validate_size((src.height, src.width))
        img = ImageOps.exif_transpose(src)
        profile = src.info.get('icc_profile')
        if profile:
            import io

            img = ImageCms.profileToProfile(
                img.convert('RGB'),
                ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                ImageCms.createProfile('sRGB'),
                outputMode='RGB',
            )
        else:
            img = img.convert('RGB')
        info = dict(
            name=path.name,
            width=img.width,
            height=img.height,
            raw=False,
            format=path.suffix[1:].upper(),
            path=str(path.resolve()),
            note='sRGB · EXIF 方向已应用',
        )
        info['photo'] = photo_metadata.from_tags(white_balance.metadata(path)[0])
        if limit:
            img.thumbnail((limit, limit), Image.Resampling.LANCZOS)
        return (pipeline.to_linear(np.asarray(img).astype(np.float32) / 255), info)


def export_image(path, rgb, quality=95, photo=None, provenance=None):
    """Atomic export. All outputs are encoded sRGB; TIFF preserves 16-bit precision."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.dng'}:
        raise ValueError('请选择 JPEG、PNG、TIFF 或 DNG 格式。')
    large_image.validate_size(rgb.shape)
    if not large_image.finite(rgb):
        raise ValueError('图像包含无效像素。')
    temp = path.with_name(path.stem + '.ccraw-tmp' + ext)
    try:
        if ext == '.dng':
            dng.write(temp, rgb, photo, provenance)
        elif ext in {'.tif', '.tiff'}:
            import tifffile

            profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
            tifffile.imwrite(
                str(temp),
                large_image.encode_strips(rgb),
                shape=rgb.shape,
                dtype=np.uint16,
                rowsperstrip=large_image.STRIP_ROWS,
                photometric='rgb',
                metadata=None,
                description=photo_metadata.description(photo, provenance)
                if photo is not None
                else None,
                extratags=[(34675, 'B', len(profile), profile, False)],
            )
        else:
            data = large_image.allocate(rgb.shape, np.uint8)
            for y, block in large_image.strips(rgb):
                data[y : y + len(block)] = np.round(np.clip(block, 0, 1) * 255).astype(np.uint8)
            image = Image.fromarray(data)
            profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
            options = dict(icc_profile=profile)
            if ext in {'.jpg', '.jpeg'}:
                options.update(quality=quality, subsampling=0)
            image.save(temp, **options)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
