"""Measure held-slider feedback with a real Qt event loop and CPU pipeline.

python tools/bench_live_preview.py photo.png result.json [--root old/source]
Measures on-screen frame arrival separately from the input event rate. Screenshots
are saved after timing, so PNG encoding cannot falsify event-loop stall results.
"""

import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('photo')
    parser.add_argument('output')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--seconds', type=float, default=3)
    args = parser.parse_args()
    sys.path.insert(0, args.root)
    import numpy as np
    from PySide6.QtCore import QEventLoop, QTimer, QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from ccraw import engine
    from ccraw.app import MainWindow, STYLE

    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    errors = []
    QMessageBox.warning = lambda *a: errors.append(str(a[2]))
    QMessageBox.information = lambda *a: None
    QMessageBox.question = lambda *a: QMessageBox.StandardButton.Discard
    w = MainWindow()
    w.resize(1600, 1040)
    w.show()

    def settle(timeout=60):
        start = time.perf_counter()
        while w.jobs or w.timer.isActive() or w.detail_timer.isActive() or w.loading:
            app.processEvents()
            time.sleep(0.002)
            if time.perf_counter() - start > timeout:
                raise TimeoutError('preview did not settle')

    w.open_path(str(Path(args.photo).resolve()))
    settle()
    w.backend_combo.setCurrentIndex(1)
    settle()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    scenarios = [
        ('exposure', 'exposure'),
        ('contrast', 'contrast'),
        ('dehaze', 'dehaze'),
        ('local_mask', 'exposure'),
        ('zoom_100', 'exposure'),
        ('curve', 'curve'),
        ('exposure_curve', 'exposure_curve'),
        ('grading_wheel', 'wheel'),
        ('clarity', 'clarity'),
        ('hsl', 'hsl'),
        ('vignette', 'vignette'),
    ]
    for name, parameter in scenarios:
        w.reset_edits()
        w.canvas.fit()
        w.tabs.setCurrentIndex(0)
        settle()
        if name == 'local_mask':
            w.tabs.setCurrentIndex(4)
            w.add_mask('radial')
            w.overlay_check.setChecked(True)
            settle()
        elif name == 'zoom_100':
            w.canvas.actual_size()
            settle()
        widget = None
        if name in ('curve', 'exposure_curve', 'grading_wheel'):
            w.tabs.setCurrentIndex({'curve': 1, 'exposure_curve': 0, 'grading_wheel': 6}[name])
            widget = w.wheels['midtones'] if name == 'grading_wheel' else getattr(w, name)
            if name == 'grading_wheel':
                center, radius = widget.center_radius()
                start = center.toPoint() + QPoint(round(radius * 0.3), 0)
            else:
                start = (
                    widget.screen(0.5, 0.5).toPoint()
                    if name == 'exposure_curve'
                    else widget.screen([0.5, 0.5]).toPoint()
                )
            slider = None
        else:
            control = (
                w.local_controls[parameter]
                if name == 'local_mask'
                else w.hsl_controls[1]
                if name == 'hsl'
                else w.effect_controls[parameter]
                if name == 'vignette'
                else w.controls[parameter]
            )
            slider = control.slider
        before = w.canvas.image.copy()
        frames, beats, queues, lengths = [], [], [], []
        compute = []
        quality = getattr(w, 'preview_quality', None)
        record = quality.record if quality is not None else None
        if quality is not None:

            def measured(seconds):
                compute.append(seconds * 1000)
                record(seconds)

            quality.record = measured
        held_image = None
        original = w.canvas.set_image
        original_detail = w.canvas.set_detail
        started = time.perf_counter()

        def presented(image):
            nonlocal held_image
            original(image)
            if name == 'zoom_100':
                return
            frames.append(time.perf_counter() - started)
            lengths.append(max(w.rendered.shape[:2]))
            held_image = w.canvas.image.copy()

        def detail_presented(layer):
            original_detail(layer)
            if (
                name == 'zoom_100'
                and layer is not None
                and layer.main
                and getattr(w, '_preview_dragging', 0)
            ):
                frames.append(time.perf_counter() - started)
                lengths.append(max(w.rendered.shape[:2]))

        w.canvas.set_image = presented
        w.canvas.set_detail = detail_presented
        loop = QEventLoop()
        input_timer, heartbeat = QTimer(), QTimer()
        input_timer.setInterval(12)
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: beats.append(time.perf_counter()))
        step = 0
        end = start if widget is not None else None

        def move():
            nonlocal step, end
            step += 1
            low, high = (-1.2, 1.2) if parameter == 'exposure' else (0, 70)
            t = (np.sin(step * 0.08) + 1) / 2
            if widget is not None:
                end = start + QPoint(
                    round(15 * np.sin(step * 0.08)) if name == 'grading_wheel' else 0,
                    round(35 * np.sin(step * 0.08)),
                )
                QTest.mouseMove(widget, end)
            else:
                slider.setValue(round((low + (high - low) * t) * control.factor))
            queues.append(len(w.scheduler.queue))

        if widget is not None:
            QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
        else:
            slider.setSliderDown(True)
        heartbeat.start()
        input_timer.timeout.connect(move)
        input_timer.start()
        QTimer.singleShot(round(args.seconds * 1000), loop.quit)
        loop.exec()
        duration = time.perf_counter() - started
        heartbeat.stop()
        input_timer.stop()
        held_canvas = w.canvas.grab().toImage()
        w.canvas.set_image = original
        w.canvas.set_detail = original_detail
        if quality is not None:
            quality.record = record
        released = time.perf_counter()
        if widget is not None:
            QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=end)
        else:
            slider.setSliderDown(False)
        settle()
        refined_ms = (time.perf_counter() - released) * 1000
        expected = engine.process(
            w.source,
            w.edits,
            engine.Backend('cpu'),
            apply_crop=False,
            detail_scale=max(0.1, w.source.shape[1] / w.info['width']),
        )
        error = float(np.max(np.abs(expected - w.rendered)))
        gaps = np.diff(beats) * 1000
        frame_gaps = np.diff(frames) * 1000
        row = dict(
            scenario=name,
            input_events=step,
            held_seconds=round(duration, 3),
            frames_while_held=len(frames),
            fps=round(len(frames) / duration, 2),
            first_frame_ms=round(frames[0] * 1000, 1) if frames else None,
            median_frame_gap_ms=round(float(np.median(frame_gaps)), 1) if len(frame_gaps) else None,
            p95_heartbeat_gap_ms=round(float(np.percentile(gaps, 95)), 1) if len(gaps) else None,
            max_heartbeat_gap_ms=round(float(gaps.max()), 1) if len(gaps) else None,
            max_queue=max(queues, default=0),
            interactive_long_edges=sorted(set(lengths)),
            median_worker_ms=round(float(np.median(compute)), 1) if compute else None,
            release_to_settled_ms=round(refined_ms, 1),
            final_preview_max_error=error,
        )
        rows.append(row)
        before.save(str(output.with_name(output.stem + '-' + name + '-before.png')))
        held_canvas.save(str(output.with_name(output.stem + '-' + name + '-held-canvas.png')))
        if held_image is not None:
            held_image.save(str(output.with_name(output.stem + '-' + name + '-held.png')))
        w.canvas.image.save(str(output.with_name(output.stem + '-' + name + '-final.png')))
        print(json.dumps(row), flush=True)
    report = dict(
        root=args.root,
        source_photo=str(Path(args.photo).resolve()),
        source_size=[w.info['width'], w.info['height']],
        preview_size=list(w.source.shape[1::-1]),
        backend='CPU',
        event_loop='Qt QEventLoop; 12ms input timer; 10ms heartbeat',
        results=rows,
        errors=errors,
    )
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    w.saved_edits = copy.deepcopy(w.edits)
    w.saved_snapshots = copy.deepcopy(w.snapshots)
    w.documents.clear()
    w.close()
    app.processEvents()
    return 0 if not errors and all(r['final_preview_max_error'] < 1e-5 for r in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
