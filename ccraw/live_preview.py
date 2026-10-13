"""Full-quality interactive previews and original-pixel visible-region rendering.

The GUI owns the quality controller. Image preparation runs in the single image
worker, including QImage conversion (QPixmap remains on the GUI thread).
"""

from dataclasses import dataclass
from collections import OrderedDict
from pathlib import Path
import copy
import threading
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

    FRAME_SECONDS = 1 / 60

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


class FrameCancellation:
    """Only a changed gesture/document expires a frame; input never starves it."""

    def __init__(self, predicate):
        self.predicate = predicate

    def is_set(self):
        return bool(self.predicate())

    def check(self):
        if self.is_set():
            raise InterruptedError('预览已过期')


class DecodedPreviews:
    """Bounded input LRU; preference changes never wait for image decoding."""

    def __init__(self, max_bytes=96 * 2**20):
        self._lock = threading.RLock()
        self._max_bytes = max(0, int(max_bytes))
        self._epoch = 0
        self._items = OrderedDict()

    @property
    def max_bytes(self):
        with self._lock:
            return self._max_bytes

    @max_bytes.setter
    def max_bytes(self, value):
        with self._lock:
            self._max_bytes = max(0, int(value))
            self._epoch += 1
            while self._items and self.bytes > self._max_bytes:
                self._items.popitem(last=False)

    @property
    def bytes(self):
        with self._lock:
            return sum(source.nbytes for source, _ in self._items.values())

    def clear(self):
        with self._lock:
            self._epoch += 1
            self._items.clear()

    def load(self, path, limit):
        path = Path(path).resolve()
        before = path.stat()
        key = (str(path), before.st_mtime_ns, before.st_size, limit)
        with self._lock:
            epoch = self._epoch
            item = self._items.pop(key, None)
        if item is None:
            item = engine.load_image(path, preview_limit=limit)
        after = path.stat()
        with self._lock:
            if (
                epoch == self._epoch
                and after.st_mtime_ns == before.st_mtime_ns
                and after.st_size == before.st_size
            ):
                for old in list(self._items):
                    if old[0] == str(path):
                        self._items.pop(old)
                if item[0].nbytes <= self._max_bytes:
                    while self._items and self.bytes + item[0].nbytes > self._max_bytes:
                        self._items.popitem(last=False)
                    self._items[key] = item
        return item[0], copy.deepcopy(item[1])


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


def prepare_region(holder, request, view, previous_rgb, cancel=None):
    """At high zoom, update original pixels only where they are visible.

    The offscreen whole-frame preview and its histogram refine after release;
    during the gesture the histogram represents the rendered visible region.
    """
    import threading
    from . import viewport

    started = time.perf_counter()
    results = viewport.render(holder, request, cancel or threading.Event())
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


def prepare(
    source,
    edits,
    backend,
    cache,
    detail_scale,
    original,
    options,
    interactive=False,
    color_settings=None,
    check=None,
):
    """Render the exact recipe at the chosen scale and prepare immutable UI data."""
    started = time.perf_counter()
    check = check or (lambda: None)
    final, comparing, split, shadows, highlights, mask = options
    result = engine.process(
        source,
        edits,
        backend,
        apply_crop=False,
        detail_scale=detail_scale,
        cache=cache,
        check=check,
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
    check()
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
        if mask['kind'] == 'point-range':
            alpha = point_range(source, edits, mask['point'], backend, detail_scale, cache=cache)
        else:
            geometry = {
                k: v for k, v in mask.items() if k not in ('adjustments', 'name', 'enabled')
            }
            key = engine._key('overlay', geometry, result.shape, edits['develop'])
            alpha = cache.alpha(
                key,
                lambda: engine.mask_alpha(
                    mask, result.shape, original if engine._has_luminance(mask) else None
                ),
            )
        from .imaging.professional import warp_mask

        alpha = warp_mask(alpha, edits.get('professional', {}))
        if final:
            alpha = engine.crop_rotate(alpha, edits)
        data = np.zeros((*alpha.shape, 4), np.uint8)
        data[..., :3] = [155, 122, 255]
        data[..., 3] = np.clip(alpha * 105, 0, 105).astype(np.uint8)
        overlay = _rgba(data)
    edited_display = engine.crop_rotate(result, edits) if final else result
    check()
    hist_source = engine.resize_limit(edited_display, 500) if interactive else edited_display
    values = (np.clip(hist_source[::3, ::3], 0, 1) * 255).astype(np.uint8)
    histogram = [np.bincount(values[..., c].ravel(), minlength=256) for c in range(3)]
    from .imaging.display import convert

    image = qimage(convert(display, color_settings))
    check()
    before_image = qimage(convert(before, color_settings)) if split and not comparing else None
    navigator = (
        None
        if interactive
        else qimage(convert(engine.resize_limit(edited_display, 180), color_settings))
    )
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


def point_range(source, edits, point, backend, detail_scale, cache=None):
    """Compute the point's pre-edit reference on the image worker, without a recipe mutation."""
    import copy
    from .domain.professional import defaults
    from .imaging.professional import point_mask

    clean = copy.deepcopy(edits)
    pro = clean['professional']
    neutral = defaults()
    for key in ('point_color', 'profile', 'geometry', 'lensfun', 'bw_mixer'):
        pro[key] = neutral[key]
    pro['optics'].update(distortion=0, ca_red=0, ca_blue=0)
    clean.update(monochrome=False, masks=[], effects={})
    clean['grading'] = {}
    space = 'Rec.2020' if clean.get('process', 1) == 2 else 'sRGB'
    key = engine._key(clean, space, detail_scale)

    def reference():
        return engine.render_output(
            source, clean, space, backend, apply_crop=False, detail_scale=detail_scale
        )

    rgb = reference() if cache is None else cache.get('point-range-reference', key, reference)
    selection = {
        k: point.get(k)
        for k in ('rgb', 'hue_range', 'saturation_range', 'luminance_range', 'feather')
    }
    return (
        point_mask(rgb, point, space)
        if cache is None
        else cache.alpha(
            engine._key('point-overlay', key, selection), lambda: point_mask(rgb, point, space)
        )
    )
