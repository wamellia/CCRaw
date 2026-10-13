"""SDR display conversion and proofing, separate from recipes and output files."""

import ctypes
from ctypes import wintypes as wt
from functools import lru_cache
from pathlib import Path
import sys

import numpy as np

from . import cms, colorspaces as cs


def system_profile(window_handle):
    if sys.platform != 'win32' or not window_handle:
        return ''

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ('size', wt.DWORD),
            ('monitor', wt.RECT),
            ('work', wt.RECT),
            ('flags', wt.DWORD),
            ('device', wt.WCHAR * 32),
        ]

    user, gdi = (
        ctypes.WinDLL('user32', use_last_error=True),
        ctypes.WinDLL('gdi32', use_last_error=True),
    )
    user.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
    user.MonitorFromWindow.restype = wt.HANDLE
    user.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.POINTER(MonitorInfo)]
    monitor = user.MonitorFromWindow(window_handle, 2)
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    if not user.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return ''
    gdi.CreateDCW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPVOID]
    gdi.CreateDCW.restype = wt.HDC
    gdi.GetICMProfileW.argtypes = [wt.HDC, ctypes.POINTER(wt.DWORD), wt.LPWSTR]
    gdi.DeleteDC.argtypes = [wt.HDC]
    dc = gdi.CreateDCW('DISPLAY', info.device, None, None)
    if not dc:
        return ''
    try:
        count = wt.DWORD(32768)
        text = ctypes.create_unicode_buffer(count.value)
        return text.value if gdi.GetICMProfileW(dc, ctypes.byref(count), text) else ''
    finally:
        gdi.DeleteDC(dc)


@lru_cache(maxsize=16)
def _profile(path, stamp, size, rgb_only):
    return cms.load_profile(path, rgb_only=rgb_only)


def profile(path, rgb_only=True):
    path = Path(path)
    stat = path.stat()
    return _profile(str(path.resolve()), stat.st_mtime_ns, stat.st_size, rgb_only)


def snapshot(window):
    from PySide6.QtGui import QGuiApplication

    path = window.property('displayProfilePath') or ''
    if (
        not path
        and window.property('displayAutomaticICC')
        and QGuiApplication.platformName() != 'offscreen'
    ):
        path = system_profile(int(window.winId()))
    options = dict(
        target=profile(path) if path else cs.profile_bytes(),
        intent=window.property('displayIntent') or 'relative',
        bpc=window.property('displayBPC') is not False,
        proof=dict(getattr(window, '_softproof', {})),
    )
    if options['proof'].get('enabled'):
        options['proof']['profile'] = profile(options['proof']['path'], rgb_only=False)
    return options


def convert(rgb, settings=None):
    if settings is None:
        return rgb
    source = cs.profile_bytes('sRGB')
    proof = settings.get('proof', {})
    if proof.get('enabled'):
        result = cms.soft_proof(
            rgb,
            source,
            proof['profile'],
            settings['target'],
            intent=proof.get('intent', 'relative'),
            paper_ink=proof.get('paper_ink', False),
            black_point_compensation=proof.get('black_point_compensation', True),
        )
        out = result.rgb
        if proof.get('gamut_warning', False):
            out = np.where(result.gamut_mask[..., None], [1.0, 0.0, 1.0], out).astype(np.float32)
        return out
    if settings['target'] == source:
        return rgb
    return cms.transform_icc(
        rgb,
        source,
        settings['target'],
        intent=settings['intent'],
        black_point_compensation=settings['bpc'],
    )
