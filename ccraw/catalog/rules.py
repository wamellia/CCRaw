"""Bounded, parameterized smart-album/filter rules; no evaluated strings."""

import math

FIELDS = {
    'rating': 'v.rating',
    'flag': 'v.flag',
    'color_label': 'v.color_label',
    'edited': '(v.edited_at > 0)',
    'name': 'catalog_name(v.path)',
    'size': 'v.size',
    'status': 'v.status',
    'capture': "json_extract(v.facts,'$.captured_at')",
    'camera': "json_extract(v.facts,'$.camera')",
    'lens': "json_extract(v.facts,'$.lens')",
    'iso': "json_extract(v.facts,'$.iso')",
    'format': 'catalog_extension(v.path)',
    'person_count': "CASE WHEN EXISTS(SELECT 1 FROM metadata_overrides WHERE photo_ref=v.id AND field='person_count') THEN (SELECT json_extract(value,'$') FROM metadata_overrides WHERE photo_ref=v.id AND field='person_count') ELSE json_extract(v.facts,'$.person_count') END",
}


def compile_rules(rule, album_sql=None, *, depth=0, counter=None):
    counter = counter if counter is not None else [0]
    if depth > 8 or not isinstance(rule, dict):
        raise ValueError('筛选规则最多 8 层，必须为结构化对象。')
    groups = set(rule) & {'all', 'any', 'none'}
    if groups:
        if len(groups) != 1 or len(rule) != 1:
            raise ValueError('筛选规则逻辑组无效。')
        key = next(iter(groups))
        children = rule[key]
        if not isinstance(children, list) or len(children) > 100:
            raise ValueError('筛选规则最多 100 个条件。')
        pieces, params = [], []
        for child in children:
            sql, values = compile_rules(child, album_sql, depth=depth + 1, counter=counter)
            pieces.append('(' + sql + ')')
            params.extend(values)
        expression = (' AND ' if key == 'all' else ' OR ').join(pieces) or (
            '1' if key == 'all' else '0'
        )
        return ('NOT (' + expression + ')' if key == 'none' else expression), params
    counter[0] += 1
    if counter[0] > 100 or set(rule) != {'field', 'op', 'value'}:
        raise ValueError('筛选规则字段无效或超过 100 个条件。')
    field, op, value = rule['field'], rule['op'], rule['value']
    if field == 'album':
        if op != 'eq' or not isinstance(value, str) or album_sql is None:
            raise ValueError('相册引用规则无效。')
        return album_sql(value)
    if field == 'keyword':
        if op not in ('eq', 'contains') or not isinstance(value, str) or len(value) > 200:
            raise ValueError('关键词规则无效。')
        if op == 'eq':
            return 'EXISTS(SELECT 1 FROM photo_keywords WHERE photo_ref=v.id AND keyword=?)', [
                value
            ]
        return (
            "EXISTS(SELECT 1 FROM photo_keywords WHERE photo_ref=v.id AND keyword LIKE ? ESCAPE '\\')",
            ['%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'],
        )
    if field not in FIELDS or op not in ('eq', 'ne', 'ge', 'le', 'gt', 'lt', 'contains', 'in'):
        raise ValueError('筛选包含不支持的字段或操作。')
    if op == 'in':
        if not isinstance(value, list) or not 1 <= len(value) <= 100:
            raise ValueError('筛选 in 列表需要 1–100 个值。')
        values = value
    else:
        values = [value]
    if any(
        type(v) not in (str, int, float, bool)
        or (isinstance(v, str) and len(v) > 1000)
        or (isinstance(v, float) and not math.isfinite(v))
        for v in values
    ):
        raise ValueError('筛选值必须为有限数字或短文字。')
    if field in ('rating', 'iso', 'person_count', 'size'):
        if any(type(v) not in (int, float) or v < 0 for v in values):
            raise ValueError('数值筛选字段需要非负数字。')
        if field == 'rating' and any(v > 5 for v in values):
            raise ValueError('评级必须为 0–5。')
    expression = FIELDS[field]
    if op == 'in':
        return expression + ' IN (' + ','.join('?' for _ in values) + ')', values
    if op == 'contains':
        if not isinstance(value, str):
            raise ValueError('包含筛选需要文字。')
        escaped = value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        return expression + " LIKE ? ESCAPE '\\'", ['%' + escaped + '%']
    operator = {'eq': '=', 'ne': '!=', 'ge': '>=', 'le': '<=', 'gt': '>', 'lt': '<'}[op]
    return expression + operator + '?', [value]


def describe_rules(rule):
    """Describe the exact executed constraints in user-facing language."""
    names = {
        'rating': '评级',
        'flag': '选片标记',
        'color_label': '颜色标签',
        'capture': '拍摄日期',
        'camera': '相机',
        'lens': '镜头',
        'iso': 'ISO',
        'format': '格式',
        'person_count': '人数',
        'keyword': '关键词',
        'edited': '已编辑',
        'name': '文件名',
        'size': '文件大小',
        'album': '相册',
        'status': '原片状态',
    }
    operations = {
        'eq': '等于',
        'ne': '不等于',
        'ge': '至少',
        'le': '至多',
        'gt': '大于',
        'lt': '小于',
        'contains': '包含',
        'in': '属于',
    }
    for key, join in [('all', ' 且 '), ('any', ' 或 '), ('none', ' 或 ')]:
        if key in rule:
            parts = [describe_rules(child) for child in rule[key]]
            parts = [part for part in parts if part != '全部照片']
            expression = join.join(parts) or ('无匹配照片' if key == 'any' else '全部照片')
            return (
                '排除（' + expression + '）'
                if key == 'none'
                else '（' + expression + '）'
                if len(parts) > 1
                else expression
            )
    value = rule.get('value', '')
    if rule.get('field') in ('flag', 'color_label', 'status') and isinstance(value, str):
        value = {
            'pick': '保留',
            'reject': '拒绝',
            'none': '未标记',
            'red': '红',
            'yellow': '黄',
            'green': '绿',
            'blue': '蓝',
            'purple': '紫',
            'missing': '缺失',
            'ready': '可用',
        }.get(value, value)
    if type(value) is bool:
        value = '是' if value else '否'
    return (
        names.get(rule.get('field'), rule.get('field', ''))
        + ' '
        + operations.get(rule.get('op'), rule.get('op', ''))
        + ' '
        + str(value)
    )
