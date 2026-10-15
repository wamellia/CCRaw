"""Explicit recipe section transfer shared by the editor and Agent workflows."""

import copy

from .recipes import validate

SECTIONS = {
    'adjustments': '光影与基础色彩',
    'curves': '曲线',
    'hsl': 'HSL',
    'grading': '色彩分级',
    'effects': '效果',
    'monochrome': '黑白',
    'professional.optics': '光学',
    'professional.detail': '专业细节',
    'professional.calibration': '校准',
    'professional.point_color': '点颜色',
    'professional.bw_mixer': '黑白混色',
    'professional.profile': '配置文件',
    'professional.lensfun': '镜头配置文件',
    'professional.geometry': '几何',
    'professional.red_eye': '红眼',
    'white_balance': '白平衡',
    'develop': '显影基准',
    'crop': '裁切与旋转',
    'masks': '局部蒙版',
    'retouch': '修复',
    'watermark': '水印',
}
DEFAULT = tuple(
    k
    for k in SECTIONS
    if k
    not in (
        'professional.geometry',
        'professional.red_eye',
        'white_balance',
        'develop',
        'crop',
        'masks',
        'retouch',
        'watermark',
    )
)


def transfer(source, destination, sections=DEFAULT):
    if not isinstance(sections, (list, tuple)) or set(sections) - set(SECTIONS):
        raise ValueError('不支持的同步分组。')
    src, out = validate(source), validate(destination)
    for path in sections:
        if path.startswith('professional.'):
            key = path.split('.')[1]
            out['professional'][key] = copy.deepcopy(src['professional'][key])
        else:
            out[path] = copy.deepcopy(src[path])
            if path == 'curves':
                for key in ('tone_curve', 'curve_mode'):
                    out[key] = copy.deepcopy(src[key])
            if path == 'crop':
                for key in ('rotation', 'straighten'):
                    out[key] = src[key]
            if path == 'white_balance':
                out['wb_gain'] = copy.deepcopy(src['wb_gain'])
    return validate(out)
