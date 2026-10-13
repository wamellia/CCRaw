"""Neutral professional controls shared by recipes, widgets and Agent discovery."""

import copy
import math


# label, min, max, neutral, step; ids remain stable across UI languages.
CONTROLS = {
    'optics': {
        'distortion': ('畸变', -100, 100, 0, 1),
        'vignetting': ('镜头暗角', -100, 100, 0, 1),
        'midpoint': ('暗角中点', 0, 100, 50, 1),
        'ca_red': ('红青色差', -100, 100, 0, 1),
        'ca_blue': ('蓝黄色差', -100, 100, 0, 1),
        'defringe_purple': ('去紫边', 0, 100, 0, 1),
        'defringe_green': ('去绿边', 0, 100, 0, 1),
    },
    'geometry': {
        'vertical': ('垂直透视', -100, 100, 0, 1),
        'horizontal': ('水平透视', -100, 100, 0, 1),
        'aspect': ('纵横变形', -100, 100, 0, 1),
        'scale': ('缩放 %', 50, 150, 100, 1),
        'offset_x': ('水平偏移', -100, 100, 0, 1),
        'offset_y': ('垂直偏移', -100, 100, 0, 1),
    },
    'calibration': {
        'shadows_tint': ('阴影色调', -100, 100, 0, 1),
        'red_hue': ('红原色色相', -100, 100, 0, 1),
        'red_saturation': ('红原色饱和度', -100, 100, 0, 1),
        'green_hue': ('绿原色色相', -100, 100, 0, 1),
        'green_saturation': ('绿原色饱和度', -100, 100, 0, 1),
        'blue_hue': ('蓝原色色相', -100, 100, 0, 1),
        'blue_saturation': ('蓝原色饱和度', -100, 100, 0, 1),
    },
    'detail': {
        'amount': ('锐化量', 0, 150, 0, 1),
        'radius': ('锐化半径 px', 0.5, 3, 1, 0.1),
        'detail': ('锐化细节', 0, 100, 25, 1),
        'masking': ('锐化遮罩', 0, 100, 0, 1),
        'luminance': ('亮度降噪', 0, 100, 0, 1),
        'luminance_detail': ('降噪细节', 0, 100, 50, 1),
        'luminance_contrast': ('降噪对比', 0, 100, 0, 1),
        'color': ('色彩降噪', 0, 100, 0, 1),
        'color_detail': ('色彩细节', 0, 100, 50, 1),
        'color_smoothness': ('色彩平滑', 0, 100, 50, 1),
    },
}


def defaults():
    out = {
        group: {key: spec[3] for key, spec in fields.items()} for group, fields in CONTROLS.items()
    }
    out['geometry'].update(flip_x=False, flip_y=False)
    out.update(
        bw_mixer=[0.0] * 8,
        point_color=[],
        profile={'path': '', 'amount': 100.0, 'mode': 'auto'},
        red_eye=[],
        lensfun=None,
    )
    return out


def numeric(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('专业调整参数超出范围。')
    return float(value)


def validate(data):
    out = defaults()
    if not isinstance(data, dict) or set(data) - set(out):
        raise ValueError('专业调整包含不支持的字段。')
    for group, fields in CONTROLS.items():
        values = data.get(group, {})
        extra = {'flip_x', 'flip_y'} if group == 'geometry' else set()
        if not isinstance(values, dict) or set(values) - set(fields) - extra:
            raise ValueError('专业调整分区无效。')
        for key, spec in fields.items():
            out[group][key] = numeric(values.get(key, spec[3]), spec[1], spec[2])
        for key in extra:
            value = values.get(key, False)
            if type(value) is not bool:
                raise ValueError('翻转参数无效。')
            out[group][key] = value
    mixer = data.get('bw_mixer', out['bw_mixer'])
    if not isinstance(mixer, list) or len(mixer) != 8:
        raise ValueError('黑白混色需要八个色带。')
    out['bw_mixer'] = [numeric(v, -100, 100) for v in mixer]
    samples = data.get('point_color', [])
    if not isinstance(samples, list) or len(samples) > 16:
        raise ValueError('最多支持 16 个颜色样本。')
    for sample in samples:
        base = dict(
            rgb=[0.5, 0.5, 0.5],
            hue=0.0,
            saturation=0.0,
            luminance=0.0,
            hue_range=30.0,
            saturation_range=50.0,
            luminance_range=50.0,
            feather=50.0,
            enabled=True,
            name='',
        )
        if not isinstance(sample, dict) or set(sample) - set(base):
            raise ValueError('点颜色样本无效。')
        rgb = sample.get('rgb')
        if not isinstance(rgb, list) or len(rgb) != 3:
            raise ValueError('颜色样本需要 RGB 值。')
        base['rgb'] = [numeric(v, 0, 1) for v in rgb]
        if not isinstance(sample.get('name', ''), str) or len(sample.get('name', '')) > 80:
            raise ValueError('点颜色名称过长或无效。')
        base['name'] = sample.get('name', '')
        for key in ('hue', 'saturation', 'luminance'):
            base[key] = numeric(sample.get(key, 0), -100, 100)
        for key in ('hue_range', 'saturation_range', 'luminance_range', 'feather'):
            base[key] = numeric(sample.get(key, base[key]), 0, 100)
        if type(sample.get('enabled', True)) is not bool:
            raise ValueError('颜色样本开关无效。')
        base['enabled'] = sample.get('enabled', True)
        out['point_color'].append(base)
    profile = data.get('profile', out['profile'])
    if not isinstance(profile, dict) or set(profile) - {'path', 'amount', 'mode'}:
        raise ValueError('配置文件参数无效。')
    path = profile.get('path', '')
    if not isinstance(path, str) or len(path) > 1000 or '\x00' in path:
        raise ValueError('配置文件路径无效。')
    mode = profile.get('mode', 'auto')
    if mode not in ('auto', 'camera', 'look'):
        raise ValueError('配置文件模式无效。')
    out['profile'] = dict(path=path, amount=numeric(profile.get('amount', 100), 0, 200), mode=mode)
    lens = data.get('lensfun')
    if lens is not None:
        if not isinstance(lens, dict) or set(lens) - {
            'path',
            'sha256',
            'maker',
            'model',
            'focal',
            'calibration',
        }:
            raise ValueError('镜头配置文件无效。')
        cal = lens.get('calibration', {})
        count = {'poly3': 1, 'poly5': 2, 'ptlens': 3}.get(cal.get('model'))
        values = cal.get('coefficients', [])
        if count is None or not isinstance(values, (list, tuple)) or len(values) != count:
            raise ValueError('镜头标定无效。')
        clean_cal = dict(
            model=cal['model'],
            focal=numeric(cal.get('focal', lens.get('focal')), 0.1, 2000),
            coefficients=[numeric(v, -10, 10) for v in values],
        )
        from ..imaging.lensfun_profiles import radial_factor

        radial_factor([0, 1, 2], clean_cal)
        out['lensfun'] = {
            k: str(lens.get(k, ''))[:1000] for k in ('path', 'sha256', 'maker', 'model')
        }
        out['lensfun'].update(focal=numeric(lens.get('focal'), 0.1, 2000), calibration=clean_cal)
    eyes = data.get('red_eye', [])
    if not isinstance(eyes, list) or len(eyes) > 50:
        raise ValueError('红眼修正过多。')
    for eye in eyes:
        if not isinstance(eye, dict) or set(eye) - {'center', 'radius', 'amount'}:
            raise ValueError('红眼修正参数无效。')
        center = eye.get('center')
        if not isinstance(center, list) or len(center) != 2:
            raise ValueError('红眼位置无效。')
        out['red_eye'].append(
            dict(
                center=[numeric(v, 0, 1) for v in center],
                radius=numeric(eye.get('radius', 0.02), 0.001, 0.2),
                amount=numeric(eye.get('amount', 100), 0, 100),
            )
        )
    return out


def describe():
    groups = {}
    for group, fields in CONTROLS.items():
        props = {
            key: dict(type='number', minimum=s[1], maximum=s[2], default=s[3], description=s[0])
            for key, s in fields.items()
        }
        if group == 'geometry':
            props.update({key: dict(type='boolean') for key in ('flip_x', 'flip_y')})
        groups[group] = dict(type='object', properties=props, additionalProperties=False)
    number = dict(type='number', minimum=-100, maximum=100)
    groups['bw_mixer'] = dict(type='array', items=number, minItems=8, maxItems=8)
    unit = dict(type='number', minimum=0, maximum=1)
    point = dict(
        type='object',
        additionalProperties=False,
        required=['rgb'],
        properties={
            'rgb': dict(type='array', items=unit, minItems=3, maxItems=3),
            **{k: number for k in ('hue', 'saturation', 'luminance')},
            **{
                k: dict(type='number', minimum=0, maximum=100)
                for k in ('hue_range', 'saturation_range', 'luminance_range', 'feather')
            },
            'enabled': dict(type='boolean'),
            'name': dict(type='string', maxLength=80),
        },
    )
    groups['point_color'] = dict(type='array', items=point, maxItems=16)
    eye = dict(
        type='object',
        additionalProperties=False,
        required=['center'],
        properties={
            'center': dict(type='array', items=unit, minItems=2, maxItems=2),
            'radius': dict(type='number', minimum=0.001, maximum=0.2),
            'amount': dict(type='number', minimum=0, maximum=100),
        },
    )
    groups['red_eye'] = dict(type='array', items=eye, maxItems=50)
    # Resources imported via UI are intentionally not an Agent filesystem capability.
    return dict(type='object', properties=groups, additionalProperties=False)


def neutral(data):
    return validate(data or {}) == defaults()


def merge(base, patch):
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(copy.deepcopy(value))
        else:
            out[key] = copy.deepcopy(value)
    return validate(out)
