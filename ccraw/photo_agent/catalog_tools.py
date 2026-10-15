"""Reversible catalog commands; photo mutations remain selected-only and local."""

import json

NAMES = (
    'query_catalog',
    'set_photo_state',
    'edit_photo_metadata',
    'create_album',
    'album_members',
    'create_virtual_copy',
    'create_photo_stack',
    'catalog_history',
)


def definitions(project, selected, cancelled, ToolSpec):
    from ..catalog.repository import Catalog

    catalog = Catalog(project)
    text = {'type': 'string', 'maxLength': 120}
    ids = dict(type='array', items=text, minItems=1, maxItems=30, uniqueItems=True)

    def params(properties, required=()):
        return dict(
            type='object',
            properties=properties,
            required=list(required),
            additionalProperties=False,
        )

    def guard(photo_ids):
        if cancelled.is_set():
            raise InterruptedError('相册操作已取消。')
        if (
            not isinstance(photo_ids, list)
            or not 1 <= len(photo_ids) <= 30
            or len(set(photo_ids)) != len(photo_ids)
        ):
            raise ValueError('选择 1–30 张不同照片。')
        for ref in photo_ids:
            if ref not in selected:
                raise ValueError('只能修改当前选中的照片。')
            project.photo(ref)
        return photo_ids

    def query(rules=None, album=None, sort='import', descending=False, limit=50, offset=0):
        if cancelled.is_set():
            raise InterruptedError('相册查询已取消。')
        return catalog.query(
            rules=rules, album=album, sort=sort, descending=descending, limit=limit, offset=offset
        )

    def state(photo_ids, rating=None, flag=None, color_label=None):
        values = {
            k: v
            for k, v in dict(rating=rating, flag=flag, color_label=color_label).items()
            if v is not None
        }
        catalog.set_state(guard(photo_ids), **values)
        return {'photos': [dict(id=ref, **catalog.state(ref)) for ref in photo_ids]}

    def metadata(photo_ids, metadata):
        if not isinstance(metadata, dict) or len(json.dumps(metadata)) > 16384:
            raise ValueError('元数据需要有界对象。')
        catalog.edit_metadata(guard(photo_ids), metadata)
        return {'updated': len(photo_ids), 'storage': 'project-only'}

    def create(name, kind='album', parent=None, rules=None):
        if cancelled.is_set():
            raise InterruptedError('已取消。')
        return {
            'id': catalog.create_album(name, kind=kind, parent=parent, rules=rules),
            'name': name,
        }

    def members(album, photo_ids, operation='add'):
        refs = guard(photo_ids)
        if operation not in ('add', 'remove'):
            raise ValueError('相册成员操作无效。')
        (catalog.add_members if operation == 'add' else catalog.remove_members)(album, refs)
        return {'album': album, 'updated': len(refs)}

    def virtual(photo_id, name='虚拟副本'):
        guard([photo_id])
        return {'id': catalog.create_virtual_copy(photo_id, name), 'source_photo_id': photo_id}

    def stack(photo_ids):
        return {'id': catalog.create_stack(guard(photo_ids))}

    def history(operation='undo'):
        if operation not in ('undo', 'redo'):
            raise ValueError('历史操作无效。')
        return {'changed': (catalog.undo if operation == 'undo' else catalog.redo)()}

    return [
        ToolSpec(
            'query_catalog',
            'organize',
            '查询工程相册、评级与筛选条件，返回稳定分页；rules 使用白名单字段和比较操作。',
            params(
                {
                    'rules': {'type': 'object'},
                    'album': text,
                    'sort': dict(
                        type='string', enum=['import', 'capture', 'edit', 'name', 'rating', 'size']
                    ),
                    'descending': {'type': 'boolean'},
                    'limit': dict(type='integer', minimum=1, maximum=200),
                    'offset': dict(type='integer', minimum=0, maximum=10000000),
                }
            ),
            query,
        ),
        ToolSpec(
            'set_photo_state',
            'organize',
            '对选中照片设置评级、保留/拒绝和颜色标签，操作可撤销，不修改原片。',
            params(
                {
                    'photo_ids': ids,
                    'rating': dict(type='integer', minimum=0, maximum=5),
                    'flag': dict(type='string', enum=['none', 'pick', 'reject']),
                    'color_label': dict(
                        type='string', enum=['', 'red', 'yellow', 'green', 'blue', 'purple']
                    ),
                },
                ['photo_ids'],
            ),
            state,
            False,
        ),
        ToolSpec(
            'edit_photo_metadata',
            'organize',
            '更改选中照片的工程元数据；不会写入源照片或 sidecar。',
            params(
                {
                    'photo_ids': ids,
                    'metadata': dict(
                        type='object',
                        properties={
                            k: dict(type='string', maxLength=4000)
                            for k in ('title', 'description', 'copyright', 'creator', 'location')
                        },
                        additionalProperties=False,
                    ),
                },
                ['photo_ids', 'metadata'],
            ),
            metadata,
            False,
        ),
        ToolSpec(
            'create_album',
            'organize',
            '创建普通相册、文件夹或基于白名单 rules 动态匹配的智能相册。',
            params(
                {
                    'name': text,
                    'kind': dict(type='string', enum=['album', 'folder', 'smart']),
                    'parent': text,
                    'rules': {'type': 'object'},
                },
                ['name'],
            ),
            create,
            False,
        ),
        ToolSpec(
            'album_members',
            'organize',
            '将选中照片加入或移出普通相册，可撤销。',
            params(
                {
                    'album': text,
                    'photo_ids': ids,
                    'operation': dict(type='string', enum=['add', 'remove']),
                },
                ['album', 'photo_ids'],
            ),
            members,
            False,
        ),
        ToolSpec(
            'create_virtual_copy',
            'organize',
            '为选中照片建立独立编辑配方与历史，不复制原片字节。',
            params({'photo_id': text, 'name': text}, ['photo_id']),
            virtual,
            False,
        ),
        ToolSpec(
            'create_photo_stack',
            'organize',
            '将选中的照片组成可折叠的照片堆栈。',
            params({'photo_ids': ids}, ['photo_ids']),
            stack,
            False,
        ),
        ToolSpec(
            'catalog_history',
            'organize',
            '撤销或重做上一条相册管理命令，不影响修图历史。',
            params({'operation': dict(type='string', enum=['undo', 'redo'])}, ['operation']),
            history,
            False,
        ),
    ]
