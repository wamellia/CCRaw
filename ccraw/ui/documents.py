from __future__ import annotations
from .. import branding
import copy
import threading
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QDialog, QProgressDialog
from .. import engine, model
from ..scheduler import Activity as A
from .. import watermark

from ..enhance_dialog import ExportDialog


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

        def loaded(result):
            self.source, self.info = result
            self.source_path = str(Path(path).resolve())
            if not explicit_project and (not document or not document['initialized']):
                for state in [edits] + ([document['saved_edits']] if document else []):
                    state['white_balance'] = copy.deepcopy(
                        self.info.get('white_balance', state['white_balance'])
                    )
                    state['develop'] = copy.deepcopy(self.info.get('develop', state['develop']))
                if document and 'history' in document:
                    for state in document['history'].items:
                        state['white_balance'] = copy.deepcopy(edits['white_balance'])
                        state['develop'] = copy.deepcopy(edits['develop'])
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
            self.loaded_document(self.source_path, explicit_project)
            self.refresh()
            self.update_thumbnails()
            self.changed()

        def failed(text):
            self.work.end(A.LOADING)
            self.open_button.setEnabled(True)
            self.refresh()
            self.error('无法打开文件：\n' + text)

        self.job(lambda: engine.load_image(path), loaded, failed)

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
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        ext = ['.jpg', '.png', '.tif', '.dng'][dialog.format.currentIndex()]
        path, _ = QFileDialog.getSaveFileName(
            self,
            '导出成片',
            str(Path(self.source_path).with_name(Path(self.source_path).stem + '-CCRaw' + ext)),
            f'图片 (*{ext})',
        )
        if not path:
            return
        if Path(path).suffix.lower() != ext:
            path += ext
        if Path(path).resolve() == Path(self.source_path).resolve():
            return self.error('请选择不同的文件名，以保留原片。')
        self.start_export(
            path,
            [1, 2, 4][dialog.scale.currentIndex()],
            dialog.model_choice(),
            dialog.quality.value(),
        )

    def start_export(self, path, scale=1, model_path='', quality=95):
        if self.work.busy(A.AI):
            return
        if self.work.busy(A.SELECTION):
            self.statusBar().showMessage('正在生成蒙版，请完成后再导出。')
            return
        if not self.work.can_start(A.EXPORTING):
            return
        if Path(path).resolve() == Path(self.source_path).resolve():
            return self.error('请选择不同的文件名，以保留原片。')
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
        photo = copy.deepcopy(self.info.get('photo', {}))
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
            result = engine.process(source, edits, engine.Backend('auto' if use_cuda else 'cpu'))
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
            result = watermark.apply(result, edits['watermark'], photo)
            engine.export_image(path, result, quality, photo=photo)
            return result.shape, backend

        def success(result):
            self.export_dialog.reset()
            self.work.end(A.EXPORTING)
            self.open_button.setEnabled(True)
            self.export_button.setEnabled(True)
            shape, backend = result
            self.statusBar().showMessage(f'导出完成 · {shape[1]} × {shape[0]} · {backend} · {path}')
            QMessageBox.information(
                self, '导出完成', f'已保存：\n{path}\n\n{shape[1]} × {shape[0]} 像素\n{backend}'
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

    def closeEvent(self, event):
        if self.closing:
            if self.scheduler.active is None:
                event.accept()
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
            event.accept()
