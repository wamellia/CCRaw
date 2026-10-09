"""Full-quality interactive previews and original-pixel visible-region rendering.

The GUI owns the quality controller. Image preparation runs in the single image
worker, including QImage conversion (QPixmap remains on the GUI thread).
"""

from dataclasses import dataclass
import time

import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtCore import QObject, QEvent, Qt, Signal
from PySide6.QtWidgets import QAbstractSpinBox, QSlider

from . import engine, develop
from .widgets import qimage


class InputGestures(QObject):
    """Spin-button holds and keyboard auto-repeat share the mouse drag lifecycle."""

    dragging = Signal(bool)
    committed = Signal()
    KEYS = (
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_Left,
        Qt.Key.Key_Right,
        Qt.Key.Key_PageUp,
        Qt.Key.Key_PageDown,
        Qt.Key.Key_Home,
        Qt.Key.Key_End,
    )

    def __init__(self, owner):
        super().__init__(owner)
        self.active = set()
        self.sliders = set(owner.findChildren(QSlider))
        self.owners = {}
        for widget in [*owner.findChildren(QAbstractSpinBox), *self.sliders]:
            widget.installEventFilter(self)
            self.owners[widget] = widget
            if isinstance(widget, QAbstractSpinBox):
                editor = widget.lineEdit()
                editor.installEventFilter(self)
                self.owners[editor] = widget

    def eventFilter(self, widget, event):
        widget = self.owners.get(widget)
        if widget is None:
            return False
        kind = event.type()
        keys = (
            self.KEYS
            if widget in self.sliders
            else (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown)
        )
        start = (
            kind == QEvent.Type.KeyPress and event.key() in keys and not event.isAutoRepeat()
        ) or (
            widget not in self.sliders
            and kind == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        )
        end = (
            kind in (QEvent.Type.FocusOut, QEvent.Type.Hide)
            or (kind == QEvent.Type.KeyRelease and event.key() in keys and not event.isAutoRepeat())
            or (
                widget not in self.sliders
                and kind == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
            )
        )
        if start and widget not in self.active:
            self.active.add(widget)
            self.dragging.emit(True)
        elif end and widget in self.active:
            self.active.remove(widget)
            self.dragging.emit(False)
            self.committed.emit()
        return False


class GestureGuard(QObject):
    """Balance custom mouse gestures when their panel hides or loses the grab."""

    def __init__(self, widget):
        super().__init__(widget)
        widget.installEventFilter(self)

    def eventFilter(self, widget, event):
        if event.type() in (
            QEvent.Type.Hide,
            QEvent.Type.UngrabMouse,
            QEvent.Type.WindowDeactivate,
        ):
            widget.mouseReleaseEvent(None)
        return False


class PreviewQuality:
    """Adapt frame cadence, never reduce pixel resolution to meet a deadline."""

    FRAME_SECONDS = 1 / 30

    def __init__(self):
        self.ema = None

    def record(self, seconds):
        self.ema = seconds if self.ema is None else 0.6 * self.ema + 0.4 * seconds

    def delay_ms(self, elapsed):
        return max(0, round((self.FRAME_SECONDS - elapsed) * 1000))


class PreviewBuffers:
    """Bounded original-pixel stage cache for the visible region."""

    def __init__(self):
        self.cache = engine.RenderCache(max_bytes=256 * 2**20, alpha_bytes=64 * 2**20)


@dataclass
class PreviewFrame:
    rgb: np.ndarray
    display_rgb: np.ndarray
    image: QImage
    before: object
    clipping: object
    overlay: object
    histogram: list
    navigator: object
    original: object
    seconds: float
    detail: object = None
    detail_only: bool = False
    region_rect: object = None


def prepare_region(holder, request, view, previous_rgb):
    """At high zoom, update original pixels only where they are visible.

    The offscreen whole-frame preview and its histogram refine after release;
    during the gesture the histogram represents the rendered visible region.
    """
    import threading
    from . import viewport

    started = time.perf_counter()
    results = viewport.render(holder, request, threading.Event())
    hist = [np.zeros(256, np.int64) for _ in range(3)]
    histogram_kind = 'edited' if any(kind == 'edited' for kind, *_ in results) else 'original'
    for kind, _, _, tiles in results:
        if kind != histogram_kind:
            continue
        for tile in tiles.values():
            rgb = viewport.tile_array(tile)[::3, ::3]
            for c in range(3):
                hist[c] += np.bincount(rgb[..., c].ravel(), minlength=256)
    return PreviewFrame(
        previous_rgb,
        None,
        None,
        None,
        None,
        None,
        hist,
        None,
        None,
        time.perf_counter() - started,
        (view, results),
        True,
        request['pixel_rect'],
    )


def _rgba(data):
    return QImage(
        data.data, data.shape[1], data.shape[0], data.strides[0], QImage.Format.Format_RGBA8888
    ).copy()


def prepare(source, edits, backend, cache, detail_scale, original, options, interactive=False):
    """Render the exact recipe at the chosen scale and prepare immutable UI data."""
    started = time.perf_counter()
    final, comparing, split, shadows, highlights, mask = options
    result = engine.process(
        source, edits, backend, apply_crop=False, detail_scale=detail_scale, cache=cache
    )
    # Only the first ordinary frame needs to establish the full preview reference.
    new_original = None
    if original is None:
        original = np.clip(engine.to_srgb(develop.apply(source, edits['develop'])), 0, 1)
        if not interactive:
            new_original = original
    elif original.shape != result.shape:
        import cv2

        original = cv2.resize(original, result.shape[1::-1], interpolation=cv2.INTER_AREA)
    shown = original if comparing else result
    display = engine.crop_rotate(shown, edits) if final else shown
    before = engine.crop_rotate(original, edits) if final else original
    clipping = None
    if (shadows or highlights) and not comparing:
        data = np.zeros((*display.shape[:2], 4), np.uint8)
        maximum = np.max(display, axis=2)
        if shadows:
            data[maximum <= 0.005] = [75, 134, 255, 210]
        if highlights:
            data[maximum >= 0.995] = [255, 75, 90, 210]
        clipping = _rgba(data)
    overlay = None
    if mask is not None and not comparing and not split:
        geometry = {k: v for k, v in mask.items() if k not in ('adjustments', 'name', 'enabled')}
        key = engine._key('overlay', geometry, result.shape, edits['develop'])
        alpha = cache.alpha(
            key,
            lambda: engine.mask_alpha(
                mask, result.shape, original if mask['kind'] == 'luminance' else None
            ),
        )
        if final:
            alpha = engine.crop_rotate(alpha, edits)
        data = np.zeros((*alpha.shape, 4), np.uint8)
        data[..., :3] = [155, 122, 255]
        data[..., 3] = np.clip(alpha * 105, 0, 105).astype(np.uint8)
        overlay = _rgba(data)
    edited_display = engine.crop_rotate(result, edits) if final else result
    hist_source = engine.resize_limit(edited_display, 500) if interactive else edited_display
    values = (np.clip(hist_source[::3, ::3], 0, 1) * 255).astype(np.uint8)
    histogram = [np.bincount(values[..., c].ravel(), minlength=256) for c in range(3)]
    image = qimage(display)
    before_image = qimage(before) if split and not comparing else None
    navigator = None if interactive else qimage(engine.resize_limit(edited_display, 180))
    return PreviewFrame(
        result,
        display,
        image,
        before_image,
        clipping,
        overlay,
        histogram,
        navigator,
        new_original,
        time.perf_counter() - started,
    )
