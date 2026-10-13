"""LittleCMS 2 base-engine ctypes bridge. No 8-bit Pillow image intermediates.

The bundled Windows x64 library is compiled from MIT LittleCMS 2.18. Other
platforms can use their installed liblcms2. Failure is explicit: mathematical
named-space conversion remains usable, arbitrary ICC conversion is unavailable.
"""

from __future__ import annotations

import ctypes as C
import ctypes.util
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import struct

import numpy as np

from .colorspaces import rgb_array

MAX_PROFILE_BYTES = 32 << 20
_RGB_FLOAT = (1 << 22) | (4 << 16) | (3 << 3) | 4
_RGB_U16 = (4 << 16) | (3 << 3) | 2
_NO_OPTIMIZE, _BPC, _PROOF, _GAMUT = 0x100, 0x2000, 0x4000, 0x1000
_INTENTS = {
    'perceptual': 0,
    'relative': 1,
    'relative_colorimetric': 1,
    'relative colorimetric': 1,
    'saturation': 2,
    'absolute': 3,
    'absolute_colorimetric': 3,
    'absolute colorimetric': 3,
}


class CMSUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _library():
    bundled = Path(__file__).resolve().parents[1] / 'resources' / 'lcms2.dll'
    paths = [str(bundled)] if bundled.is_file() else []
    system = ctypes.util.find_library('lcms2')
    if system:
        paths.append(system)
    errors = []
    for path in paths:
        try:
            lib = (C.WinDLL if hasattr(C, 'WinDLL') else C.CDLL)(path)

            def bind(name, result, args):
                f = getattr(lib, name)
                f.restype, f.argtypes = result, args

            p, u = C.c_void_p, C.c_uint32
            bind('cmsOpenProfileFromMem', p, [p, u])
            bind('cmsCloseProfile', C.c_int, [p])
            bind('cmsGetColorSpace', u, [p])
            bind('cmsCreateTransform', p, [p, u, p, u, u, u])
            bind('cmsCreateProofingTransform', p, [p, u, p, u, p, u, u, u])
            bind('cmsDoTransform', None, [p, p, p, u])
            bind('cmsDeleteTransform', None, [p])
            bind('cmsGetEncodedCMMversion', u, [])
            bind('cmsCreateContext', p, [p, p])
            bind('cmsDeleteContext', None, [p])
            bind('cmsSetAlarmCodesTHR', None, [p, C.POINTER(C.c_uint16)])
            bind('cmsCreateProofingTransformTHR', p, [p, p, u, p, u, p, u, u, u])
            return lib
        except (OSError, AttributeError) as exc:
            errors.append(str(exc))
    raise CMSUnavailable(
        'Native LittleCMS 2 is unavailable; arbitrary ICC and proofing disabled'
        + (': ' + '; '.join(errors) if errors else '')
    )


def available():
    try:
        _library()
        return True
    except CMSUnavailable:
        return False


def status():
    try:
        lib = _library()
        return {
            'available': True,
            'engine': 'LittleCMS base MIT',
            'version': lib.cmsGetEncodedCMMversion(),
            'library': str(lib._name),
            'formats': ['RGB float32', 'RGB uint16'],
        }
    except CMSUnavailable as exc:
        return {'available': False, 'reason': str(exc)}


def validate_profile(data, *, rgb_only=False):
    """Bounded header and tag-table validation before handing data to native code."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError('ICC profile must be bytes')
    data = bytes(data)
    if len(data) < 132 or len(data) > MAX_PROFILE_BYTES or data[36:40] != b'acsp':
        raise ValueError('Invalid ICC profile header or size')
    size, count = struct.unpack_from('>I', data)[0], struct.unpack_from('>I', data, 128)[0]
    if size != len(data) or count > 4096 or 132 + 12 * count > size:
        raise ValueError('Invalid ICC profile size or tag count')
    if rgb_only and data[16:20] != b'RGB ':
        raise ValueError('RGB source and destination ICC profiles are required')
    for i in range(count):
        offset, length = struct.unpack_from('>II', data, 136 + 12 * i)
        if offset < 132 + 12 * count or length < 8 or offset + length > size:
            raise ValueError('ICC tag extends beyond its profile')
    return data


def load_profile(path, *, rgb_only=False):
    path = Path(path)
    if path.stat().st_size > MAX_PROFILE_BYTES:
        raise ValueError('ICC profile is too large')
    return validate_profile(path.read_bytes(), rgb_only=rgb_only)


def _intent(value):
    if isinstance(value, str) and value.lower() in _INTENTS:
        return _INTENTS[value.lower()]
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool) and 0 <= value <= 3:
        return int(value)
    raise ValueError(f'Unknown ICC rendering intent: {value}')


def _execute(
    a,
    source,
    target,
    *,
    intent='relative',
    proof=None,
    proof_intent='relative',
    black_point_compensation=True,
    paper_ink=False,
    gamut=False,
    alarm_codes=(65535, 0, 65535),
):
    lib = _library()
    handles, buffers, context = [], [], None
    transform = None
    try:
        for data, rgb_only in ((source, True), (target, True), (proof, False)):
            if data is None:
                continue
            buf = C.create_string_buffer(validate_profile(data, rgb_only=rgb_only))
            handle = lib.cmsOpenProfileFromMem(buf, len(data))
            if not handle:
                raise ValueError('LittleCMS cannot parse this ICC profile')
            handles.append(handle)
            buffers.append(buf)
        flags = _NO_OPTIMIZE | (_BPC if black_point_compensation and not paper_ink else 0)
        fmt = _RGB_U16 if a.dtype == np.uint16 else _RGB_FLOAT
        output = np.empty_like(a)
        if proof is not None:
            if gamut:
                context = lib.cmsCreateContext(None, None)
                if not context:
                    raise CMSUnavailable('Cannot create LittleCMS proofing context')
                alarm = (C.c_uint16 * 16)(*alarm_codes, *([0] * 13))
                lib.cmsSetAlarmCodesTHR(context, alarm)
                flags |= _GAMUT
            flags |= _PROOF
            output_intent = 3 if paper_ink else _intent(intent)
            args = (
                handles[0],
                fmt,
                handles[1],
                fmt,
                handles[2],
                output_intent,
                _intent(proof_intent),
                flags,
            )
            transform = (
                lib.cmsCreateProofingTransformTHR(context, *args)
                if context
                else lib.cmsCreateProofingTransform(*args)
            )
        else:
            transform = lib.cmsCreateTransform(
                handles[0], fmt, handles[1], fmt, _intent(intent), flags
            )
        if not transform:
            raise ValueError('LittleCMS cannot create the requested ICC transform')
        flat_a, flat_out = a.reshape(-1, 3), output.reshape(-1, 3)
        for start in range(0, len(flat_a), 1 << 20):
            n = min(1 << 20, len(flat_a) - start)
            lib.cmsDoTransform(
                transform, flat_a[start:].ctypes.data, flat_out[start:].ctypes.data, n
            )
        if not np.isfinite(output).all():
            raise ValueError('ICC transform produced non-finite color values')
        return output
    finally:
        if transform:
            lib.cmsDeleteTransform(transform)
        for handle in handles:
            lib.cmsCloseProfile(handle)
        if context:
            lib.cmsDeleteContext(context)


def transform_icc(
    rgb,
    source_profilebytes,
    target_profilebytes,
    intent='relative',
    proof=None,
    *,
    black_point_compensation=True,
    proof_intent='relative',
    paper_ink=False,
):
    a = np.ascontiguousarray(rgb_array(rgb))
    source = validate_profile(source_profilebytes, rgb_only=True)
    target = validate_profile(target_profilebytes, rgb_only=True)
    _intent(intent)
    if source == target and proof is None:
        _library()  # The identity fast path still requires a usable native CMS.
        _validate_identity(source)
        # A real identity preserves negatives, HDR and every float32 bit.
        return a.copy()
    return _execute(
        a,
        source,
        target,
        intent=intent,
        proof=proof,
        black_point_compensation=black_point_compensation,
        proof_intent=proof_intent,
        paper_ink=paper_ink,
    )


def transform_icc_u16(rgb, source_profilebytes, target_profilebytes, intent='relative', **kwargs):
    a = np.asarray(rgb)
    if a.dtype != np.uint16 or a.ndim < 1 or a.shape[-1] != 3:
        raise ValueError('Native 16-bit ICC input must be uint16 RGB')
    source = validate_profile(source_profilebytes, rgb_only=True)
    target = validate_profile(target_profilebytes, rgb_only=True)
    _intent(intent)
    if source == target and not kwargs.get('proof'):
        _library()
        _validate_identity(source)
        return a.copy()
    return _execute(np.ascontiguousarray(a), source, target, intent=intent, **kwargs)


@lru_cache(maxsize=16)
def _validate_identity(data):
    # Building a zero-pixel transform checks ICC tables and native support without
    # rewriting identity pixels or retaining mutable native handles in a cache.
    _execute(np.empty((0, 3), np.float32), data, data)


@dataclass(frozen=True)
class ProofResult:
    rgb: np.ndarray
    gamut_mask: np.ndarray


def soft_proof(
    rgb,
    source_profilebytes,
    proof_profilebytes,
    display_profilebytes=None,
    *,
    intent='relative',
    black_point_compensation=True,
    paper_ink=False,
):
    """Proof RGB/CMYK/gray device profiles in source or display RGB; mask is boolean.

    LittleCMS's separate colorimetric gamut-check transform uses a private context,
    so alarm codes cannot alter another document's transform in concurrent renders.
    """
    a = np.ascontiguousarray(rgb_array(rgb))
    target = display_profilebytes or source_profilebytes
    source = validate_profile(source_profilebytes, rgb_only=True)
    target = validate_profile(target, rgb_only=True)
    proof = validate_profile(proof_profilebytes)
    options = dict(
        proof=proof,
        intent='relative',
        proof_intent=intent,
        black_point_compensation=black_point_compensation,
        paper_ink=paper_ink,
    )
    out = _execute(a, source, target, **options)
    # Two distinct private alarm colors distinguish actual magenta/green pixels
    # from CMS markers. Both passes retain float32 precision.
    check = _execute(
        a,
        source,
        target,
        proof=proof,
        intent='relative',
        proof_intent='relative',
        black_point_compensation=False,
        gamut=True,
    )
    check2 = _execute(
        a,
        source,
        target,
        proof=proof,
        intent='relative',
        proof_intent='relative',
        black_point_compensation=False,
        gamut=True,
        alarm_codes=(0, 65535, 0),
    )
    mask = np.all(check == [1, 0, 1], axis=-1) & np.all(check2 == [0, 1, 0], axis=-1)
    return ProofResult(out, mask)
