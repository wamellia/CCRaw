"""Resolve source, installed and frozen resources without workstation paths."""

import os
import sys
from pathlib import Path


def bundled_path(*parts):
    """Resolve resources shipped with the package, independently of optional models."""
    root = (Path(__file__).resolve().parent / 'resources').resolve()
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Resource path must stay inside the package resource directory.')
    return path


def asset_root():
    if value := os.environ.get('CCRAW_ASSET_DIR'):
        return Path(value).expanduser().resolve()
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'assets'
    source = Path(__file__).resolve().parents[1] / 'assets'
    if (source / 'runtime-assets.json').is_file():
        return source
    from .host import data_folder

    installed = data_folder() / 'assets'
    return installed if installed.is_dir() else Path(__file__).resolve().parent / 'resources'


def asset_path(*parts):
    root = asset_root().resolve()
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Resource path must stay inside the asset directory.')
    if (
        not path.exists()
        and not os.environ.get('CCRAW_ASSET_DIR')
        and len(parts) == 1
        and parts[0] in ('NotoSansSC.ttf', 'OFL.txt', 'ccraw.ico', 'ccraw.png', 'ccraw.svg')
    ):
        name = parts[0]
        group = (
            'fonts' if name == 'NotoSansSC.ttf' else 'licenses' if name == 'OFL.txt' else 'icons'
        )
        packaged = bundled_path(group, name)
        if packaged.is_file():
            return packaged
    return path
