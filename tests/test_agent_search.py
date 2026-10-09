from datetime import date

import pytest

from ccraw.photo_agent.store import Project
from ccraw.photo_agent.search import parse_query, validate_query, search


def test_relative_chinese_query_compiles_to_bounded_filters():
    query = parse_query('去年夏天海边的日落，画面中有两个人', today=date(2026, 10, 9))
    assert query['from_date'] == '2025-06-01'
    assert query['to_date'] == '2025-09-01'
    assert query['person_count'] == 2
    assert 'beach' in query['semantic_text'] and 'sunset' in query['semantic_text']


@pytest.mark.parametrize(
    'value',
    [
        {'sql': 'DROP TABLE photos'},
        {'limit': 10001},
        {'person_count': -1},
        {'from_date': 'yesterday'},
        {'quality': 'perfect'},
        {'strict_people': 'false'},
    ],
)
def test_query_rejects_unknown_fields_and_invalid_values(value):
    with pytest.raises(ValueError):
        validate_query(value)


def test_constraints_are_intersected_and_unknown_count_is_not_verified(tmp_path):
    project = Project.create(tmp_path / 'album.ccrawagent', '相册')
    paths = [tmp_path / f'{name}.jpg' for name in ('海边日落', 'other')]
    for path in paths:
        path.write_bytes(b'image')
    ids = project.import_paths(paths)
    project.update_photo(
        ids[0],
        facts=dict(
            captured_at='2025-07-01T18:00:00', flags=[], person_count=None, scenes=[{'tag': '海边'}]
        ),
    )
    project.update_photo(
        ids[1], facts=dict(captured_at='2024-07-01T18:00:00', flags=[], person_count=2)
    )
    query = dict(terms='海边', from_date='2025-06-01', to_date='2025-09-01', person_count=2)
    result = search(project, query)
    assert [r['id'] for r in result['photos']] == [ids[0]]
    assert '人数未核验' in result['photos'][0]['evidence']
    assert search(project, dict(query, strict_people=True))['photos'] == []


def test_vector_search_is_real_and_result_limit_is_enforced(tmp_path):
    project = Project.create(tmp_path / 'album.ccrawagent', '相册')
    for i, vector in enumerate(([1, 0], [0, 1], [0.8, 0.2])):
        path = tmp_path / f'{i}.png'
        path.write_bytes(b'image')
        photo_id = project.import_paths([path])[0]
        project.update_photo(photo_id, facts=dict(vector=vector, flags=[]))
    result = search(project, {'semantic_text': 'sunset', 'limit': 2}, query_vector=[1, 0])
    assert len(result['photos']) == 2
    assert result['photos'][0]['score'] > result['photos'][1]['score']
    assert all('语义向量' in r['evidence'] for r in result['photos'])
