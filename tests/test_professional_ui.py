"""Visible professional controls and actual encoder integration."""

import threading
import hashlib

import numpy as np
import pytest
import tifffile
from PySide6.QtTest import QSignalSpy


def test_professional_panel_emits_valid_full_recipe_and_restores_silently(app):
    from ccraw.ui.professional_panel import ProfessionalPanel
    from ccraw.domain import professional

    panel = ProfessionalPanel()
    panel.show()
    changed = QSignalSpy(panel.changed)
    panel.controls['geometry', 'vertical'].spin.setValue(25)
    assert changed.count() == 1
    state = changed.at(0)[0]
    assert professional.validate(state)['geometry']['vertical'] == 25
    panel.setRecipe({'professional': professional.defaults()})
    assert panel.controls['geometry', 'vertical'].spin.value() == 0
    assert changed.count() == 1
    assert all(
        panel.controls[group, key].default_value == spec[3]
        for group, fields in professional.CONTROLS.items()
        for key, spec in fields.items()
    )
    panel.close()


def test_point_samples_are_frozen_editable_and_bounded(app):
    from ccraw.ui.professional_panel import ProfessionalPanel

    panel = ProfessionalPanel()
    sample = [0.2, 0.4, 0.7]
    panel.add_color_sample(sample)
    sample[0] = 0.9
    assert panel.values()['point_color'][0]['rgb'] == [0.2, 0.4, 0.7]
    panel.point_controls['hue'].spin.setValue(20)
    assert panel.values()['point_color'][0]['hue'] == 20
    for _ in range(15):
        panel.add_color_sample([0.1, 0.2, 0.3])
    assert not panel.sample_button.isEnabled()
    with pytest.raises(ValueError):
        panel.add_color_sample([0.2, 0.3, 0.4])
    panel.delete_color_sample()
    assert len(panel.values()['point_color']) == 15 and panel.sample_button.isEnabled()
    panel.close()


def test_professional_panel_red_eye_and_bw_live_controls(app):
    from ccraw.ui.professional_panel import ProfessionalPanel

    panel = ProfessionalPanel()
    panel.add_red_eye([0.3, 0.6])
    panel.eye_radius.spin.setValue(0.04)
    assert panel.values()['red_eye'][0]['radius'] == 0.04
    panel.bw_controls[0].spin.setValue(50)
    assert panel.values()['bw_mixer'][0] == 50
    panel.flip_x.setChecked(True)
    assert panel.values()['geometry']['flip_x']
    panel.close()


def test_profile_import_validates_before_editing_recipe(app, tmp_path):
    from ccraw.ui.professional_panel import ProfessionalPanel

    panel = ProfessionalPanel()
    path = tmp_path / 'identity.cube'
    path.write_text('LUT_1D_SIZE 2\n0 0 0\n1 1 1\n', encoding='ascii')
    panel.import_profile(path)
    assert panel.values()['profile']['path'] == str(path.resolve())
    bad = tmp_path / 'broken.cube'
    bad.write_text('garbage', encoding='ascii')
    with pytest.raises(ValueError):
        panel.import_profile(bad)
    assert panel.values()['profile']['path'] == str(path.resolve())
    panel.close()


def test_professional_export_dialog_depth_space_presets_and_metadata(app, tmp_path):
    from ccraw.ui.professional_export import ProfessionalExportDialog

    dialog = ProfessionalExportDialog(
        None, str(tmp_path / 'image.nef'), preset_path=tmp_path / 'presets.json'
    )
    dialog.format.setCurrentIndex(2)
    dialog.color_space.setCurrentText('Display P3')
    dialog.bit_depth.setCurrentText('16')
    dialog.naming.setText('{stem}-{seq:03d}')
    dialog.metadata_policy.setCurrentIndex(2)
    options = dialog.options()
    assert options['format'] == 'tiff' and options['bit_depth'] == 16
    assert options['color_space'] == 'display_p3' and options['metadata_policy'] == 'copyright'
    dialog.save_preset('Print')
    dialog.format.setCurrentIndex(0)
    assert dialog.bit_depth.currentText() == '8' and not dialog.bit_depth.isEnabled()
    dialog.load_preset('Print')
    assert dialog.options()['bit_depth'] == 16
    dialog.close()


def test_batch_professional_output_preserves_16bit_and_output_profile(tmp_path):
    from ccraw import batch_export, model
    from ccraw.imaging import colorspaces
    from PIL import Image

    source = tmp_path / 'source.png'
    Image.new('RGB', (17, 11), (160, 90, 30)).save(source)
    destination = tmp_path / 'out'
    destination.mkdir()
    options = dict(
        folder=str(destination),
        extension='.tif',
        long_edge=None,
        quality=95,
        preset=dict(
            format='tiff', bit_depth=16, color_space='display_p3', file_name_template='{stem}-wide'
        ),
    )
    recipe = model.recipe()
    results = batch_export.run(
        [(str(source), recipe, True)],
        options,
        __import__('ccraw.engine', fromlist=['Backend']).Backend('cpu'),
        lambda *args: None,
        threading.Event(),
    )
    assert results[0][2] == ''
    output = destination / 'source-wide.tif'
    with tifffile.TiffFile(output) as image:
        assert image.asarray().dtype == np.uint16
        assert image.pages[0].tags[34675].value == colorspaces.profile_bytes('Display P3')


def test_main_export_professional_preset_preserves_original_and_icc(window, tmp_path):
    from test_ui import wait_until
    from ccraw.imaging import colorspaces

    original = hashlib.sha256(
        __import__('pathlib').Path(window.source_path).read_bytes()
    ).hexdigest()
    path = tmp_path / 'wide.tif'
    window.start_export(
        str(path),
        export_options={'format': 'tiff', 'bit_depth': 16, 'color_space': 'display_p3'},
        export_metadata={'title': '测试标题'},
    )
    wait_until(lambda: not window.exporting, timeout=45)
    with tifffile.TiffFile(path) as image:
        assert image.asarray().dtype == np.uint16
        assert image.pages[0].tags[34675].value == colorspaces.profile_bytes('Display P3')
    assert (
        hashlib.sha256(__import__('pathlib').Path(window.source_path).read_bytes()).hexdigest()
        == original
    )


def test_main_external_monitor_adds_derived_version_without_replacing_source(window, tmp_path):
    from ccraw.external_editor import prepare_handoff
    from ccraw.imaging.colorspaces import profile_bytes
    from test_ui import wait_until

    original_path = window.source_path
    handoff = prepare_handoff(
        original_path,
        np.full((3, 4, 3), 0.5, np.float32),
        tmp_path,
        window.edits,
        icc_profile=profile_bytes('sRGB'),
    )
    window.watch_external_handoff(handoff)
    pixels = tifffile.imread(handoff.path)
    pixels[:] = 12345
    tifffile.imwrite(handoff.path, pixels, photometric='rgb')
    window._poll_external_edits()
    wait_until(lambda: any('returned' in path for path in window.documents), timeout=30)
    assert window.source_path == original_path
    assert any(path.endswith('.tif') and 'returned' in path for path in window.documents)
    window.external_timer.stop()


def test_external_editor_configuration_is_saved_as_argument_array(app, tmp_path):
    import sys
    from ccraw.ui.professional_export import ProfessionalExportDialog

    path = tmp_path / 'presets.json'
    first = ProfessionalExportDialog(None, str(tmp_path / 'source.nef'), preset_path=path)
    first.executable.setText(sys.executable)
    first.arguments.setText('["--open", "{file}"]')
    first.save_editor()
    first.close()
    second = ProfessionalExportDialog(None, str(tmp_path / 'source.nef'), preset_path=path)
    assert second.executable.text() == sys.executable
    assert second.editor_arguments() == ['--open', '{file}']
    second.close()


def test_main_export_dialog_numbers_collision(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QDialog
    from ccraw.ui.professional_export import ProfessionalExportDialog
    from test_ui import wait_until

    path = tmp_path / 'photo.jpg'
    path.write_bytes(b'keep')
    monkeypatch.setattr(ProfessionalExportDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(path), 'JPEG'))
    window.export()
    wait_until(lambda: not window.exporting, timeout=45)
    assert path.read_bytes() == b'keep'
    assert (tmp_path / 'photo-2.jpg').is_file()


def test_optics_reset_clears_compiled_lensfun_profile(app):
    from ccraw.ui.professional_panel import ProfessionalPanel
    from ccraw.domain import professional

    values = professional.defaults()
    values['lensfun'] = {
        'path': 'approved.xml',
        'sha256': 'abc',
        'maker': 'Maker',
        'model': 'Lens',
        'focal': 50,
        'calibration': {'model': 'poly3', 'focal': 50, 'coefficients': [0.01]},
    }
    panel = ProfessionalPanel()
    panel.set_values(values)
    panel.reset_group('optics')
    assert panel.values()['lensfun'] is None
    panel.close()


def test_dcp_mode_is_editable_and_gamut_warning_is_forwarded(app, tmp_path):
    from ccraw.ui.professional_panel import ProfessionalPanel
    from ccraw.ui.professional_export import ProfessionalExportDialog

    panel = ProfessionalPanel()
    panel.profile_mode.setCurrentIndex(panel.profile_mode.findData('look'))
    assert panel.values()['profile']['mode'] == 'look'
    dialog = ProfessionalExportDialog(
        None, str(tmp_path / 'photo.tif'), preset_path=tmp_path / 'presets.json'
    )
    spy = QSignalSpy(dialog.proofRequested)
    dialog.proof_gamut.setChecked(True)
    dialog._apply_proof()
    assert spy.at(0)[0]['gamut_warning'] is True
    panel.close()
    dialog.close()


def test_external_defaults_use_global_service_with_dialog_override(app, tmp_path, monkeypatch):
    from ccraw import settings
    from ccraw.ui.professional_export import ProfessionalExportDialog

    class Service:
        def read(self):
            return {
                'external': {
                    'executable': 'C:/Editor/editor.exe',
                    'arguments': ['--open', '{file}'],
                    'format': 'psd16',
                }
            }

    monkeypatch.setattr(settings, 'get_service', lambda: Service())
    dialog = ProfessionalExportDialog(
        None, str(tmp_path / 'photo.tif'), preset_path=tmp_path / 'presets.json'
    )
    assert dialog.executable.text() == 'C:/Editor/editor.exe'
    assert dialog.editor_arguments() == ['--open', '{file}']
    dialog.action.setCurrentIndex(dialog.action.findData('external'))
    assert dialog.options()['format'] == 'psd'
    assert dialog.options()['bit_depth'] == 16
    dialog.format.setCurrentIndex(2)
    assert dialog.options()['format'] == 'tiff'
    dialog.close()


def test_import_preferences_apply_once_and_honor_preview_limit(window, tmp_path, monkeypatch):
    import copy
    from types import SimpleNamespace
    from PIL import Image
    from ccraw import engine, settings
    from test_ui import wait_until

    values = copy.deepcopy(settings.DEFAULTS)
    values['import']['other_preset'] = 'vivid'
    values['import']['metadata'] = {
        'keywords': ['portfolio'],
        'creator': 'Photographer',
        'copyright': '2026 Photographer',
    }

    class Service:
        def read(self, **kwargs):
            return values

        def project_overrides(self, project):
            return None

    monkeypatch.setattr(
        window,
        '_settings_binding',
        SimpleNamespace(
            service=Service(),
            project_getter=lambda: None,
            flush=lambda: True,
            note_edit=lambda: None,
        ),
    )
    window.setProperty('previewLimit', 128)
    source = tmp_path / 'defaults.png'
    Image.new('RGB', (400, 200), 'orange').save(source)
    calls = []
    decode = engine.load_image

    def load(path, preview_limit=1600, **kwargs):
        calls.append((str(path), preview_limit))
        return decode(path, preview_limit=preview_limit, **kwargs)

    monkeypatch.setattr(engine, 'load_image', load)
    window.open_path(str(source))
    wait_until(lambda: window.source_path == str(source.resolve()) and not window.loading)
    assert window.edits['adjustments']['vibrance'] == 18
    assert window.source.shape[:2] == (64, 128)
    assert any(path == str(source) and limit == 128 for path, limit in calls)
    assert window.documents[str(source.resolve())]['metadata']['copyright'] == '2026 Photographer'
    window.edits['adjustments']['vibrance'] = 7
    window.commit()
    window.stash_document()
    wait_until(lambda: not window.render_running)
    values['import']['other_preset'] = 'monochrome'
    window.open_path(str(source))
    wait_until(lambda: not window.loading)
    assert window.edits['adjustments']['vibrance'] == 7
    assert window.edits['monochrome'] is False
    assert window.info['photo']['keywords'] == ['portfolio']


def test_partial_dcp_import_displays_supported_stage_and_survives_restore(app, tmp_path):
    from ccraw.ui.professional_panel import ProfessionalPanel

    path = tmp_path / 'partial.dcp'
    tifffile.imwrite(
        path,
        np.zeros((2, 2, 3), np.uint8),
        photometric='rgb',
        metadata=None,
        extratags=[(50721, 'd', 9, np.eye(3).ravel(), False), (50778, 'H', 1, 21, False)],
    )
    panel = ProfessionalPanel()
    info = panel.import_profile(path)
    assert info['type'] == 'dcp'
    assert panel.values()['profile']['path'] == str(path.resolve())
    assert '相机矩阵' in panel.profile_status.text()
    assert '普通照片的外观模式不会产生改变' in panel.profile_status.text()
    panel.set_values(panel.values())
    assert '相机标定' in panel.profile_status.text()
    panel.close()
