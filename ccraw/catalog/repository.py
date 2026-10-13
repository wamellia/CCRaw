"""SQLite catalog facts, bounded commands and transactional row-level undo/redo.

Original files are never deleted or overwritten by catalog commands. Virtual photo
references share source pixels/facts but retain recipes and management state.
"""

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .rules import compile_rules


def _json(value):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(text.encode('utf8')) > 8_000_000:
        raise ValueError('管理动作记录超过大小限制。')
    return text


def _id():
    return uuid.uuid4().hex


def migrate(db):
    columns = {r[1] for r in db.execute('PRAGMA table_info(photos)')}
    if 'imported_at' not in columns:
        db.execute('ALTER TABLE photos ADD COLUMN imported_at REAL DEFAULT 0')
    db.executescript("""
        CREATE TABLE IF NOT EXISTS photo_state(photo_ref TEXT PRIMARY KEY,rating INTEGER NOT NULL DEFAULT 0 CHECK(rating BETWEEN 0 AND 5),flag TEXT NOT NULL DEFAULT 'none',color_label TEXT NOT NULL DEFAULT '',removed_at REAL,edited_at REAL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS photo_state_rating ON photo_state(rating,flag,removed_at);
        CREATE TABLE IF NOT EXISTS albums(id TEXT PRIMARY KEY,name TEXT,parent TEXT,kind TEXT,rules TEXT DEFAULT '{}',cover TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS album_members(album_id TEXT,photo_ref TEXT,position INTEGER,PRIMARY KEY(album_id,photo_ref));
        CREATE INDEX IF NOT EXISTS members_photo ON album_members(photo_ref);
        CREATE TABLE IF NOT EXISTS stacks(id TEXT PRIMARY KEY,cover TEXT,collapsed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS stack_members(stack_id TEXT,photo_ref TEXT UNIQUE,position INTEGER,PRIMARY KEY(stack_id,photo_ref));
        CREATE TABLE IF NOT EXISTS virtual_copies(id TEXT PRIMARY KEY,source_photo_id TEXT REFERENCES photos(id),name TEXT,recipe TEXT,revision INTEGER DEFAULT 0,created REAL);
        CREATE INDEX IF NOT EXISTS copies_source ON virtual_copies(source_photo_id);
        CREATE TABLE IF NOT EXISTS copy_history(id TEXT PRIMARY KEY,copy_id TEXT,revision INTEGER,recipe TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS keywords(name TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS photo_keywords(photo_ref TEXT,keyword TEXT,PRIMARY KEY(photo_ref,keyword));
        CREATE INDEX IF NOT EXISTS keyword_lookup ON photo_keywords(keyword,photo_ref);
        CREATE TABLE IF NOT EXISTS metadata_overrides(photo_ref TEXT,field TEXT,value TEXT,PRIMARY KEY(photo_ref,field));
        CREATE TABLE IF NOT EXISTS filter_presets(id TEXT PRIMARY KEY,name TEXT,rules TEXT,sort TEXT,descending INTEGER);
        CREATE TABLE IF NOT EXISTS suggestion_decisions(id TEXT PRIMARY KEY,kind TEXT,photos TEXT,decision TEXT,analysis_version TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS cluster_corrections(id TEXT PRIMARY KEY,operation TEXT,payload TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS person_cluster_members(face_ref TEXT PRIMARY KEY,cluster_id TEXT);
        CREATE TABLE IF NOT EXISTS proxies(photo_ref TEXT PRIMARY KEY,path TEXT,width INTEGER,height INTEGER,proxy_width INTEGER,proxy_height INTEGER,source_sha TEXT,source_size INTEGER,source_mtime INTEGER,color_space TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS catalog_commands(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,actions TEXT,undone INTEGER DEFAULT 0,created REAL);
        DROP VIEW IF EXISTS catalog_photos;
        CREATE VIEW catalog_photos AS
          SELECT p.id,p.id AS source_photo_id,p.path,p.size,p.mtime,p.status,p.sha,p.facts,p.imported_at,'' AS copy_name,0 AS is_copy,coalesce(s.rating,0) AS rating,coalesce(s.flag,'none') AS flag,coalesce(s.color_label,'') AS color_label,s.removed_at,coalesce(s.edited_at,0) AS edited_at FROM photos p LEFT JOIN photo_state s ON p.id=s.photo_ref
          UNION ALL
          SELECT c.id,p.id,p.path,p.size,p.mtime,p.status,p.sha,p.facts,c.created,c.name,1,coalesce(s.rating,0),coalesce(s.flag,'none'),coalesce(s.color_label,''),s.removed_at,coalesce(s.edited_at,0) FROM virtual_copies c JOIN photos p ON c.source_photo_id=p.id LEFT JOIN photo_state s ON c.id=s.photo_ref;
    """)
    # Both physical and virtual refs are validated by Project.photo. Old FKs only
    # permit physical IDs and must be removed while preserving every old record.
    for table in ('proposals', 'derivatives'):
        if db.execute(f'PRAGMA foreign_key_list({table})').fetchall():
            db.execute(f'ALTER TABLE {table} RENAME TO {table}_physical_v1')
            if table == 'proposals':
                db.execute(
                    "CREATE TABLE proposals(id TEXT PRIMARY KEY,photo_id TEXT,kind TEXT,payload TEXT,status TEXT DEFAULT 'pending',created REAL)"
                )
            else:
                db.execute(
                    'CREATE TABLE derivatives(id TEXT PRIMARY KEY,photo_id TEXT,path TEXT,provenance TEXT,created REAL)'
                )
            db.execute(f'INSERT INTO {table} SELECT * FROM {table}_physical_v1')
            db.execute(f'DROP TABLE {table}_physical_v1')
    db.execute('PRAGMA user_version=2')


_KEYS = {
    'photo_state': ('photo_ref',),
    'albums': ('id',),
    'album_members': ('album_id', 'photo_ref'),
    'stacks': ('id',),
    'stack_members': ('stack_id', 'photo_ref'),
    'virtual_copies': ('id',),
    'copy_history': ('id',),
    'keywords': ('name',),
    'photo_keywords': ('photo_ref', 'keyword'),
    'metadata_overrides': ('photo_ref', 'field'),
    'filter_presets': ('id',),
    'suggestion_decisions': ('id',),
    'cluster_corrections': ('id',),
    'proxies': ('photo_ref',),
    'photos': ('id',),
    'preferences': ('key',),
}
_SORTS = {
    'capture': "json_extract(v.facts,'$.captured_at')",
    'import': 'v.imported_at',
    'edit': 'v.edited_at',
    'name': 'lower(catalog_name(v.path))',
    'rating': 'v.rating',
    'size': 'v.size',
}
_METADATA = {'title', 'description', 'author', 'copyright', 'location', 'person_count'}


class _Transaction:
    def __init__(self, db):
        self.db, self.actions = db, []

    def put(self, table, row):
        if table not in _KEYS:
            raise ValueError('unsupported catalog table')
        keys = _KEYS[table]
        key = {k: row[k] for k in keys}
        where = ' AND '.join(k + '=?' for k in keys)
        old = self.db.execute(
            f'SELECT * FROM {table} WHERE {where}', tuple(key.values())
        ).fetchone()
        new = dict(old) if old else {}
        new.update(row)
        columns = list(new)
        self.db.execute(
            f'INSERT OR REPLACE INTO {table}({",".join(columns)}) VALUES({",".join("?" for _ in columns)})',
            list(new.values()),
        )
        new = dict(
            self.db.execute(f'SELECT * FROM {table} WHERE {where}', tuple(key.values())).fetchone()
        )
        self.actions.append(
            {
                'table': table,
                'key': key,
                'before': dict(old) if old else None,
                'after': new,
                'columns': list(row),
            }
        )

    def delete(self, table, key):
        keys = _KEYS[table]
        where = ' AND '.join(k + '=?' for k in keys)
        old = self.db.execute(
            f'SELECT * FROM {table} WHERE {where}', tuple(key[k] for k in keys)
        ).fetchone()
        if old:
            self.db.execute(f'DELETE FROM {table} WHERE {where}', tuple(key[k] for k in keys))
            self.actions.append({'table': table, 'key': key, 'before': dict(old), 'after': None})


class Catalog:
    def __init__(self, project):
        self.project = project

    @contextmanager
    def command(self, kind):
        with self.project.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            tx = _Transaction(db)
            yield tx
            if tx.actions:
                payload = _json(tx.actions)
                db.execute('DELETE FROM catalog_commands WHERE undone=1')
                db.execute(
                    'INSERT INTO catalog_commands(kind,actions,created) VALUES(?,?,?)',
                    (kind, payload, time.time()),
                )
                db.execute(
                    'INSERT INTO events(kind,payload,created) VALUES(?,?,?)',
                    ('catalog.' + kind, _json({'affected': len(tx.actions)}), time.time()),
                )

    def _refs(self, ids, *, limit=500):
        ids = list(ids)
        if any(not isinstance(ref, str) for ref in ids):
            raise ValueError('照片引用无效。')
        ids = list(dict.fromkeys(ids))
        if not 1 <= len(ids) <= limit:
            raise ValueError(f'请选择 1–{limit} 张照片。')
        with self.project.connect() as db:
            for offset in range(0, len(ids), 500):
                batch = ids[offset : offset + 500]
                found = db.execute(
                    'SELECT count(*) FROM catalog_photos WHERE id IN('
                    + ','.join('?' for _ in batch)
                    + ')',
                    batch,
                ).fetchone()[0]
                if found != len(batch):
                    raise ValueError('照片不属于当前工程。')
        return ids

    def _replay(self, redo):
        with self.project.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(
                'SELECT * FROM catalog_commands WHERE undone=? ORDER BY seq '
                + ('ASC' if redo else 'DESC')
                + ' LIMIT 1',
                (int(redo),),
            ).fetchone()
            if not row:
                return False
            actions = json.loads(row['actions'])
            for action in actions if redo else reversed(actions):
                table, key = action['table'], action['key']
                if table not in _KEYS or set(key) != set(_KEYS[table]):
                    raise ValueError('撤销记录损坏。')
                value = action['after' if redo else 'before']
                where = ' AND '.join(k + '=?' for k in key)
                if (
                    table == 'photo_state'
                    and action['before'] is None
                    and action['after'] is not None
                ):
                    current = db.execute(
                        f'SELECT * FROM {table} WHERE {where}', list(key.values())
                    ).fetchone()
                    changed = set(action.get('columns', [])) - set(key)
                    if current and any(
                        current[c] != action['after'][c]
                        for c in current.keys()
                        if c not in changed and c not in key
                    ):
                        defaults = dict(
                            rating=0, flag='none', color_label='', removed_at=None, edited_at=0
                        )
                        updates = {
                            c: (action['after'][c] if redo else defaults[c]) for c in changed
                        }
                        if updates:
                            db.execute(
                                'UPDATE photo_state SET '
                                + ','.join(c + '=?' for c in updates)
                                + ' WHERE '
                                + where,
                                [*updates.values(), *key.values()],
                            )
                        continue
                if value is None:
                    db.execute(f'DELETE FROM {table} WHERE {where}', list(key.values()))
                elif action['before'] is not None and action['after'] is not None:
                    columns = [c for c in action.get('columns', list(value)) if c not in key]
                    if not set(columns).issubset(
                        {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                    ):
                        raise ValueError('撤销记录字段损坏。')
                    if columns:
                        db.execute(
                            f'UPDATE {table} SET '
                            + ','.join(c + '=?' for c in columns)
                            + ' WHERE '
                            + where,
                            [*[value[c] for c in columns], *key.values()],
                        )
                else:
                    columns = list(value)
                    if not set(columns).issubset(
                        {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                    ):
                        raise ValueError('撤销记录字段损坏。')
                    db.execute(
                        f'INSERT OR REPLACE INTO {table}({",".join(columns)}) VALUES({",".join("?" for _ in columns)})',
                        list(value.values()),
                    )
            db.execute(
                'UPDATE catalog_commands SET undone=? WHERE seq=?', (int(not redo), row['seq'])
            )
            db.execute(
                'INSERT INTO events(kind,payload,created) VALUES(?,?,?)',
                (
                    'catalog.redo' if redo else 'catalog.undo',
                    _json({'command': row['seq']}),
                    time.time(),
                ),
            )
        return True

    def undo(self):
        return self._replay(False)

    def redo(self):
        return self._replay(True)

    def state(self, ref):
        self.project.photo(ref)
        with self.project.connect() as db:
            row = db.execute('SELECT * FROM photo_state WHERE photo_ref=?', (ref,)).fetchone()
        return (
            dict(row)
            if row
            else dict(
                photo_ref=ref, rating=0, flag='none', color_label='', removed_at=None, edited_at=0
            )
        )

    def set_state(self, ids, **values):
        ids = self._refs(ids)
        if not values or set(values) - {'rating', 'flag', 'color_label', 'removed'}:
            raise ValueError('选片状态字段无效。')
        if 'rating' in values and (
            type(values['rating']) is not int or not 0 <= values['rating'] <= 5
        ):
            raise ValueError('评级必须为 0–5。')
        if values.get('flag', 'none') not in ('none', 'pick', 'reject'):
            raise ValueError('flag 必须为 none / pick / reject。')
        if values.get('color_label', '') not in ('', 'red', 'yellow', 'green', 'blue', 'purple'):
            raise ValueError('颜色标签无效。')
        if 'removed' in values:
            if type(values['removed']) is not bool:
                raise ValueError('移除状态必须为布尔值。')
            values['removed_at'] = time.time() if values.pop('removed') else None
        with self.command('state') as tx:
            for ref in ids:
                tx.put('photo_state', dict(photo_ref=ref, **values))

    def albums(self):
        with self.project.connect() as db:
            return [
                dict(r, rules=json.loads(r['rules']))
                for r in db.execute('SELECT * FROM albums ORDER BY created,id')
            ]

    def _album_where(self, db, album, visiting=()):
        if album in visiting or len(visiting) > 8:
            raise ValueError('智能相册引用形成循环或超过 8 层。')
        row = db.execute('SELECT * FROM albums WHERE id=?', (album,)).fetchone()
        if not row:
            raise ValueError('相册不存在。')
        if row['kind'] == 'folder':
            return '0', []
        if row['kind'] == 'smart':
            return compile_rules(
                json.loads(row['rules']),
                lambda other: self._album_where(db, other, (*visiting, album)),
            )
        return 'v.id IN(SELECT photo_ref FROM album_members WHERE album_id=?)', [album]

    def _validate_parent(self, db, album, parent):
        visited = {album}
        while parent:
            if parent in visited:
                raise ValueError('相册父级形成循环。')
            visited.add(parent)
            row = db.execute('SELECT parent,kind FROM albums WHERE id=?', (parent,)).fetchone()
            if not row or row['kind'] != 'folder':
                raise ValueError('相册父级必须为现有文件夹。')
            parent = row['parent']

    def create_album(self, name, *, kind='album', parent=None, rules=None):
        album = _id()
        if (
            kind not in ('album', 'folder', 'smart')
            or not isinstance(name, str)
            or not 0 < len(name.strip()) <= 120
        ):
            raise ValueError('相册名称或类型无效。')
        rules = rules or {'all': []}
        with self.command('album.create') as tx:
            self._validate_parent(tx.db, album, parent)
            tx.put(
                'albums',
                dict(
                    id=album,
                    name=name.strip(),
                    parent=parent,
                    kind=kind,
                    rules=_json(rules),
                    cover=None,
                    created=time.time(),
                ),
            )
            self._album_where(tx.db, album)
        return album

    def update_album(self, album, **changes):
        if set(changes) - {'name', 'parent', 'rules', 'cover'}:
            raise ValueError('相册字段无效。')
        if 'name' in changes and (
            not isinstance(changes['name'], str) or not 0 < len(changes['name'].strip()) <= 120
        ):
            raise ValueError('相册名称无效。')
        with self.command('album.update') as tx:
            if not tx.db.execute('SELECT 1 FROM albums WHERE id=?', (album,)).fetchone():
                raise ValueError('相册不存在。')
            if 'parent' in changes:
                self._validate_parent(tx.db, album, changes['parent'])
            if 'rules' in changes:
                changes['rules'] = _json(changes['rules'])
            if changes.get('cover') is not None:
                clause, params = self._album_where(tx.db, album)
                if not tx.db.execute(
                    'SELECT 1 FROM catalog_photos v WHERE v.id=? AND (' + clause + ')',
                    [changes['cover'], *params],
                ).fetchone():
                    raise ValueError('相册封面需要是相册成员。')
            tx.put('albums', dict(id=album, **changes))
            for row in tx.db.execute("SELECT id FROM albums WHERE kind='smart'").fetchall():
                self._album_where(tx.db, row['id'])

    def add_members(self, album, ids):
        ids = self._refs(ids)
        with self.command('album.members') as tx:
            row = tx.db.execute('SELECT kind FROM albums WHERE id=?', (album,)).fetchone()
            if not row or row['kind'] != 'album':
                raise ValueError('只有普通相册可添加成员。')
            position = (
                tx.db.execute(
                    'SELECT coalesce(max(position),-1) FROM album_members WHERE album_id=?',
                    (album,),
                ).fetchone()[0]
                + 1
            )
            for i, ref in enumerate(ids):
                if not tx.db.execute(
                    'SELECT 1 FROM album_members WHERE album_id=? AND photo_ref=?', (album, ref)
                ).fetchone():
                    tx.put(
                        'album_members', dict(album_id=album, photo_ref=ref, position=position + i)
                    )

    def remove_members(self, album, ids):
        with self.command('album.members.remove') as tx:
            for ref in self._refs(ids):
                tx.delete('album_members', dict(album_id=album, photo_ref=ref))

    def reorder_album(self, album, ids):
        if not isinstance(ids, list) or len(ids) > 10000 or len(set(ids)) != len(ids):
            raise ValueError('相册顺序需要最多 10000 个不同引用。')
        with self.command('album.reorder') as tx:
            row = tx.db.execute('SELECT kind FROM albums WHERE id=?', (album,)).fetchone()
            members = {
                r[0]
                for r in tx.db.execute(
                    'SELECT photo_ref FROM album_members WHERE album_id=?', (album,)
                )
            }
            if not row or row['kind'] != 'album' or set(ids) != members:
                raise ValueError('自定义顺序必须包含普通相册的每个成员一次。')
            for position, ref in enumerate(ids):
                tx.put('album_members', dict(album_id=album, photo_ref=ref, position=position))

    def query(
        self,
        *,
        rules=None,
        album=None,
        preset=None,
        sort='import',
        descending=False,
        limit=200,
        offset=0,
        trash=False,
        include_collapsed=False,
        photo_refs=None,
    ):
        if (
            sort not in _SORTS
            or type(limit) is not int
            or not 1 <= limit <= 500
            or type(offset) is not int
            or offset < 0
        ):
            raise ValueError('排序或分页无效。')
        with self.project.connect() as db:
            if preset:
                row = db.execute('SELECT * FROM filter_presets WHERE id=?', (preset,)).fetchone()
                if not row:
                    raise ValueError('筛选预设不存在。')
                rules, sort, descending = (
                    json.loads(row['rules']),
                    row['sort'],
                    bool(row['descending']),
                )
            sql, params = compile_rules(rules or {'all': []}, lambda a: self._album_where(db, a))
            clauses = [sql, 'v.removed_at IS NOT NULL' if trash else 'v.removed_at IS NULL']
            if photo_refs is not None:
                if (
                    not isinstance(photo_refs, list)
                    or len(photo_refs) > 100000
                    or any(not isinstance(ref, str) or len(ref) != 32 for ref in photo_refs)
                ):
                    raise ValueError('候选照片引用无效。')
                db.execute('CREATE TEMP TABLE selected_refs(id TEXT PRIMARY KEY)')
                db.executemany(
                    'INSERT OR IGNORE INTO selected_refs VALUES(?)', [(ref,) for ref in photo_refs]
                )
                clauses.append('EXISTS(SELECT 1 FROM selected_refs WHERE id=v.id)')
            if album:
                expression, values = self._album_where(db, album)
                clauses.append(expression)
                params.extend(values)
            if not include_collapsed:
                clauses.append(
                    'NOT EXISTS(SELECT 1 FROM stack_members sm JOIN stacks st ON sm.stack_id=st.id WHERE sm.photo_ref=v.id AND st.collapsed=1 AND st.cover!=v.id)'
                )
            where = ' WHERE ' + ' AND '.join('(' + v + ')' for v in clauses)
            total = db.execute('SELECT count(*) FROM catalog_photos v' + where, params).fetchone()[
                0
            ]
            order = _SORTS[sort] + (' DESC' if descending else ' ASC') + ',v.id ASC'
            if (
                album
                and sort == 'import'
                and db.execute('SELECT kind FROM albums WHERE id=?', (album,)).fetchone()[0]
                == 'album'
            ):
                order = (
                    '(SELECT position FROM album_members WHERE album_id=? AND photo_ref=v.id),v.id'
                )
                order_params = [album]
            else:
                order_params = []
            rows = db.execute(
                'SELECT v.* FROM catalog_photos v'
                + where
                + ' ORDER BY '
                + order
                + ' LIMIT ? OFFSET ?',
                [*params, *order_params, limit, offset],
            ).fetchall()
        return {
            'photos': [dict(r, facts=json.loads(r['facts'])) for r in rows],
            'total': total,
            'offset': offset,
            'limit': limit,
        }

    def save_filter(self, name, rules, *, sort='import', descending=False):
        if sort not in _SORTS or not isinstance(name, str) or not 0 < len(name.strip()) <= 120:
            raise ValueError('筛选预设无效。')
        with self.project.connect() as db:
            compile_rules(rules, lambda a: self._album_where(db, a))
        ref = _id()
        with self.command('filter.save') as tx:
            tx.put(
                'filter_presets',
                dict(
                    id=ref,
                    name=name.strip(),
                    rules=_json(rules),
                    sort=sort,
                    descending=int(descending),
                ),
            )
        return ref

    def filter_presets(self):
        with self.project.connect() as db:
            return [
                dict(r, rules=json.loads(r['rules']))
                for r in db.execute('SELECT * FROM filter_presets ORDER BY name,id')
            ]

    def metadata(self, ref):
        photo = self.project.photo(ref)
        return self.metadata_batch([photo])[ref]

    def metadata_batch(self, photos):
        if len(photos) > 500:
            raise ValueError('元数据每批最多 500 张。')
        result = {
            p['id']: {
                **{field: '' for field in _METADATA},
                'person_count': p['facts'].get('person_count'),
                'keywords': [],
                'overridden': [],
                'exif': p['facts'],
            }
            for p in photos
        }
        if not result:
            return result
        marks = ','.join('?' for _ in result)
        with self.project.connect() as db:
            for row in db.execute(
                'SELECT key,value FROM preferences WHERE key IN(' + marks + ')',
                ['annotation:' + ref for ref in result],
            ):
                annotation = json.loads(row['value'])
                target = result[row['key'][11:]]
                target['person_count'] = annotation.get('person_count', target['person_count'])
                target['keywords'] = annotation.get('tags', [])
            for row in db.execute(
                'SELECT * FROM metadata_overrides WHERE photo_ref IN(' + marks + ')', list(result)
            ):
                target = result[row['photo_ref']]
                target['overridden'].append(row['field'])
                if row['field'] == '_keywords':
                    target['keywords'] = []
                else:
                    target[row['field']] = json.loads(row['value'])
            for row in db.execute(
                'SELECT photo_ref,keyword FROM photo_keywords WHERE photo_ref IN('
                + marks
                + ') ORDER BY keyword',
                list(result),
            ):
                target = result[row['photo_ref']]
                if row['keyword'] not in target['keywords']:
                    target['keywords'].append(row['keyword'])
        return result

    def metadata_values(self, ids):
        values = [self.metadata(ref) for ref in self._refs(ids)]
        return {
            field: {
                'value': values[0][field],
                'mixed': any(v[field] != values[0][field] for v in values[1:]),
            }
            for field in (*sorted(_METADATA), 'keywords')
        }

    def edit_metadata(self, ids, values):
        ids = self._refs(ids)
        if not values or set(values) - _METADATA:
            raise ValueError('元数据字段无效。')
        for key, value in values.items():
            if key == 'person_count':
                if value is not None and (type(value) is not int or not 0 <= value <= 100):
                    raise ValueError('人数需要 0–100 或未知。')
            elif not isinstance(value, str) or len(value) > 8000:
                raise ValueError('元数据文本最多 8000 字。')
        with self.command('metadata') as tx:
            for ref in ids:
                for field, value in values.items():
                    tx.put(
                        'metadata_overrides', dict(photo_ref=ref, field=field, value=_json(value))
                    )

    def edit_keywords(self, ids, values, *, mode='append'):
        ids = self._refs(ids)
        if (
            mode not in ('append', 'remove', 'replace')
            or not isinstance(values, list)
            or len(values) > 100
            or any(not isinstance(v, str) or not 0 < len(v.strip()) <= 200 for v in values)
        ):
            raise ValueError('关键词需要最多 100 个值和 append/remove/replace 模式。')
        values = sorted(set(v.strip() for v in values))
        with self.command('keywords') as tx:
            for ref in ids:
                # Explicit empty keywords remain distinct from unmigrated legacy tags.
                existing = self.metadata(ref)['keywords']
                tx.put('metadata_overrides', dict(photo_ref=ref, field='_keywords', value='true'))
                if mode != 'replace':
                    for keyword in existing:
                        tx.put('photo_keywords', dict(photo_ref=ref, keyword=keyword))
                if mode == 'replace':
                    for row in tx.db.execute(
                        'SELECT * FROM photo_keywords WHERE photo_ref=?', (ref,)
                    ).fetchall():
                        tx.delete('photo_keywords', dict(photo_ref=ref, keyword=row['keyword']))
                for value in values:
                    if mode == 'remove':
                        tx.delete('photo_keywords', dict(photo_ref=ref, keyword=value))
                    else:
                        tx.put('keywords', dict(name=value))
                        tx.put('photo_keywords', dict(photo_ref=ref, keyword=value))

    def create_stack(self, ids):
        ids = self._refs(ids)
        if len(ids) < 2:
            raise ValueError('堆叠需要至少两张照片。')
        ref = _id()
        with self.command('stack.create') as tx:
            for photo in ids:
                if tx.db.execute(
                    'SELECT 1 FROM stack_members WHERE photo_ref=?', (photo,)
                ).fetchone():
                    raise ValueError('照片已在另一个堆叠中。')
            tx.put('stacks', dict(id=ref, cover=ids[0], collapsed=0))
            for i, photo in enumerate(ids):
                tx.put('stack_members', dict(stack_id=ref, photo_ref=photo, position=i))
        return ref

    def stacks(self):
        with self.project.connect() as db:
            return [
                dict(
                    r,
                    photos=[
                        m[0]
                        for m in db.execute(
                            'SELECT photo_ref FROM stack_members WHERE stack_id=? ORDER BY position',
                            (r['id'],),
                        )
                    ],
                )
                for r in db.execute('SELECT * FROM stacks')
            ]

    def update_stack(self, ref, *, cover=None, collapsed=None):
        with self.command('stack.update') as tx:
            if not tx.db.execute('SELECT 1 FROM stacks WHERE id=?', (ref,)).fetchone():
                raise ValueError('堆叠不存在。')
            row = {'id': ref}
            if cover is not None:
                if not tx.db.execute(
                    'SELECT 1 FROM stack_members WHERE stack_id=? AND photo_ref=?', (ref, cover)
                ).fetchone():
                    raise ValueError('封面需要是堆叠成员。')
                row['cover'] = cover
            if collapsed is not None:
                row['collapsed'] = int(bool(collapsed))
            tx.put('stacks', row)

    def dissolve_stack(self, ref):
        with self.command('stack.dissolve') as tx:
            for row in tx.db.execute(
                'SELECT * FROM stack_members WHERE stack_id=?', (ref,)
            ).fetchall():
                tx.delete('stack_members', dict(stack_id=ref, photo_ref=row['photo_ref']))
            tx.delete('stacks', dict(id=ref))

    def auto_stack(self, *, seconds=2):
        if not isinstance(seconds, (float, int)) or not 0 <= seconds <= 3600:
            raise ValueError('自动堆叠时间间隔需要 0–3600 秒。')
        with self.project.connect() as db:
            rows = db.execute(
                "SELECT v.id,json_extract(v.facts,'$.captured_at') AS captured FROM catalog_photos v WHERE is_copy=0 AND removed_at IS NULL AND json_extract(v.facts,'$.captured_at') IS NOT NULL AND NOT EXISTS(SELECT 1 FROM stack_members sm WHERE sm.photo_ref=v.id) ORDER BY captured,v.id"
            ).fetchall()
        groups, current, previous = [], [], None
        for row in rows:
            try:
                when = datetime.fromisoformat(row['captured']).timestamp()
            except (TypeError, ValueError):
                continue
            if previous is not None and when - previous > seconds:
                if len(current) > 1:
                    groups.append(current)
                current = []
            current.append(row['id'])
            previous = when
        if len(current) > 1:
            groups.append(current)
        result = []
        with self.command('stack.auto') as tx:
            for group in groups:
                if len(group) > 500:
                    continue
                ref = _id()
                tx.put('stacks', dict(id=ref, cover=group[0], collapsed=0))
                for i, photo in enumerate(group):
                    tx.put('stack_members', dict(stack_id=ref, photo_ref=photo, position=i))
                result.append(ref)
        return result

    def create_virtual_copy(self, source, name='虚拟副本'):
        from .. import model

        photo = self.project.photo(source)
        original = photo.get('source_photo_id', source)
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 120:
            raise ValueError('副本名称无效。')
        recipe = self.project.preferences().get('recipe:' + source, model.recipe())
        ref = _id()
        with self.command('copy.create') as tx:
            tx.put(
                'virtual_copies',
                dict(
                    id=ref,
                    source_photo_id=original,
                    name=name.strip(),
                    recipe=_json(recipe),
                    revision=0,
                    created=time.time(),
                ),
            )
            tx.put('preferences', dict(key='recipe:' + ref, value=_json(recipe)))
            snapshots = self.project.preferences().get('snapshots:' + source, [])
            if snapshots:
                tx.put('preferences', dict(key='snapshots:' + ref, value=_json(snapshots)))
        return ref

    def save_copy_recipe(self, ref, recipe):
        from ..model import validate

        recipe = validate(recipe)
        with self.command('copy.recipe') as tx:
            row = tx.db.execute('SELECT * FROM virtual_copies WHERE id=?', (ref,)).fetchone()
            if not row:
                raise ValueError('虚拟副本不存在。')
            revision = row['revision'] + 1
            tx.put('virtual_copies', dict(id=ref, recipe=_json(recipe), revision=revision))
            tx.put(
                'copy_history',
                dict(
                    id=_id(),
                    copy_id=ref,
                    revision=revision,
                    recipe=_json(recipe),
                    created=time.time(),
                ),
            )
            tx.put('preferences', dict(key='recipe:' + ref, value=_json(recipe)))
            tx.put('photo_state', dict(photo_ref=ref, edited_at=time.time()))

    def copy_history(self, ref):
        with self.project.connect() as db:
            return [
                dict(r, recipe=json.loads(r['recipe']))
                for r in db.execute(
                    'SELECT * FROM copy_history WHERE copy_id=? ORDER BY revision', (ref,)
                )
            ]

    @staticmethod
    def suggestion_id(kind, group):
        photos = sorted(group.get('photos', [group.get('photo_id')]))
        return hashlib.sha256(_json([kind, photos, group.get('start')]).encode()).hexdigest()

    def decide_suggestion(self, kind, group, decision, *, analysis_version='photo-analysis-v1'):
        if decision not in ('keep', 'ignore', 'resolved') or kind not in (
            'duplicates',
            'similar',
            'quality',
            'events',
            'people',
        ):
            raise ValueError('整理建议决定无效。')
        refs = self._refs(group.get('photos', [group.get('photo_id')]), limit=100000)
        with self.command('suggestion.decide') as tx:
            tx.put(
                'suggestion_decisions',
                dict(
                    id=self.suggestion_id(kind, group),
                    kind=kind,
                    photos=_json(sorted(refs)),
                    decision=decision,
                    analysis_version=analysis_version,
                    created=time.time(),
                ),
            )

    def suggestion_decision(self, kind, group):
        with self.project.connect() as db:
            row = db.execute(
                'SELECT * FROM suggestion_decisions WHERE id=?', (self.suggestion_id(kind, group),)
            ).fetchone()
        return dict(row) if row else None

    def correct_cluster(self, operation, clusters, *, photos=None, faces=None):
        if (
            operation not in ('merge', 'split', 'remove')
            or not isinstance(clusters, list)
            or not 1 <= len(clusters) <= 100
            or any(not isinstance(c, str) or len(c) > 100 for c in clusters)
        ):
            raise ValueError('人物簇校正无效。')
        if operation == 'merge' and len(clusters) < 2:
            raise ValueError('合并需要至少两个人物簇。')
        refs = self._refs(photos) if photos else []
        face_ids = list(faces or [])
        if len(face_ids) > 500 or any(not isinstance(f, str) or len(f) > 100 for f in face_ids):
            raise ValueError('人物脸部引用无效。')
        with self.command('cluster.correct') as tx:
            tx.put(
                'cluster_corrections',
                dict(
                    id=_id(),
                    operation=operation,
                    payload=_json({'clusters': clusters, 'photos': refs, 'faces': face_ids}),
                    created=time.time(),
                ),
            )

    def stabilize_clusters(self, groups, cancel=None):
        """Keep inferred cluster IDs by overlap with previously analyzed face refs."""
        from collections import Counter

        result = []
        used = set()
        with self.project.connect() as db:
            for group in groups:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError('人物簇身份更新已取消。')
                faces = group.get('faces', [])
                votes = Counter()
                for offset in range(0, len(faces), 500):
                    batch = faces[offset : offset + 500]
                    for row in db.execute(
                        'SELECT cluster_id FROM person_cluster_members WHERE face_ref IN('
                        + ','.join('?' for _ in batch)
                        + ')',
                        batch,
                    ):
                        if row[0] not in used:
                            votes[row[0]] += 1
                identity = min(votes, key=lambda ref: (-votes[ref], ref)) if votes else group['id']
                if identity in used:
                    identity = 'person-' + _id()[:12]
                used.add(identity)
                db.executemany(
                    'INSERT OR REPLACE INTO person_cluster_members VALUES(?,?)',
                    [(face, identity) for face in faces],
                )
                result.append(dict(group, id=identity))
        return result

    def apply_cluster_corrections(self, groups):
        result = {
            g['id']: dict(g, photos=list(g['photos']), faces=list(g.get('faces', [])))
            for g in groups
        }
        with self.project.connect() as db:
            corrections = db.execute(
                'SELECT * FROM cluster_corrections ORDER BY created,id'
            ).fetchall()
        for row in corrections:
            data = json.loads(row['payload'])
            existing = [result[c] for c in data['clusters'] if c in result]
            if not existing:
                continue
            if row['operation'] == 'merge':
                head = existing[0]
                for group in existing[1:]:
                    head['photos'] = sorted(set(head['photos']) | set(group['photos']))
                    head['faces'] = sorted(set(head['faces']) | set(group['faces']))
                    result.pop(group['id'], None)
            else:
                for group in existing:
                    moved_faces = [
                        f
                        for f in group['faces']
                        if f in data['faces'] or f.split(':')[0] in data['photos']
                    ]
                    moved = [
                        p
                        for p in group['photos']
                        if p in data['photos'] or any(f.split(':')[0] == p for f in moved_faces)
                    ]
                    group['faces'] = [f for f in group['faces'] if f not in moved_faces]
                    group['photos'] = [
                        p
                        for p in group['photos']
                        if p not in data['photos']
                        and (p not in moved or any(f.split(':')[0] == p for f in group['faces']))
                    ]
                    if row['operation'] == 'split' and moved:
                        new = dict(
                            id='person-' + row['id'][:12],
                            label='人工拆分人物簇',
                            photos=moved,
                            faces=moved_faces,
                        )
                        result[new['id']] = new
        return [g for g in result.values() if g['photos']]

    def relink(self, ref, path, *, confirm_name_size=False):
        photo = self.project.photo(ref)
        if photo.get('is_copy'):
            ref = photo['source_photo_id']
            photo = self.project.photo(ref)
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError('重关联文件不可用。')
        path = path.resolve()
        before = path.stat()
        from ..photo_agent.models import digest

        sha = digest(path)
        if photo['sha'] and sha != photo['sha']:
            raise ValueError('重关联 hash 指纹不匹配。')
        if not photo['sha'] and not confirm_name_size:
            raise ValueError('缺少原始 hash，名称与大小候选需要人工确认。')
        if not photo['sha'] and (
            path.name != Path(photo['path']).name or before.st_size != photo['size']
        ):
            raise ValueError('重关联候选名称与大小不匹配。')
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('重关联检查中文件发生变化。')
        with self.command('relink') as tx:
            if tx.db.execute(
                'SELECT 1 FROM photos WHERE path=? AND id!=?', (str(path), ref)
            ).fetchone():
                raise ValueError('文件已关联工程中另一张照片。')
            tx.put(
                'photos',
                dict(
                    id=ref,
                    path=str(path),
                    sha=sha,
                    size=after.st_size,
                    mtime=after.st_mtime_ns,
                    status='ready' if photo['facts'] else 'pending',
                ),
            )

    def find_relink_candidates(self, ref, folder, *, cancel=None):
        photo = self.project.photo(ref)
        from ..photo_agent.models import digest

        candidates = []
        for root, _dirs, files in os.walk(folder, followlinks=False):
            for name in files:
                if cancel and cancel.is_set():
                    raise InterruptedError('重关联检索已取消。')
                path = Path(root) / name
                if path.is_symlink() or path.stat().st_size != photo['size']:
                    continue
                if photo['sha']:
                    if digest(path, cancel) == photo['sha']:
                        candidates.append({'path': str(path.resolve()), 'verified': True})
                elif name == Path(photo['path']).name:
                    candidates.append({'path': str(path.resolve()), 'verified': False})
                if len(candidates) > 100:
                    raise ValueError('重关联候选超过 100 个，请缩小目录。')
        return candidates

    def relink_prefix(self, old_folder, new_folder, *, confirm_name_size=False, cancel=None):
        from ..photo_agent.models import digest

        old_folder, new_folder = Path(old_folder).resolve(), Path(new_folder).resolve()
        if not new_folder.is_dir() or new_folder.is_symlink():
            raise ValueError('新的原片目录不可用。')
        replacements = []
        for offset in range(0, self.project.summary()['photos'], 200):
            for photo in self.project.photos(200, offset):
                if cancel and cancel.is_set():
                    raise InterruptedError('目录重关联已取消。')
                original = Path(photo['path'])
                if not original.is_relative_to(old_folder):
                    continue
                candidate = new_folder / original.relative_to(old_folder)
                if (
                    candidate.is_symlink()
                    or not candidate.is_file()
                    or not candidate.resolve().is_relative_to(new_folder)
                ):
                    raise ValueError('目录重关联包含缺失或越界文件。')
                before = candidate.stat()
                sha = digest(candidate, cancel)
                if photo['sha']:
                    if sha != photo['sha']:
                        raise ValueError('目录重关联 hash 指纹不匹配；未更改任何关联。')
                elif (
                    not confirm_name_size
                    or candidate.name != original.name
                    or before.st_size != photo['size']
                ):
                    raise ValueError('目录重关联缺少 hash；需要逐项名称/大小确认。')
                after = candidate.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError('目录重关联检查时文件发生变化。')
                replacements.append(
                    dict(
                        id=photo['id'],
                        path=str(candidate.resolve()),
                        sha=sha,
                        size=after.st_size,
                        mtime=after.st_mtime_ns,
                        status='ready' if photo['facts'] else 'pending',
                    )
                )
                if len(replacements) > 500:
                    raise ValueError('单次可撤销目录替换最多 500 张，请缩小范围。')
        with self.command('relink.directory') as tx:
            for row in replacements:
                if tx.db.execute(
                    'SELECT 1 FROM photos WHERE path=? AND id!=?', (row['path'], row['id'])
                ).fetchone():
                    raise ValueError('目录重关联目标已属于另一张照片。')
                tx.put('photos', row)
        return len(replacements)

    def refresh_missing(self, *, cancel=None):
        changed = []
        for offset in range(0, self.project.summary()['photos'], 200):
            for photo in self.project.photos(200, offset):
                if cancel and cancel.is_set():
                    raise InterruptedError('缺失检索已取消。')
                if not Path(photo['path']).is_file() and photo['status'] != 'missing':
                    changed.append(photo['id'])
        with self.command('missing') as tx:
            for ref in changed:
                tx.put('photos', dict(id=ref, status='missing'))
        return changed

    def create_proxy(self, ref, *, max_edge=2560, cancel=None):
        from .. import engine
        from ..photo_agent.models import digest
        import numpy as np
        import tifffile

        if type(max_edge) is not int or not 64 <= max_edge <= 8192:
            raise ValueError('代理最长边需要 64–8192 像素。')
        photo = self.project.photo(ref)
        source = Path(photo['path'])
        before = source.stat()
        source_sha = digest(source, cancel)
        rgb, info = engine.load_image(source, max_edge)
        rgb = engine.to_srgb(engine.develop.apply(rgb, info.get('develop', {})))
        if cancel and cancel.is_set():
            raise InterruptedError('代理创建已取消。')
        folder = self.project.directory('proxies')
        destination = folder / (ref + '-' + _id() + '.tiff')
        if destination.exists():
            raise ValueError('代理不能覆盖已有文件。')
        pixels = np.round(np.clip(rgb, 0, 1) * 65535).astype(np.uint16)
        temporary = folder / (_id() + '.tmp')
        try:
            tifffile.imwrite(
                temporary,
                pixels,
                photometric='rgb',
                compression='deflate',
                metadata={
                    'color_space': 'sRGB',
                    'source_sha': source_sha,
                    'original_size': [info['width'], info['height']],
                },
            )
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('创建代理时原片发生变化。')
            temporary.replace(destination)
            with self.command('proxy.create') as tx:
                tx.put(
                    'proxies',
                    dict(
                        photo_ref=ref,
                        path=destination.name,
                        width=info['width'],
                        height=info['height'],
                        proxy_width=pixels.shape[1],
                        proxy_height=pixels.shape[0],
                        source_sha=source_sha,
                        source_size=after.st_size,
                        source_mtime=after.st_mtime_ns,
                        color_space='sRGB',
                        created=time.time(),
                    ),
                )
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
        return self.proxy(ref)

    def proxy(self, ref):
        with self.project.connect() as db:
            row = db.execute('SELECT * FROM proxies WHERE photo_ref=?', (ref,)).fetchone()
        if not row:
            return None
        path = Path(row['path'])
        if not path.is_absolute():
            path = self.project.directory('proxies') / path
        if path.is_symlink() or path.resolve().parent not in self._proxy_folders():
            raise ValueError('代理路径无效。')
        return dict(row, path=str(path))

    def _proxy_folders(self):
        folders = [self.project.directory('proxies')]
        with self.project.connect() as db:
            row = db.execute(
                "SELECT value FROM preferences WHERE key='catalog.proxy_locations'"
            ).fetchone()
        for value in json.loads(row[0]) if row else []:
            path = Path(value)
            if (
                path.name != 'ccraw-proxies-' + self.project.manifest['id']
                or path.is_symlink()
                or path.is_junction()
            ):
                raise ValueError('代理存储目录无效。')
            folders.append(path.resolve())
        return folders

    def relocate_proxy(self, ref, destination_directory=None):
        """Copy into an explicitly chosen managed storage folder, retaining undo data."""
        import shutil

        proxy = self.proxy(ref)
        if not proxy or not Path(proxy['path']).is_file():
            raise ValueError('没有可迁移的代理。')
        folders = self._proxy_folders()
        target_folder = folders[0]
        if destination_directory is not None:
            parent = Path(destination_directory)
            if not parent.is_dir() or parent.is_symlink() or parent.is_junction():
                raise ValueError('代理迁移需要现有的普通目录。')
            target_folder = parent.resolve() / ('ccraw-proxies-' + self.project.manifest['id'])
            if target_folder.is_symlink() or target_folder.is_junction():
                raise ValueError('代理目录不能是链接。')
            target_folder.mkdir(exist_ok=True)
        target = target_folder / (_id() + '.tiff')
        try:
            with Path(proxy['path']).open('rb') as source, target.open('xb') as stream:
                shutil.copyfileobj(source, stream, 1024 * 1024)
            with self.command('proxy.relocate') as tx:
                if target_folder not in folders:
                    tx.put(
                        'preferences',
                        dict(
                            key='catalog.proxy_locations',
                            value=_json([str(p) for p in [*folders[1:], target_folder]]),
                        ),
                    )
                tx.put(
                    'proxies',
                    dict(
                        photo_ref=ref,
                        path=str(target) if target_folder != folders[0] else target.name,
                    ),
                )
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return self.proxy(ref)

    def cleanup_proxies(self):
        """Delete only orphan managed UUID TIFFs; keep live and undo/redo resources."""
        import re

        folders = self._proxy_folders()
        retained = set()
        with self.project.connect() as db:
            paths = [r[0] for r in db.execute('SELECT path FROM proxies')]
            for record in db.execute('SELECT actions FROM catalog_commands'):
                for action in json.loads(record[0]):
                    if action['table'] == 'proxies':
                        for value in (action['before'], action['after']):
                            if value:
                                paths.append(value['path'])
            sources = {Path(r[0]).resolve() for r in db.execute('SELECT path FROM photos')}
        for value in paths:
            path = Path(value)
            retained.add(path.resolve() if path.is_absolute() else (folders[0] / path).resolve())
        removed = []
        for folder in folders:
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                if (
                    re.fullmatch('[0-9a-f]{32}(?:-[0-9a-f]{32})?\\.tiff', path.name)
                    and path.is_file()
                    and not path.is_symlink()
                    and path.resolve().parent == folder
                    and path.resolve() not in retained | sources
                ):
                    path.unlink()
                    removed.append(str(path))
        self.project.event(
            'catalog.proxy.cleanup', {'removed': len(removed), 'undo_resources_retained': True}
        )
        return sorted(removed)

    def proxy_status(self, ref):
        proxy = self.proxy(ref)
        if not proxy or not Path(proxy['path']).is_file():
            return 'missing'
        source = Path(self.project.photo(ref)['path'])
        if not source.is_file():
            return 'offline'
        stat = source.stat()
        if (stat.st_size, stat.st_mtime_ns) != (proxy['source_size'], proxy['source_mtime']):
            return 'stale'
        from ..photo_agent.models import digest

        return 'ready' if digest(source) == proxy['source_sha'] else 'stale'

    def edit_source(self, ref, *, allow_proxy=False, prefer_proxy=False):
        photo = self.project.photo(ref)
        if prefer_proxy and self.proxy_status(ref) in ('ready', 'offline'):
            return dict(photo, path=self.proxy(ref)['path'], is_proxy=True, proxy=self.proxy(ref))
        if Path(photo['path']).is_file():
            return dict(photo, is_proxy=False)
        if allow_proxy and self.proxy_status(ref) == 'offline':
            return dict(photo, path=self.proxy(ref)['path'], is_proxy=True, proxy=self.proxy(ref))
        raise ValueError('原片缺失；正常全分辨率输出需要重新关联原片。')

    def remove_proxy(self, ref):
        """Remove the reference; keep its managed TIFF so undo works while offline."""
        self._refs([ref])
        with self.command('proxy.remove') as tx:
            tx.delete('proxies', dict(photo_ref=ref))

    def export_proxy(self, ref, path):
        from .. import engine, model
        from PIL import Image
        import numpy as np
        import tifffile

        if self.proxy_status(ref) not in ('ready', 'offline'):
            raise ValueError('代理缺失或过期，不能导出。')
        destination = Path(path)
        if destination.exists() or destination.suffix.lower() != '.png':
            raise ValueError('低分辨率代理输出需要新的 PNG 文件，不能覆盖已有文件。')
        proxy = self.proxy(ref)
        rgb = tifffile.imread(proxy['path']).astype(np.float32) / 65535
        recipe = self.project.preferences().get('recipe:' + ref, model.recipe())
        rgb = engine.process(
            engine.to_linear(rgb), recipe, detail_scale=proxy['proxy_width'] / proxy['width']
        )
        temporary = destination.with_name(_id() + '.tmp')
        try:
            with temporary.open('xb') as stream:
                Image.fromarray(np.round(np.clip(rgb, 0, 1) * 255).astype(np.uint8)).save(
                    stream, format='PNG'
                )
            if destination.exists():
                raise ValueError('输出目标已存在，不能覆盖。')
            temporary.rename(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return str(destination)

    def backup(self, path):
        path = Path(path)
        if path.exists() or path.resolve() == self.project.database.resolve():
            raise ValueError('备份不能覆盖现有文件。')
        with self.project.connect() as source:
            target = sqlite3.connect(path)
            try:
                source.backup(target)
            finally:
                target.close()

    def purge_removed(self, ids, backup_path):
        """Explicit record purge after a SQLite backup; source files are untouched."""
        ids = self._refs(ids)
        if any(self.state(ref)['removed_at'] is None for ref in ids):
            raise ValueError('只能清空已移入工程回收站的引用。')
        with self.project.connect() as db:
            for ref in ids:
                copies = [
                    r[0]
                    for r in db.execute(
                        'SELECT id FROM virtual_copies WHERE source_photo_id=?', (ref,)
                    )
                ]
                if any(copy not in ids for copy in copies):
                    raise ValueError('原片仍有虚拟副本引用；先分别处理副本。')
        self.backup(backup_path)
        with self.project.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for ref in ids:
                for table, column in [
                    ('photo_state', 'photo_ref'),
                    ('album_members', 'photo_ref'),
                    ('stack_members', 'photo_ref'),
                    ('photo_keywords', 'photo_ref'),
                    ('metadata_overrides', 'photo_ref'),
                    ('proxies', 'photo_ref'),
                    ('copy_history', 'copy_id'),
                    ('proposals', 'photo_id'),
                    ('derivatives', 'photo_id'),
                ]:
                    db.execute(f'DELETE FROM {table} WHERE {column}=?', (ref,))
                db.execute(
                    'DELETE FROM preferences WHERE key IN(?,?,?)',
                    ('recipe:' + ref, 'annotation:' + ref, 'snapshots:' + ref),
                )
                db.execute('DELETE FROM virtual_copies WHERE id=?', (ref,))
            for ref in ids:
                db.execute('DELETE FROM photos WHERE id=?', (ref,))
            db.execute('DELETE FROM stacks WHERE id NOT IN(SELECT stack_id FROM stack_members)')
            for row in db.execute('SELECT id,cover FROM stacks').fetchall():
                if row['cover'] in ids:
                    replacement = db.execute(
                        'SELECT photo_ref FROM stack_members WHERE stack_id=? ORDER BY position LIMIT 1',
                        (row['id'],),
                    ).fetchone()
                    if replacement:
                        db.execute(
                            'UPDATE stacks SET cover=? WHERE id=?', (replacement[0], row['id'])
                        )
            # Purge is recovered from its backup, not a stale row-level undo chain.
            db.execute('DELETE FROM catalog_commands')
            db.execute(
                'INSERT INTO events(kind,payload,created) VALUES(?,?,?)',
                (
                    'catalog.purged',
                    _json(
                        {
                            'photo_refs': ids,
                            'backup': str(Path(backup_path).resolve()),
                            'originals': 'untouched',
                        }
                    ),
                    time.time(),
                ),
            )
        return len(ids)
