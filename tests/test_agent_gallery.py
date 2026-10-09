import numpy as np
import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from ccraw.photo_agent.store import Project
from ccraw.photo_agent.ui import AgentWindow
from ccraw.ui.theme import apply_theme, tokens


def gallery_project(tmp_path, dimensions):
    project = Project.create(tmp_path / 'gallery.ccrawagent', '照片')
    for index, size in enumerate(dimensions):
        source = tmp_path / f'{index}-完整文件名用于验证照片标题不被硬截断-2026年夏天的旅行照片.png'
        Image.new('RGB', size, (240, 20, 20)).save(source)
        photo_id = project.import_paths([source])[0]
        preview = project.directory('previews') / f'{photo_id}.png'
        preview.write_bytes(source.read_bytes())
        project.update_photo(
            photo_id, facts={'preview': preview.name, 'width': size[0], 'height': size[1]}
        )
    return project


@pytest.mark.parametrize('dimensions', [(2400, 1200), (720, 1440), (1200, 1200), (3840, 320)])
def test_gallery_renders_complete_photo_at_original_aspect_ratio(app, tmp_path, dimensions):
    window = AgentWindow(gallery_project(tmp_path, [dimensions]))
    try:
        window.show()
        QTest.qWait(60)
        gallery = window.gallery
        item = gallery.item(0)
        item.setSelected(True)
        image = gallery.viewport().grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        pixels = np.frombuffer(image.bits(), dtype=np.uint8).reshape(
            image.height(), image.bytesPerLine() // 4, 4
        )[:, : image.width()]
        red = (pixels[:, :, 0] > 120) & (pixels[:, :, 1] < 90) & (pixels[:, :, 2] < 100)
        rows, columns = np.where(red)
        assert len(rows), 'The selected photo must remain visible'
        width, height = np.ptp(columns) + 1, np.ptp(rows) + 1
        # Rasterization can move each edge by one pixel, especially on thin panoramas.
        ratio = dimensions[0] / dimensions[1]
        assert abs(width - height * ratio) <= 2 * max(1, ratio)
        assert width > 30 and height > 10
        center = pixels[(rows.min() + rows.max()) // 2, (columns.min() + columns.max()) // 2, :3]
        assert tuple(center) == (240, 20, 20), 'Selection must not tint the photo itself'
        assert item.text() == '0-完整文件名用于验证照片标题不被硬截断-2026年夏天的旅行照片.png'
    finally:
        window.close()


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_gallery_keeps_all_four_image_corners_visible(app, tmp_path, mode):
    palette, stylesheet, font = app.palette(), app.styleSheet(), app.font()
    apply_theme(app, mode)
    project = gallery_project(tmp_path, [(300, 200)])
    photo = project.photos()[0]
    preview = project.directory('previews') / photo['facts']['preview']
    image = Image.new('RGB', (300, 200), (100, 100, 100))
    draw = ImageDraw.Draw(image)
    corners = [
        ((0, 0, 35, 35), (255, 0, 0)),
        ((264, 0, 299, 35), (0, 255, 0)),
        ((0, 164, 35, 199), (0, 0, 255)),
        ((264, 164, 299, 199), (255, 255, 0)),
    ]
    for rectangle, color in corners:
        draw.rectangle(rectangle, fill=color)
    image.save(preview)
    window = AgentWindow(project)
    try:
        window.show()
        QTest.qWait(60)
        gallery = window.gallery
        card = gallery.visualItemRect(gallery.item(0))
        unselected = gallery.viewport().grab().toImage()
        ratio = unselected.devicePixelRatio()
        background = unselected.pixelColor(
            round((card.left() + 28) * ratio), round((card.top() + 10) * ratio)
        )
        assert background == QColor(tokens().panel), 'Photo cards must follow the application theme'
        window.gallery.item(0).setSelected(True)
        rendered = (
            window.gallery.viewport()
            .grab()
            .toImage()
            .convertToFormat(QImage.Format.Format_RGBA8888)
        )
        pixels = np.frombuffer(rendered.bits(), np.uint8).reshape(
            rendered.height(), rendered.bytesPerLine() // 4, 4
        )[:, : rendered.width(), :3]
        for _, color in corners:
            assert np.count_nonzero(np.all(pixels == color, axis=2)) > 20
    finally:
        window.close()
        app.setPalette(palette)
        app.setStyleSheet(stylesheet)
        app.setFont(font)


def test_gallery_reflows_without_clipping_and_preserves_selection(app, tmp_path):
    project = gallery_project(tmp_path, [(240, 120)] * 16)
    window = AgentWindow(project)
    try:
        window.show()
        item = window.gallery.item(2)
        item.setSelected(True)
        selected = window.selected_ids()
        first_row_widths = []
        for size in [(1180, 780), (1570, 940)]:
            window.resize(*size)
            QTest.qWait(80)
            gallery = window.gallery
            rects = [gallery.visualItemRect(gallery.item(i)) for i in range(gallery.count())]
            row = [rect for rect in rects if rect.top() == rects[0].top()]
            assert len(row) >= 2
            assert all(
                rect.left() >= 0 and rect.right() < gallery.viewport().width() for rect in row
            )
            assert gallery.viewport().width() - row[-1].right() <= 24
            assert gallery.horizontalScrollBar().maximum() == 0
            assert window.selected_ids() == selected
            first_row_widths.append(row[0].width())
        assert first_row_widths[0] != first_row_widths[1]
        window.refresh_gallery()
        assert window.selected_ids() == selected
        assert window.gallery.item(2).data(Qt.ItemDataRole.UserRole) in selected
    finally:
        window.close()
