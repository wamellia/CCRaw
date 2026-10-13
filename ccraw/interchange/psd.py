"""Flat RGB Photoshop Document v1 writer (8/16-bit, uncompressed merged image).

No layer reconstruction is promised. ICC resource 1039 and optional XMP
resource 1060 describe the merged RGB channels without quantizing 16-bit data.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


def _resource(identifier, data):
    return (
        b'8BIM'
        + struct.pack('>H', identifier)
        + b'\0\0'
        + struct.pack('>I', len(data))
        + data
        + (b'\0' if len(data) % 2 else b'')
    )


def write_psd(path, rgb, *, bit_depth=16, icc_profile=None, xmp_packet=None, resolution=300):
    from ..export_presets import atomic_write, validate_rgb, validate_icc_profile, _number

    rgb = validate_rgb(rgb)
    if isinstance(bit_depth, bool) or bit_depth not in (8, 16):
        raise ValueError('PSD supports RGB at 8 or 16 bits per channel')
    if icc_profile is not None:
        validate_icc_profile(icc_profile)
    resources = _resource(1039, icc_profile) if icc_profile else b''
    _number(resolution, 'resolution', 1, 2400)
    ppi = round(resolution * 65536)
    resources += _resource(1005, struct.pack('>IHHIHH', ppi, 1, 1, ppi, 1, 1))
    if xmp_packet:
        resources += _resource(1060, xmp_packet.encode('utf-8'))
    height, width = rgb.shape[:2]
    header = struct.pack('>4sH6sHIIHH', b'8BPS', 1, b'\0' * 6, 3, height, width, bit_depth, 3)
    dtype = np.dtype('u1' if bit_depth == 8 else '>u2')

    def encode(temp):
        with temp.open('wb') as file:
            file.write(header + struct.pack('>I', 0) + struct.pack('>I', len(resources)))
            file.write(resources)
            file.write(struct.pack('>IH', 0, 0))
            # Channel-major scanlines, big-endian words. Avoid a second full-image copy.
            for channel in range(3):
                for y in range(0, height, 128):
                    block = np.rint(
                        np.clip(rgb[y : y + 128, :, channel], 0, 1) * ((1 << bit_depth) - 1)
                    )
                    file.write(block.astype(dtype).tobytes())

    return atomic_write(path, encode)


def _unpack_row(data, expected):
    output = bytearray()
    offset = 0
    while offset < len(data):
        count = data[offset]
        offset += 1
        if count <= 127:
            length = count + 1
            if offset + length > len(data):
                raise ValueError('truncated PSD PackBits literal')
            output.extend(data[offset : offset + length])
            offset += length
        elif count >= 129:
            if offset >= len(data):
                raise ValueError('truncated PSD PackBits repeat')
            output.extend(bytes([data[offset]]) * (257 - count))
            offset += 1
        if len(output) > expected:
            raise ValueError('PSD PackBits scanline exceeds width')
    if len(output) != expected:
        raise ValueError('PSD PackBits scanline does not match width')
    return output


def read_psd(path):
    """Read the merged RGB composite in a PSD v1, raw or PackBits at 8/16 bit.

    Layers are skipped, alpha is excluded and no layer compositing occurs.
    ZIP/prediction, PSB, indexed/CMYK/Lab and 32-bit documents are explicitly
    rejected; callers can inject a broader loader into the handoff API.
    Returned RGB remains encoded in the embedded profile's output space.
    """
    path = Path(path)
    file_size = path.stat().st_size
    if file_size > 2 * 1024**3:
        raise ValueError('PSD exceeds 2 GiB bound')
    with path.open('rb') as file:

        def read_exact(size):
            data = file.read(size)
            if len(data) != size:
                raise ValueError('truncated PSD')
            return data

        header = read_exact(26)
        signature, version, reserved, channels, height, width, depth, mode = struct.unpack(
            '>4sH6sHIIHH', header
        )
        if (
            signature != b'8BPS'
            or version != 1
            or reserved != b'\0' * 6
            or mode != 3
            or depth not in (8, 16)
        ):
            raise ValueError('external PSD must be RGB version 1 at 8/16 bits')
        if not 3 <= channels <= 56 or not 1 <= min(width, height) or max(width, height) > 30000:
            raise ValueError('invalid PSD dimensions or channels')
        if width * height * channels > 600_000_000:
            raise ValueError('PSD pixel allocation exceeds bound')

        def section_length():
            size = struct.unpack('>I', read_exact(4))[0]
            if size > file_size - file.tell():
                raise ValueError('PSD section exceeds file size')
            return size

        file.seek(section_length(), 1)
        resources_length = section_length()
        if resources_length > 32 * 1024 * 1024:
            raise ValueError('PSD resources exceed bound')
        resources = read_exact(resources_length)
        offset = 0
        profile = None
        while offset < len(resources):
            if offset + 7 > len(resources) or resources[offset : offset + 4] != b'8BIM':
                raise ValueError('invalid PSD image resource')
            identifier = struct.unpack_from('>H', resources, offset + 4)[0]
            name_length = resources[offset + 6] + 1
            name_length += name_length % 2
            offset += 6 + name_length
            if offset + 4 > len(resources):
                raise ValueError('truncated PSD image resource')
            size = struct.unpack_from('>I', resources, offset)[0]
            offset += 4
            if offset + size > len(resources):
                raise ValueError('truncated PSD resource content')
            if identifier == 1039:
                profile = resources[offset : offset + size]
            offset += size + size % 2
        file.seek(section_length(), 1)
        compression = struct.unpack('>H', read_exact(2))[0]
        dtype = np.dtype('u1' if depth == 8 else '>u2')
        row_size = width * dtype.itemsize
        from .. import large_image

        rgb = large_image.allocate((height, width, 3))
        if compression == 0:
            remaining = row_size * height * channels
            if remaining > file_size - file.tell():
                raise ValueError('truncated PSD composite')
            for channel in range(3):
                for y in range(height):
                    row = np.frombuffer(read_exact(row_size), dtype=dtype)
                    rgb[y, :, channel] = row.astype(np.float32) / ((1 << depth) - 1)
        elif compression == 1:
            counts = np.frombuffer(read_exact(2 * channels * height), dtype='>u2').astype(np.int64)
            if int(counts.sum()) > file_size - file.tell():
                raise ValueError('truncated PSD RLE composite')
            for channel in range(3):
                for y in range(height):
                    data = read_exact(int(counts[channel * height + y]))
                    decoded = _unpack_row(data, row_size)
                    rgb[y, :, channel] = np.frombuffer(decoded, dtype=dtype).astype(np.float32) / (
                        (1 << depth) - 1
                    )
        else:
            raise ValueError('PSD ZIP/prediction requires a compatible external image loader')
    return {'rgb': rgb, 'icc_profile': profile, 'bit_depth': depth}
