"""Float32 linear-light RAW pipeline, masks and optional GPU (DirectML / CUDA) processing."""

from __future__ import annotations
import numpy as np
from .. import large_image, performance, compute, gpu_graphs, cpu_ops

from .. import engine as pipeline


class Backend:
    """Windows pixel backend. DirectML / CUDA run pointwise ONNX graphs; NumPy is the reference.

    CuPy is an experimental, opt-in path (``CCRAW_EXPERIMENTAL_CUPY=1``);
    ``CCRAW_COMPUTE=cpu`` forces the CPU for troubleshooting.
    """

    def __init__(self, mode='auto'):
        self.xp = np
        self.use_dml = False
        self._sessions = {}
        self.name = 'CPU · NumPy / OpenCV'
        self.warning = ''
        if mode == 'cpu' or compute.requested_mode() == 'cpu':
            if mode != 'cpu':
                self.warning = 'CCRAW_COMPUTE=cpu：已按设置使用 CPU。'
            return
        if compute.cupy_enabled():
            try:
                import cupy as cp

                if cp.cuda.runtime.getDeviceCount():
                    test = cp.array([1.0], dtype=cp.float32)
                    float(cp.asnumpy(test)[0])
                    self.xp = cp
                    name = cp.cuda.runtime.getDeviceProperties(0)['name']
                    self.name = (
                        'CUDA · '
                        + (name.decode() if isinstance(name, bytes) else str(name))
                        + '（实验）'
                    )
            except Exception:
                pipeline.log.info('CuPy unavailable', exc_info=True)
        if self.xp is np:
            try:
                import onnxruntime as ort

                adapter = compute.preferred_adapter()
                if (
                    compute.requested_mode() in ('auto', 'dml')
                    and adapter
                    and ('DmlExecutionProvider' in ort.get_available_providers())
                ):
                    self.use_dml = True
                    self.name = 'DirectML · ' + adapter[1]
            except Exception:
                pipeline.log.info('DirectML detection failed', exc_info=True)
        if self.xp is np and (not self.use_dml):
            self.warning = '没有可用的 DirectML / CUDA，已使用 CPU。'

    @property
    def gpu_label(self):
        return 'DirectML'

    @property
    def gpu_pointwise(self):
        """True while fused stages run through DirectML graphs."""
        return self.use_dml

    def _disable_gpu(self, warning):
        self.name = f'CPU · {self.gpu_label} 回退'
        self.use_dml = False
        self.warning = warning
        pipeline.log.warning('GPU pixel graphs disabled: %s', warning)
        compute.state.report('CPUExecutionProvider', warning=warning)

    def _session(self, kind):
        if kind not in self._sessions:
            self._sessions[kind] = compute.session(gpu_graphs.model(kind), True)
        return self._sessions[kind]

    def _run_graph(self, kind, image, inputs):
        """Channel-last 1024² tiles: pointwise graphs have no boundary effects."""
        session = self._session(kind)
        height, width = image.shape[:2]
        out = large_image.allocate(image.shape)
        for y in range(0, height, pipeline.GPU_TILE):
            for x in range(0, width, pipeline.GPU_TILE):
                tile = np.ascontiguousarray(
                    image[y : y + pipeline.GPU_TILE, x : x + pipeline.GPU_TILE], dtype=np.float32
                )[None]
                out[y : y + tile.shape[1], x : x + tile.shape[2]] = session.run(
                    None, dict(inputs, image=tile)
                )[0][0]
        if session.get_providers()[0] == 'CPUExecutionProvider' and self.use_dml:
            self._disable_gpu(compute.state.snapshot()[3] or f'{self.gpu_label} 已回退 CPU。')
        return out

    def _gpu(self, kind, image, inputs):
        try:
            return self._run_graph(kind, image, inputs)
        except Exception as exc:
            pipeline.log.exception('GPU graph %s failed', kind)
            self._disable_gpu(f'{self.gpu_label} 运行失败，自动回退 CPU：' + str(exc)[:120])
            return None

    def tonal(self, image, a, cache=None, basis_key=None):
        if self.gpu_pointwise:
            result = self._gpu('tonal', image, gpu_graphs.tonal_inputs(a))
            if result is not None:
                return result
        try:
            result = self._tonal(image, a, self.xp, cache, basis_key)
            if self.xp is not np:
                result = self.xp.asnumpy(result)
                compute.state.report(
                    'CUDAExecutionProvider', self.name, 'CuPy · CUDA 光影显影（实验）'
                )
            else:
                compute.state.report(
                    'CPUExecutionProvider', detail=f'{performance.THREADS} 线程 · NumPy 光影显影'
                )
            return result
        except Exception as exc:
            if self.xp is np:
                raise
            pipeline.log.exception('CuPy tonal failed')
            self.xp = np
            self.name = 'CPU · CUDA 回退'
            self.warning = 'CUDA 运行失败，自动回退 CPU：' + str(exc)[:100]
            compute.state.report('CPUExecutionProvider', warning=self.warning)
            return self._tonal(image, a, np)

    def color(self, image, edits):
        """Saturation / vibrance, HSL, curves, monochrome and grading."""
        if self.gpu_pointwise:
            result = self._gpu('color', image, gpu_graphs.color_inputs(edits))
            if result is not None:
                return result
        return pipeline.color_stage(image, edits)

    def fused(self, image, edits):
        """Tonal + color in one GPU pass; only valid without spatial detail tools."""
        if self.gpu_pointwise:
            inputs = dict(
                gpu_graphs.tonal_inputs(edits['adjustments']), **gpu_graphs.color_inputs(edits)
            )
            result = self._gpu('fused', image, inputs)
            if result is not None:
                return result
        return pipeline.color_stage(self.tonal(image, edits['adjustments']), edits)

    def _tonal_dml(self, image, a):
        return self._run_graph('tonal', image, gpu_graphs.tonal_inputs(a))

    @staticmethod
    def _tonal(image, a, xp, cache=None, basis_key=None):
        if xp is np:
            result = cpu_ops.tonal(image, a, cache, basis_key)
            if result is not None:
                return result
        x = xp.asarray(image, dtype=xp.float32).copy()
        temp, tint = (a['temperature'] / 100, a['tint'] / 100)
        gains = xp.asarray(
            [
                2 ** (0.4 * temp + 0.15 * tint),
                2 ** (-0.15 * tint),
                2 ** (-0.4 * temp + 0.15 * tint),
            ],
            dtype=xp.float32,
        )
        x *= 2 ** a['exposure'] * gains
        if any((a[k] for k in ('shadows', 'highlights', 'blacks', 'whites'))):
            lum = x[..., 0] * 0.2126 + x[..., 1] * 0.7152 + x[..., 2] * 0.0722
            p = xp.clip(lum, 0, 1) ** 0.45
            sw = (1 - p) ** 2
            hw = p**3
            stops = (a['shadows'] * sw + a['highlights'] * hw) / 65
            stops += (a['blacks'] * (1 - p) ** 6 + a['whites'] * p**6) / 85
            x *= (2**stops)[..., None]
        if xp is np:
            result = cpu_ops.srgb(x, a['contrast'])
            if result is not None:
                return result
        x = xp.maximum(x, 0)
        x = xp.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)
        if a['contrast']:
            x = (x - 0.5) * (1 + a['contrast'] / 125) + 0.5
        return xp.clip(x, 0, 1)
