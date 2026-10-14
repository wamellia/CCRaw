"""Run unmodified semi-utils rendering classes without its application dependencies.

Only import/application plumbing is adapted; trim, resize, rich text, concat and
watermark algorithms are compiled directly from a supplied upstream checkout.
This adapter is used to regenerate the checked-in PNG footer fixtures.
"""

import ast
import copy
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont


def render_reference(root, size, exif):
    import typing
    from abc import ABC
    from collections.abc import MutableMapping
    from dataclasses import asdict, dataclass
    from enum import Enum
    from jinja2 import Environment

    root = Path(root)
    ns = dict(
        vars(typing),
        ABC=ABC,
        MutableMapping=MutableMapping,
        dataclass=dataclass,
        asdict=asdict,
        Enum=Enum,
        np=np,
        Image=Image,
        ImageDraw=ImageDraw,
        ImageFont=ImageFont,
        Path=Path,
        BASE_FONT_SIZE=512,
        fonts_dir=root / 'config/fonts',
        get_processor=lambda _: None,
        _parse_color=lambda c: ImageColor.getcolor(c, 'RGBA') if isinstance(c, str) else c,
    )

    def load(file, names):
        tree = ast.parse((root / file).read_text(encoding='utf-8'))
        tree.body = [node for node in tree.body if getattr(node, 'name', '') in names]
        exec(compile(tree, str(root / file), 'exec'), ns)

    load('processor/types.py', {'Alignment'})
    load('processor/core.py', {'Direction', 'PipelineContext'})
    ns['ImageProcessor'] = type('ImageProcessor', (), {})
    load(
        'processor/filters.py', {'FilterProcessor', 'TrimFilter', 'ResizeFilter', 'WatermarkFilter'}
    )
    load(
        'processor/generators.py',
        {'load_font', 'TextSegment', 'Generator', 'RichTextGenerator', 'MultiRichTextGenerator'},
    )
    load('processor/mergers.py', {'_calc_offset', 'Merger', 'ConcatMerger'})
    classes = {
        name: ns[cls]
        for name, cls in {
            'trim': 'TrimFilter',
            'resize': 'ResizeFilter',
            'rich_text': 'RichTextGenerator',
            'multi_rich_text': 'MultiRichTextGenerator',
            'concat': 'ConcatMerger',
            'watermark': 'WatermarkFilter',
        }.items()
    }

    def start(pipeline, input_path=None, output_path=None, initial_buffer=None):
        buffer = initial_buffer or []
        for config in pipeline:
            context = ns['PipelineContext'](dict(config, buffer=buffer))
            classes[config['processor_name']]().process(context)
            buffer = context.get_buffer()
        return buffer[0]

    ns['start_process'] = start
    module = types.ModuleType('processor.core')
    module.start_process = start
    environment = Environment()
    environment.globals['auto_logo'] = lambda: str(
        root / 'config/logos' / (exif['Make'].lower() + '.png')
    )
    template = (root / 'config/templates/标准水印.json').read_text(encoding='utf-8')
    config = json.loads(environment.from_string(template).render(exif=exif))
    with patch.dict(sys.modules, {'processor.core': module}):
        output = start(copy.deepcopy(config), initial_buffer=[Image.new('RGB', size, '#53716B')])
    return output.convert('RGB')
