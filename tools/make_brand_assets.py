"""Render repository-owned vector artwork for Windows and source builds."""

from pathlib import Path
from PIL import Image
from PySide6.QtCore import QByteArray
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
<rect width="512" height="512" rx="112" fill="#F5F5F7"/>
<path d="M286 136a139 139 0 1 0 0 240" fill="none" stroke="#323234" stroke-width="42" stroke-linecap="round"/>
<path d="M377 176a96 96 0 1 0 0 160" fill="none" stroke="#007AFF" stroke-width="34" stroke-linecap="round"/>
<circle cx="318" cy="256" r="21" fill="#323234"/>
</svg>"""


def render(size=1024):
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    QSvgRenderer(QByteArray(SVG.encode())).render(painter)
    painter.end()
    return image


def main():
    assets = ROOT / 'assets'
    assets.mkdir(exist_ok=True)
    (assets / 'ccraw.svg').write_text(SVG, encoding='utf-8')
    image = render()
    image.save(str(assets / 'ccraw.png'))
    with Image.open(assets / 'ccraw.png') as source:
        source.save(
            assets / 'ccraw.ico',
            sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
    package = ROOT / 'ccraw/resources'
    package.mkdir(exist_ok=True)
    for name in ('ccraw.svg', 'ccraw.ico', 'ccraw.png'):
        (package / name).write_bytes((assets / name).read_bytes())


if __name__ == '__main__':
    main()
