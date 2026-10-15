"""Monochrome toolbar symbols rendered as vectors at the display scale."""

from functools import lru_cache
from math import ceil
from PySide6.QtCore import QByteArray, QRectF, QSize
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from .theme import tokens

PATHS = {
    'plus': '<path d="M12 4v16M4 12h16"/>',
    'search': '<circle cx="10" cy="10" r="7"/><path d="m15 15 6 6"/>',
    'open': '<path d="M3 7h6l2 2h10l-3 11H3V7Zm0 5h17"/>',
    'save': '<path d="M5 3h12l4 4v14H3V3h2Zm2 0v7h10V3M7 21v-7h10v7"/>',
    'export': '<path d="M12 16V3m-4 4 4-4 4 4M4 13v8h16v-8"/>',
    'undo': '<path d="m8 4-5 5 5 5M3 9h10a7 7 0 0 1 0 14"/>',
    'redo': '<path d="m16 4 5 5-5 5m5-5H11a7 7 0 0 0 0 14"/>',
    'sidebar': '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>',
    'light': '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/>',
    'color': '<path d="M12 3a9 9 0 1 0 0 18h2a3 3 0 0 0 0-6h-1a2 2 0 0 1 0-4h3a4 4 0 0 0 4-4c0-2-4-4-8-4Z"/><circle cx="7" cy="10" r="1"/><circle cx="8" cy="15" r="1"/><circle cx="12" cy="7" r="1"/>',
    'watermark': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m5 17 5-5 4 4 3-3 3 4M6 7h5"/>',
    'detail': '<path d="m12 3 9 18H3L12 3Zm0 5v8m0 3v1"/>',
    'mask': '<circle cx="12" cy="12" r="9" stroke-dasharray="3 3"/><path d="m8 16 8-8m-9 5 6-6m-2 10 6-6"/>',
    'crop': '<path d="M6 2v16h16M2 6h16v16M10 2h12v12"/>',
    'grading': '<circle cx="12" cy="8" r="5"/><circle cx="8" cy="15" r="5"/><circle cx="16" cy="15" r="5"/>',
    'effects': '<path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3 3-7Z"/>',
    'retouch': '<path d="m4 15 11-11a4 4 0 0 1 6 6L10 21a4 4 0 0 1-6-6Zm5-5 5 5m-2-7 1 1m2 2 1 1"/>',
}


def icon(name, widget=None, primary=False):
    t = tokens(widget)
    return _render_icon(name, t, primary)


@lru_cache(maxsize=64)
def _render_icon(name, t, primary):
    result = QIcon()
    for mode, stroke in (
        (QIcon.Mode.Normal, '#FFFFFF' if primary else t.text),
        (QIcon.Mode.Disabled, t.disabled),
        (QIcon.Mode.Selected, '#FFFFFF' if primary else t.accent),
    ):
        # The view box includes stroke padding; the painter's target uses logical
        # pixels, even though the pixmap backing store uses physical pixels.
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -2 28 28"><g fill="none" stroke="{stroke}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</g></svg>'
        renderer = QSvgRenderer(QByteArray(svg.encode()))
        for ratio in (1.0, 1.25, 1.5, 2.0, 3.0):
            image = QPixmap(ceil(22 * ratio), ceil(22 * ratio))
            image.setDevicePixelRatio(ratio)
            image.fill('transparent')
            painter = QPainter(image)
            renderer.render(painter, QRectF(0, 0, 22, 22))
            painter.end()
            result.addPixmap(image, mode)
    return result


def set_icon(button, name, primary=False):
    button.setProperty('symbol', name)
    button.setProperty('filled', primary)
    button.setIcon(icon(name, button, primary))
    button.setIconSize(QSize(22, 22))
