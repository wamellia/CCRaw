"""Allowlisted metadata constraints and bounded lexical/vector hybrid ranking."""

from datetime import date, datetime
from pathlib import Path
import re
from zoneinfo import ZoneInfo


from .models import normalized

FIELDS = {
    'terms',
    'semantic_text',
    'from_date',
    'to_date',
    'person_count',
    'person_cluster',
    'strict_people',
    'quality',
    'tags',
    'photo_ids',
    'limit',
}


def validate_query(value):
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError('查询包含不支持的字段。')
    query = dict(value)
    for key in ('terms', 'semantic_text'):
        text = query.get(key, '')
        if not isinstance(text, str) or len(text) > 1000:
            raise ValueError('查询文字最多 1000 字。')
        query[key] = text.strip()
    for key in ('from_date', 'to_date'):
        if query.get(key) is not None:
            if not isinstance(query[key], str) or not re.fullmatch(
                r'\d{4}-\d{2}-\d{2}', query[key]
            ):
                raise ValueError('日期格式必须为 YYYY-MM-DD。')
            date.fromisoformat(query[key])
    if query.get('from_date') and query.get('to_date') and query['from_date'] >= query['to_date']:
        raise ValueError('日期区间无效；结束日期不包含当天。')
    for key, default, low, high in (('limit', 50, 1, 100), ('person_count', None, 0, 50)):
        number = query.get(key, default)
        if number is not None and (type(number) is not int or not low <= number <= high):
            raise ValueError(f'{key} 超出范围。')
        query[key] = number
    if type(query.get('strict_people', False)) is not bool:
        raise ValueError('strict_people 必须为布尔值。')
    if query.get('quality', 'any') not in ('any', 'good', 'low'):
        raise ValueError('质量筛选无效。')
    for key, limit in (('tags', 12), ('photo_ids', 500)):
        values = query.get(key, [])
        if (
            not isinstance(values, list)
            or len(values) > limit
            or any(not isinstance(v, str) or not 0 < len(v) <= 100 for v in values)
        ):
            raise ValueError('查询列表无效。')
        query[key] = values
    cluster = query.get('person_cluster')
    if cluster is not None and (
        not isinstance(cluster, str) or not re.fullmatch('person-[0-9a-f]{12}', cluster)
    ):
        raise ValueError('人物簇编号无效。')
    return query


def parse_query(text, *, today=None, timezone='Asia/Hong_Kong'):
    if not isinstance(text, str) or len(text) > 1000:
        raise ValueError('查询文字最多 1000 字。')
    today = today or datetime.now(ZoneInfo(timezone)).date()
    query = dict(terms=text, semantic_text=text)
    year = today.year - 1 if '去年' in text else today.year if '今年' in text else None
    match = re.search(r'(20\d{2})年?', text)
    if match:
        year = int(match[1])
    if year:
        query.update(from_date=f'{year}-01-01', to_date=f'{year + 1}-01-01')
        for season, start, end in (
            ('春', '03-01', '06-01'),
            ('夏', '06-01', '09-01'),
            ('秋', '09-01', '12-01'),
        ):
            if season in text:
                query.update(from_date=f'{year}-{start}', to_date=f'{year}-{end}')
    people = re.search(r'([0-9]+|一|二|两|三|四|五|六)(?:个)?人', text)
    if people:
        values = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6}
        query['person_count'] = values.get(
            people[1], int(people[1]) if people[1].isdigit() else None
        )
    words = [
        ('海边', 'beach'),
        ('海滩', 'beach'),
        ('日落', 'sunset'),
        ('日出', 'sunrise'),
        ('人像', 'portrait'),
        ('山', 'mountains'),
        ('夜景', 'night'),
        ('猫', 'cat'),
        ('狗', 'dog'),
    ]
    matched = [(zh, en) for zh, en in words if zh in text]
    if matched:
        query['terms'] = ' '.join(zh for zh, _ in matched)
        query['semantic_text'] = 'a photo of ' + ' and '.join(en for _, en in matched)
        if query.get('person_count') is not None:
            query['semantic_text'] += f' with {query["person_count"]} people'
    return validate_query(query)


def search(project, query, *, models=None, query_vector=None):
    query = validate_query(query)
    if query_vector is None and models is not None and query['semantic_text']:
        query_vector = models.text_vector(query['semantic_text'])
    vector = normalized(query_vector) if query_vector is not None else None
    results = []
    membership = None
    if query.get('person_cluster'):
        from .analysis import suggestions

        membership = next(
            (
                set(g['photos'])
                for g in suggestions(project)['people']
                if g['id'] == query['person_cluster']
            ),
            set(),
        )
    preferences = project.preferences()
    terms = [v for v in re.split(r'\s+', query['terms'].casefold()) if v]
    for offset in range(0, project.summary()['photos'], 200):
        for photo in project.photos(200, offset):
            if photo['status'] != 'ready':
                continue
            facts = photo['facts']
            annotation = preferences.get('annotation:' + photo['id'], {})
            evidence = []
            captured = facts.get('captured_at')
            if query.get('from_date') or query.get('to_date'):
                if (
                    not captured
                    or (query.get('from_date') and captured[:10] < query['from_date'])
                    or (query.get('to_date') and captured[:10] >= query['to_date'])
                ):
                    continue
                evidence.append('拍摄日期')
            if query['photo_ids'] and photo['id'] not in query['photo_ids']:
                continue
            if membership is not None:
                if photo['id'] not in membership:
                    continue
                evidence.append('匿名人物簇候选')
            count = annotation.get('person_count', facts.get('person_count'))
            if query['person_count'] is not None:
                if count is not None and count != query['person_count']:
                    continue
                if count is None:
                    if query.get('strict_people'):
                        continue
                    evidence.append('人数未核验')
                else:
                    evidence.append('已标注人数')
            flags = facts.get('flags', [])
            if (query.get('quality') == 'good' and flags) or (
                query.get('quality') == 'low' and not flags
            ):
                continue
            tags = annotation.get('tags', [])
            if query['tags'] and not set(query['tags']).issubset(tags):
                continue
            haystack = ' '.join(
                [Path(photo['path']).stem, *tags, *[s['tag'] for s in facts.get('scenes', [])]]
            ).casefold()
            lexical = sum(term in haystack for term in terms) / max(1, len(terms))
            similarity = 0
            signature_matches = models is None or facts.get('analysis_signature', '').endswith(
                ':' + models.signature
            )
            if vector is not None and facts.get('vector') and signature_matches:
                try:
                    candidate = normalized(facts['vector'])
                    if candidate.shape == vector.shape:
                        similarity = float(candidate @ vector)
                except ValueError:
                    pass
            if (terms or query['semantic_text']) and not lexical and similarity < 0.2:
                continue
            if lexical:
                evidence.append('文件名 / 标注 / 场景候选')
            if similarity >= 0.2:
                evidence.append('语义向量')
            score = 0.65 * max(0, similarity) + 0.3 * lexical + (0.05 if not flags else 0)
            results.append(
                dict(id=photo['id'], score=round(score, 5), evidence=evidence or ['工程照片'])
            )
        if len(results) > query['limit'] * 4:
            results = sorted(results, key=lambda r: (-r['score'], r['id']))[: query['limit']]
    return dict(
        query=query,
        photos=sorted(results, key=lambda r: (-r['score'], r['id']))[: query['limit']],
        semantic=vector is not None,
        count_policy='unknown counts are labelled; strict_people excludes them',
    )
