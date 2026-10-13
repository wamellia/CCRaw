"""Discoverable, strict Agent edit contract over CCRaw's existing recipe model."""

import copy
import json
import math

from .. import model, nl_edit
from ..domain import professional


def obj(properties, required=()):
    return dict(
        type='object', properties=properties, required=list(required), additionalProperties=False
    )


def number(low, high):
    return dict(type='number', minimum=low, maximum=high)


def array(items, low, high):
    return dict(type='array', items=items, minItems=low, maxItems=high)


POINT = array(number(0, 1), 2, 2)
CURVE = array(POINT, 2, 256)
ADJUSTMENTS = obj(
    {k: dict(number(v[1], v[2]), description=v[0]) for k, v in nl_edit.PARAMS.items()}
)
MASK = obj(
    {
        'kind': dict(type='string', enum=['linear', 'radial', 'luminance', 'brush']),
        'region': dict(
            type='string', enum=[k for k in nl_edit.REGIONS if k not in nl_edit.AI_REGIONS]
        ),
        'index': dict(type='integer', minimum=0, maximum=31),
        'name': dict(type='string', maxLength=80),
        'start': POINT,
        'end': POINT,
        'invert': dict(type='boolean'),
        'enabled': dict(type='boolean'),
        'opacity': number(0, 100),
        'feather': number(0, 100),
        'luminance_range': array(number(0, 100), 2, 2),
        'range_falloff': number(0, 100),
        'strokes': array(
            obj(
                {
                    'points': array(POINT, 1, 1000),
                    'radius': number(0.001, 0.5),
                    'erase': dict(type='boolean'),
                },
                ['points', 'radius'],
            ),
            0,
            100,
        ),
        'adjustments': ADJUSTMENTS,
    },
    ['adjustments'],
)
COMPONENT_MASK = obj(
    {
        k: copy.deepcopy(v)
        for k, v in MASK['properties'].items()
        if k not in ('region', 'index', 'adjustments')
    },
    ['kind'],
)
MASK['properties']['components'] = array(
    obj(
        {
            'op': dict(type='string', enum=['add', 'subtract', 'intersect']),
            'mask': COMPONENT_MASK,
        },
        ['op', 'mask'],
    ),
    0,
    64,
)
PATCH_SCHEMA = obj(
    {
        'adjustments': ADJUSTMENTS,
        'effects': obj(
            {k: dict(number(v[1], v[2]), description=v[0]) for k, v in nl_edit.EFFECTS.items()}
        ),
        'hsl': obj(
            {
                k: obj({part: number(-100, 100) for part in ('hue', 'saturation', 'luminance')})
                for k, *_ in nl_edit.HSL_COLORS
            }
        ),
        'grading': obj(
            {
                **{
                    k: obj({'hue': number(0, 360), 'saturation': number(0, 100)})
                    for k in ('shadows', 'midtones', 'highlights')
                },
                'balance': number(-100, 100),
            }
        ),
        'curve': array(array(number(0, 255), 2, 2), 2, 256),
        'curves': obj({k: CURVE for k in ('RGB', 'R', 'G', 'B')}),
        'crop': dict(array(number(0, 1), 4, 4), type=['array', 'null']),
        'rotation': dict(
            type='integer', minimum=0, maximum=3, description='顺时针旋转次数，每次 90°'
        ),
        'straighten': number(-15, 15),
        'monochrome': dict(type='boolean'),
        'process': dict(type='integer', enum=[1, 2]),
        'professional': professional.describe(),
        'masks': array(MASK, 0, 8),
        'reset': dict(type='boolean'),
        'explanation': dict(type='string', maxLength=600),
    }
)


def check(value, schema, path='patch'):
    """Validate the bounded JSON types/ranges used by this contract (not arbitrary schemas)."""
    kind = schema['type']
    if isinstance(kind, list):
        if value is None and 'null' in kind:
            return
        kind = next(k for k in kind if k != 'null')
    valid = {
        'object': isinstance(value, dict),
        'array': isinstance(value, list),
        'string': isinstance(value, str),
        'boolean': type(value) is bool,
        'integer': type(value) is int,
        'number': type(value) in (int, float),
    }.get(kind, False)
    if not valid:
        raise ValueError(f'{path} 类型无效。')
    if kind in ('number', 'integer'):
        if not schema.get('minimum', -math.inf) <= value <= schema.get(
            'maximum', math.inf
        ) or not math.isfinite(value):
            raise ValueError(f'{path} 超出范围。')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path} 不支持此值。')
    if kind == 'string' and len(value) > schema.get('maxLength', 1000):
        raise ValueError(f'{path} 文字过长。')
    if kind == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 256):
            raise ValueError(f'{path} 数量超出范围。')
        for i, item in enumerate(value):
            check(item, schema['items'], f'{path}[{i}]')
    if kind == 'object':
        if (
            set(value) - schema['properties'].keys()
            or set(schema.get('required', [])) - value.keys()
        ):
            raise ValueError(f'{path} 包含未知字段或缺少必要字段。')
        for key, item in value.items():
            check(item, schema['properties'][key], path + '.' + key)


def prepare(base, patch):
    try:
        encoded = json.dumps(patch, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, OverflowError):
        raise ValueError('编辑参数必须为有限数值的 JSON 对象。') from None
    if len(encoded.encode('utf8')) > 64 * 1024:
        raise ValueError('编辑参数超过 64 KiB。')
    check(patch, PATCH_SCHEMA)
    if 'curve' in patch and 'RGB' in patch.get('curves', {}):
        raise ValueError('curve 与 curves.RGB 不能同时设置。')
    regular = {
        k: v
        for k, v in patch.items()
        if k
        not in (
            'crop',
            'rotation',
            'straighten',
            'curves',
            'masks',
            'curve',
            'professional',
            'process',
        )
    }
    target = nl_edit.plan(model.validate(base), regular).target
    if 'professional' in patch:
        target['professional'] = professional.merge(target['professional'], patch['professional'])
    for key in ('crop', 'rotation', 'straighten', 'process'):
        if key in patch:
            target[key] = copy.deepcopy(patch[key])
    target['curves'].update(copy.deepcopy(patch.get('curves', {})))
    if 'curve' in patch:
        target['curves']['RGB'] = [[x / 255, y / 255] for x, y in patch['curve']]
    for request in patch.get('masks', []):
        modes = set(request) & {'kind', 'region', 'index'}
        if len(modes) != 1:
            raise ValueError('蒙版需要指定 kind、region 或 index 中的一项。')
        if 'index' in request:
            if request['index'] >= len(target['masks']) or set(request) - {'index', 'adjustments'}:
                raise ValueError('已有蒙版编号或参数无效。')
            target['masks'][request['index']]['adjustments'].update(request['adjustments'])
        elif 'region' in request:
            if set(request) - {'region', 'adjustments'}:
                raise ValueError('区域蒙版只接受 region 和 adjustments。')
            plan = nl_edit.plan(target, {'masks': [request]})
            if plan.regions or plan.notes:
                raise ValueError('此选区需要在照片编辑器中创建。')
            target = plan.target
        else:
            shape_fields = {
                'linear': {'start', 'end'},
                'radial': {'start', 'end', 'feather'},
                'luminance': {'luminance_range', 'range_falloff'},
                'brush': {'strokes', 'feather'},
            }
            shared = {'kind', 'name', 'enabled', 'invert', 'opacity', 'adjustments', 'components'}
            if set(request) - shared - shape_fields[request['kind']]:
                raise ValueError('蒙版参数与种类不匹配。')
            if request['kind'] == 'brush' and not request.get('strokes'):
                raise ValueError('画笔蒙版至少需要一条笔划。')
            mask = model.new_mask(request['kind'], len(target['masks']) + 1)
            mask.update(copy.deepcopy({k: v for k, v in request.items() if k != 'adjustments'}))
            if 'components' in request:
                components = []
                for index, component in enumerate(request['components']):
                    leaf = component['mask']
                    if set(leaf) - shared - shape_fields[leaf['kind']]:
                        raise ValueError('组合蒙版参数与种类不匹配。')
                    part = model.new_mask(leaf['kind'], index + 1)
                    part.update(copy.deepcopy(leaf))
                    if part['kind'] in ('linear', 'radial') and part['start'] == part['end']:
                        raise ValueError('组合蒙版范围不能为空。')
                    if part['kind'] == 'brush' and not part.get('strokes'):
                        raise ValueError('组合画笔蒙版至少需要一条笔划。')
                    components.append(dict(op=component['op'], mask=part))
                mask['components'] = components
            mask['adjustments'].update(request['adjustments'])
            if mask['kind'] in ('linear', 'radial') and mask['start'] == mask['end']:
                raise ValueError('渐变或径向蒙版的范围不能为空。')
            target['masks'].append(mask)
    return model.validate(target)


def describe(recipe=None, section='adjustments'):
    if section not in PATCH_SCHEMA['properties']:
        raise ValueError('不支持的参数分区。')
    recipe = model.validate(recipe or model.recipe())
    current = recipe.get(section)
    if section == 'curve':
        current = [[x * 255, y * 255] for x, y in recipe['curves']['RGB']]
    elif section == 'hsl':
        current = {
            k: dict(zip(('hue', 'saturation', 'luminance'), values))
            for (k, *_), values in zip(nl_edit.HSL_COLORS, recipe['hsl'])
        }
    elif section == 'grading':
        current = {
            **{
                k: dict(zip(('hue', 'saturation'), recipe['grading'][k]))
                for k in ('shadows', 'midtones', 'highlights')
            },
            'balance': recipe['grading']['balance'],
        }
    return dict(
        section=section,
        sections=list(PATCH_SCHEMA['properties']),
        schema=copy.deepcopy(PATCH_SCHEMA['properties'][section]),
        current=current,
        coordinates='crop/masks use normalized unrotated original-image coordinates',
        execution='proposals only; UI approval is required to apply',
    )
