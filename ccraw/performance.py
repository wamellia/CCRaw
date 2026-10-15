"""Shared compute ceiling. Idle workers are not simultaneous image computations."""

import os

MAX_THREADS = 32
THREADS = min(MAX_THREADS, max(1, os.cpu_count() or 1))

for name in ('OMP_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[name] = str(min(8, THREADS))
for name in ('OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '1'


def configure():
    import cv2

    cv2.setNumThreads(min(8, THREADS))
    # NumPy may have been imported by a plugin before these environment variables
    # were set. RGB matrices are only 3x3; a large BLAS pool adds overhead and
    # competes with the actual pixel kernels and the GUI.
    try:
        from threadpoolctl import threadpool_limits

        threadpool_limits(limits=1, user_api='blas')
    except ImportError:
        pass


def session_options():
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = THREADS
    options.inter_op_num_threads = 1
    options.add_session_config_entry('session.intra_op.allow_spinning', '0')
    return options
