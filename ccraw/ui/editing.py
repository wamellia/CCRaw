from __future__ import annotations
import copy
from PySide6.QtCore import Qt
from .. import engine, model, compute
from ..scheduler import Activity as A
from .. import performance


class EditingMixin:
    def process_changed(self, index):
        if not self.refreshing:
            self.edits['process'] = index + 1
            self.changed()
            self.commit()

    def professional_changed(self, settings):
        if self.refreshing:
            return
        from ..domain import professional

        self.edits['professional'] = professional.validate(settings)
        from pathlib import Path

        profile = settings.get('profile', {})
        if (
            Path(profile.get('path', '')).suffix.lower() == '.dcp'
            and profile.get('mode', 'auto') != 'look'
            and getattr(self.source, 'raw_path', None)
        ):
            self.edits['process'] = 2
            self.process_select.blockSignals(True)
            self.process_select.setCurrentIndex(1)
            self.process_select.blockSignals(False)
        self.clear_preset_selection()
        self.changed()

    def professional_sampling(self, mode):
        if self.source is None:
            return
        self._professional_sample = mode
        self.color_region_button.setChecked(False)
        self.wb_button.setChecked(False)
        self.update_tool()

    def professional_bw_auto(self):
        if self.source is None:
            return
        from ..imaging.professional import automatic_bw

        self.edits['monochrome'] = True
        self.mono_check.setChecked(True)
        self.professional_editor.set_bw_values(automatic_bw(self.preview_original()))

    def professional_constrain(self):
        if self.source is None:
            return
        import copy
        from ..imaging.professional import constrained_crop

        shape, pro = self.source.shape, copy.deepcopy(self.edits['professional'])
        document, revision = self.document_token, self.generation
        button = self.professional_editor.constrain_button
        button.setEnabled(False)

        def ready(crop):
            button.setEnabled(True)
            if self.document_token == document and self.generation == revision:
                self.commit()
                self.edits['crop'] = crop
                self.final_view.setChecked(True)
                self.refresh()
                self.changed()
                self.commit()

        def fail(message):
            button.setEnabled(True)
            if self.document_token == document:
                self.error(message)

        self.job(lambda: constrained_crop(shape, pro), ready, fail)

    def professional_bw_start(self, point):
        if self.source is None:
            return
        import numpy as np
        from ..imaging.professional import bw_target_band

        x = round(np.clip(point[0], 0, 1) * (self.source.shape[1] - 1))
        y = round(np.clip(point[1], 0, 1) * (self.source.shape[0] - 1))
        rgb = engine.to_srgb(self.source[y : y + 1, x : x + 1]).clip(0, 1)[0, 0]
        self._bw_target_index = bw_target_band(rgb)
        self._bw_target_value = (
            self.edits['professional']['bw_mixer'][self._bw_target_index]
            if self._bw_target_index is not None
            else 0
        )
        self.edits['monochrome'] = True
        self.mono_check.setChecked(True)

    def professional_bw_move(self, distance):
        index = getattr(self, '_bw_target_index', None)
        if index is not None:
            self.professional_editor.bw_controls[index].setValue(
                max(-100, min(100, self._bw_target_value + distance))
            )
            self.professional_editor._set_bw(
                index, self.professional_editor.bw_controls[index].spin.value()
            )

    def professional_sample(self, point):
        mode = getattr(self, '_professional_sample', None)
        if not mode or self.source is None:
            return
        self._professional_sample = None
        if mode == 'eye':
            self.professional_editor.add_red_eye(list(point))
        else:
            import numpy as np

            x = round(np.clip(point[0], 0, 1) * (self.source.shape[1] - 1))
            y = round(np.clip(point[1], 0, 1) * (self.source.shape[0] - 1))
            rgb = engine.to_srgb(self.source[y : y + 1, x : x + 1]).clip(0, 1)[0, 0].tolist()
            self.professional_editor.add_color_sample(rgb)
        self.update_tool()

    def professional_lensfun(self, settings):
        from ..domain import professional

        proposed = professional.merge(self.edits['professional'], {'lensfun': settings})
        self.professional_editor.set_values(proposed)
        self.professional_changed(proposed)
        self.commit()

    def watermark_changed(self, settings):
        if self.refreshing or self.source is None:
            return
        self.edits['watermark'] = settings
        self.changed()
        self.commit()

    def exposure_curve_changed(self, values, points):
        if self.refreshing or self.source is None:
            return
        for key in ('exposure', 'blacks', 'shadows', 'highlights', 'whites'):
            self.edits['adjustments'][key] = values[key]
            self.controls[key].setValue(values[key])
        self.edits['tone_curve'] = points
        self.clear_preset_selection()
        self.changed()

    def adjust(self, key, value, local=False):
        if self.refreshing:
            return
        if local:
            mask = self.selected_mask()
            if mask is None:
                return
            mask['adjustments'][key] = value
        else:
            self.edits['adjustments'][key] = value
            self.clear_preset_selection()
            self.exposure_curve.set_values(self.edits['adjustments'], self.edits.get('tone_curve'))
        self.changed()

    def change_hsl(self, index, value):
        if not self.refreshing:
            self.clear_preset_selection()
            self.edits['hsl'][self.color_select.currentIndex()][index] = value
            self.changed()

    def refresh_hsl(self, *_):
        for index, button in enumerate(self.swatch_buttons):
            button.setChecked(index == self.color_select.currentIndex())
        for c, v in zip(self.hsl_controls, self.edits['hsl'][self.color_select.currentIndex()]):
            c.setValue(v)

    def curve_changed(self, points):
        self.edits['curves'][self.channel.currentText()] = points
        self.clear_preset_selection()
        self.changed()

    def refresh_curve(self, *_):
        channel = self.channel.currentText()
        self.curve.set_points(self.edits['curves'][channel], channel, self.edits['curve_mode'])
        self.curve_mode.blockSignals(True)
        self.curve_mode.setCurrentIndex(0 if self.edits['curve_mode'] == 'smooth' else 1)
        self.curve_mode.blockSignals(False)
        self.curve.report(self.curve_input.value() / 255)

    def curve_selection(self, x, y):
        for control, value in [(self.curve_input, x), (self.curve_output, y)]:
            control.blockSignals(True)
            control.setValue(value)
            control.blockSignals(False)

    def curve_mode_changed(self, index):
        self.edits['curve_mode'] = 'smooth' if index == 0 else 'linear'
        self.refresh_curve()
        self.clear_preset_selection()
        self.changed()
        self.commit()

    def reset_curve(self):
        self.edits['curves'][self.channel.currentText()] = [[0.0, 0.0], [1.0, 1.0]]
        self.refresh_curve()
        self.clear_preset_selection()
        self.changed()
        self.commit()

    def selected_mask(self):
        return (
            self.edits['masks'][self.current_mask]
            if 0 <= self.current_mask < len(self.edits['masks'])
            else None
        )

    def edit_mask_components(self):
        from .mask_components import MaskComponentsDialog
        from PySide6.QtWidgets import QDialog

        mask = self.selected_mask()
        if mask is None:
            return
        before = copy.deepcopy(self.edits)
        document, index = self.document_token, self.current_mask
        dialog = MaskComponentsDialog(mask, self.edits['masks'], self)

        def preview(parts):
            if self.document_token == document and index < len(self.edits['masks']):
                self.edits['masks'][index]['components'] = copy.deepcopy(parts)
                self.changed()

        dialog.changed.connect(preview)
        self.preview_drag(True)
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            if self.document_token == document:
                if accepted:
                    self.edits['masks'][index]['components'] = copy.deepcopy(dialog.parts)
                    self.edits = model.validate(self.edits)
                else:
                    self.edits = before
                self.refresh()
                self.changed()
        finally:
            self.preview_drag(False)
            dialog.deleteLater()
        if accepted:
            self.commit()

    def add_mask(self, kind):
        if self.source is None:
            return
        if len(self.edits['masks']) >= 32:
            return self.error('最多支持 32 个蒙版。')
        self.edits['masks'].append(model.new_mask(kind, len(self.edits['masks']) + 1))
        self.current_mask = len(self.edits['masks']) - 1
        self.split_check.setChecked(False)
        self.refresh()
        self.changed()
        self.commit()

    def select_mask(self, index):
        if self.refreshing:
            return
        self.current_mask = index
        self.refresh_mask_controls()
        self.update_tool()
        self.update_overlay()

    def delete_mask(self):
        if self.selected_mask() is not None:
            self.edits['masks'].pop(self.current_mask)
            self.current_mask = min(self.current_mask, len(self.edits['masks']) - 1)
            self.refresh()
            self.changed()
            self.commit()

    def mask_property(self, key, value):
        if self.refreshing or self.selected_mask() is None:
            return
        self.selected_mask()[key] = value
        self.changed()

    def refresh_mask_controls(self):
        old = self.refreshing
        self.refreshing = True
        mask = self.selected_mask()
        for c in [
            self.enabled_check,
            self.invert_check,
            self.opacity,
            self.feather,
            *self.local_controls.values(),
        ]:
            c.setEnabled(mask is not None)
        self.brush_size.setEnabled(
            mask is not None and (mask['kind'] == 'brush' or 'raster' in mask)
        )
        self.erase_check.setEnabled(
            mask is not None and (mask['kind'] == 'brush' or 'raster' in mask)
        )
        is_range = mask is not None and mask['kind'] == 'luminance'
        self.range_box.setVisible(is_range)
        self.feather.setVisible(not is_range)
        if is_range:
            self.range_controls['low'].setValue(mask['luminance_range'][0])
            self.range_controls['high'].setValue(mask['luminance_range'][1])
            self.range_controls['falloff'].setValue(mask['range_falloff'])
        if mask:
            self.enabled_check.setChecked(mask['enabled'])
            self.invert_check.setChecked(mask['invert'])
            self.opacity.setValue(mask['opacity'])
            self.feather.setValue(mask['feather'])
            for key, control in self.local_controls.items():
                control.setValue(mask['adjustments'][key])
        self.refreshing = old

    def update_overlay(self, *_):
        if (
            self.editor_tool_active('point_color')
            and self.professional_editor.range_preview.isChecked()
            and not self.comparing
            and not self.split_check.isChecked()
        ):
            return  # The image worker computes the range with the current color stage.
        mask = self.selected_mask()
        if (
            self.source is not None
            and mask
            and self.editor_tool_active('masks')
            and self.overlay_check.isChecked()
            and not self.comparing
            and not self.split_check.isChecked()
        ):
            reference = self.preview_original() if engine._has_luminance(mask) else None
            alpha = engine.mask_alpha(mask, self.source.shape, reference)
            from ..imaging.professional import warp_mask

            alpha = warp_mask(alpha, self.edits.get('professional', {}))
            self.canvas.set_overlay(
                engine.crop_rotate(alpha, self.edits) if self.final_view.isChecked() else alpha
            )
        else:
            self.canvas.set_overlay(None)

    def update_tool(self, *_):
        self.canvas.grid_visible = (
            hasattr(self, 'professional_editor')
            and self.editor_tool_active('geometry')
            and self.professional_editor.geometry_grid.isChecked()
        )
        if self.comparing or self.split_check.isChecked():
            self.canvas.tool = 'view'
        elif (
            hasattr(self, 'color_region_button')
            and self.color_region_button.isChecked()
            and self.editor_tool_active('masks')
        ):
            self.canvas.tool = 'color'
        elif self.editor_tool_active('bw_mixer') and self.professional_editor.bw_target.isChecked():
            self.canvas.tool = 'bw-target'
        elif getattr(self, '_professional_sample', None) and (
            self.editor_tool_active('point_color') or self.editor_tool_active('red_eye')
        ):
            self.canvas.tool = 'color'
        elif self.wb_sampling:
            self.canvas.tool = 'sample'
        elif self.editor_tool_active('retouch'):
            self.canvas.tool = 'heal' if self.retouch_tool.currentIndex() == 0 else 'clone'
            self.canvas.brush_radius = self.retouch_controls['size'].spin.value() / 200
        elif self.editor_tool_active('crop'):
            self.canvas.tool = 'view' if self.final_view.isChecked() else 'crop'
        elif self.editor_tool_active('masks') and self.selected_mask():
            mask = self.selected_mask()
            self.canvas.brush_radius = self.brush_size.spin.value() / 200
            self.canvas.tool = (
                mask['kind']
                if mask['kind'] in ('brush', 'linear', 'radial')
                else (
                    'brush' if self.refine_mask_check.isChecked() and 'raster' in mask else 'view'
                )
            )
            self.canvas.mask_geometry = [mask['start'], mask['end']]
        else:
            self.canvas.tool = 'view'
        self.canvas.setCursor(
            Qt.CursorShape.CrossCursor
            if self.canvas.tool in ('sample', 'color')
            else Qt.CursorShape.ArrowCursor
        )
        self.canvas.setToolTip(
            '框内拖动移动，框外拖动重画；Enter 确认' if self.canvas.tool == 'crop' else ''
        )
        self.canvas.update()

    def tab_changed(self, *_):
        if not hasattr(self, 'overlay_check'):
            return
        if self.canvas.tool == 'crop' and self.canvas.start is not None:
            self.canvas.mouseReleaseEvent(None)
        if self.editor_tool_active('crop'):
            self.final_view.setChecked(False)
        if self.editor_groups.active in ('masks', 'crop', 'retouch'):
            self.split_check.setChecked(False)
        if self.wb_sampling and not self.editor_tool_active('white_balance'):
            self.wb_button.setChecked(False)
        if self.editor_tool_active('watermark'):
            self.watermark_editor.update_preview()
        self.update_tool()
        self.update_overlay()

    def geometry_changed(self, geometry):
        a, b = geometry
        if self.canvas.tool == 'crop':
            crop = [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
            if crop[2] - crop[0] < 0.002 or crop[3] - crop[1] < 0.002:
                return
            self.edits['crop'] = crop
            # Crop editing changes the overlay, not the displayed photo pixels.
            # Keep feedback synchronous and leave image processing to confirmation.
            self.canvas.crop = crop[:]
            self.canvas.update()
            dirty = self.edits != self.saved_edits or self.snapshots != self.saved_snapshots
            self.state_label.setText('未保存编辑' if dirty else '编辑已保存')
            self.refresh_history_buttons()
            self.commit()
            return
        elif self.selected_mask():
            self.selected_mask().update(start=a, end=b)
        if not self._preview_dragging:
            self.update_display()
        self.changed()
        self.commit()

    def stroke_finished(self, stroke):
        if stroke.get('kind') in ('heal', 'clone'):
            self.add_retouch(stroke)
            return
        mask = self.selected_mask()
        if mask and (mask['kind'] == 'brush' or 'raster' in mask):
            mask['strokes'].append(stroke)
            self.changed()
            self.commit()

    def ratio_changed(self, index):
        self.canvas.ratio = [
            None,
            self.source.shape[1] / self.source.shape[0] if self.source is not None else 1.5,
            1.0,
            1.5,
            4 / 3,
            16 / 9,
            2 / 3,
            9 / 16,
        ][index]

    def confirm_crop(self):
        if self.source is None or not self.editor_tool_active('crop'):
            return
        self.canvas.mouseReleaseEvent(None)
        self.final_view.setChecked(True)
        self.canvas.fit()
        self.commit()
        self.statusBar().showMessage('裁切已确认')

    def clear_crop(self):
        if self.canvas.tool == 'crop':
            self.canvas.mouseReleaseEvent(None)
        self.edits['crop'] = None
        self.final_view.setChecked(False)
        self.canvas.fit()
        self.update_display()
        self.changed()
        self.commit()

    def rotate(self):
        self.edits['rotation'] = (self.edits['rotation'] + 1) % 4
        self.final_view.setChecked(True)
        self.update_display()
        self.changed()
        self.commit()

    def backend_changed(self, index):
        self.backend = engine.Backend('cpu' if index else 'auto')
        if index:
            compute.state.report(
                'CPUExecutionProvider', detail=f'{performance.THREADS} 线程 · 手动 CPU 模式'
            )
        self.refresh_backend_status()
        self.changed()

    def reset_edits(self):
        if self.source is not None and not self.work.busy(A.LOADING, A.AI):
            self._preview_epoch += 1
            self.commit()
            self.edits = model.recipe()
            self.edits['white_balance'] = copy.deepcopy(
                self.info.get('white_balance', self.edits['white_balance'])
            )
            self.edits['develop'] = copy.deepcopy(self.info.get('develop', self.edits['develop']))
            self.clear_clone_source()
            self.clear_preset_selection()
            self.current_mask = -1
            self.refresh()
            self.changed()
            self.commit()

    def reset_editor_section(self):
        """Reset exactly the selected operation, retaining other recipe groups."""
        if (
            self.source is None
            or self.editor_groups.active is None
            or self.work.busy(A.LOADING, A.AI)
        ):
            return
        key = self.editor_groups.active
        defaults = model.recipe()
        self._preview_epoch += 1
        self.commit()
        adjustment_groups = {
            'light': ('exposure', 'contrast', 'shadows', 'highlights', 'blacks', 'whites'),
            'color': ('saturation', 'vibrance'),
            'detail': ('dehaze', 'clarity', 'texture', 'sharpness', 'denoise', 'color_noise'),
            'white_balance': ('temperature', 'tint'),
        }
        for adjustment in adjustment_groups.get(key, ()):
            self.edits['adjustments'][adjustment] = defaults['adjustments'][adjustment]
        recipe_groups = {
            'light': ('tone_curve',),
            'color': ('hsl', 'monochrome'),
            'curves': ('curves', 'curve_mode'),
            'grading': ('grading',),
            'effects': ('effects',),
            'watermark': ('watermark',),
            'crop': ('crop', 'rotation', 'straighten'),
            'masks': ('masks',),
            'retouch': ('retouch',),
            'process': ('process',),
            'white_balance': ('wb_gain',),
        }
        for field in recipe_groups.get(key, ()):
            self.edits[field] = copy.deepcopy(defaults[field])
        if key == 'white_balance':
            self.edits['white_balance'] = copy.deepcopy(
                self.info.get('white_balance', defaults['white_balance'])
            )
        if key == 'light':
            self.edits['develop'] = copy.deepcopy(self.info.get('develop', defaults['develop']))
        professional_group = {
            'optics': 'optics',
            'geometry': 'geometry',
            'calibration': 'calibration',
            'professional_detail': 'detail',
            'bw_mixer': 'bw_mixer',
            'point_color': 'point_color',
            'profile': 'profile',
            'red_eye': 'red_eye',
        }.get(key)
        if professional_group is not None:
            self.edits['professional'][professional_group] = copy.deepcopy(
                defaults['professional'][professional_group]
            )
            if key == 'optics':
                self.edits['professional']['lensfun'] = None
                self.professional_editor.lens_status.setText('镜头标定已移除；手动光学控制已重置。')
        if key == 'masks':
            self.current_mask = -1
        if key == 'retouch':
            self.clear_clone_source()
        if key == 'crop':
            self.final_view.setChecked(False)
        self._professional_sample = None
        self.wb_button.setChecked(False)
        self.clear_preset_selection()
        self.refresh()
        self.changed()
        self.commit()

    def undo(self, delta):
        self._preview_epoch += 1
        if self.source is None:
            return
        if delta < 0:
            self.commit()
        self.edits = self.history.move(delta)
        self.clear_preset_selection()
        self.current_mask = min(self.current_mask, len(self.edits['masks']) - 1)
        self.refresh()
        self.changed()
        self.history_timer.stop()

    def refresh(self):
        self.refreshing = True
        for key, control in self.controls.items():
            control.setValue(self.edits['adjustments'][key])
        self.refresh_hsl()
        self.refresh_curve()
        self.exposure_curve.set_values(self.edits['adjustments'], self.edits.get('tone_curve'))
        self.watermark_editor.set_settings(self.edits['watermark'])
        self.mask_list.clear()
        self.mask_list.addItems([m['name'] for m in self.edits['masks']])
        self.mask_list.setCurrentRow(self.current_mask)
        self.refresh_mask_controls()
        self.refresh_workspace()
        self.refresh_camera_wb()
        self.develop_combo.blockSignals(True)
        self.develop_combo.setCurrentIndex(0 if self.edits['develop']['mode'] == 'camera' else 1)
        self.develop_combo.blockSignals(False)
        self.develop_hint.setText(self.edits['develop']['source'])
        self.update_library_status()
        self.refresh_retouch()
        if hasattr(self, 'professional_editor'):
            self.professional_editor.setRecipe(self.edits)
            self.process_select.setCurrentIndex(self.edits.get('process', 1) - 1)
        self.save_button.setEnabled(self.source is not None and not self.work.busy(A.LOADING))
        self.export_button.setEnabled(
            self.source is not None and not self.work.busy(A.EXPORTING, A.LOADING)
        )
        self.refresh_access()
        self.compare.setEnabled(self.source is not None)
        self.refreshing = False
        self.ratio_changed(self.crop_ratio.currentIndex())
        self.update_display()
