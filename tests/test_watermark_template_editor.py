"""Typed watermark graph editing, template routing and stale-preview safety."""

from pathlib import Path
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget
from ccraw import watermark, model
from ccraw.watermark_dialog import WatermarkEditor
from ccraw.watermark_controls import ArrayEditor, DimensionEditor, ResourceEditor, TEXT_SOURCES


class Owner(QWidget):
    def __init__(self):
        super().__init__()
        self.source = np.ones((90, 120, 3), dtype=np.float32)
        self.rendered = self.source
        self.edits = model.recipe()
        self.edits['watermark'] = watermark.defaults()
        self.info = {'photo': {}, 'width': 120, 'height': 90}
        self.document_token = 1
        self.closing = False
        self.jobs = []

    def job(self, prepare, done, failed):
        self.jobs.append((prepare, done, failed))


@pytest.fixture
def editor(app):
    owner = Owner()
    widget = WatermarkEditor(owner, compact=True)
    widget._preview_timer.stop()
    yield widget, owner
    owner.closing = True
    owner.jobs.clear()
    widget.close()
    owner.close()


def test_layout_type_all_templates_and_default_without_ordinary_controls(editor):
    widget, owner = editor
    assert widget.layout_type.currentData() == 'standard2'
    assert {widget.layout_type.itemData(i) for i in range(widget.layout_type.count())} >= set(
        watermark.TEMPLATES
    )
    assert not hasattr(widget, 'preset') and not hasattr(widget, 'sides')
    emitted = []
    widget.changed.connect(emitted.append)
    for key in watermark.TEMPLATES:
        widget.layout_type.setCurrentIndex(widget.layout_type.findData(key))
        assert widget.values()['template'] == key
        assert widget.values()['pipeline'] == watermark.template_pipeline(key)
    assert emitted
    settings = widget.values()
    settings['metadata']['Artist'] = 'A photographer'
    settings['logos']['left'] = ''
    widget.set_settings(settings)
    assert widget.values()['metadata']['Artist'] == 'A photographer'
    widget.enabled.setChecked(True)
    assert widget.values()['enabled'] is True


def test_nested_text_fields_segments_and_node_order(editor):
    widget, owner = editor
    graph = widget.pipeline_editor
    graph.set_pipeline(watermark.template_pipeline('standard2'))
    surface = next(
        i for i, node in enumerate(graph.values()) if node['processor_name'] == 'watermark'
    )
    graph.select_path((surface, 'left_top', 'text_segments', 0))
    fields = graph.properties.field_widgets
    fields['text'].setText('Camera {{exif.CameraModelName}}')
    fields['text'].editingFinished.emit()
    fields['is_bold'].setChecked(False)
    fields['height'].set_value('{{vh(4)}}', emit=True)
    segment = graph.values()[surface]['left_top']['text_segments'][0]
    assert segment['text'] == 'Camera {{exif.CameraModelName}}'
    assert not segment['is_bold']
    assert segment['height'] == '{{vh(4)}}'
    graph.add_segment()
    assert len(graph.values()[surface]['left_top']['text_segments']) == 2
    graph.properties.field_widgets['text'].setText('Second')
    graph.properties.field_widgets['text'].editingFinished.emit()
    graph.move_node(-1)
    assert graph.values()[surface]['left_top']['text_segments'][0]['text'] == 'Second'
    graph.remove_node()
    assert len(graph.values()[surface]['left_top']['text_segments']) == 1
    graph.select_path((0,))
    graph.add_node('gradient_color')
    added = graph.values()[-1]
    assert added['processor_name'] == 'gradient_color'
    assert {'start_color', 'end_color', 'direction', 'interpolate_method'} <= added.keys()
    graph.move_node(-1)
    assert graph.values()[-2]['processor_name'] == 'gradient_color'


def test_latest_settings_and_document_change_discard_running_preview(editor, app):
    widget, owner = editor
    widget._render_preview()
    assert widget._preview_running and len(owner.jobs) == 1
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    widget.enabled.setChecked(True)
    assert widget._preview_pending
    owner.jobs[0][1](image)
    assert not widget._preview_running
    assert widget.preview.pixmap().isNull()
    widget._preview_timer.stop()
    widget._render_preview()
    assert len(owner.jobs) == 2
    owner.document_token += 1
    owner.jobs[1][1](image)
    assert not widget._preview_running
    assert widget.preview.pixmap().isNull()
    widget._preview_timer.stop()


def test_no_photo_settings_sync_emits_changes_and_busy_state_recovers(editor):
    widget, owner = editor
    owner.rendered = None
    emitted = []
    widget.changed.connect(emitted.append)
    widget.enabled.setChecked(True)
    assert emitted[-1]['enabled'] is True
    owner.rendered = owner.source
    owner.job = lambda *_: (_ for _ in ()).throw(RuntimeError('Queue rejected'))
    widget._render_preview()
    assert not widget._preview_running
    assert 'Queue rejected' in widget.status.text()


def test_compact_and_full_keep_template_and_graph_controls_accessible(app):
    for compact, size in [(True, (340, 780)), (False, (1100, 800))]:
        owner = Owner()
        widget = WatermarkEditor(owner, compact=compact)
        widget.setWindowFlag(Qt.WindowType.Window, True)
        widget.resize(*size)
        widget.show()
        app.processEvents()
        assert widget.layout_type.isVisible()
        assert widget.pipeline_editor.tree.isVisible()
        assert widget.layout_type.width() >= 140
        assert widget.pipeline_editor.tree.width() <= widget.width()
        assert widget.minimumSizeHint().width() <= size[0]
        widget._preview_timer.stop()
        widget.close()
        owner.close()


@pytest.mark.parametrize('key', list(watermark.LAYOUT_TYPES))
def test_all_current_and_legacy_layouts_round_trip_through_visual_graph(editor, key):
    widget, owner = editor
    widget.layout_type.setCurrentIndex(widget.layout_type.findData(key))
    assert widget.values()['pipeline'] == watermark.template_pipeline(key)
    values = watermark.validate(widget.values())
    widget.set_settings(values)
    assert widget.values() == values


def test_metadata_sources_replace_text_without_writing_template_code(editor):
    widget, owner = editor
    graph = widget.pipeline_editor
    graph.set_pipeline(watermark.template_pipeline('custom_watermark'))
    graph.select_path((0, 'right_top'))
    properties = graph.properties
    assert set(properties.field_widgets) == {'text', 'color', 'is_bold'}
    sources = properties.source_widgets['text']
    assert {sources.itemData(i) for i in range(sources.count())} == set(TEXT_SOURCES)
    for key in ('Model', 'Param', 'Filename', 'GeoInfo', 'None'):
        sources.setCurrentIndex(sources.findData(key))
        assert graph.values()[0]['right_top']['text'] == TEXT_SOURCES[key]
    sources.setCurrentIndex(sources.findData('Custom'))
    properties.field_widgets['text'].setText('My camera')
    properties.field_widgets['text'].editingFinished.emit()
    assert graph.values()[0]['right_top']['text'] == 'My camera'


def test_nested_processor_can_change_type_add_segments_and_restore_after_removal(editor):
    widget, owner = editor
    graph = widget.pipeline_editor
    surface = next(
        i for i, node in enumerate(graph.values()) if node['processor_name'] == 'watermark'
    )
    path = (surface, 'left_bottom')
    graph.select_path(path)
    graph.replace_processor(path, 'multi_rich_text')
    assert graph.values()[surface]['left_bottom']['text_segments'][0]['text']
    graph.add_segment()
    assert len(graph.values()[surface]['left_bottom']['text_segments']) == 2
    graph.select_path(path)
    graph.remove_node()
    assert 'left_bottom' not in graph.values()[surface]
    graph.select_path((surface,))
    graph.properties.presence_widgets['left_bottom'].setChecked(True)
    graph.select_path(path)
    assert graph.values()[surface]['left_bottom']['processor_name'] == 'rich_text'


def test_all_processor_schemas_have_typed_visible_properties(editor):
    widget, owner = editor
    graph = widget.pipeline_editor
    for name, schema in watermark.PROCESSOR_DEFAULTS.items():
        graph.set_pipeline([])
        graph.add_node(name)
        properties = graph.properties
        assert set(properties.field_widgets) >= set(schema) - {
            'processor_name',
            'save_buffer',
            'center_height',
        }
        assert 'save_buffer' not in properties.field_widgets
        if name == 'gradient_color':
            assert properties.field_widgets['start_color'].__class__.__name__ == 'ColorEditor'
            properties.field_widgets['direction'].setCurrentText('radial')
            assert graph.values()[0]['direction'] == 'radial'
        if name == 'legacy_watermark':
            properties.field_widgets['font_size'].setCurrentText('3')
            properties.field_widgets['logo_position'].setCurrentText('right')
            properties.field_widgets['logo_enable'].setChecked(False)
            assert graph.values()[0]['font_size'] == 3
            assert graph.values()[0]['logo_position'] == 'right'
            assert graph.values()[0]['logo_enable'] is False


def test_coordinate_expressions_show_as_units_and_preserve_jinja_on_edit(app):
    control = ArrayEditor('{{[[0,0],[vh(5),vw(5)]]}}', 'offsets')
    assert control.raw_expression is None
    dimensions = control.findChildren(DimensionEditor)
    assert dimensions[2].unit.currentData() == 'vh'
    dimensions[2].set_value('{{vh(8)}}', emit=True)
    assert 'vh(8)' in control.value()
    assert 'vw(5)' in control.value()
    context = watermark.template_context(watermark.defaults(), {}, (1000, 2000, 3))
    evaluated = watermark._evaluate(control.value(), context, 1, 'offsets')
    import json

    assert json.loads(evaluated) == [[0, 0], [80, 100]]
    mixed = ArrayEditor('[0,-{{vh(3)}}]', 'offset')
    dimensions = mixed.findChildren(DimensionEditor)
    assert dimensions[1].unit.currentData() == 'vh'
    assert dimensions[1].number.value() == -3
    assert json.loads(watermark._evaluate(mixed.value(), context, 1, 'offset')) == [0, -30]
    complex_expression = '{{[[0,0],[vh(4) + 17,vw(2)]]}}'
    fallback = ArrayEditor(complex_expression, 'offsets')
    assert fallback.raw_expression == complex_expression
    assert fallback.value() == complex_expression
    fallback.use_rows()
    assert fallback.value() == '[[0, 0]]'
    for widget in (control, mixed, fallback):
        widget.close()


def test_input_resources_are_visual_lists_not_json(editor):
    widget, owner = editor
    graph = widget.pipeline_editor
    graph.set_pipeline([{'processor_name': 'image', 'path': ['source', 'logo:left']}])
    resource = graph.properties.field_widgets['path']
    assert isinstance(resource, ResourceEditor)
    resource.controls[1].setCurrentText('logo:center')
    assert graph.values()[0]['path'] == ['source', 'logo:center']


def test_preview_uses_filename_and_folder_and_captured_final_dimensions(editor, monkeypatch):
    widget, owner = editor
    owner.source_path = Path('E:/Photography/Autumn/image.jpg')
    recorded = []
    monkeypatch.setattr(
        watermark,
        'preview',
        lambda image, settings, photo, **kw: recorded.append((photo, kw)) or image,
    )
    widget._render_preview()
    # A different document can arrive while the queued prepare waits.
    owner.info['width'] = 999
    owner.jobs[-1][0]()
    assert recorded[0][0]['filename'] == 'image.jpg'
    assert recorded[0][0]['folder'] == 'Autumn'
    assert recorded[0][1]['final_shape'] == (90, 120, 3)


def test_compact_expanded_sections_and_all_processor_forms_stay_inside_viewport(app):
    owner = Owner()
    widget = WatermarkEditor(owner, compact=True)
    widget.setWindowFlag(Qt.WindowType.Window, True)
    widget.resize(340, 780)
    widget.show()
    widget.identity_section.button.setChecked(True)
    widget.metadata_section.button.setChecked(True)
    widget.metadata.key.setCurrentText('FocalLengthIn35mmFormat')
    widget.metadata.add()
    graph = widget.pipeline_editor
    for name in watermark.PROCESSOR_DEFAULTS:
        graph.set_pipeline([])
        graph.add_node(name)
        app.processEvents()
        assert widget.form_box.width() <= widget.scroll.viewport().width(), name
        assert graph.width() <= widget.scroll.viewport().width(), name
    widget._preview_timer.stop()
    owner.closing = True
    owner.jobs.clear()
    widget.close()
    owner.close()
