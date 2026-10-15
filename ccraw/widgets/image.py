from __future__ import annotations
import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPixmap, QPainter, QPainterPath


def rounded_pixmap(image, radius=8):
    """GUI-thread thumbnail masking; source pixels remain untouched."""
    source = QPixmap.fromImage(image)
    result = QPixmap(source.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(result.rect()), radius, radius)
    painter.setClipPath(clip)
    painter.drawPixmap(0, 0, source)
    painter.end()
    return result


def qimage(rgb):
    from .. import large_image, cpu_ops

    if rgb.nbytes <= large_image.MAP_BYTES:
        data = cpu_ops.rgb8(rgb)
    else:
        data = large_image.allocate(rgb.shape, np.uint8)
        for y, block in large_image.strips(rgb):
            data[y : y + len(block)] = cpu_ops.rgb8(block)
    # Detach into Qt's native raster format in the worker. Painting/scaling an
    # RGB888 image otherwise repeatedly converts three-byte pixels on the GUI.
    return QImage(
        data.data, data.shape[1], data.shape[0], data.strides[0], QImage.Format.Format_RGB888
    ).convertToFormat(QImage.Format.Format_RGB32)
