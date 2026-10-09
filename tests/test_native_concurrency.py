"""Exercise the workqueue backend shared by concurrent editor workers."""

import os
import subprocess
import sys


def test_parallel_native_kernels_accept_concurrent_image_workers(tmp_path):
    script = """
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import numpy as np
from ccraw import native_kernels as kernels

kernels.warm()
assert kernels.enabled
x = np.linspace(0, 1, 1024 * 1024, dtype=np.float32)
table = np.linspace(0, 1, 4097)
axis = np.array([0.0, 1.0])
hsv = np.zeros((512, 512, 3), np.float32)
weights = np.ones((4, 512, 512), np.float32)
adjustments = dict(shadows=0, highlights=0, blacks=0, whites=0, contrast=0)
barrier = Barrier(2)

def worker():
    for round in range(8):
        barrier.wait(timeout=30)
        kind = round % 4
        if kind == 0:
            result = kernels.interp(x, table)
            np.testing.assert_array_equal(result, x)
        elif kind == 1:
            result = kernels.interp(x, axis, axis)
            np.testing.assert_array_equal(result, x)
        elif kind == 2:
            result = kernels.hsl(hsv, [0.0, 360.0], np.zeros((2, 3)))
            np.testing.assert_array_equal(result, hsv)
        else:
            result = kernels.range_srgb(hsv, weights, adjustments)
            np.testing.assert_array_equal(result, hsv)

with ThreadPoolExecutor(max_workers=2) as executor:
    futures = [executor.submit(worker) for _ in range(2)]
    for future in futures:
        future.result()
print('Concurrent native workers completed')
"""
    environment = dict(
        os.environ,
        NUMBA_THREADING_LAYER='workqueue',
        NUMBA_NUM_THREADS='2',
        NUMBA_CACHE_DIR=str(tmp_path / 'jit-cache'),
        PYTHONDONTWRITEBYTECODE='1',
    )
    result = subprocess.run(
        [sys.executable, '-B', '-c', script],
        capture_output=True,
        text=True,
        env=environment,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Concurrent native workers completed' in result.stdout
