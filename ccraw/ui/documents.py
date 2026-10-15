from __future__ import annotations
from .. import branding
import copy
import threading
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox, QDialog, QProgressDialog
from .. import engine, model
from ..scheduler import Activity as A
from .. import watermark

from .professional_export import ProfessionalExportDialog as ExportDialog


class DocumentsMixin:
    def job(self, fn, success, fail=None, priority=0):
        """Queue background image work; see scheduler.JobScheduler."""
        return self.scheduler.submit(fn, success, fail or self.error, priority)

    def error(self, text):
        self.statusBar().showMessage('操作未完成')
        QMessageBox.warning(self, 'CCRaw', text)

    def may_discard(self):
        if self.source is None or (
            self.edits == self.saved_edits and self.snapshots == self.saved_snapshots
        ):
            return True
        answer = QMessageBox.question(
            self,
            '保存编辑',
            '当前编辑尚未保存为工程。是否保存？',
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def open_file(self):
        if self.work.busy(A.AI):
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, '导入照片、工程或选片集', '', engine.PHOTO_FILTER
        )
        if paths:
            self.import_paths(paths)

    def import_paths(self, paths):
        if not self.work.can_start(A.LOADING):
            self.statusBar().showMessage('请等待当前读取或导出完成。')
            return
        paths = [str(Path(p).resolve()) for p in paths if Path(p).is_file()]
        if not paths:
            return
        if Path(paths[0]).suffix.lower() in branding.ALBUM_SUFFIXES:
            return self.open_album(paths[0])
        photos = [
            p
            for p in paths
            if Path(p).suffix.lower() not in (*branding.PROJECT_SUFFIXES, *branding.ALBUM_SUFFIXES)
        ]
        self.add_documents(photos)
        self.open_path(paths[0])

    def open_path(self, path):
        if self.work.busy(A.AI):
            return
        if Path(path).suffix.lower() in branding.ALBUM_SUFFIXES:
            return self.open_album(path)
        if not self.work.can_start(A.LOADING):
            self.statusBar().showMessage('请等待当前读取／导出完成。')
            return
        self.stash_document()
        explicit_project = Path(path).suffix.lower() in branding.PROJECT_SUFFIXES
        document = self.documents.get(str(Path(path).resolve())) if not explicit_project else None
        edits = copy.deepcopy(document['edits']) if document else model.recipe()
        snapshots = copy.deepcopy(document['snapshots']) if document else []
        project = document['project'] if document else ''
        try:
            if Path(path).suffix.lower() in branding.PROJECT_SUFFIXES:
                project = path
                path, edits, snapshots = model.load_project(path, include_snapshots=True)
                if not Path(path).exists():
                    QMessageBox.information(
                        self, '重新定位原片', '工程引用的原片已移动，请选择对应的原片。'
                    )
                    path, _ = QFileDialog.getOpenFileName(self, '定位工程原片')
                    if not path:
                        return
        except Exception as exc:
            self.error(str(exc))
            return
        self.work.begin(A.LOADING)
        self.cancel_detail()
        self.open_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.timer.stop()
        self.history_timer.stop()
        self.generation += 1
        self.statusBar().showMessage('正在解码原片…')
        binding = getattr(self, '_settings_binding', None)
        import_defaults = None
        if binding is not None:
            values = binding.service.read(
                project=binding.service.project_overrides(binding.project_getter())
            )
            import_defaults = values['import']
        preview_limit = self.property('previewLimit')
        preview_limit = int(preview_limit) if preview_limit is not None else 1600

        def loaded(result):
            self.source, self.info = result
            self.source_path = str(Path(path).resolve())
            import_metadata = copy.deepcopy(document.get('metadata', {})) if document else {}
            if not explicit_project and (not document or not document['initialized']):
                if import_defaults is not None:
                    from ..settings import import_preview

                    applied = import_preview(edits, self.info, import_defaults, initialized=False)
                    edits.clear()
                    edits.update(applied['recipe'])
                    import_metadata.update(
                        {key: value for key, value in applied['metadata'].items() if value}
                    )
                    self.info['import_defaults'] = {
                        'preset': applied['preset'],
                        'camera_rule': applied['camera_rule'],
                    }
                    if document:
                        document['saved_edits'] = copy.deepcopy(edits)
                for state in [edits] + ([document['saved_edits']] if document else []):
                    state['white_balance'] = copy.deepcopy(
                        self.info.get('white_balance', state['white_balance'])
                    )
                    state['develop'] = copy.deepcopy(self.info.get('develop', state['develop']))
                if document and 'history' in document:
                    for state in document['history'].items:
                        if import_defaults is not None:
                            from ..settings import import_preview

                            initialized = import_preview(
                                state, self.info, import_defaults, initialized=False
                            )['recipe']
                            state.clear()
                            state.update(initialized)
                        state['white_balance'] = copy.deepcopy(edits['white_balance'])
                        state['develop'] = copy.deepcopy(edits['develop'])
            self.info.setdefault('photo', {}).update(import_metadata)
            self.reset_resolution()
            self.canvas.image = None
            self.edits = edits
            self.clear_clone_source()
            self.project_path = project
            self.saved_edits = copy.deepcopy(edits)
            self.snapshots = snapshots
            self.saved_snapshots = copy.deepcopy(snapshots)
            self.clear_preset_selection()
            self.history = model.History(edits)
            self.current_mask = -1
            self.rendered = None
            self.work.end(A.LOADING)
            self.open_button.setEnabled(True)
            self.final_view.setChecked(False)
            self.split_check.setChecked(False)
            self.wb_button.setChecked(False)
            self.canvas.fit()
            self.file_label.setText(
                f'{self.info["name"]}   ·   {self.info["width"]} × {self.info["height"]}   ·   {self.info["format"]}'
            )
            self.file_label.setToolTip(self.info['note'])
            if document is None:
                self.add_documents([self.source_path])
                self.documents[self.source_path]['saved_edits'] = copy.deepcopy(edits)
            if import_metadata:
                self.documents[self.source_path]['metadata'] = import_metadata
            self.loaded_document(self.source_path, explicit_project)
            self.refresh()
            self.update_thumbnails()
            self.changed()

        def failed(text):
            self.work.end(A.LOADING)
            self.open_button.setEnabled(True)
            self.refresh()
            self.error('无法打开文件：\n' + text)

        self.job(
            lambda: self.decoded_previews.load(path, preview_limit), loaded, failed, priority=2
        )

    def save(self):
        if self.source is None or self.loading:
            return False
        path = self.project_path
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self,
                '保存无损编辑工程',
                str(Path(self.source_path).with_suffix('.ccraw')),
                'CCRaw 工程 (*.ccraw)',
            )
        if not path:
            return False
        if Path(path).suffix.lower() != '.ccraw':
            path += '.ccraw'
        try:
            model.save_project(path, self.source_path, self.edits, self.snapshots)
            self.project_path = path
            self.saved_edits = copy.deepcopy(self.edits)
            self.saved_snapshots = copy.deepcopy(self.snapshots)
            self.state_label.setText('编辑已保存')
            self.statusBar().showMessage('工程已保存 · 原片未修改')
            return True
        except Exception as exc:
            self.error(str(exc))
            return False

    def export(self, checked=False, enhance=False):
        if enhance:
            return self.open_ai('super')
        if self.work.busy(A.AI):
            return
        if self.work.busy(A.SELECTION):
            self.statusBar().showMessage('正在生成蒙版，请完成后再导出。')
            return
        if self.source is None or not self.work.can_start(A.EXPORTING):
            return
        dialog = ExportDialog(self, self.source_path, enhance)
        if hasattr(self, 'configure_softproof'):
            dialog.proofRequested.connect(self.configure_softproof)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        metadata = {**copy.deepcopy(self.info.get('photo', {})), **dialog.metadata()}
        if dialog.action.currentData() == 'xmp':
            from ..interchange import xmp

            portable = {key: value for key, value in metadata.items() if key in xmp.FIELDS}
            if isinstance(portable.get('creator'), str):
                portable['creator'] = [portable['creator']] if portable['creator'] else []
            try:
                sidecar = xmp.write_sidecar(self.source_path, portable, recipe=self.edits)
                self.statusBar().showMessage(f'XMP 已保存 · 原片未修改 · {sidecar}')
            except (ValueError, OSError) as exc:
                self.error(str(exc))
            return
        options = dialog.options()
        ext = ['.jpg', '.png', '.tif', '.dng', '.psd'][dialog.format.currentIndex()]
        filename = Path(self.source_path).stem + '-CCRaw' + ext
        if options:
            from ..export_presets import file_name

            filename = file_name(self.source_path, options)
        if dialog.action.currentData() == 'external':
            folder = QFileDialog.getExistingDirectory(
                self, '选择外部编辑副本目录', str(Path(self.source_path).parent)
            )
            path = str(Path(folder) / filename) if folder else ''
        else:
            path, _ = QFileDialog.getSaveFileName(
                self,
                '导出成片',
                str(Path(self.source_path).with_name(filename)),
                f'图片 (*{ext})',
            )
        if not path:
            return
        if Path(path).suffix.lower() != ext:
            path += ext
        if Path(path).resolve() == Path(self.source_path).resolve():
            return self.error('请选择不同的文件名，以保留原片。')
        if options and Path(path).exists():
            if options['collision'] == 'skip':
                self.statusBar().showMessage('同名文件已存在，已跳过导出。')
                return
            if options['collision'] == 'unique':
                original_target = Path(path)
                number = 2
                while Path(path).exists():
                    path = str(
                        original_target.with_name(
                            f'{original_target.stem}-{number}{original_target.suffix}'
                        )
                    )
                    number += 1
        self.start_export(
            path,
            [1, 2, 4][dialog.scale.currentIndex()],
            dialog.model_choice(),
            dialog.quality.value(),
            export_options=options,
            export_metadata=metadata,
            external={
                'executable': dialog.executable.text(),
                'arguments': dialog.editor_arguments(),
            }
            if dialog.action.currentData() == 'external'
            else None,
        )

    def start_export(
        self,
        path,
        scale=1,
        model_path='',
        quality=95,
        *,
        export_options=None,
        export_metadata=None,
        external=None,
    ):
        if self.work.busy(A.AI):
            return
        if self.work.busy(A.SELECTION):
            self.statusBar().showMessage('正在生成蒙版，请完成后再导出。')
            return
        if not self.work.can_start(A.EXPORTING):
            return
        if Path(path).resolve() == Path(self.source_path).resolve():
            return self.error('请选择不同的文件名，以保留原片。')
        if export_options is not None:
            from ..export_presets import validate

            try:
                export_options = validate(export_options)
            except ValueError as exc:
                return self.error(str(exc))
        self.work.begin(A.EXPORTING)
        self.export_cancel = threading.Event()
        self.export_dialog = QProgressDialog('正在全尺寸解码…', '取消导出', 0, 100, self)
        self.export_dialog.setWindowTitle('增强与导出')
        self.export_dialog.setAutoClose(False)
        self.export_dialog.setAutoReset(False)
        self.export_dialog.setMinimumDuration(0)
        self.export_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.export_dialog.canceled.connect(self.export_cancel.set)
        self.export_dialog.show()
        self.export_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.statusBar().showMessage('正在全尺寸导出… 大图或超分可能需要较长时间。')
        edits, source_path = copy.deepcopy(self.edits), self.source_path
        photo = copy.deepcopy(
            export_metadata if export_metadata is not None else self.info.get('photo', {})
        )
        use_cuda = self.backend_combo.currentIndex() == 0

        def work():
            self.export_progress.emit(2, '正在全尺寸解码…')
            source, _ = engine.load_image(source_path, preview_limit=None)
            if self.export_cancel.is_set():
                raise InterruptedError('已取消导出')
            self.export_progress.emit(15, '正在应用调色、修复与蒙版…')
            h, w = source.shape[:2]
            crop = edits['crop'] or [0, 0, 1, 1]
            pixels = h * w * (crop[2] - crop[0]) * (crop[3] - crop[1]) * scale * scale
            if pixels > engine.MAX_EXPORT_PIXELS:
                raise ValueError('输出超过 4 亿像素，请先裁切或降低超分倍数。')
            backend = engine.Backend('auto' if use_cuda else 'cpu')
            target_encoded = False
            if export_options is not None and scale == 1 and hasattr(engine, 'render_output'):
                from ..export_presets import PROFILE_NAMES

                result = engine.render_output(
                    source,
                    edits,
                    color_space=PROFILE_NAMES[export_options['color_space']],
                    backend=backend,
                    intent=export_options['intent'],
                )
                target_encoded = True
            else:
                result = engine.process(source, edits, backend)
            del source
            self.export_progress.emit(30, '正在增强…')

            def progress(done, total):
                self.export_progress.emit(
                    30 + round(65 * done / total), f'正在增强 · 分块 {done} / {total}'
                )

            result, backend = engine.super_resolve(
                result, scale, model_path or None, use_cuda, progress, self.export_cancel
            )
            if self.export_cancel.is_set():
                raise InterruptedError('已取消导出')
            self.export_progress.emit(98, '正在写入成片…')
            target = Path(path)
            handoff = None
            if export_options is not None:
                from .. import export_presets
                from ..imaging import colorspaces

                opts = copy.deepcopy(export_options)
                if not target_encoded:
                    result = colorspaces.convert(
                        result, 'sRGB', export_presets.PROFILE_NAMES[opts['color_space']]
                    )
                if external is not None:
                    from ..external_editor import prepare_handoff

                    # Preserve current borders in target space, through the same encoder.
                    if not opts['watermark']:
                        opts['watermark'] = edits['watermark']
                    clean = export_presets.apply_metadata_policy(
                        photo, opts['metadata_policy'], opts['remove_location']
                    )
                    handoff = prepare_handoff(
                        source_path,
                        result,
                        target.parent,
                        edits,
                        format=opts['format'],
                        bit_depth=opts['bit_depth'],
                        color_space=opts['color_space'],
                        metadata=clean,
                        cancelled=self.export_cancel.is_set,
                        export_options=opts,
                    )
                    target = handoff.path
                else:
                    if not opts['watermark']:
                        opts['watermark'] = edits['watermark']
                    export_presets.export_rgb(
                        target,
                        result,
                        opts,
                        metadata=photo,
                        provenance={'source': source_path, 'recipe': edits, 'ai_srgb': scale > 1},
                        cancelled=self.export_cancel.is_set,
                    )
            else:
                from ..photo_metadata import watermark_context

                result = watermark.apply(
                    result, edits['watermark'], watermark_context(photo, source_path)
                )
                engine.export_image(target, result, quality, photo=photo)
            return result.shape, backend, target, handoff

        def success(result):
            self.export_dialog.reset()
            self.work.end(A.EXPORTING)
            self.open_button.setEnabled(True)
            self.export_button.setEnabled(True)
            shape, backend, target, handoff = result
            self.statusBar().showMessage(
                f'导出完成 · {shape[1]} × {shape[0]} · {backend} · {target}'
            )
            if handoff is not None:
                from ..external_editor import launch

                try:
                    launch(handoff, external['executable'], external['arguments'])
                    self.watch_external_handoff(handoff)
                except (ValueError, OSError) as exc:
                    self.error(f'副本已保存，编辑器启动失败：{exc}')
            QMessageBox.information(
                self, '导出完成', f'已保存：\n{target}\n\n{shape[1]} × {shape[0]} 像素\n{backend}'
            )

        def failed(text):
            self.export_dialog.reset()
            self.work.end(A.EXPORTING)
            self.open_button.setEnabled(True)
            self.export_button.setEnabled(True)
            if self.export_cancel.is_set():
                self.statusBar().showMessage('导出已取消，未写入成片。')
            else:
                self.error('导出失败：\n' + text)

        self.job(work, success, failed)

    def update_export_progress(self, value, text):
        if self.work.active(A.EXPORTING) and not self.export_cancel.is_set():
            self.export_dialog.setValue(value)
            self.export_dialog.setLabelText(text)

    def watch_external_handoff(self, handoff):
        if not hasattr(self, 'external_handoffs'):
            self.external_handoffs = []
            self.external_polling = False
            self.external_timer = QTimer(self)
            self.external_timer.setInterval(1500)
            self.external_timer.timeout.connect(self._poll_external_edits)
        self.external_handoffs.append(handoff)
        self.external_timer.start()

    def _poll_external_edits(self):
        if self.closing:
            self.external_timer.stop()
            return
        if self.external_polling or self.work.busy():
            return
        self.external_polling = True
        copies = list(self.external_handoffs)

        def work():
            from ..external_editor import reload_if_changed
            from .. import export_presets
            from ..imaging.colorspaces import profile_bytes

            derived = []
            for handoff in copies:
                if not handoff.path.exists():
                    continue
                loaded = reload_if_changed(handoff)
                if loaded is None:
                    continue
                options = {
                    'format': 'tiff',
                    'bit_depth': 16,
                    'file_name_template': '{stem}-returned',
                }
                target = export_presets.output_path(handoff.path.parent, handoff.path, options)
                export_presets.export_rgb(
                    target,
                    loaded['rgb'],
                    options,
                    icc_profile=loaded.get('icc_profile') or profile_bytes('sRGB'),
                    provenance={
                        'source': str(handoff.original),
                        'external_edit': str(handoff.path),
                        'provenance_path': str(handoff.provenance_path),
                    },
                )
                derived.append(str(target))
            return derived

        def success(paths):
            self.external_polling = False
            if paths:
                self.add_documents(paths)
                self.update_thumbnails()
                self.statusBar().showMessage(f'外部编辑已返回 {len(paths)} 个派生版本 · 原片未修改')

        def failed(text):
            self.external_polling = False
            self.statusBar().showMessage('外部编辑结果暂不能读取：' + text)

        self.job(work, success, failed, priority=-10)

    def import_xmp(self):
        if self.source is None or self.work.busy():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, '读取 XMP sidecar', str(Path(self.source_path).with_suffix('.xmp')), 'XMP (*.xmp)'
        )
        if not path:
            return
        from ..interchange import xmp

        try:
            packet = xmp.read(path)
            if packet['recipe'] is not None:
                edits = model.validate(packet['recipe'])
                self.edits = edits
                self.commit()
                self.refresh()
                self.changed()
            self.info.setdefault('photo', {}).update(packet['metadata'])
            self.documents[self.source_path].setdefault('metadata', {}).update(packet['metadata'])
            self.statusBar().showMessage(
                'XMP 已读取 · 通用元数据与 CCRaw 配方 · CRS 参数未近似套用'
            )
        except (ValueError, OSError) as exc:
            self.error(str(exc))

    def closeEvent(self, event):
        if self.closing:
            if self.scheduler.active is None:
                self._accept_drained_close(event)
            else:
                event.ignore()
            return
        if self.work.busy():
            self.statusBar().showMessage('正在处理图像，请等待完成，或在 AI 窗口取消后关闭。')
            event.ignore()
            return
        if not self.confirm_close_library():
            event.ignore()
            return
        self.timer.stop()
        self.history_timer.stop()
        self.detail_timer.stop()
        self.cancel_detail()
        self.thumbnail_queue.clear()
        self.nl_recorder.cancel()
        self.nl_request += 1
        self.generation_panel.shutdown()
        self.closing = True
        if not self.scheduler.shutdown():
            self.statusBar().showMessage('正在完成当前预览任务后关闭…')
            event.ignore()
        else:
            self._accept_drained_close(event)

    def _accept_drained_close(self, event):
        # Shutdown suppresses success/failure callbacks, including the callback
        # that ends thumbnail activity. Clear those flags only after every
        # scheduler job has drained; a running worker must still block changes.
        self.work.pending_render = False
        for activity in A:
            self.work.end(activity)
        event.accept()
