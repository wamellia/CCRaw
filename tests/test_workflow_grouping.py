"""Grouped workflow controls preserve real transfer/export/selection behavior."""

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QGroupBox


def test_sync_group_selection_preserves_other_groups_and_safe_defaults(app):
    from ccraw.ui.sync_dialog import SyncDialog
    from ccraw.domain.sync import DEFAULT, SECTIONS

    dialog = SyncDialog()
    assert dialog.sections() == list(DEFAULT)
    assert set(dialog.controls) == set(SECTIONS)
    untouched = {
        k: w.isChecked() for k, w in dialog.controls.items() if k not in dialog.group_keys['color']
    }
    dialog.group_checks['color'].setCheckState(Qt.CheckState.Checked)
    assert all(dialog.controls[k].isChecked() for k in dialog.group_keys['color'])
    assert all(dialog.controls[k].isChecked() == v for k, v in untouched.items())
    dialog.controls['hsl'].setChecked(False)
    assert dialog.group_checks['color'].checkState() == Qt.CheckState.PartiallyChecked
    dialog.restore_defaults()
    assert dialog.sections() == list(DEFAULT)
    assert not dialog.controls['crop'].isChecked() and not dialog.controls['masks'].isChecked()
    dialog.close()


def test_sync_group_click_selects_all_once_and_partial_is_display_only(app):
    from ccraw.ui.sync_dialog import SyncDialog

    dialog = SyncDialog()
    dialog.group_checks['local'].click()
    assert all(dialog.controls[key].isChecked() for key in dialog.group_keys['local'])
    dialog.group_checks['local'].click()
    assert not any(dialog.controls[key].isChecked() for key in dialog.group_keys['local'])
    dialog.controls['masks'].setChecked(True)
    assert dialog.group_checks['local'].checkState() == Qt.CheckState.PartiallyChecked
    dialog.group_checks['local'].click()
    assert all(dialog.controls[key].isChecked() for key in dialog.group_keys['local'])
    dialog.close()


def test_export_groups_keep_precision_and_disable_unused_sharpening(app):
    from ccraw.ui.professional_export import ExportSettings

    settings = ExportSettings()
    settings.set_format('tiff')
    settings.color_space.setCurrentText('Display P3')
    settings.bit_depth.setCurrentText('16')
    settings.long_edge.setValue(2560)
    settings.naming.setText('{stem}-{seq:03d}')
    groups = settings.findChildren(QGroupBox)
    assert len(groups) == 4
    color_group = settings.color_space.parentWidget()
    assert settings.bit_depth.parentWidget() == color_group
    assert settings.long_edge.parentWidget() != color_group
    assert not settings.sharpening_amount.isEnabled()
    settings.output_sharpening.setCurrentIndex(1)
    assert settings.sharpening_amount.isEnabled()
    options = settings.options()
    assert options['color_space'] == 'display_p3' and options['bit_depth'] == 16
    assert options['long_edge'] == 2560 and options['file_name_template'] == '{stem}-{seq:03d}'
    settings.close()


def test_mask_empty_state_actions_and_geometry_are_contextual(app):
    from ccraw import model
    from ccraw.ui.mask_components import MaskComponentsDialog

    radial = model.new_mask('radial', 1)
    brush = model.new_mask('brush', 2)
    originals = copy.deepcopy([radial, brush])
    dialog = MaskComponentsDialog(radial, [radial, brush])
    dialog.show()
    app.processEvents()
    assert dialog.empty_state.isVisible()
    assert not dialog.remove_button.isEnabled() and not dialog.up_button.isEnabled()
    dialog.add_button.click()
    assert not dialog.empty_state.isVisible()
    assert dialog.geometry_group.isVisible() and dialog.remove_button.isEnabled()
    dialog.sources_combo.setCurrentIndex(1)
    dialog.add_button.click()
    assert not dialog.geometry_group.isVisible()
    assert dialog.up_button.isEnabled() and not dialog.down_button.isEnabled()
    dialog.remove_button.click()
    assert len(dialog.parts) == 1 and dialog.geometry_group.isVisible()
    assert [radial, brush] == originals
    dialog.reject()


def test_batch_export_keeps_confirmation_visible_and_format_label_honest(app, tmp_path):
    from ccraw.batch_export import BatchExportDialog

    dialog = BatchExportDialog(None, 4, tmp_path)
    dialog.resize(620, 470)
    dialog.show()
    app.processEvents()
    assert dialog.height() <= 520
    assert dialog.content_scroll.verticalScrollBar().maximum() > 0
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert save.mapTo(dialog, save.rect().bottomRight()).y() < dialog.height()
    dialog.format.setCurrentIndex(1)
    dialog.professional.color_space.setCurrentText('Display P3')
    dialog.professional.bit_depth.setCurrentText('16')
    assert 'sRGB' not in dialog.format.currentText() and '8-bit' not in dialog.format.currentText()
    assert dialog.options()['preset']['bit_depth'] == 16
    assert dialog.options()['preset']['color_space'] == 'display_p3'
    dialog.close()


def test_whole_photo_reset_remains_available_and_undoable_in_menu(window):
    from ccraw.scheduler import Activity
    from test_ui import wait_until

    w = window
    w.controls['exposure'].spin.setValue(1)
    w.controls['saturation'].spin.setValue(30)
    w.commit()
    before = copy.deepcopy(w.edits)
    assert w.menu_reset.isEnabled()
    w.menu_reset.trigger()
    assert w.edits['adjustments']['exposure'] == 0
    assert w.edits['adjustments']['saturation'] == 0
    w.undo(-1)
    assert w.edits == before
    w.work.begin(Activity.AI)
    try:
        w.refresh_access()
        assert not w.menu_reset.isEnabled()
    finally:
        w.work.end(Activity.AI)
        w.refresh_access()
    wait_until(lambda: not w.jobs and not w.timer.isActive())


def test_switching_sections_reveals_heading_after_previous_content_collapses(window, app):
    w = window
    w.resize(1080, 700)
    w.select_editor_section('optics')
    app.processEvents()
    w.select_editor_section('geometry')
    app.processEvents()
    header = w.editor_groups.sections['geometry'].header
    assert header.visibleRegion().boundingRect().height() == header.height()
