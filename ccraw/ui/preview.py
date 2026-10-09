from __future__ import annotations
import copy
import time
import threading
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from .. import engine
from ..scheduler import Activity as A
from .. import geometry
from .. import live_preview, viewport


class PreviewMixin:
    def changed(self):
        if self.refreshing or self.source is None:
            return
        self.generation += 1
        self.cancel_stale_detail()
        # Throttle the leading edge, never debounce by restarting on every value.
        if self.render_running:
            self.pending = True
        elif not self.timer.isActive():
            delay = (
                self.preview_quality.delay_ms(time.perf_counter() - self._preview_started)
                if self._preview_dragging
                else 16
            )
            self.timer.start(delay)
        if self._preview_dragging:
            self.history_timer.stop()
            self.detail_timer.stop()
        else:
            self.history_timer.start()
            self.detail_timer.start(220)
        dirty = self.edits != self.saved_edits or self.snapshots != self.saved_snapshots
        self.state_label.setText('未保存编辑' if dirty else '编辑已保存')
        self.refresh_history_buttons()

    def commit(self):
        if self._preview_dragging:
            return  # one mouse drag is one history transaction, even during a pause
        self.history_timer.stop()
        self.history.push(self.edits)
        self.refresh_history_buttons()

    def preview_drag(self, active):
        if active:
            self.commit()
            self._preview_dragging += 1
            self._preview_epoch += 1
            self._preview_started = 0.0
            self.history_timer.stop()
            self.detail_timer.stop()
            self.cancel_detail()
        else:
            self._preview_dragging = max(0, self._preview_dragging - 1)
            if not self._preview_dragging:
                self._preview_epoch += 1
                # Finish the latest snapshot and refresh the navigator/history.
                if self.source is not None and not self.closing:
                    self.timer.start(0)
                    self.detail_timer.start(220)

    def preview_options(self):
        mask = (
            self.selected_mask()
            if self.tabs.currentIndex() == 4 and self.overlay_check.isChecked()
            else None
        )
        return (
            self.final_view.isChecked(),
            self.comparing,
            self.split_check.isChecked(),
            self.shadow_warning.isChecked(),
            self.highlight_warning.isChecked(),
            mask,
        )

    def render(self):
        if self.source is None or self.closing or self.work.busy(A.LOADING, A.AI):
            return
        if self.work.active(A.RENDER):
            self.work.pending_render = True
            return
        self.work.begin(A.RENDER)
        self.work.pending_render = False
        source, edits, token, backend = (
            self.source,
            copy.deepcopy(self.edits),
            self.generation,
            self.backend,
        )
        # In-progress painting belongs to the render snapshot, not the saved
        # recipe. mouseRelease stores the completed stroke exactly once.
        stroke = self.canvas.stroke
        if stroke is not None:
            stroke = copy.deepcopy(stroke)
            if stroke.get('kind') in ('heal', 'clone'):
                if len(edits['retouch']) < 500:
                    stroke.pop('erase', None)
                    stroke.update(
                        enabled=True,
                        feather=self.retouch_controls['feather'].spin.value(),
                        opacity=self.retouch_controls['opacity'].spin.value(),
                    )
                    edits['retouch'].append(stroke)
            elif 0 <= self.current_mask < len(edits['masks']):
                edits['masks'][self.current_mask]['strokes'].append(stroke)
        document, epoch = self.document_token, self._preview_epoch
        interactive = bool(self._preview_dragging)
        view = self.detail_view() if interactive else None
        detail_request = None
        holder = None
        if view is not None and view.tiles:
            if self.detail_source is None:
                self.detail_source = viewport.DetailSource(self.source_path)
            holder = self.detail_source
            keys = self.detail_keys()
            blocks = [
                (kind, keys[kind], rect)
                for kind in self.wanted_kinds()
                for rect in viewport.group(view.tiles)
            ]
            detail_request = dict(
                edits=edits,
                backend=backend,
                level=view.level,
                final=view.final,
                blocks=blocks,
                proxy=source,
                cache=self.preview_buffers.cache,
                proxy_scale=max(0.1, source.shape[1] / self.info.get('width', source.shape[1])),
            )
        options = copy.deepcopy(self.preview_options())
        region_only = detail_request is not None and options[5] is None
        previous_rgb = self.rendered
        if region_only:
            detail_request['pixel_rect'] = self.live_detail_rect(view)
        mask_index = self.current_mask if options[5] is not None else None
        original_key = (self.document_token, engine._key(edits.get('develop', {})))
        original = self._original[1] if self._original[0] == original_key else None
        self.statusBar().showMessage('实时预览…' if interactive else '正在更新预览…')
        started = time.perf_counter()
        self._preview_started = started

        def work():
            if region_only:
                return live_preview.prepare_region(holder, detail_request, view, previous_rgb)
            scale = max(0.1, source.shape[1] / original_width)
            output = live_preview.prepare(
                source, edits, backend, self.render_cache, scale, original, options, interactive
            )
            if detail_request is not None:
                output.detail = (view, viewport.render(holder, detail_request, threading.Event()))
                output.seconds = time.perf_counter() - started
            return output

        def finish(output):
            self.work.end(A.RENDER)
            same_photo = source is self.source and document == self.document_token
            if output.original is not None and same_photo and self._original[0] != original_key:
                self._original = (original_key, output.original)
            # Continuous input must not starve the screen. Show the completed
            # interactive frame, then compute the latest snapshot; never do this
            # across a release/repress or document switch.
            # Spin buttons and keyboard repeats also produce continuous edits.
            # With unchanged resolution they can present completed snapshots,
            # exactly like a held mouse gesture, without starving the screen.
            live = epoch == self._preview_epoch and (not interactive or self._preview_dragging)
            if same_photo and live:
                self._preview_edits = edits
                if not output.detail_only:
                    self.rendered = output.rgb
                self.histogram.hist = output.histogram
                self.histogram.update()
                current = self.preview_options()
                current_index = self.current_mask if current[5] is not None else None
                # New slider values describe the next frame, not a different
                # presentation mode. Reusing this worker-prepared image avoids
                # reverting to GUI conversions during local adjustment drags.
                prepared = (
                    output if current[:5] == options[:5] and current_index == mask_index else None
                )
                if not output.detail_only:
                    self.update_display(prepared=prepared)
                if (
                    output.detail is not None
                    and current[:5] == options[:5]
                    and self.detail_view() == output.detail[0]
                ):
                    detail_view, results = output.detail
                    layers = {}
                    for kind, key, level, tiles in results:
                        if not output.detail_only:
                            self.tiles.add(kind, key, level, tiles)
                        layers.setdefault(kind, {}).update(tiles)
                    main = 'original' if self.comparing else 'edited'
                    self.canvas.set_detail(
                        viewport.DetailLayer(
                            detail_view.size,
                            layers.get(main, {}),
                            layers.get('original')
                            if self.split_check.isChecked() and not self.comparing
                            else None,
                        )
                    )
                    self._live_detail = (
                        document,
                        token,
                        detail_view,
                        output.region_rect,
                        options[:5],
                    )
                if output.navigator is not None:
                    pix = QPixmap.fromImage(output.navigator)
                    self.navigator.setPixmap(
                        pix.scaled(
                            max(150, self.navigator.width()),
                            116,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                    if self.info:
                        self.library_info.setText(
                            f'{self.info["format"]}  ·  {self.info["width"]} × {self.info["height"]}'
                        )
                self.backend_label.setText(self.backend.name)
                self.backend_label.setToolTip(self.backend.warning)
                self.statusBar().showMessage('实时预览' if interactive else '预览已更新')
            if same_photo and interactive:
                self.preview_quality.record(output.seconds)
            if not self.closing and (
                self.pending
                or token != self.generation
                or (interactive and not self._preview_dragging)
            ):
                delay = (
                    self.preview_quality.delay_ms(time.perf_counter() - started)
                    if self._preview_dragging
                    else 0
                )
                self.timer.start(delay)

        def failed(text):
            self.work.end(A.RENDER)
            if token == self.generation:
                self.error('预览处理失败：\n' + text)
            elif self.pending and not self.closing:
                self.timer.start(0)

        original_width = self.info.get('width', source.shape[1])
        self.job(work, finish, failed, priority=1)

    def update_display(self, *_, prepared=None):
        """Compose the preview-size display; original-resolution detail arrives as tiles."""
        if self.source is None:
            return
        source, edited = self.source, self.rendered
        if edited is None:
            return
        presentation = self.preview_options()[:5]
        previous = getattr(self, '_presentation_options', presentation)
        if self._preview_dragging and prepared is None and presentation != previous:
            self._preview_epoch += 1
            self._live_detail = None
            self.canvas.set_detail(None)
            self.changed()
        self._presentation_options = presentation
        original = self.preview_original() if prepared is None else None
        image = original if self.comparing else edited
        final = self.final_view.isChecked()
        size = (self.info.get('width', source.shape[1]), self.info.get('height', source.shape[0]))
        if final:
            matrix, display_size, brush_scale = geometry.frame(self.edits, size)
            inverse = np.linalg.inv(matrix)
            self.canvas.to_source = lambda p, m=inverse: geometry.point(m, p)
            self.canvas.to_display = lambda p, m=matrix: geometry.point(m, p)
            self.canvas.reference_size = display_size
            self.canvas.brush_scale = brush_scale
        else:
            self.canvas.to_source = self.canvas.to_display = None
            self.canvas.reference_size = size
            self.canvas.brush_scale = 1.0
        display = (
            prepared.display_rgb
            if prepared is not None
            else (engine.crop_rotate(image, self.edits) if final else image)
        )
        self.canvas.set_image(prepared.image if prepared is not None else display)
        split = self.split_check.isChecked() and not self.comparing
        before = (
            prepared.before
            if prepared is not None
            else (
                (engine.crop_rotate(original, self.edits) if final else original) if split else None
            )
        )
        self.canvas.set_before(before, split)
        if prepared is not None:
            self.canvas.set_prepared_clipping(
                prepared.clipping,
                self.shadow_warning.isChecked() and not self.comparing,
                self.highlight_warning.isChecked() and not self.comparing,
            )
        else:
            self.canvas.set_clipping(
                display,
                self.shadow_warning.isChecked() and not self.comparing,
                self.highlight_warning.isChecked() and not self.comparing,
            )
        self.canvas.crop = None if final else self.edits['crop']
        self.refresh_detail()
        self.update_tool()
        if prepared is not None:
            self.canvas.overlay = prepared.overlay
            self.canvas.update()
        else:
            self.update_overlay()

        if self.tabs.currentIndex() == 2 and not self._preview_dragging:
            self.watermark_editor.update_preview()

    def show_original(self, state):
        self.comparing = state
        self.update_display()
