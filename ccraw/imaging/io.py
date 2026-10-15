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

DECODE_TILE_PIXELS = 1 << 18


def _tiles(image):
    h, w = image.shape[:2]
    rows = max(1, min(large_image.STRIP_ROWS, DECODE_TILE_PIXELS))
    columns = max(1, DECODE_TILE_PIXELS // rows)
    for y in range(0, h, rows):
        for x in range(0, w, columns):
            yield y, x, image[y : y + rows, x : x + columns]


def _contiguous(rgb):
    if rgb.flags.c_contiguous:
        return rgb
    output = large_image.allocate(rgb.shape)
    for y, x, block in _tiles(rgb):
        output[y : y + block.shape[0], x : x + block.shape[1]] = block
    return output


class ImageSource(np.ndarray):
    """Linear RGB with source provenance retained by NumPy views."""

    def __new__(cls, rgb, **attributes):
        obj = np.asarray(_contiguous(rgb), dtype=np.float32).view(cls)
        for name in ('source_path', 'raw_path', 'camera_kelvin', 'camera_neutral', 'decode_info'):
            setattr(obj, name, attributes.get(name))
        return obj

    def __array_finalize__(self, source):
        if source is not None:
            for name in (
                'source_path',
                'raw_path',
                'camera_kelvin',
                'camera_neutral',
                'decode_info',
            ):
                setattr(self, name, getattr(source, name, None))


def _orientation(rgb, value):
    if value == 2:
        return rgb[:, ::-1]
    if value == 3:
        return rgb[::-1, ::-1]
    if value == 4:
        return rgb[::-1]
    if value == 5:
        return rgb.transpose(1, 0, 2)
    if value == 6:
        return np.rot90(rgb, 3)
    if value == 7:
        return rgb.transpose(1, 0, 2)[::-1, ::-1]
    if value == 8:
        return np.rot90(rgb)
    return rgb


def _decode_rgb(encoded, profile, denominator=1):
    from . import cms, colorspaces

    if profile:
        profile = cms.validate_profile(profile, rgb_only=True)
        target = colorspaces.profile_bytes('sRGB', linear=True)
    output = large_image.allocate((*encoded.shape[:2], 3))

    def decode_tile(tile):
        y, x, block = tile
        if block.ndim == 2:
            block = np.repeat(block[..., None], 3, axis=-1)
        block = np.ascontiguousarray(block[..., :3], dtype=np.float32) / denominator
        # Float samples go directly to a linear profile, in bounded tiles: no
        # encoded sRGB intermediate or whole-frame float64 transfer-function arrays.
        decoded = (
            cms.transform_icc(block, profile, target, black_point_compensation=False)
            if profile
            else colorspaces.decode(block, 'sRGB')
        )
        output[y : y + block.shape[0], x : x + block.shape[1]] = decoded

    if profile:
        from .. import cpu_ops

        # Each CMS call creates and owns its own native profiles/transform. No
        # mutable LittleCMS handle or output pixel is shared between workers.
        cpu_ops.map_blocks(decode_tile, _tiles(encoded))
    else:
        for tile in _tiles(encoded):
            decode_tile(tile)
    return output


def _decode_preview(encoded, profile, denominator, limit):
    """Area-resample decoded linear samples in strips; never average gamma pixels.

    The sensor/raster extent is unchanged in metadata. Only the working preview
    is small, using the same ICC transform and linear-area filter as a full decode.
    """
    h, w = encoded.shape[:2]
    if not limit or max(h, w) <= limit:
        return _decode_rgb(encoded, profile, denominator)
    from .. import native_kernels

    ratio = limit / max(h, w)
    tw, th = max(1, round(w * ratio)), max(1, round(h * ratio))
    scale = h / th
    pixels = min(2**21, max(DECODE_TILE_PIXELS, h * w // 4))
    rows = max(1, min(64, int(pixels / max(w, 1) / scale)))
    out = large_image.allocate((th, tw, 3))
    for begin in range(0, th, rows):
        end = min(th, begin + rows)
        y0, y1 = int(np.floor(begin * scale)), min(h, int(np.ceil(end * scale)))
        linear = _decode_rgb(encoded[y0:y1], profile, denominator)
        horizontal = cv2.resize(linear, (tw, y1 - y0), interpolation=cv2.INTER_AREA)
        small = native_kernels.area_rows(horizontal, y0, h, th, begin, end)
        if small is None:
            small = np.empty((end - begin, tw, 3), np.float32)
            for row in range(begin, end):
                top, bottom = row * scale, (row + 1) * scale
                indices = np.arange(int(np.floor(top)), min(h, int(np.ceil(bottom))))
                weights = (
                    np.maximum(0, np.minimum(bottom, indices + 1) - np.maximum(top, indices))
                    / scale
                )
                small[row - begin] = np.einsum('i,ijk->jk', weights, horizontal[indices - y0])
        out[begin:end] = small
    return out


def _decode_xyz(xyz):
    from .colorspaces import rgb_to_xyz_matrix

    matrix = np.linalg.inv(rgb_to_xyz_matrix('sRGB')).T
    output = large_image.allocate(xyz.shape)
    for y, x, block in _tiles(xyz):
        output[y : y + block.shape[0], x : x + block.shape[1]] = (
            block.astype(np.float32) / 65535
        ) @ matrix
    return output


def _codec_memory(shape, bytes_per_pixel):
    """Codecs without a mapped-output API must fit before allocating their raster."""
    size = int(shape[0]) * int(shape[1]) * bytes_per_pixel
    available = large_image.available_memory()
    if size > large_image.MAP_BYTES and available is not None and size * 2 > available * 0.65:
        raise ValueError('当前可用内存不足以解码这张图片；请先转换为分块 TIFF 或降低原片尺寸。')


def _source(rgb, path, info, limit):
    large_image.validate_size((info['height'], info['width']))
    return ImageSource(
        pipeline.resize_limit(rgb, limit),
        source_path=str(path.resolve()),
        raw_path=str(path.resolve()) if info.get('raw') else None,
        camera_kelvin=info.get('white_balance', {}).get('camera_kelvin'),
        camera_neutral=info.get('camera_neutral'),
        decode_info=info.get(
            'decode_info',
            {
                'source_bit_depth': info.get('source_bit_depth'),
                'icc_applied': info.get('icc_applied', False),
            },
        ),
    )


def _raster(path, limit):
    profile, orientation = None, 1
    if path.suffix.lower() in ('.tif', '.tiff'):
        import tifffile

        with tifffile.TiffFile(path) as file:
            page = file.pages[0]
            large_image.validate_size((page.imagelength, page.imagewidth))
            if page.photometric not in (1, 2):
                raise ValueError('TIFF 输入需要 RGB 或黑白图像。')
            arr = page.asarray(out='memmap' if page.nbytes > large_image.MAP_BYTES else None)
            if page.planarconfig == 2 and arr.ndim == 3:
                arr = arr.transpose(1, 2, 0)
            tag = page.tags.get(34675)
            profile = bytes(tag.value) if tag else None
            tag = page.tags.get(274)
            orientation = int(tag.value) if tag else 1
    elif path.suffix.lower() == '.psd':
        from ..interchange.psd import read_psd

        packet = read_psd(path)
        arr, profile = packet['rgb'], packet['icc_profile']
        depth = packet['bit_depth']
    else:
        with Image.open(path) as image:
            large_image.validate_size((image.height, image.width))
            _codec_memory((image.height, image.width), 8)  # PNG uint16 RGBA upper bound.
            profile = image.info.get('icc_profile')
            orientation = image.getexif().get(274, 1)
        arr = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if arr is None:
            raise ValueError('无法解码这张图片。')
        if arr.ndim == 3:
            arr = arr[..., :3][..., ::-1]
    if arr.ndim not in (2, 3) or (arr.ndim == 3 and arr.shape[-1] < 3):
        raise ValueError('输入图像需要三个 RGB 通道。')
    if arr.dtype == np.uint16:
        depth, denominator = 16, 65535
    elif arr.dtype == np.uint8:
        depth, denominator = 8, 255
    elif np.issubdtype(arr.dtype, np.floating):
        depth = depth if path.suffix.lower() == '.psd' else arr.dtype.itemsize * 8
        denominator = 1
    else:
        raise ValueError('输入支持 8/16 位整数或浮点 RGB。')
    height, width = arr.shape[:2]
    if orientation in (5, 6, 7, 8):
        width, height = height, width
    rgb = _contiguous(_orientation(_decode_preview(arr, profile, denominator, limit), orientation))
    info = dict(
        name=path.name,
        format=path.suffix[1:].upper(),
        path=str(path.resolve()),
        width=width,
        height=height,
        raw=False,
        source_bit_depth=depth,
        icc_applied=bool(profile),
        note=f'{depth}-bit · 浮点 ICC → 线性 sRGB · EXIF 方向已应用'
        if profile
        else f'{depth}-bit · 按 sRGB 解读 · EXIF 方向已应用',
    )
    info['photo'] = photo_metadata.embedded(path) or photo_metadata.from_tags(
        white_balance.metadata(path)[0]
    )
    return _source(rgb, path, info, limit), info


def _camera_neutral(raw):
    gains = np.asarray(raw.camera_whitebalance[:3], np.float64)
    if gains.shape != (3,) or not np.isfinite(gains).all() or np.any(gains <= 0):
        raise ValueError('相机白平衡缺少有效的三通道中性值；无法应用 DCP 相机校色。')
    return ((1 / gains) / (1 / gains[1])).tolist()


def decode_camera_native(path, preview_shape=None):
    """Demosaiced, unbalanced camera RGB for a DCP camera-stage transform.

    LibRaw performs black subtraction and sensor scaling into 16-bit samples.
    Unity WB, linear gamma and raw output prevent a camera matrix or RGB profile
    being applied. Four-colour sensors are unsupported by the three-channel DCP.
    ``preview_shape`` is (height, width) of the already oriented editing source.
    """
    import rawpy

    path = Path(path).resolve()
    with rawpy.imread(str(path)) as raw:
        large_image.validate_size((raw.sizes.height, raw.sizes.width))
        _codec_memory((raw.sizes.height, raw.sizes.width), 16)
        raw.unpack()
        if raw.num_colors != 3:
            raise ValueError('DCP 原生校色仅支持三通道相机。')
        neutral = _camera_neutral(raw)
        half_size = (
            preview_shape is not None
            and max(preview_shape) <= max(raw.sizes.height, raw.sizes.width) / 2
        )
        rgb = raw.postprocess(
            use_camera_wb=False,
            use_auto_wb=False,
            user_wb=[1.0, 1.0, 1.0, 1.0],
            no_auto_bright=True,
            output_bps=16,
            gamma=(1, 1),
            output_color=rawpy.ColorSpace.raw,
            half_size=half_size,
            user_flip=None,
            highlight_mode=rawpy.HighlightMode.Clip,
        )
        output = large_image.allocate(rgb.shape)
        for y, x, block in _tiles(rgb):
            output[y : y + block.shape[0], x : x + block.shape[1]] = (
                block.astype(np.float32) / 65535
            )
        rgb = output
        tags, _ = white_balance.metadata(path)
        wb = white_balance.from_metadata(tags, white_balance.estimate(raw))
    if preview_shape is not None:
        if len(preview_shape) != 2 or any(
            isinstance(n, bool) or int(n) != n or n < 1 for n in preview_shape
        ):
            raise ValueError('原生预览需要有效的 (height, width)。')
        large_image.validate_size(preview_shape)
        rgb = cv2.resize(
            rgb, (int(preview_shape[1]), int(preview_shape[0])), interpolation=cv2.INTER_AREA
        )
    return ImageSource(
        rgb,
        source_path=str(path),
        raw_path=str(path),
        camera_neutral=neutral,
        camera_kelvin=wb.get('camera_kelvin'),
        decode_info={
            'native': True,
            'color_space': 'camera-native',
            'source_bit_depth': 16,
            'white_balance': 'unity',
            'sensor_scaling': 'LibRaw black subtraction and normalization',
        },
    )


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
            large_image.validate_size((raw.sizes.height, raw.sizes.width))
            _codec_memory((raw.sizes.height, raw.sizes.width), 16)
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
            try:
                info['camera_neutral'] = _camera_neutral(raw)
            except ValueError:
                info['camera_neutral'] = None
            large_image.validate_size((info['height'], info['width']))
            rgb = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=True,
                output_bps=16,
                gamma=(1, 1),
                output_color=rawpy.ColorSpace.XYZ,
                half_size=bool(preview_limit),
                user_flip=None,
                highlight_mode=rawpy.HighlightMode.Blend,
            )
            rgb = _decode_xyz(rgb)
            info['source_bit_depth'] = 16
            info['decode_info'] = {
                'native': True,
                'source_bit_depth': 16,
                'color_space': 'XYZ D65 -> linear sRGB',
                'white_balance': 'camera',
                'precision_limit': 'LibRaw 16-bit XYZ channel bounds',
            }
            if preview_limit or develop_reference:
                curve, label = develop.camera_curve(raw, rgb, path)
                info['develop'] = dict(mode='camera', curve=curve, source=label)
        info['note'] = 'LibRaw · 相机白平衡 · XYZ → 线性 sRGB · 16-bit · 保留负值及超白分量'
    elif path.suffix.lower() in {'.tif', '.tiff', '.png', '.psd'}:
        return _raster(path, preview_limit)
    else:
        return load_pillow(path, preview_limit)
    return _source(rgb, path, info, preview_limit), info


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
    path = Path(path)
    with Image.open(path) as src:
        large_image.validate_size((src.height, src.width))
        _codec_memory((src.height, src.width), 12)
        img = ImageOps.exif_transpose(src)
        profile = src.info.get('icc_profile')
        img = img.convert('RGB')
        info = dict(
            name=path.name,
            width=img.width,
            height=img.height,
            raw=False,
            format=path.suffix[1:].upper(),
            path=str(path.resolve()),
            note='浮点 ICC → 线性 sRGB · EXIF 方向已应用' if profile else 'sRGB · EXIF 方向已应用',
            source_bit_depth=8,
            icc_applied=bool(profile),
        )
        info['photo'] = photo_metadata.from_tags(white_balance.metadata(path)[0])
        if limit:
            img.thumbnail((limit, limit), Image.Resampling.LANCZOS)
        return (_source(_decode_rgb(np.asarray(img), profile, 255), path, info, 0), info)


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
