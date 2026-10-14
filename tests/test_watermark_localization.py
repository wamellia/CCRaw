"""Newly selected properties localize while custom watermark text stays literal."""

from ccraw import i18n
from ccraw.watermark_graph import PipelineEditor


def test_preview_renders_at_physical_pixel_size_without_double_scaling(app, monkeypatch):
    import numpy as np
    from test_watermark_template_editor import Owner
    from ccraw import watermark
    from ccraw.watermark_dialog import WatermarkEditor

    owner = Owner()
    widget = WatermarkEditor(owner, compact=True)
    widget._preview_timer.stop()
    widget.preview.resize(240, 180)
    monkeypatch.setattr(widget.preview, 'devicePixelRatioF', lambda: 2.0)
    requested = []
    received = []
    original_set_pixmap = widget.preview.setPixmap

    def set_pixmap(pixmap):
        from PySide6.QtGui import QPixmap

        received.append(QPixmap(pixmap))
        original_set_pixmap(pixmap)

    monkeypatch.setattr(widget.preview, 'setPixmap', set_pixmap)

    def preview(rgb, settings, photo, **kwargs):
        requested.append(kwargs.get('limit'))
        return np.ones((240, 480, 3), np.float32)

    monkeypatch.setattr(watermark, 'preview', preview)
    widget._render_preview()
    prepare, done, _ = owner.jobs[-1]
    done(prepare())
    assert requested == [480]
    # QLabel.pixmap() normalizes its returned icon to the actual screen DPR;
    # inspect the asset supplied to the widget for this simulated 2x display.
    assert received[-1].devicePixelRatioF() == 2.0
    assert received[-1].deviceIndependentSize().width() <= widget.preview.width()
    widget.close()
    owner.close()


def test_new_properties_follow_language_and_keep_custom_text(app):
    previous = i18n.language()
    i18n.set_language('en')
    try:
        widget = PipelineEditor(
            [
                {'processor_name': 'shadow', 'shadow_radius': 20, 'shadow_color': 'black'},
                {
                    'processor_name': 'multi_rich_text',
                    'height': 20,
                    'text_segments': [
                        {'text': '曝光 Contrast — 我的标题', 'is_bold': False, 'color': 'black'}
                    ],
                },
            ]
        )
        widget.select_path((0,))
        assert widget.properties.presence_widgets['shadow_radius'].text() == 'Shadow radius'
        widget.select_path((1, 'text_segments', 0))
        assert widget.properties.field_widgets['text'].text() == '曝光 Contrast — 我的标题'
        i18n.localize_window(widget, 'en')
        item = widget._items[(1, 'text_segments', 0)]
        assert '曝光 Contrast — 我的标题' in item.text(0)
        widget.close()
    finally:
        i18n.set_language(previous)
