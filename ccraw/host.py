"""Windows per-user locations and input hints."""

from __future__ import annotations

import os
from pathlib import Path

WINDOWS = os.name == 'nt'
COMMAND = 'Ctrl'
OPTION = 'Alt'
NAVIGATION = '中键平移，滚轮放大'
ACCELERATION = '自动加速（DirectML / TensorRT for RTX）'
GPU_NOTE = '自动模式会尝试 AMD DirectML 或 NVIDIA CUDA，失败则使用 CPU。'


def keys(text):
    return text


def data_folder():
    """Per-user application data (``gpu-compat.json``)."""
    if value := os.environ.get('CCRAW_DATA_DIR'):
        return Path(value).expanduser().resolve()
    base = os.environ.get('LOCALAPPDATA')
    return (Path(base) if base else Path.home() / 'AppData' / 'Local') / 'CCRaw'


def log_folder():
    return data_folder() / 'logs'


def cache_folder():
    """Rebuildable processing caches."""
    if value := os.environ.get('CCRAW_CACHE_DIR'):
        return Path(value).expanduser().resolve()
    return data_folder() / 'cache'
